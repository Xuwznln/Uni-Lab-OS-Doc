"""单元测试：sirna 工作站甘特图回传（device_info 触发）。

覆盖 plan ``plan/甘特图回传节点_6e5ec4ab.plan.md`` 的核心行为：

- ``_query_orders_for_gantt``：把 payload 查询参数**原样透传**给 ``order_query``
  （status 原始值、空=不限状态），按返回的 ``totalCount`` **自动翻页**收集所有
  ``(order_id, status)``（status 取 item 整数字段）；缺 ``pageCount`` 时用默认批大小；
  脏 ``totalCount`` 时遇空页即停（不死循环）；跨页 order_id 去重。
- ``_fetch_gantt_for_order``：按订单 status 选甘特接口——
  ``status==60`` 先查 3.29 ``gantts_by_order_id``、空才回退 3.30
  ``gantt_with_simulation_by_order_id``；``status ∈ {80,90,100}`` 只查 3.29；
  其它/未知 status 跳过（返回 None）。输出统一为 ``{"items": [...]}``。
- ``_gantt_report_worker``：遍历 ``(order_id, status)`` 调上面方法，跳过 None，
  汇总成数组**只 POST 一次**；无可回传订单时不 POST。

测试设计原则同 ``test_sirna_wait_unload.py``：用 ``object.__new__`` 跳过 ``__init__``，
fake RPC 替代 ``self.hardware_interface``；worker 测试用 ``monkeypatch`` 注入假的
``unilabos.app.web.http_client``，避免拉起真实 web 依赖。
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODULE_PATH = "unilabos.devices.workstation.bioyond_studio.sirna_station.sirna_station"


def _import_sirna_module() -> Any:
    return importlib.import_module(MODULE_PATH)


def _bare_station() -> Any:
    """构造一个最小可用的 BioyondSirnaStation 实例（跳过 __init__）。"""
    module = _import_sirna_module()
    cls = getattr(module, "BioyondSirnaStation")
    return object.__new__(cls)


def _order(oid: str, status: Any) -> Dict[str, Any]:
    return {"id": oid, "status": status}


class _FakeRPCForGantt:
    """记录调用的 fake RPC：分页返回订单、按 order_id 返回 3.29 / 3.30 甘特原始响应。"""

    def __init__(
        self,
        orders: Optional[List[Dict[str, Any]]] = None,
        *,
        g29: Optional[Dict[str, Any]] = None,
        g30: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._orders = list(orders or [])
        self.order_query_payloads: List[Dict[str, Any]] = []
        self.g29_calls: List[str] = []
        self.g30_calls: List[str] = []
        self._g29 = g29 or {}  # order_id -> 3.29 data（数组）或 None
        self._g30 = g30 or {}  # order_id -> 3.30 data（{"items":[...]}）

    def order_query(self, json_str: str, *, return_envelope: bool = False) -> Dict[str, Any]:
        payload = json.loads(json_str)
        self.order_query_payloads.append(payload)
        skip = int(payload.get("skipCount", 0) or 0)
        page = int(payload.get("pageCount", 0) or 0)
        items = self._orders[skip: skip + page] if page > 0 else self._orders[skip:]
        return {"totalCount": len(self._orders), "items": items}

    def gantts_by_order_id(self, order_id: str, *, return_envelope: bool = False) -> Dict[str, Any]:
        self.g29_calls.append(order_id)
        # 3.29 响应：data 是数组（实际执行步骤）；未配置则为 None（表示空）
        return {"code": 1, "data": self._g29.get(order_id), "message": ""}

    def gantt_with_simulation_by_order_id(
        self, order_id: str, *, return_envelope: bool = False
    ) -> Dict[str, Any]:
        self.g30_calls.append(order_id)
        # 3.30 响应：data 是 {"items":[...]}
        return {"code": 1, "data": self._g30.get(order_id, {"items": []}), "message": ""}


class _FakeRPCDirtyTotal:
    """totalCount 谎报很大，但第二页起返回空 items —— 验证遇空页即停、不死循环。"""

    def __init__(self, first_page_items: List[Dict[str, Any]], fake_total: int) -> None:
        self._first = list(first_page_items)
        self._fake_total = fake_total
        self.calls = 0

    def order_query(self, json_str: str, *, return_envelope: bool = False) -> Dict[str, Any]:
        self.calls += 1
        items = self._first if self.calls == 1 else []
        return {"totalCount": self._fake_total, "items": items}


def _ids(orders: List[Dict[str, Any]]) -> List[str]:
    return [o["order_id"] for o in orders]


# ---------------------------------------------------------------------------
# 1. _query_orders_for_gantt 翻页逻辑
# ---------------------------------------------------------------------------


def test_query_orders_collects_all_pages() -> None:
    station = _bare_station()
    items = [_order(f"OID-{i}", 60) for i in range(25)]
    rpc = _FakeRPCForGantt(items)
    station.hardware_interface = rpc

    orders = station._query_orders_for_gantt({"status": "60", "pageCount": 10, "skipCount": 0})

    assert _ids(orders) == [f"OID-{i}" for i in range(25)]
    assert [p["skipCount"] for p in rpc.order_query_payloads] == [0, 10, 20]
    assert all(p["pageCount"] == 10 for p in rpc.order_query_payloads)


def test_query_orders_captures_status_as_int() -> None:
    """status 取 item 整数字段；非整数 → None。"""
    station = _bare_station()
    rpc = _FakeRPCForGantt([
        _order("A", 60), _order("B", 80), _order("C", None), _order("D", "x"),
    ])
    station.hardware_interface = rpc

    orders = station._query_orders_for_gantt({"pageCount": 10})
    by_id = {o["order_id"]: o["status"] for o in orders}
    assert by_id == {"A": 60, "B": 80, "C": None, "D": None}


def test_query_orders_single_page_when_total_within_pagecount() -> None:
    station = _bare_station()
    rpc = _FakeRPCForGantt([_order("A", 80), _order("B", 80), _order("C", 80)])
    station.hardware_interface = rpc

    orders = station._query_orders_for_gantt({"status": "", "pageCount": 10})
    assert _ids(orders) == ["A", "B", "C"]
    assert len(rpc.order_query_payloads) == 1


def test_query_orders_passes_status_raw_and_empty() -> None:
    """order-list 过滤入参 status 原始值透传：空/None → ""，"60" 原样传。"""
    station = _bare_station()
    rpc = _FakeRPCForGantt([_order("A", 60)])
    station.hardware_interface = rpc

    station._query_orders_for_gantt({"pageCount": 10})
    assert rpc.order_query_payloads[-1]["status"] == ""

    station._query_orders_for_gantt({"status": None, "pageCount": 10})
    assert rpc.order_query_payloads[-1]["status"] == ""

    station._query_orders_for_gantt({"status": "60", "pageCount": 10})
    assert rpc.order_query_payloads[-1]["status"] == "60"


def test_query_orders_passes_through_time_params() -> None:
    station = _bare_station()
    rpc = _FakeRPCForGantt([_order("A", 60)])
    station.hardware_interface = rpc

    station._query_orders_for_gantt({
        "timeType": "CreationTime",
        "beginTime": "2026-01-01T00:00:00.000Z",
        "endTime": "2026-12-31T23:59:59.999Z",
        "pageCount": 10,
    })
    payload = rpc.order_query_payloads[-1]
    assert payload["timeType"] == "CreationTime"
    assert payload["beginTime"] == "2026-01-01T00:00:00.000Z"
    assert payload["endTime"] == "2026-12-31T23:59:59.999Z"


def test_query_orders_defaults_pagecount_when_missing() -> None:
    station = _bare_station()
    rpc = _FakeRPCForGantt([_order("A", 60)])
    station.hardware_interface = rpc

    station._query_orders_for_gantt({"status": "60"})
    assert rpc.order_query_payloads[-1]["pageCount"] == 50


def test_query_orders_honors_skipcount_start() -> None:
    station = _bare_station()
    items = [_order(f"OID-{i}", 60) for i in range(8)]
    rpc = _FakeRPCForGantt(items)
    station.hardware_interface = rpc

    orders = station._query_orders_for_gantt({"pageCount": 5, "skipCount": 5})
    assert _ids(orders) == ["OID-5", "OID-6", "OID-7"]
    assert rpc.order_query_payloads[0]["skipCount"] == 5


def test_query_orders_dedups_across_pages() -> None:
    station = _bare_station()
    items = [_order("DUP", 60), _order("X", 60), _order("DUP", 80), _order("Y", 60)]
    rpc = _FakeRPCForGantt(items)
    station.hardware_interface = rpc

    orders = station._query_orders_for_gantt({"pageCount": 2})
    assert _ids(orders) == ["DUP", "X", "Y"]


def test_query_orders_stops_on_empty_page_even_if_total_lies() -> None:
    station = _bare_station()
    rpc = _FakeRPCDirtyTotal([_order("A", 60), _order("B", 60)], fake_total=10_000)
    station.hardware_interface = rpc

    orders = station._query_orders_for_gantt({"pageCount": 2})
    assert _ids(orders) == ["A", "B"]
    assert rpc.calls == 2


def test_query_orders_returns_empty_when_no_orders() -> None:
    station = _bare_station()
    station.hardware_interface = _FakeRPCForGantt([])
    assert station._query_orders_for_gantt({"pageCount": 10}) == []


def test_query_orders_skips_items_without_id() -> None:
    station = _bare_station()
    rpc = _FakeRPCForGantt([
        _order("A", 60), _order("", 60), {"name": "no-id", "status": 60}, _order("B", 80),
    ])
    station.hardware_interface = rpc
    assert _ids(station._query_orders_for_gantt({"pageCount": 10})) == ["A", "B"]


# ---------------------------------------------------------------------------
# 2. _fetch_gantt_for_order：按 status 选接口
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [80, 90, 100])
def test_fetch_finished_status_uses_only_3_29(status: int) -> None:
    """成功/失败/已取出 → 只查 3.29，返回 {"items": <3.29数组>}，不调 3.30。"""
    station = _bare_station()
    rpc = _FakeRPCForGantt(g29={"OID-1": [{"id": "g1"}, {"id": "g2"}]})
    station.hardware_interface = rpc

    result = station._fetch_gantt_for_order("OID-1", status)
    assert result == {"items": [{"id": "g1"}, {"id": "g2"}]}
    assert rpc.g29_calls == ["OID-1"]
    assert rpc.g30_calls == []


def test_fetch_running_uses_3_29_when_non_empty() -> None:
    """status=60 且 3.29 非空 → 用 3.29，不回退 3.30。"""
    station = _bare_station()
    rpc = _FakeRPCForGantt(
        g29={"OID-1": [{"id": "g1"}]},
        g30={"OID-1": {"items": [{"code": "sim"}]}},
    )
    station.hardware_interface = rpc

    result = station._fetch_gantt_for_order("OID-1", 60)
    assert result == {"items": [{"id": "g1"}]}
    assert rpc.g29_calls == ["OID-1"]
    assert rpc.g30_calls == [], "3.29 非空时不应回退 3.30"


def test_fetch_running_falls_back_to_3_30_when_3_29_empty() -> None:
    """status=60 且 3.29 空 → 回退 3.30，返回 3.30 的 {"items":[...]}。"""
    station = _bare_station()
    rpc = _FakeRPCForGantt(
        g29={"OID-1": None},  # 3.29 空
        g30={"OID-1": {"items": [{"code": "sim-1"}]}},
    )
    station.hardware_interface = rpc

    result = station._fetch_gantt_for_order("OID-1", 60)
    assert result == {"items": [{"code": "sim-1"}]}
    assert rpc.g29_calls == ["OID-1"]
    assert rpc.g30_calls == ["OID-1"]


def test_fetch_running_empty_list_also_falls_back() -> None:
    """3.29 返回空数组(而非 None)同样触发回退。"""
    station = _bare_station()
    rpc = _FakeRPCForGantt(g29={"OID-1": []}, g30={"OID-1": {"items": [{"code": "s"}]}})
    station.hardware_interface = rpc

    result = station._fetch_gantt_for_order("OID-1", 60)
    assert result == {"items": [{"code": "s"}]}
    assert rpc.g30_calls == ["OID-1"]


def test_fetch_running_both_empty_returns_empty_items() -> None:
    """status=60 两边都没有 → 返回 {"items": []}。"""
    station = _bare_station()
    rpc = _FakeRPCForGantt(g29={"OID-1": None}, g30={})  # g30 默认 {"items":[]}
    station.hardware_interface = rpc

    result = station._fetch_gantt_for_order("OID-1", 60)
    assert result == {"items": []}


@pytest.mark.parametrize("status", [0, 1, None, 999])
def test_fetch_other_status_skipped(status: Any) -> None:
    """其它/未知 status → 跳过(返回 None)，两个甘特接口都不调。"""
    station = _bare_station()
    rpc = _FakeRPCForGantt(g29={"OID-1": [{"id": "g"}]})
    station.hardware_interface = rpc

    assert station._fetch_gantt_for_order("OID-1", status) is None
    assert rpc.g29_calls == []
    assert rpc.g30_calls == []


# ---------------------------------------------------------------------------
# 3. _gantt_report_worker：混合状态、只 POST 一次
# ---------------------------------------------------------------------------


def _inject_fake_http_client(monkeypatch: Any) -> Dict[str, Any]:
    captured: Dict[str, Any] = {"calls": 0}

    class _FakeHTTP:
        def report_gantt(self, uuid: str, data: Any) -> None:
            captured["calls"] += 1
            captured["uuid"] = uuid
            captured["data"] = data

    fake_web = types.ModuleType("unilabos.app.web")
    fake_web.http_client = _FakeHTTP()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "unilabos.app.web", fake_web)
    return captured


def test_worker_mixed_statuses_posts_once(monkeypatch: Any) -> None:
    """混合状态：80→3.29；60(3.29空)→回退3.30；0→跳过。只 POST 一次，data 顺序对应保留的订单。"""
    station = _bare_station()
    orders = [_order("F", 80), _order("R", 60), _order("P", 0)]
    rpc = _FakeRPCForGantt(
        orders,
        g29={"F": [{"id": "f1"}], "R": None},  # F 有实际甘特；R 的 3.29 空
        g30={"R": {"items": [{"code": "r-sim"}]}},
    )
    station.hardware_interface = rpc
    captured = _inject_fake_http_client(monkeypatch)

    station._gantt_report_worker("u1", {"status": "", "pageCount": 10})

    assert captured["calls"] == 1
    assert captured["uuid"] == "u1"
    # P(status=0) 被跳过；F 用 3.29，R 回退 3.30；均为 {"items": [...]}
    assert captured["data"] == [{"items": [{"id": "f1"}]}, {"items": [{"code": "r-sim"}]}]
    assert rpc.g29_calls == ["F", "R"]
    assert rpc.g30_calls == ["R"]


def test_worker_skips_post_when_no_orders(monkeypatch: Any) -> None:
    station = _bare_station()
    station.hardware_interface = _FakeRPCForGantt([])
    captured = _inject_fake_http_client(monkeypatch)

    station._gantt_report_worker("u1", {"pageCount": 10})
    assert captured["calls"] == 0


def test_worker_skips_post_when_all_orders_filtered_out(monkeypatch: Any) -> None:
    """所有订单都是被跳过的 status → 没有可回传甘特 → 不 POST。"""
    station = _bare_station()
    rpc = _FakeRPCForGantt([_order("P1", 0), _order("P2", 1)])
    station.hardware_interface = rpc
    captured = _inject_fake_http_client(monkeypatch)

    station._gantt_report_worker("u1", {"pageCount": 10})
    assert captured["calls"] == 0
    assert rpc.g29_calls == []
    assert rpc.g30_calls == []
