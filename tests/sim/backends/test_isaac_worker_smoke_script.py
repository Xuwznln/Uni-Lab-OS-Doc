from scripts import smoke_isaac_worker


def test_smoke_script_parse_args_defaults(tmp_path):
    args = smoke_isaac_worker.parse_args(
        ["--endpoint", "http://127.0.0.1:8091", "--out", str(tmp_path / "frame.png")]
    )

    assert args.endpoint == "http://127.0.0.1:8091"
    assert args.camera == "/World/Camera"
    assert args.width == 640
    assert args.height == 480
    assert args.timeout_s == 120.0


def test_smoke_script_rejects_incomplete_png_payload():
    assert smoke_isaac_worker.is_png_like(b"\x89PNG\r\n\x1a\nmetadata") is False
    assert smoke_isaac_worker.is_png_like(b"\x89PNG\r\n\x1a\npayloadIEND\xaeB`\x82") is True
