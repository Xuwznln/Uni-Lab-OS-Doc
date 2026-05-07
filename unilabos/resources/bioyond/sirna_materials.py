"""Sirna Station Material Resource Definitions

Defines PyLabRobot resource classes for Bioyond Sirna station materials.
Each class is decorated with @resource for AST-based registry discovery.
"""

from typing import Optional
from collections import OrderedDict
from pylabrobot.resources import Plate, TipRack, Container

from unilabos.registry.decorators import resource


@resource(
    id="bioyond_sirna_g3_200ul_tip_rack",
    category=["labware", "tip_rack"],
    description="G3-200ul枪头盒 for Sirna station",
)
class BioyondSirna_G3_200ul_TipRack(TipRack):
    """G3-200ul tip rack for Sirna liquid handling."""

    def __init__(
        self,
        name: str,
        with_tips: bool = True,
    ):
        super().__init__(
            name=name,
            size_x=127.76,
            size_y=85.48,
            size_z=64.0,
            model="bioyond_sirna_g3_200ul_tip_rack",
            with_tips=with_tips,
            ordering=OrderedDict(),  # Empty ordering to satisfy PyLabRobot requirement
        )


@resource(
    id="bioyond_sirna_g3_50ul_tip_rack",
    category=["labware", "tip_rack"],
    description="G3-50ul枪头盒 for Sirna station",
)
class BioyondSirna_G3_50ul_TipRack(TipRack):
    """G3-50ul tip rack for Sirna liquid handling."""

    def __init__(
        self,
        name: str,
        with_tips: bool = True,
    ):
        super().__init__(
            name=name,
            size_x=127.76,
            size_y=85.48,
            size_z=64.0,
            model="bioyond_sirna_g3_50ul_tip_rack",
            with_tips=with_tips,
            ordering=OrderedDict(),  # Empty ordering to satisfy PyLabRobot requirement
        )


@resource(
    id="bioyond_sirna_384_well_plate",
    category=["labware", "plate"],
    description="384孔板 for Sirna assays",
)
class BioyondSirna_384WellPlate(Plate):
    """384-well plate for Sirna reporter gene detection."""

    def __init__(
        self,
        name: str,
        lid: Optional[object] = None,
    ):
        super().__init__(
            name=name,
            size_x=127.76,
            size_y=85.48,
            size_z=14.35,
            lid=lid,
            model="bioyond_sirna_384_well_plate",
            plate_type="skirted",
        )


@resource(
    id="bioyond_sirna_cell_culture_plate",
    category=["labware", "plate"],
    description="细胞培养板 for Sirna cell culture",
)
class BioyondSirna_CellCulturePlate(Plate):
    """Cell culture plate for Sirna experiments."""

    def __init__(
        self,
        name: str,
        lid: Optional[object] = None,
    ):
        super().__init__(
            name=name,
            size_x=127.76,
            size_y=85.48,
            size_z=14.35,
            lid=lid,
            model="bioyond_sirna_cell_culture_plate",
            plate_type="skirted",
        )


@resource(
    id="bioyond_sirna_reagent_trough",
    category=["labware", "trough"],
    description="试剂槽 for Sirna reagents",
)
class BioyondSirna_ReagentTrough(Container):
    """Reagent trough for Sirna station reagents (RiboGreen, etc.)."""

    def __init__(
        self,
        name: str,
        max_volume: float = 300000.0,  # 300mL default
    ):
        super().__init__(
            name=name,
            size_x=127.76,
            size_y=85.48,
            size_z=44.0,
            max_volume=max_volume,
            model="bioyond_sirna_reagent_trough",
        )


# Material type code mapping for dynamic instantiation
MATERIAL_TYPE_CODE_TO_CLASS = {
    "0016": BioyondSirna_G3_200ul_TipRack,
    "0017": BioyondSirna_G3_50ul_TipRack,
    "0015": BioyondSirna_384WellPlate,
    "0001": BioyondSirna_CellCulturePlate,
    "0006": BioyondSirna_ReagentTrough,
}


def get_material_class_by_type_code(type_code: str):
    """Get resource class by Bioyond material type code.

    Args:
        type_code: Bioyond materialTypeCode (e.g., "0016", "0017")

    Returns:
        Resource class or None if not found
    """
    return MATERIAL_TYPE_CODE_TO_CLASS.get(type_code)
