"""schedule_ws — 桥的 OS 面 WS 服务器（路径 /api/v1/ws/schedule）。

OS 的 ws_client 主动外拨此路径连入（见 unilabos/app/ws_client.py:_connection_handler），
连上后本地桥扮演「后端」角色：向 OS 下发 task_dag、接收 OS 回流的 job_status、
按 (task_id, node_id) 维护逐节点 NodeState 状态表（node_id == job_id，F002 §1.1）。

报文严格复用 F002 契约，不新造字段：
- 下行 OS：{"action": "task_dag", "data": <F002 task_dag 载荷>}
           {"action": "cancel_task", "data": {"task_id": ..., "job_id": ...}}
- 上行（OS→桥）：{"action": "job_status", "data": {job_id, task_id, device_id,
           notebook_id, action_name, status, feedback_data, return_info, timestamp}}
           status ∈ running/success/failed（见 ws_client.publish_job_status）。

为便于 hermetic 测试，协议逻辑集中在 ScheduleSession（与真实 WS 传输解耦——
通过注入的 send 协程发消息，通过 handle_incoming 喂入 OS 回来的消息）；
serve_schedule_ws 只做 websockets.serve 绑定与 send/recv 接线。

契约见 docs/features/F003-local-workflow-bridge/interface-design.md §一。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from unilabos.scheduler.dag_model import TERMINAL_STATES, NodeState, TaskDag

logger = logging.getLogger(__name__)

# OS 面 WS 路径 —— 与 ws_client._build_websocket_url 拼出的路径逐字一致
SCHEDULE_WS_PATH = "/api/v1/ws/schedule"

# job_status.status 字面量 → NodeState（F002 §1.3；NodeState 值与之同名）
_STATUS_TO_NODE_STATE: dict[str, NodeState] = {
    "running": NodeState.RUNNING,
    "success": NodeState.SUCCESS,
    "failed": NodeState.FAILED,
    "cancelled": NodeState.CANCELLED,
}

# 视为 OS「就绪」的 action（ws_client 连上后 publish_host_ready 上报）
_HOST_READY_ACTIONS = frozenset({"host_ready", "host_node_ready", "ready", "host_info"})

JobStatusCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


def serialize_task_dag(dag: TaskDag) -> dict[str, Any]:
    """把 TaskDag 反序列化回 F002 task_dag 载荷（OS 面线格式）。

    是 build_task_dag_payload 的逆——后者做 UI 别名归一，此处只做纯 dataclass→dict，
    字段名严格是 F002 契约名，供下发 OS。
    """
    return {
        "task_id": dag.task_id,
        "notebook_id": dag.notebook_id,
        "server_info": dict(dag.server_info),
        "nodes": [
            {
                "node_id": node.node_id,
                "device_id": node.device_id,
                "action": node.action,
                "action_type": node.action_type,
                "action_args": dict(node.action_args),
                "sample_material": dict(node.sample_material),
                "always_free": node.always_free,
            }
            for node in dag.nodes.values()
        ],
        "edges": [
            {"source_node_uuid": e.source_node_uuid, "target_node_uuid": e.target_node_uuid}
            for e in dag.edges
        ],
    }


class RunHandle:
    """一次 task_dag 下发的运行句柄：维护逐节点 NodeState，全终态时 done 置位。"""

    def __init__(self, dag: TaskDag) -> None:
        self.dag = dag
        self.task_id = dag.task_id
        self.node_states: dict[str, NodeState] = {
            node_id: NodeState.PENDING for node_id in dag.nodes
        }
        self.done: asyncio.Event = asyncio.Event()

    def apply_status(self, node_id: str, status: str) -> None:
        """按 job_status.status 更新单节点态；全部进入终态则置 done。"""
        state = _STATUS_TO_NODE_STATE.get(status)
        if state is None:
            logger.debug("[schedule_ws] 忽略未知 status: %s (node=%s)", status, node_id)
            return
        if node_id not in self.node_states:
            logger.debug("[schedule_ws] job_status 指向未知节点: %s", node_id)
            return
        self.node_states[node_id] = state
        if all(s in TERMINAL_STATES for s in self.node_states.values()):
            self.done.set()

    def mark_all_cancelled(self) -> None:
        """取消任务：未终态节点标为 cancelled，并置 done。"""
        for node_id, state in self.node_states.items():
            if state not in TERMINAL_STATES:
                self.node_states[node_id] = NodeState.CANCELLED
        self.done.set()

    @property
    def finished(self) -> bool:
        return self.done.is_set()

    async def wait(self) -> dict[str, NodeState]:
        """阻塞直到全图终态，返回逐节点终态快照。"""
        await self.done.wait()
        return dict(self.node_states)


class ScheduleSession:
    """单个 OS 连接的调度会话（传输无关，便于 hermetic 测试）。

    对外暴露：
    - submit_dag(dag) -> RunHandle：下发 task_dag，返回运行句柄
    - cancel_task(task_id)：下发 cancel_task
    - on_job_status(cb)：注册 job_status 回调（供 UI 面翻译回流）
    - handle_incoming(message)：喂入 OS 回来的报文（job_status / host_ready / …）
    """

    def __init__(
        self,
        send: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        session_id: str = "",
    ) -> None:
        self._send = send
        self.session_id = session_id
        self._runs: dict[str, RunHandle] = {}
        self._job_status_cbs: list[JobStatusCallback] = []
        self.host_ready: asyncio.Event = asyncio.Event()

    def on_job_status(self, cb: JobStatusCallback) -> None:
        """注册 job_status 回调——每收到一条 OS job_status 就以其 data 段回调。"""
        self._job_status_cbs.append(cb)

    def off_job_status(self, cb: JobStatusCallback) -> None:
        """注销 job_status 回调（UI 会话断开时调用，避免回调在长寿命 OS 会话上累积）。

        本 ScheduleSession 常寿命共享给多个 UI 连接；每个 UI 会话构造时注册一个回调，
        断开时必须注销——否则回调（其 send 指向已关闭的 socket）无限累积，每条回流
        都遍历一遍失效回调、抛异常刷屏。以身份匹配移除首个相等项。
        """
        try:
            self._job_status_cbs.remove(cb)
        except ValueError:
            logger.debug("[schedule_ws] off_job_status: 回调未注册，忽略")

    def get_run(self, task_id: str) -> RunHandle | None:
        return self._runs.get(task_id)

    def node_state(self, task_id: str, node_id: str) -> NodeState | None:
        """查 (task_id, node_id) 当前态；无则 None。"""
        run = self._runs.get(task_id)
        return run.node_states.get(node_id) if run else None

    async def submit_dag(self, dag: TaskDag) -> RunHandle:
        """下发一张 task_dag。同 task_id 已在执行则返回既有句柄（任务级幂等，与 OS 侧一致）。"""
        task_id = dag.task_id
        existing = self._runs.get(task_id)
        if existing is not None:
            logger.info("[schedule_ws] task_dag %s 已在执行，复用既有句柄", task_id)
            return existing
        handle = RunHandle(dag)
        self._runs[task_id] = handle
        await self._send({"action": "task_dag", "data": serialize_task_dag(dag)})
        logger.info(
            "[schedule_ws] 已下发 task_dag %s：%d 节点 / %d 边",
            task_id,
            len(dag.nodes),
            len(dag.edges),
        )
        return handle

    async def cancel_task(self, task_id: str, job_id: str = "") -> None:
        """下发 cancel_task（OS 侧 _handle_cancel_action 按 task_id/job_id 取消）。"""
        data: dict[str, Any] = {"task_id": task_id}
        if job_id:
            data["job_id"] = job_id
        await self._send({"action": "cancel_task", "data": data})
        run = self._runs.get(task_id)
        if run is not None and not job_id:
            run.mark_all_cancelled()
        logger.info("[schedule_ws] 已下发 cancel_task %s job=%s", task_id, job_id or "-")

    async def handle_incoming(self, message: dict[str, Any]) -> None:
        """喂入一条 OS 回来的报文。仅处理桥关心的 job_status / host_ready，其余忽略。"""
        if not isinstance(message, dict):
            logger.debug("[schedule_ws] 丢弃非对象报文: %r", message)
            return
        action = message.get("action", "")
        data = message.get("data")
        data = data if isinstance(data, dict) else {}
        if action == "job_status":
            await self._on_job_status(data)
        elif action in _HOST_READY_ACTIONS:
            self.host_ready.set()
            logger.info("[schedule_ws] OS 已就绪 (action=%s)", action)
        else:
            logger.debug("[schedule_ws] 忽略报文 action=%s", action)

    async def _on_job_status(self, data: dict[str, Any]) -> None:
        """更新逐节点状态表并触发回调。node_id == job_id（F002 §1.1）。

        回调可为同步或异步——异步回调（返回 awaitable）会被 await，
        使 UI 面（如 workflow_ws）能在同一时序内 await 推送，保证测试可判定。
        """
        task_id = data.get("task_id", "")
        node_id = data.get("job_id", "")
        status = data.get("status", "")
        run = self._runs.get(task_id)
        if run is not None and node_id:
            run.apply_status(node_id, status)
        for cb in self._job_status_cbs:
            try:
                result = cb(data)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001 —— 单个回调异常不得中断状态收敛
                logger.exception("[schedule_ws] job_status 回调异常")


class ScheduleWSServer:
    """OS 面 WS 服务器：在 SCHEDULE_WS_PATH 上 accept 唯一 OS 连接。

    只保留最近一个连入的 ScheduleSession（本地桥单 OS 场景）。真实传输接线：
    每条收到的文本消息 json.loads 后喂 session.handle_incoming；session 的 send
    经 websocket.send(json.dumps(..., ensure_ascii=False)) 外发。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8890) -> None:
        self.host = host
        self.port = port
        self.session: ScheduleSession | None = None
        self._server: Any = None
        self._session_ready: asyncio.Event = asyncio.Event()
        self._on_session_cbs: list[Callable[[ScheduleSession], None]] = []

    def on_session(self, cb: Callable[[ScheduleSession], None]) -> None:
        """注册「新 OS 连接就绪」回调（UI 面借此拿到 session 接线）。"""
        self._on_session_cbs.append(cb)
        if self.session is not None:
            cb(self.session)

    async def wait_session(self) -> ScheduleSession:
        """阻塞直到有 OS 连入并建立 session。"""
        await self._session_ready.wait()
        assert self.session is not None
        return self.session

    async def start(self) -> None:
        """起 websockets 服务器（延迟 import，未装 websockets 时不影响其余桥面）。"""
        import websockets

        async def handler(websocket: Any) -> None:
            path = getattr(websocket, "path", "") or getattr(
                getattr(websocket, "request", None), "path", ""
            )
            if path and not str(path).startswith(SCHEDULE_WS_PATH):
                logger.warning("[schedule_ws] 拒绝非法路径连接: %s", path)
                await websocket.close(code=1008, reason="unexpected path")
                return
            await self._serve_connection(websocket)

        self._server = await websockets.serve(handler, self.host, self.port)
        logger.info("[schedule_ws] OS 面 WS 已监听 ws://%s:%d%s", self.host, self.port, SCHEDULE_WS_PATH)

    async def _serve_connection(self, websocket: Any) -> None:
        session_id = _extract_session_id(websocket)

        async def send(msg: dict[str, Any]) -> None:
            await websocket.send(json.dumps(msg, ensure_ascii=False))

        session = ScheduleSession(send, session_id=session_id)
        self.session = session
        self._session_ready.set()
        for cb in self._on_session_cbs:
            try:
                cb(session)
            except Exception:  # noqa: BLE001
                logger.exception("[schedule_ws] on_session 回调异常")
        logger.info("[schedule_ws] OS 已连入 (session_id=%s)", session_id or "-")

        try:
            async for raw in websocket:
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    logger.error("[schedule_ws] 收到非法 JSON: %s", raw)
                    continue
                await session.handle_incoming(message)
        finally:
            if self.session is session:
                self.session = None
                self._session_ready.clear()
            logger.info("[schedule_ws] OS 连接断开 (session_id=%s)", session_id or "-")

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("[schedule_ws] OS 面 WS 已停止")


def _extract_session_id(websocket: Any) -> str:
    """从连接头 EdgeSession 取 session_id（ws_client 连入时携带）。"""
    try:
        headers = getattr(getattr(websocket, "request", None), "headers", None)
        if headers is None:
            headers = getattr(websocket, "request_headers", None)
        if headers is not None:
            return headers.get("EdgeSession", "") or ""
    except Exception:  # noqa: BLE001
        return ""
    return ""
