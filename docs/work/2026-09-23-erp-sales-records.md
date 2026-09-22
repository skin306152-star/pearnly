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

| 层级 | 状态 | 依据与未覆盖项 |
| --- | --- | --- |
| 实现 | 已完成（去开票侧 + 销售前端与一次提交 + 删草稿授权与文案收尾 + 收款付款弹窗与状态口径 + 供应商/客户菜单 + **税号查税务局** + 两页**多选/全选** + **主按动作的提示与音效** + **状态列两态** + **表头计数折行** + **上传框去掉库/链接** + **POS 开班交班**） | 旧销售页残留的「收款状态/备注」等字段面与采购一致；未做修订（见上）；删草稿不带私有附件清理（见 F1）；界面上没有再改已收付金额的入口（见 F3）；税号查询只做了"查登记带回字段"，没有做"按登记反查该税号下有哪些单据"（旧线上也没有）；"取消/作废"与采购"代收据/预扣税凭证"两处提示只按同一条代码路径补上，**没有**在浏览器里逐个点（前者在演示站作废后不可撤销，后者会往单据上挂一个文件）；POS 只做了开班/交班/当班小票与班次记录，**没有**做收银台的收银员交接签字、验钞、多收银台切换、班次打印小票（用户口径是"简单的开班交班"） |
| 本地验证 | 通过（离线 + 真实 Site + 桌面/414px 浏览器 + 真实私有附件 + 界面删草稿实删 + LINE 待处理页 + 收款付款真的写库 + 税号查询的缓存与岗位边界 + 批量启停真的写库 + 提示条与提示音逐条盯过 + POS 开班/交班/差额标红/班次记录在界面走完）+ 12 个列表页的计数格逐页量过 | 未覆盖：真机、真实付费识别（本环境无 OCR 凭据，识别走响应替身）、真实 LINE 发送（页面走真实接口造的草稿，不是 LINE 通道发来的）、生产部署、用户验收；LINE 草稿对员工角色不可见/不可确认（F2 第 3 条，归 intake 切片）；**浏览器里"点查询→字段被填上"这一步在本实验室验不到**（容器出不了外网，只验到失败分支），回填走的是本仓既有 `dialog.set_values`；"取消"与采购凭证那两处提示未在界面点过（见上）；**POS 的收款（收银开单）这一路本次没在界面点**（演示样本是走服务端服务路径造的），下次验收时可以顺手点一遍 |
| 部署 | 未执行 | 本任务未获部署授权 |
| 外部业务结果 | 未执行 | 不调用旧生产、不付费识别、不发 LINE |
| 用户验收 | 未执行 | 采购与销售都未由用户验收，本记录不代签 |

## 收尾：交接保留

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
