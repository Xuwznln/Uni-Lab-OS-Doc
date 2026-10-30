"""仅在授权驱动已安装时登记其 PLR 扩展，公开资源模型保持独立。"""

from importlib import import_module


def register() -> None:
    for module in (
        "unilabos.devices.liquid_handling.rviz_backend",
        "unilabos.devices.workstation.workstation_base",
    ):
        try:
            import_module(module)
        except ModuleNotFoundError as error:
            # 扩展本身未安装可省略；已安装扩展内部缺依赖仍应明确报错。
            if error.name is None or not (module == error.name or module.startswith(error.name + ".")):
                raise
