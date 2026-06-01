#!/usr/bin/env python3
"""Smoke test: bring up the edge sim runtime exactly as `unilab --mode sim` does,
then verify the Phase 1a sim services and Phase 3 query API are actually live on
real ROS2 + gRPC.

Run on a machine with rclpy (and optionally grpcio):

    PYTHONPATH=<working_copy> python scripts/smoke_sim_edge.py

It uses the same helpers main_slave_run.main() calls, so this exercises the real
edge boot path (not a pytest harness).
"""

from __future__ import annotations

import socket
import sys
import threading
import time


def main() -> int:
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from sensor_msgs.msg import JointState

    from unilabos.ros.main_slave_run import _start_query_services, _start_runtime_sim_nodes
    from unilabos.sim.runtime import configure_runtime
    from unilabos_client import RoboUniLabOSRemote, ros2_transport

    print("== configure runtime: mode=sim, sim_rate=10 ==")
    services = configure_runtime(mode="sim", sim_rate=10.0, start_ros_services=False)
    ctx = services.context
    ctx.sim_services_enabled = True
    ctx.query_api_enabled = True
    ctx.query_grpc_port = 50051

    if not rclpy.ok():
        rclpy.init()
    executor = SingleThreadedExecutor()

    print("== start Phase 1a sim nodes (/clock + /sim control) ==")
    _start_runtime_sim_nodes(executor)
    print("== start Phase 3 query API (/unilabos/query + gRPC :50051) ==")
    _start_query_services(executor)

    pub_node = Node("smoke_joint_pub")
    pub = pub_node.create_publisher(JointState, "/joint_states", 10)
    probe = Node("smoke_probe")
    executor.add_node(pub_node)
    executor.add_node(probe)

    stop = threading.Event()
    spinner = threading.Thread(
        target=lambda: [executor.spin_once(timeout_sec=0.05) for _ in iter(lambda: not stop.is_set(), False)],
        daemon=True,
    )
    spinner.start()

    results = {}
    try:
        time.sleep(1.0)

        # 1) ROS2 services present
        names = dict(probe.get_service_names_and_types())
        results["ros2 /unilabos/query"] = "/unilabos/query" in names
        results["ros2 /sim/set_rate"] = "/sim/set_rate" in names or "/unilab/sim/set_rate" in names

        # 2) /clock topic published (sim time)
        topics = dict(probe.get_topic_names_and_types())
        results["/clock topic"] = "/clock" in topics

        # 3) gRPC port listening
        try:
            with socket.create_connection(("localhost", 50051), timeout=2.0):
                results["gRPC :50051 listening"] = True
        except OSError:
            results["gRPC :50051 listening"] = False

        # 4) publish joint_states and query live state over ROS2
        msg = JointState()
        msg.name = ["j1", "j2"]
        msg.position = [0.314, 0.628]
        msg.header.frame_id = "ur5"
        for _ in range(12):
            pub.publish(msg)
            time.sleep(0.05)
        time.sleep(0.4)

        client = RoboUniLabOSRemote(ros2_transport(service_name="/unilabos/query", timeout_s=5.0))
        schema = client.query_action_schema("press_button")
        results["query_action_schema(press_button)"] = schema.get("action") == "press_button"
        state = client.query_state("ur5")
        results["query_state(ur5) live joints"] = state.get("values", {}).get("positions") == [0.314, 0.628]

        # 5) gRPC query (if available)
        try:
            from unilabos_client import grpc_transport

            gclient = RoboUniLabOSRemote(grpc_transport("localhost:50051", timeout_s=5.0))
            gstate = gclient.query_state("ur5")
            results["query_state(ur5) over gRPC"] = gstate.get("values", {}).get("positions") == [0.314, 0.628]
        except Exception as exc:  # noqa: BLE001
            results["query_state(ur5) over gRPC"] = f"skipped/failed: {exc}"
    finally:
        stop.set()
        spinner.join(timeout=2.0)
        for svc in getattr(rclpy, "__query_services", []):
            if hasattr(svc, "shutdown"):
                svc.shutdown()
        for n in getattr(rclpy, "__sim_runtime_nodes", []):
            if hasattr(n, "shutdown"):
                n.shutdown()
        executor.shutdown()

    print("\n== SMOKE RESULTS ==")
    ok = True
    for k, v in results.items():
        flag = "OK " if v is True else "!! "
        if v is not True:
            ok = False
        print(f"  [{flag}] {k}: {v}")
    print(f"\n== {'ALL GREEN' if ok else 'SOME CHECKS FAILED'} ==")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
