# 手机键盘盖住正在填的格（三个产品统一处理）

- 任务分支：`pearnly/mobile-keyboard-overlap-211914`，工作树 `.claude/worktrees/mobile-keyboard-overlap-211914`，基线 `origin/main` 2d484b99。
- 来源：用户 11-01「我在手机点击某段字段时，弹起来的输入框会把这个字段盖住……这么多字段，怎么处理这类情况呢」；方案由主会话提出、用户同意。

## 需求影响评估

| 栏 | 结论 |
|---|---|
| 0 需求 | ERP、Cowork 的 Desk 页面与全部弹窗，DMS 的 LINE 编辑页、LINE 盘点页：点格子后弹出的键盘不能盖住这一格。不在范围：字段布局（11-01 刚统一过）、说明文案、CC 对话页（`frontend/assistant` 自有 `lib/viewport.ts`，不是这三个产品）。 |
| 1 同类复用 | 现有零散写法只有一处：DMS 编辑页 `EditorPage.vue` 的 `keepAboveKeyboard`（fce34c27，保存被拒时把出错栏摆到贴底操作条上方）。新建一个共用模块，删掉这处。 |
| 2 Frappe 原生 | 锁定版本 Frappe 的 `www/desk.html` 写死 viewport（`maximum-scale=1.0 … user-scalable=no, minimal-ui`），没有钩子改；弹窗是 Bootstrap `.modal`（整屏固定、`overflow-y:auto`），手机上没有跟键盘的处理。不改框架文件：viewport 由共用脚本运行时补 `interactive-widget`，弹窗只改外框的 `top / height`，弹窗仍按官方方式自己滚。 |
| 3 按类通用 | 一类：所有会弹键盘的输入格（input 文本类、textarea、contenteditable），不分页面。Desk 由 `pearnly_core` 的 `app_include_js` 统一加载（ERP、Cowork、DMS、控制面同一份）；LINE 盘点页挂同一份；LINE 票据编辑器（Vue）打进自己的包。 |
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
- 带建议列表 / 日历弹层的格（Frappe Link 的 awesomplete、`role=combobox`、`aria-autocomplete`、`list`、Frappe 日期 / 日期时间 / 时间控件）
  摆到可见区上部三分之一，下面留给弹层（真机发现：仓库 Link、单据日期在可见区上半部不动时，下拉 / 日历往下展开被键盘挡得只剩一行；
  旧版同样如此，这里一并按类处理。改后仓库下拉 5 项 + 「新建仓库」、日历整月都在键盘上方）。

## 覆盖矩阵（页面类型 × 怎么接 × 验证）

| 页面类型 | 例子 | 怎么接上 | 流程（无头 Chrome） | 三星真机 |
|---|---|---|---|---|
| Desk 页面（ERP / Cowork / DMS / 控制面） | 入库新建、Cowork 录入复核、DMS 主页 | `pearnly_core` `app_include_js` | cowork.keyboard.intake（安卓式） | 见下表 |
| Desk 弹窗（p.dialog / frappe.ui.Dialog / FieldGroup，Bootstrap `.modal`） | 物料新建、团队添加用户 | 同上 + `product.css` 弹窗外框规则 | erp.keyboard.dialogs（iOS 式 + 安卓式） | 见下表 |
| 网站页（`templates/web.html`） | 改密码 `pearnly-password`、控制面发号页 `pearnly-issue` | `pearnly_core` `web_include_js` | 无（静态检查核接入） | 见下表 |
| www 独立页 | LINE 手机盘点 `stocktake.html` | 页面 `versioned_asset` 挂 | erp.keyboard.line-stocktake（安卓式） | 见下表 |
| 前端单页（Frappe UI，LIFF） | DMS LINE 订车编辑页、取景页、账号页（`pearnly-app`） | `frontend/src/main.ts` import 同一文件打进包；`dms-booking.html` / `pearnly-app.html` 交给这个包 | dms.keyboard.editor（安卓式） | 见下表 |
| CC 对话页 | `assistant.html`、`frontend/assistant` | **不接**：不在三个产品范围；自有 `lib/viewport.ts` 按可见区排版、meta 已带 `interactive-widget`，再挂会重复摆位 | — | — |
| CC 智能体入口登录页 | 控制面 `ai.html`（fatcc.com） | **不接**：CC 品牌页，只有首屏居中两格 | — | — |
| Keycloak 登录 / 改密页 | `infrastructure/keycloak/themes/*` | **不接**：登录页按规矩不改（记忆 login-pages-frozen） | — | — |
| 打印格式、邮件、Python 拼的中转页 | `printing/templates`、`portal.py` | 没有文字输入格，不需要 | — | — |

静态检查 `scripts/check-keyboard-coverage.py`（进 Development checks）：扫进版本库的全部整页文件（apps 下 html、Python 拼的整页、frontend 各单页入口），
有文字输入格或挂单页应用的必须接上，`hooks.py` 两处必须挂；按设计不接的登记在 `scripts/keyboard-coverage-exempt.json`（只许减少，比对 merge-base）。
反向验证：临时加一个带输入框的 www 页，检查失败并点名；删掉后通过。

## 删掉的零散写法

- `frontend/src/pages/dms/EditorPage.vue` 的 `keepAboveKeyboard`（fce34c27）：出错跳格后聚焦输入框，摆位交给共用模块（贴底操作条由「命中的 sticky 条」通用处理）。
- 全仓再查 `scrollIntoView / scrollBy / visualViewport / innerHeight`：其余都是分区导航、看板高度、下拉菜单定位，与键盘无关，不动。

## 验证

（下方逐项填实际结果）
