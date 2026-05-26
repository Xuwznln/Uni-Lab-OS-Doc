"""单元测试：sirna 工作站新增的「等待订单完成」+「人工下料」两个节点。

覆盖 plan ``plan/2026-05-26_sirna_wait_finish_and_unload_nodes_plan.md`` §八 的测试目标：

- ``process_order_finish_report`` override：先 super() 再保存 state，orderCode 匹配触发 event；
  super() 抛错时仍要触发 event（防御性）。
- ``_build_unload_rows_from_all_stock_material`` + ``_build_unload_table``：4 列 ``{"name", "key"}``
  结构、同名物料多库位拆多行、空 location 占位。
- ``wait_for_order_finish``：超时、立即唤醒、status 映射、用 ``order_id`` 调
  ``all_stock_material``（不是 order_code）、``order_code`` 兜底反查、缺 order_id 报错。
- ``unload_materials``：``materials_unloaded=False`` raise；调用 ``rpc.take_out(order_id, [], [])``；
  code==1 → success，code==99 → success=False。
- AST 可见性：``wait_for_order_finish`` / ``unload_materials`` / ``start_experiment`` 的
  metadata（goal_default、handles、placeholder_keys、node_type）符合 plan 设计。

测试设计原则：

- 使用 ``object.__new__(BioyondSirnaStation)`` 跳过 ``__init__``，避免 ROS / HTTP / deck 启动；
  仅注入测试所需的字段。``sirna_station.py`` 在缺 ``pylabrobot`` / ``rclpy`` 时已通过
  ``_BIOYOND_IMPORT_ERROR`` fallback 路径正常加载（``BioyondWorkstation == object``）。
  override 里的 ``super().process_order_finish_report(...)`` 会触发 AttributeError 走 except
  分支 —— 该分支必须仍然驱动事件，这是防御逻辑的核心断言。
- 用 fake RPC 替代 ``self.hardware_interface``；fake RPC 只暴露被测方法。
- AST 测试通过 ``unilabos.registry.ast_registry_scanner._parse_file`` 解析源码，
  不需要启动完整 registry。
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODULE_PATH = "unilabos.devices.workstation.bioyond_studio.sirna_station.sirna_station"
SIRNA_STATION_PATH = (
    REPO_ROOT / "unilabos/devices/workstation/bioyond_studio/sirna_station/sirna_station.py"
)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _import_sirna_module() -> Any:
    return importlib.import_module(MODULE_PATH)


def _fresh_station() -> Any:
    """Construct a bare ``BioyondSirnaStation`` instance for behavior tests.

    Skips ``__init__`` and only sets the fields the action body actually reads.
    """
    module = _import_sirna_module()
    cls = getattr(module, "BioyondSirnaStation")
    station = object.__new__(cls)
    station.bioyond_config = {
        "api_host": "http://bioyond.invalid",
        "api_key": "offline-key",
    }
    station.order_finish_event = threading.Event()
    station.last_order_code = None
    station.last_order_report = None
    station.last_used_materials = []
    # Bypass debug-call-session context manager (would otherwise reach base impl).
    station._debug_call_session = lambda _name: nullcontext()
    return station


class _ReportRequest:
    """Mimic the ``WorkstationReportRequest`` object the base callback expects."""

    def __init__(self, data: Dict[str, Any]) -> None:
        self.data = data


class _FakeUsedMaterial:
    def __init__(self, material_id: str, used_quantity: float) -> None:
        self.materialId = material_id
        self.usedQuantity = used_quantity


# ---------------------------------------------------------------------------
# 1. process_order_finish_report override
# ---------------------------------------------------------------------------


def test_process_order_finish_report_records_state_and_sets_event_on_match() -> None:
    """orderCode 匹配 → event.set + last_order_report/used_materials 落地。

    在轻量环境 (``BioyondWorkstation == object``) 下，``super().process_order_finish_report``
    本身会触发 ``AttributeError``。override 的 ``except`` 分支必须吞掉它并继续走完
    "记录 state + set event" 逻辑 —— 这是 wait 节点能拿到推送的最小保证。
    """
    station = _fresh_station()
    station.last_order_code = "EXP-0001"

    request = _ReportRequest({"orderCode": "EXP-0001", "status": "30"})
    used = [_FakeUsedMaterial("mat-1", 1.5)]

    result = station.process_order_finish_report(request, used)

    assert station.order_finish_event.is_set(), "matching orderCode must set the event"
    assert station.last_order_report == {"orderCode": "EXP-0001", "status": "30"}
    assert station.last_used_materials == used
    # 异常 fallback 路径返回 {"processed": False, "error": ...}
    assert isinstance(result, dict)
    if "from_super" not in result:
        assert result.get("processed") is False
        assert "error" in result and result["error"]


def test_process_order_finish_report_does_not_set_event_on_mismatch() -> None:
    station = _fresh_station()
    station.last_order_code = "EXP-EXPECTED"

    request = _ReportRequest({"orderCode": "EXP-OTHER", "status": "30"})
    station.process_order_finish_report(request, [])

    assert not station.order_finish_event.is_set(), "mismatched orderCode must NOT trigger event"
    # 即使不匹配，仍然要保留最近一次推送，便于排错
    assert station.last_order_report == {"orderCode": "EXP-OTHER", "status": "30"}
    assert station.last_used_materials == []


def test_process_order_finish_report_does_not_set_event_when_no_expected_code_pending() -> None:
    """如果当前没有 wait 节点在等（last_order_code is None），不应触发事件。"""
    station = _fresh_station()
    station.last_order_code = None

    station.process_order_finish_report(_ReportRequest({"orderCode": "EXP-X", "status": "30"}), [])

    assert not station.order_finish_event.is_set()


# ---------------------------------------------------------------------------
# 2. _build_unload_rows_from_all_stock_material + _build_unload_table
# ---------------------------------------------------------------------------


def test_build_unload_rows_uses_four_columns_from_locations() -> None:
    module = _import_sirna_module()
    cls = getattr(module, "BioyondSirnaStation")
    # 飞书《补充接口》文档示例数据
    rows = cls._build_unload_rows_from_all_stock_material([
        {
            "id": "m1", "code": "0017-00733", "name": "G3-50ul枪头盒",
            "typeMode": "Sample", "unit": "个", "quantity": 1.0, "isUse": False,
            "locations": [
                {"id": "L1", "code": "10-2", "whName": "自动化堆栈",
                 "quantity": 1, "x": 2, "y": 10, "z": 1},
            ],
        }
    ])
    assert rows == [
        {
            "whName": "自动化堆栈",
            "locationCode": "10-2",
            "materialName": "G3-50ul枪头盒",
            "quantity": "1",
        }
    ]
    for row in rows:
        # 4 列下料表只保留 4 个 key
        assert set(row.keys()) == {"whName", "locationCode", "materialName", "quantity"}


def test_build_unload_rows_splits_multi_locations() -> None:
    module = _import_sirna_module()
    cls = getattr(module, "BioyondSirnaStation")
    rows = cls._build_unload_rows_from_all_stock_material([
        {
            "id": "m2", "name": "细胞培养板", "quantity": 2.0,
            "locations": [
                {"code": "10-3", "whName": "自动化堆栈", "quantity": 1},
                {"code": "10-4", "whName": "自动化堆栈", "quantity": 1},
            ],
        }
    ])
    assert len(rows) == 2
    assert [row["locationCode"] for row in rows] == ["10-3", "10-4"]
    assert all(row["materialName"] == "细胞培养板" for row in rows)


def test_build_unload_rows_keeps_empty_location_placeholder() -> None:
    module = _import_sirna_module()
    cls = getattr(module, "BioyondSirnaStation")
    rows = cls._build_unload_rows_from_all_stock_material([
        {"id": "m3", "name": "裂解液", "quantity": 5, "locations": []},
    ])
    assert rows == [
        {"whName": "", "locationCode": "", "materialName": "裂解液", "quantity": "5"},
    ]


def test_build_unload_rows_falls_back_to_top_quantity() -> None:
    module = _import_sirna_module()
    cls = getattr(module, "BioyondSirnaStation")
    rows = cls._build_unload_rows_from_all_stock_material([
        {
            "id": "m4", "name": "ABC", "quantity": 9,
            "locations": [{"code": "1-1", "whName": "WH"}],  # 库位没有 quantity 字段
        }
    ])
    assert rows[0]["quantity"] == "9"


def test_build_unload_rows_handles_empty_input_and_non_dict_items() -> None:
    module = _import_sirna_module()
    cls = getattr(module, "BioyondSirnaStation")
    assert cls._build_unload_rows_from_all_stock_material([]) == []
    assert cls._build_unload_rows_from_all_stock_material(None) == []  # type: ignore[arg-type]
    # 非 dict 元素被跳过
    assert cls._build_unload_rows_from_all_stock_material(["not-a-dict"]) == []  # type: ignore[list-item]


def test_build_unload_table_uses_four_columns_in_name_key_format() -> None:
    module = _import_sirna_module()
    cls = getattr(module, "BioyondSirnaStation")
    columns_const = getattr(module, "UNLOAD_TABLE_COLUMNS")
    assert columns_const == [
        {"name": "设备", "key": "whName"},
        {"name": "位置", "key": "locationCode"},
        {"name": "物料名称", "key": "materialName"},
        {"name": "数量", "key": "quantity"},
    ]

    table = cls._build_unload_table([{"whName": "WH", "locationCode": "1-1",
                                       "materialName": "X", "quantity": "1"}])
    assert table["columns"] == columns_const
    assert table["tableName"] == "下料指引"
    assert table["data"] == [
        {"whName": "WH", "locationCode": "1-1", "materialName": "X", "quantity": "1"}
    ]


# ---------------------------------------------------------------------------
# 3. wait_for_order_finish
# ---------------------------------------------------------------------------


class _FakeRPCForWait:
    """Fake RPC that records calls for wait_for_order_finish behavior tests."""

    def __init__(
        self,
        *,
        order_report_response: Optional[Dict[str, Any]] = None,
        all_stock_response: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.order_report_calls: List[str] = []
        self.all_stock_calls: List[str] = []
        self._order_report_response = order_report_response or {}
        self._all_stock_response = list(all_stock_response or [])

    def order_report(self, order_id: str) -> Dict[str, Any]:
        self.order_report_calls.append(order_id)
        return dict(self._order_report_response)

    def all_stock_material(self, json_str: str) -> List[Dict[str, Any]]:
        self.all_stock_calls.append(json_str)
        return list(self._all_stock_response)


def test_wait_for_order_finish_returns_timeout_when_event_never_fires() -> None:
    """超时分支：``timeout_seconds`` 到达且 event 未触发，应返回 timeout，且不调用 all_stock_material。

    注意：``wait_for_order_finish`` 实现里 ``timeout_seconds=0`` 表示 "不限时"
    （沿用 ``threading.Event.wait(timeout=None)`` 语义），所以测试必须传一个正的
    短超时（1s）来真正触发 timeout 分支。
    """
    station = _fresh_station()
    station.hardware_interface = _FakeRPCForWait()

    result = station.wait_for_order_finish(
        order_id="OID-1",
        order_code="EXP-001",
        timeout_seconds=1,
        poll_mode=True,
        poll_interval_seconds=0.05,
    )
    assert result["order_finish_status"] == "timeout"
    assert result["success"] is False
    assert result["order_id"] == "OID-1"
    assert result["order_code"] == "EXP-001"
    assert result["all_stock_materials"] == []
    assert result["unloadTable"]["data"] == []
    # timeout 时不应调 all_stock_material
    assert station.hardware_interface.all_stock_calls == []


def test_wait_for_order_finish_uses_order_id_not_order_code_when_calling_all_stock_material() -> None:
    """关键验收点：all-stock-material 接收 orderId，不是 order_code（用户 v2 反馈）。"""
    station = _fresh_station()
    rpc = _FakeRPCForWait(
        all_stock_response=[
            {
                "id": "m1", "name": "样品A", "quantity": 1,
                "locations": [{"code": "1-1", "whName": "自动化堆栈"}],
            }
        ],
    )
    station.hardware_interface = rpc

    # 节点入口会先 clear() event，所以需要在轮询开始后再 set。
    def trigger_later() -> None:
        time.sleep(0.05)
        station.last_order_report = {"orderCode": "EXP-001", "status": "30"}
        station.last_used_materials = [_FakeUsedMaterial("mat-1", 1)]
        station.order_finish_event.set()

    threading.Thread(target=trigger_later, daemon=True).start()

    result = station.wait_for_order_finish(
        order_id="OID-1",
        order_code="EXP-001",
        timeout_seconds=2,
        poll_mode=True,
        poll_interval_seconds=0.01,
    )

    assert result["order_finish_status"] == "success"
    assert result["success"] is True
    # 调 all_stock_material 用的是 orderId（不是 order_code）！
    assert len(rpc.all_stock_calls) == 1
    payload = json.loads(rpc.all_stock_calls[0])
    assert payload == {"orderId": "OID-1"}, (
        "all-stock-material 必须接收 orderId 作为输入（plan v2 用户反馈核心要点）"
    )
    # unloadTable 用新 4 列结构
    assert result["unloadTable"]["columns"][0] == {"name": "设备", "key": "whName"}
    assert result["unloadTable"]["data"] == [
        {"whName": "自动化堆栈", "locationCode": "1-1", "materialName": "样品A", "quantity": "1"}
    ]
    # used_materials 序列化成 dict
    assert result["used_materials"] == [{"materialId": "mat-1", "usedQuantity": 1}]


@pytest.mark.parametrize("raw_status,expected_status,expected_success", [
    ("30", "success", True),
    ("-11", "abnormal_stop", True),
    ("-12", "manual_stop", True),
    ("999", "unknown_999", False),
    ("", "missing_status", False),
])
def test_wait_for_order_finish_maps_status(
    raw_status: str, expected_status: str, expected_success: bool,
) -> None:
    station = _fresh_station()
    station.hardware_interface = _FakeRPCForWait()

    def re_set() -> None:
        time.sleep(0.02)
        report: Dict[str, Any] = {"orderCode": "EXP-X"}
        if raw_status:
            report["status"] = raw_status
        station.last_order_report = report
        station.order_finish_event.set()

    threading.Thread(target=re_set, daemon=True).start()

    result = station.wait_for_order_finish(
        order_id="OID-1",
        order_code="EXP-X",
        timeout_seconds=2,
        poll_mode=True,
        poll_interval_seconds=0.01,
    )
    assert result["order_finish_status"] == expected_status
    assert result["success"] is expected_success


def test_wait_for_order_finish_resolves_order_code_via_order_report() -> None:
    """缺 order_code 时，应调 rpc.order_report(order_id) 反查 ``code``。"""
    station = _fresh_station()
    rpc = _FakeRPCForWait(order_report_response={"code": "EXP-FROM-REPORT"})
    station.hardware_interface = rpc

    def re_set() -> None:
        time.sleep(0.02)
        station.last_order_report = {"orderCode": "EXP-FROM-REPORT", "status": "30"}
        station.order_finish_event.set()

    threading.Thread(target=re_set, daemon=True).start()

    result = station.wait_for_order_finish(
        order_id="OID-Z",
        order_code="",  # 未传 → 走反查
        timeout_seconds=2,
        poll_mode=True,
        poll_interval_seconds=0.01,
    )
    assert rpc.order_report_calls == ["OID-Z"]
    assert result["order_code"] == "EXP-FROM-REPORT"
    assert station.last_order_code == "EXP-FROM-REPORT"


def test_wait_for_order_finish_raises_when_no_order_id_or_code() -> None:
    station = _fresh_station()
    station.hardware_interface = _FakeRPCForWait()
    with pytest.raises(ValueError):
        station.wait_for_order_finish(order_id="", order_code="", timeout_seconds=0)


def test_wait_for_order_finish_raises_when_ambiguous_order_ids_without_explicit_id() -> None:
    """多 order_ids 且没传 order_code → 暂不支持，必须 raise（防止误等错订单）。"""
    station = _fresh_station()
    station.hardware_interface = _FakeRPCForWait()
    with pytest.raises(ValueError):
        station.wait_for_order_finish(
            order_id="",
            order_code="",
            order_ids=["OID-A", "OID-B"],
            timeout_seconds=0,
        )


# ---------------------------------------------------------------------------
# 4. unload_materials
# ---------------------------------------------------------------------------


_UNLOAD_FAKE_DEFAULT = object()  # sentinel so callers can explicitly pass response=None


class _FakeRPCForUnload:
    def __init__(self, response: Any = _UNLOAD_FAKE_DEFAULT) -> None:
        self.take_out_calls: List[tuple] = []
        if response is _UNLOAD_FAKE_DEFAULT:
            self._response: Any = {"code": 1, "data": {}, "message": ""}
        else:
            self._response = response

    def take_out(self, order_id: str, preintake_ids: List[str], material_ids: List[str]) -> Any:
        self.take_out_calls.append((order_id, list(preintake_ids), list(material_ids)))
        return self._response


def test_unload_materials_raises_without_confirmation() -> None:
    station = _fresh_station()
    station.hardware_interface = _FakeRPCForUnload()
    with pytest.raises(RuntimeError, match="下料未确认"):
        station.unload_materials(order_id="OID-1", materials_unloaded=False)


def test_unload_materials_raises_without_order_id() -> None:
    station = _fresh_station()
    station.hardware_interface = _FakeRPCForUnload()
    with pytest.raises(ValueError, match="order_id"):
        station.unload_materials(order_id="", materials_unloaded=True)


def test_unload_materials_calls_take_out_with_empty_id_lists() -> None:
    """关键验收点：take-out 形参一律是 ``(order_id, [], [])`` —— 不挑物料，由上游下料表决定。"""
    station = _fresh_station()
    rpc = _FakeRPCForUnload(response={"code": 1, "data": {}, "message": "OK"})
    station.hardware_interface = rpc

    result = station.unload_materials(order_id="OID-1", materials_unloaded=True)

    assert rpc.take_out_calls == [("OID-1", [], [])]
    assert result["success"] is True
    assert result["order_id"] == "OID-1"
    assert result["take_out_result"] == {"code": 1, "data": {}, "message": "OK"}


def test_unload_materials_reports_failure_when_take_out_code_not_one() -> None:
    station = _fresh_station()
    rpc = _FakeRPCForUnload(response={"code": 99, "message": "service error"})
    station.hardware_interface = rpc

    result = station.unload_materials(order_id="OID-1", materials_unloaded=True)
    assert rpc.take_out_calls == [("OID-1", [], [])]
    assert result["success"] is False
    assert "service error" in result["confirmation_message"]


def test_unload_materials_handles_non_dict_take_out_response() -> None:
    station = _fresh_station()
    rpc = _FakeRPCForUnload(response=None)
    station.hardware_interface = rpc

    result = station.unload_materials(order_id="OID-1", materials_unloaded=True)
    assert result["success"] is False
    assert result["take_out_result"] == {}


# ---------------------------------------------------------------------------
# 5. AST 可见性
# ---------------------------------------------------------------------------


def _ast_metadata() -> Dict[str, Dict[str, Any]]:
    scanner = pytest.importorskip("unilabos.registry.ast_registry_scanner")
    devices, _ = scanner._parse_file(SIRNA_STATION_PATH, REPO_ROOT)
    return {device["device_id"]: device for device in devices if device.get("device_id")}


def _sirna_device_meta() -> Dict[str, Any]:
    metadata = _ast_metadata()
    device = metadata.get("bioyond_sirna_station")
    if device is None:
        pytest.skip("sirna 工作站 AST metadata 解析为空，跳过 AST 可见性测试")
    return device


def test_wait_for_order_finish_is_ast_visible_with_expected_handles() -> None:
    device = _sirna_device_meta()
    actions = device["actions"]
    assert "wait_for_order_finish" in actions, "wait_for_order_finish action should be AST-visible"

    meta = actions["wait_for_order_finish"]
    args = meta["action_args"]
    assert args["always_free"] is True
    assert not args.get("node_type"), "wait 节点应为 normal action，非 manual_confirm"

    goal_default = args["goal_default"]
    assert goal_default["order_id"] == ""
    assert goal_default["order_code"] == ""
    assert goal_default["timeout_seconds"] == 36000
    assert goal_default["poll_mode"] is True

    handle_keys = {handle["key"] for handle in args["handles"]}
    # 输入
    assert {"order_id", "order_ids", "order_code"} <= handle_keys
    # 输出
    assert {
        "order_id", "order_code",
        "order_finish_status", "order_finish_report",
        "used_materials", "all_stock_materials", "unloadTable",
    } <= handle_keys


def test_unload_materials_is_ast_visible_as_manual_confirm() -> None:
    device = _sirna_device_meta()
    actions = device["actions"]
    assert "unload_materials" in actions

    meta = actions["unload_materials"]
    args = meta["action_args"]
    assert args["node_type"] == "MANUAL_CONFIRM"
    assert args["always_free"] is True
    assert args["placeholder_keys"]["unloadTable"] == "unilabos_manual_confirm"
    assert args["placeholder_keys"]["assignee_user_ids"] == "unilabos_manual_confirm"

    goal_default = args["goal_default"]
    assert goal_default["order_id"] == ""
    assert goal_default["materials_unloaded"] is False
    assert goal_default["timeout_seconds"] == 3600

    handle_keys = {handle["key"] for handle in args["handles"]}
    # 输入：order_id 必须接 wait 节点；不应有任何 take-out 目标 ID 类的输入
    # （决策：take-out 形参恒为 [] / []，不让操作员误选）
    assert {
        "order_id", "order_code", "unloadTable", "used_materials", "order_finish_report",
    } <= handle_keys
    for forbidden in ("preintakeIds", "materialIds", "preintake_ids", "material_ids"):
        assert forbidden not in handle_keys, (
            f"unload_materials 决策上不应再接收 {forbidden}：take-out 只传 [] / []"
        )


def test_start_experiment_exposes_order_id_outputs() -> None:
    device = _sirna_device_meta()
    actions = device["actions"]
    assert "start_experiment" in actions

    handles = actions["start_experiment"]["action_args"]["handles"]
    all_keys = [handle["key"] for handle in handles]
    # 三个输出 handle 必须存在（同名 input/output 的 key 会重复，所以用 list 计数）
    for required in ("order_id", "order_ids", "order_code"):
        assert required in all_keys, (
            f"start_experiment 应暴露 {required} handle，当前 handle keys={all_keys}"
        )
    # 至少存在一份 output 版本：data_source 是 EXECUTOR 字符串或 DataSource.EXECUTOR 引用
    output_keys: List[str] = []
    for handle in handles:
        ds = handle.get("data_source")
        ds_text = str(ds) if ds is not None else ""
        if "EXECUTOR" in ds_text:
            output_keys.append(handle["key"])
    assert {"order_id", "order_ids", "order_code"} <= set(output_keys), (
        f"start_experiment 应将 order_id/order_ids/order_code 暴露为 EXECUTOR 输出，"
        f"当前 EXECUTOR handles={output_keys}"
    )


# ---------------------------------------------------------------------------
# 6. ORDER_FINISH_STATUS_MAP（防御性）
# ---------------------------------------------------------------------------


def test_order_finish_status_map_covers_known_bioyond_statuses() -> None:
    module = _import_sirna_module()
    status_map = getattr(module, "ORDER_FINISH_STATUS_MAP")
    assert status_map == {"30": "success", "-11": "abnormal_stop", "-12": "manual_stop"}
