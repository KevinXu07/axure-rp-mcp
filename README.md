# axure-mcp — 让 AI 真正「改」Axure RP 11

Axure RP 11 自带一个 MCP server，但只会读：`list_documents / get_sitemap / get_page /
get_diagram / get_interactions / get_annotations / get_widget_image / get_screenshot / get_selection`，
一个能改的工具都没有。

这个项目补上了写能力：**在 Axure 进程内注入一个反射桥**，直接操作它自己的文档模型——
建控件、改文字、挪位置、删控件、渲染预览、写回 `.rp` 文件，全部走 Axure 原生的数据结构，
所以画布会立刻变、存档能存档。Windows 的工具插入链路可撤销；Mac 当前支持内容编辑与原生
Preview，但直接模型插入不会产生 Axure 的原生撤销记录。

```
你: 在 Page 1 上做一个登录页：标题、邮箱、密码、记住我、登录按钮
AI: → axure_add_widget ×5 → axure_render_page → axure_save
    ┌──────────────────────────┐
    │  产品登录                 │
    │  ┌────────────────────┐  │
    │  │ 请输入邮箱地址      │  │
    │  └────────────────────┘  │
    │  ┌────────────────────┐  │
    │  │ 请输入密码          │  │
    │  └────────────────────┘  │
    │  □ 记住我                 │
    │  ┌────────────────────┐  │
    │  │      立即登录       │  │
    │  └────────────────────┘  │
    └──────────────────────────┘
```

（上图是 Axure 自己的渲染器输出的，见 `analysis/final-render.png`）

## 平台支持

Windows 注册 44 个工具。macOS 是独立适配层，已在 **Apple Silicon / Axure RP 11.0.0.4137 /
.NET 8.0.11** 上通过真实 stdio MCP 调用、原生画布与 Preview、保存后退出并用新 PID 重启读回验证：
支持 **35 个工具、16 类控件创建**。页面/文件夹、列表、图片替换、表格、富文本、动态面板状态、
页面与控件备注、分组、层级、7 类交互动作均可持久化。

Mac 暂不宣称 `axure_render_page` 与 `axure_undo`（也不提供 Windows 新增的
`axure_copy_widget` / `axure_get_widget_image` / `axure_redo` / `axure_group_add` /
`axure_group_remove` / `axure_style_page` / `axure_get_page_style`，以及 Windows 已扩展的其余
10 类交互动作）；`connector`、`textlink` 创建会被拒绝。
其他 Mac 版本会在访问文档模型前明确报版本不支持，Intel Mac 尚未实测。

安装、验证脚本、逐项限制和修复记录见 **[docs/MACOS.md](docs/MACOS.md)**。下方完整能力矩阵是上游 Windows 的验证结果，不代表 Mac 的全部能力。

## 工具

| 工具 | 作用 |
| --- | --- |
| `axure_status` | 桥是否在线、当前打开的文档 |
| `axure_list_pages` / `axure_page_tree` | 页面列表 / 完整站点树（含文件夹） |
| `axure_create_page` / `axure_create_folder` | 新建页面 / 文件夹（可嵌套） |
| `axure_rename_page` / `axure_delete_page` / `axure_move_page` | 改名 / 删除 / 移动（重排顺序） |
| `axure_get_page` | 页面上控件的几何、文字、可见性；Mac 可用 `deep=true` 读取分组成员及列表/图片/表格/面板内容 |
| `axure_add_widget` | 新建控件（button / textbox / textarea / checkbox / radiobutton / droplist / listbox / dynamicpanel / image / inlineframe / table / repeater / connector / group / hotspot / snapshot / textlink / shape）；Mac 不支持 connector / textlink |
| `axure_update_widget` | 改文字 / 位置 / 尺寸 / 可见性 / 改名 |
| `axure_copy_widget` | **复制控件**（Windows）：Axure 自己的剪贴板管线，深拷贝文本/样式/几何/交互，落点可指定，自动去重命名，可 Ctrl+Z |
| `axure_delete_widget` | 删除控件 |
| `axure_style_widget` / `axure_get_widget_style` | **样式**：填充色、边框色/宽度、圆角、透明度、字体/字号/加粗/斜体/下划线、水平与垂直对齐、行距、字距、内边距、旋转 |
| `axure_add_interaction` / `axure_get_interactions` | **交互**：事件 → 用例 → 动作。**17 个动作全部可用**（navigate / close / wait / set_opacity / move / rotate / resize / show_hide / scroll_to / focus / set_image / apply_style / enable_disable / link_frame / panel_order / expand_collapse / set_panel_state），读回带目标控件与值；Mac 每批最多 100 个动作并支持多 case，暂只开放 7 类 |
| `axure_align` / `axure_distribute` | **对齐与分布**：左/水平居中/右/上/垂直居中/下，沿水平或垂直等距铺开（可指定间距） |
| `axure_same_size` | **等宽等高**：以第一个控件为基准统一宽/高/两者 |
| `axure_group` / `axure_ungroup` | **分组/解散**：多个控件合成 Group；Mac 保持成员全局对象身份，嵌套成员仍可按持久 GUID 编辑 |
| `axure_undo` | Windows 撤销；Mac 不提供，直接模型插入不会生成原生撤销项 |
| `axure_z_order` | 层级；Mac 对顶层控件支持 front / back |
| `axure_set_list_items` | 下拉/列表框选项；Mac 支持整表替换和空列表 |
| `axure_open_file` | 打开 `.rp`；Mac 使用独立文档窗口并跟随当前窗口，Windows 在现有窗口加载 |
| `axure_render_page` | Windows 用 Axure 渲染管线出 PNG；Mac 可用原生 Publish > Preview，但不提供此工具内的 PNG |
| `axure_save` | 把改动写进 `.rp` |
| `axure_set_rich_text` | **富文本/多段落**：shape 走 RichTextPanel，纯文本控件自动回退为换行字符串 |
| `axure_panel_states` / `axure_add_panel_state` | **动态面板状态**：读状态列表 / 复制新增状态（可改名） |
| `axure_set_table` | **表格**：设置行列与单元格文字（二维数组，行优先）；Mac 支持行列尺寸，最多 10,000 个单元格 |
| `axure_set_image` | **图片**：把 base64 图片嵌进 ImageBox；Mac 可原位换图并通过 SHA-256 校验，Windows 同一控件只接受首次赋值 |
| `axure_get_widget_image` | **图片读回**（Windows）：读 ImageBox 内嵌图片的字节数与 SHA-256，`include_data=true` 附 base64 原始字节，用于校验 set_image 的落盘结果 |
| `axure_set_page_note` / `axure_get_page_notes` | **页面备注**（Notes 面板的字段，如 Page overview） |
| `axure_set_widget_note` / `axure_get_widget_notes` | **控件备注**（按 OwnerId 绑定控件，读回带控件名）；Windows 与 Mac 都可与页面备注同页共存，并随保存/重启保留 |
| `axure_save_as` | 另存为（先落盘再复制文件；Axure 自带的另存为会弹模态框把桥卡死） |

> 想看这些工具串起来能做什么：**[docs/DEMO.md](docs/DEMO.md)** —— 从零搭一个多页原型的完整流程
> （页面 + 样式 + 图片 + 表格 + 下拉 + 动态面板状态 + 等宽/分组/解散 + 交互 + 备注），
> 共 28 步，并附重启后的逐项复查结果。复现：`python tools/demo_v2.py`。


## 安装

macOS 使用 net8 桥：

```sh
dotnet build bridge/AxureBridge.csproj -c Debug -f net8.0
python3 -m axure_mcp.launcher /absolute/path/to/disposable-test.rp
```

launcher 会校验 `.rp` 路径，并在 POSIX 上把 Axure 放进独立会话；启动它的终端或 AI 会话退出后，
注入版 Axure 仍可继续使用。已经运行的桥会被复用。

完整 MCP 配置、App/可执行文件覆盖、真实 Axure 回归与边界见
**[docs/MACOS.md](docs/MACOS.md)**。Windows 安装如下。

需要：

1. **.NET SDK 10**（Axure RP 11 跑在 net10.0 上，注入的桥必须同版本）
   ```powershell
   winget install Microsoft.DotNet.SDK.10
   ```
2. **Python 3.10+**（MCP server 只用标准库，无需 pip install）
3. Axure RP 11 默认装在 `D:\Program Files (x86)\Axure\Axure RP 11`

编译注入桥：

```powershell
dotnet build bridge\AxureBridge.csproj -c Debug -f net10.0      # 桥要自己编译，仓库里不带二进制
copy bridge\bin\Debug\net10.0\AxureBridge.dll bridge\dist\AxureBridge.dll
# 注意：axure-hook.ps1 注入的是 Debug 产物；改了桥代码一定要重新 build，
#      否则跑的还是旧桥（会报 "method ... not found" 这种莫名其妙的错）
```

注册到 MCP 客户端（Claude Desktop / Claude Code / Cursor 等）：

```json
{
  "mcpServers": {
    "axure": {
      "command": "python",
      "args": ["-m", "axure_mcp.server"],
      "cwd": "E:\\code\\axure-mcp",
      "env": { "AXURE_AUTO_LAUNCH": "1" }
    }
  }
}
```

首次调用工具时，server 会自动带注入钩子启动 Axure（`DOTNET_STARTUP_HOOKS`），
也可以在启动前自己开好：

```powershell
$env:DOTNET_STARTUP_HOOKS = "<仓库路径>\bridge\dist\AxureBridge.dll"
& "D:\Program Files (x86)\Axure\Axure RP 11\AxureRP11.exe" "你的文件.rp"
```

## 这个项目现在能做什么

一句话：**用 AI 操作 Axure RP 11 画原型**——建页面、摆控件、套样式、插图片/表格/下拉、做分组与对齐、
连交互、写备注、加动态面板状态，最后存盘并导出 PNG。全部通过 MCP 工具调用，不需要人工点界面。

三条使用前提：

1. **Windows 上游要求使用 Kali/演练专用的虚拟机；Mac 只应在这台开发机的受控账号中运行。**
   桥会把 Axure 进程的任意方法暴露给本机 127.0.0.1:41999，安全软件也容易误报。
2. Axure 必须**由本项目的脚本启动**（注入 `DOTNET_STARTUP_HOOKS`），已经在跑的窗口接不上去。
3. 一次只让一个 AI 会话操作一个 `.rp`，多会话并发会互相抢窗口。

快速开始：

```powershell
cd <仓库路径>
powershell -ExecutionPolicy Bypass -File tools\axure-hook.ps1 -OpenFile 你的文件.rp   # 或直接让 MCP 自动拉起
python tools\regression_ascii.py                   # 回归自检
python tools\demo_v2.py                            # 从零生成一个演示原型
```

## MCP 层做了什么

不只是把方法包一层，协议层本身也按 MCP 规范做了处理：

- **连接复用**：一次会话复用一个桥连接（以前每次工具调用都重新发现端口 + 建连接）。
  连续 5 次调用实测 **0.01 秒**。
- **参数校验**：按工具的 `inputSchema` 预检——缺必填、多传字段、枚举值不对、类型不对，
  都返回可读的错误（例如 `mode='diagonal' is not one of ['left','hcenter',...]`），
  而不是让 Python 抛 `TypeError`。
- **断线自愈**：Axure 被杀掉后，下一次工具调用会**自动重新拉起**（`AXURE_AUTO_LAUNCH=1` 时，
  默认开启），实测 **11.7 秒**恢复（早期版本 56 秒——每次轮询把 20 个死端口都扫了一遍）。
  设 `AXURE_AUTO_LAUNCH=0` 可关闭。`axure_status` 会返回 `remembered_file`，
  告诉你崩之前开的是哪个文件，用它调 `axure_open_file` 即可继续。
- **并发安全**：多个 MCP 会话（两个编辑器、或脚本 + IDE）同时连同一个 Axure 时，
  工具调用之间用**文件锁**串行化。否则 A 会话切到自己的页面、B 会话的写操作就会落到 A 的页面上。
  实测 3 个会话各 8 次调用并发：全部成功，0.30 秒。
- **错误带提示**：连接被拒 / 超时 / 没打开文档这类错误会附一句该怎么做
  （例如"用 tools/axure-hook.ps1 启动 Axure"）。
- **协议完整性**：`initialize` / `ping` / `tools/list` / `tools/call` 正常应答；
  未知方法回 `-32601`、未知工具回 `-32602`；通知（`initialized`、`cancelled`）不误回复，
  但如果客户端给通知带了 id 也会应答，避免对端干等。

## 能力矩阵（全部经过「保存 → 重启 → 读回」验证）

判定口径：**写完之后保存、杀掉进程、重新打开同一份文件、再读回来**。只有过得了这一关才算 ✅。
自动回归脚本：`tools/regression_ascii.py`，报告：`docs/VERIFICATION.md`。

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 页面：新建 / 删除 / 重命名 / 移动 | ✅ | 文件夹同样支持 |
| 控件：新建 / 改文字与几何 / 删除 | 分平台 | Windows 新建 18 类且可 Ctrl+Z；Mac 新建 16 类，直接模型插入不产生撤销项 |
| 样式：填充 / 边框 / 圆角 / 透明度 / 字体字号 / 粗斜体 / 对齐 / 内边距 / 行距字距 | ✅ | 读写都行 |
| 交互：事件 + 用例 + 动作 | ✅ **17 个动作** | navigate / close / wait / set_opacity / move / rotate / resize / show_hide / scroll_to / focus / set_image / apply_style / enable_disable / link_frame / panel_order / expand_collapse / set_panel_state，全部过「保存→重启」审计，读回带目标与值 |
| 分组 / 解散 | ✅ | 解散用「复制回页面再删 Layer」实现 |
| 组成员编辑 | 分平台 | **Windows ✅（新增）**：`axure_group_add` / `axure_group_remove` 向已有组加/移成员，跨重启保真；Mac 未提供 |
| 页面样式（背景色） | 分平台 | **Windows ✅（新增）**：`axure_style_page` / `axure_get_page_style` 读写画布填充色；Mac 未提供 |
| 对齐 / 分布 / 等宽等高 | ✅ | 纯几何 |
| 页面备注 | ✅ | 与控件备注可同页共存，互不影响 |
| 控件备注 | 分平台 | **Windows ✅（新增）**：与页面备注共存、保存重启后保留；Mac ✅ 已验证多个控件备注与页面备注并存 |
| 控件复制 | 分平台 | **Windows ✅（新增）**：走 Axure 自己的剪贴板管线，文本/样式/几何/交互全带过去，可 Ctrl+Z；Mac 暂不提供 |
| 图片（ImageBox 内嵌 PNG） | 分平台 | Windows 首次赋值后**暂无法原位换图**（写入需编辑器样式事务上下文，见 RE 文档 26.1），换图请删掉重建；**Mac 支持同尺寸原位换图、字节读回和 SHA-256** |
| 图片读回 | 分平台 | **Windows ✅（新增）**：`axure_get_widget_image` 读回字节数与 SHA-256（可选 base64）；Mac 由 `axure_get_page` 返回内容读回 |
| 表格 | ✅ | 行列 + 单元格文字 |
| 富文本 / 多段落 | ✅ | shape 走 RichTextPanel，纯文本控件走换行 |
| 列表项（下拉/列表框） | ✅ | 整表替换 `Options` |
| 动态面板状态：读 / 新增 / 改名 | ✅ | 新增靠复制已有状态 |
| 撤销 / 重做 | 分平台 | **Windows ✅ 撤销+重做（Redo 为新增）**；Mac 直接模型插入不产生撤销项，两者都不提供 |
| 另存为 / 渲染导出 PNG | 分平台 | 另存为 = 落盘后复制文件；PNG 工具仅 Windows，Mac 原生 Preview 可人工验证 |
| 母版 / 组件 | ❌ | 手写 `mJsm` 载荷过不了序列化校验，且失败会污染内存文档 |

## 关键限制

- **必须在启动时注入**。桥靠 `DOTNET_STARTUP_HOOKS` 在 `Main()` 之前加载，
  已经在跑的 Axure 窗口接不上去（得重开一次）。重开后建议手动 `Ctrl+S` 保存一次再交给 AI。
- **改动先落在"当前打开的页面"上**。写之前会调 `client.TImN(pageId)` 把目标页面打开到窗口，
  所以 AI 动手时 Axure 窗口会切页，这是预期行为。
- **样式对象是共享的**：直接改 `style.Fill` 会污染同页所有用同一默认样式的控件，
  所以实现里每次都新建 `IHVp.uHV0` 填充对象再赋值。这个坑值得记住（第一版就踩了，
  一改按钮，整页输入框全变蓝）。
- **移动页面后不要立刻调用只读服务**：`move_page` 之后紧接着查站点服务会让进程崩。
  实现里已经绕开（`delete_page` 改用站点模型计数），并给删除加了护栏：
  不允许删最后一页、节点载荷必须匹配请求的 id。
- **分组内控件可编辑**：按名字查找会自动下潜进 Group；`widgets(deep=True)` 才会列出成员。
- **层级（z-order）只做了 front/back**：`XJ48.mJ4n` 没有可靠的"移到指定下标"原语，
  `kJnm`/`rJ4l` 会复制条目，`Remove`+`Add` 会丢掉控件内部子对象（TextArea 的文字就是这么丢的）。
- **备注注解（已修复）**：以前页面备注与控件备注同页会互相串、控件备注存盘重启后丢失。
  根因有两个：`UJiJ.gJiI` 对未赋值字段返回 **Axure 的静态共享富文本对象**（改它就是改全局默认值），
  以及 `UJiJ(package, guid)` 双参构造会把注解的对象身份设成控件的 Guid（重载时解析到控件本体，
  注解消失、注解树里多出一个控件节点）。现在写入前先 `LSmd()` 克隆富文本、只用单参构造，
  页面备注与控件备注可同页共存且都能过「保存 → 重启 → 读回」。历史脏文件由
  `widget_notes` 读取时自动修复（剔除注解树里的非注解节点）。
- **交互动作（已全量开放）**：以前只敢开放 7 个，另外 10 个"被 Axure 静默丢弃"。
  真正的根因与控件备注同源：**目标路径对象 `sJ8l.dJ8s` 用 `(package, guid)` 双参构造时，
  控件的 Guid 成了路径对象自己的身份**，存盘后的引用在重载时解析到控件本体，动作节点被吞、
  交互图被污染（fade 动作还会让整页加载抛 InvalidCastException）。另外 `DIyv` 只是描述
  格式化器，根本不注册目标；正确的目标注册是 `pIyY(path)`（路径用自带身份的 4 参构造），
  逐目标的值要在动作插入用例树之后设置（`zNzj`/`TNQY`/`qNAz`/`xSBg`/`TNzl`，它们依赖
  用例的 DataSet 上下文）。现在 17 个动作全部可用，读回带目标控件与值。
  Redo 也已随 UI 封送修复恢复可用。详见 `docs/REVERSE-ENGINEERING.md` 第 25 节。
- **动态面板状态**：读、增、改名都可用，但**新增只能靠"复制已有状态"**——
  `panel.eNaz(...)` 那条路会让文档无法序列化，已弃用。
- 已知渲染问题：`TextArea` 在设置「加粗 + 自定义字体 + 自定义填充」后，
  **快照渲染器**偶尔不画文字（数据本身在，Axure 画布正常）。用 `textbox` 或 `shape` 做标题可以规避。
- 桥监听 `127.0.0.1:41999`，**只要本机进程能连上就能操控 Axure**。
  这是本机开发工具，不要在有不受信任进程的机器上长期挂着。

## 目录

```
axure_mcp/             MCP server（Python，纯标准库）
  server.py            MCP over stdio + 工具实现
  ops.py               文档操作层（逆向出来的调用序列都在这）
  client.py            反射桥的 TCP/JSON 客户端
  launcher.py          带注入钩子启动 Axure
bridge/                注入到 Axure 进程里的 C# 反射桥
  Bridge.cs            TCP/JSON-lines 协议 + 反射调用 + UI 线程封送
  StartupHook.cs       DOTNET_STARTUP_HOOKS 入口（必须是全局命名空间）
tools/                 逆向/调试脚本（反编译、字符串解密、截图、各阶段验证脚本）
decompiled/            ILSpy 反编译产物（3000+ 文件，字符串已解密）
analysis/              逆向过程的中间产物与验证截图
docs/REVERSE-ENGINEERING.md   逆向笔记：类型映射表、坑、完整写入链路
```

## 为什么要注入

`AxureRP11.dll` 是 net10.0 + WPF，混淆过（多重模块 + 控制流平坦化 + 字符串加密）。
文档不是文本格式，`.rp` 是自定义二进制容器，页面内容 gzip 压缩后按 `(PackageId, SubPackageId)`
分槽存放，**直接改文件不现实**。

而进程内的文档模型是一套活的 WPF 对象图：页面是 `vJvL.NJvD`，图是 `hJat.OJa9`，
控件集合是 `XJ48.mJ4n`，控件基类是 `kJm2.KJm7`。注入一个 DLL 用反射调这些对象的方法，
就等价于"用 Axure 自己的手去改"——这也是它能正确刷新画布、进撤销栈、能存盘的原因。

细节都在 [docs/REVERSE-ENGINEERING.md](docs/REVERSE-ENGINEERING.md)。


