"""Layout Optimizer — AI 实验室布局自动排布。

独立开发包，无 ROS 依赖。集成阶段合并到 Uni-Lab-OS。
"""

from .models import Constraint, Device, Lab, Opening, Placement
from .optimizer import optimize
from .rail_layout import (
    DEFAULT_PARAMS,
    DEFAULT_WORKING_RADIUS,
    RailParams,
    check_feasibility,
    place_arms_and_stacks,
    assign_and_place_instruments,
    validate_placements,
)

__all__ = [
    "Device",
    "Lab",
    "Opening",
    "Placement",
    "Constraint",
    "optimize",
    "DEFAULT_PARAMS",
    "DEFAULT_WORKING_RADIUS",
    "RailParams",
    "check_feasibility",
    "place_arms_and_stacks",
    "assign_and_place_instruments",
    "validate_placements",
]
