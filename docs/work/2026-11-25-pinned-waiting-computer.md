# 排队中的单钉在一台不在线的电脑上：说出在等哪台（11-25 两台电脑实测发现）

分支 `pearnly/pinned-waiting-computer`，基线 origin/main f18fa1ec8。主会话 11-25 定：带电脑名「等待电脑 · 名称」，判断放共用层只读函数，
`push._queued_note` 先看它；LINE 回执状态词仍是「录入中」，这句只作原因行。与「等账套后不歇」（`pearnly/writer-busy-rest`）分开两条分支。

## 现象
测试机2 领走一张后失联，收回时这件钉回测试机2（`jobs._recover`：排队中、`route_device` = 那台），别的电脑不领。同公司测试机1 在线时
`jobs.writers` 报 ok，ERP 每分钟写原因的 `push._queued_note` 写空：单据页、列表、CC 对话、LINE 回执都只写「录入中」，看不出要等那台开机。

## 改动
- 共用层 `cc_desktop/jobs.waiting_computer(name)`（只读）：排队中、钉在一台电脑上、那台此刻不在线 → 那台的名称（`device_label`，连接页上显示
  的那个）；没钉、那台在线、已移除、查不到 → 空串。定时任务钉在读文件夹的那台、那台关着时同样适用。
- ERP `push._queued_note`：先看它，是就写 `express.waiting_computer:<电脑名>`（`contracts.waiting_computer`），否则照旧按 writers。
- 显示（同一句「等待电脑 · 名称」）：
  - 单据页 Express 弹窗原因栏、列表 Express 一栏悬浮说明：`transactions.js` 的 `p.waiting_computer`（状态词照旧 `p.ledger_word` →「录入中」）；
  - 服务端 `pending.reason_label`（CC 对话推送结论 `cc_tools._outcome` 的 `note`、LINE 回执）；
  - CC 对话推送结论另带 `waiting_computer`（电脑名）与 `cc_desktop_online: false`；
  - LINE 回执：「录入中」不变，另起一行写这句（`line_cards.entering(doc, why)`）。
- 停下出口表：`express.waiting_computer` 归「等」（等那台开机，`stop_exits`）。

## 文案
新增一条 ERP 词条（不改已有文案）：`Waiting for computer`（等待电脑 / รอคอมพิวเตอร์ / パソコン待ち）。主会话要求复用代录那条，但代录的「等待电脑」
是 CC 网页前端词条（`frontend/assistant/src/i18n.ts` 的 `folderWaiting`），与服务端 Frappe 词条不是一套系统，不能共用一个键；这里四语逐字照抄那一条，
测试比对两边逐字一致（`tests/test_erp_express_direct.py`）。

## 测试
- 离线：`tests/test_cc_desktop_channel.py`（钉住 / 在线 / 已移除 / 写成后）、`tests/test_erp_express_direct.py`（排队原因、原因写法与解析、
  单据页文案、状态词不变、LINE 接着等、四语同代录、单据页 JS）、`tests/test_erp_cc_chat_push.py`（CC 推送结论）、`tests/test_line_receipt.py`（LINE 另起一行）。
- 实验栈流程：`erp.express.waiting-computer`（单据页 Express 弹窗原因栏、列表悬浮、CC 对话推送结论各一条断言），结果见下。

## 真机记录
真机记录 | erp.line.postback.erp_save | 2026-11-25 | 待定 | 三星 | 没测到：LINE 回执要真 LINE 官方号与付费识别，主会话 11-25 定本窗口不上真机，交拼车窗口安排 |
真机记录 | erp.line.postback.erp_cancel | 2026-11-25 | 待定 | 三星 | 没测到：同上（只改了「录入中」回执可多一行原因，取消路径未改） |

## 共用件连带回归（`scripts/check-impact.py`）
| 入口 | 结果 |
|---|---|
| erp.cc.write、erp.cc.chat-push、erp.collection.server、erp.mrerp.direct、cc.operator.page（离线） | 通过：受影响离线测试全过 |
| erp-transactions 的 [auto] 页面入口（采购 / 销售 / 出入库 / 收银等页与详情） | 见下方流程结果 |
| 其余 [manual]（连接、主档、作废、MR.ERP 登录、桥、云端、Cowork 推送等） | 没测到：本改动只在排队中且钉在离线电脑时多一句原因，不碰这些入口的逻辑；随批验收 |
