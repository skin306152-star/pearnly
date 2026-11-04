# CC 入口三项缺陷：切换站点落到登录页、ERP 添加公司跳系统浏览器、代录模式点公司进了对话公司页（2026-11-04）

- 窗口：cc-entry-fixes（主会话 local_3d36d798 派），分支 `pearnly/cc-entry-fixes-124a82`，基线 `c9057f6f4`（= 远端 main = 生产）。
- 来源：用户 11-03 实测；根因见共享笔记「交接-切换账号与添加公司-11-03.md」「CC侧栏两种模式统一-清单-11-03.md」。
- 状态：**评估待主会话审定，还没改代码。**
- 旧空分支 `pearnly/cc-site-switch-login`（停在 c9057f6f、无提交、无远端、工作树干净）已非强制删除工作树和本地分支。

## 0. 需求与范围

| # | 现象 | 要的结果 |
|---|---|---|
| 1 | CC 左下角站点列表点另一站点，第一次回到原站，再点落到 Pearnly Keycloak 登录页 | 带上目标站点去 fatcc.com；静默确认后的账号进不了这家，就回 CC 登录页换账号 |
| 2 | ERP 版 CC「添加公司」（对话 / 代录两种模式）在 CC 桌面版里开系统浏览器、要重新登录 | 和 Cowork 一样在 CC 里弹窗 |
| 3 | 代录模式下从「公司」导航选一家，主栏出对话模式的公司页 | 代录模式下，组头「＋」和「公司」导航选公司都进代录新建任务页（顶上「公司 · 月份」，带这家和默认月份）；对话模式不变 |

不在范围：同一账号池里换另一个账号（Keycloak 已登录会忽略 login_hint，原生做法是先退出，记一笔不修）；代录任务的去重、空任务清理；
侧栏结构（11-03 已定）。

## 1. 第 1 项：切换站点（修法已定，核对代码可行）

链路（读代码，行号见交接笔记）：访客打开 B 站 `/assistant` → `login_redirect.assistant_gate` 送 fatcc.com 并留 120 秒记号 →
`www/ai.py` 无参数 → `entry.resume_url()` 用记住的账号池 `prompt=none` 静默确认 → 回调按账号能进的站点 `go(A)` → 回到 A；
120 秒内再点 B，记号在 → 走 B 自己的 `begin_login` → pearnly 主题 Keycloak 登录页。

改动（OIDC 原生做法：静默确认只说明谁登着，能不能进以已验证身份的站点列表为准）：

1. `pearnly_core/login_redirect.py`：`assistant_gate(cookies, entry_url, site="")` 带 `?site=<frappe.local.site>`；两个调用方
   （`login_redirect.redirect_guests_to_identity` 与 `www/assistant.py`）一起改。记号分支不变。
2. `pearnly_control/www/ai.py`：`entry.resume_url(args.get("site"))`。
3. `assistant_entry/entry.py`：`resume_url(site="")` 用 `contracts.site` 校验，不合法当没传；`_authorize(..., silent=True, site=want)`
   存进 state。`callback`：state 里有 site 时，site 在本次身份的站点里 → `go(site)`；不在 → `/?fresh=1`（CC 登录页）。
   没有 site（直接开 fatcc.com、CC 窗口初次打开）行为不变。静默没过（`login_required`）本来就去 `/?fresh=1`，不变。
4. 改后第一次切站点：B 访客 → fatcc 静默确认（看不见）→ 账号进不了 B → CC 登录页；输 B 的账号 → 进 B。不经过 B 的记号分支。

站点名同计费登记（`provisioning` 的 `dep.site_name`，`go()` 用 `https://{site}`），与 `frappe.local.site` 同一个值。
不涉及 Express、CC 桌面版（窗口地址规则只认 Pearnly https，fatcc.com?site= 照常在窗口里开）、文案、存储。

测试：
- `tests/test_assistant_entry.py`：带 site 且身份进得了 → `go(site)`；带 site 进不了 → `/?fresh=1`（反例：放到旧代码上应为 `go(原站)`）；
  不合法 site 当没传；不带 site 的单站点直进 / 多站点静默回上次 / pick 不变；`RememberedIssuerTests` 的 `_authorize` 调用断言跟着改。
- `tests/test_assistant.py` 的 `test_assistant_guests_go_to_the_cc_entry_once`：入口地址带 site。
- 实验栈没有身份服务，不能跑流程；`shared.login`、`control.issue-and-entry` 记「没测到（实验栈无 Keycloak，要部署后真机）」。
- 真机（部署后）：测试机 CC 同时连 testerp / testcowork，只登 testerp，第一次点 testcowork 到 CC 登录页；退出照旧回 CC 登录页；
  会话过期重开 CC 窗口仍静默回上次那家。

## 2. 第 2 项：ERP「添加公司」与同类「打开站点页」链接

### 2.1 根因（同一类）

CC 桌面版 `companion/src/companion/gui_tomo.py:73-80` 的 `_Popup`：页面里所有 `window.open` / `target=_blank` 一律
`QDesktopServices.openUrl` 交给系统浏览器，那里没有站点会话 → 站点 `begin_login` → Keycloak 登录页。网页版、手机版 CC 新标签页
带同一份 Cookie，**不受影响**；只有 CC 桌面版有这个问题。CC 桌面版没有「在 CC 里开子窗口」的桥接方法，非对话页的标题栏也没有后退，
所以前端单改**不能**让这类链接在 CC 桌面版里带会话打开。

### 2.2 逐个结论（前端里所有打开新窗口的地方）

| 入口 | 产品 | 去哪 | CC 桌面版现在 | 结论 / 修法 |
|---|---|---|---|---|
| 添加公司（公司列表页「＋」、新对话公司条） | ERP | `/desk/pearnly-company/new` | 跳系统浏览器要登录 | **方案 A**（见 2.3）：改成 CC 里的添加公司弹窗，不发 CC 桌面版 |
| 添加公司 | Cowork | CC 弹窗（`create`） | 正常 | 不改 |
| 公司页「···」→「在浏览器中打开」 | ERP | `/desk/pearnly-company/<编号>` | 跳系统浏览器要登录 | 同类，需 CC 桌面版改（方案 B） |
| 待处理一行（有 `url` 的单） | ERP | `/desk/pearnly-purchase/<单号>` 等单据页 | 跳系统浏览器要登录 | 同类，需方案 B |
| 定时任务「···」→「查看需要处理的」 | Cowork | `/desk/pearnly-intake?push_state=failed` | 跳系统浏览器要登录（用户没报，同类） | 同类，需方案 B |
| 左下角菜单 关于 / 隐私 / 条款 | 共用 | 站点公开页 `/about/` 等 | 系统浏览器，不用登录 | 不改（公开页本来就该在浏览器里看） |
| 下载 CC 桌面版安装包（设置、定时任务、安装） | Cowork | 安装包地址 | 系统浏览器下载 | 不改 |
| 帮助 `HELP_URL` | 共用 | 外部 | 系统浏览器 | 不改 |

**方案 B（CC 桌面版，按类一次修好上面三个「站点页」入口）**：`_Popup` 遇到与当前对话页**同一站点**（已有纯函数
`tomo_links.same_site`）的 https 地址，用同一个网页配置（同一会话）开一个 CC 自己的子窗口（无系统标题栏，沿用 `_ChromeBar`
那条简洁标题栏，关了就释放，不常驻）；别的站点、外部地址照旧交给系统浏览器。只在点开时才有，空闲不占资源（符合「CC 桌面版要轻量」）。
- 改 `companion/`：`gui_tomo.py`（`_Popup` / 子窗口类）、`tomo_links.py`（判定纯函数 + 单测）。载荷、服务端契约不变，
  `tests/test_companion_contract.py` 不受影响。
- **必须发 CC 桌面版新版才生效**（版本号接 main 上的 1.12.13 往上走，按「CC 桌面版改完直接发正式版」口径，发版先问主会话）。
- 测试：`tomo_links` 判定单测（同站点 → 子窗口、别的站点 / 外部 → 系统浏览器、非 https / 本地文件 → 不开）；Qt 部分只能在测试机
  Windows 真机点（实验栈不跑 CC 桌面版）。
- 建议**另开一个工作包**做（本窗口把方案 A 和第 1、3 项先合入），原因：发版要单独授权、要测试机真机验，不该拖住另两项。

### 2.3 方案 A：ERP 声明 `create`，复用 Cowork 的添加公司弹窗

**先要说明的事实**（影响 A 的内容，请主会话定 §6 问题 1）：
- ERP 已经**自动**按 CC 桌面版报来的 Express 账套建公司（`express/companies.py` 的 `ensure`，心跳后台任务：按「税号 + 分店」分组，
  没有的就建、接 Express、建一个仓、排主档同步；没有 13 位税号的账套不建）。所以接 Express 的 ERP 租户，弹窗里的 Express 候选基本都是「已添加」。
- 弹窗真正能新建的是 **MR.ERP 账套**（`mrerp/catalog.py`，ERP MRERP Connection 上的目录快照）——现在只能在 ERP 公司页选账本，
  而且只限管理员。MR.ERP 线上没有可测的账号（记忆：等岳父账号，自建测试站已否），这部分只能离线 / 假数据测。
- 「不接任何账本的纯 ERP 公司」（只做收银、开单）现在走 ERP 新建公司页（字段多：仓库、收银台、收款方式…），**弹窗做不了**。

A 的做法（只读已有数据，不调电脑、不扣费）：
- 新增 ERP 接口（`masters/cc_companies.py` 薄入口 + `masters/cc_service.py`）：
  - `candidates(search)`：要 `company.manage`。候选 = ①Express：在用电脑报的账套（`Pearnly CC Book`，同 `express/companies.scoped` 的口径），
    按「税号 + 分店」合并，有这家公司的标 `company`（已添加）；②MR.ERP：各连接目录里的账套，按 `mrerp_connection + mrerp_account_set`
    认已添加的。形状同 Cowork 的 `Candidate`（identity、title、tax_id、branch、adapter、years、connection、account_set_key、company）。
    服务端搜索、上限同 Cowork（`CANDIDATES_MAX`），不在浏览器里过滤全部。
  - `create(connection, account_set_key, request_key)`：要 `company.manage`，服务端自己读目录算名称、税号；前端给的名称不收。
    Express：抽出 `express/companies.py` 里「建一家」的那段共用（同字段、同建仓、同排主档同步），已有同税号同分店的回现有的。
    MR.ERP：建公司（名称、税号、分店取目录）+ 建仓 + `mrerp/catalog.choose` 同一套赋值（要管理员；不是管理员的候选里不列 MR.ERP）。
    幂等：同一身份、同一幂等键、并发撞唯一键都回现有那家（照 Cowork `_existing`）。
  - 不做「刷新账套」：Express 没得刷（CC 桌面版心跳会报）；MR.ERP 目录刷新照旧在 ERP 的连接页（管理员）。
- `cc_projects.SECTIONS` 把 `desk` 换成 `create`，**不**声明 `catalog`。
- 前端要改一处（发现的缺陷）：弹窗空列表时的「刷新账套」按钮写死调 `pearnly_cowork.connections.api.*`（`lib/companies.ts`
  `refreshCatalogs`），ERP 站点上没有这个接口。改成只在产品声明了 `catalog` 时出这个按钮（同公司页「刷新账套」的判断），
  没声明的空列表出已有的「无结果」行。Cowork 行为不变。
  `desk` 只给添加公司用（「在浏览器中打开」用的是 `detail.company.desk_url`，不受影响）：A 之后 ERP 不再声明它，ERP 的 `desk` 接口、
  前端 `newCompanyUrl` 与 `useAddCompanyAction` 的 desk 分支都没有调用方了，**一并删掉**（不留没人用的分支）。
- 建好之后：`announce` 推 `pearnly.project`（现有 `company_changed` 钩子在 insert 后已推），弹窗 `then(id)` 打开那家（对话模式进公司页；
  代录模式按第 3 项进新建任务页）。
- 隔离与权限：服务端判 `company.manage`；MR.ERP 另判管理员；员工（只见分到的公司）看不到「添加公司」（接口回空 / 分区不声明给他）。
  跨租户不涉及（同站点）。
- 文案：全部复用已有词条（添加公司、已添加、刷新账套、搜索），不新增。
- 测试：`tests/test_erp_cc_companies.py` 补 candidates / create（Express 已添加、Express 缺的能建、MR.ERP 能建、非管理员不列 MR.ERP、
  员工 403、前端给的名称不收、幂等、并发撞唯一键）；流程 `cc.companies`（ERP，实验栈）把「添加公司去 ERP 新建公司页」改成
  「CC 里弹窗、候选里有已添加的 Express 公司、点已添加的打开那家」，并在对话 / 代录两种模式各点一次。

**我的建议**：A 照做（满足「和 Cowork 一样在 CC 里弹窗」，不发版）；纯 ERP 公司继续在 ERP 里建（Desk 公司页），CC 里不再给入口——
等方案 B 发版后，如需要再在弹窗外加回「在 ERP 里新建」也是同一类修法。是否接受见 §6 问题 1。

## 3. 第 3 项：代录模式下进公司一律进新建任务页

现状：`components/assistant-ui/projects.tsx` 的 `useOpenCompany` 只做 `setView("company")`，不看模式；代录侧栏组头「＋」走的是
`operator-sidebar.tsx` 的 `actions.newIn(project)`：切到 `operator` 视图 + `o.create({company, company_title, period: lastMonth()})`
（建一个准备中的任务并选中，主栏顶上就是「公司 · 月份」）。`OperatorProvider` 包着整个外壳（`assistant.tsx` 的 `OperatorScope`），
所以公司列表页里也拿得到代录控制器。

### 3.1 入口全清单（Cowork / ERP 同一套组件，两边一起改）

| 入口 | 代录模式现在 | 改后 | 对话模式 |
|---|---|---|---|
| 侧栏组头「＋」（电脑、手机抽屉） | 新建任务页 | 不变 | 新对话（不变） |
| 侧栏 / 抽屉「公司」→ 公司列表页 → 点一家（卡片） | **对话公司页** | 新建任务页（同组头「＋」） | 公司页（不变） |
| 公司列表页「添加公司」→ 建好 / 点已添加的 | **对话公司页** | 新建任务页 | 公司页（不变） |
| 新对话公司条「添加公司…」 | 只在对话里 | — | 不变 |
| 右侧栏公司卡「打开公司」（`session-panel`） | 代录模式没有这块（只在对话、公司页） | 不涉及 | 不变 |
| 深链接 `?company=<编号>`（托盘定时任务提醒「主档页」） | 代录模式下也打开对话公司页（模式不变） | **建议**：先切回对话模式再开公司页（提醒来自定时任务，属对话一侧），见 §6 问题 3 | 不变 |
| 后退 / 前进 | 记录里有（公司页, 代录）这一格的只有上面的旧路径 | 改后不再产生 | 不变 |
| 键盘 ⌘/Ctrl+1–9、↑↓ Enter、搜索 | 只走侧栏行（任务行 / 组头） | 不涉及 | 不变 |
| 待处理页 | 不进公司页 | 不涉及 | 不变 |

### 3.2 改法

- `useOpenCompany` 改成按模式：代录模式 → 与组头「＋」同一个动作（抽成 operator 侧导出的一个函数，组头和这里共用，不复制一份）；
  对话模式照旧。需要公司显示名：公司列表页的行本来就有 `title`；添加公司的回调把 `title` 一起传（`createCompany` 回 `{id, title}`，
  候选行也有 `title`）。
- 不新增文案、样式。

### 3.3 测试

- 流程 `cc.sidebar-modes`（ERP）与 `cowork.cc.sidebar-modes`（Cowork），电脑 + 手机：在「代录下点公司」之后加一步「点公司列表里的一家」，
  断言：仍在代录模式、主栏是任务页、顶上公司是这家、月份是默认月（上个月）、站点上多了这家的一条准备中任务；再切回对话模式点同一家，
  断言是公司页。
- `tests/test_cc_operator.py`（离线假数据源）补：代录模式 `useOpenCompany` 走建任务、对话模式走公司页。

## 4. 共用件与连带回归（`python3 scripts/check-impact.py --files <预计改动>`，施工后用实际 diff 重跑）

命中共用件：`login-session`、`cc-sidebar-list`。要实测的入口（施工后逐行填「通过 / 不通过 / 没测到（原因）」，以后登记进产品地图）：

| 入口 | 产品 | 方式 | 计划 |
|---|---|---|---|
| `shared.login` | 共用 | manual | 没测到（实验栈无 Keycloak），部署后真机 |
| `control.issue-and-entry` | 控制面 | manual | 单测；部署后真机切站点 |
| `shared.shell-gate` | 共用 | manual | 不改开通闸；跑 `test_assistant` |
| `shared.team.members` | 共用 | manual | 不涉及（同改 login_redirect 文件连带）；跑相关单测 |
| `shared.line.login` | 共用 | manual | 不涉及（同上） |
| `cc.assistant` | CC | manual | 流程 `cc.chat` |
| `cc.operator.page` | CC | offline | `test_cc_operator` + 流程 `cc.operator` |
| `erp.cc.companies` | ERP | manual | 流程 `cc.companies`（改后）+ `cc.sidebar-modes` |
| `cc.desktop.bridge` | CC | manual | 本窗口不改 `companion/`；方案 B 另做时实测 |
| `control.execution` | 控制面 | manual | fatcc.com 入口单测 |

## 5. 验证计划

- 单测：`test_assistant_entry`、`test_assistant`、`test_erp_cc_companies`、`test_cowork_companies`、`test_cc_operator`、`test_assistant_ui`、
  `test_cc_session_panel`；新用例先放旧代码上确认会失败。
- 闸门：与 `development.yml` 同名全跑（`~/pearnly-devtools/gate.sh`），含 check-architecture、check-copy --report、frontend tokens /
  i18n / keyboard、unbounded-queries、flows coverage、check-impact。改了 `pearnly_core`（login_redirect）→ 合入前跑候选离线检查。
- 实验栈 `pearnly-regression-lab`（用前 `docker ps` 确认没人用，用完 stop 保留卷）：`cc.sidebar-modes`、`cowork.cc.sidebar-modes`、
  `cc.companies`、`cc.operator`、`cc.chat`，以及 `--changed --base origin/main` 选出的其余流程。
- 推分支 → Development checks 绿 → 快进合入 main 并推送 → 回读远端 SHA → 清理。**不部署。**
- 证据分层：真机（CC 桌面版切站点、ERP 添加公司在测试机上点）要部署后才有，交付时写「没测到」。

## 6. 要主会话定的

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | ERP 添加公司走方案 A 后，CC 里只能从账套建（Express 基本已自动建好，真正新建的是 MR.ERP）；纯 ERP 公司（不接账本）只能回 ERP 网页里建。接受吗？ | 接受；纯 ERP 公司在 ERP 里建。MR.ERP 部分只有离线测试（线上没有可测账号） | 不接受就只能等方案 B 发版，用 CC 子窗口开 ERP 新建公司页 |
| 2 | 方案 B（CC 桌面版里同站点链接开 CC 子窗口：「在浏览器中打开」、ERP 待处理单据、Cowork 定时任务「查看需要处理的」）要不要做、谁做 | 做，另开工作包，要发 CC 桌面版并在测试机真机验 | 不做则这三个入口在 CC 桌面版里继续要重新登录 |
| 3 | 代录模式下托盘提醒的深链接 `?company=`：先切回对话模式再开公司页？ | 切回对话（提醒来自定时任务） | 不改就仍会在代录模式下出现对话公司页 |
| 4 | 代录模式下每点一次公司就建一个准备中任务（与组头「＋」现状一致），会堆空任务 | 本次照现状（不扩范围）；要去重另议 | — |

## 7. 工作量（机器耗时）

- 第 1 项：写码 + 单测约 20 分钟。
- 第 3 项：写码 + 单测 + 两条流程加步骤约 40 分钟。
- 第 2 项方案 A：服务端两个接口 + 抽共用建公司 + 单测约 1 小时；流程改写约 20 分钟。
- 全闸门约 15 分钟、实验栈流程约 30–40 分钟、CI 约 15 分钟。合计约 3–3.5 小时（不含等审定）。
