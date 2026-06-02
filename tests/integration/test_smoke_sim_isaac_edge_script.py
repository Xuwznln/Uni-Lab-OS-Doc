from scripts import smoke_sim_isaac_edge


def test_smoke_script_parse_args(tmp_path):
    args = smoke_sim_isaac_edge.parse_args(
        [
            "--grpc",
            "127.0.0.1:50051",
            "--physics-endpoint",
            "http://127.0.0.1:8091",
            "--state-target",
            "arm",
            "--pose-target",
            "tool",
            "--out",
            str(tmp_path / "frame.png"),
        ]
    )

    assert args.grpc == "127.0.0.1:50051"
    assert args.physics_endpoint == "http://127.0.0.1:8091"
    assert args.camera == "/World/Camera"
    assert args.width == 640
    assert args.height == 480
    assert args.physics_timeout_s == 120.0


def test_validate_png_rejects_empty_payload():
    assert smoke_sim_isaac_edge.is_png_like(b"") is False
    assert smoke_sim_isaac_edge.is_png_like(b"\x89PNG\r\n\x1a\npayload") is False
    assert smoke_sim_isaac_edge.is_png_like(b"\x89PNG\r\n\x1a\npayloadIEND\xaeB`\x82") is True
