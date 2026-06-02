import asyncio
import time

import pytest

from unilabos.sim.clock import SimClock
from unilabos.sim.context import RuntimeContext, _reset_for_test, get_runtime_context, init_runtime_context


@pytest.fixture(autouse=True)
def reset_runtime():
    _reset_for_test()
    yield
    _reset_for_test()


def test_get_before_init_returns_default_real():
    ctx = get_runtime_context()
    assert ctx.mode == "real"
    assert ctx.clock.mode == "real"
    assert ctx.clock.scale == 1.0
    assert ctx.missing_sim_policy_default == "stub"


def test_init_sets_context_and_pause():
    init_runtime_context(RuntimeContext(mode="sim", clock=SimClock("sim", scale=5.0), sim_paused=True))
    ctx = get_runtime_context()
    assert ctx.mode == "sim"
    assert ctx.clock.scale == 5.0
    assert ctx.clock.paused is True


def test_runtime_context_stores_physics_configuration():
    ctx = RuntimeContext(
        mode="sim",
        physics_backend_name="isaac",
        physics_endpoint="http://127.0.0.1:8091",
        physics_scene="/tmp/lab.usd",
    )

    assert ctx.physics_backend_name == "isaac"
    assert ctx.physics_endpoint == "http://127.0.0.1:8091"
    assert ctx.physics_scene == "/tmp/lab.usd"


def test_sim_clock_sleep_scales_wall_time():
    clock = SimClock("sim", scale=20.0)
    t0 = time.monotonic()
    asyncio.run(clock.sleep(1.0))
    assert time.monotonic() - t0 < 0.2


def test_scale_change_affects_pending_sleep():
    clock = SimClock("sim", scale=1.0)

    async def scenario():
        task = asyncio.create_task(clock.sleep(2.0))
        await asyncio.sleep(0.05)
        clock.set_scale(50.0)
        await asyncio.wait_for(task, timeout=0.3)

    asyncio.run(scenario())


def test_pause_resume_blocks_sleep():
    clock = SimClock("sim", scale=50.0)

    async def scenario():
        clock.pause()
        task = asyncio.create_task(clock.sleep(1.0))
        await asyncio.sleep(0.1)
        assert not task.done()
        clock.resume()
        await asyncio.wait_for(task, timeout=0.2)

    asyncio.run(scenario())


def test_real_and_twin_scale_locked():
    for mode in ("real", "twin"):
        clock = SimClock(mode)
        with pytest.warns(UserWarning):
            assert clock.set_scale(10.0) is False
        assert clock.scale == 1.0


def test_invalid_scale_raises():
    with pytest.raises(ValueError):
        SimClock("sim", scale=0)
    with pytest.raises(ValueError):
        SimClock("sim").set_scale(-1)
