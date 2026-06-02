from __future__ import annotations

import asyncio
import json
import math
import threading
import time
from typing import Any, Callable, Dict, Optional, List, Tuple

import websockets

from unilabos.config.config import SimGatewayConfig
from unilabos.sim.isaac_protocol import build_envelope
from unilabos.utils import logger


_ACTIVE_GATEWAY: "Optional[IsaacSimGateway]" = None


def get_active_gateway() -> "Optional[IsaacSimGateway]":
    """Return the running gateway singleton, or None if Sim integration is off."""
    return _ACTIVE_GATEWAY


def set_active_gateway(gateway: "Optional[IsaacSimGateway]") -> None:
    global _ACTIVE_GATEWAY
    _ACTIVE_GATEWAY = gateway


class IsaacSimGateway:
    """Edge startup gateway for Isaac Sim unified WebSocket protocol."""

    def __init__(
        self,
        *,
        endpoint: str,
        auth_token: str = "",
        target: str = "isaac-sim-main",
        world_name: str = "lab_world_01",
        reconnect_backoff_ms: int = 1000,
        heartbeat_interval_ms: int = 5000,
        auto_bootstrap: bool = True,
        asset_uri_key_order: str = "source_uri,uri,url,path,asset",
        asset_uri_fallback_prefix: str = "unilab://class/",
    ) -> None:
        self.endpoint = endpoint
        self.auth_token = auth_token
        self.target = target
        self.world_name = world_name
        self.reconnect_backoff_ms = reconnect_backoff_ms
        self.heartbeat_interval_ms = heartbeat_interval_ms
        self.auto_bootstrap = auto_bootstrap
        self.asset_uri_key_order = asset_uri_key_order
        self.asset_uri_fallback_prefix = asset_uri_fallback_prefix

        self._session_id = f"sess_{int(time.time())}"
        self._seq = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._outbound_q: Optional[asyncio.Queue[Dict[str, Any]]] = None
        self._ws = None
        self._world_ready = False
        self._world_ready_event = threading.Event()
        self._pending_messages: List[Tuple[str, Dict[str, Any], Optional[bool]]] = []
        self._pending_lock = threading.Lock()

        # D: collision.event subscribers (called from the gateway loop thread).
        self._collision_handlers: List[Callable[[Dict[str, Any]], None]] = []
        # E: joint_command.set handlers (Sim -> Edge reverse joint control).
        # Handler receives payload dict and returns an optional ack payload dict
        # (e.g. {"status": "accepted"} or {"status": "rejected", "error": {...}}).
        self._joint_command_handlers: List[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = []
        # C: attach.ack waiters keyed by attachment_id.
        self._attach_lock = threading.Lock()
        self._attach_events: Dict[str, threading.Event] = {}
        self._attach_results: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def from_config(cls) -> "IsaacSimGateway":
        return cls(
            endpoint=SimGatewayConfig.endpoint,
            auth_token=SimGatewayConfig.auth_token,
            target=SimGatewayConfig.target,
            world_name=SimGatewayConfig.world_name,
            reconnect_backoff_ms=SimGatewayConfig.reconnect_backoff_ms,
            heartbeat_interval_ms=SimGatewayConfig.heartbeat_interval_ms,
            auto_bootstrap=SimGatewayConfig.auto_bootstrap,
            asset_uri_key_order=SimGatewayConfig.asset_uri_key_order,
            asset_uri_fallback_prefix=SimGatewayConfig.asset_uri_fallback_prefix,
        )

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, name="isaac_sim_gateway", daemon=True)
        self._thread.start()
        set_active_gateway(self)
        logger.info(f"[IsaacGateway] started, endpoint={self.endpoint}")

    def stop(self) -> None:
        self._running = False
        if self._loop is not None:
            self._loop.call_soon_threadsafe(lambda: None)
        if get_active_gateway() is self:
            set_active_gateway(None)
        logger.info("[IsaacGateway] stop requested")

    def wait_world_ready(self, timeout: float = 10.0) -> bool:
        """Block until world.create.ack is received (world ready), or timeout.

        Returns True if world is ready, False on timeout. Useful for callers that
        want to cleanly block before sending asset.upsert / attach.request, instead
        of relying on the internal pending-queue gating.
        """
        return self._world_ready_event.wait(timeout=timeout)

    def add_collision_handler(self, handler: Callable[[Dict[str, Any]], None]) -> None:
        """Register a collision.event subscriber. Handler runs on the gateway loop thread."""
        self._collision_handlers.append(handler)

    def add_joint_command_handler(
        self, handler: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]
    ) -> None:
        """Register a joint_command.set handler (Sim -> Edge reverse joint control).

        Handler receives the command payload and should validate joint limits then
        drive the device. Return an ack payload dict, e.g.
        ``{"status": "accepted"}`` or ``{"status": "rejected", "error": {...}}``.
        Returning None is treated as accepted. The handler runs on the gateway
        loop thread, so keep it non-blocking (dispatch heavy work elsewhere).

        Control implementation is decoupled from MoveIt / any motion-planning
        stack: the handler may publish to a joint controller topic, write a
        servo/driver register, set a fixed joint, or call a vendor SDK. Devices
        without MoveIt are fully supported; whether to do trajectory planning is
        entirely up to the handler.
        """
        self._joint_command_handlers.append(handler)

    def publish_joint_state(
        self,
        *,
        device_id: str,
        base_frame: str,
        joint_names: list[str],
        joint_positions_rad: list[float],
        joint_velocities: Optional[list[float]] = None,
        joint_efforts: Optional[list[float]] = None,
        state_timestamp_ms: Optional[int] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "device_id": device_id,
            "base_frame": base_frame,
            "joint_names": joint_names,
            "joint_positions_rad": joint_positions_rad,
            "state_timestamp_ms": state_timestamp_ms or int(time.time() * 1000),
        }
        if joint_velocities is not None:
            payload["joint_velocities"] = joint_velocities
        if joint_efforts is not None:
            payload["joint_efforts"] = joint_efforts
        # High-frequency best-effort stream: drop when world not ready, never block sender.
        self._enqueue_sync(
            "joint_state.stream",
            payload,
            need_ack=False,
            drop_if_not_ready=True,
            wait_result=False,
        )

    def request_attach(
        self,
        *,
        attachment_id: str,
        child_asset_id: str,
        parent_asset_id: str,
        parent_link: str,
        relative_pose: Dict[str, Any],
        constraint: Optional[Dict[str, Any]] = None,
        wait_ack: bool = False,
        timeout_s: float = 5.0,
    ) -> Optional[Dict[str, Any]]:
        """Send attach.request. When wait_ack is True, block until attach.ack or timeout.

        Returns the attach.ack payload when wait_ack is True (or None on timeout);
        returns None immediately in fire-and-forget mode.

        Attach does NOT depend on MoveIt or any motion-planning stack: it only asks
        Sim to create a constraint on ``parent_link``. ``parent_link`` just needs to
        exist in the asset model (URDF/USD); the link may be driven by a plain
        controller, a fixed joint, manual teaching, or be static. Any business
        event (grasp, gripper close, workflow step, manual command) may call this.
        """
        payload: Dict[str, Any] = {
            "attachment_id": attachment_id,
            "child_asset_id": child_asset_id,
            "parent_asset_id": parent_asset_id,
            "parent_link": parent_link,
            "relative_pose": relative_pose,
        }
        if constraint is not None:
            payload["constraint"] = constraint

        if not wait_ack:
            self._enqueue_sync("attach.request", payload, need_ack=True)
            return None

        event = threading.Event()
        with self._attach_lock:
            self._attach_events[attachment_id] = event
            self._attach_results.pop(attachment_id, None)
        self._enqueue_sync("attach.request", payload, need_ack=True)
        got = event.wait(timeout=timeout_s)
        with self._attach_lock:
            self._attach_events.pop(attachment_id, None)
            result = self._attach_results.pop(attachment_id, None)
        if not got:
            logger.warning(f"[IsaacGateway] attach.ack timeout: {attachment_id}")
            return None
        return result

    def upsert_asset(self, payload: Dict[str, Any]) -> None:
        self._enqueue_sync("asset.upsert", payload, need_ack=True)

    def upsert_scene_urdf(
        self,
        urdf_str: str,
        *,
        asset_id: str = "full_dev",
        prim_path: str = "/World/full_dev",
    ) -> Optional[str]:
        """Write the whole-scene URDF (from ResourceVisualization) to a file and send it
        as a single device asset.upsert via file:// URI.

        ResourceVisualization 已把全场景设备 xacro 展开成一个 ``full_dev`` 机器人，关节名带
        ``<node_id>_`` 前缀、位姿烘进 base joint、mesh 走绝对本地路径。这里直接落文件并用
        file:// URI 复用现有 asset.upsert（sim 侧 URDFParseAndImportFile 已支持），不改协议。

        Returns the written file path (as file:// URI) for logging, or None if urdf_str
        is empty. 受 world ready 门控（经 upsert_asset -> _enqueue_sync）。
        """
        import re
        import shutil
        import hashlib
        import tempfile
        import pathlib

        if not isinstance(urdf_str, str) or not urdf_str.strip():
            logger.warning("[IsaacGateway] upsert_scene_urdf skipped: empty urdf_str")
            return None

        # Isaac 的 URDF importer 会用 mesh 文件名(去扩展名)作为 USD prim 子节点名，而 USD prim
        # 路径段不允许 '-'/空格/'.' 等字符，否则生成非法/空路径导致 "Used null prim" 整单导入失败。
        # 这里把含非法字符的 mesh 复制到临时目录并改成合法名，再改写 filename；并统一反斜杠->正斜杠。
        mesh_dir = pathlib.Path(tempfile.gettempdir()) / "unilab_sim_meshes"
        mesh_dir.mkdir(parents=True, exist_ok=True)
        _illegal = re.compile(r"[^A-Za-z0-9_]")
        _used: Dict[str, str] = {}
        _destnames: Dict[str, str] = {}
        _stats = {"total": 0, "sanitized": 0, "missing": 0}

        def _to_local(p: str) -> str:
            q = p.replace("\\", "/")
            if q.startswith("file://"):
                q = q[len("file://"):]
            if re.match(r"^/[A-Za-z]:/", q):  # file:///C:/... -> C:/...
                q = q[1:]
            return q

        def _rewrite(m: "re.Match") -> str:
            local = _to_local(m.group(1))
            _stats["total"] += 1
            src = pathlib.Path(local)
            stem = src.stem
            if not _illegal.search(stem):
                return 'filename="file://' + local + '"'
            key = str(src)
            if key in _used:
                return 'filename="file://' + _used[key] + '"'
            safe_stem = _illegal.sub("_", stem)
            dest_name = safe_stem + src.suffix
            if dest_name in _destnames and _destnames[dest_name] != key:
                dest_name = f"{safe_stem}_{hashlib.md5(key.encode()).hexdigest()[:6]}{src.suffix}"
            _destnames[dest_name] = key
            dest_path = mesh_dir / dest_name
            try:
                if src.exists():
                    shutil.copyfile(src, dest_path)
                else:
                    _stats["missing"] += 1
            except Exception as cp_err:
                logger.warning(f"[IsaacGateway] mesh copy failed {src} -> {dest_path}: {cp_err}")
            dest = str(dest_path).replace("\\", "/")
            _used[key] = dest
            _stats["sanitized"] += 1
            return 'filename="file://' + dest + '"'

        normalized = re.sub(r'filename="file://([^"]+)"', _rewrite, urdf_str)
        if _stats["sanitized"]:
            logger.info(
                f"[IsaacGateway] scene urdf mesh sanitized: {_stats['sanitized']}/{_stats['total']} "
                f"(missing src: {_stats['missing']}) -> {mesh_dir}"
            )

        # 稳定临时文件，重跑覆盖；mesh 是绝对路径，文件位置不影响解析。
        path = pathlib.Path(tempfile.gettempdir()) / "unilab_sim_scene.urdf"
        path.write_text(normalized, encoding="utf-8")
        uri = path.as_uri()

        payload: Dict[str, Any] = {
            "asset_id": asset_id,
            "asset_kind": "device",
            "format": "urdf",
            "source_uri": uri,
            "prim_path": prim_path,
            "pose": {
                "frame_id": "world",
                "position_m": {"x": 0.0, "y": 0.0, "z": 0.0},
                "orientation_xyzw": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            },
            "metadata": {"id": asset_id},
            "replace_if_exists": True,
        }
        self.upsert_asset(payload)
        logger.info(f"[IsaacGateway] scene urdf upsert queued: {uri}")
        return uri

    def sync_from_resource_tree_set(self, resource_tree_set: Any) -> int:
        """Best-effort bootstrap: convert ResourceTreeSet nodes to asset.upsert messages."""
        nodes = getattr(resource_tree_set, "all_nodes", None)
        if not isinstance(nodes, list):
            logger.warning("[IsaacGateway] sync skipped: resource_tree_set has no all_nodes list")
            return 0

        sent = 0
        for node in nodes:
            res = getattr(node, "res_content", None)
            if res is None:
                continue
            payload = self._resource_to_asset_payload(res)
            if payload is None:
                continue
            self.upsert_asset(payload)
            sent += 1
        logger.info(f"[IsaacGateway] bootstrap asset.upsert sent: {sent}")
        return sent

    def _resource_to_asset_payload(self, res: Any) -> Optional[Dict[str, Any]]:
        asset_id = getattr(res, "uuid", None) or getattr(res, "id", None)
        if not isinstance(asset_id, str) or not asset_id:
            return None

        klass = getattr(res, "klass", "")
        typ = getattr(res, "type", "")
        model = getattr(res, "model", {}) or {}
        if not isinstance(model, dict):
            model = {}

        source_uri = (
            self._resolve_source_uri(model)
            or ""
        )
        if not source_uri:
            # Keep skeleton usable even before registry model paths are normalized.
            source_uri = f"{self.asset_uri_fallback_prefix}{klass or 'unknown'}"

        fmt = model.get("format") or self._guess_format_from_uri(str(source_uri))
        name = getattr(res, "name", str(asset_id))
        prim_path = f"/World/{'Devices' if typ == 'device' else 'Materials'}/{self._sanitize_name(name)}"

        pose = getattr(res, "pose", None)
        pos = getattr(pose, "position", None)
        rot = getattr(pose, "rotation", None)
        px = float(getattr(pos, "x", 0.0) or 0.0)
        py = float(getattr(pos, "y", 0.0) or 0.0)
        pz = float(getattr(pos, "z", 0.0) or 0.0)
        rx = float(getattr(rot, "x", 0.0) or 0.0)
        ry = float(getattr(rot, "y", 0.0) or 0.0)
        rz = float(getattr(rot, "z", 0.0) or 0.0)
        qx, qy, qz, qw = self._euler_deg_to_quat(rx, ry, rz)

        return {
            "asset_id": str(asset_id),
            "asset_kind": "device" if typ == "device" else "material",
            "format": fmt,
            "source_uri": str(source_uri),
            "prim_path": prim_path,
            "pose": {
                "frame_id": "world",
                "position_m": {"x": px, "y": py, "z": pz},
                "orientation_xyzw": {"x": qx, "y": qy, "z": qz, "w": qw},
            },
            "metadata": {
                "id": getattr(res, "id", ""),
                "klass": klass,
                "type": typ,
            },
            "replace_if_exists": True,
        }

    @staticmethod
    def _guess_format_from_uri(uri: str) -> str:
        u = uri.lower()
        if u.endswith(".usd") or u.endswith(".usda") or u.endswith(".usdc"):
            return "usd"
        return "urdf"

    @staticmethod
    def _sanitize_name(name: str) -> str:
        return "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in name)

    def _resolve_source_uri(self, model: Dict[str, Any]) -> str:
        keys = [k.strip() for k in self.asset_uri_key_order.split(",") if k.strip()]
        for k in keys:
            value = model.get(k)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _euler_deg_to_quat(rx_deg: float, ry_deg: float, rz_deg: float) -> tuple[float, float, float, float]:
        # Resource pose.rotation follows frontend convention (degrees).
        rx = math.radians(rx_deg)
        ry = math.radians(ry_deg)
        rz = math.radians(rz_deg)
        cx, sx = math.cos(rx * 0.5), math.sin(rx * 0.5)
        cy, sy = math.cos(ry * 0.5), math.sin(ry * 0.5)
        cz, sz = math.cos(rz * 0.5), math.sin(rz * 0.5)
        qw = cx * cy * cz + sx * sy * sz
        qx = sx * cy * cz - cx * sy * sz
        qy = cx * sy * cz + sx * cy * sz
        qz = cx * cy * sz - sx * sy * cz
        return qx, qy, qz, qw

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._outbound_q = asyncio.Queue()
        self._loop.run_until_complete(self._connection_supervisor())

    async def _connection_supervisor(self) -> None:
        while self._running:
            try:
                connect_kwargs: Dict[str, Any] = {
                    "ping_interval": max(1, self.heartbeat_interval_ms // 1000),
                }
                if self.auth_token:
                    # websockets>=14 renamed extra_headers -> additional_headers.
                    connect_kwargs["additional_headers"] = {
                        "Authorization": f"Bearer {self.auth_token}"
                    }
                async with websockets.connect(self.endpoint, **connect_kwargs) as ws:
                    self._ws = ws
                    self._world_ready = False
                    self._world_ready_event.clear()
                    logger.info("[IsaacGateway] connected")
                    if self.auto_bootstrap:
                        await self._bootstrap()
                    sender_task = asyncio.create_task(self._sender_loop())
                    recv_task = asyncio.create_task(self._receiver_loop())
                    done, pending = await asyncio.wait(
                        {sender_task, recv_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    for task in done:
                        task.result()
            except Exception as exc:
                logger.warning(f"[IsaacGateway] connection error: {exc}")
            finally:
                self._ws = None

            if self._running:
                await asyncio.sleep(max(0.2, self.reconnect_backoff_ms / 1000.0))

    async def _bootstrap(self) -> None:
        await self._enqueue("hello", {"agent": "unilab-edge", "capabilities": ["world", "asset", "attach", "joint"]}, True)
        await self._enqueue(
            "world.create",
            {
                "world_name": self.world_name,
                "up_axis": "Z",
                "units_in_meters": 1.0,
                "physics_dt": 0.01,
                "rendering_dt": 0.016,
                "reset_existing": True,
            },
            True,
        )

    async def _sender_loop(self) -> None:
        assert self._outbound_q is not None
        while self._running and self._ws is not None:
            msg = await self._outbound_q.get()
            await self._ws.send(json.dumps(msg, ensure_ascii=False))

    async def _receiver_loop(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            try:
                data = json.loads(raw)
            except Exception:
                logger.warning(f"[IsaacGateway] received non-json message: {raw!r}")
                continue
            msg_type = data.get("msg_type", "")
            if msg_type == "collision.event":
                self._handle_collision_event(data.get("payload") or {})
            elif msg_type == "joint_command.set":
                await self._handle_joint_command(data)
            elif msg_type == "attach.ack":
                self._handle_attach_ack(data.get("payload") or {})
            elif msg_type == "world.create.ack":
                self._world_ready = True
                self._world_ready_event.set()
                logger.info("[IsaacGateway] world.create.ack received, sending session.start")
                await self._enqueue("session.start", {"mode": "sim"}, True)
                logger.info("[IsaacGateway] draining pending messages after world ready")
                await self._drain_pending_messages()
            elif msg_type.endswith(".ack"):
                logger.info(f"[IsaacGateway] ack received: {msg_type}")
            else:
                logger.debug(f"[IsaacGateway] recv: {msg_type}")

    def _handle_collision_event(self, payload: Dict[str, Any]) -> None:
        logger.warning(f"[IsaacGateway] collision.event: {payload}")
        for handler in list(self._collision_handlers):
            try:
                handler(payload)
            except Exception as exc:
                logger.warning(f"[IsaacGateway] collision handler error: {exc}")

    async def _handle_joint_command(self, data: Dict[str, Any]) -> None:
        """Link E: Sim -> Edge reverse joint control. Dispatch to handlers, reply ack."""
        payload = data.get("payload") or {}
        command_id = payload.get("command_id")
        logger.info(f"[IsaacGateway] joint_command.set: {command_id} device={payload.get('device_id')}")

        ack_payload: Dict[str, Any] = {"command_id": command_id}
        ack_error: Optional[Dict[str, Any]] = None

        if not self._joint_command_handlers:
            ack_payload["status"] = "rejected"
            ack_error = {
                "code": "E_DEVICE_NOT_CONTROLLABLE",
                "message": "no joint_command handler registered on edge",
                "retryable": False,
            }
        else:
            try:
                result: Optional[Dict[str, Any]] = None
                for handler in list(self._joint_command_handlers):
                    handler_result = handler(payload)
                    if isinstance(handler_result, dict):
                        result = handler_result
                if isinstance(result, dict):
                    ack_error = result.pop("error", None)
                    ack_payload.update(result)
                    ack_payload.setdefault("status", "accepted")
                else:
                    ack_payload["status"] = "accepted"
            except Exception as exc:
                logger.warning(f"[IsaacGateway] joint_command handler error: {exc}")
                ack_payload["status"] = "rejected"
                ack_error = {"code": "E_INTERNAL", "message": str(exc), "retryable": True}

        if data.get("need_ack", True):
            await self._enqueue("joint_command.ack", ack_payload, need_ack=False, error=ack_error)

    def _handle_attach_ack(self, payload: Dict[str, Any]) -> None:
        attachment_id = payload.get("attachment_id")
        logger.info(f"[IsaacGateway] attach.ack: {payload}")
        if not isinstance(attachment_id, str):
            return
        with self._attach_lock:
            self._attach_results[attachment_id] = payload
            event = self._attach_events.get(attachment_id)
        if event is not None:
            event.set()

    async def _enqueue(
        self,
        msg_type: str,
        payload: Dict[str, Any],
        need_ack: Optional[bool],
        error: Optional[Dict[str, Any]] = None,
    ) -> None:
        assert self._outbound_q is not None
        self._seq += 1
        msg = build_envelope(
            msg_type=msg_type,
            payload=payload,
            session_id=self._session_id,
            source="edge",
            target=self.target,
            need_ack=need_ack,
            sequence=self._seq,
            error=error,
        )
        await self._outbound_q.put(msg)

    def _enqueue_sync(
        self,
        msg_type: str,
        payload: Dict[str, Any],
        need_ack: Optional[bool],
        *,
        drop_if_not_ready: bool = False,
        wait_result: bool = True,
    ) -> None:
        if self._loop is None or self._outbound_q is None:
            if drop_if_not_ready:
                return
            self._push_pending(msg_type, payload, need_ack)
            logger.debug(f"[IsaacGateway] queued before loop ready: {msg_type}")
            return
        if self._requires_world_ready(msg_type) and not self._world_ready:
            if drop_if_not_ready:
                # Best-effort streams (joint_state) are dropped, never buffered.
                return
            self._push_pending(msg_type, payload, need_ack)
            logger.debug(f"[IsaacGateway] queued before world ready: {msg_type}")
            return
        fut = asyncio.run_coroutine_threadsafe(self._enqueue(msg_type, payload, need_ack), self._loop)
        if not wait_result:
            return
        try:
            fut.result(timeout=0.3)
        except Exception:
            # Non-blocking best effort: ignore queue timeout errors.
            pass

    @staticmethod
    def _requires_world_ready(msg_type: str) -> bool:
        return msg_type in {"asset.upsert", "joint_state.stream", "attach.request"}

    def _push_pending(self, msg_type: str, payload: Dict[str, Any], need_ack: Optional[bool]) -> None:
        with self._pending_lock:
            self._pending_messages.append((msg_type, payload, need_ack))

    async def _drain_pending_messages(self) -> None:
        if self._outbound_q is None:
            return
        while True:
            with self._pending_lock:
                if not self._pending_messages:
                    return
                msg_type, payload, need_ack = self._pending_messages[0]
                if self._requires_world_ready(msg_type) and not self._world_ready:
                    return
                self._pending_messages.pop(0)
            await self._enqueue(msg_type, payload, need_ack)

