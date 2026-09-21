# Cowork 后端迁移：收入对账的总账解析（第二十七批）

日期：2026-09-21。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，本批基线 `2e33245`（第二十六批交付；`debf942` 已并入 main `42ed146`）。

本包把旧项目收入对账的**总账侧解析**按部署版迁入，并给它一个受控入账入口：`income_parse`（已抽取内容的解码）+ `income_recon_service.create_from_parsed` + 一个 POST RPC。销项税报告由另一个解析族（`services/vat/**`）负责，不在本包；读文件、跑 OCR、调用模型仍留在旧项目。

## 边界为什么切在"已抽取内容"

银行那批（第 23 批）的边界是"解析器产出的已解码页字典"，因为那条链的解码要经过 OCR/模型。总账侧同理：`parse_gl_excel` 打开工作簿、`parse_gl_pdf`/`_parse_gl_text_lines` 打开 PDF、`_parse_gl_via_pipeline` 走 OCR 管线——**读取本身留在旧项目**；迁入的是它们拿到单元格、文字行、表格与管线条目之后的判定。这样新平台既不引入 pdfplumber，也不在解析里藏外部调用。

| 来源 | 性质 | 本批处置 |
| --- | --- | --- |
| `services/recon/gl_vat_parse_common.py` | 叶子 helper 与列名常量（`_to_float`/`_is_revenue_acct`/`_hit`/`_extract_account_code`/`_is_debit_line`/`_is_skip_row`/`_map_gl_columns`/`_row_has_amount`/`_ACCT_RE`/`_SKIP_ROWS`/`_DEBIT_LINE_KW`） | **迁入**，逐条保持语义 |
| `services/recon/gl_vat_parse_excel.py: parse_gl_excel` | 工作表迭代 → 行判定（两种版式：科目列 / 科目分节标题） | **迁入**（入参改为每表单元格行） |
| `services/recon/gl_vat_parse_excel.py: _gl_rows_from_pipeline_legacy` | 管线归一化 JSON → 收入行 | **迁入** |
| `services/recon/gl_vat_parse_pdf.py: parse_gl_pdf` 的表格路径 | pdfplumber `extract_tables()` → 行判定（页面文字给分节标题） | **迁入**（入参改为每页文字 + 表格） |
| `services/recon/gl_vat_parse_pdf.py: _parse_gl_text_lines` | 文字行 regex → 行判定（两种数字尾、日期继承、余额判借贷、退货词） | **迁入**（入参改为文字行） |
| `parse_gl` / `_parse_gl_impl` / `_parse_gl_via_pipeline` | 按扩展名分发、计费上下文、OCR 管线、模型调用 | **未迁**：留在旧项目；新入口按调用方声明的 `kind` 分发 |
| `services/vat/**`（`vat_report_parser`、`vat_parser_*`、`vat_ocr_*`） | 销项税报告的解析族（含 OCR） | **未迁**：独立家族，随后续批次；本入口的销项税行由调用方交已解码数据 |

固定来源只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`；差分脚本先校验 SHA-256，再只执行选定的纯定义。

| 文件 | SHA-256 |
| --- | --- |
| `services/recon/gl_vat_parse_common.py` | `ca59a5d1b313806e4358ad5a29b8abfa8b906b1dcc1d2f26128aee932eac5403` |
| `services/recon/gl_vat_parse_excel.py` | `1b81310ee241186e6288db01376ea90217d2e1f6771167c7aa9d457baa65b115` |
| `services/recon/gl_vat_parse_pdf.py` | `a97a188b1d0bedd3ab0862091baa863c4a49d15ae5a30f72eb0bbde41f0a049e` |
| `services/recon/gl_vat_types.py`（已有） | `ee24b7dd0a7a24e67ac338f7c7deae73c71c36aca5b731ddc18b6e6af27a300d` |
| `services/recon/field_comparator.py`（已有，单号归一化） | `109c0401587154d3f3860166b4f0c3812f568b03e2e452a880af866195eec037` |

## 落点与保持的判定

实现：`reconciliation/income_parse.py`。

- `SOURCE_FORMAT = "decoded_gl_rows_v1"`、`PARSER_VERSION = "1.0.0"`。
- 入口 `decode(source, revenue_prefix="4")`，`source["kind"]` 取 `excel`（每表单元格行）/ `pdf`（每页文字 + 表格 + 文字行）/ `text`（文字行）/ `pipeline`（管线归一化 JSON）；返回部署版形状 `{"ok","rows","row_count","error","diag"}`，行是 `GlRow`。
- `decoded_rows(rows)` 把结果转成入库行契约（`income_recon_contracts` 的键集），入账走既有 `create`。

原样保留的细节（差分逐条对齐）：

- 金额一律 `_to_float`：去千分位、去 `\xa0`、括号为负、`round(2)`、读不出算 0；科目代码正则不允许小数点分隔（`419.57` 不会被当科目），允许 4–7 位加 2–3 位后缀。
- 表头判定是"任一单元格命中借方词 **且** 任一命中贷方词"，命中即整行当表头（`credit`/`cr` 这种宽匹配也照旧）。
- Excel 路径：无科目列时，带金额的行不会被当分节标题；分节标题需"有收入科目码且当前已知列里没有金额"；日期单元格转 ISO 并做佛历回退（≥2500 减 543）；数量为 0 或没有凭证号的行丢弃。
- 表格路径：分节标题先于表头判定，且页内文字里的第一个收入科目码会把整页的行归到它名下；单元格文本**不**做日期转换（`datetime` 会打印成 `2569-06-01 00:00:00`）。
- 文字行路径：三数字尾直接取借贷；两数字尾按"余额绝对值变化"判方向、判不出时落贷方；退货词（ลดหนี้/รับคืน/คืนสินค้า/ส่งคืน）优先判借方；无日期的行继承上一行日期；分节标题里的括号期初余额用于方向判定；`รวม...` 开头且无凭证号的行跳过；描述取"凭证号之后到第一个尾数字最后一次出现"之间（因此余额以同样数字结尾时描述会被拉长——差分确认与部署版一致）。
- 分发保持"表格优先、0 行回退文字行"的行为，回退时把主路径诊断（`primary_*`）附加到诊断块。

## 有意差异

1. **入参边界**：调用方交出已抽取的单元格/文字行/表格/管线条目；打开文件与 OCR 由调用方做。部署版由 `_parse_gl_impl` 按扩展名选读取器，新入口按调用方声明的 `kind` 分发。
2. **解码结果要过入库契约**：管线路径的金额是裸 `float()`（不像打印路径会 `round(2)`），所以超过 6 位小数的金额（例：`1070.1234567`）在这里被拒绝而不是入库；超长文本同理。部署版会把二进制 float 直接存进明细 JSON。
3. **不再回退到 OCR 管线**：部署版 PDF 表格与文字行都 0 行时回退到管线；新平台两条本地路径都 0 行就报 0 行，入账入口以 `reconciliation.gl_no_revenue_rows` 拒绝，回退责任交给调用方。
4. **诊断只回传给调用方**：`method`/`accounts_seen`/`candidate_data_lines`/`primary_*` 随 `parsed` 返回，尚未入库（任务表还没有诊断字段，与银行那批补 `parse_info_json` 之前的状态相同）。
5. Excel 与表格两条路径的 `diag` 是迁移新增（部署版只有一个 `diag`，在 PDF 路径）；键名沿用部署版 PDF 路径的命名。
6. 判据参数 `revenue_prefix`（旧前端"收入科目前缀"）由调用方传入，默认 `4`，与部署版一致。

## 验证

```sh
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest tests.test_cowork_income_parse
PYTHONPATH=apps/pearnly_cowork python3.14 tests/test_cowork_income_parse_parity.py \
    --source /tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest discover -s tests -p 'test_cowork_reconciliation*.py'
python3 scripts/check-architecture.py
python3 scripts/i18n-build.py --check
git diff --check

# 隔离 lab（Docker context colima-pearnly，project pearnly-cowork-backend-lab）
bench --site cowork-backend-preparation.localhost migrate     # 补齐上一批新增的三列计数
../env/bin/python /workspace/scripts/cowork-income-recon-lab-verify.py   # 两个 Site 各 13 个
```

- 离线契约用例 **29** 个通过（本批新增）：叶子 helper 的金额/科目码/跳过行/表头/方向词、Excel 两种版式与佛历日期、表格路径的版面与单元格文本、文字行两种数字尾与日期继承与退货词、分发与拒绝、入库契约键集对齐。
- 固定源差分：**305** 批输入 × 四条路径逐字段比对（Excel 161 行、表格 172 行、文字行 156 行、管线 70 行；佛历日期单元格 96 行、文字行贷方 113 行 / 借方 100 行 / 三数字尾 57 行），另加 96 组叶子 helper 用例，**差异 0**；覆盖率下限不达即失败。差分用内存替身顶替 `openpyxl`/`pdfplumber` 的**读取**，把同一份单元格/文字行/表格交给两侧，读取行为不属于本次迁移内容。
- 隔离 Site：两个 Site 各 **13** 个用例通过（上一批 10 个 + 本批 3 个：已抽取工作表入账并与解码行入口的汇总逐字段相同、文字行/表格经同一入口入账、无收入行/读不出/未知来源/精度越界在入库前被拒且库里无残留），五个端点匿名 HTTP 全 **403**。
- 架构 **0** 违规；`i18n-build --check` PASS（新错误键是 ASCII 语义键，沿用既有 `reconciliation.*` 口径，未登记文案）；`git diff --check` 干净。
- 未执行：真实 Excel/PDF 文件的读取（openpyxl/pdfplumber 不在本批范围内）、销项税报告解析、异步任务与轮询、复核工作簿导出、批删、前端、部署、外部结果与用户验收。

## 来源核对与运行投影

- 开工 `baseline-twenty-seventh-start.json`（`295f2aad…`）与交付 `baseline-twenty-seventh-delivery.json`（`db984f61…`）：`source` 均 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，`compare` **PASS**。
- 运行投影 `push-runtime-twenty-seventh-delivery.json`（`13a445a5…`）：端点 `configuration_digest` 由 `bbb0fe7e…` 变为 `1ed03962…`（现场运行 60 端点、两个平台开关不变）。该摘要每轮采集都在动，是在跑的旧侧代理写旧库；完整配置与旧状态迁入继续 **HOLD**。

## 未做与后续

- 收入族后续：销项税报告解析（`services/vat/**`）→ 文件诊断与文件名入库 → 异步任务与轮询 → 复核工作簿导出（`gl_vat_excel`）→ 批删。银行侧剩余项（异步状态与轮询、批删、手动覆盖痕迹/运行警告落库）仍挂着。
- 本批顺带把 `cowork-backend-preparation` 的 Site 结构补到当前（上一批新增的 `matched_count`/`diff_count`/`unmatched_count` 三列），两个 lab Site 现在与提交里的 DocType 定义一致。
- 未接前端、未部署、未合并进 main、未推送；除只读的来源采集外没有外部调用，没有写任何旧库。
