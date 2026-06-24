#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""RMF 集成冒烟 / 联通演示（#18 数据走向 + OS↔RMF 真实通信）。

两类模式：

A) 离线纯逻辑（默认，无需 ROS2 / RMF / 后端）：
    cd Uni-Lab-OS
    python scripts/rmf_smoke_demo.py
    python scripts/rmf_smoke_demo.py --out build/rmf_demo   # 顺便落编译产物
  逐步打印 #18 的数据走向：
    1) 坐标变换（mm→m + Y 翻转 + yaw）
    2) Pascal scene → building.yaml + semantic_map.json + 诊断（编译器）
    3) OS device action → RMF task 信封（go_to / delivery / patrol）
    4) RMF 运行态 → 归一化 DTO（FleetState / DoorState / LiftState）
    5) rmf.coordinator 离线编排（compile_map → dispatch_go_to → query_runtime）

B) 真实联通（需要一套在跑的 RMF / rmf-web，用于 edge 联调）：
  - 经 rmf-web api-server（REST）——首选“先用 rmf-web”路径：
      python scripts/rmf_smoke_demo.py --transport rest \
          --api-server-url http://127.0.0.1:8000 --token <JWT> \
          --fleet unilab_agv --place lounge
    走一次 dispatch_task → 轮询 /tasks/{id}/state → 拉 /fleets 归一化为 Go DTO。
  - 经 ROS task_api_requests（OS 原生路径，需 rclpy + rmf_*_msgs）：
      python scripts/rmf_smoke_demo.py --transport ros \
          --fleet unilab_agv --place lounge --listen 8
    用 dispatcher.attach_ros 真发 task_api_requests，并 event_collector.attach_ros
    订阅 /fleet_states，spin 若干秒把 RobotState 归一化打印。

B 复用与生产同一套 `unilabos/sim/fleet/rmf/` 组件（task_dispatcher / event_collector），
只是把传输从 stub 换成真实 REST / ROS，便于验证“OS 能否真正与 RMF 通信”。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# 确保使用当前工作树的 unilabos（而非可能已安装的旧副本）：把仓库根（scripts/ 的上级）置于最前
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Windows 控制台默认 GBK，会在打印 UTF-8 字符（中文 / 箭头）时报错；强制 UTF-8 输出。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


SEP = "=" * 72


def _title(n: int, text: str) -> None:
    print(f"\n{SEP}\n[{n}] {text}\n{SEP}")


def demo_scene() -> dict:
    """一个最小但完整的 Pascal 发布版 scene（含 charger / dispenser / ingestor）。"""
    return {
        "nodes": [
            {
                "id": "chg", "name": "charger_01", "uuid": "uuid-charger",
                "position": {"x": 1232421, "y": 658567, "z": 0},
                "data": {"rmf": {"workcellType": "charger", "placeId": "charger_01", "enabled": True}},
            },
            {
                "id": "pan", "name": "pantry", "uuid": "uuid-pantry",
                "position": {"x": 1990000, "y": 638364, "z": 0},
                "data": {"rmf": {"workcellType": "dispenser", "placeId": "pantry",
                                 "pickupWaypoint": "coke_dispenser", "enabled": True}},
            },
            {
                "id": "hw2", "name": "hardware_2", "uuid": "uuid-hw2",
                "position": {"x": 2474545, "y": 885636, "z": 0},
                "data": {"rmf": {"workcellType": "ingestor", "placeId": "hardware_2",
                                 "dropoffWaypoint": "coke_ingestor", "enabled": True}},
            },
            {
                "id": "lng", "name": "lounge", "uuid": "uuid-lounge",
                "position": {"x": 1500000, "y": 500000, "z": 0},
                "data": {"rmf": {"workcellType": "dock", "placeId": "lounge", "enabled": True}},
            },
        ]
    }


def demo_robots() -> list:
    return [{
        "robot_name": "agv_sim_01",
        "fleet_name": "unilab_agv",
        "kind": "sim",
        "footprint_radius": 0.35,
        "charger_waypoint": "charger_01",
        "initial_waypoint": "charger_01",
    }]


def step_coordinate_transform() -> None:
    _title(1, "坐标变换 pascal_to_rmf（mm→m, Y 翻转, yaw）— #18 §4.2")
    from unilabos.sim.fleet.rmf.coordinate_transform import pascal_to_rmf

    samples = [
        ("pantry", 1990000, 638364, 0.0),
        ("charger", 1232421, 658567, 1.5708),
    ]
    print(f"{'name':10} {'x_mm':>10} {'y_mm':>10} {'rot_z':>8}   ->   {'x_m':>10} {'y_m':>10} {'yaw':>8}")
    for name, x_mm, y_mm, rot in samples:
        x_m, y_m, yaw = pascal_to_rmf(x_mm, y_mm, rot)
        print(f"{name:10} {x_mm:>10} {y_mm:>10} {rot:>8.4f}   ->   {x_m:>10.3f} {y_m:>10.3f} {yaw:>8.4f}")


def step_compile(out_dir: str | None) -> None:
    _title(2, "编译器 Pascal scene -> building.yaml + semantic_map.json — #18 §4.1")
    from unilabos.sim.fleet.rmf.compiler import compile_scene, dump_building_yaml

    ir, building, semantic = compile_scene(demo_scene(), demo_robots(), lab_uuid="demo_lab", scene_hash="sha256:demo")

    print("\n--- building.yaml（编译产物，cartesian_meters，param=[type_code,val]）---")
    print(dump_building_yaml(ir))

    print("--- semantic_map.json（waypoint<->uuid / chargers / pickups / dropoffs）---")
    print(json.dumps(semantic, ensure_ascii=False, indent=2))

    print("--- 编译诊断（validation）---")
    diags = ir.diagnostics_as_dicts()
    if diags:
        for d in diags:
            print(f"  [{d['level']}] {d['code']}: {d['message']}")
    else:
        print("  (无诊断)")
    print(f"\n  has_errors = {ir.has_errors()}")

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "demo_lab.building.yaml"), "w", encoding="utf-8") as f:
            f.write(dump_building_yaml(ir))
        with open(os.path.join(out_dir, "semantic_map.json"), "w", encoding="utf-8") as f:
            json.dump(semantic, f, ensure_ascii=False, indent=2)
        print(f"\n  已写入: {os.path.abspath(out_dir)}")


def step_task_envelopes() -> None:
    _title(3, "OS device action -> RMF task 信封（task_api_requests）— #18 §2.5 / §4.3")
    from unilabos.sim.fleet.rmf.task_dispatcher import (
        build_delivery_request,
        build_go_to_request,
        build_patrol_request,
    )

    print("\n--- dispatch_go_to(place=lounge, orientation_deg=90) ---")
    print(json.dumps(build_go_to_request("lounge", orientation_deg=90), ensure_ascii=False, indent=2))

    print("\n--- dispatch_delivery(pantry/coke_dispenser -> hardware_2/coke_ingestor) ---")
    print(json.dumps(
        build_delivery_request("pantry", "coke_dispenser", "hardware_2", "coke_ingestor",
                               payload=[{"sku": "coke", "quantity": 1}]),
        ensure_ascii=False, indent=2))

    print("\n--- dispatch_patrol(places=[lounge, pantry], rounds=2) ---")
    print(json.dumps(build_patrol_request(["lounge", "pantry"], rounds=2), ensure_ascii=False, indent=2))


def step_event_normalization() -> None:
    _title(4, "RMF 运行态 -> 归一化 DTO（FleetState / Door / Lift）— #18 §4.4")
    from unilabos.sim.fleet.rmf.event_collector import (
        normalize_door_state,
        normalize_fleet_state,
        normalize_lift_state,
    )

    fleet = {
        "name": "unilab_agv",
        "robots": [{
            "name": "agv_sim_01", "task_id": "go_to-001",
            "battery_percent": 87.5,         # ROS msg 0-100
            "mode": {"mode": 2},             # 2 = MOVING
            "location": {"x": 12.34, "y": 5.67, "yaw": 1.5708, "level_name": "L1"},
        }],
    }
    print("\n--- FleetState -> RmfRobotStateDTO（battery 0-100->0-1, mode 2->moving）---")
    print(json.dumps(normalize_fleet_state(fleet, runtime_mode="sim"), ensure_ascii=False, indent=2))

    print("\n--- DoorState(value=2) -> open ---")
    print(json.dumps(normalize_door_state({"door_name": "main_door", "current_mode": {"value": 2}}),
                     ensure_ascii=False, indent=2))

    print("\n--- LiftState(door=1, motion=1) -> moving/up ---")
    print(json.dumps(normalize_lift_state({
        "lift_name": "Lift1", "current_floor": "L1", "destination_floor": "L3",
        "door_state": 1, "motion_state": 1, "session_id": "s1",
    }), ensure_ascii=False, indent=2))


def step_coordinator(out_dir: str | None) -> None:
    _title(5, "rmf.coordinator 离线编排（compile_map -> dispatch_go_to -> query_runtime）— #18 §6.2")
    from unilabos.devices.agv.rmf_coordinator import RmfCoordinator

    gen_dir = out_dir or os.path.join("build", "rmf_demo_coordinator")
    coord = RmfCoordinator(lab_uuid="demo_lab", fleet_name="unilab_agv", generated_map_dir=gen_dir)

    r = coord.compile_map(scene=demo_scene(), robots=demo_robots(), scene_hash="sha256:demo")
    print(f"\n  compile_map  -> success={r['success']} artifact={r['artifact_id']} 诊断数={len(r['diagnostics'])}")

    d = coord.dispatch_go_to(place="lounge", orientation_deg=90)
    print(f"  dispatch_go_to -> success={d['success']} task_id={d['task_id']}")
    print("  （上一行日志里的 'task_api_requests <- ...' 即下发的真实信封）")

    print(f"\n  runtime_status = {coord.runtime_status}")
    print(f"  scene_hash     = {coord.scene_hash}")
    print(f"  map_version    = {coord.map_version}")
    print("  query_runtime  =", json.dumps(coord.query_runtime(), ensure_ascii=False))
    print(f"\n  编译产物目录: {os.path.abspath(gen_dir)}")


def step_live_rest(
    api_server_url: str,
    *,
    token: str | None,
    fleet: str | None,
    robot: str | None,
    place: str,
    orientation_deg: float | None,
    poll: int,
) -> None:
    """经 rmf-web api-server（REST）真实往返：dispatch → 轮询 task state → 拉 fleets 归一化。"""
    import time

    import requests  # 现有依赖（rmf_coordinator._fetch_published_scene 已用）

    from unilabos.sim.fleet.rmf.event_collector import (
        normalize_door_state,
        normalize_fleet_state,
        normalize_lift_state,
    )
    from unilabos.sim.fleet.rmf.task_dispatcher import build_go_to_request

    base = api_server_url.rstrip("/")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    _title(6, f"真实联通(REST) -> rmf-web api-server {base} — #18 §5.4 / §2.6")

    # 1) 组装与生产同款信封并 POST /tasks/dispatch_task
    envelope = build_go_to_request(place, orientation_deg, fleet=fleet or None, robot=robot or None)
    print("\n--- POST /tasks/dispatch_task（信封由 build_go_to_request 生成，与 ROS 路径同源）---")
    print(json.dumps(envelope, ensure_ascii=False, indent=2))
    resp = requests.post(f"{base}/tasks/dispatch_task", json=envelope, headers=headers, timeout=10)
    print(f"  HTTP {resp.status_code}")
    body = resp.json() if resp.content else {}
    print(f"  resp: {json.dumps(body, ensure_ascii=False)[:600]}")

    # 提取 task_id（成功时 state.booking.id；失败打印 errors）
    task_id = ""
    state = body.get("state") if isinstance(body, dict) else None
    if isinstance(state, dict):
        task_id = (state.get("booking") or {}).get("id", "")
    if not task_id and isinstance(body, dict) and body.get("errors"):
        print(f"  [WARN] dispatch 失败: {body['errors']}")

    # 2) 轮询 /tasks/{id}/state
    if task_id:
        print(f"\n--- 轮询 /tasks/{task_id}/state（{poll} 次）---")
        for i in range(max(1, poll)):
            time.sleep(1.0)
            r = requests.get(f"{base}/tasks/{task_id}/state", headers=headers, timeout=10)
            ts = r.json() if r.content else {}
            status = ts.get("status") if isinstance(ts, dict) else None
            print(f"  [{i + 1}] status={status}")

    # 3) GET /fleets → 用生产同款 normalize_fleet_state 归一化为 Go DTO
    print("\n--- GET /fleets -> normalize_fleet_state（Go DTO）---")
    r = requests.get(f"{base}/fleets", headers=headers, timeout=10)
    fleets = r.json() if r.content else []
    for f in fleets if isinstance(fleets, list) else []:
        for dto in normalize_fleet_state(f, runtime_mode="real"):
            print(f"  {json.dumps(dto, ensure_ascii=False)}")

    # 4) 门/电梯（best-effort，环境无则跳过）
    try:
        doors = requests.get(f"{base}/doors", headers=headers, timeout=5).json()
        for d in doors if isinstance(doors, list) else []:
            name = d.get("name") if isinstance(d, dict) else None
            if not name:
                continue
            st = requests.get(f"{base}/doors/{name}/state", headers=headers, timeout=5).json()
            print("  door:", json.dumps(normalize_door_state(st), ensure_ascii=False))
        lifts = requests.get(f"{base}/lifts", headers=headers, timeout=5).json()
        for lf in lifts if isinstance(lifts, list) else []:
            name = lf.get("name") if isinstance(lf, dict) else None
            if not name:
                continue
            st = requests.get(f"{base}/lifts/{name}/state", headers=headers, timeout=5).json()
            print("  lift:", json.dumps(normalize_lift_state(st), ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        print(f"  (门/电梯查询跳过: {e})")


def step_live_ros(
    *,
    fleet: str | None,
    robot: str | None,
    place: str,
    orientation_deg: float | None,
    listen: float,
) -> None:
    """经 ROS task_api_requests（OS 原生路径）真实往返：真发信封 + 订阅 /fleet_states 归一化。"""
    import time

    import rclpy

    from unilabos.sim.fleet.rmf.event_collector import EventCollector
    from unilabos.sim.fleet.rmf.task_dispatcher import RmfTaskDispatcher, build_go_to_request

    _title(6, "真实联通(ROS) -> task_api_requests + /fleet_states — #18 §2.5 / §4.4")

    rclpy.init()
    node = rclpy.create_node("unilab_rmf_smoke_demo")
    try:
        dispatcher = RmfTaskDispatcher()
        dispatcher.attach_ros(node)  # 真发 rmf_task_msgs/ApiRequest 到 task_api_requests

        collected: list = []
        collector = EventCollector(on_event=lambda t, p: collected.append((t, p)), runtime_mode="real")
        collector.attach_ros(node)  # 订阅 /fleet_states /door_states /lift_states

        envelope = build_go_to_request(place, orientation_deg, fleet=fleet or None, robot=robot or None)
        rid = dispatcher.dispatch(envelope)
        print(f"\n  已 publish task_api_requests, request_id={rid}")
        print(f"  envelope: {json.dumps(envelope, ensure_ascii=False)}")

        print(f"\n--- spin {listen:.0f}s 收集 /fleet_states 归一化事件 ---")
        deadline = time.time() + listen
        while time.time() < deadline:
            rclpy.spin_once(node, timeout_sec=0.2)

        if not collected:
            print("  (未收到状态：确认 RMF fleet adapter 在跑、topic 名一致)")
        seen = {}
        for etype, payload in collected:
            key = (etype, payload.get("robotId") or payload.get("doorName") or payload.get("liftName"))
            seen[key] = payload  # 去重打印最新
        for (etype, _), payload in seen.items():
            print(f"  {etype}: {json.dumps(payload, ensure_ascii=False)}")
    finally:
        node.destroy_node()
        rclpy.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description="RMF 集成冒烟 / 联通演示")
    parser.add_argument("--out", default=None, help="把编译产物写到该目录（可选）")
    parser.add_argument("--only", type=int, default=0, help="离线模式下只跑某一步（1-5），默认全跑")
    parser.add_argument(
        "--transport",
        choices=["offline", "rest", "ros"],
        default="offline",
        help="offline=离线纯逻辑(默认); rest=经 rmf-web api-server; ros=经 task_api_requests",
    )
    parser.add_argument("--api-server-url", default="http://127.0.0.1:8000", help="rmf-web api-server 地址（--transport rest）")
    parser.add_argument("--token", default=None, help="rmf-web JWT（--transport rest，stub 鉴权时传 StubAuthenticator admin token）")
    parser.add_argument("--fleet", default=None, help="目标 fleet 名（不填则交给 RMF 自动分派）")
    parser.add_argument("--robot", default=None, help="目标 robot 名（与 --fleet 同填则直派单机）")
    parser.add_argument("--place", default="lounge", help="go_to 目标 waypoint 名")
    parser.add_argument("--orientation-deg", type=float, default=None, help="go_to 目标朝向（度，可选）")
    parser.add_argument("--poll", type=int, default=5, help="REST 模式轮询 task state 次数")
    parser.add_argument("--listen", type=float, default=8.0, help="ROS 模式 spin 收集状态秒数")
    args = parser.parse_args()

    try:
        if args.transport == "rest":
            print("RMF 真实联通演示（REST / rmf-web api-server）")
            step_live_rest(
                args.api_server_url,
                token=args.token,
                fleet=args.fleet,
                robot=args.robot,
                place=args.place,
                orientation_deg=args.orientation_deg,
                poll=args.poll,
            )
        elif args.transport == "ros":
            print("RMF 真实联通演示（ROS / task_api_requests）")
            step_live_ros(
                fleet=args.fleet,
                robot=args.robot,
                place=args.place,
                orientation_deg=args.orientation_deg,
                listen=args.listen,
            )
        else:
            steps = {
                1: lambda: step_coordinate_transform(),
                2: lambda: step_compile(args.out),
                3: lambda: step_task_envelopes(),
                4: lambda: step_event_normalization(),
                5: lambda: step_coordinator(args.out),
            }
            print("RMF 集成离线冒烟演示（#18 §6 实现）")
            print("无需 ROS2 / RMF runtime / 后端；纯逻辑全链路。")
            if args.only in steps:
                steps[args.only]()
            else:
                for i in range(1, 6):
                    steps[i]()
    except Exception as e:  # noqa: BLE001
        print(f"\n[FAILED] {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return 1

    print(f"\n{SEP}\n[OK] 完成。\n{SEP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
