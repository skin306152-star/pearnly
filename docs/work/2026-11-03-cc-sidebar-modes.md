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
（施工中，见下次更新）
