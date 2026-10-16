# DMS 开单回执逐项复核 + 连点确认不再回「已过期」

- 状态：实现与本地检查完成，待测试机用户试用
- 分支 / 工作树：`pearnly/dms-booking-readback-card` / `.claude/worktrees/dms-booking-readback-card`
- 基线：`origin/main` `7c9bb445`（2026-10-16 开工时）
- 集成目标：`main`；用户试用通过后再合入、部署（部署按批次另行记录）
- 授权：用户 2026-10-16 原话"按这个范围开工：不改写入和结果未知逻辑，只做一致时加一行、不一致时差异卡，做完上测试环境给我试"；
  "连点回话修复和复核卡放同一批做：第二次点击认出正在提交就不回话，只有真过期才回已过期，做完一起上测试环境给我试"。

## 来由与证据

- ris（Rayong-Isuzu-Sales）使用方要求：Pearnly 把订车单存进 DMS 后，按单号把这张单读回来，与确认前的汇总卡逐项对比；
  有不一致就列出差异。理由原话："เพราะบางทีปรากฏว่าเงินจองไม่ได้บันทึก"（有时发现订金没存进去）。
- 生产只读核对（2026-10-16，系统读档账号，只读 `showdata` + `form.php`）：ris 经 Pearnly 开的全部 8 张订车单
  （BKRY26001165 … BKRY26001263），提交值与 DMS 现存值逐项一致，订金、各渠道金额、附件 2 份全部在。
  订金没存的情况在这 8 张里没有出现；已请使用方提供具体单号与截图，尚未收到。
- 连点两次：BKRY26001261（10-12 16:44）第一次点击认领确认号并排入后台开单，第二次点击时确认号已作废，
  `booking_flow.handle_postback` 一律回 `TXT_EXPIRED`，比第一次的开单回执早十几秒到达。生产只有一条开单记录，未重复建单。

## 范围

| 项 | 做法 |
|---|---|
| 复核来源 | 回读核实那一轮已读到的表单经 `submit_booking(readback_out=…)` 交出；不多读 DMS，不改写入、重试、"结果未知"与拒单判定 |
| 比对 | 新增纯函数 `booking/booking_review.py`：汇总卡上的各项按编号（顾问、地点、车、颜色、销售条件、上牌方式、省县区、邮编、称谓、银行）、金额（`Decimal`）、身份证号（去噪）、电话与账号（只比数字）、手填文字（折叠空白）比；不比 DMS 回显的名称文字 |
| 回执 | 全部一致且附件齐：原回执 + 「ผลการตรวจสอบกับ DMS: ถูกต้อง」；有不一致：差异卡（标题、单号、逐条「确认时的值 → DMS 里的值」、请在 DMS 中修改；无按钮）；拿不到表单或附件未齐且无其它差异：原回执不变 |
| 留痕 | 推送台账 `response_body.review_diff_items` 只记不一致的项目名，不记值 |
| 连点 | 确认 / 重试没认领到时，若这次点击的确认号就是会话里正在提交的那个（或刚在并发里输给同一张卡），不回任何话；其余照旧回「已过期」 |

关键项（订金、付款、车、客户、顾问、交车日期等）对不上时仍按原逻辑报"结果未知"，不会出差异卡（用户 2026-10-16 确认这次不动）。

新增文案（四语，`dms.card.TXT_REVIEW_OK / TXT_REVIEW_DIFF_HEAD / TXT_REVIEW_BOOKING_NO / TXT_REVIEW_FIX`），
泰文与发给使用方的说明一致。

## 改动文件

- `booking/booking_submit.py`：回读核实时把表单放进 `sink`，`submit_booking` 新增可选 `readback_out`
- `client/client_ops.py`：`create_booking_via_form` 透传 `readback_out`
- `booking/booking_review.py`（新）：逐项比对；`review()` 不抛异常
- `booking/service.py`：结果带 `review`
- `booking/recovery.py`：交给回执投递时保留 `review`
- `booking/delivery.py`：回执存整条消息（`receipt.message`），旧记录只有 `text` 时照旧发
- `booking/booking_ledger.py`：台账记 `review_diff_items`
- `line/cards.py`：`booking_receipt_message`
- `booking/conversation/booking_flow.py`：`_reply_unclaimed`
- 测试：新增 `tests/test_dms_booking_review.py`（接入 `check-dms-offline.py`）；`test_dms_booking_outcome.py` 增加回执复核与连点用例；
  `test_dms_booking_attach_repair.py`、`test_dms_booking_customer_gapfill.py` 按新接口调整替身

## 验证

| 层级 | 状态 | 证据 |
|---|---|---|
| 实现 | 完成 | 本分支 |
| 本地离线检查 | 通过 | `check-architecture.py` 0 违规；`check-dms-offline.py` 32 项全过（Python 3.12）；`i18n-build.py --check` PASS；完整闸门见下 |
| 测试机（testdms 实时源码 + 真实 LINE） | 待执行 | |
| 用户试用 | 待用户 | |
| 合入 / 部署 | 未执行 | 用户试用通过后 |
