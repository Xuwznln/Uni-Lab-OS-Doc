"""End-to-end gRPC test of the query exposure + remote client.

Starts a QueryGrpcServer on an ephemeral port and calls it via grpc_transport.
Skipped if grpcio (or generated stubs) are unavailable. No ROS2 required.
"""

import pytest

pytest.importorskip("grpc")
pytest.importorskip("unilabos.api.proto.query_pb2_grpc")


@pytest.mark.integration
def test_query_roundtrip_over_grpc():
    from unilabos.api import QueryService
    from unilabos.api.grpc_query_service import QueryGrpcServer
    from unilabos.queries.ros_live_source import build_live_query_engine
    from unilabos_client import RemoteQueryError, RoboUniLabOSRemote, grpc_transport

    live, engine = build_live_query_engine()
    live.update_pose("balance_1.tare_button", [0.6, 0.0, 0.07], frame_id="robot_base")
    live.update_state("ur5", {"positions": [0.1, 0.2]})

    server = QueryGrpcServer(QueryService(engine), port=0)  # OS-assigned port
    try:
        client = RoboUniLabOSRemote(grpc_transport(f"localhost:{server.bound_port}", timeout_s=5.0))

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
        server.shutdown()
