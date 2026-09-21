# Cowork 后端迁移：收入对账的文件名与解析诊断入库（第二十九批）

日期：2026-09-21。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，本批基线 `d6abf26`（第 28 批交付）。

本包给收入任务补上"这次是从哪些文件、用哪个读取器读出来的"：任务表新增两个文件名与一段解析诊断，入账入口接受并落库。这正是部署版 `gl_vat_task` 里 `gl_filename`/`vat_filename` 两列的现代替身，也是后面复核工作簿（`gl_vat_excel` 的 File Info 段）需要的数据。

## 落点

| 位置 | 变化 |
| --- | --- |
| `Cowork Income Reconciliation` | 新增 `gl_filename`（Data 140）、`vat_filename`（Data 140）、`parse_info_json`（Long Text，隐藏）；`detail` 的字段表加入两个文件名 |
| `reconciliation/income_recon_contracts.py` | 新增 `parse_info(value)` 契约、`file_name(value, field)`、`PARSE_INFO_SECTIONS`/`MAX_PARSE_FILES`/`MAX_ACCOUNTS`/`MAX_WARNINGS`/`MAX_FILE_NAME` |
| `reconciliation/income_recon_service.py` | `create(...)` 接受 `parse_info`/`gl_filename`/`vat_filename` 并落库、回传 `parse_info`；`create_from_parsed(...)` 接受两个文件名，用 `gl_diagnostic(...)` 与报告侧读数拼出诊断 |
| `reconciliation/income_recon_api.py` | 两个 RPC 同步扩参 |
| `tests/test_cowork_income_recon_parse_info.py`、`scripts/cowork-income-recon-lab-verify.py` | 契约用例与 Site 用例 |

## 保持与新增的语义

- **部署版有的照旧**：任务带有总账与销项税两个文件名；诊断里保留读取器自己的读数（`method`、`rows`、`parser_version`，以及总账侧 `accounts_seen`/`candidate_data_lines`/`tables_seen`/`header_found`/`sheets_seen`/`rows_kept`、报告侧 `warnings`）。
- **契约收口**（部署版是裸 JSONB）：只认 `gl`/`vat` 两段；每段是最多 20 条记录、每条只认部署版出现过的键；`rows` 必须是 0..20000 的整数、计数必须是非负整数、`header_found` 存布尔；`accounts_seen`/`warnings` 是短字符串列表（各 ≤100/≤50 条）；文件名去空格且 ≤140。任何未知段/未知键/类型不符都在入库前拒绝。
- **诊断不参与重放指纹**（与银行那批同口径）：指纹只覆盖表头与两侧行，所以同一 `request_id` 同内容 replay 时返回首次结果，任务里保留**首次**的文件名与诊断——Site 用例专门验证这一点（第二次传 `other.xlsx` 不会改写）。
- `create`（调用方直接给已解码行）也可以带诊断；报告侧没走解析时该段记录为空文件、空方法、0 行。

## 有意差异

1. 部署版的诊断是单表 JSONB 里的一段；这里拆成两个文件名列 + 一段受校验的 JSON，便于 `detail` 直接展示文件名，也便于后续工作簿读取。
2. 部署版每次 `run` 都新写一行任务、文件名与诊断随新行进入；这里按 `request_id` 幂等，所以诊断属于首次入库的那次（已在上面写明并由用例固定）。
3. 诊断段只保留部署版出现过的键，多送的键会被拒绝而不是入库后静默忽略（部署版无校验）。
4. 部署版没有 `parse_info` 这类入参（诊断在服务端拼装），这里允许调用方直接提供，用于"已经自己解码好行"的场景；结构仍受同一契约约束。

## 验证

```sh
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest tests.test_cowork_income_recon_parse_info
python3 scripts/check-architecture.py
python3 scripts/i18n-build.py --check
git diff --check

# 隔离 lab（Docker context colima-pearnly，project pearnly-cowork-backend-lab）
bench --site cowork-backend-ack.localhost migrate            # 两个 Site 各一次，新增三列
bench --site cowork-backend-preparation.localhost migrate
../env/bin/python /workspace/scripts/cowork-income-recon-lab-verify.py   # 两个 Site 各 16 个
../env/bin/python /workspace/scripts/cowork-bank-recon-lab-verify.py     # 银行回归 10 个
../env/bin/python /workspace/scripts/cowork-bank-recon-export-lab-verify.py  # 3 个
```

- 离线：本批新 **7** 个契约用例通过；收入/销项税解析、两份行契约等既有 **102** 个继续通过；架构 **0** 违规；`i18n-build --check` PASS；`git diff --check` 干净。
- 隔离 Site：两个 Site `migrate` 退出码 0，`tabCowork Income Reconciliation` 已出现 `gl_filename`/`vat_filename`（varchar(140)）与 `parse_info_json`（longtext）；两个 Site 各 **16** 个用例通过（上批 15 + 本批 1：文件名去空格入库、诊断两段的键与读数、`create` 自带诊断、replay 不改写首次诊断、未知键拒绝），五个端点匿名**403**；银行 10 个与工作簿 3 个回归通过。
- 未执行：真实文件读取、报告与总账的完整导出、异步任务与轮询、复核工作簿渲染、批删、前端、部署、外部结果与用户验收。

## 来源核对与运行投影

- 交付 `baseline-twenty-ninth-delivery.json`（`272a1fb5…`，采集 2026-09-21T14:33:30Z）与第 28 批交付快照 `compare` **PASS**，`source` 仍 `ec2e5c957e6b…`。
- 运行投影 `push-runtime-twenty-ninth-delivery.json`（`2f8cd82a…`）：端点 `configuration_digest` `2227156b…` → `ff8e39f4…`（端点数 60、平台开关不变）；该摘要持续在动，是在跑的旧侧代理写旧库。**完整配置与旧状态迁入继续 HOLD**。

## 未做与后续

- 收入族还剩：异步任务与轮询、复核工作簿导出（`gl_vat_excel`，File Info 段现在有数据可读）、批删。银行侧剩余项（异步状态与轮询、批删、手动覆盖痕迹/运行警告落库）仍挂着。
- 未接前端、未部署、未合并回 main、未推送；除只读的来源采集外没有外部调用，没有写任何旧库。
