"""Null virtual device used when a real device has no simulator yet."""

from __future__ import annotations

from typing import Any


class ObservableMixin:
    def __init__(self, *args, **kwargs):
        self._observed_state: dict[str, Any] = {}
        super_init = getattr(super(), "__init__", None)
        if callable(super_init):
            try:
                super_init(*args, **kwargs)
            except TypeError:
                pass

    def set_observed_state(self, name: str, value: Any) -> None:
        self._observed_state[name] = value

    def get_observed_state(self, name: str, default: Any = None) -> Any:
        return self._observed_state.get(name, default)


class _AwaitableNone:
    def __await__(self):
        if False:
            yield None
        return None


class NullDeviceStub(ObservableMixin):
    def __init__(self, real_class_name: str = "unknown", node_id: str = "unknown", config: dict[str, Any] | None = None, **kwargs):
        self.real_class_name = real_class_name
        self.node_id = node_id
        self.config = config or {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._observed_state: dict[str, Any] = {}

    def __getattr__(self, name: str):
        if name in self._observed_state:
            return self._observed_state[name]

        def _stub_method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return _AwaitableNone()

        return _stub_method

    async def initialize(self) -> bool:
        return True

    async def cleanup(self) -> bool:
        return True
