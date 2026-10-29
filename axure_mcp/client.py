"""Client for the in-process Axure reflection bridge (newline-delimited JSON over loopback TCP)."""
from __future__ import annotations

import json
import os
import socket
import time
from typing import Any

from .paths import data_dir

DEFAULT_PORT = 41999


class BridgeError(RuntimeError):
    pass


def discovery_path() -> str:
    return os.path.join(data_dir(), "axure-bridge.json")


def discover() -> dict | None:
    """Read the bridge descriptor Axure's hooked process writes at startup."""
    try:
        with open(discovery_path(), "r", encoding="utf-8") as fh:
            info = json.load(fh)
    except (OSError, ValueError):
        return None
    if (not isinstance(info, dict) or type(info.get("port")) is not int
            or not 1 <= info["port"] <= 65535):
        return None
    return info


class Bridge:
    def __init__(self, port: int | None = None, host: str = "127.0.0.1", timeout: float = 180.0):
        if port is None:
            info = discover()
            port = int(info["port"]) if info else DEFAULT_PORT
        self.port = port
        self.host = host
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = b""
        self._id = 0

    # ---------- plumbing ----------

    def connect(self) -> "Bridge":
        self.close()
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return self

    def close(self) -> None:
        self._buf = b""
        if self._sock:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> "Bridge":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    def _readline(self) -> str:
        assert self._sock is not None
        while b"\n" not in self._buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise BridgeError("bridge closed the connection")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode("utf-8")

    def raw(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._sock is None:
            self.connect()
        self._id += 1
        assert self._sock is not None
        self._sock.sendall((json.dumps({"id": self._id, **request}) + "\n").encode("utf-8"))
        response = json.loads(self._readline())
        if not isinstance(response, dict):
            raise BridgeError("invalid bridge response: expected an object")
        # Older bridges omit id on errors; successful responses must match.
        if response.get("ok") and response.get("id") != self._id:
            raise BridgeError("bridge response id does not match request")
        return response

    def call(self, op: str, **kwargs: Any) -> Any:
        try:
            resp = self.raw({"op": op, **kwargs})
        except (OSError, ValueError, BridgeError) as exc:
            # A modal dialog (Axure wraps escaped UI exceptions as iI3A.TI3Q) blocks the UI thread, so
            # the call never returns. Read whatever the dialog says, close it, and report that instead
            # of a bare timeout.
            self.close()  # a late reply must never be consumed by the next request
            dialogs = self.dismiss_dialogs() if op not in {"dismiss", "dialogs", "ping"} else []
            detail = "; ".join(f"{d.get('title','')} :: {d.get('text','')}".strip(" :")
                               for d in dialogs) if dialogs else ""
            if detail:
                raise BridgeError(f"Axure 弹出了对话框（已关闭）：{detail}") from exc
            raise BridgeError(f"{type(exc).__name__}: {exc}") from exc
        if not resp.get("ok"):
            raise BridgeError(str(resp.get("error", "unknown error")).split("\n")[0])
        return resp.get("result")

    def dialogs(self) -> list[dict]:
        """Modal dialogs currently open in the Axure process."""
        return self.call("dialogs") or []

    def dismiss_dialogs(self) -> list[dict]:
        """Read and close modal dialogs, using a short-lived connection so a blocked call cannot stall it."""
        try:
            probe = Bridge(port=self.port, timeout=4.0).connect()
        except Exception:  # noqa: BLE001 - reporting path must never raise
            return []
        try:
            return probe.call("dismiss", close=True) or []
        except Exception:  # noqa: BLE001
            return []
        finally:
            try:
                probe.close()
            except Exception:  # noqa: BLE001
                pass

    # ---------- operations ----------

    def ping(self) -> dict:
        return self.call("ping")

    def types(self, q: str, limit: int = 40) -> list[str]:
        return self.call("types", q=q, limit=limit)

    def members(self, type_name: str, q: str | None = None) -> dict:
        return self.call("members", type=type_name, **({"q": q} if q else {}))

    def subtypes(self, type_name: str, limit: int = 200) -> list[dict]:
        return self.call("subtypes", type=type_name, limit=limit)

    def invoke(self, type_name: str, method: str, args: list | None = None, **kw) -> Any:
        return self.call("invoke", type=type_name, method=method, args=args or [], **kw)

    def invoke_on(self, handle: int, method: str, args: list | None = None, **kw) -> Any:
        return self.call("invoke", handle=handle, method=method, args=args or [], **kw)

    def ctor(self, type_name: str, args: list | None = None, **kw) -> Any:
        return self.call("ctor", type=type_name, args=args or [], **kw)

    def get(self, type_name: str, name: str, **kw) -> Any:
        return self.call("get", type=type_name, name=name, **kw)

    def get_on(self, handle: int, name: str, **kw) -> Any:
        return self.call("get", handle=handle, name=name, **kw)

    def set(self, type_name: str, name: str, value: Any, **kw) -> Any:
        return self.call("set", type=type_name, name=name, value=value, **kw)

    def set_on(self, handle: int, name: str, value: Any, **kw) -> Any:
        return self.call("set", handle=handle, name=name, value=value, **kw)

    def identity(self, handle: int) -> dict:
        return self.call("id", handle=handle)

    def dump(self, handle: int, depth: int = 4, limit: int = 50) -> Any:
        return self.call("dump", handle=handle, depth=depth, limit=limit)

    def iter(self, handle: int, limit: int = 500) -> list:
        return self.call("iter", handle=handle, limit=limit)

    def docs(self) -> Any:
        return self.call("docs")


def wait_for_bridge(port: int | None = None, timeout: float = 90.0) -> Bridge:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            return Bridge(port).connect()
        except OSError as exc:
            last = exc
            time.sleep(0.5)
    raise BridgeError(f"bridge did not answer: {last}")
