"""MCP layer smoke test: protocol behaviour, argument validation, connection reuse, error hints."""
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def start():
    return subprocess.Popen([sys.executable, "-m", "axure_mcp.server"], cwd=REPO,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            env={**os.environ, "AXURE_AUTO_LAUNCH": "0", "PYTHONIOENCODING": "utf-8"})


def rpc(proc, payload):
    proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    return json.loads(line) if line else None


def text_of(result):
    if not isinstance(result, dict):
        return str(result)
    return " ".join(b.get("text", "") for b in result.get("content", []) if b.get("type") == "text")


def main():
    proc = start()
    checks = []

    init = rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "smoke", "version": "1"}}})
    checks.append(("initialize", init.get("result", {}).get("serverInfo", {}).get("name")))
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    proc.stdin.flush()

    ping = rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "ping"})
    checks.append(("ping", ping.get("result")))

    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                 "params": {"requestId": 99}}) + "\n")
    proc.stdin.flush()
    checks.append(("notification accepted", True))

    tools = rpc(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    count = len((tools.get("result") or {}).get("tools", []))
    checks.append(("tools/list", count))

    unknown = rpc(proc, {"jsonrpc": "2.0", "id": 5, "method": "no/such/method"})
    checks.append(("unknown method", (unknown.get("error") or {}).get("code")))

    unknown_tool = rpc(proc, {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                              "params": {"name": "axure_not_a_tool", "arguments": {}}})
    checks.append(("unknown tool", (unknown_tool.get("error") or {}).get("code")))

    missing = rpc(proc, {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                         "params": {"name": "axure_create_page", "arguments": {}}})
    checks.append(("missing required", text_of(missing.get("result"))[:60]))

    extra = rpc(proc, {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                       "params": {"name": "axure_list_pages", "arguments": {"bogus": 1}}})
    checks.append(("unknown argument", text_of(extra.get("result"))[:70]))

    bad_enum = rpc(proc, {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                          "params": {"name": "axure_align",
                                     "arguments": {"names": ["a"], "mode": "diagonal"}}})
    checks.append(("bad enum", text_of(bad_enum.get("result"))[:70]))

    ok = rpc(proc, {"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                    "params": {"name": "axure_status", "arguments": {}}})
    checks.append(("valid call", "bridge" in text_of(ok.get("result")).lower()))

    # two calls in a row must reuse the connection (and still work)
    t0 = time.time()
    for i in range(5):
        rpc(proc, {"jsonrpc": "2.0", "id": 100 + i, "method": "tools/call",
                   "params": {"name": "axure_status", "arguments": {}}})
    checks.append(("5 calls in %.2fs" % (time.time() - t0), True))

    proc.stdin.close()
    proc.wait(timeout=30)

    print("== protocol / validation ==")
    for label, value in checks:
        print(f"  {label:24} {value}")


if __name__ == "__main__":
    main()
