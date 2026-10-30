# 系统路径不按当前账号核公司（2026-10-30 生产事故）

## 事故

- e9aa959b 部署激活 ERP 时，testerp 迁移在 `v1_19_terminal_main_warehouse` 报 `PermissionError: Not permitted`，ERP 停服约 9 分钟
  （主会话手工用新镜像拉起、关维护，5 个站点 200）。gize / mrerp / polymer / test01 迁移成功；testerp 的 v1_19、v1_20 没跑，下次迁移重跑。
- 调用链：`patches/v1_19` → `cashier.terminals.attach_main` → `inventory.warehouses.main_of` → `masters.company.document_company`
  （公司不在 `allowed_companies(Administrator)` 里就报错）。

## 为什么 testerp 有公司不在 Administrator 的范围

- `allowed_companies("Administrator")` 只回**启用**的公司（`is_active=1`）；补丁遍历全部公司（含停用）。
- 生产只读查一次（10-30，一次 SSH）：testerp 65 家公司，启用 64（62 家接 Express、2 家没接），**停用 1 家**：`CO-00009`「213」，
  测试账号 `aksarapak.aa@gmail.com` 2026-09-24 12:32 建、12:33 停用（Version 记录 `is_active 1 → 0`），没有税号、分店、Express 账套。
  其余 4 个站点没有停用公司，所以迁移成功。

## 修法

- `inventory.warehouses.main_warehouse(company)`：取主仓、**不核当前账号**，给系统路径；`main_of` 照旧先 `document_company` 再调它（用户路径不变）。
  `warehouses.owner_of(name)`：仓库所属公司、不核当前账号；`company_of` 照旧核。
- `cashier.terminals.attach_main`（补丁与 Express 同步之后调）改用 `main_warehouse`。
- `inventory.stock.apply_movement(system=True)`：系统路径写流水（Express 主档同步对齐库存）。必须同时 `internal=True` 且给公司；
  公司按数据核（仓库属于这家公司，`owner_of`），不走 `_movement_company` / `company_of`。页面接口 `_clean` 去掉 `system`
  （本来就去掉 `internal`，`system` 离开 `internal` 直接拒）。`express.masters._stock` 传 `system=True`。

## 按类查（补丁、after_install / after_migrate、定时任务、CC 桌面版回调、开通、识别回调）

从 hooks 里的系统入口沿调用图找能走到「不带 user 参数、按当前账号核公司」的函数（`document_company` / `allowed_companies` /
`active_company` / `scope` / `company_options`），修之前 12 条路径：

| 路径（入口 → … → 函数） | 结论 |
| --- | --- |
| `patches.v1_19` → `attach_main` → `main_of` → `document_company` | **事故本身**，改用 `main_warehouse` |
| `express.masters.advance` → … → `masters.apply` → `attach_main` → `main_of` | 同上（同步之后挂主仓） |
| `masters.apply` → `_stock` → `stock.apply_movement` → `_movement_company` / `company_of` / `required` / `resolve` / `sole` / `guard.require` | **会出同样的错**：同步以 Administrator 跑、停用公司对不上；改 `system=True`（实验栈实测旧写法报 Not permitted、新写法写入） |
| `masters.apply` → `push.requeue_unmapped` → `warehouses.names_of` → `active_company` | 不改：公司总是给了，`names_of` 只在没给公司时才取当前公司 |
| `push.poll` / `pos_day.nightly` → `push.send` → `actor_of` → `allowed_companies(user)` | 不改：核的是指定员工的范围（谁能领这件写账作业），不是当前账号 |
| `integrations.intake.run` → `totals` → `sales.entry.preview` → `guard.require` → `require_pos` | 不改：识别任务先 `frappe.set_user(发单员工)`，之后就是那位员工的用户路径 |

其他 App（Cowork / DMS / 控制面 / 共用层）的补丁里没有直接用这些函数。doc_events 钩子
（`validate_company_unchanged`、库存单写入闸）按数据或写入凭证判断，不看当前账号。

## 防再犯

- `tests/test_erp_system_paths_company_scope.py`（Development checks，纯静态）：从 hooks 的系统入口出发走调用图，走到上面那几个函数
  （不带 user 参数）就报出整条路径；靠参数或身份切换保证安全的边登记在 `SAFE_EDGES`（写明原因，只许减少），并断言 Express 库存同步
  的 `apply_movement` 带 `system=True, internal=True`。在修之前的代码上跑报出 2 条（补丁与同步后的挂主仓）。
- `tests/test_erp_terminal_main_warehouse.py`：`attach_main` 在 `document_company` 会报错（公司不在可用范围）时照样改挂。

## 验证

- 实验栈 erp-new 造一家停用公司（不在 Administrator 可用范围）+ 一台没挂仓的收银台：挂修之前的代码 migrate 重跑 v1_19，报
  `PermissionError: Not permitted`（与生产同一调用链）；换成本分支 migrate 通过，打印「terminal … now sells from the main warehouse」。
- 同一家停用公司以 Administrator 写 Express 同步调整：旧写法 Not permitted；`system=True` 写入；别家仓拒；`system` 不带 `internal` 拒（回滚未留数据）。
- 受影响流程用例：见提交状态 `pearnly/flows`。
