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
from typing import Annotated, Any, Dict, Iterable, List, Literal, Optional
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
    from unilabos.registry.placeholder_type import DeviceSlot, ResourceSlot
except Exception:  # pragma: no cover - 允许无完整依赖时导入轻量 helper
    class ResourceSlot:  # type: ignore[no-redef]
        pass

    class DeviceSlot(str):  # type: ignore[no-redef]
        pass

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

DEFAULT_READY_SIGNAL = "READY"
DEFAULT_RESET_OPERATIONS = ("scheduler_reset", "reset_order_status", "reset_location")
DAY2_PEPTIDE_WORKFLOW_NAME = "DAY2多肽定量"
PEPTIDE_SAMPLE_FILE_KEYS = ("SampleFile", "ExcelPath", "excelPath", "sampleFile")
PEPTIDE_METHOD_FILE_KEYS = (
    "NMPMethodFileName",
    "NMPMethodFile",
    "NMPFile",
    "CEMMethodFileName",
    "MethodFileName",
    "methodFileName",
)
PEPTIDE_SAMPLE_COUNT_KEYS = ("SampleCount", "sampleCount")


class PeptideWorkflowError(RuntimeError):
    """多肽工作流可恢复错误：当前动作失败并停止工作流，不退出 UniLabOS edge。"""


class SubmitExperimentRequiredParams(TypedDict):
    workflow_name: Annotated[str, Field(description="工作流名称（必填，不填写工作流 ID）")]
    sample_excel_pattern: Annotated[str, Field(description="样品 Excel 文件名匹配模式（必填）。")]


class SubmitExperimentDay2RequiredParams(TypedDict):
    sample_excel_pattern: Annotated[str, Field(description="Day2 样品 Excel 文件名匹配模式（必填）。")]


class SubmitExperimentOptionalParams(TypedDict, total=False):
    sample_file: Annotated[str, Field(description="LIMS 已上传样品 Excel 相对路径；主提交动作会按必填 pattern 重新选择。")]
    sample_count: Annotated[int, Field(description="样品数量（可选）；不填写时不由提交动作计算。")]
    local_excel_path: Annotated[str, Field(description="本地 Excel 文件路径；用于显式上传动作。")]
    cem_method_file_name: Annotated[str, Field(description="CEM 方法文件名，默认 1。")]
    order_name: Annotated[str, Field(description="订单名称（可选，自动生成）。")]
    auto_upload_local_excel: Annotated[bool, Field(default=False, description="保留给显式上传/诊断路径；主提交动作不自动上传。")]
    auto_confirm_placement: Annotated[bool, Field(default=True, description="是否自动确认 LIMS-only 物料摆放检查点。")]
    auto_confirm_checklist: Annotated[bool, Field(default=True, description="是否自动确认 LIMS-only 提交清单。")]
    verify_non_running: Annotated[bool, Field(default=True, description="提交前是否检查调度器未运行。")]
    border_number: Annotated[int, Field(default=1, description="LIMS 创建订单 borderNumber，默认 1。")]
    include_sample_count: Annotated[bool, Field(default=False, description="是否把可选 sample_count 写入参数；部分工作流实测默认不写入。")]
    include_cem_method_file_name: Annotated[bool, Field(default=False, description="是否把 CEM 方法文件名写入参数；Day3 实测默认不写入。")]
    extend_properties: Annotated[str, Field(description="LIMS extendProperties 字符串。")]


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
        self._day2_created_order_ids: set[str] = set()
        self._day2_created_order_codes: set[str] = set()

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

    @action(auto_prefix=True, description="上传 LIMS 样品 Excel 文件")
    def upload_lims_sample_excel(
        self,
        file_path: str,
        content_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """上传样品 Excel 到 LIMS。

        Args:
            file_path: 本地 Excel 文件路径；建议使用完整路径。如果使用相对路径，必须以 `./` 开头。
            content_type: 文件 MIME 类型；为空时根据文件名自动推断。
        """
        api_host = str(self.bioyond_config.get("api_host", "")).rstrip("/")
        timeout = int(self.bioyond_config.get("timeout", 30) or 30)
        if not api_host:
            raise ValueError("缺少 api_host 配置")

        file_path_text = str(file_path).strip()
        if not file_path_text:
            raise ValueError("样品 Excel 文件路径不能为空")
        if not Path(file_path_text).expanduser().is_absolute() and not file_path_text.startswith("./"):
            raise ValueError("样品 Excel 文件路径请使用完整路径；相对路径必须以 ./ 开头")

        local_path = Path(file_path_text).expanduser()
        if not local_path.exists():
            raise FileNotFoundError(f"样品 Excel 文件不存在: {local_path}")
        if not local_path.is_file():
            raise ValueError(f"样品 Excel 路径不是文件: {local_path}")

        resolved_content_type = (
            content_type
            or mimetypes.guess_type(local_path.name)[0]
            or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        url = f"{api_host}/api/lims/order/up-load-sample-file"
        logger.info(f"上传 LIMS 样品 Excel: {local_path.name}")

        with local_path.open("rb") as file:
            response = requests.post(
                url,
                files={
                    "file": (
                        local_path.name,
                        file,
                        resolved_content_type,
                    )
                },
                timeout=timeout,
            )

        try:
            response_body: Any = response.json()
        except ValueError:
            response_body = {"raw_text": response.text}

        result: Dict[str, Any] = {
            "endpoint": "/api/lims/order/up-load-sample-file",
            "http_status": response.status_code,
            "content_type": response.headers.get("content-type"),
            "request": {
                "file_path": str(local_path),
                "file_name": local_path.name,
                "field_name": "file",
                "file_content_type": resolved_content_type,
                "wrapped_lims_envelope": False,
            },
            "response": response_body,
        }

        if response.status_code >= 400:
            raise RuntimeError(f"LIMS 样品 Excel 上传 HTTP 失败: {result}")
        if not isinstance(response_body, dict) or response_body.get("code") != 1:
            raise RuntimeError(f"LIMS 样品 Excel 上传业务失败: {result}")

        file_info = response_body.get("data") if isinstance(response_body.get("data"), dict) else {}
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
                key="target_device",
                data_type="device_id",
                label="目标设备",
                data_key="target_device",
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
        required_params: SubmitExperimentRequiredParams,
        optional_params: Optional[SubmitExperimentOptionalParams] = None,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """通用多肽提交入口；具体 Day 工作流优先使用对应封装动作。

        Args:
            required_params: 必填参数组。
            optional_params: 可选参数组。
            timeout_seconds: 超时时间（秒，框架参数）。
            assignee_user_ids: 分配用户 ID 列表（框架参数）。
        """
        return self._submit_experiment_core(
            required_params=required_params,
            optional_params=optional_params,
            timeout_seconds=timeout_seconds,
            assignee_user_ids=assignee_user_ids,
            default_workflow_name="",
            **kwargs,
        )

    @action(
        always_free=True,
        description="提交多肽 Day2 定量实验到 Bioyond LIMS",
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
                key="target_device",
                data_type="device_id",
                label="目标设备",
                data_key="target_device",
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
    def submit_experiment_day2(
        self,
        required_params: SubmitExperimentDay2RequiredParams,
        optional_params: Optional[SubmitExperimentOptionalParams] = None,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """提交多肽 Day2 定量实验，工作流名称由站点封装。"""
        return self._submit_experiment_core(
            required_params={
                "workflow_name": DAY2_PEPTIDE_WORKFLOW_NAME,
                "sample_excel_pattern": str(required_params.get("sample_excel_pattern") or ""),
            },
            optional_params=optional_params,
            timeout_seconds=timeout_seconds,
            assignee_user_ids=assignee_user_ids,
            default_workflow_name=DAY2_PEPTIDE_WORKFLOW_NAME,
            **kwargs,
        )

    def _submit_experiment_core(
        self,
        *,
        required_params: Dict[str, Any],
        optional_params: Optional[SubmitExperimentOptionalParams] = None,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        default_workflow_name: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """多肽提交共享实现；公开动作按具体 Day/工作流做薄封装。"""
        del timeout_seconds, assignee_user_ids
        optional_params = optional_params or {}
        workflow_name = str(required_params.get("workflow_name") or default_workflow_name or "").strip()
        if not workflow_name:
            raise PeptideWorkflowError("提交实验必须提供 workflow_name（工作流名称），不能提供或依赖 workflow id")

        with self._debug_call_session("submit_experiment"):
            sample_file, selected_sample_excel = self._resolve_submit_sample_file(required_params, optional_params)
            sample_count = self._optional_submit_sample_count(optional_params)
            if not self._is_day2_workflow_name(workflow_name):
                submitted = self._submit_peptide_lims_experiment(
                    workflow_name=workflow_name,
                    sample_file=sample_file,
                    sample_count=sample_count,
                    optional_params=optional_params,
                    target_device=kwargs.get("unilabos_device_id") or kwargs.get("device_id") or "bioyond_peptide_station",
                )
                return submitted

            prepared = self.day2_prepare_submission(
                sample_file=sample_file,
                order_name=str(optional_params.get("order_name", "") or "") or None,
                sample_count=sample_count,
                cem_method_file_name=str(optional_params.get("cem_method_file_name", "") or "1"),
                workflow_name=workflow_name,
                auto_select_locations=True,
            )
            if bool(optional_params.get("auto_confirm_placement", True)):
                placement = self.day2_confirm_material_placement(prepared, confirm_signal=True)
                prepared = placement["prepared"]
            if bool(optional_params.get("auto_confirm_checklist", True)):
                checklist = self.day2_confirm_submission_checklist(
                    prepared,
                    checklist={
                        "fridge_low_temperature_storage_closed": True,
                        "lab_clean": True,
                        "sample_information_verified": True,
                        "device_status_acceptable": True,
                    },
                    confirm_signal=True,
                )
                prepared = checklist["prepared"]

            submitted = self.day2_submit_experiment(
                prepared,
                verify_non_running=bool(optional_params.get("verify_non_running", True)),
            )
            order_id = str(submitted.get("order_id") or "")
            submitted.update(
                {
                    "success": bool(submitted.get("submitted")),
                    "order_ids": [order_id] if order_id else [],
                    "target_device": kwargs.get("unilabos_device_id") or kwargs.get("device_id") or "bioyond_peptide_station",
                    "sample_file": sample_file,
                    "prepared": prepared,
                    "selected_sample_excel": selected_sample_excel,
                }
            )
            return submitted

    def _submit_peptide_lims_experiment(
        self,
        *,
        workflow_name: str,
        sample_file: str,
        sample_count: Optional[int],
        optional_params: Dict[str, Any],
        target_device: str,
    ) -> Dict[str, Any]:
        scheduler_status = None
        if bool(optional_params.get("verify_non_running", True)):
            scheduler_status = self._safe_scheduler_status()
            if self._scheduler_status_is_running(scheduler_status):
                raise RuntimeError(f"调度器正在运行，拒绝提交多肽实验: {scheduler_status}")

        workflow = self._resolve_workflow_by_name(workflow_name)
        sub_workflow_id = workflow["sub_workflow_id"]
        step_data = self._workflow_step_data(sub_workflow_id, {})
        raw_parameters = self._extract_workflow_parameters(step_data)
        if not isinstance(raw_parameters, dict):
            raise RuntimeError(f"LIMS 工作流参数不是 step map: {type(raw_parameters).__name__}")

        param_values = self._build_peptide_lims_param_values(
            raw_parameters,
            sample_file=sample_file,
            sample_count=sample_count if bool(optional_params.get("include_sample_count", False)) else None,
            cem_method_file_name=(
                str(optional_params.get("cem_method_file_name", "") or "1")
                if bool(optional_params.get("include_cem_method_file_name", False))
                else None
            ),
        )
        order_code, generated_name = self._build_peptide_lims_order_identity(
            workflow_name=workflow_name,
            order_name=str(optional_params.get("order_name", "") or "") or None,
        )
        order_payload: List[Dict[str, Any]] = [
            {
                "orderCode": order_code,
                "orderName": generated_name,
                "borderNumber": int(optional_params.get("border_number") or 1),
                "workFlowId": self._require_day2_lims_uuid(sub_workflow_id, "workFlowId"),
                "paramValues": self._normalize_day2_lims_param_values(param_values),
            }
        ]
        extend_properties = optional_params.get("extend_properties")
        if extend_properties not in (None, ""):
            order_payload[0]["extendProperties"] = str(extend_properties)

        response = self._create_day2_lims_order(order_payload)
        order_id = str(response.get("order_id") or "")
        self._day2_created_order_ids.add(order_id) if order_id else None
        self._day2_created_order_codes.add(order_code)
        return {
            "success": bool(order_id),
            "submitted": bool(order_id),
            "order_id": order_id,
            "order_ids": [order_id] if order_id else [],
            "order_code": order_code,
            "order_name": generated_name,
            "workflow_id": sub_workflow_id,
            "target_device": target_device,
            "sample_file": sample_file,
            "sample_count": sample_count,
            "selected_sample_excel": getattr(self, "_last_selected_sample_excel", None),
            "lims_endpoint": "/api/lims/order/order",
            "lims_order_payload": order_payload,
            "lims_response": response,
            "scheduler_status": scheduler_status,
            "param_values_mode": "task_displayable_editable_plus_sample_file",
        }

    @staticmethod
    def _is_day2_workflow_name(workflow_name: str) -> bool:
        return "DAY2" in str(workflow_name).upper()

    def _build_peptide_lims_order_identity(
        self,
        *,
        workflow_name: str,
        order_name: Optional[str] = None,
    ) -> tuple[str, str]:
        suffix = datetime.now().strftime("%m%d%H%M%S")
        order_code = f"UL{suffix}"
        if order_name:
            return order_code, order_name
        workflow_text = str(workflow_name).upper()
        label = "Day3" if "DAY3" in workflow_text else "Peptide"
        return order_code, f"UL-{label}-{suffix}"

    def _build_peptide_lims_param_values(
        self,
        raw_parameters: Dict[str, Any],
        *,
        sample_file: str,
        sample_count: Optional[int],
        cem_method_file_name: Optional[str],
    ) -> Dict[str, Any]:
        param_values = self._filter_peptide_lims_raw_parameters(
            raw_parameters,
            field_filters={"TaskDisplayable": [1, "1"], "Type": "Editable"},
        )
        if not self._set_peptide_existing_parameter_value(param_values, PEPTIDE_SAMPLE_FILE_KEYS, sample_file):
            appended = self._append_peptide_raw_parameter_value(
                param_values,
                raw_parameters,
                PEPTIDE_SAMPLE_FILE_KEYS,
                sample_file,
            )
            if appended is None:
                self._append_peptide_parameter_value(param_values, PEPTIDE_SAMPLE_FILE_KEYS, sample_file)
        if cem_method_file_name is not None:
            if not self._set_peptide_existing_parameter_value(param_values, PEPTIDE_METHOD_FILE_KEYS, cem_method_file_name):
                self._append_peptide_raw_parameter_value(param_values, raw_parameters, PEPTIDE_METHOD_FILE_KEYS, cem_method_file_name)
        if sample_count is not None:
            if not self._set_peptide_existing_parameter_value(param_values, PEPTIDE_SAMPLE_COUNT_KEYS, sample_count):
                self._append_peptide_raw_parameter_value(param_values, raw_parameters, PEPTIDE_SAMPLE_COUNT_KEYS, sample_count)
        return param_values

    def _filter_peptide_lims_raw_parameters(
        self,
        raw_parameters: Dict[str, Any],
        *,
        field_filters: Dict[str, Any],
    ) -> Dict[str, Any]:
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
                    if not self._peptide_raw_parameter_matches(parameter, field_filters):
                        continue
                    key = self._case_value(parameter, "key", "Key")
                    include_value, value = self._peptide_raw_parameter_output_value(parameter)
                    if not key or not include_value:
                        continue
                    entry: Dict[str, Any] = {"key": str(key), "value": str(value)}
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

    def _append_peptide_raw_parameter_value(
        self,
        param_values: Dict[str, Any],
        raw_parameters: Dict[str, Any],
        keys: Iterable[str],
        value: Any,
    ) -> Optional[str]:
        wanted = set(keys)
        for step_id, modules in raw_parameters.items():
            if not self._looks_like_uuid_text(step_id):
                continue
            for module in modules if isinstance(modules, list) else []:
                if not isinstance(module, dict):
                    continue
                module_m = module.get("m")
                module_n = module.get("n")
                parameter_list = module.get("parameterList") or module.get("ParameterList") or []
                for parameter in parameter_list if isinstance(parameter_list, list) else []:
                    if not isinstance(parameter, dict):
                        continue
                    key = self._case_value(parameter, "key", "Key")
                    if key not in wanted:
                        continue
                    entry: Dict[str, Any] = {"key": str(key), "value": "" if value is None else str(value)}
                    m_value = parameter.get("m", module_m)
                    n_value = parameter.get("n", module_n)
                    if m_value is not None:
                        entry["m"] = m_value
                    if n_value is not None:
                        entry["n"] = n_value
                    param_values.setdefault(str(step_id), []).append(entry)
                    return str(key)
        return None

    def _append_peptide_parameter_value(
        self,
        param_values: Dict[str, Any],
        keys: Iterable[str],
        value: Any,
    ) -> str:
        for step_id, entries in param_values.items():
            if self._looks_like_uuid_text(step_id) and isinstance(entries, list):
                key = next(iter(keys))
                entries.append({"m": 0, "n": 0, "key": key, "value": "" if value is None else str(value)})
                return key
        raise RuntimeError("LIMS 工作流参数未包含可追加的 UUID step bucket")

    def _set_peptide_existing_parameter_value(self, param_values: Any, keys: Iterable[str], value: Any) -> bool:
        wanted = set(keys)
        for entry in self._iter_peptide_parameter_entries(param_values):
            key = entry.get("key") if "key" in entry else entry.get("Key")
            if key not in wanted:
                continue
            value_key = "Value" if "Value" in entry else "value"
            display_key = "DisplayValue" if "DisplayValue" in entry else "displayValue"
            value_text = "" if value is None else str(value)
            entry[value_key] = value_text
            if display_key in entry:
                entry[display_key] = value_text
            return True
        return False

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
        value = cls._case_value(parameter, "value", "Value", missing=missing)
        if value is missing or value is None or value == "":
            display_value = cls._case_value(parameter, "displayValue", "DisplayValue", missing=missing)
            if display_value is not missing:
                value = display_value
        if value is missing or value is None or value == "":
            return False, ""
        return True, value

    @staticmethod
    def _looks_like_uuid_text(value: Any) -> bool:
        text = str(value)
        return len(text) == 36 and text.count("-") == 4

    # ==================== Day2 LIMS-only submission nodes ====================

    # ==================== Node 1: prepare ====================

    def day2_prepare_submission(
        self,
        sample_file: str,
        order_name: Optional[str] = None,
        sample_count: Optional[int] = None,
        cem_method_file_name: str = "1",
        workflow_name: str = "DAY2多肽定量",
        auto_select_locations: bool = True,
    ) -> Dict[str, Any]:
        """准备 Day 2 提交，仅构建 LIMS 订单负载，不访问 Project/cache-order。"""
        report = self._new_day2_execution_report()
        order_code, generated_name = self._build_day2_lims_order_identity(order_name=order_name)
        workflow = self._resolve_workflow_by_name(workflow_name)
        root_workflow_id = workflow["root_workflow_id"]
        sub_workflow_id = workflow["sub_workflow_id"]
        step_data = self._workflow_step_data(sub_workflow_id, report)
        raw_step_material_records = self._extract_step_material_records(step_data)
        step_material_records_json = self._json_dumps_stable(raw_step_material_records)
        inferred_sample_count = sample_count or 1
        sample_excel = self._verify_lims_sample_excel(sample_file)
        raw_parameters = self._extract_workflow_parameters(step_data)
        parameter_step_id = self._resolve_day2_lims_parameter_step_id(raw_parameters, step_data, sub_workflow_id)
        param_values = self._build_day2_lims_param_values(
            parameter_step_id=parameter_step_id,
            raw_parameters=raw_parameters,
            sample_file=sample_file,
            sample_count=inferred_sample_count,
            cem_method_file_name=cem_method_file_name,
        )
        parameters_json = self._json_dumps_stable(param_values)
        lims_order_payload = self._build_day2_lims_order_payload(
            order_code=order_code,
            order_name=generated_name,
            workflow_id=sub_workflow_id,
            param_values=param_values,
            workflow_name=workflow_name,
            sample_file=sample_file,
            sample_count=inferred_sample_count,
            cem_method_file_name=cem_method_file_name,
        )
        suggested_locs = {"sample": [], "reagent": [], "consumable": []}
        if auto_select_locations:
            logger.info("Day2 LIMS-only 准备阶段跳过 Project 库位自动选择")

        prepared = {
            "cache_order_id": None,
            "order_id": None,
            "order_code": order_code,
            "order_name": generated_name,
            "workflow_name": workflow_name,
            "root_workflow_id": root_workflow_id,
            "sub_workflow_id": sub_workflow_id,
            "current_step": 1,
            "sample_file": sample_file,
            "sample_count": inferred_sample_count,
            "cem_method_file_name": cem_method_file_name,
            "parameter_step_id": parameter_step_id,
            "execution_report": report,
            "parameters": parameters_json,
            "param_values": param_values,
            "step_material_records": step_material_records_json,
            "suggested_locs": suggested_locs,
            "lims_order_payload": lims_order_payload,
            "lims_endpoint": "/api/lims/order/order",
            "day2_node": "prepare_submission",
            "node_state": "prepared_lims_payload",
            "placement_finished": False,
            "bioyond_placement_applied": False,
            "checklist_confirmed": False,
        }
        auto_selected_placements = self._build_day2_location_payloads(prepared, suggested_locs)

        submit_info = {
            "lims_endpoint": "/api/lims/order/order",
            "order_code": prepared.get("order_code"),
            "order_name": prepared.get("order_name"),
            "workflow_name": workflow_name,
            "root_workflow_id": root_workflow_id,
            "sub_workflow_id": sub_workflow_id,
            "sample_summary": {
                "sample_file": sample_file,
                "sample_count": inferred_sample_count,
                "sample_container_count": None,
                "verify_result": "LIMS 已确认样品 Excel 文件存在",
                "lims_sample_excel": sample_excel,
            },
            "material_summary": self._summarize_material_records(raw_step_material_records),
            "checklist_text": {
                "fridge_low_temperature_storage_closed": "低温/冰箱存储已关闭",
                "lab_clean": "实验区域已清洁",
                "sample_information_verified": "样品信息已核对",
                "device_status_acceptable": "设备状态满足提交要求",
            },
        }

        prepared.update(
            {
                "auto_selected_placements": auto_selected_placements,
                "submit_info": submit_info,
                "raw": {
                    "workflow": workflow.get("raw"),
                    "workflow_step_data": step_data,
                    "step_material_records": raw_step_material_records,
                    "sample_excel": sample_excel,
                },
            }
        )
        self._remember_day2_created_order(prepared)
        return prepared

    # ==================== Node 2: material placement ====================

    def day2_confirm_material_placement(
        self,
        prepared: Dict[str, Any],
        confirmed_placements: Optional[Dict[str, Any]] = None,
        confirm_signal: bool = False,
        apply_to_bioyond: bool = False,
    ) -> Dict[str, Any]:
        """记录物料摆放确认；LIMS-only 正常路径不写入 Project 库位。"""
        prepared_copy = copy.deepcopy(prepared)
        placements = confirmed_placements or prepared_copy.get("auto_selected_placements") or prepared_copy.get("suggested_locs", {})
        placement_payloads = self._build_day2_location_payloads(prepared_copy, placements)
        presentation_log = {
            "node": "day2_confirm_material_placement",
            "confirm_signal": bool(confirm_signal),
            "apply_to_bioyond": bool(apply_to_bioyond),
            "lims_only": True,
            "external_mutation": False,
            "suggested_locs": prepared_copy.get("suggested_locs", {}),
            "placement_payloads": placement_payloads,
        }
        if apply_to_bioyond:
            presentation_log["note"] = "LIMS-only 正常路径忽略 apply_to_bioyond，不调用 Project 库位接口"
        self._log_presentation_payload("day2_confirm_material_placement", presentation_log)

        prepared_copy["node2_presentation_log"] = presentation_log
        result = {
            "placement_finished": False,
            "presentation_log": presentation_log,
            "prepared": prepared_copy,
            "placement_summary": self._summarize_location_payloads(placement_payloads),
        }
        if not confirm_signal:
            return result

        prepared_copy["placement_finished"] = True
        prepared_copy["bioyond_placement_applied"] = False
        prepared_copy["day2_node"] = "material_placement_confirmed"
        prepared_copy["node_state"] = "placement_confirmed_log_only"
        prepared_copy["placement_payloads"] = placement_payloads
        placement_summary = self._summarize_location_payloads(placement_payloads)
        self._refresh_day2_lims_order_payload(prepared_copy)

        result.update(
            {
                "placement_finished": True,
                "prepared": prepared_copy,
                "placement_summary": placement_summary,
            }
        )
        return result

    def day2_apply_material_locations(
        self,
        prepared: Dict[str, Any],
        placements: Dict[str, Any],
        *,
        allow_project_cache: bool = False,
    ) -> Dict[str, Any]:
        """旧版 Project/cache-order 路径：按前端顺序写入样品、试剂和耗材摆放。"""
        if not allow_project_cache:
            raise RuntimeError("LIMS-only 模式禁止调用 Project/cache 库位写入；如需旧路径请显式 allow_project_cache=True")
        if not prepared.get("cache_order_id"):
            raise RuntimeError("缺少 cache_order_id，无法使用 Project/cache 库位写入路径")
        prepared_copy = copy.deepcopy(prepared)
        payloads = self._build_day2_location_payloads(prepared_copy, placements)
        summary: Dict[str, Any] = {"applied": [], "responses": {}}

        sample_items = payloads.get("sample", [])
        if sample_items:
            summary["responses"]["sample_inbound"] = self._material_and_in_by_locations(sample_items)
            sample_review = self._pre_distribute_sample(prepared_copy["cache_order_id"])
            summary["responses"]["pre_distribute_sample"] = sample_review
            cache_order = self._save_day2_cache_order_step(
                prepared_copy,
                4,
                sampleNum=prepared_copy.get("sample_count") or len(sample_items),
            )
            self._update_prepared_from_cache_order(prepared_copy, cache_order)
            prepared_copy.setdefault("raw", {})["pre_distribute_sample"] = sample_review
            summary["applied"].append("sample")

        cache_order = self._save_day2_cache_order_step(prepared_copy, 5)
        self._update_prepared_from_cache_order(prepared_copy, cache_order)

        reagent_items = payloads.get("reagent", [])
        if reagent_items:
            summary["responses"]["reagent_inbound"] = self._material_and_in_by_locations(reagent_items)
            summary["applied"].append("reagent")
        cache_order = self._save_day2_cache_order_step(prepared_copy, 6)
        self._update_prepared_from_cache_order(prepared_copy, cache_order)

        consumable_items = payloads.get("consumable", [])
        if consumable_items:
            summary["responses"]["consumable_inbound"] = self._material_and_in_by_locations(consumable_items)
            summary["applied"].append("consumable")
        cache_order = self._save_day2_cache_order_step(prepared_copy, 7)
        self._update_prepared_from_cache_order(prepared_copy, cache_order)

        prepared_copy["placement_finished"] = True
        prepared_copy["bioyond_placement_applied"] = True
        prepared_copy["day2_node"] = "material_locations_applied_project_cache"
        prepared_copy["node_state"] = "project_cache_locations_applied"
        prepared_copy["placement_payloads"] = payloads
        summary.update(self._summarize_location_payloads(payloads))
        return {"placement_finished": True, "prepared": prepared_copy, "placement_summary": summary}

    # ==================== Node 3: checklist ====================

    def day2_confirm_submission_checklist(
        self,
        prepared: Dict[str, Any],
        checklist: Optional[Dict[str, bool]] = None,
        confirm_signal: bool = False,
        require_all: bool = True,
    ) -> Dict[str, Any]:
        """记录最终复核信息；确认阶段只更新本地 LIMS 提交状态。"""
        prepared_copy = copy.deepcopy(prepared)
        checklist_values = self._day2_checklist_values(checklist)
        final_review = self._day2_final_review(prepared_copy)
        presentation_log = {
            "node": "day2_confirm_submission_checklist",
            "confirm_signal": bool(confirm_signal),
            "submit_info": prepared_copy.get("submit_info", {}),
            "sample_summary": prepared_copy.get("submit_info", {}).get("sample_summary", {}),
            "material_summary": prepared_copy.get("submit_info", {}).get("material_summary", {}),
            "final_review": final_review,
            "checklist": checklist_values,
        }
        self._log_presentation_payload("day2_confirm_submission_checklist", presentation_log)

        prepared_copy["node3_presentation_log"] = presentation_log
        result = {
            "checklist_confirmed": False,
            "presentation_log": presentation_log,
            "prepared": prepared_copy,
            "final_review": final_review,
        }
        if not confirm_signal:
            return result

        if require_all and not all(checklist_values.values()):
            missing = [key for key, value in checklist_values.items() if not value]
            raise ValueError(f"Day2 提交检查未完成: {missing}")

        prepared_copy["checklist_confirmed"] = True
        prepared_copy["checklist"] = checklist_values
        prepared_copy["current_step"] = 3
        prepared_copy["day2_node"] = "submission_checklist_confirmed"
        prepared_copy["node_state"] = "ready_for_lims_submit"
        self._refresh_day2_lims_order_payload(prepared_copy)
        result.update({"checklist_confirmed": True, "prepared": prepared_copy})
        return result

    # ==================== Node 4: submit only ====================

    def day2_submit_experiment(
        self,
        prepared: Dict[str, Any],
        signatures: str = "",
        verify_non_running: bool = True,
    ) -> Dict[str, Any]:
        """通过 LIMS 创建订单，不调用 Project 启动接口或调度器启动。"""
        if not prepared.get("placement_finished") and not prepared.get("diagnostic_override_submit_guard"):
            raise RuntimeError("Day2 实验提交前需要先完成 placement/物料摆放确认")
        if not prepared.get("checklist_confirmed") and not prepared.get("diagnostic_override_submit_guard"):
            raise RuntimeError("Day2 实验提交前需要先完成最终检查确认")

        scheduler_status = None
        if verify_non_running:
            scheduler_status = self._safe_scheduler_status()
            if self._scheduler_status_is_running(scheduler_status):
                raise RuntimeError(f"调度器正在运行，拒绝提交 Day2 实验: {scheduler_status}")

        prepared_copy = copy.deepcopy(prepared)
        if signatures:
            prepared_copy["signatures"] = signatures
        self._refresh_day2_lims_order_payload(prepared_copy)
        lims_order_payload = prepared_copy.get("lims_order_payload") or []
        response = self._create_day2_lims_order(lims_order_payload)
        order_id = response.get("order_id")
        prepared_copy["order_id"] = order_id
        self._remember_day2_created_order(prepared_copy)
        submitted = bool(order_id)
        return {
            "submitted": submitted,
            "cache_order_id": prepared.get("cache_order_id"),
            "order_id": order_id,
            "order_code": prepared.get("order_code"),
            "order_name": prepared.get("order_name"),
            "workflow_id": prepared.get("sub_workflow_id"),
            "lims_endpoint": "/api/lims/order/order",
            "lims_order_payload": lims_order_payload,
            "lims_response": response,
            "scheduler_status": scheduler_status,
            "api_fallbacks": prepared.get("execution_report", {}).get("api_fallbacks", []),
        }

    # ==================== Cleanup/reset utilities ====================

    def day2_reset_experiment_creation(
        self,
        order_code: Optional[str] = None,
        order_id: Optional[str] = None,
        preintake_ids: Optional[List[str]] = None,
        material_ids: Optional[List[str]] = None,
        *,
        dry_run: bool = True,
        allow_cancel: bool = False,
        allow_take_out: bool = False,
        allow_scheduler_reset: bool = False,
        allow_storage_reset: bool = False,
        allow_order_status_reset: bool = False,
        allow_browser_routes: bool = False,
        allow_external_order: bool = False,
    ) -> Dict[str, Any]:
        """为现场测试显式清理创建实验产生的状态。"""
        report = self._new_day2_execution_report()
        discovered_preintake_ids = list(preintake_ids or [])
        discovered_material_ids = list(material_ids or [])
        discovery: Dict[str, Any] = {}

        if order_id and allow_browser_routes:
            try:
                discovery["order_basic_info"] = self._browser_order_basic_info(order_id)
                discovered_preintake_ids.extend(self._extract_preintake_ids(discovery["order_basic_info"]))
            except Exception as exc:
                discovery["order_basic_info_error"] = str(exc)
        elif order_id:
            discovery["order_basic_info_skipped"] = "未启用 allow_browser_routes，跳过 Project 订单详情读取"

        for preintake_id in list(dict.fromkeys(discovered_preintake_ids)):
            if not allow_browser_routes:
                discovery.setdefault("preintake_used_sample_record_skipped", {})[preintake_id] = (
                    "未启用 allow_browser_routes，跳过 Project 已用样品读取"
                )
                continue
            try:
                records = self._preintake_used_sample_record(preintake_id)
                discovery.setdefault("preintake_used_sample_record", {})[preintake_id] = records
                discovered_material_ids.extend(self._extract_material_ids(records))
            except Exception as exc:
                discovery.setdefault("preintake_used_sample_record_error", {})[preintake_id] = str(exc)

        if discovered_preintake_ids and not dry_run and allow_browser_routes:
            for material_type_mode in (0, 1, 2):
                try:
                    used = self._used_material(discovered_preintake_ids, material_type_mode)
                    discovery.setdefault("used_material", {})[str(material_type_mode)] = used
                    discovered_material_ids.extend(self._extract_material_ids(used))
                except Exception as exc:
                    discovery.setdefault("used_material_error", {})[str(material_type_mode)] = str(exc)
        elif discovered_preintake_ids and not dry_run:
            discovery["used_material_skipped"] = "未启用 allow_browser_routes，跳过 Project used-material 读取"

        discovered_preintake_ids = list(dict.fromkeys(discovered_preintake_ids))
        discovered_material_ids = list(dict.fromkeys(discovered_material_ids))
        planned_calls = self._day2_cleanup_planned_calls(
            order_code=order_code,
            order_id=order_id,
            preintake_ids=discovered_preintake_ids,
            material_ids=discovered_material_ids,
            allow_cancel=allow_cancel,
            allow_take_out=allow_take_out,
            allow_scheduler_reset=allow_scheduler_reset,
            allow_storage_reset=allow_storage_reset,
            allow_order_status_reset=allow_order_status_reset,
            allow_browser_routes=allow_browser_routes,
        )

        result: Dict[str, Any] = {
            "dry_run": dry_run,
            "order_code": order_code,
            "order_id": order_id,
            "preintake_ids": discovered_preintake_ids,
            "material_ids": discovered_material_ids,
            "discovery": discovery,
            "planned_calls": planned_calls,
            "executed_calls": [],
            "api_fallbacks": report["api_fallbacks"],
        }
        if dry_run:
            return result

        self._assert_day2_cleanup_order_is_owned(
            order_code=order_code,
            order_id=order_id,
            allow_external_order=allow_external_order,
        )

        if allow_cancel:
            cancel_result = self._try_lims_cancel(order_id)
            result["executed_calls"].append(
                {"operation": "lims_cancel_experiment", "endpoint": "/api/lims/order/cancel-experiment", "result": cancel_result}
            )
            if cancel_result.get("code") != 1 and order_code and allow_browser_routes:
                browser_result = self._browser_cancel_experiment_by_order_codes([order_code])
                self._record_api_fallback(
                    report,
                    "cancel_experiment",
                    "/api/lims/order/cancel-experiment",
                    "/api/order/order/cancel-experiment",
                    "LIMS 取消未成功或缺少可用 order_id",
                    "按订单号取消已提交实验",
                    browser_result,
                )
                result["executed_calls"].append(
                    {"operation": "browser_cancel_experiment", "endpoint": "/api/order/order/cancel-experiment", "result": browser_result}
                )

        if allow_take_out:
            take_out_result = self._lims_take_out(order_id or "", discovered_preintake_ids, discovered_material_ids)
            result["executed_calls"].append(
                {"operation": "lims_take_out", "endpoint": "/api/lims/order/take-out", "result": take_out_result}
            )
            if self._response_code(take_out_result) != 1 and discovered_material_ids and allow_browser_routes:
                browser_result = self._browser_take_out_sample(discovered_material_ids)
                self._record_api_fallback(
                    report,
                    "take_out_sample",
                    "/api/lims/order/take-out",
                    "/api/order/order/take-out-sample",
                    "LIMS take-out 未成功释放 Day2 物料",
                    "按 materialIds 标记样品/物料取出",
                    browser_result,
                )
                result["executed_calls"].append(
                    {"operation": "browser_take_out_sample", "endpoint": "/api/order/order/take-out-sample", "result": browser_result}
                )

        if allow_scheduler_reset:
            reset_code = self._safe_scheduler_reset()
            result["executed_calls"].append(
                {"operation": "lims_scheduler_reset", "endpoint": "/api/lims/scheduler/reset", "result": {"code": reset_code}}
            )
            if reset_code != 1 and allow_browser_routes:
                browser_result = self._browser_scheduler_reset()
                self._record_api_fallback(
                    report,
                    "scheduler_reset",
                    "/api/lims/scheduler/reset",
                    "/api/scheduler/scheduler/reset",
                    "LIMS 调度器复位未成功",
                    "复位调度器状态",
                    browser_result,
                )
                result["executed_calls"].append(
                    {"operation": "browser_scheduler_reset", "endpoint": "/api/scheduler/scheduler/reset", "result": browser_result}
                )

        if allow_storage_reset and allow_browser_routes:
            result["executed_calls"].append(
                {"operation": "browser_reset_location", "endpoint": "/api/storage/location/reset-location", "result": self._browser_reset_location()}
            )

        if allow_order_status_reset and allow_browser_routes:
            result["executed_calls"].append(
                {"operation": "browser_reset_order_status", "endpoint": "/api/order/order/reset-status", "result": self._browser_reset_order_status()}
            )

        result["api_fallbacks"] = report["api_fallbacks"]
        return result

    def day2_reset_before_create(
        self,
        *,
        dry_run: bool = True,
        allow_scheduler_reset: bool = False,
        allow_storage_reset: bool = False,
        allow_order_status_reset: bool = False,
        allow_browser_routes: bool = False,
    ) -> Dict[str, Any]:
        """在创建实验前显式复位模拟器/订单状态。"""
        report = self._new_day2_execution_report()
        planned_calls = []
        if allow_scheduler_reset:
            planned_calls.append({"operation": "scheduler_reset", "endpoint": "/api/lims/scheduler/reset", "fallback": "/api/scheduler/scheduler/reset"})
        if allow_storage_reset:
            planned_calls.append({"operation": "storage_reset", "endpoint": "/api/storage/location/reset-location", "requires_browser_route": True})
        if allow_order_status_reset:
            planned_calls.append({"operation": "order_status_reset", "endpoint": "/api/order/order/reset-status", "requires_browser_route": True})
        result = {"dry_run": dry_run, "planned_calls": planned_calls, "executed_calls": [], "api_fallbacks": report["api_fallbacks"]}
        if dry_run:
            return result

        if allow_scheduler_reset:
            reset_code = self._safe_scheduler_reset()
            result["executed_calls"].append({"operation": "lims_scheduler_reset", "result": {"code": reset_code}})
            if reset_code != 1 and allow_browser_routes:
                browser_result = self._browser_scheduler_reset()
                self._record_api_fallback(
                    report,
                    "scheduler_reset_before_create",
                    "/api/lims/scheduler/reset",
                    "/api/scheduler/scheduler/reset",
                    "LIMS 调度器复位未成功",
                    "创建实验前复位调度器",
                    browser_result,
                )
                result["executed_calls"].append({"operation": "browser_scheduler_reset", "result": browser_result})
        if allow_storage_reset:
            if not allow_browser_routes:
                raise PermissionError("storage reset 需要 allow_browser_routes=True")
            result["executed_calls"].append({"operation": "browser_reset_location", "result": self._browser_reset_location()})
        if allow_order_status_reset:
            if not allow_browser_routes:
                raise PermissionError("order status reset 需要 allow_browser_routes=True")
            result["executed_calls"].append({"operation": "browser_reset_order_status", "result": self._browser_reset_order_status()})
        result["api_fallbacks"] = report["api_fallbacks"]
        return result

    # ==================== LIMS-only order helpers ====================

    def _build_day2_lims_order_code(self) -> str:
        prefix = str(self._day2_config_value("day2_order_code_prefix", "UL"))
        timestamp_format = str(self._day2_config_value("day2_order_code_time_format", "%m%d%H%M%S"))
        return f"{prefix}{datetime.now():{timestamp_format}}"

    def _build_day2_lims_order_name(self) -> str:
        prefix = str(self._day2_config_value("day2_order_name_prefix", "UL-Day2"))
        timestamp_format = str(self._day2_config_value("day2_order_name_time_format", "%m%d%H%M%S"))
        return f"{prefix}-{datetime.now():{timestamp_format}}"

    def _build_day2_lims_order_identity(self, order_name: Optional[str] = None) -> tuple[str, str]:
        """用同一个时间戳生成 LIMS 订单编号和默认名称。"""
        created_at = datetime.now()
        timestamp_format = str(self._day2_config_value("day2_order_code_time_format", "%m%d%H%M%S"))
        suffix = f"{created_at:{timestamp_format}}"
        code_prefix = str(self._day2_config_value("day2_order_code_prefix", "UL"))
        name_prefix = str(self._day2_config_value("day2_order_name_prefix", "UL-Day2"))
        return f"{code_prefix}{suffix}", order_name or f"{name_prefix}-{suffix}"

    def _build_day2_lims_param_values(
        self,
        *,
        parameter_step_id: str,
        raw_parameters: Any = None,
        sample_file: str,
        sample_count: Optional[int],
        cem_method_file_name: str,
    ) -> Dict[str, Any]:
        """按 LIMS APICreateOrderDto 契约构建 paramValues。"""
        template = self._json_loads_if_string(raw_parameters)
        if self._looks_like_lims_step_parameter_map(template):
            template_map = {
                str(key): copy.deepcopy(value)
                for key, value in template.items()
                if self._looks_like_uuid(key) and isinstance(value, list)
            }
            param_values = self._flatten_lims_step_parameter_map(template_map)
            self._set_parameter_value(param_values, "CEMMethodFileName", str(cem_method_file_name))
            self._set_parameter_value(param_values, "SampleCount", str(sample_count or 1))
            self._set_parameter_value(param_values, "SampleFile", sample_file)
            if self._day2_lims_param_values_mode() == "sample_only":
                param_values = self._filter_day2_lims_input_parameters(param_values)
            return param_values

        if not parameter_step_id or not self._looks_like_uuid(parameter_step_id):
            raise RuntimeError("缺少 Day2 参数 step_id，无法构建 LIMS paramValues")
        return {
            str(parameter_step_id): [
                {"m": 0, "n": 0, "key": "CEMMethodFileName", "value": str(cem_method_file_name)},
                {"m": 0, "n": 0, "key": "SampleCount", "value": str(sample_count or 1)},
                {"m": 0, "n": 0, "key": "SampleFile", "value": sample_file},
            ]
        }

    def _day2_lims_param_values_mode(self) -> str:
        """返回 LIMS 创建订单参数模式，默认保留完整工作流参数。"""
        configured = (
            self._day2_config_value("day2_lims_param_values_mode")
            or self._day2_config_value("day2_param_values_mode")
            or "full"
        )
        mode = str(configured).strip().lower().replace("-", "_")
        if mode in {"sample_only", "samples_only", "required_only", "minimal"}:
            return "sample_only"
        return "full"

    def _resolve_day2_lims_parameter_step_id(self, raw_parameters: Any, step_data: Any, sub_workflow_id: str) -> str:
        """优先从 LIMS 步骤参数中取 step_id，避免误用根工作流 id。"""
        configured = self._day2_config_value("day2_parameter_step_id") or self._day2_config_value("parameter_step_id")
        for value in (
            self._first_param_values_step_id(raw_parameters),
            self._first_param_values_step_id(self._find_first_key(step_data, "paramValues")),
            self._first_param_values_step_id(self._find_first_key(step_data, "stepParameters")),
            configured,
        ):
            if value:
                return str(value)
        raise RuntimeError(f"未能从 LIMS 参数中解析 Day2 step_id，子工作流 {sub_workflow_id} 无法构建 paramValues")

    def _first_param_values_step_id(self, value: Any) -> Optional[str]:
        parsed = self._json_loads_if_string(value)
        if not isinstance(parsed, dict):
            return None
        for key, item in parsed.items():
            if isinstance(item, list) and self._looks_like_uuid(key):
                return str(key)
            if isinstance(item, dict):
                nested = self._first_param_values_step_id(item)
                if nested:
                    return nested
        return None

    def _build_day2_lims_order_payload(
        self,
        *,
        order_code: str,
        order_name: str,
        workflow_id: str,
        param_values: Dict[str, Any],
        workflow_name: str,
        sample_file: str,
        sample_count: int,
        cem_method_file_name: str,
    ) -> List[Dict[str, Any]]:
        normalized_param_values = copy.deepcopy(param_values)
        self._set_parameter_value(normalized_param_values, "CEMMethodFileName", cem_method_file_name)
        self._set_parameter_value(normalized_param_values, "SampleCount", sample_count)
        self._set_parameter_value(normalized_param_values, "SampleFile", sample_file)
        normalized_param_values = self._normalize_day2_lims_param_values(normalized_param_values)
        workflow_uuid = self._require_day2_lims_uuid(workflow_id, "workFlowId")
        order_item = {
            "orderCode": order_code,
            "orderName": order_name,
            "borderNumber": int(self._day2_config_value("day2_border_number", 1) or 1),
            "workFlowId": workflow_uuid,
            "paramValues": normalized_param_values,
        }
        extend_properties = self._day2_lims_extend_properties(
            {
                "workflow_name": workflow_name,
                "sample_file": sample_file,
                "sample_count": sample_count,
                "cem_method_file_name": cem_method_file_name,
                "lims_only": True,
                "source": "UniLabOS",
            }
        )
        if extend_properties is not None:
            order_item["extendProperties"] = extend_properties
        return [order_item]

    def _day2_lims_create_dto_style(self) -> str:
        """返回 LIMS 创建订单 DTO 字段风格，默认使用 OpenAPI 字段名。"""
        configured = self._day2_config_value("day2_lims_create_dto_style") or "openapi"
        style = str(configured).strip().lower().replace("-", "_")
        if style in {"manual", "legacy", "source", "dispensing"}:
            return "manual"
        return "openapi"

    def _day2_lims_parameter_entry_style(self) -> str:
        """返回 paramValues 内层参数字段风格。"""
        configured = self._day2_config_value("day2_lims_parameter_entry_style")
        if configured is None:
            return "openapi"
        style = str(configured).strip().lower().replace("-", "_")
        if style in {"manual", "legacy", "source", "dispensing", "upper", "uppercase"}:
            return "manual"
        return "openapi"

    def _day2_lims_extend_properties(self, prepared: Dict[str, Any]) -> Optional[str]:
        if not self._day2_config_has_key("day2_extend_properties"):
            return None
        configured = self._day2_config_value("day2_extend_properties", None)
        if configured is None:
            return None
        if isinstance(configured, str):
            return configured
        if not isinstance(configured, dict):
            return None
        properties = copy.deepcopy(configured)
        properties.update(
            {
                "source": prepared.get("source", "UniLabOS"),
                "limsOnly": bool(prepared.get("lims_only", True)),
                "workflowName": prepared.get("workflow_name"),
                "sampleFile": prepared.get("sample_file"),
                "sampleCount": prepared.get("sample_count"),
                "cemMethodFileName": prepared.get("cem_method_file_name"),
                "placementFinished": bool(prepared.get("placement_finished", False)),
                "checklistConfirmed": bool(prepared.get("checklist_confirmed", False)),
            }
        )
        if prepared.get("placement_payloads") is not None:
            properties["placementPayloads"] = prepared.get("placement_payloads")
        if prepared.get("checklist") is not None:
            properties["checklist"] = prepared.get("checklist")
        if prepared.get("signatures"):
            properties["signatures"] = prepared.get("signatures")
        return self._json_dumps_stable({key: value for key, value in properties.items() if value is not None})

    def _refresh_day2_lims_order_payload(self, prepared: Dict[str, Any]) -> None:
        payload = copy.deepcopy(prepared.get("lims_order_payload") or [])
        if not payload:
            workflow_id = prepared.get("sub_workflow_id")
            if not workflow_id:
                raise RuntimeError("缺少 sub_workflow_id，无法构建 LIMS 订单负载")
            order_code = prepared.get("order_code")
            order_name = prepared.get("order_name")
            if not order_code or not order_name:
                generated_code, generated_name = self._build_day2_lims_order_identity(order_name=order_name)
                order_code = order_code or generated_code
                order_name = order_name or generated_name
                prepared["order_code"] = order_code
                prepared["order_name"] = order_name
            payload = self._build_day2_lims_order_payload(
                order_code=order_code,
                order_name=order_name,
                workflow_id=workflow_id,
                param_values=prepared.get("param_values")
                or self._build_day2_lims_param_values(
                    parameter_step_id=prepared.get("parameter_step_id") or "",
                    sample_file=prepared.get("sample_file") or "",
                    sample_count=int(prepared.get("sample_count") or 1),
                    cem_method_file_name=prepared.get("cem_method_file_name") or "1",
                ),
                workflow_name=prepared.get("workflow_name") or "",
                sample_file=prepared.get("sample_file") or "",
                sample_count=int(prepared.get("sample_count") or 1),
                cem_method_file_name=prepared.get("cem_method_file_name") or "",
            )
        for item in payload:
            if not isinstance(item, dict):
                continue
            workflow_id = item.get("workFlowId") or item.pop("workflowId", None) or prepared.get("sub_workflow_id")
            item.pop("workflowId", None)
            item["workFlowId"] = self._require_day2_lims_uuid(workflow_id, "sub_workflow_id")
            extend_properties = self._day2_lims_extend_properties(prepared)
            item.pop("ExtendProperties", None)
            if extend_properties is None:
                item.pop("extendProperties", None)
            else:
                item["extendProperties"] = extend_properties
            if "paramValues" not in item:
                item["paramValues"] = prepared.get("param_values") or self._build_day2_lims_param_values(
                    parameter_step_id=prepared.get("parameter_step_id") or "",
                    sample_file=prepared.get("sample_file") or "",
                    sample_count=int(prepared.get("sample_count") or 1),
                    cem_method_file_name=prepared.get("cem_method_file_name") or "1",
                )
            param_values = copy.deepcopy(item.get("paramValues"))
            self._set_parameter_value(param_values, "CEMMethodFileName", prepared.get("cem_method_file_name") or "1")
            self._set_parameter_value(param_values, "SampleCount", int(prepared.get("sample_count") or 1))
            self._set_parameter_value(param_values, "SampleFile", prepared.get("sample_file") or "")
            item["paramValues"] = self._normalize_day2_lims_param_values(param_values)
            prepared["param_values"] = copy.deepcopy(item["paramValues"])
        prepared["lims_order_payload"] = payload

    def _create_day2_lims_order(self, order_payload: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not order_payload:
            raise RuntimeError("缺少 LIMS 订单负载，无法提交 Day2 实验")
        create_payload = self._canonicalize_day2_lims_create_payload(order_payload)
        rpc = getattr(self, "hardware_interface", None)
        json_str = json.dumps(create_payload, ensure_ascii=False)
        if rpc is not None and hasattr(rpc, "create_order"):
            result = rpc.create_order(json_str)
            return self._normalize_day2_lims_order_response(result, "hardware_interface.create_order")
        result = self._lims_post("/api/lims/order/order", create_payload)
        return self._normalize_day2_lims_order_response(result, "_lims_post")

    def _require_day2_lims_uuid(self, value: Any, field_name: str) -> str:
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError(f"Day2 LIMS 创建订单字段 {field_name} 必须是 UUID: {value!r}") from exc

    def _canonicalize_day2_lims_create_payload(self, order_payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        canonical_payload: List[Dict[str, Any]] = []
        for index, item in enumerate(order_payload):
            if not isinstance(item, dict):
                raise ValueError(f"Day2 LIMS 创建订单 payload[{index}] 必须是对象")
            canonical_item = copy.deepcopy(item)
            workflow_id = canonical_item.get("workFlowId") or canonical_item.pop("workflowId", None)
            canonical_item.pop("workflowId", None)
            canonical_item["workFlowId"] = self._require_day2_lims_uuid(workflow_id, "workFlowId")
            if "ExtendProperties" in canonical_item and "extendProperties" not in canonical_item:
                canonical_item["extendProperties"] = canonical_item.pop("ExtendProperties")
            else:
                canonical_item.pop("ExtendProperties", None)
            if "paramValues" in canonical_item:
                canonical_item["paramValues"] = self._normalize_day2_lims_param_values(canonical_item.get("paramValues"))
            canonical_payload.append(canonical_item)
        return canonical_payload

    def _normalize_day2_lims_order_response(self, result: Any, source: str) -> Dict[str, Any]:
        parsed = self._parse_lims_result(result)
        if isinstance(parsed, dict) and "code" in parsed:
            if parsed.get("code") != 1:
                raise RuntimeError(f"Day2 LIMS 创建订单失败: {parsed}")
            data = parsed.get("data")
            code = parsed.get("code")
        else:
            data = parsed
            code = 1 if parsed not in (None, {}, [], "") else 0
        order_id = self._extract_lims_order_id(data)
        if not order_id:
            logger.warning(f"Day2 LIMS 创建订单未解析到 order_id: {parsed}")
        return {
            "code": code,
            "source": source,
            "endpoint": "/api/lims/order/order",
            "data": data,
            "order_id": order_id,
            "raw": parsed,
        }

    def _parse_lims_result(self, result: Any) -> Any:
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

    def _extract_lims_order_id(self, value: Any) -> Optional[str]:
        parsed = self._parse_lims_result(value)
        if isinstance(parsed, str):
            return parsed if parsed else None
        if isinstance(parsed, list):
            for item in parsed:
                found = self._extract_lims_order_id(item)
                if found:
                    return found
            return None
        if isinstance(parsed, dict):
            for key in ("orderId", "orderID", "id", "order_id"):
                if parsed.get(key):
                    return str(parsed[key])
            if "data" in parsed:
                found = self._extract_lims_order_id(parsed.get("data"))
                if found:
                    return found
            if len(parsed) == 1:
                first_key = next(iter(parsed))
                if first_key and self._looks_like_lims_order_id(first_key):
                    return str(first_key)
            for item in parsed.values():
                found = self._extract_lims_order_id(item)
                if found:
                    return found
        return None

    def _looks_like_lims_order_id(self, value: Any) -> bool:
        text = str(value)
        lowered = text.lower()
        return len(text) >= 8 and ("-" in text or lowered.startswith(("order", "bso")))

    # ==================== Legacy Project/cache-order HTTP helpers ====================

    def _project_url(self, path: str) -> str:
        host = self._day2_config_value("project_api_host") or self._day2_config_value("api_host")
        if not host:
            raise ValueError("缺少 api_host/project_api_host 配置")
        return f"{str(host).rstrip('/')}/{path.lstrip('/')}"

    def _project_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self._project_request("GET", path, params=params)

    def _project_post(self, path: str, body: Any) -> Any:
        return self._project_request("POST", path, body=body)

    def _project_put(self, path: str, body: Any) -> Any:
        return self._project_request("PUT", path, body=body)

    def _project_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        body: Any = None,
    ) -> Any:
        url = self._project_url(path)
        headers = self._project_headers()
        timeout = int(self._day2_config_value("timeout", 30) or 30)
        transport = getattr(self, "_project_http_request", None)
        if callable(transport):
            return transport(method, url, params=params, json=body, headers=headers, timeout=timeout)
        response = requests.request(method, url, params=params, json=body, headers=headers, timeout=timeout)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {"raw_text": response.text}

    def _project_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        auth = self._day2_config_value("project_authorization") or self._day2_config_value("authorization")
        token = (
            self._day2_config_value("project_access_token")
            or self._day2_config_value("access_token")
            or self._day2_config_value("bearer_token")
            or self._day2_config_value("token")
        )
        if auth:
            headers["Authorization"] = str(auth)
        elif token:
            token_text = str(token)
            headers["Authorization"] = token_text if token_text.lower().startswith("bearer ") else f"Bearer {token_text}"
        cookie = self._day2_config_value("project_cookie") or self._day2_config_value("cookie")
        if cookie:
            headers["Cookie"] = str(cookie)
        extra_headers = self._day2_config_value("project_headers", {}) or {}
        if isinstance(extra_headers, dict):
            headers.update({str(key): str(value) for key, value in extra_headers.items()})
        return headers

    def _require_project_success(self, response: Any, path: str) -> Any:
        if isinstance(response, dict) and "code" in response:
            if response.get("code") != 1:
                raise RuntimeError(f"{path} 调用失败: {response}")
            return response.get("data")
        return response

    # ==================== LIMS workflow and legacy Project/cache-order helpers ====================

    def _get_order_code(self, report: Optional[Dict[str, Any]] = None) -> str:
        """旧版 Project/cache-order 辅助：读取浏览器订单号。"""
        response = self._project_get("/api/order/order/order-code")
        code = self._require_project_success(response, "/api/order/order/order-code")
        if isinstance(code, dict):
            code = code.get("orderCode") or code.get("code")
        if not code:
            raise RuntimeError(f"未能读取订单号: {response}")
        if report is not None:
            self._record_api_fallback(
                report,
                "read_order_code",
                "/api/lims/order/order",
                "/api/order/order/order-code",
                "订单号接口只在浏览器轨迹中观察到；这是只读预取",
                "读取新订单号，无状态变更",
                response if isinstance(response, dict) else {"data": response},
            )
        return str(code)

    def _save_cache_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """旧版 Project/cache-order 辅助：保存缓存订单。"""
        response = self._project_post("/api/order/commonly-order/save-cache-order", payload)
        data = self._require_project_success(response, "/api/order/commonly-order/save-cache-order")
        if not isinstance(data, dict):
            raise RuntimeError(f"save-cache-order 返回格式异常: {response}")
        return data

    def _build_cache_order_payload(
        self,
        prepared: Optional[Dict[str, Any]] = None,
        current_step: Optional[int] = None,
        **updates: Any,
    ) -> Dict[str, Any]:
        """旧版 Project/cache-order 辅助：构建缓存订单负载。"""
        prepared = prepared or {}
        payload = {
            "id": prepared.get("cache_order_id"),
            "orderCode": prepared.get("order_code"),
            "orderName": prepared.get("order_name"),
            "workflowName": prepared.get("workflow_name"),
            "workflowId": prepared.get("sub_workflow_id"),
            "workflowParameter": prepared.get("workflow_parameter"),
            "parameters": prepared.get("parameters"),
            "currentStep": prepared.get("current_step", current_step),
            "concurrencyStamp": prepared.get("concurrency_stamp"),
            "sampleNum": prepared.get("sample_count"),
            "commonlyOrderId": prepared.get("commonly_order_id"),
            "stepMaterialRecords": prepared.get("step_material_records"),
            "materialParameter": prepared.get("material_parameter"),
            "extraProperties": prepared.get("extraProperties", {}),
        }
        if current_step is not None:
            payload["currentStep"] = current_step
        payload.update(updates)
        return {key: value for key, value in payload.items() if value is not None}

    def _save_day2_cache_order_step(self, prepared: Dict[str, Any], current_step: int, **updates: Any) -> Dict[str, Any]:
        """旧版 Project/cache-order 辅助：推进缓存订单步骤。"""
        payload = self._build_cache_order_payload(prepared, current_step, **updates)
        return self._save_cache_order(payload)

    def _prepared_from_cache_order(
        self,
        cache_order: Dict[str, Any],
        workflow_name: str,
        root_workflow_id: str,
        sub_workflow_id: str,
        sample_file: str,
        sample_count: Optional[int],
        execution_report: Dict[str, Any],
    ) -> Dict[str, Any]:
        """旧版 Project/cache-order 辅助：由缓存订单生成本地状态。"""
        prepared = {
            "cache_order_id": cache_order.get("id"),
            "order_code": cache_order.get("orderCode"),
            "order_name": cache_order.get("orderName"),
            "workflow_name": workflow_name,
            "root_workflow_id": root_workflow_id,
            "sub_workflow_id": sub_workflow_id,
            "concurrency_stamp": cache_order.get("concurrencyStamp"),
            "current_step": cache_order.get("currentStep"),
            "sample_file": sample_file,
            "sample_count": sample_count,
            "execution_report": execution_report,
        }
        if not prepared["cache_order_id"]:
            raise RuntimeError(f"cache-order 未返回 id: {cache_order}")
        return prepared

    def _update_prepared_from_cache_order(self, prepared: Dict[str, Any], cache_order: Dict[str, Any]) -> None:
        """旧版 Project/cache-order 辅助：同步缓存订单返回值。"""
        prepared["cache_order_id"] = cache_order.get("id", prepared.get("cache_order_id"))
        prepared["order_code"] = cache_order.get("orderCode", prepared.get("order_code"))
        prepared["order_name"] = cache_order.get("orderName", prepared.get("order_name"))
        prepared["workflow_name"] = cache_order.get("workflowName", prepared.get("workflow_name"))
        prepared["sub_workflow_id"] = cache_order.get("workflowId", prepared.get("sub_workflow_id"))
        prepared["concurrency_stamp"] = cache_order.get("concurrencyStamp", prepared.get("concurrency_stamp"))
        prepared["current_step"] = cache_order.get("currentStep", prepared.get("current_step"))
        if cache_order.get("parameters") is not None:
            prepared["parameters"] = cache_order.get("parameters")
        if cache_order.get("stepMaterialRecords") is not None:
            prepared["step_material_records"] = cache_order.get("stepMaterialRecords")
        if cache_order.get("sampleNum") is not None:
            prepared["sample_count"] = cache_order.get("sampleNum")
        if isinstance(prepared.get("raw"), dict) and isinstance(prepared["raw"].get("cache_order"), dict):
            prepared["raw"]["cache_order"].update(cache_order)
        self._remember_day2_created_order(prepared)

    def _resolve_workflow_by_name(self, workflow_name: str) -> Dict[str, str]:
        params = {"type": 0, "filter": workflow_name, "includeDetail": True}
        data: Any = {}
        rpc = getattr(self, "hardware_interface", None)
        if rpc is not None and hasattr(rpc, "query_workflow"):
            data = rpc.query_workflow(json.dumps(params, ensure_ascii=False))
        if not data:
            data = self._lims_workflow_list(params)

        records = list(self._iter_dicts(data))
        exact_records = [record for record in records if self._record_name(record) == workflow_name]
        root = self._choose_workflow_record(exact_records) or self._choose_workflow_record(records)
        root_id = self._record_id(root) or self._workflow_id_from_config(workflow_name)
        sub = self._choose_sub_workflow_record(root, workflow_name) or root
        sub_id = self._record_id(sub) or root_id
        if not root_id or not sub_id:
            raise RuntimeError(f"无法解析工作流 {workflow_name}: {data}")
        return {
            "workflow_name": workflow_name,
            "root_workflow_id": str(root_id),
            "sub_workflow_id": str(sub_id),
            "raw": root or data,
        }

    def _lims_workflow_list(self, params: Dict[str, Any]) -> Any:
        rpc = getattr(self, "hardware_interface", None)
        if rpc is not None and hasattr(rpc, "post"):
            host = getattr(rpc, "host", None) or self._day2_config_value("api_host")
            api_key = getattr(rpc, "api_key", None) or self._day2_config_value("api_key")
            if host and api_key:
                response = rpc.post(
                    url=f"{str(host).rstrip('/')}/api/lims/workflow/work-flow-list",
                    params={"apiKey": api_key, "requestTime": _utc_now_iso8601_ms(), "data": params},
                )
                if response and response.get("code") == 1:
                    return response.get("data")
        return {}

    def _workflow_step_data(self, sub_workflow_id: str, report: Dict[str, Any]) -> Any:
        rpc = getattr(self, "hardware_interface", None)
        if rpc is not None and hasattr(rpc, "workflow_step_query"):
            data = rpc.workflow_step_query(sub_workflow_id)
            if data:
                return data
        logger.warning(f"LIMS 未返回 Day2 子工作流参数，继续使用空参数: {sub_workflow_id}")
        return {}

    def _get_browser_step_parameters(self, sub_workflow_id: str, report: Optional[Dict[str, Any]] = None) -> Any:
        path = f"/api/workflow/sub-workflow/{sub_workflow_id}/step-parameters"
        response = self._project_get(path)
        data = self._require_project_success(response, path)
        if report is not None:
            self._record_api_fallback(
                report,
                "read_step_parameters",
                "/api/lims/workflow/sub-workflow-step-parameters",
                path,
                "LIMS step 参数未返回可用数据",
                "读取子工作流参数，无状态变更",
                response if isinstance(response, dict) else {"data": response},
            )
        return data

    def _get_browser_step_material_records(self, sub_workflow_id: str, report: Optional[Dict[str, Any]] = None) -> Any:
        path = f"/api/workflow/sub-workflow/{sub_workflow_id}/step-material-records"
        response = self._project_get(path)
        data = self._require_project_success(response, path)
        if report is not None:
            self._record_api_fallback(
                report,
                "read_step_material_records",
                "/api/order/commonly-order/handle-step-material-parameter/{workFlowId}",
                path,
                "本地 schema 未提供直接读取物料记录的 LIMS 等价能力",
                "读取步骤物料记录，无状态变更",
                response if isinstance(response, dict) else {"data": response},
            )
        return data

    # ==================== Parameters and legacy Project sample helpers ====================

    def _handle_or_normalize_workflow_parameters(
        self,
        raw_parameters: Any,
        *,
        sample_file: str,
        sample_count: Optional[int],
        cem_method_file_name: str,
    ) -> str:
        normalized = self._normalize_parameter_keys(self._json_loads_if_string(raw_parameters))
        self._set_parameter_value(normalized, "CEMMethodFileName", cem_method_file_name)
        if sample_count is not None:
            self._set_parameter_value(normalized, "SampleCount", sample_count)
        self._set_parameter_value(normalized, "SampleFile", sample_file)
        return self._json_dumps_stable(normalized)

    def _normalize_parameter_keys(self, obj: Any) -> Any:
        if isinstance(obj, list):
            return [self._normalize_parameter_keys(item) for item in obj]
        if isinstance(obj, dict):
            normalized: Dict[str, Any] = {}
            for key, value in obj.items():
                next_key = _PARAMETER_KEY_ALIASES.get(key, key[:1].lower() + key[1:] if key[:1].isupper() else key)
                normalized[next_key] = self._normalize_parameter_keys(value)
            return normalized
        return obj

    def _hydrate_lims_parameter_defaults(self, parameters: Any) -> None:
        def visit(obj: Any, parent_m: Any = None, parent_n: Any = None) -> None:
            if isinstance(obj, list):
                for item in obj:
                    visit(item, parent_m, parent_n)
                return
            if not isinstance(obj, dict):
                return

            current_m = obj.get("m", parent_m)
            current_n = obj.get("n", parent_n)
            parameter_list = obj.get("parameterList")
            if isinstance(parameter_list, list):
                for parameter in parameter_list:
                    if not isinstance(parameter, dict):
                        continue
                    value_key = "Value" if "Value" in parameter else "value"
                    display_key = "DisplayValue" if "DisplayValue" in parameter else "displayValue"
                    value = parameter.get(value_key)
                    if value in (None, "") and parameter.get(display_key) not in (None, ""):
                        parameter[value_key] = str(parameter.get(display_key))
                    elif value is not None:
                        parameter[value_key] = str(value)
                    if current_m is not None and parameter.get("m") is None:
                        parameter["m"] = current_m
                    if current_n is not None and parameter.get("n") is None:
                        parameter["n"] = current_n
                return

            for item in obj.values():
                visit(item, current_m, current_n)

        visit(parameters)

    def _flatten_lims_step_parameter_map(self, parameters: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        flattened: Dict[str, List[Dict[str, Any]]] = {}
        required_day2_keys = {"CEMMethodFileName", "SampleCount", "SampleFile"}
        for step_id, modules in parameters.items():
            entries: List[Dict[str, Any]] = []
            for module in self._as_list(modules):
                if not isinstance(module, dict):
                    continue
                module_m = module.get("m")
                module_n = module.get("n")
                parameter_list = module.get("parameterList") or module.get("ParameterList") or []
                for parameter in self._as_list(parameter_list):
                    if not isinstance(parameter, dict):
                        continue
                    key = parameter.get("Key") or parameter.get("key")
                    if not key:
                        continue
                    value_key = "Value" if "Value" in parameter else "value"
                    display_key = "DisplayValue" if "DisplayValue" in parameter else "displayValue"
                    value = parameter.get(value_key)
                    if self._is_blank_lims_parameter_value(value) and not self._is_blank_lims_parameter_value(parameter.get(display_key)):
                        value = parameter.get(display_key)
                    if self._is_blank_lims_parameter_value(value) and str(key) not in required_day2_keys:
                        continue
                    entry: Dict[str, Any] = {"key": str(key), "value": "" if self._is_blank_lims_parameter_value(value) else str(value)}
                    m_value = parameter.get("m", module_m)
                    n_value = parameter.get("n", module_n)
                    if m_value is not None:
                        entry["m"] = int(m_value)
                    if n_value is not None:
                        entry["n"] = int(n_value)
                    entries.append(entry)
            if entries:
                flattened[str(step_id)] = entries
        return flattened

    def _filter_day2_lims_input_parameters(self, param_values: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
        required_keys = {"CEMMethodFileName", "SampleCount", "SampleFile"}
        filtered: Dict[str, List[Dict[str, Any]]] = {}
        for step_id, entries in param_values.items():
            day2_entries = [entry for entry in entries if entry.get("Key") in required_keys or entry.get("key") in required_keys]
            if day2_entries:
                filtered[step_id] = day2_entries
        return filtered

    def _normalize_day2_lims_param_values(self, param_values: Any) -> Dict[str, Any]:
        """统一 LIMS create-order 参数字段名，避免后端按小写字段读取时拿到空参数。"""
        parsed = self._json_loads_if_string(param_values)
        if not isinstance(parsed, dict):
            return {}

        normalized: Dict[str, Any] = {}
        for step_id, entries in parsed.items():
            if not self._looks_like_uuid(step_id):
                continue
            normalized_entries = [
                normalized_entry
                for entry in self._as_list(entries)
                if (normalized_entry := self._normalize_day2_lims_parameter_entry(entry)) is not None
            ]
            if normalized_entries:
                normalized[str(step_id)] = normalized_entries
        return normalized

    def _normalize_day2_lims_parameter_entry(self, entry: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(entry, dict):
            return None
        normalized = copy.deepcopy(entry)
        for source_key, target_key in _PARAMETER_KEY_ALIASES.items():
            if source_key in normalized and target_key not in normalized:
                normalized[target_key] = normalized.pop(source_key)
            elif source_key in normalized:
                normalized.pop(source_key)
        key = normalized.get("key")
        if self._is_blank_lims_parameter_value(key):
            return None
        value = normalized.get("value")
        display_value = normalized.get("displayValue")
        if self._is_blank_lims_parameter_value(value) and not self._is_blank_lims_parameter_value(display_value):
            value = display_value
        if self._is_blank_lims_parameter_value(value):
            return None
        sanitized: Dict[str, Any] = {"key": str(key), "value": str(value)}
        for axis in ("m", "n"):
            axis_value = normalized.get(axis)
            if self._is_blank_lims_parameter_value(axis_value):
                continue
            try:
                sanitized[axis] = int(axis_value)
            except (TypeError, ValueError):
                sanitized[axis] = axis_value
        return sanitized

    def _is_blank_lims_parameter_value(self, value: Any) -> bool:
        return value is None or (isinstance(value, str) and value.strip() == "")

    def _set_parameter_value(self, parameters: Any, key: str, value: Any) -> None:
        value_text = "" if value is None else str(value)

        def visit(obj: Any) -> None:
            if isinstance(obj, list):
                for item in obj:
                    visit(item)
                return
            if not isinstance(obj, dict):
                return
            param_key = obj.get("key") or obj.get("Key")
            if param_key == key:
                value_key = "Value" if "Value" in obj else "value"
                display_key = "DisplayValue" if "DisplayValue" in obj else "displayValue"
                obj[value_key] = value_text
                if display_key in obj:
                    obj[display_key] = value_text
            for item in obj.values():
                visit(item)

        visit(parameters)

    def _sample_excel_list(self) -> List[Dict[str, Any]]:
        response = self._project_post(
            "/api/data/order/sample-excel-list",
            {"beginDate": None, "endDate": None, "nameFilter": ""},
        )
        data = self._require_project_success(response, "/api/data/order/sample-excel-list")
        return data if isinstance(data, list) else []

    def _samples_from_file(self, sample_file: str, sub_workflow_id: str) -> List[Dict[str, Any]]:
        response = self._project_post(
            "/api/data/order/samples-from-file",
            {"fileName": sample_file, "subWFId": sub_workflow_id},
        )
        data = self._require_project_success(response, "/api/data/order/samples-from-file")
        return data if isinstance(data, list) else []

    def _verify_sample_excel(self, cache_order_id: str, sample_file: str) -> str:
        response = self._project_post(
            "/api/order/commonly-order/verify-sample-excel",
            {"commonlyOrderId": cache_order_id, "excelUrl": sample_file},
        )
        data = self._require_project_success(response, "/api/order/commonly-order/verify-sample-excel")
        return "" if data is None else str(data)

    def _verify_lims_sample_excel(self, sample_file: str) -> Dict[str, Any]:
        """通过 LIMS 文件列表确认样品 Excel 已上传。"""
        file_name = self._day2_sample_file_name(sample_file)
        response = self._lims_post(
            "/api/lims/order/sample-info-excels",
            {"beginDate": None, "endDate": None, "nameFilter": file_name},
        )
        if response.get("code") != 1:
            raise RuntimeError(f"LIMS 样品 Excel 列表查询失败: {response}")
        files = response.get("data") if isinstance(response.get("data"), list) else []
        match = self._find_lims_sample_excel(files, sample_file)
        if match is None:
            raise RuntimeError(f"LIMS 未找到样品 Excel 文件: {sample_file}")
        return match

    def _find_lims_sample_excel(
        self,
        files: List[Dict[str, Any]],
        sample_file: str,
    ) -> Optional[Dict[str, Any]]:
        expected_path = self._normalize_day2_sample_path(sample_file)
        expected_name = self._day2_sample_file_name(sample_file).lower()
        for item in files:
            if not isinstance(item, dict):
                continue
            relative_path = self._normalize_day2_sample_path(str(item.get("relativePath") or ""))
            file_name = str(item.get("fileName") or "").lower()
            if relative_path == expected_path or file_name == expected_name:
                return item
        return None

    def _day2_sample_file_name(self, sample_file: str) -> str:
        return str(sample_file).replace("\\", "/").rstrip("/").split("/")[-1]

    def _normalize_day2_sample_path(self, sample_file: str) -> str:
        return str(sample_file).replace("\\", "/").strip().lower()

    def _extract_workflow_parameters(self, step_data: Any) -> Any:
        parsed = self._json_loads_if_string(step_data)
        if self._looks_like_lims_step_parameter_map(parsed):
            return parsed
        for key in ("paramValues", "stepParameters", "workflowParameter", "parameters", "data"):
            value = self._find_first_key(step_data, key)
            if value not in (None, {}, []):
                return value
        return parsed

    def _extract_step_material_records(self, step_data: Any) -> Any:
        for key in ("stepMaterialRecords", "stepMaterialRecord", "materialRecords", "materialRecord"):
            value = self._find_first_key(step_data, key)
            if value not in (None, {}, []):
                return self._json_loads_if_string(value)
        return {}

    # ==================== Legacy Project location helpers ====================

    def _locations_by_type(
        self,
        workflow_id: str,
        material_type_mode: int,
        commonly_order_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"WorkflowId": workflow_id, "MaterialTypeMode": material_type_mode}
        if commonly_order_id:
            params["CommonlyOrderId"] = commonly_order_id
        response = self._project_get("/api/storage/location/locations-by-type", params=params)
        data = self._require_project_success(response, "/api/storage/location/locations-by-type")
        return data if isinstance(data, list) else []

    def _empty_locations_by_type(
        self,
        workflow_id: str,
        material_type_mode: int,
        loc_count: int,
        commonly_order_id: str,
    ) -> List[Dict[str, Any]]:
        params = {
            "WorkFlowId": workflow_id,
            "CommonlyOrderId": commonly_order_id,
            "MaterialTypeMode": material_type_mode,
            "LocCount": loc_count,
        }
        response = self._project_get("/api/storage/location/empty-locations-by-type", params=params)
        data = self._require_project_success(response, "/api/storage/location/empty-locations-by-type")
        return data if isinstance(data, list) else []

    def _material_and_in_by_locations(self, items: List[Dict[str, Any]]) -> bool:
        response = self._project_post("/api/storage/location/material-and-in-by-locations", items)
        data = self._require_project_success(response, "/api/storage/location/material-and-in-by-locations")
        return bool(data)

    def _pre_distribute_sample(self, cache_order_id: str) -> List[Dict[str, Any]]:
        path = f"/api/storage/location/pre-distribute-sample/{cache_order_id}"
        response = self._project_get(path)
        data = self._require_project_success(response, path)
        return data if isinstance(data, list) else []

    def _out_apply_material(self, cache_order_id: str) -> List[Dict[str, Any]]:
        path = f"/api/storage/location/out-apply-material-sSDT/{cache_order_id}"
        response = self._project_get(path, params={"destType": "TempOrder"})
        data = self._require_project_success(response, path)
        return data if isinstance(data, list) else []

    def _pre_distributed_sample_for_temp_order(self, cache_order_id: str) -> List[Dict[str, Any]]:
        path = f"/api/storage/location/pre-distributed-sample-for-temp-order/{cache_order_id}"
        response = self._project_get(path)
        data = self._require_project_success(response, path)
        return data if isinstance(data, list) else []

    def _build_day2_location_payloads(self, prepared: Dict[str, Any], placements: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        if self._looks_like_location_payloads(placements):
            return self._normalize_location_payloads(prepared, placements)
        return {
            "sample": self._build_sample_location_items(prepared, placements.get("sample", [])),
            "reagent": self._build_material_location_items(prepared, placements.get("reagent", []), include_container_details=True),
            "consumable": self._build_material_location_items(prepared, placements.get("consumable", []), include_container_details=False),
        }

    def _build_sample_location_items(self, prepared: Dict[str, Any], suggestions: Any) -> List[Dict[str, Any]]:
        items = []
        sample_count = int(prepared.get("sample_count") or 0)
        for group_index, group in enumerate(self._as_list(suggestions), start=1):
            if sample_count and group_index > sample_count:
                break
            locations = self._locations_from_group(group)
            if not locations:
                continue
            loc_with_intake = []
            sequence = str(group.get("sequence") or group_index)
            preintake_code = group.get("preintakeCode") or f"{prepared.get('order_name')}-{group_index:02d}"
            for location in locations:
                loc_id = location.get("id") or location.get("locId") or location.get("locationId")
                if not loc_id:
                    continue
                loc_with_intake.append(
                    {
                        "locId": loc_id,
                        "sequence": sequence,
                        "preintakeCode": preintake_code,
                    }
                )
            if not loc_with_intake:
                continue
            item = {
                "associateId": prepared.get("cache_order_id"),
                "materialTypeId": group.get("materialTypeId") or group.get("holdMTypeId"),
                "locWithIntake": loc_with_intake,
                "verifyLocs": group.get("verifyLocs"),
            }
            items.append({key: value for key, value in item.items() if value is not None})
        return items

    def _build_material_location_items(
        self,
        prepared: Dict[str, Any],
        suggestions: Any,
        *,
        include_container_details: bool,
    ) -> List[Dict[str, Any]]:
        items = []
        for group in self._as_list(suggestions):
            locations = self._locations_from_group(group)
            location_ids = [loc.get("id") or loc.get("locId") or loc.get("locationId") for loc in locations]
            location_ids = [loc_id for loc_id in location_ids if loc_id]
            if not location_ids:
                continue
            item = {
                "associateId": prepared.get("cache_order_id"),
                "materialTypeId": group.get("materialTypeId") or group.get("holdMTypeId"),
                "quantity": group.get("quantity", len(location_ids)),
                "locationIds": location_ids,
                "verifyLocs": group.get("verifyLocs"),
            }
            if include_container_details:
                item["containerDetails"] = group.get("containerDetails") if "containerDetails" in group else group.get("containerDetail")
            items.append({key: value for key, value in item.items() if value is not None or key == "containerDetails"})
        return items

    # ==================== Review and logging helpers ====================

    def _day2_checklist_values(self, checklist: Optional[Dict[str, bool]]) -> Dict[str, bool]:
        values = {
            "fridge_low_temperature_storage_closed": False,
            "lab_clean": False,
            "sample_information_verified": False,
            "device_status_acceptable": False,
        }
        if checklist:
            values.update({key: bool(value) for key, value in checklist.items() if key in values})
        return values

    def _day2_final_review(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        payload = prepared.get("lims_order_payload") or []
        return {
            "lims_endpoint": "/api/lims/order/order",
            "order_payload_ready": bool(payload),
            "order_payload_count": len(payload) if isinstance(payload, list) else 0,
            "placement_finished": bool(prepared.get("placement_finished")),
            "checklist_confirmed": bool(prepared.get("checklist_confirmed")),
            "project_mutation": False,
        }

    def _log_presentation_payload(self, node_name: str, payload: Dict[str, Any]) -> None:
        logger.info(f"{node_name} 展示信息: {json.dumps(payload, ensure_ascii=False, sort_keys=True)}")

    def _new_day2_execution_report(self) -> Dict[str, Any]:
        return {"api_fallbacks": []}

    def _record_api_fallback(
        self,
        report: Dict[str, Any],
        operation: str,
        preferred_lims_endpoint: str,
        fallback_endpoint: str,
        reason: str,
        side_effect: str,
        result: Dict[str, Any],
    ) -> None:
        report.setdefault("api_fallbacks", []).append(
            {
                "operation": operation,
                "preferred_lims_endpoint": preferred_lims_endpoint,
                "fallback_endpoint": fallback_endpoint,
                "fallback_type": "project-browser-observed",
                "reason": reason,
                "side_effect": side_effect,
                "result_code": self._response_code(result),
                "result": result,
            }
        )

    # ==================== Browser cleanup helpers ====================

    def _browser_cancel_experiment_by_order_codes(self, order_codes: List[str]) -> Dict[str, Any]:
        response = self._project_put("/api/order/order/cancel-experiment", order_codes)
        data = self._require_project_success(response, "/api/order/order/cancel-experiment")
        return response if isinstance(response, dict) else {"code": 1, "data": data}

    def _browser_take_out_sample(self, material_ids: List[str]) -> Dict[str, Any]:
        response = self._project_post("/api/order/order/take-out-sample", {"materialIds": material_ids})
        data = self._require_project_success(response, "/api/order/order/take-out-sample")
        return response if isinstance(response, dict) else {"code": 1, "data": data}

    def _preintake_used_sample_record(self, preintake_id: str) -> List[Dict[str, Any]]:
        response = self._project_get("/api/order/order/preintake-used-sample-record", params={"id": preintake_id})
        data = self._require_project_success(response, "/api/order/order/preintake-used-sample-record")
        return data if isinstance(data, list) else []

    def _used_material(self, preintake_ids: List[str], material_type_mode: int) -> List[Dict[str, Any]]:
        response = self._project_post(
            "/api/storage/location/used-material",
            {"associateId": preintake_ids, "materialTypeMode": material_type_mode},
        )
        data = self._require_project_success(response, "/api/storage/location/used-material")
        return data if isinstance(data, list) else []

    def _lims_take_out(self, order_id: str, preintake_ids: List[str], material_ids: List[str]) -> Dict[str, Any]:
        if not order_id:
            return {"code": 0, "message": "缺少 order_id"}
        return self._lims_post(
            "/api/lims/order/take-out",
            {"orderId": order_id, "preintakeIds": preintake_ids, "materialIds": material_ids},
        )

    def _browser_scheduler_reset(
        self,
        user_id: Optional[str] = None,
        user_name: Optional[str] = None,
        timeout: int = 50000,
    ) -> Dict[str, Any]:
        body = {"userId": user_id, "userName": user_name, "timeout": timeout}
        response = self._project_post("/api/scheduler/scheduler/reset", body)
        data = self._require_project_success(response, "/api/scheduler/scheduler/reset")
        return response if isinstance(response, dict) else {"code": 1, "data": data}

    def _browser_reset_location(self) -> Dict[str, Any]:
        response = self._project_post("/api/storage/location/reset-location", {})
        data = self._require_project_success(response, "/api/storage/location/reset-location")
        return response if isinstance(response, dict) else {"code": 1, "data": data}

    def _browser_reset_order_status(self) -> Dict[str, Any]:
        response = self._project_put("/api/order/order/reset-status", {})
        data = self._require_project_success(response, "/api/order/order/reset-status")
        return response if isinstance(response, dict) else {"code": 1, "data": data}

    def _browser_order_basic_info(self, order_id: str) -> Dict[str, Any]:
        path = f"/api/order/order/order-basic-info/{order_id}"
        response = self._project_get(path)
        data = self._require_project_success(response, path)
        return data if isinstance(data, dict) else {}

    # ==================== LIMS low-level helpers ====================

    def _lims_post(self, path: str, data: Any) -> Dict[str, Any]:
        rpc = getattr(self, "hardware_interface", None)
        host = getattr(rpc, "host", None) or self._day2_config_value("api_host")
        api_key = getattr(rpc, "api_key", None) or self._day2_config_value("api_key")
        if not host or not api_key:
            return {"code": 0, "message": "缺少 LIMS host/api_key"}
        payload = {"apiKey": api_key, "requestTime": _utc_now_iso8601_ms(), "data": data}
        if rpc is not None and hasattr(rpc, "post"):
            response = rpc.post(url=f"{str(host).rstrip('/')}{path}", params=payload)
            return response or {"code": 0, "message": "LIMS API 无响应"}
        response = requests.post(
            f"{str(host).rstrip('/')}{path}",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=int(self._day2_config_value("timeout", 30) or 30),
        )
        response.raise_for_status()
        return response.json()

    def _try_lims_cancel(self, order_id: Optional[str]) -> Dict[str, Any]:
        if not order_id:
            return {"code": 0, "message": "缺少 order_id"}
        rpc = getattr(self, "hardware_interface", None)
        if rpc is not None and hasattr(rpc, "cancel_experiment"):
            return {"code": int(rpc.cancel_experiment(order_id) or 0)}
        return self._lims_post("/api/lims/order/cancel-experiment", order_id)

    def _safe_scheduler_reset(self) -> int:
        rpc = getattr(self, "hardware_interface", None)
        if rpc is not None and hasattr(rpc, "scheduler_reset"):
            try:
                return int(rpc.scheduler_reset() or 0)
            except Exception as exc:
                logger.warning(f"LIMS 调度器复位失败: {exc}")
        return 0

    # ==================== Generic utilities ====================

    def _day2_config_value(self, key: str, default: Any = None) -> Any:
        config = getattr(self, "bioyond_config", None)
        if isinstance(config, dict) and key in config:
            return config.get(key)
        return default

    def _day2_config_has_key(self, key: str) -> bool:
        config = getattr(self, "bioyond_config", None)
        return isinstance(config, dict) and key in config

    def _looks_like_uuid(self, value: Any) -> bool:
        try:
            UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            return False
        return True

    def _looks_like_lims_step_parameter_map(self, value: Any) -> bool:
        parsed = self._json_loads_if_string(value)
        if not isinstance(parsed, dict):
            return False
        return any(self._looks_like_uuid(key) and isinstance(item, list) for key, item in parsed.items())

    def _iter_dicts(self, obj: Any) -> Iterable[Dict[str, Any]]:
        if isinstance(obj, dict):
            yield obj
            for value in obj.values():
                yield from self._iter_dicts(value)
        elif isinstance(obj, list):
            for item in obj:
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
            if self._record_id(record) and self._record_name(record):
                return record
        return None

    def _choose_sub_workflow_record(self, root: Optional[Dict[str, Any]], workflow_name: str) -> Optional[Dict[str, Any]]:
        if not isinstance(root, dict):
            return None
        candidates = []
        for key in ("workflows", "subWorkflows", "subWorkflowList", "children"):
            value = root.get(key)
            if isinstance(value, list):
                candidates.extend([item for item in value if isinstance(item, dict)])
        exact = [item for item in candidates if self._record_name(item) == workflow_name]
        return self._choose_workflow_record(exact) or self._choose_workflow_record(candidates)

    def _workflow_id_from_config(self, workflow_name: str) -> Optional[str]:
        mappings = self._day2_config_value("workflow_mappings", {}) or {}
        if isinstance(mappings, dict):
            value = mappings.get(workflow_name)
            if isinstance(value, dict):
                return value.get("id") or value.get("workflowId")
            if value:
                return str(value)
        return None

    def _find_first_key(self, obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            if key in obj:
                return obj[key]
            for value in obj.values():
                found = self._find_first_key(value, key)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = self._find_first_key(item, key)
                if found is not None:
                    return found
        return None

    def _json_loads_if_string(self, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return value
        return value

    def _json_dumps_stable(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def _infer_sample_count(self, samples: Any) -> int:
        if not isinstance(samples, list):
            return 0
        return len(samples)

    def _as_list(self, value: Any) -> List[Any]:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def _locations_from_group(self, group: Any) -> List[Dict[str, Any]]:
        if not isinstance(group, dict):
            return []
        if isinstance(group.get("locations"), list):
            return [item for item in group["locations"] if isinstance(item, dict)]
        if isinstance(group.get("locationIds"), list):
            return [{"id": loc_id} for loc_id in group["locationIds"]]
        if isinstance(group.get("locWithIntake"), list):
            return [{"id": item.get("locId")} for item in group["locWithIntake"] if isinstance(item, dict) and item.get("locId")]
        if group.get("id") or group.get("locId") or group.get("locationId"):
            return [group]
        return []

    def _looks_like_location_payloads(self, placements: Dict[str, Any]) -> bool:
        if not isinstance(placements, dict):
            return False
        for key in ("sample", "reagent", "consumable"):
            value = placements.get(key)
            if isinstance(value, list) and value and isinstance(value[0], dict):
                if "locationIds" in value[0] or "locWithIntake" in value[0]:
                    return True
        return False

    def _normalize_location_payloads(self, prepared: Dict[str, Any], placements: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        normalized: Dict[str, List[Dict[str, Any]]] = {"sample": [], "reagent": [], "consumable": []}
        for key in normalized:
            for item in copy.deepcopy(self._as_list(placements.get(key))):
                if not isinstance(item, dict):
                    continue
                item.setdefault("associateId", prepared.get("cache_order_id"))
                if key == "sample":
                    item.setdefault("locationIds", [])
                    item.setdefault("locWithIntake", [])
                else:
                    item.setdefault("locationIds", [])
                    item.setdefault("locWithIntake", [])
                if key == "reagent":
                    item.setdefault("containerDetails", item.get("containerDetail", []))
                normalized[key].append(item)
        return normalized

    def _summarize_material_records(self, records: Any) -> Dict[str, Any]:
        parsed = self._json_loads_if_string(records)
        if isinstance(parsed, dict):
            return {"group_count": len(parsed), "record_count": sum(len(v) for v in parsed.values() if isinstance(v, list))}
        if isinstance(parsed, list):
            return {"record_count": len(parsed)}
        return {"record_count": 0}

    def _summarize_location_payloads(self, payloads: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        return {
            "sample_item_count": len(payloads.get("sample", [])),
            "reagent_item_count": len(payloads.get("reagent", [])),
            "consumable_item_count": len(payloads.get("consumable", [])),
            "sample_location_count": sum(len(item.get("locWithIntake", [])) for item in payloads.get("sample", [])),
            "reagent_location_count": sum(len(item.get("locationIds", [])) for item in payloads.get("reagent", [])),
            "consumable_location_count": sum(len(item.get("locationIds", [])) for item in payloads.get("consumable", [])),
        }

    def _response_code(self, result: Any) -> Any:
        if isinstance(result, dict):
            if "code" in result:
                return result.get("code")
            if result.get("data") is True:
                return 1
        if result is True:
            return 1
        return None

    def _extract_preintake_ids(self, obj: Any) -> List[str]:
        ids = []
        parsed = self._json_loads_if_string(obj)

        def visit(value: Any, in_preintakes: bool = False) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    key_text = str(key)
                    child_in_preintakes = in_preintakes or key_text in {"preIntakes", "preintakes", "preIntakeList"}
                    if key_text in {"preintakeId", "preIntakeId", "preintake_id"} and child:
                        ids.append(str(child))
                    elif key_text == "id" and in_preintakes and child:
                        ids.append(str(child))
                    else:
                        visit(child, child_in_preintakes)
                return
            if isinstance(value, list):
                for item in value:
                    visit(item, in_preintakes)

        visit(parsed)
        return ids

    def _extract_material_ids(self, obj: Any) -> List[str]:
        ids = []
        for record in self._iter_dicts(obj):
            for key in ("materialId", "detailMaterialId", "holdMId"):
                if record.get(key):
                    ids.append(str(record[key]))
        return ids

    def _day2_cleanup_planned_calls(
        self,
        *,
        order_code: Optional[str],
        order_id: Optional[str],
        preintake_ids: List[str],
        material_ids: List[str],
        allow_cancel: bool,
        allow_take_out: bool,
        allow_scheduler_reset: bool,
        allow_storage_reset: bool,
        allow_order_status_reset: bool,
        allow_browser_routes: bool,
    ) -> List[Dict[str, Any]]:
        calls = []
        if allow_cancel:
            calls.append({"operation": "cancel_experiment", "endpoint": "/api/lims/order/cancel-experiment", "order_id": order_id})
            if allow_browser_routes and order_code:
                calls.append({"operation": "browser_cancel_experiment", "endpoint": "/api/order/order/cancel-experiment", "order_code": order_code})
        if allow_take_out:
            calls.append({"operation": "take_out", "endpoint": "/api/lims/order/take-out", "order_id": order_id, "preintake_ids": preintake_ids, "material_ids": material_ids})
            if allow_browser_routes and material_ids:
                calls.append({"operation": "browser_take_out_sample", "endpoint": "/api/order/order/take-out-sample", "material_ids": material_ids})
        if allow_scheduler_reset:
            calls.append({"operation": "scheduler_reset", "endpoint": "/api/lims/scheduler/reset"})
        if allow_storage_reset:
            calls.append({"operation": "storage_reset", "endpoint": "/api/storage/location/reset-location", "requires_browser_route": True})
        if allow_order_status_reset:
            calls.append({"operation": "order_status_reset", "endpoint": "/api/order/order/reset-status", "requires_browser_route": True})
        return calls

    def _remember_day2_created_order(self, prepared: Dict[str, Any]) -> None:
        order_ids = getattr(self, "_day2_created_order_ids", set())
        order_codes = getattr(self, "_day2_created_order_codes", set())
        cache_order_id = prepared.get("cache_order_id")
        order_id = prepared.get("order_id")
        order_code = prepared.get("order_code")
        if cache_order_id:
            order_ids.add(cache_order_id)
        if order_id:
            order_ids.add(order_id)
        if order_code:
            order_codes.add(order_code)
        self._day2_created_order_ids = order_ids
        self._day2_created_order_codes = order_codes

    def _assert_day2_cleanup_order_is_owned(
        self,
        *,
        order_code: Optional[str],
        order_id: Optional[str],
        allow_external_order: bool,
    ) -> None:
        if allow_external_order:
            return
        created_ids = getattr(self, "_day2_created_order_ids", set())
        created_codes = getattr(self, "_day2_created_order_codes", set())
        if order_id and order_id in created_ids:
            return
        if order_code and order_code in created_codes:
            return
        raise PermissionError("拒绝清理非当前运行创建的 Day2 订单；需要 allow_external_order=True")

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={
            "target_device": "unilabos_devices",
            "resource": "unilabos_resources",
            "mount_resource": "unilabos_resources",
            "assignee_user_ids": "unilabos_manual_confirm",
        },
        goal_default={
            "materials_loaded": False,
            "timeout_seconds": 3600,
            "assignee_user_ids": [],
        },
        feedback_interval=300,
        description="请核对并装载多肽物料；确认后启动 Bioyond 调度器",
        handles=[
            ActionInputHandle(
                key="target_device",
                data_type="device_id",
                label="目标设备",
                data_key="target_device",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionInputHandle(
                key="resource",
                data_type="resource",
                label="待装载物料",
                data_key="resource",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionInputHandle(
                key="mount_resource",
                data_type="resource",
                label="库位",
                data_key="mount_resource",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionInputHandle(
                key="order_id",
                data_type="bioyond_order_id",
                label="实验ID",
                data_key="order_id",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionInputHandle(
                key="order_ids",
                data_type="bioyond_order_ids",
                label="实验ID列表",
                data_key="order_ids",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
        ],
    )
    def start_experiment(
        self,
        target_device: DeviceSlot = "bioyond_peptide_station",
        resource: Optional[List[ResourceSlot]] = None,
        mount_resource: Optional[List[ResourceSlot]] = None,
        order_id: str = "",
        materials_loaded: bool = False,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """手动装载确认后启动 LIMS 调度器。"""
        del target_device, timeout_seconds, assignee_user_ids
        with self._debug_call_session("start_experiment"):
            order_ids = self._extract_order_ids(order_id=order_id, **kwargs)
            has_material_display = any(value for value in (resource, mount_resource, kwargs.get("resource"), kwargs.get("mount_resource")))
            if has_material_display and not bool(materials_loaded):
                raise RuntimeError("多肽物料装载未确认，拒绝启动调度器")
            result = self._run_scheduler_action("scheduler_start", "启动", **kwargs)
            result["order_ids"] = order_ids
            result["materials_loaded"] = bool(materials_loaded)
            return result

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
        goal_default={
            "reset_operations": ["scheduler_reset", "reset_order_status", "reset_location"],
            "dry_run": True,
            "ready_signal": "",
            "timeout_seconds": 3600,
            "assignee_user_ids": [],
        },
        feedback_interval=300,
        description="复位多肽实验前状态",
    )
    def reset(
        self,
        reset_operations: Optional[
            List[Literal["scheduler_reset", "reset_order_status", "reset_location"]]
        ] = None,
        dry_run: bool = True,
        ready_signal: str = "",
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """按显式操作列表复位调度器、订单状态或库位。"""
        del timeout_seconds, assignee_user_ids
        with self._debug_call_session("reset"):
            operations = list(reset_operations or DEFAULT_RESET_OPERATIONS)
            planned = [
                {"operation": operation, "endpoint": self._reset_operation_endpoint(operation)}
                for operation in operations
            ]
            result: Dict[str, Any] = {"dry_run": bool(dry_run), "planned_calls": planned, "executed_calls": []}
            if dry_run:
                return result
            self._require_ready_signal(ready_signal or str(kwargs.get("ready_signal") or ""))
            rpc = self._require_hardware_interface()
            for operation in operations:
                if operation == "scheduler_reset":
                    code = rpc.scheduler_reset()
                    result["executed_calls"].append({"operation": operation, "result": {"code": code}})
                elif operation == "reset_order_status":
                    order_id = str(kwargs.get("reset_order_id") or kwargs.get("order_id") or "").strip()
                    code = rpc.reset_order_status(order_id)
                    result["executed_calls"].append({"operation": operation, "order_id": order_id, "result": {"code": code}})
                elif operation == "reset_location":
                    location_id = str(kwargs.get("reset_location_id") or kwargs.get("location_id") or "").strip()
                    code = rpc.reset_location(location_id)
                    result["executed_calls"].append({"operation": operation, "location_id": location_id, "result": {"code": code}})
                else:
                    raise ValueError(f"未知 reset operation: {operation}")
            return result

    @action(always_free=True, description="直接启动 Bioyond 多肽调度器")
    def scheduler_start(self, **kwargs: Any) -> Dict[str, Any]:
        """直接调用 Bioyond 调度器启动接口。"""
        return self._run_scheduler_action("scheduler_start", "启动", **kwargs)

    @action(always_free=True, description="直接停止 Bioyond 多肽调度器")
    def scheduler_stop(self, **kwargs: Any) -> Dict[str, Any]:
        """直接调用 Bioyond 调度器停止接口。"""
        return self._run_scheduler_action("scheduler_stop", "停止", **kwargs)

    @action(always_free=True, description="直接暂停 Bioyond 多肽调度器")
    def scheduler_pause(self, **kwargs: Any) -> Dict[str, Any]:
        """直接调用 Bioyond 调度器暂停接口。"""
        return self._run_scheduler_action("scheduler_pause", "暂停", **kwargs)

    @action(always_free=True, description="直接继续 Bioyond 多肽调度器")
    def scheduler_continue(self, **kwargs: Any) -> Dict[str, Any]:
        """直接调用 Bioyond 调度器继续接口。"""
        return self._run_scheduler_action("scheduler_continue", "继续", **kwargs)

    def _resolve_submit_sample_file(
        self,
        required_params: Dict[str, Any],
        optional_params: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any]]:
        pattern = str(required_params.get("sample_excel_pattern") or "").strip()
        if not pattern:
            raise PeptideWorkflowError("提交实验必须提供 sample_excel_pattern（样品 Excel 文件名匹配模式）")

        selected = self._select_available_lims_sample_excel(pattern)
        self._last_selected_sample_excel = selected
        sample_file = str(selected.get("relativePath") or selected.get("filePath") or "").replace("/", "\\")
        if not sample_file:
            raise PeptideWorkflowError(f"LIMS 样品 Excel 匹配 {pattern!r}，但返回记录缺少 relativePath/filePath")
        return sample_file, selected

    def _select_available_lims_sample_excel(self, pattern: str) -> Dict[str, Any]:
        name_filter = pattern.replace("*", "")
        records = self._list_lims_sample_excels(name_filter=name_filter)
        matched = [record for record in records if self._filename_matches_pattern(str(record.get("fileName") or ""), pattern)]
        if not matched:
            raise PeptideWorkflowError(f"未找到匹配 {pattern!r} 的 LIMS 样品 Excel，工作流已停止")
        if len(matched) > 1:
            names = ", ".join(str(item.get("fileName") or "") for item in matched)
            raise PeptideWorkflowError(f"找到多个匹配 {pattern!r} 的 LIMS 样品 Excel: {names}，请收窄匹配模式")
        return matched[0]

    @staticmethod
    def _optional_submit_sample_count(optional_params: Dict[str, Any]) -> Optional[int]:
        value = optional_params.get("sample_count")
        if value in (None, ""):
            return None
        count = int(value)
        if count <= 0:
            raise PeptideWorkflowError("sample_count 如填写必须大于 0")
        return count

    def _list_lims_sample_excels(self, name_filter: str = "") -> List[Dict[str, Any]]:
        rpc = self._require_hardware_interface()
        response = rpc.post(
            url=f"{rpc.host}/api/lims/order/sample-info-excels",
            params={
                "apiKey": rpc.api_key,
                "requestTime": _utc_now_iso8601_ms(),
                "data": {"beginDate": None, "endDate": None, "nameFilter": name_filter or None},
            },
        )
        if not isinstance(response, dict) or response.get("code") != 1:
            return []
        data = response.get("data")
        return data if isinstance(data, list) else []

    def _resolve_local_excel_path(self, optional_params: Dict[str, Any], pattern: str) -> Path:
        explicit = str(optional_params.get("local_excel_path") or self.bioyond_config.get("default_local_excel_path") or "").strip()
        if explicit:
            return Path(explicit).expanduser()
        input_dir = Path.cwd()
        matches = sorted(input_dir.glob(pattern))
        if not matches:
            raise FileNotFoundError(f"本地未找到样品 Excel: {input_dir / pattern}")
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

    def _run_scheduler_action(self, method_name: str, label: str, **kwargs: Any) -> Dict[str, Any]:
        with self._debug_call_session(method_name):
            ready_signal = str(kwargs.get("ready_signal") or DEFAULT_READY_SIGNAL)
            self._require_ready_signal(ready_signal)
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
            rpc = self._require_hardware_interface()
            status = rpc.scheduler_status()
            return status if isinstance(status, dict) else {}
        except Exception as exc:
            return {"error": str(exc)}

    @staticmethod
    def _scheduler_status_is_running(status: Any) -> bool:
        if not status:
            return False
        scheduler_status = status.get("schedulerStatus") if isinstance(status, dict) else status
        if isinstance(scheduler_status, str):
            if scheduler_status.lower() in {"running", "run", "2"}:
                return True
        elif scheduler_status == 2:
            return True
        if isinstance(status, dict):
            text = json.dumps(status, ensure_ascii=False).lower()
            return any(token in text for token in ["running", "运行", "\"schedulerstatus\": 1", "\"hastask\": true"])
        return False

    def _require_hardware_interface(self):
        rpc = getattr(self, "hardware_interface", None)
        if rpc is None:
            raise RuntimeError("Bioyond RPC 客户端未初始化")
        return rpc

    @staticmethod
    def _require_ready_signal(ready_signal: str) -> None:
        if str(ready_signal).strip().upper() != DEFAULT_READY_SIGNAL:
            raise PermissionError(f"需要 ready_signal={DEFAULT_READY_SIGNAL} 才能执行该操作")

    @staticmethod
    def _extract_order_ids(order_id: str = "", **kwargs: Any) -> List[str]:
        raw_order_ids = kwargs.get("order_ids")
        if isinstance(raw_order_ids, list):
            order_ids = [str(value) for value in raw_order_ids if value]
        elif isinstance(raw_order_ids, str) and raw_order_ids.strip():
            try:
                parsed = json.loads(raw_order_ids)
                order_ids = [str(value) for value in parsed] if isinstance(parsed, list) else [raw_order_ids]
            except ValueError:
                order_ids = [raw_order_ids]
        else:
            order_ids = []
        if order_id:
            order_ids.insert(0, str(order_id))
        return list(dict.fromkeys(order_ids))

    @staticmethod
    def _reset_operation_endpoint(operation: str) -> str:
        return {
            "scheduler_reset": "/api/lims/scheduler/reset",
            "reset_order_status": "/api/lims/order/reset-order-status",
            "reset_location": "/api/lims/storage/reset-location",
        }.get(operation, "")


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
