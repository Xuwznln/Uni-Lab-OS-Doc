# Layout Optimizer Handover

**Date**: 2026-03-25 | **Branch**: `feat/layout_optimize_end2end_test` | **Commit**: `1c811ff` | **Tests**: 193 (183 pass + 10 LLM skip w/o API key)

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
    │     scipy differential_evolution              │
    │     3N-dim: [x0, y0, θ0, x1, y1, θ1, ...]   │
    │     Cost = hard_penalties + soft_penalties      │
    │     Graduated collision penalties (not binary) │
    │                                               │
    │  4. θ snap            (optimizer.snap_theta)  │
    │     Snap near-cardinal angles to 0/90/180/270 │
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

Returns all 10 intent types with parameter specs. LLM agent should call this before translating.

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
  "angle_granularity": 4,
  "maxiter": 200,
  "seed": 42
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
  "de_ran": true
}
```

`position`/`rotation` format matches Cloud's `CommonPositionType`. `rotation.z` is θ in radians.
`angle_granularity` is optional and opt-in. Supported values are `4`, `8`, `12`, `24`. When set, the optimizer uses an angle-first hybrid mode: snap all device angles onto the global lattice, greedily sweep angles, then run DE on `x/y` only. `4` is the practical axis-aligned mode for tidy lab layouts.

### `GET /devices` — Device catalog

Returns all known devices with bbox, openings, model paths. The LLM agent should receive this list as context so it can resolve fuzzy device names.

### `GET /health`

Returns `{"status": "ok"}`.

---

## 3. Intent Types (10 total)

| Intent | Params | Generates | Type |
|--------|--------|-----------|------|
| `reachable_by` | `arm` (str), `targets` (list[str]) | `reachability` per target | hard |
| `close_together` | `devices` (list[str]), `priority` (low/medium/high) | `minimize_distance` per pair | soft |
| `far_apart` | `devices` (list[str]), `priority` | `maximize_distance` per pair | soft |
| `max_distance` | `device_a`, `device_b`, `distance` (float m) | `distance_less_than` | hard |
| `min_distance` | `device_a`, `device_b`, `distance` (float m) | `distance_greater_than` | hard |
| `min_spacing` | `min_gap` (float m, default 0.3) | `min_spacing` | hard |
| `workflow_hint` | `workflow` (str), `devices` (ordered list[str]) | `minimize_distance` consecutive + `workflow_edges` | soft |
| `face_outward` | (none) | `prefer_orientation_mode` outward | soft |
| `face_inward` | (none) | `prefer_orientation_mode` inward | soft |
| `align_cardinal` | (none) | `prefer_aligned` | soft |

Priority weights: `low=1.0`, `medium=3.0`, `high=8.0`.

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

| Rule Name | Params | What it checks |
|-----------|--------|---------------|
| `no_collision` | (default, always on) | OBB-SAT pairwise collision between all devices |
| `within_bounds` | (default, always on) | All devices within lab boundary |
| `reachability` | `arm_id`, `target_device_id` | Target center within arm reach radius |
| `distance_less_than` | `device_a`, `device_b`, `distance` | OBB edge-to-edge distance ≤ threshold |
| `distance_greater_than` | `device_a`, `device_b`, `distance` | OBB edge-to-edge distance ≥ threshold |
| `min_spacing` | `min_gap` | All device pairs have ≥ min_gap edge-to-edge |

### Soft Constraints (weighted penalty)

| Rule Name | Params | What it minimizes |
|-----------|--------|------------------|
| `minimize_distance` | `device_a`, `device_b` | OBB edge-to-edge distance × weight |
| `maximize_distance` | `device_a`, `device_b` | 1/(distance+ε) × weight |
| `prefer_orientation_mode` | `mode` (outward/inward) | Angle between opening direction and ideal direction |
| `prefer_aligned` | (none) | Deviation from nearest 90° angle |
| `prefer_seeder_orientation` | (none) | Deviation from seeder-assigned θ |

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
| File | Lines | Purpose |
|------|-------|---------|
| `models.py` | 96 | Dataclasses: Device, Lab, Placement, Constraint, Intent, Opening |
| `device_catalog.py` | — | Loads devices from footprints.json + uni-lab-assets + registry |
| `footprints.json` | 183KB | 499 device bounding boxes, heights, openings (offline extracted) |
| `seeders.py` | — | Force-directed initial layout with presets |
| `optimizer.py` | 196 | scipy DE optimizer, 3N encoding, seed injection, θ snap |
| `constraints.py` | — | Unified constraint evaluation (hard + soft + graduated) |
| `intent_interpreter.py` | 315 | 10 intent handlers, pure translation, no side effects |
| `obb.py` | — | OBB geometry: corners, overlap SAT, min_distance, penetration_depth |
| `server.py` | 510 | FastAPI: /interpret, /interpret/schema, /optimize, /devices |

### Reference / Utilities
| File | Purpose |
|------|---------|
| `extract_footprints.py` | How footprints.json was generated (offline STL/GLB → 2D bbox extraction via trimesh) |

> **Note**: `optimizer.py` and `seeders.py` import `pencil_integration` for fallback initial layout. This module is removed from the handover (not part of the pipeline — see D2). Replace with your own initial layout or simply rely on the seeder (`seeders.py`), which is the primary code path.

### Integration Layer
| File | Purpose |
|------|---------|
| `interfaces.py` | Protocol definitions for CollisionChecker / ReachabilityChecker |
| `mock_checkers.py` | Dev-mode checkers (OBB collision, Euclidean reachability) |
| `ros_checkers.py` | MoveIt2/IKFast adapters for real collision + reachability |

### LLM
| File | Purpose |
|------|---------|
| `llm_skill/layout_intent_translator.md` | System prompt for LLM: intent schema, device resolution, translation rules, examples |

### Configuration
| File | Purpose |
|------|---------|
| `pyproject.toml` | Package deps: scipy, numpy, fastapi, uvicorn, pydantic |

### Tests (193 total: 183 pass + 10 skip without API key)
| File | Tests | Coverage |
|------|-------|----------|
| `test_intent_interpreter.py` | 19 | All 10 handlers, validation, priority, multi-intent |
| `test_interpret_api.py` | 6 | /interpret and /interpret/schema endpoints |
| `test_e2e_pcr_pipeline.py` | 12 | Full pipeline: interpret → optimize → verify placements |
| `test_llm_skill.py` | 10 | Real LLM fuzzy input → structured output (needs ANTHROPIC_API_KEY) |
| `test_constraints.py` | — | Constraint evaluation, hard/soft, graduated penalties |
| `test_optimizer.py` | — | DE optimizer, vector encoding, bounds |
| `test_mock_checkers.py` | — | MockCollisionChecker, MockReachabilityChecker |
| `test_ros_checkers.py` | — | MoveIt2/IKFast adapter tests |
| `test_seeders.py` | — | Force-directed seeder presets |
| `test_device_catalog.py` | — | Device loading, footprint merging |
| `test_obb.py` | — | OBB geometry functions |
| `test_bugfixes_v2.py` | — | Regression: duplicate IDs, orientation, min_spacing |

---

## 8. How to Run

### Quick Start
```bash
# Install
pip install -e ".[dev]"

# Run server
uvicorn layout_optimizer.server:app --host 0.0.0.0 --port 8000 --reload

# Run tests
pytest layout_optimizer/tests/ -v

# Run LLM skill tests (needs API key)
ANTHROPIC_API_KEY=sk-... pytest layout_optimizer/tests/test_llm_skill.py -v
```

### Dependencies
- Python ≥ 3.10
- scipy, numpy, fastapi, uvicorn, pydantic
- Optional: anthropic (for LLM skill tests)
- Optional: python-fcl (for real collision checking, not needed for mock mode)

### Environment Variables
| Variable | Default | Purpose |
|----------|---------|---------|
| `UNI_LAB_ASSETS_DIR` | `../uni-lab-assets` | Path to device 3D models |
| `UNI_LAB_OS_DEVICE_MESH_DIR` | `Uni-Lab-OS/unilabos/device_mesh/devices` | Registry device meshes |
| `LAYOUT_CHECKER_MODE` | `mock` | `mock` or `moveit` for checker selection |
| `ANTHROPIC_API_KEY` | (none) | For LLM skill tests |

---

## 9. Known Limitations

1. **Mock reachability**: `MockReachabilityChecker` uses 100m fallback for unknown arm IDs — effectively "always reachable" for mock mode. Real arm reach requires `ros_checkers.py` with MoveIt2.

2. **No real LLM in tests**: `test_llm_skill.py` tests are skipped without `ANTHROPIC_API_KEY`. We verified with Claude Sonnet subagent that the skill doc produces correct output for PCR workflow scenarios.

3. **Opening data coverage**: 289/499 devices have opening direction annotations. Devices without openings default to local -Y as front with no alignment penalty.

4. **Single lab room**: No multi-room or corridor support yet. Lab is a single rectangle with optional rectangular obstacles.

5. **Intent interpreter is stateless**: It translates intents one-by-one with no cross-referencing between them. Duplicate/conflicting constraints are the LLM's responsibility to avoid.

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
