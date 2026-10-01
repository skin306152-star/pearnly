# Cowork 识别：预览图预存、Cloud Tasks 派发、Cloud Workflows 多页（2026-10-01）

- 分支 `codex/cowork-ocr-preview-tasks`，基线 `origin/main` `2821e5c1`（已含「让对账等识别时不占进程」那批修复）。
- 授权（用户 2026-10-01，经压测窗口转达）：只在任务分支上实现、测试、推分支跑 CI。**不合入 main、不部署生产、不部署 Cloud Run、
  不改谷歌项目配置**；要开通的外部配置写成清单，等用户确认。云上空闲零花费；识别读法（提示词、模型、页序自核、再读）不改。
- 范围调整（同日，压测窗口转用户指示）：原「多页拆成单页先不做」改为这次一起做，用 Cloud Workflows。
- 问题来源：压测窗口生产实测（main `2bbcb4ee`，Cloud Run `cowork-ocr-00011`）：三租户各 100 张直传并发时
  `files.api.page` p50 17–21 秒、p95 36–44 秒；`files.api.original`（只是 302）p50 20–25 秒——同步等 Cloud Run 现画页图的请求把
  gunicorn 线程占满，连 302 都排队。识别派发每 90–100 秒让出一次再靠 Redis ETA / 每分钟唤醒续上，12 页银行账单来回 5 次、10.5 分钟。

## 1. 预览图在识别时就存好（已做）

### 做法

- 识别请求（录入台发票，`workflow._recognize` 交给 Cloud Run 的那条）多带一个预览规格 `previews = {source, sha256, kind}`
  （`ocr.remote.contract.previews`；只在配了原件桶、原件是 PDF / 图片时带，`files.service.preview_spec`）。
- Cloud Run（`ocr.remote.runner.Previews`）识别的同时另起一个线程，逐页用**与服务器现画同一份** `ocr.originals.render_page`
  画预览（PDF 150 dpi；照片长边 2048；超过 400 KB 的改 JPEG 85，规则不变），存进原件桶 `gs://pearnly-cowork-originals-sg`：
  - 对象名 `<站点>/previews/<原件名>/v<画法版本>-<sha256 前 16 位>/p<页码>`（`contract.preview_object`），只新建
    （`ifGenerationMatch=0`），已有就只查版本号不重画（让出后续跑不重复画）。
  - 只读原件字节，不碰识别用的页面对象和付费调用；这一线程出任何错（桶不可用、某页太大）都只是少存几页，识别结果照常交回。
    测试证明加不加预览，识别结果、计费证据、发出的谷歌请求逐字相同。
  - 识别读完后最多再等 20 秒（`PREVIEW_WAIT_SECONDS`，通常早已存完），存好的页随 `done` 结果交回（桶名 + 每页对象 / 版本号 / 类型）。
- 服务器（`files.service.preview_record`）逐项核对（页码在范围内、对象名正是这份原件这一页、桶名等于站点配置的原件桶、类型只允许
  PNG / JPEG），合格的**随绑定识别结果那一次原件保存**写进原件记录新字段 `preview_objects`（`history.recognition.persist_batch`），
  不额外写原件表（不增加 1020 冲突面）。中途断掉没记上也没关系：照旧现画。
- `files.api.page`：先做与原来完全相同的权限判定（`files.service.page_source`：读权限、记录引用这一页、页码在原件范围内），
  有存好的预览就记访问日志（同一个 `page_png`）后 302 到 5 分钟有效、钉住版本号、`inline` 显示的签名地址
  （`files.cloud.preview_url`，签名在本机算），响应同样带 `no-store` / `nosniff`；没有（旧数据、本机识别的、画法版本变了、
  内容哈希对不上）就照旧现画（`files.service.render_page`，原件在桶里的仍由 Cloud Run `/v1/cowork/preview` 画）。
- 页面用 `<img src>` 显示（`pearnly_core/public/desk/viewer.js`），跟随 302 不需要 CORS；前端不改。
- 画法版本 `contract.PREVIEW_VERSION`：`render_page` 改了就加 1，旧图不再用、回到现画，保证存的图与现画的一模一样。
  Cloud Run 镜像的 pymupdf / Pillow 版本与生产 Frappe 镜像逐个锁定一致（`infrastructure/cloud-run/cowork-ocr.Dockerfile`）。

### 交付方式选择与成本（单价 2026-10-01 从谷歌计费目录 API 读取，美元）

| 项 | 单价 |
|---|---|
| 新加坡标准存储 | 0.020 / GiB·月 |
| A 类操作（写） | 每月前 5000 次免费，之后 0.005 / 千次 |
| B 类操作（读、查元数据） | 每月前 5 万次免费，之后 0.0004 / 千次 |
| 存储出网到亚太（Download APAC） | 目录里前 100 GiB 为 0，之后 0.12（1 TiB 起 0.11、10 TiB 起 0.08）/ GiB |

- **选浏览器直接从桶取**。生产服务器在 SSD Nodes 新加坡（ADR-002），不在谷歌云里：「服务器转发」时服务器从桶下载同样要付出网费
  （同一价目），还要占服务器线程和带宽——正是这次要消掉的瓶颈。直接从桶取：出网字节一样多，服务器只做权限判定 + 本机签名
  （不调谷歌接口）。现在的做法（Cloud Run 现画后回传服务器）本来也付同样的出网字节，所以出网费不增加。
- 按每月 10 万页、预览平均约 0.3 MB 估：
  - 写入 10 万次 ≈ 0.48；查版本号 ≈ 0.02；
  - 存储每月新增约 29 GiB ≈ 每月 +0.58，**原件桶没有自动删除，按年累加**：第 12 个月约 7 / 月。
    可选（需用户确认、要改桶配置）：给预览加生命周期（如 90 天删除）并让服务器对超龄记录回到现画，存储就封顶在约 1.7 / 月。
    这次按要求跟随原件桶现有规则（不删），未加。
  - 出网：每页看一次约 29 GiB / 月，目录前 100 GiB 为 0；超出按 0.12 / GiB（与现在相同的字节）。
  - Cloud Run 画图 CPU：每页约 0.1–0.4 秒，与识别等谷歌回复的时间重叠；最坏全额计入约 2.4 / 月，而现在每看一页都要一次
    Cloud Run 现画，看得越多省得越多。
- 没有常驻花费；不需要新的谷歌权限（识别服务账号已有原件桶的对象读写；签名用同一把密钥）。

### 验证（离线，生产镜像 `ca98da7e…`（main 2821e5c1 构建）里跑，工作树 apps 覆盖镜像 apps）

- `tests/test_cowork_remote_ocr.py`（39 项通过，新增 `StoredPreviews` 8 项）：三页 PDF 每页都存且字节与 `render_page` 一致；
  加预览后识别结果 / 计费证据 / 谷歌请求逐字相同；续跑不重画；某页存不了只少这一页；桶整个坏掉识别照常 `done`；
  规格与内容哈希必须一致；回来的预览逐项核对（页码、对象名、站点、版本号、类型）。
- `tests/test_cowork_direct_upload.py`（22 项通过，新增 `StoredPreviews` 8 项）：只在开桶且 PDF / 图片时带规格；记录核对
  （别的桶、别的内容、越界页、空列表都不记）；签名地址钉版本、`inline`、5 分钟；**Cloud Run 存的图与服务器现画逐字节一致**
  （三页不同底色 PDF）；`page` 有预览时 302 + 访问日志 + 私有响应头、不现画；没有时照旧现画；权限拒绝时既不给地址也不记日志。
- `tests/test_cowork_remote_forward.py`（17 项通过）：带规格才让 Cloud Run 存、读完把结果交给回调；规格哈希不符拒绝发出。
- `python3 scripts/check-architecture.py` 通过。`check-i18n.py --strict` 在 main 上原本就不通过（98 行同样的输出），与本次无关。

### 上线前还缺

- Cloud Run 新镜像部署。先后无所谓：旧 Cloud Run 不认识 `previews` 字段会忽略（照旧识别、不回预览，服务器照旧现画）；
  新 Cloud Run 收到不带规格的旧请求也不存图。
- 站点迁移加字段 `Cowork Source File.preview_objects`（原生 DocType 同步，无数据补丁）。
- 测试机或生产真实谷歌存储上的端到端：签名地址能打开、浏览器显示、访问日志。

## 2. Cloud Tasks 派发（已做，站点开关默认关）

### 做法

- 开关：站点配置 `pearnly_cowork_ocr_tasks`（且 `pearnly_cowork_ocr_remote` 开着、在识别任务里）。关着完全走原来的按行等回复。
  ```json
  {"enabled": 1, "project": "pearnly", "location": "asia-southeast1", "queue": "cowork-ocr",
   "bucket": "pearnly-ocr-checkpoints-sg", "workflow": "cowork-document"}
  ```
  可选：`workflow_min_pages`（默认 3）、`callback_host`（默认站点名）、`invoker`（默认识别服务账号）。
- 服务器（`ocr.remote.tasks`，从 `forward._call` 进入；录入发票、销项税核查发票、对账文档三条都经这里）：
  1. 整份请求（与按行发出的同一份）存进检查点桶 `<站点>/<任务>/task/<派发号>/request.json`（桶已有 7 天删除），
     Cloud Task 只带小信封（`contract.task`：站点、任务、派发号、请求对象 + 版本号、回调地址），不带单据内容
     （Cloud Task 上限 1 MB，原件可到 20 MB）。任务名由派发号算出，重复建只有一个（409 当已建好）。
  2. 先建任务、后在识别任务检查点里记派发号（状态 `external`），抛 `Deferred("remote_dispatched", 300)`：**识别线程立刻空出来**；
     300 秒只是兜底（回调丢了时回来看一眼：取一次库、不付费）。建不成（连不上 / 没权限）就暂缓 15 秒、不记派发号。
  3. 再进来（回调唤醒或兜底）：派发号对得上、结果在 → 从桶里按版本号读回 `reply.json`，补上回调时没送到的进度事件，
     交给 `forward._outcome` —— **之后的复核、存结果、扣费、计费参考号与按行等回复时完全相同**（同一段代码）。
     结果是「暂缓」（限流超过原地等的上限、另一个实例在跑）→ 下次重新派发（新派发号）；没回来 → 接着等；
     3 小时还没回来 → 当作丢了重新派发。
- 共用层（`pearnly_core.execution.work`，两处小改）：
  - `wake(name)`：回调到了把排队 / 暂缓中的这份立刻放回队列；正在跑的先记一笔，那次运行以「暂缓」收尾时不按原定延迟等
    （先记一笔、后改状态，两步之间对方走到哪都不漏）。
  - 排队时限：检查点里有 `external`（已交给外部执行，云上可能已付费）与有已完成调用同样对待——否则一份读得久、回调到时被判
    「排队超时」，已付费的结果作废。付费宽限的绝对上限（3 小时）不变。
- Cloud Run（`/v1/cowork/task`，`runner.task`）：读回存好的请求、核对是这份任务的；用同一个 `recognize` 读（让出时限放到
  1500 秒：服务器不在旁边等，一口气读完，不再每 90–100 秒让出一次）；结果 + 全部事件写成 `reply.json`（只新建）；
  用 Cloud Run 服务账号经元数据服务器签发的身份令牌（受众 = 回调地址）回调站点。回调没送到回 503 让 Cloud Tasks 重试——
  重试时结果已在，只补回调，不重读、不付费。读的过程中每 5 秒把新事件作为进度回调（只作展示）。
- 站点回调（`pearnly_cowork.ocr.remote.callback.receive`，访客可调、只收 POST）：
  - 只认谷歌签发（公钥按缓存时间缓存）、受众正是本站回调地址、签发方 accounts.google.com、签发对象是识别服务账号且邮箱已验证的
    令牌；放在 `X-Pearnly-Callback` 头（`Authorization: Bearer` 会被 Frappe 当成它的 OAuth 令牌，查不到直接 401）。
    别的站点的令牌受众不同，过不去。
  - 结果回调只把「结果在哪」按派发号记在识别任务检查点里并唤醒任务，不信回调里的任何结果内容（结果从我们自己的桶按版本号读）。
    重复回调写同一行同样内容（幂等）。已结束的任务、别的站点的、对象名不在本站本任务目录下的一律 200 不处理。
  - 进度回调按派发号记已应用到第几个事件（重复 / 乱序的不应用），交给产品登记的函数（hooks `cowork_remote_progress`：
    录入页按页进度、对账看板），与按行等回复时同一套看板逻辑（`document_ocr._board` 抽出来两边共用）。
- 迁移保护：付费防重（云上检查点 + 桶租约，键算法不变）、工作单租约、计费参考号、存结果前权限复核、外部写者锁（识别不涉及）都不变；
  回调不碰业务数据、不建会话。

### 租户公平

按行等回复时每份拿一次 Redis 名额（全站 / 租户并发、公平轮转）；任务派发时服务器不再持有名额（不等），云上并发由 Cloud Tasks
队列（最多同时 150 个派发）和 Cloud Run 最多 20 实例 × 8 并发约束，受理时每租户待处理上限（`tenant_pending`）照旧。
一家一次压满时其他租户可能排在后面——要严格公平可再按租户分队列（队列免费），这次未做，开启前先按压测数据判断。

## 3. Cloud Workflows 多页（已做，站点开关默认关）

- 用于对账文档（银行账单 / 总账 / 销项税报告）页数 ≥ `workflow_min_pages`（默认 3）时：Cloud Task 改为新开一次 Workflows 执行
  （`infrastructure/cloud-workflows/cowork-document.yaml`），三步：
  1. 各块读字并行（Document AI 每块 2 页，原来一块一块顺序读）；
  2. 各页第一遍抽取并行（每页一次 Cloud Run 请求，渲染分散到不同实例；原来同一请求里渲染要排队，PDF 库不是线程安全的）；
  3. **原封不动读整份**（`/v1/cowork/task`）：第一遍全部从检查点取回、不再付费，自核 / 余额链 / 表头沿用 / 再读照旧按页序；写结果、回调站点。
- 跨页上下文不拆：①② 只是预热，用的是与整份读法同一套步骤（`enterprise.first_pass` 复用 `run` 的 `_local`、`_transcripts`、
  `_first_reading`），发出的请求逐字相同、检查点键相同；本地拼行算得平时 `run` 不调模型，预热也不调。①② 某块 / 某页失败
  （重试用完）就跳过，③照常补上；同一调用两边同时发只有一边写得进「started」，另一边不付费。
- 发票（多为 1–3 页，各页已在一次请求里并行）这次不拆。

### 一致性证据（离线，假谷歌，生产镜像里跑）

`tests/test_cowork_remote_ocr.py::WarmThenWhole`：5 页税报（每页都会触发再读）——预热后整份读的结果与一次读完逐字相同；
整份那一遍真正发出的付费调用数 = 结果里再读的页数（第一遍全部命中检查点，付费 0 次）；预热 + 整份发出的全部请求与一次读完
**逐字节相同、次数相同**。抽取在读字没做完时不预热（`not_ready`，不发任何请求）；页号超出跳过。

真实语料（B07-GIZE-BAY-CA-2026-03、B08-冰厂SINCERE-BBL0555-2026-01 / -04）对比：脚本 `scripts/cowork-compare-paged-reading.py`
（每份只付一遍费：新路径真读；旧路径在新路径的检查点上重放、不连谷歌，出现新路径没发过的请求即判「输入变了」；再逐页逐行比）。
由压测窗口在生产机一次性容器里跑（用户已定），结果待回。生产 Cloud Run 还是线上版本，所以生产上只能验「分页预热 → 整份读」
这段读法（在容器进程里跑，与 Cloud Run 同一份代码）；Cloud Tasks / Workflows / 回调这一整条链只能离线验，开通后在测试环境验。

## 费用测算（每月 10 万页，其中对账多页文件约 3 万页；单价 2026-10-01 谷歌计费目录 API）

| 项 | 单价 | 估算 |
|---|---|---|
| Cloud Tasks | 每月前 100 万次操作免费，之后 0.40 / 百万 | 发票约 7 万页 ≈ 6 万份 + 对账约 3000 份 + 重派；每份建 + 派发 + 偶尔重试约 3 次 ≈ 20 万次 → **0** |
| Workflows 内部步骤（谷歌计费页：调 `*.run.app`、`*.googleapis.com` 计为内部步骤；无空闲费用） | 每月前 5000 步免费，之后 0.01 / 千步 | 平均 10 页 / 份 ≈ 每份 3×(5 块 + 10 页) + 10 ≈ 55 步 × 3000 份 ≈ 16.5 万步 → **约 1.6** |
| （若按外部步骤计） | 前 2000 步免费，之后 0.025 / 千步 | 最坏 **约 4.1** |
| Cloud Run 多出的请求 | 与现有同价（2 vCPU + 4 GiB，每实例秒约 0.000081） | 预热每份多 15 个请求，每个约 0.5 秒开销 → 最坏 **约 2** |
| 检查点桶（请求 / 结果对象） | A 类 0.005 / 千次（前 5000 免费）、标准存储 0.02 / GiB·月、7 天删 | 写约 13 万次 ≈ **0.6**；存储可忽略 |
| 结果读回服务器（出网） | 前 100 GiB 为 0，之后 0.12 / GiB | 每份几十 KB–1 MB，约 5 GiB → **0** |
| Cloud Logging | 每月前 50 GiB 免费 | Workflows 只记错误 → **0** |

合计每月约 **2–7 美元**，全部按量；不设最小实例、没有定时任务、不用 Memorystore / NAT / VPC 连接器，空闲为 0。
谷歌识别费（Gemini、Vision、Document AI）不变：预热与整份读的请求与原来逐字相同，检查点保证不重复付费。

## 要开通的外部配置（**需用户确认后才做**，本窗口没有执行任何一项）

1. 开通接口：`cloudtasks.googleapis.com`、`workflows.googleapis.com`、`workflowexecutions.googleapis.com`（项目 pearnly）。
2. 建队列（新加坡）：
   `gcloud tasks queues create cowork-ocr --project pearnly --location asia-southeast1 --max-dispatches-per-second 20
   --max-concurrent-dispatches 150 --max-attempts 8 --min-backoff 10s --max-backoff 300s --max-doublings 4 --max-retry-duration 7200s`
   （并发 150 ≈ Cloud Run 20 实例 × 8；Cloud Run 忙回 429 时按退避重试。）
3. 识别服务账号 `pearnly-ocr-sa` 授权：
   - 队列上 `roles/cloudtasks.enqueuer`（服务器建任务）；
   - 自身上 `roles/iam.serviceAccountUser`（建任务时让 Cloud Tasks 以它的身份签令牌调 Cloud Run / Workflows）；
   - 项目上 `roles/workflows.invoker`（Cloud Task 新开 Workflows 执行）；
   - 项目上 `roles/logging.logWriter`（Workflows 以它运行时记错误日志；可选）。
   已有：Cloud Run 调用权限（`roles/run.invoker`）、检查点桶 / 原件桶对象读写——不用再加。
4. 部署 Cloud Run 新镜像（`scripts/cloud-run/deploy-cowork-ocr.sh`，单次时限改为 1800 秒，最少实例仍为 0）。
5. 部署 Workflows（`scripts/cloud-run/deploy-cowork-workflow.sh`）。
6. 站点：迁移（新字段 `preview_objects`）；确认回调地址 `https://<租户域名>/api/method/pearnly_cowork.ocr.remote.callback.receive`
   从谷歌能访问到（前面若有 Cloudflare / WAF 规则要放行这条 POST）；先在测试租户写 `pearnly_cowork_ocr_tasks`，验证后再逐租户开。

## 替换下来的手写调度（新路径在生产验证通过后再删）

开启任务派发后，下面这些在 Cowork 识别上不再起作用，但**这次一行不删**，新路径在生产验证通过、关掉开关的回退窗口结束后再删：
`forward._stream`（按行等回复）与对账文档 / 销项税核查的 `YIELD_SECONDS` 让出（90 / 120 秒）在任务派发时只作上限；
共用层的 Redis ETA 续跑（`dispatch.later`）与每分钟修复（`sweep`）对等回调的任务只作兜底；对账的 `waiting` / `slots` 每分钟唤醒照旧
（它们管的是对账任务等识别，与识别如何派发无关）。ERP / DMS 的识别仍走共用层原路。

## 收尾登记

- 工作树 `/Users/skin/pearnly/.claude/worktrees/upbeat-lamarr-9e73fe`、分支 `codex/cowork-ocr-preview-tasks`：**交接保留**
  （未合入，按授权等压测窗口重测数据与用户确认后再定合入 / 部署）。
