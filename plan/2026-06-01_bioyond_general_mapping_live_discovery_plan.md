# Bioyond Order-Scoped Material Sync And Sirna Material Classes Plan

Date: 2026-06-01

## Summary

This plan replaces the earlier live-discovery/material-attachment draft. The new direction is:

- Live discovery/config-update work starts by asking the user for the graph/config JSON path to modify. Do not perform live API discovery that will update config, and do not edit a static graph/config JSON, until that target file/location is explicit.
- New Bioyond materials get new Bioyond-specific PLR classes. Do not reuse branded or device-specific classes such as `PRCXI_nest_12_troughplate` for Bioyond station material mapping.
- Prefer generic PLR bases (`Plate`, `Container`, `TipRack`, `ordered_items`, `Well`) and thin UniLabOS `@resource` classes. Avoid silent fallback to `RegularContainer`.
- Keep the existing `sync_from_external` function as the legacy `stock-material` sync path. Its API returns unused materials across all orders, not a complete deck/order snapshot.
- Add a new by-order/full-scope shared sync function named `sync_from_external_by_order`, based on `POST /api/lims/storage/materials-by-order-id`.
- For Sirna automatic hooks, use `sync_from_external_by_order`; do not use the legacy stock sync for init/reset/resource-tree-refresh hooks.
- For Sirna, no reagent/material stacking is expected. If two different Bioyond IDs point to the same deck slot, warn and skip the conflicting row; if an incoming Bioyond material lands on a placeholder/local-only occupancy, overwrite only when the station policy says that placeholder is safe to replace.
- Treat the live PLR deck/resource tree as the runtime truth. Mutate live PLR resources first, then publish the deck; do not mutate `ResourceDict` as the primary business object.
- Treat `status == 2` plus `locations == []` as a known Bioyond material that is off-deck/unlocated, not as deletion. Move it under a shared virtual `BioyondOffDeckMaterials` parent and keep its Bioyond ID.

## Public Interfaces

- Add `BioyondV1RPC.materials_by_order_id(order_id: str) -> list[dict]`.
  - Endpoint: `POST /api/lims/storage/materials-by-order-id`.
  - Request envelope: `{apiKey, requestTime, data: order_id}`.
  - Response envelope: `{code, data, message, timestamp}`; success is `code == 1`.
- Keep the existing shared/station `sync_from_external(...)` behavior and name for the legacy stock-material path.
  - It continues to use `stock-material` typeMode `0`, `1`, and `2`.
  - Because this endpoint returns unused materials across all orders, do not treat its response as a full external snapshot.
  - Sirna constructor/init and `reset_stock_locations` flows should not call this old stock-based function; they should call `sync_from_external_by_order(order_id=None, ...)`.
- Add a new shared Bioyond order/full-scope sync method, exposed by station actions as `sync_from_external_by_order(order_id: Optional[str] = None, publish_resource_tree: bool = True, del_rest_bioyond: bool = False)`.
  - This new public `sync_from_external_by_order` uses `materials-by-order-id`, not `stock-material`.
  - If station action caller omits `order_id`, run full-scope mode: query `order_query` / `/api/lims/order/order-list` with `pageCount: 10`, `skipCount: 0`, and `sorting: "creationTime desc"`; call `materials_by_order_id(...)` for each returned order ID; aggregate material rows by Bioyond material ID.
  - In full-scope aggregation, if the same Bioyond material ID appears in multiple recent orders, keep the row from the newest order and record a debug/warning entry only if the duplicate rows conflict in status/location.
  - `del_rest_bioyond` defaults to `False` and is only valid when `order_id is None`. If `order_id` is provided, `del_rest_bioyond=True` must be rejected or ignored with a warning; single-order sync must not delete other Bioyond materials from the deck.
  - In full-scope mode with `del_rest_bioyond=True`, reconcile-remove local deck materials that have `unilabos_extra["material_bioyond_id"]` but whose Bioyond IDs are absent from the aggregated returned material ID set after cache refresh. Never delete local resources without Bioyond ID.
  - It must be declared/wrapped on the decorated station class when action exposure is needed, because AST scanning only sees methods in the decorated class body.
- Add an order ID resolver helper used by Sirna submit/finish hooks before calling the by-order sync:
  - If an `order_id` is already present, use it directly.
  - If only `orderCode` / `order_code` is present, query `order_query` / `/api/lims/order/order-list` with `filter` set to the order code, `pageCount` large enough for disambiguation, and `sorting: "creationTime desc"`; choose the exact `orderCode` match and use its `id`.
  - If neither is present and the caller explicitly wants broad refresh, call `sync_from_external_by_order(order_id=None, ...)` full-scope mode. Otherwise warn and skip sync.
- Add a shared per-row helper:
  - `process_bioyond_material_update(row, deck, config, existing_index, *, source, allow_remove: bool = False)`
  - Identity is strictly `row["id"]`; do not match by name, code, type name, slot, or barcode.
  - This helper is used by both `sync_from_external_by_order` and `handle_external_change` / the `process_material_change_report` path.
  - It only applies the single material row it receives; it must not fetch other order/material rows and must not delete unrelated local materials.
  - It mutates live PLR objects on the deck/resource tree, not `ResourceDict`.
- Add a shared Bioyond event handler:
  - `handle_external_change(report_data, publish_resource_tree: bool = True)`.
  - The station HTTP route/report method extracts `body.text`, then delegates to this synchronizer method.
  - Inside the handler, update the material cache from the posted row/detail rows, apply `process_bioyond_material_update(...)`, and publish only if the live PLR tree changed.
  - Keep the parent-facing contract compatibility-friendly: return `bool` or a small ACK-compatible dict and put detailed counts in a summary/log field; do not require callers to consume a new complex return shape.
- Add a station-level cache helper:
  - `update_bioyond_material_cache_from_rows(rows)`
  - It updates `hardware_interface.material_cache` from each material row and from each row's `detail` rows.
  - `sync_from_external_by_order` calls it with all rows returned by `materials-by-order-id`.
  - `handle_external_change` calls it with only the posted material row for `process_material_change_report`.
- Add a shared virtual-parent helper:
  - `get_or_create_bioyond_off_deck_parent(deck) -> BioyondOffDeckMaterials`
  - It returns the existing virtual parent if it was deserialized or already created in the live tree; otherwise it creates one live PLR resource and attaches it under the deck.
  - This parent is not a Bioyond material and must not have `unilabos_extra["material_bioyond_id"]`.

## Live Discovery And Config Update Gate

Some implementation work is pure runtime sync, while live discovery may call Bioyond APIs and update station graph/config JSON. Treat config mutation as a separate, explicit step:

- First ask the user which graph/config JSON file should be modified.
- Confirm whether the operation is read-only discovery or discovery plus config rewrite.
- Do not guess a config path from the current branch, uploaded runtime state, or another Bioyond project.
- If the user only wants runtime sync, create/reuse live resources such as `BioyondOffDeckMaterials` during initialization and publish the resource tree; do not rewrite static JSON.
- If the user wants discovered mappings/warehouses/material classes persisted into JSON, edit only the user-provided config path and summarize exactly which nodes/mappings changed.

## External Schemas And Examples

The implementation should rely on the embedded contracts below, not on re-reading raw log files during implementation.

### Order List

`BioyondV1RPC.order_query(json_str)` posts to `/api/lims/order/order-list` with this envelope:

```json
{
  "apiKey": "<api key>",
  "requestTime": "2026-05-25T00:00:00.000Z",
  "data": {
    "timeType": "",
    "beginTime": null,
    "endTime": null,
    "status": "",
    "filter": "<optional orderCode/order name filter>",
    "skipCount": 0,
    "pageCount": 10,
    "sorting": "creationTime desc"
  }
}
```

Accepted response shape:

```json
{
  "code": 1,
  "message": "",
  "timestamp": 1775802373135,
  "data": {
    "totalCount": 1,
    "items": [
      {
        "id": "3a2087b7-b2ec-04ac-79cd-f664c31913f4",
        "orderCode": "test0527180825",
        "name": "test0527180825",
        "orderName": "test0527180825",
        "status": 80,
        "statusName": "完成",
        "creationTime": "2026-04-10T11:58:47.532782"
      }
    ]
  }
}
```

Current Sirna action normalizes order-list to:

```json
{
  "success": true,
  "orders": [
    {
      "order_id": "3a2087b7-b2ec-04ac-79cd-f664c31913f4",
      "order_code": "test0527180825",
      "order_name": "test0527180825",
      "status": "80",
      "created_at": "2026-04-10T11:58:47.532782",
      "raw": {}
    }
  ],
  "order_id": "3a2087b7-b2ec-04ac-79cd-f664c31913f4",
  "order_ids": ["3a2087b7-b2ec-04ac-79cd-f664c31913f4"],
  "order_code": "test0527180825",
  "order_codes": ["test0527180825"],
  "query": {}
}
```

For full-scope sync, query newest 10 with `pageCount: 10`, `skipCount: 0`, and `sorting: "creationTime desc"`. For `orderCode -> order_id`, query with `filter` set to the order code and choose only an exact `item["orderCode"] == requested_order_code` match.

### Create Order / Submit Experiment

Sirna create-order request data:

```json
[
  {
    "orderCode": "<resolved_order_code>",
    "orderName": "<resolved_order_name>",
    "borderNumber": 8,
    "workFlowId": "<sub_workflow_id>",
    "paramValues": {
      "<step_id>": [
        {"key": "protocolName", "m": 0, "n": 3, "value": "吸弃上清"}
      ]
    },
    "extendProperties": ""
  }
]
```

Create-order success response is an allocation map whose top-level keys are Bioyond order IDs:

```json
{
  "code": 1,
  "message": "",
  "timestamp": 1779876658790,
  "data": {
    "3a217516-2d5a-eea6-3108-69874c7af55a": [
      {
        "destinationId": "3a217516-2d5a-eea6-3108-69874c7af55a",
        "destinationType": "Order",
        "id": "3a217516-2d2f-5ffd-789d-e48897998c46",
        "locationCode": "10-1",
        "locationId": "3a2083ab-5359-cd8f-9ec8-7f030dbe37b0",
        "locationShowName": "10-1",
        "materialCode": "0016-00065",
        "materialId": "3a217516-2a87-0c87-3114-d0dc43dae650",
        "materialName": "G3-200ul枪头盒",
        "materialTypeCode": "0016",
        "materialTypeId": "3a1faa16-f119-5d37-335e-f9e39a806c12",
        "materialTypeMode": "Consumables",
        "materialTypeName": "G3-200ul枪头盒",
        "quantity": "1个"
      }
    ]
  }
}
```

Current Sirna submit result exposes both single and multi-order outputs:

```json
{
  "success": true,
  "order_code": "<submitted/resolved code>",
  "order_name": "<submitted/resolved name>",
  "order_id": "3a217516-2d5a-eea6-3108-69874c7af55a",
  "order_ids": ["3a217516-2d5a-eea6-3108-69874c7af55a"],
  "create_order_result": {},
  "materials": [],
  "resultTable": {}
}
```

Compatibility note: some Bioyond/current-code paths return order code only, such as `{"data": [{"orderCode": "..."}]}`, `{"data": {"orderCode": "..."}}`, or `{"data": "..."}`. When only `orderCode` is available, resolve order ID through exact-match `order-list`.

### Materials By Order

`BioyondV1RPC.materials_by_order_id(order_id)` posts to `/api/lims/storage/materials-by-order-id` with this envelope:

```json
{
  "apiKey": "<api key>",
  "requestTime": "2026-05-27T10:17:52.784Z",
  "data": "3a217b15-13cd-1fd8-4e6f-cfdefc0e786d"
}
```

Expected success response:

```json
{
  "code": 1,
  "message": "",
  "timestamp": 1779877072784,
  "data": [
    {
      "barCode": null,
      "code": "0007-00010",
      "detail": [],
      "id": "3a217b15-07db-e8e8-046b-171218c7e6c8",
      "isUse": true,
      "locations": [
        {
          "code": "2-1",
          "id": "3a2083ab-5359-9aa3-d5ea-f3d1f8367532",
          "quantity": 0,
          "whName": "自动化堆栈",
          "whid": "3a2083ab-5356-4056-083a-2c94d3eeddb4",
          "x": 1,
          "y": 2,
          "z": 1
        }
      ],
      "lockQuantity": 0.0,
      "name": "试剂槽DEPC H2O",
      "quantity": 1.0,
      "status": 1,
      "typeName": "试剂槽DEPC H2O",
      "unit": "个"
    }
  ]
}
```

Material row contract:

```json
{
  "barCode": null,
  "code": "0007-00010",
  "detail": [],
  "id": "3a217b15-07db-e8e8-046b-171218c7e6c8",
  "isUse": true,
  "locations": [
    {
      "code": "2-1",
      "id": "3a2083ab-5359-9aa3-d5ea-f3d1f8367532",
      "quantity": 0,
      "whName": "自动化堆栈",
      "whid": "3a2083ab-5356-4056-083a-2c94d3eeddb4",
      "x": 1,
      "y": 2,
      "z": 1
    }
  ],
  "lockQuantity": 0.0,
  "name": "试剂槽DEPC H2O",
  "quantity": 1.0,
  "status": 1,
  "typeName": "试剂槽DEPC H2O",
  "unit": "个"
}
```

Implementation must preserve `id`, `code`, `barCode`, `name`, `typeName`, `unit`, `quantity`, `lockQuantity`, `isUse`, `status`, `detail`, and the first/active location metadata in `unilabos_extra`. Treat `locations` as a list even when an observed row has one location. If a material row lacks `id`, warn and skip deck mutation for that row; it cannot participate in Bioyond-ID diffing.

Known row examples now embedded in this plan:

- `status == 1` with non-empty `locations`: material is in storage/on deck.
- `status == 2` with `locations == []`: material is still known externally but is not in a deck/warehouse slot; move the live resource under `BioyondOffDeckMaterials` and do not delete it from this row alone.
- `status == 0`: no concrete material-row raw example was found; the status behavior below is an implementation contract.

### Finish Report Posts

`/report/sample_finish` body:

```json
{
  "method": "POST",
  "token": "",
  "request_time": "2026-05-27T18:17:47.4759435+08:00",
  "data": {
    "orderCode": "test0527180825",
    "orderName": "test0527180825",
    "sampleId": "3a217b15-140b-b367-e2c4-ce49295afd88",
    "startTime": "2026-05-27T18:11:00.854759",
    "endTime": "2026-05-27T18:17:47.340686",
    "status": "20"
  }
}
```

`/report/order_finish` body:

```json
{
  "method": "POST",
  "token": "",
  "request_time": "2026-05-27T18:17:47.4916933+08:00",
  "data": {
    "orderCode": "test0527180825",
    "orderName": "test0527180825",
    "startTime": "2026-05-27T18:11:00.854759",
    "endTime": "2026-05-27T18:17:47.340686",
    "status": "30",
    "workflowStatus": "completed",
    "completionTime": "2026-05-27T18:17:47.340686",
    "usedMaterials": [
      {
        "materialId": "3a217b15-03f4-39aa-e7d8-3a8954d8b6a4",
        "locationId": "3a2083ab-5359-a795-f690-98fbf1f90368",
        "typemode": "1",
        "usedQuantity": 1
      }
    ]
  }
}
```

Accept both `typemode` and `typeMode` in `usedMaterials`. These reports may lack `orderId`, so Sirna should resolve `orderCode` through order-list before calling by-order sync.

`/report/step_finish` body:

```json
{
  "method": "POST",
  "token": "",
  "request_time": "2026-05-27T17:52:14.106553+08:00",
  "data": {
    "orderCode": "test0527175143",
    "orderName": "test0527175143",
    "stepName": "堆栈出库",
    "stepId": "790fc877-2db1-4ec4-8bd3-78315fa4977e",
    "sampleId": "3a217b05-c0cd-c57e-3279-18e579867057",
    "startTime": "2026-05-27T17:52:13.6049607+08:00",
    "endTime": "2026-05-27T17:52:14.0285361+08:00",
    "executionStatus": "completed"
  }
}
```

Step-finish is intentionally status/report/debug only and must not trigger material sync.

### Material Cache

`hardware_interface.material_cache` is a `dict[str, str]` mapping material display name to Bioyond material ID:

- For each material row: if `row["name"]` and `row["id"]`, set `material_cache[row["name"]] = row["id"]`.
- For each detail row: if `detail["name"]` exists, set `material_cache[detail["name"]] = detail.get("detailMaterialId") or detail.get("id")`.
- Duplicate names overwrite previous values; this matches current cache behavior and should be logged only at debug level if needed.
- Do not add `BioyondOffDeckMaterials` to `material_cache`; it is not a Bioyond material and has no external material ID.

## Runtime PLR Vs Serialized Resource Tree

The runtime object to mutate is the live PLR deck/resource tree:

- A live PLR resource is the Python object currently attached to `self.deck`, a warehouse, carrier, slot, or parent material.
- PLR parent/child affiliation is represented by live object relationships: `resource.parent`, parent `children`, itemized carrier site occupancy, and warehouse slot occupancy.
- Bioyond sync must add/move/off-deck-attach/unassign resources by mutating these live PLR objects first.

`ResourceDict`, `ResourceDictInstance`, and `ResourceTreeSet` are serialized UniLabOS transport/cloud-sync representations:

- `ResourceTreeSet.from_plr_resources([deck])` walks the live PLR tree, fills missing `unilabos_uuid`, reads `resource.unilabos_extra`, and writes that dict into serialized `ResourceDict.extra`.
- Parentage is serialized through `parent_uuid`; `ResourceDict.parent` is not the primary runtime relationship.
- `ResourceTreeSet.dump()` publishes flattened serialized nodes.
- `graphio` reconstructs parent/child relationships from serialized `parent_uuid` first, then `parent`.

Implementation rule:

- Do not hand-build or patch `ResourceDict` as the primary Bioyond material-sync mutation.
- Mutate live PLR objects first.
- Publish after mutation with `update_resource(resources=[deck])`, equivalent to Sirna `_publish_resource_tree_update()`.
- If a narrow future cloud update is proven safe, it should still be derived from already-mutated live PLR resources.

## Bioyond Off-Deck Virtual Parent

Add a shared virtual PLR parent named/classed `BioyondOffDeckMaterials` for Bioyond materials that still exist externally but have no deck/warehouse location:

- It is a UniLabOS/PLR bookkeeping resource, not a Bioyond material.
- It must not be part of the Bioyond material type mapping table and must never be selected from `typeName`/`typeId`.
- It must not set `unilabos_extra["material_bioyond_id"]`, so Bioyond material lookup never treats the parent as an external material.
- It may set a non-material marker such as `unilabos_extra["bioyond_virtual_role"] = "off_deck_materials"` for idempotent lookup/debugging.
- It should be an import-visible `@resource(...)` class with a stable model/resource id, tolerant `*args, **kwargs` constructor, and no physical slot/warehouse mapping.
- It should serialize/deserialize like any other PLR resource: assign `unilabos_uuid` if missing, keep children in the serialized resource tree, and reuse the deserialized parent instead of creating duplicates.
- It should live under the deck/resource tree so children remain visible in `ResourceTreeSet.from_plr_resources([deck])` and cloud/API resource sync.
- Moving a child into or out of `BioyondOffDeckMaterials` is a resource-tree parent change and therefore should publish via `update_resource(resources=[deck])` when `publish_resource_tree=True`.
- Definition is code-first, not config-JSON-first: define/register the `BioyondOffDeckMaterials` class in source, then create or reuse the live instance during station/deck initialization with `get_or_create_bioyond_off_deck_parent(deck)` after the deck object exists.
- This mirrors the intended deck-child setup flow: initialization makes the live PLR instance available, while serialization/publish makes it visible to the frontend/cloud.
- Do not rewrite uploaded graph/config JSON by default. If a future implementation wants to pre-seed `BioyondOffDeckMaterials` into a static config JSON, it must explicitly ask the user for the graph/config JSON path/location before editing that file.

When a material row has `status == 2` and `locations == []`, interpret it as present but not currently in deck/warehouse storage, most likely off-deck or inside an instrument unless Bioyond provides a more exact device location. The local action is:

- Find the matching live PLR resource by `unilabos_extra["material_bioyond_id"]`.
- Update the material's Bioyond metadata, including empty location fields and a marker such as `material_bioyond_location_state = "off_deck_unlocated"`.
- Unassign it from its current warehouse/deck slot using the correct parent/carrier API.
- Attach it as a child of `BioyondOffDeckMaterials`.
- If it is already under `BioyondOffDeckMaterials`, treat as metadata-only/no-op unless metadata changed.
- If no local resource exists, create the mapped material resource only if the material type is supported, set Bioyond metadata, and attach it under `BioyondOffDeckMaterials`; otherwise warn and skip local creation.

Full-scope `del_rest_bioyond=True` is different: if a Bioyond ID is absent from the aggregated external rows, remove that Bioyond-managed local resource even if it is currently under `BioyondOffDeckMaterials`.

## By-Order And Full-Scope Sync Semantics

The `materials-by-order-id` endpoint returns all materials, used and unused, for the given order. Do not filter rows by `isUse`; preserve `isUse` in metadata and process the row by Bioyond ID/status/location.

After every successful `materials-by-order-id` call, update `hardware_interface.material_cache` from all returned material rows and `detail` rows before applying deck mutations. Cache refresh is independent of whether a row becomes add/move/off-deck-attach/safe-unassign/gated-remove/no-op. This is station-specific behavior: the station/synchronizer updates the cache, then calls the shared row helper for each material.

When `order_id` is provided, `sync_from_external_by_order` is a single-order sync. It processes only the returned rows for that order and never performs `del_rest_bioyond` deletion.

When `order_id is None`, `sync_from_external_by_order` is full-scope mode:

- Query the latest 10 orders from `order-list` sorted by `creationTime desc`.
- Call `materials_by_order_id(order_id)` for each returned order.
- Aggregate all returned material rows by `row["id"]`.
- Refresh `hardware_interface.material_cache` from the aggregated rows and their `detail` rows.
- Process each aggregated material row through the same per-row helper.
- If `del_rest_bioyond=True`, after row processing reconcile-remove local deck materials with Bioyond IDs absent from the aggregated returned ID set or, equivalently after cache refresh, absent from the refreshed external Bioyond ID set represented by the aggregated rows/details. This deletion flag is only allowed for `order_id=None`, and only resources carrying `unilabos_extra["material_bioyond_id"]` are eligible.

For each returned material row, `status` 0 means 未入库，1 means 在库, 2 means 已出库:

- `bioyond_id = row["id"]` is the sole identity key.
- If `status == 1` and `locations` is non-empty:
  - If `bioyond_id` already exists locally, update metadata and compare location ID/slot.
  - If location is unchanged, do not mutate and do not log a per-material entry.
  - If location changed, move the existing PLR resource to the resolved new slot and log a position change.
  - If no local material has that `bioyond_id`, instantiate the mapped Bioyond resource class, attach it to the resolved slot, and log a new material. The station caller has already updated the material cache before this helper runs.
- If `status == 2` and `locations == []`:
  - Treat as a known external material that is off-deck/unlocated, not as deletion.
  - Update Bioyond metadata, clear location metadata, set `material_bioyond_location_state = "off_deck_unlocated"`, and move/create the live resource under `BioyondOffDeckMaterials`.
  - Keep the material in the resource tree under `BioyondOffDeckMaterials`; do not delete from this row even during full-scope sync.
  - Full-scope deletion/reconcile is based on Bioyond IDs absent from the aggregated returned rows, not on a present row with `status == 2`.
  - If none exists and the material type cannot be mapped to a PLR class, warn and skip local creation.
- If `status == 2` with non-empty locations, or any other non-`1` status:
  - Warn and skip mutation unless future evidence defines a safe behavior.
- If `status == 0`:
  - Treat as 未入库 and warn; do not create or move local resources.
- If `locations == []` with any status other than `2`:
  - Log a workflow non-fatal warning/error; do not delete.

Batch logging should include only:

- position changes,
- new materials,
- off-deck/unlocated parent changes,
- gated removed/deleted materials,
- actionable warnings/errors.

Do not log per-row no-ops when a material stays in place.

## Bioyond ID Storage And Local Lookup

For every PLR resource instance created from a Bioyond material row, store the external ID on the PLR object itself:

```python
resource.unilabos_extra["material_bioyond_id"] = row["id"]
```

`resource.unilabos_extra` is serialized to `ResourceDict.extra` when the deck is published. Keep the Bioyond metadata stable there:

- `material_bioyond_name`
- `material_bioyond_type`
- `material_bioyond_type_id`
- `material_bioyond_code`
- `material_bioyond_barcode`
- `material_bioyond_unit`
- `material_bioyond_quantity`
- `material_bioyond_status`
- `material_bioyond_is_use`
- `material_bioyond_location_id`
- `material_bioyond_location_code`
- `material_bioyond_warehouse_id`
- `material_bioyond_warehouse_name`
- `material_bioyond_location_x`
- `material_bioyond_location_y`
- `material_bioyond_location_z`
- `material_bioyond_location_state`
- `material_bioyond_source`
- `material_bioyond_last_order_id`
- `material_bioyond_last_seen_at`

The implementation should update these fields on every processed row, even when the local slot does not change.

To check whether a Bioyond material already exists locally, build an index by walking current PLR resources under the deck:

```python
def index_local_bioyond_materials(deck):
    index = {}

    def visit(resource):
        extra = getattr(resource, "unilabos_extra", {}) or {}
        bioyond_id = extra.get("material_bioyond_id")
        if bioyond_id:
            index[bioyond_id] = resource
        for child in getattr(resource, "children", []) or []:
            visit(child)

    visit(deck)
    return index
```

Lookup rule:

```python
local_resource = index_local_bioyond_materials(deck).get(row["id"])
```

This is the only local identity check. Do not search by `name`, `code`, `typeName`, barcode, slot code, or warehouse position. A resource with no `unilabos_extra["material_bioyond_id"]` is a local-only resource for conflict handling.

## Live PLR Add, Move, Unassign, Remove

Add path:

- Normalize the Bioyond row and require `row["id"]`.
- Classify the row as slot labware, liquid content, ignore, defer, or unsupported.
- For slot labware, map `typeName`/`typeId` to an explicit PLR resource class. Do not fall back to `RegularContainer` for unknown Bioyond types.
- Create the live PLR resource instance.
- Allocate Bioyond-created PLR resource names with one idempotent helper, not ad hoc suffixing:
  - If an existing resource is found by `material_bioyond_id`, keep its current `resource.name`; do not rename or append another suffix during update/move.
  - For a new resource, derive `base_name` only from the raw Bioyond row (`row["name"]`, then `row["typeName"]`, then a generic fallback). Never derive the next name from an already allocated local name.
  - If `base_name` is unused in the current serialized tree, use it.
  - If it is taken and `row["code"]` exists, try a readable code form such as `{base_name} ({code})`.
  - If still taken, choose the smallest available numeric suffix from the canonical `base_name`, such as `{base_name}_2`, `{base_name}_3`, etc.
  - Because suffixing always starts from canonical `base_name`, repeated sync must not produce stacked names such as `name_1_1`.
  - Store the full Bioyond ID in `unilabos_extra["material_bioyond_id"]`, not in the visible name.
- Ensure names are unique within the serialized tree, because current deserialization paths can restore UUID/extra by resource name.
- Set `resource.unilabos_uuid` if missing.
- Set `resource.unilabos_extra` using the Bioyond metadata contract above.
- Resolve the target slot ID-first.
- Assign only into an empty slot, a placeholder/string occupancy, or a local-only placeholder resource that the station policy says can be overwritten.
- Publish the deck after the batch mutation.

Move path:

- Find the existing live PLR resource by `unilabos_extra["material_bioyond_id"]`.
- Update Bioyond metadata in `unilabos_extra`.
- Resolve the target slot ID-first.
- If the target is the same parent/site, treat as metadata-only/no-op.
- If the target is occupied by another real resource, warn and defer/skip. Do not overwrite.
- If the move is safe, unassign from the old parent/site using the parent/carrier API, then assign to the target slot.
- Publish the deck after the batch mutation.

Unassign/remove path:

- Only Bioyond-managed resources with `unilabos_extra["material_bioyond_id"]` are eligible.
- A single ambiguous event or row with `locations == []` should not delete the live resource by default.
- For `status == 2` plus `locations == []`, unassign the material from deck/warehouse occupancy and attach it under `BioyondOffDeckMaterials`.
- Keep the material's `material_bioyond_id` and Bioyond metadata while it is under `BioyondOffDeckMaterials`.
- Actual remove/delete is allowed only for Bioyond IDs absent from the full-scope aggregate when `del_rest_bioyond=True`, or for a future external event with confirmed delete semantics. A present row with `status == 2` and `locations == []` is not that signal.
- Remove/deletion must log the Bioyond ID and previous parent/site, then publish the deck.

Parent/offspring guidance:

- Treat PLR `parent`, parent `children`, and itemized-carrier/warehouse site occupancy as the runtime source of parent/offspring truth.
- Use local PLR APIs that maintain both sides of the relationship: `assign_child_resource`, `assign_resource_to_site`, `warehouse[idx] = resource`, and `unassign_child_resource`.
- Bioyond `detail` rows are not automatically independent deck-slot resources. Keep them as child resources or liquid/metadata under their parent unless the station-specific classifier says the detail row is physical slot labware.
- `BioyondOffDeckMaterials` is the only planned shared virtual parent for locationless Bioyond materials. It has no Bioyond material ID and should be ignored by Bioyond material-ID indexing.
- Sirna-specific policy remains simple: no normal Sirna material should be stacked on another Sirna material; reagent reservoirs are standalone slot resources.

## Legacy Stock-Material Sync Semantics

The legacy stock-based sync remains named `sync_from_external` for compatibility/manual use:

- Still query `stock-material` for typeMode `0`, `1`, and `2`.
- Treat the response as unused materials across all orders, not all materials and not a full current deck snapshot.
- Update `hardware_interface.material_cache` from all returned rows and detail rows.
- Apply the same Bioyond-ID keyed add/move/update helper where possible.
- Do not delete local deck materials just because their Bioyond IDs are absent from the `stock-material` response.
- Never delete local resources that do not have a Bioyond ID.
- Publish the resource tree once after the batch if any add/move/off-deck-attach/safe-unassign/gated-remove occurred.

## Material-Change Report Semantics

`/report/material_change` posts one changed material row in `body.text`. It has the same material-row shape as `materials-by-order-id`.

Example inbound/on-deck post:

```json
{
  "method": "POST",
  "token": "",
  "request_time": "2026-05-27T17:52:32.2613047+08:00",
  "brand": "bioyond",
  "text": {
    "id": "3a217b05-bf40-5129-c8cf-70fe5d8c6d35",
    "typeName": "G3-200ul枪头盒",
    "code": "0016-00085",
    "barCode": null,
    "name": "G3-200ul枪头盒",
    "quantity": 1.0,
    "lockQuantity": 0.0,
    "unit": "个",
    "status": 1,
    "isUse": true,
    "parameters": null,
    "locations": [
      {
        "id": "00000000-0000-0000-0000-000000000000",
        "whid": "3a2083ab-5356-4056-083a-2c94d3eeddb4",
        "whName": "自动化堆栈",
        "code": "10-1",
        "x": 1,
        "y": 10,
        "z": 1,
        "quantity": 0
      }
    ],
    "detail": []
  }
}
```

Example outbound post:

```json
{
  "method": "POST",
  "token": "",
  "request_time": "2026-05-27T18:16:25.9371686+08:00",
  "brand": "bioyond",
  "text": {
    "id": "3a217b15-0d3a-64da-e55a-2ecd12b0eefd",
    "typeName": "384孔配平板",
    "code": "0023-00009",
    "barCode": null,
    "name": "384孔配平板",
    "quantity": 1.0,
    "lockQuantity": 0.0,
    "unit": "块",
    "status": 2,
    "isUse": false,
    "parameters": null,
    "locations": [],
    "detail": []
  }
}
```

When processing `process_material_change_report()`:

- Extract the material row from `report_data["text"]` / posted body `text`. If the row is missing or lacks `id`, warn and return a non-fatal skipped result.
- Delegate the normalized event to the shared synchronizer, e.g. `resource_synchronizer.handle_external_change(report_data, publish_resource_tree=True)`. Current source only ACKs this report, so this delegation is a required behavior change.
- Inside `handle_external_change`, update `hardware_interface.material_cache` from exactly this material row and its `detail` rows.
- Inside `handle_external_change`, call the same `process_bioyond_material_update(...)` helper used by `sync_from_external_by_order`, with `source="material_change"` and normal non-removal behavior.
- Apply only this posted material. Do not call `materials-by-order-id`, do not call `stock-material`, and do not mutate unrelated local materials.
- For the example `status == 2` with `locations == []`, do not delete from the single material-change event by default. Move/create the matching material under `BioyondOffDeckMaterials` when its type is supported; if no local match exists and the type is unsupported, warn and skip local creation after cache update.
- Publish the resource tree once only if this single-row update caused add/move/off-deck-attach/safe-unassign.

## Location And Conflict Rules

Resolve slot location in this order:

1. `locations[].id` through reverse `warehouse_mapping[*].site_uuids` or any configured Bioyond site-ID index.
2. `locations[].whid` through `warehouse_bioyond_ids` plus `locations[].code` / site UUID mapping.
3. Configured warehouse mapping by `whid`, `code`, and `x/y/z`.
4. Coordinate fallback using warehouse `bioyond_axis`, `bioyond_key_axis`, and `ordering_layout`.
5. `locations[].whName` as display-name fallback only.

Conflict policy:

- Same Bioyond ID in the target slot: update metadata/no-op.
- Different Bioyond ID already in the target slot: warn and skip the incoming row.
- Target slot contains a local UniLab-only resource with no Bioyond ID: overwrite only if it is a known placeholder or the station policy explicitly marks it safe to replace; otherwise warn and defer.
- Target slot contains stale placeholder/string occupancy: replace it.
- Multiple locations for one material: choose the first location only if the API or station config defines that as active; otherwise warn and skip until the rule is confirmed.

## Bioyond Material Classes

New Bioyond material types should be represented by explicit Bioyond-owned classes:

- Use `@resource(...)` on import-visible classes.
- Class default `model` must match the resource `id`.
- Use tolerant constructors: `def __init__(self, *args, **kwargs)` with `kwargs.setdefault(...)`, then `super().__init__(*args, **kwargs)`.
- For itemized resources, provide real `ordered_items` when wells/slots are modeled, or an empty `OrderedDict()` when intentionally not modeled.
- Do not inherit from PRCXI-specific classes or register PRCXI model IDs as Bioyond material models.
- Do not silently instantiate unmapped material rows as `RegularContainer`; report unmapped `id`, `typeName`, `status`, and location.

`BioyondOffDeckMaterials` is separate from the Bioyond material type classes:

- It is a shared virtual parent/resource class, not a material class selected by Bioyond `typeName` or `typeId`.
- It should be registered/import-visible for serialization/deserialization, but omitted from Bioyond material mapping tables.
- It must not carry `material_bioyond_id`, `material_bioyond_type`, or any other field that would make the parent look like a Bioyond-originated material row.

Sirna-specific class plan:

- 96-well plate: a thin generic `Plate` subclass without wells for now, using SBS rough dimensions and empty ordering.
- 12-well trough: a new Bioyond/Sirna generic `Plate` subclass with 12 simple `Well` children in a `12 x 1` layout. Use rough dimensions only; do not copy detailed PRCXI V-bottom/brand geometry.
- Reagent reservoirs such as `试剂槽RiboGreen`, `试剂槽DEPC H2O`, etc.: standalone reservoir materials, each represented as physical deck-slot labware with one large liquid-holding volume. Inherit generic `Container` unless a single-well `Plate` is truly needed by downstream APIs.
- In Sirna, reagent reservoirs are not children/siblings stacked on top of a 12-well trough. They occupy their own Bioyond slots as normal materials.

## Sirna Sync Trigger Policy

For Sirna, automatic material sync should use `sync_from_external_by_order`, not the old `sync_from_external`.

- After station init, call `sync_from_external_by_order(order_id=None, publish_resource_tree=True, del_rest_bioyond=True)` so Sirna syncs full-scope materials from the latest 10 Bioyond orders and removes stale local Bioyond-ID deck materials not present in that aggregate.
- During station/deck initialization, before automatic material sync, call `get_or_create_bioyond_off_deck_parent(deck)` so the virtual parent exists after edge start even before any `status == 2` row is processed.
- After `reset_stock_locations` / reset-stock-location flows, call `sync_from_external_by_order(order_id=None, publish_resource_tree=True, del_rest_bioyond=True)` for the same full-scope refresh.
- After `submit_experiment` / `submit_experiment_1` / `submit_experiment_2`, call `sync_from_external_by_order` only when the update-resource-tree box/flag is checked. Use the newly generated order ID. If the submit result only returns `orderCode`, resolve it to order ID first through `order-list` exact match.
- If submit creates multiple order IDs, sync each generated order ID and publish the resource tree once after the batch.
- In `process_order_finish_report()`, call `sync_from_external_by_order` using the order ID from the report or an order ID resolved from the report's `orderCode`.
- In `process_sample_finish_report()`, call `sync_from_external_by_order` using the order ID from the report or an order ID resolved from the report's `orderCode`.
- In `process_material_change_report()`, do not run an order sync. Update the cache and local resource tree for only the posted material row using the shared per-row helper.
- Do not trigger material sync from `process_step_finish_report()`. Step-finish handling should remain status/report/debug handling only.
- If no order ID can be resolved for a submit/order-finish/sample-finish hook, warn and skip order-scoped sync as a workflow non-fatal issue.
- After applying mutations, publish the resource tree once if anything changed.
- Keep raw HTTP capture under `_debug_call_session(...)` and `debug_log`; do not create noisy material no-op logs.

## Test Plan

- RPC test for `BioyondV1RPC.materials_by_order_id()` path `/api/lims/storage/materials-by-order-id`, envelope, and failure handling.
- Schema tests:
  - order-list full-scope query uses `pageCount: 10`, `skipCount: 0`, and `sorting: "creationTime desc"`,
  - orderCode resolver accepts only exact `orderCode` matches,
  - order/sample/step/material-change report parsers accept the embedded wrapper shapes,
  - order-finish `usedMaterials` accepts both `typemode` and `typeMode`.
- Unit tests for `process_bioyond_material_update()`:
  - new `status == 1` material with location adds resource and logs new material,
  - existing same ID/same location is no-op with no per-row log,
  - existing same ID/new location moves resource and logs position change,
  - `status == 2` with empty locations moves the matching resource under `BioyondOffDeckMaterials` during normal row processing,
  - `status == 2` with empty locations creates a supported mapped resource under `BioyondOffDeckMaterials` when no local resource exists,
  - `status == 2` with empty locations does not remove the resource even in full-scope row processing,
  - full-scope reconcile removes Bioyond-managed local resources absent from the aggregate, including resources under `BioyondOffDeckMaterials`,
  - `status == 0` warns and does not create,
  - `status == 1` with empty locations warns without mutation.
- Conflict tests:
  - different Bioyond ID in same slot warns/skips,
  - incoming Bioyond ID overwrites only stale placeholder/string or explicitly replaceable local-only occupancy,
  - incoming Bioyond ID does not overwrite a real occupied local-only resource unless station policy marks it replaceable,
  - stale placeholder/string slot can be replaced.
- Legacy stock sync tests:
  - old `sync_from_external(...)` still uses `stock-material`,
  - local Bioyond-ID resources absent from the `stock-material` response are not deleted,
  - local resources without Bioyond ID are preserved.
- Material-change report tests:
  - `process_material_change_report()` updates `hardware_interface.material_cache` from the posted row and detail rows,
  - material-change delegates to `resource_synchronizer.handle_external_change(...)`,
  - material-change `status == 2` with empty locations does not delete from the single event by default and moves/creates the material under `BioyondOffDeckMaterials` when supported,
  - material-change for an unknown outbound Bioyond ID updates cache and warns/skips local creation if the type is unsupported,
  - material-change does not call `materials-by-order-id`, `stock-material`, or mutate unrelated resources.
- Runtime/resource-tree tests:
  - Bioyond metadata is written to live `resource.unilabos_extra` and appears as `ResourceDict.extra` after `ResourceTreeSet.from_plr_resources([deck])`,
  - add/move/off-deck-attach/unassign mutates live PLR parent/children/site occupancy before publishing,
  - `BioyondOffDeckMaterials` serializes/deserializes as a virtual deck child, keeps its children, has no `material_bioyond_id`, and is reused instead of duplicated,
  - moving a material to `BioyondOffDeckMaterials` clears slot occupancy while preserving the child's `material_bioyond_id`,
  - publish calls `update_resource(resources=[deck])`, not hand-built `ResourceDict` patches.
- AST/action tests:
  - public Sirna `sync_from_external(...)` still uses legacy stock sync,
  - public Sirna `sync_from_external_by_order(order_id=None, publish_resource_tree=True)` queries latest 10 orders, aggregates their materials, and uses full-scope sync,
  - full-scope sync deduplicates repeated Bioyond material IDs across recent orders by keeping the newest order's row,
  - public Sirna `sync_from_external_by_order(order_id=None, publish_resource_tree=True, del_rest_bioyond=True)` deletes local Bioyond-ID deck materials absent from the aggregate,
  - public Sirna `sync_from_external_by_order(order_id, publish_resource_tree=True)` skips newest-order lookup and uses the given order ID,
  - public Sirna `sync_from_external_by_order(order_id, publish_resource_tree=True, del_rest_bioyond=True)` rejects or ignores delete-rest with a warning,
  - Sirna init/reset paths call `sync_from_external_by_order(order_id=None, del_rest_bioyond=True, ...)`, not `sync_from_external`,
  - Sirna submit-experiment paths call `sync_from_external_by_order` only when the update-resource-tree flag is checked,
  - Sirna order-finish and sample-finish report paths call `sync_from_external_by_order` after resolving order ID from `order_id` or `orderCode`,
  - Sirna step-finish report path does not trigger material sync.
- Resource class tests:
  - new Sirna 96-well plate instantiates and round-trips without wells,
  - new Sirna 12-well trough has 12 generic wells and no PRCXI model inheritance,
  - reagent reservoir classes instantiate as standalone slot materials,
  - `BioyondOffDeckMaterials` instantiates and round-trips without Bioyond material mapping or physical slot mapping.

## Assumptions And Evidence Boundaries

- `plan/peptide_sync_implementation_plan.md` was not present in this checkout; nearest local sync guidance was used only as practice context.
- The schemas and examples needed for implementation are embedded above; implementation should not depend on reading raw log files.
- `status == 1` with locations and `status == 2` with empty locations are represented by embedded examples. `status == 0` and `status == 2` with non-empty locations remain policy cases without a concrete material-row example found in this pass.
- Bioyond `status` / `isUse` delete semantics are still not enough by themselves to delete a live PLR resource from a single event; remove/delete remains gated.
- The requested status behavior is treated as the implementation contract unless later live evidence contradicts it.
- Bioyond material IDs are the only identity key for diffing; names, codes, type names, barcodes, and slots are metadata or diagnostics only.
