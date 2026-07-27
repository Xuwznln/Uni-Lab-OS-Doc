"""端到端：真实 localhost TCP 上的握手、心跳在线监控、请求关联与重连。"""

import threading
import time

import pytest

from unilabos.hostlink.client import HostLinkClient
from unilabos.hostlink.protocol import LinkError, RemoteError
from unilabos.hostlink.server import HostLinkServer


@pytest.fixture()
def server():
    srv = HostLinkServer(bind="127.0.0.1", port=0, heartbeat_timeout=2.0).start()
    yield srv
    srv.stop()


def _client(server: HostLinkServer, **kwargs) -> HostLinkClient:
    defaults = dict(
        machine_name="slave-t",
        heartbeat_interval=0.2,
        connect_timeout=2.0,
        request_timeout=2.0,
        reconnect_max_backoff=0.2,
    )
    defaults.update(kwargs)
    return HostLinkClient("127.0.0.1", server.port, **defaults)


class TestHandshakeAndOnline:
    def test_connect_hello_registers_peer(self, server):
        server.hello_payload = {"ros": {"domain_id": 7, "static_peers": ["10.0.0.1"]}}
        client = _client(server)
        try:
            assert client.connect_blocking(timeout=5)
            assert client.online
            # hello 缓存带回 ros 组网协助
            info = client.hello_ros_info()
            assert info.domain_id == 7
            assert info.static_peers == ["10.0.0.1"]
            # 服务端登记了 peer 身份并在线
            peers = server.peers()
            assert len(peers) == 1
            assert peers[0]["machine_name"] == "slave-t"
            assert peers[0]["role"] == "slave"
            assert peers[0]["online"] is True
        finally:
            client.close()

    def test_offline_detection_on_client_close(self, server):
        client = _client(server)
        assert client.connect_blocking(timeout=5)
        client.close()
        deadline = time.time() + 3
        while time.time() < deadline:
            peers = server.peers()
            if peers and peers[0]["online"] is False:
                break
            time.sleep(0.05)
        assert server.peers()[0]["online"] is False

    def test_status_change_callback_and_reconnect(self, server):
        events = []
        gate = threading.Event()

        def on_change(online: bool) -> None:
            events.append(online)
            if len(events) >= 3:  # 上线 → 掉线 → 重连上线
                gate.set()

        client = _client(server, on_status_change=on_change)
        try:
            assert client.connect_blocking(timeout=5)
            # 模拟 host 重启：停服务再原端口拉起
            port = server.port
            server.stop()
            time.sleep(0.3)
            revived = HostLinkServer(bind="127.0.0.1", port=port, heartbeat_timeout=2.0).start()
            try:
                assert gate.wait(timeout=8), f"status events: {events}"
                assert events[0] is True and False in events and events[-1] is True
                assert client.online
            finally:
                revived.stop()
        finally:
            client.close()


class TestRequestChannel:
    def test_material_request_round_trip(self, server):
        def material_handler(data, peer):
            assert peer["machine_name"] == "slave-t"
            assert data["uuid"] == "u-42"
            return {"nodes": [{"uuid": "u-42", "name": "beaker", "parent_uuid": None}]}

        server.register_handler("material", material_handler)
        client = _client(server)
        try:
            assert client.connect_blocking(timeout=5)
            nodes = client.get_resource(uuid="u-42")
            assert nodes == [{"uuid": "u-42", "name": "beaker", "parent_uuid": None}]
        finally:
            client.close()

    def test_unknown_action_returns_remote_error(self, server):
        client = _client(server)
        try:
            assert client.connect_blocking(timeout=5)
            with pytest.raises(RemoteError, match="unknown action_type"):
                client.request("no_such_action")
        finally:
            client.close()

    def test_handler_exception_becomes_remote_error(self, server):
        def broken(data, peer):
            raise ValueError("resource not found: u-x")

        server.register_handler("material", broken)
        client = _client(server)
        try:
            assert client.connect_blocking(timeout=5)
            with pytest.raises(RemoteError, match="resource not found"):
                client.get_resource(uuid="u-x")
        finally:
            client.close()

    def test_concurrent_requests_correlate(self, server):
        def echo(data, peer):
            time.sleep(0.05)  # 制造交叠
            return {"echo": data["n"]}

        server.register_handler("echo", echo)
        client = _client(server)
        results = {}
        try:
            assert client.connect_blocking(timeout=5)

            def call(n: int) -> None:
                results[n] = client.request("echo", data={"n": n})["echo"]

            threads = [threading.Thread(target=call, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)
            assert results == {i: i for i in range(8)}
        finally:
            client.close()

    def test_request_while_offline_raises_link_error(self, server):
        client = _client(server)
        with pytest.raises(LinkError, match="offline"):
            client.request("ping")


class TestPeersRestEndpoint:
    """/api/v1/hostlink/peers：组网在线状态的 REST 暴露（host/slave/disabled 三态）。"""

    @pytest.fixture()
    def rest_client(self):
        fastapi = pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient
        from unilabos.app.scheduler.api import create_app

        return TestClient(create_app())

    def test_disabled_when_no_link(self, rest_client):
        from unilabos.hostlink.client import set_hostlink_client
        from unilabos.hostlink.server import set_hostlink_server

        set_hostlink_server(None)
        set_hostlink_client(None)
        body = rest_client.get("/api/v1/hostlink/peers").json()
        assert body == {"role": "disabled", "peers": [], "client": None}

    def test_host_role_lists_peers(self, server, rest_client):
        from unilabos.hostlink.server import set_hostlink_server

        set_hostlink_server(server)
        try:
            client = _client(server)
            try:
                assert client.connect_blocking(timeout=5)
                body = rest_client.get("/api/v1/hostlink/peers").json()
                assert body["role"] == "host"
                assert body["peers"][0]["machine_name"] == "slave-t"
                assert body["peers"][0]["online"] is True
            finally:
                client.close()
        finally:
            set_hostlink_server(None)

    def test_slave_role_reports_client(self, server, rest_client):
        from unilabos.hostlink.client import set_hostlink_client

        client = _client(server)
        set_hostlink_client(client)
        try:
            assert client.connect_blocking(timeout=5)
            body = rest_client.get("/api/v1/hostlink/peers").json()
            assert body["role"] == "slave"
            assert body["client"]["online"] is True
            assert body["client"]["port"] == server.port
        finally:
            set_hostlink_client(None)
            client.close()


class TestHeartbeatAcrossServerTimeouts:
    """回归：心跳间隔 > 服务端 socket 超时窗口时连接必须保持稳定。

    实机联调击中的 bug：sock.makefile + settimeout 组合在首次超时后损坏
    （"cannot read from timed out object"），服务端静默断连，客户端每个心跳
    周期掉线重连一次。LineReader（recv 缓冲）修复后本测试守住该行为。
    """

    def test_connection_survives_idle_timeout_windows(self):
        server = HostLinkServer(
            bind="127.0.0.1", port=0, heartbeat_timeout=5.0, socket_timeout=0.3
        ).start()
        status_events = []
        client = HostLinkClient(
            "127.0.0.1", server.port, machine_name="steady",
            heartbeat_interval=1.2,  # 跨约 4 个服务端超时窗口
            connect_timeout=2.0, request_timeout=2.0,
            on_status_change=status_events.append,
        )
        try:
            assert client.connect_blocking(timeout=5)
            time.sleep(4.0)  # ≥3 个心跳周期、≥13 个服务端超时窗口
            assert client.online
            assert status_events == [True], f"不应有掉线重连: {status_events}"
            peers = server.peers()
            assert len(peers) == 1, f"不应产生多条连接记录: {peers}"
            assert peers[0]["online"] is True
            assert peers[0]["last_seen"] > peers[0]["connected_at"]  # 心跳确实到达
        finally:
            client.close()
            server.stop()
