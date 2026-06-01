"""Protocol for high-fidelity physics backends.

The first simulation slice can run with simple virtual devices, but Phase 1b
and later engines need a stable OS <-> simulator contract. Backends may wrap
Matterix, Isaac Sim, MuJoCo, LabUtopia, or a lightweight test double as long as
they provide these methods.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class PhysicsBackend(Protocol):
    name: str

    def reset(self) -> None:
        ...

    def step(self, dt: float) -> None:
        ...

    def get_observation(self, entity_id: str) -> dict[str, Any]:
        ...

    def set_command(self, entity_id: str, command: dict[str, Any]) -> None:
        ...

    def attach_rigid_body(self, name: str, asset_path: str, pose: dict[str, Any]) -> str:
        ...

    def get_joint_states(self, body_id: str) -> dict[str, float]:
        ...

    def apply_wrench(self, body_id: str, wrench: dict[str, Any]) -> None:
        ...

    def register_contact_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        ...
