"""Bioyond 多肽工作站：LIMS 提交/复位/调度与样品 Excel 工作流。"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import mimetypes
import sys
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Dict, Iterable, List, Literal, Optional, Tuple
from uuid import UUID

import requests

try:
    from typing_extensions import TypedDict
except ImportError:  # pragma: no cover
    from typing import TypedDict  # type: ignore

try:
    from pydantic import Field
except Exception:  # pragma: no cover
    def Field(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return kwargs

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[5]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

from unilabos.utils.log import logger
from unilabos.resources.bioyond.peptide_materials import DEFAULT_PEPTIDE_MATERIAL_TYPE_MAPPINGS

try:
    from unilabos.registry.decorators import (
        ActionInputHandle,
        ActionOutputHandle,
        DataSource,
        NodeType,
        action,
        device,
    )
    _REGISTRY_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover
    _REGISTRY_IMPORT_ERROR = exc

    class NodeType:  # type: ignore[no-redef]
        MANUAL_CONFIRM = "manual_confirm"

    class DataSource:  # type: ignore[no-redef]
        HANDLE = "handle"
        EXECUTOR = "executor"

    class _FallbackActionHandle:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    class ActionInputHandle(_FallbackActionHandle):  # type: ignore[no-redef]
        pass

    class ActionOutputHandle(_FallbackActionHandle):  # type: ignore[no-redef]
        pass

    def device(*args: Any, **kwargs: Any):
        def decorator(cls):
            return cls

        return decorator

    def action(*args: Any, **kwargs: Any):
        if len(args) == 1 and callable(args[0]):
            func = args[0]
            func._action_registry_meta = {}  # type: ignore[attr-defined]
            return func

        def decorator(func):
            func._action_registry_meta = dict(kwargs)  # type: ignore[attr-defined]
            return func

        return decorator

try:
    from unilabos.devices.workstation.workstation_base import WorkstationBase
    from unilabos.devices.workstation.bioyond_studio.station import BioyondWorkstation
    _BIOYOND_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover
    WorkstationBase = object  # type: ignore[assignment,misc]
    BioyondWorkstation = object  # type: ignore[assignment,misc]
    _BIOYOND_IMPORT_ERROR = exc


DEBUG_CLI_ENABLED = False
DEFAULT_RESET_OPERATIONS = ("scheduler_reset", "reset_order_status", "reset_location")
RESULT_TABLE_COLUMNS = [
    {"name": "设备", "key": "whName"},
    {"name": "位置", "key": "locationCode"},
    {"name": "物料名称", "key": "materialName"},
    {"name": "数量", "key": "quantity"},
]
MATERIAL_TYPE_ORDER = ("Sample", "Consumables", "Reagent")
PEPTIDE_SAMPLE_FILE_KEY = "SampleFile"
DAY1_CEM_METHOD_KEY = "CEMMethodFileName"
DAY1_CEM_METHOD_DEFAULT = "5microdouble-20250911.MPM"

# 绑定信息（最后更新 2026-05-16）
DAY1_PEPTIDE_WORKFLOW_NAME = "Day1线肽合成"
DAY2_PEPTIDE_WORKFLOW_NAME = "DAY2多肽定量"
DAY3_PEPTIDE_WORKFLOW_NAME = "Day3线肽环化"
DAY4_PEPTIDE_WORKFLOW_NAME = "Day4环肽酰化-酶标"
DAY4_LCMS_PEPTIDE_WORKFLOW_NAME = "Day4环肽酰化-酶标+LCMS"
DAY4_LCMS_SUB_WORKFLOW_NAME = "Day4环肽酰化-酶标LCMS"

DAY_WORKFLOW_BINDINGS: Dict[str, Dict[str, str]] = {
    "day1": {"root_name": DAY1_PEPTIDE_WORKFLOW_NAME, "sub_name": DAY1_PEPTIDE_WORKFLOW_NAME},
    "day2": {"root_name": DAY2_PEPTIDE_WORKFLOW_NAME, "sub_name": DAY2_PEPTIDE_WORKFLOW_NAME},
    "day3": {"root_name": DAY3_PEPTIDE_WORKFLOW_NAME, "sub_name": DAY3_PEPTIDE_WORKFLOW_NAME},
    "day4": {"root_name": DAY4_PEPTIDE_WORKFLOW_NAME, "sub_name": DAY4_PEPTIDE_WORKFLOW_NAME},
    "day4_lcms": {"root_name": DAY4_LCMS_PEPTIDE_WORKFLOW_NAME, "sub_name": DAY4_LCMS_SUB_WORKFLOW_NAME},
}


class PeptideWorkflowError(RuntimeError):
    """多肽工作流可恢复错误。"""


class PeptideCommonSubmitOptionalParams(TypedDict, total=False):
    order_name: Annotated[str, Field(description="订单名称；为空时自动生成，用户可覆盖。")]
    auto_register_materials: Annotated[bool, Field(default=True, description="是否自动登记返回的物料信息；默认勾选。本轮仅回传开关，不修改资源树。")]
    parameter_overrides: Annotated[
        List[Dict[str, Any]],
        Field(
            default=[{"m": 0, "n": 0, "Key": "Example", "Value": "example value"}],
            description="参数覆盖列表：Key 和 Value 必填，m/n 可选；省略 m/n 时 Key 必须唯一匹配。",
        ),
    ]
    border_number: Annotated[int, Field(default=1, description="LIMS 创建订单 borderNumber，默认 1。")]
    extend_properties: Annotated[str, Field(description="LIMS extendProperties 字符串；默认不传或传空。")]


class PeptideGenericSubmitRequiredParams(TypedDict):
    workflow_name: Annotated[str, Field(description="Bioyond 根工作流名称；用于解析一个非 Day1 子工作流。")]
    sample_excel_pattern: Annotated[str, Field(description="样品 Excel 文件名匹配模式。若通过上游句柄提供 sample_excel_relative_path，可留空。")]


class PeptideGenericSubmitOptionalParams(PeptideCommonSubmitOptionalParams, total=False):
    subworkflow_name: Annotated[str, Field(description="Bioyond 子工作流名称过滤；为空时 workflow_name 下必须只有一个可用子工作流。")]


class PeptideDay1RequiredParams(TypedDict):
    sample_excel_pattern: Annotated[str, Field(description="样品 Excel 文件名匹配模式。若通过上游句柄提供 sample_excel_relative_path，可留空。")]
    cem_method_file_name: Annotated[
        str,
        Field(default=DAY1_CEM_METHOD_DEFAULT, description="Day1 CEM 方法文件名称，默认 5microdouble-20250911.MPM。"),
    ]


class PeptideDay1OptionalParams(PeptideCommonSubmitOptionalParams, total=False):
    pass


class PeptideDay2RequiredParams(TypedDict):
    sample_excel_pattern: Annotated[str, Field(description="样品 Excel 文件名匹配模式。若通过上游句柄提供 sample_excel_relative_path，可留空。")]


class PeptideDay2OptionalParams(PeptideCommonSubmitOptionalParams, total=False):
    pass


class PeptideDay3RequiredParams(PeptideDay2RequiredParams):
    pass


class PeptideDay3OptionalParams(PeptideCommonSubmitOptionalParams, total=False):
    pass


class PeptideDay4RequiredParams(PeptideDay2RequiredParams):
    pass


class PeptideDay4OptionalParams(PeptideCommonSubmitOptionalParams, total=False):
    pass


class PeptideDay4LCMSRequiredParams(PeptideDay2RequiredParams):
    pass


class PeptideDay4LCMSOptionalParams(PeptideCommonSubmitOptionalParams, total=False):
    pass


def _apply_default_peptide_material_type_mappings(config: Dict[str, Any]) -> None:
    configured = config.get("material_type_mappings")
    if not isinstance(configured, dict):
        configured = {}
    merged = dict(DEFAULT_PEPTIDE_MATERIAL_TYPE_MAPPINGS)
    merged.update(configured)
    config["material_type_mappings"] = merged


def _utc_now_iso8601_ms() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_peptide_config(config_path: str | Path) -> Dict[str, Any]:
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def fetch_workflow_list(
    config: Optional[Dict[str, Any]] = None,
    config_path: Optional[str | Path] = None,
    workflow_type: int = 0,
    filter_text: str = "",
    include_detail: bool = True,
) -> Dict[str, Any]:
    """调试专用：直接 HTTP 拉取工作流列表。运行时应使用 BioyondPeptideStation.fetch_workflow_list。"""
    assert DEBUG_CLI_ENABLED, "模块级 fetch_workflow_list 仅供调试；运行时请调用站点实例方法"
    resolved_config = dict(config or {})
    if config_path is not None:
        resolved_config.update(load_peptide_config(config_path))
    api_host = str(resolved_config.get("api_host", "")).rstrip("/")
    api_key = str(resolved_config.get("api_key", ""))
    timeout = int(resolved_config.get("timeout", 10))
    if not api_host or not api_key:
        raise ValueError("缺少 api_host/api_key 配置")
    url = f"{api_host}/api/lims/workflow/work-flow-list"
    payload = {
        "apiKey": api_key,
        "requestTime": _utc_now_iso8601_ms(),
        "data": {"type": workflow_type, "filter": filter_text, "includeDetail": include_detail},
    }
    result: Dict[str, Any] = {"url": url, "request_payload": payload}
    try:
        response = requests.post(url, json=payload, timeout=timeout, headers={"Content-Type": "application/json"})
        result["http_status"] = response.status_code
        try:
            result["response"] = response.json()
        except ValueError:
            result["response"] = {"raw_text": response.text}
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


@device(
    id="bioyond_peptide_station",
    category=["workstation", "bioyond", "bioyond_peptide_station"],
    description="Bioyond 多肽合成工作站",
    display_name="Bioyond Peptide Station",
    icon="preparation_station.webp",
)
class BioyondPeptideStation(BioyondWorkstation):
    """多肽 LIMS 工作站。"""

    _REQUIRED_CONFIG_KEYS = ("api_key", "api_host", "warehouse_mapping")

    def __init__(
        self,
        bioyond_config: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
        config_path: Optional[str | Path] = None,
        deck: Optional[Any] = None,
        protocol_type: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        if _BIOYOND_IMPORT_ERROR is not None:
            raise RuntimeError(f"BioyondPeptideStation 基类导入失败: {_BIOYOND_IMPORT_ERROR}") from _BIOYOND_IMPORT_ERROR
        kwargs.pop("children", None)
        merged_config: Dict[str, Any] = {}
        if config_path is not None:
            merged_config.update(load_peptide_config(config_path))
        if isinstance(config, (str, Path)):
            merged_config.update(load_peptide_config(config))
        elif config:
            merged_config.update(config)
        if bioyond_config:
            merged_config.update(bioyond_config)
        merged_config.update(kwargs)
        _apply_default_peptide_material_type_mappings(merged_config)
        missing = [k for k in self._REQUIRED_CONFIG_KEYS if not merged_config.get(k)]
        if missing:
            raise ValueError(f"BioyondPeptideStation 缺少必要配置: {', '.join(missing)}")
        self.protocol_type = protocol_type
        self.bioyond_config = merged_config
        super().__init__(bioyond_config=self.bioyond_config, deck=deck)
        logger.info("BioyondPeptideStation 初始化完成: %s", self.bioyond_config.get("api_host", ""))

    def _debug_call_session(self, action_name: str):
        parent_debug_session = getattr(super(), "_debug_call_session", None)
        if parent_debug_session is not None:
            return parent_debug_session(action_name)
        return nullcontext()

    def fetch_workflow_list(
        self,
        workflow_type: int = 0,
        filter_text: str = "",
        include_detail: bool = True,
    ) -> Dict[str, Any]:
        """运行时通过 RPC 拉取工作流列表。"""
        payload = {"type": workflow_type, "filter": filter_text, "includeDetail": include_detail}
        data = self._require_hardware_interface().query_workflow(json.dumps(payload, ensure_ascii=False))
        items = self._as_list(data.get("items") if isinstance(data, dict) else data)
        return {"items": items, "totalCount": data.get("totalCount") if isinstance(data, dict) else len(items), "raw": data}

    @action(auto_prefix=True, description="上传多肽样品 Excel 文件")
    def upload_sample_excel(self, file_path: str, content_type: Optional[str] = None) -> Dict[str, Any]:
        with self._debug_call_session("upload_sample_excel"):
            result = self._upload_sample_excel_file(file_path, content_type=content_type)
        file_info = result.get("lims_file_info") if isinstance(result.get("lims_file_info"), dict) else {}
        return {
            "success": True,
            "data": file_info,
            "relative_path": str(result.get("relative_path") or ""),
            "sample_file_parameter": str(result.get("sample_file_parameter") or ""),
            "upload_result": result,
        }

    @action(
        always_free=True,
        description="查询 LIMS 样品 Excel 列表，可选确定性解析",
        handles=[
            ActionOutputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel 相对路径",
                data_key="sample_excel_relative_path",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="sample_excel_data",
                data_type="json",
                label="样品 Excel 列表",
                data_key="sample_excel_data",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def list_sample_excels(
        self,
        begin_date: str = "",
        end_date: str = "",
        name_filter: str = "",
        sample_excel_pattern: str = "",
        deterministic_resolve: bool = False,
    ) -> Dict[str, Any]:
        with self._debug_call_session("list_sample_excels"):
            records = self._list_sample_excels(
                name_filter=name_filter or sample_excel_pattern.replace("*", ""),
                begin_date=begin_date or None,
                end_date=end_date or None,
            )
            payload: Dict[str, Any] = {"success": True, "sample_excel_data": records}
            if deterministic_resolve:
                pattern = sample_excel_pattern or name_filter
                if not pattern:
                    raise PeptideWorkflowError("确定性解析模式需要 sample_excel_pattern 或 name_filter")
                selected = self._select_sample_excel_record(records, pattern)
                payload["sample_excel_relative_path"] = str(selected.get("relativePath") or "").replace("/", "\\")
                payload["selected_sample_excel"] = selected
            return payload

    @action(
        always_free=True,
        description="查询子工作流步骤参数（支持必填/可选/隐藏过滤）",
        handles=[
            ActionOutputHandle(
                key="step_parameters_raw_json",
                data_type="json",
                label="步骤参数 JSON",
                data_key="step_parameters_raw_json",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="filtered_subworkflows",
                data_type="json",
                label="匹配子工作流",
                data_key="filtered_subworkflows",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def get_step_parameters(
        self,
        sub_workflow_id: str = "",
        workflow_name_filter: str = "",
        subworkflow_name_filter: str = "",
        required_para: bool = True,
        optional_parameter: bool = True,
        hidden_para: bool = False,
    ) -> Dict[str, Any]:
        with self._debug_call_session("get_step_parameters"):
            if sub_workflow_id.strip():
                step_data = self._query_step_parameters(sub_workflow_id.strip())
                flattened = self._flatten_step_parameters(step_data)
                filtered = self._filter_step_parameter_records(flattened, required_para, optional_parameter, hidden_para)
                augmented = {
                    "subworkflowId": sub_workflow_id.strip(),
                    "code": 1,
                    "data": {"filteredParameters": filtered, "raw": step_data},
                }
                return {"step_parameters_raw_json": augmented, "filtered_subworkflows": []}

            bindings = self._filter_workflow_records(
                self._query_workflow_records(workflow_name_filter),
                workflow_name_filter=workflow_name_filter,
                subworkflow_name_filter=subworkflow_name_filter,
            )
            if len(bindings) != 1:
                message = f"匹配到 {len(bindings)} 个子工作流，请收窄 workflow_name_filter/subworkflow_name_filter"
                status = {
                    "code": 0 if bindings else -1,
                    "message": message,
                    "data": {"matches": bindings},
                    "matches": len(bindings),
                }
                return {"step_parameters_raw_json": status, "filtered_subworkflows": bindings}

            binding = bindings[0]
            step_data = self._query_step_parameters(binding["subworkflowId"])
            flattened = self._flatten_step_parameters(step_data)
            filtered = self._filter_step_parameter_records(flattened, required_para, optional_parameter, hidden_para)
            augmented = {
                "workflowId": binding.get("workflowId"),
                "workflowName": binding.get("workflowName"),
                "subworkflowId": binding.get("subworkflowId"),
                "subworkflowName": binding.get("subworkflowName"),
                "code": 1,
                "data": {"filteredParameters": filtered, "rawData": step_data},
            }
            return {"step_parameters_raw_json": augmented, "filtered_subworkflows": bindings}

    @action(
        always_free=True,
        description="按工作流名称提交多肽实验（非 Day1）",
        handles=[
            ActionInputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel 相对路径",
                data_key="sample_excel_relative_path",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionOutputHandle(key="order_id", data_type="bioyond_order_id", label="实验ID", data_key="order_id", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="order_ids", data_type="bioyond_order_ids", label="实验ID列表", data_key="order_ids", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="resultTable", data_type="table", label="装载确认表", data_key="resultTable", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="sample_file", data_type="bioyond_sample_file", label="样品文件", data_key="sample_file", data_source=DataSource.EXECUTOR),
        ],
    )
    def submit_experiment(
        self,
        required_params: PeptideGenericSubmitRequiredParams,
        optional_params: Optional[PeptideGenericSubmitOptionalParams] = None,
        sample_excel_relative_path: str = "",
    ) -> Dict[str, Any]:
        return self._submit_experiment_core(
            day_key=None,
            required_params=required_params,
            optional_params=optional_params,
            sample_excel_relative_path=sample_excel_relative_path,
            generic=True,
        )

    @action(
        always_free=True,
        description="提交 Day2 多肽定量实验",
        handles=[
            ActionInputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel 相对路径",
                data_key="sample_excel_relative_path",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionOutputHandle(key="order_id", data_type="bioyond_order_id", label="实验ID", data_key="order_id", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="order_ids", data_type="bioyond_order_ids", label="实验ID列表", data_key="order_ids", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="resultTable", data_type="table", label="装载确认表", data_key="resultTable", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="sample_file", data_type="bioyond_sample_file", label="样品文件", data_key="sample_file", data_source=DataSource.EXECUTOR),
        ],
    )
    def submit_experiment_day2(
        self,
        required_params: PeptideDay2RequiredParams,
        optional_params: Optional[PeptideDay2OptionalParams] = None,
        sample_excel_relative_path: str = "",
    ) -> Dict[str, Any]:
        return self._submit_experiment_core("day2", required_params, optional_params, sample_excel_relative_path)

    @action(
        always_free=True,
        description="提交 Day3 线肽环化实验",
        handles=[
            ActionInputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel 相对路径",
                data_key="sample_excel_relative_path",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionOutputHandle(key="order_id", data_type="bioyond_order_id", label="实验ID", data_key="order_id", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="order_ids", data_type="bioyond_order_ids", label="实验ID列表", data_key="order_ids", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="resultTable", data_type="table", label="装载确认表", data_key="resultTable", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="sample_file", data_type="bioyond_sample_file", label="样品文件", data_key="sample_file", data_source=DataSource.EXECUTOR),
        ],
    )
    def submit_experiment_day3(
        self,
        required_params: PeptideDay3RequiredParams,
        optional_params: Optional[PeptideDay3OptionalParams] = None,
        sample_excel_relative_path: str = "",
    ) -> Dict[str, Any]:
        return self._submit_experiment_core("day3", required_params, optional_params, sample_excel_relative_path)

    @action(
        always_free=True,
        description="提交 Day4 环肽酰化（酶标）实验",
        handles=[
            ActionInputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel 相对路径",
                data_key="sample_excel_relative_path",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionOutputHandle(key="order_id", data_type="bioyond_order_id", label="实验ID", data_key="order_id", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="order_ids", data_type="bioyond_order_ids", label="实验ID列表", data_key="order_ids", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="resultTable", data_type="table", label="装载确认表", data_key="resultTable", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="sample_file", data_type="bioyond_sample_file", label="样品文件", data_key="sample_file", data_source=DataSource.EXECUTOR),
        ],
    )
    def submit_experiment_day4(
        self,
        required_params: PeptideDay4RequiredParams,
        optional_params: Optional[PeptideDay4OptionalParams] = None,
        sample_excel_relative_path: str = "",
    ) -> Dict[str, Any]:
        return self._submit_experiment_core("day4", required_params, optional_params, sample_excel_relative_path)

    @action(
        always_free=True,
        description="提交 Day4 环肽酰化 LCMS 实验",
        handles=[
            ActionInputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel 相对路径",
                data_key="sample_excel_relative_path",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionOutputHandle(key="order_id", data_type="bioyond_order_id", label="实验ID", data_key="order_id", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="order_ids", data_type="bioyond_order_ids", label="实验ID列表", data_key="order_ids", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="resultTable", data_type="table", label="装载确认表", data_key="resultTable", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="sample_file", data_type="bioyond_sample_file", label="样品文件", data_key="sample_file", data_source=DataSource.EXECUTOR),
        ],
    )
    def submit_experiment_day4_LCMS(
        self,
        required_params: PeptideDay4LCMSRequiredParams,
        optional_params: Optional[PeptideDay4LCMSOptionalParams] = None,
        sample_excel_relative_path: str = "",
    ) -> Dict[str, Any]:
        return self._submit_experiment_core("day4_lcms", required_params, optional_params, sample_excel_relative_path)

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
        goal_default={"materials_loaded": False, "timeout_seconds": 3600, "assignee_user_ids": []},
        feedback_interval=300,
        description="Day1 线肽合成提交占位（暂不创建订单）",
        handles=[
            ActionInputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel",
                data_key="sample_excel_relative_path",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
        ],
    )
    def submit_experiment_day1(
        self,
        required_params: PeptideDay1RequiredParams,
        optional_params: Optional[PeptideDay1OptionalParams] = None,
        sample_excel_relative_path: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        # TODO: Day1 订单创建待 API 现场验证后再接入 create_order；目前只回显占位结构。
        del kwargs
        optional = dict(optional_params or {})
        sample_file, selected = self._resolve_submit_sample_file(
            required_params,
            optional,
            sample_excel_relative_path,
        )
        cem_method = str(required_params.get("cem_method_file_name") or DAY1_CEM_METHOD_DEFAULT).strip() or DAY1_CEM_METHOD_DEFAULT
        partial_entries, override_warnings = self._build_partial_parameter_entries(
            sample_excel_relative_path=sample_file,
            day_key="day1",
            parameter_overrides=optional.get("parameter_overrides"),
            extra_autofill=[{"Key": DAY1_CEM_METHOD_KEY, "Value": cem_method}],
        )
        binding = self._resolve_workflow_binding("day1")
        return {
            "success": True,
            "status": "manual_confirm_placeholder",
            "message": "Day1 订单创建暂未启用，请人工确认样品与方法文件后继续下游节点。",
            "workflow": binding,
            "sample_file": sample_file,
            "selected_sample_excel": selected,
            "partial_parameter_entries": partial_entries,
            "cem_method_file_name": cem_method,
            "auto_register_materials": bool(optional.get("auto_register_materials", True)),
            "warnings": override_warnings,
        }

    def _submit_experiment_core(
        self,
        day_key: Optional[str],
        required_params: Dict[str, Any],
        optional_params: Optional[Dict[str, Any]],
        sample_excel_relative_path: str = "",
        *,
        generic: bool = False,
    ) -> Dict[str, Any]:
        optional = dict(optional_params or {})
        warnings: List[str] = []
        action_name = "submit_experiment" if generic else f"submit_experiment_{day_key}"
        with self._debug_call_session(action_name):
            if generic:
                workflow_name = str(required_params.get("workflow_name") or "").strip()
                if not workflow_name:
                    raise PeptideWorkflowError("submit_experiment 必须提供 workflow_name")
                if workflow_name == DAY1_PEPTIDE_WORKFLOW_NAME or "day1" in workflow_name.lower():
                    raise PeptideWorkflowError("Day1 请使用 submit_experiment_day1；通用提交暂不支持 Day1 线肽合成")
                subworkflow_name = str(optional.get("subworkflow_name") or "").strip()
                binding = self._resolve_workflow_binding_from_names(workflow_name, subworkflow_name)
            else:
                binding = self._resolve_workflow_binding(day_key or "")

            sample_file, selected = self._resolve_submit_sample_file(required_params, optional, sample_excel_relative_path)
            partial_entries, override_warnings = self._build_partial_parameter_entries(
                sample_excel_relative_path=sample_file,
                day_key=day_key,
                parameter_overrides=optional.get("parameter_overrides"),
            )
            warnings.extend(override_warnings)

            step_data = self._query_step_parameters(binding["sub_workflow_id"])
            flattened = self._flatten_step_parameters(step_data)
            # 未来校验可能基于 TaskDisplayable/Value/DisplayValue 分类（见 get_step_parameters）。
            resolved_entries = self._resolve_parameter_entries_against_live_steps(partial_entries, flattened)
            param_values = self._group_resolved_entries_to_param_values(resolved_entries)
            order_code, order_name = self._build_order_identity(day_key or "generic", optional.get("order_name"))
            order_payload = self._create_order_payload(
                order_code=order_code,
                order_name=order_name,
                sub_workflow_id=binding["sub_workflow_id"],
                param_values=param_values,
                border_number=int(optional.get("border_number") or 1),
                extend_properties=optional.get("extend_properties"),
            )
            create_order_raw = self._create_order(order_payload)
            allocation = self._parse_create_order_allocation_map(create_order_raw)
            order_ids = allocation["order_ids"]
            order_id = order_ids[0] if order_ids else ""
            if not allocation["allocation_rows"]:
                warnings.append("create_order_allocation_unavailable_for_result_table")
            result_table = self._build_result_table(allocation["materials_by_type"])
            auto_register = bool(optional.get("auto_register_materials", True))
            material_registration = (
                {"requested": True, "status": "not_implemented"} if auto_register else {"requested": False, "status": "skipped"}
            )
            return {
                "success": bool(order_ids),
                "order_id": order_id,
                "order_ids": order_ids,
                "order_code": order_code,
                "order_name": order_name,
                "workflow": binding,
                "sub_workflow_id": binding["sub_workflow_id"],
                "sample_file": sample_file,
                "selected_sample_excel": selected,
                "payload_summary": {"borderNumber": int(optional.get("border_number") or 1), "orderCode": order_code},
                "create_order_data_raw": create_order_raw,
                "allocation_map": allocation["allocation_map"],
                "allocation_rows": allocation["allocation_rows"],
                "resultTable": result_table,
                "start_experiment": {
                    "order_id": order_id,
                    "order_ids": order_ids,
                    "resultTable": result_table,
                    "materials_loaded": False,
                },
                "auto_register_materials": auto_register,
                "material_registration": material_registration,
                "warnings": warnings,
            }

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
        goal_default={"materials_loaded": False, "timeout_seconds": 3600, "assignee_user_ids": []},
        feedback_interval=300,
        description="确认物料装载后启动调度器",
        handles=[
            ActionInputHandle(key="order_id", data_type="bioyond_order_id", label="实验ID", data_key="order_id", data_source=DataSource.HANDLE, io_type="source"),
            ActionInputHandle(key="order_ids", data_type="bioyond_order_ids", label="实验ID列表", data_key="order_ids", data_source=DataSource.HANDLE, io_type="source"),
            ActionInputHandle(key="resultTable", data_type="table", label="装载确认表", data_key="resultTable", data_source=DataSource.HANDLE, io_type="source"),
        ],
    )
    def start_experiment(
        self,
        order_id: str = "",
        order_ids: Optional[List[str]] = None,
        resultTable: Optional[Dict[str, Any]] = None,
        materials_loaded: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        with self._debug_call_session("start_experiment"):
            resolved_order_ids = self._extract_order_ids(order_id=order_id, order_ids=order_ids, **kwargs)
            table_rows = resultTable.get("data") if isinstance(resultTable, dict) else []
            if table_rows and not materials_loaded:
                raise RuntimeError("多肽物料装载未确认，拒绝启动调度器")
            result = self._run_scheduler_action("scheduler_start", "启动")
            result["order_ids"] = resolved_order_ids
            result["materials_loaded"] = bool(materials_loaded)
            result["resultTable"] = resultTable or {}
            return result

    @action(
        always_free=True,
        goal_default={
            "reset_operations": ["scheduler_reset", "reset_order_status", "reset_location"],
        },
        description="复位调度器/订单/库位",
    )
    def reset(
        self,
        reset_operations: Optional[
            List[Literal["scheduler_reset", "reset_order_status", "reset_location"]]
        ] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        with self._debug_call_session("reset"):
            operations = self._normalize_reset_operations(reset_operations)
            result: Dict[str, Any] = {
                "selected_operations": operations,
                "executed_calls": [],
                "skipped_operations": [],
            }
            rpc = self._require_hardware_interface()
            for operation in operations:
                if operation == "scheduler_reset":
                    code = rpc.scheduler_reset()
                    result["executed_calls"].append({"operation": operation, "result": {"code": code}})
                elif operation == "reset_order_status":
                    resolved = str(
                        kwargs.get("reset_order_id") or kwargs.get("order_id") or ""
                    ).strip()
                    if not resolved:
                        result["skipped_operations"].append(
                            {"operation": operation, "reason": "缺少 order_id/reset_order_id"}
                        )
                        continue
                    code = rpc.reset_order_status(resolved)
                    result["executed_calls"].append(
                        {"operation": operation, "order_id": resolved, "result": {"code": code}}
                    )
                elif operation == "reset_location":
                    resolved = str(
                        kwargs.get("reset_location_id") or kwargs.get("location_id") or ""
                    ).strip()
                    if not resolved:
                        result["skipped_operations"].append(
                            {"operation": operation, "reason": "缺少 location_id/reset_location_id"}
                        )
                        continue
                    code = rpc.reset_location(resolved)
                    result["executed_calls"].append(
                        {"operation": operation, "location_id": resolved, "result": {"code": code}}
                    )
                else:
                    raise ValueError(f"未知 reset operation: {operation}")
            return result

    @action(always_free=True, description="启动 Bioyond 调度器")
    def scheduler_start(self, **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        return self._run_scheduler_action("scheduler_start", "启动")

    @action(always_free=True, description="停止 Bioyond 调度器")
    def scheduler_stop(self, **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        return self._run_scheduler_action("scheduler_stop", "停止")

    @action(always_free=True, description="暂停 Bioyond 调度器")
    def scheduler_pause(self, **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        return self._run_scheduler_action("scheduler_pause", "暂停")

    @action(always_free=True, description="继续 Bioyond 调度器")
    def scheduler_continue(self, **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        return self._run_scheduler_action("scheduler_continue", "继续")

    @action(always_free=True, description="查询 LIMS 订单列表")
    def get_order_list(
        self,
        time_type: str = "",
        begin_time: Any = None,
        end_time: Any = None,
        status: str = "",
        filter_text: str = "",
        skip_count: int = 0,
        page_count: int = 20,
        sorting: str = "",
    ) -> Dict[str, Any]:
        params = self._normalize_order_list_params(
            {
                "timeType": time_type,
                "beginTime": begin_time,
                "endTime": end_time,
                "status": status,
                "filter": filter_text,
                "skipCount": skip_count,
                "pageCount": page_count,
                "sorting": sorting,
            }
        )
        with self._debug_call_session("get_order_list"):
            raw = self._require_hardware_interface().order_query(json.dumps(params, ensure_ascii=False))
        items = self._as_list(raw.get("items") if isinstance(raw, dict) else raw)
        return {"success": True, "raw": raw, "items": items, "total_count": raw.get("totalCount") if isinstance(raw, dict) else len(items)}

    @action(always_free=True, description="查询单订单实验报告")
    def get_order_report(self, order_id: str) -> Dict[str, Any]:
        resolved = self._require_uuid(order_id, "order_id")
        with self._debug_call_session("get_order_report"):
            raw = self._require_hardware_interface().order_report(resolved)
        return {"success": True, "order_id": resolved, "raw": raw, "summary": self._normalize_order_report(raw)}

    @action(always_free=True, description="聚合订单报告（占位）")
    def get_aggregated_order_report(self, order_id: str) -> Dict[str, Any]:
        # TODO: 待多肽侧确认聚合需求后再实现。
        # Sirna 风格聚合通常组合以下接口：
        #   - /api/lims/order/order-report
        #   - /api/lims/order/order-list （order-query）
        #   - /api/lims/order/gantt-with-simulation-by-order-id
        #   - /api/lims/order/gantts-by-order-id
        #   - /api/lims/storage/material-info
        resolved = self._require_uuid(order_id, "order_id")
        return {
            "success": False,
            "status": "not_implemented",
            "order_id": resolved,
            "message": "聚合报告尚未实现，请使用 get_order_report。",
        }

    # ---------- 样品 Excel ----------

    def _resolve_submit_sample_file(
        self,
        required_params: Dict[str, Any],
        optional_params: Dict[str, Any],
        sample_excel_relative_path: str = "",
    ) -> Tuple[str, Dict[str, Any]]:
        del optional_params  # local upload 已在 plan 移除范围内
        direct = str(sample_excel_relative_path or "").strip()
        if direct:
            return direct.replace("/", "\\"), {"relativePath": direct, "fileName": Path(direct).name}
        pattern = str(required_params.get("sample_excel_pattern") or "").strip()
        if not pattern:
            raise PeptideWorkflowError("必须提供 sample_excel_relative_path 或 sample_excel_pattern")
        selected = self._select_sample_excel_record(
            self._list_sample_excels(name_filter=pattern.replace("*", "")),
            pattern,
        )
        relative = str(selected.get("relativePath") or "").replace("/", "\\")
        if not relative:
            raise PeptideWorkflowError(f"样品 Excel 记录缺少 relativePath: {selected}")
        return relative, selected

    def _select_sample_excel_record(self, records: List[Dict[str, Any]], pattern: str) -> Dict[str, Any]:
        matched = [
            record
            for record in records
            if self._filename_matches_pattern(str(record.get("fileName") or ""), pattern)
            or self._filename_matches_pattern(str(record.get("relativePath") or ""), pattern)
        ]
        if not matched:
            raise PeptideWorkflowError(f"未找到匹配 {pattern!r} 的样品 Excel")
        if len(matched) > 1:
            names = ", ".join(str(item.get("fileName") or "") for item in matched)
            raise PeptideWorkflowError(f"找到多个匹配 {pattern!r} 的样品 Excel: {names}")
        return matched[0]

    def _list_sample_excels(self, name_filter: str = "", begin_date: Any = None, end_date: Any = None) -> List[Dict[str, Any]]:
        rpc = self._require_hardware_interface()
        payload = {"beginDate": begin_date, "endDate": end_date, "nameFilter": name_filter or None}
        response = rpc.post(
            url=f"{rpc.host}/api/lims/order/sample-info-excels",
            params={"apiKey": rpc.api_key, "requestTime": _utc_now_iso8601_ms(), "data": payload},
        )
        if not response or response.get("code") != 1:
            raise RuntimeError(f"样品 Excel 列表查询失败: {response}")
        data = response.get("data")
        return data if isinstance(data, list) else []

    def _upload_sample_excel_file(self, local_excel_path: str | Path, content_type: Optional[str] = None) -> Dict[str, Any]:
        api_host = str(self.bioyond_config.get("api_host", "")).rstrip("/")
        timeout = int(self.bioyond_config.get("timeout", 30) or 30)
        local_path = Path(str(local_excel_path).strip()).expanduser()
        if not local_path.is_absolute() and not str(local_excel_path).startswith("./"):
            raise ValueError("样品 Excel 路径请使用完整路径；相对路径必须以 ./ 开头")
        if not local_path.exists() or not local_path.is_file():
            raise FileNotFoundError(f"样品 Excel 不存在: {local_path}")
        resolved_content_type = (
            content_type
            or mimetypes.guess_type(local_path.name)[0]
            or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        with local_path.open("rb") as file:
            response = requests.post(
                f"{api_host}/api/lims/order/up-load-sample-file",
                files={"file": (local_path.name, file, resolved_content_type)},
                timeout=timeout,
            )
        try:
            body: Any = response.json()
        except ValueError:
            body = {"raw_text": response.text}
        if response.status_code >= 400 or not isinstance(body, dict) or body.get("code") != 1:
            raise RuntimeError(f"样品 Excel 上传失败: status={response.status_code}, body={body}")
        file_info = body.get("data") if isinstance(body.get("data"), dict) else {}
        remote = str(file_info.get("filePath") or "")
        return {
            "success": True,
            "lims_file_info": file_info,
            "relative_path": remote,
            "sample_file_parameter": remote.replace("/", "\\") if remote else "",
            "response": body,
        }

    @staticmethod
    def _filename_matches_pattern(file_name: str, pattern: str) -> bool:
        if not pattern or pattern == "*":
            return True
        if "*" in pattern:
            from fnmatch import fnmatch

            return fnmatch(file_name, pattern)
        # 无通配符 → 子串匹配（plan 中样品 pattern 通常是文件名前缀，如 "DPR019"）。
        return pattern in file_name

    # ---------- 工作流 / 步骤参数 ----------

    def _resolve_workflow_binding(self, day_key: str) -> Dict[str, Any]:
        config = DAY_WORKFLOW_BINDINGS.get(day_key)
        if not config:
            raise PeptideWorkflowError(f"未知 day_key: {day_key}")
        return self._resolve_workflow_binding_from_names(config["root_name"], config["sub_name"])

    def _resolve_workflow_binding_from_names(self, workflow_name: str, subworkflow_name: str = "") -> Dict[str, Any]:
        bindings = self._filter_workflow_records(
            self._query_workflow_records(workflow_name),
            workflow_name_filter=workflow_name,
            subworkflow_name_filter=subworkflow_name,
        )
        if not bindings:
            raise PeptideWorkflowError(f"未找到工作流 {workflow_name!r} 的可用子工作流")
        if len(bindings) > 1:
            names = ", ".join(item.get("subworkflowName", "") for item in bindings)
            raise PeptideWorkflowError(f"工作流 {workflow_name!r} 匹配到多个子工作流: {names}")
        item = bindings[0]
        return {
            "workflow_name": item.get("workflowName"),
            "root_workflow_id": item.get("workflowId"),
            "sub_workflow_id": item.get("subworkflowId"),
            "sub_workflow_name": item.get("subworkflowName"),
            "raw": item,
        }

    def _query_workflow_records(self, workflow_name_filter: str, include_detail: bool = True) -> List[Dict[str, Any]]:
        payload = {"type": 0, "filter": workflow_name_filter, "includeDetail": include_detail}
        data = self._require_hardware_interface().query_workflow(json.dumps(payload, ensure_ascii=False))
        records: List[Dict[str, Any]] = []
        for item in self._as_list(data.get("items") if isinstance(data, dict) else data):
            if not isinstance(item, dict):
                continue
            root_id = str(item.get("id") or "")
            root_name = str(item.get("name") or "")
            for sub in self._as_list(item.get("subWorkflows")):
                if not isinstance(sub, dict):
                    continue
                if sub.get("isSaved") is False:
                    continue
                records.append(
                    {
                        "workflowId": root_id,
                        "workflowName": root_name,
                        "subworkflowId": str(sub.get("id") or ""),
                        "subworkflowName": str(sub.get("name") or ""),
                        "sequence": sub.get("sequence"),
                        "status": sub.get("status"),
                    }
                )
        return records

    def _filter_workflow_records(
        self,
        workflow_records: List[Dict[str, Any]],
        *,
        workflow_name_filter: str = "",
        subworkflow_name_filter: str = "",
    ) -> List[Dict[str, Any]]:
        wf_filter = workflow_name_filter.strip()
        sub_filter = subworkflow_name_filter.strip()

        def _match_name(value: str, needle: str) -> bool:
            if not needle:
                return True
            return needle in value

        filtered = [
            record
            for record in workflow_records
            if _match_name(str(record.get("workflowName") or ""), wf_filter)
            and _match_name(str(record.get("subworkflowName") or ""), sub_filter)
        ]
        if wf_filter:
            exact = [r for r in filtered if str(r.get("workflowName") or "") == wf_filter]
            if exact:
                filtered = exact
        if sub_filter:
            exact_sub = [r for r in filtered if str(r.get("subworkflowName") or "") == sub_filter]
            if exact_sub:
                filtered = exact_sub
        return filtered

    def _query_step_parameters(self, sub_workflow_id: str) -> Any:
        data = self._require_hardware_interface().workflow_step_query(self._require_uuid(sub_workflow_id, "sub_workflow_id"))
        return data or {}

    def _flatten_step_parameters(self, step_data: Any) -> List[Dict[str, Any]]:
        parsed = self._json_loads_if_string(step_data)
        if not isinstance(parsed, dict):
            return []
        flattened: List[Dict[str, Any]] = []
        for step_id, modules in parsed.items():
            if not self._looks_like_uuid_text(step_id):
                continue
            for module in self._as_list(modules):
                if not isinstance(module, dict):
                    continue
                step_name = str(module.get("name") or "")
                module_m = module.get("m")
                module_n = module.get("n")
                for parameter in self._as_list(module.get("parameterList") or module.get("ParameterList")):
                    if not isinstance(parameter, dict):
                        continue
                    key = parameter.get("Key") or parameter.get("key")
                    if not key:
                        continue
                    flattened.append(
                        {
                            "step": str(step_id),
                            "step_name": step_name,
                            "Key": str(key),
                            "display_para_name": (
                                parameter.get("display_para_name")
                                or parameter.get("displayParaName")
                                or parameter.get("DisplayName")
                                or parameter.get("name")
                                or str(key)
                            ),
                            "m": parameter.get("m", module_m),
                            "n": parameter.get("n", module_n),
                            "TaskDisplayable": parameter.get("TaskDisplayable", parameter.get("task_displayable", parameter.get("taskDisplayable"))),
                            "Value": parameter.get("Value", parameter.get("value")),
                            "DisplayValue": parameter.get("DisplayValue", parameter.get("displayValue")),
                        }
                    )
        return flattened

    def _filter_step_parameter_records(
        self,
        records: List[Dict[str, Any]],
        required_para: bool,
        optional_parameter: bool,
        hidden_para: bool,
    ) -> List[Dict[str, Any]]:
        filtered: List[Dict[str, Any]] = []
        for record in records:
            displayable = record.get("TaskDisplayable")
            is_hidden = displayable in (0, "0", False)
            is_displayable = displayable in (1, "1", True)
            has_value = self._parameter_value_present(record.get("Value")) or self._parameter_value_present(record.get("DisplayValue"))
            if is_hidden and hidden_para:
                filtered.append(record)
            elif is_displayable and not has_value and required_para:
                filtered.append(record)
            elif is_displayable and has_value and optional_parameter:
                filtered.append(record)
            # TaskDisplayable 既非 0/1 时（None 或其它），保守跳过避免误归类。
        return filtered

    @staticmethod
    def _parameter_value_present(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str) and value == "":
            return False
        return True

    def _build_partial_parameter_entries(
        self,
        *,
        sample_excel_relative_path: str,
        day_key: Optional[str],
        parameter_overrides: Any = None,
        extra_autofill: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        warnings: List[str] = []
        entries: List[Dict[str, Any]] = [{"Key": PEPTIDE_SAMPLE_FILE_KEY, "Value": sample_excel_relative_path}]
        if day_key == "day1" and extra_autofill:
            entries.extend(extra_autofill)
        entries.extend(self._normalize_override_list(parameter_overrides, warnings))
        return entries, warnings

    def _normalize_override_list(self, overrides: Any, warnings: List[str]) -> List[Dict[str, Any]]:
        if overrides is None or overrides == "" or overrides == []:
            return []
        if isinstance(overrides, dict):
            # 容错：用户传 dict 时按 {Key: Value} 展开。
            overrides = [{"Key": k, "Value": v} for k, v in overrides.items()]
        if not isinstance(overrides, list):
            raise PeptideWorkflowError("parameter_overrides 必须是列表或字典")
        normalized: List[Dict[str, Any]] = []
        seen: Dict[Tuple[Any, ...], int] = {}
        for raw in overrides:
            entry = self._normalize_parameter_entry(raw, allow_key_only=True)
            if entry is None:
                continue
            key = (entry.get("Key"), entry.get("m"), entry.get("n"))
            if key in seen:
                warnings.append(f"parameter_overrides 重复项 {key}，采用最后一次覆盖")
                normalized[seen[key]] = entry
            else:
                seen[key] = len(normalized)
                normalized.append(entry)
        return normalized

    def _normalize_parameter_entry(self, entry: Any, *, allow_key_only: bool = False) -> Optional[Dict[str, Any]]:
        if not isinstance(entry, dict):
            return None
        key = entry.get("Key") or entry.get("key")
        value = entry.get("Value", entry.get("value"))
        if not key:
            return None
        if value is None and not allow_key_only:
            return None
        normalized: Dict[str, Any] = {"Key": str(key), "Value": value}
        for axis in ("m", "n"):
            if axis in entry and entry[axis] is not None:
                try:
                    normalized[axis] = int(entry[axis])
                except (TypeError, ValueError):
                    normalized[axis] = entry[axis]
        return normalized

    def _resolve_parameter_entries_against_live_steps(
        self,
        partial_entries: List[Dict[str, Any]],
        flattened: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        resolved: List[Dict[str, Any]] = []
        for partial in partial_entries:
            key = partial.get("Key")
            m_filter = partial.get("m")
            n_filter = partial.get("n")
            matches = [
                live
                for live in flattened
                if live.get("Key") == key
                and (m_filter is None or live.get("m") == m_filter)
                and (n_filter is None or live.get("n") == n_filter)
            ]
            if len(matches) != 1:
                raise PeptideWorkflowError(
                    f"参数 Key={key} m={m_filter if m_filter is not None else '<omitted>'} "
                    f"n={n_filter if n_filter is not None else '<omitted>'} 期望唯一匹配，实际 {len(matches)} 条"
                )
            live = matches[0]
            resolved.append(
                {
                    "step": live.get("step"),
                    "Key": key,
                    "Value": partial.get("Value"),
                    "m": partial.get("m", live.get("m")),
                    "n": partial.get("n", live.get("n")),
                }
            )
        return resolved

    def _group_resolved_entries_to_param_values(self, resolved_entries: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for entry in resolved_entries:
            step_id = str(entry.get("step") or "")
            if not self._looks_like_uuid_text(step_id):
                continue
            payload_entry: Dict[str, Any] = {
                "key": str(entry.get("Key")),
                "value": "" if entry.get("Value") is None else str(entry.get("Value")),
            }
            for axis in ("m", "n"):
                if entry.get(axis) is not None:
                    try:
                        payload_entry[axis] = int(entry[axis])
                    except (TypeError, ValueError):
                        payload_entry[axis] = entry[axis]
            grouped.setdefault(step_id, []).append(payload_entry)
        return grouped

    def _build_order_identity(self, day_key: str, order_name_override: Any = None) -> Tuple[str, str]:
        stamp = datetime.now().strftime("%y%m%d-%H%M%S")
        order_code = f"EXP{stamp}"
        if order_name_override:
            return order_code, str(order_name_override)
        return order_code, f"实验{stamp}"

    def _create_order_payload(
        self,
        *,
        order_code: str,
        order_name: str,
        sub_workflow_id: str,
        param_values: Dict[str, List[Dict[str, Any]]],
        border_number: int,
        extend_properties: Any,
    ) -> List[Dict[str, Any]]:
        item: Dict[str, Any] = {
            "orderCode": order_code,
            "orderName": order_name,
            "borderNumber": border_number,
            "workFlowId": self._require_uuid(sub_workflow_id, "workFlowId"),
            "paramValues": param_values,
            "extendProperties": "" if extend_properties in (None, "") else str(extend_properties),
        }
        return [item]

    def _create_order(self, order_payload: List[Dict[str, Any]]) -> Any:
        return self._require_hardware_interface().create_order(json.dumps(order_payload, ensure_ascii=False))

    def _parse_create_order_allocation_map(self, create_order_data_raw: Any) -> Dict[str, Any]:
        parsed = self._parse_result(create_order_data_raw)
        allocation_map: Dict[str, Any] = {}
        if isinstance(parsed, dict):
            allocation_map = parsed
        order_ids = [key for key in allocation_map if self._looks_like_uuid_text(key)]
        rows: List[Dict[str, Any]] = []
        for order_key in order_ids:
            for row in self._as_list(allocation_map.get(order_key)):
                if isinstance(row, dict):
                    rows.append(row)
        materials_by_type: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            mode = str(row.get("materialTypeMode") or "Unknown")
            materials_by_type.setdefault(mode, []).append(row)
        return {
            "allocation_map": allocation_map,
            "allocation_rows": rows,
            "order_ids": order_ids,
            "materials_by_type": materials_by_type,
        }

    def _build_result_table(self, materials_by_type: Dict[str, List[Dict[str, Any]]], table_name: str = "resultTable") -> Dict[str, Any]:
        material_info_cache: Dict[str, Dict[str, Any]] = {}
        ordered_modes: List[str] = []
        for mode in MATERIAL_TYPE_ORDER:
            if mode in materials_by_type:
                ordered_modes.append(mode)
        for mode in materials_by_type:
            if mode not in ordered_modes:
                ordered_modes.append(mode)
        rows: List[Dict[str, Any]] = []
        for mode in ordered_modes:
            for record in materials_by_type.get(mode, []):
                material_id = str(record.get("materialId") or "")
                location_code = str(record.get("locationShowName") or record.get("locationCode") or "")
                rows.append(
                    {
                        "whName": self._resolve_wh_name_by_material_id(material_id, material_info_cache),
                        "locationCode": location_code,
                        "materialName": str(record.get("materialName") or ""),
                        "quantity": str(record.get("quantity") or ""),
                    }
                )
        return {"data": rows, "columns": copy.deepcopy(RESULT_TABLE_COLUMNS), "tableName": table_name}

    def _resolve_wh_name_by_material_id(self, material_id: str, cache: Dict[str, Dict[str, Any]]) -> str:
        if not material_id:
            return ""
        if material_id not in cache:
            try:
                cache[material_id] = self._require_hardware_interface().material_info(material_id) or {}
            except Exception as exc:
                logger.warning("material_info 查询失败 material_id=%s: %s", material_id, exc)
                cache[material_id] = {}
        locations = self._as_list(cache[material_id].get("locations"))
        location = next((loc for loc in locations if isinstance(loc, dict)), {})
        return str(location.get("whName") or "")

    def _normalize_order_list_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "timeType": params.get("timeType") or "",
            "beginTime": params.get("beginTime"),
            "endTime": params.get("endTime"),
            "status": params.get("status") or "",
            "filter": params.get("filter") or "",
            "skipCount": int(params.get("skipCount") or 0),
            "pageCount": int(params.get("pageCount") or 20),
            "sorting": params.get("sorting") or "",
        }

    def _normalize_order_report(self, raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        return {
            "id": raw.get("id"),
            "name": raw.get("name"),
            "code": raw.get("code"),
            "workflow_name": raw.get("workflowName"),
            "status": raw.get("status"),
            "status_name": raw.get("statusName"),
            "pre_intakes_count": len(self._as_list(raw.get("preIntakes"))),
            "result_list_count": len(self._as_list(raw.get("resultList"))),
        }

    # ---------- 基础设施 ----------

    def _run_scheduler_action(self, method_name: str, label: str) -> Dict[str, Any]:
        rpc = self._require_hardware_interface()
        method = getattr(rpc, method_name, None)
        if not callable(method):
            raise RuntimeError(f"RPC 缺少调度器方法: {method_name}")
        code = method()
        success = code == 1
        return {"success": success, "code": code, "message": f"调度器{label}{'成功' if success else '失败'}"}

    def _require_hardware_interface(self):
        interface = getattr(self, "hardware_interface", None)
        if interface is None:
            raise RuntimeError("BioyondPeptideStation 未绑定 hardware_interface")
        return interface

    @staticmethod
    def _normalize_reset_operations(reset_operations: Optional[List[str]]) -> List[str]:
        alias_map = {
            "scheduler": "scheduler_reset",
            "scheduler_reset": "scheduler_reset",
            "order": "reset_order_status",
            "order_status": "reset_order_status",
            "reset_order_status": "reset_order_status",
            "location": "reset_location",
            "reset_location": "reset_location",
        }
        normalized: List[str] = []
        for operation in list(reset_operations or DEFAULT_RESET_OPERATIONS):
            canonical = alias_map.get(str(operation).strip())
            if not canonical:
                raise ValueError(f"未知 reset operation: {operation}")
            if canonical not in normalized:
                normalized.append(canonical)
        return normalized

    @staticmethod
    def _reset_operation_endpoint(operation: str) -> str:
        return {
            "scheduler_reset": "/api/lims/scheduler/reset",
            "reset_order_status": "/api/lims/order/reset-order-status",
            "reset_location": "/api/lims/storage/reset-location",
        }.get(operation, "")

    @staticmethod
    def _extract_order_ids(order_id: str = "", order_ids: Optional[List[str]] = None, **kwargs: Any) -> List[str]:
        resolved: List[str] = []
        if order_id:
            resolved.append(str(order_id))
        raw = order_ids if order_ids is not None else kwargs.get("order_ids")
        if isinstance(raw, list):
            resolved.extend(str(value) for value in raw if value)
        return list(dict.fromkeys(resolved))

    def _parse_result(self, result: Any) -> Any:
        if not isinstance(result, str):
            return result
        text = result.strip()
        if not text:
            return text
        try:
            return json.loads(text)
        except ValueError:
            pass
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return text

    @staticmethod
    def _looks_like_uuid_text(value: Any) -> bool:
        text = str(value)
        return len(text) == 36 and text.count("-") == 4

    def _require_uuid(self, value: Any, field_name: str) -> str:
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError(f"{field_name} 必须是 UUID: {value!r}") from exc

    @staticmethod
    def _json_loads_if_string(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return value
        try:
            return json.loads(text)
        except ValueError:
            return value

    @staticmethod
    def _as_list(value: Any) -> List[Any]:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]


def main() -> int:
    assert DEBUG_CLI_ENABLED, "CLI 工作流探测仅在 DEBUG_CLI_ENABLED=True 时可用"
    parser = argparse.ArgumentParser(description="Peptide Station 工作流列表拉取（调试）")
    parser.add_argument("config_path", help="JSON 配置文件路径")
    parser.add_argument("--workflow-type", type=int, default=0)
    parser.add_argument("--filter", default="")
    args = parser.parse_args()
    result = fetch_workflow_list(config_path=args.config_path, workflow_type=args.workflow_type, filter_text=args.filter)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    response_body = result.get("response", {})
    ok = result.get("http_status") == 200 and isinstance(response_body, dict) and response_body.get("code") == 1
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
