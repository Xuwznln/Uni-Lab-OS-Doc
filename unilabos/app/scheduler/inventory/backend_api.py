"""FastAPI adapter matching Backend c35d821 resource routes and envelope."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from unilabos.app.scheduler.inventory.backend_contract import (
    MAX_SITE_IDENTITY_RESOLUTION_IDS,
    TEMPLATE_DATA_CONFLICT,
    BackendContractError,
    BackendResourceService,
)
from unilabos.app.scheduler.inventory.backend_response import (
    call as _call,
)
from unilabos.app.scheduler.inventory.backend_response import (
    error_response as _error,
)
from unilabos.app.scheduler.inventory.backend_response import (
    success as _success,
)
from unilabos.app.scheduler.inventory.content_api import (
    create_container_content_router,
)
from unilabos.app.scheduler.inventory.content_contract import (
    BackendContainerContentService,
)
from unilabos.app.scheduler.inventory.reagent_api import (
    MaterialReagentRequest,
    create_reagent_router,
)
from unilabos.app.scheduler.inventory.reagent_contract import BackendReagentService


class BackendModel(BaseModel):
    """Gin binding-compatible model: recognized fields are typed, extras ignored."""

    model_config = ConfigDict(extra="ignore")


class ResourceTemplateSyncRequest(BackendModel):
    resources: List[Dict[str, Any]] = Field(default_factory=list)


class ResourceTemplateUpdateRequest(BackendModel):
    display_name: str = ""
    description: Optional[str] = None
    icon: Optional[str] = None
    registry_type: str = "resource"
    model: Dict[str, Any] = Field(default_factory=dict)
    class_: Dict[str, Any] = Field(default_factory=dict, alias="class")
    handles: List[Dict[str, Any]] = Field(default_factory=list)
    init_param_schema: Optional[Dict[str, Any]] = None
    category: List[Any] = Field(default_factory=list)
    config_info: List[Any] = Field(default_factory=list)
    available_sites: List[Dict[str, Any]] = Field(default_factory=list)
    cover: Optional[str] = None
    scene: List[Any] = Field(default_factory=list)
    device_params: Dict[str, Any] = Field(default_factory=dict)


class SitePlacementRequest(BackendModel):
    action: Literal["place", "remove"]
    site_uuid: Optional[UUID] = None

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value: Any) -> Any:
        """把 Backend 接受的大小写动作规范为小写枚举值。

        参数：``value`` 是未信任 JSON 动作。返回：字符串时去空白并转小写，其余
        类型交给 Pydantic 拒绝。异常：非法枚举由字段校验统一报告。
        """

        return value.strip().lower() if isinstance(value, str) else value


class RelativePositionRequest(BackendModel):
    position_x: float = 0
    position_y: float = 0
    position_z: float = 0
    depth: float = Field(default=0, ge=0)
    length: float = Field(default=0, ge=0)
    width: float = Field(default=0, ge=0)
    scale_x: float = Field(default=1, gt=0)
    scale_y: float = Field(default=1, gt=0)
    scale_z: float = Field(default=1, gt=0)
    rotation_x: float = 0
    rotation_y: float = 0
    rotation_z: float = 0
    description: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)


class MaterialRequest(BackendModel):
    resource_template_uuid: UUID
    parent_uuid: Optional[UUID] = None
    barcode: str = ""
    name: str
    description: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)
    config: Dict[str, Any] = Field(default_factory=dict)
    relative_position: Optional[RelativePositionRequest] = None
    site_placement: Optional[SitePlacementRequest] = None


class MaterialCreateRequest(MaterialRequest):
    """创建物料时可选地在同一事务装入试剂。"""

    reagent: Optional[MaterialReagentRequest] = None


class MaterialUpdateRequest(BackendModel):
    """按字段更新物料；未出现或显式为 ``null`` 的字段保持原值。"""

    resource_template_uuid: Optional[UUID] = None
    parent_uuid: Optional[UUID] = None
    barcode: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    meta_data: Optional[Dict[str, Any]] = None
    config: Optional[Dict[str, Any]] = None
    relative_position: Optional[RelativePositionRequest] = None
    site_placement: Optional[SitePlacementRequest] = None
    expected_revision: Optional[int] = Field(default=None, ge=0)


class MaterialStateRequest(BackendModel):
    status: Optional[str] = None
    state_data: Dict[str, Any] = Field(default_factory=dict)
    source: Optional[str] = None
    observed_at: Optional[str] = None
    description: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)


class SiteIdentityResolveRequest(BackendModel):
    """Edge 注册阶段批量解析库位业务身份的窄请求。"""

    source_node_ids: List[str] = Field(
        max_length=MAX_SITE_IDENTITY_RESOLUTION_IDS
    )

    @field_validator("source_node_ids", mode="before")
    @classmethod
    def normalize_source_node_ids(cls, value: Any) -> Any:
        """在批量上限校验前去除空白和重复字符串。"""

        if not isinstance(value, list):
            return value
        normalized: List[Any] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                normalized.append(item)
                continue
            identity = item.strip()
            if identity and identity not in seen:
                seen.add(identity)
                normalized.append(identity)
        return normalized


def create_backend_resource_router(
    service: BackendResourceService,
    *,
    material_shapes: Sequence[Mapping[str, Any]] = (),
    material_model_catalog: Any = None,
    compound_source: Any = None,
) -> APIRouter:
    """创建前端资源合同路由并附带静态物料外形。

    参数：``service`` 提供公共资源读写；``material_shapes`` 是已由包资产编译器
    校验的静态外形投影；``material_model_catalog`` 只读取本启动代际授权的模型
    资产。返回：不包含 Edge 私有库存路由的 ``APIRouter``。异常：外形项目或模型
    目录形状非法时原样抛出。
    """

    router = APIRouter(prefix="/api/v1", tags=["backend-resource-contract"])
    router.include_router(
        create_material_asset_router(
            material_shapes=material_shapes,
            material_model_catalog=material_model_catalog,
        )
    )
    router.include_router(
        create_reagent_router(
            BackendReagentService(
                service.store,
                edge_id=service.edge_id,
                lab_id=service.lab_id,
                compound_source=compound_source,
            )
        )
    )
    router.include_router(
        create_container_content_router(
            BackendContainerContentService(
                service.store,
                edge_id=service.edge_id,
                lab_id=service.lab_id,
            )
        )
    )

    @router.post("/resource-templates")
    def sync_resource_templates(body: ResourceTemplateSyncRequest) -> JSONResponse:
        return _call(service.sync_resource_templates, body.resources)

    @router.get("/resource-templates")
    def list_resource_templates(
        page: int = Query(default=0),
        page_size: int = Query(default=0),
        keyword: str = Query(default=""),
        resource_type: str = Query(default=""),
    ) -> JSONResponse:
        return _call(
            service.list_resource_templates,
            page=page,
            page_size=page_size,
            keyword=keyword,
            resource_type=resource_type,
        )

    @router.get("/resource-templates/{template_uuid}")
    def get_resource_template(template_uuid: UUID) -> JSONResponse:
        return _call(service.get_resource_template, str(template_uuid))

    @router.put("/resource-templates/{template_uuid}")
    def update_resource_template(
        template_uuid: UUID, body: ResourceTemplateUpdateRequest
    ) -> JSONResponse:
        """更新一项本地资源模板（ResourceTemplate）定义。

        参数：``template_uuid`` 是既有模板稳定身份，``body`` 是调用方显式更新
        内容。返回：公共 Backend 信封中的最新模板详情；模板不存在、字段非法或
        持久化冲突时返回对应业务错误，未传 ``available_sites`` 时保留既有定义。
        """

        try:
            template_identity = str(template_uuid)
            current = service.get_resource_template(template_identity)
            if (
                current.get("resource_type") == "device"
                and service.store.runtime_device_template_catalog_required
            ):
                raise BackendContractError(
                    TEMPLATE_DATA_CONFLICT,
                    "runtime device templates can only be changed by the domain package",
                )
            definition = body.model_dump(by_alias=True, mode="json")
            if "registry_type" not in body.model_fields_set:
                definition["registry_type"] = current.get("resource_type") or "resource"
            if "handles" not in body.model_fields_set:
                definition.pop("handles", None)
            if "available_sites" not in body.model_fields_set:
                # 旧调用方更新展示字段时未携带新字段，不得清空既有库位模板定义。
                definition["available_sites"] = current.get("available_sites") or []
            definition["id"] = current["name"]
            service.sync_resource_templates([definition])
            return _success(service.get_resource_template(template_identity))
        except BackendContractError as error:
            return _error(error)

    @router.delete("/resource-templates/{template_uuid}")
    def delete_resource_template(template_uuid: UUID) -> JSONResponse:
        return _call(service.delete_resource_template, str(template_uuid))

    @router.post("/materials")
    def create_material(body: MaterialCreateRequest) -> JSONResponse:
        return _call(
            service.create_material,
            body.model_dump(mode="json"),
            status_code=201,
        )

    @router.get("/materials")
    def list_materials(
        page: int = Query(default=0),
        page_size: int = Query(default=0),
        name: str = Query(default=""),
        barcode: str = Query(default=""),
        resource_template_uuid: Optional[UUID] = Query(default=None),
    ) -> JSONResponse:
        return _call(
            service.list_materials,
            page=page,
            page_size=page_size,
            name=name,
            barcode=barcode,
            resource_template_uuid=(
                str(resource_template_uuid) if resource_template_uuid else None
            ),
        )

    @router.get("/materials/graph")
    def get_material_graph() -> JSONResponse:
        return _call(service.material_graph)

    @router.post("/materials/sites/resolve-identities")
    def resolve_site_identities(body: SiteIdentityResolveRequest) -> JSONResponse:
        """把资源图 Site 业务 ID 批量解析为 Backend 稳定 UUID。"""

        return _call(service.resolve_site_identities, body.source_node_ids)

    @router.get("/materials/{material_uuid}")
    def get_material(material_uuid: UUID) -> JSONResponse:
        return _call(service.get_material, str(material_uuid))

    @router.put("/materials/{material_uuid}")
    def update_material(
        material_uuid: UUID, body: MaterialUpdateRequest
    ) -> JSONResponse:
        """按字段修改物料，或通过 ``site_placement`` 执行人工上下料。

        参数：路径 UUID 是物料稳定身份，``body`` 仅更新明确给出且非空的字段；
        ``place`` 放入或换到目标库位，``remove`` 解除当前精确库位占用。返回：统一
        数值信封中的最新物料详情。异常：参数、修订、模板、父级或库位
        冲突由资源服务映射为稳定 Backend 错误码。
        """

        values = body.model_dump(mode="json", exclude_unset=True)
        values["_specified_fields"] = set(body.model_fields_set)
        values["_relative_position_specified"] = (
            "relative_position" in body.model_fields_set
            and body.relative_position is not None
        )
        return _call(
            service.update_material,
            str(material_uuid),
            values,
        )

    @router.delete("/materials/{material_uuid}")
    def delete_material(
        material_uuid: UUID,
        scope: Literal["material_only", "material_with_contents"] = Query(
            default="material_only"
        ),
    ) -> JSONResponse:
        """删除空闲物料；带内容的容器需 ``scope=material_with_contents``。"""

        return _call(service.delete_material, str(material_uuid), scope=scope)

    @router.get("/materials/{material_uuid}/ledger")
    def list_material_ledger(
        material_uuid: UUID,
        page: int = Query(default=1),
        page_size: int = Query(default=50),
    ) -> JSONResponse:
        """读取一只物料的台账，按发生时间倒序。"""

        return _call(
            service.list_material_ledger,
            str(material_uuid),
            page=page,
            page_size=page_size,
        )

    @router.get("/materials/{material_uuid}/sites")
    def list_sites(material_uuid: UUID) -> JSONResponse:
        try:
            material_identity = str(material_uuid)
            service.get_material(material_identity)
            return _success(service.list_sites(material_identity))
        except BackendContractError as error:
            return _error(error)

    @router.post("/materials/{material_uuid}/states")
    def append_material_state(
        material_uuid: UUID, body: MaterialStateRequest
    ) -> JSONResponse:
        return _call(
            service.append_material_state,
            str(material_uuid),
            body.model_dump(mode="json"),
            status_code=201,
        )

    @router.get("/materials/{material_uuid}/states")
    def list_material_states(
        material_uuid: UUID,
        before_time: Optional[str] = Query(default=None),
        before_uuid: Optional[str] = Query(default=None),
        limit: int = Query(default=0),
    ) -> JSONResponse:
        return _call(
            service.list_material_states,
            str(material_uuid),
            before_time=before_time,
            before_uuid=before_uuid,
            limit=limit,
        )

    @router.get("/materials/{material_uuid}/states/latest")
    def latest_material_state(material_uuid: UUID) -> JSONResponse:
        return _call(service.latest_material_state, str(material_uuid))

    @router.get("/material-states/{state_uuid}")
    def get_material_state(state_uuid: UUID) -> JSONResponse:
        return _call(service.get_material_state, str(state_uuid))

    @router.get("/sites/{site_uuid}")
    def get_site(site_uuid: UUID) -> JSONResponse:
        return _call(service.get_site, str(site_uuid))

    return router


def create_material_asset_router(
    *,
    material_shapes: Sequence[Mapping[str, Any]] = (),
    material_model_catalog: Any = None,
) -> APIRouter:
    """创建不依赖 Inventory 的只读物料外形与模型资产路由。

    参数：``material_shapes`` 和 ``material_model_catalog`` 必须来自同一工作区
    编译代。返回：可挂载到正式资源合同或纯 Authoring Web 进程的 Router。
    异常：外形项目形状非法时原样抛出。
    """

    router = APIRouter()
    # ``frozen_material_shapes`` 与调用者容器隔离，保证启动代际不会随请求漂移。
    frozen_material_shapes = tuple(deepcopy(dict(shape)) for shape in material_shapes)

    @router.get("/material-shapes")
    def list_material_shapes() -> JSONResponse:
        """返回工作区包声明的静态物料外形。

        参数：无。返回：Backend 公共信封中的外形项目列表。异常：无。
        """

        return _success({"items": deepcopy(list(frozen_material_shapes))})

    @router.get("/material-models/{asset_path:path}")
    def read_material_model(asset_path: str) -> Response:
        """返回一项工作区 3D 模型资产。

        参数：``asset_path`` 是公共模型路由内的相对路径。返回：带媒体类型、摘要
        和禁止陈旧缓存策略的资产字节。异常：目录未安装或资产未授权时返回 404。
        """

        if material_model_catalog is None:
            raise HTTPException(status_code=404, detail="模型资产目录未安装")
        public_path = f"/api/v1/material-models/{asset_path}"
        try:
            # ``asset`` 是当前工作区启动代际完成边界校验后的不可变读取结果。
            asset = material_model_catalog.read_asset(public_path)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="模型资产未找到") from error
        return Response(
            content=asset.content,
            media_type=asset.media_type,
            headers={
                "Cache-Control": "private, max-age=0, must-revalidate",
                "ETag": f'"{asset.etag}"',
            },
        )

    return router


def install_backend_resource_api(
    app: FastAPI,
    service: BackendResourceService,
    *,
    material_shapes: Sequence[Mapping[str, Any]] = (),
    material_model_catalog: Any = None,
    compound_source: Any = None,
) -> None:
    """安装公共资源路由与 Backend 校验信封。

    参数：``app`` 是产品 FastAPI 应用；``service`` 是资源合同服务；
    ``material_shapes`` 是工作区包资产的静态公共投影；
    ``material_model_catalog`` 是同代受限模型目录。返回：无。
    异常：路由、外形或模型装配错误原样抛出。
    """

    @app.exception_handler(RequestValidationError)
    async def backend_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        shared_prefixes = (
            "/api/v1/resource-templates",
            "/api/v1/materials",
            "/api/v1/material-states",
            "/api/v1/sites",
            "/api/v1/compounds",
            "/api/v1/reagent-infos",
            "/api/v1/reagents",
            "/api/v1/reagent-history",
            "/api/v1/samples",
            "/api/v1/current-substances",
            "/api/v1/substance-history",
            "/api/v1/workflows",
            "/api/v1/workflow-tasks",
            "/api/v1/workflow-node-jobs",
        )
        if any(request.url.path.startswith(prefix) for prefix in shared_prefixes):
            return _error(BackendContractError(1000, "Invalid request parameter"))
        return await request_validation_exception_handler(request, error)

    app.include_router(
        create_backend_resource_router(
            service,
            material_shapes=material_shapes,
            material_model_catalog=material_model_catalog,
            compound_source=compound_source,
        )
    )


__all__ = [
    "create_backend_resource_router",
    "create_material_asset_router",
    "install_backend_resource_api",
]
