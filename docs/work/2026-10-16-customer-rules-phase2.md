# 第 2 期：统一客户规则（数字会计员 / 客户记忆，2026-10-16）

依据 [施工计划](2026-10-15-digital-accountant-plan.md)「客户记忆：统一的「客户规则」」。设计先只读勘察出一版，再做对抗审查，按审查改正后定稿如下；
开发窗口按目标自定了审查留下的三问（卡片名、「让 CC 起草」位置、账套记忆与公司规则的先后）。原始稿与审查在本机 scratchpad，不进仓库。

## 定稿

1. **存储**：知识库 `Cowork Company Note` 原表原样（不改名、不加补丁）。新建 `Cowork Company Choice` 只放入账规则：company、workspace、kind（取 `push.express.choices.KINDS`）、
   subject、subject_label、value、value_label、branch（空 = 全公司）、source（本期只有 `person`）、evidence_json（人从复核页 / 当场选定的记那张单；历史提升的只记张数与最近日期，如实写）、
   session、谁 / 何时（存站点时区，输出经 `when()` 转 UTC）。键 sha256(company, kind, subject, branch) 唯一；每家最多 500 条。hooks 补齐 permission_query、has_permission、
   doc_events 守卫、拒绝分享，并进 `companies.access.DOCTYPES`。原生模型同步建表，本期无补丁。
2. **优先级（最具体的说了算）**：本票人工裁决 / 票面写明 > 账套记忆（人定，某份账套）> 公司规则 > Express 历史已定 > 名称匹配。存或提升一条公司规则时，同一事务里删掉这家各账套同键的记忆，
   规则立刻生效；之后某份账套再另选，以那份为准。从历史提升 = 存规则 + 经钩子把这家供应商的历史记成忘掉（幂等），删规则后回到「问人」（守住 V9「删掉后再推又问」）。
3. **纯函数** `push/express/company_choices.py`：覆盖、有效性（值必须在这份账套主档里，主档没报上来时放行到推送时再判）、「定了什么、按什么定的」。push 与 companies 都能导入；不放 contracts（策略不允许它导入 push）。
4. **推送接入**（单独一个提交，在多电脑 T5 第 1–3 批合入后变基）：新钩子 `cowork_push_company_choices(connection, account_set, account_row)`，按 `identity.company_identity` 直接查公司，捕获异常、不缓存；
   `_decisions` 在账套记忆之下垫一层公司规则；取不到规则时这一张不用「历史已定」（免得规则没读到却按别的来源自动写）；`resolve` 的付款来源加 `company_rule`，`pushed.PAYMENT_FROM` 加映射；
   冻结目标加 `decided_by {kind: source}`，经 F19 的 `written` 交给 CC（与第二轮加的 `account_from` 同一口径）。
5. **不做「这一张就这样、不记」的新通道**。「对话里明说」照旧经 `cowork_save_push_choices` 存进账套记忆，按第 2 条它已经高于公司规则。
6. **F18 当场选**：规则生效后 `needed` 为空时，`pending_api.answer` 跳过保存直接重推，前端按钮显示为直接推送，不报 `connection.config_invalid`。
7. **权限**：看入账规则要录入权限（同 `choices.listing`）；写要录入权限 + 这家至少一条 Express 连接的使用授权，并按覆盖本月那份账套的主档校验值，拿不到账套就拒绝。知识库照旧。
8. **CC 上下文**：`<team_notes>` 与各项上限不动；新增一小段 `<client_choices>`，只列公司规则（一次查询、最多 40 行），说明会自动套用；账套记忆与历史让 CC 用 `cowork_company_profile` 查。GUIDANCE 加一句：用到规则或知识库时说出是哪条。
9. **CC 工具**：新增 `cowork_company_choice_save` / `cowork_company_choice_remove`（都要点「允许」；subject 由服务端按单据算，也可传一行的 key 做提升）；`cowork_company_profile` 加 `rules`。
10. **界面**：保留「知识库」「记住的选择」两张卡，已有文案不改。公司规则行显示「全公司 / 分店 X」；账套行与历史行加「设为全公司」；公司规则行的付款 / 抵进项可用下拉直接改。
    「让 CC 起草」只放公司页「资料」里空的知识库卡（会话栏照外壳设计不显示空卡），点了在新对话里预填、不自动发送，预填写明「按这家的推送记录、记住的选择和 Express 历史起草几条」。
11. **新文案**（en / zh / th 同批，服务端进 `messages.tsv`）：全公司、分店 {b}、设为全公司、让 CC 起草、起草预填句、新错误码（`cowork.company.choice_not_found` / `choice_invalid` / `choices_full` / `choice_scope_invalid`）、工具步骤名与确认卡问句。
12. **挪到第 4 期**：冲突提示与确认、`proposed` / `cc_learned`、年度范围、`rule_conflict` 异常码、从回读纠正中学。
13. **收益记一笔**：多电脑 T5 新登记的本机账套键带主机名，账套记忆跟不过去，公司规则能跟过去。

## 进度

### 2026-10-16 施工（分支 `pearnly/customer-rules`，原基于 `pearnly/month-ledger` `b6f163cf`，复审后变基到集成分支 `pearnly/cc-shell-rebuild` `5db2c92b`（含第二轮 `4083977e` / `96b526d6`）；本地完成，未推送、未合入、未部署）

**交付了什么（按定稿条目）**

1. 存储：新建原生表 `Cowork Company Choice`（`companies/rules.py` 管形状与归属），知识库原表不动。键 = sha256(公司, 类别, 归类键, 分店)，
   同键覆盖；每家最多 500 条（`cowork.company.choices_full`）。证据如实记：账套提升记哪份账套、谁何时选的；历史提升记张数与最近日期；
   CC 按单据存记那张单。时间存站点时区，给页面与 CC 前经 `service.when` 转 UTC。守卫同知识库（`permission_query` / `has_permission`
   拒绝、控制器守卫、拒绝分享，进 `companies.access.DOCTYPES`）；删主档时连同规则一起删（账套记忆不动）。没有补丁。
2. 优先级：本票人工裁决 / 票面写明 > 账套记忆 > 公司规则（分店的 > 全公司的）> Express 历史已定 > 名称匹配。
   存、改、提升公司规则都在同一事务里删掉这家各账套（分店规则只删那个分店的账套）同类别同归类键的记忆。
   从历史提升 = 存规则 + 经钩子 `cowork_company_history_forget` 把这家供应商的历史记成忘掉；删规则后回到问人（V9 不变）。
3. 纯函数 `push/express/company_choices.py`：`overlay`（账套记忆值还能用就说了算，记的值已不在主档里时落到规则）、`valid`
   （按这份账套主档核，主档没报上来放行）、`pick`（分店的比全公司的具体，别的分店不用）、`decided_by` / `finish`。
   `choices.resolve` 加了可选的 `trace`，不给时结果不变。
4. 推送接入：单独一个提交「feat: push reads company choices…」，放在最后，便于多电脑 T5 第 1–3 批合入后变基。
   - 新钩子 `cowork_push_company_choices`（`companies.rules.for_push`）：按目录这一行的身份直接查公司，不缓存。
   - `_decisions` 在账套记忆之下垫公司规则；钩子出错时这一张不用历史已定。
   - 付款由规则定时，载荷 `doctype_src = company_rule`，`pushed.PAYMENT_FROM` 加了映射。
   - 冻结目标加 `decided_by {kind: source}`；`outcomes` / `written` 读目标交给 `pushed.written`，多给 `decided_by`。
   - 费用科目由规则定时，`_account_from` 先认规则，载荷 `account_source = company_rule`，`pushed.ACCOUNT_FROM` 加了映射（见下方复审修正）。
5. 不做「这一张就这样、不记」的新通道；对话里明说的照旧存进账套记忆，按第 2 条高于公司规则。
6. F18 当场选：展开后 `needed` 为空时按钮是「推送」，交空的选择；`pending_api.answer` 收空表时不记、直接重推，不再报 `connection.config_invalid`。
7. 权限：
   - 看：同记住的选择，要录入权限。
   - 写：录入权限 + 覆盖本月那份 Express 账套所在连接的授权，值按这份账套全部主档核（`connections.preparation.reported_masters` 加了 `keys` 参数）。
     这家（或这个分店）没有覆盖本月的账套就拒绝（`choice_scope_invalid`）；目录完全没报会计期间时用最新年度那份。
   - 删：录入权限 + 这家任一条 Express 连接的授权。
8. CC 上下文：`<team_notes>` 与原有上限不动，新增 `<client_choices>`：
   - 一次查询，最多 40 行，名称与值都转义，写明推送时自动套用。
   - 整段截断时补上没闭合的标签。
   - GUIDANCE 加一句：用到规则或知识库时说出是哪条；另加一句：整家公司的选择用新工具，只管这一张的照旧。
9. CC 工具：`cowork_company_choice_save` / `cowork_company_choice_remove`，都要点「允许」，卡片列公司 / 范围 / 对象 → 值。
   - 归类键由服务端按单据算（对方档、存货、费用科目、付款、抵进项、部门），也可传资料里一行的 key 做提升。
   - `cowork_company_profile` 加 `rules`；`choices` 里只给可提升的行和规则留 key。
10. 界面：两张卡与已有文案不改。
    - 公司规则行写「全公司 / 分店 X」（总公司写「总公司」），付款 / 抵进项可用下拉直接改。
    - 账套行、历史行加「设为全公司」（`companies.api.choice_promote`）。
    - 公司页「资料」里空的知识库卡给「让 CC 起草」：新对话预填、不自动发送；会话栏照旧不显示空卡。
11. 新文案（en / zh / th，服务端进 `messages.tsv` 并重建翻译包）：全公司、分店 {b}、设为全公司、让 CC 起草、起草预填句、
    `choice_invalid` / `choices_full` / `choice_scope_invalid`（`choice_not_found` 原已有，沿用）、工具步骤名、确认卡问句。

**与定稿的出入**

- 当场选「直接推送」的按钮文案定稿没列，新加了「推送 / Push / ส่ง」（`co.pushNow`）。
- 定稿写「与第二轮加的 `account_from` 同一口径」：变基到含第二轮的集成分支后已按同一口径接上（见下方复审修正），不再由 `decided_by` 推。
- 证据：账套行提升时账套记忆没记是哪张单，如实记账套、谁、何时；只有 CC 按单据存的才记单据号。
- 部门（整份账套一个）可存成规则；POS 收款科目只能从账套行提升，CC 不按单据算。

**验证（本地）**

- 新增与改动的测试：
  - `tests/test_cowork_companies.py`：优先级矩阵（含「账套记忆胜过公司规则」）、存 / 提升同事务删记忆、从历史提升经钩子忘掉历史且删规则后回到问人、
    权限、主档核值、没有账套拒绝、上限、钩子按身份认公司、删主档连同规则、`<client_choices>` 转义与上限、CC 工具与允许卡、
    F18 空作答直接重推。
  - `tests/test_cowork_account_suggest.py`：推送接入；规则高于历史、钩子出错不用历史、`decided_by` 经 `written` 交给 CC。
  - `tests/test_assistant_ui.py` / `tests/test_cc_mobile_content.py`：规则行、设为全公司、直接推送、让 CC 起草。
- Frappe 相关的 `test_cowork_push_account` / `test_assistant_push` / `test_cowork_push_contract` 在离线容器
  `pearnly/frappe-sample:16.34.1`（`--network none`、只读挂载）通过。
- `check-architecture`（0 违规）、`check-i18n --strict`、`i18n-build --check`、前端 `npm run build` 通过；
  全闸门 `SKIP_NPM_CI=1 gate.sh` 在含推送接入的工作树上 85 步全绿。
- 没做：真实 Site 建表（`bench migrate`）、测试机真机点验、第二个测试用户的多人场景；都要等下一步。

### 2026-10-16 复审修正（复审 HEAD `c5fa4ee2`，无阻塞项）

- 中 1 `account_from` 与第二轮口径不一致：整条分支变基到 `pearnly/cc-shell-rebuild` `5db2c92b`，推送接入仍是最后单独一个提交。
  - `_decisions` 同时收 `since`（第二轮）与 `account_row`（本期）。
  - `_account_from` 在任务默认、Express 历史之后先认公司规则（按 `seller_tax` 同一取法算归类键），不再落到 `remembered`。
  - `ACCOUNT_FROM` 加 `company_rule`；`written` 不再从 `decided_by` 推 `account_from`。
  - `decided_by` 的 `history` 改为 `express_history`，与第二轮同一套词；给 CC 的 WRITTEN_NOTE 补上 `company_rule` 与 `decided_by`。
- 低 2 CC 按单据存规则只认 `seller_tax`（同 `choices._party`），票面只有 `seller_tax_id` 时按名字存，推送查得到。
- 低 3 历史行只能提升为全公司规则；带分店报 `choice_scope_invalid`，不碰历史与记忆。给 CC 的工具说明同步写明。
- 低 4 合并显示的对方档行提升时，组内每个归类键各取最新值各存一条规则，同键记忆一起删。
  非最新的分店值已不在本月主档里的跳过，那份账套记忆照旧留着；最新那条不能用时整条拒绝。
- 低 5 确认卡 key 找不到时显示空，执行时报找不到；形状不对（类别不认、没给品名、单据没有卖方、归类键为空）改报新词条
  `cowork.company.choice_unsupported`（「无法保存这条选择」，en / zh / th / ja）。
  - 没用复审建议的 `connection.config_invalid`：它的文案是「请检查 ERP 连接配置」，对这些情况同样误导。
  - `choice_invalid` 只留给值不在主档里。
- 说明 3 条（分类映射时不问费用科目、`_drop_memories` 只删本人可用连接上的记忆、忘掉历史写文件不跟事务）是原有行为，
  记入真机与多人测试核对项，这次不改。

**复审修正后的验证（本地）**

- 新增测试：
  - `test_cowork_account_suggest`：规则定的科目在票传上来以后（`chosen_at` 查不到 / 更晚）仍报 `company_rule`；
    历史定的报 `express_history`；`written` 只按 `account_source` 给 `account_from`。
  - `test_cowork_companies`：历史行带分店被拒；合并对方档行各分店都存；形状错误的新词条；按 `seller_tax_id` 存的键；
    确认卡不露 key。
  - `test_cowork_assistant_tools`：`company_rule` 映射。
- 离线容器 `pearnly/frappe-sample:16.34.1`（`--network none`、只读挂载）：`test_cowork_push_account`、
  `test_assistant_push`、`test_cowork_push_contract` 共 71 个通过。
- `check-architecture` 0 违规；全闸门 `SKIP_NPM_CI=1 gate.sh` 在复审修正后的工作树（推送接入为最后一个代码提交 `0e08e468`）上 85 步全绿。
- 还没做：真实 Site 建表（`bench migrate`）、测试机真机点验、第二个测试用户的多人场景。

### 2026-10-16 测试窗口：规则对象与来源（本分支 `pearnly/cc-shell-rebuild`，本地提交、未推送）

- 推送冻结用上的公司规则（目标 `company_rules`：编号、对象、值、范围），CC 回读 `written.company_rules` 说是哪一条、现在还在不在
  （钩子 `cowork_push_company_choices_saved` → `rules.values_now`）；不再叫 CC 去 `<client_choices>` 找。
- `<client_choices>` 每行带对象税号，写明是现在的规则、不是入账记录。
- CC 存规则加 `expect_party`：服务端按传进来的单据算对象，与 CC 说给用户的对方对不上就不出卡片、不写库（共用层工具声明 `refuse`）。
- 详见 [CC 外壳任务记录](2026-10-16-cc-shell-rebuild.md) 同日条目。
