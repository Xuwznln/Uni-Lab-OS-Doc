"""PLR 运行时状态与 UniLab ``data`` 的适配工具。"""

import copy
from typing import Any, Dict


def get_unilabos_state(resource: Any) -> Dict[str, Any]:
    """获取运行时资源状态；该状态由 ``ResourceDict.data`` 注入。"""

    state = getattr(resource, "_unilabos_state", None)
    if state is None:
        state = {}
        resource._unilabos_state = state
    if not isinstance(state, dict):
        raise ValueError(f"资源 {getattr(resource, 'name', resource)} 的 _unilabos_state 必须是对象")
    return state


def serialize_all_state_with_unilabos(resource: Any) -> Dict[str, Dict[str, Any]]:
    """序列化 PLR 原生状态，并保留由 UniLab ``data`` 注入的扩展状态。"""

    states = copy.deepcopy(resource.serialize_all_state())

    def merge(current: Any) -> None:
        native_state = states.get(current.name, {})
        if not isinstance(native_state, dict):
            raise ValueError(f"资源 {current.name} 的原生 state 必须是对象")
        # 原生动态状态优先，避免旧 _unilabos_state 覆盖液体体积等实时值。
        states[current.name] = {
            **copy.deepcopy(get_unilabos_state(current)),
            **native_state,
        }
        for child in current.children:
            merge(child)

    merge(resource)
    return states


def load_all_state_with_unilabos(resource: Any, states: Dict[str, Dict[str, Any]]) -> None:
    """从 UniLab ``data`` 加载 PLR 原生状态，并注入完整运行时 state。"""

    resource.load_all_state(states)

    def inject(current: Any) -> None:
        state = states.get(current.name, {})
        if not isinstance(state, dict):
            raise ValueError(f"资源 {current.name} 的 data/state 必须是对象")
        current._unilabos_state = copy.deepcopy(state)
        for child in current.children:
            inject(child)

    inject(resource)
