"""小核酸工作站最小运行时脚手架。"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple
from urllib import error, request
from uuid import UUID

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
    from unilabos.registry.decorators import NodeType, action, device, not_action
    _REGISTRY_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - 允许无完整依赖时导入轻量 helper
    _REGISTRY_IMPORT_ERROR = exc

    class NodeType:  # type: ignore[no-redef]
        MANUAL_CONFIRM = "manual_confirm"

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
    from unilabos.devices.workstation.workstation_base import WorkstationBase
    from unilabos.devices.workstation.bioyond_studio.station import BioyondWorkstation
    _BIOYOND_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - 允许在轻量探测模式下运行配置辅助函数
    WorkstationBase = object  # type: ignore[assignment,misc]
    BioyondWorkstation = object  # type: ignore[assignment,misc]
    _BIOYOND_IMPORT_ERROR = exc


WORKFLOW_LIST_ENDPOINT = "/api/lims/workflow/work-flow-list"
SUPPORTED_WORKFLOW_TYPES = {0, 1, 2}
DEFAULT_READY_SIGNAL = "READY"
DEFAULT_RESET_OPERATIONS = ("scheduler_reset", "reset_order_status", "reset_location")


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
        self._apply_env_api_config(merged_config)

        self.protocol_type = protocol_type
        self.bioyond_config = merged_config

        logger.info("BioyondSirnaStation 初始化开始")
        logger.info(f"  - API Host: {self.bioyond_config.get('api_host', '')}")
        logger.info(f"  - Workflow 映射数量: {len(self.bioyond_config.get('workflow_mappings', {}))}")

        self._lazy_frontend_init = deck is None or not self._has_required_api_config(self.bioyond_config)
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
            logger.warning(
                "BioyondSirnaStation 以延迟模式初始化：缺少 deck 或 api_host/api_key，"
                "设备会先注册动作，首次调用动作时再检查 Bioyond API 配置。"
            )
        else:
            super().__init__(bioyond_config=self.bioyond_config, deck=deck)

        logger.info("BioyondSirnaStation 初始化完成")

    @not_action
    def post_init(self, ros_node: Any) -> None:
        if getattr(self, "_lazy_frontend_init", False):
            WorkstationBase.post_init(self, ros_node)
            logger.warning("BioyondSirnaStation 延迟模式跳过 Bioyond 后台同步和 HTTP 服务启动")
            return
        super().post_init(ros_node)

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
        goal_default={"timeout_seconds": 3600, "assignee_user_ids": []},
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
        return self._with_ready_signal(result)

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
        goal_default={"timeout_seconds": 3600, "assignee_user_ids": []},
        feedback_interval=300,
        description="提交小核酸实验",
    )
    def submit_experiment(
        self,
        workflow_name: str = "",
        sub_workflow_name: str = "",
        order_code: str = "",
        order_name: str = "",
        sample_throughput: int = 4,
        parameter_overrides: Optional[Dict[str, Any]] = None,
        include_all_task_displayable: bool = False,
        reset_before_create: bool = True,
        reset_operations: Optional[
            List[Literal["scheduler_reset", "reset_order_status", "reset_location"]]
        ] = None,
        reset_order_id: str = "",
        reset_location_id: str = "",
        cleanup_order_code: str = "",
        api_host: str = "",
        api_key: str = "",
        ready_signal: str = "READY",
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """实时查询 LIMS 工作流和步骤参数，构造并提交 create-order。"""
        del timeout_seconds, assignee_user_ids, kwargs
        self._update_runtime_api_config(api_host=api_host, api_key=api_key)
        self._require_ready_signal(ready_signal)
        rpc = self._require_hardware_interface("create_order")
        workflow = self._resolve_experiment_workflow(
            rpc,
            workflow_name=workflow_name,
            sub_workflow_name=sub_workflow_name,
        )
        step_data = rpc.workflow_step_query(workflow["sub_workflow_id"])
        param_values, parameter_template = self._build_param_values_from_step_data(
            step_data,
            parameter_overrides=parameter_overrides or [],
            include_all_task_displayable=include_all_task_displayable,
        )
        if not param_values:
            raise RuntimeError("未从 LIMS 子工作流参数中提取到 create_order paramValues")

        resolved_order_code, resolved_order_name = self._build_bioyond_order_identity(order_code, order_name)
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

        reset_result = None
        if reset_before_create:
            reset_result = self._run_reset_operations(
                rpc,
                reset_operations=reset_operations,
                reset_order_id=reset_order_id,
                reset_location_id=reset_location_id,
                cleanup_order_code=cleanup_order_code,
            )

        logger.info(f"正在提交小核酸实验: {resolved_order_name} ({resolved_order_code})")
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
        result = {
            "success": self._create_result_success(parsed_result, order_ids, material_records),
            "order_code": resolved_order_code,
            "order_name": resolved_order_name,
            "order_ids": order_ids,
            "workflow": workflow,
            "sample_throughput": int(sample_throughput),
            "reset_result": reset_result,
            "payload": order_payload,
            "parameter_template": parameter_template,
            "create_order_result": parsed_result,
            "materials": material_records,
            "suggested_locations": suggested_locations,
            "start_experiment": start_experiment_info,
            "confirmation_message": self._format_create_order_confirmation(
                order_code=resolved_order_code,
                order_name=resolved_order_name,
                workflow=workflow,
                order_ids=order_ids,
                material_records=material_records,
                suggested_locations=suggested_locations,
            ),
        }
        return self._with_ready_signal(result)

    @action(
        always_free=True,
        node_type=NodeType.MANUAL_CONFIRM,
        placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
        goal_default={"timeout_seconds": 3600, "assignee_user_ids": []},
        feedback_interval=300,
        description="启动小核酸实验调度",
    )
    def start_experiment(
        self,
        submit_experiment_result: Optional[Dict[str, Any]] = None,
        order_id: str = "",
        order_ids: Optional[List[str]] = None,
        api_host: str = "",
        api_key: str = "",
        ready_signal: str = "READY",
        timeout_seconds: int = 3600,
        assignee_user_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """启动 Bioyond 调度器。"""
        del timeout_seconds, assignee_user_ids, kwargs
        self._update_runtime_api_config(api_host=api_host, api_key=api_key)
        self._require_ready_signal(ready_signal)
        start_info = self._resolve_start_experiment_info(submit_experiment_result, order_id, order_ids)
        rpc = self._require_hardware_interface("scheduler_start")
        logger.info("正在启动小核酸调度器")
        result = rpc.scheduler_start()
        return self._with_ready_signal({
            "success": result == 1,
            "return_info": result,
            "scheduler_start_result": result,
            "start_experiment": start_info,
            "confirmation_message": "调度器启动成功" if result == 1 else "调度器启动失败，请检查 LIMS 状态",
        })

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
        if not self._has_required_api_config(self.bioyond_config):
            raise RuntimeError(
                "Bioyond RPC 客户端未初始化，且缺少 api_host/api_key。"
                "请在前端节点 config 中配置 api_host、api_key，或在动作参数中传入 api_host、api_key。"
            )
        from unilabos.devices.workstation.bioyond_studio.bioyond_rpc import BioyondV1RPC

        self.hardware_interface = BioyondV1RPC(self.bioyond_config)
        return self.hardware_interface

    def _has_required_api_config(self, config: Dict[str, Any]) -> bool:
        return not self._is_blank(config.get("api_host")) and not self._is_blank(config.get("api_key"))

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
        workflow_data = rpc.query_workflow(json.dumps(workflow_query, ensure_ascii=False))
        workflow_items = self._workflow_items(workflow_data)
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
            raise RuntimeError("未从 LIMS 查询到可用的小核酸工作流")
        sub = self._select_workflow_record(self._sub_workflow_records(root), sub_workflow_name)
        if not sub:
            label = self._record_name(root) or workflow_name or self._record_id(root)
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
        skipped_operations: List[Dict[str, str]] = []
        if "reset_order_status" in operations:
            reset_order_id = self._resolve_reset_order_id(rpc, reset_order_id, cleanup_order_code)
            if not reset_order_id:
                skipped_operations.append({
                    "operation": "reset_order_status",
                    "reason": "未查询到可复位订单，跳过订单状态复位",
                })
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
        if "reset_order_status" in operations and reset_order_id:
            self._require_rpc_method(rpc, "reset_order_status")
            result["reset_order_status"] = rpc.reset_order_status(reset_order_id)
        if "reset_location" in operations:
            self._require_rpc_method(rpc, "reset_location")
            result["reset_location"] = rpc.reset_location(reset_location_id)

        if "reset_order_status" in operations and reset_order_id:
            snapshot = self._query_order_snapshot(rpc, cleanup_order_code or reset_order_id)
            targets = self._extract_takeout_targets(snapshot, reset_order_id)
            result["cleanup_targets"] = targets
            if targets["requires_take_out"]:
                result["take_out"] = self._take_out_remaining_materials(rpc, targets)
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
    ) -> str:
        lines = [
            f"实验已提交: {order_name} ({order_code})",
            f"工作流: {workflow.get('workflow_name', '')} / {workflow.get('sub_workflow_name', '')}",
        ]
        if order_ids:
            lines.append(f"实验ID: {', '.join(order_ids)}")
        if material_records:
            lines.append("所需物料与建议库位:")
            for index, record in enumerate(material_records, 1):
                name = record.get("materialName") or "未命名物料"
                code = record.get("materialCode") or "-"
                quantity = record.get("quantity") or "-"
                material_type = record.get("materialTypeName") or record.get("materialTypeMode") or "-"
                location = record.get("locationShowName") or record.get("locationCode") or "-"
                lines.append(f"{index}. {name} [{code}], {quantity}, {material_type}, 建议库位 {location}")
        else:
            lines.append("所需物料与建议库位: LIMS 未返回预分配记录")
        if suggested_locations:
            lines.append("库位汇总:")
            for index, location in enumerate(suggested_locations, 1):
                material = location.get("material_name") or "未命名物料"
                code = location.get("location_code") or location.get("location_id") or "-"
                lines.append(f"{index}. {material} -> {code}")
        return "\n".join(lines)

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


def main() -> int:
    """命令行入口：读取配置并拉取工作流列表。"""
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
