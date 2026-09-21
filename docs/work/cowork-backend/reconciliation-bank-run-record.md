# Cowork 后端迁移：银行对账的 run 警告与锚点记录（第三十四批）

日期：2026-09-21。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，本批基线 `41c3578`（第三十三批清点）。

本包补上[缺口清点](reconciliation-family-audit.md)认定的**本族最后一处真缺口**：银行 run 的业务警告与锚点快照/覆盖此前没有原生存储，所以工作簿的警告条与"手动录入痕迹"段不显示。现在三样都存进原生任务，工作簿按部署版渲染；**银行对账这一族随之闭合**。

## 落点

| 位置 | 变化 |
| --- | --- |
| `reconciliation/bank_warnings.py`（新） | `services/recon/bank_recon_warnings.py` 的机械搬移：四语 `_BRV2_WARN`（期间不重叠 / GL 行数过少 / 无任何匹配 / 完整性 / 多账户）、`brv2_warn`、`_COMP_LBL`/`_MISSPAGE_LBL`/`completeness_details`、`detect_recon_mismatch`、`apply_anchor_overrides` |
| `reconciliation/bank_recon_contracts.py` | 新增 `run_info(value)` 契约、`ANCHOR_KEYS`、`MAX_WARNINGS`/`MAX_WARNING_TEXT` |
| `reconciliation/bank_recon_service.py` | `anchor_plan`（覆盖生效 + 快照/覆盖记录）、`run_info`（本批按部署规则**自产**不匹配警告 + 调用方的 run 级警告）、`create`/`create_from_parsed` 新增 `warnings`/`anchors`/`lang`；`export` 把三样传给工作簿；`TASK_SUMMARY_FIELDS` 补上四个锚点金额 |
| `Cowork Bank Reconciliation` DocType | 新增 `run_info_json`（Long Text，隐藏） |
| `tests/test_cowork_bank_warnings.py`、`tests/test_cowork_bank_warnings_source_parity.py`、两个 lab 脚本 | 契约用例、固定源差分、Site 与工作簿用例 |

固定来源只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`；差分脚本先校验 SHA-256，再执行固定源码（该模块只依赖标准库）。

| 文件 | SHA-256 |
| --- | --- |
| `services/recon/bank_recon_warnings.py` | `73a84874c21a351c32b7dc8fe5e125a3e3f25a0adc8c714e6f8df9c81888c3bf` |

## 保持的规则（差分逐条对齐）

- **不匹配警告**：两侧都有日期且完全不重叠 → `period_mismatch`（带 `GL:{g};对账单:{s}`，日期按对象比较，印刷文本则按文本比较）；对账单 ≥20 行且 GL 行数 × 5 < 对账单行数 → `gl_too_few`；`matched_count == 0` 且两侧合计 ≥10 → `no_match`。整个检测包在 `try/except` 里，比较抛错（日期类型混用）时返回空——原样保留。
- **完整性片段**：笔数缺口的"疑似缺页"判据是 `printed >= max(got*2, got+20)`，四个数字片段各自千分位格式，`balance_break` 按行数在四语里出不同文案。
- **锚点**：`apply_anchor_overrides` 始终回传 OCR 快照（即使没有覆盖），每个覆盖记 `{"ocr": …, "user": …}` 并替换用于匹配的金额；四个锚点（对账单期初/期末、总账期初/期末）齐全。

## 有意差异

1. **谁产生警告**：部署版在 run/worker 里检测（它握着解析结果）；原生在**入账时**对已存的行与命中数跑同一套规则，语言取调用方的 `lang`（默认 `th`）。调用方仍可通过 `warnings` 追加 run 级警告（多账户、完整性、其它运行提示），追加在检测结果之后，与部署版顺序一致。
2. **锚点来源**：部署版的 OCR 值来自解析器；原生由调用方在 `anchors` 里给出（缺某个键时回落到它同时发来的表头金额）。覆盖可以只写替换金额（`{"gl_closing": 495}`），存储时补上读取值，保持部署版的双边形状。
3. **存储形态**：部署版把 `_brv2_warnings`/`_anchor_ocr`/`_anchor_overrides` 塞进 `summary_json`；原生单独一列 `run_info_json`（键名仍是部署版那三个），导出时读出来传给工作簿。
4. **没有 run 就没有记录**：部署版每次 run 都会写快照；原生只有调用方给出锚点时才有快照段（没有 run 编排的一方不该凭空造 OCR 值）。无警告且无锚点时 `run_info_json` 是 `{}`，工作簿不显示这两段。
5. 顺带修掉一个既有缺口：银行任务的四个锚点金额列此前一直是 0（`TASK_SUMMARY_FIELDS` 没带它们，只有 `summary_json` 里有），本批补上，列与摘要一致（Site 用例固定）。

## 验证

```sh
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest tests.test_cowork_bank_warnings
PYTHONPATH=apps/pearnly_cowork python3.14 tests/test_cowork_bank_warnings_source_parity.py \
    --source /tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b
python3 scripts/check-architecture.py
python3 scripts/i18n-build.py --check ; python3 scripts/check-i18n.py
git diff --check

# 隔离 lab（Docker context colima-pearnly，project pearnly-cowork-backend-lab）
bench --site cowork-backend-ack.localhost migrate            # 两个 Site，新增 run_info_json
bench --site cowork-backend-preparation.localhost migrate
../env/bin/python /workspace/scripts/cowork-bank-recon-lab-verify.py          # 两个 Site 各 14 个
../env/bin/python /workspace/scripts/cowork-bank-recon-export-lab-verify.py   # 两个 Site 各 4 个
../env/bin/python /workspace/scripts/cowork-income-recon-lab-verify.py        # 各 19 个
../env/bin/python /workspace/scripts/cowork-income-recon-export-lab-verify.py # 各 3 个
```

- 离线：新 **18** 个警告/锚点/契约用例通过；对账族 **137** 个与解析/契约 **120** 个继续通过；架构 **0** 违规；`i18n-build --check` PASS；`check-i18n` REPORT-ONLY（四语警告表按[登记豁免](../../scripts/i18n/keep-cjk.tsv)）；`git diff --check` 干净。
- 固定源差分：**66** 组标签/格式化用例、**75** 组完整性片段（含缺页提示与余额链两种分支）、**418** 批不匹配检测（5767 行对账单 / 5744 行总账，期间 93、规模 26、无匹配 190 次命中）、**6480** 组锚点组合（其中 5184 组带覆盖），**差异 0**；覆盖率下限不达即失败。检测结果同时与"按输入重述的三条规则"逐条比对，不只比两侧相等。
- 隔离 Site：两个 Site `migrate` 退出码 0 并新增 `run_info_json`；银行 **14** 个用例（第 24 批 10 + 本批 4：警告/快照/覆盖入库并回报、覆盖真的进了匹配（同内容无覆盖时公式差正好差 5）、调用方追加警告、未知键与坏金额入库前拒绝且无残留），银行工作簿 **4** 个用例（新增：警告条文案来自存储、手动录入警示与痕迹段出现、痕迹行带用户值与 OCR/差额批注、无 run 记录的任务不出现这两段）；收入两侧 19+3 继续通过。
- 顺带处理了一次 lab 环境问题：今天多轮脚本把隔离 Site 的 Redis 队列堆到上限（`QueueOverloaded: Too many queued background jobs (750)`），清掉 lab Redis 的 `rq:*`（仅隔离环境、只含测试产生的延迟写入）后恢复。这是 lab 自身，不是产品行为。
- 未执行：真实文件与模型解析、异步编排、前端、部署、外部结果与用户验收。

## 来源核对与运行投影

- 交付 `baseline-thirty-fourth-delivery.json`（`e81f4908…`，采集 2026-09-21T15:10:33Z）与第三十三批快照 `compare` **PASS**，`source` 仍 `ec2e5c957e6b…`。
- 运行投影 `push-runtime-thirty-fourth-delivery.json`（`5b784b84…`）：端点 `configuration_digest` `6d327c98…` → `c5882f4b…`（端点数 60、平台开关不变）。**完整配置与旧状态迁入继续 HOLD**。

## 未做与后续

- 银行族与收入族的**后端已闭合**（本族清点表里的真缺口为零）；下一批起按族推进：OCR/抽取族的清点、M2 销项税 Excel 族、工单对账族，以及等用户定方向的主数据。
