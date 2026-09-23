# 银行单 / GL 文档级 OCR 第三层后半：GL 直读、文本行与 PDF 文字层（第三十九批）

第三十九批从新平台 `8e92423` 开工，工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`。开工与交付前各独立采集一次线上来源，仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`（固定只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`）。不接前端、不部署、不改 DMS、不写旧库、不调模型。

第 ③ 层「确定性读取器」在本批收口：上一批交了低级表格 IO 与银行流水 xlsx/csv 直读，本批把**总账直读、银行单文本行、PDF 文字层**三块补齐。这一层整体是"不花钱的那条路"——表格、CSV、PDF 文字层能直接读出来时就不必送模型；只有它们都读不出东西，才轮到第 ④ 层的模型路径。

## 一、本批交付

| 新落点 | 来源（sha256 固定） | 内容 |
| --- | --- | --- |
| `reconciliation/gl_sheets.py` | `services/recon/bank_gl_excel.py`（`5999a1bd…`，365 行）的**确定性那一段** + 共用 `services/recon/bank_gl_common.py`（`ddae19a8…`，76 行） | 列名词典、`map_gl_columns`、`extract_account_code`、`attach_running_balance`、`find_gl_header`、`parse_gl_workbook` |
| `reconciliation/bank_text.py` | `services/recon/bank_stmt_text.py`（`ef8cd5ae…`，334 行） | `parse_statement_text_lines`（一行一笔）、`parse_kbank_text_columns`（KBank 列被压成行） |
| `reconciliation/table_io.py` 追加 `extract_pdf_text_pages` | `services/recon/bank_table_io.py`（`19b9161e…`）的 `_pdf_extract_text_safe` | PDF 文字层：pdfminer → pypdf 兜底 |
| `reconciliation/bank_parse.py` 追加 `GL_SKIP_KEYWORDS` / `is_gl_skip_row` | `services/recon/bank_recon_utils.py`（`35b143f7…`）的 `_GL_SKIP_KW` / `_is_gl_skip_row` | 总账的汇总/结转行识别（只看前四格） |

保留的旧口径（都有真实事故，别按直觉改）：

- **总账表头必须同时有日期列和钱列（借或贷）**：只有日期+科目+余额不算表头 —— 旧侧注释写明，否则单列净额 GL 会解析出 0 行还一路跑到"完成"，或在 CSV 路径降级模型把无表头数据硬读成空日期行参与匹配（凭空造出 matched）。
- **期初单独识别且扫该行前四格**：固定模板把"期初"标签放在**日期列**而不是描述列；期初优先读余额列（模板与 Mr.erp 把期初放余额列），没有余额列才退回"贷 − 借"。
- **空日期沿用上一行的日期**（Mr.erp 一天只印一次日期）；借贷两列都是 0 的行不是交易。
- **期末优先用表内余额列的最后一笔**：老公式 `期初 + 贷 − 借` 对收入科目正确、对资产科目方向相反（BUG-FIX-T2）；`closing_printed` 如实说明期末是读来的还是算出来的。
- 文本行解析：方向靠**余额差**判断（差 = +金额 → 存、= −金额 → 取），差额对不上才退回描述里的动词，再不行按存款；泰文用句点当千分位（`115.586.50`）要认；摘要里的数字不能把"金额+余额"这一对挤掉（取**最右边**那个至少两个数的连续数字组）。
- KBank 列压行：先按"日期行"抽交易头，再按 金额/余额 交替配对，**第一个没有时间的交易头是期初那行，去掉**；余额那行右侧的渠道文字并进摘要。

### 有意差异（都不改判定）

| 旧 | 新 | 理由 |
| --- | --- | --- |
| `bank_gl_excel` 的 ADR-006 **模板学习层**（已存映射 / 本地推断 / `allow_ai` 时调模型 / 否则 `needs_mapping` 交前端确认列对应） | **不迁** | 与上一批同因：它要一张映射存储表和一个确认界面。后果写明：表头认不出时返回 `gl_headers_not_found`，即旧代码在"没有学习层"时的结果。固定词典里也没有单列净额（`amount`）键，所以旧代码里"按符号拆借/贷"那段只有学习层给映射时才走得到——本批照旧保留该分支并写明它现今不可达 |
| 旧函数的私有名与模块划分 | 公开 `map_gl_columns` / `find_gl_header` / `extract_account_code` / `parse_gl_workbook`、`parse_statement_text_lines` / `parse_kbank_text_columns`、`extract_pdf_text_pages` | 这一层是给第 ④ 层接线用的公开入口；判定与返回串一字未动 |
| `_parse_stmt_text_lines(text, bank_code)` 的 `bank_code` | 保留形参 | 部署版收下这个参数后**从未读过它**，而调用方一直传；形状照旧免得接线改两处 |
| `_pdf_extract_text_safe` 里的 `logger` / 旧文件级注释 | 不迁日志器 | 纯模块不持有 logger；两个解析函数里本来就没有日志调用 |
| 文本行解析使用的 `_to_float` / `_parse_date` | 注入本侧已迁的 `bank_parse.to_float` / `parse_date` | 这两个原语在此前批次迁入并差分过，不重复搬 |

## 二、验证与证据

- 离线用例（隔离 bench）：总账 **23** 个 + 文本行/PDF **13** 个 = 本批 **36** 个。覆盖：四语列名与"同一角色只认第一次出现"、表头必须带钱列（单列净额不算 → `gl_headers_not_found`）、表头在前 10 行、科目号（裸号/分级号/从凭证或摘要里抽/长数字与小数旁不算）、运行余额方向、期初读余额列与"贷−借"回落、空日期沿用、无日期行丢弃、汇总/结转行跳过、无发生行不算交易、`account_code` 前缀过滤、`closing_printed` 真假两态、认不出表头、空文件报"读不了"、任意字节落到 CSV 兜底后报无表头、CSV 带与不带扩展名；文本行的期初/期末标记、余额差定方向、差额对不上退回描述动词、首行无前余额按存款、泰文句点千分位、不能成交易的行跳过、零发生跳过、期末回落末行、空文本；KBank 列压行的期初与两笔配对、渠道并入摘要、无日期返回空；PDF 文字层读出文本、非 PDF 与空字节返回空。
- 固定源差分（只执行被搬的纯定义；行类、数字/日期解析、CSV 装载器从本侧注入，因为此前批次已迁并差分过）：**差异 0**。计数：总账 225 批（出结果 187、行合计 166、非零期初 115、期末读自表内 61、无表头 37、读不了 1、其中 CSV 固定样例 2 批、带科目过滤 19）；文本 187 批（一行一笔 182 批/284 行、存 216、取 68、有期初 136、有期末 159；KBank 列压行 5 批/4 行）；PDF 4 批（2 批读出文字、2 批返回空）。比对整份结果：总账逐行九个字段（含去重指纹 `row_hash`）、账户表、期初/期末与 `closing_printed`，失败时连**错误文案**一起比；文本逐行五个字段与期初/期末。列名词典、科目号正则、期初关键词表、`_GL_SKIP_KW`、`AMOUNT_TOL` 另有等值比对。
- 同族回归（隔离 bench）：银行族与读取器一起 **228** 个用例通过；上一批的流水读取器差分重跑（`table_io` 本批有新增）仍是 **227 批差异 0**（汇总行泄漏 0）。
- 静态检查：架构 **0** 违规（新模块都在既有 `reconciliation` 域内，无新增跨域方向）；`i18n-build --check` PASS；`check-i18n` REPORT-ONLY（注释与既有同类，未动术语表）；`git diff --check` 干净。
- 跑差分时 pypdf 会对"不是 PDF 的字节"打印 `invalid pdf header` 到 stderr —— 那是库自己的告警，两侧都打，不是差异。

### 线上来源

开工 `baseline-thirty-ninth-start.json`（`b9a7c2cc…`）与第三十八批交付 `compare` **PASS**；交付 `baseline-thirty-ninth-delivery.json`（`bfcf1183…`，采集 17:18:00Z）再次 PASS。运行投影 `push-runtime-thirty-ninth-delivery.json`（`c76b26dc…`）与上一批**逐项相同**（`settings_unchanged=true`、`observations_changed=false`）。**完整配置与旧状态迁入继续 HOLD**。

## 三、第 ③ 层至此交完，剩下第 ④ 层

确定性读取器这一层已闭合：银行流水 xlsx/csv（第 38 批）、总账 xlsx/csv、银行单文本行、KBank 列压行、PDF 文字层（本批）。**没有搬的**是旧侧同目录下另外几支读取路径，各自有归属：

- `bank_stmt_extract.py` / `bank_gl_pdf.py` / `bank_gl_pdf_mrerp.py` / `bank_gl_stacked.py` —— pdfplumber 表格与坐标解析、MR.ERP 版式、堆叠版式：属"器件"（打开 PDF、拿坐标、按列归位），新平台的边界是"调用方交出已解码内容"；
- `bank_stmt_gemini.py` / `bank_gl_gemini.py` —— 模型路径，归第 ④ 层；
- `bank_stmt_reread.py`（整页升档重读 `reread_bank_page`）—— 归第 ④ 层；
- `bank_arbitration_guard.py`、`bank_recon_v2.py` 的编排 —— 归第 ④ 层的执行接线。

第 ④ 层要做的：银行/GL 提示词、引擎的文档档位、执行接线，以及把 ①②③ 三层接进原生银行任务（那时才让这些读取器真的被调用）。

## 四、未做

- 未把任何读取器接到调用方：第 ④ 层还没开工，接半条链的入口等于假装它能用。
- 未跑真实文件、未调模型、未跑队列、未接前端、未部署、未推送、未合回主线；未动共享底座（`features.py`、`pearnly_core/**`、各产品术语表）。
