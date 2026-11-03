# 需求影响评估：CC 通用疑难处理（直写 + 代录）

> 分支 `pearnly/cc-general-troubleshooting`，工作树 `../pearnly-wt-cc-troubleshooting`，基线 `origin/main` = `9a6a60112`。
> 现状盘点（带文件:行号）在共享笔记 `CC通用疑难处理-现状盘点-11-03.md`；本文只引用要点，关键几处已逐条复核。

## 0. 需求原文与理解

- 原话（用户 11-03，中文，经主会话转达）：
  - 「cc 是否具备这种中途补料续接的流程……纸上印着『1/2』但后面那页不在文件里，就明确标『缺页』，不让它带着不完整的金额写进账」；
  - 「我们现在不是有待处理吗，用户在待处理那里继续作业……cc 接着干」；
  - 「所讨论的这些，直写和代录希望都覆盖上，而且我不希望只是遇到缺页就处理缺页……能查到问题，能问能答能做」。
- 理解：不按情况加规则，分两层。
  1. **代码只守管钱的底线**（通用护栏）：不能闭合、缺必填、身份对不上、被对方拒收或结果不明的单，一律不写账，进「待处理」并留证据。
  2. **判断根因、想办法补交给 CC**：给它能查、能问答、能做的通用工具，加只写原则的 GUIDANCE。
  - 缺页是第一个验收用例，不单独写「缺页规则」。
- 不在范围：
  - 识别读法、提示词、模型都不改（第 1.3 节用数据说明为什么不加「认页码」）；
  - 不新增入口；
  - CC 侧栏导航（`assistant.tsx` 的 `PendingNav`、`projects.tsx`）不改；
  - MR.ERP 代录执行层（不在 main、开关关着、等客户账号）不在本次实测范围。

## 1. 第一层：通用护栏（代码里管钱的底线）

### 1.1 现有拦截盘点（已核实）

| 情况 | Cowork → Express 直写 | Cowork → MR.ERP 直写 | ERP → Express | ERP → MR.ERP |
| --- | --- | --- | --- | --- |
| 缺总额 | 拦（`express_rules/push/common.py:497` `amounts()` → `amounts_not_consistent`） | **采购照写**：`mrerp/push/xlsx/purchase.py:155` 缺总额用「小计 + 税额」顶上；赊销拦（`ERR_NO_TOTAL_AMOUNT`） | ERP 单据合计由明细算，印的合计缺失不影响写入（见 1.4） | 同左 |
| 小计 + 税额 ≠ 总额 | 拦（同上，容差 0.02） | **不查**，只有隐含税率不在 5%–9% 才拦（`generator.py:155`） | 拦（`erp_portal/express/contracts.py:281` `checked()`） | 不查 |
| 只印总额 | 按 7% 含税反推，照写（`common.py:518`） | 照写 | — | — |
| 明细合计 ≠ 税前额 | **照写**，退回只写表头（`common.py:147` `ITEMS_MISMATCH`），不进待处理 | 不查 | 拦 | 不查 |
| 缺日期 | 拦 | 拦 | 拦 | 拦 |
| 缺单号 | 完整税票拦，收据 / 简式税票照写 | 赊销拦，采购不查 | — | — |
| 身份对不上（本方税号 / 分店 / 期间、买卖同税号、外币等） | 拦（`push/application/service.py:369`、`doc_sanity.py:96`） | 账套税号匹配闸（`transport.py:205`） | 账套归属 | 同左 |
| 对方拒收 / 结果不明 | 进待处理（`failed` / `result_unknown`），CC 有 `cowork_resolve_push_result` | 进待处理（`mrerp.result_unknown`） | 进 ERP 待处理（`held` / `failed` / `mismatch`） | 同左 |
| 代录（Express operator） | 三级分级（`express_operator/contracts.py:18`）：必须人工的单停下、出「需要你」 | MR.ERP 代录不在 main | — | — |

离线实测（11-03，本机直接调这几个纯函数，不连站点、不写账）：
- BJC 第 1 页那张（缺第 2 页，金额全空）：`amounts()` → None，Express 直写拦；
- BJC 第 7 页那张（小计 38869.00、税 2720.83、无总额）：Express 直写拦，**MR.ERP 采购会按 41589.83 照写**；
- 「只有总额 1070」→ 反推 1000 + 70 照写；
- 「明细合计 500、税前 1000」→ `mismatch`，退回只写表头、照写。

### 1.2 统一护栏（新增一个共用判定，四条直写路与代录建任务前都调）

新增 `pearnly_core.tax_documents.ledger_guard.check(doc) -> [原因码]`：纯计算，全平台一份，复用 `tax_documents/amounts.py` 现有的 `printed_issues` 判据与容差，不另写一套。只管三类底线：

1. **金额不闭合**：
   - 印了小计和税额时，小计（减折扣）+ 税额 ≠ 总额；
   - 明细有金额时，明细合计对不上税前额或含税总额（同 `matched_target` 口径，含整单折扣、含税单价两种合法对法）。
2. **必填缺失**：单号、日期、总额。单号的口径按单据类型：收据 / 简式税票没印单号时不算缺，沿用现有 `confidence.py` 的类型区分。
3. **单据身份**：已有闸照旧（`account_match`、`doc_sanity`、MR.ERP 匹配闸），只把结果并进同一份原因码。

接入点（不改各自已有的拦截，只在前面加这一道；任何一条不过 → 不写账、进待处理、原因码进单据）：
- Cowork → Express 直写：`push/application/service.py` 预检处；
- Cowork → MR.ERP 直写：`mrerp/push` 预检处。去掉采购「缺总额用小计 + 税额顶上」这条——这正是会把缺页单照写进账的那条；
- ERP → Express、ERP → MR.ERP：`erp_portal/express/prepare.py`、`erp_portal/mrerp/push.py` 进写账前；
- 代录：Express 代录建任务前（`express_operator` 收单处），同一判定不过的单不交给电脑。

待处理里显示：原因码进现有码表 `companies/exceptions.py` 与 ERP `express/contracts.py`，复用现有「失败 / 停下」两类显示，不新增状态。新增的原因文案只有三条（金额不闭合、缺总额、缺单号 / 日期），按文案规矩走 `check-copy`。

### 1.3 识别要不要加「认页码」：不加

- BJC 两张缺页单在通用护栏下都会被拦（缺总额），已离线实测（第 1.1 节）。
- 识别规格（`spec.py`）冻结，改它要过金标全量；而缺页的根因由 CC 看原件那一页就能说清（印着「หน้า 1/2」），不需要读法自己判。
- 施工时用 549 份回放和 10 份长样本统计：护栏会新拦下哪些单、其中有没有印着「1/2」却没被拦的。有漏网的再提「认页码」，单独过闸。

### 1.4 ERP 的一个待核实点

ERP 单据的合计由明细算出，识别时的复核码（`review`，含 `total_missing`）只在识别结果里。网页 / LINE 卡片怎么呈现、拦不拦提交，本次没有核实清楚（采购页脚本里没找到消费点）。施工第一步先核实：
- 如果复核码不拦提交，ERP 侧护栏放在写账前，判据用「印的合计 vs 单据合计」：识别时把印的数存在单据上，`printed` 已有；
- 两者不一致、或印的合计缺失 → 进 ERP 待处理。

## 2. 第二层：CC 的通用工具（能查、能问答、能做）

原则：现有工具够用就不新增，不做「某种情况专用」的工具。

| 能力 | 现有（复用） | 本次补（通用） |
| --- | --- | --- |
| 查原件某页 | `cowork_document_original`（可指定 `page`） | — |
| 查识别结果与自检点名的问题 | `cowork_document_detail` 只给 `needs_review` 布尔值 | `detail` 加 `checks`：识别复核原因（`review_reasons` / 校验提示）、护栏原因码、推送失败原因。不加新工具 |
| 查推送 / 代录失败原因 | `cowork_push_results`、`express_operator_status` | 代录失败单回到待处理时带原因码与说明（核实现状后补） |
| 读回 Express | `cowork_express_readback`（按月） | — |
| 读回 MR.ERP | 无 | 不在本次（MR.ERP 没有读回接口） |
| 列待处理、主动汇报 | 只有 `cowork_list_documents` 的 waiting 近似 | 新增 `cowork_pending`：直接调 `companies/pending.py` 同一个后端，回待处理项与原因；ERP 侧同一工具名，调 `erp_portal/express/pending.py` |
| 改字段 | `cowork_edit_document`（带版本号） | 加必填参数 `basis`（依据）。动金额 / 税号 / 单号 / 日期时必须给：用户口述、看原件第 n 页、按 7% 补算……存一条改动记录（谁、何时、前后值、依据），单据详情里可见 |
| 补传文件并进原单重读 | `cowork_recognize_attachments` 只能建新单 | 加参数 `into_document`：把附件接在原单原件后面，整份重读（用长文件分段，多份原件同 ERP `read_originals`），结果覆盖原单字段（旧值进改动记录），不新建单 |
| 重推 | `cowork_push_documents` | — |
| 结果不明判定 | `cowork_resolve_push_result` | — |
| 先不录 | 待处理里保持原状 | 不新增：CC 用 `basis` 记一条「用户决定先不录」，单子留在待处理 |
| 作废 / 删除 | `cowork_delete_documents`（真删，已推的跳过） | — |
| 改走代录 | 只能从实操入口进 | 第二期再定（见第 6 节问题 3） |

ERP 同样能力：
- ERP 的 CC 工具现在有推送 6 个（`erp_portal/express/cc_tools.py`）、公司 4 个；
- 本次补 `pending` 和 `detail` 的 `checks`；
- 改字段与补传：只对草稿单开放（已提交的 ERP 单按 ERP 原生走作废重开，CC 说明并给链接），同样带 `basis`、同一份改动记录。

代录的「不认识的弹框」：
- `ComputerUseHelper` 已写好，`host.py:419` 没接线，现在一律转人工；
- 本次把它接上，按设计（Haiku 8 轮 → Opus 14 轮 → 转人工）；
- 转人工时这张单带原因与当时截图进待处理（截图存本机证据目录，待处理里给本机路径）。

## 3. GUIDANCE（只写原则）

在 Cowork / ERP 的 GUIDANCE 各加一段，不罗列具体情况：
- 单据卡住或用户问「有什么要处理的」时，先看待处理和这张单的原件、自检问题、失败原因，找出根因，用一两句人话说清；
- 能根据原件或用户的话补齐的，说明依据再补；补不了就问用户要资料或决定；
- 不猜着合并：补传的文件对不上是哪张单，先问；不编数字：算出来的数要说明怎么算的；
- 动金额、税号、单号、日期的修改，都带依据；
- 用户拍板后接着做完（补齐 → 重推 → 回报结果）。打开一家公司的对话、有待处理项时，主动报一次。

## 4. 上下游、入口、隔离

- 入口：
  - 待处理（Cowork 公司侧栏「待处理 N」与 ERP 待处理）、CC 对话、审核页，三个入口用同一套后端（`pending.py` 判定、同一个护栏、同一份改动记录）；
  - 待处理行上现有「交给 CC」（预填单号、对方、金额、日期）就是续接入口，补传文件在对话里附上即可，第一期不改待处理界面；
  - 第二期如要在待处理行上直接「补传」，只改 `components/pending.tsx`，与侧栏统一窗口协调，不碰侧栏导航文件。
- Express / MR.ERP：
  - 护栏只会让少写，不改任何写入格式；
  - CC 桌面版载荷不变；
  - 代录建任务前多一道判定，CC 桌面版不用发新版。AI 接手那项要发新版。
- 隔离：
  - 所有工具沿用现有按工作空间、按公司的权限；
  - `into_document` 只能补到本工作空间、这家公司的单上，服务端校验。

## 5. 共用件、成本、删除

- 共用件：
  - `tax_documents`（新增 `ledger_guard`）；
  - `express_rules/push`、`mrerp/push`（接入护栏）；
  - `claude_ocr`（补传重读复用，不改）；
  - `assistant` 工具与 GUIDANCE；
  - `express_operator` 与 `companion`（AI 接手）。
  - 施工时跑 `check-impact` 逐入口回归。
- 成本：
  - 护栏零成本；
  - 补传重读：按现有规矩，重读的页照常扣费；
  - AI 接手：每次 Haiku 最多 8 轮、Opus 最多 14 轮，按截图估约 0.05–0.5 美元一次，只在认不出的弹框时发生；
  - CC 对话本身照现有计费。
- 删除 / 作废：
  - 改动记录随单据删除一起删（轻便工具口径）；
  - 已写进 Express 的单，作废照现有口径（CC 说明改不了，不指引去 Express 改）。

## 6. 要问主会话的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 只印总额的单（简式税票、含税收据很常见）现在按 7% 反推照写，算不算「不闭合」 | 不算：含税价印的总额本身闭合。只有完整税票（`tax_invoice`）只读到总额、没有小计和税额时进待处理 | 一律拦会让大量小票进待处理 |
| 2 | 明细合计对不上时 Express 直写现在退回只写表头、照写 | 先用测试租户历史统计这类单的占比，再定：改成进待处理，或照写表头但在待处理里提示「明细没写」 | 直接改拦，待处理量可能明显上升 |
| 3 | 「改走代录」做不做 | 第二期：先把护栏、工具、AI 接手做完再看 | 不做时，被直写拒收的单只能人工从实操入口改走代录 |
| 4 | 补传重读的扣费口径 | 重读的整份页数照常扣（同「上传识别都扣费」） | 只扣新补的页要改计费逻辑 |
| 5 | 验收要用办公室测试机（Express 期间关账拒收、代录陌生弹框），要动测试机 | 由你协调测试机时段与占用登记 | 没有测试机，这两类只能单元级验证 |

## 7. 验收（真模型实测，记下每个样本的对话过程）

样本（都没有写专门规则），每个记录：上传 / 推送结果、待处理显示、CC 对话全过程（原话）、CC 做的工具调用、最后入账结果。

| # | 样本 | Cowork 直写 | Cowork 代录（Express） | ERP 直写 | 期望 |
| --- | --- | --- | --- | --- | --- |
| 1 | 缺页（BJC 第 1、7 页） | ✓ | ✓ | ✓ | 不写账，进待处理；CC 看原件说出「印着 1/2、缺第 2 页」；补传第 2 页后并进原单重读、写账；或按用户口述 / 按 7% 补算并带依据写账；或先不录 |
| 2 | 模糊或读错一位数（单号 / 税号校验位） | ✓ | | ✓ | CC 看原件指出疑点，问用户或按原件改，带依据 |
| 3 | 传错文件（不是本公司的单） | ✓ | | ✓ | 身份闸拦下；CC 说明买卖方都不是本公司，建议删除或移到对的公司 |
| 4 | 同一张单重复 | ✓ | | ✓ | CC 指出重复，按用户决定删一张 |
| 5 | 买方税号不符 | ✓ | ✓ | ✓ | 拦下；CC 对照原件与公司资料说清，按用户决定改或不录 |
| 6 | Express 拒收（期间已关账） | ✓（测试机） | ✓（测试机） | ✓（测试机） | 进待处理带原因；CC 说明、给方案（改期间 / 等开账再推） |
| 7 | 代录遇到不认识的弹框 | | ✓（测试机） | | AI 接手处理或转人工，带原因与截图进待处理；CC 能说清 |

- MR.ERP 直写只做单元 / 回放级（缺总额不再照写），MR.ERP 代录不在本次。
- 费用：识别约 20–40 页、CC 对话约 30 轮，加 AI 接手几次，预计 10–25 美元，在 100 美元总额度内（到 80 停）。脚本里设硬上限 30 美元，逐次记台账。

## 8. 排期（机器耗时，按顺序）

1. 护栏：`ledger_guard` + 四条直写路 + 代录建任务前接入 + 原因码与文案 + 单元测试；再用回放和测试租户历史统计护栏新拦的单。约 2–3 小时。
2. CC 工具：`pending`、`detail.checks`、`edit_document.basis` 加改动记录、`recognize_attachments.into_document`；ERP 对应；GUIDANCE 原则段。约 3–4 小时。
3. 代录：接上 AI 接手，转人工带原因与截图进待处理。CC 桌面版要发新版（合入 main 后直接发正式版）。约 2 小时，另加测试机实测。
4. 验收：第 7 节样本，真模型，记录对话。约 2–3 小时，加上等测试机的时间。
5. 每步过本地闸门、候选离线检查，合入 main；部署由主会话安排。

## 9. 审定（主会话 11-03）与施工进度

审定：
- 5 点按第 6 节建议定：只印总额的简式税票 / 含税收据不算不闭合，照账套规则反推；明细对不上的单，表头闭合时照写表头（CC 在 checks 里能报出）；改走代录放第二期；补传重读照常扣费；测试机由主会话排时段。
- 重复单按「推送不查重不拦」：护栏不拦，CC 只在被问到时指出。

### 9.1 ERP 复核码核实（施工第一步）

- 识别时算出的 review 码（`total_missing`、`printed_math`、`computed_total` 等）只写进复核提示词，不给用户看（`recognition/checks.py:30`，用户 10-02「录入侧不出识别后的错误提示」）。
- 网页 `purchase-form.js:217-229` 只填字段和明细；LINE 卡片显示的是按明细算出的合计。
- 提交和 ERP→Express 的 `checked()` 只核对单据自己算出的数；ERP 单据上没存票面印的金额。
- 结论：缺页单在 ERP 会按已有明细算出自洽但不完整的合计，照写进账。ERP 侧判据（存票面合计 → 写账前比对）已报主会话，**等回复再施工**。

### 9.2 已完成：共用护栏 + Cowork 两条直写路（提交 `960ed8055`）

- `tax_documents/ledger_guard.py`：五个底线码，判据复用 `amounts.printed_issues`。
- `express_rules/push/ledger.py`：从展平 history 取值，换成两边已有的拦截原因。Express 用 `amounts_not_consistent` / `low_confidence:invoice_no_missing` / `bad_or_missing_date`，沿用已有文案；MR.ERP 用 `ERR_NO_TOTAL_AMOUNT` / `ERR_NO_INVOICE_NO` / `ERR_NO_INVOICE_DATE`，加一个新码 `ERR_AMOUNTS_NOT_CLOSED`，列入不自动重试的用户数据错。
- 接入点：
  - Express 直写：`preflight.py` 单据一步；
  - Cowork 的 Express 代录：交单逐张走同一个推送预检（`operator_source.submit` → `tools._push_one`），自动覆盖；
  - MR.ERP 直写：`transport._preflight`，赊销、采购、费用三种模块都先过护栏。Cowork 和 ERP 推 MR.ERP 共用这一处。
- 反例（`tests/test_ledger_guard.py` 13 条）：把护栏换回旧写法，4 条拦截测试失败：
  - Express「完整税票只读到总额按 7% 反推照写」；
  - MR.ERP 采购、费用「缺总额用小计 + 税额顶上照写」（BJC 第 7 页按 41589.83 写进账）；
  - MR.ERP「金额不闭合照写」。
- 本地闸门：架构 0 违规、文案无新增、现有 Express 预检 24 条通过；`check-impact` 命中 express-rules（12 入口）、mrerp-rules（4）、tax-documents（6），逐入口回归放在候选检查与验收。

### 9.3 统计（549 份回放的新代码读法，514 张录入类单据）

| 原因 | 张数 | 现在 Express 直写 | 现在 MR.ERP 直写 |
| --- | --- | --- | --- |
| 缺总额 | 19 | 已拦 | 有小计 + 税额的照写（护栏新拦） |
| 金额不闭合 | 9 | 已拦 | 照写（护栏新拦） |
| 完整税票只读到总额 | 11 | 照写（护栏新拦） | 照写（护栏新拦） |
| 缺日期 | 6 | 已拦 | 已拦 |
| 缺单号（完整税票） | 0 | — | — |

- 「完整税票只读到总额」的 11 张里：5 张模型判为 `bill_invoice`（没有税票抬头的发票，8000 / 54000 / 15000 这类整数，多半是未登记增值税的卖方，本来就只印总额），平台码把它们也映射成 `tax_invoice`。另有 1 张 debit_note，2 张 tax_invoice，3 张 receipt_tax_invoice。**已报主会话定口径**（见 9.4）。
- 明细合计对不上 10 张：其中 6 张表头闭合（照写表头，钱没错），4 张表头也不闭合（护栏已拦）。

### 9.4 待主会话定

- 「完整税票只读到总额」是否排除模型判为 `bill_invoice` 的单（只对 `tax_invoice` / `receipt_tax_invoice` 生效，回放上会拦 5 张，约 1%）。
- ERP 侧判据（9.1）。

### 9.5 接下来（按顺序）

1. ERP 侧护栏（等 9.1 回复）：存票面合计 → ERP→Express / ERP→MR.ERP 写账前比对；ERP 代录同一处。
2. CC 工具：`pending`、`detail.checks`、`edit_document.basis` 加改动记录、`recognize_attachments.into_document`；ERP 对应；GUIDANCE 原则段。
3. 代录 AI 接手接线（`host.py:419`）。
4. 真模型验收（第 7 节）。

### 9.6 ERP 判据（主会话 11-03 定）

- 采购、销售单据各加一个「票面合计」字段：
  - 原生字段，用户可改，不加说明文案（10-02 口径）；
  - 开 `track_changes`，版本记录里查得到谁、何时改的；
  - 识别写入的值与用户改的值要分得清来源；
  - 字段名、标签用简短官方用词，新词条走文案流程。
- 写账前（ERP→Express、ERP→MR.ERP、ERP 代录）比对「票面合计」与单据合计：
  - 对不上或票面合计为空 → 进 ERP 待处理；
  - 用户照纸上总额填，或补齐明细让两边对上，就自动放行；
  - CC 通过 edit_document 改时必须带 basis。
- 只拦**新识别的单**（有识别标记的）：手工录入的单、上线前的老单（字段为空）照旧放行。迁移用原生 patch，老单留空。

### 9.7 「完整税票只读到总额」口径（主会话 11-03 定）

- 只对模型判为 `tax_invoice` / `receipt_tax_invoice` 的单生效；`bill_invoice` 等照账套规则走。护栏 `check` 加了 `title`（模型的 `doc_kind`）参数。
- 回放统计上会拦 5 张（约 1%），名单见 9.3。
- **现在还不生效**：Cowork 单据字段里没有带 `doc_kind`（`ThaiInvoice` 没有这一栏），推送时取不到。下一窗口在 Cowork 的 Claude 转换（`ocr/execution/claude.invoice`）带上它，ERP 同样，带上之后这一条自动生效。没带上之前不会误拦。

## 10. 剩余四块施工进度（11-03 晚，接手窗口；分支 `pearnly/cc-troubleshooting-rest`，基线 `25001bbed`）

### 10.1 (a) ERP 侧护栏（提交 `3cbd75c7f`）

- 采购单、销售单各加两栏（原生字段，表结构随 migrate 同步，老单留空，不回填）：
  - `printed_total`「票面合计」：Currency，人可以改；识别来的单才在页面上显示（`purchase-fields.js` / `sales-fields.js`，识别后显示）；两张表原来就开着 `track_changes`，谁何时改的查版本记录；
  - `ocr_review`：隐藏只读，识别标记 = 这次识别的复核码 + 识别读到的合计（JSON，`recognition.checks.marks`），与人改过的票面合计分得清来源。
- 写入：网页识别（`purchasing/recognition.py`、`sales/recognition.py` 结果带上两栏，表单原样提交）；LINE 卡片建单（`integrations/intake.build_payload`）；LINE 编辑页提交时服务端按卡片补识别标记（`intake.submit`，页面不带复核码，维持 10-02 口径）。
- 判据 `express/contracts.printed_hold`（纯函数，容差与预扣税实付额对法同识别核对 `amounts.computed_issues`）；读单据 `express/printed.hold`。接在：Express 直写与代录交单 `push.send`、代录预检 `push.prepare_only`、MR.ERP 导入 `mrerp/push.prepare`。原因码 `express.printed_total_missing` / `express.printed_total_mismatch`，不在自动重试表里 → 进 ERP 待处理；文案「没有票面合计」「合计与票面合计不一致」。
- 解除：已提交的单在单据页「修改」（原生作废重开），填对票面合计或补齐明细，新单照常写。没有识别标记的单（手工录、上线前老单）不核对。
- 反例：把 `printed.hold` 换回「一律放行」，Express 直写、代录交单、代录预检、MR.ERP 四条测试失败（`tests/test_erp_express_direct.py::PrintedTotal`、`tests/test_erp_mrerp_direct.py`）。
- 候选离线检查（生产镜像 `ab3adbb1d56f`，`~/pearnly-devtools/cand-ccrest-a.log`）：`LANE EXIT 0`。

### 10.2 (b) CC 工具（提交 `020cea68a`，部分）

已做：
- `cowork_pending`（`assistant/company_tools.py`）、`erp_pending`（`express/cc_tools.py`）：直接调两边侧栏「待处理」的后端；ERP 的识别单另带 checks（复核码、票面合计、识别读到的合计、单据合计）。
- `cowork_document_detail` 加 `checks`（识别复核原因、护栏原因码、明细对账、推送原因）和 `edits`（改动记录）：`assistant/document_checks.py`。
- `cowork_edit_document` 加 `basis`：动金额 / 税号 / 单号 / 日期 / 票种 / 币种 / 明细时必填；改动记录存为原生 Comment（subject `pearnly.document_edit`，谁、何时、改前改后、依据），删单据时一起删（`history/removal.py`）。
- Cowork 识别带上 Claude 的 `doc_kind`（`ThaiInvoice.doc_kind`，`ocr/execution/claude.invoice`）：护栏「模型判为税票却只读到总额」从此生效；读法、提示词不动；转换在本机代码，不用重部署甲骨文。
- GUIDANCE：共用层 `direct_push.guidance_troubleshooting(<待处理工具名>)` 一段原则，Cowork、ERP 都挂上（含「推送不查重不拦，可提醒不可拦删」「公司对话第一句时查一次待处理」）。
- 反例：去掉 basis 检查 → `EditDocumentTests` 失败；去掉 doc_kind → `test_claude_ocr_products::Cowork.test_the_model_kind_reaches_the_ledger_guard` 失败。

没做（接手继续）：
- `cowork_recognize_attachments` 加 `into_document`（补传文件接在原单原件后面整份重读、覆盖字段、旧值进改动记录、照常扣费）。
- ERP 没有单据详情 / 改字段工具：已提交的单只能「修改」重开，`erp_pending` 的说明里写了让 CC 给链接；要不要给 ERP 加查原件 / 改草稿的工具，待主会话定。

### 10.3 (c)(d) 未开始

- (c) 代录 AI 接手：CC 桌面版的 `ComputerUseHelper` 需要一个经服务端转发模型请求的接口（密钥不在电脑上、按任务费用上限），服务端目前没有，要新建；失败单也要截图并随原因进待处理。
- (d) 真模型验收：见第 7 节矩阵。
