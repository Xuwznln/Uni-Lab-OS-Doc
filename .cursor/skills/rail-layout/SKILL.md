---
name: rail-layout
description: Orchestrate deterministic rail-mounted robot-arm lab layout for linear (single-instance) experiment workflows — resolve the user's workflow devices to catalog footprints, run the feasibility check, compute arm/stack/instrument coordinates analytically, and report each device's lab position + cardinal rotation. Does NOT push to the 3D frontend. Use when the user wants to lay out a rail/gantry robot-arm workstation where instruments are arranged around arms following workflow order, or mentions 导轨机械臂/导轨布局/机械臂周围布仪器/线性流程布局/rail layout. For random constraint-satisfaction layouts use the sibling skill `lab-layout-optimizer` instead.
---

# Rail-Mounted Robot-Arm Layout Orchestrator

You orchestrate **deterministic analytical layout** for rail-mounted robot-arm labs: arms are placed along the long wall, stacks sit between adjacent arms, and instruments are packed around each arm in workflow order. Unlike `lab-layout-optimizer`, this does NOT run differential evolution — coordinates are computed exactly from distance parameters.

> Scope: **linear experiment workflows with no duplicate instruments** (一种仪器只有一台). Multi-instance handling is a later phase.

## What this skill delivers

The **final deliverable is the coordinates table** — for every instrument, arm, and stack, its position `(x, y, z)` in the lab and its rotation, **restricted to 0 / 90 / 180 / 270 degrees**. This skill does **NOT** push placements to the 3D frontend. There is no `POST /scene/placements` step and no viewer.

## Prerequisites

- Device name resolution rules are bundled in [device_name_resolution_zh.md](device_name_resolution_zh.md). **Read it before Step 1** — it tells you how to match the user's workflow devices (informal names) to exact catalog footprint IDs, and how to split them into arm / stack / ordered instruments.
- This skill auto-detects and, if needed, starts the layout optimizer server (compute-only; see Step 0).

## CRITICAL OUTPUT RULES

- During the pipeline, output ONLY short status lines. No markdown fences. No raw JSON. No explanations.
- Every HTTP call uses `curl -s` (silent). Never show curl output to the user.
- Parse responses internally; extract only the fields needed.
- Server base URL: `http://localhost:8000`
- The ONE rich output allowed is the **final coordinates table** in Step 5.
- At the END of every reply, print the stop hint (see "Stopping the server").

## Default distance parameters

Defined centrally in `rail_layout.DEFAULT_PARAMS` (meters), user-overridable via the request `params` field:

- `a=0.5` arm short-side to wall · `b=0.2` arm long-side to instrument · `c=0.3` instrument-to-instrument · `d=0.3` instrument to wall · `e=0.2` arm to stack
- Hard reachability convention: `b < working_radius` and `e < working_radius`.
- Working radius defaults to `0.3m` (TODO: replace with a per-model `rail_arm_models.json` lookup).
- Default stack model `thermo_stacker` (real bbox/openings from `footprints.json`); user may override with `stack_model`.

## Pipeline

### Step 0 — Ensure server is running (compute only)

Resolve `UNI_LAB_ASSETS_DIR` (look for an open workspace folder named `uni-lab-assets`, else fall back to `/Users/tyf/uni-lab-assets`) and print it first:
```
UNI_LAB_ASSETS_DIR: <resolved-path>
```
Check `GET /health`. If `200`, print `server already running at localhost:8000`. Otherwise start it NON-blocking in the `unilab` conda env and poll until ready:
```
conda run -n unilab \
  env UNI_LAB_ASSETS_DIR=<resolved-path> \
  uvicorn unilabos.layout_optimizer.server:app --host 0.0.0.0 --port 8000
```
```
for i in $(seq 1 30); do curl -s http://localhost:8000/health && break; sleep 1; done
```
Print `server ready at localhost:8000`. Do NOT open the browser/viewer (no frontend in this skill).

### Step 1 — Retrieve devices and resolve names

```
curl -s http://localhost:8000/devices
```
Using the rules in `device_name_resolution_zh.md`, resolve the user's workflow into three groups:
- **arm** — the rail robot arm (`device_type: articulation` or keyword match).
- **stack** — `thermo_stacker` by default, or the user-specified model.
- **ordered_instruments** — the remaining instruments, **kept in workflow order** (order matters).

Print a concise resolution table (only the relevant devices):
```
resolving devices...
  arm    → arm_slider
  stack  → thermo_stacker (default)
  flow   → thermo_orbitor_rs2_hotel → opentrons_liquid_handler → agilent_plateloc → inheco_odtc_96xl
```
If a name is ambiguous, use the **AskQuestion** tool to disambiguate; do not guess.

### Step 2 — Lab dimensions

Use the dimensions the user gave. If none, read the current scene:
```
curl -s http://localhost:8000/scene/lab
```
Returns `{"width": W, "depth": D}`. Do not print anything for this step.

### Step 3 — Feasibility check

POST the resolved devices + ordered instrument ids + lab (+ optional `params` / `arm_model` / `stack_model`):
```
curl -s -X POST http://localhost:8000/rail/feasibility \
  -H "Content-Type: application/json" \
  -d '{ "devices": [...], "ordered_instruments": ["dev_a", "dev_b", ...], "lab": {"width": W, "depth": D} }'
```
Branch on the response:
- `feasible: true` → print `feasibility ok — N arms, M stacks (n_max=K)`, go to Step 4.
- `feasible: false` → print each `reasons[]` line, then use the **AskQuestion** tool to let the user relax (enlarge room / fewer instruments / shorter rail / adjust params), pairing options to `suggestions[]`. Re-run Step 3 after they answer.

### Step 4 — Compute layout

```
curl -s -X POST http://localhost:8000/rail/layout \
  -H "Content-Type: application/json" \
  -d '{ "devices": [...], "ordered_instruments": [...], "lab": {"width": W, "depth": D}, "mode": "near_wall" }'
```
`mode` is `near_wall` (default) or `centered` (only if the user explicitly asks AND short-side inequality 1 holds). Capture `placements` (instruments), `arms`, `stacks`, and `conflicts`. The server already handles 多退少补 (drop empty arms / add an arm when instruments remain) internally.

If `conflicts` is non-empty (e.g. `unplaced_instruments`, `out_of_bounds`, `obstacle_collision`), print each conflict `message`, then use **AskQuestion** to relax (from `suggestion`). Otherwise print `layout computed — N instruments, A arms, S stacks`.

### Step 5 — Report coordinates (final deliverable)

Convert every rotation from radians to **cardinal degrees** (see "Rotation normalization") and print ONE table covering all instruments + arms + stacks. Use the workflow order for instruments, then arms (arm1..), then stacks (stack1..). Positions in meters.

```
布局结果（坐标单位 m，朝向单位 °，仅取 0/90/180/270）:
类型    设备                         x       y      z     rotation
仪器    thermo_orbitor_rs2_hotel    0.40    0.58   0.00   90
仪器    opentrons_liquid_handler    0.40    0.90   0.00   90
...
机械臂  arm1                        1.00    1.00   0.00   0
堆栈    stack1                      1.00    1.90   0.00   0
```

Field sources from the `/rail/layout` response:
- **instruments** → `placements[].position` and `placements[].rotation.z`.
- **arms** → `arms[].center` (x, y; z = 0) and `arms[].theta`.
- **stacks** → `stacks[].center` (x, y; z = 0); rotation = the arm rotation (stack footprint is aligned to the rail), normalized to a cardinal value.

## Rotation normalization (0 / 90 / 180 / 270)

Server angles are radians and always a multiple of π/2. Convert and snap:
1. `deg = round(theta_rad * 180 / π)`
2. `deg = ((deg % 360) + 360) % 360`  → maps e.g. `-90 → 270`
3. Snap to the nearest of `{0, 90, 180, 270}` (defensive; values are already cardinal).

Always present rotation as one of `0`, `90`, `180`, `270`.

## Division of labor (script vs agent)

- "Which arm / which side / which instruments / coordinates / orientation" → fully deterministic, computed by `rail_layout.py`. Do NOT ask the LLM to decide these.
- Agent role = high-level orchestration only: resolve device names → feasibility → layout → report coordinates.
- Multi-退少补 is handled server-side inside `/rail/layout`; do not orchestrate it manually.

## Stopping the server

If this skill started the server, it NEVER stops it. End every reply with:
```
停止服务请运行: lsof -ti:8000 | xargs kill
```

## Example full output

For input: "导轨布局：流程是 板架 → 移液 → 封板 → PCR，arm_slider 负责转运，房间 4×8m"

```
UNI_LAB_ASSETS_DIR: /Users/tyf/uni-lab-assets
server ready at localhost:8000

resolving devices...
  arm    → arm_slider
  stack  → thermo_stacker (default)
  flow   → thermo_orbitor_rs2_hotel → opentrons_liquid_handler → agilent_plateloc → inheco_odtc_96xl

feasibility ok — 1 arms, 0 stacks (n_max=3)
layout computed — 4 instruments, 1 arms, 0 stacks

布局结果（坐标单位 m，朝向单位 °，仅取 0/90/180/270）:
类型    设备                         x       y      z     rotation
仪器    thermo_orbitor_rs2_hotel    0.46    0.84   0.00   90
仪器    opentrons_liquid_handler    0.45    1.55   0.00   90
仪器    agilent_plateloc            3.55    1.30   0.00   270
仪器    inheco_odtc_96xl            3.58    0.93   0.00   270
机械臂  arm1                        2.00    1.00   0.00   0

停止服务请运行: lsof -ti:8000 | xargs kill
```

## Boundaries (avoid confusion with the optimizer)

- Do NOT use `/optimize`, `/optimize/auto`, or any DE seeders here.
- Reused server I/O: `GET /devices`, `GET /scene/lab`, `POST /rail/feasibility`, `POST /rail/layout`, optional `POST /rail/validate`.
- Two skills coexist: random constraint-satisfaction → `lab-layout-optimizer`; deterministic rail linear flow → `rail-layout`.
