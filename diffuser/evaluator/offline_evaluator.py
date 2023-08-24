from functools import partial
from typing import Any, Dict

import jax
import jax.numpy as jnp

from utilities.jax_utils import batch_to_jax, next_rng

from .base_evaluator import BaseEvaluator


class DiffuserOfflineEvaluator(BaseEvaluator):
    eval_mode: str = "offline"

    def __init__(self, config, policy, eval_dataloader):
        super().__init__(config, policy)
        self._eval_dataloader = eval_dataloader

    def evaluate(self, epoch: int) -> Dict[str, Any]:
        eval_batch = batch_to_jax(next(self._eval_dataloader))
        rng = next_rng()
        metrics = self._offline_eval_step(self._policy.params, rng, eval_batch)
        self.dump_metrics(metrics, epoch, suffix="_offline")
        return metrics

    @partial(jax.jit, static_argnames=("self"))
    def _offline_eval_step(self, params, rng, eval_batch):
        metrics = {}

        samples = eval_batch["samples"]
        conditions = eval_batch["conditions"]
        returns = eval_batch["returns"]
        actions = eval_batch["actions"]

        pred_actions = self._inv_model.apply(
            params["inv_model"],
            jnp.concatenate([samples[:, :-1], samples[:, 1:]], axis=-1),
        )

        pred_act_mse = jnp.mean(jnp.square(pred_actions - actions[:, :-1]))
        pred_act_mse_first_step = jnp.mean(
            jnp.square(pred_actions[:, 0] - actions[:, 0])
        )

        plan_observations = self._planner.apply(
            params["planner"],
            rng,
            conditions=conditions,
            returns=returns,
            method=self._planner.ddpm_sample,
        )
        plan_obs_comb = jnp.concatenate(
            [plan_observations[:, :-1], plan_observations[:, 1:]], axis=-1
        )
        plan_actions = self._inv_model.apply(
            params["inv_model"],
            plan_obs_comb,
        )

        plan_obs_mse_first_step = jnp.mean(
            jnp.square(plan_observations[:, 1] - samples[:, 1])
        )
        plan_obs_mse = jnp.mean(jnp.square(plan_observations - samples))

        plan_act_mse = jnp.mean(jnp.square(plan_actions - actions[:, :-1]))
        plan_act_mse_first_step = jnp.mean(
            jnp.square(plan_actions[:, 0] - actions[:, 0])
        )

        metrics["plan_obs_mse"] = plan_obs_mse
        metrics["plan_act_mse"] = plan_act_mse
        metrics["pred_act_mse"] = pred_act_mse

        metrics["plan_obs_mse_first_step"] = plan_obs_mse_first_step
        metrics["plan_act_mse_first_step"] = plan_act_mse_first_step
        metrics["pred_act_mse_first_step"] = pred_act_mse_first_step

        return metrics
