#!/usr/bin/env python3
"""修补 building_map_generator 生成的 Ignition world，对齐 office_ign 约定。"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def patch_world(text: str) -> str:
    # Ignition Gazebo 6 保留名 "world"
    text = re.sub(r'<world name="world">', '<world name="sim_world">', text, count=1)
    # 用内联光源替代 model://sun（资源路径在不同环境不一致）
    text = text.replace(
        """    <include>
      <uri>model://sun</uri>
    </include>
""",
        """    <light type="directional" name="sun">
      <cast_shadows>true</cast_shadows>
      <pose>0 0 10 0 0 0</pose>
      <diffuse>1 1 1 1</diffuse>
      <specular>0.2 0.2 0.2 1</specular>
      <direction>-0.5 0.1 -0.9</direction>
    </light>
""",
    )
    text = text.replace("model://Open-RMF/TinyRobot", "model://TinyRobot")
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description="修补 unilab_layout.world 供 Ignition Gazebo 6 加载")
    ap.add_argument("--world", required=True)
    args = ap.parse_args()
    path = Path(args.world)
    patched = patch_world(path.read_text(encoding="utf-8"))
    path.write_text(patched, encoding="utf-8")
    print(f"已修补 world → {path}")


if __name__ == "__main__":
    main()

