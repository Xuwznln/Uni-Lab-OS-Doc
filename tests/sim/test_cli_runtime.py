from unilabos.app.main import build_argparser


def test_cli_defaults_are_backward_compatible():
    args = build_argparser().parse_args([])
    assert args.mode == "real"
    assert args.sim_rate == 1.0
    assert args.sim_paused is False


def test_cli_accepts_sim_options():
    args = build_argparser().parse_args(["--mode", "sim", "--sim_rate", "20", "--sim_paused"])
    assert args.mode == "sim"
    assert args.sim_rate == 20.0
    assert args.sim_paused is True


def test_cli_can_disable_sim_ros_services():
    args = build_argparser().parse_args(["--mode", "sim", "--disable_sim_services"])
    assert args.disable_sim_services is True
