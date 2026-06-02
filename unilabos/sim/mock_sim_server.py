"""零依赖 Mock Sim 服务端：在没有 Isaac Sim 时，用同一套协议（spec 16/17）跑通整条
Edge<->Sim 通信链路，便于本地/CI 直接运行与验证。

仅依赖 `websockets`（已在 unilabos/utils/requirements.txt），不引入 isaacsim。

作为库使用（测试）::

    from unilabos.sim.mock_sim_server import MockSimServer
    server = MockSimServer(host="127.0.0.1", port=0)
    server.start()
    print(server.endpoint)   # ws://127.0.0.1:<port>/edge-sim/v1
    ...
    server.stop()

作为命令行使用（手测）::

    python -m unilabos.sim.mock_sim_server --host 127.0.0.1 --port 9000 --path /edge-sim/v1 --demo

收到 Edge 的 session.start 后，--demo 会定时演示推送一次 collision.event 与 joint_command.set，
用于手动观察反向链路 D/E。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import websockets

SPEC_VERSION = "1.0.0"


def build_envelope(
    msg_type: str,
    payload: Dict[str, Any],
    session_id: str,
    *,
    target: str = "edge",
    source: str = "sim",
    need_ack: Optional[bool] = None,
    error: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    sequence: Optional[int] = None,
) -> Dict[str, Any]:
    msg: Dict[str, Any] = {
        "spec_version": SPEC_VERSION,
        "msg_id": str(uuid.uuid4()),
        "msg_type": msg_type,
        "timestamp_ms": int(time.time() * 1000),
        "session_id": session_id,
        "source": source,
        "target": target,
        "payload": payload,
        "error": error,
    }
    if need_ack is not None:
        msg["need_ack"] = need_ack
    if trace_id is not None:
        msg["trace_id"] = trace_id
    if sequence is not None:
        msg["sequence"] = sequence
    return msg


class MockSimServer:
    """模拟 Isaac Sim 的 WebSocket 服务端，应答 Edge 网关的统一协议消息。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, path: str = "/edge-sim/v1") -> None:
        self.host = host
        self.port = port
        self.path = path
        self.session_id = f"mock_sess_{int(time.time())}"

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[Any] = None
        self._started = threading.Event()
        self._clients: "set[Any]" = set()

        # 线程安全的接收记录：按 msg_type 计数 + 保留最近 payload + 全量信封列表
        self._lock = threading.Lock()
        self.counts: Dict[str, int] = {}
        self.last_payload: Dict[str, Dict[str, Any]] = {}
        self.messages: List[Dict[str, Any]] = []
        # session.start 是否已收到（demo / 等待用）
        self.session_started = threading.Event()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self, timeout: float = 5.0) -> "MockSimServer":
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._run, name="mock_sim_server", daemon=True)
        self._thread.start()
        if not self._started.wait(timeout=timeout):
            raise RuntimeError("MockSimServer failed to start within timeout")
        return self

    def stop(self) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._thread = None

    @property
    def endpoint(self) -> str:
        return f"ws://{self.host}:{self.port}{self.path}"

    # ------------------------------------------------------------------
    # 反向链路注入（D: 碰撞；E: 关节命令）
    # ------------------------------------------------------------------
    def push_collision(self, pairs: List[Dict[str, Any]], severity: str = "warn") -> None:
        payload = {
            "event_id": f"col_{uuid.uuid4().hex[:8]}",
            "severity": severity,
            "sim_time_s": round(time.time() % 1000, 3),
            "pairs": pairs,
        }
        self._broadcast(build_envelope("collision.event", payload, self.session_id, need_ack=False))

    def push_joint_command(
        self,
        device_id: str,
        joint_names: List[str],
        target_positions_rad: List[float],
        control_mode: str = "position",
        **extra: Any,
    ) -> str:
        command_id = f"cmd_{device_id}_{uuid.uuid4().hex[:8]}"
        payload = {
            "command_id": command_id,
            "device_id": device_id,
            "control_mode": control_mode,
            "joint_names": joint_names,
            "target_positions_rad": target_positions_rad,
            "command_timestamp_ms": int(time.time() * 1000),
        }
        payload.update(extra)
        self._broadcast(build_envelope("joint_command.set", payload, self.session_id, need_ack=True))
        return command_id

    # ------------------------------------------------------------------
    # 接收记录访问器
    # ------------------------------------------------------------------
    def count(self, msg_type: str) -> int:
        with self._lock:
            return self.counts.get(msg_type, 0)

    def payload_of(self, msg_type: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self.last_payload.get(msg_type)

    def wait_for(self, msg_type: str, timeout: float = 5.0, min_count: int = 1) -> bool:
        """轮询等待某类消息累计到 min_count（best-effort，避免引入额外同步原语）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.count(msg_type) >= min_count:
                return True
            time.sleep(0.02)
        return self.count(msg_type) >= min_count

    # ------------------------------------------------------------------
    # 内部：事件循环 / 连接处理
    # ------------------------------------------------------------------
    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._start_server())
        try:
            self._loop.run_forever()  # 直到 stop() 调用 loop.stop()
        finally:
            try:
                self._loop.run_until_complete(self._shutdown())
            finally:
                self._loop.close()

    async def _start_server(self) -> None:
        self._server = await websockets.serve(self._handler, self.host, self.port)
        # port=0 时回填实际监听端口
        try:
            sock = self._server.sockets[0]
            self.port = sock.getsockname()[1]
        except Exception:
            pass
        print(f"[mock-sim] listening {self.endpoint}")
        self._started.set()

    async def _shutdown(self) -> None:
        for ws in list(self._clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._clients.clear()
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

    async def _handler(self, ws: Any) -> None:
        self._clients.add(ws)
        peer = getattr(ws, "remote_address", None)
        print(f"[mock-sim] edge connected: {peer}")
        try:
            async for raw in ws:
                try:
                    env = json.loads(raw)
                except Exception as exc:
                    print(f"[mock-sim] bad message: {exc}")
                    continue
                await self._on_message(ws, env)
        except Exception as exc:
            print(f"[mock-sim] connection error: {exc}")
        finally:
            self._clients.discard(ws)
            print("[mock-sim] edge disconnected")

    async def _on_message(self, ws: Any, env: Dict[str, Any]) -> None:
        msg_type = env.get("msg_type", "")
        payload = env.get("payload") or {}
        trace_id = env.get("trace_id")
        with self._lock:
            self.counts[msg_type] = self.counts.get(msg_type, 0) + 1
            self.last_payload[msg_type] = payload
            self.messages.append(env)

        if msg_type == "hello":
            await self._reply(ws, "hello.ack", {
                "capabilities": ["world", "asset", "attach", "joint", "collision", "joint_command"],
                "formats": ["urdf", "usd"],
                "max_joint_hz": 200,
            }, trace_id=trace_id)
        elif msg_type == "world.create":
            await self._reply(ws, "world.create.ack", {
                "world_name": payload.get("world_name", ""),
                "success": True,
            }, trace_id=trace_id)
        elif msg_type == "session.start":
            self.session_started.set()
            await self._reply(ws, "session.ready", {
                "mode": payload.get("mode", "sim"),
            }, trace_id=trace_id)
        elif msg_type == "asset.upsert":
            await self._reply(ws, "asset.upsert.ack", {
                "asset_id": payload.get("asset_id"),
                "prim_path": payload.get("prim_path"),
                "success": True,
            }, trace_id=trace_id)
        elif msg_type == "attach.request":
            await self._reply(ws, "attach.ack", {
                "attachment_id": payload.get("attachment_id"),
                "status": "ok",
                "sim_constraint_handle": f"mock_joint_{uuid.uuid4().hex[:8]}",
            }, trace_id=trace_id)
        elif msg_type == "joint_state.stream":
            # 高频流：仅记录，不回 ack
            pass
        elif msg_type == "joint_command.ack":
            # Edge 对反向 joint_command.set 的回执：仅记录
            pass
        else:
            print(f"[mock-sim] recv (no reply): {msg_type}")

    async def _reply(
        self,
        ws: Any,
        msg_type: str,
        payload: Dict[str, Any],
        *,
        trace_id: Optional[str] = None,
    ) -> None:
        msg = build_envelope(msg_type, payload, self.session_id, need_ack=False, trace_id=trace_id)
        await ws.send(json.dumps(msg, ensure_ascii=False))

    def _broadcast(self, msg: Dict[str, Any]) -> None:
        loop = self._loop
        if loop is None:
            return

        async def _send_all() -> None:
            data = json.dumps(msg, ensure_ascii=False)
            for ws in list(self._clients):
                try:
                    await ws.send(data)
                except Exception:
                    self._clients.discard(ws)

        asyncio.run_coroutine_threadsafe(_send_all(), loop)


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock Isaac Sim WebSocket server (no Isaac dependency)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--path", default="/edge-sim/v1")
    parser.add_argument("--demo", action="store_true",
                        help="收到 session.start 后定时演示推送 collision.event / joint_command.set")
    args = parser.parse_args()

    server = MockSimServer(host=args.host, port=args.port, path=args.path)
    server.start()
    print(f"[mock-sim] ready: {server.endpoint}  (Ctrl+C to quit)")

    try:
        demo_sent = False
        while True:
            time.sleep(1.0)
            if args.demo and server.session_started.is_set() and not demo_sent:
                demo_sent = True
                print("[mock-sim] demo: push collision.event + joint_command.set")
                server.push_collision([
                    {"a_asset_id": "ur5_left/tool0", "b_asset_id": "material_tube_001"},
                ])
                server.push_joint_command(
                    device_id="ur5_left",
                    joint_names=["shoulder_pan_joint", "shoulder_lift_joint"],
                    target_positions_rad=[0.12, -1.30],
                )
    except KeyboardInterrupt:
        print("\n[mock-sim] shutting down")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
