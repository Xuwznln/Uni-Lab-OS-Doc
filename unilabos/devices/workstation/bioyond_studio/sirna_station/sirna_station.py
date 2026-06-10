"""小核酸工作站最小运行时脚手架。"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import sys
import threading
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Dict, Iterable, List, Literal, Optional, Tuple
from urllib import error, request
from uuid import UUID

DEBUG_CLI_ENABLED = False

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

try:
    from unilabos.resources.bioyond.decks import BIOYOND_SirnaStation_Deck as _SIRNA_DECK_CLASS
except Exception:  # pragma: no cover - 允许无 pylabrobot 依赖时导入轻量 helper
    _SIRNA_DECK_CLASS = None

try:
    from unilabos.registry.decorators import (
        ActionInputHandle,
        ActionOutputHandle,
        DataSource,
        NodeType,
        action,
        device,
        not_action,
    )
    _REGISTRY_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - 允许无完整依赖时导入轻量 helper
    _REGISTRY_IMPORT_ERROR = exc

    class NodeType:  # type: ignore[no-redef]
        MANUAL_CONFIRM = "manual_confirm"

    class DataSource:  # type: ignore[no-redef]
        HANDLE = "handle"
        EXECUTOR = "executor"

    class _FallbackActionHandle:  # pragma: no cover - lightweight import-only fallback
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

        def to_registry_dict(self) -> Dict[str, Any]:
            return dict(self.__dict__)

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
            return args[0]

        def decorator(func):
            return func

        return decorator

    def not_action(func):
        return func

try:
    from unilabos.registry.placeholder_type import DeviceSlot, ResourceSlot
except Exception:  # pragma: no cover - 允许无 pylabrobot 依赖时导入轻量 helper
    class ResourceSlot:  # type: ignore[no-redef]
        pass

    class DeviceSlot(str):  # type: ignore[no-redef]
        pass

try:
    from unilabos.devices.workstation.workstation_base import WorkstationBase
    from unilabos.devices.workstation.bioyond_studio.station import BioyondWorkstation, BioyondResourceSynchronizer
    _BIOYOND_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - 允许在轻量探测模式下运行配置辅助函数
    WorkstationBase = object  # type: ignore[assignment,misc]
    BioyondWorkstation = object  # type: ignore[assignment,misc]
    BioyondResourceSynchronizer = object  # type: ignore[assignment,misc]
    _BIOYOND_IMPORT_ERROR = exc


WORKFLOW_LIST_ENDPOINT = "/api/lims/workflow/work-flow-list"
SUPPORTED_WORKFLOW_TYPES = {0, 1, 2}
RESET_PHYSICAL_CLEANUP_MESSAGE = "确认离心机配平板堆栈、G3移液站、自动化堆栈已清空，仪器内没有残留样品、耗材、试剂"
RESET_OPERATION_DEFINITIONS = (
    {
        "key": "reset_scheduler",
        "label": "复位调度器",
        "method": "scheduler_reset",
        "endpoint": "/api/lims/scheduler/reset",
    },
    {
        "key": "reset_order_status",
        "label": "复位订单状态",
        "method": "reset_order_status",
        "endpoint": "/api/lims/order/reset-order-status",
    },
    {
        "key": "reset_location",
        "label": "复位库位",
        "method": "reset_location",
        "endpoint": "/api/lims/storage/reset-location",
    },
    {
        "key": "reset_devices",
        "label": "复位设备",
        "method": "reset_devices",
        "endpoint": "/api/lims/device/reset-devices",
    },
)
DEFAULT_SIRNA_MATERIAL_TYPE_MAPPINGS = {
    "bioyond_sirna_g3_200ul_tip_rack": ["G3-200ul枪头盒", ""],
    "bioyond_sirna_g3_50ul_tip_rack": ["G3-50ul枪头盒", ""],
    "bioyond_sirna_384_well_plate": ["384孔板", ""],
    "bioyond_sirna_cell_culture_plate": ["细胞培养板", ""],
    "bioyond_sirna_reagent_trough": ["试剂槽RiboGreen", ""],
}
SIRNA_EXPERIMENT_1_WORKFLOW_NAME = "场景一：报告基因检测流程"
SIRNA_EXPERIMENT_1_SUB_WORKFLOW_NAME = "报告基因检测流程"
SIRNA_EXPERIMENT_1_WORKFLOW_ID = "3a1fc8e9-f807-3f9e-6f48-7132f594141a"
SIRNA_EXPERIMENT_1_SUB_WORKFLOW_ID = "3a1fc8ea-35b0-ce0c-1a46-ab506b647e4e"
SIRNA_EXPERIMENT_2_WORKFLOW_NAME = "场景二：基因表达检测"
OrderStatus = Literal["全部（\"\"）", "成功（80）", "失败（90）", "执行中（60）", "已取出（100）"]
ORDER_STATUS_VALUE_MAP = {
    "全部（\"\"）": "",
    "成功（80）": "80",
    "失败（90）": "90",
    "执行中（60）": "60",
    "已取出（100）": "100",
}
SIRNA_EXPERIMENT_2_SUB_WORKFLOW_NAME = "基因编辑检测"
SIRNA_EXPERIMENT_2_WORKFLOW_ID = "3a1fcdbd-316c-a4b8-a7ee-a262099552fa"
SIRNA_EXPERIMENT_2_SUB_WORKFLOW_ID = "3a1fd2d4-5d3f-fae1-8b3d-ec6d0abb6646"
SIRNA_WORKFLOW_BINDING_LAST_UPDATED = "2026-05-12"


# 「下料指引表」列结构（plan v2：4 列，与上料表 _build_result_table 一致，便于前端复用渲染逻辑）
UNLOAD_TABLE_COLUMNS: List[Dict[str, str]] = [
    {"name": "设备", "key": "whName"},
    {"name": "位置", "key": "locationCode"},
    {"name": "物料名称", "key": "materialName"},
    {"name": "数量", "key": "quantity"},
]


# Bioyond /report/order_finish 推送 status 字段语义映射（与 cell workstation 对齐）。
ORDER_FINISH_STATUS_MAP: Dict[str, str] = {
    "30": "success",
    "-11": "abnormal_stop",
    "-12": "manual_stop",
}


class SubmitExperimentRequiredParams(TypedDict):
    workflow_name: Annotated[str, Field(description="工作流名称（必填，不填写工作流 ID）")]
    sample_throughput: Annotated[int, Field(description="样品通量（1-96，必填），表示一次实验处理的样品数量。")]


class SubmitExperimentOptionalParams(TypedDict, total=False):
    sub_workflow_name: Annotated[str, Field(description="子工作流名称（可选；为空时选中根工作流下的可用子工作流）")]
    order_code: Annotated[str, Field(description="订单编号（可选，自动生成）")]
    order_name: Annotated[str, Field(description="订单名称（可选，自动生成）")]
    parameter_overrides: Annotated[
        List[Dict[str, Any]],
        Field(description='参数覆盖列表，格式应为 [{"m": 0, "n": 0, "Key": "Example", "Value": "example value"}]。'),
    ]
    auto_register_materials: Annotated[bool, Field(default=True, description="是否自动同步 Bioyond 物料到资源树")]


# 绑定信息（最后更新 2026-05-12）：
# 工作流「场景一：报告基因检测流程」= 3a1fc8e9-f807-3f9e-6f48-7132f594141a
# 子工作流「报告基因检测流程」= 3a1fc8ea-35b0-ce0c-1a46-ab506b647e4e
class Experiment1RequiredParams(TypedDict):
    sample_throughput: Annotated[int, Field(description="样品通量（1-96，必填），表示一次实验处理的样品数量。")]


class Experiment1OptionalParams(TypedDict, total=False):
    order_code: Annotated[str, Field(description="订单编号（可选，自动生成）")]
    order_name: Annotated[str, Field(description="订单名称（可选，自动生成）")]
    parameter_overrides: Annotated[
        List[Dict[str, Any]],
        Field(description='参数覆盖列表，格式应为 [{"m": 0, "n": 0, "Key": "Example", "Value": "example value"}]。'),
    ]
    auto_register_materials: Annotated[bool, Field(default=True, description="是否自动同步 Bioyond 物料到资源树")]


# 绑定信息（最后更新 2026-05-12）：
# 工作流「场景二：基因表达检测」= 3a1fcdbd-316c-a4b8-a7ee-a262099552fa
# 子工作流「基因编辑检测」= 3a1fd2d4-5d3f-fae1-8b3d-ec6d0abb6646
class Experiment2RequiredParams(TypedDict):
    sample_throughput: Annotated[int, Field(description="样品通量（1-96，必填），表示一次实验处理的样品数量。")]


class Experiment2OptionalParams(TypedDict, total=False):
    order_code: Annotated[str, Field(description="订单编号（可选，自动生成）")]
    order_name: Annotated[str, Field(description="订单名称（可选，自动生成）")]
    parameter_overrides: Annotated[
        List[Dict[str, Any]],
        Field(description='参数覆盖列表，格式应为 [{"m": 0, "n": 0, "Key": "Example", "Value": "example value"}]。'),
    ]
    auto_register_materials: Annotated[bool, Field(default=True, description="是否自动同步 Bioyond 物料到资源树")]


def _utc_now_iso8601_ms() -> str:
    """返回与 Bioyond LIMS 接口兼容的 UTC 时间戳。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _workflow_list_data(
    workflow_type: int = 0,
    filter_text: str = "",
    include_detail: bool = True,
) -> Dict[str, Any]:
    """构造 Sirna LIMS 已确认的工作流列表 data 载荷。"""
    if workflow_type not in SUPPORTED_WORKFLOW_TYPES:
        raise ValueError("workflow_type 必须是 Sirna LIMS schema 确认的 0、1 或 2")

    return {
        "type": workflow_type,
        "filter": filter_text,
        "includeDetail": include_detail,
    }


def _apply_default_sirna_material_type_mappings(config: Dict[str, Any]) -> None:
    configured = config.get("material_type_mappings")
    if not isinstance(configured, dict):
        configured = {}
    merged = dict(DEFAULT_SIRNA_MATERIAL_TYPE_MAPPINGS)
    merged.update(configured)
    config["material_type_mappings"] = merged


def load_sirna_config(config_path: str | Path) -> Dict[str, Any]:
    """从 JSON 文件读取小核酸站配置。"""
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
    """调用 Sirna LIMS 工作流列表接口。

    该 helper 只使用 OpenAPI 已确认的 LIMS workflow-list schema，不包含站点业务逻辑。
    """
    assert DEBUG_CLI_ENABLED == True, "fetch_workflow_list 是调试/CLI 快捷入口，运行时请使用 BioyondSirnaStation.fetch_workflow_list()"

    resolved_config: Dict[str, Any] = {}
    if config_path is not None:
        resolved_config.update(load_sirna_config(config_path))
    if config:
        resolved_config.update(config)

    api_host = str(resolved_config.get("api_host", "")).rstrip("/")
    api_key = str(resolved_config.get("api_key", ""))
    timeout = float(resolved_config.get("timeout", 10))

    if not api_host:
        raise ValueError("缺少 api_host 配置")
    if not api_key:
        raise ValueError("缺少 api_key 配置")

    url = f"{api_host}{WORKFLOW_LIST_ENDPOINT}"
    payload = {
        "apiKey": api_key,
        "requestTime": _utc_now_iso8601_ms(),
        "data": _workflow_list_data(
            workflow_type=workflow_type,
            filter_text=filter_text,
            include_detail=include_detail,
        ),
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    http_request = request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    result: Dict[str, Any] = {
        "url": url,
        "request_payload": payload,
    }

    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
            result["http_status"] = response.status
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        result["http_status"] = exc.code
    except Exception as exc:
        result["error"] = str(exc)
        return result

    try:
        result["response"] = json.loads(response_body)
    except ValueError:
        result["response"] = {"raw_text": response_body}

    return result


@device(
    id="bioyond_sirna_station",
    category=["workstation", "bioyond", "bioyond_sirna_station"],
    description="Bioyond 小核酸工作站",
    display_name="Bioyond Sirna Station",
    icon="preparation_station.webp",
)
class BioyondSirnaStation(BioyondWorkstation):
    """小核酸工作站最小运行时实现。"""

    _DEBUG_LOG_DEFAULT_DIR = "temp_benyao/sirna/_logs"

    def __init__(
        self,
        bioyond_config: Optional[Dict[str, Any]] = None,
        config: Optional[Any] = None,
        config_path: Optional[str | Path] = None,
        deck: Optional[Any] = None,
        protocol_type: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        if _BIOYOND_IMPORT_ERROR is not None:
            raise RuntimeError(f"BioyondSirnaStation 基类导入失败: {_BIOYOND_IMPORT_ERROR}") from _BIOYOND_IMPORT_ERROR

        kwargs.pop("children", None)
        merged_config: Dict[str, Any] = {}
        if config_path:
            merged_config.update(load_sirna_config(config_path))
        if isinstance(config, (str, Path)):
            merged_config.update(load_sirna_config(config))
        elif config:
            merged_config.update(config)
        if bioyond_config:
            merged_config.update(bioyond_config)
        merged_config.update(kwargs)
        _apply_default_sirna_material_type_mappings(merged_config)
        self._apply_env_api_config(merged_config)

        self.protocol_type = protocol_type
        self.bioyond_config = merged_config

        logger.info("BioyondSirnaStation 初始化开始")
        logger.info(f"  - API Host: {self.bioyond_config.get('api_host', '')}")
        logger.info(f"  - Workflow 映射数量: {len(self.bioyond_config.get('workflow_mappings', {}))}")

        missing_api_keys = self._missing_api_config_keys(self.bioyond_config)
        if missing_api_keys:
            logger.warning(
                "BioyondSirnaStation 缺少 Bioyond API 配置 %s，进入延迟初始化模式。"
                "请通过站点 graph/config 或环境变量补齐后再调用 RPC 动作。"
                "缺失项可来自 graph 节点 config，或环境变量 "
                "BIOYOND_SIRNA_API_HOST / BIOYOND_SIRNA_EXP1_API_HOST 与 "
                "BIOYOND_SIRNA_API_KEY / BIOYOND_SIRNA_EXP1_API_KEY。",
                missing_api_keys,
            )

        self._lazy_frontend_init = deck is None or bool(missing_api_keys)
        if self._lazy_frontend_init:
            WorkstationBase.__init__(self, deck=deck)
            self.is_running = False
            self.workflow_mappings = {}
            self.workflow_sequence = []
            self.pending_task_params = []
            self.http_service = None
            self.connection_monitor = None
            self._http_service_config = self.bioyond_config.get("http_service_config", {})
            if "workflow_mappings" in self.bioyond_config:
                self.workflow_mappings = dict(self.bioyond_config.get("workflow_mappings") or {})
            if deck is None:
                logger.warning(
                    "BioyondSirnaStation deck 未提供（graph 节点缺少 config.deck），"
                    "Bioyond 后台同步与 HTTP 服务暂不启动；动作将在补齐 deck 后才能完成 RPC 调用。"
                )
        else:
            super().__init__(bioyond_config=self.bioyond_config, deck=deck)

        # 「等待订单完成」节点使用的事件 + 最近一次推送上下文。
        # 必须在基类 HTTP 服务启动前赋值，避免 /report/order_finish 推送先到时字段未建造成 AttributeError。
        self.order_finish_event: threading.Event = threading.Event()
        self.last_order_code: Optional[str] = None
        self.last_order_report: Optional[Dict[str, Any]] = None
        self.last_used_materials: List[Any] = []

        # 甘特图回传：同 uuid 幂等去重 + 并发保护
        self._gantt_report_lock: threading.Lock = threading.Lock()
        self._gantt_reported_uuids: set[str] = set()

        logger.info("BioyondSirnaStation 初始化完成")

    @not_action
    def post_init(self, ros_node: Any) -> None:
        if getattr(self, "_lazy_frontend_init", False):
            WorkstationBase.post_init(self, ros_node)
            logger.warning(
                "BioyondSirnaStation 处于延迟模式：跳过 Bioyond 后台同步和 HTTP 服务启动。"
                "首次调用动作时会重新检查 api_host/api_key 与环境变量。"
            )
            return
        super().post_init(ros_node)

    @not_action
    def process_order_finish_report(
        self,
        report_request: Any,
        used_materials: Optional[List[Any]] = None,
    ) -> Any:
        """Override 基类 ``/report/order_finish`` 回调，做 orderCode 匹配 + 事件触发。

        必须先调用 ``super().process_order_finish_report()`` 以保留基类副作用（``_publish_task_status``
        推送 ROS 任务状态、status==30 时触发 ``resource_synchronizer.sync_from_external()`` 同步物料）。
        当推送的 ``orderCode`` 与 ``self.last_order_code`` 严格相等时 ``set()`` 事件，
        否则仅记日志，保证多 ``wait_for_order_finish`` 节点的隔离。
        """
        materials = list(used_materials or [])
        try:
            base_result = super().process_order_finish_report(report_request, materials)
        except Exception as exc:
            # 防御性兜底：基类异常不应吞掉事件触发，否则 wait 节点永远等不到结果。
            logger.error(
                f"[sirna] 基类 process_order_finish_report 抛错: {exc}",
                exc_info=True,
            )
            base_result = {"processed": False, "error": str(exc)}

        data = getattr(report_request, "data", None) or {}
        order_code = str(data.get("orderCode") or "")
        status = data.get("status")

        self.last_order_report = data
        self.last_used_materials = materials

        logger.info(
            f"[sirna] /report/order_finish 收到: orderCode={order_code} status={status} "
            f"expected={self.last_order_code!r} used_materials={len(materials)}"
        )

        if self.last_order_code and order_code == self.last_order_code:
            logger.info("[sirna] order_finish orderCode 匹配，触发 order_finish_event")
            self.order_finish_event.set()
        else:
            logger.info(
                f"[sirna] order_finish orderCode 不匹配当前等待项，仅记录 "
                f"(expected={self.last_order_code!r} got={order_code!r})"
            )
        return base_result

    def _debug_call_session(self, action_name: str):
        parent_debug_session = getattr(super(), "_debug_call_session", None)
        if parent_debug_session is not None:
            return parent_debug_session(action_name)
        return nullcontext()

    @staticmethod
    def _missing_api_config_keys(config: Dict[str, Any]) -> List[str]:
        missing: List[str] = []
        if BioyondSirnaStation._is_blank(config.get("api_host")):
            missing.append("api_host")
        if BioyondSirnaStation._is_blank(config.get("api_key")):
            missing.append("api_key")
        return missing

    def fetch_workflow_list(
        self,
        workflow_type: int = 0,
        filter_text: str = "",
        include_detail: bool = True,
    ) -> Dict[str, Any]:
        """通过 self.hardware_interface 拉取 Sirna LIMS 工作流列表。"""
        if not getattr(self, "hardware_interface", None):
            raise RuntimeError("Bioyond RPC 客户端未初始化")

        payload_data = _workflow_list_data(
            workflow_type=workflow_type,
            filter_text=filter_text,
            include_detail=include_detail,
        )
        logger.info("正在通过 Bioyond RPC 查询小核酸工作流列表")
        return self.hardware_interface.query_workflow(json.dumps(payload_data, ensure_ascii=False))

    @action(
        always_free=True,
        description="自动复位小核酸工作站状态（失败仅记录告警，不阻断流程）",
    )
    def reset_auto(
        self,
        reset_scheduler: bool = True,
        reset_order_status: bool = True,
        reset_location: bool = True,
        reset_devices: bool = False,
        sync_from_external_after_reset: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """按固定顺序执行选中的复位操作；自动模式下失败只返回告警。"""
        with self._debug_call_session("reset_auto"):
            del kwargs
            rpc = self._require_hardware_interface_for_reset()
            result = self._run_reset_operations(
                rpc,
                reset_scheduler=reset_scheduler,
                reset_order_status=reset_order_status,
                reset_location=reset_location,
                reset_devices=reset_devices,
                action_name="reset_auto",
            )
            self._maybe_sync_after_reset(
                result,
                sync_from_external_after_reset=sync_from_external_after_reset,
                manual_mode=False,
            )
            result["success"] = True
            return result

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
        goal_default={
            "reset_scheduler": True,
            "reset_order_status": True,
            "reset_location": True,
            "reset_devices": False,
            "sync_from_external_after_reset": False,
            "physical_cleanup_confirmed": False,
            "timeout_seconds": 3600,
            "assignee_user_ids": [],
        },
        feedback_interval=300,
        description="确认离心机配平板堆栈、G3移液站、自动化堆栈已清空，仪器内没有残留样品、耗材、试剂",
    )
    def reset_manual(
        self,
        reset_scheduler: bool = True,
        reset_order_status: bool = True,
        reset_location: bool = True,
        reset_devices: bool = False,
        sync_from_external_after_reset: bool = False,
        physical_cleanup_confirmed: bool = False,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """人工确认物理清空后复位；失败会在收集全部操作结果后阻断流程。"""
        with self._debug_call_session("reset_manual"):
            del kwargs
            result = self._empty_reset_result(
                action_name="reset_manual",
                reset_scheduler=reset_scheduler,
                reset_order_status=reset_order_status,
                reset_location=reset_location,
                reset_devices=reset_devices,
            )
            result["confirmation_message"] = RESET_PHYSICAL_CLEANUP_MESSAGE
            result["timeout_seconds"] = timeout_seconds
            result["assignee_user_ids"] = list(assignee_user_ids or [])
            if not self._as_manual_gate(physical_cleanup_confirmed):
                result["success"] = False
                result["blocked"] = True
                result["all_operations_successful"] = False
                result["skipped_operations"] = [
                    {
                        "key": operation["key"],
                        "label": operation["label"],
                        "reason": "manual_cleanup_not_confirmed",
                    }
                    for operation in RESET_OPERATION_DEFINITIONS
                ]
                result["warnings"].append({
                    "operation": "physical_cleanup_confirmed",
                    "reason": "manual_cleanup_not_confirmed",
                    "message": RESET_PHYSICAL_CLEANUP_MESSAGE,
                })
                return result

            rpc = self._require_hardware_interface_for_reset()
            result = self._run_reset_operations(
                rpc,
                reset_scheduler=reset_scheduler,
                reset_order_status=reset_order_status,
                reset_location=reset_location,
                reset_devices=reset_devices,
                action_name="reset_manual",
            )
            result["confirmation_message"] = RESET_PHYSICAL_CLEANUP_MESSAGE
            result["timeout_seconds"] = timeout_seconds
            result["assignee_user_ids"] = list(assignee_user_ids or [])
            self._maybe_sync_after_reset(
                result,
                sync_from_external_after_reset=sync_from_external_after_reset,
                manual_mode=True,
            )
            if not result["all_operations_successful"]:
                failed = [call["operation"] for call in result["executed_calls"] if not call.get("success")]
                raise RuntimeError(
                    "reset_manual 复位失败: "
                    f"failed_operations={failed}; details={result['executed_calls']}"
                )
            sync_result = result.get("external_material_sync")
            if (
                isinstance(sync_result, dict)
                and sync_result.get("sync_attempted")
                and not sync_result.get("success")
            ):
                raise RuntimeError(f"reset_manual 同步失败: {sync_result}")
            result["success"] = True
            return result

    @action(
        always_free=True,
        description="从 Bioyond 同步库存物料到本地资源树",
    )
    def sync_from_external(
        self,
        publish_resource_tree: bool = True,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """手动触发 Bioyond 外部物料同步。"""
        del kwargs
        with self._debug_call_session("sync_from_external"):
            return self._sync_from_external_and_optionally_publish(
                publish_resource_tree=publish_resource_tree,
                action_name="sync_from_external",
            )

    @action(
        always_free=True,
        description="只读查询 Bioyond 小核酸调度器状态",
    )
    def scheduler_status(
        self,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """直接调用 Bioyond 调度器状态接口。"""
        del kwargs
        with self._debug_call_session("scheduler_status"):
            rpc = self._require_hardware_interface("scheduler_status")
            status = rpc.scheduler_status()
            scheduler_status = status.get("schedulerStatus") if isinstance(status, dict) else None
            has_task = status.get("hasTask") if isinstance(status, dict) else None
            logger.info(
                "小核酸调度器状态查询完成: schedulerStatus=%s hasTask=%s",
                scheduler_status,
                has_task,
            )
            return {
                "success": bool(status),
                "scheduler_status": status if isinstance(status, dict) else {},
                "status": scheduler_status or "",
                "has_task": bool(has_task) if has_task is not None else False,
                "return_info": status,
            }

    @action(
        always_free=True,
        description="直接启动 Bioyond 小核酸调度器，不执行装载确认门禁",
    )
    def scheduler_start(
        self,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """直接调用 Bioyond 调度器启动接口。"""
        del kwargs
        return self._run_scheduler_action("scheduler_start", "启动")

    @action(
        always_free=True,
        description="手动确认后直接停止 Bioyond 小核酸调度器",
    )
    def scheduler_stop(
        self,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """直接调用 Bioyond 调度器停止接口。"""
        del kwargs
        return self._run_scheduler_action("scheduler_stop", "停止")

    @action(
        always_free=True,
        description="手动确认后直接暂停 Bioyond 小核酸调度器",
    )
    def scheduler_pause(
        self,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """直接调用 Bioyond 调度器暂停接口。"""
        del kwargs
        return self._run_scheduler_action("scheduler_pause", "暂停")

    @action(
        always_free=True,
        description="手动确认后直接继续 Bioyond 小核酸调度器",
    )
    def scheduler_continue(
        self,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """直接调用 Bioyond 调度器继续接口。"""
        del kwargs
        return self._run_scheduler_action("scheduler_continue", "继续")

    @action(
        always_free=True,
        goal_default={
            "order_id": "",
            "preintake_ids": [],
            "material_ids": [],
        },
        description="按订单取出 Bioyond LIMS 中已分配/预占的物料",
        handles=[
            ActionInputHandle(
                key="order_id",
                data_type="bioyond_order_id",
                label="实验ID",
                data_key="order_id",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionOutputHandle(
                key="order_id",
                data_type="bioyond_order_id",
                label="实验ID",
                data_key="order_id",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def take_out(
        self,
        order_id: str,
        preintake_ids: Optional[List[str]] = None,
        material_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """按订单调用 Bioyond take-out 接口。

        Args:
            order_id: Bioyond 实验 ID。
            preintake_ids: 可选预占记录 ID 列表；省略时传空列表。
            material_ids: 可选物料 ID 列表；省略时传空列表。
        """
        with self._debug_call_session("take_out"):
            del kwargs
            normalized_order_id = str(order_id or "").strip()
            if not normalized_order_id:
                raise ValueError("take_out 需要提供非空 order_id")
            normalized_preintake_ids = self._normalize_optional_string_list(
                preintake_ids,
                "preintake_ids",
            )
            normalized_material_ids = self._normalize_optional_string_list(
                material_ids,
                "material_ids",
            )
            rpc = self._require_hardware_interface("take_out")
            take_out_result = rpc.take_out(
                normalized_order_id,
                normalized_preintake_ids,
                normalized_material_ids,
            )
            logger.info(
                "小核酸 take_out 返回: order_id=%s preintakes=%s materials=%s result=%s",
                normalized_order_id,
                len(normalized_preintake_ids),
                len(normalized_material_ids),
                take_out_result,
            )
            normalized_result = self._normalize_service_result(take_out_result)
            return {
                "success": normalized_result["success"],
                "order_id": normalized_order_id,
                "preintake_ids": normalized_preintake_ids,
                "material_ids": normalized_material_ids,
                "take_out": take_out_result,
                "raw_result": take_out_result,
                "code": normalized_result.get("code"),
                "message": normalized_result.get("message", ""),
            }

    @action(
        always_free=True,
        goal_default={"order_codes": []},
        description="按实验编号批量取消 Bioyond 实验，仅调用批量取消接口，不执行 take_out",
        handles=[
            ActionInputHandle(
                key="order_codes",
                data_type="bioyond_order_codes",
                label="实验编号列表",
                data_key="order_codes",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
        ],
    )
    def batch_cancel_experiment(
        self,
        order_codes: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """按 orderCode 列表批量取消 Bioyond 实验。

        Args:
            order_codes: 非空 Bioyond 实验编号列表；该接口在 Sirna 中传 orderCode。
        """
        with self._debug_call_session("batch_cancel_experiment"):
            del kwargs
            normalized_order_codes = self._normalize_optional_string_list(
                order_codes,
                "order_codes",
            )
            if not normalized_order_codes:
                raise ValueError("取消实验需要提供非空 order_codes 列表；该接口在 Sirna 中传 orderCode 列表")
            rpc = self._require_hardware_interface("batch_cancel_experiment")
            code = rpc.batch_cancel_experiment(normalized_order_codes)
            logger.info(
                "小核酸批量取消实验返回: order_codes=%s code=%s",
                normalized_order_codes,
                code,
            )
            return {
                "success": code == 1,
                "order_codes": normalized_order_codes,
                "code": code,
                "message": "取消实验已提交" if code == 1 else "取消实验失败，请检查 LIMS 状态",
            }

    @action(
        always_free=True,
        description="提交小核酸实验1（报告基因检测）",
        handles=[
            ActionOutputHandle(
                key="order_id",
                data_type="bioyond_order_id",
                label="实验ID",
                data_key="order_id",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                # 兼容旧工作流：历史节点连接使用 order_ids。
                key="order_ids",
                data_type="bioyond_order_ids",
                label="实验ID列表",
                data_key="order_ids",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="resultTable",
                data_type="object",
                label="物料装载结果表",
                data_key="resultTable",
                data_source=DataSource.EXECUTOR,
                io_type="target",
            ),
        ],
    )
    def submit_experiment_1(
        self,
        required_params: Experiment1RequiredParams,
        optional_params: Optional[Experiment1OptionalParams] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """提交小核酸实验1（报告基因检测）到 Bioyond LIMS。

        自动查询实验1工作流参数，使用 API 默认值填充所有参数，
        创建订单并分配物料，最后将物料注册到 UniLabOS 资源树。

        Args:
            required_params: 必填参数组
                sample_throughput: 样品通量（1-96，必填），表示一次实验处理的样品数量。
            optional_params: 可选参数组
                order_name: 订单名称（可选，自动生成）
                parameter_overrides: 结构化参数覆盖列表
                auto_register_materials: 是否自动同步 Bioyond 物料到资源树（默认True）
            timeout_seconds: 超时时间（秒，框架参数）
            assignee_user_ids: 分配用户ID列表（框架参数）

        Returns:
            包含以下字段的字典:
            - success (bool): 是否成功
            - order_code (str): 订单编号
            - order_name (str): 订单名称
            - order_ids (List[str]): 订单ID列表
            - materials (List[Dict]): 物料记录列表
            - materials_by_type (Dict): 按类型分组的物料
            - confirmation_message (str): 确认消息
            - material_registration (Dict): 提交后物料同步摘要
        """
        optional_params = optional_params or {}
        if isinstance(required_params, dict):
            sample_throughput = required_params.get("sample_throughput")
        else:
            sample_throughput = required_params
        return self._submit_experiment_core(
            action_name="submit_experiment_1",
            experiment_number=1,
            workflow_name=SIRNA_EXPERIMENT_1_WORKFLOW_NAME,
            sub_workflow_name=SIRNA_EXPERIMENT_1_SUB_WORKFLOW_NAME,
            sample_throughput=int(sample_throughput),
            order_code=str(optional_params.get("order_code", "") or ""),
            order_name=str(optional_params.get("order_name", "") or ""),
            parameter_overrides=optional_params.get("parameter_overrides", []),
            auto_register_materials=bool(optional_params.get("auto_register_materials", True)),
            **kwargs,
        )

    @action(
        always_free=True,
        description="按工作流名称提交小核酸实验（通用入口，不暴露工作流 ID）",
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
                data_type="object",
                label="物料装载结果表",
                data_key="resultTable",
                data_source=DataSource.EXECUTOR,
                io_type="target",
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
        """按工作流名称提交小核酸实验到 Bioyond LIMS。"""
        optional_params = optional_params or {}
        workflow_name = ""
        sample_throughput: Any = None
        if isinstance(required_params, dict):
            workflow_name = str(required_params.get("workflow_name", "") or "")
            sample_throughput = required_params.get("sample_throughput")
        else:
            sample_throughput = required_params
        return self._submit_experiment_core(
            action_name="submit_experiment",
            experiment_number=None,
            workflow_name=workflow_name,
            sub_workflow_name=str(optional_params.get("sub_workflow_name", "") or ""),
            sample_throughput=int(sample_throughput),
            order_code=str(optional_params.get("order_code", "") or ""),
            order_name=str(optional_params.get("order_name", "") or ""),
            parameter_overrides=optional_params.get("parameter_overrides", []),
            auto_register_materials=bool(optional_params.get("auto_register_materials", True)),
            timeout_seconds=timeout_seconds,
            assignee_user_ids=assignee_user_ids,
            **kwargs,
        )

    @action(
        always_free=True,
        description="提交小核酸实验2（基因表达检测）",
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
                data_type="object",
                label="物料装载结果表",
                data_key="resultTable",
                data_source=DataSource.EXECUTOR,
                io_type="target",
            ),
        ],
    )
    def submit_experiment_2(
        self,
        required_params: Experiment2RequiredParams,
        optional_params: Optional[Experiment2OptionalParams] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """提交小核酸实验2（基因表达检测）到 Bioyond LIMS。"""
        optional_params = optional_params or {}
        if isinstance(required_params, dict):
            sample_throughput = required_params.get("sample_throughput")
        else:
            sample_throughput = required_params
        return self._submit_experiment_core(
            action_name="submit_experiment_2",
            experiment_number=2,
            workflow_name=SIRNA_EXPERIMENT_2_WORKFLOW_NAME,
            sub_workflow_name=SIRNA_EXPERIMENT_2_SUB_WORKFLOW_NAME,
            sample_throughput=int(sample_throughput),
            order_code=str(optional_params.get("order_code", "") or ""),
            order_name=str(optional_params.get("order_name", "") or ""),
            parameter_overrides=optional_params.get("parameter_overrides", []),
            auto_register_materials=bool(optional_params.get("auto_register_materials", True)),
            **kwargs,
        )

    def _submit_experiment_core(
        self,
        action_name: str,
        experiment_number: Optional[int],
        workflow_name: str,
        sub_workflow_name: str,
        sample_throughput: int,
        order_code: str,
        order_name: str,
        parameter_overrides: Any,
        auto_register_materials: bool,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        with self._debug_call_session(action_name):
            if self._is_blank(workflow_name):
                raise ValueError("提交实验必须提供 workflow_name（工作流名称），不能提供或依赖 workflow id")
            logger.info(
                "小核酸实验提交开始: action=%s experiment=%s workflow=%s sub_workflow=%s "
                "sample_throughput=%s auto_register_materials=%s overrides=%s",
                action_name,
                experiment_number,
                workflow_name,
                sub_workflow_name,
                sample_throughput,
                bool(auto_register_materials),
                bool(parameter_overrides),
            )

            rpc = self._require_hardware_interface("create_order")
            workflow = self._resolve_experiment_workflow(
                rpc,
                workflow_name=workflow_name,
                sub_workflow_name=sub_workflow_name,
            )
            logger.info(
                "小核酸实验工作流已解析: root=%s(%s) sub=%s(%s)",
                workflow.get("workflow_name", ""),
                workflow.get("root_workflow_id", ""),
                workflow.get("sub_workflow_name", ""),
                workflow.get("sub_workflow_id", ""),
            )

            step_data = rpc.workflow_step_query(workflow["sub_workflow_id"])
            param_values, parameter_template, override_warnings = self._build_param_values_from_step_data(
                step_data,
                parameter_overrides=parameter_overrides,
                include_all_task_displayable=False,
            )
            if not param_values:
                logger.error("小核酸实验参数构建失败: LIMS 子工作流未返回可用 paramValues")
                raise RuntimeError("未从 LIMS 子工作流参数中提取到 create_order paramValues")
            param_entry_count = sum(len(entries) for entries in param_values.values())
            logger.info(
                "小核酸实验参数已构建: steps=%s entries=%s template_items=%s overrides=%s",
                len(param_values),
                param_entry_count,
                len(parameter_template),
                len(self._parameter_override_items(parameter_overrides)),
            )

            resolved_order_code, resolved_order_name = self._build_bioyond_order_identity(
                experiment_number=experiment_number,
                order_code=order_code,
                order_name=order_name,
            )
            order_payload = [
                {
                    "orderCode": resolved_order_code,
                    "orderName": resolved_order_name,
                    "borderNumber": int(sample_throughput),
                    "workFlowId": workflow["sub_workflow_id"],
                    "paramValues": param_values,
                    "extendProperties": "",
                }
            ]

            logger.info("正在提交小核酸实验: %s (%s)", resolved_order_name, resolved_order_code)
            raw_result = rpc.create_order(json.dumps(copy.deepcopy(order_payload), ensure_ascii=False))
            parsed_result = self._parse_lims_result(raw_result)
            material_records = self._extract_create_order_materials(parsed_result)
            suggested_locations = self._extract_suggested_locations(material_records)
            order_ids = self._extract_created_order_ids(parsed_result)
            self._last_submitted_order_ids = list(order_ids)
            self._last_submitted_order_code = resolved_order_code
            start_experiment_info = {
                "order_ids": order_ids,
                "order_code": resolved_order_code,
                "order_name": resolved_order_name,
                "workflow": workflow,
            }
            confirmation_data = self._format_create_order_confirmation(
                order_code=resolved_order_code,
                order_name=resolved_order_name,
                workflow=workflow,
                order_ids=order_ids,
                material_records=material_records,
                suggested_locations=suggested_locations,
            )
            material_type_counts = {
                str(key): len(self._as_list(value))
                for key, value in confirmation_data.get("materials_by_type", {}).items()
            }
            create_success = self._create_result_success(parsed_result, order_ids, material_records)
            logger.info(
                "小核酸实验创建结果: success=%s order_ids=%s materials=%s "
                "suggested_locations=%s material_types=%s",
                create_success,
                order_ids,
                len(material_records),
                len(suggested_locations),
                material_type_counts,
            )
            if not create_success:
                logger.error(
                    "小核酸实验创建未返回成功结果: order_code=%s order_ids=%s materials=%s",
                    resolved_order_code,
                    order_ids,
                    len(material_records),
                )
            elif not material_records:
                logger.warning("小核酸实验创建成功但未返回物料分配记录: order_code=%s", resolved_order_code)
            elif not suggested_locations:
                logger.warning("小核酸实验创建成功但未解析到推荐库位: order_code=%s", resolved_order_code)

            warnings = list(override_warnings)
            material_registration, registration_warnings = self._submit_material_registration_summary(
                requested=bool(auto_register_materials),
                create_success=create_success,
                rpc=rpc,
            )
            warnings.extend(registration_warnings)

            result = {
                "success": create_success,
                "order_code": resolved_order_code,
                "order_name": resolved_order_name,
                "order_id": order_ids[0] if order_ids else "",
                "order_ids": order_ids,
                "workflow": workflow,
                "sample_throughput": int(sample_throughput),
                "payload": order_payload,
                "parameter_template": parameter_template,
                "create_order_result": parsed_result,
                "materials": material_records,
                "materials_by_type": confirmation_data.get("materials_by_type", {}),
                "resultTable": self._build_result_table(
                    confirmation_data.get("materials_by_type", {}),
                    table_name="物料放置指引",
                ),
                "suggested_locations": suggested_locations,
                "start_experiment": start_experiment_info,
                "confirmation_message": confirmation_data.get("confirmation_message", ""),
                "material_registration": material_registration,
                "warnings": warnings,
            }
            logger.info(
                "小核酸实验提交完成: action=%s order_code=%s success=%s result_rows=%s",
                action_name,
                resolved_order_code,
                result["success"],
                len(self._result_table_rows(result["resultTable"])),
            )
            return result

    def _submit_material_registration_summary(
        self,
        requested: bool,
        create_success: bool,
        rpc: Any,
    ) -> Tuple[Dict[str, Any], List[str]]:
        if not requested:
            return {
                "requested": False,
                "attempted": False,
                "success": None,
                "publish_resource_tree": True,
                "resource_tree_update_requested": False,
                "message": "未请求 Bioyond 物料同步",
            }, []
        if not create_success:
            return {
                "requested": True,
                "attempted": False,
                "success": False,
                "skipped": True,
                "publish_resource_tree": True,
                "resource_tree_update_requested": False,
                "message": "Bioyond 订单创建未成功，跳过物料同步",
            }, ["auto_register_materials_skipped_due_to_create_order_failure"]

        try:
            del rpc
            sync_result = self._sync_from_external_and_optionally_publish(
                publish_resource_tree=True,
                action_name="auto_register_materials.sync_from_external",
            )
        except Exception as exc:
            logger.warning("小核酸提交后 Bioyond 物料同步失败: %s", exc)
            return {
                "requested": True,
                "attempted": True,
                "success": False,
                "publish_resource_tree": True,
                "resource_tree_update_requested": False,
                "message": "Bioyond 资源同步失败",
                "error": str(exc),
            }, ["auto_register_materials_sync_failed"]

        success = bool(isinstance(sync_result, dict) and sync_result.get("success"))
        skipped = bool(isinstance(sync_result, dict) and sync_result.get("skipped"))
        summary = {
            "requested": True,
            "attempted": not skipped,
            "success": success,
            "publish_resource_tree": True,
            "resource_tree_update_requested": success,
            "message": "Bioyond 资源同步成功" if success else "Bioyond 资源同步失败",
        }
        if skipped:
            summary["skipped"] = True
            summary["message"] = "Bioyond 资源同步跳过"
        if isinstance(sync_result, dict) and sync_result.get("error"):
            summary["error"] = sync_result["error"]

        warnings: List[str] = []
        if skipped:
            warnings.append("auto_register_materials_sync_skipped")
        elif not success:
            warnings.append("auto_register_materials_sync_failed")
        return summary, warnings

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={
            "resultTable": "unilabos_manual_confirm",
            "assignee_user_ids": "unilabos_manual_confirm",
        },
        goal_default={
            "materials_loaded": False,
            "timeout_seconds": 3600,
            "assignee_user_ids": [],
        },
        feedback_interval=300,
        description="请核对并装载实验物料；勾选装载确认后方可启动调度",
        handles=[
            # Order metadata for scheduler start.
            ActionInputHandle(
                key="order_id", data_type="bioyond_order_id",
                label="实验ID", data_key="order_id",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionInputHandle(
                # 兼容旧工作流：历史节点连接使用 order_ids。
                key="order_ids",
                data_type="bioyond_order_ids",
                label="实验ID列表",
                data_key="order_ids",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionInputHandle(
                key="resultTable",
                data_type="object",
                label="物料装载结果表",
                data_key="resultTable",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            # plan §3.4 — 给下游 wait_for_order_finish / unload_materials 暴露订单 metadata。
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
                key="order_code",
                data_type="bioyond_order_code",
                label="订单编号",
                data_key="order_code",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def start_experiment(
        self,
        resultTable: Optional[Dict[str, Any]] = None,
        order_id: str = "",
        order_ids: Optional[List[str]] = None,
        materials_loaded: bool = False,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Guided manual-load checkpoint that gates ``rpc.scheduler_start()``.

        Args:
            resultTable: 上游句柄提供的新格式结果表（data/columns/tableName）。
            order_id: 上游 ``submit_experiment_1`` 创建的订单 ID（可选；若上游连接则自动传入）。
            order_ids: 上游 ``submit_experiment`` 创建的订单 ID 列表。
            materials_loaded: 操作员勾选确认物料已装载。未勾选且存在物料显示则阻断启动。
            timeout_seconds: 超时时间（秒，框架参数）。
            assignee_user_ids: 分配用户 ID 列表（框架参数）。
        """
        with self._debug_call_session("start_experiment"):
            del timeout_seconds, assignee_user_ids, kwargs

            category_arrays = {
                "materials_loaded": (
                    "物料",
                    self._as_manual_gate(materials_loaded),
                    [self._result_table_rows(resultTable)],
                ),
            }
            gates: Dict[str, Dict[str, Any]] = {}
            missing_labels: List[str] = []
            for gate_key, (label, ticked, arrays) in category_arrays.items():
                required = any(bool(arr) for arr in arrays)
                gates[gate_key] = {"label": label, "required": required, "ticked": bool(ticked)}
                if required and not ticked:
                    missing_labels.append(label)
            if missing_labels:
                logger.warning(
                    "小核酸实验启动存在未确认装载项，但当前动作按兼容策略继续: missing=%s",
                    missing_labels,
                )
            # if missing_labels:
            #     raise RuntimeError(
            #         f"以下分类装载尚未确认，无法启动调度: {', '.join(missing_labels)}"
            #     )

            resolved_order_ids: List[str] = []
            if order_id:
                resolved_order_ids.append(str(order_id))
            for candidate in self._as_list(order_ids):
                if candidate:
                    resolved_order_ids.append(str(candidate))
            resolved_order_ids = list(dict.fromkeys(resolved_order_ids))
            if not resolved_order_ids:
                raise RuntimeError("启动实验需要显式提供 order_id 或 order_ids")
            start_info = {
                "order_id": resolved_order_ids[0],
                "order_ids": resolved_order_ids,
                "resultTable": resultTable or {},
            }
            logger.info(
                "小核酸实验启动检查: order_id=%s order_ids=%s gates=%s missing=%s",
                start_info.get("order_id", ""),
                start_info.get("order_ids", []),
                gates,
                missing_labels,
            )
            rpc = self._require_hardware_interface("scheduler_start")
            logger.info("正在启动小核酸调度器")
            result = rpc.scheduler_start()
            logger.info("小核酸调度器启动返回: result=%s success=%s", result, result == 1)
            if result != 1:
                logger.error("小核酸调度器启动失败或返回非成功码: result=%s", result)
            return {
                "success": result == 1,
                "return_info": result,
                "scheduler_start_result": result,
                "order_id": resolved_order_ids[0],
                "order_ids": resolved_order_ids,
                # plan §3.4 — 下游 wait_for_order_finish 用 order_code 做 /report/order_finish 推送匹配。
                # 当前 start_experiment 不直接持有 order_code，留空字符串占位即可；wait 节点会通过
                # rpc.order_report(order_id) 兜底反查 code 字段。
                "order_code": "",
                "resultTable": resultTable or {},
                "start_experiment": start_info,
                "gates": gates,
                "confirmation_message": "调度器启动成功" if result == 1 else "调度器启动失败，请检查 LIMS 状态",
            }

    @action(
        always_free=True,
        goal_default={
            "order_id": "",
            "order_code": "",
            "timeout_seconds": 36000,
            "poll_mode": True,
            "poll_interval_seconds": 0.5,
        },
        description=(
            "阻塞等待奔耀通过 /report/order_finish 推送任务完成，"
            "并调用 /api/lims/storage/all-stock-material 整理「下料指引表」给下游节点。"
            "v1 仅等待单个订单：order_ids 长度>1 且未指定 order_id/order_code 时报错。"
        ),
        handles=[
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
            ActionInputHandle(
                key="order_code",
                data_type="bioyond_order_code",
                label="订单编号",
                data_key="order_code",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionOutputHandle(
                key="order_id",
                data_type="bioyond_order_id",
                label="实验ID",
                data_key="order_id",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="order_code",
                data_type="bioyond_order_code",
                label="订单编号",
                data_key="order_code",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="order_finish_status",
                data_type="string",
                label="完成状态",
                data_key="order_finish_status",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="order_finish_report",
                data_type="object",
                label="订单完成推送报文",
                data_key="order_finish_report",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="used_materials",
                data_type="array",
                label="使用物料列表",
                data_key="used_materials",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="all_stock_materials",
                data_type="array",
                label="实验台全部物料",
                data_key="all_stock_materials",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="resultTable",
                data_type="object",
                label="下料指引表",
                data_key="resultTable",
                data_source=DataSource.EXECUTOR,
                io_type="target",
            ),
        ],
    )
    def wait_for_order_finish(
        self,
        order_id: str = "",
        order_code: str = "",
        order_ids: Optional[List[str]] = None,
        timeout_seconds: int = 36000,
        poll_mode: bool = True,
        poll_interval_seconds: float = 0.5,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """阻塞等待奔耀订单完成推送，并整理「下料指引表」给下游节点。

        Args:
            order_id: 实验 UUID（用于调 all-stock-material 与 order_code 兜底反查）。
            order_code: 订单编号字符串（用于匹配 /report/order_finish 推送）；缺省时
                内部通过 ``rpc.order_report(order_id)`` 反查 ``code`` 字段。
            order_ids: 兼容 submit_experiment 多订单输出；当 ``order_id`` 为空且长度 == 1 时
                自动取第一个；长度 > 1 且未显式指定 ``order_id`` / ``order_code`` 时 ``raise``。
            timeout_seconds: 超时秒数；0 表示不限时（沿用 ``threading.Event.wait(timeout=None)``）。
            poll_mode: True 走 0.5s 轮询 + 超时（不挡 ROS2 feedback 派发线程），
                False 走单次 ``event.wait()``。
            poll_interval_seconds: 轮询间隔（仅 poll_mode 生效），测试用例可调小。

        Returns:
            含 ``success``/``order_id``/``order_code``/``order_finish_status``/``order_finish_report``/
            ``used_materials``/``all_stock_materials``/``resultTable``/``confirmation_message`` 的字典。
        """
        with self._debug_call_session("wait_for_order_finish"):
            del kwargs

            # 1) 解析 order_id：优先入参；缺省时尝试从 order_ids 取唯一一个。
            normalized_order_id = str(order_id or "").strip()
            candidates = [str(v).strip() for v in (order_ids or []) if str(v or "").strip()]
            if not normalized_order_id:
                if len(candidates) == 1:
                    normalized_order_id = candidates[0]
                elif len(candidates) > 1:
                    raise ValueError(
                        "wait_for_order_finish 在多 order_ids 场景下需要显式指定 order_id 或 order_code；"
                        f"当前 order_ids={candidates}"
                    )

            normalized_order_code = str(order_code or "").strip()
            if not normalized_order_id and not normalized_order_code:
                raise ValueError(
                    "wait_for_order_finish 需要提供 order_id 或 order_code（请连接上游 start_experiment 输出）"
                )

            # 2) 若仅有 order_id 没有 order_code，兜底反查（仅用于推送匹配，不参与 all-stock-material）。
            if not normalized_order_code and normalized_order_id:
                try:
                    rpc_for_report = self._require_hardware_interface("order_report")
                    report = rpc_for_report.order_report(normalized_order_id)
                    if isinstance(report, dict):
                        normalized_order_code = str(
                            report.get("code") or report.get("orderCode") or ""
                        ).strip()
                except Exception as exc:
                    logger.warning(
                        f"[sirna] wait_for_order_finish 反查 order_code 失败 "
                        f"(order_id={normalized_order_id}): {exc}"
                    )

            if not normalized_order_code:
                raise ValueError(
                    "wait_for_order_finish 无法解析 order_code（rpc.order_report 反查也失败）；"
                    "请显式传入 order_code 或确认 order_id 在 Bioyond LIMS 中存在"
                )

            # 3) 准备事件状态，必须在 last_order_code 赋值后再 clear()，避免基类回调竞态。
            self.last_order_code = normalized_order_code
            self.last_order_report = None
            self.last_used_materials = []
            self.order_finish_event.clear()

            logger.info(
                f"[sirna] wait_for_order_finish 开始等待: order_id={normalized_order_id} "
                f"order_code={normalized_order_code} timeout={timeout_seconds}s poll={poll_mode}"
            )

            # 4) 阻塞等待推送。timeout_seconds<=0 当作不限时。
            timeout_effective: Optional[float] = float(timeout_seconds) if timeout_seconds and timeout_seconds > 0 else None
            triggered = False
            if poll_mode:
                interval = max(float(poll_interval_seconds or 0.5), 0.001)
                deadline = (time.monotonic() + timeout_effective) if timeout_effective else None
                while True:
                    if self.order_finish_event.wait(timeout=interval):
                        triggered = True
                        break
                    if deadline is not None and time.monotonic() >= deadline:
                        break
            else:
                triggered = bool(self.order_finish_event.wait(timeout=timeout_effective))

            # 5) 解析推送状态。
            report = self.last_order_report or {}
            if not triggered:
                mapped_status = "timeout"
                logger.warning(
                    f"[sirna] wait_for_order_finish 超时: order_code={normalized_order_code}"
                )
            else:
                raw_status = str(report.get("status", "")).strip() if isinstance(report, dict) else ""
                if raw_status in ORDER_FINISH_STATUS_MAP:
                    mapped_status = ORDER_FINISH_STATUS_MAP[raw_status]
                elif raw_status:
                    mapped_status = f"unknown_{raw_status}"
                else:
                    mapped_status = "missing_status"

            # 6) 仅在 status 命中已知正常状态时拉取实验台物料；timeout / unknown / missing 不调。
            all_materials: List[Dict[str, Any]] = []
            if mapped_status in {"success", "abnormal_stop", "manual_stop"} and normalized_order_id:
                try:
                    rpc_for_stock = self._require_hardware_interface("all_stock_material")
                    payload = {"orderId": normalized_order_id}
                    raw = rpc_for_stock.all_stock_material(
                        json.dumps(payload, ensure_ascii=False)
                    )
                    if isinstance(raw, list):
                        all_materials = list(raw)
                except Exception as exc:
                    logger.error(
                        f"[sirna] wait_for_order_finish 调用 all_stock_material 失败: {exc}",
                        exc_info=True,
                    )

            # 7) 整理 resultTable（4 列 v2 结构）+ 序列化 used_materials。
            unload_rows = self._build_unload_rows_from_all_stock_material(all_materials)
            unload_table = self._build_unload_table(unload_rows)
            used_materials_serialized = [
                self._used_material_to_dict(item) for item in self.last_used_materials
            ]

            return {
                "success": mapped_status in {"success", "abnormal_stop", "manual_stop"},
                "order_id": normalized_order_id,
                "order_code": normalized_order_code,
                "order_finish_status": mapped_status,
                "order_finish_report": report if isinstance(report, dict) else {},
                "used_materials": used_materials_serialized,
                "all_stock_materials": all_materials,
                "resultTable": unload_table,
                "confirmation_message": (
                    f"任务完成: status={mapped_status}; 已整理 {len(unload_rows)} 行下料指引"
                ),
            }

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={
            "resultTable": "unilabos_manual_confirm",
            "assignee_user_ids": "unilabos_manual_confirm",
        },
        goal_default={
            "order_id": "",
            "timeout_seconds": 3600,
            "assignee_user_ids": [],
        },
        feedback_interval=300,
        description=(
            "展示上一节点 wait_for_order_finish 整理的下料指引表；"
            "操作员物理取出后点击确认通过，本节点即调用 "
            "/api/lims/order/take-out 通知奔耀下料完成（preintakeIds=[], materialIds=[]）。"
        ),
        handles=[
            ActionInputHandle(
                key="order_id",
                data_type="bioyond_order_id",
                label="实验ID",
                data_key="order_id",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionInputHandle(
                key="order_code",
                data_type="bioyond_order_code",
                label="订单编号",
                data_key="order_code",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionInputHandle(
                key="resultTable",
                data_type="object",
                label="下料指引表",
                data_key="resultTable",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionInputHandle(
                key="used_materials",
                data_type="array",
                label="使用物料列表",
                data_key="used_materials",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionInputHandle(
                key="order_finish_report",
                data_type="object",
                label="订单完成推送报文",
                data_key="order_finish_report",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionOutputHandle(
                key="success",
                data_type="boolean",
                label="是否成功",
                data_key="success",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="order_id",
                data_type="bioyond_order_id",
                label="实验ID",
                data_key="order_id",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="take_out_result",
                data_type="object",
                label="take-out 返回包",
                data_key="take_out_result",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def unload_materials(
        self,
        order_id: str = "",
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """人工下料确认节点：操作员点击确认通过后调用 ``take-out`` 通知奔耀。

        plan 决策：``take_out`` 形参恒为 ``(order_id, [], [])`` —— 不按物料挑选，
        由奔耀根据订单自己决定取出范围；本节点只负责"展示给人看 + 确认后通知"。

        Args:
            order_id: 上游 ``wait_for_order_finish`` 提供的订单 UUID（必填）。
            timeout_seconds: 框架超时时间（秒，本动作不读）。
            assignee_user_ids: 框架分配用户 ID 列表（本动作不读）。

        Returns:
            含 ``success`` / ``order_id`` / ``take_out_result`` / ``confirmation_message`` 的字典。
        """
        with self._debug_call_session("unload_materials"):
            del timeout_seconds, assignee_user_ids, kwargs

            normalized_order_id = str(order_id or "").strip()
            if not normalized_order_id:
                raise ValueError(
                    "unload_materials 需要 order_id（请连接 wait_for_order_finish.order_id 或显式传入）"
                )

            rpc = self._require_hardware_interface("take_out")
            logger.info(
                f"[sirna] unload_materials 调用 take_out: order_id={normalized_order_id}"
            )
            take_out_response = rpc.take_out(normalized_order_id, [], [])
            logger.info(
                f"[sirna] unload_materials take_out 返回: {take_out_response}"
            )

            if isinstance(take_out_response, dict):
                success = take_out_response.get("code") == 1
                message = str(take_out_response.get("message", "") or "")
                normalized_response: Dict[str, Any] = dict(take_out_response)
            else:
                success = False
                message = ""
                normalized_response = {}

            return {
                "success": bool(success),
                "order_id": normalized_order_id,
                "take_out_result": normalized_response,
                "confirmation_message": (
                    "下料确认，已通知奔耀 take-out 成功"
                    if success
                    else f"下料确认，但 take-out 返回失败/异常，请检查 LIMS 状态: {message}"
                ),
            }

    # ------------------------------------------------------------------
    # 节点 m：计算实验二结果并写入实验记录本
    # ------------------------------------------------------------------

    def _resolve_notebook_id(self, action_name: str) -> str:
        """从 device_manager 反查当前 job 的 notebook_id(不改框架)。

        notebook_id 不进 ROS goal，但活在 ``DeviceActionManager.JobInfo``；
        本节点与 ws client 同进程，经 ``get_communication_client().device_manager``
        按 ``device_action_key`` 命中当前 active job。always_free 节点走
        ``get_active_jobs()`` 兜底。取不到返回空串(由调用方决定是否中止)。
        """
        try:
            from unilabos.app.communication import get_communication_client

            client = get_communication_client()
            dm = getattr(client, "device_manager", None)
            device_id = getattr(getattr(self, "_ros_node", None), "device_id", None)
            if dm is None or not device_id:
                logger.warning(
                    f"[sirna] _resolve_notebook_id 无法取得 device_manager/device_id "
                    f"(device_id={device_id})"
                )
                return ""
            key = f"/devices/{device_id}/{action_name}"
            job = dm.active_jobs.get(key)
            if job is None:
                try:
                    job = next(
                        (j for j in dm.get_active_jobs() if getattr(j, "device_action_key", "") == key),
                        None,
                    )
                except Exception:  # pragma: no cover - 兜底
                    job = None
            return (getattr(job, "notebook_id", "") or "") if job is not None else ""
        except Exception as exc:  # pragma: no cover - 反查失败不应阻断主流程之外的判断
            logger.warning(f"[sirna] _resolve_notebook_id 异常: {exc}")
            return ""

    @staticmethod
    def _parse_report_files(report_file: Any) -> List[str]:
        """把 reportFile 归一成相对路径列表(兼容 list 或 ;,/换行 分隔的字符串)。"""
        if isinstance(report_file, (list, tuple)):
            items = [str(p) for p in report_file]
        elif isinstance(report_file, str):
            items = report_file.replace(";", ",").replace("\n", ",").split(",")
        else:
            items = []
        return [p.strip() for p in items if str(p).strip()]

    @staticmethod
    def _extract_report_files(report: Dict[str, Any]) -> List[str]:
        """从 order_report data 提取 reportFile 相对路径列表。

        兼容：``extraProperties`` 为 dict 或 JSON 字符串；``reportFile`` 也可能直接挂顶层。
        """
        extra = report.get("extraProperties")
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        if not isinstance(extra, dict):
            extra = {}
        report_file = extra.get("reportFile")
        if report_file in (None, "", []):
            report_file = report.get("reportFile")
        return BioyondSirnaStation._parse_report_files(report_file)

    @staticmethod
    def _locate_report_file(host_prefix: str, rel: str) -> str:
        """把 LIMS 相对路径拼到本地前缀(默认 D:\\bioyond_rb\\host)。"""
        rel_norm = str(rel).strip().replace("\\", "/").lstrip("/")
        parts = [p for p in rel_norm.split("/") if p]
        return os.path.join(host_prefix, *parts) if parts else host_prefix

    def _poll_order_report(
        self,
        order_id: str,
        poll_interval_seconds: float,
        poll_timeout_seconds: float,
    ) -> Dict[str, Any]:
        """轮询 rpc.order_report 直到 status==80，返回报告 data 字典。"""
        rpc = self._require_hardware_interface("order_report")
        interval = max(float(poll_interval_seconds or 10.0), 0.5)
        deadline = (
            time.monotonic() + float(poll_timeout_seconds)
            if poll_timeout_seconds and poll_timeout_seconds > 0
            else None
        )
        while True:
            report = rpc.order_report(order_id)
            status = report.get("status") if isinstance(report, dict) else None
            if status in (80, "80"):
                logger.info(f"[sirna] order_report 命中 status=80 (order_id={order_id})")
                return report
            logger.info(
                f"[sirna] order_report 轮询中 order_id={order_id} status={status}，"
                f"{interval}s 后重试"
            )
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(
                    f"order_report 轮询超时(order_id={order_id}, 末次 status={status})"
                )
            time.sleep(interval)

    @action(
        goal_default={
            "order_id": "",
            "notebook_id": "",
            "host_prefix": r"D:\bioyond_rb\host",
            "poll_interval_seconds": 10.0,
            "poll_timeout_seconds": 3600,
            "plate_map_path": "",
            "archive_raw_files": True,
        },
        feedback_interval=300,
        description=(
            "计算实验二结果并写入实验记录本：轮询 order-report 直到 status==80 拿到 "
            "reportFile 两个文件(.csv=RNA / .xml=qPCR)，定位本地文件后生成 RNA 浓度检测"
            "(原生表格) 与 qPCR 扩增曲线(图片，传 OSS)，再追加到 notebook 的 lab_record。"
            "order_id 可连 wait_for_order_finish 或手填；notebook_id 留空自动反查当前记录本，"
            "单独测试时可手填指定写入哪本。"
        ),
        handles=[
            ActionInputHandle(
                key="order_id",
                data_type="bioyond_order_id",
                label="实验ID",
                data_key="order_id",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionOutputHandle(
                key="success",
                data_type="boolean",
                label="是否成功",
                data_key="success",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="notebook_id",
                data_type="string",
                label="记录本ID",
                data_key="notebook_id",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="image_urls",
                data_type="array",
                label="图片URL列表",
                data_key="image_urls",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def compute_experiment2_result(
        self,
        order_id: str = "",
        notebook_id: str = "",
        host_prefix: str = r"D:\bioyond_rb\host",
        poll_interval_seconds: float = 10.0,
        poll_timeout_seconds: int = 3600,
        plate_map_path: str = "",
        archive_raw_files: bool = True,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """计算实验二结果(RNA 表格 + qPCR 曲线图)并写入实验记录本。

        Args:
            order_id: 实验订单 UUID(连上游 wait_for_order_finish.order_id，亦可手填)。
            notebook_id: 目标记录本 UUID；**留空则自动从当前 job 反查(生产默认)**。
                单独测试时可手填，把结果写进指定记录本。
            host_prefix: 报告文件本地前缀(工作站固定盘符路径)。
            poll_interval_seconds: order_report 轮询间隔秒。
            poll_timeout_seconds: order_report 轮询超时秒(<=0 不限时)。
            plate_map_path: 96 孔样本映射 xlsx；缺省用 gen_report.DEFAULT_MAP。
            archive_raw_files: 是否把原始 csv/xml 作为附件(file)归档进记录本。

        Returns:
            含 success / order_id / notebook_id / image_urls / confirmation_message 的字典。
        """
        import tempfile
        from datetime import datetime as _dt

        from unilabos.devices.workstation.bioyond_studio.sirna_station import (
            notebook_client as nbc,
        )
        from unilabos.script import gen_report

        with self._debug_call_session("compute_experiment2_result"):
            del kwargs

            normalized_order_id = str(order_id or "").strip()
            if not normalized_order_id:
                raise ValueError(
                    "compute_experiment2_result 需要 order_id(请连接 wait_for_order_finish.order_id)"
                )

            # 1) 取 notebook_id：优先手填(便于单独测试)，否则 device_manager 反查；
            #    取不到则中止，不静默写空记录本。
            notebook_id = str(notebook_id or "").strip()
            if not notebook_id:
                notebook_id = self._resolve_notebook_id("compute_experiment2_result")
            if not notebook_id:
                raise ValueError(
                    "compute_experiment2_result 未取到 notebook_id(未手填且 device_manager "
                    "当前无匹配 active job)；中止写记录本。手动测试请显式传入 notebook_id。"
                )

            # 2) 轮询 order_report 直到 status==80，取 reportFile 两路径
            report = self._poll_order_report(
                normalized_order_id, poll_interval_seconds, poll_timeout_seconds
            )
            rel_files = self._extract_report_files(report)
            if not rel_files:
                raise RuntimeError(
                    f"order_report(status=80) 未解析到 reportFile；report keys={list(report.keys())}"
                )

            csv_path = xml_path = ""
            for rel in rel_files:
                local = self._locate_report_file(host_prefix, rel)
                ext = os.path.splitext(local)[1].lower()
                if ext == ".csv":
                    csv_path = local
                elif ext == ".xml":
                    xml_path = local
            logger.info(
                f"[sirna] 实验二报告文件定位: csv={csv_path or '<无>'} xml={xml_path or '<无>'}"
            )
            if not csv_path or not os.path.exists(csv_path):
                raise FileNotFoundError(f"RNA(.csv) 报告文件缺失或不存在: {csv_path}")
            if not xml_path or not os.path.exists(xml_path):
                raise FileNotFoundError(f"qPCR(.xml) 报告文件缺失或不存在: {xml_path}")

            # 3) 生成数据/图片
            plate_map = str(plate_map_path or "").strip() or gen_report.DEFAULT_MAP
            out_dir = os.path.join(tempfile.gettempdir(), "sirna_exp2", normalized_order_id)
            os.makedirs(out_dir, exist_ok=True)

            rna = gen_report.build_rna_table(csv_path, plate_map_path=plate_map)
            qpcr_png = os.path.join(out_dir, "qpcr_amp_curves.png")
            gen_report.render_qpcr_curve_image(xml_path, qpcr_png, plate_map_path=plate_map)

            # 4) qPCR 图片走 OSS(img 节点需 url)
            img_meta = nbc.upload_to_oss(qpcr_png, scene="image", content_type="image/png")

            # 5) 构造记录本块：RNA 原生表格 + qPCR 图片(+ 可选原始文件归档)
            stamp = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
            blocks: List[Dict[str, Any]] = [
                nbc.text_block(f"实验二结果(自动生成 {stamp}, order_id={normalized_order_id})"),
                nbc.text_block("一、RNA 浓度检测"),
                nbc.build_table_node(rna["header"], rna["rows"]),
                nbc.text_block("二、qPCR 扩增曲线"),
                nbc.build_image_node(img_meta),
            ]

            if archive_raw_files:
                blocks.append(nbc.text_block("三、原始数据附件"))
                for raw_path, scene, ctype in (
                    (csv_path, "file", "text/csv"),
                    (xml_path, "file", "application/xml"),
                ):
                    try:
                        meta = nbc.upload_to_oss(raw_path, scene=scene, content_type=ctype)
                        blocks.append(nbc.build_file_node(meta))
                    except Exception as exc:
                        logger.warning(f"[sirna] 原始文件归档上传失败({raw_path}): {exc}")

            # 末尾补一个空段落：img/file 是 void 节点，作为末块时前端难以在其后落光标继续编辑。
            blocks.append(nbc.text_block(""))

            # 6) 追加到记录本并保存(写前校验 editing)
            result = nbc.append_blocks_to_notebook(notebook_id, blocks)
            logger.info(
                f"[sirna] 实验二结果已写入记录本 notebook_id={notebook_id} "
                f"appended={result['appended']} total={result['total']}"
            )

            return {
                "success": True,
                "order_id": normalized_order_id,
                "notebook_id": notebook_id,
                "image_urls": [img_meta.get("url", "")],
                "confirmation_message": (
                    f"实验二结果已写入记录本 {notebook_id}: RNA 表格 {len(rna['rows'])} 行 + qPCR 曲线图"
                ),
            }

    @action(
        always_free=True,
        goal_default={
            "status": "全部（\"\"）",
            "max_results": 10,
            "filter_text": "",
            "sorting": "creationTime desc",
            "skipCount": 0,
            "timeType": "",
            "beginTime": None,
            "endTime": None,
            "latest_only": True,
        },
        description=(
            "只读查询 Bioyond LIMS 订单列表。"
            "status 必填：全部（\"\"）/成功（80）/失败（90）/执行中（60）/已取出（100）。"
            "max_results 对应 pageCount，默认 10。其余查询条件可选。"
        ),
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
                key="order_code",
                data_type="bioyond_order_code",
                label="实验编号",
                data_key="order_code",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="order_codes",
                data_type="bioyond_order_codes",
                label="实验编号列表",
                data_key="order_codes",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def get_order_list(
        self,
        status: OrderStatus,
        max_results: int = 10,
        filter_text: str = "",
        sorting: str = "creationTime desc",
        skipCount: int = 0,
        timeType: str = "",
        beginTime: Optional[str] = None,
        endTime: Optional[str] = None,
        latest_only: bool = True,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """只读查询 Bioyond LIMS 订单列表。

        Args:
            status: 订单状态下拉值，映射到 Bioyond ``order_query.status``。
            max_results: 一次返回的最大订单条数，对应 ``pageCount``，默认 10。
            filter_text: 订单编号 / 名称模糊匹配字符串，对应 LIMS ``order_query.filter``。
            sorting: 排序字段，默认 ``creationTime desc``。
            skipCount: 跳过条数，对应 Bioyond ``skipCount``。
            timeType: 查询时间类型，可选 CreationTime 或 FinishedTime；留空表示不限定，实测没啥用。
            beginTime: 开始时间，可选。时间格式示例 2026-05-26T13:50:54.742373，实测没啥用。
            endTime: 结束时间，可选。时间格式示例 2026-05-26T13:50:54.742373，必须大于 beginTime，实测没啥用。
            latest_only: 默认 ``True``，仅返回最新（创建时间最大）的一条订单作为 ``order_id``。

        Returns:
            ``{"success": bool, "orders": [...], "order_id": str, "order_ids": [...], "order_code": str, "order_codes": [...], "query": {...}}``。
        """
        with self._debug_call_session("get_order_list"):
            del kwargs
            try:
                normalized_max = int(max_results)
            except (TypeError, ValueError):
                normalized_max = 10
            if normalized_max <= 0:
                normalized_max = 10
            try:
                normalized_skip = int(skipCount or 0)
            except (TypeError, ValueError):
                normalized_skip = 0
            normalized_skip = max(0, normalized_skip)
            status_text = str(status)
            if status_text not in ORDER_STATUS_VALUE_MAP:
                raise ValueError(f"未知订单状态: {status_text!r}; 支持 {list(ORDER_STATUS_VALUE_MAP)}")
            rpc = self._require_hardware_interface("order_query")
            query_payload = {
                "timeType": str(timeType or ""),
                "beginTime": str(beginTime).strip() if beginTime else None,
                "endTime": str(endTime).strip() if endTime else None,
                "status": ORDER_STATUS_VALUE_MAP[status_text],
                "filter": str(filter_text or ""),
                "skipCount": normalized_skip,
                "pageCount": normalized_max,
                "sorting": str(sorting or "creationTime desc"),
            }
            logger.info(
                "正在查询 Bioyond LIMS 订单列表 filter=%r status=%r latest_only=%s",
                filter_text,
                status_text,
                latest_only,
            )
            raw_result = rpc.order_query(json.dumps(query_payload, ensure_ascii=False))
            items = self._order_items(raw_result)

            orders: List[Dict[str, Any]] = []
            warnings: List[str] = []
            for item in items:
                order_id = str(item.get("id") or "")
                if not order_id:
                    continue
                order_code = str(item.get("orderCode") or "").strip()
                normalized_order = {
                    "order_id": order_id,
                    "order_code": order_code,
                    "order_name": str(item.get("name") or item.get("orderName") or ""),
                    "status": str(item.get("status") or item.get("statusName") or ""),
                    "created_at": str(item.get("creationTime") or item.get("createTime") or ""),
                    "raw": item,
                }
                if not order_code:
                    warning = f"order {order_id} 缺少 orderCode，已从 order_codes 输出中省略"
                    warnings.append(warning)
                    normalized_order["missing_order_code"] = True
                    logger.warning("Bioyond LIMS 订单缺少 orderCode: order_id=%s raw=%s", order_id, item)
                orders.append({
                    **normalized_order,
                })

            order_ids = [order["order_id"] for order in orders]
            order_codes = [order["order_code"] for order in orders if order.get("order_code")]
            if latest_only and orders:
                chosen = orders[0]
                order_id_value = chosen["order_id"]
                order_code_value = str(chosen.get("order_code") or "")
            elif len(order_ids) == 1:
                order_id_value = order_ids[0]
                order_code_value = order_codes[0] if len(order_codes) == 1 else ""
            else:
                order_id_value = ""
                order_code_value = ""
            logger.info(
                "Bioyond LIMS 订单列表查询完成: raw_items=%s orders=%s selected_order_id=%s selected_order_code=%s",
                len(items),
                len(orders),
                order_id_value,
                order_code_value,
            )
            if not orders:
                logger.warning(
                    "Bioyond LIMS 订单列表未查询到结果: filter=%r status=%r",
                    filter_text,
                    status_text,
                )

            result = {
                "success": bool(orders),
                "orders": orders,
                "order_id": order_id_value,
                "order_ids": order_ids,
                "order_code": order_code_value,
                "order_codes": order_codes,
                "query": query_payload,
            }
            if warnings:
                result["warnings"] = warnings
            return result

    @action(
        always_free=True,
        goal_default={"order_id": ""},
        description="只读查询 Bioyond LIMS 订单报告",
        handles=[
            ActionOutputHandle(
                key="order_id",
                data_type="bioyond_order_id",
                label="实验ID",
                data_key="order_id",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="report",
                data_type="bioyond_order_report",
                label="订单报告",
                data_key="report",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def get_order_report(
        self,
        order_id: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """只读查询 Bioyond LIMS 订单报告。

        Args:
            order_id: Bioyond LIMS 订单 ID。

        Returns:
            ``{"success": bool, "order_id": str, "report": {...}, "raw": ...}``。
        """
        with self._debug_call_session("get_order_report"):
            del kwargs
            normalized_order_id = str(order_id or "").strip()
            if not normalized_order_id:
                raise ValueError("order_id 不能为空")

            rpc = self._require_hardware_interface("order_report")
            logger.info("正在查询 Bioyond LIMS 订单报告 order_id=%s", normalized_order_id)
            raw_result = rpc.order_report(normalized_order_id, return_envelope=True)
            result = self._normalize_order_report_result(normalized_order_id, raw_result)
            report_data = result.get("report")
            logger.info(
                "Bioyond LIMS 订单报告查询完成: order_id=%s success=%s report_keys=%s",
                result.get("order_id", normalized_order_id),
                result.get("success"),
                list(report_data.keys()) if isinstance(report_data, dict) else type(report_data).__name__,
            )
            if not result.get("success"):
                logger.warning(
                    "Bioyond LIMS 订单报告未返回可用数据: order_id=%s message=%s",
                    normalized_order_id,
                    result.get("message", ""),
                )
            return result

    @action(
        always_free=True,
        goal_default={
            "order_id": "",
            "filter_text": "",
            "include_order_list": True,
            "include_order_report": True,
            "include_gantt": True,
            "include_gantt_with_simulation": True,
            "include_material_info": True,
            "include_raw": True,
        },
        description="聚合 Bioyond LIMS 前端样式订单报告",
        handles=[
            ActionOutputHandle(
                key="order_id",
                data_type="bioyond_order_id",
                label="实验ID",
                data_key="order_id",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="report",
                data_type="bioyond_frontend_like_order_report",
                label="聚合订单报告",
                data_key="report",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def get_aggregated_order_report(
        self,
        order_id: str = "",
        filter_text: str = "",
        include_order_list: bool = True,
        include_order_report: bool = True,
        include_gantt: bool = True,
        include_gantt_with_simulation: bool = True,
        include_material_info: bool = True,
        include_raw: bool = True,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """聚合 Bioyond LIMS 前端样式订单报告。

        Args:
            order_id: Bioyond LIMS 订单 ID。为空时用 ``filter_text`` 从订单列表解析。
            filter_text: 订单名称 / 编号 / ID 过滤文本。
            include_order_list: 是否查询订单列表摘要。
            include_order_report: 是否查询订单报告详情。
            include_gantt: 是否允许普通甘特图作为时间线回退。
            include_gantt_with_simulation: 是否优先查询合并模拟甘特图。
            include_material_info: 是否对订单物料 ID 查询物料详情。
            include_raw: 是否返回各分段原始响应和错误。

        Returns:
            前端报告调试结构，包含 header、parameters、samples、materials、timeline、raw/errors。
        """
        with self._debug_call_session("get_aggregated_order_report"):
            del kwargs

            requested_order_id = str(order_id or "").strip()
            query_filter = str(filter_text or requested_order_id or "").strip()
            if not requested_order_id and not query_filter:
                raise ValueError("order_id 和 filter_text 不能同时为空")

            rpc = self._require_hardware_interface_for_reset()
            included_sections = {
                "order_list": bool(include_order_list),
                "order_report": bool(include_order_report),
                "gantt": bool(include_gantt),
                "gantt_with_simulation": bool(include_gantt_with_simulation),
                "material_info": bool(include_material_info),
                "raw": bool(include_raw),
            }
            logger.info(
                "聚合订单报告查询开始: order_id=%s filter=%s sections=%s",
                requested_order_id,
                query_filter,
                included_sections,
            )
            raw_sections: Dict[str, Any] = {}
            errors: List[str] = []
            warnings: List[str] = []

            order_list_payload: Any = None
            order_items: List[Dict[str, Any]] = []
            if include_order_list:
                query_payload = self._order_list_query_payload(query_filter)
                order_list_payload = self._call_order_query_for_report(
                    rpc,
                    query_payload,
                    errors,
                )
                if include_raw:
                    raw_sections["order_list"] = order_list_payload
                self._append_lims_section_error(order_list_payload, errors, "order_list")
                order_items = self._order_items(order_list_payload)

            order_record = self._select_order_record(order_items, requested_order_id, query_filter)
            if requested_order_id and order_items and order_record is None:
                warnings.append("order_list 未返回与 order_id 完全匹配的订单，跳过订单列表摘要合并")
            resolved_order_id = requested_order_id
            if order_record is not None and order_record.get("id"):
                resolved_order_id = str(order_record["id"])
            if not resolved_order_id:
                raise RuntimeError("无法解析 Bioyond 订单 ID，请提供 order_id 或可命中的 filter_text")
            logger.info(
                "聚合订单报告已解析订单: resolved_order_id=%s order_list_items=%s matched=%s",
                resolved_order_id,
                len(order_items),
                bool(order_record),
            )

            report_payload: Any = None
            primary_report_failed = False
            normalized_report: Dict[str, Any] = {
                "success": False,
                "order_id": resolved_order_id,
                "report": {},
                "raw": {},
            }
            if include_order_report:
                report_payload = self._call_single_arg_lims_section(
                    rpc,
                    "order_report",
                    resolved_order_id,
                    errors,
                    "order_report",
                )
                normalized_report = self._normalize_order_report_result(resolved_order_id, report_payload)
                if not normalized_report.get("success"):
                    primary_report_failed = True
                    message = str(normalized_report.get("message") or "order_report 未返回可用 data")
                    logger.error(
                        "Bioyond LIMS order_report 调用失败 order_id=%s: %s",
                        resolved_order_id,
                        message,
                    )
                    errors.append(f"order_report: {message}")
                if include_raw:
                    raw_sections["order_report"] = report_payload

            report_data = normalized_report.get("report")
            if not isinstance(report_data, dict):
                report_data = {}

            timeline: List[Dict[str, Any]] = []
            timeline_source = ""
            if include_gantt_with_simulation:
                gantt_payload = self._call_single_arg_lims_section(
                    rpc,
                    "gantt_with_simulation_by_order_id",
                    resolved_order_id,
                    errors,
                    "gantt_with_simulation",
                )
                if include_raw:
                    raw_sections["gantt_with_simulation"] = gantt_payload
                self._append_lims_section_error(gantt_payload, errors, "gantt_with_simulation")
                timeline = self._gantt_items(gantt_payload)
                if timeline:
                    timeline_source = "gantt_with_simulation"
            if not timeline and include_gantt:
                gantt_payload = self._call_single_arg_lims_section(
                    rpc,
                    "gantts_by_order_id",
                    resolved_order_id,
                    errors,
                    "gantt",
                )
                if include_raw:
                    raw_sections["gantt"] = gantt_payload
                self._append_lims_section_error(gantt_payload, errors, "gantt")
                timeline = self._gantt_items(gantt_payload)
                if timeline:
                    timeline_source = "gantt"
            if (include_gantt or include_gantt_with_simulation) and not timeline:
                warnings.append("未获取到甘特图时间线")

            material_ids = self._order_material_ids(order_record, report_data)
            logger.info("聚合订单报告物料详情查询准备: material_ids=%s", len(material_ids))
            material_info_by_id: Dict[str, Any] = {}
            if include_material_info:
                for material_id in material_ids:
                    material_payload = self._call_single_arg_lims_section(
                        rpc,
                        "material_info",
                        material_id,
                        errors,
                        f"material_info:{material_id}",
                    )
                    material_data = self._service_data_or_value(material_payload)
                    if isinstance(material_data, dict):
                        material_info_by_id[material_id] = material_data
                    if include_raw:
                        raw_sections.setdefault("material_info", {})[material_id] = material_payload
                    self._append_lims_section_error(material_payload, errors, f"material_info:{material_id}")

            header = self._build_aggregated_report_header(order_record, report_data)
            if not header.get("order_id"):
                header["order_id"] = resolved_order_id
            parameters = self._build_aggregated_report_parameters(report_data, warnings)
            samples = self._build_aggregated_report_samples(order_record, report_data, material_info_by_id)
            reagents, consumables = self._split_used_materials(report_data)

            success = bool(order_record or report_data or timeline or samples) and not primary_report_failed
            logger.info(
                "聚合订单报告查询完成: order_id=%s success=%s samples=%s reagents=%s "
                "consumables=%s timeline=%s source=%s errors=%s warnings=%s",
                resolved_order_id,
                success,
                len(samples),
                len(reagents),
                len(consumables),
                len(timeline),
                timeline_source,
                len(errors),
                len(warnings),
            )
            result: Dict[str, Any] = {
                "success": success,
                "order_id": resolved_order_id,
                "order_code": header.get("code", ""),
                "order_name": header.get("name", ""),
                "included_sections": included_sections,
                "header": header,
                "parameters": parameters,
                "samples": samples,
                "reagents": reagents,
                "consumables": consumables,
                "timeline": timeline,
                "timeline_source": timeline_source,
                "section_errors": errors,
                "errors": errors,
                "warnings": warnings,
            }
            if include_raw:
                result["raw"] = raw_sections
            result["report"] = {
                "header": header,
                "parameters": parameters,
                "samples": samples,
                "reagents": reagents,
                "consumables": consumables,
                "timeline": timeline,
            }
            return result

    @not_action
    def report_gantt_by_order(self, uuid: str) -> None:
        """收到 scheduler ``device_info`` 触发后，后台一次性拉甘特并回传后端。

        通过 ``get_order_list(status="执行中（60）", latest_only=False)`` **实时查询** LIMS 所有正在
        执行的订单，逐个调 ``gantt_with_simulation_by_order_id`` 取**完整原始响应**，汇总成一个数组，
        **只 POST 一次**（body 的 ``data`` 为该数组，每个元素是一个订单甘特接口的原始响应）。当前无
        执行中订单时记日志跳过。``uuid`` 仅作回传 body 的 ``uuid`` 字段。整个过程在后台 daemon 线程
        执行，绝不阻塞调用方（ws 消息循环），异常只记日志不外抛。
        """
        from unilabos.config.config import GanttReportConfig

        if not GanttReportConfig.enabled:
            logger.info("甘特图回传已禁用(GanttReportConfig.enabled=False)，跳过 uuid=%s", uuid)
            return

        normalized_uuid = str(uuid or "").strip()
        if not normalized_uuid:
            logger.error("report_gantt_by_order 缺少 uuid，跳过")
            return

        with self._gantt_report_lock:
            if normalized_uuid in self._gantt_reported_uuids:
                logger.info("甘特图回传 uuid=%s 已触发过，跳过(幂等)", normalized_uuid)
                return
            self._gantt_reported_uuids.add(normalized_uuid)

        thread = threading.Thread(
            target=self._gantt_report_worker,
            args=(normalized_uuid,),
            name=f"gantt-report-{normalized_uuid[:8]}",
            daemon=True,
        )
        thread.start()
        logger.info("甘特图回传线程已启动: uuid=%s", normalized_uuid)

    @not_action
    def _gantt_report_worker(self, uuid: str) -> None:
        """后台线程体：实时查所有执行中订单 → 逐个拉甘特(原始响应)汇总成数组 → 一次性 POST 回传。"""
        try:
            order_result = self.get_order_list(status="执行中（60）", latest_only=False)
            order_ids = [
                str(oid).strip()
                for oid in (order_result or {}).get("order_ids", [])
                if str(oid).strip()
            ]
            if not order_ids:
                logger.error(
                    "甘特图回传：未查询到执行中(60)的订单，跳过 uuid=%s",
                    uuid,
                )
                return
            rpc = self._require_hardware_interface("gantt_with_simulation_by_order_id")
            gantts: List[Any] = []
            for order_id in order_ids:
                try:
                    gantts.append(
                        rpc.gantt_with_simulation_by_order_id(order_id, return_envelope=True)
                    )
                except Exception as exc:
                    logger.error("甘特图拉取失败 order_id=%s: %s", order_id, exc)
            if not gantts:
                logger.error("甘特图回传：所有订单拉取均失败，跳过 uuid=%s", uuid)
                return
            logger.info(
                "甘特图拉取完成: uuid=%s 执行中订单数=%s 成功=%s",
                uuid,
                len(order_ids),
                len(gantts),
            )
            from unilabos.app.web import http_client

            http_client.report_gantt(uuid, gantts)
        except Exception as exc:
            logger.error("甘特图回传失败 uuid=%s: %s", uuid, exc, exc_info=True)

    def _require_hardware_interface(self, method_name: str) -> Any:
        rpc = getattr(self, "hardware_interface", None)
        if rpc is None:
            rpc = self._initialize_hardware_interface_from_config()
        if not hasattr(rpc, method_name):
            raise RuntimeError(f"Bioyond RPC 客户端缺少 {method_name} 方法")
        return rpc

    def _require_hardware_interface_for_reset(self) -> Any:
        rpc = getattr(self, "hardware_interface", None)
        if rpc is None:
            rpc = self._initialize_hardware_interface_from_config()
        return rpc

    def _initialize_hardware_interface_from_config(self) -> Any:
        self._apply_env_api_config(self.bioyond_config)
        missing = self._missing_api_config_keys(self.bioyond_config)
        if missing:
            api_host_present = "api_host" not in missing
            api_key_present = "api_key" not in missing
            lines = [
                f"无法调用 Bioyond RPC：缺少 {missing}（站点处于延迟初始化模式，构造时未提供完整 API 配置）。",
                f"  - 当前 api_host: {'已配置' if api_host_present else '<缺失>'}",
                f"  - 当前 api_key:  {'已配置' if api_key_present else '<缺失>'}",
                "请按以下任一方式补齐后重试：",
                "  1) 在前端节点 config 中填入 api_host / api_key 并重新下发 graph；",
                "  2) 设置环境变量后重启 edge：",
                "       BIOYOND_SIRNA_API_HOST 或 BIOYOND_SIRNA_EXP1_API_HOST",
                "       BIOYOND_SIRNA_API_KEY  或 BIOYOND_SIRNA_EXP1_API_KEY",
            ]
            raise RuntimeError("\n".join(lines))
        from unilabos.devices.workstation.bioyond_studio.bioyond_rpc import BioyondV1RPC

        rpc = BioyondV1RPC(self.bioyond_config)
        self._set_hardware_interface(rpc)
        return self.hardware_interface

    def _apply_env_api_config(self, config: Dict[str, Any]) -> None:
        env_pairs = {
            "api_host": ("BIOYOND_SIRNA_API_HOST", "BIOYOND_SIRNA_EXP1_API_HOST"),
            "api_key": ("BIOYOND_SIRNA_API_KEY", "BIOYOND_SIRNA_EXP1_API_KEY"),
        }
        for key, env_names in env_pairs.items():
            if not self._is_blank(config.get(key)):
                continue
            for env_name in env_names:
                value = os.environ.get(env_name)
                if not self._is_blank(value):
                    config[key] = value
                    break

    def _config_value(self, *keys: str) -> Optional[str]:
        config = getattr(self, "bioyond_config", {}) or {}
        for key in keys:
            value = config.get(key)
            if not self._is_blank(value):
                return str(value)
        return None

    def _resolve_experiment_workflow(
        self,
        rpc: Any,
        workflow_name: str = "",
        sub_workflow_name: str = "",
    ) -> Dict[str, str]:
        workflow_name = workflow_name or ""
        sub_workflow_name = sub_workflow_name or ""
        workflow_query = {"type": 0, "filter": workflow_name or sub_workflow_name, "includeDetail": True}
        logger.info("正在解析小核酸工作流: filter=%r", workflow_query["filter"])
        workflow_data = rpc.query_workflow(json.dumps(workflow_query, ensure_ascii=False))
        workflow_items = self._workflow_items(workflow_data)
        logger.info("小核酸工作流查询返回: items=%s", len(workflow_items))
        roots_with_children = [item for item in workflow_items if self._sub_workflow_records(item)]
        root_candidates = roots_with_children or workflow_items
        if not workflow_name and sub_workflow_name:
            roots_matching_sub = [
                item
                for item in root_candidates
                if self._select_workflow_record(self._sub_workflow_records(item), sub_workflow_name)
            ]
            if roots_matching_sub:
                root_candidates = roots_matching_sub
        root = self._select_workflow_record(root_candidates, workflow_name)
        if not root:
            logger.error(
                "未从 LIMS 查询到可用的小核酸工作流: workflow_name=%r sub_workflow_name=%r items=%s",
                workflow_name,
                sub_workflow_name,
                len(workflow_items),
            )
            raise RuntimeError("未从 LIMS 查询到可用的小核酸工作流")
        sub = self._select_workflow_record(self._sub_workflow_records(root), sub_workflow_name)
        if not sub:
            label = self._record_name(root) or workflow_name or self._record_id(root)
            logger.error("小核酸工作流缺少可用子工作流: root=%s sub_filter=%r", label, sub_workflow_name)
            raise RuntimeError(f"工作流 {label} 缺少可用子工作流")
        sub_id = self._record_id(sub)
        self._require_uuid(sub_id, "workFlowId")
        return {
            "workflow_name": self._record_name(root) or workflow_name,
            "root_workflow_id": self._record_id(root),
            "sub_workflow_name": self._record_name(sub) or sub_workflow_name,
            "sub_workflow_id": sub_id,
        }

    def _build_param_values_from_step_data(
        self,
        step_data: Any,
        parameter_overrides: Any,
        include_all_task_displayable: bool,
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]], List[str]]:
        parameter_map = self._extract_workflow_parameter_map(step_data)
        if not isinstance(parameter_map, dict):
            logger.error("workflow_step_query 未返回可解析的步骤参数: type=%s", type(parameter_map).__name__)
            raise RuntimeError("workflow_step_query 未返回可解析的步骤参数")
        param_values: Dict[str, List[Dict[str, Any]]] = {}
        flattened, parameter_template = self._flatten_workflow_parameters(parameter_map)
        override_items, override_warnings = self._resolve_parameter_override_items(parameter_overrides, flattened)
        override_record_ids = {id(item["record"]) for item in override_items}
        records_by_step: Dict[str, List[Dict[str, Any]]] = {}
        for record in flattened:
            records_by_step.setdefault(record["step_id"], []).append(record)

        for step_id, records in records_by_step.items():
            entries: List[Dict[str, Any]] = []
            for record in records:
                parameter_type = str(record.get("type") or "")
                task_displayable = record.get("TaskDisplayable", 1)
                if parameter_type.lower() == "hidden" or task_displayable == 0:
                    continue
                is_required_default = record.get("key") == "protocolName"
                is_explicit_override = id(record) in override_record_ids
                if not include_all_task_displayable and not is_required_default and not is_explicit_override:
                    continue
                value_for_create_order = record.get("value")
                if self._is_blank(value_for_create_order) and not is_explicit_override:
                    continue
                entry: Dict[str, Any] = {
                    "key": record["key"],
                    "value": "" if self._is_blank(value_for_create_order) else str(value_for_create_order),
                }
                if record.get("m") is not None:
                    entry["m"] = record["m"]
                if record.get("n") is not None:
                    entry["n"] = record["n"]
                entries.append(entry)
            if entries:
                param_values[step_id] = entries

        self._apply_structured_parameter_overrides(param_values, override_items)
        return param_values, parameter_template, override_warnings

    def _flatten_workflow_parameters(
        self,
        parameter_map: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        flattened: List[Dict[str, Any]] = []
        parameter_template: List[Dict[str, Any]] = []
        for step_id, value in parameter_map.items():
            if not self._looks_like_uuid(step_id):
                continue
            for module in self._as_list(value):
                if not isinstance(module, dict):
                    continue
                module_m = module.get("m")
                module_n = module.get("n")
                module_name = module.get("name") or module.get("moduleName") or module.get("ModuleName") or ""
                for parameter in self._as_list(module.get("parameterList") or module.get("ParameterList")):
                    if not isinstance(parameter, dict):
                        continue
                    key = self._parameter_key(parameter)
                    if not key:
                        continue
                    task_displayable = parameter.get("TaskDisplayable", parameter.get("taskDisplayable", 1))
                    parameter_type = str(parameter.get("Type") or parameter.get("type") or "")
                    raw_m = parameter.get("m")
                    raw_n = parameter.get("n")
                    m_value = self._optional_int(module_m if self._is_blank(raw_m) else raw_m)
                    n_value = self._optional_int(module_n if self._is_blank(raw_n) else raw_n)
                    live_value = parameter.get("Value") if "Value" in parameter else parameter.get("value")
                    display_value = parameter.get("DisplayValue") if "DisplayValue" in parameter else parameter.get("displayValue")
                    value_for_create_order = self._value_for_create_order(parameter)
                    record = {
                        "step_id": str(step_id),
                        "step_uuid": str(step_id),
                        "module": module_name,
                        "step_name": module.get("stepName") or module.get("StepName") or "",
                        "m": m_value,
                        "n": n_value,
                        "key": key,
                        "display_name": (
                            parameter.get("DisplayName")
                            or parameter.get("displayName")
                            or parameter.get("Name")
                            or parameter.get("name")
                            or ""
                        ),
                        "type": parameter_type,
                        "task_displayable": task_displayable,
                        "TaskDisplayable": task_displayable,
                        "Value": live_value,
                        "DisplayValue": display_value,
                        "value": value_for_create_order,
                    }
                    flattened.append(record)
                    parameter_template.append(dict(record))
        return flattened, parameter_template

    def _value_for_create_order(self, parameter: Dict[str, Any]) -> Any:
        value = parameter.get("Value") if "Value" in parameter else parameter.get("value")
        display_value = parameter.get("DisplayValue") if "DisplayValue" in parameter else parameter.get("displayValue")
        if self._is_blank(value) and not self._is_blank(display_value):
            return display_value
        return value

    def _apply_structured_parameter_overrides(
        self,
        param_values: Dict[str, List[Dict[str, Any]]],
        override_items: List[Dict[str, Any]],
    ) -> None:
        if not override_items:
            return
        for item in override_items:
            record = item["record"]
            step_id = record["step_id"]
            entries = param_values.setdefault(step_id, [])
            replacement: Dict[str, Any] = {"key": record["key"], "value": item["value"]}
            if record.get("m") is not None:
                replacement["m"] = record["m"]
            if record.get("n") is not None:
                replacement["n"] = record["n"]
            for index, entry in enumerate(entries):
                if self._same_param_value_entry(entry, replacement):
                    entries[index] = replacement
                    break
            else:
                entries.append(replacement)

    def _resolve_parameter_override_items(
        self,
        overrides: Any,
        flattened_parameters: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        raw_items = self._parameter_override_items(overrides)
        if not raw_items:
            return [], []
        deduped: Dict[Tuple[str, Optional[int], Optional[int]], Dict[str, Any]] = {}
        warnings: List[str] = []
        for item in raw_items:
            key = item["key"]
            dedupe_key = (key, item.get("m"), item.get("n"))
            if dedupe_key in deduped:
                warning = f"parameter_override_duplicate_last_write_wins:{key}"
                warnings.append(warning)
                logger.warning("参数覆盖重复，采用最后一次填写: key=%s m=%s n=%s", key, item.get("m"), item.get("n"))
            deduped[dedupe_key] = item

        resolved: List[Dict[str, Any]] = []
        for item in deduped.values():
            candidates = [
                record
                for record in flattened_parameters
                if record.get("key") == item["key"]
                and (item.get("m") is None or record.get("m") == item.get("m"))
                and (item.get("n") is None or record.get("n") == item.get("n"))
            ]
            if not candidates:
                raise ValueError(
                    f"paramValues 中找不到可覆盖参数: {item['key']} (m={item.get('m')}, n={item.get('n')})"
                )
            if len(candidates) > 1:
                raise ValueError(
                    f"参数覆盖匹配到多个 Bioyond 参数，请补充 m/n 消歧: {item['key']}"
                )
            resolved.append({"record": candidates[0], "value": item["value"]})
        return resolved, warnings

    def _parameter_override_items(self, overrides: Any) -> List[Dict[str, Any]]:
        if not overrides:
            return []
        if isinstance(overrides, str):
            raise ValueError("parameter_overrides 必须是结构化列表，不能使用 'a=b,c=d' 文本格式")
        if isinstance(overrides, dict):
            if not any(key in overrides for key in ("Key", "key", "Value", "value")):
                raise ValueError("parameter_overrides 必须是包含 m/n/Key/Value 的对象列表，不能使用 key-only 字典")
            overrides = [overrides]
        override_items: List[Dict[str, Any]] = []
        for override in self._as_list(overrides):
            if not override:
                continue
            if isinstance(override, dict):
                key = override.get("Key") if "Key" in override else override.get("key")
                if self._is_blank(key):
                    raise ValueError("parameter_overrides 条目缺少 Key")
                if "Value" in override:
                    value = override["Value"]
                elif "value" in override:
                    value = override["value"]
                else:
                    raise ValueError(f"parameter_overrides 条目缺少 Value: {key!r}")
                override_items.append({
                    "key": str(key),
                    "value": value,
                    "m": self._optional_int(override.get("m")),
                    "n": self._optional_int(override.get("n")),
                })
                continue
            raise ValueError(f"parameter_overrides 条目必须是包含 m/n/Key/Value 的对象: {override!r}")
        return override_items

    def _same_param_value_entry(self, left: Dict[str, Any], right: Dict[str, Any]) -> bool:
        return (
            left.get("key") == right.get("key")
            and left.get("m") == right.get("m")
            and left.get("n") == right.get("n")
        )

    def _optional_int(self, value: Any) -> Optional[int]:
        if self._is_blank(value):
            return None
        return int(value)

    def _build_bioyond_order_identity(
        self,
        order_code: str = "",
        order_name: str = "",
        experiment_number: Optional[int] = None,
    ) -> Tuple[str, str]:
        if order_code and order_name:
            return order_code, order_name
        prefix_keys: List[str] = []
        if experiment_number is not None:
            prefix_keys.extend(
                [
                    f"experiment_{experiment_number}_order_prefix",
                    f"sirna_exp{experiment_number}_order_prefix",
                    f"sirna_exp{experiment_number}_order_code_prefix",
                ]
            )
        prefix_keys.extend(
            [
                "experiment_1_order_prefix",
                "sirna_exp1_order_prefix",
                "sirna_exp1_order_code_prefix",
            ]
        )
        prefix = self._config_value(*prefix_keys) or "test"
        suffix = datetime.now().strftime("%m%d%H%M%S")
        value = f"{prefix}{suffix}"
        return order_code or value, order_name or value

    def _sync_from_external_and_optionally_publish(
        self,
        publish_resource_tree: bool,
        action_name: str,
    ) -> Dict[str, Any]:
        """运行 base Bioyond 同步器，成功后按需发布资源树。"""
        self._require_hardware_interface("stock_material")
        result: Dict[str, Any] = {
            "success": False,
            "action": action_name,
            "sync_mode": "shared_bioyond",
            "synchronizer": "BioyondResourceSynchronizer",
            "publish_resource_tree": bool(publish_resource_tree),
            "resource_tree_update_requested": False,
            "sync_attempted": False,
            "warnings": [],
        }

        if getattr(self, "deck", None) is None:
            warning = {
                "reason": "no_deck",
                "message": "Bioyond 资源同步跳过：工作站未初始化 deck",
            }
            result["skipped"] = True
            result["warnings"].append(warning)
            result["message"] = warning["message"]
            logger.warning(warning["message"])
            return result

        try:
            synchronizer = getattr(self, "resource_synchronizer", None)
            if type(synchronizer) is not BioyondResourceSynchronizer:
                synchronizer = BioyondResourceSynchronizer(self)
                self.resource_synchronizer = synchronizer
            result["sync_attempted"] = True
            result["success"] = bool(synchronizer.sync_from_external())
            if result["success"] and publish_resource_tree:
                self._publish_resource_tree_update()
                result["resource_tree_update_requested"] = True
            result["message"] = "Bioyond 资源同步成功" if result["success"] else "Bioyond 资源同步失败"
            if result["success"]:
                logger.info("Bioyond 外部物料同步完成: publish_resource_tree=%s", bool(publish_resource_tree))
            else:
                result["warnings"].append({
                    "reason": "sync_returned_false",
                    "message": "Bioyond 资源同步未返回成功",
                })
                logger.warning("Bioyond 外部物料同步未返回成功")
        except Exception as exc:
            logger.error(f"Bioyond 外部物料同步失败: {exc}")
            result["error"] = str(exc)
            result["message"] = "Bioyond 资源同步失败"
            result["warnings"].append({
                "reason": "sync_exception",
                "message": str(exc),
            })
        return result

    def _run_scheduler_action(
        self,
        method_name: str,
        action_label: str,
    ) -> Dict[str, Any]:
        """统一封装直接调度器动作，保持 station action 只是薄包装。"""
        with self._debug_call_session(method_name):
            rpc = self._require_hardware_interface(method_name)
            logger.info("正在%s小核酸调度器: method=%s", action_label, method_name)
            result = getattr(rpc, method_name)()
            success = result == 1
            if success:
                logger.info("小核酸调度器%s成功: result=%s", action_label, result)
            else:
                logger.error("小核酸调度器%s失败或返回非成功码: result=%s", action_label, result)
            return {
                "success": success,
                "return_info": result,
                f"{method_name}_result": result,
                "scheduler_action": method_name,
                "confirmation_message": (
                    f"调度器{action_label}成功"
                    if success
                    else f"调度器{action_label}失败，请检查 LIMS 状态"
                ),
            }

    def _run_reset_operations(
        self,
        rpc: Any,
        reset_scheduler: bool,
        reset_order_status: bool,
        reset_location: bool,
        reset_devices: bool,
        action_name: str,
    ) -> Dict[str, Any]:
        result = self._empty_reset_result(
            action_name=action_name,
            reset_scheduler=reset_scheduler,
            reset_order_status=reset_order_status,
            reset_location=reset_location,
            reset_devices=reset_devices,
        )
        logger.info(
            "小核酸复位操作开始: action=%s selected=%s",
            action_name,
            [item["key"] for item in result["selected_operations"] if item["selected"]],
        )

        for operation in RESET_OPERATION_DEFINITIONS:
            key = operation["key"]
            selected = next(item["selected"] for item in result["selected_operations"] if item["key"] == key)
            if not selected:
                continue
            call: Dict[str, Any] = {
                "operation": key,
                "label": operation["label"],
                "method": operation["method"],
                "endpoint": operation["endpoint"],
                "success": False,
            }
            try:
                method = getattr(rpc, operation["method"], None)
                if not callable(method):
                    raise RuntimeError(f"Bioyond RPC 客户端缺少 {operation['method']} 方法")
                return_code = method()
                call["return_code"] = return_code
                call["success"] = return_code == 1
                if not call["success"]:
                    result["warnings"].append({
                        "operation": key,
                        "reason": "non_success_return_code",
                        "return_code": return_code,
                    })
            except Exception as exc:
                call["exception"] = f"{type(exc).__name__}: {exc}"
                result["warnings"].append({
                    "operation": key,
                    "reason": "exception",
                    "message": str(exc),
                })
            result["executed_calls"].append(call)
            result[key] = call.get("return_code")

        if not result["executed_calls"]:
            result["warnings"].append({
                "reason": "no_reset_operations_selected",
                "message": "未选择任何复位操作",
            })

        result["all_operations_successful"] = all(
            call.get("success") for call in result["executed_calls"]
        )
        logger.info(
            "小核酸复位操作完成: action=%s all_success=%s warnings=%d",
            action_name,
            result["all_operations_successful"],
            len(result["warnings"]),
        )
        return result

    def _empty_reset_result(
        self,
        action_name: str,
        reset_scheduler: bool,
        reset_order_status: bool,
        reset_location: bool,
        reset_devices: bool,
    ) -> Dict[str, Any]:
        selected_by_key = {
            "reset_scheduler": bool(reset_scheduler),
            "reset_order_status": bool(reset_order_status),
            "reset_location": bool(reset_location),
            "reset_devices": bool(reset_devices),
        }
        selected_operations = [
            {
                "key": operation["key"],
                "label": operation["label"],
                "selected": selected_by_key[operation["key"]],
            }
            for operation in RESET_OPERATION_DEFINITIONS
        ]
        skipped_operations = [
            {
                "key": operation["key"],
                "label": operation["label"],
                "reason": "not_selected",
            }
            for operation in RESET_OPERATION_DEFINITIONS
            if not selected_by_key[operation["key"]]
        ]
        return {
            "success": False,
            "action": action_name,
            "selected_operations": selected_operations,
            "executed_calls": [],
            "skipped_operations": skipped_operations,
            "warnings": [],
            "all_operations_successful": False,
        }

    def _maybe_sync_after_reset(
        self,
        result: Dict[str, Any],
        sync_from_external_after_reset: bool,
        manual_mode: bool,
    ) -> None:
        result["sync_from_external_after_reset"] = bool(sync_from_external_after_reset)
        if not sync_from_external_after_reset:
            return
        if not result.get("all_operations_successful"):
            warning = {
                "reason": "sync_from_external_after_reset_skipped_due_to_reset_failure",
                "message": "复位操作未全部成功，跳过请求的外部物料同步",
            }
            result["warnings"].append(warning)
            result["external_material_sync"] = {
                "success": False,
                "skipped": True,
                "sync_attempted": False,
                "reason": warning["reason"],
                "warnings": [warning],
            }
            logger.warning(warning["message"])
            return

        sync_result = self._sync_from_external_and_optionally_publish(
            publish_resource_tree=True,
            action_name=f"{result.get('action', 'reset')}.sync_from_external_after_reset",
        )
        result["external_material_sync"] = sync_result
        if not sync_result.get("success"):
            warning = {
                "reason": "sync_from_external_after_reset_failed",
                "message": sync_result.get("message", "外部物料同步失败"),
            }
            result["warnings"].append(warning)
            if manual_mode and sync_result.get("skipped"):
                logger.warning("manual reset requested sync but sync was skipped: %s", sync_result)

    def _normalize_optional_string_list(self, value: Optional[List[str]], field_name: str) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str) or not isinstance(value, list):
            raise ValueError(f"{field_name} 必须是列表，不能传单个字符串或其他类型")
        return [str(item).strip() for item in value if str(item or "").strip()]

    @staticmethod
    def _as_manual_gate(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y", "on", "checked"}:
                return True
            if normalized in {"false", "0", "no", "n", "off", "unchecked", ""}:
                return False
        return bool(value)

    def _result_table_rows(self, result_table: Optional[Dict[str, Any]]) -> List[Any]:
        if not isinstance(result_table, dict):
            return []
        rows = result_table.get("data")
        if rows is None:
            rows = result_table.get("rows")
        return self._as_list(rows)

    def _extract_workflow_parameter_map(self, step_data: Any) -> Any:
        parsed = self._json_loads_if_string(step_data)
        if isinstance(parsed, dict) and self._looks_like_step_parameter_map(parsed):
            return parsed
        if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
            data = self._json_loads_if_string(parsed["data"])
            if isinstance(data, dict) and self._looks_like_step_parameter_map(data):
                return data
        return parsed

    def _workflow_items(self, workflow_data: Any) -> List[Dict[str, Any]]:
        parsed = self._json_loads_if_string(workflow_data)
        if isinstance(parsed, dict):
            items = parsed.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
            data = parsed.get("data")
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return [item for item in data["items"] if isinstance(item, dict)]
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        return []

    def _select_workflow_record(
        self,
        records: Iterable[Dict[str, Any]],
        workflow_name: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        candidates = [record for record in records if self._record_id(record)]
        if not candidates:
            return None
        if workflow_name:
            exact = [record for record in candidates if self._record_name(record) == workflow_name]
            if exact:
                return exact[0]
            contains = [record for record in candidates if workflow_name in (self._record_name(record) or "")]
            if contains:
                return contains[0]
        return candidates[0]

    def _sub_workflow_records(self, root_workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for key in ("subWorkflows", "subWorkflowList", "workflows", "children"):
            value = root_workflow.get(key)
            if isinstance(value, list):
                records.extend(item for item in value if isinstance(item, dict))
        return records

    def _order_items(self, order_snapshot: Any) -> List[Dict[str, Any]]:
        parsed = self._json_loads_if_string(order_snapshot)
        if isinstance(parsed, dict):
            data = parsed.get("data")
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return [item for item in data["items"] if isinstance(item, dict)]
            if isinstance(parsed.get("items"), list):
                return [item for item in parsed["items"] if isinstance(item, dict)]
        return []

    def _normalize_order_report_result(self, order_id: str, raw_result: Any) -> Dict[str, Any]:
        parsed = self._parse_lims_result(raw_result)
        report = parsed
        success = bool(parsed)
        message = ""

        if isinstance(parsed, dict) and "data" in parsed:
            success = parsed.get("code") in {1, "1", None} and bool(parsed.get("data"))
            message = str(parsed.get("message") or "")
            report = self._parse_lims_result(parsed.get("data"))

        resolved_order_id = str(order_id or "").strip()
        if isinstance(report, dict):
            for key in ("id", "orderId", "order_id"):
                value = report.get(key)
                if not self._is_blank(value):
                    resolved_order_id = str(value)
                    break

        result: Dict[str, Any] = {
            "success": bool(success),
            "order_id": resolved_order_id,
            "report": report if report is not None else {},
            "raw": parsed,
        }
        if message:
            result["message"] = message
        return result

    def _order_list_query_payload(self, filter_text: str, max_results: int = 20) -> Dict[str, Any]:
        return {
            "timeType": "",
            "beginTime": None,
            "endTime": None,
            "status": "",
            "filter": str(filter_text or ""),
            "skipCount": 0,
            "pageCount": max_results,
            "sorting": "creationTime desc",
        }

    def _call_order_query_for_report(
        self,
        rpc: Any,
        query_payload: Dict[str, Any],
        errors: List[str],
    ) -> Any:
        if hasattr(rpc, "order_query"):
            try:
                return rpc.order_query(json.dumps(query_payload, ensure_ascii=False), return_envelope=True)
            except Exception as exc:
                errors.append(f"order_list: {exc}")
                logger.warning("聚合订单报告 order_list 查询失败，继续使用空结果: %s", exc)
                return {}
        errors.append("order_list: Bioyond RPC 客户端缺少 order_query 方法")
        logger.error("聚合订单报告 order_list 缺少专用 RPC 方法")
        return {}

    def _call_single_arg_lims_section(
        self,
        rpc: Any,
        method_name: str,
        data: str,
        errors: List[str],
        section_name: str,
    ) -> Any:
        if hasattr(rpc, method_name):
            try:
                return getattr(rpc, method_name)(data, return_envelope=True)
            except Exception as exc:
                errors.append(f"{section_name}: {exc}")
                logger.warning(
                    "聚合订单报告 %s 查询失败，继续使用空结果: %s",
                    section_name,
                    exc,
                )
                return {}
        errors.append(f"{section_name}: Bioyond RPC 客户端缺少 {method_name} 方法")
        logger.error("聚合订单报告 %s 缺少 RPC 方法 %s", section_name, method_name)
        return {}

    def _service_data_or_value(self, payload: Any) -> Any:
        parsed = self._parse_lims_result(payload)
        if isinstance(parsed, dict) and "data" in parsed and (
            "code" in parsed or "message" in parsed or "timestamp" in parsed
        ):
            return self._parse_lims_result(parsed.get("data"))
        return parsed

    def _normalize_service_result(self, payload: Any) -> Dict[str, Any]:
        parsed = self._parse_lims_result(payload)
        code = parsed.get("code") if isinstance(parsed, dict) else parsed
        message = ""
        if isinstance(parsed, dict):
            message = str(parsed.get("message") or "")
        return {
            "success": code in {1, "1"},
            "code": code,
            "message": message,
            "raw": parsed,
        }

    def _append_lims_section_error(
        self,
        payload: Any,
        errors: List[str],
        section_name: str,
    ) -> None:
        parsed = self._parse_lims_result(payload)
        if not isinstance(parsed, dict) or "code" not in parsed:
            return
        if parsed.get("code") in {1, "1", None}:
            return
        message = str(parsed.get("message") or f"LIMS 返回 code={parsed.get('code')}")
        entry = f"{section_name}: {message}"
        if entry not in errors:
            errors.append(entry)

    def _select_order_record(
        self,
        order_items: List[Dict[str, Any]],
        order_id: str,
        filter_text: str,
    ) -> Optional[Dict[str, Any]]:
        if not order_items:
            return None
        normalized_order_id = str(order_id or "").strip()
        normalized_filter = str(filter_text or "").strip()
        if normalized_order_id:
            for item in order_items:
                if str(item.get("id") or "") == normalized_order_id:
                    return item
            return None
        if normalized_filter:
            for item in order_items:
                candidates = {
                    str(item.get("id") or ""),
                    str(item.get("name") or ""),
                    str(item.get("code") or ""),
                    str(item.get("orderCode") or ""),
                    str(item.get("orderName") or ""),
                }
                if normalized_filter in candidates:
                    return item
        return order_items[0]

    def _build_aggregated_report_header(
        self,
        order_record: Optional[Dict[str, Any]],
        report_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        source: Dict[str, Any] = {}
        if order_record:
            source.update(order_record)
        if report_data:
            for key, value in report_data.items():
                if key not in source or self._is_blank(source.get(key)):
                    source[key] = value

        sample_count = self._sample_count(source)
        return {
            "order_id": str(source.get("id") or source.get("orderId") or ""),
            "name": str(source.get("name") or source.get("orderName") or ""),
            "code": str(source.get("code") or source.get("orderCode") or ""),
            "status": source.get("status"),
            "statusName": str(source.get("statusName") or ""),
            "requester": str(source.get("requester") or ""),
            "workflowName": str(source.get("workflowName") or ""),
            "sample_count": sample_count,
            "sampleInfo": source.get("sampleInfo"),
            "requestTime": source.get("requestTime"),
            "startPreparationTime": source.get("startPreparationTime"),
            "completeTime": source.get("completeTime"),
            "useTime": source.get("useTime"),
            "orderProgress": source.get("orderProgress"),
        }

    def _sample_count(self, source: Dict[str, Any]) -> int:
        sample_info = source.get("sampleInfo")
        if isinstance(sample_info, (int, float)):
            return int(sample_info)
        if isinstance(sample_info, str) and sample_info.strip().isdigit():
            return int(sample_info.strip())
        sample_ids = set()
        for preintake in self._as_list(source.get("preIntakes")):
            if not isinstance(preintake, dict):
                continue
            for sample in self._as_list(preintake.get("sampleMaterials")):
                if isinstance(sample, dict):
                    sample_id = sample.get("materialId") or sample.get("materialCode") or sample.get("sampleCode")
                    if sample_id:
                        sample_ids.add(str(sample_id))
            material_ids_text = str(preintake.get("materialIds") or "")
            for material_id in material_ids_text.replace(";", "|").replace(",", "|").split("|"):
                if material_id.strip():
                    sample_ids.add(material_id.strip())
        return len(sample_ids)

    def _build_aggregated_report_parameters(
        self,
        report_data: Dict[str, Any],
        warnings: List[str],
    ) -> Dict[str, Any]:
        raw_parameters = report_data.get("workflowParameters") if isinstance(report_data, dict) else None
        if self._is_blank(raw_parameters):
            warnings.append("order_report 未提供 workflowParameters，实验参数为空")
            return {
                "source": "order_report.workflowParameters",
                "items": {},
                "reason": "order_report workflowParameters unavailable",
            }
        parsed = self._json_loads_if_string(raw_parameters)
        return {
            "source": "order_report.workflowParameters",
            "items": parsed if isinstance(parsed, (dict, list)) else {},
            "raw": raw_parameters if not isinstance(parsed, (dict, list)) else None,
        }

    def _order_material_ids(
        self,
        order_record: Optional[Dict[str, Any]],
        report_data: Dict[str, Any],
    ) -> List[str]:
        material_ids: List[str] = []
        for source in (order_record or {}, report_data or {}):
            for preintake in self._as_list(source.get("preIntakes")):
                if not isinstance(preintake, dict):
                    continue
                if preintake.get("materialId"):
                    material_ids.append(str(preintake["materialId"]))
                material_ids_text = str(preintake.get("materialIds") or "")
                for material_id in material_ids_text.replace(";", "|").replace(",", "|").split("|"):
                    if material_id.strip():
                        material_ids.append(material_id.strip())
                for sample in self._as_list(preintake.get("sampleMaterials")):
                    if isinstance(sample, dict) and sample.get("materialId"):
                        material_ids.append(str(sample["materialId"]))
        return list(dict.fromkeys(material_ids))

    def _build_aggregated_report_samples(
        self,
        order_record: Optional[Dict[str, Any]],
        report_data: Dict[str, Any],
        material_info_by_id: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        sample_by_id: Dict[str, Dict[str, Any]] = {}
        for source in (order_record or {}, report_data or {}):
            for preintake in self._as_list(source.get("preIntakes")):
                if not isinstance(preintake, dict):
                    continue
                status_name = preintake.get("statusName")
                throughput_code = preintake.get("code")
                material_ids_text = str(preintake.get("materialIds") or "")
                for sample in self._as_list(preintake.get("sampleMaterials")):
                    if not isinstance(sample, dict):
                        continue
                    material_id = str(sample.get("materialId") or "")
                    if not material_id:
                        continue
                    sample_by_id.setdefault(material_id, {})
                    sample_by_id[material_id].update({
                        "material_id": material_id,
                        "name": sample.get("materialName"),
                        "type": sample.get("materialTypeName"),
                        "code": sample.get("materialCode"),
                        "barcode": sample.get("materialBarCode") or sample.get("materialCode"),
                        "location": sample.get("materialLocation"),
                        "target_location": sample.get("materialTargetLocation"),
                        "sample_code": sample.get("sampleCode"),
                        "throughput_code": throughput_code,
                        "statusName": status_name,
                    })
                for material_id in material_ids_text.replace(";", "|").replace(",", "|").split("|"):
                    material_id = material_id.strip()
                    if material_id:
                        sample_by_id.setdefault(material_id, {"material_id": material_id, "throughput_code": throughput_code, "statusName": status_name})

        for material_id, material_info in material_info_by_id.items():
            sample = sample_by_id.setdefault(material_id, {"material_id": material_id})
            if not isinstance(material_info, dict):
                continue
            sample.setdefault("name", material_info.get("name"))
            sample.setdefault("type", material_info.get("typeName"))
            sample.setdefault("code", material_info.get("code"))
            sample.setdefault("barcode", material_info.get("barCode") or material_info.get("code"))
            locations = self._as_list(material_info.get("locations"))
            location = next((item for item in locations if isinstance(item, dict)), {})
            if isinstance(location, dict):
                location_text = self._material_location_text(location)
                if location_text:
                    sample["location"] = sample.get("location") or location_text
                    sample["target_location"] = sample.get("target_location") or location_text
                sample["location_detail"] = location
            sample["material_info"] = material_info

        return list(sample_by_id.values())

    def _material_location_text(self, location: Dict[str, Any]) -> str:
        wh_name = str(location.get("whName") or "")
        code = str(location.get("code") or "")
        if wh_name and code:
            return f"{wh_name}:{code}"
        return wh_name or code

    def _split_used_materials(self, report_data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        reagents: List[Dict[str, Any]] = []
        consumables: List[Dict[str, Any]] = []
        for material in self._as_list(report_data.get("usedMaterials") if isinstance(report_data, dict) else None):
            if not isinstance(material, dict):
                continue
            item = {
                "name": material.get("holdMName") or material.get("materialName"),
                "code": material.get("holdMCode") or material.get("materialCode"),
                "type_name": material.get("holdMTypeName") or material.get("materialTypeName"),
                "quantity": material.get("quantity"),
                "unit": material.get("unit"),
                "location": material.get("fromLocationCode") or material.get("toLocationCode"),
                "batchCode": material.get("batchCode"),
                "raw": material,
            }
            type_value = material.get("type")
            name = str(item.get("name") or "")
            if type_value == 2 or "试剂" in name:
                reagents.append(item)
            else:
                consumables.append(item)
        return reagents, consumables

    def _gantt_items(self, gantt_payload: Any) -> List[Dict[str, Any]]:
        parsed = self._service_data_or_value(gantt_payload)
        if isinstance(parsed, dict):
            items = parsed.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        return []

    def _extract_create_order_materials(self, result: Any) -> List[Dict[str, Any]]:
        parsed = self._parse_lims_result(result)
        if isinstance(parsed, dict) and "data" in parsed:
            parsed = self._parse_lims_result(parsed.get("data"))
        records: List[Dict[str, Any]] = []
        if isinstance(parsed, dict):
            for order_id, value in parsed.items():
                for item in self._as_list(value):
                    if not isinstance(item, dict):
                        continue
                    record = dict(item)
                    record.setdefault("orderId", order_id)
                    records.append(record)
        elif isinstance(parsed, list):
            records.extend(item for item in parsed if isinstance(item, dict))
        return records

    def _extract_suggested_locations(self, material_records: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        seen = set()
        locations: List[Dict[str, str]] = []
        for record in material_records:
            location_id = str(record.get("locationId") or "")
            location_code = str(record.get("locationShowName") or record.get("locationCode") or "")
            if not location_id and not location_code:
                continue
            key = (location_id, location_code)
            if key in seen:
                continue
            seen.add(key)
            locations.append(
                {
                    "locationId": location_id,
                    "locationCode": str(record.get("locationCode") or ""),
                    "locationShowName": str(record.get("locationShowName") or ""),
                    "materialName": str(record.get("materialName") or ""),
                    "materialCode": str(record.get("materialCode") or ""),
                    "location_id": location_id,
                    "location_code": location_code,
                    "material_name": str(record.get("materialName") or ""),
                    "material_code": str(record.get("materialCode") or ""),
                }
            )
        return locations

    def _extract_created_order_ids(self, result: Any) -> List[str]:
        parsed = self._parse_lims_result(result)
        if isinstance(parsed, dict) and "data" in parsed:
            parsed = self._parse_lims_result(parsed.get("data"))
        if isinstance(parsed, dict):
            return [str(key) for key in parsed.keys() if self._looks_like_uuid(key)]
        if isinstance(parsed, str) and self._looks_like_uuid(parsed):
            return [parsed]
        return []

    def _create_result_success(self, parsed_result: Any, order_ids: List[str], material_records: List[Dict[str, Any]]) -> bool:
        if isinstance(parsed_result, dict) and "code" in parsed_result:
            return parsed_result.get("code") == 1
        return bool(order_ids or material_records)

    def _format_create_order_confirmation(
        self,
        order_code: str,
        order_name: str,
        workflow: Dict[str, str],
        order_ids: List[str],
        material_records: List[Dict[str, Any]],
        suggested_locations: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """Format create order confirmation message with grouped materials.

        Returns:
            Dict with 'confirmation_message' (str) and 'materials_by_type' (dict)
        """
        lines = [
            f"实验已提交: {order_name} ({order_code})",
            f"工作流: {workflow.get('workflow_name', '')} / {workflow.get('sub_workflow_name', '')}",
        ]
        if order_ids:
            lines.append(f"实验ID: {', '.join(order_ids)}")

        # Group materials by type for better readability
        grouped = {}
        if material_records:
            lines.append("\n实验物料分配确认:")
            grouped = self._group_materials_by_type(material_records)

            for mode in ["Sample", "Consumables", "Reagent"]:
                materials = grouped.get(mode, [])
                if materials:
                    lines.append(f"\n【{mode}】")
                    for i, mat in enumerate(materials, 1):
                        name = mat.get("materialName") or "未命名物料"
                        code = mat.get("materialCode") or "-"
                        quantity = mat.get("quantity") or "-"
                        location = mat.get("locationShowName") or mat.get("locationCode") or "-"
                        lines.append(f"  {i}. {name} ({code})")
                        lines.append(f"     数量: {quantity}, 位置: {location}")
        else:
            lines.append("所需物料与建议库位: LIMS 未返回预分配记录")

        if suggested_locations:
            lines.append("\n库位汇总:")
            for index, location in enumerate(suggested_locations, 1):
                material = location.get("material_name") or "未命名物料"
                code = location.get("location_code") or location.get("location_id") or "-"
                lines.append(f"{index}. {material} -> {code}")

        return {
            "confirmation_message": "\n".join(lines),
            "materials_by_type": grouped,
        }

    def _group_materials_by_type(self, materials: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Group materials by materialTypeMode (Sample/Consumables/Reagent)."""
        grouped: Dict[str, List[Dict[str, Any]]] = {
            "Sample": [],
            "Consumables": [],
            "Reagent": [],
        }
        for mat in materials:
            mode = mat.get("materialTypeMode", "Unknown")
            if mode not in grouped:
                grouped[mode] = []
            grouped[mode].append(mat)
        return grouped

    def _build_result_table(
        self,
        materials_by_type: Dict[str, List[Dict[str, Any]]],
        table_name: str = "resultTable",
    ) -> Dict[str, Any]:
        """构建新的前端结果表格 schema（data/columns/tableName 结构）。

        Args:
            materials_by_type: 按模式分组的物料（Sample/Consumables/Reagent）
            table_name: 前端表格标识符

        Returns:
            包含以下键的字典: data（行对象列表）、columns（列规格列表）、tableName
        """
        material_info_cache: Dict[str, Dict[str, Any]] = {}
        ordered_modes: List[str] = []
        for mode in ("Sample", "Consumables", "Reagent"):
            if mode in materials_by_type:
                ordered_modes.append(mode)
        for mode in materials_by_type:
            if mode not in ordered_modes:
                ordered_modes.append(mode)

        data_rows = []
        for mode in ordered_modes:
            for mat in materials_by_type.get(mode, []):
                material_id = str(mat.get("materialId") or "")
                data_rows.append({
                    "whName": self._resolve_wh_name_by_material_id(material_id, material_info_cache),
                    "locationCode": str(mat.get("locationShowName") or mat.get("locationCode") or ""),
                    "materialName": str(mat.get("materialName") or ""),
                    "quantity": str(mat.get("quantity") or ""),
                })

        # 定义列 schema
        columns = [
            {"name": "设备", "key": "whName"},
            {"name": "位置", "key": "locationCode"},
            {"name": "物料名称", "key": "materialName"},
            {"name": "数量", "key": "quantity"},
        ]

        logger.debug(
            "构建结果表格: tableName=%s, columns=%s, dataRows=%s",
            table_name,
            columns,
            data_rows,
        )

        return {
            "data": data_rows,
            "columns": columns,
            "tableName": table_name,
        }

    @staticmethod
    def _format_unload_quantity(q: Any) -> str:
        """把 materials-by-order-id 返回的 ``quantity`` 字段格式化成展示字符串。

        - ``None`` / 空串 → ``""``（前端 No data 占位）
        - 整数值 float（``1.0`` / ``4.0``）→ ``"1"`` / ``"4"``，去掉无意义的 ``.0`` 尾巴
        - 其他保留 ``str(q)`` 原样
        """
        if q is None or q == "":
            return ""
        if isinstance(q, bool):
            return str(q)
        if isinstance(q, float) and q.is_integer():
            return str(int(q))
        return str(q)

    @classmethod
    def _build_unload_rows_from_all_stock_material(
        cls,
        all_materials: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """把 ``materials-by-order-id`` 返回数据展平成下料表行（plan v2：4 列）。

        每条物料若有多 ``locations`` 则按 location 拆多行，方便操作员一对一物理取出；
        ``locations`` 为空时仍保留一行空坐标占位，提示该物料无法定位但需要操作员处理。

        Quantity 语义（实测奔曜实现）：
        - 物料级 ``quantity`` 是该物料在订单里的真实总量（操作员关心的"几个"）。
        - location 级 ``quantity`` 是运行时计数，实验未开始 / 已结束时通常为 0；
          下料指引场景没有展示价值。
        因此 location 级为 0 / None / 空时一律回退到物料级 ``top_quantity``，
        避免前端表格里全是 0 的误导。
        """
        rows: List[Dict[str, Any]] = []
        for mat in all_materials or []:
            if not isinstance(mat, dict):
                continue
            material_name = str(mat.get("name") or "")
            top_quantity = mat.get("quantity")
            locations = mat.get("locations") or []
            if not isinstance(locations, list) or not locations:
                rows.append({
                    "whName": "",
                    "locationCode": "",
                    "materialName": material_name,
                    "quantity": cls._format_unload_quantity(top_quantity),
                })
                continue
            for loc in locations:
                if not isinstance(loc, dict):
                    continue
                loc_quantity = loc.get("quantity")
                # 关键：奔曜实际返回 location.quantity=0（运行时计数），
                # 必须用 truthy 判断回退到物料级 quantity，不能用 `is None`。
                if not loc_quantity:
                    loc_quantity = top_quantity
                rows.append({
                    "whName": str(loc.get("whName") or ""),
                    "locationCode": str(loc.get("code") or ""),
                    "materialName": material_name,
                    "quantity": cls._format_unload_quantity(loc_quantity),
                })
        return rows

    @staticmethod
    def _build_unload_table(
        unload_rows: Optional[List[Dict[str, Any]]],
        table_name: str = "下料指引",
    ) -> Dict[str, Any]:
        """按 ``UNLOAD_TABLE_COLUMNS`` 渲染下料指引表的 ``data/columns/tableName`` 三段。"""
        return {
            "data": list(unload_rows or []),
            "columns": list(UNLOAD_TABLE_COLUMNS),
            "tableName": table_name,
        }

    @staticmethod
    def _used_material_to_dict(item: Any) -> Dict[str, Any]:
        """把基类 ``WorkstationReportRequest.usedMaterials`` 元素序列化成可 JSON 输出的 dict。

        基类把 usedMaterials 反序列化成对象（有 ``materialId``/``locationId``/``usedQuantity`` 等属性），
        本方法兜底 dict / 对象 / 其他三种情况，避免后续 json.dumps 抛。
        """
        if isinstance(item, dict):
            return dict(item)
        if item is None:
            return {}
        if hasattr(item, "__dict__"):
            return {key: value for key, value in vars(item).items() if not key.startswith("_")}
        return {"value": str(item)}

    def _resolve_wh_name_by_material_id(self, material_id: str, cache: Dict[str, Dict[str, Any]]) -> str:
        if not material_id:
            return ""
        if material_id not in cache:
            try:
                cache[material_id] = self._require_hardware_interface("material_info").material_info(material_id) or {}
            except Exception as exc:
                logger.warning("material_info 查询失败 material_id=%s: %s", material_id, exc)
                cache[material_id] = {}
        locations = self._as_list(cache[material_id].get("locations"))
        location = next((loc for loc in locations if isinstance(loc, dict)), {})
        return str(location.get("whName") or "")

    def _publish_resource_tree_update(self) -> None:
        """触发 UniLabOS 资源树更新（异步、非阻塞）。

        ``BaseROS2DeviceNode.update_resource`` 的真实签名是
        ``async def update_resource(self, resources: List[ResourcePLR])``。
        因此必须用 ``run_async_func`` 调度并传入 ``resources=[deck]``，
        不能传 ``resource_name``/``resource_data`` 这两个不存在的关键字。
        """
        ros_node = getattr(self, "_ros_node", None)
        if ros_node is None:
            return
        deck = getattr(self, "deck", None)
        if deck is None:
            return
        update_resource_callable = getattr(ros_node, "update_resource", None)
        if update_resource_callable is None:
            return
        try:
            try:
                from unilabos.ros.nodes.base_device_node import ROS2DeviceNode  # type: ignore
            except Exception:  # pragma: no cover - 轻量环境无 ros2
                ROS2DeviceNode = None  # type: ignore[assignment]
            if ROS2DeviceNode is not None and hasattr(ROS2DeviceNode, "run_async_func"):
                ROS2DeviceNode.run_async_func(
                    update_resource_callable,
                    True,
                    **{"resources": [deck]},
                )
                logger.info(f"已调度 deck '{deck.name}' 的资源树更新（async）")
            else:
                # 轻量/测试场景：直接调用，便于测试通过 monkeypatch 验证关键字。
                update_resource_callable(resources=[deck])
        except TypeError as exc:
            # 严格定位错误调用形态，便于回归。
            logger.error(f"resource tree 更新失败 (调用签名错误): {exc}")
            raise
        except Exception as exc:
            logger.warning(f"resource tree 更新失败 (非阻塞): {exc}")

    def _record_name(self, record: Optional[Dict[str, Any]]) -> str:
        if not isinstance(record, dict):
            return ""
        for key in ("name", "workflowName", "workFlowName", "displayName"):
            if record.get(key):
                return str(record[key])
        return ""

    def _record_id(self, record: Optional[Dict[str, Any]]) -> str:
        if not isinstance(record, dict):
            return ""
        for key in ("id", "workflowId", "workFlowId", "subWorkflowId", "subWorkFlowId"):
            if record.get(key):
                return str(record[key])
        return ""

    def _parameter_key(self, parameter: Dict[str, Any]) -> str:
        value = parameter.get("Key") or parameter.get("key")
        return "" if self._is_blank(value) else str(value)

    def _looks_like_step_parameter_map(self, value: Any) -> bool:
        return isinstance(value, dict) and any(self._looks_like_uuid(key) and isinstance(item, list) for key, item in value.items())

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

    def _json_loads_if_string(self, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return value
        return value

    def _require_uuid(self, value: Any, field_name: str) -> str:
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError(f"{field_name} 必须是 UUID: {value!r}") from exc

    def _looks_like_uuid(self, value: Any) -> bool:
        try:
            UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            return False
        return True

    def _as_list(self, value: Any) -> List[Any]:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    @staticmethod
    def _is_blank(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ""
        if isinstance(value, list):
            return all(BioyondSirnaStation._is_blank(item) for item in value)
        if isinstance(value, dict):
            return not value
        return False


def main() -> int:
    """命令行入口：读取配置并拉取工作流列表。"""
    assert DEBUG_CLI_ENABLED == True, "main 是调试/CLI 快捷入口，运行时不应调用 sirna_station.py 的 CLI 路径"

    parser = argparse.ArgumentParser(description="Sirna Station 工作流列表拉取")
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
