"""Transport-agnostic remote client for the Phase 3 query API.

``RoboUniLabOSRemote`` speaks the same ``{op, args} -> {ok, result|error}`` JSON
boundary as ``unilabos.api.query_dispatch`` but over a pluggable *transport*
(a ``command_str -> response_str`` callable). This lets the exact same client be
driven by:

- a ROS2 service client (``ros2_transport``) — cross-process over DDS,
- a local in-process ``QueryService`` (great for tests),
- a future gRPC/HTTP stub.

Mirrors the method surface of ``unilabos_client.local.RoboUniLabOS`` so policy
code can swap local<->remote transparently.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

Transport = Callable[[str], str]


class RemoteQueryError(RuntimeError):
    def __init__(self, op: str, error: str, code: str = "error"):
        super().__init__(f"{op}: {error} ({code})")
        self.op = op
        self.error = error
        self.code = code


class RoboUniLabOSRemote:
    def __init__(self, transport: Transport):
        self._transport = transport

    def _call(self, op: str, **args: Any) -> Dict[str, Any]:
        # drop None kwargs so server-side defaults apply
        clean = {k: v for k, v in args.items() if v is not None}
        raw = self._transport(json.dumps({"op": op, "args": clean}))
        resp = json.loads(raw)
        if not resp.get("ok"):
            raise RemoteQueryError(op, resp.get("error", "unknown"), resp.get("code", "error"))
        return resp["result"]

    def query_pose(self, target: str, frame: Optional[str] = None) -> Dict[str, Any]:
        return self._call("query_pose", target=target, frame=frame)

    def query_state(self, target: str) -> Dict[str, Any]:
        return self._call("query_state", target=target)

    def query_affordance(self, target: str, kind: Optional[str] = None) -> Dict[str, Any]:
        return self._call("query_affordance", target=target, kind=kind)

    def query_action_schema(self, action: str) -> Dict[str, Any]:
        return self._call("query_action_schema", action=action)

    def query_safety_zones(self) -> Dict[str, Any]:
        return self._call("query_safety_zones")

    def query_verification(
        self,
        task_id: str,
        context: Optional[Dict[str, Any]] = None,
        action: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._call("query_verification", task_id=task_id, context=context, action=action)


def local_transport(service: Any) -> Transport:
    """Transport backed by an in-process QueryService (no network). Useful for tests."""
    from unilabos.api.query_dispatch import dispatch_json

    return lambda command: dispatch_json(service, command)


def ros2_transport(
    node: Any = None,
    service_name: str = "/unilabos/query",
    timeout_s: float = 5.0,
) -> Transport:
    """Transport backed by the ROS2 ``/unilabos/query`` SerialCommand service.

    rclpy / message types imported lazily. Reuses ``node`` if given, else creates
    a throwaway client node.
    """
    import rclpy
    from rclpy.node import Node
    from unilabos_msgs.srv import SerialCommand

    if not rclpy.ok():
        rclpy.init(args=None)
    owns_node = node is None
    client_node = node or Node("unilabos_query_client")
    client = client_node.create_client(SerialCommand, service_name)

    def _transport(command: str) -> str:
        if not client.wait_for_service(timeout_sec=timeout_s):
            return json.dumps({"ok": False, "error": f"service {service_name} unavailable", "code": "timeout"})
        request = SerialCommand.Request()
        request.command = command
        future = client.call_async(request)
        rclpy.spin_until_future_complete(client_node, future, timeout_sec=timeout_s)
        result = future.result()
        if result is None:
            return json.dumps({"ok": False, "error": "no response", "code": "timeout"})
        return result.response

    _transport.owns_node = owns_node  # type: ignore[attr-defined]
    _transport.node = client_node  # type: ignore[attr-defined]
    return _transport


def grpc_transport(target: str = "localhost:50051", timeout_s: float = 5.0) -> Transport:
    """Transport backed by the gRPC query server.

    grpcio / generated stubs imported lazily. The exact same RoboUniLabOSRemote
    works over gRPC, ROS2, or local with no code change.
    """
    import grpc

    from unilabos.api.proto import query_pb2, query_pb2_grpc

    channel = grpc.insecure_channel(target)
    stub = query_pb2_grpc.QueryServiceStub(channel)

    def _transport(command: str) -> str:
        try:
            reply = stub.Query(query_pb2.QueryRequest(command=command), timeout=timeout_s)
            return reply.response
        except grpc.RpcError as exc:  # noqa: BLE001
            detail = exc.details() if hasattr(exc, "details") else str(exc)
            return json.dumps({"ok": False, "error": str(detail), "code": "grpc_error"})

    _transport.channel = channel  # type: ignore[attr-defined]
    return _transport
