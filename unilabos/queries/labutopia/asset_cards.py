from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from unilabos.queries.models import Pose, QueryAffordance, SafetyZone, State


def _as_float_list(value: Any) -> List[float]:
    if not isinstance(value, list):
        return []
    result: List[float] = []
    for item in value:
        try:
            result.append(float(item))
        except (TypeError, ValueError):
            return []
    return result


def _center_from_bounds(card: Dict[str, Any]) -> List[float]:
    geometry = card.get("geometry") or {}
    bbox_min = _as_float_list(geometry.get("bbox_min"))
    bbox_max = _as_float_list(geometry.get("bbox_max"))
    if len(bbox_min) == 3 and len(bbox_max) == 3:
        return [(bbox_min[i] + bbox_max[i]) / 2.0 for i in range(3)]
    return []


def _size_from_card(card: Dict[str, Any]) -> List[float]:
    return _as_float_list((card.get("geometry") or {}).get("bbox_size_m_approx"))


def _candidate_action(kind: str) -> str:
    mapping = {
        "button": "press_button",
        "pressable": "press_button",
        "door": "open_lid",
        "articulated": "open_lid",
        "container": "pour",
        "liquid_holder": "pour",
        "beaker": "pour",
        "support_surface": "move_to",
        "bench": "move_to",
    }
    return mapping.get(kind, kind)


class LabUtopiaAssetCardSource:
    name = "labutopia_asset_cards"

    def __init__(self, cards: Iterable[Dict[str, Any]], source_path: Optional[str] = None):
        self.source_path = source_path
        self._cards_by_id: Dict[str, Dict[str, Any]] = {}
        self._cards_by_prim: Dict[str, Dict[str, Any]] = {}
        for card in cards:
            asset_id = str(card.get("asset_id") or card.get("id") or "")
            if asset_id:
                self._cards_by_id[asset_id] = card
            prim_path = str(((card.get("source") or {}).get("prim_path")) or "")
            if prim_path:
                self._cards_by_prim[prim_path] = card

    @classmethod
    def from_directory(cls, directory: str | Path) -> "LabUtopiaAssetCardSource":
        path = Path(directory)
        cards = []
        for card_path in sorted(path.glob("*.json")):
            if card_path.name == "summary.json":
                continue
            cards.append(json.loads(card_path.read_text(encoding="utf-8")))
        return cls(cards, source_path=str(path))

    def _get_card(self, target: str) -> Optional[Dict[str, Any]]:
        return self._cards_by_id.get(target) or self._cards_by_prim.get(target)

    def query_pose(self, target: str, frame: Optional[str] = None) -> Optional[Pose]:
        card = self._get_card(target)
        if card is None:
            return None
        center = _center_from_bounds(card)
        if len(center) != 3:
            return None
        return Pose(
            xyz=center,
            frame_id=frame or "labutopia_world",
            source=self.name,
            metadata={
                "asset_id": card.get("asset_id"),
                "prim_path": (card.get("source") or {}).get("prim_path"),
                "bbox_size": _size_from_card(card),
                "source_path": self.source_path,
            },
        )

    def query_state(self, target: str) -> Optional[State]:
        card = self._get_card(target)
        if card is None:
            return None
        return State(
            name=str(card.get("asset_id", target)),
            values={
                "asset_class_tags": list(card.get("asset_class_tags") or []),
                "affordances": list(card.get("affordances") or []),
                "candidate_tasks": list(card.get("candidate_tasks") or []),
                "geometry": dict(card.get("geometry") or {}),
                "psb_semantics": dict(card.get("psb_semantics") or {}),
                "source": dict(card.get("source") or {}),
            },
            source=self.name,
        )

    def query_affordance(self, target: str, kind: Optional[str] = None) -> List[QueryAffordance]:
        card = self._get_card(target)
        if card is None:
            return []
        pose = self.query_pose(target)
        result: List[QueryAffordance] = []
        for item in card.get("affordances") or []:
            item_kind = str(item)
            if kind is not None and item_kind != kind:
                continue
            result.append(
                QueryAffordance(
                    id=item_kind,
                    kind=item_kind,
                    pose=pose,
                    action_primitives=[_candidate_action(item_kind)],
                    target=str((card.get("source") or {}).get("prim_path") or card.get("asset_id")),
                    metadata={
                        "asset_id": card.get("asset_id"),
                        "candidate_tasks": list(card.get("candidate_tasks") or []),
                        "operation_hints": dict(card.get("operation_hints") or {}),
                        "needs_manual_verification": bool((card.get("metadata") or {}).get("needs_manual_verification", True)),
                    },
                )
            )
        return result

    def query_action_schema(self, action: str):
        return None

    def query_safety_zones(self) -> List[SafetyZone]:
        zones: List[SafetyZone] = []
        for asset_id, card in self._cards_by_id.items():
            size = _size_from_card(card)
            center = _center_from_bounds(card)
            if len(size) != 3 or len(center) != 3:
                continue
            tags = set(card.get("asset_class_tags") or [])
            zone_type = "workspace" if tags & {"bench", "support_surface"} else "collision"
            zones.append(
                SafetyZone(
                    id=f"{asset_id}.bbox",
                    zone_type=zone_type,
                    frame_id="labutopia_world",
                    bbox_center=center,
                    bbox_size=size,
                    source=self.name,
                    metadata={
                        "asset_id": asset_id,
                        "prim_path": (card.get("source") or {}).get("prim_path"),
                    },
                )
            )
        return zones
