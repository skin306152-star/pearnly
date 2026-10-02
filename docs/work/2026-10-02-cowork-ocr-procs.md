# Cowork 识别进程 4 → 8（2026-10-02，下一批）

分支 `codex/cowork-ocr-procs`，基线 main c5092f48。来源：派发窗口 m7 计时——高峰两分钟（12:01:30–12:03:30Z）识别任务
「可以开跑到真正开跑」等 12–20 秒，但每进程最多 8–9 个线程在忙（上限 24）；卡在 worker-pool 的 4 个派发循环（每接一个任务
初始化 Frappe、连库、交线程、收尾，高峰 0.75–0.81 秒 / 个，空闲 0.07 秒）。

## 改动

- `sizing.py`：Cowork 识别进程数 = `max(2, 核数)`、每进程 12 线程（8 核上 8 × 12 = 96，与原 4 × 24 总线程、识别名额里 Cowork 那一份相同）；
  每线程内存估算 30 → 20 MiB；识别容器上限按 256 MiB 取整（原按 GiB 取整，每个产品白占最多 1 GiB 整机预算）。
- compose：redis-cache `maxmemory` 128mb → 512mb、容器上限 192m → 640m（见下「redis-cache」）。
- `check-production-capacity.py`：公式在 8 核 / 32095 MiB 上等于下面的上线值；四档机器都过容量检查。
- `docs/development/production-capacity.md` 对照表同步。

## 依据（生产 cgroup `memory.peak`，容器 11:41Z 起、含 m7）

| 容器 | 峰值 | 原上限 | 新上限 |
| --- | --- | --- | --- |
| cowork-ocr-worker（4 进程，各 207–295 MiB RSS） | 1115M | 5120M | 5376M（8 进程） |
| erp-ocr-worker（m7 未压 ERP，不作峰值依据） | 356M | 7168M | 6656M（底线 6600M） |
| dms-ocr-worker | 269M | 2048M | 1792M（底线 1740M） |
| redis-cache（顶上限 8969 次，OOM 0） | 192M | 192M | 640M（maxmemory 512mb） |

整机：容器上限之和 37760 → 37696 MiB，加宿主保留 3209 = 40905 ≤ 1.3 倍 41723（余量 754 → 818 MiB）。
数据库连接估算 169，上限 210 → 220（85% = 187）；MariaDB 8g 不变。

## 上线（随下一批）

runtime.env 写死了这些参数，激活前改成（先备份 `runtime.env.before-ocr-procs`）：
`PEARNLY_COWORK_OCR_WORKERS=8`、`PEARNLY_COWORK_OCR_THREADS=12`、`PEARNLY_COWORK_OCR_THREAD_MB=20`、`PEARNLY_COWORK_OCR_MEM=5376m`、
`PEARNLY_ERP_OCR_MEM=6656m`、`PEARNLY_DMS_OCR_MEM=1792m`、`PEARNLY_MARIADB_MAX_CONNECTIONS=220`。
max_connections 在 foundation 重建时生效，所以这一批也要跑 activate-foundation。
上线后下一轮压测读一次 `memory.peak` 复核每线程内存，并看「可以开跑 → 真正开跑」的等待是否下降。

## 验证（本地）

`check-production-capacity.py` 31 项、`test-activate-product.py`、`test-provision-agent.py`、`test-site-step-capacity.py`、`check-architecture.py` 通过。

## redis-cache（m7 后核实，web CPU 一项）

- m7 期间整机 8 核饱和（各容器合计峰值约 9.6 核，load 25）；cowork-web 峰值 3.4 核，按请求量折合每个请求约 260 ms CPU，
  而接口本身只要 7–23 ms（工作台 23、详情 10、对账列表 7–18，一次性容器在 testcowork 以压测用户实测）。
- redis-cache 128mb 满、`allkeys-lru` 累计淘汰 10230 个键，查找未命中 14%（814,884 / 5,876,537）。占用 115MB 中 87MB 是
  `<site>|bootinfo`（Frappe `sessions.get` 每个用户缓存一份启动信息；单份 1.13MB，其中 `__messages` 整份翻译表 1028KB）。
  testcowork2 / testcowork3 各 34 个用户各 44.5MB。几百人在线时 128mb 远装不下，元信息、权限等被挤出后各进程反复重算。
- 不改 Frappe 核心：`maxmemory` 放到 512mb（约 400 个活跃用户的启动信息 + 其余缓存），容器 640m。
- 上线后复核：压测期间 `evicted_keys` 基本不涨、未命中率下降；cowork-web 每请求 CPU（宿主机耗时日志 + docker stats）对比 m7。
