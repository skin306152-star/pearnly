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

### F4：收发存的「关联单据」对作废行显示内部编号（2026-09-23，未部署）

用户报「单号在收发存显示得不对」。看真实界面（库存页的明细弹窗 + 收发存二级明细页）后确认：**原单那几行是对的**（入库 `IR2569-000003`、销售出库 `SI2569-000004` 都是单据号），**只有作废产生的那一行不对** —— 它显示的是 Frappe 内部名（`SD-00286`、`STE-2026-00225` 这种），因为：

| 事实 | 证据 |
| --- | --- |
| 报表的"关联单据"列 = `doc_no || desc` | `public/desk/stock-card.js` 的 `movement_cells` / `movement_rows`；`inventory/report.py` 里 `desc` 取的是 `ref_id`（内部单据名） |
| 作废行没有 `doc_no` | `ERP Stock Movement` 里 `movement_type='adjust'`、`ref_type='void'` 的行 `doc_no` 全为空（演示站 13 条） |
| 原因在共用冲销入口 | `inventory/stock.py::reverse_movements` 复制原流水时**没带 `doc_no`**；它被五处调用：销售作废、采购作废（两处）、入库/出库作废、收银退货/作废 |

**做法**：`reverse_movements` 复制原行的 `doc_no` 到冲销行 —— 同一个单据的冲销行，号与原单一致。改一处，五处调用一起正确。

**历史行**：演示站里修复前写下的 13 条作废行（1 条销售 + 12 条采购）已按原单的号补上 `doc_no`（只补空的、可重跑）。生产未部署过这套，所以不存在需要处理的真实历史。

**顺带发现（未改，不是销售域）**：`scripts/erp-pos-lab-inventory-verify.py` 已经跑不起来 —— 它给 `documents.create_entry` 传 `doc_no="LAB-INV-ENTRY-1"`，而单据号早已改成系统发号、`create_entry` 不再收这个参数，于是第 181 行 `TypeError: string indices must be integers`。冲销路径本次改为**直接验**：隔离站建一张入库单 → 提交 → 作废，回读冲销行 `doc_no == IR2569-000001`。该探针的修复归库存/POS 任务。

### F5：销售演示样本（2026-09-23，只落演示站）

用户要求"销售这边多搞点数据"。全部走**真实业务接口**（`masters.create_customer`、`products.create_product`、`inventory.create_entry`、`sales.entry.post` / `payment` / `files.add` / `cancel`），没有直接写表；请求号是 `demo-sales-01..22`，重跑会命中幂等、不会重复建。

| 造了什么 | 数量 | 说明 |
| --- | --- | --- |
| 客户主档 | 6 | 泰文公司名/人名，带地址与电话；公司 5 家、个人 1 位 |
| 商品主档 | 6 | 泰文品名（鱼露/鱼罐头/茉莉香米/植物油/方便面/炼乳），带单位、售价与参考成本 |
| 入库单 | 6 张（`IR2569-000001..000006`，各 150） | 让这些商品在收发存里有一条进来的流水 |
| 销售单据 | 22 张（`SI2569-000002..000023`） | 收据 20 + 报价单 2；已收款 14、部分收款 3、未收款 4；来源含人工/OCR/LINE；1 张已作废 |
| 私有附件 | 2 张（付款凭证 + 单据） | 挂在样本单上，供附件区与查看器看 |

**本次造数据时自己的两个失误，都已处理**：把 6 张入库单的单价录成了 0 → 已作废并按参考成本重开（`IR2569-000007..000012`）；有一条本想记成"未收款"，被服务端默认成已收满 → 按表头口径改回未收。副作用：这几样商品的**结存均价**被"0 价那张历史 + 冲销"带着走（例：ข้าวหอมมะลิ 现在 91.83，而参考成本是 185）。数量是对的，报表算得也对，只是均价不像"一次录对"的样子；要干净的一组样本需要连同样本单据一起重建，等用户定。

### F6：编号定义与"每一笔记录联动"的逐条核对（2026-09-23，只读审计 + 记录）

用户口径："物料是自动生成编号的、有编号定义的；出库入库、采购销售也有编号定义、也是自动生成的；然后这些编号都会和商品库存、出库入库、收发存报表，每一笔记录都产生联动。"

**MR.ERP 的编号定义长什么样**（用户给的源码 `/Users/skin/mrerp-source`，只读）：

| 事实 | 出处 |
| --- | --- |
| 定义表 `tbl_autono`：`comidyear` + `idmenu`（单据类型）+ `statusautono`（是否自动编号）+ `userdefinedno`（前缀）+ `runautono`（当前流水） | `stderp2020.sql:1920`；真实数据行例 `(27, 5, 68, '1', '6401', 3)` |
| 定义跟着**菜单维护**走：可勾选自动编号、可填前缀（≤8 字符）、当前流水只读展示 | `src/menu/menuform.php:143,496-506`、`src/menu/menuupdate.php:224-257` |
| 号码形状 = 前缀 + `-` + 六位零填充流水；表单上填进**只读**输入框 | `src/stktrant/allform.php:384-391` |
| 保存时校验"提交的号不小于计数器"，并把计数器推到已用号 +1 | `src/stktrant/allsave.php:295-331` |

**我们这边（新平台）的对应实现**：

| 对象 | 定义 / 形状 | 自动发号入口 | 界面（只读预览） |
| --- | --- | --- | --- |
| 物料 | `P-` + 6 位流水（`products/contracts.py` 的 `CODE_PREFIX` / `next_code`） | `products.service._allocate_code` | 新建商品只读显示"系统将要发的编码"（`api_next_product_code`）；库存页有"编码"列 |
| 入库 | `IR` + 佛历年 + `-` + 6 位 | `numbering.allocate(direction="in")` | 新建入库单只读显示当前号（`api_next_stock_entry_number`） |
| 出库 | `IS` + 佛历年 + `-` + 6 位 | `numbering.allocate(direction="out")` | 同上 |
| 采购 | `PE` + 佛历年 + `-` + 6 位 | `numbering.allocate(direction="purchase")` | 列表首列与页面标题用 `system_no` |
| 销售 | `SI` + 佛历年 + `-` + 6 位 | `numbering.allocate(direction="sales")` | 表单只读字段"单据编号" + 列表首列 |
| 计数器 | 原生 `ERP Doc Sequence`，一个"类型 + 年"一行，取号时 `for update` 排队 | `numbering/service.py` | 演示站现有 `in-IR-2569`、`purchase-PE-2569`、`sales-SI-2569`（出库那行首次发号时才建） |

**与 MR.ERP 的有意差异**：前缀与形状写在代码里（不改库、租户改不了），MR.ERP 是在"菜单维护"里可改前缀与开关。本仓按 [架构要求](../development/architecture.md) 与用户既定口径保留固定前缀；要改成"租户可定义"是产品决定，本记录只登记差异。

**"每一笔记录都联动"的逐条核对**（演示站回读 + 界面截图）：

| 记录来源 | 流水带号 | 在哪看得见 |
| --- | --- | --- |
| 采购确认 → 入库 | `PE2569-000073` 等 | 收发存明细"单据编号"、库存明细弹窗"关联单据"、采购列表首列 |
| 销售确认 → 出库 | `SI2569-000002` 等 | 同上（销售列表首列 `SI2569-0000xx`） |
| 入库 / 出库单据 | `IR2569-0000xx` / `IS2569-0000xx` | 出入库列表首列、收发存、库存明细 |
| 收银卖货 / 退货 | 小票号（`cashier/sales.py` 传 `doc_no=doc.receipt_no`） | 同上 |
| 作废冲销 | **原单的号**（上一轮 F4 修的） | 同上；修复前显示内部名 |
| 商品 | `P-0000xx` | 库存页"编码"列、出入库明细行、收发存**导出** |

**核对出的三个没编号的入口（都不在用户这次的清单里，登记待定，未改）**：

1. **盘点过账**（`inventory/count.py::post_count`）：流水 `movement_type='count'`、`doc_no` 为空 → 收发存只能显示内部名。隔离站实测：`STC-00120` 过账后流水 `doc_no=None`，明细行显示 `STC-00120`。**旧线上不是这样**：盘点差异给每个差异分配 `IR`/`IS` 号并**落一张入库/出库单据**（`services/erp/inventory.py` 的 `document_numbers.allocate` + `INSERT INTO erp_stock_documents ... source: inventory_count`）。要按旧线上口径对齐，就得让盘点差异生成入库/出库单据 —— 那是盘点窗口的模块，且会影响 `stocktake-workbench-lab-verify.py` 的断言，本任务不擅自改。
2. **期初**（`inventory/openings.py`）：流水 `ref_type='opening'`，无号；期初日在报表区间之前时它并进"期初"一行（不占行、也就无所谓号），落在区间内才会显示成一行。
3. **直接动库存的 API**（`inventory/api.py` 的 `api_receive` / `api_issue` / `api_adjust`）：不建单据、不带号；前端没有调用方，目前只有 `scripts/erp-data-flow-lab-verify.py` 在用。

**一处呈现不一致（未改）**：收发存的**列表**只有物料名称、没有"编码"列，而同一个报表的**导出**与库存页都有编码。

### F7：按 MR.ERP 对齐"盘点差异也开单带号"（2026-09-23，未部署）

用户口径：业务联动逻辑对齐 MR.ERP，全部。

**MR.ERP 那边"谁写库存"是清楚的**（`rg "INSERT INTO tbl_stkcard" src/`）：

| MR.ERP 模块 | 写库存卡 |
| --- | --- |
| 收货 `stktranrec`、发货 `stktraniss` | ✓ |
| **调整 / 盘点 `stktranmod`** | ✓（`trandocno = 自己菜单定义的前缀 + 流水`） |
| 采购 `aptran`（另有 `appe/appd/appc`）、销售 `artran`（另有 `arse/arsd/arsc`） | ✓（`tbl_stktran.trandocno = 单据类型 + 单据号`，`transowner` 记来源模块） |

也就是：**每一种会动库存的单据，都把自己的号写进库存卡**，没有"无号的库存流水"。我们这边原先只有一处例外：盘点过账直接写 `count` 流水、不带号（收发存只能显示 `STC-00379` 这种内部名），而旧线上盘点差异本来是**发 IR/IS 号并落一张出入库单据**的（`services/erp/inventory.py` 的 `document_numbers.allocate` + `insert erp_stock_documents ... source: inventory_count`）。

**改法**：`inventory/count.py::post_count` 不再直接写 count 流水，改为对每笔差异用 `documents.create_entry` 开一张入库/出库单据（系统发的 IR/IS 号、`ref_type=count`、`ref_id=盘点单`、`reason=stocktake:<盘点单>`），流水带着这个号；`create_entry` 新增 `commit=False`（盘点整批一个事务，默认行为不变），所以**过账的原子性没破**。

**验证**：

| 场景 | 结果 |
| --- | --- |
| 演示站盘点（`STC-00379`，差异 +3） | 生成 `IR2569-000020`；流水 `doc_no=IR2569-000020`、`ref_type=count`；库存明细该行显示 `入库 IR2569-000020` |
| 出入库页 | 列表出现这些单据（`IR2569-000020 ... stocktake:STC-00379`），可按号打开 |
| 作废该单据 | 隔离站实测：数量退回原值（`IR2569-000002` 作废后 2 → 0），说明这类单据的冲销链路是通的 |
| 盘点验收脚本 | `stocktake-workbench-lab-verify.py` 仍 `{"ok": true}`（含"atomic posting rollback""finished variance valuation is frozen"）；回滚断言按新落点改为"没有 `ref_type=count` 的单据、也没有 `stocktake:<盘点>` 的流水" |

**行为差异（有意）**：盘点过账现在走和出入库同一道门，所以**停用商品不能过账**（原先 `allow_inactive=True` 放行过）；流水类型由 `count` 变为 `stock_in/stock_out`，收发存里按"入库/出库"显示（旧线上盘点差异是 in/out 单据，同形）。这两点如需保留旧行为再定。

### F8：「容器里看不到盘点」的根因（2026-09-23，已修实验室环境）

用户问"你们的容器怎么都没盘点功能，代码没对齐吗"。核对结论：**代码是对齐的，是实验室站点少了"买到盘点"这一步。**

| 检查项 | 事实 |
| --- | --- |
| 菜单声明 | `pearnly_erp_portal/hooks.py` 的 `pearnly_navigation` 里有 `('nav.stockCount', 'pearnly-stock-count', 'boxes', 'stocktake')`，且 `available` 含它 |
| 页面 | `pearnly_core/.../page/pearnly_stock_count/`（`Page` 记录 `pearnly-stock-count` 已在库里） |
| 服务与验收 | `inventory/count.py` 在（本窗口刚改过过账）；`stocktake-workbench-lab-verify.py` 跑出 `{"ok": true}` |
| **站点"已购功能"** | `pearnly_features = ["products","warehouses","stockcard","purchase","sales","clients"]` —— **没有 `stocktake`，也没有 `cashier`** |
| 侧栏 | 侧栏是原生 `Workspace Sidebar`（`Pearnly ERP`）文档，按当时的已购功能生成并留存；`visible_pages()` 只按"已购 + 有权限 + 已实现"过滤，所以盘点被藏 |

成因：`scripts/erp-pos-lab-init.py` 把功能清单**手抄**在脚本里（漏了后来加的 `stocktake` 与 `cashier`），盘点那条功能码就成了"没买的功能"。盘点那包当时是在自己的探针里临时补站点配置绕过（`erp-pos-lab-stock-count-verify.py:107-110` 注释"样板站点在开通时写死一份，实验室自己补"）。

**已做**：

1. `scripts/erp-pos-lab-init.py` 不再手抄清单，改为读产品自己的目录 `apps/pearnly_erp_portal/pearnly_erp_portal/features.json`（加控制面的 `home_subscription`）——下次建实验室不会再漏。
2. 两个实验室站点的 `pearnly_features` 按产品目录补全（6 → 10 个功能码：加 `home_subscription/stocktake/cashier/erp_push`）。
3. 重跑 `pearnly_core.desk.navigation.ensure_navigation` 重建侧栏（幂等）。侧栏文档现在是 `…stockCard, stockCount, …sales, cashier`。

**验证**：侧栏出现「盘点」与「收银 POS」；打开 `/desk/pearnly-stock-count` 正常（标题「盘点」、按钮「新建盘点」「筛选」，列表 5/5，含本次的 `盘点联动检查`）。

### F9：MR.ERP 里"采购/销售要不要进入库/出库列表"（2026-09-23，只读核对）

用户口径：只按 MR.ERP 判断，不看旧线上。

| MR.ERP 事实 | 出处 |
| --- | --- |
| 库存单据头 `tbl_stktran` 区分两类来源：出入库模块写 `tran`（收货/发货/调整各自模块），采购/销售/费用模块**不写 `tran`**、写 `transowner`（来源模块名），`trandocno = 单据类型 + 单据号` | `src/stktrant/allsave.php:64-68`（`tran` + `idmenu`）、`src/aptran/allsave.php:253-258`（`transowner='aptran'`）、`stderp2020.sql:3425-3447` 的字段 |
| 入库/出库**列表按各自菜单号过滤**：`FROM tbl_stktran WHERE (idmenu=?)` | `src/stktranrec/component/showdata.php:25-48`、`src/stktrant/component/showdata.php:25-48` |
| 报表能专门区分：出库报表有一版条件带 `(m.tran IS NOT NULL)`，即"真正的出入库单" | `src/expstktraniss/component/expdata.php:102,104` |
| 内部出入库的编码定义 = 每个出入库菜单一行（自动编号开关 + 前缀 + 当前流水） | `tbl_autono` 的定义与 `src/menu/menuform.php:143,496-506` |

**所以按 MR.ERP：采购不在入库列表出现、销售不在出库列表出现。** 它们以**自己的单据号**进库存卡（报表），用 `transowner` 标明来源模块。内部出入库单是**另一套独立定义**（收货/发货/调整各一个菜单、各自一行编号定义）。

**我们的现状与之对照**：采购确认/销售确认**不生成入库/出库单**，只写带 `PE`/`SI` 号的流水（`purchasing/entry.py`、`sales/entry.py` 直接 `stock.apply_lines`）✓；入库/出库是 `IR`/`IS` 两个独立定义 ✓（计数器 `in-IR-2569` / `out-IS-2569`）。**一处差异**：MR.ERP 把"调整/盘点"当第三个内部模块、有它自己的编号定义（`stktranmod`），我们目前把盘点差异放进入库/出库单（用 IR/IS）。要不要给盘点单独一套定义（例如独立前缀），等用户定。

### F10：收款弹窗与采购对齐、采购补"单据编号"（2026-09-23，未部署）

用户给三张图：销售收款弹窗的下拉把下面的字段盖住、要和采购付款弹窗一样；采购新建页缺销售那样的"单据编号"。

| 改的东西 | 落点 |
| --- | --- |
| 销售收款弹窗补"备注"，成为与采购付款**同一套四个字段**（金额 / 方式 / 日期 / 备注） | `public/desk/sales-form.js` 的 `u.payment` |
| 备注跟钱一起记：单据新增 `payment_note`（Small Text），`entry.payment` 收 `note`，撤销收款时一并清掉；详情页只读显示"收款备注" | `doctype/erp_sales_document/*.json`、`sales/entry.py`、`public/desk/sales-fields.js` |
| 采购新建/详情页"详细信息"第一格补只读"单据编号"`system_no`（与销售同位同口径），新建时显示系统将要发的 PE 号 | `public/desk/purchase-fields.js` |
| 术语表补 `Collection Remarks` | `i18n/messages.tsv` + 生成包 |

**验证**：销售收款弹窗字段 = 已收金额 / 收款方式 / 收款日期 / 备注（与采购付款一致）；在真实界面提交一笔带备注的收款成功，回读 `payment_note = 现金收讫 by 采购验收`（随后把该演示样本改回未收款，备注一并清空）；采购新建页显示 `PE2569-000074` 预览；销售详情页出现"收款备注"；销售探针加 `payment_note` 断言后仍 `problems: {}`。

**注意（未改）**：这两个弹窗里的"方式"是自动完成式下拉，展开时会盖住它下面的字段 —— 采购、销售**同样**如此（图一就是这个现象）。要彻底不遮，需要把这类下拉换成原生 `<select>`（会牵动全站的选择控件外观），等用户定。

### F11：盘点独立编号（AD）+ 删掉采购的「单据类型」（2026-09-23，未部署）

用户口径：盘点也按 MR.ERP 对齐（那边"调整/盘点"是第三个内部模块、有自己的编号定义）；并要判断采购的「类型」与「单据类型」哪个没用、删掉没用的。

**一、盘点独立编号（对齐 MR.ERP 的 `stktranmod`）**

| 改的东西 | 落点 |
| --- | --- |
| 编号定义新增第三段：盘点/调整 `AD`（与入库 `IR`、出库 `IS`、采购 `PE`、销售 `SI` 并列，方向名 `stocktake`） | `numbering/contracts.py` |
| 建单可取号方向覆盖：`create_entry(..., numbering_direction=...)`；单据仍按 in/out 决定货的方向，号来自覆盖后的定义 | `inventory/documents.py` |
| 盘点过账传 `numbering_direction="stocktake"` | `inventory/count.py` |

**验证**：演示站盘点 −2 生成单据 `AD2569-000001`，收发存该行 `单号 = AD2569-000001`（`kind=stock_out`）；计数器独立成行（`stocktake-AD-2569`，与 `in-IR-2569` / `out-IS-2569` 并存）；盘点验收脚本加断言"盘点过账的单据号都以 AD 开头"后仍 `{"ok": true}`。

**二、删掉采购的「单据类型」`doc_type`**

两个字段的实际用途（只看代码，不看旧线上）：

| 字段 | 语义 | 谁在用 |
| --- | --- | --- |
| 「类型」`doc_kind` | 采购发票 / 采购订单 / 费用 | **在用**：默认 `purchase_invoice`；`service` 时明细按服务处理；列表「费用」分组用它；导出列；识别也会填 |
| 「单据类型」`doc_type` | 税务发票 / 简式税务发票 / 收据 / 采购订单 / 替代收据 / 其它 | **没有规则用它**：只做了四件事 —— 存下来、列表能筛、导出翻一列、识别时问一句。MR.ERP 的采购单头也没有这个字段（头表只有供应商税号 `vatnum` + `vatdate`） |

按"没用的删掉"删了 `doc_type`：DocType 字段、`HEAD_FIELDS`、默认值、列表字段与筛选条件、导出枚举、识别提示词、采购表单字段、采购列表筛选项一起清掉；采购验收探针里那两处引用同步删掉。保留 `doc_kind`（它承载业务分支）。术语表里的 `tax_invoice` 等词条留着不动（不影响；将来真做进项税报表要用到"票种"这个概念时再决定放哪）。

**验证**：采购新建页只剩「单据编号 / 记账日期 / 供应商发票号 / 类型 / 已付款 / 付款方式 / 到期日」等，不再有「单据类型」；`frappe.get_meta('Purchase Document').get_field('doc_type')` 为空；销售探针 `problems: {}`、端口测试 102 条 OK、架构检查 0 违规。Frappe migrate 不删列（旧列留在库里，应用不再读写，同 D1 的处理）。

### F12：数量显示按实际位数（不留尾零）（2026-09-23，未部署）

用户口径："采购销售的物料数量不要留那么多0，涉及到数量的地方都改规整。"

**根因**：框架的 Float 控件按字段的 `precision` 补零 —— 采购/销售明细数量写的是 `precision: 6`，所以 2.5 显示成 `2.500000`；只读时框架又走 `frappe.format(value, df)`，它把 `precision: 0` 当"没设"退回系统默认（2 → `2.000`、2.5 → `2.500`），所以不能靠调 precision 解决。

**做法**（共享层一处，所有数量都受益）：`pearnly_core/public/desk/product.js` 新增 `p.tidy_number(field)`，在 `p.layout`（所有 FieldGroup）与 `p.dialog`（所有我们自己的弹窗）里对 **Float / Percent** 控件自动挂上：

- 可编辑：包一层 `format_for_input` 去掉尾零（只改显示文本；`parse` 走另一条路，所以输入 2.5 不会被四舍五入）。
- 只读：`set_disp_area` 改用我们自己的 `p.number()`（按实际位数），避开框架那个 precision 回退。
- **金额（Currency）不动** —— 钱固定两位；百分数用框架自带的"按值截断"逻辑（7 → 7%）。

**验证**：采购详情数量 `2.500000 → 2.5`；销售详情数量 `18`（不是 18.000000）；采购新建行默认数量 `1`（不是 1.000000）；销售新建输入 2.5 后失焦仍是 `2.5`；入库单弹窗数量 `2`；这些页面全页扫描再无 `数字.三位以上小数`。

### F13：供应商 / 客户独立菜单（2026-09-23，未部署）

用户口径（两条，后者覆盖前者）：先要"新建时一个弹窗输入相关信息就搞定"，随后明确
"**单独做一个供应商和客户的菜单**，和销售采购联动上就好了"。

所以做成**两个独立菜单 + 一个弹窗**（不是采购/销售表单里内嵌的新建按钮；那条路已经撤掉）：

| 菜单 | 路由 | 列 | 弹窗字段 |
| --- | --- | --- | --- |
| 供应商（采购系统组） | `/desk/pearnly-suppliers` | 供应商 / 税号 / 分支 / 电话 / 地址 / 状态 | 供应商名称\* / 电话 / 税号 / 分支类型 / 分支编号 / 地址 / 备注（改既有供应商时多一个"启用"） |
| 客户（销售系统组） | `/desk/pearnly-clients` | 客户名称 / 客户类型 / 税号 / 电话 / 地址 / 状态 | 客户名称\* / 客户类型 / 电话 / 税号 / 地址 / 备注（同上） |

**联动方式**：就是原生的 Link 字段 —— 采购单据的"供应商"指向 `ERP Supplier`、销售单据的"客户"指向 `ERP Customer`，所以菜单里建完，开单时就能选到；选中后供应方名称/税号/地址（采购）与客户名/类型/地址（销售）由单据页自动回填。收银在客户表上只读（沿用既有岗位口径），所以收银员看得到这一页、建不了。

| 改的东西 | 落点 |
| --- | --- |
| 页面登记从 core 的占位（`notBuilt`）搬到 ERP App：`page/pearnly_suppliers/`、`page/pearnly_clients/`（json + register 脚本）；core 的四个占位文件删除 | `apps/pearnly_erp_portal/.../page/`、`apps/pearnly_core/.../page/` |
| 两个页面的实现（原生列表 + 弹窗；`can_write` 决定弹窗是编辑还是只读） | `public/desk/suppliers.js`、`public/desk/clients.js` |
| 菜单挂到已有分组（供应商→采购系统、客户→销售系统）；`available` 与 `permissions`（按 `ERP Supplier` / `ERP Customer` 的读权限）登记 | `hooks.py` 的 `pearnly_navigation` |
| 新命名空间 `supplier.` / `client.` 进文案白名单（否则整页取不到文案） | `pearnly_core/browser_text.py` |
| 术语表新增 `supplier.*` / `client.*` 共 44 条（四语言） | `i18n/messages.tsv` + 生成包 |

**验证**：

| 场景 | 结果 |
| --- | --- |
| 两个页面的登记与归属 | migrate 后 `Page` 记录的 module 变成 `Pearnly ERP Portal`（不再是核心的占位）；侧栏出现「供应商」（采购系统下）与「客户」（销售系统下）；两页标题/列头全中文 |
| 建供应商（弹窗） | 真实界面填名称/电话/税号/地址 → 创建 → 列表出现该行；服务端回读 `SUP-00482`，字段与填写一致 |
| 建客户（弹窗） | 真实界面填名称/类型/电话/税号/地址 → 创建 → 列表出现该行（`CUS-00483`） |
| **采购联动** | 采购新建单的"供应商"输入 `SUP-004`，下拉出现刚建的 `SUP-00482`（并提示"已按 激活 已启用过滤"） |
| **销售联动** | 销售新建单的"客户"输入 `CUS-004`，下拉出现刚建的 `CUS-00483` |
| 服务端口径 | 以登录用户身份跑 link 检索：`ERP Customer CUS-00483`、`ERP Supplier SUP-00482` 都能查到（说明两个 Link 的取数路径通） |
| 离线检查 | 端口测试 102 条 OK；架构检查 0 违规；术语表生成 PASS；三个新 JS 语法 OK |
| 主数据探针 | `erp-pos-lab-masters-verify.py` 由红转绿（见下） |

**顺带修的一处探针过期（不是本次改动引起）**：`erp-pos-lab-masters-verify.py` 原来断言"另一个站点客户/公司数必须为 0"，而演示站这时已经留着给用户看的样本（6 客户 + 演示公司），于是它报红。隔离要验的是"**本轮的夹具**没有漏到另一个站点"，所以改成按夹具名（`Lab Buyer%` / `Lab Company%`）查泄漏，不再要求对方是空站。同类的"空站前提"在采购探针那边也早就踩过（采购任务已记录）。

### F14：税号查泰国税务局（RD）+ 列表多选/全选（2026-09-23，未部署）

用户口径两条：

1. "旧项目那边有一个东西，也搬过来，输入税号后点查询就能得到相应字段，**那是泰国税务局真实登记的信息**"；
2. "列表把多选全选加进去，**为什么每个窗口做这个列表都不加，是官方原生组件没有这个吗**"。

第 2 条的答案：**原生组件本来就有**（表头那颗全选框与每行的勾选框都是框架 `ListView` 自己渲染的），是**我们自己的产品列表默认把它删掉了** —— 共享层 `pearnly_core/public/desk/list.js` 的 `render_header()` 在页面没有声明 `selectable` 时执行 `remove(".select-like, .checkbox-actions")`。所以这是"每页要显式开一次"的选择，不是框架缺功能；选中的去向也要各页自己给（否则勾了没有动作）。本窗口把这套补齐到供应商/客户两页，并回盘了全部列表页（见下）。

| 改的东西 | 落点 |
| --- | --- |
| 解析层（**纯规则**：不联网、不写库、不依赖框架，所以能拿真实响应离线断言）：SOAP 请求模板、`vXxx` 摊平、按别名取值、地址/名称拼装、TIN 与 VAT 两种负载、税号归一 | `masters/rd_contracts.py` |
| 服务层：TIN + VAT 两个接口、5 秒超时、失败重试 1 次、7 天缓存（**"查不到"也缓存**）、**失败不抛异常**只回原因（`invalid_format` / `not_found` / `not_vat_registered` / `rd_unreachable` / `parse_error`） | `masters/rd.py` |
| 缓存表：原生 DocType `ERP Tax Lookup Cache`（`autoname: TAX-{tax_id}-{branch_no}-{service}`）+ 控制器；键与旧线上 `rd_cache` 表同形状（tax_id / branch_no / service / payload / is_success / error_msg / expires_at） | `doctype/erp_tax_lookup_cache/` |
| 接口与能力码：`api_lookup_tax(tax_id, branch)`；新能力 `tax.lookup` = 采购 / 客户 / 销售三个功能码（收银不开票、不给） | `masters/api.py`、`access/rules.py` |
| 两页弹窗里的「查询」按钮 + 回填：供应商在**税号下一格**（回填 名称/地址/分支类型/分支编号），客户同一位置（回填 名称/地址，`depends_on` 只在"公司"出现 —— 个人/外国人没有 RD 登记） | `public/desk/suppliers.js`、`public/desk/clients.js` |
| 多选/全选：两页 `selectable: true` + 共享层的 `p.selection_action`，选中后主按钮变「启用 N / 停用 N」（批量走既有 `api_update_supplier` / `api_update_customer`），做完整批后清空选择并刷新 | 同上 |
| 术语表 +27 条（四语言，含 4 条失败原因各一份 `supplier.lookup.*` / `client.lookup.*`）；`Head Office` / `Branch {0}` 是**服务端** `_()` 文案，按英文原文当 msgid 追加，不是键 | `i18n/messages.tsv` + 生成包 |

**照旧线上基准核对过，并有一处有意差异**（旧仓 `/Users/skin/Developer/Pearnly/pearnly-app`，生产基准 `ec2e5c957e6b`；`git diff ec2e5c957e6b -- services/rd/` 为空，即本地那份就是线上那份）：

| 项 | 旧线上 | 这里 |
| --- | --- | --- |
| 入口 | `services/rd/{rd_api,store,official_name}.py`，Postgres `rd_cache` 表 + `rd_daily_usage` 日配额 | 一个 App 内的 `masters/rd*.py` + 原生 DocType 缓存；**没有搬日配额**（旧线上是给公网用户防刷，本仓是租户内网员工用） |
| 缓存 | 7 天，成功与失败都写 | 一致 |
| **解析别名** | **只认** `vProvinceName` / `vAmphurName` / `vThumbolName`（`rd_api.py:412-414`） | **按别名**：线上下发的是 `vProvince` / `vAmphur` / `vThambol`，只认 `*Name` 会把**省和区丢掉**（测试夹具就是真实响应，专门断言了这点） |

**多选/全选的页面覆盖回盘**（`selectable: true` 的页面 / 未开的页面）：

| 状态 | 页面 |
| --- | --- |
| 已有（本窗口之前就有） | 物料、采购记录、销售记录、库存、入库、出库、收发存报表、盘点、收银 |
| **本窗口加上** | 供应商、客户 |
| 仍没有 | 采购/销售各自的 **LINE 收件箱**两个列表（它们是"逐条打开、逐条取消"的收件箱，批量取消要一次给一个取消原因）、单据表单里的**附件子列表**（逐行动作） |

**验证**见下方「验证」表最后六行与探针输出；**未覆盖项**：①浏览器里"点查询→字段被真实填上"这一步在本实验室**验不到** —— 容器出不了外网（`rdws.rd.go.th` 只在宿主上通），所以浏览器侧只验到"连不上时提示正确、不抛异常、弹窗与字段不受影响"；回填用的 `dialog.set_values` 是本仓既有用法（`purchase.js`、`purchase-fields.js` 同一 API）。②LINE 收件箱的多选未做（见上表）。③真机、生产部署、用户验收都未做。

### F15：主按动作的提示与音效、状态列两态、列表计数折行（2026-09-23，未部署）

用户口径三条（一次给的）："采购和销售的主按动作（每次）通知和音效"、"供应商和客户的状态要联动停用和正常状态"、"图中这个显示不规范，所有页排查修复下"（图：采购列表表头右上角 `20 /` `73` 折成两行）。

**一条条对**：

| 用户说的 | 查到的原因 | 改法 |
| --- | --- | --- |
| 主按动作没提示、没音效 | 采购/销售两页的写入动作（收款、付款、删除草稿、取消）走完只关弹窗 + 刷新，**没有**调用过全站那套 `p.success`（提示条 + 提示音）；其他页（收银、物料、入库、盘点）都有，就这两页漏了 | 补上 `p.success(...)`：收款「已收款 {金额}」、付款「已付款 {金额}」、删除草稿「已删除 {N}」、取消「已取消」；供应商/客户保存「已保存」；采购的"代收据/预扣税凭证"生成后「已保存」 |
| 供应商/客户的状态要联动 | 那两页的"状态"列**只在停用时写字**，正常时是**空白**（看着像没数据）；而且物料页同一个概念写的是「激活」（框架词典的译法），三页三种说法 | 三页统一成**正常 / 已停用**；停用/启用后列表刷新即跟着变（批量启停也走同一条） |
| 表头计数折成两行 | 皮肤把"有行内动作"的列表**表头右区**也按动作列宽固定了；动作列宽是按按钮文字量出来的，采购/销售那种「付款 / 收款」只有 **43px**，减去内边距后 `20 / 73` 放不下就折行 | 表头右区改成**按内容撑开**（行里的动作列保持固定宽度不动），并把"不许折行"写在那一格上（窄屏下框架把计数设成 `display: contents`，写在计数自己身上管不着） |

**一处必须记下来的坑**：这次不能用 `__("Normal")` / `__("Disabled")` 这种英文原文取词 —— **框架自带的词典会盖过我们 App 的翻译包**（`frappe/locale/zh.po`：Normal→普通、Disabled→禁用、Active→激活、Saved→已保存、Cancelled→已取消）。实测"状态"列一度显示成「普通」，所以状态两态改成**每页自己的术语键**（`product.active` / `supplier.active` / `client.active` 等），键不可能与框架词典撞名。带占位符的那几条（`Collected {0}` / `Paid {0}` / `Deleted {0}`）框架词典里没有，照英文原文取词没问题。

**音效怎么算验过**：`p.success` = `frappe.show_alert` + `frappe.utils.play_sound("submit")`，而 `play_sound` 会读账户级的静音开关（`mute_sounds`）。所以验的时候不只等提示条，还盯了页面里那个 `<audio id="sound-submit">`：点下去后 `paused` 变 `false`、`currentTime` 从 0 走到 874ms（`submit.mp3` 长度 882ms）才停 —— 声音真的放了，不是只弹了提示。

### F16：上传框只留「我的设备 / 摄像头」+ POS 开班交班（2026-09-23，未部署）

用户口径两条："接下来开始做 POS，做简单的开班交班，用官方原生前端、复用一些实用小功能，数据联动我们产品的数据"；"**全局**涉及到上传框的，库和链接给我取消掉"（附的图是原生上传框的四颗按钮：我的设备 / 库 / 链接 / 摄像头 —— 但那张弹窗**还带「设为私有 + 上传」的 footer**，说明是框架默认那条路，不是我们自己的六处调用）。

#### 一、上传框（全局一条线收紧）

| 按钮 | 框架里由谁控制 | 处理 |
| --- | --- | --- |
| 我的设备 | 永远有（拖放区/选择文件） | 留 |
| **库** | `disable_file_browser`（默认 false） | **去掉**（`disable_file_browser: true`） |
| **链接** | `allow_web_link`（默认 true） | **去掉**（`allow_web_link: false`） |
| 摄像头 | `allow_take_photo`（默认 true，且要浏览器有 `mediaDevices`） | 留 |

为什么不是按调用点改：我们自己的六处调用里有四处早就传了这两个开关，但框架自己还有**一堆**上传入口（附件侧栏 `attachments.js`、表单 `form.js`、明细网格 `grid.js`、文件管理器 `file_view.js`、沟通记录 `communication.js`…），那些我们改不到 —— 而且用户截图里那张正是框架默认那条（`file_view.js` 的 `new frappe.ui.FileUploader({folder})`，一个选项都没传）。

所以做法是在公共层挂一个**属性存取器**（`pearnly_core/public/desk/product.js`）：`frappe.ui.FileUploader` 是**懒加载**的（`frappe/upload.js` 里那句 `frappe.require("file_uploader.bundle.js")` 是异步的），我们这段脚本可能跑在它前、也可能跑在后。挂上 setter 之后，框架**什么时候**赋值都会被换成"固定关掉这两项"的子类；直接替换的话框架那次赋值会把补丁盖掉。

#### 二、POS 开班交班

后端 `cashier/{shifts,contracts,terminals}.py` 本来就有整套（行锁保证一个收银台同时只有一个开班、只能操作自己的班、找零只发现金、应有现金 = 备用金 + 现金实收 − 找零、差额 = 实点 − 应有）。这次补的是**看得见的那一层**：

| 改的东西 | 之前 | 现在 |
| --- | --- | --- |
| 开班弹窗 | 收银台要自己挑，开完没有任何回音 | 收银台**默认带出本店默认台**（与服务端 `terminals.resolve` 同一口径，留空也能用）；开完提示「已开班 · 班次 N」+ 提示音 |
| 交班弹窗 | 只让你填一个"实点现金"，**看不到系统记了多少**，差额自己算 | 先把**当班汇总**摆出来：笔数 / 退款笔数 / 毛额 / 净额 / 各收款方式（现金·刷卡·扫码·转账）/ 备用现金 / 找零合计 / **应有现金**；再填实点现金（默认 = 应有），**差额实时算**，不为 0 就标红并写清"多钱/短钱"；提交后提示「已交班 · 差额 X」+ 提示音 |
| 班次记录 | `api_list_shifts` 有接口，但界面上没有入口 | 新增「班次记录」：原生列表弹出，列 班次编号/收银台/收银员/状态/开班时间/交班时间/备用现金/应有现金/实点现金/**差额**（差额不为 0 标红） |
| 当班小票的「单据类型 / 状态」 | 走 `__("sale")` / `__("completed")`，会露出英文原文 | 用本页术语键 `trade.kind.*` / `trade.state.*` → 销售 / 退货退款 / 已完成 / 已作废 |

**顺带修掉两个真问题**（都不是本次功能引入的，是撞上的）：

1. **收银页的主按钮被列表刷掉**：列表没选中任何行时会去清右上角主按钮（"没选中就没有列表动作"是共享层的规矩），而收银页的主按钮是**班次动作**（开班 / 收银开单）—— 于是列表一刷新，「开班」/「收银开单」就被清成隐藏，页面上根本点不到。修法与商品页当初一致：把班次动作作为 `p.selection_action` 的 `empty` 回填（`restore_primary`）。
2. **原生表格在弹窗里画不出来**：`p.table` 构建时会往容器里插一个 `<style>`，容器还没接上文档时 `styleEl.sheet` 是 `null`，`insertRule` 直接抛 `TypeError`（实测就是空表：表头画出来、一行都没有）。这不是表格坏了，是**时序** —— 弹窗的表单要等 `shown.bs.modal` 才挂到文档上。修法照盘点明细弹窗那份：把渲染放进 `dialog.on_page_show`（框架在 `shown` 时调用），不靠定时器。

#### 三、演示站的 POS 样本（给用户点着看）

演示站原来**没有收银台、没有班次、没有一张 POS 小票**（`ERP Sale` 0 行）。这次按真实服务路径造了一套（不是直接写库，走 `cashier.sales.create_sale`，所以销售、收款、库存出库三处联动都真实发生）：

| 样本 | 内容 |
| --- | --- |
| 收银台 `TERM-00485` | "Counter 1"，默认台（`terminals.ensure_default` 生成的） |
| 班次 1 `SHIFT-2026-00486` | 备用金 1,000；2 笔小票（现金 140 含找零 4 / 扫码 78）；**已交班**，实点 1,130 → **差额 −6.00**（我故意少点 6 元，用来演示差额标红；要清掉说一声） |
| 班次 2 `SHIFT-2026-00492` | 备用金 1,000；1 笔小票（现金 40 含找零 1）→ 应有现金 1,039；**开着**，用户点「交班」就能看到完整汇总 |
| 小票 | `R20260923-0001/0002/0003`，都是「销售 / 已完成」 |

造样本时误开了一个空班（`SHIFT-2026-00491`，备用 100、无小票，我自己的误点），已删除；它名下没有小票，删掉不影响任何记录。

### F17：收银单号接上销售记录那套号、收发存确认、以及"POS 记录放哪"的官方口径（2026-09-23，未部署）

用户口径："收银这边的每一笔订单编号，要和销售记录的订单编号同步、业务逻辑自动延续，以及进收发存报表"；"数据联动要做好"；"POS 的记录官方是放在销售记录还是单独记，这个帮我看一下"（并要求看**本仓自己**的官方口径，不要翻旧项目）。

#### 一、收银单号改成与销售记录同一套（已改、已验）

改前收银自己发 `R20260923-0001`（按天递增，而且是自己 `get_value(order_by="receipt_no desc")` 取最大值 +1，**没走取号那把行锁**）；销售记录走共享取号 `numbering.allocate(direction="sales")` → `SI2569-0000NN`。两边各发各的。

| | 改前 | 改后 |
| --- | --- | --- |
| 收银小票号 | `R{日期}-{4位流水}`（另一套） | `numbering.allocate(direction="sales")` → **`SI2569-0000NN`**，与销售记录**共用** `sales-SI-{佛历年}` 那一行计数器与同一把行锁 |
| 收发存里的"关联单据" | `R20260923-0001` | `SI2569-000024`（与销售记录/入库/盘点同一个序列，一眼看得出是同一本账） |
| 并发 | 自己查最大值，两个收银台同时开单有撞号风险 | 走 `for update` 行锁排队 |

**验过的三层**（不是只看代码）：

1. 隔离检查站（`erp-pos-b`）落一笔 → `receipt_no SI2569-000033`，同一笔的流水 `doc_no` 也是 `SI2569-000033`；
2. 退货（`refund`）落一笔 → `SI2569-000034`，入库流水同样带 `SI2569-000034`（同一序列继续）；作废的冲销行沿用**原单**的号（`stock.reverse_movements` 带 `doc_no`，F4 那条）；
3. 演示站落一笔 → `SI2569-000024`，**正好接在现有 22 张销售单的 `SI2569-000023` 后面**；界面收银列表显示 `SI2569-000024`（历史那三张仍是原来的 `R…`，不动历史）；收发存报表里该商品（`ปลากระป๋อง ตราสามแม่ครัว`）的明细第 9 行就是 `SI2569-000024`（出库 2、结存 62），与 `IR…`/`AD…`/`SI…` 同一个账。

**一处要说明的影响**：顾客手里的小票号现在**就是**那个 `SI…` 号（不再是"每天从 1 开始"的小票号）。这是"和销售记录同步、自动延续"的直接结果；如果还想要一个独立的每日小票号，就得在收银单上再加一个 `system_no`（销售记录那张表就是"内部名 `SD-…` + 系统号 `SI…`"两个号），说一声我加。

#### 二、顺带修掉收发存明细页的一个真报错（数据联动缺陷）

`/desk/pearnly-stock-card/<物料>/<仓库>/` 这一页的**开始/结束日期改了没反应**：`df.change` 调的是一个**不存在**的 `reload()`，控制台报 `ReferenceError: reload is not defined`，期初/期末/明细都不重取（实测点日历换日期，表格 9 条明细纹丝不动）。改成把重取逻辑定义成 `refresh_card`，日期两格与右上角刷新都用它；验：日期改到 `2026-09-10` 后明细从 **9 条变 3 条**、期末从 62 变 276（3,588.00），没有报错。

**记一个环境坑**：Desk 那几支 JS 的 URL 带 `?v=<内容哈希>`，而哈希是**服务端启动时**算的（`desk/assets.py` 的 `versioned_asset`）。改完 JS 只重启 web 不够、得**先 `clear-cache` 再重启**，否则页面仍用旧的 `?v=` → 浏览器命中缓存 → 你看到的还是改前的代码（本次实测：文件哈希已变 `a116f324…`，页面却还在请求 `f945539e…`）。

#### 三、"POS 的记录官方放在销售记录还是单独记" —— 本仓口径：**进销售记录**（source=pos）

查的是我们自己的官方声明（不是旧项目）：

| 依据 | 原文 / 事实 |
| --- | --- |
| `apps/pearnly_erp_portal/pearnly_erp_portal/sales/contracts.py:230` | "用户 2026-09-21 对齐：销售记录的进出只有两条 —— **进**：POS 收银、上传单据识别、手工录入；**出**：商品/库存/收发存报表"；紧跟着 `SOURCE_POS = "pos"`、`SOURCES = (pos, ocr, manual)` |
| `ERP Sales Document` 的 `source` 选项 | `web / manual / ocr / line / **pos**` |
| `sales/records.py` | 列表/合计/导出共用的查询里，`source` **已经是可筛字段**，也在返回字段里 |
| 原生参照（Frappe/ERPNext） | POS 单也是一张销售单（`is_pos` 标记），交班另记一张 Closing Entry —— 同一本账加标记，不另立一套 |

**现状与声明的差**：POS 现在落的是**另一张表** `ERP Sale`（+行+付款），**不进**销售记录列表；销售记录页也还没有 `source` 那一列与筛选项（`source` 存了但没露出来）。也就是说 —— 官方口径是"一种来源"，实现是"两本账"。

**建议（等用户定）**：把 POS 小票**并进销售记录列表**（显示 `来源=POS`），点开仍开 POS 小票弹窗（班次/收款方式/找零那些留在 `ERP Sale`）。这样一条账、一个号、一张列表，且不动已有数据。另一条路是彻底并表（POS 直接写 `ERP Sales Document`，`ERP Sale` 退役），代价是给销售单加 `shift/terminal/change_amount` 等字段并搬一遍历史 —— 除非用户要求，不建议。

### F18：收银台按官方骨架重做（2026-09-23，未部署）

用户口径："收银台也要做，用官方原生的，数据关联商品，库存，收发存"、"扫码可以用官方的"，随后质疑"**你确定官方的收银台和进入方式长这样吗？**"。

#### 一、先把"官方长什么样"查清楚（本仓自己那份 2026-09-21 决定 + 框架源码）

本仓 `docs/work/2026-09-21-erp-pos-migration.md:143` 的登记："**POS 借 ERPNext 的前端体验与交互**，但业务后端仍走 Pearnly 的商品、库存、收发存与销售"。照这条去翻框架那份实现（bench 里 `apps/erpnext`，只读参考）：

| 官方（ERPNext POS）的做法 | 依据 |
| --- | --- |
| 是**一个标准 Desk Page**，不是"某个列表页里叠几段"：Page 名 `point-of-sale`、`standard: Yes`、路由 `/app/point-of-sale`、`roles`（Sales User/Manager、Accounts User/Manager）限定、`restrict_to_domain: Retail` | `erpnext/selling/page/point_of_sale/point_of_sale.json` |
| 页面里是**一屏多面**由这几块拼起来：商品选择（`pos_item_selector`，含 filter-section + items-container）、购物车（`pos_item_cart`）、行明细（`pos_item_details`）、**数字键盘**（`pos_number_pad`）、收款面板（`pos_payment`）、历史单（`pos_past_order_list/summary`） | `erpnext/public/js/point-of-sale.bundle.js` 的 import 清单 + 同名 js |
| 版式是**两栏**：`.point-of-sale-app` 下 `> .items-container` 与 `> .customer-cart-container`（8:4），另有 `.number-pad`；窄屏有专门断点 | `erpnext/public/scss/point-of-sale.scss:1/114/319/1120/1371` |
| 商品卡上直接给"可用数量"（`Quantity Available` 列） | `pos_item_selector.js:114-118` |
| **进来先开账**：`check_opening_entry()` 拉不到当前用户的开账单就 `create_opening_voucher()` 弹开账对话框（选支付方式 + 期初余额）；交班同理是这张页面上的 Dialog | `pos_controller.js:7-30` |
| **不是 Vue**：全是普通 class（`erpnext.PointOfSale.Controller` 等）挂在 Page 上 | 各 js 里 `createApp/defineComponent` 计数为 0 |

**结论（回答用户的质疑）**：他质疑得对 —— 我第一版把收银台做成了"收银页里叠三段原生列表（商品/购物车/当班小票）"，**版式不是官方那样**。进入方式那一层我们与官方**是一致的**（都是独立 Desk Page + 自己的路由 + 按角色显示；我们没开班时先弹「开班」Dialog = 官方那步开账），差的是**页面内容**。

#### 二、改成官方那套两栏收银台

| 改的东西 | 之前（我的第一版） | 现在 |
| --- | --- | --- |
| 版式 | 三段列表从上往下叠 | **一屏两栏**：左商品（约 8/12）、右购物车（约 4/12），照官方 `items-container` : `customer-cart-container` 的比例；窄屏（≤991px）堆成一栏 |
| 商品区 | 只有"物料/编码/单位/单价/库存"五行列表 | 同一张原生列表，但**搜索与扫码放进商品那一栏顶上**（官方也是把筛选/search 放在商品区里），五列宽度收紧到能挤进左栏（先前 240/130/70/100/90 超宽，库存列被顶出去） |
| 购物车 | 独立一段列表 | 右栏：顶上那行"应收金额 ฿X" + 明细列表（点行改数量/单价/折扣，行内「删除」），与右上角主按钮上的金额同一个数 |
| 扫码 | —— | **框架自带的 `frappe.ui.Scanner`**（用户："扫码可以用官方的"），扫到再走 `api_counter_lookup` 找品；查四处的规则在商品域里，收银台不另写一套 |
| 数据联动 | —— | 新增 `cashier/counter.py`：`products_page()` 一次给全**商品 + 售价 + 该收银台仓库的结存**（读 `ERP Stock Balance`，就是收发存那张表）；`lookup()` 同理；仓库按"收银台配的 → 默认仓库"解析，与落单时的口径一致 |
| 没有照搬的 | —— | 官方的**数字键盘**没做（我们收款走的是原生 Dialog 输入，收银只需键入口数）；官方的"历史单面板"用页面下方的「当班小票」列表代替（本仓原有那一块，保留） |

**新增/落点**：`cashier/counter.py`（新）、`cashier/api.py` 的两个只读接口（`api_counter_products` / `api_counter_lookup`）、`public/desk/cashier.js` 重做、`public/desk/cashier.css`（新，两栏版式）+ `hooks.py` 注册、术语表 +15 条。

#### 三、验过的（界面走完整条链，不是只看代码）

| 步骤 | 结果 |
| --- | --- |
| 打开「收银 POS」（班次 2 开着） | 一屏两栏：左 576px 商品、右 288px 购物车；商品表头 `物料 编码 单位 单价 库存 20 / 38`；右上角「收款 THB 0」禁用；按钮 交班/班次记录/清空购物车 |
| 搜"ปลากระป๋อง" | `1 / 1`，该行 `ปลากระป๋อง ตราสามแม่ครัว · P-000023 · กระป๋อง · THB 45 · **62**`（商品+售价+库存三样一起出来） |
| 点这一行 | 购物车出现 1 行：`… 1 THB 45 THB 45 删除`；右栏"应收金额 THB 45"；主按钮变「收款 THB 45」可点 |
| 点「收款」 | 原生弹窗「收银开单」：应收金额 THB 45 / 方式 现金 / 实收 45.00 / 找零 THB 0 |
| 实收改 100 | 找零**实时**变 `THB 55` |
| 提交 | 提示条「**小票 SI2569-000025 · 找零 THB 55**」+ 音效（0→882ms）；小票弹窗标题 `SI2569-000025`；购物车清空、合计回 THB 0、主按钮回禁用；「当班小票」变 `3 / 3` 且新单在最上 |
| 服务端回读 | `ERP Sale`：`SI2569-000025 · sale · completed · 总计 45 · 实收 100 · 找零 55 · 收银员 purchase-preview · 班次 SHIFT-2026-00492`；流水：`STM-2026-00498 · PRD-00220 · -1 · sale_out · doc_no SI2569-000025`；结存 PRD-00220 **62 → 61** |

#### 四、被用户当场否掉的两处（第一版仍然不对，F19 重做）

用户看完界面说："你这不是官方的收银台啊 ERPNext 是这样的收银台吗？POS 的记录我也是让你看 ERPNext 的"。两条都成立：

1. **收银台版式还是不对**：我做成了"两栏的原生列表"。ERPNext 的收银台是**商品卡片网格**（`pos_item_selector.js:150-200`：`.item-wrapper` = 库存色点 `indicator-pill`（>10 绿 / ≤0 红 / 其余橙）+ 商品图或缩写 `frappe.get_abbr` + 名称 + `售价 / 单位`），**购物车带合计区**（`总件数 / 净额 / 合计`，`pos_item_cart.js:88-105`）和**常驻数字键盘**（`pos_number_pad.js`；购物车那份是 5 列：`1 2 3 数量 / 4 5 6 折扣 / 7 8 9 单价 / . 0 删除 移除` + 底部「收款」，`pos_item_cart.js:120-150`；收款面板那份是 3 列 `1 2 3 / 4 5 6 / 7 8 9 / ± 0 删除`，`pos_payment.js:121-135`）。
2. **"POS 记录放哪"我答错了方向**：我先按本仓自己那份声明（`sales/contracts.py` 的 `SOURCE_POS`）回答"应该进销售记录"。用户要的是 **ERPNext 的答案**，而 ERPNext 是**单独记**：
   - `POS Invoice` 是一张**独立单据**（`accounts/doctype/pos_invoice`，`is_submittable: 1`，**有自己的 `naming_series`** —— 独立编号序列、独立列表），**不进 Sales Invoice 列表**；
   - 开班 / 交班各是一张独立单据：`POS Opening Entry`（`autoname: POS-OPE-.YYYY.-.#####`）与 `POS Closing Entry`；
   - **只有交班时并账**：`POS Closing Entry` 校验当班 POS Invoice（同一 profile、未合并、已提交）后，由 `POS Invoice Merge Log` 生成一张 `Sales Invoice`（`sales_invoice.is_pos = 1`），并把 `consolidated_invoice` **回写到每张 POS Invoice** 上（`pos_invoice_merge_log.py:158/364/379`）。
   - 也就是说：**POS 记录是单独的一本（POS Invoice），交班时才合并进销售一侧**；不是"直接写进销售记录"。

**这条对我们意味着**：现状（POS 落 `ERP Sale`、与销售记录分开）在"分开记"这一点上与 ERPNext 一致 —— 不需要把 POS 并进销售记录列表。差的可能是**交班时该不该生成一张汇总的销售单据**（ERPNext 是生成 Sales Invoice；我们没有会计侧）。这条留给用户定，不擅自加。

### F19：收银台照 ERPNext 重做（卡片网格 + 键盘），入口与页面动作对齐（2026-09-23，未部署）

用户连问三次"官方的收银台/进入方式是这样吗"，要求一律按 **ERPNext 本身**对，不是按我们的列表凑。查完的结论与改动：

| 项 | ERPNext（证据） | 改前（我的） | 现在 |
| --- | --- | --- | --- |
| **进入方式** | **侧栏一项**：`workspace_sidebar/selling.json` 里 `label: "POS"`、`link_to: "point-of-sale"`、`link_type: "Page"` | 侧栏「收银 POS」→ 独立 Desk Page | **本来就是对的**（同一机制：独立页面 + 侧栏入口） |
| 进门第一步 | `pos_controller.check_opening_entry()`：拉不到开账单就弹 **Create POS Opening Entry**（POS Profile + 各支付方式期初） | 没开班就弹「开班」（选收银台 + 备用金） | 一致（我们一个"收银台"承担官方 POS Profile 的角色） |
| **商品区** | **卡片网格** `pos_item_selector.js`：库存色点（>10 绿 / ≤0 红 / 其余橙）+ 图片或 `frappe.get_abbr` 缩写 + 名称 + `售价 / 单位` | 原生列表（五行） | **卡片网格**（色点规则照抄，缩写用 `frappe.get_abbr`） |
| **购物车** | `pos_item_cart.js`：行列表 + **合计区**（总件数 / 净额 / 合计）+ **常驻数字键盘**（`1 2 3 数量 / 4 5 6 折扣 / 7 8 9 单价 / . 0 ⌫ 移除`）+ 键盘下「Checkout」 | 原生列表（点行开弹窗改数量） | 行列表 + 合计区 + 同布局键盘 + 键盘下「收款」 |
| 收款 | `pos_payment.js`：收款面板另带一副 3 列键盘（`1 2 3 / 4 5 6 / 7 8 9 / ± 0 ⌫`） | 纯原生弹窗输入 | 弹窗里加了同一副 3 列键盘，直接写"实收"，找零实时算 |
| 页面动作 | `prepare_btns/prepare_menu`：主按钮 **New Invoice**、次按钮 **Recent Orders**（切换"最近的单"显隐）、菜单 **Open Form View** / **Close the POS** | 头部三颗按钮：交班 / 班次记录 / 清空购物车 | 主按钮**「新单」**、次按钮**「当班小票」**（切换那一节显隐）、菜单里放 交班 / 班次记录 / 清空购物车 |
| 关店 | `close_pos()` → 新建 **POS Closing Entry** 并跳到它的表单页 | 自定义「交班」弹窗（汇总 + 实点 + 差额） | 保留我们的弹窗（我们没有那张 DocType），内容与官方同：汇总 + 实点 + 差额标红 |

**没有照搬的两处**（写清楚，不是漏）：官方的 **Open Form View**（把当前单开到 DocType 表单页）—— 产品把 Desk DocType 表单关掉了，没有对应物；官方的**历史单面板**——我们用「当班小票」那一节代替。

**顺带修掉两个真问题**：

1. **`page.set_title_sub()` 在我们页面壳里是空调用**：框架 `Page.$sub_title_area = wrapper.find("h6")`，而产品页面的 h6 数量是 **0**（实测），那句写进去什么都不显示。所以"开班时间"不往 h6 塞补丁，改放进**原生指示器**：`班次 2 · 开班 2026-09-23 03:42 · 应有现金 THB 1,309`。
2. **`p.clear_page` 不清菜单与次按钮**：而 `render()` 每次开班/交班都重跑一遍，不自己清就会一轮一轮往上叠（交班/班次记录/清空购物车越堆越多）。在 `render()` 里补 `page.clear_menu()` + `page.clear_secondary_action()`。

**界面验过**（桌面视口）：头部 = 标题 + 指示器（班次·开班时间·应有现金）+ ⋯菜单 + 当班小票 + 新单；商品区 24 张卡（色点 0 红 / 53、58、79、101、102、126 绿）；点卡片 → 购物车 1 行、合计 THB 45→78 跟着变；键盘 ⌫ 后按 3 → 数量 3、合计 THB 135、总件数 3；点「新单」→ 购物车清空、合计回 0；点「当班小票」→ 那一节展开显示 5 张小票；收款弹窗键盘点 1/5/0 → 实收 150.00、找零 THB 15（应收 135）。

### F20：POS 记录并进销售记录 + 收银记录 / 交班记录两个入口（2026-09-23，未部署）

用户给了 `/Users/skin/pearnly-erp`（ERPNext 那套产品的仓库）说"你读这个看看，真的是这样吗，我记得不是这样啊"，读完确认**我 F18 第四节那段是错的**，并照他的产品改。

#### 一、读 `pearnly-erp` 得到的事实（我错在哪）

| 事实 | 证据 |
| --- | --- |
| ERPNext 的 POS 单据由 `POS Settings.invoice_type` **两选一**：POS Invoice（独立单据）或 **Sales Invoice**（POS 单就是销售发票，用 `is_created_using_pos=1` 区分） | `upstream/erpnext/.../pos_controller.js:133` 读它；`apps/pearnly_business/sample_pos.py:9` 按它决定侧栏指向 |
| **他的产品用的是 Sales Invoice 模式**，所以零售与批发**进同一套原生销售报表** | `scripts/business-sample/test-sales.py:51` 断言 `invoice_type == "Sales Invoice"`；`docs/business-sample.md:21` |
| "收银记录"**跟随模式**：Sales Invoice 模式下按 `is_created_using_pos=1` 筛同一张表，避免混入普通销售表单开的单 | `docs/business-sample.md:23`、`sample_pos.py:15`（`route_options`） |
| 侧栏 = 一个 POS 分组**三个入口**：`POS Counter`（页面 point-of-sale）、`POS Receipts`（跟随模式）、`POS Shift Records`（POS Closing Entry），旁边一个默认折叠的 `POS Management`（POS Profile / POS Settings / POS Opening Records） | `sample_pos.py:9-15、46-83` |
| 收银台界面保持原生，只把原生商品搜索框接上**摄像头扫码**（改成 Barcode 控件），配合 `auto_add_item_to_cart` 扫完直接进车 | `hooks.py: page_js = {"point-of-sale": "public/js/pos_scanner.js"}`、`pos_scanner.js` |

**我 F18 第四节说"ERPNext 是单独记、不进 Sales Invoice 列表"是拿默认模式当成了唯一答案** —— 对他的产品是错的。正确结论：**POS 记录就是销售记录的一种**（他们那种模式），并有独立的"收银记录"入口（同一张表加筛选）。

#### 二、照这个改（本轮做的）

| 改的东西 | 落点 |
| --- | --- |
| **收银小票进销售记录**：`rows()` 现在把 `ERP Sales Document` 与 `ERP Sale` 归并成一列形状（同一套列名、同一套筛选、同一套排序），`source=pos` | `sales/records.py`（新增 `_pos_rows/_pos_shape/_attach_pos_summaries/wanted_sources/_pos_applies`） |
| 「来源」列 + 来源筛选（手工 / 识别 / LINE / 收银）——对齐他们的 `is_created_using_pos` 区分 | `public/desk/sales.js`（列 + 筛选弹窗的 `multi("source", …)`） |
| 点"来源=收银"的行 → 开**小票查看器**（不是销售单详情页）；查看器抽成共享件，收银页 / 收银记录 / 销售记录三处同一个 | `public/desk/transactions.js` 的 `p.trade.receipt_view`；`cashier.js` 改用它 |
| **收银记录**（所有班次的小票，按日期/类型/状态筛）与**交班记录**（各班次汇总 + 差额标红）两个**独立页面 + 侧栏入口**，放在销售系统分组里，顺序对齐他们的 POS Counter / POS Receipts / POS Shift Records | 新页面 `pearnly-pos-receipts`、`pearnly-pos-shifts`（page json + register）、`public/desk/pos-receipts.js`、`pos-shifts.js`、`hooks.py` 清单与导航 |
| 导出跟着走：收银小票也写进「销售记录」表（明细取 `ERP Sale Line`），不再只导销售单 | `sales/output.py`、`cashier/sales.py` 的 `pos_export_lines` |

**没并表**（按上一轮说好的）：收银小票仍在 `ERP Sale` 里单独存 —— 这一轮只是**读**的时候合成一份；真要像 ERPNext 那样只用一张单，是单独一步（要迁历史）。

#### 三、验过的

| 步骤 | 结果 |
| --- | --- |
| 服务端取数 | 全部 **29** 条（23 销售 + **6 收银**）；`source=["pos"]` → 6 条且全为 pos；`source=["manual"]` → 16 条且全为 manual；收银行带 `system_no=SI2569-000026 / receipt / submitted / paid / 135.0 / 摘要"ปลากระป๋อง ตราสามแม่ครัว ×3.0"` |
| 销售记录页 | 表头多出「来源」列；首屏 6 行收银（`SI2569-000026 / 025 / 024 / R20260923-0003 / 0002 / 0001`）都显示「收银」，其余显示「手工」等；计数 **20 / 29** |
| 筛选弹窗 | 字段里出现「来源」（类型 / 期间 / 日期 / 开始 / 结束 / 单据类型 / 收款状态 / **来源**） |
| 点收银行的行 | 开小票查看器：标题 `SI2569-000026`、状态 已完成、明细 1 行 `ปลากระป๋อง ตราสามแม่ครัว … 3 … THB 45`、应收 THB 135（只读，没有列表里那排"添加行/删除/上传"） |
| 「收银记录」页 | 6 / 6 条：单据编号 / 日期 / 类型 / 状态 / 应收 / 实收 / 班次编号；点行同样开小票查看器（有权限时带 退货退款 / 作废） |
| 「交班记录」页 | 2 个班次；已交班那行 `应有 1,136 / 实点 1,130 / 差额 **THB -6**`（红）；点行弹汇总：收银台、收银员、笔数 2、退款 0、找零 4、各收款方式、应点/实点、差额 -6（红） |
| 侧栏 | 销售系统下依次：销售记录 / 收银 POS / **收银记录** / **交班记录** / 客户 |
| 离线检查 | 架构 0 违规、术语表 PASS（erp 753 条）、权限回归 102 条 OK、`check-i18n --strict` 与基线一致（18 / 113） |

### F21：列表勾选框按官方默认打开；收银记录 / 交班记录 / LINE 待处理补上批量动作（2026-09-23，未部署）

用户："我不明白那个列表都不加单选多选，你打开容器自己研究吧"、"你自己打开那个容器（`http://127.0.0.1:8084/desk/point-of-sale`），看看别人的状态"。

#### 一、先把"官方"看清楚（8084 那台 = `pearnly-erp` 自己跑出来的示例站）

环境：容器组 `pearnly-ci-business-sample`，站点 `business.localhost`，`http://127.0.0.1:8084`；Frappe **16.33.0** / ERPNext **16.34.1**；登录 `owner@sample.localhost`（示例密码），站点单值 `POS Settings.invoice_type = Sales Invoice`。

| 官方看到的事实 | 证据 |
| --- | --- |
| 侧栏 = 销售 → **POS 收银** 组：收银台 / 收银记录 / 交班记录，另有一个默认折叠的 **POS 管理** | 截图（8084 收银台） |
| 收银台 = 商品**卡片网格**（库存色点 + 缩写 + 名称 + `售价 / 单位`）+ 右栏购物车（商品/数量/金额 → 总数量/净总计/总计 → 结账）；页面动作 = `…` 菜单 + 次按钮「最近订单」+ 主按钮「新发票」 | 截图 |
| **点购物车行** → 左侧「物料详细信息」面板（数量 / 单位 / 单价 / 单位换算系数 / 折扣率(%) / 仓库 / 数量（仓库）/ 标价），右侧**这时才出现数字键盘**（5 列 `1 2 3 数量 / 4 5 6 折扣 / 7 8 9 单价 / . 0 删除 移除` + 底部「结账」） | 截图 + DOM：`.point-of-sale-app > .items-selector / .item-details-container / .customer-cart-container / .payment-container / .past-order-list / .past-order-summary`；`.number-pad` 只在选中行后才可见 |
| 「收银记录」= **同一张销售发票表加预设筛选**：`/desk/sales-invoice/view/list?is_created_using_pos=1` | 地址栏 |
| **官方列表每行天生有勾选框**，表头天生有全选 | 截图；源码 `apps/frappe/frappe/public/js/frappe/list/list_view.js`：`get_subject_element()` **无条件** append 复选框、`get_header_html()` **无条件**给全选框；`grep -rn selectable apps/frappe` 只命中 `calendar.js`，**框架里没有"能否选择"的开关** |
| 全选 20 行后：表头变「已选 20 条记录」，右上角出现「**操作 ▾**」下拉 = 销售出库 / 付款 / 复制到剪贴板 / 编辑 / 导出 / 执行人 / 清除分配 / 应用分配规则 / 添加标签 / 打印 / 提交 / 取消 / 删除 | 截图 |

**回答用户的疑问：官方有，而且是默认就有**；是我们共享列表层把它做成 `selectable` 开关、默认关掉，新建的页面没传就没了勾选框。

#### 二、改前在我们自己站点逐页量到的实情（8147 / `erp-pos-a.localhost`）

`.list-row-checkbox` 个数 / 行数 —— 12 个列表页本来就有（物料 20/20、库存 20/20、入库 20/20、出库 15/15、收发存 20/20、盘点 10/10、采购记录 20/20、供应商 36/36、销售记录 20/20、客户 7/7 …），**只有三个地方没有**：本轮新加的「收银记录」0/6、「交班记录」0/2，以及两个 **LINE 待处理**列表（`purchase-intake.js` / `sales-intake.js` 的 `p.list` 一个 selection 参数都没传）。

#### 三、改了什么

| 改动 | 落点与依据 |
| --- | --- |
| 共享列表层**默认打开**勾选框：`selectable: options.selectable !== false`，只有明确不要选择的列表才传 `false` | `pearnly_core/public/desk/list.js`。依据就是上面那条：官方那份渲染器没有开关，每行都有 |
| **收银记录**：勾选 → 右上角「作废选中的 N 张」（`p.selection_action` + 既有 `api_void_sale`）；只对"已完成"可用（否则按钮禁用、原因写在 title 上），确认框 + 提示 + 音效 | `public/desk/pos-receipts.js`（词条复用既有的 `trade.action.voidCount / voidConfirmCount / voidNotice / voidBlocked`，与入库/出库列表同一套） |
| **交班记录**：勾选 → 「导出选中的 N 个班次」 | 新 `cashier/output.py` + `cashier/api.py:api_shifts_export`；取数给 `shifts.list_shifts` 加 `names=` 参数（权限口径不变：非管理员只看自己的班），xlsx 走框架自带 `frappe.utils.xlsxutils.build_xlsx_response`；前端 `public/desk/pos-shifts.js` |
| **LINE 待处理**（采购、销售两个入口）：加勾选框 + 「取消选中的 N 条」，一次填一个原因批量丢掉 | `public/desk/purchase-intake.js`、`sales-intake.js`。为此把取数从"进页面之前取一次"挪进 `load` —— 否则批量完 `reload()` 拿到的还是那一份旧行；批量这条没用 `p.selection_action`（它那口子只有 yes/no，收不到原因），沿用行内「取消」同一个原因弹窗 |
| 表单里的**附件行**明确 `selectable: false` | `sales-form.js` / `purchase-form.js`：那是表单里的行（每行自带预览/删除），不是可批量操作的集合列表 |
| 术语表 +4 条 | 带 key 的走产品 `t()`（`trade.action.exportCount` / `trade.exportNotice`）；LINE 那页用的是框架 `__()`，所以 `Cancel {0}` / `Cancelled {0}` **不带 key**（英文原文当 msgid） |

**选中之后都有真动作**，不是只长一个勾选框：收银记录 → 作废；交班记录 → 导出；LINE 待处理 → 取消。官方把这些放在「操作 ▾」下拉里，我们沿用产品既有口径（同一套 `p.selection_action`，右上角主按钮）。

#### 四、验过的

| 步骤 | 结果 |
| --- | --- |
| 改后逐页复量（13 个列表页） | 除收银 POS（0 行）外，**每一页的勾选框数 = 行数**；收银记录 6/6、交班记录 2/2、盘点 10/10 都在；原来有勾选框的 12 页一个没少 |
| 收银记录：表头全选 | 6 行全勾、表头计数 `6 / 6`、右上角出现「**作废选中的 6 张**」 |
| 收银记录：点那颗按钮 | 原生确认框「作废选中的 6 张单据并冲销对应库存变动？」（否 / 是），点「否」后**选择保留 6 行、按钮可点**；全程没有作废（回读 `ERP Sale` 仍是 6 条 `completed`） |
| 交班记录：全选 → 点按钮 | 按钮「**导出选中的 2 个班次**」；点下去开新标签 `…api_shifts_export?names=["SHIFT-2026-00492","SHIFT-2026-00486"]` |
| 导出内容（宿主 curl 带会话取，再用 bench 里的 openpyxl 读） | `HTTP 200 / application/octet-stream / 5375 字节`；表 `Shift Records` A1:J3，两行：`SHIFT-2026-00492 … 应有 1309 / 实点 1309 / 差额 0`、`SHIFT-2026-00486 … 应有 1136 / 实点 1130 / 差额 -6` |
| LINE 待处理（销售向）：造 2 条草稿 → 进页面 | 勾选框出现（改前没有）；全选后按钮「**取消选中的 2 条**」 |
| LINE 待处理：点按钮 → 原因弹窗 → 确认 | 弹原生「取消」框（取消原因），填原因提交后：提示条「**已取消 2 条**」、列表刷新成「无数据」；回读两条 `status=discarded`、`discard_reason="自测：批量取消校验"` |
| 销售单详情（`SD-00204`，带 1 个附件） | 附件区 1 行、`.list-row-checkbox` **0** 个 —— 附件行确实没有勾选框；整页勾选框 0 |
| 夹具自清 | 见下面第六节：2 条测试草稿 + 它们的 4 个附件文件已删；回读演示站 `ERP LINE Intake` 9 条（全是采购向已确认）、intake 附件文件 18 个，与开工一致 |
| 离线检查 | `check-architecture.py` `PASS (0 violations)`；`i18n-build.py --check` `RESULT: PASS`（erp **757** 条，含本轮 +4）；`tests/test_erp_portal_access.py` 102 条 OK（6 跳过）；`check-i18n.py` 合计 **113**（与基线逐项相同，未新增未登记文案）；7 个改动过的 JS 全过 `node --check`；3 个改动的 py 过 `py_compile` |

#### 五、没照搬的与已知限制

- **没有做官方的「操作 ▾」下拉**：产品 13 个列表统一用右上角主按钮（`p.selection_action`），本轮只补"勾选框 + 动作"，不动这个交互骨架。
- **导出表头中英混排**（`Shift No / Terminal / 收银 / 状态 / Opened At / …`）：`_()` 只认"英文原文当 msgid"的词条，而 `Shift No`、`Terminal` 这些在术语表里是**带 key** 的行（走产品自己的 `t()`）。**这不是本次引入的** —— 销售记录导出同样是这个形态（实测表头 `编号 / 状态 / Document Kind / 来源 / System number / 日期 / Customer / 客户名 / …`）。要统一得动整个术语表口径，不在本轮范围。
- **没有写权限的人在收银记录页勾选不会有动作**（官方也是勾选框常在、动作按权限给）。
- **交班记录没有可批量做的写动作**（没有作废/改班次的业务），所以选中给的是导出。

#### 六、演示站副作用

- 为验 LINE 批量取消，用服务路径 `intake.receive()` 造了 2 条 **sales 向**草稿 `LI-00501 / LI-00502`（发送人 = 登录用户），在界面上批量取消掉，然后把这两条连同它们的 **4 个附件文件**一起删除；回读 = 9 条草稿（全是采购向 `confirmed`）+ intake 附件文件 18 个，与开工时一致。
- 收银记录的批量作废**只点到确认框就选了「否」**；回读 `ERP Sale` 6 条全部 `completed`、`ERP Sales Document` 23 条、`SI2569-%` 流水 34 条，与改前一致。
- 演示站收银台现在开着的是**班次 3**（`SHIFT-2026-00503`，开班 04:43、备用金 1,000），班次 2 在 **04:39** 已交班（实点 1309 = 应有 1309、差额 0）。**这两步不是本窗口做的**（本窗口在收银页只点了批量作废的确认框后取消，没点过开班/交班）；按既有记录保留，不还原。

### F22：收银台与交接班界面照 ERPNext **1:1** 重做（2026-09-23，未部署）

用户口径："pos交接班和pos收银台界面请按100%比例做出，可以关闭一些我们没有的功能，位置尺寸动态交互逻辑必须100%，不要自己画，看清楚摸清楚。"

#### 一、先把官方的"位置尺寸与交互"摸清（离线快照 + 8084 实测）

- 源码快照（只读，从 `pearnly-ci-business-sample-bench-1` 的 `apps/erpnext` 复制出来读）：
  `selling/page/point_of_sale/{pos_controller,pos_item_selector,pos_item_cart,pos_item_details,pos_number_pad,pos_payment,pos_past_order_list,pos_past_order_summary}.js`、
  `public/scss/point-of-sale.scss`（1438 行）。
- 版本：**Frappe 16.33.0 / ERPNext 16.34.1**，与本实验室容器**同一版**（`apps/erpnext` 在本 bench 里就有），所以样式变量（`--margin-md` 等）与官方逐字一致。
- 8084 实测（`http://127.0.0.1:8084/desk/point-of-sale`，固定视口 1440×900，逐块量 `getBoundingClientRect`）：

| 量到的 | 数值（官方，1440 视口） |
| --- | --- |
| `.point-of-sale-app` | x=220 y=49 w=1220 h=844.4；`grid-template-columns: repeat(10, 1fr)`；gap 15px；padding 1%（12.1953px） |
| 每块尺寸 | 商品区 span 6 → w=711.4；购物车 span 4 → w=469.2；明细/收款 span 6；最近订单 span 4；汇总 span 6 |
| 段高 | `calc(100vh - 5rem)` = 820（min-height 30rem） |
| 商品卡 | w=152.8 h=194 = 图/缩写区 8rem(128) + `.item-detail` 3.5rem(56)，图区左右各留 var(--margin-sm) |
| 商品网格 | `repeat(4, 1fr)`，gap/padding 都 var(--margin-lg)=20px |
| 筛选区 | 12 列：标签 span 4(223.8) / 搜索 span 5(259.7) / 物料分组 span 3(167.8) |
| 明细面板 | 与商品区同一格 (711.4×820)；`.form-container` 两列各 328.18、gap 15；`.item-display` 高 176（= 缩写框 11rem） |
| 购物车行 | h=46；`.rate-amount-header` 与 `.item-rate-amount` 同宽（JS 按内容取最大宽度写死） |
| 键盘 | 购物车那副 5 列（目标键 `col-span-2`，按钮 h=51）；收款那副 3 列（58.6） |
| 价格面板 | 左右各 50%；`.number-pad` 绝对定位在右侧贴底；`.totals-section` 高 81；`.submit-order-btn` 高 38 |

**交互逻辑（官方源码逐条读出来，不是猜的）**：

| 动作 | 官方行为 | 依据 |
| --- | --- | --- |
| 点商品卡 | 加 1 件进车（不展开明细） | `pos_controller.on_cart_update` |
| 点购物车某行 | 左半边**换成明细面板**、右半边**这时才出现键盘**；再点同一行 = 收起 | `pos_item_details.toggle_item_details_section`、`pos_item_cart` 的 `numpad_section_hidden` 判断 |
| 键盘目标键 | 先按「数量/折扣/单价」选目标（该键加 `highlighted-numpad-btn`），再按数字；没选目标就按数字 → 红提示 + error 音 | `pos_item_cart.on_numpad_event` |
| 「删除」/「移除」 | 删除 = 退一格；移除 = 整行删掉 | 同上 |
| 结账 | 购物车**留在左四列**、收款面板进右六列（网格自动排：DOM 里购物车在收款之前） | 8084 实测 x：购物车 958.6→ 收款 716.4 |
| 收款方式 | 点哪个方式才展开它的金额格（`.border-primary` + 显示 control）；键盘写它 | `pos_payment.on_payment_mode_click` |
| 收款合计 | 总计 / 付款金额 / 剩余金额；**多付时标签变「找零」**、变绿 | `pos_payment.update_totals_section` |
| 收款完成 | 藏起收银台，右半边显示这张单的汇总 + 绿色提示 | `pos_controller.submit_invoice` → `toggle_components(false)` + `toggle_submitted_invoice_summary(true)` |
| 最近订单 | 次按钮切换；订单列表在左四列、汇总在右六列；按钮文案变「隐藏最近订单」 | `pos_controller.toggle_recent_order` + 8084 实测 |
| 开班 | 无开账单时弹「Create POS Opening Entry」：POS Profile + **期初余额表**（支付方式 × 期初金额） | `pos_controller.create_opening_voucher` |
| 交班 | 「Close the POS」菜单项 → 一张 POS Closing Entry，**支付方式对账表**：期初 / 应有 / 实点 / 差额（实点填完自动算差额） | `pos_closing_entry.json` + `pos_closing_entry.js` 的 `closing_amount` 触发器 |

#### 二、改成什么样

| 项 | 落点 |
| --- | --- |
| 页面结构换成官方那六块（类名、层级、显示规则一致） | `public/desk/cashier.js` 重写：`.point-of-sale-app > items-selector / item-details-container / customer-cart-container / payment-container / past-order-list / past-order-summary` |
| 皮：数值照官方样式表逐条搬（行尾标官方行号），只落我们真正渲染的规则 —— **没有整份拷 ERPNext 的 SCSS**（本站不装它，也不该把别家产品的样式表搬进本仓） | `public/desk/cashier.css` 重写 |
| 购物车键盘只在选中行时出现（原先常驻）；目标键高亮；删除/移除语义对齐 | `cashier.js` 的 `cart_numpad` / `paint_cart` / `paint_views` |
| 收款从「弹窗」改成**官方那套收款面板**（支付方式 + 3 列键盘 + 总计/付款金额/剩余金额 + 完成订单）；收款时购物车留左边 | `cashier.js` 的 `checkout/paint_payment/pay_numpad/submit_order` |
| 「当班小票」从一段列表升级成官方的**最近订单 + 汇总**两块（点订单在右边出汇总；有权限时给「退货退款 / 作废」） | `paint_orders/show_summary` |
| 页面动作对齐官方：主「新单」、次「最近订单 / 隐藏最近订单」、菜单「交班」（官方还有 Open Form View，产品不开放 DocType 表单页，关掉） | `paint_actions` |
| **班次对账表（按支付方式）**：新子表 `ERP Shift Balance`（支付方式/期初/应有/实点/差额，`istable`），`ERP Shift` 加 `balances` | 新 DocType + `doctype/erp_shift/erp_shift.json` |
| 开班/交班服务端：期初按方式记一笔（现金那份同时写回父字段 `opening_float`）；应有 = 期初 + 该方式净额（现金再减找零）；交班收 `counted` 逐方式算差额，父字段仍按现金那份写 | `cashier/shifts.py`（`_clean_amounts` / `_balance_rows` / `open_shift` / `summary` / `close_shift`）、`cashier/api.py` 收 JSON 参数 |
| 老班次（本轮之前开的，没有子表）读的时候按"只有现金一笔备用金"合出来，旧数据不会变空白 | `_balance_rows` 的回退分支（实测：检查站上老班次返回一行现金 0/0/0） |
| 术语表 +20 条（所有物料 / 物料详细信息 / 数量（仓库）/ 标价 / 期初·应有·实点金额 / 完成订单 / 剩余金额 / 最近订单 / 隐藏最近订单 / 小票状态 / 支付方式对账 / 回到购物车 …） | `i18n/messages.tsv` + 生成包 |

**明确关掉的官方功能（产品没有对应数据，位置留着不画）**：客户选择与客户信息、物料分组筛选（我们只有 `category_id` 这个裸整数，没有分组主数据）、单位换算系数、行号/批次、忠诚度积分、发票附加信息、打印/邮件小票、订单级折扣（`add-discount-wrapper` 没画 —— 画一颗点不动的按钮就是之前那种"展示作用"的坑）、Open Form View。

#### 三、验过的

| 步骤 | 结果 |
| --- | --- |
| 版式比例 | 我们的页面（1159 视口）：app w=1109、10 列各 95.18、gap 15、padding 1%（11.086）；商品区 w=646.1（=6×95.18+5×15）、购物车 w=425.7（=4×95.18+3×15）、段高 = 视口−5rem —— 与官方同一套公式（官方 1440：1220 / 106.055 / 711.4 / 469.2） |
| 商品卡 | w=136.5 h=194（图区 128 + 明细 56）—— 与官方同结构（官方 152.8/194） |
| 选中购物车行 | 商品区 `display:none`、明细面板 `display:flex` 占同一格；`.item-display` 176；`.form-container` 两列、7 个原生控件带标签（数量/单位/单价/折扣金额/仓库/数量（仓库）/标价）；键盘 `display:flex`、5 列、目标键 `col-span-2`、按钮顺序 `1 2 3 数量 / 4 5 6 折扣金额 / 7 8 9 单价 / . 0 删除 移除 / 收银开单`；购物车合计区同时隐藏（官方同款切换） |
| 键盘改数量 | 点「数量」→ 该键高亮；按 3 → 明细里的数量 3.000、行显示「3 件」、键盘上方合计「总件数: 3 净额: THB 30 应收金额: THB 30」全部实时跟着变 |
| 结账 | 收款面板进右六列（x=501.8）、购物车留最左（x=61.1）—— **官方那种左右调换**；四种方式各一行，只有被点中的展开金额格；3 列键盘；合计行「应收金额 THB 30 / 实收金额 THB 30 / 剩余金额 THB 0」 |
| 收款键盘 | 按 1、5 → 现金格 15.00、方式行显示 THB 15、合计行标签变**「找零 THB 5」**（绿） |
| 完成订单（真落单） | 提示「小票 SI2569-000028 · 找零 THB 0」；页面换成这张小票的汇总（小票号/时间/收银员/状态 + 物料明细 + 应收金额 + 按收款方式 + 退货退款/作废）；服务端回读：`SALE-2026-00507 · SI2569-000028 · completed · 78/78/0 · 班次 SHIFT-2026-00503`；流水 `SI2569-000028 · PRD-00224 · 2 · sale_out · WH-00003`；结存 53 → **51** |
| 最近订单 | 次按钮点了变「隐藏最近订单」；订单列表在左四列（小票状态 + 按小票编号搜索 + 行：类型/小票号/收银员/时间/金额/状态）、汇总在右六列；购物车隐藏 |
| 交班弹窗 | 期初/应有/实点/差额四列表格，四行（现金/银行卡/PromptPay/转账）；老班次回读正确：现金 `期初 THB 1,000.00 / 应有 THB 1,010.00 / 实点 THB 1,010.00 / 差额 THB 0.00`；金额显示 THB（不是 `$`）；单元格可编辑 |
| 交互影响面 | 菜单里只剩「交班」（官方那两条里 Open Form View 关掉）；无班次时主按钮＝「开班」、指示器＝「尚未开班」（橙） |
| **交班写路径（隔离检查站 `erp-pos-b`，不在演示站动用户的班）** | 开班 `balances={cash:300, card:100}` → 落一笔现金 50 + 刷卡 20 的单 → 汇总：现金应有 **350**（300+50，无找零）、刷卡应有 **120** → `close_shift(counted={cash:345, card:120})` → 差额现金 **−5**、刷卡 **0**；父字段 `expected_cash 350 / counted_cash 345 / cash_diff −5`；子表四行落库与算出来的一致 |
| 离线检查 | `check-architecture.py` PASS(0)、`i18n-build.py --check` PASS（erp 777 条）、`tests/test_erp_portal_access.py` 102 条 OK（6 跳过）、`check-i18n.py` 合计 **113**（与基线逐项相同）、两个站点 `bench migrate` 退出码 0（新表 `tabERP Shift Balance` 建好、`ERP Shift.balances` 字段就位） |

#### 四、没验到的（如实记）

- **开班弹窗没在浏览器里点开过**：登录用户（`purchase-preview@example.test`）此刻就有一个开着的班（班次 3，04:43 开的），而开班弹窗只在"本人没有开着的班"时才出现；要在这个班上验就得先把用户的班交掉。**没有替他做这个决定**。它的字段与对账表跟已验过的交班弹窗是**同一段代码**（同一个 `balance_table()`、同一个 `p.dialog`、同一套币种处理），差别只是少三列、多一个收银台字段。
- **窄屏（≤620px）没在真机/窄视口量过**：官方那几条窄屏规则照搬了（1 列、商品两列、收款键盘隐藏、汇总 100%），但没有像桌面这样逐块对过。
- **官方的 1200px 断点在我们这儿也不生效**，原因是官方自己那条 `.show-item-image` 规则多一个类、优先级更高（实测：视口 1159、`matchMedia('(max-width:1200px)')` 为 true，网格仍是 4 列）—— 这一条我们与官方**表现相同**，不是漏搬。

### F23：导出件术语口径统一 + 租户员工与权限页（2026-09-23，未部署）

用户口径："导出的报表 术语表口径要统一，做租户的添加用户和权限和管理复用Frappe/ERPNext，功能的实现只用我们自己的，不做复杂的配置，只添加员工，基础信息，邮箱，初始用户和密码，勾选功能权限，把以上做完闭环。"

#### 一、导出件里为什么是英文（根因）

- 同一句话有三处入口：界面按 **key** 取词（`t("trade.docNo")`），服务端 `_()` 与 DocType 字段标签认的是**英文原文**。术语表里带 key 的词条原先**只按 key 落进语言包**，于是界面是中文、导出件里还是英文。
- 生成器补一条：`scripts/i18n-build.py` 的 `_aliases()` —— 把"没有同英文的无 key 行、且同英文各条译文一致"的带 key 词条，按英文原文再落一行；两种情况**不补**（已有同英文的无 key 行、同英文各条译文不一致），理由写在文件头。
- 服务端补一条按 key 取词的口子：`pearnly_core/browser_text.py` 的 `label(key, lang="")`（取词顺序 当前语言 → 英文 → key），`inventory/api.py` 的模板表头改走它（原先本地又写了一遍 `label_of`）；`cashier/output.py` 的交班表头从英文原文改成 10 个 `trade.*` 键。
- 导出表头来自 DocType 标签与术语表，所以这一轮把标签也归一到已有词条：`Document Kind → Document Type`、`Discount % → Discount (%)`、`Opening Float → Opening Cash`、`Method → Payment Method`，术语表补 `Source File / File Fingerprint / Cancelled At / No / Discount (%) / Line Total / Original Line`。

#### 二、同一件东西两个名（本轮又找出两处，已改）

| 问题 | 改法 |
| --- | --- |
| 采购列表把系统号叫「编号」（`__("ID")`），而销售/采购表单和收发存都叫「单据编号」；采购导出里于是出现两列都叫「编号」（内部名 + 系统号） | 两个列表的首列标签由 `ID` 改成 `System number`（销售列表本来就是同一列，一并统一）；采购 DocType 的 `system_no` 标签由 `ID` 改成 `System number` |
| 销售导出里「客户」（链接到客户主档）与「客户名称」（手填的名字）两列都叫「客户名称」 | 术语表 `Customer` 的译文改成「客户 / ลูกค้า / 顧客」（与 ERPNext 一致：Customer=客户、Customer Name=客户名称）；客户主档的名字字段标签由 `Customer` 改成 `Customer Name`，所以它在界面上仍叫「客户名称」，用户看不到变化 |

#### 三、员工与权限页（复用框架，功能自己写）

- 账号就是框架的 `User`，权限就是框架的 `Role`；**不另存一份"谁有什么权限"**（那必然和框架判据走偏）。
- 能勾的岗位是服务端 `team.role_catalog()` 给的 —— 本站点真实存在的功能角色，也就是这个租户买到的功能；勾完之后员工看到哪些菜单由框架的角色判据决定，页面不自己算。
- 界面上只有三件事（用户口径"不做复杂的配置"）：添加员工（姓名 / 邮箱＝登录名 / 初始密码 / 勾岗位）、改员工（姓名 / 岗位 + 重置密码 + 停用·启用）、名单（姓名 / 邮箱 / 岗位 / 状态 / 最后登录 + 批量停用·启用）。
- 都用原生件：岗位是框架的 `MultiCheck`、密码是原生 `Password` 控件、名单是 page-level 列表；页面文档在 `pearnly_core`（路由要各产品都指得过来），渲染在 `pearnly_erp_portal`（与收银台同一套注册写法）。
- 真租户站（站点配了身份服务）建号会**先建身份服务账号并绑定**，建不出来整件事失败；只有在没有身份服务的站点才写本地密码。判据：`team.service._identity_configured()` / `_local_password_allowed()`。
- 顺手修掉一个真 bug：`set_member_roles` 原先给 `user.roles` 赋 `[{...}]`，Frappe 16 落库时炸（`_dict has no attribute is_new`）；改成只动子表 + 最后 `save(ignore_permissions=True)`。

#### 四、员工名单只画一行（根因，与上一窗口的猜测不同）

- 现象：8 个人只画出 1 行，表头计数 `1 / 8`，而同一时刻接口返回 8 人。
- 根因：框架 `frappe/public/js/frappe/list/base_list.js` 的 `prepare_data()` 末尾有一句
  `this.data = this.data.uniqBy((d) => d.name)`。我们的员工行里**没有 `name` 字段**，
  8 行被当成同一行合并成 1 行（其它列表页的行都带 `name`，所以只有这一页中招）。
- 改法：`team.service.list_members()` 给每行加 `name`（值就是邮箱，与框架里 `User.name` 同一个值，勾选与打开行也按它找行）；团队页里上一窗口留下的三个临时探针（`window.__team_debug` / `__team_loads` / `__team_last`）已删除，不留在交付里。

#### 五、验过的

| 步骤 | 结果 |
| --- | --- |
| 销售记录导出（带会话真登录取 xlsx，bench 里 openpyxl 读回） | 主表 29 列表头**全中文**：编号 / 状态 / 单据类型 / 来源 / 单据编号 / 日期 / **客户 / 客户名称** / 客户类型 / 地址 / 计算增值税 / 增值税率 / … / 收款备注；**没有重复表头**；明细表 `折扣率 (%) / 明细小计`；附件表 `类型 / 文件名` |
| 采购记录导出 | 48 列全中文；前两列由 `编号 / 编号` 变成 **`编号 / 单据编号`**；物料 / 付款 / 附件三张表全中文 |
| 交班记录导出 | 10 列全中文（班次编号 / 收银台 / 收银员 / 状态 / 开班时间 / 交班时间 / 备用现金 / 应有现金 / 实点现金 / 差额） |
| 库存导出（CSV） | 表头 物料名称 / 编码 / 条码（二维码）/ 仓库 / 仓位 / 单位 / 在库 / 平均成本 / 金额 / 状态 |
| 员工页（演示站 8147） | 名单 **8 行 / 8 个勾选框 / 计数 `8 / 8`**（改前 1 行、`1 / 8`）；列＝姓名 / 邮箱 / 功能权限 / 状态 / 最后登录，状态列「正常 / 已停用」 |
| 添加员工（浏览器里真走一遍） | 弹窗字段＝姓名 / 邮箱 / 初始密码（带眼睛）/ 功能权限（原生 MultiCheck：采购 库存 商品 收银 销售）；填 `selftest-browser@example.test` 勾「收银」提交 → 提示「已添加 自测临时员工」、音效播到 882ms、名单变 9 行 |
| 改员工 | 改名 + 改岗（收银→销售）→ 提示「已保存」、行内变「销售」；「重置密码」→ 提示「密码已更新」；「停用」→ 该行「已停用」标红；再「启用」→ 回到「正常」 |
| 新员工真能登录吗（服务端证据） | 停用状态下 `POST /api/method/login` **401**（`User disabled or missing`）；启用后用新密码 `login` **200**（`full_name`＝自测临时员工二）；持会话调 `api_list_members` **403**（岗位是销售，不是管理员）、`api_sales_records` **200**（销售岗可读；`total 0` 是因为销售岗只看到本人的单据，同一时刻老板账号读同一接口 `total 31`） |
| 页面文案 | 销售列表首列「单据编号」、采购列表首列「单据编号」；销售新建弹窗「单据编号 / **客户** / 客户名称 / 客户类型」；客户列表仍是「客户名称 / 客户类型 / 税号 / 电话 / 地址 / 状态」 |
| 离线检查 | `check-architecture.py` PASS(0)；`i18n-build.py --check` PASS（erp 809 条）；`tests/test_erp_portal_access.py` 102 条 OK（6 跳过）；`check-i18n.py --strict` 合计 **113**（py-bare-cjk 18，与基线逐项相同）；改动的 JS `node --check` OK、改动的 py `py_compile` OK、两个 DocType JSON 可解析；两个站点 `bench migrate` 退出码 0 |
| 隔离检查站 `erp-pos-b` | 销售探针、主数据探针均 `problems: {}`；数据流探针 `result: PASS`（19 项） |

#### 六、没验到 / 留给用户定的

- **采购导出里还有三对同名列**：`净总计 / 折扣金额 / 总计` 各出现两次 —— 来自「手动设置金额」那组字段（`override_*`：表单里勾上手动设置才出现，导出里一直有）。要区分有两条路：给这三列加「（手动）」后缀，或者导出里不带这三列。这属于产品措辞，**没有替你定**。
- **真租户站（带身份服务）的建号 / 停用没实测**：本地站点没有身份服务，走的是本地密码分支；身份服务的停用联动、邮件邀请都没做。
- `Calculation Version` 仍然是英文：它是 `purchase_document` 的隐藏内部字段（`hidden=1`），表单不渲染、导出显式排除它，所以没有补词条。
- 员工页的窄屏（≤620px）与真机没量过；岗位勾选只验了「收银→销售」这一次改动。

### F24：团队管理从侧栏移到左下角那颗用户按钮里（2026-09-23，未部署）

用户口径（两轮，第二轮是对第一轮做法的纠正）：

1. "不要主数据，把团队管理放在左下角里面，列入，我点左下角，左侧出现 1.个人信息 2.团队管理"；
2. "我的意思菜单放在左侧侧栏，点左下角，左侧菜单就只有个人信息和团队管理，**不是挂在左下角**"。

第一轮我做成了左下角的浮层菜单（`frappe.ui.create_menu`），用户当场否掉；现在是**点左下角那颗按钮 → 左侧那栏的条目整个换成这两项**。

#### 一、改了什么

| 层 | 落点 | 说明 |
| --- | --- | --- |
| 声明 | `pearnly_erp_portal/hooks.py` | 「团队管理」从 `nav.group.master` 拿掉（主数据组因此没有成员，侧栏整组不出现）；新增声明键 `user_menu: ['pearnly-team']`（写**路由**，词与图标仍从 `pages` 取，不复制一份） |
| 服务端 | `pearnly_core/desk/navigation.py` | `_user_menu_routes()` / `sidebar_pages()`（= 能打开的页面去掉走左下角的那些）/ `user_menu()`（按与侧栏同一判据筛：页面已实现 + 当前用户能进）；`boot.pearnly_user_menu`；`_sidebar_items()` 与 boot 投影都按 `sidebar_pages` 出条目 |
| 外壳 | `pearnly_core/public/desk/product.js` | 接管那颗按钮（原生是 `<a onclick="route_to_user()">`）：把**侧栏条目区**换成「个人信息 + 产品声明的项」，用框架自己的 `sidebar_item` 模板渲染。**左侧菜单跟着当前页走**：「个人信息 / 团队管理」算一个账号区，停在里面就保持这两项（点菜单里的另一项也不例外），回到业务页面（首页、销售记录…）才用原生 `make_sidebar()` 画回业务菜单；点左下角时右侧默认落在个人信息，再点一次画回业务菜单（右侧不动） |

第二轮修正（用户看到第一版后当场指出"点了其他菜单，左侧菜单发生了变化，你这逻辑不对吧"）：账号区里的两项互相跳转时不再把菜单画回业务那份 —— 只有离开账号区才画回去。
| 术语 | `pearnly_core/i18n/messages.tsv` | `My Profile` → 个人信息 / ข้อมูลส่วนตัว / 個人プロフィール |
| ADR | `docs/adr/0009-product-owned-declarations.md` | 声明表加一行：产品可以在 `user_menu` 里声明"放左下角那颗按钮、不放侧栏"的入口 |

#### 二、顺手钉住的三处（都是这次改动暴露出来的真脆弱点）

1. **被移出侧栏的页面会让侧栏整条换掉**：框架按"这个页面属于哪个模块 / 哪个工作区"决定侧栏，判据是"当前侧栏的条目里有没有这个页面"。把「团队管理」移出条目之后，点进去左侧会变成一份二十来项的原生平铺清单（上面还带着 `团队管理`）。做法：`Sidebar.setup()` 的目标钉成产品自己那一份 —— 模块/工作区侧栏留给没有外壳的裸 Desk（实测：点「团队管理」后侧栏仍是 `Pearnly ERP`）。
2. **侧栏文档里的旧分组字段会让整条侧栏崩成一项**：页面移出分组后，站点里那份 Workspace Sidebar 文档还留着它 `child=1/indent=1` 的旧字段；原生渲染认这两个字段，一个"带 child 却没有分组父项"的条目会让 `sidebar_item.make()` 读 `parent.indent` 抛错 —— 界面看上去像"菜单全丢了（只剩第一项）"。做法：boot 投影里平铺项一律重写 `child/indent`。另外 hooks 有缓存：**先 `clear-cache` 再跑 `ensure_navigation`**，顺序反了会把旧声明写回文档（这次踩到了）。
3. **「侧栏排序」保存一直是坏的（改之前就坏）**：编辑器只提交顶层项（分组标题没有 `link_to`、子项在 `nested_items` 里），服务端拿到 `[首页, null, null, null]` 只会回"值无效"；而且保存成功后 `make_sidebar()` 不做嵌套那一步，每组的子项会被画两遍。做法：提交前铺平成路由；保存后先 `prepare()` 再 `make_sidebar()`；服务端校验与返回值跟着改成"校验侧栏那一份、另回一份能打开的页面"。实测保存与放弃两条路都不再报错、子项也不重复。

#### 三、验过的

| 步骤 | 结果 |
| --- | --- |
| 收起态（演示站 8147，销售页） | 左侧 = 首页 + 物料与库存（物料/库存/入库/出库/收发存报表/盘点）+ 采购系统（采购记录/供应商）+ 销售系统（销售记录/收银 POS/收银记录/交班记录/客户）；**没有「团队管理」、也没有「主数据」那一组** |
| 业务页点左下角那颗按钮 | 左侧条目区正好两项：**个人信息 / 团队管理**（框架 `sidebar_item` 模板渲染，图标与 hover 与侧栏一致），**右侧默认落在个人信息**；截图见本窗口对话 |
| 账号区里两项互跳 | 点「团队管理」→ `/desk/pearnly-team`（页面 **8 / 8** 行），**左侧仍是那两项**（用户第二轮口径）；再点「个人信息」→ `/desk/user/…`，左侧同样是那两项 |
| 再点一次左下角 | 菜单画回业务那份（首页/物料与库存/…），右侧不动 |
| 离开账号区 | 到业务页（销售记录/首页）左侧就是业务菜单；再点左下角又回到两项 + 个人信息 |
| 直接打开团队页（书签/刷新） | 左侧同样是那两项（菜单跟着当前页走），不是业务菜单 |
| 非管理员看不看得到 | 用 ERP Sales 岗位建一个临时账号 → 登录 → 取 `/desk/pearnly-sales` 页面里的 boot：`pearnly_user_menu: []`、`pearnly_pages` 里也没有 `pearnly-team`（连项都没有）；查完把临时账号与它的会话删掉，员工名单回 8 人 |
| 侧栏排序 | 进编辑态点「保存」：不再报"值无效"，保存后分组与子项都不重复；点「放弃」同样干净；测试写下的个人偏好行（`DefaultValue`）已删除，回到开工状态 |
| 隔离检查站 `erp-pos-b` | 同一份声明与投影（`clear-cache` + `ensure_navigation` 各跑一遍，退出码 0） |
| 离线检查 | `check-architecture.py` PASS(0)；`i18n-build.py --check` PASS（erp 809 条）；`check-frontend-i18n.py` 0 问题；`tests/test_erp_portal_access.py` 102 条 OK（6 跳过）；`check-i18n.py --strict` 合计 **113**（与基线逐项相同）；改动的 JS `node --check`、PY `py_compile` 全 OK |

#### 四、没验到 / 留给用户的

- **窄屏（≤620px）与折叠态下点开那两项的观感没量过**：折叠态（只剩图标）时那两项走的是原生条目的同一套样式，但没有像桌面这样看一眼。
- 真机没测。
- 「侧栏排序」这颗入口对**分组菜单基本没有可见效果**（分组与组内顺序都是产品声明的，个人顺序只影响平铺项）。要不要去掉这颗入口、或者做成真能改顺序，等用户定。
- **另记一个框架层现象（不是本轮引入，也没修）**：框架的加载遮罩是**计数**式的（`frappe.dom.freeze_count`），而"整页跳转"会掐断在途请求、它的回调不再执行 —— 计数不落回 0 时遮罩就一直在，界面看着"点不动"，刷新即恢复。本窗口用自动化连续整页跳转时踩到过（`freeze_count` 卡在 1、侧栏点不动），正常点菜单不会；要不要在框架那边加兜底（例如路由变化时复位计数），等用户定。

### F25：团队管理改叫「用户」、用户名/邮箱开户时定死、只改密码（2026-09-23，未部署）

用户口径（2026-09-23）：

1. "团队管理里面 不要出现员工，写添加用户，或者只写一个添加就好了"；
2. "你个人信息是不是动过了？你看看dms有些啥，对齐一下，但是有个问题，dms那边上次我说用户名可以自定义修改，这个不太符合逻辑，可以用用户名登录，但是不能修改，后台管理发号后，用户名和邮箱是绑死这个租户的，唯一关系，用户只可以自己改密码，员工也是一样"。

#### 一、先回答"个人信息是不是动过"

- 没有。`git log -- apps/pearnly_core/pearnly_core/public/desk/user.js` 在本分支没有任何提交（最后一次改动来自更早的共享提交 `a1fe87d`）。本任务在 `pearnly_core` 只动过 `browser_text.py`（F23）与 `desk/navigation.py`、`public/desk/{product,sidebar}.js`（F24）。个人信息那一屏的字段与只读规则来自 `account/layout.py` + `user.js`，此前没碰。

#### 二、DMS 那边是什么（对齐的依据）

- **花名册**（`pearnly_dms/operators/roster.py` + `public/desk/roster.js` + `frontend/src/pages/dms/RosterPage.vue`）：每个操作员 = 本地账号 + 他自己的 DMS 账密 + 一行档案；账号只走 LINE、不发本地密码。
- **我的 DMS 账号**（`frontend/src/pages/dms/AccountPage.vue` + `account/api.py`）：用户名与密码两个框原本都能改 —— 就是用户说的"上次说可以自定义修改"那一处。
- 对齐后的统一规则：**用户名与邮箱开户时发号、绑死这个租户，之后谁都不改；用户只能改自己的密码**（DMS 与 ERP 的员工账号同一条）。

#### 三、改了什么

| 层 | 落点 | 说明 |
| --- | --- | --- |
| 个人信息（core） | `account/layout.py` | `first_name`（界面上就是「用户名」）加一条 **DocType 级只读**的 Property Setter；发号（新建账号）仍是程序写入，不受影响 |
| 个人信息（core） | `account/profile.py` / `hooks.py` / `account/oidc_session.py` | 删掉"用户自己改名"整条路（`prepare_profile` 里的改名校验与 `username` 写入、`sync_username` 钩子、身份服务里那个改名分支），身份服务那条通道只剩"语言偏好"一件事；邮箱仍不能改 |
| 个人信息（core） | `public/desk/user.js` | 邮箱只读；**「更改密码」两种站点都给**：云站点走身份服务，本地站点进 `/pearnly-password`（改前本地站点这个按钮是隐藏的 —— 等于本地用户根本改不了自己的密码） |
| ERP 团队管理 | `public/desk/team.js`、`team/service.py`、`team/api.py`、术语表 | 文案改「添加用户 / 用户」（页面里不再出现"员工"）；编辑弹窗里姓名与邮箱**只读**（带一句"用户名和邮箱是开户时发的，之后不能修改"），能改的只有岗位；删掉 `api_update_member` / `update_member` / `set_member_name` |
| DMS 花名册 | `public/desk/roster.js`、`RosterPage.vue`、`operators/roster.py` | DMS 用户名改成"新建时填一次、之后只读"；服务端对"想改用户名"的请求**明确拒绝**（值一样或没带照旧通过，老客户端不炸） |
| DMS 我的账号 | `AccountPage.vue`、`account/api.py` | 用户名只给看不给改（提交只带密码）；服务端 `save_credentials` 只收密码，带不同用户名一律拒 |
| 术语表 | core / ERP / DMS | 新增「用户名和邮箱是开户时发的，之后不能修改」等词条；删掉 5 条只服务于"改名"的旧词条（`account.usernameFormat/Taken/profileChanged/signInToRename/profileUpdateFailed`）；顺手补上团队服务那几句**一直没登记**的错误文案（请填写用户名 / 请填写正确的邮箱 / 已存在 / 至少勾一个功能权限 / 密码至少 N 位）—— 之前它们会原样显示英文 |

#### 四、顺手修掉一个真 bug（保存岗位会把岗位点没）

- 现象：编辑一个账号、**什么都不改**直接保存 → 它的功能岗位被摘掉、账号直接从这一页消失。
- 根因：`team/service.py` 的 `set_member_roles` 先记下 `held = {已有岗位}`、再摘掉功能角色、最后按勾选补回 —— 补回时跳过"已在 `held` 里"的，于是被摘掉的那个永远补不回来。改法：`held` 改在**摘掉之后**再取。
- 实测代价：演示站夹具 `erp-pos-line-buyer@example.com` 的「采购」被我点没了一次，已用同一条服务路径改回（最终回读 `ERP Purchase` + `Desk User`，与开工一致）。
- 顺带：`scripts/erp-pos-lab-team-verify.py` 这条探针**本来就是坏的**（`add_member` 现在要求初始密码，探针一个都没传 → 从头到尾 ValidationError），补齐夹具与密码，并加了一条"原样保存同一批岗位不能丢"的断言。

#### 五、验过的

| 步骤 | 结果 |
| --- | --- |
| 个人信息页（演示站，浏览器） | 用户名/邮箱/中间名/姓/全名都只读（用户名灰底点不动），语言/时区/静音可改，**「更改密码」按钮出现**（本地站点 → `/pearnly-password`） |
| 团队页文案（浏览器） | 主按钮「添加用户」、名单列 姓名/邮箱/功能权限/状态/最后登录，整页没有"员工"字样 |
| 编辑弹窗（浏览器） | 姓名与邮箱只读并带"开户时发的、不能修改"提示；只有岗位的勾选框可点，另有「重置密码」「停用」 |
| 岗位保存（浏览器真点三次） | 改成"收银 + 采购"→ 行内变 `收银 · 采购`、名单仍 `8 / 8`；**原样保存** → 岗位保持（bug 修好）；取消一个勾选 → 回到 `采购` |
| 隔离检查站 `erp-pos-b` | `scripts/erp-pos-lab-team-verify.py` → `problems: {}`（含新断言：原样保存同一批岗位后仍是 `["ERP Purchase"]`） |
| 两站服务端 | `ensure_profile_fields`（用户名只读的那条 Property Setter 落库，回读 `read_only=1`）+ `clear-cache` + web 重启 |
| 离线检查 | `check-architecture.py` PASS(0)、`i18n-build.py --check` PASS（erp 815 条）、`check-frontend-i18n.py` 0 问题、`tests/test_erp_portal_access.py` 102 条 OK（6 跳过）、`check-i18n.py --strict` 合计 **113**（与基线逐项相同）、改动的 py/js `py_compile`/`node --check` 全过 |

#### 六、没验到（如实记）

- **云站点那两条真的要身份服务才走得通的路没实测**：改密码走身份服务、以及"名字/邮箱在身份服务里唯一"这条判据 —— 本实验室的站点没有身份服务。
- **DMS 的界面没在浏览器里看过**：这台机器上只有 ERP 站点（`erp-pos-a/b`），所以 DMS 的花名册与"我的 DMS 账号"两处改动只做了静态检查（`py_compile` / `node --check` / 逐行核对），没跑 DMS 探针。
- **前端没做类型检查/构建**：`frontend/` 的依赖没装，而且这台机器的 `npm` 指向一个不存在的 `npm-cli.js`，`vue-tsc` 跑不起来；两处 Vue 改动（用户名只读、编辑弹窗去掉用户名的框）只做了人工核对。
- 用户自己改密码的完整闭环（点「更改密码」→ 改完用新密码登录）没在本地站点走完一遍（那是身份服务/改密页的路径，交给有身份的站点验更合适）。

### F27：四个"官方口径"问题、个人信息页少的那一行、撤掉两个非官方字段（2026-09-23，未部署）

用户口径：

> "公司这边官方只允许一个公司吗？如何添加其它公司，公司和公司的数据隔离然后做？权限管理包含公司的分配和权限吗？我说了，用官方的，我说了三次了，官方有单据前戳吗？官方有 财年起月吗？图一：dms 这边对齐，点左下角只出现个人信息，其它功能不加，和 erp 一样，你 erp 这边，少一个域名显示"

#### 一、四个问题，按**官方源码**回答（`apps/erpnext` 就在 bench 里，直接读的）

| 问 | 官方怎么做的（证据） | 我们现在的样子 |
| --- | --- | --- |
| 官方只允许一个公司吗 | **不限**：`Company` 是树 —— `is_group`（Check）+ `parent_company`（Link → Company） | 一个租户一个站点、站内一家公司（`ERP Company` 只有一条默认） |
| 如何添加其它公司 | 官方就是"再建一条 `Company`"（每家自带账套 / 成本中心那套）；单据挂在公司上 —— `Sales Invoice.company` 是 `Link → Company` 且 **reqd=1** | 我们界面上没有"新建公司"（用户 2026-09-23 上午才说"就一个公司就好了"，所以撤了）；服务端 `create_company` 还在 |
| 公司与公司之间怎么隔离 | 官方的隔离件是 **`User Permission`**：把 `Company = 某公司` 指给某个用户，Frappe 原生按它过滤带该 Link 的列表与报表 | 我们用**站点级**隔离（一个租户一个站点、一套库，Frappe 原生的多租户做法）；单据表里没有公司字段 |
| 权限管理包含公司的分配吗 | 官方的"公司分配"就在 `User Permission` 那一页，不在角色里 | 团队管理（用户）页只发**功能岗位**（采购/销售/库存/收银…），没有"分配公司"这一项 —— 站内只有一家公司，没有可分配的 |
| 官方有"单据前缀"吗 | **没有**。官方单号走**命名序列**（`Document Naming Settings` / DocType 的 naming series），不在 Company 上 | 我们那个 `doc_prefix` 是上一轮自己加的，**现在不接任何地方**（单号走 `numbering/contracts.py` 那套固定前缀） |
| 官方有"财年起月"吗 | **没有**。官方财年是**独立单据** `Fiscal Year`（`year` / `year_start_date` / `year_end_date` / `companies` 子表），不是公司上的一个"起月" | 我们那个 `fiscal_year_start_month` 也是自己加的，只在控制器里校验 1–12，没人读 |

**要用户定的两件事**（都要动表或装东西，本窗口没动）：① 要不要在同一站点里放多家公司 —— 那是 ERPNext 那套（单据加 `company` 字段 + `User Permission` 隔离 + 账套），要么就维持"一家公司 = 一个站点"；② 那两列（`doc_prefix` / `fiscal_year_start_month`）要不要 `migrate` 删掉（界面已经不再显示）。

#### 二、"erp 这边少一个域名显示" —— 查到根因并修好

根因两条，缺一不可：

1. 框架有一条**官方规则**：只读且没有值的字段**整行藏起来**（`System Settings.hide_empty_read_only_fields = 1`，`base_control.get_status()` 走到 `"None"` 就挂 `hide-control`）；
2. 我们服务端只在带身份服务的站点（`pearnly_native_account`）才把「公司名 / 您的访问域名」两项发给页面（`account/profile.py` 的 `load_details`）—— 本地实验室站点没有那个开关，两格是空的，于是被框架藏掉，看着就是"少一行"。

改法：`load_details` 改成**只要是产品站点、看的是自己的资料就给值**；域名用框架自己的 `get_url()`（官方取站点地址的函数，取 `host_name`，没有就按当前站点拼），不再自己从 `host_name` 取。实测：个人信息页出现「公司名 = Purchase Preview / 您的访问域名 = http://erp-pos-a.localhost」。

#### 三、撤掉两个非官方字段

「单据前缀」与「财年起月」从公司页拿掉（见上表：官方没有这两样，而且我们的没有人读）。页面现在只剩「公司信息 / 地址与备注」两节 + 保存 + 查询，共 11 个字段。表结构里的两列**还在**（界面不显示），删列要 migrate，等用户一句话。

#### 四、"dms 这边对齐" —— 代码里已经一样，那张图是旧部署

- 三个产品共用同一套外壳（`pearnly_core/public/desk/product.js` 的 `p.user_menu`）；`user_menu` 只有 ERP 声明了（公司排在个人信息上面），**DMS 与 Cowork 都没有声明** —— 所以它们的账号区就是「个人信息」一项，点左下角也只出现这一项。
- 用户截的那张线上 DMS（`testdms.pearnly.com`）里侧栏还是业务菜单，是因为**这次的外壳改动今天才做，还没部署**；线上 DMS 只到 302（跳登录），我在本地没有账号可比对线上代码版本，这一点如实记。

#### 五、顺带：三支一直是红的 JS 测试修回来了

`tests/test-native-desk.cjs`、`test-field-rows.cjs`、`test-stock-card.cjs` 从 F24（`0a9b21e` 引入 `class ProductSidebar extends NativeSidebar`）起就整支加载即抛 `Class extends value undefined` —— 它们的假环境里没有 `frappe.ui.Sidebar`。补上这个成员（`test-native-desk.cjs` 还补了 `frappe.boot.user` 与个人信息页真实的字段清单），现在 `tests/*.cjs` **10 支全绿**；并给"静音保存后当场生效"加了一条断言。

#### 六、没验到（如实记）

- 非管理员视角的公司页（只读、没有保存与查询按钮）：仍然只在服务端测试里覆盖，浏览器没点过。
- 线上 DMS：只到 302，没有账号，没在线上比对版本与观感。
- 真机、部署、用户验收：未做。

### F26：公司合成一屏（原名「公司资料 / 公司主档」）+ 税号查询 + 静音开关即时生效（2026-09-23，未部署）

用户口径（本轮四条，按时间顺序）：

1. "那个静音是不是一个假的，根本不起作用" + "个人信息和团队管理你是不是没有加选中的主底色" + "然后做公司，按官方原生隔离系统做好数据隔离，然后数据关联全站打通，做在个人信息上方"；
2. "不要公司资料公司主档，就一个公司就好了，包含全部"；
3. "我说了用官方的功能简化，而且我说了，只写公司，再把税号查询接进来"；
4. "不要出现哪些傻逼文案"。

一条条对：

| 用户说的 | 查到的 / 做的 |
| --- | --- |
| 静音是不是假的 | **开关是真的**：框架 `frappe.utils.play_sound` 见到 `frappe.boot.user.mute_sounds` 直接不播，我们自己的 6 处提示音（`product.js` 的 submit、`cashier.js` 的 error/numpad-touch、`scan.js`）全走它。**假的观感来自那个值只在整页加载时取一次** —— 勾上并保存后当前这一页照响，刷新才静音。改法：`user.js` 的 `refresh` 里回写一次（含保存之后那一次刷新）。实测两向都通（见验证表）。 |
| 两个账号菜单项没有选中底色 | 业务侧栏用原生 `.active-sidebar` 上底色，我们画「个人信息 / 团队管理」时没加。改成按当前路由给对应项加同一个类：个人信息 = `Form/User/<自己>`，产品声明的项 = 路由首段。 |
| 公司做成"一屏" | 原来是「公司资料（默认公司那一屏）+ 公司主档（公司列表/新建/设默认/停用）」两张面孔。按第 2 条合成**一屏**：`/desk/pearnly-company` 一页放下抬头、简称、税号、分支、电话、联系人、联系邮箱、PromptPay、单据前缀、财年起月、地址、备注；`nav.companyMaster` 从 `pages`/分组里拿掉（服务端 `list_companies / create_company` 留着，实验室脚本还在用，界面不再进）。 |
| 名字只写「公司」 | core 术语表里 `nav.company` 那条英文 msgid 由 `Company Profile` 改成 `Company`、中文由「公司资料」改成「公司」 —— 这条 nav 词是 Cowork 与 ERP 共用的，Cowork 那边的菜单项也跟着显示「公司」。 |
| 税号查询接进来 | 复用供应商 / 客户两页那套：同一接口 `api_lookup_tax`（泰国税务局官方登记）、同一套失败原因文案；按钮是**原生 `Button` 字段**（不是自画的控件），点在税号下面，回填抬头 / 税号 / 地址 / 分支号。能不能查由服务端 `can_lookup`（能力码 `tax.lookup`）说 —— 没这个能力的岗位连按钮都不出现。 |
| "不要哪些傻逼文案" | 上一轮我自己加的四条说明（税号 13 位 / 收款账号 / 「单据前缀先存着」/「1 = 一月」）与页面副标题**全部删掉**；页面现在只剩分区标题与字段标签，查询/保存的提示沿用供应商页那几条已统一的文案。 |
| （顺手修掉的真 bug） | 框架的 `FieldGroup.get_values()` 把空串当"没这个字段"丢掉（`is_null("")` 为真），所以**清空一格再保存根本不生效**。实测：清掉备注保存后回读还是原文。公司页改成按自己的字段清单逐个取（空就是空），并补上"名称必填"用框架那两条 msgid 的提示。**同样的写法还在别处**（供应商 / 客户 / 团队等页用 `dialog.get_values()` 保存），本轮只修公司页，其余要不要一起改等用户发话。 |

**隔离与"全站打通"的现状（本轮只读核对，没动数据模型）**：

| 项 | 现状 |
| --- | --- |
| 数据隔离 | 平台原生 **Site 级**：一个租户一个站点、一套库（`ERP Company` 就是"本租户的公司"）。单据表（`ERP Sales Document` / `Sales Document` / `ERP Sale` / `Purchase Document`）里**没有**公司字段 —— 即数据不按公司切分，站上只有一条 `is_default` 公司。 |
| 已经打通的 | 采购的「代收据 / 预扣税凭证」PDF 抬头取默认公司的 **名称 / 税号 / 地址**（`purchasing/output.py` + `purchase_document`）。 |
| 还没打通的 | 导出件抬头、销售 / 收银的小票抬头都还没读公司；`doc_prefix`（单据前缀）**当前不接任何地方** —— 单号仍走编号定义（`numbering/contracts.py` 那套 PE/SI/IR/IS… 前缀）。 |
| 要用户定的一句话 | 是"**一个租户一家公司**，把抬头接进导出与小票就好"，还是"**多公司严格隔离**（单据挂公司字段、各页按公司过滤）"。这决定要不要动表结构，本轮没动。 |

**没验到（如实记）**：①非管理员进公司页那一路（字段只读、没有保存按钮、没有查询按钮）只在服务端有测试覆盖，**浏览器里没点过**；②隔离检查站 `erp-pos-b` 只跑了 `clear-cache`，没跑探针；③真机、部署、用户验收未做。

> **与旧记录的冲突说明**：`docs/work/2026-09-21-erp-pos-migration.md` 与 `docs/frontend/01-page-inventory.md` 记的是 **2026-09-21** 的口径 —— "公司主档与公司资料两个不同页面，用户确认两者都要"。**2026-09-23 用户改口径了**（"不要公司资料公司主档，就一个公司就好了，包含全部"），以本轮为准；那两份旧记录按原样留着当历史，不改写。

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
| 2026-09-23 | F4 改动（未提交时） | 宿主 | `check-architecture.py` | `PASS (0 violations)` | 共享冲销入口的改动没有越界 |
| 2026-09-23 | 同上 | `pearnly-purchase-lab` / `erp-pos-b.localhost` 隔离检查站 | `erp-pos-lab-sales-verify.py` | `problems: {}`；新断言 `void_movement_doc_no = SI2569-000025`、`void_movement_matches_number = true` | 销售作废的冲销行带的**就是本单的号**，不是内部名 |
| 2026-09-23 | 同上 | 同上（直接验，因为库存探针已跑不起来，见 F4） | 建入库单 → 提交 → 作废，回读冲销行 | 入库单号 `IR2569-000001`，冲销行 `doc_no = IR2569-000001`、`movement_type = adjust`、`qty_base = -3` | 入库/出库这条冲销路径也带号；采购（两处）与收银退货走的是同一个函数 |
| 2026-09-23 | 同上 | 内嵌浏览器 / 演示站 8147 | 库存页搜「ข้าวหอมมะลิ」→ 打开明细弹窗；收发存选中该商品 → 查看流水 | 明细弹窗「关联单据」列：`IR2569-000003 / IR2569-000009 / SI2569-000004 / … / SI2569-000021`，**作废那条显示 `SI2569-000023`**（修复前显示 `SD-00286`）；收发存明细页同样每一行都是单据号，作废行显示 `SI2569-000023`（+2）与 `IR2569-000003`（-150） | 用户报的"单号显示不对"在两层界面都已修正；日期列在窄弹窗里显示截断是列宽，不是数据 |
| 2026-09-23 | F5 演示样本（未提交时） | 内嵌浏览器 / 演示站 8147 | 销售列表首屏 | 表头 `20 / 23`；行内 6 个「收款」按钮、8 个红色未收/部分状态；点「筛选」选未付得 `9 / 9` 且状态只有未付与部分付款 | 样本按真实接口生成，列表/筛选/颜色都能看到效果 |
| 2026-09-23 | F14 改动（未提交时） | 宿主，无 Site | `python3 tests/test_erp_tax_lookup.py` | 11 条 **OK** / 0 | 夹具是 2026-09-23 从 `rdws.rd.go.th` 抓的真实响应（税号 `0105546015062`）：抬头、门牌、区、省、邮编、VAT 首次登记日逐项断言；另断言 `anyType` 包装不进字段表、`-` 当空、乱响应返回空 |
| 2026-09-23 | 同上 | 宿主 | `tests/test_erp_portal_access.py` / `check-architecture.py` / `i18n-build.py --check` / `node --check suppliers.js clients.js` | 102 条（96 通过 / 6 跳过）、`PASS (0 violations)`、`RESULT: PASS`（erp 692 条）、JS OK / 全 0 | 契约与边界未被破坏；新命名空间走的是既有白名单 |
| 2026-09-23 | 同上 | 宿主 | `python3 scripts/check-i18n.py --strict` | `FAIL`，但 `py-bare-cjk` **18** 条、合计 **113** 条，与基线**逐项相同**；新增文件一条没有 | 与基线一致，未改忽略规则、未删断言 |
| 2026-09-23 | 同上 | `pearnly-purchase-lab`，A + B | `bench --site erp-pos-{a,b}.localhost migrate` | 退出码 0；回读 `show columns from \`tabERP Tax Lookup Cache\`` 得到 tax_id/branch_no/service/is_success/error_msg/fetched_at/expires_at/payload | 缓存表按原生迁移建好（第一次 migrate 报 `ImportError`：DocType 缺同名控制器 `.py`，补上后通过） |
| 2026-09-23 | 同上 | `pearnly-purchase-lab` / `erp-pos-a.localhost` 服务端 | `bench execute pearnly_erp_portal.masters.rd.lookup`（税号 `123` 与 `0105546015062`） | `{"ok": false, "error": "invalid_format"}` / `{"ok": false, "error": "rd_unreachable"}` | 格式闸在打接口之前；**容器出不了外网**，失败只回原因、不抛异常 |
| 2026-09-23 | 同上 | **宿主**直连真实接口（`urllib`，超时 10s，走 `rd_contracts` 的模板与解析） | VAT + TIN 两个服务 | VAT：`บริษัท ซินเซียร์ไอซ์ จำกัด`、`14 รามคำแหง 189 มีนบุรี มีนบุรี กรุงเทพมหานคร 10510`、省 `กรุงเทพมหานคร`、邮编 `10510`、VAT 登记日 `2546-02-14`；TIN：`valid/exists = true` | 解析层对**活着的**线上接口成立（含省与区 —— 旧线上那套只认 `*Name` 的写法在这里会丢；同一次抓下来的响应就是测试夹具） |
| 2026-09-23 | 同上 | `pearnly-purchase-lab` / `erp-pos-b.localhost` 隔离检查站 | `PEARNLY_ERP_POS_LAB_SITE=erp-pos-b.localhost ./env/bin/python /workspace/scripts/erp-pos-lab-masters-verify.py` | `problems: {}`；新断言：`tax_cache_doctype true` / `denied_for_cashier true` / `denied_for_outsider true` / `bad_format "invalid_format"` / `allowed_for_sales true` / `allowed_for_purchase true` / `first_is_fresh true` / `cached_second_time true` / `http_calls 1` / `cache_rows 1` / `offline_raised false` / `offline_error "rd_unreachable"` / `other_site_tax_cache_rows 0` / `cache_rows_left 0` | 站点里的整条链路：真实响应→缓存 DocType→第二次命中缓存**只打一次外部接口**；岗位边界（收银与无岗位被 `PermissionError` 拒）；连不上不抛异常；缓存不外漏到另一站点；跑完自己删掉缓存行 |
| 2026-09-23 | 同上 | `pearnly-purchase-lab` / `erp-pos-b.localhost` | `./env/bin/python /workspace/scripts/erp-pos-lab-sales-verify.py` | `problems: {}` / 0 | 本次只加主数据与前端，销售域未被带动破坏 |
| 2026-09-23 | 同上 | 内嵌浏览器 / 演示站 8147 / 客户页 | 点表头「全选」→ 主按钮 → 再全选 → 主按钮 | 7 行全勾、主按钮变「停用 7」；点下去提示「已更新 7 位客户」、7 行标「已停用」、选择清空、主按钮回「新建客户」；再全选时按钮已读成「启用 7」，点完 7 行恢复；服务端回读 7 位 `is_active=1` | 全选/批量**真的写库**且能来回；窗口内的截图另存于本窗口对话，仓库不留图 |
| 2026-09-23 | 同上 | 同上 / 供应商页 | 勾前 2 行 → 「停用 2」→ 再勾 → 「启用 2」 | 提示「已更新 2 家供应商」、两行标「已停用」→ 恢复；主按钮回到「新建供应商」 | 部分选择与全选走同一条路径；演示站的 36 家供应商已全部回到启用 |
| 2026-09-23 | 同上 | 同上 / 两个弹窗 | 客户弹窗「查询」：空税号 / `123` / 真税号 `0105546015062` | 依次弹「请先填税号」/「税号必须是 13 位数字」/「现在连不上税务局」；三种都在**不改动弹窗**的前提下返回（弹窗不关、字段不丢、主按钮可用、遮罩已释放） | 提示能说人话（分别对应前端闸、`invalid_format`、`rd_unreachable`）；供应商弹窗点「查询」同样得到「现在连不上税务局」 |
| 2026-09-23 | 同上 | 同上 / 客户弹窗 | 客户类型切「个人」→ 切回「公司」 | 切到个人：`查询` 那一格 `display: none`；切回公司：重新出现 | `depends_on` 生效（个人/外国人没有 RD 登记） |

| 截图证据（F14，桌面视口） |
| --- |
| 客户页全选后：表头全选框勾上、7 行全勾、右上角主按钮「停用 7」；弹窗「查询」按钮在税号与地址之间；提示框为原生「信息 / 现在连不上税务局」 |
| 2026-09-23 | F15 改动（未提交时） | 内嵌浏览器 / 演示站 8147 | 采购列表量 `20 / 73` 那一格（改前 → 改后） | 改前：`--pearnly-actions-width` 43px、计数格 **33×42**（两行，与用户截图一致）；改后：同一格 **47×21**（一行） | 与用户那张图同一页同一位置，改前改后各量一次 |
| 2026-09-23 | 同上 | 同上 | 12 个页面逐个量计数格高度（物料 / 库存 / 入库 / 出库 / 收发存 / 盘点 / 采购记录 / 供应商 / 销售记录 / 收银 / 客户 / 首页） | 除收银（0/0、无行内动作）与首页（无列表）外**全部 h=21**（一行） | "所有页排查"这一条，逐页量过 |
| 2026-09-23 | 同上 | 同上 / 视口 414×844 | 采购列表计数格 | 窄屏下框架把 `.list-count` 设成 `display: contents`（`headerRight` 60.8px），文字**仍在一行** | 窄屏是另一套规则（`@media max-width:991px`），单独验过一次；验完已把视口复位 |
| 2026-09-23 | 同上 | 同上 | 物料 / 供应商 / 客户三页的状态列 | 三页正常行都显示「**正常**」（改前：物料显示「激活」、供应商/客户是空白） | 同一概念三页同一说法 |
| 2026-09-23 | 同上 | 同上 / 客户页 | 勾 1 行 →「停用 1」→ 再「启用 1」 | 状态格 `正常` → `已停用` → `正常`，提示条「已更新 1 位客户」；回读 `is_active=1` | "状态联动停用与正常"这一条 |
| 2026-09-23 | 同上 | 同上 / 供应商弹窗 | 打开演示供应商 → 点「保存」（未改动任何字段） | 提示条「**已保存**」；`#sound-submit` `paused=false`、`currentTime` 96→722ms | 提示 + 音效都发生 |
| 2026-09-23 | 同上 | 同上 / 销售列表 | `SI2569-000010`（未付）行内「收款」→ 弹窗默认 1,078.56 → 提交 | 提示条「**已收款 THB 1,078.56**」；音效走到 882ms（文件长度）才停；列表行变已付款、不再给收款入口 | 主按动作的回音（销售侧）；随后已用同一条服务路径 `entry.payment("SD-00255", paid=0)` 改回未付，回读 `unpaid / 0 / 方法日期备注全 NULL` |
| 2026-09-23 | 同上 | 同上 / 采购列表 | `PE2569-000070`（未付）行内「付款」→ 弹窗默认 104.00（正好是欠款）→ 提交 | 提示条「**已付款 THB 104**」+ 音效；随后 `purchasing.service.set_payment_status("PUR-2026-00190", "unpaid")` 改回，回读 `unpaid / paid_amount 0`（与开工时一致） | 主按动作的回音（采购侧） |
| 2026-09-23 | 同上 | 同上 + `bench console`（造夹具，非界面） | 造一条合成草稿 `SD-00484`（owner 改成登录用户）→ 列表勾选 →「删除 (1)」→「是」 | 提示条「**已删除 1**」+ 音效（0→874ms）；回读该单 `count=0`，销售单据回到 `submitted 22 / void 1`（与开工时一致） | 删除草稿的回音；夹具自清，演示站不留痕 |
| 2026-09-23 | 同上 | 宿主 | `check-architecture.py` / `i18n-build.py --check` / `tests/test_erp_portal_access.py` / `node --check`（7 个改动过的 JS） | `PASS (0 violations)` / `RESULT: PASS`（erp 700 条）/ 102 条 OK（6 跳过）/ JS 全 OK | 本次只动前端与皮肤，服务端契约未动 |
| 2026-09-23 | 同上 | 宿主 | `check-i18n.py --strict` | `FAIL`，但 `py-bare-cjk` **18**、合计 **113**，与基线**逐项相同** | 未新增未登记文案，未改忽略规则 |
| 2026-09-23 | F16 改动（未提交时） | 宿主 | `check-architecture.py` / `i18n-build.py --check` / `tests/test_erp_portal_access.py` / `node --check product.js cashier.js` | `PASS (0 violations)` / `RESULT: PASS`（erp 730 条）/ 102 条 OK（6 跳过）/ JS OK | 本次只动前端与皮肤，服务端契约未动 |
| 2026-09-23 | 同上 | 宿主 | `check-i18n.py --strict` | `FAIL`，`py-bare-cjk` 18 / 合计 113，与基线相同 | 同上 |
| 2026-09-23 | 同上 | 内嵌浏览器 / 演示站 8147 / **框架自带的文件管理器** `/desk/file` | 点「添加文件」→ 看上传框的按钮 | 改前（用户截图同一处）：我的设备 / 库 / 链接 / 摄像头 **4 颗**；改后：**我的设备 / 摄像头 2 颗** | 这一处的调用是 `new frappe.ui.FileUploader({folder})`（一个选项都没传）= 框架默认那条路；说明补丁盖住了我们自己改不到的地方 |
| 2026-09-23 | 同上 | 同上 / 收银 POS | 打开页面 | 改前：主按钮「收银开单」带 `hide`（**点不到**）、当班小票的类型/状态是英文原文；改后：主按钮可见、列表为 销售 / 已完成，指示器「班次 2 · 应有现金 THB 1,039」 | 主按钮被列表清掉的问题（见 F16 第 1 条）同一处验完 |
| 2026-09-23 | 同上 | 同上 / 收银 POS | 点「开班」→ 看默认值 → 备用金填 1,000 → 开班 | 收银台**默认带出 `TERM-00485`**；提示条「**已开班 · 班次 2**」+ 音效（0→882ms）；指示器变「班次 2 · 应有现金 THB 1,000」，主按钮变「收银开单」 | 开班这一路（含提示与音效） |
| 2026-09-23 | 同上 | 同上 / 收银 POS | 点「交班」→ 读汇总 → 实点填 1,140 → 1,120 → 1,130 → 提交 | 汇总与后端逐项一致（笔数 2 / 毛额 214 / 退款 0 / 净额 214 / 现金 140 / 扫码 78 / 刷卡 0 / 转账 0 / 备用 1,000 / 找零 4 / 应有 1,136）；差额 1,140→**多钱 +4**、1,120→**短钱 −16**、两处都是 `rgb(204,41,41)`（框架 `--red-600`）；按 1,130 提交 → 提示「**已交班 · 差额 THB -6**」+ 音效；页面回「尚未开班 / 开班」 | 交班这一路（汇总、实时差额、标红、提示音效）一次走完；服务端回读 `closed / expected 1136 / counted 1130 / diff -6` |
| 2026-09-23 | 同上 | 同上 / 收银 POS | 点「班次记录」 | 表头 班次编号/收银台/收银员/状态/开班时间/交班时间/备用现金/应有现金/实点现金/差额；两行数据（含刚交的 `SHIFT-2026-00486`，实点 1,130、差额 **THB -6 标红**）；收银员显示全名「采购验收」 | 第一次是**空表**（表头有一行没有）→ 查出是 `p.table` 在弹窗表单挂上文档之前构建（`styleEl.sheet` 为 null）→ 改用 `on_page_show` 后正常（见 F16 第 2 条） |
| 2026-09-23 | 同上 | `pearnly-purchase-lab` / `erp-pos-a.localhost`（服务端造样本，非界面） | `cashier.terminals.ensure_default` / `shifts.open_shift` / `cashier.sales.create_sale` | 终端 `TERM-00485`；班次 `SHIFT-2026-00486`（备用 1,000）+ 小票 `SALE-2026-00487`（136.00 现金、找零 4）与 `SALE-2026-00489`（78.00 扫码）；汇总 `expected_cash 1136` 与界面读到的**一致** | 演示样本走真实服务路径（销售 + 收款 + 出库三处联动），不是直接写库 |
| 2026-09-23 | F17 改动（未提交时） | 宿主 | `check-architecture.py` / `tests/test_erp_portal_access.py` / `i18n-build.py --check` / `node --check stock-card.js cashier.js` | `PASS (0 violations)` / 102 条 OK（6 跳过）/ `RESULT: PASS`（erp 730 条）/ JS OK | 本次动的是取号调用与前端重取逻辑，服务端契约未改 |
| 2026-09-23 | 同上 | 宿主 | `check-i18n.py --strict` | `FAIL`，`py-bare-cjk` 18 / 合计 113，与基线相同 | 未新增未登记文案 |
| 2026-09-23 | 同上 | `pearnly-purchase-lab` / **隔离检查站** `erp-pos-b.localhost` | 服务端落一笔收银 + 一笔退货 | 收银 `receipt_no SI2569-000033`，流水 `doc_no SI2569-000033`（`sale_out`，-1）；退货 `SI2569-000034`，流水 `doc_no SI2569-000034`（`stock_in`，+1）；计数器 `sales-SI-2569` 从 33 → 35 | 与销售记录共用同一行计数器与行锁；退货也接在同一序列 |
| 2026-09-23 | 同上 | `pearnly-purchase-lab` / 演示站 `erp-pos-a.localhost` | 服务端落一笔收银（`PRD-00220` × 2 @ 45，现金 100）→ 界面收银列表 → 收发存明细 | `receipt_no SI2569-000024`（**紧接销售记录的 `SI2569-000023`**）；界面列表显示 `SI2569-000024 · 销售 · 已完成 · THB 90`，历史三张仍是 `R…`；收发存 `ปลากระป๋อง ตราสามแม่ครัว` 明细第 9 行 = `SI2569-000024`（出库 2、结存 62），与 `IR…/AD…/SI…` 同一账；班次 2 汇总含这笔（应有现金 1,129） | 用户能看到的三层（列表 / 单号 / 账）都对上 |
| 2026-09-23 | 同上 | 内嵌浏览器 / 演示站 | 收发存明细页：点日历把结束日期改成 `2026-09-10` | 改前：控制台 `ReferenceError: reload is not defined`、明细 9 条纹丝不动；改后：合计 **3 条明细**、期末 **276 / 3,588.00**、`SI2569-000024` 按日期被正确排除、无报错 | F17 第二节那个缺陷；顺带记了"改 Desk JS 要先 clear-cache 再重启 web，否则浏览器吃旧缓存" |

| 2026-09-23 | F21 改动（未提交时） | 外部环境：`pearnly-ci-business-sample` / `business.localhost` / `127.0.0.1:8084`（`pearnly-erp` 自己的示例站，只用浏览器只读查看） | 登录 `owner@sample.localhost` → `/desk/point-of-sale` → 点商品卡 → 点购物车行 → 侧栏「收银记录」→ 全选 → 展开「操作 ▾」 | 收银台 = 卡片网格 + 购物车；点购物车行出「物料详细信息」面板且键盘这时才出现；`.number-pad`/`.item-details-container` 在 DOM 里；「收银记录」地址 = `/desk/sales-invoice/view/list?is_created_using_pos=1`；列表每行都有勾选框、全选后「已选 20 条记录」+「操作 ▾」13 项 | 回答"官方有没有单选多选"：有，而且是默认的（另附框架源码：`list_view.js` 里没有开关） |
| 2026-09-23 | 同上 | 内嵌浏览器 / 演示站 8147，13 个列表页逐个量 `.list-row-checkbox` / `.list-row` | 改前 / 改后各量一遍 | 改前：12 页勾选框数 = 行数；**收银记录 0/6、交班记录 0/2**；改后：收银记录 **6/6**、交班记录 **2/2**、盘点 10/10，其余 12 页一个没少 | 用户那条"列表都不加单选多选"落到具体页上就是这个范围 |
| 2026-09-23 | 同上 | 同上 / 收银记录页 | 表头全选 → 点「作废选中的 6 张」→ 确认框点「否」 | 按钮文案 `作废选中的 6 张`；确认框「作废选中的 6 张单据并冲销对应库存变动？」（否/是）；点否后 6 行仍勾着、按钮可点；回读 `ERP Sale` 6 条全 `completed`（没误作废） | 真实动作 + 不误伤数据 |
| 2026-09-23 | 同上 | 同上 / 交班记录页 | 全选 → 点「导出选中的 2 个班次」；宿主 `curl` 带会话取同一 URL；bench 里 openpyxl 读回 | 按钮文案 `导出选中的 2 个班次`；新标签 URL `…api_shifts_export?names=["SHIFT-2026-00492","SHIFT-2026-00486"]`；`HTTP 200 / application/octet-stream / 5375 字节`；表 `Shift Records` A1:J3 = 两行班次（00492 应有 1309/实点 1309/差额 0；00486 应有 1136/实点 1130/差额 -6） | 新的导出链路端到端可用；表头中英混排见 F21 第五节（与销售记录导出同形态） |
| 2026-09-23 | 同上 | 同上 / LINE 待处理（销售向） | 服务路径造 2 条草稿 `LI-00501/502` → 页面全选 → 「取消选中的 2 条」→ 填原因 → 确认 | 改前该页无勾选框；改后 2/2 勾选框 + 按钮 `取消选中的 2 条`；提交后提示条「已取消 2 条」+ 列表「无数据」；回读两条 `discarded` 且 `discard_reason` 落库；随后连同 4 个附件文件删除，回读演示站 = 9 条草稿 + 18 个 intake 文件（与开工一致） | 批量取消真的写库且带原因；夹具自清 |
| 2026-09-23 | 同上 | 同上 / 销售单详情 `SD-00204` | 打开带 1 个附件的销售单 | 附件区 1 行、`.list-row-checkbox` = **0**；整页勾选框 0 | 表单里的附件行明确不参与批量选择 |
| 2026-09-23 | 同上 | 宿主 | `check-architecture.py` / `i18n-build.py --check` / `tests/test_erp_portal_access.py` / `check-i18n.py` / `node --check`（7 个 JS）/ `py_compile`（3 个 py） | `PASS (0 violations)` / `RESULT: PASS`（erp **757** 条）/ 102 条 OK（6 跳过）/ 合计 **113**（与基线逐项相同）/ JS 全 OK / PY OK | 只动前端、一个只读接口与术语表，服务端契约与权限口径未变 |

| 2026-09-23 | F22 改动（未提交时） | 外部环境：`pearnly-ci-business-sample` / `business.localhost` / `127.0.0.1:8084`（读官方源码与样式表；固定视口 1440×900 逐块量盒子） | 登录 `owner@sample.localhost` → `/desk/point-of-sale` → 点商品 → 点购物车行 → 结账 → 最近订单 | app `1220×844.4`、10 列各 106.055、gap 15、padding 1%；商品区 711.4 / 购物车 469.2 / 段高 820；卡片 194=128+56；键盘只在选中行后出现（5 列）；结账后购物车 x=958.6 留左、收款面板 x=716.4 占右六列；最近订单左四列+汇总右六列 | 官方那套"位置尺寸与交互"的**基准值**，后面逐条对 |
| 2026-09-23 | 同上 | 内嵌浏览器 / 演示站 8147 / 新收银台 | 重写后按同一批动作走一遍（加商品 → 选行 → 键盘改数量 → 结账 → 收款键盘 → 完成订单 → 最近订单 → 交班弹窗） | 版式公式与官方一致（app 1109、列 95.18、商品区 646.1、购物车 425.7、段高=视口−5rem、卡片 194=128+56）；键盘高亮目标键后按 3 → 数量 3.000/行「3 件」/合计 THB 30 全跟着变；收款面板进右六列、购物车留最左；按 1、5 → 现金 15、合计标签变「找零 THB 5」；完成订单 → 提示「小票 SI2569-000028 · 找零 THB 0」+ 右半边换成本单汇总 | 界面与交互按官方复刻后逐条对上（截图另存于本窗口对话，仓库不留图） |
| 2026-09-23 | 同上 | `pearnly-purchase-lab` / 演示站 `erp-pos-a.localhost` | 服界面上真卖一笔（`PRD-00224` × 2、现金 78）→ 服务端回读 | `SALE-2026-00507 / SI2569-000028 / completed / 78 / 78 / 找零 0`；流水 `SI2569-000028 · PRD-00224 · 2 · sale_out · WH-00003`；结存 53 → 51 | 新收银台 → 我们的销售/库存/收发存三处联动没断 |
| 2026-09-23 | 同上 | 同上（自测用的那笔） | 先用 `PRD-00410`（库存 0）卖了一笔 `SI2569-000027` | 卖出后该商品结存变成 **-1** → 用服务路径作废该单（`void_sale(reason="自测：该商品本来没库存，作废还原")`）→ 结存回 **0**；单据留成 `void` | 自测数据自己收拾；留下一条作废单（下面的收尾里登记） |
| 2026-09-23 | 同上 | `pearnly-purchase-lab` / **隔离检查站** `erp-pos-b.localhost` | 新表写路径：`open_shift(balances={cash:300,card:100})` → 卖一笔现金 50+刷卡 20 → `close_shift(counted={cash:345,card:120})` | 开班后有四行对账；应有现金 **350**、刷卡 **120**；交班后差额现金 **−5**、刷卡 **0**；父字段 `expected_cash 350 / counted_cash 345 / cash_diff −5`；子表四行与算出来的一致 | 按支付方式对账的**写路径**在真站点跑通（不在演示站动用户那个开着的班） |
| 2026-09-23 | 同上 | 同上 / 检查站上的老班次 | 直接读一个**本轮之前开的**班的汇总 | 返回一行现金 `opening 0 / expected 0 / counted None`（开着的班不报实点） | 老数据不会因为新子表变空白 |
| 2026-09-23 | 同上 | 宿主 | `check-architecture.py` / `i18n-build.py --check` / `tests/test_erp_portal_access.py` / `check-i18n.py` / `node --check cashier.js` / 两站 `bench migrate` | `PASS (0 violations)` / `RESULT: PASS`（erp **777** 条）/ 102 条 OK（6 跳过）/ 合计 **113**（与基线逐项相同）/ JS OK / migrate 退出码 0 | 新 DocType 与新字段走原生迁移；未新增未登记文案 |

| 2026-09-23 | F23 改动（未提交时） | `pearnly-purchase-lab` / 演示站 `erp-pos-a.localhost` + 检查站 `erp-pos-b.localhost` | 带会话真取四份导出件（销售 / 采购 / 交班 xlsx、库存 CSV），bench 里 openpyxl 读表头 | 销售 29 列全中文、**无重复表头**（改前两列都叫「客户名称」）；采购 48 列全中文、前两列由 `编号 / 编号` 变成 `编号 / 单据编号`；交班 10 列、库存 10 列全中文 | 用户口径"导出的报表术语表口径要统一"：导出件与页面用同一批词 |
| 2026-09-23 | 同上 | 同上 / 演示站 8147（内嵌浏览器） | 员工页：重载 → 量行数与勾选框 → 添加员工 → 改名改岗 → 重置密码 → 停用 → 启用 | 改前 `1 / 8` 一行（框架 `uniqBy(d => d.name)` 把没有 `name` 的 8 行并成 1 行）；补 `name` 后 **8 行 / 8 勾选框 / `8 / 8`**；添加提示「已添加 自测临时员工」+ 提示音（882ms）；改名改岗「已保存」；重置密码「密码已更新」；停用后该行「已停用」标红、`login` **401**；启用后新密码 `login` **200** | 员工页整条闭环在界面走完，权限边界有服务端回读（`api_list_members` 403 / `api_sales_records` 200） |
| 2026-09-23 | 同上 | 同上 / 演示站 8147 | 销售列表 / 采购列表 / 客户列表表头 + 销售新建弹窗字段标签 | 销售与采购首列都读「单据编号」（改前「编号」）；销售弹窗「单据编号 / 客户 / 客户名称 / 客户类型」；客户列表「客户名称 / 客户类型 / 税号 / 电话 / 地址 / 状态」不变 | 术语归一到页面可见层，不是只改词表 |
| 2026-09-23 | 同上 | 宿主 + 两站 | `check-architecture.py` / `i18n-build.py --check` / `tests/test_erp_portal_access.py` / `check-i18n.py --strict` / `node --check`（3 个 JS）/ `py_compile`（5 个 py）/ 两站 `bench migrate` / 检查站三个探针 | `PASS (0 violations)` / `RESULT: PASS`（erp **809** 条）/ 102 条 OK（6 跳过）/ 合计 **113**（py-bare-cjk 18，与基线逐项相同）/ JS 与 PY OK / migrate 退出码 0 / 三个探针 `problems: {}` 与 `result: PASS` | 术语表、边界、权限契约、迁移与数据流都过了一遍；本次是导出措辞 + 员工页，不涉业务契约 |

| 2026-09-23 | F24 改动（未提交时） | 演示站 `erp-pos-a.localhost`（内嵌浏览器）+ 两站服务端 | 收起态量左侧条目 → 点左下角那颗按钮 → 点「团队管理」/「个人信息」 → 再点一次收起 | 收起态：首页 + 三组（**无团队管理、无主数据组**）；点开后条目区正好 `个人信息 / 团队管理`；两者各自到 `/desk/pearnly-team`（页面 8 / 8 行）与 `/desk/user/…`，**侧栏都仍是产品那份**；再点一次画回原菜单 | 用户第二轮口径：点左下角 → 左侧只剩这两项（不是浮层） |
| 2026-09-23 | 同上 | 演示站（服务端 + HTTP） | 用 ERP Sales 岗位建临时账号 → 登录 → 取 desk 页面里的 boot → 删账号 | `pearnly_user_menu: []`、`pearnly_pages` 无 `pearnly-team`；账号与 `Pearnly Session` 已删，员工名单回 8 人 | 非管理员连这一项都看不到（判据与页面准入同一条） |
| 2026-09-23 | 同上 | 演示站（内嵌浏览器） | 进「侧栏排序」编辑态 → 点保存 → 再进一次点放弃 | 改前保存回"值无效"（编辑器只提交顶层项）、成功后子项会被画两遍；改后两条路都干净、分组不重复；测试写下的个人偏好行已删 | 这两处是改之前就有的毛病，这轮顺手修掉 |
| 2026-09-23 | 同上 | 宿主 + 两站 | `check-architecture.py` / `i18n-build.py --check` / `check-frontend-i18n.py` / `tests/test_erp_portal_access.py` / `check-i18n.py --strict` / `node --check`（4 个 JS）/ `py_compile` / 两站 `clear-cache` + `ensure_navigation` | `PASS (0 violations)` / PASS（erp 809）/ 0 问题 / 102 条 OK（6 跳过）/ 合计 **113**（与基线逐项相同）/ JS 与 PY OK / 退出码 0 | 声明键、术语表、边界与权限契约都没破 |

| 2026-09-23 | F25 改动（未提交时） | 演示站 `erp-pos-a.localhost`（内嵌浏览器）+ 检查站 `erp-pos-b.localhost`（探针） | 个人信息页字段只读态 → 团队页按钮/表头 → 编辑弹窗三类字段 → 三次岗位保存（改、原样、取消） | 用户名/邮箱只读、语言/时区/静音可改、「更改密码」出现；团队页按钮「添加用户」、整页无"员工"；弹窗里只有岗位可改；保存三次的结果分别是 `收银 · 采购`、原样保持、回到 `采购`，名单始终 `8 / 8` | 用户口径"不要出现员工" + "用户名和邮箱绑死、只能改密码"；顺带修掉"原样保存会丢岗位"的真 bug |
| 2026-09-23 | 同上 | 检查站 `erp-pos-b.localhost` | `PEARNLY_ERP_POS_LAB=1 PEARNLY_ERP_POS_LAB_SITE=erp-pos-b.localhost python scripts/erp-pos-lab-team-verify.py` | `problems: {}`；新增断言 `resave_same_roles_kept = ["ERP Purchase"]` | 探针本身此前是坏的（`add_member` 缺初始密码），这轮补齐；隔离站上岗位保存不再丢 |
| 2026-09-23 | 同上 | 宿主 + 两站 | `check-architecture.py` / `i18n-build.py --check` / `check-frontend-i18n.py` / `tests/test_erp_portal_access.py` / `check-i18n.py --strict` / `py_compile` / `node --check` / 两站 `ensure_profile_fields` + `clear-cache` | `PASS (0 violations)` / PASS（erp **815** 条）/ 0 问题 / 102 条 OK（6 跳过）/ 合计 **113**（与基线逐项相同）/ 语法全过 / Property Setter 回读 `read_only=1` | 术语表、边界、权限契约都没破；用户名只读那条落在站点上 |

| 2026-09-23 | F26 改动（未提交时） | 宿主 + 两站 | `node --check company.js` / `py ast` / `check-architecture.py` / `i18n-build.py` + `--check` / `check-frontend-i18n.py` / `tests/test_erp_portal_access.py` / `check-i18n.py --strict` / `tests/test_native_desk_contract.py` / 两站 `clear-cache` + `ensure_navigation` + web 重启 | JS 与 PY 语法过 / `PASS (0 violations)` / PASS（erp **842** 条）/ 0 问题 / 102 条 OK（6 跳过）/ 合计 **113**（与基线逐项相同）/ 原生契约 18 条 2 失败 / 两站退出码 0 | 那个 2 失败**与基线相同**：我在 `/tmp` 用 `git worktree add --detach HEAD`（提交 `e2a331b`）跑同一支测试，基线是 17 条 4 失败（`save_sidebar_order` 那两条与本轮无关，是更早那批留下的加 `pages` 字段引起的）；本轮把产品清单 17→18、`available` 清单与真实一致两处**按现状对齐**（不是放宽断言），另加两条新断言：Desk 页面用到的文案命名空间必须都登记在 `browser_text.APP_BY_PREFIX`（`company.` 就是这么漏的）、`company/team/supplier/client/sales` 的键必须四语齐全 |
| 2026-09-23 | 同上 | `pearnly-purchase-lab` / `erp-pos-a.localhost` | `bench execute pearnly_core.desk.navigation.user_menu` + `pearnly_erp_portal.masters.api.api_get_profile` | `[{route: pearnly-company, before_profile: true}, {route: pearnly-team, before_profile: false}]`；资料 = `CO-00552 Purchase Preview`、`can_write: true`、`can_lookup: true` | 菜单声明与资料接口的服务端回读 |
| 2026-09-23 | 同上 | 内嵌浏览器 / 演示站 8147 | 菜单顺序与底色、页面标题、字段面、按钮 | 侧栏正好 `公司 / 个人信息 / 团队管理`，停在公司页时**公司**高亮、停在个人信息页时**个人信息**高亮；标题与面包屑都是「公司」；12 个字段标签全中文、**没有任何说明文案**；按钮 = 保存 + 查询 | 用户四条口径里的"只写公司""做在个人信息上方""不要傻逼文案"逐条对上 |
| 2026-09-23 | 同上 | 同上 | 税号空着点「查询」→ 填 13 位再点 | 空着提示「请先填税号」；填了之后提示「现在连不上税务局」（本实验室容器出不了外网，税务局只在宿主上通）—— 失败分支正确、页面与字段不受影响，没有抛异常 | 回填分支在本环境**验不到**（同 F14 的限制）；接口本身在页面里直接调过：`{ok: false, error: "rd_unreachable"}` |
| 2026-09-23 | 同上 | 同上 | 改备注 + 电话 → 保存 → 回读；再清空两格 → 保存 → 回读 | 第一次写进 `phone=021234567`、`note=F26 验证用…`；**改前**清空保存后回读还是老值（`get_values()` 丢空值）；**改后**清空保存回读 `NULL / NULL` | 顺带把税号也清回 `NULL`，记录回到开工原样（见收尾） |
| 2026-09-23 | 同上 | 同上 | 静音开关两向（勾上 → 保存 → 读 boot 与 `<audio id="sound-submit">`；取消 → 保存 → 再读） | 勾上保存后 `boot.user.mute_sounds = 1` **当场生效**（不用刷新），`frappe.utils.play_sound("submit")` 后 `paused: true`（没响）；取消保存后 `= 0`，同一句 `play_sound` 后 `paused: false`、`currentTime` 走到 0.317（真响了） | 回答用户"那个静音是不是假的"：开关是真的，之前只是**要刷新才生效**；改完两向都即时 |
| 2026-09-23 | 同上 | 同上 | 表单里实际提交的字段（页面控制台临时包一层 `frappe.call` 记录一次请求体） | `api_update_profile {"company_name":"Purchase Preview","tax_id":"…","fiscal_year_start_month":1,"note":"ZZZ-临时"}` —— **空格子不在里面**，这就是"清空不生效"的证据 | 定位手段只记录、不改行为；页面随后导航过，包的那层已不在 |

| 2026-09-23 | F27 改动（未提交时） | `pearnly-purchase-lab` / `erp-pos-a.localhost`（内嵌浏览器 + CDP） | 读官方源码回答四个问题；改完看个人信息页与公司页 | 官方：`Company.is_group` + `parent_company`（树）、`Sales Invoice.company` = `Link→Company` **reqd=1**、`Fiscal Year` 是独立单据（`year/year_start_date/year_end_date/companies`）、`Document Naming Settings` 是单号那套；**公司上没有"单据前缀"也没有"财年起月"**。改后：个人信息页出现「公司名 = Purchase Preview / 您的访问域名 = http://erp-pos-a.localhost」两行（改前这两行被框架的 `hide_empty_read_only_fields` 藏掉了）；公司页字段 = 公司名称/简称/税号/查询/分支/电话/联系人/联系邮箱/PromptPay/地址/备注（**没有**单据前缀与财年起月），分区 = 公司信息 / 地址与备注 | 用户"官方有哪些"的口径逐条对过；"少一个域名显示"根因是官方那条"只读且空值就藏行"的规则 + 我们只在身份服务站点给值 |
| 2026-09-23 | 同上 | 宿主 | `node tests/*.cjs`（10 支）/ `check-architecture.py` / `i18n-build.py` + `--check` / `check-frontend-i18n.py` / `tests/test_erp_portal_access.py` / `check-i18n.py` / `tests/test_native_desk_contract.py` | **10 支 JS 全绿**（改前 `test-native-desk` / `test-field-rows` / `test-stock-card` 三支从 F24 起整支加载即抛错）/ `PASS (0 violations)` / PASS（erp **839** 条）/ 0 问题 / 102 条 OK（6 跳过）/ 合计 **113**（与基线逐项相同）/ 2 失败（与基线同一批） | 三支 JS 测试只是假环境缺 `frappe.ui.Sidebar`（`0a9b21e` 引入），补上即恢复；顺带给"静音保存当场生效"加了断言。`tests/test_product_feature_catalog.py` 的 3 个 error 是**宿主 Python 3.9 跑 `dict | None` 注解**所致，与本次改动无关（容器里 3.11 正常） |

| 层级 | 状态 | 依据与未覆盖项 |
| --- | --- | --- |
| 实现 | 已完成（去开票侧 + 销售前端与一次提交 + 删草稿授权与文案收尾 + 收款付款弹窗与状态口径 + 供应商/客户菜单 + **税号查税务局** + 两页**多选/全选** + **主按动作的提示与音效** + **状态列两态** + **表头计数折行** + **上传框去掉库/链接** + **POS 开班交班**） | 旧销售页残留的「收款状态/备注」等字段面与采购一致；未做修订（见上）；删草稿不带私有附件清理（见 F1）；界面上没有再改已收付金额的入口（见 F3）；税号查询只做了"查登记带回字段"，没有做"按登记反查该税号下有哪些单据"（旧线上也没有）；"取消/作废"与采购"代收据/预扣税凭证"两处提示只按同一条代码路径补上，**没有**在浏览器里逐个点（前者在演示站作废后不可撤销，后者会往单据上挂一个文件）；POS 只做了开班/交班/当班小票与班次记录，**没有**做收银台的收银员交接签字、验钞、多收银台切换、班次打印小票（用户口径是"简单的开班交班"） |
| 本地验证 | 通过（离线 + 真实 Site + 桌面/414px 浏览器 + 真实私有附件 + 界面删草稿实删 + LINE 待处理页 + 收款付款真的写库 + 税号查询的缓存与岗位边界 + 批量启停真的写库 + 提示条与提示音逐条盯过 + POS 开班/交班/差额标红/班次记录在界面走完）+ 12 个列表页的计数格逐页量过 | 未覆盖：真机、真实付费识别（本环境无 OCR 凭据，识别走响应替身）、真实 LINE 发送（页面走真实接口造的草稿，不是 LINE 通道发来的）、生产部署、用户验收；LINE 草稿对员工角色不可见/不可确认（F2 第 3 条，归 intake 切片）；**浏览器里"点查询→字段被填上"这一步在本实验室验不到**（容器出不了外网，只验到失败分支），回填走的是本仓既有 `dialog.set_values`；"取消"与采购凭证那两处提示未在界面点过（见上）；**POS 的收款（收银开单）这一路本次没在界面点**（演示样本是走服务端服务路径造的），下次验收时可以顺手点一遍 |
| 部署 | 未执行 | 本任务未获部署授权 |
| 外部业务结果 | 未执行 | 不调用旧生产、不付费识别、不发 LINE |
| 用户验收 | 未执行 | 采购与销售都未由用户验收，本记录不代签 |

> F21 对上表的补充（2026-09-23）：「实现」再加一条 —— **列表勾选框按官方默认打开**（收银记录→批量作废、交班记录→导出选中、LINE 待处理→批量取消）；「本地验证」再加一条 —— 13 个列表页的勾选框逐页量过、交班记录导出取到真 xlsx（`HTTP 200`、openpyxl 读回两行班次）、LINE 批量取消真写库并带原因。

> F22 的补充（2026-09-23）：「实现」再加 —— **收银台与交接班界面按 ERPNext 1:1 重做**（六块骨架 / 商品卡 / 明细面板 / 只在选中行出现的键盘与它的目标高亮 / 收款面板 + 3 列键盘 + 总计·付款·剩余·找零 / 最近订单与汇总 / 开班交班的按支付方式对账表 + 新子表 `ERP Shift Balance`）；「本地验证」再加 —— 官方基准值逐块对过（1440 视口）、演示站上真卖一笔联动到收发存、按支付方式的对账写路径在隔离检查站跑通、两站 migrate、离线四项检查。「用户验收」仍未执行；**开班弹窗与窄屏没在浏览器里点过/量过**（原因写在 F22 第四节）。

> F23 的补充（2026-09-23）：「实现」再加 —— **导出件的术语口径统一**（术语表带 key 的词条按英文原文再落一行；服务端补按键取词的 `browser_text.label`；销售 / 采购 / 交班 / 库存四份导出件表头全中文且无重复列；采购列表与表单的「编号」统一成「单据编号」；`Customer`=客户、`Customer Name`=客户名称）+ **租户员工与权限页**（框架 `User`/`Role`、原生 MultiCheck 与 Password、添加 / 改名改岗 / 重置密码 / 停用启用 / 批量启停，页面文档在 core、渲染在 erp_portal）；「本地验证」再加 —— 四份导出件用真会话取回并在 bench 里读表头、员工页在浏览器里把添加→改→停用→启用走完、新账号真登录（停用 401 / 启用 200）与越权 403 回读、两个列表与销售弹窗的标签在页面上量过、两站 migrate、离线五项检查与检查站三个探针。**未验到**：真租户站（带身份服务）的建号与停用、员工页窄屏与真机、采购导出里 `override_*` 那三对同名列的措辞（留给用户定，见 F23 第六节）。

> F24 的补充（2026-09-23）：「实现」再加 —— **「团队管理」从侧栏移进左下角那颗用户按钮**（产品在 `pearnly_navigation` 里声明 `user_menu`；点开时左侧条目区换成「个人信息 + 产品声明的项」，再点一次或换路由画回去；`My Profile`=个人信息）；顺带钉住三处脆弱点（被移出侧栏的页面会让框架把侧栏换成模块那份 → `Sidebar.setup()` 目标钉成产品自己那份；侧栏文档里的旧分组字段会让整条侧栏崩成一项 → 平铺项重写 `child/indent`；「侧栏排序」保存一直报"值无效"且保存后子项画两遍 → 铺平提交 + 保存后先 `prepare()`）。「本地验证」再加 —— 收起态与点开态逐项量过、两条入口的去向与侧栏都在浏览器里走过、非管理员账号的 boot 里没有这一项、侧栏排序保存与放弃两条路都干净、两站 `clear-cache` + `ensure_navigation`、离线五项检查。**未验到**：窄屏与折叠态下那两项的观感、真机（见 F24 第四节）。



> F25 的补充（2026-09-23）：「实现」再加 —— **身份规则统一成"用户名/邮箱开户时发号、绑死租户，之后谁都不改，只能改自己的密码"**（core：用户名 DocType 级只读、删掉用户改名那条路、身份服务通道只剩语言、本地站点也能进改密页；ERP 团队页：文案改「用户/添加用户」、编辑只给改岗位；DMS：花名册的用户名改成只读、`save_credentials` 只收密码）；顺手修掉 `set_member_roles` "原样保存会把岗位点没"的真 bug（`held` 在摘角色之后才取）。「本地验证」再加 —— 个人信息页只读态与改密按钮、团队页文案与弹窗三类字段、三次岗位保存（改/原样/取消）都在浏览器里点过、检查站团队探针 `problems: {}`（探针此前是坏的，这轮补齐）、两站 `ensure_profile_fields` + `clear-cache`、离线五项检查。**未验到**：云站点走身份服务的改密与唯一性、DMS 界面（本机没有 DMS 站点）、前端 `vue-tsc`（依赖没装且本机 npm 坏了）—— 见 F25 第六节。
## 收尾：交接保留

- F27 在演示站的痕迹：**没有动任何数据**（只读源码 + 改代码 + 浏览器看页面）。公司记录 `CO-00552` 仍是 F26 还原后的样子；`pearnly_company` / `pearnly_domain` 两格的值来自站点配置（`pearnly_company_name`、`get_url()`），没有写库。演示站浏览器最后停在个人信息页（用户当时在看的那一屏）。
- F27 改了 `account/profile.py` 的一处**判据**（不再要求 `pearnly_native_account` 才发公司/域名），影响面是全产品站点的个人信息页：云站点上本来就是 `host_name` 的值，观感不变；本地/非身份服务站点从此也有这两行。要回退就是那一行。
- F26 在演示站的痕迹**已还原**：公司记录 `CO-00552`（`Purchase Preview`）中途为验证写过 `phone=021234567`、`note=F26 验证用…`、`tax_id=0105546015062`，**都已清掉**，最后一次回读是 `company_name=Purchase Preview`、`fiscal_year_start_month=1`、其余字段 `NULL`（只有 `modified` 时间戳变了），与开工时 `api_get_profile` 的第一份回读逐字段一致。没有新建 / 删除任何公司，也没动默认公司标记。
- F26 的静音开关：开工时数据库里就是 `mute_sounds=0`（上一条交接写的是"我留成开了"，实测不是 —— 以数据库为准）；本轮为验证勾上、取消各一次，**结束在 0**，与开工一致。`ERP Tax Lookup Cache` 回读 **0 行**（容器连不上税务局，失败分支按设计不写缓存）。
- F26 的浏览器标签：最后一个标签停在 `/desk/pearnly-company`（用户当时正在看的那一屏）。为抓一次真实请求体，我在该标签的控制台里临时包了一层 `frappe.call`（只记录、不改行为），页面导航后包的那层已消失、没有其它改动留在页面上。
- F26 为核对"测试文件在基线就是红的"，在 `/tmp/pearnly-baseline` 建过一个 `--detach` 的临时工作树（`git worktree add … HEAD`，提交 `e2a331b`），**已 `git worktree remove` 删掉**，本仓内没有残留引用。

- F23 在演示站的痕迹：为验收「添加员工」这条闭环，从界面真建了一个临时账号 `selftest-browser@example.test`（自测临时员工 → 改名自测临时员工二，岗位收银 → 销售，走完改名/改岗/重置密码/停用/启用），**验完已删**（先删掉它自己的 `Pearnly Session`，再删 User；回读 `frappe.db.exists` 无、名单回到 8 人，与 F23 开工时一致）。**保留**：上一窗口留下的自测员工 `selftest-cashier@example.test`（显示名「自测销售员」，岗位 销售＋库存，启用中）—— 它是员工页的演示样本，要清就说一声。
- F23 的术语改动是代码层（术语表 / DocType 标签 / 列表列名），随 `bench migrate` 落到站点，没有改任何业务数据；两个站点都已 migrate、缓存已清、web 已重启。
- F24 在演示站的痕迹：为验"非管理员看不到这一项"建过一个临时账号 `selftest-menu@example.test`（ERP Sales），**验完已删**（连同它的 `Pearnly Session`，回读员工名单 8 人）；点「侧栏排序 → 保存」写下的个人偏好行（`DefaultValue`，值就是原顺序）**也已删掉**，回到开工时没有这行的状态。侧栏的改动是代码 + 站点文档（`ensure_navigation` 重写 `Workspace Sidebar`，把「团队管理」从分组里去掉），两站都跑过。
- F25 在演示站的痕迹：为验"保存岗位"把夹具 `erp-pos-line-buyer@example.com` 的「采购」点没了一次（就是这一轮修掉的那个 bug），**已用同一条服务路径改回**，最后一次回读是 `ERP Purchase` + `Desk User`、该行显示「采购」，与开工一致；没有新建或删除任何账号。两站都跑过 `ensure_profile_fields`（用户名只读那条 Property Setter）与 `clear-cache`，web 已重启。
- **本窗口自己的失误，如实记**：收尾跑回归探针时漏了 `PEARNLY_ERP_POS_LAB_SITE`，`scripts/erp-data-flow-lab-verify.py` 于是落在**演示站** `erp-pos-a.localhost`（它按 `SITE` 默认值取站点，只由 `.erp-pos-lab` 标记把关，不认"这是演示站"），在 `2026-09-23 05:28:46` 一口气造了一整套 `FLOW-` 夹具。
  - **已收拾**：探针建的 3 个账号 `flow-purchase / flow-cashier / flow-sales@example.com` 已删除（先删各自的 `Pearnly Session`，再删 User），员工名单回读 **8 人**，与 F23 开工时一致。
  - **未删（属数据删除，本任务没有自主授权）**：仓库 `WH-00510`（`FLOW-… 自填仓库`）、商品 `PRD-00509 / PRD-00536`、已过账盘点 `STC-00530`、已提交入库 `STE-2026-00534`、开着的班 `SHIFT-2026-00521`（收银员账号已随账号删除而消失）、已作废的 `PUR-2026-00511 / SD-00517 / SALE-2026-00522 / SALE-2026-00525 / STE-2026-00514`，以及它们带出的 16 条流水 `STM-2026-00512…00539`。清理路径已想清楚（`STE-2026-00534` 走入库作废、`STC-00530` 是已过账盘点、`WH-00510`/`PRD-0050x` 与流水互相引用），**等用户点头再做**。
  - 教训记在这：跑 `scripts/erp-*-lab-verify.py` 一律显式带 `PEARNLY_ERP_POS_LAB_SITE=erp-pos-b.localhost`，否则默认值是**演示站**。

- 保留：采购工作树 `/Users/skin/.codex/worktrees/erp-purchase-records/pearnly` 及分支 `codex/erp-purchase-records`（含采购成果与本次交接，未合并主线，作为销售基线与回退挂载来源）；销售工作树与本任务分支；`pearnly-purchase-lab` 六个容器、`sites`/`db` 卷、合成样本与私有附件（继续用于演示与验收）。解除条件为销售成果按授权集成、采购完成用户验收、且核验无挂载与独有提交。
- 本次在演示站**删掉**了上一窗口为「删草稿」检查建的合成草稿 `SD-00206`（它没有附件，站内也不再有它的私有文件）；`SD-00204`（含私有附件）保留供用户与下一窗口看，状态已回读确认与原样一致（已收款 214 / 现金 / 2026-09-22）—— 中途为验 F3 改过（部分收款、全额、日期），最后一次已用同一条服务路径改回原值。
- F3 期间在演示站改动过的采购样本 `PUR-2026-00190`（`PE2569-000070`）已改回未付（`paid 0 / is_paid 0 / unpaid`），与本次开工时一致；`PUR-2026-00183`（`PE2569-000066`）也在试弹窗时改过，同样已改回未付。
- F5 的销售演示样本（6 客户 / 6 商品 / 6 张入库单 / 22 张销售单据 / 2 个私有附件）保留在演示站，供用户与下一窗口看；它们不是临时文件，按样本批次编号处理。用户在浏览器里看到的 `SD-0020x` 是内部名，界面用的是系统号（`SI2569-0000xx`）。
- 误跑一次默认站是演示站的旧 LINE 探针（F2 第 4 条）之后，把它在演示站产生的 3 条草稿（`LI-00209/210/211`）与 6 个私有文件清掉；用于看 LINE 页面的 `LI-00212` 也连同 3 个文件删除。回读演示站：`ERP LINE Intake` 9 条（都是采购方向已确认的历史样本）、intake 文件 18 个、销售单据只有 `SD-00204`（附件 1 个）、销售方向待处理 0 条 —— 与本次开工时一致。
- 原 stocktake / erp-pos / Cowork 工作树与容器归各自任务，本任务只读查看，未取得清理授权。
- 本任务未创建远端分支；未创建临时夹具或额外服务。
- F14 在演示站只做了**可逆**的批量启停（客户 7 位、供应商前 2 家）：两次方向相反的批量动作后已回读全部 `is_active=1`，与开工时一致；税号查询的演示缓存行没留下（容器连不上税务局，失败分支不写缓存），探针在 `erp-pos-b` 写的缓存行由探针自己删掉（`tax_cache_rows_left 0`）。浏览器会话已恢复到列表页、无弹窗、无选中。
- F15 在演示站的改动**全部已还原**并回读确认：销售 `SD-00255` 回到 `unpaid / 0 / 方式日期备注全 NULL`；采购 `PUR-2026-00190` 回到 `unpaid / 0`；为"删草稿"检查造的合成草稿 `SD-00484` 已由界面删除，回读 `count=0`，销售单据回到 `submitted 22 / void 1`。为造夹具在 `bench console` 里临时改写过的 `owner`（那条草稿）随该单一起消失，没有其它记录被动过。浏览器视口已复位（414×844 → 默认）。
- F16 在演示站**新增**了 POS 演示样本（1 个收银台 + 2 个班次 + 3 张小票，见 F16 第三节），并删掉自己误开的一个空班。用户点「交班」会作用在**班次 2**（开着、3 笔单、应有 1,039）—— 交完就成了一条真实记录，需要清掉时说一声。样本消耗的库存：`PRD-00219` 2 瓶、`PRD-00223` 1 包、`PRD-00224` 1 罐（都走出库流水，与销售记录一致）。
- F17 在演示站又落了**一笔**收银小票（`SALE-2026-00495` / `SI2569-000024`，`PRD-00220` × 2、现金 100、找零 10），用来验证单号接在销售记录后面、以及收发存里带上同一个号；它在**班次 2** 里（该班现在 2 笔、应有现金 1,129）。要清掉说一声（作废会把库存还回去）。隔离检查站 `erp-pos-b` 上另有两笔验证单（`SALE-2026-00169` 销售 + 它的一笔退货），是检查站自己的数据。
- F18 又在演示站的**班次 2** 里从界面上卖出一笔（`SALE-2026-00500` / `SI2569-000025`，`PRD-00220` × 1、现金 100、找零 55），用来验收银台整条链；同时把当天那两笔历史单号仍是 `R…` 的原样保留。演示站的班次 2 现在 3 笔（应有现金随交班界面实时算）。用户点「交班」后这些都会成为交班记录的一部分。
- F21 在演示站的痕迹**已清干净**：为验 LINE 批量取消造的 2 条 sales 向草稿 `LI-00501 / LI-00502` 与它们的 4 个附件文件已删（回读 9 条草稿 / 18 个 intake 文件，与开工一致）；收银记录的批量作废只到确认框就点了「否」，`ERP Sale` 6 条仍是 `completed`。**另记**：演示站收银台现在是**班次 3**（`SHIFT-2026-00503`，开班 04:43、备用金 1,000），班次 2 已于 04:39 交班（实点 1309 = 应有 1309、差额 0）—— 这两步不是本窗口做的，按既有记录保留、未还原。
- F22 在演示站留下的东西：新收银台照官方重做后从界面上真卖了 **1 笔**（`SALE-2026-00507` / `SI2569-000028`，`PRD-00224` ×2 @39、现金 78、找零 0，在班次 3 里，消耗库存 2 → 该商品结存 53 → 51）；另有 1 笔自测单 `SALE-2026-00504`（`SI2569-000027`，`PRD-00410` ×1）**已作废**（`void_reason="自测：该商品本来没库存，作废还原"`，库存回到 0，单据以 `void` 留痕）。这两笔都是真实业务记录，**没有删**；要清掉说一声（作废/删除都要走业务路径）。
- F22 在**隔离检查站 `erp-pos-b`** 上新增：收银台 `TERM-00173`（自测用）、班次 `SHIFT-2026-00174`（已交班）、一笔 `SI2569-000035`（现金 50 + 刷卡 20）。它们只服务于"按支付方式对账"这条写路径的验证，站点本身就是检查站；要不要清掉由用户定。
- F22 读官方源码时把 `apps/erpnext` 的 POS 源码与样式表复制到宿主 `/tmp/pos-official.<随机>/` 做只读比对，**没有进仓库**（容器里随时能再拷一份，属于可再生的临时件）。
