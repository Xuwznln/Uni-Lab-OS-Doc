from pylabrobot.resources import Coordinate, ResourceHolder, create_homogeneous_resources

from unilabos.devices.workstation.AI4C.bottles import AI4C_Powder_Cylinder, AI4C_Well_Plate
from unilabos.resources.itemized_carrier import BottleCarrier


def AI4C_PowderCylinderCarrier(name: str) -> BottleCarrier:
    """创建 AI4C 单粉桶载架。"""
    carrier_size_x = 80.0
    carrier_size_y = 80.0
    carrier_size_z = 20.0
    cylinder_diameter = 50.0

    carrier = BottleCarrier(
        name=name,
        size_x=carrier_size_x,
        size_y=carrier_size_y,
        size_z=carrier_size_z,
        sites=create_homogeneous_resources(
            klass=ResourceHolder,
            locations=[
                Coordinate(
                    (carrier_size_x - cylinder_diameter) / 2,
                    (carrier_size_y - cylinder_diameter) / 2,
                    5.0,
                )
            ],
            resource_size_x=cylinder_diameter,
            resource_size_y=cylinder_diameter,
            resource_size_z=90.0,
            name_prefix=name,
        ),
        model="AI4C_PowderCylinderCarrier",
    )
    carrier.num_items_x = 1
    carrier.num_items_y = 1
    carrier.num_items_z = 1
    carrier[0] = AI4C_Powder_Cylinder(f"{name}_powder_cylinder_1")
    return carrier


def AI4C_WellPlateCarrier(name: str) -> BottleCarrier:
    """创建 AI4C 单孔板载架。"""
    carrier_size_x = 137.0
    carrier_size_y = 96.0
    carrier_size_z = 20.0

    carrier = BottleCarrier(
        name=name,
        size_x=carrier_size_x,
        size_y=carrier_size_y,
        size_z=carrier_size_z,
        sites=create_homogeneous_resources(
            klass=ResourceHolder,
            locations=[Coordinate(4.6, 5.25, 3.0)],
            resource_size_x=127.8,
            resource_size_y=85.5,
            resource_size_z=14.5,
            name_prefix=name,
        ),
        model="AI4C_WellPlateCarrier",
    )
    carrier.num_items_x = 1
    carrier.num_items_y = 1
    carrier.num_items_z = 1
    carrier[0] = AI4C_Well_Plate(f"{name}_well_plate_1")
    return carrier
