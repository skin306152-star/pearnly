# 排队中的单钉在一台不在线的电脑上：说出在等哪台（11-25 两台电脑实测发现）

分支 `pearnly/pinned-waiting-computer`，基线 origin/main f18fa1ec8。主会话 11-25 定：带电脑名「等待电脑 · 名称」，判断放共用层只读函数，
`push._queued_note` 先看它；LINE 回执状态词仍是「录入中」，这句只作原因行。与「等账套后不歇」（`pearnly/writer-busy-rest`）分开两条分支。

## 现象
测试机2 领走一张后失联，收回时这件钉回测试机2（`jobs._recover`：排队中、`route_device` = 那台），别的电脑不领。同公司测试机1 在线时
`jobs.writers` 报 ok，ERP 每分钟写原因的 `push._queued_note` 写空：单据页、列表、CC 对话、LINE 回执都只写「录入中」，看不出要等那台开机。

## 改动
- 共用层 `cc_desktop/jobs.waiting_computer(name)`（只读）：排队中、钉在一台电脑上、那台此刻不在线 → 那台的名称（`device_label`，连接页上显示
  的那个；那台没有名称回空串，页面只写「等待电脑」）；没钉、那台在线、已移除、查不到 → None（不在等哪一台）。原因栏只认第一个冒号前的前缀，名称里有冒号等照存。定时任务钉在读文件夹的那台、那台关着时同样适用。
- ERP `push._queued_note`：先看它，是就写 `express.waiting_computer:<电脑名>`（`contracts.waiting_computer`），否则照旧按 writers。
- 显示（同一句「等待电脑 · 名称」）：
  - 单据页 Express 弹窗原因栏（列表 Express 一栏的悬浮同一个函数，但共用列表目前不渲染悬浮文字，见下）：`transactions.js` 的 `p.waiting_computer`（状态词照旧 `p.ledger_word` →「录入中」）；
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
- 实验栈流程：`erp.express.waiting-computer`（单据页 Express 弹窗原因栏、列表状态词、CC 对话推送结论各一条断言），结果见下。

## 真机记录
11-25 合并上机（实验栈 testerp = 本地组合 17bf1fd3e：本分支 3f614958e + writer-busy-rest 09de6ce87 + cc-line-result-unknown 21fb47a04；
测试机1 / 测试机2 真 CC 1.12.24，写 69EXP\TEST、测完整本还原）。三星 LINE ERP-TEST：采购 · บริษัท Pearnly จำกัด（现用账套 69EXP\TEST）发票图 TS6911-0014
→ 保存 → 测试机1 领到即冻结其 CC → 约 5 分钟后这件钉回测试机1（PUR-2026-212653 / PE2569-000040）。截图 `~/pearnly-devtools/handoff-1125/twpc-shots/`。

真机记录 | erp.line.postback.erp_save | 2026-11-25 | 3f614958e | 三星 SM-A146P + LINE ERP-TEST | 通过 | 钉住后再点卡片「保存」：回「录入中」，下一行「等待电脑 · 测试机1」，状态词不变，快捷回复【查看结果】 |
真机记录 | erp.line.postback.erp_cancel | 2026-11-25 | 3f614958e | 三星 SM-A146P + LINE ERP-TEST | 通过 | 同一张已提交的单点「取消」：不作废、回当前状态「录入中 / 等待电脑 · 测试机1」 |

同一次另看到：「查看结果」回同一句；单据页 Express 弹窗状态「录入中」、原因「等待电脑 · 测试机1」；CC 推送结论
`{status: queued, cc_desktop_online: false, waiting_computer: 测试机1, note: 等待电脑 · 测试机1}`；解冻后迟到回执照收，写成 RR691114-001、只一次。

## 实验栈流程
`erp.express.waiting-computer` 本机实验栈（pearnly-twpc-lab，本分支源码）通过：单据页 Express 弹窗原因栏与状态词、CC 推送结论、列表里这张的 Express 一栏仍是「录入中」。
原有问题（不是本分支引入，已报主会话）：电脑宽度的共用列表把 Express 一栏的悬浮文字渲染成空，11-12 的「在等 Express 关闭」悬浮在列表上同样不出来；
手机宽度列表不显示 Express 一栏。所以列表上看不到「等待电脑 · 名称」，点开单据看 Express 弹窗。

## 共用件连带回归（`scripts/check-impact.py`）
| 入口 | 结果 |
|---|---|
| erp.cc.write、erp.cc.chat-push、erp.collection.server、erp.mrerp.direct、cc.operator.page（离线） | 通过：受影响离线测试全过 |
| erp-transactions 的 [auto] 页面入口（采购 / 销售 / 出入库 / 收银等页与详情） | 采购页与详情：流程 erp.express.waiting-computer 通过；其余页本分支只多一个只在排队中且原因是等待电脑时才返回文字的函数，交合入前的 Flows 跑 |
| 其余 [manual]（连接、主档、作废、MR.ERP 登录、桥、云端、Cowork 推送等） | 没测到：本改动只在排队中且钉在离线电脑时多一句原因，不碰这些入口的逻辑；随批验收 |
