"""驱动包管理 API（``/api/v1/driver-packages``）。

给前端「驱动包」页用：看台账、装 / 卸 / 启停、跟踪后台操作；生效需要走
``POST /api/v1/restart``（安静点整进程重启），本路由只把 ``restart_required``
标出来，不自己重启。官方 / 社区驱动包索引由前端（edge-ui）持有并读取，
这里只接收前端下发的安装规格。
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from unilabos.server.services.driver_packages import (
    DriverPackageError,
    get_driver_package_service,
)


class DriverPackageInstallRequest(BaseModel):
    """安装来源：GitHub 仓库地址（https://github.com/<owner>/<repo>[@ref]）、zip / tar.gz 归档地址或本机目录。

    源码树落到 unilabos_data/driver_packages/<name>/<version>/（本机目录原地登记），
    依赖用 uv / pip 预装，不 pip install 包体。
    """

    spec: str = Field(min_length=1)
    enable: bool = True
    upgrade: bool = Field(default=False, description="重新下载源码树，并以 --upgrade 重装其依赖")
    name: str = Field(default="", description="已知的包名（索引条目自带）；源码树没有 pyproject 时用它登记")


class DriverPackageEnableRequest(BaseModel):
    enabled: bool


def create_driver_packages_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/driver-packages", tags=["driver-packages"])

    def _call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except DriverPackageError as exc:
            message = str(exc)
            status = 404 if "not found" in message else 422
            raise HTTPException(status_code=status, detail=message) from exc

    @router.get("")
    def inventory() -> Dict[str, Any]:
        return get_driver_package_service().inventory()

    @router.get("/catalog")
    def catalog() -> Dict[str, Any]:
        """官方索引（HTTPConfig.driver_package_index_url）+ 本地 driver_package_catalog.json 合并后的可安装目录。"""
        return get_driver_package_service().catalog()

    @router.post("/install", status_code=202)
    def install(body: DriverPackageInstallRequest) -> Dict[str, Any]:
        return _call(get_driver_package_service().start_install, body.spec, body.enable, body.upgrade, body.name)

    @router.get("/operations")
    def operations() -> List[Dict[str, Any]]:
        return get_driver_package_service().operations()

    @router.get("/operations/{operation_id}")
    def operation(operation_id: str) -> Dict[str, Any]:
        return _call(get_driver_package_service().operation, operation_id)

    @router.put("/{name}/enabled")
    def set_enabled(name: str, body: DriverPackageEnableRequest) -> Dict[str, Any]:
        return _call(get_driver_package_service().set_enabled, name, body.enabled)

    @router.delete("/{name}", status_code=202)
    def uninstall(name: str) -> Dict[str, Any]:
        return _call(get_driver_package_service().start_uninstall, name)

    return router


__all__ = ["create_driver_packages_router"]
