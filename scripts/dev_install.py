#!/usr/bin/env python3
"""开发安装入口：安装当前解释器的可编辑包及声明依赖，默认不安装 ROS。"""

from __future__ import annotations

import argparse
import locale
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

TSINGHUA_MIRROR = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"


def build_command(project_root: Path, *, use_pip: bool, skip_deps: bool, mirror: bool,
                  wheelhouse: Path | None = None, extras: str = "") -> list[str]:
    if not use_pip and shutil.which("uv"):
        command = ["uv", "pip", "install", "--python", sys.executable]
    else:
        command = [sys.executable, "-m", "pip", "install"]
    requested = list(dict.fromkeys(value.strip() for value in extras.split(",") if value.strip()))
    if set(requested) - {"ros2", "full"}:
        raise ValueError(f"未知的可选依赖组：{extras}")
    target = str(project_root) + (f"[{','.join(requested)}]" if requested else "")
    command.extend(["-e", target])
    if skip_deps:
        command.append("--no-deps")
    if wheelhouse:
        command.extend(["--find-links", str(wheelhouse.resolve())])
    if mirror:
        command.extend(["--index-url", TSINGHUA_MIRROR])
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mirrors = parser.add_mutually_exclusive_group()
    mirrors.add_argument("--china", action="store_true", help="使用清华 Python 包镜像")
    mirrors.add_argument("--no-mirror", action="store_true", help="使用默认 Python 包源")
    parser.add_argument("--skip-deps", action="store_true", help="只安装源码；依赖须已由 Conda 等方式安装")
    parser.add_argument("--use-pip", action="store_true", help="不用 uv，使用当前解释器的 pip")
    parser.add_argument("--wheelhouse", type=Path, help="已有配套 wheel 目录；省略时从固定源码构建，不上传 PyPI")
    parser.add_argument("--extras", default="", choices=("ros2", "full"),
                        help="可选 ros2 或 full（含文档/测试/开发）；原生 ROS 须另由 Conda 安装")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parent.parent
    language = locale.getlocale()[0] or ""
    mirror = not args.no_mirror and (args.china or language.lower().startswith(("zh", "chinese")))
    print("[INFO] 安装默认 HostLink 开发环境；PLR/Opentrons 使用配套 wheel，不在启动时切换分支。")
    try:
        with tempfile.TemporaryDirectory(prefix="unilab-dev-wheels-") as temporary:
            wheelhouse = args.wheelhouse
            if not args.skip_deps and wheelhouse is None:
                subprocess.run([sys.executable, str(project_root / "scripts/build_wheel_release.py"),
                                "--dependencies-only", "--output-dir", temporary], check=True)
                wheelhouse = Path(temporary) / "wheelhouse"
            command = build_command(project_root, use_pip=args.use_pip, skip_deps=args.skip_deps,
                                    mirror=mirror, wheelhouse=wheelhouse, extras=args.extras)
            subprocess.run(command, check=True)
        subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    except subprocess.CalledProcessError as exc:
        print(f"[ERROR] 安装或依赖验证失败，退出码 {exc.returncode}。请检查上述输出。")
        return exc.returncode
    print("[OK] 安装完成。可运行 python -m unilabos.app.main --disable-browser")
    return 0


if __name__ == "__main__":
    sys.exit(main())
