# Cowork 后端迁移：收入对账的原生任务与行存储（第二十六批）

日期：2026-09-21。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，本批基线 `debf942`（并入 main `42ed146` 之后的合并提交；上一批为 `44fdd9a`）。

本包把第二十五批迁入的纯判定核心接到新平台的原生存储：新增一对 DocType（任务 + 结果行）、一个存储服务、一组专用 RPC 与权限门，并把银行那套的共享契约、权限门、控制器门与任务机制抽出来复用。没有前端、没有部署、没有推送，也没有真实文件、模型或外部调用。

## 为什么不是照搬旧表

旧仓收入对账有两代表，本批只承接其中真正在服务的那一代：

| 来源 | 性质 | 本批处置 |
| --- | --- | --- |
| `services/recon/vat_recon_schema.py`、`vat_recon_store.py`、`vat_recon_tasks_store.py`（`vat_report` / `reconciliation_task` / `reconciliation_row`） | 更早的一代：报告入库 +「已归档发票 ↔ 报告行」配对（`pair_confidence`/`diff_fields`/`accountant_action`），按期间唯一 | **未迁**：那是发票存档族的对账，不是总账对销项税 |
| `services/recon/gl_vat_store.py`（单表 `gl_vat_task`：元数据 + `detail_json` + `summary_json`） | 部署版在用的这一代 | **语义迁入，存储结构有意不照搬** |

原生落点是 `Cowork Income Reconciliation`（任务）+ `Cowork Income Reconciliation Row`（结果行），与银行那套同型：任务表放汇总与状态，行表放每条销项税行的配对结果，可查询、可分页、可删。旧单表把明细塞进一段 JSONB，跨任务统计只能整段读出来；拆成行表后与银行的查询、分页、删除机制共用同一份代码。

## 字段映射

任务表（← `gl_vat_task`）：

| 原生字段 | 来源 | 说明 |
| --- | --- | --- |
| `record_id` | `id`（BIGSERIAL） | 改为由 `站点 \| 用户 \| request_id` 派生的 `CIR-<40hex>` 稳定名，供幂等重放 |
| `workspace` | `workspace_client_id` | 旧"套账归属"列的现代替身 |
| `title` | 无（旧表以两个文件名代替标题） | 调用方给的任务标题 |
| `status` | `status` | 本批只有同步成功路径，固定 `completed` |
| `gl_account_code` | 无 | 可选收入科目码，仅存档（与银行同型字段） |
| `source_format` | 无 | `decoded_vat_rows_v1` |
| `source_digest` / `request_digest` | `detail_json` + `summary_json`（的内容） | 请求指纹；同 `request_id` 换内容即冲突 |
| `request_key` | 无 | 幂等键 |
| `row_count` | `vat_row_count` | 结果行 = 销项税行（核心以报告为主表，一行一条） |
| `gl_row_count` | `gl_row_count` | 入库的总账行数 |
| `matched_count` / `diff_count` / `unmatched_count` | 同名三列 | 部署版在路由里按明细算：`gl_amount is None` → 未配对；否则 `diff == 0` → 命中、非零 → 差异 |
| `amount_tolerance` | 无（旧版固定 0.01） | 可选表头，默认 0.01，上限 1000 |
| `gl_total`、`gl_only_credit`、`gl_only_debit`、`vat_only_positive`、`vat_only_negative`、`vat_total` | `summary_json` 的六个标量 | 从汇总数据类拆成列，列表可直接展示 |
| `summary_json` | `summary_json` | 完整汇总，含四个调整桶的逐条明细 |
| `created_by` / `created_at` | `user_id` / `created_at` | |

未存：`gl_filename` / `vat_filename`（文件读取尚未迁，随解析批次补）、`error`（本批没有异步失败路径）。

结果行（← `detail_json[i]`，即 `ReconRow`）：

| 原生字段 | 来源 |
| --- | --- |
| `record_id` | `<任务名>-<序号>` |
| `workspace` / `task` / `row_index` | 归属与明细顺序（部署版按 `(date, doc_no)` 排序后的位置） |
| `doc_no` | `ReconRow.doc_no` |
| `vat_date` | `ReconRow.date`（报告上的印刷文本，故意不转日期） |
| `customer_name` | `ReconRow.customer_name` |
| `vat_amount` | `ReconRow.vat_amount` |
| `has_gl_match` | `ReconRow.gl_amount is not None`（旧 JSON 直接存 `null`） |
| `gl_amount` / `diff` | `ReconRow.gl_amount` / `ReconRow.diff`（无对手方时存 0，靠 Check 区分） |
| `account_codes` | `ReconRow.account_codes` |

## 有意差异

1. 存储结构：单表 JSONB → 原生任务 + 行两表（与银行同型），明细可按 `row_index` 分页，删除走服务而不是 `DELETE` 路由。
2. 行契约边界：入参金额先用 `Decimal` 精确校验（6 位小数、24 位有效数字、负号只允许出现在销项税侧），再存部署版二进制 float（与银行第 21 批同口径）。旧路由直接把解析器的 float 交给核心，没有这一层；这是本平台对"入库前先收口"的一贯做法，不改变判定结果。
3. `has_gl_match` 取代 JSON 的 `null`：不用 Float 列的 `NULL` 表示"没有对手方"，因为 `0` 是合法金额。
4. 日期字段名为 `vat_date`，值仍是印刷文本：部署版就是按文本排序，转成 `Date` 会改变排序与展示。
5. 幂等：旧 `run` 每次落一行新任务；原生按 `request_id` 派生稳定任务名，同内容重放返回原结果，同 id 换内容报冲突。
6. 空报告金额按部署版的 `or 0` 兜底存 0（不是"未知"），与核心读取口径一致。
7. 未提供旧 `POST /gl-vat/tasks/batch_delete` 与 `GET /gl-vat/{task_id}/export`：导出随导出批次，批删未迁，删除按任务逐个走专用 RPC。

## 落点

共享部分（本批从银行那套抽出，银行侧改为薄别名，行为不变）：

| 模块 | 作用 |
| --- | --- |
| `reconciliation/recon_contracts.py` | 金额/文本/日期/未知字段、规范化 JSON、请求指纹 |
| `reconciliation/recon_access.py` | 任务/行两表的权限规则（`ReconAccess` 工厂） |
| `reconciliation/recon_documents.py` | 服务专属写门（`WRITE_TOKEN` + `persist`）与不可变身份检查 |
| `reconciliation/recon_tasks.py` | 命名、精确重放、列表、明细、删除 |

收入部分：

| 模块 | 作用 |
| --- | --- |
| `reconciliation/income_recon_contracts.py` | 两侧行契约、表头、指纹、转匹配形状 |
| `reconciliation/income_recon_access.py`、`income_recon_documents.py` | 绑定两个新 DocType 的权限与写门 |
| `reconciliation/income_recon_service.py` | `create` / `records` / `detail` / `delete` |
| `reconciliation/income_recon_api.py` | 专用 RPC（POST `create`、GET `records`/`detail`、POST `delete`） |
| `pearnly_cowork/pearnly_cowork/doctype/cowork_income_reconciliation{, _row}/` | 两个 DocType（含控制器） |
| `hooks.py` | 两个新 DocType 的 `permission_query_conditions`、`has_permission`、`DocShare` 拒绝 |
| `tests/test_cowork_income_recon_contracts.py`、`scripts/cowork-income-recon-lab-verify.py` | 离线契约用例与隔离 Site 用例 |

权限沿用同一个 `Cowork Bank Reconciliation` 角色：部署版的对账权限不区分对账类型，`features.py` / `pearnly_core/**` / `scripts/i18n/messages.tsv` 这三处共享底座本批一律未动。

## 验证

```sh
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest discover -s tests -p 'test_cowork_reconciliation*.py'
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest tests.test_cowork_bank_recon_contracts tests.test_cowork_income_recon_contracts
python3 scripts/check-architecture.py
python3 scripts/i18n-build.py --check
git diff --check

# 隔离 lab（Docker context colima-pearnly，project pearnly-cowork-backend-lab）
bench --site cowork-backend-ack.localhost migrate            # 首次安装 + 重复一次
bench --site cowork-backend-preparation.localhost migrate
../env/bin/python /workspace/scripts/cowork-income-recon-lab-verify.py   # 两个 Site 各 10 个
../env/bin/python /workspace/scripts/cowork-bank-recon-lab-verify.py     # 银行回归 10 个
../env/bin/python /workspace/scripts/cowork-bank-recon-export-lab-verify.py  # 3 个
```

- 离线条约：对账族 **137** 个通过（本批未改判定核心），银行行契约 **15** + 收入行契约 **17** 全通过；新用例覆盖单号归一化派生、负数贷项单、空金额按 0、精度与超长拒绝、表头容差上下限、批量上限、指纹变化、转匹配形状。
- 隔离 Site：两个 Site `migrate` 退出码 0（ack 重复一次仍 0），新收入用例各 **10** 个通过（含幂等重放、同 id 换内容拒绝、容差档位、范围与功能授权、撤销授权、通用写入/分享被拒、身份不可改写、分页、删除），四个端点匿名 HTTP 全部 **403**；银行 10 个与工作簿 3 个回归通过，未因抽取共享模块回归。
- 架构 **0** 违规；`i18n-build --check` PASS（本批无界面文案）；`git diff --check` 干净。
- 未跑：真实文件解析、工作簿导出、批删、异步任务与轮询、前端、部署、真机验收。Site 验证是本地隔离库的合成数据，不是业务验收。

## 来源核对与运行投影

- 线上来源：本批交付前重新采集（`baseline-twenty-sixth-delivery.json`），`source=ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，与上一批交付快照 `compare` **PASS**（固定源码 SHA 未变）。
- 运行投影（`push-runtime-twenty-sixth-delivery.json`）：端点 `configuration_digest` 由 `fc713ee1` 变为 `307628d8`，30 秒后再采集仍为 `307628d8`、之后又变 `bbb0fe7e` —— 该表由在跑的旧侧代理持续更新（本批不写旧库），与迁移无关；端点数量仍 60、平台开关未变。运行投影导入继续 `HOLD`。

## 未做与后续

- 收入族的后续批次：总账/销项税文件的解析（列识别与余额链）、把 `gl_filename`/`vat_filename` 与文件诊断入库、异步任务与轮询、复核工作簿导出（`gl_vat_excel`，与银行那批同型）、批删。
- 未接前端、未部署、未合并进 main、未推送；除来源采集外没有任何外部写入，写入旧 ERP 的动作本批不存在。
