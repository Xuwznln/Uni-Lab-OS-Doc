import pytest

from unilabos.sim.backends.factory import build_physics_backend
from unilabos.sim.backends.fake_physics import FakePhysicsBackend
from unilabos.sim.backends.isaac_bridge import IsaacBridgeBackend


def test_factory_returns_none_for_none_backend():
    assert build_physics_backend("none", endpoint=None, scene=None) is None


def test_factory_builds_fake_backend_and_loads_scene():
    backend = build_physics_backend("fake", endpoint=None, scene="/tmp/lab.usd")

    assert isinstance(backend, FakePhysicsBackend)
    assert backend.scene_path == "/tmp/lab.usd"


def test_factory_requires_endpoint_for_isaac_backend():
    with pytest.raises(ValueError, match="--physics_endpoint is required"):
        build_physics_backend("isaac", endpoint=None, scene=None)


def test_factory_builds_isaac_backend_without_calling_scene_when_absent():
    backend = build_physics_backend("isaac", endpoint="http://127.0.0.1:8091", scene=None, timeout=42.0)

    assert isinstance(backend, IsaacBridgeBackend)
    assert backend.endpoint == "http://127.0.0.1:8091"
    assert backend.timeout == 42.0


def test_factory_rejects_unknown_backend():
    with pytest.raises(ValueError, match="Unsupported physics backend"):
        build_physics_backend("mujoco", endpoint=None, scene=None)
