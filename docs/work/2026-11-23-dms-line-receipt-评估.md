# 需求影响评估：DMS 的 LINE 等结果动作对齐「原生状态优先、只回复、四个状态词」（主会话 11-23 已审，第 13 栏）

LINE 回执窗口，11-23。施工分支 `pearnly/dms-line-receipt`（基于 LINE 回执 7551c779e，随 train-1123 进 main 后变基）。

## 0. 需求原文与理解
- 用户 11-22：「我要line这边全部会触发执行状态的指令都要优先用line原生的状态，我们的文案才是兜底」；11-23：「为什么要定义时间？为什么不是到达官方原生动画还未结果才触发我们的兜底」；用户问「dms为什么不在本次范围」。
- 理解：DMS 的 LINE 等结果动作与 ERP / Cowork 用同一个共用件 `pearnly_core.line_ui.receipt`：loading 开满 60 秒 → 结果到了用同一张回复令牌回结果 → 第 55 秒还没结果才回兜底「录入中」+【查看结果】；只回复、不推送；状态词只用 录入中 / 成功 / 失败 / 已取消。原评估第 0/1 栏写 DMS「已是目标形态」不准，本次更正。

## 1. 现有同类与复用
- DMS 自己的 `line/out.py` `_SlowReply`（`out.py:254`）：60 秒 loading、`SLOW_AFTER=40` 秒回 `TXT_STILL_WORKING` +【ดูผล】，结果在任务结束时 `line.batch` 先回复、失败转推送；「查看结果」读 24 小时的结果存档（`out.py:317`）。
- 共用件 `pearnly_core.line_ui.receipt`（ERP、Cowork 已用）：start / settle / wake / resume，计时线程与结果抢同一个 GETDEL，只回一次、只回复。
- 做法：DMS 的 `_SlowReply` 换成共用件（不另写一套）；DMS 的「查看结果」存档（结果超过 5 条分批、24 小时）保留为 DMS 的 `resume` / 查看函数，状态词改用标准词。

## 2. Frappe 原生
- 不涉及界面组件；LINE loading、回复令牌是 LINE 协议。后台任务仍走 DMS 现有的 `run_guarded` / `work.run`（不改队列）。

## 3. 按类通用
- 同一类「LINE 上等执行结果」三产品一个常量对（`LOADING_SECONDS=60`、`WAIT_SECONDS=55`）、一个回执共用件、一张状态词表；DMS 不另定秒数。

## 4. 入口清点（三产品一张表）

| # | 产品 | 用户动作 | 现在 | 改后 |
|---|---|---|---|---|
| E1 | ERP | 识别卡【保存】（`integrations/handlers.py _save_card/_await`） | 已对齐（本批） | 同 |
| E2 | ERP | 收款确认【保存】（`line_collection._release`） | 已对齐 | 同 |
| E3 | ERP | 收款选客户 / 【刷新】在读（`_wait_read`） | 已对齐 | 同 |
| E4 | ERP | 收款【下一步】主档在读 | 已对齐 | 同 |
| C1 | Cowork | 识别卡【保存】（`line/save.py`） | 已对齐 | 同 |
| C2 | Cowork | 保存时科目表在读 | 已对齐 | 同 |
| C3 | Cowork | 菜单选目标时公司列表在读（`line/flow.py`） | 已对齐 | 同 |
| D1 | DMS | 发身份证照片（`intake/flow.py:107 handle_image` → `out.enqueue("image")` → `run_image`） | loading 60 + 任务开始再 30；40 秒回「正在处理，完成后请点击「查看结果」」+【ดูผล】；结果回复、失败转推送；队列满 / 延后 `push_text`（`out.py:203`）；识别失败 `push_or_reply_text`（`out.py:388`） | loading 60；结果到了同令牌回；55 秒回「录入中」+【查看结果】；不推送（队列满 / 延后 / 失败都改成用这次令牌回，令牌过了就等人点【查看结果】） |
| D2 | DMS | 输入电话后查重（`text_router.py:135`、`menu_flow.py:178`、重试 `flow.py:680`，`run_dedup`） | **没有 loading**（webhook 里同步跑）；40 秒兜底同上 | 补 loading；其余同 D1 |
| D3 | DMS | 确认新建 / 更新客户（`flow.py:251/253` → `execute`） | 同 D1；回执 `_push` = `push_or_reply_text`（`flow.py:807`） | 同 D1；回执用这次令牌回 |
| D4 | DMS | 【重试】（`flow.py:243 _run_retry`） | 同 D2/D3；没有可重试的回「此项已过期，请重新开始」 | 同 D2/D3（过期那句是真过期，保留） |
| D5 | DMS | 订车预览【确认建单】/【重试】（`booking_flow.py:93`） | 同 D1；`booking_flow.py:76/179` 写死 `push_text`；连点两次第二次不回 | 同 D1；不推送 |
| D6 | DMS | 开单回执差异卡【更新资料】（`review_fix.py:113`） | 同 D1；`review_fix.py:145` 推送或回复 | 同 D1 |
| D7 | DMS | 查询（记录 / 排行）要现读 DMS（`query_flow.py:285/296`） | loading 60（读取时续 60）；**没有兜底、没有【查看结果】**；读完 `_push_result`（`snapshot_sync.py:238`）一定推送 | loading 60；读完同令牌回结果；55 秒回原来的「正在读取」那句 +【查看结果】（读取不是录入，沿用 ERP 收款读取的做法：原句兜底）；不推送 |
| D8 | DMS | 网页编辑页提交后回聊天送卡（`page_reply.py:78`） | `reply_or_push`（网页提交，手上没有 LINE 回复令牌） | **不在范围**：不是 LINE 触发的动作，没有令牌只能推送（待定 1） |
| D9 | DMS | 订车补送 / 恢复（`claim_recovery.py:32`、`recovery.py:125`、`delivery.py:249/296` 定时补送回执） | 推送（没有令牌） | **不在范围**：定时 / 恢复路径，不是人在等的那次回复（待定 1） |
| D10 | DMS | 「查看结果」本身（`out.py:317 show_result`） | 没结果回「此项已过期」；`pending` 回「处理尚未完成，请稍后再次点击「查看结果」」；>5 条分批 | 在等回「录入中」+【查看结果】；有结果回结果（分批保留）；**修缺陷见第 5 栏** |

## 5. 已知缺陷（一并修）
- **等待中却回「此项已过期」**（11-23 多行补测实测）：识别被延后（`Deferred("claude_not_configured")` 或 `claude_unavailable`）时，`run_guarded` 的 `finally` 调 `slow.finish()`，兜底已发过就把结果存档写成 `{"messages": []}`（`out.py:314`）盖掉 `pending`，用户点【查看结果】走 `out.py:338` 回「此项已过期，请重新开始」，其实还在重试。改后：延后 / 重试中的仍记「在等」，点了回「录入中」；只有真没有这件事才回过期。
- D2 查重没有 loading：补上。
- 结果存档的键只有 LINE 用户 ID（同一人同时只存一份）：保留现状，记录（同一人前后两件时，后一件盖前一件，旧的点【查看结果】看到的是新的；与「一个动作一个键」的共用件不一致，施工时按动作分键，见待定 3）。

## 6. 上下游、Express / CC、隔离权限
- 不改 DMS 写入（建客户、建订车单）与对 DMS 系统的读写、不改权限与会话锁；只改 LINE 上回什么、什么时候回。操作员绑定、DMS 账号排队（`run_guarded` 续 loading）保持。

## 7. 入口与共用件
- 入口：LINE（三星真机）。网页、CC 桌面版不涉及。
- 共用件：`pearnly_core.line_ui.receipt`（不改它的行为；DMS 新调用方登记进 `tests/test_line_receipt.py` 的 WAITERS 与共用件登记表）；`pearnly_core.line` 的 `batch` / `reply_or_push` 不改（ERP、Cowork 也在用），DMS 侧不再调推送。

## 8. 成本与并发
- 不加 push（反而去掉 D1/D3/D5/D6/D7 的推送计数）；计时线程同 ERP（30 并发已实测）；不占后台队列。

## 9. 删除 / 作废
- 不涉及；DMS「放弃」「已取消」照旧。

## 10. 文案（改已有文案，四语清单给用户看）

| key | 旧（中 / 泰 / 英 / 日） | 新（中 / 泰 / 英 / 日） |
|---|---|---|
| `dms.card.TXT_STILL_WORKING`（55 秒兜底） | 正在处理，完成后请点击「查看结果」。 / กำลังดำเนินการ เมื่อเสร็จแล้วกรุณากด «ดูผล» / Still working on it. When it is done, tap View result. / 処理中です。終わったら「結果を見る」を押してください。 | 录入中 / กำลังบันทึก / Entering / 入力中（标准词 `Entering`，同 ERP / Cowork） |
| `dms.card.TXT_NOT_READY`（点【查看结果】时还在等） | 处理尚未完成，请稍后再次点击「查看结果」。 / ยังไม่เสร็จ กรุณารอสักครู่แล้วกด «ดูผล» อีกครั้ง / Not done yet. Please tap View result again in a moment. / まだ終わっていません。少し待ってからもう一度「結果を見る」を押してください。 | 录入中 / กำลังบันทึก / Entering / 入力中 |
| `dms.card.BTN_SHOW_RESULT`（按钮） | 查看结果 / ดูผล / View result / 結果を見る | 查看结果 / ดูผลลัพธ์ / View result / 結果を見る（泰文对齐 ERP / Cowork 的 `View result`） |
| D7 查询读取的兜底 | （现在没有兜底） | 沿用查询现有「正在读取」原句 +【查看结果】（不新增文案；施工时贴出原句） |

不变：`TXT_EXPIRED`（真过期才用）、`TXT_MORE_RESULTS`（分批）、识别队列繁忙 / 待核实 / 未完成三句（是结果本身，不是过程词）。成功 / 失败那句是 DMS 现有的结果卡与回执，不改。

## 11. 验收
- 离线：`tests/test_line_receipt.py` 加 DMS 调用方；DMS 现有离线测试（`scripts/check-dms-offline.py` 与受影响定向检查）改期望；`sample-verify` 只跑无副作用的（先读脚本，真建单 / 发 LINE / 付费识别不进）。
- 真机（testdms + 三星，排队问主会话）：D1 发身份证（成功、55 秒兜底、【查看结果】在等时回「录入中」）、D2 查重有 loading、D5 确认建单（55 秒内回结果不推送）、D7 查询现读、D10 延后时不再回「已过期」。

## 12. 待定（请主会话 / 用户定）
1. D8 网页提交回聊天、D9 定时补送 / 恢复：没有 LINE 回复令牌，只能推送。建议不在本次范围（不是人点了在等的那次回复），保留推送。
2. 识别（D1）延后很久时：令牌过了 55 秒就只能等人点【查看结果】，不再自动送卡（ERP / Cowork 识别现状是超时推送送卡，评估原第 12 栏「识别保留现状」）。DMS 身份证要不要同 ERP / Cowork 识别一样保留推送送卡？建议：同 ERP / Cowork 识别口径（保留推送送卡），D1 只把过程词与 55 秒对齐。
3. 结果存档按 LINE 用户一份 → 按动作分键（同共用件）：建议改（一个人前后两件不再互相盖）。

## 13. 主会话 11-23 批复（已定）
1. D8 网页提交转聊天、D9 定时补发 / 恢复：没有回复令牌，**保留推送，不在范围**（理由同第 12 栏 1）。
2. D1 身份证识别慢：与 ERP / Cowork 识别同口径，**超时推卡保留**，只对齐「录入中」与 55 秒。
3. 结果存档**按动作分键**：改。
4. `run_guarded` finally 覆盖 `pending` 的缺陷：同一改动修。
5. 第 10 栏三处改已有文案：主会话拿去给用户看，**用户同意后才登记 copy-approved**（施工可先做代码，文案登记等批）。
6. 回复被拒（主会话 11-23 定）：同 ERP / Cowork 共用件全程不推送——被拒时结果存下给【查看结果】，不推送；只有身份证识别超时送卡、D8 / D9 无令牌照旧推送。

## 14. 风险（请主会话判断，改就三产品一起改）
- 回复被拒（令牌被 LINE 判失效：例如 webhook 重投、令牌在 55 秒内已被别的回复用掉、LINE 侧异常）且还没回过兜底时，这个人**既没收到结果、也没收到「录入中」+【查看结果】**：三个点到 60 秒自己消失后聊天里什么都没有，只有他自己想到再点菜单 / 发消息才知道。ERP、Cowork 的共用件同样如此（`receipt.settle` 回复失败即止）。
- 频率：正常 55 秒内回复的令牌 LINE 都接受（11-23 实测 20 / 40 / 55 秒都送达）；被拒基本只在重投与异常时出现。
- 可选做法（不在本次施工内）：被拒且没回过兜底时，按「无令牌」一类推一句「录入中」+【查看结果】（一条推送，结果仍等他点）；要做就改共用件，ERP / Cowork / DMS 一起生效。
7. 第 10 栏三处文案：用户 11-23「文案可以」（主会话转达）；已改并登记 `scripts/i18n/copy-approved.tsv`（TXT_STILL_WORKING、TXT_NOT_READY → 录入中 / กำลังบันทึก / Entering / 入力中；BTN_SHOW_RESULT 泰文 ดูผล → ดูผลลัพธ์）。
