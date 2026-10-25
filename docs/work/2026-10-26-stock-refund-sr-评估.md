# 需求影响评估：收银跨天退货写成 Express 退货单 SR（出入库第一期 B 段 ③ / B3b）

> 按 [需求影响评估模板](../development/templates/impact-assessment.md) 填写，交主会话审定后再施工。基线 main `d8d42e6f`。
> 设计来源：`docs/work/2026-10-25-stock-b-remaining-design.md` 第 3 节（主会话 10-25 同意 B 段顺序），实录：计划文档九.7c、
> 「现销退货、当场退现金的写法」（服务器测试账套第 5 条 SR0000001）。

## 0. 需求原文与理解

- 原话：主会话 10-26「B3b（退货 SR）排在作废之后马上做，做好后这些待写的自动补写」「跨天退货停在 ERP，标『待写退货』：要让人看得到、
  不会被忘掉。在日汇总页和公司页 Express 区列出数量，库存对齐时按 ERP 独有的单照算，不被抹掉」；10-26 晚「等待期间按设计顺序继续做
  B 段下一步（③ 退货 SR / B3b），只做不依赖这几条实录结论的部分」。
- 理解：接了 Express 的公司，收银台**跨天**退货（退货小票 `sale_type = refund`、原小票在更早一天、原小票所在日汇总 HS 已写进 Express）
  现在停在 ERP。B3b 把它们写成 Express 原生的退货单 SR（退现金 CNTYP 3、货回仓 RETSTK Y），挂原小票所在的那张 HS。
- 不在范围：同一天的退货（已在 B3a 冲进当天 HS，主会话 10-25 定）；销售单（ERP Sales Document）退货、采购退货 GR（第二期）；
  「只减价、不回仓」与「留着抵下次收款」（CNTYP 2，待测，ERP 收银没有这两种退法）；改 Cowork。

## 1. 现有同类与复用

- 同类：B3a 收银日汇总 HS（`express/pos_day.py` 合单、`push.py` 写账队列、`ERP POS Day Summary` DocType、`cashier/vat.py` 逐行税额、
  共用写账规则 `express_rules` 的 POS 块与付款方式转账凭证）；CC 写现销 `companion/dbf_sales.py`（HS / IV，含 POS 日汇总、STLOC、ISVAT）；
  出入库单作废的「挂原单」写法（`dbf_void.py` 按单号认原单）。
- 结论：**扩展现有**。ERP 侧在 `pos_day` 旁边加「退货日汇总」（复用同一个 DocType 加单别栏，或新 DocType，见第 12 节第 1 问）；
  共用规则加 SR 映射；CC 在 `dbf_sales.py` 加 RECTYP 5 分支，复用 HS 的明细 / STLOC / ISVAT / 转账凭证写法。不新建平行模块。

## 2. Frappe 原生能力

- 不涉及新界面组件：退货日汇总沿用 B3a 的后台 DocType + 小票页 Express 列 + 单据 Express 弹窗；公司页 Express 区加一行计数用原生 FieldGroup 只读字段。
- 结论：只用原生 DocType / 字段 / 已有弹窗，不加自定义样式。

## 3. 按类通用

- 类别：「收银退货写进 Express」。覆盖：跨天退货（原单 HS 已写）。
- 一并处理：B3a 里「晚到的同日退货单独成一张补单 → 冲成负数停在人工」的情形，改成同样写 SR（它本质也是原单 HS 已写之后才来的退货）——
  这是对 B3a 行为的调整，列第 12 节第 2 问。
- 不覆盖：原小票在「收银写 Express 起始日」之前（原单没写进 Express，不写 SR，照旧只在 ERP，库存对齐照算）；原单所在 HS 还没写成
  （排队 / 停在人工）时退货先等，HS 写成后再写。

## 4. 上下游数据

- ERP 单据流：退货小票（`cashier/sales.refund`）→ 每晚合成「退货日汇总」（一家公司一天、按原 HS 分张：SR 的 SONUM 只能挂一张原单）→
  写账队列 → CC 写 SR → 回执记在退货日汇总与小票上（同 HS）。库存流水不变（退货小票已入库）；库存对齐 `masters._pending`：已写进 SR 的
  退货小票流水不算 ERP 独有（同 HS 里的销售流水）。
- Express 映射（照第 5 条实录）：ARTRN RECTYP 5、CNTYP 3、SONUM = 原 HS 单号、IVCAMT = 原 HS 金额、RCVAMT = 退款额、CMPLAPP Y；
  凭证 JNLTYP 03：借退货科目、借销项税、贷现金（不挂应收）、借存货 / 贷成本；STCRD POSOPR 2、FLAG 2、RETSTK Y、RDOCNUM = 原单号补空格到
  14 位 + 原单行号、成本按原单那一行（CC 写 HS 时的成本，对应原生「按原批次」）；STLOC 回仓；STMAS 结存加回；ISVAT 一行 RECTYP 5 正数
  （混有免税商品时应税基数同 HS 的 `vat_fields` 规则）；ISRUN SR +1；ARMAS.BALANCE、ARBAL.SR 桶照原生冲减（实录现象，照做不修正）。
  先进先出批次链（MLOTNUM / LOTBAL）不写，由 Express「计算成本」重排（同 CC 现有库存写法，计划文档九.7 第 3 条）。
- 退款方式非现金（PromptPay / 刷卡 / 转账）：同 HS，另记一张普通日记账凭证（方向与收款相反：借现金 / 贷收款科目），收款科目沿用 HS 记住的那一个。
- CC 桌面版：载荷新增 SR → 升载荷第 **10** 版（9 已用于 `vat_fields`），旧版诚实拒收；**进 CC 1.12.6**（1.12.5 已验收通过、不再改）。
- Cowork / DMS：不涉及（Cowork POS 导入按退款日期冲减，已登记为已知差别；共用规则加 SR 映射对 Cowork 现有类型不改字节）。

## 5. 隔离与权限

- 退货日汇总按公司（仓库所属公司）合，写账作业归这家公司里电脑够得到账套的员工（`push.actor_of`，同 HS）；查看 / 选科目 / 重试权限同 HS
  （`cashier.read` / `cashier.write`，服务端 `express.api` 拦）。不跨公司：一张 SR 只含同一公司、同一原 HS 的退货。

## 6. 入口：网页、手机、LINE、拍照

- 退货本身仍在收银台 / 收银记录（网页、窄屏同一套），不新增入口；不涉及 LINE、拍照、CC 对话。
- 可见：收银记录 Express 列显示 SR 单号或「待写退货」；公司页 Express 区显示「待写退货 N 张」；退货小票弹窗 Express 按钮看 SR 写入情况。

## 7. 共用件与连带回归

- 共用件：`express-rules`（共用写账规则加 SR）、`cc-dbf-write`（CC 写入路径）、`cc-desktop`（载荷版本）、`erp-transactions`（Express 弹窗）。
  施工时贴 `check-impact` 输出，新入口 `erp.cc.pos-refund`（收银跨天退货写 SR）补进登记表。
- 冒烟：`scripts/smoke` 加一条跨天退货（实验栈造一张昨天的小票 + 今天退货）写 C 盘 T 账套并读回；101 本 T 账套离线回归加 SR 写入与读回。

## 8. 存储与成本

- 每家公司每天最多几张 SR（按原 HS 分），每张一份载荷（几 KB）；无识别、无 LINE、无云存储调用；无新增固定月费。

## 9. 删除、作废、追溯

- 已进退货日汇总的退货小票不能作废（同 HS 规则，提示已计入日汇总）。已写进 Express 的 SR 作废走 B 段 ① 第二步（IV / RR / HP / HS 作废，
  等第 8 条实录），本步不做。

## 10. 文案与官方 UI

- 新增文案（预计）：「待写退货」（状态）、「待写退货 {0} 张」（公司页）、SR 写入失败原因码沿用现有 Express 原因；不改已有文案。
- 官方组件：沿用小票页列、Express 弹窗、公司页 FieldGroup。

## 11. 测试与验收

- 单元：退货日汇总分张（按原 HS）、金额 / 税额 / 付款方式、HS 未写成时等待、起始日之前不写、库存对齐排除、作废拦截。
- CC：SR 写入全表（ARTRN / STCRD / STLOC / STMAS / GLJNL / ISVAT / ISRUN / ARMAS / ARBAL）对照 SR0000001 逐字段；读回；失败整单回滚。
- 离线回归 101 本 T 账套副本；实验栈冒烟（跨天退货写 SR、CC 1.12.6 测试包）；验收复核与人工入口实测。
- 待实录（不挡施工，挡上线）：SR 的 SONUM 挂 HS **日汇总**时 Express 认不认（第 7 节第 3 条，accserver 上线后验收录）。

## 12. 要问用户 / 客户的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 退货日汇总用 B3a 的 `ERP POS Day Summary` 加「单别 HS / SR」一栏，还是另建 DocType？ | 同一个 DocType 加单别（写账队列、弹窗、权限、补单序号全复用） | 另建要复制一套队列接线 |
| 2 | B3a 里「晚到的同日退货（原单 HS 已写）冲成负数停在人工」改成写 SR 吗？ | 改成写 SR（和跨天同一种情形） | 不改就一直停在人工 |
| 3 | 一天里退了几张不同原 HS 的小票：按原 HS 分成几张 SR？ | 是（SR 只能挂一张原单，实录如此） | 合一张就没法挂原单 |

## 13. 工作量与排期

- 步骤：① 共用规则 SR 映射 + CC SR 写入 + 载荷 10（约 4 小时机器耗时）→ ② ERP 退货日汇总、待写退货计数、库存对齐、作废拦截（约 2 小时）→
  ③ 离线回归 + 实验栈冒烟（约 2 小时）→ 交验收。①② 可并行，③ 依赖前两步。全部进 CC 1.12.6，跟这批（1.12.5）之后的下一批上线。
