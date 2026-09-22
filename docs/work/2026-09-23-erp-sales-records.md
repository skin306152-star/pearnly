# 工作包：ERP 销售记录（沿用采购交互，替换销售字段与契约）

> 接续上一窗口的 [采购记录实施与验收记录](2026-09-22-erp-purchase-records.md) 末尾“销售记录接续交接（2026-09-23）”。
> 采购定稿 = `a4b95398cc332b46f8b2595dc06da81e15be3a63`，交接文档 = `bbd51faaf4ae4d8d89bde3f5eea5911351da180a`。
> 本文件是销售任务唯一工作记录；采购原有的实施与验收记录继续由采购任务保有，不改写其结论。

## 目标和范围

- 目标 / 完成条件：销售记录按**采购已定稿的前端交互**实现——原生 page-level 列表 + 独立路由的连续录入/详情页 + 同页复核 + 旧项目票图查看器 + 桌面 60/40 与手机滚动定位布局；业务字段与服务端契约换成**销售自己的**，以旧项目当前线上销售域为基准。
- 本次不做：不重新设计录入方式、电脑/手机布局、文件查看器或列表；不复制采购的付款/凭证/入库事务；不增加草稿步骤、审批流、AI 提示、分支管理、Google Drive / Sheets 同步；不新增产品没有的业务模块与入口。
- 本次范围状态：进行中（接手完成，尚未写销售代码）
- 收尾状态：施工中，未收尾
- 分支 / 基线 SHA / 工作树 / 集成目标：分支 `codex/erp-sales-records`，工作树 `/Users/skin/.codex/worktrees/erp-sales-records/pearnly`，基线 `bbd51faaf4ae4d8d89bde3f5eea5911351da180a`（含本机 main `717d202`、盘点 `8a67e7b`、共享 ADR-009 `db4c7dc`）。集成目标为本机 `main`。
- 合并 / 推送 / 远端回读状态：未合并、未推送；远端无 `codex/erp-sales-records`。
- 负责人 / 关联任务或 PR：本窗口（销售记录包）。上游交接见采购记录文件末尾章节。
- 允许修改的路径 / 数据和运行资源归属：`apps/pearnly_erp_portal/pearnly_erp_portal/sales/`、该 App 的 `public/desk/sales*.js|css`、`doctype/erp_sales_*`、`i18n/messages.tsv` 与生成 CSV、`features.json` / `hooks.py` 的销售声明、本任务新增的 `scripts/`、`tests/` 与 `docs/work/` 记录；`pearnly-purchase-lab` 六个容器与 `sites`/`db` 卷已由采购任务交接给本任务继续演示。
- 必要外部操作及当前授权：无。不部署、不改旧生产、不推送。

## 接手核对（本次实际执行）

- 采购树干净、无未提交：`git -C .../erp-purchase-records/pearnly status --short --branch` → `## codex/erp-purchase-records`（无改动），tip `bbd51fa`。
- 按交接步骤创建销售工作树：`git worktree add -b codex/erp-sales-records /Users/skin/.codex/worktrees/erp-sales-records/pearnly codex/erp-purchase-records`。
- 8147 演示环境交接：`docker --context colima-pearnly compose -p pearnly-purchase-lab -f infrastructure/compose/erp-pos-lab.yml -f infrastructure/compose/erp-purchase-preview.yml up -d --no-deps --force-recreate bench web socketio frontend`。
  回读四个 App 容器的 App / scripts / tests 挂载已全部指向销售树；`pearnly-purchase-lab_sites`（sites）与 db/redis 容器未重建、未换卷；镜像仍是共享预览 `sha256:90c1d545d215d6081965740013dd8af84337ca23c5fde5c36acdf162cd4655c3`。
- 交接时采购树仍被挂载，切换后采购演示内容不变（销售分支由采购 tip 创建，内容同源）；采购树保留，可原样回退挂载。

## 迁移映射

- 首次线上核验（本窗口，2026-09-23，曼谷时区，只读）：GCP `pearnly` / `asia-southeast1`，`pearnly-web` 与 `pearnly-worker` 均为 `*-ec2e5c957e6b-s3`、各 100% 流量。
- 完整源码 SHA / 映射依据：`ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`（与采购开工/交付时相同，revision 短 SHA 未变，说明旧线上自采购交付后没有新发布）。只读快照位置 `/Users/skin/Developer/Pearnly/pearnly-app`（`git show <sha>:<path>`，本地 HEAD `9aa7a6b2` 不是生产 SHA）。
- 相关运行配置：同上，未发现影响销售域的开关变化；未读取 Secret 明文、未调用业务接口。
- 交付前线上复查：待施工完成后按 [迁移基准](../development/migration-baseline.md) 重做，并比较与本次的增量。

采购对照表（P01–P22）是采购口径，**不能直接套到销售**。销售的字段与服务端契约核对是 S1 工作包，落点与边界在下表登记（S1 完成后补齐）。

| 来源仓库 / SHA / 文件 | 新落点 | 保持的契约 | 有意变化及依据 | 验证场景 |
| --- | --- | --- | --- | --- |
| 待 S1 逐文件核对后填写 | `pearnly_erp_portal/sales/`、`public/desk/sales*.js`、`doctype/erp_sales_*` | 待填 | 一律以“用采购交互、销售业务”为准 | 待填 |

## 现状盘点（只读，尚未改动）

新平台销售侧已有：

- `sales/service.py`（建单/改单/开出/作废/红冲补开/上传识别/报价转开票/票面模型）、`sales/api.py`、`sales/contracts.py`（金额与泰国税票口径、连号）、`sales/ocr.py`、`sales/render.py`。
- DocType `ERP Sales Document` / `ERP Sales Document Line`（字段已覆盖 `doc_kind` 七种、`party_type` 四种、税号/分支/地址、VAT/WHT/折扣/合计/净应收、`payment_status`、`references_document`、`source_file`、`stock_posted_at`）；主数据 `ERP Customer`、`masters/customers.py`；POS 侧 `ERP Sale` / `ERP Sale Line` / `ERP Sale Payment`。
- 菜单与功能目录已声明销售、客户与收银（`hooks.py` 的 `pearnly_navigation`、`features.json` 的 `sales` / `clients`）。

尚未具备（对照采购已交付的同等结构）：

- 前端：`public/desk/sales.js` 仍是旧形态——列表 + 大弹窗编辑、先存草稿再开单；没有连续页路由、原生连续表面板、附件区/票图查看器、手机滚动定位导航、原生选择与批量动作、筛选 Dialog、导出菜单。
- 服务端：没有采购那种 `entry.py`（一次提交即过账）、`records.py`（统一查询/汇总/导出共享条件）、`access.py`、`files.py`、`preview.py`（鉴权分页渲染 PDF）、`output.py`（替代收据/WHT 凭证）、`amounts.py`；也没有销售收款流水 DocType（采购有 `ERP Purchase Payment`）。
- 状态机差异：现有销售是 `draft → pending_approval → issued → void` 的两步开单；目标交互是一次录入、复核、确认。历史单据兼容保留，服务端需协调为目标状态机，不再给用户增加草稿/审批步骤。

旧项目销售域规模（`ec2e5c957e6b`，只读）：前端 25 个文件（`src/home/sales-records.ts`、`sales-record-detail.ts`、`sales-common.ts`、`sales-detail.ts`、`sales-wizard*.ts`、`sales-workbench.ts`、`sales-report*.ts`、`sales-settings.ts` 等），后端 35 个文件（`services/sales/*.py` 含 `document*.py`、`issue_gates.py`、`totals.py`、`wht.py`、`promptpay.py`、`numbering.py`、`pdf*.py`、`credit_note.py`、`quotation.py`、`product_import.py`；另有 `services/sales_agg/`、`alembic/versions/0006…0025/0085/0107` 及 `docs/sales-module/`）。**只照当前 `sales.js` 的少量字段做会漏**，S1 必须按域逐项核对；但交互与布局不再从旧向导取，只取字段与业务契约。

## 工作包划分

| 包 | 内容 | 完成判据 |
| --- | --- | --- |
| S1 | 销售字段/契约基准：按生产 SHA 逐文件核对开票域（单据类型、买方类型与证件、分支、折扣、价内价外、VAT/WHT、收款与 PromptPay、连号、红冲/补开、报价转换、商品导入与图片），登记对照表 | 对照表可指向来源路径与新落点；缺项与有意差异写清 |
| S2 | 服务端：一次提交即开票的 entry、records 统一查询/汇总/导出、access、files（附件归属与私有权限）、preview（鉴权只读分页）、output（替代收据/WHT 凭证）、收款流水与状态回读 | 真实 Site 事务、失败回滚、幂等、连号不跳、权限与租户隔离通过 |
| S3 | 前端：`sales.js` 原生列表（搜索/筛选/新建/选择/导出）、`sales-entry.js`（默认上传识别卡片、字段显隐、整批一次识别、手动零识别）、`sales-fields.js`（原生 Section/FieldGroup 连续字段）、`sales-form.js`（同页复核与提交）、`sales-layout.js`（60/40、手机滚动定位）、`sales-viewer.js`（旧查看器交互）、`sales.css` | 浏览器覆盖列表/选择/录入/复核/查看器/390px，且不破坏采购交互 |
| S4 | 词条与翻译：ERP `i18n/messages.tsv` → 生成中/泰/英/日 CSV | `i18n-build.py --check` 与词条检查通过，不新增 bare CJK 存量 |
| S5 | 验证：架构检查、JS 语法、定向业务探针、浏览器验收、真实 Site 回读 | 分层报告，缺哪层写缺哪层 |
| S6 | 收尾：合并/推送/部署按授权，资源清理与保留登记 | 本任务独有资源逐项核对 |

## 已知限制与风险

### 范围确认：不引入「开票系统」（2026-09-23 用户指示）

用户本轮明确：开票系统此前已说过不要，如果被迁进来就直接删。**本次只读核查结论：没有迁入，无需删除。**

| 核查项 | 结果 |
| --- | --- |
| 决策记录 | [前端页面清单](../frontend/01-page-inventory.md) 已把「发票工作台（`sales-invoices`）」「账套 / 开票资料（`sales-account`）」列为 **不要**；[ERP/POS 迁移记录](2026-09-21-erp-pos-migration.md) 的「不迁」表同样登记「销售开票组：发票工作台、账套·开票资料」，并写明开票资料已由「公司资料」承担 |
| 菜单与路由 | `pearnly_erp_portal/hooks.py` 的 `pearnly_navigation` 只有首页/商品/库存/入库/出库/收发存/盘点/采购/供应商/采购设置/销售记录/收银/客户/主数据等；没有发票工作台或开票资料入口 |
| 页面 | 全仓 Desk 页面目录（core / dms / erp / cowork）没有发票工作台、账套或开票资料页面，也没有对应路由 |
| DocType | 没有发票工作台、开票资料、发票模板或销售设置类 DocType（`find apps -path "*doctype*" | grep -i invoice` 为空） |
| 品牌资产 / 模板 | 旧 `sales-account.ts` 的卖方主体、logo/印章/签字、票面模板**未迁**；`ERP Company` 只有 `company_name / short_name / tax_id / branch / phone / contact_person / contact_email / promptpay_id / doc_prefix / fiscal_year_start_month / address / note` |
| 其他 App | Cowork / DMS / 控制面没有开票功能；`pearnly_control/billing.py` 是 OCR 按页计费底座，与开票无关 |

仍然存在、但属于「销售记录」自身输出、不是被否掉的开票系统：`sales/render.py` 的票面模型（标签、泰文金额大写、未收款时的 PromptPay 载荷）与 `api_sale_document_print`，以及 `contracts` 里的报价转换、红冲/补开、WHT 预设。这些是否保留由用户在 S1 范围确认时拍板；本记录不把它们当成「开票系统」自动删除，也不因为本次核查而扩展开票侧能力。

**用户随后明确「开税务发票这件事也不要」（2026-09-23），已按下面 D1 删除。** 本节原来的「待拍板」到此结束。

### D1：去掉税务发票能力（2026-09-23 已执行，未部署）

范围判据：只要还在服务**开税务发票**的东西就去掉；剩下的是「记录一次销售」。

| 去掉 | 具体落点 |
| --- | --- |
| 三种税票类型与两种冲正单类型 | `sales/contracts.py` 的 `DOC_KINDS`（只剩 `quotation` / `receipt`）；DocType `doc_kind` 选项同步 |
| 税票号（正式连号） | `sales/service.py` 的 `allocate_number`、`doc_no` 字段与"已开出必须带号"校验；`contracts` 的 `RESET_*` / `DEFAULT_PREFIXES` / `resolve_prefix` / `period_key` / `format_number` / `fiscal_year_start` |
| 票面与打印 | `sales/render.py`（PromptPay 载荷、泰文标签、泰文金额大写、打印模型）整文件删除；`contracts.baht_text`；`api_sale_document_print` / `print_document` |
| 预扣税 WHT | `contracts.WHT_PRESETS` / `wht_label` / `compute_totals` 的 WHT 与 `net_payable`；`wht_rate` / `wht_amount` / `net_payable` 字段 |
| 红冲 / 补开 | `contracts` 的 `NOTE_TYPES` / `note_gate` / `note_prefix`；`service.create_note`；`api_credit_note` / `api_debit_note` |
| 报价转开票 | `contracts.CONVERT_TARGETS` / `convert_gate`；`service.convert_quotation`；`api_convert_quotation` |
| 买方的税票要素 | `tax_id` / `branch_type` / `branch_no` 字段（只为开税票而存在）；`masters.contracts.missing_for_tax_invoice`、`masters.customers.required_for_invoice`、`api_customer_invoice_requirements` |
| 开票语义的状态与动作 | 状态收敛为 `draft / submitted / void`；动作表只留 `edit / confirm / void`；`issued`、`pending_approval` 及审批动作、`trade.issue`、`trade.taxNo` 词条去掉 |

保留（就是「销售记录」本身）：建单（我方单号 `system_no`）、确认（确认即过账出库，幂等）、作废（反向流水）、上传识别与同页复核、报价单（不作为销售凭证、不动货）、金额与 VAT 规则、来源三种、岗位与租户隔离。旧的 `issue` 流程改名为 `confirm`（`api_confirm_sale_document`）；前端 `desk/sales.js` 同步到新契约（仍是有待按采购交互重做的现状页）。

**没有动**：`ERP Customer` 主档（税号/分店是客户主数据，也给收银用）、采购侧的 `doc_type`（进项票种仍要有，`tax_invoice` 词条因此保留）、POS 收银域、商品/库存/盘点已验收页面。

**遗留**：Frappe 的原生 migrate 只同步元数据，不会删掉旧列；实验室两个站点的 `ERP Sales Document` 表里 `doc_no / tax_id / branch_type / branch_no / wht_rate / wht_amount / net_payable / references_document` 列仍在，但已不在 DocType 里，应用不再读写。按本仓规矩没有手工改库；销售记录本来就没有数据（两站都是 0 条），不存在迁移旧值的问题。

### S2/S3：销售前端与一次提交（2026-09-23 已执行，未部署）

交互按采购定稿那一套落地，业务字段与契约换成销售自己的。服务端模块（`pearnly_erp_portal/sales/`）：

| 模块 | 职责 |
| --- | --- |
| `access.py` | 单据读写权限与本人范围；并在 `hooks.py` 注册 `ERP Sales Document` 的 has_permission / permission_query_conditions |
| `records.py` | 列表、单据、导出共用的一份条件（搜索、类型、收款状态、来源、期间、日期口径） |
| `entry.py` | **一次提交即落定**：建单 + 出库 + 收款标记同一事务；请求号幂等（重复提交返回同一张单、只出一次库）；失败整体回滚；另有 `preview`（前端算金额）、`cancel`、`payment`、`delete_drafts` |
| `files.py` / `preview.py` / `recognition.py` / `output.py` | 私有附件与归属校验、鉴权后的 PDF 分页渲染、显式触发的整批识别、同查询的 Excel 导出（3 张表） |

原来的 `sales/service.py` 与 `sales/ocr.py` 已删除（写入路径只剩 `entry`，读取只剩 `records`），避免两套写入口。`integrations/intake.py` 的销项分支改为走 `entry.post(..., finalize=...)`，与采购同一条路（LINE 收进来的单也一次落定并出库）。

前端（`public/desk/sales*.js|css`）：`sales.js`（列表 + 过滤 + 导出 + 选择删草稿 + 收款动作）、`sales-fields.js`（原生 Section/FieldGroup 业务字段与逐项明细）、`sales-entry.js`（默认上传识别卡片、互斥、上传完成才整批识别一次）、`sales-layout.js`（电脑 60/40、手机滚动定位导航）、`sales-form.js`（同页复核、金额、提交、合并/分拆、收款弹窗）、`sales-viewer.js`（旧查看器的缩放/拖动/旋转/复位/翻页/全屏）、`sales-intake.js`（LINE 待处理）。页面登记从 core 移到 ERP App（`page/pearnly_sales/`，与采购同一处），`browser_text.py` 放行 `sales.` 命名空间；`features.json` 的销售功能加 `ERP Sales Attachment`。

落定时明确的产品判断（逐条可追）：

- **收款按旧线上表头口径**：钱记在单据上（`paid_amount` + 状态 + 方式 + 日期），状态由「收了多少 / 该收多少」算；不另开流水表。收款方式取旧线上常用的现金/转账/PromptPay/其他。
- **收据不再要求先收款**：去掉税票之后，收据同时承担现金卖货与赊账两种情形；收到钱用「收款」动作记上去。฿0 的收据仍然拦（`zero_amount`）。
- **不自动建商品**：旧线上销项明细的商品关联是可空的（"现填时可为空"），卖的东西没建档时明细照样能记，只是这一行不动库存；建档归物料岗位。明细按物料/名称匹配现有主数据，匹配不上就留空。
- **不做「修订」**：旧线上的更正关联是税票侧的口径（已随开票一起删除）；销售的可走路径是作废后重录，因此菜单里只有作废。
- **导出只做 Excel**（与采购同一查询条件），不引入 Drive/Sheets。

- **销售探针已改成非破坏性**：原 `scripts/erp-pos-lab-sales-verify.py` 会遍历删除 Site 内全部 `ERP Sales Document`（`frappe.delete_doc(..., force=True)`）。现已重写为「样本只建在隔离检查站 `erp-pos-b.localhost`、演示站 `erp-pos-a.localhost` 只读回读、不删除任何记录」，并在断言里包含税票/冲正单类型一律被拒。
- 当前 8147 没有 OCR 凭据：只能验证“未配置时原生提示拒绝”和带明确标记的响应替身交互；真实付费识别、真实 LINE 发送、生产环境、手机真机触摸都不能据此声称通过。
- 采购的“全部为草稿才可删除”是采购规则，不直接套销售；批量动作必须按销售自身状态与角色判断。
- 销售出库方向与采购入库相反，收款方向与付款相反，**不得通过改 API 前缀复用采购事务**。
- 采购尚无用户最终验收声明，本任务不代签，也不得破坏其可访问样本（`PUR-2026-00202` 等）。

## 变更与决定

- 本次仅接手：创建销售工作树、切换 8147 源码挂载、重做线上只读核验、建立本记录。无代码变更。
- 复用共享层（Core 原生列表/路由/布局、共享 OCR、登录、外壳）不重写；销售自有声明仍写在 ERP App。

### F1：删草稿的授权口径与两处英文残留（2026-09-23，未部署）

**问题**：列表选中草稿点「删除 (1)」→ 弹「没有权限 / 角色权限管理中未授权用户 purchase-preview@example.test 访问 ERP Sales Document」。

**根因（只读证据）**：失败点不是销售自己的闸，而是框架 `frappe.model.delete_doc.check_permission_and_not_submitted` → `doc.check_permission("delete")`。实验室站点回读：

| 事实 | 证据 |
| --- | --- |
| 功能角色在业务表上一律没有 `delete` | `Custom DocPerm`：`ERP Sales`(read/write/create=1, delete=0)、`ERP Purchase`(同上)；全站 `delete=1` 的行只有 `System Manager` 与 Frappe 自带的 `Contact/All` |
| 这不是销售特有 | `frappe.has_permission("Purchase Document","delete", user=…)` 与 `ERP Sales Document` **同为 False** —— 采购页的「删草稿」在租户角色下同样是坏的（本次未改采购，见下） |
| 平台为什么这么给 | `pearnly_core/provisioning.py` 的 `FEATURE_PERMS`：功能角色拿读写建，**不给删除**，理由是"业务单据只能作废不能抹掉"。这是跨产品口径，本次不动 |
| 但旧线上录入员**是能删草稿的** | 生产 SHA `ec2e5c957e6b` 的 `docs/permissions/02-roles-permissions.md`：`*.create / edit / delete（草稿层）` 给 owner/admin/accountant/clerk；销售路由 `routes/sales_routes.py` 的 `api_delete_draft` 用权限码 `sales.doc.delete` 放行，采购 `routes/purchase_routes.py` 用 `purchase.doc.delete` |

**做法**：框架那一位继续关着（通用/桌面端删除仍然拒绝，共享策略没动），放行的是**产品自己的动作** —— `sales/entry.py::delete_drafts` 在过完三道闸后以 `ignore_permissions` 删除。三道闸：`access.get_document(..., write=True, lock=True)`（`sales.write` 岗位 + 本人范围 + 行锁）、整批核验 `status == draft`、已落定/已作废一律拒绝。这与本 App 既有的 `sales/files.py::remove` 用同一写法（先自己判权限，再 `ignore_permissions` 删 File）。

**两处英文残留**（用户看到的「System number / Add VAT / Customer」）：实际核对后词条键都是好的（`trade.systemNo` / `trade.hasVat` 渲染为「单据编号 / 计算增值税」），真正漏的是**没走词条的英文原文**：

| 位置 | 原因 | 处理 |
| --- | --- | --- |
| 客户分组标题 | `u.section("Customer")` 用的是英文原文，而术语表里 `Customer` 那行填了 key（msgid 变成 `trade.buyer`），英文原文没有 msgid | 改成 `u.section("trade.buyer")`（与采购的 `u.section("Supplier")` 同一写法） |
| 税费区 `discount_total` | 标签 `__("Discount Total")` 在术语表里没有这一条 | 术语表新增 `Discount Total / 折扣总额 / ยอดส่วนลดรวม / 割引合計`，重生成四个语言的翻译包 |

**明确没做**：不清理草稿自带私有附件（采购的删除也不清；本 App 的 `intake.discard` 有意保留留底）。要清的话是另一个决定。

**留给采购任务的发现**：`ERP Purchase` 角色在 `Purchase Document` 上同样没有 `delete`，采购页的「删草稿」会撞同一个错。采购工作树与结论归采购任务，本次只记录证据、不改它。

### F2：LINE 待处理入口核对，以及旧 LINE 探针的三处过期（2026-09-23，未部署）

销售页的「LINE」入口只在**存在销售方向待处理草稿**时才出现（`api_sales_defaults` 的 `capabilities.line_intakes`）。本次用真实接口在演示站造了一条销售方向待处理草稿来验页面，验完已删除（见收尾）。

| 核对项 | 结果 |
| --- | --- |
| 列表页入口 | 有待处理草稿时出现「LINE」按钮；验完删掉后按钮消失（口径随数据变化，符合设计） |
| `/desk/pearnly-sales/intakes` | 原生列表，列为 编号 / 创建日期 / 用户 / 备注，中文，无英文残留 |
| 打开一条待处理草稿 | 跳到 `/desk/pearnly-sales/intake/LI-00212`，走「新建销售单」录入页并带 `source=line`、附件挂进查看器与附件清单 |
| 行内「取消」（丢掉草稿） | 原生弹窗：标题「取消」、字段「取消原因」、按钮「确认」；提交后回读 `status=discarded` 且留了原因；页面回到列表 |

**旧探针 `scripts/erp-pos-lab-intake-verify.py` 的三处过期**（本次只修第一处；后两处不是销售域的，报告不改）：

1. **D1 遗留（已修）**：销售方向的确认传的是 `doc_kind="tax_invoice"`，而税票类型已按用户 2026-09-23 的决定删除，所以必然 `ValidationError: Invalid value`。改成 `receipt` 后，隔离站同一条待处理草稿确认成功（`SD-00079` 挂到 `LI-00078`、`source=line`、指纹一致）。这与采购侧那轮"去掉开票前检查"的探针同步是同一类收尾。
2. **采购方向已先过期**：采购确认现在要求「供应商发票号」（`purchasing/entry.py` 的 `Supplier Invoice No is required`），而这个 2026-09-21 的探针没带 `doc_no`，所以采购方向的确认也过不去。属采购任务的口径变化，未改本探针的采购分支。
3. **权限范围拦住了员工**（不是本次改动引入，证据在 `28f13cf`）：`integrations/intake.py` 的 `_get` / `has_permission` / `permission_query` 按 `doc.owner` 判范围，而 LINE 收单是**通道身份（Administrator）**落库的，`sent_by` 才是那位员工。结果是员工角色既看不到、也确认不了自己发进来的 LINE 草稿 —— 销售页的 LINE 入口因此只对 `ERP Admin` 出现。这是**共享的 intake 切片**（采购/销售两向同源），归 LINE/采购任务；本次只记录，未改。
4. 另记：该探针的默认站点是演示站 `erp-pos-a.localhost`，一跑就往演示站写草稿；销售探针的口径是"样本只落隔离站"。本次误跑一次已把产生的草稿与文件全部清回原样（见收尾）。

### F3：收款/付款弹窗点不动、以及「已收付不可改」（2026-09-23，未部署）

用户报「点收款付款的已收/已付提交不了」「那个按钮是不是只是展示」，并要求：未付/未收标红、已付/已收不可更改。查下来是**两个真原因**，都复现到了。

**原因一：下拉浮层盖住了「提交」按钮（框架自己的 z-index 反差）。**

框架样式里 `.awesomplete ul { z-index: 2 }`、`.modal-footer { position: sticky; z-index: 1 }` —— 也就是下拉**故意**画在 footer 之上。字段少的弹窗（收款/付款正是四个字段）里，最后一行选项正好压在「提交」上。用 `document.elementFromPoint()` 取「提交」按钮中心那个点，拿到的是**下拉里的 `<li>`，不是按钮**：

| 视口 | 「提交」中心那个点实际命中的元素 | 按钮可点？ |
| --- | --- | --- |
| 1440×900 | `LI`（未付） | 否 |
| 1100×900 | `LI`（未付） | 否 |
| 768×900 | `LI`（未付） | 否 |
| 600×900 | `LI`（未付） | 否 |
| 390×844 | `LI`（未付） | 否 |

所以用户点「提交」其实点在选项上：钱没提交，选项被改了一下 —— 看起来就是"按钮没用"。这解释了为什么我先前用键盘选完选项再点提交能过（那时下拉已经关掉了），而用户鼠标点完字段直接点提交就过不去。

**原因二：弹窗里那个状态下拉的默认值会把已经收到的钱静默清零。**

收款/付款弹窗原先有「收款状态」下拉（收款 / 未付 / 已付款），默认值是 `余额 > 0 ? 收款 : 未付`。一张**已经收满**的单子打开弹窗，默认落在「未付」且金额字段被藏起来 —— 直接点提交等于把收款清零，页面上没有任何确认。演示站实测：`SD-00204`（已收 214）打开弹窗后提交，变成「未付」，行内按钮翻成「已付款」（已按原值改回）。

**做法**（销售与采购同一套，用户 2026-09-23 口径）：

| 改的东西 | 落点 |
| --- | --- |
| 弹窗只问三件事：金额（必填，销售默认=总计、采购默认=还欠）、方式、日期（+采购备注）。**去掉状态下拉**，界面上再没有"把已收到的钱清零"这条路 | `sales-form.js` / `purchase-form.js` 的 `u.payment` |
| 行内动作变成真正的动作按钮：还欠钱时给「收款 / 付款」，**已付已收一行不给按钮** | `sales.js` / `purchase.js` 的 `row_actions` |
| 详情页主按钮同样只在还欠钱时出现（已收满的收据页不再有「收款」） | `sales-form.js` / `purchase-form.js` 的 submitted 分支 |
| 未付/未收（含部分）在列表里标红，色值用框架 `--red-600`，与差异看板同一个红 | 列的 `cell_class` + `product.css` 的 `.pearnly-payment-due` |
| footer 抬到浮层之上：`p.dialog` 给弹窗挂 `pearnly-dialog` 类，皮肤里 `.modal-footer { z-index: 3 }`。**共享层改动**（core 的 `product.js` / `product.css`，一行类名 + 两条规则），修的是框架这个通用反差，销售/采购/筛选弹窗都受益 | `pearnly_core/pearnly_core/public/desk/product.js`、`product.css` |

**取舍**：footer 抬上去之后，特别矮的弹窗里浮层的最后几项可能被 footer 压住（可以打字过滤后再回车选中）。实测 390×844 的采购付款弹窗里六个付款方式全部露在 footer 之上，没有实际遮挡。

**没有做**：没给"撤销收款/撤销付款"留界面入口（用户明确「已付已收不可更改」）。服务端的 `payment(..., paid=0)` 仍保留，供作废/冲正与探针使用；界面上要改已收付金额现在是走不通的。

## 验证

| 时间 | 代码 SHA / 未提交差异 | 环境与数据归属 | 命令或场景 | 预期 / 实际 / 退出码 | 结论和证据 |
| --- | --- | --- | --- | --- | --- |
| 2026-09-23 | `bbd51fa`（销售分支基线，无代码改动） | 本地 Git | `git worktree add -b codex/erp-sales-records … codex/erp-purchase-records` | 工作树创建成功 / 分支指向 `bbd51fa` | 交接步骤 1 |
| 2026-09-23 | 同上 | `colima-pearnly` / project `pearnly-purchase-lab` | `compose config --quiet` 后 `up -d --no-deps --force-recreate bench web socketio frontend` | 四个容器重建成功，`config` 退出码 0 | 挂载已切到销售树，sites/db/redis 未动 |
| 2026-09-23 | 同上 | GCP `pearnly` / `asia-southeast1` | `gcloud run services describe pearnly-web\|pearnly-worker` | 两个组件仍 100% 流量在 `*-ec2e5c957e6b-s3` | 线上身份未变，销售基准仍是 `ec2e5c957e6b` |
| 2026-09-23 | D1 改动（未提交时） | 宿主 Python，无 Site | `python3 tests/test_erp_portal_access.py` | 101 条 / 95 通过 / 6 跳过（openpyxl 只在 bench 装）/ 退出码 0 | 销售契约、金额、闸、动作、下游、识别映射 |
| 2026-09-23 | D1 改动（未提交时） | 宿主 | `python3 scripts/check-architecture.py` | `PASS (0 violations)` / 0 | App 边界与命名空间未破 |
| 2026-09-23 | D1 改动（未提交时） | 宿主 | `python3 scripts/i18n-build.py` 与 `--check` | `RESULT: PASS`（erp 587 条）/ 0 | 术语表与生成包一致；生成器按设计保留 CSV 里已有行，所以文件无变化 |
| 2026-09-23 | D1 改动（未提交时） | 宿主 | `python3 scripts/check-i18n.py --strict` | `FAIL`：bare CJK 18 处、全在 `inventory/workbook.py`（与基线相同，销售文件不在列） | 本次未新增 bare CJK，未改忽略规则 |
| 2026-09-23 | D1 改动（未提交时） | 宿主机 Node | `node --check …/desk/sales.js` | 退出码 0 | Desk 脚本语法 |
| 2026-09-23 | D1 改动（未提交时） | `pearnly-purchase-lab`，A + B | `bench --site erp-pos-{a,b}.localhost migrate` | 退出码 0；回读 `doc_kind` 选项 = `quotation / receipt`、`status` = `draft / submitted / void` | 结构变更走原生迁移；旧列残留见上 |
| 2026-09-23 | D1 改动（未提交时） | `pearnly-purchase-lab` / `erp-pos-b.localhost` 隔离检查站 | `./env/bin/python /workspace/scripts/erp-pos-lab-sales-verify.py`（`PEARNLY_ERP_POS_LAB=1`） | `problems: {}` / 0 | 真实 Site 事务：建单占我方单号、闸拦住 ฿0 与未收款、确认出库一次且重复确认被拒、作废回冲回到 100、报价不动货、五种税票/冲正类型全被拒、上传识别落 `source=ocr` 且留底与哈希齐、复核回填 214.00、收银员不能建单、销售可读 |
| 2026-09-23 | 同上 | 同上 | `scripts/erp-pos-lab-masters-verify.py`（`PEARNLY_ERP_POS_LAB_SITE=erp-pos-b.localhost`） | `problems: {}` | 去掉开票前检查后客户主档检查仍全通过 |
| 2026-09-23 | 同上 | 同上 | `scripts/erp-data-flow-lab-verify.py` | `result: PASS`（含 "sales confirm and void"）/ 0 | 采购→销售→库存→收银的联动未被破坏 |
| 2026-09-23 | 同上 | `pearnly-purchase-lab` web | 登录后 `GET /desk/pearnly-sales`、`api_list_sales`、`api_sale_document_total` | 200 / `{"message":[]}` / `{"message":0}` | 页面与接口可用 |
| 2026-09-23 | 同上 | 内嵌浏览器（本窗口自己的标签，8147） | 打开销售页 → 新建销售单对话 | 列表渲染「无数据」、对话字段为 客户名称/日期/单据类型（默认收据）/客户类型/地址/计算增值税/增值税率/明细/付款状态/付款方式/付款日期/备注，**没有税号与分店字段**；未提交 | 浏览器层（桌面视口）通过；未做真机与移动宽度 |
| 2026-09-23 | S2/S3 改动（未提交时） | 宿主 | `python3 tests/test_erp_portal_access.py` / `check-architecture.py` / `i18n-build.py --check` / `node --check sales*.js` | 102 条（96 通过 / 6 跳过）、0 违规、`RESULT: PASS`（erp 618 条）、JS OK | 契约、边界、术语表、前端语法；上述 D1 行里的确认/复核接口已被本次一次提交（`api_post_sale`）取代，行内容按当时提交读 |
| 2026-09-23 | S2/S3 改动（未提交时） | `pearnly-purchase-lab` / `erp-pos-b.localhost` 隔离检查站 | `scripts/erp-pos-lab-sales-verify.py` | `problems: {}` | 一次提交（单据 + 出库 + 收款）与请求号重放、金额闸、五种税票/冲正类型被拒、报价不动货、部分/全额/撤销收款、列表搜索与过滤、私有附件与 PDF 分页、Excel 导出、岗位与本人范围、作废回冲、识别归一化 |
| 2026-09-23 | 同上 | 同上 | `scripts/erp-pos-lab-masters-verify.py`、`erp-data-flow-lab-verify.py` | 均无 problems / `result: PASS` | 去掉开票前检查后客户主档仍全通过；采购→销售→库存→收银联动未破 |
| 2026-09-23 | 同上 | 内嵌浏览器 / 演示站 8147 | 列表（8 列 + 搜索 + 过滤 + 导出菜单 + 选择删草稿 + 收款行内动作）→ 新建（上传识别/手动录入卡片、默认隐藏字段）→ 手动录入 → 提交 | 记录 `SD-00204`：`submitted`、`system_no SI2569-000001`、`summary` 有值、明细 2 盒 × 100、含税 214、已收款 214/现金、`stock_posted_at` 有值、库存 3→1；列表与详情按新交互显示 | 浏览器层通过（默认 919px 桌面视口） |
| 2026-09-23 | 同上 | 同上 | 详情页真实上传私有 PNG → 查看器与附件清单；收款弹窗改「已付款」并提交；过滤「未收款」→ 0 条、重置 → 1 条；390×844 视口 | 附件为私有且挂在 `SD-00204`；预览画布可见并加载图片（600px 宽）；收款回到「已付款 / THB 214 / 现金」；过滤与重置符合预期；手机单列、滚动定位导航（附件/详细信息/物料）高亮跟随、无横向溢出 | 浏览器层通过；上传与查看器走真实私有文件；真机未测 |
| 2026-09-23 | 同上 | 内嵌浏览器 / 采购页 | 打开 `/desk/pearnly-purchase` | 标题「采购记录」、20 / 73 行、按钮齐全、无报错 | 未破坏采购交互（本次只动 core 的命名空间白名单与销售页登记位置） |
| 2026-09-23 | F1 改动（未提交时） | 宿主 | `tests/test_erp_portal_access.py` / `check-architecture.py` / `i18n-build.py --check` / `node --check sales-fields.js` | 102 条（96 通过 / 6 跳过）、`PASS (0 violations)`、`RESULT: PASS`（erp 619 条）、JS OK | 契约与边界未被本次改动破坏；术语表 +1 条已同步生成包 |
| 2026-09-23 | 同上 | 宿主 | `python3 scripts/check-i18n.py --strict` | `FAIL`：bare CJK 18 处，全在 `inventory/workbook.py`（另有 95 处内部日志用中文，与基线同类） | 与基线一致；本次只在 `sales-fields.js` 去掉一个英文字面量，没有新增 bare CJK，未改忽略规则 |
| 2026-09-23 | 同上 | `pearnly-purchase-lab` / `erp-pos-b.localhost` 隔离检查站 | `./env/bin/python /workspace/scripts/erp-pos-lab-sales-verify.py`（`PEARNLY_ERP_POS_LAB=1`） | `problems: {}` / 0 | 新增断言全过：草稿本人可删、删除后记录不存在；员工（无销售岗位）与其他岗位删不动；已作废与已落定的删除被拒；`frappe.has_permission(...,"delete")` 对销售角色仍为 False（框架那一位没放开） |
| 2026-09-23 | 同上 | 内嵌浏览器 / 演示站 8147 | 列表选中合成草稿 `SD-00206` → 主按钮「删除 (1)」→ 确认「是」 | 不再报权限错；行消失，列表只剩 `SI2569-000001`（`SD-00204`） | 服务端回读：`ERP Sales Document` 只剩 `SD-00204`；无权限弹窗；`SD-00206` 是上一窗口为这次检查建的合成草稿 |
| 2026-09-23 | 同上 | 内嵌浏览器 / 演示站 8147 | 新建页（手动录入）与 `SD-00204` 详情页逐项扫「System number / Add VAT / Customer / Discount Total / Attachment Gallery」 | 两页**都没有**上述英文原文；分组标题为「客户名称」、税费区为「折扣总额」，列表 8 列与其他标签全中文 | 界面文案已收干净；`Attachment Gallery` 是字段类型标签，页面上不渲染 |
| 2026-09-23 | 同上 | 内嵌浏览器 / 演示站 8147 | 列表行内动作「未付」→ 收款弹窗 | 弹窗原生「收款」，字段 收款状态/已收金额/收款方式/收款日期 全中文；因应收 214，默认值为 收款 / 214.00 / 现金 / 当天 | 行内动作按状态给标签（已付款的行显示「未付」，反之显示「已付款」） |
| 2026-09-23 | 同上 | 同上 | 同一弹窗上误按回车触发提交 | 记录变成「未付」、行内动作翻成「已付款」 | 顺带证明弹窗提交这条路真的会写库；随后用 `entry.payment('SD-00204', amount=214, method='cash', date='2026-09-22')` 恢复，回读 `paid_amount 214 / payment_status paid / payment_method cash / payment_date 2026-09-22 / status submitted`，与原样一致 |
| 2026-09-23 | 同上 | 内嵌浏览器 / 演示站 8147，视口 390×844 | 新建页（手动录入）单列与滚动定位 | `scrollWidth == innerWidth == 390`（无横向溢出），滚动定位导航为「附件 / 详细信息 / 物料」，标签全中文，折扣总额正常显示 | 手机宽度回归本次未破；随后已把视口复位；真机仍未测 |
| 2026-09-23 | F2 改动（未提交时） | 内嵌浏览器 / 演示站 8147 | 用真实接口造一条销售方向待处理草稿 → 列表出现「LINE」→ `/desk/pearnly-sales/intakes` → 打开该草稿 → 行内「取消」并填原因 | 列表出现 LINE 按钮；待处理页原生列表（编号/创建日期/用户/备注，中文）；打开后进「新建销售单」并带 `source=line` 与附件；取消弹窗（取消原因 + 确认）提交后 `status=discarded`、原因留痕、页面回列表；删掉该草稿后 LINE 按钮消失 | 三层都通过（页面 / 交互 / 服务端回读）；该草稿是本次造的，已删除；页面上查看器对这条 1×1 合成图显示「无法加载预览」是服务端重编码该图为无效 PNG 所致（原图 `naturalWidth=1` 能加载），不是页面问题 |
| 2026-09-23 | 同上 | `pearnly-purchase-lab` / `erp-pos-b.localhost` 隔离检查站 | `PEARNLY_ERP_POS_LAB_SITE=erp-pos-b.localhost ./env/bin/python /workspace/scripts/erp-pos-lab-intake-verify.py` | 仍有 problems（采购方向缺 `doc_no`、员工被 owner 范围拦住、隔离断言受演示站遗留影响）；销售方向在改 `receipt` 后单跑成功 | 见 F2：本探针只修了 D1 遗留那一处，其余两处不是销售域的；**没有**据此声称 LINE 切片全通过 |
| 2026-09-23 | F3 改动（未提交时） | 宿主 | `node --check sales*.js purchase*.js product.js` / `check-architecture.py` / `i18n-build.py --check` | JS OK / `PASS (0 violations)` / `RESULT: PASS`（erp 619 条） | 新引用的文案（Grand Total / Outstanding Amount / Payment / Collection）都已在术语表里，没有新增英文原文 |
| 2026-09-23 | 同上 | 内嵌浏览器 / 演示站 8147（改动前后同一场景） | 打开收款/付款弹窗 → 点开方式下拉 → 取「提交」按钮中心那个点问它是谁 | **改前**：1440/1100/768/600/390 五个宽度全部命中下拉里的 `LI`（按钮点不到）；**改后**：同样操作全部命中 `BUTTON`（可点） | F3 原因一，改前改后各量一次 |
| 2026-09-23 | 同上 | 内嵌浏览器 / 演示站 8147 | 采购列表：`PE2569-000070`（未付）与 `PE2569-000069`（部分付款）行 | 「未付 / 部分付款」两个字变红（`rgb(204,41,41)` = 框架 `--red-600`），行内有「付款」按钮；15 个欠钱单元格全在列，已付款行不标红 | 与差异看板同一个红；颜色走 `cell_class`，没有往列表里塞 HTML |
| 2026-09-23 | 同上 | 同上 | 采购行内「付款」→ 弹窗（付款金额 104.00 / 现金 / 记账日期 / 备注）→ 金额改 50 → 提交 | 记录 `PUR-2026-00190` 回读 `paid_amount 50 / net_payable 104 / payment_status partial`；行内仍是「付款」 | 用户要求「输入金额试试」这一条，服务端回读为证；随后已把该样本改回未付 |
| 2026-09-23 | 同上 | 同上 | 销售行内「收款」→ 弹窗（已收金额 214.00 / 现金 / 收款日期）→ 提交 | `SD-00204` 回读 `paid 214 / paid / submitted`，列表行**不再有按钮**、状态不标红 | 收款/付款两边都真的写库 |
| 2026-09-23 | 同上 | 同上 | 已付清的销售单详情页与未付清的采购单详情页 | 销售已付清：主按钮区没有「收款」（只有菜单里的「取消」）；采购未付清：仍有「付款」 | 详情页与列表同一口径（已付已收不可更改） |
| 2026-09-23 | 同上 | `pearnly-purchase-lab` / `erp-pos-b.localhost` 隔离检查站 | `./env/bin/python /workspace/scripts/erp-pos-lab-sales-verify.py` | `problems: {}` / 0 | 本次只改前端与共享皮肤，服务端契约未动；探针仍全绿 |

| 层级 | 状态 | 依据与未覆盖项 |
| --- | --- | --- |
| 实现 | 已完成（去开票侧 + 销售前端与一次提交 + 删草稿授权与文案收尾 + 收款付款弹窗与状态口径） | 旧销售页残留的「收款状态/备注」等字段面与采购一致；未做修订（见上）；删草稿不带私有附件清理（见 F1）；界面上没有再改已收付金额的入口（见 F3） |
| 本地验证 | 通过（离线 + 真实 Site + 桌面/390px 浏览器 + 真实私有附件 + 界面删草稿实删 + LINE 待处理页 + 收款付款真的写库） | 未覆盖：真机、真实付费识别（本环境无 OCR 凭据，识别走响应替身）、真实 LINE 发送（页面走真实接口造的草稿，不是 LINE 通道发来的）、生产部署、用户验收；LINE 草稿对员工角色不可见/不可确认（F2 第 3 条，归 intake 切片） |
| 部署 | 未执行 | 本任务未获部署授权 |
| 外部业务结果 | 未执行 | 不调用旧生产、不付费识别、不发 LINE |
| 用户验收 | 未执行 | 采购与销售都未由用户验收，本记录不代签 |

## 收尾：交接保留

- 保留：采购工作树 `/Users/skin/.codex/worktrees/erp-purchase-records/pearnly` 及分支 `codex/erp-purchase-records`（含采购成果与本次交接，未合并主线，作为销售基线与回退挂载来源）；销售工作树与本任务分支；`pearnly-purchase-lab` 六个容器、`sites`/`db` 卷、合成样本与私有附件（继续用于演示与验收）。解除条件为销售成果按授权集成、采购完成用户验收、且核验无挂载与独有提交。
- 本次在演示站**删掉**了上一窗口为「删草稿」检查建的合成草稿 `SD-00206`（它没有附件，站内也不再有它的私有文件）；`SD-00204`（含私有附件）保留供用户与下一窗口看，状态已回读确认与原样一致（已收款 214 / 现金 / 2026-09-22）—— 中途为验 F3 改过（部分收款、全额、日期），最后一次已用同一条服务路径改回原值。
- F3 期间在演示站改动过的采购样本 `PUR-2026-00190`（`PE2569-000070`）已改回未付（`paid 0 / is_paid 0 / unpaid`），与本次开工时一致；`PUR-2026-00183`（`PE2569-000066`）也在试弹窗时改过，同样已改回未付。
- 误跑一次默认站是演示站的旧 LINE 探针（F2 第 4 条）之后，把它在演示站产生的 3 条草稿（`LI-00209/210/211`）与 6 个私有文件清掉；用于看 LINE 页面的 `LI-00212` 也连同 3 个文件删除。回读演示站：`ERP LINE Intake` 9 条（都是采购方向已确认的历史样本）、intake 文件 18 个、销售单据只有 `SD-00204`（附件 1 个）、销售方向待处理 0 条 —— 与本次开工时一致。
- 原 stocktake / erp-pos / Cowork 工作树与容器归各自任务，本任务只读查看，未取得清理授权。
- 本任务未创建远端分支；未创建临时夹具或额外服务。
