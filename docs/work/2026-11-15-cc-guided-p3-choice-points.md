# CC 引导式 P3（Cowork 常规功能）：开始前的选择点盘点（11-15）

用途：P3 开工前先盘清 Cowork 在 CC 里能做的全部常规功能（上传识别推送、待处理与改单、POS、回读、对账、报税草稿、盘点、定时任务、公司备注与记住的选择、删除类），按「步骤 × 值来源 × 服务端校验」列出每个要人定的点，标出现在还靠模型复述的值。本文是只读子代理盘点，没改任何行为；格式与口径照 P2 那份（`guide:docs/work/2026-11-15-cc-guided-p2-choice-points.md`）。

只读盘点，读的是 git 对象：

- `origin/main` = `9c1e783ad`
- `origin/pearnly/cc-guided-phase1` = `27b9d6789`（第 1 期：`write_precheck/guide.py`、`assistant/guide_tool.py`、`choices.resolve(guided=)`、`contracts/required_fields.py`）
- `origin/pearnly/cc-guided-core` = `909971dbb`（`express_operator/choice_points.py`、`assistant/stop_exits.py`）
- `origin/pearnly/cc-guided-collection` = `92f6033f8`（引导声明 `assistant/guide/declaration.py`、收款引导 `collection/guide.py`、Cowork 声明 `assistant/guides.py`）

下文的 `main:`、`P1:`、`core:`、`guide:` 分别指这四个分支，行号是该分支上的行号。没写分支前缀的 Cowork 文件都是 `main:`，路径省掉前缀 `apps/pearnly_cowork/pearnly_cowork/`；共用层写成 `pearnly_core/…`（= `apps/pearnly_core/pearnly_core/…`）。`assistant/tools.py`、`company_tools.py`、`write_scope.py`、`pending_api.py` 在 main 与 P1 上行号相同。`precheck_source.py` 在 P1 上第 23 行后多 3 行、`_goods_item` 里再多 6 行。

## 0. 先说结论

1. **Cowork 的单写账前是一张识别记录**（`Cowork OCR History`），写进账本的值来自识别结果、账套记忆、公司规则、Express 历史。但和 ERP 不同，**识别之后模型还能改票面**：`cowork_edit_document` 改金额、税号、方向、入账类型、明细；`into_document` 补页重读后覆盖原单；识别时和推送时，模型还会给 `direction / posting_kind / payment` 参数。这些都写进账本（第 4 节第 1–5 条）。
2. 推送前要人定的项，`precheck_source.py:81 document` 已经一次收齐：录不录、方向、待选账套、商品单入账方式、写账规则 9 类（对方 / 存货 / 科目 / 收款科目 / 部门 / 现赊 / 抵不抵 / 服务品税口径 / 仓）、代录子菜单、结果不明。第 1 期的 `pearnly_guide` 能直接出这些题。和 ERP 不同的是，**Cowork 直写也在引导菜单上**（`P1: hooks.py:721`；`guide: assistant/guides.py:8-19` 两项都是 `chat + operator`）。但同样的题还能走模型路：`cowork_choose_document_policy`（有允许卡）和 `pearnly_precheck_answer`（口头作答）。
3. **判定卡不出、只能靠模型的三类**：MR.ERP 导入菜单 / 默认值（`cowork_mrerp_setup`，有允许卡）、存货科目组（`cowork_stock_group`，有允许卡）、POS 认列（`cowork_pos_import / cowork_pos_layout`，没有允许卡）。
4. **会改账本、或改变写不写，却既没有允许卡、也不经写入计划的**：`cowork_edit_document`、`cowork_recognize_attachments`（`into_document / direction / posting_kind / payment`）、`cowork_resolve_push_result`、`cowork_delete_documents`、`cowork_run_folder_task`（它还不过写入闸）、POS 认列答案。
5. **采购 / 销售以外没有引导声明**（`guide: assistant/guides.py` 只有 purchase、sales 两项）：POS、回读、对账、报税草稿、盘点、定时任务、公司规则 / 笔记、看板都还没有。其中报税草稿、回读、补页、POS 认列**未查到网页 / LINE 的同一函数**（这几个模块里没有 whitelist），出选项只能用 CC 现在调的那个服务函数。
6. 代录范围按用户 11-14 口径，只做采购 / 销售。Cowork 声明代录的账本里含 `mrerp`，但 `main: pearnly_core/express_operator/contracts.py:74 MRERP_OPERATOR_ENABLED = False`，交单回 `operator_express_only`（`assistant/tools.py:1064-1069`）。Cowork 工具里只有 `cowork_push_documents` 写了 `modes: ["chat"]`（`:4888`），其余在代录会话里也能调（`P1: pearnly_core/assistant/tools.py:307`）。
7. Cowork 自己的停下原因码都**不在 `core: stop_exits.py`**，`core: scripts/check-stop-exits.py:79-89` 的 `SOURCES` 也不扫 Cowork 的登记处，包括 `document_policy_needed_*`、`posting_needs_review`、`account_set_not_chosen`、`not_this_company_book / not_this_conversation_book`、`no_company`、`waiting_for_cc`、`layout_needed`、`operator_express_only`（第 3 节）。

---

## 1. 主表

列说明同 P2：

- **经手**：「服务端原值」= 选项码由服务端给、按 key 取回；「模型经手」= 值经过模型的工具参数（哪怕受 ask_user 卡或允许卡约束，也写清约束是什么）。
- **直写 / 代录**：两种模式的差别。

A 表、B 表用 `#` 对齐。

- **B 表的输入方式**取 `guide: pearnly_core/assistant/guide/declaration.py:26 INPUTS` 九种之一。
- **何时问**取 `:32 ASK`；记住的范围取 `:35 REMEMBER_SCOPES`（`company / company_party / ledger`）。凡是钱和对象（对方、金额、写哪几张、删哪几张）一律写 `always`。
- **check / facts / wait** 照 `guide: apps/pearnly_erp_portal/pearnly_erp_portal/collection/guide.py` 的写法：`check` = 作答时服务端再核，`facts` = 题目下的事实行，`wait` = 选项回 `wait`（在读）。

### A 表：选择点 × 出题 × 值来源 × 校验 × 经手

#### 1.1 上传识别 → 推送（采购 / 销售；直写与代录；Express / MR.ERP）

| # | 功能 / 步骤 | 选择点 | 出题 / 判定（文件:行 函数） | 值来源 | 服务端校验 | 经手 | 直写 / 代录；MR.ERP |
|---|---|---|---|---|---|---|---|
| 1 | 写账前 | 公司（本对话公司） | 写入闸 `assistant/write_scope.py:163 check`：没挂公司回 `no_company`，带候选（`:262 _companies`）；补挂走 `pearnly_file_conversation`（`P1: pearnly_core/assistant/tools.py:174`） | 对话的公司；补挂时 ask_user 上模型列的公司 | 每个写入点都过写入闸（账套属本对话公司 `companies.service.work_sets`，`write_scope.py:62`）；补挂同 P2 第 1 行 | 新建对话页选的：服务端原值；补挂：**模型经手**（受 ask_user 答案约束） | 代录按任务的公司（`assistant/tools.py:963 _scope_refusal` 取 `operator.company`） |
| 2 | 写账前 | 本对话账套 | `write_scope.py:68 pinned`、`:187 aim`；未定回 `ledger_needed`（`:230 needed`）；第 1 期卡上第一题 `P1: pearnly_core/assistant/guide_tool.py:76 _ledger_question` | 共用 `pearnly_core.assistant.ledger` 的候选（Cowork 由 `companies.provider` 供，`hooks.py` 的 `pearnly_assistant_projects`） | `aim`：模型另给的账套一律拒（`not_this_conversation_book`），没给就写本对话账套；同一本在几台电脑各有一处时，按本人在线的那台挑（`:128 _serving`） | 卡上和右栏是服务端原值；`pearnly_conversation_ledger.ledger` 是**模型经手**（同 P2 第 2 行） | 代录按任务会话的账套（`pinned(company, task)`）。MR.ERP 账套键同一判定 |
| 3 | 识别 | 录入去向（程序 / 账套，或仅识别） | `cowork_intake_options`（`assistant/tools.py:4668`、`:2119 intake_options`）；`cowork_recognize_attachments` 的 `target / connection / account_set_key`（`:4626`、`:1600 _choices`） | `connections.targets` + `account_search.usable`（同网页新建录入、LINE，`:2029` 注释）；对话里只留本对话公司的（`:2151 write_scope.keep`）；有本对话账套时 `suggested` 就是它 | `:1658 _selectable`（在可选清单里）；`:1710 _scope_refusal`；没给账套时 `:1713 _default_target` 补本对话账套 | **模型经手**，被写入闸兜住（只能是本对话账套） | 两种模式相同；代录放进输入框的文件由任务交识别，不再另交（`:1437 files.taken`） |
| 4 | 识别 | 方向（进项 / 销项） | `cowork_recognize_attachments.direction`（`:4654`）；拿回早先的识别时，`:1830 _declare_direction` 把方向写到票上 | 模型参数 | 只归一枚举（`ocr/execution/service.py:57 normalize_direction`） | **模型经手**：写成票上「声明的方向」，推送判方向时优先于税号判据（`assistant/tools.py:334 judged_direction`） | 两种模式相同 |
| 5 | 推送前 | 方向判不出：采购 / 费用 / 销售 | `precheck_source.py:159-167`（kind `direction`，subject = 对方键 `operator_source.py:204 party_key`） | 固定三项 | `precheck_source.py:521 remember` → `operator_source.py:381 regroup`：改这张的方向与入账类型，按公司记住这个对方的分组（`companies.rules.GROUP`） | 卡上是服务端原值；口头作答是模型经手 | 代录资料区拖动走同一条（`regroup`） |
| 6 | 识别 / 推送 | 整张的入账类型参数（走库存 / 记费用） | `cowork_recognize_attachments.posting_kind`（`:4658`）、`cowork_push_documents.posting_kind`（`:4923`）、`cowork_pos_import.posting_kind`（`:4822`） | 模型参数 | 只核枚举；推送时 `:1025 posting_kind or doc.posting_kind` | **模型经手**；定了以后判定卡不再问第 7 行（第 4 节第 1、5 条） | 代录交单按资料区分组明传（`operator_source.py:295 _settle`），不是模型参数 |
| 7 | 推送前 | 商品单走库存还是记费用（按公司） | `precheck_source.py:314 _goods_item`（永续 / 混合库存账套、没声明入账类型的商品单；预选 stock）；P1 引导模式下账套没报记账画像也问、预选「记费用」（`P1: precheck_source.py:323-331`）；详情 `assistant/tools.py:2392` 标 `handling_choice_needed goods_posting` | 固定 stock / service | `push/application/goods_posting.py:35 choose`（按公司记住） | 卡上是服务端原值；`cowork_choose_document_policy.value`（stock / service）是**模型经手**（允许卡约束） | 两种模式相同；MR.ERP 不走 |
| 8 | 推送前 | 录不录（转账凭条、预扣税凭证） | `precheck_source.py:118-124` → `:298 _policy_item`（预选来自 `preselect.document_policy`，RULE）；票上原因 `document_policy_needed_<类>`（`contracts/document_policy.py:48`） | 固定 enter / skip（`contracts/document_policy.py:47 VALUES`） | `history/document_policy.py:39 choose`；**整个工作空间这一类单都按它**（工具说明 `assistant/tools.py:4752-4757`） | 卡上是服务端原值（WHETHER 类，不预选，`P1: write_precheck/guide.py:29`）；`cowork_choose_document_policy.value` 是**模型经手**（允许卡约束） | 两种模式相同 |
| 9 | 推送前 | 待选账套（同期两本 / 没有覆盖日期的） | `precheck_source.py:305 _book_item`（kind `current_book`，有本对话账套时不出，`:146`）；详情 `assistant/tools.py:2418 _current_book_options`；推送回 `account_set_not_chosen`（`:2670 _not_chosen`） | `companies.pending.account_set_options`（同待处理页 `pending_api.py:103 account_sets`）；对话里只留本对话公司的（`:1526 _stop_target`） | `precheck_source.py:584 _choose_book` → `pending.account_set_pick`；工具路 `:2611 _choose_current_book` 先过写入闸、再 `pending.current_book_choice` | 卡上是服务端原值；`cowork_choose_document_policy.account_set_key`、`cowork_push_documents.account_set_key` 是**模型经手**（候选与写入闸约束） | 两种模式相同 |
| 10 | 推送前 | 上次结果不明：已入账 / 没入账 | `precheck_source.py:107-113`（`lifecycle.result_unknown` → `card.UNKNOWN`） | 推送记录状态 | 直写：`cowork_resolve_push_result` → `push/application/service.py:933 resolve_unknown`（只核状态是结果不明）；代录：`operator_source.py:534 resolve` | 代录开始卡上是服务端原值；直写的 `entered / docnum` 是**模型经手**，没有允许卡 | 代录走开始卡，直写走工具 |
| 11 | 代录开始前 | 同票已写进 Express：再录 / 跳过 | `operator_source.py:136 written_twin`（同票号、同对方税号、同金额、已成功）→ `:117 precheck` 回 `state=written` | 同工作区已推成功的识别单 | `operator_source.py:416 submit` 交单时再核一次；`again` 只认待录清单里的（`P1: write_plan.py:200-202`） | 开始卡上勾回是服务端原值；`pearnly_write_plan.again[]` 是**模型经手** | 只有代录有。直写对已推的回 `already_success`（`assistant/tools.py:1002-1009`） |
| 12 | 推送前 | 对方档（已有 / 新建） | `push/application/service.py:558 choices_view` → `precheck_source.py:372 choice_items`（kind `party`，大列表带 `search=parties`）；预选 `:342 _preselect` → `preselect.party` | `reported_customers / reported_suppliers`；NEW | `push/application/service.py:715 save_choices`：值在主档里或 NEW；定时任务默认定下的类别拒收（`:727-729`） | 卡上是服务端原值；`pearnly_precheck_answer` 是**模型经手**（搜索类可以给主档里任何一个码） | 两种模式相同；MR.ERP 按税号找、找不到自建，不问（`assistant/tools.py:811-813` 注释） |
| 13 | 推送前 | 存货 / 按行记费用 | 同上（kind `item`；没声明过账类型的行多「记费用」`EXPENSE`，`precheck_source.py:383-384`；搜索 `products / products_all`） | `reported_products`；NEW / EXPENSE；预选固定 NEW（RULE） | `save_choices`：值在主档里或 NEW / EXPENSE | 同第 12 行 | 两种模式相同 |
| 14 | 推送前 | 费用科目 / 收入科目 | 同上（kind `account`，`search=accounts`）；预选取科目建议第一条（SUGGESTED，`precheck_source.py:353-356`），建议受 `pearnly_precheck.hint` 影响（`:498 check(hint)` → `service.py:558 choices_view(hint)`） | `reported_accounts`；科目建议；已定的从记住 / 公司规则 / Express 历史来（`:470 decided_items`） | `save_choices`：值在科目表里 | 同第 12 行；另外 `hint` 是**模型写的文字**，会改预选（第 4 节第 16 条） | 两种模式相同 |
| 15 | 推送前 | 现 / 赊 | 同上（kind `payment`）；预选 `preselect.payment`；这一张自己的例外 `precheck_source.py:441 document_payment_item` | `choice_rules.PAYMENT_VALUES`；票面 | `save_choices`；只改这一张走 `:455 _set_document_payment`（只认进项） | 卡上是服务端原值；`pearnly_precheck_answer.answers[].doc_id` 是**模型经手**（同 P2 第 4 节第 2 条） | 两种模式相同；MR.ERP 见第 24 行 |
| 16 | 推送前 | 进项税抵不抵 | 同上（kind `vat`，预选 `preselect.vat`） | `VAT_VALUES` | `save_choices` | 同第 12 行 | 两种模式相同 |
| 17 | 推送前 | 服务品税口径（权责 / 收付实现） | 同上（kind `service_vat`，由 `choices.resolve` 出，同 P2 第 25 行） | `service_vat.OPTIONS` | `save_choices` | 同第 12 行 | 代录弹框已接上（`core: express_operator/choice_points.py:40-46`） |
| 18 | 推送前 | 仓 | 同上（kind `location`，预选主仓 `precheck_source.py:365-366`）；按预选「走库存」重判才多出的仓题带 `when`（`:169-183`） | `warehouses_in_use(reported_warehouses)`（同网页 `connections/api.py:84 warehouses`） | `save_choices` | 同第 12 行 | 两种模式相同 |
| 19 | 推送前 | 部门 / 销售员 | `choices.resolve` 不问、只用记过的或单据簿默认（同 P2 第 29 行）；`department` 在 `CHOICE_KINDS` 里（`precheck_source.py:30`），定时任务可设默认（第 52 行） | 记忆、账套设置、对方档 | `save_choices` 核在 `reported_departments` 里 | 服务端；不问 | 两种模式相同 |
| 20 | POS 推送前 | 各付款方式的收款科目 / 退货科目 | 同第 12 行（kind `receipt` / refund，预选 `preselect.receipt_account / refund_account`，`precheck_source.py:361-364`） | `reported_accounts` | `save_choices` | 同第 12 行 | 只直写（POS 不在代录范围） |
| 21 | 代录停下后 | 定制菜单的子菜单 | `precheck_source.py:195 _menu_stop`（读推送记录回执 `meta.operator`）→ `:218 _menu_item`（kind `operator_form`） | CC 读到的候选菜单文字；记忆 `push.choices.operator_form` | `:248 _remember_menu`：值须是停下时的候选之一 | 卡上是服务端原值；口头作答是模型经手 | 只有代录有 |
| 22 | 推送前 | 存货科目组（新建库存品挂的组） | **判定卡不出**（`precheck_source.py:30 CHOICE_KINDS` 没有这一类）；CC 桌面版回 `STOCK_ACC_GROUP_UNFIT` 时，模型调 `cowork_stock_group`（`assistant/tools.py:4679`、`:2200`） | `connections/service.py:427 stock_acc_group`（合格组 + 选过的） | `connections/service.py:451 choose_stock_acc_group` → `stock_groups.choose`，不在组里回 None 即拒 | **模型经手**（允许卡 `approval: ["acccod"]`，`:4688`；卡上显示 `:4439 preview_stock_group`） | 两种模式相同。ERP 判定卡有这一题（P2 第 20 行） |
| 23 | 推送前 / 推送中 | MR.ERP 导入菜单（每类单）、主档默认值（部门、员工、应付 / 应收科目、银行科目、费用科目…） | **判定卡不出**（`precheck_source.py:127-133` 注释：「本批不进卡片，评估 B5」）；推送前 `assistant/tools.py:918 _mrerp_setup_needed` → `:1054-1062` 回 `needs_choice` + `mrerp_setup`；推送中才发现的 `:1181-1184`ã`:1265-1271` | `connections/service.py:489 mrerp_menu`（菜单 + 默认值，同待处理页 `pending_api.py:140 mrerp_needs`） | `connections/service.py:514 choose_mrerp_menu`、`:533 choose_mrerp_default`：只收最近读到的选项 | **模型经手**（`cowork_mrerp_setup.menus / defaults`，允许卡 `:4715`；卡上显示 `:4449 preview_mrerp_setup`） | MR.ERP 代录关着（第 0 节第 6 条）。ERP 判定卡有这两类题（P2 第 30 行） |
| 24 | 识别 | MR.ERP 现 / 赊（销售：现销 / 赊销；采购只有赊） | `cowork_recognize_attachments.payment`（`:4659`）；`intake_options.modes` 说明（`:2187`） | 模型参数 | 只归一（`ocr/execution/service.py:59 normalize_payment`），存到识别批次（`:111`） | **模型经手**，没有允许卡 | 推送侧怎么落到 MR.ERP 菜单：**未查到**（没读 MR.ERP 推送侧） |
| 25 | 写入 | 写哪几张 | `P1: pearnly_core/assistant/write_plan.py:186 make`：直写由模型列 `documents`，代录按待录清单（`express_operator.api.waiting`）；放行 `:305 permit` 只核在已开始的计划里 | 直写：模型列；代录：服务端清单（`operator_source.py:54 sources`，同 `cowork_list_documents` 的 waiting） | 计划开始时逐张重判、摘要变了的不写（`:268-273`）；复述后用户要再说一句（`:256`） | 直写 `pearnly_write_plan.documents[]` 是**模型经手**（同 P2 第 4 节第 7 条） | 代录由服务端定清单 |
| 26 | 待处理 / 复核 | 改票面字段（名称、税号、分店、单号、日期、票种、金额、币种、方向、入账类型、明细） | `cowork_edit_document`（`assistant/tools.py:4726`、`:2469 edit_document`）；可改字段 `:2308 EDITABLE` | 模型参数（用户的话 / 模型看原件） | 字段白名单；枚举 `:2313 CHOICES`；金额 / 税号形状 `:2342 _clean`；版本号；金额、税号、单号、日期、票种、币种、明细要带 `basis`（`assistant/document_checks.py:18`，只记文字，不核内容） | **模型经手**，没有允许卡 | 两种会话都能调（没写 modes）；已推的不回写账本（工具说明） |
| 27 | 待处理 | 补页重读：补到哪一张 | `cowork_recognize_attachments.into_document`（`:4660`）→ `assistant/into_document.py:79 run` | 模型定这一页属于哪张单 | 原单可读、不在推送中 / 已入账（`into_document.py:25 LOCKED`）；重读只出一张才覆盖，否则不合并 | **模型经手**，没有允许卡；覆盖后的识别字段直接进账本 | 两种会话都能调 |
| 28 | 待处理 | 删哪几张（连原件） | `cowork_delete_documents`（`assistant/tools.py:4930`、`:2706`） | 模型列单据名 | `history/removal.py:49 delete`：本人可删，已推送的跳过 | **模型经手**，没有允许卡（说明要求「用户点名就直接删」，`:4932-4934`） | 只直写 |
| 29 | 推送中 | 单位、现金科目、新建对方类型 / 科目、成本口径 | 同 P2 第 14、19、21 行与第 5 节：Cowork 共用 `choices.resolve`，**同样没有服务端出题** | — | CC 桌面版拒写 | — | 同 P2 |

#### 1.2 POS 导出（销售日汇总）

| # | 功能 / 步骤 | 选择点 | 出题 / 判定 | 值来源 | 服务端校验 | 经手 | 直写 / 代录；MR.ERP |
|---|---|---|---|---|---|---|---|
| 30 | 导入 | 推往哪本账套 | `cowork_pos_import`（`assistant/tools.py:4803`、`:1925`）→ `:2097 _book_defaults` 缺省本对话账套 | 本对话账套 / 模型参数 | `:1935 _selectable`、`:1938 _scope_refusal` | 有本对话账套时是服务端原值，否则**模型经手**（写入闸约束） | 只直写；只 Express |
| 31 | 导入 / 文件夹任务 | 认列：哪一列是日期 / 票号 / 金额 / 增值税 / 付款方式 / 类型 / 状态 / 品名 / 数量；日期顺序；金额含不含税；状态值是销售 / 作废 / 退货 | 服务端出问题：`pos_import/service.py:116 preview` → `pos_import/contracts.py:417 apply_answers`（剩下没答的问题照列）；文件夹任务里停着的列为 `pos_layout_needed`（`companies/exceptions.py:40`） | 问题与选项（表头、枚举）由服务端出；答案由模型填进 `answers`（`assistant/tools.py:1864 POS_ANSWERS_SCHEMA`） | 表头不在这份导出里当没答；枚举合法；答了按公司记住（`companies/choices.py:6` 的 `pos` 版式） | **模型经手**，没有允许卡 | 只直写 |
| 32 | 推送 | 走第 25 行写入计划 + 第 20 行收款科目 | — | — | — | — | — |

#### 1.3 回读

| # | 功能 / 步骤 | 选择点 | 出题 / 判定 | 值来源 | 服务端校验 | 经手 | 直写 / 代录；MR.ERP |
|---|---|---|---|---|---|---|---|
| 33 | 回读一个月 | 账套、月份、科目前缀 | `cowork_express_readback`（`assistant/tools.py:4992`、`:3861`）→ `:2097 _book_defaults(period=True)` 缺省本对话账套与月份 | 本对话 / 模型参数 | 写入闸（别家账套拒）；只读，不写账本 | 缺省时是服务端原值；`accounts` 是模型经手（只影响读哪些科目） | 只直写；MR.ERP 不能回读 |
| 34 | 删回读 | 删哪一份 | `cowork_express_readback_delete`（`:5020`、`:3880`） | 模型参数 | `readback.service.delete` | 模型经手，没有允许卡；不写账本 | — |

#### 1.4 对账（银行 / 收入 / 销项税核查）

| # | 功能 / 步骤 | 选择点 | 出题 / 判定 | 值来源 | 服务端校验 | 经手 | 直写 / 代录；MR.ERP |
|---|---|---|---|---|---|---|---|
| 35 | 开始 | 类别、两侧文件、标题 | `cowork_recon_start`（`assistant/tools.py:5105`、`:2800`） | 模型定哪份文件放哪一侧 | 文件须是本对话的附件；同组附件 10 分钟内只开一套（`:2809-2810`） | 模型经手；不写账本（扫描件按页付费识别） | 只直写 |
| 36 | 结果出来后 | 结果里的问题（分组、对的总账科目、刷卡手续费率…） | 服务端出问题（`cowork_recon_result` 的 questions）；作答 `cowork_recon_answer`（`:5147`、`:2970`）→ `recon_center/presets.py:26 answer` | 问题与候选由服务端出；答案由模型填 `rule / value` | 范围由服务端从任务算（`presets.py:15 _task_scope`）；重跑类只收建任务时的候选（`:44-48`）；其余 `preset_store.remember` 核形状 `presets.valid` | 模型经手；按账户 / 公司记住；不写账本 | 只直写 |
| 37 | 预设 | 改 / 删一条记住的答案 | `cowork_recon_presets`（`:5156`、`:2991`）→ `presets.py:64 change` / `:74 remove` | 模型参数 | 只改已有的预设 | 模型经手，没有允许卡；不写账本 | — |
| 38 | 删除 | 删哪几套 | `cowork_recon_delete`（`:5169`、`:3036`）→ `recon_center/api.py:83 delete` | 模型参数 | 本人可删 | 模型经手，没有允许卡 | — |

#### 1.5 报税草稿（ภ.พ.30、ภ.ง.ด.3 / 53）

| # | 功能 / 步骤 | 选择点 | 出题 / 判定 | 值来源 | 服务端校验 | 经手 | 直写 / 代录；MR.ERP |
|---|---|---|---|---|---|---|---|
| 39 | ภ.พ.30 | 账套、税月 | `cowork_vat_draft`（`assistant/tools.py:5174`、`:3116`）→ `_book_defaults(period=True)` | 本对话 / 模型参数 | 写入闸；没有回读回 `readback_needed`（`:3131`） | 缺省时是服务端原值 | 只 Express |
| 40 | ภ.พ.30 | 免税销售（第 3 格）、上期留抵（第 10 格）、零税额行去向 | 同上 `exempt_sales / excess_brought_forward / zero_vat_as` | 模型参数（用户的话） | `tax.service.vat_draft` 记在这份草稿里 | **模型经手**；进草稿表格，不写账本 | — |
| 41 | ภ.ง.ด. | 付款公司税号、月份、分店 | `cowork_wht_draft`（`:5197`、`:3166`）→ `:3148 _wht_defaults` 没给时用本对话公司 | 本对话 / 模型参数 | **给了税号就不核是不是本对话公司**（`_wht_defaults` 只在没给时补） | 模型经手；不写账本 | — |
| 42 | ภ.ง.ด. | 收款方收入类型（questions） | 服务端出问题（草稿的 `questions`）；作答 `cowork_wht_answer`（`:5235`、`:3196`） | 收入类型 `tax_contracts.INCOME_CODES`；模型按用户的话填 | 枚举；按整个工作空间记住（工具说明 `:5236-5241`） | 模型经手，没有允许卡；不写账本 | — |
| 43 | ภ.ง.ด. | 待确认的行算不算（`review`），行修改（日期、类型、税率、基数、税额、收款方税号、表格） | 服务端列 `review / suggestions / ewht_check`；作答 `cowork_wht_draft.edits` | 模型参数 | 枚举字段、`edits_rejected` 回不收的 | 模型经手；不写账本 | — |
| 44 | 删除 | 删哪几份草稿 | `cowork_tax_draft_delete`（`:5252`、`:3213`） | 模型参数 | `tax.service.delete` | 模型经手，没有允许卡 | — |

#### 1.6 盘点（Cowork 盘点不写 Express：`stocktake/` 里没有推送）

| # | 功能 / 步骤 | 选择点 | 出题 / 判定 | 值来源 | 服务端校验 | 经手 | 直写 / 代录；MR.ERP |
|---|---|---|---|---|---|---|---|
| 45 | 建盘点 | 用哪份 Excel、标题 | `cowork_stocktake_create`（`assistant/tools.py:5267`、`:3256`） | 对话附件 | 同对话同附件只建一张（`:3269-3271`） | 模型经手 | 只直写 |
| 46 | 记实盘数 | 物料、数量、仓 / 库位、重盘 | `cowork_stocktake_count`（`:5275`、`:3296`） | 模型参数；仓位缺省取账面位置 | `stocktake/desk_api.py:185 api_stock_count_lookup` 认码；`:228 api_stock_count_add_entry` | 模型经手；不写账本 | — |
| 47 | 结束 / 删除 | 哪一张 | `cowork_stocktake_close`（`:5279`）、`cowork_stocktake_delete`（`:5287`） | 模型参数 | `desk_api.py:164 close`、`:70 delete` | 模型经手，没有允许卡 | — |

#### 1.7 文件夹录入与定时任务（任务以后自动识别、自动写账）

| # | 功能 / 步骤 | 选择点 | 出题 / 判定 | 值来源 | 服务端校验 | 经手 | 直写 / 代录；MR.ERP |
|---|---|---|---|---|---|---|---|
| 48 | 建 / 改任务 | 读哪台电脑的哪个文件夹 | `cowork_folder_task_options`（`assistant/tools.py:4947`、`:3536`）；`cowork_save_folder_task(s)` 的 `reader / root_id / folder`（`:5025`、`:5044`） | `folder_intake/rules.py options`（同网页 `folder_intake/api.py:32 options`） | 启用任务的文件夹不能重叠（工具说明 `:5033-5034`） | **模型经手**（允许卡 `:5039 / :5059`；卡上各行 `:4299 _task_lines`） | 只直写 |
| 49 | 建 / 改任务 | 去向：连接、账套、方向、入账类型、MR.ERP 付款 | 同上 `connection / account_set_key / direction / posting_kind / payment` | 模型参数 | `:3971 _task_scope`（写入闸按公司判，`whole_company`）；MR.ERP 先定菜单 / 默认值才出卡（`:3976 folder_task_refusal`）；已有任务不能换程序 | **模型经手**（允许卡） | MR.ERP 任务要 payment |
| 50 | 建 / 改任务 | 默认处理：现 / 赊、抵不抵、科目、存货（新建 / 停下问）、部门 | 同上 `defaults`；未知类先拒（`:3930 _unknown_defaults`） | 模型参数；可选值 `folder_task_options` 的 accounts / departments | `folder_intake/contracts.py:139 defaults`：枚举；科目 / 部门在已读主档里（**主档没读过不核**，`:144-146`） | **模型经手**（允许卡）；定下的类以后推送不再问（`task_default`，`push/application/service.py:727-729`） | 只 Express |
| 51 | 建 / 改任务 | 日程、提醒 | 同上 `run_day / run_time / remind / remind_times` | 模型参数 | `folder_intake/contracts.py:125 schedule` | 模型经手（允许卡） | — |
| 52 | 启停 | 启用 / 停用 / 提醒 | `cowork_set_folder_task(s)_enabled`（`:5065`、`:5080`） | 模型参数 | 任务服务 | 模型经手（允许卡 `:5073 / :5092`） | — |
| 53 | 立即运行 | 跑哪一个 | `cowork_run_folder_task`（`:5345`、`:3503`）→ `folder_intake/rules.py:609 run_now` | 模型参数 | 只核权限、任务已启用；**不过写入闸**（公司对话里能跑别家公司的任务）、**没有允许卡**、不经写入计划 | **模型经手** | 触发识别（扣费）+ 按任务默认推送 |
| 54 | 删除 | 删哪几个任务 | `cowork_delete_folder_tasks`（`:5096`、`:4126`） | 模型参数 | 任务服务 | 模型经手（允许卡 `:5101`） | — |
| 55 | 取文件进对话 | 哪台电脑、哪些文件 | `cowork_folder_files`（`:4962`）、`cowork_folder_fetch`（`:4979`、`:3716`） | 电脑报上来的清单 | 只读，不识别、不扣费 | 模型经手；不写账本 | — |

#### 1.8 公司备注与记住的选择

| # | 功能 / 步骤 | 选择点 | 出题 / 判定 | 值来源 | 服务端校验 | 经手 | 直写 / 代录；MR.ERP |
|---|---|---|---|---|---|---|---|
| 56 | 团队笔记 | 新增 / 改 / 删一条 | `cowork_company_note_save / _remove`（`assistant/company_tools.py:656`、`:668`） | 模型写的文字 | `:141 company_refusal`（别家税号不出卡）；500 字、50 条上限 | 模型经手（允许卡） | — |
| 57 | 公司规则 | 存一条（类、值、对象、分店），或把账套记忆提升为全公司 | `cowork_company_choice_save`（`:678`、`:529 choice_save`） | `value` 由模型从 `pearnly_precheck` 抄码；对象按单据算（`:394 _party`） | `:98 resolve(write=True)`（只认本对话公司或明传编号）；`:471 mismatch`（`expect_party` 要对上单据的对方）；`companies/choices.py:769 _save_rule` 按工作月份那本账套主档核值（`:775-776`；主档没读过放行，`:722-723`） | **模型经手**（允许卡 `:703`；卡上显示 `:607 preview_choice_save`） | 只 Express（MR.ERP 公司不存，`:499 MRERP_NO_RULES`） |
| 58 | 公司规则 | 删一条 | `cowork_company_choice_remove`（`:708`） | 模型给 key | 公司核对 | 模型经手（允许卡） | — |

#### 1.9 任务看板（不写账本）

| # | 功能 / 步骤 | 选择点 | 出题 / 判定 | 值来源 | 服务端校验 | 经手 | 直写 / 代录；MR.ERP |
|---|---|---|---|---|---|---|---|
| 59 | 卡片 | 建、改、移动、完成、评论 | `cowork_work_create_card / _update_card / _move_card / _comment`（`assistant/tools.py:5303-5318`）→ `work/api.py:69 create`、`:74 update`、`:79 move`、`:84 toggle_done`、`:129 comment` | 模型参数 | `modified` 版本号；看板权限 | 模型经手；不写账本 | — |

### B 表：输入方式 × 何时问 × 选项函数 × check / facts / wait × 开始前 / 中途 × 可复用卡

| # | 输入方式 | 何时问 | 选项来自哪个已有服务函数（网页 / LINE 同一个） | check / facts / wait | 开始前 / 中途 | 可直接复用的现有卡 |
|---|---|---|---|---|---|---|
| 1 | choice | auto（对话挂了就定）；没挂 always 问一次 | `companies/api.py:13 companies`（网页公司列表；写入闸候选用 `companies.service.listing`） | — | 开始前 | 收款引导的公司题同法（`guide: collection/guide.py company_default / company_options`）；现在是 ask_user + `pearnly_file_conversation` |
| 2 | choice | auto（本对话已定）；未定 always 一次 | `pearnly_core.assistant.ledger.view`（右栏同一份） | check：`ledger.choose(by="page")`（同 `guide: collection/guide.py book_apply`）；facts：会计期间 | 开始前 | `pearnly_guide` 账套题（P1） |
| 3 | choice | auto（= 本对话账套） | `connections/api.py:49 targets` + `account_search.usable` | facts：`cc_desktop_online`（`assistant/tools.py:2139`）、`TARGET_NOTES`（`:2020`） | 开始前 | 并进第 2 行，不单独成题 |
| 4 | choice | auto（按税号判，`judged_direction`）；判不出交第 5 行 | 无现成选项函数（固定两项） | — | 开始前 | 不该再有模型参数 |
| 5 | choice | remember（company_party） | `precheck_source.py:81 document`；网页资料区拖动 `operator_source.py:381 regroup` | check：`regroup` | 开始前 | `pearnly_guide`（`direction` 是 WHETHER 类） |
| 6 | choice | 由第 7 行定 | 复核页字段（`history/api.py:56 save`） | — | 开始前 | 由第 7 行的题取代 |
| 7 | choice | remember（company） | `push/application/api.py:73 choose_goods_posting`（复核页） | check：`goods_posting.choose` | 开始前 | `pearnly_guide`（判定卡已有 `goods_posting`） |
| 8 | choice | remember（整个工作空间；声明的 `REMEMBER_SCOPES` 没有这一档，`guide: declaration.py:35`） | `history/api.py:27 choose_document_policy`（复核页） | check：`document_policy.choose` | 开始前 | `pearnly_guide`（WHETHER 类） |
| 9 | choice | remember（company，按这家这一组同期账套） | `pending_api.py:103 account_sets`（待处理页）→ `companies.pending.account_set_options` | check：`pending.account_set_pick` / `current_book_choice` | 开始前 | `pearnly_guide`（`current_book` 是 WHETHER 类） |
| 10 | choice | always | `push/application/api.py:87 resolve_unknown`（复核页「核对」） | check：`service.py:933` 状态核 | 开始前（判定卡 `facts.unknown`） | `pearnly_guide` 的 `facts.unknown`；代录开始卡 |
| 11 | choice | always | `operator_source.py:136 written_twin` | — | 开始前 | 代录开始卡；写入计划 `again` |
| 12 | choice（search） | always（钱和对象；现在按对方记住的只作预选） | `push/application/api.py:67 choices`（复核页、待处理）；搜索 `precheck_source.py:400 search`（parties） | check：`service.py:715 save_choices`；wait：主档在读（`precheck_source.py:156` → `card.WAITING`） | 开始前 | `pearnly_guide` |
| 13 | choice（search） | remember（company_party：对方 × 品名） | 同上；搜索 products / products_all | 同上 | 开始前 | `pearnly_guide` |
| 14 | choice（search） | remember（company_party） | 同上；搜索 accounts | 同上；wait：科目表在读（`assistant/tools.py:1041-1044`） | 开始前 | `pearnly_guide` |
| 15 | choice | remember（company_party）；单张例外 always | 同上（`PAYMENT_VALUES`）；`precheck_source.py:441 document_payment_item` | check：`save_choices` / `_set_document_payment` | 开始前 | `pearnly_guide` |
| 16 | choice | remember（company_party） | 同上（`VAT_VALUES`） | check：`save_choices` | 开始前 | `pearnly_guide` |
| 17 | choice | remember（company_party；销项按 ledger） | 同上（`service_vat.OPTIONS`） | check：`save_choices` | 开始前 | `pearnly_guide` |
| 18 | choice | remember（ledger） | 同上；`connections/api.py:84 warehouses` | check：`save_choices` | 开始前 | `pearnly_guide` |
| 19 | choice | auto | 同上（`reported_departments`）；`folder_intake/api.py:32 options` | — | — | 不出题 |
| 20 | choice（search） | remember（ledger，按付款方式） | `push/application/api.py:67 choices`（kind receipt） | check：`save_choices` | 开始前 | `pearnly_guide` |
| 21 | choice | remember（ledger × 单种） | `precheck_source.py:218 _menu_item`（回执候选） | check：`_remember_menu` | 中途才知道（第一次停下），之后开始前 | `pearnly_guide`；代录开始卡 |
| 22 | choice | remember（ledger） | `connections/api.py:79 stock_acc_group` / `:89 choose_stock_acc_group` | check：`stock_groups.choose`；wait：账套没读过（`assistant/tools.py:2212-2218`） | 开始前（要新建库存品时） | 现为允许卡；判定卡要补题类（ERP 已有 `stock_group`） |
| 23 | choice（默认值带 search） | remember（ledger，按单据种类 / 字段） | `connections/api.py:94 mrerp_menu`；待处理页 `pending_api.py:140 mrerp_needs` / `:167 mrerp_answer` | check：`choose_mrerp_menu / _default`；wait：`read_again` 在读（`assistant/tools.py:2228-2240`） | 开始前（读到过菜单）；否则中途（推送时才发现） | 现为允许卡；判定卡要补题类（ERP 已有 `mrerp_menu / mrerp_default`） |
| 24 | choice | always | 无现成选项函数（固定两项，`contracts.push.normalize_payment`） | — | 开始前 | 无现成卡（新题） |
| 25 | multi | always | 直写：`assistant/tools.py:584 list_documents`（`push_state=waiting`）；代录：`operator_source.py:54 sources`；网页待处理 `companies/api.py:133 pending` | check：写入计划三道保险（`P1: write_plan.py:248 start`） | 开始前 | `pearnly_write_plan`（复述 + 用户回话）；代录开始卡 |
| 26 | text / number / date / choice（按字段） | always | 复核页 `history/api.py:56 save` | check：`:2342 _clean`、版本号 | 开始前（复核时） | 无现成卡 |
| 27 | choice（补到哪张）+ file | always | 无现成函数（`into_document.run` 只有 CC 调） | check：`LOCKED`、只出一张才合并 | 开始前 | 无现成卡 |
| 28 | multi | always | 网页列表 `history/api.py:72 delete` | check：`removal.delete` 跳过已推送 | — | 现无卡；可用允许卡 |
| 29 | — | — | 同 P2 | — | 中途（CC 桌面版回执） | 同 P2 |
| 30 | choice | auto（本对话账套） | 同第 2 行 | — | 开始前 | 同第 2 行 |
| 31 | choice（每列从表头选）/ choice（枚举） | remember（company，按版式） | `pos_import/service.py:116 preview`（文件夹任务 `folder_intake/pump.py:243` 同用；网页入口**未查到**） | check：`apply_answers` 回剩下的问题；facts：表头与样例行 | 开始前（第一次见这种版式）；定时任务里是中途（`pos_layout_needed`） | 无现成卡（新题类） |
| 32 | — | — | — | — | — | 同第 20、25 行 |
| 33 | choice（auto）+ month + multi | auto（本对话账套与月份） | 无网页函数（`readback/` 里没有 whitelist，只有 CC 调） | wait：在读（`assistant/tools.py:3802 _readback_wait`）；facts：`waiting_for_cc`（offline / update_needed / unreachable） | 开始前 | 表单引导（month） |
| 34 | choice | always | 无网页函数 | — | — | 现无卡；可用允许卡 |
| 35 | choice + file + file + text | always | 网页 `recon_center/api.py:23 start` | facts：扫描件要付费识别（工具说明 `:5112-5113`）；wait：任务在跑 | 开始前 | 表单引导（file） |
| 36 | choice | remember（按账户 / 公司，服务端算范围） | 网页结果页 `recon_center/presets.py:26 answer` | check：`presets.valid` / 重跑候选；wait：`rerun_job` | 中途（结果出来才知道） | 现为 ask_user；要表单题 |
| 37 | choice | always | `presets.py:64 change` / `:74 remove` | check：只改已有的 | — | 现无卡 |
| 38 | multi | always | `recon_center/api.py:83 delete` | — | — | 现无卡；可用允许卡 |
| 39 | choice（auto）+ month | auto（本对话） | 无网页函数（`tax/` 里没有 whitelist，只有 CC 与公司页汇总调） | facts：`readback_needed` | 开始前 | 表单引导（month） |
| 40 | amounts / number + choice | always（金额） | 无现成函数 | — | 开始前 | 表单引导（number） |
| 41 | choice（auto） | auto（本对话公司） | 无现成函数 | — | 开始前 | 表单引导 |
| 42 | choice | remember（整个工作空间，按收款方税号） | `tax.service.answer`（只有 CC 调） | check：枚举 | 中途（草稿出来才知道） | 现为 ask_user；要表单题 |
| 43 | multi / amounts | always | 无现成函数 | check：`edits_rejected` | 中途 | 无 |
| 44 | multi | always | 无网页函数 | — | — | 现无卡；可用允许卡 |
| 45 | file + text | always | 网页 `stocktake/desk_api.py:85 api_create_stock_count` | — | 开始前 | 表单引导（file） |
| 46 | choice（search）+ number + choice | always | `desk_api.py:185 api_stock_count_lookup`、`:228 api_stock_count_add_entry` | check：`lookup` 认码 | 中途 | 无 |
| 47 | choice | always | `desk_api.py:164 close`、`:70 delete` | — | — | 现无卡；可用允许卡 |
| 48 | choice + choice | always | `folder_intake/api.py:32 options` | facts：电脑在线、文件数（`assistant/tools.py:3551-3563`） | 开始前 | 现为允许卡；可改表单引导 |
| 49 | choice | always | 同上 + `connections/api.py:49 targets` | check：`_task_scope`、MR.ERP 先定菜单 | 开始前 | 现为允许卡 |
| 50 | choice（科目带 search） | always（建任务这一下；存下的就是任务默认） | `folder_intake/api.py:32 options`（accounts / departments / stock_group） | check：`folder_intake/contracts.py:139 defaults`；wait：`accounts_status` 在读（`assistant/tools.py:3565-3568`） | 开始前 | 现为允许卡 |
| 51 | number + text | always | 无（`contracts.schedule` 只核） | check：`schedule` | 开始前 | 现为允许卡 |
| 52 | choice | always | `folder_intake/api.py:43 set_enabled` | — | — | 允许卡 |
| 53 | choice | always | `folder_intake/api.py:53 run_now` | — | — | 现无卡；可用允许卡 + 写入闸 |
| 54 | multi | always | `folder_intake/api.py:48 delete` | — | — | 允许卡 |
| 55 | choice + multi | always | 无网页函数（经 CC 桌面版请求） | wait：在读；facts：`waiting_for_cc` | 开始前 | 表单引导（file） |
| 56 | text / choice | always | `companies/api.py:67 note_save`、`:77 note_delete` | check：`company_refusal` | — | 允许卡 |
| 57 | choice + choice（search） | always（这一下）；存下的是 remember（company） | `companies/api.py:112 choice_set`、`:122 choice_promote`；按单据算对象的 `companies/choices.py:787 save_rule` 只有 CC 调 | check：`mismatch` + `_save_rule` | — | 允许卡 |
| 58 | choice | always | `companies/api.py:102 choice_forget` | — | — | 允许卡 |
| 59 | text / date / choice | always | `work/api.py:69 / :74 / :79 / :84 / :129` | check：`modified` | — | 无（不写账本） |

---

## 2. 代录中途可能弹的框 → 对应开始前哪一题

同 P2 第 2 节：两个产品共用 `core: pearnly_core/express_operator/choice_points.py:31-73`，框与接没接上都一样（`unit` 没接，`service_vat_basis / service_output_vat_basis` 已接，`negative_stock / period_outside / picker` 停下）。Cowork 的差异只有下面几条：

| 账本 | 框 / 原因 | Cowork 的开始前对应 | 和 P2 的差别 |
|---|---|---|---|
| Express | `menu_choice`（定制菜单子菜单） | `precheck_source.py:195 _menu_stop`（读推送记录回执）→ `:218 _menu_item` → `:248 _remember_menu`；交单时载荷带 `operator.form_choice` | 机制相同，Cowork 在 `precheck_source.py` |
| Express | `picker` → `party_missing / item_missing` | 代录预检的旧回包里，对方、存货的选项最前面加 NEW（「在 Express 新建」，`precheck_source.py:269 _legacy`，`:281-284`） | 只覆盖「主档里有候选、要人选」的情况；主档里一个都没有、又没出题时仍在中途停下 |
| Express | `duplicate_ref`（1029） | 开始前同票已写的跳过（`operator_source.py:136 written_twin`、`:431-434`） | Cowork 按同工作区同连接同账套的已成功识别单比对 |
| Express | 分组 / 方向 | 开始前 `direction` 题、资料区拖动 → `operator_source.py:381 regroup`（按公司记住对方分组）；交单按分组明传入账类型（`:440-441`） | ERP 没有这一题 |
| MR.ERP | `kicked / bad_login / period / required / save_error` | **Cowork 现在不会出现**：`MRERP_OPERATOR_ENABLED = False`（`main: pearnly_core/express_operator/contracts.py:74`），MR.ERP 单交单回 `operator_express_only`（`assistant/tools.py:1064-1069`）。开了以后由服务端先按直写备好表单值（`push/mrerp/operator.stage`，`assistant/tools.py:1070-1079`） | ERP 侧同 P2 |
| — | 登记表外的画面 | 代录 AI 接手（同 P2 第 4 节第 9 条） | 相同 |

---

## 3. 现有原因码里属于「要人选」的（Cowork 侧）

| 原因码 | 在哪产生 | 选项来源 | 现在怎么问 | `stop_exits` 归类了吗 |
|---|---|---|---|---|
| `choice_needed:<类,…>` | 记在票上：`assistant/tools.py:1050`；交给模型时改成一句人话 + `choose` 列表（`:1259-1264`） | `push/application/service.py:558 choices_view` → `precheck_source.py:372 choice_items` | 判定卡（`pearnly_precheck`；P1 `pearnly_guide`） | 有 `choice_needed`（precheck:any，live），但登记来源是 ERP 的 `express/contracts.py`；Cowork 的码带后缀 |
| `document_policy_needed_<payment_evidence \| withholding_certificate>` | `contracts/document_policy.py:48`；判定 `precheck_source.py:118-124` | `document_policy.VALUES` | 判定卡 `document_policy`（WHETHER）；或 `cowork_choose_document_policy`（允许卡） | **未归类** |
| `document_policy_skip_*` | `contracts/document_policy.py:49`；判定列为不能写（`precheck_source.py:119-120`） | — | 不问（这个租户选了不录） | **未归类** |
| `posting_needs_review*`（商品单入账方式） | 详情识别它 `assistant/tools.py:2392`；判定 `precheck_source.py:314` | stock / service | 判定卡 `goods_posting`；或 `cowork_choose_document_policy` | **未归类**；产生处**未查到**（没读到写这个码的地方） |
| `account_set_not_chosen`（待选账套；自动推判出多份同期 `book_many`） | `assistant/tools.py:2670 _not_chosen`；`:1526 _stop_target` | `companies.pending.account_set_options` | 判定卡 `current_book`；或 `cowork_choose_document_policy current_book`；或推送带 `account_set_key` | **未归类**（只有 ERP 的 `express.account_set_several`） |
| `ledger_needed` | `write_scope.py:24`、`:230 needed` | 本对话公司的账套 | ask_user + `pearnly_conversation_ledger`（`write_scope.py:41-45` 的说明）；P1 引导卡账套题 | 有（ledger，live） |
| `not_this_conversation_book` / `not_this_company_book` | `write_scope.py:22-23` | `allowed_account_sets` | 不出题，照实说；用户要换才 ask_user + `pearnly_conversation_ledger` | **未归类** |
| `no_company` | `write_scope.py:25` | 本人能用的公司（≤20，`:262`） | ask_user + `pearnly_file_conversation` | **未归类** |
| `ERP_CONFIRMED_PUSH_RESULT_UNKNOWN`（`service.RESULT_UNKNOWN`） | `precheck_source.py:107-113` | 已入账 / 没入账 | 判定卡 `facts.unknown`；直写 `cowork_resolve_push_result`（模型）；代录开始卡 `operator_source.resolve` | 写入计划码 `last_write_result_unknown` 有（live） |
| `ERR_MRERP_MENU_CHOICE_REQUIRED / _MENU_CHANGED / _DEFAULT_CHOICE_REQUIRED / _DEFAULT_CHANGED` | `assistant/tools.py:851-852 MRERP_SETUP_CODES`；`:1054-1062`、`:1181-1184`ã`:1265-1271` | `connections/service.py:489 mrerp_menu` | `cowork_mrerp_setup`（ask_user + 允许卡），判定卡不出 | 有（precheck:any，live），但那是 ERP 的判定卡；**Cowork 判定卡不出这一类** |
| `STOCK_ACC_GROUP_UNFIT`（CC 桌面版回执） | 词条 `i18n/messages.tsv:1471 intake.error.STOCK_ACC_GROUP_UNFIT` | `connections/service.py:427 stock_acc_group` | `cowork_stock_group`（允许卡） | 在 `core: scripts/stop-exits-baseline.json` 的未归类里 |
| `layout_needed` / `pos_layout_needed` | `pos_import/contracts.py:417 apply_answers` 剩下的问题；`companies/exceptions.py:40` | 表头 / 枚举 | ask_user + `cowork_pos_layout` 或 `cowork_pos_import.answers` | **未归类** |
| `waiting_for_cc`（reading / offline / update_needed / not_yours / unreachable） | `assistant/tools.py:1041-1044`；`companion/request_service.py:295-323` | — | 等（不问） | **未归类**（属 wait） |
| `cowork.masters.reading / failed` | `assistant/tools.py:784` | — | 等 / 告诉用户 | **未归类** |
| `readback_needed` | `assistant/tools.py:3131`；`companies/exceptions.py:47` | — | 先跑 `cowork_express_readback` | **未归类** |
| `operator_express_only` | `assistant/tools.py:1064-1069` | — | 不出题 | **未归类** |
| 报税草稿 `questions`（收入类型）、`review` 里的 `certificate_direction_unclear / pairing_unclear / buyer_unconfirmed / amount_unreadable / payee_tax_missing`、`ewht_check` | `cowork_wht_draft` 回包（`assistant/tools.py:5197` 说明、`tax.service.wht_draft`） | 收入类型 `INCOME_CODES`；是 / 否 | ask_user + `cowork_wht_answer` / `edits` | **未归类**（不写账本） |
| 对账结果 `questions`（分组、总账科目、刷卡手续费…） | `cowork_recon_result`；`recon_center/presets.py:26` | 重跑类：`rerun.questions` 的候选；其余：`presets.valid` | ask_user + `cowork_recon_answer` | **未归类**（不写账本） |
| `UNIT_CHOICE / COST_BASIS_CHOICE / CASH_ACCOUNT_CHOICE`、`UNIT_CODE_NOT_FOUND`、`PARTY_TYPE_NOT_FOUND`、`*_BRANCH_AMBIGUOUS`、`*_DUP_SUSPECTED`、`negative_stock`、`STOCK_INSUFFICIENT` 等共用码 | 同 P2 第 3 节 | 同 P2 | 同 P2 | 同 P2 |

`stop_exits` 不收 Cowork 的码的依据：`core: scripts/check-stop-exits.py:79-89` 的 `SOURCES` 只列共用层（`K`）和 ERP（`E`）的登记文件，没有 Cowork 的文件。

`write_precheck` 里「做不做」类、不预选的题（`P1: write_precheck/guide.py:29 WHETHER_KINDS`）在 Cowork 出的有 `document_policy / direction / current_book`。

---

## 4. 现在仍靠模型复述、会写进账本的值（P3 必须改）

1. **`cowork_recognize_attachments.direction / posting_kind / payment`**
   - 在哪：声明 `assistant/tools.py:4626`；`:1600 _choices`；`ocr/execution/service.py:52-59`。
   - 服务端核了：只归一枚举。
   - 去向：写到识别批次，再到单据。`direction` 成为票上「声明的方向」，优先于税号判据（`assistant/tools.py:334`）。拿回早先识别的单时，`:1830 _declare_direction` 也会把它补写到票上。`payment` 决定 MR.ERP 的现销 / 赊销。
   - `posting_kind` 定了以后，判定卡不再问「走库存 / 记费用」：`precheck_source.py:314 _goods_item` 在 `kind` 为真时回 None；`kind` 取自 `push/application/service.py:575 _choices` 的 `_posting_decision`，明传的优先。这一点按代码路径推断，没有实测。
   - 没核：与票面、与用户原话是否一致。没有允许卡。
2. **`cowork_recognize_attachments.target / connection / account_set_key`**
   - 在哪：`:1600`、`:1658`、`:1710-1713`。
   - 服务端核了：在可选清单里，并过写入闸。有本对话账套时只能是它，没给就补它。
   - 结论：模型经手，但已被服务端兜住。列在这里是因为它仍是模型参数。
3. **`cowork_recognize_attachments.into_document`**
   - 在哪：`:4660`；`assistant/into_document.py:79 run`。
   - 做什么：模型定补传的这一页属于哪张单。
   - 服务端核了：单据可读；不在推送中、也没入账（`:25 LOCKED`）；重读只出一张单才覆盖。
   - 没核：这一页是否真属于这张单。覆盖后的识别字段直接进账本。没有允许卡。
4. **`cowork_edit_document.changes / items`**
   - 在哪：`assistant/tools.py:4726`、`:2469`。可改字段 `:2308 EDITABLE`：对方名称、税号、分店、单号、日期、票种、金额、币种、方向、入账类型、明细。
   - 服务端核了：字段白名单；枚举（`:2313`）；金额可解析、税号形状（`:2342 _clean`）；版本号；金额、税号、单号、日期、票种、币种、明细要带 `basis`（`assistant/document_checks.py:18`）。`basis` 只记文字，不核内容。
   - 没核：值与原件、与用户原话是否一致。名称、分店、方向、入账类型连 `basis` 都不要。没有允许卡。
   - 写入计划会因判定摘要变了而不写这张（`P1: write_plan.py:268-273`），但改完重建计划，就照新值写。
5. **`cowork_push_documents.posting_kind`**
   - 在哪：参数 `assistant/tools.py:4923`；`:1249` → `:1282` → `:1094` → `:1025 posting_kind or doc.posting_kind`。
   - 服务端核了：只核枚举。
   - 去向：推送时直接改整张「走库存 / 记费用」。写入计划的判定是按单据自己的入账类型算的（`P1: write_plan.py:133 _judge` → `precheck_source.py:498 check`，入账类型取 `operator_source._settle`）；放行 `P1: write_plan.py:305 permit` 只核这张单在不在计划里。
   - 没核：这次推送的 `posting_kind` 与计划时一致。
   - 另：同一工具的 `connection / account_set_key` 在有本对话账套时，另给就拒（`write_scope.py:187 aim`），不算漏洞。
6. **`cowork_pos_import.answers` / `cowork_pos_layout.answers`**
   - 在哪：`assistant/tools.py:1864`、`:1925`、`:1975`；`pos_import/contracts.py:417 apply_answers`。
   - 做什么：问题由服务端出，但答案是模型把用户的话转成的：哪一列是日期 / 金额 / 增值税 / 付款方式 / 状态、日期顺序、金额含不含税、哪些状态值算作废或退货。答了按公司记住。
   - 服务端核了：表头在这份导出里（写错当没答）、枚举合法。
   - 没核：映射是不是用户的原意。这些答案决定每天的金额、税额拆分、哪些行不算销售，直接成单写进 Express。没有允许卡。
7. **`cowork_choose_document_policy.value / account_set_key`**
   - 在哪：`assistant/tools.py:4751`、`:2583`；允许卡 `:4782`，卡上显示 `:4416 preview_choose_document_policy`。
   - 做什么：录不录（整个工作空间这一类单）、商品单走库存或记费用（按公司）、现用账套（按公司）。
   - 服务端核了：值在枚举里（`history/document_policy.py:39`、`push/application/goods_posting.py:35`）；账套过写入闸，并且在候选里（`:2611` + `companies.pending.current_book_choice`）。
   - 结论：模型经手，受允许卡约束。和判定卡上同一题（`document_policy / goods_posting / current_book`）并存。
8. **`cowork_mrerp_setup.menus / defaults`**
   - 在哪：`assistant/tools.py:4692`、`:2221`；允许卡 `:4715`。
   - 做什么：MR.ERP 每类单推哪个导入菜单，以及部门、员工、应付 / 应收科目、银行科目、费用科目等默认值。写进之后每一张 MR.ERP 单。
   - 服务端核了：只收最近读到的选项（`connections/service.py:514`、`:533`）。
   - 判定卡不出这两类（`precheck_source.py:127-133`），推送回 `needs_choice` + `mrerp_setup`，模型用 ask_user 问、用本工具存。
   - 结论：模型经手，受允许卡约束。
9. **`cowork_stock_group.acccod`**
   - 在哪：`assistant/tools.py:4679`、`:2200`；允许卡 `:4688`。
   - 做什么：新建库存品挂哪个存货科目组。
   - 服务端核了：在合格组里（`connections/service.py:451`）。
   - 判定卡没有这一题。结论：模型经手，受允许卡约束。
10. **`cowork_resolve_push_result.entered / docnum`**
    - 在哪：`assistant/tools.py:4926`、`:2676`；没有允许卡。
    - 服务端核了：这张单有推送记录，状态是结果不明（`push/application/service.py:933`）。
    - 没核：用户确实核对过、说的是哪一张。记成「没入账」后可以再推，可能重复入账；单号由模型填。
    - 代录开始卡上这一题是服务端原值（`operator_source.py:534`）。
11. **`cowork_delete_documents.documents`**
    - 在哪：`assistant/tools.py:4930`、`:2706`；没有允许卡，工具说明要求用户点名就直接删。
    - 服务端核了：本人可删，已推送的跳过（`history/removal.py:49`）。
    - 没核：删的就是用户说的那几张。真删记录与原件，不能恢复。
12. **`cowork_save_folder_task(s)` 的 `connection / account_set_key / direction / posting_kind / payment / defaults.* / reader / root_id / folder`**
    - 在哪：`assistant/tools.py:5025`、`:5044`、`:4008`、`:4020`；允许卡。
    - 去向：之后每次自动识别、自动写账；默认处理定下的类以后推送不再问（`push/application/service.py:727-729`）。
    - 服务端核了：写入闸按公司判（`:3971`）；MR.ERP 先定菜单（`:3976`）；默认值枚举；科目、部门在已读主档里（`folder_intake/contracts.py:139-173`）。
    - 没核：主档没读过时，科目、部门不核。
    - 结论：模型经手，受允许卡约束。
13. **`cowork_set_folder_task(s)_enabled.enabled`**
    - 在哪：`:5065`、`:5080`；允许卡。
    - 做什么：恢复即恢复自动写账。模型经手，受允许卡约束。
14. **`cowork_run_folder_task.task`**
    - 在哪：`assistant/tools.py:5345`、`:3503` → `folder_intake/rules.py:609 run_now`。
    - 服务端核了：只核权限、任务已启用。
    - 没核：**不过写入闸**，公司对话里能跑别家公司的任务；**没有允许卡**；不经写入计划。
    - 去向：触发识别（扣费），并按任务默认自动推送。
15. **`cowork_company_choice_save` 的 `kind / value / document / expect_party / item / direction / branch / key`**
    - 在哪：`assistant/company_tools.py:678`、`:529`；允许卡。
    - 去向：之后这家公司所有 Express 账套的推送都用（`company_rule`）。
    - 服务端核了：公司只认本对话公司或明传编号（`:98`）；对象按单据算，`expect_party` 要对上（`:471`）；值按工作月份那本账套的主档核（`companies/choices.py:775-776`）。
    - 没核：主档没读过就放行（`:722-723`）；`value` 由模型从 `pearnly_precheck` 抄码，没核它就是用户说的那个。
    - 结论：模型经手，受允许卡约束。
16. **`pearnly_precheck.hint`**
    - 在哪：`P1: pearnly_core/assistant/tools.py:79`；Cowork 接收：`precheck_source.py:498 check(hint)` → `push/application/service.py:558 choices_view(hint)`。
    - 做什么：模型写的费用类别会把某个科目排成建议第一，也就是预选（`precheck_source.py:353-356`）。
    - 第 1 期引导卡上 HOW 题要点了才算（`P1: write_precheck/guide.py:9`），口头作答路径（第 17 条）可以直接记下。
    - ERP 不接收（P2 第 4 节第 11 条），Cowork 接收，这是差异。
17. **共用路径（同 P2 第 4 节第 1–7 条，Cowork 照样走）**
    - 涉及：`pearnly_precheck_answer.answers[].value / doc_id`、`pearnly_conversation_ledger.ledger`、`pearnly_file_conversation.company`、`pearnly_write_plan.documents[] / again[]`、`pearnly_guide.company / doc_ids`。
    - Cowork 的口头作答记法是 `precheck_source.py:521 remember` → `push/application/service.py:715 save_choices`：值要在主档里，但搜索类不核是不是卡上给过的。
    - 代录开始前的题按 `operator_source.py:117-121` 的说明，「由 CC 调 `write_precheck.api.check` 取、用 ask_user 问」，仍是模型路。
18. **代录 AI 接手**：同 P2 第 4 节第 9 条。

不写账本、但进报税草稿或盘点数的模型值（不算本节必须改，列出备查）：

- `cowork_vat_draft.exempt_sales / excess_brought_forward / zero_vat_as`（`:3116`）；
- `cowork_wht_draft.edits / company_tax_id`（`company_tax_id` 给了就不核是不是本对话公司，`:3148`）；
- `cowork_wht_answer.answers`（按整个工作空间记住）；
- `cowork_stocktake_count.qty / code / warehouse`；
- `cowork_recon_answer.value`。

---

## 5. 登记了但没人出题 / 没接的

- Cowork 的引导声明在代录模式里列了 `mrerp`（`guide: assistant/guides.py:9-18`），但 `MRERP_OPERATOR_ENABLED = False`（`main: pearnly_core/express_operator/contracts.py:74`），交单回 `operator_express_only`。
- `core: scripts/check-stop-exits.py:79-89` 的 `SOURCES` 不收 Cowork 的登记处。第 3 节标「未归类」的 Cowork 码，闸门都看不到。
- MR.ERP 导入菜单 / 默认值不进 Cowork 判定卡（`precheck_source.py:127-133`，「本批不进卡片，评估 B5」）；ERP 判定卡已有这两类（P2 第 30 行）。
- 存货科目组不进 Cowork 判定卡（`precheck_source.py:30 CHOICE_KINDS`）；ERP 判定卡有这一题（P2 第 20 行）。
- 第 1 期登记的新题类 `cash_account / unit / new_item / new_party / cost_basis` 和载荷 21 新键：同 P2 第 5 节。Cowork 共用同一个 `choices.resolve`，同样没有出题、没有填值。
- 除采购 / 销售外，Cowork 没有任何引导声明（`guide: assistant/guides.py`）。
- 「整个工作空间」这一档记忆（录不录、报税收入类型）不在声明的 `REMEMBER_SCOPES`（`guide: pearnly_core/assistant/guide/declaration.py:35`）里。
- 未查到：
  - POS 认列在网页 / LINE 上的入口（`pos_import/` 没有 whitelist；文件夹任务 `folder_intake/pump.py:243` 用同一服务）。
  - 报税草稿、回读的网页入口（`tax/`、`readback/` 没有 whitelist）。
  - 补页重读的网页入口（`into_document` 只被 `assistant/tools.py` 调）。
  - 按单据算对象存公司规则的网页同一函数（网页 `companies/api.py:112 choice_set` 按 key 改）。
  - 识别时 MR.ERP `payment` 在推送侧怎么落到菜单（没读 MR.ERP 推送侧）。
  - `posting_needs_review` 的产生处。
- 第 4 节第 1 条「识别时给了 `posting_kind` 就不再问商品单入账方式」是按代码路径推断的，没有实测。

---

## 6. P3 施工建议的顺序

只按「能不能直接复用现有卡」排，不写时间。

1. **推送路径上判定卡已出的题，直接复用 `pearnly_guide`**：第 5、7–18、20、21 行。同时收掉并行的模型路：`cowork_choose_document_policy`；识别 / 推送参数里的 `direction / posting_kind`；口头作答的 `pearnly_precheck_answer`；代录开始前的 ask_user。
2. **复用判定卡、补两个题类**（ERP 已有同类题）：MR.ERP 导入菜单 / 默认值（第 23 行，选项函数 `connections/service.py:489 mrerp_menu`）、存货科目组（第 22 行，`connections/service.py:427 stock_acc_group`），都进 `precheck_source.py:81 document`。识别时的 MR.ERP 现 / 赊（第 24 行）同批定成题。
3. **复用允许卡，或改成表单引导（`guide:` 引擎 `source=form`）**：
   - 已有允许卡的：定时任务的建 / 改 / 启停 / 删除（第 48–54 行）、公司规则 / 笔记（第 56–58 行）。
   - 现在没有卡、会改账本或写不写的：删单据（第 28 行）、结果不明确认（第 10 行直写）、立即运行（第 53 行，同时要过写入闸）、改票面（第 26 行）、补页（第 27 行）。
4. **新题类、中途才知道的**：POS 认列（第 31 行，每列从表头选 + 枚举）、对账结果问题（第 36 行）、报税收入类型与待确认行（第 42–43 行）。这几类都要「中途出卡」。
5. **不写账本的，优先级最低**：回读（第 33 行）、对账开始（第 35 行）、报税草稿开始（第 39–41 行）、盘点（第 45–47 行）、看板（第 59 行），用表单引导的 `month / file / choice` 即可。
