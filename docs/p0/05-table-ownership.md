# P0.05 数据表归属分类

输入：计划包 `appendix/schema-inventory.csv`，176 张表。
**重要限制**：该快照是参考 schema，**不是现网 schema**；本轮没有数据库访问，未核对线上实际的列、约束、触发器、RLS 与扩展。本表是**归属分类**，不是 schema 契约。

分类口径按计划要求：控制面 / 身份 / 产品本地 / 纯配置 / 待退役。

## 身份与账号（保留，跨产品核心）— 18

`users`、`memberships`、`member_scopes`、`user_company_roles`、`user_settings`、`roles`、`invitations`、`ownership_transfers`、`password_reset_log`、`login_failure_log`、`email_codes`、`api_keys`、`operation_logs`、`error_events`、`risk_log`、`ip_usage`、`monthly_page_usage`、`alembic_version`

## 租户与平台控制面（保留，落新控制 Site）— 15

`tenants`、`tenant_modules`、`tenant_subscriptions`、`tenant_credits`、`workspace_clients`、`platform_settings`、`platform_setting_allowlist`、`notification_rules`、`notification_logs`、`billing_balance_log`、`credit_transactions`、`subscription_log`、`topup_requests`、`payment_pending`、`ai_usage`

注：`ai_usage` 被 `routes/admin_ocr_engine_routes.py` 引用，属 OCR 引擎计量，**保留**。

## Cowork（保留）— 34

LINE 侧：`line_bindings`、`line_binding_codes`、`line_action_nonces`、`line_chat_history`、`line_message_refs`、`line_webhook_events`、`line_pending_actions`、`line_pending_intents`、`line_pending_entry`、`line_funnel_events`、`line_agent_anchors`、`line_agent_profiles`、`line_ocr_jobs`

客户与识别：`clients`、`client_assignments`、`client_name_aliases`、`client_rules`、`buyer_to_client_memory`、`ocr_history`、`ocr_correction_examples`、`ocr_cost_log`

进项与费用：`expense_categories`、`expense_draft`、`expense_learned`

其它：`document_number_sequences`、`import_template_mappings`、`exception_whitelist`、`exceptions`、`archive_settings`、`seller_workspace_routes`、`line_client_bind_codes`、`line_client_contacts`、`line_client_questions`

## DMS（保留）— 6

`dms_line_sessions`、`dms_operator_profiles`、`dms_masters_cache`、`line_dms_binding_codes`、`line_dms_bindings`、`mrerp_credentials`

## 对账（保留，属 Cowork）— 10

`vat_recon_tasks`、`vat_report`、`gl_vat_task`、`reconciliation_task`、`reconciliation_row`、`recon_jobs`、`bank_reconcile_candidates`、`bank_reconcile_sessions`、`bank_reconcile_transactions`、`bank_recon_v2_task`

## ERP 门户（保留）— 24

商品与库存：`products`、`product_units`、`warehouses`、`inventory_batches`、`inventory_stock`、`inventory_transactions`

采购：`purchase_docs`、`purchase_lines`、`purchase_attachments`、`purchase_settings`、`suppliers`、`supplier_categories`、`supplier_client_mapping`、`supplier_posting_profiles`

销售：`sales_documents`、`sales_document_lines`、`sales_document_sends`、`sales_settings`

客户与合规：`client_tax_profiles`、`client_period_obligations`、`invoice_risk_checks`

导出与归档：`export_archived_docs`、`export_google_credentials`、`export_oauth_states`

## 对外 ERP 集成（保留）— 8

`erp_endpoints`、`erp_oauth_states`、`erp_oauth_tokens`、`erp_account_mappings`、`erp_client_mappings`、`erp_product_mappings`、`erp_tax_mappings`、`erp_push_logs`

`erp_push_logs` 是推送状态的唯一来源，按计划不得降级为"HTTP 200 即完成"。

## 待退役（功能已确认不要）— 58

| 来源 | 张数 | 表 |
|---|---|---|
| POS 收银 | 15 | `pos_areas`、`pos_cashiers`、`pos_entitlements`、`pos_kot`、`pos_payment_settings`、`pos_payments`、`pos_sale_lines`、`pos_sales`、`pos_session_lines`、`pos_sheets_settings`、`pos_shifts`、`pos_store_codes`、`pos_table_sessions`、`pos_tables`、`pos_terminals` |
| 会计 | 11 | `chart_of_accounts`、`acct_bank_accounts`、`acct_bank_lines`、`acct_voucher_templates`、`accounting_settings`、`accounting_posting_failures`、`account_mappings`、`coa_erp_bridge`、`journal_lines`、`journal_vouchers`、`review_learned` |
| 报税 | 8 | `tax_filings`、`tax_obligation_defs`、`tax_settings`、`filing_lines`、`etax_channel_settings`、`etax_submissions`、`rd_cache`、`rd_daily_usage` |
| 工单 | 5 | `work_orders`、`work_order_events`、`work_order_items`、`work_order_deliverables`、`brain_shadow_log` |
| AI 管家与知识库 | 14 | `ai_contract_files`、`ai_goal_contracts`、`steward_attachments`、`steward_cost_entries`、`steward_messages`、`steward_sessions`、`steward_tasks`、`knowledge_answers`、`knowledge_bases`、`knowledge_chunks`、`knowledge_documents`、`knowledge_embeddings`、`knowledge_ingest_jobs`、`agent_turn_logs` |
| 工资 | 2 | `client_payroll_rows`、`client_payroll_templates` |
| 邮件接入 | 3 | `email_ingest_accounts`、`email_ingest_logs`、`email_ingest_seen_uids` |
| 未使用 | 1 | `line_voice_quota`（全仓只出现在一条 alembic 迁移里） |
| 影子双跑 | 1 | `shadow_money_log`（见下方说明：用户已确认新系统不做） |

### 影子双跑不迁移（用户确认 2026-09-17）

旧系统有一个机制：**B 档（经济档）正常出识别结果后，后台再异步用 A 档（高精档）
只读复核金额字段**，结果落 `shadow_money_log` 用于统计真实错误率（不改结果、
不计费、异常吞掉）。对应实现是 `services/ocr/shadow_money.py`、
`shadow_money_store.py`。

**用户明确：新系统这边不要影子双跑。** 因此：

- `shadow_money_log` 从"保留"改为**待退役**；
- 新系统里 A 档与 B 档是**各自独立的能力**：租户选了哪一档就用哪一档，
  不存在"用 B 档出结果、A 档在背后偷偷复核"的行为；
- `services/ocr/shadow_money*.py` 不迁移。

这条简化也意味着：**同一份文件不会因为双跑而被收两次费**，费用口径只按实际使用的那一档算。

`knowledge_bases` 同样只出现在 alembic 迁移，与其同批的 knowledge 表归入 AI 管家一族。

## 待确认 — 2

| 表 | 现状 | 需要判断 |
|---|---|---|
| `automation_rules` | `services/automation/schema.py`、`services/tenant/owner_users.py`、多个测试 | 是租户级自动化还是 WeKan 看板自动化；决定随平台保留还是随 AI 退役 |
| `excel_templates` | `services/tenant/owner_users.py`、`services/erp/credentials_rls.py` | 是导出模板（保留）还是表内 Excel 对账内测用的（退役） |

## 处置原则

1. **本轮不删任何表、不改任何 schema。** 待退役表在旧系统中照常存在。
2. 待退役表在新系统不建。旧库在切换通过观察期后按退役清单处理，且需先确认无引用。
3. `erp_push_logs`、`ocr_history`、`reconciliation_*` 这三个家族**不能因为界面里看不到就砍**：它们分别是推送状态、识别记录、对账的数据源。
