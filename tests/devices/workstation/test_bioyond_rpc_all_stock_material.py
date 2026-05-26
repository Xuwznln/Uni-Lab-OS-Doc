"""单元测试：``BioyondV1RPC.all_stock_material`` 新增方法的 payload 契约。

设计原则：

- 静态 AST 测试（always-on）：在轻量环境（无 ``rclpy``）下也能跑，验证方法存在 +
  其源码出现关键 endpoint / 字段名 / orderId 校验逻辑。
- 运行时测试（``pytest.importorskip("rclpy")``）：在完整环境下跑，验证
  HTTP 出口（url / params / data）严格按飞书《补充接口》文档构造，且空 orderId
  / 失败响应 / 非法 JSON 均返回 ``[]``。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

RPC_PATH = REPO_ROOT / "unilabos/devices/workstation/bioyond_studio/bioyond_rpc.py"


# ---------------------------------------------------------------------------
# AST-only checks (run everywhere; no rclpy / heavy deps required)
# ---------------------------------------------------------------------------


def _all_stock_material_function() -> ast.FunctionDef:
    """Return the AST node for ``BioyondV1RPC.all_stock_material``."""
    source = RPC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(RPC_PATH))
    for module_node in ast.walk(tree):
        if isinstance(module_node, ast.ClassDef) and module_node.name == "BioyondV1RPC":
            for item in module_node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "all_stock_material":
                    return item
    raise AssertionError("BioyondV1RPC.all_stock_material AST node not found")


def test_all_stock_material_method_exists_in_class() -> None:
    func = _all_stock_material_function()
    assert func.args.args[0].arg == "self"
    # 单 json_str 入参（与 stock_material 同形）
    assert [arg.arg for arg in func.args.args] == ["self", "json_str"]


def test_all_stock_material_source_targets_correct_endpoint_and_payload() -> None:
    """源码字面量验证：endpoint、apiKey、requestTime、data、orderId 字段全部出现。"""
    func = _all_stock_material_function()
    source = ast.unparse(func) if hasattr(ast, "unparse") else ""
    # 优先用 ast.unparse；不可用时 fallback 到 segment slicing。
    if not source:
        full_source = RPC_PATH.read_text(encoding="utf-8")
        segment = ast.get_source_segment(full_source, func)
        source = segment or ""
    assert "all-stock-material" in source, (
        "all_stock_material 必须 POST /api/lims/storage/all-stock-material"
    )
    assert "apiKey" in source and "requestTime" in source and "data" in source, (
        "payload 必须包含 apiKey / requestTime / data 三个 Bioyond 标准字段"
    )
    assert "orderId" in source, "必须按 orderId 校验/过滤 —— 不是 orderCode"


# ---------------------------------------------------------------------------
# Runtime payload tests (require rclpy → BaseRequest import chain)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rpc_class() -> Any:
    pytest.importorskip(
        "rclpy",
        reason="BioyondV1RPC depends on rclpy via unilabos.device_comms.rpc",
    )
    from unilabos.devices.workstation.bioyond_studio.bioyond_rpc import BioyondV1RPC
    return BioyondV1RPC


def _make_rpc(rpc_class: Any) -> Any:
    """Create a BioyondV1RPC without invoking __init__ (avoids material cache I/O)."""
    instance = object.__new__(rpc_class)
    instance.config = {"api_key": "test-key", "api_host": "http://invalid.local"}
    instance.api_key = "test-key"
    instance.host = "http://invalid.local"
    instance.location_mapping = {}
    instance.material_cache = {}
    # SimpleLogger fallback that records ERROR messages without printing.
    class _SilentLogger:
        def __init__(self) -> None:
            self.errors: List[str] = []
        def info(self, msg: Any) -> None: pass
        def debug(self, msg: Any) -> None: pass
        def warning(self, msg: Any) -> None: pass
        def critical(self, msg: Any) -> None: pass
        def error(self, msg: Any) -> None:
            self.errors.append(str(msg))
    instance._logger = _SilentLogger()
    return instance


def test_all_stock_material_runtime_posts_to_correct_url_with_order_id(rpc_class: Any) -> None:
    rpc = _make_rpc(rpc_class)
    posted: Dict[str, Any] = {}

    def fake_post(*, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        posted["url"] = url
        posted["params"] = params
        return {"code": 1, "data": [{"id": "m1", "name": "X"}]}

    rpc.post = fake_post  # type: ignore[method-assign]

    result = rpc.all_stock_material(json.dumps({"orderId": "OID-xyz", "typeMode": 0}))

    assert result == [{"id": "m1", "name": "X"}]
    assert posted["url"] == "http://invalid.local/api/lims/storage/all-stock-material"
    assert posted["params"]["apiKey"] == "test-key"
    assert "requestTime" in posted["params"], "Bioyond payload 缺 requestTime"
    assert posted["params"]["data"] == {"orderId": "OID-xyz", "typeMode": 0}


def test_all_stock_material_runtime_returns_empty_when_order_id_missing(rpc_class: Any) -> None:
    rpc = _make_rpc(rpc_class)
    rpc.post = lambda **_kw: pytest.fail(  # type: ignore[method-assign]
        "缺 orderId 时不应发起 HTTP 请求"
    )
    assert rpc.all_stock_material(json.dumps({"typeMode": 0})) == []
    assert any("orderId" in msg for msg in rpc._logger.errors)


def test_all_stock_material_runtime_returns_empty_on_invalid_json(rpc_class: Any) -> None:
    rpc = _make_rpc(rpc_class)
    rpc.post = lambda **_kw: pytest.fail(  # type: ignore[method-assign]
        "json 解析失败时不应发起 HTTP 请求"
    )
    assert rpc.all_stock_material("not-json") == []


def test_all_stock_material_runtime_returns_empty_when_code_not_one(rpc_class: Any) -> None:
    rpc = _make_rpc(rpc_class)
    rpc.post = lambda **_kw: {"code": 99, "message": "boom", "data": [{"id": "x"}]}  # type: ignore[method-assign]
    assert rpc.all_stock_material(json.dumps({"orderId": "OID-1"})) == []


def test_all_stock_material_runtime_returns_empty_when_post_returns_falsy(rpc_class: Any) -> None:
    rpc = _make_rpc(rpc_class)
    rpc.post = lambda **_kw: None  # type: ignore[method-assign]
    assert rpc.all_stock_material(json.dumps({"orderId": "OID-1"})) == []


def test_all_stock_material_runtime_returns_data_list_on_success(rpc_class: Any) -> None:
    rpc = _make_rpc(rpc_class)
    sample_payload: List[Dict[str, Any]] = [
        {
            "id": "m1", "code": "0017-00733", "name": "G3-50ul枪头盒",
            "typeMode": "Sample",
            "locations": [{"code": "10-2", "whName": "自动化堆栈", "quantity": 1}],
        }
    ]
    rpc.post = lambda **_kw: {"code": 1, "data": sample_payload}  # type: ignore[method-assign]
    assert rpc.all_stock_material(json.dumps({"orderId": "OID-1"})) == sample_payload
