"""Reopen the WeChat mock in a fresh Axure process and prove the page survived."""
import hashlib
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from axure_mcp import launcher
from axure_mcp.ops import Axure

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RP = os.path.join(REPO, "test", "wechat.rp")
BEFORE = os.path.join(REPO, "analysis", "wechat-home.png")
AFTER = os.path.join(REPO, "analysis", "wechat-after-reload.png")


def kill_all():
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-Process | Where-Object { $_.ProcessName -like '*AxureRP11*' } | "
                    "ForEach-Object { Stop-Process -Id $_.Id -Force }"], capture_output=True)
    time.sleep(3)


def digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:16], os.path.getsize(path)


def main():
    print("before:", digest(BEFORE))
    kill_all()
    launcher.launch(RP)
    time.sleep(8)

    app = Axure()
    if not app.file_name().lower().endswith("wechat.rp"):
        app.open_file(RP)
    print("doc:", app.file_name())

    pages = app.pages()
    print("pages:", [p["name"] for p in pages])
    page = next(p for p in pages if p["name"] == "微信")
    pkg = app.open_page(page["id"])
    widgets = app.widgets(pkg)
    print("widgets after reload:", len(widgets))

    probes = {"hdrTitle": "Text", "av0": "Fill", "ico0": "Fill", "bd1": "Fill"}
    for name, field in probes.items():
        try:
            if field == "Text":
                info = next(w for w in widgets if w["Name"] == name)
                print(f"  {name}: text={info.get('Text')!r}")
            else:
                style = app.widget_style(pkg, name)
                print(f"  {name}: fill={style.get('Fill')}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name}: ERR {str(exc)[:60]}")

    png = app.render_page(page["id"], 900)
    with open(AFTER, "wb") as fh:
        fh.write(png)
    print("after :", digest(AFTER))
    print("identical:", digest(BEFORE)[0] == digest(AFTER)[0])


if __name__ == "__main__":
    main()
