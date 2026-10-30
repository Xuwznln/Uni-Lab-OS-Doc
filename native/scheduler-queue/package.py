"""为开源串行动态库生成经过验证的交付目录。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from native.scheduler_shared.package import main
from native.scheduler_shared.package import package as package_engine

DESCRIBE_TIMEOUT_SECONDS = 10


def package(library: Path, destination: Path, *, python: str | Path = sys.executable) -> Path:
    return package_engine(library, destination, engine_id="unilab.queue", python=python, timeout=DESCRIBE_TIMEOUT_SECONDS)


if __name__ == "__main__":
    main("unilab.queue")
