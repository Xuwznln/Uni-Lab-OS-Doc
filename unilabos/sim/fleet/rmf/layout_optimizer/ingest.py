"""读取 layout-optimizer 目录产物（#18 §9.1 / #21 §1）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class LayoutOptimizerArtifacts:
    """layout-optimizer 一次管线输出的 JSON 集合。"""

    directory: Path
    lab: Dict[str, Any] = field(default_factory=dict)
    placements: List[Dict[str, Any]] = field(default_factory=list)
    aisle_network: Dict[str, Any] = field(default_factory=dict)
    transfers_doc: Dict[str, Any] = field(default_factory=dict)
    flow_matrix: Dict[str, Any] = field(default_factory=dict)
    dock_and_turn: Dict[str, Any] = field(default_factory=dict)

    @property
    def transfers(self) -> List[Dict[str, Any]]:
        return list(self.transfers_doc.get("transfers") or [])

    @property
    def transfers_meta(self) -> Dict[str, Any]:
        return dict(self.transfers_doc.get("meta") or {})

    @property
    def lab_origin(self) -> tuple[float, float]:
        lab_obj = self.lab.get("lab") or {}
        origin = lab_obj.get("origin") or [0.0, 0.0]
        return float(origin[0]), float(origin[1])

    @property
    def source_scene(self) -> str:
        meta = self.lab.get("_meta") or {}
        return str(meta.get("source_scene") or self.directory.name)


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_layout_optimizer_dir(directory: str | Path) -> LayoutOptimizerArtifacts:
    """加载 layout-optimizer 输出目录中的标准 JSON 文件。"""
    root = Path(directory).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"layout-optimizer 目录不存在: {root}")

    lab = _read_json(root / "lab.json") or {}
    placements = _read_json(root / "placements.json") or []
    aisle_network = _read_json(root / "aisle_network.json") or {}
    transfers_doc = _read_json(root / "transfers.json") or {"meta": {}, "transfers": []}
    flow_matrix = _read_json(root / "flow_matrix.json") or {}
    dock_and_turn = _read_json(root / "dock_and_turn.json") or {}

    if not isinstance(placements, list):
        raise ValueError("placements.json 必须是数组")
    if not isinstance(transfers_doc, dict):
        raise ValueError("transfers.json 必须是对象")

    return LayoutOptimizerArtifacts(
        directory=root,
        lab=lab,
        placements=placements,
        aisle_network=aisle_network,
        transfers_doc=transfers_doc,
        flow_matrix=flow_matrix,
        dock_and_turn=dock_and_turn,
    )
