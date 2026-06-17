# Integration Smoke Report

## 1. Scope
- Sample-integrated generated devices into `Uni-Lab-OS/unilabos/device_mesh/devices`:
  - `dev_1600` (URDF converted sample)
  - `dp_lz_bz` (standalone STL wrapped sample)

## 2. Smoke Load Command
- Script: `scripts/smoke_load_check.py`
- Args:
  - `--device-mesh-dir F:/GitHub/new/LeapLab/Uni-Lab-OS/unilabos/device_mesh`
  - `--device-ids dev_1600,dp_lz_bz`

## 3. Result
- Status: PASS
- Expanded links: `5`
- Expanded joints: `4`
- Sample links:
  - `world`
  - `smoke1_device_link`
  - `smoke1_base_link`
  - `smoke2_device_link`
  - `smoke2_base_link`
- Sample joints:
  - `smoke1_base_link_joint`
  - `smoke1_device_link_joint`
  - `smoke2_base_link_joint`
  - `smoke2_device_link_joint`
