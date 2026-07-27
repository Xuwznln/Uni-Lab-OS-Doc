"""ROS2 组网协助：HostLink 握手时把组网信息下发 slave，slave 在 rclpy.init 前套用。

ROS2（Iron+ / Fast DDS）提供三档「可降级」的发现机制，全部由环境变量控制，
正好可以经 TCP 通路统一分发：

- ``ROS_DOMAIN_ID``                   域号（host/slave 必须一致才能互见）
- ``ROS_AUTOMATIC_DISCOVERY_RANGE``   自动发现范围：
      ``SUBNET``（默认，组播全网段）→ ``LOCALHOST``（仅本机）→ ``OFF``（完全关闭
      组播自动发现）。降级到 OFF 后仅靠静态对端/发现服务器单播组网，
      适合禁组播的实验室网络，也是逐步去 ROS 的过渡形态。
- ``ROS_STATIC_PEERS``                静态对端列表（分号分隔的 ip），OFF/受限
      范围下与列表中的地址仍可单播互发现。
- ``ROS_DISCOVERY_SERVER``            Fast DDS Discovery Server 地址（ip:port），
      配置后走服务器化发现，完全不依赖组播。

host 侧 ``build_host_ros_info()`` 汇总当前进程的组网配置；slave 侧
``apply_ros_network_env()`` 必须在 ``rclpy.init`` 之前调用（DDS 只在初始化时
读取这些变量）。
"""

from __future__ import annotations

import os
import socket
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, MutableMapping, Optional

_VALID_RANGES = ("SYSTEM_DEFAULT", "SUBNET", "LOCALHOST", "OFF")


@dataclass
class RosNetworkInfo:
    """经 HostLink 下发的 ROS 组网信息（hello 响应的 ``ros`` 字段）。"""

    domain_id: Optional[int] = None
    automatic_discovery_range: str = ""      # 空 = 不干预
    static_peers: List[str] = field(default_factory=list)
    discovery_server: str = ""               # "ip:port"；空 = 不使用

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "RosNetworkInfo":
        data = data or {}
        domain = data.get("domain_id")
        return cls(
            domain_id=int(domain) if domain is not None else None,
            automatic_discovery_range=str(data.get("automatic_discovery_range") or ""),
            static_peers=[str(p) for p in (data.get("static_peers") or []) if p],
            discovery_server=str(data.get("discovery_server") or ""),
        )


def build_host_ros_info(
    host_ip: str = "",
    domain_id: Optional[int] = None,
    discovery_range: str = "",
    static_peers: Optional[List[str]] = None,
    discovery_server: str = "",
    environ: Optional[MutableMapping[str, str]] = None,
) -> RosNetworkInfo:
    """host 侧汇总组网信息；未显式配置的项回退到 host 自身环境变量。

    host_ip 非空且未提供 static_peers 时，自动把 host 自身加入静态对端——
    slave 至少能与 host 单播互发现（降级组网的最小可用形态）。
    """
    env = environ if environ is not None else os.environ
    if domain_id is None:
        raw = env.get("ROS_DOMAIN_ID", "").strip()
        domain_id = int(raw) if raw.isdigit() else None
    if not discovery_range:
        discovery_range = env.get("ROS_AUTOMATIC_DISCOVERY_RANGE", "").strip().upper()
    if static_peers is None:
        raw_peers = env.get("ROS_STATIC_PEERS", "")
        static_peers = [p.strip() for p in raw_peers.split(";") if p.strip()]
    if not static_peers and host_ip:
        static_peers = [host_ip]
    if not discovery_server:
        discovery_server = env.get("ROS_DISCOVERY_SERVER", "").strip()
    if discovery_range and discovery_range not in _VALID_RANGES:
        raise ValueError(
            f"invalid discovery range {discovery_range!r}, expected one of {_VALID_RANGES}"
        )
    return RosNetworkInfo(
        domain_id=domain_id,
        automatic_discovery_range=discovery_range,
        static_peers=static_peers,
        discovery_server=discovery_server,
    )


def apply_ros_network_env(
    info: RosNetworkInfo,
    environ: Optional[MutableMapping[str, str]] = None,
) -> Dict[str, str]:
    """把组网信息写入环境变量（必须在 rclpy.init 之前调用）。

    只写有值的项，不清除既有变量（本地显式配置优先级最低的覆盖策略：
    host 下发什么就套用什么，没下发的保持原样）。返回实际写入的键值对。
    """
    env = environ if environ is not None else os.environ
    applied: Dict[str, str] = {}
    if info.domain_id is not None:
        applied["ROS_DOMAIN_ID"] = str(info.domain_id)
    if info.automatic_discovery_range:
        if info.automatic_discovery_range not in _VALID_RANGES:
            raise ValueError(f"invalid discovery range {info.automatic_discovery_range!r}")
        applied["ROS_AUTOMATIC_DISCOVERY_RANGE"] = info.automatic_discovery_range
    if info.static_peers:
        applied["ROS_STATIC_PEERS"] = ";".join(info.static_peers)
    if info.discovery_server:
        applied["ROS_DISCOVERY_SERVER"] = info.discovery_server
    env.update(applied)
    return applied


def detect_local_ip(probe_addr: str = "8.8.8.8") -> str:
    """探测本机对外 IP（UDP connect 技巧，不产生真实流量）；失败返回空串。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((probe_addr, 80))
            return sock.getsockname()[0]
    except OSError:
        return ""


__all__ = [
    "RosNetworkInfo",
    "apply_ros_network_env",
    "build_host_ros_info",
    "detect_local_ip",
]
