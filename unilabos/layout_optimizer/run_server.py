"""layout_optimizer 独立启动入口（支持 config 文件 + CLI 凭据覆盖）。

用法示例：
    python -m unilabos.layout_optimizer.run_server --config layout_optimizer.config.json
    python -m unilabos.layout_optimizer.run_server --ak <AK> --sk <SK> --addr <API_ADDR>
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from unilabos.config.config import BasicConfig, HTTPConfig

DEFAULT_CONFIG_NAME = "layout_optimizer.config.json"
ADDR_ALIASES = {
    # 对齐 reference.md 55-56（edge 约定）
    "test": "https://leap-lab.test.bohrium.com",
    "uat": "https://leap-lab.uat.bohrium.com",
    "local": "http://127.0.0.1:48197",
}
DEFAULT_EDGE_BASE = "https://leap-lab.bohrium.com"


def discover_config_path(explicit_path: str | None, cwd: Path | None = None) -> Path | None:
    """发现可用配置文件路径。

    优先级：
    1) --config 显式传入路径
    2) 当前目录下默认文件名 layout_optimizer.config.json
    """
    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        return path
    search_cwd = cwd or Path.cwd()
    candidate = (search_cwd / DEFAULT_CONFIG_NAME).resolve()
    return candidate if candidate.exists() else None


def load_json_config(config_path: Path) -> dict[str, Any]:
    """读取 JSON 配置文件。"""
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"配置文件必须是 JSON object: {config_path}")
    return payload


def normalize_remote_addr(raw_addr: str, fallback_addr: str = "") -> str:
    """归一化远端地址，支持 test/uat/local 别名。

    规则：
    - ``test``/``uat``/``local`` → reference.md 指定 BASE
    - 空值时用 ``fallback_addr``，若仍为空则用 DEFAULT_EDGE_BASE
    - 若最终地址不含 ``/api/v1``，自动追加
    """
    value = (raw_addr or "").strip()
    if value:
        lowered = value.lower()
        if lowered in ADDR_ALIASES:
            base = ADDR_ALIASES[lowered]
        else:
            base = value
    else:
        base = (fallback_addr or "").strip() or DEFAULT_EDGE_BASE

    normalized = base.rstrip("/")
    if not normalized.endswith("/api/v1"):
        normalized = f"{normalized}/api/v1"
    return normalized


def apply_runtime_config(
    *,
    file_config: dict[str, Any],
    ak: str,
    sk: str,
    addr: str,
    mount_uuid: str,
) -> dict[str, str]:
    """把文件配置 + CLI 覆盖应用到运行时配置。

    CLI 优先级高于 config 文件。
    """
    cfg_ak = str(file_config.get("ak") or "")
    cfg_sk = str(file_config.get("sk") or "")
    cfg_addr = str(file_config.get("addr") or file_config.get("remote_addr") or "")
    cfg_mount_uuid = str(
        file_config.get("mount_uuid") or file_config.get("mountUuid") or ""
    )

    if cfg_ak:
        BasicConfig.ak = cfg_ak
    if cfg_sk:
        BasicConfig.sk = cfg_sk
    # layout_optimizer 独立入口默认走 edge 基准域名（reference.md 55-56）
    HTTPConfig.remote_addr = normalize_remote_addr(cfg_addr, "")
    if cfg_mount_uuid:
        os.environ["LAYOUT_MOUNT_UUID"] = cfg_mount_uuid

    # CLI 覆盖
    if ak:
        BasicConfig.ak = ak
    if sk:
        BasicConfig.sk = sk
    HTTPConfig.remote_addr = normalize_remote_addr(addr, HTTPConfig.remote_addr)
    if mount_uuid:
        os.environ["LAYOUT_MOUNT_UUID"] = mount_uuid

    return {
        "ak": BasicConfig.ak,
        "sk": BasicConfig.sk,
        "addr": HTTPConfig.remote_addr,
        "mount_uuid": os.getenv("LAYOUT_MOUNT_UUID", ""),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run layout_optimizer FastAPI server")
    parser.add_argument(
        "--config",
        type=str,
        default="",
        help="配置文件路径（JSON），默认自动查找当前目录下 layout_optimizer.config.json",
    )
    parser.add_argument("--ak", type=str, default="", help="实验室 Access Key（覆盖 config）")
    parser.add_argument("--sk", type=str, default="", help="实验室 Secret Key（覆盖 config）")
    parser.add_argument(
        "--addr",
        type=str,
        default="",
        help=(
            "云端地址（覆盖 config）。支持别名: test / uat / local；"
            "也可传完整 URL，若缺 /api/v1 会自动补齐。"
        ),
    )
    parser.add_argument(
        "--mount_uuid",
        type=str,
        default="",
        help="可选：默认挂载点 UUID（覆盖 config），写入环境变量 LAYOUT_MOUNT_UUID",
    )
    parser.add_argument("--host", type=str, default="127.0.0.1", help="FastAPI 监听地址")
    parser.add_argument("--port", type=int, default=8000, help="FastAPI 监听端口")
    parser.add_argument("--reload", action="store_true", help="是否启用自动重载")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    file_config: dict[str, Any] = {}
    config_path = discover_config_path(args.config or None)
    if config_path:
        file_config = load_json_config(config_path)
        print(f"[layout_optimizer] loaded config: {config_path}")
    elif args.config:
        raise FileNotFoundError(f"--config 指定的文件不存在: {args.config}")
    else:
        print(
            "[layout_optimizer] no config file found in current directory, "
            "using CLI/default values",
        )

    effective = apply_runtime_config(
        file_config=file_config,
        ak=args.ak,
        sk=args.sk,
        addr=args.addr,
        mount_uuid=args.mount_uuid,
    )

    if not effective["ak"] or not effective["sk"]:
        print(
            "[layout_optimizer] warning: ak/sk is empty; /optimize/scene upload will fail "
            "unless provided via CLI/config",
        )

    print(
        f"[layout_optimizer] server starting at http://{args.host}:{args.port}, "
        f"remote_addr={effective['addr']}"
    )

    import uvicorn

    uvicorn.run(
        "unilabos.layout_optimizer.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()

