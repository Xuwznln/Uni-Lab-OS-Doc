#!/usr/bin/env python3
"""ensure_latest_map 生成 nav_graph 之后再打补丁（RMF 原生图层属性）。

run_rmf_layout.sh 会先重新生成 nav_graphs/0.yaml，若在生成前修改会被覆盖。
本脚本必须在 ensure_latest_map 之后、building_map_server 启动之前执行：
- 去掉 star_* 的 is_holding_point（避免主干交汇驻停）
- 去掉非充电 dock_* 的 is_holding_point（避免同 dock 叠停）
- 给已确认冲突的 star 交汇点接入车道加独立 mutex（RMF 原生互斥）
- 清理本工具链注入过的临时 mutex，避免跨轮污染
"""

from __future__ import annotations

import sys
from pathlib import Path

# 全部 star 交汇点：顶点 + 接入车道共用 mutex。
_STAR_JUNCTION_MUTEX_TARGETS = (
    "star_l0_left",
    "star_l0_right",
    "star_l1_left",
    "star_l1_right",
    "star_l2_left",
    "star_l2_right",
    "star_l3_left",
    "star_l3_right",
    "star_l4_left",
    "star_l4_right",
    "star_l5_left",
    "star_l5_right",
)


def _load_yaml(path: Path):
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _dump_yaml(path: Path, data) -> None:
    import yaml

    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


# 竖向主干走廊（同 x 坐标、star+充电 dock 叠列）：统一 mutex，避免不同 star_junction
# 分段锁导致 mock 直线插值穿越其他 star 点。
_SPINE_COLUMNS: tuple[tuple[float, str], ...] = (
    (41.718999999999994, "left_spine_mutex"),
    (61.718999999999994, "right_spine_mutex"),
)
_SPINE_X_TOL = 0.02
_ROW_Y_TOL = 0.02


def _row_key_from_star_name(name: str) -> str | None:
    parts = str(name or "").split("_")
    if len(parts) >= 2 and parts[0] == "star":
        return f"{parts[0]}_{parts[1]}"
    return None


def _is_injected_mutex(mutex: str) -> bool:
    if not mutex:
        return False
    if mutex in {
        "hotspot_right_mutex",
        "star_corridor_mutex",
        "hotspot_focus_mutex",
        "left_spine_mutex",
        "right_spine_mutex",
    }:
        return True
    return mutex.endswith("_row_mutex") or (
        mutex.startswith("dock_access_")
        or mutex.startswith("star_junction_")
        or mutex.startswith("dock_junction_")
    )


def patch_nav_graph(map_dir: str) -> dict[str, int]:
    nav_path = Path(map_dir).expanduser().resolve() / "nav_graphs" / "0.yaml"
    if not nav_path.is_file():
        return {
            "star_holding": 0,
            "dock_holding": 0,
            "mutex_cleared": 0,
            "charge_parking": 0,
            "star_mutex": 0,
            "dock_mutex": 0,
        }

    graph = _load_yaml(nav_path)
    level = ((graph.get("levels") or {}).get("L1") or {})
    verts = level.get("vertices") or []
    lanes = level.get("lanes") or []

    star_holding = 0
    dock_holding = 0
    charge_parking = 0
    star_idx: dict[str, int] = {}
    dock_idx: dict[str, int] = {}
    for idx, v in enumerate(verts):
        if not (isinstance(v, list) and len(v) >= 3 and isinstance(v[2], dict)):
            continue
        props = v[2]
        name = str(props.get("name") or "")
        if name.startswith("star_"):
            star_idx[name] = idx
            if props.get("is_holding_point"):
                props.pop("is_holding_point", None)
                star_holding += 1
            # 主干交汇点禁止作为 parking，避免空闲车占路口。
            if props.get("is_parking_spot"):
                props.pop("is_parking_spot", None)
            continue
        if not name.startswith("dock_"):
            continue
        dock_idx[name] = idx
        is_charge = name.startswith("dock_charge_") or bool(props.get("is_charger"))
        if is_charge:
            # finishing_request=charge 需要充电点同时是 parking spot。
            if not props.get("is_parking_spot"):
                props["is_parking_spot"] = True
                charge_parking += 1
            if not props.get("is_holding_point"):
                props["is_holding_point"] = True
            continue
        if props.get("is_holding_point"):
            props.pop("is_holding_point", None)
            dock_holding += 1
        if props.get("is_parking_spot"):
            props.pop("is_parking_spot", None)

    mutex_cleared = 0
    for v in verts:
        if not (isinstance(v, list) and len(v) >= 3 and isinstance(v[2], dict)):
            continue
        props = v[2]
        mutex = str(props.get("mutex") or "")
        if _is_injected_mutex(mutex):
            props.pop("mutex", None)
            mutex_cleared += 1
    for lane in lanes:
        if not (isinstance(lane, list) and len(lane) >= 3 and isinstance(lane[2], dict)):
            continue
        props = lane[2]
        mutex = str(props.get("mutex") or "")
        if _is_injected_mutex(mutex):
            props.pop("mutex", None)
            mutex_cleared += 1

    # 对热点 star：顶点 + 接入车道写入同一 mutex（RMF 原生互斥语义）。
    star_mutex = 0
    target_idx = {
        name: star_idx[name]
        for name in _STAR_JUNCTION_MUTEX_TARGETS
        if name in star_idx
    }
    for name, idx in target_idx.items():
        v = verts[idx]
        if not (isinstance(v, list) and len(v) >= 3 and isinstance(v[2], dict)):
            continue
        props = v[2]
        target = f"star_junction_{name}"
        current = str(props.get("mutex") or "")
        if current and not _is_injected_mutex(current):
            continue
        if current != target:
            props["mutex"] = target
            star_mutex += 1

    for lane in lanes:
        if not (isinstance(lane, list) and len(lane) >= 3 and isinstance(lane[2], dict)):
            continue
        try:
            src = int(lane[0])
            dst = int(lane[1])
        except Exception:  # noqa: BLE001
            continue
        hit_name = None
        for name, idx in target_idx.items():
            if src == idx or dst == idx:
                hit_name = name
                break
        if hit_name is None:
            continue
        props = lane[2]
        # 保留地图原有非本工具链 mutex。
        current = str(props.get("mutex") or "")
        if current and not _is_injected_mutex(current):
            continue
        target = f"star_junction_{hit_name}"
        if current != target:
            props["mutex"] = target
            star_mutex += 1

    # 每个 dock：顶点必须有独立 mutex。
    # 接入车道：若对端是 star，保留 star mutex（否则会拆掉交汇互斥，两车仍可同占 star）；
    # 仅对非 star 对端车道写入 dock mutex。
    dock_mutex = 0
    star_indices = set(star_idx.values())
    for name, idx in dock_idx.items():
        v = verts[idx]
        if not (isinstance(v, list) and len(v) >= 3 and isinstance(v[2], dict)):
            continue
        props = v[2]
        target = f"dock_junction_{name}"
        current = str(props.get("mutex") or "")
        if current and not _is_injected_mutex(current):
            continue
        if current != target:
            props["mutex"] = target
            dock_mutex += 1

    for lane in lanes:
        if not (isinstance(lane, list) and len(lane) >= 3 and isinstance(lane[2], dict)):
            continue
        try:
            src = int(lane[0])
            dst = int(lane[1])
        except Exception:  # noqa: BLE001
            continue
        dock_name = None
        other = None
        for name, idx in dock_idx.items():
            if src == idx:
                dock_name = name
                other = dst
                break
            if dst == idx:
                dock_name = name
                other = src
                break
        if dock_name is None or other is None:
            continue
        if other in star_indices:
            continue
        props = lane[2]
        current = str(props.get("mutex") or "")
        if current and not _is_injected_mutex(current):
            continue
        target = f"dock_junction_{dock_name}"
        if current != target:
            props["mutex"] = target
            dock_mutex += 1

    # 竖向 spine：同列 star / 充电 dock 及其直连车道共用列级 mutex。
    spine_mutex = 0

    def _spine_mutex_for_x(x: float) -> str | None:
        for spine_x, mutex_name in _SPINE_COLUMNS:
            if abs(float(x) - spine_x) <= _SPINE_X_TOL:
                return mutex_name
        return None

    for v in verts:
        if not (isinstance(v, list) and len(v) >= 3 and isinstance(v[2], dict)):
            continue
        target = _spine_mutex_for_x(v[0])
        if not target:
            continue
        props = v[2]
        name = str(props.get("name") or "")
        if not (
            name.startswith("star_")
            or name.startswith("dock_charge_")
            or bool(props.get("is_charger"))
        ):
            continue
        if str(props.get("mutex") or "") != target:
            props["mutex"] = target
            spine_mutex += 1

    for lane in lanes:
        if not (isinstance(lane, list) and len(lane) >= 3 and isinstance(lane[2], dict)):
            continue
        try:
            src = int(lane[0])
            dst = int(lane[1])
        except Exception:  # noqa: BLE001
            continue
        v0, v1 = verts[src], verts[dst]
        m0 = _spine_mutex_for_x(v0[0])
        m1 = _spine_mutex_for_x(v1[0])
        if not m0 or m0 != m1:
            continue
        props = lane[2]
        if str(props.get("mutex") or "") != m0:
            props["mutex"] = m0
            spine_mutex += 1

    # 水平 star 行（同 y 的 dock 带）：统一 row mutex，避免 mock 直线插值同 y 并行穿透。
    row_mutex = 0
    row_y_by_key: dict[str, float] = {}
    for name, idx in star_idx.items():
        row_key = _row_key_from_star_name(name)
        if not row_key:
            continue
        v = verts[idx]
        if isinstance(v, list) and len(v) >= 2:
            row_y_by_key[row_key] = float(v[1])

    def _row_mutex_for_y(y: float) -> str | None:
        for row_key, ry in row_y_by_key.items():
            if abs(float(y) - float(ry)) <= _ROW_Y_TOL:
                return f"{row_key}_row_mutex"
        return None

    for v in verts:
        if not (isinstance(v, list) and len(v) >= 3 and isinstance(v[2], dict)):
            continue
        target = _row_mutex_for_y(v[1])
        if not target:
            continue
        props = v[2]
        if str(props.get("mutex") or "") != target:
            props["mutex"] = target
            row_mutex += 1

    for lane in lanes:
        if not (isinstance(lane, list) and len(lane) >= 3 and isinstance(lane[2], dict)):
            continue
        try:
            src = int(lane[0])
            dst = int(lane[1])
        except Exception:  # noqa: BLE001
            continue
        v0, v1 = verts[src], verts[dst]
        m0 = _row_mutex_for_y(v0[1])
        m1 = _row_mutex_for_y(v1[1])
        if not m0 or m0 != m1:
            continue
        props = lane[2]
        if str(props.get("mutex") or "") != m0:
            props["mutex"] = m0
            row_mutex += 1

    if (
        star_holding
        or dock_holding
        or mutex_cleared
        or charge_parking
        or star_mutex
        or dock_mutex
        or spine_mutex
        or row_mutex
    ):
        _dump_yaml(nav_path, graph)
    return {
        "star_holding": star_holding,
        "dock_holding": dock_holding,
        "mutex_cleared": mutex_cleared,
        "charge_parking": charge_parking,
        "star_mutex": star_mutex,
        "dock_mutex": dock_mutex,
        "spine_mutex": spine_mutex,
        "row_mutex": row_mutex,
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: patch_nav_graph_holding.py <map_dir>", file=sys.stderr)
        return 2
    stats = patch_nav_graph(args[0])
    print(
        "[patch_nav_graph] "
        f"removed star_holding={stats['star_holding']} "
        f"dock_holding={stats['dock_holding']} "
        f"charge_parking={stats.get('charge_parking', 0)} "
        f"star_mutex={stats.get('star_mutex', 0)} "
        f"dock_mutex={stats.get('dock_mutex', 0)} "
        f"spine_mutex={stats.get('spine_mutex', 0)} "
        f"row_mutex={stats.get('row_mutex', 0)} "
        f"cleared_mutex={stats['mutex_cleared']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
