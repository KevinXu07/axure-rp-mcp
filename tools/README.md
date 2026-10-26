# tools/

只保留六个能长期用的脚本。其余 350 多个探索脚本全在 `scratch/` 里（保留但不再维护，可随时删）。

| 脚本 | 用途 | 怎么跑 |
| --- | --- | --- |
| `health.py` | 十秒体检：桥在不在、文档开没开、有几个页面 | `python tools\health.py` |
| `mcp_smoke2.py` | MCP 协议层自检：握手、tools/list、参数校验、错误码 | `python tools\mcp_smoke2.py` |
| `regression_ascii.py` | 全量回归：40+ 写操作 → 保存 → 杀进程 → 重开 → 断言，产出 `docs/VERIFICATION.md` | `python tools\regression_ascii.py` |
| `demo_v2.py` | 从零生成一个多页演示原型（页面/样式/图片/表格/面板状态/分组/交互） | `python tools\demo_v2.py` |
| `wechat_home.py` | 微信主页复刻（60 个控件，含精确排版） | `python tools\wechat_home.py` |
| `wechat_verify.py` | 重启后复查微信页：哈希比对 + 关键属性读回 | `python tools\wechat_verify.py` |

## 跑之前

```powershell
chcp 65001 > $null                     # 否则中文在控制台是乱码
$env:PYTHONIOENCODING = "utf-8"
```

Axure 必须是由 `tools\axure-hook.ps1` 启动的（注入桥），已经在跑的窗口接不上去。

## 注意：脚本里有些"错误"是故意的

`regression_ascii.py` 和 `wechat_verify.py` 里的 `kill_all()` **故意杀掉 Axure**，用来验证
"保存后重开数据还在"。看到 `ConnectionRefusedError` 别慌，那是杀进程之后的正常现象——
真正的判据是最后那几行 PASS/FAIL 和渲染哈希。

## scratch/

历史探索脚本，记录了我怎么一点点摸清 Axure 的 .NET 内部结构。**不参与构建、不进文档**，
留着只是方便回溯"当时为什么这么写"。想清干净直接 `Remove-Item tools\scratch -Recurse`。
