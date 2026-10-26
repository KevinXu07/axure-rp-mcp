# 端到端示例：用 MCP 从零做一个多页原型

脚本：`tools/demo_v2.py`（每次都用 `test/clean.rp` 重新开始，可反复重跑）。
全部操作通过 MCP 工具调用完成，**重启 Axure 后逐项复查仍然成立**。

![生成结果](analysis/demo2-after-reload.png)

## 一、站点结构

```
业务模块/          ← axure_create_folder
首页               ← 主页面
详情页             ← 由首页按钮跳转
```

## 二、首页内容（28 步，0 失败）

| 元素 | 控件类型 | 关键调用 |
| --- | --- | --- |
| 产品中心 | textbox | `axure_add_widget` + `axure_style_widget`（26px 加粗） |
| 卡片底板 | shape | 圆角 12 / 填充 #F8FAFC / 边框 #E2E8F0 |
| 配图 | image | `axure_set_image`（base64 内嵌 PNG） |
| 智能工作台 | textbox | 16px 加粗 |
| 筛选下拉 | droplist | `axure_set_list_items`（全部/进行中/已完成） |
| 查看详情 | button | 填充 #2F6FED / 白字 / 圆角 8 |
| 统计表 | table | `axure_set_table`（2×3，行优先二维数组） |
| 标签一/二/三 | button ×3 | `axure_same_size` 统一尺寸 → `axure_group` 分组 → `axure_ungroup` 解散 |
| 动态面板 | dynamicpanel | `axure_add_panel_state` 新增「第二页」状态 |

交互与备注：

```json
{"name":"axure_add_interaction","arguments":{
   "name":"goDetail","event":"OnClick","action":"navigate","target_page_id":"<详情页 id>"}}
{"name":"axure_set_page_note","arguments":{"text":"首页说明：演示原型，由 MCP 生成。"}}
```

## 三、重启后复查（脚本第 4 段）

```
tree            : 详情页 / 首页 / 业务模块 / Page 1          ← 文件夹与页面关系还在
widgets         : 11 个全部在（title card hero cardTitle filter goDetail stats tabs t3 t2 t1）
panel states    : ['State 1', '第二页']                      ← 动态面板状态保留
interactions    : Axure:InteractionLinkWindowAction          ← 跳转动作存活
page note       : 首页说明：演示原型，由 MCP 生成。
render          : 重启前后两张 PNG 字节数一致（23055 B）
```

## 四、已知问题与规避

- **标题用 `textbox`，不要用 `textarea`**：`TextArea` 在「加粗 + 自定义字体」组合下，
  快照渲染器偶尔不画文字（数据在、画布正常）。会显示文字的形状（Paragraph/H1…）也可以。
- **控件备注存盘后会丢**：需要备注请写在**页面级**（`axure_set_page_note`）。
  页面备注和控件备注还共用同一存储，所以两者在同一页不能同时用。
- **移动页面之后要重新打开**：`axure_move_page` 已经在实现里自动重开目标页，否则紧接着写控件会挂起。
- 交互动作只有 7 个可持久化（navigate/close/wait/set_opacity/move/rotate/resize），
  其余会在存盘时被 Axure 丢弃，已在代码里禁用。

## 五、复现

```powershell
cd <仓库路径>
python tools\demo_v2.py
```

脚本流程：停实例 → 复制干净文件 → 启动注入桥的 Axure → 建页面 → 建控件与样式 → 图片/表格/下拉 →
面板状态 → 等宽+分组+解散 → 交互与备注 → 渲染 → 杀进程重启 → 逐项复查。
