# Cowork 收入对账 / 销项核查：识别出的税报逐行自检（2026-09-30）

来源：压测窗口（收入 / 销项 OCR 修复第 1、2 条）：照片税报行错误 3–4%，要做到「错的都被点名」。

- 分支 `codex/cowork-report-row-check`，工作树 `.claude/worktrees/cowork-report-row-check`，叠在 `codex/cowork-recon-reread`（025668a7）上，
  它合入 main 后再变基。只改 `pearnly_cowork`。只作用于识别读出的税报（照片 / 扫描件），表格与文字层 PDF 不变。

## 改动

| 位置 | 改动 |
|---|---|
| `reconciliation/vat_parse.py` `pipeline_rows` | 行日期先按印出来的原文（`transaction_date_raw`，佛历 / 日-月-年）换算，换算不出再用模型的 `transaction_date`（rG IMG_2540：原文 17/05/2569、模型给 05-16） |
| `ocr/enterprise/self_check.py` `suspect_report_rows` | 逐行自检：税额与税基×7% 差 > 0.01（税额 0 的零税率 / 免税行不算）、只有税额没有税基、同一份报告里单号重复、单号开头印的佛历年月日（「690526-011」）与行日期不同（差一年内才比） |
| `recon_center/vat_check.py` | 自检没过的行点名（单号 + 原因，最多 30 行）进收入对账与销项核查的标题下提示（`reconciliation.report_rows_suspect` 与四个原因词条，四语） |

## rM 跟进（2026-09-30）

- rM：识别读错的 12 行全部被点名；行自检 7 行里 5 行误报（原单据本来如此）：
  - 02000076 / 077 税额与税基×7% 差 0.02，是含税价倒算税额的正常尾差 → 改为按税基×7% 或含税价×7/107 倒算任一差 0.05 以内都算对；
  - 690519-007 等单号上的日子与开票日差 1–3 天（先编号后开票、补录）→ 差 3 天以上才报（真读错的 690526-011 差 15 天）。
- 提示按文件归在一起写（文件名只写一次），少被截断。

## 验证

- `tests/test_cowork_recon_reread.py` 新增 2 条；`test_cowork_rec*` / `vat*` / `income*` 通过；税表解析旧线上对照 `test_cowork_vat_parse_parity` 0 差异
  （对照样本没有 `transaction_date_raw`，旧读法不变）。
- 真实语料复测待压测窗口。

## 未做

- 第 4 条（发票买方税号为空 / 校验位不过）：现有比对（`vat_check_core._diff_dims`）已把「发票上空、报告有」列为税号差异，待压测窗口确认
  「静默当作一致」出在哪个结果上再改。
- 未合并、未部署。
