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

## 谷歌前置配置：已完成（2026-10-01，用户在本窗口确认「可以」后执行；gcloud，项目 pearnly）

清单第 1–3 项已做并回读；第 4–5 项（Cloud Run 新镜像、Workflows）属于统一部署，修复窗口完成服务器部署并通知后由本窗口执行。
开关没开之前服务器不会建任务，线上行为不变；以下全部空闲 0 花费。

| 项 | 回读 |
|---|---|
| 接口 | `cloudtasks.googleapis.com`、`workflows.googleapis.com`、`workflowexecutions.googleapis.com` 均为已启用 |
| 队列 | `projects/pearnly/locations/asia-southeast1/queues/cowork-ocr`，`RUNNING`；每秒最多 20、同时最多 150（突发 10）；重试最多 8 次、退避 10–300 秒、翻倍 4 次、最长 7200 秒 |
| 队列 IAM | `roles/cloudtasks.enqueuer` → `pearnly-ocr-sa` |
| 服务账号 IAM | `pearnly-ocr-sa` 自身上 `roles/iam.serviceAccountUser` → `pearnly-ocr-sa` |
| 项目 IAM（`pearnly-ocr-sa`） | 新增 `roles/workflows.invoker`、`roles/logging.logWriter`（原有 `aiplatform.user`、`documentai.apiUser`、`visionai.serviceAgent` 不变），均无条件 |
| 资源管理接口 | 第一次以 pearnly 名义加项目级权限时因 pearnly 未开 `cloudresourcemanager.googleapis.com` 被拒，当时改用本机默认项目名义调用（权限照样加在 pearnly、不收费）；之后用户同意，已在 pearnly 开通该接口（免费、不影响线上），以 pearnly 名义回读项目权限一致 |
| 已有，未改 | Cloud Run `cowork-ocr` 服务上 `roles/run.invoker` → `pearnly-ocr-sa`；Cloud Tasks 服务代理 `service-112074003592@gcp-sa-cloudtasks` 已有 `roles/cloudtasks.serviceAgent` |

## 顺带的谷歌账户整理（2026-10-01，用户逐项同意）

- 本机 gcloud 默认项目由旧「pearnly erp」（`project-d0fbd530-1ee3-436d-b58`，早已申请删除、无付款账户）改为 `pearnly`（只改本机命令行配置）。
- 删除三个无付款账户、无存储桶 / 服务账号的空项目：`project-3933c6ec-c1cd-498c-b86`、`project-40c21e2c-4975-4027-944`（My First Project）、
  `gen-lang-client-0397119443`（Default Gemini Project，AI Studio 自动建；用户确认没在用其中的密钥）。回读状态均为 `DELETE_REQUESTED`，
  30 天内可恢复（`gcloud projects undelete <项目>`）。它们曾在 `2026-09-24-legacy-cloud-retirement.md` 中因用途不明保留，本次按用户决定清理。
- 现在账户下只剩 `pearnly` 一个在用项目。

## 上线手册（统一部署时由修复窗口执行，每一步先找用户确认；用户要求「不上半成品」：测试租户端到端全过才全开）

前提：「要开通的外部配置」1–5 已做完（接口、队列、授权、Cloud Run 新镜像、Workflows），站点已迁移（`preview_objects` 字段）。
以下 `bench` 命令在对应产品的 backend 容器里、`sites` 目录下执行；`<站点>` 换成实际站点名。

### 打开

- 预览预存：**不需要开关**，新 Cloud Run + 站点迁移后，配了原件桶（`pearnly_cowork_originals_bucket`）且开了 Cloud Run 识别的站点
  自动生效（默认开）。
- 任务派发 + Workflows（先只开测试租户）：
  ```bash
  bench --site <站点> set-config -p pearnly_cowork_ocr_tasks '{"enabled": 1, "project": "pearnly", "location": "asia-southeast1", "queue": "cowork-ocr", "bucket": "pearnly-ocr-checkpoints-sg", "workflow": "cowork-document"}'
  ```
  回读：`bench --site <站点> show-config` 里能看到这一项；`bench --site <站点> console` 里
  `from pearnly_cowork.ocr.remote import tasks; tasks.settings()` 不是 None（配置不合规时是 None，即仍走原路）。
  **网页进程按 60 秒缓存站点配置**：改开关后最多 60 秒才在所有进程生效（2026-10-01 测试租户实测）。
  开之前先自检：`bench --site <站点> execute pearnly_cowork.ocr.remote.tasks.self_check`，`ok` 为 true 才开（真写一个探测对象
  再删掉、建一个只访问 Cloud Run 健康检查的任务；不 ok 就别开）。

### 端到端验证（测试租户；全部通过才全开）

1. **预览签名地址**：录入台直传一份多页 PDF、等识别完，打开原件预览。
   - 浏览器开发者工具：`/api/method/pearnly_cowork.files.api.page?...` 回 **302**，`Location` 是
     `https://storage.googleapis.com/pearnly-cowork-originals-sg/<站点>/previews/CWS-…/v1-…/p<页码>?…generation=…&X-Goog-Signature=…`，
     跟随后 200、`image/png` 或 `image/jpeg`，页面上显示正常；翻每一页都一样。
   - `Access Log`（Desk 列表）有这次的记录：`Cowork OCR History`、`page_png`。
   - `bench console`：`frappe.db.get_value("Cowork Source File", "<原件名>", "preview_objects")` 有每页一项。
   - Cloud Run 日志有 `previews_stored`，`pages` = `of` = 页数。
   - 权限：用一个看不到这条记录的账号打开同一个页图地址 → 403，且没有新增访问日志、没有给出签名地址。
   - 通过标准：上面全对；页图接口在压测下 p50 应接近普通查询（不再等 Cloud Run 现画）。
2. **任务派发 → Cloud Run → 回调验签 → 入库**：开关打开后录入台再传一张发票。
   - `Pearnly OCR Checkpoint`（按这份识别任务的 `work` 过滤）：先有 `cowork.remote:dispatch`（状态 `external`），
     几秒到几十秒后多一行 `cowork.remote:reply:<派发号>`（状态 `reply`）。
   - `Pearnly OCR Work`：`last_code` 经过 `remote_dispatched`，最后 `succeeded`；识别记录建好、扣费一次（计费记录按参考号只有一条）。
   - Cloud Run 日志：`task_done status=done`、`task_callback status=200`；`gcloud tasks list --queue cowork-ocr --location asia-southeast1`
     里这条任务已不在（成功即删除）。
   - 验签反例：`curl -X POST https://<站点>/api/method/pearnly_cowork.ocr.remote.callback.receive -d '{}'` → 401；
     带一个随便的 `X-Pearnly-Callback` 头 → 401。
   - 通过标准：上面全对，识别结果与按行等回复时相同（同一张票关开关各传一次对比字段）。
3. **12 页以上银行账单走 Workflows**：测试租户建一套对账，银行账单用 12 页以上的那份。
   - `gcloud workflows executions list cowork-document --location asia-southeast1`：多一次执行，状态 `SUCCEEDED`。
   - Cloud Run 日志：`warm stage=read` 1…⌈页数/2⌉ 和 `warm stage=extract` 1…页数（`done`），之后 `task_done`；
     整份那一遍的 `provider_call` 次数 = 结果里再读的页数；全程没有 `progress_yield`。
   - 对账结果与关开关时一致（同一套文件各跑一次，或以压测窗口的真实语料对比为准）。
4. **重试不重复付费**：测试租户上让一次回调失败再重试——在 Cloud Run 读的过程中把测试站点设为维护
   （`bench --site <站点> set-maintenance-mode on`，站点对回调回 503），等 Cloud Run 日志出现 `task_callback status=503`，
   再关维护（`... set-maintenance-mode off`）。
   - Cloud Tasks 按退避重试：Cloud Run 日志再出现 `task_callback status=200`，**这次之间没有任何新的 `provider_call`**；
   - 识别完成、扣费一次（计费参考号只有一条）。
   - 通过标准：重试那次没有付费调用，结果入库、只扣一次。

### 全开与回读

四项都通过后，对每个 Cowork 站点执行上面的 `set-config`，逐个回读 `tasks.settings()` 不是 None；之后压测按「半量的半量 → 全量」。

### 回退（任一项不通过，或上线后出问题）

顺序：**先暂停队列、等在途的派发和 Workflows 执行收尾（一般几分钟），再关开关**；要退 Cloud Run 镜像时，必须先关开关、等在途清空
再切修订（旧镜像不认 `external` 状态、没有回调接口）。直接关开关也能收尾、客户不会被重复扣费，但还在进行的预热和改走按行等回复的
那次可能同时读同一页，谷歌那边会多付一次（2026-10-01 复查）。

```bash
gcloud tasks queues pause cowork-ocr --project pearnly --location asia-southeast1
gcloud workflows executions list cowork-document --project pearnly --location asia-southeast1 --filter="state=ACTIVE"   # 等到为空
# 任务派发 / Workflows：回到按行等回复（进行中的照样能收尾：回调到了被拒，识别任务 5 分钟内按原路从云上检查点接着读，不重复付费）
bench --site <站点> set-config -p pearnly_cowork_ocr_tasks '{"enabled": 0}'
# 预览预存：不再存、不再用存好的图，回到现画
bench --site <站点> set-config pearnly_cowork_stored_previews 0
# 需要连 Cloud Run 一起退：切回上一个修订
gcloud run services update-traffic cowork-ocr --project pearnly --region asia-southeast1 --to-revisions <上一个修订>=100
# 重新打开队列（恢复前）
gcloud tasks queues resume cowork-ocr --project pearnly --location asia-southeast1
```

### 回调能否从谷歌访问到（按仓库配置核对）

租户域名直接解析到生产机（压测窗口查过，不经 Cloudflare）；主机 nginx 租户配置（`provision-agent.py::nginx`）对 `location /` 全部
原样转给前端 nginx，没有按路径 / 来源拦截；前端 nginx（`frappe.conf.template`）同样把 `/api/method/…` 交给网页进程；防火墙放行 443。
回调体很小（几百字节），远低于 30 MB 上限。生产实际情况以上面第 2 步真实回调一次为准。

## 识别调用的重试规则（批次 A，2026-10-01；修复窗口照此推到其余对外调用）

| 情况 | 规则 | 付费与检查点 |
|---|---|---|
| 谷歌 429 / 503 / 额度用完 | 暂缓（`provider_quota`）：Gemini 立刻换备用入口（全球 → 美国 → 欧洲），读字换区域，都不行原地短等（每步 ≤ 20 秒、累计 ≤ 60 秒）后交回识别任务稍后续跑；同时反馈限流冷却（10 秒内 3 次才整体放慢） | 被拒的请求不计费；检查点记「已拒（暂缓）」，续跑可以重发 |
| 谷歌 500 / 502 / 504、Vision 回复里带 8 / 14 | **一律暂缓**，录入发票也一样（原来 5xx 当终局拒绝）：Vision 全球入口原地隔 2 秒再试 2 次，仍不行依次换美国 / 欧洲区域入口（价格相同，与 Gemini 同一个备用入口开关）；Gemini 换入口，读字换区域；都不行交回，任务派发连续暂缓按 15→30→60…最长 300 秒退避 | 失败的请求不计费；同上 |
| 结果不明（读超时、连接断） | **重发一次**（录入发票的模型调用、Vision 原来不重发、整份判结果不明）：同样内容、尝试号 + 1（新检查点）；再不明才判结果不明 | 被放弃的那次谷歌可能已收费（只在长尾时）；续跑时直接取重发那次，不再付费 |
| 单次等待上限 | Vision 第一次 15 秒（正常不到 1 秒），没回按「结果不明」重发、重发用原上限（60 秒）；Gemini 不收紧（今天 70–180 秒成功的不少，收紧只会多付不会更快），对账抽取 180 秒照旧 | 同上 |
| 谷歌明确拒绝（4xx、回的不是 JSON） | 终局：按拒绝原因记失败码（不再是 `workflow_error`），记一份拒绝证据，续跑不重付 | 不重发 |
| 预热（Workflows） | 不重发结果不明的调用、碰上正在进行的同一调用就跳过，留给整份那一遍 | 预热不会多付 |

**第三轮修正（2026-10-02）**：初版「一份结果带 429 就乘 0.8、每份加 1/上限」在 429 只占 2–10% 时也几乎每 5 秒缩一次，发票池长期压在
下限 8，吞吐降了近一半；加上「名额满 → 暂缓 → 3 秒后重跑」每分钟空转 277–590 次，ocr-worker 多花约 600 CPU 秒。已先关掉自适应止损，
改为：按 30 秒窗口内的 429 比例（超过 15% 才乘 0.8，一个窗口最多缩一次），放开用慢启动（干净窗口翻倍，碰过收缩线后每窗口 +2），
发票池下限 24、对账池 8，上限存 Redis 30 天接着用；名额满时在识别线程里原地等（每秒看一次、最多 60 秒，每进程最多 4 个线程等），
不再写库重排；派发调谷歌改为全进程共用连接池会话。

在途派发上限按谷歌 429 自适应（加性增、乘性减，2026-10-02）：两轮半量压测按分钟看，Gemini 每分钟约 150 次、同时约 16 个
调用时 429 约 3–4%，250–385 次 / 40–47 个时 20–46%，902 次（多为 429 后的重试）时 62%——共享额度大约稳定承受每分钟
150–250 次、同时 30–40 个。每份结果带回这次挨了几次 429 / 503（`throttled`），服务器取回时调全站上限：挨了乘 0.8（每 5 秒
最多缩一次），干净的每份加 1/上限；从 32 起步，介于 8 与现有名额配置之间；`pearnly_cowork_ocr_tasks.adaptive` 写 0 关掉。
控制放在服务器的派发名额上（Cloud Run 连不到 Redis，不加常驻组件）。按额度池分开调：发票（flash-lite + Vision）与对账文档
（Document AI + 3.8-flash）是谷歌不同的额度，各一个上限、各按自己结果里的 429 收缩，对账被限流不连带压住发票；全站名额与
租户上限照旧跨池。

对账等识别的时限认得云上进展：结果 / 进度 / 失败回调，以及 Workflows 每做完一步预热由 Cloud Run 发的 ``alive`` 回调，都在库里写一行
``progress`` 检查点（只更新时间、不算付费调用），共用层「最近一次进展」认它；开读 25 分钟后最近 5 分钟内还有进展就接着等（最多 50 分钟），
Workflows 还在重试、云上没有任何进展超过 5 分钟才判超时。

派发路径（Cloud Tasks / Workflows / 回调）：回调只有 200 / 404 算送达，其余重试；结果已写好的重试只补回调；同一份另一个实例在读时
不写结果、回 503 稍后再投；桶 / 令牌一时出错回 503；Workflows 整份那一遍按「谷歌默认判据 + 500」重试约 40 分钟，用完由 Cloud Run
转告站点（`failed`），站点立刻重新派发；站点兜底每 5 分钟去桶里直接看结果（回调丢了最多晚 5 分钟）；建任务连续失败 3 次改走按行等回复
并记错误日志；在途派发按与按行等回复同一份名额配置计数（全站 / 每租户），同时预热的文件最多 6 份（满了不预热、照常派发）。

## 验证与合入状态（2026-10-01）

- 分支已按用户「统一合入、统一部署」的安排变基到修复窗口的 `codex/billing-throughput`（8a90f849，基于 main 2821e5c1），无冲突；
  合入与部署由修复窗口负责，每一步由它找用户确认。本窗口不合入、不部署、没动任何谷歌配置。
- 变基后在生产镜像 `ca98da7e…`（main 2821e5c1）里、用工作树的 pearnly_cowork / pearnly_core / pearnly_control 覆盖镜像，
  跑完整候选离线道 `scripts/ci-candidate-checks.sh offline`：35 步全过（含本任务新增 `test_cowork_ocr_tasks.py`、`test_wake`）。
  `check-architecture.py` 通过。分支 CI（Development checks）首次推送已通过，之后的推送见分支状态。
- 还缺：真实语料对比（压测窗口在生产一次性容器跑 `scripts/cowork-compare-paged-reading.py`，B07 / B08-01 / B08-04）；
  统一部署后测试租户上的端到端（预览签名地址、任务派发、回调、Workflows）；半量 → 全量压测。

## 上线记录（2026-10-01 夜，用户授权三窗口自行协调按批次部署）

| 时间（UTC） | 动作 | 结果 |
|---|---|---|
| 14:15–14:36 | 首批：main e7cca8a2 服务器激活（修复窗口）；Cloud Run cowork-ocr-00013-jlm、Workflows 000001 | testcowork 端到端：派发 / 回调 / 预览 / 12 页对账 Workflows 通过；Vision 502 让单页发票判失败 → 批次 A 修 |
| 16:15–16:30 | 批次 A：main 5f97938c 服务器激活（修复窗口）；Cloud Run cowork-ocr-00014-5bs、Workflows 000002-542 | testcowork：单页 PDF 重跑读完（碰上 Vision 全球入口短时回 14，未判失败）；B07 对账 229 秒（上午顺序读 646 秒）；B08-04 回调连续 503 共 5 分 12 秒后送达、重试期间 0 付费调用 |
| 16:50–16:53 | 回退演练 | 暂停队列 → 在途清空 → testcowork 关开关 → 照片走按行等回复 29 秒读完 → 开回、恢复队列 |
| 16:55–17:05 | 小修 main d937e3b0（Vision 8/14 原地重试 + 美 / 欧区域入口、连续暂缓退避）只激活 Cowork；Cloud Run cowork-ocr-00015-7jp | — |
| 17:10 | 全站打开：erpcowork、testcowork、testcowork2、testcowork3 | 逐站回读 settings 生效、self_check 全部 ok；交压测窗口跑「半量的半量」 |

| 18:00–19:05 | 提效批（c14ab8a6）、按 429 自适应（54667ec6 → 按池 ccc21ca0）、第三轮后关掉自适应止损、原地等名额 + 窗口比例自适应（0fd1621f，Cloud Run 00018） | 第二轮 0 失败；第三轮发现自适应把发票池压在下限、名额暂缓空转，已改 |

**待命（用户 2026-10-02 指示：本次部署完成并确认健康后三窗口原地待命）**：分支 `codex/cowork-lite-timeouts`（a4b93312，基于 main
0fd1621f，CI 通过，未合入、未部署）——flash-lite 单次第一次最多等 40 秒、超时按「结果不明重发」用原上限再发；备用入口重发后仍
不回时退回全球入口再试一次（新尝试号，只多这一次付费调用）。压测窗口已同意做，等用户解除待命后再合入、发 Cloud Run。

遗留：历史单据的预览后台补画（下一批）；小助手 lease 在站点维护期间不退避（小助手侧）；回调写检查点撞 1020 的重试（分支
`codex/cowork-callback-writes`，随下一小批）。

| 2026-10-01 21:24–21:35 | 统一批（用户：修完一起部署）：main 10a4f07d（待复核提示 7d197c66 → flash-lite 40 秒与全球入口再试 d571a39c → 对账无余额行崩溃修复 10a4f07d），修复窗口只激活 Cowork；Cloud Run cowork-ocr-00019-l2j（21:34:37Z 接全部流量） | 四站开关与自适应开、自检全过、队列 RUNNING、新版本无错误日志；开关未动。之后修复窗口跑准确率评测，压测窗口再跑 100 人 |

## 发票识别以外的时间拆分（压测窗口 2026-10-02 要求；m1 = 2026-10-01 20:55–21:06 UTC 的 100 人压测，383 份）

按派发号逐份拼服务器时间戳：原件建立 → 识别单建立 → `request.json` 建立（拿到名额后）→ 派发检查点 `at` → `reply.json` 建立
（Cloud Run 读完）→ 回复检查点建立（回调落库）→ 历史记录建立（结果入库）→ 识别单最后修改（扣费收尾，推送紧随）。
脚本在本窗口草稿区（只读：web 容器里只读会话取库，本机取谷歌存储对象时间），下一轮复用。

| 段（秒） | p50 | p95 | 平均 |
|---|---|---|---|
| 上传受理 → 提交识别 | 0.5 | 2.4 | 0.8 |
| 提交 → 拿到名额写好请求 | 6.8 | 25.8 | 9.4 |
| 写请求 → 建好任务 | 0.3 | 1.8 | 0.5 |
| 建任务 → Cloud Run 读完 | 19.9 | 37.3 | 21.9 |
| Cloud Run 读完 → 回调落库 | 0.2 | 2.3 | 0.5 |
| 回调落库 → 结果入库 | 10.8 | 36.1 | 12.6 |
| 结果入库 → 扣费收尾 | 2.9 | 18.3 | 4.7 |
| 合计 上传受理 → 扣费收尾 | 51.0 | 102.0 | 52.8 |

- 云上不是问题：Cloud Run 任务请求 p50 20.2 / p95 42.3，invoice_done p50 19.3 / p95 35.0，投递 + 冷启动 + 回调约 1 秒，只起了 2 个新实例。
- 服务器侧三段在低负载时都约 1 秒（第 0–1、7 分钟），高峰第 3–6 分钟（每分钟 59–77 份）三段同时变慢（名额前 p50 15–21、回调到入库
  10–25、扣费 4–5），同时云上在途只有 23–49 份：是服务器整体忙，不是名额。另 19 份（5%）派发两次，多 p50 42 秒。
- 现有数据分不开「进程排队 / 处理 / 等解释器锁」：分支 `codex/cowork-run-timing` 给每次识别运行加一行计时（`ready` 可开跑到开跑、
  `wall`、`cpu` 本线程 CPU 秒、各步 `check / mark / balance / remote / capacity / request / task / reply_read / validate / freeze /
  persist / settle`），容器标准输出，随下一批上。下一轮 100 人：本窗口开 Cloud Tasks 投递日志（测完关）+ 服务器每 2 秒采样识别进程 CPU，
  压测窗口做 py-spy。

## 2026-10-02 追加（压测窗口转来的两项）

- 27 页扫描件（testcowork 577624e5…）单页 9–36 秒：不是 flash-lite（它拿到回复 2–13 秒），是读字。PDF 页按 200 DPI 渲染成无损 PNG
  约 3.3MB、不走读字缩小，全球入口读字 6–8 秒；全球入口回 8/14 后转美国入口，上传要 25–31 秒，第一次 15 秒截断再付费重发。
  修：读字首次与重发都按图大小多给时间（每 MB 10 秒）；发票识别补记成本（原来恒为 0，价目挪到 `ocr/engine/cost.py` 共用）。
  PDF 页改 JPEG 90（约 0.65MB）属读法改动：开关 `OCR_PDF_PAGE_JPEG_QUALITY`（Cloud Run 环境变量，默认关），金标对比不降质才打开。
- 重复上传照样扣费：现有设计（重复判定只是提醒；扣费按识别单参考号防重）。用户 2026-10-02 定：保持现状。
- testcowork 等站 99 份识别单停在 recognizing 等：Work 都已终态、页面按 Work 显示、未扣费，均为旧版本或中断留下，不需要改。

## 收尾登记

- 分支 `codex/cowork-ocr-preview-tasks`、`codex/cowork-provider-retries`：内容已经由发布分支 / 快进合入 main（e7cca8a2、5f97938c、
  d937e3b0），可清理（远端与本地分支删除待本任务收尾时核对后执行）。
- 2026-10-02 已删：本地与远端 `codex/cowork-lite-timeouts`、`codex/cowork-adaptive-dispatch`、`codex/cowork-adaptive-pools`、
  `codex/cowork-capacity-wait`（都是 main 的祖先）；本地 `codex/cowork-callback-writes`、`codex/cowork-ocr-preview-tasks`（无远端；
  `git cherry` 核对每个提交在 main 上都有等价补丁，变基后 SHA 不同才用了 -D）。
- Cloud Run 评测标签 `eval`（0% 流量；修复窗口分支 `codex/invoice-read-rules` 的提示词，依次 00020 fd46ca38、00021 df81e3e4、
  00023 61e28855、00024 6172790）和服务级自定义受众（标签地址；派发令牌以站点配置的地址为受众，标签地址不加自定义受众回 401，
  2026-10-01 23:00Z 实测）：**交接保留**，修复窗口跑完 eval3、testcowork 地址改回主地址后通知本窗口，删标签并
  `--remove-custom-audiences`。加自定义受众后生产主地址照常 200（23:11Z 回读）。之后发 Cloud Run 时流量按修订版名固定着，
  发布后要 `gcloud run services update-traffic cowork-ocr --to-latest` 并回读 100%。
- 工作树 `/Users/skin/pearnly/.claude/worktrees/upbeat-lamarr-9e73fe`、分支 `codex/cowork-run-timing`：**交接保留**（计时日志未合入，
  等下一批；下一轮 100 人拆分完成后复核）。
