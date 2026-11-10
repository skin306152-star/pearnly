# 识别挂起之前那 90 秒也有页数（11-10 线上压测问题 3、5 的剩余部分）

分支 `pearnly/cc-inline-recognition-progress`，基线 origin/main a810621e1。主会话 11-10 派（C 上传线），这一项的 toolrun / waits / Cowork
识别工具归本线；A 线接手窗口 11-10 确认 `recognize_attachments` 的等待循环两个排号分支都没动。

## 现象与证据（生产 81ec70ae3，压测 trail 每 10 秒一行）
- S1 直写 7 份 102 页：第 38 秒步骤已成过去时「อ่านเอกสารแล้ว」，状态行只有「N 秒 · กำลังดำเนินการ…」，一直到 3 分钟。
- S5a 代录 25 页：第 27 秒步骤成过去时，工具记录却还是 running；第 114 秒（当场等满 90 秒、挂起）状态行整行消失，发送键可点。

## 两个原因
1. **主因**（已在 `pearnly/cc-wait-progress-shown` 修，前端，排号中）：页面兜底把服务端回的 running（别处在执行）当成结果，并了空结果，
   这一步提前结束。挂起后整步算完、模型空闲，状态行就没了。代录与直写用同一个工具 `cowork_recognize_attachments`、同一条挂起路径，一并修好。
2. **本分支**：推送线程里识别工具先当场等最多 90 秒（`RECOGNIZE_WAIT_SECONDS`）再挂起，而进度只推给挂着的执行记录（`waits.touch` /
   `announce`）。这 90 秒即使步骤保持进行中，状态行也只能写「识别中」，没有页数；102 页那份 90 秒里约读了一半。

## 改法
- `toolrun._finish`：执行上下文多带这次调用的编号 `tool_use_id`。
- `waits.report(progress)`：工具当场等时按这次调用推一条 `pearnly.progress`，与挂起后的进度同一个写法、同一个编号（`_event`），
  挂起那一刻 `announce` 推同样的数，页面按编号只收一次。不在对话的工具执行里、数不对就不推；出错不影响工具。
- 补拉也带上：`report` 把最近一条另记在缓存里（5 分钟）。对话页重开、实时通道断了靠轮询时，`progress_events` 把还在执行、没挂起的
  调用的那一条并进来；执行完了就不再带。起因：第一遍流程里当场等那段看不到页数，因为实验栈没有实时推送服务（没有 socketio），
  页面只靠轮询，而轮询原来只现算挂起后的进度。生产上页面刷新一下或实时通道断开，也会碰到同样的缺口。
- Cowork `recognize_attachments`：当场等的每一轮（3 秒）按 `_reading` 算一次已读页，变了才报。`_reading` 也是挂起后核对
  `recognition_wait` 用的那一个（识别完的按整份算，还在读的按已读页）：两段说的是同一个数。
- 前端不改：状态行按进行中那一步的调用编号取进度（`thread.tsx` `liveProgress`），挂没挂起都一样。

## 验证
- 先红后绿：`test_assistant.WaitTests.test_a_tool_waiting_in_place_reports_the_pages_read_so_far_for_its_own_call`（旧代码 KeyError，
  上下文里没有调用编号）；`test_cowork_assistant_tools.RecognitionWaitTests.test_waiting_in_place_reports_the_pages_read_so_far_each_time_they_change`
  （旧代码一次进度都没报）。改后两份全绿（test_assistant、test_cowork_assistant_tools 250 条）。
- 两条老断言按字面核对执行上下文，跟着加上 `tool_use_id`。

## 上线后要人验
两个分支（本分支与 `cc-wait-progress-shown`）都上线后，在 testcowork 放一份 20 页以上的扫描 PDF：直写说「录进 Express」、代录同样放一份。
状态行从「识别中」很快变成「第 x / y 页」，挂起后照常走、不消失，到识别完 CC 接着说。线上压测脚本 S1 / S5a 可以原样重跑来核。
