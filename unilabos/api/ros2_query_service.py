"""ROS2 service exposure of the Phase 3 query API.

Exposes the six query operations over a single ROS2 service ``/unilabos/query``
using the existing ``unilabos_msgs/SerialCommand`` srv (string command -> string
response), so no new .srv types / colcon rebuild are required. The request
``command`` is JSON ``{"op": "query_pose", "args": {...}}``; the response
``response`` is the JSON dispatch result.

This is what lets an external VLA/policy process call the OS as an information
layer over ROS2. rclpy / message types are imported lazily.
"""

from __future__ import annotations

from typing import Any

from unilabos.api.query_dispatch import dispatch_json
from unilabos.api.query_service import QueryService

QUERY_SERVICE_NAME = "/unilabos/query"


class QueryServiceNode:
    def __init__(self, service: QueryService, service_name: str = QUERY_SERVICE_NAME, auto_start: bool = True):
        self.service = service
        self.service_name = service_name
        self.node = None
        self._srv = None
        if auto_start:
            self.start()

    def start(self) -> None:
        if self.node is not None:
            return
        import rclpy
        from rclpy.node import Node
        from unilabos_msgs.srv import SerialCommand

        if not rclpy.ok():
            rclpy.init(args=None)
        self.node = Node("unilabos_query_service")
        self._srv = self.node.create_service(SerialCommand, self.service_name, self._handle)

    def _handle(self, request: Any, response: Any) -> Any:
        response.response = dispatch_json(self.service, getattr(request, "command", ""))
        return response

    def shutdown(self) -> None:
        if self.node is not None:
            self.node.destroy_node()
            self.node = None
            self._srv = None
