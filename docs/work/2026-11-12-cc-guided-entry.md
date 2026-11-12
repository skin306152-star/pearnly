# 需求影响评估：CC 引导式录入（采购、销售、收款）

- 分支 `pearnly/cc-guided-entry-51673b`，基线 `origin/main` 32695c276，工作树 `.claude/worktrees/cc-guided-entry-51673b`。
- 本记录只有评估和方案：没改产品代码、没连测试机和生产、没花识别或模型费用。待主会话审定后才施工。
- 界面示意在 [2026-11-12-cc-guided-entry/](2026-11-12-cc-guided-entry/)：7 张静态示意（`01`–`07`，由 `mockup.html` 生成）+ 4 张**真组件预览**截图（`真组件-*.png`，
  用 `components/ui` 的标准组件在展示页拼的，源码 `frontend/assistant/src/kit/guided.tsx`，本机 `kit.html?page=guided&lang=zh` 可看四语、深浅色、手机宽度）。
- 范围按主会话 11-12 更正：三种业务是**采购、销售、收款**；付款不做。界面一律用标准组件库 `frontend/assistant/src/components/ui`。

路径缩写：`K/` = `apps/pearnly_core/pearnly_core/`，`C/` = `apps/pearnly_cowork/pearnly_cowork/`，`E/` = `apps/pearnly_erp_portal/pearnly_erp_portal/`
（ERP 包的实际目录是 `pearnly_erp_portal`），`F/` = `frontend/assistant/src/`，`D/` = `companion/src/companion/`，
`收款支` = 分支 `pearnly/collection-cc-chat` 71fe3fb2e（工作树 `.claude/worktrees/recursing-ishizaka-4441ac`，只读看过）。

## 0. 需求原文与理解

- 用户 11-12：「cc不管是直写还是代录，不管是cowork还是erp，对话框的前端交互迟迟未达到我们的期望……做成引导式的方法……先选择业务类型，
  提供卡片的引导式让用户去选择，引导的主题来源于这批次要录入的帐套和exp的规则……就算一个新手也能更好的上手」「实在不行先在对话框上方把菜单做出了，
  点了菜单后引导用户上传和选择」「是收款，我说错了，不是付款」「前端一定要用上次迁移的新的标准组件」。
- 我的理解：录单这条路上，**问什么由后台按固定清单出题，不由模型临场决定**。用户在输入框上方点业务 → 上传 → 后台逐项对主档，对得上的不问，
  对不上的合成一张卡问完（同类票答一次全批沿用、答过记住）→ 复核 → 说「录吧」→ 结果。模型只负责听懂人话、解释、复述，不再自己出业务题。
  四种组合（Cowork / ERP × 直写 / 代录）同一套。自由聊天（查账、提问）不受影响。
- 不在本需求范围：付款单（两产品、两模式都没有写账能力，见 1.4）；Express 原生作废写错的单（11-09「不管」）；LINE 收票、文件夹定时录入
  （11-09 选 A：只做 CC 对话入口）；库存单、POS 导入、报税草稿；MR.ERP 专属配方（D 线按同一接口接）；登录页、控制面。

## 1. 现有同类与复用（现状盘点）

### 1.1 四种组合 × 三种业务：今天能不能做

| | 采购 | 销售 | 收款 |
|---|---|---|---|
| **Cowork 直写** | 能。赊购 RR / 现购 HP（`K/express_rules/push/mapper.py:324-339`），费用票也是 RR / HP（`mapper.py:26-33`） | 能。赊销 IV / 现销 HS（`K/express_rules/push/sales_mapper.py:382-388`） | **不能。** Cowork 不产出收款单 RE；服务端的 RE 请求契约注明由 ERP 后台发起（`K/cc_desktop/wire/request_contracts.py:54-55,83-85`）。已定口径「Cowork 不做」（记忆 collection-receipt-feature-2026-11-10） |
| **Cowork 代录** | 能。配方 RR 1-4-1 / HP 1-2（`D/express_operator/recipes/express-1.0.1.94.json:46,62`） | 能。IV 2-4 / HS 2-2（同文件 :78,94） | **不能。** RE 只登记了分期、没有配方（同文件 :113-116）；带代录标记的 RE 也一律直写（`D/worker.py:300-303`）。已定口径「代录不做收款」 |
| **ERP 直写** | **半通。** 对话里能推已有的 ERP 采购单（`E/express/cc_tools.py:659`），但**对话上传票据没有收单工具**：后端 `receive_chat → run_chat → submit_chat`（`E/integrations/intake.py:490,535,628`）只有代录在调（`E/express/operator.py:93`）；ERP CC 工具清单里没有识别、新建采购单（`E/hooks.py:652-653`） | 同左（销售单；`E/express/contracts.py:93` 只推收据） | **在分支上、未合入。** 收款支 10 个 `erp_collection_*` 工具写 RE（`收款支 E/collection/cc_tools.py:298-372`），等测试机 NLTTEST 实写通过后合入 |
| **ERP 代录** | 能。放文件 → 收单识别 → 待提交记录 → 开始卡 → 开录时提交成采购单 → 录进 Express（`E/express/operator.py:86,340-404,564`） | 能（同一条链，销售单） | **不能**（同 Cowork 代录） |

需要先补的能力（方案里不默认它们存在）：

1. **ERP 直写的对话收单工具**（采购、销售）。后端三个函数已有，缺 CC 工具声明，以及共用判定认 `I:` 前缀（`E/express/pending.py:56` 现在不认）。
   ADR-027 对照表把它标为「在做（P0-2）」（`docs/work/2026-11-08-erp-cowork-cc-parity-盘点.md` §11 第 298、308 行）。11-12 查到底的结论：
   **P0-2 只做了后端那一半，工具那一半没人在做。**
   - 已合入：P0-2 第一块「ERP 对话收单后端」493e5d0ae（原分支 `pearnly/erp-cc-recognize`）随 A 批 1 在 11-08 合入 main（a0caf85bf，分支已删），
     同批合入的 P0-4 让代录用上了它（cb66cbb44、37328cd2a）。
   - 没做：P0-2 评估第 13 节第 1 步里的直写工具 `erp_recognize_attachments`、`erp_recognized_documents` 与共用骨架 `pearnly_core.assistant.recognize`
     （评估在共享笔记 local_e395dace…/`P0-2-ERP对话识别-评估-11-08.md` 第 60、114-120 行）。main 与所有本地 / 远端分支里都搜不到这两个工具名，
     `K/assistant/` 下也没有 `recognize.py`。当时写的是「等 `cc-recognition-wait` 合入后与 P0-1 同批」（`CC线窗口-施工状态-11-08.md` 第 12、31 行），
     之后 A 线转去做代录完整、乱用兜底、写入计划，A 线各棒交接（11-09 至 11-11 第 6 棒）里都没有再提这一步。
   - 连带：P0-1「CC 不自读票据」在分支 `pearnly/cc-no-self-read` 3edfc4cf6（11-08，1 个提交，**未合入**，基于旧 main），按 11-08 定的口径要和 P0-2 工具同批上。
   - 相邻但不是这件事：`pearnly/erp-cc-sales-tickets`（11-10，未合入 7 个提交）做的是代录收单的销售票与本公司税号，不含直写工具。
   - 所以第 2 期的「ERP 直写对话收单工具」是**新做**（按 P0-2 评估第 1 步，写码 5–7 小时已含在第 2 期估时里），并把 `cc-no-self-read` 变基后同批带上。
2. **收款支合入 main**（依赖测试机实写 5 张 + 三星 9 项，见共享笔记「收款-交接-11-11.md」第 5 步）。
3. Cowork 收款、代录收款：按已定口径**不补**。菜单里对应位置的做法见 12 节第 3 问。

### 1.2 上传到写进 Express 之间，用户现在会遇到哪些提问

**后台判、模型转述的题**（`pearnly_precheck` 回事实，模型自己拼成 `ask_user` 卡，再把选项名映射回编码存回去；`K/write_precheck/api.py` 文件头
写明「问什么由 CC 定」；工具说明 `K/assistant/tools.py:79-127,172-190`）：

| 题 | 问什么 | 选项来源 | 预选 | 记到哪 | 位置 |
|---|---|---|---|---|---|
| direction | 进项还是销项 | 固定三项 | 无 | 公司 × 对方 | `C/assistant/precheck_source.py:126-134,529` |
| current_book / account_set | 同期几份账套用哪份 | 候选账套 | 记过即已定 | Cowork：公司 × 分店 × 年段；ERP：公司 × 年度 | `precheck_source.py:113,272,551`；`E/express/prepare.py:180` |
| document_policy | 转账凭条、50 ทวิ 录不录 | 录 / 不录 | 不录 | 整个租户 | `precheck_source.py:86-91,265`；`K/write_precheck/preselect.py:129` |
| party | 对方落 Express 哪个档 | Express 主档读回 + 新建 | 唯一档 / 新建 | 账套 × 对方 | `K/express_rules/push/choices.py:201-227` |
| item | 行落哪个存货 / 新建 / 记费用 | Express 存货读回 | 新建 | 账套 × 对方 × 品名 | `choices.py:272-333`；ERP `E/express/precheck.py:234` |
| account | 服务类费用科目 | 科目表 + 建议 | 建议第一个 | 账套 × 供应商 | `choices.py:343-357` |
| payment | 进项现购还是赊购 | 现 / 赊 | 付款判定漏斗 | 账套 × 供应商；可只改一张 | `choices.py:239-257`；`K/express_rules/push/payment.py:32` |
| vat | 小票的税抵不抵 | 抵 / 记费用 | 不抵 | 账套 × 供应商 | `choices.py:258-266` |
| goods_posting / stock_group / location | 走库存还是记费用、存货科目组、默认仓 | 固定 / 主档 | 有 | 公司 / 账套 | `precheck_source.py:281-298`；`choices.py:402-443` |
| service_vat | 服务品进项税口径（ERP 出） | 固定 | 历史一贯 | 公司 × 账套 × 供应商 | `choices.py:446` |
| operator_form（仅代录） | 定制菜单里这种单走哪个子菜单 | 电脑回执带回的候选 | 记过的 | 账套 × 单种 | `precheck_source.py:185-223`；`E/express/operator.py:524` |

ERP 里 payment、vat 两题实际不出（现购 / 赊购取 ERP 单上的「已付」，`E/express/push.py:292`；`E/express/contracts.py:215,336,340`）；
仓库没映射不出题、直接停在 `warehouse_unmapped`（`E/express/contracts.py:48`、`E/express/push.py:355-361`）。

**模型按工具说明自己出的题**（没有服务端清单，问不问、怎么问随模型）：

- 「可以自加问题，例如买方不是本公司」`K/assistant/tools.py:181`；挂哪家公司 `:160-161`；疑难时问缺页或怎么办 `K/assistant/direct_push.py:208-210`。
- 没挂公司问公司、同期两份问用哪份 `K/assistant/write_plan.py:45`、`C/assistant/write_scope.py:22-30`。
- Cowork：没说录到哪先问 `C/assistant/tools.py:4385-4387`、去向只问一次 `:4421-4423`、待选账套 `:1411-1416`、补页归哪张 `:4413`、
  单据不在本月怎么处理 `:4622-4623`、MR.ERP 菜单 `:4646-4650,796-800`、月份文件夹与分店 `C/companies/context.py:65,143`。
- A 线在途的 `pearnly_conversation_ledger` 也是「先用 ask_user 问账套」（`docs/work/2026-11-12-cc-conversation-ledger.md` 1.1）。

**代录另一套**：开始卡由服务端在第一句后自动投出（`K/express_operator/feed.py:205,215`），题目取旧预检回包的 `needed`
（`F/components/tool-uis/operator-start.tsx:110,146`、`F/components/tool-uis/ask-shared.ts:268-283`；Cowork `precheck_source.py:233`，
ERP `E/express/operator.py:497-536`），不预选、不显示记住的值；而写入计划判「还没定」用的是 `write_precheck`（`K/assistant/write_plan.py:99`）。
同时工具说明又让模型走 `pearnly_precheck + ask_user`，两条路并存（审计 D-1、D-2）。ERP 代录放文件另有三题（付款、每行物料、仓库，
`E/express/operator.py:340-404`），其中仓库按每条收单记录各问一次（`:370-373`，审计 D-5）。

**收款（分支）**：不出题也不走 `ask_user`，模型靠 10 个工具一步步向用户要客户、发票、方式、金额，每次存草稿，服务端回确认卡
（`ready` / `problem` 原因码 / 版本号，`收款支 E/collection/service.py:454`；判定里 `items` 恒为空 `collection/plan.py:83`）。
放行走写入计划（单号 `R:<记录>@<版本>`）。

### 1.3 已有、可以直接复用的件

- 服务端判定契约 `K/write_precheck/contracts.py`：按业务分组（`GROUPS` 里已有 book / purchase / sales / stock / service / payment）、跨单去重
  （同类同归类键只问一次并记下影响哪几张单）、三种状态（已定 / 预选 / 未定）、有条件的题（`when`）、答案展开到每张单、哪些单留下。
  **「同类票答一次全批沿用、答过记住」后台已经有，缺的只是「卡由后台直接出」。**
- 页面直接作答的白名单接口 `write_precheck.api.check / search / answer`（`K/write_precheck/api.py:57,79,92`）；「需要处理」框里的 `PendingAsk`
  已经是页面按回包出题、不经模型（`F/components/pending.tsx:393`、`ask-shared.ts:238`）。
- 写入计划三道保险（复述 → 用户下一句同意 → 才放行；改过的不写）`K/assistant/write_plan.py:151,209,259`；「录吧」快捷回复
  `F/components/assistant-ui/thread.tsx:363-375`。
- 记住的选择：Cowork `Cowork Express Choice`（账套级）、`Cowork Company Choice`（公司级）、`Cowork Workspace.document_policy`（租户级）
  （`C/push/choices.py:33`、`C/companies/rules.py:1-11`、`C/history/document_policy.py:1-8`）；ERP `ERP Express Choice`、`ERP Line Item Memory`
  （`E/express/choices.py:51-84`、`E/products/line_memory.py:1-9`）。CC、网页、LINE 写的是同一套存储。
- 复核卡的纯契约（票面行、标记码、就地改一格、确认后未改规则）在分支 `pearnly/cc-review-card`（3 个提交，`K/write_precheck/review.py`），
  **没有接口、没有前端**；main 上前端没有复核卡，也没有计划 / 结果卡（只接 `pearnly.write_plan` 事件出「录吧」，
  `F/lib/managed-agents/events.ts:101-111`）。现在所谓「复核」就是那张 `ask_user` 提问卡。
- 标准组件库 38 件（`docs/development/cc-ui-kit.md`），其中 `Questionnaire`、`Card`、`Item`、`Table`、`Field`、`ToggleGroup`、`Attachment`、
  `Empty`、`Progress`、`Badge`、`Collapsible` 业务里都还没用上。

结论：**扩展现有**。不新建第二套出题、记忆、计划；新建的只有「后台出的引导卡」这一个对话事件、输入框上方的业务菜单、复核 / 结果两张卡的前端。

### 1.4 付款（范围外，如实记录）

- 对已有应付发票的付款单 PS：两产品、两模式都没有。CC 桌面版只在注释里写了「以后付款单 PS」（`D/payment_methods.py:4`）；PS 没有代录配方
  （`express-1.0.1.94.json:114`）；ERP 采购单上的「付款」按钮只改 ERP 单据、不写 Express（`E/purchasing/api.py:64`、`E/express/push.py:122-143`）。
- 「现购」不是付款单：买的时候就付了，写成 HP、贷现金科目（`D/dbf_writer.py:580-585`）。

## 2. Frappe 原生能力

- 会话上记「本对话业务」：`Pearnly Assistant Session` 加一个 Data 字段，随 DocType JSON 走原生迁移（A 线的 `project_ledger` 同一做法）。
- 记忆、权限、白名单接口全部沿用现有 DocType 与 `frappe.whitelist`。CC 前端不是 Desk 页面，界面用 CC 标准组件库（见 10 节），不涉及 ADR-005 / 007 的 Desk 组件。

## 3. 按类通用

- 类：**「CC 对话里把单据录进账本之前要人定的事」**。一份清单契约、一张卡、一个作答接口，四种组合与三种业务共用；产品只声明「这种业务要哪些项、
  每项怎么自动定」，不写界面、不写题目流程。
- 一起覆盖：Cowork 直写、Cowork 代录、ERP 直写、ERP 代录的采购与销售；ERP 直写的收款；代录中途的选择卡（`operator_choose`）改用同一张卡的外观。
- 不覆盖：LINE、文件夹定时、网页待处理页（它们已有各自的服务端出题界面，11-09 选 A）；MR.ERP 专属项由 D 线按同一契约声明。
- 不针对某公司、某账套、某商品：清单每一项的来源都是「本对话账套 + Express 主档读回 + 记住的选择 + 票面」，没有写死值。

## 4. 上下游数据

### 4.1 每种业务的固定清单

「怎么自动定」按先后顺序试，前一个定了就不看后面；全部定不了才出题。**账套、公司、月份、分店是对话给定的输入，后面任何一步不重判**
（A 线 `pearnly/cc-conversation-ledger` 落地后由 `ledger.of_session()` 提供；记忆 cc-target-is-selected-ledger）。

**采购**（Express 赊购 RR / 现购 HP；写 APTRN、ISVAT、GLJNL、APBAL，`D/dbf_schema.py:64-65`）

| # | 必须有的 | 自动定的来源（按顺序） | 定不了才问 | 记住的粒度 |
|---|---|---|---|---|
| 1 | 公司、账套 | 本对话账套 | 账套 0 本或多本时问一次（A 线） | 会话 |
| 2 | 这张票是采购 | 菜单选了采购 → 记住的方向 → 本公司税号在买方（`K/express_rules/contracts/push.py:154`） | 票面看是销售、或买卖双方都不是本公司（审计 D-3） | 公司 × 对方 |
| 3 | 录不录（转账凭条、50 ทวิ 等非发票） | 租户记过的 | 第一次遇到 | 租户 |
| 4 | 供应商在 Express 的编号 | ERP 供应商上记的 Express 编号（`E/express/push.py:153-163`）→ 记住的 → 主档同税号唯一档 | 同税号几个档；一个都没有（预选「新建」） | 账套 × 对方 |
| 5 | 现购还是赊购 | ERP 单上的「已付」→ 这张单上人定的 → 记住的 → 票面写明 → 付款判定漏斗 | 漏斗没把握 | 账套 × 供应商（可只改一张） |
| 6 | 每行：存货 / 费用科目 | ERP 物料上记的 Express 编号 → 记住的行对照 → Express 同名存货 → 公司规则 → Express 历史一贯 | 行对不上（预选「新建」或建议科目第一个） | 账套 × 对方 × 品名；科目按供应商 |
| 7 | 税：票种、抵不抵、服务品口径 | 票种判定 → 记住的 → 账套历史一贯 | 小票 / 简易税票第一次遇到 | 账套 × 供应商 |
| 8 | 走库存还是记费用、存货科目组 | 记住的 | 这本账套第一次建库存品 | 公司 / 账套 |
| 9 | 仓位 | 行上带的 → 记住的 → 默认仓 → 主仓（ERP：仓库上记的 Express 仓库码） | 在用的仓 ≥2 且没记过；ERP 没映射现在是直接停住，改成出题（需主会话定，见 12-6） | 账套 |
| 10 | 分店、部门 | 账套按分店；部门用记过的或单据簿默认（`choices.py:230-236`） | 不问 | — |
| 11 | 单号、日期、金额（票面） | 识别结果 | 不出题，在复核卡里标红、就地改（11-09 口径） | — |
| 12 | 代录：走哪个子菜单 | 记住的 → 菜单树只有一项 | 定制菜单有多项 | 账套 × 单种 |

**销售**（Express 赊销 IV / 现销 HS；写 ARTRN，`D/dbf_schema.py:76-80`）：同上表 1–4、6、9–12（对方是客户）；差别是

| # | 必须有的 | 自动定的来源 | 定不了才问 |
|---|---|---|---|
| 5' | 赊销还是现销 | ERP 单上的「已收」；Cowork 默认赊销 IV（`sales_mapper.py:388`） | 不问 |
| 6' | 收入科目 | 映射 → 任务默认 → 连接默认 | 都没有时现在是 `no_revenue_account` 直接停（`sales_mapper.py:256`），改成出题（见 12-6） |
| 7' | 本公司是否增值税登记户、免税票怎么拆 | 公司税号与历史（`preselect.vat_registered`） | 第一次 |

**收款**（Express 收款单 RE；仅 ERP 直写；清单取自收款支 `E/collection/service.py`）

| # | 必须有的 | 自动定的来源 | 定不了才问 | 记住 |
|---|---|---|---|---|
| 1 | 公司、账套 | 本对话账套（**解决 A11**：列发票、列方式、建草稿三步都用它，不再一步按今天、一步按单据日期） | 同期两份时与采购销售同一道账套题（**解决 A10**：`choose_book` 有了作答入口） | 会话；公司 × 年度 |
| 2 | 客户 | 用户话里的编号或部分名在 Express 客户里唯一命中；上传的转账凭证不带客户 | 没命中或多个命中：卡里搜索选一个（`service.customers`） | 不记 |
| 3 | 收哪几张发票、各收多少 | 该客户未收清发票只有一张且金额与用户说的一致 | 其余情况：卡里勾发票、填本次收（`service.open_invoices`） | 不记 |
| 4 | 收款方式：现金 / 转账种类 | 账套里只有一种可用方式；上传了转账凭证 → 转账 | 多种方式（`service.methods`） | 不记（11-10 口径没说记，见 12-5） |
| 5 | 转账凭证 | 对话里的附件 | 选了转账而没附件：卡里「上传」 | — |
| 6 | 没收够时：再加一种方式 / 折扣 / 下次再收 | 金额正好 | 合计小于所选发票未收额 | 不记 |
| 7 | 销售员、部门 | 第一张发票的原值（已定口径） | 不问，卡里可改 | — |
| 8 | 日期 | 今天 | 不问，卡里可改 | — |

收款没有「上传一批票」这一步，它的卡是一张**表单卡**（客户 → 发票 → 方式 → 合计），由后台按 `service.card` 的 `problem` 决定还缺哪一段；
放行仍是「复述 → 录吧」。

### 4.2 ERP 内与 Express 映射

- ERP：采购单、销售单的提交仍走网页同一个入账函数（`submit_chat` → `entry.post`），库存流水、收发存不变；收款写 `ERP Collection` 记录（收款支）。
- Express：写入内容、单号规则、读回核对全部不变；本方案只改「写之前怎么问」。接了 Express 的公司仍以 Express 主档读回为准。
- CC 桌面版：**载荷不变**，不升版本；收款依赖已发的 1.12.20（载荷 v20）。
- Cowork / DMS：DMS 不涉及。Cowork 网页复核页、LINE 与 CC 共用记忆存储，本方案不改存储结构，三端互相看得见对方记下的选择（现状如此）。

## 5. 隔离与权限

- 卡的取数与作答都过产品的 `allowed`（`K/write_precheck/api.py:35-38`），再由产品按单据核；本对话公司、账套由服务端从会话取，
  **不接受模型或页面传别家的**（接审计第 3 批 C1）。
- 页面直接作答（不经模型）走白名单 `answer`，只认本人本对话那张挂着的卡（复用 `toolrun.decide` 只认本对话本人 `awaiting_approval` 的判法）。
- 收款沿用收款支的岗位「收款员」与 `access.py`；代录会话不挂收款工具（收款支已锁测试）。
- 不串公司：菜单、卡、复核、结果的抬头都显示「公司 · 账套」，来源是会话，不是票面。

## 6. 入口：网页、手机、LINE、拍照

### 6.1 输入框上方的业务菜单

- **位置**：输入框正上方那一行，就是现在「录吧」快捷回复所在的位置（`F/components/assistant-ui/thread.tsx:177-189`）。同一行同一时刻只显示一种：
  有待确认的计划时显示「录吧」；有卡挂着或正在录入时不显示；其余时候显示菜单。
- **内容**：采购 / 销售 / 收款，图标 + 两个字（lucide：`ShoppingCart`、`Tag`、`Wallet`）。只列「这个产品 × 这个模式真能做」的业务，由服务端按能力声明给出：
  Cowork 两种模式 = 采购、销售；ERP 直写 = 采购、销售、收款；ERP 代录 = 采购、销售。直写、代录是同一个组件、同一份声明，只是列表不同。
- **新对话**：问候下面就是输入框，菜单在它上方（示意 01）。没挂公司时点业务 → 先出公司选择（现有公司选择窗，不新造）。
- **进行中的对话**：一批录完、对话空闲时菜单重新出现（示意 05）。选中后变成一个带「×」的选中态，可取消。
- **手机**：同一行，三个按钮一行放得下（示意 07 第 1 台）；将来业务多了横向滑动，不换行、不进「…」。
- **选了以后**（用户 11-12：「上传区是不是有点冗余了，可以用文案代替，用户可以在对话框上传吧」）：**不出卡**。菜单那一行变成已选的「采购 ×」；
  输入框的占位文字从「回复…」换成按业务的一句（采购「上传采购票据」、销售「上传销售票据」、收款「输入客户名称或编号」）；上传就用输入框本来的入口
  （加号、拖入、粘贴）（示意 02、07 第 2 台）。业务记在会话上，这一批上传的票默认按它定方向。
- **收款同一原则**：点「收款」后也不先出卡，用户直接在输入框打客户名或编号。后台在 Express 客户里查：唯一命中 → 直接出收款表单卡（客户已填好、可改，
  下面是该客户未收清发票、收款方式、合计，示意 06）；多个命中 → 引导卡里一题列出候选选一个；没命中 → CC 一句话说没找到。
  表单卡本身保留：它承载勾发票、填金额、选方式这些选择和数据，不是提示。
- **其余几张按「只起提示作用的卡不要」过了一遍**：引导卡（承载选择）、复核表（承载数据与就地修改）、结果表（承载每张单的结果与账本单号）保留；
  「已识别 N 张」本来就是一行过程文字，不是卡；没有别的纯提示卡。
- 代录：选业务后的引导卡就是现在的开始卡位置（资料区），上传走现有 `uploads` 链。

### 6.2 选了业务之后：一张卡收成一个入口

```
菜单选业务 ─→ 上传 ─→ 识别（现有进度）─→ 后台判定 write_precheck.card
                                              │ 全部已定：不出卡
                                              └ 有未定 / 预选项：出「引导卡」（后台出题，页面直接作答）
                                                      ↓
                                   复核卡（票面表，可就地改，标红不拦）
                                                      ↓
                          CC 一句话复述（计划）→ 用户「录吧」→ 写入 → 结果卡
```

- **引导卡 = 现有三样东西合成一个**：
  - 现在直写的「`pearnly_precheck` → 模型拼 `ask_user` → 模型映射回编码 → `pearnly_precheck_answer`」四步，改成一步：新共用工具
    `pearnly_guide`（暂名）由**服务端**把 `write_precheck.card` 的未定 / 预选项生成卡（题目、选项、推荐理由按本轮语言由服务端出好），
    作为一张等待卡投进对话；用户在卡上点选，**页面直接调 `write_precheck.answer`**，模型只收到结果「已答几项、哪些单留下」。
  - 代录开始卡：改成渲染同一张卡（审计第 4 批 D-1、D-2），删掉 `needed` 旧回包那条路。
  - `ask_user`：保留给自由聊天里的非业务问题；录单流程里不再用（机制见 6.4）。
- 一张卡问完：一题一页可翻（现有口径），「第 n 项，共 m 项」；同一供应商 5 张票只出一题并写明张数（示意 03）；已定的收在「已定 N 项」里可展开改；
  答过的按现有粒度记住，下次直接进「已定」。
- 复核卡、结果卡是新做的前端（复核契约用 `pearnly/cc-review-card` 那一份）。一批一张，不是一张票一张。
- 卡上没有「开始」按钮；开始 = 用户一句话或点「录吧」（11-10 口径不变）。

### 6.3 没点菜单直接上传、一批里混着几种

按「不会用又乱用」设计，**菜单只是让新手少走一步，不是必经之路**：

| 情况 | 处理 |
|---|---|
| 没点菜单直接上传（输入框占位仍是「回复…」） | 照常识别；后台按票面判方向（本公司税号在买方 / 卖方），判得出的不问；判不出的在卡里出一题。之后同一条路 |
| 选了采购，但一批里有几张是销售票 | 卡里一题：「这 3 张票面是销售」→ 按销售录 / 按采购录 / 这次不录，不预选（去留类，见 12-1） |
| 一批混着采购、销售 | 一张卡按业务分组出题（`GROUPS` 已有），复核卡按业务分段，复述里分别报张数 |
| 混进转账凭条、50 ทวิ | 录不录按租户记过的；第一次出一题 |
| 混进收款凭证（ERP 直写） | 不自动建收款；卡里列出「这 2 张是转账凭证」，结果里提示可点「收款」处理 |
| 在 Cowork 或代录里想录收款 | 菜单里没有收款；用话说了，CC 照实回答这里做不了（不叫用户换模式，11-08 口径） |
| 同一批票重复上传 / 已录过 | 先核账本，在则卡里问「跳过 / 再录一次」，不预选（11-10 口径） |
| 卡挂着时用户直接打字 | 按输入框窗口的新行为：这句话当场发出，卡记为未答（`pearnly/cc-composer-send-ae62d2`）；CC 听懂是答案就调同一个作答接口记下，听不懂就把卡重新投出 |
| 卡挂着时换了公司 / 账套 / 月份 | 旧卡作废，按新账套重判重出（A 线 `ledger_changed`） |
| 点了业务又不上传、直接问别的 | 当普通聊天答；引导卡留在对话里，菜单恢复 |

### 6.4 模型负责什么、不再负责什么，怎么保证

- **仍负责**：听懂人话（「这家都是现金」「第 3 张不要」→ 调作答接口，等同点卡）；解释卡上的事实；写前一句话复述计划；报结果；自由聊天。
- **不再负责**：决定问什么、选项有哪些、推荐哪个、题目和选项的文字；选项名与编码之间的来回映射；判断写哪本账套、哪个方向。
- **三道机制**：
  1. 工具说明：删掉 1.2 里列的每一处「先问」「可以自加问题」，改成「缺什么调 `pearnly_guide`，不自己出题」。
  2. 服务端拒绝：`ask_user` 已有拒绝钩子（`K/assistant/ask.py:37`）。本对话有待写单据、或有引导卡未答时，`ask_user` 一律拒绝并回「用 pearnly_guide」；
     推送 / 开始写入时仍有未定项 → 拒绝并带回那张卡（现有 `write_plan` 的 undecided 判法）。
  3. 测试：假智能体后端的流程里让「模型」故意调 `ask_user` 出业务题，断言被拒；真模型四格场景断言整段对话没有 `ask_user` 调用、卡全部来自 `pearnly_guide`。

### 6.5 其它入口

- LINE、拍照：不涉及（CC 输入框的拍照 / 附件沿用现有上传，相机三步不变）。CC 桌面版与网页是同一份前端，自动跟着走。

## 7. 共用件与连带回归

- 会动到的登记共用件（`scripts/impact/shared-parts.toml`）：`assistant`（:453，`K/assistant/**`）、`write-precheck`（:229）、`cc-composer`（:387，
  `thread.tsx`、`attachment.tsx`）、`cc-ui-kit`（:378，只在需要给标准件补外观槽时）。施工时按实际改动跑 `python3 scripts/check-impact.py --files …`，
  清单贴进各期的任务记录；提问卡、开始卡的前端文件目前没单独登记，本方案把新的引导卡登记成共用件 `cc-guide-card`。
- 产品地图（`scripts/impact/product-map/cowork.toml`、`erp.toml`）要增改：
  - 新增 cc-action：`cowork.cc.action.guide-menu`、`erp.cc.action.guide-menu`（业务菜单）、`*.cc.action.guide-card`（引导卡）、`*.cc.action.review`（复核卡）；
  - 新增 cc-tool：`pearnly_guide`（两产品）；ERP 新增收单识别工具两行、收款支合入时带的 10 个 `erp_collection_*`；
  - 修改：`record-reply`（与菜单同一行的互斥规则）、cc-tool `ask_user`（录单流程里不用）。
- 新增流程（`tests/flows/registry.toml`）：`cc.guide.cowork-direct`、`cc.guide.cowork-operator`、`cc.guide.erp-direct`、`cc.guide.erp-operator`、
  `cc.guide.erp-collection`、`cc.guide.misuse`（混批、没点菜单、模型自己出题被拒）。现有 `cowork.cc.ask`、`cc.operator`、`cowork.cc.record-reply`、
  `cowork.pending.operator`、`cowork.cc.send-while-waiting` 受影响、照跑。
- 现有全链路空白：直写「上传 → 识别 → 计划 → 写入」main 上没有一条端到端流程（前端盘点没查到），本方案补上。

## 8. 存储与成本

- 新增数据：会话一列（本对话业务）；卡的答案沿用现有记忆表，不新增表；复核卡的就地修改写回现有票面存储（Cowork `storage.update_pages`、
  ERP 收单记录）。无新文件、无新外部调用、**无新增固定月费**。
- 模型用量：每批少两到三次工具往返（模型不再拼卡、映射答案），只减不增。识别费用不变。

## 9. 删除、作废、追溯

- 卡过期（换账套、票被改、任务结束）就作废，不留可点的旧卡；答案已记进记忆的保留（在公司资料「记住的选择」里可看可删，现有入口）。
- 对话删除：卡随会话事件一起删；ERP 待提交的收单记录、收款草稿按各自现有规则（未写入可撤回）。
- 已写进 Express 的单：不做原生作废（已定口径）。追溯沿用现有写入记录与「同意依据 `cc:<对话>:<消息序号>`」。

## 10. 文案与官方 UI

### 10.1 每块界面用哪个标准组件

| 界面 | 标准组件（`components/ui`） | 示意 |
|---|---|---|
| 业务菜单 | `Button variant="outline"` + lucide 图标（与「录吧」同一行、同一种按钮）；已选的一项 `variant="secondary"` 带「×」。不用 `ToggleGroup`：库里它是分段开关的样子，和「录吧」并排不一致 | 01、07 |
| 选了业务之后 | 不出卡；输入框（`thread.tsx` 现有输入框）的占位文字按业务换；已传文件沿用 `Attachment`，识别进度沿用现有上传卡 / `Trace` | 02、07 |
| 引导卡（出题） | `Card` + `Questionnaire`（进度、题目、选项、跳过 / 下一步）+ `Badge`（推荐）+ `Collapsible`（已定 N 项）+ `Button size="xs"`；大列表在卡内用 `InputGroup` + `Item` 列表就地搜索（卡内不放弹出层，ccs 规矩） | 03、07 第 3 台 |
| 复核卡 | `Card` + `Table` + `Checkbox` + `Input`（`aria-invalid` 标红）+ `Badge`；手机窄屏每张票一行 `Item` | 04、07 第 4 台 |
| 「录吧」 | `Button variant="outline"` | 04 |
| 结果卡 | `Card` + `Progress` + `Table` + `Badge` | 05 |
| 收款表单卡 | `Card` + `Field` + `InputGroup`（搜客户）+ `Table` + `Checkbox` + `Input` + `RadioGroup` + `Item` / `Attachment`（凭证） | 06 |

- 库里**没有步骤条**，不补：用 `Questionnaire` 自带的「第 n 项，共 m 项」表达进度。本方案**不需要从 shadcn 补新件**。
- 碰到哪换到哪，一并换掉的手写控件（`scripts/frontend-kit-baseline.json` 现值）：`tool-uis/ask-card.tsx`（K1 8、K4 6）→ 改用 `Questionnaire`；
  `tool-uis/operator-start.tsx`（K1 5、K4 4）→ 渲染引导卡后整段删除；`tool-uis/operator-card.tsx`（K1 1、K4 2）；`run/upload-card.tsx`（K1 1、K4 1）；
  `assistant-ui/thread.tsx` 里「录吧」那一处（文件现值 K1 5、K4 5，只换本方案碰到的）。`approval-bar.tsx`、`approval-sheet.tsx`、`mode-switch.tsx` 不在本方案改动面，不动。
  基线随提交 `--update` 下降。
- 样式只走 `globals.css` 变量与 `cc-ui-*` 槽；新外观槽（如菜单行）写在文末「标准组件库」块，过 `check-frontend-tokens.py`、`check-frontend-kit.py`。
- `01`–`07` 是静态页按 globals.css 浅色主题的色值画的版式示意；`真组件-*.png` 是真的标准组件渲染（电脑中文、电脑泰文、手机中文、深色引导卡），
  过了 `check-frontend-kit.py`、`check-frontend-tokens.py`、`check-copy.py` 与 `tsc`。真组件预览里看到的两处要在施工时处理：手机宽度下结果表、
  收款发票表太宽（改成同复核卡一样每行一个 `Item`）；`Questionnaire` 的预选用 `QuestionnaireChoice defaultChecked`。没有参照 Claude 的任何界面。

### 10.2 文案

新增（四语，官方简短；业务名复用已有词条 `op.group.purchase / sales`，`F/i18n.ts:1103,1180,1257,1334`）：

| 键（暂名） | zh | en | th | ja |
|---|---|---|---|---|
| guide.collection | 收款 | Collection | รับชำระ | 入金 |
| guide.placeholder.purchase | 上传采购票据 | Upload purchase documents | อัปโหลดเอกสารซื้อ | 仕入書類をアップロード |
| guide.placeholder.sales | 上传销售票据 | Upload sales documents | อัปโหลดเอกสารขาย | 売上書類をアップロード |
| guide.placeholder.collection | 输入客户名称或编号 | Enter a customer name or code | ป้อนชื่อหรือรหัสลูกค้า | 得意先名またはコードを入力 |
| guide.decided(n) | 已定 n 项 | n decided | กำหนดแล้ว n รายการ | 決定済み n 件 |
| guide.review | 复核 | Review | ตรวจทาน | 確認 |
| guide.more(n) | 还有 n 张 | n more | อีก n ใบ | ほか n 件 |
| guide.result(a,b) | 已录入 a 张，b 张未录 | a entered, b not entered | บันทึกแล้ว a ใบ ไม่ได้บันทึก b ใบ | 記帳 a 件、未記帳 b 件 |
| guide.entered | 已录入 | Entered | บันทึกแล้ว | 記帳済み |
| guide.recorded | 已录过 | Already entered | เคยบันทึกแล้ว | 記帳済みあり |

题目与选项的文字由服务端按本轮语言出（接审计第 5 批 E2），走各产品 `i18n/messages.tsv`；施工时出完整清单过 `check-copy.py --report`。

**改已有文案**（要主会话转用户看，现在只列、不改）：

1. 「推荐」标记：现在是选项名后缀「(推荐) / (Recommended) / (แนะนำ) /（おすすめ）」（`F/i18n.ts:1441-1453`），改成独立徽章「推荐 / Recommended / แนะนำ / おすすめ」，
   同时修掉日文不预选的缺陷（审计 D-4；`ask-shared.ts:44` 的正则不认日文写法）。
2. 代录开始卡现有的题目文字随旧回包一起退场，换成服务端出的同一套题目文字（清单施工时给）。

## 11. 测试与验收

- 单元（先写、旧代码红）：清单契约（每种业务每一项「自动定的顺序」与「定不了才问」）；同类合题与张数；有条件的题；去留类不预选；
  `ask_user` 在录单流程里被拒；会话业务与票面方向不一致出题；收款 A10 / A11 回放（同期两份账套能答、三步同一本）。
- 流程（实验栈、假智能体后端，7 节列的 6 条）：每种组合至少一条新手端到端——
  1. Cowork 直写 · 采购：新对话 → 点「采购」→ 传 12 张 → 一张卡两题 → 复核改一格 → 「录吧」→ 结果。
  2. Cowork 代录 · 销售：代录模式同样六步，断言交给电脑的是复述里的那几张。
  3. ERP 直写 · 采购：同 1（依赖 ERP 对话收单工具）。
  4. ERP 代录 · 采购：同 2，断言仓库题一批只问一次（审计 D-5）。
  5. ERP 直写 · 收款：点「收款」→ 搜客户 → 勾两张发票 → 转账 + 凭证 → 「录吧」→ RE 号。
  6. 乱用：不点菜单直接传混批；卡挂着打字；模型自己出业务题。
- 三条硬断言（每条流程都带）：
  - **全程没有模型自己出的业务题**：对话事件里 `ask_user` 调用数 = 0；所有卡的来源是 `pearnly_guide`。
  - **同一件事只问一次**：同一归类键（类 + 对象）在整段对话里最多出现在一张卡的一题里；第二批同供应商的票不再出这题（进「已定」）。
  - **写进的是本对话账套**：写入请求的连接 + 账套 = 会话 `project_ledger`；换一本推送被服务端拒（沿用 A 线 R2-5 回放）。
- 真模型：四格 + 收款一格，各跑新手流程一遍，查「听懂人话代答」（「这家都是现金」）与复述；约 3 美元一轮，记台账。
- 测试机实写：TEST 账套采购 / 销售各 3 张（直写、代录各一轮）、NLTTEST 收款 2 张；先问主会话、登记占用表；白天做。
- 没法在实验栈验的：真 Express 界面代录的耗时与弹框、手机真机上卡浮在键盘上方的表现（三星走 LINE 入口复验）。
- 验收：主会话按示意图逐张对；用户亲自看的只有「新手从点菜单到录完」电脑、手机各一遍。

## 12. 要主会话 / 用户定的点

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | **「CC 预选」（11-04 / 11-05）与「不做默认勾选」（11-10）怎么并存。** 读原文：11-10 用户反对的是 CC 把「录过的票」默认不勾、替他决定去留；同一天他又说「卡片是预选，自然语言才是兜底」。我的理解是两条不冲突，分的是题的种类 | 分三档：① 后台能定的事实（记住的、主档唯一）不问，收进「已定」可改；② **怎么录**的题（落哪个档、现购赊购、哪个科目）推荐项排第一并预选，但要用户点了「下一步 / 提交」才算数；③ **做不做**的题（录不录、再录不再录、按哪种业务录、哪本账套）不预选，必须明选。另：现有代码「预选项没答也照预选值写」（`pearnly_precheck_answer` 说明、`KEPT_PROPOSALS`，`K/write_precheck/api.py:121`）与 11-10 口径有出入，建议改成「卡上没点过的预选项不生效」 | 卡的行为定不下来，第一期做不了 |
| 2 | 菜单是不是必经之路 | 不是。没点菜单直接上传照常走同一条路；菜单只是预先说明业务 | 做成必经会挡住老用户和乱用的用户 |
| 3 | Cowork、代录里菜单要不要显示「收款」 | 不显示（那里做不了；显示了点进去只能说做不了）。Cowork 两种模式与 ERP 代录只有采购、销售 | 显示会给新手一个走不通的入口 |
| 4 | 第一期先做到哪 | 菜单 + 引导卡收成一个入口（Cowork 直写、Cowork 代录、ERP 代录的采购销售）。这一期做完用户就能看到「点业务 → 上传 → 一张卡 → 录吧」；复核表、结果卡、ERP 直写收单、收款放后面两期 | 一次全做要等 A 线、收款线都合入，至少晚 2–3 天才看得到变化 |
| 5 | 收款方式要不要按客户记住（上次这家用转账就预选转账） | 不记、不预选（钱的事每次明选；11-10 收款口径也没说记） | 小 |
| 6 | 现在直接停住、不出题的三种情况改不改成出题：ERP 仓库没映射（`warehouse_unmapped`）、没有收入科目（`no_revenue_account`）、没有进项税科目 | 改成出题（「缺了没法正确写入」的必要信息正是卡该问的），答案按账套记住 | 不改的话新手遇到这三种还是卡在一句看不懂的原因上 |
| 7 | 选了「采购」却传了销售票时，默认听谁的 | 不默认，卡里问一次（见 6.3） | — |
| 8 | 菜单三个词的四语，以及 10.2 两处改已有文案 | 按表；「推荐」改徽章需用户点头 | 文案闸门 |

### 主会话 11-12 批复

- 已定（照建议）：第 2 问菜单不是必经之路；第 3 问 Cowork、代录不显示「收款」；第 5 问收款方式不记、不预选；第 6 问三种直接停住的改成出题并按账套记住；
  第 7 问选了采购却传销售票在卡里问一次。
- 已定：审计第 4 批并入本方案第 1 期由一个窗口做（A 线不做第 4 批）；分支 `pearnly/cc-review-card` 归第 2 期接手，变基沿用，不另起一套。
- 等用户：第 1 问（预选三档与「没点过不生效」）、第 4 问（第一期范围）、第 8 问（两处改已有文案）和整体样子。用户回复前不开工。

## 13. 工作量与排期

估时 = 写码 + 机器跑（gate、候选离线、流程每轮约 30 分钟、CI）+ 等人；顺序即依赖，不排日期。

| 期 | 做什么 | 依赖 | 写码 | 机器 | 等人 | 费用 |
|---|---|---|---|---|---|---|
| 0 | 别的线：A 线本对话账套 + 写入只走计划合入；输入框分支合入；审计第 3 批（公司不由模型填） | — | — | — | — | — |
| 1 | **菜单 + 引导卡一个入口**：`pearnly_guide`、会话记业务、服务端出题文字四语、页面直接作答、代录开始卡并入（= 审计第 4 批）、`ask_user` 拒绝、去留类不预选、换标准组件；Cowork 直写 / 代录、ERP 代录的采购销售；流程 3 条 + 乱用 1 条 | 0 期（A 线先合，否则同改 `K/assistant/tools.py`、`sessions.py`、`C/assistant/tools.py`、`precheck_source.py`） | 26–32 小时 | 6–8 小时 | 主会话审卡的行为（12-1）与文案 | 真模型三格约 2 美元 |
| 2 | **复核卡 + 结果卡**（接 `pearnly/cc-review-card` 契约，做接口与前端）；**ERP 直写对话收单工具**（采购、销售）并接进同一条路；流程补 ERP 直写 | 1 期 | 18–24 小时 | 6 小时 | — | 真模型一格约 1 美元 |
| 3 | **收款接进引导**：收款表单卡（后台按 `problem` 出缺的段）、菜单加收款、A10 / A11、工具说明删「一步步问」 | 收款支合入 main；1 期 | 10–12 小时 | 4 小时 | 收款窗口对接口 | 真模型约 1 美元 |
| 4 | 验收：五格真模型全流程、测试机实写（TEST 采购销售、NLTTEST 收款）、三星手机一遍、产品地图与共用件登记收口 | 1–3 期 | 4–6 小时 | 8 小时（含测试机约 3 小时） | 测试机占用审批；用户看一遍 | 真模型约 3 美元；识别约 50 次调用约 10 泰铢 |

合计写码约 58–74 小时、机器约 24–26 小时、测试费用约 7 美元。第 1 期做完用户就能感到明显变化。

### 与在途工作的交叠（同一文件只一个写者）

| 文件 | 谁在改 | 本方案动什么 | 安排 |
|---|---|---|---|
| `K/assistant/tools.py`、`sessions.py`、`contracts.py`、`api.py` | A 线（账套）、输入框分支（`sessions.py`、`ask.py`、`toolrun.py`） | 新工具声明、会话业务字段、`ask.py` 拒绝钩子 | 等两支合入后再开；1 期开工前对一遍 |
| `C/assistant/tools.py`、`precheck_source.py`、`write_scope.py` | A 线（未提交） | 删模型出题的说明；题目文字改服务端出 | 同上 |
| `K/express_operator/feed.py`、`api.py`；`E/express/operator.py`；`C/assistant/operator_source.py` | 审计第 4 批（未开） | 开始卡改同一张卡 | 第 4 批并入本方案 1 期，由一个窗口做 |
| `F/components/assistant-ui/thread.tsx`、`attachment.tsx`、`F/lib/managed-agents/reducer.ts`、`globals.css` | 输入框分支 | 菜单行、引导卡事件 | 等它合入 |
| `F/components/tool-uis/ask-card.tsx`、`ask-shared.ts`、`operator-start.tsx` | 无人 | 换 `Questionnaire`、删开始卡旧出题 | 本方案 |
| `E/collection/**`、`E/express/precheck.py` 的 `R:` 分支 | 收款窗口 | 3 期才动 | 等收款支合入；这两天不碰 |
| `K/write_precheck/review.py` | 分支 `pearnly/cc-review-card`（3 提交，未合） | 2 期接着做 | 先问主会话那支归谁 |

## 没查到 / 不确定

- ~~ERP 对话收单工具（ADR-027 P0-2）现在有没有窗口在做~~：11-12 已查清，见 1.1「需要先补的能力」第 1 条（后端已合入、直写工具没人做）。
- `frontend/src/pages/cowork/IntakePage.vue`（另一套 Cowork 网页录入页）现在是否在用：没查证，本方案不依赖它。
- 收款方式是否应记住、一笔钱付几家客户：收款线待岳父答复的事项，本方案按「不记、不做」。
- 示意图里的 Express 单号、供应商、金额是编的示例，只为看版式。
