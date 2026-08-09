"""server — 本地工作流桥组合入口（单 asyncio event loop 起三面 + 可选离线执行核）。

把桥的三面服务器组合在一个事件循环里：
- schedule_ws.ScheduleWSServer(:8890)——OS 面 WS，真实 OS 的 ws_client 连入。
- workflow_ws.WorkflowWSServer(:8891)——实现 A UI 面 WS，云端两个 panel 连入。
- local_api.LocalApiServer(:8014)——实现 B UI 面 HTTP，SZLab local_ui 轮询。

两档执行模式（见 interface-design.md §四）：
- 真实下发（默认）：ScheduleWSServer 等真实 OS 连入建 ScheduleSession，两个 UI 面
  经此 session 把整张 DAG 下发 OS、收真实 job_status 回流。单一事实源在 OS。
- 离线自足（--offline）：无真实 OS 时，用 offline_os.OfflineOS 在进程内顶替 OS 面——
  同一 ScheduleSession 的 send 接到 OfflineOS.receive，OfflineOS 用 F002 DagExecutor
  走同一张 TaskDag、每设备锁保 I3、逐节点回发 job_status，UI 面因而无 OS 也能完整动。

UI 面经 get_schedule_session / get_local_api_state 解析「当前就绪 session」——真实模式由
OS 连入时的 on_session 回调注入并据此建唯一 LocalApiState；离线模式启动即注入。
build_offline_session 为纯装配（无网络），便于 hermetic 测。

python -m unilabos.app.local_bridge.server [--offline] 独立起桥，不改动既有 unilab 启动路径。
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from unilabos.app.local_bridge.local_api import LocalApiServer, LocalApiState
from unilabos.app.local_bridge.offline_os import OfflineOS
from unilabos.app.local_bridge.schedule_ws import ScheduleSession, ScheduleWSServer
from unilabos.app.local_bridge.workflow_ws import WorkflowWSServer
from unilabos.scheduler.dag_model import NodeState

logger = logging.getLogger(__name__)

# 三面默认端口（对齐 interface-design.md 与两套前端代理配置）
DEFAULT_SCHEDULE_PORT = 8890
DEFAULT_WORKFLOW_PORT = 8891
DEFAULT_API_PORT = 8014


def build_offline_session(
    results: dict[str, NodeState] | None = None,
) -> tuple[ScheduleSession, OfflineOS]:
    """装配离线执行核：ScheduleSession(send→OfflineOS.receive) + OfflineOS.bind(session)。

    返回 (session, offline)——session 的行为与真实 OS 连入时建立的完全一致（下发 task_dag、
    收 job_status），只是对端换成进程内 OfflineOS。纯装配无网络，供离线模式与 hermetic 测复用。
    """
    offline = OfflineOS(results=results)
    session = ScheduleSession(offline.receive, session_id="offline")
    offline.bind(session)
    return session, offline


class LocalBridgeServer:
    """组合三面服务器 + 管理「当前就绪 ScheduleSession / LocalApiState」。

    - 真实模式：ScheduleWSServer 于 OS 连入时经 on_session 注入 session，据此建唯一 LocalApiState。
    - 离线模式：构造即经 build_offline_session 装配 session 并建 LocalApiState，OS 面 WS 仍监听
      （允许真实 OS 之后接管，但离线 session 已足以驱动 UI）。
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        schedule_port: int = DEFAULT_SCHEDULE_PORT,
        workflow_port: int = DEFAULT_WORKFLOW_PORT,
        api_port: int = DEFAULT_API_PORT,
        offline: bool = False,
    ) -> None:
        self.host = host
        self.offline = offline
        self._session: ScheduleSession | None = None
        self._local_api_state: LocalApiState | None = None
        self._offline_os: OfflineOS | None = None

        if offline:
            self._session, self._offline_os = build_offline_session()
            self._local_api_state = LocalApiState(self._session)
            logger.info("[bridge] 离线模式：进程内 OfflineOS 顶替 OS 面")

        self._schedule_server = ScheduleWSServer(host=host, port=schedule_port)
        self._schedule_server.on_session(self._adopt_session)
        self._workflow_server = WorkflowWSServer(
            self._get_schedule_session, host=host, port=workflow_port
        )
        self._api_server = LocalApiServer(
            self._get_local_api_state, host=host, port=api_port
        )

    def _adopt_session(self, session: ScheduleSession) -> None:
        """OS 连入（真实模式）：接管为当前 session 并据此建唯一 LocalApiState。"""
        self._session = session
        self._local_api_state = LocalApiState(session)
        logger.info("[bridge] 已接管 OS 连入的调度会话，UI 面就绪")

    def _get_schedule_session(self) -> ScheduleSession | None:
        return self._session

    def _get_local_api_state(self) -> LocalApiState | None:
        return self._local_api_state

    async def start(self) -> None:
        """并起三面服务器（各自延迟 import 传输依赖）并常驻。"""
        await asyncio.gather(
            self._schedule_server.start(),
            self._workflow_server.start(),
            self._api_server.start(),
        )

    async def stop(self) -> None:
        await asyncio.gather(
            self._schedule_server.stop(),
            self._workflow_server.stop(),
            self._api_server.stop(),
            return_exceptions=True,
        )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Uni-Lab 本地工作流桥（替代 Go 后端）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--schedule-port", type=int, default=DEFAULT_SCHEDULE_PORT, help="OS 面 WS 端口")
    parser.add_argument("--workflow-port", type=int, default=DEFAULT_WORKFLOW_PORT, help="实现 A UI 面 WS 端口")
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT, help="实现 B UI 面 HTTP 端口")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="离线自足模式：进程内 OfflineOS 顶替 OS 面（无真实 OS 亦可驱动 UI）",
    )
    return parser.parse_args(argv)


async def _amain(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    server = LocalBridgeServer(
        host=args.host,
        schedule_port=args.schedule_port,
        workflow_port=args.workflow_port,
        api_port=args.api_port,
        offline=args.offline,
    )
    logger.info(
        "[bridge] 启动：schedule=ws://%s:%d /api/v1/ws/schedule | workflow=ws://%s:%d /ws/workflow/{uuid} | api=http://%s:%d/api",
        args.host,
        args.schedule_port,
        args.host,
        args.workflow_port,
        args.host,
        args.api_port,
    )
    try:
        await server.start()
    finally:
        await server.stop()


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        asyncio.run(_amain(argv))
    except KeyboardInterrupt:
        logger.info("[bridge] 收到中断，退出")


if __name__ == "__main__":
    main()
