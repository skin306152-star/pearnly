# 被电脑拦下、要选一次的单：答过就不再问（11-26）

分支 `pearnly/blocked-question-remembered`，基线 origin/main f18fa1ec8。主会话 11-26 派活：定性为线上原有缺陷、不进今晚一车，进下一批。

## 现象（11-26 真机，testerp 测试环境副本，CO-140489，账套 69EXP\TEST）

待处理 PE2569-000021（PUR-2026-198709）上次被 CC 桌面版以 `STOCK_ACC_GROUP_UNFIT` 拦下（转人工）。CC 对话里说「把 PE2569-000021 录入 Express」，
卡片问存货科目组，选 วัตถุดิบ（DM）提交：ERP Express Choice 已记下（kind=stock_group、subject=账套键、value=DM），
但随后 `pearnly_precheck` 回的这一项 value 仍为空，`pearnly_write_plan` 判 `undecided_items`，CC 反复追问。直写、代录都一样。
归因与证据见共享笔记「已知问题与结论.md」11-26 条：`_with_blocked` / `blocked_needed`（44f4748ff，11-16）引入，已在线上 d8510e61a，
main 未部署提交没动过这条路径。

## 原因

`express/precheck.collect_document` 照写入同一条准备判完、不用问了（DM 已记住），转到 `_with_blocked` → `api.blocked_needed` →
`api._party_blocked`：它**只看这张单上一次写账件的失败码**，码属「要选一次」的三类（`prepare.PARTY_CODES` 对方档疑似重复 / 分店认不准、
`GROUP_CODES` 存货科目组不合格、`SERVICE_VAT_CODES` 服务品进项口径）就把那道题原样补回卡片，不看人在那之后答没答。
补回的题当前值由预选算（存货组只取在用最多的），不读记住的值 → 永远「未定」→ 写入计划不放行 → 没有新的写账件去刷新失败码 → 死循环。
单据页「选一次」（`api.answer`）不受影响：它记下后当场撤掉原件（`replaced`）、重新排一件。

## 修法（按类，三类同一个出口）

`express/api._party_blocked` 算出被拦那几项后经新的 `_unanswered` 过滤：
- 这张单**停下之后**（写账件 `modified`）答过、答的仍是这一项能选的值（`_options`，与单据页「选一次」同一份核对）→ 不再出这道题；
- 于是单据视图 `needed` 为空、`can_retry` 为真：单据页出「再写一次」，CC 直写出口（`cc_tools._route`，写入计划同意后逐张调）照
  「再写一次」（`api.retry`）把原件标 `replaced`、按记住的值重新准备一件，由新的写账结果说话。**不清失败码放行**：原件的原因照旧留在原件上；
- 答在停下之前的（带着这个答案写过、电脑仍拦下）照旧问；记住的值已不在可选项里（组被判不合格、档被删）当没答过；读不到停下时刻照旧问。
- 新增 `express/choices.answered(company, kind, subject)`：库里记着的值与最后一次答的时刻（不含判定范围里临时当作选过的）。
  同一项再答同一个值也刷新时刻（`remember` 照存一遍），被拦后重答一次即算新答案。

**被拦的题一律不带当前值**（`precheck._unset`，推荐项照旧排第一、带依据）：实验室流程第一次跑就查出，被拦的题带着预选值
（存货组取在用最多的合格组）时，对话模式的写入计划按「预选项没答也照预选写」（`write_precheck.contracts.held_back`）放行，
可预选值没被记下、单据视图仍判要答，直写出口（`_route`）不交，等于换一种方式卡住。不带值就要人点一次，点了即记下、时刻晚于停下，下一遍不再问。
主会话要求的「需要补题时当前值先取记住的值」因此**没有照做**：还要补的题只剩「答在停下之前」（带着它写过、仍被拦，预填即上面那种卡住）
与「答的值已不能选」（预填不了）两种。

覆盖的入口：单据页（`api._view` / `answer`）、开始前确认卡片（直写 `write_precheck` 卡、代录预检 `operator.precheck`，都经
`precheck._with_blocked`）、CC 直写出口（`cc_tools._route`）、库存单 / 盘点被拦（`book_group_needed`，同一过滤）。

## 影响评估（简）

- 同类复用：三类被拦单同一个出口一起修；Cowork 的被拦单走自己的推送队列（`push/application`），不经 `api._party_blocked`，本次不涉及。
- 上下游：CC 桌面版、Express 写法不变；新件照原有 `push.send` 准备，载荷带记住的值。
- 共用件：只改 ERP 自有 App（`express/api.py`、`express/choices.py`），不动 pearnly_core。
- 成本：每次判被拦单多读一行写账件 `modified` 与每项一行选择记录（被拦的只有一两项）。
- 文案：无新增 / 改动。

## 验证

- 单测 `tests/test_erp_blocked_question_remembered.py`（7 项，进 `development.yml`）：三类各测「停下后答过 → 不再问、可再写一次」
  「没答 → 照旧问、值为空」「答在停下前 → 照旧问」「答的值已不能选 → 照旧问」；库存单 / 盘点同一过滤；其余停法与读不到时刻不变；
  `choices.answered` 读库里的值不读临时值。**去掉修法 4 项失败。**既有 `test_erp_express_direct`（222）、`test_erp_write_precheck`、
  `test_erp_cc_chat_push`、`test_erp_stock_entry_guide`、`test_erp_mrerp_direct`、`test_express_operator_code_cc` 全过。
- 对方档、服务品进项两类：单测按同一套替身复现了「答过仍补题」（去掉修法即失败），**真机未复现**（只有存货科目组在真机上见到）。
- 页面流程 `erp.cc.blocked-answered-once`（新）：备数造一张被 `STOCK_ACC_GROUP_UNFIT` 拦下的采购单；直写的开始前确认卡片与代录预检
  答前问、答后不问，写入计划放行；直写出口照再写一次重新准备（原件 cancelled/replaced、新件载荷带 DM）；代录会话页面那张卡不再是提问卡。
  直写、代录各造一张（代录预检不接直写交出的单：`already_queued`，所以代录那张用代录交）。
  运行（借 pearnly-cov1124-lab，源码按本分支覆盖，`--jobs 1`）：第 1 次发现被拦题带预选值的卡住（上文），加 `_unset` 后 **1/1 通过**（16:47）。
- 真机：用 PE2569-000021 / 000019 复验，等主会话今晚部署完通知后再排（要切测试环境）。
