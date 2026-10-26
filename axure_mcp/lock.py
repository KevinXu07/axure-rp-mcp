"""Cross-process lock around Axure operations.

Two MCP client sessions (two editors, or a script plus an IDE) both talk to the same Axure process.
Each tool call opens the page it is about to edit, so without a lock one session can switch the
window to its page and the other session's write lands on the wrong page. The lock is a file lock so
it also covers separate server processes.
"""
from __future__ import annotations

import contextlib
import os
import sys
import time

LOCK_PATH = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                         "Axure", "axure-mcp.lock")


def lock_path() -> str:
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    return LOCK_PATH


_DEPTH = {"n": 0}


@contextlib.contextmanager
def bridge_lock(timeout: float = 180.0, poll: float = 0.05):
    """Serialise access to the Axure bridge across threads and processes (re-entrant per process)."""
    if _DEPTH["n"] > 0:
        # already held by this process - Windows locks are per handle, so nesting must not re-lock
        _DEPTH["n"] += 1
        try:
            yield
        finally:
            _DEPTH["n"] -= 1
        return

    path = lock_path()
    handle = open(path, "a+b")
    acquired = False
    deadline = time.time() + timeout
    try:
        if sys.platform == "win32":
            import msvcrt
            while not acquired:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                except OSError:
                    if time.time() > deadline:
                        raise TimeoutError("another session is holding the Axure bridge lock")
                    time.sleep(poll)
            _DEPTH["n"] = 1
            try:
                yield
            finally:
                _DEPTH["n"] = 0
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover - Windows is the target platform
            import fcntl
            while not acquired:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    if time.time() > deadline:
                        raise TimeoutError("another session is holding the Axure bridge lock")
                    time.sleep(poll)
            _DEPTH["n"] = 1
            try:
                yield
            finally:
                _DEPTH["n"] = 0
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
