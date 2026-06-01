"""Verify --query_labutopia_usd wires a LabUtopiaUsdSource (first) into the engine sources.

Source construction is lazy (USD stage opened only on query), so this does not
require pxr. The actual USD reading is validated by the live 4090 smoke.
"""

import pytest

# _build_labutopia_sources lives in main_slave_run which imports rclpy at module load
rclpy = pytest.importorskip("rclpy")


@pytest.mark.integration
def test_usd_source_registered_first_when_configured():
    from unilabos.ros.main_slave_run import _build_labutopia_sources
    from unilabos.sim.context import RuntimeContext

    ctx = RuntimeContext(
        mode="sim",
        query_labutopia_usd="/tmp/does_not_need_to_exist.usd",
    )
    sources = _build_labutopia_sources(ctx)
    assert sources, "USD source should be registered when query_labutopia_usd is set"
    assert type(sources[0]).__name__ == "LabUtopiaUsdSource"
    assert sources[0].usd_path == "/tmp/does_not_need_to_exist.usd"


@pytest.mark.integration
def test_no_sources_when_unconfigured():
    from unilabos.ros.main_slave_run import _build_labutopia_sources
    from unilabos.sim.context import RuntimeContext

    assert _build_labutopia_sources(RuntimeContext(mode="sim")) == []
