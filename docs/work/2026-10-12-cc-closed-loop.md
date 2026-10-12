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
- 2026-10-12 23:45 DMS 窗口确认其 DMS 栈已收尾；本窗口 `PEARNLY_TESTENV_REFRESH=1 lab-start cowork`（镜像 faaac313，与生产同），
  testcowork 数据从线上重新导出导入（租户 T-2026-0287），域名切到测试机，线上 testcowork 调度按规程暂停（lab-stop 恢复）。

### 测试环境定时任务（交接 F2「测试环境定时器」）

只读核对（2026-10-12 23:45–23:51，测试机 testcowork）：`scheduler status` 为 enabled；站点配置 `pause_scheduler` 假、
`maintenance_mode` 0；System Settings `enable_scheduler=1`、`dormant_days=4`。Frappe 的 `is_dormant` 先判
`on_frappecloud()`，不在 Frappe Cloud 上直接回「不休眠」（读镜像内源码确认），与交接一致，不是原因。
实测 `folder_intake.schedule.tick`、`recon_center.waiting.tick`、`work.reminders.run` 的 `last_execution` 从导出时的 23:44 前进到 23:48，
即调度在跑。Cowork 栈没有设 `scheduler_tick_interval`（只有 DMS 设成 60），Frappe 默认节拍 240 秒：
「每分钟」的任务在 Cowork（测试机与生产相同）实际每 4 分钟执行一次。先前「导入的租户定时任务不跑」在刷新后的数据上没能复现；
提醒仍保留 `now` 参数按指定时刻手动触发，便于验收。

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
  再等 3 分钟（2026-10-13 由 10 分钟缩短，简报已按电脑分开）发，最长等 13 小时（F7 销售最多等 12 小时，再加 1 小时）后带「处理中」强制发；历史运行一律不补发。
- **F7**：只管存货任务。同一写入连接 + 同一账套，存货销售文件等同账套的存货进货文件推送有了结论（写完或停下待人）再准备；
  进货停下待人不拦销售；销售最多等 12 小时；同步下发顺序进货任务在前。
- **F6**：任务默认值在推送服务里按钩子取（`cowork_push_fixed_choices`），网页复核页、手机、CC 重推都带上；被任务默认值决定的类别
  不写进账套记忆。任务停用仍套用；任务删除、推往别的连接或账套、回导工作簿生成的新票不套用；对方档（party）不套用。
- **F8**：每次发话在批末附一条 `system.message`（与上下文接续、未带过的提醒合成一条），写曼谷当地日期时间与佛历年；不改系统提示。
  上线前在测试环境对引擎目录里四个模型各实测一条，任一不接受就停下改法。
- L6、L7 与 F4/F5 同在 `assistant/tools.py`，随第一批一起做（L7 的说法在 L1 上线后再补一句回读）。

### 用户决定（2026-10-13）

用户原话：「l1 l5按你的建议处理，尽量做到完整能让cc完成任务」。

- **L1 回读留存**：只在用户请求时按「账套 + 月份」读取，存为租户站点私有文件；同一人、同账套、同月只留最新一份，可删除。
  `product_manifest.py` 的禁止事项改为「不把配对密钥明文存到云端；本机会计数据只在用户请求时按期间读取并私有留存」。
- **L5 的 50 ทวิ**：租户把预扣税凭证设成「不录入」时，50 ทวิ 副本也存一条参考记录供 ภ.ง.ด.3/53 草稿读取：
  不推送、不另外扣费（识别照常发生，不新增调用），不进录入待办。按「做完整、让 CC 能完成任务」取这一项。
- **70EXP 写入确认**（2026-10-13 01:0x，用户原话「可以写 T9SINCER」）：自测只写 `C:\PearnlyExpress\70EXP` 的 T9SINCER（69SINCER 测试副本），
  最多 6 张：费用 6900573 水费、6900844 电费，进货 6900872 税票照片，销售 6901885、6901892、6901904；测试文件夹
  `C:\PearnlyStress\_cctest\SINCERE\`（从语料原样复制，不改原语料），识别走回放、零费用。

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
  未带过的提醒包括本对话的、本用户没挂对话的，以及本用户挂在别的对话上、24 小时内的（复查 business-logic-3）；只把真带进系统消息的几条
  按记录名标成 carried。`notices.owned_conversation(user, candidates)` 给产品挑出第一段仍归该用户的对话；`for_references` 每条带 `refs`。

**B. 共用层手机网页推送（`assistant/webpush.py` 纯加密、`assistant/push.py` 服务）**

- 白名单：`pearnly_core.assistant.api.push_key`（GET，回 `{"key": <VAPID 公钥 base64url>}`）、`push_subscribe(endpoint, p256dh, auth)`（POST）、
  `push_unsubscribe(endpoint)`（POST）。都要登录。
- `push.send_notice(notice)`：读提醒，`push.send(user, title=title or "CC", body=text, url=contracts.deep_link(session_id), tag="notice:"+id, ttl=ttl)`。
- 后台脚本点通知：已有 `/assistant` 窗口就聚焦并 `postMessage({type: "pearnly-open", url})`，没有就 `openWindow(url)`。
- 订阅行记认领时登录会话编号的摘要（`sid`），退出登录（`on_logout`）删掉本会话的订阅，发送前核对会话还在（复查 security-2）。

**C. 前端（`frontend/assistant`）**

- 地址参数：`/assistant?c=<对话>` 打开那段对话（不是本人的或不存在就打开定时任务页，没有定时任务就新对话），`/assistant?view=tasks`
  打开定时任务页；处理完用 `history.replaceState` 回到 `/assistant`。另收后台脚本消息 `pearnly-open` 与窗口事件 `tomo-open`（detail 为对话编号，
  空串 = 定时任务页）；`window.tomo.open(target)` 派发该事件。
- 实时：`realtime.ts` 加通用 `onSiteEvent(event, handler)`、`onConnectionChange(listener)`；`session_id` 为空的 `pearnly.notice` 也要派发
  （现在会被丢掉）。定时任务页监听 `cowork_folder_changed`（载荷 `{rule}`）。

**D. Cowork 定时任务服务（`folder_intake`）**

- Rule 新字段：`include_existing`（Check，默认 0，新建 = 1）、`remind`（Check，默认 0）、`remind_since`（Datetime）、`cc_session`（Data 140）。
  Run 新字段：`run_owner`（Link User）、`slot_at`（Datetime）、`brief_state`（Select `pending/sent/skipped`，索引）、`brief_at`、`brief_key`、
  `brief_group`（定时 = 时刻；立即运行 = 同一负责人 10 分钟内开始的一批；补传沿用父运行）。简报 = 负责人 + 分组 + 读文件夹的电脑；
  提醒 = 负责人 + 时刻 + 时段 + 读文件夹的电脑（复查 interfaces-1 / security-1）。新任务的文件夹与同一工作空间的停用任务重叠时
  `include_existing=0`（接续，只收此后放入的；`rules.continues_from`，卡片写明，复查 business-logic-1）。
  File：`source_file` 加索引。补丁 `v0_0_16_folder_run_briefs`（历史运行置 `skipped`）。
- `rules.save(values, *, cc_session=None)`：`values` 可带 `remind`（0/1/bool），不带保留原值；`cc_session` 只从参数取，绝不从 values 取。
- `rules.save_many(items, *, cc_session=None) -> {"saved": [project…], "failed": [{"task": 序号(1 起), "title", "reason"}]}`，`BATCH_MAX = 20`，
  任一失败整批回滚（保存点），失败项之后的仍校验，一次报全。
- `rules.set_enabled(name, enabled=None, *, remind=None, remind_times=None, cc_session=None)`：None = 不变；网页 `api.set_enabled(name, enabled)` 签名不变。
- `rules.listing()` 每行加 `include_existing`、`remind`、`waiting`（待处理数，不知道为 None）、`waiting_partial`；`results` 已有 `moving`
  （改为不计 24 小时前仍停在 registered 的行）。`rules.project()` 加 `include_existing`、`remind`、`remind_since`。
- `rules.waiting(reader, root_id, folder) -> {"files": int, "partial": bool} | None`（文件夹计数，给新任务卡片和表单）；
  `rules.account_label(connection, account_set_key) -> str`、`rules.master_names(connection, account_set_key) -> {"accounts": {code: name}, "departments": {code: name}}`
  （给卡片预览用的公开薄入口）。
- `rules.options()` 的 `readers[].roots[]` 加 `counts`（`{文件夹: n}`，键是上报的文件夹相对路径，`""` 是根目录）与 `partial`。
- 实时事件 `cowork_folder_changed`，载荷 `{"rule": <名>}`，推给负责人（及文件的 actor），提交后发。
- 提醒时刻可自选（2026-10-13，用户：「提醒规则…可以自定义选择规定的提醒时间就触发提醒」「那天，那个时间段都可以自定义」）：
  Rule 新字段 `remind_times`（Small Text，只读，JSON `[{"days_before": 0–7, "time": "HH:MM"}]`，最多 3 个；空 = 默认前一天 08:00、14:00，
  老任务行为不变）。纯规则 `contracts.remind_times` 校验、去重、按离运行由远到近排；`rules.save` 的 `values` 与
  `rules.set_enabled(name, enabled=None, *, remind=None, remind_times=None, cc_session=None)` 带了才改，带了而没带 `remind` 就打开提醒，
  生效的一组变了 `remind_since` 记成现在；`project` / `listing` / CC `cowork_folder_tasks` 回生效的 `remind_times`。每个提醒时刻 =
  运行日减天数的那个曼谷时间，从它发到同一任务下一个更晚的提醒时刻（最后一个到运行时刻），落在运行时刻当时或之后的不发；合并与去重键 =
  负责人 + 运行时刻 + 提醒时刻（`remind_label`：前一天整点沿用「08」「14」，其余「<天>d<HH:MM>」）+ 电脑。文字：今天 / 明天照旧，两天及以后
  写日期（`cowork.folder.remind.date`）。CC 三个任务工具都收 `remind_times`，卡片写「前 1 天 08:00 / 当天 07:05」；网页（CC 任务页
  原生时间输入、Desk 原生 Select + Time）打开提醒后最多 3 行「当天 / 前 1–7 天｜任意时间」。永远发不出的不静默存：提醒开着时
  「当天」的时间要早于运行时间，否则 `save` / `set_enabled` 拒（`cowork.folder.remind_same_day`「当天提醒要早于运行时间」），改运行时间让
  已有的当天提醒发不出也同样拒；「添加时间」与 Desk 空行按 前 1 天 08:00、14:00、前 2 天 08:00、前 1 天 18:00 取还没有的那个。
- 提醒与简报只经 `folder_intake/notify.py` 调用共用层 `notices.post`；调度入口 `pearnly_cowork.folder_intake.schedule.notices(now=None)`
  挂每 5 分钟；`now` 是站点时区时间字符串，给测试环境手动触发。
- F6 钩子：`hooks.cowork_push_fixed_choices = ["pearnly_cowork.folder_intake.push.fixed_for"]`，
  `fixed_for(history, connection, account_set) -> dict`（取不到回 `{}`，不抛错）；`push.application.service` 在调用方没给 fixed 时按钩子取，
  `choices_view` 回 `"fixed"`（非空时），`save_choices` 拒收被任务默认值决定的类别（词条 `cowork.choice.task_default`）。

**E. 小助手 ↔ 服务端文件夹同步（向后兼容，不改协议号）**

- 同步请求新增：`rule_pending`（`{规则名: int}`，上一轮算的待处理数）、`roots[].counts`（`{文件夹: n}`）、`roots[].partial`（bool）、
  `notices_after`（epoch float，新版才带）、`runs[].slot`（该次运行所属定时时刻 epoch，可缺）。
- 同步回复新增：`rules[].existing`（0/1，缺 = 0 = 照旧拍底）、规则下发顺序进货任务在前、`rule_names`（本机读的全部任务名，含停用的，
  小助手据此清掉久未出现任务的台账）、`notices`（只给涉及任务全归本机的那几条；请求带了 `notices_after` 才回，
  最多 20 条、12 小时窗，字段 `NOTICE_FIELDS = ("id", "title", "text", "created", "session")`）、`companion_latest`（同心跳的形状）。
- 小助手 1.4.4：按 `existing` 决定拍不拍底；台账墓碑；托盘按 `"notice:"+id` 去重弹通知，点了打开那段对话（没有就定时任务页）；
  只读文件夹的成员也从同步回复拿新版提示。

**F. 词条归属**（防止并行重复加）：待处理数用 `cowork.folder.pending`，文件夹计数用 `cowork.folder.waiting`，接续用 `cowork.folder.continues`；`cowork.folder.*` 新词条与 `cowork.folder.waiting_purchases` 归定时任务服务；工具标签归工具；
`cowork.choice.task_default` 归 F6；共用层只加一条 `This device cannot receive notifications.`；前端词条只在 `frontend/assistant/src/i18n.ts`。
翻译包 `translations/*.csv` 由合并后统一 `i18n-build.py` 生成。

## 第一批实现与验证（F1–F9，顺带 L6 / L7；分支 `pearnly/cc-closed-loop`，自检到 a4c1af5c）

### 实现（8 路并行 + 复查修复，合并到本分支）

- 共用层：对话提醒 `assistant/notices.py` + `Pearnly Assistant Notice`；手机网页推送 `assistant/webpush.py`（RFC 8291 / 8292，只用
  cryptography）、`assistant/push.py`、订阅与 VAPID 两张表、`service_worker.js` 推送与点击；`sessions` 并入提醒、未读、随话带给 CC；
  F8 当地日期（批末唯一一条 `system.message`）。
- Cowork：`folder_intake` 的 F1（`include_existing`、计数与待处理数、接续停用任务）、F2 提醒 `reminders.py`、F3 简报 `briefs.py`、
  `notify.py`（唯一调用共用层的地方）、`live.py`（`cowork_folder_changed`）、F7 进货先写（`pump`）、F4 `rules.save_many`；
  F6 推送服务钩子 `cowork_push_fixed_choices`；CC 工具批量保存、默认值、提醒、份数、L6 列表、L7 说明；Desk 定时任务页。
- 前端：提醒卡、未读圆点与浮顶、推送开关、深链接 `?c=` / `?view=tasks`、桌面打开对话、定时任务页处理中与自动刷新、错误边界。
- CC 桌面端 1.4.4：新任务不拍底、计数与待处理上报、台账墓碑与清理、运行中途打断续跑、托盘合并提醒、电脑通知改用 Windows 正式通知
  （`windows-toasts` 1.3.1 + winrt 3.2.1，锁进 `requirements-build.txt`，打包 hiddenimports 已补；失败退回托盘气泡）。
- 复查：6 个方向 33 条发现（核实 31 条成立 / 可能），全部修复或记为限制；修复各带回归测试，并在修复前代码上确认会失败。

### 验证

| 时间 | 代码 | 环境 | 场景 / 命令 | 结果 |
| --- | --- | --- | --- | --- |
| 10-13 01:15 | a4c1af5c | 本机 | `development.yml` 全部同名闸门（58 项）、CC 桌面端 pytest、推送加密测试 | 全过；桌面端 655 / 38 跳过；RFC 8291 附录 A 逐字节一致 |
| 10-13 01:35 | d67c914d | 测试机，生产镜像 faaac313 | 完整 `ci-candidate-checks offline`（挂候选代码） | LANE EXIT 0（含 `test_assistant_push` 38、`test_cowork_push_account` 10；镜像 cryptography 50.0.1） |
| 10-13 | aeb3946b / d67c914d / a4c1af5c | GitHub | Development checks、Companion checks（Windows 测试 + 打包） | 均 success |
| 10-13 00:37 | aeb3946b | 测试机 testcowork | F8：引擎目录 4 个模型各发一条带日期的系统消息 | 全部接受，均答「2026 年 10 月 13 日，星期二」 |
| 10-13 01:28 | d67c914d | 同上 | 第 4 节「发完消息就锁屏」：只发消息、不调页面兜底 | 工具由服务端转发线程执行，执行记录 1 条 done |
| 10-13 01:35–01:38 | d67c914d | 同上，CC 1.4.4 | 先放 6 份到 `_cctest\SINCERE`，再让 CC 一句话建 3 个任务 | 一张卡片列 3 行（含「现有 2 / 1 / 3 份，到点一起处理」「提醒：开」）；允许后 3 个任务 `include_existing=1`、`cc_session` 记对 |
| 10-13 01:39 | 同上 | 同上 | `schedule.notices(now='2026-10-13 08:05')` | 合并一条「明天 19:00 有 3 个定时任务开始（还有 1 天 10 小时）……（还有 N 份未处理）」，挂对话、侧栏未读；下一句话 CC 复述了提醒 |
| 10-13 01:43–01:48 | 同上 | 同上，回放识别 | CC 立即运行 3 个任务 | 6 份全部处理（回放库 6 个检查点、零谷歌调用）；水费 RR690127-001、电费 HP690617-001 写入 T9SINCER；4 张按设计停下（缺对方 / 付款 / 科目） |
| 10-13 01:52 | 同上 | 同上 | F5 / F6：CC 给进货任务设默认现金 + 抵扣，再查重推 | 卡片显示两项默认值；重推只需选对方与科目；改付款被拒（「由定时任务的默认处理决定」），账套记忆行数不变 |
| 10-13 02:00 | 同上 | 同上 | F3 立即运行的简报 | 一份三任务简报（已推送 2；待处理 2 / 3 及原因；结果文件夹位置），写进对话；距最后一份完成 12 分钟（当时等待 10 分钟） |
| 10-13 02:06–02:12 | a4c1af5c（服务端） | 同上 | 任务定在「现在 + 3 分钟」按时运行；简报等待改为 3 分钟 | 到点后 2 分钟内服务端收到「按时」运行记录；简报 4 分 10 秒写进对话（「没有新文件」） |
| 10-13 02:15 | a4c1af5c，CC 1.4.4（28adcb19） | 测试机锁屏 | 14:00 时段提醒经文件夹同步送到电脑 | Windows 正式通知进通知中心（标签 = 提醒编号，正文绑定数据完整）；原托盘气泡不进通知中心 |

| 层级 | 状态 | 依据与未覆盖项 |
| --- | --- | --- |
| 实现 | 通过 | 见上 |
| 本地验证 | 通过 | 闸门、单测、候选离线 |
| CI | 通过 | a4c1af5c 两项均 success |
| 部署 | 未执行 | 下一步按批次部署 |
| 外部业务结果 | 部分 | T9SINCER 写入 2 张（用户确认清单内）；手机推送没有真实设备订阅，未端到端 |
| 用户 / 真机验收 | 未执行 | iPhone 主屏幕 app 收推送、点电脑通知打开对话需人在设备前；F7 只做了单测与源码守护，未在存货账套实测 |

已知限制：删除的任务不留痕迹，删后重建会重新处理文件夹里已有文件；同一负责人的任务分在多台电脑上时提醒 / 简报各发一份；只读文件夹
的成员要手动装一次 1.4.4；长时间首轮上传期间提醒延后到下一次同步；Desk 新建任务表单在接续停用任务时仍显示「现有 N 份」；
Windows 正式通知的点击在 CC 运行时才能打开页面（没有注册系统级激活）。

## 第二批实现（集成 399e3e81）

分支 `pearnly/ccl-b2-integrate`，基线 `origin/main` 3a16e261；依次 `--no-ff` 合入 `pearnly/cc-closed-loop`（第一批发布记录）、
`ccl-remind-times`、`ccl-b2-presets`（L8）、`ccl-b2-channel`（L3 + 按需通道 + L2）、`ccl-b2-readback`（L1）、`ccl-b2-tax`（L4 + L5）、
`ccl-b2-pos`（L9），再补 6 个集成修正（见下）。L6、L7 已随第一批上线；L7 的说法在 L1 合入后补了「改完用回读看」。
未推送、未合入 main、未部署。

### 提醒时间自定义

- Rule 新字段 `remind_times`（≤3 个 `{days_before 0–7, time HH:MM}`，空 = 前一天 08:00、14:00，老任务不变）；CC
  `cowork_save_folder_task(s)` / `cowork_set_folder_task_enabled` 收 `remind_times`，卡片列出时刻；CC 任务页与 Desk 对话框最多 3 行。
- 开关文字「前一天提醒」改「提醒」；提醒开着时「当天」时刻要早于运行时间，否则拒（`cowork.folder.remind_same_day`
  「当天提醒要早于运行时间」），改运行时间让已有的当天提醒发不出也拒。口径细节见上文 D 节。

### L3 工具产出再当输入

- 共用层 `assistant/files.add_to_conversation(..., origin="tool"|"folder")` 在站点私有目录留一份（`tool/`、`folder/`），
  `attachment()` 按编号 / 路径 / 文件名都取得到，对账、盘点工具不用改。识别工具只拒 `origin == "tool"`（`made_by_tool`）。
- 集成修正：报税工作簿与回读工作簿一样显式 `origin="tool"`。

### 按需请求通道（L1 / L2 的底座）

- 私有表 `Cowork Companion Request`，只经 `companion.request_service` 写；两条机器方法 `request_api.next_request` /
  `complete_request` 登记在 `http_auth.REQUEST_METHODS`。状态 requested → leased → done / failed；10 分钟没人领、租约到期算 expired。
- 结果按 4 MiB 分块续传，末块核大小与 sha256，存站点私有目录、取走即删；同一人同一连接最多挂 20 件；7 天以上的旧件在下次新建时清。
- 发起方一律 `find` → `waiting` → `create`：同一请求号还在等、在做、做完没取走就接着等，不问电脑在不在；有未到期租约也算在线。
  `MIN_COMPANION = 1.5.0`，旧版回 `waiting_for_cc / update_needed`。
- CC 桌面版 `RequestWorker` 独立守护线程（15 秒一问），不碰推送线、看门狗、账套锁；托盘与文件夹线程一起起停，自动更新等手上这件做完。

### L2 从已授权文件夹取文件

- CC 工具 `cowork_folder_files`（列：路径、大小、曼谷时间；按子文件夹 / 搜索词 / 类型筛；一页 100、最多 200，10 分钟内翻页不重列）、
  `cowork_folder_fetch`（一次 ≤5 份，`origin="folder"` 进对话，可直接给对账、识别、POS 导入）。只读、不识别、不扣费、不弹允许卡。
- 电脑离线 / 太旧回 `waiting_for_cc`（不建请求）；列等 45 秒、取等 90 秒，还没完回 `reading`，原样再调接着等，已取进的不重取。
- 桌面端 `folder_fetch`：只认本机加过的根目录，拒 `..`、盘符、UNC、绝对路径、链接 / 联接点跳出；类型同对话附件、单份 30 MB；
  列文件 10 秒 / 5 万项 / 12 层 / 5000 份上限，超出标 truncated。

### L1 回读 Express

- CC 工具 `cowork_express_readback` / `_status` / `_delete`（不弹允许卡、不扣费）；最多等 90 秒，还在读回 `reading`。
- 桌面端 `express_read`：只读推送同一组授权的账套；不调 `account_lock`、不开 ISRUN，要读的表先共享读复制到本机临时目录（Express
  开着也能读），被独占隔 3 / 10 / 20 秒重试后回 `account_busy`；复制前后各记一次源文件的大小、修改时间与表头，有变化（复制途中
  推送或 Express 正存一张单）整份隔 5 秒重读，读满 3 次仍在变照读并报 `snapshot_changed`（服务端转成提示
  `books_changed_while_reading`）；借贷不平整份隔 5 秒重读一次；金额一律 Decimal 两位。
- 服务端 `readback` 域：期初 = BEGLY + Σ(DEBIT{k}LY − CREDIT{k}LY) + 本月前未作废凭证行（照原型 xmake 逐项对照过）；出总账、销项税报、
  进项税报、差异表四份工作簿，存私有目录，同一人同一连接同一账套同一月只留最新一份，只给发起人。私有表 `Cowork Express Readback`。
- 总账在 Express 八列后另加一列科目号：收入对账解析器没有科目列时会把收入段后的费用行记到收入科目上。
- **改了 `reconciliation/gl_sheets.py`**：科目标题行的科目名不再当期初标签，给了科目后别的科目段里的期初行不算。Express 自导的
  全科目总账同样受影响，只在回读侧规避盖不住用户上传件（旧代码银行期初读成 −252000，修后 1100）。
- 差异按 CC 桌面版实际写法比：现购 HP / 现销 HS 记账套现金科目、赊购 RR / 赊销 IV 记对方档科目、期末盘存制进项存货换购货；进项票号按
  `fit_ref_no` 缩法比；另列删除、找不到、作废与直接录入。处理出错记 `process_failed`，不停在 reading。`vat_records` 供 L4。
- 集成修正：发起改为 `find` → `waiting` → `create`（请求号由人、连接、账套、月、科目定）；POS 日汇总同一次写入的转账凭证
  （推送回执 `meta.pos_vouchers`，原先被回执白名单整个滤掉，现由 `sanitize_push_meta` 从 `notes.pos_vouchers` 留下）算推送过的、
  点名读回、按「借 收款科目 / 贷 现金」比，删 / 作废 / 改科目记在当天 HS 的分录项下；POS 的 HS 本身（自编单号、YOUREF 与 REFNUM 是
  区间、借现金）照现销比。
- 读账参数 `request_contracts.PARAMS_MAX` 8 → 64 KiB（点名 ≤2000 个单号）；推送记录按月初前 92 天以后建立的取，点名的按单号查。

### L4 ภ.พ.30 草稿与进项核查 / L5 ภ.ง.ด.3 / 53 草稿

- 新域 `tax`，入口只有 CC 工具 `cowork_vat_draft`、`cowork_wht_draft`、`cowork_wht_answer`、`cowork_tax_draft_delete`（无 Desk 页）；
  功能码 `tax_drafts`（default_on）、角色 Cowork Tax Filing；补丁 `v0_0_17_tax_drafts_feature` 给所有 Cowork 站点补功能码，并把角色发给
  已有 Cowork VAT Check 的启用用户。预扣税草稿另要识别记录读权限。
- ภ.พ.30：没有回读回 `readback_needed`；只取 VATPRD 在本月的行，第 1–12 栏为合计，13–16 栏留空；核查只标不改数（进项缺 / 错税号、
  早于开票月、超 6 个月、6 个月内顺延提醒注记、简易税票与非税票、买方缺失 / 别家、重复；销项期间、按日期税率表核税额容差 0.02）。
  摘要含「อย่างย่อ」的 POS 日汇总行照常计入、不逐行核税率（交叉测试锁住 CC 桌面版写的摘要确实带这几个字）。税率表只到 2027-09-30。
- ภ.ง.ด.3 / 53：取识别记录里印了预扣税的费用票、50 ทวิ 与参考记录；税号 0 开头进 53、0991 / 0992 与其余进 3；按付款日落月（凭证日 >
  当场已付的票据日 > 票据日并标「推定」）；票与凭证按收款方一对一 / 多对一 / 一对多配对（±45 天；付款日只是推定的赊账票，凭证在
  票据日之后 90 天内也配，取数相应往月末后多看 90 天、票与凭证都取），配不清标 `pairing_unclear`、
  方向不清标 `certificate_direction_unclear`，都不计入；所得类型按收款方或分类问一次记住（`Cowork WHT Choice`）；ใบแนบ 每页 6 名、
  每名 3 行；1% 服务类标 `ewht_check`。
- 50 ทวิ 参考记录（用户 10-13 定）：租户选「不录入」时识别存档同一事务用保存点写 `Cowork WHT Certificate`，不推送、不进待办、
  不新增识别调用；录入工作台删原件时一并删。
- 草稿 `Cowork Tax Draft` 只存各栏、合计与问题计数（`service.kept`），行级明细只在本次返回与工作簿里；写前只锁这份草稿。

### L8 通用版式银行对账单记预设

- 只做 L8a：`GENERIC` 账单有 4 位尾号时按「GENERIC + 尾号 + 总账科目」记预设，问一次后沿用；尾号只采信识别读出的账户或表头上方
  标签旁的账号（`account_read`），不用表名退路（挡住「Jan 2026」被当尾号）；结果页存档指纹按本任务尾号收窄。
- 「กรุงเทพ 认成 BBL」没改：同一版式稳定认成 BBL，预设照样记得住，错的只是标签与导出名；改了会让所有文字层 PDF 对账换银行码、
  已记在 `bank|BBL|…` 下的账户全部重问，且 `detect_bank` 被旧线上源码对照测试固定。要改须先在测试机对压测语料跑零费用对照。

### L9 POS 导出按天录销售

- 新域 `pos_import` 与共用纯契约 `contracts/pos.py`；CC 工具 `cowork_pos_import`（对话附件或 L2 取来的文件）、`cowork_pos_layout`
  （答认列问题，也可答定时任务里停着的文件，答完续跑）。读 .xlsx / .xlsm / .csv，.xls 回 `pos.xls_not_supported`。
- 通用认列，认不准才问（缺列、同类多列、日月顺序、含不含税、新状态值），按「公司 + 表头指纹」记住（`Cowork POS Layout`）。
- 每天一张现金客户简易税票：Decimal 逐日与 POS 合计对平；有税额列取逐票税额之和，没有按合计 × 7/107 四舍五入、尾差落税前额；
  作废不算、退款记负；参考号 ≤15 字节（区间 → 缩写 → ABB + 佛历日期）。不识别、不扣费、不查重。
- 推送载荷 3，Express 原生记法：HS 单号自编（HS + 佛历年月日 + 序号）、借现金 ACCNUM00；ISVAT S 每天一行，摘要
  「ใบกำกับภาษีอย่างย่อ 起-止」、REFNUM 区间；每种非现金付款方式同一次写入一张普通日记账凭证（单据簿 = ISRUN 里 DOCTYP GL、DOCCOD 00 那一行的单别，真账套是 JV；JNLTYP 00 的是别的单据，不按它找）
  「借 收款科目 / 贷 现金」，同锁、同备份、同回滚、逐张读回。收款科目是推送选择 `receipt`，按公司问一次（同公司别的年度账套记过且
  科目在本账套就带出）。单据簿定不下来 / 科目不能记账在写前停（`JOURNAL_BOOK_NOT_FOUND` / `ACCOUNT_NOT_POSTABLE`）；只推 Express
  （`pos_express_only`）；非 POS 载荷与基线逐字节相同。
- 定时任务：推往 Express 的销售任务另收 POS 导出（同步下发 `extensions`），到点先建天再交识别；认不准停在 `pos.layout_needed`（算停下
  待人）；结果表每份导出一张 `<文件名>_POS.xlsx`，每天一行带推送状态与 ERP 单号；1.4 的老任务与 1.4.4 时建的新任务对新收的
  扩展名先补拍底再扫（新任务记 `seen_exts` / `added_baseline`；1.5 上第一次见到的新任务、换了文件夹的老任务不补拍）。

### 集成修正（合并后单独提交）

| 提交 | 内容 |
| --- | --- |
| 08c94441 | 回读发起 `find` → `waiting` → `create`，测试补「租约中不判离线」「记录丢了接上原请求」 |
| d4e22a48 | 报税工作簿 `origin="tool"`，工具测试核 origin |
| 8b5d1330 | 回执保留 `pos_vouchers`；回读认 POS 转账凭证、比其分录；POS 日 HS 比对测试 |
| 64c99de8 | 交叉测试：POS 日销项行（CC 桌面版摘要）进 ภ.พ.30 合计、不报 `output_rate` |
| 26915aef | CC 桌面版 1.4.4 → 1.5.0（`version.py` 单一来源），契约测试锁住版本 ≥ `MIN_COMPANION` |
| 399e3e81 | `i18n-build.py` 重生成 core / cowork 四语翻译包 |

### 接缝审查修正（合并后第二轮审查）

| 审查项 | 处理 |
| --- | --- |
| 读账副本单据有、凭证没有（中） | `express_read.snapshot` 复制前后比源文件戳，变了整份重读；读满仍变报 `snapshot_changed` → 提示 `books_changed_while_reading`；测试按推送写入顺序在复制途中写一张单 |
| 同时两次一样的回读共用一件请求（低） | `readback.service._drop_request` 有另一份还在读就不删请求，`_drop_older` 不删共用同一件请求、还在读的那份；两份都出结果 |
| 赊账票 + 60 天后的凭证两个月各算一次（中） | `wht._reach`：推定付款日的票与票据日后 90 天内的凭证也配；`sources.wht_documents` 往月末后多看 90 天、票与凭证都取（别的月配给后一张票的凭证这个月也配给它，原 7 月票不再悄悄计入或丢掉，进待核）；原「远的分开」测试改为当场付的分开、赊账的配上 |
| 两组回归不在任何 CI 通道（中） | `ci-candidate-checks.sh` offline 登记 `test_cowork_bank_sheets.py`、`test_cowork_reconciliation_gl_sheets.py` |
| 1.4.4 时建的新任务升级后把现有表格当 POS 导出（中） | 小助手对新任务也按扩展名补拍底（见 L9）；测试覆盖 1.4.4 新任务升级与 1.5 新见 / 换文件夹两种 |
| 产品清单 must_not 一条写成否定句（低） | 改回禁止的写法，用户定的正面口径写在注释 |

`architecture-policy.json` 合并即正确，无需另改：`readback`（access / companion / connections / push / targets）、`tax`（access / contracts /
history / readback / reconciliation）、`pos_import`（access / connections / contracts / files / history / ocr / tabular / targets）与实际导入
一致、无环；`assistant` 依赖三者，`folder_intake` 依赖 `pos_import`。

### 验证

| 时间 | 代码 | 环境 | 场景 / 命令 | 结果 |
| --- | --- | --- | --- | --- |
| 10-13 | 399e3e81 | 本机 | `development.yml` 全部同名闸门（67 步，含 `npm ci` 与前端构建） | 全过；`check-architecture` 0 违规、`i18n-build --check` PASS |
| 10-13 | 399e3e81 | 本机 | CC 桌面端 pytest | 721 过、38 跳过（Windows 专用） |
| 10-13 | 399e3e81 | 本机，带 openpyxl | `readback_workbooks` / `tax_workbook` / `pos_xlsx` | 12 / 3 / 3 过 |
| 10-13 | 接缝审查修正 | 本机 | `development.yml` 全部同名闸门（67 步；`npm ci` 首轮跑过、复跑跳过） | 全过；readback 41、tax_wht 40、tax_service 24、folder_intake 97 |
| 10-13 | 接缝审查修正 | 本机 | CC 桌面端 pytest | 725 过、38 跳过 |
| 10-13 | 接缝审查修正 | 本机，带 openpyxl | 上面三个 + `bank_sheets` / `reconciliation_gl_sheets` | 12 / 3 / 3 / 36 过；gl_sheets 30 过、3 条缺 Frappe（候选镜像通道跑） |

主要单测：companion_contract 31、companion_requests 24、readback 39、folder_intake 97、pos_import 28、folder_reminders 38、
folder_task_batch 20、assistant_tools 93、tax_pp30 28、tax_wht 38、tax_service 24、assistant 74、http_auth 19。

| 层级 | 状态 | 依据与未覆盖项 |
| --- | --- | --- |
| 实现 | 通过 | 见上 |
| 本地验证 | 通过 | 闸门、单测；需要 Frappe 的候选离线通道（`recon_presets`、`recon_view_store`、三个 openpyxl 测试、`push_account` 等）未在生产镜像跑 |
| CI | 未执行 | 未推送 |
| 部署 | 未执行 | — |
| 外部业务结果 / 真机验收 | 未执行 | 见下 |

### 部署与合入前

- 改了 `pearnly_core`（`assistant/files.py`、词条）：合入前在生产镜像跑完整 `ci-candidate-checks offline`（含新登记的
  `readback_workbooks`、`tax_workbook`、`pos_xlsx`、`bank_sheets`、`reconciliation_gl_sheets`；后者 3 条用例要 Frappe，本机跑不了）。
- 新表随 `bench migrate`：Cowork Companion Request、Cowork Express Readback、Cowork Tax Draft、Cowork WHT Choice、Cowork WHT Certificate、
  Cowork POS Layout；补丁 `v0_0_17`。先部署服务端，再发 CC 桌面版 1.5.0。
- `reconciliation/*.py` 变了：部署后结果页存档先给旧的、后台重算（曾约 490 CPU 秒）。

### 还要在测试机 / Express 上做

- 通道与 L2：1.5.0 staging 版列 2000 份以上的文件夹、取 25 MB PDF、取来的对账单直接对账；慢上行时重调不判离线；NTFS 联接点与符号链接
  跳出被拒、Excel 开着的文件、映射盘 / 网络共享；MR.ERP 只读成员；生产 nginx 收 4 MiB 分块；深于 12 层的目录标 truncated。
- L1：Express 开着 / 录单 / 整理时各读一次；总账逐科目对打印件（非 1 月起账、推送后月份的 GLBAL 期初）；税报排序、作废显示、顺延进项；
  现购现销与期末盘存制月份差异表不误报；真实 ISINFO 列；佛历日期格式；大账套耗时；银行 / 收入 / 销项三种对账端到端；POS 日的转账凭证
  不进直接录入。
- L4 / L5：9 月 ภ.พ.30 草稿对 Express 税报与 ภ.พ.30 打印逐项对照（VATPRD 选行、作废、减项单正负、排序）；税期改到 +7 个月报错；
  50 ทวิ 付款 / 收款方字段与税额字段用回放库核；一张凭证付几张票、分期、跨月的配对与会计手工对照；删混合文件后参考记录消失。
- L8：零费用语料对比，有标签账号的表格账单问一次记住，有银行名的不变。
- L9：JV 单据簿、凭证号与 SEQIT、ISRUN 计数器；Express 能否打开 / 打印 / 修改自编 HS 与 JV；销售与 ภ.พ.30 销项报表每天一行带区间、
  现金与银行明细对平；真实 Ocha / Loyverse 导出；存货路径与 0 元赠品；现金净额为负的一天；1.4 → 1.5 老任务不重传旧表格；
  `.xls` 显示失败与原因；跨年度带出收款科目。写 T9SINCER 前先把写入清单给用户确认。

### 已知限制

- 通道：没取走的结果只在同一连接下次新建请求时清；多租户同一台电脑做 A 的长请求时 B 可能显示离线一个租约期；电脑中途掉线到租约
  过期前仍算在线；25–30 MB 的文件能进对话但对账单份上限 25 MB；根目录下名字以空格开头的文件列得出取不回。
- L1：复制途中原地改写（不改大小）且网络盘上修改时间与表头都还没变的，比不出来（推送与新录单都是追加，会变大小与表头记录数）；
  说明含 ยกมา / ยอดรวม 的行会被当期初 / 合计（与 Express 自导一致）；直接录入工具只显示前 20 条；存货单不比分录；对方档科目
  按回读当时判断；推送后改了供应商档科目会报改过；92 天窗口外极端情况漏查；没有 Desk 页面。
- L4 / L5：参考记录无界面删除入口（只能 include=false）；改到别的月的付款日只存在本月草稿；赊购真实付款日没读；e-WHT 只提示；
  节假日不顺延；识别记录取前 90 天到月末后 90 天、最多 5000 张（超出标 truncated）；赊账票付款超过 90 天才开凭证的仍按票据日计入、
  凭证月再计一次（超出取数窗口）。
- L8：读不出尾号的 GENERIC 账单不记不问；同一账户一次 KBANK 一次 GENERIC 会存两份预设；不同公司尾号与科目都相同会共用。
- L9：收款科目跨年度靠公司名一致；Loyverse 商品级导出没有付款方式列整天按现金；一票多种付款方式按一种非现金记；不查重（改名重放
  会再记）；多台收银机同一天各出一张；`cowork_recognize_attachments` 不自动转走 POS 导出（模型选错会按识别扣费）；同时有
  Payment status 与 Status 两列时状态列不采用；刷卡记含手续费全额；服务端部署后、这台电脑还没升到 1.5 之间新建的销售任务，
  升级时文件夹里已有的表格按「任务那时不收」补拍底、不处理（发版紧跟部署，窗口很短）。
