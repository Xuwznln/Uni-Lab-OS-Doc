"""M-8: AST scanner passes through virtual driver marks (no import needed)."""

from concurrent.futures import ThreadPoolExecutor

from unilabos.registry.ast_registry_scanner import scan_directory

MODULE_SRC = '''
from unilabos.registry.decorators import device


@device(
    id="virtual_x_ast",
    category=["heaterstirrer", "virtual_device"],
    driver_runtime_kind="virtual",
    simulation_kind="mock",
    supported_modes=["sim", "twin"],
    sim_engine="none",
)
class VirtualXAst:
    pass


@device(id="real_y_ast", category=["pump"])
class RealYAst:
    pass
'''


def test_scanner_extracts_virtual_marks(tmp_path):
    f = tmp_path / "mydrivers.py"
    f.write_text(MODULE_SRC, encoding="utf-8")

    with ThreadPoolExecutor(max_workers=2) as ex:
        result = scan_directory(tmp_path, include_files=[f], executor=ex)

    devices = result["devices"]
    assert "virtual_x_ast" in devices
    v = devices["virtual_x_ast"]
    assert v["driver_runtime_kind"] == "virtual"
    assert v["simulation_kind"] == "mock"
    assert v["supported_modes"] == ["sim", "twin"]
    assert v["sim_engine"] == "none"

    r = devices["real_y_ast"]
    assert r["driver_runtime_kind"] == "real"
    assert r["simulation_kind"] is None
    assert r["supported_modes"] == []


def test_yaml_legacy_top_level_fields_preserved(tmp_path):
    """Plan 08 §6.3.1(4): YAML legacy registry keeps top-level sim marks via yaml.safe_load."""
    import yaml

    src = (
        "virtual_dalong_heaterstirrer:\n"
        "  category: [heaterstirrer, virtual_device]\n"
        "  driver_runtime_kind: virtual\n"
        "  simulation_kind: mock\n"
        "  supported_modes: [sim, twin]\n"
        "  sim_engine: none\n"
        "  class:\n"
        "    module: community.dalong.virtual:VirtualDalongHeaterStirrer\n"
    )
    loaded = yaml.safe_load(src)["virtual_dalong_heaterstirrer"]
    # the loader (registry._load_single_device_file) stores device_config as-is,
    # so these top-level keys survive into the upload payload unchanged.
    for key in ("driver_runtime_kind", "simulation_kind", "supported_modes", "sim_engine"):
        assert key in loaded
