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

**0.2 Check if the server is already running:**
```
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health
```
- If it returns `200`, the server is already up. Print `server already running at localhost:8000`, then go to Step 1. Do NOT start it again and do NOT open the browser.
- Otherwise, continue to 0.3.

**0.3 Start the server (background) in the `unilab` conda env.** Run from the `Uni-Lab-OS` repo root, with `UNI_LAB_ASSETS_DIR` set to the path resolved in 0.1. Launch it as a NON-blocking background process (do not block on it):
```
conda run -n unilab \
  env UNI_LAB_ASSETS_DIR=<resolved-path> \
  uvicorn unilabos.layout_optimizer.server:app --host 0.0.0.0 --port 8000
```
Do NOT use `--reload` for the background process. Print `starting server (conda env: unilab)...`.

**0.4 Wait for readiness.** Poll `/health` until it succeeds, up to ~30 seconds:
```
for i in $(seq 1 30); do curl -s http://localhost:8000/health && break; sleep 1; done
```
Print `server ready at localhost:8000`. If it never becomes ready, print `error: server failed to start` and stop.

**0.5 Open the viewer (first start only).** Because this skill just launched the server, open the 3D viewer ONCE:
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

### Step 4 — Optimize layout

Build the optimize request using:
- `devices`: the relevant devices from Step 1 (id, name, device_type)
- `lab`: the `{"width": W, "depth": D}` from Step 3.5
- `constraints`: from Step 3 interpret response
- `workflow_edges`: from Step 3 interpret response
- `seeder`: `"compact_outward"` (default)
- `seeder_overrides`: generally not needed. Cardinal alignment is handled by the `align_cardinal` intent (generates `prefer_aligned` constraint). Do NOT use `align_weight` in seeder_overrides — it is deprecated.
- `snap_cardinal`: `false` (default). Set `true` only if user explicitly requests snapping to 0/90/180/270.
- `run_de`: `true`
- `maxiter`: `200`
- `seed`: `42`

Run:
```
curl -s -X POST http://localhost:8000/optimize \
  -H "Content-Type: application/json" \
  -d '{ ... }'
```

Print:
```
optimizing layout (DE, 200 iterations)...
optimization complete — cost: X.XX, success: true/false
```

If `success` is false, print:
```
error: optimization failed (cost: inf) — constraints may conflict
```
And stop.

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
- Optimize fails: `error: optimization failed (cost: inf) — constraints may conflict`
- After any error, stop and wait for user input.

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

optimizing layout (DE, 200 iterations)...
optimization complete — cost: 0.00, success: true

applying placements to scene...
layout applied — 5 devices positioned

停止服务请运行: lsof -ti:8000 | xargs kill
```
