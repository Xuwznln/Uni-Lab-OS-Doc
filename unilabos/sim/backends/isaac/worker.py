from __future__ import annotations

import argparse
import base64
import queue
import struct
import threading
import zlib
from dataclasses import dataclass, field
from typing import Any

from unilabos.sim.backends.isaac.worker_http import ThreadingHTTPServer, make_handler


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(tag)
    checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", checksum)


def encode_png_rgb(image: Any) -> bytes:
    import numpy as np

    array = np.asarray(image)
    if array.ndim == 2:
        array = np.repeat(array[:, :, None], 3, axis=2)
    if array.ndim != 3 or array.shape[2] < 3:
        raise ValueError(f"RGB image must have shape HxWxC with C>=3, got {array.shape}")
    if array.dtype != np.uint8:
        if np.issubdtype(array.dtype, np.floating) and float(np.nanmax(array)) <= 1.0:
            array = array * 255.0
        array = np.clip(array, 0, 255).astype(np.uint8)
    channels = 4 if array.shape[2] >= 4 else 3
    color_type = 6 if channels == 4 else 2
    array = np.ascontiguousarray(array[:, :, :channels])
    height, width = int(array.shape[0]), int(array.shape[1])
    raw = b"".join(b"\x00" + array[row].tobytes() for row in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Uni-Lab-OS Isaac physics worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--scene", default=None)
    parser.add_argument("--robot-prim", default=None)
    parser.add_argument("--camera", default="/World/Camera")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--warmup-steps", type=int, default=2)
    parser.add_argument("--rpc-timeout-s", type=float, default=600.0)
    return parser.parse_args(argv)


@dataclass
class _WorkerJob:
    op: str
    args: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None


class IsaacWorkerState:
    def __init__(
        self,
        controller: Any,
        *,
        dispatch_on_main_thread: bool = False,
        rpc_timeout_s: float = 600.0,
    ):
        self.controller = controller
        self.rpc_timeout_s = float(rpc_timeout_s)
        self._jobs: queue.Queue[_WorkerJob] | None = queue.Queue() if dispatch_on_main_thread else None

    def health(self) -> dict[str, Any]:
        pending = self._jobs.qsize() if self._jobs is not None else 0
        return {"ok": True, "backend": "isaac", "controller": type(self.controller).__name__, "pending": pending}

    def dispatch(self, op: str, args: dict[str, Any]) -> Any:
        if self._jobs is None:
            return self._dispatch_direct(op, args)

        job = _WorkerJob(op=op, args=dict(args))
        self._jobs.put(job)
        if not job.event.wait(self.rpc_timeout_s):
            raise TimeoutError(f"Isaac worker op timed out waiting for main thread: {op}")
        if job.error is not None:
            raise job.error
        return job.result

    def process_next(self, timeout: float = 0.05) -> bool:
        if self._jobs is None:
            return False
        try:
            job = self._jobs.get(timeout=timeout)
        except queue.Empty:
            return False
        try:
            job.result = self._dispatch_direct(job.op, job.args)
        except BaseException as exc:
            job.error = exc
        finally:
            job.event.set()
            self._jobs.task_done()
        return True

    def _dispatch_direct(self, op: str, args: dict[str, Any]) -> Any:
        if op == "reset":
            return self.controller.reset()
        if op == "step":
            return self.controller.step(float(args.get("dt", 0.0)))
        if op == "load_scene":
            return self.controller.load_scene(str(args["scene_path"]))
        if op == "get_observation":
            return self.controller.get_observation(str(args["entity_id"]))
        if op == "set_command":
            return self.controller.set_command(str(args["entity_id"]), dict(args.get("command") or {}))
        if op == "attach_rigid_body":
            return self.controller.attach_rigid_body(
                str(args["name"]),
                str(args["asset_path"]),
                dict(args.get("pose") or {}),
            )
        if op == "get_joint_states":
            return self.controller.get_joint_states(str(args["body_id"]))
        if op == "apply_wrench":
            return self.controller.apply_wrench(str(args["body_id"]), dict(args.get("wrench") or {}))
        if op == "render":
            image = self.controller.render(str(args["camera"]), int(args["width"]), int(args["height"]))
            return {"encoding": "base64", "data": base64.b64encode(image).decode("ascii")}
        raise ValueError(f"Unsupported Isaac worker op: {op}")


class IsaacController:
    def __init__(self, headless: bool, camera: str, robot_prim: str | None, warmup_steps: int = 2):
        from isaacsim import SimulationApp

        self.app = SimulationApp({"headless": bool(headless)})
        self.camera = camera
        self.robot_prim = robot_prim
        self.scene_path: str | None = None
        self.commands: dict[str, dict[str, Any]] = {}
        self.observations: dict[str, dict[str, Any]] = {}
        self.joint_states: dict[str, dict[str, float]] = {}
        self.rigid_bodies: dict[str, dict[str, Any]] = {}
        self.wrenches: list[tuple[str, dict[str, Any]]] = []
        self.render_fallback: str | None = None
        self.render_error: str | None = None
        self._stage = None
        self._last_dt = 0.0
        self._rgb_annotators: dict[tuple[str, int, int], Any] = {}
        for _ in range(max(0, int(warmup_steps))):
            self.app.update()

    def reset(self) -> None:
        self.commands.clear()
        self.observations.clear()
        self.joint_states.clear()
        self.wrenches.clear()
        self._rgb_annotators.clear()
        self.app.update()

    def step(self, dt: float) -> None:
        self._last_dt = float(dt)
        self.app.update()

    def load_scene(self, scene_path: str) -> None:
        import omni.usd

        self.scene_path = str(scene_path)
        self._rgb_annotators.clear()
        omni.usd.get_context().open_stage(self.scene_path)
        for _ in range(2):
            self.app.update()
        self._stage = omni.usd.get_context().get_stage()

    def get_observation(self, entity_id: str) -> dict[str, Any]:
        observation = dict(self.observations.get(entity_id, {}))
        observation.setdefault("entity_id", entity_id)
        observation.setdefault("scene_path", self.scene_path)
        observation.setdefault("source", "isaac_worker")
        observation.setdefault("last_dt", self._last_dt)
        if entity_id in self.commands:
            observation["last_command"] = dict(self.commands[entity_id])
        if entity_id in self.joint_states:
            observation["joint_states"] = dict(self.joint_states[entity_id])
            observation["joint_names"] = list(self.joint_states[entity_id].keys())
            observation["joint_positions"] = list(self.joint_states[entity_id].values())
        if entity_id in self.rigid_bodies:
            observation.update(self.rigid_bodies[entity_id])
        prim_pose = self._query_prim_pose(entity_id)
        if prim_pose is not None:
            observation["pose"] = prim_pose
        if self.render_fallback is not None:
            observation["render_fallback"] = self.render_fallback
        if self.render_error is not None:
            observation["render_error"] = self.render_error
        return observation

    def set_command(self, entity_id: str, command: dict[str, Any]) -> None:
        self.commands[entity_id] = dict(command)
        joints = command.get("joint_positions") or command.get("q")
        if isinstance(joints, list):
            self.joint_states[entity_id] = {f"joint_{index + 1}": float(value) for index, value in enumerate(joints)}
        self.app.update()

    def attach_rigid_body(self, name: str, asset_path: str, pose: dict[str, Any]) -> str:
        body_id = str(name)
        self.rigid_bodies[body_id] = {"name": body_id, "asset_path": str(asset_path), "pose": dict(pose)}
        return body_id

    def get_joint_states(self, body_id: str) -> dict[str, float]:
        return dict(self.joint_states.get(body_id, {}))

    def apply_wrench(self, body_id: str, wrench: dict[str, Any]) -> None:
        self.wrenches.append((str(body_id), dict(wrench)))

    def idle(self) -> None:
        self.app.update()

    def render(self, camera: str, width: int, height: int) -> bytes:
        image = self._render_with_isaac(camera, width, height)
        if image is not None:
            self.render_fallback = None
            self.render_error = None
            return image
        self.render_fallback = "minimal_png"
        meta = f"isaac-worker-render camera={camera} width={int(width)} height={int(height)} scene={self.scene_path}".encode()
        return b"\x89PNG\r\n\x1a\n" + meta

    def close(self) -> None:
        self.app.close()

    def _query_prim_pose(self, entity_id: str) -> dict[str, Any] | None:
        if self._stage is None or not entity_id.startswith("/"):
            return None
        try:
            from pxr import Gf, UsdGeom

            prim = self._stage.GetPrimAtPath(entity_id)
            if not prim or not prim.IsValid():
                return None
            matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0.0)
            translation = matrix.ExtractTranslation()
            quat = Gf.Transform(matrix).GetRotation().GetQuat()
            imaginary = quat.GetImaginary()
            return {
                "xyz": [float(translation[0]), float(translation[1]), float(translation[2])],
                "quat_xyzw": [float(imaginary[0]), float(imaginary[1]), float(imaginary[2]), float(quat.GetReal())],
                "frame_id": "world",
            }
        except Exception:
            return None

    def _render_with_isaac(self, camera: str, width: int, height: int) -> bytes | None:
        image = self._render_with_replicator(camera, width, height)
        if image is not None:
            return image
        return self._render_with_viewport(camera, width, height)

    def _render_with_replicator(self, camera: str, width: int, height: int) -> bytes | None:
        try:
            import omni.replicator.core as rep
        except Exception as exc:
            self.render_error = f"replicator unavailable: {exc}"
            return None

        try:
            self._ensure_camera(camera)
            key = (str(camera), int(width), int(height))
            if key not in self._rgb_annotators:
                render_product = rep.create.render_product(str(camera), (int(width), int(height)), force_new=True)
                annotator = rep.AnnotatorRegistry.get_annotator("rgb")
                annotator.attach([render_product])
                self._rgb_annotators[key] = (render_product, annotator)
            _, annotator = self._rgb_annotators[key]
            data = None
            for _ in range(4):
                rep.orchestrator.step()
                self.app.update()
                data = annotator.get_data()
                if data is not None:
                    if isinstance(data, dict):
                        data = data.get("data") if data.get("data") is not None else data.get("rgb")
                    shape = getattr(data, "shape", None)
                    if shape is not None and len(shape) >= 2 and int(shape[0]) > 0 and int(shape[1]) > 0:
                        break
            if data is None:
                self.render_error = "replicator rgb annotator returned no data"
                return None
            return encode_png_rgb(data)
        except Exception as exc:
            self.render_error = f"replicator render failed: {exc}"
            return None

    def _render_with_viewport(self, camera: str, width: int, height: int) -> bytes | None:
        try:
            import omni.kit.viewport.utility
            from omni.kit.viewport.utility import capture_viewport_to_buffer
        except Exception as exc:
            self.render_error = f"viewport capture unavailable: {exc}"
            return None

        try:
            self._ensure_camera(camera)
            viewport = omni.kit.viewport.utility.get_active_viewport()
            if viewport is None:
                self.render_error = "active viewport unavailable"
                return None
            viewport.camera_path = camera
            viewport.resolution = (int(width), int(height))
            self.app.update()
            capture = capture_viewport_to_buffer(viewport)
            data = getattr(capture, "data", None)
            if isinstance(data, bytes):
                return data
        except Exception as exc:
            self.render_error = f"viewport render failed: {exc}"
            return None
        return None

    def _ensure_camera(self, camera: str) -> None:
        if self._stage is None:
            return
        try:
            from pxr import Gf, UsdGeom

            prim = self._stage.GetPrimAtPath(camera)
            if prim and prim.IsValid() and prim.IsA(UsdGeom.Camera):
                return
            camera_prim = UsdGeom.Camera.Define(self._stage, camera)
            camera_prim.GetFocalLengthAttr().Set(24.0)
            xform = UsdGeom.Xformable(camera_prim.GetPrim())
            xform.ClearXformOpOrder()
            xform.AddTranslateOp().Set(Gf.Vec3d(2.0, -3.0, 2.0))
            xform.AddRotateXYZOp().Set(Gf.Vec3f(60.0, 0.0, 35.0))
            self.app.update()
        except Exception as exc:
            self.render_error = f"camera setup failed: {exc}"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    controller = IsaacController(
        headless=args.headless,
        camera=args.camera,
        robot_prim=args.robot_prim,
        warmup_steps=args.warmup_steps,
    )
    if args.scene:
        controller.load_scene(args.scene)
    state = IsaacWorkerState(
        controller,
        dispatch_on_main_thread=True,
        rpc_timeout_s=args.rpc_timeout_s,
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"[isaac worker] serving http://{args.host}:{args.port}/rpc", flush=True)
    try:
        while True:
            processed = state.process_next(timeout=0.05)
            if not processed:
                controller.idle()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2.0)
        controller.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
