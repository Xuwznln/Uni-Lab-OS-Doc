from __future__ import annotations

from pathlib import Path

from unilabos.sim.fleet.rmf.runtime.launcher import RmfRuntimeLauncher, RmfRuntimeOptions
from unilabos.sim.fleet.rmf.runtime.policy import build_startup_policy


def test_build_startup_policy_defaults():
    policy = build_startup_policy(
        {"generated_map_dir": "/tmp/unilab_runtime/maps/latest"},
        generated_map_dir="/tmp/unilab_runtime/maps/latest",
    )
    assert policy.auto_compile_map is False
    assert policy.auto_start_runtime is False
    assert policy.runtime_mode == "headless"
    assert policy.runtime_root == "/tmp/unilab_runtime"
    assert policy.map_dir == "/tmp/unilab_runtime/maps/latest"
    assert policy.should_bootstrap is False


def test_build_startup_policy_with_flags():
    policy = build_startup_policy(
        {
            "generated_map_dir": "/tmp/x/maps/latest",
            "auto_compile_map": "1",
            "auto_start_runtime": "true",
            "runtime_mode": "sim",
            "runtime_use_sim_time": "yes",
            "layout_optimizer_dir": "/tmp/layout",
            "runtime_root": "/tmp/runtime",
            "start_api_server": "false",
            "stop_before_start": "on",
        }
    )
    assert policy.should_bootstrap is True
    assert policy.auto_compile_map is True
    assert policy.auto_start_runtime is True
    assert policy.runtime_mode == "sim"
    assert policy.runtime_use_sim_time is True
    assert policy.layout_optimizer_dir == "/tmp/layout"
    assert policy.runtime_root == "/tmp/runtime"
    assert policy.start_api_server is False
    assert policy.stop_before_start is True


def test_launcher_option_path_normalization(tmp_path: Path):
    runtime_root = tmp_path / "runtime"
    map_dir = runtime_root / "maps" / "latest"
    map_dir.mkdir(parents=True)

    launcher = RmfRuntimeLauncher()
    opt = RmfRuntimeOptions(runtime_root=str(runtime_root), map_dir="")
    normalized = launcher._normalize_options(opt)  # noqa: SLF001 - internal contract test
    assert normalized.runtime_root == str(runtime_root.resolve())
    assert normalized.map_dir == str(map_dir.resolve())


def test_launcher_status_without_state(tmp_path: Path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)
    launcher = RmfRuntimeLauncher()
    out = launcher.runtime_status(RmfRuntimeOptions(runtime_root=str(runtime_root), map_dir=""))
    assert out["success"] is True
    assert out["stateExists"] is False
    assert isinstance(out["processes"], list)

