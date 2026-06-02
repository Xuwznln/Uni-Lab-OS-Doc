"""Edge startup wiring test: _start_query_services brings up ROS2 + gRPC query API,
and live /joint_states flows through RosLiveSource into both transports.

Skipped if rclpy/grpc unavailable.
"""

import threading
import time

import pytest

rclpy = pytest.importorskip("rclpy")


def test_build_query_static_sources_puts_physics_before_labutopia(monkeypatch):
    from unilabos.ros.main_slave_run import _build_query_static_sources
    from unilabos.sim.backends.fake_physics import FakePhysicsBackend
    from unilabos.sim.context import RuntimeContext

    class StaticSource:
        name = "static"

    import unilabos.ros.main_slave_run as mod

    monkeypatch.setattr(mod, "_build_labutopia_sources", lambda ctx: [StaticSource()])

    sources = _build_query_static_sources(RuntimeContext(mode="sim", physics=FakePhysicsBackend()))

    assert sources[0].name == "physics_live"
    assert sources[1].name == "static"


@pytest.mark.integration
def test_edge_query_services_live_flow(ros_context):
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from sensor_msgs.msg import JointState

    from unilabos.ros.main_slave_run import _start_query_services
    from unilabos.sim.context import RuntimeContext, _reset_for_test, init_runtime_context
    from unilabos_client import RoboUniLabOSRemote, ros2_transport

    grpc_available = True
    try:
        import grpc  # noqa: F401

        from unilabos.api.proto import query_pb2_grpc  # noqa: F401
    except Exception:
        grpc_available = False

    port = 50071 if grpc_available else 0
    _reset_for_test()
    init_runtime_context(RuntimeContext(mode="sim", query_api_enabled=True, query_grpc_port=port))

    executor = SingleThreadedExecutor()
    _start_query_services(executor)
    started = getattr(rclpy, "__query_services", [])
    assert started, "_start_query_services should bring up at least the ROS2 query node"

    pub_node = Node("test_joint_state_pub")
    pub = pub_node.create_publisher(JointState, "/joint_states", 10)
    executor.add_node(pub_node)

    stop = threading.Event()
    spinner = threading.Thread(
        target=lambda: [executor.spin_once(timeout_sec=0.05) for _ in iter(lambda: not stop.is_set(), False)],
        daemon=True,
    )
    spinner.start()
    try:
        msg = JointState()
        msg.name = ["j1", "j2"]
        msg.position = [0.1, 0.2]
        msg.header.frame_id = "ur5"
        for _ in range(12):
            pub.publish(msg)
            time.sleep(0.05)
        time.sleep(0.3)

        # --- over ROS2 ---
        rclient = RoboUniLabOSRemote(ros2_transport(service_name="/unilabos/query", timeout_s=5.0))
        assert rclient.query_action_schema("press_button")["action"] == "press_button"
        assert rclient.query_state("ur5")["values"]["positions"] == [0.1, 0.2]

        # --- over gRPC (if available) ---
        if grpc_available:
            from unilabos_client import grpc_transport

            gclient = RoboUniLabOSRemote(grpc_transport(f"localhost:{port}", timeout_s=5.0))
            assert gclient.query_state("ur5")["values"]["positions"] == [0.1, 0.2]
    finally:
        stop.set()
        spinner.join(timeout=2.0)
        for svc in started:
            if hasattr(svc, "shutdown"):
                try:
                    svc.shutdown()
                except Exception:
                    pass
        executor.shutdown()
        _reset_for_test()
