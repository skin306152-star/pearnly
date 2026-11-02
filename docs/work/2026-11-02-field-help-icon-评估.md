# 需求影响评估：字段说明收进标题旁的注释图标（ERP / Cowork）

基线：`df57672aa`（= main = 生产部署中的版本）；分支 `pearnly/field-help-icon-fcafe3`；Frappe `16.33.0`（样板容器 `frappe/__init__.py` 实读）。
状态：主会话 11-02 审定通过（图标用 `info`；B 类留在下方并登记豁免、只减不增；真机走三星 adb 反连本机实验栈，测试机不动；MultiCheck 照现状），已施工，验证记录见文末「施工与验证」。

## 0. 需求原文与理解

- 用户原话（11-02，经主会话转达）：「这类字段下方的文案，放在标题旁边，放个注释图标，鼠标放上去才显示文案，cowork 和 erp 全部有这样的地方，全部按这个做。dms 和后台管理不用」「就用原生自带的吧……覆盖完整，不用漏做」。
- 理解：ERP、Cowork 站点里所有 Frappe 字段的 description（字段下方灰色小字）不再显示在下方；标题旁出一个图标，电脑鼠标移上去 / 键盘聚焦显示，触屏点一下显示、再点或点别处收起，读屏能读到。
- 不在范围：DMS、控制面（管理后台）、Keycloak 登录页、CC 对话页（`frontend/assistant`）、LINE 票据编辑器；页面手写的结果 / 进度小字（不是字段说明，见 §3 C 类）；文案一个字不改。

## 1. 现有同类与复用

- 字段说明全部经框架 `ControlInput.set_description()` 写进控件自己的 `.help-box`（`frappe/form/controls/base_input.js` 235–268）；`ControlCheck` 有自己的 `.help-box`，Time / Datetime 在说明后追加时区。
- 仓库里没有现成的「说明图标」组件。共用层 `pearnly_core/public/desk/product.js` 已有对 `ControlData` 的统一扩展（产品站点单行文本换成会换行的 textarea），本次同一层再加一处，不逐页改。
- 结论：在共用外壳扩展框架控件，一处覆盖所有 `p.layout` / `p.dialog` / `frappe.ui.Dialog` / `FieldGroup` / DocType 表单 / 表格行展开表单。

## 2. Frappe 原生能力（核实结果）

Frappe 16.33.0 **自带同类能力**：DocField 属性 `show_description_on_click`（`core/doctype/docfield/docfield.json`），控件见 `frappe/form/info_card.js`：
标题旁加 `info` 图标（xs），**只有点击**弹出 `frappe.ui.Card` 卡片（带关闭 ×，popper 定位），点别处收起。

直接打开它不能满足口径，原因（均实读源码）：
1. 只能点击，没有鼠标悬停和键盘聚焦；图标是 `<svg role=button>`，没有 `tabindex`，键盘到不了，读屏读不到说明。
2. 卡片内容只在建控件那一刻取一次；之后 `set_df_property(…, "description", …)` 直接被 `set_description()` 第一行 `return` 掉——动态说明会**整句消失**（§3 B 类会坏）。
3. 给标题区加 `white-space: nowrap`：泰文长标题在手机宽会撑出横向滚动，与 11-01 表单排版闸门冲突。
4. 每个控件往 `document` 挂一个永不解绑的点击监听（弹窗开关多次后累积）。
5. 不覆盖 Button（按钮控件把标题区整个隐藏）。

所以按主会话口径用官方另一组件：**图标** `frappe.utils.icon("help", "sm")`（timeless 图标集 `icon-help`，样板 `public/icons/timeless/icons.svg` 已核有 `id="icon-help"`）；**提示** Bootstrap 4 tooltip（Desk 自带，框架页头 `ui/page.js` 176/244/740 行同样用 `.tooltip({…})`），颜色字号全用官方样式，不加主题色。
图标放进框架模板里**本来就留在标题旁的 `<span class="help">` 插槽**（`base_input.js` 第 26 行；Check 是 `<span class="ml-1 help">`），`set_label()` 重写标题时不会冲掉它。

> 要主会话定的第 1 点：图标用 `help`（主会话口径）还是框架那条原生路径用的 `info`。我建议 `help`（问号更像「说明」，`info` 在 Desk 里也当状态提示用）。

## 3. 盘点清单（静态：js / py / json 全扫；运行时扫描见 §11 第 1 步）

扫描方法：`apps/pearnly_erp_portal`、`apps/pearnly_cowork`、`apps/pearnly_core`（两站点都加载的共用页）里 JS 的 `description:`、`set_df_property(…"description"…)` / `set_description(` / `set_new_description(`、所有 DocType / Custom Field / Property Setter JSON 的 description；逐条看上下文去掉非字段说明（POS 快捷键说明、明细行的 `description` 数据列、空列表 `meta.description`、智能体工具说明等）。

### A 类：静态说明 → 改成图标（共 13 处字段 + 框架自带）

| # | 站点 | 文件:行 | 字段 / 弹窗 | 说明（中文） |
| --- | --- | --- | --- | --- |
| A1 | ERP | `erp_portal/public/desk/products.js:109` | 物料新建 / 详情 · 编码（只读） | 自动生成 |
| A2 | ERP | `products.js:121` | 物料 · 库存单位 | 库存按此单位计量；留空则与销售单位相同 |
| A3 | ERP | `products.js:131` | 物料 · 默认仓位 | 仅作为新单据的默认值，各仓位库存单独计算。 |
| A4 | ERP | `products.js:508` | 物料批量录入表格 · 编码列（行展开表单里显示；表格列头框架本来不显示说明） | 自动生成 |
| A5 | ERP | `stock-entry.js:61` | 入库 / 出库 / 调拨 · 单号（只读，新单时） | 自动生成 |
| A6 | ERP | `stocktake-entry.js:18` | 盘点录入 · 扫码格 | 扫码后填写实际仓位和数量，保存后进入下一项。 |
| A7 | Cowork | `cowork/public/desk/stocktake-entry.js:22` | 同上（Cowork 盘点） | 同上 |
| A8 | ERP | `pos/pearnly-pos-shift.js:98` | 开班 · 收银台 | 收银台所属公司及出货仓库 |
| A9 | ERP | `transactions.js:145` | 推送 Express 的「对应项」弹窗 · 每个下拉（说明是该项在 Express 里的名称） | 运行时取值（`item.label`），建控件时就定 |
| A10 | Cowork | `targets.js:134` | 连接 CC 桌面版弹窗 · 「下载」按钮 | 安装后用本账号登录即可连接。 |
| A11 | Cowork | `recon.js:259`（规格 `recon.js:65`） | 银行对账 · 左侧文件（只读） | 网银导出的 Excel/CSV 或电子版 PDF |
| A12 | Cowork | `intake-folders.js:204` | 文件夹定时任务 · 文件夹（初始说明；之后换成文件数，见 B6） | 每个文件夹对应一个定时任务，仅支持 PDF 和图片文件。 |
| A13 | ERP | `team.js:171` | 团队 · 可见公司（MultiCheck） | 不勾选则可查看全部公司——**框架 MultiCheck 本来就不显示 description（`multicheck.js` 无此逻辑），现在界面上看不到**；照现状不出图标，否则等于新加一句可见文案 |
| A* | 两站点 | 框架 | 个人页（User 表单，`user` 路由）里框架字段的说明；Time / Datetime 用户时区与系统时区不同时追加的时区行；收银台明细面板按 DocType 元数据画的字段；产品可进的查询报表筛选 | 框架原文，走原生 `__()` |

DocType JSON 里 ERP 30 条、Cowork 12 条、共用 15 条 description：产品站点只开放一个框架路由（`query-report/ERP Stock Count Variance`），这些 DocType 表单在 ERP / Cowork 界面里打不开，**界面不可见**；共用层统一处理后若以后开放也自动是图标。运行时扫描会核实这一点。

### B 类：运行时写的状态 / 数值（我建议**仍留在字段下方**，等主会话定）

| # | 文件:行 | 字段 | 内容 | 为什么建议不收 |
| --- | --- | --- | --- | --- |
| B1 | ERP `transactions.js:450 / 531` | 采购 / 销售明细 · 物料 | 选中物料后的「分类 · 特性」 | 11-01 岳父要求「同名不同色 / 尺寸一眼分得开」，收进图标就看不见了 |
| B2 | ERP `sales-form.js:253` | 收款弹窗 · 金额 | 「应收合计 ฿x」 | 填金额时对照用的数 |
| B3 | ERP `purchase-form.js:285` | 付款弹窗 · 金额 | 「未付金额 ฿x」 | 同上 |
| B4 | Cowork `intake-choices.js:145–148` / `intake-folders.js:474–477` | 新建录入 / 定时任务 · 账套 | 「CC 桌面版需更新」 | 状态警告 |
| B5 | Cowork `intake-choices.js:172` / `intake-folders.js:441` | 公司 / 年度 | 「正在从 X 读取…」、读取失败原因、「没有可选的公司和年度」 | 加载状态与错误 |
| B6 | Cowork `intake-folders.js:396` | 文件夹 | 「N 份待处理」等文件数 | 实时数 |
| B7 | Cowork `intake-folders.js:422` | 默认科目 | 科目表读取中 / 未读到 | 加载状态 |
| B8 | 框架 | 密码格强度提示、Select 附件「请先上传附件」、命名格「已存在」 | 框架校验反馈 | 密码强度不走 `set_description`，天然不受影响；后两项产品界面里没有 |

做法：B 类字段在定义上加一个自有属性 `description_inline: 1`，共用层见到就照原生显示在下方；每一处登记进闸门豁免表（写原因，基线只减不增）。
> 要主会话定的第 2 点：B1–B7 是留在下方（我的建议），还是也收进图标（用户原话「全部按这个做」）。

### C 类：页面手写的小字（不是字段说明，不动）

盘点录入里「已在别处盘过 / 已录入」（`stocktake-entry.js:120/132`，两站点）、序列号进度（ERP `stocktake-entry.js:211`）、对账结果计数（`recon.js:233/915`）、定时任务运行结果（`intake-folders.js:538/540`）、任务看板评论时间（`work.js`）——都是结果 / 进度，不是 description，不在本次范围。

## 4. 上下游数据

不涉及：纯前端展示，数据、接口、Express 映射、CC 桌面版载荷都不变。

## 5. 隔离与权限

不涉及：不改服务端与权限；只读控件、隐藏字段照旧。

## 6. 入口：网页、手机、LINE、拍照

- 电脑（`pointer: fine`）：鼠标移上去、键盘 Tab 聚焦图标显示；移开 / 失焦 / Esc 收起。
- 触屏（`pointer: coarse`，含 LINE 内置浏览器打开的网页端）：点图标显示，再点或点别处收起；全页只挂一个点别处收起的监听。
- 读屏：图标 `role="button" tabindex="0" aria-label="<说明纯文本>"`；tooltip 打开时 Bootstrap 自带 `aria-describedby`。
- Check：图标在 `<label>` 里，点图标 `preventDefault`，不顺带勾选。Button：标题区被框架隐藏，图标放在按钮右侧。
- 键盘遮挡统一处理（`core/public/desk/keyboard.js`）：图标不是输入格，不触发它；tooltip 挂在 `body`，弹窗滚动时收起，不跟键盘处理抢滚动。
- 拍照 / 相机：不涉及。

## 7. 共用件与连带回归

- 改动文件：新增 `pearnly_core/public/desk/field-help.js`（文件头写「多产品共用」，`hooks.app_include_js` 放在 `product.js` 之后）、`product.css` 加几行图标样式（只在 `.pearnly-product-desk` 下）、`desk/navigation.py` 把产品声明带进启动信息、ERP / Cowork `hooks.py` 的 `pearnly_navigation` 各加一项声明 `field_help: "icon"`（ADR-009：产品自己声明，DMS 不声明就照原生）。
- 站点核实：控制面不装 `pearnly_core`（`keyboard.js` 文件头已登记、`apps/pearnly_control/hooks.py` 无此 App 的资源）→ 挂不上；DMS 装 `pearnly_core`，靠「没声明 → 不启用」排除，闸门同时检查 DMS `hooks.py` 没有这项声明。
- `check-impact.py`（product.js / product.css / hooks.py / navigation.py）：命中 `desk-shell` 51 个入口、`upload-box` 15 个、`desk-navigation` 2 个；新文件 `field-help.js` 补进 `scripts/impact/shared-parts.toml` 的 `desk-shell`。逐入口结果在施工后补进本记录。

## 8. 存储与成本

不涉及：无数据、无外部调用、无月费。

## 9. 删除、作废、追溯

不涉及。

## 10. 文案与官方 UI

- 文案一个字不改，只换位置；仍走 `__(df.description, null, df.parent)`（同框架原句），四语照旧。`check-copy.py` 照跑，预期无新增 / 改动条目。
- 组件：官方 `help` 图标 + Bootstrap tooltip；样式只设图标颜色（`--text-muted`）、光标与和标题的间距，不加主题色。

## 11. 测试与验收

1. **运行时扫描（施工第一步，改前改后各一次）**：在自己的实验栈里给共用层加一个只在扫描时开启的记录（每个带 description 的控件记下 站点 / 路由 / 弹窗标题 / 字段名 / 说明），跑 `form_layout_shots.py` 全部 ERP / Cowork 页面加受影响流程，产出「运行时清单」与上表对账；改后同一扫描断言页面上没有一个可见的非空 `.help-box`（B 类登记的除外）。
2. **闸门**（进 `development.yml` 的 architecture-and-offline）：新 `scripts/check-field-help.py`
   - 共用层组件存在且由 `pearnly_core` 的 `app_include_js` 加载；ERP、Cowork 声明了、DMS 没声明；
   - ERP / Cowork / 共用前端代码里不许绕开：`set_new_description(`、直接写 `.help-box`、`show_description_on_click`；
   - `description_inline` 每一处必须在 `scripts/field-help-inline.tsv` 登记（文件 / 字段 / 原因），条数只许减少。
   以后新字段写 `description:` 自动走图标，不用再登记。流程测试按 11-01 口径留在本机，运行时断言放进流程公共步骤与排版巡检。
3. **回归**：`check-form-layout.py`、`check-keyboard-coverage.py`、`check-architecture.py`、`check-copy.py`、`check-unbounded-queries.py`；实验栈流程：物料新建 / 批量、出入库扫码、盘点录入（两站点）、开班、团队添加、新建录入 / 定时任务、银行对账、收付款弹窗、键盘遮挡两条流程（`erp_keyboard_dialogs`、`cowork_keyboard_intake`）；只读控件、必填星号、Link 下拉、日期选择器、表格行内编辑逐项看；电脑 1280 与手机 375 截图前后对照（`form_layout_shots.py`）；DMS 页面抽查不变。
4. **真机**：三星测试机。主会话口径是从 testerp / testcowork 的 LINE 官方号富菜单进网页端——但这两个域名现在指向生产，本次不部署，生产上没有这次改动。可选做法见 §12 第 3 点。

## 12. 要问主会话的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 图标用 `help`（问号）还是框架原生那条路径的 `info` | `help`，照主会话口径 | 只影响图标形状 |
| 2 | B 类运行时状态 / 数值（B1–B7）留在字段下方，还是也收进图标 | 留在下方，登记豁免 | 收进去的话 B1 岳父要的「一眼分得开」看不见、加载 / 错误要点开才知道 |
| 3 | 真机怎么测：(a) 三星 adb 反连本机实验栈站点（11-01 erp-dev-2 做法，不经 LINE、不动测试机）；(b) 测试机 `testenv.sh lab-start erp` / `cowork` 把 testerp / testcowork 切到实时源码，走 LINE 富菜单 | (a) 先测，用户要看 LINE 入口再 (b)；(b) 要动测试机，按规矩先问 | (b) 期间 testerp / testcowork 不是生产版本 |
| 4 | A13 团队「可见公司」：框架现在就不显示这句，照现状不出图标 | 照现状 | 出图标等于新加一句可见文案 |

## 13. 工作量与排期（机器耗时）

1. 起自己的实验栈 + 改前运行时扫描：约 40 分钟（栈启动与 `lab_init` 为主）。
2. 共用组件 + 声明 + 豁免登记 + 闸门脚本：写代码约 40 分钟。
3. 本地闸门 + 改后扫描 + 受影响流程 + 两宽截图：约 60 分钟（本机流程）。
4. 真机：按 §12 第 3 点，约 20 分钟。
5. 推分支等 Development checks、变基快进 main（合前发主会话一句）：约 20 分钟。不部署。

## 施工与验证（11-02）

### 改动

| 文件 | 内容 |
| --- | --- |
| `apps/pearnly_core/pearnly_core/public/desk/field-help.js`（新增，多产品共用） | 扩展框架 `ControlInput.set_description`：有标题的格把说明放进标题旁 `.help` 插槽，图标 `frappe.utils.icon("info", "xs")`，Bootstrap tooltip（电脑 `hover focus`、触屏 `manual` 点开再点收、点别处 / 关弹窗 / 换页 / 滚动收起、Esc 只收提示不关弹窗），`role=button tabindex=0 aria-label=说明原文`；按钮放在按钮右边；没标题、只有输入框、`description_inline` 照原生并带 `data-pearnly-description="inline"` |
| `pearnly_core/hooks.py` | `app_include_js` 在 `product.js` 后挂 `desk/field-help.js` |
| `pearnly_core/desk/navigation.py` | 启动信息 `pearnly_field_help` = 产品声明的 `field_help` |
| `pearnly_erp_portal/hooks.py`、`pearnly_cowork/hooks.py` | `pearnly_navigation` 声明 `'field_help': 'icon'`（DMS 不声明） |
| `pearnly_core/public/desk/product.css` | 图标颜色 `--text-muted`、光标、对齐；触屏放大点按范围（不改占位） |
| B 类 9 个字段（5 个文件） | `description_inline: 1`；定时任务文件夹有文件数时才置 1、没有时说明进图标 |
| `scripts/check-field-help.py` + `scripts/field-help-inline.json` | 闸门（接线、声明、绕开写法、例外只减不增，比对 merge-base(origin/main)），进 development.yml |
| `tests/test-desk-field-help.cjs` | 离线用例 8 条（真实 field-help.js + 假控件 / jQuery） |
| `scripts/regression/ui_steps.mjs`、`scripts/flows/run.py` | 流程每一步后检查「字段下方还有说明」→ 判定项 `field descriptions below fields`；同时记下图标里的说明（运行时清单） |
| `scripts/regression/form_layout_shots.py` | 排版巡检每屏记下图标与下方说明 |
| `scripts/impact/shared-parts.toml` | `field-help.js` 并入 `desk-shell` 共用件 |

### 实测（实验栈 `pearnly-fieldhelp-lab`，源码 = 本工作树）

- 物料新建弹窗（erp-old，电脑 1280 / 手机 375 无头 Chrome）：编码（只读）/ 库存单位 / 默认仓位三处都是图标、下方 0 处；悬停显示、移开收起、键盘聚焦显示（带 `aria-describedby`）、Esc 只收提示弹窗仍开；手机点开、再点收起、点别处收起；页面与弹窗横向溢出均为 0。
- 三星 SM-A146P 真机（Chrome，adb 反连实验栈，真实触摸 `input tap`）：点「库存单位」图标显示说明、再点收起、点空白收起；提示开着时点到提示框本身只收提示；点输入格键盘弹起，`pearnly-keyboard-open` 生效、格子摆在可见区（keyboard.js 未回退）。
- 剩下的 A 类逐处探针（无头 Chrome，电脑 1280 悬停 / 移开 / 键盘聚焦 / Esc，手机 375 点开 / 再点 / 点别处，均截图）：A8 开班收银台、A10 CC 下载按钮（图标在按钮右边）、A11 银行对账左侧文件、A12 定时任务文件夹（没有文件数时）、A7 Cowork 连续盘点扫码格，全部通过、下方 0 处、横向溢出 0。
- A4 物料批量新建表格：`in_place_edit` 表格框架不给行展开表单（`grid_row.js` 332 行），这句说明原生也从来不显示，照现状（同 A13）。
- A9 推 Express 对应项：流程 `erp.express.service_vat` 打开过这个弹窗，那条待选项没有名称；带名称的（出入库待选物料）实验栈没造出来，按 `needed_fields` 同样的字段形状（Autocomplete、带说明、25 个选项）起弹窗核对通过。
- 泰文长标题（手机 375）：第一版图标掉到单独一行（不横向滚动，但多一行）；改成带图标的标题按行内排后图标跟在最后一个字 / 必填星号后面，物料弹窗两栏高度改前改后都是 411 / 410。
- 勾选框带说明：点图标不会顺带勾上；英文原文照常翻成中文。
- 三星真机（反连实验栈）另测 Cowork 定时任务文件夹：点开显示、再点收起。真机截图：共享笔记目录 `字段说明图标-真机截图-11-02/`。

### 运行时清单（流程全套 190 格 + 排版巡检 66 屏，自动记下的图标与下方说明）

| 站点 | 字段 | 说明 | 在哪看到 |
| --- | --- | --- | --- |
| ERP | 物料 · 编码 / 库存单位 / 默认仓位 | A1–A3 | products.create、products.opening、keyboard.dialogs、排版巡检 |
| ERP | 入库 / 出库 / 调拨 · 单号 | A5 | stock-entry.scan、stock-in.units、排版巡检三页 |
| ERP | 盘点录入 · 扫码格 | A6 | stocktake.gs1 / new-product / places 等 |
| ERP | 公司设置 · `express_synced_at` | 框架 Datetime 的时区行（A*） | company.settings |
| Cowork | 银行对账 · 左侧文件 | A11 | 排版巡检 |
| 两站点 | 字段下方仍有说明 | **0 处**（B 类登记的例外不算） | 全部 190 格每一步、66 屏 |
| DMS | 花名册 · 密码「留空则不修改当前密码。」 | 仍在下方——DMS 不在范围，照原生，证明没被波及 | 排版巡检 |

### 回归

| 项目 | 结果 |
| --- | --- |
| 流程全套（57 条流程 × 两套数据 × 两种宽度） | 190/190 通过（dms.booking.editor new/desktop 首轮负载超时，单独补跑 51.6 秒通过，同共享笔记已知偶发）；新判定「field descriptions below fields」全部通过 |
| 键盘流程（改样式后重跑：erp.keyboard.dialogs、cowork.keyboard.intake、erp.keyboard.line-stocktake、dms.keyboard.editor） | 4/4 通过 |
| 排版巡检（ERP / Cowork / DMS 全部页，电脑 1280 + 手机 375 截图） | 66 屏无报错；截图在 scratchpad `shots-all/`（本机，不进仓库） |
| 静态闸门 | check-field-help、check-architecture、check-form-layout（48 张表单）、check-keyboard-coverage、check-copy（新增 0 改动 0）、check-unbounded-queries、flows/coverage、test_flows_run、test-desk-field-help.cjs（8 条） |

### 共用件连带回归（`check-impact` → `scripts/smoke/run.py --target local --diff`，实验栈 = 本工作树）

自动入口 42 个通过、0 个不通过；没测到的逐条写原因，能由流程用例实测到的标了流程名。

| 入口 | 产品 | 方式 | 结果 | 说明 |
|---|---|---|---|---|
| `erp.page.home` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.products` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.inventory` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.stock-in` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.stock-out` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.stock-transfer` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.stock-card` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.stock-count` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.cashier` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.company` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.warehouses` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.team` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.purchase` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.suppliers` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.sales` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.pos-receipts` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.pos-shifts` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.clients` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.tasks` | ERP | auto：page | 通过 | 0.7 秒 |
| `cowork.page.home` | Cowork | auto：page | 通过 | 1.2 秒 |
| `cowork.page.intake` | Cowork | auto：page | 通过 | 0.7 秒 |
| `cowork.page.recon-bank` | Cowork | auto：page | 通过 | 1.3 秒 |
| `cowork.page.recon-income` | Cowork | auto：page | 通过 | 0.7 秒 |
| `cowork.page.recon-vat` | Cowork | auto：page | 通过 | 0.7 秒 |
| `cowork.page.stocktake` | Cowork | auto：page | 通过 | 0.7 秒 |
| `cowork.page.work` | Cowork | auto：page | 通过 | 0.4 秒 |
| `cowork.page.team` | Cowork | auto：page | 通过 | 0.5 秒 |
| `dms.page.home` | DMS | auto：page | 通过 | 1.8 秒 |
| `dms.page.connect` | DMS | auto：page | 通过 | 0.7 秒 |
| `dms.page.records` | DMS | auto：page | 通过 | 0.7 秒 |
| `dms.page.roster` | DMS | auto：page | 通过 | 0.7 秒 |
| `erp.page.never.push-logs` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.never.reconcile` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.never.guide` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.page.never.history` | ERP | auto：page | 通过 | 0.7 秒 |
| `erp.report.stock-count-variance` | ERP | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `shared.sidebar-order` | 共用 | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `erp.company-menu` | ERP | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `erp.detail.sales.draft` | ERP | auto：doc_detail | 没测到 | no draft ERP Sales Document |
| `erp.detail.sales.submitted` | ERP | auto：doc_detail | 通过 |  |
| `erp.detail.sales.void` | ERP | auto：doc_detail | 通过 |  |
| `erp.detail.purchase.draft` | ERP | auto：doc_detail | 没测到 | no draft Purchase Document |
| `erp.detail.purchase.submitted` | ERP | auto：doc_detail | 通过 |  |
| `erp.detail.purchase.void` | ERP | auto：doc_detail | 没测到 | no void Purchase Document |
| `erp.detail.stock-in.submitted` | ERP | auto：doc_detail | 通过 |  |
| `erp.detail.stock-out.void` | ERP | auto：doc_detail | 没测到 | no void ERP Stock Entry on the first list page |
| `erp.stock-count.switch.posted-to-active` | ERP | auto：record_switch | 通过 |  |
| `erp.stock-count.switch.active-to-posted` | ERP | auto：record_switch | 通过 |  |
| `erp.purchase.detail.not-permitted` | ERP | auto：not_permitted | 没测到 | 没配受限账号 SMOKE_USER_ERP_LIMITED |
| `erp.sales.detail.not-permitted` | ERP | auto：not_permitted | 没测到 | 没配受限账号 SMOKE_USER_ERP_LIMITED |
| `cowork.stocktake.switch` | Cowork | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `erp.place.header` | ERP | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `erp.products.new-dialog.opening` | ERP | manual | 通过（流程用例 erp.products.opening，全套 190/190） | manual：交验收窗口按清单实测 |
| `erp.inventory.opening.row-warehouse` | ERP | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `erp.warehouses.express-new-hidden` | ERP | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `shared.navigation` | 共用 | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `shared.shell-gate` | 共用 | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `shared.keyboard.erp-dialogs` | ERP | manual | 通过（流程用例 erp.keyboard.dialogs，全套 190/190） | manual：交验收窗口按清单实测 |
| `erp.stock-in.item-scan` | ERP | manual | 通过（流程用例 erp.stock-entry.scan，全套 190/190） | manual：交验收窗口按清单实测 |
| `erp.stock-out.item-scan` | ERP | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `erp.purchase.item-scan` | ERP | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `erp.sales.item-scan` | ERP | manual | 通过（流程用例 erp.sales.scan，全套 190/190） | manual：交验收窗口按清单实测 |
| `erp.serials.dialog` | ERP | manual | 通过（流程用例 erp.stock-in.units，全套 190/190） | manual：交验收窗口按清单实测 |
| `erp.scan.pick-product` | ERP | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `shared.page-head-declared` | 共用 | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `erp.line.chat` | ERP | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `cowork.line.chat` | Cowork | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `shared.line.login` | 共用 | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `erp.cc.connect` | ERP | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `shared.exports` | 共用 | manual | 通过（流程用例 erp.exports、cowork.exports，全套 190/190） | manual：交验收窗口按清单实测 |
| `shared.upload.shrink-photo` | 共用 | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `erp.masters.scope` | ERP | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `erp.print.purchase` | ERP | manual | 通过（流程用例 erp.print.all，全套 190/190） | manual：交验收窗口按清单实测 |
| `erp.print.sales` | ERP | manual | 通过（流程用例 erp.print.all，全套 190/190） | manual：交验收窗口按清单实测 |
| `erp.products.find-by-code` | ERP | auto：find_product | 通过 |  |
| `shared.originals.download` | 共用 | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `shared.login` | 共用 | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `shared.execution` | 共用 | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `cowork.execution` | Cowork | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `erp.execution` | ERP | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `shared.assistant` | 共用 | manual | 通过（流程用例 cc.chat，全套 190/190） | manual：交验收窗口按清单实测 |
| `cowork.assistant` | Cowork | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `cc.operator.thread` | CC | manual | 通过（流程用例 cc.operator，全套 190/190） | manual：交验收窗口按清单实测 |
| `erp.provisioning` | ERP | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
| `shared.browser-text` | 共用 | manual | 没测到 | manual：交验收窗口按清单实测；本次只在 `app_include_js` 加载一个脚本、ERP / Cowork 声明一项，与此入口的业务无关，未单独点 |
