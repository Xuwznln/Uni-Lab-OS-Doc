"""End-to-end ROS2 test of the query exposure (⑧) + remote client (⑨).

Starts a QueryServiceNode on a background executor and calls it over the real
``/unilabos/query`` ROS2 service via ros2_transport. Skipped if rclpy is missing.
"""

import threading
import time

import pytest

rclpy = pytest.importorskip("rclpy")


@pytest.mark.integration
def test_query_pose_roundtrip_over_ros2(ros_context):
    from rclpy.executors import SingleThreadedExecutor

    from unilabos.api import QueryService
    from unilabos.api.ros2_query_service import QueryServiceNode
    from unilabos.queries.ros_live_source import build_live_query_engine
    from unilabos_client import RemoteQueryError, RoboUniLabOSRemote, ros2_transport

    live, engine = build_live_query_engine()
    live.update_pose("balance_1.tare_button", [0.6, 0.0, 0.07], frame_id="robot_base")
    live.update_state("ur5", {"positions": [0.1, 0.2]})

    server = QueryServiceNode(QueryService(engine), service_name="/unilabos/query_test")
    executor = SingleThreadedExecutor()
    executor.add_node(server.node)
    stop = threading.Event()

    def _spin():
        while not stop.is_set():
            executor.spin_once(timeout_sec=0.05)

    spinner = threading.Thread(target=_spin, daemon=True)
    spinner.start()
    try:
        client = RoboUniLabOSRemote(ros2_transport(service_name="/unilabos/query_test", timeout_s=5.0))
        time.sleep(0.2)  # let discovery settle

        pose = client.query_pose("balance_1.tare_button")
        assert pose["xyz"] == [0.6, 0.0, 0.07]
        assert pose["frame_id"] == "robot_base"

        state = client.query_state("ur5")
        assert state["values"]["positions"] == [0.1, 0.2]

        schema = client.query_action_schema("press_button")
        assert schema["action"] == "press_button"

        with pytest.raises(RemoteQueryError) as ei:
            client.query_pose("nonexistent_object")
        assert ei.value.code == "not_found"
    finally:
        stop.set()
        spinner.join(timeout=2.0)
        executor.shutdown()
        server.shutdown()
