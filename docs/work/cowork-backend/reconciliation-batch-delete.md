# Cowork 后端迁移：对账任务的批量删除（第三十一批）

日期：2026-09-21。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，本批基线 `68ee91f`（第 30 批交付）。

本包补齐两家对账都缺的**批量删除**入口：机制放进共享的 `ReconTasks`，收入与银行各挂一个 POST RPC。部署版两家都有这个入口（`/gl-vat/tasks/batch_delete`、`/bank-v2/tasks/batch_delete`），所以这是同一个共享实现的两次接线，不是两条独立逻辑。

## 来源与部署版语义

| 来源 | 语义 |
| --- | --- |
| `routes/recon_routes_glvat.py: gl_vat_batch_delete` + `gl_vat_store.delete_gl_vat_tasks_batch` | `recon.create` 权限；`ids` 空 → `{"deleted": 0}`；只删 `user_id = 本人` 的行；返回 `rowcount` |
| `routes/recon_routes_bankv2.py: bank_v2_batch_delete` + `bank_recon_v2_store.delete_bank_recon_v2_tasks_batch` | 同上，只是表不同 |
| `bank_recon_v2_store.ensure_bank_recon_v2_table`、`gl_vat_store.ensure_gl_vat_task_table` | **单表**设计：明细在 `detail_json` 里，删任务行就是全部，没有子表要级联 |

固定源相关文件：`services/recon/gl_vat_store.py`、`routes/recon_routes_glvat.py`、`services/recon/bank_recon_v2_store.py`、`routes/recon_routes_bankv2.py`（只读副本 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`）。本批是**行为接线**而非纯函数搬运，所以没有逐格差分；证据是隔离 Site 的行为用例（下表）。

## 落点与判定

| 位置 | 变化 |
| --- | --- |
| `reconciliation/recon_tasks.py` | 新增 `MAX_BATCH_DELETE = 200`、`_batch_names(task_ids)`、`ReconTasks.delete_many(task_ids)`；`delete` 与 `delete_many` 共用 `_remove(doc)` |
| `income_recon_service/income_recon_api`、`bank_recon_service/bank_recon_api` | 各加 `delete_many(task_ids)`（POST RPC，沿用同一 `_transaction` 死锁重试壳） |
| `scripts/cowork-income-recon-lab-verify.py`、`scripts/cowork-bank-recon-lab-verify.py` | 两侧各加批量删除用例与匿名探针 |

判定与部署版的对照：

- **空批次**：`None`/`""`/`[]` → `{"deleted": 0, "rows_deleted": 0, "ids": []}`，不报错（与部署版"空 ids 返回 0"一致）。
- **重复的名字**只删一次；**非字符串、空串、超长名字、非列表、超过 200 条**在删除前拒绝。
- **权限**：逐个任务按原生任务 API 的口径 `access.require(workspace)`（`recon` 功能 + 操作角色 + 工作区范围），与部署版的 `recon.create` 权限位对应（第 24 批已登记同一差异）。
- **原生子表**：部署版删任务行即可（明细在 JSONB 里）；原生有结果行表，所以先按任务删行、再删任务，并回报 `rows_deleted`。

## 有意差异

1. **先检后删，全批原子**：部署版对不属于本人或不存在的 id **静默跳过**，返回一个计数；原生在删任何东西之前先把整批检查完（存在性 + 范围），任何一条不合格就整批拒绝——不会留下"删了一半"的批次。用例固定了这一点（被拒的批次两条任务与其行都还在）。
2. **不存在的任务**报 `reconciliation.task_not_found`（`DoesNotExistError`），不再静默；部署版返回计数 0、无法区分"没这条"与"不是你的"。
3. **上限 200 条**（部署版无上限），与任务行数上限同量级；超限在删除前拒绝。
4. **返回体**：`{"deleted", "rows_deleted", "ids"}`，比部署版的单个 `deleted` 计数多出被删的行数与真实删掉的名字（前端要展示"删了哪些"时可用）。
5. 序号型 id：部署版是自增整数 id，原生是 `CIR-`/`CBR-` 前缀的字符串名，所以入参是名字列表而不是 id 列表。

## 验证

```sh
python3 scripts/check-architecture.py
python3 scripts/i18n-build.py --check
git diff --check

# 隔离 lab（Docker context colima-pearnly，project pearnly-cowork-backend-lab）
../env/bin/python /workspace/scripts/cowork-income-recon-lab-verify.py   # 两个 Site 各 19 个
../env/bin/python /workspace/scripts/cowork-bank-recon-lab-verify.py     # 两个 Site 各 12 个
../env/bin/python /workspace/scripts/cowork-income-recon-export-lab-verify.py  # 各 3 个
../env/bin/python /workspace/scripts/cowork-bank-recon-export-lab-verify.py    # 各 3 个
```

- 隔离 Site：收入 **19** 个（第 30 批 16 + 本批 3：整批删除含重复名与空批次、跨范围整批拒绝且无部分删除、未知名字与坏形状拒绝）、银行 **12** 个（第 24 批 10 + 本批 2：整批删除、跨范围与未知名字都不删）通过；两侧匿名探针新增 `delete`/`delete_many` 均 **403**；两个工作簿导出脚本各 **3** 个继续通过。
- 主机侧：对账 **137** 个用例、架构 **0** 违规、`i18n-build --check` PASS、`git diff --check` 干净。本批无新增界面文案。
- 未执行：真实文件读取、异步任务与轮询、前端、部署、外部结果与用户验收。

## 来源核对与运行投影

- 交付 `baseline-thirty-first-delivery.json`（`4f1b28f2…`，采集 2026-09-21T14:50:09Z）与第 30 批交付快照 `compare` **PASS**，`source` 仍 `ec2e5c957e6b…`。
- 运行投影 `push-runtime-thirty-first-delivery.json`（`0139ae60…`）：端点 `configuration_digest` `bf041a45…` → `6d327c98…`（端点数 60、平台开关不变）。**完整配置与旧状态迁入继续 HOLD**。

## 未做与后续

- 收入族只剩**异步任务与轮询**（`/gl-vat/submit` 那条链）。银行侧剩余项：异步状态与轮询、手动覆盖痕迹/运行警告落库。
- 未接前端、未部署、未合并回 main、未推送；除只读的来源采集外没有外部调用，没有写任何旧库。
