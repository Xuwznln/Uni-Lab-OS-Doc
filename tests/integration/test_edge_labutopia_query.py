"""Edge + LabUtopia scene query integration test.

Configures RuntimeContext with LabUtopia asset cards (fixtures), starts the edge
query services, then queries a real scene object's pose/affordance over ROS2
(and gRPC if available). Proves `unilab --mode sim --query_labutopia_assets ...`
serves the LabUtopia scene through the query API.

Skipped if rclpy is unavailable.
"""

import threading
import time
from pathlib import Path

import pytest

rclpy = pytest.importorskip("rclpy")

FIXTURE_ASSETS = Path(__file__).parents[1] / "fixtures" / "labutopia" / "asset_cards"


@pytest.mark.integration
def test_edge_serves_labutopia_scene_over_query_api(ros_context):
    from rclpy.executors import SingleThreadedExecutor

    from unilabos.ros.main_slave_run import _start_query_services
    from unilabos.sim.context import RuntimeContext, _reset_for_test, init_runtime_context
    from unilabos_client import RoboUniLabOSRemote, ros2_transport

    grpc_available = True
    try:
        import grpc  # noqa: F401

        from unilabos.api.proto import query_pb2_grpc  # noqa: F401
    except Exception:
        grpc_available = False

    port = 50073 if grpc_available else 0
    _reset_for_test()
    init_runtime_context(
        RuntimeContext(
            mode="sim",
            query_api_enabled=True,
            query_grpc_port=port,
            query_labutopia_assets=str(FIXTURE_ASSETS),
        )
    )

    executor = SingleThreadedExecutor()
    _start_query_services(executor)
    started = getattr(rclpy, "__query_services", [])
    assert started, "query services should start"

    stop = threading.Event()
    spinner = threading.Thread(
        target=lambda: [executor.spin_once(timeout_sec=0.05) for _ in iter(lambda: not stop.is_set(), False)],
        daemon=True,
    )
    spinner.start()
    try:
        time.sleep(0.3)
        client = RoboUniLabOSRemote(ros2_transport(service_name="/unilabos/query", timeout_s=5.0))

        # LabUtopia scene object pose (beaker), keyed by prim path
        pose = client.query_pose("/World/beaker1")
        assert pose["source"] == "labutopia_asset_cards"
        assert len(pose["xyz"]) == 3

        # affordance for the same object (pour primitive expected for a beaker)
        aff = client.query_affordance("World__beaker1")
        primitives = {p for item in aff["affordances"] for p in item["action_primitives"]}
        assert primitives, "beaker should expose at least one affordance primitive"

        # safety zones derived from scene bboxes
        zones = client.query_safety_zones()
        assert len(zones["safety_zones"]) >= 1

        if grpc_available:
            from unilabos_client import grpc_transport

            gclient = RoboUniLabOSRemote(grpc_transport(f"localhost:{port}", timeout_s=5.0))
            gpose = gclient.query_pose("/World/beaker1")
            assert gpose["source"] == "labutopia_asset_cards"
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
