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
- 等服务器测试账套实录（不挡施工，挡上线）：SR 的原单号挂 CC 自编的日汇总单号时 Express 认不认；原单行号两位数时 RDOCNUM 的写法；非现金退款的凭证写法。
