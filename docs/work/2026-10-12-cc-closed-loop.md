# 工作包：CC 一家客户整月闭环 + 定时任务提醒（2026-10-12）

## 目标和范围

- 目标：会计员把一家客户整月的单据交给 CC，从收单到报税前由 CC 完成（录入推送 Express → 回读 → 对账 → 税务核查 →
  ภ.พ.30、ภ.ง.ด.3/53 草稿），会计员只审核重要细节；为 CC 新手教程（真实使用视角）铺路。
- 来源：复核窗口（会话「CC 新手会计员使用教程」）的交接《开发交接-CC闭环与提醒-2026-10-12》，三轮只读核对 + 对抗核查，
  基线 `origin/main` 774b209d。本记录抄录要点并逐项登记实现、证据和验证；以代码为准，开工时已重新核对每条证据（见下）。
- 完成条件：第一批 F1–F9、第二批 L1–L9 各自实现 + 单测 + 本地同名闸门 + 测试环境自测，交复核窗口在测试机复核；
  复核通过且用户同意后才合入 main、部署。
- 本次不做（用户已定）：手机推送以外的 LINE 推送；改识别提示词 / 作废章 / 两页票明细；每任务月度汇总表；只跑一次的任务；
  签名证书；工资社保（ภ.ง.ด.1、สปส.1-10）。
- 本次范围状态：进行中
- 收尾状态：施工中，尚未收尾
- 分支 / 基线 / 工作树 / 集成目标：`pearnly/cc-closed-loop` / 774b209d / `.claude/worktrees/happy-brahmagupta-055a7b` / `main`
- 合并 / 推送 / 远端回读：未合并；复核窗口在测试机复核通过、用户同意之前不合入 main、不部署。
- 负责人：开发窗口（会话「CC 闭环与定时任务提醒开发」）；复核：会话「CC 新手会计员使用教程」。

## 施工约束（摘自交接与 AGENTS.md）

- 测试机（lenovo-office-lan，skin3）保持 CC 常驻模式，不关常驻、不恢复旧配置；不碰 `\\Accserver` 与 S: 盘真账；
  往 `C:\PearnlyExpress\70EXP` 任一账套写入前先列清单给用户确认；计划任务 `PearnlyRestoreCompanionConfig` 不动。
- 识别费用：自测优先回放库（`scripts/ocr-replay-corpus.py`，零费用）；真识别单次 ≤20 份、谷歌实付 ≤฿10，超出先问；
  `ocr-budget-pause-2026-10` 暂停未解除。
- 推分支前按 `development.yml` 本地跑全部同名闸门；改 `pearnly_core` 合入前在生产镜像跑完整 `ci-candidate-checks offline`；
  架构检查不扩大基线。
- 新功能声明：可售功能 `features.json`、四语词条各产品 `i18n/messages.tsv`、前端词条 `frontend/assistant/src/i18n.ts`、
  新域登记 `architecture-policy.json`；文案官方简短、不加说明性文字。
- 测试环境：`PEARNLY_TESTENV_SRC=<工作树> bash scripts/testenv.sh live cowork`，前端改动带 `PEARNLY_TESTENV_LOCAL_SPA=1`；
  交出前从外部读回确认是新版本。CC 桌面端经 Windows CI 打包，测试机装 staging 版，以 skin3 普通权限重启。

## 开工核对（2026-10-12）

- 基线：`origin/main` = 774b209d（与本地 HEAD 一致），工作树干净。
- 测试环境：`testcowork.pearnly.com` 解析到生产（107.155.65.74）；测试机当前跑的是 DMS 栈（`faaac313…`，约 1 小时前起，
  `testdms`、`testai` 切在隧道上）。起 Cowork 栈会停掉 DMS 栈，动手前先与在用的会话核对。
- 测试机 CC：1.4.3（skin3，`C:\Users\skin3\AppData\Local\Programs\Pearnly Companion`），心跳正常，连生产 testcowork。

## 证据复核与设计

2026-10-12 用 9 路只读核对 + 1 路完整性复查重新核了交接里的每条「文件:行号」。结论：行为描述全部成立，行号有几处偏移
（`folder_worker.py` `_baseline` 实在 177-188、跳过在 252-254；`choices.py` fixed 生效点在 122-125；`push_choices` 在 750-774；
`bank_parse.py` 签名在 60-68；工具清单 1574-1968；`architecture-policy.json` 实际在 `docs/development/`）。另查出交接没写的几处：

- 台账 120 天后整行删除（`folder_store.py:126-130`），删后同一份文件会再登记一次、结果表格再写一份到当月；F1 去掉拍底后台账是唯一防线，
  本批改成留「墓碑」，完整扫描后清掉已不存在的文件。
- 「清空后重放」只对不同的文件成立：资源管理器原样拷回的同一文件保留修改时间，台账按「路径 + 大小 + 修改时间」认作已处理。
- 推送服务的进货贷项单现在都会被 `doc_sanity` 转人工（`push/express/doc_sanity.py:121-122`），F7 不用另管。
- 网页复核页重推同样不带任务默认值（`public/desk/intake-upload.js`、`targets.js`），F6 改在推送服务里统一取，网页、手机、CC 同一口径。
- 单个任务保存的允许卡片里 `run_day=0` 显示成「0」（`tools.py:1497`），顺手改成「月末」。
- `pearnly_core/assistant/tools.py:82` 卡片最多 20 行，超出静默截掉：批量保存上限定为 20 个任务（一个任务一行）。
- 以下测试文件不在任何 CI 通道（改到就登记）：`test_cowork_scheduled_task_fixes.py`、`test_cowork_folder_tasks_mrerp.py`、
  `test_cowork_companion_release.py`、`test_cowork_companion_http_auth.py`、`test_cowork_recon_presets.py`、`test_cowork_push_account.py` 等。

### 口径决定（交接授权本窗口定）

- **F1 停用后再启用**：新任务（`include_existing=1`）重新启用时不重新拍底，`active_since` 只作排程下限（启用当下不补跑已错过的时刻）；
  到点处理文件夹里所有本任务还没处理过的文件，包括停用期间放进来的。老任务（`include_existing=0`）一切照旧（重新启用重新拍底，
  2026-10-07 的口径只对老任务保留）。老任务改了读文件夹的电脑、根目录或文件夹，按新任务口径处理（原基线本来就对不上新文件夹）。
- **F1 的 N**：允许卡片和表单用「文件夹计数」（该文件夹含子文件夹里会被录入的文件数）；任务列表和提醒用「待处理数」（不在台账、
  不在基线的文件）。数到上限显示「N+」，不知道时不显示，不用 0 冒充。不加确认步骤、不设上限（交接：只让人看得见）。
- **F3 简报**：一份简报 = 同一负责人 + 同一定时时刻；立即运行单独成一份；没有新文件也发（写「没有新文件」）；全部文件到终态后
  再等 10 分钟发，最长等 13 小时（F7 销售最多等 12 小时，再加 1 小时）后带「处理中」强制发；历史运行一律不补发。
- **F7**：只管存货任务。同一写入连接 + 同一账套，存货销售文件等同账套的存货进货文件推送有了结论（写完或停下待人）再准备；
  进货停下待人不拦销售；销售最多等 12 小时；同步下发顺序进货任务在前。
- **F6**：任务默认值在推送服务里按钩子取（`cowork_push_fixed_choices`），网页复核页、手机、CC 重推都带上；被任务默认值决定的类别
  不写进账套记忆。任务停用仍套用；任务删除、推往别的连接或账套、回导工作簿生成的新票不套用；对方档（party）不套用。
- **F8**：每次发话在批末附一条 `system.message`（与上下文接续、未带过的提醒合成一条），写曼谷当地日期时间与佛历年；不改系统提示。
  上线前在测试环境对引擎目录里四个模型各实测一条，任一不接受就停下改法。
- L6、L7 与 F4/F5 同在 `assistant/tools.py`，随第一批一起做（L7 的说法在 L1 上线后再补一句回读）。

### 第一批接口约定（各路实现以此为准）

**A. 共用层对话提醒（`apps/pearnly_core/pearnly_core/assistant/notices.py` + DocType `Pearnly Assistant Notice`）**

- 表字段（全部只读，平台记录，System Manager 只读）：`user`（Link User，必填，索引）、`session_id`（Data，索引；空 = 不出卡片）、
  `kind`（Data，必填，如 `cowork.folder.reminder` / `cowork.folder.brief`）、`title`（Data）、`text`（Small Text，必填，≤4000 字）、
  `dedup_key`（Data，必填，唯一）、`reference_doctype`、`reference_name`（Data，复合索引）、`refs`（Small Text，JSON 列表，
  这条提醒涉及的全部记录名）、`ttl`（Int，手机推送有效秒数）、`seen`（Check）、`carried`（Check）。autoname hash。
- `post(user, kind, text, dedup_key, *, title="", conversation="", reference_doctype="", reference_names=(), ttl=6*3600) -> dict`：
  回 `{"name", "id": "pnotice_"+name, "session_id", "created": bool}`；同键已存在回 `created: False`，不再推送。
  `conversation` 只有属于该用户、未归档、当前引擎时才记进 `session_id`，否则存空（只走手机推送和电脑通知）。插入用保存点，
  不整笔回滚调用方事务。挂上对话时把会话行 `modified` 改成现在（侧栏浮顶）。提交后：实时事件（沿用 `relay.EVENT`，
  载荷 `{"session_id": <对话或"">, "event": <事件>}`，推给 user）+ 排队 `pearnly_core.assistant.push.send_notice(notice=<name>)`
  （queue short，`enqueue_after_commit=True`，按 notice 去重）。
- 事件形状：`{"type": "pearnly.notice", "id": "pnotice_<name>", "processed_at": <RFC3339 UTC>, "kind", "title", "text", "session": <对话或"">}`。
  桌面与浏览器通知的去重标签一律 `"notice:" + id`。
- `exists(dedup_key) -> bool`；`for_references(doctype, names, since_epoch, limit=20) -> [{"id","title","text","created"(epoch),"session"}]`
  （`reference_doctype == doctype`、`creation > since`、`refs` 与 `names` 有交集，按时间先后）。
- `contracts.deep_link(session_id="") -> str`：有合法对话编号回 `/assistant?c=<编号>`，否则 `/assistant?view=tasks`。
- 会话侧：`sessions.listing` 每行加 `unread: bool`（有 `seen=0` 的提醒）；`sessions.events` 整段回放按时间并入本对话提醒、增量补拉带上
  锚点时间之后的提醒；新白名单 `pearnly_core.assistant.api.notices_seen(session_id)`（POST）；删除对话时提醒的 `session_id` 置空（不删行，
  去重键留着）；`sessions.send` 批末附一条 `system.message`（接续 + 未带过的提醒 + 日期，见 F8），发出成功后标 `carried`。
  未带过的提醒包括本对话的和本用户没挂对话的。

**B. 共用层手机网页推送（`assistant/webpush.py` 纯加密、`assistant/push.py` 服务）**

- 白名单：`pearnly_core.assistant.api.push_key`（GET，回 `{"key": <VAPID 公钥 base64url>}`）、`push_subscribe(endpoint, p256dh, auth)`（POST）、
  `push_unsubscribe(endpoint)`（POST）。都要登录。
- `push.send_notice(notice)`：读提醒，`push.send(user, title=title or "CC", body=text, url=contracts.deep_link(session_id), tag="notice:"+id, ttl=ttl)`。
- 后台脚本点通知：已有 `/assistant` 窗口就聚焦并 `postMessage({type: "pearnly-open", url})`，没有就 `openWindow(url)`。

**C. 前端（`frontend/assistant`）**

- 地址参数：`/assistant?c=<对话>` 打开那段对话（不是本人的或不存在就打开定时任务页，没有定时任务就新对话），`/assistant?view=tasks`
  打开定时任务页；处理完用 `history.replaceState` 回到 `/assistant`。另收后台脚本消息 `pearnly-open` 与窗口事件 `tomo-open`（detail 为对话编号，
  空串 = 定时任务页）；`window.tomo.open(target)` 派发该事件。
- 实时：`realtime.ts` 加通用 `onSiteEvent(event, handler)`、`onConnectionChange(listener)`；`session_id` 为空的 `pearnly.notice` 也要派发
  （现在会被丢掉）。定时任务页监听 `cowork_folder_changed`（载荷 `{rule}`）。

**D. Cowork 定时任务服务（`folder_intake`）**

- Rule 新字段：`include_existing`（Check，默认 0，新建 = 1）、`remind`（Check，默认 0）、`remind_since`（Datetime）、`cc_session`（Data 140）。
  Run 新字段：`run_owner`（Link User）、`slot_at`（Datetime）、`brief_state`（Select `pending/sent/skipped`，索引）、`brief_at`、`brief_key`。
  File：`source_file` 加索引。补丁 `v0_0_16_folder_run_briefs`（历史运行置 `skipped`）。
- `rules.save(values, *, cc_session=None)`：`values` 可带 `remind`（0/1/bool），不带保留原值；`cc_session` 只从参数取，绝不从 values 取。
- `rules.save_many(items, *, cc_session=None) -> {"saved": [project…], "failed": [{"task": 序号(1 起), "title", "reason"}]}`，`BATCH_MAX = 20`，
  任一失败整批回滚（保存点），失败项之后的仍校验，一次报全。
- `rules.set_enabled(name, enabled=None, *, remind=None, cc_session=None)`：None = 不变；网页 `api.set_enabled(name, enabled)` 签名不变。
- `rules.listing()` 每行加 `include_existing`、`remind`、`waiting`（待处理数，不知道为 None）、`waiting_partial`；`results` 已有 `moving`
  （改为不计 24 小时前仍停在 registered 的行）。`rules.project()` 加 `include_existing`、`remind`、`remind_since`。
- `rules.waiting(reader, root_id, folder) -> {"files": int, "partial": bool} | None`（文件夹计数，给新任务卡片和表单）；
  `rules.account_label(connection, account_set_key) -> str`、`rules.master_names(connection, account_set_key) -> {"accounts": {code: name}, "departments": {code: name}}`
  （给卡片预览用的公开薄入口）。
- `rules.options()` 的 `readers[].roots[]` 加 `counts`（`{文件夹: n}`，键是上报的文件夹相对路径，`""` 是根目录）与 `partial`。
- 实时事件 `cowork_folder_changed`，载荷 `{"rule": <名>}`，推给负责人（及文件的 actor），提交后发。
- 提醒与简报只经 `folder_intake/notify.py` 调用共用层 `notices.post`；调度入口 `pearnly_cowork.folder_intake.schedule.notices(now=None)`
  挂每 5 分钟；`now` 是站点时区时间字符串，给测试环境手动触发。
- F6 钩子：`hooks.cowork_push_fixed_choices = ["pearnly_cowork.folder_intake.push.fixed_for"]`，
  `fixed_for(history, connection, account_set) -> dict`（取不到回 `{}`，不抛错）；`push.application.service` 在调用方没给 fixed 时按钩子取，
  `choices_view` 回 `"fixed"`（非空时），`save_choices` 拒收被任务默认值决定的类别（词条 `cowork.choice.task_default`）。

**E. 小助手 ↔ 服务端文件夹同步（向后兼容，不改协议号）**

- 同步请求新增：`rule_pending`（`{规则名: int}`，上一轮算的待处理数）、`roots[].counts`（`{文件夹: n}`）、`roots[].partial`（bool）、
  `notices_after`（epoch float，新版才带）、`runs[].slot`（该次运行所属定时时刻 epoch，可缺）。
- 同步回复新增：`rules[].existing`（0/1，缺 = 0 = 照旧拍底）、规则下发顺序进货任务在前、`notices`（请求带了 `notices_after` 才回，
  最多 20 条、12 小时窗，字段 `NOTICE_FIELDS = ("id", "title", "text", "created", "session")`）、`companion_latest`（同心跳的形状）。
- 小助手 1.4.4：按 `existing` 决定拍不拍底；台账墓碑；托盘按 `"notice:"+id` 去重弹通知，点了打开那段对话（没有就定时任务页）；
  只读文件夹的成员也从同步回复拿新版提示。

**F. 词条归属**（防止并行重复加）：`cowork.folder.*` 新词条与 `cowork.folder.waiting_purchases` 归定时任务服务；工具标签归工具；
`cowork.choice.task_default` 归 F6；共用层只加一条 `This device cannot receive notifications.`；前端词条只在 `frontend/assistant/src/i18n.ts`。
翻译包 `translations/*.csv` 由合并后统一 `i18n-build.py` 生成。

## 验证

（施工中补充）
