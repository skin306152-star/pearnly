# 手机键盘盖住正在填的格（三个产品统一处理）

- 任务分支：`pearnly/mobile-keyboard-overlap-211914`，工作树 `.claude/worktrees/mobile-keyboard-overlap-211914`，基线 `origin/main` 2d484b99。
- 来源：用户 11-01「我在手机点击某段字段时，弹起来的输入框会把这个字段盖住……这么多字段，怎么处理这类情况呢」；方案由主会话提出、用户同意。

## 需求影响评估

| 栏 | 结论 |
|---|---|
| 0 需求 | ERP、Cowork 的 Desk 页面与全部弹窗，DMS 的 LINE 编辑页、LINE 盘点页：点格子后弹出的键盘不能盖住这一格。不在范围：字段布局（11-01 刚统一过）、说明文案、CC 对话页（`frontend/assistant` 自有 `lib/viewport.ts`，不是这三个产品）。 |
| 1 同类复用 | 现有零散写法只有一处：DMS 编辑页 `EditorPage.vue` 的 `keepAboveKeyboard`（fce34c27，保存被拒时把出错栏摆到贴底操作条上方）。新建一个共用模块，删掉这处。 |
| 2 Frappe 原生 | 锁定版本 Frappe 的 `www/desk.html` 写死 viewport（`maximum-scale=1.0 … user-scalable=no, minimal-ui`），没有钩子改；弹窗是 Bootstrap `.modal`（整屏固定、`overflow-y:auto`），手机上没有跟键盘的处理。不改框架文件：viewport 由共用脚本运行时补 `interactive-widget`，弹窗只改外框的 `top / height`，弹窗仍按官方方式自己滚。 |
| 3 按类通用 | 一类：所有会弹键盘的输入格（input 文本类、textarea、contenteditable），不分页面。Desk 由 `pearnly_core` 的 `app_include_js` 统一加载（ERP、Cowork、DMS 同一份；控制面站点不装 pearnly_core，不接）；LINE 盘点页挂同一份；LINE 票据编辑器（Vue）打进自己的包。 |
| 4 上下游 | 纯前端显示，不涉及数据、Express、CC 桌面版载荷。 |
| 5 隔离权限 | 不涉及（不读写数据、不调接口）。 |
| 6 入口 | Desk 页面（整页滚）、Desk 弹窗（弹窗里滚）、LINE 票据编辑器 `/assets/pearnly_core/pearnly-app/index.html#/dms/editor`（整页滚、贴底操作条）、LINE 盘点页 `/stocktake`（整页滚）。 |
| 7 共用件 | 新登记共用件 `phone-keyboard`（`scripts/impact/shared-parts.toml`），入口见下方清单。 |
| 8 成本 | 零：几 KB 脚本，无外部调用。 |
| 9 删除作废 | 不涉及。 |
| 10 文案与官方 UI | 不加任何文案；用官方弹窗，只在键盘开着时把外框摆进可见区。 |
| 11 测试 | 单测 `tests/test-desk-keyboard.cjs`（CI）；流程 4 条（无头 Chrome 缩小可见区模拟键盘）；三星真机；iPhone 没有测试机。 |
| 12 待问 | 无。 |

## 根因

键盘弹出时浏览器只缩「可见区」（`window.visualViewport`），布局视口不变：iOS 一直如此，安卓 Chrome 108 起默认也如此（`interactive-widget` 默认 `resizes-visual`）。
于是：① Frappe 弹窗 `.modal` 仍按整屏高，最后几格在弹窗滚到底时仍在屏幕下部，正好在键盘下面，怎么滚都上不来；
② 浏览器把聚焦的格挪进可见区时不看贴顶 / 贴底的 sticky、fixed 条（Desk 页头、Cowork 分区导航、DMS 操作条），格子被条盖住。
之前只在 DMS 编辑页出错跳格那一处单独补过。

## 统一模块

`apps/pearnly_core/pearnly_core/public/desk/keyboard.js`（文件头写明多产品共用）：

1. viewport 补 `interactive-widget=resizes-content`：安卓 Chrome / WebView 键盘弹出时整页变矮（iOS 不认）。Desk 运行时补；LINE 盘点页、票据编辑器（`frontend/index.html`、`pearnly-app.html`）直接写在 meta 里。
2. 键盘开着（可见区比布局矮 120px 以上、没缩放）时 `<html>` 带 `pearnly-keyboard-open`，并写 `--pearnly-visible-top / --pearnly-visible-height`；
   `product.css` 一条规则把 `.modal.show` 外框摆成可见区那么高，弹窗在外框里照官方方式滚。
3. 输入格获得焦点、可见区变化（键盘弹出）时：可见区减去命中的贴顶 / 贴底 fixed、sticky 条（包着这一格的弹窗不算），把这一格摆到中间
   （比可见区高的多行框顶端对齐）；先滚它所在的滚动容器，滚到头还差的再滚整页；在固定弹层里时不滚整页。人自己滚动不拉回。
4. 加载：Desk `hooks.app_include_js` 第二个（`errors.js` 之后）；LINE 盘点页 `stocktake.html` 用 `versioned_asset` 挂；
   LINE 票据编辑器（真正打开的是 Vite 产物 `index.html`，不经过 `pearnly-app.html`）在 `frontend/src/main.ts` 里 import 同一个文件打进包。同一页挂两次只生效一次。

与主会话方案的差别：方案写的是「`.modal-dialog` 最大高度跟随可见区、弹窗主体可滚动」。改成把 `.modal` 外框摆进可见区——
Bootstrap 本来就是外框滚、对话框在里面不限高，这样不改对话框本身的高度和页眉 / 页脚布局，表单排版不动。效果相同：弹窗整体在可见区里滚。

## 动作边界（主会话 11-01 补充：「别把这边改坏了」「又或者某些没覆盖到位」）

- 只在触屏设备（`pointer: coarse`）加载生效；电脑上一行不执行（不改 meta、不挂监听）。
- 只在「文字输入格（文本类 input、textarea、contenteditable）获得焦点，且可见区比同宽度下见过的最高时矮 120px 以上」期间动作；
  勾选框、按钮、下拉（select）、日期 / 时间（原生选择器，不弹键盘）、只读 / 禁用格不算。转屏换宽度重新记。
- 不监听页面滚动、不锁滚动、不改 body 位置：只在聚焦那一刻、键盘弹起那一下滚这一格所在的容器（容器到头再滚整页；在固定弹层里不滚整页）。
- 弹窗外框只在键盘弹起期间改 `top / height`（类名 + 两个变量），键盘收起即去掉，弹窗回到原样（流程断言收起后类名去掉、弹窗无内联高度）。
- 摆完再量一次：sticky 的条（弹窗标题栏、页头）滚动之后才贴到边上，被它盖住就再挪一次（最多两轮；真机开班弹窗发现，单测覆盖）。
- 带建议列表 / 日历弹层的格（Frappe Link 的 awesomplete、`role=combobox`、`aria-autocomplete`、`list`、Frappe 日期 / 日期时间 / 时间控件）
  摆到可见区上部三分之一，下面留给弹层（真机发现：仓库 Link、单据日期在可见区上半部不动时，下拉 / 日历往下展开被键盘挡得只剩一行；
  旧版同样如此，这里一并按类处理。改后仓库下拉 5 项 + 「新建仓库」、日历整月都在键盘上方）。

## 覆盖矩阵（页面类型 × 怎么接 × 验证）

| 页面类型 | 例子 | 怎么接上 | 流程（无头 Chrome） | 三星真机 |
|---|---|---|---|---|
| Desk 页面（ERP / Cowork / DMS） | 入库新建、Cowork 录入复核、DMS 主页 | `pearnly_core` `app_include_js` | cowork.keyboard.intake（安卓式） | 见下表 |
| Desk 弹窗（p.dialog / frappe.ui.Dialog / FieldGroup，Bootstrap `.modal`） | 物料新建、团队添加用户 | 同上 + `product.css` 弹窗外框规则 | erp.keyboard.dialogs（iOS 式 + 安卓式） | 见下表 |
| 网站页（`templates/web.html`） | 改密码 `pearnly-password` | `pearnly_core` `web_include_js` | 无（静态检查核接入） | 见下表 |
| www 独立页 | LINE 手机盘点 `stocktake.html` | 页面 `versioned_asset` 挂 | erp.keyboard.line-stocktake（安卓式） | 见下表 |
| 前端单页（Frappe UI，LIFF） | DMS LINE 订车编辑页、取景页、账号页（`pearnly-app`） | `frontend/src/main.ts` import 同一文件打进包；`dms-booking.html` / `pearnly-app.html` 交给这个包 | dms.keyboard.editor（安卓式） | 见下表 |
| CC 对话页 | `assistant.html`、`frontend/assistant` | **不接**：不在三个产品范围；自有 `lib/viewport.ts` 按可见区排版、meta 已带 `interactive-widget`，再挂会重复摆位 | — | — |
| 控制面 Desk 与发号页 `pearnly-issue` | 控制面站点 | **不接**：控制面站点只装 `pearnly_control`、不装 `pearnly_core`（实验栈 `list-apps` 核过），共用模块挂不上；管理后台在本机电脑上用（AGENTS.md）；用户说过发号页不要管 | 电脑宽零变化见下 | — |
| CC 智能体入口登录页 | 控制面 `ai.html`（fatcc.com） | **不接**：CC 品牌页，只有首屏居中两格 | — | — |
| Keycloak 登录 / 改密页 | `infrastructure/keycloak/themes/*` | **不接**：登录页按规矩不改（记忆 login-pages-frozen） | — | — |
| 打印格式、邮件、Python 拼的中转页 | `printing/templates`、`portal.py` | 没有文字输入格，不需要 | — | — |

静态检查 `scripts/check-keyboard-coverage.py`（进 Development checks）：扫进版本库的全部整页文件（apps 下 html、Python 拼的整页、frontend 各单页入口），
有文字输入格或挂单页应用的必须接上，`hooks.py` 两处必须挂；按设计不接的登记在 `scripts/keyboard-coverage-exempt.json`（只许减少，比对 merge-base）。
反向验证：临时加一个带输入框的 www 页，检查失败并点名；删掉后通过。

## 删掉的零散写法

- `frontend/src/pages/dms/EditorPage.vue` 的 `keepAboveKeyboard`（fce34c27）：出错跳格后聚焦输入框，摆位交给共用模块（贴底操作条由「命中的 sticky 条」通用处理）。
- 全仓再查 `scrollIntoView / scrollBy / visualViewport / innerHeight`：其余都是分区导航、看板高度、下拉菜单定位，与键盘无关，不动。

## 验证（分支 `pearnly/mobile-keyboard-overlap-211914`）

环境：本机回归实验栈 `pearnly-kbd-lab`（生产镜像同一套底座，挂本工作树源码，端口 18801–18803），三星 SM-A146P（Chrome 156，安卓）经 adb 开实验栈页面。

### 单测与静态检查

- `node --test tests/test-desk-keyboard.cjs`：9 项通过（电脑一行不执行、meta 只补一次、整页、贴底条（安卓整页变矮）、固定弹窗、没聚焦不动、
  勾选框 / 文件 / 日期 / 只读 / 禁用 / 下拉不动、转屏、Link 与日期摆上部三分之一）。
- `python3 scripts/check-keyboard-coverage.py`：通过（28 个页面）；反向：临时加一个带输入框的 www 页 → 失败并点名，删掉后通过。
- 本地全闸门（照 development.yml 158 步，`~/pearnly-devtools/gate.sh`）：首轮全过；最终提交上重跑结果见下方「合入前」。
- 生产镜像候选离线检查（`cand-local.sh offline`，镜像 fd60caff）：通过（EXIT=0）。

### 流程（无头 Chrome，模拟键盘）

| 流程 | 模式 | 有统一模块 | 去掉模块对照（`KEYBOARD_BASELINE=1`） |
|---|---|---|---|
| erp.keyboard.dialogs（物料新建、团队添加用户、入库新建明细） | iOS + 安卓 | 17/17 通过：9 格都在可见区、弹窗高 = 可见区 380、滚得到按钮栏；收起后类名去掉 | 8/17：例如物料新建最后一格 566–594、可见区只到 380 |
| cowork.keyboard.intake（复核页最下面三格） | 安卓 | 4/4 | 3/4：备注 788–828 |
| dms.keyboard.editor（订车编辑页最下面三格） | 安卓 | 4/4 | 3/4：付款格 776–804 |
| erp.keyboard.line-stocktake（新建商品最下面三格） | 安卓 | 4/4 | 3/4：成本格 785–825 |

无头 Chrome 的安卓模式是把视口真的变矮，Chrome 不会像真机那样自己把聚焦格滚进来，所以「去掉模块」那一列比真机悲观；真机结果见下表。
iOS 只模拟了弹窗（整页滚动时 iOS 自己平移可见区，这个平移模拟不了）。

分支受影响流程（`run.py --changed`，电脑 + 手机两种宽度、两套数据）：4e638e56 上 7 条 14/14 通过；最终提交结果见「合入前」。

### 三星真机（实测，截图在共享笔记 `手机键盘真机截图-11-01/`）

| 页面 | 点的格 | 键盘弹起后 | 结果 |
|---|---|---|---|
| ERP 物料「新建」弹窗 | 低库存提醒（最下面） | 页面变矮到 354，弹窗外框 354，格子 195–223，提交按钮在键盘上方 | 通过 |
| ERP 团队「添加用户」弹窗 | 邮箱、密码 | 195–223 / 133–161，弹窗 354 | 通过 |
| ERP 入库新建（手机上是整页） | 原因、单位成本 | 262–302（可见 418）/ 167–195（可见 354） | 通过 |
| ERP 入库 仓库 Link 下拉 | 仓库 | 改前：格子不动，下拉被键盘挡得一项都不完整；改后格子到页头下方，5 项 + 「新建仓库」可见 | 通过（改后） |
| ERP 入库 单据日期（日历弹层） | 日期 | 改前：只露第一周；改后整月可见（只有「今天」按钮半截） | 通过（改后） |
| ERP 收银台开班弹窗 | 备注；支付方式表格最后一行（表格内联编辑） | 279–319；表格行在键盘上方 | 通过 |
| ERP 收银台开班弹窗 收银台 Link（第一格） | 收银台 | 改前（第一版统一模块）：格子摆到顶端后被滚动才贴顶的弹窗标题栏盖住；改后摆完再量一次，格子 55–83、标题栏到 47 | 通过（改后） |
| ERP 收银台 POS 全流程 | 开班（接口，同收银首单流程）→ 搜索格输条码（键盘弹起，搜索格 112–156 在键盘上方）→ 加进购物车 → 结账 → 现金收款（POS 自带数字键盘，不弹系统键盘）→ 完成订单 | 实验室单 SI2569-000001「已付款」 | 通过 |
| ERP 扫码弹窗（入库物料扫码按钮） | 不弹键盘 | 相机画面在播（1080 宽），模块不介入 | 通过 |
| 弹窗上再弹 msgprint | 邮箱聚焦时 `frappe.msgprint` | 提示框拿走焦点、键盘收起、两个弹窗都回整屏 | 通过 |
| Cowork 录入复核 | 备注、价税合计 | 363–403 / 264–292（可见 418），贴顶分区导航没挡 | 通过 |
| DMS LINE 订车编辑页 | 付款金额、登记人姓名、交车日期 | 都在 136–164，贴底操作条在 300 | 通过 |
| DMS 编辑页保存被拒（删掉的零散补丁原来管的） | 真触摸点保存 | 出错的交车日期标红聚焦，156–184，提示条和保存在 340 | 通过 |
| LINE 手机盘点 新建商品 | 低库存、默认库位 | 189–230（可见 354） | 通过 |
| 网站页 改密码（web_include_js） | 确认新密码 | 288–316（可见 354），脚本与 meta 已接上 | 通过 |

### 电脑宽零变化（主会话要求）

同一页面用电脑宽度无头 Chrome 各开两次：模块生效 / 页面脚本前占住 `window.pearnlyKeyboard` 让模块不执行，比页面状态（viewport、`<html>` 类名与内联变量）和截图像素：

| 页面 | 状态 | 像素差异 | 页面报错 |
|---|---|---|---|
| 控制面发号页 `/pearnly-issue` | 相同（控制面站点没装 pearnly_core，模块根本不加载） | 无 | 无 |
| ERP 入库新建 | 相同（meta 未改、无类名无变量，`pointer: coarse` 为假） | 无 | 无 |
| ERP 物料新建弹窗（聚焦低库存提醒） | 相同 | 无 | 无 |
| ERP 改密码网站页（聚焦确认密码） | 相同 | 无 | 无 |
| Cowork 录入列表 | 相同 | 无 | 无 |

另外受影响流程与组件回归流程都跑了电脑宽度（见「合入前」）。

没测到：iPhone（没有测试机）；LINE 内置浏览器（实验室站点进不了 LIFF，安卓 LINE 内置浏览器与 Chrome 同为 Chromium 内核，部署后用 LINE 真机看）。

真机顺带发现（不在本次范围，已记进共享笔记「已知问题与结论」）：LINE 盘点页底部透明提示条 `.toast`（opacity 0）仍接收点击，
格子滚到屏幕最下约 60px 时点不中。

## 共用件连带回归清单（`check-impact.py`，最终提交 6f9704c4）

命中共用件 `desk-shell`（product.css）与新登记的 `phone-keyboard`，72 个入口。自动入口用 `scripts/smoke/run.py --target local --diff`
对本机实验栈跑；人工入口按上面的真机与流程结果逐个填，「没测到」写原因。合计：通过 51、不通过 0、没测到 21。


| 入口 | 产品 | 方式 | 结果 | 说明 |
|---|---|---|---|---|
| `erp.page.home` | ERP | auto：page | 通过 | 1.0 秒 |
| `erp.page.products` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.inventory` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.stock-in` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.stock-out` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.stock-transfer` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.stock-card` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.stock-count` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.cashier` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.company` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.warehouses` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.team` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.purchase` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.suppliers` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.sales` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.pos-receipts` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.pos-shifts` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.clients` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.tasks` | ERP | auto：page | 通过 | 0.5 秒 |
| `cowork.page.home` | Cowork | auto：page | 通过 | 1.4 秒 |
| `cowork.page.intake` | Cowork | auto：page | 通过 | 0.5 秒 |
| `cowork.page.recon-bank` | Cowork | auto：page | 通过 | 1.1 秒 |
| `cowork.page.recon-income` | Cowork | auto：page | 通过 | 0.5 秒 |
| `cowork.page.recon-vat` | Cowork | auto：page | 通过 | 0.5 秒 |
| `cowork.page.stocktake` | Cowork | auto：page | 通过 | 0.4 秒 |
| `cowork.page.work` | Cowork | auto：page | 通过 | 0.4 秒 |
| `cowork.page.team` | Cowork | auto：page | 通过 | 0.4 秒 |
| `dms.page.home` | DMS | auto：page | 通过 | 1.3 秒 |
| `dms.page.connect` | DMS | auto：page | 通过 | 0.4 秒 |
| `dms.page.records` | DMS | auto：page | 通过 | 0.4 秒 |
| `dms.page.roster` | DMS | auto：page | 通过 | 0.4 秒 |
| `erp.page.never.push-logs` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.never.reconcile` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.never.guide` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.page.never.history` | ERP | auto：page | 通过 | 0.5 秒 |
| `erp.report.stock-count-variance` | ERP | manual | 没测到 | 报表页，手机键盘只在文字格聚焦时动作；页面打开由 Desk 外壳页面冒烟同一加载路径覆盖 |
| `shared.sidebar-order` | 共用 | manual | 通过 | 受影响流程与组件回归流程（ERP / Cowork 电脑 + 手机宽度）都经过侧栏导航，全过 |
| `erp.company-menu` | ERP | manual | 没测到 | 只因调用方文件 `pearnly_core/hooks.py` 登记在它下面而列入；本次 hooks.py 只多挂一个 `desk/keyboard.js`、新增 `web_include_js`，不碰这一项的逻辑；挂这个脚本的 Desk 页面打开由上面 41 个自动页面冒烟覆盖（全过） |
| `erp.detail.sales.draft` | ERP | auto：doc_detail | 没测到 | 实验室这套数据没有草稿单：no draft ERP Sales Document |
| `erp.detail.sales.submitted` | ERP | auto：doc_detail | 通过 |  |
| `erp.detail.sales.void` | ERP | auto：doc_detail | 通过 |  |
| `erp.detail.purchase.draft` | ERP | auto：doc_detail | 没测到 | 实验室这套数据没有草稿单：no draft Purchase Document |
| `erp.detail.purchase.submitted` | ERP | auto：doc_detail | 通过 |  |
| `erp.detail.purchase.void` | ERP | auto：doc_detail | 没测到 | 没有作废单：no void Purchase Document |
| `erp.detail.stock-in.submitted` | ERP | auto：doc_detail | 通过 |  |
| `erp.detail.stock-out.void` | ERP | auto：doc_detail | 没测到 | 列表为空：empty list |
| `erp.stock-count.switch.posted-to-active` | ERP | auto：record_switch | 通过 |  |
| `erp.stock-count.switch.active-to-posted` | ERP | auto：record_switch | 通过 |  |
| `erp.purchase.detail.not-permitted` | ERP | auto：not_permitted | 没测到 | 没配受限账号 SMOKE_USER_ERP_LIMITED |
| `erp.sales.detail.not-permitted` | ERP | auto：not_permitted | 没测到 | 没配受限账号 SMOKE_USER_ERP_LIMITED |
| `cowork.stocktake.switch` | Cowork | manual | 没测到 | Cowork 盘点详情切换，不涉及文字格；同一 Desk 加载路径由页面冒烟覆盖 |
| `shared.keyboard.erp-dialogs` | ERP | manual | 通过 | 流程 erp.keyboard.dialogs（iOS + 安卓式 17/17）；三星真机物料新建、团队添加用户、开班弹窗（含表格内联编辑、收银台 Link）全过 |
| `shared.keyboard.cowork-intake` | Cowork | manual | 通过 | 流程 cowork.keyboard.intake 4/4；三星真机备注、价税合计在键盘上方 |
| `shared.keyboard.dms-editor` | DMS | manual | 通过 | 流程 dms.keyboard.editor（从 /pearnly-app 进）；三星真机付款金额、登记人、交车日期、保存被拒跳出错格全过 |
| `shared.keyboard.erp-line-stocktake` | ERP | manual | 通过 | 流程 erp.keyboard.line-stocktake 4/4；三星真机新建商品各格在键盘上方 |
| `erp.line.stocktake` | ERP | manual | 通过 | 流程 erp.line.stocktake.phone、erp.line.stocktake 两套数据电脑 + 手机全过（LIFF 登录走本账号绑定回落，真 LINE 没测到） |
| `dms.liff.id-scan` | DMS | manual | 通过（实验室） | 流程 dms.liff.scan-loading 通过（Vue 包入口加了一行 import）；真 LINE 拍证没测到，部署后 LINE 真机补验 |
| `shared.page-errors` | 共用 | manual | 没测到 | errors.js 与回传接口未改，只是同页多挂一个脚本；各流程都断言页面无报错、站点 Error Log 无新增前端回传，全过 |
| `erp.line.stocktake.camera` | ERP | manual | 没测到 | LINE LIFF 里的相机要真 LINE；盘点页只多挂一个脚本，扫码没动；Desk 扫码弹窗三星真机相机画面在播 |
| `erp.scan.pick-product` | ERP | manual | 没测到 | 只因调用方文件 `pearnly_core/hooks.py` 登记在它下面而列入；本次 hooks.py 只多挂一个 `desk/keyboard.js`、新增 `web_include_js`，不碰这一项的逻辑；挂这个脚本的 Desk 页面打开由上面 41 个自动页面冒烟覆盖（全过） |
| `shared.line.login` | 共用 | manual | 没测到 | 只因调用方文件 `pearnly_core/hooks.py` 登记在它下面而列入；本次 hooks.py 只多挂一个 `desk/keyboard.js`、新增 `web_include_js`，不碰这一项的逻辑；挂这个脚本的 Desk 页面打开由上面 41 个自动页面冒烟覆盖（全过） |
| `erp.cc.connect` | ERP | manual | 没测到 | 只因调用方文件 `pearnly_core/hooks.py` 登记在它下面而列入；本次 hooks.py 只多挂一个 `desk/keyboard.js`、新增 `web_include_js`，不碰这一项的逻辑；挂这个脚本的 Desk 页面打开由上面 41 个自动页面冒烟覆盖（全过） |
| `shared.exports` | 共用 | manual | 没测到 | 只因调用方文件 `pearnly_core/hooks.py` 登记在它下面而列入；本次 hooks.py 只多挂一个 `desk/keyboard.js`、新增 `web_include_js`，不碰这一项的逻辑；挂这个脚本的 Desk 页面打开由上面 41 个自动页面冒烟覆盖（全过） |
| `shared.navigation` | 共用 | manual | 通过 | 各流程与 41 个页面冒烟经过侧栏 / 页头，全过；电脑宽 5 个页面零差异 |
| `shared.upload.shrink-photo` | 共用 | manual | 没测到 | 只因调用方文件 `pearnly_core/hooks.py` 登记在它下面而列入；本次 hooks.py 只多挂一个 `desk/keyboard.js`、新增 `web_include_js`，不碰这一项的逻辑；挂这个脚本的 Desk 页面打开由上面 41 个自动页面冒烟覆盖（全过） |
| `erp.line.stocktake.new-product` | ERP | manual | 通过 | 流程 erp.line.stocktake 两套数据全过；三星真机新建商品表单各格（未提交） |
| `shared.originals.download` | 共用 | manual | 没测到 | 只因调用方文件 `pearnly_core/hooks.py` 登记在它下面而列入；本次 hooks.py 只多挂一个 `desk/keyboard.js`、新增 `web_include_js`，不碰这一项的逻辑；挂这个脚本的 Desk 页面打开由上面 41 个自动页面冒烟覆盖（全过） |
| `shared.login` | 共用 | manual | 通过（部分） | 网站页改密码页三星真机与电脑宽都正常（web_include_js）；OIDC 登录 / 退出没测到（Keycloak 登录页不改、实验栈无 Keycloak） |
| `shared.execution` | 共用 | manual | 没测到 | 只因调用方文件 `pearnly_core/hooks.py` 登记在它下面而列入；本次 hooks.py 只多挂一个 `desk/keyboard.js`、新增 `web_include_js`，不碰这一项的逻辑；挂这个脚本的 Desk 页面打开由上面 41 个自动页面冒烟覆盖（全过） |
| `shared.assistant` | 共用 | manual | 没测到 | CC 对话页按设计不挂共用模块（静态检查豁免、assistant.html 未改） |
| `shared.browser-text` | 共用 | manual | 没测到 | 只因调用方文件 `pearnly_core/hooks.py` 登记在它下面而列入；本次 hooks.py 只多挂一个 `desk/keyboard.js`、新增 `web_include_js`，不碰这一项的逻辑；挂这个脚本的 Desk 页面打开由上面 41 个自动页面冒烟覆盖（全过） |
| `erp.browser-text` | ERP | manual | 没测到 | 只因调用方文件 `pearnly_core/hooks.py` 登记在它下面而列入；本次 hooks.py 只多挂一个 `desk/keyboard.js`、新增 `web_include_js`，不碰这一项的逻辑；挂这个脚本的 Desk 页面打开由上面 41 个自动页面冒烟覆盖（全过） |

## 合入前（最终提交 6f9704c4）

- 受影响流程 `run.py --changed --post-status`：7 条 × 两套数据 × 电脑 / 手机，14/14 通过，已回写 `pearnly/flows`。
- 组件回归流程（新开户数据，电脑 + 手机）：物料新建、采购新建、收银首单 POS、出入库扫码（相机）、销售扫码、团队添加、供应商 / 客户弹窗、
  盘点扫码、Cowork 复核，9 条 18/18 通过（在 4d52d811 上跑；之后只改了 DMS 键盘流程的起始地址与断言）。DMS 取景页流程通过。
- 本地全闸门 158 步：在 4d52d811 上只有流程覆盖闸（pearnly-app.html 没有流程覆盖）不过，已补并在 6f9704c4 上单跑覆盖闸通过。

## 上线后补验（本任务不部署）

- 主会话：下一次部署后在生产上再点一遍 POS 和扫码，作为镜像层补验（本次用本机实验栈 + 真机代替测试机 live-off，主会话 11-01 同意）。
- 真机进生产测试租户网页：从 testerp / testcowork 的 LINE 官方号富菜单进（走 LINE 登录），不在手机 Chrome 输密码（用户指点，主会话 11-01）。
  顺带补验 LINE 内置浏览器里的 DMS 编辑页、LINE 盘点页、Desk 弹窗最下面几格。

## 后续：LINE 盘点页透明提示条挡点击（分支 `pearnly/stocktake-toast-click`，主会话 11-01 让修）

- 现象：`/stocktake` 底部的提示条（fixed、bottom 16px、高约 44px）不显示时只是 `opacity: 0`，仍接收点击；格子滚到屏幕最下约 60px 时点到的是看不见的提示条（键盘遮挡真机测试时发现）。
- 修法：提示条只显示文字，任何时候 `pointer-events: none`（显示时点穿到下面的格），不显示时 `visibility: hidden`（不占读屏）。
- 全仓同类：其余靠透明度隐藏的元素要么已 `pointer-events: none`（扫码闪光与对勾、Link 箭头），要么宽度为 0（看板完成勾），要么是 CC 登录页吉祥物内部装饰；
  Desk 手机宽度核过：Frappe 提示用时才建、用完即删，没有常驻透明容器，屏幕最下 60px 点到的都是页面内容；日期选择器未打开时在屏幕外（left −100000）。只有这一处要改。
- 流程 `erp.line.stocktake.phone` 加断言：把格子滚到屏幕最下 60px 内，中心点点得中；提示条不显示时那一条点不到它。对照：撤回 CSS 时这一项失败（点到的是 `toast`），恢复后受影响流程 3 条 × 两套数据 7/7 通过。
- 真机：
  - 生产：LINE ERP-TEST 富菜单「盘点」进的是 Desk 盘点页（`STOCKTAKE_ROUTE`），不是 `/stocktake`，所以生产上从 testerp 富菜单复现不了这张页；只打开了 QA1101 看了一眼，未保存未改。
  - 实验栈 `pearnly-toast-lab`（手机 Chrome）：「低库存提醒」摆在提示条那一带（671–711，提示条 669–713），真触摸 y=691 聚焦成功、键盘弹起后格子摆到 189–230。
    改前同样操作点到的是提示条（键盘遮挡测试时实测）。为把格子滚到那么低，测试时在页面末尾临时加了一段空白（只为摆位置）。
- 没测到：LINE 内置浏览器里的 `/stocktake`（testerp 富菜单进不去）。

## 上线后真机补验（生产 ee77289e，发布标记 release-20261101-1546，11-01 23:21–23:50）

三星 SM-A146P，全程在 LINE 内置浏览器里：ERP-TEST（testerp）、DMS-TEST（testdms）、Cowork-test（testcowork）富菜单进网页，LINE 登录，没输密码、没解绑 / 重绑。
testerp 公司是当前选中的 บริษัท ซิสเตอร์ เมคอัพ จำกัด（QA1101 盘点、以往 POS 班次都在这家；问过主会话用哪家，没等到回复，按这家做）。
截图在共享笔记 `部署后补验-真机-11-01/`（文件名前的序号即下表证据号）。

注意：部署前就开着的 LINE 页面（Desk 单页应用）切侧栏不会换脚本，第一次点物料弹窗最下格被底栏挡住（证据 05）就是旧版本；
「⋮ → 刷新」后才是新版（证据 07 起），正好当了新旧对照。

| # | 项目 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 新建物料弹窗：「低库存提醒」（最下一格，数字键盘） | 通过：摆在标题栏和底栏中间，弹窗收在键盘上方 | 08（旧页 05 被底栏挡） |
| 2 | 新建物料弹窗：「默认仓位」（Link） | 通过：摆到上部；这家没有仓位，下拉无选项 | 09、10 |
| 3 | 新建物料弹窗：「库存单位」（普通文字格） | 通过：摆在中间 | 12、13 |
| 4 | 团队「添加用户」：「邮箱」（最下文字格） | 通过 | 50–52 |
| 5 | 新建入库单：明细「物料」Link | 通过：格子摆到顶部，下拉完整显示在键盘上方（含「新建物料」） | 21 |
| 6 | 新建入库单：「备注」（最下一格，多行） | 通过 | 23 |
| 7 | 新建入库单：明细「仓位」Link | 通过：摆到顶部（无仓位，无选项） | 25 |
| 8 | 新建入库单：「日期」（日历弹层） | **不通过（新问题）**：格子不在上部时第一次点，格子被挪到顶部后键盘和日历随即都收起，要再点一次；再点（已在顶部）日历完整显示在键盘上方。原因：Frappe 弹窗滚动时会让日期 / 时间格失去焦点（`frappe/public/js/frappe/ui/dialog.js` 的 `$wrapper.on("scroll")`），共用模块为把这类格摆到上部去滚 `.modal` 正好触发。只影响弹窗里的 Date / Datetime / Time；页面不受影响；改前不会自己收起 | 30、31b、32 |
| 9 | 盘点 QA1101 连续盘点：「本次实盘数量」「说明」 | 通过：找到物料后焦点跳到数量格，都在键盘上方 | 39、40 |
| 10 | 盘点连续盘点：「实际仓位」 | 与键盘无关的问题：每次点都弹服务端「未找到 / 未找到仓库」（这条上次录在仓「123」），见下方「顺带发现」 | 41、43 |
| 11 | DMS 订车编辑页：「楼层」（靠近贴底操作条） | 通过：摆在操作条和键盘上方 | 86 |
| 12 | DMS 订车编辑页：最下「金额」 | 通过：在贴底操作条上方 | 88 |
| 13 | POS 开班弹窗：表格内联编辑「现金」期初金额、「备注」 | 通过 | 100、101 |
| 14 | POS 开班 → 搜索框录条码加商品 → 结账 → 现金收款 | 通过：开班「班次 4」；条码 8851932283953 回车加入沐浴露 1 瓶 ฿100；收银单 **SI2569-000006**「已付款」 | 102、106–111 |
| 15 | POS 交班弹窗：表格内联编辑「现金」清点金额 | 通过：格子摆到弹窗上方；交班差额 ฿0，班次 **SHIFT-2026-170008** | 114–119 |
| 16 | POS 扫码弹窗相机 | 通过（画面）：LINE 相机授权「仅允许本次」后取景是活的，开闪光灯看得到桌面；**读码没测到**：夜里手机朝桌面，没有码图（要用测试机屏幕显示码图需主会话批） | 103–105 |
| 17 | 盘点录入相机（erp.line.stocktake.camera） | 通过（画面），读码没测到（同上） | 37、38 |
| 18 | LINE 登录（shared.line.login） | 通过：Desk 里退出 → 停在 Keycloak 登录页（没输密码）→ 关掉、富菜单「盘点」重进，约 12 秒内经 LINE 自动登录回到盘点列表 | 124–128 |
| 19 | 公司切换菜单（erp.company-menu） | 通过：工作空间菜单 → 公司列表、搜索框正常 | 15–17 |
| 20 | 销售详情（erp.detail.sales） | 通过：SI2569-000006 详情「已完成」、฿100 | 122 |
| 21 | DMS 扫描身份证取景页（dms.liff.id-scan） | 通过（画面）：相机授权后取景框、「拍照 / 返回聊天」正常；没有实体身份证，没拍 | 131–133 |
| 22 | Cowork 盘点详情来回切换（cowork.stocktake.switch） | 通过：两张盘点详情从列表来回打开正常 | 138–141 |

没测到（原因）：iPhone（没有测试机）；`/stocktake` 独立页（testerp 富菜单进的是 Desk 盘点页）；实拍读码（见 16）；
草稿 / 作废单详情、无权限详情（生产测试租户没有草稿单、没配受限账号）；`shared.page-errors`（没查生产 Error Log）；
其余只因 hooks.py 列入的项（导出、照片压缩、原件下载、执行、浏览器文字等）没逐个点，页面打开由上面各页覆盖。

测试数据：testerp ซิสเตอร์ เมคอัพ 新增班次 SHIFT-2026-170008（已交班）与收银单 SI2569-000006（现金 ฿100，沐浴露库存 −1），留在测试租户；
物料、入库、添加用户、盘点录入弹窗都没保存，QA1101 没改。DMS-TEST 订车草稿已「放弃」（没写 DMS、客户主档没改；识别合成身份证图 1 次），
之后为看取景又点过一次「订车 → 扫描身份证」，对话停在「请发送客户的身份证照片」。推到手机的合成图已删。

顺带发现（与键盘改动无关，未修）：盘点连续盘点里认出上次录在仓「123」的物料后，「实际仓位」每次都弹「未找到仓库」。
读代码：`stocktake-entry.js` 第 223 行把「实际仓库」设成 `warehouse_name`，仓位候选 `inventory.locations.api_location_options` 用这个值当仓库编号去查；
仓库名与编号不同时就会查不到。没在库里核实「123」的编号。

## 后续：iPhone LINE 内置浏览器里弹窗最下面的格没滚上来（分支 `pearnly/keyboard-ios`，主会话 11-01 23:31 转用户实测）

- 现象（用户 iPhone、LINE 内置浏览器、生产 testerp）：调拨「新建调拨单」点最下面「备注」，键盘弹起后弹窗收到键盘上方，但内容停在「单位成本」那一段，备注看不见。
- 复现：Mac 上 iOS 模拟器（iOS 27.0、iPhone 17）。Safari 复现不了；做了一个仿 LINE 的最小 App（顶部栏 + WKWebView，两种键盘处理：
  WebKit 自己处理 / 键盘弹出时 App 把网页视图缩矮）后复现，与用户截图一致。模拟器 Safari 与宿主里的网页经 `ios_webkit_debug_proxy` 读数。
- 根因：App 内嵌网页键盘弹起后 `visualViewport.offsetTop` 报 363、高 351，但 `getBoundingClientRect` / `elementFromPoint` 的原点就是可见区顶端
  （弹窗 CSS `top: 363px`，矩形却是 0–351）。共用模块按「offsetTop 到 offsetTop+高」（363–714）当可见区，备注 568–608 被当成看得见，没滚。
  Safari、安卓 Chrome 两套坐标一致，所以 Safari、安卓真机和原来的模拟流程都过了。凡是 iOS 上的 App 内置浏览器（LINE、Facebook 等），
  弹窗与页面里靠下的格都会这样，不只备注。
- 修法：可见区不再用 visualViewport 的数自己推。键盘弹起期间放一个看不见的 fixed 探针（与弹窗外框同一组变量），读它实际落在哪里；
  键盘停稳后 400ms 再核一次。电脑与键盘收起时没有探针。
- 同批修：弹窗里靠下的日期 / 时间格第一次点就收起（上线后补验第 8 项）：Frappe 弹窗滚动时让 Date / Datetime / Time 格失焦，
  摆它的那一下先拿掉类型标记、两帧后放回；官方日历按可见区重选上 / 下。
- 数值（仿 LINE 宿主、WebKit 自己处理键盘）：聚焦前可见区 748、弹窗滚到 921，备注 568–608；键盘弹起后 `innerHeight` 351、
  `visualViewport` 高 351 / `offsetTop` 363 / `pageTop` 397，弹窗 CSS `top` 363px、矩形 0–351，y=50–340 命中弹窗内容。
  另一种宿主（键盘弹出时 App 把网页视图缩矮）同样复现。

### 复工结果（11-02，分支 `pearnly/keyboard-ios`，基线 main 339e38b7，最终提交见下）

实测方式：iOS 模拟器（iOS 27.0、iPhone 17），模拟器面板没授权，全程用 `simctl` 起宿主 / 截图、`ios_webkit_debug_proxy` 在网页里聚焦与读数；
宿主为测试放开「程序聚焦也弹键盘」，弹出的是真软键盘。三种 iOS 宿主：Safari；仿 LINE「WebKit 自己处理」（inset）；仿 LINE「键盘弹出时 App 把网页缩矮」（resize）。
每个入口取最下面几格，先收起键盘、把格子滚到屏幕底部停 1 秒，再聚焦，1.6 秒后量：格子在可见区里、中心点就是它、模块标记键盘已弹起。
脚本 `~/pearnly-devtools/ios-keyboard-sim/sim_entries.py`（复用各流程的数据准备），截图在共享笔记 `iOS键盘模拟器-11-02/`。

| 入口（测的格） | 旧代码（生产 ee77289e）· iOS 仿 LINE | 新 · iOS Safari | 新 · iOS 仿 LINE（inset） | 新 · iOS 仿 LINE（resize） | 新 · 安卓 |
| --- | --- | --- | --- | --- | --- |
| 新建物料（参考成本、默认仓位、低库存提醒） | **3/3 被挡**（格子 525–561，可见区 351–378） | 通过 | 通过 | 通过 | 流程通过；三星真机低库存提醒在可见区 |
| 团队添加用户（最后三格） | **2/3 被挡**（初始密码、邮箱） | 通过 | 通过 | 通过 | 流程通过 |
| 入库新建明细（单位成本、用途、备注） | **3/3 被挡** | 通过 | 通过 | 通过 | 流程通过 |
| 出库新建明细（单位、用途、备注） | **3/3 被挡** | 通过 | 通过 | 通过 | 键盘流程未单列（同一弹窗写法），页面冒烟通过 |
| 调拨新建（数量、单位、备注）—— 用户这次的场景 | **3/3 被挡** | 通过 | 通过 | 通过 | 流程通过（新加）；三星真机备注 497→189、数量在可见区 |
| 盘点连续录入（数量、说明） | **2/2 被挡** | 通过 | 通过 | 通过 | 流程（盘点扫码）通过 |
| POS 开班弹窗（收银台、备注） | **备注被挡** | 通过 | 通过 | 通过 | 流程（收银首单）通过 |
| Cowork 录入复核（预扣税、合计、备注） | **3/3 被挡** | 通过 | 通过 | 通过 | 流程通过 |
| DMS LINE 订车编辑页（最下三格） | 格子都看得见（iOS 自己滚的），模块没介入 | 同左 | 同左 | 同左 | 流程通过；三星真机最后一格 635→195 |
| LINE 盘点页 `/stocktake` 新建物料（参考成本、默认仓位、低库存） | **2/3 被挡**（参考成本、低库存提醒 538–580，可见区 378） | 通过 | 通过（见下） | 通过 | 流程通过 |
| 弹窗下部日期格（第一次点就开日历） | 第一次点会收起（11-01 三星真机） | 通过：保持聚焦，日历在键盘上方 | 通过 | 通过 | 三星真机 3/3 第一次点保持聚焦、日历在格子下方（见下）；单测锁住 |

回答「有多少没被覆盖」：生产上的共用模块在 iOS 的 App 内置浏览器（LINE 等）里，测到的 9 个弹窗 / 页面入口最下面的格全部（或大部分）没被摆进可见区，
也就是 iOS 内置浏览器这一层整层没覆盖；Safari 和安卓不受影响。新代码这 9 个在三种 iOS 宿主里全部通过。

另记：
- DMS 编辑页的输入框字号小于 16px，iOS 聚焦时把页面放大到 1.14 倍；共用模块按设计在页面被放大时不动手，格子由 iOS 自己滚进可见区，
  三种宿主都看得见、没被贴底条挡住（新旧代码一样）。放大本身是 iOS 对小字号输入框的默认行为，要不放大得把输入框字号改成 16px（界面改动，没做）。
- LINE 盘点页在仿 LINE（inset）宿主里：把格子滚到底后 300ms 内就点（页面还在滚），第一格（参考成本，数字键盘）停在键盘工具条下沿（346–388，可见区 378）；
  停 1 秒再点则三遍都过。试过「窗口高度变了也重摆」没有帮助、已撤回。这一点留给用户 iPhone 实测。
- 弹窗下部日期格在安卓上的根因比 11-01 记的多一层：键盘弹出、页面变矮时 Chrome 自己先把弹窗平滑滚一段露出这一格，
  这次滚动在共用模块动手之前，滚动事件连着发一百多毫秒；只在模块自己滚的那一下拿掉标记不够（真机仍收起，已用事件记录核实）。
  最终做法：弹窗里的日期 / 时间格从聚焦起拿掉类型标记，弹窗滚动停下（150ms 无新滚动、最多 1 秒）或离开这一格时放回，再按可见区重选日历上 / 下；
  1.2 秒内没弹键盘也放回。只动这一格自己的属性，Frappe 别处只在表格按键时读它。单测「浏览器先滚」一条在上一版上失败、新版通过。
  三星真机：日历下沿 374、可见区 354，最下一行「今天」被键盘挡 20px——日历 280 高，格子下方只剩 271，空间本身不够；上方也放不下，照 Frappe 规则放下面。

最终（代码提交 e4fe87a7，DMS 前端包重建后）：
- 单测 `tests/test-desk-keyboard.cjs` 12/12；iOS 三种宿主 × 11 个入口（含弹窗下部日期格）全部通过（DMS 见上）。
- 安卓：受影响流程 `run.py --changed` 4/4（`erp.keyboard.dialogs` 30 项含新加的 iOS 内嵌与调拨备注）；组件回归 10 条 19/19（物料、采购、收银首单、出入库扫码、销售扫码、团队、供应商 / 客户、盘点扫码、Cowork 复核、DMS 取景页）。
- 三星真机（本机实验栈、手机 Chrome、真触摸）：调拨备注 / 数量、弹窗下部日期格、物料新建最下格、DMS 编辑页最下格都通过。
- 共用件 `phone-keyboard` 39 个入口：35 个自动页面冒烟全部通过（先备齐 old 数据；没备时 8 个页显示无权限，换回 main 上的模块同样 8 个，属环境）；
  4 个手动入口（ERP 弹窗、Cowork 复核、DMS 编辑页、LINE 盘点页）由上表的 iOS 模拟器、三星真机与流程覆盖，均通过。
- 没测到：真 iPhone 与真 LINE（模拟器 + 仿 LINE 宿主代替；部署后请用户 iPhone 在 LINE 里点调拨备注）；iPad。

另记（与键盘无关）：盘点连续录入点「实际仓位」弹「未找到仓库」（「实际仓库」被设成仓库名称）已由盘点仓位窗口修好并合入 main（e9a11f90 / f27c228c）。
