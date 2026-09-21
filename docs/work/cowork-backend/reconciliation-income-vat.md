# Cowork 后端迁移：收入对账（总账对销项税）纯核心（第二十五批）

日期：2026-09-21。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，本批基线 `abcd1d6`（并入 main `2294a10` 之后的合并提交）。

本包只迁对账中心"收入"类型的**纯判定核心**：总账行与销项税报告的配对、容差、贷项单方向、以及汇总与调整项明细。没有新增 DocType、RPC、界面、存储或部署；读文件/落库/出工作簿仍是后续批次。

## 为什么先迁这一段

对账中心三种类型里，银行那代已经落地（行契约 → 匹配 → 原生任务存储 → 受控解析入账 → 复核工作簿）。收入这代对应 `gl_vat_*` 家族，旧的入口是 `/recon/gl-vat/*`（run / tasks / task / export / delete）。这一族按依赖同样分三层：

| 来源模块 | 性质 | 本批处置 |
| --- | --- | --- |
| `services/recon/gl_vat_types.py` | 三个数据类（`GlRow`/`ReconRow`/`GlVatSummary`），含 `__post_init__` 把四个调整项列表补成 `[]` | **迁入** |
| `services/recon/gl_vat_reconcile.py` | 纯判定：`_vat_doc_no` + `reconcile_gl_vat`（客户 2026-05-15 确认的规则） | **迁入**（`_vat_doc_no` 公开为 `vat_doc_no`） |
| `services/recon/gl_vat_parse_common.normalize_doc_no` | 单据号归一化，只有一行：委托给发票号归一化器 | **迁入**（委托给已迁的 `normalization.normalize_invoice_no`） |
| `services/recon/gl_vat_parse_excel.py`、`gl_vat_parse_pdf.py`、`gl_vat_parse_common` 其余（列名启发式、余额链） | 文件读取与列识别 | 未迁，随解析批次 |
| `vat_recon_schema.py`、`vat_recon_store.py`、`vat_recon_tasks_store.py`、`gl_vat_store.py` | DDL/RLS 与两代表存储 | 未迁，随存储批次 |
| `gl_vat_excel.py`、`gl_vat_serialize.py`、`gl_vat_reconciler.py` | 工作簿导出、序列化、编排（含 Excel 读取） | 未迁；工作簿随导出批次（与银行那批同型） |

固定来源只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`；差分脚本先校验 SHA-256，再只执行选定的纯函数与数据类。

| 文件 | SHA-256 |
| --- | --- |
| `services/recon/gl_vat_types.py` | `ee24b7dd0a7a24e67ac338f7c7deae73c71c36aca5b731ddc18b6e6af27a300d` |
| `services/recon/gl_vat_reconcile.py` | `05a5751d27c730819c53a94f13ccab5606677571f41839a056154d50647c13b8` |
| `services/recon/gl_vat_parse_common.py` | `ca59a5d1b313806e4358ad5a29b8abfa8b906b1dcc1d2f26128aee932eac5403` |
| `services/recon/field_comparator.py`（已有，归一化链） | `109c0401587154d3f3860166b4f0c3812f568b03e2e452a880af866195eec037` |

## 落点与保持的规则

实现：`reconciliation/income_vat.py`（`GlRow`、`ReconRow`、`GlVatSummary`、`normalize_doc_no`、`vat_doc_no`、`reconcile_gl_vat`，容差常量 `AMOUNT_TOLERANCE = 0.01`）。字段名、顺序与默认值全部保持（含 `GlVatSummary` 的 `None` 默认 + `__post_init__` 补空列表）。

保持的判定口径：

- 配对键：VAT 行的参考单号（`report_ref_no` → `report_reference_no` → `report_invoice_no`，取第一个非空，`str().strip()`）与 GL 行的 `norm_doc_no` 按视觉归一化后相等；一个 GL 单号可对应多行，贷方/借方分别求和。
- 方向：VAT 金额为正 → 与 GL **贷方**合计比；为负（贷项单）→ 与 GL **借方**合计的相反数比。
- 容差：`|diff| <= amount_tolerance`（默认 0.01）时差额显式记 `0.0`，否则记真实差额。
- 明细（`ReconRow`）：以 VAT 为主表，逐行给出单号/日期/客户名/税额/GL 金额/差额/去重排序后逗号拼接的收入科目；无 GL 对手方时 `gl_amount` 与 `diff` 为 `None`、科目为空串；最后按 `(date, doc_no)` 排序（空值当空串）。
- 汇总（`GlVatSummary`）：`gl_total = Σ贷方 − Σ借方`、`vat_total = Σ税前金额`（部署版这个合计**不做**异常兜底）；四个调整桶（不在 VAT 的 GL 贷方、不在 VAT 的 GL 借方、不在 GL 的 VAT 正数、不在 GL 的 VAT 负数）各自带 `{doc_no, date, name, amount}` 明细行。
- 细节保留：明细里 VAT 金额不可读时按 `0.0` 处理（`try/except`），但汇总的 `vat_total` 是裸 `float()`，遇到非空不可读值会整体抛错；`gl_only` 用「VAT 归一化单号集合」判定、`vat_only` 用「GL 索引」判定，两者口径不同（原样保留）。

## 验证

```sh
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest discover -s tests -p 'test_cowork_reconciliation*.py'
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest tests.test_cowork_bank_recon_contracts
PYTHONPATH=apps/pearnly_cowork python3.14 tests/test_cowork_reconciliation_income_vat_parity.py \
    --source /tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b
python3 scripts/check-architecture.py
git diff --check
```

- 对账用例 **137** 个通过（本批新增 **14** 个收入用例：单号优先级与归一化、精确配对与科目排序、贷项单方向、容差档、无对手方行、排序、空值金额、汇总四桶与明细形状、空输入、合计异常边界、输入不被改写）。
- 固定源差分：**1205** 批输入（含 5 组固定形状：精确配对、贷项单、容差内差额、两侧单边、空输入）逐字段比对明细与汇总（含四个调整项列表），**1105** 行明细、253 行命中、29 行贷项单、28 行容差归零、四桶分别 424/399/432/103 行；两侧异常身份逐例一致（759 批因部署版裸 `float()` 抛错，迁移版抛同样异常）；单号归一化 15 例一致；差异 0。覆盖率下限不达即失败。
- 架构 **0** 违规；`i18n-build --check` PASS；`git diff --check` 干净（本批无界面文案，无 i18n 登记变更）。
- 未跑隔离 Site：本批只加纯逻辑，没有 DocType/权限/RPC/存储变化，Site 层没有新增行为可验（既有 Site 用例在上一批跑过）。
- 运行投影：开工 `baseline-twenty-fifth-start.json`（`d52f3576…`，source 仍 `ec2e5c957e6b…`）；交付证据见[本批清单](evidence/twenty-fifth-local-validation.json)。

## 未做与后续

- 收入这一族的后续批次：GL/VAT 的解析（Excel/PDF 列识别与余额链）、原生任务与行存储（`vat_recon_*` / `gl_vat_store` 两代里选定的那一代）、任务 API 与轮询、复核工作簿导出（`gl_vat_excel`，与银行那批同型）、以及把这些接到 `reconciliation_type="income"`。
- 未接前端、未部署、未合并进 main、未推送；所有输入都是任务内合成数据，没有真实文件、模型调用或外部结果。
