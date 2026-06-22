"""
Guessed driver for the slide_w140 linear slide module.
Generated from mesh/xacro evidence; not tested against real hardware.
"""
from typing import Any, Dict, Optional

from unilabos.registry.decorators import action, device


@device(
    id="slide_w140",
    category=["linear_slide", "positioning_stage"],
    description="W140线性滑台模组，可执行回零、绝对位移、相对步进及行程状态上报，用于自动化装置中的直线定位场景。",
    model={
        "type": "device",
        "mesh": "slide_w140",
        "path": "https://uni-lab-test.oss-cn-zhangjiakou.aliyuncs.com/uni-lab-test/devices/slide_w140/macro_device.xacro",
    },
)
class SlideW140Guessed:
    DEFAULT_TRAVEL_M = 0.1

    def __init__(self, device_id: Optional[str] = None, config: Optional[Dict[str, Any]] = None, **kwargs):
        self.device_id = device_id or "slide_w140"
        self.config = config or {}
        self.travel_m = float(self.config.get("length", self.DEFAULT_TRAVEL_M))
        self.position_m = 0.0

    @action()
    def home_slider(self, reference: str = "left_endstop") -> dict:
        """执行滑台回零，建立当前位置为起始端。"""
        self.position_m = 0.0
        print(
            f"[{self.device_id}] Homing complete: reference={reference}, "
            f"position={self.position_m:.4f}m, stroke={self.travel_m:.4f}m"
        )
        return {"success": True, "position_m": self.position_m, "reference": reference}

    @action()
    def move_absolute(self, target_m: float, speed_mps: float = 0.02) -> dict:
        """按绝对位置移动滑台，位置单位为米。"""
        if target_m < 0.0 or target_m > self.travel_m:
            return {
                "success": False,
                "error": f"target_out_of_range:0.0_to_{self.travel_m:.4f}m",
                "requested_target_m": target_m,
            }

        self.position_m = target_m
        print(
            f"[{self.device_id}] Absolute move done: target={target_m:.4f}m, "
            f"speed={speed_mps:.4f}m/s, axis=prismatic_x"
        )
        return {"success": True, "position_m": self.position_m, "speed_mps": speed_mps}

    @action()
    def move_relative(self, delta_m: float, speed_mps: float = 0.02) -> dict:
        """按相对距离移动滑台，正向为+X方向。"""
        target_m = self.position_m + delta_m
        if target_m < 0.0 or target_m > self.travel_m:
            return {
                "success": False,
                "error": f"target_out_of_range:0.0_to_{self.travel_m:.4f}m",
                "requested_target_m": target_m,
            }

        self.position_m = target_m
        print(
            f"[{self.device_id}] Relative move done: delta={delta_m:.4f}m, "
            f"position={self.position_m:.4f}m, speed={speed_mps:.4f}m/s"
        )
        return {"success": True, "position_m": self.position_m, "delta_m": delta_m, "speed_mps": speed_mps}

    @action()
    def report_slide_state(self) -> dict:
        """返回当前滑台位姿和可用行程区间。"""
        state = {
            "success": True,
            "position_m": round(self.position_m, 6),
            "travel_m": round(self.travel_m, 6),
            "limits_m": {"lower": 0.0, "upper": round(self.travel_m, 6)},
            "joint_type": "prismatic",
        }
        print(
            f"[{self.device_id}] State report: pos={state['position_m']:.4f}m, "
            f"stroke={state['travel_m']:.4f}m, joint={state['joint_type']}"
        )
        return state
