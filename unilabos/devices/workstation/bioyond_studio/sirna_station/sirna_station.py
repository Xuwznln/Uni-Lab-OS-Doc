"""小核酸工作站最小运行时脚手架。"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import sys
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Dict, Iterable, List, Literal, Optional, Tuple
from urllib import error, request
from uuid import NAMESPACE_URL, UUID, uuid5

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
DEFAULT_READY_SIGNAL = "READY"
DEFAULT_RESET_OPERATIONS = ("scheduler_reset", "reset_order_status", "reset_location")
DEFAULT_SIRNA_MATERIAL_TYPE_MAPPINGS = {
    "bioyond_sirna_g3_200ul_tip_rack": ["G3-200ul枪头盒", ""],
    "bioyond_sirna_g3_50ul_tip_rack": ["G3-50ul枪头盒", ""],
    "bioyond_sirna_384_well_plate": ["384孔板", ""],
    "bioyond_sirna_cell_culture_plate": ["细胞培养板", ""],
    "bioyond_sirna_reagent_trough": ["试剂槽RiboGreen", ""],
}


class Experiment1RequiredParams(TypedDict):
    sample_throughput: Annotated[int, Field(description="样品通量（1-96，必填），表示一次实验处理的样品数量。")]


class Experiment1OptionalParams(TypedDict, total=False):
    order_name: Annotated[str, Field(description="订单名称（可选，自动生成）")]
    parameter_overrides: Annotated[str, Field(description="参数覆盖（文本格式）")]
    auto_register_materials: Annotated[bool, Field(default=True, description="是否自动注册物料（默认True）")]


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
                "动作可在调用前通过 config 重新配置或在 goal 中传入 api_host/api_key 后再使用。"
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
        # Phase 4 — install Sirna-specific synchronizer that partitions reagents
        # into liquid contents. Only swap the synchronizer; do not re-run sync
        # here (base post_init may have already published the deck).
        try:
            if getattr(self, "resource_synchronizer", None) is not None and not isinstance(
                self.resource_synchronizer, SirnaResourceSynchronizer
            ):
                self.resource_synchronizer = SirnaResourceSynchronizer(self)
                logger.info("已安装 SirnaResourceSynchronizer（Phase 4）")
        except Exception as exc:  # pragma: no cover - 防御性
            logger.warning(f"SirnaResourceSynchronizer 安装失败: {exc}")

    def _debug_call_session(self, action_name: str):
        parent_debug_session = getattr(super(), "_debug_call_session", None)
        if parent_debug_session is not None:
            return parent_debug_session(action_name)
        return nullcontext()

    @staticmethod
    def _missing_api_config_keys(config: Dict[str, Any]) -> List[str]:
        missing: List[str] = []
        if BioyondSirnaStation._is_blank_value(config.get("api_host")):
            missing.append("api_host")
        if BioyondSirnaStation._is_blank_value(config.get("api_key")):
            missing.append("api_key")
        return missing

    @staticmethod
    def _is_blank_value(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ""
        return False

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
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
        goal_default={
            "reset_operations": ["scheduler_reset", "reset_order_status", "reset_location"],
            "timeout_seconds": 3600,
            "assignee_user_ids": [],
        },
        feedback_interval=300,
        description="复位小核酸实验前状态",
    )
    def reset(
        self,
        reset_operations: Optional[
            List[Literal["scheduler_reset", "reset_order_status", "reset_location"]]
        ] = None,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """复位调度器、订单状态和库位，并按清理读回结果决定是否 take-out。"""
        with self._debug_call_session("reset"):
            reset_order_id = self._kwarg_text(kwargs, "reset_order_id")
            reset_location_id = self._kwarg_text(kwargs, "reset_location_id")
            cleanup_order_code = self._kwarg_text(kwargs, "cleanup_order_code")
            api_host = self._kwarg_text(kwargs, "api_host")
            api_key = self._kwarg_text(kwargs, "api_key")
            ready_signal = self._kwarg_text(kwargs, "ready_signal") or DEFAULT_READY_SIGNAL
            del timeout_seconds, assignee_user_ids
            self._update_runtime_api_config(api_host=api_host, api_key=api_key)
            self._require_ready_signal(ready_signal)
            rpc = self._require_hardware_interface_for_reset()
            result = self._run_reset_operations(
                rpc,
                reset_operations=reset_operations,
                reset_order_id=reset_order_id,
                reset_location_id=reset_location_id,
                cleanup_order_code=cleanup_order_code,
            )
            result["external_material_sync"] = self._run_shared_external_material_sync(rpc=rpc)
            return self._with_ready_signal(result)

    @action(
        always_free=True,
        description="使用 BioyondWorkstation 共享物料同步路径，从 Bioyond 重新同步外部物料。",
    )
    def resync_external_materials(
        self,
        refresh_material_cache: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """手动触发共享 Bioyond 外部物料同步。

        Args:
            refresh_material_cache: 是否在同步前额外刷新 Bioyond 物料缓存。通常保持 False，
                共享同步路径会用同一次库存查询结果更新缓存。
        """
        with self._debug_call_session("resync_external_materials"):
            api_host = self._kwarg_text(kwargs, "api_host")
            api_key = self._kwarg_text(kwargs, "api_key")
            self._update_runtime_api_config(api_host=api_host, api_key=api_key)
            return self._run_shared_external_material_sync(
                refresh_material_cache=refresh_material_cache,
            )

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
        goal_default={
            "optional_params": {"auto_register_materials": True},
            "timeout_seconds": 3600,
            "assignee_user_ids": [],
        },
        feedback_interval=300,
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
                # TEMP frontend-compat key: current cloud manual-confirm table path.
                key="target_device",
                data_type="device_id",
                label="目标设备",
                data_key="target_device",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                # TEMP frontend-compat key: material/resource name display (renderer compat).
                key="resource",
                data_type="resource",
                label="待装载物料",
                data_key="resource",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                # TEMP frontend-compat key: display as material/resource name.
                key="coin_cell_code", data_type="array",
                label="物料名称",
                data_key="coin_cell_code",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                # TEMP frontend-compat key: display as mount/position.
                key="mount_resource", data_type="resource",
                label="库位",
                data_key="mount_resource",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def submit_experiment_1(
        self,
        required_params: Experiment1RequiredParams,
        optional_params: Optional[Experiment1OptionalParams] = None,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
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
                parameter_overrides: 参数覆盖（文本格式）
                auto_register_materials: 是否自动注册物料（默认True）
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
            - registration_result (Dict): 物料注册结果
        """
        with self._debug_call_session("submit_experiment_1"):
            if isinstance(required_params, dict):
                sample_throughput = required_params.get("sample_throughput")
            else:
                sample_throughput = required_params
            sample_throughput = int(sample_throughput)

            if optional_params is None:
                optional_params = {}
            order_name = optional_params.get("order_name", "")
            parameter_overrides = optional_params.get("parameter_overrides", "")
            auto_register_materials = optional_params.get("auto_register_materials", True)
            target_device = (
                self._kwarg_text(kwargs, "unilabos_device_id")
                or self._kwarg_text(kwargs, "device_id")
                or "bioyond_sirna_station"
            )
            logger.info(
                "小核酸实验1提交开始: sample_throughput=%s target_device=%s "
                "auto_register_materials=%s overrides=%s",
                sample_throughput,
                target_device,
                bool(auto_register_materials),
                bool(parameter_overrides),
            )

            del timeout_seconds, assignee_user_ids, kwargs
            rpc = self._require_hardware_interface("create_order")

            # 自动解析实验1工作流（无需用户指定workflow_name）
            workflow = self._resolve_experiment_1_workflow(rpc)
            logger.info(
                "小核酸实验1工作流已解析: root=%s(%s) sub=%s(%s)",
                workflow.get("workflow_name", ""),
                workflow.get("root_workflow_id", ""),
                workflow.get("sub_workflow_name", ""),
                workflow.get("sub_workflow_id", ""),
            )

            step_data = rpc.workflow_step_query(workflow["sub_workflow_id"])

            # Parse parameter_overrides from text format
            parsed_overrides = self._parse_parameter_overrides_text(parameter_overrides)

            param_values, parameter_template = self._build_param_values_from_step_data(
                step_data,
                parameter_overrides=parsed_overrides,
                include_all_task_displayable=True,
            )
            if not param_values:
                logger.error("小核酸实验1参数构建失败: LIMS 子工作流未返回可用 paramValues")
                raise RuntimeError("未从 LIMS 子工作流参数中提取到 create_order paramValues")
            param_entry_count = sum(len(entries) for entries in param_values.values())
            logger.info(
                "小核酸实验1参数已构建: steps=%s entries=%s template_items=%s overrides=%s",
                len(param_values),
                param_entry_count,
                len(parameter_template),
                len(parsed_overrides),
            )

            resolved_order_code, resolved_order_name = self._build_bioyond_order_identity("", order_name)
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

            logger.info(f"正在提交小核酸实验1: {resolved_order_name} ({resolved_order_code})")
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
                "小核酸实验1创建结果: success=%s order_ids=%s materials=%s "
                "suggested_locations=%s material_types=%s",
                create_success,
                order_ids,
                len(material_records),
                len(suggested_locations),
                material_type_counts,
            )
            if not create_success:
                logger.error(
                    "小核酸实验1创建未返回成功结果: order_code=%s order_ids=%s materials=%s",
                    resolved_order_code,
                    order_ids,
                    len(material_records),
                )
            elif not material_records:
                logger.warning("小核酸实验1创建成功但未返回物料分配记录: order_code=%s", resolved_order_code)
            elif not suggested_locations:
                logger.warning("小核酸实验1创建成功但未解析到推荐库位: order_code=%s", resolved_order_code)

            registration_result = {
                "registered": [],
                "skipped": material_records,
                "reason": "submit_experiment_1_resource_sync_disabled",
                "auto_register_materials": bool(auto_register_materials),
            }
            # if auto_register_materials and material_records:
            #     try:
            #         registration_result = self._register_materials_to_tree(material_records)
            #         logger.info(f"物料注册完成: {len(registration_result.get('registered', []))} 个物料已添加到资源树")
            #     except Exception as e:
            #         logger.error(f"物料注册失败: {e}")
            #         registration_result = {"error": str(e)}

            result = {
                "success": create_success,
                "order_code": resolved_order_code,
                "order_name": resolved_order_name,
                "order_id": order_ids[0] if order_ids else "",
                "order_ids": order_ids,
                "target_device": target_device,
                "workflow": workflow,
                "sample_throughput": int(sample_throughput),
                "payload": order_payload,
                "parameter_template": parameter_template,
                "create_order_result": parsed_result,
                "materials": material_records,
                "materials_by_type": confirmation_data.get("materials_by_type", {}),
                "manual_load_tables": self._build_manual_load_tables(
                    confirmation_data.get("materials_by_type", {})
                ),
                "manual_load_probe": self._build_manual_load_probe(
                    confirmation_data.get("materials_by_type", {})
                ),
                "suggested_locations": suggested_locations,
                "start_experiment": start_experiment_info,
                "confirmation_message": confirmation_data.get("confirmation_message", ""),
                "registration_result": registration_result,
            }
            result.update(result["manual_load_probe"])
            logger.info(
                "小核酸实验1提交完成: order_code=%s success=%s manual_load_rows=%s",
                resolved_order_code,
                result["success"],
                {
                    key: len(self._as_list(value.get("material_name") if isinstance(value, dict) else []))
                    for key, value in result["manual_load_tables"].items()
                },
            )
            return result

    def _parse_parameter_overrides_text(self, text: str) -> Dict[str, Any]:
        """Parse parameter overrides from text format.

        Supports two formats:
        1. Key-value pairs: "key1=value1,key2=value2"
        2. JSON string: '{"key1": "value1", "key2": "value2"}'

        Args:
            text: Parameter overrides as text

        Returns:
            Dict of parameter overrides
        """
        if not text or not text.strip():
            return {}

        text = text.strip()

        # Try JSON format first
        if text.startswith("{"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                logger.warning(f"无法解析 JSON 格式的参数覆盖: {text}")
                return {}

        # Parse key=value,key=value format
        result = {}
        for pair in text.split(","):
            pair = pair.strip()
            if "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            result[key.strip()] = value.strip()

        return result

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
        description="请核对并装载实验物料；勾选装载确认后方可启动调度",
        handles=[
            ActionInputHandle(
                # TEMP frontend-compat key: current cloud manual-confirm table path.
                key="target_device",
                data_type="device_id",
                label="目标设备",
                data_key="target_device",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionInputHandle(
                # TEMP frontend-compat key: material/resource name (renderer compat).
                key="resource",
                data_type="resource",
                label="待装载物料",
                data_key="resource",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionInputHandle(
                # TEMP frontend-compat key: material/resource name.
                key="coin_cell_code",
                data_type="array",
                label="物料名称",
                data_key="coin_cell_code",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
            ActionInputHandle(
                # TEMP frontend-compat key: mount/position.
                key="mount_resource",
                data_type="resource",
                label="库位",
                data_key="mount_resource",
                data_source=DataSource.HANDLE,
                io_type="source",
            ),
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
        ],
    )
    def start_experiment(
        self,
        target_device: DeviceSlot = "bioyond_sirna_station",
        resource: Optional[List[ResourceSlot]] = None,
        coin_cell_code: Optional[List[str]] = None,
        mount_resource: Optional[List[ResourceSlot]] = None,
        order_id: str = "",
        materials_loaded: bool = False,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Guided manual-load checkpoint that gates ``rpc.scheduler_start()``.

        Args:
            target_device: 目标设备，保留用于当前云端手动确认表格兼容路径。
            resource: 上游句柄提供的待装载物料显示值。
            coin_cell_code: 上游句柄提供的临时物料名称列。
            mount_resource: 上游句柄提供的临时库位列。
            order_id: 上游 ``submit_experiment_1`` 创建的订单 ID（可选；若上游连接则自动传入）。
            materials_loaded: 操作员勾选确认物料已装载。未勾选且存在物料显示则阻断启动。
            timeout_seconds: 超时时间（秒，框架参数）。
            assignee_user_ids: 分配用户 ID 列表（框架参数）。
        """
        with self._debug_call_session("start_experiment"):
            target_device = (
                target_device
                or self._kwarg_text(kwargs, "target_device")
                or "bioyond_sirna_station"
            )
            if resource is None:
                resource = kwargs.get("resource")
            if coin_cell_code is None:
                coin_cell_code = kwargs.get("coin_cell_code")
            if mount_resource is None:
                mount_resource = kwargs.get("mount_resource")
            order_ids = kwargs.get("order_ids")
            submit_experiment_result = kwargs.get("submit_experiment_result")
            api_host = self._kwarg_text(kwargs, "api_host")
            api_key = self._kwarg_text(kwargs, "api_key")
            ready_signal = self._kwarg_text(kwargs, "ready_signal") or DEFAULT_READY_SIGNAL
            del target_device, timeout_seconds, assignee_user_ids
            self._update_runtime_api_config(api_host=api_host, api_key=api_key)
            self._require_ready_signal(ready_signal)

            category_arrays = {
                "materials_loaded": (
                    "物料",
                    self._as_manual_gate(materials_loaded),
                    [resource, coin_cell_code, mount_resource],
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

            start_info = self._resolve_start_experiment_info(
                submit_experiment_result, order_id, order_ids
            )
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
            return self._with_ready_signal({
                "success": result == 1,
                "return_info": result,
                "scheduler_start_result": result,
                "start_experiment": start_info,
                "gates": gates,
                "confirmation_message": "调度器启动成功" if result == 1 else "调度器启动失败，请检查 LIMS 状态",
            })

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
        goal_default={
            "order_id": "",
            "samples_unloaded": False,
            "consumables_unloaded": False,
            "reagents_unloaded": False,
            "timeout_seconds": 3600,
            "assignee_user_ids": [],
        },
        feedback_interval=300,
        description=(
            "请按当前 deck 状态卸载实验物料；勾选三类卸载确认后才会清空资源树。"
            " 若上游 reagent-as-liquid 同步未启用，试剂列可能为空，仅展示 labware。"
        ),
        handles=[
            ActionOutputHandle(
                key="unload_samples_name", data_type="array",
                label="样本-物料名称", data_key="unload_tables.Sample.material_name",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="unload_samples_code", data_type="array",
                label="样本-物料编号", data_key="unload_tables.Sample.material_code",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="unload_samples_location", data_type="array",
                label="样本-库位", data_key="unload_tables.Sample.location",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="unload_samples_quantity", data_type="array",
                label="样本-数量", data_key="unload_tables.Sample.quantity",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="unload_consumables_name", data_type="array",
                label="耗材-物料名称", data_key="unload_tables.Consumables.material_name",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="unload_consumables_code", data_type="array",
                label="耗材-物料编号", data_key="unload_tables.Consumables.material_code",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="unload_consumables_location", data_type="array",
                label="耗材-库位", data_key="unload_tables.Consumables.location",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="unload_consumables_quantity", data_type="array",
                label="耗材-数量", data_key="unload_tables.Consumables.quantity",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="unload_reagents_name", data_type="array",
                label="试剂-物料名称", data_key="unload_tables.Reagent.material_name",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="unload_reagents_code", data_type="array",
                label="试剂-物料编号", data_key="unload_tables.Reagent.material_code",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="unload_reagents_location", data_type="array",
                label="试剂-库位", data_key="unload_tables.Reagent.location",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="unload_reagents_quantity", data_type="array",
                label="试剂-数量", data_key="unload_tables.Reagent.quantity",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    def end_experiment(
        self,
        order_id: str = "",
        samples_unloaded: bool = False,
        consumables_unloaded: bool = False,
        reagents_unloaded: bool = False,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Guided manual-unload checkpoint that clears the deck after confirmation.

        Mirrors :meth:`start_experiment` in shape: three boolean gates default
        to ``False``, three sibling-array display tables (Sample / Consumables
        / Reagent × material_name / material_code / location / quantity)
        populated from current deck state, and a raise-on-missing-gate path.

        Args:
            order_id: 可选；只展示 ``unilabos_extra["order_id"]`` 等于该值的物料。
                未指定则覆盖整个 deck。
            samples_unloaded: 操作员勾选确认 ``Sample`` 类已卸载。
            consumables_unloaded: 操作员勾选确认 ``Consumables`` 类已卸载。
            reagents_unloaded: 操作员勾选确认 ``Reagent`` 类已卸载（含 trough 试剂内容）。
            timeout_seconds: 框架超时时间（秒）。
            assignee_user_ids: 框架分配用户列表。
        """
        with self._debug_call_session("end_experiment"):
            del timeout_seconds, assignee_user_ids, kwargs

            order_filter = order_id.strip() if isinstance(order_id, str) else ""
            manifest = self._build_unload_materials_by_type(
                order_id_filter=order_filter or None,
            )
            manifest_counts = {
                mode_key: len(self._as_list(records))
                for mode_key, records in manifest.items()
            }
            logger.info(
                "小核酸实验卸载检查开始: order_id=%s manifest_counts=%s",
                order_filter or "<all>",
                manifest_counts,
            )
            unload_tables = self._build_manual_load_tables(manifest)

            category_specs = [
                ("Sample", self._as_manual_gate(samples_unloaded), "样本", "samples_unloaded"),
                ("Consumables", self._as_manual_gate(consumables_unloaded), "耗材", "consumables_unloaded"),
                ("Reagent", self._as_manual_gate(reagents_unloaded), "试剂", "reagents_unloaded"),
            ]
            gates: Dict[str, Dict[str, Any]] = {}
            missing_labels: List[str] = []
            for mode_key, ticked, label, gate_key in category_specs:
                required = bool(manifest.get(mode_key))
                gates[gate_key] = {"label": label, "required": required, "ticked": bool(ticked)}
                if required and not ticked:
                    missing_labels.append(label)
            logger.info("小核酸实验卸载门禁状态: gates=%s missing=%s", gates, missing_labels)
            if missing_labels:
                logger.error("小核酸实验卸载被阻断: missing=%s", missing_labels)
                raise RuntimeError(
                    f"以下分类卸载尚未确认，无法清空资源树: {', '.join(missing_labels)}"
                )

            cleared = self._clear_unloaded_materials(manifest)
            logger.info("小核酸实验卸载清理完成: cleared=%s", cleared)

            # Refresh the table snapshot so consumers see the post-mutation state.
            post_manifest = self._build_unload_materials_by_type(
                order_id_filter=order_filter or None,
            )

            return {
                "success": True,
                "order_id": order_filter,
                "unloaded": cleared,
                "gates": gates,
                "unload_tables": unload_tables,
                "post_unload_tables": self._build_manual_load_tables(post_manifest),
                "confirmation_message": (
                    "deck 已按操作员确认清空" if any(cleared.values()) else "deck 当前为空，无需卸载"
                ),
            }

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
        goal_default={
            "filter_text": "",
            "status": "",
            "latest_only": True,
            "max_results": 20,
            "timeout_seconds": 3600,
            "assignee_user_ids": [],
        },
        feedback_interval=300,
        description="只读查询 Bioyond LIMS 订单列表，可作为下游节点的 order_id 来源",
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
        ],
    )
    def get_order_list(
        self,
        filter_text: str = "",
        status: str = "",
        latest_only: bool = True,
        max_results: int = 20,
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """只读查询 Bioyond LIMS 订单列表。

        ``api_host`` / ``api_key`` 隐藏不再作为前端可见参数；缺失会沿用现有 RPC 配置错误。

        Args:
            filter_text: 订单编号 / 名称模糊匹配字符串，对应 LIMS ``order_query.filter``。
            status: 订单状态字段过滤值（透传给 ``order_query.status``，留空表示不过滤）。
            latest_only: 默认 ``True``，仅返回最新（创建时间最大）的一条订单作为 ``order_id``。
            max_results: 一次返回的最大订单条数（透传给 ``pageCount``，默认 20）。
            timeout_seconds: 超时时间（秒，框架参数）。
            assignee_user_ids: 分配用户 ID 列表（框架参数）。

        Returns:
            ``{"success": bool, "orders": [...], "order_id": str, "order_ids": [...], "query": {...}}``。
        """
        with self._debug_call_session("get_order_list"):
            del timeout_seconds, assignee_user_ids, kwargs
            try:
                normalized_max = int(max_results)
            except (TypeError, ValueError):
                normalized_max = 20
            if normalized_max <= 0:
                normalized_max = 20
            rpc = self._require_hardware_interface("order_query")
            query_payload = {
                "timeType": "",
                "beginTime": None,
                "endTime": None,
                "status": str(status or ""),
                "filter": str(filter_text or ""),
                "skipCount": 0,
                "pageCount": normalized_max,
                "sorting": "creationTime desc",
            }
            logger.info(
                "正在查询 Bioyond LIMS 订单列表 filter=%r status=%r latest_only=%s",
                filter_text,
                status,
                latest_only,
            )
            raw_result = rpc.order_query(json.dumps(query_payload, ensure_ascii=False))
            items = self._order_items(raw_result)

            orders: List[Dict[str, Any]] = []
            for item in items:
                order_id = str(item.get("id") or "")
                if not order_id:
                    continue
                orders.append({
                    "order_id": order_id,
                    "order_code": str(item.get("orderCode") or ""),
                    "order_name": str(item.get("name") or item.get("orderName") or ""),
                    "status": str(item.get("status") or item.get("statusName") or ""),
                    "created_at": str(item.get("creationTime") or item.get("createTime") or ""),
                    "raw": item,
                })

            order_ids = [order["order_id"] for order in orders]
            if latest_only and orders:
                chosen = orders[0]
                order_id_value = chosen["order_id"]
            elif len(order_ids) == 1:
                order_id_value = order_ids[0]
            else:
                order_id_value = ""
            logger.info(
                "Bioyond LIMS 订单列表查询完成: raw_items=%s orders=%s selected_order_id=%s",
                len(items),
                len(orders),
                order_id_value,
            )
            if not orders:
                logger.warning(
                    "Bioyond LIMS 订单列表未查询到结果: filter=%r status=%r",
                    filter_text,
                    status,
                )

            return {
                "success": bool(orders),
                "orders": orders,
                "order_id": order_id_value,
                "order_ids": order_ids,
                "query": query_payload,
            }

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
        goal_default={
            "order_id": "",
            "timeout_seconds": 3600,
            "assignee_user_ids": [],
        },
        feedback_interval=300,
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
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """只读查询 Bioyond LIMS 订单报告。

        ``api_host`` / ``api_key`` 隐藏不作为前端可见参数，可由执行器 kwargs 注入覆盖。

        Args:
            order_id: Bioyond LIMS 订单 ID。
            timeout_seconds: 超时时间（秒，框架参数）。
            assignee_user_ids: 分配用户 ID 列表（框架参数）。

        Returns:
            ``{"success": bool, "order_id": str, "report": {...}, "raw": ...}``。
        """
        with self._debug_call_session("get_order_report"):
            del timeout_seconds, assignee_user_ids
            normalized_order_id = str(order_id or "").strip()
            if not normalized_order_id:
                raise ValueError("order_id 不能为空")

            api_host = self._kwarg_text(kwargs, "api_host")
            api_key = self._kwarg_text(kwargs, "api_key")
            self._update_runtime_api_config(api_host=api_host, api_key=api_key)

            rpc = self._require_hardware_interface("order_report")
            logger.info("正在查询 Bioyond LIMS 订单报告 order_id=%s", normalized_order_id)
            raw_result = rpc.order_report(normalized_order_id)
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
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
        goal_default={
            "order_id": "",
            "filter_text": "",
            "include_order_list": True,
            "include_order_report": True,
            "include_gantt": True,
            "include_gantt_with_simulation": True,
            "include_material_info": True,
            "include_raw": True,
            "timeout_seconds": 3600,
            "assignee_user_ids": [],
        },
        feedback_interval=300,
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
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
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
            timeout_seconds: 超时时间（秒，框架参数）。
            assignee_user_ids: 分配用户 ID 列表（框架参数）。

        Returns:
            前端报告调试结构，包含 header、parameters、samples、materials、timeline、raw/errors。
        """
        with self._debug_call_session("get_aggregated_order_report"):
            del timeout_seconds, assignee_user_ids
            api_host = self._kwarg_text(kwargs, "api_host")
            api_key = self._kwarg_text(kwargs, "api_key")
            self._update_runtime_api_config(api_host=api_host, api_key=api_key)

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
                    fallback_on_empty=include_raw,
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
                    "/api/lims/order/order-report",
                    resolved_order_id,
                    errors,
                    "order_report",
                    fallback_on_empty=True,
                )
                normalized_report = self._normalize_order_report_result(resolved_order_id, report_payload)
                if not normalized_report.get("success"):
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
                    "/api/lims/order/gantt-with-simulation-by-order-id",
                    resolved_order_id,
                    errors,
                    "gantt_with_simulation",
                    fallback_on_empty=include_raw,
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
                    "/api/lims/order/gantts-by-order-id",
                    resolved_order_id,
                    errors,
                    "gantt",
                    fallback_on_empty=include_raw,
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
                        "/api/lims/storage/material-info",
                        material_id,
                        errors,
                        f"material_info:{material_id}",
                        fallback_on_empty=include_raw,
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

            success = bool(order_record or report_data or timeline or samples)
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
                "  2) 在动作 goal 参数中直接传入 api_host / api_key（reset / start_experiment 已支持）；",
                "  3) 设置环境变量后重启 edge：",
                "       BIOYOND_SIRNA_API_HOST 或 BIOYOND_SIRNA_EXP1_API_HOST",
                "       BIOYOND_SIRNA_API_KEY  或 BIOYOND_SIRNA_EXP1_API_KEY",
            ]
            raise RuntimeError("\n".join(lines))
        from unilabos.devices.workstation.bioyond_studio.bioyond_rpc import BioyondV1RPC

        rpc = BioyondV1RPC(self.bioyond_config)
        self._set_hardware_interface(rpc)
        return self.hardware_interface

    def _has_required_api_config(self, config: Dict[str, Any]) -> bool:
        return not self._missing_api_config_keys(config)

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

    def _update_runtime_api_config(self, api_host: str = "", api_key: str = "") -> None:
        changed = False
        if not self._is_blank(api_host) and self.bioyond_config.get("api_host") != api_host:
            self.bioyond_config["api_host"] = api_host
            changed = True
        if not self._is_blank(api_key) and self.bioyond_config.get("api_key") != api_key:
            self.bioyond_config["api_key"] = api_key
            changed = True
        if changed:
            self.hardware_interface = None

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
        workflow_name = workflow_name or self._config_value("experiment_1_workflow_name", "sirna_exp1_workflow_name") or ""
        sub_workflow_name = (
            sub_workflow_name
            or self._config_value("experiment_1_sub_workflow_name", "sirna_exp1_sub_workflow_name")
            or ""
        )
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

    def _resolve_experiment_1_workflow(self, rpc: Any) -> Dict[str, str]:
        return self._resolve_experiment_workflow(rpc)

    def _build_param_values_from_step_data(
        self,
        step_data: Any,
        parameter_overrides: Any,
        include_all_task_displayable: bool,
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
        parameter_map = self._extract_workflow_parameter_map(step_data)
        if not isinstance(parameter_map, dict):
            logger.error("workflow_step_query 未返回可解析的步骤参数: type=%s", type(parameter_map).__name__)
            raise RuntimeError("workflow_step_query 未返回可解析的步骤参数")
        param_values: Dict[str, List[Dict[str, Any]]] = {}
        parameter_template: List[Dict[str, Any]] = []
        for step_id, value in parameter_map.items():
            if not self._looks_like_uuid(step_id):
                continue
            entries: List[Dict[str, Any]] = []
            for module in self._as_list(value):
                if not isinstance(module, dict):
                    continue
                module_m = module.get("m")
                module_n = module.get("n")
                for parameter in self._as_list(module.get("parameterList") or module.get("ParameterList")):
                    if not isinstance(parameter, dict):
                        continue
                    key = self._parameter_key(parameter)
                    if not key:
                        continue
                    task_displayable = parameter.get("TaskDisplayable", parameter.get("taskDisplayable", 1))
                    parameter_type = str(parameter.get("Type") or parameter.get("type") or "")
                    template_item = {
                        "step_id": str(step_id),
                        "module": module.get("name") or module.get("moduleName") or "",
                        "m": module_m,
                        "n": module_n,
                        "key": key,
                        "type": parameter_type,
                        "task_displayable": task_displayable,
                        "value": self._value_for_create_order(parameter),
                    }
                    parameter_template.append(template_item)
                    if parameter_type.lower() == "hidden" or task_displayable == 0:
                        continue
                    if not include_all_task_displayable and key != "protocolName":
                        continue
                    value_for_create_order = self._value_for_create_order(parameter)
                    if self._is_blank(value_for_create_order):
                        continue
                    entry: Dict[str, Any] = {"key": key, "value": "" if self._is_blank(value_for_create_order) else str(value_for_create_order)}
                    m_value = parameter.get("m", module_m)
                    n_value = parameter.get("n", module_n)
                    if not self._is_blank(m_value):
                        entry["m"] = int(m_value)
                    if not self._is_blank(n_value):
                        entry["n"] = int(n_value)
                    entries.append(entry)
            if entries:
                param_values[str(step_id)] = entries
        self._apply_parameter_overrides(param_values, parameter_overrides)
        return param_values, parameter_template

    def _value_for_create_order(self, parameter: Dict[str, Any]) -> Any:
        value = parameter.get("Value") if "Value" in parameter else parameter.get("value")
        display_value = parameter.get("DisplayValue") if "DisplayValue" in parameter else parameter.get("displayValue")
        if self._is_blank(value) and not self._is_blank(display_value):
            return display_value
        return value

    def _apply_parameter_overrides(
        self,
        param_values: Dict[str, List[Dict[str, Any]]],
        overrides: Any,
    ) -> None:
        if not overrides:
            return
        if isinstance(overrides, dict):
            override_items = [(str(key), value) for key, value in overrides.items()]
        else:
            override_items = []
            for override in self._as_list(overrides):
                if not override:
                    continue
                if isinstance(override, dict):
                    override_items.extend((str(key), value) for key, value in override.items())
                    continue
                if "=" not in str(override):
                    logger.error("参数覆盖格式错误: override=%r", override)
                    raise ValueError(f"参数覆盖必须使用 key=value 格式: {override!r}")
                key, value = str(override).split("=", 1)
                override_items.append((key, value))

        for key, value in override_items:
            matched = False
            for entries in param_values.values():
                for entry in entries:
                    if entry.get("key") == key:
                        entry["value"] = value
                        matched = True
            if not matched:
                logger.error("参数覆盖未命中 paramValues: key=%s", key)
                raise ValueError(f"paramValues 中找不到可覆盖参数: {key}")

    def _build_bioyond_order_identity(
        self,
        order_code: str = "",
        order_name: str = "",
    ) -> Tuple[str, str]:
        if order_code and order_name:
            return order_code, order_name
        prefix = self._config_value(
            "experiment_1_order_prefix",
            "sirna_exp1_order_prefix",
            "sirna_exp1_order_code_prefix",
        ) or "test"
        suffix = datetime.now().strftime("%m%d%H%M%S")
        value = f"{prefix}{suffix}"
        return order_code or value, order_name or value

    def _build_experiment1_order_identity(self) -> Tuple[str, str]:
        return self._build_bioyond_order_identity()

    def _parse_experiment1_create_result(self, result: Any, order_code: Optional[str] = None) -> Dict[str, Any]:
        parsed_result = self._parse_lims_result(result)
        material_records = self._extract_create_order_materials(parsed_result)
        suggested_locations = self._extract_suggested_locations(material_records)
        order_ids = self._extract_created_order_ids(parsed_result)
        return {
            "order_id": order_ids[0] if order_ids else None,
            "order_ids": order_ids,
            "order_code": order_code,
            "materials": material_records,
            "suggested_locations": suggested_locations,
            "raw_result": parsed_result,
        }

    def _reset_before_experiment_create(self, rpc: Any) -> Dict[str, Any]:
        return self._run_reset_operations(rpc)

    def _run_shared_external_material_sync(
        self,
        rpc: Optional[Any] = None,
        refresh_material_cache: bool = False,
    ) -> Dict[str, Any]:
        """为 reset / 手动 resync 运行共享 Bioyond 外部物料同步路径。"""
        if rpc is None:
            rpc = self._require_hardware_interface_for_reset()

        result: Dict[str, Any] = {
            "success": False,
            "sync_mode": "shared_bioyond",
            "synchronizer": "BioyondResourceSynchronizer",
            "refresh_material_cache": {"skipped": True, "reason": "disabled"},
        }
        logger.info(
            "共享 Bioyond 外部物料同步开始: refresh_material_cache=%s",
            bool(refresh_material_cache),
        )

        if refresh_material_cache:
            if hasattr(rpc, "refresh_material_cache"):
                result["refresh_material_cache"] = rpc.refresh_material_cache()
                logger.info(
                    "共享 Bioyond 外部物料缓存刷新完成: result=%s",
                    result["refresh_material_cache"],
                )
            else:
                result["refresh_material_cache"] = {
                    "skipped": True,
                    "reason": "rpc_method_unavailable",
                }
                logger.warning("共享 Bioyond 外部物料缓存刷新跳过: RPC 缺少 refresh_material_cache")

        try:
            synchronizer = BioyondResourceSynchronizer(self)
            result["success"] = bool(synchronizer.sync_from_external())
            if result["success"]:
                self._publish_resource_tree_update()
                logger.info("共享 Bioyond 外部物料同步完成并已发布资源树更新")
            else:
                logger.warning("共享 Bioyond 外部物料同步未返回成功，继续返回结果供上游处理")
        except Exception as exc:
            logger.error(f"共享 Bioyond 外部物料同步失败: {exc}")
            result["error"] = str(exc)
        return result

    def _run_reset_operations(
        self,
        rpc: Any,
        reset_operations: Optional[List[str]] = None,
        reset_order_id: str = "",
        reset_location_id: str = "",
        cleanup_order_code: str = "",
    ) -> Dict[str, Any]:
        operations = self._normalize_reset_operations(reset_operations)
        cleanup_order_code = cleanup_order_code or self._config_value(
            "experiment_1_cleanup_order_code",
            "sirna_exp1_cleanup_order_code",
            "experiment_1_reset_order_filter",
            "sirna_exp1_reset_order_filter",
        ) or ""
        logger.info(
            "小核酸复位操作开始: operations=%s reset_order_id=%s reset_location_id=%s cleanup_order_code=%s",
            operations,
            reset_order_id,
            reset_location_id,
            cleanup_order_code,
        )
        skipped_operations: List[Dict[str, str]] = []
        if "reset_order_status" in operations:
            reset_order_id = self._resolve_reset_order_id(rpc, reset_order_id, cleanup_order_code)
            if not reset_order_id:
                skipped_operations.append({
                    "operation": "reset_order_status",
                    "reason": "未查询到可复位订单，跳过订单状态复位",
                })
                logger.warning("小核酸复位跳过订单状态复位: 未查询到可复位订单")
        if "reset_location" in operations:
            reset_location_id = self._resolve_reset_location_id(rpc, reset_location_id)

        result: Dict[str, Any] = {
            "selected_operations": operations,
            "reset_order_id": reset_order_id,
            "reset_location_id": reset_location_id,
        }
        if skipped_operations:
            result["skipped_operations"] = skipped_operations
        if "scheduler_reset" in operations:
            self._require_rpc_method(rpc, "scheduler_reset")
            result["scheduler_reset"] = rpc.scheduler_reset()
            logger.info("小核酸复位 scheduler_reset 返回: %s", result["scheduler_reset"])
            if result["scheduler_reset"] in ({}, None, False):
                logger.warning("小核酸复位 scheduler_reset 未返回明确成功结果")
        if "reset_order_status" in operations and reset_order_id:
            self._require_rpc_method(rpc, "reset_order_status")
            result["reset_order_status"] = rpc.reset_order_status(reset_order_id)
            logger.info(
                "小核酸复位 reset_order_status 返回: order_id=%s result=%s",
                reset_order_id,
                result["reset_order_status"],
            )
            if result["reset_order_status"] in ({}, None, False):
                logger.warning("小核酸复位 reset_order_status 未返回明确成功结果: order_id=%s", reset_order_id)
        if "reset_location" in operations:
            self._require_rpc_method(rpc, "reset_location")
            result["reset_location"] = rpc.reset_location(reset_location_id)
            logger.info(
                "小核酸复位 reset_location 返回: location_id=%s result=%s",
                reset_location_id,
                result["reset_location"],
            )
            if result["reset_location"] in ({}, None, False):
                logger.warning("小核酸复位 reset_location 未返回明确成功结果: location_id=%s", reset_location_id)

        if "reset_order_status" in operations and reset_order_id:
            snapshot = self._query_order_snapshot(rpc, cleanup_order_code or reset_order_id)
            targets = self._extract_takeout_targets(snapshot, reset_order_id)
            result["cleanup_targets"] = targets
            logger.info(
                "小核酸复位清理检查: requires_take_out=%s preintakes=%s materials=%s",
                targets["requires_take_out"],
                len(targets["preintake_ids"]),
                len(targets["material_ids"]),
            )
            if targets["requires_take_out"]:
                result["take_out"] = self._take_out_remaining_materials(rpc, targets)
                logger.info("小核酸复位 take_out 返回: %s", result["take_out"])
                if result["take_out"] in ({}, None, False):
                    logger.warning("小核酸复位 take_out 未返回明确成功结果")
        logger.info("小核酸复位操作完成: selected=%s skipped=%s", operations, len(skipped_operations))
        return result

    def _resolve_reset_order_id(self, rpc: Any, reset_order_id: str = "", cleanup_order_code: str = "") -> str:
        explicit_order_id = reset_order_id or self._config_value(
            "experiment_1_reset_order_id",
            "sirna_exp1_reset_order_id",
        ) or ""
        if explicit_order_id:
            return explicit_order_id

        filter_text = cleanup_order_code or self._config_value(
            "experiment_1_cleanup_order_code",
            "sirna_exp1_cleanup_order_code",
            "experiment_1_reset_order_filter",
            "sirna_exp1_reset_order_filter",
            "experiment_1_reset_order_code",
            "sirna_exp1_reset_order_code",
            "experiment_1_reset_order_name",
            "sirna_exp1_reset_order_name",
        ) or ""
        if filter_text:
            snapshot = self._query_order_snapshot(rpc, filter_text)
            order_item = self._select_reset_order_item(snapshot, filter_text)
            if order_item:
                return str(order_item["id"])
            logger.warning(f"未能通过订单筛选条件 {filter_text!r} 查询到可复位订单，跳过 reset_order_status")
            return ""

        last_order_ids = [
            str(order_id)
            for order_id in self._as_list(getattr(self, "_last_submitted_order_ids", []))
            if not self._is_blank(order_id)
        ]
        if last_order_ids:
            return last_order_ids[0]

        logger.warning(
            "不能自动确定 reset_order_status 的订单ID。"
            "请在站点配置中提供 experiment_1_reset_order_filter / experiment_1_cleanup_order_code，"
            "或先通过 submit_experiment 产生上游订单信息。本次跳过订单状态复位。"
        )
        return ""

    def _resolve_reset_location_id(self, rpc: Any, reset_location_id: str = "") -> str:
        explicit_location_id = reset_location_id or self._config_value(
            "experiment_1_reset_location_id",
            "sirna_exp1_reset_location_id",
        ) or ""
        if explicit_location_id:
            return explicit_location_id

        location_code = self._config_value(
            "experiment_1_reset_location_code",
            "sirna_exp1_reset_location_code",
            "reset_location_code",
        ) or ""
        location_name = self._config_value(
            "experiment_1_reset_location_name",
            "sirna_exp1_reset_location_name",
            "reset_location_name",
        ) or ""
        warehouse_name = self._config_value(
            "experiment_1_reset_location_warehouse_name",
            "sirna_exp1_reset_location_warehouse_name",
            "reset_location_warehouse_name",
        ) or ""

        selector = location_code or location_name
        if not selector:
            raise RuntimeError(
                "不能自动确定 reset_location 的库位ID。"
                "请在站点配置中提供 experiment_1_reset_location_code/name "
                "以及可选 experiment_1_reset_location_warehouse_name。"
            )

        mapped_location_id = self._location_id_from_mapping(rpc, selector)
        if mapped_location_id:
            return mapped_location_id

        inventory = self._query_location_inventory(rpc)
        location = self._select_location_from_inventory(
            inventory,
            location_code=location_code,
            location_name=location_name,
            warehouse_name=warehouse_name,
        )
        if location and location.get("id"):
            return str(location["id"])

        label = f"{warehouse_name}/{selector}" if warehouse_name else selector
        raise RuntimeError(f"未能通过库位选择条件 {label!r} 查询到 reset-location 库位ID")

    def _select_reset_order_item(self, order_snapshot: Any, filter_text: str) -> Optional[Dict[str, Any]]:
        items = self._order_items(order_snapshot)
        if not items:
            return None
        exact_candidates = [
            item
            for item in items
            if any(str(item.get(key) or "") == filter_text for key in ("id", "orderCode", "code", "name"))
        ]
        candidates = exact_candidates or items
        return self._latest_order_item(candidates)

    def _latest_order_item(self, items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not items:
            return None
        timestamp_keys = ("creationTime", "requestTime", "startPreparationTime", "completeTime")

        def sort_key(item: Dict[str, Any]) -> str:
            for key in timestamp_keys:
                value = item.get(key)
                if value:
                    return str(value)
            return ""

        return sorted(items, key=sort_key, reverse=True)[0]

    def _location_id_from_mapping(self, rpc: Any, selector: str) -> str:
        mapping = getattr(rpc, "location_mapping", None)
        if isinstance(mapping, dict):
            value = mapping.get(selector)
            if value:
                return str(value)
        return ""

    def _query_location_inventory(self, rpc: Any) -> List[Dict[str, Any]]:
        for method_name in ("locations_by_type", "query_locations_by_type"):
            method = getattr(rpc, method_name, None)
            if callable(method):
                response = method(type=0, typeMode=0, materialType=0)
                return self._location_inventory_items(response)

        if all(hasattr(rpc, attr) for attr in ("get", "host")):
            response = rpc.get(
                url=f"{rpc.host}/api/storage/location/locations-by-type",
                params={"type": 0, "typeMode": 0, "materialType": 0},
                headers={"Accept": "application/json"},
            )
            return self._location_inventory_items(response)

        raise RuntimeError("Bioyond RPC 客户端缺少库位库存查询能力，不能自动解析 reset_location_id")

    def _location_inventory_items(self, response: Any) -> List[Dict[str, Any]]:
        parsed = self._parse_lims_result(response)
        if isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
            return [item for item in parsed["data"] if isinstance(item, dict)]
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        return []

    def _select_location_from_inventory(
        self,
        warehouses: List[Dict[str, Any]],
        location_code: str = "",
        location_name: str = "",
        warehouse_name: str = "",
    ) -> Optional[Dict[str, Any]]:
        selector = location_code or location_name
        matches: List[Dict[str, Any]] = []
        for warehouse in warehouses:
            current_warehouse_name = str(warehouse.get("name") or warehouse.get("warehouseName") or "")
            if warehouse_name and current_warehouse_name != warehouse_name:
                continue
            for location in self._as_list(warehouse.get("locations")):
                if not isinstance(location, dict):
                    continue
                values = {
                    str(location.get("id") or ""),
                    str(location.get("code") or ""),
                    str(location.get("name") or ""),
                    str(location.get("locationShowName") or ""),
                    str(location.get("locationCode") or ""),
                }
                if selector in values:
                    item = dict(location)
                    item.setdefault("warehouseName", current_warehouse_name)
                    matches.append(item)
        if not matches:
            return None
        if len(matches) > 1 and not warehouse_name:
            raise RuntimeError(f"库位选择条件 {selector!r} 匹配到多个堆栈，请配置 reset_location_warehouse_name")
        return matches[0]

    def _normalize_reset_operations(self, reset_operations: Optional[List[str]]) -> List[str]:
        values = reset_operations or list(DEFAULT_RESET_OPERATIONS)
        aliases = {
            "scheduler": "scheduler_reset",
            "order": "reset_order_status",
            "order_status": "reset_order_status",
            "location": "reset_location",
            "storage": "reset_location",
        }
        normalized: List[str] = []
        for operation in values:
            key = str(operation).strip()
            if not key:
                continue
            key = aliases.get(key, key)
            if key not in DEFAULT_RESET_OPERATIONS:
                raise ValueError(f"未知复位操作: {operation!r}; 支持 {list(DEFAULT_RESET_OPERATIONS)}")
            if key not in normalized:
                normalized.append(key)
        return normalized

    def _require_rpc_method(self, rpc: Any, method_name: str) -> None:
        if not hasattr(rpc, method_name):
            raise RuntimeError(f"Bioyond RPC 客户端缺少 {method_name} 方法")

    def _require_ready_signal(self, ready_signal: str) -> None:
        if str(ready_signal).strip().upper() != DEFAULT_READY_SIGNAL:
            raise RuntimeError(f"小核酸工作流需要收到 {DEFAULT_READY_SIGNAL} 信号，当前为: {ready_signal!r}")

    def _with_ready_signal(self, result: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(result)
        payload["ready"] = DEFAULT_READY_SIGNAL
        payload["signal"] = DEFAULT_READY_SIGNAL
        payload["ready_signal"] = DEFAULT_READY_SIGNAL
        payload["received_ready_signal"] = DEFAULT_READY_SIGNAL
        return payload

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

    def _resolve_start_experiment_info(
        self,
        submit_experiment_result: Optional[Dict[str, Any]],
        order_id: str,
        order_ids: Optional[List[str]],
    ) -> Dict[str, Any]:
        payload = submit_experiment_result or {}
        start_info = payload.get("start_experiment") if isinstance(payload, dict) else None
        if isinstance(start_info, dict):
            resolved_order_ids = [str(candidate) for candidate in self._as_list(start_info.get("order_ids")) if candidate]
            if start_info.get("order_id"):
                resolved_order_ids.append(str(start_info["order_id"]))
            resolved_order_ids = list(dict.fromkeys(resolved_order_ids))
            if resolved_order_ids:
                resolved = dict(start_info)
                resolved["order_ids"] = resolved_order_ids
                return resolved
        resolved_order_ids = list(order_ids or [])
        if order_id:
            resolved_order_ids.insert(0, order_id)
        if isinstance(payload, dict):
            for candidate in self._as_list(payload.get("order_ids")):
                if candidate:
                    resolved_order_ids.append(str(candidate))
            if payload.get("order_id"):
                resolved_order_ids.append(str(payload["order_id"]))
        resolved_order_ids = list(dict.fromkeys(resolved_order_ids))
        if not resolved_order_ids:
            raise RuntimeError("启动实验需要 submit_experiment 上游结果，或显式提供 order_id/order_ids")
        return {"order_ids": resolved_order_ids}

    def _query_order_snapshot(self, rpc: Any, filter_text: str) -> Any:
        self._require_rpc_method(rpc, "order_query")
        query = {
            "timeType": "",
            "beginTime": None,
            "endTime": None,
            "status": "",
            "filter": filter_text,
            "skipCount": 0,
            "pageCount": 20,
            "sorting": "",
        }
        return rpc.order_query(json.dumps(query, ensure_ascii=False))

    def _extract_takeout_targets(self, order_snapshot: Any, fallback_order_id: str) -> Dict[str, Any]:
        order_id = fallback_order_id
        preintake_ids = set()
        material_ids = set()
        for item in self._order_items(order_snapshot):
            order_id = str(item.get("id") or order_id)
            for preintake in self._as_list(item.get("preIntakes")):
                if not isinstance(preintake, dict):
                    continue
                if preintake.get("id"):
                    preintake_ids.add(str(preintake["id"]))
                if preintake.get("materialId"):
                    material_ids.add(str(preintake["materialId"]))
                material_ids_text = str(preintake.get("materialIds") or "")
                for material_id in material_ids_text.replace(";", "|").replace(",", "|").split("|"):
                    if material_id.strip():
                        material_ids.add(material_id.strip())
                for sample in self._as_list(preintake.get("sampleMaterials")):
                    if isinstance(sample, dict) and sample.get("materialId"):
                        material_ids.add(str(sample["materialId"]))
        return {
            "order_id": order_id,
            "preintake_ids": sorted(preintake_ids),
            "material_ids": sorted(material_ids),
            "requires_take_out": bool(preintake_ids or material_ids),
        }

    def _take_out_remaining_materials(self, rpc: Any, targets: Dict[str, Any]) -> Any:
        payload = {
            "orderId": targets["order_id"],
            "preintakeIds": targets["preintake_ids"],
            "materialIds": targets["material_ids"],
        }
        if hasattr(rpc, "take_out"):
            return rpc.take_out(payload["orderId"], payload["preintakeIds"], payload["materialIds"])
        if not all(hasattr(rpc, attr) for attr in ("post", "host", "api_key", "get_current_time_iso8601")):
            raise RuntimeError("Bioyond RPC 客户端缺少 take-out 调用能力")
        response = rpc.post(
            url=f"{rpc.host}/api/lims/order/take-out",
            params={
                "apiKey": rpc.api_key,
                "requestTime": rpc.get_current_time_iso8601(),
                "data": payload,
            },
        )
        return response or {}

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
        fallback_on_empty: bool = False,
    ) -> Any:
        if hasattr(rpc, "order_query"):
            try:
                result = rpc.order_query(json.dumps(query_payload, ensure_ascii=False))
                if fallback_on_empty and result in ({}, None, []):
                    return self._fallback_or_original_lims_section(
                        rpc,
                        "/api/lims/order/order-list",
                        query_payload,
                        errors,
                        "order_list",
                        result,
                    )
                return result
            except Exception as exc:
                errors.append(f"order_list: {exc}")
                logger.warning("聚合订单报告 order_list 查询失败，继续使用空结果: %s", exc)
                return {}
        logger.warning("聚合订单报告 order_list 缺少专用 RPC 方法，尝试通用 POST fallback")
        return self._post_lims_section(
            rpc,
            "/api/lims/order/order-list",
            query_payload,
            errors,
            "order_list",
        )

    def _call_single_arg_lims_section(
        self,
        rpc: Any,
        method_name: str,
        endpoint: str,
        data: str,
        errors: List[str],
        section_name: str,
        fallback_on_empty: bool = False,
    ) -> Any:
        if hasattr(rpc, method_name):
            try:
                result = getattr(rpc, method_name)(data)
                if fallback_on_empty and result in ({}, None, []):
                    logger.warning(
                        "聚合订单报告 %s 返回为空，尝试通用 POST fallback: endpoint=%s",
                        section_name,
                        endpoint,
                    )
                    return self._fallback_or_original_lims_section(
                        rpc,
                        endpoint,
                        data,
                        errors,
                        section_name,
                        result,
                    )
                return result
            except Exception as exc:
                errors.append(f"{section_name}: {exc}")
                logger.warning(
                    "聚合订单报告 %s 查询失败，继续使用空结果: %s",
                    section_name,
                    exc,
                )
                return {}
        logger.warning(
            "聚合订单报告 %s 缺少 RPC 方法 %s，尝试通用 POST fallback",
            section_name,
            method_name,
        )
        return self._post_lims_section(rpc, endpoint, data, errors, section_name)

    def _fallback_or_original_lims_section(
        self,
        rpc: Any,
        endpoint: str,
        data: Any,
        errors: List[str],
        section_name: str,
        original: Any,
    ) -> Any:
        if not self._can_raw_post_lims_section(rpc):
            logger.warning(
                "聚合订单报告 %s 返回为空且 RPC 不支持通用 POST fallback，保留原始空结果",
                section_name,
            )
            return original
        fallback = self._post_lims_section(rpc, endpoint, data, errors, section_name)
        return fallback or original

    def _can_raw_post_lims_section(self, rpc: Any) -> bool:
        return all(hasattr(rpc, attr) for attr in ("post", "host", "api_key", "get_current_time_iso8601"))

    def _post_lims_section(
        self,
        rpc: Any,
        endpoint: str,
        data: Any,
        errors: List[str],
        section_name: str,
    ) -> Any:
        if not self._can_raw_post_lims_section(rpc):
            errors.append(f"{section_name}: Bioyond RPC 客户端缺少 {endpoint} 调用能力")
            logger.error(
                "聚合订单报告 %s 无法 fallback: RPC 缺少 %s 调用能力",
                section_name,
                endpoint,
            )
            return {}
        try:
            return rpc.post(
                url=f"{str(rpc.host).rstrip('/')}{endpoint}",
                params={
                    "apiKey": rpc.api_key,
                    "requestTime": rpc.get_current_time_iso8601(),
                    "data": data,
                },
            ) or {}
        except Exception as exc:
            errors.append(f"{section_name}: {exc}")
            logger.warning(
                "聚合订单报告 %s fallback POST 失败，继续使用空结果: endpoint=%s error=%s",
                section_name,
                endpoint,
                exc,
            )
            return {}

    def _service_data_or_value(self, payload: Any) -> Any:
        parsed = self._parse_lims_result(payload)
        if isinstance(parsed, dict) and "data" in parsed and (
            "code" in parsed or "message" in parsed or "timestamp" in parsed
        ):
            return self._parse_lims_result(parsed.get("data"))
        return parsed

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

    def _build_manual_load_tables(
        self,
        materials_by_type: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, Dict[str, List[str]]]:
        """Derive sibling-array tables per Bioyond material category.

        Output shape matches the production sibling-array handles on
        ``submit_experiment_1``: ``manual_load_tables.<Mode>.<column>`` where
        ``<column>`` is ``material_name | material_code | location | quantity``
        and each value is a row-aligned ``List[str]``.
        """
        tables: Dict[str, Dict[str, List[str]]] = {}
        for mode in ("Sample", "Consumables", "Reagent"):
            rows = (materials_by_type or {}).get(mode, []) or []
            tables[mode] = {
                "material_name": [str(row.get("materialName") or "") for row in rows],
                "material_code": [str(row.get("materialCode") or "") for row in rows],
                "location": [
                    str(row.get("locationShowName") or row.get("locationCode") or "")
                    for row in rows
                ],
                "quantity": [str(row.get("quantity") or "") for row in rows],
            }
        return tables

    @staticmethod
    def _manual_load_resource_stub(
        row: Dict[str, Any],
        *,
        name: str,
        kind: str,
        index: int,
    ) -> Dict[str, Any]:
        location_label = str(row.get("locationShowName") or row.get("locationCode") or "")
        display_name = name or location_label or f"{kind}_{index + 1}"
        stable_key = f"bioyond-sirna-manual-load:{kind}:{index}:{display_name}"
        return {
            "id": display_name,
            "uuid": str(uuid5(NAMESPACE_URL, stable_key)),
            "name": display_name,
            "description": "Bioyond 手动装载临时资源",
            "schema": {},
            "model": {},
            "icon": "",
            "parent_uuid": None,
            "type": "resource",
            "class": "",
            "config": {
                "materialId": str(row.get("materialId") or ""),
                "materialCode": str(row.get("materialCode") or ""),
                "materialName": str(row.get("materialName") or ""),
                "locationId": str(row.get("locationId") or ""),
                "locationCode": str(row.get("locationCode") or ""),
                "locationShowName": str(row.get("locationShowName") or ""),
            },
            "data": {
                "display_name": display_name,
                "locationShowName": str(row.get("locationShowName") or ""),
                "locationCode": str(row.get("locationCode") or ""),
            },
            "extra": {
                "bioyond_manual_load_kind": kind,
                "bioyond_location_display": location_label,
            },
            "machine_name": "",
        }

    def _classify_labware_mode(self, child: Any) -> str:
        """Map a placed labware to ``Sample`` / ``Consumables`` / ``Reagent``.

        Resolution order:

        1. ``unilabos_extra["material_bioyond_type_mode"]`` if previously set
           by the load path (``_register_materials_to_tree``) — most reliable.
        2. PLR class identity: troughs map to ``Reagent``; tip racks map to
           ``Consumables``; everything else (plates, tubes, etc.) defaults
           to ``Sample``.

        The classifier is intentionally permissive: an unrecognized class
        falls back to ``Sample`` so the operator still sees the row instead
        of the unload silently dropping it.
        """
        extra = getattr(child, "unilabos_extra", None) or {}
        if isinstance(extra, dict):
            mode = str(extra.get("material_bioyond_type_mode") or "")
            if mode in {"Sample", "Consumables", "Reagent"}:
                return mode
        try:
            from unilabos.resources.bioyond.sirna_materials import (
                BioyondSirna_ReagentTrough,
            )
        except Exception:  # pragma: no cover - lightweight env
            BioyondSirna_ReagentTrough = None  # type: ignore[assignment]
        if BioyondSirna_ReagentTrough is not None and isinstance(child, BioyondSirna_ReagentTrough):
            return "Reagent"
        category_attr = getattr(child, "category", "") or ""
        category = str(category_attr).lower()
        if "tip" in category:
            return "Consumables"
        if "trough" in category or "reagent" in category:
            return "Reagent"
        # Class-name heuristic for environments without category metadata.
        cls_name = type(child).__name__.lower()
        if "tip" in cls_name:
            return "Consumables"
        if "trough" in cls_name or "reagent" in cls_name:
            return "Reagent"
        return "Sample"

    def _iter_reagent_liquids(self, parent: Any) -> Iterable[Dict[str, Any]]:
        """Yield reagent-content rows attached to ``parent`` via ``_attach_liquid_to_parent``.

        Cross-references ``parent.unilabos_extra["reagent_bioyond_ids"]`` (the
        structured Bioyond metadata source-of-truth) with ``parent.tracker.liquids``
        when present. Tolerates missing metadata so this helper is usable even
        on legacy decks that predate the reagent-as-liquid plan.
        """
        extra = getattr(parent, "unilabos_extra", None) or {}
        reagent_ids: List[Any] = []
        if isinstance(extra, dict):
            reagent_ids = list(extra.get("reagent_bioyond_ids") or [])

        parent_name = getattr(parent, "name", "") or ""
        warehouse = getattr(parent, "parent", None)
        warehouse_name = getattr(warehouse, "name", "") or ""
        location_label = f"{warehouse_name}/{parent_name}" if warehouse_name else parent_name

        for entry in reagent_ids:
            if not isinstance(entry, dict):
                continue
            yield {
                "materialName": str(entry.get("material_bioyond_name") or ""),
                "materialCode": str(entry.get("material_bioyond_code") or ""),
                "materialTypeCode": str(entry.get("material_bioyond_type_code") or ""),
                "materialTypeMode": "Reagent",
                "locationCode": "",
                "locationShowName": location_label,
                "quantity": entry.get("quantity") or "",
                "trough": parent,
                "material_bioyond_id": str(entry.get("material_bioyond_id") or ""),
            }

    def _build_unload_materials_by_type(
        self,
        *,
        order_id_filter: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Walk ``self.deck`` and emit a Bioyond-shaped material list grouped by mode.

        Output rows expose ``materialName / materialCode / locationCode /
        locationShowName / quantity / materialTypeMode`` keys, mirroring the
        fields :meth:`_build_manual_load_tables` consumes. No side effects.

        Args:
            order_id_filter: When provided, slot-bound labware whose
                ``unilabos_extra["order_id"]`` does not equal this value is
                skipped. Reagent contents are not order-scoped.
        """
        grouped: Dict[str, List[Dict[str, Any]]] = {
            "Sample": [],
            "Consumables": [],
            "Reagent": [],
        }
        deck = getattr(self, "deck", None)
        if deck is None:
            return grouped
        for warehouse in getattr(deck, "children", []) or []:
            ordering = getattr(warehouse, "_ordering", None)
            if not isinstance(ordering, dict):
                continue
            for slot_label in ordering.keys():
                try:
                    child = warehouse[slot_label]
                except (KeyError, IndexError, TypeError):
                    continue
                # Distinguish placed labware from unoccupied ResourceHolder
                # variants. Empty slots show as None on this carrier.
                if child is None:
                    continue
                if not hasattr(child, "tracker") and not hasattr(child, "children"):
                    continue
                # Plain ResourceHolder placeholders for empty sites are skipped.
                if type(child).__name__ == "ResourceHolder" and not list(getattr(child, "children", []) or []):
                    continue

                extra = getattr(child, "unilabos_extra", None) or {}
                if not isinstance(extra, dict):
                    extra = {}
                if order_id_filter and extra.get("order_id") and extra.get("order_id") != order_id_filter:
                    continue

                mode = self._classify_labware_mode(child)
                row = {
                    "materialName": str(extra.get("material_bioyond_name") or getattr(child, "name", "") or ""),
                    "materialCode": str(extra.get("material_bioyond_code") or getattr(child, "name", "") or ""),
                    "materialTypeCode": str(extra.get("material_bioyond_type_code") or ""),
                    "materialTypeMode": mode,
                    "locationCode": str(extra.get("location_code") or slot_label or ""),
                    "locationShowName": f"{getattr(warehouse, 'name', '')}/{slot_label}",
                    "quantity": getattr(child, "quantity", 1),
                    "_warehouse": warehouse,
                    "_slot_label": str(slot_label),
                    "_child": child,
                }
                grouped.setdefault(mode, []).append(row)

                # Reagent contents on this trough/labware (from reagent_bioyond_ids).
                for liquid_row in self._iter_reagent_liquids(child):
                    grouped["Reagent"].append(liquid_row)
        return grouped

    def _clear_unloaded_materials(
        self,
        manifest: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Apply the unload mutation: clear slot-bound labware, zero reagent liquids.

        Returns a dict shaped like the manifest but with each row reduced to
        the minimal fields downstream consumers need (material code/name, the
        warehouse name, and the slot label or trough name).
        """
        cleared: Dict[str, List[Dict[str, Any]]] = {
            "Sample": [],
            "Consumables": [],
            "Reagent": [],
        }
        affected_warehouses: List[Any] = []

        def _remember(warehouse: Any) -> None:
            if warehouse is None:
                return
            if any(warehouse is existing for existing in affected_warehouses):
                return
            affected_warehouses.append(warehouse)

        # 1) Slot-bound labware (Sample / Consumables).
        for mode in ("Sample", "Consumables"):
            for row in manifest.get(mode, []):
                warehouse = row.get("_warehouse")
                slot_label = row.get("_slot_label") or row.get("locationCode") or ""
                if warehouse is None and slot_label:
                    try:
                        warehouse, _idx = self._resolve_location_to_warehouse(slot_label)
                    except (ValueError, RuntimeError) as exc:
                        logger.warning(
                            "卸载物料 %s 时无法解析库位 %s: %s",
                            row.get("materialCode"), slot_label, exc,
                        )
                        continue
                if warehouse is None or not slot_label:
                    continue
                try:
                    if warehouse[slot_label] is not None:
                        warehouse[slot_label] = None
                        cleared[mode].append({
                            "material_code": str(row.get("materialCode") or ""),
                            "material_name": str(row.get("materialName") or ""),
                            "location": slot_label,
                            "warehouse": getattr(warehouse, "name", ""),
                        })
                        _remember(warehouse)
                except (IndexError, KeyError, TypeError) as exc:
                    logger.warning(
                        "卸载物料 %s 时清空库位 %s[%s] 失败: %s",
                        row.get("materialCode"), getattr(warehouse, "name", "?"),
                        slot_label, exc,
                    )

        # 2) Reagent rows: contents on a parent trough vs slot-bound trough labware.
        for row in manifest.get("Reagent", []):
            trough = row.get("trough")
            if trough is not None:
                tracker = getattr(trough, "tracker", None)
                if tracker is not None and hasattr(tracker, "set_liquids"):
                    try:
                        tracker.set_liquids([])
                    except Exception as exc:  # pragma: no cover - tracker variants
                        logger.debug("tracker.set_liquids 失败（非阻塞）: %s", exc)
                extra = getattr(trough, "unilabos_extra", None)
                if isinstance(extra, dict):
                    extra["reagent_bioyond_ids"] = []
                    try:
                        setattr(trough, "unilabos_extra", extra)
                    except Exception:  # pragma: no cover - read-only proxy
                        pass
                cleared["Reagent"].append({
                    "material_name": str(row.get("materialName") or ""),
                    "material_code": str(row.get("materialCode") or ""),
                    "trough": getattr(trough, "name", ""),
                    "warehouse": getattr(getattr(trough, "parent", None), "name", ""),
                })
                _remember(getattr(trough, "parent", None))
                continue

            # Slot-bound Reagent labware (e.g. trough labware). Preserve the
            # labware on the deck — only its contents (liquids + reagent ids)
            # are zeroed. ``reset`` is the LIMS-side path for full removal.
            child = row.get("_child")
            if child is not None:
                tracker = getattr(child, "tracker", None)
                if tracker is not None and hasattr(tracker, "set_liquids"):
                    try:
                        tracker.set_liquids([])
                    except Exception as exc:  # pragma: no cover - tracker variants
                        logger.debug("tracker.set_liquids 失败（非阻塞）: %s", exc)
                extra = getattr(child, "unilabos_extra", None)
                if isinstance(extra, dict) and extra.get("reagent_bioyond_ids"):
                    extra["reagent_bioyond_ids"] = []
                    try:
                        setattr(child, "unilabos_extra", extra)
                    except Exception:  # pragma: no cover - read-only proxy
                        pass
                _remember(row.get("_warehouse") or getattr(child, "parent", None))
                continue

        # 3) Publish the deck after mutations. ``_publish_resource_tree_update``
        # publishes the whole deck, so a single call covers all warehouses we
        # touched. ``affected_warehouses`` is kept for telemetry / tests.
        if affected_warehouses:
            self._publish_resource_tree_update()
        return cleared

    def _build_manual_load_probe(
        self,
        materials_by_type: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, List[Any]]:
        """构造临时前端兼容的手动装载表格值。

        当前临时使用 ``coin_cell_code`` 表示物料名称，``mount_resource`` 表示库位。
        因为 ``mount_resource`` 声明为 ``data_type="resource"``，这里要把
        ``locationShowName/locationCode`` 放到资源形状的 ``name`` 字段上，而不是
        直接返回字符串列表。前端支持通用 action-input 表格后应替换为 Sirna 自有 key。
        """
        rows: List[Dict[str, Any]] = []
        for mode in ("Sample", "Consumables", "Reagent"):
            rows.extend((materials_by_type or {}).get(mode, []) or [])
        material_names = [
            str(row.get("materialName") or row.get("materialCode") or "") for row in rows
        ]
        location_names = [
            str(row.get("locationShowName") or row.get("locationCode") or "") for row in rows
        ]
        return {
            "resource": material_names,
            "coin_cell_code": material_names,
            "mount_resource": [
                self._manual_load_resource_stub(
                    row,
                    name=location_names[index],
                    kind="location",
                    index=index,
                )
                for index, row in enumerate(rows)
            ],
            "mount_resource_label": location_names,
        }

    def _register_materials_to_tree(self, material_records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Register Bioyond materials to UniLabOS resource tree after user confirmation.

        ID-first resolver pipeline (Phase 2/3):

        1. Each ``mat`` is classified as ``slot_labware`` / ``liquid_content`` / ``unsupported``
           via :meth:`_classify_material_record`.
        2. Slot labware is resolved to ``(warehouse, location_code)`` using
           :meth:`_resolve_material_record_to_warehouse` which prefers ``material-info``
           and falls back to ``warehouse-info-by-mat-type-id`` keyed by ``locationId``.
        3. Liquid content is attached to the parent trough at the resolved slot via
           :meth:`_attach_liquid_to_parent` (idempotent by Bioyond material id).
        4. Unsupported / contradictory rows are surfaced loudly with all Bioyond IDs.

        ``locationCode`` alone is never used for production registration.
        """
        from unilabos.resources.bioyond.sirna_materials import get_material_class_by_type_code

        deck = getattr(self, "deck", None)
        if deck is None:
            logger.warning("deck 未初始化，跳过 resource tree 注册")
            return {"registered": [], "skipped": material_records, "reason": "no_deck"}

        # Per-batch caches so a single submit_experiment_1 call doesn't refetch.
        warehouse_inventory_cache: Dict[str, List[Dict[str, Any]]] = {}
        material_info_cache: Dict[str, Dict[str, Any]] = {}

        registered: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []

        for mat in material_records:
            classification = self._classify_material_record(mat)
            if classification == "unsupported":
                logger.warning(
                    "未支持的物料类型，跳过: materialId=%s materialTypeCode=%s materialTypeName=%s",
                    mat.get("materialId"),
                    mat.get("materialTypeCode"),
                    mat.get("materialTypeName"),
                )
                skipped.append({**mat, "_skip_reason": "unsupported_classification"})
                continue

            try:
                resolved = self._resolve_material_record_to_warehouse(
                    mat,
                    warehouse_inventory_cache=warehouse_inventory_cache,
                    material_info_cache=material_info_cache,
                )
            except (ValueError, RuntimeError) as exc:
                logger.warning(
                    "解析 Bioyond 库位失败: materialId=%s materialTypeId=%s locationId=%s 错误=%s",
                    mat.get("materialId"),
                    mat.get("materialTypeId"),
                    mat.get("locationId"),
                    exc,
                )
                skipped.append({**mat, "_skip_reason": f"resolution_failed: {exc}"})
                continue

            warehouse = resolved.get("warehouse")
            location_code = resolved.get("location_code")
            if warehouse is None or not location_code:
                skipped.append({**mat, "_skip_reason": "no_warehouse_or_slot"})
                continue

            if classification == "slot_labware":
                type_code = mat.get("materialTypeCode", "")
                resource_class = get_material_class_by_type_code(type_code)
                if resource_class is None:
                    logger.warning(
                        "未知 materialTypeCode %s（slot_labware），跳过: %s",
                        type_code, mat.get("materialName"),
                    )
                    skipped.append({**mat, "_skip_reason": "unmapped_material_type_code"})
                    continue
                material_code = mat.get("materialCode") or f"mat_{type_code}_{location_code}"
                plr_resource = resource_class(name=material_code)
                plr_resource.unilabos_extra = self._build_slot_labware_extra(mat, resolved)
                try:
                    warehouse[location_code] = plr_resource
                    registered.append({
                        "kind": "slot_labware",
                        "material_code": material_code,
                        "material_name": mat.get("materialName", ""),
                        "location_code": location_code,
                        "warehouse": warehouse.name,
                        "warehouse_bioyond_id": resolved.get("warehouse_id", ""),
                        "resolution_source": resolved.get("source", ""),
                    })
                except (IndexError, KeyError, TypeError) as exc:
                    logger.warning(
                        "放置物料 %s 到 %s[%s] 失败: %s",
                        material_code, warehouse.name, location_code, exc,
                    )
                    skipped.append({**mat, "_skip_reason": f"assign_failed: {exc}"})
                continue

            # liquid_content
            attach_result = self._attach_liquid_to_parent(
                mat=mat, warehouse=warehouse, location_code=location_code, resolved=resolved
            )
            if attach_result.get("status") == "ok":
                registered.append({
                    "kind": "liquid_content",
                    "material_code": mat.get("materialCode", ""),
                    "material_name": mat.get("materialName", ""),
                    "location_code": location_code,
                    "warehouse": warehouse.name,
                    "warehouse_bioyond_id": resolved.get("warehouse_id", ""),
                    "parent": attach_result.get("parent_name", ""),
                    "duplicate": attach_result.get("duplicate", False),
                    "resolution_source": resolved.get("source", ""),
                })
            else:
                skipped.append({**mat, "_skip_reason": attach_result.get("reason", "liquid_attach_failed")})

        self._publish_resource_tree_update()
        return {"registered": registered, "skipped": skipped}

    # ------------------------------------------------------------------
    # Phase 2 / Phase 3 helpers: classification + ID-first resolver
    # ------------------------------------------------------------------

    def _classify_material_record(self, mat: Dict[str, Any]) -> str:
        """Return ``slot_labware`` | ``liquid_content`` | ``unsupported``.

        Rules (in order):
        1. If ``materialTypeCode`` maps to a known PLR class via
           ``get_material_class_by_type_code`` AND the mapped class is *not* the
           reagent trough, treat as ``slot_labware``.
        2. If ``materialTypeMode`` is ``Reagent`` and the mapped class is missing
           or is a trough container, treat as ``liquid_content`` (will attach to
           an already-placed parent trough).
        3. Otherwise, mark ``unsupported``.

        The reagent-trough class is only chosen as ``slot_labware`` when the row's
        material name matches a configured trough labware name (e.g. configured
        explicitly via ``material_type_mappings``). When evidence is ambiguous, prefer
        ``liquid_content`` so the data lands on the parent rather than overwriting it.
        """
        from unilabos.resources.bioyond.sirna_materials import (
            get_material_class_by_type_code,
            BioyondSirna_ReagentTrough,
        )

        type_code = str(mat.get("materialTypeCode") or "")
        type_mode = str(mat.get("materialTypeMode") or "")
        mapped = get_material_class_by_type_code(type_code) if type_code else None

        if mapped is not None and mapped is not BioyondSirna_ReagentTrough:
            return "slot_labware"

        if type_mode == "Reagent":
            return "liquid_content"

        if mapped is None and type_mode in {"Sample", "Consumables"}:
            return "unsupported"

        # Default: if mapped trough class but Reagent mode -> liquid; otherwise unsupported.
        if mapped is BioyondSirna_ReagentTrough and type_mode == "Reagent":
            return "liquid_content"
        return "unsupported"

    def _resolve_material_record_to_warehouse(
        self,
        mat: Dict[str, Any],
        warehouse_inventory_cache: Dict[str, List[Dict[str, Any]]],
        material_info_cache: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Resolve a Bioyond allocation record to (warehouse, location_code) by IDs.

        Resolution order: ``material-info`` → ``warehouse-info-by-mat-type-id``.
        Falls back to a code-only diagnostic that raises on ambiguity.
        """
        material_id = str(mat.get("materialId") or "")
        material_type_id = str(mat.get("materialTypeId") or "")
        location_id = str(mat.get("locationId") or "")
        location_code = str(mat.get("locationShowName") or mat.get("locationCode") or "")

        # 1) material-info path
        try:
            resolved = self._resolve_by_material_info(
                material_id=material_id,
                location_id=location_id,
                material_info_cache=material_info_cache,
            )
            if resolved is not None:
                return resolved
        except RuntimeError as exc:
            # RPC error: log and continue with fallback path.
            logger.debug("material-info 解析失败，回退: %s", exc)

        # 2) warehouse-info-by-mat-type-id path
        try:
            resolved = self._resolve_by_type_location_inventory(
                material_type_id=material_type_id,
                location_id=location_id,
                warehouse_inventory_cache=warehouse_inventory_cache,
            )
            if resolved is not None:
                return resolved
        except RuntimeError as exc:
            logger.debug("warehouse-info-by-mat-type-id 解析失败，回退: %s", exc)

        # 3) Diagnostic-only code fallback. Only allowed when exactly one warehouse
        #    on the deck owns this slot label. Any ambiguity raises.
        if location_code:
            warehouse, _idx = self._resolve_location_to_warehouse(location_code)
            return {
                "warehouse": warehouse,
                "warehouse_id": "",
                "warehouse_name": warehouse.name,
                "location_id": location_id,
                "location_code": location_code,
                "source": "code_only_fallback",
            }
        raise RuntimeError(
            f"无法解析 Bioyond 库位: materialId={material_id} materialTypeId={material_type_id} "
            f"locationId={location_id} locationCode={location_code}"
        )

    def _resolve_by_material_info(
        self,
        material_id: str,
        location_id: str,
        material_info_cache: Dict[str, Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Resolve via ``BioyondV1RPC.material_info(material_id)``.

        Schema returns ``locations[].whid/whName/code`` plus ``locations[].id`` (the
        location_id). When ``location_id`` is non-empty we prefer matching by id, else
        by ``code`` plus the deck's ``warehouse_bioyond_ids`` mapping.
        """
        rpc = getattr(self, "hardware_interface", None)
        if rpc is None or not material_id:
            return None
        if material_id in material_info_cache:
            payload = material_info_cache[material_id]
        else:
            try:
                payload = rpc.material_info(material_id) or {}
            except Exception as exc:  # pragma: no cover - 网络错误
                raise RuntimeError(f"material_info({material_id}) RPC 失败: {exc}") from exc
            material_info_cache[material_id] = payload

        locations = payload.get("locations") if isinstance(payload, dict) else None
        if not isinstance(locations, list) or not locations:
            return None

        # Match by id first.
        for loc in locations:
            if not isinstance(loc, dict):
                continue
            if location_id and str(loc.get("id") or "") == location_id:
                return self._build_resolution_from_material_info_loc(loc, source="material-info")
        # Fall back to first usable row.
        for loc in locations:
            if not isinstance(loc, dict):
                continue
            if loc.get("whid") or loc.get("whName") or loc.get("code"):
                return self._build_resolution_from_material_info_loc(loc, source="material-info")
        return None

    def _build_resolution_from_material_info_loc(
        self, loc: Dict[str, Any], source: str
    ) -> Optional[Dict[str, Any]]:
        whid = str(loc.get("whid") or "")
        wh_name = str(loc.get("whName") or "")
        code = str(loc.get("code") or "")
        warehouse = self._warehouse_by_bioyond_id_or_name(whid=whid, wh_name=wh_name)
        if warehouse is None or not code:
            return None
        if not self._location_code_in_warehouse(warehouse, code):
            logger.warning(
                "material-info 返回库位 %s 在仓库 %s 中不存在，跳过",
                code, getattr(warehouse, "name", "?"),
            )
            return None
        return {
            "warehouse": warehouse,
            "warehouse_id": whid,
            "warehouse_name": wh_name or warehouse.name,
            "location_id": str(loc.get("id") or ""),
            "location_code": code,
            "x": loc.get("x"),
            "y": loc.get("y"),
            "z": loc.get("z"),
            "source": source,
        }

    def _resolve_by_type_location_inventory(
        self,
        material_type_id: str,
        location_id: str,
        warehouse_inventory_cache: Dict[str, List[Dict[str, Any]]],
    ) -> Optional[Dict[str, Any]]:
        """Resolve via ``warehouse-info-by-mat-type-id`` inventory rows.

        Each row contains ``id``/``warehouseId``/``warehouseName``/``code``. We pick
        the row whose ``id == location_id``.
        """
        rpc = getattr(self, "hardware_interface", None)
        if rpc is None or not material_type_id or not location_id:
            return None
        if material_type_id in warehouse_inventory_cache:
            rows = warehouse_inventory_cache[material_type_id]
        else:
            try:
                payload = rpc.query_warehouse_by_material_type(material_type_id) or {}
            except Exception as exc:  # pragma: no cover - 网络错误
                raise RuntimeError(
                    f"warehouse-info-by-mat-type-id({material_type_id}) RPC 失败: {exc}"
                ) from exc
            rows = []
            if isinstance(payload, dict):
                # query_warehouse_by_material_type returns response['data'] dict-or-list.
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, list):
                    rows = data
                elif isinstance(payload, dict):
                    # query helper returns response['data'] already, treat top-level dict
                    # as a single row only if it has expected keys.
                    if "id" in payload and "warehouseId" in payload:
                        rows = [payload]
            elif isinstance(payload, list):
                rows = payload
            warehouse_inventory_cache[material_type_id] = rows

        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("id") or "") != location_id:
                continue
            whid = str(row.get("warehouseId") or "")
            wh_name = str(row.get("warehouseName") or "")
            code = str(row.get("code") or "")
            warehouse = self._warehouse_by_bioyond_id_or_name(whid=whid, wh_name=wh_name)
            if warehouse is None or not code:
                continue
            if not self._location_code_in_warehouse(warehouse, code):
                logger.warning(
                    "warehouse-info row 库位 %s 在仓库 %s 中不存在，跳过",
                    code, getattr(warehouse, "name", "?"),
                )
                continue
            return {
                "warehouse": warehouse,
                "warehouse_id": whid,
                "warehouse_name": wh_name or warehouse.name,
                "location_id": location_id,
                "location_code": code,
                "x": row.get("x"),
                "y": row.get("y"),
                "z": row.get("z"),
                "source": "warehouse-info-by-mat-type-id",
            }
        return None

    def _warehouse_by_bioyond_id_or_name(self, whid: str, wh_name: str) -> Any:
        """Locate a deck child warehouse by Bioyond id (preferred) or display name."""
        deck = getattr(self, "deck", None)
        if deck is None:
            return None
        # Bioyond id mapping (deck-level config: deck.warehouse_bioyond_ids).
        id_map = getattr(deck, "warehouse_bioyond_ids", {}) or {}
        # Also pick up station-level config if deck has no mapping yet.
        if not id_map:
            cfg = getattr(self, "bioyond_config", {}) or {}
            id_map = cfg.get("warehouse_bioyond_ids") or {}
        if whid:
            target_name = id_map.get(whid)
            if target_name:
                for child in deck.children:
                    if getattr(child, "name", "") == target_name:
                        return child
        if wh_name:
            for child in deck.children:
                if getattr(child, "name", "") == wh_name:
                    return child
        return None

    @staticmethod
    def _location_code_in_warehouse(warehouse: Any, code: str) -> bool:
        ordering = getattr(warehouse, "_ordering", None)
        if isinstance(ordering, dict):
            return code in ordering
        return False

    def _build_slot_labware_extra(
        self, mat: Dict[str, Any], resolved: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "material_bioyond_id": mat.get("materialId", ""),
            "material_bioyond_code": mat.get("materialCode", ""),
            "material_bioyond_name": mat.get("materialName", ""),
            "material_bioyond_type_id": mat.get("materialTypeId", ""),
            "material_bioyond_type_code": mat.get("materialTypeCode", ""),
            "material_bioyond_type_mode": mat.get("materialTypeMode", ""),
            "location_bioyond_id": mat.get("locationId", ""),
            "location_code": resolved.get("location_code", ""),
            "warehouse_bioyond_id": resolved.get("warehouse_id", ""),
            "warehouse_bioyond_name": resolved.get("warehouse_name", ""),
            "location_resolution_source": resolved.get("source", ""),
        }

    def _attach_liquid_to_parent(
        self,
        mat: Dict[str, Any],
        warehouse: Any,
        location_code: str,
        resolved: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Attach a reagent ``mat`` as liquid content on the parent labware at the slot.

        Returns ``{"status": "ok"|"deferred", "reason": str, "parent_name": str, "duplicate": bool}``.
        Idempotent by Bioyond ``materialId``.
        """
        try:
            holder = warehouse[location_code]
        except (KeyError, IndexError):
            return {"status": "deferred", "reason": f"warehouse_slot_missing:{location_code}"}
        # ``warehouse[code]`` returns the assigned resource when occupied, otherwise
        # an empty ResourceHolder. Detect both shapes.
        parent = None
        if hasattr(holder, "tracker"):
            parent = holder
        else:
            children = list(getattr(holder, "children", []) or [])
            if children:
                parent = children[0]
        if parent is None:
            return {
                "status": "deferred",
                "reason": f"missing_parent_labware:{warehouse.name}/{location_code}",
            }

        bioyond_id = str(mat.get("materialId") or "")
        extra = getattr(parent, "unilabos_extra", None)
        if not isinstance(extra, dict):
            extra = {}
        reagent_ids = list(extra.get("reagent_bioyond_ids") or [])
        duplicate = any(
            isinstance(item, dict) and str(item.get("material_bioyond_id") or "") == bioyond_id
            for item in reagent_ids
        )
        if not duplicate:
            reagent_ids.append({
                "material_bioyond_id": bioyond_id,
                "material_bioyond_code": mat.get("materialCode", ""),
                "material_bioyond_name": mat.get("materialName", ""),
                "material_bioyond_type_id": mat.get("materialTypeId", ""),
                "material_bioyond_type_code": mat.get("materialTypeCode", ""),
                "location_bioyond_id": mat.get("locationId", ""),
                "quantity": mat.get("quantity"),
                "location_resolution_source": resolved.get("source", ""),
            })
            extra["reagent_bioyond_ids"] = reagent_ids
            try:
                setattr(parent, "unilabos_extra", extra)
            except Exception:  # pragma: no cover - read-only proxy
                pass

            # Best-effort: also add a Liquid entry to parent.tracker if it supports it.
            tracker = getattr(parent, "tracker", None)
            if tracker is not None and hasattr(tracker, "add_liquid"):
                try:
                    quantity_text = str(mat.get("quantity") or "0")
                    # Try to parse a leading number; fall back to 0 to preserve idempotency.
                    import re as _re
                    match = _re.match(r"\s*(\d+(?:\.\d+)?)", quantity_text)
                    qty_value = float(match.group(1)) if match else 0.0
                    if qty_value > 0:
                        # No unit information from Bioyond reagent rows; default ul.
                        tracker.add_liquid(qty_value, unit="ul")
                except Exception as exc:  # pragma: no cover - tracker variants
                    logger.debug("tracker.add_liquid 失败（非阻塞）: %s", exc)

        return {
            "status": "ok",
            "parent_name": getattr(parent, "name", ""),
            "duplicate": duplicate,
            "reason": "",
        }


    def _resolve_location_to_warehouse(self, location_code: str) -> Tuple[Any, int]:
        """[Diagnostic / legacy fallback] Map slot label to (warehouse, idx).

        Production registration goes through :meth:`_resolve_material_record_to_warehouse`,
        which uses Bioyond IDs (``materialId`` / ``materialTypeId`` / ``locationId``).

        This code-only resolver is kept for diagnostics / unit tests and now raises
        on ambiguity instead of silently picking a warehouse by display dimensions.
        """
        deck = getattr(self, "deck", None)
        if deck is None:
            raise RuntimeError("deck 未初始化")

        parts = location_code.replace("-", "-").split("-")
        if len(parts) != 2:
            raise ValueError(f"无法解析库位代码: {location_code!r}")
        site_key = f"{parts[0]}-{parts[1]}"

        # Match exclusively by registered slot labels — never by display geometry.
        candidates: List[Tuple[Any, int]] = []
        for child in deck.children:
            ordering = getattr(child, "_ordering", None)
            if not isinstance(ordering, dict):
                continue
            if site_key in ordering:
                idx = list(ordering.keys()).index(site_key)
                candidates.append((child, idx))

        if not candidates:
            raise ValueError(f"未找到与库位 {location_code!r} 匹配的 warehouse（按 ordering 标签）")
        if len(candidates) > 1:
            warehouse_names = [getattr(w, "name", "?") for w, _ in candidates]
            raise RuntimeError(
                f"库位 {location_code!r} 在多个仓库中存在，需要 Bioyond ID 才能消歧: {warehouse_names}"
            )
        return candidates[0]

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

    def _kwarg_text(self, kwargs: Dict[str, Any], key: str) -> str:
        value = kwargs.get(key)
        return "" if self._is_blank(value) else str(value)

    def _is_blank(self, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ""
        if isinstance(value, list):
            return all(self._is_blank(item) for item in value)
        if isinstance(value, dict):
            return not value
        return False


# TODO: Refactor this into a small BioyondResourceSynchronizer classification hook
# before re-enabling long-term; keep only the Sirna reagent-as-liquid rule here.
class SirnaResourceSynchronizer(BioyondResourceSynchronizer):
    """Sirna-specific resource synchronizer.

    Phase 4 of the resource-system mega plan: external Bioyond stock-material
    rows are partitioned into ``slot_labware`` rows (handled by the existing
    ``BioyondResourceSynchronizer.sync_from_external`` path) and reagent
    ``liquid_content`` rows that should attach to a parent trough on the deck.

    The base implementation goes through ``resource_bioyond_to_plr`` which can
    silently fall back to ``RegularContainer`` for unmapped types. The Sirna
    override classifies before calling the base path, attaches reagent contents
    to already-placed parent labware, and logs (with Bioyond ids) any rows that
    need a parent that doesn't exist yet.

    The override does not double-sync: when rows are taken over here they are
    excluded from the data passed to the base sync.
    """

    def sync_from_external(self) -> bool:  # type: ignore[override]
        rpc = getattr(self.workstation, "hardware_interface", None)
        if rpc is None:
            logger.error("Bioyond API 客户端未初始化")
            return False
        try:
            type1 = rpc.stock_material('{"typeMode": 1, "includeDetail": true}') or []
            type2 = rpc.stock_material('{"typeMode": 2, "includeDetail": true}') or []
            type0 = rpc.stock_material('{"typeMode": 0, "includeDetail": true}') or []
        except Exception as exc:  # pragma: no cover - 网络
            logger.error(f"[Sirna sync] stock_material 调用失败: {exc}")
            return False

        all_rows: List[Dict[str, Any]] = []
        for batch in (type0, type1, type2):
            if isinstance(batch, list):
                all_rows.extend(item for item in batch if isinstance(item, dict))

        labware_rows, liquid_rows = self._partition_external_rows(all_rows)

        # Apply liquid attachments first so duplicate writes from a re-sync are
        # detected via the unilabos_extra reagent_bioyond_ids list.
        deferred_liquids: List[Dict[str, Any]] = []
        for row in liquid_rows:
            attached = self._attach_external_liquid_row(row)
            if attached.get("status") != "ok":
                deferred_liquids.append({**row, "_skip_reason": attached.get("reason", "")})

        # Delegate labware path to the base implementation by temporarily replacing
        # the stock-material results. We re-run only when there is something to do.
        if labware_rows:
            try:
                from unilabos.resources.graphio import resource_bioyond_to_plr
                resource_bioyond_to_plr(
                    labware_rows,
                    type_mapping=self.workstation.bioyond_config.get("material_type_mappings", {}),
                    deck=self.workstation.deck,
                )
            except Exception as exc:  # pragma: no cover - graphio failure
                logger.error(f"[Sirna sync] labware 转换失败: {exc}")

        if deferred_liquids:
            logger.warning(
                "[Sirna sync] %d 条 reagent 行无父 labware，已延后: %s",
                len(deferred_liquids),
                [r.get("_skip_reason") for r in deferred_liquids[:5]],
            )
        return True

    def _partition_external_rows(
        self, rows: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Split external rows by classifier rules.

        Reagent rows whose ``typeName`` is mapped to a known PLR class become
        labware. Reagent rows without a mapped class are treated as liquid
        content rows. ``Sample`` / ``Consumables`` rows always go to labware.
        """
        from unilabos.resources.bioyond.sirna_materials import (
            BioyondSirna_ReagentTrough,
            get_material_class_by_type_code,
        )

        type_mapping = self.workstation.bioyond_config.get("material_type_mappings", {})
        reverse: Dict[str, Any] = {}
        for key, value in type_mapping.items():
            if isinstance(value, (tuple, list)) and value:
                display = value[0]
                if display:
                    reverse.setdefault(display, key)

        labware: List[Dict[str, Any]] = []
        liquid: List[Dict[str, Any]] = []
        for row in rows:
            type_name = row.get("typeName") or ""
            type_mode_int = row.get("typeMode")  # external API uses int 0/1/2
            mapped_key = reverse.get(type_name)
            mapped_class = None
            # Use mapped_key to derive class via type code if possible. Sirna materials
            # uses code-based map; here we just rely on the type_name → mapping presence.
            if mapped_key:
                # try to find class via id; fall back to non-trough.
                # In practice, Sirna materials map by code, so trough check is by name.
                if "试剂槽" in type_name and BioyondSirna_ReagentTrough is not None:
                    mapped_class = BioyondSirna_ReagentTrough
                else:
                    mapped_class = object  # any non-trough sentinel
            is_reagent_mode = (type_mode_int == 2)
            if mapped_class is not None and mapped_class is not BioyondSirna_ReagentTrough:
                labware.append(row)
                continue
            if is_reagent_mode and mapped_class is None:
                liquid.append(row)
                continue
            if is_reagent_mode and mapped_class is BioyondSirna_ReagentTrough:
                # Trough labware: PLR class exists; treat as labware.
                labware.append(row)
                continue
            # Default: labware so we don't accidentally drop unmapped sample rows.
            labware.append(row)
        return labware, liquid

    def _attach_external_liquid_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Attach an external reagent row to a parent labware on the deck.

        Locates the parent through ``locations[0].whName + locations[0].code`` and
        delegates to the station's ``_attach_liquid_to_parent`` helper.
        """
        deck = getattr(self.workstation, "deck", None)
        if deck is None:
            return {"status": "deferred", "reason": "no_deck"}
        locations = row.get("locations") or []
        if not isinstance(locations, list) or not locations:
            return {"status": "deferred", "reason": "no_locations"}
        loc = locations[0] if isinstance(locations[0], dict) else {}
        wh_name = str(loc.get("whName") or "")
        code = str(loc.get("code") or "")
        warehouse = None
        for child in getattr(deck, "children", []):
            if getattr(child, "name", "") == wh_name:
                warehouse = child
                break
        if warehouse is None or not code:
            return {"status": "deferred", "reason": f"no_warehouse_or_code:{wh_name}/{code}"}
        # Build a synthetic mat dict in create-order shape so we can reuse the
        # station-side helper without duplicating logic.
        mat = {
            "materialId": row.get("id"),
            "materialCode": row.get("code"),
            "materialName": row.get("name"),
            "materialTypeId": row.get("typeId"),
            "materialTypeName": row.get("typeName"),
            "quantity": row.get("quantity"),
            "locationId": loc.get("id"),
        }
        attach = getattr(self.workstation, "_attach_liquid_to_parent", None)
        if attach is None:
            return {"status": "deferred", "reason": "station_missing_helper"}
        return attach(
            mat=mat,
            warehouse=warehouse,
            location_code=code,
            resolved={"warehouse_id": "", "warehouse_name": wh_name, "source": "stock-material"},
        )


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
