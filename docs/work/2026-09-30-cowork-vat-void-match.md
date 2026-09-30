# Cowork 销项税报告核查：作废一致（2026-09-30）

来源：用户 2026-09-30 同意——税报上作废的发票（行里标「*」或写着 ยกเลิก，未税 / 税额 / 含税全是 0 或空）和发票那边同一张
（可能盖作废章、金额不为 0）原来被判成差异或两边各自缺失，每次都要人去查；改成结果行显示「作废一致」，不计入差异 / 缺失。
压测实例：作废票识别时单号掉了开头的「I」（V69/00581、V6900585、V69.00572，税报上是 IV69/00581 等）。

- 分支 `codex/cowork-vat-void-match`，工作树 `.claude/worktrees/cowork-vat-void-match`，基线 `codex/cowork-recon-reread` `d94b133e`
  （销项核查的相关改动在它上面，尚未合入 main）。
- 只改 `pearnly_cowork`；共用层、DocType、前端未动。

## 改动位置

| 位置 | 改动 |
|---|---|
| `reconciliation/vat_check_core.py` | 作废判定（`_void_marked` / `_zero_row` / `_void_report` / `_void_pair`）与「只差开头字母」（`_number_parts` / `_same_but_leading_letters`）；`_build_recon_pairs` 第 1 轮同号先配正常行、配上的作废行记 `kind=void`，第 1 轮之后作废行按只差开头字母再配，没有发票的作废行单独返回 `void_rep`。文件头登记有意改动 |
| `reconciliation/export/vat_check_sheet3.py` | `void` 行状态「✓ 作废一致」、`void_rep` 行「✓ 作废（无需发票）」，都是绿底；作废行 6 个维度差异列清空、差异金额记 0、不进差异金额合计；`n_diff` 不含作废，另返回 `n_void` |
| `reconciliation/export/vat_check_workbook.py` | 汇总（`summary_json`）多一个 `n_void` |
| `i18n/messages.tsv` + `translations/*.csv` | `vat_check.status.void_match`（作废一致）、`vat_check.status.void_no_invoice`（作废（无需发票））四语，`scripts/i18n-build.py` 生成 |
| `tests/test_cowork_vat_void.py` | 新增离线测试 |

## 判定规则（最终实现）

1. **税报作废行**：未税、税额、含税全是 0 或空，且单号 / 参考号 / 客户名里有「*」或作废字样（ยกเลิก、作废、作廢、取消、無効、
   cancel、void；先做 NFKC、大小写、PDF 私用区泰文规范化）。解析出的税报行没有单独的备注列，所以只看这三格。
2. **只有发票有标记也算**：税报行金额全 0 但没标记，配上的发票单号 / 客户名里有作废字样（发票表里的作废行常这样写）→ 作废一致。
3. **配对**：
   - 单号按 `normalize_invoice_no` 相同 → 第 1 轮照旧配上；同号既有作废行又有正常行（作废后重开同号）时先配正常行。
     配上后若满足 1 或 2 → `void`。
   - 第 1 轮之后，没配上的金额全 0 行：两边单号按 `normalize_invoice_no` 规范化、只留字母数字，从第一个数字起的部分相同，且一边
     开头的字母是另一边开头字母的结尾（掉了最前面的字母；IV ↔ CN 这种换了字母的不算）。这张发票只像这一行、这一行也只像这张
     发票（两边候选都唯一）才配，且须满足 1 或 2；不唯一不配。
4. **没有发票的作废行**：作废本来就不需要发票，显示「作废（无需发票）」、绿底，不算缺失（`kind=report_void`，不以 `orphan` 结尾）。
5. **发票那边的作废标记**：识别结果没有 is_void / status 之类字段；模型的 `notes`（备注）没带进核查记录，也没用——发票页脚常印
   退换 / 取消条款（带 ยกเลิก 字样），拿来判作废会误报。所以发票侧只看单号 / 客户名。
6. **计数**：`n_ok`（列表「匹配」）不含作废；`n_diff = n_total − n_ok − n_void`，所以「差异」「缺失」都不含作废；作废数记在汇总
   `n_void`。任务 DocType 没有作废数的列，列表页不显示这个数（要显示需加字段和迁移，未做）。

## 有意变化

- 与旧线上 ec2e5c95 的差别只有上面的作废处理（配对多 `void` / `void_rep`、状态、计数、汇总 `n_void`）。销项核查的配对与
  工作簿没有旧线上对照测试（`vat_check_core` / `vat_check_sheet3` 不在任何 parity 测试里），所以没有需要登记的对照差异。

## 验证（本地离线，frappe-sample:16.34.1 镜像，`--network none`）

- `test_cowork_vat*.py` 63 条通过（含新增 `test_cowork_vat_void.py` 9 条：同号作废一致、各种标记与空金额、掉开头字母 4 种写法、
  候选不唯一不配（两张发票像一行 / 一张发票像两行 / 换了字母）、只有发票有标记、同号优先正常行、有金额的标记行不算作废、
  工作簿状态 / 绿底 / 计数 / `_counts`、没有作废行时不需要翻译）。
- `test_cowork_rec*.py` 461 条通过；`test_cowork_*.py` 全部 1910 条只有 2 个导入错误（镜像里没有 `docx`，与本改动无关）。
- `scripts/check-architecture.py` PASS；`tests/test_native_desk_contract.py` 22 条通过；`scripts/i18n-build.py --check` PASS。
- 旧线上对照（`--source` 挂 ec2e5c95）：`test_cowork_vat_parse_parity` 0 差异；`test_cowork_reconciliation_income_vat_parity` 0 差异。
  `test_cowork_income_recon_export_source_parity`（标签断言）与 `test_cowork_reconciliation_source_parity`（reconcile rows drifted）
  在基线 d94b133e 上同样失败，与本改动无关。
- 未做：测试环境试用、真实税报 / 作废票语料复测（需压测窗口按批次安排）。

## 未做 / 拿不准

- 「*」若印在解析器没映射的单独一列（不在单号 / 客户名格子里），解析时就丢了，这种行只能靠发票侧标记判定；要覆盖需改 `vat_parse`
  保留标记列（会动旧线上对照），这次没做。
- 列表页不显示作废数（见上）。
- 发票表 / 发票照片里有、税报里没有的作废发票（发票孤儿）不在本次范围，仍显示「发票有 · 报告无」。
