"""Registry Authority：条目级版本、workflow 引用冲突、还原与上报 API。"""

from __future__ import annotations

import gzip
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from unilabos.server.api.runtime.registry import install_registry_api
from unilabos.server.services.runtime.registry import (
    RegistryAuthorityError,
    RegistryService,
    set_registry_service,
    template_uuid,
)


def _device(name: str, *, module: str = "pkg.mod:Cls", lock=None, goal=None) -> dict[str, Any]:
    action: dict[str, Any] = {
        "type": "UniLabJsonCommand",
        "goal": goal or {},
        "handles": {"input": [], "output": []},
    }
    if lock is not None:
        action["materials_need_lock"] = lock
    return {
        "id": name,
        "registry_type": "device",
        "class": {
            "module": module,
            "type": "python",
            "action_value_mappings": {"run": action},
        },
        "handles": [],
    }


def _ref_row(
    name: str,
    action: str,
    *,
    workflow: str = "wf-1",
    workflow_name: str = "工作流A",
    node: str = "node-1",
    node_name: str = "节点1",
) -> dict[str, str]:
    """一行 workflow 节点对模板 action 的引用（store 明细行形状）。"""

    return {
        "template_uuid": template_uuid(name),
        "action": action,
        "node_uuid": node,
        "node_name": node_name,
        "workflow_uuid": workflow,
        "workflow_name": workflow_name,
    }


@pytest.fixture()
def refs():
    return []


@pytest.fixture()
def service(tmp_path, refs):
    # registry 三表落 runtime.db（同一 RUNTIME_DATABASE 规格，无第五个库）
    instance = RegistryService(
        tmp_path / "runtime.db", reference_rows_resolver=lambda: refs
    )
    try:
        yield instance
    finally:
        instance.close()


class TestEntryReport:
    def test_first_report_adds_all_entries_at_v1(self, service) -> None:
        report = service.report([_device("pump"), _device("stirrer")], edge_uuid="e1")

        counts = report["summary"]["counts"]
        assert counts["added"] == 2 and counts["updated"] == 0
        assert report["report_id"] == 1
        assert {t["name"] for t in report["templates"]} == {"pump", "stirrer"}
        entries = {e["name"]: e for e in service.list_entries()}
        assert entries["pump"]["active_version"] == 1
        assert entries["pump"]["status"] == ["active"]

    def test_identical_report_keeps_versions(self, service) -> None:
        service.report([_device("pump")])
        again = service.report([_device("pump")])

        assert again["summary"]["counts"]["unchanged"] == 1
        assert service.list_entries()[0]["active_version"] == 1
        assert service.entry_versions("pump") == [
            {
                "version": 1,
                "created_at_ms": service.entry_versions("pump")[0]["created_at_ms"],
                "source": "edge-report",
                "edge_uuid": "",
                "restored_from": None,
                "content_sha256": service.entry_versions("pump")[0]["content_sha256"],
            }
        ]

    def test_any_field_change_bumps_entry_version(self, service) -> None:
        service.report([_device("pump")])
        changed = _device("pump")
        changed["display_name"] = "只是改名"

        report = service.report([changed])

        assert report["summary"]["updated"] == ["pump"]
        state = service.list_entries()[0]
        assert state["active_version"] == 2 and state["pending_version"] is None

    def test_unreferenced_action_change_updates_automatically(self, service, refs) -> None:
        service.report([_device("pump", goal={"speed": 1})])
        refs.clear()  # 无 workflow 引用

        report = service.report([_device("pump", goal={"speed": 2})])

        assert report["summary"]["updated"] == ["pump"]
        assert service.list_entries()[0]["active_version"] == 2


class TestReferenceConflicts:
    def test_referenced_action_change_goes_pending(self, service, refs) -> None:
        service.report([_device("pump", goal={"speed": 1})])
        refs.append(_ref_row("pump", "run"))

        report = service.report([_device("pump", goal={"speed": 2})])

        pending = report["summary"]["pending"]
        assert pending == [
            {"name": "pump", "conflicts": [{"action": "run", "reason": "action-changed"}]}
        ]
        state = service.list_entries()[0]
        assert state["active_version"] == 1 and state["pending_version"] == 2
        assert state["status"] == ["active", "pending"]

    def test_referenced_action_removed_goes_pending(self, service, refs) -> None:
        service.report([_device("pump")])
        refs.append(_ref_row("pump", "run"))
        without_action = _device("pump")
        without_action["class"]["action_value_mappings"] = {}

        report = service.report([without_action])

        assert report["summary"]["pending"][0]["conflicts"] == [
            {"action": "run", "reason": "action-removed"}
        ]

    def test_broken_reference_does_not_block_update(self, service, refs) -> None:
        service.report([_device("pump")])
        refs.append(_ref_row("pump", "ghost-action"))  # 基线版本也不包含该动作。

        report = service.report([_device("pump", module="pkg.mod:V2")])

        assert report["summary"]["updated"] == ["pump"]

    def test_unreferenced_field_change_with_reference_present(self, service, refs) -> None:
        """被引用的 action 没变时，其他字段变化仍自动生效。"""

        service.report([_device("pump")])
        refs.append(_ref_row("pump", "run"))
        changed = _device("pump")
        changed["description"] = "新描述"

        report = service.report([changed])

        assert report["summary"]["updated"] == ["pump"]
        assert service.list_entries()[0]["active_version"] == 2

    def test_apply_pending_activates_new_version(self, service, refs) -> None:
        service.report([_device("pump", goal={"speed": 1}, lock=["v1"])])
        refs.append(_ref_row("pump", "run"))
        service.report([_device("pump", goal={"speed": 2}, lock=["v2"])])
        assert service.material_lock_parameters("pump", "run") == ["v1"]

        state = service.apply_pending("pump")

        assert state["active_version"] == 2 and state["pending_version"] is None
        assert service.material_lock_parameters("pump", "run") == ["v2"]

    def test_dismiss_pending_keeps_active(self, service, refs) -> None:
        service.report([_device("pump", goal={"speed": 1})])
        refs.append(_ref_row("pump", "run"))
        service.report([_device("pump", goal={"speed": 2})])

        state = service.dismiss_pending("pump")

        assert state["active_version"] == 1 and state["pending_version"] is None
        assert state["pending_conflicts"] == []

    def test_new_report_overrides_stale_pending(self, service, refs) -> None:
        service.report([_device("pump", goal={"speed": 1})])
        refs.append(_ref_row("pump", "run"))
        service.report([_device("pump", goal={"speed": 2})])
        service.report([_device("pump", goal={"speed": 3})])

        state = service.list_entries()[0]
        assert state["active_version"] == 1 and state["pending_version"] == 3

    def test_apply_without_pending_raises(self, service) -> None:
        service.report([_device("pump")])
        with pytest.raises(RegistryAuthorityError, match="no pending"):
            service.apply_pending("pump")

    def test_pending_impacts_list_affected_nodes(self, service, refs) -> None:
        """挂起条目按冲突 action 反查受影响画布节点；无关节点不出现。"""

        service.report([_device("pump", goal={"speed": 1}), _device("stirrer")])
        refs.append(_ref_row("pump", "run", node="n-run", node_name="进料"))
        refs.append(_ref_row("pump", "idle", node="n-idle"))  # 引用了未冲突 action
        service.report([_device("pump", goal={"speed": 2}), _device("stirrer")])

        impacts = service.pending_impacts()

        assert len(impacts) == 1
        impact = impacts[0]
        assert impact["name"] == "pump"
        assert impact["template_uuid"] == template_uuid("pump")
        assert impact["active_version"] == 1 and impact["pending_version"] == 2
        assert impact["conflicts"] == [{"action": "run", "reason": "action-changed"}]
        assert impact["affected_nodes"] == [
            {
                "workflow_uuid": "wf-1",
                "workflow_name": "工作流A",
                "node_uuid": "n-run",
                "node_name": "进料",
                "action": "run",
            }
        ]

    def test_pending_impacts_empty_without_pending(self, service) -> None:
        service.report([_device("pump")])
        assert service.pending_impacts() == []


class TestRemoveRestore:
    def test_missing_entry_is_soft_removed_and_revivable(self, service) -> None:
        service.report([_device("pump"), _device("stirrer")])

        removed_report = service.report([_device("pump")])
        assert removed_report["summary"]["removed"] == ["stirrer"]
        stirrer = {e["name"]: e for e in service.list_entries()}["stirrer"]
        assert stirrer["status"] == ["removed"]
        assert service.material_lock_parameters("stirrer", "run") == []

        revived_report = service.report([_device("pump"), _device("stirrer")])
        assert revived_report["summary"]["revived"] == ["stirrer"]
        stirrer = {e["name"]: e for e in service.list_entries()}["stirrer"]
        assert stirrer["status"] == ["active"] and stirrer["active_version"] == 1

    def test_restore_creates_new_active_version(self, service) -> None:
        service.report([_device("pump", module="pkg.mod:V1")])
        service.report([_device("pump", module="pkg.mod:V2")])

        state = service.restore("pump", 1)

        assert state["active_version"] == 3
        versions = service.entry_versions("pump")
        assert versions[0]["source"] == "restore" and versions[0]["restored_from"] == 1
        detail = service.entry_detail("pump")
        assert detail["active_payload"]["class"]["module"] == "pkg.mod:V1"

    def test_restore_identical_content_is_idempotent(self, service) -> None:
        service.report([_device("pump")])
        state = service.restore("pump", 1)
        assert state["active_version"] == 1
        assert len(service.entry_versions("pump")) == 1

    def test_restore_unknown_version_raises(self, service) -> None:
        service.report([_device("pump")])
        with pytest.raises(RegistryAuthorityError, match="not found"):
            service.restore("pump", 99)


class TestUnusable:
    def test_unusable_entry_records_reason_without_version(self, service) -> None:
        report = service.report(
            [
                {"id": "no-class", "registry_type": "device"},
                {"id": "bad-type", "registry_type": "widget", "class": {}},
                {"registry_type": "device", "class": {"module": "m"}},
            ]
        )

        unusable = {item["id"]: item["reason"] for item in report["summary"]["unusable"]}
        assert unusable == {
            "no-class": "missing-class",
            "bad-type": "invalid-registry-type",
            "": "missing-id",
        }
        states = {e["name"]: e for e in service.list_entries()}
        assert states["no-class"]["active_version"] is None
        assert states["no-class"]["status"] == ["unusable"]
        with pytest.raises(RegistryAuthorityError):
            service.entry_versions("no-class")

    def test_unusable_update_keeps_active_version_serving(self, service) -> None:
        service.report([_device("pump", lock=["vessel"])])

        broken = {"id": "pump", "registry_type": "device"}
        service.report([broken])

        state = service.list_entries()[0]
        assert state["active_version"] == 1
        assert state["unusable_reason"] == "missing-class"
        assert service.material_lock_parameters("pump", "run") == ["vessel"]

        service.report([_device("pump", lock=["vessel"])])
        assert service.list_entries()[0]["unusable_reason"] == ""


class TestLockMirror:
    def test_lock_parameters_from_active_entry(self, service) -> None:
        service.report([_device("pump", lock=["from_vessel", "to_vessel"])])
        assert service.material_lock_parameters("pump", "run") == [
            "from_vessel",
            "to_vessel",
        ]

    def test_lock_parameters_auto_prefix_fallback(self, service) -> None:
        entry = _device("pump", lock=["vessel"])
        mappings = entry["class"]["action_value_mappings"]
        mappings["auto-transfer"] = mappings.pop("run")
        service.report([entry])
        assert service.material_lock_parameters("pump", "transfer") == ["vessel"]

    def test_lock_parameters_empty_before_first_report(self, service) -> None:
        assert service.material_lock_parameters("pump", "run") == []


class TestWorkflowReferenceRows:
    def test_store_rows_join_workflow_and_skip_deleted(self) -> None:
        """store 明细行含 workflow/node 名，软删的节点/工作流不计入。"""

        from unilabos.server.services.runtime.workflow.store import WorkflowStore

        store = WorkflowStore(":memory:")
        pump_uuid = template_uuid("pump")
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO workflow (uuid, create_time, update_time, name, meta_data, tags)"
                " VALUES ('wf-1', 't0', 't0', '合成A', '{}', '[]')"
            )
            conn.execute(
                "INSERT INTO workflow_node_template (uuid, create_time, update_time,"
                " authority_id, resource_template_uuid, name, display_name, type, node_type,"
                " meta_data, goal, goal_default, feedback, result)"
                " VALUES ('tpl-1', 't0', 't0', 'auth', ?, 'run', '运行', 'device', 'action',"
                " '{}', '{}', '{}', '{}', '{}')",
                (pump_uuid,),
            )
            conn.execute(
                "INSERT INTO workflow_node (uuid, create_time, update_time, workflow_uuid,"
                " workflow_node_template_uuid, name, status, type, disabled, minimized,"
                " meta_data, pose, param, execution_policy)"
                " VALUES ('n-1', 't0', 't0', 'wf-1', 'tpl-1', '进料', 'idle', 'device', 0, 0,"
                " '{}', '{}', '{}', '{}')"
            )
            conn.execute(
                "INSERT INTO workflow_node (uuid, create_time, update_time, deleted_at,"
                " workflow_uuid, workflow_node_template_uuid, name, status, type,"
                " disabled, minimized, meta_data, pose, param, execution_policy)"
                " VALUES ('n-gone', 't0', 't0', 't1', 'wf-1', 'tpl-1', '已删', 'idle',"
                " 'device', 0, 0, '{}', '{}', '{}', '{}')"
            )

        rows = store.list_template_action_references()

        assert rows == [
            {
                "template_uuid": pump_uuid,
                "action": "run",
                "node_uuid": "n-1",
                "node_name": "进料",
                "workflow_uuid": "wf-1",
                "workflow_name": "合成A",
            }
        ]


class TestRegistryApi:
    @pytest.fixture()
    def client(self, service):
        app = FastAPI()
        install_registry_api(app)
        set_registry_service(service)
        try:
            yield TestClient(app)
        finally:
            set_registry_service(None)

    def test_gzip_report_roundtrip(self, client) -> None:
        body = gzip.compress(
            json.dumps({"resources": [_device("pump")]}).encode("utf-8")
        )
        response = client.post(
            "/api/v1/resource-templates",
            content=body,
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
                "Authorization": "Bearer edge-report",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["code"] == 0
        data = payload["data"]
        assert data["templates"] == [{"name": "pump", "uuid": template_uuid("pump")}]
        assert data["report_id"] == 1
        assert data["summary"]["counts"]["added"] == 1

    def test_entry_endpoints(self, client, refs) -> None:
        client.post("/api/v1/resource-templates", json={"resources": [_device("a", goal={"v": 1})]})
        refs.append(_ref_row("a", "run"))
        client.post("/api/v1/resource-templates", json={"resources": [_device("a", goal={"v": 2})]})

        pending = client.get("/api/v1/registry/entries", params={"status": "pending"}).json()["data"]
        assert [e["name"] for e in pending["entries"]] == ["a"]

        detail = client.get("/api/v1/registry/entries/a").json()["data"]
        assert detail["active_payload"]["class"]["action_value_mappings"]["run"]["goal"] == {"v": 1}
        assert detail["pending_payload"]["class"]["action_value_mappings"]["run"]["goal"] == {"v": 2}

        impacts = client.get("/api/v1/registry/pending-impacts").json()["data"]["impacts"]
        assert impacts[0]["name"] == "a"
        assert impacts[0]["affected_nodes"][0]["node_uuid"] == "node-1"

        applied = client.post("/api/v1/registry/entries/a/apply").json()["data"]
        assert applied["active_version"] == 2

        restored = client.post("/api/v1/registry/entries/a/restore/1").json()["data"]
        assert restored["active_version"] == 3

        versions = client.get("/api/v1/registry/entries/a/versions").json()["data"]
        assert [v["version"] for v in versions["versions"]] == [3, 2, 1]

        reports = client.get("/api/v1/registry/reports").json()["data"]
        assert reports["total"] == 2

    def test_apply_without_pending_returns_409(self, client) -> None:
        client.post("/api/v1/resource-templates", json={"resources": [_device("a")]})
        response = client.post("/api/v1/registry/entries/a/apply")
        assert response.status_code == 409

    def test_invalid_body_rejected(self, client) -> None:
        response = client.post(
            "/api/v1/resource-templates",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_missing_service_returns_503(self, service) -> None:
        app = FastAPI()
        install_registry_api(app)
        set_registry_service(None)
        response = TestClient(app).get("/api/v1/registry/entries")
        assert response.status_code == 503
