from __future__ import annotations

import argparse
from pathlib import Path

from unilabos.sim.backends.isaac_bridge import IsaacBridgeBackend


def is_png_like(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n") and data.endswith(b"IEND\xaeB`\x82")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test a running Isaac worker")
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--scene", default=None)
    parser.add_argument("--entity", default="/World")
    parser.add_argument("--camera", default="/World/Camera")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    backend = IsaacBridgeBackend(args.endpoint, timeout=args.timeout_s)
    if args.scene:
        backend.load_scene(args.scene)
    backend.step(0.016)
    observation = backend.get_observation(args.entity)
    image = backend.render(args.camera, args.width, args.height)
    if not is_png_like(image):
        raise RuntimeError(f"render payload is not a complete PNG, got {len(image)} bytes")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(image)
    print({"observation": observation, "image_path": str(out), "bytes": len(image)}, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
