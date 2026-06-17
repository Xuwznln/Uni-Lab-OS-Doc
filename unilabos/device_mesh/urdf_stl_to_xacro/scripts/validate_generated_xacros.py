from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


MESH_REF_PATTERN = re.compile(
    r"^file://\$\{mesh_path\}/devices/(?P<device>[^/]+)/meshes/(?P<file>[^/]+)$"
)


@dataclass
class ValidationFailure:
    device_id: str
    check: str
    message: str


def local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def collect_macro_mesh_refs(macro_path: Path) -> list[str]:
    root = ET.parse(macro_path).getroot()
    refs: list[str] = []
    for mesh_node in root.findall(".//mesh"):
        mesh_ref = mesh_node.attrib.get("filename", "").strip()
        if mesh_ref:
            refs.append(mesh_ref)
    return refs


def validate_mesh_ref(device_id: str, mesh_ref: str, device_dir: Path) -> str | None:
    match = MESH_REF_PATTERN.match(mesh_ref)
    if match is None:
        return f"mesh ref format invalid: {mesh_ref}"
    ref_device = match.group("device")
    mesh_file = match.group("file")
    if ref_device != device_id:
        return f"mesh ref device mismatch: {ref_device} != {device_id}"
    if not (device_dir / "meshes" / mesh_file).exists():
        return f"mesh file missing: meshes/{mesh_file}"
    return None


def process_xacro_file(macro_path: Path) -> str:
    import xacro  # type: ignore[import-not-found]

    mappings = {
        "parent_link": "world",
        "station_name": "",
        "device_name": "unit_",
        "x": "0",
        "y": "0",
        "z": "0",
        "rx": "0",
        "ry": "0",
        "r": "0",
        "mesh_path": "/tmp",
    }
    doc = xacro.process_file(str(macro_path), mappings=mappings)
    return doc.toxml()


def validate_expanded_urdf(device_id: str, urdf_xml: str) -> list[str]:
    errors: list[str] = []
    root = ET.fromstring(urdf_xml)
    links = root.findall(".//link")
    joints = root.findall(".//joint")

    link_names: list[str] = []
    for link in links:
        name = link.attrib.get("name", "").strip()
        if not name:
            errors.append("expanded URDF has unnamed link")
            continue
        link_names.append(name)

    if len(set(link_names)) != len(link_names):
        errors.append("expanded URDF has duplicate link names")

    joint_names: list[str] = []
    for joint in joints:
        name = joint.attrib.get("name", "").strip()
        if not name:
            errors.append("expanded URDF has unnamed joint")
            continue
        joint_names.append(name)
    if len(set(joint_names)) != len(joint_names):
        errors.append("expanded URDF has duplicate joint names")

    link_name_set = set(link_names)
    for joint in joints:
        parent = joint.find("parent")
        child = joint.find("child")
        if parent is None or child is None:
            continue
        parent_name = parent.attrib.get("link", "").strip()
        child_name = child.attrib.get("link", "").strip()
        if parent_name and parent_name not in link_name_set:
            errors.append(f"joint parent link missing: {parent_name}")
        if child_name and child_name not in link_name_set:
            errors.append(f"joint child link missing: {child_name}")

    return errors


def build_markdown_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines: list[str] = []
    lines.append("# Xacro Validation Report")
    lines.append("")
    lines.append(f"- Generated at: `{payload['generated_at']}`")
    lines.append(f"- Output directory: `{payload['output_dir']}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total devices: `{summary['total_devices']}`")
    lines.append(f"- Passed: `{summary['passed_total']}`")
    lines.append(f"- Failed: `{summary['failed_total']}`")
    lines.append("")
    lines.append("## Failures")
    lines.append("")
    if payload["failures"]:
        for failure in payload["failures"]:
            lines.append(
                f"- `{failure['device_id']}` [{failure['check']}]: {failure['message']}"
            )
    else:
        lines.append("- None.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate generated macro_device.xacro outputs."
    )
    parser.add_argument("--output-dir", required=True, help="Generated device output directory.")
    parser.add_argument("--report-json", required=False, help="Output JSON report path.")
    parser.add_argument("--report-md", required=False, help="Output markdown report path.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    if not output_dir.exists():
        raise SystemExit(f"Output directory not found: {output_dir.as_posix()}")

    failures: list[ValidationFailure] = []
    total_devices = 0
    passed_total = 0

    device_dirs = [path for path in output_dir.iterdir() if path.is_dir()]
    device_dirs.sort()
    for device_dir in device_dirs:
        device_id = device_dir.name
        macro_path = device_dir / "macro_device.xacro"
        if not macro_path.exists():
            continue
        total_devices += 1
        current_errors: list[ValidationFailure] = []

        try:
            mesh_refs = collect_macro_mesh_refs(macro_path)
        except Exception as exc:
            current_errors.append(
                ValidationFailure(device_id=device_id, check="xml_parse", message=str(exc))
            )
            failures.extend(current_errors)
            continue

        for mesh_ref in mesh_refs:
            error = validate_mesh_ref(device_id, mesh_ref, device_dir)
            if error:
                current_errors.append(
                    ValidationFailure(device_id=device_id, check="mesh_ref", message=error)
                )

        try:
            expanded_xml = process_xacro_file(macro_path)
            urdf_errors = validate_expanded_urdf(device_id, expanded_xml)
            for error in urdf_errors:
                current_errors.append(
                    ValidationFailure(device_id=device_id, check="xacro_expand", message=error)
                )
        except Exception as exc:
            current_errors.append(
                ValidationFailure(device_id=device_id, check="xacro_expand", message=str(exc))
            )

        if current_errors:
            failures.extend(current_errors)
        else:
            passed_total += 1

    payload: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": output_dir.as_posix(),
        "summary": {
            "total_devices": total_devices,
            "passed_total": passed_total,
            "failed_total": len({failure.device_id for failure in failures}),
        },
        "failures": [failure.__dict__ for failure in failures],
    }

    if args.report_json:
        report_json = Path(args.report_json).resolve()
        report_json.parent.mkdir(parents=True, exist_ok=True)
        report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.report_md:
        report_md = Path(args.report_md).resolve()
        report_md.parent.mkdir(parents=True, exist_ok=True)
        report_md.write_text(build_markdown_report(payload), encoding="utf-8")

    print(
        json.dumps(
            {
                "total_devices": payload["summary"]["total_devices"],
                "passed_total": payload["summary"]["passed_total"],
                "failed_device_total": payload["summary"]["failed_total"],
                "failure_record_total": len(payload["failures"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
