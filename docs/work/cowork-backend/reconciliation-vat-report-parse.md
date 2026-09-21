# Cowork 后端迁移：收入对账的销项税报告解析（第二十八批）

日期：2026-09-21。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，本批基线 `d8f7e3f`（并入 main `21581e4` 之后的合并提交；第 27 批交付为 `d9acfef`）。

本包把旧项目销项税报告（ภาษีขาย）的解析按部署版迁入，并接到同一个受控入账入口：`vat_parse`（已抽取内容的解码）+ `income_recon_service.create_from_parsed` 新增 `vat_source` 入参。至此收入对账的**两侧输入**都能从"旧项目抽取出的内容"一路走到存储；净下来的只有异步与导出。

## 边界同样是"已抽取内容"

| 来源 | 性质 | 本批处置 |
| --- | --- | --- |
| `services/vat/vat_parser_common.py` | 叶子 helper（`_hit`/`_to_float`/`_map_columns`/`_build_row`/`_filter_garbage_rows`）与列名词表、垃圾行常量、`PARSER_VERSION`、`MIN_DETERMINISTIC_ROWS` | **迁入** |
| `services/vat/vat_parser_excel.py: parse_excel` | 活动表 → 表头识别 → 行 | **迁入**（入参改为该表的单元格行） |
| `services/vat/vat_parser_pdf.py: parse_pdf_text` + `_parse_vat_pdf_text_lines` | 抽表 + 文字行 regex 两条通道 | **迁入**（入参改为每页文字/表格与文字行） |
| `services/vat/vat_parser_csv.py: parse_csv_direct`（含 MR.ERP 列名表、Decimal 金额） | CSV/TSV 确定性直读 | **迁入**（入参改为已解码文本 + 分隔符；字节→文本的编码嗅探留在调用方） |
| `services/vat/vat_report_parser.py: _parse_vat_via_pipeline` 的转换半段 | 管线归一化 JSON → 报告行 | **迁入** |
| `parse_vat_report`、`_parse_vat_report_impl`、`_parse_vat_via_pipeline` 的管线调用、`vat_parser_gemini`、`vat_ocr_*`、`vat_excel_*`、`vat_report_checks/merge` | OCR 与 Gemini 调用、计费上下文、引擎档、工作簿导出、报告校验 | **未迁**：留在旧项目；新入口按调用方声明的 `kind` 解码 |

固定来源只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`；差分脚本先校验 SHA-256，再只执行选定的纯定义。

| 文件 | SHA-256 |
| --- | --- |
| `services/vat/vat_parser_common.py` | `ae435d57ef32f5c14b2cf43953e77d1a36b5f502cdf2c9539cf940b7aa46e2a1` |
| `services/vat/vat_parser_excel.py` | `3c4733035f994773f4ff2acb27162ef972c731e8c300ad2e951903ec8d7c1aea` |
| `services/vat/vat_parser_pdf.py` | `d3d2c71b1a890b497a0f86dcbc8aa8f3593538c5cc2c5274349c5fd94d19d60b` |
| `services/vat/vat_parser_csv.py` | `183aa63e7a2b4fe545136608f6b2731e6a90755a295efefffee7324655719f5c` |
| `services/vat/vat_report_parser.py` | `8ecf5e796268ce633b99d0a4894068eee126aad237190d7263a4cabf1e2ec296` |
| `services/recon/field_comparator.py`（已有：税号/分支/日期归一化） | `109c0401587154d3f3860166b4f0c3812f568b03e2e452a880af866195eec037` |

## 落点与保持的判定

实现：`reconciliation/vat_parse.py`。

- 入口 `decode(source)`，`source["kind"]` 取 `excel`（活动表单元格行）/ `pdf`（每页文字 + 表格 + 文字行）/ `text`（文字行）/ `csv`（已解码文本 + 分隔符）/ `pipeline`（管线条目 + 引擎名）；返回部署版结果形状（`ok`/`rows`/`meta`/`warnings`/`parser_version`/`row_count`/`method`）。
- **`None` 是契约的一部分**：`pdf` 与 `csv` 两条路径沿用部署版的语义——文字层过薄且 0 行 = 扫描件、列名认不出 = 交给调用方兜底，都返回 `None`（不是抛错）。
- `decoded_rows(rows)` 把报告行转成入库行契约（六键）。报告行里的税号/分支/税额/合计暂不入库（登记为缺口，与第 27 批的诊断缺口同类）。
- 入账入口：`create_from_parsed(..., vat_rows=None, vat_source=None)`，二选一；给了 `vat_source` 就先解码（`None`→`reconciliation.vat_report_unparsed`、0 行→`vat_no_rows`），否则用调用方给的已解码行；两者都没给→`request_invalid`。返回新增 `vat_parsed`（行数、方法、版本）。

原样保留的规则（差分逐条对齐）：

- 金额 `_to_float` 读不出返回 `None`（不是 0）；列名词表覆盖泰/英/中/日，且**参考号列优先于发票号列**；参考号的宽匹配也照旧。
- 表头 = "有日期列 **且** 有单据号或任一金额列"的首行；Excel 扫前 30 行、表格扫前 10 行、CSV 扫前 20 行（且 CSV 先试 MR.ERP 精确列名再试关键词表）。
- 第一列含合计词的行不进数据，改为填 `meta` 的 `total_amount_pre_vat`/`total_vat`。
- PDF：抽表命中就用表；文字层 < 200 字符且 0 行判为扫描件；有文字层但表 0 行 → 文字行 regex（`method=pdf_text_regex`），仍 0 行 → `None`。
- 文字行：必须"日期 + 行末两个 `.2` 金额"；跳过 `รวมทั้งสิ้น`/`รวมแต่ละหน้า`/`grand total` 与"เลขที่ + ใบกำกับ"表头行；行首 1–3 位纯数字当序号跳过；发票号按 `字母+数字` 切开粘连的客户名尾巴；参考号必须含数字且不是 13 位税号；税号 13 位、分支 `0xxxx`（有税号但无分支时补 `00000`）；买方名去掉已识别 token、`0xxxx`、行首 `XX n` 残留与行尾数字后合并空格；日期做佛历回退；`report_amount = 净额 + 税额`。
- CSV：金额走 `Decimal` + `ROUND_HALF_UP`；没有合计列时用"净额 + 税"补；没有单据号的行丢弃并计入 `warnings`；买方三键总是在（缺列补空）。
- 管线：发票号必须含 ≥2 位字母数字，否则丢弃（`enterprise-` 引擎除外）；`seq_no` 是数字时当 `row_no`。
- 每条通道的出口统一过一遍 `filter_garbage_rows`（部署版 `_parse_vat_report_impl` 的行为），剔除页眉/分隔线/表头行。

## 有意差异

1. **入参边界**：调用方交出已抽取的单元格/文字行/表格/CSV 文本/管线条目；打开文件、编码嗅探、OCR 与模型调用留在调用方。部署版按扩展名分发并自带 Gemini/管线回退，新入口按 `kind` 分发。
2. **扫描件与认不出的结构返回 `None`**，由入账入口翻成 `reconciliation.vat_report_unparsed`；部署版在此时改走 Gemini 或统一管线。
3. **入库只保留六键**：报告行的税号、分支、VAT 税额、含税额与 `row_no` 不落库（存储契约仍只有对账需要的字段），因此批次 27 的 `parsed` 与批次 28 的 `vat_parsed` 只回报行数/方法/版本，不回灌明细。
4. `MIN_DETERMINISTIC_ROWS`（≥3 行才信确定性通道）**未用于新侧**：那是"要不要改走模型"的路由判据，落库不需要；常量仍按部署版迁入并导出，供调用方判断。
5. 源形状不合法（未知 `kind`、缺必填段、页不是映射）抛 `ReconciliationContractError`，与 GL 侧和入库契约一致；部署版对这些情况返回错误 dict 或 `None`。
6. `filter_garbage_rows` 会在 `decode()` 出口统一应用一次（与部署版一致），调用方若自行解码也可单独调用。

## 验证

```sh
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest tests.test_cowork_vat_parse
PYTHONPATH=apps/pearnly_cowork python3.14 tests/test_cowork_vat_parse_parity.py \
    --source /tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b
python3 scripts/check-architecture.py
python3 scripts/i18n-build.py --check
git diff --check

# 隔离 lab（Docker context colima-pearnly，project pearnly-cowork-backend-lab）
../env/bin/python /workspace/scripts/cowork-income-recon-lab-verify.py   # 两个 Site 各 15 个
```

- 离线契约用例 **34** 个通过（本批新增）：金额未知语义、四语列名与参考号优先、行字段与佛历日期、垃圾行过滤、Excel 表头扫描/合计行/无表头、文字行的粘连切分与参考号规则与行号、PDF 抽表/文字行回退/扫描件判 None、CSV 关键词与 MR.ERP 列名/缺合计补算/无单据号告警/认不出返回 None、管线条目过滤与企业引擎例外、分发与拒绝、入库契约键集。
- 固定源差分：**257** 批输入 × 五条通道逐字段比对（Excel 519 行、抽表 276 行、文字行 339 行、CSV 463 行、管线 410 行），另加 33 组叶子 helper 用例，**差异 0**；分支覆盖含文字行回退 1 例、扫描件判 None 162 例、MR.ERP 列名 48 例、无单据号丢弃 46 例，覆盖率下限不达即失败。读取用内存替身顶替（`openpyxl`/`pdfplumber`/OCR 管线两模块），CSV 路径用同一段文本喂两侧。
- 隔离 Site：两个 Site 各 **15** 个用例通过（第 27 批 13 个 + 本批 2 个：已抽取销项税表入账后逐项汇总与解码行入口相同、扫描件/列名认不出/空报告/来源不合法在入库前被拒且无残留），五个端点匿名全 **403**。
- 架构 **0** 违规；`i18n-build --check` PASS（新错误键是 ASCII 语义键）；`git diff --check` 干净。
- 未执行：真实文件读取、Gemini/管线 OCR、计费与引擎档、异步任务与轮询、复核工作簿、批删、前端、部署、外部结果与用户验收。

## 来源核对与运行投影

- 本批未单独采开工快照（并入 main 后即开工），交付 `baseline-twenty-eighth-delivery.json`（`46d01fea…`）与第 27 批交付快照 `db984f61…` `compare` **PASS**，`source` 仍 `ec2e5c957e6b…`。
- 运行投影 `push-runtime-twenty-eighth-delivery.json`（`56fb569e…`）：端点 `configuration_digest` `1ed03962…` → `2227156b…`（端点数 60、平台开关不变）。该摘要每轮都在动，是在跑的旧侧代理写旧库；完整配置与旧状态迁入继续 **HOLD**。

## 合并与清理

- 本批开工时把 main（`21581e4`：ERP「LINE 渠道接销售单据 / 打印页事实与报价转换 / 确认后指向销售单据」三个提交）并入本分支，合并提交 `d8f7e3f`。`scripts/i18n/messages.tsv` 出现一次冲突：两边都在 ERP 行块尾部追加（main +5 条、本分支 498 条自有行），按"两边都保留、ERP 行块保持连续"解决，再跑 `i18n-build.py` 重生成语言包（`pearnly_erp_portal` 17 → 22 条），`--check` PASS；`apps/pearnly_control/pearnly_control/features.py` 自动合并。
- 未接前端、未部署、未合并回 main、未推送；除只读的来源采集外没有外部调用，没有写任何旧库。

## 未做与后续

- 收入族只剩：把两端的文件诊断与文件名入库 → 异步任务与轮询 → 复核工作簿导出（`gl_vat_excel`）→ 批删。银行侧剩余项（异步状态与轮询、批删、手动覆盖痕迹/运行警告落库）仍挂着；第一代 `vat_recon_*`（报告入库 + 发票存档配对）不在本族范围。
