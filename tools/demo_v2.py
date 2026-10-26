"""End-to-end demo v2: adds dynamic panel states, group/ungroup and same_size to the prototype."""
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
RP = os.path.join(REPO, "test", "demo2.rp")
SHOT = os.path.join(REPO, "analysis", "demo2-home.png")


def make_png(width=240, height=140, rgb=(47, 111, 237)):
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row += bytes((255, 255, 255) if ((y // 28) % 2 == 0 and 60 <= x < 180) else rgb)
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
                    "clientInfo": {"name": "demo2", "version": "1"}}})
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    proc.stdin.flush()
    out = []
    for i, item in enumerate(requests, start=2):
        out.append(rpc({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                        "params": {"name": item[1], "arguments": item[2]}})["result"])
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

    img = base64.b64encode(make_png()).decode()
    steps = [
        ("folder", "axure_create_folder", {"name": "业务模块"}),
        ("page_home", "axure_create_page", {"name": "首页"}),
        ("page_detail", "axure_create_page", {"name": "详情页"}),
        ("pages", "axure_list_pages", {}),
    ]
    out = mcp(steps)
    ids = {p["name"]: p["id"] for p in payload(out[3]).get("pages", [])}
    home, detail = ids["首页"], ids["详情页"]

    build = [
        ("title", "axure_add_widget", {"kind": "textbox", "name": "title", "text": "产品中心",
                                       "x": 60, "y": 40, "width": 420, "height": 46,
                                       "page_name": "首页"}),
        ("title_style", "axure_style_widget", {"name": "title", "font_size": 26, "bold": True,
                                               "font": "Microsoft YaHei", "text_color": "#0F172A",
                                               "page_name": "首页"}),
        ("card", "axure_add_widget", {"kind": "shape", "name": "card", "x": 60, "y": 110,
                                      "width": 420, "height": 200, "page_name": "首页"}),
        ("card_style", "axure_style_widget", {"name": "card", "fill": "#F8FAFC",
                                              "border_color": "#E2E8F0", "border_width": 1,
                                              "corner_radius": 12, "page_name": "首页"}),
        ("hero", "axure_add_widget", {"kind": "image", "name": "hero", "x": 80, "y": 130,
                                      "width": 180, "height": 110, "page_name": "首页"}),
        ("hero_img", "axure_set_image", {"name": "hero", "image_base64": img,
                                         "filename": "hero.png", "page_name": "首页"}),
        ("cardTitle", "axure_add_widget", {"kind": "textbox", "name": "cardTitle",
                                           "text": "智能工作台", "x": 280, "y": 130,
                                           "width": 180, "height": 32, "page_name": "首页"}),
        ("cardTitle_style", "axure_style_widget", {"name": "cardTitle", "font_size": 16,
                                                    "bold": True, "font": "Microsoft YaHei",
                                                    "page_name": "首页"}),
        ("filter", "axure_add_widget", {"kind": "droplist", "name": "filter", "x": 280, "y": 172,
                                        "width": 180, "height": 30, "page_name": "首页"}),
        ("filter_items", "axure_set_list_items", {"name": "filter",
                                                  "items": ["全部", "进行中", "已完成"],
                                                  "selected": 0, "page_name": "首页"}),
        ("goDetail", "axure_add_widget", {"kind": "button", "name": "goDetail", "text": "查看详情",
                                          "x": 280, "y": 214, "width": 180, "height": 40,
                                          "page_name": "首页"}),
        ("goDetail_style", "axure_style_widget", {"name": "goDetail", "fill": "#2F6FED",
                                                  "text_color": "#FFFFFF", "font_size": 14,
                                                  "bold": True, "corner_radius": 8,
                                                  "border_width": 0, "page_name": "首页"}),
        ("table", "axure_add_widget", {"kind": "table", "name": "stats", "x": 60, "y": 340,
                                       "width": 420, "height": 110, "page_name": "首页"}),
        ("table_data", "axure_set_table", {"name": "stats", "rows": 2, "columns": 3,
                                           "data": [["模块", "数量", "状态"],
                                                    ["工作台", "12", "正常"]],
                                           "page_name": "首页"}),
        # --- dynamic panel with two states ---
        ("panel", "axure_add_widget", {"kind": "dynamicpanel", "name": "tabs", "x": 540, "y": 110,
                                       "width": 300, "height": 200, "page_name": "首页"}),
        ("panel_state", "axure_add_panel_state", {"name": "tabs", "state_name": "第二页",
                                                   "page_name": "首页"}),
        # --- three buttons: same width, aligned, grouped then dissolved ---
        ("t1", "axure_add_widget", {"kind": "button", "name": "t1", "text": "标签一",
                                    "x": 540, "y": 340, "width": 90, "height": 36,
                                    "page_name": "首页"}),
        ("t2", "axure_add_widget", {"kind": "button", "name": "t2", "text": "标签二",
                                    "x": 650, "y": 340, "width": 130, "height": 36,
                                    "page_name": "首页"}),
        ("t3", "axure_add_widget", {"kind": "button", "name": "t3", "text": "标签三",
                                    "x": 800, "y": 340, "width": 60, "height": 36,
                                    "page_name": "首页"}),
        ("same_size", "axure_same_size", {"names": ["t1", "t2", "t3"], "mode": "both",
                                          "page_name": "首页"}),
        ("group", "axure_group", {"names": ["t1", "t2", "t3"], "group_name": "tabGrp",
                                  "page_name": "首页"}),
        ("ungroup", "axure_ungroup", {"name": "tabGrp", "page_name": "首页"}),
        ("nav", "axure_add_interaction", {"name": "goDetail", "event": "OnClick",
                                          "action": "navigate", "target_page_id": detail,
                                          "link_type": "Current", "page_name": "首页"}),
        ("page_note", "axure_set_page_note", {"text": "首页说明：演示原型，由 MCP 生成。",
                                              "page_name": "首页"}),
        ("save", "axure_save", {}),
        ("render", "axure_render_page", {"page_name": "首页", "max_dimension": 1100}),
        ("get_page", "axure_get_page", {"page_name": "首页"}),
        ("panel_states", "axure_panel_states", {"name": "tabs", "page_name": "首页"}),
    ]
    out = mcp(build)
    fails = [(i[0], text(r)[:90]) for i, r in zip(build, out) if r.get("isError")]
    print("steps:", len(build), "| failures:", fails if fails else "none")

    for block in out[-3].get("content", []):
        if block.get("type") == "image":
            with open(SHOT, "wb") as fh:
                fh.write(base64.b64decode(block["data"]))
            print("render:", os.path.getsize(SHOT), "bytes ->", SHOT)
    widgets = payload(out[-2]).get("widgets", [])
    print("widgets:", [w["Name"] for w in widgets])
    print("panel states:", payload(out[-1]).get("states"))

    print("\n-- restart --")
    kill_all()
    launcher.launch(RP)
    time.sleep(7)
    verify = [
        ("pages", "axure_page_tree", {}),
        ("get_page", "axure_get_page", {"page_name": "首页"}),
        ("panel", "axure_panel_states", {"name": "tabs", "page_name": "首页"}),
        ("inter", "axure_get_interactions", {"name": "goDetail", "page_name": "首页"}),
        ("note", "axure_get_page_notes", {"page_name": "首页"}),
        ("render", "axure_render_page", {"page_name": "首页", "max_dimension": 1100}),
    ]
    out2 = mcp(verify)
    print("tree:", [n["Name"] for n in payload(out2[0]).get("tree", [])])
    print("widgets after reload:", [w["Name"] for w in payload(out2[1]).get("widgets", [])])
    print("panel states:", payload(out2[2]).get("states"))
    print("interactions:", [a.get("TypeName") for ev in payload(out2[3]).get("interactions", [])
                            for c in ev["cases"] for a in c["actions"]])
    print("page note:", payload(out2[4]).get("notes"))
    for block in out2[5].get("content", []):
        if block.get("type") == "image":
            path = os.path.join(REPO, "analysis", "demo2-after-reload.png")
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(block["data"]))
            print("re-render:", os.path.getsize(path), "bytes")


if __name__ == "__main__":
    main()
