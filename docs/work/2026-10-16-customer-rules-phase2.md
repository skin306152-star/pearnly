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

（施工中登记）
