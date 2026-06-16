from typing import Any

from pylabrobot.resources import Resource


class ResourceSlot(Resource):
    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        # pydantic 无法内省 pylabrobot Resource 子类，会导致包含 ResourceSlot 的
        # TypedDict 整体回退为 {"type": "object"}。这里显式声明为对象 schema。
        from pydantic_core import core_schema

        return core_schema.dict_schema()


class DeviceSlot(str):
    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        # DeviceSlot 本质是设备 id 字符串；pydantic 不会把 str 子类当 str 处理，
        # 不声明就会让包含它的 TypedDict 解析失败并回退为 {"type": "object"}。
        from pydantic_core import core_schema

        return core_schema.str_schema()


# ---------------------------------------------------------------------------
# placeholder_keys 常量
# ---------------------------------------------------------------------------
# 这些常量标注「动作参数在前端应以何种选择器填入」。与 ResourceSlot/DeviceSlot 同源：
# ResourceSlot 让框架把传入的 uuid 解析成实例（参数类型层面），而 placeholder_keys
# 常量告诉前端这个字段该用哪种选择器（界面/数据来源层面）。
PLACEHOLDER_RESOURCES = "unilabos_resources"
PLACEHOLDER_DEVICES = "unilabos_devices"
PLACEHOLDER_NODES = "unilabos_nodes"
PLACEHOLDER_CLASS = "unilabos_class"
PLACEHOLDER_MANUAL_CONFIRM = "unilabos_manual_confirm"
# 物料扣减：前端选择资源注册表类型 + 数量，由服务端扣减后回传实例的 uuid。
PLACEHOLDER_DEDUCT_RESOURCE = "unilabos_deduct_resource"
