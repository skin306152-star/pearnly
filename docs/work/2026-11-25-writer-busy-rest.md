# 等账套后不歇、约 1.5 秒重领（11-25 两台电脑实测发现）

分支 `pearnly/writer-busy-rest`，基线 origin/main f18fa1ec8。主会话 11-25 定性为缺陷、批准修。

## 现象（实验栈 testerp + 测试机1/2，69EXP\TEST）
测试机2 卡在写这本账套（锁着 `PEARNLY.LCK`）时，测试机1 领到同一本的另一件：回「另一台电脑正在写这本账套」（`[ACCOUNT_WRITER_BUSY]`，
waiting_lock），45 秒内重领 31 次（fence 0→31，最后 49），每次领单 + 回执各一个请求，ERP 每次还回写一次单据账本栏。

## 原因
- CC 桌面版写账循环（`companion/multi_profile_worker.run_forever`）一轮里处理过一件就不睡、立刻再领。
- 服务端回执（共用 `cc_desktop/wire/ack_rules.acknowledgement`）只给「Express 开着」（`ACCOUNT_BUSY_LOCKED`）留原因、让这本账套歇 30 秒；
  别的等账套原因清空、马上能再派 → 同一件 1.5 秒一圈。
- 同类的还有「这台暂时够不到这本」（`ACCOUNT_SET_UNREACHABLE`）。

## 修法（按类，一个入口）
- `ack_rules.REST_CODES = {ACCOUNT_BUSY_LOCKED, ACCOUNT_WRITER_BUSY, ACCOUNT_SET_UNREACHABLE}`（「这本账套此刻写不了、一个字节没动」）；
  回执留原因码，`ack_rules.rests()` / `resting()` 判歇；两条通道都用它：共用写账件 `jobs._heads` / `jobs.acknowledge`（ERP 等产品），
  Cowork `companion/transport` 原本就调 `ack_rules.resting`，自动跟上。歇的秒数沿用 `EXPRESS_OPEN_REST_SECONDS`（30 秒）。
- 不在其列（有意）：`ERR_ACCOUNT_UID_MISMATCH`（那台电脑的可达行标 stale、单子马上交给别的电脑，CC 端另有冷却）；代录的
  `OPERATOR_*` 等待（代录自己的节奏，10-30 用户定不改「等电脑空闲」）。
- 「在等 Express 关闭」的显示只认 `ACCOUNT_BUSY_LOCKED`（`express_open` / `express_wait` 不变）：别的电脑在写、够不到不显示成 Express 开着。
- 不改 CC 桌面版：歇在服务端，对所有已装版本生效，不等 1.12.25。

## fence 虚增的副作用（核对结论）
- 共用写账件：`fence` 只是领取计数，不下发给电脑，不参与「3 次转人工」（看 `attempt`，等账套不计次数）也不参与「领后失联」（看租约到期 +
  电脑 180 秒没报到）。所以没有判定上的副作用，只是白白多出的请求与写库。
- Cowork：每次领取新建一条尝试记录（attempt + fence 令牌），虚增 = 每圈多一行尝试记录与一次写库；回执按尝试令牌认，不影响转人工与结果不明判定。

## 可见变化
- 别的电脑在写 / 够不到时，排队中的写账件原因不再是空，而是这两个码。ERP 单据排队中时账本原因栏照旧写空，收款记录排队中不抄原因，所以 ERP 网页、收款、LINE 不变。
  Cowork 推送记录排队中带这两个码时，导出与 CC 工具里会用现成词条显示「另一台电脑正在写入此账套，完成后将自动推送」/「暂时无法访问此账套，连接后将自动推送」
  （同 Express 开着已有的做法，不新增、不改文案）。
- 同一本账套的其他电脑最多晚 30 秒接上（够不到是某一台的事，整本歇 30 秒；以前那台会一直空转着占着这件）。

## 测试（离线）
- `tests/test_cc_desktop_channel.py`：别的电脑在写 / 够不到 → 留原因、歇着时来领 30 次都不派、fence 不涨、不说成 Express 开着、歇够马上接上 fence +1；
  文件号对不上与代录等待不歇；原「其它等待马上再派」改用代录等待验；原「等账套不计次数」那条先歇够再重试。
- `tests/test_cowork_express_dispatch.py`：Cowork 领单同样歇、歇够照派。
- `tests/test_cowork_push_lifecycle.py`：规则本身（哪些码留原因、哪些不留，歇的时长）。

## 共用件连带回归（`scripts/check-impact.py`：cc-desktop 15 个入口）
| 入口 | 结果 |
|---|---|
| erp.cc.write、erp.cc.chat-push、erp.collection.server、erp.line.collection、erp.mrerp.direct、cc.operator.page（离线） | 通过：受影响离线测试全过（test_cc_desktop_channel、test_erp_express_direct、test_cowork_* 回执与派单、test_companion_contract） |
| erp.cc.connect、erp.cc.masters、erp.cc.stock-void、cowork.cc.channel、erp.cc.companies、cc.mrerp-login、cc.desktop.bridge、cowork.cc-chat.push、cc.desktop.cloud-express（人工） | 没测到：主会话 11-25 定本次不上测试机；改动只在「等账套」回执之后是否歇，不碰连接、主档、作废、MR.ERP 登录、桥与云端；实机待合入后随批验收 |
