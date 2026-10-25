# 收银跨天退货写成 Express 退货单 SR（B3b）

基线 main `d8d42e6f`，分支 `pearnly/stock-refund-sr`。评估（已审定）：`docs/work/2026-10-26-stock-refund-sr-评估.md`；
共用写账规则与 CC 桌面版那一层的记录：`docs/work/2026-10-26-stock-refund-sr-cc.md`。

评估第 12 节三问按建议做：退货日汇总用同一个 DocType（`ERP POS Day Summary` 加单别 `doc_kind`、原 HS `against`）；
B3a 里晚到的同日退货（原小票已进 HS）不再冲成负数停下，改写 SR；一天退了几张不同原 HS 的小票按原 HS 分张。

## ERP 侧

- `express/pos_day.py`：现销日汇总只并「同一天卖、同一天退、原小票还没汇总」的退货（先选再改，不在一条 UPDATE 里自连接判断）。
  原小票已进某张 HS 的退货（跨天、晚到）每晚按 (退货那天, 原 HS) 合成退货日汇总（`refund_groups` / `summarize_refunds`），排进写账队列。
  退货单内容照正数记（退回的数量、税前额、退给客户的钱），带 `refund: {against: 原 HS 的 Express 单号, against_total: 原 HS 含税合计}`；
  原 HS 还没写进 Express 时停在「等待原日汇总写入」（`express.pos_refund_waiting`，列进 `RETRY_REASONS`，每分钟再看），写进去了自动接着写。
- `express/contracts.py::pages`：refund 块照 pos 块的路写进主页面（`pos_contract.refund_audit`）。
- `express/prepare.py`：`choice_rules.resolve(..., refund=...)`，退货科目问一次、按公司记住；`express/api.py` 退货科目从这份账套的科目表选。
- 库存对齐：退货小票进了写成的退货单就不算 ERP 独有（同一个 DocType，原查询照用）。
- 页面：选择种类「销货退回科目」、原因「等待原日汇总写入」「退货明细须为 Express 库存商品」，CC 回报的五个退货原因码照 Cowork 同一套文案。
- 新增文案（ERP）：`express.kind.refundAccount`、`express.reason.refundWaiting`、`express.reason.refundItems`、`express.error.REFUND_*` 五条；不改已有文案。

## 验证

- `tests/test_erp_express_direct.py`：退货单照正数记、挂原 HS、原 HS 没写成先等；按原 HS 分组建退货日汇总；
  退货日汇总经共用写账规则先问退货科目、记住后出 SR 载荷（第 10 版、CNTYP 3、RETSTK Y）。CC 与共用规则的测试见 cc 那份记录。

## 待做

- 第 ③ 步（要测试机）：101 本 T 账套离线回归加 SR 写入与读回；实验栈冒烟（造昨天的小票 + 今天退货 → 写 C 盘 T 账套 → 读回）；出 CC 1.12.6 测试包。
- 等服务器测试账套实录（不挡施工，挡上线）：SR 的原单号挂 CC 自编的日汇总单号时 Express 认不认；非现金退款的凭证写法（RDOCNUM 的多位行号写法已由 S 盘副本答清，见下）。

## 验收复核修正：RDOCNUM 拼法（10-26）

验收：原来「原单号补空格到 14 位再接行号」从原单第 10 行起超出 15 位、错位。在测试机 S 盘副本（`C:\PearnlyStress\S盘副本`，验收从 S 盘只读拷出，
1039 本账套、STCRD 里约 300 万行带 RDOCNUM 的退货 / 引用行）只读扫了一遍：RDOCNUM 一律 C(15)，**原单号左对齐、原单行号右对齐到第 15 位，
中间补空格**（如 `HS0000039     1`、`IV5801890    12`、单号 10 位 + 3 位行号 `…   100`；单号 14 位时行号紧贴）。`dbf_refund.rdocnum` 照此改，
读已退数量按「单号后面只能是空格加数字」认行号。补用例：1 / 12 / 100 行的写法与已退数量累计。CC 全量测试 1175 过。

## 验收复核修正：ERP 侧（10-26，「B3b-SR-df3416f6-复核.md」，主会话口径）

- 混合免税的应税基数：退货日汇总与原 HS 用同一套可改常量和算法（只记应税部分）；`exempt_total` 对退货小票记负数，退货单里照正数扣
  （`vat_fields(..., sign=-1)`），两边基数对得上（例：卖应税 107 + 免税 50、次日全退，SR 的 AMT01 = 100，同原 HS）。
- 出口：收银日汇总 / 退货单停住（停下、失败、电脑转人工）时，Express 弹窗给「再写一次」（停下的按此刻内容重新准备）和
  「标记为已在 Express 手工处理」（新状态 `handled`，撤掉挂着的写账件，库存对齐当作已写进 Express）。原 HS 是手工处理的，退货单停在
  「原日汇总已在 Express 手工处理」，也走同一个出口。
- 待写退货：公司页 Express 区显示「待写退货」张数（原小票已进日汇总、退货自己还没进写成的退货单）；收银记录的 Express 列对还没合成退货单的
  退货小票标「待写退货」；退货单等原 HS 时显示「等待原日汇总写入」，原 HS 写成后每分钟的检查自动接着写。
- 跨年：原 HS 的日期与退货日期选到的账套不同（年度账套分开）时停在「原单与退货在不同年度账套」，Express 原生跨年退货写法列进实录点，实录前停人工。
- 合单的 UPDATE 再核 `status = 'completed'`（选出来之后被作废的小票不进，HS 同样受益）。

## 验收复核修正：已退数量精确匹配（10-26 第二轮）

- 原来读已退数量按「RDOCNUM 以原单号开头、后面是空格加数字」认，单号长短不一时会误认：14 位单号第 1 行（`HS000003912345` + `1`）
  与 13 位单号第 51 行拼出来一模一样。改为按原单真有的每一行用 `rdocnum(原单号, 行号)` 拼出原生写法全等比较，物料码也要一样才算。
- 合成表 `table_specs.py` 的 STCRD.RDOCNUM 已是 C(15)（e7cea853），与真表一致。
- 免税基数、非库存品、待写退货计数、换电脑重写幂等、跨年四条中优先级已在 60dad37f / e7cea853 修完（见上两节与 cc 记录）。
