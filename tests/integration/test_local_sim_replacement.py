"""Local real->virtual replacement (Plan 08 Phase 1A, no cloud):
`unilab --mode sim` replaces a real device with its virtual driver via the
repository `device_pair.yaml` + PairRegistry, fully offline.

Proven here through the real edge entry point `initialize_device_from_dict`:
a node declared as `heaterstirrer.dalong` is instantiated as the virtual
`VirtualHeatChill` driver in sim mode.
"""

import pytest

VIRTUAL_ENTRY = {
    "class": {
        "module": "unilabos.devices.virtual.virtual_heatchill:VirtualHeatChill",
        "type": "python",
        "status_types": {},
        "action_value_mappings": {},
    }
}


@pytest.mark.integration
def test_sim_mode_replaces_real_with_virtual_from_local_pair(ros_context):
    from unilabos.registry import pair_registry
    from unilabos.registry.registry import lab_registry
    from unilabos.resources.resource_tracker import ResourceDictInstance
    from unilabos.ros.initialize_device import initialize_device_from_dict
    from unilabos.sim.context import RuntimeContext

    # repository device_pair.yaml already maps heaterstirrer.dalong -> virtual_heatchill
    pair_registry.reset_pair_registry()
    assert pair_registry.lookup("heaterstirrer.dalong").virtual == "virtual_heatchill"

    lab_registry.device_type_registry["virtual_heatchill"] = dict(VIRTUAL_ENTRY)
    try:
        device_config = ResourceDictInstance.get_resource_instance_from_dict({
            "name": "hs1",
            "type": "device",
            "class": "heaterstirrer.dalong",
            "config": {},
        })
        node = initialize_device_from_dict("hs1", device_config, runtime=RuntimeContext(mode="sim"))

        assert node is not None
        driver = getattr(node, "driver_instance", None)
        from unilabos.devices.virtual.virtual_heatchill import VirtualHeatChill

        assert isinstance(driver, VirtualHeatChill)  # real device replaced by virtual in sim mode
    finally:
        lab_registry.device_type_registry.pop("virtual_heatchill", None)
        pair_registry.reset_pair_registry()
