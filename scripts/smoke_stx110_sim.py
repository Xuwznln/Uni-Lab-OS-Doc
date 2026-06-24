#!/usr/bin/env python3
"""CP1 smoke: 走真实 initialize_device 路径验证 08 sim 替换 + action 驱动。

incubator.liconic_stx110 (real) --sim--> VirtualLiconicStx110 (virtual)，
经 ros2_device_node 实例化为真实设备节点，driver.load_plate 驱动 /joint_states。
"""
import asyncio
import math
import sys


def main() -> int:
    import rclpy

    if not rclpy.ok():
        rclpy.init()

    from unilabos.registry.registry import build_registry, lab_registry
    from unilabos.registry import pair_registry
    from unilabos.resources.resource_tracker import ResourceDictInstance
    from unilabos.ros.initialize_device import initialize_device_from_dict
    from unilabos.sim.context import RuntimeContext
    from unilabos.devices.virtual.virtual_liconic_stx110 import VirtualLiconicStx110

    print("== build_registry ==")
    build_registry()
    pair_registry.reset_pair_registry()
    pe = pair_registry.lookup("incubator.liconic_stx110")
    assert pe.virtual == "virtual_liconic_stx110", pe
    print(f"[08] pair: {pe.real} -> {pe.virtual}  engine={pe.engine}  observed={pe.twin_observed}")
    assert "virtual_liconic_stx110" in lab_registry.device_type_registry, "virtual class not registered"

    dc = ResourceDictInstance.get_resource_instance_from_dict({
        "name": "liconic_stx110",
        "type": "device",
        "class": "incubator.liconic_stx110",
        "config": {"target_temperature": 37.0},
    })
    print("== initialize_device_from_dict (mode=sim) ==")
    node = initialize_device_from_dict("liconic_stx110", dc, runtime=RuntimeContext(mode="sim"))
    assert node is not None, "node is None"
    driver = getattr(node, "driver_instance", None)
    assert isinstance(driver, VirtualLiconicStx110), f"driver is {type(driver)}"
    print(f"[sim] real incubator.liconic_stx110 -> {type(driver).__name__}  (真实替换 OK)")

    print("== driver.initialize (起关节发布器) ==")
    driver.initialize()

    async def seq():
        for s in [1, 3, 5, 2]:
            await driver.load_plate(s)
            ang = round(math.degrees(driver._carousel_angle), 1)
            print(f"  load_plate({s}) -> carousel_angle={ang}°  data.status={driver.data.get('status')}")
            assert abs(ang - (s - 1) * 72) < 0.5, f"angle mismatch slot {s}: {ang}"
            await asyncio.sleep(0.2)
    asyncio.run(seq())

    print("\n== CP1 GREEN: 08 替换 + initialize_device + load_plate 全通 ==")
    driver.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
