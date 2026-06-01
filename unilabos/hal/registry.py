from __future__ import annotations

from typing import Dict, Optional

from unilabos.hal.base import RobotHAL


class HALRegistry:
    def __init__(self):
        self._robots: Dict[str, RobotHAL] = {}

    def register(self, robot_id: str, hal: RobotHAL) -> None:
        self._robots[robot_id] = hal

    def get(self, robot_id: str) -> Optional[RobotHAL]:
        return self._robots.get(robot_id)

    def robot_ids(self):
        return sorted(self._robots)
