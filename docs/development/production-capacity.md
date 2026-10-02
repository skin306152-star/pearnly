# 生产容量：配置对照表、公式与扩容自检

本页回答三件事：每个容量参数现在是多少、为什么、和机器核数 / 内存是什么关系；换机器时怎么自动跟上；
将来加机器（第二台 Web、数据库独立、加只读副本）之前还差什么。

- 公式：`infrastructure/production/sizing.py`（部署时自动跑，结果写 `/opt/pearnly/state/sizing.env`）
- 把关：`infrastructure/production/capacity.py`（每次激活前检查，不过不启动）
- 守护：`scripts/check-production-capacity.py`（公式在 8 核 / 32095 MiB 上必须与生产逐项相同；8/32、12/48、16/64、32/128 都要过容量检查，4/16 必须被拒绝）

## 1. 部署时参数从哪来

`activate-product.sh` / `activate-foundation.sh` 开头先跑 `sizing.py --write /opt/pearnly/state/sizing.env`，
所有 `docker compose` 调用（含 `capacity.py` 与开户代理）按 **先 `sizing.env`、后 `runtime.env`** 的顺序读：

- `runtime.env` 里写了的值照旧以它为准（后读覆盖）——人工定过的值不会被公式改掉；
- 没写的跟着机器走——换机器或加内存后重新部署，自动跟上；
- `sizing.py` 出错或机器低于最小支持规格时，`sizing.env` 写空，完全按 `runtime.env`，与没有这一步时相同。

2026-10-02 生产机（8 核 / 32095 MiB）的 `runtime.env` 把下表的参数都写死了，所以接入公式**不改变任何现值**。
以后想让某项跟机器走，从 `runtime.env` 删掉那一行再部署即可；想手工定值，写进 `runtime.env`。

## 2. 配置对照表

记号：`C` = 核数，`M` = 物理内存 MiB，`half = max(2, C // 2)`，`quarter = max(1, C // 4)`。

### 本机进程（随机器变）

| 参数 | 8 核 / 32 GiB（2026-10-03 batch-1003 起生产现值） | 公式 | 依据 |
| --- | --- | --- | --- |
| Cowork Web 进程 `COWORK_WEB_WORKERS` | 6 | `max(2, round(C × 0.75))` | Cowork 页面请求最多、单请求短；每进程 256 MiB |
| Cowork Web 内存 `COWORK_WEB_MEM` | 1536m | `256 × 进程数` | 实测单进程峰值 < 200 MiB |
| ERP Web 进程 × 线程 | 4 × 8 | `half × 8` | ERP 请求重（报表、单据），线程分担 IO 等待 |
| ERP Web 内存 | 1536m | `384 × 进程数` | 线程多，单进程比 Cowork 大 |
| DMS Web 进程 / 内存 | 4 / 1024m | `half`，`256 × 进程数` | |
| Cowork 长任务进程 / 内存 | 4 / 2048m | `half`，`512 × 进程数` | 对账、导出等长任务；等识别时已让出进程 |
| Cowork 识别进程 × 线程 | 8 × 12 | `max(2, C) × 12` | 识别在 Cloud Run，本机线程只带原件等回复。m7 实测高峰每进程只有 8–9 个线程在忙，瓶颈在 worker-pool 的派发循环（每接一个任务 0.75–0.81 秒），所以进程跟核数走、总线程 96 不变 |
| Cowork 识别内存 | 5376m | `ceil_256MiB((450 + 20 × 11) × 进程数)` | 与容量检查的每进程底线同一公式；每线程 20 MiB：m7 实测 4 进程合计峰值 1115 MiB，折合每线程约 10–15 MiB，留 2 倍 |
| ERP 识别进程 × 线程 | 4 × 16 | `half × 16` | 同上，ERP 原件较大，每线程约 80 MiB |
| ERP 识别内存 | 6656m | `ceil_256MiB((450 + 80 × 15) × 进程数)` | 按 256 MiB 取整（原按 GiB 取整白占约 0.5 GiB）；ERP 识别还没有压测实测 |
| DMS 识别进程 × 线程 | 2 × 8 | `quarter × 8` | DMS 本机读字，每线程约 60 MiB |
| DMS 识别内存 | 1792m | `ceil_256MiB((450 + 60 × 7) × 进程数)` | |
| MariaDB 缓冲池 `MARIADB_BUFFER_POOL` | 4G | `max(1, M × 0.13 // 1024)` G | 2026-10-02 热数据约 1 GB，留约 4 倍增长 |
| MariaDB 连接上限 `MARIADB_MAX_CONNECTIONS` | 220 | `max(151, 向上取整到 10(估算连接数 / 0.8))` | 估算连接数由渲染后的进程 × 线程得出；容量检查的拦截线是 85% |
| MariaDB 内存 `MARIADB_MEM` | 8g | `ceil_GiB((缓冲池 + 286 + 连接上限 × 5) / 0.7)` | 估算值不超过上限的 70%，与容量检查相同 |

### 本机固定（不随机器变）

| 项 | 值 | 依据 |
| --- | --- | --- |
| redis-cache | `maxmemory` = 物理内存 / 128（256mb–1gb，32G 上 256mb），`allkeys-lru`；容器 = maxmemory + 128m（`sizing.py`）。启动信息的整份翻译表按语言共享（`pearnly_core/desk/boot_messages.py`），每个在线用户约 0.1MB | 只放可丢的缓存；会被淘汰，不能放状态（见 `docs/work/2026-10-02-redis-state.md`） |
| redis-queue | 160 MB，`noeviction`，AOF 每秒刷盘 | 队列与不能丢的状态（锁、登录 state、对账进度） |
| 调度器 | 每个产品一个（`dms-scheduler` / `erp-scheduler` / `cowork-scheduler`） | 加 Web 机器时**不得**在新机器上再起调度器 |
| 前端 nginx、socketio、Keycloak | compose 内固定上限 | 实测远低于上限 |

### 云上（按谷歌配额与模型限速定，不随本机变）

| 项 | 值 | 依据 |
| --- | --- | --- |
| Cloud Run `cowork-ocr` | 最多 20 实例 × 每实例并发 8，2 CPU / 4Gi，超时 1800 s | 20 × 8 = 160，与识别名额对齐 |
| Cloud Tasks 队列 | `cowork-ocr` 与 `cowork-ocr-1`…`-3` 四个，各并发 40、5 次/秒（站点 `pearnly_cowork_ocr_tasks` 设 `"shards": 4`）；合计并发 160、20 次/秒，最多重试 8 次 | 合计不超过识别名额与 Cloud Run 的 160；分片数为 1 时只用 `cowork-ocr`（并发 150） |
| 识别名额（OCR budget） | 160 | = Cowork 8 × 12 + ERP 4 × 16；本机线程多于名额时原地等名额，不报错 |

本机变大后识别线程会超过 160：多出的线程只是原地等名额，不会多花钱。要真正提高识别吞吐，需同时调高
识别名额、Cloud Run 实例上限与 Cloud Tasks 并发，并先确认谷歌模型配额——这是**成本相关改动，先告知用户**。

### 整机把关（`capacity.py`）

- 所有容器内存上限之和 ≤ 物理内存 × 1.3；
- 每个识别进程内存 ≥ `450 + 每线程 MB × (线程数 − 1)`；
- 估算连接数 ≤ `max_connections × 0.85`；
- MariaDB 估算（缓冲池 + 286 + 连接 × 5）≤ 上限 × 70%。

### 最小支持机器与几档参考

最小支持 **8 核 / 30 GiB**：三个产品全开、识别线程照满时，更小的机器会超出整机 1.3 倍预算。更小的机器要么换，
要么在 `runtime.env` 里手工写死较小的进程 / 线程数。

| 机器 | Cowork Web | ERP / DMS Web | 识别进程 C/E/D | 缓冲池 | 连接上限 | MariaDB 内存 |
| --- | --- | --- | --- | --- | --- | --- |
| 8 核 / 32 GiB | 6 | 4 / 4 | 8 / 4 / 2 | 4G | 220 | 8g |
| 12 核 / 48 GiB | 9 | 6 / 6 | 12 / 6 / 3 | 6G | 290 | 11g |
| 16 核 / 64 GiB | 12 | 8 / 8 | 16 / 8 / 4 | 8G | 370 | 15g |
| 32 核 / 128 GiB | 24 | 16 / 16 | 32 / 16 / 8 | 16G | 680 | 28g |

## 3. 扩容后必须压测

每次扩容（换机器、加内存、加 Web 机器、数据库独立或加副本）部署后，在测试租户跑 **100 人** 和 **300 人**
两档压测，速度不得比扩容前退步；结果按发布记录的前后对照表写进 `docs/work/`。没跑完不算扩容完成。

## 4. 可拆分自检

验收口径：加第二台 Web、把数据库挪到独立机器、加只读副本，都**只需改配置和部署**，不改业务代码。

### 已就绪（只改配置）

- 数据库与 Redis 地址：`runtime.env` 的 `PEARNLY_DB_HOST` / `PEARNLY_DB_PORT` / `PEARNLY_REDIS_CACHE_URL` /
  `PEARNLY_REDIS_QUEUE_URL`，默认 `mariadb` / `3306` / 本机两个 Redis；compose、Keycloak、开户脚本（`site-step.py`、
  `bootstrap-billing.py`）都读这几项。
- 容量参数：按机器自动算（本页第 1、2 节）。
- 不能丢的状态已从会淘汰的 redis-cache 挪到 redis-queue（`pearnly_core.execution.state`，可用 `pearnly_state_redis` 单独指向）。
- 调度器每产品一个，写在 compose 里；加 Web 机器时新机器只起 Web / worker，不起 scheduler。
- 异地备份：每日备份到 `gs://pearnly-backups-sg`（`docs/work/2026-10-02-offsite-backup.md`）。

### 扩容前必须改

| 项 | 现状 | 要做的 |
| --- | --- | --- |
| sites 卷共享 | Docker 本地卷 | 多台 Web 前改成共享文件系统（NFS / Filestore），各机挂同一份 |
| 多机部署工具 | 激活脚本只管本机 | 支持“数据库 / Redis 在别的机器”与“多台 Web”：按机器角色选 profile，数据库机不起 Web，Web 机不起 mariadb |
| Redis 认证 | 只在内部网络、无密码 | 跨机器前加 `requirepass` 与 TLS 或专线，地址写进 `runtime.env` |
| 数据库跨机访问 | 只在内部网络 | 独立后加防火墙白名单与独立账号 |

### 可以等

| 项 | 说明 |
| --- | --- |
| 私有文件放对象存储 | 先用共享文件系统，量大再迁 |
| 只读接口标 `@frappe.read_only` | 加只读副本前补齐，否则副本分不到读流量 |
| redis-cache 内存 | 已按主机内存自动算（第 2 节；生产 256mb，m8 压测淘汰 0），命中率下降再调公式 |
| `ocr-work` 临时目录 | 多机后每台各自清理 |
| 计费地址写死 | 计费服务独立部署时改成配置 |
| 新租户原件桶自动设置 | 现在开户后手工设置，租户多了再自动化 |
