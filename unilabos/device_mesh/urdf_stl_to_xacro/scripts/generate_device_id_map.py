from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


SAFE_ID_PATTERN = re.compile(r"[^a-z0-9]+")
GENERIC_SLUGS: set[str] = {
    "urdf",
    "stl",
    "sldasm",
    "asm",
    "part",
    "model",
    "device",
}


@dataclass
class MappingItem:
    source_kind: str
    source_path: str
    raw_name: str
    base_slug: str
    device_id: str
    note: str


def stable_hash(text: str, length: int = 8) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def ascii_slug(text: str, max_len: int = 48) -> str:
    lowered: str = text.strip().lower()
    normalized: str = unicodedata.normalize("NFKD", lowered)
    ascii_text: str = normalized.encode("ascii", "ignore").decode("ascii")
    slug: str = SAFE_ID_PATTERN.sub("_", ascii_text).strip("_")
    if not slug:
        return ""
    if slug[0].isdigit():
        slug = f"dev_{slug}"
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("_")
    return slug


def strip_known_extensions(name: str) -> str:
    value: str = name.strip()
    lowered: str = value.lower()
    for suffix in (".urdf", ".sldasm", ".stl", ".step"):
        if lowered.endswith(suffix):
            value = value[: -len(suffix)]
            lowered = value.lower()
    return value.strip()


def load_inventory(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_source_items(inventory: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for record in inventory.get("urdf_records", []):
        source_path: str = str(record.get("source_urdf", ""))
        raw_name: str = str(record.get("robot_name") or Path(source_path).stem)
        items.append(
            {
                "source_kind": "urdf",
                "source_path": source_path,
                "raw_name": raw_name,
            }
        )

    for stl_rel_path in inventory.get("standalone_stl_records", []):
        source_path = str(stl_rel_path)
        raw_name = Path(source_path).stem
        items.append(
            {
                "source_kind": "stl",
                "source_path": source_path,
                "raw_name": raw_name,
            }
        )

    items.sort(key=lambda entry: (entry["source_kind"], entry["source_path"]))
    return items


def assign_device_ids(items: list[dict[str, str]]) -> list[MappingItem]:
    used: dict[str, int] = {}
    mapped: list[MappingItem] = []

    for item in items:
        source_key: str = f"{item['source_kind']}::{item['source_path']}"
        raw_name_clean: str = strip_known_extensions(item["raw_name"])
        source_path = Path(item["source_path"])

        raw_slug: str = ascii_slug(raw_name_clean)
        parent_slug: str = ascii_slug(strip_known_extensions(source_path.parent.name))
        grand_parent_slug: str = ascii_slug(strip_known_extensions(source_path.parent.parent.name))

        candidate_pool: list[tuple[str, str]] = [
            (raw_slug, "from_raw_name"),
            (parent_slug, "from_parent_path"),
            (grand_parent_slug, "from_grand_parent_path"),
        ]

        base_slug: str = ""
        note: str = ""
        for candidate_slug, candidate_note in candidate_pool:
            if not candidate_slug:
                continue
            if candidate_slug in GENERIC_SLUGS:
                continue
            if len(candidate_slug) < 4:
                continue
            base_slug = candidate_slug
            note = candidate_note
            break

        if not base_slug:
            fallback_slug: str = raw_slug or parent_slug or grand_parent_slug
            if fallback_slug:
                if fallback_slug in GENERIC_SLUGS:
                    base_slug = f"dev_{stable_hash(source_key, 10)}"
                    note = "generic_slug_hash_fallback"
                else:
                    base_slug = f"{fallback_slug}_{stable_hash(source_key, 6)}"
                    note = "weak_slug_hash_enhanced"
            else:
                base_slug = f"dev_{stable_hash(source_key, 10)}"
                note = "empty_ascii_slug_fallback"

        candidate: str = base_slug
        if candidate in used:
            hash_suffix: str = stable_hash(source_key, 6)
            candidate = f"{base_slug}_{hash_suffix}"
            note = f"{note};collision_suffix"

        index: int = used.get(candidate, 0)
        if index > 0:
            candidate = f"{candidate}_{index + 1}"
            note = f"{note};index_suffix"

        used[candidate] = used.get(candidate, 0) + 1

        mapped.append(
            MappingItem(
                source_kind=item["source_kind"],
                source_path=item["source_path"],
                raw_name=item["raw_name"],
                base_slug=base_slug,
                device_id=candidate,
                note=note,
            )
        )
    return mapped


def to_markdown(mapped: list[MappingItem], source_dir: str) -> str:
    lines: list[str] = []
    lines.append("# Device ID Mapping")
    lines.append("")
    lines.append(f"- Source directory: `{source_dir}`")
    lines.append(f"- Generated at: `{datetime.now().isoformat(timespec='seconds')}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    urdf_total: int = sum(1 for item in mapped if item.source_kind == "urdf")
    stl_total: int = sum(1 for item in mapped if item.source_kind == "stl")
    collision_total: int = sum(1 for item in mapped if "collision_suffix" in item.note)
    fallback_total: int = sum(1 for item in mapped if "empty_ascii_slug_fallback" in item.note)
    lines.append(f"- Total items: `{len(mapped)}`")
    lines.append(f"- URDF items: `{urdf_total}`")
    lines.append(f"- STL items: `{stl_total}`")
    lines.append(f"- Collision-resolved IDs: `{collision_total}`")
    lines.append(f"- Hash-fallback IDs: `{fallback_total}`")
    lines.append("")
    lines.append("## Mapping Table")
    lines.append("")
    lines.append("| # | Kind | Raw Name | Source Path | Device ID | Note |")
    lines.append("|---:|---|---|---|---|---|")
    for index, item in enumerate(mapped, start=1):
        note = item.note.replace("|", "/")
        lines.append(
            f"| {index} | {item.source_kind} | {item.raw_name} | "
            f"`{item.source_path}` | `{item.device_id}` | {note} |"
        )
    lines.append("")
    return "\n".join(lines)


def to_json_payload(mapped: list[MappingItem], source_dir: str) -> dict[str, Any]:
    return {
        "source_dir": source_dir,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "items": [
            {
                "source_kind": item.source_kind,
                "source_path": item.source_path,
                "raw_name": item.raw_name,
                "base_slug": item.base_slug,
                "device_id": item.device_id,
                "note": item.note,
            }
            for item in mapped
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate stable ASCII device_id mapping for URDF/STL conversion."
    )
    parser.add_argument("--inventory-json", required=True, help="Inventory JSON from inventory_assets.py")
    parser.add_argument("--md-out", required=False, help="Output markdown mapping report")
    parser.add_argument("--json-out", required=False, help="Output JSON mapping report")
    args = parser.parse_args()

    inventory_path: Path = Path(args.inventory_json).resolve()
    inventory: dict[str, Any] = load_inventory(inventory_path)
    source_dir: str = str(inventory.get("source_dir", ""))

    source_items: list[dict[str, str]] = build_source_items(inventory)
    mapped: list[MappingItem] = assign_device_ids(source_items)

    markdown: str = to_markdown(mapped, source_dir)
    payload: dict[str, Any] = to_json_payload(mapped, source_dir)

    if args.md_out:
        md_out = Path(args.md_out).resolve()
        md_out.parent.mkdir(parents=True, exist_ok=True)
        md_out.write_text(markdown, encoding="utf-8")
    else:
        print(markdown)

    if args.json_out:
        json_out = Path(args.json_out).resolve()
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
