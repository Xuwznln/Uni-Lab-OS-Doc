"""RMF 运行态批量上报 Go 后端 + session 心跳（#18 §4.5 / #17 §5.2 / §6.5）。

- 事件批量 POST `/laboratories/:uuid/rmf/events:batch`，与 `ws_client` 标量状态并行。
- session 注册/更新 POST `/sessions`、PATCH `/sessions/:id`。
- 每个事件携带一致性信封（lab_uuid / scene_hash / rmf_artifact_id / runtime_session_id）。
- 鉴权复用边缘的 `Authorization: Lab base64(ak:sk)` + `EdgeSession`，不引第二套。

网络异常不抛到调用方（仅日志），避免拖垮采集线程；无 requests 时优雅降级。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

try:
    from unilabos.utils.log import logger
except Exception:  # pragma: no cover - 日志兜底
    import logging

    logger = logging.getLogger("rmf.backend_reporter")


class BackendReporter:
    def __init__(
        self,
        lab_uuid: str,
        *,
        remote_addr: Optional[str] = None,
        edge_session: str = "",
        flush_interval_ms: int = 500,
        max_batch: int = 200,
    ):
        self.lab_uuid = lab_uuid
        self.edge_session = edge_session
        self.flush_interval_s = max(0.05, flush_interval_ms / 1000.0)
        self.max_batch = max_batch
        self._remote_addr = remote_addr or self._default_remote_addr()
        self._buffer: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # 一致性信封字段（由 coordinator 在编译/启动后填入）
        self.scene_hash: str = ""
        self.rmf_artifact_id: str = ""
        self.runtime_session_id: str = ""

    @staticmethod
    def _default_remote_addr() -> str:
        try:
            from unilabos.config.config import HTTPConfig

            return getattr(HTTPConfig, "remote_addr", "")
        except Exception:
            return ""

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        try:
            from unilabos.config.config import BasicConfig

            secret = BasicConfig.auth_secret()
            if secret:
                headers["Authorization"] = f"Lab {secret}"
        except Exception:
            pass
        if self.edge_session:
            headers["EdgeSession"] = self.edge_session
        return headers

    def set_consistency(self, scene_hash: str = "", rmf_artifact_id: str = "", runtime_session_id: str = "") -> None:
        if scene_hash:
            self.scene_hash = scene_hash
        if rmf_artifact_id:
            self.rmf_artifact_id = rmf_artifact_id
        if runtime_session_id:
            self.runtime_session_id = runtime_session_id

    def _envelope(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "lab_uuid": self.lab_uuid,
            "edge_session": self.edge_session,
            "scene_hash": self.scene_hash,
            "rmf_artifact_id": self.rmf_artifact_id,
            "runtime_session_id": self.runtime_session_id,
            "event_type": event_type,
            "entity_id": payload.get("robotId") or payload.get("doorName") or payload.get("liftName") or "",
            "payload_json": payload,
            "stale": bool(payload.get("stale", False)),
            "timestamp": int(time.time() * 1000),
        }

    # ----------------------------------------------------------- 采集回调入口
    def enqueue(self, event_type: str, payload: Dict[str, Any]) -> None:
        """供 EventCollector.on_event 使用。缓冲达到 max_batch 时立即上报，否则交给定时 flush。"""
        overflow: List[Dict[str, Any]] = []
        with self._lock:
            self._buffer.append(self._envelope(event_type, payload))
            if len(self._buffer) >= self.max_batch:
                overflow = self._drain_locked()
        if overflow:
            self._post_events(overflow)

    def _drain_locked(self) -> List[Dict[str, Any]]:
        events = self._buffer
        self._buffer = []
        return events

    # ----------------------------------------------------------- 后台 flush
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="rmf-backend-reporter", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.flush()

    def _loop(self) -> None:
        while not self._stop.wait(self.flush_interval_s):
            self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            events = self._drain_locked()
        self._post_events(events)

    def _post_events(self, events: List[Dict[str, Any]]) -> None:
        if not events or not self._remote_addr:
            return
        url = f"{self._remote_addr}/laboratories/{self.lab_uuid}/rmf/events:batch"
        try:
            import requests

            requests.post(url, json={"events": events}, headers=self._headers(), timeout=5)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rmf] events:batch 上报失败 ({len(events)} 条): {e}")

    # ----------------------------------------------------------- session 心跳
    def register_session(self, body: Dict[str, Any]) -> Optional[str]:
        if not self._remote_addr:
            return None
        url = f"{self._remote_addr}/laboratories/{self.lab_uuid}/rmf/sessions"
        try:
            import requests

            resp = requests.post(url, json=body, headers=self._headers(), timeout=5)
            data = resp.json() if resp.content else {}
            self.runtime_session_id = data.get("uuid") or data.get("session_uuid") or self.runtime_session_id
            return self.runtime_session_id
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rmf] session 注册失败: {e}")
            return None

    def patch_session(self, status: str, extra: Optional[Dict[str, Any]] = None) -> None:
        if not self._remote_addr or not self.runtime_session_id:
            return
        url = f"{self._remote_addr}/laboratories/{self.lab_uuid}/rmf/sessions/{self.runtime_session_id}"
        body = {"status": status, "last_heartbeat_at": int(time.time() * 1000), **(extra or {})}
        try:
            import requests

            requests.patch(url, json=body, headers=self._headers(), timeout=5)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rmf] session PATCH 失败: {e}")
