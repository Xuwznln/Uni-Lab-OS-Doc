"""跨平台进程级文件锁。"""

from __future__ import annotations

import errno
import os
import time
from typing import BinaryIO

try:  # POSIX
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - 由 Windows 回归覆盖
    _fcntl = None

try:  # Windows
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - 由 POSIX 回归覆盖
    _msvcrt = None

_WINDOWS_LOCK_ERRORS = {errno.EACCES, errno.EAGAIN, errno.EDEADLK}


def acquire_exclusive_file_lock(stream: BinaryIO) -> None:
    """阻塞获取 ``stream`` 对应文件的跨进程排他锁。"""

    if _fcntl is not None:
        _fcntl.flock(stream.fileno(), _fcntl.LOCK_EX)
        return
    if _msvcrt is None:  # pragma: no cover - CPython 支持的平台均有一个实现
        raise RuntimeError("当前平台不支持进程级文件锁")

    # msvcrt.locking() 锁定从当前偏移开始的字节区间，因此锁文件至少
    # 需要一个字节，并且加锁/解锁前都必须回到固定偏移。
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b"\0")
        stream.flush()

    while True:
        stream.seek(0)
        try:
            _msvcrt.locking(stream.fileno(), _msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if exc.errno not in _WINDOWS_LOCK_ERRORS:
                raise
            time.sleep(0.05)


def release_file_lock(stream: BinaryIO) -> None:
    """释放由 :func:`acquire_exclusive_file_lock` 获取的锁。"""

    if _fcntl is not None:
        _fcntl.flock(stream.fileno(), _fcntl.LOCK_UN)
        return
    if _msvcrt is None:  # pragma: no cover - CPython 支持的平台均有一个实现
        raise RuntimeError("当前平台不支持进程级文件锁")
    stream.seek(0)
    _msvcrt.locking(stream.fileno(), _msvcrt.LK_UNLCK, 1)
