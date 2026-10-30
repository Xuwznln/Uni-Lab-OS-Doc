"""标记需要 POSIX 进程组或 POSIX C 编译器的历史测试夹具。"""

from __future__ import annotations

import os

import pytest

requires_posix_worker = pytest.mark.skipif(
    os.name != "posix",
    reason="此夹具需要 POSIX 进程组或 POSIX C 编译器",
)
