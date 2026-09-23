# Cowork 后端迁移：银行流水行契约与余额链

日期：2026-09-21。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，本批基线 `f052738`（第十八批交付）。

本工作包只迁入无框架、无 IO 的纯计算：流水行契约、页脚合计过滤、解析质量比、方向自动纠正、逐行核对、金额反推修复、`finalize_rows` 定案序列和完整性交叉校验。没有新增 RPC、DocType、数据库迁移、队列、前端、语言包或部署，不能据此宣称银行对账功能已可用；文件解析、任务持久化、匹配评分、导出和人工复核接口仍未迁移。

## 为什么先做这一段（来源清点）

银行与 GL/VAT 这一族在固定来源里有近 50 个模块，多数与 XLSX/PDF/文本解析、模型抽取、缓存和任务存储耦合。按“先看对账中心实际消费哪几条路径”的判断，本批先取**被解析路径共同复用的余额链纯函数**：

`services/recon/bank_stmt_balance.py` 是被 `bank_stmt_xlsx`、`bank_stmt_extract`、`bank_stmt_reread`、`bank_recon_pipeline`、`bank_recon_v2` 引用的单一事实源；`bank_recon_v2` 再被银行对账 V2 路由消费。对账在 `docs/p0/04-scope-and-drop-list.md` 里属保留的 Cowork 能力，既不在被砍的报税/工单/Excel 公式内测里，也不依赖会计账簿（`gl_*` 只是导入文件字段名）。因此这一段是保留范围内、可独立验证、且被多条解析路径共享的最小切片。

`bank_recon_utils.py` 不整块迁移：它同时携带模型结果的内存与磁盘缓存；本批只复现其中的 `AMOUNT_TOL`，并由差分脚本核对常量相等。

## 来源与边界

开工与交付均重新采集线上来源。开工证据 `baseline-nineteenth-start.json`（`3e9dfa0fcc1b3b933ff7127db66685019314f192ddf03acdf00f4aae06037022`，`2026-09-21T11:26:34Z`），交付证据 `baseline-nineteenth-delivery.json`（`c40b32d52823ff160ef94d87941763facaab4cc5add600167ef4856f4003316f`，`2026-09-21T11:28:50Z`），source 比较 PASS，源码仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。

- 所属 App：`pearnly_cowork`；域：`reconciliation`。
- 数据归属：将来的 Cowork 当前 Site 内、已授权公司范围；纯函数不能确立授权或数据库隔离。
- 依赖：只依赖标准库与本域既有模块。没有 Frappe、HTTP、数据库、文件、模型、OCR、会计或工单依赖，没有新增跨域方向。
- 不新增产品 Web、LINE 或原生 API 入口；将来由受控解析适配层调用。函数按部署行为**就地修改传入行对象**并返回过滤后的新列表，因为解析器把自己持有的行交给这条链。

固定来源只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`；差分脚本先校验文件 SHA-256，再只执行选定的纯函数/类 AST，不导入旧项目启动、数据库、网络或模型。

| 文件 | SHA-256 |
| --- | --- |
| `services/recon/bank_stmt_balance.py` | `36cb82f137502c65a069dae61cb468e1def6bfeab8169471430fdcdb6ffa7013` |
| `services/recon/bank_recon_types.py` | `df0508cccb9891a5ea628cbce104ee09001b976ade15afd4d599c5d766de149e` |
| `services/recon/bank_table_io.py` | `19b9161eb5a96f8244efba97b215b50710365ba97f1bd93f8ce04ccd302f190e` |
| `services/recon/bank_recon_utils.py` | `35b143f7cfc115fc497397902026c463cdc7a589da70ea690d85f35b4f016745` |

| 固定来源 | 新落点 | 保持的规则 |
| --- | --- | --- |
| `bank_recon_types.py:StatementRow` | `bank_statement.StatementRow` | 字段与默认值、`balance_ok` 三态、两个自动修正标记；指纹仍含余额（`account_no\|date\|withdrawal\|deposit\|balance\|description[:40]` 的 md5 前 12 位，同额同日不同余额仍是两笔） |
| `bank_table_io.py:_is_summary_row` 与其关键词表 | `bank_statement.is_summary_row`、`SUMMARY_ROW_KEYWORDS` | 17 个中英泰页脚合计关键词；空描述不算合计行 |
| `bank_stmt_balance.py:_stmt_bad_ratio` | `bank_statement.statement_row_bad_ratio` | 只比金额量级忽略方向；余额列为 0 且有金额计坏；动行少于 3 行不判；返回坏行占比 |
| `_correct_direction_from_balance` | `bank_statement.correct_direction_from_balance` | 需可靠上一行余额、方向与余额涨跌相反、金额与涨跌幅度差 ≤ `AMOUNT_TOL`；命中才换列并打标记 |
| `_verify_row_balances` | `bank_statement.verify_row_balances` | `round(prev + 存 − 取, 2)` 与印刷余额比较；容差 `min(max(0.02, 金额×0.005), 1.0)`；无余额、无动行、期初/合计行描述、期初不可靠后的首笔动行都记 `None`；无动行仍更新 prev |
| `_repair_amount_from_balance`、`_REPAIR_RATIO` | `bank_statement.repair_amount_from_balance`、`REPAIR_RATIO` | 需上一行可靠、下一行佐证本行余额、反推差 < 30% 才改金额并按涨跌定方向；命中置 `amount_autocorrected` 且 `balance_ok=True`，否则保持 `False` 交人工 |
| `finalize_rows` | `bank_statement.finalize_rows` | 顺序不变式：过滤合计行 → 方向纠正 → 逐行核对 → 金额修复；返回过滤后的新列表 |
| `_audit_completeness` | `bank_statement.audit_completeness` | 期末平衡、印刷合计、印刷笔数三道闸；合计被原样填进余额（<0.5 元或两合计相同）时跳过合计校验但笔数照查；笔数只在 `0 ≤ n < 100000` 时采信 |

接口注意：部署的数据类会把余额格式化进指纹，因此**余额未读到的行必须先在构造后用显式赋值置 `None`**（差分脚本同时验证两种实现都以 `TypeError` 拒绝直接传 `None`，这是来源既有约束，没有放宽）。`AMOUNT_TOL`、`REPAIR_RATIO` 与关键词表都由差分脚本与来源常量逐项比对，不是凭抄写。

## 尚未迁移的部分

银行与 GL/VAT 的其余能力都还没有落点：流水/GL 的 XLSX、PDF、文本与模型抽取解析器，`bank_stmt_reread` 的换眼重读编排，模型结果的内存/磁盘缓存，`GlRow`/`BankReconRow`/`BankReconSummary`，匹配分层与评分，任务存储与轮询，导出与模板，人工复核动作，以及 GL/VAT 汇总与银行会话路由。工单侧 `machine_actions` 对余额链的引用同样未迁。本批没有建立第二套库存账、账簿或报税口径，也没有写 ERP。

## 验证

```sh
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest discover -s tests -p 'test_cowork_reconciliation*.py' -v
PYTHONPATH=apps/pearnly_cowork python3.14 tests/test_cowork_reconciliation_source_parity.py \
    --source /tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b
python3 scripts/check-architecture.py
git diff --check
```

- 对账用例 **66** 个通过（本批新增 **35** 个银行流水用例：行契约与指纹 2、页脚合计 2、质量比 5、方向纠正 6、逐行核对 5、金额修复 4、定案序列 3、完整性审计 7、`__main__` 之外无跳过）。覆盖零余额列、交易号当金额、只是方向反、续页无期初、容差三档（0.02 下限 / 0.5% 中段 / 1 元上限）、超过 30% 不修、未被下一行佐证不修、合计误填与笔数漏行。
- 固定源差分：**1,500** 批余额链随机语料，逐批比对质量比、三个步骤的独立结果、`finalize_rows` 返回集（含行指纹）、`audit_completeness` 全量返回；累计过滤 **3,098** 条页脚行、**218** 次方向纠正、**74** 次金额修复、**567** 条完整性 issue，全部与哈希固定的旧实现一致。同时核对 `AMOUNT_TOL`、`REPAIR_RATIO`、页脚关键词表相等，以及余额 `None` 的构造行为一致。
- 上一批的 119,037 组金额契约、6,000 组 `compare_field`、3,000 组整行对照、2,000 批完整配对继续通过。架构检查 **0 违规**，`git diff --check` 通过。

未执行：Site 安装/升级与真实事务、公司与角色权限、真实 XLSX/PDF 解析、模型抽取与缓存、导出模板、业务 API、队列、浏览器、LINE 真机、部署、外部业务结果、用户验收。本机缺 `openpyxl`，未改动模块的盘点导出用例仍未运行，也未为此安装依赖。

线上运行投影：本批开工与交付两侧都采集成功（`push-runtime-nineteenth-start.json` `0444436525cd…`、`push-runtime-nineteenth-delivery.json` `70f8843274…`），差异仍只有采集时间与 Web/Worker 两处端点 `configuration_digest`，与第十四至十八批的 HOLD 同因；完整配置与旧状态迁入继续暂停，不能由摘要反推变化键或归因心跳。
