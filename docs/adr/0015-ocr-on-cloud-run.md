# ADR-015 Cowork 发票识别在 Cloud Run 上执行

- 状态：已采纳（2026-10-01）。用户：搬到 Cloud Run「只改在哪里执行，读法不变」；同意谷歌云按量小额费用；
  同意识别结果（不含原图）在新加坡存储桶保留 7 天自动删除；云上不开定时任务、常驻实例或按小时计费的组件。
- 范围：Cowork 发票识别（`cowork.invoice` 的图片 / PDF 原件）、对账销项税核查的发票（`cowork.vat_check`）、对账三类文档（`cowork.recon_document`，企业档管线含 Document AI）。Word / Excel 表格、复核表回导仍在本机。ERP / DMS 见下文「ERP 与 DMS」。
- 代码：`pearnly_cowork.ocr.remote`（`contract` 纯约定、`runner` / `gcs` / `app` 只在 Cloud Run 镜像、`forward` 服务器转发）；
  `pearnly_core.ocr_models.orient`（转正实现移出依赖 Frappe 的 `pearnly_core.ocr`，原入口保留）；
  镜像 `infrastructure/cloud-run/cowork-ocr.Dockerfile`；部署 `scripts/cloud-run/deploy-cowork-ocr.sh`。

## 问题

生产压测（2026-10-01）：128 张同时提交，单张中位数 158 秒、整批 343 秒，单张本身约 13 秒；多出来的全是在我们服务器上排队
（名额、进程数、内存）。每页在我们 8 核服务器上约 2 秒 CPU，几百人同时传几千份时，单台服务器的核数决定了一定要排队。

## 决定

1. **只搬执行位置**。Cloud Run 跑同一份引擎（`run_invoice`、`PageSource`、`ProviderClient`、同一份转正），受理时冻结的配置
   （环境、策略、示例快照、锚定日期）原样带过去。对照测试（`tests/test_cowork_remote_ocr.py`）证明同一原件、同样的谷歌回复，
   两边结果与计费证据一字不差、发出的谷歌请求顺序相同。
2. **服务器保留**：受理、工作单、余额检查、权限、上传限制、每租户 / 全站名额、存结果、扣费、推送与页面实时推送
   （2026-10-03 起结果一入库先通知等候页、再扣费；入库事务内范围权限只查一次）。
3. **Cloud Run 不公开**：只接受识别服务账号 `pearnly-ocr-sa` 签发的身份令牌（`roles/run.invoker`）；它自己也以该服务账号调谷歌，
   不新增模型 / OCR 权限；该账号只新增了检查点桶 `pearnly-ocr-checkpoints-sg` 的对象读写。
4. **配置**：新加坡 `asia-southeast1`；每实例 2 vCPU / 4 GiB、并发 8；多页文件每份最多 8 页同时读（`OCR_PAGE_THREADS`，与本机 `map_pages` 同一约定）；最少 0、最多 20 个实例；单次 1800 秒（`scripts/cloud-run/deploy-cowork-ocr.sh`；采纳时为 600 秒，随下文任务派发改为 1800）；启动加速；按请求计费。
   不用 Scheduler、Cloud NAT、VPC 连接器或常驻实例 —— 空闲零花费。
5. **开关**：站点配置 `pearnly_cowork_ocr_remote = {"enabled": 1, "url": "https://…run.app"}`；不配、关掉或地址不合规就照旧在本机识别。

## 服务器这边等回复时不占进程、不占连接（2026-10-01 补）

识别交给 Cloud Run 后，服务器上的识别任务只是带着原件等回复。识别进程开多线程（`PEARNLY_OCR_THREADS`），每个线程等
回复前先提交并断开数据库连接（`pool.release_db`），回来后第一条查询自动重连：几十份同时在等，连接数不涨。
本机识别（表格、复核表回导、尚未搬上云的对账文档 / 销项税核查）用 `pool.local_slot` 限成每进程同时 1 份
（`PEARNLY_OCR_LOCAL_SLOTS`），等名额前同样先放开连接。等回复的线程省内存，容量按 `PEARNLY_OCR_THREAD_MB`（每线程）核算，
本机那一份由进程底线 450MB 兜住。

## 对账（2026-10-01 补）

- 销项税核查与对账文档的读法抽成纯模块 `ocr.recon_reading`（核查发票：抽取结果不明重发一次、一页不明不作废整份；对账文档：企业档管线、
  一页抽取不明记成读不出），服务器本机与 Cloud Run 共用；`recon_center.invoice_ocr` / `document_ocr` 保留原名入口。
- 请求带 `variant`（`intake` / `vat_check`）与 `yield_seconds`（核查 120 秒、对账文档 90 秒、录入 420 秒，与本机同一让出时限）。
  对账文档单独一个入口 `/v1/cowork/document`，Cloud Run 逐行回 `read` / `extracted` / `unread` 事件，服务器照本机回调更新对账看板。
- 对账是否已结束：发出前、收到后各看一次（云上读的过程中不能打断；结束后读完不扣费）。
- Document AI：区域顺序与处理器取站点配置 `pearnly_document_ai_regions`（不配用代码默认六区，全部 v2.1.1），由同一份 `ProviderClient`
  按区域试、被限流立刻换下一区、都被限流交回服务器稍后续跑。云上不连额度 Redis，各区每分钟固定额度靠谷歌 429 + 换区 + 交回续跑消化，
  不引入常驻组件；需要更平滑时再在转发处按区拿名额。

## ERP 与 DMS（2026-10-01 补）

ERP 采购 / 销售识别与 DMS 身份证识别在服务器上的计算很轻（转正约 15 毫秒、其余是等谷歌回复），经 Cloud Run 中转只多一跳；
用户目标「不排队、只等识别本身」靠服务器等待时不占进程与连接实现：识别线程里共用层 `provider._post` 在发出谷歌请求前、原地等额度前
放开数据库连接（`pool.release_db`，只在识别线程里，网页请求的事务照旧到请求结束才提交）；网页线程数可调
（`PEARNLY_{DMS,ERP,COWORK}_WEB_THREADS`）。ERP 网页上传识别 2026-10-02 起不再是同步请求：改为 `ocr` 队列里的后台任务
（`pearnly_erp_portal.recognition.background`），页面推送优先、轮询兜底（实时连接在线 20 秒、断开 5 秒一次），
见 [记录](../work/2026-10-02-erp-recognise-background.md)。

## 保护在云上的形式（与本机不同的地方）

| 保护 | 本机 | Cloud Run |
|---|---|---|
| 付费防重（检查点） | MariaDB `Pearnly OCR Checkpoint`，按调用一行 | 存储桶对象 `site/work/cp/<sha256(key)>.json`，键算法相同；「started」用 generation 前置条件原子创建，两个实例抢同一调用只有一个能发出；已完成直接取回，残留 started 判结果不明，不自动重付费 |
| 同一份不并跑 | 工作单租约（数据库） | 再加一把存储桶租约（`site/work/lease`，660 秒过期可接手）；服务器那边断开、云上还在跑时，重试拿不到租约就暂缓 |
| 权限复核 | 每次付费调用前复核 | **改为发出前与存结果前各复核一次**（云上没有站点）。中途撤权 / 取消不能打断已在路上的这一份，结果不落库、不扣费，但谷歌那边的调用已付费 |
| 名额与调速 | 每次付费调用拿一次 Redis 名额 | **每份**拿一次（`budget.permit`，与本机同一套全站 / 租户计数、公平轮转）；云上不连 Redis，被谷歌限流就换入口（全球 → 美国 → 欧洲）或把暂缓交回服务器，不原地等 |
| 结果不明 | 同 | 同：传输中断 / 超时判结果不明，服务器把工作单记为 uncertain |
| 计费 | 存结果后扣费 | 不变 |
| 已付费任务的过期判断 | 看数据库检查点是否有已完成调用 | 云上的已付费调用数据库看不到：排队时限按未付费算（1800 秒）。云上单份通常十几秒，极端情况可能让已付费的一份过期，需人工处理 |

Document AI 的区域固定额度（发票识别不用；对账文档阶段要用）：在转发时按区域向额度 Redis 拿名额，把拿到的区域交给 Cloud Run，
被谷歌拒绝就交回、换下一区域 —— 不引入云上常驻组件。花费封顶：Cloud Run 最大实例数（20）+ 服务器名额 + 谷歌预算提醒；
每租户 / 全站每日页数上限在对账阶段一并加。

## 证据（2026-10-01，合成发票，直接调 Cloud Run，真实谷歌）

- 单张：读出单号、双方税号、小计、VAT、合计、日期全对，未标复核；4 次调用（Gemini 3.1-flash-lite ×3、Vision ×1）。
- 40 张同时发：三轮 40 / 40、40 / 40、39 / 40 完成；自动开 5 个实例。云内单张中位数 10.6 秒、P90 13.7 秒。
  每张 CPU 中位数 0.23 秒（服务器上约 2 秒）。慢的都在谷歌：全球入口 2 次 429 立即转美国入口，其中一次美国入口 60 秒无回应
  → 判结果不明（与本机同一规则）。
- 冷启动：见任务记录 `docs/work/2026-10-01-ocr-cloud-run.md`。

## 成本（谷歌官方计费目录，2026-10-01 核实）

- Cloud Run 新加坡属第二档：CPU 0.0000336 美元 / vCPU 秒，内存 0.0000035 美元 / GiB 秒，请求每月前 200 万次免费。
  按实测每份约 11 秒、每实例 8 份并发：每份约 1.4 实例秒 × (2 × 0.0000336 + 4 × 0.0000035) ≈ 0.00011 美元。
- 存储桶 0.02 美元 / GiB 月，结果 7 天删除；镜像仓库前 0.5 GiB 免费，只保留最近 2 版（镜像约 140 MB）。
- 谷歌识别费（Gemini、Vision、Document AI）与本机执行相同，搬家不改变。

## 补充（2026-10-01）：Gemini 第三个备用入口

用户同意把 Gemini 欧洲多区域入口加为第三个备用：全球 → 美国 → 欧洲。全球、美国被谷歌拒绝（`provider_*`）时这一次立刻转下一个，
不原地等；欧洲是最后一个，再被拒才按原规则原地短等。只有被转过去的那一次贵 10%、数据在欧洲处理。Cowork（`policy.vertex_overflows`，
Cloud Run 与本机共用）和共用层（`ocr.overflow_locations`，ERP / DMS）同一规则；备用入口结果不明时各重发一次；续跑时任一备用入口
已读完的直接取回。开关：Cowork 环境变量 `VERTEX_OVERFLOW`、站点配置 `pearnly_ocr_vertex_overflow`，默认 `us,eu`，`us` 只转美国，`off` 关掉。

## 补充（2026-10-01）：任务派发（Cloud Tasks / Cloud Workflows，默认关）

站点配置 `pearnly_cowork_ocr_tasks` 开着时，识别任务不再占线程按行等 Cloud Run：整份请求存进检查点桶、只把小信封交给 Cloud Tasks
就暂缓；Cloud Run 读完把结果写回桶、用谷歌签发的身份令牌（受众 = 站点回调地址）回调；站点只记「结果在哪」并唤醒识别任务
（`pearnly_core.execution.work.wake`），之后的复核 / 存结果 / 扣费与按行等回复同一段代码。云上一口气读到 1500 秒才让出
（Cloud Run 单次时限 1800 秒）。多页对账文档交给 Cloud Workflows：各块读字、各页第一遍抽取并行预热，再原封不动读整份
（第一遍全部从检查点取回，跨页上下文仍由同一份读法负责）。检查点状态 `external` 让共用层把已交给云上的任务按「可能已付费」对待。
关掉开关完全回到按行等回复。代码：`ocr.remote.tasks` / `callback`、`runner.task` / `warm`、`enterprise.first_pass`；
设计、费用、要开通的配置见任务记录 `docs/work/2026-10-01-cowork-ocr-preview-tasks.md`。
**生产现状**：代码默认仍关，但生产 Cowork 租户 2026-10-02 起开着，派发分到 4 个 Cloud Tasks 队列（`"shards": 4`，
`cowork-ocr`、`cowork-ocr-1`…`-3`）；识别进程 2026-10-03 起由 `pearnly_core.execution.ocr_worker.OcrWorker` 直接把队列里的识别任务交给空闲线程，
不再先做一遍完整站点初始化。参数见 [生产容量](../development/production-capacity.md)。
