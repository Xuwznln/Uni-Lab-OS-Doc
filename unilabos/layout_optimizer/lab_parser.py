"""解析实验室平面图 JSON。

简单格式：
{
    "width": 6.0,
    "depth": 4.0,
    "obstacles": [
        {"x": 2.0, "y": 0.0, "width": 0.1, "depth": 1.0}
    ]
}
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import Lab, Obstacle, WallObstacle


def parse_lab(data: dict) -> Lab:
    """从字典解析实验室平面图。"""
    obstacles = []
    for obs in data.get("obstacles", []):
        obstacles.append(
            Obstacle(
                x=float(obs["x"]),
                y=float(obs["y"]),
                width=float(obs["width"]),
                depth=float(obs["depth"]),
            )
        )
    wall_obstacles = []
    for w in data.get("wall_obstacles", []):
        wall_obstacles.append(
            WallObstacle(
                cx=float(w["cx"]),
                cy=float(w["cy"]),
                length=float(w["length"]),
                thickness=float(w["thickness"]),
                yaw=float(w.get("yaw", 0.0)),
            )
        )
    return Lab(
        width=float(data["width"]),
        depth=float(data["depth"]),
        obstacles=obstacles,
        wall_obstacles=wall_obstacles,
    )


def load_lab_from_file(path: str | Path) -> Lab:
    """从 JSON 文件加载实验室平面图。"""
    with open(path) as f:
        data = json.load(f)
    return parse_lab(data)


def create_simple_lab(width: float, depth: float) -> Lab:
    """创建一个无障碍物的简单矩形实验室。"""
    return Lab(width=width, depth=depth)
