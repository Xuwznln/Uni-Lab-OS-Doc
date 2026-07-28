from pylabrobot.resources import Resource

from unilabos.resources.itemized_carrier import Bottle


def AI4C_Powder_Cylinder(
    name: str,
    diameter: float = 50.0,
    height: float = 90.0,
    max_volume: float = 100000.0,
    barcode: str = None,
) -> Bottle:
    """创建 AI4C 粉桶。"""
    return Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="AI4C_Powder_Cylinder",
    )


def AI4C_Well_Plate(
    name: str,
    size_x: float = 127.8,
    size_y: float = 85.5,
    size_z: float = 14.5,
) -> Resource:
    """创建 AI4C 流程中机械臂搬运的孔板占位资源。"""
    return Resource(
        name=name,
        size_x=size_x,
        size_y=size_y,
        size_z=size_z,
        category="plate",
        model="AI4C_Well_Plate",
    )
