# 需求影响评估：MR.ERP 连接只留一份界面（Cowork 网页「推送目标」与 CC「设置 → 连接」）

> **审定：主会话 11-07 审过，第 12 节 5 条全部按建议定。** 并进 CC 连接评估的 D 步，由接手 B / C / D 的窗口施工。

> 按 [需求影响评估模板](../development/templates/impact-assessment.md) 填写；**只写评估，不改代码**，交主会话审定。
> 基线 `origin/main` = `8e5cb5724`（11-07）；分支 `pearnly/deferred-three-assess-42d230`。`C/` = `apps/pearnly_cowork/pearnly_cowork/`，`F/` = `frontend/assistant/src/`。
> 证据：只读代码 + CC 连接评估（`pearnly/cc-connect-company-picker-3e453c` 分支 `docs/work/2026-11-07-cc-connect-company-picker-评估.md` §1.4、§12.1）。
> 没碰测试机、生产、实验栈。

## 结论先说

1. **背后本来就是一条连接**：四处界面都调 `connections.api.configure` 改同一条 MR.ERP 主连接，授权都走 `MrErpLogin` 的一次性票；数据不会分叉。要收的只是界面。
2. 界面现在有**两份实现、四个入口**（见 1.1）。CC 连接评估的 D 步（已审定、未施工）会把 CC 里的两处并成一个组件；剩下的就是 Desk 的 `connect_dialog`。
3. **建议唯一入口 = CC「设置 → 连接」最上面的 MR.ERP 一行**（D 步要加的那一行）。理由：授权只能在 CC 桌面版做（ADR-026 第 7 条），Desk 那份连授权按钮都是灰的，只能改网址。
4. Desk 两处（新建录入、自动录入规则）的目标卡片**保留状态和「连接」按钮**，按钮改成打开 CC 的「设置 → 连接」（新开一页 `/pearnly-app/?settings=connections`），Desk 不再自己弹 MR.ERP 窗口。CC 定时任务里的「连接」按钮同样直接打开设置的这一节。
5. 不改行为：谁能改网址（管理员）、谁能授权到站点 / 只存本机、目录什么时候刷新，全部照现状，由服务端判。
6. **排在 D 步之后做**，最好就并进 D 步：D 步本来就要抽组件、加这一行，再多一个深链接、两处 Desk 按钮改指向，约 1 小时写码。

## 0. 需求原文与理解

- 来源：CC 连接评估 §1.4 / §12 #12 的「以后要并成一个入口另起需求」，主会话 11-07 派活：「两份界面，背后同一条主连接。评估怎么收成一份（谁是唯一入口、另一处怎么链接过去），不改行为。」用户要求所有待办都排进去做。
- 我的理解：Cowork 管理员配 MR.ERP（网址 + 授权）以后只有一个地方可去；别的地方看到「未连接」时点一下就到那里。
- 不在范围：ERP 站点的 MR.ERP 连接（`ERP MRERP Connection`，`MRERP_LEDGER_ENABLED = False`，D 步已定开关关着时不出这一行）；Express 连接（下载 CC、电脑池、位置）不动；不加「断开连接」；不改授权规则。

## 1. 现有同类与复用

### 1.1 今天有哪几处

| # | 入口 | 实现 | 能做什么 | 出处 |
|---|---|---|---|---|
| 1 | Cowork Desk 录入工作台 → 新建录入 → 目标卡片 MR.ERP 的「连接」 | `targets.js` 的 `connect_dialog`（Frappe 原生 Dialog） | 改网址；显示「已授权：用户名 · 电脑 · 时间」；「在 CC 桌面版中授权」**一直是灰的** | `C/public/desk/targets.js:92-128`；调用 `C/public/desk/intake-choices.js:244-249` |
| 2 | Cowork Desk 录入工作台 → 自动录入规则 → 新建规则 → 目标卡片 | 同上 `connect_dialog` | 同上 | `C/public/desk/intake-folders.js:290-295` |
| 3 | CC 定时任务 → 目标卡片「连接」/ 规则里「连接」 | `scheduled-tasks.tsx` 的 `MrerpConnect` | 改网址 + `MrErpLogin`（在 CC 桌面版里可点授权，浏览器里灰） | `F/components/scheduled-tasks.tsx:1276-1335`，打开处 `:965,994,1147-1157` |
| 4 | CC 设置 → 连接（D 步要加，**还没有**） | D 步：把 `MrerpConnect` 抽成 `components/mrerp-connect.tsx` 共用 | 同 3，授权后调 `refreshCatalogs` 刷新目录 | CC 连接评估 §1.4 做法 1–3，§14 D 步 |

今天 CC 设置的「连接」：桌面版是 Express 那一节（位置、连接电脑）+ 电脑池；浏览器只有电脑池（只读）（`F/components/settings.tsx:101-150,678-680`）。**没有** MR.ERP 一行。

### 1.2 背后是同一条连接（核过）

- 1–3 都调 `pearnly_cowork.connections.api.configure`（`targets.js:111-116`、`scheduled-tasks.tsx:1291-1296`）：已有主连接就带 `name + expected_revision` 改那一条，没有才建；幂等键在弹窗打开时定。
- 主连接 = 本归属下、不挂客户、启用、代数 0 的最早一条（`C/connections/service.py` `main_connections / main_of`，ADR-021）；网页录入、LINE、CC 都按它认。
- 授权：`MrErpLogin` → `companion.api.mrerp_login_ticket` 发票 → CC 桌面版原生小窗收密码 → 机器通道交站点（`F/components/mrerp-login.tsx:1-30`）。谁写站点由 `connections/mrerp_login.py:47-55 scope_of` 判：管理员 `site`，名单成员 `local`（只存本机）。
- 所以**收界面不需要动服务端**，也没有要合并的数据。

### 1.3 结论

扩展 D 步：唯一入口用 D 步那个共用组件 + 设置里那一行；Desk 的 `connect_dialog` 删掉，按钮改成跳过去。不新建界面。

## 2. Frappe 原生能力

- Desk 那边删掉一个自定义内容的原生 Dialog，按钮仍是目标卡片自带的 action（`p.cowork_choice` 的 `action`），只改 `on_click` 去向：`window.open("/pearnly-app/?settings=connections")`。不新增 Desk 组件。
- CC 是自己的前端（ADR-007 的 CC 例外范围内），不涉及 Frappe 组件。

## 3. 按类通用

- 这一类是「连接配置入口」。同类还有 Express 的「连接」（Desk 的 `express_dialog` = 下载 CC + 在线状态，`targets.js:130-170`；CC 设置里是整节 Express）。
- **建议 Express 本批不动**：Express 的 Desk 弹窗只是「下载 CC 桌面版 + 显示在不在线」，不改配置，没有两份要维护的逻辑；用户 11-07 又定了 Express 连接保持现状（CC 连接评估开头的收窄段）。要一起收，作为待定项 #3。

## 4. 上下游数据

- ERP 内：不涉及（ERP 的 MR.ERP 账本关着）。
- Express / MR.ERP：不涉及，写入、目录读取都不变。
- CC 桌面版：不发新版。`MrErpLogin` 早已在 CC 里；深链接是网页前端的事。
- Cowork：录入工作台、自动录入规则的目标卡片状态照旧从 `intake_targets.state()` 读，`ready()` 判断不变，所以「MR.ERP 未连接时卡片置灰、不能选」的行为不变。

## 5. 隔离与权限

- 全部照现状，服务端判：`configure` 只给管理员（`access.require_actor(management=True)`）；授权范围 `scope_of`。
- D 步已定：设置那一行哪些按钮能点由 `records` 视图的 `management` / `credential` 决定，前端不判。Desk 跳过去以后看到的就是这同一份。
- 非管理员在 Desk 点「连接」：今天能打开弹窗、填了网址提交会被服务端拒；改后到 CC 设置里看到只读状态。行为等价（都改不了），提示更早。

## 6. 入口：网页、手机、LINE、CC

| 入口 | 改后 |
|---|---|
| CC 桌面版「设置 → 此电脑 → 连接」 | MR.ERP 一行（D 步）：网址 + 授权，唯一能授权的地方 |
| 浏览器 CC「设置 → 连接」 | 同一行，授权按钮灰（同今天 `MrErpLogin` 在浏览器里的样子），管理员可改网址 |
| CC 定时任务目标卡片「连接」 | 直接打开设置的「连接」一节（`openSettings("connections")`，`F/lib/desktop.ts:38`），不再弹自己的窗 |
| Desk 新建录入 / 自动录入规则的 MR.ERP「连接」 | 新标签页打开 `/pearnly-app/?settings=connections`；回到 Desk 后卡片状态照今天的定时重读刷新（`intake-choices.js` 开着弹窗时定时 `state()`） |
| 手机（CC 网页） | 同浏览器 |
| LINE | 不涉及（LINE 只选目标，不配连接） |

- **要新增的一处**：CC 打开时认 `?settings=<section>`，派发现有的 `tomo-settings` 事件（`settings.tsx:79-84` 已经按事件里的 section 打开）。今天没有这种深链接（查过 `F/lib/`，没找到）。
- 浏览器 CC 的「连接」节今天只给 Cowork 站点出（`settings.tsx:103 web = !api && product === "cowork"`），正好覆盖 Desk 的用户。

## 7. 共用件与连带回归

- 碰到的登记共用件：`cowork.page.intake`（`targets.js`、`intake-choices.js`、`intake-folders.js`，`scripts/impact/shared-parts.toml:861-871`）、`cc.mrerp-login`（`:2553`）、`cc.settings.connection`。施工时跑 `python3 scripts/check-impact.py --files ...` 出清单逐项实测。
- 流程：`cowork.intake.review`、`cowork.mrerp-login`、`cc.settings`；新增一条断言「Desk 点 MR.ERP 连接 → 新页打开 CC 设置的连接一节」。

## 8. 存储与成本

不涉及：不加表、不加外部调用、不加月费。

## 9. 删除、作废、追溯

- 删掉 Desk 的 `connect_dialog` 代码与它专用的词条（`connect.mrerp.authorizeInCC` 若别处不用，`check-copy.py` 会报孤儿）。
- 连接本身不删不改；连接变更记录（版本号）照旧。

## 10. 文案与官方 UI

- 不新增文案：按钮仍叫「连接」，状态词沿用 `connect.state.*`。
- 删除的词条以 `check-copy.py --report` 为准，施工时附清单。

## 11. 测试与验收

- 单元：`?settings=connections` 打开设置的连接节；未知 section 落到「通用」。
- 流程：Desk 新建录入里点 MR.ERP「连接」→ 新页 URL 与设置节；CC 定时任务点「连接」→ 设置连接节；`cowork.mrerp-login` 原样通过（授权、目录刷新）。
- 测试机 CC 桌面版实测随 D 步一起（主会话排时间）：在设置那一行授权一次，Desk 卡片变「已连接」。
- 用户要看的：Desk 点「连接」跳到 CC 的样子是否接受（待定 #2）。

## 12. 要主会话定的点

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | 唯一入口放哪 | CC「设置 → 连接」的 MR.ERP 一行 | 放 Desk 不行：Desk 不能授权，还是要去 CC |
| 2 | Desk 两处「连接」按钮怎么链过去 | 新标签页打开 CC 的「设置 → 连接」（`/pearnly-app/?settings=connections`）；保留卡片状态 | 在当前页跳走会丢掉正在填的新建录入弹窗 |
| 3 | Express 的 Desk「连接」弹窗要不要一起收 | 本批不动（只是下载 + 在线状态，无配置；用户 11-07 定 Express 保持现状） | 一起收要改 Express 的 Desk 下载入口，超出「不改行为」 |
| 4 | CC 定时任务里的「连接」 | 直接打开设置的连接节，不再弹 `MrerpConnect` | 留着就是同一组件两处出现（不算两份实现，但入口仍两处） |
| 5 | 排期 | 并进 CC 连接评估的 D 步一起做 | 单独做要等 D 步合入再改，多一轮闸门 |

## 13. 工作量与排期（机器耗时）

| 步 | 内容 | 写码 + 单测 | 机器跑 |
|---|---|---|---|
| 1 | （D 步本身，已估 45 分钟） | — | — |
| 2 | CC 认 `?settings=`；定时任务「连接」改 `openSettings("connections")`；Desk `connect_dialog` 删、两处按钮改指向；词条清理；流程断言 | 约 1 小时 | 流程 `cowork.intake.review`、`cc.settings`、`cowork.mrerp-login` 约 25 分钟；全闸门约 20 分钟 |

依赖：D 步之后（D 步排在按行选物料 Cowork / ERP、识别完自动继续合入 `main` 之后）。不动 `pearnly_core`，不跑生产镜像候选离线检查；不发 CC 桌面版。
