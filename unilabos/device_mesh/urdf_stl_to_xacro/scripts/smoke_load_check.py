from __future__ import annotations

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def build_xacro_text(device_mesh_dir: Path, device_ids: list[str]) -> str:
    lines: list[str] = []
    lines.append('<?xml version="1.0"?>')
    lines.append('<robot xmlns:xacro="http://www.ros.org/wiki/xacro" name="smoke">')
    lines.append('  <link name="world"/>')
    for device_id in device_ids:
        macro_file = (device_mesh_dir / "devices" / device_id / "macro_device.xacro").as_posix()
        lines.append(f'  <xacro:include filename="{macro_file}"/>')
    for index, device_id in enumerate(device_ids, start=1):
        lines.append(
            f'  <xacro:{device_id} parent_link="world" mesh_path="{device_mesh_dir.as_posix()}" '
            f'device_name="smoke{index}_" station_name="" x="0" y="0" z="0" rx="0" ry="0" r="0"/>'
        )
    lines.append("</robot>")
    return "\n".join(lines)


def run_smoke_check(device_mesh_dir: Path, device_ids: list[str]) -> dict:
    import xacro  # type: ignore[import-not-found]

    xacro_text = build_xacro_text(device_mesh_dir, device_ids)
    document = xacro.parse(xacro_text)
    xacro.process_doc(document)
    expanded = document.toxml()

    root = ET.fromstring(expanded)
    links = [node.attrib.get("name", "") for node in root.findall(".//link")]
    joints = [node.attrib.get("name", "") for node in root.findall(".//joint")]
    if not links:
        raise RuntimeError("no links generated after xacro expansion")
    if not joints:
        raise RuntimeError("no joints generated after xacro expansion")

    return {
        "expanded_link_total": len(links),
        "expanded_joint_total": len(joints),
        "sample_links": links[:6],
        "sample_joints": joints[:6],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-load generated devices from device_mesh/devices.")
    parser.add_argument("--device-mesh-dir", required=True, help="Path to unilabos/device_mesh directory.")
    parser.add_argument("--device-ids", required=True, help="Comma-separated device IDs to instantiate.")
    args = parser.parse_args()

    device_mesh_dir = Path(args.device_mesh_dir).resolve()
    device_ids = [item.strip() for item in args.device_ids.split(",") if item.strip()]
    if not device_ids:
        raise SystemExit("No device IDs provided")

    for device_id in device_ids:
        macro_path = device_mesh_dir / "devices" / device_id / "macro_device.xacro"
        if not macro_path.exists():
            raise SystemExit(f"macro_device.xacro not found: {macro_path.as_posix()}")

    result = run_smoke_check(device_mesh_dir, device_ids)
    print(json.dumps({"device_ids": device_ids, **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
