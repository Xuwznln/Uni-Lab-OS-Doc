"""串行公开发行包在源码目录之外运行，验证依赖闭包与真实生产装配。"""

import ast
import os
import subprocess
import sys
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]
_BUILD_TIMEOUT_SECONDS = 180


def _capture(command, *, cwd, timeout, env=None):
    """按 UTF-8 读取构建日志，避免 Windows 本地编码解码失败。"""

    child_env = dict(os.environ if env is None else env)
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        command,
        cwd=cwd,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )

@pytest.fixture(scope="module")
def distribution(tmp_path_factory):
    directory = tmp_path_factory.mktemp("scheduler-distribution")
    source_dist = directory / "source"
    built = _capture(
        [sys.executable, "setup.py", "sdist", "--dist-dir", str(source_dist)],
        cwd=ROOT,
        timeout=_BUILD_TIMEOUT_SECONDS,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    archive = next(source_dist.glob("*.tar.gz"))
    unpacked = directory / "unpacked"
    with tarfile.open(archive) as source:
        source.extractall(unpacked, filter="data")
    source_root = next(unpacked.iterdir())
    wheel_dir = directory / "wheels"
    built = _capture(
        [sys.executable, "setup.py", "bdist_wheel", "--dist-dir", str(wheel_dir)],
        cwd=source_root,
        timeout=_BUILD_TIMEOUT_SECONDS,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    installed = directory / "installed"
    wheel = next(wheel_dir.glob("*.whl"))
    with zipfile.ZipFile(wheel) as package:
        package.extractall(installed)
    return directory, installed, archive, wheel

def run_installed(distribution, script, *arguments, python_executable=None, installed_root=None):
    directory, installed, _, _ = distribution
    installed = installed_root or installed
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("UNILABOS_SCHEDULING_", "UNILABOS_TIME_SCHEDULING_", "ULAB_ESTIMATE_"))
    }
    environment.update(PUBCHEM_ADDR="off", ULAB_DEVICE_STATE_DB="off")
    bootstrap = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import unilabos
assert Path(unilabos.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
"""
    result = _capture(
        [str(python_executable or sys.executable), "-I", "-c", bootstrap + script, str(installed), *map(str, arguments)],
        cwd=directory,
        env=environment,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

def test_installed_queue_has_the_complete_contract_and_runs_all_four_operations(distribution):
    run_installed(distribution, """
import importlib.abc
import json
class NoSolver(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("unilabos.devices"):
            raise ModuleNotFoundError("公开包没有保留授权的驱动", name=fullname)
        if fullname.startswith(("ortools", "unilabos.scheduling.cpsat")):
            raise RuntimeError("默认队列不能导入求解器：" + fullname)
sys.meta_path.insert(0, NoSolver())
from unilabos.scheduling.plugin import verify_contract
from unilabos.scheduling.protocol import CONTRACT_DIR, CONTRACT_SHA256, decode_message, encode_message
from unilabos.scheduling.startup import configured_engine
verify_contract(CONTRACT_DIR, CONTRACT_SHA256)
assert (CONTRACT_DIR.parent / "resource-bindings-v1.md").is_file()
cases = {
    item["name"]: item["message"]
    for item in json.loads((CONTRACT_DIR / "scheduler-engine-v1.examples.json").read_bytes())["cases"]
}
selected = configured_engine({})
engine = selected.transport
from unilabos.scheduling.plugin import PluginTransport
assert isinstance(engine, PluginTransport)
for operation in ("describe", "create", "decide", "close"):
    request = cases[operation + "_request"]
    response = decode_message(engine.call(encode_message(request), timeout_ms=request["timeout_ms"]))
    assert response["ok"], response
    if operation == "describe":
        assert response["result"]["engine_id"] == "unilab.queue"
    elif operation == "decide":
        assert response["result"]["task_scope"] == ["task-A"]
        assert response["result"]["dispatch_now"][0]["node"]["node_id"] == "node-A1"
engine.shutdown()
from unilabos.resources.resource_tracker import ResourceTreeSet
assert ResourceTreeSet([]).to_plr_resources() == []
from pylabrobot.resources import Resource
tree = ResourceTreeSet.from_plr_resources([Resource("public-sample", 1, 2, 3)], known_newly_created=True)
assert [resource.name for resource in tree.to_plr_resources()] == ["public-sample"]
""")

def test_public_distribution_separates_solver_sources_and_optional_runtime(distribution):
    _, _, archive, wheel = distribution
    with tarfile.open(archive) as source:
        assert not any("/unilabos/scheduling/cpsat/" in name for name in source.getnames())
        assert not any("/native/scheduler-cpsat/" in name for name in source.getnames())
        assert not any("/unilabos/devices/" in name for name in source.getnames())
    with zipfile.ZipFile(wheel) as package:
        assert not any(name.startswith("unilabos/scheduling/cpsat/") for name in package.namelist())
        assert not any(name.startswith("unilabos/devices/") for name in package.namelist())
        for module in ("time_scheduling", "workflow_planning", "time_planning", "estimation", "timing"):
            shim = ast.parse(package.read(f"unilabos/app/scheduler/{module}.py").decode())
            assert not any(isinstance(node, (ast.ClassDef, ast.FunctionDef)) for node in ast.walk(shim))
        assert "unilabos/scheduling/queue_native/plugin.json" in package.namelist()
        assert any(name.startswith("unilabos/scheduling/queue_native/") and name.endswith((".so", ".dylib", ".dll"))
                   for name in package.namelist())
        metadata_path = next(name for name in package.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(package.read(metadata_path).decode())
    assert metadata["Requires-Python"] == ">=3.11"
    assert "-py3-none-" in wheel.name
    dependencies = [Requirement(value) for value in metadata.get_all("Requires-Dist", [])]
    solvers = [item for item in dependencies if item.name == "ortools"]
    assert solvers == []

def test_installed_production_queue_finishes_tasks_serially_without_solver(distribution):
    run_installed(distribution, (Path(__file__).with_name("installed_queue_smoke.py")).read_text(encoding="utf-8"))

@pytest.mark.skipif(os.environ.get("UNILAB_TEST_CLEAN_INSTALL") != "1", reason="显式开启空环境安装验收")
def test_clean_install_runs_the_serial_platform_without_solver(distribution):
    import venv

    directory, _, _, wheel = distribution
    environment_root = directory / "clean-venv"
    venv.EnvBuilder(with_pip=True).create(environment_root)
    executable = environment_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    installed = _capture(
        [str(executable), "-I", "-m", "pip", "install", "--disable-pip-version-check", str(wheel)],
        cwd=directory,
        timeout=180,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    script = """
import importlib.util
assert importlib.util.find_spec("ortools") is None
assert importlib.util.find_spec("unilabos.scheduling.cpsat") is None
assert importlib.util.find_spec("unilabos.devices") is None
""" + Path(__file__).with_name("installed_queue_smoke.py").read_text(encoding="utf-8")
    run_installed(distribution, script, python_executable=executable, installed_root=environment_root)
