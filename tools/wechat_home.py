"""WeChat chat-list home screen, drawn entirely through the MCP ops layer.

Notes on Axure behaviour this works around:
- a textbox carries its own white fill, so a label placed on a coloured block hides it. Text that
  must sit on a colour goes INSIDE the shape widget instead; plain labels get their fill set to the
  background colour they sit on (transparent fill is not preserved: #00FFFFFF is stored as #FFFFFF).
"""
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from axure_mcp import launcher
from axure_mcp.ops import Axure

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RP = os.path.join(REPO, "test", "wechat.rp")
SHOT = os.path.join(REPO, "analysis", "wechat-home.png")

W = 390
GREEN = "#07C160"
HEADER_BG = "#EDEDED"
WHITE = "#FFFFFF"
DIVIDER = "#E5E5E5"
GRAY = "#B2B2B2"
DARK = "#191919"
TABBAR_BG = "#F7F7F7"
PAGE_BG = "#FFFFFF"

CHATS = [
    ("文件传输助手", "[文件] 设计稿-微信主页.png", "刚刚", 0, "#5B7FFF"),
    ("产品需求群", "李雷：评审改到下午三点", "10:24", 3, "#FF9F43"),
    ("张伟", "好的，我这就发你", "09:58", 0, "#4ECDC4"),
    ("妈妈", "[语音] 12″", "昨天", 0, "#F368E0"),
    ("设计小组", "韩梅梅：视觉稿已更新", "昨天", 1, "#A55EEA"),
    ("王小明", "文件已接收", "周二", 0, "#54A0FF"),
    ("运营数据播报", "[链接] 3月数据周报", "周一", 0, "#26C281"),
    ("刘洋", "收到，谢谢！", "周一", 0, "#FF6B6B"),
]

TABS = [("微信", True), ("通讯录", False), ("发现", False), ("我", False)]


def kill_all():
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-Process | Where-Object { $_.ProcessName -like '*AxureRP11*' } | "
                    "ForEach-Object { Stop-Process -Id $_.Id -Force }"], capture_output=True)
    time.sleep(3)


def build(app, pkg):
    add, st = app.add_widget, app.style

    def block(name, x, y, w, h, fill, radius=0, border=None, border_w=0, text=None,
              text_color=WHITE, size=14, bold=True, align="center", valign="middle"):
        handle = add(pkg, "shape", name, text, x, y, w, h)
        opts = {"fill": fill, "corner_radius": radius}
        if border:
            opts["border_color"] = border
            opts["border_width"] = border_w or 1
        if text:
            opts.update({"text_color": text_color, "font_size": size, "bold": bold,
                         "align": align, "valign": valign, "font": "Microsoft YaHei"})
        st(pkg, handle=handle, **opts)
        return handle

    def label(name, text, x, y, w, h, size=14, color=DARK, bold=False,
              align="left", bg=PAGE_BG):
        handle = add(pkg, "textbox", name, text, x, y, w, h)
        st(pkg, handle=handle, font_size=size, text_color=color, bold=bold,
           align=align, valign="middle", font="Microsoft YaHei", fill=bg, border_width=0)
        return handle

    # ---------- status bar + nav ----------
    block("hdr", 0, 0, W, 128, HEADER_BG)
    label("clock", "9:41", 20, 12, 60, 20, size=14, bold=True, bg=HEADER_BG)
    block("batBody", W - 42, 18, 22, 11, WHITE, radius=3, border="#7A7A7A", border_w=1)
    block("batFill", W - 40, 20, 15, 7, "#4C4C4C", radius=1)
    label("plus", "+", W - 44, 48, 28, 32, size=24, bold=False, align="center", bg=HEADER_BG)
    label("hdrTitle", "微信", 145, 50, 100, 30, size=17, bold=True, align="center", bg=HEADER_BG)
    block("search", 12, 92, W - 24, 32, WHITE, radius=6)
    block("searchIcon", 26, 100, 12, 12, WHITE, radius=6, border="#9A9A9A", border_w=1)
    label("searchTxt", "搜索", 46, 92, 120, 32, size=13, color=GRAY, bg=WHITE)

    # ---------- chat rows ----------
    top, row_h = 138, 68
    for i, (name, preview, when, unread, colour) in enumerate(CHATS):
        y = top + i * row_h
        block(f"av{i}", 14, y + 10, 48, 48, colour, radius=6,
              text=name[0], size=20)
        label(f"nm{i}", name, 76, y + 11, 216, 23, size=16)
        label(f"pv{i}", preview, 76, y + 35, 236, 20, size=13, color="#8A8A8A")
        label(f"tm{i}", when, 288, y + 11, 88, 18, size=11, color=GRAY, align="right")
        if unread:
            block(f"bd{i}", 334, y + 33, 20, 20, "#FA5151", radius=10,
                  text=str(unread), size=11)
        if i < len(CHATS) - 1:
            block(f"dv{i}", 76, y + row_h - 1, W - 76, 1, DIVIDER)

    # ---------- tab bar ----------
    tab_y = 760
    block("tabBg", 0, tab_y, W, 84, TABBAR_BG)
    block("tabLine", 0, tab_y, W, 1, "#DDDDDD")
    slot = W / 4
    for i, (name, active) in enumerate(TABS):
        cx = slot * i + slot / 2
        colour = GREEN if active else "#7A7A7A"
        block(f"ico{i}", cx - 12, tab_y + 12, 24, 24, colour, radius=5,
              text=name[0], size=13)
        label(f"tab{i}", name, cx - 30, tab_y + 40, 60, 18, size=10, color=colour,
              align="center", bg=TABBAR_BG)


def main():
    print("[1/5] 关掉旧实例、复制干净文件")
    kill_all()
    shutil.copy(os.path.join(REPO, "test", "clean.rp"), RP)

    print("[2/5] 启动 Axure（注入桥）")
    launcher.launch(RP)
    time.sleep(8)

    app = Axure()
    if not app.file_name().lower().endswith("wechat.rp"):
        app.open_file(RP)
    if not app.file_name().lower().endswith("wechat.rp"):
        print("      中止：文档不是 wechat.rp ->", app.file_name())
        return
    print("      文档:", app.file_name())

    print("[3/5] 新建页面「微信」")
    page = app.create_page("微信")
    pkg = app.open_page(page["id"])

    print("[4/5] 画控件：状态栏 / 导航 / 搜索 / 8 条会话 / 底部 Tab")
    t0 = time.time()
    build(app, pkg)
    print("      完成：%d 个控件，%.1f 秒" % (len(app.widgets(pkg)), time.time() - t0))

    print("[5/5] 保存 + 渲染 PNG")
    app.save(pkg)
    png = app.render_page(page["id"], 900)
    with open(SHOT, "wb") as fh:
        fh.write(png)
    print("      ->", SHOT, "(%d 字节)" % len(png))


if __name__ == "__main__":
    main()
