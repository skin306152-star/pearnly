# 打开 MR.ERP 两个开关（2026-11-22，用户定对用户开放）

分支 `pearnly/mrerp-switch-on`。用户 11-22 定：MR.ERP 计划里没有定开关时机，就打开，随今晚批次上线（主会话转）。

## 改了什么

- `apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/__init__.py`：`MRERP_LEDGER_ENABLED = True`（ERP 选账本 / 连接 / 账套 / 选择 / 导入 /
  对话工具 / 待处理的 MR.ERP 分支 / CC 授权钩子 / 收款写进 MR.ERP）。
- `apps/pearnly_core/pearnly_core/express_operator/contracts.py`：`MRERP_OPERATOR_ENABLED = True`（Cowork 与 ERP 的 MR.ERP 代录）。
- 两处都不按站点 / 租户开；改回 False 就回到只有 Express 的行为（两个常量仍是唯一总闸，「关着」那几组测试改成打补丁关着照跑）。
- 不经人触发的 MR.ERP 读取不登录（主会话定 B，按类做全）：每小时整读主档（`mrerp.masters.sync_all`）与写单后的那次整读
  （`masters.after_write`）都走 `masters.run(warm=True)`——只用缓存里还热的 MR.ERP 会话（共用层 `mrerp.account.use(login=False)`，
  即时同步分支带进来的），没有或过期就跳过这次、不登录、不记失败。人点的（公司页同步、选账套、打开收款卡排的读取）和写账本身照旧。
- `scripts/impact/cc-coverage.toml`：线开了，5 格从「等 MR.ERP」升「半」（采购 / 销售 × Cowork 代录、ERP 代录的 MR.ERP 格；ERP 直写收款的
  MR.ERP 格），总表第 8 节重生成。

## 开关打开会暴露的自动路径（查过的）

| 路径 | 触发 | 会不会自己登录 MR.ERP | 处理 |
| --- | --- | --- | --- |
| ERP 每小时 `mrerp.masters.sync_all` → `masters.run` | 定时 | 原来会（会话过期就自动重登，顶掉会计浏览器） | 改走 `warm=True`，不登录 |
| ERP `masters.after_write` → `masters.run` | 写单成功后顺手 | 原来会 | 改走 `warm=True`，不登录（刚写过，会话一般还热） |
| ERP `mrerp.push.poll`（每分钟） | 人提交的单等账号 / 中断后再排 | 写账时登录 | 不改：写账是用户提交单据后发生（产品现有设计） |
| ERP `collection.mrerp.poll` / `run` / `run_read` | 人确定的收款、人打开的收款卡 | 写 / 读时登录 | 不改：用户操作后发生 |
| ERP `collection.mrerp._queue_masters` → `choices.run_read` | 打开收款卡、主档没读到 | 读时登录 | 不改：用户操作后发生 |
| Cowork `push.mrerp.runner.sweep`（每 5 分钟） | 用户已提交的推送记录重排 | 写账时登录 | 不改：用户操作后发生 |
| Cowork `mrerp_collector` / `mrerp_menu_reader` | 只从人点的接口（`connections.api`，`manual=True`） | 人点了才登录 | 不改；没有定时 / 非人触发的自动读（查过） |

## 部署说明（随批次）

- 两个开关对所有站点生效。线上 mrerp 站点（已有一台事务所电脑接 ERP）开后会看到 MR.ERP 入口（选账本、连接里的 MR.ERP、从连接添加的
  MR.ERP 标签等）；**不会自动写任何账**：MR.ERP 写入只在用户提交单据、确定收款或点同步之后发生。
- 接上 MR.ERP 的公司每小时只用还活着的会话整读主档，**不会自动登录**（不会顶掉会计浏览器里的 MR.ERP 登录）；会话不在时跳过，等人操作后再读。

## 实测（本机实验栈 + MR.ERP 专属测试账套）

环境：实验栈 `pearnly-switch-lab`（回归实验室编排，源码 = 本分支工作树，开关在源码里已开），站点 `erp.regression.localhost`；MR.ERP 线上站点的
专属测试账号与测试账套（账套键 6:1）。走产品服务函数（同网页 / 对话调的一组），没有用浏览器点。

| 入口 | 结果 |
| --- | --- |
| ERP 接 MR.ERP 连接、读账套目录（`catalog.run`） | 通过：列出两本账套（6:1、15:1） |
| ERP 选账本 MR.ERP 与选账套（`catalog.choose`） | 通过：公司 `mrerp_enabled=1`、账套 6:1、`express_enabled=0`；整读主档 ready |
| ERP 直写采购（服务费，赊购） | 通过：PUR-2026-13871 → MR.ERP **PG691122-288584**（首次推单按产品「选一次」记下采购菜单 68、费用菜单 454、费用科目 5130-20） |
| ERP 直写销售（服务，赊销） | 通过：SD-13873 → MR.ERP **SI691122-409406**（642.00；选一次默认银行科目 1112-01） |
| ERP 收款引导卡（一种收款方式） | 通过：读取中「Reading from MR.ERP」；方式题 `max_rows: 1`；现金 + 转账两行被拒 `too_many_rows`；现金 642 → COL261122-5C7C → MR.ERP **FRCOL261122-5C7C**（这次由实验栈后台进程照开关打开后的正常路径写入） |
| ERP / Cowork 代录 MR.ERP | 没测到：要测试机上的 CC 桌面版在 MR.ERP 网页里录（本次不排测试机） |

测试账套上本次留下的单：PG691122-288584、SI691122-409406、FRCOL261122-5C7C（专属测试账套，可留）。实验栈用完连卷删除。

真机记录 | erp.desk.pearnly-purchase.action.Submit | 2026-11-22 | a8b5ea33f | 本机实验栈 pearnly-switch-lab（a8b5ea33f + 本分支开关改动）+ MR.ERP 专属测试账套 | 通过 | 开关打开后 ERP 采购单提交排上 MR.ERP 导入，选一次菜单与费用科目后写成 PG691122-288584
真机记录 | erp.desk.pearnly-sales.action.Submit | 2026-11-22 | a8b5ea33f | 本机实验栈 pearnly-switch-lab（a8b5ea33f + 本分支开关改动）+ MR.ERP 专属测试账套 | 通过 | 开关打开后 ERP 销售单提交写成 SI691122-409406
