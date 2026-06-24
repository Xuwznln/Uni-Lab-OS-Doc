#!/usr/bin/env python3
"""验证:① generated device_pair.yaml 的 Phase 1A 兼容;② 无 virtual 时 stub/skip/fail 在 Edge 生效。"""
import rclpy

if not rclpy.ok():
    rclpy.init()

from pathlib import Path

from unilabos.registry.registry import build_registry
from unilabos.registry import pair_registry
from unilabos.registry.pair_registry import PairRegistry
from unilabos.resources.resource_tracker import ResourceDictInstance
from unilabos.ros.initialize_device import initialize_device_from_dict, MissingVirtualDeviceError
from unilabos.sim.context import RuntimeContext

print("== build_registry ==")
build_registry()

# ---------- 第1部分:generated yaml 的 Phase 1A 兼容 ----------
gen = Path.home() / "dev/Uni-Lab-OS/unilabos_data/simulation_pairs/device_pair.generated.yaml"
print(f"\n[1] generated yaml: {gen}  exists={gen.exists()}")
if gen.exists():
    pr = PairRegistry(path=str(gen))  # 用 Phase 1A 同款 PairRegistry 解析
    pe = pr.lookup("incubator.liconic_stx110")
    print(f"[1] PairRegistry.lookup -> real={pe.real} virtual={pe.virtual} engine={pe.engine} "
          f"observed={pe.twin_observed}  => Phase 1A 兼容(同一 PairEntry)")

# ---------- 第2部分:stub/skip/fail 在 Edge 生效 ----------
pair_registry.reset_pair_registry()  # 用仓库默认 device_pair.yaml

def mk(cls):
    return ResourceDictInstance.get_resource_instance_from_dict(
        {"name": "d", "type": "device", "class": cls, "config": {}}
    )

print("\n[2] sim 模式下无 virtual 的策略:")
# stub:未配对的真实类 → 默认 policy=stub → NullDeviceStub 节点
n = initialize_device_from_dict("d_stub", mk("some.unknown.real.device"), runtime=RuntimeContext(mode="sim"))
drv = type(getattr(n, "driver_instance", None)).__name__ if n else None
print(f"  stub  (some.unknown.real.device) -> node driver = {drv}  (期望 NullDeviceStub)")

# skip:device_pair.yaml 里 cameracontroller_device: virtual=null, policy=skip → 返回 None
n = initialize_device_from_dict("d_skip", mk("cameracontroller_device"), runtime=RuntimeContext(mode="sim"))
print(f"  skip  (cameracontroller_device)  -> node = {n}  (期望 None)")

# fail:device_pair.yaml 里 Qone_nmr: virtual=null, policy=fail → 抛 MissingVirtualDeviceError
try:
    initialize_device_from_dict("d_fail", mk("Qone_nmr"), runtime=RuntimeContext(mode="sim"))
    print("  fail  (Qone_nmr)                 -> 没抛异常 ❌")
except MissingVirtualDeviceError:
    print("  fail  (Qone_nmr)                 -> MissingVirtualDeviceError ✅ (期望抛)")

print("\n== 验证完成 ==")
