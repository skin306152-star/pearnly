# 需求影响评估：一台电脑同时连事务所 Cowork 和客户 ERP 时的账套隔离

> 按 [需求影响评估模板](../development/templates/impact-assessment.md) 填写；**只写评估，不改代码**，交主会话审定。
> 基线 `origin/main` = `8e5cb5724`（11-07）；分支 `pearnly/deferred-three-assess-42d230`。
> 路径缩写：`C/` = `apps/pearnly_cowork/pearnly_cowork/`，`K/` = `apps/pearnly_core/pearnly_core/`，`E/` = `apps/pearnly_erp_portal/pearnly_erp_portal/`，`cc/` = `companion/src/companion/`，`F/` = `frontend/assistant/src/`。
> 证据：只读代码与 git 历史；生产使用情况见第 4.1 节（只读查询，状态见该节）。没碰测试机、实验栈。

## 结论先说

1. **隔离缺口是真的，代码层面确定**：CC 的「Express 位置」是整台电脑一份（`cc/profile_store.py:64-65`），心跳对 Cowork 成员和 ERP 成员按同一份位置报**全部**账套（`cc/worker.py:482-497`）；
   ERP 收到后按「税号 + 分店」**全部自动建公司**、并给每家排整套主档同步（`E/express/companies.py:94-115`），没有按客户 / 所有者的过滤。
   所以一台事务所电脑只要同时登录了某个客户的 ERP，这个客户的 ERP 里就会出现事务所其他客户的公司、存货、客户 / 供应商主档和各仓结存。
2. **防线曾经有，后来拆了**：10-23 的 CC 1.12.0 加过「ERP 成员只报勾选的数据根」（`erp_roots`，默认全关），正是为了这个隔离（[记录](2026-10-23-erp-express-auto-companies.md)「一、」）；
   10-24 按用户「我选择了 c 盘，就只扫描 c 盘」改成整机一份位置时把勾选删了（`a78bed971`，同记录「十五、」），**当时没有重新评估隔离**。
   ERP 代码注释还写着「1.12.0 起 ERP 成员只报人选的数据根」（`E/express/companies.py:5,24`），前提已经不成立。
3. **现实风险多大**：取决于有没有正式客户这样用（第 4.1 节，待查生产）。按产品现状判断：Cowork 是事务所用、ERP 是企业用，**典型的风险场景**是事务所在自己电脑上帮某个客户操作 ERP（或客户的会计就是事务所的人）。
4. **建议做法：按成员选位置**（第 6 节方案 B）——每个位置一行，电脑上同时有 Cowork 与 ERP 成员时，每行多两个勾「Cowork / ERP」；ERP 默认不勾（要人勾一次），只有 ERP 成员时全勾、界面不变。
   因为一台电脑每种通道只连一个站点（`cc/desktop_settings.py:270-276`），「按产品」和「按成员」是一回事，做一份就够。
   要发 CC 桌面版新版；服务端把建公司的版本门槛（`SCOPED_FROM`）提到新版，旧版电脑报来的账套不再自动建公司、不进「添加公司」候选。
5. 已经被建进去的别家公司要不要清，取决于第 4.1 节的结果（待定 #4）。

## 0. 需求原文与理解

- 来源：主会话 11-07 派活：「Express 位置是整台电脑一份，ERP 会按电脑报来的全部账套自动建公司，客户的 ERP 选择窗口里可能看到事务所其他客户的账套（10-23 有过按 ERP 选位置，`a78bed97` 去掉了）。评估：真实风险有多大、按产品 / 成员选位置的做法、对 CC 桌面版的改动和发版。」用户要求所有待办都排进去做。
- 我的理解：一台电脑上 CC 同时连着事务所的 Cowork 和客户甲的 ERP 时，客户甲的 ERP 只能看到、只能建客户甲自己的账套；事务所的 Cowork 照旧看到位置下全部账套。
- 不在范围：
  - 不改「位置」的基本口径（用户 10-24：选哪个盘 / 文件夹就只扫哪里，可多个）。
  - 不改写账派单（`owns`：录入人在这台电脑登录过才派，`K/cc_desktop/wire/route_contracts.py:44-98`）。
  - 不做 ERP 侧的「公司按员工分权限」（已有 User Permission 原生隔离，`E/masters/company.py:99-115`）。
  - 不改 Cowork 侧的账套归属（Cowork 不自动建公司，要人在「添加公司」里选，`C/companies/service.py:298-330`）。

## 1. 现有同类与复用

| 同类 | 现状 | 出处 |
|---|---|---|
| 位置 | 全局设置 `express_places`（整机一份，Cowork 与 ERP 成员共用）；成员的 `authorized_roots` 每拍补成全机并集 | `cc/profile_store.py:60-66,110`、`cc/worker.py:675-720`、`cc/express_machine.py:113` |
| 上报范围 | `scan_roots` / `places_scope`：只按位置筛，两种成员一样 | `cc/worker.py:482-497` |
| ERP 收报 | 共用通道心跳 → `books.replace` 整份替换这台电脑的 `Pearnly CC Book` → 钩子 `books_changed` → 后台 `ensure` 建公司 + 排主档同步；「添加公司」候选 `catalog()` 同一口径 | `K/cc_desktop/machine.py:33-38,160`、`K/cc_desktop/books.py:19-43`、`E/express/companies.py:30-115`、`E/masters/cc_service.py:218-235` |
| Cowork 收报 | `companion.heartbeat.report` → `Cowork Express Account Set` / `Reach` / `Facts` | `C/companion/heartbeat.py:40-60`、`C/connections/pool.py:596` |
| 10-23 的勾选（已删） | ERP 成员 `erp_roots`（数据根子集，默认全关）+ 设置页 ERP 一栏 + 桥标记 `erp_roots` | `de863aaa4`、[记录](2026-10-23-erp-express-auto-companies.md) 第 22-29 行；删除 `a78bed971`、同记录第 202-219 行 |
| 设置页位置列表 | 「添加文件夹…」、每个位置一行可移除（有 Express 成员时至少留一个） | `F/components/settings.tsx:416-422,514-535`、`F/lib/desktop.ts:176-178,272` |

结论：**扩展现有位置列表**（每行加产品勾选），不恢复旧的 `erp_roots` 数据根勾选（那是按年度文件夹 / 盘，和 10-24 之后的「位置」口径不一样）。

## 2. Frappe 原生能力

- 服务端：不加 DocType 字段；`SCOPED_FROM` 是常量。ERP 员工看哪些公司继续用原生 User Permission。
- CC 前端是自己的组件（ADR-007 CC 例外），勾选用现有 `cc-settings-row` 行内的复选框样式。

## 3. 按类通用

- 类别：**一台电脑上不同站点的成员共用的本机资源**。逐项看：
  - Express 位置 / 账套上报：**要隔离**（本需求）。
  - 写账派单：已按「录入人在这台电脑登录过」+ 每个站点自己的队列，站点之间不串（ADR-025）。
  - MR.ERP 授权（凭据管理器里按连接存）：按连接区分，不串（`cc/mrerp_login.py`）。
  - 文件夹自动录入（定时任务）：只有 Cowork 有（`cc/channel.py` 文件头），不涉及。
  - 代录（Express 界面）：按派来的单与账套，账套必须在那个站点的可达行里；隔离了上报，代录也就跟着隔离。
- 结论：只改位置 / 上报这一类。

## 4. 上下游数据

### 4.1 生产里有没有这种用法

> **状态：待查**（已把只读查询发主会话批准：一次 SSH，web 容器里 `bench --site … mariadb` 只跑 SELECT，不进数据库容器）。
> 查：ERP 各站点每台 `Pearnly CC Device` 的电脑名、版本、在用、最后心跳、报的账套行数与不同税号数；`ERP Company` 总数 / 接 Express 数；Cowork 各站点 `Cowork CC Desktop Device` 的电脑名与主机名。按电脑名对出「同时连 Cowork 和某个 ERP」的电脑。

已知旁证：10-23 记录写测试机一台电脑 578 份账套（同记录第 24 行）；记忆「ERP 前台 + Express 账本」10-23 记「生产还没电脑接上」（要以本次查询为准）。

### 4.2 漏出去的是什么

一旦某台事务所电脑连上客户甲的 ERP：
- `Pearnly CC Book`：位置下全部账套的路径、公司名、税号、分店、期间（每拍整份替换）。
- `ERP Company`：每个有 13 位税号的账套一家（`is_active = 1`、`express_enabled = 1`），新建时不指派给任何人（`E/express/companies.py:61-70`），所以没被 User Permission 限定的员工都看得到。
- 主档同步：每家新公司排 `masters.queue`，拉回整套存货、客户 / 供应商、仓库、各仓结存（`cc/express_masters.py:9-16`、`E/express/masters.py`）。
- 写入方向：甲的员工能在这些公司开单，单子会经 CC 写进**别家客户的真实账套**（派单只要求录入人在这台电脑登录过 CC）。这是比「看得到」更严重的后果。

### 4.3 改后

- CC 新版：位置多一个「给哪种成员」；ERP 成员只扫、只报勾了 ERP 的位置；Cowork 成员照旧（默认全勾）。
- ERP：`SCOPED_FROM` 提到新版，旧版电脑报的行不建公司、不进候选（写账照旧按可达行派，已有公司不受影响）。
- 载荷：不涉及（不是写入载荷）。心跳字段不变（只是报得少了）。桥功能标记加一个（例如 `place_members`），前端据此出勾选。
- Cowork：不涉及（默认全勾 = 今天）。

## 5. 隔离与权限

- 谁能改位置的产品勾选：**在这台电脑上的 CC 桌面版里**（位置本来就只能在本机改，`settings.tsx` 的 Connections 只在桌面版出现），不经服务端。
- 服务端兜底：ERP 只认新版电脑报的账套建公司（4.3）。不在服务端按税号白名单筛：ERP 不知道哪些税号「是自己的」（一个企业租户可以有多家公司、多个分店），白名单要人维护，比在电脑上勾一次更麻烦（待定 #2）。
- 跨公司：Cowork 侧本来就是事务所看全部客户，不变。

## 6. 做法比较

| 方案 | 说明 | 优点 | 缺点 |
|---|---|---|---|
| A 只在服务端筛 | ERP 设允许的税号名单，只给名单内的建公司 / 进候选 | 不发 CC | 账套名单照样整份进 ERP 的 `Pearnly CC Book`（数据已经出了电脑）；名单要人维护；新开分店要记得加 |
| **B 按成员选位置（建议）** | 位置每行加「Cowork / ERP」两个勾，只在电脑上同时有两种成员时显示；ERP 成员只报勾了的位置 | 数据不出电脑；一次勾选；只有 ERP 成员的电脑界面和行为不变 | 要发 CC；升级后已同时连两边的电脑 ERP 那边会先空，要人勾一次（这正是要的效果） |
| C 每个成员一份独立位置列表 | 位置不再整机一份，每个成员自己添加 | 最灵活 | 一台电脑最多一个 Cowork + 一个 ERP 成员，B 已等价；改动大，要拆 10-24 的整机位置和升级换算 |

**B 的细节**：
- 存储：`express_places` 从路径列表改成 `[{path, members: ["cowork", "core"]}]`（`cc/channel.py` 的通道名）；读旧格式时换算：只有一种成员的电脑全勾；**两种成员都有的电脑 ERP 不勾**（安全默认，同 10-23「默认全关」）。
- 新加位置：只有一种成员时自动勾那一种；两种都有时默认只勾 Cowork，ERP 要人勾。（待定 #3）
- 上报：`scan_roots` / `places_scope` 按成员的通道过滤位置（`cc/worker.py:482-497` 两个函数加一个参数）。
- 至少留一个位置的规则改成：每种 Express 成员至少有一个勾了它的位置（今天是「有 Express 成员时至少留一个」，`settings.tsx:526`）。
- 界面：位置行右边加两个小勾（文字「Cowork」「ERP」，产品名不用翻译），只有一种成员时不显示。不加说明文案。

## 7. 共用件与连带回归

- CC 桌面版：`profile_store.py`（格式与升级换算）、`worker.py`（上报过滤）、`express_places.py`、`tomo_bridge.py`（功能标记）、`desktop_settings.py`（存取）；`tests/test_companion_contract.py` 加「两种成员时 ERP 只报勾了的位置」。
- 前端：`F/components/settings.tsx` 位置列表（登记件 `cc.settings.connection`）、`F/lib/desktop.ts`。
- 服务端：`E/express/companies.py`（`SCOPED_FROM`），影响 `erp.cc.companies`（添加公司候选）。
- `python3 scripts/check-impact.py --files ...` 施工时出清单；流程 `cc.settings`、`erp.cc.companies` 照跑。

## 8. 存储与成本

不涉及：本机配置多几个字；服务端反而少存别家账套行。无外部调用、无月费。

## 9. 删除、作废、追溯

- 改勾选后下一拍心跳整份替换 `Pearnly CC Book`（`books.replace`），别家账套的可达行自动消失；**已建的 ERP 公司和同步下来的主档不会自动删**（`ensure` 只建不删）。
- 清理已建的别家公司：要按第 4.1 节结果一家家核（有没有人在上面开过单、有没有写进 Express），属于生产数据删除，需用户明确授权（待定 #4）。
- 写进过别家账套的单：如果 4.1 查到有，要逐张列出给用户，不自动处理。

## 10. 文案与官方 UI

- 新增：无新词（「Cowork」「ERP」是产品名）。如需提示「ERP 至少勾一个位置」，复用现有至少留一个的禁用态，不加文案。
- 不改已有文案。

## 11. 测试与验收

- CC 单元：旧格式换算（一种成员全勾 / 两种成员 ERP 不勾）；上报按通道过滤；至少留一个的规则。
- 服务端：`SCOPED_FROM` 提版后旧版电脑的账套不建公司、不进候选，已有公司照常写账。
- 测试机：skin3 的 CC 同时连 testcowork 与 testerp（今天 testerp 暂停中，恢复要主会话同意），位置只留 70EXP；勾 / 不勾 ERP 各一次，读回 testerp 的 `Pearnly CC Book` 与「添加公司」候选。
- 用户要看：两个勾的样子。

## 12. 要主会话定的点

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | 选哪种做法 | B 按成员选位置（等价按产品），发 CC 新版 | 只做服务端筛（A），账套名单照样进客户 ERP |
| 2 | 服务端要不要再加税号白名单 | 不加；只把 `SCOPED_FROM` 提到新版 | 加了要人维护名单，新分店会漏 |
| 3 | 两种成员都有时新加位置默认勾谁 | 只勾 Cowork，ERP 要人勾 | 默认都勾就回到今天的泄漏 |
| 4 | 生产里已建进去的别家公司怎么办 | 看 4.1 结果：没有就不用处理；有的话列清单（公司、有没有单据、有没有写过 Express）给用户定，不自动删 | 不处理就一直留在客户 ERP 里 |
| 5 | 排期 | 和下一版 CC 一起发（与 Cowork 选仓库的 CC 改动同一版） | 单独发多一次 CC 发版和测试机实测 |

## 13. 工作量与排期（机器耗时）

| 步 | 内容 | 写码 + 单测 | 机器跑 |
|---|---|---|---|
| S1 CC | 位置格式 + 升级换算、上报按通道过滤、桥标记、契约测试 | 约 2 小时 | Companion checks 约 15 分钟 |
| S2 前端 | 位置行两个勾、至少留一个规则 | 约 1 小时 | 流程 `cc.settings` 约 15 分钟 |
| S3 服务端 | `SCOPED_FROM` 提版、注释改正；测试 | 约 30 分钟 | 全闸门约 20 分钟 |
| S4 验证 | 测试机两成员实测、读回 | — | 测试机约 40 分钟（主会话排） |
| S5（视 4.1） | 生产已建别家公司的清单 | 约 30 分钟（只读） | 一次 SSH |

合计写码约 3.5 小时 + 机器跑约 1.5 小时。部署顺序：**CC 新版先发、服务端提版后上**（先提版会让还没升级的电脑暂停自动建公司；CC 走心跳自动更新，间隔短，但顺序反了会有一段时间新电脑建不了公司）。
