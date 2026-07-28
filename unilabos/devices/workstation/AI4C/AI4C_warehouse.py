from typing import Dict, List, Optional, Union

from pylabrobot.resources import Coordinate
from pylabrobot.resources.carrier import ResourceHolder, create_homogeneous_resources

from unilabos.resources.itemized_carrier import ItemizedCarrier, ResourcePLR


LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def warehouse_factory(
    name: str,
    num_items_x: int = 1,
    num_items_y: int = 1,
    num_items_z: int = 1,
    dx: float = 10.0,
    dy: float = 10.0,
    dz: float = 10.0,
    item_dx: float = 137.0,
    item_dy: float = 96.0,
    item_dz: float = 120.0,
    resource_size_x: float = 127.8,
    resource_size_y: float = 85.5,
    resource_size_z: float = 25.0,
    removed_positions: Optional[List[int]] = None,
    category: str = "warehouse",
    model: Optional[str] = None,
    col_offset: int = 0,
    layout: str = "row-major",
    custom_keys: Optional[List[Union[str, int]]] = None,
    naming_mode: str = "continuous_number",
    reverse_col_order: bool = False,
):
    """创建 AI4C 仓库槽位，槽位顺序与机械臂位置编号保持一致。"""
    locations = []

    for layer in range(num_items_z):
        for row in range(num_items_y):
            for col in range(num_items_x):
                x = dx + col * item_dx
                y = dy + row * item_dy if layout == "row-major" else dy + (num_items_y - row - 1) * item_dy
                z = dz + (num_items_z - layer - 1) * item_dz
                locations.append(Coordinate(x, y, z))

    if removed_positions:
        locations = [loc for i, loc in enumerate(locations) if i not in removed_positions]

    _sites = create_homogeneous_resources(
        klass=ResourceHolder,
        locations=locations,
        resource_size_x=resource_size_x,
        resource_size_y=resource_size_y,
        resource_size_z=resource_size_z,
        name_prefix=name,
    )

    if custom_keys:
        keys = [str(k) for k in custom_keys]
        if len(keys) != len(_sites):
            raise ValueError(f"自定义键名数量({len(keys)})与位置数量({len(_sites)})不匹配")
    elif naming_mode == "letter_number":
        keys = []
        for layer in range(num_items_z):
            for row in range(num_items_y):
                letter = LETTERS[layer * num_items_y + row]
                row_keys = [f"{letter}{col + 1 + col_offset}" for col in range(num_items_x)]
                keys.extend(reversed(row_keys) if reverse_col_order else row_keys)
    else:
        keys = []
        counter = 1 + col_offset
        for _layer in range(num_items_z):
            for _row in range(num_items_y):
                row_keys = []
                for _col in range(num_items_x):
                    row_keys.append(str(counter))
                    counter += 1
                keys.extend(reversed(row_keys) if reverse_col_order else row_keys)

    sites = {key: site for key, site in zip(keys, _sites.values())}

    return WareHouse(
        name=name,
        size_x=dx + item_dx * max(num_items_x, 1),
        size_y=dy + item_dy * max(num_items_y, 1),
        size_z=dz + item_dz * max(num_items_z, 1),
        num_items_x=num_items_x,
        num_items_y=num_items_y,
        num_items_z=num_items_z,
        ordering_layout=layout,
        sites=sites,
        category=category,
        model=model,
    )


class WareHouse(ItemizedCarrier):
    """AI4C 仓库载具，用于描述机械臂可取放的固定位置集合。"""

    def __init__(
        self,
        name: str,
        size_x: float,
        size_y: float,
        size_z: float,
        num_items_x: int,
        num_items_y: int,
        num_items_z: int,
        layout: str = "x-y",
        sites: Optional[Dict[Union[int, str], Optional[ResourcePLR]]] = None,
        category: str = "warehouse",
        model: Optional[str] = None,
        ordering_layout: str = "row-major",
        **kwargs,
    ):
        super().__init__(
            name=name,
            size_x=size_x,
            size_y=size_y,
            size_z=size_z,
            num_items_x=num_items_x,
            num_items_y=num_items_y,
            num_items_z=num_items_z,
            layout=layout,
            sites=sites,
            category=category,
            model=model,
        )
        self.ordering_layout = ordering_layout

    def serialize(self) -> dict:
        data = super().serialize()
        data["ordering_layout"] = self.ordering_layout
        return data

    def get_site_by_layer_position(self, row: int, col: int, layer: int) -> ResourceHolder:
        if not (0 <= layer < self.num_items_z and 0 <= row < self.num_items_y and 0 <= col < self.num_items_x):
            raise ValueError(f"无效的位置: layer={layer}, row={row}, col={col}")

        site_index = layer * self.num_items_x * self.num_items_y + row * self.num_items_x + col
        return self.sites[site_index]

    def add_rack_to_position(self, row: int, col: int, layer: int, rack) -> None:
        site = self.get_site_by_layer_position(row, col, layer)
        site.assign_child_resource(rack)

    def get_rack_at_position(self, row: int, col: int, layer: int):
        site = self.get_site_by_layer_position(row, col, layer)
        return site.resource
