# 需求影响评估：ERP 普通单据与代录的写账人改成和收款一致（office_actor 兜底）

> 2026-11-16，主会话转用户原话「按建议」。代码依据 origin/main 290bc368b。待主会话审定后单独分支施工（排在 H3 与「未连接提示」之后）。

## 0. 需求与范围
- 要做：ERP 采购 / 销售单（网页、LINE 提交后的直写、CC 对话直写）与 ERP 代录交的单，写账件的 actor 从「开单人」改为 `express.push.office_actor(开单人, 公司, 账套)`：开单人自己有登录过 CC 的电脑（在线或关着）→ 仍归本人；没有 → 归这家公司里在够得到这本账套的电脑上登录过 CC、`issuer_valid`、这家公司在其可见公司里的员工（在线电脑优先、同台按名字）；一个都没有 → 仍归本人（显示「CC 桌面版未连接」）。
- 不做：Cowork（仍谁的活谁的电脑做）；收银日汇总、收款（已是 office_actor）。
- 待定（第 12 节）：出入库单 / 盘点调整（`actor_of` 现在对它们同样回开单人），用户原话只说采购 / 销售。

## 1. 复用
- 直接复用 `express.push.office_actor`（10-25 收银日汇总、11-11 收款抽成共用）。改一处：`push.actor_of` 对采购 / 销售（待定：出入库、盘点）也走 `office_actor`。不新建机制。

## 3. 按类与全部调用方
`actor_of` 只有两处调用（`express/push.py`）：
- `send` 里排写账件（第 494 行）——所有写 Express 的入口都汇到这里：网页提交（`express/api.py`）、LINE 提交后的自动写（提交即排，经 `push.send`）、CC 对话直写（`express/cc_tools.py`、`conversation.py`）、代录（`express/operator.py submit`）、写账判定卡片重推（`precheck.resume`）、票面合计改后重推（`printed.py`）、每分钟补交（`push.poll`）。
- `_queued_note`（第 843 行）：单据页「为什么还没写」的原因（未连接 / 离线 / 不是你的 / 没有电脑能写）按同一个 actor 判，改后自动一致——兜底找到人后不再显示「未连接」。
- 定时 / 文件夹：ERP 没有单据的定时与文件夹录入（11-12 定 ERP 定时 / 文件夹不做），不涉及。

## 4. 上下游
- ERP 侧：单据 `owner`（开单人）不变，页面、权限、记录范围照旧记原发单人；只改写账件（Pearnly CC Job）的 `actor`。
- Express 里的录入人：采购 / 销售载荷里没有「录入人」字段（`express/contracts.document`），Express 记的是那台电脑上 CC 用的 Express 登录，和今天收款一样（收款载荷带 `user`=收款员，只作电脑侧记录）。改后保持这一套，不新增载荷字段，CC 桌面版不升载荷版本。
- 回执：照旧回到单据（`job_changed` 按单据），与 actor 无关。

## 5. 隔离与权限（不放松）
- `office_actor` 现有判据原样：只在 `books.reachers([这本账套])` 够得到这本账套的电脑里找；人必须 `devices.issuer_valid` 且这家公司在其 `companies.allowed_companies` 里。账套属于公司（按税号 + 分店），所以不跨公司、不跨账套。
- 领单端照旧：电脑只领 `actor` 在本机 `signed_in_users` 里的件（`cc_desktop/jobs.py` 领单 SQL、`route_contracts.serves`），领到时再核 `issuer_valid(actor)`，不合格转人工。
- 反例流程：另一家公司的电脑（够不到这本账套）不领。

## 6. 入口
网页（桌面 / 窄屏）、LINE、CC 对话直写、CC 代录都经 `push.send`，一处改动全部覆盖；单据页原因、对话进度行（「未连接提示」那一批做的 `computer_state`）同按 `office_actor` 的结果判，兜底找到人就不显示未连接。

## 7. ERP 代录要不要同时带 route_device（建议的一致规则）
- 现状：ERP 代录交单不带 `route_device`（`operator.submit` 收到 `computer` 没往下传）；Cowork 带。
- 建议：actor 一律 `office_actor(发单人…)`；任务有执行电脑（`computer`，对话开在某台 CC 桌面版上）且那台电脑服务这个 actor 时，带上 `route_device=computer`（指定这台写，同 Cowork）；否则不带，由服务 actor 的电脑领。这样「人在哪台电脑上开的代录就由那台写」与「没电脑的人由办公室电脑兜底」不冲突。带了 `route_device` 但那台不服务 actor 的不带（不靠指定绕过归属）。

## 8. 存量排队中的单
- 写账件的 actor 在排队那一刻定下（`jobs.enqueue`），改代码不会改已排的件：已经排着、actor = 没电脑的人的件，改后**不会**被自动领走，仍显示未连接；还没排件的（停下后每分钟 `poll` 重交的）下次重交时按新规则排，会被办公室电脑领走写进 Express。
- 要不要把已排的件改归兜底人：建议做一个一次性补丁（只改 status=queued、actor 名下没有电脑的件，按 `office_actor` 重算），部署后这些单会在办公室电脑开着时陆续写进 Express——**这是会真写账的动作，要不要做、要不要先告诉用户 / 客户，请定**。不做的话，人在单据上点「再写一次」也会按新规则重排。

## 9. 「未连接」提示
兜底也找不到人时 `office_actor` 回发单人本人 → `jobs.writers` 仍是 missing → 单据页照旧「CC 桌面版未连接」；对话进度行按「未连接提示」那批的 `computer_state` 同样显示。

## 10. 文案
不新增、不改文案（沿用 `express.cc_desktop_not_connected` 等已有词条）。

## 11. 测试
- 单测：`actor_of` 对采购 / 销售走 `office_actor`（本人有电脑归本人、没有归够得到账套的同公司员工、都没有归本人）；代录带 / 不带 `route_device` 的规则。
- 流程（新加一格）：B 只用手机、电脑只登录过 A → B 的销售单写账件归 A、被那台电脑领走（实验栈造回执写成）、ERP 单据 owner 仍是 B、单据页与对话不显示未连接；反例：另一家公司的电脑够不到这本账套，不领。
- 真机：测试机1 上 testerp 复验（主会话排）。

## signed_in_users 累计不删（只报不改）
- 现状：`cc_desktop/devices.py` 每次有人在某台电脑登录 CC 就把他加进这台的 `signed_in_users`（`contracts.with_user`），换配对、换人都不删。`serves()` 按这份名单判「这台写谁的活」；名单为空的老电脑过渡期谁的活都写。
- 风险：① 离职或调岗的人仍在名单里：直写领单时只看名单（领到后 `issuer_valid` 才拦，账号停用的转人工）；`office_actor` 已要求 `issuer_valid` + 公司可见，停用账号不会被选为兜底人，但没停用、只是不再用这台电脑的人仍会被当成「这台的人」。② 临时在别人电脑上登录过一次的人，此后他的单都能被那台领走（今天 H 组测试机1 领走 e2eerp 单很可能就是这个原因，未连库核实）。③ 兜底按名单选人，名单越杂，兜底落到谁越难预期。
- 建议后续另立一项：名单按最近登录时间老化或在电脑设置里可移除某人（要问用户口径），本需求不改。

## 12. 要定的点
| # | 问题 | 建议 | 不定的影响 |
|---|---|---|---|
| 1 | 出入库单 / 盘点调整是否一起改 | 一起改（同一个 `actor_of`，仓管多用手机） | 不改的话仓管手机建的出入库单仍排着显示未连接 |
| 2 | 存量排队件要不要补丁改归兜底人 | 做，部署前告诉用户会有一批旧单被写进 Express | 不做：旧单要人逐张点「再写一次」 |
| 3 | 代录 route_device 规则 | 按第 7 节 | — |

## 13. 工作量（机器时间）
写代码约 1 小时（`actor_of` + 代录 route_device + 可选补丁）；单测 + 流程约 1.5 小时；闸门、候选、并集流程约 1 小时。
