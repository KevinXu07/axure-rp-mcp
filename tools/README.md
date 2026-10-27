# tools/

只保留长期使用的回归与演示脚本。其余探索脚本不进入公开交付。

| 脚本 | 用途 | 怎么跑 |
| --- | --- | --- |
| `health.py` | 十秒体检：桥在不在、文档开没开、有几个页面 | `python tools\health.py` |
| `mcp_smoke2.py` | MCP 协议层自检：握手、tools/list、参数校验、错误码 | `python tools\mcp_smoke2.py` |
| `regression_ascii.py` | 全量回归：40+ 写操作 → 保存 → 杀进程 → 重开 → 断言，产出 `docs/VERIFICATION.md` | `python tools\regression_ascii.py` |
| `regression_macos.py` | Mac 基线：35 个可用工具里的基础能力、16 类控件、文件复制/重开、重启读回 | 见 `docs/MACOS.md` |
| `regression_macos_advanced.py` | Mac 高级能力：文件夹、备注、列表/图片/表格、分组、面板状态和交互；85 次真实 MCP 调用 | 见 `docs/MACOS.md` |
| `demo_v2.py` | 从零生成一个多页演示原型（页面/样式/图片/表格/面板状态/分组/交互） | `python tools\demo_v2.py` |
| `wechat_home.py` | 微信主页复刻（60 个控件，含精确排版） | `python tools\wechat_home.py` |
| `wechat_verify.py` | 重启后复查微信页：哈希比对 + 关键属性读回 | `python tools\wechat_verify.py` |

## 跑之前

```powershell
chcp 65001 > $null                     # 否则中文在控制台是乱码
$env:PYTHONIOENCODING = "utf-8"
```

Axure 必须由本项目的注入入口启动（Windows 用 `tools\axure-hook.ps1`，Mac 用
`python3 -m axure_mcp.launcher`），已经在跑的未注入窗口接不上去。

## 注意：脚本里有些"错误"是故意的

Windows 的 `regression_ascii.py` 和 `wechat_verify.py` 会有意重启 Axure，用来验证
"保存后重开数据还在"。Mac 的 `regression_macos*.py` 只做 exercise/verify，不杀进程：
用户需要自行优雅退出并用同一文件重新启动注入版 Axure。真正判据是报告中的新 PID 与逐项状态比对。

## scratch/

历史探索脚本，记录了我怎么一点点摸清 Axure 的 .NET 内部结构。**不参与构建、不进文档**，
留着只是方便回溯"当时为什么这么写"。想清干净直接 `Remove-Item tools\scratch -Recurse`。
