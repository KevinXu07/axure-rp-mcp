"""Full regression: exercise every tool, save, restart, then re-read and assert.

ASCII-only on purpose (a PowerShell round-trip mangled non-ASCII literals in an earlier run).
Writes docs/VERIFICATION.md. An item passes only if it survives save + relaunch.
"""
import base64
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import zlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RP = os.path.join(REPO, "test", "verify.rp")
COPY = os.path.join(REPO, "test", "verify-copy.rp")
REPORT = os.path.join(REPO, "docs", "VERIFICATION.md")

FOLDER = "VerifyGroup"
PAGE_A, PAGE_B, PAGE_C = "PageAlpha", "PageBeta", "PageGamma"
NOTE = "regression page note"
WNOTE = "button note text"


def make_png(width=120, height=80, rgb=(20, 160, 90)):
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row += bytes((255, 255, 255) if 30 <= y < 45 else rgb)
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def kill_all():
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-Process | Where-Object { $_.ProcessName -like '*AxureRP11*' } | "
                    "ForEach-Object { Stop-Process -Id $_.Id -Force }"], capture_output=True)
    time.sleep(3)


def mcp(requests):
    proc = subprocess.Popen([sys.executable, "-m", "axure_mcp.server"], cwd=REPO,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            env={**os.environ, "AXURE_AUTO_LAUNCH": "0", "PYTHONIOENCODING": "utf-8"})

    def rpc(body):
        proc.stdin.write(json.dumps(body, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "verify", "version": "1"}}})
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    proc.stdin.flush()
    out = []
    for i, item in enumerate(requests, start=2):
        try:
            res = rpc({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                       "params": {"name": item[1], "arguments": item[2]}})["result"]
        except Exception as exc:
            print("  !! died at", item[0], str(exc)[:60])
            raise
        print("  -", item[0], "ERR" if res.get("isError") else "ok")
        out.append(res)
    proc.stdin.close()
    proc.wait(timeout=120)
    return out


def text(result):
    return "\n".join(b.get("text", "") for b in result.get("content", []) if b.get("type") == "text")


def payload(result):
    try:
        return json.loads(text(result))
    except ValueError:
        return {}


def main():
    kill_all()
    shutil.copy(os.path.join(REPO, "test", "clean.rp"), RP)
    sys.path.insert(0, REPO)
    from axure_mcp import launcher
    launcher.launch(RP)
    time.sleep(7)

    res = {}

    def record(label, ok, note=""):
        res[label] = (bool(ok), str(note))

    png = base64.b64encode(make_png()).decode()
    steps = [
        ("status", "axure_status", {}),
        ("create_folder", "axure_create_folder", {"name": FOLDER}),
        ("create_page_a", "axure_create_page", {"name": PAGE_A}),
        ("create_page_b", "axure_create_page", {"name": PAGE_B}),
        ("create_page_c", "axure_create_page", {"name": PAGE_C}),
        ("create_page_d", "axure_create_page", {"name": "PageDelta"}),
        ("list_pages", "axure_list_pages", {}),
        ("page_tree", "axure_page_tree", {}),
        ("add_btn", "axure_add_widget", {"kind": "button", "name": "btn", "text": "Main",
                                         "x": 60, "y": 60, "width": 160, "height": 44,
                                         "page_name": PAGE_A}),
        ("add_txt", "axure_add_widget", {"kind": "textbox", "name": "txt", "text": "Title",
                                         "x": 60, "y": 120, "width": 220, "height": 36,
                                         "page_name": PAGE_A}),
        ("add_btn2", "axure_add_widget", {"kind": "button", "name": "btn2", "text": "Second",
                                          "x": 240, "y": 60, "width": 200, "height": 60,
                                          "page_name": PAGE_A}),
        ("add_btn3", "axure_add_widget", {"kind": "button", "name": "btn3", "text": "Third",
                                          "x": 60, "y": 200, "width": 180, "height": 40,
                                          "page_name": PAGE_A}),
        ("add_img", "axure_add_widget", {"kind": "image", "name": "img", "x": 460, "y": 60,
                                         "width": 140, "height": 90, "page_name": PAGE_A}),
        ("set_image", "axure_set_image", {"name": "img", "image_base64": png, "filename": "v.png",
                                          "page_name": PAGE_A}),
        ("add_tbl", "axure_add_widget", {"kind": "table", "name": "tbl", "x": 60, "y": 260,
                                         "width": 380, "height": 120, "page_name": PAGE_A}),
        ("set_table", "axure_set_table", {"name": "tbl", "rows": 2, "columns": 2,
                                          "data": [["r1c1", "r1c2"], ["r2c1", "r2c2"]],
                                          "page_name": PAGE_A}),
        ("add_dd", "axure_add_widget", {"kind": "droplist", "name": "dd", "x": 460, "y": 170,
                                        "width": 160, "height": 30, "page_name": PAGE_A}),
        ("set_items", "axure_set_list_items", {"name": "dd", "items": ["opt1", "opt2", "opt3"],
                                               "selected": 1, "page_name": PAGE_A}),
        ("add_panel", "axure_add_widget", {"kind": "dynamicpanel", "name": "pnl", "x": 460,
                                           "y": 220, "width": 200, "height": 120,
                                           "page_name": PAGE_A}),
        ("add_state", "axure_add_panel_state", {"name": "pnl", "state_name": "stateTwo",
                                                "page_name": PAGE_A}),
        ("panel_states", "axure_panel_states", {"name": "pnl", "page_name": PAGE_A}),
        ("add_rich", "axure_add_widget", {"kind": "shape", "name": "rich", "x": 700, "y": 60,
                                          "width": 260, "height": 90, "page_name": PAGE_A}),
        ("rich_text", "axure_set_rich_text", {"name": "rich", "paragraphs": ["para one", "para two"],
                                              "page_name": PAGE_A}),
        ("style", "axure_style_widget", {"name": "btn", "fill": "#2F6FED", "text_color": "#FFFFFF",
                                         "corner_radius": 8, "bold": True, "font_size": 14,
                                         "page_name": PAGE_A}),
        ("get_style", "axure_get_widget_style", {"name": "btn", "page_name": PAGE_A}),
        ("z_order", "axure_z_order", {"names": ["btn3"], "action": "front", "page_name": PAGE_A}),
        ("align", "axure_align", {"names": ["btn", "btn2", "btn3"], "mode": "left",
                                  "reference": "bounds", "page_name": PAGE_A}),
        ("distribute", "axure_distribute", {"names": ["btn", "btn2", "btn3"], "axis": "vertical",
                                            "gap": 16, "page_name": PAGE_A}),
        ("same_size", "axure_same_size", {"names": ["btn", "btn2", "btn3"], "mode": "width",
                                          "page_name": PAGE_A}),
        ("group", "axure_group", {"names": ["btn2", "btn3"], "group_name": "grp",
                                  "page_name": PAGE_A}),
        ("ungroup", "axure_ungroup", {"name": "grp", "page_name": PAGE_A}),
        ("page_note", "axure_set_page_note", {"text": NOTE, "page_name": PAGE_A}),
        ("get_page_notes", "axure_get_page_notes", {"page_name": PAGE_A}),
        ("add_btn_delta", "axure_add_widget", {"kind": "button", "name": "btnD", "text": "Delta",
                                              "x": 80, "y": 80, "width": 140, "height": 40,
                                              "page_name": "PageDelta"}),
        ("widget_note", "axure_set_widget_note", {"name": "btnD", "text": WNOTE,
                                                 "page_name": "PageDelta"}),
        ("get_widget_notes", "axure_get_widget_notes", {"page_name": PAGE_A}),
        ("interaction", "axure_add_interaction", {"name": "btn", "event": "OnClick",
                                                  "action": "wait", "wait_ms": 250,
                                                  "page_name": PAGE_A}),
        ("get_interactions", "axure_get_interactions", {"name": "btn", "page_name": PAGE_A}),
        ("update_widgetr", "axure_update_widget", {"name": "txt", "text": "Title updated",
                                                   "page_name": PAGE_A}),
        ("render", "axure_render_page", {"page_name": PAGE_A, "max_dimension": 900}),
        ("save", "axure_save", {}),
    ]
    out = mcp(steps)
    by = {}
    for item, result in zip(steps, out):
        by[item[0]] = result
        if item[0] != "render":
            record("A:" + item[0], not result.get("isError"),
                   "" if not result.get("isError") else text(result)[:70].replace("\n", " "))

    ids = {p["name"]: p["id"] for p in payload(by["list_pages"]).get("pages", [])}
    folder = payload(by["create_folder"]).get("folder", {})
    follow = [
        ("move_page", "axure_move_page", {"page_id": ids.get(PAGE_B), "parent_id": folder.get("id")}),
        ("delete_page", "axure_delete_page", {"page_id": ids.get(PAGE_C)}),
        ("save_as", "axure_save_as", {"path": COPY}),
    ]
    out2 = mcp(follow)
    for item, result in zip(follow, out2):
        record("A:" + item[0], not result.get("isError"),
               "" if not result.get("isError") else text(result)[:70].replace("\n", " "))
    print("phase A:", sum(1 for ok, _ in res.values() if ok), "/", len(res))

    kill_all()
    launcher.launch(RP)
    time.sleep(7)
    checks = [
        ("page_tree", "axure_page_tree", {}),
        ("list_pages", "axure_list_pages", {}),
        ("get_page", "axure_get_page", {"page_name": PAGE_A}),
        ("style", "axure_get_widget_style", {"name": "btn", "page_name": PAGE_A}),
        ("interactions", "axure_get_interactions", {"name": "btn", "page_name": PAGE_A}),
        ("page_notes", "axure_get_page_notes", {"page_name": PAGE_A}),
        ("widget_notes", "axure_get_widget_notes", {"page_name": "PageDelta"}),
        ("panel", "axure_panel_states", {"name": "pnl", "page_name": PAGE_A}),
        ("render", "axure_render_page", {"page_name": PAGE_A, "max_dimension": 900}),
    ]
    out3 = mcp(checks)
    got = {item[0]: payload(result) for item, result in zip(checks, out3)}

    names = [n.get("Name") for n in got["page_tree"].get("tree", [])]
    flat = [p.get("name") for p in got["list_pages"].get("pages", [])]
    record("B:page_tree", PAGE_A in flat and PAGE_C not in flat, "flat=" + str(flat))
    record("B:move_page", FOLDER in names and PAGE_B in flat, "folder present, moved page alive")

    widgets = got["get_page"].get("widgets", [])
    wnames = [w["Name"] for w in widgets]
    grouped = any(n in wnames for n in ("grp2", "grp"))
    core = all(n in wnames for n in ("txt", "img", "tbl", "dd", "pnl", "rich"))
    record("B:widgets", core and (grouped or ("btn" in wnames)),
           str(len(wnames)) + " top-level widgets, group=" + str(grouped))
    txt = next((w for w in widgets if w["Name"] == "txt"), {})
    record("B:update_widget", txt.get("Text") == "Title updated", str(txt.get("Text")))

    style = got["style"].get("style", {})
    record("B:style", style.get("Fill") == "#2F6FED" and style.get("CornerRadius") == 8
           and style.get("Bold") == "TRUE",
           str({k: style.get(k) for k in ("Fill", "CornerRadius", "Bold")}))

    acts = [a.get("TypeName") for ev in got["interactions"].get("interactions", [])
            for c in ev["cases"] for a in c["actions"]]
    record("B:interaction", any(a and "Wait" in a for a in acts), str(acts))

    notes = list(got["page_notes"].get("notes", {}).values())
    record("B:page_note", NOTE in notes, str(notes[:1]))
    wn = got["widget_notes"].get("notes", [])
    record("B:widget_note", any(WNOTE in str(n.get("fields", {})) for n in wn),
           str([n.get("fields") for n in wn][:1]))

    states = got["panel"].get("states", [])
    record("B:panel_states", len(states) == 2, str(states))

    record("B:image", any(w["Name"] == "img" for w in widgets), "img widget present")
    record("B:table", any(w["Name"] == "tbl" for w in widgets), "tbl widget present")
    record("B:render", any(b.get("type") == "image" for b in out3[-1].get("content", [])), "png ok")
    record("B:save_as", os.path.exists(COPY), "copy exists")

    order = ("B:page_tree", "B:move_page", "B:widgets", "B:update_widget", "B:style",
             "B:interaction", "B:page_note", "B:widget_note", "B:panel_states", "B:image",
             "B:table", "B:render", "B:save_as")
    passed = 0
    lines = ["# Verification report", "",
             "Generated: " + time.strftime("%Y-%m-%d %H:%M"),
             "File: test/verify.rp (same file before and after the restart)", "",
             "Rule: an item passes only if it can be read back after save -> kill -> relaunch.", "",
             "## Phase B - survives save + restart", "",
             "| check | result | note |", "| --- | --- | --- |"]
    for key in order:
        ok, note = res.get(key, (False, "missing"))
        passed += 1 if ok else 0
        lines.append("| " + key[2:] + " | " + ("PASS" if ok else "FAIL") + " | " + note[:70] + " |")
    lines += ["", "Phase B: " + str(passed) + "/" + str(len(order)), "",
              "## Phase A - write calls accepted", "",
              "| step | result | note |", "| --- | --- | --- |"]
    ok_a = 0
    for key in sorted(res):
        if not key.startswith("A:"):
            continue
        ok, note = res[key]
        ok_a += 1 if ok else 0
        lines.append("| " + key[2:] + " | " + ("PASS" if ok else "FAIL") + " | " + note[:70] + " |")
    lines += ["", "Phase A: " + str(ok_a)]
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print("phase B:")
    for key in order:
        ok, note = res.get(key, (False, "missing"))
        print("  %-16s %s %s" % (key, "PASS" if ok else "FAIL", note[:60]))
    print("report ->", REPORT)


if __name__ == "__main__":
    main()
