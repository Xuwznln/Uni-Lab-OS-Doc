"""Registry Authority API：Edge 上报 + 条目级版本管理。

``POST /api/v1/resource-templates`` 接收 gzip JSON 全量快照，并返回
``{code, data:{templates:[{name,uuid}]}}``。其余端点提供条目状态、
版本历史、挂起版本确认或忽略、历史还原及上报批次统计。
"""

from __future__ import annotations

import gzip
import json
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request

from unilabos.server.services.runtime.registry import (
    RegistryAuthorityError,
    RegistryService,
    get_registry_service,
)

router = APIRouter(tags=["Registry"])


def _require_service() -> RegistryService:
    service = get_registry_service()
    if service is None:
        raise HTTPException(status_code=503, detail="registry service not ready")
    return service


def _ok(data: Any) -> dict[str, Any]:
    return {"code": 0, "data": data}


@router.post("/api/v1/resource-templates")
async def report_resource_templates(request: Request) -> dict[str, Any]:
    """Edge 全量上报（条目级替换：变了才升该条目版本，冲突挂起待确认）。"""

    service = _require_service()
    body = await request.body()
    if request.headers.get("content-encoding", "").lower() == "gzip":
        try:
            body = gzip.decompress(body)
        except OSError as exc:
            raise HTTPException(status_code=400, detail="invalid gzip body") from exc
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("resources"), list):
        raise HTTPException(status_code=400, detail="body must contain resources list")

    edge_uuid = str(payload.get("edge_uuid") or request.headers.get("x-edge-uuid") or "")
    try:
        report = service.report(payload["resources"], edge_uuid=edge_uuid)
    except RegistryAuthorityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _ok(report)


@router.get("/api/v1/registry/entries")
def list_registry_entries(
    status: str = Query(default=""),
) -> dict[str, Any]:
    """条目状态列表；``status`` 过滤 active/pending/removed/unusable。"""

    return _ok({"entries": _require_service().list_entries(status=status)})


@router.get("/api/v1/registry/pending-impacts")
def list_registry_pending_impacts() -> dict[str, Any]:
    """挂起条目影响面：冲突明细 + 受影响 workflow 节点（画布徽标数据源）。"""

    return _ok({"impacts": _require_service().pending_impacts()})


@router.get("/api/v1/registry/entries/{name}")
def get_registry_entry(name: str) -> dict[str, Any]:
    """条目详情：状态 + 生效 payload + 挂起 payload 与冲突明细。"""

    try:
        return _ok(_require_service().entry_detail(name))
    except RegistryAuthorityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/v1/registry/entries/{name}/versions")
def list_registry_entry_versions(name: str) -> dict[str, Any]:
    try:
        return _ok({"versions": _require_service().entry_versions(name)})
    except RegistryAuthorityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/v1/registry/entries/{name}/versions/{version}")
def get_registry_entry_version(name: str, version: int) -> dict[str, Any]:
    try:
        return _ok(_require_service().get_entry_version(name, version))
    except RegistryAuthorityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/v1/registry/entries/{name}/apply")
def apply_registry_entry(name: str) -> dict[str, Any]:
    """把挂起版本切换为生效版本。"""

    try:
        return _ok(_require_service().apply_pending(name))
    except RegistryAuthorityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/v1/registry/entries/{name}/dismiss")
def dismiss_registry_entry(name: str) -> dict[str, Any]:
    """忽略挂起版本（历史保留，生效版本不动）。"""

    try:
        return _ok(_require_service().dismiss_pending(name))
    except RegistryAuthorityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/v1/registry/entries/{name}/restore/{version}")
def restore_registry_entry(name: str, version: int) -> dict[str, Any]:
    """把历史版本内容还原为新的生效版本（条目版本号继续自增）。"""

    try:
        return _ok(_require_service().restore(name, version))
    except RegistryAuthorityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/v1/registry/reports")
def list_registry_reports(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """上报批次统计（新增/更新/挂起/移除/复活/不可用计数与明细）。"""

    reports, total = _require_service().list_reports(page=page, page_size=page_size)
    return _ok({"reports": reports, "total": total, "page": page, "page_size": page_size})


def install_registry_api(app: FastAPI) -> None:
    app.include_router(router)


__all__ = ["install_registry_api", "router"]
