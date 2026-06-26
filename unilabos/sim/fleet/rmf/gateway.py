"""RmfGateway：RMF core + rmf-web api-server 进程生命周期治理（#18 §6.2 / #17 §6.4）。

镜像 `unilabos/sim/isaac_gateway.py` 的「出进程系统桥接」思路，但 RMF core 是一组
独立 ROS2 进程，故这里用 subprocess 管理：启动顺序、健康检查、端口、日志、退出回收。

是否启动由 graph/config 推导（存在 `rmf.coordinator` + fleet-capable 设备）；无则 no-op。
进程启动命令依部署而定，通过 `ProcessSpec` 注入（默认给出占位，便于本地/CI 覆盖）。
"""

from __future__ import annotations

import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

try:
    from unilabos.utils.log import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("rmf.gateway")


@dataclass
class ProcessSpec:
    name: str
    command: str  # shell 命令字符串
    required: bool = True  # 关键进程异常 → gateway 视为 error


@dataclass
class RmfGatewayConfig:
    generated_map_dir: str = "/tmp/unilabos/rmf_maps"
    nav_graph_subdir: str = "nav_graphs"
    api_server_url: str = "http://127.0.0.1:8000"
    trajectory_server_url: str = "ws://127.0.0.1:8006"
    startup_timeout_s: float = 120.0
    readonly_web_enabled: bool = True
    processes: List[ProcessSpec] = field(default_factory=list)


class RmfGateway:
    """管理 RMF core 进程组（building map server / fleet adapter / dispatcher /
    trajectory server）与可选的 rmf-web api-server（展示面，只读）。"""

    def __init__(self, config: Optional[RmfGatewayConfig] = None):
        self.config = config or RmfGatewayConfig()
        self._procs: Dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()
        self._started = False

    # ---------------------------------------------------------- nav graph
    def generate_nav_graph(self, building_yaml_path: str) -> str:
        """调用 RMF 官方 CLI 由 building.yaml 生成 nav_graph，返回输出目录。

        `ros2 run rmf_building_map_tools building_map_generator nav <yaml> <out_dir>`
        """
        out_dir = str(Path(self.config.generated_map_dir) / self.config.nav_graph_subdir)
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        cmd = [
            "ros2", "run", "rmf_building_map_tools", "building_map_generator", "nav",
            building_yaml_path, out_dir,
        ]
        logger.info(f"[rmf] 生成 nav_graph: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.config.startup_timeout_s)
        if result.returncode != 0:
            raise RuntimeError(f"building_map_generator nav 失败: {result.stderr.strip()}")
        return out_dir

    # ---------------------------------------------------------- lifecycle
    def start(self) -> None:
        """按 ProcessSpec 启动 RMF core（+ 可选 api-server）。无 spec 时 no-op。"""
        specs = list(self.config.processes)
        if not specs:
            logger.info("[rmf] RmfGateway 无 ProcessSpec，跳过进程拉起（外部已运行 RMF 时为预期）")
            return
        with self._lock:
            for spec in specs:
                if not self.config.readonly_web_enabled and "api-server" in spec.name:
                    continue
                self._spawn(spec)
            self._started = True
        self._await_ready()

    def _spawn(self, spec: ProcessSpec) -> None:
        logger.info(f"[rmf] 启动进程 {spec.name}: {spec.command}")
        try:
            proc = subprocess.Popen(shlex.split(spec.command))
            self._procs[spec.name] = proc
        except Exception as e:  # noqa: BLE001
            if spec.required:
                raise RuntimeError(f"RMF 关键进程 {spec.name} 启动失败: {e}") from e
            logger.warning(f"[rmf] 非关键进程 {spec.name} 启动失败: {e}")

    def _await_ready(self) -> None:
        """简单就绪 gating：在超时内确认关键进程未立即退出。"""
        deadline = time.time() + min(5.0, self.config.startup_timeout_s)
        while time.time() < deadline:
            dead = [n for n, p in self._procs.items() if p.poll() is not None]
            if dead:
                raise RuntimeError(f"RMF 进程启动后立即退出: {dead}")
            time.sleep(0.2)

    def is_healthy(self) -> bool:
        if not self._started:
            return True  # 外部 RMF 模式：gateway 不负责健康
        return all(p.poll() is None for p in self._procs.values())

    def unhealthy_processes(self) -> List[str]:
        return [n for n, p in self._procs.items() if p.poll() is not None]

    def stop(self) -> None:
        with self._lock:
            for name, proc in list(self._procs.items()):
                if proc.poll() is None:
                    logger.info(f"[rmf] 终止进程 {name}")
                    proc.terminate()
            for name, proc in list(self._procs.items()):
                try:
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    logger.warning(f"[rmf] 进程 {name} 未优雅退出，强制 kill")
                    proc.kill()
            self._procs.clear()
            self._started = False
