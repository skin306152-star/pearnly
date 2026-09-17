# P0.04 范围确认与「不在三个产品里」清单

日期：2026-09-17。

## 用户确认的范围

**只要三个产品 + 租户管理，其它不要。**

三个产品（以界面外壳为准）：

| 产品 | 入口 | 界面里有什么 |
|---|---|---|
| Cowork | `/cowork` | 录入工作台、识别记录、推送日志、对账中心、库存盘点、主数据、Express 推送手册 |
| ERP | `/erp` | 商品数据、库存、入库、出库、收发存报表、采购记录、供应商、采购设置、销售系统、主数据 |
| DMS | `/dms` | 身份证录入、推送记录、操作员、套餐与余额 |

租户管理 = 现在的后台（`/admin`，也叫 `/earn`）：发号、公司、产品与功能、域名、角色、账套。

## 更正上一条的错误

我此前把「库存盘点挂在 `/api/cowork/*`」说成路径与业务错位。**这个判断是错的**：库存盘点本来就在 Cowork 的侧边栏里，路径与界面一致。同理「对账中心」也在 Cowork 侧边栏，所以对账相关路由归 Cowork，不是独立产品。

## 有自己的入口壳、但不属于这三个产品的

| 入口 | 是什么 |
|---|---|
| `/pos` | POS 老板后台 |
| `/cashier` | POS 收银台（含 `/cashier-sw.js`） |
| `/ai` | AI 工具（智能管家、目标驱动前门） |
| `/daily` | Daily 周记账（含 `/daily-sw.js`） |
| `/home` | 主入口页 |
| `/work` | 协作页（WeKan 桥） |

`/reset`、`/terms`、`/privacy` 是功能页，不是产品。

## 后端有、但三个产品界面里看不到的能力

这些是"代码里存在、界面上没有位置"的功能，需要决定去留（**不是现在删，是决定新系统做不做**）：

| 能力 | 规模 | 说明 |
|---|---|---|
| POS 收银 | 68 条路由 | 开/交班、税票、餐厅、支付、报表、Google Sheet 留档 |
| 会计凭证与科目 | 36 条 | 凭证、科目表、账簿、银行 |
| 对账（含银行、GL/VAT） | 30+9 条 | **注意：对账中心在 Cowork 侧边栏里，属 Cowork** |
| 工单制 | 26 条 | 审核队列、签批闭环、月度报表、银行流水倒推 |
| 报税 | 约 20 条 | 泰国 RD 税务、VAT 报表检查、Excel 公式对账内测 |
| AI 智能管家与前台 | 22 条 | `pearnly_ai` 门闸；管家会话、SSE 事件流、目标驱动前门 |
| Daily 周记账 | 5 条 | 独立门闸 `daily_finance`，每用户独立租户隔离 |
| 工资 | 5 条 | ภ.ง.ด.1 工资预扣工具卡 |
| 邮件接入 | 6 条 | 邮件转单据 |
| Excel 公式对账内测 | 5 条 | 仅 `skin306152` 账号可见 |

## 处置方式（重要）

"不要"不等于"现在删"：

1. 本阶段只产出**不迁移清单**——新系统不做它。
2. 旧系统与旧数据保持不动，直到切换完成并通过观察期，才按退役清单处理。
3. 被点掉的入口，先确认没有别的产品在依赖它（例如推送日志、OCR 识别、对账中心本身就是 Cowork 的功能，不能因为"界面里没看到"就砍）。

## 待用户决定

**用户已确认（2026-09-17）**：协作页 `/work`（WeKan）留，属 Cowork；对账留，属 Cowork；其余"以前开发的功能，不要了"。

## 确认后的不迁移清单

不要：收银台 POS（`/cashier` + `/pos`）、AI 管家（`/ai`）、Daily 周记账（`/daily`）、会计凭证与科目账簿、工单制、报税、工资、邮件接入、Excel 公式对账内测。

处置仍是"新系统不搬"，旧系统照旧运行到切换完成并通过观察期。

## 依赖核对结果（`appendix/module-dependencies.csv`）

按"保留文件是否引用被砍模块"核对，结论分三档。

### 一档：可以干净去掉（保留侧几乎无引用）

| 模块 | 保留侧引用 |
|---|---|
| `services/ledger`（账簿） | 0 |
| `services/tax`（报税） | 0 |
| `services/daily`（Daily） | 0 |
| `services/etax` | 0 |
| `services/rd`（泰国 RD） | 1（`ocr_history/mutations.py`） |
| `services/payroll`（工资） | 1（`workspace/tax_profile_schema.py`） |

### 二档：必须"切一刀"，不能整块删

| 模块 | 谁在用 | 处理 |
|---|---|---|
| `services/ai_gateway` | **`services/ocr/` 的 5 个文件** + `expense/category_ai.py` | OCR 识别要留，所以只砍 steward/front_desk 那一支，保留 OCR 用的 AI 通道 |
| `services/fileconv` | `summary_import/pdf_table.py`、`reports/financials_pdf.py` | 汇总表批量建单要留，只砍独立入口 `/api/fileconv` |
| `services/pos` | `pos_schema.py`、`startup.py`、`modules/presets.py`、`cloud_tasks/handlers.py`、`admin_pos_entitlement_routes`（租户管理） | 清启动期 schema 与预设、云任务 handler，以及租户管理里的 POS 权益入口 |
| `services/steward` | `startup.py`、`cloud_tasks/handlers.py`、`cloud_runtime/schema.py`、`background_loops.py` | 清启动、后台循环与 schema |
| `services/email_ingest` | `background_loops.py` | 清后台循环 |

### 三档：会改动保留功能，需要确认

1. **会计（`services/accounting`）**：被 `purchase/posting.py`、`sales/document.py`、`sales/credit_note.py`、`export/entries.py` 引用。即**采购与销售记录现在会自动过账生成会计凭证**。
   **用户已确认（2026-09-17）：采购与销售录完之后不要会计。** 会计整套不迁移，见下节。
2. **工单（`services/workorder`）**：对账侧有 `recon/workorder_recon_adapter.py` 直接适配它。**对账要留、工单不要**，这个适配口必须拆掉；另有 `background_loops.py`、`cloud_tasks/workorders.py`、`cloud_runtime/schema.py`、`workspace/purge_files.py`、`workspace/client_alias_store.py` 需要清理。
3. **增值税计算与报税是两件事**：`services/vat` 被**对账**（`recon_routes`、`recon_routes_v1_batch`、`recon_routes_glvat`）引用。报税流程不要，但**税额计算要留**，否则对账无数据源。

## 范围缩减的量级

不迁移的功能约占 732 条路由中的 190 余条（POS 68、会计 36、工单 26、报税约 20、AI 管家 22、Daily 5、工资 5、邮件接入 6、Excel 内测 5）。这对目标机资源预算是明显利好，但**不改变 P1 必须先做资源样板的要求**。

## 会计不迁移的切法与连带影响

### 切法是干净的：只有 4 个挂点

采购/销售与会计之间只有一层薄接缝 `services/accounting/hooks.py`，4 个函数：

| 函数 | 作用 | 调用点 |
|---|---|---|
| `enqueue_posting` | 投递凭证 | `sales/document.py:335`、`purchase/posting.py:60,146`、`sales/credit_note.py:111` |
| `payment_event_id` | 生成过账来源 ID | `purchase/posting.py:151,233` |
| `void_for_source` | 按来源冲销 | `purchase/posting.py:322` |
| `void_or_reverse` | 冲销或红冲 | `purchase/posting.py:258` |

把这些挂点改成空动作即可，**采购与销售的主流程代码不需要改结构**。另有 `services/accounting/vouchers.py` 被 `purchase/posting.py:222,267` 与 `export/entries.py` 懒加载，一并移除。

付款/收款状态本身是业务事实，保留；只是不再产生凭证。

### 对账不受影响（已核对，不是推测）

- `services/recon/`、`services/recon_jobs/` **不引用** `services/accounting` 或 `services/ledger`。
- 对账里出现的 `gl_account`/`gl_opening`/`gl_closing`/`gl_rows` **是银行流水文件里的字段名**，不是我们的账簿。
- 对账自己的表：`vat_recon_tasks`、`reconciliation_task`、`gl_vat_task`、`bank_reconcile_*`、`vat_report`。

结论：对账能独立保留，不需要会计。

### 会踩到的坑：收发存报表的权限挂在会计模块下

`stockcard.report.view`、`stockcard.opening.manage` 两个权限码当前映射到 `accounting` 模块（`services/authz/registry.py` 的 `_PREFIX_MODULE`）。会计模块一删，**ERP 菜单里的"收发存报表"会被模块闸一起关掉**。处理：把这两个码改挂 `inventory`（或新模块），并在模块自检里同步。

### 连带清理

- 权限码 `acct.entry.*`、`acct.coa.*`、`acct.settings.*`、`acct.ledger.export` 全部消失；`ROLE_PERMISSIONS` 里 accountant/clerk 的相应授予同步删除。
- 后台的会计设置入口（`admin_*` 中与会计相关项）随之移除。
- 不迁移的会计文件：`services/accounting`（29）、`services/ledger`（8）、`routes/accounting_*`（3 个文件 36 条路由）、凭证导出 `services/export/entries.py`。
