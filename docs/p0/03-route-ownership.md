# P0.03 路由归属表（第一版）

基线：`pearnly-app` @ `ecdacdba`（= `~/Developer/Pearnly/pearnly-tenant-isolation`，只读）。
输入：计划包 `appendix/route-inventory.csv`（732 条声明）+ `route-owner-candidates.csv`（735 条装配），逐项与源码核对。

本版解决"谁的路由、挂什么权限、受什么开关管"。**逐条的数据副作用与新旧路径映射仍未完成**，见文末。

## 前端入口（`routes/pages_routes.py`，28 条）

这是现有产品的入口地图，直接决定新架构要搬几个前端：

| 路径 | 指向 |
|---|---|
| `/login/`、`/reset/` | 登录与重置 |
| `/home/` | 主入口 |
| `/cowork/`、`/work/` | Cowork 与协作 |
| `/erp/`、`/pos/`、`/cashier/`、`/ai/` | ERP 与 POS 相关 |
| `/dms/` | DMS |
| `/daily/` | Daily |
| `/admin/`、`/earn/` | 后台（`/earn` 即当前 admin） |
| `/terms/`、`/privacy/` | 静态法务页 |
| `/pos-sw.js`、`/cashier-sw.js`、`/daily-sw.js` | Service Worker |
| `/api/health`、`/api/ready`、`/api/contact` | 探针与联系表单 |

## 权限与模块模型（`services/authz/registry.py` + `services/modules/store.py`）

这块是后续所有权限合同的基准，先固定下来：

- **权限码**形如 `<模块>.<资源>.<动作>`，共 13 组：`sales`、`purchase`、`acct`、`tax`、`recon`、`ar`、`inv`、`pos`、`intake`、`stockcard`、`erp`、`billing`/`settings`（横切）、`field`（字段级遮蔽）。
- **模块开关**（`tenant_modules`）8 个键：`sales`、`expense`、`accounting`、`recon`、`receivable`、`inventory`、`pos`、`config`。权限码按前缀映射到模块，**模块关 = 整组 403 `module_disabled`**。`tax`、`erp`、`billing`、`settings`、`field` 当前不挂模块开关。
- **6 个角色**：`owner`（全权）、`admin`（全集减去 `billing.manage` 与 `erp.endpoint.manage`）、`accountant`、`clerk`、`viewer`、`cashier`（仅 `pos.sale.operate`、`pos.shift.operate`，走 POS 双令牌体系，不进 memberships）。
- 启动时 `selfcheck()` 校验角色码集与 `roles` 表 JSONB，防止码漂移。

## 重要更正：静态索引的 guard 列不能当安全结论

`route-inventory.csv` 显示 **732 条里有 335 条在声明处看不到守卫**。实测这是**扫描口径问题，不是缺口**：

- 全仓只有 2 个 router 用 `APIRouter(dependencies=[...])`，没有任何认证中间件（只有 CORS、限流、RequestContext、SecurityHeaders）。
- 真实守卫写在**函数体内**：`auth_member(request, "<权限码>")`、`_authorize(request)`、`_require_user` 等，配合 `db.get_cursor_rls(tid, …)` 的租户级游标与 `gate(cur, tid)` 的模块闸。

例：`routes/accounting_routes.py:92` 的 `GET /vouchers` 声明处无守卫，函数体第一行是 `auth_member(request, "acct.entry.view")`，随后 `get_cursor_rls(tid)` + `gate(cur, tid)` + `resolve_ws(...)`。

**结论**：判断某条路由的认证主体必须读函数体，不能靠索引的文本列。逐条认证主体核对见文末待办。

全量权限码取用统计（`auth_member` 调用，13 个文件）：`purchase.doc.view` 13、`acct.entry.review` 11、`purchase.supplier.manage` 10、`acct.entry.view` 10、`purchase.doc.approve` 9、`purchase.doc.create` 5、`acct.coa.manage` 5、`purchase.doc.edit` 5、`acct.entry.approve` 3、`acct.settings.manage` 2、`purchase.settings.manage`、`purchase.doc.delete`、`intake.upload`、`acct.ledger.export` 各 1。

## 后端 API 归属（按 URL 前缀，共 732 条声明路由）

### 平台控制面 → 新 `pearnly_control`

| 前缀 | 条数 | 文件 |
|---|---|---|
| `/api/admin/*` | 80 | `admin_users_{query,mutation}`、`admin_{cost,logs,diagnostics,migration,ocr_engine,pos_entitlement,pearnly_ai,daily,dms,erp}`、`auth_admin{,_risk}`、`tenant_routes`、`billing_topup`(部分) |
| `/internal/*` | 9 | `admin_diagnostics_routes`（`deploy`、`install-playwright`）、`services/cloud_tasks/routes`、`services/cloud_runtime/application` |

依据：registry 注释标注"超管"，全仓 `_require_super_admin` 出现 80 次。

### 身份与会话 → 跨产品核心（新 OIDC/Frappe session）

`/api/auth/*`、`/api/login`、`/api/logout`、`/api/me/*`、`/api/switch-company`、`/api/my-companies`、`/api/v1/*`（别名）。
文件：`login_routes`、`auth_{email_code,password,me,admin,admin_risk}_routes`、`oauth_routes`、`oauth_line_routes`、`line_account_merge_routes`、`services/auth/auth_signup`。

### Cowork（WeKan 看板 2026-09-21 起不再迁移，`/api/work/*` 只作旧系统记录）

| 前缀 | 条数 | 文件 |
|---|---|---|
| `/api/cowork-line/*` | 16 | `cowork_line_binding_routes`、`cowork_line_intake_routes`、`cowork_line_webhook_routes` |
| `/api/work/*` | 11 | `work_bridge_routes`（WeKan 桥） |
| `/api/cowork/*` | 14 | **`stocktake_routes`（库存盘点挂在 cowork 路径下）** |
| `/liff/cowork-*` | 2 | 绑定与接入的 LIFF 入口 |

### DMS

| 前缀 | 条数 | 文件 |
|---|---|---|
| `/api/dms/*` | 16 | `dms_routes`、`dms_roster_routes` |
| `/api/line/*`（DMS 部分） | 12 | `line_dms_credentials_routes`、`line_dms_portal_routes`、`line_dms_booking_edit_routes` |
| `/liff/dms-booking`、`/home/dms-booking`、`/login/dms-booking` | 3 | 预约编辑入口 |

### ERP 门户（商家经营）

| 前缀 | 条数 | 代表文件 |
|---|---|---|
| `/api/pos/*` | 68 | `pos_{sales,auth,report,shift,sales_log,taxinv,modules,restaurant,restaurant_admin,payment,sheets}_routes` |
| `/api/purchase/*` | 39 | `purchase_routes`、`purchase_config_routes`、`purchase_intake_routes`、`supplier_posting_routes`、`export_routes` |
| `/api/accounting/*` | 36 | `accounting_routes`、`accounting_bank_routes`、`accounting_books_routes` |
| `/api/sales/*` | 35 | `sales_routes`、`sales_{export,seller,send,settings}_routes`、`products_routes` |
| `/api/workorder/*` | 26 | `workorder_{,financials,review,bank_sales}_routes` |
| `/api/recon/*`、`/gl-vat/*`、`/bank-v2/*`、`/tasks/*` | 30 | `recon_routes`、`recon_jobs_routes`、`recon_routes_{v1_batch,glvat,bankv2,bankv2_run}` |
| `/api/tax/*`、`/api/tax-profile/*` | 14 | `tax_routes`、`tax_profile_routes` |
| `/api/bank-recon/*` | 9 | `bank_recon_routes` |
| `/api/inventory/*`、`/api/stockcard/*` | 11 | `inventory_routes`、`inventory_report_routes`、`stock_card_routes` |
| 其余 | 约 45 | `clients_routes`、`categories_routes`、`settings_routes`、`report_routes`、`history_routes`、`exceptions_routes`、`email_ingest_routes`、`uploads_routes`、`fileconv_routes`、`ocr_*`、`vat_excel*`、`payroll_routes`、`rd_routes`、`summary_import_routes`、`import_routes`、`daily_routes` |

### 对外 ERP 集成层

`/api/erp/*` 共 **76 条**：`erp_routes`（推送）、`erp_push_log_routes`、`erp_endpoints_routes`、`erp_mappings_routes`、`erp_agent`（Express 本地 Agent 出站拉取）、`erp_bridge_routes`、`erp_intake_routes`、`erp_stock_routes`、`erp_team_routes`、`erp_listing_routes`、`erp_export_routes`、`erp_confirmation_status_routes`、`erp_target_projection_routes`、`erp_push_debug_routes`、`erp_shared_express_*`、`erp_express_account_routes`。

### 计费与额度

`/api/credits/*`、`/api/subscription/*`，文件 `billing_{credits,topup,records,subscription}_routes`；`billing.view`/`billing.manage` 是横切权限码。

## 待确认归属（不能靠 URL 判定）

1. **`/api/erp/*`（76 条）**：是 ERP 门户产品内的能力，还是跨产品共用的对外集成层？影响它落在哪个 App。
2. **`/api/ai/*`（22 条）**：`steward_*`、`front_desk_routes`、`workorder_routes`(部分)。门闸 `pearnly_ai_m1` 是平台级功能开关还是产品内功能？
3. **`/api/daily/*`**：Daily 周记账有独立门闸 `daily_finance` 与"每用户独立租户隔离"，是独立产品、ERP 内功能，还是待退役入口？
4. **`/api/cowork/*` 下的 `stocktake_routes`**：路径属 Cowork、业务属库存。这正是"按 URL 分不准"的实例，需按消费者定。
5. **`/api/history/*`、`/api/ocr/*`**：是共用能力，还是 ERP 专属？
6. **`/api/workspace/*`（20 条）**：账套管理属平台还是产品内？
7. **`/api/v1/*`（12 条）**：哪些是真别名，哪些是对外消费者的契约（不能随意改）？

## 本轮未完成

- 732 条路由**逐条**的认证主体、账套对象、数据副作用、新旧路径映射（需读函数体，逐文件推进）。
- 176 表参考清单与真实 schema 的差异分类。
- 每条代表流程（网页、LINE、WeKan、Companion）。
- ADR-001、ADR-002 定稿。
