"""HostLink 服务端（host node 侧）：TCP 监听 + peer 注册 + 心跳在线监控。

线程模型与本仓库其余部分一致（多线程、无 asyncio）：
``ThreadingTCPServer`` 每连接一个处理线程，逐行读请求、同步调 handler、
写回响应。请求处理是无状态纯函数，天然支持多 slave 并发。

在线监控：
- slave 连接后先发 ``hello`` 注册身份（machine_name 等），随后按
  ``heartbeat_interval`` 发 ``ping``；
- 服务端记录每个 peer 的 ``last_seen``；``peers()`` 按
  ``last_seen + heartbeat_timeout`` 现算 online（无独立清扫线程，无竞态）；
- 连接断开即离线（保留最后一次记录供排查）。
"""

from __future__ import annotations

import socket
import socketserver
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from unilabos.hostlink.protocol import (
    ActionType,
    LineReader,
    LinkError,
    new_response,
    read_message,
    send_message,
)
from unilabos.utils import logger

#: handler 签名：(data, peer_info) -> 响应 data；抛异常 = ok=false
Handler = Callable[[Dict[str, Any], Dict[str, Any]], Any]


class _LinkTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    link: "HostLinkServer"  # start() 时注入


class _LinkRequestHandler(socketserver.BaseRequestHandler):
    """每连接一个线程；self.server 是 _LinkTCPServer，业务状态经 .link 访问。"""

    def handle(self) -> None:  # noqa: D102 - socketserver 接口
        link: HostLinkServer = self.server.link  # type: ignore[attr-defined]
        sock: socket.socket = self.request
        sock.settimeout(link.socket_timeout)
        # 不能用 sock.makefile：带 timeout 的 socket 上一次超时后文件对象即损坏
        # （"cannot read from timed out object"）；LineReader 基于 recv，超时可安全重试
        reader = LineReader(sock)
        peer_key = f"{self.client_address[0]}:{self.client_address[1]}"
        # 单连接内每请求独立线程分发：慢 handler（大物料树查询）不得阻塞
        # 后续请求——尤其是心跳 ping，否则会被误判离线。写回共用一把锁防交错。
        write_lock = threading.Lock()

        def _serve_one(message: Dict[str, Any]) -> None:
            response = self._dispatch(link, message, peer_key)
            try:
                with write_lock:
                    send_message(sock, response)
            except OSError:
                pass  # 连接已断，读循环会退出并标记离线

        try:
            while not link.stopping.is_set():
                try:
                    message = read_message(reader)
                except LinkError as exc:
                    logger.warning(f"[HostLink] {peer_key} bad frame, closing: {exc}")
                    break
                except (socket.timeout, TimeoutError):
                    continue  # 空闲连接继续等；在线判定交给 last_seen
                except OSError:
                    break
                if message is None:
                    break  # 对端正常关闭
                if message.get("kind") != "req":
                    continue  # 服务端只消费请求帧
                threading.Thread(
                    target=_serve_one, args=(message,), daemon=True,
                    name=f"hostlink-req-{peer_key}",
                ).start()
        finally:
            reader.close()
            link.mark_disconnected(peer_key)

    @staticmethod
    def _dispatch(
        link: "HostLinkServer", message: Dict[str, Any], peer_key: str
    ) -> Dict[str, Any]:
        request_id = str(message.get("id") or "")
        action = str(message.get("action_type") or "")
        data = message.get("data") or {}
        peer = link.touch_peer(peer_key, action, message)
        handler = link.handlers.get(action)
        if handler is None:
            return new_response(request_id, False, error=f"unknown action_type: {action}")
        try:
            result = handler(dict(data), peer)
        except Exception as exc:  # noqa: BLE001 - 业务异常统一转 ok=false
            logger.warning(f"[HostLink] handler {action} failed for {peer_key}: {exc}")
            return new_response(request_id, False, error=str(exc))
        return new_response(request_id, True, data=result)


class HostLinkServer:
    """host 侧通路服务。``start()`` 后台线程监听；``stop()`` 幂等关闭。"""

    def __init__(
        self,
        bind: str = "0.0.0.0",
        port: int = 7302,
        heartbeat_timeout: float = 15.0,
        socket_timeout: float = 1.0,
    ):
        self._bind = bind
        self._port = port
        self.heartbeat_timeout = heartbeat_timeout
        self.socket_timeout = socket_timeout
        self.handlers: Dict[str, Handler] = {}
        self.stopping = threading.Event()
        self._peers: Dict[str, Dict[str, Any]] = {}
        self._peers_lock = threading.Lock()
        self._tcp: Optional[_LinkTCPServer] = None
        self._thread: Optional[threading.Thread] = None
        # 内置动作：心跳与握手
        self.register_handler(ActionType.PING, self._handle_ping)
        self.register_handler(ActionType.HELLO, self._handle_hello)
        #: hello 响应附带的静态信息（ros 组网协助等），由装配方填充
        self.hello_payload: Dict[str, Any] = {}

    # ── 生命周期 ─────────────────────────────────────────────

    def start(self) -> "HostLinkServer":
        self._tcp = _LinkTCPServer((self._bind, self._port), _LinkRequestHandler)
        self._tcp.link = self
        # 端口 0 时回读实际端口（测试友好）
        self._port = self._tcp.server_address[1]
        self._thread = threading.Thread(
            target=self._tcp.serve_forever, name="hostlink-server", daemon=True
        )
        self._thread.start()
        logger.info(f"[HostLink] server listening on {self._bind}:{self._port}")
        return self

    def stop(self) -> None:
        self.stopping.set()
        if self._tcp is not None:
            self._tcp.shutdown()
            self._tcp.server_close()
            self._tcp = None

    @property
    def port(self) -> int:
        return self._port

    # ── handler 与 peer 注册 ─────────────────────────────────

    def register_handler(self, action_type: str, handler: Handler) -> None:
        self.handlers[action_type] = handler

    def touch_peer(self, peer_key: str, action: str, message: Dict[str, Any]) -> Dict[str, Any]:
        """任何请求都刷新 last_seen；hello 额外登记身份。"""
        now = time.time()
        with self._peers_lock:
            peer = self._peers.setdefault(
                peer_key,
                {"addr": peer_key, "machine_name": "", "role": "", "connected_at": now},
            )
            peer["last_seen"] = now
            peer["connected"] = True
            if action == ActionType.HELLO:
                data = message.get("data") or {}
                peer["machine_name"] = str(data.get("machine_name") or "")
                peer["role"] = str(data.get("role") or "slave")
            return dict(peer)

    def mark_disconnected(self, peer_key: str) -> None:
        with self._peers_lock:
            peer = self._peers.get(peer_key)
            if peer is not None:
                peer["connected"] = False

    def peers(self) -> List[Dict[str, Any]]:
        """当前已知 peer 及在线状态（online = 连接存活且心跳未超时）。"""
        now = time.time()
        with self._peers_lock:
            result = []
            for peer in self._peers.values():
                snapshot = dict(peer)
                snapshot["online"] = bool(
                    snapshot.get("connected")
                    and now - snapshot.get("last_seen", 0) < self.heartbeat_timeout
                )
                result.append(snapshot)
            return result

    # ── 内置动作 ─────────────────────────────────────────────

    def _handle_ping(self, data: Dict[str, Any], peer: Dict[str, Any]) -> Dict[str, Any]:
        return {"pong": True, "server_time": time.time()}

    def _handle_hello(self, data: Dict[str, Any], peer: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "server_time": time.time(),
            "heartbeat_timeout": self.heartbeat_timeout,
            **self.hello_payload,
        }


# ── 进程级单例（host 主流程注册，REST 面取用做在线监控） ─────────

_server_lock = threading.Lock()
_server: Optional[HostLinkServer] = None


def set_hostlink_server(server: Optional[HostLinkServer]) -> None:
    global _server
    with _server_lock:
        _server = server


def get_hostlink_server() -> Optional[HostLinkServer]:
    with _server_lock:
        return _server


__all__ = ["Handler", "HostLinkServer", "get_hostlink_server", "set_hostlink_server"]
