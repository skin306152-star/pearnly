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

## 2. Cloud Tasks 派发（进行中）

## 3. Cloud Workflows 多页（进行中）

## 收尾登记

- 工作树 `/Users/skin/pearnly/.claude/worktrees/upbeat-lamarr-9e73fe`、分支 `codex/cowork-ocr-preview-tasks`：**交接保留**
  （未合入，按授权等压测窗口重测数据与用户确认后再定合入 / 部署）。
