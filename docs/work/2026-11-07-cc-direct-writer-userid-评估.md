# 评估：CC 直写的录入人记 Express 账户名（11-07）

> 用户原话：「录入人应该记 exp 的账户人吧」。主会话排在结存修复（[评估](2026-11-07-cc-stock-balance-native-评估.md)）之后、进货总账之前，版本跟 1.12.16。只评估，审定后再改。

## 现状与原生

- CC 直写的单一律记 `PEARNLY`（`dbf_schema.PEARNLY_USERID`，10-08 起，之前记 BIT9）：单头 `APTRN / ARTRN / STTRN.USERID`、凭证 `GLJNL.CREBY`、
  CC 新建的供应商 / 客户 / 物料主档 `CREBY / USERID`。代录走 Express 界面，录入人是 Express 自己按登录名记的。
- 原生（S 盘 95 本只读，120 万张进销单）：录入人 **100% 大写**，长度 1–8（栏宽 `C(8)`），74 个不同的人（NLT、BIT9、JJT、BIT5、NAPAT…）；
  凭证 `CREBY` 与单头 `USERID` 相同的 67%，不同的 33%（凭证可以是别人过账 / 改过）。各年度 `secure\SCUSER.DBF` 是 Express 用户表。

## 取哪个名字

- 来源：这台电脑、这个 Windows 用户在 CC「授权登录」存的 Express 账号（`express_operator/credentials.py`，目标名 `Pearnly CC/Express/default`）。
  **一个 Windows 用户只存一份，不分连接**；代录也用这一份登录，所以同一台电脑上**直写和代录的录入人是同一个人**。
  只读用户名（凭据的 UserName），密码不读出来（加一个只回用户名的函数，不经过 `read`），不写日志、不上报。
- 规整：去空白、转大写。**超过 8 个字符、含 cp874 写不出的字符、或为空 → 照旧记 `PEARNLY`**，不截断（截断后可能变成另一个真人的名字），记一条 warning。
- 建议再核一道：名字不在这本账套所在年度的 `secure\SCUSER.DBF` 用户表里（打错字、别家事务所的账号）也记 `PEARNLY`；读不到用户表时照用这个名字。
- 没授权过、读凭据失败 → `PEARNLY`，不拦单。

## 要一起处理的：CC 靠 `PEARNLY` 认自己写的单

| 位置 | 用途 | 改成 Express 账户名后 |
| --- | --- | --- |
| `account_history._pearnly`（APTRN.USERID / GLJNL.CREBY） | 读「供应商常用科目」时去掉 CC 写的单 | CC 写的单会被当成人录的，**自己学自己**（写错一次就越学越错） |
| `service_vat`（APTRN.USERID） | 服务品进项税口径按历史定时去掉 CC 写的 | 同上 |
| `product_master.shape_gaps`（STMAS.CREBY） | 只补「CC 建的」非库存品缺的形状字段 | 认不出 CC 建的档，就不补了（或误补人家建的） |
| `express_operator/masters.py`、`scripts/da-replay.py` | 代录建主档、回放统计 | 同类 |

三种做法：

1. **凭证 `GLJNL.CREBY` 继续记 `PEARNLY`，单头 `USERID` 和主档建档人记 Express 账户名**（建议）。Express 界面上单据的录入人看单头，显示的就是 BIT5；
   原生凭证 CREBY 与单头不同本来就占 33%，不算异样。认 CC 写的单改看凭证 CREBY（`account_history` 本来就读 GLJNL；`service_vat` 要多读一张 GLJNL）。
   主档没有凭证，`shape_gaps` 改成只补「本机任务日志里记过是 CC 建的码」或干脆按「形状缺了就补」，审定时定。
2. 全部记 Express 账户名，认 CC 写的单改用服务端：服务端知道自己推过哪些单号（Cowork 推送记录、ERP 的 CC 写账件），读历史时按单号去掉。
   要改服务端和 CC 两边，读历史的口径分到两处，工作量大。
3. 只靠本机任务日志（`task_journal` 记着写过的单号）去掉：别的电脑写的去不掉。不建议。

## 其他问题

- CC 设置里显示「直写录入人：BIT5」：建议在已有的「授权登录」那一行把已授权的账号名显示出来（只读），没授权时不显示；新增文案 1 条、过 `check-copy`。
  不加说明文字。
- 服务端要不要知道：写账回执的 meta 带上这张单实际记的录入人（`writer`），服务端存进推送记录 / 写账件，出问题时查得到是谁的名义写的；
  心跳不用报（用户名属于这台电脑的这个 Windows 用户，服务端用不上）。回执多一个键要服务端白名单收下，服务端可以晚一批，旧服务端忽略即可。
- 不影响：Express 打开账套不按单据上的录入人核用户表（10-08 核过），记谁都照常打开。

## 测试

- 单元：假凭据（只给用户名）——`bit5` → 单头 / 主档记 `BIT5`、凭证 CREBY 记 `PEARNLY`（按做法 1）；9 个字符、泰文、空、读失败 → 全记 `PEARNLY`；
  不在用户表 → `PEARNLY`；密码不进日志（日志里搜不到）。`account_history`、`service_vat` 仍能去掉 CC 写的单（夹具单头记 BIT5）。还原修复必失败。
- 离线回归：`express-stock-balance-regression.py` 同一套 T 账套副本，新写法只许 USERID / CREBY 栏不同。
- TEST：测试机 skin3 已授权（代录用的那份）→ 直写一张到 TEST，Express 里打开单据看录入人是那个账号。

## 要主会话定

| # | 问题 | 建议 |
| --- | --- | --- |
| 1 | 认 CC 写的单用哪种做法 | 做法 1（凭证 CREBY 留 `PEARNLY`） |
| 2 | 不在 Express 用户表里的名字记不记 | 不记，退回 `PEARNLY` |
| 3 | 设置里显示已授权账号名 | 显示，1 条新文案 |
| 4 | 回执带录入人给服务端 | 带，服务端白名单可晚一批 |

工作量（机器耗时）：写代码约 1 小时、CC 全量测试 10 分钟、回归与 TEST 约 30 分钟。

## 主会话审定（11-07）

四条全按建议：① 单头 USERID 与主档建档人记 Express 账户名，凭证 `GLJNL.CREBY` 继续记 `PEARNLY` 当机器标记；4 处认自己单的地方统一改成按 CREBY 标记认
（测试：录入人改成真人名后，CC 不会把自己写的单当人录的来学）；② 不在这本账套 `SCUSER` 里的照旧记 `PEARNLY`；③ CC 设置「授权登录」那一行显示已授权账号名，
新文案过 `check-copy`；④ 写账回执带上实际记的录入人，服务端白名单晚一批收、心跳不报。结存修复合入后再动代码，跟 1.12.17 发，合入前发主会话。

## 施工记录（分支 `pearnly/cc-writer-userid`，基线 main 5b73b90a8）

- `writer_id.resolve(账套)`：读 `credentials.user_name("Pearnly CC/Express/default")`（新函数，只回用户名、不读密码）→ 去空白、转大写 → 空 / 超过 8 个字符 /
  cp874 写不出 / 不在账套所在年度 `secure\SCUSER.DBF`（往上找 4 层，只读 RECTYP U 的 RECCOD）→ `PEARNLY`；读凭据出错也是 `PEARNLY`。
  worker 写账入口统一算一次传给各写入函数（进货、销货含期初与收银日汇总、库存单、调拨、作废、退货），代录建主档（`express_operator/masters.py`）同样用它。
- 凭证头 `GLJNL.CREBY` 在 5 处（进货、销货、库存单、退货、收银转账凭证）一律记 `PEARNLY`（`dbf_schema.is_pearnly` 认）；库存单凭证头的 `USERID` 记录入人。
- 认 CC 写的单：`writer_id.cc_vouchers(账套)` 扫凭证头取机器标记的凭证号；`service_vat` 读历史时去掉这些单（旧版单头 PEARNLY 的照旧去掉）；
  `product_master.backfill_account_items` 认「CC 写过的单」改成「单头 PEARNLY 或凭证头机器标记」；`account_history` 本来就看凭证头 CREBY，不用改；
  `product_master.shape_gaps` 只补旧版 CC 建的档（CREBY = PEARNLY；新建的档形状本来就齐），不改。
- 回执 meta 带 `writer`（成功时）；服务端 `sanitize_push_meta` 只收白名单键，旧服务端忽略，服务端收存晚一批。
- 桥 `operator_state` 多回 `login_user`（已授权账户名）；页面显示放哪里待主会话定（设置页没有 Express 授权那一行）。
- 测试：新增 `tests/test_writer_id.py` 12 条 + `test_account_history.py` 1 条；业务代码换回 main（保留新模块）后 4 条失败。
