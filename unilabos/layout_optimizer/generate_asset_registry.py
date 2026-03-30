"""
Generate a YAML registry file for all devices in uni-lab-assets that don't
already have a registry entry (identified by model.mesh value).

Output: Uni-Lab-OS/unilabos/registry/devices/asset_models.yaml
"""

import json
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths (resolved relative to this script's location)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent

ASSETS_DIR = REPO_ROOT.parent / "uni-lab-assets" / "device_models"
REGISTRY_DIR = REPO_ROOT / "Uni-Lab-OS" / "unilabos" / "registry" / "devices"
OUTPUT_FILE = REGISTRY_DIR / "asset_models.yaml"

OSS_BASE = (
    "https://uni-lab.oss-cn-zhangjiakou.aliyuncs.com/uni-lab/devices"
)
CONTAINER_CLASS = (
    "unilabos.devices.resource_container.container:HotelContainer"
)


# ---------------------------------------------------------------------------
# Step 1 — collect mesh names already present in the registry
# ---------------------------------------------------------------------------

def collect_registered_meshes() -> set[str]:
    """Return the set of mesh values found in all existing registry YAML files."""
    registered: set[str] = set()
    for yaml_file in REGISTRY_DIR.glob("*.yaml"):
        # Skip the output file itself so the script is idempotent
        if yaml_file == OUTPUT_FILE:
            continue
        try:
            with yaml_file.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except Exception as exc:
            print(f"  [warn] Could not parse {yaml_file.name}: {exc}")
            continue
        if not isinstance(data, dict):
            continue
        for _key, entry in data.items():
            if not isinstance(entry, dict):
                continue
            model = entry.get("model")
            if isinstance(model, dict):
                mesh = model.get("mesh")
                if mesh:
                    registered.add(str(mesh))
    return registered


# ---------------------------------------------------------------------------
# Step 2 — scan uni-lab-assets/device_models/
# ---------------------------------------------------------------------------

def scan_asset_devices() -> list[dict]:
    """
    Return a list of device dicts for every subfolder that has a modal.xacro.
    Each dict has keys: folder_name, description.
    """
    devices = []
    if not ASSETS_DIR.is_dir():
        raise FileNotFoundError(f"Assets directory not found: {ASSETS_DIR}")

    for device_dir in sorted(ASSETS_DIR.iterdir()):
        if not device_dir.is_dir():
            continue

        folder_name = device_dir.name

        # modal.xacro is required
        if not (device_dir / "modal.xacro").exists():
            continue

        # Read optional meta.json
        description = folder_name
        meta_path = device_dir / "meta.json"
        if meta_path.exists():
            try:
                with meta_path.open("r", encoding="utf-8") as fh:
                    meta = json.load(fh)
                # Use name field if present; otherwise fall back to folder name
                description = meta.get("name", folder_name)
            except Exception as exc:
                print(f"  [warn] Could not parse {meta_path}: {exc}")

        devices.append(
            {
                "folder_name": folder_name,
                "description": description,
            }
        )

    return devices


# ---------------------------------------------------------------------------
# Step 3 — build registry entry for a single device
# ---------------------------------------------------------------------------

def build_entry(folder_name: str, description: str) -> dict:
    return {
        "category": ["asset_model"],
        "class": {
            "action_value_mappings": {},
            "module": CONTAINER_CLASS,
            "status_types": {},
            "type": "python",
        },
        "config_info": [],
        "description": description,
        "handles": [],
        "icon": "",
        "init_param_schema": {
            "config": {
                "properties": {},
                "required": [],
                "type": "object",
            },
            "data": {
                "properties": {},
                "required": [],
                "type": "object",
            },
        },
        "model": {
            "mesh": folder_name,
            "path": f"{OSS_BASE}/{folder_name}/macro_device.xacro",
            "type": "device",
        },
        "version": "1.0.0",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Scanning existing registry for registered meshes...")
    registered_meshes = collect_registered_meshes()
    print(f"  Found {len(registered_meshes)} already-registered mesh(es).")

    print(f"\nScanning asset devices in: {ASSETS_DIR}")
    all_devices = scan_asset_devices()
    print(f"  Found {len(all_devices)} device folder(s) with modal.xacro.")

    registry: dict[str, dict] = {}
    skipped = 0
    generated = 0

    for device in all_devices:
        folder_name = device["folder_name"]
        if folder_name in registered_meshes:
            skipped += 1
            continue
        key = f"asset_model.{folder_name}"
        registry[key] = build_entry(folder_name, device["description"])
        generated += 1

    print(f"\nWriting {generated} new entr(ies) to: {OUTPUT_FILE}")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as fh:
        yaml.dump(
            registry,
            fh,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    print("\n--- Summary ---")
    print(f"  Total devices found (with modal.xacro): {len(all_devices)}")
    print(f"  Already registered (skipped):           {skipped}")
    print(f"  Newly generated:                        {generated}")


if __name__ == "__main__":
    main()
