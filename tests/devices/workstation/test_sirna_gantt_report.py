"""单元测试：sirna 工作站甘特图回传（device_info 触发）。

覆盖 plan ``plan/甘特图回传节点_6e5ec4ab.plan.md`` 的核心行为：

- ``_query_order_ids_for_gantt``：把 payload 查询参数**原样透传**给 ``order_query``
  （status 原始值、空=不限状态），并按返回的 ``totalCount`` **自动翻页**收集所有
  ``items[].id``；缺 ``pageCount`` 时用默认批大小；脏 ``totalCount`` 时遇空页即停（不死循环）；
  跨页 id 去重。
- ``_gantt_report_worker``：拿到全部 order_id 后逐个拉甘特，**只取响应里的 ``data``**
  （``{"items": [...]}``，去掉与 body 外层 ``data`` 的双层嵌套），汇总成数组**只 POST 一次**；
  payload 查不到订单时不 POST。

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
    """构造一个最小可用的 BioyondSirnaStation 实例（跳过 __init__）。

    ``_query_order_ids_for_gantt`` / ``_gantt_report_worker`` 只依赖
    ``self.hardware_interface``（经 ``_require_hardware_interface`` 取用），其余字段不需要。
    """
    module = _import_sirna_module()
    cls = getattr(module, "BioyondSirnaStation")
    return object.__new__(cls)


class _FakeRPCForGantt:
    """记录调用的 fake RPC：分页返回订单、按 order_id 返回甘特原始响应。"""

    def __init__(
        self,
        total_items: List[Dict[str, Any]],
        *,
        gantt_map: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self._items = list(total_items)
        self.order_query_payloads: List[Dict[str, Any]] = []
        self.gantt_calls: List[str] = []
        self._gantt_map = gantt_map or {}

    def order_query(self, json_str: str, *, return_envelope: bool = False) -> Dict[str, Any]:
        payload = json.loads(json_str)
        self.order_query_payloads.append(payload)
        skip = int(payload.get("skipCount", 0) or 0)
        page = int(payload.get("pageCount", 0) or 0)
        page_items = self._items[skip: skip + page] if page > 0 else self._items[skip:]
        return {"totalCount": len(self._items), "items": page_items}

    def gantt_with_simulation_by_order_id(
        self, order_id: str, *, return_envelope: bool = False
    ) -> Dict[str, Any]:
        self.gantt_calls.append(order_id)
        # 默认返回带一层 data 的 envelope（与奔曜原始响应一致）
        return self._gantt_map.get(
            order_id, {"code": 1, "data": {"items": [order_id]}, "message": ""}
        )


class _FakeRPCDirtyTotal:
    """totalCount 谎报很大，但第二页起返回空 items —— 用来验证遇空页即停、不死循环。"""

    def __init__(self, first_page_items: List[Dict[str, Any]], fake_total: int) -> None:
        self._first = list(first_page_items)
        self._fake_total = fake_total
        self.calls = 0

    def order_query(self, json_str: str, *, return_envelope: bool = False) -> Dict[str, Any]:
        self.calls += 1
        items = self._first if self.calls == 1 else []
        return {"totalCount": self._fake_total, "items": items}


# ---------------------------------------------------------------------------
# 1. _query_order_ids_for_gantt 翻页逻辑
# ---------------------------------------------------------------------------


def test_query_order_ids_collects_all_pages() -> None:
    """totalCount 超过 pageCount 时，自动翻页拿到全部 order_id。"""
    station = _bare_station()
    items = [{"id": f"OID-{i}"} for i in range(25)]
    rpc = _FakeRPCForGantt(items)
    station.hardware_interface = rpc

    order_ids = station._query_order_ids_for_gantt(
        {"status": "60", "pageCount": 10, "skipCount": 0}
    )

    assert order_ids == [f"OID-{i}" for i in range(25)]
    # 25 条、每页 10 → 3 页：skipCount 0/10/20，pageCount 恒为 10
    assert [p["skipCount"] for p in rpc.order_query_payloads] == [0, 10, 20]
    assert all(p["pageCount"] == 10 for p in rpc.order_query_payloads)


def test_query_order_ids_single_page_when_total_within_pagecount() -> None:
    """totalCount <= pageCount 时只查一页。"""
    station = _bare_station()
    rpc = _FakeRPCForGantt([{"id": "A"}, {"id": "B"}, {"id": "C"}])
    station.hardware_interface = rpc

    order_ids = station._query_order_ids_for_gantt({"status": "", "pageCount": 10})

    assert order_ids == ["A", "B", "C"]
    assert len(rpc.order_query_payloads) == 1


def test_query_order_ids_passes_status_raw_and_empty() -> None:
    """status 原始值透传：空/None → ""（不限状态），"60" 原样传，不做标签映射。"""
    station = _bare_station()
    rpc = _FakeRPCForGantt([{"id": "A"}])
    station.hardware_interface = rpc

    station._query_order_ids_for_gantt({"pageCount": 10})  # 无 status
    assert rpc.order_query_payloads[-1]["status"] == ""

    station._query_order_ids_for_gantt({"status": None, "pageCount": 10})
    assert rpc.order_query_payloads[-1]["status"] == ""

    station._query_order_ids_for_gantt({"status": "60", "pageCount": 10})
    assert rpc.order_query_payloads[-1]["status"] == "60"


def test_query_order_ids_passes_through_time_params() -> None:
    """timeType/beginTime/endTime 原样透传给 order-list。"""
    station = _bare_station()
    rpc = _FakeRPCForGantt([{"id": "A"}])
    station.hardware_interface = rpc

    station._query_order_ids_for_gantt({
        "timeType": "CreationTime",
        "beginTime": "2026-01-01T00:00:00.000Z",
        "endTime": "2026-12-31T23:59:59.999Z",
        "pageCount": 10,
    })
    payload = rpc.order_query_payloads[-1]
    assert payload["timeType"] == "CreationTime"
    assert payload["beginTime"] == "2026-01-01T00:00:00.000Z"
    assert payload["endTime"] == "2026-12-31T23:59:59.999Z"


def test_query_order_ids_defaults_pagecount_when_missing() -> None:
    """payload 未给 pageCount（order-list 必填）→ 用默认批大小 50。"""
    station = _bare_station()
    rpc = _FakeRPCForGantt([{"id": "A"}])
    station.hardware_interface = rpc

    station._query_order_ids_for_gantt({"status": "60"})
    assert rpc.order_query_payloads[-1]["pageCount"] == 50


def test_query_order_ids_honors_skipcount_start() -> None:
    """skipCount 作为翻页起点。"""
    station = _bare_station()
    items = [{"id": f"OID-{i}"} for i in range(8)]
    rpc = _FakeRPCForGantt(items)
    station.hardware_interface = rpc

    order_ids = station._query_order_ids_for_gantt(
        {"pageCount": 5, "skipCount": 5}
    )
    # 从第 6 条起：OID-5..OID-7
    assert order_ids == ["OID-5", "OID-6", "OID-7"]
    assert rpc.order_query_payloads[0]["skipCount"] == 5


def test_query_order_ids_dedups_across_pages() -> None:
    """跨页重复 id 去重。"""
    station = _bare_station()
    # 故意制造重复 id（第 0 条和第 2 条同 id）
    items = [{"id": "DUP"}, {"id": "X"}, {"id": "DUP"}, {"id": "Y"}]
    rpc = _FakeRPCForGantt(items)
    station.hardware_interface = rpc

    order_ids = station._query_order_ids_for_gantt({"pageCount": 2})
    assert order_ids == ["DUP", "X", "Y"]


def test_query_order_ids_stops_on_empty_page_even_if_total_lies() -> None:
    """脏 totalCount（谎报很大）但后续页为空 → 遇空页即停，不死循环。"""
    station = _bare_station()
    rpc = _FakeRPCDirtyTotal([{"id": "A"}, {"id": "B"}], fake_total=10_000)
    station.hardware_interface = rpc

    order_ids = station._query_order_ids_for_gantt({"pageCount": 2})
    assert order_ids == ["A", "B"]
    # 第 1 页有数据 → 翻第 2 页空 → 停。总共 2 次调用，远小于 max_pages
    assert rpc.calls == 2


def test_query_order_ids_returns_empty_when_no_orders() -> None:
    station = _bare_station()
    rpc = _FakeRPCForGantt([])
    station.hardware_interface = rpc

    assert station._query_order_ids_for_gantt({"pageCount": 10}) == []


def test_query_order_ids_skips_items_without_id() -> None:
    station = _bare_station()
    rpc = _FakeRPCForGantt([{"id": "A"}, {"id": ""}, {"name": "no-id"}, {"id": "B"}])
    station.hardware_interface = rpc

    assert station._query_order_ids_for_gantt({"pageCount": 10}) == ["A", "B"]


# ---------------------------------------------------------------------------
# 2. _gantt_report_worker：去内层 data + 只 POST 一次
# ---------------------------------------------------------------------------


def _inject_fake_http_client(monkeypatch: Any) -> Dict[str, Any]:
    """注入假的 ``unilabos.app.web.http_client``，捕获 report_gantt 入参。返回捕获 dict。"""
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


def test_worker_unwraps_inner_data_and_posts_once(monkeypatch: Any) -> None:
    """每个订单甘特只取响应里的 data（{"items":[...]}），汇总成数组只 POST 一次。"""
    station = _bare_station()
    items = [{"id": "OID-1"}, {"id": "OID-2"}]
    rpc = _FakeRPCForGantt(items, gantt_map={
        "OID-1": {"code": 1, "data": {"items": ["a1"]}, "message": ""},
        "OID-2": {"code": 1, "data": {"items": ["b1"]}, "message": ""},
    })
    station.hardware_interface = rpc
    captured = _inject_fake_http_client(monkeypatch)

    station._gantt_report_worker("u1", {"status": "", "pageCount": 10})

    assert captured["calls"] == 1, "必须只 POST 一次"
    assert captured["uuid"] == "u1"
    # 去掉内层 data 一层：每个元素是 {"items": [...]}，不再是 {"data": {"items": [...]}}
    assert captured["data"] == [{"items": ["a1"]}, {"items": ["b1"]}]
    assert rpc.gantt_calls == ["OID-1", "OID-2"]


def test_worker_skips_post_when_no_orders(monkeypatch: Any) -> None:
    station = _bare_station()
    station.hardware_interface = _FakeRPCForGantt([])
    captured = _inject_fake_http_client(monkeypatch)

    station._gantt_report_worker("u1", {"pageCount": 10})

    assert captured["calls"] == 0, "查不到订单时不应 POST"


def test_worker_skips_order_when_gantt_data_missing(monkeypatch: Any) -> None:
    """某订单甘特响应缺 data 字段 → 跳过该条，其余正常上报。"""
    station = _bare_station()
    items = [{"id": "OID-1"}, {"id": "OID-2"}]
    rpc = _FakeRPCForGantt(items, gantt_map={
        "OID-1": {"code": 1, "message": "no data here"},  # 缺 data
        "OID-2": {"code": 1, "data": {"items": ["b1"]}},
    })
    station.hardware_interface = rpc
    captured = _inject_fake_http_client(monkeypatch)

    station._gantt_report_worker("u1", {"pageCount": 10})

    assert captured["calls"] == 1
    assert captured["data"] == [{"items": ["b1"]}]
