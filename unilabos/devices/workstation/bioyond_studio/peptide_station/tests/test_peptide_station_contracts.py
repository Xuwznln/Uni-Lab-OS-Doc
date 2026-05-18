"""多肽站 AST/参数/结果表 离线契约测试。"""

from __future__ import annotations

import importlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[6]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODULE_PATH = "unilabos.devices.workstation.bioyond_studio.peptide_station.peptide_station"
CLASS_NAME = "BioyondPeptideStation"

ORDER_GUID = "3a20eabe-bad5-ef95-49bd-7ffbd5df189d"
CREATE_ALLOCATION = {
    ORDER_GUID: [
        {
            "materialId": "mat-tip",
            "materialName": "200μL枪头盒",
            "materialCode": "0008-00105",
            "quantity": "1个",
            "materialTypeMode": "Consumables",
            "locationCode": "1-01",
            "locationShowName": "1-01",
        },
        {
            "materialId": "mat-plate",
            "materialName": "96孔板",
            "materialCode": "PLATE-96",
            "quantity": "1",
            "materialTypeMode": "Sample",
            "locationCode": "A1",
            "locationShowName": "A1-show",
        },
        {
            "materialId": "mat-extra",
            "materialName": "未知耗材",
            "materialCode": "X-1",
            "quantity": "2",
            "materialTypeMode": "Future",
            "locationCode": "Z9",
            "locationShowName": "",
        },
    ]
}

FLATTENED_LIVE = [
    {"step": "39c78d4b-b5d3-f721-2001-9d52000084c3", "step_name": "S1", "Key": "SampleFile", "m": 0, "n": 0, "Value": "", "DisplayValue": "", "TaskDisplayable": 1},
    {"step": "39c78d4b-b5d3-f721-2001-9d52000084c3", "step_name": "S1", "Key": "Example", "m": 0, "n": 0, "Value": "x", "DisplayValue": "x", "TaskDisplayable": 1},
    {"step": "39c78d4b-b5d3-f721-2001-9d52000084c4", "step_name": "S2", "Key": "protocol", "m": 14, "n": 28, "Value": "", "DisplayValue": "", "TaskDisplayable": 1},
    {"step": "39c78d4b-b5d3-f721-2001-9d52000084c5", "step_name": "S3", "Key": "CEMMethodFileName", "m": 0, "n": 0, "Value": "", "DisplayValue": "", "TaskDisplayable": 1},
]


def _import_module() -> Any:
    return importlib.import_module(MODULE_PATH)


def _make_station() -> Any:
    module = _import_module()
    cls = getattr(module, CLASS_NAME)
    station = object.__new__(cls)
    station.bioyond_config = {"api_host": "http://test", "api_key": "k", "warehouse_mapping": {}}
    rpc = MagicMock()
    rpc.host = "http://test"
    rpc.api_key = "k"
    rpc.material_info.return_value = {"locations": [{"whName": "自动化堆栈", "code": "1-01"}]}
    station.hardware_interface = rpc
    return station


# ---------------------------------------------------------------------------
# 1. AST/导入面
# ---------------------------------------------------------------------------


def test_required_actions_exposed() -> None:
    cls = getattr(_import_module(), CLASS_NAME)
    required = {
        "upload_sample_excel",
        "list_sample_excels",
        "get_step_parameters",
        "submit_experiment",
        "submit_experiment_day1",
        "submit_experiment_day2",
        "submit_experiment_day3",
        "submit_experiment_day4",
        "submit_experiment_day4_LCMS",
        "start_experiment",
        "reset",
        "scheduler_start",
        "scheduler_stop",
        "scheduler_pause",
        "scheduler_continue",
        "get_order_list",
        "get_order_report",
        "get_aggregated_order_report",
    }
    have = {name for name, _ in inspect.getmembers(cls, inspect.isfunction)}
    missing = sorted(required - have)
    assert not missing, f"缺少动作: {missing}"


def test_manual_confirm_node_types() -> None:
    module = _import_module()
    cls = getattr(module, CLASS_NAME)
    manual = {"submit_experiment_day1", "start_experiment"}
    normal = {
        "submit_experiment",
        "submit_experiment_day2",
        "submit_experiment_day3",
        "submit_experiment_day4",
        "submit_experiment_day4_LCMS",
        "reset",
        "scheduler_start",
        "list_sample_excels",
        "get_step_parameters",
        "get_order_list",
        "get_order_report",
    }
    for name in manual:
        meta = getattr(getattr(cls, name), "_action_registry_meta", {})
        assert meta.get("node_type") == module.NodeType.MANUAL_CONFIRM, name
    for name in normal:
        meta = getattr(getattr(cls, name), "_action_registry_meta", {})
        assert meta.get("node_type") != module.NodeType.MANUAL_CONFIRM, name


def test_submit_and_reset_signatures_exclude_legacy_manual_confirm() -> None:
    cls = getattr(_import_module(), CLASS_NAME)
    for name in (
        "submit_experiment",
        "submit_experiment_day2",
        "submit_experiment_day3",
        "submit_experiment_day4",
        "submit_experiment_day4_LCMS",
        "reset",
    ):
        params = inspect.signature(getattr(cls, name)).parameters
        assert "timeout_seconds" not in params, name
        assert "assignee_user_ids" not in params, name


def test_day1_submit_accepts_manual_confirm_kwargs() -> None:
    """plan: Day1 是 MANUAL_CONFIRM；框架会注入 timeout_seconds/assignee_user_ids，函数必须能接收。"""
    cls = getattr(_import_module(), CLASS_NAME)
    sig = inspect.signature(cls.submit_experiment_day1)
    has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    assert has_kwargs, "submit_experiment_day1 必须有 **kwargs 以容纳人工确认框架字段"


def test_typed_dicts_present() -> None:
    module = _import_module()
    for cls_name in (
        "PeptideGenericSubmitRequiredParams",
        "PeptideGenericSubmitOptionalParams",
        "PeptideDay1RequiredParams",
        "PeptideDay1OptionalParams",
        "PeptideDay2RequiredParams",
        "PeptideDay2OptionalParams",
        "PeptideDay3RequiredParams",
        "PeptideDay3OptionalParams",
        "PeptideDay4RequiredParams",
        "PeptideDay4OptionalParams",
        "PeptideDay4LCMSRequiredParams",
        "PeptideDay4LCMSOptionalParams",
    ):
        assert hasattr(module, cls_name), cls_name


def test_workflow_constants_split() -> None:
    module = _import_module()
    assert module.DAY4_PEPTIDE_WORKFLOW_NAME == "Day4环肽酰化-酶标"
    assert module.DAY4_LCMS_PEPTIDE_WORKFLOW_NAME == "Day4环肽酰化-酶标+LCMS"
    assert module.DAY_WORKFLOW_BINDINGS["day4_lcms"]["sub_name"] == "Day4环肽酰化-酶标LCMS"
    assert module.DAY1_CEM_METHOD_DEFAULT == "5microdouble-20250911.MPM"


# ---------------------------------------------------------------------------
# 2. Sample Excel
# ---------------------------------------------------------------------------


def test_list_sample_excels_modes() -> None:
    station = _make_station()
    records = [
        {"fileName": "DPR019-a.xlsx", "relativePath": "upload\\sample\\DPR019-a.xlsx"},
        {"fileName": "DPR019-b.xlsx", "relativePath": "upload\\sample\\DPR019-b.xlsx"},
    ]
    station._list_sample_excels = MagicMock(return_value=records)  # type: ignore[method-assign]

    info = station.list_sample_excels(sample_excel_pattern="DPR019-a", deterministic_resolve=False)
    assert "sample_excel_data" in info
    assert "sample_excel_relative_path" not in info

    resolved = station.list_sample_excels(sample_excel_pattern="DPR019-a", deterministic_resolve=True)
    assert resolved["sample_excel_relative_path"] == "upload\\sample\\DPR019-a.xlsx"

    with pytest.raises(Exception):
        station.list_sample_excels(sample_excel_pattern="DPR019", deterministic_resolve=True)


def test_resolve_submit_sample_file_direct_path() -> None:
    station = _make_station()
    relative, selected = station._resolve_submit_sample_file({}, {}, "upload/sample/x.xlsx")
    assert relative == "upload\\sample\\x.xlsx"
    assert selected["fileName"] == "x.xlsx"


def test_filename_matches_pattern_substring_and_glob() -> None:
    station = _make_station()
    assert station._filename_matches_pattern("DPR019-20260421-thrombin-5.xlsx", "DPR019")
    assert station._filename_matches_pattern("a.xlsx", "*.xlsx")
    assert not station._filename_matches_pattern("a.xlsx", "*.docx")
    assert station._filename_matches_pattern("a.xlsx", "")


# ---------------------------------------------------------------------------
# 3. Step parameter helper
# ---------------------------------------------------------------------------


def test_filter_step_parameters_preserves_zero_and_skips_unknown() -> None:
    station = _make_station()
    records = [
        {"TaskDisplayable": 1, "Value": 0, "DisplayValue": ""},
        {"TaskDisplayable": 1, "Value": "", "DisplayValue": ""},
        {"TaskDisplayable": 0, "Value": "", "DisplayValue": ""},
        {"TaskDisplayable": None, "Value": "", "DisplayValue": ""},
    ]
    filtered = station._filter_step_parameter_records(records, True, True, True)
    assert {(r.get("Value"), r.get("TaskDisplayable")) for r in filtered} == {(0, 1), ("", 1), ("", 0)}


def test_get_step_parameters_zero_match_returns_status() -> None:
    station = _make_station()
    station._query_workflow_records = MagicMock(return_value=[])  # type: ignore[method-assign]
    out = station.get_step_parameters(workflow_name_filter="不存在")
    status = out["step_parameters_raw_json"]
    assert status.get("code") == -1
    assert out["filtered_subworkflows"] == []


def test_get_step_parameters_multi_match_returns_status() -> None:
    station = _make_station()
    station._query_workflow_records = MagicMock(return_value=[  # type: ignore[method-assign]
        {"workflowId": "w1", "workflowName": "A", "subworkflowId": "s1", "subworkflowName": "A1"},
        {"workflowId": "w1", "workflowName": "A", "subworkflowId": "s2", "subworkflowName": "A2"},
    ])
    out = station.get_step_parameters(workflow_name_filter="A")
    assert out["step_parameters_raw_json"].get("code") == 0
    assert len(out["filtered_subworkflows"]) == 2


def test_get_step_parameters_direct_sub_workflow_id() -> None:
    station = _make_station()
    station._query_step_parameters = MagicMock(return_value={  # type: ignore[method-assign]
        "39c78d4b-b5d3-f721-2001-9d52000084c3": [
            {"name": "S1", "m": 0, "n": 0, "parameterList": [
                {"Key": "SampleFile", "TaskDisplayable": 1, "Value": "", "DisplayValue": ""},
            ]},
        ]
    })
    out = station.get_step_parameters(sub_workflow_id="39c78d4b-b5d3-f721-2001-9d52000084c3")
    augmented = out["step_parameters_raw_json"]
    assert augmented["code"] == 1
    assert any(p["Key"] == "SampleFile" for p in augmented["data"]["filteredParameters"])


# ---------------------------------------------------------------------------
# 4. Partial parameter entries + live resolution
# ---------------------------------------------------------------------------


def test_partial_entries_inject_samplefile_and_overrides() -> None:
    station = _make_station()
    entries, warnings = station._build_partial_parameter_entries(
        sample_excel_relative_path="upload\\sample\\f.xlsx",
        day_key="day2",
        parameter_overrides=[{"Key": "Example", "Value": 0}],
    )
    assert entries[0] == {"Key": "SampleFile", "Value": "upload\\sample\\f.xlsx"}
    assert any(e["Key"] == "Example" and e["Value"] == 0 for e in entries)
    assert warnings == []


def test_day1_partial_entries_inject_cem_default() -> None:
    station = _make_station()
    entries, _ = station._build_partial_parameter_entries(
        sample_excel_relative_path="upload\\sample\\f.xlsx",
        day_key="day1",
        extra_autofill=[{"Key": "CEMMethodFileName", "Value": "5microdouble-20250911.MPM"}],
    )
    assert any(e["Key"] == "CEMMethodFileName" and e["Value"] == "5microdouble-20250911.MPM" for e in entries)


def test_overrides_duplicate_last_write_wins_warning() -> None:
    station = _make_station()
    entries, warnings = station._build_partial_parameter_entries(
        sample_excel_relative_path="x",
        day_key="day2",
        parameter_overrides=[
            {"Key": "Example", "m": 0, "n": 0, "Value": "first"},
            {"Key": "Example", "m": 0, "n": 0, "Value": "second"},
        ],
    )
    example_entries = [e for e in entries if e["Key"] == "Example"]
    assert len(example_entries) == 1
    assert example_entries[0]["Value"] == "second"
    assert any("重复" in w for w in warnings)


def test_resolve_against_live_unique_match_and_failure() -> None:
    station = _make_station()
    resolved = station._resolve_parameter_entries_against_live_steps(
        [{"Key": "SampleFile", "Value": "upload\\sample\\f.xlsx"}], FLATTENED_LIVE
    )
    assert resolved[0]["step"] == "39c78d4b-b5d3-f721-2001-9d52000084c3"
    assert resolved[0]["m"] == 0 and resolved[0]["n"] == 0
    # 没有 protocol 在 m/n=0/0 处 → 0 匹配
    with pytest.raises(Exception) as exc:
        station._resolve_parameter_entries_against_live_steps(
            [{"Key": "protocol", "m": 0, "n": 0, "Value": "v"}], FLATTENED_LIVE
        )
    assert "0 条" in str(exc.value)


def test_group_resolved_entries_uses_lowercase_keys() -> None:
    station = _make_station()
    grouped = station._group_resolved_entries_to_param_values([
        {"step": "39c78d4b-b5d3-f721-2001-9d52000084c3", "Key": "SampleFile", "m": 0, "n": 0, "Value": "x"},
    ])
    step_entries = grouped["39c78d4b-b5d3-f721-2001-9d52000084c3"]
    assert step_entries[0] == {"key": "SampleFile", "value": "x", "m": 0, "n": 0}


def test_create_order_payload_shape() -> None:
    station = _make_station()
    payload = station._create_order_payload(
        order_code="EXP260518-103000",
        order_name="实验260518-103000",
        sub_workflow_id="3a1d35f9-63ce-67d6-1784-9f6abcca4eda",
        param_values={"39c78d4b-b5d3-f721-2001-9d52000084c3": [{"key": "SampleFile", "value": "x", "m": 0, "n": 0}]},
        border_number=1,
        extend_properties=None,
    )
    assert isinstance(payload, list) and len(payload) == 1
    item = payload[0]
    assert item["workFlowId"] == "3a1d35f9-63ce-67d6-1784-9f6abcca4eda"
    assert item["paramValues"]
    assert item["extendProperties"] == ""
    assert item["borderNumber"] == 1


def test_order_identity_format() -> None:
    station = _make_station()
    code, name = station._build_order_identity("day2")
    assert code.startswith("EXP") and len(code) == 16  # EXP + YYMMDD-HHmmss
    assert name.startswith("实验")
    code2, name2 = station._build_order_identity("day2", "自定义")
    assert name2 == "自定义"


# ---------------------------------------------------------------------------
# 5. Generic submit / day wrappers (含会抦住 BUG 1 的用例)
# ---------------------------------------------------------------------------


def _wire_submit_pipeline(station: Any) -> None:
    station._resolve_workflow_binding_from_names = MagicMock(return_value={  # type: ignore[method-assign]
        "workflow_name": "DAY2多肽定量",
        "root_workflow_id": "3a1d35f0-9436-895b-2eda-039a5465275e",
        "sub_workflow_id": "3a1d35f0-9f7e-c2c1-0bc0-8d94b81d90ca",
        "sub_workflow_name": "DAY2多肽定量",
        "raw": {},
    })
    station._resolve_workflow_binding = MagicMock(side_effect=lambda day_key: station._resolve_workflow_binding_from_names("DAY2多肽定量"))  # type: ignore[method-assign]
    station._query_step_parameters = MagicMock(return_value={})  # type: ignore[method-assign]
    station._flatten_step_parameters = MagicMock(return_value=FLATTENED_LIVE)  # type: ignore[method-assign]
    station._create_order = MagicMock(return_value=json.dumps(CREATE_ALLOCATION))  # type: ignore[method-assign]


def test_submit_experiment_generic_succeeds() -> None:
    """plan §「Generic And Day 1 Submit」line 919-924；这条同时抦住 BUG 1（binding= 关键字）。"""
    station = _make_station()
    _wire_submit_pipeline(station)
    result = station.submit_experiment(
        {"workflow_name": "DAY2多肽定量", "sample_excel_pattern": ""},
        {"parameter_overrides": []},
        sample_excel_relative_path="upload/sample/f.xlsx",
    )
    assert result["success"] is True
    assert result["order_id"] == ORDER_GUID
    assert result["resultTable"]["tableName"] == "resultTable"


def test_submit_experiment_rejects_day1_alias() -> None:
    station = _make_station()
    with pytest.raises(Exception):
        station.submit_experiment(
            {"workflow_name": "Day1线肽合成", "sample_excel_pattern": "x"},
            {},
            sample_excel_relative_path="upload/sample/f.xlsx",
        )


def test_submit_experiment_day2_calls_pipeline() -> None:
    station = _make_station()
    _wire_submit_pipeline(station)
    result = station.submit_experiment_day2(
        {"sample_excel_pattern": ""},
        {"parameter_overrides": []},
        sample_excel_relative_path="upload/sample/f.xlsx",
    )
    assert result["success"] is True
    assert result["order_ids"] == [ORDER_GUID]
    assert result["auto_register_materials"] is True
    assert result["material_registration"]["status"] == "not_implemented"


def test_day1_placeholder_does_not_call_create_order() -> None:
    station = _make_station()
    station._resolve_workflow_binding = MagicMock(return_value={  # type: ignore[method-assign]
        "workflow_name": "Day1线肽合成",
        "root_workflow_id": "rid",
        "sub_workflow_id": "sid",
        "sub_workflow_name": "Day1线肽合成",
        "raw": {},
    })
    station._create_order = MagicMock(side_effect=AssertionError("Day1 不应触达 create_order"))  # type: ignore[method-assign]
    out = station.submit_experiment_day1(
        {"sample_excel_pattern": "", "cem_method_file_name": ""},
        {},
        sample_excel_relative_path="upload/sample/f.xlsx",
        # 模拟人工确认框架注入的字段（这条会抦住 BUG 3）
        timeout_seconds=3600,
        assignee_user_ids=[],
        materials_loaded=False,
    )
    assert out["status"] == "manual_confirm_placeholder"
    assert out["cem_method_file_name"] == "5microdouble-20250911.MPM"
    assert isinstance(out["partial_parameter_entries"], list)


# ---------------------------------------------------------------------------
# 6. Allocation map parsing + resultTable
# ---------------------------------------------------------------------------


def test_parse_allocation_map_extracts_order_id_and_groups() -> None:
    station = _make_station()
    parsed = station._parse_create_order_allocation_map(json.dumps(CREATE_ALLOCATION))
    assert parsed["order_ids"] == [ORDER_GUID]
    assert len(parsed["allocation_rows"]) == 3
    assert set(parsed["materials_by_type"].keys()) == {"Consumables", "Sample", "Future"}


def test_parse_allocation_map_handles_python_str_repr() -> None:
    """RPC.create_order 返回的是 str(dict)，含单引号。"""
    station = _make_station()
    parsed = station._parse_create_order_allocation_map(str(CREATE_ALLOCATION))
    assert parsed["order_ids"] == [ORDER_GUID]


def test_parse_allocation_map_empty() -> None:
    station = _make_station()
    parsed = station._parse_create_order_allocation_map("{}")
    assert parsed["allocation_rows"] == []
    assert parsed["order_ids"] == []


def test_build_result_table_order_and_columns() -> None:
    station = _make_station()
    parsed = station._parse_create_order_allocation_map(json.dumps(CREATE_ALLOCATION))
    table = station._build_result_table(parsed["materials_by_type"])
    assert table["tableName"] == "resultTable"
    assert [c["key"] for c in table["columns"]] == ["whName", "locationCode", "materialName", "quantity"]
    # 顺序：Sample → Consumables → Future（未知 mode 保留在末尾）
    names = [row["materialName"] for row in table["data"]]
    assert names == ["96孔板", "200μL枪头盒", "未知耗材"]
    # locationShowName 优先 locationCode
    assert table["data"][0]["locationCode"] == "A1-show"
    assert table["data"][1]["locationCode"] == "1-01"


def test_build_result_table_empty_returns_empty_data() -> None:
    station = _make_station()
    table = station._build_result_table({})
    assert table["data"] == []
    assert [c["key"] for c in table["columns"]] == ["whName", "locationCode", "materialName", "quantity"]


def test_resolve_wh_name_handles_material_info_failure() -> None:
    station = _make_station()
    station.hardware_interface.material_info.side_effect = RuntimeError("HTTP 500")
    cache: Dict[str, Dict[str, Any]] = {}
    assert station._resolve_wh_name_by_material_id("mat-1", cache) == ""


def test_submit_returns_warning_when_allocation_empty() -> None:
    station = _make_station()
    _wire_submit_pipeline(station)
    station._create_order = MagicMock(return_value="{}")  # type: ignore[method-assign]
    result = station.submit_experiment_day2(
        {"sample_excel_pattern": ""},
        {},
        sample_excel_relative_path="upload/sample/f.xlsx",
    )
    assert "create_order_allocation_unavailable_for_result_table" in result["warnings"]


# ---------------------------------------------------------------------------
# 7. Reports + workflow records
# ---------------------------------------------------------------------------


def test_get_order_list_passes_json_string() -> None:
    station = _make_station()
    station.hardware_interface.order_query.return_value = {"items": [], "totalCount": 0}
    station.get_order_list(filter_text="abc", page_count=10)
    args, kwargs = station.hardware_interface.order_query.call_args
    payload = json.loads(args[0])
    assert payload["filter"] == "abc"
    assert payload["pageCount"] == 10


def test_get_order_report_calls_typed_rpc() -> None:
    station = _make_station()
    station.hardware_interface.order_report.return_value = {"id": ORDER_GUID, "name": "x", "preIntakes": [], "resultList": []}
    out = station.get_order_report(ORDER_GUID)
    station.hardware_interface.order_report.assert_called_once_with(ORDER_GUID)
    assert out["success"] is True
    assert out["summary"]["id"] == ORDER_GUID


def test_get_aggregated_order_report_is_todo_placeholder() -> None:
    station = _make_station()
    out = station.get_aggregated_order_report(ORDER_GUID)
    assert out["status"] == "not_implemented"


def test_query_workflow_records_filters_unsaved_subworkflows() -> None:
    station = _make_station()
    station.hardware_interface.query_workflow.return_value = {
        "items": [
            {
                "id": "rid",
                "name": "Day3线肽环化",
                "subWorkflows": [
                    {"id": "saved-id", "name": "Day3线肽环化", "isSaved": True},
                    {"id": "draft-id", "name": "Day3线肽环化-草稿", "isSaved": False},
                ],
            }
        ]
    }
    records = station._query_workflow_records("Day3线肽环化")
    assert [r["subworkflowId"] for r in records] == ["saved-id"]


# ---------------------------------------------------------------------------
# 8. Debug / fetch_workflow_list 守护
# ---------------------------------------------------------------------------


def test_module_fetch_workflow_list_is_debug_guarded() -> None:
    module = _import_module()
    assert module.DEBUG_CLI_ENABLED is False
    with pytest.raises(AssertionError):
        module.fetch_workflow_list(config={"api_host": "http://x", "api_key": "k"})


def test_station_fetch_workflow_list_uses_rpc() -> None:
    station = _make_station()
    station.hardware_interface.query_workflow.return_value = {"items": [], "totalCount": 0}
    station.fetch_workflow_list(filter_text="Day2")
    args, _ = station.hardware_interface.query_workflow.call_args
    payload = json.loads(args[0])
    assert payload["filter"] == "Day2"
    assert payload["includeDetail"] is True


# ---------------------------------------------------------------------------
# 9. start_experiment 装载闸门
# ---------------------------------------------------------------------------


def test_start_experiment_blocks_when_materials_not_loaded() -> None:
    station = _make_station()
    station.hardware_interface.scheduler_start.return_value = 1
    with pytest.raises(RuntimeError):
        station.start_experiment(
            order_id=ORDER_GUID,
            resultTable={"data": [{"materialName": "x"}]},
            materials_loaded=False,
        )


def test_start_experiment_starts_when_table_empty() -> None:
    station = _make_station()
    station.hardware_interface.scheduler_start.return_value = 1
    result = station.start_experiment(order_id=ORDER_GUID, resultTable={"data": []})
    assert result["success"] is True
    assert result["order_ids"] == [ORDER_GUID]


# ---------------------------------------------------------------------------
# 10. Reset
# ---------------------------------------------------------------------------


def test_reset_signature_drops_legacy_params_and_uses_literal() -> None:
    """plan 调整：删除 dry_run/order_id/location_id；reset_operations 用 Literal 注解。"""
    cls = getattr(_import_module(), CLASS_NAME)
    sig = inspect.signature(cls.reset)
    params = sig.parameters
    for legacy in ("dry_run", "order_id", "location_id"):
        assert legacy not in params, f"reset 不应再有 {legacy} 入参"
    assert "reset_operations" in params
    assert any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()), \
        "reset 必须保留 **kwargs 以兜底 reset_order_id/reset_location_id"

    annotation = params["reset_operations"].annotation
    rendered = annotation if isinstance(annotation, str) else repr(annotation)
    for op in ("scheduler_reset", "reset_order_status", "reset_location"):
        assert op in rendered, f"reset_operations 的 Literal 必须包含 {op}"


def test_reset_goal_default_contains_all_operations() -> None:
    """像 sirna 一样，goal_default 默认勾选全部三个 reset 操作。"""
    cls = getattr(_import_module(), CLASS_NAME)
    meta = getattr(cls.reset, "_action_registry_meta", {})
    goal_default = meta.get("goal_default") or {}
    assert goal_default.get("reset_operations") == [
        "scheduler_reset",
        "reset_order_status",
        "reset_location",
    ]


def test_reset_executes_typed_rpc_calls() -> None:
    station = _make_station()
    station.hardware_interface.scheduler_reset.return_value = 1
    station.hardware_interface.reset_order_status.return_value = 1
    station.hardware_interface.reset_location.return_value = 1
    out = station.reset(
        reset_operations=["scheduler_reset", "reset_order_status", "reset_location"],
        reset_order_id=ORDER_GUID,
        reset_location_id="loc-1",
    )
    station.hardware_interface.scheduler_reset.assert_called_once_with()
    station.hardware_interface.reset_order_status.assert_called_once_with(ORDER_GUID)
    station.hardware_interface.reset_location.assert_called_once_with("loc-1")
    assert out["selected_operations"] == [
        "scheduler_reset",
        "reset_order_status",
        "reset_location",
    ]
    assert len(out["executed_calls"]) == 3
    assert out["skipped_operations"] == []


def test_reset_skips_when_ids_missing() -> None:
    """没有 order_id / location_id 时应该 skip 而不是抛错。"""
    station = _make_station()
    station.hardware_interface.scheduler_reset.return_value = 1
    out = station.reset(
        reset_operations=["scheduler_reset", "reset_order_status", "reset_location"],
    )
    station.hardware_interface.scheduler_reset.assert_called_once_with()
    station.hardware_interface.reset_order_status.assert_not_called()
    station.hardware_interface.reset_location.assert_not_called()
    skipped_ops = {item["operation"] for item in out["skipped_operations"]}
    assert skipped_ops == {"reset_order_status", "reset_location"}
