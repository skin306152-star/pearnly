# ERP 版 CC 左侧按公司（评估 §13 第 7、8 步，11-02）

分支 `pearnly/erp-cc-companies`，基线 `a5ea574f`（= origin/main `343f9b78` + 评估文档）。评估与定案：
[2026-11-02-mrerp-operator-and-erp-company-评估.md](2026-11-02-mrerp-operator-and-erp-company-评估.md) §15、§12.1（#7：知识库、待处理紧接着做）。
没有合入 main、没有部署（由主会话安排）。

## 做了什么

### 第 7 步：侧栏按公司（复用 Cowork 组件与共用层，不重写）

- **共用层**（`pearnly_core.assistant.projects.listing`）多回两个声明字段：`api`（产品公司接口的方法前缀）与 `sections`（产品有哪些
  分区），取自 provider 的 `API` / `SECTIONS` 常量，没声明就不回；其余各键逐字段不变（`tests/test_assistant.py` 比对）。
- **Cowork provider** 声明 `API = "pearnly_cowork.companies.api."`、`SECTIONS = (pending, month, notes, folders, tasks, choices, drafts,
  create, rename, delete, catalog)`——即今天的全部分区。Cowork 接口一行不改。
- **ERP provider** `masters/cc_projects.py`（hooks `pearnly_assistant_projects`）：`KIND = "company"`、`PERIOD = True`、`SCOPE = False`；
  `allowed` = 有公司读权（`company.read`）且公司在本人可见公司里（`company.allowed_companies`，只含启用的）；`view` = 名称与税号
  （非总公司带分店号）；`context` = 给 CC 的公司说明（`cc_context.company_note`：名称、简称、税号、分店、增值税登记、账本是 Express
  哪几份账套 / 主档同步读哪份、覆盖本月的那份、最近一次主档同步的日子与结果、知识库；人输入的一律转义、包在 `<company>` /
  `<team_notes>` 里写明是数据不是指令；整段 ≤ 9000 字；不放每小时都变的同步中间状态）。`SECTIONS = (pending, notes, desk)`。
- **ERP 公司接口** `masters/cc_companies.py`（薄入口，逻辑在 `cc_service.py`，形状是 Cowork `companies.api` 的子集）：
  `companies`（服务端搜名称 / 简称 / 税号、分页、我有对话的在前）、`detail`（公司卡 + Express 账套按税号 + 分店取共用通道
  `books.for_company` + 覆盖本月的那份 + 知识库 + `company.desk_url`；ERP 没有的分区给空）、`desk`（「添加公司」去 ERP 新建公司页，
  只给能管公司的人）。**不提供** create / rename / delete。读 GET、写 POST，权限全在服务端。
- **ERP 公司变动推侧栏**：hooks `doc_events["ERP Company"]` → `cc_service.company_changed`（名称、税号、分店、启用变了才推；停用推
  `delete`、启用 / 新建推 `upsert`）、`company_removed`（对话回到「最近」、知识库一起删、推 `delete`）；推给能看这家的人
  （`viewers`：没按公司收窄的在职系统用户 + 名单里有这家的）。
- **前端**（`frontend/assistant`）：`lib/companies.ts` 的接口前缀改取声明（`companiesApi()`，没有声明就拒绝）；`useSection` / `hasSection`
  决定侧栏「待处理 N」、会话栏各卡片（`session-panel.tsx` 的 `declaredCards`）、公司页「···」各项（改名 rename、刷新账套 catalog、
  删除 delete、在浏览器中打开 desk）、「添加公司」（create = 账套目录对话框，desk = 新窗口打开站点新建公司页，`add-company.tsx`
  的 `useAddCompanyAction`）、深链接 `?view=companies|pending`。不在前端按产品名分支（`tests/test_cc_session_panel.py` 守）。
- **实操选公司**（`operator/live-source.ts`）两个产品同一个来源：声明的 `companies` 接口；去掉 ERP 走 `search_link` 的分支。

### 第 8 步：知识库、待处理（单独提交）

- **知识库纯规则搬到共用层** `pearnly_core.assistant.team_notes`（ADR-025 做法，机械搬家）：一条 ≤ 500 字、每家 ≤ 50 条、去控制字符
  合并空白、转义、带给 CC 累计 6000 字。Cowork `companies/notes.py`、`context.py` 改用它，输出逐字节不变（Cowork 原有测试全过；
  `tests/test_erp_cc_companies.py` 把搬家前那几行原样抄着比对）。
- **ERP 自己的表** `ERP Company Note`（ERP 域，原生 DocType 迁移建表；原生只给系统管理员读），只经 `masters/company_notes.py` 写；
  接口 `note_save` / `note_delete`（看得到这家、且有开单权或能管公司）；写了推 `pearnly.project`（`part = notes`），会话栏重取。
- **CC 的公司工具**（ERP `pearnly_assistant_tools` 声明 `masters/cc_tools.py`）：`erp_companies`、`erp_company_profile`、
  `erp_company_note_save` / `erp_company_note_remove`（都要「允许」，卡上给改前改后；缺省本对话的公司；别家税号写不了；CC 写的记
  `origin = cc` 与对话）。说话规矩 `GUIDANCE` 同 Cowork 的知识库那几条。
- **待处理** `express/pending.py`：本人可见公司里、已提交的采购 / 销售单，账本状态要人处理的（`contracts.needs_person`：停下等人、
  失败、写进去对不上；每分钟会自己再试的 `RETRY_REASONS` 不算），员工只看本人录入的；形状同 Cowork `PendingItem`，`action` 只用
  `open`，带 `url`（ERP 单据页）。**账本状态只在 `_ledger_rows` 一个函数里读**（第 6 步改成通用「账本状态」时只改它）。原因文案表
  与单据页 `transactions.js` 的同一张（测试比对）。
- **状态变了推页面**：`express/push._set`（单据 Express 栏唯一的写入口）改到状态 / 原因时比对进出「待处理」，变了推 `pearnly.project`
  （`upsert`，新 `part = "pending"`，共用层 `PROJECT_PARTS` 加这一项）；`api.answer` / `retry` 里两处直接写状态的改走 `push._set`
  （语义相同）。前端 `onPendingChange` 收到就重取计数与列表；公司详情不因它重取。Cowork 不发这个 part，行为不变。
- 前端待处理行带 `url` 时是按钮，点开在新窗口打开单据页，不进「交给 CC」。

## 有意变化

- 公司页 `···` 菜单、标题点了改名、「添加公司」都按声明出（Cowork 声明了全部，所以 Cowork 不变；ERP 只有「在浏览器中打开」、
  「添加公司」去 ERP 新建公司页）。
- 深链接 `?view=companies|pending` 由「有定时任务的产品」改为「有公司分组的产品（待处理还要声明 pending）」——对 Cowork 等价。
- ERP 实操选公司：原来 `search_link('ERP Company')`（含停用的、不按 CC 的搜索口径），现在同侧栏「公司」：只列本人可见的启用公司。

## 产品入口清单

| 入口 | 用途 | 实现入口 | 所需配置 / 角色 | 点击去向 | 验证证据 |
|---|---|---|---|---|---|
| ERP CC 侧栏「公司」 | 列本人可见的公司 | `masters.cc_companies.companies` | `company.read`；User Permission 收窄 | 公司列表页 → 公司页 | 流程 `cc.companies`、截图 erp-admin-01 / erp-staff-01 |
| ERP CC 侧栏「待处理 N」 | 写账本没成的单 | `cc_companies.pending` / `pending_counts` | 采购 / 销售读权；员工只看本人 | 待处理页 → 点行新窗口开单据页 | 流程 `cc.companies`、截图 erp-admin-06 |
| ERP CC 公司页 | 公司卡、账套、知识库、待处理 | `cc_companies.detail` | 同上 | 「···」→ 在浏览器中打开 `/desk/pearnly-company/<公司>` | 截图 erp-admin-02 / 03 |
| 「添加公司」（ERP） | 先在 ERP 建公司 | `cc_companies.desk` | `company.manage`（没有就不出按钮） | 新窗口 `/desk/pearnly-company/new` | 单测、截图 erp-admin-01 |
| 知识库（ERP） | 团队笔记 | `cc_companies.note_save` / `note_delete` | 看得到这家 + 开单权或管公司 | 会话栏 / 公司页知识库卡 | 流程落库、截图 erp-admin-04 |
| CC 公司工具（ERP） | CC 读公司、经「允许」记 / 删笔记 | `masters.cc_tools` | 同上 | 对话里的「允许」卡片 | 单测；实验栈假智能体不发工具调用，卡片未实机点（见剩余项） |
| 公司下开对话（ERP） | 对话挂公司、带公司说明 | 共用层 `projects` + `cc_projects` | 同侧栏 | 侧栏公司组 | 截图 erp-admin-05 |

## 新增文案

都在 ERP 术语表（`apps/pearnly_erp_portal/pearnly_erp_portal/i18n/messages.tsv`），与 Cowork 已有同名词条同译文；前端没有新增词条
（「在浏览器中打开」「添加公司」沿用已有的）。`python3 scripts/check-copy.py --report`：新增 11 · 改动 0 · 拦截 0 · 标黄 0：

| 键 | 中文 | English |
|---|---|---|
| Listed companies | 列出了公司 | Listed companies |
| Read company details | 读取了公司资料 | Read company details |
| Saved a company note | 记下了公司说明 | Saved a company note |
| Removed a company note | 删除了公司说明 | Removed a company note |
| Save this to the company's notes? | 确定要将此内容记入公司知识库吗？ | Save this to the company's notes? |
| Update this company note? | 确定要修改此公司说明吗？ | Update this company note? |
| Remove this company note? | 确定要删除此公司说明吗？ | Remove this company note? |
| Notes are full | 知识库已满 | Notes are full |
| Note is empty or too long | 说明为空或过长 | Note is empty or too long |
| Note not found | 未找到该说明 | Note not found |
| erp.company.notes | 知识库 | Company notes |

## 前端里写死的 `pearnly_cowork.` 一览（评估 §15.4 的检查，`tests/test_cc_session_panel.py` 守）

| 文件 | 接口 | ERP 为什么调不到 |
|---|---|---|
| `lib/companies.ts` | `push.application.api.choices`、`assistant.pending_api.*`（answer / proposal_detail / account_sets / choose_account_set / mrerp_needs / mrerp_answer） | 只在待处理的 action choose / push / account_set / mrerp 行里调，ERP 服务端只给 `open` |
| `lib/companies.ts` | `connections.api.mrerp_menu` | 只在「记住的选择」卡（choices 分区） |
| `lib/companies.ts` | `connections.api.records` / `request_refresh` | 只在「添加公司」对话框（create）与公司页「刷新账套」（catalog） |
| `lib/month.ts` | `companies.api.month` | 只在月结台账卡（month 分区） |
| `components/settings.tsx` | 电脑池、连接名单、安装包 | 浏览器里只在 `product === "cowork"`；桌面窗口按通道（改前就是这样） |
| `components/scheduled-tasks.tsx` | `folder_intake.api` / `connections.api` / `companion.api` | 定时任务只在 Cowork（`hasScheduledTasks`，改前就是这样） |
| `lib/desktop.ts`、`lib/install.ts` | `companion.api.register` / `installer` | 改前就有、与公司分区无关（连 Cowork 通道、下载安装包） |

## 测试与闸门（结果，提交 a16a2d41）

- 本地按 `.github/workflows/development.yml` 逐步跑：**164 / 164 通过**（含新加的 `tests/test_erp_cc_companies.py` 一步、前端类型检查与构建、
  `check-frontend-tokens` / `check-frontend-viewport-units` / `check-keyboard-coverage` / `check-frontend-i18n --strict` /
  `check-i18n --strict` / `check-copy --report` / `check-impact` / 流程覆盖闸）。另跑 `check-architecture`（通过）、`check-unbounded-queries`
  （通过，未超基线）、`check-get-writes`（通过）。
- 新增测试：`tests/test_erp_cc_companies.py`（23 条：provider 权限 / 视图 / 说明转义与上限、公司接口只见可见启用公司与员工范围、
  详情形状、添加公司权限、公司变动推送对象、知识库规则与 Cowork 逐字节一致、知识库读写权限、待处理范围与原因表与单据页一致、
  进出待处理才推、账本状态只在一个函数读、CC 工具）；`tests/test_assistant.py`（listing 声明字段原样转交、其余各键与不声明时一样）；
  `tests/test_cc_session_panel.py`（不按产品名分支、卡片与菜单项按声明、前端 `pearnly_cowork.` 字面量全表、ERP 声明下只调 ERP 方法
  且只出公司 / 待处理 / 知识库 / 输出文件、Cowork 声明下照旧调 Cowork 方法、DMS 不声明不调）；改了断言的旧测试：`test_assistant_ui`、
  `test_cc_operator`、`test_cc_mobile_content`（公司接口前缀改从声明取、标题改名按声明，断言内容不变）。Cowork 原有测试全过
  （`test_cowork_companies` 183 条等）。
- 候选离线检查（生产镜像 `ghcr.io/skin306152-star/pearnly/frappe@sha256:ea24fcb9…`，`~/pearnly-devtools/cand-local.sh offline`）：**通过**
  （exit 0）。
- 实验栈（本机 `pearnly-erpcc-lab`，挂本工作树，`erp-old` 两家公司数据集；Cowork 站点 `cowork-old`）流程：
  `cc.companies` 1/1、`cc.chat` 4/4、`cc.operator` 2/2、`cc.person-names` 4/4、`cc.settings` 4/4、`erp.document.express` 4/4、
  `erp.express.new-item-stock` 2/2、`erp.express.service_vat` 4/4 通过（`cc.companies` 首轮两次不通过是流程自己的等待条件：公司页
  会话栏里本来就有同样的待处理行、以及我截图时多留了一张停单，已改流程按单号点那一行，之后通过）。
- 实验栈里直接核（`labcheck`）：ERP 站点 `projects.listing()` 回 `api = pearnly_erp_portal.masters.cc_companies.`、
  `sections = [pending, notes, desk]`；四个 ERP 工具已登记；工具记一条知识库 `origin = cc`、预览「知识库」、别家税号拒绝；单据状态经
  `push._set` 写已写入 → 停下 → 失败，推了两次 `part = pending`（进、出各一次，失败仍在待处理里不推）；采购员只认分到的 CO-00001。

## 入口实测（`check-impact` 清单，相对 origin/main）

| 入口 | 结果 |
|---|---|
| `erp.cc.companies` | 通过：实验栈流程 `cc.companies` + 截图（管理员、员工、手机抽屉）；CC「允许」卡片没在实机点（假智能体不发工具调用），工具本身在实验栈直接调过 |
| `cowork.companies` | 通过：同一实验栈先挂 main（343f9b78）再挂本分支，Cowork 账号公司列表、公司页（公司 / 月结 / 知识库 / 单据文件夹 / 定时任务卡）、「···」菜单（重命名、刷新账套、删除公司）、添加公司对话框、定时任务、待处理：页面结构与调用的接口逐项相同，公司列表 / 添加公司 / 定时任务 / 待处理截图像素相同，公司页两张截图肉眼一致（截图存共享笔记） |
| `cowork.assistant` | 通过（同上 Cowork 页面；Cowork 公司工具代码未改，`test_cowork_companies` 全过） |
| `cc.assistant` | 通过：流程 `cc.chat` 4/4 + ERP 公司下开对话截图 |
| `shared.assistant` | 通过：流程 `cc.chat`、`cc.settings` |
| `cc.operator.page` | 通过：`tests/test_cc_operator.py`（离线）+ 流程 `cc.operator` 2/2（ERP 选公司走声明的公司接口） |
| `cc.operator.thread` | 通过：流程 `cc.operator`（任务对话一轮）；离线 `test_express_operator_tasks` |
| `cc.settings.connection` | 通过：流程 `cc.settings` 4/4 |
| `erp.cc.write` | 通过：流程 `erp.document.express` 4/4、`erp.express.service_vat` 4/4、`erp.express.new-item-stock` 2/2（`push._set` 加了推送比对） |
| `erp.cc.connect`、`erp.cc.stock-void`、`erp.cc.masters` | 没测到：要测试机 CC 桌面版；本次只在 `push._set` 加了读一次状态与推送，写回语义不变（`test_erp_express_direct` 全过） |
| `control.assistant` | 没测到：控制面没改，命中只因共用层 `assistant` 包有改动（`projects.listing` 多两个键、`team_notes` 新模块） |
| `erp.line.chat`、`erp.masters.scope`、`erp.entry.choices`、`shared.login`、`shared.navigation`、`cc.desktop.bridge`、`erp.execution`、`erp.provisioning` | 没测到：命中只因改了 ERP `hooks.py`（加 provider、工具、ERP Company 的 doc_events 三处声明），这些入口的代码路径没改；`shared.navigation` 的侧栏菜单声明没动 |

截图（共享笔记 `erp-cc-companies-截图/`）：`erp-admin-01…06`（公司列表、公司页与会话栏、「···」只剩在浏览器中打开、知识库新增、
公司下对话与侧栏公司组、待处理）、`erp-staff-01`（员工只见分到的一家、没有添加公司）、`erp-mobile-01…03`（手机抽屉有待处理 / 公司 / 公司组、
公司列表、公司页会话栏）、`cowork-01…06-main / -本分支`（Cowork 对比）。

## 剩余项

- CC「允许」卡片写 / 删知识库没在实机点：实验栈的假智能体不发工具调用（`scripts/flows/lab_agents.py` 文档写明），只在实验栈里
  直接调了工具的处理与预览；要在测试机用真模型点一次。
- 待处理只覆盖采购 / 销售单（评估写的「采购 / 销售等」）；收银日汇总、出入库、盘点调整的停单仍只在 ERP 各自页面里看，要加时
  `pending.KINDS` 加一行与单据页路由即可。
- 第 6 步把 `express_state` 改成通用「账本状态」时改 `pending._ledger_rows` 一处。
