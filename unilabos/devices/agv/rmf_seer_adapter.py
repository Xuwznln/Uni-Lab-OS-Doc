"""`agv.SEER_RMF`：真实 AGV adapter，复用 `AgvNavigator`，不重写 SEER 协议（#17 §7.3 / #18 §1.7）。

在 SEER 之上加一层：
- waypoint → SEER target id 映射（`target_map`）。
- 安全闸：`allow_real_motion` / `scene_hash` 一致 / `target_map` 命中 / 二次确认。

`AgvNavigator(host)` 构造即连 TCP，故这里惰性连接，连接失败不拖垮初始化。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from unilabos.utils.log import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("rmf.seer_adapter")

SIMULATION_META: Dict[str, Any] = {
    "driver_runtime_kind": "real",
}


class RmfMotionDenied(RuntimeError):
    """安全闸拒绝真实移动。"""


class SeerRmfAdapter:
    def __init__(
        self,
        host: str = "",
        robot_name: str = "seer_agv_01",
        fleet_name: str = "unilab_agv",
        target_map: Optional[Dict[str, str]] = None,
        allow_real_motion: bool = False,
        require_operator_confirm: bool = True,
        device_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ):
        self.device_id = device_id or robot_name
        self.host = host
        self.robot_name = robot_name
        self.fleet_name = fleet_name
        self.target_map: Dict[str, str] = dict(target_map or {})
        self.allow_real_motion = bool(allow_real_motion)
        self.require_operator_confirm = bool(require_operator_confirm)
        self.config = config or {}
        self.data: Dict[str, Any] = {}
        self._nav = None  # 惰性 AgvNavigator

    async def initialize(self) -> bool:
        return True

    async def cleanup(self) -> bool:
        return True

    # ----------------------------------------------------------- nav 句柄
    def _navigator(self):
        if self._nav is None:
            from unilabos.devices.agv.agv_navigator import AgvNavigator

            self._nav = AgvNavigator(self.host)
        return self._nav

    # ----------------------------------------------------------- status
    @property
    def pose(self) -> List[float]:
        try:
            return self._navigator().pose
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rmf] SEER pose 读取失败: {e}")
            return []

    @property
    def status(self) -> str:
        try:
            return self._navigator().status
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rmf] SEER status 读取失败: {e}")
            return "NONE"

    # ----------------------------------------------------------- 安全下发
    def resolve_target(self, waypoint: str) -> str:
        target = self.target_map.get(waypoint)
        if not target:
            raise RmfMotionDenied(f"waypoint '{waypoint}' 无 SEER target 映射（target_map 缺失）")
        return target

    def go_to_waypoint(
        self,
        waypoint: str,
        scene_hash: str = "",
        published_scene_hash: str = "",
        operator_confirmed: bool = False,
    ) -> Dict[str, Any]:
        """安全闸校验后，把 RMF waypoint 翻译成 SEER target 并下发（#17 §11.2）。"""
        if not self.allow_real_motion:
            raise RmfMotionDenied("allow_real_motion=false，禁止真实移动")
        if published_scene_hash and scene_hash and scene_hash != published_scene_hash:
            raise RmfMotionDenied(f"scene_hash 落后于发布版（stale），禁止真实移动: {scene_hash} != {published_scene_hash}")
        if self.require_operator_confirm and not operator_confirmed:
            raise RmfMotionDenied("需要操作员二次确认（require_operator_confirm=true）")
        target = self.resolve_target(waypoint)
        import json

        result = self._navigator().send_nav_task(json.dumps({"target": target}))
        return {"success": True, "waypoint": waypoint, "seer_target": target, "result": result}
