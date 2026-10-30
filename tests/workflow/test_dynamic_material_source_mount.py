"""上游 Material 作为运行时 material_source mount 的集成合同。"""

import json
from copy import deepcopy
from uuid import uuid4

import pytest

from unilabos.app.scheduler.material_source_resolution import (
    MaterialSourceResolutionCoordinator,
)
from unilabos.workflow.authoring_engine import WorkflowAuthoringEngine
from unilabos.workflow.authoring_kernel import AuthoringCatalogSnapshot
from unilabos.workflow.execution_plan import ExecutionPlanBuilder, ExecutionPlanBuildError

from .test_authoring_engine import WORKFLOW_UUID, _applied_graph, _handle
from .test_f05_material_source_authoring import (
    MATERIAL_SOURCE_NODE_UUID,
    MATERIAL_SOURCE_TEMPLATE_UUID,
    MOUNT_MATERIAL_UUID,
    PLATE_TEMPLATE_UUID,
)
from .test_f05_material_source_authoring_roundtrip import _goal_material_engine
from .test_material_io_contract import (
    LOAD_UUID,
    SITE_UUID,
    _material_io_catalog,
)
from .test_material_io_contract import (
    MATERIAL_SOURCE as IO_MATERIAL_SOURCE,
)
from .test_runtime_material_replenishment import BACKUP, REFILL
from .test_structured_condition_authoring import CONDITION_NODE_UUID, _condition_template

MOUNT_HANDLE_UUID = "40000000-0000-4000-8000-000000000019"
SUFFICIENT_HANDLE_UUID = "40000000-0000-4000-8000-000000000020"
READY_TARGET_UUID = "40000000-0000-4000-8000-000000000021"
READY_SOURCE_UUID = "40000000-0000-4000-8000-000000000022"
DIRECT_ACTION_UUID = "20000000-0000-4000-8000-000000000011"
CHILD_SOURCE_UUID = "20000000-0000-4000-8000-000000000014"
ACTUAL_MOUNT_UUID = "50000000-0000-4000-8000-000000000099"
SELECTED_MATERIAL_UUID = "50000000-0000-4000-8000-000000000098"


def _engine() -> WorkflowAuthoringEngine:
    base = _goal_material_engine()._catalog
    material_io = _material_io_catalog()
    templates = [action.detached_template() for action in base.actions]
    templates.extend(action.detached_template() for action in material_io.actions)
    templates.append(_condition_template())
    handles = [
        handle
        for catalog in (base, material_io)
        for action in catalog.actions
        for handle in action.detached_handles()
    ]
    handles.extend(
        [
            _handle(
                MOUNT_HANDLE_UUID,
                node_template_uuid=MATERIAL_SOURCE_TEMPLATE_UUID,
                key="mount",
                io_type="target",
                value_type="ResourceSlot",
                required=False,
                data_source="goal",
            ),
            _handle(
                SUFFICIENT_HANDLE_UUID,
                node_template_uuid=MATERIAL_SOURCE_TEMPLATE_UUID,
                key="sufficient",
                io_type="source",
                value_type="boolean",
            ),
            _handle(
                READY_TARGET_UUID,
                node_template_uuid=MATERIAL_SOURCE_TEMPLATE_UUID,
                key="ready",
                io_type="target",
                value_type="any",
                data_source="dependency",
            ),
            _handle(
                READY_SOURCE_UUID,
                node_template_uuid=MATERIAL_SOURCE_TEMPLATE_UUID,
                key="ready",
                io_type="source",
                value_type="any",
                data_source="dependency",
            ),
        ]
    )
    return WorkflowAuthoringEngine(
        catalog=AuthoringCatalogSnapshot.from_entities(
            templates,
            handles,
            resource_template_symbols={
                "lab.resources:plate_96": PLATE_TEMPLATE_UUID,
            },
        )
    )


def _source(*, dynamic_mount: bool = True) -> str:
    backup_mount = "loaded.material" if dynamic_mount else (
        f'resource_ref("{MOUNT_MATERIAL_UUID}")'
    )
    return f'''from lab.devices import Reactor
from lab.resources import plate_96
from unilabos.workflow.authoring import (
    MaterialCustodyPolicy, MaterialFlowRole, device, material_io,
    material_source, resource_ref, workflow, workflow_output,
)

reactor: Reactor = device()

@workflow(workflow_uuid="{WORKFLOW_UUID}", displayname="Dynamic mount")
def dynamic_mount_workflow():
    # unilab:node_uuid={LOAD_UUID}
    loaded = material_io(
        mode="load", resource_template=plate_96,
        mount=resource_ref("{MOUNT_MATERIAL_UUID}"), site="{SITE_UUID}",
        material=None, disposition=None, scope=None, timeout_seconds=45,
    )
    # unilab:node_uuid={MATERIAL_SOURCE_NODE_UUID}
    plate = material_source(
        resource_template=plate_96, mode="existing",
        mount=resource_ref("{MOUNT_MATERIAL_UUID}"), material_uuid=None,
        site=None, slot_range=None, flow_role=MaterialFlowRole.PRIMARY_SAMPLE,
        custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE,
        supports_replenishment=True,
    )
    # unilab:node_uuid={CONDITION_NODE_UUID}
    if plate.sufficient:
        # unilab:node_uuid={DIRECT_ACTION_UUID}
        direct = reactor.process_plate(plate=plate)
    else:
        # unilab:node_uuid={BACKUP}
        backup = material_source(
            resource_template=plate_96, mode="existing",
            mount={backup_mount}, material_uuid=None, site=None, slot_range=None,
            flow_role=MaterialFlowRole.PRIMARY_SAMPLE,
            custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE,
        )
        # unilab:node_uuid={REFILL}
        refilled = reactor.process_plate(plate=backup)
    return workflow_output()
'''


def _compiled(*, dynamic_mount: bool = True, engine=None):
    return (engine or _engine()).compile(
        workflow_uuid=WORKFLOW_UUID,
        workflow_revision=7,
        python_source=_source(dynamic_mount=dynamic_mount),
        source_uri="memory://dynamic_mount.py",
        applied_graph=_applied_graph(),
    )


def test_dynamic_mount_authoring_graph_source_and_plan_round_trip() -> None:
    engine = _engine()
    compiled = _compiled(engine=engine)
    assert compiled.valid and compiled.graph is not None, compiled.diagnostics
    backup = next(node for node in compiled.graph["nodes"] if node["uuid"] == BACKUP)
    assert backup["param"]["mount"] is None
    mount_edge = next(
        edge
        for edge in compiled.graph["edges"]
        if edge["target_node_uuid"] == BACKUP
        and edge.get("target_handle_uuid") == MOUNT_HANDLE_UUID
    )
    assert mount_edge["source_node_uuid"] == LOAD_UUID
    assert mount_edge["source_handle_uuid"] == IO_MATERIAL_SOURCE
    repeated = engine.compile(
        workflow_uuid=WORKFLOW_UUID,
        workflow_revision=7,
        python_source=compiled.normalized_python_source,
        source_uri="memory://dynamic_mount.py",
        applied_graph=compiled.graph,
    )
    assert repeated.valid and repeated.graph == compiled.graph, repeated.diagnostics

    plan, _ = ExecutionPlanBuilder().build(
        compiled.graph, run_mode="normal", target_node_uuid=None
    )
    planned = next(node for node in plan["nodes"] if node["uuid"] == BACKUP)
    assert planned["runtime_material_source"] is True
    assert planned["runtime_mount_binding"] == {
        "source_node_uuid": LOAD_UUID,
        "source_data_key": "material",
    }


def test_dynamic_mount_is_rejected_from_task_preadmission() -> None:
    compiled = _compiled()
    graph = deepcopy(compiled.graph)
    graph["nodes"] = [
        node
        for node in graph["nodes"]
        if node["uuid"] in {LOAD_UUID, BACKUP}
    ]
    graph["edges"] = [
        edge
        for edge in graph["edges"]
        if edge["source_node_uuid"] in {LOAD_UUID, BACKUP}
        and edge["target_node_uuid"] in {LOAD_UUID, BACKUP}
    ]
    next(node for node in graph["nodes"] if node["uuid"] == BACKUP)[
        "parent_uuid"
    ] = None
    with pytest.raises(
        ExecutionPlanBuildError,
        match="动态 mount",
    ):
        ExecutionPlanBuilder().build(
            graph, run_mode="normal", target_node_uuid=None
        )


def test_runtime_coordinator_uses_actual_mount_in_single_admission() -> None:
    compiled = _compiled()
    plan, jobs = ExecutionPlanBuilder().build(
        compiled.graph, run_mode="normal", target_node_uuid=None
    )
    for job in jobs:
        if job["workflow_node_uuid"] == LOAD_UUID:
            job["return_info"] = {"material": {"uuid": ACTUAL_MOUNT_UUID}}

    class Inventory:
        calls = []

        def admit_task_materials(self, task_uuid, requests, quantities, **options):
            self.calls.append((task_uuid, requests, quantities))
            assert requests[0].requirement.mount_uuid == ACTUAL_MOUNT_UUID
            return {
                "allocations": {BACKUP: [SELECTED_MATERIAL_UUID]},
                "allocation_sites": {BACKUP: {}},
            }

    class Projection:
        binding = None

        def project_material_source_admission(self, task_uuid, bindings, **options):
            self.binding = bindings

    inventory = Inventory()
    projection = Projection()
    result = MaterialSourceResolutionCoordinator(
        inventory=inventory,
        projection=projection,
    ).reconcile(
        {"uuid": str(uuid4()), "execution_plan": plan},
        jobs,
        runtime_node_uuid=BACKUP,
    )
    assert result.status == "admitted"
    assert len(inventory.calls) == 1
    assert projection.binding[BACKUP]["uuid"] == SELECTED_MATERIAL_UUID


def test_runtime_coordinator_reads_device_action_return_value_envelope() -> None:
    compiled = _compiled()
    plan, jobs = ExecutionPlanBuilder().build(
        compiled.graph, run_mode="normal", target_node_uuid=None
    )
    for job in jobs:
        if job["workflow_node_uuid"] == LOAD_UUID:
            job["return_info"] = {
                "error": "",
                "result": {
                    "return_info": json.dumps(
                        {
                            "return_value": {
                                "material": {"uuid": ACTUAL_MOUNT_UUID},
                                "present": True,
                            }
                        }
                    ),
                    "success": True,
                },
                "return_value": {
                    "material": {"uuid": ACTUAL_MOUNT_UUID},
                    "present": True,
                },
                "suc": True,
                "suc_type": "normal",
            }

    class Inventory:
        calls = []

        def admit_task_materials(self, task_uuid, requests, quantities, **options):
            self.calls.append((task_uuid, requests, quantities))
            assert requests[0].requirement.mount_uuid == ACTUAL_MOUNT_UUID
            return {
                "allocations": {BACKUP: [SELECTED_MATERIAL_UUID]},
                "allocation_sites": {BACKUP: {}},
            }

    class Projection:
        binding = None

        def project_material_source_admission(self, task_uuid, bindings, **options):
            self.binding = bindings

    result = MaterialSourceResolutionCoordinator(
        inventory=Inventory(),
        projection=Projection(),
    ).reconcile(
        {"uuid": str(uuid4()), "execution_plan": plan},
        jobs,
        runtime_node_uuid=BACKUP,
    )
    assert result.status == "admitted"


def test_static_mount_behavior_does_not_gain_runtime_binding() -> None:
    compiled = _compiled(dynamic_mount=False)
    assert compiled.valid, compiled.diagnostics
    plan, _ = ExecutionPlanBuilder().build(
        compiled.graph, run_mode="normal", target_node_uuid=None
    )
    backup = next(node for node in plan["nodes"] if node["uuid"] == BACKUP)
    assert backup["param"]["mount"] == {"uuid": MOUNT_MATERIAL_UUID}
    assert "runtime_mount_binding" not in backup


def _nested_lookup_source() -> str:
    return f'''from lab.devices import Reactor
from lab.resources import plate_96
from unilabos.workflow.authoring import (
    MaterialCustodyPolicy, MaterialFlowRole, device, material_source,
    resource_ref, workflow, workflow_output,
)

reactor: Reactor = device()

@workflow(workflow_uuid="{WORKFLOW_UUID}", displayname="Nested lookup")
def nested_lookup_workflow():
    # unilab:node_uuid={MATERIAL_SOURCE_NODE_UUID}
    box = material_source(
        resource_template=plate_96, mode="existing",
        mount=resource_ref("{MOUNT_MATERIAL_UUID}"), material_uuid=None,
        site=None, slot_range=None, flow_role=MaterialFlowRole.PRIMARY_SAMPLE,
        custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE,
        supports_replenishment=True,
    )
    # unilab:node_uuid={CONDITION_NODE_UUID}
    if box.sufficient:
        # unilab:node_uuid={CHILD_SOURCE_UUID}
        tip = material_source(
            resource_template=plate_96, mode="existing",
            mount=box.material, material_uuid=None, site=None, slot_range=None,
            flow_role=MaterialFlowRole.PRIMARY_SAMPLE,
            custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE,
        )
        # unilab:node_uuid={DIRECT_ACTION_UUID}
        used = reactor.process_plate(plate=tip)
    else:
        # unilab:node_uuid={BACKUP}
        fallback = material_source(
            resource_template=plate_96, mode="existing",
            mount=resource_ref("{MOUNT_MATERIAL_UUID}"), material_uuid=None,
            site=None, slot_range=None, flow_role=MaterialFlowRole.PRIMARY_SAMPLE,
            custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE,
        )
        # unilab:node_uuid={REFILL}
        missing = reactor.process_plate(plate=fallback)
    return workflow_output()
'''


def test_parent_source_mounts_child_source_via_runtime_binding() -> None:
    compiled = _engine().compile(
        workflow_uuid=WORKFLOW_UUID,
        workflow_revision=7,
        python_source=_nested_lookup_source(),
        source_uri="memory://nested_lookup.py",
        applied_graph=_applied_graph(),
    )
    assert compiled.valid and compiled.graph is not None, compiled.diagnostics
    plan, jobs = ExecutionPlanBuilder().build(
        compiled.graph, run_mode="normal", target_node_uuid=None
    )
    parent = next(node for node in plan["nodes"] if node["uuid"] == MATERIAL_SOURCE_NODE_UUID)
    child = next(node for node in plan["nodes"] if node["uuid"] == CHILD_SOURCE_UUID)
    assert parent.get("material_binding_targets", []) == []
    assert child["runtime_material_source"] is True
    assert child["runtime_mount_binding"] == {
        "source_node_uuid": MATERIAL_SOURCE_NODE_UUID,
        "source_data_key": "material",
    }

    for job in jobs:
        if job["workflow_node_uuid"] == MATERIAL_SOURCE_NODE_UUID:
            job["return_info"] = {
                "material": {"uuid": ACTUAL_MOUNT_UUID},
                "sufficient": True,
            }

    class Inventory:
        calls = []

        def admit_task_materials(self, task_uuid, requests, quantities, **options):
            self.calls.append((task_uuid, requests, quantities))
            assert requests[0].requirement.mount_uuid == ACTUAL_MOUNT_UUID
            return {
                "allocations": {CHILD_SOURCE_UUID: [SELECTED_MATERIAL_UUID]},
                "allocation_sites": {CHILD_SOURCE_UUID: {}},
            }

    class Projection:
        binding = None

        def project_material_source_admission(self, task_uuid, bindings, **options):
            self.binding = bindings

    inventory = Inventory()
    projection = Projection()
    result = MaterialSourceResolutionCoordinator(
        inventory=inventory,
        projection=projection,
    ).reconcile(
        {"uuid": str(uuid4()), "execution_plan": plan},
        jobs,
        runtime_node_uuid=CHILD_SOURCE_UUID,
    )
    assert result.status == "admitted"
    assert projection.binding[CHILD_SOURCE_UUID]["uuid"] == SELECTED_MATERIAL_UUID


def test_parent_box_source_does_not_claim_child_tip_consumers() -> None:
    """范围选盒后再选枪头时，父级来源不能穿过子级来源争写同一动作参数。"""

    active = {
        "box_source": {},
        "tip_source": {},
        "consume": {},
    }
    kinds = {
        "box_source": "material_source",
        "tip_source": "material_source",
        "consume": "device_action",
    }
    outgoing = {
        "box_source": [("tip_source", "mount")],
        "tip_source": [("consume", "resource"), ("consume", "target_box")],
    }
    order = {"box_source": 0, "tip_source": 1, "consume": 2}
    box_consumers = ExecutionPlanBuilder._device_consumer_bindings(
        "box_source",
        active=active,
        kinds=kinds,
        outgoing=outgoing,
        order=order,
    )
    tip_consumers = ExecutionPlanBuilder._device_consumer_bindings(
        "tip_source",
        active=active,
        kinds=kinds,
        outgoing=outgoing,
        order=order,
    )
    assert box_consumers == []
    assert tip_consumers == [("consume", "resource"), ("consume", "target_box")]
