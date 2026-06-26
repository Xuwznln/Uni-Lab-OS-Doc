"""layout-optimizer 产物 → RMF 中间表示（#18 §9 / #21）。

读取 `placements.json` / `aisle_network.json` / `transfers.json` / `lab.json`，
产出 `RmfTransferPlan` 与编译 `RmfMapIR` 所需的结构化 dict。不依赖 uni-lab-designer 包。
"""

from unilabos.sim.fleet.rmf.layout_optimizer.ingest import LayoutOptimizerArtifacts, load_layout_optimizer_dir
from unilabos.sim.fleet.rmf.layout_optimizer.transfer_plan_builder import build_transfer_plan
from unilabos.sim.fleet.rmf.layout_optimizer.slug import build_instance_waypoint_map

__all__ = [
    "LayoutOptimizerArtifacts",
    "load_layout_optimizer_dir",
    "build_transfer_plan",
    "build_instance_waypoint_map",
]
