from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.dom import minidom


XACRO_NS = "http://www.ros.org/wiki/xacro"
ET.register_namespace("xacro", XACRO_NS)

MOVABLE_JOINT_TYPES: set[str] = {
    "revolute",
    "continuous",
    "prismatic",
    "planar",
    "floating",
}
SAFE_TOKEN_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass
class ConversionFailure:
    source_kind: str
    source_path: str
    device_id: str
    error: str


def stable_hash(text: str, length: int = 8) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def ascii_token(text: str, prefix: str = "name", max_len: int = 48) -> str:
    lowered: str = text.strip().lower()
    normalized: str = unicodedata.normalize("NFKD", lowered)
    ascii_text: str = normalized.encode("ascii", "ignore").decode("ascii")
    token: str = SAFE_TOKEN_PATTERN.sub("_", ascii_text).strip("_")
    if not token:
        token = f"{prefix}_{stable_hash(text, 6)}"
    if token[0].isdigit():
        token = f"{prefix}_{token}"
    if len(token) > max_len:
        token = token[:max_len].rstrip("_")
    return token


def local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def normalize_rel_path(path: Path, base: Path) -> str:
    return str(path.resolve().relative_to(base.resolve())).replace("\\", "/")


def pretty_xml(element: ET.Element) -> str:
    xml_bytes = ET.tostring(element, encoding="utf-8")
    parsed = minidom.parseString(xml_bytes)
    return parsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def find_package_root(urdf_path: Path) -> Path | None:
    current = urdf_path.parent
    while True:
        if (current / "package.xml").exists():
            return current
        if current == current.parent:
            return None
        current = current.parent


def resolve_mesh_reference(mesh_ref: str, urdf_path: Path, package_root: Path | None) -> Path:
    raw_ref = mesh_ref.strip()
    if raw_ref.startswith("package://"):
        body = raw_ref[len("package://") :]
        _, _, rel = body.partition("/")
        if not rel:
            raise ValueError(f"Invalid package mesh ref: {mesh_ref}")
        if package_root is not None:
            return (package_root / rel).resolve()
        return (urdf_path.parent / rel).resolve()

    if raw_ref.startswith("file://"):
        file_path = raw_ref[len("file://") :]
        if len(file_path) >= 3 and file_path[0] == "/" and file_path[2] == ":":
            file_path = file_path[1:]
        return Path(file_path).resolve()

    return (urdf_path.parent / raw_ref).resolve()


class MeshCopier:
    def __init__(self, device_id: str, dest_mesh_dir: Path, urdf_path: Path | None = None) -> None:
        self.device_id = device_id
        self.dest_mesh_dir = dest_mesh_dir
        self.urdf_path = urdf_path
        self.package_root = find_package_root(urdf_path) if urdf_path is not None else None
        self.by_source: dict[str, str] = {}
        self.by_target: dict[str, str] = {}

    def _target_name(self, source_path: Path) -> str:
        stem = ascii_token(source_path.stem, prefix="mesh", max_len=56)
        suffix = source_path.suffix.lower() or ".stl"
        if suffix not in {".stl", ".dae", ".obj"}:
            suffix = ".stl"
        candidate = f"{stem}{suffix}"
        if candidate in self.by_target and self.by_target[candidate] != source_path.as_posix().lower():
            candidate = f"{stem}_{stable_hash(source_path.as_posix().lower(), 6)}{suffix}"
        return candidate

    def copy_from_urdf_ref(self, mesh_ref: str) -> str:
        if self.urdf_path is None:
            raise RuntimeError("MeshCopier is not configured with urdf_path")
        source_path = resolve_mesh_reference(mesh_ref, self.urdf_path, self.package_root)
        source_key = source_path.as_posix().lower()
        if source_key in self.by_source:
            return self.by_source[source_key]
        if not source_path.exists():
            raise FileNotFoundError(f"Missing mesh file: {source_path.as_posix()}")
        ensure_dir(self.dest_mesh_dir)
        target_name = self._target_name(source_path)
        target_path = self.dest_mesh_dir / target_name
        shutil.copy2(source_path, target_path)
        self.by_source[source_key] = target_name
        self.by_target[target_name] = source_key
        return target_name

    def copy_standalone_stl(self, stl_path: Path) -> str:
        source_path = stl_path.resolve()
        source_key = source_path.as_posix().lower()
        if source_key in self.by_source:
            return self.by_source[source_key]
        if not source_path.exists():
            raise FileNotFoundError(f"Missing STL file: {source_path.as_posix()}")
        ensure_dir(self.dest_mesh_dir)
        target_name = self._target_name(source_path)
        target_path = self.dest_mesh_dir / target_name
        shutil.copy2(source_path, target_path)
        self.by_source[source_key] = target_name
        self.by_target[target_name] = source_key
        return target_name


def build_mount_structure(macro: ET.Element, root_link_expr: str) -> None:
    mount_joint = ET.SubElement(
        macro,
        "joint",
        {"name": "${station_name}${device_name}base_link_joint", "type": "fixed"},
    )
    ET.SubElement(
        mount_joint,
        "origin",
        {"xyz": "${x} ${y} ${z}", "rpy": "${rx} ${ry} ${r}"},
    )
    ET.SubElement(mount_joint, "parent", {"link": "${parent_link}"})
    ET.SubElement(mount_joint, "child", {"link": "${station_name}${device_name}device_link"})
    ET.SubElement(mount_joint, "axis", {"xyz": "0 0 0"})

    ET.SubElement(macro, "link", {"name": "${station_name}${device_name}device_link"})
    bridge_joint = ET.SubElement(
        macro,
        "joint",
        {"name": "${station_name}${device_name}device_link_joint", "type": "fixed"},
    )
    ET.SubElement(bridge_joint, "origin", {"xyz": "0 0 0", "rpy": "0 0 0"})
    ET.SubElement(bridge_joint, "parent", {"link": "${station_name}${device_name}device_link"})
    ET.SubElement(bridge_joint, "child", {"link": root_link_expr})
    ET.SubElement(bridge_joint, "axis", {"xyz": "0 0 0"})


def prefixed_name(name: str) -> str:
    return f"${{station_name}}${{device_name}}{name}"


def deep_copy(element: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(element, encoding="unicode"))


def rewrite_meshes_in_element(
    element: ET.Element,
    mesh_copier: MeshCopier,
    device_id: str,
) -> None:
    for mesh_node in element.findall(".//mesh"):
        mesh_ref = mesh_node.attrib.get("filename", "").strip()
        if not mesh_ref:
            continue
        mesh_file = mesh_copier.copy_from_urdf_ref(mesh_ref)
        mesh_node.set(
            "filename",
            f"file://${{mesh_path}}/devices/{device_id}/meshes/{mesh_file}",
        )


def build_unique_name_map(raw_names: list[str], prefix: str) -> dict[str, str]:
    mapped: dict[str, str] = {}
    used: set[str] = set()
    for index, raw_name in enumerate(raw_names, start=1):
        source_name = raw_name.strip() if raw_name else ""
        base = ascii_token(source_name or f"{prefix}_{index}", prefix=prefix)
        candidate = base
        serial = 2
        while candidate in used:
            candidate = f"{base}_{serial}"
            serial += 1
        used.add(candidate)
        mapped[raw_name] = candidate
    return mapped


def find_root_link(link_names: list[str], joint_nodes: list[ET.Element]) -> str:
    child_links: set[str] = set()
    for joint in joint_nodes:
        child = joint.find("child")
        if child is None:
            continue
        child_name = child.attrib.get("link", "").strip()
        if child_name:
            child_links.add(child_name)
    for name in link_names:
        if name not in child_links:
            return name
    return link_names[0]


def write_meta_json(
    path: Path,
    payload: dict[str, Any],
) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def convert_urdf_device(
    source_dir: Path,
    source_rel_path: str,
    device_id: str,
    output_dir: Path,
) -> dict[str, Any]:
    source_urdf = (source_dir / source_rel_path).resolve()
    if not source_urdf.exists():
        raise FileNotFoundError(f"URDF not found: {source_urdf.as_posix()}")

    tree = ET.parse(source_urdf)
    root = tree.getroot()

    link_nodes = root.findall("link")
    joint_nodes = root.findall("joint")
    if not link_nodes:
        raise ValueError("URDF contains no <link> nodes")

    link_names = [node.attrib.get("name", "").strip() for node in link_nodes]
    if any(not name for name in link_names):
        raise ValueError("URDF contains unnamed link")
    root_link = find_root_link(link_names, joint_nodes)

    link_name_map = build_unique_name_map(link_names, prefix="link")
    joint_names = [node.attrib.get("name", "").strip() for node in joint_nodes]
    for index, name in enumerate(joint_names):
        if not name:
            joint_names[index] = f"joint_{index + 1}"
    joint_name_map = build_unique_name_map(joint_names, prefix="joint")

    device_dir = output_dir / device_id
    mesh_dir = device_dir / "meshes"
    ensure_dir(mesh_dir)
    mesh_copier = MeshCopier(device_id=device_id, dest_mesh_dir=mesh_dir, urdf_path=source_urdf)

    robot = ET.Element("robot")
    macro = ET.SubElement(
        robot,
        f"{{{XACRO_NS}}}macro",
        {
            "name": device_id,
            "params": (
                "parent_link:='' station_name:='' device_name:='' "
                "x:=0 y:=0 z:=0 rx:=0 ry:=0 r:=0 mesh_path:=''"
            ),
        },
    )
    build_mount_structure(macro, prefixed_name(link_name_map[root_link]))

    for link_node in link_nodes:
        original_name = link_node.attrib["name"]
        new_link = ET.SubElement(macro, "link", {"name": prefixed_name(link_name_map[original_name])})
        for child in list(link_node):
            copied_child = deep_copy(child)
            rewrite_meshes_in_element(copied_child, mesh_copier, device_id)
            new_link.append(copied_child)

    for joint_index, joint_node in enumerate(joint_nodes):
        original_joint_name = joint_node.attrib.get("name", "").strip() or f"joint_{joint_index + 1}"
        new_joint = ET.SubElement(
            macro,
            "joint",
            {
                "name": prefixed_name(joint_name_map[original_joint_name]),
                "type": joint_node.attrib.get("type", "fixed"),
            },
        )
        for child in list(joint_node):
            tag = local_tag(child.tag)
            if tag == "parent":
                parent_name = child.attrib.get("link", "").strip()
                if not parent_name or parent_name not in link_name_map:
                    raise ValueError(f"Joint parent link not found: {parent_name}")
                ET.SubElement(new_joint, "parent", {"link": prefixed_name(link_name_map[parent_name])})
            elif tag == "child":
                child_name = child.attrib.get("link", "").strip()
                if not child_name or child_name not in link_name_map:
                    raise ValueError(f"Joint child link not found: {child_name}")
                ET.SubElement(new_joint, "child", {"link": prefixed_name(link_name_map[child_name])})
            else:
                new_joint.append(deep_copy(child))

    macro_path = device_dir / "macro_device.xacro"
    macro_path.write_text(pretty_xml(robot), encoding="utf-8")

    movable_joint_total = 0
    for joint_node in joint_nodes:
        joint_type = joint_node.attrib.get("type", "").strip().lower()
        if joint_type in MOVABLE_JOINT_TYPES:
            movable_joint_total += 1

    meta_payload: dict[str, Any] = {
        "device_id": device_id,
        "source_kind": "urdf",
        "source_path": normalize_rel_path(source_urdf, source_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stats": {
            "link_total": len(link_nodes),
            "joint_total": len(joint_nodes),
            "movable_joint_total": movable_joint_total,
            "mesh_total": len(mesh_copier.by_source),
        },
        "root_link": root_link,
        "sanitized_link_names": link_name_map,
        "sanitized_joint_names": joint_name_map,
    }
    write_meta_json(device_dir / "meta.json", meta_payload)
    return meta_payload


def convert_stl_device(
    source_dir: Path,
    source_rel_path: str,
    device_id: str,
    output_dir: Path,
) -> dict[str, Any]:
    source_stl = (source_dir / source_rel_path).resolve()
    if not source_stl.exists():
        raise FileNotFoundError(f"STL not found: {source_stl.as_posix()}")

    device_dir = output_dir / device_id
    mesh_dir = device_dir / "meshes"
    ensure_dir(mesh_dir)
    mesh_copier = MeshCopier(device_id=device_id, dest_mesh_dir=mesh_dir)
    mesh_file = mesh_copier.copy_standalone_stl(source_stl)

    robot = ET.Element("robot")
    macro = ET.SubElement(
        robot,
        f"{{{XACRO_NS}}}macro",
        {
            "name": device_id,
            "params": (
                "parent_link:='' station_name:='' device_name:='' "
                "x:=0 y:=0 z:=0 rx:=0 ry:=0 r:=0 mesh_path:=''"
            ),
        },
    )
    root_expr = "${station_name}${device_name}base_link"
    build_mount_structure(macro, root_expr)

    base_link = ET.SubElement(macro, "link", {"name": root_expr})
    visual = ET.SubElement(base_link, "visual")
    ET.SubElement(visual, "origin", {"xyz": "0 0 0", "rpy": "0 0 0"})
    visual_geometry = ET.SubElement(visual, "geometry")
    ET.SubElement(
        visual_geometry,
        "mesh",
        {"filename": f"file://${{mesh_path}}/devices/{device_id}/meshes/{mesh_file}"},
    )
    material = ET.SubElement(visual, "material", {"name": "default"})
    ET.SubElement(material, "color", {"rgba": "0.85 0.85 0.85 1"})

    collision = ET.SubElement(base_link, "collision")
    ET.SubElement(collision, "origin", {"xyz": "0 0 0", "rpy": "0 0 0"})
    collision_geometry = ET.SubElement(collision, "geometry")
    ET.SubElement(
        collision_geometry,
        "mesh",
        {"filename": f"file://${{mesh_path}}/devices/{device_id}/meshes/{mesh_file}"},
    )

    macro_path = device_dir / "macro_device.xacro"
    macro_path.write_text(pretty_xml(robot), encoding="utf-8")

    meta_payload: dict[str, Any] = {
        "device_id": device_id,
        "source_kind": "stl",
        "source_path": normalize_rel_path(source_stl, source_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stats": {
            "link_total": 1,
            "joint_total": 0,
            "movable_joint_total": 0,
            "mesh_total": 1,
        },
        "source_format": "stl",
    }
    write_meta_json(device_dir / "meta.json", meta_payload)
    return meta_payload


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_mapping_index(mapping: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in mapping.get("items", []):
        key = (str(item.get("source_kind", "")), str(item.get("source_path", "")))
        index[key] = item
    return index


def conversion_markdown_report(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Conversion Report")
    lines.append("")
    lines.append(f"- Generated at: `{payload['generated_at']}`")
    lines.append(f"- Source dir: `{payload['source_dir']}`")
    lines.append(f"- Output dir: `{payload['output_dir']}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    summary = payload["summary"]
    lines.append(f"- Total attempted: `{summary['attempted_total']}`")
    lines.append(f"- Total succeeded: `{summary['succeeded_total']}`")
    lines.append(f"- Total failed: `{summary['failed_total']}`")
    lines.append(f"- URDF succeeded: `{summary['urdf_succeeded']}`")
    lines.append(f"- STL succeeded: `{summary['stl_succeeded']}`")
    lines.append("")
    lines.append("## Failures")
    lines.append("")
    failures = payload.get("failures", [])
    if failures:
        for failure in failures:
            lines.append(
                f"- `{failure['source_kind']}::{failure['source_path']}` -> "
                f"`{failure['device_id']}`: {failure['error']}"
            )
    else:
        lines.append("- None.")
    lines.append("")
    return "\n".join(lines)


def parse_only_filter(raw_only: str | None) -> set[str]:
    if not raw_only:
        return set()
    return {value.strip() for value in raw_only.split(",") if value.strip()}


def prepare_output_device_dir(device_dir: Path, overwrite: bool) -> None:
    if device_dir.exists():
        if not overwrite:
            raise FileExistsError(f"Output exists: {device_dir.as_posix()}")
        shutil.rmtree(device_dir)
    ensure_dir(device_dir)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert URDF/STL assets into macro_device.xacro packages."
    )
    parser.add_argument("--inventory-json", required=True, help="Inventory JSON file path.")
    parser.add_argument("--mapping-json", required=True, help="Device ID mapping JSON file path.")
    parser.add_argument("--output-dir", required=True, help="Output directory for generated devices.")
    parser.add_argument("--report-json", required=False, help="Path to write conversion report JSON.")
    parser.add_argument("--report-md", required=False, help="Path to write conversion report markdown.")
    parser.add_argument("--only", required=False, help="Comma-separated device_id allowlist.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing generated device folders.")
    parser.add_argument(
        "--stl-as-device",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Treat standalone STL as Type-C device wrapper.",
    )
    args = parser.parse_args()

    inventory = load_json(Path(args.inventory_json).resolve())
    mapping = load_json(Path(args.mapping_json).resolve())
    source_dir = Path(str(inventory.get("source_dir", ""))).resolve()
    output_dir = Path(args.output_dir).resolve()
    ensure_dir(output_dir)

    mapping_index = build_mapping_index(mapping)
    only_filter = parse_only_filter(args.only)

    attempted_total = 0
    succeeded_total = 0
    urdf_succeeded = 0
    stl_succeeded = 0
    failures: list[ConversionFailure] = []

    for urdf_record in inventory.get("urdf_records", []):
        source_rel = str(urdf_record.get("source_urdf", ""))
        mapping_item = mapping_index.get(("urdf", source_rel))
        if mapping_item is None:
            failures.append(
                ConversionFailure(
                    source_kind="urdf",
                    source_path=source_rel,
                    device_id="",
                    error="missing mapping entry",
                )
            )
            continue
        device_id = str(mapping_item.get("device_id", ""))
        if only_filter and device_id not in only_filter:
            continue

        attempted_total += 1
        try:
            device_dir = output_dir / device_id
            prepare_output_device_dir(device_dir, overwrite=args.overwrite)
            convert_urdf_device(source_dir, source_rel, device_id, output_dir)
            succeeded_total += 1
            urdf_succeeded += 1
        except Exception as exc:
            failures.append(
                ConversionFailure(
                    source_kind="urdf",
                    source_path=source_rel,
                    device_id=device_id,
                    error=str(exc),
                )
            )

    if args.stl_as_device:
        for source_rel in inventory.get("standalone_stl_records", []):
            source_rel_path = str(source_rel)
            mapping_item = mapping_index.get(("stl", source_rel_path))
            if mapping_item is None:
                failures.append(
                    ConversionFailure(
                        source_kind="stl",
                        source_path=source_rel_path,
                        device_id="",
                        error="missing mapping entry",
                    )
                )
                continue
            device_id = str(mapping_item.get("device_id", ""))
            if only_filter and device_id not in only_filter:
                continue

            attempted_total += 1
            try:
                device_dir = output_dir / device_id
                prepare_output_device_dir(device_dir, overwrite=args.overwrite)
                convert_stl_device(source_dir, source_rel_path, device_id, output_dir)
                succeeded_total += 1
                stl_succeeded += 1
            except Exception as exc:
                failures.append(
                    ConversionFailure(
                        source_kind="stl",
                        source_path=source_rel_path,
                        device_id=device_id,
                        error=str(exc),
                    )
                )

    payload: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_dir": source_dir.as_posix(),
        "output_dir": output_dir.as_posix(),
        "summary": {
            "attempted_total": attempted_total,
            "succeeded_total": succeeded_total,
            "failed_total": len(failures),
            "urdf_succeeded": urdf_succeeded,
            "stl_succeeded": stl_succeeded,
        },
        "failures": [failure.__dict__ for failure in failures],
    }

    if args.report_json:
        report_json_path = Path(args.report_json).resolve()
        ensure_dir(report_json_path.parent)
        report_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.report_md:
        report_md_path = Path(args.report_md).resolve()
        ensure_dir(report_md_path.parent)
        report_md_path.write_text(conversion_markdown_report(payload), encoding="utf-8")

    print(
        json.dumps(
            {
                "attempted_total": attempted_total,
                "succeeded_total": succeeded_total,
                "failed_total": len(failures),
                "urdf_succeeded": urdf_succeeded,
                "stl_succeeded": stl_succeeded,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
