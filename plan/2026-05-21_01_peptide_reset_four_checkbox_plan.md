# Peptide Four-Checkbox Reset Plan

Date: 2026-05-21 16:30
Status: Proposal only / not executed

## Scope

This plan replaces `2026-05-21_1556_peptide_reset_sirna_reference_plan.md` for Peptide reset work.

User direction captured here:

- `take_out` is unnecessary for Peptide reset.
- Do not add a material-cache refresh checkbox.
- Change reset to four checkbox-controlled operations:
  - 调度器复位
  - 订单状态复位
  - 库位复位
  - 仪器复位
- The first three checkboxes default to checked.
- The fourth checkbox, 仪器复位 / `reset_devices`, defaults to unchecked.
- Replace the current public `reset` action with:
  - `reset_auto`: normal ILab action node. This is the renamed/replaced version of the current reset implementation.
  - `reset_manual`: manual-confirm action node with a physical cleanup confirmation message.

## Evidence Summary

Current Peptide source:

- Reset action code is currently in `unilabos/devices/workstation/bioyond_studio/peptide_station/peptide_station.py`.
- Current Peptide reset selects `scheduler_reset`, `reset_order_status`, and `reset_location`, and passes ids to order/location resets.
- `BioyondV1RPC.reset_devices()` already calls `/api/lims/device/reset-devices` with only `apiKey` and `requestTime`.
- `BioyondV1RPC.scheduler_reset()` already calls `/api/lims/scheduler/reset` with only `apiKey` and `requestTime`.
- `BioyondV1RPC.reset_order_status(order_id)` and `reset_location(location_id)` currently send `data`, but live probes showed that omitted `data` succeeds.

Live Peptide no-data reset probes using `temp_benyao/peptide/peptide_station_config.example.json`:

- `POST /api/lims/order/reset-order-status` with request keys `["apiKey", "requestTime"]` returned HTTP 200 and `code=1`.
- `POST /api/lims/scheduler/reset` with request keys `["apiKey", "requestTime"]` returned HTTP 200 and `code=1`.
- `POST /api/lims/storage/reset-location` with request keys `["apiKey", "requestTime"]` returned HTTP 200 and `code=1`.
- `reset-devices` was not live-probed in this session, but the current RPC wrapper already sends no `data`.

Raw findings:

- `temp_benyao/peptide/_findings/2026-05-21_1613_reset_order_status_no_data_live.md`
- `temp_benyao/peptide/_findings/2026-05-21_1615_remaining_resets_no_data_live.md`

## Proposed Public Actions

### `reset_auto`

Normal action node. This is the auto/no-manual-confirm path. It replaces the current public `reset` action; do not leave a second public `reset` action unless a later compatibility request explicitly asks for an alias.

Checkbox schema rule:

- Use plain `bool` annotations in the action signature.
- Do not use `Annotated[bool, Field(...)]` for these checkbox params in this implementation plan.
- The current AST registry schema path does not unwrap `Annotated[...]`; plain `bool` is required so generated JSON Schema marks the fields as boolean and the renderer can show checkboxes.
- Put human-facing labels/descriptions in the method docstring or action description. If field-level `Field(description=...)` metadata is required later, add registry `Annotated` support and a schema test as a separate change.

Decorator shape:

```python
@action(
    always_free=True,
    goal_default={
        "reset_scheduler": True,
        "reset_order_status": True,
        "reset_location": True,
        "reset_devices": False,
    },
    description="自动复位调度器/订单状态/库位，可选仪器复位",
)
def reset_auto(
    self,
    reset_scheduler: bool = True,
    reset_order_status: bool = True,
    reset_location: bool = True,
    reset_devices: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """自动复位调度器/订单状态/库位，可选仪器复位。

    Args:
        reset_scheduler[调度器复位]: 调用 /api/lims/scheduler/reset，默认勾选。
        reset_order_status[订单状态复位]: 调用 /api/lims/order/reset-order-status，默认勾选。
        reset_location[库位复位]: 调用 /api/lims/storage/reset-location，默认勾选。
        reset_devices[仪器复位]: 调用 /api/lims/device/reset-devices，默认不勾选。
    """
    ...
```

Implementation notes:

- Use real plain-`bool` parameters, not hidden `**kwargs` and not `Annotated`, so the action renderer can expose four checkboxes.
- Rename/replace the existing `reset` action as `reset_auto`; the implementation should not keep the old id-shaped `reset` action as another public path by default.
- Keep the three routine reset defaults checked.
- Keep `reset_devices` unchecked because it can be broader and more disruptive.
- Do not require or resolve order ids or location ids.
- Do not call `take_out`.
- Do not call `refresh_material_cache`.

### `reset_manual`

Manual-confirm node. It should show the operator a physical cleanup warning, then execute the same reset helper as `reset_auto` after the operator confirms.

Actual manual-confirm decorator pattern in this repo:

- Use `@action(node_type=NodeType.MANUAL_CONFIRM)`.
- Set `always_free=True`.
- Add `placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"}`.
- Include `timeout_seconds: int` and `assignee_user_ids: list[str]`.
- Add `goal_default` for `timeout_seconds` and `assignee_user_ids`.
- Manual-confirm actions are normally side-effect-light, but existing Peptide `start_experiment` is already a `MANUAL_CONFIRM` action that performs scheduler start after the operator gate, so a reset-after-confirm pattern is compatible with current Peptide style.

Proposed confirmation text:

```text
请确认G3、CEM、Tecan、撕膜机、封膜机、打标机、旋转堆栈上下料位、3个转台等位置的物料已清理完毕；
请开门检查冰箱、IDOT、酶标仪、离心机、LCMS内部没有遗留物料。
```

Decorator/function shape:

```python
RESET_MANUAL_CONFIRM_MESSAGE = (
    "请确认G3、CEM、Tecan、撕膜机、封膜机、打标机、旋转堆栈上下料位、3个转台等位置的物料已清理完毕；\n"
    "请开门检查冰箱、IDOT、酶标仪、离心机、LCMS内部没有遗留物料。"
)

@action(
    always_free=True,
    node_type=NodeType.MANUAL_CONFIRM,
    placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"},
    goal_default={
        "reset_scheduler": True,
        "reset_order_status": True,
        "reset_location": True,
        "reset_devices": False,
        "physical_cleanup_confirmed": False,
        "timeout_seconds": 3600,
        "assignee_user_ids": [],
    },
    feedback_interval=300,
    description=RESET_MANUAL_CONFIRM_MESSAGE,
)
def reset_manual(
    self,
    reset_scheduler: bool = True,
    reset_order_status: bool = True,
    reset_location: bool = True,
    reset_devices: bool = False,
    physical_cleanup_confirmed: bool = False,
    timeout_seconds: int = 3600,
    assignee_user_ids: Optional[List[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """人工确认物理清理后执行复位。

    Args:
        reset_scheduler[调度器复位]: 调用 /api/lims/scheduler/reset，默认勾选。
        reset_order_status[订单状态复位]: 调用 /api/lims/order/reset-order-status，默认勾选。
        reset_location[库位复位]: 调用 /api/lims/storage/reset-location，默认勾选。
        reset_devices[仪器复位]: 调用 /api/lims/device/reset-devices，默认不勾选。
        physical_cleanup_confirmed[物理清理确认]: 确认清理提示中的物料检查已经完成，默认不勾选。
    """
    ...
```

Execution rule:

- If `physical_cleanup_confirmed` is false, return a blocked result and do not call any reset API.
- If it is true, call the same internal helper as `reset_auto`.
- Return `confirmation_message` in the result payload so call logs preserve the exact operator instruction text.

Renderer caveat:

- `description` should carry the warning in generated action metadata.
- `physical_cleanup_confirmed` must remain a plain `bool` so it renders as a checkbox.
- The cleanup warning should be carried by the action `description` and the docstring param description. Do not rely on `Field(description=...)` unless registry `Annotated` support has been implemented and tested.
- If the current frontend does not show action descriptions or docstring field descriptions reliably, add a read-only string parameter such as `confirmation_message: str = RESET_MANUAL_CONFIRM_MESSAGE` with `goal_default`, or use a handle-based display only after renderer behavior is verified.

## Shared Internal Helper

Both public actions should delegate to one helper, for example:

```python
def _execute_reset_operations(
    self,
    *,
    reset_scheduler: bool,
    reset_order_status: bool,
    reset_location: bool,
    reset_devices: bool,
) -> Dict[str, Any]:
    ...
```

Call order:

1. `scheduler_reset`
2. `reset_order_status`
3. `reset_location`
4. `reset_devices`

Result shape:

```python
{
    "selected_operations": [
        {"key": "reset_scheduler", "label": "调度器复位", "selected": True},
        {"key": "reset_order_status", "label": "订单状态复位", "selected": True},
        {"key": "reset_location", "label": "库位复位", "selected": True},
        {"key": "reset_devices", "label": "仪器复位", "selected": False},
    ],
    "executed_calls": [
        {"operation": "scheduler_reset", "endpoint": "/api/lims/scheduler/reset", "result": {"code": 1}},
    ],
    "skipped_operations": [
        {"operation": "reset_devices", "reason": "checkbox_disabled"},
    ],
    "warnings": [],
}
```

Failure handling:

- Execute selected operations sequentially and record each result.
- If an operation returns non-`1` code, add a warning and continue unless the caller later requests fail-fast.
- If an RPC method raises, catch it, record an error entry, and continue to the next selected operation unless fail-fast is introduced.

## RPC Wrapper Adjustment

Adjust the two id-shaped wrappers to no-data calls:

- `BioyondV1RPC.reset_order_status()` should no longer require `order_id`.
- `BioyondV1RPC.reset_location()` should no longer require `location_id`.

Current no-data wrappers already exist:

- `scheduler_reset()`
- `reset_devices()`

Suggested RPC signatures:

```python
def scheduler_reset(self) -> int: ...
def reset_order_status(self) -> int: ...
def reset_location(self) -> int: ...
def reset_devices(self) -> int: ...
```

Compatibility option:

```python
def reset_order_status(self, order_id: Optional[str] = None) -> int:
    del order_id
    ...

def reset_location(self, location_id: Optional[str] = None) -> int:
    del location_id
    ...
```

This keeps older code from crashing while making the actual wire request no-data.

## Adjusted Runtime API Schemas

These are the schemas Peptide reset code should target at runtime after the live no-data probes. They intentionally omit `data`, even though OpenAPI models nullable `data` for these endpoints.

All four requests use:

```json
{
  "apiKey": "string",
  "requestTime": "date-time"
}
```

No `data` field should be sent by default.

All four responses use:

```json
{
  "code": 1,
  "message": "",
  "timestamp": 0
}
```

### 调度器复位

Endpoint:

```text
POST /api/lims/scheduler/reset
```

Adjusted request:

```json
{
  "apiKey": "B10B5995",
  "requestTime": "2026-05-21T08:15:16.494Z"
}
```

Live response:

```json
{
  "code": 1,
  "message": "",
  "timestamp": 1779351316072
}
```

Notes:

- OpenAPI says `data` is nullable int32.
- Live Peptide accepted omitted `data`.

### 订单状态复位

Endpoint:

```text
POST /api/lims/order/reset-order-status
```

Adjusted request:

```json
{
  "apiKey": "B10B5995",
  "requestTime": "2026-05-21T08:13:34.750Z"
}
```

Live response:

```json
{
  "code": 1,
  "message": "",
  "timestamp": 1779351214422
}
```

Notes:

- OpenAPI says `data` is nullable string.
- Live Peptide accepted omitted `data`.
- Do not model this as order-id scoped unless Bioyond confirms backend behavior.

### 库位复位

Endpoint:

```text
POST /api/lims/storage/reset-location
```

Adjusted request:

```json
{
  "apiKey": "B10B5995",
  "requestTime": "2026-05-21T08:15:18.924Z"
}
```

Live response:

```json
{
  "code": 1,
  "message": "",
  "timestamp": 1779351318565
}
```

Notes:

- OpenAPI says `data` is nullable string.
- Live Peptide accepted omitted `data`.
- Do not model this as location-id scoped unless Bioyond confirms backend behavior.

### 仪器复位

Endpoint:

```text
POST /api/lims/device/reset-devices
```

Adjusted request:

```json
{
  "apiKey": "B10B5995",
  "requestTime": "date-time"
}
```

Expected response shape:

```json
{
  "code": 1,
  "message": "",
  "timestamp": 0
}
```

Notes:

- OpenAPI says `data` is nullable string.
- Current `BioyondV1RPC.reset_devices()` already sends no `data`.
- This endpoint was not live-probed in the no-data reset session.
- Keep checkbox default unchecked.

## Tests To Add Before Implementation

1. `reset_auto` is not `NodeType.MANUAL_CONFIRM`.
2. `reset_manual` has `node_type=NodeType.MANUAL_CONFIRM`.
3. `reset_manual` metadata includes:
   - `always_free=True`
   - `placeholder_keys={"assignee_user_ids": "unilabos_manual_confirm"}`
   - `timeout_seconds=3600`
   - `assignee_user_ids=[]`
   - `physical_cleanup_confirmed=False`
4. Both reset actions expose four real boolean params:
   - `reset_scheduler`
   - `reset_order_status`
   - `reset_location`
   - `reset_devices`
5. The generated registry schema marks those reset params as JSON Schema `type: boolean`, not `object` or `string`, so the frontend can render checkboxes.
6. `reset_auto` replaces the current public `reset` action. Unless a later compatibility request adds an alias, no old id-shaped public `reset` action remains.
7. Goal defaults are:
   - first three reset checkboxes `True`
   - `reset_devices=False`
8. `reset_manual(..., physical_cleanup_confirmed=False)` does not call any RPC reset method.
9. `reset_auto()` with defaults calls:
   - `scheduler_reset()`
   - `reset_order_status()`
   - `reset_location()`
   - not `reset_devices()`
10. `reset_auto(reset_devices=True)` also calls `reset_devices()`.
11. `reset_order_status()` and `reset_location()` RPC wrappers send no `data` key.
12. No reset path calls `take_out`.
13. No reset path calls `refresh_material_cache`.

## Non-Goals

- Do not implement `take_out` in reset.
- Do not refresh `material_cache` from reset.
- Do not resolve order ids or location ids for reset.
- Do not add Project/cache/browser cleanup routes.
- Do not make `reset_devices` default-on.
- Do not execute this plan during planning.
