"""公开源码导出与便携包不能包含商业实现、历史或本地秘密。"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_public_source_export_keeps_serial_build_and_excludes_commercial_sources(tmp_path):
    spec = importlib.util.spec_from_file_location("public_export", ROOT / "scripts/export_public_source.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    source = tmp_path / "private"
    files = {
        "unilabos/scheduling/queue.py": "公开串行实现",
        "native/scheduler-queue/Cargo.toml": "串行构建入口",
        "native/scheduler_shared/runtime.rs": "公开 ABI",
        "unilabos/scheduling/cpsat/planning.py": "商业算法秘密",
        "native/scheduler-cpsat/build_bundle.py": "商业构建秘密",
        "unilabos/devices/arm/elite_robot.py": "保留授权的驱动源码",
        ".git/objects/history": "历史中的商业算法",
        ".env": "本地凭证",
        "native/scheduler-queue/target/debug/generated.py": "临时编译内容",
    }
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    (source / "credentials.json").write_text("未跟踪凭证", encoding="utf-8")
    destination = tmp_path / "public"
    tool.export_public_source(source, destination)
    exported = {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()}
    assert exported == {
        "unilabos/scheduling/queue.py", "native/scheduler-queue/Cargo.toml",
        "native/scheduler_shared/runtime.rs",
    }


def test_export_refuses_a_source_without_a_valid_git_index(tmp_path):
    spec = importlib.util.spec_from_file_location("public_export", ROOT / "scripts/export_public_source.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    source = tmp_path / "untracked"
    source.mkdir()
    (source / "credentials.json").write_text("私有信息", encoding="utf-8")
    destination = tmp_path / "public"
    with pytest.raises(RuntimeError, match="Git"):
        tool.export_public_source(source, destination)
    assert not destination.exists()


def test_exported_repository_builds_serial_library_without_commercial_tree(tmp_path):
    spec = importlib.util.spec_from_file_location("public_export", ROOT / "scripts/export_public_source.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    destination = tmp_path / "public"
    tool.export_public_source(ROOT, destination)
    assert not (destination / ".git").exists()
    assert not (destination / "unilabos/scheduling/cpsat").exists()
    assert not (destination / "native/scheduler-cpsat").exists()
    assert {p.name for p in (destination / ".github/workflows").iterdir()} == {"public-scheduler.yml"}
    built = subprocess.run(
        [sys.executable, "native/scheduler-queue/build.py", "--profile", "debug", "--destination",
         "unilabos/scheduling/queue_native"], cwd=destination, capture_output=True, timeout=180,
    )
    assert built.returncode == 0, built.stderr.decode(errors="replace")
    checked = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/scheduling/test_public_native_contract.py"],
        cwd=destination, capture_output=True, timeout=30,
    )
    assert checked.returncode == 0, checked.stdout.decode(errors="replace") + checked.stderr.decode(errors="replace")
