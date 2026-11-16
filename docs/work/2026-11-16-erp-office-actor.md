# ERP 写账人改走 office_actor（2026-11-16）

分支 `pearnly/erp-office-actor`，叠在 `pearnly/erp-operator-intake`（H3 与「未连接一态」）之上：本改动让「未连接」的判断随写账人一起走 `office_actor`，
两条要按顺序合入。评估：[2026-11-16-erp-office-actor-评估.md](2026-11-16-erp-office-actor-评估.md)（主会话审定「可以做」，用户原话「按建议」）。

## 改了什么

- `express/push.actor_of`：采购 / 销售 / 出入库 / 盘点 / 收银日汇总一律 `office_actor(开单人, 公司, 账套)`——开单人自己有登录过 CC 的电脑（在线或关着）
  归开单人；没有就归这家公司里在够得到这本账套的电脑上登录过 CC、`issuer_valid`、这家公司在其可见公司里的员工（在线电脑优先）；一个都没有仍归开单人。
  收款原本就是这条（`collection/push.py`）。ERP 单据 `owner` 不变，Express 载荷不变（采购 / 销售载荷没有录入人字段）。
- 单据页原因（`push._queued_note`）本来就按 `actor_of` 判，随之一致；代录对话那句（`operator.computer_state`）改成按同一个 `office_actor` 判：兜底找得到人就不说「未连接」。
- 代录 `route_device`（评估第 7 节的规则）：现在写入计划开始（`express_operator.api.hand_in`）不带执行电脑，`computer` 恒为空，按规则不带 `route_device`；
  以后任务带上执行电脑时再按「那台服务这个 actor 才带」接上。本次不改代码。
- 只读统计脚本 `scripts/production/count-erp-queued-no-computer.py`（单测 `test-count-erp-queued-no-computer.py`，已登记进 Development checks）。

## 部署说明（必须写进发布记录）

- **上线后行为：还没排写账件、靠每分钟 `push.poll` 重交的单，会按新规则交给办公室电脑写进 Express。**
- 已排队、写账件归的人名下没有电脑的件不会自动改归（写账件的 actor 排队时定下）；不做补丁。部署前主会话在各 ERP 站点只读跑一次：

      env/bin/python /opt/pearnly/deploy-src/scripts/production/count-erp-queued-no-computer.py --site <站点>

  输出 `{total, by_doctype: {类型: {count, earliest}}, would_move}`，把数字告诉用户再定。

## 证据（本机实验栈 pearnly-lab-co8，镜像 = 线上 02eebaa4）

- 流程 `erp.office-actor`（B = sales@ 只用手机；A = admin@ 的电脑够得到这本账套；C = purchase@ 的电脑只报另一家公司的账套）：
  - 修前红（push.py / operator.py 退回 H3 分支版本）：写账件归 B、A 的电脑排头里没有它。
  - 修后绿：写账件归 A、A 的电脑排头里有它、C 的电脑没有也够不到这本账套；单据页原因与对话那句都不是「未连接」；照电脑回执后单据 written、owner 仍是 B。
  - 结果文件在主会话共享笔记「office_actor-流程证据-11-16」。
- 单测：`tests/test_erp_express_direct.py`（PhoneOnlySeller：四种单据归 A、A 不在这家可见公司时不给、够不到账套的电脑不算、B 自己有电脑照旧归 B；PosDayActor 的出入库断言按新口径改）。

## signed_in_users 累计不删（只报不改）

见评估文末：每次有人在某台电脑登录 CC 就记进那台的 `signed_in_users`，换配对 / 换人都不删；离职未停用、临时登录过一次的人会一直被当成「这台的人」，兜底选人也按这份名单。建议另立一项（老化或在电脑设置里可移除），要问用户口径。
