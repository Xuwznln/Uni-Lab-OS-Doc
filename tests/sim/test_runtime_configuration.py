from unilabos.sim.context import _reset_for_test, get_runtime_context
from unilabos.sim.runtime import configure_runtime


def setup_function():
    _reset_for_test()


def teardown_function():
    _reset_for_test()


class DummyPhysics:
    name = "dummy"


def test_configure_runtime_initializes_context_without_ros_services():
    services = configure_runtime(mode="sim", sim_rate=25.0, sim_paused=True, start_ros_services=False)

    assert get_runtime_context().mode == "sim"
    assert get_runtime_context().clock.scale == 25.0
    assert get_runtime_context().clock.paused is True
    assert services.clock_publisher is None
    assert services.clock_control is None


def test_configure_runtime_real_mode_keeps_sim_services_off():
    services = configure_runtime(mode="real", start_ros_services=True)

    assert services.context.mode == "real"
    assert services.clock_publisher is None
    assert services.clock_control is None


def test_configure_runtime_stores_physics_backend_and_config():
    physics = DummyPhysics()

    services = configure_runtime(
        mode="sim",
        physics=physics,
        physics_backend_name="fake",
        physics_endpoint="http://127.0.0.1:8091",
        physics_scene="/tmp/lab.usd",
        physics_timeout=45.0,
    )

    assert services.context.physics is physics
    assert get_runtime_context().physics is physics
    assert services.context.physics_backend_name == "fake"
    assert services.context.physics_endpoint == "http://127.0.0.1:8091"
    assert services.context.physics_scene == "/tmp/lab.usd"
    assert services.context.physics_timeout == 45.0
