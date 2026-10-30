"""公开源码树独立验证串行动态库，不依赖商业源码或求解器。"""

import json
import socket
import subprocess
import sys
from copy import deepcopy

import pytest

from unilabos.scheduling.plugin import PluginTransport
from unilabos.scheduling.protocol import CONTRACT_DIR, decode_message, encode_message, validate_message
from unilabos.scheduling.startup import configured_engine


def test_public_sdk_does_not_import_runtime_storage_or_solver():
    script = '''
import importlib.abc, sys
class PublicSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("unilabos.workflow", "pydantic", "pylabrobot", "ortools")):
            raise RuntimeError("公开调度 SDK 不应依赖运行态和商业求解器：" + fullname)
sys.meta_path.insert(0, PublicSDK())
from unilabos.scheduling.startup import configured_engine
selected = configured_engine({})
assert selected.engine_id == "unilab.queue"
selected.transport.shutdown()
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode(errors="replace")


@pytest.mark.parametrize("inherited_socket", [True, False], ids=["platform-default", "portable-loopback"])
def test_public_installation_uses_serial_native_protocol(monkeypatch, inherited_socket):
    if not inherited_socket:
        def unavailable(*args, **kwargs):
            raise NotImplementedError

        monkeypatch.setattr(socket, "socketpair", unavailable)
    cases = {item["name"]: item["message"] for item in json.loads(
        (CONTRACT_DIR / "scheduler-engine-v1.examples.json").read_bytes()
    )["cases"]}
    selected = configured_engine({})
    try:
        assert isinstance(selected.transport, PluginTransport)
        assert selected.engine_id == "unilab.queue"
        assert selected.transport.manifest["artifact"]["kind"] == "native"
        for operation in ("describe", "create", "decide", "close"):
            request = deepcopy(cases[operation + "_request"])
            response = decode_message(selected.transport.call(encode_message(request), timeout_ms=5000))
            validate_message(response)
            assert response["ok"], response
            if operation == "decide":
                assert response["result"]["task_scope"] == ["task-A"]
                assert response["result"]["forecast"] is None
    finally:
        selected.transport.shutdown()
