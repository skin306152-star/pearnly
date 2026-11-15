# CC 引导式 P2（ERP 采购 / 销售，含代录）：开始前的选择点盘点（11-15）

主会话 11-15 要求在等前端分支的空档里先盘清楚：采购、销售直写与代录开始前要问的全部选择点，按「步骤 × 值来源 × 服务端校验」列出，标出现在还靠模型复述的值。只读代码盘点（子代理），没改行为；P2 开工时按它定声明与步骤。


只读盘点，读的是 git 对象：

- `origin/main` = `0c87836b`
- `origin/pearnly/cc-guided-phase1` = `27b9d678`（第 1 期：`write_precheck/guide.py`、`assistant/guide_tool.py`、`choices.resolve(guided=)`、`contracts/required_fields.py`）
- `origin/pearnly/cc-guided-core` = `909971db`（`express_operator/choice_points.py`、`assistant/stop_exits.py`、`express/cc_guides.py`）

下文的 `main:`、`P1:`、`core:` 分别指这三个分支；行号是该分支上的行号。

## 0. 先说结论

1. **ERP 的单在写账前已经是一张 ERP 单据**（采购单 Purchase Document / 销售单 ERP Sales Document，或代录放文件后还没提交的收单记录 `I:`）。所以写进 Express 的大部分值来自 **ERP 单据字段 / ERP 主档**（`push.document` → `contracts.pages`）或 **识别结果**（收单记录 `result`）。这一段没有模型经手：识别结果是服务端识别引擎出的，ERP 主档是服务端读的。
2. 要人定的项由两处出：
   - **Express 侧**：`express_rules.push.choices.resolve`，经 ERP 的 `express/prepare.py` → `express/precheck.py collect`；
   - **ERP 侧收单记录**：`express/operator.py intake_items`，包括付款、行物料、仓。

   第 1 期的 `pearnly_guide` 由服务端出卡，卡上的值是服务端原值。但仍有几条路是**模型经手**的：口头作答 `pearnly_precheck_answer`、账套 `pearnly_conversation_ledger`、公司 `pearnly_file_conversation`、重录 `pearnly_write_plan.again`、代录 AI 接手，详见第 4 节。
3. 第 1 期在契约里登记了新题类 `cash_account / unit / new_item / new_party / cost_basis`，载荷 21 也登记了新键（`due_date_be / cash_account / unit_code / acccod / stkgrp / cost_choice / cost / negallow / type / payterm`）。但**全仓没找到任何服务端代码出这些题、也没找到填这些载荷键的代码**，现在只靠 CC 桌面版回 `*_CHOICE` 停下。详见第 5 节。
4. ERP 的引导菜单（`P1: pearnly_erp_portal/hooks.py:697`）是 `{"chat": ["collection"], "operator": ["purchase", "sales"]}`，`core: express/cc_guides.py` 也只放 `modes: ["operator"]`。也就是说 **ERP 直写的采购 / 销售还不在引导菜单上**，直写那一列是「如果放开会走哪条」。

---

## 1. 主表：步骤 × 值来源 × 服务端校验

列说明：

- **出题 / 判定**：现在由哪段代码判出这一题。
- **值来源**：选项和值从哪来。
- **服务端校验**：答案在哪核对、核对什么。
- **经手**：「服务端原值」= 卡上的选项码由服务端给、按 key 取回；「模型经手」= 值经过模型的工具参数。
- **直写 / 代录**：两种模式的差别。

| # | 选择点 | 出题 / 判定（文件:行 函数） | 值来源 | 服务端校验 | 经手 | 直写 / 代录差别；MR.ERP |
|---|---|---|---|---|---|---|
| 1 | 公司 | `main: masters/cc_tools.py` 的对话挂公司 `filed()`；没挂公司时 `pearnly_file_conversation`（`P1: assistant/tools.py:174`，`tools.file_conversation`）；写入闸 `main: express/cc_tools.py:118 _other_company`；计划按对话的公司 `P1: assistant/write_plan.py:171 _mode_and_company` | 对话 `project` 字段（新建对话时页面选的）；补挂时用模型在 `ask_user` 上列的公司（选项标签由模型写） | `file_chosen` 只认最近一张 ask_user 卡上用户点 / 填的、只能挂一次；写入时别家的单回 `other_company`；`pearnly_guide / pearnly_precheck` 的 `company` 只核「本人可见 + 有写权限 + 单据属于这家」（`main: express/precheck.py:84 allowed`、`:100 resolve_target`），**不核是不是本对话的公司** | 新建页选的是服务端原值；补挂走 `pearnly_file_conversation.company`，**模型经手（受卡片答案约束）**；`pearnly_guide.company` 是**模型经手**，但写入时由对话公司兜住 | 两种模式相同。代录按任务的公司（`tasks.read`） |
| 2 | 账套（本对话账套） | `main: express/conversation.py:68 wanted` → `target.py:49 pick(wanted)`；候选与默认在 `main: masters/cc_projects.py:82 ledgers`；第 1 期卡上第一题 `P1: assistant/guide_tool.py:76 _ledger_question`（「做不做」类，不预选） | Pearnly CC Book（各电脑心跳报的账套名单）按 税号 + 分店 + 月份 选；只有一本覆盖这个月时是默认；同期多本时按 `ERP Express Choice(account_set, year:<起始日>)` 取记过的 | `ledger.choose` 只认候选里的；第 1 期卡上的答案经 `guide_tool.result` → `ledger.choose(by="page")`（`:194`）；模型路径 `pearnly_conversation_ledger` → `P1: ledger.py:226 choose`，用 `ask.names_one_of` 核最近 ask_user 的答案（编号或名称）；`pick` 再按 `named` 核账套在这家公司名单里、核会计期间（`account_match.conflict`）、核有没有能写的电脑 | 第 1 期卡上和右栏是服务端原值；`pearnly_conversation_ledger.ledger` 是**模型经手**（受 ask_user 约束，ask_user 的选项标签由模型写） | 代录用 `conversation.for_task(task)` 取任务会话的账套（`main: push.py:423`）。MR.ERP：`cc_projects.ledgers` 对没勾 Express 的公司回 None，不走本对话账套 |
| 3 | 同期两本账套（不在 CC 对话 / 代录里时） | `main: target.py:62-69 pick` 回 `SEVERAL_BOOKS` → `main: prepare.py:181`（`P1` 同）`_needed_book` → `kind=account_set` | `pick` 给的候选 `{code: 账套键, name: 账套名 · 位置}`；预选 `main: precheck.py:234-240 _preselect` 用 `target.current` 能写的那本（来源 RULE，属于预选） | `precheck.save`（`main: precheck.py:515`）用 `code_rules.choose` 核值在 `full_options` 里，按公司 + 年度记住 | 卡 / 网页上是服务端原值；`pearnly_precheck_answer.answers[].value` 是**模型经手** | 在 CC 对话里不出这一题（`wanted` 不是 None 时走第 2 行）；`stop_exits` 把 `express.account_set_several` 归为 ask(ledger) p0 |
| 4 | 单据类型 / 方向（采购还是销售） | 代录放文件：`main: integrations/intake.py:499 receive_chat(direction="")` 先按采购读，票上卖方是本公司就改按销售（`:579 _read_chat`）；第 1 期卡上「业务对不上」题 `P1: guide_tool.py:102 _mismatch`（按票面写 / 不写，不预选） | 识别结果（卖方税号是不是本公司）；会话的 `business`（页面业务菜单） | `_mismatch` 只比方向；答「不写入」或没答的单留下（`guide_tool.py:196-200`） | 服务端原值；模型不出这一题 | 只有代录有。直写的采购 / 销售还没上引导菜单（`core: cc_guides.py`） |
| 5 | 交了不符合业务的票（转账凭条、预扣税凭证、非发票、作废票） | ERP：**未查到**出题处。`document_policy` 题类只在 Cowork 出（`P1: pearnly_cowork/assistant/pending_api.py`、`write_precheck/preselect.py:129`）；ERP 的 `express/precheck.py` 不产 `document_policy` | — | — | — | 未查到 ERP 侧「这张不是采购 / 销售票，录不录」的判定 |
| 6 | 已入账再办一次 / 跳过（重复票） | ERP 单据已写过：`main: precheck.py:316-317 document_view` → `not_writable / already_written`；代录收单记录和已写的单同票号、同税号、同金额：`main: operator.py:253 written_twin`、`:283 _intake_precheck` 回 `state=written`；交单时 `:576 submit` 再核 `_intake_twin`；跳过：`main: express_operator/api.py:163 waiting(again)` | ERP 单的 `ledger_state`；同公司已写的采购单（`doc_no / supplier_tax_id / grand_total`） | `again` 只认 waiting 名单里的单（`P1: write_plan.py:200-202`）；`stop_exits` 把 `already_entered` 归为 ask(fixed) p2，不是现成的 | **模型经手**：`pearnly_write_plan.again[]`（用户说「再录一次」，由模型转成单号）；开始卡上勾回再录（页面）是服务端原值 | 只有代录有「再录一次」。直写对已写的单回 `already_success`，不重写 |
| 7 | 上次结果不明（已入账 / 没入账） | `main: precheck.py:319-320`（`jobs.LOST`）→ `card.UNKNOWN`；直写用 `erp_resolve_push_result`（`main: cc_tools.py:682`） | 写账件状态 | 交回原来那台电脑按单号核 Express；`direct_push.permitted` 只放写入计划里的单 | `erp_resolve_push_result.document` 是模型给的单号（不是账本值） | 代录：`resolve(company, doc_id, entered, docnum)`（`main: express_operator/api.py:93`） |
| 8 | 对方：已有档（同税号多档 / 分店对不上） | `P1: express_rules/push/choices.py:232-258 resolve`（同税号 ≥2 档，或有同税号档但分店对不上，或记过的值已不在主档里）；被电脑拦回（`*_DUP_SUSPECTED / *_BRANCH_AMBIGUOUS`）走 `main: prepare.py:124 party_needed` | ERP 单上供应商 / 客户的 `express_code` 是来源单钉的码，照写不问（`push.py:153 _party`、`choices.py:244-247`）；选项来自 `facts.reported_suppliers / reported_customers`（CC 桌面版读回的主档）；预选 `main: precheck.py:241-248` 调 `preselect.party`（同分店唯一档 / 这个税号只有一个档；RULE，属于预选）；记忆 `ERP Express Choice(party, direction\|税号\|分店\|名称)` | `precheck.save`：值在 `full_options`（`api._options`）里；`contracts.expand`：有选项的题核在选项里，**带 `search`（对方 >20 条）的不核选项**，由产品 `save` 再核在整张主档里 | 卡上是服务端原值；`pearnly_precheck_answer` 是**模型经手**（尤其是搜索类，模型可以给主档里任何一个码） | 两种模式相同（代录交单时也走 `push.send → prepare`）。MR.ERP 不走这一题 |
| 9 | 对方：新建（同名疑似重复 A9；没有档） | `P1: choices.py:259-267`（guided 下：没有同税号档、但有同名档 → 选项 = 同名档 + `__new__`）；预选 `preselect.party` 给 NEW 时 `NOTE_NOT_IN_BOOK`（`main: precheck.py:244-245`） | 主档同名行；`__new__` | 同第 8 行；选 NEW 时载荷带 `confirmed_new`（`choices.py:607-608 apply_to_payload`） | 同第 8 行 | 新档的名称、税号、分店都来自票面 / ERP 主档（`prepare.py:147 master_party` 只在挂现成档时改）。**`new_party` 题（G-A8：类型、应付 / 应收科目）没找到出题处**，登记在 `P1: required_fields.py:444 OPEN_GAPS`（等 `reported_party_types`） |
| 10 | 对方：ERP 侧档（ERP Supplier / Customer） | 收单记录提交时按税号认或建档（`main: intake.py:282 build_payload` → `purchasing.entry`）；销售按票面买方税号预选客户（`intake.py` 的 `sales_recognition._customer`） | 识别结果 + ERP 主档 | ERP 提交函数自己核 | 服务端；不问 | **未查到**引导卡问 ERP 侧对方档（认错或新建都不问） |
| 11 | 付款方式：现购 / 赊购（Express 侧） | ERP 单据：`main: express/contracts.py:337 pages` 固定写 `payment_status=paid/unpaid`（来自 ERP `is_paid` / `payment_status`），所以 `payment_written` 为真；`P1: choices.py:283-297` 对 ERP 单**不出付款题**（A1 / A12 只在票面没写明时出） | ERP 单据 `is_paid`、`payment_status`（采购：`push.py:299`；销售：`:297`） | — | 服务端原值 | 两种模式相同 |
| 12 | 付款方式：已付哪种 / 未付（ERP 侧收单记录） | `main: operator.py:352 intake_items`，kind=`payment`，subject=`erp-intake-pay:<方向>\|<税号或名称>`（`:397 payment_subject`） | 选项：`line_flow.PURCHASE_METHODS / SALES_METHODS` + `credit`；预选 `:406 payment_default`：按对方记住的 → 采购看票面到期日晚于单据日记「未付」，否则「已付、现金」；销售没记过不预选 | 写回 `:433 apply_intake_answers`：值要在方式里（`_payment_values`），否则请求无效；**没答时 `:668-672 _submit_intake` 照 `payment_default` 写**（只有销售且没记过才停下 `NO_PAYMENT_TEXT`） | 卡上是服务端原值；`pearnly_precheck_answer` 是**模型经手**（可以加 `doc_id` 只改这一张，`P1: contracts.py:90 DOCUMENT_ANSWER_KINDS`） | 只有代录有（直写的采购 / 销售没有收单记录这一步）。**和第 1 期「没点过的预选不生效」不一致**：写入计划 `P1: write_plan.py:148 _sorted_out` 用的是 `contracts.held_back`（旧口径，预选不留单），交单时又按预选写了 |
| 13 | 付款：现金 / 转账路线（代录点哪个子菜单） | `main: push.py:507 operator_pay_route` → `menu_choice.pay_route` | ERP 付款明细（`ERP Purchase Payment.method`），没有就用单头 `payment_method` | — | 服务端 | 只有代录有；直写不分路线 |
| 14 | 现金科目（HP CSHPAY / HS CSHRCV） | **未查到**服务端出题处：`cash_account` 只登记在 `P1: write_precheck/contracts.py:78`、`required_fields.py:96/196`、`common.py:244`（载荷键）；现在靠 CC 桌面版回 `CASH_ACCOUNT_CHOICE`（`core: stop_exits.py:76` 归为 ask(receipt:choice) p2，不是现成的） | 账套设置（ISINFO）由 CC 桌面版取 | CC 桌面版拒写 | — | 代录：`MODE_GAPS.operator.express:HS CSHRCV`（`P1: required_fields.py:436-441`），由 Express 界面自己定 |
| 15 | 仓（仓位） | ERP 侧：单据行的仓 → `ERP Warehouse.express_code`（`main: push.py:184-189`），对不上 Express 仓 → `WAREHOUSE_UNMAPPED` 停下；收单记录有入库 / 出库行又没定仓：`main: operator.py:382-388 intake_items` kind=`location`（只有一个仓时预选它）；Express 侧多仓：`P1: choices.py:477 _locations`（guided 或 ask_location 时出 `location/*`，预选主仓） | ERP 启用的仓（`warehouses.names_of`）；`reported_warehouses` 里标 used 的仓；记忆 `location` | 收单记录：仓不在本公司启用仓里就请求无效（`operator.py:463-466`）；Express 侧：`save` 核值在选项里 | 卡上是服务端原值；`pearnly_precheck_answer` 是模型经手 | ERP 单据的行本来就带仓，`loccod` 在 `pages` 里，Express 侧通常不再问；收单记录的仓题只有代录有 |
| 16 | 物料：已有 / 新建 / 只记账（ERP 侧收单记录行） | `main: operator.py:368-376 intake_items` kind=`item`，subject=`erp-intake:<记录>:<行号>`，search=`own_products`；行上没 `decided` 的才问 | 识别时匹配的 `line_choices`（`product` 或 NEW 或 `@record`）作预选（RULE）；搜索 `main: operator.py:483 intake_search`（ERP 物料分页 + 新建 + 只记账） | `apply_intake_answers`（`:448-459`）：`@record` / NEW / `ERP Product` 存在；行号越界就请求无效；没定的行交单时停下 `UNDECIDED_LINES_TEXT`（`:664-666`） | 卡上是服务端原值（搜索选中的也是服务端返回的码）；`pearnly_precheck_answer` 是**模型经手**（搜索类不核选项，只核 `ERP Product` 存在） | 只有代录有 |
| 17 | 物料：落到 Express 哪个存货（库存行） | `P1: choices.py:327-392`：行上带 Express 码（ERP 物料 `express_code`）照写；Express 同名不问；否则出 `item` 题 | `reported_products`（库存品）；预选 `main: precheck.py:249-250` 固定给 NEW（RULE）；搜索 `products` | `save` 核在 `full_options` 里；搜索类在 contracts 层不核 | 同第 8 行 | 两种模式相同；`main: prepare.py:56 _current_codes` 会去掉 Express 里已经没有的码 |
| 18 | 走库存还是记费用（行的过账类型） | ERP 单据行 `item_type`（goods / service）+ 物料 `express_stock` 定（`main: push.py:177-184`）；行上声明了，`choices.resolve` 就不再按行问；收单记录由第 16 行定 | ERP 单据 / 物料档 | — | 服务端 | `goods_posting` 题类（`P1: contracts.py:72`）只在 Cowork 出；ERP 未查到出题 |
| 19 | 单位（票面单位对不上物料单位 G-A5） | **未查到**服务端出题处（`unit` 题类只登记）；ERP 行带 `factor`（ERP 物料单位表，`push.py:192-193`）；直写靠 CC 桌面版回 `UNIT_CHOICE`；代录弹框 `unit` 在 `core: choice_points.py:33`，`answer=None`，列在 `NOT_WIRED` | ERP 物料单位表 | — | — | 代录：`MODE_GAPS.operator.express:LINE`（`XTRNQTY / TFACTOR / XTRNVAL`）由 Express 界面自己处理 |
| 20 | 新物料的存货科目组 | `main: prepare.py:214-216`（共用规则回 `REASON_ACC_GROUP_REQUIRED`）→ `_needed_group`；电脑回 `STOCK_ACC_GROUP_UNFIT` → `:107 group_needed`；kind=`stock_group`，subject=账套键 | `stock_acc_group.fit_stock_acc_groups(config)`；预选 `main: precheck.py:265-269` 取账套里挂得最多的合格组（HISTORY，属于预选） | `save` 核在选项里，按账套记住，之后写进 `config.stock_acccod`（`prepare.py:39-41`） | 卡上是服务端原值；`pearnly_precheck_answer` 是模型经手 | 两种模式相同。载荷 21 的 `acccod / stkgrp / unit_code / negallow` 新键**未查到服务端填值处**（`OPEN_GAPS.express:STMAS`） |
| 21 | 成本口径（出库没有成本基础 G-A11） | **未查到**服务端出题处（`cost_basis` 只登记，`P1: guide.py:33 FREE_KINDS`）；靠 CC 桌面版回 `COST_BASIS_CHOICE` | — | — | — | 代录：`MODE_GAPS` 写明由 Express 界面处理 |
| 22 | 费用科目（进项服务行） | `P1: choices.py:398-412`：没有库存行，或有一行定为服务，并且 `account_resolved` 为假时出 `account/<供应商键>`；ERP 服务行的物料码就是科目号时 `main: prepare.py:69 _own_accounts` 记作已定 | `reported_accounts`；记忆按供应商；**ERP 不预选**（`main: precheck.py:231 _preselect` 没有 `account` 分支）；搜索 `accounts`；`hint` 在 ERP 不起作用（`express/precheck.py:478 check` 没有 hint 参数，`P1: products.py check` 只交给声明了它的产品） | `save` 核在整张科目表里 | 搜索类：**模型经手**（`pearnly_precheck_answer` 可以给任意合法科目，用户在卡上看不到） | 两种模式相同 |
| 23 | 收入科目（销项 A14） | `P1: choices.py:416-434`：定时任务设了默认就照用；guided 时按账套记一个（`account/revenue`），没记过就问，`current` = 连接默认 `revenue_acc` | `reported_accounts`；连接默认收入科目 | 同第 22 行 | 同第 22 行；而且 **ERP 的 `_item` 丢掉了 `current`**（`main: precheck.py:208-224` 只用 `_preselect`），卡上看不到连接默认的预选 | 只在 guided（CC 对话里）出 |
| 24 | 进项税抵不抵（小票 / 简易税票 vat） | `P1: choices.py:299-306`，条件 `_expense_receipt`：票种是 receipt / simplified，并且税额 >0、卖方税号 13 位 | 预选 `preselect.vat()`（expense） | `save` | — | ERP 的 `pages` 有税就写 `tax_invoice`、没税写 `receipt`，所以 **ERP 单永远不会触发这一题**（推断：照代码路径，没有实测） |
| 25 | 服务品进项 / 销项税口径（权责 / 收付实现 SRV_VATTYP） | 进项：`P1: choices.py:571 _service_vat`（`ask_service_vat=True`，`prepare.py:199`）；销项：`:532 _sales_service_vat`（只在 guided）；电脑拦回 `SERVICE_VAT_CODES` → `main: prepare.py:94 service_vat_needed` | 选项固定 `service_vat.OPTIONS`（1 / 2）；记忆按 账套 × 供应商（销项按账套）；预选 `main: precheck.py:259-261` `preselect.service_vat`（取这本账套各供应商口径的众数，RULE） | `save`；写进载荷 `srv_vattyp`（`choices.py:631-638`） | 卡上是服务端原值；口头作答是模型经手 | 代录：弹框 `service_vat_basis / service_output_vat_basis` 按 `srv_vattyp` 点（`core: choice_points.py:40-46`，已接上） |
| 26 | 明细对不上怎么录（A7） | `P1: choices.py:436-442`（guided）；按 `common.extract_line_items` 算 `items_status` 是 mismatch / incomplete | 固定 `amount_only / skip`，不预选（「做不做」类） | `save` 核在选项里，只管这一张 | 卡上是服务端原值 | ERP 单在 `contracts.checked` 已经要求行合计等于税前额（`main: contracts.py:330-332`），对不上直接 failed，所以 ERP 单基本不会出 A7（推断） |
| 27 | 日期与会计期间 | 日期：ERP 单据 `doc_date` / 识别结果 `doc_date`；期间：`main: target.py:57-58 pick` 用 `account_match.conflict` 回 `PERIOD_OUTSIDE` → `main: precheck.py:296` 归 `not_writable / period_mismatch`；关账：`prepare.py:213 respect_period_lock` 由 CC 桌面版回 `PERIOD_LOCKED` | 票面 / ERP 单 | `contracts.checked` 核日期合法 | 服务端；不问 | `stop_exits`：`express.account_period_mismatch` 归 ask(ledger) p2，`period_locked` 归 act；代录弹框 `period_outside` 是 BLOCK（`core: choice_points.py:53`） |
| 28 | 负库存 | 直写：**未查到**开始前的题；CC 桌面版回 `STOCK_INSUFFICIENT`（`stop_exits.py:88` 归 ask(fixed) p2，不是现成的）；新物料 `negallow` 载荷键没找到服务端填值；代录：弹框 `negative_stock` 是 BLOCK（`choice_points.py:51`） | — | — | — | 两种模式都是写的时候停下，开始前不问 |
| 29 | 销售员 / 部门 | 部门：`P1: choices.py:269-276` 不问，只用记过的，否则用单据簿默认（`required_fields DEPCOD default book_default`）；销售员：`required_fields.py SLMCOD auto=master`（取客户档，D-6） | 记忆 / 账套设置 / 客户档 | 记过的部门要在 `reported_departments` 里 | 服务端；不问 | 两种模式相同 |
| 30 | 单据簿 / 子菜单 | 直写：单别 `RECTYP` 是常量、`DOCNUM` 取序号（`required_fields`），**未查到**按单据簿选择；代录定制菜单子菜单：`main: precheck.py:346 menu_stop / :365 menu_item` kind=`operator_form` | 选项 = CC 读到的候选菜单文字（回执 `meta.operator`）；记忆 `operator_subject(账套, 单种)` | `main: precheck.py:376 _menu_answers`：值要是停下时的候选之一，否则请求无效；写进载荷 `operator.form_choice`（`push.py:483-485`） | 卡上是服务端原值；口头作答是模型经手 | 只有代录有。MR.ERP：`mrerp_menu`（导入菜单）、`mrerp_default`（主档默认值），`main: precheck.py:419 mrerp_needed` / `:451 save_mrerp`，用 `code_rules.choose` 核在选项里 |
| 31 | 凭证（分录、凭证号） | `required_fields VOUCHER / VOUDAT / GLJNL` 是 computed / constant | 由单据算出 | — | 服务端；不问 | — |
| 32 | 票面合计（写账前核对） | `main: printed.py:11 hold`：票面合计缺或不等于单据合计就停下（`contracts.printed_hold`） | 识别出的 `printed_total`，或人 / CC 填的 | `printed.set_total` 记历史 | `erp_set_printed_total.value`、`basis`：**模型经手**（只影响放不放行，不写进账本） | 两种模式相同 |
| 33 | 增值税登记户、免税写法、收款 / 退货科目 | POS 日汇总、退货（`precheck.py:262-273`） | — | — | — | 不在采购 / 销售范围，只简记 |

---

## 2. 代录中途可能弹的框 → 对应开始前哪一题

来源：`core: apps/pearnly_core/pearnly_core/express_operator/choice_points.py`。

| 账本 | 框（name） | cls | 开始前对应的题 / 原因码 | 现在接上了吗 |
|---|---|---|---|---|
| Express | `unit`（หน่วยนับ） | CHOICE | `ask=unit`（第 19 行） | **没接**（`answer=None`，列在 `NOT_WIRED`）；服务端也不出 unit 题 |
| Express | `picker`（ค้นหา） | BLOCK | `master_missing` → `party_missing / item_missing`（`stop_exits` 归 `precheck:new_party / new_item`，p2） | 停下转人；开始前没有对应的题（第 9、17 行只覆盖主档里有候选的情况） |
| Express | `duplicate_ref`（1029） | NOTICE | `duplicate_ref`（wait） | 自动点「ตกลง」 |
| Express | `service_vat_basis`（ภาษีซื้อของสินค้าบริการ） | CHOICE | `ask=service_vat`（第 25 行），走载荷 `srv_vattyp` | **已接**（2 点收付实现，其余点权责） |
| Express | `service_output_vat_basis`（ภาษีขายของสินค้าบริการ） | CHOICE | `ask=service_vat`（第 25 行，销项） | **已接** |
| Express | `no_history`（4108） | NOTICE | — | 自动点 |
| Express | `never_bought`（4079） | NOTICE | — | 自动点 |
| Express | `zero_price_list`（4107） | NOTICE | — | 自动点 |
| Express | `stock_warning`（605） | NOTICE | — | 自动点 |
| Express | `negative_stock`（4224 / 4091） | BLOCK | `negative_stock`（第 28 行；`stop_exits` 归 ask(fixed) DESKTOP p2） | 停下；开始前不问 |
| Express | `period_outside`（Message#70） | BLOCK | `period_outside`（第 27 行） | 停下；开始前判成 `not_writable` |
| Express | `license`（-330） | SESSION | `license`（act） | — |
| Express | `network`（-70 / 90621） | SESSION | `network_wait` | — |
| MR.ERP | `kicked` / `not_logged_in` | SESSION | `account_in_use` | — |
| MR.ERP | `bad_login` | SESSION | `login_failed` | — |
| MR.ERP | `period` | BLOCK | `period_outside` | 没有原生实录（`verified=False`） |
| MR.ERP | `required` | BLOCK | `recipe_mismatch` | 没有原生实录 |
| MR.ERP | `save_error` | BLOCK | `unknown_screen` | 没有原生实录 |

另外：登记表外的画面交给代录 AI 接手（`main: express_operator/ai.py`，Haiku → Opus 的 computer-use 一轮一轮操作），原因码是 `ai_resolved` / `unknown_screen`。**这一路由模型在账本界面上点**，见第 4 节第 9 条。

---

## 3. 现有原因码里属于「要人选」的

来源：`core: assistant/stop_exits.py` 里 `next=ask` 的码，再加 `*_CHOICE`。

| 原因码 | 选项来源（source） | 谁执行 | 现成 / 排期 |
|---|---|---|---|
| `choice_needed`（ERP，`main: express/contracts.py:67`，形如 `choice_needed:<kinds>`） | precheck:any | server | 现成 |
| `menu_choice`（代录） | receipt:menu_options | desktop | 现成 |
| `express.warehouse_unmapped` | precheck:location | server | 现成 |
| `undecided_items`（写入计划跳过） | precheck:any | server | 现成 |
| `ledger_needed` | ledger | server | 现成 |
| `last_write_result_unknown` | fixed | server | 现成 |
| `DUPLICATE_CONTENT_DIFFERS`、`PRIOR_DOC_STILL_IN_ERP` | fixed | server | 现成 |
| `ERR_MRERP_MENU_CHOICE_REQUIRED / _MENU_CHANGED / _DEFAULT_CHOICE_REQUIRED / _DEFAULT_CHANGED` | precheck:any | server | 现成 |
| `UNIT_CHOICE`、`COST_BASIS_CHOICE`、`CASH_ACCOUNT_CHOICE`（`common.py GUIDED_CHOICE_CODES`，回执 `meta.choice` 带候选） | receipt:choice | desktop | **p2**（`clean_choice_meta` 已经能净化回执，但**未查到把回执转成卡上一题的代码**） |
| `UNIT_CODE_NOT_FOUND` | precheck:unit | server | p2 |
| `ITEM_GROUP_NOT_FOUND` | precheck:stock_group | server | p2 |
| `PARTY_TYPE_NOT_FOUND`、`party_missing` | precheck:new_party | server / desktop | p2 |
| `item_missing` | precheck:new_item | desktop | p2 |
| `express.item_unmapped` | precheck:new_item | desktop | p4 |
| `CUSTOMER_/SUPPLIER_BRANCH_AMBIGUOUS`、`*_DUP_SUSPECTED` | precheck:party | server | p2（ERP 已有 `prepare.party_needed` 把它们转成第 8 行那一题，但 stop_exits 标的是 p2） |
| `express.account_set_several` | ledger | server | p0 |
| `express.account_period_mismatch`、`account_period_mismatch`、`period_outside` | ledger | server | p2 |
| `negative_stock`、`STOCK_INSUFFICIENT` | fixed | desktop | p2 |
| `already_entered`、`moved_to_not_entered` | fixed | server | p2 |
| `user_active`、`readback_mismatch`、`recipe_mismatch`、`unsupported` | fixed | desktop / server | p2 |
| `company_pick`、`unknown_screen` | screen | desktop | p2 |
| `tax_setting_mismatch`、`INVALID_DUE_DATE`、`FIELD_TOO_LONG`、`express.printed_total_*`、`ERR_*`（MR.ERP 字段类） | review | server | p2 / mrerp |

`write_precheck` 里「做不做」类、不预选的题（`P1: guide.py:29 WHETHER_KINDS`）：`document_policy / direction / account_set / current_book / detail / cost_basis`。

---

## 4. 现在仍靠模型复述、会写进账本的值（P2 必须改）

1. **`pearnly_precheck_answer.answers[].value`**（`P1: assistant/tools.py:122`，`write_precheck/api.py tool_answer`）：口头作答时，模型把用户的话转成选项码。覆盖对方档、存货、费用科目 / 收入科目、仓、存货科目组、服务品税口径、付款（收单记录）、代录子菜单、MR.ERP 菜单 / 默认值。有选项的题服务端核在选项里；**搜索类（`search=parties/products/accounts/own_products`）在 `contracts.expand` 不核**，只由产品核「主档里存在」，所以模型可以给出用户在卡上从没见过的合法码。
2. **`pearnly_precheck_answer.answers[].doc_id`**（只对付款）：模型决定这张单的例外只管这一张。
3. **`ask_user` + `pearnly_precheck_answer` 的组合**：第 1 期的 `refuse_ask`（`P1: guide_tool.py:240`）只在题目带 `search` 或引导卡挂着时才拒，不带 search 的业务题（付款、税口径、记不记）模型仍然可以自己用 ask_user 问，再用 precheck_answer 记下。
4. **`pearnly_conversation_ledger.ledger`**（`P1: assistant/ledger.py:226/256`）：模型给账套编号；服务端只核 ask_user 卡上的答案里有没有这个名字或编号，ask_user 的选项标签是模型写的。第 1 期引导卡上的账套题是服务端原值，但这条工具路还在。
5. **`pearnly_file_conversation.company`**：同上，决定写进哪家公司的账套。
6. **`pearnly_write_plan.again[]`**（`P1: write_plan.py:200`）：重复票「再录一次」由模型把用户的话转成单号；服务端只核在待录清单里。
7. **`pearnly_write_plan.documents[]` / `pearnly_guide.doc_ids` / `company`**：写哪几张由模型列。公司在写入时被对话公司兜住，单据列表没有服务端原值可对照。这一条不是账本里的值，但决定写不写。
8. **`erp_set_printed_total.value`**（`main: express/cc_tools.py:598`）：模型填票面合计（依据是模型看原件或用户的话）。不写进账本，但决定这张单放不放行。
9. **代录 AI 接手**（`main: express_operator/ai.py`）：登记表外的画面由模型直接在 Express / MR.ERP 界面上点，选了什么按钮由模型决定，结果原因码是 `ai_resolved`。
10. （隐性，不是模型，但违反「没点过的预选不生效」）**收单记录的付款预选**：没答时 `main: operator.py:668-672` 照 `payment_default` 写进 ERP 单，进而写进 Express 的现 / 赊。写入计划用 `contracts.held_back`（`P1: write_plan.py:148-160`），也不留单。
11. （隐性）`pearnly_precheck.hint`：ERP 不接收，不影响 ERP。

---

## 5. 读不清 / 没找到依据（未查到）

- **ERP 直写对话里怎么把识别的票提交成 ERP 单**：`cc_tools.py:399` 提到 `receive_chat` 记的 `session_id`，但只找到代录 `operator.intake` 一个调用方，没找到直写对话的上传 / 提交工具。`core: cc_guides.py` 写明直写整链还没走通。
- 出题处未查到：`cash_account`、`unit`、`new_item`、`new_party`、`cost_basis`（只登记在 `P1: write_precheck/contracts.py:76-87` 和 `required_fields.py`）。
- 载荷 21 键的服务端填值处未查到：`due_date_be`、`cash_account`、`unit_code`、`acccod`、`stkgrp`、`cost_choice`、`cost`、`negallow`、`type`、`payterm`。
- 把 `*_CHOICE` 回执（`meta.choice`）转成卡上一题的代码未查到。
- 到期日 / 账期（PAYTRM、DUEDAT）：收单记录有 `due_date`（`intake.py:295-301`），但它怎样进 Express 载荷（`due_date_be`）未查到。
- ERP 侧「交了不符合业务的票」（转账凭条、预扣税凭证、作废票）：未查到判定或出题。
- 负库存开始前的题：未查到（用户 11-14 定的「按 Express 规则问」还没有代码）。
- 单据簿（直写时选哪个 ISRUN 单据簿）：未查到选择点。服务端只有单别常量和部门默认取单据簿；CC 桌面版那一侧没读。
- 销售员：只在 `required_fields` 登记 `SLMCOD auto=master`，CC 桌面版取值的实现没读。
- 第 24 行（vat 抵扣）和第 26 行（A7）「ERP 单不会触发」是按代码路径推断的，没有实测。
- 收单记录的仓题没答时（只有一个仓时有预选），交单时怎么处理没读到（`apply_intake_answers` 只在答了时写 `chosen["warehouse"]`）。
- 代录开始卡（页面走 `express_operator.api.start(answers=…)`，不经 `pearnly_guide`）有没有也按「没点过的预选不生效」处理：没读页面代码。
