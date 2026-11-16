# CC 回复：补拉带回的整条直接显示全文（2026-11-14，主会话 local_6e6aa7b0 派）

## 来由
引导式第 1 期窗口跑流程时发现：实验室里（没有实时通道，只靠轮询）CC 对话页的回复偶尔停在半截。调查见共享笔记
「CC对话轮询停在半截-调查-11-14.md」。逐 100 毫秒取样查清的真实情况如下：
- 补拉一次带回的是整条回复，但组件库（assistant-ui `MarkdownTextPrimitive`）的逐字动画默认对所有回复都开着，会把已经到齐的整条从头慢慢画出。
- 这段时间里页面是半截：实测约 0.6–1 秒，机器忙时更久。
- 内容不会丢，没有清空重打，补拉也不会停。
- 线上实时通道在线时，回复本来就是逐字推来的，动画是想要的效果；通道断开、退回轮询时，每条回复都会有这一段先半截再补完。

## 改法
| 处 | 改成 |
|---|---|
| `lib/managed-agents/reducer.ts` | 逐字预览（`event_start`）打开的回复在消息上记 `metadata.custom.streamed` |
| `components/assistant-ui/markdown-text.tsx` | `smooth={streamed}`：只有逐字推来的回复用逐字动画；补拉、重放一次带回的整条直接显示全文 |
| `scripts/flows/lab_agents.py` | 新增 `[lab-idle-delay]N[/lab-idle-delay]`：回一段 30 段的长文字，隔 N 秒才记这一轮结束，中途记一条思考事件；与 `[lab-ask]` / `[lab-tool]` 同用时，作答后的回复同样晚收尾 |
| `scripts/flows/cc_reply_whole.py`（新流程 `cc.reply-whole`） | 普通回复、提问卡作答后的回复各量一遍。每 100 毫秒取字数，断言四条：一出现就是全文、只增不减、服务端有全文后 3 秒内显示且不等这一轮结束、结束后仍是全文 |
| `scripts/flows/cc_lib.py` | 新增 `turnDone()`：等这一轮结束（停止键消失、稳定 1 秒）再读回复。引导式流程里按服务端事件绕过去的那处改回用它（引导式分支，由主会话转） |

`thread.tsx` 没碰。文案没有新增和改动。

## 验证（基线 origin/main a145e0eb6）
- 新流程改前（main 原样前端）红：两种回复在电脑、手机都出现「有字但不全」的中间状态，约 1 秒（从 66 字一路长到 1205 字）。
  改后绿：两种回复一出现就是全文（1205 / 1346 字），电脑、手机都过。
- 单测：`test_assistant_ui.py::test_only_a_reply_streamed_by_previews_is_typed_out`（逐字推来的记标记，补拉整条、重放不记；`markdown-text.tsx` 按标记开动画）。
- 受影响流程（本机实验栈 pearnly-uos-lab）：`cc.chat`、`cc.operator.replay`、`cc.send-unblocked`、`cowork.cc.send-unblocked`、`cc.attach-delete`、`cowork.cc.ask`，16 格全过。
- 本地全闸门：除本机流程锁那一项（已知问题，修复在 `pearnly/flows-lock-test`：本机另一个 run.py 占着默认实验室的锁）外全过；候选离线与 CI 见交付说明。
- 没测到：实时通道在线时逐字推来的回复照旧逐字出。实验室没有实时通道，这条路只由单测覆盖，要在测试机或线上看一眼。
