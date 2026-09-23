# 银行单 / GL 文档级 OCR 第四层后半：文件入口接进原生银行任务（第四十一批）

第四十一批从新平台 `41869ff` 开工，工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`。线上来源仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`（固定只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`）。不接前端、不部署、不改 DMS、不写旧库、**不调模型**。

这一批是用户要的那一句"把 ①②③ 层接进原生银行任务"：**上传的原件 → 行 → 存储的银行对账任务**。前面三批交的是零件（文档模型与余额链、列来源校验、确定性读取器、模型路径的判定），本批交把零件接成链的那一段，并让它在隔离 Site 上真的跑通一次。

## 一、本批交付

| 新落点 | 来源（sha256 固定） | 内容 |
| --- | --- | --- |
| `reconciliation/bank_intake.py` | `services/recon/bank_recon_v2.py` 的 `_parse_bank_statement_impl` / `_parse_gl_impl` 分发 + `services/recon/bank_recon_pipeline.py` 里"选哪条路"的那一段 | `read_statement_file`、`read_ledger_file`：扩展名分发 → 免费读取器 → 注入的模型路 → 定案序列 → 换眼重读 → 期初期末兜底 → 完整性交叉校验 |
| `reconciliation/bank_arbitration.py` | `services/recon/bank_arbitration_guard.py`（`5bb3eb43…`，62 行） | `diverges`、`divergence_reasons`（两法分歧守门） |
| `reconciliation/bank_recon_service.py` 追加 `create_from_documents` | `bank_recon_service.create_from_parsed` 的同形兄弟 | 字节进 → 行 → 与 `create` 同一条校验存储路径 |
| `doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py` 追加 `create_from_sources` | 新的编排接线 | 用 files 域读原件（查权限与校验和）→ 交给上面的服务入口 |

### 为什么编排那一段落在 DocType 控制器里

本仓的域依赖表里 `reconciliation` **不许依赖 `files`**（部署版也是路由层把字节递进来，对账包自己不开文件）。而"读上传的原件"必须经过 files 域（它负责权限与校验和）。两个域都不许依赖对方，能同时用两者的只有 **App 的模块目录（编排层）** —— 这正是架构文档写的"DocType 控制器在其中按需调用各域"。所以：纯逻辑在 `reconciliation/`，读文件的那一段在控制器里，`create_from_sources` 因此在控制器模块上（已在 lab 上用 HTTP 匿名 403 验证它确实是被登记的 RPC）。

### 保留的旧口径（都是钱路上的判定）

- **文件名里的银行码优先**（内容检测会被跨行转账的对手行名淹没）；
- **xlsx/xls/xlsm/csv/tsv 先直读**，认不出表头才轮到模型（部署版就是"直读优先、miss 才降级"，零成本先走）；
- **轮到模型的两个条件**：行数 < `MIN_TABLE_ROWS`(=3) 或免费解析的余额链坏行占比 > 0.30（部署版 `_need_gemini`）；采纳与否仍比余额链自洽度；
- **两法分歧必须留痕**：两法都出行、期初/期末显著分歧（>5% 或一正一负，绝对值地板 1.0 泰铢）→ 标 `method_divergence` 并把 completeness 置 not-ok，**不替用户静默选一个**（BBL2645 的教训）；
- **定案序列顺序不变**（跳汇总行 → 方向纠正 → 逐行核对 → 金额反推修复），**换眼重读只覆盖 PDF**、严格更少断链才采纳、一页失败整轮弃；
- **期初/期末兜底**：显式 B/F 行的余额，或首笔"余额 − 净额"反推；期末取最后一笔带余额的行；
- 直读路**自带它自己的完整性**（`closing_mismatch` / `balance_break`），文本与模型路才用打印合计/笔数与期末平衡的交叉校验 —— 与部署版两条路各自的口径一致。

### 有意差异与未接的部分

| 旧 | 新 | 理由 |
| --- | --- | --- |
| 部署版 PDF **先试模型管线**（`document_type='bank_statement'`，提示词+文档模型+列来源校验），拿不到行才退回本地链 | 原生**先试免费的文字层**与文本行解析，读不出才走模型 | 部署版那条管线要整个 OCR 文档档位（模型客户端 + 文档模型 + 校验器接线），而原生已具备的是本地链的每一段。顺序调换会改变"谁赢"：同一份 PDF 两边都读得出时，原生采用免费结果。等文档档位落地后要完全对齐，把 `model_rows` 提到分发之前调用即可（本模块的分发顺序是显式可改的一处） |
| 图片 / Word / 坐标解析表格 / pdfplumber | 未接：这些是器件，原生还没有 | 如实返回 `unsupported_format` / `model_unavailable`，不假装能读 |
| 模型客户端、升级策略、缓存、渲染、切页、并发 | 注入：`model_rows(document_type, file_bytes, filename)` | 谁实现由编排层决定；本包已迁的 `bank_model_path`（旧 gemini 路）与 OCR 域的文档档位都能填这个缝 |
| `needs_mapping`（ADR-006 学习层"确认列对应"） | 未迁（第三十九批起就写明）：认不出表头 → 直读失败 → 轮到模型 | 学习层要映射存储表与确认界面；部署版遇到它会弹面板而不是找模型，这条差异在此显式记录 |
| `_parse_bank_statement_impl` 里 `enterprise_adapter` 那一支 | 未迁 | 企业版不在新产品范围（OCR 族清点里已有结论） |

## 二、验证与证据

- **隔离 Site 端到端（本批的重点）**：`scripts/cowork-bank-recon-lab-verify.py` 新增 `BankReconciliationFromFiles` —— 用 openpyxl 造真的对账单工作簿与总账工作簿 → 经 **files 域**上传（查权限、查校验和）→ 控制器 `create_from_sources` → 存储的任务里逐行核对：`matched`（对账单 06-02 取 400 ↔ 总账贷 400，日期容差层）、`stmt_withdrawal_only`（余额 510）、`gl_debit_only`（借 75），行数 3、`parsed.bank_code` 取文件名（bbl）、期初 1000 / 期末 510、`statement_source`/`gl_source` 都是 `workbook`。另加三条：读不了的**对账单**被拒且**一张任务都没落库**；读不了的**总账**被拒；真 PDF 扫描件（文本层只有 "SYNTHETIC SCAN"）在**假模型**下走模型缝（对账单与总账各被问一次，两侧都有答案时任务照常落库，只有一侧有答案时被拒而不是落一张空任务）。整份 18 个用例通过；匿名 HTTP 复核含新 RPC（8 个端点全 403）。
- 离线用例（隔离 bench）：intake **24** 个（工作簿/CSV/PDF 三条路各自不打扰模型、文件名银行码优先、行数不足与坏链两种升级条件、两法分歧留痕、读不了/无表头/无行/不支持格式的错误码、期初两条兜底、换眼只在给了页读取器时发生且工作簿永不换眼、总账的直读/模型/不需要模型/模型失败各条）。银行族一起 **287** 个通过。
- 固定源差分：`bank_arbitration_guard.py` —— 256 组金额对（含符号、地板、阈值、百万级）+ 720 批随机（含 rel/abs_floor 覆盖），**差异 0**；常量逐一比对；部署版 BBL2645 那一例单独固化。
- 静态检查：架构 **0** 违规（两个新模块都在 `reconciliation` 域内；控制器在模块编排层，域检查不适用）；`i18n-build --check` PASS；`check-i18n` REPORT-ONLY（新增一条 `reconciliation.ledger_unparsed`，与该文件既有同类文案同档）；`git diff --check` 干净。
- 线上来源：交付 `evidence/baseline-forty-first-delivery.json`（`789a3f66…`，采集 17:41:34Z）与第四十批交付快照 `compare` **PASS**，来源仍 `ec2e5c95…`；运行投影 `push-runtime-forty-first-delivery.json`（`aea23c1a…`）与上一批**逐项相同**。

### 本批没做开工采集

沿用上一批的做法（`before` 用第四十批的交付快照）：它证明的是"上一批交付到现在线上没变"，不等于"本窗口开工那一刻的身份"。如实记在这里。

## 三、第 ④ 层仍差的一件事：OCR 域的文档档位

现在这条链的模型缝是空的（`create_from_sources` 不传 `model_rows`，所以原生读不出的文件会被明确拒绝，而不是静默变空）。要把它填上还差：

1. OCR 引擎的**文档档位**（`run_invoice` 现在只准入发票类；银行单/总账需要各自的提示词档位与准入，提示词本包已迁 `bank_prompts`）；
2. 把"模型 JSON → 文档模型 → **列来源校验**（第 ② 层）→ entries → 行"接成 `model_rows` 的实现（这条路才是部署版 PDF 的默认路，也才用得上第三十七批的校验器）；
3. 一次真实的模型调用预算（本批与之前各批都没有付过费；真机与付费调用按既有约定不进默认回归）。

## 四、未做

- 未接前端、未部署、未推送、未合回主线；未动共享底座（`features.py`、`pearnly_core/**`、各产品术语表）；
- 未调模型、未跑真实银行单、未跑队列、未做用户验收；未跑多文件合并（部署版 `merge_statements`/`merge_gl_files` 仍未迁，见对账族清点）。
