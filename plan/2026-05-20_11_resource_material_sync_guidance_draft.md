# Draft: Resource And Material Sync Guidance For Sirna And Similar Bioyond Systems

Status: draft for discussion, not an implementation mandate.

This note triangulates across five source categories with different authority:

1. `docs/developer_guide/examples/workstation_architecture.md`: desired shape
   and vocabulary. It is a design target, not proof that the current code has
   every behavior.
2. `BioyondWorkstation`, `BioyondResourceSynchronizer`, and shared graphio code:
   the current compatibility anchor. New guidance should mostly preserve this
   lifecycle and extend it deliberately.
3. Existing non-Sirna Bioyond stations: practical examples of how the shared
   base is used, including shortcuts that should not become policy.
4. Sirna implementation, plans, findings, and guide notes: stress-test evidence.
   They expose real missing cases, but the current Sirna code is not the
   architecture authority because parts were added without fully aligning to the
   shared base.
5. UniLabOS resource framework behavior around PLR resources,
   `ResourceTreeSet`, serialize/deserialize, and `update_resource(resources=...)`.

The short recommendation is:

Keep the shared Bioyond workstation lifecycle as the center of gravity. Evolve
the shared synchronizer with small project hooks for classification, ID-based
resolution, and non-slot material handling, rather than letting Sirna become a
parallel synchronization model. For Sirna, those hooks should resolve placement
by Bioyond IDs, distinguish physical slot labware from reagent liquid contents,
preserve Bioyond IDs in `unilabos_extra`, mutate the local PLR deck, and publish
the full deck through `update_resource(resources=[deck])`.

Do not treat every Bioyond stock row as a deck resource.

## Evidence Weighting

This task is not a Sirna implementation retrospective. It is a best-practice
alignment pass across the architecture target, the shared base class, observed
station behavior, and Sirna's newly exposed edge cases.

Use the sources this way:

- Architecture doc: ask "what shape should this system eventually have?"
- Shared Bioyond base: ask "what behavior must remain compatible today?"
- Other Bioyond stations: ask "what patterns are already working in practice?"
- Sirna current code and notes: ask "what did the base model fail to handle, and
  which Sirna fixes conflict with the shared lifecycle?"
- Live API/schema evidence: ask "what is true for this deployment's material,
  warehouse, and coordinate data?"

The Sirna AGENT_GUIDE is useful for finding caveats and prior investigations,
but it should not be cited as an independent source of truth when source code,
framework behavior, live API evidence, or architecture docs can answer the same
question.

## Mental Model

There are three resource worlds. Confusing them is the main source of bugs.

| World | Owner | Purpose | Recommended truth |
| --- | --- | --- | --- |
| Bioyond/LIMS | Bioyond APIs through `BioyondV1RPC` | External stock, material IDs, warehouse IDs, location IDs, inbound/outbound side effects | External material truth |
| PLR deck | Workstation driver | Runtime workstation material layout, warehouse occupancy, liquid contents | Local mutation surface |
| UniLabOS resource tree | `ResourceTreeSet` / ROS node / host resource APIs | Canonical UniLabOS/cloud representation | Framework/cloud truth |

The architecture guide describes `Deck` as the local material system and
`ResourceSynchronizer` as the optional external-system bridge
(`docs/developer_guide/examples/workstation_architecture.md:221`). The broader
framework serializes PLR objects into `ResourceTreeSet`, whose resource dicts
carry `id`, `uuid`, `parent_uuid`, `type`, `class`, `pose`, `config`, `data`,
and `extra` (`unilabos/resources/resource_tracker.py:107`).

Therefore the correct path is:

```text
Bioyond rows
  -> normalized material records
  -> preserve Bioyond materialTypeMode: Sample / Consumables / Reagent
  -> resolve material/location/warehouse IDs
  -> choose UniLabOS handling for that mode
  -> mutate PLR deck
  -> ResourceTreeSet.from_plr_resources([deck])
  -> ROS update_resource(resources=[deck])
  -> host/cloud resource-tree update
```

Avoid direct cloud JSON patches and avoid treating Bioyond records as already
being UniLabOS resource nodes.

## Target Architecture

The ideal architecture in `workstation_architecture.md` is still the right
direction:

1. `WorkstationBase` owns the local `deck`, workflow state, and hardware
   interface.
2. `ResourceSynchronizer` owns external material synchronization.
3. `BioyondResourceSynchronizer` owns the Bioyond-specific use of
   `BioyondV1RPC`.
4. `ROS2WorkstationNode.update_resource(resources)` owns the UniLabOS/cloud
   resource-tree update.
5. Optional HTTP report handlers may trigger local deck mutation, external sync,
   and cloud publication.

The documented startup sequence is:

1. Create workstation.
2. Initialize PLR deck and warehouses.
3. Create Bioyond RPC hardware interface.
4. Create resource synchronizer.
5. `sync_from_external()`.
6. Initialize ROS node and children.
7. `post_init(ros_node)`.
8. Upload `resources=[deck]`.

See `docs/developer_guide/examples/workstation_architecture.md:308`,
`docs/developer_guide/examples/workstation_architecture.md:497`, and
`docs/developer_guide/examples/workstation_architecture.md:737`.

Important caveat: that document is the hope. It is still valuable because it
names the desired responsibilities, but current Bioyond stations implement a
more limited, best-effort side-effect sync. When the doc and current code
diverge, use the doc to choose direction and the shared base code to choose the
next compatible step.

## Current Practical Behavior

The shared `BioyondWorkstation` implementation is the current behavioral
baseline. It does this today:

1. Requires a deck.
2. Reconstructs `deck.warehouses` from deck children/config when needed.
3. Creates `BioyondV1RPC`.
4. Installs `BioyondResourceSynchronizer`.
5. Immediately calls `sync_from_external()`.
6. Later, in `post_init`, publishes the whole deck with
   `ROS2DeviceNode.run_async_func(self._ros_node.update_resource, True,
   resources=[self.deck])`.

Relevant code:

- `BioyondResourceSynchronizer` is defined in
  `unilabos/devices/workstation/bioyond_studio/station.py:117`.
- `sync_from_external()` fetches stock `typeMode` 0, 1, and 2, then passes all
  rows to `resource_bioyond_to_plr(...)`
  (`unilabos/devices/workstation/bioyond_studio/station.py:147`).
- `BioyondWorkstation.__init__` installs the synchronizer and syncs immediately
  (`unilabos/devices/workstation/bioyond_studio/station.py:856`).
- `post_init()` uploads the deck (`unilabos/devices/workstation/bioyond_studio/station.py:893`).
- `resource_tree_add()` performs Bioyond create/inbound side effects
  (`unilabos/devices/workstation/bioyond_studio/station.py:958`).

This practical behavior works for simple physical-resource stock import, but it
does not provide full continuous two-way sync, conflict resolution, or reliable
stale-state cleanup. In particular:

- Re-fetching stock does not clearly clear stale local deck state first.
- Local-to-external update no-ops unless `unilabos_extra["update_resource_site"]`
  is present.
- `process_material_change_report()` is mostly TODO-level in the base station.
- Some station-specific code bypasses the shared synchronizer with direct LIMS
  calls.
- Some existing stations contain shortcuts that should not be copied, such as
  duplicate initialization, stale globals, or hardcoded warehouse/axis cases.

Treat current Bioyond behavior as operationally useful and compatibility
important, not as proof that the ideal architecture is already achieved. The
goal is to tighten this base path, not replace it with a Sirna-only path.

## Recommended Shared Pipeline

For Sirna and similar Bioyond systems, the base synchronizer should evolve from
the current shared path into one shared pipeline with small project hooks:

```text
sync_from_external()
  fetch stock rows from Bioyond
  update RPC material cache
  normalize rows into a common internal shape
  preserve materialTypeMode: Sample / Consumables / Reagent
  resolve warehouse/location by Bioyond IDs when available
  choose handling for the row's mode
  apply Sample / Consumables rows as mapped slot labware by default
  apply Reagent rows as physical reagent labware or liquid content by evidence
  report unknown modes or unmapped handling loudly
  publish deck if deck changed and ROS node is available
```

Suggested extension points:

```python
class BioyondResourceSynchronizer(ResourceSynchronizer):
    def external_material_mode(self, row: dict) -> str:
        return row["materialTypeMode"]  # Sample | Consumables | Reagent

    def resolve_external_row(self, row: dict) -> dict:
        ...

    def apply_external_row(self, row: dict, mode: str, resolved: dict) -> None:
        ...
```

The base class should continue to own:

- stock fetches for `typeMode` 0/1/2;
- material-cache refresh;
- common normalization;
- mode validation for `Sample` / `Consumables` / `Reagent`;
- publication orchestration;
- error aggregation;
- stale-state policy once agreed.

Project code should own only the parts that are truly deployment-specific:

- material type/mode to PLR class mapping;
- project-local handling inside `Sample` / `Consumables` / `Reagent`;
- project-local warehouse ID/name mapping;
- project-local coordinate conventions;
- special row handling such as Sirna reagent-as-liquid.

This keeps existing Bioyond stations close to the same lifecycle while still
absorbing the Sirna lessons that the earlier base did not model. The default
hook behavior can remain "Sample/Consumables/Reagent rows become mapped slot
labware" for simpler stations; Sirna should override Reagent handling where
evidence requires liquid-content behavior.

## Sirna Findings To Feed Back Into Shared Design

Treat Sirna as a stress test for the shared base, not as a replacement design.
It raises real questions that earlier stations did not need to answer:

- within the `Reagent` mode, some external rows may be liquid contents rather
  than slot-occupying reagent labware;
- placement should be ID-first where Bioyond supplies material/location IDs;
- warehouse and axis metadata must survive serialize/deserialize;
  project-specific mode handling is available.

For Sirna-like deployments, the old implicit rule "stock row equals PLR
resource" is too coarse. For simpler deployments, it can remain the default
mode-handling behavior.

### IDs Win

Placement identity should prefer:

```text
materialId
locationId
materialTypeId
warehouseId / whid
```

Display/debug fields are not identity:

```text
materialName
materialCode
locationCode
locationShowName
warehouseName / whName
```

`locationCode` such as `1-1` is not globally unique. It can exist in multiple
warehouses. Code-only resolution may be kept as a diagnostic fallback, but it
must raise on ambiguity.

The current Sirna ID-first resolver is useful source evidence for this direction
(`unilabos/devices/workstation/bioyond_studio/sirna_station/sirna_station.py:3659`).
The Sirna mega plan captures the same concern, but the implementation and live
Bioyond IDs are the stronger evidence.

### Bioyond Modes And UniLabOS Handling Are Different

Bioyond has three primary material modes in this context:

```text
Sample
Consumables
Reagent
```

Those modes should be preserved as the external taxonomy. UniLabOS still needs a
handling decision inside the mode: should the row create/place a physical PLR
resource, or update contents on an already-placed parent resource?

Physical slot resources include things like:

- tip racks;
- plates;
- cell culture plates;
- empty trough/bottle labware;
- other objects that occupy a warehouse slot.

Reagents are contents of a parent labware when the row describes a liquid rather
than a physical holder. For Sirna, Bioyond `stock_material(typeMode=2)` returns
`materialTypeMode="Reagent"` rows; those rows still need evidence-based handling
as either physical reagent labware or reagent liquid content. The finding in
`temp_benyao/sirna/_findings/2026-05-07_reagents_vs_resources.md:6` records the
bug: the generic path can fall back to `RegularContainer`, fail registry lookup,
and drop reagent rows such as `试剂槽裂解液/Betame`.

Recommended behavior:

1. Validate `materialTypeMode` as `Sample`, `Consumables`, or `Reagent`.
2. For `Sample` and `Consumables`, default to mapped slot labware: instantiate
   the mapped PLR class and place it into the resolved warehouse slot.
3. For `Reagent`, decide whether the row represents physical reagent labware or
   reagent liquid content from material type evidence, row shape, and live data.
4. For physical reagent labware, place the mapped PLR resource in the resolved
   slot.
5. For reagent liquid content, find the parent trough/bottle and attach liquid
   metadata to its tracker.
6. Preserve Bioyond IDs in `parent.unilabos_extra["reagent_bioyond_ids"]`.
7. Make liquid attachment idempotent by Bioyond material ID.
8. If the parent labware is missing or the mode/handling cannot be resolved,
   defer or log with IDs; do not guess.

The existing Sirna helper `_attach_liquid_to_parent()` already follows the
idempotent metadata direction
(`unilabos/devices/workstation/bioyond_studio/sirna_station/sirna_station.py:4074`).

### The `0003` Question Must Stay Evidence-Based

Do not blindly map `materialTypeCode 0003` to `BioyondSirna_ReagentTrough`.

If live/schema evidence says a `0003` row is physical trough labware, map it to
a PLR resource class. If evidence says it is liquid content, attach it to the
parent trough. If evidence is contradictory, mark it unsupported and log the
Bioyond IDs.

Treat this as an open design decision until source/schema/live evidence settles
the row shape. The Sirna plan records the question, but it should not decide the
mapping by itself.

### Sirna Warehouse And Axis Rules Are Project-Local

Sirna warehouse layout and axis conventions must be verified from Sirna source,
Sirna schema, current deck behavior, and live/read-only Sirna APIs when
available. Do not import Peptide, reaction, dispensing, or cell station layout
truth.

For Sirna specifically, the current integrated deck display should be treated as
the good baseline. The prior col-row slot-key fix is already present in the
deck/graphio path, and any remaining x/y or y-reverse correction should be
handled through shared warehouse metadata and shared graphio/display mapping
rather than by reshaping the Sirna station display ad hoc.

### Display Geometry: Evidence Before Axis Values

Peptide resource sync should not be treated as a validated model, but its
UniLabOS display work is useful and should influence this guidance.

Confirmed Peptide behavior:

- Peptide live warehouse evidence found `自动化堆栈` with 170 locations where
  `code="10-17"` appears at `x=17, y=10`
  (`../Uni-Lab-OS-Peptide/temp_benyao/peptide/_findings/2026-05-13_1404_peptide_col_row_deck.md:6`).
- Peptide's current display convention is Peptide-specific evidence:
  `bioyond_axis="xy_col_row"` and `bioyond_key_axis="col_row"` in the current
  Peptide station implementation. Do not copy that pair into Sirna or any other
  project without live warehouse evidence for that target system. With this
  Peptide combination, graphio does not apply the legacy x/y swap
  (`../Uni-Lab-OS-Peptide/unilabos/resources/graphio.py:868`).
- Peptide models the main automation stack as 17 visual rows by 10 visual
  columns while preserving labels such as `10-17`
  (`../Uni-Lab-OS-Peptide/unilabos/resources/bioyond/decks.py:308`).
- Peptide warehouses and deck child positions use `frontend_y_flip=True` /
  `_frontend_y_flipped_coordinate(...)` so stored PLR coordinates compensate for
  the frontend y-axis inversion
  (`../Uni-Lab-OS-Peptide/unilabos/resources/bioyond/decks.py:299`;
  `temp_benyao/peptide/_findings/2026-05-13_1514_frontend_y_flip_layout.md:6`).
- The older Peptide graph-layout note is stale for the Sirna discussion. The
  current Sirna integrated station/deck display is a good baseline; the reusable
  Peptide lesson is axis metadata and frontend y-reverse compatibility.
- Peptide tests encode the intended behavior: `10-17` lands at row 17 /
  column 10, and after frontend y-flip the displayed site positions match the
  expected top-to-bottom layout
  (`../Uni-Lab-OS-Peptide/temp_benyao/peptide/tests/test_peptide_deck_layout.py:60`).

Guidance for Sirna and similar systems:

1. Treat `bioyond_axis` and `bioyond_key_axis` as two separate concepts:
   - `bioyond_axis` describes how Bioyond numeric `x/y` map to visual axes.
   - `bioyond_key_axis` describes how slot labels such as `10-17` are generated
     or preserved.
2. Do not infer display orientation from label strings alone. Both `row_col`
   and `col_row` can preserve the same final label text while producing
   different visual layouts and graphio swap behavior.
3. Use live/read-only `code/x/y` evidence for each project and each warehouse
   family before choosing axis metadata.
4. Keep visual orientation fixes separate from material identity. IDs and slot
   labels determine registration; display dimensions and frontend y-flip only
   determine how the deck appears.
5. Author intended display coordinates first, then convert stored y coordinates
   for the active frontend convention:
   - deck child stored y = `deck_height - display_y - child_height`;
   - warehouse site stored y = `warehouse_height - display_y - site_height`;
   - graph-level y values should be transformed only when the active frontend
     convention requires it.
6. Preserve the current Sirna display as the baseline unless concrete frontend
   evidence shows a y-reverse problem. Do not change station/deck graph
   semantics just to match stale Peptide layout notes.
7. Add layout tests that assert:
   - warehouse `num_items_x`, `num_items_y`, capacity, first key, and last key;
   - representative `code/x/y` examples land on the intended site key;
   - frontend y-flip produces expected displayed positions;
   - generated deck children do not overlap in displayed coordinates.
8. When borrowing from Peptide, borrow the evidence pattern: live discovery,
   axis/key-axis metadata, y-flip tests, and display fixtures. Do not borrow a
   literal axis pair or Peptide resource-sync behavior as a validated path.

Concrete live discovery workflow:

1. Prefer the reusable read-only probe pattern before reading old findings:

```bash
python3 temp_benyao/sirna/tests/probe_readonly_storage_inventory.py \
  --base-url <api_host> \
  --api-key <api_key> \
  --output temp_benyao/<project>/_logs/<timestamp>_readonly_storage_inventory_probe.json
```

This probe checks swagger candidates, project storage/location endpoints,
material type endpoints, `warehouse-info-by-mat-type-id`, and `stock-material`,
then writes a redacted evidence file and a merged warehouse summary.

2. For Sirna quick checks, `temp_benyao/sirna/tests/discover_sirna_warehouses.py`
   is the narrower historical helper. Treat it as a template unless it has been
   parameterized for the target config.
3. Query `/api/storage/location/locations-by-type?type=0&typeMode=0&materialType=0`
   first for stack names, warehouse IDs, full slot lists, `code`, `x`, `y`, `z`,
   and display mode. This endpoint is the topology source when available.
4. Cross-reference with `/api/lims/storage/material-types` and
   `/api/lims/storage/warehouse-info-by-mat-type-id` to learn material-type
   placement constraints. Use `stock-material` only for occupied-slot evidence;
   it cannot reveal empty topology.
5. Infer `bioyond_key_axis` from how slot labels must be generated or preserved,
   and infer `bioyond_axis` from how raw Bioyond `x/y` must map to PLR holder
   indices. A label such as `10-17` alone is ambiguous; compare it to the same
   record's `x/y`, and use boundary examples from non-square stacks.
6. Encode the discovered convention on the warehouse resource, not in a one-off
   station branch. The metadata must serialize/deserialize with the warehouse
   because graph load and cloud sync rebuild resources.
7. Add or update layout tests before changing Sirna display behavior. For Sirna,
   test against the current good display first, then only change shared x/y or
   y-reverse mapping if the fixture demonstrates a real mismatch.

### Deserialize Must Be Idempotent

This is confirmed framework behavior, not a possible risk. Resource publication
builds `ResourceTreeSet` from live PLR resources by calling
`resource.serialize()` and `resource.serialize_all_state()`
(`unilabos/resources/resource_tracker.py:547`). Readback/reconstruction goes
through `ResourceTreeSet.to_plr_resources()`, finds the PLR subclass, calls
`sub_cls.deserialize(...)`, then restores PLR state, UUIDs, and `unilabos_extra`
(`unilabos/resources/resource_tracker.py:637`). `ResourceTreeSet.dump()` also
serializes resource nodes while excluding `children` from each individual node
record (`unilabos/resources/resource_tracker.py:914`), so parent/child
relationships and object state must survive the framework tree shape rather than
only an in-memory PLR object graph.

Sirna resource/deck classes therefore must survive:

1. registry-time construction;
2. `Resource.deserialize()` round trips;
3. cloud-synced deck state with serialized children.

If a synced deck already has serialized `children`, default setup must not
create duplicate/stale child resources. Sirna noticed this problem, but the
reason is framework-level: cloud/resource-tree sync reconstructs PLR objects
from serialized state, so constructors and setup logic must be idempotent.

Existing Sirna deck code already acknowledges the issue: `BIOYOND_Sirna_Deck`
turns `setup=False` during deserialize when serialized `children` are present
(`unilabos/resources/bioyond/decks.py:153`). Sirna material classes also use
registry-visible `@resource(...)` decorators and tolerant constructors with
`*args, **kwargs`, defaults, and `ordering` fallbacks
(`unilabos/resources/bioyond/sirna_materials.py:14`). Treat those as required
patterns for any new synced resource class.

Guidance:

- Do not add a deck/resource class until it round-trips through
  `Resource.serialize()` / `Resource.deserialize()` and
  `ResourceTreeSet.from_plr_resources(...).to_plr_resources()`.
- Preserve `unilabos_uuid`, parentage, `unilabos_extra`, and liquid state across
  the round trip.
- Make default setup conditional: if serialized children exist, do not recreate
  default warehouses or default child resources on top of them.
- Itemized PLR subclasses must provide `ordered_items` or `ordering`; otherwise
  deserialization can fail or rebuild a different structure.

## Startup And Resync Recommendation

The current Sirna code installs `SirnaResourceSynchronizer` after
`super().post_init(ros_node)` (`sirna_station.py:363`). That is useful as a
phase patch and as evidence that classification hooks are needed, but it should
not become the long-term lifecycle. Base initialization may already have run
generic stock sync and base post-init may already have published the deck before
the Sirna synchronizer is installed.

Recommended pathway:

1. Keep the `BioyondWorkstation` lifecycle as the base path.
2. Add a synchronizer factory or hook registration point before eager sync, for
   example `create_resource_synchronizer()`.
3. Let Sirna provide classification/resolution hooks through that shared
   synchronizer shape.
4. Make startup sync use the installed hook-aware synchronizer before the first
   deck publication.
5. Make manual resync, reset resync, report-triggered resync, and future
   periodic sync all call `self.resource_synchronizer.sync_from_external()`.
6. Do not instantiate fresh base synchronizers inside Sirna actions, because
   that bypasses the installed project logic.

This directly addresses the overlap finding in
`temp_benyao/sirna/_findings/2026-05-12_sirna_synchronizer_overlap.md:6`.

## Material Registration From Create-Order Results

Create-order allocation registration should remain separate from stock sync, but
it should use the same classification and apply logic.

Recommended create-order pipeline:

1. Normalize allocation records into:

```python
{
    "materialId": "...",
    "materialCode": "...",
    "materialName": "...",
    "materialTypeId": "...",
    "materialTypeCode": "...",
    "materialTypeMode": "Sample|Consumables|Reagent",
    "materialTypeName": "...",
    "locationId": "...",
    "locationCode": "...",
    "locationShowName": "...",
}
```

2. Resolve warehouse and slot by:

```text
material-info(materialId)
  -> warehouse-info-by-mat-type-id(materialTypeId) matched by locationId
  -> code-only diagnostic fallback only if unambiguous
```

3. Classify as `slot_labware`, `liquid_content`, or `unsupported`.
4. Apply mutation to the PLR deck.
5. Publish the full deck once after the batch.

The current Sirna `_register_materials_to_tree()` already documents this flow
(`sirna_station.py:3659`) and should be the local reference implementation until
the shared hook is designed.

## Cloud / UniLabOS Publication Rules

Always mutate real tracked PLR resources first. Then publish through the
framework.

Correct call shape:

```python
ROS2DeviceNode.run_async_func(
    self._ros_node.update_resource,
    True,
    **{"resources": [self.deck]},
)
```

The real method is:

```python
async def update_resource(self, resources: List["ResourcePLR"])
```

See `unilabos/ros/nodes/base_device_node.py:727`.

Do not call:

```python
update_resource(resource_name=..., resource_data=...)
```

Do not manually serialize the deck for this path. `update_resource()` creates a
`ResourceTreeSet`, sends it to `/c2s_update_resource_tree`, and applies returned
UUID mappings. Missing root parent UUIDs are auto-mounted to the current device,
so parentage should be preserved on PLR objects before publication.

## Metadata Contract

Every Bioyond-originated slot resource should carry enough metadata for unload,
audit, and later sync:

```python
plr_resource.unilabos_extra = {
    "material_bioyond_id": mat["materialId"],
    "material_bioyond_code": mat["materialCode"],
    "material_bioyond_name": mat["materialName"],
    "material_bioyond_type_id": mat["materialTypeId"],
    "material_bioyond_type_code": mat["materialTypeCode"],
    "material_bioyond_type_mode": mat["materialTypeMode"],
    "location_bioyond_id": mat["locationId"],
    "location_code": resolved["location_code"],
    "warehouse_bioyond_id": resolved["warehouse_id"],
    "warehouse_bioyond_name": resolved["warehouse_name"],
    "location_resolution_source": resolved["source"],
}
```

Every Bioyond-originated liquid content should preserve equivalent metadata on
the parent labware:

```python
parent.unilabos_extra.setdefault("reagent_bioyond_ids", []).append({
    "material_bioyond_id": mat["materialId"],
    "material_bioyond_code": mat["materialCode"],
    "material_bioyond_name": mat["materialName"],
    "material_bioyond_type_id": mat["materialTypeId"],
    "material_bioyond_type_code": mat["materialTypeCode"],
    "location_bioyond_id": mat["locationId"],
    "quantity": mat.get("quantity"),
    "location_resolution_source": resolved["source"],
})
```

This metadata is not decorative. It is required for correct unload, audit,
duplicate prevention, and future round-trip sync.

## Stale State And Conflict Policy

This is confirmed current behavior, not just a theoretical risk.

Current stock import places resources into empty warehouse slots and skips
occupied slots. That prevents overwrites, but it can leave stale local resources
after external moves/deletes.

Evidence:

- `BioyondResourceSynchronizer.sync_from_external()` fetches Bioyond stock and
  delegates to `resource_bioyond_to_plr(...)`, but it does not compute a
  before/after diff or remove local resources absent from the snapshot
  (`unilabos/devices/workstation/bioyond_studio/station.py:147`).
- `resource_bioyond_to_plr(...)` only assigns when the target warehouse position
  is empty or a placeholder; when a real resource already occupies the slot, it
  logs "跳过放置" and leaves the existing object in place
  (`unilabos/resources/graphio.py:936`).
- Bioyond outbound/removal logic exists in separate local-to-external hooks such
  as `resource_tree_remove(...)`, but that is not invoked by stock refresh for
  Bioyond rows that disappeared externally
  (`unilabos/devices/workstation/bioyond_studio/station.py:987`).

Recommended direction:

1. Define Bioyond as the source of truth for external stock snapshots unless a
   local operation is in progress.
2. Before applying a stock snapshot, compute a diff by Bioyond material ID.
3. Remove or mark local resources whose Bioyond IDs disappeared from the
   snapshot, subject to workflow safety checks.
4. Move local resources whose Bioyond location ID changed.
5. Attach/detach liquid contents idempotently by Bioyond material ID.
6. Publish once after applying the batch.
7. In ambiguous or active-operation cases, log and require manual confirmation.

Until this policy exists, call the current implementation "refresh/import" rather
than "authoritative synchronization."

## External-To-Local And Local-To-External Boundaries

External-to-local:

- Bioyond stock and create-order allocation rows mutate the local PLR deck.
- Then UniLabOS publication derives resource trees from the PLR deck.

Local-to-external:

- `resource_tree_add` / update / remove may call Bioyond add, inbound, outbound,
  or move APIs.
- These paths should require Bioyond IDs or explicit creation parameters.
- Movement should use `unilabos_extra["update_resource_site"]` only as an
  explicit request, not as hidden ambient state.
- After successful Bioyond side effects, refresh or update the PLR deck and
  publish the deck.

Avoid station-level direct LIMS calls that bypass the synchronizer unless the
action explicitly reconciles the deck afterward.

## Error Handling Rules

Fail loudly on:

- unknown material type code/name;
- unresolved warehouse ID/name;
- location ID not found in `warehouse-info-by-mat-type-id`;
- code-only location ambiguity;
- missing parent labware for liquid content;
- invalid PLR resource class or `Resource.deserialize()` failure;
- Bioyond RPC returning empty/fallback values where an ID is required.

Do not silently create `RegularContainer` to hide mapping failures. The Sirna
finding at `temp_benyao/sirna/_findings/2026-05-07_reagents_vs_resources.md:52`
calls this out as a reusable trap.

## Tests And Validation

Minimum offline tests:

1. `update_resource` is called only with `resources=[deck]`.
2. `ResourceTreeSet.from_plr_resources([deck])` preserves UUIDs, parentage,
   `data`, and `unilabos_extra`.
3. Sirna allocation records resolve by `material-info` first.
4. `warehouse-info-by-mat-type-id` resolves by `locationId`.
5. Code-only fallback raises on ambiguous slots.
6. `Sample` and `Consumables` rows become slot labware.
7. Reagent content rows become parent liquid metadata.
8. Re-running registration does not duplicate liquid entries.
9. Missing parent labware is deferred/logged, not guessed.
10. Unknown material type emits Bioyond IDs.
11. Deck deserialize does not recreate duplicate default children.
12. Warehouse coordinate mapping is tested per warehouse, not globally.
13. A synced Sirna deck round-trips through PLR deserialize and
    `ResourceTreeSet` conversion without duplicate default children.
14. Stock refresh with a missing Bioyond material ID proves current add/skip
    behavior first, then verifies the chosen diff/delete policy once implemented.
15. Display geometry tests cover project-local live discovery fixtures,
    `bioyond_axis`, `bioyond_key_axis`, representative `code/x/y` mappings,
    frontend y-flip, and no-overlap deck layout.

Focused Sirna command:

```bash
pytest temp_benyao/sirna/tests/test_sirna_resource_system.py -q
```

Shared conversion command, when changing graphio or Bioyond converters:

```bash
pytest tests/resources/test_converter_bioyond.py -q
```

Live/read-only validation should capture fixtures for:

- `stock_material(typeMode=0/1/2, includeDetail=true)`;
- `material-info(materialId)`;
- `warehouse-info-by-mat-type-id(materialTypeId)`;
- create-order allocation records;
- frontend/cloud resource-tree readback after publication.

## Discrepancies To Discuss

### 1. Architecture Is Direction; Base Code Is The Compatibility Anchor

The architecture guide presents `ResourceSynchronizer` as bidirectional. Current
Bioyond code is mostly eager external import plus selected add/remove/update
side effects. It has no complete continuous conflict-resolution loop.

Sirna current code should be read in this context: it exposes missing
capabilities, but parts of it conflict with the existing base lifecycle because
it was layered on after the base sync behavior already existed.

Recommendation: keep the bidirectional architecture as the target, preserve
`BioyondWorkstation` as the implementation anchor, and introduce shared hooks
for the missing Sirna-class problems. Describe the current implementation as
best-effort refresh plus Bioyond side effects until a diff/conflict policy
exists.

### 2. The Doc Centers `Deck`; The Framework Centers `ResourceTreeSet`

The architecture guide uses PLR `Deck` as the material system. UniLabOS resource
tracking uses `ResourceTreeSet` as the canonical serialized representation.

Recommendation: phrase the model as "Deck is the local PLR mutation surface;
ResourceTreeSet is the UniLabOS/cloud representation derived from it."

### 3. Base Bioyond Sync Treats Every Stock Row As PLR Resource

`BioyondResourceSynchronizer.sync_from_external()` currently feeds typeMode
0/1/2 rows into `resource_bioyond_to_plr(...)`.

Sirna evidence shows this is wrong for reagent/content-like rows.

Recommendation: add classification hooks before conversion. Rows classified as
liquid content must not go through standalone PLR resource conversion.

### 4. Sirna Fix Exists As A Fork, Not A Shared Hook

Current `SirnaResourceSynchronizer` captures the important reagent-as-liquid
question, but it duplicates or bypasses shared sync mechanics. Startup can run
base sync before the Sirna synchronizer is installed, and manual/reset paths
have used fresh base synchronizers.

Recommendation: keep the discovered behavior where evidence supports it, but
refactor the shape. A factory/hook in the shared synchronizer is preferable to a
long-lived parallel sync class.

### 5. Sirna Create-Order Registration Is Stronger Than Stock Sync

Create-order registration now has ID-first resolution and a clearer classifier.
That is valuable evidence, not automatically the canonical sync design. The
stock sync path still resolves external liquid rows by warehouse name/code and
stores weaker metadata.

Recommendation: extract the stronger resolver/classifier ideas into shared
hooks, then converge stock sync and create-order registration on the same
functions.

### 6. Graphio Has Shared Fragility

`resource_bioyond_to_plr()` contains hardcoded warehouse/coordinate cases and a
fallback to `RegularContainer`. Hardening it affects multiple Bioyond projects.

Recommendation: after Sirna is green, discuss shared graphio hardening in a
separate change: no silent `RegularContainer`, explicit unknown-type diagnostics,
and project-specific axis metadata instead of hardcoded names.

### 7. Serialize/Deserialize Is A Gating Contract

This is not optional documentation neatness. Any new resource/deck class or sync
metadata must pass the framework serialize/deserialize path before it can be
trusted in cloud-synced operation.

Recommendation: make round-trip tests part of acceptance for resource-system
changes, especially for Sirna deck setup, warehouse metadata, `reagent_bioyond_ids`,
liquid tracker state, and itemized labware ordering.

### 8. Existing Stock Sync Is Add/Skip-Biased, Not Delete-Aware

Current `sync_from_external()` imports and places observed Bioyond resources,
but it does not delete local resources missing from the latest stock snapshot.
When a target slot is occupied by a real resource, graphio skips placement rather
than replacing it.

Recommendation: describe the current behavior as "stock refresh/import" until a
Bioyond-ID diff policy is implemented. Deletion/removal must be a deliberate
phase, with workflow-safety checks and tests, not implied by the current stock
sync name.

### 9. Peptide Display Is Useful; Peptide Sync Is Not Yet Authority

Peptide's latest display work is useful evidence for Peptide's own warehouse
axis/key-axis metadata and for the shared need to account for frontend y-axis
inversion. It does not define Sirna's axis values. Its resource sync remains
untested/problematic and should not be treated as the model for Sirna sync
behavior.

Recommendation: fold the Peptide display lesson into shared guidance as a
discovery-and-test pattern, not as a literal axis pair. Keep Sirna display
behavior grounded in Sirna live evidence and the current good deck baseline; keep
resource synchronization recommendations grounded in the Sirna ID-first
registration path and the shared Bioyond synchronizer refactor.

### 10. Some Existing Stations Should Be Examples Only, Not Templates

Non-Sirna stations show useful patterns, but also shortcuts:

- direct LIMS calls bypassing the synchronizer;
- duplicate `super().__init__()` in reaction station;
- stale/undefined `WAREHOUSE_MAPPING` references;
- RPC methods collapsing failures into empty values;
- no robust stale deck cleanup on stock refresh.

Recommendation: borrow the shared deck publication and Bioyond ID metadata
patterns, not the incidental shortcuts.

## Recommended Discussion Path

For the next implementation discussion, decide these in order:

1. What is the smallest shared-base extension that lets project hooks exist
   before eager sync without breaking existing Bioyond stations?
2. Should `BioyondWorkstation` gain a synchronizer factory, a hook registry, or
   a delayed eager-sync point?
3. Is `materialTypeCode 0003` in current Sirna live data physical labware,
   liquid content, or both depending on row shape?
4. Where should Sirna warehouse Bioyond IDs live: station config, deck metadata,
   or both?
5. Should stock refresh clear/diff deck state now, or remain append/skip until
   after create-order registration is stable?
6. Should `update_resource` remain async/non-blocking for material registration,
   or should selected user-facing actions fail if publication fails?
7. Which parts of the Sirna reagent-as-liquid rule are project-local, and which
   should become shared Bioyond behavior after Peptide/cell validation?
8. Should graphio fallback hardening land before or after Sirna stock sync is
   fully validated?
9. Should any remaining Sirna x/y or y-reverse issue be fixed in shared
   graphio/display mapping after a live fixture proves it, or should the current
   Sirna deck remain untouched?

## Recommended Pathway

The pragmatic path is:

1. Preserve and test the current `BioyondWorkstation` lifecycle as the shared
   compatibility baseline.
2. Treat Sirna create-order registration as a local proof of the missing
   classification/ID-resolution behavior, not as the architecture shape to copy.
3. Validate live/read-only Sirna fixtures for `0003`, warehouse IDs, and stock
   reagent rows.
4. Refactor the shared Bioyond synchronizer to expose classification,
   resolution, and non-slot row hooks with default behavior matching existing
   stations.
5. Make Sirna install hooks before the first external sync and first deck
   publication through the shared lifecycle.
6. Route startup, manual resync, reset resync, and report-triggered resync
   through the installed synchronizer.
7. Converge stock sync and create-order registration on one
   resolver/classifier/apply implementation.
8. Add stale-state diffing only after the basic ID-first path is stable.
9. Harden graphio fallback as a shared follow-up.

This gives Sirna the necessary behavior without locking in a second Bioyond sync
system that future projects will have to debug around.
