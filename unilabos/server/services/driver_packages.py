"""驱动包（设备包）管理：安装台账、安装 / 卸载操作与启动期挂载。

驱动包 = 含 ``@device`` / ``@resource`` 的 Python 分发（pip 规格、git URL 或本地目录）。
本模块把 ``unilab package install`` 的能力搬到管理 API 上，并补上两件 CLI 没有的事：

- **台账**：``<working_dir>/driver_packages.json``（working_dir 即 unilabos_data 目录）记录每个包的分发名、
  安装规格、版本、包目录、扫描到的设备类与启用状态；
- **启动期挂载**：``enabled_package_dirs()`` 把已启用的包目录并入 ``--devices`` 扫描
  目录，安装后只要重启进程（``POST /api/v1/restart``）驱动即可用。

安装 / 卸载是后台线程里的长操作，用 operation 记录进度与日志供前端轮询。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from unilabos.utils import logger

LEDGER_FILENAME = "driver_packages.json"
CATALOG_FILENAME = "driver_package_catalog.json"
OPERATION_HISTORY_LIMIT = 30
INSTALL_TIMEOUT_S = 900
CATALOG_FETCH_TIMEOUT_S = 8


class DriverPackageError(RuntimeError):
    """驱动包操作的可预期错误（参数非法 / 不存在 / 冲突）。"""


@dataclass
class DriverPackageRecord:
    name: str
    spec: str = ""
    version: str = ""
    package_dirs: List[str] = field(default_factory=list)
    device_ids: List[str] = field(default_factory=list)
    enabled: bool = True
    installer: str = ""
    installed_at_ms: int = 0
    updated_at_ms: int = 0


@dataclass
class DriverPackageOperation:
    operation_id: str
    kind: str  # install | uninstall
    spec: str
    status: str = "running"  # running | succeeded | failed
    package_name: str = ""
    started_at_ms: int = 0
    finished_at_ms: Optional[int] = None
    log: str = ""
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _normalize(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def ledger_path(working_dir: str | Path) -> Path:
    return Path(working_dir) / LEDGER_FILENAME


def catalog_path(working_dir: str | Path) -> Path:
    """实验室自维护的可安装目录（可选文件）。"""
    return Path(working_dir) / CATALOG_FILENAME


def _catalog_entries(data: Any) -> List[Dict[str, Any]]:
    """目录 JSON 既接受 ``{"packages": [...]}`` 也接受裸数组。"""
    items = data.get("packages") if isinstance(data, dict) else data
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _catalog_entry(raw: Dict[str, Any], *, source: str, installed: set[str]) -> Optional[Dict[str, Any]]:
    name = str(raw.get("name") or "").strip()
    spec = str(raw.get("spec") or name).strip()
    if not name or not spec:
        return None

    def _strings(value: Any) -> List[str]:
        return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []

    return {
        "name": name,
        "spec": spec,
        "version": str(raw.get("version") or ""),
        "description": str(raw.get("description") or ""),
        "homepage": str(raw.get("homepage") or ""),
        "devices": _strings(raw.get("devices")),
        "tags": _strings(raw.get("tags")),
        # 官方索引里的条目默认就是官方包；本地目录默认社区 / 自用，除非显式标 official
        "official": bool(raw.get("official", source == "remote")),
        "source": source,
        "installed": _normalize(name) in installed,
    }


def load_ledger(working_dir: str | Path) -> Dict[str, DriverPackageRecord]:
    path = ledger_path(working_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 损坏台账按空处理，不影响启动
        logger.warning(f"[DriverPackages] 台账读取失败: {exc}")
        return {}
    records: Dict[str, DriverPackageRecord] = {}
    for item in data.get("packages", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        known = {key: item[key] for key in DriverPackageRecord.__dataclass_fields__ if key in item}
        records[_normalize(str(item["name"]))] = DriverPackageRecord(**known)
    return records


def save_ledger(working_dir: str | Path, records: Dict[str, DriverPackageRecord]) -> None:
    path = ledger_path(working_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "packages": [asdict(record) for record in records.values()]}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def enabled_package_dirs(working_dir: str | Path) -> List[str]:
    """已启用且目录仍存在的驱动包目录（启动期并入 ``--devices``）。"""
    dirs: List[str] = []
    for record in load_ledger(working_dir).values():
        if not record.enabled:
            continue
        for item in record.package_dirs:
            if Path(item).is_dir() and item not in dirs:
                dirs.append(item)
    return dirs


def _spec_looks_like_path(spec: str) -> bool:
    s = spec.strip()
    if s.startswith(("git+", "http://", "https://")):
        return False
    return (
        s.startswith(("file:", ".", "/", "~"))
        or re.match(r"^[A-Za-z]:[\\/]", s) is not None
        or "\\" in s
        or "/" in s
    )


def _spec_distribution_name(spec: str) -> str:
    """安装规格对应的分发名：本地目录读 pyproject；``name==ver`` 取名字；git/URL 拿不到返回空串。

    CLI 里的 ``_spec_dist_name`` 只认裸名字，Windows 绝对路径会被它读成盘符 ``C``，所以先判路径。
    """
    from unilabos.app.cli.package import _local_dist_name, _spec_dist_name

    if _spec_looks_like_path(spec):
        return _local_dist_name(spec)
    return _spec_dist_name(spec)


def _distribution_snapshot() -> Dict[str, str]:
    from importlib.metadata import distributions

    snapshot: Dict[str, str] = {}
    for dist in distributions():
        try:
            name = dist.metadata["Name"]
        except Exception:  # noqa: BLE001
            continue
        if name:
            snapshot[_normalize(name)] = dist.version or ""
    return snapshot


def _dist_package_dirs(dist_name: str) -> List[str]:
    """已安装分发的顶层包目录（用于 AST 扫描挂载）。"""
    import importlib.util
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        dist = distribution(dist_name)
    except PackageNotFoundError:
        return []
    top_modules: List[str] = []
    try:
        top_text = dist.read_text("top_level.txt") or ""
        top_modules = [line.strip() for line in top_text.splitlines() if line.strip()]
    except Exception:  # noqa: BLE001
        top_modules = []
    if not top_modules:
        inferred: set[str] = set()
        for entry in dist.files or []:
            parts = entry.parts
            if not parts:
                continue
            head = parts[0]
            if head in {"..", "__pycache__"} or head.endswith((".dist-info", ".data")):
                continue
            if len(parts) > 1 and "." not in head:
                inferred.add(head)
        top_modules = sorted(inferred) or [_normalize(dist_name)]
    dirs: List[str] = []
    for module_name in top_modules:
        try:
            spec = importlib.util.find_spec(module_name)
        except (ImportError, ValueError):
            continue
        if spec is None:
            continue
        for location in spec.submodule_search_locations or []:
            path = str(Path(location).resolve())
            if Path(path).is_dir() and path not in dirs:
                dirs.append(path)
    return dirs


class DriverPackageService:
    """驱动包台账 + 后台安装 / 卸载操作。"""

    def __init__(self, working_dir: str | Path) -> None:
        self.working_dir = Path(working_dir)
        self._lock = threading.Lock()
        self._operations: Dict[str, DriverPackageOperation] = {}
        self._order: List[str] = []
        self._scan_dirs: List[str] = []
        self._restart_required = False
        self._external_only = False

    # ── 运行态上下文（启动时由 main 注入） ─────────────────────────

    def configure(self, scan_dirs: List[str], external_only: bool) -> None:
        self._scan_dirs = [str(Path(item).resolve()) for item in scan_dirs if item]
        self._external_only = external_only

    # ── 读 ────────────────────────────────────────────────────────

    def inventory(self) -> Dict[str, Any]:
        records = load_ledger(self.working_dir)
        loaded_ids = self._loaded_device_ids()
        packages = []
        for record in sorted(records.values(), key=lambda item: item.name.lower()):
            dirs_exist = all(Path(item).is_dir() for item in record.package_dirs)
            mounted = record.enabled and bool(record.package_dirs) and all(
                item in self._scan_dirs for item in record.package_dirs
            )
            packages.append(
                {
                    **asdict(record),
                    "dirs_exist": dirs_exist,
                    "mounted": mounted,
                    "loaded_device_ids": [item for item in record.device_ids if item in loaded_ids],
                }
            )
        return {
            "python": {"executable": sys.executable, "version": sys.version.split()[0]},
            "working_dir": str(self.working_dir),
            "ledger_path": str(ledger_path(self.working_dir)),
            "scan_dirs": list(self._scan_dirs),
            "external_only": self._external_only,
            "restart_required": self._restart_required,
            "packages": packages,
            "operations": self.operations(limit=5),
        }

    def catalog(self) -> Dict[str, Any]:
        """可安装目录 = 官方索引（``HTTPConfig.driver_package_index_url``）∪ 本地
        ``<working_dir>/driver_package_catalog.json``；同名以官方为准，并标出台账里已登记的。

        两个来源都是 ``{"packages": [{name, spec, version?, description?, homepage?,
        devices?, tags?, official?}]}``，``spec`` 直接喂给 install。
        """
        installed = set(load_ledger(self.working_dir).keys())
        sources: List[Dict[str, Any]] = []
        packages: List[Dict[str, Any]] = []
        seen: set[str] = set()

        def _collect(entries: List[Dict[str, Any]], source: str) -> int:
            count = 0
            for raw in entries:
                entry = _catalog_entry(raw, source=source, installed=installed)
                if entry is None or _normalize(entry["name"]) in seen:
                    continue
                seen.add(_normalize(entry["name"]))
                packages.append(entry)
                count += 1
            return count

        url = self._index_url()
        if url:
            try:
                entries = _catalog_entries(self._fetch_json(url))
                sources.append({"kind": "remote", "location": url, "ok": True, "count": _collect(entries, "remote")})
            except Exception as exc:  # noqa: BLE001 - 离线 / 索引损坏不影响本地目录
                sources.append({"kind": "remote", "location": url, "ok": False, "count": 0, "error": str(exc)})

        local = catalog_path(self.working_dir)
        if local.is_file():
            try:
                entries = _catalog_entries(json.loads(local.read_text(encoding="utf-8")))
                sources.append({"kind": "local", "location": str(local), "ok": True, "count": _collect(entries, "local")})
            except Exception as exc:  # noqa: BLE001
                sources.append({"kind": "local", "location": str(local), "ok": False, "count": 0, "error": str(exc)})
        else:
            sources.append({"kind": "local", "location": str(local), "ok": True, "count": 0, "missing": True})
        return {"sources": sources, "packages": packages}

    @staticmethod
    def _index_url() -> str:
        try:
            from unilabos.config.config import HTTPConfig

            return str(getattr(HTTPConfig, "driver_package_index_url", "") or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _fetch_json(url: str) -> Any:
        import urllib.request

        with urllib.request.urlopen(url, timeout=CATALOG_FETCH_TIMEOUT_S) as response:  # noqa: S310 - 管理员配置的索引地址
            return json.loads(response.read().decode("utf-8"))

    def operations(self, limit: int = OPERATION_HISTORY_LIMIT) -> List[Dict[str, Any]]:
        with self._lock:
            ordered = [self._operations[item] for item in reversed(self._order)]
        return [asdict(item) for item in ordered[:limit]]

    def operation(self, operation_id: str) -> Dict[str, Any]:
        with self._lock:
            record = self._operations.get(operation_id)
        if record is None:
            raise DriverPackageError(f"operation not found: {operation_id}")
        return asdict(record)

    # ── 写 ────────────────────────────────────────────────────────

    def set_enabled(self, name: str, enabled: bool) -> Dict[str, Any]:
        records = load_ledger(self.working_dir)
        key = _normalize(name)
        record = records.get(key)
        if record is None:
            raise DriverPackageError(f"driver package not found: {name}")
        if record.enabled != enabled:
            record.enabled = enabled
            record.updated_at_ms = _now_ms()
            save_ledger(self.working_dir, records)
            self._restart_required = True
        return asdict(record)

    def start_install(
        self, spec: str, enable: bool = True, upgrade: bool = False, name: str = ""
    ) -> Dict[str, Any]:
        """``name`` 是调用方已知的分发名（索引条目里带），git / URL 规格靠它可靠登记。"""
        spec = (spec or "").strip()
        if not spec:
            raise DriverPackageError("缺少安装目标：pip 规格（name==version）、git URL 或本地目录")
        if any(token in spec for token in ("\n", "\r", "\x00")):
            raise DriverPackageError("安装目标含非法字符")
        name = (name or "").strip()
        if name and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise DriverPackageError(f"分发名不合法：{name}")
        operation = self._new_operation("install", spec)
        if name:
            operation.package_name = name
        threading.Thread(
            target=self._run_install,
            args=(operation.operation_id, spec, enable, upgrade, name),
            name=f"DriverPackageInstall-{operation.operation_id[:8]}",
            daemon=True,
        ).start()
        return asdict(operation)

    def start_uninstall(self, name: str) -> Dict[str, Any]:
        records = load_ledger(self.working_dir)
        key = _normalize(name)
        record = records.get(key)
        if record is None:
            raise DriverPackageError(f"driver package not found: {name}")
        operation = self._new_operation("uninstall", record.name)
        operation.package_name = record.name
        threading.Thread(
            target=self._run_uninstall,
            args=(operation.operation_id, record.name),
            name=f"DriverPackageUninstall-{operation.operation_id[:8]}",
            daemon=True,
        ).start()
        return asdict(operation)

    # ── 内部 ──────────────────────────────────────────────────────

    def _new_operation(self, kind: str, spec: str) -> DriverPackageOperation:
        operation = DriverPackageOperation(
            operation_id=str(uuid.uuid4()),
            kind=kind,
            spec=spec,
            started_at_ms=_now_ms(),
        )
        with self._lock:
            self._operations[operation.operation_id] = operation
            self._order.append(operation.operation_id)
            while len(self._order) > OPERATION_HISTORY_LIMIT:
                stale = self._order.pop(0)
                self._operations.pop(stale, None)
        return operation

    def _append_log(self, operation_id: str, text: str) -> None:
        if not text:
            return
        with self._lock:
            operation = self._operations.get(operation_id)
            if operation is not None:
                operation.log = (operation.log + text.rstrip() + "\n")[-20000:]

    def _finish(self, operation_id: str, *, error: Optional[str] = None, result: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            operation = self._operations.get(operation_id)
            if operation is None:
                return
            operation.status = "failed" if error else "succeeded"
            operation.error = error
            operation.result = result
            operation.finished_at_ms = _now_ms()

    def _run_pip(self, operation_id: str, args: List[str]) -> int:
        command = [sys.executable, "-m", "pip", *args]
        self._append_log(operation_id, "$ " + " ".join(command))
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=INSTALL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self._append_log(operation_id, f"[timeout] 超过 {INSTALL_TIMEOUT_S}s")
            return 124
        self._append_log(operation_id, proc.stdout)
        self._append_log(operation_id, proc.stderr)
        return proc.returncode

    def _run_install(
        self, operation_id: str, spec: str, enable: bool, upgrade: bool = False, name_hint: str = ""
    ) -> None:
        try:
            from unilabos.app.cli.package import _installed_device_ids

            before = _distribution_snapshot()
            code = self._run_pip(operation_id, ["install", *(["--upgrade"] if upgrade else []), spec])
            if code != 0:
                self._finish(operation_id, error=f"pip install 退出码 {code}")
                return
            after = _distribution_snapshot()
            changed = [name for name, version in after.items() if before.get(name) != version]
            named = name_hint or _spec_distribution_name(spec)
            # 分发名已知（调用方给的 / 规格里看得出）且确实装着 → 以它为准（重复安装同版本
            # 也照常登记）；git / URL 等看不出名字的，看安装前后多出 / 变化的分发
            if named and _normalize(named) in after:
                candidates = [named]
            else:
                if name_hint:
                    self._append_log(operation_id, f"[warn] 环境里没有叫 {name_hint} 的分发，改按安装前后差异识别")
                candidates = changed
            if not candidates:
                self._finish(
                    operation_id,
                    error="pip 已退出 0，但没有识别到新安装或版本变化的分发（可能早已是该版本）；"
                    "如需重新登记请先卸载再装，或改用带名字的 pip 规格",
                )
                return
            records = load_ledger(self.working_dir)
            installed: List[Dict[str, Any]] = []
            for candidate in candidates:
                package_dirs = _dist_package_dirs(candidate)
                device_ids = _installed_device_ids(candidate) if package_dirs else []
                self._append_log(
                    operation_id,
                    f"[scan] {candidate}: dirs={package_dirs or '-'} devices={', '.join(device_ids) or '-'}",
                )
                key = _normalize(candidate)
                previous = records.get(key)
                record = DriverPackageRecord(
                    name=candidate,
                    spec=spec,
                    version=after.get(key, ""),
                    package_dirs=package_dirs,
                    device_ids=device_ids,
                    enabled=enable if previous is None else previous.enabled,
                    installer="pip",
                    installed_at_ms=previous.installed_at_ms if previous else _now_ms(),
                    updated_at_ms=_now_ms(),
                )
                records[key] = record
                installed.append(asdict(record))
            save_ledger(self.working_dir, records)
            self._restart_required = True
            with self._lock:
                operation = self._operations.get(operation_id)
                if operation is not None and installed:
                    operation.package_name = installed[0]["name"]
            self._finish(operation_id, result={"packages": installed, "restart_required": True})
        except Exception as exc:  # noqa: BLE001 - 后台线程兜底
            logger.exception("[DriverPackages] install failed")
            self._append_log(operation_id, f"[error] {exc}")
            self._finish(operation_id, error=str(exc))

    def _run_uninstall(self, operation_id: str, name: str) -> None:
        try:
            code = self._run_pip(operation_id, ["uninstall", "-y", name])
            if code != 0:
                self._finish(operation_id, error=f"pip uninstall 退出码 {code}")
                return
            records = load_ledger(self.working_dir)
            records.pop(_normalize(name), None)
            save_ledger(self.working_dir, records)
            self._restart_required = True
            self._finish(operation_id, result={"removed": name, "restart_required": True})
        except Exception as exc:  # noqa: BLE001
            logger.exception("[DriverPackages] uninstall failed")
            self._append_log(operation_id, f"[error] {exc}")
            self._finish(operation_id, error=str(exc))

    @staticmethod
    def _loaded_device_ids() -> set[str]:
        try:
            from unilabos.registry.registry import lab_registry

            return set(lab_registry.device_type_registry.keys())
        except Exception:  # noqa: BLE001
            return set()


_service: Optional[DriverPackageService] = None
_service_lock = threading.Lock()


def get_driver_package_service() -> DriverPackageService:
    global _service
    with _service_lock:
        if _service is None:
            from unilabos.config.config import BasicConfig

            _service = DriverPackageService(getattr(BasicConfig, "working_dir", None) or Path.cwd())
        return _service


__all__ = [
    "DriverPackageError",
    "DriverPackageOperation",
    "DriverPackageRecord",
    "DriverPackageService",
    "catalog_path",
    "enabled_package_dirs",
    "get_driver_package_service",
    "ledger_path",
    "load_ledger",
    "save_ledger",
]
