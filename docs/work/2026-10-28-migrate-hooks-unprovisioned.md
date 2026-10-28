# 迁移收尾钩子与激活脚本：未完成开户的站点不再中断整条迁移（10-28）

起因：[发布 fcb65cca](2026-10-28-release-fcb65cca.md) 时，`activate-product.sh` 的 `bench --site all migrate` 在新租户 polymer（开户卡在「创建工作台」、
站点配置里还没有 `pearnly_features`）上中断：ERP 迁移收尾钩子 `work.ensure_on_migrate` 建默认任务看板时被「没买任务管理」的权利检查拒，抛
`PermissionError`，已开户的 mrerp、testerp 跟着停在维护里约 10 分钟。主会话 10-28 定为当天第一项，随下一批部署。

## 改动

1. **钩子（按类）**：全仓 after_migrate / after_install 钩子：core（导航、站点图标 / 名称、个人页字段、会话时长、隐藏报错堆栈）、ERP（同步角色、登录设置、
   任务看板）、Cowork（同步角色、站点归属、任务看板）、控制面（只在控制面站点）。只有 **ERP 任务看板**依赖已购功能（`work.access.entitled()` 要求
   `tasks` 在 `pearnly_features` 里）；Cowork 任务看板只认产品与角色（迁移以 Administrator 跑，角色齐），不受影响。
   - core `pearnly_core.shell.migration_skip(hook, reason)`：迁移收尾钩子在这个站点跳过时用——打一行到迁移输出、记 `pearnly.migrate` 日志，不抛错。
   - ERP `work.boards.ensure_on_migrate`：站点没买「任务管理」（含半开户、还没写功能清单）→ `migration_skip`，不建看板。兜底已有：功能配好后的下一次迁移
     建；或第一次打开任务管理页（`open_board` → `default_board` + `ensure_lists`）当场建。
2. **激活脚本**（`scripts/production/activate-product.sh`）：`bench --site all migrate` 改为**逐站迁移**。
   - 开户没做完的站点（站点配置里没有 `pearnly_features`）迁移失败：告警、写进 `/opt/pearnly/state/unfinished-sites-<产品>.txt` 留给开户重试，
     其它站点照常迁移、照常放开。
   - 已开户的站点迁移失败：照旧整次激活失败（停在维护、不放开流量、不起旧 worker，同原设计）；一个站点都没列出来也算失败。
   - 站点清单只认站点名形状（容器启动会往标准输出打「Linking fresh assets to volume...」，10-28 续做脚本踩过）。

## 验证

- `scripts/production/test-activate-product.py` 25 条（新 3 条）：半开户站点迁移失败其余照常且记进清单、已开户站点迁移失败照旧停下且后面的站点不迁、
  没有站点不放开；原有的维护 / 备份 / 迁移失败不放开等用例照旧通过（替身改成逐站迁移）。
- `tests/test_erp_work.py` 新 2 条：没买任务管理的站点跳过并记一行、不建看板；买了的照旧建。
- **实验室实测**（本机 ERP 实验室 `pearnly-hooks-lab`，挂本工作树，Frappe 16.33.0）：新建站点 `half.localhost`（装 core + ERP、`pearnly_product=erp`、
  没有 `pearnly_features`，同 polymer 当时的状态）：

  | 情况 | 结果 |
  |---|---|
  | 改动前的 `work/boards.py`，迁移 | rc=1，`PermissionError: Not Permitted`（复现） |
  | 修复后，迁移 | rc=0，输出 `skip pearnly_erp_portal.work.ensure_on_migrate on half.localhost: feature tasks is not configured on this site`；其余收尾钩子都跑完 |
  | 配上 `pearnly_features=["tasks"]` 再迁移 | rc=0，默认看板 1 个、列 3 列 |
  | 正常站点 erp-pos-a 迁移 | rc=0 |

- 激活脚本只做了离线替身测试（生产编排在实验室跑不了）；下一次生产部署时看 `unfinished-sites-*.txt` 与各站点迁移输出。

## 相关但不在本次

- 新开户不走 migrate（`provisioning.finalize_site` 注释）：新租户的默认任务看板要等第一次打开任务管理页或下一次部署迁移才建。polymer 已在 10-28 00:13 单站补过。
