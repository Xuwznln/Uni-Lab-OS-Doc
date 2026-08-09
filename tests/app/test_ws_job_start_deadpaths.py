"""F002 回归测试：_handle_job_start 死路径必回终态 + task_dag 起跑逃逸异常兜底。

os-reviewer/python-reviewer 发现的 HIGH 悬挂缺陷：当 job_start 起跑因
`HostNode` 不可用或在构造 queue_item 前抛异常而**提前退出且不发 job_status**，
则 backend/DAG 侧对应 job 永不收到终态——DAG 节点 future 永久悬挂、DagExecutor.run
无限阻塞、runner 泄漏。本测试锁死修复：任一死路径都必须发 "failed" job_status，
且 task_dag 节点起跑协程逃逸异常时经 notify_task_dag_terminal 判 failed。

hermetic：不连真实设备/ROS/网络。DeviceActionManager 为纯内存状态机；
websocket_client 用最小 fake 记录 publish_job_status；HostNode 经 monkeypatch。
"""

from __future__ import annotations

import asyncio
from queue import Queue

import unilabos.app.ws_client as ws
from unilabos.app.ws_client import DeviceActionManager, MessageProcessor


class _FakeWSClient:
    """最小 fake：记录 publish_job_status，其余为 no-op。"""

    def __init__(self) -> None:
        self.published: list[tuple[str, str, str]] = []  # (job_id, task_id, status)

    def register_job_start_request(self, request_data: dict) -> bool:
        return True  # 视为新请求，走真正执行路径

    def publish_job_status(self, feedback_data, item, status, return_info=None) -> None:
        self.published.append((item.job_id, item.task_id, status))

    def publish_action_lock(self, device_id: str, action: str, free: bool) -> None:
        pass


def _mp() -> tuple[MessageProcessor, _FakeWSClient]:
    mp = MessageProcessor("ws://test", Queue(), DeviceActionManager())
    fake = _FakeWSClient()
    mp.set_websocket_client(fake)
    return mp, fake


def _payload(job_id: str = "n1", task_id: str = "t1") -> dict:
    return {
        "device_id": "d1",
        "action": "add",
        "action_type": "SendCmd",
        "action_args": {},
        "sample_material": {},
        "task_id": task_id,
        "job_id": job_id,
        "notebook_id": "nb",
        "server_info": {},
    }


def test_job_start_host_node_missing_publishes_failed(monkeypatch):
    """Fix A：HostNode 不可用时必发 failed 终态，而非静默返回（否则悬挂）。"""
    mp, fake = _mp()

    class _NoHost:
        @staticmethod
        def get_instance(_i):
            return None

    monkeypatch.setattr(ws, "HostNode", _NoHost)

    asyncio.run(mp._handle_job_start(_payload("n1")))

    assert ("n1", "t1", "failed") in fake.published


def test_job_start_enqueue_raises_before_queue_item_publishes_failed(monkeypatch):
    """Fix B：enqueue_job 在构造 queue_item 前抛错，仍经 fallback queue_item 发 failed。"""
    mp, fake = _mp()

    def _boom(_job_info):
        raise RuntimeError("enqueue 爆炸")

    monkeypatch.setattr(mp.device_manager, "enqueue_job", _boom)

    asyncio.run(mp._handle_job_start(_payload("n2")))

    assert ("n2", "t1", "failed") in fake.published


def test_start_dag_node_guarded_escaping_exception_marks_failed(monkeypatch):
    """桥接兜底：_handle_job_start 逃逸异常时经 notify_task_dag_terminal 判 failed。"""
    mp, _ = _mp()

    class _RecRunner:
        def __init__(self) -> None:
            self.terminals: list[tuple[str, str]] = []

        def notify_terminal(self, job_id: str, status) -> None:
            self.terminals.append((job_id, status))

    rec = _RecRunner()
    mp._task_dag_runners["t1"] = rec

    async def _raise(_payload):
        raise RuntimeError("起跑逃逸")

    monkeypatch.setattr(mp, "_handle_job_start", _raise)

    asyncio.run(mp._start_dag_node_guarded("t1", "n3", _payload("n3")))

    assert ("n3", "failed") in rec.terminals


def test_start_dag_node_guarded_normal_return_does_not_resolve(monkeypatch):
    """正常返回**不**解析节点：排队(同设备串行)节点须保持 pending，终态另由回流。"""
    mp, _ = _mp()

    class _RecRunner:
        def __init__(self) -> None:
            self.terminals: list[tuple[str, str]] = []

        def notify_terminal(self, job_id: str, status) -> None:
            self.terminals.append((job_id, status))

    rec = _RecRunner()
    mp._task_dag_runners["t1"] = rec

    async def _ok(_payload):
        return None  # 副作用完成，无异常

    monkeypatch.setattr(mp, "_handle_job_start", _ok)

    asyncio.run(mp._start_dag_node_guarded("t1", "n4", _payload("n4")))

    assert rec.terminals == []  # 未被兜底解析
