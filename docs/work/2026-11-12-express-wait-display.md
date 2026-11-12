# 各处显示「在等 Express 关闭」（2026-11-12）

分支 `pearnly/express-wait-display`，基于 `pearnly/express-busy-probe`（`3f3e9e8ac`）。主会话 11-12 批准方案后施工。进行中，未合入。

## 做法

- 不加字段、不另存：真相是排队中的写账记录上的原因码 `ACCOUNT_BUSY_LOCKED` 与上次回报时间（上一支留下的）。读的时候算，上次回报超过 90 秒不再算（`ack_rules.waiting_for_express`）；被领走、写成、转人工后自然消失。不抄进票 / 单据的失败原因栏。
- 共用查询：共用通道 `pearnly_core.cc_desktop.jobs.express_wait(name)`、`express_wait_many(names)`、`book_express_wait(book_key)`；Cowork 账本 `pearnly_cowork.push.express_wait.many / one / account / mark`。

## 改已有文案（用户 11-12：「文案统一用「写入」吧」，主会话转）

两个词条 `pearnly_cowork:intake.error.ACCOUNT_BUSY_LOCKED`、`pearnly_erp_portal:express.error.ACCOUNT_BUSY_LOCKED`，四语相同：

| 语言 | 改前 | 改后 |
|---|---|---|
| zh | Express 正在使用此账套，关闭后将自动推送 | Express 正在使用此账套，关闭后将自动写入 |
| en | Express has this account set open. It is sent automatically once Express is closed | Express has this account set open. It is written automatically once Express is closed |
| th | Express กำลังเปิดชุดบัญชีนี้อยู่ ระบบจะส่งให้อัตโนมัติเมื่อปิด Express | Express กำลังเปิดชุดบัญชีนี้อยู่ ระบบจะบันทึกให้อัตโนมัติเมื่อปิด Express |
| ja | Express でこの会計データが開かれています。Express を閉じると自動で送信されます | Express でこの会計データが開かれています。Express を閉じると自動で書き込まれます |

英 / 泰 / 日按现有词条里「写入」的译法对齐：en written（`Written At`、`cannot be written to Express`）、th บันทึก（`บันทึกลง Express`、`เวลาบันทึก`）、ja 書き込み（`書き込み日時`、`Express への書き込み`）。

没改、列给主会话的相近句子：
- CC 桌面版（要随新版发，`companion/src/companion/i18n.py`）：`busy_body`「账套正被 Express 占用。关闭正在使用该账套的 Express 后点击「重试」，或点击「终止推送」。」、`busy_abort`「终止推送」、`st_waiting_lock`「正在等待 Express 释放账套…」。
- 同一组里的另一句（Cowork 与 ERP 词条表）：「另一台电脑正在写入此账套，完成后将自动推送」。
- 全仓同义文案清单另出，范围由用户定。

## 各处显示

（施工中，完成后逐处补：位置、组件、截图、测试。）
