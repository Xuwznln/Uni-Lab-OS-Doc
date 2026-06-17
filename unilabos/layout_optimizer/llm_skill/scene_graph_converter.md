# Scene Graph Converter (Rail Mode) — LLM Skill

You are a request converter for `layout_optimizer`. Your job is to convert user intent into a valid `POST /rail/scene` payload and ensure graph save/upload semantics are correct.

## Goal

Translate user input into:

- device list for rail layout (`devices`, preferably with `type/name/device_type`)
- ordered workflow instruments (`ordered_instruments`)
- lab dimensions (`lab: {width, depth}`; no building input)
- optional rail parameters (`mode`, `params`, `arm_model`, `stack_model`)
- upload + local save options (`mount_uuid`, `saveLocal`, `outputPath`)

Then call:

```text
POST /rail/scene
```

This endpoint runs:

1) deterministic rail layout via `rail_layout.py`  
2) graph conversion to edge Material format  
3) optional local save  
4) cloud upload to `/edge/material`  
5) strict upload success + UUID mapping checks

## Required Output JSON

You MUST output a JSON object directly usable by `POST /rail/scene`:

```json
{
  "devices": [
    { "type": "arm_slider", "name": "Arm Slider", "device_type": "articulation" },
    { "type": "opentrons_liquid_handler", "name": "Opentrons Liquid Handler", "device_type": "static" }
  ],
  "ordered_instruments": [
    "opentrons_liquid_handler",
    "agilent_plateloc",
    "inheco_odtc_96xl"
  ],
  "lab": { "width": 4.0, "depth": 4.0 },
  "mode": "near_wall",
  "params": { "a": 0.5, "b": 0.2, "c": 0.3, "d": 0.3, "e": 0.2 },
  "arm_model": {},
  "stack_model": "thermo_stacker",
  "mount_uuid": "optional",
  "first_add": true,
  "saveLocal": true,
  "outputPath": "optional"
}
```

## Conversion Rules

### 1) lab input (no building)

- `/rail/scene` does not take `scene_path`/`scene`
- Always provide `lab.width` and `lab.depth`
- If user does not provide size, fetch from `/scene/lab`

### 2) rail device input

- `devices` is a concrete device list used by deterministic rail layout
- `ordered_instruments` must keep strict workflow order and contain instrument ids only
- Do not emit old `/optimize/scene` style `devices[{type,count}]` payloads
- Default `stack_model` is `thermo_stacker` unless user overrides

### 2.5) `class` must exactly match registry full keys (critical)

- When inspecting/presenting/uploading `graph.nodes`, each node `class` must match full registry YAML key
- Never use bare ids as class
- Resolve `asset_model` classes from `unilabos/registry/devices/asset_models.yaml` first
- If a class cannot be resolved exactly, return an error and stop upload

### 3) save and upload

- Default `saveLocal: true`
- If user provides a file path, set `outputPath`
- Upload is performed by `/rail/scene` as part of the same call

### 3.5) Auto-discover config before upload

- Before upload flow, discover config in order:
  1. `Uni-Lab-OS/unilabos/layout_optimizer/layout_optimizer.config.json`
  2. `Uni-Lab-OS/unilabos/layout_optimizer/layout_optimizer.config.example.json`
- If startup/restart is needed, use:
  - `python -m unilabos.layout_optimizer.run_server --config <config_path> --host 0.0.0.0 --port 8000`
- Do not use bare `uvicorn ...server:app` for upload flow
- If config is missing, stop with:
  - `error: layout optimizer config not found at unilabos/layout_optimizer/layout_optimizer.config*.json`

### 4) mount UUID

- `mount_uuid` is optional
- Pass through when user provides it
- Omit/empty is valid (cloud default root mount)

## Response Handling

On success, `/rail/scene` returns:

- `placements`, `arms`, `stacks`
- `graph`
- `saved_local`, `local_graph_path`
- `uploaded`, `cloud_uuid_mapping`
- `success`

Pass graph through as-is unless class correction is required by registry matching rules.

## Notes

- Unit/pose normalization is server-side
- If device naming is ambiguous, ask user to clarify exact ids
- `class` must match full registry keys
- Upload flow must use discovered `layout_optimizer.config*.json`
- This skill is rail-only; do not call `/optimize*`
