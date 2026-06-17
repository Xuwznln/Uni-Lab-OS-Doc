# URDF/STL to Xacro Workspace

## 1. Purpose
- This folder is the dedicated workspace for converting external URDF/STL assets into OS-ready `macro_device.xacro` device packages.

## 2. Planned Contents
- `input/`: staged source assets to convert.
- `output/`: generated device packages before final integration.
- `reports/`: inventory and validation reports.
- `scripts/`: conversion and validation scripts.

## 3. Scope
- Target integration path: `Uni-Lab-OS/unilabos/device_mesh/devices`.
- Conversion rules and acceptance criteria follow `product_designs/3d_builder_and_simulation/15-urdf-stl-to-xacro-plan.md`.

## 4. Script Pipeline
1. Inventory source assets:
   - `scripts/inventory_assets.py`
2. Generate stable ASCII `device_id` mapping:
   - `scripts/generate_device_id_map.py`
3. Convert URDF/STL to `macro_device.xacro` packages:
   - `scripts/convert_assets.py`
4. Validate generated xacros:
   - `scripts/validate_generated_xacros.py`
5. Smoke-load selected samples after integration:
   - `scripts/smoke_load_check.py`

## 5. Current Outputs
- Inventory:
  - `reports/inventory_report.md`
  - `reports/inventory_report.json`
- Device ID mapping:
  - `reports/device_id_map.md`
  - `reports/device_id_map.json`
- Conversion:
  - `reports/conversion_report.md`
  - `reports/conversion_report.json`
- Validation:
  - `reports/validation_report.md`
  - `reports/validation_report.json`
- Integration smoke check:
  - `reports/integration_smoke_report.md`
