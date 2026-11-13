# 收款 LINE 侧：卡片有效期、推送、颜色、按钮、凭证后语言（11-13）

分支 `pearnly/collection-line-side`，基线 `pearnly/collection-on-ledger` @ 8d9c1788a。只改服务端 LINE 侧；CC 桌面版、测试机、实验栈、三星归收款接手窗口。
依据：共享笔记「卡片有效期规则-对照与整改-11-13」（A 节 DMS 基准、B 节 1–11）、「收款-LINE侧-交接-11-13」、主会话任务书（今晚批次 7 条）。

## 今晚批次（实现 + 离线测试；红转绿都在 `tests/test_erp_collection_line.py`）

| # | 改了什么 | 测试（旧代码上红） |
|---|---|---|
| 1 | 确认卡一卡一个一次性确认号（共用 `line_session.consume_nonce`，不另写）；【确认】【撤回】都要它。出新卡、重开一笔、取消时旧卡失效、没放行的草稿作废（`service.discard_draft`，标已撤回、记录与凭证保留）；点旧卡不改会话、不抹正在录的；同一号再点不回话；其余失效回「此项已过期，请重新开始。」（DMS 同一句，ERP 字典新增同译文）。「已记录 · 待写入」上的【撤回】带这一笔自己的撤回号 | `CardLife.*`、`test_withdraw_after_release_from_the_pending_message`、`test_a_forged_button…` |
| 2 | 卡片那一步会话 120 分钟（其余步 30 分钟续期照旧）；卡上记出卡时刻，超 120 分钟点【确认】回已过期、草稿作废；会话没了点旧卡同样作废那张草稿。单据日期按放行当天（`service.release`） | `test_a_card_is_valid_for_120_minutes…`、`test_an_expired_session…`、`test_the_document_date_is_the_day_it_is_released` |
| 3 | 【取消】带步号（每存一步换一个），只认最新那条消息上的；取消时未放行草稿一并作废 | `test_cancel_only_from_the_current_step_and_it_discards_the_draft` |
| 4 | 写成不推送；「已记录 · 待写入」带【查看结果】（postback，回复当前状态 / RE 号，了结的记作看过）；进「收款」时先用回复列出 7 天内写成 / 失败、还没告诉过的（最多 10 笔）；写失败推一条（同一笔一次，看过不推）；等结果、存凭证两处仍是先回复不行才推 | `test_written_after_the_wait_is_not_pushed…`（成功 0 条）、`test_the_menu_lists…`、`test_a_failure_is_pushed_once…`（失败 ≤1 条） |
| 5 | LINE 卡片按业务类型一处取色（`line_cards.COLORS`）：采购绿 #16873E、销售红 #B11B50、收款品牌紫 #6B3FF2（`product.css` 的 --pearnly-accent-deep；绿色已是采购的） | `test_the_card_color_is_its_own` |
| 6 | 收款方式按钮经 `choice_labels`（原来 NLTTEST 的「เงินโอนเข้าบัญชี SCB/ออมทรัพย์」「…SCB/กระแส」截完一样），消息里列全名。其余 LINE 用长名做按钮的：公司 / 账套 / 客户 / ERP line_flow 已在上一批改；`line_items` 选仓库照旧保留（要付费识别的真机证据） | `test_each_step_is_asked_in_order…`、`test_line_choice_labels.CallSites` |
| 7 | 后台任务（存凭证、等结果、失败推送）的回复语言统一经 `speaking()`：在换身份之后设；没带语言时按 LINE 现取（不是后台任务默认的站点语言泰文）；存完原件后再设一次 | `VoucherLanguage.*` |

第 7 条根因没在真机钉死（webhook 那一刻的语言值没有现场记录）；离线覆盖了两种会让它变泰文的情况（没带语言 → 站点默认；换身份 / 存原件时语言被改回）。真机复测见下。

## 文案（四语）

新增两条（`i18n/messages.tsv`）：

| 英 | 中 | 泰 | 日 |
|---|---|---|---|
| This item has expired. Please start again. | 此项已过期，请重新开始。 | รายการนี้หมดอายุแล้ว กรุณาเริ่มใหม่อีกครั้ง | 有効期限が切れました。もう一度やり直してください。 |
| View result | 查看结果 | ดูผลลัพธ์ | 結果を見る |

第一条与 DMS `dms.card.TXT_EXPIRED` 逐字相同。没有改已有文案；"This option is no longer current" 仍用在旧步骤按钮（「原步重问」在今晚之后那批）。

## 有意保留 / 已知限制

- 卡片 120 分钟内跨了会计年度最后一天的午夜：单据日期按放行当天，账套仍是建草稿时冻结的那本（不重选）。
- 菜单里列的「上次之后」按「告诉过没有」记（同一结果的告知标记，7 天），不另存「上次进菜单的时间」。
- 实验栈流程 `erp.line.collection` 的旧卡【确认】改为用出卡时那张卡的按钮数据（带确认号）。

## 要真机看的（交收款接手窗口，三星 LINE）

1. 录到确认卡 → 重新点富菜单「收款」→ 点旧卡【确认】/【撤回】：回「此项已过期…」，新的一笔不受影响，网页「我的收款」里旧那笔是已撤回。
2. 确认卡连点两下【确认】：只回一次、只写一张。
3. 录到一半，点更早那条消息上的【取消】：回旧步提示，当前步不变；点最新那条的【取消】：已取消，草稿作废。
4. 转账发凭证图后那条回复与按钮的语言 = 手机语言（中文手机测一次）。不对的话读 `pearnly:line:user-language:<uid>` 缓存与 webhook 那一刻的语言。
5. 放行后电脑不在线 → 「已记录 · 待写入」带【查看结果】【撤回】；电脑上线写成后手机不收推送；点【查看结果】出 RE 号；或再点「收款」，第一条列出写成的那笔。
6. 写失败（例如方式没科目）→ 收到一条推送；之后进「收款」不再重复列它。
7. 收款卡片的头部颜色是紫色，与采购（绿）销售（红）卡区分。
8. 收款方式按钮：同前缀的转账方式（SCB 储蓄 / 活期）按钮互不相同。
