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

**还没做（S2/S3）**：把录入做成采购那套「一次提交即落定 + 连续页面 + 同页复核 + 附件区/查看器/手机滚动定位」；届时确认动作与前端一起重做。采购的草稿/删除规则不套到销售。

- **销售探针已改成非破坏性**：原 `scripts/erp-pos-lab-sales-verify.py` 会遍历删除 Site 内全部 `ERP Sales Document`（`frappe.delete_doc(..., force=True)`）。现已重写为「样本只建在隔离检查站 `erp-pos-b.localhost`、演示站 `erp-pos-a.localhost` 只读回读、不删除任何记录」，并在断言里包含税票/冲正单类型一律被拒。
- 当前 8147 没有 OCR 凭据：只能验证“未配置时原生提示拒绝”和带明确标记的响应替身交互；真实付费识别、真实 LINE 发送、生产环境、手机真机触摸都不能据此声称通过。
- 采购的“全部为草稿才可删除”是采购规则，不直接套销售；批量动作必须按销售自身状态与角色判断。
- 销售出库方向与采购入库相反，收款方向与付款相反，**不得通过改 API 前缀复用采购事务**。
- 采购尚无用户最终验收声明，本任务不代签，也不得破坏其可访问样本（`PUR-2026-00202` 等）。

## 变更与决定

- 本次仅接手：创建销售工作树、切换 8147 源码挂载、重做线上只读核验、建立本记录。无代码变更。
- 复用共享层（Core 原生列表/路由/布局、共享 OCR、登录、外壳）不重写；销售自有声明仍写在 ERP App。

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

| 层级 | 状态 | 依据与未覆盖项 |
| --- | --- | --- |
| 实现 | 已完成（去开票侧） | 采购那套连续页面/同页复核/附件区尚未做（S2/S3） |
| 本地验证 | 通过（离线 + 真实 Site + 桌面浏览器） | 未覆盖：真机/移动宽度、真实付费识别、真实 LINE、生产 |
| 部署 | 未执行 | 本任务未获部署授权 |
| 外部业务结果 | 未执行 | 不调用旧生产、不付费识别、不发 LINE |
| 用户验收 | 未执行 | 采购与销售都未由用户验收，本记录不代签 |

## 收尾：交接保留

- 保留：采购工作树 `/Users/skin/.codex/worktrees/erp-purchase-records/pearnly` 及分支 `codex/erp-purchase-records`（含采购成果与本次交接，未合并主线，作为销售基线与回退挂载来源）；销售工作树与本任务分支；`pearnly-purchase-lab` 六个容器、`sites`/`db` 卷、合成样本与私有附件（继续用于演示与验收）。解除条件为销售成果按授权集成、采购完成用户验收、且核验无挂载与独有提交。
- 原 stocktake / erp-pos / Cowork 工作树与容器归各自任务，本任务只读查看，未取得清理授权。
- 本任务未创建远端分支；未创建临时夹具或额外服务。
