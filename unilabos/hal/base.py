from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from unilabos.queries.models import Pose


@dataclass(frozen=True)
class JointState:
    names: List[str]
    positions: List[float]
    velocities: List[float] = field(default_factory=list)
    efforts: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "names": list(self.names),
            "positions": list(self.positions),
            "velocities": list(self.velocities),
            "efforts": list(self.efforts),
        }


class RobotHAL(ABC):
    @abstractmethod
    def get_pose(self, frame: str = "tool0") -> "Pose":
        ...

    @abstractmethod
    def get_joint_state(self) -> JointState:
        ...

    @abstractmethod
    def move_l(self, pose: "Pose", speed: float = 0.1) -> None:
        ...

    @abstractmethod
    def move_j(self, joints: List[float], speed: float = 0.5) -> None:
        ...

    @abstractmethod
    def open_gripper(self) -> None:
        ...

    @abstractmethod
    def close_gripper(self) -> None:
        ...

    def skill(self, name: str, **kwargs) -> Any:
        raise NotImplementedError(f"Skill is not implemented by {type(self).__name__}: {name}")
