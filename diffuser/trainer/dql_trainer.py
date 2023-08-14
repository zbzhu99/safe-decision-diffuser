import numpy as np
import torch
from ml_collections import ConfigDict

from diffuser.algos import DiffusionQL
from diffuser.diffusion import GaussianDiffusion, LossType, ModelMeanType, ModelVarType
from diffuser.nets import Critic, DiffusionPolicy, GaussianPolicy, Value
from diffuser.policy import SamplerPolicy
from diffuser.trainer.base_trainer import BaseTrainer
from utilities.data_utils import cycle, numpy_collate
from utilities.utils import set_random_seed, to_arch


class DiffusionQLTrainer(BaseTrainer):
    @staticmethod
    def get_default_config(updates=None):
        cfg = ConfigDict()
        cfg.discount = 0.99
        cfg.tau = 0.005
        cfg.policy_tgt_freq = 5
        cfg.num_timesteps = 100
        cfg.schedule_name = "linear"
        cfg.time_embed_size = 16
        cfg.alpha = 2.0  # NOTE 0.25 in diffusion rl but 2.5 in td3
        cfg.use_pred_astart = True
        cfg.max_q_backup = False
        cfg.max_q_backup_topk = 1
        cfg.max_q_backup_samples = 10
        cfg.nstep = 1

        # learning related
        cfg.lr = 3e-4
        cfg.diff_coef = 1.0
        cfg.guide_coef = 1.0
        cfg.lr_decay = False
        cfg.lr_decay_steps = 1000000
        cfg.max_grad_norm = 0.0
        cfg.weight_decay = 0.0

        cfg.loss_type = "TD3"
        cfg.sample_logp = False

        cfg.adv_norm = False
        # CRR-related hps
        cfg.sample_actions = 10
        cfg.crr_ratio_upper_bound = 20
        cfg.crr_beta = 1.0
        cfg.crr_weight_mode = "mle"
        cfg.fixed_std = True
        cfg.crr_multi_sample_mse = False
        cfg.crr_avg_fn = "mean"
        cfg.crr_fn = "exp"

        # IQL-related hps
        cfg.expectile = 0.7
        cfg.awr_temperature = 3.0

        # for dpm-solver
        cfg.dpm_steps = 15
        cfg.dpm_t_end = 0.001

        # useless
        cfg.target_entropy = -1
        if updates is not None:
            cfg.update(ConfigDict(updates).copy_and_resolve_references())
        return cfg

    def _setup(self):
        set_random_seed(self._cfgs.seed)
        # setup logger
        self._wandb_logger = self._setup_logger()

        # setup dataset and eval_sample
        dataset, self._eval_sampler = self._setup_dataset()
        sampler = torch.utils.data.RandomSampler(dataset)
        self._dataloader = cycle(
            torch.utils.data.DataLoader(
                dataset,
                sampler=sampler,
                batch_size=self._cfgs.batch_size,
                collate_fn=numpy_collate,
                drop_last=True,
                num_workers=8,
            )
        )

        if self._cfgs.algo_cfg.target_entropy >= 0.0:
            action_space = self._eval_sampler.env.action_space
            self._cfgs.algo_cfg.target_entropy = -np.prod(action_space.shape).item()

        # setup policy
        self._policy = self._setup_policy()
        self._policy_dist = GaussianPolicy(
            self._action_dim, temperature=self._cfgs.policy_temp
        )

        # setup Q-function
        self._qf = self._setup_qf()
        self._vf = self._setup_vf()

        # setup agent
        self._agent = DiffusionQL(
            self._cfgs.algo_cfg, self._policy, self._qf, self._vf, self._policy_dist
        )

        # setup sampler policy
        self._sampler_policy = SamplerPolicy(self._agent.policy, self._agent.qf)

    def _setup_qf(self):
        qf = Critic(
            self._observation_dim,
            self._action_dim,
            to_arch(self._cfgs.qf_arch),
            use_layer_norm=self._cfgs.qf_layer_norm,
            act=self._act_fn,
            orthogonal_init=self._cfgs.orthogonal_init,
        )
        return qf

    def _setup_vf(self):
        vf = Value(
            self._observation_dim,
            to_arch(self._cfgs.qf_arch),
            use_layer_norm=self._cfgs.qf_layer_norm,
            act=self._act_fn,
            orthogonal_init=self._cfgs.orthogonal_init,
        )
        return vf

    def _setup_policy(self):
        gd = GaussianDiffusion(
            num_timesteps=self._cfgs.algo_cfg.num_timesteps,
            schedule_name=self._cfgs.algo_cfg.schedule_name,
            model_mean_type=ModelMeanType.EPSILON,
            model_var_type=ModelVarType.FIXED_SMALL,
            loss_type=LossType.MSE,
            min_value=-self._max_action,
            max_value=self._max_action,
        )
        policy = DiffusionPolicy(
            diffusion=gd,
            observation_dim=self._observation_dim,
            action_dim=self._action_dim,
            arch=to_arch(self._cfgs.policy_arch),
            time_embed_size=self._cfgs.algo_cfg.time_embed_size,
            use_layer_norm=self._cfgs.policy_layer_norm,
            sample_method=self._cfgs.sample_method,
            dpm_steps=self._cfgs.algo_cfg.dpm_steps,
            dpm_t_end=self._cfgs.algo_cfg.dpm_t_end,
        )

        return policy

    def _sample_trajs(self, act_method: str):
        # TODO: merge these two
        self._sampler_policy.act_method = (
            act_method or self._cfgs.sample_method + "ensemble"
        )
        if self._cfgs.sample_method == "ddim":
            self._sampler_policy.act_method = "ensemble"
        trajs = self._eval_sampler.sample(
            self._sampler_policy.update_params(self._agent.train_params),
            self._cfgs.eval_n_trajs,
            deterministic=True,
        )
        return trajs
