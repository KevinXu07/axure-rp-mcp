"""Launch Axure RP with the reflection bridge injected (DOTNET_STARTUP_HOOKS)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
import sys
import errno

from .client import DEFAULT_PORT, Bridge, BridgeError, discover, wait_for_bridge

DEFAULT_EXE = r"D:\Program Files (x86)\Axure\Axure RP 11\AxureRP11.exe"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BRIDGE = os.path.join(REPO_ROOT, "bridge", "dist", "AxureBridge.dll")
FALLBACK_BRIDGE = os.path.join(REPO_ROOT, "bridge", "bin", "Debug", "net10.0", "AxureBridge.dll")
MAC_LAUNCH_STABILITY_SECONDS = 1.25
MAC_LAUNCH_RETRY_DELAY_SECONDS = 1.5


def exe_path() -> str:
    configured = os.environ.get("AXURE_EXE")
    if configured:
        configured = os.path.normpath(configured)
        if sys.platform == "darwin" and configured.lower().endswith(".app"):
            return os.path.join(configured, "Contents", "MacOS", os.path.basename(configured)[:-4])
        return configured
    if sys.platform == "darwin":
        return "/Applications/Axure RP 11.app/Contents/MacOS/Axure RP 11"
    return DEFAULT_EXE


def bridge_path() -> str:
    """Pick the bridge assembly to inject.

    Preferring `dist` blindly was a trap: after a rebuild the Debug output is newer, and injecting the
    stale `dist` copy made some bridge ops fail with "method ... not found". Use the most recently
    built one unless AXURE_BRIDGE_DLL says otherwise.
    """
    configured = os.environ.get("AXURE_BRIDGE_DLL")
    if configured:
        return configured
    framework = "net8.0" if sys.platform == "darwin" else "net10.0"
    build = os.path.join(REPO_ROOT, "bridge", "bin", "Debug", framework, "AxureBridge.dll")
    # Mac must never select a net10 assembly built for the Windows host.
    dist = os.path.join(REPO_ROOT, "bridge", "dist", framework, "AxureBridge.dll")
    paths = (dist, build) if sys.platform == "darwin" else (DEFAULT_BRIDGE, FALLBACK_BRIDGE, dist)
    candidates = [path for path in paths if os.path.exists(path)]
    if not candidates:
        return DEFAULT_BRIDGE
    return max(candidates, key=os.path.getmtime)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError as exc:
            return exc.errno == errno.EPERM
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
    except OSError:
        return True  # cannot tell: assume alive
    return str(pid) in out


def candidate_ports(port: int | None = None) -> list[int]:
    """Ports worth probing, most specific first. The bridge walks up from 41999 when taken."""
    ports: list[int] = []
    if port:
        ports.append(port)
    info = discover()
    if info and info.get("port"):
        try:
            if _pid_alive(int(info.get("pid", 0))):
                ports.append(int(info["port"]))
        except (TypeError, ValueError):
            pass
    ports.extend(range(DEFAULT_PORT, DEFAULT_PORT + 21))
    seen, ordered = set(), []
    for candidate in ports:
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _ping_port(port: int, timeout: float = 0.2) -> bool:
    """Cheap single-port liveness check (connect + ping)."""
    try:
        probe = Bridge(port=port, timeout=timeout).connect()
    except (BridgeError, OSError):
        return False
    try:
        probe.ping()
        return True
    except BridgeError:
        return False
    finally:
        try:
            probe.close()
        except Exception:  # noqa: BLE001
            pass


def bridge_alive(port: int | None = None, quick: bool = True) -> int | None:
    """Return the port of a live bridge, or None.

    quick=True only probes the expected port and its immediate neighbours with a 0.2s timeout -
    scanning the whole 41999..42019 range with a 0.6s timeout made a relaunch take ~56s because
    every poll iteration walked dead ports.
    """
    candidates = ([port] if port else []) + [DEFAULT_PORT]
    info = discover()
    if info and info.get("port"):
        try:
            if _pid_alive(int(info.get("pid", 0))):
                candidates.insert(0, int(info["port"]))
        except (TypeError, ValueError):
            pass
    if not quick:
        candidates.extend(range(DEFAULT_PORT, DEFAULT_PORT + 21))
    else:
        candidates.extend(range(DEFAULT_PORT + 1, DEFAULT_PORT + 5))
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _ping_port(candidate):
            return candidate
    if quick:
        return bridge_alive(port, quick=False)
    return None


def _wait_for_launched_bridge(process: subprocess.Popen, start_port: int, wait: float) -> Bridge | None:
    """Wait until the child process and its bridge are both stable.

    The startup hook can expose the bridge while Client.Mac is still creating
    NSApplication. A TCP ping at that point is not enough: macOS can
    exit shortly afterwards if Axure is relaunched before Cocoa has fully
    released the prior application instance.
    """
    deadline = time.monotonic() + wait
    stable_port = None
    stable_since = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return None
        found = next((candidate for candidate in range(start_port, start_port + 5)
                      if _ping_port(candidate)), None)
        now = time.monotonic()
        if found is None:
            stable_port = stable_since = None
        elif found != stable_port:
            stable_port, stable_since = found, now
        elif now - stable_since >= MAC_LAUNCH_STABILITY_SECONDS:
            if process.poll() is not None:
                return None
            try:
                bridge = wait_for_bridge(found, timeout=5)
            except BridgeError:
                return None
            if process.poll() is None:
                return bridge
            bridge.close()
            return None
        time.sleep(0.2)
    return None


def launch(open_file: str | None = None, port: int | None = None, wait: float = 90.0) -> Bridge | None:
    """Start Axure with the bridge injected (or reuse a running bridge)."""
    alive = bridge_alive(port)
    if alive is not None:
        return wait_for_bridge(alive, timeout=5)

    open_path = None
    if open_file:
        open_path = os.path.realpath(os.path.expanduser(str(open_file)))
        if not os.path.isfile(open_path):
            raise FileNotFoundError(f"Axure file not found: {open_path}")
        if Path(open_path).suffix.lower() != ".rp":
            raise ValueError(f"Axure file must end in .rp: {open_path}")

    start_port = port or DEFAULT_PORT
    exe, dll = exe_path(), bridge_path()
    if not os.path.exists(exe):
        raise FileNotFoundError(f"Axure executable not found: {exe} (set AXURE_EXE)")
    if not os.path.exists(dll):
        raise FileNotFoundError(
            f"bridge assembly not found: {dll} (build bridge/AxureBridge.csproj, or set AXURE_BRIDGE_DLL)")

    env = dict(os.environ)
    env["DOTNET_STARTUP_HOOKS"] = os.pathsep.join(filter(None, (env.get("DOTNET_STARTUP_HOOKS"), dll)))
    env["AXURE_BRIDGE_PORT"] = str(start_port)

    # A direct Popen child stays in the caller's terminal session. If the
    # launching shell/agent closes first, macOS can deliver SIGHUP to Axure and
    # make a successful bridge look like a crash. Start a new POSIX session so
    # the GUI survives its launcher and can be reused by later MCP sessions.
    attempts = 2 if sys.platform == "darwin" else 1
    for attempt in range(attempts):
        process = subprocess.Popen(
            [exe] + ([open_path] if open_path else []),
            env=env,
            close_fds=True,
            start_new_session=sys.platform != "win32",
        )
        bridge = _wait_for_launched_bridge(process, start_port, wait)
        if bridge is not None:
            return bridge
        if attempt + 1 < attempts and process.poll() is not None:
            print(
                "[axure-mcp] Axure exited during Cocoa startup; retrying once",
                file=sys.stderr,
            )
            time.sleep(MAC_LAUNCH_RETRY_DELAY_SECONDS)
            continue
        break
    return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", help=".rp file to open when launching Axure")
    args = parser.parse_args()
    bridge = launch(open_file=args.file)
    if bridge is None:
        raise SystemExit("Axure started but the bridge did not become available")
    try:
        print(bridge.ping())
    finally:
        bridge.close()
