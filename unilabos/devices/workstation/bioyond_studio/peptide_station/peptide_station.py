"""多肽工作站最小脚手架。"""

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
from typing import Annotated, Any, Dict, Iterable, List, Optional
from uuid import UUID

import requests

try:
    from typing_extensions import TypedDict
except ImportError:  # pragma: no cover - 仅用于轻量环境导入
    from typing import TypedDict  # type: ignore

try:
    from pydantic import Field
except Exception:  # pragma: no cover - 仅用于无 pydantic 的轻量环境导入
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
except Exception as exc:  # pragma: no cover - 允许轻量 helper 导入
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
except Exception as exc:  # pragma: no cover - 允许在轻量探测模式下运行
    WorkstationBase = object  # type: ignore[assignment,misc]
    BioyondWorkstation = object  # type: ignore[assignment,misc]
    _BIOYOND_IMPORT_ERROR = exc


_PARAMETER_KEY_ALIASES = {
    "Type": "type",
    "Key": "key",
    "Value": "value",
    "DisplayValue": "displayValue",
    "Name": "name",
    "Unit": "unit",
    "Options": "options",
    "Children": "children",
    "Items": "items",
}

DEFAULT_RESET_OPERATIONS = ("scheduler_reset", "reset_order_status", "reset_location")
# Day1 多肽合成工作流已在接口手册中出现，但当前站点不公开 Day1 提交动作。
DAY1_PEPTIDE_WORKFLOW_NAME = "多肽合成"
DAY2_PEPTIDE_WORKFLOW_NAME = "DAY2多肽定量"
DAY3_PEPTIDE_WORKFLOW_NAME = "Day3线肽环化"
DAY4_PEPTIDE_WORKFLOW_NAME = "Day4环肽酰化-酶标+LCMS"
PEPTIDE_SAMPLE_FILE_KEYS = ("SampleFile", "ExcelPath", "excelPath", "sampleFile")


class PeptideWorkflowError(RuntimeError):
    """多肽工作流可恢复错误：当前动作失败并停止工作流，不退出 UniLabOS edge。"""


class PeptideSubmitRequiredParams(TypedDict):
    sample_excel_pattern: Annotated[str, Field(description="样品 Excel 文件名匹配模式（必填）。")]


class PeptideGenericSubmitRequiredParams(PeptideSubmitRequiredParams):
    workflow_name: Annotated[str, Field(description="工作流名称（必填，不填写工作流 ID）")]


class PeptideSubmitOptionalParams(TypedDict, total=False):
    order_name: Annotated[str, Field(description="订单名称（可选，自动生成）。")]
    border_number: Annotated[int, Field(default=1, description="LIMS 创建订单 borderNumber，默认 1。")]
    extend_properties: Annotated[str, Field(description="LIMS extendProperties 字符串。")]
    local_excel_path: Annotated[str, Field(description="本地 Excel 文件路径；auto_upload_local_excel=True 时上传。")]
    auto_upload_local_excel: Annotated[bool, Field(default=False, description="提交前是否先上传 local_excel_path。")]
    parameter_values: Annotated[Dict[str, Any], Field(description="按参数 key 覆盖 TaskDisplayable=1 工作流默认值。")]


def _apply_default_peptide_material_type_mappings(config: Dict[str, Any]) -> None:
    configured = config.get("material_type_mappings")
    if not isinstance(configured, dict):
        configured = {}
    merged = dict(DEFAULT_PEPTIDE_MATERIAL_TYPE_MAPPINGS)
    merged.update(configured)
    config["material_type_mappings"] = merged


def _utc_now_iso8601_ms() -> str:
    """返回与 Bioyond 接口兼容的 UTC 时间戳。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_peptide_config(config_path: str | Path) -> Dict[str, Any]:
    """从 JSON 文件读取多肽站配置。"""
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
    """调用工作流列表接口。"""
    resolved_config = dict(config or {})
    if config_path is not None:
        resolved_config.update(load_peptide_config(config_path))

    api_host = str(resolved_config.get("api_host", "")).rstrip("/")
    api_key = str(resolved_config.get("api_key", ""))
    timeout = int(resolved_config.get("timeout", 10))

    if not api_host:
        raise ValueError("缺少 api_host 配置")
    if not api_key:
        raise ValueError("缺少 api_key 配置")

    url = f"{api_host}/api/lims/workflow/work-flow-list"
    payload = {
        "apiKey": api_key,
        "requestTime": _utc_now_iso8601_ms(),
        "data": {
            "type": workflow_type,
            "filter": filter_text,
            "includeDetail": include_detail,
        },
    }
    result: Dict[str, Any] = {
        "url": url,
        "request_payload": payload,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
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
    """多肽工作站占位实现。"""

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
        self._created_order_ids: set[str] = set()
        self._created_order_codes: set[str] = set()

        logger.info("BioyondPeptideStation 初始化开始")
        logger.info(f"  - API Host: {self.bioyond_config.get('api_host', '')}")
        logger.info(f"  - Workflow 映射数量: {len(self.bioyond_config.get('workflow_mappings', {}))}")

        super().__init__(bioyond_config=self.bioyond_config, deck=deck)

        logger.info("BioyondPeptideStation 初始化完成")

    def _debug_call_session(self, action_name: str):
        parent_debug_session = getattr(super(), "_debug_call_session", None)
        if parent_debug_session is not None:
            return parent_debug_session(action_name)
        return nullcontext()

    @staticmethod
    def fetch_workflow_list(
        config: Optional[Dict[str, Any]] = None,
        config_path: Optional[str | Path] = None,
        workflow_type: int = 0,
        filter_text: str = "",
        include_detail: bool = True,
    ) -> Dict[str, Any]:
        """静态辅助方法，便于直接拉取工作流列表。"""
        return fetch_workflow_list(
            config=config,
            config_path=config_path,
            workflow_type=workflow_type,
            filter_text=filter_text,
            include_detail=include_detail,
        )

    @action(auto_prefix=True, description="上传多肽样品 Excel 文件")
    def upload_sample_excel(
        self,
        file_path: str,
        content_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """上传样品 Excel 到 Bioyond LIMS。

        Args:
            file_path: 本地 Excel 文件路径；建议使用完整路径。如果使用相对路径，必须以 `./` 开头。
            content_type: 文件 MIME 类型；为空时根据文件名自动推断。
        """
        with self._debug_call_session("upload_sample_excel"):
            return self._upload_sample_excel_file(file_path, content_type=content_type)

    @action(
        always_free=True,
        description="按工作流名称提交多肽实验到 Bioyond LIMS",
        handles=[
            ActionOutputHandle(
                key="order_id",
                data_type="bioyond_order_id",
                label="实验ID",
                data_key="order_id",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="order_ids",
                data_type="bioyond_order_ids",
                label="实验ID列表",
                data_key="order_ids",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="resultTable",
                data_type="table",
                label="装载确认表",
                data_key="resultTable",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="sample_file",
                data_type="bioyond_sample_file",
                label="样品文件",
                data_key="sample_file",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def submit_experiment(
        self,
        required_params: PeptideGenericSubmitRequiredParams,
        optional_params: Optional[PeptideSubmitOptionalParams] = None,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """通用多肽提交入口。

        Args:
            required_params: 必填参数组，包含 workflow_name 和 sample_excel_pattern。
            optional_params: 可选参数组；parameter_values 可按参数 key 覆盖工作流默认值。
            timeout_seconds: 传递给后续手动确认动作的超时时间。
            assignee_user_ids: 传递给后续手动确认动作的用户 ID 列表。
        """
        del kwargs
        return self._submit_experiment_core(
            required_params=required_params,
            optional_params=optional_params,
            default_workflow_name="",
            timeout_seconds=timeout_seconds,
            assignee_user_ids=assignee_user_ids,
        )

    @action(
        always_free=True,
        description="提交多肽 Day2 定量实验到 Bioyond LIMS",
        handles=[
            ActionOutputHandle(key="order_id", data_type="bioyond_order_id", label="实验ID", data_key="order_id", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="order_ids", data_type="bioyond_order_ids", label="实验ID列表", data_key="order_ids", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="resultTable", data_type="table", label="装载确认表", data_key="resultTable", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="sample_file", data_type="bioyond_sample_file", label="样品文件", data_key="sample_file", data_source=DataSource.EXECUTOR),
        ],
    )
    def submit_experiment_day2(
        self,
        required_params: PeptideSubmitRequiredParams,
        optional_params: Optional[PeptideSubmitOptionalParams] = None,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """提交 Day2，工作流名称由站点封装。"""
        del kwargs
        return self._submit_experiment_core(
            required_params=required_params,
            optional_params=optional_params,
            default_workflow_name=DAY2_PEPTIDE_WORKFLOW_NAME,
            timeout_seconds=timeout_seconds,
            assignee_user_ids=assignee_user_ids,
        )

    @action(
        always_free=True,
        description="提交多肽 Day3 线肽环化实验到 Bioyond LIMS",
        handles=[
            ActionOutputHandle(key="order_id", data_type="bioyond_order_id", label="实验ID", data_key="order_id", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="order_ids", data_type="bioyond_order_ids", label="实验ID列表", data_key="order_ids", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="resultTable", data_type="table", label="装载确认表", data_key="resultTable", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="sample_file", data_type="bioyond_sample_file", label="样品文件", data_key="sample_file", data_source=DataSource.EXECUTOR),
        ],
    )
    def submit_experiment_day3(
        self,
        required_params: PeptideSubmitRequiredParams,
        optional_params: Optional[PeptideSubmitOptionalParams] = None,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """提交 Day3，工作流名称由站点封装。"""
        del kwargs
        return self._submit_experiment_core(
            required_params=required_params,
            optional_params=optional_params,
            default_workflow_name=DAY3_PEPTIDE_WORKFLOW_NAME,
            timeout_seconds=timeout_seconds,
            assignee_user_ids=assignee_user_ids,
        )

    @action(
        always_free=True,
        description="提交多肽 Day4 环肽酰化实验到 Bioyond LIMS",
        handles=[
            ActionOutputHandle(key="order_id", data_type="bioyond_order_id", label="实验ID", data_key="order_id", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="order_ids", data_type="bioyond_order_ids", label="实验ID列表", data_key="order_ids", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="resultTable", data_type="table", label="装载确认表", data_key="resultTable", data_source=DataSource.EXECUTOR),
            ActionOutputHandle(key="sample_file", data_type="bioyond_sample_file", label="样品文件", data_key="sample_file", data_source=DataSource.EXECUTOR),
        ],
    )
    def submit_experiment_day4(
        self,
        required_params: PeptideSubmitRequiredParams,
        optional_params: Optional[PeptideSubmitOptionalParams] = None,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """提交 Day4 默认酶标+LCMS 工作流；Day4 运行效果仍需现场验证。"""
        del kwargs
        return self._submit_experiment_core(
            required_params=required_params,
            optional_params=optional_params,
            default_workflow_name=DAY4_PEPTIDE_WORKFLOW_NAME,
            timeout_seconds=timeout_seconds,
            assignee_user_ids=assignee_user_ids,
        )

    def _submit_experiment_core(
        self,
        *,
        required_params: Dict[str, Any],
        optional_params: Optional[PeptideSubmitOptionalParams] = None,
        default_workflow_name: str = "",
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """多肽提交共享实现：Excel -> 工作流参数 -> LIMS 创建订单。"""
        optional = dict(optional_params or {})
        workflow_name = str(required_params.get("workflow_name") or default_workflow_name or "").strip()
        if not workflow_name:
            raise PeptideWorkflowError("提交实验必须提供 workflow_name（工作流名称）")

        action_name = "submit_experiment" if not default_workflow_name else f"submit_{workflow_name}"
        with self._debug_call_session(action_name):
            sample_file, selected_sample_excel = self._resolve_submit_sample_file(required_params, optional)
            workflow = self._resolve_workflow_by_name(workflow_name)
            sub_workflow_id = workflow["sub_workflow_id"]
            step_data = self._workflow_step_data(sub_workflow_id)
            raw_parameters = self._extract_workflow_parameters(step_data)
            if not self._looks_like_step_parameter_map(raw_parameters):
                raise PeptideWorkflowError(f"工作流 {workflow_name} 未返回可用步骤参数，无法创建订单")

            param_values = self._build_param_values(
                raw_parameters,
                sample_file=sample_file,
                parameter_overrides=optional.get("parameter_values") or {},
            )
            order_code, order_name = self._build_order_identity(workflow_name=workflow_name, order_name=optional.get("order_name"))
            order_payload = [
                {
                    "orderCode": order_code,
                    "orderName": order_name,
                    "borderNumber": int(optional.get("border_number") or 1),
                    "workFlowId": self._require_uuid(sub_workflow_id, "workFlowId"),
                    "paramValues": self._normalize_param_values(param_values),
                }
            ]
            extend_properties = optional.get("extend_properties")
            if extend_properties not in (None, ""):
                order_payload[0]["extendProperties"] = str(extend_properties)

            create_order_result = self._create_order(order_payload)
            parsed_result = self._parse_result(create_order_result)
            order_ids = self._extract_order_ids_from_result(parsed_result)
            order_id = order_ids[0] if order_ids else ""
            self._created_order_ids.update(order_ids)
            self._created_order_codes.add(order_code)
            result_table = self._build_result_table(parsed_result)
            start_experiment_info = {
                "order_id": order_id,
                "order_ids": order_ids,
                "resultTable": result_table,
                "materials_loaded": False,
                "timeout_seconds": timeout_seconds,
                "assignee_user_ids": list(assignee_user_ids or []),
            }
            return {
                "success": bool(order_ids),
                "order_id": order_id,
                "order_ids": order_ids,
                "order_code": order_code,
                "order_name": order_name,
                "workflow": workflow,
                "sample_file": sample_file,
                "selected_sample_excel": selected_sample_excel,
                "payload": order_payload,
                "create_order_result": parsed_result,
                "resultTable": result_table,
                "start_experiment": start_experiment_info,
                "confirmation_message": "请按 resultTable 完成多肽物料装载后调用 start_experiment。",
            }

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
        goal_default={
            "materials_loaded": False,
            "timeout_seconds": 3600,
            "assignee_user_ids": [],
        },
        feedback_interval=300,
        description="请核对并装载多肽物料；确认后启动 Bioyond 调度器",
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
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """手动装载确认后启动 LIMS 调度器。"""
        del timeout_seconds, assignee_user_ids
        with self._debug_call_session("start_experiment"):
            resolved_order_ids = self._extract_order_ids(order_id=order_id, order_ids=order_ids, **kwargs)
            table_rows = resultTable.get("data") if isinstance(resultTable, dict) else []
            if table_rows and not bool(materials_loaded):
                raise RuntimeError("多肽物料装载未确认，拒绝启动调度器")
            result = self._run_scheduler_action("scheduler_start", "启动")
            result["order_ids"] = resolved_order_ids
            result["materials_loaded"] = bool(materials_loaded)
            result["resultTable"] = resultTable or {}
            return result

    @action(always_free=True, description="复位多肽实验前状态")
    def reset(
        self,
        reset_operations: Optional[List[str]] = None,
        dry_run: bool = True,
        order_id: str = "",
        location_id: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """按显式操作列表复位调度器、订单状态或库位。"""
        with self._debug_call_session("reset"):
            operations = self._normalize_reset_operations(reset_operations)
            planned = [{"operation": operation, "endpoint": self._reset_operation_endpoint(operation)} for operation in operations]
            result: Dict[str, Any] = {"dry_run": bool(dry_run), "planned_calls": planned, "executed_calls": [], "skipped_operations": []}
            if dry_run:
                return result
            rpc = self._require_hardware_interface()
            for operation in operations:
                if operation == "scheduler_reset":
                    code = rpc.scheduler_reset()
                    result["executed_calls"].append({"operation": operation, "result": {"code": code}})
                elif operation == "reset_order_status":
                    resolved_order_id = str(kwargs.get("reset_order_id") or order_id or kwargs.get("order_id") or "").strip()
                    if not resolved_order_id:
                        result["skipped_operations"].append({"operation": operation, "reason": "缺少 order_id/reset_order_id"})
                        continue
                    code = rpc.reset_order_status(resolved_order_id)
                    result["executed_calls"].append({"operation": operation, "order_id": resolved_order_id, "result": {"code": code}})
                elif operation == "reset_location":
                    resolved_location_id = str(kwargs.get("reset_location_id") or location_id or kwargs.get("location_id") or "").strip()
                    if not resolved_location_id:
                        result["skipped_operations"].append({"operation": operation, "reason": "缺少 location_id/reset_location_id"})
                        continue
                    code = rpc.reset_location(resolved_location_id)
                    result["executed_calls"].append({"operation": operation, "location_id": resolved_location_id, "result": {"code": code}})
                else:
                    raise ValueError(f"未知 reset operation: {operation}")
            return result

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
        operations = list(reset_operations or DEFAULT_RESET_OPERATIONS)
        normalized: List[str] = []
        for operation in operations:
            key = str(operation).strip()
            canonical = alias_map.get(key)
            if not canonical:
                raise ValueError(f"未知 reset operation: {operation}")
            if canonical not in normalized:
                normalized.append(canonical)
        return normalized

    @action(always_free=True, description="直接启动 Bioyond 多肽调度器")
    def scheduler_start(self, **kwargs: Any) -> Dict[str, Any]:
        """直接调用 Bioyond 调度器启动接口。"""
        del kwargs
        return self._run_scheduler_action("scheduler_start", "启动")

    @action(always_free=True, description="直接停止 Bioyond 多肽调度器")
    def scheduler_stop(self, **kwargs: Any) -> Dict[str, Any]:
        """直接调用 Bioyond 调度器停止接口。"""
        del kwargs
        return self._run_scheduler_action("scheduler_stop", "停止")

    @action(always_free=True, description="直接暂停 Bioyond 多肽调度器")
    def scheduler_pause(self, **kwargs: Any) -> Dict[str, Any]:
        """直接调用 Bioyond 调度器暂停接口。"""
        del kwargs
        return self._run_scheduler_action("scheduler_pause", "暂停")

    @action(always_free=True, description="直接继续 Bioyond 多肽调度器")
    def scheduler_continue(self, **kwargs: Any) -> Dict[str, Any]:
        """直接调用 Bioyond 调度器继续接口。"""
        del kwargs
        return self._run_scheduler_action("scheduler_continue", "继续")

    def _resolve_submit_sample_file(self, required_params: Dict[str, Any], optional_params: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        pattern = str(required_params.get("sample_excel_pattern") or "").strip()
        if not pattern:
            raise PeptideWorkflowError("提交实验必须提供 sample_excel_pattern（样品 Excel 文件名匹配模式）")
        if bool(optional_params.get("auto_upload_local_excel")):
            local_path = self._resolve_local_excel_path(optional_params, pattern)
            self._upload_sample_excel_file(local_path)
        selected = self._select_available_sample_excel(pattern)
        sample_file = str(selected.get("relativePath") or selected.get("filePath") or "").replace("/", "\\")
        if not sample_file:
            raise PeptideWorkflowError(f"样品 Excel 匹配 {pattern!r}，但返回记录缺少 relativePath/filePath")
        return sample_file, selected

    def _select_available_sample_excel(self, pattern: str) -> Dict[str, Any]:
        return self._find_sample_excel(self._list_sample_excels(name_filter=pattern.replace("*", "")), pattern)

    def _find_sample_excel(self, records: List[Dict[str, Any]], pattern: str) -> Dict[str, Any]:
        matched = [record for record in records if self._filename_matches_pattern(str(record.get("fileName") or ""), pattern)]
        if not matched:
            raise PeptideWorkflowError(f"未找到匹配 {pattern!r} 的样品 Excel，工作流已停止")
        if len(matched) > 1:
            names = ", ".join(str(item.get("fileName") or "") for item in matched)
            raise PeptideWorkflowError(f"找到多个匹配 {pattern!r} 的样品 Excel: {names}，请收窄匹配模式")
        return matched[0]

    def _list_sample_excels(self, name_filter: str = "", begin_date: Any = None, end_date: Any = None) -> List[Dict[str, Any]]:
        api_host = str(self.bioyond_config.get("api_host", "")).rstrip("/")
        api_key = str(self.bioyond_config.get("api_key", ""))
        timeout = int(self.bioyond_config.get("timeout", 30) or 30)
        if not api_host or not api_key:
            raise ValueError("缺少 api_host/api_key 配置")
        response = requests.post(
            f"{api_host}/api/lims/order/sample-info-excels",
            json={
                "apiKey": api_key,
                "requestTime": _utc_now_iso8601_ms(),
                "data": {"beginDate": begin_date, "endDate": end_date, "nameFilter": name_filter or None},
            },
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
        try:
            body: Any = response.json()
        except ValueError:
            body = {"raw_text": response.text}
        if response.status_code >= 400 or not isinstance(body, dict) or body.get("code") != 1:
            raise RuntimeError(f"样品 Excel 列表查询失败: status={response.status_code}, response={body}")
        data = body.get("data")
        return data if isinstance(data, list) else []

    def _upload_sample_excel_file(self, local_excel_path: str | Path, content_type: Optional[str] = None) -> Dict[str, Any]:
        api_host = str(self.bioyond_config.get("api_host", "")).rstrip("/")
        timeout = int(self.bioyond_config.get("timeout", 30) or 30)
        if not api_host:
            raise ValueError("缺少 api_host 配置")
        local_path = Path(str(local_excel_path).strip()).expanduser()
        if not local_path.is_absolute() and not str(local_excel_path).startswith("./"):
            raise ValueError("样品 Excel 文件路径请使用完整路径；相对路径必须以 ./ 开头")
        if not local_path.exists() or not local_path.is_file():
            raise FileNotFoundError(f"样品 Excel 文件不存在: {local_path}")
        resolved_content_type = content_type or mimetypes.guess_type(local_path.name)[0] or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        logger.info(f"上传多肽样品 Excel: {local_path.name}")
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
        result = {
            "endpoint": "/api/lims/order/up-load-sample-file",
            "http_status": response.status_code,
            "content_type": response.headers.get("content-type"),
            "request": {
                "file_path": str(local_path),
                "file_name": local_path.name,
                "field_name": "file",
                "file_content_type": resolved_content_type,
                "wrapped_envelope": False,
            },
            "response": body,
        }
        if response.status_code >= 400 or not isinstance(body, dict) or body.get("code") != 1:
            raise RuntimeError(f"样品 Excel 上传失败: {result}")
        file_info = body.get("data") if isinstance(body.get("data"), dict) else {}
        remote_file_path = str(file_info.get("filePath") or "")
        result.update(
            {
                "success": True,
                "lims_file_info": file_info,
                "relative_path": remote_file_path,
                "sample_file_parameter": remote_file_path.replace("/", "\\") if remote_file_path else "",
            }
        )
        return result

    def _resolve_local_excel_path(self, optional_params: Dict[str, Any], pattern: str) -> Path:
        explicit = str(optional_params.get("local_excel_path") or self.bioyond_config.get("default_local_excel_path") or "").strip()
        if explicit:
            return Path(explicit).expanduser()
        matches = sorted(Path.cwd().glob(pattern))
        if not matches:
            raise FileNotFoundError(f"本地未找到样品 Excel: {Path.cwd() / pattern}")
        return matches[-1]

    @staticmethod
    def _filename_matches_pattern(file_name: str, pattern: str) -> bool:
        if pattern == "*" or not pattern:
            return True
        if pattern.startswith("*") and pattern.endswith("*"):
            return pattern.strip("*") in file_name
        if pattern.startswith("*"):
            return file_name.endswith(pattern[1:])
        if pattern.endswith("*"):
            return file_name.startswith(pattern[:-1])
        return file_name == pattern

    def _resolve_workflow_by_name(self, workflow_name: str) -> Dict[str, Any]:
        rpc = self._require_hardware_interface()
        params = {"type": 0, "filter": workflow_name, "includeDetail": True}
        data = rpc.query_workflow(json.dumps(params, ensure_ascii=False))
        records = list(self._iter_dicts(data))
        exact_records = [record for record in records if self._record_name(record) == workflow_name]
        root = self._choose_workflow_record(exact_records) or self._choose_workflow_record(records)
        root_id = self._record_id(root) or self._workflow_id_from_config(workflow_name)
        sub = self._choose_sub_workflow_record(root, workflow_name) or root
        sub_id = self._record_id(sub) or root_id
        if not root_id or not sub_id:
            raise RuntimeError(f"无法解析工作流 {workflow_name}: {data}")
        return {"workflow_name": workflow_name, "root_workflow_id": str(root_id), "sub_workflow_id": str(sub_id), "raw": root or data}

    def _workflow_step_data(self, sub_workflow_id: str) -> Any:
        data = self._require_hardware_interface().workflow_step_query(self._require_uuid(sub_workflow_id, "sub_workflow_id"))
        if not data:
            logger.warning(f"LIMS 未返回子工作流参数: {sub_workflow_id}")
        return data or {}

    def _build_order_identity(self, *, workflow_name: str, order_name: Any = None) -> tuple[str, str]:
        suffix = datetime.now().strftime("%m%d%H%M%S")
        order_code = f"UL{suffix}"
        if order_name:
            return order_code, str(order_name)
        if "DAY2" in workflow_name.upper():
            label = "Day2"
        elif "DAY3" in workflow_name.upper():
            label = "Day3"
        elif "DAY4" in workflow_name.upper():
            label = "Day4"
        else:
            label = "Peptide"
        return order_code, f"UL-{label}-{suffix}"

    def _build_param_values(self, raw_parameters: Dict[str, Any], *, sample_file: str, parameter_overrides: Dict[str, Any]) -> Dict[str, Any]:
        param_values = self._filter_raw_parameters(raw_parameters)
        if not self._set_peptide_existing_parameter_value(param_values, PEPTIDE_SAMPLE_FILE_KEYS, sample_file):
            appended = self._append_peptide_raw_parameter_value(param_values, raw_parameters, PEPTIDE_SAMPLE_FILE_KEYS, sample_file)
            if appended is None:
                self._append_peptide_parameter_value(param_values, PEPTIDE_SAMPLE_FILE_KEYS, sample_file)
        for key, value in dict(parameter_overrides or {}).items():
            if not self._set_peptide_existing_parameter_value(param_values, [str(key)], value):
                appended = self._append_peptide_raw_parameter_value(param_values, raw_parameters, [str(key)], value)
                if appended is None:
                    self._append_peptide_parameter_value(param_values, [str(key)], value)
        return param_values

    def _filter_raw_parameters(self, raw_parameters: Dict[str, Any]) -> Dict[str, Any]:
        filtered: Dict[str, List[Dict[str, Any]]] = {}
        for step_id, modules in raw_parameters.items():
            if not self._looks_like_uuid_text(step_id):
                continue
            entries: List[Dict[str, Any]] = []
            for module in modules if isinstance(modules, list) else []:
                if not isinstance(module, dict):
                    continue
                module_m = module.get("m")
                module_n = module.get("n")
                parameter_list = module.get("parameterList") or module.get("ParameterList") or []
                for parameter in parameter_list if isinstance(parameter_list, list) else []:
                    if not isinstance(parameter, dict):
                        continue
                    if not self._peptide_raw_parameter_matches(parameter, {"TaskDisplayable": [1, "1", True]}):
                        continue
                    key = self._case_value(parameter, "key", "Key")
                    include_value, value = self._peptide_raw_parameter_output_value(parameter)
                    if not key or not include_value:
                        continue
                    entry: Dict[str, Any] = {"key": str(key), "value": self._peptide_raw_parameter_output_text(value)}
                    m_value = parameter.get("m", module_m)
                    n_value = parameter.get("n", module_n)
                    if m_value is not None:
                        entry["m"] = m_value
                    if n_value is not None:
                        entry["n"] = n_value
                    entries.append(entry)
            if entries:
                filtered[str(step_id)] = entries
        return filtered

    def _append_peptide_raw_parameter_value(self, param_values: Dict[str, Any], raw_parameters: Dict[str, Any], keys: Iterable[str], value: Any) -> Optional[str]:
        wanted = set(keys)
        for step_id, modules in raw_parameters.items():
            if not self._looks_like_uuid_text(step_id):
                continue
            for module in modules if isinstance(modules, list) else []:
                if not isinstance(module, dict):
                    continue
                parameter_list = module.get("parameterList") or module.get("ParameterList") or []
                for parameter in parameter_list if isinstance(parameter_list, list) else []:
                    if not isinstance(parameter, dict):
                        continue
                    key = self._case_value(parameter, "key", "Key")
                    if key not in wanted:
                        continue
                    entry: Dict[str, Any] = {"key": str(key), "value": self._peptide_raw_parameter_output_text(value)}
                    for axis in ("m", "n"):
                        axis_value = parameter.get(axis, module.get(axis))
                        if axis_value is not None:
                            entry[axis] = axis_value
                    param_values.setdefault(str(step_id), []).append(entry)
                    return str(key)
        return None

    def _append_peptide_parameter_value(self, param_values: Dict[str, Any], keys: Iterable[str], value: Any) -> str:
        for step_id, entries in param_values.items():
            if self._looks_like_uuid_text(step_id) and isinstance(entries, list):
                key = next(iter(keys))
                entries.append({"m": 0, "n": 0, "key": key, "value": self._peptide_raw_parameter_output_text(value)})
                return str(key)
        raise RuntimeError("LIMS 工作流参数未包含可追加的 UUID step bucket")

    def _set_peptide_existing_parameter_value(self, param_values: Any, keys: Iterable[str], value: Any) -> bool:
        wanted = set(keys)
        updated = False
        for entry in self._iter_peptide_parameter_entries(param_values):
            key = entry.get("key") if "key" in entry else entry.get("Key")
            if key not in wanted:
                continue
            value_key = "Value" if "Value" in entry else "value"
            display_key = "DisplayValue" if "DisplayValue" in entry else "displayValue"
            value_text = self._peptide_raw_parameter_output_text(value)
            entry[value_key] = value_text
            if display_key in entry:
                entry[display_key] = value_text
            updated = True
        return updated

    def _iter_peptide_parameter_entries(self, value: Any) -> Iterable[Dict[str, Any]]:
        if isinstance(value, dict):
            for child in value.values():
                yield from self._iter_peptide_parameter_entries(child)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and ("key" in item or "Key" in item):
                    yield item
                yield from self._iter_peptide_parameter_entries(item)

    @staticmethod
    def _case_value(obj: Dict[str, Any], *keys: str, missing: Any = None) -> Any:
        for key in keys:
            if key in obj:
                return obj.get(key)
        return missing

    @classmethod
    def _peptide_raw_parameter_matches(cls, parameter: Dict[str, Any], field_filters: Dict[str, Any]) -> bool:
        for field_name, expected in field_filters.items():
            actual = cls._case_value(parameter, str(field_name), str(field_name)[0].lower() + str(field_name)[1:], missing=None)
            if isinstance(expected, (list, tuple, set)):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False
        return True

    @classmethod
    def _peptide_raw_parameter_output_value(cls, parameter: Dict[str, Any]) -> tuple[bool, Any]:
        missing = object()
        for key in ("value", "Value", "displayValue", "DisplayValue", "defaultValue", "DefaultValue"):
            value = cls._case_value(parameter, key, missing=missing)
            if value is not missing and value not in (None, ""):
                return True, value
        return False, ""

    def _peptide_raw_parameter_output_text(self, value: Any) -> str:
        if isinstance(value, (dict, list)):
            return self._json_dumps_stable(value)
        return "" if value is None else str(value)

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

    def _extract_order_ids_from_result(self, value: Any) -> List[str]:
        ids: List[str] = []
        parsed = self._parse_result(value)

        def add(candidate: Any) -> None:
            if candidate and self._looks_like_order_id(candidate):
                ids.append(str(candidate))

        def visit(obj: Any) -> None:
            obj = self._parse_result(obj)
            if isinstance(obj, str):
                add(obj)
                return
            if isinstance(obj, list):
                for item in obj:
                    visit(item)
                return
            if not isinstance(obj, dict):
                return
            for key in ("orderId", "orderID", "order_id"):
                add(obj.get(key))
            for key in ("orderIds", "order_ids"):
                for item in self._as_list(obj.get(key)):
                    add(item)
            if obj.get("id") and any(obj.get(key) for key in ("orderCode", "orderName", "statusName", "status")):
                add(obj.get("id"))
            if len(obj) == 1:
                first_key = next(iter(obj))
                add(first_key)
            for item in obj.values():
                if isinstance(item, (dict, list)):
                    visit(item)

        visit(parsed)
        return list(dict.fromkeys(ids))

    @staticmethod
    def _looks_like_order_id(value: Any) -> bool:
        text = str(value)
        lowered = text.lower()
        return len(text) >= 8 and ("-" in text or lowered.startswith(("order", "bso", "3a")))

    @staticmethod
    def _looks_like_uuid_text(value: Any) -> bool:
        text = str(value)
        return len(text) == 36 and text.count("-") == 4

    def _looks_like_uuid(self, value: Any) -> bool:
        try:
            UUID(str(value))
            return True
        except (TypeError, ValueError, AttributeError):
            return False

    def _looks_like_step_parameter_map(self, value: Any) -> bool:
        parsed = self._json_loads_if_string(value)
        return isinstance(parsed, dict) and any(self._looks_like_uuid(key) and isinstance(item, list) for key, item in parsed.items())

    def _extract_workflow_parameters(self, step_data: Any) -> Any:
        parsed = self._json_loads_if_string(step_data)
        if self._looks_like_step_parameter_map(parsed):
            return parsed
        for key in ("paramValues", "stepParameters", "workflowParameter", "parameters", "data"):
            value = self._find_first_key(parsed, key)
            if value not in (None, {}, []):
                loaded = self._json_loads_if_string(value)
                if self._looks_like_step_parameter_map(loaded):
                    return loaded
        return parsed

    @staticmethod
    def _is_blank_parameter_value(value: Any) -> bool:
        return value is None or (isinstance(value, str) and value.strip() == "")

    def _create_order(self, order_payload: List[Dict[str, Any]]) -> Any:
        if not order_payload:
            raise RuntimeError("缺少 LIMS 订单负载，无法提交多肽实验")
        return self._require_hardware_interface().create_order(json.dumps(self._canonicalize_create_payload(order_payload), ensure_ascii=False))

    def _require_uuid(self, value: Any, field_name: str) -> str:
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError(f"LIMS 创建订单字段 {field_name} 必须是 UUID: {value!r}") from exc

    def _canonicalize_create_payload(self, order_payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        canonical_payload: List[Dict[str, Any]] = []
        for index, item in enumerate(order_payload):
            if not isinstance(item, dict):
                raise ValueError(f"LIMS 创建订单 payload[{index}] 必须是对象")
            canonical_item = copy.deepcopy(item)
            workflow_id = canonical_item.get("workFlowId") or canonical_item.pop("workflowId", None)
            canonical_item.pop("workflowId", None)
            canonical_item["workFlowId"] = self._require_uuid(workflow_id, "workFlowId")
            if "ExtendProperties" in canonical_item and "extendProperties" not in canonical_item:
                canonical_item["extendProperties"] = canonical_item.pop("ExtendProperties")
            else:
                canonical_item.pop("ExtendProperties", None)
            canonical_item["paramValues"] = self._normalize_param_values(canonical_item.get("paramValues"))
            canonical_payload.append(canonical_item)
        return canonical_payload

    def _normalize_param_values(self, param_values: Any) -> Dict[str, Any]:
        parsed = self._json_loads_if_string(param_values)
        if not isinstance(parsed, dict):
            return {}
        normalized: Dict[str, Any] = {}
        for step_id, entries in parsed.items():
            if not self._looks_like_uuid(step_id):
                continue
            normalized_entries = [entry for item in self._as_list(entries) if (entry := self._normalize_parameter_entry(item)) is not None]
            if normalized_entries:
                normalized[str(step_id)] = normalized_entries
        return normalized

    def _normalize_parameter_entry(self, entry: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(entry, dict):
            return None
        normalized = copy.deepcopy(entry)
        for source_key, target_key in _PARAMETER_KEY_ALIASES.items():
            if source_key in normalized and target_key not in normalized:
                normalized[target_key] = normalized.pop(source_key)
            elif source_key in normalized:
                normalized.pop(source_key)
        key = normalized.get("key")
        value = normalized.get("value")
        display_value = normalized.get("displayValue")
        if self._is_blank_parameter_value(value) and not self._is_blank_parameter_value(display_value):
            value = display_value
        if self._is_blank_parameter_value(key) or self._is_blank_parameter_value(value):
            return None
        sanitized: Dict[str, Any] = {"key": str(key), "value": self._peptide_raw_parameter_output_text(value)}
        for axis in ("m", "n"):
            axis_value = normalized.get(axis)
            if self._is_blank_parameter_value(axis_value):
                continue
            try:
                sanitized[axis] = int(axis_value)
            except (TypeError, ValueError):
                sanitized[axis] = axis_value
        return sanitized

    def _build_result_table(self, create_order_result: Any) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        for record in self._iter_dicts(create_order_result):
            material_name = record.get("materialName") or record.get("name") or record.get("materialTypeName")
            location = record.get("materialLocation") or record.get("locationName") or record.get("targetLocation") or record.get("materialTargetLocation")
            material_code = record.get("materialCode") or record.get("code") or record.get("materialBarCode")
            quantity = record.get("quantity") or record.get("useQuantity") or record.get("actualQuantity")
            if any(value not in (None, "") for value in (material_name, location, material_code, quantity)):
                rows.append(
                    {
                        "material_name": "" if material_name is None else str(material_name),
                        "material_code": "" if material_code is None else str(material_code),
                        "location": "" if location is None else str(location),
                        "quantity": "" if quantity is None else str(quantity),
                    }
                )
        return {
            "tableName": "多肽物料装载确认",
            "columns": [
                {"key": "material_name", "title": "物料"},
                {"key": "material_code", "title": "编号"},
                {"key": "location", "title": "库位"},
                {"key": "quantity", "title": "数量"},
            ],
            "data": rows,
        }

    def _run_scheduler_action(self, method_name: str, label: str) -> Dict[str, Any]:
        with self._debug_call_session(method_name):
            rpc = self._require_hardware_interface()
            before = self._safe_scheduler_status()
            code = getattr(rpc, method_name)()
            after = self._safe_scheduler_status()
            return {
                "success": code == 1,
                "operation": method_name,
                "operation_label": label,
                "code": code,
                "scheduler_status_before": before,
                "scheduler_status_after": after,
            }

    def _safe_scheduler_status(self) -> Dict[str, Any]:
        try:
            status = self._require_hardware_interface().scheduler_status()
            return status if isinstance(status, dict) else {}
        except Exception as exc:
            return {"error": str(exc)}

    def _require_hardware_interface(self):
        rpc = getattr(self, "hardware_interface", None)
        if rpc is None:
            raise RuntimeError("Bioyond RPC 客户端未初始化")
        return rpc

    @staticmethod
    def _extract_order_ids(order_id: str = "", order_ids: Optional[List[str]] = None, **kwargs: Any) -> List[str]:
        raw_order_ids = order_ids if order_ids is not None else kwargs.get("order_ids")
        if isinstance(raw_order_ids, list):
            resolved = [str(value) for value in raw_order_ids if value]
        elif isinstance(raw_order_ids, str) and raw_order_ids.strip():
            try:
                parsed = json.loads(raw_order_ids)
                resolved = [str(value) for value in parsed] if isinstance(parsed, list) else [raw_order_ids]
            except ValueError:
                resolved = [raw_order_ids]
        else:
            resolved = []
        if order_id:
            resolved.insert(0, str(order_id))
        return list(dict.fromkeys(resolved))

    @staticmethod
    def _reset_operation_endpoint(operation: str) -> str:
        return {
            "scheduler_reset": "/api/lims/scheduler/reset",
            "reset_order_status": "/api/lims/order/reset-order-status",
            "reset_location": "/api/lims/storage/reset-location",
        }.get(operation, "")

    def _iter_dicts(self, obj: Any) -> Iterable[Dict[str, Any]]:
        parsed = self._json_loads_if_string(obj)
        if isinstance(parsed, dict):
            yield parsed
            for value in parsed.values():
                yield from self._iter_dicts(value)
        elif isinstance(parsed, list):
            for item in parsed:
                yield from self._iter_dicts(item)

    def _record_name(self, record: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(record, dict):
            return None
        for key in ("name", "workflowName", "workFlowName", "displayName"):
            if record.get(key):
                return str(record[key])
        return None

    def _record_id(self, record: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(record, dict):
            return None
        for key in ("id", "workflowId", "workFlowId", "subWorkflowId", "subWorkFlowId"):
            if record.get(key):
                return str(record[key])
        return None

    def _choose_workflow_record(self, records: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        for record in records:
            if isinstance(record, dict) and ("subWorkflows" in record or "workflows" in record or "workflowId" in record or "id" in record):
                return record
        return None

    def _choose_sub_workflow_record(self, root: Optional[Dict[str, Any]], workflow_name: str) -> Optional[Dict[str, Any]]:
        if not isinstance(root, dict):
            return None
        candidates = []
        for key in ("subWorkflows", "subworkflows", "workflows", "Workflows"):
            candidates.extend([item for item in self._as_list(root.get(key)) if isinstance(item, dict)])
        for record in candidates:
            if self._record_name(record) == workflow_name:
                return record
        return candidates[0] if candidates else None

    def _workflow_id_from_config(self, workflow_name: str) -> Optional[str]:
        mappings = self.bioyond_config.get("workflow_mappings", {}) or {}
        if isinstance(mappings, dict):
            value = mappings.get(workflow_name)
            if value:
                return str(value)
        return None

    def _find_first_key(self, obj: Any, key: str) -> Any:
        parsed = self._json_loads_if_string(obj)
        if isinstance(parsed, dict):
            if key in parsed:
                return parsed.get(key)
            for value in parsed.values():
                found = self._find_first_key(value, key)
                if found is not None:
                    return found
        elif isinstance(parsed, list):
            for item in parsed:
                found = self._find_first_key(item, key)
                if found is not None:
                    return found
        return None

    def _json_loads_if_string(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return value
        try:
            return json.loads(text)
        except ValueError:
            return value

    def _json_dumps_stable(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _as_list(value: Any) -> List[Any]:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]


def main() -> int:
    """命令行入口：读取配置并拉取工作流列表。"""
    parser = argparse.ArgumentParser(description="Peptide Station 工作流列表拉取")
    parser.add_argument("config_path", help="JSON 配置文件路径")
    parser.add_argument("--workflow-type", type=int, default=0, help="工作流类型，默认 0")
    parser.add_argument("--filter", default="", help="工作流名称过滤字段")
    args = parser.parse_args()

    result = fetch_workflow_list(
        config_path=args.config_path,
        workflow_type=args.workflow_type,
        filter_text=args.filter,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))

    response_body = result.get("response", {})
    is_success = (
        result.get("http_status") == 200
        and isinstance(response_body, dict)
        and response_body.get("code") == 1
    )
    return 0 if is_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
