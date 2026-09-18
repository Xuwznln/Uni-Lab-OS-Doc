#!/usr/bin/env python3
"""RMF runtime 统一 CLI 薄入口。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unilabos.sim.fleet.rmf.runtime.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

