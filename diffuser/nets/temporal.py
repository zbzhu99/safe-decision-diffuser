from functools import partial
from typing import Tuple

import distrax
import flax.linen as nn
import jax
import jax.numpy as jnp
from einops.layers.flax import Rearrange

from diffuser.diffusion import GaussianDiffusion, ModelMeanType, _extract_into_tensor
from diffuser.dpm_solver import DPM_Solver, NoiseScheduleVP

from .helpers import Conv1dBlock, DownSample1d, TimeEmbedding, UpSample1d, mish


class ResidualTemporalBlock(nn.Module):
    out_channels: int
    kernel_size: int
    mish: bool = True

    @nn.compact
    def __call__(self, x, t):
        if self.mish:
            act_fn = mish
        else:
            act_fn = nn.silu

        time_mlp = nn.Sequential(
            [
                act_fn,
                nn.Dense(self.out_channels),
                Rearrange("batch f -> batch 1 f"),
            ]
        )

        out = Conv1dBlock(self.out_channels, self.kernel_size, self.mish)(x) + time_mlp(
            t
        )
        out = Conv1dBlock(self.out_channels, self.kernel_size, self.mish)(out)

        if x.shape[-1] == self.out_channels:
            return out
        else:
            return out + nn.Conv(self.out_channels, (1,))(x)


class TemporalUnet(nn.Module):
    sample_dim: int
    dim: int = 128
    dim_mults: Tuple[int] = (1, 4, 8)
    returns_condition: bool = False
    cost_returns_condition: bool = False
    condition_dropout: float = 0.1
    kernel_size: int = 5
    max_traj_length: int = 1000

    def setup(self):
        self.dims = dims = [
            self.sample_dim,
            *map(lambda m: self.dim * m, self.dim_mults),
        ]
        self.in_out = list(zip(dims[:-1], dims[1:]))
        print(f"[ diffuser/nets/temporal.py ] Channel dimensions: {self.in_out}")

    @nn.compact
    def __call__(
        self,
        rng,
        x: jnp.ndarray,
        time: jnp.ndarray,
        env_ts: jnp.ndarray,
        returns_to_go: jnp.ndarray = None,
        cost_returns_to_go: jnp.ndarray = None,
        use_dropout: bool = True,
        # force_dropout: bool = False,
        reward_returns_force_dropout: bool = False,
        cost_returns_force_droupout: bool = False,
    ):
        act_fn = mish

        time_mlp = TimeEmbedding(self.dim)

        mask_dist = None
        if self.returns_condition:
            returns_mlp = nn.Sequential(
                [
                    nn.Dense(self.dim),
                    act_fn,
                    nn.Dense(self.dim * 4),
                    act_fn,
                    nn.Dense(self.dim),
                ]
            )
            mask_dist = distrax.Bernoulli(probs=1 - self.condition_dropout)

        if self.cost_returns_condition:
            assert self.returns_condition is True
            cost_returns_mlp = nn.Sequential(
                [
                    nn.Dense(self.dim),
                    act_fn,
                    nn.Dense(self.dim * 4),
                    act_fn,
                    nn.Dense(self.dim),
                ]
            )

        t = time_mlp(time)
        env_ts_emb = nn.Embed(self.max_traj_length, self.dim)(env_ts)
        emb = jnp.stack([t, env_ts_emb], axis=1)
        if self.returns_condition:
            assert returns_to_go is not None
            returns_to_go = returns_to_go.reshape(-1, 1)
            returns_embed = returns_mlp(returns_to_go)
            if use_dropout:
                rng, sample_key = jax.random.split(rng)
                mask = mask_dist.sample(
                    seed=sample_key, sample_shape=(returns_embed.shape[0], 1)
                )
                returns_embed = returns_embed * mask

            # if force_dropout:
            if reward_returns_force_dropout:
                returns_embed = returns_embed * 0
            emb = jnp.concatenate([emb, jnp.expand_dims(returns_embed, 1)], axis=1)

        if self.cost_returns_condition:
            assert cost_returns_to_go is not None
            cost_returns = cost_returns_to_go.reshape(-1, 1)
            cost_returns_embed = cost_returns_mlp(cost_returns)
            if use_dropout:
                rng, sample_key = jax.random.split(rng)
                mask = mask_dist.sample(
                    seed=sample_key, sample_shape=(returns_embed.shape[0], 1)
                )
                cost_returns_embed = cost_returns_embed * mask

            # if force_dropout:
            if cost_returns_force_droupout:
                cost_returns_embed = cost_returns_embed * 0
            emb = jnp.concatenate([emb, jnp.expand_dims(cost_returns_embed, 1)], axis=1)

        emb = nn.LayerNorm()(emb)
        emb = emb.reshape(-1, emb.shape[1] * emb.shape[2])

        h = []
        num_resolutions = len(self.in_out)
        for ind, (_, dim_out) in enumerate(self.in_out):
            is_last = ind >= (num_resolutions - 1)

            x = ResidualTemporalBlock(
                dim_out,
                kernel_size=self.kernel_size,
                mish=True,
            )(x, emb)
            x = ResidualTemporalBlock(
                dim_out,
                kernel_size=self.kernel_size,
                mish=True,
            )(x, emb)
            h.append(x)

            if not is_last:
                x = DownSample1d(dim_out)(x)

        mid_dim = self.dims[-1]
        x = ResidualTemporalBlock(
            mid_dim,
            kernel_size=self.kernel_size,
            mish=True,
        )(x, emb)
        x = ResidualTemporalBlock(
            mid_dim,
            kernel_size=self.kernel_size,
            mish=True,
        )(x, emb)

        for ind, (dim_in, _) in enumerate(reversed(self.in_out[1:])):
            is_last = ind >= (num_resolutions - 1)

            x = jnp.concatenate([x, h.pop()], axis=-1)
            x = ResidualTemporalBlock(
                dim_in,
                kernel_size=self.kernel_size,
                mish=True,
            )(x, emb)
            x = ResidualTemporalBlock(
                dim_in,
                kernel_size=self.kernel_size,
                mish=True,
            )(x, emb)

            if not is_last:
                x = UpSample1d(dim_in)(x)

        x = nn.Sequential(
            [
                Conv1dBlock(self.dim, kernel_size=self.kernel_size, mish=True),
                nn.Conv(self.sample_dim, (1,)),
            ]
        )(x)

        return x


class DiffusionPlanner(nn.Module):
    diffusion: GaussianDiffusion
    sample_dim: int
    action_dim: int
    horizon: int
    history_horizon: int
    dim: int
    dim_mults: Tuple[int]
    returns_condition: bool = True
    cost_returns_condition: bool = True
    condition_dropout: float = 0.25
    kernel_size: int = 5
    sample_method: str = "ddpm"
    dpm_steps: int = 15
    dpm_t_end: float = 0.001
    max_traj_length: int = 1000

    def setup(self):
        self.base_net = TemporalUnet(
            sample_dim=self.sample_dim,
            dim=self.dim,
            dim_mults=self.dim_mults,
            returns_condition=self.returns_condition,
            cost_returns_condition=self.cost_returns_condition,
            condition_dropout=self.condition_dropout,
            kernel_size=self.kernel_size,
            max_traj_length=self.max_traj_length,
        )

    def ddpm_sample(
        self,
        rng,
        conditions,
        env_ts,
        deterministic=False,
        returns_to_go=None,
        cost_returns_to_go=None,
    ):
        batch_size = list(conditions.values())[0].shape[0]
        return self.diffusion.p_sample_loop_jit_no_observation(
            rng_key=rng,
            model_forward=self.base_net,
            shape=(batch_size, self.horizon + self.history_horizon, self.sample_dim),
            conditions=conditions,
            condition_dim=self.sample_dim - self.action_dim,
            returns_to_go=returns_to_go,
            cost_returns_to_go=cost_returns_to_go,
            env_ts=env_ts,
            clip_denoised=True,
        )

    def dpm_sample(
        self,
        rng,
        samples,
        conditions,
        env_ts,
        deterministic=False,
        returns_to_go=None,
        cost_returns_to_go=None,
    ):
        raise NotImplementedError
        noise_clip = True
        ns = NoiseScheduleVP(
            schedule="discrete", alphas_cumprod=self.diffusion.alphas_cumprod
        )

        def wrap_model(model_fn):
            def wrapped_model_fn(
                x, t, env_ts, returns_to_go=None, cost_returns_to_go=None
            ):
                t = (t - 1.0 / ns.total_N) * ns.total_N

                out = model_fn(
                    rng,
                    x,
                    t,
                    env_ts=env_ts,
                    returns_to_go=returns_to_go,
                    cost_returns_to_go=cost_returns_to_go,
                )
                # add noise clipping
                if noise_clip:
                    t = t.astype(jnp.int32)
                    x_w = _extract_into_tensor(
                        self.diffusion.sqrt_recip_alphas_cumprod, t, x.shape
                    )
                    e_w = _extract_into_tensor(
                        self.diffusion.sqrt_recipm1_alphas_cumprod, t, x.shape
                    )
                    max_value = (self.diffusion.max_value + x_w * x) / e_w
                    min_value = (self.diffusion.min_value + x_w * x) / e_w

                    out = out.clip(min_value, max_value)
                return out

            return wrapped_model_fn

        dpm_sampler = DPM_Solver(
            model_fn=wrap_model(
                partial(
                    self.base_net,
                    samples,
                    env_ts=env_ts,
                    returns_to_go=returns_to_go,
                    cost_returns_to_go=cost_returns_to_go,
                )
            ),
            noise_schedule=ns,
            predict_x0=self.diffusion.model_mean_type is ModelMeanType.START_X,
        )
        x = jax.random.normal(rng, samples.shape)
        out = dpm_sampler.sample(x, steps=self.dpm_steps, t_end=self.dpm_t_end)

        return out

    def ddim_sample(
        self,
        rng,
        conditions,
        env_ts,
        deterministic=False,
        returns_to_go=None,
        cost_returns_to_go=None,
    ):
        # expect a loop-jitted version of ddim_sample_loop, otherwise it's too slow
        raise NotImplementedError
        batch_size = list(conditions.items())[0].shape[0]
        return self.diffusion.ddim_sample_loop(
            rng_key=rng,
            model_forward=self.base_net,
            shape=(batch_size, self.horizon + self.history_horizon, self.sample_dim),
            conditions=conditions,
            returns_to_go=returns_to_go,
            cost_returns_to_go=cost_returns_to_go,
            env_ts=env_ts,
            clip_denoised=True,
        )

    def __call__(
        self,
        rng,
        conditions,
        env_ts,
        deterministic=False,
        returns_to_go=None,
        cost_returns_to_go=None,
    ):
        return getattr(self, f"{self.sample_method}_sample")(
            rng, conditions, env_ts, deterministic, returns_to_go, cost_returns_to_go
        )

    def loss(
        self,
        rng_key,
        samples,
        conditions,
        ts,
        env_ts,
        masks,
        returns_to_go=None,
        cost_returns_to_go=None,
    ):
        terms = self.diffusion.training_losses(
            rng_key,
            model_forward=self.base_net,
            x_start=samples,
            conditions=conditions,
            condition_dim=self.sample_dim - self.action_dim,
            returns_to_go=returns_to_go,
            cost_returns_to_go=cost_returns_to_go,
            env_ts=env_ts,
            t=ts,
            masks=masks,
        )
        return terms
