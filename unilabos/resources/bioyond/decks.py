from pylabrobot.resources import Deck, Coordinate, Rotation

from unilabos.resources.bioyond.YB_warehouses import (
    bioyond_warehouse_1x4x4,
    bioyond_warehouse_1x4x4_right,  # 新增：右侧仓库 (A05～D08)
    bioyond_warehouse_1x4x2,
    bioyond_warehouse_reagent_stack,  # 新增：试剂堆栈 (A1-B4)
    bioyond_warehouse_liquid_and_lid_handling,
    bioyond_warehouse_1x2x2,
    bioyond_warehouse_2x2x1,  # 新增：321和43窗口 (2行×2列)
    bioyond_warehouse_1x3x3,
    bioyond_warehouse_5x3x1,  # 手动堆栈左右 (5行×3列)
    bioyond_warehouse_4x2x1,  # 5号右侧手动堆栈 (2行×4列)
    bioyond_warehouse_10x1x1,
    bioyond_warehouse_3x3x1,
    bioyond_warehouse_3x2x1,
    bioyond_warehouse_3x3x1_2,
    bioyond_warehouse_5x1x1,
    bioyond_warehouse_1x8x4,
    bioyond_warehouse_letter_row,
    bioyond_warehouse_1x1,
    bioyond_warehouse_c03_only,
    bioyond_warehouse_reagent_storage,
    # bioyond_warehouse_liquid_preparation,
    bioyond_warehouse_density_vial,
)
from unilabos.resources.bioyond.warehouses import (
    bioyond_warehouse_tipbox_storage_left,   # 新增：Tip盒堆栈(左)
    bioyond_warehouse_tipbox_storage_right,  # 新增：Tip盒堆栈(右)
)


class BIOYOND_PolymerReactionStation_Deck(Deck):
    def __init__(
        self,
        name: str = "PolymerReactionStation_Deck",
        size_x: float = 2700.0,
        size_y: float = 1080.0,
        size_z: float = 1500.0,
        category: str = "deck",
    ) -> None:
        super().__init__(name=name, size_x=2700.0, size_y=1080.0, size_z=1500.0)

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
    ) -> None:
        super().__init__(name=name, size_x=2700.0, size_y=1080.0, size_z=1500.0)

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


class BioyondElectrolyteDeck(Deck):
    def __init__(
        self,
        name: str = "YB_Deck",
        size_x: float = 4150,
        size_y: float = 1400.0,
        size_z: float = 2670.0,
        category: str = "deck",
        setup: bool = False,
    ) -> None:
        super().__init__(name=name, size_x=4150.0, size_y=1400.0, size_z=2670.0)
        if setup:
            self.setup()

    def setup(self) -> None:
        # 添加仓库
        self.warehouses = {
            "自动堆栈-左": bioyond_warehouse_2x2x1("自动堆栈-左"),  # 2行×2列
            "自动堆栈-右": bioyond_warehouse_2x2x1("自动堆栈-右"),  # 2行×2列
            "手动堆栈右": bioyond_warehouse_5x3x1("手动堆栈右", row_offset=0),  # A01-E03
            "5号右侧手动堆栈": bioyond_warehouse_4x2x1("5号右侧手动堆栈"),  # A01-A04, B01-B04
            # LIMS code=0018，电导板配液完成后停在此处，等 conductivity_test_inline
            "5号自动传递窗": bioyond_warehouse_2x2x1("5号自动传递窗"),  # 2行×2列
            "手动堆栈左": bioyond_warehouse_5x3x1("手动堆栈左", row_offset=5),  # F01-J03
            "粉末加样头堆栈左": bioyond_warehouse_letter_row("粉末加样头堆栈左", 10, letter_offset=0),  # A01-J01
            "粉末加样头堆栈右": bioyond_warehouse_letter_row("粉末加样头堆栈右", 10, letter_offset=10),  # K01-T01
            "配液站内试剂仓库": bioyond_warehouse_3x3x1("配液站内试剂仓库"),
            "站内Tip头盒堆栈": bioyond_warehouse_3x2x1("站内Tip头盒堆栈"),  # A01-C02
            "试剂替换仓库左": bioyond_warehouse_letter_row("试剂替换仓库左", 5, letter_offset=0),  # A01-E01
            "试剂替换仓库右": bioyond_warehouse_letter_row("试剂替换仓库右", 5, letter_offset=5),  # F01-J01
            "2号手套箱内部堆栈": bioyond_warehouse_3x3x1("2号手套箱内部堆栈"),
            "1号2号手套箱交接堆栈": bioyond_warehouse_1x1("1号2号手套箱交接堆栈"),
            "大分液瓶堆栈": bioyond_warehouse_3x3x1("大分液瓶堆栈", removed_positions=[8]),  # A01-C02，不含 C03
            "小分液瓶堆栈": bioyond_warehouse_c03_only("小分液瓶堆栈"),  # 仅 C03
            # 试剂替换左上方（从左到右，对齐 A01–D01）
            "预留": bioyond_warehouse_1x1("预留"),
            "分液站内Tip头盒位置库": bioyond_warehouse_1x1("分液站内Tip头盒位置库"),
            "移液站内小瓶板仓库(无需提前入料)": bioyond_warehouse_1x1("移液站内小瓶板仓库(无需提前入料)"),
            "移液站内大瓶板仓库(无需提前如料)": bioyond_warehouse_1x1("移液站内大瓶板仓库(无需提前如料)"),
            # 试剂替换右上方（从左到右，对齐 F01–J01）
            "配液站内Tip头盒位置库": bioyond_warehouse_1x1("配液站内Tip头盒位置库"),
            "配液站内50uLTip盒位置库": bioyond_warehouse_1x1("配液站内50uLTip盒位置库"),
            "配液站内配液大板仓库(无需提前上料)": bioyond_warehouse_1x1("配液站内配液大板仓库(无需提前上料)"),
            "配液站内配液小板仓库(无需以前入料)": bioyond_warehouse_1x1("配液站内配液小板仓库(无需以前入料)"),  # 与大板共用坐标
            "适配器位仓库": bioyond_warehouse_letter_row("适配器位仓库", 2),  # 物理两格，LIMS 仅 A01
        }
        # letter-row / 单点位格宽，用于试剂替换上方对齐
        _slot = 137.0
        _replace_left = 1164.0
        _replace_right = 2717.0
        _overhead_y = 740.0
        # warehouse 的位置
        self.warehouse_locations = {
            "自动堆栈-左": Coordinate(50.0, 1000.0, 0.0),
            "自动堆栈-右": Coordinate(3980.0, 1000.0, 0.0),
            "手动堆栈左": Coordinate(-150.0, 300.0, 0.0),
            "手动堆栈右": Coordinate(4160.0, 300.0, 0.0),
            "5号右侧手动堆栈": Coordinate(4600.0, 300.0, 0.0),  # 随手动堆栈右下移对齐
            "5号自动传递窗": Coordinate(4420.0, 1000.0, 0.0),  # 随自动堆栈-右向左下移
            "粉末加样头堆栈左": Coordinate(385.0, 0, 0.0),
            # 夹在粉末左右之间，底部 y=0 对齐（粉末左右缘 1765，粉末右 2187，居中）
            "站内Tip头盒堆栈": Coordinate(1834.0, 0.0, 0.0),
            "粉末加样头堆栈右": Coordinate(2187.0, 0, 0.0),
            # 原站内Tip 位置，顶栏居中、靠近自动堆栈
            "配液站内试剂仓库": Coordinate(2152.0, 967.0, 0.0),
            "试剂替换仓库左": Coordinate(_replace_left, 624.0, 0.0),
            "试剂替换仓库右": Coordinate(_replace_right, 624.0, 0.0),
            "2号手套箱内部堆栈": Coordinate(-800, 800.0, 0.0),
            # 2号手套箱左侧，单格 A01
            "1号2号手套箱交接堆栈": Coordinate(-800 - 160.0, 800.0, 0.0),
            # 原配液站内试剂仓库位置
            "大分液瓶堆栈": Coordinate(749.0, 945.0, 0.0),
            "小分液瓶堆栈": Coordinate(749.0 + 274.0, 945.0, 0.0),
            "预留": Coordinate(_replace_left, _overhead_y, 0.0),
            "分液站内Tip头盒位置库": Coordinate(_replace_left + _slot, _overhead_y, 0.0),
            "移液站内小瓶板仓库(无需提前入料)": Coordinate(_replace_left + 2 * _slot, _overhead_y, 0.0),
            "移液站内大瓶板仓库(无需提前如料)": Coordinate(_replace_left + 3 * _slot, _overhead_y, 0.0),
            "配液站内Tip头盒位置库": Coordinate(_replace_right, _overhead_y, 0.0),
            "配液站内50uLTip盒位置库": Coordinate(_replace_right + _slot, _overhead_y, 0.0),
            "配液站内配液大板仓库(无需提前上料)": Coordinate(_replace_right + 2 * _slot, _overhead_y, 0.0),
            "配液站内配液小板仓库(无需以前入料)": Coordinate(_replace_right + 2 * _slot, _overhead_y, 0.0),
            "适配器位仓库": Coordinate(_replace_right + 3 * _slot, _overhead_y, 0.0),
        }

        for warehouse_name, warehouse in self.warehouses.items():
            self.assign_child_resource(warehouse, location=self.warehouse_locations[warehouse_name])


# 向后兼容别名，日后废弃
BIOYOND_YB_Deck = BioyondElectrolyteDeck


def bioyond_electrolyte_deck(name: str) -> BioyondElectrolyteDeck:
    deck = BioyondElectrolyteDeck(name=name)
    deck.setup()
    return deck


# 向后兼容别名，日后废弃
def YB_Deck(name: str) -> BioyondElectrolyteDeck:
    return bioyond_electrolyte_deck(name)
