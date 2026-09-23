# Cowork 后端迁移：对账匹配核心与发票候选评分

日期：2026-09-21。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，本批基线 `f461b82`（第十九批交付）。

本工作包只迁入无框架、无 IO 的纯计算：流水对 GL 的三层匹配与汇总，以及银行流水对发票候选的逐字段评分与排名。没有新增 RPC、DocType、数据库迁移、队列、前端、语言包或部署。

## 来源清点：为什么是这两块，`bank_recon_match` 为什么没迁

上一批把银行流水行与余额链迁完后，余下部分按依赖分两类：

| 来源模块 | 性质 | 本批处置 |
| --- | --- | --- |
| `services/recon/bank_recon_reconcile.py` | 文件自述且实际为纯函数：三层匹配 + 汇总 | **迁入** |
| `services/recon/bank_recon_scoring.py` | 逐字段评分与 `match_one_tx` 纯计算；同文件的 `run_matching_for_session` 直接读写旧库 | **只迁纯计算部分** |
| `services/recon/bank_recon_match.py` | 发票候选检索与结果落库的 SQL DAL（`db.get_cursor_rls`、`ocr_history` 查询、`save_match_result`） | **不迁**，属持久化层 |
| `services/recon/bank_recon_pipeline.py`、`bank_stmt_xlsx.py`、`bank_stmt_extract.py`、`bank_stmt_reread.py` | 解析与编排，含文件与模型 IO | 未迁 |

所以本批给的是“打分与配对”这层纯逻辑，取候选与写结果仍留在旧项目；这样将来接新平台时，候选来源和落库方式可以独立设计，不必继承旧 DAL。

## 来源与边界

开工与交付均重新采集线上来源：开工 `baseline-twentieth-start.json`（`5bfedbe9344748d66d8c35a68d1b7e1acfece46fbf9744c34e52b1a66f8cd874`，`2026-09-21T11:30:15Z`），交付 `baseline-twentieth-delivery.json`（`4673e667007971b510d7bbdb5d4f914fee9aa0a7c401b4006be7c326814a3ea3`，`2026-09-21T11:32:33Z`），source 比较 PASS，源码仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。

- 所属 App：`pearnly_cowork`；域：`reconciliation`。依赖只在本域内（`bank_statement` 的容差常量与流水行类型）加标准库，没有新增跨域方向。
- 两个模块都不写 ERP、不写库、不读文件、不调用模型；匹配与评分是给会计看的证据，自动勾选只是建议分档，不等于自动过账。
- `bank_matching` 的三层顺序与“每行只用一次”仍是索引式实现，和部署版一致；`bank_scoring` 的权重合计 100，阈值仍是 85/60。

固定来源只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`；差分脚本先校验 SHA-256，再只执行选定的纯函数与数据类 AST。

| 文件 | SHA-256 |
| --- | --- |
| `services/recon/bank_recon_reconcile.py` | `8b1da4e4c5879bdcd79ef8225787b3b05bb3ec758535696f1b3c1c1c2349f153` |
| `services/recon/bank_recon_scoring.py` | `9738f080c874b59c7d3739db4fa9048b8742295a605f9875b38d23a36424133b` |

| 固定来源 | 新落点 | 保持的规则 |
| --- | --- | --- |
| `bank_recon_types.py` 的 `GlRow`/`BankReconRow`/`BankReconSummary` | `bank_matching.GlRow`/`BankReconRow`/`BankReconSummary` | 字段名、顺序与默认值全部保持，差分按声明顺序逐字段比对 |
| `bank_recon_utils.py:_day_diff`、`_amount_matches`、`DATE_TOL_DAYS` | `bank_matching.day_diff`、`amount_matches`、`DATE_TOL_DAYS` | 绝对天数差；缺日期即不参与匹配；金额容差沿用余额链登记的 `AMOUNT_TOL`（单一来源） |
| `bank_recon_reconcile.py:reconcile` | `bank_matching.reconcile` | 三层：L1 同日、L2 容差内取最近未用 GL 行、L3 只看金额；付款对 GL 贷方、收款对 GL 借方；每行只用一次；余额为 0 的 GL 行不匹配；剩余行按借/贷记 `gl_debit_only`/`gl_credit_only`，流水侧记 `stmt_withdrawal_only`/`stmt_deposit_only`；匹配行排前、其余保持追加顺序；汇总公式与计数一致 |
| `bank_recon_scoring.py` 权重/阈值/容差 | `bank_scoring` 同名常量 | 金额 50、日期 30、方向 15、关键词 5；自动 85、疑似 60；金额分档 0.01 / 1.00 / 10.00 |
| `score_amount`、`score_date`、`score_direction`、`score_keyword` | 同名函数 | 任一侧缺失即 0 分；日期按 ISO 解析失败即 0 分；方向按关键词表分类，未分类的付出钱保留 0.7 倍分；关键词按 3 字以上拉丁/泰文/汉字段计 3 分、票号命中加 2 分、上限 5 分 |
| `match_one_tx` | 同名函数 | 金额 0 分的候选直接丢弃；按总分降序（稳定排序）；最多 5 条；理由文案与 `breakdown` 四字段保持一致 |

两处“文档与实现不一致”按部署行为保留并记录：`bank_recon_reconcile` 的 docstring 写 “±3-day”，实际常量 `DATE_TOL_DAYS` 是 7；`bank_recon_scoring` 的日期档位同样是 7 天。差分脚本核对常量相等，不按注释改行为。

## 尚未迁移的部分

候选检索与结果落库（`bank_recon_match` 的 SQL DAL）、`run_matching_for_session` 的会话编排、流水/GL 的 XLSX/PDF/文本/模型抽取解析、`bank_stmt_reread` 换眼重读、模型结果缓存、两代任务存储与轮询、导出与模板、人工复核动作与路由。工单侧对银行结果的引用同样未迁。本批没有建立第二套账簿、库存账或报税口径。

## 验证

```sh
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest discover -s tests -p 'test_cowork_reconciliation*.py' -v
PYTHONPATH=apps/pearnly_cowork python3.14 tests/test_cowork_reconciliation_source_parity.py \
    --source /tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b
python3 scripts/check-architecture.py
git diff --check
```

- 对账用例 **102** 个通过（本批新增 **36** 个），覆盖：三层配对与同用一次、方向列映射（付款只对贷方、收款只对借方）、最近 GL 行优先、缺日期落 L3、零额 GL 行不匹配、单侧行的状态与金额、排序稳定性、流水行注记透传；评分四档金额、五档日期、方向四类、关键词上限与泰文/中文命中、候选丢弃与降序稳定、理由与 breakdown、上限 5 条、阈值常量。
- 固定源差分：**1,200** 批随机配对比对（逐字段比对 `reconcile` 的全部 `BankReconRow` 与 `BankReconSummary`），实际产生 **146** 次 L1、**235** 次 L2、**401** 次 L3、**5,731** 条单侧行；**1,200** 批评分比对（含 1,046 条入选候选）与四个评分函数的逐值比对；11 个常量与来源逐一核对相等。上一批的余额链 1,500 批、第十八批的 119,037 组金额契约等差分继续通过。
- 架构检查 **0 违规**，`git diff --check` 通过。

未执行：Site 安装/升级与真实事务、公司与角色权限、真实 XLSX/PDF 解析、模型抽取与缓存、候选检索与落库、会话编排、导出模板、业务 API、队列、浏览器、LINE 真机、部署、外部业务结果、用户验收。本机缺 `openpyxl`，未改动模块的盘点导出用例仍未运行。

## 运行投影：漂移本批没有复现

本批开工与交付两侧都采集成功。两分钟内首尾比较结果为 `settings_unchanged: true, observations_changed: false`，即**本批窗口内没有端点配置摘要差异**；跨批比较（第十九批交付 → 本批开工）仍报 `push_runtime_identity_changed`。这说明该摘要会在几分钟的量级上变化，且不是每次采集都变；本批没有把它归因于任何具体字段，也没有降低采集器校验，**完整配置与旧状态迁入继续 HOLD**，等待能解释变化键的证据再解。
