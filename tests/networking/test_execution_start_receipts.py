"""真实开始回执先在 Edge 落盘，断线和终态后的重报仍保留最初身份。"""

import asyncio
import threading
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.networking.test_edge_control_protocol import FakeDataPlane, FakeHostNode, _settings, _start_job
from tests.scheduler_core.conftest import build_core_runtime
from tests.scheduler_core.test_engine_execution_started import started_events
from unilabos.app.edge_control.client import EdgeControlClient
from unilabos.app.edge_control.execution_observation import ExecutionStartReporter
from unilabos.app.edge_control.http import EdgeDataPlane, EdgeProtocolHTTPError
from unilabos.app.edge_control.local_authority import (
    LocalEdgeAuthorityStore,
    LocalEdgeControlAuthority,
    create_local_edge_control_router,
)
from unilabos.scheduling.queue import QueueEngine


@pytest.mark.parametrize("retired_before_receipt", [False, True])
def test_driver_start_receipt_survives_restart_and_result_arriving_first(tmp_path, retired_before_receipt):
    async def scenario():
        path = tmp_path / "runtime.db"
        class Host(FakeHostNode):
            def send_goal(self, item, **kwargs):
                super().send_goal(item, **kwargs)
                if retired_before_receipt:
                    first.store.save_pending_outcome(item.job_id, "success", {}, [])
                    event_id = first.store.complete_pending_outcome(item.job_id, {"job_uuid": item.job_id})
                    first.store.acknowledge_event(event_id)
                    assert first.store.get_job(item.job_id) is None

        host = Host()
        class Plane(FakeDataPlane):
            def fetch_job(self, job):
                return {**super().fetch_job(job), "station_controlled": True}

        plane = Plane()
        first = EdgeControlClient(_settings(path), data_plane=plane, host_node_provider=lambda: host)
        job_id = str(uuid4())
        _start_job(first.store, job_id)
        first._connected.set()
        await first._execute_job(job_id)
        context = host.started[0]["item"]
        received = []
        plane.commit_execution_started = lambda job, receipt: received.append(dict(receipt)) or {
            "receipt_id": receipt["receipt_id"], "created": True,
        }
        context.execution_start_reporter(
            receipt_id=str(uuid4()), started_at_ms=1800000000250,
            source_ref="device:heater-01:running-ack:17",
        )
        # 尚未有网络事件循环，报告仍须已保存；随后结果先于重报到达。
        if not retired_before_receipt:
            first.store.set_job_status(job_id, "completed")
        pending = first.store.pending_execution_starts()
        assert len(pending) == 1
        first.store.close()
        second = EdgeControlClient(_settings(path), data_plane=plane, host_node_provider=lambda: host)
        try:
            await second.flush_execution_starts()
            assert received == pending
            assert second.store.pending_execution_starts() == []
            if retired_before_receipt:
                assert second.store.get_job(job_id) is None
            else:
                assert second.store.get_job(job_id).status == "completed"
            await second.flush_execution_starts()
            assert received == pending
            assert len(host.started) == 1
        finally:
            second.store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["disconnect", "wrong_ack"])
def test_pending_receipt_is_retained_on_transport_failure_and_concurrent_flush_is_single(tmp_path, failure):
    async def scenario():
        client = EdgeControlClient(_settings(tmp_path / "retry.db"), data_plane=FakeDataPlane())
        job_id = str(uuid4())
        _start_job(client.store, job_id)
        job = client.store.get_job(job_id)
        reporter = ExecutionStartReporter(
            job_id, job.command_uuid, job.attempt, str(uuid4()),
            lambda value: client.report_execution_started(job, value),
        )
        try:
            reporter(receipt_id=str(uuid4()), started_at_ms=1800000000100, source_ref="device:ack:9")
            pending = client.store.pending_execution_starts()
            calls = []

            def fail(_job, value):
                calls.append(value)
                if failure == "disconnect":
                    raise ConnectionError("测试网络中断")
                return {"receipt_id": str(uuid4())}

            client.data_plane.commit_execution_started = fail
            await client.flush_execution_starts()
            assert client.store.pending_execution_starts() == pending
            with pytest.raises(ValueError, match="不能清除"):
                client.store.reset_transient_state()
            entered, release = threading.Event(), threading.Event()

            def commit(_job, value):
                calls.append(value)
                entered.set()
                assert release.wait(2)
                return {"receipt_id": value["receipt_id"], "created": True}

            client.data_plane.commit_execution_started = commit
            first = asyncio.create_task(client.flush_execution_starts())
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.gather(*(client.flush_execution_starts() for _ in range(10)))
            release.set()
            await first
            assert calls == pending * 2
            assert client.store.pending_execution_starts() == []
        finally:
            client.store.close()

    asyncio.run(scenario())


def test_actual_http_chain_authenticates_start_and_preserves_receipt_after_terminal(tmp_path):
    async def scenario():
        authority = LocalEdgeControlAuthority(LocalEdgeAuthorityStore(tmp_path / "authority.db"), api_key="test")
        runtime = build_core_runtime(tmp_path, execution_backend=authority, scheduling_engine=QueueEngine())
        application = FastAPI()
        application.include_router(create_local_edge_control_router(authority))
        http = TestClient(application)
        plane = EdgeDataPlane("http://testserver", "http://testserver", "test")
        plane._session = http
        host = FakeHostNode()
        client = EdgeControlClient(
            _settings(tmp_path / "edge.db"), data_plane=plane, host_node_provider=lambda: host,
        )
        try:
            task = runtime.submit(task_name="http-start", devices=["reactor-a"])
            job_id = task["jobs"][0]["uuid"]
            command = next(command for command in authority.store.pending_commands() if command["type"] == "job.start")
            client.store.save_job_start(command["payload"], command["message_uuid"])
            client._connected.set()
            await client._execute_job(job_id)
            assert len(host.started) == 1
            context = host.started[0]["item"]
            # Goal 受理及 S 都不会发布实际开始。
            client.publish_job_started(context)
            assert started_events(runtime) == []
            assert context.start_guard() == "START"
            assert started_events(runtime) == []
            context.execution_start_reporter(
                receipt_id=str(uuid4()), started_at_ms=1800000000100,
                source_ref="device:reactor-a:running-ack:77",
            )
            pending = client.store.pending_execution_starts()[0]
            job = client.store.get_job(job_id)
            with pytest.raises(EdgeProtocolHTTPError) as denied:
                plane.commit_execution_started(replace(job, job_access_token="wrong"), pending)
            assert denied.value.status_code == 401
            with pytest.raises(EdgeProtocolHTTPError) as conflict:
                plane.commit_execution_started(replace(job, attempt=job.attempt + 1), pending)
            assert conflict.value.status_code == 409
            runtime.clock.advance(1)
            runtime.scheduler.on_job_finished(job_id, True, {})
            settled = runtime.workflow_store.get_job(job_id)
            await client.flush_execution_starts()
            runtime.bridge.reschedule()
            assert client.store.pending_execution_starts() == []
            assert len(started_events(runtime)) == 1
            assert plane.commit_execution_started(job, pending)["created"] is False
            assert runtime.workflow_store.get_job(job_id) == settled
            assert runtime.bridge.active_or_uncertain_job_ids() == set()
            with pytest.raises(EdgeProtocolHTTPError) as changed:
                plane.commit_execution_started(job, {**pending, "source_ref": "another-command"})
            assert changed.value.status_code == 409
            assert len(host.started) == 1
        finally:
            client.store.close()
            http.close()
            runtime.close()
            authority.stop()

    asyncio.run(scenario())
