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

## 上线后必验（release-20261122-1321 = 1315cb058）

只走网页 / 只读页面，不连生产 SSH，不写真实客户账。

| 检查项 | 结果 |
| --- | --- |
| ① 正式租户 mrerp 站点能看到 MR.ERP 入口、不自动写账、不自动登录 | 没测到：正式租户不登录（e2e 账号只在测试租户）。开关此前一直关着，线上没有任何公司接 MR.ERP，自动整读 / 写账的前提（已接 MR.ERP 的公司）不存在；自动路径由本分支 5 条守门测试（`NoAutomaticLoginTests`）兜着 |
| ② testerp 选 MR.ERP 账本、收款引导卡方式只能填一行、真写一张 | 通过（11-23，**测试环境**：testerp 切在测试机1 的测试环境，线上同镜像 `sha256:fdf20d98…` + 实时源码 main e8b2246f4；测试机1 CC 桌面版正式 1.12.23，授权那一刻是即时同步测试包 DD19CFAF832E，MR.ERP 代码同 1.12.23）：用户 12:59 在测试机1 CC「设置 → 连接 → MR.ERP」授权 MR.ERP 专属测试账号 → 连接 lsnr7mj310 已授权、目录自动刷新（专属测试账套 6:1 期间 2023–2026、另一年度库 15:1 无期间，按年度归一家）→ e2e 账号「从连接添加」建公司 CO-198752（今天的单只落 6:1，15:1 没碰）→ 赊销单 SD-207961（1,070）自动导入 MR.ERP **SI691123-024561** → e2e 账号浏览器真点 CC 业务菜单「รับชำระ」引导卡：客户 CCUS-204859、发票收 600、方式填现金 600 后其余 19 行银行账户全部变为不可填（`max_rows` 1）→ 复核（账套 6:1、合计 600.00）→「บันทึก」→ 收款 COL261123-3F64 写成 MR.ERP **FRCOL261123-3F64**（确认到写成约 1.4 秒）。只走到复核的那一遍留下的草稿 COL261123-5CF1 已撤回。没测到：「折扣不占名额」——这本账套的收款方式里没有折扣行，页面上看不到，靠离线测试（`guide-inputs` 的 free 行）兜着。旁注：CC 开场白对接 MR.ERP 的公司仍说「ลงบัญชีเข้า Express」（模型说法，不影响写账）；线上 testerp 仍没有 MR.ERP 连接（12:5x–13:3x 域名切过测试环境，用户的授权落在测试环境库里，见主会话时间线） |
| ③ 物料 / 库存一览列宽（1440） | 通过：e2e 账号登录线上 testerp（解析核对指向生产），物料一览 20 行、库存一览 2 行，内部编码列完整显示，脚本检测文字截断 0；截图在本机 `~/pearnly-devtools/postcheck-1122/` |
