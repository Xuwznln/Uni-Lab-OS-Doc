"""`DisplayDevice`：轻量占位设备（#24.1 §3.3）。

用于把 layout-optimizer 场景里的实验设备（58 台、14 种 footprint）作为 OS graph 节点上云、
在 cloud 3D 按真实位置显示——**不连真实硬件**，仅承载 status / 静态位姿（节点 `pose`）用于展示。

按需求：registry 里 **14 个不同的 class 名（= footprint_key）共用本模块**（class 名不同、引用的
python 相同）。各 class 的 `model` 在 `registry/devices/layout_devices.yaml` 里各自指向对应 GLB/xacro。
接真实驱动时，把对应 class 的 `module` 换成真实设备类即可，graph / 前端不变。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

try:
    from unilabos.utils.log import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("display_device")


class DisplayDevice:
    """占位显示设备：仅暴露 `status`（标量），位置由 graph 节点 `pose` 静态决定。"""

    def __init__(
        self,
        device_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        self.device_id = device_id or "display_device"
        self.config = {**(config or {}), **kwargs}
        self._status = str(self.config.get("status", "idle"))
        self.data: Dict[str, Any] = {"status": self._status}

    @property
    def status(self) -> str:
        return self._status

    async def initialize(self) -> bool:
        self.data["status"] = self._status
        return True

    async def cleanup(self) -> bool:
        return True
