"""HostLink 客户端（slave 侧）：组网、在线监控、请求通道。

组网：配置 host node 的 ip:port（HostLinkConfig / --hostlink_addr），
``start()`` 后台线程维持连接：connect → hello 握手 → 周期 ping 心跳；
断线指数退避自动重连。``online`` 随时可查，状态变化回调 ``on_status_change``。

请求通道：``request(action_type, ...)`` 同步等响应（按消息 id 关联，支持并发
调用）；物料查询封装为 ``get_resource()``，返回与旧云端接口一致的
raw dict 列表，设备端零改动换源。

进程级单例：slave 主流程 ``set_hostlink_client()`` 注册后，设备节点用
``get_hostlink_client()`` 取用（TCP 优先，ROS service 兜底）。
"""

from __future__ import annotations

import socket
import threading
import time
import uuid as uuid_mod
from typing import Any, Callable, Dict, List, Optional

from unilabos.hostlink.protocol import (
    ActionType,
    LineReader,
    LinkError,
    RemoteError,
    new_request,
    read_message,
    send_message,
)
from unilabos.hostlink.ros_assist import RosNetworkInfo
from unilabos.utils import logger


class _Pending:
    __slots__ = ("event", "response")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.response: Optional[Dict[str, Any]] = None


class HostLinkClient:
    """与 host 的长连接客户端；线程安全，可并发 request。"""

    def __init__(
        self,
        host: str,
        port: int,
        machine_name: str = "",
        heartbeat_interval: float = 5.0,
        connect_timeout: float = 5.0,
        request_timeout: float = 10.0,
        reconnect_max_backoff: float = 10.0,
        on_status_change: Optional[Callable[[bool], None]] = None,
    ):
        self.host = host
        self.port = port
        self.machine_name = machine_name
        self.heartbeat_interval = heartbeat_interval
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self.reconnect_max_backoff = reconnect_max_backoff
        self.on_status_change = on_status_change

        self._sock: Optional[socket.socket] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._manager_thread: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()
        self._pending: Dict[str, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._stop = threading.Event()
        self._online = threading.Event()
        #: hello 响应缓存（含 ros 组网协助）
        self.hello_info: Dict[str, Any] = {}

    # ── 生命周期 ─────────────────────────────────────────────

    def start(self) -> "HostLinkClient":
        """启动后台连接管理线程（非阻塞）。"""
        if self._manager_thread is not None and self._manager_thread.is_alive():
            return self
        self._stop.clear()
        self._manager_thread = threading.Thread(
            target=self._run, name="hostlink-client", daemon=True
        )
        self._manager_thread.start()
        return self

    def connect_blocking(self, timeout: float = 10.0) -> bool:
        """启动并阻塞等待首次上线（slave 启动期用：拿 ROS 组网信息再起 ROS）。"""
        self.start()
        return self._online.wait(timeout)

    def close(self) -> None:
        self._stop.set()
        self._teardown_socket()
        if self._manager_thread is not None:
            self._manager_thread.join(timeout=3)

    @property
    def online(self) -> bool:
        return self._online.is_set()

    # ── 请求通道 ─────────────────────────────────────────────

    def request(
        self,
        action_type: str,
        data: Optional[Dict[str, Any]] = None,
        query_key: str = "",
        key: str = "",
        timeout: Optional[float] = None,
    ) -> Any:
        """发送请求并同步等待响应 data；离线/超时抛 LinkError，业务失败抛 RemoteError。"""
        sock = self._sock
        if sock is None or not self._online.is_set():
            raise LinkError(f"hostlink offline (host={self.host}:{self.port})")
        message = new_request(action_type, data=data, query_key=query_key, key=key)
        pending = _Pending()
        request_id = message["id"]
        with self._pending_lock:
            self._pending[request_id] = pending
        try:
            with self._write_lock:
                send_message(sock, message)
            if not pending.event.wait(timeout or self.request_timeout):
                raise LinkError(f"request timeout: {action_type} ({request_id[:8]})")
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        response = pending.response or {}
        if not response.get("ok"):
            raise RemoteError(str(response.get("error") or "remote error"))
        return response.get("data")

    def get_resource(
        self,
        uuid: Optional[str] = None,
        res_id: Optional[str] = None,
        with_children: bool = True,
        timeout: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """物料/资源查询：返回扁平 raw dict 列表（与旧云端接口同形状）。"""
        data = self.request(
            ActionType.MATERIAL,
            data={"uuid": uuid, "id": res_id, "with_children": with_children},
            query_key="uuid" if uuid else "id",
            key=uuid or res_id or "",
            timeout=timeout,
        )
        nodes = (data or {}).get("nodes")
        return list(nodes or [])

    def ros_info(self, timeout: Optional[float] = None) -> RosNetworkInfo:
        """拉取 host 的 ROS 组网协助信息（hello 后也可单独刷新）。"""
        data = self.request(ActionType.ROS_INFO, timeout=timeout)
        return RosNetworkInfo.from_dict((data or {}).get("ros") or data)

    def hello_ros_info(self) -> RosNetworkInfo:
        """从 hello 缓存读 ROS 组网信息（connect_blocking 成功后可用）。"""
        return RosNetworkInfo.from_dict(self.hello_info.get("ros"))

    # ── 内部：连接管理 ────────────────────────────────────────

    def _run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            try:
                self._connect_once()
                backoff = 0.5  # 连上即重置退避
                self._heartbeat_loop()
            except (OSError, LinkError) as exc:
                logger.debug(f"[HostLink] connection cycle ended: {exc}")
            self._set_online(False)
            self._teardown_socket()
            if self._stop.is_set():
                break
            self._stop.wait(backoff)
            backoff = min(backoff * 2, self.reconnect_max_backoff)

    def _connect_once(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        sock.settimeout(None)  # 读超时交给 reader 线程阻塞读
        self._sock = sock
        self._reader_thread = threading.Thread(
            target=self._read_loop, args=(sock,), name="hostlink-reader", daemon=True
        )
        self._reader_thread.start()
        # 握手（直接走 pending 机制之前，需要 online 未置位也能发）
        message = new_request(
            ActionType.HELLO,
            data={"machine_name": self.machine_name, "role": "slave", "pid": None},
        )
        pending = _Pending()
        with self._pending_lock:
            self._pending[message["id"]] = pending
        with self._write_lock:
            send_message(sock, message)
        if not pending.event.wait(self.connect_timeout):
            raise LinkError("hello timeout")
        response = pending.response or {}
        if not response.get("ok"):
            raise LinkError(f"hello rejected: {response.get('error')}")
        self.hello_info = dict(response.get("data") or {})
        self._set_online(True)
        logger.info(f"[HostLink] connected to {self.host}:{self.port}")

    def _heartbeat_loop(self) -> None:
        """周期 ping；失败/超时视为断线，交回 _run 重连。

        ping 超时用 request_timeout（而非发送周期）：服务端连接内已并发分发，
        正常负载下 ping 秒回；宽超时只兜底半开 TCP（对端悄然消失）的检测。
        """
        while not self._stop.is_set():
            if self._stop.wait(self.heartbeat_interval):
                return
            self.request(ActionType.PING, timeout=self.request_timeout)

    def _read_loop(self, sock: socket.socket) -> None:
        reader = LineReader(sock)  # 见 protocol.LineReader：makefile 与 timeout 不兼容
        try:
            while True:
                message = read_message(reader)
                if message is None:
                    break
                if message.get("kind") != "resp":
                    continue
                request_id = str(message.get("id") or "")
                with self._pending_lock:
                    pending = self._pending.get(request_id)
                if pending is not None:
                    pending.response = message
                    pending.event.set()
        except (LinkError, OSError):
            pass
        finally:
            reader.close()
            self._set_online(False)
            # 唤醒所有等待者（以离线错误收场，避免卡满超时）
            with self._pending_lock:
                for pending in self._pending.values():
                    if pending.response is None:
                        pending.response = {"ok": False, "error": "connection closed"}
                    pending.event.set()

    def _teardown_socket(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _set_online(self, value: bool) -> None:
        changed = value != self._online.is_set()
        if value:
            self._online.set()
        else:
            self._online.clear()
        if changed and self.on_status_change is not None:
            try:
                self.on_status_change(value)
            except Exception:  # noqa: BLE001 - 回调故障不影响通路
                logger.exception("[HostLink] on_status_change callback failed")


# ── 进程级单例（slave 主流程注册，设备节点取用） ───────────────

_client_lock = threading.Lock()
_client: Optional[HostLinkClient] = None


def set_hostlink_client(client: Optional[HostLinkClient]) -> None:
    global _client
    with _client_lock:
        _client = client


def get_hostlink_client() -> Optional[HostLinkClient]:
    with _client_lock:
        return _client


__all__ = ["HostLinkClient", "get_hostlink_client", "set_hostlink_client"]
