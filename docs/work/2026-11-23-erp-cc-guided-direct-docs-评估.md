# 需求影响评估：ERP 直写采购 / 销售引导（含 ERP 待处理 4509b017c）

基线：origin/main **4ceef0788**（车 3–6 已合入）。分支 `pearnly/erp-cc-guided-direct-docs`，工作树 `.claude/worktrees/erp-cc-guided-direct-docs`。
主会话 local_0d36f9f2 11-23 审定批准（两点照建议；付款题措辞改正：不预选、推荐排第一、点了才算，记住的直接用）。

## 0. 需求原文与理解

- 原话（主会话 11-23）：给 ERP 直写放出采购 / 销售引导，覆盖本地 Express 与 MR.ERP；照 Cowork 直写采购 / 销售与 ERP 代录已有的引导（同一套引擎、卡片，不另写）；
  把远端 `pearnly/cc-guided-pending` 上的 4509b017c 一并带进来，车 4 的 `wait:erp_docs` 那格同时升；同提交改 `cc-coverage.toml`；
  页面流程按「真页面点卡从题到结果」写。
- 我的理解：ERP 账号在 CC 对话（`/assistant`，直写模式）里点输入框上方「采购」/「销售」→ 卡上交票 → 识别 → 题卡（付款、各行物料、仓库、Express / MR.ERP 要选的项）
  → 复核（就地改票面）→ 确定 → 提交成 ERP 单并排写账 → 结果卡带账本单号。待处理里停下的 ERP 单可以点选后交给同一张采购 / 销售引导卡接着办。
- 不在范围：云端 Express（`wait:cloud` 不动）；随单新建对方 / 物料那一行（`new_master`，另一行）；车 7 出入库；LINE 收单与网页手录的行为不改；打字路径（`erp_push_documents` 等工具）不改。

## 1. 现有同类与复用

- 代码基本都已在 main 上，缺的只是「直写」这一档没开：
  - 声明 `apps/pearnly_erp_portal/pearnly_erp_portal/express/cc_guides.py`：purchase / sales `modes: ["operator"]`，文件头写明「ERP 直写整链还没走通过，P2 整链验过后把 chat 加回」。
  - 产品函数 `express/guide_documents.py` 已有直写分支：`confirm` 逐张调网页同一个入账函数 `integrations.intake.submit_chat`（请求号 `cc-intake:<记录>`，一张只入一次），
    提交前用 `_not_ready` 守两条底线（行没定、付款没定 → 这一张留下、原因原样）；`P:` / `S:`（已是 ERP 单）走对话推送同一条 `cc_tools._push_one`。
  - 共用引擎 `pearnly_core/assistant/guide/documents.py::confirm`：先过写入计划 `write_plan.from_page`（逐张重算判定，只写可写的），之后按会话模式分叉：直写调声明的 `confirm`，代录交 `hand_in`。
  - 引擎按模式拒在 `guide/run.py:564`（`now["mode"] not in guide["modes"]`），菜单按 `registry.py:95` 同一条件过滤：只要声明加 `"chat"`，菜单与 run.start 同时放开，不另改引擎。
  - Cowork 直写采购 / 销售（`pearnly_cowork/assistant/guides.py`，`modes: ["chat", "operator"]`）是同形状先例。
  - 待处理：4509b017c（`express/cc_guide_pending.py` + `express/guide_pending.py`）只转调侧栏「待处理」同一个 `express.pending.pending`，确定后 `run.start(业务, doc_ids=[P:/S:], at="asking")` 交给单据引导，与 main 上的 Cowork 待处理（车 4）同形状。
- 结论：**扩展现有**——声明加 `chat`，带进 4509b017c 并按 main 现在的引擎口径对齐（车 4 里 Cowork 待处理改过的：拒收 `{格: {code, text}}`、`run._idle` 时钟桩），补测试与页面流程。不新建产品函数、不改引擎。

## 2. Frappe 原生能力

- 不涉及新界面：卡片在 `/assistant`（ADR-019 限定的 React 例外，同一张引导卡 `guide-run-card.tsx`），不新增组件。ERP 单据仍是原生 DocType（Purchase Document / ERP Sales Document），入账走网页同一个 `entry.post`。

## 3. 按类通用

- 类别：「按单据办的业务（documents 源）× 产品 × 模式」。四格里 Cowork 直写、Cowork 代录、ERP 代录已开，本次补齐 ERP 直写，此后四格同一份声明形状。
- 同类同时覆盖：采购、销售两项一起开；待处理（ERP）一起带进来，因为它只能交给直写的采购 / 销售引导。
- 不覆盖：收款（已是直写）、出入库（车 7）、云端 Express 账本（来源仍是占位）。

## 4. 上下游数据

- ERP 单据流：收单记录 `ERP Chat Intake`（source=cc，本人、本对话、这家公司）→ 确定时 `submit_chat` → `purchasing.entry.post` / `sales.entry.post`（库存、付款照手工录入）
  → `express.push.queue`：公司的账本是 MR.ERP（`mrerp.push.handles`）交 MR.ERP 导入直写，否则排 Express 直写并带上这段对话（`conversation.for_session` → 本对话账套、写入计划）。
- **写账判定与推送同一判据**：题卡出题用 `write_precheck.api.card`，ERP 端认 `I:<记录>` 走 `express.precheck` → `operator._intake_precheck(judged=True)`（付款、各行物料、仓库 `intake_items` + Express / MR.ERP 要选的项）；
  确定时 `write_plan.from_page` 逐张**重算同一份判定**，只放行可写的；提交后的单据再由推送前的预检判（同网页提交）。直写与代录在确定之前完全同一条路，只在确定后分叉。
  施工时补一条测试把这件事钉住：同一张 `I:` 记录在 chat / operator 两种模式下出的题键相同，确定时放行集合等于判定可写集合。
- **付款方式题**：出在题卡上（`intake_items` 第一项 `kind=payment`：选项 = 付款方式 + 未付；按「方向 + 对方」合成一题、答一次按对方记住）。
  卡上**不预选**：服务端按票面判出的推荐值只让该选项排第一、标「推荐」（`write_precheck.guide` 的 HOW 档 → 前端 `askQuestionsOf` 标 recommended、
  `AskCard preselect={false}` 不默认选中），用户点了才算（主会话 11-16）；**记住过的**（对这个对方答过一次）按「口径只决定问不问、已答的各口径都用」
  直接用、不再出题（来源 REMEMBERED → 已定）。与 Cowork 直写同一引擎、同一张卡，不另写。确定时 `_not_ready` → `operator.settle_payment` 再核：卡上点过或对这个对方记住过的才算定了，否则这一张留下、写「付款没定」原句，不悄悄按「已付、现金」交单。
  直写与代录同一个判法（`settle_payment` 文件头已写明）。
- Express 映射：不变。写进本对话账套，过写入计划的门；结果不明不再推（`cc_tools._push_one` 同对话推送）。
- MR.ERP：开关 11-22 已开。MR.ERP 推送 `mrerp_push.queue(doctype, doc)` 不带对话（MR.ERP 按公司的连接写，没有「本对话账套」之分），这与 ERP 直写打字推送一致；
  首次推单的菜单 / 默认科目「选一次」在题卡上作为 `mrerp_menu` 项问（测试 `test_operator_intake` 已有）。11-22 生产实录：ERP 直写采购 PG691122-288584、销售 SI691122-409406（打字路径）。
- CC 桌面版：载荷不变（直写只是服务端提交 + 已有的写账通道），不升载荷版本。
- Cowork / DMS：不改。

## 5. 隔离与权限

- 都在服务端：收单记录只认本人、`source=cc`、本对话公司、有该方向开单权（`_records` / `DIRECTION_CAPABILITY`）；`P:` / `S:` 走单据页同一判据（查看权、公司可见、员工只看本人录入）。
- 菜单出不出：声明的 `ledgers` 按对话公司的账本过滤（`conversation.guide_ledger`）；待处理另有 `allowed`（能开采购或销售单的人）。
- 跨公司：引导运行带 `expires: company / ledger / period / master`，换公司 / 账套后旧卡作废；待处理清单按本对话公司取。

## 6. 入口：网页、手机、LINE、拍照

| 入口 | 现在 | 本次后 |
|---|---|---|
| 电脑网页 `/assistant`（直写） | 菜单只有收款等，没有采购 / 销售；打字照旧能办 | 菜单出「采购」「销售」「待处理」，点卡办完 |
| 手机网页 `/assistant`（同一页面窄屏） | 同上 | 同上；页面流程桌面 + 手机宽度各跑一遍 |
| CC 桌面版（Windows） | 对话页同一个网页 | 同上；不改桌面版代码 |
| LINE 收单 | 自己的卡片（`save_card`，11-18 / 11-19），不经引导 | 不变；同一份写账前置判定，题不会两边不一致 |
| 拍照 | 卡上交文件用对话附件（已有的上传 / 拍照入口） | 不变，不碰相机共用件 |
| 代录 | 已开 | 不变 |

## 7. 共用件与连带回归

- 只改产品声明与产品函数（`pearnly_erp_portal`），不改 `pearnly_core` 引擎、不改前端。`check-impact` 列出的入口里要实测的：
  `erp.cc.chat-push`（离线测试）、`erp.cc.collection`（菜单同屏）、`cc.operator.thread`（代录不受影响，跑代录流程 `erp.cc.operator.intake*`）、`erp.cc.write`（auto）。
  `erp.line.chat`、`erp.execution`（LINE / 付费识别）不在本次改动链上，标「没测到（不在改动链，付费识别）」。
- `shared-parts.toml`：两份新声明文件（待处理）补进 `not_entries`（同 Cowork 待处理那两行的写法）。
- 冒烟：不新增 `scripts/smoke/`；新增页面流程见第 11 节。

## 8. 存储与成本

- 识别：每份交的文件识别一次、照常扣费（「上传识别都扣费」），同一份文件再交回原记录不重复识别（请求键 = 对话文件编号）。量与代录 / LINE 同级，没有新增固定月费。
- 原件：收单记录源文件走已有的原件层，不新增存储类型。
- 测试：页面流程不调付费识别（假识别结果，同 `erp_cc_guide_documents_edit` 流程的造数法），不连 Express、不调模型。

## 9. 删除、作废、追溯

- 卡上取消 / 过期：收单记录留在 READY（同代录），`expire_stale` 只清排队超 6 小时的；不新增删除逻辑。
- 已写进 Express / MR.ERP 的单作废：走单据页已有的作废（`entry` 的 `express_push.void`），本次不改。
- 追溯：同意凭证 `guide:<运行>:<版本>`（引擎存档），单据来源 `cc`，请求号 `cc-intake:<记录>`。

## 10. 文案与官方 UI

- 新增词条只来自 4509b017c：`guide.business.pending`（待处理）、`guide.open.pending`、`guide.step.pending.item`，**与 Cowork 待处理同一组键与字**（main 上 Cowork 已有，ERP 的 `messages.tsv` 补上同一行，四语）。
- 采购 / 销售沿用已有键（`guide.business.purchase` / `sales`、`guide.open.*`），不新增文案。`check-copy.py` 照跑，`--report` 附交付。
- 官方 UI：不改前端组件。

## 11. 测试与验收

- 单元 / 离线：
  - `tests/test_cc_guide_registry.py`：ERP 菜单直写模式出采购 / 销售 / 待处理，代录不出待处理。
  - ERP 单据源直写：`tests/test_erp_guide_documents.py` 补直写确定 → `submit_chat` 一张一事务、付款没定 / 行没定留下、同一张不入两次；题键 chat 与 operator 相同（第 4 节那条）。
  - 待处理：`tests/test_erp_pending_guide.py`（随 4509b017c 带进来，按 main 引擎口径改：拒收 `{格: {code, text}}` 比 code、`run._idle` 时钟桩）。
  - 权限失败：员工 / 别家公司 / 无开单权各一条（已有判据，补断言）。
- 页面流程（真页面点卡，从题到结果；**先与页面流程窗口 local_e7561a69 对写法**，碰同一格先问主会话）：
  - `erp.cc.guide.purchase`：直写对话点「采购」→ 交一份造好识别结果的票 → 题卡答付款（未付）、物料、仓库 → 复核改一格 → 确定 → 结果行；库里断言：单据来源 cc、付款 = 卡上点的、同意凭证 = 页面这一下、排了写账（本地 Express，假电脑 / 不连）。电脑 + 手机宽度。
  - `erp.cc.guide.sales`：同上，销售，付款选现金。
  - `erp.cc.guide.pending`：待处理里点一张停下的单 → 进销售 / 采购卡的判定那一步 → 确定 → 结果。
  - MR.ERP 格：同一条流程在 MR.ERP 账本的公司上跑一遍（假 MR.ERP 连接、只断言交给 `mrerp_push.queue`、不连 MR.ERP）。是否单列一条由流程窗口定写法。
- 总表（同提交改 `scripts/impact/cc-coverage.toml`）：
  - 采购、销售 `erp_direct`：`todo:p2 → half`（本地 Express），`wait:mrerp → half`（MR.ERP），`wait:cloud` 不动；有页面流程后按闸门规则判是否该升 ✅（声明完整 review + confirm + result + page_flows → 闸门要求标 done）。
  - 待处理 `erp_direct.express_local`：`wait:erp_docs → half`，MR.ERP 格同理；`wait:erp_docs` 这条等待线不再有格用时，从 `check-cc-coverage.py` WAITS 与 `verification.md` 里删掉（同提交）。
  - `tests/test_cc_coverage.py` 期望数随之改。
- 真模型 / 真机：不在本次（证据档写「只有服务端」/「页面流程」）。上线后由测试窗口按产品地图补。
- 验收：主会话复核；用户要看的是 ERP 对话里点「采购」从交票到看到账本单号走得通。

## 12. 要问用户 / 客户的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | MR.ERP 直写页面流程是单列一条，还是同一条流程换账本参数跑两遍 | 主会话 11-23 定：同一条流程参数化跑两遍 | — |
| 2 | 待处理里的单交给采购 / 销售卡后，复核行只读（改票面回单据页）——沿用 4509b017c 的做法 | 主会话 11-23 定：沿用 | — |

（都是主会话可定的工程口径，不需问用户。）

## 13. 工作量与排期

1. 带进 4509b017c 并对齐 main 引擎口径 + 测试：写代码约 30 分钟，闸门约 15 分钟。
2. 声明加 `chat` + 直写离线测试（题键一致、付款底线、一张一事务）：约 40 分钟。
3. 页面流程 2–3 条（先与 local_e7561a69 对写法）：写约 1 小时，本机实验栈跑约 30 分钟。
4. 总表 / 等待线 / 文档 / 产品地图：约 20 分钟。
5. 全闸门 + 候选离线（改了 app 代码）+ public-scan + 推送 + GitHub：机器约 40 分钟。
依赖：1、2 可并行写，3 依赖 1、2；一个分支一次交合入。今天不部署。

## 施工记录（11-23）

- 页面流程分工与写法照页面流程窗口 local_e7561a69：ERP 直写 6 格归本分支；骨架复用它的 `erp_cc_guide_operator_lib.py`（507ec23d0 摘进来，它进 main 后变基自动去重），
  直写版 `scripts/flows/erp_cc_guide_direct_lib.py` + 薄模块 `erp_cc_guide_purchase.py` / `erp_cc_guide_sales.py`；待处理 `erp_cc_guide_pending.py`（菜单点「待处理」→ 选单 → 交给销售引导 → 题卡 → 复核 → 确定）。
  造数 `erp_cc_operator_intake.prepare` 加 `mode`（直写会话不建代录任务）与 `company_name` 两个参数，代录那几条不变。
- 总表：ERP 直写采购 / 销售（本地 Express）有了完整声明 + 页面流程，按闸门规则标 ✅；待处理是 form 源、没有结果步骤，留 ◐。
- MR.ERP 两格（采购 / 销售 × MR.ERP）：实验栈没有 MR.ERP 连接与主档，同一条流程换 MR.ERP 公司跑要先能造出 MR.ERP 公司的写账判定数据；本批先不登记，留 ◐，照实报主会话。
- 已知共用层缺口（页面流程窗口报、主会话已派它修）：documents 源出题 `card_for` 没进对话口径 `guided.within`，判定说可写、推送仍要付款 / 科目。修好之前「写账判定与推送同一判据」不完全成立；本分支不绕。
