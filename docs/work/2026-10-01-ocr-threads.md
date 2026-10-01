# 一个识别进程同时跑几份（生产压测，2026-10-01）

用户目标：多人同时上传时只等识别本身，不排队；不加成本或只加少量成本。用户同意按「一个进程同时挂多个谷歌请求 + 进度推送 +
查清每张的 CPU」做（2026-10-01）。分支 `codex/ocr-concurrency`，基线 `origin/main` 1b9b2b94。

## 证据（生产 testcowork，budget 40 / 14 / 500，cowork 识别 18 进程 / 8g，不 fork）

- 128 张合成发票同时提交：单张出结果中位数 158 秒、整批 343 秒；单张自己约 13 秒。多出来的是排队：
  谷歌只回了 16 次 429，被自家名额推迟 2274 次；`tenant_pending` 50 让 78 张提交被拒后自动重交。
- 每张在我们这边的 CPU（生产识别容器内联跑、cProfile 按 CPU 计时，Xeon Silver 4214 2.2GHz）：约 2.4 秒；同样的活测试机 0.44 秒。
  大头：每个 HTTPS 连接重新解析 certifi 证书库（requests 2.33.1 没有预载上下文）每次约 0.12 秒、每张约 0.5 秒；其余是结果整理的
  深拷贝、每次付费调用前的权限复核查询（每张约 200 条 SQL）。网络等待占单张时间的约 95%。
- 以前一份识别占一个进程（每个约 300–450MB），进程数被内存卡住，同时识别的份数跟着卡住。
- 录入页每人最多同时等 6 份、每份每 5 秒读一次状态；上百人时这些读状态就超过网页进程每秒 42–72 次的处理能力。

## 改动

- `pearnly_core/execution/pool.py`：不 fork 的识别进程按 `PEARNLY_OCR_THREADS` 开线程。RQ 取到识别任务交给空闲线程即返回，
  线程都忙时 RQ 原地等（最多 300 秒，超时就地跑这一份）。线程用全新上下文跑 Frappe 原生 `execute_job`（init / connect / 回滚记错 /
  死锁重试 / before_job / after_job / destroy 都照旧），与生产网页 gunicorn `--threads=4 --worker-class=gthread` 同一种用法。
  非守护线程：进程停止（发版、按内存回收）时不再取新任务，在跑的跑完再退出。默认 1 = 不开，行为不变。
- `recycle.py`：按内存回收的阈值每多一个线程加 150MB；判断只在 RQ 那边（线程里没有 RQ 任务）。
- `provider.py`：调谷歌用共用 TLS 上下文（同一份 certifi、urllib3 同一工厂与校验要求），每次仍新建会话与连接、用完关闭。
  生产容器实测：证书库只解析一次；过期 / 主机名不符 / 自签 / 不受信根证书照样全部拒绝；设了 REQUESTS_CA_BUNDLE 时照旧每次新建、
  不混进共用上下文。
- 进度推送：`store.pages_reached` 读到新一页就推 `pearnly_ocr_work`（带 `pages_done`）给提交人；录入页收到直接更新按页进度，
  实时连接在线时轮询放慢到 20 秒兜底，连接不在照旧 5 秒。有结论的推送（`work._finish`）不变。
- 部署开关：compose 三个识别容器加 `PEARNLY_OCR_THREADS: ${PEARNLY_{DMS,ERP,COWORK}_OCR_THREADS:-1}`；`capacity.py` 每个识别进程
  最低内存按线程数加 150MB/线程，线程数只接受 1–32。

- Gemini 被谷歌限流（用户 2026-10-01 同意，含「转美国入口多付 10%、数据在美国处理」）：
  - 生产实测入口（`gregion.py`，每个入口一次极短请求）：`gemini-3.1-flash-lite`、`gemini-3.8-flash` 在 global、`us`、`eu` 多区域入口
    可用（多区域主机 `aiplatform.{us,eu}.rep.googleapis.com`），14 个单区域全部 404。原注释「只能走 global」只对了一半。
  - `budget.feedback(..., shared=True)`：共享额度（DSQ）10 秒内被拒不到 3 次，只让被拒的请求自己退避，不整体停顿 / 放慢；
    到 3 次照原规则。Document AI 等固定额度不变。真实额度 Redis 上用临时测试桶核验：共享 [不停, 不停, 停×2]，固定 [停×2, ×4, ×8]。
  - `provider.post(hand_back=("provider_",), shared_quota=True)`：被谷歌拒绝的这次直接交回；自家额度的等待照旧原地等。
  - Cowork `client._vertex` 与共用层 `ocr._post_with_backoff`（DMS / ERP）：全球入口被谷歌拒绝（429 / 503 / 额度）时这一次改发
    美国多区域入口；美国入口再被拒按原规则退避。续跑时已在美国入口读完的直接取回（Cowork 先确认任务有效再读检查点）。
    关掉：Cowork 环境 `VERTEX_OVERFLOW=off`，DMS / ERP 站点配置 `pearnly_ocr_vertex_overflow=off`。

- 上传写超时：`provider._post` 的 `timeout=(10, …)` 第一项在 urllib3 里同时是发送请求体每一步的时限；pB 中 06:49 / 07:15
  （泰国时间）Document AI 连续 3 次 `The write operation timed out`，B04-SCB3061-2026-05 整套读不出、B09 一个文件 0 行。放到 30 秒
  （`UPLOAD_STEP_SECONDS`），读回复时限不变。

不改：读法、模型、调用顺序、提示词、检查点与付费防重；结果整理里的深拷贝和每次调用前的权限复核（动了有改变结果 / 放宽复核的风险）。

## 验证

- 离线（生产镜像一次性容器，断网）：`pearnly_core.execution` 7 组 27 条全过（新 `test_pool` 7 条：默认不开、交给线程、最多 N 个
  同时跑且 RQ 那边等、等不到就地跑、出错释放、非守护线程、回收阈值）。
- `check-production-capacity.py` 23 项、`check-architecture.py`、`test_architecture_guard.py`、`test_queue_targets.py`、
  `node --check intake-upload.js`、`git diff --check` 通过。
- 加上限流改动后：`pearnly_core` 离线 31 条（含新 `test_vertex_overflow` 3 条、`test_provider_wait` 交回 1 条）、
  `test_cowork_provider_runtime` 19 条（新增转入口 / 不转 / 续跑取回 3 条）、`test_cowork_document_ai_regions` 9 条、
  `test_cowork_recon_scanned_docs` 118 条通过（生产镜像一次性容器、断网）。
- 生产多线程实测（独立进程、新代码、12 张合成发票同时跑）：见下方结果。

## 待办

- 生产多线程实测结果与进程 / 线程 / 名额（budget concurrency / tenant_concurrency / tenant_pending）目标值。
- 合入前在生产镜像跑完整 `ci-candidate-checks offline`（改了共用层）；部署在夜间维护窗口、每一步用户同意。
- 部署后同样 128 张复测，对比中位数 158 秒。
