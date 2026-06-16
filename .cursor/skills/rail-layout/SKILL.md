---
name: rail-layout
description: Orchestrate deterministic rail-mounted robot-arm lab layout for linear (single-instance) experiment workflows — fetch devices, run the feasibility check, compute arm/stack/instrument coordinates analytically, and push placements to the 3D frontend. Use when the user wants to lay out a rail/gantry robot-arm workstation where instruments are arranged around arms following workflow order, or mentions 导轨机械臂/导轨布局/机械臂周围布仪器/线性流程布局/rail layout. For random constraint-satisfaction layouts use the sibling skill `lab-layout-optimizer` instead.
---

# Rail-Mounted Robot-Arm Layout Orchestrator

You orchestrate **deterministic analytical layout** for rail-mounted robot-arm labs: arms are placed along the long wall, stacks sit between adjacent arms, and instruments are packed around each arm in workflow order. Unlike `lab-layout-optimizer`, this does NOT run differential evolution — coordinates are computed exactly from distance parameters.

> Scope: **linear experiment workflows with no duplicate instruments** (一种仪器只有一台). Multi-instance handling is a later phase.

> STATUS (M0 — public infrastructure / 阶段0): the core algorithm functions in
> `rail_layout.py` and the server endpoints are skeletons. `/rail/feasibility`,
> `/rail/layout`, and `/rail/validate` currently return HTTP **501** until M1~M3
> are implemented. Until then, use this skill to validate the pipeline wiring,
> not to produce real placements.

## CRITICAL OUTPUT RULES

- Output ONLY short status lines. No markdown fences. No raw JSON. No explanations.
- Every HTTP call uses `curl -s` (silent). Never show curl output to the user.
- Server base URL: `http://localhost:8000`
- At the END of every reply, print the stop hint (see "Stopping the server").

## Shared infrastructure (reused from `lab-layout-optimizer`)

This skill reuses the same server I/O — only the `/rail/*` deterministic endpoints differ from `/optimize`:

| Capability | Endpoint | Use |
|---|---|---|
| Device catalog (bbox, openings, footprint) | `GET /devices` | arm/instrument sizes + orientation |
| Room dimensions | `GET /scene/lab` | feasibility long/short-side inequalities |
| Push coordinates to frontend | `POST /scene/placements` (send twice for version bump) | render layout |
| Environment obstacles | `LabSpec.obstacles` | collision guard input |

## Default distance parameters

Defined centrally in `rail_layout.DEFAULT_PARAMS` (meters), user-overridable:

- `a=0.5` arm short-side to wall · `b=0.2` arm long-side to instrument · `c=0.3` instrument-to-instrument · `d=0.3` instrument to wall · `e=0.2` arm to stack
- Hard reachability convention: `b < working_radius` and `e < working_radius`.
- Working radius defaults to `0.3m` (TODO: replace with a per-model `rail_arm_models.json` lookup).

## Pipeline

### Step 0 — Ensure server is running

Same as `lab-layout-optimizer` Step 0: resolve `UNI_LAB_ASSETS_DIR`, check `GET /health`, and if down start it in the `unilab` conda env:
```
conda run -n unilab \
  env UNI_LAB_ASSETS_DIR=<resolved-path> \
  uvicorn unilabos.layout_optimizer.server:app --host 0.0.0.0 --port 8000
```
Poll `/health` until ready. Open `http://localhost:8000/` once on first start.

### Step 1 — Retrieve devices

```
curl -s http://localhost:8000/devices
```
Identify the rail robot arm(s) and the workflow instruments. Build an id→name lookup and print only the devices relevant to the request.

### Step 2 — Read lab dimensions

```
curl -s http://localhost:8000/scene/lab
```
Returns `{"width": W, "depth": D}`.

### Step 3 — Feasibility check

POST the ordered instrument ids (workflow order) + lab + optional param overrides:
```
curl -s -X POST http://localhost:8000/rail/feasibility \
  -H "Content-Type: application/json" \
  -d '{ "devices": [...], "ordered_instruments": ["dev_a", "dev_b", ...], "lab": {"width": W, "depth": D} }'
```
Branch on the response:
- `feasible: true` → print `feasibility ok — N arms, M stacks (n_max=K)`, go to Step 4.
- `feasible: false` → print each `reasons[]` line, then use the **AskQuestion** tool to let the user relax (enlarge room / fewer instruments / shorter rail / adjust params), pairing options to `suggestions[]`. Re-run Step 3 after they answer.
- HTTP `501` → the algorithm is not implemented yet (M0); print `rail feasibility not implemented yet (M0)` and stop.

### Step 4 — Compute layout

```
curl -s -X POST http://localhost:8000/rail/layout \
  -H "Content-Type: application/json" \
  -d '{ "devices": [...], "ordered_instruments": [...], "lab": {"width": W, "depth": D}, "mode": "near_wall" }'
```
`mode` is `near_wall` (default) or `centered` (only if the user explicitly asks AND short-side inequality 1 holds). Capture `placements`.
- HTTP `501` → print `rail layout not implemented yet (M0)` and stop.

### Step 5 — Apply placements

POST `placements` to `POST /scene/placements`, then send the **same request a second time** to bump the version (the frontend polls and only renders on a version increase). The frontend auto-creates any referenced device, so an empty scene is fine.
```
applying placements to scene...
layout applied — N devices positioned
```

## Division of labor (script vs agent)

- "Which arm / which side / which instruments / coordinates / orientation" → fully deterministic, computed by `rail_layout.py`. Do NOT ask the LLM to decide these.
- Agent role = high-level orchestration only: fetch data → feasibility → layout → (optional) validate → push to frontend.
- Multi-退少补 (drop empty arms / add an arm when instruments remain) is handled server-side inside `/rail/layout`; do not orchestrate it manually.

## Stopping the server

This skill starts the server but NEVER stops it. End every reply with:
```
停止服务请运行: lsof -ti:8000 | xargs kill
```
