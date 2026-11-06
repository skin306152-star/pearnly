# 需求影响评估：CC 识别完成后自动接着做、识别期间如实显示进度

> 基线：`pearnly/cc-ocr-auto-continue-171c12` @ `dcd7ade50`（只读勘察，未改代码）。不进 11-06 晚部署，走下一批。

## 0. 需求原文与理解

- 原话（用户 11-06，测试机实测）：「cc 如果是识别中会报识别已开始，进行中，这个时候后台可能正在识别，但是识别完了，不主动继续，需要问，问的识别它报已经识别完了」。同类：代录卡片识别期间一直「0 / 12 页」（12 页约 3–4 分钟），用户以为没发出去，又重传两次。
- 理解：在 CC 对话里交单据识别，识别超过工具等待时限后 CC 只说「识别已开始」就停；识别完成后要 CC 自己接着做原本的事（出开始前确认卡片、录入）；识别期间进度只显示真实数字。
- 不在范围：识别速度本身、重复上传查重（口径：重复上传照常识别扣费）、代录执行电脑那一段。

## 1. 现状（证据）

| 环节 | 现在怎么走 | 位置 |
| --- | --- | --- |
| 直写对话交识别 | CC 调 `cowork_recognize_attachments` → 交共用识别队列 → 工具在推送线程里最多等 **90 秒**（`RECOGNIZE_WAIT_SECONDS`），没完就把「还在识别 + 批次号」交回 CC | `apps/pearnly_cowork/pearnly_cowork/assistant/tools.py:1174`、`:1432` |
| CC 这一轮 | 拿到「还在识别」→ 回一句「识别已开始」→ `end_turn`，一轮结束、推送线程收线 | `assistant/relay.py` `finished()` |
| 识别完成 | 结果入库后推 `pearnly_ocr_work`（给提交人）、调 `cowork_ocr_recognized` 钩子（LINE 推卡）。**没有任何东西通知这段对话**；对话提醒（`notices`）按设计也不进引擎（「写进去会开一轮、要花钱」） | `ocr/execution/workflow.py` `_announce`；`pearnly_core/execution/work.py:232`；`assistant/notices.py` |
| 唤醒机制 | 只有引擎回调（`webhook`）：会话停下等 **Pearnly 工具结果** 时由服务端执行工具。会话已 `end_turn` 时没有任何唤醒 | `assistant/webhook.py` |
| 代录上传卡片进度 | 卡片读 `pearnly.progress`（`reading_pages`，done/total=页），数来自 `store.pages_done` 缓存，由 `store.pages_reached` 写 | `express_operator/feed.py:228`、`ocr/execution/store.py:90` |
| **页数为什么不动** | `pages_reached` 只有谷歌 / Cloud Run 路线调用；**生产走的 Claude 路线（甲骨文 `recognize_original`）完全不报页**，所以读完前一直 0。Claude 按段读，每段最多 8 页（`PAGES_PER_CALL=8`）：12 页 = 2 次调用，第一段读完时本来就有真实的「已读完几页」，只是没报出来 | `ocr/execution/workflow.py:104-128`、`claude_ocr/segments.py:122` |
| 前端补拉 | 代录事件源注释说「页面收到 `pearnly_ocr_work` 即补拉」，但 `frontend/assistant` **没有订阅它**；识别完成后卡片靠低频补拉（空闲 30 秒一次）才变 | `frontend/assistant/src/lib/managed-agents/poll-source.ts:38` |
| Cowork / ERP | 对话里交识别只有 Cowork（直写工具 + 代录资料区 `intake`）；ERP 版 CC 没有对话内识别工具，也没登记代录 `uploads`，ERP 单据在采购 / 销售页识别。框架在共用层，ERP 以后接入同样生效 | `pearnly_erp_portal/hooks.py:618` |

引擎约束（Managed Agents 官方说明）：会话停在 `requires_action`（等自定义工具结果）时**只收「交结果 / 停止」**，此时发 `user.message` 返回 400；没有写明等待时限（本仓「允许」卡片、提问卡片停几小时后再答是现成先例）。

## 2. 修法（一次到位，直写与代录共用）

**A. 识别没完成时，那次工具调用「挂着」，识别完成再交结果（不伪造用户消息）**
1. 共用层 `toolrun` 加「后交结果」：工具返回「等这些外部事情」（如 `ocr:<批次>`）时，执行记录照旧 `running`、记下等待键，**不交回引擎**。会话停在 `requires_action`、不开模型、不花 token；页面按现有「本站正在执行」显示这一步在做（`contracts.run_state` 的 `executing`）。
2. 识别结束（成功、不是单据、失败都算）时，识别队列收尾处调共用 `toolrun.settle(键)`：找出等这个键的执行记录，键都结束了就按原工具同一取数（`_result`）生成结果，抢占（`UPDATE … WHERE status='running'`，只交一次）后交回引擎 → **CC 在同一轮里接着做原本的事**（出确认卡片、录入）。页面开着时现有 `_follow` 读到「在跑」就接上推送线程，回复实时出；页面关着 CC 照样做完，下次打开就在。
3. 兜底：收尾钩子漏了（进程重启）→ 页面读运行状态时顺带检查挂着的记录，键都结束就同样 `settle`（幂等）。
4. 挂着时用户又发消息：先把挂着的调用按现在的口径交回（「还在识别，批次 X」），再发这条消息——避免 400，行为退回今天的样子；用户点停止照旧打断，事后识别完成找不到等待记录就什么都不做。
5. 90 秒内识别完的照旧同一轮拿到结果，不走挂起。

**B. 进度只用真实数字**
1. Claude 路线报页：`segments.read_all` 加可选回调，每段读完报「已读完到第几页」（下一段起始页 − 1，读完 = 总页数）→ `store.pages_reached`，与谷歌路线同一个缓存、同一个 `pearnly_ocr_work` 推送。8 页以内只有一次调用，中途没有可报的真实页数，就不编。
2. 文案：已读页为 0 时不写「0 / 12 页」，写「识别中 · 共 12 页」（加对话状态行已有的计时，计时是真实耗时）；有真实页数才写「识别中 8/12 页」。代录上传卡片、直写对话步骤、状态行同一个 `progressText`。
3. 直写对话的识别步骤也出 `pearnly.progress`（按调用编号，页数变了随 `pearnly_ocr_work` 推、重放时按缓存现算），状态行在没有整段进度时读正在执行那一步的进度。
4. 前端订阅 `pearnly_ocr_work`：收到就补拉一次（代录卡片、直写步骤识别完成即刻变，不等 30 秒）。

## 3. 按类通用

- 一类：「对话里交给后台、几分钟后才有结论的工具」。识别先接；同一机制以后可接推送等写账结果（现在 `PUSH_WAIT_SECONDS` 也是等到时限交回「还在排」）——本次不改推送，只把机制做成通用。
- 报页改的是 Claude 识别共用路线：录入工作台等候页、LINE 收单、代录卡片的「0/N 页」同时好；ERP 识别若走同一 `segments` 也受益（ERP 页面是否显示页数不在本次改）。

## 4. 上下游

- 不改识别结果、扣费、存档、推送任何口径；`settle` 只读识别结果、交回引擎。
- CC 桌面版载荷不变；Express 不涉及。
- Cowork、ERP 共用同一套对话与执行记录；ERP 无对话内识别入口，暂无调用方。

## 5. 隔离与权限

- `settle` 以执行记录里的会话主人身份取结果（同现在推送线程、引擎回调），按批次原有权限（`access.require_batch`）读；只交回登记了这个键的那段会话。

## 6. 入口

- 网页 / 手机 CC 对话页、代录上传卡片、录入工作台等候页（页数）。LINE、拍照不涉及界面改动。

## 7. 共用件与连带回归

`check-impact.py` 命中：**ocr**（9 个入口）、**assistant**（9 个入口）、**cc-composer**（2 个入口）。重点实测：`cc.assistant`、`cc.operator.page`、`cowork.cc-chat.push`、`cowork.cc.troubleshooting`、`shared.ocr.pipeline`、`cowork.ocr`（录入工作台页数）。新增离线测试：挂起 → 识别完成交回一次；失败交回；多附件全完才交；挂起时发消息先交回再发；停止后完成不交；重复 `settle` 只交一次；Claude 分段报页。

## 8. 成本（实数估算，CC 默认 Opus 5.5：输入 $4 / 缓存读 $0.20 / 缓存写 $5 / 输出 $20 每百万 token）

- 挂着等识别：会话空闲，**0 token**。
- 唤醒一次 = CC 收到结果后的那一次模型请求：上下文约 3–4 万 token（Cowork 工具说明约 1.6 万 + 系统说明 + 前文，估算），输出几百 token。
  - 5 分钟内识别完（缓存还在）：约 $0.007 + $0.01 ≈ **$0.02（约 ฿0.6）**；
  - 超过 5 分钟（缓存要重写）：约 $0.18 + $0.01 ≈ **$0.19（约 ฿6.6）**。
- 对比现在：CC 先回「识别已开始」一次 + 用户再问一句后 CC 查结果再继续，至少多 2 次请求。**改后每次净省**，不新增固定月费。
- 实测：一份 12 页 PDF 小样本（识别约 ฿2.4）+ 几轮 CC，在测试费用上限内。
- 待核（施工时读账单）：Managed Agents 会话空闲等待是否另计运行时长。

## 9. 删除、作废、追溯

- 挂起状态在执行记录里（加等待键一列），结果照旧写 `result`；停止 / 删对话按现有作废逻辑收尾，不留悬挂记录。

## 10. 文案与官方 UI

- 新增 1 条（三语）：「识别中 · 共 {n} 页」（`run.readingTotal`），过 `check-copy.py`。不改已有文案；不加说明文字。

## 11. 测试与验收

- 离线：上面第 7 栏的用例 + 前端 reducer / `progressText` 单测。
- 本机实验栈：Cowork 直写对话交一份 12 页 PDF，关页面 / 开页面各一次 → CC 识别完自己出确认卡片；代录资料区传同一份 → 卡片「识别中 · 共 12 页」→「8/12」→ 完成即刻变。
- 测试机真机：下一批随批验收，由测试窗口照做（今晚测试机不动）。

## 12. 要问主会话的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 识别挂着时用户又发消息怎么办 | 先按「还在识别」交回、再发消息（不锁输入框） | 不定就得锁输入框，等识别期间用户不能说话 |
| 2 | 新增文案「识别中 · 共 {n} 页」 | 照此（新增文案，不改已有） | — |
| 3 | 机制做成通用、本次只接识别 | 照此；推送等结果以后有实测问题再接 | — |

## 13. 工作量与排期（机器耗时）

1. 共用层挂起 / 交回（`toolrun`、执行记录加列、`sessions` 发消息前交回、读状态兜底）+ 离线测试：写约 1.5 小时，测约 15 分钟。
2. Claude 分段报页 + 识别收尾调 `settle`：约 40 分钟。
3. 前端：订阅 `pearnly_ocr_work`、步骤进度、文案：约 1 小时，构建与单测约 10 分钟。
4. 本机实验栈实测（含小样本付费识别）：约 40 分钟。
5. 合入后随下一批部署，测试窗口真机验收。

## 14. 审定与施工记录

主会话 11-06 审定：A + B 同意；三件事按建议（挂着时发消息先交回再发、新增「识别中 · 共 {n} 页」、机制通用只接识别）；
补两条：挂着要有上限（定 15 分钟：12 页约 3–4 分钟，排队高峰留余量；到了交回「还在识别」、结束这一轮；点停止立刻作废）；
测试覆盖成功、失败、超时、中途发消息、漏通知补交，还原修复就失败。不进 11-06 晚这批，随下一批合入。

| 改动 | 位置 |
| --- | --- |
| 挂起 / 交回 / 作废 / 每分钟巡检（到上限交回、漏通知补交）/ 进度 | `apps/pearnly_core/pearnly_core/assistant/waits.py`（新）、`toolrun.py`（`_finish` 挂起不交回、`cancel_waiting` 连带作废）、`sessions.py`（发消息前交回、读事件并进进度）、`hooks.py`（每分钟 `waits.sweep`） |
| 执行记录加 4 列（`wait_keys` / `wait_check` / `wait_remote` / `wait_until`，后者带索引，只扫挂着的） | `pearnly_assistant_tool_run.json`；列名进 `i18n/messages.tsv` |
| 执行记录推送带 `settled`（页面马上补拉、接上推送线程） | `contracts.tool_run_event` |
| Claude 分段读每段报真实已读页数 | `claude_ocr/segments.py`、`claude_ocr/client.py`；Cowork `ocr/execution/claude.py`、`workflow.py`；ERP `recognition/claude.py`（同一回报接到 ERP 已有的按页进度） |
| Cowork 识别工具：没识别完就挂起；核对函数；识别结束钩子；读到新页 / 开始读推进度 | `pearnly_cowork/assistant/tools.py`、`ocr/execution/store.py`、`hooks.py`（`pearnly_ocr_finished_handlers`） |
| 前端：交回时马上补拉、识别推送补拉（2 秒内一次）、状态行读正在执行那一步的进度、「识别中 · 共 {n} 页」 | `poll-source.ts`、`progress.ts`、`turn.ts`、`thread.tsx`、`upload-state.ts`、`events.ts`、`i18n.ts` |
| 实验室假引擎：`[lab-tool]` 模拟任意工具调用；等工具结果时照真实引擎拒收用户消息 | `scripts/flows/lab_agents.py` |

给模型的识别工具说明改了一句（「等到识别完再交回，接着做」），`tests/fixtures/assistant/cowork-push-tools-golden.json` 按说明重写（只此一项不同）。
`tests/flows/behind_baseline.txt` 去掉 `ocr/execution/store.py`（现在已在流程覆盖范围内）。

前端 CC 只有中英泰三语；「四语」的第四种（日文）只在 Desk 术语表里，新列名已带四语。

### 验证（11-06 晚，基线 `f34e084a4` 之上的工作树）

- 离线：`tests/test_assistant.py` `WaitTests`（挂起不交回、识别结束交回一次、失败照交、到上限交回「还在做」、核对出错到上限照交挂起时的结果、巡检补交漏的结束提醒、
  发消息先交回、点停止作废后不再交、只交等这个键的、进度只用真实数字、不挂起的照常当场交回、巡检每分钟登记）；`tests/test_cowork_assistant_tools.py`
  `RecognitionWaitTests`（没识别完就挂起、核对函数的真实页数与交回结果、提前交回的说明、结束提醒按识别任务号找、Claude 两条路线报页并推进度）；
  `tests/test_claude_ocr_segments.py`（24 页报 7/14/21、单段不报、回报出错不影响读）；前端 `tests/test_cc_run_indicator.py`（交回时马上补拉、识别推送 2 秒内补拉一次、
  关了不听；状态行读正在执行那一步的进度、「识别中 · 共 12 页」）、`tests/test_cc_run_ui.py`（上传卡片不写「0/12」）。
- 闸门：本地按 `development.yml` 全跑 196 项，首轮 5 项不过（流程覆盖基线、黄金样本、翻译包、共用件调用方 ×2），改后逐项重跑通过；
  `cand-local.sh offline`（线上同版镜像 `fb1037ad…`）通过。
- 本机实验栈（`pearnly-ocrwait-lab`，回归实验室编排，假引擎 `[lab-tool]`，识别没有 ocr 进程、只排队）：Cowork 对话传 PDF、假 CC 调
  `cowork_recognize_attachments` → 推送线程等约 90 秒后挂起（执行记录 running、运行状态 `executing`、进度事件 `queued 0/1`）。
  五种结束方式：识别结束（共用队列 `work._finish` 落定）→ 短任务交回 → 假 CC 回「[lab-agent] cowork_recognize_attachments …not_invoice…」；
  挂着时发「还要多久」→ 先交回（带 user_message 说明）再发，引擎不拒、两段回话；到上限 → 巡检交回（带 deadline 说明）；点停止 → 作废，
  之后识别结束不再交回、没有回话；结束提醒漏了 → 巡检补交。实验栈已 `down -v` 清掉。
- 没测到：真引擎（Managed Agents）与真识别（Claude 12 页）——测试机今晚另有安排、实验室没有识别凭据，留给下一批随批在测试机 / testcowork 验收
  （付费识别只跑一份 12 页小样本）；Managed Agents 空闲等待是否另计运行时长，施工后读账单核。
