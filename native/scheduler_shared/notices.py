"""由 Cargo 锁定依赖的本地许可文件生成发行声明，供维护依赖时更新。"""

import argparse
import json
import subprocess
from collections import defaultdict
from pathlib import Path


def collect(manifest: Path) -> str:
    packages = {}
    for target in ("aarch64-apple-darwin", "x86_64-unknown-linux-gnu", "x86_64-pc-windows-msvc"):
        metadata = json.loads(subprocess.check_output(["cargo", "metadata", "--locked", "--format-version", "1", "--filter-platform", target, "--manifest-path", str(manifest)]))
        nodes = {node["id"]: node for node in metadata["resolve"]["nodes"]}
        pending, reachable = [metadata["resolve"]["root"]], set()
        while pending:
            identity = pending.pop()
            if identity in reachable:
                continue
            reachable.add(identity)
            pending.extend(dependency["pkg"] for dependency in nodes[identity]["deps"])
        packages.update({package["id"]: package for package in metadata["packages"] if package["id"] in reachable})
    notices = defaultdict(list)
    for package in packages.values():
        if package["name"].startswith("unilab-scheduler-"):
            continue
        root = Path(package["manifest_path"]).parent
        files = sorted(path for path in root.iterdir() if path.is_file() and path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")))
        if package.get("license_file"):
            files.append(root / package["license_file"])
        if not files and package["name"] in {"uuid-simd", "vsimd"}:
            files = [Path(__file__).with_name("licenses") / "simd-MIT.txt"]
        if not files:
            raise RuntimeError("依赖缺少可分发许可文件：" + package["name"])
        for path in set(files):
            notices[path.read_text(encoding="utf-8")].append(package["name"] + " " + package["version"])
    text = "\n\n".join("Rust crates: " + ", ".join(sorted(set(names))) + "\n\n" + text
                         for text, names in sorted(notices.items(), key=lambda item: sorted(item[1])))
    return "\n".join(line.rstrip() for line in text.splitlines()).rstrip() + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(collect(args.manifest), encoding="utf-8")
