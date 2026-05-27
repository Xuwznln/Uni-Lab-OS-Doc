"""单元测试：``BioyondV1RPC.all_stock_material`` 方法的 payload 契约。

设计原则：

- 静态 AST 测试（always-on）：在轻量环境（无 ``rclpy``）下也能跑，验证方法存在 +
  其源码出现关键 endpoint / 字段名 / orderId 校验逻辑。
- 运行时测试（``pytest.importorskip("rclpy")``）：在完整环境下跑，验证
  HTTP 出口（url / params / data）严格按飞书《瑞博 LIMS 通信协议》
  「实验物料详情查询接口」(``/api/lims/storage/materials-by-order-id``)
  构造 —— ``data`` 字段直接传 orderId GUID 字符串 ——
  且空 orderId / 失败响应 / 非法 JSON / ``data: null`` 均返回 ``[]``。

历史背景：方法名保留 ``all_stock_material``（不重命名），便于上游 station
代码与前端 handle key ``all_stock_materials`` 不变；endpoint 已从
``/api/lims/storage/all-stock-material``（仿真器未实现，404）迁移到
飞书主协议文档里实际存在的 ``materials-by-order-id``。
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
    assert "materials-by-order-id" in source, (
        "all_stock_material 必须 POST /api/lims/storage/materials-by-order-id"
        "（飞书《瑞博 LIMS 通信协议》「实验物料详情查询接口」）"
    )
    assert "all-stock-material" not in source, (
        "旧 endpoint /api/lims/storage/all-stock-material 在仿真器返回 404，"
        "不应继续出现在源码里"
    )
    assert "apiKey" in source and "requestTime" in source and "data" in source, (
        "payload 必须包含 apiKey / requestTime / data 三个 Bioyond 标准字段"
    )
    assert "orderId" in source, "必须按 orderId 校验 —— 不是 orderCode"


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

    # 入参 dict 里多余字段（如历史 typeMode）应被忽略——新接口只认 orderId。
    result = rpc.all_stock_material(json.dumps({"orderId": "OID-xyz", "typeMode": 0}))

    assert result == [{"id": "m1", "name": "X"}]
    assert posted["url"] == "http://invalid.local/api/lims/storage/materials-by-order-id"
    assert posted["params"]["apiKey"] == "test-key"
    assert "requestTime" in posted["params"], "Bioyond payload 缺 requestTime"
    # 飞书文档明确：materials-by-order-id 的 data 字段是 GUID 字符串，不是对象。
    assert posted["params"]["data"] == "OID-xyz", (
        "materials-by-order-id 的 data 字段必须是 orderId GUID 字符串，不能传对象"
    )


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
            "typeName": "G3-50ul枪头盒",
            "locations": [{"code": "10-2", "whName": "自动化堆栈", "quantity": 1}],
        }
    ]
    rpc.post = lambda **_kw: {"code": 1, "data": sample_payload}  # type: ignore[method-assign]
    assert rpc.all_stock_material(json.dumps({"orderId": "OID-1"})) == sample_payload


def test_all_stock_material_runtime_returns_empty_when_response_data_is_null(rpc_class: Any) -> None:
    """data 为 null 时不能返回 None（否则 wait_for_order_finish 里 isinstance(raw, list) 假，下游 unloadTable.data=[] 但不易诊断）。"""
    rpc = _make_rpc(rpc_class)
    rpc.post = lambda **_kw: {"code": 1, "data": None}  # type: ignore[method-assign]
    assert rpc.all_stock_material(json.dumps({"orderId": "OID-1"})) == []
