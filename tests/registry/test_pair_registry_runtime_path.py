"""M-4: init_pair_registry points the global registry at a runtime YAML."""

from unilabos.registry.pair_registry import (
    get_pair_registry,
    init_pair_registry,
    lookup,
    reset_pair_registry,
)


def test_init_pair_registry_runtime_path(tmp_path):
    runtime = tmp_path / "device_pair.generated.yaml"
    runtime.write_text(
        "pairs:\n"
        "  - real: dalong_heaterstirrer\n"
        "    virtual: community.dalong.virtual_heaterstirrer\n"
        "    missing_sim_policy: stub\n",
        encoding="utf-8",
    )
    try:
        reg = init_pair_registry(runtime)
        assert reg is get_pair_registry()
        # global lookup (used by initialize_device) now resolves via the runtime file
        assert lookup("dalong_heaterstirrer").virtual == "community.dalong.virtual_heaterstirrer"
        # unknown class falls back to default policy stub, virtual None
        miss = lookup("unknown_device")
        assert miss.virtual is None and miss.missing_sim_policy == "stub"
    finally:
        reset_pair_registry()


def test_reset_pair_registry_restores_default():
    reset_pair_registry()
    # get_pair_registry should lazily build the repo-default registry without error
    assert get_pair_registry() is not None
    reset_pair_registry()
