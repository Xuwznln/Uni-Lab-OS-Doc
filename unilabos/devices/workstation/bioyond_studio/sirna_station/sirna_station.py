"""小核酸工作站最小运行时脚手架。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import error, request

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[5]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

from unilabos.utils.log import logger

try:
    from unilabos.devices.workstation.bioyond_studio.station import BioyondWorkstation
    _BIOYOND_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - 允许在轻量探测模式下运行配置辅助函数
    BioyondWorkstation = object  # type: ignore[assignment,misc]
    _BIOYOND_IMPORT_ERROR = exc


WORKFLOW_LIST_ENDPOINT = "/api/lims/workflow/work-flow-list"
SUPPORTED_WORKFLOW_TYPES = {0, 1, 2}


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


class BioyondSirnaStation(BioyondWorkstation):
    """小核酸工作站最小运行时实现。"""

    def __init__(
        self,
        bioyond_config: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
        deck: Optional[Any] = None,
        protocol_type: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        if _BIOYOND_IMPORT_ERROR is not None:
            raise RuntimeError(f"BioyondSirnaStation 基类导入失败: {_BIOYOND_IMPORT_ERROR}") from _BIOYOND_IMPORT_ERROR

        merged_config: Dict[str, Any] = {}
        if config:
            merged_config.update(config)
        if bioyond_config:
            merged_config.update(bioyond_config)
        merged_config.update(kwargs)

        self.protocol_type = protocol_type
        self.bioyond_config = merged_config

        logger.info("BioyondSirnaStation 初始化开始")
        logger.info(f"  - API Host: {self.bioyond_config.get('api_host', '')}")
        logger.info(f"  - Workflow 映射数量: {len(self.bioyond_config.get('workflow_mappings', {}))}")

        super().__init__(bioyond_config=self.bioyond_config, deck=deck)

        logger.info("BioyondSirnaStation 初始化完成")

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
