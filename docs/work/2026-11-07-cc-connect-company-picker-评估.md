# 需求影响评估：CC 连接里加 MR.ERP「连接」+ 对话里点公司弹窗选择 + 删掉左侧「公司」

> **11-07 范围收窄（用户补充，主会话转来，以本段为准）**：「或者为了减少改动，现在我们的 exp 已经具备了，添加多个文件帐套那 exp 就不改了，
> 加个 mr.erp 的连接按钮即可呗」。所以第 1 件事改成：**Express 那边（CC 设置里加多个数据文件夹、Express 登录）保持现状不动**，不重做成卡片；
> 只在 CC 的连接设置里**加一个 MR.ERP「连接」按钮**（网址 + 在 CC 桌面版原生小窗授权账号密码，复用现有 `MrErpLogin` / `mrerp_login`，不另做一份）。
> 连上后 MR.ERP 账套进账套目录，出现在公司选择弹窗的 MR.ERP 标签下。第 2 件（删左侧公司）、第 3 件（选公司改弹窗、选即建）不变。
> 下文按收窄后的范围改过；MR.ERP 按钮的做法、与 Desk「推送目标」是不是同一条连接、ERP 站点怎么办见 **§1.4**。

- 状态：**评估已审定**（主会话 `local_3d36d798`，11-07，定案见 §12.1），未改代码；**等主会话通知开工**（按行选物料 Cowork / ERP、识别完自动继续合入 `main` 之后）。分支 `pearnly/cc-connect-company-picker-3e453c`，
  基线 `1fe911cc4`（= 写评估时的 `origin/main`）。
- 勘察方式：只读读码（服务端公司数据、前端公司页与选择器、CC 桌面版连接三路），没动测试机、生产、实验栈。
  文中「文件:行号」以基线为准。

## 0. 需求原文与理解

- 原话（用户 11-07，中文，主会话转来）：「我现在还是觉得左侧公司有些冗余了，因为我在连接里已经有了这个帐套，所有帐套也到 cc 了，这时候我只想要
  在对话框现有的选择器里选择即可，分清楚 mr.erp 还是 exp 即可。连接那个界面我希望整理下，还是按 mr.erp 和 exp 卡片，点击卡片上的连接，然后
  exp 这边就是添加文件夹（这里也可以输入账号密码授权给代录），mr.erp 是输入网址和账号密码授权连接，这里设置好后，代录和直写对话框选择公司里
  就会有对应的 erp 公司选择，但是对话框现在的选择公司是下拉，我希望做成点击公司后，是添加公司的那个弹窗界面，分 mr.erp 和 exp 的公司，选择
  公司后就在这个对话框开始工作」。主会话复述后用户确认「是的，就是这样」。
- 我的理解（三件事，Cowork、ERP 两个产品，直写、代录两种模式一样）：
  1. **CC 连接设置里加 MR.ERP「连接」按钮**（11-07 收窄后）：填网址，账号密码在 CC 桌面版原生小窗里授权；授权后读一次 MR.ERP 公司目录。
     Express 的数据文件夹、Express 登录**照现状不动**。（收窄前是「整理成 MR.ERP、Express 两张卡片」，已作废。）
  2. **对话框的公司选择器改成弹窗**：点「公司」弹出今天「添加公司」那个窗口（原生对话框、搜索、MR.ERP / Express 标签），列出连接里读到的
     全部账套公司；选哪家就在这段新对话里开始工作。还没用过的公司在选中时顺手建好，不再有单独的「添加公司」一步。
  3. **删掉左侧「公司」入口**（公司列表页、公司页）；公司页上独有的功能先搬走（第 1.3 节）。
- 不在本需求范围（防止顺手扩展）：
  - 不改公司身份算法（`tax:` / `name:express:` / `mrerp:`）、不合并同税号 Express 与 MR.ERP（ADR-020「下一批」）、不做按公司分权限。
  - 不改 Cowork Desk 的「推送目标」（`public/desk/targets.js`）与 ERP Desk 的公司页（`/desk/pearnly-company`）。
  - 不开 ERP 的 MR.ERP 账本（`MRERP_LEDGER_ENABLED = False`）、不开 MR.ERP 代录（`MRERP_OPERATOR_ENABLED = False`），开关照旧。
  - 已有对话不能换公司（用户 10-17「移到」已删）不变；侧栏按公司分组、组头「＋」不变。
  - **Express 连接相关一律不动**：设置「此电脑 → 连接」的数据文件夹、位置列表、Express 登录入口（代录开始前卡 / 续录卡）、电脑池。
  - 不加新的本机能力（CC 桌面版不发新版，见第 4 节）。

## 1. 现有同类与复用

### 1.1 今天怎么加连接、怎么加公司

| | Cowork | ERP |
|---|---|---|
| Express 连接 | 在装了 Express 的电脑上装 CC、用本账号登录，自动建连接（`companion.api.register`，10-10 起不用配对码）。数据文件夹在 CC「设置 → 此电脑 → 连接」添加（桥 `add_place`，原生选择框，`settings.tsx:378-595`；位置存 `%APPDATA%/Pearnly/companion.json` 的 global 段，**整台电脑一份，Cowork 与 ERP 成员共用**，`express_places.py:7`） | 同一台电脑同一份位置；ERP 成员经共用机器通道 `pearnly_core.cc_desktop` 心跳报账套（ADR-025） |
| Express 登录（代录） | CC 原生小窗（`login_dialog.py`），存本机凭据管理器 `Pearnly CC/Express/default`，**整台电脑一份**，服务端不知道有没有设（`credentials.py:24-61`、`host.py:423`）；入口只在代录「开始前」卡与续录卡（`operator-start.tsx:145-149`、`operator-resume.tsx:42-43`，桥 `operator_authorize`），设置页没有 | 同左 |
| MR.ERP 连接 | 网址在网页填（Cowork Desk 录入页 `targets.js:92-130`，或 CC 定时任务里的连接弹窗 `scheduled-tasks.tsx:1277-1340`，`connections.api.configure`）；账号密码只在 CC 桌面版原生小窗授权（`MrErpLogin` → 桥 `mrerp_authorize`，ADR-026 第 7 条 / 评估 §14.4）；管理员授权写站点（直写、目录采集用），使用名单成员只存本机（`connections/mrerp_login.py:47`） | `ERP MRERP Connection`，网址只由 ERP 管理员在服务端设（`mrerp/connection.py:1-10`），整块挂在 `MRERP_LEDGER_ENABLED = False` 后面，**对用户不开放** |
| 连接页（CC） | 浏览器里「设置 → 连接」只读：池里的电脑、在线、移除、「全所可用」（`settings.tsx:128-149, 678-754`）；桌面版多「此电脑 → 连接」：站点行、Express 数据文件夹、位置列表 | 浏览器里没有「连接」一栏；桌面版同 Cowork 的「此电脑」 |
| 账套目录 | 每条连接最近一次快照（`Cowork ERP Snapshot`，`connections/service.py:251 account_set_facts`）；哪台电脑够得到记 `Cowork Express Reach`（带 `device`） | `Pearnly CC Book`（每行带 `device`，`cc_desktop/books.py:19`）；`express/companies.py:79 catalog()` 按「税号 + 分店」合并、不分电脑 |
| 加公司 | CC 侧栏「公司」列表 / 新对话公司下拉末尾「添加公司…」→ `add-company.tsx` 弹窗：`companies.api.candidates`（目录里按身份合并，≤200 条）→ `create(connection, account_set_key, request_key)` 建 `Cowork Company`（要「录入」权限，`companies/service.py:53-58, 298-331`） | **心跳换了账套就自动建公司**（`books_changed` → `express/companies.py:28 ensure / :63 build`，13 位税号才建，停用的不重建）；CC 里同一个弹窗（11-04 `b5acb0c8`），`masters/cc_companies.py` 的 `candidates` / `create`，只给 `company.manage`（`cc_service.py:191`，`cc_projects.py:23-25` 没权的人不声明 `create`）；不接账本的纯 ERP 公司在 ERP Desk 公司页建 |

### 1.2 公司数据存哪

- 共用层**不建公司表**：会话 `Pearnly Assistant Session` 上 `project`（不透明编号，空 = 「最近」）、`period`、`project_scope`、`project_noted`
  （ADR-020）；哪些编号有效、谁能用、给 CC 的说明由产品 provider 声明（hooks `pearnly_assistant_projects`；`pearnly_core/assistant/projects.py`）。
  `projects.listing` 下发 `api`（产品公司接口前缀）与 `sections`（产品有哪些分区）。
- Cowork：`Cowork Company`（`title`、`legal_name`、`tax_id`、`branch`、`identity` 唯一、`adapter`、`seed_connection`、`seed_account_set`、
  `request_key` 唯一…），全所一家一份，**不做公司隔离**（ADR-020），删 = 真删行、对话回「最近」。知识库 `Cowork Company Note`，文件夹 `Cowork Company Folder`。
- ERP：`ERP Company`（`CO-#####`，`is_active` 停用、`express_enabled`、`express_book`、`mrerp_connection`…），公司间靠 User Permission 隔离；
  知识库 `ERP Company Note`。改名、删除、停用只在 ERP Desk 公司页。
- 前端：`lib/companies.ts`（`listCompanies` / `listCandidates` / `createCompany` / `renameCompany` / `deleteCompany` / `refreshCatalogs` /
  `useCompanyNav` …，接口前缀取声明 `companiesApi()`）；`lib/managed-agents/projects.ts`（侧栏分组、草稿 `draft.project`）。
- 开对话：`pearnly_core.assistant.api.create(project, period, scope)`；`move` 只能改月份、分店，换公司被拒（`sessions.py:528-538`）。

### 1.3 公司页上的功能逐项去处（11-05 主会话的搬迁表，本次逐项核对后写定）

| # | 公司页上的功能（实现） | 今天对话页右侧栏有没有 | 搬到哪 |
|---|---|---|---|
| 1 | 改名（标题就地改 / 「···」，声明 `rename`；Cowork 有，ERP 没有）`company-page.tsx:148-171, 236-248` | 没有 | 侧栏公司**组头「···」**（今天只有「分组和排序」，`sidebar-group.tsx:168-246`）+ 右侧栏**公司卡「···」**；用原生菜单项 + 就地改名（同侧栏对话改名） |
| 2 | 删除公司（不确认）`company-page.tsx:183-194, 261-269` | 没有 | 同上两个「···」，**先确认一次**（§12.1 #8，同删对话的确认弹窗）；删完侧栏这组消失、对话回「最近」（服务端已是这样） |
| 3 | 刷新账套（声明 `catalog`）`company-page.tsx:173-182` | 没有（只在添加公司弹窗列表为空时有） | 设置 → 连接的 **MR.ERP 一行**授权后自动刷新一次（§1.4；Express 照旧「重新查找」）+ **选择弹窗**头部刷新按钮（今天空列表时才有，改成一直在）；公司卡「···」也放一项（主会话表） |
| 4 | 在 ERP 中打开（声明 `desk`，`openSitePage`）`company-page.tsx:136, 255-260` | 没有 | 公司卡「···」与组头「···」 |
| 5 | 全部账套（`CompanyPageCard`，`session-panel.tsx:302-326`） | 公司卡只列本月、本分店覆盖的那份（`setsForScope`） | 公司卡加「全部账套」展开（同卡内折叠，不新开页） |
| 6 | 知识库空时「新增」与「让 CC 起草」（`keepEmpty`，`company-cards.tsx:280-359`） | 有卡，但一条都没有时整卡不显示，**对话里加不了第一条** | 右侧栏知识库卡**空时也显示「＋」**；「让 CC 起草」（新对话预填）在对话里语义不顺，建议不搬，直接在当前对话里说就行（§12 #5） |
| 7 | 月结台账换月（月份胶囊，`month-ledger.tsx:324-341`） | 「本月」卡跟对话月份、不能换、没内容不显示 | 「本月」卡加只读的月份胶囊：只换卡里看的月份、不 `move` 对话（§12 #4） |
| 8 | 定时任务卡（空时也有「＋」，`company-cards.tsx:571-619`） | 有，但没任务时整卡不显示、没有「＋」 | 右侧栏定时任务卡空时也显示「＋」（只在声明 `tasks` 的 Cowork）；**定时任务提醒点开**改落到定时任务页按这家筛（`useCompanyNav.taskCompany` 已有，`scheduled-tasks.tsx:338-409`） |
| 9 | 单据文件夹（只在 CC 桌面版选）`company-cards.tsx:427-522` | 有，但 `hideEmpty`：**一个都没绑时不显示，对话里绑不了第一个** | 右侧栏文件夹卡在 CC 桌面版里空时也显示「选择文件夹」；浏览器里空时照旧不显示。另改 CC 说明文案 `companies/context.py:162`（给模型的话，写着 "on the company page"）改成右侧栏 |
| 10 | 待处理全部月份（`company-cards.tsx:234-245`） | 对话里列本对话月份以外的；本月的在「需要处理」框（11-04 并进对话） | 不用搬：已在对话（侧栏月份占位行 + 「需要处理」框 + 右侧栏待处理卡）。`?view=pending` 的落点见第 6 节 |
| 11 | 记住的选择、申报草稿 | 两边一样 | 不用搬 |
| 12 | 公司页自己的输入框（公司固定 + 月份 / 分店） | — | 就是「新对话 + 弹窗选好公司」，删 |
| 13 | 这家我的对话列表（分页）`company-page.tsx:425-554` | 侧栏公司分组里有（组内「显示更多」翻页，`thread-list.tsx:384-407`） | 删，靠侧栏分组 |
| 14 | 公司列表页（全部公司卡片、`companies.tsx`） | — | 选择弹窗（全部公司 + 未用过的目录公司，可搜索）；侧栏组头照旧 |
| 15 | 一家都没有时选择器只给「添加」 | 今天选择器整个不显示（`projects.tsx:455-459`），只能去公司页添加 | 选择器**有连接 / 有目录就显示**；目录也空时弹窗里给去连接页的入口（§12 #7） |

### 1.4 MR.ERP「连接」按钮（11-07 收窄后的第 1 件）

**今天的事实（核对过）**

- Cowork 的 MR.ERP 连接是 `Cowork ERP Connection` 里 `adapter = mrerp` 的一行。每种 ERP 只认**主连接**：本归属下、不挂客户、启用、代数为 0 的
  最早那条（`connections/service.py:290-310 main_connections / main_of`）。电脑登记、网页录入、LINE、CC 都按它认（ADR-021）。
- 现在有两处界面改它：Cowork Desk 录入页的「推送目标」连接弹窗（`targets.js:92-130`，只填网址 + 显示授权状态 + 灰的「在 CC 桌面版中授权」），
  以及 CC 定时任务里的连接弹窗（`scheduled-tasks.tsx:1277-1340 MrerpConnect`，网址 + `MrErpLogin`）。两处都调 `connections.api.configure`：
  已有连接就按版本改那一条，没有才建一条。**所以两处改的就是同一条主连接，不会出现两条记录**。
- 改网址 / 建连接只给管理员（`configure` 里 `access.require_actor(management=True)`）。授权（`mrerp_login.store`）：管理员写进站点
  （Password 字段，直写和目录采集用）；连接使用名单上的成员只存本机（给自己代录用），站点上什么都不写。
- **账套目录由服务端登录 MR.ERP 读**（`pearnly_core/mrerp/collect.py`，Cowork `connections/mrerp_collector.py`），用的是站点存的那份密码。
  所以**只有管理员授权过，MR.ERP 公司才进得了目录**；成员在本机授权不影响目录。
- 授权成功后**不会自动刷新目录**：`store` 只写凭据，没排读取。要等别处点「刷新账套」（`connections.api.request_refresh`，能用这条连接的人都能点），
  或推送时顺带读。
- ERP：`ERP MRERP Connection` 是另一张表、另一套接口（`pearnly_erp_portal.mrerp.api`），整块挂在 `MRERP_LEDGER_ENABLED = False` 后面。
  hooks 只在开关打开时才登记授权钩子（`hooks.py:374`），关着时一律当作没有这个功能。

**做法（建议）**

1. 位置：CC「设置 → 连接」最上面加一行「MR.ERP」：左边是状态，用现有词条显示「未连接」或「已授权：用户名 · 电脑 · 时间」；右边是「连接」按钮
   （已连接时同一个按钮，点开改网址、重新授权）。浏览器里（Cowork 的「连接」栏）和 CC 桌面版（「此电脑 → 连接」）各有这一行，下面的
   Express 部分一行不改。
2. 弹窗**不另做**：把 `scheduled-tasks.tsx` 里的 `MrerpConnect` 原样抽成 `components/mrerp-connect.tsx`，设置页和定时任务共用这一个。
   里面照旧是网址 + `MrErpLogin`：密码只在 CC 桌面版原生小窗填，浏览器和手机里按钮是灰的。
3. 授权成功、或改了网址存下以后，页面调一次现有的 `refreshCatalogs`（`records` + `request_refresh`），让服务端读一遍 MR.ERP 公司目录。
   这样关掉弹窗、点「公司」，弹窗的 MR.ERP 标签下就有这些公司。服务端不改。
4. 权限照今天：只有管理员能填网址、做站点授权；成员这一行只读（看状态），在 CC 桌面版里可以点授权，但只存本机。哪些按钮可用由服务端
   视图（`records` 的 `management` / `credential`）决定，前端不自己判断。
5. 不提供「断开 / 删除 MR.ERP 连接」：产品现在本来就不允许删连接（11-02 测试站清理时记录过），本批不加。

**Desk「推送目标」里的 MR.ERP 连接要不要保留**

- 建议**保留**，不改它。它和 CC 里的按钮改的是同一条主连接、同一份授权状态，两边显示一致，不会分叉。
- 保留的理由：
  - Desk 录入页是用户在网页里选「推送目标」的那一步，「仅识别」和 Express 的下载 CC 桌面版也都在那里；
  - 按行选物料的两个分支正在改 `targets.js`，现在去动它会冲突；
  - 它本来就不收密码（ADR-026 第 7 条），没有第二份要维护的密码逻辑。
- 剩下的「两份界面」只是外观：Desk 是 Frappe 原生弹窗，CC 是 CC 的组件，背后是同一个接口。以后要并成一个入口另起需求（§12 #12）。

**ERP 站点怎么处理**

- 建议本批在 ERP 站点**不出这一行**：浏览器（ERP 本来就没有「连接」栏）和 CC 桌面版「此电脑 → 连接」里都不出。
- 由服务端声明决定，不靠前端藏：产品在会话列表声明里给一个 `connect` 字段，例如 `{mrerp: "<连接接口前缀>"}`（与 `api` / `sections` 同一处下发，
  `projects.listing`）。
  - Cowork 声明 `pearnly_cowork.connections.api.`；
  - ERP 只在 `mrerp.enabled()` 为真时声明 `pearnly_erp_portal.mrerp.api.`，开关关着就不声明，前端就不出这一行；
  - 前端不按产品名分支（`tests/test_cc_session_panel.py` 守）。
- 将来 ERP 开 MR.ERP 账本时，开关一开这一行就出现。前提是 ERP 的连接接口要补齐与 Cowork `records` / `configure` / `request_refresh` 同形的方法，
  这部分归「ERP 开 MR.ERP」那个任务，不在本批。

## 2. Frappe 原生能力

- CC 对话页（`frontend/assistant`）是 ADR-005/007 登记的 React 例外，遵循 ccs（`docs/development/ccs.md`）：弹窗沿用现有 Radix `Dialog` +
  `Tabs`（`add-company.tsx` 已在用，`.cc-dialog` / `.cc-tabs` / `.cc-list-row`），菜单沿用 `ui/menu.tsx` 的 DropdownMenu，卡片沿用设置页
  `cc-settings-row` / `Row` / `Heading` 与右侧栏 `PanelCard`。**不新造组件、不写死尺寸**，样式只走 `globals.css`。
- 服务端：公司仍是产品自己的 DocType（`Cowork Company` / `ERP Company`），建公司走现有 `create`（幂等 `request_key`）；列表搜索、分页在服务端
  （§4.2）。Desk 侧不涉及。
- 结论：全部复用现有组件与接口，只加一个产品接口方法（选择列表，§4.2）；不需要自定义样式。

## 3. 按类通用

- 这一类：**「CC 里所有选公司的地方」**。逐个列：
  1. 新对话下巴「公司 ▾」（电脑，`DraftChin`，`projects.tsx:447-486`）→ 改弹窗；
  2. 手机新对话问候下面一行（`WelcomeFiling`，同一个 `DraftChin side="bottom"`）→ 同一个弹窗（手机盖满屏，`--cc-add-company-share` 已有）；
  3. 代录新建页（同一个下巴）→ 同上；
  4. 定时任务「＋」先选公司（`openAddCompany(…, co.chooseCompany)`，`scheduled-tasks.tsx:341`）→ 改用同一个选择弹窗（今天就是它，只是改成合并列表）；
  5. 11-02 记录里的「实操选公司」（`operator/live-source.ts`）在 11-04 统一对话框后已删，代录选公司就是第 3 条，不另算；
  6. 已有对话下巴的公司名（`SessionChin`，点了打开公司卡）、对话顶栏公司名、手机胶囊「…」里的公司 → **不变**（已有对话不能换公司）。
- 另一类：**「所有连接设置入口」**：CC 设置页（浏览器 / 桌面版）、CC 定时任务里的连接弹窗（`scheduled-tasks.tsx:1277-1380`，已是 MR.ERP / Express
  两张卡 + 连接弹窗）、Cowork Desk 录入页 `targets.js`。本需求只在 CC 设置页加 MR.ERP 一行（§1.4）；定时任务里的 MR.ERP 连接弹窗抽成**同一个组件**（`components/mrerp-connect.tsx`，两处共用，不留两份）；Desk `targets.js` 不动（同一条主连接，另一窗口正在改它，见第 13 节）。Express 部分不动。
- DMS 没有公司分组（`kind = None`），全部不出现、不受影响。

## 4. 上下游数据

### 4.1 改后的数据流

```
连接设置                            账套目录（服务端）                     选择弹窗                               对话
Express：添加文件夹（桥 add_place，**不变**） → 心跳报账套 → Cowork 快照 / ERP Pearnly CC Book
MR.ERP：设置 → 连接「连接」：网址（configure）+ CC 小窗授权 → 页面调 request_refresh → 服务端登录采集 → 连接快照（Cowork；ERP 开关关着不出）
                                     ↓
                     产品 choices(search, adapter, start)：已建公司 + 目录里还没建的，按身份合并、分 MR.ERP / Express
                                     ↓
                     点一行：已建 → 编号；没建 → create（幂等）→ 编号
                                     ↓
                     setDraft(project, 上个月) → 发第一句 = api.create(project, period, scope) → 侧栏这家的组
```

### 4.2 服务端要加什么

- 今天弹窗用 `candidates`（只看目录，≤200 条、内存里筛；已建的标 `company`），下拉用 `companies`（只看已建、分页 50）。新选择弹窗要的是
  两者合并：**不在前端拼两份**（「列表先用服务端分页」规矩），各产品加一个方法 `choices(search, adapter, start, limit)`（Cowork
  `companies.api.choices`，ERP `masters.cc_companies.choices`；共用层 `projects.listing` 照旧下发前缀，前端不按产品名分支）：
  - 行 = `{identity, company|None, title, legal_name, tax_id, branch, years, adapter, connection, account_set_key}`，与 `Candidate` 同形（多 `company`）；
  - 顺序：我有对话的按最近活动在前，其余按名称（同 `listing`）；`adapter` 过滤在服务端（标签切换重取）；每页 50、可「显示更多」；
  - 已建但目录里已经没有账套的公司（连接删了、账套搬走）**照样列**（它下面有对话）；ERP 不接账本的纯 ERP 公司见 §12 #3。
  - 实现复用 `service.candidates` 的合并与 `service.listing` 的排序，不另写身份算法。`scripts/check-unbounded-queries.py` 照查（目录行来自快照、已有上限）。
- 选中即建：复用现有 `create`，**不改**入参与幂等；ERP 的 `create` 今天只给 `company.manage`，按用户口径「能用这个连接的人都能选」要放宽（§12 #1）。
- 改名、删除、刷新、在 ERP 中打开：现有接口不变，只是入口挪位置。
- `companies/context.py:162` 给 CC 的说明改一句（文件夹在右侧栏选）。

### 4.3 Express / MR.ERP / CC 桌面版

- Express 映射、写账规则、载荷：**不涉及**（只改「从哪里选公司」，公司身份与账套归属算法不变）。
- CC 桌面版：**不发新版**。连接页是服务端给的网页，要用的桥方法都已在 1.12.15：`add_place` / `remove_place` / `rescan` / `detect`、
  `operator_state`（读 Express 登录有没有设）/ `operator_authorize`（设 Express 登录）/ `operator_forget`、`mrerp_authorize`；载荷版本不变。
  `tests/test_companion_contract.py::test_every_method_the_page_calls_is_in_the_bridge` 守住「页面只调桥里有的方法」。
  要发新版的情况（本次不做）：Express 登录按账套 / 按位置分开、浏览器里显示「Express 已授权」（要新心跳字段）、原生小窗里填网址。
- Cowork / DMS：Cowork 公司主档、知识库、文件夹、记住的选择、待处理、月结的数据与接口不变；DMS 不涉及。ERP 自动建公司（`books_changed`）不变。

## 5. 隔离与权限（全部服务端判）

| 动作 | Cowork 今天 | ERP 今天 | 改后（建议） |
|---|---|---|---|
| 看 MR.ERP 一行 | —（今天只在定时任务连接弹窗、Desk 录入页） | — | Cowork 浏览器「连接」栏与桌面版「此电脑 → 连接」都出；ERP 在 `MRERP_LEDGER_ENABLED` 关着时**不出**（服务端声明 `connect` 里不给，不靠前端藏，§1.4） |
| 加 / 删 Express 文件夹 | 只在那台电脑的 CC 桌面版里（原生选择框，网页不传路径） | 同 | **不变**（收窄后不动） |
| 设 Express 登录 | 本机，代录开始前卡 / 续录卡 | 同 | **不变** |
| 建 / 改 MR.ERP 网址 | 管理员（`configure` 要 `management=True`） | 管理员（开关关着） | 不变 |
| MR.ERP 授权 | 管理员写站点；使用名单成员只存本机 | 管理员（开关关着） | 不变；**只有管理员的站点授权会让 MR.ERP 公司进目录**（目录由服务端用站点密码读），成员本机授权只给自己代录 |
| 在弹窗里选已建公司 | 录入 / 识别读权限 | `company.read` + 本人可见公司 | 不变 |
| 选中**没建过**的公司（顺手建） | 录入权限 + 本人能用这条连接（`usable_connections`） | `company.manage` | Cowork 不变；ERP 放宽为「开单权限 + 看得到这份账套」（§12 #1） |
| 改名 / 删除公司 | 录入权限（Cowork 主档全所共用） | 不在 CC 里（ERP Desk） | 主会话建议「只给管理员」：Cowork 现在是录入权限就能改删，要收紧需服务端改 `require_write` → 管理员（§12 #2） |

- 跨公司串数据：ERP 选择列表只出本人可见公司（`allowed_companies`）+ 本人看得到的账套；Cowork 无公司隔离（ADR-020，不变）。
  事务所电脑同时连 Cowork 和客户 ERP 时，Express 位置整台电脑一份、两边都会报（10-23 的 ERP 勾选已在 `a78bed97` 去掉）；ERP 会按报来的账套
  自动建公司——**这是今天就有的行为**，本需求不加剧，但选择弹窗会把它们显眼地列出来（§12 #9 只记录不处理）。

## 6. 入口：电脑、手机、浏览器、CC 桌面版、LINE、深链接

| 入口 | 改后 |
|---|---|
| 电脑网页 / CC 桌面版窗口：新对话下巴「公司」 | 点了弹选择窗；选了下巴变成「公司名 + 月份 + 分店」，与今天选完一样 |
| 手机：新对话问候下面一行 | 同一弹窗（手机盖满屏、搜索框不自动弹键盘，沿用 `add-company` 已有的手机规则） |
| 代录新建页 | 同一个下巴、同一弹窗；选了进这家新建任务（今天 `useOpenCompany` 的代录分支保留） |
| 侧栏 | 删「公司」一行（`CompanyNav`，`assistant.tsx:531-553`，电脑与手机抽屉同一个）；公司分组、组头「＋」、月份占位行不变；组头「···」加改名 / 删除 / 在 ERP 中打开 |
| 设置 → 连接 | 最上面加「MR.ERP」一行（状态 +「连接」，§1.4）；下面 Express 数据文件夹、位置、站点行、电脑池**一行不改** |
| 深链接 `?view=companies` | 落到新对话并打开选择弹窗 |
| 深链接 `?company=<编号>` | 来源只有一个：定时任务提醒没挂对话时（`folder_intake/notify.py:30-40` → `contracts.deep_link`，手机推送、电脑通知、托盘 `tomo_links.py:133-182` 都用它，**已发出的旧提醒也是这个地址**）。改为前端把它落到**定时任务页按这家筛**（与提醒内容一致）。服务端、CC 桌面版的地址格式**不改**，旧推送照样能点（§12 #10） |
| 深链接 `?view=pending` | 今天多家时落到公司列表 / 公司页（`nav.ts:326-359`）。改为：一家 → 这家这个月的对话（有就开，没有就开新对话 + 「需要处理」框，同侧栏占位行）；多家 → 对话页（侧栏占位行带提醒点） |
| 不认识的旧 `view` | 今天什么也不做停在对话页；删页面后 `companies` / `company` 走上面两条，不会落空 |
| LINE | 不涉及：LINE 代码里没有指向 `/assistant` 的链接（服务端勘察 grep 无结果） |
| 相机 | 不涉及 |
| Desk（Cowork 录入、ERP 公司页） | 不变 |

- 多台电脑（ADR-021）：Express 部分不变（文件夹各台电脑各自的、只在那台的 CC 里加删）；MR.ERP 是服务器上的一条连接，不分电脑，
  「已授权：用户名 · 电脑 · 时间」照旧写明是哪台电脑授权的。账套目录合并后不分电脑（Cowork 按连接、ERP 按税号 + 分店），选择弹窗不显示电脑。

## 7. 共用件与连带回归

`python3 scripts/check-impact.py --markdown --files <预计改动的 22 个文件>`（前端 16 个 + Cowork companies 3 个 + ERP cc_* 3 个）：

- 命中共用件：`cc-assistant-settings`（CC 设置页）、`cc-sidebar-list`（CC 侧栏列表）。
- 要实测的入口 10 个：`cc.settings.connection`（auto）、`cc.desktop.bridge`（manual，测试机 CC）、`cc.mrerp-login`（manual）、`cc.assistant`、
  `cc.operator.page`（offline）、`erp.cc.companies`、`cowork.companies`、`shared.login`、`shared.team.members`、`cowork.assistant`。
  交付时逐行填「通过 / 不通过 / 没测到（原因）」。
- 新调用方：选择弹窗的新组件（若从 `add-company.tsx` 改名）要补进 `scripts/impact/shared-parts.toml` 的 `cc-sidebar-list` 或新登记一个
  `cc-company-picker` 共用件（入口：新对话下巴、手机问候行、代录新建页、定时任务选公司），同一提交。
- 冒烟：`scripts/smoke/` 下没有公司相关；流程层面要改的见第 11 节。

## 8. 存储与成本

- 不新增数据、文件、外部调用、固定月费。选择列表读的是已有快照与表；「刷新」照旧发已有的按需读取（电脑读目录，零费用）。
- 选中即建只是多建一行 `Cowork Company` / `ERP Company`（今天点「添加公司」也是这一行）。

## 9. 删除、作废、追溯

- 删公司：服务端行为不变（Cowork 真删行、对话回「最近」、知识库 / 文件夹绑定一起删；ERP 不在 CC 里删）。入口从公司页挪到「···」。
- 选错了公司：新对话发第一句之前随时改；已发出的对话不能换公司（不变）。顺手建出来的公司没人用也就是一行主档，不自动清（与今天点了添加一样）。
- 删页面的代码：公司列表页 `companies.tsx`、公司页 `company-page.tsx` 整个文件删除（不留隐藏路由），`View` 去掉 `companies` / `company`
  （`nav.ts:35`），相关文案键随之删除（第 10 节）。

## 10. 文案与官方 UI

### 10.1 界面

- **连接设置**（设置 → 连接；Cowork 浏览器「连接」栏、桌面版「此电脑 → 连接」）：最上面加一行「MR.ERP」：状态（未连接 / 「已授权：用户名 · 电脑 · 时间」 / 未授权）
  +「连接」按钮 → 弹窗（从定时任务抽出的 `MrerpConnect`：网址（管理员可改）+「在 CC 桌面版中授权」，浏览器里灰）。存下或授权成功后刷新一次目录。
  下面 Express 部分一行不改。
- **选择弹窗**：标题「选择公司」（已有词条 `co.chooseCompany`），搜索、MR.ERP / Express 标签（只有一种不摆）、列表一行两行字（名称；法定名 · 税号 · 分店 · 年度），
  当前选中的打勾；头部刷新按钮；底部（或首行）「最近」= 不挂公司（§12 #11）。
- 右侧栏：公司卡「···」（改名 / 刷新账套 / 删除公司 / 在 ERP 中打开，按声明）、「全部账套」折叠；知识库 / 文件夹 / 定时任务空时也显示「＋」；「本月」卡月份胶囊。

### 10.2 文案清单（初稿，施工时 `check-copy.py --report` 出正式清单）

- **沿用已有、不改**：选择公司、搜索、没有结果、最近、MR.ERP、Express（产品名不翻译）、连接、未连接、
  已授权：{…} / 未授权 / 在 CC 桌面版中授权（`st.mrerpAuthorized` 等）、System URL（网址，服务端词条）、
  刷新账套、删除公司、重命名、在浏览器中打开（今天公司页「在 ERP 中打开」用的就是这个词条 `t.openInBrowser`）、电脑、暂停、断开连接。
- **新增**（照官方简短写，新增不用逐条问）：「全部账套」（公司卡展开）、「删除公司？」（删除确认标题，照删对话「删除对话？」的写法，按钮沿用「取消 / 删除」）；其余尽量用状态、图标、禁用态表达。
- **删除**（逐个 grep 过只在要删的地方用）：`co.addCompany`「添加公司」（公司列表、下拉末尾、弹窗标题）、`co.added`「已添加」（弹窗行尾）、
  `co.openCompany`「打开公司」（会话栏公司卡点名字进公司页）、`co.draftNotes`「让 CC 起草」（若 §12 #5 照建议不搬）、`co.conversations` /
  `co.noChats`（公司页对话列表）、`co.sortBy`（公司列表）。`co.companies`「公司」下巴按钮还在用，保留；`co.deleteCompany` / `co.refreshSets`
  挪到「···」继续用。施工时以 `check-copy --report` 第 3 节为准。
- **改已有文案**：预计**没有**。若施工中发现必须改（例如设置页「连接」下的分组标题），先把清单贴进本记录给用户确认，再登记 `copy-approved.tsv`。

## 11. 测试与验收

- 离线单测（改断言 / 新增）：
  - `tests/test_cowork_companies.py`、`tests/test_erp_cc_companies.py`：新 `choices`（合并、排序、标签过滤、分页、已建无账套照列、权限：ERP 员工
    能选能建、没开单权拒、看不到的公司不出、Cowork 只读角色不能建）。
  - `tests/test_assistant_ui.py`（深链接 2779 附近、侧栏顺序 3366-3370、设置页源码比对 1396-1404 / 2638-2646）、`tests/test_cc_session_panel.py`
    （卡片与菜单项按声明）、`tests/test_cc_mobile_overlays.py`、`tests/test_cc_ccs.py`（1419-1535）、`tests/test_cc_idle_motion.py`、
    `tests/test_cc_month_ledger.py`、`tests/test_cc_mobile_content.py`：删页面后的源码断言与新弹窗。
  - `tests/test_companion_contract.py`：页面调的桥方法仍都在桥里（不加新方法）。
- 流程（实验栈，本机约 30 分钟一轮）：
  - `scripts/flows/cc_lib.py:30-38 pickCompany` 今天按下拉的 `role=menuitem` 选公司，`cowork_operator_unknown`、`cowork_pending_operator`、
    `cc_operator` 等都靠它——改成在弹窗里选，**所有用它的流程一起跑**。
  - `scripts/flows/cc_companies.py`（ERP，今天从 `?view=companies` 起）改成从新对话弹窗起：员工能看到并选建、管理员看到「在 ERP 中打开」、
    知识库落库、待处理点开到单据页。
  - `scripts/flows/cc_sidebar_modes.py`（侧栏共有入口顺序去掉「公司」）、`cowork.cc.sidebar-modes`、`cowork.mrerp-login`（连接弹窗挪进设置页）。
  - e2e：`tests/e2e/registry.toml` 的 `e2e.cowork.add-company`、`e2e.erp.add-company`（`scripts/e2e/scenarios.py:241, 339-354`）改成「选择弹窗选公司」；
    截图场景 `scripts/cc-visual/scenes.mjs:194-206`。
- 产品地图（`scripts/impact/product-map/`，`check-product-map.py` 拦）：同一提交——删 `cowork.cc.view.companies` / `.company`、
  `erp.cc.view.companies` / `.company`；`cc.action.add-company` 两行改为「对话里选公司（弹窗）」；`cc.action.company-open-in-product` 改位置到
  「···」；`cc.action.deep-link` 补 `?company=` / `?view=pending` / `?view=companies` 新落点；`cowork.cc.action.mrerp-auth` 位置改为设置 → 连接；
  `ccaction` 锚点来自代码 `// map:` 注释，跟着组件改。
- 真机（测试机 CC 桌面版，**用户或测试窗口**，本窗口不动测试机）：设置 → 连接的 MR.ERP 一行：管理员填网址、在原生小窗授权 → 目录刷新 →
  弹窗 MR.ERP 标签出现公司；成员授权只存本机、目录不变；浏览器里按钮灰；Express 部分与 main 一致；选一家没用过的公司 → 直写开对话、代录开任务。
- 验收要用户亲自看：连接里的 MR.ERP 一行与弹窗；对话里点公司的弹窗；侧栏没了「公司」后改名 / 删除在「···」里找得到。

## 12. 要主会话定的点（每条附建议）

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | ERP 里「选中没建过的公司就建」今天只给 `company.manage`；用户口径是「能用这个连接的人都能选」 | 放宽为「开单权限 + 看得到这份账套」（ERP 本来就按账套自动建公司，员工选中只是提前建好那一行）；改名 / 停用 / 删除仍只在 ERP Desk、只给管理员 | 员工在弹窗里看得到却点不了，或看不到没建的公司 |
| 2 | 改名、删除公司谁能做：Cowork 今天有录入权限就能改删（主档全所共用），主会话建议只给管理员 | 收紧为 Cowork 管理员（`Cowork Admin`）服务端判；非管理员「···」里不出这两项 | 不收紧就照今天，任何录入人员都能删全所共用的主档 |
| 3 | ERP 里没接任何账本的纯 ERP 公司（在 ERP Desk 手建、不接 Express）放在弹窗哪里 | 标签只有 MR.ERP / Express 两种时，这类公司放在第三个标签「ERP」（产品名、不翻译、只在有这类公司时出）；Cowork 不会有 | 不定就会在弹窗里找不到这类公司（今天在下拉里能选到） |
| 4 | 「本月」卡换月：只换卡里看的月份，还是同时把对话移到那个月 | 只换卡里看的，不动对话（对话月份用下巴的月份胶囊改，今天就有） | 两处都能改对话月份，容易误移 |
| 5 | 公司页知识库空时的「让 CC 起草」（开一段新对话预填）搬不搬 | 不搬，删掉：在对话里直接说就行；右侧栏空知识库卡只给「＋」 | 搬的话要定它在当前对话里怎么起草 |
| 6 | （收窄后作废：Express 登录照现状，不进本需求） | — | — |
| 7 | 一个连接都没有、目录也空时，点「公司」弹窗里给什么 | 空状态一行「没有结果」+ 按钮「连接」（打开设置 → 连接）；不加说明文字 | 新账号找不到从哪里接账套 |
| 8 | 删公司今天不确认（公司页「···」） | 照旧不确认（无确认口径）；删对话要确认是用户点名的例外，公司不在其中 | 挪进侧栏「···」后误点概率比公司页高，是否要确认由主会话定 |
| 9 | 事务所电脑同时连 Cowork 与客户 ERP：Express 位置整台电脑一份，ERP 会按报来的全部账套自动建公司，选择弹窗会把它们都列出来 | 本需求不处理（今天就有的行为），记一条已知问题；要隔离另起需求（按 ERP 选位置，10-23 有过、`a78bed97` 去掉了） | 客户 ERP 的弹窗里可能看到事务所别的客户 |
| 10 | 旧提醒地址 `?company=<编号>` 落到哪 | 定时任务页按这家筛（提醒就是定时任务提醒）；服务端、CC 桌面版的地址格式不改，前端换落点 | 不定就点旧提醒什么也不打开 |
| 11 | 弹窗里怎么选回「不挂公司」（今天下拉末尾「最近」） | 列表第一行「最近」（已有词条，选了 = 不挂公司），当前选中打勾 | 选了公司就退不回去 |
| 12 | Cowork Desk 录入页「推送目标」里的 MR.ERP 连接要不要保留 | **保留、不动**：与 CC 里的按钮改的是同一条主连接（`main_connections`），授权状态两边一致；Desk 是选推送目标那一步、另一窗口正在改 `targets.js`、它本来就不收密码。以后要并成一个入口另起需求 | 去掉的话网页录入选 MR.ERP 时没处连；两边各留一份界面只是外观，不会出现两条连接 |
| 13 | ERP 站点上的 MR.ERP「连接」一行 | 本批**不出**：ERP 的 MR.ERP 账本没对用户开放（`MRERP_LEDGER_ENABLED = False`），由服务端声明 `connect` 控制，开关一开才出；ERP 的连接接口补成同形方法归「ERP 开 MR.ERP」任务 | 现在出了也点不通（ERP 接口关着一律当没有） |
| 14 | 只有管理员的站点授权会让 MR.ERP 公司进目录；成员在本机授权后弹窗里仍看不到 MR.ERP 公司 | 照今天口径（11-02 定 #9）不改：成员一行只读状态、可在本机授权给自己代录；目录要管理员授权 | 成员会以为「连上了但没公司」；如要成员也能让目录读到，要改成员授权写站点，动 11-02 定案 |

### 12.1 定案（主会话 11-07 审定）

- 范围：Express 连接保持现状、不做卡片；连接设置里只加 MR.ERP「连接」一行（§1.4）。第 4 步按此缩小（§14 的 D 步）。
- #1 同意放宽：ERP 有开单权限、看得到这份账套的人，就能在弹窗里选中即建。
- #2 同意收紧：Cowork 改名、删除公司只给 Cowork 管理员，服务端判。
- #3 同意：只在有纯 ERP 公司时出「ERP」标签。
- #4 同意：「本月」卡只换卡里看的月份，不移动对话。
- #5 同意：不搬「让 CC 起草」，空知识库卡只给「＋」。
- #6 照现状：Express 登录本批不动。
- #7 同意：「没有结果」+「连接」按钮。
- **#8 改为删除公司要确认一次**：用 CC 现有的删除确认弹窗（`delete-chats.tsx` / 定时任务的 `Confirm` 同一种样子：只有标题「删除公司？」，按钮「取消 / 删除」，打开时焦点在「取消」）。侧栏组头「···」、右侧栏公司卡「···」两个入口都先过它。理由：删公司会连带它的对话、文件夹、知识库，比删一段对话更重；挪进「···」后也更容易误点。
- #9 记成已知问题，另起需求。
- #10、#11、#12 同意（旧提醒落到定时任务页按这家筛；第一行「最近」；Desk 推送目标保留不动，同一条主连接）。
- #13 同意：ERP 站点由服务端声明控制，`MRERP_LEDGER_ENABLED` 打开才出 MR.ERP 一行。
- #14 同意照 11-02 定案：只有管理员的站点授权让 MR.ERP 公司进目录，成员本机授权只给自己代录用。
- 右侧栏单据文件夹卡、知识库卡空的时候也显示「＋」：认定为缺陷，本批一起修（§1.3 第 6、9 项）。
- 排期：等按行选物料（Cowork、ERP）和识别完自动继续合入 `main` 后再开工，避免 `i18n.ts`、产品地图、`shared-parts.toml` 冲突。开工从最新 `origin/main` 起，按 §14 的 4 步小步提交；测试机实测 CC 桌面版由主会话排时间。

## 13. 与正在进行的窗口的交叠

按基线 `origin/main` 对比各分支改动的文件：

| 窗口 / 分支 | 它改的文件里与本需求可能重叠的 | 处理 |
|---|---|---|
| 按行选物料 Cowork `pearnly/cowork-line-item`、ERP `pearnly/erp-line-item-choice` | `frontend/assistant/src/i18n.ts`、`components/tool-uis/ask-card.tsx`、`tool-uis/operator-words.ts`、`apps/pearnly_cowork/.../public/desk/targets.js`、`scripts/impact/product-map/{cowork,erp}.toml`、`scripts/impact/shared-parts.toml`、`tests/flows/registry.toml` | 本需求不碰 `ask-card` / `operator-words` / `targets.js`；`i18n.ts`、产品地图、登记表、流程登记是追加 / 删行，等它们合入后变基解决 |
| 识别完自动继续 `pearnly/cc-ocr-auto-continue-171c12` | `components/assistant-ui/thread.tsx`、`lib/managed-agents/{events,poll-source,progress,turn}.ts`、`i18n.ts`、`scripts/impact/shared-parts.toml`、`tests/test_assistant.py` | 本需求改 `assistant-ui/projects.tsx` 与 `lib/managed-agents/projects.ts`，不改它那几个；`thread.tsx` 只有在下巴插槽要动时才碰（预计不动）；`tests/test_assistant.py` 可能都改，变基解决 |
| 混行原生证据 `pearnly/cc-mixed-lines` | `companion/` 写账、`express_rules` | 无重叠 |
| 按行选物料评估 `pearnly/line-item-stock-choice-770877` | 只改文档 | 无重叠 |

建议：本需求施工排在上面三个前端 / 产品地图分支合入 `main` 之后，从最新 `origin/main` 开工，减少 `i18n.ts` 与产品地图的冲突。

## 14. 工作量与排期（按机器耗时）

拆成 4 步，可分别提交，按顺序合入（后一步依赖前一步）：

| 步 | 内容 | 写代码 + 单测 | 机器跑（闸门 / 流程） |
|---|---|---|---|
| A 服务端选择列表 | Cowork / ERP 各加 `choices`；ERP 建公司放宽（§12 #1）；Cowork 改名删除收紧（§12 #2）；`context.py` 一句 | 约 1 小时 | 全闸门约 20 分钟；只动产品域、不动 `pearnly_core` 时不跑生产镜像候选离线检查（动了就跑） |
| B 选择弹窗 | `add-company.tsx` 改成选择弹窗（合并列表、标签、刷新、「最近」、打勾）；下巴 / 手机行 / 代录新建页 / 定时任务接上；流程 `pickCompany` 改 | 约 1.5 小时 | 流程一轮约 30 分钟 |
| C 删公司页、搬功能 | 删 `companies.tsx` / `company-page.tsx` / 侧栏入口；组头与公司卡「···」；全部账套；空卡「＋」；本月卡月份；深链接新落点；产品地图、e2e、截图场景 | 约 2.5 小时 | 流程 + e2e + 截图约 1 小时（Cowork、ERP × 电脑、手机） |
| D 连接里加 MR.ERP | 抽出 `MrerpConnect` 成共用组件；设置 → 连接最上面加 MR.ERP 一行（Cowork 浏览器与桌面版）；授权后 `refreshCatalogs`；声明加 `connect`（Cowork 给、ERP 按开关）；Express 部分不动 | 约 45 分钟 | 流程 `cc.settings`、`cowork.mrerp-login` 约 20 分钟 |
| 等人 | 测试机 CC 桌面版实测（MR.ERP 授权小窗、目录刷新后弹窗出公司、Express 部分不变）+ 用户看样子 | — | 视用户 / 测试窗口 |

合计写代码约 5.75 小时 + 机器跑约 2.5 小时；不发 CC 桌面版新版；部署由主会话安排（测试机试前端要带 `PEARNLY_TESTENV_LOCAL_SPA=1` 同步 SPA，
否则 `testenv.sh live` 盖不到 `frontend/` 构建产物；交给用户前先从外部读回确认是新版本）。

## 15. 施工记录（B、C 步，11-07，窗口 cc-picker-bcd）

分支 `pearnly/cc-picker-bcd-7b34a9`（第 A 步 `8bdd4375f` 变基到 `origin/main` 后接着做）。截图前后对比在共享笔记
「公司选择弹窗-BCD-11-07/」（B-选择弹窗、C-删公司页搬功能；电脑、手机、CC 桌面版 × 浅深色，本机假服务端 `scripts/cc-visual`）。

### B 选择公司弹窗
- `components/add-company.tsx` 改名为 `components/company-picker.tsx`（`openCompanyPicker(then, current?)`），数据走第 A 步的 `choices`
  （服务端搜索、按标签筛、每页 50、「显示更多」）；标签按 `adapters`（两种以上才摆，有纯 ERP 公司多「ERP」）；第一行「最近」（新对话才有）、
  当前那行打勾；没建的点了用现有 `create` 建（同一行重点同一个 `request_key`）；头部「刷新账套」（声明 `catalog`）；一家都没有时「没有结果」
  +「连接」（打开设置 → 连接；设置里没有「连接」一栏的地方不出，`lib/desktop.ts` 的 `useHasConnections`，同设置页的判断）。
- 新对话下巴、手机问候行、代录新建页（同一个 `DraftChin`）与定时任务「＋」都开这一个弹窗；下巴在有公司分组的产品里一直显示。
- 前端不再调 `candidates`：两个产品的 `candidates` 白名单接口与服务包装删了（产品地图拦「无调用方接口」），测试改测 `_catalog`。
- 流程 `cc_lib.pickCompany` 在弹窗里选；e2e `add-company` 改名 `pick-company`（没跑过，产品地图先不引用）。

### C 删公司页、搬功能
- 删 `components/companies.tsx`、`components/company-page.tsx`、侧栏「公司」一行；`View` 只剩 chat / tasks / operator；公司页会话栏模式、
  公司页月份（`usePagePeriod`）、`hasAnyCompany`、`useOpenCompany` 一并删。
- 新 `components/company-menu.tsx`：改名（就地，`InlineRename`）、刷新账套、在浏览器中打开（desk_url）、删除公司（先确认「删除公司？」，
  `delete-chats.tsx` 的同一个弹窗）；侧栏组头「···」上半段与右侧栏公司卡「···」同用；按声明出（Cowork 改名、删除只给管理员）。
- 公司卡：名称不再点进公司页；「全部账套」展开（同待处理卡的展开行）。知识库、定时任务卡空的也显示「＋」；「让 CC 起草」删了。
- 「本月」卡：卡头月份胶囊只换卡里看的月份（不移动对话）；标题改用已有的「月结」（月份已由胶囊写出）。
- 深链接：`?view=companies` → 新对话并弹选择公司；`?company=` → 定时任务页只看这家（没有定时任务的产品在这家开新对话）；
  `?view=pending` → 那家那个月的对话 / 新对话，多家开对话页；都先回对话模式。
- 文案：新增「删除公司？」「全部账套」；删掉不再用的词条（添加公司、已添加、打开公司、对话、暂无对话、排序方式 / 最近活动 / 名称、
  让 CC 起草及其预填句、本月）。没有改已有文案。
- 流程 `cc_companies`（ERP）从新对话起：弹窗列可见公司与没建的账套、选中即建、代录选公司、右侧栏分区与「···」、组头「···」、知识库、
  待处理；`cc_sidebar_modes` 去掉「公司」入口相关步骤、两种模式都在弹窗里选。e2e `operator-new` 改从弹窗选（去掉 since）。

## 16. 施工记录（D 步：连接里加 MR.ERP，并进「MR.ERP 连接只留一个入口」，11-07）

分支 `pearnly/cc-mrerp-connect`（叠在 B+C `pearnly/cc-picker-bcd-7b34a9` 上）。截图在共享笔记「公司选择弹窗-BCD-11-07/D-连接里加MR.ERP/」。

- 服务端声明 `connect`：共用层 `projects.declared` 转交产品 `connect()` 给的 `{种类: 连接接口前缀}`（形状不对的丢掉，空就不回）；
  Cowork `companies.provider.connect()` 给 `pearnly_cowork.connections.api.`；ERP `masters.cc_projects.connect()` 只在
  `MRERP_LEDGER_ENABLED` 打开时给 `pearnly_erp_portal.mrerp.api.`（今天关着，不出这一行）。
- 前端 `components/mrerp-connect.tsx`：`MrerpRow`（设置 → 连接最上面一行：MR.ERP、状态「未连接 / 未授权 / 已授权：…」、「连接」），
  `MrerpConnect`（原定时任务里的弹窗搬来：系统地址 + `MrErpLogin`）；接口前缀只来自声明，存下或授权后对这条连接
  `request_refresh` 读一次账套目录。桌面版「此电脑 → 连接」与浏览器的「连接」都在最上面放这一行，下面 Express 部分不动。
- 唯一入口：定时任务的 MR.ERP「连接」改成打开设置的连接一节（定时任务里的 MR.ERP 弹窗删了，Express 的照旧）；Cowork Desk 录入工作台
  与自动录入规则的 MR.ERP「连接」改成新开一页 `/assistant?settings=connections`（评估原写 `/pearnly-app/?settings=…`，那是 Desk 的壳、
  会落到 Desk 首页，改成 CC 的地址）；CC 认 `?settings=<一栏>` 打开那一栏、地址里去掉。Desk 不再用的三个词条（connect.mrerp.*）删了。
- 新增词条「系统地址」（CC 里原来用服务端词条 System URL，同一个中文）。没有改已有文案。
- `lib/when.ts`：`formatWhen` 抽成小模块（`lib/companies` 照旧导出），设置页经 MR.ERP 授权组件不再连带加载公司数据与实时连接。
- 流程 `cowork.mrerp-login` 改成：定时任务「连接」→ 设置 → 连接（MR.ERP 一行「已授权：flow-old」在最上面）→ 弹窗（没有密码框、授权按钮在
  浏览器里不可用）→ 改地址存下；Desk 的「连接」新开 `/assistant?settings=connections`；打开这个地址就是设置的连接一节。
