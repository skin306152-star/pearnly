# CC 一句话下令开始（写入计划 + 按计划开始）施工记录

> A 线第 6 棒，2026-11-11。评估：共享笔记「A线-第4棒-一句话下令-评估-待审-11-10.md」，主会话 11-10 批复四条（第 4 棒交接第二节）。
> 基线 origin/main d22f453ee。分支 `pearnly/cc-write-plan`。

## 批复要点

1. 计划存共用层新 DocType、随对话删，Cowork / ERP / MR.ERP 共用。
2. 页面上不经 CC 的「开始」一律去掉；`express_operator.api.start` 不再是页面接口，只留给 `pearnly_write_start` 内部调，测试锁住。
3. 「停」即时生效：停止键 / 停止词（B 线已在 main：`sessions.last_stop`）。
4. 第 4 项（代录只交已加载那一页）已单修合入。

## 设计

### 服务端（共用层 `pearnly_core.assistant.write_plan`）

- DocType `Pearnly Assistant Write Plan`：对话、用户、模式（direct / operator）、公司、代录任务号、建计划时的用户消息序号
  （`sessions.user_turn`）、要写的单 `[{doc_id, version}]`、跳过的单 `[{doc_id, reason}]`、状态（awaiting / started / replaced /
  void）、开始时刻。对话删掉随之删。
- 工具 `pearnly_write_plan`（CC 调）：
  - 代录：按服务端完整待录清单（`api._waiting`，同第 4 项）算，不认页面给的单。
  - 直写：CC 点名的单（本对话识别的）。
  - 每张经产品写账前置判定（`write_precheck.products.check`，与写入同一套判断）分出：可写 / 跳过（原因照实：不入账、写不了、
    录过、结果不明）；可写的记下版本（判定结果的摘要：之后票面、分组、答案变了就不同）。
  - 同一对话里新计划把旧的 awaiting 计划标 replaced。
  - 回给 CC：公司、账套、张数、跳过清单与原因 + 要求「用一句话照这几项复述，问用户可不可以，等用户回答」。
  - 没有可写的：`nothing_to_write` + 原因，CC 用话说明。
- 工具 `pearnly_write_start(plan)`：
  - 只在 `sessions.user_turn(对话) > 计划的序号` 时放行（复述之后用户又说了一句）；否则 `not_confirmed`。
  - 计划之后停过（`sessions.last_stop` 晚于建计划）也不放行，要重新复述。
  - 逐张重算版本：变了的这次不写、回「已改，要重新复述」，其余照写。
  - 代录：内部调 `api.start` 同一路径（任务、完整清单、只交计划里没变的）；直写：计划标 started，CC 接着用本产品推送工具推这些单。
- 直写推送工具（`cowork_push_documents` / `erp_push_documents`）：只推本对话已开始计划里的单；停止键 / 停止词之后（last_stop 晚于计划开始）
  剩下的不推，回已写到第几张。
- 事件：计划状态作为本站事件 `pearnly.write_plan` `{plan, status, count}` 并进对话事件（同执行记录）。
  **给 B 线「录吧」快捷回复的出现条件**：本对话最新一条 `pearnly.write_plan` 是 `awaiting`、且模型空闲。

### 前端（A 归属的卡片）

- 代录开始卡：去掉「开始」按钮与对 `start` 的调用，只留预选、看清、就地改；卡不再停在等人点（会话状态按空闲）。
- 「录吧」胶囊挂在输入框上方：B 线。

## 测试（先写，旧代码红）

- 51 张：代录计划含全部 51 张。
- 没有复述后的新用户消息 → start 拒；有 → 放行。
- 计划后改了一张 → 那张不写、其余写。
- 停过 → start 拒 / 直写剩下的不推。
- 没账套 / 都不能写：`nothing_to_write`，不建任务。
- 页面不能直接调 `api.start`（不是 whitelist）。
- 直写推送工具不在已开始计划里的单拒。

## 进度（11-11）

- [x] 服务端 DocType + 两个工具 + 单测（`tests/test_write_plan.py` 11 条，新模块，旧代码上不存在 → 红）
- [x] 直写推送工具接计划（Cowork / ERP 两边 `direct_push.permitted`）
- [x] 代录开始卡去按钮、答题只记下（`express_operator.api.remember`）、feed 准备中算空闲；`api.start` 不是页面接口（测试锁住，旧代码红）
- [x] 流程：cc.operator 改成说一句建计划（实验栈单据都是「等电脑读账套主档」，计划回 nothing_to_write、跳过 3 张，写 + 跳过 = 站点等着录的张数）；
      cowork.operator.unknown 改成卡上没有「开始」
- [x] 闸门 220 过（a41d270aa）；候选离线 LANE EXIT 0（794ac5f32）；受影响流程：cc.chat 4、cc.operator 4、cc.operator.replay 4、
      cowork.operator.unknown 2、cc.sidebar-modes 2、cowork.cc.sidebar-modes 2、cowork.pending.operator 2、
      cowork.operator.send-while-waiting 2、cowork.cc.send-while-waiting 2，全过
- [ ] 真模型场景（Cowork / ERP × 直写 / 代录：CC 先复述、等用户回话再开始，用户没回话时服务端拒）——待主会话定费用与场景脚本来源
- [x] B 线「录吧」（`pearnly/cc-record-quick-reply` a62e19c65，本分支叠在它上面）：新流程 `cowork.cc.record-reply`
      「说一句建计划 → 出现录吧 → 点了照常发出 → 按计划开始后消失」电脑、手机 2/2 过；`not_flows.toml` 里 record-reply.ts 那条已删
- [x] 「没有可写的单」不存计划（否则页面会出「录吧」）
- 工具结果的 status：有要写的回 `ready`、做不了回 `nothing_to_write`；计划表与 `pearnly.write_plan` 事件的状态仍是 awaiting / started / replaced（事件形状已定，不再改）

## 部署回读清单

- 新 DocType `Pearnly Assistant Write Plan`（Pearnly Core）：部署迁移后逐站核表在（`doctype_guard`），各产品站点都有。
- 新接口 `pearnly_core.express_operator.api.remember`（POST）；`express_operator.api.start` 不再是页面接口。
- 新 CC 共用工具 `pearnly_write_plan`、`pearnly_write_start`（两产品工具清单里都有，代录、直写两种会话都能用）。

## 检查影响清单

`python3 scripts/check-impact.py`：命中 assistant、write-precheck 两个共用件；登记表完整（write_plan.py 登记在 cc.operator.page）。
