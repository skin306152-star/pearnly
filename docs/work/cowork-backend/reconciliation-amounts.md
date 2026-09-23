# Cowork 后端迁移：对账数值规则、逐字段对照与第二轮配对

日期：2026-09-21。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，本批基线 `14a40dd4f0722c1c84d8ea7d55ee5e3567f79870`。

本工作包只迁入无框架、无 IO 的纯计算：金额判据、逐字段对照和两轮强配对。没有新增 RPC、DocType、数据库迁移、队列、前端、语言包或部署，不能据此宣称对账中心已可用。持久任务、文件解析、导出、计费和会计复核接口仍未迁移。

## 来源与边界

开工与交付均重新采集线上来源：后端源码 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。开工证据 `baseline-eighteenth-start.json`（`fb28b661c4f7382e98210d7372c5e59465bd3514aff6ce4ebce2249d495c5c5b`，`2026-09-21T11:18:49Z`），交付证据 `baseline-eighteenth-delivery.json`（`cd83c8e3456af885cd22512e113a64ce4f412d2182ecee59500c79c1b88c9da5`，`2026-09-21T11:22:50Z`），source 比较 PASS。

- 所属 App：`pearnly_cowork`；域：`reconciliation`。
- 数据归属：将来的 Cowork 当前 Site 内、已授权公司范围；纯函数不能确立授权或数据库隔离。
- 依赖：只依赖标准库和本域 `normalization`/`amounts`。没有 Frappe、HTTP、数据库、文件、OCR、会计或工单依赖，没有新增跨域方向。
- 不新增产品 Web、LINE 或原生 API 入口；这些函数是将来持久化/解析适配层调用的内部入口。
- 配对只做强键断言（单号、日期+税号+金额），不做模糊判定，也不写 ERP。旧第三轮 fuzzy 在来源中已被永久砍除，本批继续保持孤儿交给用户自己对照。

固定来源只读副本仍为 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。差分脚本在运行前校验文件 SHA-256，只执行选定的纯函数 AST，不导入旧项目启动、数据库、网络或 OCR。

| 文件 | SHA-256 |
| --- | --- |
| `services/recon/reconciliation_matcher.py` | `bc62255e03f1898a4eb6eb83e088d2af08f0cc93ab1d07d9dde2ee359cdeb4e6` |
| `services/recon/field_comparator.py` | `109c0401587154d3f3860166b4f0c3812f568b03e2e452a880af866195eec037` |

## 实际迁入

| 固定来源 | 新落点与内部接口 | 保持的规则 |
| --- | --- | --- |
| `reconciliation_matcher.py:_get_total` | `amounts.invoice_total` / `amounts.report_total` | 侧别字段优先序（发票侧 `amount_pre_vat`→`total_amount`→0；报告侧 `report_amount`→`amount_pre_vat`→0）、去千分位、`round(float,2)`、解析失败静默为 `0.0` |
| `_get_total` 的低两位取整与 `abs(a-b) <= 0.01` | `amounts.within_tolerance`、`amounts.parse_amount` | 二进制浮点判据逐位一致，含非有限值的失败结果 |
| `field_comparator.py:compare_field` 的金额分支 | `amounts.compare_amount` | 前税字段与销项税字段共用同一容差，但**解析失败文案不同**（`金额无法解析` / `税额无法解析`），差异文案为 `f"差 {报告侧-发票侧:+.2f}"` |
| `compare_field` 其余分支 | `fields.compare_field` | 日期无法解析判负、`±1` 天为 `date_diff`、`>20` 天为 `date_period_mismatch`；单号/名称要求归一化后非空才判一致；个人买家税号与分店双空判一致；未识别字段退回归一化字符串严格比较 |
| `compare_all_fields` 与 `_FIELD_MAP` | `fields.compare_all_fields`、`fields.FIELD_MAP` | 七字段固定顺序；`skip_buyer` 只跳过税号与分店；`categories` 去重排序；`has_diff` 任一字段不符 |
| `run_matching` 第二轮 | `matching.pair_by_date_tax_amount` | 日期、买方税号、金额三者全等（金额按上表）；任一缺失或无法解析即不配对；同一报告行只消费一次；已由第一轮配走的行不再出现 |
| `run_matching` 组合与统计 | `matching.match_rows`、`matching._pair` | 配对顺序=第一轮再第二轮；`pair_confidence` 1.0/0.95；孤儿按行自身标识（发票 `id`、报告 `row_no`）；`stats` 八个键含 `pass3_count: 0` |

### 重复候选的选择顺序

第二轮在原实现中遍历的是 `set(range(n))` 去掉第一轮已配对索引后的 CPython 集合。对这类由连续小整数构成、只做删除的集合，迭代顺序是升序；新实现按下标升序访问候选项，因此同键多行时选中同一行。该等价性由回归用例覆盖（0/1/2/7/8/9/31/32/33/100/129/400/2000 等规模），并由 2,000 批含重复键的完整配对差分验证。

## 登记的有意差异：金额仍使用二进制浮点

平台规则要求金额用 `Decimal`。本模块**不适用**，理由是旧线上配对结果由二进制浮点决定，换成 Decimal 会改变配对：

- `round(float("2.675"), 2) == 2.67`，而 `Decimal("2.675").quantize(.01, ROUND_HALF_EVEN) == 2.68`。
- `round(float("1.005"), 2) == 1.00`，同样是 Decimal 会进位的情形。

因此 `amounts` 被登记为**导入历史发票/报告金额的兼容契约**：它逐位复现已部署判据，使历史对账结果不变；**新原生记录的金额仍使用 `Decimal`**。要改成十进制口径属于独立的行为变更，需单独 ADR 与会计确认，不能混在本次迁移里。

同时记录一个来源本身的既有事实：`abs(a-b) <= 0.01` 在浮点下**不是均匀的一分容差**，而是随数量级变化。固定源实测：

| 发票侧 | 报告侧 | 源取整结果 | 源判据 |
| --- | --- | --- | --- |
| `0.00` | `0.01` | `0.0 / 0.01` | 匹配（差值恰为同一浮点字面量） |
| `1000.00` | `1000.01` | `1000.0 / 1000.01` | 匹配（差值 `0.009999999999990905`） |
| `100.00` | `100.01` | `100.0 / 100.01` | **不匹配**（差值 `0.010000000000005116`） |
| `1.00` | `1.01` | `1.0 / 1.01` | **不匹配**（差值 `0.010000000000000009`） |
| `2.675` | `2.67` | `2.67 / 2.67` | 匹配（取整后相等） |

迁移不修正这个行为，只如实保留；将来若改为十进制容差，必须作为独立行为变更并附会计确认。

## 尚未迁移的部分

纯计算之外的对账能力都还没有落点，继续按原状留在旧项目：VAT/GL/银行任务持久化与轮询、上传文件解析（XLSX、银行流水、GL/VAT 文件）、余额链校验、人工复核动作与结果页、导出与模板、单条/批量删除、OCR 与计费依赖、公司身份与角色授权，以及 GL/VAT 汇总和银行金额算法。本批没有建立第二套库存账、账簿或报税口径。

## 验证

```sh
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest discover -s tests -p 'test_cowork_reconciliation*.py' -v
PYTHONPATH=apps/pearnly_cowork python3.14 tests/test_cowork_reconciliation_source_parity.py \
    --source /tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b
python3 scripts/check-architecture.py
git diff --check
```

- 对账用例 **31** 个通过（新增 **21** 个：金额契约 5、逐字段 6、第二轮 6、组合 3、候选顺序 1；原有 10 个继续通过）。覆盖浮点取整、字段优先序与静默 0、非有限值、两套解析失败文案、日期三条分支、空标识不配对、个人买家跳过、第二轮三键必需与 0.01 边界、重复候选消费顺序、第一轮不重复派发、组合输出与统计、输入不被修改。
- 固定源差分：金额契约 **119,037** 组、`compare_field` **6,000** 组、`compare_all_fields` **3,000** 组、完整配对 **2,000** 批（其中第二轮实际配对 **232** 对），全部与哈希固定的旧函数一致；另有既有的 4,020 组数量契约与 1,000 批第一轮差分继续通过。8 组 Decimal 调查保留原结果，并新增断言：已安装规则与源判据在这 8 组上一致。
- 架构检查 **0 违规**，`git diff --check` 通过。文件缩进在 `reconciliation` 包内与相邻纯模块保持一致（新模块用空格，`matching.py` 沿用制表符），不改变任何被引用行为。

未执行：Site 安装/升级、真实事务与并发、公司与角色权限、真实文件解析、导出模板、业务 API、队列、浏览器、LINE 真机、部署、外部业务结果、用户验收。`tests/test_cowork_stocktake*.py` 未在本批运行：本机 `python3.14` 缺 `openpyxl`，两个导出用例无法导入，与本批未改动模块无关，也未为此安装依赖。

线上运行投影复核：`push-runtime-eighteenth-start.json` 采集成功（`e0b475a5fc391e4295cdae6e8ca3821d42311b2ed8057f8e9aa3d559fe24fd61`）；交付侧采集被采集器以 `component_database_identity_changed` 拒绝，未生成证据，**没有降低采集器校验**。本批不触碰推送/小助手运行时，完整配置与旧状态迁入继续处于 HOLD。
