# Device Macro Schema

## 1. Goal
- Define one consistent `macro_device.xacro` contract for all converted assets.
- Cover three input types:
  - Type-A: URDF static
  - Type-B: URDF articulated
  - Type-C: standalone STL wrapped as device

## 2. Required Macro Signature
- Macro name: `<device_id>`
- Macro params:
  - `parent_link:=''`
  - `station_name:=''`
  - `device_name:=''`
  - `x:=0 y:=0 z:=0`
  - `rx:=0 ry:=0 r:=0`
  - `mesh_path:=''`

## 3. Required Root Mount Structure
1. Fixed joint: `${station_name}${device_name}base_link_joint`
   - parent: `${parent_link}`
   - child: `${station_name}${device_name}device_link`
2. Link: `${station_name}${device_name}device_link`
3. Fixed joint: `${station_name}${device_name}device_link_joint`
   - parent: `${station_name}${device_name}device_link`
   - child: `${station_name}${device_name}<root_link>`

## 4. Name Prefixing Rules
- Every link and joint emitted from source URDF must be renamed with:
  - `${station_name}${device_name}<sanitized_name>`
- Sanitization target:
  - ASCII lowercase
  - only `[a-z0-9_]`
  - no leading digit (`dev_` prefix if needed)
- Keep deterministic mapping and emit mapping report.

## 5. Mesh Path Rule
- All mesh references in generated xacro must be rewritten to:
  - `file://${mesh_path}/devices/<device_id>/meshes/<mesh_file>`
- Source `package://...` and relative mesh paths are both normalized.

## 6. Type-C (Standalone STL) Minimum Layout
- Generate one visual+collision link named:
  - `${station_name}${device_name}base_link`
- No movable joint by default.
- Wrap with required root mount structure in section 3.

## 7. Optional Extensions
- `joint_config.json`: for Type-B where actuator limits are needed by downstream pipeline.
- `meta.json`: source metadata such as `source_format`, `source_path`, `generated_at`.

## 8. Validation Targets
- `xacro` expansion succeeds.
- No unresolved mesh path.
- Parent/child links of all joints are resolvable.
- No duplicate link/joint names after prefixing.
