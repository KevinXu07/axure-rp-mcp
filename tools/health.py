import os
"""Ten-second health check: bridge up? document open? how many pages?"""
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from axure_mcp import launcher
from axure_mcp.ops import Axure


def main() -> int:
    port = launcher.bridge_alive()
    if port is None:
        print("桥：未运行 —— 用 tools\\axure-hook.ps1 启动 Axure")
        return 1
    print(f"桥：127.0.0.1:{port} 在线")
    try:
        app = Axure()
        doc = app.file_name()
        pages = app.pages()
    except Exception as exc:  # noqa: BLE001
        print(f"文档：读取失败 —— {type(exc).__name__}: {str(exc).splitlines()[0][:100]}")
        return 1
    print(f"文档：{doc or '(未保存的新文档)'}")
    print(f"页面：{len(pages)} 个")
    for page in pages[:10]:
        print(f"  - {page['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
