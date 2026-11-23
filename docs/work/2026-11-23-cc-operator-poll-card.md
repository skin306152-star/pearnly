# 代录开始前卡片：推送丢了、补拉也不出（11-23）

主会话 local_0d36f9f2 派（11-23）。分支 `pearnly/cc-operator-poll-card`，工作树 `.claude/worktrees/cc-operator-poll-card`，基 main 7d935d206。

## 现象

cc-1124 f9450e7c0 的 Flows（run 45156961702，分片 1/2，jobs 3）里 `cc.operator [old / mobile]` 不通过（69.9 秒）：
「代录事件源出『开始前』卡片」seen `{card: false}`；「资料那一步」`no sources step`。同一次运行 `[old / desktop]` 30.4 秒通过；
main 7d935d206 同格通过；Flows 自带的 finish 补跑 43.3 秒通过。本分支不碰代录对话代码（cc-1124 只改 MR.ERP 连接与 CC 桌面版）。

证据（CI 只留 bench 日志最后 300 行，失败那格前半段没有服务端日志）：页面 10:40:07 / 13 / 19 / 25 每 6 秒补拉一次
`events?after=sevt_…120`（锚点不变；实验栈不起 socketio，页面一直只靠补拉，6 秒是 `SLOW_MS`），卡片仍不出；直到
10:40:27 第二句发出后，卡片组件才去取 `documents` / `precheck`——卡片这时才画出来。

## 根因

本机回归实验栈（main 7d935d206，jobs 3，同 CI 分片的 5 条流程）4 轮 64/64 全过，**没复现**。由代码确认两条会造成这个现象的缺口：

1. **第一句时刻记不上只留日志**（`sessions.send` → `express_operator.tasks.message_sent`）：卡片要任务的 `message_at`
   才出（`feed.project`），推送也只在它保存时发。它在 `send` 的同一笔事务里做：那笔事务前面已有一致性读，站点库
   MariaDB 11.8 开着快照隔离，`message_sent` 锁定读任务行时这行如果在读视图之后被别的请求改过并提交，就报 1020
   （Frappe 抛 `QueryDeadlockError`）；原来 `except Exception` 只记 Error Log，之后的补拉也不出卡，下一句发出时再记一次才出
   ——与失败那格的时间线一致。附件交出（`files.commit`）是同一写法。
2. **补拉读不到引擎时把本站事件整页丢了**（`sessions.events` / `_operator_events` 的 partial 分支）：只回提醒，执行记录、
   写入计划、引导运行、等后台结论的进度、被停掉的半段回复、代录事件源（资料、开始前卡片）都不给。引擎「列出事件」连着几次
   读不到时，卡片就一直不出。

## 改法

- `sessions._mark_after_send`：消息发出之后才记的事（代录第一句时刻、附件交出）先提交已有写入，再在自己的事务里经
  `transaction.conflicts.retried` 做（1020 / 1213 整笔回滚、退避重来，共 4 次）；用尽或别的错误回滚、只留日志，不当发送失败。
  `message_sent` 记过不改、`files.commit` 可重入，重来安全。
- `sessions._partial_page`：补拉读不到引擎时，提醒之外照给与正常增量同一套本站事件（`_with_tool_runs` 一并带的执行记录、
  写入计划、引导运行、等待进度；直写会话加被停掉的半段回复，代录会话加代录事件源），没有锚点时间就取最近 30 分钟，页面按编号
  去重；这页仍标 `partial`，页面不拿它当锚点、不收缺口（`poll-source.ts` 不改）。
- 整段重放（没有锚点）读不到引擎仍报错、不改：页面首次加载只拿到一部分事件会带来排序问题，不在本次范围。
- 按类排查：推送与补拉都出的本站事件就是上面这几路（`_with_tool_runs`、`notices`、`stopped`、`feed`），都已并进 partial 页。

## 诊断与流程

- 流程运行器 `scripts/flows/run.py`：每格多记一条「server errors in Error Log (record only)」——这一格期间服务端新增的
  Error Log 标题（只记录不判）；不通过的格打印时排在最前。下次再红能直接看到服务端那一侧。
- `cc.operator` 加一格「实时通道连不上、页面只靠补拉：回复收起后开始前卡片照样出」（记 `realtime`、`cardMs`）。
- 单测 `tests/test_assistant.py`：partial 页带本站事件（直写、代录两种，最近 30 分钟窗口）；发出后才记的撞 1020 重来、
  用尽只留日志。两条在改前的代码上红、改后绿。

## 已知问题第 92 条（jobs 3 并行时 cc.operator 偶发资料 0 张、侧栏删除找不到行）

症状不同（资料 0 张是站点这家等着录的单为 0，侧栏删除找不到行），不像同源。本次在实验栈 jobs 3 并行跑 cc.operator 与
cc.chat 等共 7 轮（改前 4 轮、改后 3 轮，cc.operator 28 格），都没出现「资料 0 张」或「删除找不到行」，未复现、无法判定同源，
第 92 条保持原结论。

## 没做的

- 读的时候按第一句时刻兜底出卡（`message_at` 一直记不上时）：增量补拉拿不到第一句时刻，要再读一次引擎；重来上限内记上 +
  partial 页带卡片已覆盖两条路，用尽的情况只留日志。卡片编号固定（`pop_<任务>_start`），同一任务只会有一张。
- 「引擎读不到」「第一次记不上」两种情况的流程格：实验栈不能在页面拿到锚点之后、卡片出现之前定点让引擎列事件失败或让任务行
  撞 1020，这两路由单测覆盖（改前红、改后绿）；流程格只加「只靠补拉也出卡片」。

## 验证（3684e7ffa，基 main 7d935d206；本机回归实验栈挂本工作树）

- 单测：`tests/test_assistant.py` 266 通过（新两条改前红、改后绿）；`tests/test_express_operator_feed.py` 23、
  `test_cowork_assistant_tools.py` 270、`test_cowork_folder_intake.py` 128、`test_cowork_folder_task_batch.py` 33 通过。
- 流程：cc.operator / cc.chat / cc.operator.replay 两轮 24/24；`--changed --base origin/main` 7 条 20/20（cc.chat、cc.operator、
  cc.operator.replay、cc.operator.stop、cc.sidebar-modes、cowork.cc.sidebar-modes、erp.cc.guide.stop）。新格「只靠补拉也出卡片」
  8/8（realtime false，卡片在回复收起后 0–1 毫秒内已在）。
- 新诊断第一次跑就看到一条原有的服务端错误：cc.operator.replay 每格 3 条、cc.sidebar-modes 旧数据集每格 1 条
  「Write precheck: unknown document kind」（不判、本分支不碰预检，另报主会话）。
- 共用件影响清单（`scripts/check-impact.py`，命中 assistant 13 个入口）：
  - 通过（流程实测）：cc.assistant（cc.chat、cc.sidebar-modes）、cc.operator.thread（cc.operator、cc.operator.replay、cc.operator.stop）、
    cc.operator.start-card（cc.operator 开始前卡片）、shared.assistant（对话页打开、各流程页面无报错）、
    cowork.assistant 的侧栏部分（cowork.cc.sidebar-modes）。
  - 没测到（原因：改动只在补拉读不到引擎时与发出后记时刻两处，这些入口的工具 / 业务路径不经过，离线单测覆盖到同一函数）：
    control.assistant、erp.cc.companies、erp.cc.chat-push、erp.cc.stocktake、cowork.cc-chat.push、cowork.cc.troubleshooting、
    erp.cc.collection、erp.page.collections。
- 全闸门 288 ok / RC 0（12fa910a2）；候选离线 LANE EXIT 0（3684e7ffa，之后只改测试与记录）；public-scan 见推送前。
