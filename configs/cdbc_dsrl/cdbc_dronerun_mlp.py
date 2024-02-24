from configs.base_cdbc import get_base_config

def get_config():
    config = get_base_config()
    config.exp_name = "cdbc_dsrl"
    config.log_dir_format = (
        "{exp_name}/{env}/tgt_{target_returns}-guidew_{condition_guidance_w}/{seed}/2_20_1"
    )
    # TODO: design the logging for eval
    config.eval_log_dir_format = (
        "{exp_name}/{env}/tgt_{target_returns}-guidew_{condition_guidance_w}/{seed}/2_20_1/eval"
    )

    config.env = "OfflineDroneRun-v0"
    config.dataset = "dsrl"
    config.returns_condition = True
    config.cost_returns_condition = True
    config.env_ts_condition = True
    
    # TODO: design new loss setting to learn
    config.target_returns = "300.0, 0, 400.0,10, 500.0,20, 600.0,40"
    config.cost_limit = 10.0

    config.max_traj_length = 200
    config.horizon = 1

    config.eval_period = 25
    config.eval_n_trajs = 20
    config.num_eval_envs = 10

    # data aug configs
    config.aug_percent = 0.0
    config.aug_deg = 1
    config.aug_max_rew_decrease = 100
    config.aug_max_reward = 700.0
    config.aug_min_reward = 1

    config.condition_guidance_w = 2

    config.n_epochs = 2000
    config.n_train_step_per_epoch = 1000

    config.save_period = 25

    # special variable for cdbc
    config.architecture: str = "mlp"

    # evaluate_pro config
    config.eval_target_reward_returns_list = "600.0, 650.0, 700.0"
    config.eval_target_cost_returns_list = "0.0, 2.0, 5.0, 7.0, 10.0, 20.0, 25.0, 30.0"

    # mode represents whether the config is used for training or evaluation
    config.mode = "train" # or "eval"

    # learning related
    config.algo_cfg.lr = 1e-4
    config.algo_cfg.lr_decay = False
    config.algo_cfg.lr_decay_steps = 20
    config.algo_cfg.lr_decay_alpha = 0.05

    return config
