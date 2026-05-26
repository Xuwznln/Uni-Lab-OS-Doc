from os import name

from pylabrobot.resources import Deck, Coordinate, Rotation

from unilabos.registry.decorators import resource
from unilabos.resources.bioyond.YB_warehouses import (
    bioyond_warehouse_1x4x4,
    bioyond_warehouse_1x4x4_right,  # 新增：右侧仓库 (A05～D08)
    bioyond_warehouse_1x4x2,
    bioyond_warehouse_reagent_stack,  # 新增：试剂堆栈 (A1-B4)
    bioyond_warehouse_liquid_and_lid_handling,
    bioyond_warehouse_1x2x2,
    bioyond_warehouse_2x2x1,  # 新增：321和43窗口 (2行×2列)
    bioyond_warehouse_1x3x3,
    bioyond_warehouse_5x3x1,  # 新增：手动传递窗仓库 (5行×3列)
    bioyond_warehouse_10x1x1,
    bioyond_warehouse_3x3x1,
    bioyond_warehouse_3x3x1_2,
    bioyond_warehouse_5x1x1,
    bioyond_warehouse_1x8x4,
    bioyond_warehouse_reagent_storage,
    # bioyond_warehouse_liquid_preparation,
    bioyond_warehouse_density_vial,
)
from unilabos.resources.bioyond.warehouses import (
    bioyond_warehouse_tipbox_storage_left,   # 新增：Tip盒堆栈(左)
    bioyond_warehouse_tipbox_storage_right,  # 新增：Tip盒堆栈(右)
    bioyond_warehouse_sirna_automation_stack,
    bioyond_warehouse_sirna_centrifuge_balance_plate_stack,
    bioyond_warehouse_sirna_g3_liquid_handler,
    bioyond_warehouse_numeric_stack,  # 新增：数字编码堆栈 (用于多肽站)
    bioyond_warehouse_live_grid,
)


class BIOYOND_PolymerReactionStation_Deck(Deck):
    def __init__(
        self,
        name: str = "PolymerReactionStation_Deck",
        size_x: float = 2700.0,
        size_y: float = 1080.0,
        size_z: float = 1500.0,
        category: str = "deck",
        setup: bool = False
    ) -> None:
        super().__init__(name=name, size_x=2700.0, size_y=1080.0, size_z=1500.0)
        if setup:
            self.setup()

    def setup(self) -> None:
        # 添加仓库
        # 说明: 堆栈1物理上分为左右两部分
        #   - 堆栈1左: A01～D04 (4行×4列, 位于反应站左侧)
        #   - 堆栈1右: A05～D08 (4行×4列, 位于反应站右侧)
        self.warehouses = {
            "堆栈1左": bioyond_warehouse_1x4x4("堆栈1左"),  # 左侧堆栈: A01～D04
            "堆栈1右": bioyond_warehouse_1x4x4_right("堆栈1右"),  # 右侧堆栈: A05～D08
            "站内试剂存放堆栈": bioyond_warehouse_reagent_storage("站内试剂存放堆栈"),  # A01～A02
            # "移液站内10%分装液体准备仓库": bioyond_warehouse_liquid_preparation("移液站内10%分装液体准备仓库"),  # A01～B04
            "站内Tip盒堆栈(左)": bioyond_warehouse_tipbox_storage_left("站内Tip盒堆栈(左)"),  # A02～B03
            "站内Tip盒堆栈(右)": bioyond_warehouse_tipbox_storage_right("站内Tip盒堆栈(右)"),  # A01～B01
            "测量小瓶仓库(测密度)": bioyond_warehouse_density_vial("测量小瓶仓库(测密度)"),  # A01～B03
        }
        self.warehouse_locations = {
            "堆栈1左": Coordinate(-200.0, 400.0, 0.0),  # 左侧位置
            "堆栈1右": Coordinate(2350.0, 400.0, 0.0),  # 右侧位置
            "站内试剂存放堆栈": Coordinate(640.0, 400.0, 0.0),
            "站内Tip盒堆栈(左)": Coordinate(300.0, 100.0, 0.0),
            "站内Tip盒堆栈(右)": Coordinate(2250.0, 100.0, 0.0),  # 向右偏移 2 * item_dx (137.0)
            "测量小瓶仓库(测密度)": Coordinate(1000.0, 530.0, 0.0),
        }

        for warehouse_name, warehouse in self.warehouses.items():
            self.assign_child_resource(warehouse, location=self.warehouse_locations[warehouse_name])

class BIOYOND_PolymerPreparationStation_Deck(Deck):
    def __init__(
        self,
        name: str = "PolymerPreparationStation_Deck",
        size_x: float = 2700.0,
        size_y: float = 1080.0,
        size_z: float = 1500.0,
        category: str = "deck",
        setup: bool = False
    ) -> None:
        super().__init__(name=name, size_x=2700.0, size_y=1080.0, size_z=1500.0)
        if setup:
            self.setup()

    def setup(self) -> None:
        # 添加仓库 - 配液站的3个堆栈，使用Bioyond系统中的实际名称
        # 样品类型（typeMode=1）：烧杯、试剂瓶、分装板 → 试剂堆栈、溶液堆栈
        # 试剂类型（typeMode=2）：样品板 → 粉末堆栈
        self.warehouses = {
            # 试剂类型 - 样品板
            "粉末堆栈": bioyond_warehouse_1x4x4("粉末堆栈"),  # 4行×4列 (A01-D04)

            # 样品类型 - 烧杯、试剂瓶、分装板
            "试剂堆栈": bioyond_warehouse_reagent_stack("试剂堆栈"),  # 2行×4列 (A01-B04)
            "溶液堆栈": bioyond_warehouse_1x4x4("溶液堆栈"),  # 4行×4列 (A01-D04)
        }
        self.warehouse_locations = {
            "粉末堆栈": Coordinate(-200.0, 400.0, 0.0),
            "试剂堆栈": Coordinate(1750.0, 160.0, 0.0),
            "溶液堆栈": Coordinate(2350.0, 400.0, 0.0),
        }

        for warehouse_name, warehouse in self.warehouses.items():
            self.assign_child_resource(warehouse, location=self.warehouse_locations[warehouse_name])

@resource(
    id="BIOYOND_SirnaStation_Deck",
    category=["deck"],
    description="BIOYOND 小核酸工作站 Deck",
    icon="配液站.webp",
)
class BIOYOND_SirnaStation_Deck(Deck):
    WAREHOUSE_BIOYOND_AXIS = {
        "G3移液站": "xy_col_row",
        "自动化堆栈": "xy_col_row",
        "离心机配平板堆栈": "xy_col_row",
    }
    WAREHOUSE_BIOYOND_KEY_AXIS = {
        "G3移液站": "col_row",
        "自动化堆栈": "col_row",
        "离心机配平板堆栈": "col_row",
    }
    # Bioyond warehouse UUID -> 本地仓库名称 映射。
    # 留空时由配置（station config 的 ``warehouse_bioyond_ids``）注入。
    # graph 节点也可在 deck.config.warehouse_bioyond_ids 覆盖。
    WAREHOUSE_BIOYOND_IDS: dict = {}

    def __init__(
        self,
        name: str = "SirnaStation_Deck",
        size_x: float = 2700.0,
        size_y: float = 1080.0,
        size_z: float = 1500.0,
        category: str = "deck",
        setup: bool = False,
        warehouse_bioyond_ids: dict | None = None,
        **kwargs,
    ) -> None:
        super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z)
        # 按需写入实例级覆盖；保留默认空 mapping，避免改动模型常量。
        self.warehouse_bioyond_ids: dict = dict(self.WAREHOUSE_BIOYOND_IDS)
        if warehouse_bioyond_ids:
            self.warehouse_bioyond_ids.update(warehouse_bioyond_ids)
        if setup:
            self.setup()

    @classmethod
    def deserialize(cls, data: dict, allow_marshal: bool = False):
        if data.get("children") and data.get("setup") is True:
            data = data.copy()
            data["setup"] = False
        result = super().deserialize(data, allow_marshal=allow_marshal)
        result._ensure_sirna_warehouse_metadata()
        return result

    def _ensure_sirna_warehouse_metadata(self) -> None:
        for child in getattr(self, "children", []):
            name = getattr(child, "name", "")
            axis = self.WAREHOUSE_BIOYOND_AXIS.get(name)
            if axis and not hasattr(child, "bioyond_axis"):
                child.bioyond_axis = axis
            key_axis = self.WAREHOUSE_BIOYOND_KEY_AXIS.get(name)
            if key_axis and not hasattr(child, "bioyond_key_axis"):
                child.bioyond_key_axis = key_axis

    def _frontend_flipped_location(self, resource, display_location: Coordinate) -> Coordinate:
        return Coordinate(
            display_location.x,
            self.get_size_y() - display_location.y - resource.get_size_y(),
            display_location.z,
        )

    def setup(self) -> None:
        # Sirna 读接口 /api/storage/location/locations-by-type 返回完整固定堆栈清单。
        # LIMS 在库物料接口仍使用相同的 自动化堆栈 名称和数字库位编码。
        self.warehouses = {
            "G3移液站": bioyond_warehouse_sirna_g3_liquid_handler(),
            "自动化堆栈": bioyond_warehouse_sirna_automation_stack(),
            "离心机配平板堆栈": bioyond_warehouse_sirna_centrifuge_balance_plate_stack(),
        }
        self.warehouse_locations = {
            "G3移液站": Coordinate(0.0, 0.0, 0.0),
            "自动化堆栈": Coordinate(220.0, 0.0, 0.0),
            "离心机配平板堆栈": Coordinate(1740.0, 0.0, 0.0),
        }

        for warehouse_name, warehouse in self.warehouses.items():
            self.assign_child_resource(
                warehouse,
                location=self._frontend_flipped_location(
                    warehouse,
                    self.warehouse_locations[warehouse_name],
                ),
            )

class BIOYOND_YB_Deck(Deck):
    def __init__(
        self,
        name: str = "YB_Deck",
        size_x: float = 4150,
        size_y: float = 1400.0,
        size_z: float = 2670.0,
        category: str = "deck",
        setup: bool = False
    ) -> None:
        super().__init__(name=name, size_x=4150.0, size_y=1400.0, size_z=2670.0)
        if setup:
            self.setup()

    def setup(self) -> None:
        # 添加仓库
        self.warehouses = {
            "321窗口": bioyond_warehouse_2x2x1("321窗口"),  # 2行×2列
            "43窗口": bioyond_warehouse_2x2x1("43窗口"),  # 2行×2列
            "手动传递窗右": bioyond_warehouse_5x3x1("手动传递窗右", row_offset=0),  # A01-E03
            "手动传递窗左": bioyond_warehouse_5x3x1("手动传递窗左", row_offset=5),  # F01-J03
            "加样头堆栈左": bioyond_warehouse_10x1x1("加样头堆栈左"),
            "加样头堆栈右": bioyond_warehouse_10x1x1("加样头堆栈右"),

            "15ml配液堆栈左": bioyond_warehouse_3x3x1("15ml配液堆栈左"),
            "母液加样右": bioyond_warehouse_3x3x1_2("母液加样右"),
            "大瓶母液堆栈左": bioyond_warehouse_5x1x1("大瓶母液堆栈左"),
            "大瓶母液堆栈右": bioyond_warehouse_5x1x1("大瓶母液堆栈右"),
            "2号手套箱内部堆栈": bioyond_warehouse_3x3x1("2号手套箱内部堆栈"),  # 新增：3行×3列 (A01-C03)
        }
        # warehouse 的位置
        self.warehouse_locations = {
            "321窗口": Coordinate(-150.0, 158.0, 0.0),
            "43窗口": Coordinate(4160.0, 158.0, 0.0),
            "手动传递窗左": Coordinate(-150.0, 877.0, 0.0),
            "手动传递窗右": Coordinate(4160.0, 877.0, 0.0),
            "加样头堆栈左": Coordinate(385.0, 1300.0, 0.0),
            "加样头堆栈右": Coordinate(2187.0, 1300.0, 0.0),

            "15ml配液堆栈左": Coordinate(749.0, 355.0, 0.0),
            "母液加样右": Coordinate(2152.0, 333.0, 0.0),
            "大瓶母液堆栈左": Coordinate(1164.0, 676.0, 0.0),
            "大瓶母液堆栈右": Coordinate(2717.0, 676.0, 0.0),
            "2号手套箱内部堆栈": Coordinate(-800, -500.0, 0.0),  # 新增：位置需根据实际硬件调整
        }

        for warehouse_name, warehouse in self.warehouses.items():
            self.assign_child_resource(warehouse, location=self.warehouse_locations[warehouse_name])

@resource(
    id="BIOYOND_PeptideStation_Deck",
    category=["deck"],
    description="BIOYOND 多肽工作站 Deck",
    icon="preparation_station.webp",
)
class BIOYOND_PeptideStation_Deck(Deck):
    WAREHOUSE_BIOYOND_AXIS = dict.fromkeys(
        [
            "自动化堆栈",
            "低温冰箱仓库",
            "Tecan移液站库",
            "G3移液站库",
            "IDOT移液站库",
            "G3缓冲库",
            "盖板缓冲库",
            "配平板缓冲库",
            "IDOT缓冲库",
            "固相合成板底座缓冲位",
            "离心机库位",
            "热封膜机位",
        ],
        "xy_col_row",
    )
    WAREHOUSE_BIOYOND_KEY_AXIS = dict.fromkeys(WAREHOUSE_BIOYOND_AXIS, "row_col")

    def __init__(
        self,
        name: str = "PeptideStation_Deck",
        size_x: float = 3500.0,
        size_y: float = 1800.0,
        size_z: float = 1500.0,
        category: str = "deck",
        setup: bool = False
    ) -> None:
        super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z)
        if setup:
            self.setup()

    @classmethod
    def deserialize(cls, data: dict, allow_marshal: bool = False):
        if data.get("children") and data.get("setup") is True:
            # 已有序列化子资源，跳过 setup 避免重复创建
            result = super(BIOYOND_PeptideStation_Deck, cls).deserialize(data, allow_marshal=allow_marshal)
        else:
            result = super(BIOYOND_PeptideStation_Deck, cls).deserialize(data, allow_marshal=allow_marshal)
        result._ensure_peptide_warehouse_metadata()
        return result

    def _ensure_peptide_warehouse_metadata(self) -> None:
        for child in getattr(self, "children", []):
            name = getattr(child, "name", "")
            axis = self.WAREHOUSE_BIOYOND_AXIS.get(name)
            if axis and not hasattr(child, "bioyond_axis"):
                child.bioyond_axis = axis
            key_axis = self.WAREHOUSE_BIOYOND_KEY_AXIS.get(name)
            if key_axis and not hasattr(child, "bioyond_key_axis"):
                child.bioyond_key_axis = key_axis

    def setup(self) -> None:
        # 多肽工作站仓库配置
        # 基于 2026-05-09 live API probe 发现的实际仓库拓扑 (12个仓库)
        # 数据来源: temp_benyao/peptide/_logs/warehouse_discovery_raw_live_2026-05-09.json
        self.warehouses = {
            # 主自动化堆栈 - live API: code 10-17 -> x=17, y=10，显示为 10 行×17 列
            "自动化堆栈": bioyond_warehouse_numeric_stack(
                "自动化堆栈", rows=10, columns=17, bioyond_axis="xy_col_row", bioyond_key_axis="row_col"
            ),

            # 低温存储
            "低温冰箱仓库": bioyond_warehouse_live_grid(
                "低温冰箱仓库", rows=2, columns=3, slot_keys=["1", "2", "3", "4", "5", "6"]
            ),

            # 移液站库位
            "Tecan移液站库": bioyond_warehouse_live_grid(
                "Tecan移液站库", rows=1, columns=18, slot_keys=[str(index) for index in range(1, 19)]
            ),
            "G3移液站库": bioyond_warehouse_live_grid(
                "G3移液站库",
                rows=1,
                columns=18,
                slot_keys=["1", "2", "3", "4", "垃圾桶", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15", "16", "17", "18"],
            ),
            "IDOT移液站库": bioyond_warehouse_live_grid(
                "IDOT移液站库",
                rows=1,
                columns=12,
                slot_keys=[f"0009-{index:04d}" for index in range(1, 13)],
            ),

            # 缓冲库位
            "G3缓冲库": bioyond_warehouse_live_grid(
                "G3缓冲库", rows=1, columns=5, slot_keys=[str(index) for index in range(1, 6)]
            ),
            "盖板缓冲库": bioyond_warehouse_live_grid(
                "盖板缓冲库", rows=1, columns=7, slot_keys=[str(index) for index in range(1, 8)]
            ),
            "配平板缓冲库": bioyond_warehouse_live_grid(
                "配平板缓冲库", rows=1, columns=3, slot_keys=[str(index) for index in range(1, 4)]
            ),
            "IDOT缓冲库": bioyond_warehouse_live_grid(
                "IDOT缓冲库", rows=1, columns=2, slot_keys=["1", "1"]
            ),
            "固相合成板底座缓冲位": bioyond_warehouse_live_grid(
                "固相合成板底座缓冲位",
                rows=1,
                columns=4,
                slot_keys=[f"0015-{index:04d}" for index in range(1, 5)],
            ),

            # 设备库位
            "离心机库位": bioyond_warehouse_live_grid(
                "离心机库位", rows=1, columns=4, slot_keys=[f"0017-{index:04d}" for index in range(1, 5)]
            ),
            "热封膜机位": bioyond_warehouse_live_grid(
                "热封膜机位", rows=1, columns=2, slot_keys=[f"0016-{index:04d}" for index in range(1, 3)]
            ),
        }

        # 仓库位置布局 (需根据实际硬件布局调整)
        self.warehouse_locations = {
            "自动化堆栈": Coordinate(0.0, 0.0, 0.0),
            "Tecan移液站库": Coordinate(0.0, 1150.0, 0.0),
            "G3移液站库": Coordinate(0.0, 1300.0, 0.0),
            "IDOT移液站库": Coordinate(0.0, 1450.0, 0.0),
            "G3缓冲库": Coordinate(0.0, 1600.0, 0.0),
            "盖板缓冲库": Coordinate(850.0, 1600.0, 0.0),
            "低温冰箱仓库": Coordinate(2700.0, 0.0, 0.0),
            "配平板缓冲库": Coordinate(2700.0, 300.0, 0.0),
            "IDOT缓冲库": Coordinate(2700.0, 450.0, 0.0),
            "固相合成板底座缓冲位": Coordinate(2700.0, 600.0, 0.0),
            "离心机库位": Coordinate(2700.0, 750.0, 0.0),
            "热封膜机位": Coordinate(2700.0, 900.0, 0.0),
        }

        for warehouse_name, warehouse in self.warehouses.items():
            self.assign_child_resource(warehouse, location=self.warehouse_locations[warehouse_name])

def YB_Deck(name: str) -> Deck:
    by=BIOYOND_YB_Deck(name=name)
    by.setup()
    return by
