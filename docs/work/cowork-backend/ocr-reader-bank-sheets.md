# 银行单 / GL 文档级 OCR 第三层前半：确定性读取器（第三十八批）

第三十八批从新平台 `242339d` 开工，工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`。开工与交付前各独立采集一次线上来源，仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`（固定只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`）。不接前端、不部署、不改 DMS、不写旧库、不调模型。

这是[四层计划](ocr-document-chains.md)的第 ③ 层「确定性读取器」。这一层的意义是**不花钱的那条路**：文件本身就是电子表格或 CSV 时，列名、数字、日期都能直接读出来，没有必要送去模型识别；只有这条路走不通才轮到第 ④ 层的模型路径。本批交付其中的第一块——**低级表格 IO + 银行流水 xlsx/csv 直读**；GL 直读、银行单文本行、PDF 文字层属于同一层，放下一批。

## 一、本批交付

| 新落点 | 来源（sha256 固定） | 内容 |
| --- | --- | --- |
| `reconciliation/table_io.py` | `services/recon/bank_table_io.py`（`19b9161e…`，151 行） | `header_hit`（旧 `_hit`）、`load_workbook_sheets`（旧 `_load_excel_all_sheets`：openpyxl → xlrd 兜底）、`load_csv_sheets`（旧 `_load_csv_sheets`：五种编码 + 分隔符嗅探） |
| `reconciliation/bank_sheets.py` | `services/recon/bank_stmt_xlsx.py`（`e8df8a52…`，498 行）的**确定性那一段** | 表头词典、`map_statement_columns`、`find_statement_header`、`extract_sheet_account`、`scan_preheader_opening`、`parse_statement_sheet`、`read_statement_workbook` |
| `reconciliation/bank_parse.py` 追加 `bank_code_from_filename` | `services/recon/bank_recon_utils.py` 的 `_bank_from_filename` | 文件名里的银行识别（只认 ASCII 关键词、词边界匹配、泰文品牌名不参与） |

搬过来的是**路径本身**：`header_hit` 里"短 ASCII 词（`in`/`out`/`cr`/`dr`）必须整词匹配"这条，就是 KTB 真实事故留下的（分行号列 `Init Br.` 被当成存款列）；文件名优先于内容这条，是银行单塞满跨行转账、对手行名出现上百次而自家品牌在 logo 图片里留下的。

保留的旧口径（都有真实事故，别按直觉改）：

- 表头可在前 **16** 行任意位置（KTB 在第 11 行）；
- 没有独立存取列时用**单一带符号 amount 列**（正=存、负=取）；
- 多账户（每 sheet 一个）**各自用各自的期初**跑余额链，绝不跨账户 —— KTB 8258（期初 −39）与 8606（期初 −740 万）混成一条链会让整链作废；
- 期初汇总区被银行整块留白时，用首笔交易「余额 − 净额」反推期初（唯一可证的算术）；
- 期末取**最后一个带余额的行**（末行常是无余额的 Sweep 行），且「余额真的是 0」与「余额单元格空着」必须分开（旧代码曾把归零户的 0 当空，期末报成上一行的值）；判空看**原始单元格**，因为 `0.0` 在字符串化那一步已经变成 `""`；
- 汇总/合计行整行跳过，且**必须在记期末之前跳**（否则 `รวมยอด` 那种大额合计会污染期末）；
- 逐账户做「期初 + Σ存 − Σ取 = 期末」交叉校验（`closing_mismatch`），行级断链汇总成 `balance_break`，两头缺真值就不报（不误报）。

### 有意差异（都不改判定）

| 旧 | 新 | 理由 |
| --- | --- | --- |
| ADR-006 **模板学习层**（查已存映射 / 本地推断 / `allow_ai` 时调模型问列对应 / 否则返回 `needs_mapping` 交前端弹"确认列对应"） | **不迁** | 它要一张映射存储表和一个确认界面，属另一件事，本平台还没落。后果写明：表头认不出来时返回旧代码在"没有学习层"时的那个结果 `stmt_headers_not_found`，不会假装能交 `needs_mapping` |
| `_pdf_extract_text_safe`（同一份 `bank_table_io.py` 里的 PDF 文字层） | 本批不迁，随 PDF 路径下一批走 | 现在搬过来就是一段没人用的代码 |
| `_is_summary_row` + `_SUMMARY_ROW_KW` | 早已随余额链迁到 `bank_statement.is_summary_row`（同一份关键词表），本批直接复用 | 同一个判断两处实现会分叉 |
| `_parse_stmt_sheet` 里内联过滤汇总行 + 三次调用 `_correct_direction_from_balance` / `_verify_row_balances` / `_repair_amount_from_balance` | `bank_statement.finalize_rows`（过滤 + 同序三步），**每账户各跑一次** | 原生侧这三步已经合成一个入口（第三十四批迁入），顺序与旧侧逐字相同；每账户不跨链这条旧口径原样保留 |
| 私有名 `_map_bank_stmt_cols` / `_find_stmt_header` / `_extract_sheet_account` / `_scan_preheader_opening` / `_parse_stmt_sheet` / `parse_bank_stmt_xlsx_direct` | 公开 `map_statement_columns` / `find_statement_header` / `extract_sheet_account` / `scan_preheader_opening` / `parse_statement_sheet` / `read_statement_workbook` | 这一层是给后面的接线用的公开入口，不再是文件内部助手；判定与返回串一字未动 |

结果字典形状与旧侧一致：`{ok, rows, row_count, opening, closing, bank_code:"generic", parser_version, sheets_parsed, accounts, account_codes, multi_account, completeness:{ok,issues}, needs_review:False}`；失败两条：`file_unreadable`、`stmt_headers_not_found`。`parser_version` 仍是旧串 `bank_recon_v2+xlsx_direct_v3`。

## 二、验证与证据

- 离线用例（隔离 bench）**36** 个：`header_hit` 的短词整词/长词子串/空表头；四语表头与单一 amount 列、同一角色只认第一次出现、表头在第 2 行/只扫前 16 行、缺日期或缺余额不算表头；账户号取标签行、退回 sheet 名、不像账号的 sheet 名不认；期初标签行读取、期初为真 0 不算缺、没有标签就是 None；逐行账户号与日期、缺日期沿用上一行、期初行不进 rows、带符号 amount 拆成存取、汇总行在记期末前被跳、无发生行跳过、真 0 期末不被上一行覆盖、缺余额列不崩；单 sheet 一个账户、多账户各自期初、期初汇总区留白时反推、断链与期末对不上各自冒到 `completeness`、没有流水表的 sheet 跳过、全都没有就报 `stmt_headers_not_found`、读不了的文件报 `file_unreadable`、CSV 的编码与分隔符（含没有 `.csv` 后缀也走 CSV 兜底）；文件名给银行 / 偶然子串不算 / 泰文品牌名忽略 / 无匹配返回空。
- 固定源差分（只执行被搬的纯定义；行类、数字/日期解析与三个余额链步骤从本侧注入，因为它们在此前批次已迁并差分过 —— 这样比较**只隔离本批新搬的读取器**）：**227** 批，差异 **0**。计数：装入并出结果 144、多账户 27、账户 171、`closing_mismatch` 130、`balance_break` 110、读不了 1、无表头 82、**汇总行泄漏 0**、CSV 1。两边喂同一批内存工作簿/CSV，比对**整个结果**：状态、逐账户行、期初/期末、聚合与完整性条目；表头词典、`_STMT_ACCT_LABEL`、`_STMT_OPEN_KW` 与 `_hit` 另有等值比对。
- 静态检查：架构 **0** 违规（两个新模块都在既有 `reconciliation` 域内，无新增跨域方向）；`i18n-build --check` PASS；`check-i18n` REPORT-ONLY（新注释与既有同类，未动术语表）；`git diff --check` 干净。

### 线上来源

交付时独立采集一次：`evidence/baseline-thirty-eighth-delivery.json`（`3e1696c9…`，采集 17:10:00Z）与第三十七批快照 `compare` **PASS**，`source` 仍 `ec2e5c957e6b…`；运行投影 `push-runtime-thirty-eighth-delivery.json`（`65f2c18b…`）与上一批**逐项相同**（`settings_unchanged=true`、`observations_changed=false`，端点摘要仍 `c5882f4b…`）。**完整配置与旧状态迁入继续 HOLD**。

## 三、下一包（第 ③ 层剩下的）

同一层还剩三块，都在旧侧同一个目录：

1. `bank_gl_excel.py`（365 行）——GL 直读，`_GL_OPENING_KW` + `parse_gl_excel`；
2. `bank_stmt_text.py`（334 行）——银行单的文本行解析（PDF 抽出的文字行）；
3. `bank_table_io._pdf_extract_text_safe`——PDF 文字层（pdfminer → pypdf）。

再之后是第 ④ 层：银行/GL 提示词、引擎的文档档位、执行接线、整页升档重读 `reread_bank_page`，以及把 ①②③ 三层接进原生银行任务。

## 四、未做

- 未把 `read_statement_workbook` 接到任何调用方：第 ③ 层还没搬完、第 ④ 层没开工，接一个半条链的入口等于假装它能用。
- 未跑真实文件、未调模型、未跑队列、未接前端、未部署、未推送、未合并主线；未动共享底座（`features.py`、`pearnly_core/**`、各产品术语表）。
