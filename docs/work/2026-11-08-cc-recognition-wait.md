# CC 识别挂在服务端等时：② 挂起就推进度、③ 不锁输入框（11-08）

来源：11-08 上线验收第 7 项（testcowork 直写对话传 12 页 BJC「识别并录入」）。自动继续通过；两处与 11-06 设计不符，主会话交本窗口修：
② 挂着的 5 分钟里状态行一直「运行中」，没有「识别中 · 共 12 页」也没有页数；③ 挂着期间发送键一直是停止键，消息发不出去。
线上证据：共享笔记 `上线验收-11-08/7a–7c`、`7-逐10秒记录.txt`。分支 `pearnly/cc-recognition-wait`，两件分开提交。

## ② 挂起那一刻就推进度

### 取证（本机实验栈真识别 1 次，6QAA 评测密钥，约 0.57 美元；临时日志只在实验栈，测完删栈还原）

| 时刻 | 服务端 | 推没推 | 页面（实验栈没有实时通道，每 6 秒拉一次） |
|---|---|---|---|
| +1 秒 开始读 | `reading_started` → `waits.touch` | 推了，但还没有挂着的记录（rows=[]），没人收 | 「正在识别单据」 |
| +90 秒 挂起 | `waits.park` / `hold` | **不推** | 下一次拉事件现算 0/12 →「识别中 · 共 12 页」 |
| +145 秒 第一段读完 | `pages_reached(8)` → `touch` | 推 `pearnly.progress` 7/12 | 「识别中 7/12 页」 |
| +4 分 15 秒 读完 | 交回 | — | CC 自己接着做 |

- 结论：挂起之前推的没人收、挂起时不推。靠推送为主、拉得稀（8–30 秒）的线上页面，这一段必然空着。「读到新页就推」实验栈通，内容正确。
- 前端行为测试按线上调用顺序（先 `cowork_intake_options` 再识别）复现：进度一到，状态行就是「识别中 · 共 12 页」，页面显示没问题。
- 只读核对（主会话要求）：Cowork 识别进程 `cowork-ocr-worker` 与 `cowork-web`、`cowork-socketio` 挂同一个 `sites-cowork` 卷、读同一份站点配置，
  `site-step.py` 把每个产品的 `redis_socketio` 设成与队列同一个 Redis 库；识别进程里 `waits.touch` → `frappe.publish_realtime` 与网页发的推送
  走同一个通道，配置上没有分岔。

### 修法
- `toolrun._finish` 挂起并提交后调 `waits.announce(执行记录)`：以会话主人身份现算这条记录此刻的进度，推一条 `pearnly.progress`。
  读到新页的推送（`pages_reached` → `touch`）不变。
- 测试：`tests/test_assistant.py::WaitTests::test_parking_pushes_the_current_progress_at_once`（修前红：挂起时 0 条进度推送；修后绿）。

### 上线后待复验（主会话 11-08 定）
- 线上那 5 分钟里连「读完第一段」的 7/12 也没显示，实验栈复现不出来。随批部署后线上验收本来要真跑一次：届时在页面上记下每次拉事件的
  返回（有没有 `pearnly.progress`）、有没有收到实时推送，一次查清。费用到时由主会话批。

## ③ 识别挂在服务端等时不锁输入框（用户 11-08 定方案 B）

口径：模型空闲就显示「发送」，模型在工作就显示「停止」。识别在服务端挂着等时模型没有在工作，算空闲：只显示发送、输入框不锁，
状态行照常显示「识别中 · 共 N 页」。模型真在回复或执行步骤时照旧只显示停止。

### 根因
- 直写、代录是同一个输入框（`thread.tsx` 的 `ComposerAction`），按 `thread.isRunning`（= `isBusy`）换停止键；`ComposerStop` 从 11-04 起没人传。
- 服务端运行状态把挂着的调用报成「本站正在执行」（`contracts.run_state` 的 `executing`），页面 `applyRunStatus` / 执行记录推送据此算在忙。
- 11-07 记的「代录挂着时发消息 13 秒有回复」是直接调接口，不算数（已记进共享笔记「已知问题与结论」：界面类验收必须在界面上点）。

### 修法（服务端与页面一套，直写与代录同一处）
- 服务端：`contracts.tool_run_event` 加 `waiting`；`run_state` 加 `waiting`（停着等的调用里本站正在执行、又挂着等的）；`sessions` 取执行记录时
  带 `wait_until`，事件里的执行记录、运行状态都标出挂着等的；`toolrun._finish` 挂起时推一条带 `waiting` 的执行记录。代录会话引擎停着等时
  走同一个 `_run_status`。
- 页面：`reducer` 记 `waitingIds`（推来的执行记录、运行状态读回两路），挂着等的不算在忙；引擎重新开跑、这一步有结果、停止、会话结束时清掉。
  `turn.ts` 不在忙时有挂着等的那一步就照常说它（进度照 `liveProgress`）。桌面版「回复完了」通知按「在忙或有挂着等的」判，挂起不误报。
- 发消息：`sessions.send` 先 `waits.release`（按「还在识别」交回挂着的调用）再收这条，原有逻辑不变。

### 测试
- 服务端：`tests/test_assistant.py`（运行状态 `waiting` 只认本站正在执行的；挂起推带 `waiting` 的执行记录）。
- 前端：`tests/test_cc_run_indicator.py::test_a_call_waiting_on_the_server_is_idle_but_the_line_still_says_the_step`（修前红：挂着等时
  `isBusy` 为真；修后绿）。
- 界面流程（在页面上点发送）：`cowork.cc.send-while-waiting`（直写）、`cowork.operator.send-while-waiting`（代录），电脑与手机各一格。
  修前 / 修后两遍结果见下。

## 结果（本机实验栈 pearnly-sendwait-lab，假智能体 + 只排队的识别）

- 界面流程「修前」（③ 改动前的代码，`~/pearnly-devtools/flows-sendwait-red-final.json`）：四格（直写、代录 × 电脑、手机）都在调用真正挂起后
  （100 秒）看，输入框右边一直是停止、打的字点不出去，状态行「排队中」。**全红**。
- 界面流程「修后」（`flows-sendwait-green-direct.json`、`flows-sendwait-green.json`）：四格挂起后（服务端运行状态出现 waiting）输入框右边是
  发送、没有停止，状态行照常「排队中」；在界面上打一句点发送，这句进了对话，服务端先交回挂着的调用再收这句，假智能体回了这句。**全绿**。
- 施工中踩到的一处：挂起那条执行记录与挂起前那条同调用同状态的 running 编号相同，页面按编号去重把它丢了（第一遍修后仍红）。编号另带
  `_waiting` 后通过；前端测试改成按服务端真实编号先来一条 running、再来挂着等的那条。
- 口径说明：调用挂起之前，推送线程同步等识别最多约 90 秒，这段模型在执行这一步、服务端也收不了消息，照方案 B 显示停止；挂起后才算空闲。

## 11-09 测试机验收 ② 仍没过：页面兜底把「别处在执行」当成了结果

现象（CC 1.12.19，testcowork 直写 BJC 12 页，执行记录 ibonkc1do9，17:31:14 开始、17:32:49 挂起、17:34:43 完成；截图
`~/pearnly-devtools/release-1108/shots/06–10`）：第 28 秒工具标题就成了过去时「识别了单据」，状态行一直「x 秒 · 执行中…」；挂起后状态行整行消失，
「识别中 · 共 12 页」一直没出。

取证：
- 线上那段会话的引擎事件（只读取回）：17:31:13 `agent.custom_tool_use` → `status_idle`（requires_action）→ 17:34:48 本站交回
  `user.custom_tool_result` → CC 回话。中间引擎什么都没发，第 28 秒的变化不是引擎来的。
- 服务端：推送线程已在执行这次调用时，页面兜底的 `run_tool` 当场回 `{status: "running"}`（`toolrun.execute`：别处已在执行）。
- 页面：`session-controller.ts` 的 `answerPendingCustomTools` 除 `awaiting_approval` 外一律并一条空的乐观 `user.custom_tool_result`。这一步
  提前「有结果」：标题按有结果说过去时；状态行认为这一步已有结论，退成「执行中…」；挂起后 `turn.ts` 的 `waitingActivity` 要这一步
  还没结果才说它 → 整行消失，进度推送到了也显示不出来。11-08 那条前端测试把兜底设成永不返回，正好盖住了这一路。

修法（分支 `pearnly/cc-wait-progress-shown`）：兜底回 `running` 时不并结果，照推来的执行记录并一条 `pearnly.tool_run` running（不再欠、
不再提醒），这一步仍是进行中。测试 `tests/test_cc_run_indicator.py::test_the_page_nudge_answered_running_does_not_close_the_step_still_reading`
（兜底当场回 running；修前红在「这一步有了结果」，修后挂起前状态行说这一步、挂起后「识别中 · 共 12 页」）。服务端不改。
