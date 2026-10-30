"""导出可公开的源码树；不复制 Git 历史、商业算法或本地构建产物。"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

PRIVATE_ROOTS = ("unilabos/scheduling/cpsat", "native/scheduler-cpsat", "unilabos/devices")
BUILD_PARTS = {".git", "__pycache__", "target", "dist", "dist-package", "build", "node_modules", ".venv"}


def public_path(path: Path) -> bool:
    name = path.as_posix()
    return not (
        any(name == root or name.startswith(root + "/") for root in PRIVATE_ROOTS)
        or any(part in BUILD_PARTS or part.endswith(".egg-info") for part in path.parts)
        or path.name == ".env" or path.name.startswith(".env.")
        or path.suffix in {".pyc", ".pyo", ".so", ".dylib", ".dll"}
        or name.startswith("unilabos/scheduling/queue_native/")
        or (name.startswith(".github/workflows/") and path.name != "public-scheduler.yml")
    )


def export_public_source(source: Path, destination: Path) -> None:
    source, destination = source.resolve(), destination.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    if destination.is_relative_to(source):
        raise ValueError("公开源码目标必须位于源目录之外，避免递归复制")
    listed = subprocess.run(
        ["git", "-C", str(source), "ls-files", "-z", "--cached"],
        capture_output=True,
    )
    if listed.returncode != 0:
        raise RuntimeError("无法读取 Git 已跟踪文件，停止公开导出")
    paths = [Path(name) for name in listed.stdout.decode("utf-8").split("\0") if name]
    destination.mkdir(parents=True)
    try:
        for relative in sorted(set(paths)):
            path = source / relative
            if not public_path(relative) or not path.is_file():
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(source):
                raise ValueError("公开源码不接受符号链接或目录外引用：" + str(relative))
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    except BaseException:
        shutil.rmtree(destination)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    export_public_source(args.source, args.destination)
