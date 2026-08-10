"""调度权威运行模式（SchedulerAuthorityProfile）的公开接口测试。"""

from __future__ import annotations

import pytest

from unilabos.storage.profiles import (
    SchedulerAuthorityConflict,
    SchedulerAuthorityProfile,
    select_scheduler_authority_profile,
)
from unilabos.workflow.service import WorkflowError, WorkflowService
from unilabos.workflow.store import WorkflowStore


@pytest.mark.parametrize(
    ("wire_value", "expected"),
    [
        ("local_scheduler", SchedulerAuthorityProfile.LOCAL_SCHEDULER),
        ("backend_controlled", SchedulerAuthorityProfile.BACKEND_CONTROLLED),
        ("offline_recovery", SchedulerAuthorityProfile.OFFLINE_RECOVERY),
    ],
)
def test_parse_accepts_only_canonical_profiles(wire_value, expected) -> None:
    """线协议只接受三个规范调度权威模式值。"""

    assert SchedulerAuthorityProfile.parse(wire_value) is expected
    assert SchedulerAuthorityProfile.parse(expected) is expected


def test_parse_rejects_ambiguous_auto_profile() -> None:
    """模糊的 auto 模式必须被拒绝，不能在运行期猜测权威。"""

    with pytest.raises(ValueError, match="SchedulerAuthorityProfile"):
        SchedulerAuthorityProfile.parse("auto")


def test_profile_capabilities_do_not_adopt_another_task_authority() -> None:
    """各模式能力边界不得隐式接管另一方的任务权威。"""

    local = SchedulerAuthorityProfile.LOCAL_SCHEDULER
    backend = SchedulerAuthorityProfile.BACKEND_CONTROLLED
    recovery = SchedulerAuthorityProfile.OFFLINE_RECOVERY

    assert local.can_create_local_workflow_task
    assert local.can_recover_local_workflow_task
    assert not local.can_execute_backend_command

    assert not backend.can_create_local_workflow_task
    assert not backend.can_recover_local_workflow_task
    assert backend.can_execute_backend_command

    assert not recovery.can_create_local_workflow_task
    assert recovery.can_recover_local_workflow_task
    assert not recovery.can_execute_backend_command


def test_startup_selection_is_deterministic_and_rejects_dual_authority() -> None:
    """默认选择必须确定，并拒绝 Edge 控制与本地调度并行。"""

    assert (
        select_scheduler_authority_profile("", edge_control_enabled=False)
        is SchedulerAuthorityProfile.LOCAL_SCHEDULER
    )
    assert (
        select_scheduler_authority_profile("", edge_control_enabled=True)
        is SchedulerAuthorityProfile.BACKEND_CONTROLLED
    )
    with pytest.raises(SchedulerAuthorityConflict, match="edge_control"):
        select_scheduler_authority_profile(
            "local_scheduler",
            edge_control_enabled=True,
        )


@pytest.mark.parametrize(
    "profile",
    [
        SchedulerAuthorityProfile.BACKEND_CONTROLLED,
        SchedulerAuthorityProfile.OFFLINE_RECOVERY,
    ],
)
def test_non_local_profiles_cannot_create_executable_workflow_tasks(
    tmp_path,
    profile,
) -> None:
    """非本地调度模式不得创建新的本地可执行工作流任务。"""

    store = WorkflowStore(tmp_path / "workflow.db")
    service = WorkflowService(store, authority_profile=profile)
    try:
        with pytest.raises(WorkflowError) as raised:
            service.create_workflow_task(
                workflow_uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                run_mode="normal",
                target_node_uuid=None,
                input_value={},
                description=None,
                meta_data={},
            )
        assert raised.value.code == "local_task_authority_forbidden"
    finally:
        store.close()
