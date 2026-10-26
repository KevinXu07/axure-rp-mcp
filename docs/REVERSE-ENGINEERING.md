# Axure RP 11 逆向笔记

记录「Axure RP 11 自带 MCP 只读，如何加上写能力」这一次逆向的关键结论，
方便以后扩展（样式、交互、母版、新建页面……）。

## 1. 目标形态

| 事实 | 依据 |
| --- | --- |
| 桌面端是 **.NET 10 + WPF**（CoreCLR，非 Electron） | `AxureRP11.runtimeconfig.json`：`net10.0` + `Microsoft.WindowsDesktop.App 10.0.10` |
| 主程序集 `AxureRP11.dll` 16MB，**混淆**（多 module、控制流平坦化、字符串加密） | 元数据里 17 个 `<Module>{guid}` 类型，288 个 `zIhQ.sIho(int)` 加密串调用点 |
| 文档层有 F# 血统 | 依赖 `FSharp.Core`、`FsLexYacc`（表达式解析） |
| `.rp` 不是文本格式 | 头部 `ac ef 0b 00` + 分槽二进制；页面内容 **gzip** 压缩后按 `(PackageId, SubPackageId)` 存 |
| 自带 MCP 是**进程内 HTTP server** | 依赖 `ModelContextProtocol.AspNetCore`，`Axure.McpServer.*` 命名空间 |

## 2. 工具链

1. **反编译**：`dotnet tool install -g ilspycmd` → `ilspycmd -p -o decompiled AxureRP11.dll`
   （产物 3079 文件 / 48MB）。
2. **字符串解密**：混淆器把所有字符串字面量换成 `zIhQ.sIho(<int 表达式>)`，
   int 由模块级常量 `_003CModule_003E_..._.m_b6418...m_xxx` 算出。
   写了一个 C# 小工具（`tools/DecryptStrings`）：
   - `Assembly.LoadFrom` 加载机器上的 `AxureRP11.dll`
   - 反射拿到 `<Module>{7f12322c-...}` 单例，读出 120 个 int 常量
   - 解析反编译源码里每个 `sIho(...)` 的 XOR 表达式 → 调用真解密函数 → **就地替换成明文**
   - 结果：**24,924 处调用点、12,338 个唯一字符串、1190 个文件变成可读源码**，
     映射表见 `analysis/string-map.txt`
3. **运行期探索**：光静态读不够（混淆后方法名全是 `xImJ`/`TImN` 这种），
   所以写了**进程内反射桥**（`bridge/`），可以 `types / members / subtypes / invoke / ctor /
   get / set / dump / iter / id` 在活对象上试探。下面所有调用序列都是这样试出来的。

## 3. 自带 MCP（只读）长什么样

- 启用：环境变量 `AXURE_MCP=1`（`Axure.McpServer.McpConfig.EnvEnabled`）
- 监听：`127.0.0.1:41414..41434`（`McpHost.EnsureStarted(directory, port = 41414)`，占用则 +1 重试）
- 路由：`MapMcp("/mcp")`，另有 `GET /health`
- 发现文件：`%LOCALAPPDATA%\Axure\mcp-server.json`
- 工具类：`Axure.McpServer.Tools.RpMcpTools`，`.WithTools<RpMcpTools>()` 注册，**只有 9 个读工具**
- 另有一套给「独立前端进程」用的本地 HTTP 文档 API：`/docapi/{document,sitemap,components,
  diagram,interactions,annotations,widgetimage,screenshot,selection}`

结论：**没有任何写入口**，必须在进程内自己动手。

## 4. 为什么不能「改完集合再 Save」

这是最大的坑，三个都要绕：

1. **`LoadFragment(pageId)` 每次返回全新副本**
   `DocumentToolService.LoadFragment` 先看 `client.qImj()`（窗口当前的活包），
   拿不到才 `doc.INEj(pageId, name)` 反序列化一份新的。
   往副本里加控件读得出来、渲染得出，但下一次请求就没了。
   验证方式：连续两次拿 `Diagram`，用 `RuntimeHelpers.GetHashCode` 比 identity（不同）。
2. **`DiagramObjects.Add()` 不会通知文档**
   加进去了、画布也认、**撤销栈也认**（`KIX4().Undo` 能撤掉），
   但文档不会认为这个包「脏」，`doc.Save()` 写的是未变的旧内容（文件大小、哈希都不变）。
3. **`Document.Save()` 只写容器里已有的内容**
   `.rp` 的写回是「把活包重新序列化进容器槽位」→ 再 `Save()`。
   只调 `Save()` 等于把旧内容重写一遍。

## 5. 完整写入链路（已验证）

```csharp
// 1. 把目标页面打开到窗口（等价于点页面树）——这一步之后 qImj() 才是活对象
client.TImN(pageId, Guid.Empty, false);
IJvQ page = (IJvQ)client.qImj();          // 活包，identity 跨调用稳定
OJa9 diagram = page.Diagram;
XJ48.mJ4n objects = diagram.DiagramObjects;

// 2. 建控件（构造参数就是页面包本身；IJvQ 继承自 Ftdz.btdo）
var button = new Axure.DocumentModel.Objects.Button(page);
button.YJ0S(new h5uH.j5u5(x, y, w, h), true);   // 几何：注意是 YJ0S，不是 Rect setter
button.Name = "loginBtn";
button.Text = "立即登录";
objects.Add(button);                             // 立刻出现在画布上

// 3. 把活包序列化回容器槽位（关键！）
var ctx = new etOx.stOK(page, doc.aNEQ());       // 序列化上下文
ctx.TtUP(SubPackageHandling.SaveAllInternally);
ctx.dtO7();
Stream slot = doc.LNEY().Create(page);           // 容器里这个包的槽位
using var gz = new System.IO.Compression.GZipStream(slot, CompressionMode.Compress);
gt2C.Save(ctx, gz);                              // 必须 gzip，读的时候是 GZip 解压
gz.Dispose(); slot.Dispose();

// 4. 落盘
doc.Save();
```

### 各步的坑

| 步骤 | 错误做法 | 现象 |
| --- | --- | --- |
| 取活包 | `doc.INEj(pageId, name)` / `LoadFragment` | 每次都是新副本，写完全丢 |
| 写几何 | `widget.Rect = rect` | `Property set method not found`（只读属性），要用 `YJ0S(rect, bool)` |
| 建控件 | `new TextRectObject(page)` | 抽象类；可用具体类型 18 个，文本类用 `TextArea`/`TextBox`，矩形类用 `VectorShape` |
| 序列化 | 不写回，直接 `doc.Save()` | 画布对、文件里没有；重开就没了 |
| 序列化 | 不 gzip 直接 `gt2C.Save(ctx, slot)` | 重开报 `Loading Failed with GZip Error`，文件直接坏掉 |
| 序列化 | 写两遍（BNEW + gt2C.Save） | 同上，流被写乱 |
| 排序 | 先写回再建控件 | 写回去的是旧内容 |

## 6. 类型/方法映射表

| 混淆名 | 含义 | 关键成员 |
| --- | --- | --- |
| `mbkg.Bbk3` | 打开的文档窗口客户端 | `XIXf()` 文档、`nIXw()` 页面树、`qImj()` 当前包、`xImJ()` 当前图、`TImN()` 打开页面、`KIX4()` 命令集、`eIX8()` 包缓存、`Editor` 画布编辑器 |
| `HNR1.LNRH` | 文档 | `Save()`、`INEj(Guid,string)` 反序列化包、`aNEQ()` 文档设置、`LNEY()` 容器存储、`yNEF()` 编辑态加载 |
| `Ftdz.btdo` | 页面/包基类 | `PackageId`、`SubPackageId`、`Name`、`IsSubPackage` |
| `GJvA.IJvQ` | 页面接口（继承 `btdo`） | `Diagram`、`DiagramObjects`、`StyleSheet`、`Size` |
| `vJvL.NJvD` | 页面实现 | 上面那些 |
| `hJat.OJa9` | 图（页面画布 / 动态面板状态 / 母版） | `DiagramObjects`、`Bounds`、`PageFragment`、`iJaR(obj)` |
| `XJ48.mJ4n` | 控件集合（IList） | `Add/Insert/Remove/Clear`、`Count`、`Insert`、`sJnF`(复制用) |
| `kJm2.KJm7` | 控件基类 | `Name`、`Text`、`X/Y/Width/Height`、`Visible`、`IsLocked`、`YJ0S(rect,bool)` 设几何、`Move()` |
| `Axure.DocumentModel.Objects.*` | 具体控件 | `Button/Checkbox/RadioButton/TextBox/TextArea/ComboBox/ListBox/DynamicPanel/ImageBox/InlineFrame/Table/TableCell/Repeater/Connector/Layer/ImageMapRegion/Screenshot/InlineLink/VectorShape/MenuObject/TreeNodeObject/ReferenceDiagramObject` |
| `h5uH.j5u5` | 矩形值类型 | ctor(x,y,w,h)、`X/Y/Width/Height` |
| `m5wr.f5wV` | 点 | ctor(x,y) |
| `W30.M3m` | 文档窗口 | `Editor`、`ActiveDiagramId`、`Undo/Redo`、`KIX4()`、`qImj()` |
| `EFql.EFqs` | 画布编辑器 | `xFUI()` 组插入载荷、`LFyv`(内部 helper)、`Paste()` |
| `WJzm.KJzX` | 插入/复制载荷 | `DiagramObjects`、`PNG6(List<Guid>)`、`OJzd()` 出图 |
| `ntqt.Mtq9` | 每窗口的包缓存（实现 `Ttpq.Ntpp`） | `xtqk()` 查、`Gtq1()` 注册、`atqR()` 占用、`dIl8()` 载入 |
| `qtcD.Otc6` | `.rp` 容器存储 | `Create(package)→Stream`、`Save()`、`etcC()/mtca()` 枚举槽 |
| `etOx.stOK` | 序列化上下文 | ctor(package, settings)、`TtUP(SubPackageHandling)`、`dtO7()` |
| `pt2i.gt2C` | 包序列化器 | `Save(stOK, Stream)`、`Load(Stream, double, bool)` |
| `qbk.zbKz` / `Y5ZQ.B5Zv` | 作用域 / 结果封装 | `Dispose()` |

## 7. 反射桥协议

`bridge/AxureBridge.dll` 由 `DOTNET_STARTUP_HOOKS` 载入（类型必须叫 `StartupHook`
且在**全局命名空间**，方法 `public static void Initialize()`；带 namespace 会让进程启动直接失败）。

监听 `127.0.0.1:41999`（`AXURE_BRIDGE_PORT` 可改），newline-delimited JSON：

```jsonc
// 请求
{"id":1,"op":"invoke","handle":42,"method":"YJ0S","args":[{"$h":43},true]}
{"id":2,"op":"ctor","type":"Axure.DocumentModel.Objects.Button","args":[{"$h":12}]}
{"id":3,"op":"get","type":"HNR1.LNRH","name":"aNEQ"}
{"id":4,"op":"dump","handle":7,"depth":4,"limit":50}
// 响应
{"id":1,"ok":true,"result":null}
{"id":2,"ok":true,"result":{"$h":44,"type":"...","str":"..."}}
{"id":1,"ok":false,"error":"..."}
```

- 对象用 handle 传递（`{"$h":n}`），返回值里的复杂对象也回传 handle 供链式调用
- `$guid` / `$arr` / `$t` 分别表示 Guid、集合、Type
- 所有调用默认封送到 **UI 线程**：优先 `System.Windows.Application.Current.Dispatcher`，
  退化到 app 自己的 `kSnw.xSnT.iS8H().M3GD(Action)`（实测 WPF Dispatcher 在这个宿主里拿不到，走的是后者）
- 支持 `out`/`ref`、泛型集合参数（`IEnumerable<T>` → 自动造 `List<T>`）

## 8. 第二阶段：页面、样式、交互（已实现）

### 8.1 站点树（页面/文件夹的增删改移）

`client.nIXw()` → `nJhk.XJhE`（站点模型），`.Sitemap` → `cNrY.eNrZ`，基类 `BNrv.FNry` 就是那棵树：

| 操作 | 调用 |
| --- | --- |
| 找节点 | `sitemap.iNFj(guid)`（页面用 PackageId，文件夹用 FolderId） |
| 新建页面 | `sitemap.TNFt(null, sitemap.Tree, index, Guid)` → payload 是 **Guid** |
| 新建文件夹 | `new WJsy.kJsl(anchorPackage, name)` → `sitemap.TNFt(..., kJsl)`（payload 类型决定是"页面"还是"文件夹"） |
| 改名 | `sitemap.XIwn(node, name)` |
| 删除 | `node.qJlx()`（从父节点摘下） |
| 移动 | **先 `qJlx()` 再** `sitemap.MNF5(node, newParent, index)`；不先摘会在原位置留一份重复节点 |

`SitemapMapper.MapChildren` 读的就是 payload 类型：`Guid` → page，`kJsl` → folder，`mJsm` → component。
文件夹的 `kJsl` 构造函数需要一个文档里的包（只用来回溯文档），所以拿当前打开的页面当锚点即可。

### 8.2 样式：`KJm7.vJOH()` → `xtod.jtoq`

一个控件 182 个方法、54 个属性的样式对象，直接读写属性即可：

```
FontName FontSize Bold Italic Underline Strikethrough ForeGroundFill Fill BorderFill
BorderWidth CornerRadius Opacity HorizontalAlignment VerticalAlignment Padding* LineSpacing
CharacterSpacing LinePattern Rotation OuterShadow InnerShadow ...
```

- 枚举：`AxStringAlignment` = `Near/Center/Far/Justify`；`BoolOverride` = `IGNORE/TRUE/FALSE`；
  `LineStyle`、`Position`（边框/圆角边）、`AxFontStyle` 同名可猜。
- 颜色：`B5kg.m5k3` 结构体，`w5kx(uint argb)` 从 ARGB 造；它的四个通道是**私有字节字段**
  `p5ex(A) e5ee(R) R5eM(G) i5eC(B)`，属性只暴露了 A/R/B，所以读 G 要读 `R5eM`。
- 填充：`x5UG.t5Oz` 是抽象类，实体是 `IHVp.uHV0`，ctor 直接吃颜色。
- **坑**：`style.Fill` 拿到的是共享对象（默认样式同一实例），**就地改会污染所有同款控件**
  （第一次试验把整页输入框都改蓝了）。正确做法是新建 `IHVp.uHV0(color)` 再 `set Fill`。

### 8.3 交互：`KJm7.GIss()` → `MJuB.gJuG`

交互本身也是一棵树（`MJuB.gJuG : BNrv.FNry`），层级是 控件 → 事件 → 用例 → 动作：

```
ixMap = widget.GIss()
event  = ixMap.fJuI(IxEventType.OnClick)              // 已有则复用，否则：
       = ixMap.TNFt(null, ixMap.Tree, 0, new IxEvent(package, "OnClick"))
case   = ixMap.TNFt(null, eventNode, 0, new IxCase(package))
action = ixMap.TNFt(null, caseNode, 0, new IxLinkWindowAction(package, targetPageGuid, LinkType.Current))
```

- `IxEventType` 有 40 个值（`OnClick/OnMouseOver/OnLoad/OnShow/OnHide/OnTextChange/OnSwipeLeft/...`）。
- `LinkType`：`Invalid/Current/New/Popup/Parent/Frame/ParentFrame`。
- 可用动作类（`Axure.DocumentModel.Interaction.Ix*Action`）：
  `IxLinkWindowAction`（开页面）、`IxLinkFrameAction`、`IxLinkAction`（基类）、`IxSetPanelStateAction`、
  `IxSetImageAction`、`IxSetOpacityAction`、`IxSetWidgetSizeAction`、`IxMoveWidgetAction`、
  `IxRotateWidgetAction`、`IxFadeWidgetAction`、`IxScrollToWidgetAction`、`IxSetFocusOnWidgetAction`、
  `IxCloseCurrentAction`、`IxWaitAction`、`IxSetAdaptiveViewAction`、`IxEnableDisableWidgetsAction`、
  `IxOtherAction`（通用兜底）等。**"显示/隐藏"和"设置文本"不是独立类**，需要从 `IxOtherAction`
  或 `IxPathInfoMapAction<T>` 家族里找，是下一阶段的活。

### 8.4 层级（z-order）为什么只做了 front/back

`XJ48.mJ4n`（`IList<KJm7>`）看起来方法很多，但：

- `kJnm(index, obj)` / `rJ4l(index, obj)` —— 会把集合搞坏（出现过 `['backShape','backShape']`，
  label 直接消失）；
- `NJ4z(index, index)` —— `ArgumentOutOfRangeException`；
- `Remove(obj)` + `Add(obj)` —— 集合对了，但控件的内部子对象（`TextArea` 的 `TextPanel`）丢失，
  **文字静默消失**；
- 唯一稳妥的是 `SendToBack(obj)`，但它一次只挪一个，且名字和实际方向相反
  （实测"最后加进来的在最上层"）。

所以当前实现只暴露前后，多选时按顺序逐个处理（最后一个生效），文档里已标注。

## 9. 第三阶段：对齐分布、文件打开、列表项与图片

### 9.1 对齐 / 分布（纯几何，无需内部 API）

`KJm7` 的 `X/Y/Width/Height` 读出来做运算，再用 `YJ0S(rect, true)` 写回即可。
页面尺寸从 `package.Diagram.Bounds(true)` 拿（`Bounds` 是带一个 bool 参数的方法，不是属性）。
实现里 `reference="bounds"` 对齐到所选控件的包围盒，`"selection"` 对齐到画板。

### 9.2 在运行中的 Axure 里打开文件

`HNR1.LNRH.Load(string fileName, out string errorMessage, bool fullConvert, Axure.IO.AccessMode mode)`，
`AccessMode` = `Read / Write / ReadWrite`。实测 `Load(path, null, false, "ReadWrite")` 直接返回 `True`
并把当前空文档换成目标文件，比靠启动参数传文件可靠（启动参数在已有实例时会丢）。

### 9.3 列表项：`Options` 是只读集合

`ComboBox`/`ListBox` 的选项是 `List<otfF.Wtfr>`，注意两点：

- `Options` **只有 getter**，`set Options` 会失败，必须 `Clear()` 后逐项 `Add`；
- 每一项是 `new otfF.Wtfr(package, 标签, 是否选中)`，标签还可以用 `xtfJ(object)` / `Dtf5(string)` 补。

实测在"页面上已经存在、且被打开过"的 droplist 上工作正常（读回 3 个选项）；
但**新建的 droplist 上读 `Options` 会偶发抛 `The node must be of type 'JsonValue'`**，
所以这个能力标记为实验性，是下一阶段要排查的第一项（怀疑与 ComboBox 的惰性初始化有关）。

### 9.4 图片：`dta1.QtaH` 从流构造

`ImageBox.Image` 的类型是 `dta1.QtaH`（值类型），构造函数第一个参数是 `System.IO.Stream`：
`new QtaH(stream, name, "png", false)`。桥已经支持把 base64 字符串直接解成 `byte[]` 参数，
所以路径是 `base64 → MemoryStream → QtaH → set Image`。链接子已铺好但**尚未做端到端验证**。

### 9.5 动态面板状态（已定位，**尚未打通**，附踩坑记录）

`DynamicPanel` 暴露：`PanelStates`（`BJzx.LJzK`）、`PanelStateNames` / `PanelStateIds`、
`SubDiagrams`（每个状态一张子图，元素也是 `BJzx.LJzK`）、`CurrentPanelIndex`、`NumberOfPanelStates`。
读取都正常（新建面板默认 1 个 `State 1`，能拿到它的 `DiagramObjects`）。

**写状态是本次唯一没打通的点**，两条路都试过：

| 路线 | 结果 |
| --- | --- |
| `panel.eNaz(guid, index)` | **状态数确实 +1**，但内部抛 `TargetInvocationException`；随后 `PanelStateNames` NRE，`SubDiagrams` 出现 `null` 项 |
| `DynamicPanelStatesEdit.QNaB(panel, text)` + `Commit()` | 编辑对象建出来了、`Commit()` 不报错，但状态数不变（无论 text 传新名字还是"旧名+\n新名"） |

**严重警告**：`eNaz` 那条路会**把整个文档搞坏** —— 之后大部分页面 `TImN` 都抛 `NullReferenceException`，
连 `doc.Load()` 重载都失败，只能重启进程。不要在真实文件上试这条。

已确认的线索，供下一轮继续：

- `DynamicPanelStatesEdit` 有 4 参构造：`(panel, string text, IEnumerable<bbpL<string, LJzK>> states, bool flag)`，
  工厂是 `BNhz(panel)`（会读 `panel.jNaQ()` 里现有的状态文本）。
- 另有 `static oNaV(LJzK state, string newName) -> bS31`（改名）、`static DuplicateState(LJzK, ref int)`（复制）、
  `static lNaG(LJzK[])`（整组重建）。
- 基类 `JS3I.bS31` 是**可撤销命令**：有 `Commit()`、`Combine()`、`DS3M(xS3J.vS3b)`（注入 client state）、
  `fS3e()`（取 client state）。`Commit()` 不生效的原因很可能就是**缺少 client state / 未登记的 outer objects**，
  下一轮应先找到 UI 里把 `bS31` 挂到窗口的那条路径（`DS3M` 在反编译代码里没有调用点，说明另有包装层）。



## 10. 第四阶段：撤销与"可撤销的创建"

### 10.1 撤销命令

`client.KIX4()` 返回菜单命令集（`RHXe.AHXx`），每条命令是 `mHhE.DHhR`，执行入口是 `RHhc()`：

```csharp
var undo = client.KIX4().Undo;   // 同 Ctrl+Z；Redo 同名
undo.RHhc();
```

### 10.2 关键发现：`DiagramObjects.Add()` 不可撤销

直接往集合里 `Add` 的控件**画布可见、能存盘，但 Ctrl+Z 撤不掉**（撤销栈里没有这条记录）。
所以 `add_widget` 改成走编辑器插入管线（也就是从工具箱拖控件的那条路）：

```
temp = new OJa9(diagram.btnD())          // 临时图，归属同一文档
temp.DiagramObjects.cJ4T([widget])
payload = editor.xFUI(temp, temp.DiagramObjects, false, out ids, true, false)
helper.zjzI(payload, false, point, false, false)   // point = new f5wV(x, y)
```

走这条路之后 `Undo.RHhc()` 能把控件撤掉（实测 2 → 3 → 2），撤销栈和变更通知都是白送的。
`add_widget` 里做了降级：拿不到编辑器（没有窗口）时回退到直接 `Add`。

### 10.3 警告：`Redo.RHhc()` 会把进程卡死

实测调用 `Redo` 之后 **Axure 主线程阻塞（`Responding = False`）**，桥不再响应，只能杀进程。
所以 MCP 只暴露了 `axure_undo`，`redo` 留在 ops 里但没做成工具，等排查清楚（很可能是打开了模态框）
再放出来。

## 11. 第五阶段：坐标真相与分组

### 11.1 为什么控件位置会"翻倍"

之前观察到"请求 (80,80)、落盘 (160,160)"，根因不在 `set_rect`，而在插入管线：

**`helper.zjzI(payload, false, point, false, false)` 是把控件"复制"进活图，落点 = `point + 控件自身矩形`。**
所以：

- 传 `point = (x, y)` 且控件自带 `(x, y)` → 位置翻倍；
- 而且 `add_widget` 早先返回的是**草稿图里那个原对象**，不是活图里的副本 —— 读它的 `X/Y` 永远是"对的"，
  但页面上真正的那个控件是偏移的。两边读数不一致，才让这个问题看起来时有时无。

**修法**：`point` 固定传 `(0, 0)`，让控件自己的矩形决定位置；插入后用"身份哈希"比对集合前后差异，
**返回活图里那个副本的句柄**（桥的 handle 每次编码都会变，所以只能用 `identity().id` 做集合差分）。

修完之后 `add_widget` 的位置就是准的：请求 (80,80)/(240,80)/(80,160)，读回完全一致。

### 11.2 分组（Layer）

Axure 的"组"就是 `Axure.DocumentModel.Objects.Layer`，它自己带一个子集合 `_DiagramObjects`。

关键顺序（都踩过）：

1. **Layer 必须走正常的插入管线创建**（`add_widget(kind="group")`）。手工 `new Layer()` 再
   `pageObjects.Add(layer)` 会抛异常，`Add()` 对没有正常初始化的 Layer 不认；
2. 创建时按成员的包围盒给尺寸，`Add()` 之后**要再写一次矩形**，否则位置会被集合重置；
3. 搬成员用 `layer.Add(widget, 0)`，它会自己完成 re-parent（**先 `Remove` 会抛异常**，所以 Remove 只能
   best-effort，失败就跳过）；
4. 成员在 Layer 内**仍然保存绝对坐标**（实测 g1(80,80)、g2(240,80)、g3(80,160) 原样保留），
   不需要做相对坐标换算。

实测结果：3 个按钮 → `mcpGroup`（280×120，位于 (80,80)），子成员 3 个，存盘后读回一致。

### 11.3 解散分组：关键是 `Layer.Remove()`

第一版直接把成员 `Add` 回页面集合，**进程直接挂掉**（连接被拒、要重启）。
真正的原因是**成员还归 Layer 所有**，页面集合接手一个"别人家的孩子"会炸。

正确顺序：

```
layer.Remove(child)     // 先把所有权释放回外层
pageObjects.Add(child)  // 再交给页面
set_rect(child, 原绝对坐标)
pageObjects.Remove(layer)   // 最后删掉 Layer 本体
```

实测（3 个按钮，group → ungroup）：

```
before   : u1(80,80) u2(240,80) u3(80,160)
grouped  : unGroup(80,80, 280x120)，3 个成员在里面
ungrouped: u1(80,80) u2(240,80) u3(80,160)   ← 原样归位
persisted: 重新连一个客户端读回，一致
```

另外两个小坑：Layer 删掉之后它的句柄就废了，**返回结果里的名字必须在删除前先取**；
成员的 `widget_info` 也要在搬运**之前**采集，搬完之后句柄不稳。

## 12. 第六阶段：另存为

`HNR1.LNRH` 只有无参的 `Save()`，没有 `SaveAs`；文件级命令在 `client.KIX4()` 里（202 条），
相关的是 `FileSave` / `FileSaveAs` / `FileExport` / `FileAutoSave` / `ExportToImageDocument` /
`ExportToImageOuterObject`。

**但这些命令对象只有无参的 `RHhc()`**，执行等于替用户点了菜单——会弹模态对话框。
模态框会阻塞 UI 线程，而桥的所有调用都 marshal 到 UI 线程上，所以**一弹框桥就失联**
（`Redo` 就是这么把进程挂住的）。因此 `save_as` 没有走命令，而是：
先 `Save()` 落盘，再把 `.rp` 文件复制到目标路径。

实测：`save_as("test/saved-as.rp")` → 41465 字节；**杀掉进程、重新用该文件启动 Axure**，
页面（Page 1）和三个控件（u1(80,80) / u2(240,80) / u3(80,160)）全部完好，说明产物是独立的合法 `.rp`。

图片导出不需要命令：直接复用 `render_page`（它走的是 Axure 自己的渲染管线），
`export_image()` 就是它的别名。

## 13. 第七阶段：备注注解

### 13.1 页面备注（已打通）

页面备注是 `package.Annotation`（`tJiN.UJiJ`）上的字段。读写的完整链路：

```
ann      = package.Annotation                    // UJiJ
fields   = ann.CJih()                            // H51t.S519，注意它**不是 IEnumerable**
entries  = fields.W51X()                         // 枚举出字段，每项是 k5IJ.R5Ib（Name / PropertyNameId）
value    = ann.gJiI(propertyId)                  // tSmX.rSma 富文本（Text / Paragraphs / Links / Length）
value.Text = "要写的内容"
ann.XJi5(fieldName, value)                       // ← 真正的写入口，写完整条备注就生效
```

- 只写 `Text` 不够，必须通过 `XJi5(字段名, 富文本对象)` 回写，否则 `IsEmpty` 仍是 `true`；
- 默认文档只有一个字段：`Page overview`；
- 读回用 `ann.bJi1(fieldName)`（字符串），或 `gJiI(propertyId)`（富文本）。

**实测**：写入后 `IsEmpty` 变 `false`、`bJi1` 返回原文；**杀进程重启后重新读取，备注仍在**。

### 13.2 控件备注（已打通）

控件备注不是 `KJm7.Footnote`（该属性在具体控件类型上不存在，`set` 直接报类型转换错）。
它是一条挂在**图层注解散列**里的 `UJiJ`，用 `OwnerId` 绑定到控件：

```
col   = diagram.zIuI()                                          // zJis.GJiu
ann   = new UJiJ(package)                                       // 构造出来是"空壳"
ann.vJi6(widgetGuid)                                            // ① 绑定 OwnerId
ann.JJik("DiagramObject")                                       // ② 设定 kind（DJiE 才有值）
fields = model.DiagramObjectAnnotationFieldSet.K5HC()           // ③ 控件字段集，注意不是页面的
rich  = ann.gJiI(field.PropertyNameId)                          // 富文本
rich.Text = 内容
ann.XJi5(field.Name, rich)
col.Add(ann)                                                    // 挂到图层
```

三个"不做就不行"的点：

1. 只 `new UJiJ(package, guid)` 是不够的，`OwnerId` 仍是全零、`DJiE()` 返回 `null`，
   随后 `gJiI()` 直接 `NullReferenceException`。**必须 `vJi6(guid)` + `JJik("DiagramObject")`**；
2. 字段 id 必须取**控件**字段集（`DiagramObjectAnnotationFieldSet`，默认只有一个 `Note` 字段）。
   用页面字段集（`Page overview`）不会报错，但 `gJiI` 会把**页面备注**的内容返回给你，写进去就串了；
3. 覆盖已有备注要先在集合里找到 `OwnerId` 相同的那条，`Remove` 之后再 `Add`，否则会堆出多条。

**实测**：写入后 `IsEmpty=false`；杀进程重启，再从 `diagram.zIuI().QJPN()` 读回，
`owner` 与控件 `Mtn3()` 的 Guid 一致、`kind=DiagramObject`、内容完好。

### 14.1 动态面板状态：`eNaz` 这条路彻底否掉

`panel.eNaz(guid, index)` 看起来很美——`NumberOfPanelStates` 从 1 变 2，`SubDiagrams` 也真的多出
一个合法的 `BJzx.LJzK`。但**这个新状态无法序列化**：紧接着 `commit()` 就失败，
文档随即变成不可保存（之后 `doc.Load` 都失败，只能重启进程）。

所以结论：**eNaz 只能用来观察，不能用来创造**。要增删状态仍然得走 `DynamicPanelStatesEdit`，
而它的基类 `JS3I.bS31` 是可撤销命令，`Commit()` 需要 client state（`DS3M(xS3J.vS3b)`）；在反编译代码里
`DS3M` 没有调用点，说明还有一层包装没找到。下一轮从"面板状态对话框"的 UI 代码入手找那层包装。

### 14.2 交互动作扩展

动作和事件用例一样，都是交互树上的节点（`TNFt` 插入），所以新增动作只是换一个具体类：

| action | 类 | 参数 |
| --- | --- | --- |
| `navigate` | `IxLinkWindowAction(package, targetGuid, LinkType)` | target_page_id / link_type |
| `close` | `IxCloseCurrentAction(package)` | — |
| `wait` | `IxWaitAction(package)` + `WaitTime` 属性 | wait_ms |
| `scroll_to` | `IxScrollToWidgetAction(package, widgetGuid)` | target_widget |
| `focus` | `IxSetFocusOnWidgetAction(package, widgetGuid)` | target_widget |
| `show_hide` | `IxFadeWidgetAction(package, widgetGuid)` + `DefaultFadeAction` | target_widget / fade |
| `set_opacity` | `IxSetOpacityAction(package, Dictionary<string,double>)` + `DIyv` | target_widget / value |
| `move` | `IxMoveWidgetAction(...)` + `DIyv` | target_widget / value |
| `rotate` | `IxRotateWidgetAction(...)` + `DIyv` | target_widget / value |
| `resize` | `IxSetWidgetSizeAction(...)` + `DIyv` | target_widget / value |
| `set_image` | `IxSetImageAction(package, widgetGuid)` | target_widget |
| `apply_style` | `IxApplyStyleAction(package, widgetGuid)` | target_widget |
| `enable_disable` | `IxEnableDisableWidgetsAction(package, Guid, EnableActionType)` | target_widget / mode |
| `link_frame` | `IxLinkFrameAction(package, Guid, LinkType)` | target_widget / link_type |
| `panel_order` | `IxSetPanelOrderAction(package, Guid, bool)` | target_widget / value(1=前,0=后) |
| `expand_collapse` | `IxExpandCollapseTreeAction(package, Guid, bool)` | target_widget / value |

### 14.2d 重载消歧的一招：用**三参数**构造函数

`IxEnableDisableWidgetsAction` / `IxLinkFrameAction` / `IxSetPanelOrderAction` /
`IxExpandCollapseTreeAction` 都有两个 2 参数构造：`(btdo, Guid)` 和 `(btdo, 枚举/bool)`。
桥的重载绑定会撞上后者，报 `Guid cannot be converted to EnableActionType` 之类，
即使传了 `params` 类型提示也救不回来（`BindCtor` 只是最后把异常抛出来）。

**有效办法是绕开歧义**：这些类都有 3 参数版本 `(btdo, Guid, 枚举/bool)`，只有一个候选，
绑定必然成功：

```python
IxEnableDisableWidgetsAction(pkg, guid, "Enable"|"Disable"|"Toggle")   // EnableActionType
IxLinkFrameAction(pkg, guid, "Current"|"New"|...)                     // LinkType
IxSetPanelOrderAction(pkg, guid, True|False)                          // 置前/置后
IxExpandCollapseTreeAction(pkg, guid, True|False)                     // 展开/收起
```

读回 `EditorText` 分别是 `Enable/Disable <a>widget</a>`、`Open <a>Link in frame</a>`、
`Bring <a>widget</a> to front/back`、`Expand/Collapse <a>tree node</a>`。

### 14.2b 数值型动作的通用模式（一次打通四个）

`set_opacity` / `move` / `rotate` / `resize` 的构造函数**都不接受 Guid**，只接受
`(btdo, Dictionary<string, double>)`。真正的模式是：

```python
table = makedict("System.String", "System.Double")        // 空字典即可
action = ctor(cls, [package, table])                      // 先造动作
path   = new sJ8l.dJ8s(package, widgetGuid)               // 再造目标路径
action.DIyv(path, "0.4")                                  // 按路径把数值写进去
```

`DIyv` 会返回规范化后的值（如 `"0.4 "`），`EditorText` 永远是模板文案
（`Set Opacity on <a>widget</a>`），**不要用 EditorText 判断是否生效**，要看存盘重载后动作是否还在。

实测四个动作都能生成并**在保存、重启后原样保留**（`act2.rp` 上验的），
和面板状态动作不同——它们不会毁文件。

### 14.2c 只能选控件的两个动作

`IxSetImageAction`（设图片）和 `IxApplyStyleAction`（应用样式）是简单的
`(btdo, Guid)` 构造，`EditorText` 分别是 `Set <a>image</a>` 和 `Apply style`。

实测读回（`EditorText` 是最直接的验证）：

```
Axure:InteractionCloseCurrentAction        "Close current window"
Axure:InteractionWaitAction                "Wait <a>800 ms</a>"
Axure:InteractionScrollToWidgetAction      "Scroll to <a>Widget</a>"
Axure:InteractionSetFocusOnWidgetAction    "Set focus on <a>Widget</a>"
Axure:InteractionFadeWidgetAction          "Show/hide widget"
```

**重要发现**：菜单里那三条 `InteractionShowPanelAction` / `InteractionHidePanelAction` /
`InteractionTogglePanelVisibilityAction` 是**旧版遗留的 TypeName**（只出现在显示名注册表里），
新版 UI 的"显示/隐藏"就是 `IxFadeWidgetAction`，具体是显示还是隐藏由每条目标的
`FadeActionType = {Hide, Show, Toggle}` 决定（`IxFadeWidgetAction.DefaultFadeAction`）。

控件 Guid 用 `KJm7.Mtn3()` 取（和控件备注里拿到的是同一个 id）。

**剩下的动作分两类**：一类和上面一样是"选个控件/页面"，照着补即可
（`IxMoveWidgetAction`、`IxFadeWidgetAction`、`IxSetOpacityAction`、`IxSetWidgetSizeAction`、
`IxRotateWidgetAction`、`IxLinkFrameAction`、`IxEnableDisableWidgetsAction`…）；
另一类是 `IxPathInfoMapAction<T>` 家族，构造要传 `Dictionary<string, ...Info>`（按控件路径存配置），
需要先摸清对应的 `...Info` 类型，这部分留到后面。

### 14.3 桥的泛型构造能力 + 一个必须知道的重编译坑

**重编译坑（很容易白白浪费几十分钟）**：`tools\axure-hook.ps1` 注入的是
`bridge\bin\Debug\net10.0\AxureBridge.dll`（Debug 配置）。如果只 `dotnet build -c Release`，
注入的还是旧 DLL，改动看起来"完全没生效"（现象是 `unknown op: xxx`）。
另外 `bridge\dist\AxureBridge.dll` 是一份历史副本，`launcher.bridge_path()` 会优先用它，
所以**改完桥必须**：`dotnet build -c Debug` → 确认 `bin\Debug\net10.0\AxureBridge.dll` 的时间戳更新。

**新增 op `makedict`**：手拼闭合泛型的 assembly-qualified name 极易出错（要写全
`System.Private.CoreLib, Version=10.0.0.0, PublicKeyToken=7cec85d7bea7798e` 之类），
所以让桥来做：

```json
{"op": "makedict", "key": "System.String", "value": "..."}   // 返回一个空 Dictionary<TKey,TValue>
```

配合 `invoke(handle, "Add", [key, value])` 就能构造任意 `Dictionary<,>` 参数。

### 14.4 设置面板状态：构造函数要的不是状态，是时长

`IxSetPanelStateAction` 的实际构造函数是
`(btdo, Dictionary<string, double>)` —— 那个字典是**动画时长**（按控件路径），
不是状态信息。另有个 `(btdo, Guid)`，但重载解析会先撞上字典那个，传 Guid 会报
`Guid cannot be converted to Dictionary`。

状态本身不在构造函数里，而是由 `SetPanelStateInfo(btdo package, Guid diagramId)` 描述
（属性：`SetStateType` / `DiagramId` / `StateValue` / `EnterEasing` / `ExitDuration` / `EnterAnchor` …），
所以下一步要找到「action 和 info 之间是怎么关联的」——大概率是 action 上的某个
`Dictionary<string, SetPanelStateInfo>` 属性或 `IxPathInfoMapAction<T>` 的基类成员，而不是构造参数。

面板状态的读取已经验证可用：`panel.SubDiagrams` 枚举出 `BJzx.LJzK`，每项 `Name` + `Mtn3()`
拿到状态 Guid（新建面板默认一个 `State 1`）。

### 14.5 action ↔ info 的关联：`IPathInfoMapAction`

`IxSetPanelStateAction` 的基类实现 `IPathInfoMapAction`，接口只有三个方法，全部围绕"路径对象"：

```
DIyv ( sJ8l.dJ8s path, string value )    // 按路径写
hIvt ( sJ8l.dJ8s path )                  // 按路径读
LIv9 ( sJ8l.dJ8s path )                  // 按路径清
```

`sJ8l.dJ8s` 就是路径，构造有三个：`(btdo, Guid)`、`(btdo, List<Guid>, List<Guid>, Guid)`、无参。
两条前提都已验证可用（`makedict` 能造字典、`dJ8s(package, guid)` 能造路径），但卡在
**`IxSetPanelStateAction` 的构造函数**：`members` 里列了无参构造，实际 `ctor([])` 却报
"no ctor with 0 args"（很可能那个是 `protected`，`Type.GetConstructors()` 只返回 public 时前缀对不上）。
下一轮改用 `GetConstructor(NonPublic|Instance)`（桥里加个开关），或直接拿 UI 里现成的 action 实例做克隆。

### 14.6 图片：已打通（之前是误判）

之前判断"图片设置成功但渲染仍显占位图"是**错的** —— 我把一张**自己渲染出来、本身就含占位图的 PNG**
当图片嵌了回去，看起来自然还是占位图。换成明显不同的图（纯色块 + 白色横条）后立刻正常显示。

正确做法（已验证：画布渲染正确 + 重启后仍在）：

```python
stream = new MemoryStream(bytes)                      // 桥支持把 base64 串自动解成 byte[] 参数
image  = new dta1.QtaH(stream, "logo.png", "png", false)
widget.Image = image                                    // ImageBox.Image setter
```

`dta1.QtaH` 的元数据可直接读回校验：`Length` / `Width` / `Height`。

**唯一限制**：**Axure 只接受第一次 `Image` 赋值**。已经带图的 ImageBox 再赋新值会被静默忽略
（读回还是旧图的 `Length`），要换图必须删掉控件重建。所以 `set_image()` 里做了长度校验，
不一致就抛错，避免"看起来成功其实没换"。

### 14.7 设置面板状态：能造出来，但产物不可加载（暂时封存）

绕过构造函数（无参构造实际不可用）后，这条路终于能走通到"动作生成"：

```python
durations = makedict("System.String", "System.Double")          # 动画时长字典，可以先空着
action = IxSetPanelStateAction(package, durations)              # EditorText: "Set <a>panel to state</a>"
path   = new sJ8l.dJ8s(package, panelGuid)                      # 面板自己的 Guid
action.DIyv(path, "State 1")                                    # 按路径写入状态
```

动作能插进交互树、`TypeName` 读回是 `Axure:InteractionSetPanelStateAction`、`save()` 也不报错。

**但是**：存盘之后再 `Load` 这个 `.rp` 会失败（同一类"产物无法序列化"的问题，和 `eNaz` 一模一样），
文档等于废掉。所以：

- `set_panel_state` 的代码留在 `ops.py` 里，**但没有加进 MCP 工具的动作枚举**，避免误用；
- 说明状态信息可能不是通过 `DIyv` 的一条路径写进去的，而是需要一个完整的
  `SetPanelStateInfo`（含 `SetStateType`），单写状态名会产生半成品结构。

### 14.8 面板状态增删：已打通（用 DuplicateState，不是 eNaz）

前面记的三条死路里，`eNaz` 和 `IxSetPanelStateAction + DIyv` 确实不能用（都会让文档无法序列化），
但**第四条今天打通了**：不新建状态，而是**复制一个已有状态**——

```python
edit = DynamicPanelStatesEdit.DuplicateState(existingState, 0)   // 静态方法，返回可撤销命令
edit.Commit()
```

- `DuplicateState(BJzx.LJzK state, ref int index)` 是**静态方法**，返回 `DynamicPanelStatesEdit`；
- 拿到对象后直接 `Commit()`，状态数立刻 +1，名字自动递增（`State 1` → `State 2`）；
- 改名用同一类型上的静态 `oNaV(state, newName)` 再 `Commit()`。

**实测（完整闭环）**：

```
states before: 1
DuplicateState + Commit -> 2   ['State 1', 'State 2']
save() -> 52763 bytes
杀掉进程、重新打开同一个 .rp -> 2   ['State 1', 'State 2']      ← 持久化成立
```

再叠加改名和批量：

```
axure_add_panel_state(count=2, state_name="第三页")
-> count=3, states=['State 1','State 3','第三页']
重启后依然 3 个状态
```

对应工具：`axure_panel_states`（读）、`axure_add_panel_state`（加 + 可选改名）。

> 注意：每次 `DuplicateState` 都要**重新取一次** `SubDiagrams`，不要复用旧句柄——状态集合在
> Commit 之后会重建。

## 15. 第八阶段：表格单元格

`Table` 是 `DiagramObjectContainer` 的子类，结构比想象中直接：

```
table.Rows   = 2            // setter 会重建 cell 子对象
table.Columns = 3
table.RowHeights / ColumnWidths  （集合属性）
table.AllTableCells         // 按行优先顺序返回全部 TableCell
cell.Text = "内容"           // TableCell 也是 KJm7，Text 直接可写
```

实测：`Rows=3, Columns=3` 之后 `AllTableCells` 正好 9 个（行优先），逐个写 `Text` 全部读回正确，
渲染出来是带边框的 3×3 表格；写入后**重启 Axure，2×3 的表格和 6 个单元格文字都还在**。

对应的工具是 `axure_set_table(name, rows, columns, data=[[...]], row_heights=, column_widths=)`，
`data` 按行优先二维数组给，内部换算成 `index = row * columns + col`。

## 16. 第九阶段：富文本与多段落

### 16.1 两条完全不同的路径

| 控件族 | 载体 | 写法 |
| --- | --- | --- |
| 纯文本（TextBox / TextArea / 大部分 Button） | `PlainTextObject.TextPanel` | 直接 `widget.Text = "行一\n行二"`，`\n` 就是分段 |
| 富文本（`TextRectObject` / `RichTextObject` 派生：Paragraph、H1–H6 等 shape） | `widget.RichTextPanel` | `panel.AttrString.Text = "..."` |

实测：TextArea 里写 `"行一\n行二\n行三"` **确实渲染成三行**（截图 `analysis/richtext.png`）；
Paragraph shape 通过 `RichTextPanel.AttrString` 写入也渲染成多段（`analysis/richtext2.png`）。

### 16.2 一个容易误判的坑

**同样的富文本写法，放在 `Rectangle` 形状上不显示**。因为 `axure_add_widget(kind="shape")` 默认是
`Rectangle`，它不承载文字渲染；要有可见文字，建控件时必须指定会显示文字的形状
（`Paragraph` / `H1`…`H6` / `Placeholder` 等），或者直接用纯文本控件走 `Text`。

`set_rich_text()` 会自动判断：控件有 `RichTextPanel` 就走富文本，否则回退到 `Text` 字符串，
返回里用 `mode` 字段告诉你走了哪条（`rich-text` / `plain-text`）。

**持久化**：两种模式写入后杀进程重启，`RichTextPanel.AttrString.Text` 与 `Text` 都能原样读回。

## 17. 第十阶段：母版/组件（未打通，但有精确结论）

### 17.1 组件不在页面站点树里

`SitemapMapper` 暴露了关键事实：

```csharp
MapSitemap(document)    => MapChildren(document.Sitemap, document.Sitemap.Tree, "page");
MapComponents(document) => MapChildren(document.lJhT(),  document.lJhT().Tree,  "component");
```

**组件有自己的一棵树**：`model.lJhT()`（运行期类型 `IJlg.uJl3`，而页面树是 `cNrY.eNrZ`）。
节点载荷判定顺序是：`kJsl` → folder、`Guid` → page、`hJs0.mJsm` → component。

读侧完全可用：`lJhT().Tree` 的 `Children` 拿到节点，`map.IIw8(node)` 取名字、
`map.xNFB(node)` 取载荷、`mJsm.PackageId` 取包 id。

### 17.2 写侧被序列化器拦下（不是崩溃，是校验）

两条路都试过，`save()` 都被同一句话拒绝：

```
Exception: Package Maps should only contain strings and package ids
```

| 尝试 | 结果 |
| --- | --- |
| `new mJsm(Guid, Guid)` 作载荷 + `TNFt` + `XIwn` | 节点建得出来、名字也设上了，但**存盘报上面那句** |
| 直接拿 `Guid` 当载荷塞进组件树 | 同样报错（说明不是载荷类型的问题） |

所以问题在**更底层**：组件树内部维护的 "package map" 只接受"字符串和包 id"，
也就是说**master 的包必须先登记进文档的包表**，节点才能引用它。手工 new 出来的 mJsm
没有对应的包实体，校验就过不去。

### 17.3 实例这一侧是现成的

`Axure.DocumentModel.Objects.ReferenceDiagramObject` 构造有三个：
`(btdo package, GJvA.IJvQ master)`、`(btdo, Guid)`、无参；属性有
`ReferredMaster` / `ReferredPackageId` / `MasterViews` / `BadMaster` / `IsNullOrBadMaster`。
**只要能拿到一个真实存在的 master 包**，插入实例就是一行。

### 17.4 注意：失败的组件节点会"毒化"内存里的文档

手工塞进去的那个节点**不会被内存丢弃**。一旦它存在，之后**任何** `save()` 都会报
`Package Maps should only contain strings and package ids`，连保存普通页面都不行。
只有**重启 Axure**（文件本身没被写坏，因为保存一直失败）才能恢复。

所以试组件树的时候一定要在一次性文件上做，并且做完立刻重启。

### 17.5 下轮方向

顺着"包表"找：文档模型 `HtZf.dtZ8` / 包缓存 `eIX8` / `qtcD.Otc6` 附近应该有
「新建包并登记」的入口（页面创建时 `TNFt` 传 Guid 能成功，说明页面那条路会自动建包；
组件树走的不是同一条路）。找到它，母版就通了。

### 18.1 母版：三条路都被同一句校验拦下（结论：需要文档模型配合，暂缓）

`SitemapMapper` 说明组件有自己的树 `document.lJhT()`，但**写侧怎么试都过不去**：

| 尝试 | 位置 | 结果 |
| --- | --- | --- |
| `mJsm(Guid,Guid)` 载荷 | 组件树 `lJhT()` | 节点可建、可命名，`save()` 报 `Package Maps should only contain strings and package ids` |
| `Guid` 作载荷 | 组件树 | 同样报错 |
| `mJsm(Guid,Guid)` 载荷 | **页面**站点树 | 同样报错（mapper 在页面树里把 mJsm 也当 page 显示，但序列化器不认） |

结论：`mJsm` 内部维护的"package map"要求里面只有字符串和**已登记的包 id**，
手工 `new` 出来的对象没有对应的包实体，任何一棵树都过不了校验。
而且**失败的节点会污染内存文档**（此后普通 `save()` 也失败，必须重启进程）。

要做成，得先找到文档模型（`HtZf.dtZ8` / 包缓存 `eIX8` / `qtcD.Otc6`）里"新建包并登记"的入口，
让 master 的包先存在。3 轮尝试后我把它标为**暂缓**——它是唯一需要动文档包表的功能，
风险收益比最差，而 `ReferenceDiagramObject` 的实例侧本来是现成的（`(btdo, Guid)` 一行）。

### 18.2 顺带补上的布局能力：等宽等高

`same_size(names, mode="width|height|both")`：以第一个控件为基准，把其余控件拉成同样的宽/高。
纯几何操作（读 `X/Y/Width/Height` → `YJ0S` 写回），实测 `m2(200×60)`、`m3(90×50)` 都被拉成
`m1` 的 `120×40`，渲染正确。

### 18.3 列表项：已转正（之前是旧桥导致的误判）

之前记录"新建控件上读 `Options` 会抛 `The node must be of type 'JsonValue'`"——**那是旧桥构建的锅**
（当时注入的 DLL 还是 Debug 旧版，见 14.3 的重编译坑）。换到正确的桥之后，新建 droplist 完全正常。

真正的结构在基类 `ListObject` 上：

```
Options   : List<otfF.Wtfr>     // get_Options + set_Options 都有（属性面板里看不到 setter）
Multiple  : bool
SelectedObjects : IEnumerable<otfF.Wtfr>
```

**最干净的写法是整表替换**，而不是清空原表再填：

```python
lst = new List<otfF.Wtfr>()
for i, label in enumerate(labels):
    lst.Add(new otfF.Wtfr(package, label, i == selected))
widget.Options = lst          // 走 set_Options
```

读回用 `Options` → 逐项 `ltft()` 取标签。实测：4 个选项、选中第 3 项，
**重启后选项与选中项都在**，渲染里下拉框显示的就是选中项。

## 18.4 重要：交互动作的「存盘重载」审计（推翻了之前的结论）

之前我按 `EditorText` 判定"16 个动作都可用"——**这是错的**。`EditorText` 只能证明对象构造成功，
证明不了它能序列化。做了严格审计（一次性文件放 14 个动作 → save → 杀进程 → 重开）：

| 动作 | 重载后 |
| --- | --- |
| navigate / close / wait | ✅ 存活 |
| set_opacity / move / rotate / resize | ✅ 存活 |
| scroll_to / focus / set_image / apply_style / enable_disable / link_frame / panel_order / expand_collapse | ❌ **动作节点被静默丢弃**（事件和用例还在，里面的动作没了） |
| show_hide（`IxFadeWidgetAction`） | ❌ **更糟**：重载抛 `InvalidCastException: Unable to cast IxFadeWidgetAction to KJm7`，整个文档读不出来 |
| set_panel_state | ❌ 存盘后文件无法加载（早前已封存） |

所以现在**只开放 7 个动作**：`navigate` / `close` / `wait` / `set_opacity` / `move` / `rotate` / `resize`。
其余在 `ops.UNSAFE_ACTIONS` 里登记了原因，调用直接抛错，避免"看起来成功、动作却丢了"。

**教训**：判断能力是否可用，必须走 **保存 → 重启 → 重新读取** 这条链。
`EditorText`、内存对象、甚至 `save()` 不报错，都不能当证据。

## 21. 全量回归（本轮）与它抓出的问题

`tools/regression_ascii.py`：40+ 个写操作按顺序跑 → `save` → **杀掉进程** → 重开同一文件 →
重新读取并断言。结果写进 `docs/VERIFICATION.md`。

**已修复：`ungroup` 会杀死进程**
旧实现手工 re-parent（`layer.Remove` + `pageObjects.Add`），在复杂页面上会把进程带崩。
改成**走编辑器管线把成员复制回页面，再删 Layer**：

```
for child in layer.DiagramObjects:
    clone = insert_via_editor(child)      # zjzI 复制，落点用 child 自己的矩形
    set_rect(clone, 原绝对坐标)
    rename(clone, 原名)
page.remove(layer)
```

实测：3 个按钮 → 分组 → 解散 → 三个按钮回到 (80,80)/(240,80)/(80,160)，
`save` 正常，**重启后仍在原位**。回归里 41 步全绿。

**未修复：页面备注与控件备注会互相串**
`set_widget_note` 用 `(package, widgetGuid)` 构造 + `vJi6` 后，owner 正确、页面备注在内存里也对，
但**存盘重启后** `page.Annotation.bJi1("Page overview")` 又会返回控件备注的文本。
根因是 `tJiN.UJiJ` 的内部存储在不同实例间共享，save/load 时被合并。
**当前处理**：`set_widget_note` 现在会先检查该页是否已有页面备注，有就直接报错拒绝
（而不是悄悄覆盖）。错误信息里写明"一个页面只能用一种备注"。
实测：页面写了 `guard-test` 之后再写控件备注 → 请求被拒，页面备注完好。
放长看应该给控件备注换一个不共享的容器，这是已知待办。



`tools/regression_ascii.py`：把 40+ 个写操作按顺序跑一遍 → `save` → **杀掉进程** → 重开同一文件 →
重新读取并断言。结果写进 `docs/VERIFICATION.md`。

**抓出的问题一：`ungroup` 会杀死进程**
回归跑到 `ungroup` 时桥立刻失联（`ConnectionRefusedError`），它之后的所有步骤全废。
之前我没发现，是因为当时只在「3 个按钮、刚分组」的极简页面上试过。
现在标记为**不稳定**，回归里已移除该步（`axure_ungroup` 工具仍在，但要当心）。

**抓出的问题二：控件备注会污染页面备注**
`set_widget_note` 原先用 `new UJiJ(package)` 单参构造——这个对象的存储和页面注解**共享**，
写完控件备注后 `package.Annotation.bJi1("Page overview")` 会返回控件备注的文本。
改用 `new UJiJ(package, widgetGuid)` 双参构造（再补 `vJi6(guid)` 设 OwnerId）后两者就分开了：
实测页面备注保持 `page-clean`，控件备注 owner 正确。


### 21.3 另外两个在回归里暴露的问题

1. **给「已被移动进文件夹的页面」写控件会挂起**：`add_btn_beta` 那一步直接 `TimeoutError`，
   之后所有调用都 `ConnectionRefusedError`。推测是 `move_page` 之后该页面的包缓存状态不一致，
   写操作触发重建时死锁。**建议：移动页面后先重新打开（或重启）再写入。**
2. **控件备注的持久化在两种构造之间摇摆**：`(package)` 构造能持久化但会覆盖页面备注；
   `(package, guid)` 构造能保住页面备注但存盘后读不回来。当前选择"能持久化 + 护栏互斥"，
   并且把矩阵里的状态降级为 ⚠️，不再声称完全可用。

### 21.4 本轮修复与一个失败实验

**修复：移动页面后写入会挂起**
`move_page` 之后窗口仍指向移动前的包，紧接着写控件会 `TimeoutError` 并拖死整个进程。
现在 `move_page` 末尾会重新 `TImN` 打开该页面刷新缓存。实测：建文件夹 → 建页 → 移入 →
写控件 → 存盘，全部正常。

**失败实验：不要在 save 里遍历所有页面**
猜测"只提交当前包会丢多页编辑"，于是改成 save 时逐页 `TImN` + `commit`。
结果**文档被写坏**：保存后除了 Page 1 其余页面全没了。已回滚。
教训：Axure 每页一个 live package，逐页开关会破坏文档结构；正确做法是**每个工具在处理完自己那页后
就提交自己那个包**（现有 handler 已经是这么做的，`app.save(package)` 都传了 package）。

**最终回归（回滚后）12/13 通过**，唯一失败项是控件备注的持久化（见矩阵）。

### 21.5 本轮：嵌套结构可读、分组内控件可编辑

**`page_tree()` 现在返回真正的嵌套树**
只读服务的 DTO 把 `Children` 当不透明句柄返回，没法做断言。改成自己从站点模型重建：
`map.IIw8(node)` 取名字、`map.xNFB(node)` 取载荷，载荷是 `Guid` → page、`kJsl` → folder（带
`FolderId`）、`mJsm` → component。键名沿用 DTO 的 `Id/Name/Type/Children` 以免破坏调用方。

**分组内的控件可以被找到和编辑**
`find_widget` 先扫顶层，找不到再**递归进 `DiagramObjects`**（最多 5 层）。
实测：三个按钮分组后，按名字仍能对成员 `n2` 套样式（`Fill=#2F6FED`）、加交互（`WaitAction`），
**存盘重启后都还在**。

注意：`widgets(deep=True)` 里的深遍历是**按需开启**的——默认不看子对象，
因为遍历表格单元格/面板状态这些容器的 `DiagramObjects` 又慢又容易让进程不稳。

**仍在的抖动：页面树操作**
`move_page` / `delete_page` 连续操作时偶发进程崩溃（`ConnectionRefused` / `ConnectionReset`）。
`move_page` 末尾会自动重开目标页（这修好了"移页后写控件挂起"），但紧接着删页面仍可能炸。
建议：**移动/删除页面之后先保存一次，再做别的页操作**。

### 21.6 关键修复：`move_page` 之后不要马上调用只读服务

回归里 `delete_page` 一直 `ConnectionRefusedError`，隔离测试却是好的。差别只有一个：
回归是 `move_page` 紧接着 `delete_page`，而 `delete_page` 原来第一件事就是调
`GetSitemap`（只读服务的 DTO）来数页面。**`move_page` 之后立刻调这个服务会让进程崩掉。**

改法：`delete_page` 改从站点模型自己数

```
root.Children.Count <= 1  ->  拒绝删除（保护最后一页）
```

顺带加了两道护栏（曾经出现过"一次删掉整组页面"）：
- 节点载荷必须和请求的 id 对得上（页面载荷是 Guid，文件夹是 `kJsl`）
- 永远不允许删掉最后一页

改完回归 **12/13 通过**，唯一失败项仍是控件备注持久化。


### 21.7 控件备注的最终结论（三种插入方式都试过）

在一次性文件上分别用三种方式把控件注解挂进 `diagram.zIuI()`，然后存盘 + 重启：

| 插入方式 | 写入 | 重启后 |
| --- | --- | --- |
| `Add(UJiJ)` | 成功 | 读不回来 |
| `iJPr(UJiJ)` | 抛 `Annotation not in tree map` | — |
| `nJPV(UJiJ)` | 成功 | 读不回来 |

三种方式下**页面备注都保持干净**（用 `(package, guid)` 构造），说明污染问题已解决，
但控件备注本身**无法通过序列化**——面板里那条注解在存盘后不会被写进 `.rp`。

结论：**控件备注按不可用处理**，需要备注就写页面级。已同步到 README 的能力矩阵与
`docs/VERIFICATION.md`。

## 22. MCP 协议层的优化

工具本身能跑通之后，把 MCP 这一层也按规范补齐了（这些都不影响 Axure 侧的行为，只影响可用性）：

| 项 | 之前 | 现在 |
| --- | --- | --- |
| 连接 | 每次工具调用新建 `Axure()`（重新发现端口 + 建 socket） | 会话内复用；5 次调用 0.01 秒 |
| 参数 | 直接调 handler，错误参数抛 `TypeError` | 按 `inputSchema` 预检：必填/未知字段/枚举/类型 |
| 断线 | 桥挂了之后所有调用一直失败 | 自动重新拉起 Axure 并重连（可关） |
| 错误 | 只有异常名和信息 | 追加可操作提示（启动脚本、超时含义等） |
| 通知 | `cancelled` 等未处理 | 正确忽略；带 id 时也回一次，避免对端阻塞 |

一个实现细节：校验要读 `inputSchema` 里的 `properties/required/additionalProperties`，
不是工具描述对象的顶层字段——第一版读错了层级，导致校验形同虚设，`axure_list_pages` 传
`{"bogus":1}` 都能过。

### 22.1 第二轮优化：重连速度、并发锁，以及一个藏在 `bridge_path()` 里的坑

**重连 56 秒 → 11.7 秒**
`launcher.launch()` 每轮询一次就调 `bridge_alive()`，而它会把 41999..42019 二十个端口
**每个用 0.6 秒超时**试一遍——Axure 冷启动那十几秒里，轮询本身就在烧时间。
改成：只探"请求的端口 + 紧邻的 4 个"，单端口超时 0.2 秒，轮询间隔 0.4 秒；全端口扫描留作兜底。

**并发锁**
两个 MCP 会话连同一个 Axure 时，各自调用都会 `TImN` 把自己的页面切到窗口上，
于是 A 切页、B 写入 → **写到 A 的页面上**。加 `axure_mcp/lock.py`：Windows 用 `msvcrt.locking`
对 `%LOCALAPPDATA%\Axure\axure-mcp.lock` 加文件锁（跨进程），工具调用全程持有。
同进程内可重入（Windows 锁是按句柄的，嵌套再锁会失败）。
实测 3 个进程 × 8 次调用并发：24 次全成功，0.30 秒。

**坑：`bridge_path()` 会注入旧构建**
它优先返回 `bridge/dist/AxureBridge.dll`，但那是个旧副本；重连时注入旧桥，
`HNR1.LNRH.Load` 直接报 `method ... not found`（因为新版桥才加了静态方法调用约定）。
改成**按 mtime 选最新的那个**（dist 与 bin/Debug 比时间），环境变量仍可覆盖。

**顺带发现：不要在刚拉起的窗口里立刻 `open_file`**
重连后马上调 `open_file` 去恢复文档，会卡到超时（实测 190 秒），因为新窗口还在初始化。
现在只在 `axure_status` 里回 `remembered_file`，让调用方自己决定何时打开。

## 23. `iI3A.TI3Q` 是什么，以及为什么它能让桥失联

用户报的错：`Exception of type 'iI3A.TI3Q' was thrown.`

反编译里查清楚了：

```csharp
namespace iI3A;
public class TI3Q : Exception {
    public aI3N Window { get; set; }   // 出错的那个窗口
    private bool VIgF;                 // Closed 标志 (KIgG/yI3o)
}
```

构造点只有一处：`NI3S.aI3N.MI35(Exception P_0, aI3N P_1)`——**窗口级未处理异常处理器**。
任何从 UI 线程逃出去、没被业务代码 catch 的异常，都会在这里被包成 `TI3Q` 挂上窗口，然后弹框。
入口 `wb5l.ib5s`（`[STAThread]`）里还有个空的 `tb5v(TI3Q)`。`JI3v.zI3y` 是它的委托签名。

**所以这句话本身没有信息量，真正的原因在 `InnerException`。** 危害是：弹框会阻塞 UI 线程，
桥的所有调用跟着超时，整个会话看起来像死机。

### 三个改动

1. **错误递归解包**（`Bridge.Describe`）：`TargetInvocationException` 会套好几层，
   `TI3Q` 也是一层壳。以前只解一层，用户看到的就是那句废话；现在一直挖到真正的异常。
   实测同一个操作：改前 `TargetInvocationException: Exception has been thrown by the target of
   an invocation.`，改后 `ApplicationException: Object synchronization method was called from an
   unsynchronized block of code.`（Axure 的 `Load` 要求调用方持有它的锁）。

2. **弹框自动读出并关闭**（`bridge/Dialogs.cs`）：`EnumWindows` 找本进程的模态窗口
   （`#32770` 或带按钮的 WPF 错误窗），读标题和各子控件文字，然后 `WM_CLOSE`。
   桥新增 `dialogs` / `dismiss` 两个 op；Python 侧在**调用超时时**自动用一条新连接去收弹框，
   把弹框文字作为错误信息抛出来，而不是干等超时。

3. **WPF 分发器的全局钩子装不上**：`System.Windows.Application.Current` 在 Axure 里始终为
   null（它用自己的消息循环，不是标准 WPF Application），所以挂 `Dispatcher.UnhandledException`
   这条路走不通。桥里保留了探测代码但只记一行日志，真正的兜底是第 2 条的"读出来再关掉"。

### 排查建议

下次再看到那个框：**先看它的详细信息/InnerException**。如果框里有"复制"/"Details"，
把内容发我；没有的话，把出错前一步调的工具名告我，桥的日志
（`%TEMP%\axure-bridge.log`）里 `GUARD swallowed:` 开头的行也会带出真实异常。
