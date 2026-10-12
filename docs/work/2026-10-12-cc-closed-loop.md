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
