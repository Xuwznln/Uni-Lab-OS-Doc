"""gRPC exposure of the Phase 3 query API.

Reuses the transport-agnostic ``dispatch_json`` boundary: a single generic
``Query(command) -> response`` RPC carries the same JSON as the ROS2 path, so the
six query operations are defined exactly once in ``QueryService``.

grpcio and the generated stubs are imported lazily so importing ``unilabos.api``
never requires grpcio to be installed (ROS2 / local paths stay dependency-free).
"""

from __future__ import annotations

from typing import Any

from unilabos.api.query_service import QueryService

DEFAULT_GRPC_PORT = 50051


class QueryGrpcServer:
    def __init__(
        self,
        service: QueryService,
        port: int = DEFAULT_GRPC_PORT,
        max_workers: int = 8,
        auto_start: bool = True,
    ):
        self.service = service
        self.port = port
        self.max_workers = max_workers
        self.server: Any = None
        self.bound_port: int = port
        if auto_start:
            self.start()

    def start(self) -> "QueryGrpcServer":
        if self.server is not None:
            return self
        from concurrent import futures

        import grpc

        from unilabos.api.proto import query_pb2, query_pb2_grpc
        from unilabos.api.query_dispatch import dispatch_json

        service = self.service

        class _Servicer(query_pb2_grpc.QueryServiceServicer):
            def Query(self, request, context):  # noqa: N802 - gRPC method name
                return query_pb2.QueryReply(response=dispatch_json(service, request.command))

        self.server = grpc.server(futures.ThreadPoolExecutor(max_workers=self.max_workers))
        query_pb2_grpc.add_QueryServiceServicer_to_server(_Servicer(), self.server)
        # port=0 lets the OS pick a free port; add_insecure_port returns the bound one
        self.bound_port = self.server.add_insecure_port(f"[::]:{self.port}")
        self.server.start()
        return self

    def wait_for_termination(self) -> None:
        if self.server is not None:
            self.server.wait_for_termination()

    def shutdown(self, grace: float = 1.0) -> None:
        if self.server is not None:
            self.server.stop(grace)
            self.server = None
