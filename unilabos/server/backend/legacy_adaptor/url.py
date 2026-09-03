"""Backend 连接地址构建函数。"""

from __future__ import annotations

from typing import Optional
from unilabos.config.config import HTTPConfig
from unilabos.utils.address import derive_websocket_address


def build_backend_websocket_url() -> Optional[str]:
    """从显式 schedule 地址或 Backend HTTP 地址构建 WS URL。

    ``/ws/schedule`` 是 Backend 的线协议路径；同一 Backend 会话也可供
    其他数据域复用。
    """

    if not HTTPConfig.remote_addr:
        return None
    return derive_websocket_address(
        HTTPConfig.remote_addr,
        websocket_address=HTTPConfig.schedule_addr,
    )


__all__ = ["build_backend_websocket_url"]
