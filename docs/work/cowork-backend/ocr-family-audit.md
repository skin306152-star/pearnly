# Cowork 后端迁移：OCR 族缺口清点（2026-09-21）

只读清点 `services/ocr/**`（**98 个文件 / 16,929 行**，含 `feedback/`、`handlers/`、`jobs/`、`recognize/`、`enterprise_local/` 五个子包），回答一个问题：**这一族还剩什么真缺口。**（第三十六批更新：用户已决定原生承接银行单/GL 文档级 OCR，第三节因此从"候选缺口"转为"已开工"，逐条对照与分层见第四节与[批次记录](ocr-document-chains.md)。）结论先写：

- 真缺口两处已在[本批](ocr-validation-and-error-text.md)补上——供应商历史量级基线（`magnitude_baseline.py`）与单行错误文案（`error_format.py`）。
- 第三处（`bank_chain_gate.py`、`gl_balance_chain.py`）当初按"跑在旧侧银行单 / GL 文档模型上、而本切片只承接 `invoice`/`auto`"登记为待决策；用户判断 OCR 是产品核心产物 → **要**，现按四层推进，第一层已交付。逐条对应见第四节。
- 其余 45 个文件不是"丢了"，是**有意不迁**（表三）：文件/PDF/图像读取、模型与成本策略、路由与队列编排、企业版抽表、动态运行策略读取——各由新平台别的承担者接手。

原生 `apps/pearnly_cowork/pearnly_cowork/ocr/` 现在 **53 个文件 / 5,214 行**，按 12 个包组织。本文不改代码、不写库；固定来源为只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`，线上来源在本批交付时重新采集（见[证据](evidence/thirty-fifth-local-validation.json)）。

## 一、98 → 53：少的是哪一层

旧包把四件事混在一起：**抽取规则**（提示词、schema、校验、表引擎）、**器件**（打开 PDF/Excel/Word、调 Gemini/Qwen、成本与配额）、**编排**（route、队列、job、状态机）、**运行策略**（读环境与数据库决定档位）。新平台的边界是"调用方交出解码后的内容"，所以只有第一层按职责搬过来，且拆成更小的包；第二、三、四层由新平台已有的承担者接手（表三逐项写明）。

行数比（16,929 → 5,214）不能当完成度：旧包最大的一块是器件与编排，而新平台这些位置的实现不在 `ocr/` 里（例如队列与计费在 `ocr/execution`，模型调用在 `ocr/providers`，路由在 `ocr/api.py`）。

## 二、已迁（有落点与批次）

| 旧来源（组） | 新落点 | 批次 / 记录 |
| --- | --- | --- |
| `schemas_invoice.py`、`schemas_layer1.py`、`schemas_results.py`、`schemas.py` 的发票与证据部分 | `ocr/schema/{invoice,evidence,results}.py` | 第三批 · [invoice-contracts](invoice-contracts.md) |
| `validators.py`（**发票**那一半）、`sanity.py`、`sanity_multi.py`、`triggers.py`、`money.py`、`date_sanity.py`、`confidence.py` | `ocr/validation/{sanity,math,sources,dates,gates,confidence}.py`、`ocr/money.py` | 第三批 · [invoice-contracts](invoice-contracts.md) |
| `validators.py`（**总账 + 银行对账单**那一半：列名表、`validate_gl_document`、`validate_bank_document`、拒绝即清值） | `ocr/validation/sources.py` | **第三十七批** · [列来源校验](ocr-document-validation.md) |
| `layer2_prompts.py`、`qwen_prompts.py`、`legacy_adapter.py`、`layer1_base.py`、`layer2_structure.py`、`layer3_fallback.py` | `ocr/engine/{prompts,parsing,patterns,runner}.py` | 第三/四批 · [invoice-engine](invoice-engine.md) |
| `policy.py`、`engine_policy.py` 的**静态**档位判据 | `ocr/policy.py`、`ocr/engine/config.py` | 第三批 · [ocr-runtime-policy](ocr-runtime-policy.md) |
| `invoice_grouper.py`、`invoice_no.py` | `ocr/grouping.py`、`ocr/engine/patterns.py` | 第三批 · [invoice-grouping](invoice-grouping.md) |
| `table_path.py`、`text_path.py`（读取与结构化解码） | `ocr/table_inputs/{reader,archive,model}.py` | 第五批 · [table-inputs](table-inputs.md) |
| `pipeline.py` 的表格与文本 L2 编排、`handlers/invoice.py` 的表格分流 | `ocr/table_engine/{runner,roundtrip}.py` | 第五批 · [table-engine](table-engine.md) |
| `page_runner.py` 的校验编排与触发 | `ocr/engine/runner.py` + `ocr/validation/**` | 第五批 · [table-engine](table-engine.md) |
| `feedback/**`（`context/diff/fewshot/schema/store`） | `ocr/feedback/**` | 第三批 · [invoice-feedback](invoice-feedback.md) |
| `recognize/core.py` 的输入元数据、页判定、置信与分组 | `ocr/inputs.py`、`ocr/pages.py`、`ocr/results.py` | 第二/三批 · [ocr-pipeline](ocr-pipeline.md) |
| `pipeline.py` 的帧与供应商 IO 边界、`model_client.py` 的调用合同 | `ocr/providers/{client,core}.py`、`ocr/inputs_runtime/source.py` | 第四批 · [invoice-runtime-io](invoice-runtime-io.md) |
| `jobs/**`、`handlers/**` 的队列与状态 | `ocr/execution/**`（批次、队列、重试、计费、工作单守卫） | 第四批 · [invoice-execution](invoice-execution.md) |
| `roundtrip_intake.py` | `ocr/table_engine/roundtrip.py` + `roundtrip/` 域 | [roundtrip-parser](roundtrip-parser.md)、[roundtrip-parity-audit](roundtrip-parity-audit.md) |
| `recognize/{persist,history_postprocess}.py` 的历史投影 | `history/`（原生 DocType + `contracts/history.py`） | 更早批次（`ocr_history` 功能码） |
| `magnitude_baseline.py` | `ocr/validation/magnitude.py` | **第三十五批（本文）** |
| `error_format.py` | `contracts/errors.py`（+ 解析落点 `error` 字段） | **第三十五批（本文）** |
| `bank_stmt_xlsx.py` 的**确定性那一段**（表头词典、找表头、账户号、期初、逐行转 `StatementRow`、逐账户余额链、期末交叉校验） | `reconciliation/bank_sheets.py` | **第三十八批** · [读取器](ocr-reader-bank-sheets.md) |
| `bank_table_io.py`（`_hit` 短词整词匹配、openpyxl→xlrd 读表、CSV 编码与分隔符嗅探） | `reconciliation/table_io.py`（同文件的 PDF 文字层随 PDF 路径下一批走） | **第三十八批** · [读取器](ocr-reader-bank-sheets.md) |
| `bank_recon_utils._bank_from_filename` | `reconciliation/bank_parse.py` 的 `bank_code_from_filename` | **第三十八批** · [读取器](ocr-reader-bank-sheets.md) |
| `bank_gl_excel.py` 的**确定性那一段** + `bank_gl_common.py`（列名词典、科目号正则、表头映射、运行余额） | `reconciliation/gl_sheets.py` | **第三十九批** · [读取器](ocr-reader-gl-and-text.md) |
| `bank_stmt_text.py`（一行一笔的文本行、KBank 列压行） | `reconciliation/bank_text.py` | **第三十九批** · [读取器](ocr-reader-gl-and-text.md) |
| `bank_table_io.py` 的 `_pdf_extract_text_safe`（PDF 文字层） | `reconciliation/table_io.py` 的 `extract_pdf_text_pages` | **第三十九批** · [读取器](ocr-reader-gl-and-text.md) |
| `bank_recon_utils._GL_SKIP_KW` / `_is_gl_skip_row` | `reconciliation/bank_parse.py` | **第三十九批** · [读取器](ocr-reader-gl-and-text.md) |

## 三、有意不迁（有理由，非遗漏）

| 旧来源（组） | 理由 |
| --- | --- |
| `pdf_backfill.py`、`pdf_searchable.py`、`pdf_storage.py`、`pdf_text_extractor.py`、`pdf_utils.py`、`direct_read.py`、`image_first.py` | 打开文件、渲染帧、读 PDF 层、图像首读：新平台的边界是"调用方交出已解码内容"（`ocr/inputs_runtime/source.py` 只定义调用方交付的形状） |
| `model_client.py` 的重试/熔断实现、`gemini_models.py`、`qwen_direct.py`、`layer2_gemini.py`、`layer3_gemini.py` | 供应商 SDK 与模型目录：原生由 `ocr/providers` + `pearnly_core` 的调用出口承担，档位名由调用方在 `EngineConfig` 显式钉住 |
| `cost.py`、`shadow_money.py`、`shadow_money_store.py`、`quota_pacing.py`、`escalation_budget.py`、`totals_rescue.py`、`pattern_memory.py` | 成本估算、影子账、配额节流、升级预算、L3 视觉复读、模式记忆：属旧侧的成本与自适应策略，新产品按 `pearnly_core.billing_client` 冻结口径与 `ocr/execution` 的重试入口承接（见[对账族清点](reconciliation-family-audit.md)第四节） |
| `pipeline.py`、`controller.py`、`legacy_adapter.py` 的编排、`entrypoints.py` 的路由、`handlers/**`（除发票表格分流）、`jobs/**` | route/队列/状态机：原生由 `ocr/api.py` + `ocr/execution/{service,workflow,store}.py` 承担 |
| `admin_runtime.py`、`connectivity_check.py`、`observability.py`、`engine_policy.py` 的动态读库部分 | 运行期配置读取与可观测性：属控制面与运维面，模型与档位在原生侧是显式入参 |
| `enterprise_adapter.py`、`enterprise_contract.py`、`enterprise_pipeline.py`、`enterprise_quota.py`、`enterprise_reader.py`、`enterprise_request.py`、`enterprise_local/**` | 企业版（本地抽表/配额/请求编排）不属新产品范围；`pearnly_dms.ocr`、`pearnly_core.ocr` 是 DMS 侧的既有实现，与本族无关 |
| `schemas_documents.py` 的银行单/GL 文档模型、`id_card_extract.py` | 银行单/GL 文档模型不在本切片（只承接 `invoice`/`auto`）；身份证归一化属 DMS 的 `ocr_id_card` |
| `recognize/{autopush,cache,category_tag,errors,sanitize,workspace_assignment}.py` | 推送触发、缓存、分类标签、工作区分配：由 `push/`、`files/`、`history/` 域按各自职责承担 |

## 四、真缺口：两处已补，一处明确不属本切片

**（1）`magnitude_baseline.py`（`magnitude_anomaly_reason`，47 行）——已补。** 供应商历史量级基线：加油票真值 ~1,780 被读成 44.67（选错列）时，内部自洽、其它闸门抓不住，唯一确定性信号是"这家供应商历史总额一直在几千"。落点 `ocr/validation/magnitude.py`，保留 `RATIO=20.0`、`MIN_HISTORY=5`、中位数而非均值、中文原因串，以及 `True` 被当正数的既有怪癖。

> 部署版自己注明：喂 history 的"按 seller_tax 取近 N 张正 total_amount"查询**没有提交**，所以旧侧从未接线。原生侧同样只保留决策核心，历史来源留给调用方——不凭空造一条历史查询，也不假装已接线。

**（2）`error_format.py`（`short_error` / `_fmt_validation`，78 行）——已补。** 把 pydantic `ValidationError` 压成一行：去文档 URL、只留首行、>160 字符截断加省略号、空文案回落类名、字段去重后列前三个加溢出标记。部署版被银行/GL/VAT 的**文件级**解析入口用来填结果的 `error` 字段（`bank_recon_pipeline.py:133/232`、`gl_vat_parse_excel.py:254`、`vat_report_parser.py:86`、`enterprise_adapter.py:42`）。落点 `contracts/errors.py`：抽取侧持有 pydantic schema，对账侧持有 `error` 字段，放共享纯契约域让两侧都不依赖对方；原生 `income_parse` 的两个 `error` 落点已改回这个文案（`diag.exception` 保留原文便于诊断）。

**（3）`bank_chain_gate.py`（276 行）+ `gl_balance_chain.py`（135 行）——用户已决定原生承接，第一层已在第三十六批交付。** 它们是**文档级**余额链修复器，读写的是旧侧 pydantic 文档模型（`StatementRow.chain_repaired`/`chain_amount_imputed`/`review_required`、`GeneralLedgerDocument.opening_balance`/`total_debit`/`total_credit`、`raw_row_data`），并且它们存在的理由是给旧引擎**省升级成本**（翻正一行胜过整页升 max 重读）。用户判断"OCR 是产品的核心产物" → 要，整条链按依赖分四层：**①文档模型 + 余额链修复（第三十六批已交付，见[批次记录](ocr-document-chains.md)）** → **②列来源校验（第三十七批已交付）** → **③确定性读取器（第三十八批：低级表格 IO + 银行流水 xlsx/csv 直读，见[记录](ocr-reader-bank-sheets.md)；第三十九批：总账 xlsx/csv、银行单文本行、KBank 列压行、PDF 文字层，见[记录](ocr-reader-gl-and-text.md)，本层已闭合）** → ④模型路径（含整页升档重读 `reread_bank_page`）。下表是这层规则的逐条对应，随后两列说明现在归到哪一层：

| 旧规则 | 原生现状 | 归层 |
| --- | --- | --- |
`repair_bank_chain` a：空/承转行放行、余额跳变即拒 | ①`validation/chains.py`（文档层）+ 对账层 `bank_statement.verify_row_balances` | 文档层按旧口径完整迁入；对账层那套是另一层的既有规则 |
| b：正常向核验 | ①同上（容差 `0.01`） | 已在文档层 |
| c：翻转向 | ①`chains.repair_bank_chain` + 对账层 `correct_direction_from_balance` | 两层各有自己的实现，互不替代 |
| d：链推定覆写（锚=模型读数，带 20%） | ①`chains._impute_chain_amount`；对账层另有带宽 30% 的一代 | 文档层按旧口径迁入；两层不混用 |
| e：断一处即停，交调用方升级重读 | ①返回首处断点；**整页升档读**归第 ④ 层（`reread_bank_page` 未迁） | 断点判定已迁；升级动作待第 ④ 层 |
| `repair_gl_document` 1：方向纠正并打 `direction_autocorrected` | ①`chains.repair_gl_document` | 已迁 |
| 2：期初反推（期初未印时 = 首行余额 − 首行净变动） | ①`chains._derive_opening` | 已迁 |
| 3：印刷合计对首行消歧（翻首行就两项全平则翻） | ①`chains._reconcile_printed_totals` | 已迁 |
| 4：断链标记顶人工复核 | ①`chains.repair_gl_document` 返回的警告 + `bank_chain_warnings` | 已迁 |

这些规则都必须读写 GL/银行**文档**模型，所以它们随文档层整体迁：塞进对账行层只会得到一份既不是旧行为、也没有真实数据来源的实现。第三十六批已把这层的地基（文档模型 + 两个修复器）按旧口径迁入，逐条对照见[批次记录](ocr-document-chains.md)。

## 五、架构归位：本批做了什么、还剩什么债

旧侧 `services/ocr/**` 是一个平铺的大包，新侧已经按职责分好 12 个包。本批在架构上做了三件事，并把债务写下来：

1. **新增共享纯契约域条目**：`contracts/errors.py`。新增依赖方向 `reconciliation → contracts` 已登记在 `architecture-policy.json`，理由是"对账侧要产出与部署版同形的单行 `error` 文案，而该文案不能来自 `ocr`（否则对账会依赖抽取域）"。`contracts` 本身无框架、IO、模型依赖，不产生环。
2. **给 Cowork 登记域依赖并纳入自动检查**（`cowork_domain_dependencies` + `scripts/check-architecture.py` 的 Cowork 分支）。以后新增未登记方向会在 `scripts/check-architecture.py`（含共享 pre-commit 钩子）直接失败，新增方向必须同时说明契约与理由。表见 `docs/development/architecture.md` 的"Cowork 的实际职责"。
3. **把既有环如实登记为债务**：`ocr ↔ roundtrip`、`files ↔ ocr`、`history ↔ ocr`（后两组各有一条是函数内延迟导入）。它们来自旧项目同一批抽取/往返流程的相互调用，搬迁时按原职责落位；登记只表示"现在如此"，不表示方向正确。改到这些链时要按真实职责拆开并补回归，不能用新的延迟导入扩大环。

一并记录的既有整理项（不改行为、不属本批）：

- `pearnly_cowork` 根仍有基线内的根部模块 `intake.py`，与按域组织的新代码并存；
- App 的框架模块目录（`pearnly_cowork/pearnly_cowork/doctype|page`）是编排层，DocType 控制器在其中按需调用各域，因此不进域依赖表（DMS 的模块目录即域目录，两者结构不同）；
- `masters` 域同时承载"工作区/客户受理 RPC"（`api.py`）与"输入受理契约"（`push_inputs.py`），`reconciliation → masters` 这条边只用到后者，属于后续可拆项；
- 原生抽取切片只承接 `invoice`/`auto`（`ocr/engine/runner.py` 明确拒绝其它 `document_type`），银行单/GL 文档级识别与表四的修复器一起待决策。

## 六、建议的后续顺序

1. **银行单 / GL 文档级 OCR（用户已定：要）**，按依赖分四层推进：①文档模型 + 余额链修复（**第三十六批已交付**）→ ②列来源校验（金额只能来自金额列，**第三十七批已交付**）→ ③确定性读取器（**第三十八批交付低级表格 IO + 银行流水 xlsx/csv 直读，第三十九批交付总账 xlsx/csv、银行单文本行、KBank 列压行与 PDF 文字层，本层已闭合**；pdfplumber 表格/坐标解析、模型路径与整页升档仍归第 ④ 层或器件层）→ ④模型路径（银行/GL 提示词、引擎文档档位、执行接线、整页升档重读）。
2. 其余按族推进：M2 销项税 Excel 族、工单对账族各自清点。
3. 主数据（`master_data`）方向由用户定：Cowork 自有一份，还是读 ERP 成品投影。

## 来源核对

清点本身只读，但本批交付同时独立采集了线上来源：`evidence/baseline-thirty-fifth-delivery.json`（`40ae6777…`）与第三十四批快照 `compare` **PASS**，`source` 仍 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。

## 未做

- 未改 DMS、未接前端、未部署、未写旧库；未跑真实文件与真实模型识别。
- 未核 `services/vat/**`、`services/summary_import/**`、`services/workorder/**` 与 `services/erp/**` 的逐件对应——那几族各自清点。
