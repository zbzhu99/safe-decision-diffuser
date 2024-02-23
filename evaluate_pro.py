import argparse
import importlib
import json
import os

import orbax
from ml_collections import ConfigDict

from utilities.utils import dot_key_dict_to_nested_dicts
import gc
from utilities.utils import set_random_seed, str_to_list, to_arch


# ant run
# log_dir = "logs/cdbc_dsrl/OfflineAntRun-v0/tgt_700.0,10, 750.0,20, 800.0,40-guidew_2.0/300/2_16_3"
# epochs = [650, 700, 1200, 1550, 3150]

# car circle

# car run
# log_dir = "logs/cdbc_dsrl/OfflineCarRun-v0/tgt_575.0,10, 575.0,20, 575.0,40-guidew_2.0/300/2_17_3"
# epochs = [250, 2300, 2400, 2950, 3700]

# drone circle
# log_dir = "logs/cdbc_dsrl/OfflineDroneCircle-v0/tgt_700.0,10, 750.0,20, 800.0,40-guidew_2.0/300/2_17_2"
# epochs = [2550, 3000, 3650, 3700, 3750, 3800, 3850, 3900, 3950, 4000]

# drone run
log_dir = "logs/cdbc_dsrl/OfflineDroneRun-v0/tgt_400.0,10, 500.0,20, 600.0,40-guidew_2.0/300/2_17_2"
epochs = [650, 750, 1000, 1100, 1250, 1450, 1700, 2000, 2200]


def main():
    parser = argparse.ArgumentParser()
    # parser.add_argument("log_dir", type=str)
    parser.add_argument("-g", type=int, default=0)
    parser.add_argument("--evaluator_class", type=str, default="OnlineEvaluator")
    parser.add_argument("--num_eval_envs", type=int, default=10)
    parser.add_argument("--eval_n_trajs", type=int, default=20)
    parser.add_argument("--eval_env_seed", type=int, default=0)
    parser.add_argument("--eval_batch_size", type=int, default=128)
    # parser.add_argument("--epochs", type=int, nargs="+", required=True)
    args = parser.parse_args()
    args.log_dir = log_dir
    args.epochs = epochs
    if args.g < 0:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    else:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.g)

    with open(os.path.join(args.log_dir, "variant.json"), "r") as f:
        variant = json.load(f)

    config = dot_key_dict_to_nested_dicts(variant)
    config = ConfigDict(config)

    # rewrite configs
    config.evaluator_class = args.evaluator_class
    config.num_eval_envs = args.num_eval_envs
    config.eval_n_trajs = args.eval_n_trajs
    config.eval_env_seed = args.eval_env_seed
    config.eval_batch_size = args.eval_batch_size

    config.returns_condition = True
    config.cost_returns_condition = True
    config.mode = "eval"

    # config.condition_guidance_w = 2.0
    config.target_returns = "200.0, 0.0"

    evaluator = getattr(importlib.import_module("diffuser.trainer"), config.trainer)(
        config, use_absl=False
    )
    evaluator._setup()

    # eval_pro_data_record = {"epoch":[], "average_normalized_return":[], "average_normalized_cost_return":[], "target_reward_return":[], "target_cost_return":[], "average_reward_return":[],
    #                         "average_cost_return":[], "reward_return":[], "cost_return":[], "target_returns":[]}
    eval_pro_data_record = {
        "epoch": [],
        "average_normalized_return": [],
        "average_normalized_cost_return": [],
        "average_reward_return": [],
        "average_cost_return": [],
        "normalized_reward_return": [],
        "normalized_cost_return": [],
        "reward_return": [],
        "cost_return": [],
        "target_returns": [],
    }

    # target_returns_list = []
    eval_target_returns = ""
    target_reward_returns_list = str_to_list(config.eval_target_reward_returns_list)
    target_cost_returns_list = str_to_list(config.eval_target_cost_returns_list)

    for reward in target_reward_returns_list:
        for cost in target_cost_returns_list:
            eval_target_returns += f"{reward}, {cost},"
    eval_target_returns = eval_target_returns[:-1]
    # target_returns_list.append(f"{reward}, {cost}")

    # for tmp_target_returns in target_returns_list:
    #     evaluator._reset_target_returns(tmp_target_returns)
    #     orbax_checkpointer = orbax.checkpoint.PyTreeCheckpointer()
    #     target = {"agent_states": evaluator._agent.train_states}
    #     for i in range(5):
    #         print()
    #     print(tmp_target_returns, 'this is the tmp_target_returns')
    #     for epoch in args.epochs:
    #         ckpt_path = os.path.join(args.log_dir, f"checkpoints/model_{epoch}")
    #         restored = orbax_checkpointer.restore(ckpt_path, item=target)
    #         eval_params = {
    #             key: restored["agent_states"][key].params_ema
    #             or restored["agent_states"][key].params
    #             for key in evaluator._agent.model_keys
    #         }
    #         evaluator._evaluator.update_params(eval_params)
    #         metrics = evaluator._evaluator.evaluate(epoch)
    #         print(f"\033[92m Epoch {epoch}: {metrics} \033[00m\n")
    #         eval_pro_data_record["epoch"].append(epoch)
    #         eval_pro_data_record["target_reward_return"].append(tmp_target_returns.split(",")[0])
    #         eval_pro_data_record["target_cost_return"].append(tmp_target_returns.split(",")[1])
    #         eval_pro_data_record["average_normalized_return"].append(metrics["average_normalized_return"])
    #         eval_pro_data_record["average_normalized_cost_return"].append(metrics["average_normalized_cost_return"])
    #         eval_pro_data_record["average_reward_return"].append(metrics["average_return"])
    #         eval_pro_data_record["average_cost_return"].append(metrics["average_cost_return"])
    #         eval_pro_data_record["reward_return"].append(metrics["return_record"])
    #         eval_pro_data_record["cost_return"].append(metrics["cost_return_record"])
    #         gc.collect()

    evaluator._reset_target_returns(eval_target_returns)
    orbax_checkpointer = orbax.checkpoint.PyTreeCheckpointer()
    target = {"agent_states": evaluator._agent.train_states}
    for epoch in args.epochs:
        ckpt_path = os.path.join(args.log_dir, f"checkpoints/model_{epoch}")
        restored = orbax_checkpointer.restore(ckpt_path, item=target)
        eval_params = {
            key: restored["agent_states"][key].params_ema
            or restored["agent_states"][key].params
            for key in evaluator._agent.model_keys
        }
        evaluator._evaluator.update_params(eval_params)
        metrics = evaluator._evaluator.evaluate(epoch)
        print(f"\033[92m Epoch {epoch}: {metrics} \033[00m\n")
        eval_pro_data_record["epoch"].append(epoch)
        eval_pro_data_record["target_returns"].append(eval_target_returns)
        eval_pro_data_record["average_normalized_return"].append(
            metrics["average_normalized_return"]
        )
        eval_pro_data_record["average_normalized_cost_return"].append(
            metrics["average_normalized_cost_return"]
        )
        eval_pro_data_record["average_reward_return"].append(metrics["average_return"])
        eval_pro_data_record["normalized_reward_return"].append(
            metrics["normalized_return_record"]
        )
        eval_pro_data_record["normalized_cost_return"].append(
            metrics["normalized_cost_return_record"]
        )
        eval_pro_data_record["average_cost_return"].append(
            metrics["average_cost_return"]
        )
        eval_pro_data_record["reward_return"].append(metrics["return_record"])
        eval_pro_data_record["cost_return"].append(metrics["cost_return_record"])
        gc.collect()

    #
    import pandas as pd

    df = pd.DataFrame(eval_pro_data_record)
    os.makedirs(name=args.log_dir + "/eval", exist_ok=True)
    df.to_csv(
        f"{args.log_dir}/eval/tcr-{config.eval_target_cost_returns_list}--trr-{config.eval_target_reward_returns_list}.csv",
        index=False,
    )


if __name__ == "__main__":
    main()
