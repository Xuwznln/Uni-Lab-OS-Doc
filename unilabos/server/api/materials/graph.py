"""``/api/v1/graphs`` FastAPI 路由：materials 域设备图的 HTTP 契约。

图不是独立权威：节点/边/快照都落 materials.db，业务在
``services.graph.GraphService``。响应统一使用 envelope
（``{"code": 0, "data": ...}``），与云端 Backend 协议同构；``--remote``
时 CLI 客户端以同一契约访问云端。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from unilabos.server.services.materials.graph import GraphError, GraphService

#: envelope 业务码：not_found 独立编码便于客户端区分，其余归为通用失败。
_BUSINESS_CODES = {"not_found": 3002, "invalid_input": 2, "invalid_payload": 2}


class GraphUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    payload: Dict[str, Any]
    uuid: Optional[str] = None
    tags: List[Any] = Field(default_factory=list)
    description: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)


def _success(data: Any = None) -> JSONResponse:
    content: Dict[str, Any] = {"code": 0}
    if data is not None:
        content["data"] = data
    return JSONResponse(status_code=200, content=content)


def _error(error: GraphError) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "code": _BUSINESS_CODES.get(error.code, 1),
            "error": error.message,
        },
    )


def create_graph_router(service: GraphService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["graphs-v1"])

    @router.get("/graphs")
    async def list_graphs(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=100, ge=1, le=1000),
        name: str = Query(default=""),
    ):
        try:
            return _success(
                service.list_graphs(page=page, page_size=page_size, name=name)
            )
        except GraphError as error:
            return _error(error)

    @router.post("/graphs")
    async def upsert_graph(value: GraphUpsertRequest):
        try:
            from unilabos.registry.registry import lab_registry

            device_site_templates = {
                device_id: (entry or {}).get("available_sites") or []
                for device_id, entry in lab_registry.device_type_registry.items()
            }
            return _success(
                service.upsert_graph(
                    name=value.name,
                    payload=value.payload,
                    uuid=value.uuid,
                    tags=value.tags,
                    description=value.description,
                    meta_data=value.meta_data,
                    device_site_templates=device_site_templates,
                )
            )
        except GraphError as error:
            return _error(error)

    @router.get("/graphs/live/payload")
    async def get_live_payload():
        """当前真实拓扑：material + material_link 实时序列化（非快照回放）。"""
        try:
            return _success(service.live_payload())
        except GraphError as error:
            return _error(error)

    @router.get("/graphs/{identity}")
    async def get_graph(identity: str):
        try:
            return _success(service.get_graph(identity))
        except GraphError as error:
            return _error(error)

    @router.get("/graphs/{identity}/payload")
    async def get_graph_payload(identity: str):
        try:
            return _success(service.get_payload(identity))
        except GraphError as error:
            return _error(error)

    @router.delete("/graphs/{identity}")
    async def delete_graph(identity: str):
        try:
            service.delete_graph(identity)
            return _success({})
        except GraphError as error:
            return _error(error)

    return router


def install_graph_api(app: FastAPI, service: GraphService) -> None:
    app.include_router(create_graph_router(service))


__all__ = ["GraphUpsertRequest", "create_graph_router", "install_graph_api"]
