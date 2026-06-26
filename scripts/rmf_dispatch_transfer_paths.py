#!/usr/bin/env python3
"""把 rmf_transfer_paths.json（#18 §9.9）按时间窗口发布给 RMF（api-server REST），OS 监听器可读回。

用法:
    python scripts/rmf_dispatch_transfer_paths.py \\
        --paths ../.rmf_run_logs/maps/latest/rmf_transfer_paths.json \\
        --api-url http://127.0.0.1:8000 --ready-from 0 --ready-to 120 --max 5 \\
        --fleet unilab_agv --robot unilab_agv1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unilabos.sim.fleet.rmf.transfer_dispatcher import (
    build_delivery_envelopes_from_paths,
    build_patrol_envelopes_from_paths,
)

JWT_DEFAULT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJzdHViIiwicHJlZmVycmVkX3VzZXJuYW1lIjoiYWRtaW4iLCJpYXQiOjE1MTYyMzkwMjIsImF1ZCI6InJtZl9hcGlfc2VydmVyIiwiaXNzIjoic3R1YiIsImV4cCI6MjA1MTIyMjQwMH0."
    "zzX3zXp467ldkzmLVIadQ_AHr8M5uWVV43n4wEB0OhE"
)


def main() -> None:
    ap = argparse.ArgumentParser(description="发布 rmf_transfer_paths → RMF（窗口下发）")
    ap.add_argument("--paths", required=True)
    ap.add_argument("--api-url", default="http://127.0.0.1:8000")
    ap.add_argument("--token", default=JWT_DEFAULT)
    ap.add_argument("--ready-from", type=int, default=None)
    ap.add_argument("--ready-to", type=int, default=None)
    ap.add_argument("--max", type=int, default=5)
    ap.add_argument("--fleet", default="unilab_agv")
    ap.add_argument("--robot", default="unilab_agv1")
    ap.add_argument(
        "--mode",
        choices=["delivery", "patrol"],
        default="delivery",
        help="delivery=取放料（需真实 dispenser，sim 中常不出价）；patrol=沿转运路线行驶（sim 中可实跑）",
    )
    ap.add_argument(
        "--start-asap",
        dest="start_asap",
        action="store_true",
        default=True,
        help="把 earliest_start_time 置 0 立即开始（sim use_sim_time 必须，默认开）",
    )
    ap.add_argument("--no-start-asap", dest="start_asap", action="store_false", help="保留墙钟 start（仅真实时钟 RMF）")
    ap.add_argument("--dry-run", action="store_true", help="只生成信封不实际下发")
    args = ap.parse_args()

    transfer_paths = json.loads(Path(args.paths).read_text(encoding="utf-8"))
    builder = build_patrol_envelopes_from_paths if args.mode == "patrol" else build_delivery_envelopes_from_paths
    envelopes = builder(
        transfer_paths,
        ready_min_from=args.ready_from,
        ready_min_to=args.ready_to,
        max_count=args.max,
        fleet=args.fleet,
        robot=args.robot,
    )
    if args.start_asap:
        # 统一到 sim 时钟：earliest_start=0（永远是 sim 过去）→ 立即开始（#21 §7.4 时钟统一）
        for env in envelopes:
            req = env.get("request") or {}
            req["unix_millis_earliest_start_time"] = 0
            req["unix_millis_request_time"] = 0
    print(
        f"窗口内 {args.mode} 信封: {len(envelopes)}（共 {len(transfer_paths.get('transfers') or [])} 条 transfer，"
        f"start_asap={args.start_asap}）"
    )
    if args.dry_run:
        if envelopes:
            print("样例信封:", json.dumps(envelopes[0], ensure_ascii=False)[:400])
        return

    import requests

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {args.token}"}
    ok = 0
    for env in envelopes:
        try:
            resp = requests.post(
                f"{args.api_url.rstrip('/')}/tasks/dispatch_task", json=env, headers=headers, timeout=15
            )
            body = resp.json() if resp.content else {}
            tid = ""
            if isinstance(body, dict):
                tid = str(((body.get("state") or {}).get("booking") or {}).get("id") or "")
            if resp.status_code == 200:
                ok += 1
                print(f"  ✓ dispatched task_id={tid}")
            else:
                print(f"  ✗ HTTP {resp.status_code}: {str(body)[:200]}")
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {e}")
    print(f"成功下发 {ok}/{len(envelopes)} → {args.api_url}")


if __name__ == "__main__":
    main()
