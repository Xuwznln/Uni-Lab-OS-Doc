# Sirna RPC Action Split And Sync Cleanup Implementation Plan

Date: 2026-05-25

## Goal

Update the Sirna Bioyond workstation action surface so small operational actions are normal RPC-backed nodes instead of manual-confirm bundles:

- Convert `get_order_list` to a normal action with a required status dropdown and visible/defaulted optional query fields.
- Add dedicated `take_out(...)` and `cancel_experiment(...)` public actions.
- Remove `cancel_experiment_and_take_out(...)` entirely.
- Replace public `resync_external_materials(...)` with `sync_from_external(...)`; do not keep a wrapper alias.
- Remove old public `reset(...)` per `2026-05-25_sirna_rpc_reuse_handover.md` and keep reset/take-out/sync behavior decoupled.
- Remove Sirna-specific synchronizer runtime installation and related stock reagent-as-liquid sync paths.
- Keep station actions thin: validate public inputs, call typed `BioyondV1RPC` methods, normalize results.

## Sources

- Main handover: `plan/2026-05-25_sirna_rpc_reuse_handover.md`
- Peptide plan style reference: `temp_benyao/peptide/_plans/2026-05-16_02_peptide_cleanup_implementation_v2.md`
- Sirna station: `unilabos/devices/workstation/bioyond_studio/sirna_station/sirna_station.py`
- Shared RPC: `unilabos/devices/workstation/bioyond_studio/bioyond_rpc.py`
- Shared Bioyond station/synchronizer: `unilabos/devices/workstation/bioyond_studio/station.py`

Current relevant locations at planning time:

- `BioyondSirnaStation`: `sirna_station.py:284`
- old `reset`: `sirna_station.py:425`
- old `resync_external_materials`: `sirna_station.py:460`
- old `cancel_experiment_and_take_out`: `sirna_station.py:527`
- current `get_order_list`: `sirna_station.py:1392`
- `_run_shared_external_material_sync`: `sirna_station.py:2145`
- reset helper with take-out coupling: `sirna_station.py:2223`
- `_extract_takeout_targets`: `sirna_station.py:2585`
- `_take_out_remaining_materials`: `sirna_station.py:2612`
- `SirnaResourceSynchronizer`: `sirna_station.py:4300`
- `BioyondResourceSynchronizer.sync_from_external`: `station.py:151`
- `order_query`: `bioyond_rpc.py:684`
- typed `take_out`: `bioyond_rpc.py:785`
- single `cancel_experiment`: `bioyond_rpc.py:855`
- `batch_cancel_experiment`: `bioyond_rpc.py:875`
- no-data `reset_location`: `bioyond_rpc.py:418`
- no-data `reset_order_status`: `bioyond_rpc.py:935`

## Evidence Review Updates

This section records the 2026-05-25 GPT-5.5 high evidence pass. Source code and current Sirna API schemas win over older handovers and temp plans.

Current source snapshot:

- The target action surface is not implemented yet. Current public Sirna actions still include old `reset`, `resync_external_materials`, and `cancel_experiment_and_take_out`.
- Current public Sirna actions do not include `take_out`, `cancel_experiment`, `sync_from_external`, `reset_auto`, or `reset_manual`.
- Current `resync_external_materials` is a normal action, not a manual-confirm action.
- Current `get_order_list` is still `NodeType.MANUAL_CONFIRM`, has optional plain-string `status`, hides `timeType` / `beginTime` / `endTime` / `skipCount` / `sorting`, and exposes `timeout_seconds` / `assignee_user_ids`.
- Current `cancel_experiment_and_take_out` calls single `rpc.cancel_experiment(resolved_order_id)` and optionally take-out; it does not call `batch_cancel_experiment(order_codes)`.
- Current reset still performs order/location lookup, can call take-out through `_take_out_remaining_materials`, and then calls `_run_shared_external_material_sync`.
- Current hidden per-action credential mutation via `_update_runtime_api_config(api_host, api_key)` is still active in `reset`, `resync_external_materials`, scheduler actions, `cancel_experiment_and_take_out`, `start_experiment`, `get_order_report`, and `get_aggregated_order_report`.

API/schema corrections:

- Reset location endpoint is `POST /api/lims/storage/reset-location`, not `/api/lims/location/reset-location`.
- Current `BioyondV1RPC.reset_location(...)` and `reset_order_status(...)` already send no `data` key. The older handover sentence saying this repo still sends reset `data` is stale.
- OpenAPI schemas still describe reset wrappers as `BaseInputDto[String]` with nullable `data`; the no-data behavior is supported by current source and manual examples, not by schema alone.
- `/api/lims/order/batch-cancel-experiment` request `data` is `array[string]`. The Sirna manual describes it as experiment/order codes and shows a value like `BSO2026041300004`, so Sirna should expose `order_codes`.
- Current `/api/lims/order/order-list` schema/manual does not prove that each row contains `orderCode`. Existing source reads `item.get("orderCode")`, but this is live/undocumented behavior unless confirmed from runtime logs.
- Current `/api/lims/storage/material-info` OpenAPI schema shows a direct `GetMaterialRespDto`, but a live Sirna test returned a service envelope with `code`, `data`, `error`, `message`, `path`, and `timestamp`. Treat live runtime evidence as stronger for this endpoint and keep the report RPC envelope plan, while documenting the schema/runtime mismatch in tests and comments.

Registry/action-schema mechanics:

- `Literal[...]` becomes JSON Schema `enum` and is dropdown-capable if the frontend renders JSON Schema enums.
- Because separate enum labels are not supported in the current registry path, use readable enum values such as `全部（""）` and map those strings internally to Bioyond status values.
- A parameter with no default is required. `Optional[str] = None` is optional, but the AST schema path strips `Optional` and does not emit `None` as a default. If the frontend must show JSON `null` defaults for `beginTime` / `endTime`, use explicit `goal_default={"beginTime": None, "endTime": None}`.
- Action handles are not inferred from parameters or return values. Chaining ports must be declared explicitly with matching `ActionInputHandle` / `ActionOutputHandle`.

Synchronizer/resource-tree corrections:

- Base `BioyondResourceSynchronizer.sync_from_external()` fetches stock `typeMode` 0/1/2, updates `hardware_interface.material_cache`, then calls `resource_bioyond_to_plr(...)`, which mutates the deck/warehouse tree. It does not publish by itself.
- `SirnaResourceSynchronizer` partitions labware vs reagent-liquid rows and attaches reagent liquid rows, but it does not update `material_cache`, does not call base sync, and does not publish.
- Current Sirna `reset()` and `resync_external_materials()` already bypass the installed Sirna subclass by constructing a fresh `BioyondResourceSynchronizer(self)` inside `_run_shared_external_material_sync`.
- Removing `SirnaResourceSynchronizer` is accepted in this cleanup. Automatic stock reagent-as-liquid attachment is intentionally dropped rather than moved into a shared/base hook in this pass.

## User Decisions From Interview

1. `get_order_list` keeps the public name `max_results`; internally map it to Bioyond `pageCount`.
2. `status` should render as dropdown choices using user-facing strings `全部（""）`, `成功（80）`, `失败（90）`, `执行中（60）`, `已取出（100）`, then map internally to `""`, `"80"`, `"90"`, `"60"`, `"100"`.
3. Optional `get_order_list` fields should be visible after required fields and show defaults:
   - `filter`: empty string.
   - `sorting`: `"creationTime desc"`.
   - `skipCount`: `0`.
   - `timeType`: empty string; description lists `CreationTime` and `FinishedTime`.
   - `beginTime`: default `None` / JSON `null`; description shows example format such as `2026-05-26T13:50:54.742373`.
   - `endTime`: default `None` / JSON `null`; description says it must be later than `beginTime` and shows the same timestamp format.
4. `take_out` must follow the updated shared RPC wrapper.
5. Remove `cancel_experiment_and_take_out` entirely.
6. `cancel_experiment` should only call the batch-cancel experiment API; no take-out, no sync side effect. In Sirna, wrap this explicitly as an `order_codes` input because the batch-cancel API expects orderCode values for this station.
7. Need to document what Sirna synchronizer adds before removing it.
8. Do not keep `resync_external_materials` as a compatibility wrapper.
9. `get_order_list` should expose `order_codes` as an output handle.
10. `cancel_experiment` should accept only `order_codes`; do not accept `order_id` and do not resolve `order_id` to codes inside the cancel action.
11. Dropping automatic stock reagent-as-liquid sync from `SirnaResourceSynchronizer` is acceptable in this cleanup.
12. Reset should be able to call sync only through an explicit optional checkbox; default should keep reset and sync separate.
13. `auto_register_materials` stays as a visible optional submit checkbox, defaults `True`, and when checked calls `sync_from_external(publish_resource_tree=True)` once after successful order creation/result parsing.
14. `take_out(order_id)` directly calls `rpc.take_out(order_id, [], [])` when optional ID lists are omitted. It does not query an order snapshot or extract targets by default.
15. `get_order_report` and `get_aggregated_order_report` become normal read-only actions.
16. Report aggregation keeps rich Bioyond response envelopes by adding `return_envelope: bool = False` to existing `BioyondV1RPC` methods; Sirna product flow must not use station-local raw report POST fallback.
17. Debug CLI/direct HTTP helpers may stay only when explicitly gated by `DEBUG_CLI_ENABLED`; non-debug product routes must be `/api/lims/*` or `/report/*`.
18. Remove hidden per-action `api_host` / `api_key` overrides from all Sirna actions, including scheduler/start/report actions.
19. Submit/start handles follow Peptide style: `order_id`, `order_ids`, and `resultTable`.
20. `parameter_overrides` must be a structured list of bundled `m`, `n`, `Key`, `Value` entries; old text forms are rejected.
21. Sirna warehouse/deck layout should change stored coordinates using `frontend_y_flip=True` for both deck children and warehouse sites; do not add new `flip_y` serialized metadata.
22. `reset_devices` defaults `False`.
23. Main repo tests should be updated/added; `temp_benyao/sirna/tests` are evidence/diagnostics and are not push targets.
24. Remove legacy submit/start frontend-compat handles now. Keep only `order_id`, `order_ids`, and `resultTable` for submit/start handles in this cleanup.
25. Follow current Peptide station config behavior: Bioyond API credentials come from station config / RPC construction, not action goal kwargs. Sirna actions must not accept or mutate per-action `api_host` / `api_key`; update missing-config messages so they tell users to fix station config rather than action goals.
26. If `sync_from_external` or auto-sync is requested while the station has no deck, skip the sync and return warning/error metadata. A missing deck is a non-blocking sync precondition failure, not a reason to reintroduce Sirna local resource mutation.
27. Optional reset sync runs only after all selected reset operations succeed. If selected reset calls fail, skip `sync_from_external_after_reset` and add a warning.
28. `get_order_list` exposes both scalar `order_code` and list `order_codes`. Most order flows use `order_code`; only batch cancel consumes `order_codes`.

## Required Public Action Surface

Final Sirna public actions in this scope:

| Action | Status | Node Type | RPC / helper |
| --- | --- | --- | --- |
| `get_order_list` | modify | normal | `rpc.order_query(...)` |
| `get_order_report` | modify | normal | `rpc.order_report(..., return_envelope=True)` |
| `get_aggregated_order_report` | modify | normal | report-related RPC methods with `return_envelope=True` |
| `take_out` | new | normal | `rpc.take_out(...)` |
| `cancel_experiment` | new | normal | `rpc.batch_cancel_experiment(order_codes)` |
| `sync_from_external` | new/rename | normal | installed/repaired base `self.resource_synchronizer.sync_from_external()` |
| `reset_auto` | from handover | normal | shared reset helper |
| `reset_manual` | from handover | manual_confirm | shared reset helper gated by `physical_cleanup_confirmed` |
| scheduler actions | keep/thin | normal | inherited RPC methods |

Public actions to remove:

- `reset`
- `resync_external_materials`
- `cancel_experiment_and_take_out`

Do not keep compatibility aliases unless a future migration decision explicitly reverses this plan.

## Configuration Contract

Follow the current Peptide station pattern for Bioyond credentials and RPC access:

- Require `api_host` / `api_key` through station configuration and `BioyondV1RPC` construction.
- Do not expose `api_host` or `api_key` on Sirna action signatures, action `goal_default`, hidden kwargs, or action docs.
- Remove `_update_runtime_api_config(...)` once no kept action uses per-call credential mutation.
- Update lazy/missing-config messages so they say to fix station config or graph config; do not tell users to pass credentials through action goals.
- Product actions should get the RPC client through `self.hardware_interface` or local helpers such as `_require_hardware_interface(...)`.
- If constructor-time strict validation cannot be applied because Sirna still supports light import/test modes, action preflight should fail clearly before RPC calls when required config is missing.

## Function Inventory And Refactor Map

### Public Actions

| Function | Current | Target |
| --- | --- | --- |
| `get_order_list` | manual-confirm action with `timeout_seconds` / `assignee_user_ids`; hides query fields | normal read-only action; status required; optional query fields visible with defaults |
| `get_order_report` | report read action with hidden credential overrides | normal read-only action; typed RPC only; no per-action credentials |
| `get_aggregated_order_report` | report aggregator with station-local raw fallback helpers | normal read-only action; uses typed RPC envelope support; partial sections return `section_errors` / warnings |
| `cancel_experiment_and_take_out` | manual-confirm combined cancel plus take-out | delete completely |
| `take_out` | absent public action | add dedicated normal action |
| `cancel_experiment` | absent public action | add dedicated normal action calling batch cancel |
| `resync_external_materials` | public normal sync action with hidden `api_host` / `api_key` kwargs | replace with `sync_from_external`; no alias |
| `reset` | old public reset action | replace by `reset_auto` / `reset_manual` from handover |

### Helpers To Keep

| Helper | Reason |
| --- | --- |
| `_order_items` | Useful normalization for order-list results. |
| `_query_order_snapshot` | Keep only if another kept product path still uses it; dedicated `take_out(order_id)` does not need it. |
| `_publish_resource_tree_update` | Needed by `sync_from_external(publish_resource_tree=True)`. |
| `_resolve_material_record_to_warehouse` family | Still used by submit-time `resultTable` warehouse resolution; do not remove unless result-table resolution is replaced. |

### Helpers To Simplify

| Helper | Target |
| --- | --- |
| `_run_shared_external_material_sync` | Rename or reshape into `_sync_from_external(publish_resource_tree: bool, action_name: str)`; use installed/repaired base synchronizer, not a fresh temporary object. |
| `_take_out_remaining_materials` | Remove raw POST fallback; require shared `rpc.take_out`. Optionally rename to `_run_take_out`. |
| `_run_reset_operations` | Remove take-out cleanup. Reset may sync only through `sync_from_external_after_reset`; manual and auto have different failure semantics below. |
| ID-first material registration helpers | Audit references and keep only the pieces still required by submit/manual-load/report paths. Remove reagent-as-liquid helpers when only the deleted synchronizer path uses them. |
| `_parameter_override_items` / `_apply_parameter_overrides` | Replace text/key-only override parsing with the structured `ParameterOverrideEntry` live-parameter resolver. |

### Helpers / Runtime Paths To Remove

| Code | Why |
| --- | --- |
| `post_init` replacement with `SirnaResourceSynchronizer(self)` | Creates mixed runtime sync semantics; base constructor syncs first, reset/resync already bypass Sirna subclass. |
| `SirnaResourceSynchronizer` as runtime-installed subclass | It is a partial stock sync fork and does not update material cache like base sync. |
| `SirnaResourceSynchronizer`-only helpers `_partition_external_rows` / `_attach_external_liquid_row` | Remove with the subclass. |
| station-local raw `post(...)` fallback for `/api/lims/order/take-out` | Shared typed RPC exists. |
| station-local raw report fallback helpers in product flow | Existing report endpoints are covered by `BioyondV1RPC`; add `return_envelope=True` support there instead. |
| station-local runtime `api_host` / `api_key` kwargs in Sirna actions | Actions should use existing RPC config, not per-action credential switching. |

## Expanded Removal Inventory

Sirna is larger than the Peptide target shape mainly because it has accumulated whole side systems around the core submit/start/end/order-list flow:

- stock reagent-as-liquid synchronization;
- local resource-tree mutation/registration;
- guided load/unload compatibility output tables;
- raw LIMS fallback POST helpers;
- reset/cancel/take-out bundled cleanup logic;
- a full frontend-style report aggregator.

The Peptide reference should be read as a style boundary: small explicit actions, typed RPC boundaries, no station-local raw LIMS fallback, no hidden credential mutation, and no speculative resource mutation. Do not copy Peptide Day1-Day4 domain details into Sirna.

### Definitely Remove In This Cleanup

| Code | Current ref | Removal reason |
| --- | --- | --- |
| old public `reset` | `sirna_station.py:432` | Replaced by `reset_auto` / `reset_manual`. |
| old public `resync_external_materials` | `sirna_station.py:464` | Replaced by `sync_from_external`; no wrapper alias. |
| old public `cancel_experiment_and_take_out` | `sirna_station.py:543` | Split into dedicated `cancel_experiment` and `take_out`; no bundled action. |
| `post_init` synchronizer swap | `sirna_station.py:371` | Runtime must not replace base synchronizer with Sirna subclass. |
| `SirnaResourceSynchronizer` | `sirna_station.py:4302` | Drops the stock reagent-as-liquid fork outright. |
| raw take-out fallback | `sirna_station.py:2612` | Shared typed `BioyondV1RPC.take_out` exists. |
| reset-time take-out probe/call | `sirna_station.py:2291` | Reset must not take out materials. |
| reset-time external sync coupling | `sirna_station.py:457` | Keep sync explicit via `sync_from_external`. |

### Keep Only Behind `DEBUG_CLI_ENABLED`

| Code | Current ref | Removal reason |
| --- | --- | --- |
| module-level direct HTTP `fetch_workflow_list` | `sirna_station.py:215` | May stay only as debug/CLI probing guarded by `DEBUG_CLI_ENABLED`; runtime should use station/RPC methods. |
| `main()` CLI | `sirna_station.py:4466` | May stay only as a debug entrypoint guarded by `DEBUG_CLI_ENABLED`. |
| `argparse`, `urllib.request`, `urllib.error`, `DEBUG_CLI_ENABLED` | file top | Keep only as support for the explicitly debug-gated CLI/direct HTTP path. |

### Likely Remove After Reference Check

| Code | Current ref | Removal condition |
| --- | --- | --- |
| `_parse_experiment1_create_result` | `sirna_station.py:2128` | No station-file call site; remove if only archived/temp tests reference it. |
| `_reset_before_experiment_create` | `sirna_station.py:2142` | No station-file call site after reset rewrite. |
| `_register_materials_to_tree` family | `sirna_station.py:3659` | `auto_register_materials` now means post-submit `sync_from_external`, not local registration mutation; remove old local mutation helpers if no kept product path uses them. |
| `_classify_material_record` | `sirna_station.py:3782` | Remove with resource-tree registration/reagent mutation path unless still called by kept code. |
| `_build_slot_labware_extra` | `sirna_station.py:4047` | Remove with resource-tree registration/reagent mutation path unless still called by kept code. |
| `_attach_liquid_to_parent` | `sirna_station.py:4064` | Remove if reference check shows it only supports deleted local registration and Sirna stock reagent-as-liquid sync. |
| `_post_lims_section` | `sirna_station.py:2828` | Remove from product report flow; use typed RPC `return_envelope=True`. |
| `_fallback_or_original_lims_section` | `sirna_station.py:2807` | Remove from product report flow with raw report fallback. |
| `_can_raw_post_lims_section` | `sirna_station.py:2825` | Remove from product report flow with raw report fallback. |
| `_update_runtime_api_config` and hidden `api_host` / `api_key` action kwargs | `sirna_station.py:1916` | Remove after actions stop accepting per-call credential mutation. |

### Simplify Or Merge

| Area | Current refs | Target |
| --- | --- | --- |
| legacy parameter override stack | `sirna_station.py:1013`, `sirna_station.py:2052`, `sirna_station.py:2071` | Replace text/key-only parsing with the structured live-parameter resolver from the handover. |
| report aggregation | `sirna_station.py:1630` plus helpers through `sirna_station.py:3097` | Make it a normal typed-RPC product action. Add `return_envelope` to existing RPC methods and remove station-local raw POST fallback from product flow. |
| manual load output compatibility | `sirna_station.py:3233`, `sirna_station.py:3258`, `sirna_station.py:3624` | Remove legacy submit/start compatibility handles in this cleanup. Keep only `order_id`, `order_ids`, and `resultTable`; if the frontend still needs sibling arrays, that is a frontend migration issue, not a station compatibility alias. |
| `batch_cancel_experiment` wrapper docs | `bioyond_rpc.py:875` | Keep the shared method name/signature for compatibility, but update docs to say the payload is a generic string list and Sirna passes experiment/order codes. |
| `auto_register_materials` public option | `sirna_station.py:147`, `sirna_station.py:964` | Keep visible/default `True`; when checked, call `sync_from_external(publish_resource_tree=True)` once after successful submit/result parsing. |

### Keep For Now

| Area | Current refs | Reason |
| --- | --- | --- |
| submit wrappers/core and workflow resolution | `sirna_station.py:668`, `sirna_station.py:833`, `sirna_station.py:1935` | Core Sirna submit flow. |
| `start_experiment` guided manual-load | `sirna_station.py:1130` | Product-facing load confirmation flow. |
| `end_experiment` guided unload/report behavior | `sirna_station.py:1306` | Product-facing unload/report flow. |
| `get_order_list` / `get_order_report` / `get_aggregated_order_report` | `sirna_station.py:1423`, `sirna_station.py:1547`, `sirna_station.py:1630` | Keep, but make actions normal/thin and typed-RPC-backed. |
| scheduler actions | `sirna_station.py:487` | Keep as thin inherited RPC wrappers. |

## Sirna Warehouse Frontend Y Flip

For Sirna warehouse/deck layout, change stored coordinates using the existing `frontend_y_flip=True` mechanism. Do not add a new serialized `flip_y` metadata flag in this pass.

Current source note:

- Sirna currently uses `BioyondWareHouse` metadata `bioyond_axis="xy_col_row"` and `bioyond_key_axis="col_row"` for `G3移液站`, `自动化堆栈`, and `离心机配平板堆栈`.
- Existing warehouse factories already support `frontend_y_flip`, which changes stored site coordinates.
- Peptide uses pre-flipped stored coordinates for frontend layout; Sirna should follow that coordinate approach now.

Implementation guidance:

- Pass `frontend_y_flip=True` from the three Sirna warehouse factory functions.
- Also flip Sirna deck child placements, not only internal warehouse site coordinates.
- Keep the change Sirna-scoped; do not alter shared Bioyond or Peptide warehouse defaults without a separate layout decision.
- Do not change warehouse identifiers, slot labels, Bioyond IDs, `bioyond_axis`, or `bioyond_key_axis`.
- Repair legacy deserialized Sirna deck children in `BIOYOND_SirnaStation_Deck._ensure_sirna_warehouse_metadata()` only as needed to preserve existing axis/key metadata; do not invent `flip_y`.
- Verify by instantiating and serializing `BIOYOND_SirnaStation_Deck(setup=True)` or a `ResourceTreeSet`, not by inspecting only `sirna_station_graph.example.json`; the graph only points at the deck class with `setup: true`.
- Add coordinate tests analogous to the Peptide layout tests to confirm both deck children and warehouse sites are pre-flipped as expected.

Acceptance:

- Sirna deck child placements and warehouse site coordinates are stored in the frontend-flipped orientation.
- Existing warehouse identifiers, slot labels, and Bioyond IDs are unchanged.
- Existing `bioyond_axis="xy_col_row"` and `bioyond_key_axis="col_row"` values are unchanged.
- Serialized Sirna warehouse nodes do not rely on a new `flip_y` flag.

## Action Handle Simplification

Simplify Sirna submit/start handles and remove legacy compatibility handles.

### Submit Outputs

For `submit_experiment`, `submit_experiment_1`, and `submit_experiment_2`, keep only Peptide-style handles:

- `order_id`
- `order_ids`
- `resultTable`

Remove legacy/temporary submit output handles such as:

- `orderID`
- `orderIDs`
- `target_device`
- `resource`
- `mount`
- `Sample`
- `Consumables`
- `Reagent`
- any temporary frontend-compat aliases not listed above

The returned payload should follow the same names: `order_id`, `order_ids`, and `resultTable`. Do not expose duplicate camel/mixed-case handles.

### Start Inputs

For `start_experiment`, keep only the input handles needed to consume submit output:

- `order_id`
- `order_ids`
- `resultTable`

Remove legacy start input handles matching the old submit aliases. Do not keep duplicate handle routes for the same data.

### Cancel / Take-Out Boundary

- `cancel_experiment` consumes `order_codes`.
- `take_out` consumes `order_id`.
- Do not make `cancel_experiment` accept or infer take-out targets.
- Do not make `take_out` consume `order_codes`.

## API Schema Contracts

### Order List

Endpoint:

```text
POST /api/lims/order/order-list
```

RPC wrapper:

```python
def order_query(self, json_str: str) -> dict:
    ...
```

Request `data`:

```json
{
  "timeType": "",
  "beginTime": null,
  "endTime": null,
  "status": "",
  "filter": "",
  "skipCount": 0,
  "pageCount": 10,
  "sorting": "creationTime desc"
}
```

Public naming:

- `max_results` maps to `pageCount`.
- Use public Python parameter `filter_text`; emit Bioyond payload key `"filter"`. Avoid naming the Python action parameter `filter`.
- `skipCount` may remain camelCase to match the API and the user request.

Status values:

| Label | Value |
| --- | --- |
| `全部（""）` | `""` |
| `成功（80）` | `"80"` |
| `失败（90）` | `"90"` |
| `执行中（60）` | `"60"` |
| `已取出（100）` | `"100"` |

Use the user-facing status strings in the action schema and map them internally to the Bioyond payload values. This makes the dropdown easier to read while keeping the RPC payload stable.

Order code caveat:

- The current OpenAPI/manual shape for `/api/lims/order/order-list` does not prove that row objects include `orderCode`.
- Product decision: expose `order_codes` from `get_order_list`.
- Current source already attempts `item.get("orderCode")`; keep normalization from row `orderCode`.
- If a row lacks `orderCode`, do not infer it from `order_id`; keep the row in `orders` with a warning/missing-code marker and omit the blank from the `order_codes` handle list.
- Do not resolve order IDs to order codes inside shared `batch_cancel_experiment`.

### Take Out

Endpoint:

```text
POST /api/lims/order/take-out
```

Shared RPC:

```python
def take_out(
    self,
    order_id: str,
    preintake_ids: list[str] | None = None,
    material_ids: list[str] | None = None,
) -> dict:
    payload = {
        "orderId": order_id,
        "preintakeIds": list(preintake_ids or []),
        "materialIds": list(material_ids or []),
    }
```

Station action accepts `order_id` and optional explicit target ID lists. If `preintake_ids` / `material_ids` are omitted, call `rpc.take_out(order_id, [], [])` directly. Do not query an order snapshot or extract targets by default.

If `preintake_ids` or `material_ids` is provided, it must already be a list. Reject a single string or any other non-list shape with a workflow-blocking input validation error.

### Cancel Experiment

Endpoint to use:

```text
POST /api/lims/order/batch-cancel-experiment
```

Shared RPC exists:

```python
def batch_cancel_experiment(self, order_ids: List[str]) -> int:
    ...
```

Keep the shared wrapper unchanged in this plan. Sirna's station action owns the orderCode-specific naming.

Sirna's station action should expose and pass `order_codes` explicitly:

```python
code = rpc.batch_cancel_experiment(list(order_codes))
```

Do not call single `/api/lims/order/cancel-experiment` unless the user later asks to keep that wrapper behavior. Do not change shared RPC semantics beyond the docstring clarification; only Sirna's station-level realization should expose `order_codes`.

Do not add order ID to orderCode resolution inside the shared RPC method. Sirna owns the `order_codes` station-level input contract.

`cancel_experiment` requires a list input. The action schema/goal default should show `order_codes=[]` so the UI shows the expected shape, but an empty list after trimming blanks is a workflow-blocking input validation error. Do not accept a single string.

### Report RPC Envelope Support

`get_order_report` and `get_aggregated_order_report` are normal read-only actions.

Existing RPC methods already cover all report aggregation endpoints:

- `order_query` -> `/api/lims/order/order-list`
- `order_report` -> `/api/lims/order/order-report`
- `gantts_by_order_id` -> `/api/lims/order/gantts-by-order-id`
- `gantt_with_simulation_by_order_id` -> `/api/lims/order/gantt-with-simulation-by-order-id`
- `material_info` -> `/api/lims/storage/material-info`

Modify these existing methods to accept `return_envelope: bool = False`:

```python
def order_report(self, order_id: str, *, return_envelope: bool = False) -> dict:
    response = self.post(...)
    if return_envelope:
        return response
    if response.get("code") == 1:
        return response.get("data") or {}
    return {}
```

Apply the same compatibility rule to the other four report aggregation methods. With `return_envelope=False`, preserve existing data-only behavior: `code == 1` returns `data`, otherwise `{}`. With `return_envelope=True`, return the full Bioyond envelope so report actions can preserve `code`, `message`, and `data`.

`material_info` note:

- The current Sirna OpenAPI schema says `/api/lims/storage/material-info` returns `GetMaterialRespDto` directly.
- A live Sirna runtime test returned a service envelope:
  `{"code": 1, "data": {...}, "error": null, "message": "", "path": "/api/lims/storage/material-info", "timestamp": ...}`.
- Implement `BioyondV1RPC.material_info(..., return_envelope=False)` against the live envelope shape and return `response["data"]` when `code == 1`.
- Keep a defensive fallback only if needed for direct-object schema compatibility, but product report flow should consume the envelope path with `return_envelope=True`.

Report actions must not use station-local raw `rpc.post(...)` fallback in product flow. Any direct/raw helpers may exist only under explicit `DEBUG_CLI_ENABLED` debug paths.

Report failure behavior:

- `get_order_report` returns `success=False` with the Bioyond envelope when `code != 1`; it does not raise for a normal service-level report failure.
- `get_aggregated_order_report` returns a partial report when secondary sections fail and includes `section_errors` / `warnings`.
- `get_aggregated_order_report.success` is `True` when the main order can be identified and the report is returned, even if secondary sections have errors.
- Only fail the whole aggregated action when the main order cannot be identified or the primary order lookup/report path cannot proceed.

### Reset Endpoints

Endpoints:

```text
POST /api/lims/order/reset-order-status
POST /api/lims/storage/reset-location
```

Current shared RPC wrappers already send only:

```json
{
  "apiKey": "...",
  "requestTime": "..."
}
```

Do not send `data` from Sirna reset actions. The OpenAPI schema still shows nullable string `data`, so document this as source/manual-confirmed no-data behavior rather than schema-only truth.

For reset operation success, return code `1` is success. Non-`1`, exceptions, or missing RPC methods are operation failures. `reset_manual` and `reset_auto` handle those failures differently in the reset section below.

### Sync From External

Public result shape:

```json
{
  "success": true,
  "action": "sync_from_external",
  "publish_resource_tree": true,
  "resource_tree_update_requested": true,
  "message": "Bioyond 资源同步成功"
}
```

Failure shape:

```json
{
  "success": false,
  "action": "sync_from_external",
  "publish_resource_tree": true,
  "resource_tree_update_requested": false,
  "message": "Bioyond 资源同步失败",
  "error": "..."
}
```

Deck/config preconditions:

- `sync_from_external` requires a configured RPC client and a station deck because base sync calls `resource_bioyond_to_plr(..., deck=self.workstation.deck)`.
- If RPC config is missing, fail clearly with a config error that points to station/graph config, not action kwargs.
- If the deck is missing, skip sync and return a non-blocking result such as:

```json
{
  "success": false,
  "skipped": true,
  "action": "sync_from_external",
  "publish_resource_tree": true,
  "resource_tree_update_requested": false,
  "message": "Bioyond 资源同步跳过：station deck 未初始化",
  "warnings": ["deck_not_initialized"]
}
```

- Submit-time `auto_register_materials=True`, `reset_auto(..., sync_from_external_after_reset=True)`, and `reset_manual(..., sync_from_external_after_reset=True)` should include this skipped sync result as warning metadata and should not reintroduce local Sirna resource mutation.

### Submit `auto_register_materials`

`auto_register_materials` remains a visible optional checkbox on submit optional params and defaults to `True`.

When checked:

- Run normal submit/create-order flow first.
- Parse order ids and allocation/result data.
- Call the same sync helper as `sync_from_external(publish_resource_tree=True)` once per submit action call, not once per returned order.
- If sync succeeds, submit still returns success and includes a concise `material_registration` summary.
- If sync fails, submit still returns success because the Bioyond order was created; include `material_registration.success=false` and add a warning.

Concise submit return shape:

```json
{
  "success": true,
  "order_id": "...",
  "order_ids": ["..."],
  "resultTable": {},
  "material_registration": {
    "requested": true,
    "attempted": true,
    "success": true,
    "publish_resource_tree": true,
    "resource_tree_update_requested": true,
    "message": "Bioyond 资源同步成功"
  },
  "warnings": []
}
```

Do not embed the full sync result under a broad `sync` key in submit returns. Keep the registration summary short and stable.

## Public Input Schemas

### `get_order_list`

Recommended signature:

```python
OrderStatus = Literal["全部（\"\"）", "成功（80）", "失败（90）", "执行中（60）", "已取出（100）"]

@action(
    always_free=True,
    goal_default={
        "status": "全部（\"\"）",
        "max_results": 10,
        "filter_text": "",
        "sorting": "creationTime desc",
        "skipCount": 0,
        "timeType": "",
        "beginTime": None,
        "endTime": None,
        "latest_only": True,
    },
    description=(
        "只读查询 Bioyond LIMS 订单列表。"
        "status 必填：全部（\"\"）/成功（80）/失败（90）/执行中（60）/已取出（100）。"
        "max_results 对应 pageCount，默认 10。"
        "其余查询条件可选。"
    ),
    handles=[...],
)
def get_order_list(
    self,
    status: OrderStatus,
    max_results: int = 10,
    filter_text: str = "",
    sorting: str = "creationTime desc",
    skipCount: int = 0,
    timeType: str = "",
    beginTime: Optional[str] = None,
    endTime: Optional[str] = None,
    latest_only: bool = True,
    **kwargs: Any,
) -> Dict[str, Any]:
    ...
```

Notes:

- `status` is required but prefilled through `goal_default`/UI defaults with `全部（""）` when supported.
- Map the display value to the payload value before calling RPC. For example, `全部（""） -> ""` and `成功（80） -> "80"`.
- `beginTime=None` and `endTime=None` should serialize to JSON `null`; because current AST schema generation does not emit `None` defaults automatically, set explicit action `goal_default` for these keys.
- `timeType` remains a string defaulting to empty. Description should say: `查询时间类型，可选 CreationTime 或 FinishedTime；留空表示不限定，实测没啥用。`
- `beginTime` description should show example format: `开始时间，可选。时间格式示例 2026-05-26T13:50:54.742373，实测没啥用。`
- `endTime` description should say: `结束时间，可选。时间格式示例 2026-05-26T13:50:54.742373，必须大于 beginTime，实测没啥用。`
- Remove `timeout_seconds` and `assignee_user_ids` entirely.
- Remove `node_type=NodeType.MANUAL_CONFIRM`, `placeholder_keys`, and `feedback_interval`.

Implementation snippet:

```python
query_payload = {
    "timeType": str(timeType or ""),
    "beginTime": str(beginTime).strip() if beginTime else None,
    "endTime": str(endTime).strip() if endTime else None,
    "status": ORDER_STATUS_VALUE_MAP[str(status)],
    "filter": str(filter_text or ""),
    "skipCount": max(0, int(skipCount or 0)),
    "pageCount": max(1, int(max_results or 10)),
    "sorting": str(sorting or "creationTime desc"),
}
raw_result = rpc.order_query(json.dumps(query_payload, ensure_ascii=False))
```

Output handles:

- Keep `order_id` and `order_ids`.
- Add scalar `order_code`.
- Add list `order_codes`.
- Populate `order_codes` from non-empty row `orderCode` values only. If a row lacks `orderCode`, do not infer it from `order_id`; keep the row in `orders` with a warning/missing-code marker, but omit the blank from the `order_codes` handle list.
- `orders` always remains a list, even when `max_results=1` or `latest_only=True`.
- When `latest_only=True`, still request `pageCount=max_results`; scalar `order_id` and `order_code` point at the first/latest normalized row while `order_ids` and `order_codes` keep list values.
- `cancel_experiment` requires non-empty `order_codes`, so a missing-code order-list result should fail clearly when chained.

Use explicit handle declarations; the registry does not infer ports from return keys. Recommended `get_order_list` output handles:

```python
ActionOutputHandle(key="order_id", data_type="bioyond_order_id", label="实验ID", data_key="order_id", data_source=DataSource.EXECUTOR)
ActionOutputHandle(key="order_ids", data_type="bioyond_order_ids", label="实验ID列表", data_key="order_ids", data_source=DataSource.EXECUTOR)
ActionOutputHandle(key="order_code", data_type="bioyond_order_code", label="订单编号", data_key="order_code", data_source=DataSource.EXECUTOR)
ActionOutputHandle(key="order_codes", data_type="bioyond_order_codes", label="订单编号列表", data_key="order_codes", data_source=DataSource.EXECUTOR)
```

### `take_out`

Recommended signature:

```python
@action(always_free=True, description="按订单取出 Bioyond LIMS 中已分配/预占的物料")
def take_out(
    self,
    order_id: str,
    preintake_ids: Optional[List[str]] = None,
    material_ids: Optional[List[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    ...
```

Return:

```json
{
  "success": true,
  "order_id": "...",
  "preintake_ids": [],
  "material_ids": [],
  "take_out": {
    "code": 1,
    "data": {}
  }
}
```

Action output handles:

- `order_id`, so it can chain from `get_order_list` and into later nodes.
- Optional `order_ids` is not needed for `take_out`.

### `cancel_experiment`

Recommended signature:

```python
@action(
    always_free=True,
    goal_default={"order_codes": []},
    description="按订单取消 Bioyond 实验，仅调用批量取消实验接口，不执行 take_out",
)
def cancel_experiment(
    self,
    order_codes: Optional[List[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    ...
```

Implementation:

```python
rpc = self._require_hardware_interface("batch_cancel_experiment")
self._require_rpc_method(rpc, "batch_cancel_experiment")

normalized_order_codes = [
    str(value).strip()
    for value in (order_codes or [])
    if str(value or "").strip()
]
if not normalized_order_codes:
    raise ValueError("取消实验需要提供非空 order_codes 列表；该接口在 Sirna 中传 orderCode 列表")

code = rpc.batch_cancel_experiment(normalized_order_codes)
return {
    "success": code == 1,
    "order_codes": normalized_order_codes,
    "code": code,
    "message": "取消实验已提交" if code == 1 else "取消实验失败，请检查 LIMS 状态",
}
```

Do not include manual-confirm params unless the product explicitly decides cancel must be gated. The current user request says split into dedicated functions and "just call batch-cancel experiment api."

Action handles:

- Add only `order_codes` as the cancellation input handle.
- Do not expose `order_id` / `order_ids` inputs on `cancel_experiment`.
- Do not resolve `order_id` to `order_code` inside `cancel_experiment`; users must pass or chain `order_codes`.
- Recommended input handle:

```python
ActionInputHandle(key="order_codes", data_type="bioyond_order_codes", label="订单编号列表", data_key="order_codes", data_source=DataSource.HANDLE, io_type="source")
```

### `sync_from_external`

Recommended signature:

```python
@action(always_free=True, description="从 Bioyond 同步库存物料到本地资源树")
def sync_from_external(
    self,
    publish_resource_tree: bool = True,
    **kwargs: Any,
) -> Dict[str, Any]:
    with self._debug_call_session("sync_from_external"):
        return self._sync_from_external_and_optionally_publish(
            publish_resource_tree=publish_resource_tree,
            action_name="sync_from_external",
        )
```

Do not expose:

- `refresh_material_cache`
- `api_host`
- `api_key`

## Synchronizer Cleanup

### What `SirnaResourceSynchronizer` Adds

Compared with base `BioyondResourceSynchronizer`, `SirnaResourceSynchronizer` only adds reagent-as-liquid stock sync:

- Queries stock material `typeMode` 0/1/2 itself.
- Splits rows into `labware_rows` and `liquid_rows`.
- Treats unmapped reagent rows (`typeMode == 2`) as liquid contents.
- Attaches those reagent liquid rows to an existing parent labware by `locations[0].whName + code`.
- Reuses `_attach_liquid_to_parent`, including `reagent_bioyond_ids` idempotency and best-effort tracker liquid volume.
- Defers liquid rows when parent warehouse/location is missing.

Important limitation:

- It does not update material cache like base `BioyondResourceSynchronizer.sync_from_external`.
- It is installed only after base init/post-init and does not re-run sync when installed.
- Current reset/resync path already bypasses it by constructing `BioyondResourceSynchronizer(self)` directly.
- Inherited/base paths that call `self.resource_synchronizer.sync_from_external()` after `post_init` can still hit the Sirna subclass today.

Base synchronizer behavior to preserve:

- Fetch stock material for `typeMode` 0, 1, and 2.
- Update `hardware_interface.material_cache`, including material names and child/detail material names when present.
- Call `resource_bioyond_to_plr(...)`, which mutates the deck/warehouse tree.
- Publish resource-tree updates only from the station action/helper after successful sync; the base synchronizer itself does not publish.

### Target Decision

For this cleanup, remove it from the runtime path:

- Delete the `post_init` block that replaces `self.resource_synchronizer` with `SirnaResourceSynchronizer(self)`.
- Make `sync_from_external` use the installed base synchronizer. If missing or stale, repair it to `BioyondResourceSynchronizer(self)` and assign it back to `self.resource_synchronizer`.
- Do not keep a parallel Sirna stock synchronizer as the main path.
- Preserve base sync material-cache update and deck mutation behavior.
- Product decision: automatic stock reagent-as-liquid attachment is dropped for this cleanup. Do not move it into a shared/base hook in this pass.

Delete the stock reagent-as-liquid helper family when reference checks show they only support `SirnaResourceSynchronizer`. If a helper is also used by submit/manual-load/report code, classify it in the broader removal audit before deleting it.

## Reset And Take-Out Decoupling

The old reset path can call take-out after cleanup probing. That must stop.

Reset target behavior:

- `reset_auto`: normal action.
- `reset_manual`: `NodeType.MANUAL_CONFIRM`.
- Both actions use plain `bool` inputs so registry/frontend renders checkboxes:
  - `reset_scheduler: bool = True`
  - `reset_order_status: bool = True`
  - `reset_location: bool = True`
  - `reset_devices: bool = False`
  - `sync_from_external_after_reset: bool = False`
- `reset_manual` additionally has `physical_cleanup_confirmed: bool = False`, `timeout_seconds: int = 3600`, and `assignee_user_ids: Optional[List[str]] = None`.
- `reset_manual` metadata:
  - `node_type=NodeType.MANUAL_CONFIRM`
  - `placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"}`
  - `goal_default` includes the four reset booleans, `sync_from_external_after_reset=False`, `physical_cleanup_confirmed=False`, `timeout_seconds=3600`, and `assignee_user_ids=[]`.
- Reset selected operations only:
  1. `reset_scheduler`
  2. `reset_order_status`
  3. `reset_location`
  4. `reset_devices`
- Execute selected operations in that fixed order.
- Reset operation map:

| Operation key | Public checkbox | RPC method | Endpoint | Success |
| --- | --- | --- | --- | --- |
| `reset_scheduler` | `reset_scheduler` | `scheduler_reset()` | `/api/lims/scheduler/reset` | return code `1` |
| `reset_order_status` | `reset_order_status` | `reset_order_status()` | `/api/lims/order/reset-order-status` | return code `1` |
| `reset_location` | `reset_location` | `reset_location()` | `/api/lims/storage/reset-location` | return code `1` |
| `reset_devices` | `reset_devices` | `reset_devices()` | `/api/lims/device/reset-devices` | return code `1` |

- Return structured data:
  - `selected_operations`: one item per operation with key, label, and selected boolean.
  - `executed_calls`: one item per selected operation attempted, including endpoint/RPC method and return code or exception.
  - `skipped_operations`: unchecked operations or all operations when manual cleanup is not confirmed.
  - `warnings`: missing method, exception, or non-success return details. These are fatal for confirmed `reset_manual` at the end of collection and non-fatal for `reset_auto`.
- `all_operations_successful`: `True` only when every selected operation returned success code `1`.
- If no reset operation checkbox is selected, return normally with all operations skipped and a warning.
- `reset_manual(physical_cleanup_confirmed=False)` must not call any RPC. It returns `success=False`, `blocked=True`, and selected/skipped operation metadata.
- Reset does not call `take_out`.
- Reset does not call material cache refresh.
- Reset may call `sync_from_external` only when `sync_from_external_after_reset=True`.
- If `sync_from_external_after_reset=True`, call sync only after all selected reset operations succeeded and return a concise `external_material_sync` result section.
- If any selected reset operation fails, skip requested sync and add a warning such as `sync_from_external_after_reset_skipped_due_to_reset_failure`.
- If `sync_from_external_after_reset=False`, reset must not sync.

Failure semantics differ by action:

- `reset_manual`, after confirmation, attempts all selected reset operations and collects every result. If any selected operation raises or returns non-`1`, raise a workflow-blocking `RuntimeError` after the collection pass. The error message should summarize failed operation keys and include enough structured detail for debugging. Do not let downstream workflow steps proceed after a failed manual reset.
- `reset_manual` should run optional `sync_from_external_after_reset` only after all selected reset operations succeeded. If the sync is skipped because no deck is initialized, return/attach warning metadata instead of raising. If a real sync attempt runs and fails after the deck precondition passes, raise a workflow-blocking `RuntimeError`.
- `reset_auto` is a testing shortcut. It attempts all selected reset operations, records failures as warnings, and returns normally. On failures, return `success=True`, `all_operations_successful=False`, and warnings/executed-call details.
- `reset_auto` may run optional `sync_from_external_after_reset=True` only after all selected reset operations succeeded. Reset or sync failures are recorded as warnings and do not raise.

Manual reset confirmation wording must remain:

```text
确认离心机配平板堆栈、G3移液站、自动化堆栈已清空，仪器内没有残留样品、耗材、试剂
```

## Structured Parameter Override Cleanup

Current Sirna submit still parses text/dict-like `parameter_overrides` and applies overrides by key only. Replace that with a structured live-parameter resolver in Phase 5. Phase 1 may land the RPC action split first, but the overall cleanup is not complete until the old ambiguous override path is removed.

Target public override shape:

```python
class ParameterOverrideEntry(TypedDict, total=False):
    m: Annotated[int, Field(description="Bioyond 参数坐标 m，可选。")]
    n: Annotated[int, Field(description="Bioyond 参数坐标 n，可选。")]
    Key: Annotated[str, Field(description="Bioyond 参数 Key，必填。")]
    Value: Annotated[Any, Field(description="覆盖值，必填；运行时不改写 value。")]


class Experiment1OptionalParams(TypedDict, total=False):
    order_code: Annotated[str, Field(description="订单编号（可选，自动生成）")]
    order_name: Annotated[str, Field(description="订单名称（可选，自动生成）")]
    parameter_overrides: Annotated[
        List[ParameterOverrideEntry],
        Field(default_factory=list, description="参数覆盖列表；点击新增时应生成一组 m/n/Key/Value 字段。"),
    ]
    auto_register_materials: Annotated[bool, Field(default=True, description="是否自动同步 Bioyond 物料到资源树")]
```

Registry/schema guidance:

- Follow the UniLabOS decorator rule that registry-facing actions must be AST-visible on the decorated `BioyondSirnaStation` class.
- Keep `ParameterOverrideEntry` and optional-param `TypedDict` classes as named same-file types so the registry scanner can resolve them through the import map.
- Do not rely on a direct method parameter annotation like `parameter_overrides: Annotated[List[ParameterOverrideEntry], Field(...)]` unless `Annotated` support is added to the AST schema path and tested.
- The schema must render `parameter_overrides` as a list of bundled object entries. The frontend "add entry" behavior should create one grouped row with `m`, `n`, `Key`, and `Value`, following the previous x/y/z bundled-object pattern. It must not render as a plain text box.
- Add registry metadata tests that prove the generated schema contains the list item properties `m`, `n`, `Key`, and `Value`, with descriptions/defaults preserved enough for the frontend renderer.

Resolver requirements:

- Default is an empty list. Verify that the schema still exposes the item shape so the frontend can create the four-field entry from an empty default.
- Accept `Key`/`key` and `Value`/`value`; normalize internally.
- Reject string override formats such as `"a=b,c=d"`; no migration shim.
- Runtime rejects missing `Key` or missing `Value`. An explicit `Value=None` is allowed and must be distinguishable from a missing value.
- Preserve `0` and `"0"` as distinct real values.
- Treat `Value` as a generic JSON value; do not coerce or modify it except as required by the external API payload.
- Flatten live `workflow_step_query(sub_workflow_id)` parameters before building `paramValues`.
- Preserve live step UUID, module/step name, key, display name, `m`, `n`, `TaskDisplayable`, `Value`, and `DisplayValue` in the flattened records.
- Resolve each override against live records. Provided `m` / `n` filters candidates; omitted coordinates do not filter.
- A match count of zero or more than one is an error before `create_order`.
- Deduplicate only within user overrides by `(Key, m, n)` with last-write-wins and a warning.
- Emit final entries grouped by live step UUID with lowercase keys: `{"m": 0, "n": 0, "key": "...", "value": "..."}`.

`sample_throughput` remains Sirna's required submit input and currently maps to `borderNumber`. If a live workflow parameter also represents throughput, route that through the same resolver; do not invent a new Sirna parameter key without user approval.

## Implementation Phases

Implement these as explicit phases. Do not bundle later phases into an earlier PR unless the user asks for one large cleanup.

### Phase 1: RPC Action Split

Goal: land the small public action surface without touching reset, submit parameter overrides, or warehouse layout more than necessary.

- Keep shared `take_out(order_id, preintake_ids, material_ids)`.
- Leave shared `batch_cancel_experiment(order_ids)` signature unchanged, but update its docstring to say the payload is a generic string list and Sirna passes order/experiment codes.
- Modify `get_order_list` to a normal action with required dropdown-capable `status`, visible/defaulted optional query fields, and explicit handles for `order_id`, `order_ids`, `order_code`, and `order_codes`.
- Add `take_out` as a normal action. It consumes `order_id`, accepts optional list-shaped `preintake_ids` / `material_ids`, and passes empty lists when omitted.
- Add `cancel_experiment` as a normal action. It consumes only `order_codes` and calls `rpc.batch_cancel_experiment(order_codes)`.
- Delete `cancel_experiment_and_take_out`.
- Remove raw take-out POST fallback from product paths.
- Add focused tests for order-list payload/status mapping, order code handles, take-out validation, and batch cancel behavior.

Phase 1 acceptance:

- `cancel_experiment_and_take_out` is gone.
- `get_order_list` output includes `order_code` and `order_codes`.
- `cancel_experiment` has no `order_id` / `order_ids` input handles.
- No order-id-to-order-code resolver is introduced for cancellation.

### Phase 2: Peptide-Style Configuration Cleanup

Goal: remove Sirna per-action credential mutation and align with the current Peptide station pattern.

- Remove `api_host` / `api_key` from action signatures, hidden kwargs handling, action docs, and `goal_default`.
- Remove or retire `_update_runtime_api_config(...)` after all kept actions stop calling it.
- Update lazy/missing-config guidance to tell users to fix station/graph config, not action goals.
- Ensure scheduler/start/report/reset/sync actions use `self.hardware_interface` / `self.bioyond_config` directly through helper preflight.
- Keep debug CLI/direct HTTP helpers only if gated by `DEBUG_CLI_ENABLED`; product actions must not depend on them.

Phase 2 acceptance:

- `rg -n "api_host|api_key" sirna_station.py` shows only config setup, debug helpers, or messages about station config, not action-goal mutation.
- Product actions do not call `_update_runtime_api_config`.

### Phase 3: Reset, Sync, And Synchronizer Cleanup

Goal: replace old `reset` / `resync_external_materials` with explicit reset and sync actions.

- Implement `_sync_from_external_and_optionally_publish(publish_resource_tree, action_name)` using the installed/repaired base `BioyondResourceSynchronizer`.
- If `self.deck` is missing, skip sync with warning metadata and do not publish.
- Add `sync_from_external(publish_resource_tree=True)`.
- Add `reset_auto` and `reset_manual` using the reset operation map in this plan.
- Delete old public `reset`.
- Delete `resync_external_materials`; do not keep a wrapper alias.
- Delete reset-time take-out probing/calls and implicit reset-time sync.
- Delete runtime `SirnaResourceSynchronizer` installation and class. Remove `SirnaResourceSynchronizer`-only helpers after reference checks.

Phase 3 acceptance:

- Reset never calls take-out.
- Reset sync only occurs through `sync_from_external_after_reset=True`, after all selected reset operations succeeded.
- If reset operations fail, requested sync is skipped with a warning.
- If sync is requested without a deck, sync is skipped with warning metadata.
- Runtime no longer installs `SirnaResourceSynchronizer`.

### Phase 4: Report Envelope Cleanup

Goal: make read-only report actions normal typed-RPC product actions.

- Add backward-compatible `return_envelope: bool = False` to `order_query`, `order_report`, `gantts_by_order_id`, `gantt_with_simulation_by_order_id`, and `material_info`.
- Preserve default data-only behavior for existing callers.
- Use `return_envelope=True` in Sirna report actions so `code`, `message`, `path`, and `data` are preserved.
- For `material_info`, trust live runtime envelope evidence and document that the OpenAPI schema is stale for this endpoint.
- Remove station-local raw report fallback helpers from product flow. Debug-only raw helpers may remain behind `DEBUG_CLI_ENABLED`.

Phase 4 acceptance:

- `get_order_report` and `get_aggregated_order_report` are normal actions.
- Service-level report failures return structured `success=False` / `section_errors` / warnings rather than raw fallback behavior.

### Phase 5: Submit/Start Handle And Parameter Override Cleanup

Goal: make submit/start chaining and parameter overrides match the final public contract.

- Remove legacy submit output handles now. Keep only `order_id`, `order_ids`, and `resultTable`.
- Remove legacy start input handles now. Keep only `order_id`, `order_ids`, and `resultTable`.
- Replace text/key-only `parameter_overrides` with named same-file `TypedDict` schemas and a structured live-parameter resolver.
- Reject old string override formats.
- Implement `auto_register_materials=True` as one post-submit `sync_from_external(publish_resource_tree=True)` call with concise `material_registration` summary.
- If submit auto-sync is skipped or fails, the Bioyond order creation still succeeds and returns a warning.

Phase 5 acceptance:

- Submit/start action handles expose only `order_id`, `order_ids`, and `resultTable`.
- Registry tests prove `parameter_overrides` renders as a list of bundled `m` / `n` / `Key` / `Value` object entries.

### Phase 6: Sirna Warehouse Frontend Y Flip

Goal: handle coordinate layout separately from action behavior.

- Apply `frontend_y_flip=True` to Sirna warehouse sites and deck child placements.
- Keep Sirna-scoped behavior; do not alter shared Bioyond or Peptide defaults.
- Do not add serialized `flip_y` metadata.
- Add coordinate tests with expected flipped deck child placements and warehouse site coordinates.

Phase 6 acceptance:

- Sirna deck child placements and warehouse site coordinates are stored in frontend-flipped orientation.
- Existing warehouse identifiers, slot labels, Bioyond IDs, `bioyond_axis`, and `bioyond_key_axis` remain unchanged.

### Phase 7: Final Cleanup And Full Verification

- Collapse duplicated helpers after reference checks.
- Remove stale parser/reset/resource-tree mutation helpers only when no kept product path references them.
- Run focused tests first, then broader `pytest tests/` if practical.
- Do not treat `temp_benyao/sirna/tests` as push-target tests; use them only as evidence/diagnostics.

## Test Plan

Recommended new files:

- `tests/devices/workstation/test_sirna_actions.py`
- `tests/devices/workstation/test_bioyond_rpc.py`
- `tests/devices/workstation/test_sirna_submit_params.py`

Practical test strategy:

- For schema-only tests, use `unilabos.registry.ast_registry_scanner._parse_file(...)` or normalized registry metadata so tests do not need ROS or a full station boot.
- For station behavior tests, instantiate with `object.__new__(BioyondSirnaStation)`, set `hardware_interface`, `bioyond_config`, and any helper attributes directly, and stub `_publish_resource_tree_update`.
- For RPC payload tests, instantiate `BioyondV1RPC` with `object.__new__`, set `host`, `api_key`, and `_logger`, then monkeypatch `post`; avoid normal construction because it can load material cache / query stock.

Station/schema tests:

- `get_order_list` exists and is not `manual_confirm`.
- `get_order_list` has required `status` enum, no `timeout_seconds`, no `assignee_user_ids`.
- `get_order_list` status enum includes user-facing strings such as `全部（""）` and maps them to RPC values.
- `get_order_list` goal/default schema includes `max_results=10`, `filter_text=""`, `sorting="creationTime desc"`, `skipCount=0`, `timeType=""`, `beginTime=None`, `endTime=None`.
- `get_order_report` and `get_aggregated_order_report` exist and are normal actions.
- `cancel_experiment_and_take_out` is absent.
- `resync_external_materials` is absent.
- public `reset` is absent.
- `reset_auto` exists as a normal action with four bool reset inputs.
- `reset_manual` exists as manual-confirm with `physical_cleanup_confirmed=False` and the required cleanup wording.
- `take_out` exists and returns/chains `order_id`.
- `cancel_experiment` exists with only the `order_codes` input handle and no `order_id` / `order_ids` cancellation handles.
- `get_order_list` exposes `order_codes` and scalar `order_code` output handles.
- submit output handles are only `order_id`, `order_ids`, and `resultTable`.
- `start_experiment` input handles are only `order_id`, `order_ids`, and `resultTable`.
- Sirna deck child placements and warehouse site coordinates are frontend-y-flipped; no new `flip_y` metadata is required.
- `sync_from_external` exists with `publish_resource_tree: bool = True`.
- `auto_register_materials` is visible on submit optional params and defaults `True`.
- `parameter_overrides` is `List[ParameterOverrideEntry]` with bundled `m`, `n`, `Key`, `Value` item schema and empty-list default.

Behavior tests with fake RPC/synchronizer:

- `get_order_list` sends `pageCount == max_results`, mapped status value, and optional query fields to `rpc.order_query`.
- `get_order_list(status='全部（""）')` sends an empty status value.
- `get_order_list` sends `beginTime=None` and `endTime=None` as JSON null when left unset.
- `get_order_list` normalizes rows into `orders`, `order_id`, `order_ids`, `order_code`, and `order_codes`; `order_codes` includes only non-empty row `orderCode` values.
- `get_order_list(latest_only=True)` still requests `pageCount=max_results`, keeps list outputs, and sets scalar `order_id` / `order_code` from the first/latest row.
- `take_out(order_id, explicit lists)` calls `rpc.take_out(order_id, preintake_ids, material_ids)`.
- `take_out(order_id)` passes empty lists and does not query an order snapshot.
- `take_out` rejects single-string `preintake_ids` / `material_ids`.
- `cancel_experiment(order_codes=[...])` calls only `rpc.batch_cancel_experiment(order_codes)`; it must not call `take_out`, sync, or order-list resolution.
- `cancel_experiment(order_codes=[])` raises a workflow-blocking input validation error.
- `cancel_experiment(order_codes="...")` is rejected; order codes must be a list.
- `get_order_report` returns `success=False` with the Bioyond envelope on service-level report failure instead of raising.
- `get_aggregated_order_report` returns partial report data with `section_errors` / warnings when secondary sections fail.
- `material_info(return_envelope=True)` returns the live envelope shape; default `return_envelope=False` returns `data` for `code == 1`.
- `reset_manual(physical_cleanup_confirmed=False)` calls no RPC and returns blocked/skipped metadata.
- Confirmed `reset_manual` attempts all selected reset operations, then raises `RuntimeError` if any selected operation failed or returned non-`1`.
- `reset_auto` records reset operation failures as warnings and returns normally with `success=True`, `all_operations_successful=False`.
- `reset_auto(sync_from_external_after_reset=True)` and `reset_manual(sync_from_external_after_reset=True)` run sync only after all selected reset operations succeed; otherwise sync is skipped with a warning.
- `sync_from_external(publish_resource_tree=True)` publishes only after successful sync.
- `sync_from_external(publish_resource_tree=False)` syncs without publishing.
- `sync_from_external` skips with warning metadata and does not publish when `self.deck` is missing.
- `sync_from_external` uses the installed/repaired base synchronizer and preserves material cache update behavior through base sync.
- Submit with `auto_register_materials=True` calls sync once after successful submit/result parsing.
- Submit still succeeds if auto sync fails, but returns a concise failed `material_registration` summary and warning.

Structured submit-parameter tests:

- Public `parameter_overrides` is `List[ParameterOverrideEntry]`, default empty, with schema item fields `m`, `n`, `Key`, and `Value`.
- `Key`/`key` and `Value`/`value` aliases normalize.
- `m` / `n` disambiguate duplicate live parameter keys.
- Missing live key raises before `create_order`.
- Ambiguous live key without `m` / `n` raises before `create_order`.
- `0` and `"0"` values are preserved.
- Missing `Key` or missing `Value` raises before `create_order`; explicit `Value=None` is preserved.
- Duplicate user overrides warn and last-write-wins.
- Old `"a=b,c=d"` string input is rejected.

Shared RPC tests:

- `take_out` posts to `/api/lims/order/take-out` with `data.orderId`, `data.preintakeIds`, and `data.materialIds`.
- shared `batch_cancel_experiment` posts to `/api/lims/order/batch-cancel-experiment` with `data` from its `order_ids` argument.
- report aggregation RPC methods preserve existing data-only behavior by default and return full envelopes with `return_envelope=True`.
- `material_info` test uses the live Sirna envelope shape even though the OpenAPI schema currently says direct DTO.
- `reset_location` posts without `data`.
- `reset_order_status` posts without `data`.

Temp/reference tests:

- `temp_benyao/sirna/tests` are local evidence/diagnostics and are not push-target tests for this cleanup.
- Do not spend implementation time updating temp tests unless they are actively used for local diagnostics.
- Main repo tests under `tests/` carry the pushed verification contract.

Verification commands:

```bash
pytest tests/devices/workstation/test_sirna_actions.py
pytest tests/devices/workstation/test_bioyond_rpc.py
pytest tests/devices/workstation/test_sirna_actions.py tests/devices/workstation/test_bioyond_rpc.py
pytest tests/
rg -n "def (resync_external_materials|cancel_experiment_and_take_out|reset)\\b" unilabos/devices/workstation/bioyond_studio/sirna_station/sirna_station.py
rg -n "SirnaResourceSynchronizer\\(" unilabos/devices/workstation/bioyond_studio/sirna_station/sirna_station.py
rg -n "batch_cancel_experiment\\(\\[.*order_id|resolve_order_code|_resolve_order_code" unilabos/devices/workstation/bioyond_studio/sirna_station/sirna_station.py
rg -n "orderID|orderIDs|target_device|coin_cell_code|mount_resource|resource|mount" unilabos/devices/workstation/bioyond_studio/sirna_station/sirna_station.py
```

Expected `rg` results:

- No public `def resync_external_materials`.
- No public `def cancel_experiment_and_take_out`.
- No public `def reset`.
- No runtime `SirnaResourceSynchronizer(...)` installation.
- No Sirna `batch_cancel_experiment([order_id])` call and no Sirna order-id-to-order-code resolver for cancellation.
- Submit/start action handles expose only `order_id`, `order_ids`, and `resultTable`.

## Acceptance Checklist

- `get_order_list` is a normal action.
- `get_order_list` no longer has `timeout_seconds` or `assignee_user_ids`.
- `status` is required and dropdown-capable.
- `status` includes `全部（""）` and maps internally to value `""`.
- `max_results` remains the public query-count field and maps to `pageCount`.
- `latest_only=True` keeps list outputs and exposes first/latest scalar handles.
- Optional order-list query fields are visible after required fields and show defaults.
- `beginTime` and `endTime` default to `None` / JSON `null`; descriptions show timestamp examples.
- `cancel_experiment_and_take_out` is gone.
- `get_order_list` returns and exposes scalar `order_code` plus list `order_codes`.
- `cancel_experiment(order_codes=[...])` calls batch cancel only with the explicit orderCode list and returns `order_codes`.
- `cancel_experiment` requires `order_codes` as a list; empty or string inputs raise workflow-blocking input validation errors.
- `take_out(order_id, ...)` calls shared typed `take_out` and returns `order_id`.
- `take_out(order_id)` passes empty target ID lists by default and does not query order snapshots.
- `take_out` requires list-shaped `preintake_ids` / `material_ids` when provided.
- `cancel_experiment` consumes explicit `order_codes`; `get_order_list` outputs `order_codes` from non-empty row `orderCode` values only.
- `get_order_report` and `get_aggregated_order_report` are normal actions and use typed RPC envelope support, not station-local raw POST fallback.
- `material_info` report support follows the live Sirna envelope shape and documents the stale OpenAPI direct-DTO schema.
- `get_aggregated_order_report` returns partial data with `section_errors` / warnings for secondary section failures.
- Sirna warehouse/deck coordinates are frontend-y-flipped via stored coordinates, including deck children and warehouse sites.
- Sirna warehouses keep existing `bioyond_axis="xy_col_row"` and `bioyond_key_axis="col_row"` behavior.
- No new `flip_y` metadata flag is required.
- Submit outputs and start inputs expose only `order_id`, `order_ids`, and `resultTable`.
- `auto_register_materials=True` runs one post-submit `sync_from_external(publish_resource_tree=True)` call and returns a concise `material_registration` summary.
- Submit still succeeds when auto sync fails, but includes a warning.
- `parameter_overrides` uses bundled `ParameterOverrideEntry` rows with `m`, `n`, `Key`, and `Value`; old string overrides are rejected.
- `resync_external_materials` is gone.
- `sync_from_external` is the only public external sync action.
- `sync_from_external` uses the installed/repaired base synchronizer and publishes only when requested and successful.
- `sync_from_external` preserves base sync material-cache update and deck mutation behavior.
- `sync_from_external` skips with warning metadata when no station deck is initialized.
- Runtime no longer installs `SirnaResourceSynchronizer`.
- Reset is split into `reset_auto` and `reset_manual`; old public `reset` is gone.
- Reset no longer performs take-out.
- `reset_manual(physical_cleanup_confirmed=False)` calls no RPC.
- Confirmed `reset_manual` collects all selected operation results and raises `RuntimeError` at the end on any selected operation failure/non-`1` return.
- `reset_auto` returns normally with warnings and `all_operations_successful=False` when selected operations fail.
- Optional reset sync runs only after all selected reset operations succeeded; failed reset operations skip requested sync with a warning.
- Reset result includes selected/skipped/executed/warnings metadata.
- Generic Bioyond APIs are reached through `BioyondV1RPC`, not station-local raw HTTP.
- Non-debug product routes do not use non-`/api/lims/*` frontend endpoints such as `/api/storage/location/locations-by-type`.
- Hidden per-action `api_host` / `api_key` overrides are removed from all Sirna actions.
- Missing credential messages point to station/graph config, not action goal overrides.

## Open Risks

- Frontend dropdown labels are handled by using display strings such as `全部（""）`; this keeps labels readable even if separate label/value metadata is unavailable.
- Current order-list schema/manual does not prove `orderCode` appears in rows. The action still exposes `order_codes`, but chained cancellation only works when returned rows contain non-empty `orderCode` values or the user supplies explicit codes.
- `/api/lims/storage/material-info` has stale OpenAPI output docs: schema says direct DTO, but live Sirna returned a service envelope. Tests should lock the live envelope behavior while keeping a small defensive fallback if practical.
- Removing `SirnaResourceSynchronizer` intentionally drops stock-material reagent-as-liquid auto-attachment. Current reset/resync behavior already used base sync, but inherited `self.resource_synchronizer.sync_from_external()` callbacks will change to base behavior after removing the post-init swap.
- `take_out(order_id, [], [])` is the accepted target behavior. If a live Bioyond deployment rejects order-only take-out, users must provide explicit `preintake_ids` / `material_ids` lists rather than reintroducing snapshot extraction.
- `parameter_overrides` item-schema rendering should be verified because the default is an empty list; the frontend must still create a bundled `m/n/Key/Value` row when users add an entry.
