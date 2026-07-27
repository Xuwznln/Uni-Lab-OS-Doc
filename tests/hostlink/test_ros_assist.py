"""ROS 组网协助：信息构建与环境变量套用（含降级形态）。"""

import pytest

from unilabos.hostlink.ros_assist import (
    RosNetworkInfo,
    apply_ros_network_env,
    build_host_ros_info,
)


class TestBuildHostInfo:
    def test_from_explicit_args(self):
        info = build_host_ros_info(
            host_ip="192.168.1.10",
            domain_id=42,
            discovery_range="OFF",
            static_peers=["192.168.1.10", "192.168.1.11"],
            environ={},
        )
        assert info.domain_id == 42
        assert info.automatic_discovery_range == "OFF"
        assert info.static_peers == ["192.168.1.10", "192.168.1.11"]

    def test_fallback_to_host_environ(self):
        env = {
            "ROS_DOMAIN_ID": "7",
            "ROS_AUTOMATIC_DISCOVERY_RANGE": "localhost",
            "ROS_STATIC_PEERS": "10.0.0.1;10.0.0.2",
            "ROS_DISCOVERY_SERVER": "10.0.0.1:11811",
        }
        info = build_host_ros_info(environ=env)
        assert info.domain_id == 7
        assert info.automatic_discovery_range == "LOCALHOST"
        assert info.static_peers == ["10.0.0.1", "10.0.0.2"]
        assert info.discovery_server == "10.0.0.1:11811"

    def test_host_ip_becomes_default_static_peer(self):
        info = build_host_ros_info(host_ip="192.168.9.9", environ={})
        assert info.static_peers == ["192.168.9.9"]

    def test_invalid_range_rejected(self):
        with pytest.raises(ValueError):
            build_host_ros_info(discovery_range="ULTRA", environ={})


class TestApplyEnv:
    def test_degraded_unicast_form(self):
        """降级形态：关闭组播自动发现，仅与 host 单播互发现。"""
        env = {}
        info = RosNetworkInfo(
            domain_id=42, automatic_discovery_range="OFF", static_peers=["192.168.1.10"]
        )
        applied = apply_ros_network_env(info, environ=env)
        assert env == applied == {
            "ROS_DOMAIN_ID": "42",
            "ROS_AUTOMATIC_DISCOVERY_RANGE": "OFF",
            "ROS_STATIC_PEERS": "192.168.1.10",
        }

    def test_discovery_server_form(self):
        env = {}
        info = RosNetworkInfo(discovery_server="192.168.1.10:11811")
        apply_ros_network_env(info, environ=env)
        assert env == {"ROS_DISCOVERY_SERVER": "192.168.1.10:11811"}

    def test_empty_info_touches_nothing(self):
        env = {"ROS_DOMAIN_ID": "1"}
        applied = apply_ros_network_env(RosNetworkInfo(), environ=env)
        assert applied == {}
        assert env == {"ROS_DOMAIN_ID": "1"}

    def test_round_trip_serialization(self):
        info = RosNetworkInfo(
            domain_id=3,
            automatic_discovery_range="LOCALHOST",
            static_peers=["a", "b"],
            discovery_server="s:1",
        )
        assert RosNetworkInfo.from_dict(info.to_dict()) == info
