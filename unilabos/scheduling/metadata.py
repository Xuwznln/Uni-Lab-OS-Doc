"""宿主提供的计时规则身份目录；不含估时公式、统计模型或算法实现。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from unilabos.scheduling.protocol import require, validate_value


def timing_profile_catalog(value: Any) -> dict[str, dict[str, str]]:
    require(isinstance(value, dict), "CONFIG_INVALID", "计时元数据必须是动作身份到 TimingProfile 的对象")
    for action, profile in value.items():
        validate_value(action, "Id", code="CONFIG_INVALID")
        validate_value(profile, "TimingProfile", code="CONFIG_INVALID")
    return deepcopy(value)
