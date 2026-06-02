from unilabos.app.main import _can_start_without_cloud_auth, build_argparser


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


def test_cli_physics_defaults_are_backward_compatible():
    args = build_argparser().parse_args([])

    assert args.physics == "none"
    assert args.physics_endpoint is None
    assert args.physics_scene is None
    assert args.physics_timeout == 120.0


def test_cli_accepts_isaac_physics_options():
    args = build_argparser().parse_args(
        [
            "--mode",
            "sim",
            "--physics",
            "isaac",
            "--physics_endpoint",
            "http://127.0.0.1:8091",
            "--physics_scene",
            "/tmp/lab.usd",
            "--physics_timeout",
            "180",
        ]
    )

    assert args.physics == "isaac"
    assert args.physics_endpoint == "http://127.0.0.1:8091"
    assert args.physics_scene == "/tmp/lab.usd"
    assert args.physics_timeout == 180.0


def test_local_graph_fastapi_can_start_without_cloud_auth():
    args = {"app_bridges": ["fastapi"], "use_remote_resource": False}

    assert _can_start_without_cloud_auth(args, "/tmp/mock_all.json") is True


def test_websocket_or_remote_resource_still_requires_cloud_auth():
    assert _can_start_without_cloud_auth({"app_bridges": ["websocket"], "use_remote_resource": False}, "/tmp/g.json") is False
    assert _can_start_without_cloud_auth({"app_bridges": ["fastapi"], "use_remote_resource": True}, "/tmp/g.json") is False
    assert _can_start_without_cloud_auth({"app_bridges": ["fastapi"], "use_remote_resource": False}, None) is False
