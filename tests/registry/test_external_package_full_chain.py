"""Plan 09 (B): full local external-package chain —
discover unilabos_registry/ -> load_device_types ($ref expanded) -> construct a
variant via class.init. Mirrors what startup wiring does for `--devices <pkg>`.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from unilabos.registry.external_registry_discovery import discover_registry_paths_from_project
from unilabos.registry.initializer import build_instance_from_registry_entry
from unilabos.registry.registry import Registry

PKG = Path(__file__).parent / "fixtures" / "external_variant_pkg"


def test_external_package_discover_load_construct():
    # 1) discover the package's unilabos_registry/ via pyproject [tool.unilabos.registry]
    paths = discover_registry_paths_from_project(PKG)
    assert paths == [(PKG / "unilabos_registry").resolve()]

    # 2) load it (the real registry loader, with $ref expansion)
    reg = Registry()  # singleton (needs unilabos_msgs -> 4090)
    if reg._startup_executor is None:
        reg._startup_executor = ThreadPoolExecutor(max_workers=2)
    reg.load_device_types(paths[0], complete_registry=False)

    a = reg.device_type_registry["vendor.lh.model_a"]
    b = reg.device_type_registry["vendor.lh.model_b"]
    assert a["class"]["module"].endswith(":SharedDevice")
    assert b["class"]["module"].endswith(":SharedDevice")  # same class, two entries
    assert a["class"]["init"]["kwargs"]["channels"] == 8
    assert b["class"]["init"]["kwargs"]["channels"] == 96
    assert "setup" in a["class"]["action_value_mappings"]  # $ref expanded
    assert "initialized" in b["class"]["status_types"]

    # 3) construct a variant via class.init (shared class, injected config)
    dev_a = build_instance_from_registry_entry(a, node={"id": "lh_a"}, config={"host": "10.0.0.9", "port": 7})
    assert dev_a.name == "lh_a"
    assert dev_a.channels == 8
    assert dev_a.backend.host == "10.0.0.9"
    assert dev_a.deck.name == "model-a-deck"

    dev_b = build_instance_from_registry_entry(b, node={"id": "lh_b"}, config={"host": "10.0.0.9", "port": 7})
    assert dev_b.channels == 96
    assert dev_b.deck.name == "model-b-deck"
