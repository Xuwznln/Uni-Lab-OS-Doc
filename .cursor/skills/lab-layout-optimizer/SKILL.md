---
name: lab-layout-optimizer
description: Orchestrate lab device layout optimization from a natural language request — retrieve devices, translate intent to optimizer constraints, run the optimization, and push placements to the 3D frontend. Use when the user wants to lay out / place / arrange lab devices, set up a workflow layout (PCR, liquid handling, plate sealing, etc.), optimize a lab floor plan, or mentions 布局/排布/摆放/工作站布局/layout optimizer/3D 场景.
---

# Lab Layout Orchestrator

You are a lab layout agent. Your job is to take a natural language lab request, translate it into optimizer constraints, run the optimization, and push results to the 3D frontend — all while outputting only concise, readable status lines.

## Prerequisites

- This skill auto-detects and, if needed, starts the layout optimizer server (see Step 0). You do NOT need to start it manually.
- Intent translation rules live in the bundled reference [layout_intent_translator.md](layout_intent_translator.md). Read it before Step 2.

## CRITICAL OUTPUT RULES

- Output ONLY short status lines. No markdown fences. No raw JSON. No explanations.
- Every HTTP call uses `curl -s` (silent). Never show curl output to the user.
- Parse responses internally. Extract only the fields needed for your status lines.
- Server base URL: `http://localhost:8000`
- At the END of EVERY reply, always print the stop hint (see "Stopping the server"). This skill only starts the server — it never stops it.

## Pipeline

Execute these steps in order. Print the status line shown after each step.

### Step 0 — Ensure server is running

**0.1 Resolve the assets directory.** Look through the open workspace folders for one named `uni-lab-assets`. If found, use its absolute path. Otherwise fall back to `/Users/tyf/uni-lab-assets`. At the VERY START of your reply (before any other status line), print which path you will use:
```
UNI_LAB_ASSETS_DIR: <resolved-path>
```

**0.2 Resolve layout optimizer config path.** In `Uni-Lab-OS/unilabos/layout_optimizer/`, look for config in this order:
1) `layout_optimizer.config.json`
2) `layout_optimizer.config.example.json`

If found, print:
```
LAYOUT_OPTIMIZER_CONFIG: <resolved-path>
```
If neither exists, print:
```
LAYOUT_OPTIMIZER_CONFIG: not found
```

**0.3 Check if the server is already running:**
```
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health
```
- If it returns `200`, the server is already up. Print `server already running at localhost:8000`, then go to Step 1. Do NOT start it again and do NOT open the browser.
- Otherwise, continue to 0.4.

**0.4 Start the server (background) in the `unilab` conda env.** Run from the `Uni-Lab-OS` repo root, with `UNI_LAB_ASSETS_DIR` set to the path resolved in 0.1.

If config path from 0.2 exists, launch with `run_server --config` (preferred for cloud upload readiness):
```
conda run -n unilab \
  env UNI_LAB_ASSETS_DIR=<resolved-path> \
  python -m unilabos.layout_optimizer.run_server \
  --config <resolved-config-path> \
  --host 0.0.0.0 --port 8000
```

If config path is missing, fallback to:
```
conda run -n unilab \
  env UNI_LAB_ASSETS_DIR=<resolved-path> \
  uvicorn unilabos.layout_optimizer.server:app --host 0.0.0.0 --port 8000
```
Do NOT use `--reload` for the background process. Print `starting server (conda env: unilab)...`.

**0.5 Wait for readiness.** Poll `/health` until it succeeds, up to ~30 seconds:
```
for i in $(seq 1 30); do curl -s http://localhost:8000/health && break; sleep 1; done
```
Print `server ready at localhost:8000`. If it never becomes ready, print `error: server failed to start` and stop.

**0.6 Open the viewer (first start only).** Because this skill just launched the server, open the 3D viewer ONCE:
```
open http://localhost:8000/
```
Print `opening viewer... http://localhost:8000/`. Remember internally that this skill started the server. On follow-up requests (server already running), do NOT open the browser again.

### Step 1 — Retrieve devices

Run:
```
curl -s http://localhost:8000/devices
```

Filter to `is_standalone: true` entries. Count them. Build an id→name lookup.

Print:
```
retrieving devices... N standalone devices found
```

Then print an id mapping table showing the user-friendly name → device_id for devices relevant to the user's request:
```
id mapping:
  plate hotel    → thermo_orbitor_rs2_hotel
  robot arm      → arm_slider
  liquid handler → opentrons_liquid_handler
  plate sealer   → agilent_plateloc
  pcr machine    → inheco_odtc_96xl
```

Only include devices that are relevant to the user's request, not the full catalog.

### Step 2 — Translate intent to constraints

Using the rules in `layout_intent_translator.md` (which you have already read), translate the user's natural language request into an intents JSON structure.

Do NOT print the JSON. Instead, print a human-readable constraint summary:
```
translating intent to constraints...
constraints:
  hard: arm_slider must reach 4 devices
  hard: min spacing 0.05m between all devices
  soft: workflow order hotel → liquid handler → sealer → pcr
  soft: all devices close together (high priority)
  soft: align to cardinal directions
```

### Step 3 — Interpret intents

Send the intents JSON to the interpret endpoint:
```
curl -s -X POST http://localhost:8000/interpret \
  -H "Content-Type: application/json" \
  -d '{ "intents": [...] }'
```

Capture the `constraints` and `workflow_edges` arrays from the response. Do NOT print anything for this step — it's a silent validation.

If `errors` is non-empty, print:
```
warning: N intents failed to translate
```

### Step 3.5 — Read lab dimensions

```
curl -s http://localhost:8000/scene/lab
```

Returns `{"width": W, "depth": D}`. Use these values for the optimize request. Do NOT print anything for this step.

### Step 4 — Optimize layout (auto self-healing)

Use the **self-healing** endpoint `POST /optimize/auto`. It does three things server-side, so you do NOT orchestrate retries yourself:

1. **Analytical pre-check (situation A).** Before running any DE, it checks for *deterministic* conflicts whose feasible region is provably empty (total device area > lab area, a device that cannot fit, `max_distance < min_distance`, `min_distance > lab diagonal`, `max_distance < min_spacing`). If any are found it **short-circuits** without running DE and returns them in `conflicts`.
2. **Parallel multi-start DE (situation B).** Otherwise it runs the `seeds × seeders` grid in **parallel processes**. The FIRST start that finds a feasible layout wins and the others are killed immediately.
3. **Aggregated culprits.** If every start fails, it returns the hard constraints that stayed violated across ALL runs in `violations` (`persistent: true` ones are the binding culprits).

Build the request using:
- `devices`: the relevant devices from Step 1 (id, name, device_type)
- `lab`: the `{"width": W, "depth": D}` from Step 3.5
- `constraints`: from Step 3 interpret response
- `workflow_edges`: from Step 3 interpret response
- `seeds`: `[42, 7, 123, 2024]` (multi-start diversity)
- `seeders`: `["compact_outward", "spread_inward", "workflow_cluster"]`
- `maxiter`: `400` (fixed large value; DE early-stops on its own — do NOT sweep maxiter as a grid axis)
- `snap_cardinal`: `false` (default). Set `true` only if user explicitly requests snapping to 0/90/180/270.
- `seeder_overrides`: generally not needed. Cardinal alignment is handled by the `align_cardinal` intent. Do NOT use `align_weight` — it is deprecated.

Run:
```
curl -s -X POST http://localhost:8000/optimize/auto \
  -H "Content-Type: application/json" \
  -d '{ ... }'
```

Print:
```
optimizing layout (parallel multi-start DE)...
```

Then branch on the response:

**(a) `success: true`** — print and go to Step 5:
```
optimization complete — cost: X.XX, seeder: <winner>, tried X/Y starts
```

**(b) `success: false` and `conflicts` non-empty (situation A — constraints can't all be satisfied):** Do NOT apply placements. Print one line per conflict using its `message`, then **use the AskQuestion tool** to ask the user how to relax. Build the options from each conflict's `suggestion` (e.g. enlarge the lab, remove/replace a device, increase a `max_distance`, decrease a `min_distance`/`min_spacing`, or drop one constraint). Example status lines:
```
optimization failed — hard constraints conflict (no valid layout exists):
  [area] total device area 32.0㎡ exceeds lab 25.0㎡
```
Then call AskQuestion with concrete relax choices. After the user answers, re-run from Step 2 with the adjusted intents/lab.

**(c) `success: false` and `conflicts` empty (situation B — all parallel starts failed):** Do NOT apply placements. Print the `persistent: true` entries from `violations` (these are the hard constraints that never reached zero across every start), then **use the AskQuestion tool** to ask the user to relax the named constraint(s). Example:
```
optimization failed after Y parallel starts — persistent violation:
  reachability(arm_slider, inheco_odtc_96xl)
```
Then call AskQuestion targeting that constraint (e.g. drop the reachability target, increase arm reach, or reduce other constraints crowding it).

### Step 5 — Apply placements

Take the `placements` array from the optimize response and POST them. Do NOT add a `location` field — the backend schema only accepts `device_id`, `uuid`, `position`, and `rotation`. Extra fields will cause validation errors.

```
curl -s -X POST http://localhost:8000/scene/placements \
  -H "Content-Type: application/json" \
  -d '{ "placements": [
    {
      "device_id": "...",
      "uuid": "...",
      "position": {"x": ..., "y": ..., "z": ...},
      "rotation": {"x": ..., "y": ..., "z": ...}
    }
  ] }'
```

**Important — version-based polling:** The frontend polls `GET /scene/placements` every 1 second and uses a version number to detect changes. On the **first poll**, it captures the current version as a baseline and does **not** apply placements. It only renders placements when the version **increases beyond** that baseline. This means if you POST placements before the frontend has polled once, the frontend will silently skip that update.

**Solution:** After the initial POST, send the **same request a second time** to bump the version. This guarantees the frontend sees a version increase after its baseline poll and applies the placements.

**Note — no manual scene setup needed (since 2026-06):** The frontend now **auto-creates** any device referenced by a placement that isn't already in the scene (matched by `uuid`, fallback `device_id`). You can push placements onto a completely empty scene and they will render — the user does not have to add devices from the library first. See README §11.2 "Scene polling behavior".

Print:
```
applying placements to scene...
layout applied — N devices positioned
```

## Follow-up Requests

If the user gives a follow-up request (e.g., "now move the sealer farther from the thermal cycler"):

1. Print a `---` separator
2. Keep the same device list (no need to re-fetch)
3. Translate the NEW request into intents — these REPLACE the previous constraints entirely
4. Run Steps 3–5 again with the new constraints
5. Same output format

## Error Handling

- Server unreachable: `error: server unreachable at localhost:8000`
- Optimize failure is handled by the Step 4 branches (a)/(b)/(c) — the server already retried in parallel and diagnosed the cause, so do NOT manually re-POST with different seeds. Instead surface `conflicts`/`violations` and use AskQuestion to let the user relax.
- After a hard conflict (situation A) or exhausted multi-start (situation B), do NOT apply placements; wait for the user's relax decision, then re-run from Step 2.

## Output Rules Exception — AskQuestion on failure

The "only short status lines" rule still holds for the happy path. The ONE exception: when `/optimize/auto` returns `success: false`, after printing the short diagnostic lines you MUST call the AskQuestion tool to collect the user's relaxation choice. Derive the options from `conflicts[].suggestion` (situation A) or the `persistent` `violations` (situation B).

## AskQuestion Templates (failure relaxation)

Use these fixed templates verbatim (substitute real device names from the conflict/violation payload). Always make the first option the recommended one and append " (Recommended)". The tool always appends an "Other" free-text path, so do NOT add your own "Other".

### Situation A — `conflicts` non-empty (deterministic infeasibility)

Emit ONE AskQuestion `questions[]` entry per conflict, keyed by `conflict.kind`:

| `kind` | `prompt` | `options` (id → label) |
|---|---|---|
| `area` | "Total device footprint exceeds the lab — it physically cannot fit. How should I relax this?" | `enlarge` → Enlarge the lab (Recommended) · `remove` → Remove a device · `smaller` → Swap in smaller-footprint devices |
| `device_too_large` | "Device '{device}' cannot fit in the current lab in any orientation. How should I handle it?" | `enlarge` → Enlarge the lab (Recommended) · `remove` → Remove this device · `smaller` → Use a smaller model |
| `distance_contradiction` | "'{a}' and '{b}' are required to be both ≤ {max}m and ≥ {min}m apart — contradictory. Which do I relax?" | `relax_max` → Loosen the max distance (Recommended) · `relax_min` → Loosen the min distance · `drop_one` → Drop one of the two |
| `min_distance_exceeds_lab` | "'{a}' and '{b}' must be ≥ {min}m apart, exceeding the lab diagonal. How should I relax this?" | `relax_min` → Reduce the min distance (Recommended) · `enlarge` → Enlarge the lab · `drop` → Drop the min-distance rule |
| `max_distance_below_min_spacing` | "'{a}' and '{b}' must be ≤ {max}m apart, below the global min spacing. How should I relax this?" | `relax_max` → Increase the max distance (Recommended) · `relax_spacing` → Reduce min_spacing · `drop_one` → Drop one rule |

### Situation B — all parallel starts failed (`violations` with `persistent: true`)

Emit ONE AskQuestion entry targeting the top `persistent` violation, keyed by `violation.rule`:

| `rule` | `prompt` | `options` (id → label) |
|---|---|---|
| `reachability` | "The arm can never reach '{target}' under the other constraints. How should I relax this?" | `drop_target` → Drop this reachability target (Recommended) · `inc_reach` → Increase the arm's reach · `loosen_others` → Loosen constraints crowding it (min_spacing/far_apart) · `enlarge` → Enlarge the lab |
| `min_spacing` | "The global min spacing is too large for the devices to fit. How should I relax this?" | `relax_spacing` → Reduce min_spacing (Recommended) · `enlarge` → Enlarge the lab · `remove` → Remove a device |
| `distance_greater_than` | "The min distance between '{a}' and '{b}' can't be met together with the rest. How should I relax this?" | `relax_min` → Reduce that min distance (Recommended) · `drop` → Drop the rule · `enlarge` → Enlarge the lab |
| `distance_less_than` | "The max distance between '{a}' and '{b}' can't be met together with the rest. How should I relax this?" | `relax_max` → Increase that max distance (Recommended) · `drop` → Drop the rule |
| `no_collision` / `within_bounds` | "Devices can't be arranged without overlap/overflow in this lab. How should I relax this?" | `enlarge` → Enlarge the lab (Recommended) · `remove` → Remove a device · `relax_spacing` → Reduce min_spacing |

After the user answers, apply the chosen relaxation to the intents (or to the lab dimensions via `POST /scene/lab`) and re-run from Step 2 with the adjusted request.

## Stopping the server

This skill starts the server but NEVER stops it — the user stops it manually. At the END of every reply, always print this stop hint (in Chinese) as the final line:
```
停止服务请运行: lsof -ti:8000 | xargs kill
```

## Device Name Resolution

You have `layout_intent_translator.md` loaded as context. Use its device name resolution rules to match user's informal names (e.g., "PCR machine", "the arm", "liquid handler") to exact device IDs from the catalog retrieved in Step 1.

## Example Full Output

For input: "Set up a PCR workflow — hotel, liquid handler, sealer, thermal cycler. The arm handles all transfers. Keep it compact."

```
UNI_LAB_ASSETS_DIR: /Users/tyf/uni-lab-assets
starting server (conda env: unilab)...
server ready at localhost:8000
opening viewer... http://localhost:8000/

retrieving devices... 47 standalone devices found

id mapping:
  plate hotel    → thermo_orbitor_rs2_hotel
  robot arm      → arm_slider
  liquid handler → opentrons_liquid_handler
  plate sealer   → agilent_plateloc
  pcr machine    → inheco_odtc_96xl

translating intent to constraints...
constraints:
  hard: arm_slider must reach 4 devices
  soft: workflow order hotel → liquid handler → sealer → pcr
  soft: all devices close together (high priority)
  soft: align to cardinal directions

optimizing layout (parallel multi-start DE)...
optimization complete — cost: 0.00, seeder: compact_outward, tried 1/12 starts

applying placements to scene...
layout applied — 5 devices positioned

停止服务请运行: lsof -ti:8000 | xargs kill
```
