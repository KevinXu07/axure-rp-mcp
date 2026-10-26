"""Re-entrant thread and process lock for complete Axure tool operations."""
from __future__ import annotations

import contextlib
import os
import sys
import threading
import time

from .paths import data_dir

LOCK_PATH = os.path.join(data_dir(), 'axure-mcp.lock')
_THREAD_LOCK = threading.RLock()
_LOCAL = threading.local()


def lock_path():
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    return LOCK_PATH


@contextlib.contextmanager
def bridge_lock(timeout: float = 180.0, poll: float = 0.05):
    deadline = time.monotonic() + timeout
    if not _THREAD_LOCK.acquire(timeout=max(0, timeout)):
        raise TimeoutError('another thread is holding the Axure bridge lock')
    try:
        if getattr(_LOCAL, 'depth', 0):
            _LOCAL.depth += 1
            try:
                yield
            finally:
                _LOCAL.depth -= 1
            return
        with open(lock_path(), 'a+b') as handle:
            # msvcrt locks from the current position. Append mode moves to EOF;
            # always lock AND unlock byte zero, even if a stale file is nonempty.
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b'\0')
                handle.flush()
            if sys.platform == 'win32':
                import msvcrt
                def acquire():
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                def release():
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                def acquire():
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                def release():
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            while True:
                try:
                    acquire()
                    break
                except OSError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('another session is holding the Axure bridge lock')
                    time.sleep(min(poll, remaining))
            _LOCAL.depth = 1
            try:
                yield
            finally:
                _LOCAL.depth = 0
                release()
    finally:
        _THREAD_LOCK.release()
