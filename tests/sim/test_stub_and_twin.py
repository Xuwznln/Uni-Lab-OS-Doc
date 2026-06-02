import asyncio
import time

import pytest

from unilabos.sim.stub_device import NullDeviceStub
from unilabos.sim.twin_bridge import TwinBridge


def test_null_stub_accepts_arbitrary_async_usage():
    stub = NullDeviceStub("real_device", "dev1")

    async def scenario():
        return await stub.any_async_action(1, name="x")

    assert asyncio.run(scenario()) is None
    assert stub.calls[0][0] == "any_async_action"


def test_null_stub_observed_state_becomes_attribute():
    stub = NullDeviceStub("real_device", "dev1")
    stub.set_observed_state("temperature", 37.5)
    assert stub.temperature == 37.5


def test_twin_bridge_writes_observed_fields():
    virtual = NullDeviceStub("real_device", "dev1")
    bridge = TwinBridge(real_driver=object(), virtual_driver=virtual, node_id="dev1", observed_fields=["temperature"])
    assert bridge.update_from_observation({"temperature": 42, "ignored": 1}) is True
    assert virtual.temperature == 42
    assert not hasattr(virtual, "ignored") or virtual.get_observed_state("ignored") is None


def test_twin_bridge_throttles_updates():
    virtual = NullDeviceStub("real_device", "dev1")
    bridge = TwinBridge(real_driver=object(), virtual_driver=virtual, node_id="dev1", throttle_hz=1000)
    assert bridge.update_from_observation({"x": 1}) is True
    assert bridge.update_from_observation({"x": 2}) is False
    time.sleep(0.002)
    assert bridge.update_from_observation({"x": 3}) is True


def test_twin_bridge_requires_observable_virtual():
    bridge = TwinBridge(real_driver=object(), virtual_driver=object(), node_id="dev1")
    with pytest.raises(TypeError):
        bridge.update_from_observation({"x": 1})
