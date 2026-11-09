# CC 附件上传服务端提速（U0 + U2）与同一对话并发上传 500（2026-11-09）

- 来由：研究 `docs/work/2026-11-08-cc-upload-ux-研究.md`（主会话 11-09 审定）里服务端那一半交本窗口：U0 分段计时、U2 并行化，
  目标 10MB 级文件服务端回话 ≤5 秒；主会话 11-09 选 A：U0+U2 照现状收尾随下一批部署，部署后读生产 U0 日志，「存原件 + 交识别」超过 3 秒再做 U2b'。
  另派：查 11-08 晚 erpcowork 并发上传 9 个 500。
- 分支 `pearnly/cc-upload-fast-server`，基线 origin/main `7f5f47da0`。前端（`attachment.tsx` 等 U1 / U1b / U3–U5）归 CC 一套窗口，本分支不碰。
- 与 CC 一套窗口核对过（11-09）：它的分支不改 `files.py`、`express_operator/uploads.py`，`anthropic_managed_agents.py` 只改沙箱说明文字、
  `operator_source.py` 只改 precheck / submit / status，不交叠。

## 改了什么

| 项 | 内容 | 文件 |
|---|---|---|
| U2 | 字节传引擎（`api.upload`，只做网络调用）放线程里，与站点这边同时做：开 / 接续远端会话、代录交任务（存原件 + 交识别）、本站留存、读页数；两边做完再挂进会话（`api.mount`）。引擎一时没收：同一个上传编号再传一次，任务只交一次 | `assistant/files.py` `_upload`、`_Background` |
| 上传编号本站先定 | `upl_` + 32 位十六进制（`contracts.file_id` 认得）。挂载位置、本站留存、页数缓存、交任务的请求键都用它，不必等引擎回编号；会话文件列表、消息里的附件、工具取文件本来就按挂载位置里的编号认，老对话（引擎编号）照旧 | `files.py`、`engines/base.py`（新 `upload` / `mount`）、`engines/anthropic_managed_agents.py`（`attach` = `upload` + `mount`，工具放进对话的文件照旧用引擎编号） |
| U0 | 每份上传记一行 `pearnly.assistant.upload`：大小、页数、是否代录、各段秒数（read / session / task / keep / engine_wait / engine / mount / total），不带文件名与票面。站点日志默认只记 ERROR，这个日志自己设 INFO | `files.py` `_Steps` |
| 并发 500 修复 | 对话到了计价界线时的接续（`segment_for_turn`）按对话排队：拿锁先提交结束本事务、重读记录，前面已接续就用新的那段；接续完先提交再放锁 | `assistant/sessions.py` |

测试（改前红、改后绿）：`test_upload_sends_bytes_to_the_engine_while_the_site_side_works`（串着做 1.31 秒超 1.05 上限；重试同编号、任务只交一次；
计时日志在生产级别下留得下、不带文件名）；`test_requests_arriving_together_at_the_limit_open_one_new_segment`（改前两个请求开 2 段写 2 次，改后 1 段 1 次）。

## 计时（真数）

| 场景 | 数 | 来源 |
|---|---|---|
| 新加坡 → Anthropic 文件库，10.6MB | 1.6 / 5.2 / 1.6 秒 | free-arm（与生产同在新加坡）curl，评测密钥，传完即删 |
| 本机实验栈，真引擎（评测密钥）+ 本机存原件，10.7MB 24 页，**改前** | 代录首份 3.3–5.1 秒，再传 2.7–3.4；直写 2.4–2.9 | 客户端计时，3 轮 |
| 同上，**改后** | 多数 2.3–3.4 秒；一次 7.3 秒（那次引擎本身 6.6 秒） | U0 日志，2 轮 |
| 生产 11-08 事故那份（24 页 10.6MB，代录） | 后端 13.35 秒 | `/var/log/nginx/pearnly-timing.log` urt |
| 生产当晚办公室小文件 | 后端 1.6–3.7 秒 | 同上 |

读法：实验栈改前就只有 3–5 秒，生产同一份 13.35 秒——多出的约 8–9 秒在实验栈复现不了的那段：存原件到甲骨文（14MB base64 经 8443）+
Cowork `files.create` + 计费余额核对（实验栈本机存原件这段 0.1 秒）。U2 只把引擎那 2–5 秒与它叠起来，**单靠 U2 生产大概到不了 ≤5 秒**。
甲骨文那段没在实验栈接（那是生产存放处，本任务不写生产），真实分段等部署后读 U0 日志。

## 部署后要做（本窗口）

1. 只读生产 `logs/pearnly.assistant.upload.log`（各产品 web 容器），按 `task=` 看「存原件 + 交识别」那段。
2. 超过 3 秒 → 做 U2b'：上传在引擎挂好、本站留存后就回话，存原件 + 交识别挪到后台任务；识别工具改成等这份后台任务（不重交），
   发消息时等它记好上传行再对。动识别工具 / 等待 / 上传卡片时序，开工前先与 CC 一套窗口对好文件归属（同一批文件只一个窗口写）。

## 11-08 晚 erpcowork 并发上传 500（已查清、已修）

- 证据（只读）：nginx 计时日志 20:59、21:03（泰国时间）办公室 CC 桌面版成批同时上传（每批 5–9 份），9 个 500（后端 3.2–6.5 秒）；
  Error Log 21:00 4 条、21:16 8 条，全是 1020「Record has changed since last read in table 'tabPearnly Assistant Session'」，
  调用栈 `api.upload → files._upload → sessions.segment_for_turn → frappe.db.set_value`；那条记录的 remote_ids 已有 3 段。
- 根因：直写上传不按对话排队（代录 11-06 已加锁）；对话到了计价界线，同时进来的几份各自接续、各开一段远端会话、同时写同一条记录，
  MariaDB 快照隔离只放第一份。发消息与上传撞上同样会这样。
- 修法见上表；结论已写进共享笔记「已知问题与结论.md」。
- 同窗口另见（不在上传链路、未查）：20:48 一条 1020 在 `tabCowork ERP Connection`，已报主会话。

## 验证

- 本地全闸门（= development.yml）211 步全过（两次：U0+U2 后、并发修复后）。
- 受影响流程（本机实验栈 pearnly-upload-lab，假智能体）：cc.chat、cc.operator、cc.attach-delete、cc.sidebar-modes、cowork.cc.attach-delete、
  cowork.cc.sidebar-modes、cc.operator.replay（新栈先跑 erp.purchase.create 补单，同已知问题 11-05）——两套数据 × 电脑 / 手机共 20 格全过。
- 生产镜像候选离线检查（`cand-local.sh offline`，镜像同实验栈默认的线上 digest `sha256:fb1037ad…`）：105 组，`LANE EXIT 0`。
- 没测到：生产甲骨文那段的实际耗时（部署后 U0 日志）；真机界面（本分支不改前端）。
