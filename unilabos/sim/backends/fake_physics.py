from __future__ import annotations

from typing import Any, Callable


class FakePhysicsBackend:
    name = "fake"

    def __init__(self) -> None:
        self.scene_path: str | None = None
        self.sim_time = 0.0
        self.commands: dict[str, dict[str, Any]] = {}
        self.observations: dict[str, dict[str, Any]] = {}
        self.joint_states: dict[str, dict[str, float]] = {}
        self.rigid_bodies: dict[str, dict[str, Any]] = {}
        self.wrenches: list[tuple[str, dict[str, Any]]] = []
        self._contact_callbacks: list[Callable[[dict[str, Any]], None]] = []

    def reset(self) -> None:
        self.sim_time = 0.0
        self.commands.clear()
        self.observations.clear()
        self.joint_states.clear()
        self.wrenches.clear()

    def step(self, dt: float) -> None:
        self.sim_time += float(dt)

    def load_scene(self, scene_path: str) -> None:
        self.scene_path = str(scene_path)

    def get_observation(self, entity_id: str) -> dict[str, Any]:
        observation = dict(self.observations.get(entity_id, {}))
        if entity_id in self.commands:
            observation["last_command"] = dict(self.commands[entity_id])
        if entity_id in self.joint_states:
            observation["joint_positions"] = list(self.joint_states[entity_id].values())
            observation["joint_states"] = dict(self.joint_states[entity_id])
        if entity_id in self.rigid_bodies:
            observation.update(self.rigid_bodies[entity_id])
        observation.setdefault("entity_id", entity_id)
        observation.setdefault("sim_time", self.sim_time)
        return observation

    def set_observation(self, entity_id: str, observation: dict[str, Any]) -> None:
        self.observations[entity_id] = dict(observation)

    def set_command(self, entity_id: str, command: dict[str, Any]) -> None:
        self.commands[entity_id] = dict(command)

    def attach_rigid_body(self, name: str, asset_path: str, pose: dict[str, Any]) -> str:
        body_id = str(name)
        self.rigid_bodies[body_id] = {"name": str(name), "asset_path": str(asset_path), "pose": dict(pose)}
        return body_id

    def set_joint_states(self, body_id: str, joints: dict[str, float]) -> None:
        self.joint_states[body_id] = {str(key): float(value) for key, value in joints.items()}

    def get_joint_states(self, body_id: str) -> dict[str, float]:
        return dict(self.joint_states.get(body_id, {}))

    def apply_wrench(self, body_id: str, wrench: dict[str, Any]) -> None:
        payload = dict(wrench)
        self.wrenches.append((body_id, payload))
        event = {"type": "wrench", "body_id": body_id, "wrench": payload}
        for callback in list(self._contact_callbacks):
            callback(event)

    def register_contact_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._contact_callbacks.append(callback)

    def render(self, camera: str, width: int, height: int) -> bytes:
        meta = f"fake-render camera={camera} width={int(width)} height={int(height)}".encode()
        return b"\x89PNG\r\n\x1a\n" + meta
