from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from unilabos.sim.backends.isaac_bridge import IsaacBridgeBackend


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test Uni-Lab-OS edge + Isaac worker")
    parser.add_argument("--grpc", default="127.0.0.1:50051")
    parser.add_argument("--physics-endpoint", required=True)
    parser.add_argument("--state-target", required=True)
    parser.add_argument("--pose-target", required=True)
    parser.add_argument("--camera", default="/World/Camera")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--out", required=True)
    parser.add_argument("--physics-timeout-s", type=float, default=120.0)
    parser.add_argument("--poll-timeout-s", type=float, default=30.0)
    parser.add_argument("--poll-interval-s", type=float, default=1.0)
    return parser.parse_args(argv)


def is_png_like(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n") and data.endswith(b"IEND\xaeB`\x82")


def _poll_query(client, state_target: str, pose_target: str, timeout_s: float, interval_s: float):
    deadline = time.monotonic() + timeout_s
    last_error = None
    while time.monotonic() < deadline:
        try:
            state = client.query_state(state_target)
            pose = client.query_pose(pose_target)
            return state, pose
        except Exception as exc:
            last_error = exc
            time.sleep(interval_s)
    raise RuntimeError(f"query API did not return expected state/pose before timeout: {last_error}")


def main(argv: list[str] | None = None) -> int:
    from unilabos_client import RoboUniLabOSRemote, grpc_transport

    args = parse_args(argv)
    client = RoboUniLabOSRemote(grpc_transport(args.grpc))
    state, pose = _poll_query(client, args.state_target, args.pose_target, args.poll_timeout_s, args.poll_interval_s)
    physics = IsaacBridgeBackend(args.physics_endpoint, timeout=args.physics_timeout_s)
    image = physics.render(args.camera, args.width, args.height)
    if not is_png_like(image):
        raise RuntimeError(f"render payload is not PNG-like, got {len(image)} bytes")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(image)
    print(json.dumps({"state": state, "pose": pose, "image": str(out), "bytes": len(image)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
