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

## 上线后补验（本任务不部署）

- 主会话：下一次部署后在生产上再点一遍 POS 和扫码，作为镜像层补验（本次用本机实验栈 + 真机代替测试机 live-off，主会话 11-01 同意）。
- 真机进生产测试租户网页：从 testerp / testcowork 的 LINE 官方号富菜单进（走 LINE 登录），不在手机 Chrome 输密码（用户指点，主会话 11-01）。
  顺带补验 LINE 内置浏览器里的 DMS 编辑页、LINE 盘点页、Desk 弹窗最下面几格。
