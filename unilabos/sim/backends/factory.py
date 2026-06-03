from __future__ import annotations

from unilabos.sim.physics_backend import PhysicsBackend


def build_physics_backend(
    name: str | None,
    endpoint: str | None = None,
    scene: str | None = None,
    timeout: float = 120.0,
) -> PhysicsBackend | None:
    backend_name = (name or "none").strip().lower()
    if backend_name == "none":
        return None
    if backend_name == "fake":
        from unilabos.sim.backends.fake_physics import FakePhysicsBackend

        backend: PhysicsBackend = FakePhysicsBackend()
    elif backend_name == "isaac":
        if not endpoint:
            raise ValueError("--physics_endpoint is required when --physics isaac")
        from unilabos.sim.backends.isaac_bridge import IsaacBridgeBackend

        backend = IsaacBridgeBackend(endpoint, timeout=timeout)
    else:
        raise ValueError(f"Unsupported physics backend: {name}")

    if scene:
        backend.load_scene(scene)
    return backend
