"""资源根级运行态的公共类型。"""

from typing import Optional, Tuple


LiquidStateEntry = Tuple[str, float, str]
LiquidHistoryEntry = Tuple[Optional[str], float, str]

# PLR Container.serialize_state() 中属于物质运行态的字段。它们像 barcode 一样
# 在 ResourceDict 中只保留根字段；max_volume/thing 仍留在 data。
TRACKER_STATE_KEYS = ("liquids", "liquid_history", "unknown_counter")


__all__ = [
    "LiquidHistoryEntry",
    "LiquidStateEntry",
    "TRACKER_STATE_KEYS",
]
