# 需求影响评估：CC 交互批 A（刷新不丢、排队发送、首轮流式、停止保留、标题与侧栏、跟随滚动、提问卡浮起）

- 基线：origin/main `53859921a`（11-09）。分支：本评估 `pearnly/cc-batch-a-plan`；施工一项一个小分支 `pearnly/cc-a<N>-<主题>`，一项一合。
- 来源：对照文档 `docs/work/2026-11-09-cc-vs-claude-ux-audit.md`（分支 `pearnly/cc-vs-claude-ux-audit-813cf9`，排号合入中）第 1、2、3、4、6、10 项与卡片第五、六节。

## 0. 需求原文与理解

- 主会话 11-09（转用户）：「批 A 交给你来做：刷新不丢（网址带对话编号 + 草稿和附件保留）、回复中可以先打下一句排队、新对话第一轮先查清 30 秒的原因再修、
  停止后保留半段 + 已停止 + 重试、标题提速并把新对话放到侧栏顶上、电脑跟随滚动」；「浮卡：窄屏照 Claude 实测做，提问卡浮在输入框上方，对话里只留『正在提问』小标签……
  电脑宽度……照实测结果做」（电脑宽度实测：同样浮，见对照文档第六节）。每项先写旧代码上会红的测试，过闸门、候选检查和受影响流程，小分支一项一合，四语齐全，文案过闸。
- 理解：CC 对话页（`frontend/assistant`，Cowork / ERP 两产品、网页 / 手机 / CC 桌面版同一套前端）的 7 件交互改成与 Claude 实测一致。
- 不在范围：复核卡、开始前卡片（归 CC 线窗口）；上传 U1–U5（CC 线）；批 B（编辑已发消息、快捷键、正文搜索等）；提问卡本身的内容与行为（只改它放在哪、怎么挂）。

## 1. 现有同类与复用（逐项，下同编号）

| 项 | 现状（调查结论，带出处） | 做法 |
|---|---|---|
| A1 网址带编号 + 草稿附件 | 已有 `?c=<编号>` 深链接，只在打开时读一次、随即 `replaceState` 清回 `/assistant`（`lib/nav.ts:200-216, 366-375`）；前进后退是内存栈（`nav.ts:141-187`）；手机返回键用 `pushState` 记弹层（`lib/back-stack.ts:142`）。草稿只在内存（`runtime-provider.tsx:219`），已传附件在 `attachments.ts:18` 的 `uploaded`。服务端已按「本站 + 当前用户 + 引擎」核会话归属（`assistant/sessions.py:75-81`） | 扩展现有：打开 / 切换对话时网址保持 `?c=<编号>`（`replaceState`，与 back-stack 共用 `history.state`）；草稿文字 + 已传附件元数据按「用户 + 对话」存 localStorage，刷新后填回 `uploaded`。服务端不改 |
| A2 回复中排队 | 回车只换行是组件库没开排队时的默认行为（`@assistant-ui/react` ComposerInput.tsx:251）；外部数据运行时有 `queue` 选项和现成排队条目组件（external-store-adapter.ts:138、primitives/queueItem）。挂着识别时发消息服务端已「先交回再发」（`waits.py:196`） | 复用组件库排队：`runtime-provider.tsx` 传 `queue`，本轮结束自动发；`thread.tsx` 在输入框上方显示排队条目，带撤回 |
| A3 首轮不逐字 | 调查得两个最可能原因（未复现，待证实）：① 首轮补拉第一次走整段历史，没有超时兜底，引擎列事件单次 6 秒 × 3 次约 24 秒报错，`onerror` 置 isTailing 否 → 顶部「正在重新连接…」（`sessions.py:701`、`session-controller.ts:384`、`assistant.tsx:219-228`）；② 尾巴连上后又整段重放，重放回来前推来的逐字预览压在 `pendingTail`，回来后一次交出（`session-controller.ts:361-379`） | 先在实验栈复现（假引擎列事件加延迟）并量时刻，再修；修法按证据定，不先写 |
| A4 停止保留半段 | 停止 = 向引擎发 `user.interrupt`（`sessions.py:913`）；半段只是逐字预览，只在本页内存，预览不进事件记录（`poll-source.ts:274`），被中断的回复引擎不发正式 `agent.message`；末尾「…」是出错收口补的（`reducer.ts:790-800`） | 服务端：中断时 relay 把已收到的预览存成本站事件（照 `pearnly.notice` 并进事件列表，`sessions.py:672` 起）；前端：reducer 加「已停止」状态，消息底部「已停止」+〔重试〕（= 再发同一句） |
| A5 标题与侧栏 | 首条发出后服务端即排 Haiku 概括（`sessions.py:859-862`、`titles.py:40-67`），前端等发送请求返回才取标题、没有就用整句（`runtime-provider.tsx:208`、`session-list-adapter.ts:277-286`）；没挂公司的落「最近」组，固定在公司组之后（`sidebar-groups.ts:223-225`）；当前行有 `data-active` 高亮，没有滚到可见 | 先量清 55–60 秒各段（发送、排队、模型），再改；当前对话滚到可见；「顶上」口径见第 12 栏 |
| A6 电脑跟随滚动 | 视口 `turnAnchor="top"` 没传 `autoScroll`，组件库默认不跟（`thread.tsx:132`）；往上翻停跟随组件库自带 | `autoScroll={!phone}`（`useIsMobile`），改 `follow-scroll.ts` 注释与打开对话的停位规则核对 |
| A7 提问卡浮起 | `ask_user` 卡在对话流里（`tool-uis/ask-card.tsx`）；手机已有允许卡浮层 `approval-sheet.tsx`（浮在输入框上，挂在 `thread.tsx` 的 ViewportFooter） | 复用允许卡浮层的挂法：等答的提问卡（窄屏、电脑都）浮在输入框上方，对话流里换成「正在提问」小标签；答完浮层消失、流里是只读摘要卡（已有）；卡开着时输入框可打字，发出 = 当前题的「其他」、其余题跳过（照 Claude 实测） |

## 2. Frappe 原生能力

- 不涉及：都在 `/assistant` 这一个 React 例外入口内（ADR-019 第 6 条）；A4 服务端只在共用层 `pearnly_core.assistant` 加一种本站事件，不新增 DocType（如需持久化表，施工前再报）。

## 3. 按类通用

- 同一套前端覆盖：Cowork / ERP、直写 / 代录、网页 / 手机 / CC 桌面版（桌面版网址由外壳管：`nav.ts:16`「桌面端记住的地址是干净的」，A1 在桌面版只做草稿与附件，不改外壳地址——施工时核实外壳是否重载页面）。
- A7 只动 `ask_user` 通用提问卡的挂载位置；代录中途选择卡、待处理框复用同一组件，是否一起浮，见第 12 栏。

## 4. 上下游数据

- ERP / Express 单据流：不涉及（只改对话页交互，不改任何写账路径）。
- CC 桌面版载荷：不涉及（不改桥接协议；A1 若需外壳配合另报）。
- A2 排队：排队的消息在本轮结束后才发，不改服务端「一轮一轮」的顺序；真引擎在跑时收 `user.message` 的行为没查到，所以不在回合中途发。
- A4：新增的本站事件只给页面显示用，不交给模型（模型侧仍是被中断）；重试 = 新的一轮。

## 5. 隔离与权限

- 网址里的对话编号由服务端 `owned()` 核归属，打不开的回落新建页（已有，`nav.ts:256-293`）；本机存的草稿键带站点与用户名（照 `sidebar-fold.ts:21`），换人登录读不到别人的草稿；退出登录清掉本人草稿。
- A4 存的半段按会话归属同样经 `owned()` 读。

## 6. 入口

- 网页（电脑 / 窄屏）、手机 PWA、CC 桌面版都走同一前端，逐项在三处验；LINE、拍照不涉及。

## 7. 共用件与连带回归

- 登记的共用件：`cc-composer`（A2、A7）、`cc-sidebar-list`（A5）、`cc-desktop-bridge` / `cc.desktop.bridge`（A1 的 back-stack）、`cc-prefs-sync`（A1 不用它存草稿，只核不冲突）、后端 `assistant`（A4、A3 可能）。
  每项施工时跑 `python3 scripts/check-impact.py --files …` 出清单，逐入口实测记「通过 / 不通过 / 没测到」；改 `pearnly_core` 的项合入前在生产镜像跑候选离线检查。
- 流程：`cc.chat`、`cc.sidebar-modes`、`cowork.cc.sidebar-modes`、`cc_send_while_waiting*`；新增：停止保留（A4）、刷新恢复（A1）、排队（A2）的流程断言。

## 8. 存储与成本

- A1：浏览器本机每段对话一条草稿（文字 + 附件元数据，几 KB），发出或删对话即清；不上服务端。
- A4：每次停止多存一条本站事件（半段文字，通常几 KB）。
- 无新增外部调用、无固定月费；A3、A5 只量和改时序。

## 9. 删除、作废、追溯

- 删对话时同时清本机草稿；A4 的半段事件随会话删除一起删（照现有会话删除路径，施工时核）。不涉及 Express。

## 10. 文案与官方 UI

- 新增文案（四语 th / zh / en / ja，过 `check-copy.py`）：「已停止」、「重试」（先查已有词条复用）、排队条目的撤回（图标按钮，读屏名复用「取消」类已有词条）、「正在提问」。
- 不加说明性文案；样式只走 `globals.css` 标准定义。

## 11. 测试与验收

- 每项先在 `tests/test_assistant_ui.py`（esbuild 打包 node 跑）或 `tests/test_assistant.py` 写旧代码上会红的用例，再改到绿：
  A1 刷新后编号仍在网址、草稿附件恢复；A2 回复中回车进排队、可撤回、结束自动发；A3 按复现结论写；A4 中断后重放仍有半段 + 已停止；A5 标题请求时序；A6 电脑 autoScroll 开、手机关；A7 等答时浮层在、流里是小标签、打字发出交「其他」。
- 闸门 = development.yml 全套；候选离线（改共用层的项）；受影响流程电脑 / 手机 × Cowork / ERP；合入后在测试租户截图复核。
- 用户要亲自看：刷新后回到原对话、回复中能排一句、停止后半段还在。

## 12. 要问主会话的点

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | 「新对话放到侧栏顶上」：现在分组是「已置顶 → 各公司 → 最近」（用户 10-17 定）。没挂公司的新对话在「最近」组，排在所有公司下面 | 不动分组顺序；新建或切到某段对话时，侧栏自动滚到这一行并高亮（挂了公司的在公司组里、没挂的在「最近」组里，都看得见）。若一定要放最顶上，就把「最近」组提到公司组上面（改用户 10-17 定的顺序，要用户点头） | A5 侧栏那半不能开工 |
| 2 | 代录中途选择卡、对话末尾待处理框也复用提问卡组件，是否也浮起 | 本批只浮 `ask_user`；另两处在 CC 线复核卡批次里定（它们和复核卡同一片） | 无（先只做 ask_user） |
| 3 | 停止后「重试」 | 重试 = 把被停的那句再发一次（新一轮），不做「编辑后重答」（批 B 再看引擎能否截断） | 无 |

## 13. 工作量与顺序（写码 + 机器，小时；不含等人与排号）

| 顺序 | 项 | 写码 | 机器 | 说明 |
|---|---|---|---|---|
| 1 | A6 电脑跟随滚动 | 0.5 | 1 | 最小，先合 |
| 2 | A3 先查（实验栈复现 + 量时刻） | 1.5 | 1 | 查清后修法与估时报主会话 |
| 3 | A1 网址带编号 + 草稿附件 | 4 | 2 | 先合 attachments.ts，CC 线 U 系列后变基（已对齐） |
| 4 | A4 停止保留半段 | 4 | 2.5 | 动共用层，跑候选离线 |
| 5 | A2 回复中排队 | 3 | 1.5 | |
| 6 | A7 提问卡浮起 + 打字交「其他」 | 3 | 1.5 | 与 A2 同在输入框一片，A2 合后做 |
| 7 | A5 标题提速 + 侧栏 | 2 | 1.5 | 等第 12 栏第 1 问 |
| — | A3 修 | 视查的结果 | | |
| 合计 | | 约 18 + A3 修 | 约 11 | |

文件归属已与 CC 线窗口（local_fd1ef94e）对齐（11-09）：上面各文件它的分支都没改；提问卡浮起归本批；`attachments.ts` 本批先合、它的 U 系列后变基；它若要动 `thread.tsx` / `reducer.ts` 先找本窗口。

## 施工记录（主会话 11-09 审定后）

审定结论：照第 13 栏顺序施工；侧栏顺序不改，新建 / 切换对话时侧栏滚到这一行并高亮；本批只把普通提问卡浮起；重试 = 把被停的那句再发一次；
卡开着打字 = 交当前题的「其他」（依据：对照文档 card-claude-20）。

| 项 | 分支 | 提交 | 测试（旧代码红 → 绿） | 闸门 / 候选 / 流程 | 状态 |
|---|---|---|---|---|---|
| ① 电脑跟随滚动 | `pearnly/cc-a6-follow-scroll` | ddb7c5f51 | `test_cc_mobile_content` 视口 `autoScroll={!phone}`；cc.chat 加「长回复」步骤（电脑离底 0、手机提问离上沿 20 像素） | 闸门 213 过；流程 cc.chat / attach-delete / operator.replay 过 | 排号待合 |
| cc.operator.replay 缺单 | `pearnly/flows-replay-own-purchases` | a5319d743 | 新实验栈复现「need two submitted purchase documents」→ 流程自备两张服务费采购单，缺前置开跑即报 | 新老 × 电脑手机 4/4 过 | 排号待合 |
| ② 计时 | `pearnly/cc-a2-turn-timing` | a35962615 | `test_creating_and_sending_log_how_long_each_step_took` | 闸门过；候选离线 107 组 LANE EXIT 0 | 排号待合，随下一批上线读生产真数 |
| ② 第 3、4 条 | `pearnly/cc-a2-first-turn` | e74cf833f | `test_a_new_chat_streams_its_first_reply_while_the_backfill_is_slow`、`test_a_backfill_landing_after_the_streamed_words_keeps_one_reply` | 闸门过；流程 14/14 过 | 排号待合 |
| ② 预热 | `pearnly/cc-a2-prewarm` | 9de7ac6c0 | `test_the_agent_is_prepared_in_the_background_after_a_migrate_and_after_the_key_changes` | 闸门过；候选离线 LANE EXIT 0；实验栈实测后台任务备好缓存；cc.chat 4/4 | 排号待合 |

② 实测与结论见共享笔记「已知问题与结论.md」11-09 两条：热的时候建对话 0.6–0.8 秒、发送 1.0–1.3 秒、发出后 2.4–2.8 秒出第一条回复；
那次 30 秒 = 冷的建对话 + 首次整段补拉串在发送前 + 补拉慢时逐字压着。第 2 条（首句才开远端会话）主会话改成预热；第 5 条（发送里等推送
通道最多 3 秒）等生产计时数据再定。

预热挂的钩子（主会话要求记下）：
- 部署迁移后：`apps/pearnly_core/pearnly_core/hooks.py` 的 `after_migrate` 加 `pearnly_core.assistant.engine.prewarm`（每个站点 migrate 完排一次）。
- 换密钥 / 引擎 / 模型后：`infrastructure/production/site-step.py` 的 `set_assistant` 分支写完站点配置后调 `engine.prewarm()`。
- 两处都只是排一个后台任务（short 队列、按站点去重），任务里调 `engine.prepared` 备好资源写进缓存；没开通 CC 的站点不排；排不上、备失败只记 Error Log
  （「Assistant prewarm not queued / failed」），不挡迁移和部署；不进定时任务、不循环、不常驻。缓存照旧 6 小时过期（过期后第一个新对话现备约 2 秒，未改）。
