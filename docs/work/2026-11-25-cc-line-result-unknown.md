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

## 真机记录（11-25 合并上机，窗口 two-pc-write-test）
实验栈 testerp = 本地组合 17bf1fd3e（本分支 21fb47a04 + writer-busy-rest 09de6ce87 + pinned-waiting-computer 3f614958e），测试机1 / 测试机2 真 CC 1.12.24。
三星 LINE ERP-TEST：采购 · บริษัท Pearnly จำกัด 发票图 TS6911-0014 → 保存（PUR-2026-212653）→ 测试机1 领到即冻结其 CC，约 5 分钟后收回、钉回测试机1；
再按 `jobs._recover` 第 3 次收回的写法把这件落成转人工、结果不明（实验栈里直接改写账件，原因 `cc_desktop.result_unknown`，单据 held）。截图 `~/pearnly-devtools/handoff-1125/twpc-shots/04-*`。

真机记录 | erp.line.postback.erp_result | 2026-11-25 | 21fb47a04 | 三星 SM-A146P + LINE ERP-TEST | 通过 | 点【查看结果】：回「结果不明」，快捷回复只有【查看结果】，没有【编辑】 |
真机记录 | erp.line.postback.erp_save | 2026-11-25 | 21fb47a04 | 三星 SM-A146P + LINE ERP-TEST | 通过 | 再点卡片「保存」：回「结果不明」+【查看结果】，没有【编辑】、没有说失败 |
真机记录 | erp.line.postback.erp_cancel | 2026-11-25 | 21fb47a04 | 三星 SM-A146P + LINE ERP-TEST | 通过 | 再点卡片「取消」：不作废，回「结果不明」+【查看结果】 |
真机记录 | cowork.line.postback.cowork_result | 2026-11-25 | 21fb47a04 | 三星 SM-A146P | 没测到：要另切 testcowork 的测试环境，测试副本里 Cowork 识别服务地址已失效，今天没切；主会话 11-25 准许记没测到 |

解冻测试机1 后它补的迟到回执照收，这件写成 RR691114-001、只一次（69EXP\TEST 测完已整本还原）。
