"""显式构建并验证开源串行动态库，运行时不调用编译器。"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from native.scheduler_shared.package import package


def build(destination: Path, *, profile: str = "release") -> Path:
    crate = Path(__file__).resolve().parent
    cargo = shutil.which("cargo")
    if cargo is None:
        raise RuntimeError("构建串行动态库需要 Rust cargo")
    command = [cargo, "build", "--locked", "--manifest-path", str(crate / "Cargo.toml")]
    if profile == "release":
        command.append("--release")
    subprocess.run(command, check=True)
    filename = ("unilab_scheduler_queue.dll" if sys.platform == "win32" else
                "libunilab_scheduler_queue." + ("dylib" if sys.platform == "darwin" else "so"))
    return package(crate / "target" / profile / filename, destination, engine_id="unilab.queue")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("debug", "release"), default="release")
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.destination, profile=args.profile))
