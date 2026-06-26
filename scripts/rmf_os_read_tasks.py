#!/usr/bin/env python3
"""OS 侧最简「读取 RMF 任务」—— 直接 HTTP 轮询 api-server REST，不依赖 rmf_msg / ROS / rclpy。

适用：只想让 OS 看到"已发布给 RMF 的转运任务及其状态"，避开 rmf_task_msgs typesupport ABI 问题。
依赖仅 `requests`（conda unilab 已有）。

用法:
    python scripts/rmf_os_read_tasks.py                 # 拉一次
    python scripts/rmf_os_read_tasks.py --watch 3       # 每 3s 刷新
    python scripts/rmf_os_read_tasks.py --label transfer_plan
"""

from __future__ import annotations

import argparse
import time

import requests

JWT_DEFAULT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJzdHViIiwicHJlZmVycmVkX3VzZXJuYW1lIjoiYWRtaW4iLCJpYXQiOjE1MTYyMzkwMjIsImF1ZCI6InJtZl9hcGlfc2VydmVyIiwiaXNzIjoic3R1YiIsImV4cCI6MjA1MTIyMjQwMH0."
    "zzX3zXp467ldkzmLVIadQ_AHr8M5uWVV43n4wEB0OhE"
)


def _get(api_url: str, token: str, path: str):
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(f"{api_url.rstrip('/')}{path}", headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _matches_label(task: dict, label: str) -> bool:
    if not label:
        return True
    labels = (task.get("booking") or {}).get("labels") or []
    return any(label in str(x) for x in labels)


def print_runtime(api_url: str, token: str, label: str, limit: int) -> None:
    """OS 侧读取并打印 RMF **全部运行态**：机器人 + 任务 + 门/梯/工位计数。"""
    fleets = _get(api_url, token, "/fleets") or []
    tasks = _get(api_url, token, f"/tasks?limit={limit}") or []
    try:
        doors = _get(api_url, token, "/doors") or []
        lifts = _get(api_url, token, "/lifts") or []
        disp = _get(api_url, token, "/dispensers") or []
        ing = _get(api_url, token, "/ingestors") or []
    except Exception:  # noqa: BLE001
        doors = lifts = disp = ing = []

    print(f"════════ OS 读取 RMF 全部运行态 @ {time.strftime('%H:%M:%S')} ════════")
    nrob = sum(len(f.get("robots") or {}) for f in fleets)
    print(f"机器人 ({nrob}):")
    for f in fleets:
        fname = f.get("name", "?")
        for rn, rs in (f.get("robots") or {}).items():
            loc = rs.get("location") or {}
            batt = rs.get("battery")
            batt_s = f"{float(batt) * 100:.0f}%" if isinstance(batt, (int, float)) else "?"
            print(
                f"  [{fname}] {rn:<14} {str(rs.get('status') or '?'):<9} "
                f"pos=({loc.get('x', 0):.2f},{loc.get('y', 0):.2f}) yaw={loc.get('yaw', 0):.2f} "
                f"batt={batt_s} task={rs.get('task_id') or '-'}"
            )

    rows = [t for t in tasks if _matches_label(t, label)]
    label_s = f" label≈'{label}'" if label else ""
    print(f"任务 ({len(rows)}){label_s}:")
    for t in rows:
        booking = t.get("booking") or {}
        detail = t.get("detail") if isinstance(t.get("detail"), dict) else {}
        pickup = (detail.get("pickup") or {}).get("place") if isinstance(detail.get("pickup"), dict) else None
        dropoff = (detail.get("dropoff") or {}).get("place") if isinstance(detail.get("dropoff"), dict) else None
        route = f"{pickup}→{dropoff}" if (pickup or dropoff) else ""
        tid = str(booking.get("id") or "?")
        cat = str(t.get("category") or "?")
        status = str(t.get("status") or "?")
        print(f"  - {tid:<22} {cat:<14} {status:<10} {route}")

    print(f"门={len(doors)}  梯={len(lifts)}  取料器(dispenser)={len(disp)}  收料器(ingestor)={len(ing)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="OS 读取 RMF 全部运行态：机器人+任务+门/梯/工位（REST，无 rmf_msg）")
    ap.add_argument("--api-url", default="http://127.0.0.1:8000")
    ap.add_argument("--token", default=JWT_DEFAULT)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--label", default="", help="按 booking.labels 过滤（如 transfer_plan）")
    ap.add_argument("--watch", type=float, default=0.0, help=">0 时每 N 秒刷新")
    args = ap.parse_args()

    while True:
        try:
            print_runtime(args.api_url, args.token, args.label, args.limit)
        except Exception as e:  # noqa: BLE001
            print(f"[OS 读取] 失败: {e}")
        if args.watch <= 0:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
