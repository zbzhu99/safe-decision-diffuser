import importlib
import sys

import absl

from utilities.utils import define_flags_with_default, import_file


def diffuser_sampling():
    config = getattr(
        import_file("configs/diffuser_walker_mdreplay.py", "default_config"),
        "get_config",
    )()
    config = define_flags_with_default(**config)
    absl.flags.FLAGS(sys.argv[:1])

    trainer = getattr(
        importlib.import_module("diffuser.trainer"), absl.flags.FLAGS.trainer
    )(config)
    trainer._setup()

    trainer._sampler_policy.act_method = "ddpm"
    trajs = trainer._eval_sampler.sample(
        trainer._sampler_policy.update_params(trainer._agent.eval_params),
        n_trajs=100,
        deterministic=True,
    )


if __name__ == "__main__":
    diffuser_sampling()
