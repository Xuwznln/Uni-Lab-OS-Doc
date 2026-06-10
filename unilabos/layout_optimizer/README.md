# Layout Optimizer Handover

**Date**: 2026-04-10 | **Branch**: `feat/3d_layout_and_visualize` | **Commit**: `99dc821a` | **Tests**: 270 (260 pass + 10 LLM skip w/o API key)

This package is a standalone lab layout optimizer. It takes a device list + constraints and returns optimized placements. Your integration points are the HTTP API and the LLM skill document.

---

## 1. Full Pipeline Overview

```
User NL request
       │
       ▼
┌─────────────────┐    skill doc: llm_skill/layout_intent_translator.md
│   LLM Agent     │◄── + device list from scene (GET /devices)
│ (your side)     │    + schema discovery (GET /interpret/schema)
└────────┬────────┘
         │ structured intents JSON
         ▼
  POST /interpret          ← intent_interpreter.py (pure translation)
         │
         │ { constraints, translations, workflow_edges, errors }
         ▼
   User confirms           ← translations have human-readable explanations
         │
         ▼
  POST /optimize           ← full pipeline below
         │
    ┌────┴─────────────────────────────────────────┐
    │  1. Device catalog    (device_catalog.py)     │
    │     footprints.json → Device objects          │
    │     bbox, height, openings per device         │
    │                                               │
    │  2. Seeder            (seeders.py)            │
    │     Force-directed initial placement          │
    │     Presets: compact_outward, spread_inward,  │
    │       workflow_cluster, row_fallback           │
    │     Accounts for openings, workflow edges      │
    │                                               │
    │  3. DE Optimizer      (optimizer.py)          │
    │     Custom DE loop (best1bin/currenttobest1bin│
    │       /rand1bin strategies)                    │
    │     3N-dim: [x0, y0, θ0, x1, y1, θ1, ...]   │
    │     Broad-phase AABB sweep (broad_phase.py)   │
    │     θ lattice snap in joint discrete mode     │
    │     Cost = hard_penalties + soft_penalties      │
    │     Graduated collision penalties (not binary) │
    │                                               │
    │  4. θ snap            (optimizer.snap_theta)  │
    │     Snap near-cardinal angles to 0/90/180/270 │
    │     (opt-in via snap_cardinal=True)           │
    │                                               │
    │  5. Final eval        (constraints.py)        │
    │     Binary pass/fail for response.success     │
    └──────────────────────────────────────────────┘
         │
         ▼
   { placements, cost, success }
```

---

## 2. API Reference

### `POST /interpret` — LLM intent → constraints

Translates semantic intents into optimizer constraints. The LLM agent calls this after translating user NL.

**Request:**

```json
{
  "intents": [
    {
      "intent": "reachable_by",
      "params": {"arm": "arm_slider", "targets": ["opentrons_liquid_handler", "inheco_odtc_96xl"]},
      "description": "Robot arm must reach these devices"
    },
    {
      "intent": "workflow_hint",
      "params": {"workflow": "pcr", "devices": ["device_a", "device_b", "device_c"]},
      "description": "PCR workflow order"
    },
    {
      "intent": "close_together",
      "params": {"devices": ["device_a", "device_b"], "priority": "high"},
      "description": "Keep these close"
    }
  ]
}
```

**Response:**

```json
{
  "constraints": [
    {"type": "hard", "rule_name": "reachability", "params": {"arm_id": "arm_slider", "target_device_id": "opentrons_liquid_handler"}, "weight": 1.0},
    ...
  ],
  "translations": [
    {
      "source_intent": "reachable_by",
      "source_description": "Robot arm must reach these devices",
      "source_params": {"arm": "arm_slider", "targets": ["..."]},
      "generated_constraints": [...],
      "explanation": "机械臂 'arm_slider' 需要能够到达 2 个目标设备",
      "confidence": "high"
    }
  ],
  "workflow_edges": [["device_a", "device_b"], ["device_b", "device_c"]],
  "errors": []
}
```

The `constraints` and `workflow_edges` arrays pass directly to `/optimize` — no transformation needed.

### `GET /interpret/schema` — LLM discovery

Returns all 11 intent types with parameter specs. LLM agent should call this before translating.

### `POST /optimize` — Run layout optimization

**Request:**

```json
{
  "devices": [
    {"id": "thermo_orbitor_rs2_hotel", "name": "Plate Hotel", "device_type": "static"},
    {"id": "arm_slider", "name": "Robot Arm", "device_type": "articulation"},
    ...
  ],
  "lab": {"width": 6.0, "depth": 4.0},
  "constraints": [...],
  "workflow_edges": [["device_a", "device_b"]],
  "seeder": "compact_outward",
  "run_de": true,
  "maxiter": 200,
  "seed": 42,
  "angle_granularity": 4,
  "snap_cardinal": false,
  "strategy": "currenttobest1bin",
  "mutation": [0.5, 1.0],
  "theta_mutation": null,
  "recombination": 0.7,
  "crossover_mode": "device"
}
```

**Response:**

```json
{
  "placements": [
    {
      "device_id": "thermo_orbitor_rs2_hotel",
      "uuid": "thermo_orbitor_rs2_hotel",
      "position": {"x": 1.33, "y": 2.35, "z": 0.0},
      "rotation": {"x": 0.0, "y": 0.0, "z": 1.5708}
    },
    ...
  ],
  "cost": 0.0,
  "success": true,
  "seeder_used": "compact_outward",
  "de_ran": true,
  "breakdown": [{"name": "[predefined] collision", "rule": "no_collision", "type": "hard", "weight": 500, "cost": 0.0}, ...],
  "violations": [],
  "conflicts": []
}
```

`position`/`rotation` format matches Cloud's `CommonPositionType`. `rotation.z` is θ in radians.

**Failure diagnostics (since 2026-06):** every `/optimize` response now also carries:
- `breakdown` — per-constraint graduated penalty for the final layout (predefined collision/boundary + user constraints).
- `violations` — the hard constraints with `cost > 0`, sorted desc (the failure culprits).
- `conflicts` — deterministic conflicts found by the analytical pre-check (see `/optimize/auto`).

### `POST /optimize/auto` — Self-healing layout optimization

Wraps `/optimize` with failure self-healing. Accepts everything `/optimize` does, plus a multi-start grid, and runs it across **parallel processes**:

```json
{
  "devices": [...],
  "lab": {"width": 6.0, "depth": 4.0},
  "constraints": [...],
  "workflow_edges": [...],
  "seeds": [42, 7, 123, 2024],
  "seeders": ["compact_outward", "spread_inward", "workflow_cluster"],
  "maxiter": 400,
  "max_workers": null
}
```

Server behaviour:
1. **Analytical pre-check (situation A).** Detects provably-infeasible inputs (total device area > lab area, a device that can't fit, `max_distance < min_distance`, `min_distance > lab diagonal`, `max_distance < min_spacing`) and **short-circuits** without running DE. Zero false positives — only deterministic conflicts.
2. **Parallel multi-start DE (situation B).** Runs the `seeds × seeders` grid in `multiprocessing` (spawn) processes; the FIRST start that reaches a feasible layout wins and the rest are terminated. `maxiter` is fixed-large (DE early-stops), it is NOT a grid axis.
3. **Aggregated culprits.** If all starts fail, returns the hard constraints that stayed violated across ALL runs (`persistent: true` = binding culprit).

**Response (extends OptimizeResponse):**

```json
{
  "placements": [...],
  "cost": 0.0,
  "success": true,
  "seeder_used": "compact_outward",   // winning seeder
  "de_ran": true,
  "tried": 1,                          // starts completed before a winner / exhaustion
  "total": 12,                         // planned starts (len(seeds) × len(seeders))
  "conflicts": [],                     // non-empty ⇒ situation A (short-circuited)
  "violations": [],                    // persistent culprits when all starts fail (situation B)
  "breakdown": []
}
```

Each `conflicts[]` entry has `{kind, message, devices, rules, suggestion}`; each `violations[]` entry (failure path) has `{name, rule, type, runs_violated, total_runs, mean_residual, persistent}`.

**DE hyperparameters:**


| Param               | Default                     | Description                                                                                                                        |
| ------------------- | --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `strategy`          | `"currenttobest1bin"`       | DE mutation strategy (`best1bin`, `currenttobest1bin`, `rand1bin`)                                                                 |
| `mutation`          | `[0.5, 1.0]`                | Dithered F range for position dimensions                                                                                           |
| `theta_mutation`    | `null` (same as `mutation`) | Separate F range for θ dimensions (decoupled mutation)                                                                             |
| `recombination`     | `0.7`                       | Crossover probability                                                                                                              |
| `crossover_mode`    | `"device"`                  | `"device"` = per-device CR, `"dimension"` = per-dimension CR                                                                       |
| `angle_granularity` | `null`                      | `4`/`8`/`12`/`24` — snaps θ to a discrete lattice during DE (joint mode). `4` = axis-aligned (0/90/180/270). `null` = continuous θ |
| `snap_cardinal`     | `false`                     | Post-DE snap to nearest cardinal angle with collision rollback                                                                     |


### Scene State API

Shared scene state between the LLM agent and the frontend. The agent pushes layout results here; the frontend polls for updates.

#### `GET /scene/lab` / `POST /scene/lab` — Lab dimensions

**GET** returns current lab dimensions. **POST** sets them (frontend sends this when user changes lab size).

```json
{"width": 6.0, "depth": 4.0}
```

#### `GET /scene/placements` / `POST /scene/placements` / `DELETE /scene/placements`

**GET** returns current placements + a version counter. Frontend polls this every 1s and re-renders when version changes.

```json
{"version": 3, "placements": [...]}
```

**POST** pushes new placements (from `/optimize` result or agent). Bumps version.

> **Frontend auto-create (2026-06):** When the frontend polls and finds a placement whose
> device is not yet in the scene, it now **auto-creates** that device from the loaded catalog
> (no manual "add to scene" needed) and animates it into position. This means an agent can push
> placements for a fresh/empty scene and the frontend will render them directly. Matching is
> keyed by `placement.uuid` (multi-instance ready), falling back to `device_id` for compatibility
> with manually added devices. See §11.2.

**DELETE** clears all placements (resets scene).

### `GET /devices` — Device catalog

Returns all known devices with bbox, openings, model paths. The LLM agent should receive this list as context so it can resolve fuzzy device names.

### `GET /health`

Returns `{"status": "ok"}`.

---

## 3. Intent Types (11 total)


| Intent           | Params                                              | Generates                                          | Type |
| ---------------- | --------------------------------------------------- | -------------------------------------------------- | ---- |
| `reachable_by`   | `arm` (str), `targets` (list[str])                  | `reachability` per target                          | hard |
| `close_together` | `devices` (list[str]), `priority` (low/medium/high) | `minimize_distance` per pair                       | soft |
| `far_apart`      | `devices` (list[str]), `priority`                   | `maximize_distance` per pair                       | soft |
| `keep_adjacent`  | `devices` (list[str]), `priority`                   | `minimize_distance` per pair                       | soft |
| `max_distance`   | `device_a`, `device_b`, `distance` (float m)        | `distance_less_than`                               | hard |
| `min_distance`   | `device_a`, `device_b`, `distance` (float m)        | `distance_greater_than`                            | hard |
| `min_spacing`    | `min_gap` (float m, default 0.3)                    | `min_spacing`                                      | hard |
| `workflow_hint`  | `workflow` (str), `devices` (ordered list[str])     | `minimize_distance` consecutive + `workflow_edges` | soft |
| `face_outward`   | (none)                                              | `prefer_orientation_mode` outward                  | soft |
| `face_inward`    | (none)                                              | `prefer_orientation_mode` inward                   | soft |
| `align_cardinal` | (none)                                              | `prefer_aligned`                                   | soft |


Intent priorities are baked into the final emitted constraint `weight` during interpretation. The caller only sees the resulting weight, not a separate constraint-level priority field.

---

## 4. LLM Integration Guide

### What You Need to Build (Your Side)

The LLM agent that converts user natural language → structured intents JSON. We provide:

1. **Skill document** (`llm_skill/layout_intent_translator.md`) — system prompt for the LLM. Contains intent schema, device name resolution rules, translation rules, and PCR workflow examples.
2. **Runtime schema** (`GET /interpret/schema`) — machine-readable intent specs. LLM agent should call this for discovery.
3. **Device context** — before translating, feed the LLM the scene's device list (from `GET /devices` or your scene state). The LLM uses this to resolve fuzzy names like "PCR machine" → `inheco_odtc_96xl`.

### Integration Flow

```
1. User enters NL request in Cloud UI
2. Your LLM agent receives:
   - User message
   - Scene device list (id, name, type, bbox)
   - Skill doc as system prompt
   - Optional: GET /interpret/schema for discovery
3. LLM outputs: {"intents": [...]}
4. POST /interpret with LLM output
5. Show user the translations for confirmation
6. POST /optimize with confirmed constraints + workflow_edges
7. Apply placements to scene
```

### Device Name Resolution (handled by LLM, not by optimizer)

The skill doc teaches the LLM to match fuzzy names:

- "PCR machine" / "thermal cycler" → `inheco_odtc_96xl`
- "liquid handler" / "pipetting robot" → `opentrons_liquid_handler`
- "plate hotel" / "storage" → `thermo_orbitor_rs2_hotel`
- "robot arm" / "the arm" → device with `type: articulation`
- "plate sealer" → `agilent_plateloc`

No search endpoint needed — the device list is already in context.

### Tested LLM Outputs

We tested with Claude Sonnet (via subagent, no API key required). Examples:

**Input**: "Take plate from hotel, prepare sample in the pipetting robot, seal it, then run thermal cycling. The arm handles all transfers. Keep liquid handler and sealer close, minimum 15cm gap."

**LLM produced**: `reachable_by` (arm→4 devices), `workflow_hint` (correct PCR order), `close_together` (high, LH+sealer), `min_distance` (0.15m, LH+sealer)

**Input**: "I want an automatic PCR lab, make it compact and neat"

**LLM produced**: `reachable_by`, `workflow_hint`, `close_together` (all devices), `min_spacing` (0.05m), `align_cardinal`

All outputs pass through `/interpret` → `/optimize` successfully.

---

## 5. Constraint System Details

### Hard Constraints (cost = ∞ on violation)


| Rule Name               | Params                             | What it checks                                 |
| ----------------------- | ---------------------------------- | ---------------------------------------------- |
| `no_collision`          | (default, always on)               | OBB-SAT pairwise collision between all devices |
| `within_bounds`         | (default, always on)               | All devices within lab boundary                |
| `reachability`          | `arm_id`, `target_device_id`       | Target center within arm reach radius          |
| `distance_less_than`    | `device_a`, `device_b`, `distance` | OBB edge-to-edge distance ≤ threshold          |
| `distance_greater_than` | `device_a`, `device_b`, `distance` | OBB edge-to-edge distance ≥ threshold          |
| `min_spacing`           | `min_gap`                          | All device pairs have ≥ min_gap edge-to-edge   |


### Soft Constraints (weighted penalty)


| Rule Name                   | Params                              | What it minimizes                                                                                                                               |
| --------------------------- | ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `minimize_distance`         | `device_a`, `device_b`              | OBB edge-to-edge distance × weight                                                                                                              |
| `maximize_distance`         | `device_a`, `device_b`              | 1/(distance+ε) × weight                                                                                                                         |
| `prefer_orientation_mode`   | `mode` (outward/inward)             | Angle between opening direction and ideal direction                                                                                             |
| `prefer_aligned`            | (none)                              | Deviation from nearest 90° angle                                                                                                                |
| `prefer_seeder_orientation` | (none)                              | Deviation from seeder-assigned θ                                                                                                                |
| `crossing_penalty`          | (auto, part of `reachability` eval) | Segment-OBB intersection length of opening-to-arm path blocked by other devices (Cyrus-Beck clipping via `obb.segment_obb_intersection_length`) |


### Weight Normalization


| Constant                  | Value | Meaning                                                |
| ------------------------- | ----- | ------------------------------------------------------ |
| `DEFAULT_WEIGHT_DISTANCE` | 100.0 | 1 cm → penalty 1.0                                     |
| `DEFAULT_WEIGHT_ANGLE`    | 60.0  | 5° → penalty ~1.0                                      |
| `HARD_MULTIPLIER`         | 5.0   | Hard constraint penalty multiplier during graduated DE |


Constraints support a `priority` field (`critical` / `high` / `normal` / `low`) with multipliers 5× / 2× / 1× / 0.5×.

### Graduated Penalties (DE internals)

Default hard constraints (collision, boundary) use **graduated penalties** during DE optimization — proportional to penetration depth / overshoot distance. This gives DE a smooth gradient instead of binary inf. Final evaluation uses binary mode for pass/fail reporting.

---

## 6. Checker Architecture (Mock → Real)

```
interfaces.py (Protocol definitions)
    ├── CollisionChecker.check(placements) → collisions
    ├── CollisionChecker.check_bounds(placements, w, d) → out_of_bounds
    └── ReachabilityChecker.is_reachable(arm_id, arm_pose, target) → bool

mock_checkers.py (current, no ROS)
    ├── MockCollisionChecker — OBB SAT
    └── MockReachabilityChecker — Euclidean distance, 100m fallback for unknown arms

ros_checkers.py (for ROS2/MoveIt2 integration)
    ├── MoveItCollisionChecker — python-fcl direct + OBB fallback
    └── IKFastReachabilityChecker — precomputed voxel O(1) + live IK fallback
    └── create_checkers(mode) — factory, controlled by LAYOUT_CHECKER_MODE env var
```

To switch to real checkers: `LAYOUT_CHECKER_MODE=moveit` + pass MoveIt2 instance.

---

## 7. File Inventory

### Core Pipeline


| File                    | Lines | Purpose                                                                                   |
| ----------------------- | ----- | ----------------------------------------------------------------------------------------- |
| `models.py`             | 97    | Dataclasses: Device, Lab, Placement, Constraint, Intent, Opening                          |
| `device_catalog.py`     | 303   | Loads devices from footprints.json + uni-lab-assets + registry                            |
| `footprints.json`       | 183KB | 499 device bounding boxes, heights, openings (offline extracted)                          |
| `seeders.py`            | 331   | Force-directed initial layout with presets                                                |
| `optimizer.py`          | 1056  | Custom DE loop: per-device crossover, θ wrapping, discrete angle lattice, multi-strategy  |
| `broad_phase.py`        | 66    | 2-axis sweep-and-prune AABB broad phase for collision pair pruning                        |
| `constraints.py`        | 627   | Unified constraint evaluation (hard + soft + graduated + crossing penalty)                |
| `obb.py`                | 257   | OBB geometry: corners, overlap SAT, min_distance, penetration_depth, segment intersection |
| `intent_interpreter.py` | 366   | 11 intent handlers, pure translation, no side effects                                     |
| `feasibility.py`        | ~310  | Analytical conflict pre-check (situation A) + breakdown/violations extraction (no import side effects, multiprocessing-safe) |
| `parallel_optimize.py`  | ~290  | Parallel multi-start DE (spawn): first-success-wins + kill others + aggregate persistent culprits |
| `server.py`             | ~900  | FastAPI: /interpret, /optimize, /optimize/auto, /devices, /scene/* endpoints              |
| `lab_parser.py`         | 50    | Parse lab floor plan JSON to Lab dataclass                                                |


### Reference / Utilities


| File                         | Purpose                                                                              |
| ---------------------------- | ------------------------------------------------------------------------------------ |
| `extract_footprints.py`      | How footprints.json was generated (offline STL/GLB → 2D bbox extraction via trimesh) |
| `generate_asset_registry.py` | Generate YAML registry entries for uni-lab-assets devices not already registered     |


### Integration Layer


| File               | Purpose                                                         |
| ------------------ | --------------------------------------------------------------- |
| `interfaces.py`    | Protocol definitions for CollisionChecker / ReachabilityChecker |
| `mock_checkers.py` | Dev-mode checkers (OBB collision, Euclidean reachability)       |
| `ros_checkers.py`  | MoveIt2/IKFast adapters for real collision + reachability       |


### LLM


| File                                    | Purpose                                                                                                             |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `llm_skill/layout_intent_translator.md` | System prompt for LLM: intent schema, device resolution, translation rules, examples                                |
| `llm_skill/demo_agent.md`               | LLM agent orchestration instructions for demo (GET /devices → intents → /interpret → /optimize → /scene/placements) |


### Demo / Frontend


| File                | Purpose                                                                                                  |
| ------------------- | -------------------------------------------------------------------------------------------------------- |
| `static/lab3d.html` | Three.js 3D visualization frontend (1250 lines): device library, drag-to-add, auto layout, scene polling (auto-creates devices from pushed placements) |


### Configuration


| File             | Purpose                                                |
| ---------------- | ------------------------------------------------------ |
| `pyproject.toml` | Package deps: scipy, numpy, fastapi, uvicorn, pydantic |


### Tests (270 total: 260 pass + 10 skip without API key)


| File                         | Tests | Coverage                                                                    |
| ---------------------------- | ----- | --------------------------------------------------------------------------- |
| `test_intent_interpreter.py` | 19    | All 11 handlers, validation, priority, multi-intent                         |
| `test_interpret_api.py`      | 6     | /interpret and /interpret/schema endpoints                                  |
| `test_e2e_pcr_pipeline.py`   | 12    | Full pipeline: interpret → optimize → verify placements                     |
| `test_llm_skill.py`          | 10    | Real LLM fuzzy input → structured output (needs ANTHROPIC_API_KEY)          |
| `test_constraints.py`        | 30    | Constraint evaluation, hard/soft, graduated penalties, crossing penalty     |
| `test_optimizer.py`          | 50    | DE optimizer, vector encoding, bounds, discrete angles, strategies          |
| `test_mock_checkers.py`      | 15    | MockCollisionChecker, MockReachabilityChecker                               |
| `test_ros_checkers.py`       | 40    | MoveIt2/IKFast adapter tests                                                |
| `test_seeders.py`            | 12    | Force-directed seeder presets                                               |
| `test_device_catalog.py`     | 25    | Device loading, footprint merging                                           |
| `test_obb.py`                | 18    | OBB geometry functions, segment intersection                                |
| `test_bugfixes_v2.py`        | 28    | Regression: duplicate IDs, orientation, min_spacing, cardinal snap defaults |
| `test_broad_phase.py`        | 5     | Sweep-and-prune AABB broad phase                                            |


---

## 8. How to Run

### Quick Start

```bash
# Install
pip install -e ".[dev]"

# Run server
UNI_LAB_ASSETS_DIR=/Users/tyf/uni-lab-assets \
uvicorn unilabos.layout_optimizer.server:app --host 0.0.0.0 --port 8000 --reload

# Run server with debug logging (shows DE cost breakdown per generation)
LAYOUT_DEBUG=1 uvicorn unilabos.layout_optimizer.server:app --host 0.0.0.0 --port 8000 --reload

# Run tests
pytest unilabos/layout_optimizer/tests/ -v

# Run LLM skill tests (needs API key)
ANTHROPIC_API_KEY=sk-... pytest unilabos/layout_optimizer/tests/test_llm_skill.py -v
```

**Log files**: All requests are logged to `unilabos/layout_optimizer/logs/{YYYYMMDD_HHMMSS}.log` at DEBUG level (frontend polling GET /scene/placements excluded).

### Dependencies

- Python ≥ 3.10
- scipy, numpy, fastapi, uvicorn, pydantic
- Optional: anthropic (for LLM skill tests)
- Optional: python-fcl (for real collision checking, not needed for mock mode)

### Environment Variables


| Variable                     | Default                                   | Purpose                                                                       |
| ---------------------------- | ----------------------------------------- | ----------------------------------------------------------------------------- |
| `UNI_LAB_ASSETS_DIR`         | `../uni-lab-assets`                       | Path to device 3D models                                                      |
| `UNI_LAB_OS_DEVICE_MESH_DIR` | `Uni-Lab-OS/unilabos/device_mesh/devices` | Registry device meshes                                                        |
| `LAYOUT_CHECKER_MODE`        | `mock`                                    | `mock` or `moveit` for checker selection                                      |
| `LAYOUT_DEBUG`               | (unset)                                   | Set to `1` for DEBUG-level console logging (DE cost breakdown per generation) |
| `ANTHROPIC_API_KEY`          | (none)                                    | For LLM skill tests                                                           |


---

## 9. Known Limitations

1. **Mock reachability**: `MockReachabilityChecker` uses 100m fallback for unknown arm IDs — effectively "always reachable" for mock mode. Real arm reach requires `ros_checkers.py` with MoveIt2.
2. **No real LLM in tests**: `test_llm_skill.py` tests are skipped without `ANTHROPIC_API_KEY`. We verified with Claude Sonnet subagent that the skill doc produces correct output for PCR workflow scenarios.
3. **Opening data coverage**: 289/499 devices have opening direction annotations. Devices without openings default to local -Y as front with no alignment penalty.
4. **Single lab room**: No multi-room or corridor support yet. Lab is a single rectangle with optional rectangular obstacles.
5. **Intent interpreter is stateless**: It translates intents one-by-one with no cross-referencing between them. Duplicate/conflicting constraints are the LLM's responsibility to avoid.
6. `**align_weight` and `snap_cardinal` default to off**: `prefer_aligned` weight defaults to 0 (was `DEFAULT_WEIGHT_ANGLE=60`) and `snap_theta_safe` is opt-in via `snap_cardinal=True`. Both remain available when explicitly requested via `align_cardinal` intent or API param.
7. **Hybrid angle mode deprecated**: The angle-first hybrid mode (separate angle sweep + position-only DE) has been replaced by joint discrete mode as the default when `angle_granularity` is set. Joint mode snaps θ to the discrete lattice within the normal 3N DE loop.

---

## 10. Quick Verification (curl)

```bash
# 1. Health check
curl http://localhost:8000/health

# 2. Schema discovery
curl http://localhost:8000/interpret/schema | python3 -m json.tool

# 3. Interpret PCR workflow
curl -X POST http://localhost:8000/interpret \
  -H "Content-Type: application/json" \
  -d '{
    "intents": [
      {"intent": "reachable_by", "params": {"arm": "arm_slider", "targets": ["opentrons_liquid_handler", "inheco_odtc_96xl"]}, "description": "arm reaches targets"},
      {"intent": "workflow_hint", "params": {"workflow": "pcr", "devices": ["thermo_orbitor_rs2_hotel", "opentrons_liquid_handler", "agilent_plateloc", "inheco_odtc_96xl"]}, "description": "PCR order"}
    ]
  }' | python3 -m json.tool

# 4. Optimize (use constraints from step 3)
curl -X POST http://localhost:8000/optimize \
  -H "Content-Type: application/json" \
  -d '{
    "devices": [
      {"id": "thermo_orbitor_rs2_hotel", "name": "Plate Hotel"},
      {"id": "arm_slider", "name": "Robot Arm", "device_type": "articulation"},
      {"id": "opentrons_liquid_handler", "name": "Liquid Handler"},
      {"id": "agilent_plateloc", "name": "Plate Sealer"},
      {"id": "inheco_odtc_96xl", "name": "Thermal Cycler"}
    ],
    "lab": {"width": 6.0, "depth": 4.0},
    "constraints": [
      {"type": "hard", "rule_name": "reachability", "params": {"arm_id": "arm_slider", "target_device_id": "opentrons_liquid_handler"}, "weight": 1.0},
      {"type": "hard", "rule_name": "reachability", "params": {"arm_id": "arm_slider", "target_device_id": "inheco_odtc_96xl"}, "weight": 1.0},
      {"type": "soft", "rule_name": "minimize_distance", "params": {"device_a": "thermo_orbitor_rs2_hotel", "device_b": "opentrons_liquid_handler"}, "weight": 3.0},
      {"type": "soft", "rule_name": "minimize_distance", "params": {"device_a": "opentrons_liquid_handler", "device_b": "agilent_plateloc"}, "weight": 3.0},
      {"type": "soft", "rule_name": "minimize_distance", "params": {"device_a": "agilent_plateloc", "device_b": "inheco_odtc_96xl"}, "weight": 3.0}
    ],
    "workflow_edges": [
      ["thermo_orbitor_rs2_hotel", "opentrons_liquid_handler"],
      ["opentrons_liquid_handler", "agilent_plateloc"],
      ["agilent_plateloc", "inheco_odtc_96xl"]
    ],
    "run_de": true,
    "angle_granularity": 4,
    "maxiter": 100,
    "seed": 42
  }' | python3 -m json.tool
```

---

## 11. Demo Setup

This section documents the device processing pipeline, test frontend, and LLM agent demo for the layout optimizer.

### 11.1 Device Processing Pipeline

How devices go from 3D meshes to collision footprints:

1. **Source data**:
  - `uni-lab-assets/` repository: GLB/STL 3D models + XACRO robot descriptions
  - `Uni-Lab-OS/device_mesh/devices/` registry: device metadata directories
2. **Extraction** (`extract_footprints.py`):
  - Load meshes via `trimesh` (STL for geometry, GLB for display)
  - Compute oriented bounding box (OBB): width, depth, height
  - Apply GLB root node rotation to align with world frame
  - Detect openings from XACRO `<joint type="fixed">` elements containing "socket" in name
  - Compute opening direction: centroid of socket origins → cardinal direction mapping
  - Manual overrides for devices with non-standard opening patterns (`MANUAL_OPENINGS` dict)
  - Write results to `footprints.json` (499 devices, 183KB)
3. **Catalog merging** (`device_catalog.py`):
  - Load `footprints.json` (OBB + openings)
  - Load `uni-lab-assets/data.json` (asset tree structure)
  - Load `Uni-Lab-OS/device_mesh/devices/` (registry devices)
  - Merge: registry devices get priority for metadata, but assets' 3D model paths preferred
  - Fallback sizes: `KNOWN_SIZES` dict provides manual dimensions when trimesh extraction fails
4. **Standalone filtering** (`server.py:_is_standalone_device`):
  - Bbox >30cm = device (standalone equipment)
  - Bbox <5cm = consumable (plates, tubes, tips)
  - 5-30cm = keyword heuristic (check name for "plate", "tube", "tip", "rack")

### 11.2 Test Frontend (`static/lab3d.html`)

Interactive 3D lab layout visualization and design tool (1250 lines).

**Technology stack**:

- Three.js v0.169.0 (ES modules from esm.sh CDN)
- WebGL renderer with PCF soft shadow maps, ACES filmic tone mapping
- OrbitControls for camera interaction

**Features**:

- **Device library**: Left sidebar with search/filter, toggle between devices and consumables
- **Drag-to-add**: Click device in library → adds to scene with random position
- **Selected devices panel**: Right panel lists all placed devices, click to remove
- **Lab dimensions**: Width × Depth inputs (meters), collision margin slider
- **View modes**: 3D perspective (default) and top-down orthographic
- **Grid system**: 0.5m grid with lab boundary highlighting
- **Device visualization**: Box geometry with emissive materials, edge highlights, CSS2D labels
- **Opening markers**: Orange arrows and semi-transparent strips showing device access directions
- **Auto Layout button**: Calls `POST /optimize` with current devices + constraints
- **Scene polling**: 1-second polling of `GET /scene/placements` for agent-pushed updates (version-based change detection)
- **Auto-create from placements**: If a pushed placement references a device not yet in the scene, the poller creates it automatically from the catalog — the agent no longer requires the user to manually add devices first (see "Scene polling behavior" below)
- **Smooth animation**: Lerp interpolation for device placement changes

**Backend integration**:

- `GET /devices` — Load device catalog on startup
- `POST /optimize` — Send devices + constraints, receive placements
- `POST /scene/lab` — Push lab dimensions when changed
- `GET /scene/placements` — Poll every 1s for agent-pushed updates

**Key JavaScript functions**:

- `loadDeviceCatalog()` — Fetch device list, build catalog with color pool
- `createDeviceMesh(deviceId, uuid)` — Create Three.js Group with body, edges, opening markers
- `addDevice(deviceId)` / `removeDevice(uuid)` — Manage selected devices
- `runLayout()` — Call backend `/optimize` or local bin packing fallback
- `animatePlacement(uuid, tx, tz, theta)` — Smooth lerp to target position
- `setView('3d' | 'top')` — Switch camera perspective

#### Scene polling behavior (auto-create + uuid matching)

**Change date:** 2026-06 · **File:** `static/lab3d.html` (Scene Poller, `setInterval(... , 1000)`)

**Purpose:** Let an agent drive the 3D scene end-to-end. Previously the poller only
*repositioned* devices that the user had already added manually; if `POST /scene/placements`
referenced a device that wasn't in `state.selected`, the poller silently skipped it (`if (!entry) continue`),
so an empty scene stayed empty. Now the agent can push placements onto a fresh scene and the
frontend renders them without any manual "add to scene" step.

**What the poller does for each placement (`p`):**

1. **Match by `p.uuid` first** — primary key, so the same catalog device can later appear as
   multiple distinct instances (multi-instance ready).
2. **Fallback match by `p.device_id`** among entries not yet claimed in this batch — preserves
   the previous behavior for devices the user added manually in the UI.
3. **Auto-create if still unmatched** — when the device exists in the loaded `CATALOG`, call
   `addDeviceToScene(device_id, p.uuid)`, register `{device_id, uuid}` in `state.selected`, then
   animate it into place. Creation uses the placement's own `uuid` so repeated polls are
   idempotent (no duplicates) and future multi-instance pushes work.

**Safeguards:**

- Skips the whole tick until `CATALOG` is loaded (avoids creating meshless entries on cold start).
- A per-batch `claimed` set prevents several same-type placements from all snapping onto the
  first existing instance — required for correct multi-instance creation.
- Skips a placement whose `device_id` is unknown to the catalog, or whose mesh fails to build.
- Updates `state.placements` and refreshes the sidebar (`updateUI()`) only when something was created.

**Not changed:** manual add/remove via the device library, the version-based change detection,
and the "delete = clear scene" semantics. The poller still does **not** remove devices that the
backend stops reporting (server placements are not treated as the full source of truth).

### 11.3 LLM Agent Demo (`llm_skill/demo_agent.md`)

LLM agent orchestration instructions for natural language lab layout design.

**Agent workflow**:

1. `GET /devices` — Fetch device catalog for context
2. Parse user natural language request
3. Build structured intents JSON (using `layout_intent_translator.md` skill)
4. `POST /interpret` — Translate intents to constraints
5. `POST /optimize` — Run layout optimization
6. `POST /scene/placements` — Push results to shared scene state
7. Frontend auto-updates via polling (no manual refresh needed)

**Example user requests**:

- "Design a PCR lab with robot arm automation, keep it compact"
- "Place liquid handler, thermal cycler, and plate sealer. Arm must reach all devices."
- "Add a plate hotel, make sure it's close to the liquid handler"

### 11.4 Running the Demo

```bash
# Start the server
uvicorn unilabos.layout_optimizer.server:app --host 0.0.0.0 --port 8000 --reload

# Open in browser
# http://localhost:8000/

# Use Claude Code with demo_agent.md skill to orchestrate via natural language
# The agent will call the API endpoints and push results to /scene/placements
# The frontend will automatically update via polling
```

**Demo flow**:

1. Open `http://localhost:8000/` in browser
2. Frontend loads device catalog and displays 3D scene
3. Use Claude Code with `demo_agent.md` skill to send natural language requests
4. Agent translates request → intents → constraints → optimization → scene update
5. Frontend polls `/scene/placements` every 1s and animates changes
6. User can manually add/remove devices or adjust lab size in the UI
7. Click "Auto Layout" to re-optimize with current devices

