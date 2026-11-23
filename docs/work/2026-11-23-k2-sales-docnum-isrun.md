# 需求影响评估：K2 CC 写销货用票面号当单号，前缀不在 ISRUN 时挡住财务原生录单（Message 43）

> 证据：11-22 K1 窗口实录（`docs/work/2026-11-22-stock-purchase-sale-chain.md`「另发现」）、11-07 作废实录（`docs/work/2026-11-07-cc-stock-balance-native-评估.md:287`）、
> 11-23 69SINCER 只读统计（`~/pearnly-devtools/stock-k1-1123/sincer-k1.json`）、生产只读前缀统计（`~/pearnly-devtools/prod-ro-1122/out/k1-both.txt`）。

## 0. 需求原文与理解

- 来源：K1 测试中发现（不是用户提的需求，是缺陷）。11-22 测试用 ISRUN 里没有的前缀「K1-…」写了一张 CC 销货，之后财务在同一天原生存进货类单，
  Express 重算当天时弹 Message 43「ISRUN(K1) 找不到」，原生那张存不下去。
- 理解：CC 直写销货时单号 = 票面号（`companion/src/companion/dbf_sales.py::_sales_docnum`，09-30 定，为了 ภ.พ.30 连号核对）。Express 用单号前两位去 ISRUN 找单据簿；
  找不到时，凡是要回头重算这张单的原生动作就会弹 Message 43。票面号前缀与账套单据簿不一致的客户，财务当天原生录进货 / 库存类单会被挡住。
- 不在范围：采购（单号本来按 ISRUN 自编号）、退货 SR / 收款 RE / 库存单 / 调拨 / 收银日汇总（都按 ISRUN 自编号，已核 `dbf_refund` `dbf_receipt` `dbf_stock` `dbf_transfer`）；
  代录（Express 自己编号）；MR.ERP（另一套账本）。

## 1. 现有同类与复用

- `_sales_docnum` 已经有一条退回路径：票号超过 DOCNUM C(12) 或为空 → 退回自编号 `_next_docnum`，ISVAT.REFNUM 与 ARTRN.YOUREF 仍记真实票号（报表不受影响）。
- 结论：**扩展现有退回条件**——票号前两位不在该账套 ISRUN 的 PREFIX 里时，同样退回自编号，票面号照旧进 REFNUM / YOUREF。不新建机制。

## 2. Frappe 原生能力

- 不涉及：问题在 CC 桌面版写 Express DBF，服务端与 Desk 不改。

## 3. 按类通用

- 类别：「CC 直写 Express、单号不按 ISRUN 编的单」。全仓核过只有销货（IV / HS）一处；其余写入器都走 ISRUN 自编号。
- 同时加一条机器检查（单测）：所有写入器写出的 DOCNUM 前两位必须在账套 ISRUN 里，防以后新写入器再犯。

## 4. 上下游数据

- 现状数据（只读）：
  - 69SINCER（唯一用 CC 直写销货的真实客户，erpcowork）：推送 151 条、去重 127 张；账里现存 95 张，前缀全是 IV（ISRUN 有 IV）→ **现存 0 张中招**。
    SSIVA 开头的 31 张（SSIVA01001…SSIVA31001，10 月每天一张，前两位 SS 不在 ISRUN）和 IV/69/06751：线上推送记录都是「写成功」（Cowork Push Record status = success），
    账里按这些单号查不到。11-23 只读查清：财务（录入人 BIT5）11-18 把这批改成了自己的格式 `IV6910dd-001`（客户、物料、日期逐张对得上，
    这 32 个单号不在任何推送记录里）；IV/69/06751 同一天的单次日以 IV69/00752 推过并在账，最可能被财务删掉。不是写入丢失。
    它们在账期间若财务当天原生录进货类单，会中招；现状不中。
  - 另：推送清单里 24 张 IV 推了两次（推送不查重口径），账里现有 ARTRN 98 行对 95 张单。
  - 其他租户只有测试账套（IV / HS / JU / PI / OU / RL，都在 ISRUN）。
- Express 映射：退回自编号后 ARTRN.DOCNUM = 账套流水号（如 IV0000278），票面号在 ARTRN.YOUREF 与 ISVAT.REFNUM（ภ.พ.30 销项税报表按 REFNUM 打印）。查找、对账要按 YOUREF / REFNUM。
- CC 桌面版：载荷不变（不升载荷版本）；只改 CC 内部决定单号的逻辑，随 CC 版本发。ERP / Cowork 回执里显示的 Express 单号按实际写入的回传（现已如此）。
- Cowork / DMS：Cowork 识别的销项票是主要来源（票面号来自识别）；ERP 销售单号是 Pearnly 自编（IV…）一般在 ISRUN 里。DMS 不涉及。

## 5. 隔离与权限

- 不涉及：只改单个账套内的单号取法，ISRUN 只读，不跨账套。

## 6. 入口：网页、手机、LINE、拍照

- ERP 网页 / LINE / 拍照、Cowork 推送、CC 对话直写的销货最后都走 `dbf_sales.write_sale`，改一处全覆盖。代录不走这里。

## 7. 共用件与连带回归

- `check-impact.py --files companion/src/companion/dbf_sales.py` → 命中共用件 cc-dbf-write，9 个入口：cc.desktop.write、cc.desktop.operator、cowork.cc.channel、erp.cc.write、
  erp.cc.stock-entry、erp.cc.stock-void、erp.cc.pos-day、erp.cc.pos-refund、erp.cc.stock-transfer。实际只改销货单号分支，重点测 cc.desktop.write、erp.cc.write、cowork.cc.channel、erp.cc.pos-day（HS）。
- 冒烟：在 C 盘 T 账套离线回归里加一条「前缀不在 ISRUN 的票号 → 退回自编号、REFNUM / YOUREF = 票号」。

## 8. 存储与成本

- 不涉及：无新增数据、外部调用或月费。

## 9. 删除、作废、追溯

- 作废按原单号找（CC 存了实际写入的 DOCNUM），不受影响。追溯：票面号仍在 YOUREF / REFNUM。
- 11-07 实录作废时也弹过 Message 43（ISRUN(BA)），按确定后作废照常完成——说明作废只是提示、不挡；挡的是当天重算。
- 存量：69SINCER 现存 0 张中招，不需要修存量。

## 10. 文案与官方 UI

- 不涉及：无界面文案改动。

## 11. 测试与验收

- 先原生核实（测试机1、70EXP\test，写前整本快照、测完还原）三件事，再定写法：
  1. Express 自己录一张 IV，手改单号成 ISRUN 没有的前缀（如「SS…」）能不能存；存了以后当天原生录 RR，会不会同样弹 Message 43 —— 判断这是不是 Express 原生也会犯的问题。
  2. ISRUN 查找是只看前两位 PREFIX，还是 PREFIX + DOCTYP 都要对（例：HS 现销用 IV 开头的票号、ISRUN 里 IV 属于 DOCTYP IV）。
  3. 除当天重算、作废外，财务改单 / 打印 / 月结重算是否也触发。
- 单测：前缀在 / 不在 ISRUN、PREFIX 一位（如「R」）、票号小写、超宽四种；所有写入器 DOCNUM 前缀在 ISRUN 的机器检查。
- 离线回放：C 盘 T 账套跑销货回归，比对新旧（只许前缀不在 ISRUN 的那几张单号变）。
- 真机：Cowork 推一张 SS 开头票号的销货进 70EXP\test → 写成自编号、REFNUM = 票号；当天原生录 RR 能存。

## 12. 要问用户 / 客户的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 票面号前缀不是账套单据簿前缀时，Express 单号改用账套流水号（票号留在参考号 / 税表），可以吗？ | 可以。跟票号太长时已有的做法一样；ภ.พ.30 按参考号打印票号不变 | 不改的话，这类客户财务当天原生录进货会被 Express 挡住存不了 |
| 2 | 若第 11 节第 1 条证实 Express 原生手改单号也一样会挡，仍按建议改吗？（「Express 不拦我们不拦」口径） | 仍改：原生是录入时人自己选的单号，CC 写的是客户看不到的后台单号，挡住财务录单是我们造成的 | 不改则保留现状风险 |

## 13. 工作量与排期

1. 原生核实三件事（测试机1，约 40 分钟机器时间，办公室服务器关机前）。
2. 改 `_sales_docnum` + 单测 + 写入器前缀检查（约 30 分钟）。
3. C 盘 T 账套离线回放 + 测试机真机一张（约 40 分钟）。
4. 随下一个 CC 版本（1.12.24 之后，或主会话定同车）。依赖：无；排在 K1 合入之后，避免同改 dbf_sales 冲突。

## 14. 用户决定与施工（11-23）

- 用户原话「k2 按建议改」（主会话转达，11-23）。口径以账本证据为准（`2026-11-23-k2-账本证据-大白话` 摘要）：
  - Express 销货单号格前缀锁死为单据簿前缀（70EXP\test 原生实录：「IV」删不掉，打 SS2311001 得 IVSS2311001），财务把纸面发票号打在后面。
  - 69SINCER 3,218 张前两位全是 IV，REFNUM = 单号 2,935 张；抽查 32 本真账财务录的单前两位 100% 在 ISRUN。
  - 多本销货簿的账套（NLTTEST IV/IO/IE/IT/IG，T9WNF 75 本）不同前缀对应不同发票本。
- 写法（`companion/src/companion/dbf_sales.py`，赊销 IV、现销 HS 同一处）：
  - 票面号前两位等于这个单别某本两位前缀 ISRUN 簿的前缀（区分大小写）→ 单号 = 票面号，日记账 / 默认部门 / 计数器用那本簿（计数器照旧加一）。
    一位前缀的簿（T7/T8/T9WNF 有 S、R、E …）不认：Express 按前两位找 ISRUN（Message 43「ISRUN(K1)」），一位前缀它怎么找没实证；
    101 本回放首轮里 SSIVA02001 在 WNF 三本对上了 S 簿而保留成单号，改成照原生由 IV 簿往下编。
  - 不以任何一本开头、超 DOCNUM C(12) 或为空 → 照原生留空单号：默认簿（前缀与单别同名，没有就第一本）的前缀 + ISRUN 下一个号（号用过就往后跳，含软删行），写完 ISRUN 记成下一个号；票面号照旧进 ARTRN.YOUREF 与 ISVAT.REFNUM。
  - 账套没有这个单别的簿（合成账套）→ 照旧用票面号；簿没有能用的流水号 → 退回 前缀 + 佛历年月日 + 当日序号。
  - 收银日汇总（POS）单号照旧（HS + 佛历年月日 + 序号，前缀在 ISRUN）。回退开关 PEARNLY_SALES_DOCNUM=seq 不变。
  - 与评估第 1 节的差别：原评估说「退回自编号」，按用户认可的口径改成照原生由簿往下编；票号超宽的退回路径也统一成同一编法（原来是当日序号）。
- 按类查同类写入器：采购 `dbf_writer`、退货 `dbf_refund`（单别 + 日期）、收款 `dbf_receipt`、库存单 `dbf_stock`、调拨 `dbf_transfer`（ISRUN 流水号）都不拿外部号当单号。
  另发现 `stock_sale.set_opening_stock`（补期初卡）在 STCRD 写 `OB yymmdd-nnn` 期初行，OB 多半不是 ISRUN 簿前缀，属同类风险；它只有明细没有单头，改法另评估，本次不动（已报主会话）。
- 测试：
  - 单测 `companion/tests/test_dbf_writer_sales.py` 新增 8 个 `test_k2_*` 用例（含 7 种票号参数化，共 15 条）：前缀对上、对不上、号已用跳号、带年份段流水号、多本簿、一位前缀的簿不算、别的单别的簿不算、小写 / 超宽、所有单号都以销货簿前缀开头的机器检查。
  - 结果：见下方「验证记录」。

## 验证记录

| 层 | 内容 | 结果 |
| --- | --- | --- |
| 单测 | `test_dbf_writer_sales.py -k k2` 14 条 | 通过 |
| companion 全套 | `pytest tests` | 待填 |
| 离线回放 | C 盘 T 账套销货回归（含 NLTTEST / T9WNF 多簿、69SINCER 样式票号） | 待填 |
| 真机 | 69EXP\TEST：Cowork / CC 写一张 SS 前缀销货 → 原生同日录 RR，不弹 Message 43 | 待填 |

影响清单（`check-impact.py --files companion/src/companion/dbf_sales.py`，共用件 cc-dbf-write）：

| 入口 | 结果 |
| --- | --- |
| cc.desktop.write | 待填 |
| erp.cc.write | 待填 |
| cowork.cc.channel | 待填 |
| erp.cc.pos-day | 单号路径未改（POS 照旧）；companion 全套覆盖 |
| cc.desktop.operator、erp.cc.stock-entry、erp.cc.stock-void、erp.cc.pos-refund、erp.cc.stock-transfer | 不走销货单号路径，没测到（不受影响）；companion 全套覆盖 |
