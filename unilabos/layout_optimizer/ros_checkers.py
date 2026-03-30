"""ROS2/MoveIt2 碰撞检测与 IKFast 可达性检测适配器。

集成阶段替换 mock_checkers.py 中的 Mock 实现，
依赖 Uni-Lab-OS 的 moveit2.py 提供的 MoveIt2 Python 接口。

用法：
    from .ros_checkers import MoveItCollisionChecker, IKFastReachabilityChecker

    # 碰撞检测
    checker = MoveItCollisionChecker(moveit2_instance)
    collisions = checker.check(placements)

    # 可达性检测（体素图 O(1) 查询 + 实时 IK 回退）
    reachability = IKFastReachabilityChecker(moveit2_instance, voxel_dir="/path/to/voxels")
    reachable = reachability.is_reachable("elite_cs66", arm_pose, target)

环境变量：
    LAYOUT_CHECKER_MODE: "mock" | "moveit" — 选择检测器实现（默认 "mock"）
    LAYOUT_VOXEL_DIR: 预计算体素图目录路径（.npz 文件）

前置条件：
    - ROS2 + MoveIt2 运行中
    - moveit2.py 中的 MoveIt2 实例已初始化
    - 命名规范：碰撞对象使用 {device_id}_ 前缀
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from .obb import obb_corners, obb_overlap

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------- 坐标变换辅助 ----------


def _yaw_to_quat(theta: float) -> tuple[float, float, float, float]:
    """将 2D 旋转角（绕 Z 轴弧度）转换为四元数 (x, y, z, w)。"""
    return (0.0, 0.0, math.sin(theta / 2), math.cos(theta / 2))


def _transform_to_arm_frame(
    arm_pose: dict, target: dict,
) -> tuple[float, float, float]:
    """将目标点从世界坐标系变换到机械臂基坐标系。

    Args:
        arm_pose: {"x": float, "y": float, "theta": float}
        target: {"x": float, "y": float, "z": float}

    Returns:
        (local_x, local_y, local_z) 在臂基坐标系中的位置
    """
    dx = target["x"] - arm_pose["x"]
    dy = target["y"] - arm_pose["y"]
    theta = arm_pose.get("theta", 0.0)
    cos_t = math.cos(-theta)
    sin_t = math.sin(-theta)
    local_x = dx * cos_t - dy * sin_t
    local_y = dx * sin_t + dy * cos_t
    local_z = target.get("z", 0.0)
    return (local_x, local_y, local_z)


# ---------- MoveItCollisionChecker ----------


class MoveItCollisionChecker:
    """通过 MoveIt2 PlanningScene 进行碰撞检测。

    工作流程：
    1. 将所有设备同步为 MoveIt2 碰撞盒（{device_id}_ 前缀）
    2. 使用 python-fcl 进行精确两两碰撞检测（若可用）
    3. 若 FCL 不可用，回退到 OBB SAT 检测

    同步到 MoveIt2 确保机器人运动规划也能感知设备布局。
    """

    def __init__(
        self,
        moveit2: Any,
        *,
        default_height: float = 0.4,
        sync_to_scene: bool = True,
    ):
        """
        Args:
            moveit2: Uni-Lab-OS moveit2.py 中的 MoveIt2 实例
            default_height: 碰撞盒默认高度（米）
            sync_to_scene: 是否同步碰撞对象到 MoveIt2 规划场景
        """
        self._moveit2 = moveit2
        self._default_height = default_height
        self._sync_to_scene = sync_to_scene
        self._fcl_available = self._check_fcl()

    @staticmethod
    def _check_fcl() -> bool:
        """检查 python-fcl 是否可用。"""
        try:
            import fcl  # noqa: F401
            return True
        except ImportError:
            return False

    def check(self, placements: list[dict]) -> list[tuple[str, str]]:
        """返回碰撞设备对列表。

        Args:
            placements: [{"id": str, "bbox": (w, d), "pos": (x, y, θ)}, ...]

        Returns:
            [("device_a", "device_b"), ...] 存在碰撞的设备对
        """
        # 同步到 MoveIt2 规划场景
        if self._sync_to_scene:
            self._sync_collision_objects(placements)

        # 碰撞检测
        if self._fcl_available:
            return self._check_with_fcl(placements)
        return self._check_with_obb(placements)

    def check_bounds(
        self, placements: list[dict], lab_width: float, lab_depth: float,
    ) -> list[str]:
        """返回超出实验室边界的设备 ID 列表。"""
        out_of_bounds: list[str] = []
        for p in placements:
            hw, hd = self._rotated_half_extents(p)
            x, y = p["pos"][:2]
            if x - hw < 0 or x + hw > lab_width or y - hd < 0 or y + hd > lab_depth:
                out_of_bounds.append(p["id"])
        return out_of_bounds

    def _sync_collision_objects(self, placements: list[dict]) -> None:
        """将设备布局同步到 MoveIt2 规划场景。

        使用 {device_id}_ 前缀命名碰撞对象。
        """
        for p in placements:
            obj_id = f"{p['id']}_"
            w, d = p["bbox"]
            x, y = p["pos"][:2]
            theta = p["pos"][2] if len(p["pos"]) > 2 else 0.0
            h = self._default_height

            try:
                self._moveit2.add_collision_box(
                    id=obj_id,
                    size=(w, d, h),
                    position=(x, y, h / 2),
                    quat_xyzw=_yaw_to_quat(theta),
                )
            except Exception:
                logger.warning("Failed to sync collision object %s", obj_id, exc_info=True)

    def _check_with_fcl(self, placements: list[dict]) -> list[tuple[str, str]]:
        """使用 python-fcl 进行精确碰撞检测。"""
        import fcl

        objects: list[tuple[str, Any]] = []
        for p in placements:
            w, d = p["bbox"]
            h = self._default_height
            x, y = p["pos"][:2]
            theta = p["pos"][2] if len(p["pos"]) > 2 else 0.0

            geom = fcl.Box(w, d, h)
            tf = fcl.Transform(
                _yaw_to_rotation_matrix(theta),
                np.array([x, y, h / 2]),
            )
            obj = fcl.CollisionObject(geom, tf)
            objects.append((p["id"], obj))

        collisions: list[tuple[str, str]] = []
        n = len(objects)
        for i in range(n):
            for j in range(i + 1, n):
                id_a, obj_a = objects[i]
                id_b, obj_b = objects[j]
                request = fcl.CollisionRequest()
                result = fcl.CollisionResult()
                ret = fcl.collide(obj_a, obj_b, request, result)
                if ret > 0:
                    collisions.append((id_a, id_b))

        return collisions

    def _check_with_obb(self, placements: list[dict]) -> list[tuple[str, str]]:
        """OBB SAT 回退检测（与 MockCollisionChecker 相同算法）。"""
        collisions: list[tuple[str, str]] = []
        n = len(placements)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = placements[i], placements[j]
                corners_a = obb_corners(
                    a["pos"][0], a["pos"][1],
                    a["bbox"][0], a["bbox"][1],
                    a["pos"][2] if len(a["pos"]) > 2 else 0.0,
                )
                corners_b = obb_corners(
                    b["pos"][0], b["pos"][1],
                    b["bbox"][0], b["bbox"][1],
                    b["pos"][2] if len(b["pos"]) > 2 else 0.0,
                )
                if obb_overlap(corners_a, corners_b):
                    collisions.append((a["id"], b["id"]))
        return collisions

    @staticmethod
    def _rotated_half_extents(p: dict) -> tuple[float, float]:
        """计算旋转后 AABB 的半宽和半深。"""
        w, d = p["bbox"]
        theta = p["pos"][2] if len(p["pos"]) > 2 else 0.0
        cos_t = abs(math.cos(theta))
        sin_t = abs(math.sin(theta))
        half_w = (w * cos_t + d * sin_t) / 2
        half_d = (w * sin_t + d * cos_t) / 2
        return half_w, half_d


# ---------- IKFastReachabilityChecker ----------


class IKFastReachabilityChecker:
    """基于 MoveIt2 compute_ik 和预计算体素图的可达性检测。

    双模式：
    1. 体素图模式（O(1)）：从 .npz 文件加载预计算可达性网格，
       将目标点变换到臂基坐标系后直接查表。
    2. 实时 IK 模式（~5ms/call）：调用 MoveIt2.compute_ik()，
       支持约束感知的精确可达性判断。

    优先使用体素图，无匹配时回退到实时 IK。
    """

    def __init__(
        self,
        moveit2: Any = None,
        *,
        voxel_dir: str | Path | None = None,
        voxel_resolution: float = 0.01,
    ):
        """
        Args:
            moveit2: MoveIt2 实例（用于实时 IK 回退）
            voxel_dir: 预计算体素图目录（.npz 文件，文件名 = arm_id）
            voxel_resolution: 体素分辨率（米），用于坐标 → 索引转换
        """
        self._moveit2 = moveit2
        self._voxel_resolution = voxel_resolution
        self._voxel_maps: dict[str, _VoxelMap] = {}

        if voxel_dir is not None:
            self._load_voxel_maps(Path(voxel_dir))

    def is_reachable(self, arm_id: str, arm_pose: dict, target: dict) -> bool:
        """判断机械臂在给定位姿下能否到达目标点。

        Args:
            arm_id: 机械臂设备 ID
            arm_pose: {"x": float, "y": float, "theta": float}
            target: {"x": float, "y": float, "z": float}

        Returns:
            True 如果可达
        """
        local = _transform_to_arm_frame(arm_pose, target)

        # 1. 体素图查询（O(1)）
        if arm_id in self._voxel_maps:
            return self._check_voxel(arm_id, local)

        # 2. 实时 IK 回退
        if self._moveit2 is not None:
            return self._check_live_ik(local)

        # 无可用检测方式，乐观返回（记录警告）
        logger.warning(
            "No reachability checker available for arm %s, returning True", arm_id,
        )
        return True

    def _load_voxel_maps(self, voxel_dir: Path) -> None:
        """加载目录下所有 .npz 体素图文件。

        文件格式：{arm_id}.npz，包含：
            - "grid": bool ndarray (nx, ny, nz) — True 表示可达
            - "origin": float ndarray (3,) — 网格原点（臂基坐标系）
            - "resolution": float — 体素分辨率（米）
        """
        if not voxel_dir.exists():
            logger.warning("Voxel directory does not exist: %s", voxel_dir)
            return

        for npz_file in voxel_dir.glob("*.npz"):
            arm_id = npz_file.stem
            try:
                data = np.load(str(npz_file))
                grid = data["grid"].astype(bool)
                origin = data["origin"].astype(float)
                resolution = float(data.get("resolution", self._voxel_resolution))
                self._voxel_maps[arm_id] = _VoxelMap(
                    grid=grid, origin=origin, resolution=resolution,
                )
                logger.info(
                    "Loaded voxel map for %s: shape=%s, resolution=%.3f",
                    arm_id, grid.shape, resolution,
                )
            except Exception:
                logger.warning("Failed to load voxel map %s", npz_file, exc_info=True)

    def _check_voxel(self, arm_id: str, local: tuple[float, float, float]) -> bool:
        """通过体素网格查询可达性。"""
        vm = self._voxel_maps[arm_id]
        ix = int(round((local[0] - vm.origin[0]) / vm.resolution))
        iy = int(round((local[1] - vm.origin[1]) / vm.resolution))
        iz = int(round((local[2] - vm.origin[2]) / vm.resolution))

        if (
            0 <= ix < vm.grid.shape[0]
            and 0 <= iy < vm.grid.shape[1]
            and 0 <= iz < vm.grid.shape[2]
        ):
            return bool(vm.grid[ix, iy, iz])

        # 超出体素图范围 → 不可达
        return False

    def _check_live_ik(self, local: tuple[float, float, float]) -> bool:
        """调用 MoveIt2.compute_ik() 进行实时可达性检测。

        compute_ik 返回 JointState（成功）或 None（不可达）。
        使用默认朝下姿态（四元数 0, 1, 0, 0 即绕 X 轴旋转 180°）。
        """
        # 目标姿态：末端执行器朝下
        quat_xyzw = (0.0, 1.0, 0.0, 0.0)
        try:
            result = self._moveit2.compute_ik(
                position=local,
                quat_xyzw=quat_xyzw,
            )
            return result is not None
        except Exception:
            logger.warning("compute_ik call failed", exc_info=True)
            return False


# ---------- 体素图数据类 ----------


class _VoxelMap:
    """预计算可达性体素网格。"""

    __slots__ = ("grid", "origin", "resolution")

    def __init__(
        self,
        grid: np.ndarray,
        origin: np.ndarray,
        resolution: float,
    ):
        self.grid = grid
        self.origin = origin
        self.resolution = resolution


# ---------- FCL 辅助 ----------


def _yaw_to_rotation_matrix(theta: float) -> np.ndarray:
    """绕 Z 轴旋转矩阵（3×3）。"""
    c, s = math.cos(theta), math.sin(theta)
    return np.array([
        [c, -s, 0.0],
        [s,  c, 0.0],
        [0.0, 0.0, 1.0],
    ])


# ---------- 工厂函数 ----------


def create_checkers(
    moveit2: Any = None,
    *,
    mode: str | None = None,
    voxel_dir: str | None = None,
) -> tuple[Any, Any]:
    """根据环境变量或参数创建检测器实例。

    Args:
        moveit2: MoveIt2 实例（moveit 模式必需）
        mode: "mock" | "moveit"（默认从 LAYOUT_CHECKER_MODE 环境变量读取）
        voxel_dir: 体素图目录（默认从 LAYOUT_VOXEL_DIR 环境变量读取）

    Returns:
        (collision_checker, reachability_checker)
    """
    if mode is None:
        mode = os.getenv("LAYOUT_CHECKER_MODE", "mock")

    if mode == "moveit":
        if moveit2 is None:
            raise ValueError("MoveIt2 instance required for 'moveit' checker mode")

        if voxel_dir is None:
            voxel_dir = os.getenv("LAYOUT_VOXEL_DIR")

        collision = MoveItCollisionChecker(moveit2)
        reachability = IKFastReachabilityChecker(
            moveit2, voxel_dir=voxel_dir,
        )
        logger.info("Using MoveIt2 checkers (voxel_dir=%s)", voxel_dir)
        return collision, reachability

    # 默认：mock 模式
    from .mock_checkers import MockCollisionChecker, MockReachabilityChecker

    logger.info("Using mock checkers")
    return MockCollisionChecker(), MockReachabilityChecker()
