from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


MOVABLE_JOINT_TYPES: set[str] = {
    "revolute",
    "continuous",
    "prismatic",
    "planar",
    "floating",
}


@dataclass
class UrdfRecord:
    source_urdf: str
    robot_name: str
    package_root: str | None
    joint_total: int
    movable_joint_total: int
    mesh_ref_total: int
    missing_mesh_refs: list[str]
    missing_mesh_paths: list[str]
    classification: str
    parse_error: str | None


def normalize_path_key(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").lower()


def discover_urdf_files(source_dir: Path) -> list[Path]:
    urdf_files: list[Path] = []
    for path in source_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".urdf":
            urdf_files.append(path)
    urdf_files.sort()
    return urdf_files


def discover_stl_files(source_dir: Path) -> list[Path]:
    stl_files: list[Path] = []
    for path in source_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".stl":
            stl_files.append(path)
    stl_files.sort()
    return stl_files


def find_package_root(urdf_path: Path) -> Path | None:
    current: Path = urdf_path.parent
    while True:
        if (current / "package.xml").exists():
            return current
        if current == current.parent:
            return None
        current = current.parent


def resolve_mesh_reference(
    mesh_ref: str, urdf_path: Path, package_root: Path | None
) -> Path | None:
    raw_ref: str = mesh_ref.strip()
    if not raw_ref:
        return None

    if raw_ref.startswith("package://"):
        body: str = raw_ref[len("package://") :]
        _, _, relative_path = body.partition("/")
        if not relative_path:
            return None
        if package_root is not None:
            return (package_root / relative_path).resolve()
        return (urdf_path.parent / relative_path).resolve()

    if raw_ref.startswith("file://"):
        file_path: str = raw_ref[len("file://") :]
        if len(file_path) >= 3 and file_path[0] == "/" and file_path[2] == ":":
            file_path = file_path[1:]
        return Path(file_path).resolve()

    return (urdf_path.parent / raw_ref).resolve()


def parse_urdf_record(source_dir: Path, urdf_path: Path) -> tuple[UrdfRecord, set[str]]:
    package_root: Path | None = find_package_root(urdf_path)
    referenced_meshes: set[str] = set()
    source_rel: str = str(urdf_path.relative_to(source_dir)).replace("\\", "/")

    try:
        root: ET.Element = ET.parse(urdf_path).getroot()
    except ET.ParseError as exc:
        record = UrdfRecord(
            source_urdf=source_rel,
            robot_name=urdf_path.stem,
            package_root=(
                str(package_root.relative_to(source_dir)).replace("\\", "/")
                if package_root is not None
                else None
            ),
            joint_total=0,
            movable_joint_total=0,
            mesh_ref_total=0,
            missing_mesh_refs=[],
            missing_mesh_paths=[],
            classification="Type-A",
            parse_error=str(exc),
        )
        return record, referenced_meshes

    robot_name: str = root.attrib.get("name", urdf_path.stem)
    joint_types: list[str] = []
    for joint_node in root.findall(".//joint"):
        joint_type: str = joint_node.attrib.get("type", "").strip().lower()
        if joint_type:
            joint_types.append(joint_type)

    movable_joint_total: int = sum(
        1 for joint_type in joint_types if joint_type in MOVABLE_JOINT_TYPES
    )
    classification: str = "Type-B" if movable_joint_total > 0 else "Type-A"

    mesh_refs: list[str] = []
    missing_mesh_refs: list[str] = []
    missing_mesh_paths: list[str] = []
    for mesh_node in root.findall(".//mesh"):
        mesh_ref: str = mesh_node.attrib.get("filename", "").strip()
        if not mesh_ref:
            continue
        mesh_refs.append(mesh_ref)
        resolved_mesh: Path | None = resolve_mesh_reference(mesh_ref, urdf_path, package_root)
        if resolved_mesh is None:
            missing_mesh_refs.append(mesh_ref)
            continue

        mesh_key: str = normalize_path_key(resolved_mesh)
        referenced_meshes.add(mesh_key)
        if not resolved_mesh.exists():
            missing_mesh_refs.append(mesh_ref)
            missing_mesh_paths.append(str(resolved_mesh).replace("\\", "/"))

    record = UrdfRecord(
        source_urdf=source_rel,
        robot_name=robot_name,
        package_root=(
            str(package_root.relative_to(source_dir)).replace("\\", "/")
            if package_root is not None and package_root.is_relative_to(source_dir)
            else (str(package_root).replace("\\", "/") if package_root is not None else None)
        ),
        joint_total=len(joint_types),
        movable_joint_total=movable_joint_total,
        mesh_ref_total=len(mesh_refs),
        missing_mesh_refs=missing_mesh_refs,
        missing_mesh_paths=missing_mesh_paths,
        classification=classification,
        parse_error=None,
    )
    return record, referenced_meshes


def build_markdown_report(
    source_dir: Path,
    urdf_records: list[UrdfRecord],
    standalone_stl_files: list[Path],
) -> str:
    type_a_count: int = sum(1 for record in urdf_records if record.classification == "Type-A")
    type_b_count: int = sum(1 for record in urdf_records if record.classification == "Type-B")
    parse_error_count: int = sum(1 for record in urdf_records if record.parse_error is not None)
    urdf_with_missing_mesh_count: int = sum(
        1 for record in urdf_records if len(record.missing_mesh_refs) > 0
    )
    timestamp: str = datetime.now().isoformat(timespec="seconds")

    lines: list[str] = []
    lines.append("# Inventory Report: URDF/STL Inputs")
    lines.append("")
    lines.append(f"- Source directory: `{source_dir.as_posix()}`")
    lines.append(f"- Generated at: `{timestamp}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- URDF files: `{len(urdf_records)}`")
    lines.append(f"- Type-A (URDF static): `{type_a_count}`")
    lines.append(f"- Type-B (URDF articulated): `{type_b_count}`")
    lines.append(f"- Type-C (standalone STL): `{len(standalone_stl_files)}`")
    lines.append(f"- URDF with missing mesh refs: `{urdf_with_missing_mesh_count}`")
    lines.append(f"- URDF parse errors: `{parse_error_count}`")
    lines.append("")
    lines.append("## URDF Records")
    lines.append("")
    lines.append(
        "| # | Type | Robot | URDF | Joints (movable/total) | Mesh refs | Missing mesh refs | Parse error |"
    )
    lines.append("|---:|---|---|---|---:|---:|---:|---|")
    for index, record in enumerate(urdf_records, start=1):
        parse_error_text: str = (
            record.parse_error.replace("|", "/") if record.parse_error is not None else "-"
        )
        lines.append(
            f"| {index} | {record.classification} | {record.robot_name} | "
            f"`{record.source_urdf}` | {record.movable_joint_total}/{record.joint_total} | "
            f"{record.mesh_ref_total} | {len(record.missing_mesh_refs)} | {parse_error_text} |"
        )

    lines.append("")
    lines.append("## Standalone STL Candidates (Type-C)")
    lines.append("")
    if standalone_stl_files:
        for index, stl_path in enumerate(standalone_stl_files, start=1):
            rel_path: str = str(stl_path.relative_to(source_dir)).replace("\\", "/")
            lines.append(f"{index}. `{rel_path}`")
    else:
        lines.append("- None detected.")

    lines.append("")
    lines.append("## Missing Mesh Details")
    lines.append("")
    has_missing_details: bool = False
    for record in urdf_records:
        if not record.missing_mesh_refs:
            continue
        has_missing_details = True
        lines.append(f"- `{record.source_urdf}`")
        for mesh_ref in record.missing_mesh_refs:
            lines.append(f"  - ref: `{mesh_ref}`")
        for mesh_path in record.missing_mesh_paths:
            lines.append(f"  - resolved_missing_path: `{mesh_path}`")
    if not has_missing_details:
        lines.append("- None.")

    lines.append("")
    return "\n".join(lines)


def build_json_payload(
    source_dir: Path,
    urdf_records: list[UrdfRecord],
    standalone_stl_files: list[Path],
) -> dict[str, Any]:
    type_a_count: int = sum(1 for record in urdf_records if record.classification == "Type-A")
    type_b_count: int = sum(1 for record in urdf_records if record.classification == "Type-B")
    parse_error_count: int = sum(1 for record in urdf_records if record.parse_error is not None)
    urdf_with_missing_mesh_count: int = sum(
        1 for record in urdf_records if len(record.missing_mesh_refs) > 0
    )

    return {
        "source_dir": source_dir.as_posix(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "urdf_total": len(urdf_records),
            "type_a_total": type_a_count,
            "type_b_total": type_b_count,
            "type_c_total": len(standalone_stl_files),
            "urdf_with_missing_mesh_total": urdf_with_missing_mesh_count,
            "urdf_parse_error_total": parse_error_count,
        },
        "urdf_records": [record.__dict__ for record in urdf_records],
        "standalone_stl_records": [
            str(path.relative_to(source_dir)).replace("\\", "/") for path in standalone_stl_files
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inventory URDF/STL assets for macro_device.xacro conversion."
    )
    parser.add_argument("--source-dir", required=True, help="Source directory to scan.")
    parser.add_argument(
        "--md-out",
        required=False,
        help="Optional output path for markdown report.",
    )
    parser.add_argument(
        "--json-out",
        required=False,
        help="Optional output path for JSON report.",
    )
    args = parser.parse_args()

    source_dir: Path = Path(args.source_dir).resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        raise SystemExit(f"Invalid source directory: {source_dir.as_posix()}")

    urdf_files: list[Path] = discover_urdf_files(source_dir)
    stl_files: list[Path] = discover_stl_files(source_dir)

    urdf_records: list[UrdfRecord] = []
    referenced_meshes: set[str] = set()
    for urdf_path in urdf_files:
        record, record_references = parse_urdf_record(source_dir, urdf_path)
        urdf_records.append(record)
        referenced_meshes.update(record_references)

    standalone_stl_files: list[Path] = [
        path for path in stl_files if normalize_path_key(path) not in referenced_meshes
    ]

    markdown_report: str = build_markdown_report(source_dir, urdf_records, standalone_stl_files)
    json_payload: dict[str, Any] = build_json_payload(source_dir, urdf_records, standalone_stl_files)

    if args.md_out:
        md_out_path: Path = Path(args.md_out).resolve()
        md_out_path.parent.mkdir(parents=True, exist_ok=True)
        md_out_path.write_text(markdown_report, encoding="utf-8")
    else:
        print(markdown_report)

    if args.json_out:
        json_out_path: Path = Path(args.json_out).resolve()
        json_out_path.parent.mkdir(parents=True, exist_ok=True)
        json_out_path.write_text(
            json.dumps(json_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
