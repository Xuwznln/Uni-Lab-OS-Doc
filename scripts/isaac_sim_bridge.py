"""
Isaac Sim 侧适配器：消费 Edge(OS) 下发的统一消息（spec 16/17），操作场景并回传 ack/碰撞。
Isaac Sim 作为 WebSocket 服务端，Edge 作为客户端。

直接用 Isaac Sim 自带 python 运行（不要用系统 Python）：
    # Linux（standalone 5.1.0 根目录）
    ./python.sh isaac_sim_bridge.py --host 127.0.0.1 --port 9000 --path /edge-sim/v1
    # Windows
    python.bat isaac_sim_bridge.py --host 127.0.0.1 --port 9000 --path /edge-sim/v1

依赖（用同一个解释器安装）：
    ./python.sh -m pip install websockets   # Linux
    python.bat -m pip install websockets    # Windows

协议见 product_designs/simulation_assets_and_embodied_AI/16-spec & 17-api。
Edge 侧实现见 unilabos/sim/isaac_gateway.py。
"""

import argparse
import asyncio
import json
import queue
import sys
import threading
import time
import uuid

# 重定向输出在 python.bat 下默认按块缓冲，会让 [bridge] 日志迟迟不显示；强制行缓冲。
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# ============================================================
# 0. 先启动 SimulationApp（必须在导入任何 isaacsim/omni 场景 API 之前）
# ============================================================
try:
    from isaacsim import SimulationApp            # Isaac Sim 4.5 / 5.x 规范写法
except Exception:
    try:
        from isaacsim.simulation_app import SimulationApp
    except Exception:
        from omni.isaac.kit import SimulationApp  # 旧版兜底

_args_early = argparse.ArgumentParser(add_help=False)
_args_early.add_argument("--host", default="0.0.0.0")
_args_early.add_argument("--port", type=int, default=9000)
_args_early.add_argument("--path", default="/edge-sim/v1")
_args_early.add_argument("--headless", action="store_true")
CLI_ARGS, _ = _args_early.parse_known_args()

simulation_app = SimulationApp({"headless": CLI_ARGS.headless})

# ---- SimulationApp 启动后才能导入以下模块 ----
import numpy as np
import omni.kit.commands
import omni.usd
from pxr import Gf, UsdPhysics, PhysicsSchemaTools, PhysxSchema  # noqa: F401

# World / 资产 / Articulation —— 做新旧命名空间兼容
try:
    from isaacsim.core.api import World
    from isaacsim.core.utils.stage import add_reference_to_stage
    from isaacsim.core.prims import SingleArticulation as _Articulation
    from isaacsim.core.prims import SingleXFormPrim as _XFormPrim
except Exception:  # 旧版 (<=4.2)
    from omni.isaac.core import World
    from omni.isaac.core.utils.stage import add_reference_to_stage
    from omni.isaac.core.articulations import Articulation as _Articulation
    from omni.isaac.core.prims import XFormPrim as _XFormPrim

from omni.physx import get_physx_simulation_interface

# standalone 模式下显式启用 URDF 导入扩展，避免首次执行导入命令时扩展未加载
try:
    from isaacsim.core.utils.extensions import enable_extension
except Exception:
    from omni.isaac.core.utils.extensions import enable_extension
# 5.1.0 内置 isaacsim.asset.importer.urdf；成功后立即 break，避免再尝试旧版
# omni.importer.urdf（本地不存在会触发 ~30s 在线 registry 同步并报错）。
for _ext in ("isaacsim.asset.importer.urdf", "omni.importer.urdf"):
    try:
        if enable_extension(_ext):
            print(f"[bridge] URDF importer extension enabled: {_ext}")
            break
    except Exception:
        pass

SPEC_VERSION = "1.0.0"


# ============================================================
# 1. 信封工具
# ============================================================
def build_envelope(msg_type, payload, session_id, *, target="edge",
                   need_ack=None, error=None, trace_id=None, sequence=None):
    msg = {
        "spec_version": SPEC_VERSION,
        "msg_id": str(uuid.uuid4()),
        "msg_type": msg_type,
        "timestamp_ms": int(time.time() * 1000),
        "session_id": session_id,
        "source": "sim",
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


def quat_xyzw_to_wxyz(o):
    # 协议用 xyzw，Isaac core set_world_pose 用 wxyz(scalar-first)
    return np.array([o.get("w", 1.0), o.get("x", 0.0), o.get("y", 0.0), o.get("z", 0.0)], dtype=float)


# ============================================================
# 2. 场景管理器（全部在主线程调用）
# ============================================================
class SceneManager:
    def __init__(self, outbound_q: "queue.Queue"):
        self.outbound_q = outbound_q
        self.world = None
        self.session_id = f"sim_sess_{int(time.time())}"
        self.world_ready = False
        # asset_id -> {"prim_path","kind","articulation","initialized"}
        self.assets = {}
        # 设备 device_id -> prim_path（joint_state 用）
        self.device_prim = {}
        # prim_path -> asset_id（碰撞回传时把裸 prim 路径映射回业务 asset_id）
        self.prim_to_asset = {}
        self._contact_sub = None

    # ---------- world.create ----------
    def handle_world_create(self, env):
        p = env.get("payload", {})
        physics_dt = float(p.get("physics_dt", 0.01))
        rendering_dt = float(p.get("rendering_dt", 0.016))
        if self.world is not None and p.get("reset_existing", True):
            self.world.clear()
        if self.world is None:
            self.world = World(stage_units_in_meters=float(p.get("units_in_meters", 1.0)),
                               physics_dt=physics_dt, rendering_dt=rendering_dt)
            self.world.scene.add_default_ground_plane()
        self.world.reset()
        self._subscribe_contacts()
        self.world_ready = True
        self._send("world.create.ack", {"world_name": p.get("world_name", ""), "success": True},
                   trace=env)

    # ---------- session.start ----------
    def handle_session_start(self, env):
        self._send("session.ready", {"mode": env.get("payload", {}).get("mode", "sim")}, trace=env)

    # ---------- asset.upsert ----------
    def handle_asset_upsert(self, env):
        p = env.get("payload", {})
        asset_id = p["asset_id"]
        prim_path = p["prim_path"]
        fmt = p.get("format", "usd")
        uri = p.get("source_uri", "")
        kind = p.get("asset_kind", "material")
        local = self._uri_to_local(uri)
        err = None
        try:
            if asset_id in self.assets and not p.get("replace_if_exists", True):
                pass
            else:
                if fmt == "urdf":
                    prim_path = self._import_urdf(local, prim_path)
                else:  # usd
                    add_reference_to_stage(usd_path=local, prim_path=prim_path)
                self._apply_pose(prim_path, p.get("pose", {}))
                self._enable_contact_report(prim_path)
                self.assets[asset_id] = {"prim_path": prim_path, "kind": kind,
                                         "articulation": None, "initialized": False}
                self.prim_to_asset[prim_path] = asset_id
                if kind == "device":
                    self.device_prim[p.get("metadata", {}).get("id", asset_id)] = prim_path
                    self.device_prim[asset_id] = prim_path
        except Exception as e:
            err = {"code": "ASSET_LOAD_FAILED", "message": str(e), "retryable": False}
        self._send("asset.upsert.ack",
                   {"asset_id": asset_id, "prim_path": prim_path, "success": err is None},
                   trace=env, error=err)

    # ---------- joint_state.stream ----------
    def handle_joint_state(self, env):
        if not self.world_ready:
            return
        p = env.get("payload", {})
        device_id = p.get("device_id")
        prim_path = self.device_prim.get(device_id)
        if prim_path is None:
            # 合并场景（full_dev 单 articulation）：device_id 未映射时回退到唯一设备
            devs = [a["prim_path"] for a in self.assets.values() if a.get("kind") == "device"]
            prim_path = devs[0] if len(devs) == 1 else None
        if prim_path is None:
            return
        art = self._get_articulation(prim_path)
        if art is None:
            return
        names = p.get("joint_names", [])
        positions = p.get("joint_positions_rad", [])
        if not names or not positions:
            return
        try:
            dof_names = list(art.dof_names)
            idx, vals = [], []
            for n, v in zip(names, positions):
                if n in dof_names:
                    idx.append(dof_names.index(n))
                    vals.append(float(v))
            if idx:
                # 直接 set 位置做可视化镜像（teleport）
                art.set_joint_positions(np.array(vals), joint_indices=np.array(idx))
        except Exception as e:
            print(f"[bridge] joint_state apply error: {e}")

    # ---------- attach.request ----------
    def handle_attach(self, env):
        p = env.get("payload", {})
        attachment_id = p["attachment_id"]
        child = self.assets.get(p["child_asset_id"])
        parent = self.assets.get(p["parent_asset_id"])
        err = None
        joint_path = None
        try:
            if child is None or parent is None:
                raise RuntimeError("child or parent asset not found")
            stage = omni.usd.get_context().get_stage()
            parent_link = self._resolve_link(parent["prim_path"], p["parent_link"])
            if not stage.GetPrimAtPath(parent_link).IsValid():
                err = {"code": "E_LINK_NOT_FOUND",
                       "message": f"parent_link '{p['parent_link']}' not found", "retryable": False}
            else:
                joint_path = f"{parent_link}/Attach_{self._safe(attachment_id)}"
                joint = UsdPhysics.FixedJoint.Define(stage, joint_path)
                joint.CreateBody0Rel().SetTargets([parent_link])
                joint.CreateBody1Rel().SetTargets([child["prim_path"]])
                rp = p.get("relative_pose", {})
                pos = rp.get("position_m", {})
                rot = rp.get("orientation_xyzw", {})
                joint.CreateLocalPos1Attr().Set(
                    Gf.Vec3f(float(pos.get("x", 0)), float(pos.get("y", 0)), float(pos.get("z", 0))))
                joint.CreateLocalRot1Attr().Set(
                    Gf.Quatf(float(rot.get("w", 1)), float(rot.get("x", 0)),
                             float(rot.get("y", 0)), float(rot.get("z", 0))))
        except Exception as e:
            err = {"code": "E_CONSTRAINT_FAILED", "message": str(e), "retryable": True}
        self._send("attach.ack",
                   {"attachment_id": attachment_id,
                    "status": "ok" if err is None else "failed",
                    "sim_constraint_handle": joint_path},
                   trace=env, error=err)

    # ---------- joint_command.set（可选：Sim 反向驱动 Edge）----------
    def send_joint_command(self, device_id, joint_names, target_positions_rad,
                           control_mode="position", **kw):
        payload = {
            "command_id": f"cmd_{device_id}_{uuid.uuid4().hex[:8]}",
            "device_id": device_id,
            "control_mode": control_mode,
            "joint_names": joint_names,
            "target_positions_rad": target_positions_rad,
            "command_timestamp_ms": int(time.time() * 1000),
        }
        payload.update(kw)
        self._send("joint_command.set", payload, need_ack=True)

    # ---------- 碰撞订阅 ----------
    def _subscribe_contacts(self):
        if self._contact_sub is not None:
            return
        self._contact_sub = get_physx_simulation_interface().subscribe_contact_report_events(
            self._on_contact)

    def _on_contact(self, contact_headers, contact_data):
        pairs = []
        for ch in contact_headers:
            try:
                a = str(PhysicsSchemaTools.intToSdfPath(ch.actor0))
                b = str(PhysicsSchemaTools.intToSdfPath(ch.actor1))
            except Exception:
                continue
            pairs.append({
                "a_asset_id": self._prim_to_asset_id(a),
                "b_asset_id": self._prim_to_asset_id(b),
            })
        if not pairs:
            return
        payload = {
            "event_id": f"col_{uuid.uuid4().hex[:8]}",
            "severity": "warn",
            "sim_time_s": float(self.world.current_time) if self.world else 0.0,
            "pairs": pairs,
        }
        self.outbound_q.put(build_envelope("collision.event", payload, self.session_id, need_ack=False))

    def _prim_to_asset_id(self, prim_path):
        # 精确命中优先；否则按前缀匹配（link 路径以资产 prim_path 开头）
        if prim_path in self.prim_to_asset:
            return self.prim_to_asset[prim_path]
        for root, asset_id in self.prim_to_asset.items():
            if prim_path == root or prim_path.startswith(root + "/"):
                return asset_id
        return prim_path

    # ---------- 工具 ----------
    def _send(self, msg_type, payload, *, trace=None, need_ack=None, error=None):
        trace_id = (trace or {}).get("trace_id")
        self.outbound_q.put(build_envelope(msg_type, payload, self.session_id,
                                            need_ack=need_ack, error=error, trace_id=trace_id))

    def _import_urdf(self, urdf_path, dest_prim):
        status, import_config = omni.kit.commands.execute("URDFCreateImportConfig")
        import_config.merge_fixed_joints = False
        import_config.fix_base = True
        import_config.make_default_prim = False
        status, prim_path = omni.kit.commands.execute(
            "URDFParseAndImportFile", urdf_path=urdf_path, import_config=import_config)
        return prim_path or dest_prim

    def _apply_pose(self, prim_path, pose):
        pos = pose.get("position_m", {})
        rot = pose.get("orientation_xyzw", {"w": 1.0})
        xf = _XFormPrim(prim_path)
        xf.set_world_pose(
            position=np.array([float(pos.get("x", 0)), float(pos.get("y", 0)), float(pos.get("z", 0))]),
            orientation=quat_xyzw_to_wxyz(rot))

    def _enable_contact_report(self, prim_path):
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(prim_path)
        if prim and prim.IsValid():
            try:
                PhysxSchema.PhysxContactReportAPI.Apply(prim)
            except Exception:
                pass

    def _get_articulation(self, prim_path):
        for a in self.assets.values():
            if a["prim_path"] == prim_path:
                if a["articulation"] is None:
                    try:
                        art = _Articulation(prim_path)
                        art.initialize()
                        a["articulation"] = art
                    except Exception:
                        return None
                return a["articulation"]
        return None

    def _resolve_link(self, root_prim, link_name):
        # 优先 root/link_name，找不到则在子树中搜索同名 prim
        candidate = f"{root_prim}/{link_name}"
        stage = omni.usd.get_context().get_stage()
        if stage.GetPrimAtPath(candidate).IsValid():
            return candidate
        for prim in stage.Traverse():
            if prim.GetName() == link_name and str(prim.GetPath()).startswith(root_prim):
                return str(prim.GetPath())
        return candidate

    @staticmethod
    def _uri_to_local(uri):
        if uri.startswith("file://"):
            path = uri[len("file://"):]
            # Windows: file:///C:/...  ->  C:/...
            if len(path) > 2 and path[0] == "/" and path[2] == ":":
                path = path[1:]
            return path
        return uri  # 本地路径 / Nucleus omniverse:// 路径直接交给导入器

    @staticmethod
    def _safe(name):
        return "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in str(name))


# ============================================================
# 3. WebSocket 服务端（独立线程 asyncio）
# ============================================================
class WsServer:
    def __init__(self, host, port, path, inbound_q, outbound_q):
        self.host, self.port, self.path = host, port, path
        self.inbound_q, self.outbound_q = inbound_q, outbound_q
        self.clients = set()
        self.loop = None

    def start(self):
        t = threading.Thread(target=self._run, name="edge_ws_server", daemon=True)
        t.start()

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._serve())

    async def _serve(self):
        async def handler(ws):
            self.clients.add(ws)
            print(f"[bridge] edge connected: {ws.remote_address}")
            try:
                async for raw in ws:
                    try:
                        self.inbound_q.put(json.loads(raw))
                    except Exception as e:
                        print(f"[bridge] bad message: {e}")
            finally:
                self.clients.discard(ws)
                print("[bridge] edge disconnected")

        async with __import__("websockets").serve(handler, self.host, self.port):
            print(f"[bridge] listening ws://{self.host}:{self.port}{self.path}")
            asyncio.create_task(self._pump_outbound())
            await asyncio.Future()  # run forever

    async def _pump_outbound(self):
        while True:
            try:
                while not self.outbound_q.empty():
                    msg = self.outbound_q.get_nowait()
                    data = json.dumps(msg, ensure_ascii=False)
                    dead = []
                    for ws in list(self.clients):
                        try:
                            await ws.send(data)
                        except Exception:
                            dead.append(ws)
                    for ws in dead:
                        self.clients.discard(ws)
            except Exception as e:
                print(f"[bridge] outbound error: {e}")
            await asyncio.sleep(0.005)


# ============================================================
# 4. 主循环
# ============================================================
DISPATCH = {
    "hello": "handle_hello",
    "world.create": "handle_world_create",
    "session.start": "handle_session_start",
    "asset.upsert": "handle_asset_upsert",
    "joint_state.stream": "handle_joint_state",
    "attach.request": "handle_attach",
}


def main():
    inbound_q, outbound_q = queue.Queue(), queue.Queue()
    scene = SceneManager(outbound_q)

    # hello 直接在主循环里处理（回 capabilities）
    def handle_hello(env):
        outbound_q.put(build_envelope(
            "hello.ack",
            {"capabilities": ["world", "asset", "attach", "joint", "collision", "joint_command"],
             "formats": ["urdf", "usd"], "max_joint_hz": 200},
            scene.session_id, trace_id=env.get("trace_id")))
    scene.handle_hello = handle_hello

    ws = WsServer(CLI_ARGS.host, CLI_ARGS.port, CLI_ARGS.path, inbound_q, outbound_q)
    ws.start()

    print("[bridge] simulation loop started")
    while simulation_app.is_running():
        # 处理本帧到达的所有 Edge 消息（主线程操作场景）
        for _ in range(256):
            if inbound_q.empty():
                break
            env = inbound_q.get_nowait()
            mt = env.get("msg_type", "")
            fn = DISPATCH.get(mt)
            if fn and hasattr(scene, fn):
                try:
                    getattr(scene, fn)(env)
                except Exception as e:
                    print(f"[bridge] handler {mt} error: {e}")
            else:
                print(f"[bridge] ignore msg_type: {mt}")

        # 推进世界
        if scene.world is not None:
            scene.world.step(render=not CLI_ARGS.headless)
        else:
            simulation_app.update()

    simulation_app.close()


if __name__ == "__main__":
    main()
