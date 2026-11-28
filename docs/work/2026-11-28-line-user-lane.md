# LINE 事件按用户一条道（④，11-28）

- 分支 `pearnly/line-user-lane`，基线 `084453d5f`（集成头，等「这一批已进 main」后变基）。
- 方案与定案：共享笔记「并发冲突-④LINE按用户排队方案.md」末尾（主会话 11-28 定 A / B / C / D）；会话根函数见「并发冲突-④施工清单.md」A1。
- 分两期：第一期 = 共用层的道 + 会话根函数 + Cowork `flow.locked` 并入 + 流程；第二期 = 后台路径（L6–L12）外部动作之后加锁重读再合并写回、DMS 身份证识别结果进检查点。

## 现象（修前）

同一个 LINE 用户连发几条，LINE 几乎同时投来好几个 webhook 请求，每个请求在自己的网页线程里当场跑处理器，几路同时读改写这个人的会话：

- ERP 收图：`set_session` 保存前比对 417 → 回「Could not store this document」，这张丢了要重发；
- Cowork：Redis 锁等满 60 秒 → 报错，网页线程被占 60 秒；
- DMS：身份证图与手机号同时到 → 一条 417、事件 failed、没有回复；
- webhook 处理器报错不回滚就提交（K15 / D1）：报错前的半截写入被一起提交。

## 影响评估（摘要）

| 项 | 结论 |
|---|---|
| 同类复用 | 租约写法同 `execution/work.py`（Python 取 UTC、带条件 UPDATE 看 rowcount，同 `assistant/toolrun.py`）；串行块即原 Cowork `flow.locked`，搬进共用层后三个产品一把锁 |
| Frappe 原生 | 两张 DocType + `on_doctype_update` 原生建索引；`request.after_response` 回应发出后起线程；`frappe.enqueue(deduplicate=True)` 兜底；`scheduler_events` cron / daily |
| 按类通用 | 改在共用层 webhook，三个产品所有一对一 LINE 事件同一条路；处理器一行不改 |
| 上下游 / Express / CC | 不碰写账、推送、CC；识别仍各自并发、各扣一次 |
| 隔离与权限 | 道按站点、按 LINE 用户；线程里身份照旧 Guest，处理器按绑定切人；两张表只给 System Manager |
| 各入口 | 一对一的 message / postback / follow / unfollow 全进道；群组等没有用户的照旧当场处理 |
| 共用件 | `line`（加道与两张表）、`line-session`；影响清单见下 |
| 成本 | 零新增：不加队列、不加常驻进程；正常连发推送数不变（只回复）；排队超 3 分钟的真故障才推送一条 |
| 作废删除 | 事件行处理完 7 天删（每天 `prune`），闲置一天的道删 |
| 文案 | 不新增：超时只用 core 已有的「Please try again later」（四语齐） |
| 待问 | 见末节 |

## 设计（第一期）

1. **收件**（`pearnly_core.line.webhook`）：验签（在落行之前，不变）→ 一对一事件 `line_lane.store` 落一行 `Pearnly LINE Event`（queued；用户、到达时刻 UTC、LINE 毫秒时间、imageSet 序号、最小内容）→ 每存一条当场提交 → 每个用户 `start` 一次 → 回 200。事件号唯一约束照旧挡重投。
2. **道**（`pearnly_core.line_lane.lane`）：`Pearnly LINE Lane` 一行一个用户，`holder` + `lease_until`（90 秒）。`acquire` = `UPDATE … WHERE lease_until IS NULL OR lease_until < now`，影响 1 行才算拿到；拿不到直接走。持道的按 `(line_timestamp, set_index, arrived_at, name)` 逐条：开头提交（结束读视图）→ 续租（续不上说明被接走，退出）→ `status='queued'` 条件认领 → 在 `line_session.serialized` 块里跑 `line._dispatch_with_feedback` → 记结论、清内容。道空了放道后再查一次。
3. **在哪跑**：webhook 回了 200 之后在网页进程起一个**非守护**线程排空（空 `contextvars.Context`，`frappe.init(force=True)` / `connect` / `destroy` 自建自关连接）；进程回收时等手上这条跑完（优雅退出时限 120 秒内）。每进程最多 8 条线程，满了放道交短队列 `line_lane.lane.run`。没有 `after_response` 的进程内调用（离线流程、站点脚本）当场排空。
4. **兜底**：每分钟 `sweep`：有 queued / processing 却没人持道的用户交短队列（job_id 按用户固定、去重；单个用户出错不卡整轮）。
5. **不重放，按事实告诉本人**（主会话 11-28）：processing 且租约到点（进程没了）→ 记 failed「interrupted: not replayed」、记数 `lane_interrupted`，不再跑（ERP 收单不按消息号去重，重放会多一条、多扣一次）。
   - 事件行上记「回过本人没有」（`replied`）：处理器每次回复 / 推送给本人**之前**（`line._lane_sent`，含 `reply_sender` 计时线程里的兜底回复；推给别人不算）落标记，宁可多记不可漏记。标记先写 Redis（`pearnly:line:lane-replied:<事件号>`，留一天）：处理器的数据库事务要到处理完才提交，中途进程没了库里什么都不剩；处理完 / 判中断时抄到 `replied`。
   - 中断时没回过 → 真故障，推「Please try again later」一次（去重键按事件号）；回过 → 只记 failed 与日志，不再推。Redis 读不到按「回过」算（宁可少打扰一次，不让办成的事再办一遍）。
5a. **处理中保活**（审查 11-28）：一条事件跑得比租约（90 秒）还久时，持道的人每 15 秒在 Redis 里报一次「还活着」（`pearnly:line:lane-alive:<用户>`，45 秒过期）；`acquire` 先看它，扫描起的新一轮抢不到道，不会把还在跑的这条判成中断、也不会并行跑下一条。
5b. **失败也按事实告诉本人**（审查 11-28）：处理器报错（含串行块等满 60 秒拿不到锁）且没回过本人 → 推「Please try again later」一次；回过的不推。
5c. **进程回收**：gunicorn 按 max-requests 退出时（`threading._register_atexit`，在等非守护线程之前），排空线程跑完手上这条就放道，余下的由下一条 webhook 或扫描接走。
6. **真故障**：排队超过 180 秒才轮到 → 记 expired、记数 `lane_expired`、推送「Please try again later」一次（`X-Line-Retry-Key` 按事件号）。
7. **三个点**：`_dispatch_with_feedback(event, waited)` 从到达算起，排过 0.6 秒的一开始就显示。
8. **失败回滚**（L4）：处理器报错先 `rollback` 再记日志再记 failed（`_dispatch` 不再在 raise 前记日志，改挂 `pearnly_line_handler`）；群组同步路径同样。

### 会话根函数（A1 L1 / L2 / L5）

- `serialized(user)`：Redis 锁（键 `pearnly:line:serial:<sha256 前 24 位>`，60 秒），按 `frappe.local` 计数可重入；块里 `get_session` 是加锁读（行不存在时不加锁，躲间隙锁）。
- `set_session`：锁行后直接 `set_value` 三列，不再 `get_doc` + `save`；本事务读到的版本与锁到的不一样 → 照旧报 `TimestampMismatchError`（不静默盖掉；第二期之前后台写者仍是报 417）。
- `update_session(user, change)`：块内加锁读 → `change` → 写回 / 不写 / 删。
- Cowork `flow.locked` 删除，`flow._save / _clear`、`ask.arrive / touch / step / text` 改用 `line_session.serialized`。部署那一刻新旧进程键不同，是一个很短的窗口。

### 保留期与清理

- 内容（文字、回复令牌、postback 数据）在处理完的那一刻清掉；不存图片本体，文件只存消息号。
- 行留 7 天挡 LINE 重投（重投窗口未核实，先取一周）；排队中 / 处理中的不删，归扫描管。
- 每天 `prune`：每批 1000、最多 10 批，删不完第二天接着删；闲置一天且没有待办的道删掉。

## 共用件影响（`scripts/check-impact.py`）

改到 `line`、`line-session`，清单 145 个入口（大部分只是调 `line.reply / push`，本次没改这些函数）。按本次真正改的路径实测：三个产品 webhook 模拟（`erp / cowork / dms.line.webhook-sim`）、`erp / cowork.line.chat`、`dms.line.chat`、`erp / cowork.line.precheck-questions`、`erp.line.collection`、`erp / cowork.line.card-result`；真机记录行 `erp.line.postback.erp_item`、`erp.line.postback.precheck` 待真机。结果见「验证」。

## 验证

| 层 | 状态 |
|---|---|
| 离线 | `tests/test_line_lane.py` 26 条（失败未回复推一次、失败已回复不推、报活着时抢不到道、进程退出放道，中断没回过推一次、中断已回过不推、回复落标记推给别人不算，顺序、重投、群组、持道、失败先回滚、中断不重放、超时只推一次、放道瞬间新到、被接走就停、回应后起线程、线程满交短队列、webhook 只收件、验签失败不落行、三个点计时、串行块可重入、旧读拒写、块内读最新、update_session）；现有 `run-offline-tests --plain` 65/65、`--image` 45/45、Cowork / DMS 替身跟着改后全过 |
| 闸门 | 架构 0 违规；无上限查询未超基线；未加锁读后写回 `--extended` 新增 0（5 处根内锁读按 A1.3 S0 从基线挪进误报登记，道的 3 处认领是带条件 UPDATE） |
| 实验栈流程 | 本机栈 pearnly-b3（经主会话同意借用），`scripts/flows/line_lane_lib.py`（流程 `erp.line.lane` / `cowork.line.lane`），修前树 `084453d5f`、修后 `0b326ba85` 加脚本修正，结果见下表 |
| 真机 | 未做（上线前三星连发 10 张，约 ฿2，时段问主会话） |

### 实验栈：修前红、修后绿（11-28 晚，pearnly-b3）

一个进程模拟 LINE 平台：每个 webhook 请求一个线程、各自连库调真实 webhook，回应之后跑 `after_response`（修后在这里起道的线程，同线上）；
出站与取图换记录器、识别换假引擎（睡 1–3 秒，零费用）、识别队列 2 路并发（同线上 ocr 池）。跑格期间停掉本栈调度（免得真的后台进程接走格 ④ 的事件）。

| 格 | 修前（ERP / Cowork） | 修后（ERP / Cowork） |
|---|---|---|
| ① 同一人同时发 10 张（ERP 途中点第一张卡「取消」） | 收下 2 / 1 张，其余 8 / 9 张回「存单失败请重发」（日志：会话 417） | 10 / 10：10 条收单、10 个计费参考号各一次、10 张卡、收单按发送序、推送 0、没有 failed 事件；ERP 点「取消」回「ยกเลิกแล้ว」；连接数跑前 0、跑后 0 |
| ② 重投同一事件 | — | 不多一条收单、不多扣一次 |
| ③ 两个人同时各发 10 张 | 各收下 1 张 | 各 10 / 10，同 ① |
| ④ 线程死在半路，扫描接走（修前没有道，不跑） | — | 没回复过的那条推一次「กรุณาลองใหม่ภายหลัง」、回复过的不推，都记 failed 不重放；后面排着的那条由扫描交短队列、只处理一次（attempts 1） |

没测到：网页进程 4 个请求线程被占住时普通页面是否秒回——道的排空在单独起的线程里，不占 gunicorn 的请求线程（结构上），
但没在真网页进程上压测（要 LINE 平台替身打真网页进程、配渠道密钥，留给上线前真机前的那一轮）。

### code-review（medium，11-28）

| 发现 | 真问题 | 修了 |
|---|---|---|
| 处理器跑超租约时道被扫描抢走、还在跑的被判中断 | 是 | 修：处理中 Redis 保活，抢道先看它 |
| 失败且没回过本人时一句也没有（新增的串行块等锁失败尤甚） | 是 | 修：同中断口径推一次 |
| 非守护排空线程拖住进程回收直到被杀 | 是（轻） | 修：进程退出时放道 |
| 每天清理与抢道撞上时这次抢不到 | 是（轻） | 修：重建一次再抢 |
| 块内 `get_session` 先用普通读判有无行 | 是（轻，后台调用方旧读视图时才会） | 没修：改成加锁读会带回间隙锁死锁；第二期后台路径进块前先提交（A1 锁序）时一并收掉 |
| 流程没在回归实验室经 run.py 跑过、真网页进程路径没压 | 是 | 没修：回归实验室今天归列车窗口；上线前真机那一轮一起补 |

审查后的修改（保活、失败告诉、退出放道、清理重试）只有离线单测（26 条全过），实验栈结果绑定在审查前的 `416f2573f`。

## 第二期（后台路径，11-28 晚，只有离线）

原则（施工清单「统一判据」第 3 条）：外部动作（识别、取图、存原件、写 DMS）之后改会话，先在这个人的串行块里加锁重读，只并自己那几项；
不拿外部动作之前读到的那份整份写回。

| # | 入口 | 改法 | 测试 |
|---|---|---|---|
| L6 | ERP 识别完出题 `line_questions.begin`（后台识别任务里调） | 整段进 `line_session.serialized`（调用方已先提交）：块里读会话是加锁读，`_precheck` 中途提交也没人能插进来；两张同时识别完、识别完时本人在答题，不再整份互盖。锁到点后别人改过，写回照旧报 417，调用方降级直接出卡 | `tests/test_line_lane_phase2.py` ErpBeginSerialized；ERP 自带 `test_line_intake` 148 条 |
| L7 | ERP 按行追问 `line_items.answer / search` | 点回来时这张卡加锁读（`_locked_doc`），与网页编辑、CC 代录记付款写同一列互斥；会话在道里已串行 | `test_line_intake` |
| L8 | ERP 收款凭证 `line_collection.store_voucher`（后台） | 取图、存凭证在块外；存好先提交，进块加锁重读，还在等这一笔才挂上；已取消 / 换了一笔就删掉刚存的凭证、不回话，不把取消的流程写活 | `tests/test_erp_collection_line.py` VoucherAfterCancel |
| L9 | ERP 收图 `_receive_document` 整份写回 | 不用改：它在道里（串行块），读是加锁读；后台出题进了同一把锁 | — |
| L10 | DMS 写档失败 `_dms_failed / _offer_check`（写 DMS 之后） | 改 `_set_retry`：`update_session` 里加锁读此刻的会话、只改 `dms_retry` 一键；会话已经没了不重建 | DMS 离线全套 |
| L11 | DMS 客户写入 `execute` 的「草稿没换」核对 | 不用改：`intake/service.py` 在调 `assert_current` 前已提交一次，读到的是最新的 | — |
| L12 | DMS 订车逐问 `_to_preview` | 不用改：webhook 路径，在道里串行 | — |
| 身份证 | DMS 身份证识别 `_recognize_image` | 识别结果先进执行器检查点（`dms.id_card.result`，同 ERP LINE 收单的写法）；写会话改 `update_session`：仍在收这张图（最后一张为准）才并进身份证，识别期间刚到的手机号留着、接着查重。以前按识别前那份整份比对，期间来了手机号就判「草稿变了」，已付费的结果被丢掉；记会话撞冲突时任务重跑从检查点取回，不再付费 | `tests/test_line_lane_phase2.py` IdCardMerge 4 格；`tests/test_dms_ocr_timing.py` |

会话写回检查：5 处基线（DMS `_dms_failed`、`_offer_check`、`_recognize_image`，ERP `line_items.answer / search`）修好、同一提交删行。

没做：识别完成往道里追加内部事件（方案第 3 条）。L6 进串行块后，出题与 LINE 事件已互斥，内部事件只为「到达序处理、完成序出卡」时序更整齐，
没有实测问题支撑，先不加（不为过渡加一层）。第二期的实验栈格（两张同时识别完 × 正在答题、凭证 × 取消、身份证 × 手机号）随页面压测时段一起跑。

## 待主会话定 / 未核实

1. （已定，主会话 11-28）中断的事件按「回过本人没有」分开，见设计第 5 条。
2. LINE webhook 重投窗口没核实，保留期先取 7 天。
3. 坑：DMS 在块里的 `get_session` 变成加锁读，会把本人会话行锁到这条事件提交；DMS 后台 `replace_session_snapshot` 撞上要等（最长 `innodb_lock_wait_timeout`）。第二期逐个核 DMS 同步段。
