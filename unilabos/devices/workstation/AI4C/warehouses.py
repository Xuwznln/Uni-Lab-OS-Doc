from unilabos.devices.workstation.AI4C.AI4C_warehouse import WareHouse, warehouse_factory


def AI4C_loading_rack_1x8x1(name: str) -> WareHouse:
    """创建 AI4C 孔板上料架，机械臂位置编号 1-8。"""
    return warehouse_factory(
        name=name,
        num_items_x=1,
        num_items_y=8,
        num_items_z=1,
        dx=10.0,
        dy=10.0,
        dz=10.0,
        item_dx=137.0,
        item_dy=96.0,
        item_dz=120.0,
        custom_keys=[str(i) for i in range(1, 9)],
        category="warehouse",
        model="AI4C_loading_rack_1x8x1",
    )


def AI4C_unloading_rack_1x8x1(name: str) -> WareHouse:
    """创建 AI4C 孔板下料架，机械臂位置编号 1-8。"""
    return warehouse_factory(
        name=name,
        num_items_x=1,
        num_items_y=8,
        num_items_z=1,
        dx=10.0,
        dy=10.0,
        dz=10.0,
        item_dx=137.0,
        item_dy=96.0,
        item_dz=120.0,
        custom_keys=[str(i) for i in range(1, 9)],
        category="warehouse",
        model="AI4C_unloading_rack_1x8x1",
    )


def AI4C_powder_stack_5x5x1(name: str) -> WareHouse:
    """创建 AI4C 固态称量粉桶堆栈，机械臂位置编号 1-25。"""
    return warehouse_factory(
        name=name,
        num_items_x=5,
        num_items_y=5,
        num_items_z=1,
        dx=10.0,
        dy=10.0,
        dz=10.0,
        item_dx=80.0,
        item_dy=80.0,
        item_dz=120.0,
        resource_size_x=70.0,
        resource_size_y=70.0,
        resource_size_z=100.0,
        naming_mode="continuous_number",
        category="warehouse",
        model="AI4C_powder_stack_5x5x1",
    )


def AI4C_solid_weighing_1x1x1(name: str) -> WareHouse:
    """创建 AI4C 固态称量位。"""
    return warehouse_factory(
        name=name,
        num_items_x=1,
        num_items_y=1,
        num_items_z=1,
        custom_keys=["Solid_Weighing"],
        category="warehouse",
        model="AI4C_solid_weighing_1x1x1",
    )


def AI4C_solid_weighing_powder_1x1x1(name: str) -> WareHouse:
    """创建 AI4C 固态称量粉桶位。"""
    return warehouse_factory(
        name=name,
        num_items_x=1,
        num_items_y=1,
        num_items_z=1,
        custom_keys=["Powder_In_Solid_Weighing"],
        category="warehouse",
        model="AI4C_solid_weighing_powder_1x1x1",
    )


def AI4C_pipetting_station_4x4x1(name: str) -> WareHouse:
    """创建 AI4C 移液站板位，机械臂位置编号 1-16。"""
    return warehouse_factory(
        name=name,
        num_items_x=4,
        num_items_y=4,
        num_items_z=1,
        dx=10.0,
        dy=10.0,
        dz=10.0,
        item_dx=137.0,
        item_dy=96.0,
        item_dz=120.0,
        custom_keys=[str(i) for i in range(1, 17)],
        category="warehouse",
        model="AI4C_pipetting_station_4x4x1",
    )


def AI4C_magnetic_stirrer_1x1x1(name: str) -> WareHouse:
    """创建 AI4C 磁搅位。"""
    return warehouse_factory(
        name=name,
        num_items_x=1,
        num_items_y=1,
        num_items_z=1,
        custom_keys=["Magnetic_Stirrer"],
        category="warehouse",
        model="AI4C_magnetic_stirrer_1x1x1",
    )


def AI4C_hplc_station_1x1x1(name: str) -> WareHouse:
    """创建 AI4C HPLC 工站位。"""
    return warehouse_factory(
        name=name,
        num_items_x=1,
        num_items_y=1,
        num_items_z=1,
        custom_keys=["HPLC"],
        category="warehouse",
        model="AI4C_hplc_station_1x1x1",
    )
