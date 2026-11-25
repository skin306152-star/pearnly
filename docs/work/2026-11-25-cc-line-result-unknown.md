# LINE 保存回执：结果不明不说失败（2026-11-25）

分支 `pearnly/cc-line-result-unknown`，基 `pearnly/cc-status-words`（a13779bba）。主会话 11-25 定：结果不明（账本里可能有、还没核对）单独一类，
沿用已有词「结果不明」，不说失败、不给【编辑】（改了再存会重推、可能重复入账），只给【查看结果】。

- Cowork：`push.application.card_save.outcome` 用 `push.lifecycle.result_unknown` 判出后回 ``unknown``；`line.save._outcome` 回「结果不明」+【查看结果】。
- ERP：`integrations.line_cards.result` 先判 `_unconfirmed`（MR.ERP 停在结果不明；Express 写账件领出后失联，同 `cc_tools._outcome` 的 unconfirmed），
  回「结果不明」+【查看结果】。
- 测试：`tests/test_line_receipt.py` 新增一条源码守门。

## 真机

改到的入口 erp.line.postback.erp_save / erp_result / erp_cancel、cowork.line.postback.cowork_result 按产品地图要真机记录（真 LINE 官方号、
付费识别、postback），实验栈没有 LINE 频道。由拼车窗口安排真机，做完在本文件按固定格式写真机记录后再进 main。
造「结果不明」要让写账件领出后失联（或 MR.ERP 导入受理后查不到），真机时与主会话商定做法。
