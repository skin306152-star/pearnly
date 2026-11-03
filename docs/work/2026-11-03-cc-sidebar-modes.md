# CC 侧栏「对话 / 代录」两种模式共用一个框架（11-03）

分支 `pearnly/cc-sidebar-modes`，基线 `9a6a6011`。清单交审稿：共享笔记 `CC侧栏两种模式统一-清单-11-03.md`；主会话 11-03 审定 8 条（见下）。
部署由主会话安排。

## 起因

用户拿 Claude 桌面版比：切模式时两边共有的入口一直在原位，只有列表和模式自己的入口换。CC 的代录模式只剩搜索、新建任务、任务列表，
共有入口全不见；Cowork 代录只显示「暂无任务」，ERP 代录能看到公司分组。

**两边不一样的原因**：代码同一套，没有按产品分支。代录侧栏的公司组原来只从「本人在本站的任务」里建（`taskSidebar`），截图里的
Cowork 账号在该站没有代录任务；对话侧栏的公司组来自「我有对话的公司」（共用层 `projects.listing`）。实验栈复核见「验证」。

## 主会话审定（11-03）

1. 定时任务挪到「公司」下面：共有入口在两种模式里位置一点不动。
2. 代录模式下点「待处理 / 公司」留在代录，主栏换成那一页。
3. 待处理页列出「需要你处理」的代录任务，组名用已有词「任务」。
4. 任务变化用 Frappe 原生 realtime 推（`publish_realtime`），不另起通道。
5. 切模式时搜索框里的字留着。
6. 声明的分区放进首屏那次已有的请求（`shell.describe`），定时任务不晚出。
7. 代录的公司组同对话：没有任务的公司也出组头，组头 ＋ 在这家新建任务。
8. 待处理按单据去重，同一张单只算一次；数字与待处理页列出的条数一致。待处理页的内容结构尽量不动（另一窗口以后可能改）。

## 做了什么

### 前端 `frontend/assistant`
- **共用导航** `assistant.tsx` 的 `SidebarNav`（电脑侧栏、手机抽屉同一个）：新建（`NewNav`：对话「新建」/ 代录「新建任务」，同一位置、
  同一个加号圆）→ 待处理 N → 公司 → 定时任务（`ScheduledNav`，只对话模式、按声明 `tasks`）。下面按模式接对话列表或任务列表。
  手机抽屉没有新建这一行（底部胶囊按模式「新建会话 / 新建任务」，现状）。`OperatorSidebar` 去掉自己顶上的「新建任务」。
- **模式与视图拆开**（`lib/nav.ts`）：`useModePlace.mode` 单独记；`chat / tasks` 是对话自己的、`operator` 是代录自己的、
  `companies / company / pending` 两种模式共有。进模式自己的视图就换模式（`noteViewMode`，ViewContext 的 setView 都经它），进共有的不换。
  两种模式各记最后的视图（`chatView` / `operatorView`）；后退 / 前进记模式；`openOperator` 打开某个任务（从对话模式来的先记对话视图）。
- 代录 Ctrl / ⌘ + N 从主栏挪到外壳（`useModeKeys`）：看着公司、待处理页时也能新建任务。
- **待处理 N** = 产品 `pending_counts` + 代录 `task_attention`（`components/operator/attention.ts`，站点事件 `pearnly_operator_task`、产品待处理
  变化、回前台、重连时重取；同一事件顺手更新侧栏任务列表）。公司组头、会话栏卡的数不变（只是产品的待处理，与按公司筛的页面一致）。
- **待处理页**只加两处：不按公司筛时在下面接「任务」一组（`operator/pending-tasks.tsx`，行同单据行，点了切到代录打开任务）；
  `PendingList` 加 `others`，另一组有东西时这一块空着不出空状态。其余内容结构不动。
- 代录公司组：对话侧栏的公司（`useProjects`）+ 有任务的公司，`empty` 跟对话侧栏「显示 → 空分组」；没有任务也没有公司组才写「暂无任务」。
- `hasScheduledTasks` 改按 `profile.sections`（不按产品名）。

### 服务端
- 共用层 `express_operator/tasks.py`：`attention()`（本人本站状态「需要你处理」的任务，按单据去重：任务里没录成的单都在
  `products.pending_documents()` 里的不算）；建 / 改 / 收尾 / 删提交后给本人推 `pearnly_operator_task`（`_announce`，`after_commit`）。
  `api.task_attention`（GET）。`products.py` 新可选声明 `pending`。
- ERP `express/operator.pending_documents`（`P:` / `S:` + 单号，同 `express.pending` 口径）；Cowork `operator_source.pending_documents` →
  `companies/pending.documents()`（同 `pending_counts` 口径）。两边 hooks 声明。
- `shell.describe` 多回 `sections`（共用层 `projects.declared`）。

## 有意变化
- Cowork 对话模式侧栏顺序：「待处理 / 定时任务 / 公司」→「待处理 / 公司 / 定时任务」（审定第 1 条）。
- 代录模式下点公司、待处理不再切回对话；代录侧栏多出没有任务的公司组。
- 待处理 N 含需要你处理的代录任务（按单据去重）。

## 文案
没有新增、改动词条（「任务」「需要你处理」「新建任务」都是已有的）。`check-copy --report`：新增 0 · 改动 0。

## 验证

- 单测（离线）：`test_express_operator_tasks`（attention 按单据去重、只给本人本站产品；每次变化提交后推给本人）、`test_assistant`
  （describe 带声明的分区）、`test_erp_cc_companies` / `test_cowork_companies`（去重用的单据集合就是页面列着的）、`test_cc_operator`
  （模式与视图拆开、共用导航顺序、定时任务按声明且只对话模式、Ctrl+N 在外壳）、`test_assistant_ui`、`test_cc_session_panel`。
- 本地全闸门（同 development.yml）：175 / 175 通过。另 `check-architecture`、`check-unbounded-queries`（tasks.py 子表那一处写了 bounded）、
  `check-get-writes`、`check-copy --report`（新增 0 · 改动 0）、流程覆盖闸通过。
- 候选离线（生产镜像 `sha256:ea24fcb9…`，`cand-local.sh offline`）：72 个测试文件全部 OK。
- 实验栈 `pearnly-sidebar-lab`（挂本分支）：新流程 `cc.sidebar-modes`（ERP）、`cowork.cc.sidebar-modes`（Cowork）电脑 + 手机 4 格通过，
  每步截图在共享笔记 `CC侧栏两种模式-截图/`（对话 / 代录 × Cowork / ERP × 电脑 / 手机，外加代录下公司页、待处理页、打开任务、切回对话）。
  ERP 那格用真数据跑了去重：一张单标成写账失败进了待处理，两条「需要你处理」的任务里单据已在待处理的那条不算，侧栏 2 = 1 张单 + 1 个任务，
  页面「任务」一组 1 行。Cowork 回归数据里没有识别单据（待处理为空），去重只由单测守。
  `--changed --base origin/main`：8 条流程 21 / 21 格通过（cc.chat、cc.companies、cc.operator、cc.person-names、erp.stocktake.scan、
  cowork.operator.unknown 与两条新流程）。
- Cowork / ERP 代录显示不同的原因（实验栈复核）：两站 admin 在流程准备任务前都是 0 个任务、0 段对话，旧代码下两边都会是「暂无任务」；
  用户截图里 ERP 那个账号本来就有任务。代码没有按产品分支，差别来自数据。

### 入口实测（`check-impact`，相对 origin/main）

| 入口 | 结果 |
|---|---|
| `cc.operator.page`、`cc.operator.thread` | 通过：`test_cc_operator`、`test_express_operator_tasks` + 流程 `cc.operator`、两条新流程 |
| `cc.assistant`、`shared.assistant`、`cowork.assistant` | 通过：流程 `cc.chat`、`cc.person-names`、两条新流程（对话侧栏、新建、待处理页） |
| `cowork.companies`、`erp.cc.companies` | 通过：流程 `cc.companies`；Cowork 公司页这次没改（只多声明一个 `pending` 钩子与 `pending.documents`） |
| `cc.settings.connection` | 通过：设置没改；流程 `cc.chat` 里打开对话页正常 |
| `erp.cc.chat-push`、`cowork.cc.mrerp-setup` | 没测到：命中只因 ERP / Cowork 的 `hooks.py` 多了一行 `pending` 声明，这两条路径的代码没改 |
| `shared.login`、`control.issue-and-entry`、`shared.shell-gate`、`shared.team.members`、`shared.line.login` | 没测到：命中只因 `shell.describe` 多回一个 `sections` 键（只读、无写），登录与开通闸逻辑没改；流程里各站登录、打开对话页正常 |
| `erp.line.chat`、`cowork.line.chat`、`erp.cc.connect`、`cc.desktop.bridge`、`cowork.execution`、`erp.execution`、`erp.provisioning`、`erp.masters.scope`、`shared.navigation` | 没测到：命中只因改了 `hooks.py`（各加一行声明）与 `shell.py`；这些入口的代码路径没改 |

## 剩余项
- 真机：测试机 CC 桌面版窗口（电脑宽、Ctrl+N、Ctrl+Shift+1/2）和手机 LINE 入口没点；实验栈网页同一套前端。
- 别的设备上任务变化的实时推送：单测守住了推送（提交后推给本人），没有用两台设备实测。
- 任务「需要你处理」在收尾（有页面开着时）才定；CC 录完时没有页面开着，服务端仍是「进行中」，数字晚到下次打开收尾时（现状，未改）。
