from typing import Optional

from pylabrobot.resources import Coordinate, Deck, Resource

from unilabos.devices.workstation.AI4C.warehouses import (
    AI4C_hplc_station_1x1x1,
    AI4C_loading_rack_1x8x1,
    AI4C_magnetic_stirrer_1x1x1,
    AI4C_pipetting_station_4x4x1,
    AI4C_powder_stack_5x5x1,
    AI4C_solid_weighing_1x1x1,
    AI4C_solid_weighing_powder_1x1x1,
    AI4C_unloading_rack_1x8x1,
)


class AI4C_deck(Deck):
    """AI4C 工作站物料台面。"""

    def __init__(
        self,
        name: str = "AI4C_deck",
        size_x: float = 1217.0,
        size_y: float = 1580.0,
        size_z: float = 2670.0,
        origin: Coordinate = Coordinate(0, 35, 0),
        category: str = "deck",
        setup: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, origin=origin)
        self.warehouses = {}
        self.warehouse_locations = {}
        if setup:
            self.setup()

    def setup(self) -> None:
        # 仓库名称与 AI4C 动作语义保持一致，便于从流程节点追踪真实工位。
        self.warehouses = {
            "孔板上料架": AI4C_loading_rack_1x8x1("孔板上料架"),
            "孔板下料架": AI4C_unloading_rack_1x8x1("孔板下料架"),
            "固态称量粉桶堆栈": AI4C_powder_stack_5x5x1("固态称量粉桶堆栈"),
            "固态称量": AI4C_solid_weighing_1x1x1("固态称量"),
            "固态称量粉桶位": AI4C_solid_weighing_powder_1x1x1("固态称量粉桶位"),
            "移液站": AI4C_pipetting_station_4x4x1("移液站"),
            "磁搅": AI4C_magnetic_stirrer_1x1x1("磁搅"),
            "HPLC工站": AI4C_hplc_station_1x1x1("HPLC工站"),
        }

        # 位置为前端/资源树展示坐标；后续可按实机 CAD 或标定结果微调。
        self.warehouse_locations = {
            "孔板上料架": Coordinate(40.0, 1130.0, 0.0),
            "孔板下料架": Coordinate(40.0, 180.0, 0.0),
            "固态称量粉桶堆栈": Coordinate(245.0, 900.0, 0.0),
            "固态称量": Coordinate(520.0, 1030.0, 0.0),
            "固态称量粉桶位": Coordinate(620.0, 1030.0, 0.0),
            "移液站": Coordinate(740.0, 950.0, 0.0),
            "磁搅": Coordinate(520.0, 610.0, 0.0),
            "HPLC工站": Coordinate(820.0, 610.0, 0.0),
        }

        for warehouse_name, warehouse in self.warehouses.items():
            self.assign_child_resource(warehouse, location=self.warehouse_locations[warehouse_name])

    def assign_child_resource(
        self,
        resource: Resource,
        location: Optional[Coordinate],
        reassign: bool = True,
    ):
        super().assign_child_resource(resource, location, reassign)
        self.warehouses[resource.name] = resource
        self.warehouse_locations[resource.name] = location
