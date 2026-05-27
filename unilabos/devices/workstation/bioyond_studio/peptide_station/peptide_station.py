"""Bioyond 多肽工作站：LIMS 提交/复位/调度与样品 Excel 工作流。"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import mimetypes
import sys
import threading
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Dict, Iterable, List, Optional, Tuple
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
RESET_OPERATION_KEYS: Tuple[str, ...] = (
    "reset_scheduler",
    "reset_order_status",
    "reset_location",
    "reset_devices",
)
RESET_OPERATION_LABELS: Dict[str, str] = {
    "reset_scheduler": "调度器复位",
    "reset_order_status": "订单状态复位",
    "reset_location": "库位复位",
    "reset_devices": "仪器复位",
}
RESET_OPERATION_ENDPOINTS: Dict[str, str] = {
    "reset_scheduler": "/api/lims/scheduler/reset",
    "reset_order_status": "/api/lims/order/reset-order-status",
    "reset_location": "/api/lims/storage/reset-location",
    "reset_devices": "/api/lims/device/reset-devices",
}
RESET_MANUAL_CONFIRM_MESSAGE = (
    "请确认G3、CEM、Tecan、撕膜机、封膜机、打标机、旋转堆栈上下料位、3个转台等位置的物料已清理完毕；\n"
    "请开门检查冰箱、IDOT、酶标仪、离心机、LCMS内部没有遗留物料。"
)
CEM_INFO_CONFIRM_MESSAGE = "打开下述链接查看CEM校验信息，确认无误后勾选 cem_info_confirmed。"
RESULT_TABLE_COLUMNS = [
    {"name": "设备", "key": "whName"},
    {"name": "位置", "key": "locationCode"},
    {"name": "物料名称", "key": "materialName"},
    {"name": "数量", "key": "quantity"},
]
UNLOAD_TABLE_COLUMNS = [
    {"name": "仓库名称", "key": "whName"},
    {"name": "坐标 X", "key": "posX"},
    {"name": "坐标 Y", "key": "posY"},
    {"name": "坐标 Z", "key": "posZ"},
    {"name": "单位", "key": "unit"},
    {"name": "物料名称", "key": "materialName"},
]
UNLOAD_TABLE_COLUMNS_MULTI_ORDER = [
    {"name": "订单编号", "key": "orderCode"},
    *UNLOAD_TABLE_COLUMNS,
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
            default_factory=list,
            description=(
                "参数覆盖列表，默认留空（不覆盖）。"
                "如需覆盖子工作流某个步骤参数，按 [{\"Key\": \"参数名\", \"Value\": \"值\", \"m\": 0, \"n\": 0}] 格式填写。"
                "Key 必须与 Bioyond 子工作流里某个 step 参数名精确匹配；m/n 可选，省略时 Key 在工作流内必须唯一。"
            ),
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
        # 订单完成报送等待机制（多肽场景）：
        # - last_order_code 记录当前正在等待的 orderCode（业务编号），用于回调侧多订单隔离
        # - last_order_report 缓存最近一次匹配到的 report.data
        # - order_finish_event 用于阻塞等待 + 唤醒 wait_for_order_finish 动作
        self.order_finish_event = threading.Event()
        self.last_order_code: Optional[str] = None
        self.last_order_report: Optional[Dict[str, Any]] = None
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
            ActionOutputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel 相对路径",
                data_key="sample_excel_relative_path",
                data_source=DataSource.EXECUTOR,
            ),
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
            ActionOutputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel 相对路径",
                data_key="sample_excel_relative_path",
                data_source=DataSource.EXECUTOR,
            ),
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
            ActionOutputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel 相对路径",
                data_key="sample_excel_relative_path",
                data_source=DataSource.EXECUTOR,
            ),
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
            ActionOutputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel 相对路径",
                data_key="sample_excel_relative_path",
                data_source=DataSource.EXECUTOR,
            ),
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
            ActionOutputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel 相对路径",
                data_key="sample_excel_relative_path",
                data_source=DataSource.EXECUTOR,
            ),
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
        description="提交 Day1 线肽合成实验",
        handles=[
            ActionInputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel",
                data_key="sample_excel_relative_path",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionOutputHandle(key="order_id", data_type="bioyond_order_id", label="实验ID", data_key="order_id", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="order_ids", data_type="bioyond_order_ids", label="实验ID列表", data_key="order_ids", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="resultTable", data_type="table", label="装载确认表", data_key="resultTable", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="cem_method_file_name", data_type="str", label="CEM 方法文件", data_key="cem_method_file_name", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel 相对路径",
                data_key="sample_excel_relative_path",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def submit_experiment_day1(
        self,
        required_params: PeptideDay1RequiredParams,
        optional_params: Optional[PeptideDay1OptionalParams] = None,
        sample_excel_relative_path: str = "",
    ) -> Dict[str, Any]:
        required = dict(required_params or {})
        cem_method = str(required.get("cem_method_file_name") or DAY1_CEM_METHOD_DEFAULT).strip() or DAY1_CEM_METHOD_DEFAULT
        required["cem_method_file_name"] = cem_method
        result = self._submit_experiment_core("day1", required, optional_params, sample_excel_relative_path)
        result["cem_method_file_name"] = cem_method
        return result

    @action(
        always_free=True,
        description="生成 Day1 CEM 校验信息",
        goal_default={"cem_method_file_name": DAY1_CEM_METHOD_DEFAULT},
        handles=[
            ActionInputHandle(
                key="cem_method_file_name",
                data_type="str",
                label="CEM 方法文件",
                data_key="cem_method_file_name",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionInputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel 相对路径",
                data_key="sample_excel_relative_path",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionOutputHandle(key="success", data_type="bool", label="是否成功", data_key="success", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="cem_method_file_name", data_type="str", label="CEM 方法文件", data_key="cem_method_file_name", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel 相对路径",
                data_key="sample_excel_relative_path",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(key="cem_pdf_path", data_type="str", label="CEM 校验文件路径", data_key="cem_pdf_path", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="cem_info_url", data_type="str", label="CEM 校验链接", data_key="cem_info_url", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="prepare_cem_response", data_type="json", label="prepare-cEM 响应", data_key="prepare_cem_response", data_source=DataSource.EXECUTOR),
        ],
    )
    def prepare_cem(
        self,
        cem_method_file_name: str = DAY1_CEM_METHOD_DEFAULT,
        sample_excel_relative_path: str = "",
    ) -> Dict[str, Any]:
        excel_path = str(sample_excel_relative_path or "").strip().replace("/", "\\")
        if not excel_path:
            raise PeptideWorkflowError("prepare_cem 缺少 sample_excel_relative_path")
        method = str(cem_method_file_name or DAY1_CEM_METHOD_DEFAULT).strip() or DAY1_CEM_METHOD_DEFAULT
        rpc = self._require_hardware_interface()
        api_host = str(getattr(rpc, "host", "") or self.bioyond_config.get("api_host", "")).rstrip("/")
        request_body = {
            "apiKey": rpc.api_key,
            "requestTime": _utc_now_iso8601_ms(),
            "data": {"methodFileName": method, "excelPath": excel_path},
        }
        with self._debug_call_session("prepare_cem"):
            response = rpc.post(
                url=f"{api_host}/api/lims/order/prepare-cEM",
                params=request_body,
            )
        if not isinstance(response, dict) or response.get("code") != 1:
            raise RuntimeError(f"prepare-cEM 调用失败: {response}")
        data = response.get("data")
        cem_pdf_path = self._extract_cem_pdf_path(data)
        if not cem_pdf_path:
            raise RuntimeError(f"prepare-cEM 响应缺少 data: {response}")
        return {
            "success": True,
            "cem_method_file_name": method,
            "sample_excel_relative_path": excel_path,
            "cem_pdf_path": cem_pdf_path,
            "cem_info_url": self._join_api_url(api_host, cem_pdf_path),
            "prepare_cem_response": response,
        }

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
        goal_default={"cem_info_confirmed": False, "timeout_seconds": 3600, "assignee_user_ids": []},
        feedback_interval=300,
        description=CEM_INFO_CONFIRM_MESSAGE,
        handles=[
            ActionInputHandle(key="cem_pdf_path", data_type="str", label="CEM 校验文件路径", data_key="cem_pdf_path", data_source=DataSource.HANDLE, io_type="source"),
            ActionInputHandle(key="cem_info_url", data_type="str", label="CEM 校验链接", data_key="cem_info_url", data_source=DataSource.HANDLE, io_type="source"),
            ActionInputHandle(key="cem_method_file_name", data_type="str", label="CEM 方法文件", data_key="cem_method_file_name", data_source=DataSource.HANDLE, io_type="source"),
            ActionInputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel 相对路径",
                data_key="sample_excel_relative_path",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionOutputHandle(key="cem_pdf_path", data_type="str", label="CEM 校验文件路径", data_key="cem_pdf_path", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="cem_info_url", data_type="str", label="CEM 校验链接", data_key="cem_info_url", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="cem_method_file_name", data_type="str", label="CEM 方法文件", data_key="cem_method_file_name", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(
                key="sample_excel_relative_path",
                data_type="bioyond_sample_file",
                label="样品 Excel 相对路径",
                data_key="sample_excel_relative_path",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(key="instruction_text", data_type="str", label="确认说明", data_key="instruction_text", data_source=DataSource.EXECUTOR),
        ],
    )
    def confirm_cem_info(
        self,
        cem_pdf_path: str = "",
        cem_info_url: str = "",
        cem_method_file_name: str = "",
        sample_excel_relative_path: str = "",
        cem_info_confirmed: bool = False,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        del timeout_seconds, assignee_user_ids, kwargs
        if not bool(cem_info_confirmed):
            raise RuntimeError("CEM 校验信息未确认，拒绝继续工作流")
        return {
            "success": True,
            "cem_pdf_path": str(cem_pdf_path or ""),
            "cem_info_url": str(cem_info_url or ""),
            "cem_method_file_name": str(cem_method_file_name or DAY1_CEM_METHOD_DEFAULT).strip() or DAY1_CEM_METHOD_DEFAULT,
            "sample_excel_relative_path": str(sample_excel_relative_path or "").replace("/", "\\"),
            "cem_info_confirmed": True,
            "instruction_text": CEM_INFO_CONFIRM_MESSAGE,
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
                subworkflow_name = str(optional.get("subworkflow_name") or "").strip()
                binding = self._resolve_workflow_binding_from_names(workflow_name, subworkflow_name)
            else:
                binding = self._resolve_workflow_binding(day_key or "")

            resolved_sample_excel_path, selected = self._resolve_submit_sample_file(required_params, optional, sample_excel_relative_path)
            partial_entries, override_warnings = self._build_partial_parameter_entries(
                sample_excel_relative_path=resolved_sample_excel_path,
                day_key=day_key,
                required_params=required_params,
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
                "sample_excel_relative_path": resolved_sample_excel_path,
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
            ActionOutputHandle(key="order_id", data_type="bioyond_order_id", label="实验ID", data_key="order_id", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="order_ids", data_type="bioyond_order_ids", label="实验ID列表", data_key="order_ids", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="resultTable", data_type="table", label="装载确认表", data_key="resultTable", data_source=DataSource.EXECUTOR),
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
            result["order_id"] = resolved_order_ids[0] if resolved_order_ids else str(order_id or "")
            result["order_ids"] = resolved_order_ids
            result["materials_loaded"] = bool(materials_loaded)
            result["resultTable"] = resultTable or {}
            return result

    def process_order_finish_report(self, report_request, used_materials: Optional[List[Any]] = None) -> Dict[str, Any]:
        """处理 LIMS /report/order_finish 推送：保留父类语义，并按 orderCode 唤醒等待动作。

        说明：
        - 工作站 HTTP 服务为进程级单例，所有 wait 节点共用同一条推送通道；
          需要按 ``self.last_order_code`` 过滤，避免别的订单 push 错误唤醒当前等待。
        """
        materials = used_materials or []
        try:
            result = super().process_order_finish_report(report_request, materials)
        except Exception as exc:
            logger.error("基类 process_order_finish_report 失败: %s", exc, exc_info=True)
            result = {"processed": False, "error": str(exc)}

        try:
            data = getattr(report_request, "data", {}) or {}
            report_order_code = str(data.get("orderCode") or "")
            self.last_order_report = data
            expected = self.last_order_code
            logger.info(
                "[peptide.order_finish] 收到 orderCode=%s 期望=%s status=%s",
                report_order_code,
                expected,
                data.get("status"),
            )
            if expected and report_order_code and expected == report_order_code:
                self.order_finish_event.set()
                logger.info("[peptide.order_finish] orderCode 匹配，已触发 order_finish_event")
            elif expected and report_order_code and expected != report_order_code:
                logger.warning(
                    "[peptide.order_finish] orderCode 不匹配，忽略本次 push (期望=%s 实际=%s)",
                    expected,
                    report_order_code,
                )
        except Exception as exc:  # pragma: no cover - 仅为防御
            logger.error("[peptide.order_finish] 触发 event 失败: %s", exc, exc_info=True)

        return result

    @action(
        always_free=True,
        description="等待订单完成回调并预生成下料表",
        handles=[
            ActionInputHandle(key="order_id", data_type="bioyond_order_id", label="实验ID", data_key="order_id", data_source=DataSource.HANDLE, io_type="source"),
            ActionInputHandle(key="order_ids", data_type="bioyond_order_ids", label="实验ID列表", data_key="order_ids", data_source=DataSource.HANDLE, io_type="source"),
            ActionOutputHandle(key="order_id", data_type="bioyond_order_id", label="实验ID", data_key="order_id", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="order_code", data_type="str", label="订单编号", data_key="order_code", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="order_finish_status", data_type="str", label="订单完成状态", data_key="order_finish_status", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="order_finish_report", data_type="json", label="订单完成报文", data_key="order_finish_report", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="used_materials", data_type="json", label="使用物料列表", data_key="used_materials", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="material_ids", data_type="json", label="物料ID列表", data_key="material_ids", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="preintake_ids", data_type="json", label="通量ID列表", data_key="preintake_ids", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="unloadTable", data_type="table", label="下料表", data_key="unloadTable", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="unload_summary", data_type="json", label="下料摘要", data_key="unload_summary", data_source=DataSource.EXECUTOR),
        ],
    )
    def wait_for_order_finish(
        self,
        order_id: str = "",
        order_ids: Optional[List[str]] = None,
        order_code: str = "",
        timeout_seconds: int = 36000,
        poll_mode: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """阻塞等待 LIMS 订单完成回调，并基于 usedMaterials 预生成下料表。

        - 多订单 ``order_ids`` 时按顺序逐个等；任何一个 ``abnormal_stop`` 立即返回。
        - 节点 1 在此就把 ``unloadTable`` 组装好（前端 manual_confirm 弹窗在节点 2
          中通过 ``getPreviousNodeResult`` 拿前一个节点 param 渲染）。
        """
        with self._debug_call_session("wait_for_order_finish"):
            resolved_order_ids = self._extract_order_ids(order_id=order_id, order_ids=order_ids, **kwargs)
            order_code_input = str(order_code or "").strip()
            if not resolved_order_ids and not order_code_input:
                raise PeptideWorkflowError("wait_for_order_finish 至少需要 order_id/order_ids/order_code 之一")

            material_info_cache: Dict[str, Dict[str, Any]] = {}
            missing_material_info: List[str] = []
            unload_rows: List[Dict[str, Any]] = []
            used_materials_total: List[Dict[str, Any]] = []
            material_ids_total: List[str] = []
            preintake_ids_total: List[str] = []
            order_codes_seen: List[str] = []
            last_status: str = ""
            last_report: Dict[str, Any] = {}
            multi_order = len(resolved_order_ids) > 1 or (resolved_order_ids and order_code_input)

            wait_targets: List[Tuple[str, str]] = []
            if resolved_order_ids:
                for oid in resolved_order_ids:
                    code_for_oid = self._resolve_order_code(oid, fallback=order_code_input if len(resolved_order_ids) == 1 else "")
                    wait_targets.append((oid, code_for_oid))
            else:
                wait_targets.append(("", order_code_input))

            for oid, code_for_oid in wait_targets:
                if not code_for_oid:
                    raise PeptideWorkflowError(
                        f"wait_for_order_finish 无法解析 orderCode (order_id={oid!r})"
                    )
                order_codes_seen.append(code_for_oid)
                wait_result = self._wait_single_order_finish(code_for_oid, timeout_seconds, poll_mode=poll_mode)
                last_status = wait_result["status"]
                last_report = wait_result["report"] or {}
                used_materials = self._extract_used_materials(last_report)
                used_materials_total.extend(used_materials)
                material_ids_total.extend(self._collect_material_ids(used_materials))
                preintake_ids_total.extend(self._collect_preintake_ids(used_materials))

                rows = self._build_unload_rows(
                    used_materials,
                    material_info_cache=material_info_cache,
                    missing_material_info=missing_material_info,
                    order_code=code_for_oid if multi_order else None,
                )
                unload_rows.extend(rows)

                if last_status == "timeout":
                    break
                if last_status == "abnormal_stop":
                    break

            unload_table = self._compose_unload_table(unload_rows, multi_order=multi_order)
            unload_summary = {
                "order_codes": order_codes_seen,
                "total_items": len(unload_rows),
                "missing_material_info": list(dict.fromkeys(missing_material_info)),
            }
            primary_order_id = resolved_order_ids[0] if resolved_order_ids else ""
            primary_order_code = order_codes_seen[0] if order_codes_seen else order_code_input

            return {
                "success": last_status == "success",
                "order_id": primary_order_id,
                "order_ids": resolved_order_ids,
                "order_code": primary_order_code,
                "order_codes": order_codes_seen,
                "order_finish_status": last_status,
                "order_finish_report": last_report,
                "used_materials": used_materials_total,
                "material_ids": list(dict.fromkeys(material_ids_total)),
                "preintake_ids": list(dict.fromkeys(preintake_ids_total)),
                "unloadTable": unload_table,
                "unload_summary": unload_summary,
            }

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
        goal_default={"materials_unloaded": False, "timeout_seconds": 3600, "assignee_user_ids": []},
        feedback_interval=300,
        description="确认人工下料完成后调用 take-out 通知奔耀同步状态",
        handles=[
            ActionInputHandle(key="order_id", data_type="bioyond_order_id", label="实验ID", data_key="order_id", data_source=DataSource.HANDLE, io_type="source"),
            ActionInputHandle(key="material_ids", data_type="json", label="物料ID列表", data_key="material_ids", data_source=DataSource.HANDLE, io_type="source"),
            ActionInputHandle(key="preintake_ids", data_type="json", label="通量ID列表", data_key="preintake_ids", data_source=DataSource.HANDLE, io_type="source"),
            ActionInputHandle(key="unloadTable", data_type="table", label="下料表", data_key="unloadTable", data_source=DataSource.HANDLE, io_type="source"),
            ActionOutputHandle(key="take_out_result", data_type="json", label="取出接口结果", data_key="take_out_result", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="unloaded_count", data_type="int", label="同步物料数量", data_key="unloaded_count", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="success", data_type="bool", label="同步是否成功", data_key="success", data_source=DataSource.EXECUTOR),
        ],
    )
    def unload_materials(
        self,
        order_id: str = "",
        material_ids: Optional[List[str]] = None,
        preintake_ids: Optional[List[str]] = None,
        unloadTable: Optional[Dict[str, Any]] = None,
        materials_unloaded: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """节点 2：人工下料 manual_confirm 解锁后调用 take-out 通知奔耀同步状态。

        时序：操作员物理下料 → 勾选 ``materials_unloaded=True`` → 批准 →
        manual_confirm 解除阻塞 → 此处调用 ``take-out`` 让奔耀清空对应库位状态。
        """
        del unloadTable, kwargs  # unloadTable 仅供前端弹窗渲染，本节点函数体不消费
        with self._debug_call_session("unload_materials"):
            if not bool(materials_unloaded):
                raise RuntimeError("下料未确认，拒绝结束节点")
            resolved_order_id = str(order_id or "").strip()
            if not resolved_order_id:
                raise PeptideWorkflowError("unload_materials 缺少 order_id")
            material_ids_list = [str(item) for item in (material_ids or []) if item]
            preintake_ids_list = [str(item) for item in (preintake_ids or []) if item]
            rpc = self._require_hardware_interface()
            try:
                take_out_result = rpc.take_out(
                    resolved_order_id,
                    preintake_ids=preintake_ids_list,
                    material_ids=material_ids_list,
                ) or {}
            except Exception as exc:
                logger.warning(
                    "take_out 调用异常 order_id=%s material_ids=%s: %s",
                    resolved_order_id,
                    material_ids_list,
                    exc,
                )
                take_out_result = {"code": 0, "message": f"take_out_invoke_failed: {exc}"}

            code_value = take_out_result.get("code") if isinstance(take_out_result, dict) else None
            success = bool(isinstance(take_out_result, dict) and code_value == 1)
            if not success:
                logger.warning(
                    "take_out 业务失败，未阻塞工作流，请人工核对奔耀库位 order_id=%s response=%s",
                    resolved_order_id,
                    take_out_result,
                )
            return {
                "success": success,
                "order_id": resolved_order_id,
                "material_ids": material_ids_list,
                "preintake_ids": preintake_ids_list,
                "unloaded_count": len(material_ids_list),
                "take_out_result": take_out_result,
            }

    @action(
        always_free=True,
        goal_default={
            "reset_scheduler": True,
            "reset_order_status": True,
            "reset_location": True,
            "reset_devices": False,
        },
        description="自动复位调度器/订单状态/库位，可选仪器复位",
    )
    def reset_auto(
        self,
        reset_scheduler: bool = True,
        reset_order_status: bool = True,
        reset_location: bool = True,
        reset_devices: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """自动复位调度器/订单状态/库位，可选仪器复位。

        Args:
            reset_scheduler[调度器复位]: 调用 /api/lims/scheduler/reset，默认勾选。
            reset_order_status[订单状态复位]: 调用 /api/lims/order/reset-order-status，默认勾选。
            reset_location[库位复位]: 调用 /api/lims/storage/reset-location，默认勾选。
            reset_devices[仪器复位]: 调用 /api/lims/device/reset-devices，默认不勾选。
        """
        del kwargs
        with self._debug_call_session("reset_auto"):
            return self._execute_reset_operations(
                reset_scheduler=bool(reset_scheduler),
                reset_order_status=bool(reset_order_status),
                reset_location=bool(reset_location),
                reset_devices=bool(reset_devices),
            )

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
        goal_default={
            "reset_scheduler": True,
            "reset_order_status": True,
            "reset_location": True,
            "reset_devices": False,
            "physical_cleanup_confirmed": False,
            "timeout_seconds": 3600,
            "assignee_user_ids": [],
        },
        feedback_interval=300,
        description=RESET_MANUAL_CONFIRM_MESSAGE,
    )
    def reset_manual(
        self,
        reset_scheduler: bool = True,
        reset_order_status: bool = True,
        reset_location: bool = True,
        reset_devices: bool = False,
        physical_cleanup_confirmed: bool = False,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """人工确认物理清理完毕后执行复位。

        操作员需先按弹窗提示完成 G3/CEM/Tecan/撕膜机/封膜机/打标机/旋转堆栈/3 个转台
        等位置的物料清理，并开门检查冰箱/IDOT/酶标仪/离心机/LCMS 内部无遗留，再勾选
        ``physical_cleanup_confirmed``，节点才会真正调用复位接口。

        Args:
            reset_scheduler[调度器复位]: 调用 /api/lims/scheduler/reset，默认勾选。
            reset_order_status[订单状态复位]: 调用 /api/lims/order/reset-order-status，默认勾选。
            reset_location[库位复位]: 调用 /api/lims/storage/reset-location，默认勾选。
            reset_devices[仪器复位]: 调用 /api/lims/device/reset-devices，默认不勾选。
            physical_cleanup_confirmed[物理清理确认]: 确认弹窗中的物料检查已完成，默认不勾选；未勾选时不会调用任何 RPC。
        """
        del kwargs, timeout_seconds, assignee_user_ids
        with self._debug_call_session("reset_manual"):
            if not bool(physical_cleanup_confirmed):
                logger.info("[reset_manual] 物理清理未确认，拒绝执行复位 RPC")
                return {
                    "status": "blocked",
                    "physical_cleanup_confirmed": False,
                    "confirmation_message": RESET_MANUAL_CONFIRM_MESSAGE,
                    "selected_operations": self._build_selected_operations_summary(
                        reset_scheduler=bool(reset_scheduler),
                        reset_order_status=bool(reset_order_status),
                        reset_location=bool(reset_location),
                        reset_devices=bool(reset_devices),
                    ),
                    "executed_calls": [],
                    "skipped_operations": [
                        {"operation": op, "reason": "physical_cleanup_not_confirmed"}
                        for op in RESET_OPERATION_KEYS
                    ],
                    "warnings": ["physical_cleanup_not_confirmed"],
                }
            payload = self._execute_reset_operations(
                reset_scheduler=bool(reset_scheduler),
                reset_order_status=bool(reset_order_status),
                reset_location=bool(reset_location),
                reset_devices=bool(reset_devices),
            )
            payload["physical_cleanup_confirmed"] = True
            payload["confirmation_message"] = RESET_MANUAL_CONFIRM_MESSAGE
            return payload

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

    @staticmethod
    def _extract_cem_pdf_path(data: Any) -> str:
        if isinstance(data, dict):
            for key in ("cemPdfPath", "cem_pdf_path", "pdfPath", "path", "url"):
                value = data.get(key)
                if value:
                    return str(value)
            return ""
        return str(data or "")

    @staticmethod
    def _join_api_url(api_host: str, path: str) -> str:
        base = str(api_host or "").rstrip("/")
        suffix = str(path or "").replace("\\", "/").lstrip("/")
        return f"{base}/{suffix}" if base else suffix

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
        required_params: Optional[Dict[str, Any]] = None,
        parameter_overrides: Any = None,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        warnings: List[str] = []
        entries: List[Dict[str, Any]] = [{"Key": PEPTIDE_SAMPLE_FILE_KEY, "Value": sample_excel_relative_path}]
        if day_key == "day1":
            required = dict(required_params or {})
            cem_method = str(required.get("cem_method_file_name") or DAY1_CEM_METHOD_DEFAULT).strip() or DAY1_CEM_METHOD_DEFAULT
            entries.append({"Key": DAY1_CEM_METHOD_KEY, "Value": cem_method})
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

    # ---------- wait_for_order_finish / unload_materials 辅助 ----------

    def _wait_single_order_finish(
        self,
        order_code: str,
        timeout_seconds: int,
        *,
        poll_mode: bool = False,
        poll_interval: float = 0.5,
    ) -> Dict[str, Any]:
        """阻塞等待单个 orderCode 的 LIMS 完成推送，返回 ``{status, report}``.

        与 :class:`BioyondCellWorkstation` 保持相同语义：
        - 状态映射 ``"30" -> success`` / ``"-11" -> abnormal_stop`` /
          ``"-12" -> manual_stop`` / 其它 ``unknown_<status>``；超时返回 ``timeout``。
        """
        if not order_code:
            return {"status": "error", "report": {}, "message": "empty order_code"}
        self.last_order_code = order_code
        self.last_order_report = None
        self.order_finish_event.clear()
        timeout_value = max(int(timeout_seconds or 0), 1)
        logger.info("[peptide.order_finish] 开始等待 orderCode=%s timeout=%ss poll_mode=%s", order_code, timeout_value, poll_mode)

        if poll_mode:
            start_time = time.time()
            while not self.order_finish_event.is_set():
                if time.time() - start_time > timeout_value:
                    logger.error("[peptide.order_finish] 等待超时 orderCode=%s", order_code)
                    return {"status": "timeout", "report": {}, "orderCode": order_code}
                time.sleep(poll_interval)
        else:
            if not self.order_finish_event.wait(timeout=timeout_value):
                logger.error("[peptide.order_finish] 等待超时 orderCode=%s", order_code)
                return {"status": "timeout", "report": {}, "orderCode": order_code}

        report = self.last_order_report or {}
        report_code = str(report.get("orderCode") or "")
        if report_code and report_code != order_code:
            logger.warning("[peptide.order_finish] 报送 orderCode 不匹配 期望=%s 实际=%s", order_code, report_code)
            return {"status": "mismatch", "report": report}
        status_text = str(report.get("status") or "").strip()
        status_map = {"30": "success", "-11": "abnormal_stop", "-12": "manual_stop"}
        normalized = status_map.get(status_text, f"unknown_{status_text or 'empty'}")
        return {"status": normalized, "report": report}

    def _resolve_order_code(self, order_id: str, fallback: str = "") -> str:
        """将 order_id (UUID) 反查为 orderCode。fallback 用于 CLI 调试时直接传 orderCode。"""
        order_id_clean = str(order_id or "").strip()
        if not order_id_clean:
            return fallback.strip()
        try:
            raw = self._require_hardware_interface().order_report(order_id_clean) or {}
        except Exception as exc:
            logger.warning("反查 orderCode 失败 order_id=%s: %s", order_id_clean, exc)
            return fallback.strip()
        if isinstance(raw, dict):
            for key in ("code", "orderCode", "order_code"):
                value = raw.get(key)
                if value:
                    return str(value)
        return fallback.strip()

    def _extract_used_materials(self, report: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(report, dict):
            return []
        result: List[Dict[str, Any]] = []
        for item in self._as_list(report.get("usedMaterials")):
            if isinstance(item, dict):
                result.append(item)
        return result

    @staticmethod
    def _collect_material_ids(used_materials: List[Dict[str, Any]]) -> List[str]:
        ids: List[str] = []
        for item in used_materials:
            material_id = item.get("materialId") or item.get("MaterialId") or ""
            if material_id:
                ids.append(str(material_id))
        return ids

    @staticmethod
    def _collect_preintake_ids(used_materials: List[Dict[str, Any]]) -> List[str]:
        ids: List[str] = []
        for item in used_materials:
            preintake_id = item.get("preintakeId") or item.get("preIntakeId") or ""
            if preintake_id:
                ids.append(str(preintake_id))
        return ids

    def _build_unload_rows(
        self,
        used_materials: List[Dict[str, Any]],
        *,
        material_info_cache: Dict[str, Dict[str, Any]],
        missing_material_info: List[str],
        order_code: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for material in used_materials:
            material_id = str(material.get("materialId") or material.get("MaterialId") or "")
            info = self._fetch_material_info_cached(material_id, material_info_cache, missing_material_info)
            location = self._first_location(info)
            row = {
                "whName": str(location.get("whName") or ""),
                "posX": self._stringify_coord(location.get("posX")),
                "posY": self._stringify_coord(location.get("posY")),
                "posZ": self._stringify_coord(location.get("posZ")),
                "unit": str(info.get("unit") or location.get("unit") or ""),
                "materialName": str(info.get("name") or ""),
                "materialId": material_id,
                "typeMode": str(material.get("typeMode") or material.get("typemode") or ""),
            }
            if order_code is not None:
                row["orderCode"] = order_code
            rows.append(row)
        return rows

    def _fetch_material_info_cached(
        self,
        material_id: str,
        cache: Dict[str, Dict[str, Any]],
        missing_material_info: List[str],
    ) -> Dict[str, Any]:
        if not material_id:
            return {}
        if material_id in cache:
            return cache[material_id]
        try:
            info = self._require_hardware_interface().material_info(material_id) or {}
        except Exception as exc:
            logger.warning("material_info 查询失败 material_id=%s: %s", material_id, exc)
            info = {}
        if not isinstance(info, dict) or not info:
            missing_material_info.append(material_id)
            info = {}
        cache[material_id] = info
        return info

    def _first_location(self, info: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(info, dict):
            return {}
        for location in self._as_list(info.get("locations")):
            if isinstance(location, dict):
                return location
        return {}

    @staticmethod
    def _stringify_coord(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            if value.is_integer():
                return str(int(value))
        return str(value)

    @staticmethod
    def _compose_unload_table(rows: List[Dict[str, Any]], *, multi_order: bool) -> Dict[str, Any]:
        columns = UNLOAD_TABLE_COLUMNS_MULTI_ORDER if multi_order else UNLOAD_TABLE_COLUMNS
        return {
            "data": rows,
            "columns": copy.deepcopy(columns),
            "tableName": "unloadTable",
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
    def _build_selected_operations_summary(
        *,
        reset_scheduler: bool,
        reset_order_status: bool,
        reset_location: bool,
        reset_devices: bool,
    ) -> List[Dict[str, Any]]:
        flags: Dict[str, bool] = {
            "reset_scheduler": bool(reset_scheduler),
            "reset_order_status": bool(reset_order_status),
            "reset_location": bool(reset_location),
            "reset_devices": bool(reset_devices),
        }
        return [
            {"key": key, "label": RESET_OPERATION_LABELS[key], "selected": flags[key]}
            for key in RESET_OPERATION_KEYS
        ]

    def _execute_reset_operations(
        self,
        *,
        reset_scheduler: bool,
        reset_order_status: bool,
        reset_location: bool,
        reset_devices: bool,
    ) -> Dict[str, Any]:
        """根据 4 个 checkbox 选择顺序调用对应 RPC。

        - 调用顺序固定为 scheduler → order_status → location → devices；
        - 单步失败（``code != 1`` 或 RPC 抛异常）记 warning 但继续执行后续选中的步骤，
          不做 fail-fast，便于操作员在遇到部分故障时仍能完成可恢复的复位。
        """
        rpc = self._require_hardware_interface()
        flags: Dict[str, bool] = {
            "reset_scheduler": bool(reset_scheduler),
            "reset_order_status": bool(reset_order_status),
            "reset_location": bool(reset_location),
            "reset_devices": bool(reset_devices),
        }
        selected_operations = self._build_selected_operations_summary(
            reset_scheduler=reset_scheduler,
            reset_order_status=reset_order_status,
            reset_location=reset_location,
            reset_devices=reset_devices,
        )
        result: Dict[str, Any] = {
            "selected_operations": selected_operations,
            "executed_calls": [],
            "skipped_operations": [],
            "warnings": [],
        }

        rpc_method_map: Dict[str, str] = {
            "reset_scheduler": "scheduler_reset",
            "reset_order_status": "reset_order_status",
            "reset_location": "reset_location",
            "reset_devices": "reset_devices",
        }

        for operation in RESET_OPERATION_KEYS:
            if not flags[operation]:
                result["skipped_operations"].append(
                    {"operation": operation, "reason": "checkbox_disabled"}
                )
                continue
            method_name = rpc_method_map[operation]
            method = getattr(rpc, method_name, None)
            endpoint = RESET_OPERATION_ENDPOINTS[operation]
            if not callable(method):
                msg = f"RPC 缺少方法: {method_name}"
                logger.warning("[reset] %s", msg)
                result["executed_calls"].append({
                    "operation": operation,
                    "endpoint": endpoint,
                    "result": {"code": 0},
                    "error": msg,
                })
                result["warnings"].append(f"{operation}: {msg}")
                continue
            try:
                code = method()
            except Exception as exc:  # 单步异常不阻断其余 reset
                logger.warning("[reset] %s 调用异常: %s", method_name, exc)
                result["executed_calls"].append({
                    "operation": operation,
                    "endpoint": endpoint,
                    "result": {"code": 0},
                    "error": str(exc),
                })
                result["warnings"].append(f"{operation}: {exc}")
                continue
            result["executed_calls"].append({
                "operation": operation,
                "endpoint": endpoint,
                "result": {"code": code},
            })
            if code != 1:
                result["warnings"].append(f"{operation}: rpc_returned_non_one_code={code}")
        return result

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
