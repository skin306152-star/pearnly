# 需求影响评估：ERP 领单时判定写账人（与 Cowork 同一条规则）

> 状态：待主会话审定。依据 `origin/pearnly/cowork-office-actor` 15054aaa1（备用 A，基于 main 1a91b178b）只读盘点。
> 本分支 `pearnly/erp-writer-at-claim` 叠在它上面：共用判据只用它那一份，不另写。

## 0. 需求原文与理解

主会话 11-19：「领单时判定写账人：两产品共用同一份纯判据，与备用 A 的 pearnly/cowork-office-actor 对齐只留一份；管钱点：两台电脑同时抢同一件只能写一次，要有并发流程；旧件闸照 11-17 定稿：没配置 = 新规则全部生效，存量站点由原生 patch 写入 pearnly_writer_rule_since；生产 mrerp 站点已有真实电脑 MRERP03，统计和流程要算进去。」

规则定稿（11-17，两产品同一条）：公司里只要有一台开着 CC、够得到那本账套、登录人有权限的电脑，任何成员提交的单就能写。提交人自己的电脑在线用自己的；否则在线、够得到、有权限的那台替他写；都不在线就排队，谁先上线谁写。在电脑来领单那一刻判，不在排队时写死。

## 1. 现有同类与复用

- ERP 现在（11-16 `push.office_actor`）在**排队时**把写账件的 `actor` 换成办公室同事，规则不同（本人有电脑——哪怕关着——就只等本人）。本次改成领单时判，`office_actor` 删除。
- 共用判据全部复用备用 A：`route_contracts.decide(..., stand_in=)`、`pick_user`、`owner_online_elsewhere`、`stand_in_open`、`db_time`、`writer_status`、`writer_rule.stamp_since`。

## 2. Frappe 原生能力

- 旧件闸用原生迁移补丁（ERP `patches.txt` post_model_sync）调 `writer_rule.stamp_since()`，幂等。
- 新字段走 DocType JSON（Pearnly CC Job 加 `writer`，只读、只作记录）。

## 3. 按类通用

一个落点管所有 ERP 写账件：采购、销售、出入库、盘点、收银日汇总 / 退货日汇总、收款（`collection.push`）、CC 直写与代录——都经 `cc_desktop.jobs` 领单，都走同一个 `_heads` / `_claim`。不分单据类型。

## 4. 上下游数据

| 落点 | 改动 |
|---|---|
| `express/push.py` `actor_of` | 回开单人（不再换人）；`office_actor` 删 |
| `collection/push.py` | 作业 actor = 收款员本人 |
| `cc_desktop/jobs.py` `_heads` | 这台有能替写的人时，看「自己的 + 切换时刻之后排的别人的」（`db_time(since)`），定时任务（`route_device`）照旧只归那台 |
| `cc_desktop/jobs.py` `_claim` | `pick_user(本机登录人, 此刻能用这家公司且签发有效)` → `decide(stand_in=)`；再核 `stand_in_open(creation, since)`；领到时记 `writer` |
| `cc_desktop/jobs.py` `writers` | 按提交人看：他的或能替他写的电脑有在线 → ok；都关着 → offline；不再回 `not_yours`；状态名换成 `writer_status` |
| `express/push.py` `_queued_note`、`operator.computer_state`、`express/target.py`、`cc_tools` | 跟着 `writers` 走，不另判 |
| ERP 资格（谁能替写） | 产品钩子：`devices.issuer_valid(user)` 且这家公司在 `companies.allowed_companies(user)` 里（同 11-16 `office_actor` 的判据） |

Express：写进哪本账套不变（写账件冻结的 `book_key`）；Express 里的录入人照载荷，不是替写的人。

## 5. 隔离与权限（管钱 / 隔离单列）

1. **两台电脑同时抢同一件只能写一次**：领单 `get_doc(..., for_update=True)` + `status == queued` 复核 + 同账套写者唯一键（`writing_key`）已有；替写后多了「提交人自己的那台 + 同事的那台」同时来领的组合。加并发流程：两台各自连库、同时领同一件，断言只有一台领到、`fence` 只加一次、Express 只写一次。
2. 替写的登录人必须此刻对这家公司有权限、签发有效；别的公司的电脑（够不到这本账套）一律不替。
3. 旧件闸：切换前排着的别人的件，替写电脑看不到（排头查询就筛掉），不会挡住这本账套后面的新件。

## 6. 入口

网页单据页、手机、LINE 收款、CC 直写 / 代录、定时任务：全部经写账件，同一处判。界面只换状态来源（`writers`），不加字。

## 7. 共用件与连带回归

共用件 cc-desktop（`jobs.py`、`route_contracts`）。按 `check-impact.py` 出影响清单逐入口实测；Cowork 那半由备用 A 的分支自带。现有流程 `erp.office-actor`（11-16 的排队时换人）改成领单时判的断言。

## 8. 存储与成本

一个字段；零新增常驻花费。

## 9. 删除、作废、追溯

作业上的 `actor` 始终是提交人；`writer` 记实际替写的人（排查用，不显示）。作废、撤件路径不变。

## 10. 文案

不加新文案。`express.cc_desktop_not_yours`（你的电脑离线）按新规则不再出现，词条与旧分支留到下一批清理（同备用 A 的处理）。

## 11. 测试与验收

- 单测：`_heads` 按 since 筛、`_claim` 替写 / 不替（本人另一台在线、定时任务、无权限、旧件）、`writers` 新状态、`actor_of` 回本人。
- 流程：`erp.writer-at-claim`（新）：本人无电脑 → 同事那台领到；本人电脑在线 → 同事那台不领；切换前的旧件 → 同事不领、后面新件照领；两台同时领同一件 → 只领到一次。
- 上线前只读统计（按站点）：排着的件张数、最老排队时刻、放开旧件闸后会被别人写的张数；**生产 mrerp 站点有真实电脑 MRERP03，统计按它的登录人与够得到的账套算**。执行要连生产只读，先问主会话。

## 12. 要问的点

1. 「添加人那台排前」：ERP 里成员由谁添加没有现成字段（`inviter_of` 还没有）。建议先按用户名排（同 Cowork 现状），不加字段；要做再单独排。
2. 生产统计只读脚本由谁、何时在生产跑（需要你点头）。

## 13. 工作量

写代码约 1.5 小时；单测 + 新流程约 1 小时；闸门 + 候选离线 + 并集流程机器约 50 分钟。依赖备用 A 分支先进 main（或同车，我叠在它上面）。

## 施工结果（主会话 11-19 审定后）

- 添加人排前：取 Frappe 原生 `User.owner`（谁建的这个提交人），取不到按用户名（`jobs._helper` → `route_contracts.pick_user`）；不加字段。
- 替写时领单那道「提交人签发有效」改为「替写人有权限（钩子已核）+ 提交人账号没停用」：收银日汇总的建单人是系统、收款员不登记电脑，原来的检查会把这些件转人工。
- 流程 `erp.writer-at-claim` 首跑抓到：两台同时抢同一件时，抢输那台加锁读撞 MariaDB 1020，库把整笔领单事务回滚（保存点也没了），领单报错。改为回滚后空手返回、电脑下一轮再来；同一流程复跑通过（只领到一次、fence 只加一次、记下替写人）。
- 写账件 `creation` 存站点时区，旧件闸按 UTC 比（`jobs._open` / `_since`）；单测按 Asia/Bangkok 守住边界。
- 只读统计脚本在 `~/pearnly-devtools/prod-ro-1119/`，交主会话合并到部署前那一次连接里跑。

## 部署说明（线上能看到的行为变化，主会话 11-19 认可）

1. 写账件一律归提交人本人；谁的电脑写在电脑来领单那一刻判：提交人自己的电脑在线用自己的，否则公司里在线、够得到这本账套、登录人对这家公司有权限的电脑替他写，写账件上记下替写人（Writer，只读，只在 Desk 里看得到）。11-16 起排队时就换成办公室同事的写法作废。
2. 替写时领单的检查是「替写人有权限 + 提交人账号没停用」（不再要求提交人自己能连 CC）：收银日汇总（系统建）、不登记电脑的收款员与销售员的单由办公室电脑写，不再转人工。权限底线不变：替写人必须签发有效、对这家公司有权限。
3. 两台电脑同时来领同一件：只有一台领到；另一台这一轮空手（不报错），下一轮再来。
4. 旧件闸：存量站点迁移时补丁写上 `pearnly_writer_rule_since`，切换前排着的件只等提交人自己的电脑；数字由主会话部署前只读统计后问用户，定了「旧件也写」就删掉这项配置。新开户站点没有这项配置，全部按新规则。
5. 单据页、代录那句、CC 的状态把能替写的电脑算进来：同事那台在线时不再显示「CC 桌面版未连接」或「您的电脑离线」。
