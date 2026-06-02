# RoboArm Chem 04 Robot Asset

This package makes the user's real chemistry roboarm usable as an early
Robo-UniLabOS robot asset.

## Contents

- `asset_manifest.json`: robot id, frames, URDF path, workspace, and Feetech
  servo-to-URDF mapping.
- `urdf/04_source.urdf`: original exported URDF.
- `urdf/roboarm_chem_04_query.urdf`: query-ready URDF with rewritten mesh
  package names, conservative nonzero joint limits, coarse collision boxes, and
  a provisional `tool0` frame.
- `meshes/`: STL visual meshes copied from the real-arm model export.

## Query Contract

The expected early Phase 13 queries are:

```text
query_pose("roboarm_chem_04.tool0")
query_state("roboarm_chem_04")
query_affordance("roboarm_chem_04", kind="end_effector")
query_safety_zones()
```

Run a static smoke test from the Uni-Lab-OS checkout:

```bash
PYTHONPATH=. python3 scripts/validate_roboarm_urdf_query.py \
  --asset robot_assets/roboarm_chem_04 \
  --raw-position-json '{"base":2048,"s1":2100,"s2":1996,"e1":2010,"e2":2086,"wrist_p":2048,"wrist_r":2048,"gripper":2000}'
```

Run a live read-only smoke test when the leader state endpoint is up:

```bash
PYTHONPATH=. python3 scripts/validate_roboarm_urdf_query.py \
  --asset robot_assets/roboarm_chem_04 \
  --leader-endpoint http://192.168.1.112:8090/api/leader/state
```

## Boundaries

This asset is for kinematic/query validation. It does not validate real
actuation, torque enable, follower execution, calibrated gripper operation,
controller plugins, transmissions, or high-fidelity dynamics.
