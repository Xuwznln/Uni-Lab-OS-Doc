from unilabos.app import backend as backend_mod
from unilabos.sim.backends.fake_physics import FakePhysicsBackend


def test_initialize_runtime_for_backend_builds_fake_physics():
    services = backend_mod._initialize_runtime_for_backend(
        backend="ros",
        kwargs={
            "mode": "sim",
            "sim_rate": 10.0,
            "sim_paused": True,
            "physics": "fake",
            "physics_endpoint": None,
            "physics_scene": "/tmp/lab.usd",
            "physics_timeout": 77.0,
            "disable_sim_services": False,
            "disable_query_api": False,
            "query_grpc_port": 50051,
        },
    )

    assert isinstance(services.context.physics, FakePhysicsBackend)
    assert services.context.physics_backend_name == "fake"
    assert services.context.physics_scene == "/tmp/lab.usd"
    assert services.context.physics_timeout == 77.0
    assert services.context.clock.scale == 10.0
    assert services.context.clock.paused is True
    assert services.context.sim_services_enabled is True
    assert services.context.query_api_enabled is True


def test_initialize_runtime_for_non_ros_keeps_query_api_off():
    services = backend_mod._initialize_runtime_for_backend(
        backend="simple",
        kwargs={"mode": "sim", "physics": "none", "disable_query_api": False},
    )

    assert services.context.physics is None
    assert services.context.query_api_enabled is False
