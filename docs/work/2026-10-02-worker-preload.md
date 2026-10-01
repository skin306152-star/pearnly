# 后台 worker 父进程预导入 + 镜像预编译字节码（2026-10-02）

压测窗口"半量的半量"（main d937e3b0，17:02–17:16 UTC）的 py-spy 热点第 1 条：后台进程每个任务都重新导入模块。
分支 `codex/worker-preload`，基线 main `8f03049a`。对结果零影响：任务照旧每次 fork、照旧在自己的进程里跑，只是不再重复导入。

## 证据（生产 /tmp/pearnly-prof/q3-*.txt，各采样 90 秒）

| 进程 | 样本里在导入 | 主要是谁 |
| --- | --- | --- |
| cowork-worker（`bench worker`，short/default） | 88% | Frappe 自己的 `database.query` 962、`safe_exec` 945（共 2765 样本的 69%）；`bench worker` 不预导入，原生 worker-pool 会 |
| cowork-long-worker（worker-pool，long） | 28% | 对账模块 `recon_center.document_ocr` 543、`jobs` 490、`cowork_bank_reconciliation` 223、pdfminer、text_layer |
| cowork-ocr-worker（不 fork） | 0.3% | — |

`.pyc` 可写（容器里 frappe 用户、非只读根），但写在容器可写层：每次部署重建容器就丢，erpnext 镜像里只有 3 个 `.pyc`；
从源码现编译占 cowork-worker 样本的 10%（部署后头几分钟最重）。

## 改法

- `pearnly_core/execution/worker.py`：父进程先导入 Frappe 公共清单（压测导入热点）+ `--preload <app>` 指定的产品在自己
  `hooks.py` 声明的 `pearnly_worker_preload`，`gc.freeze()` 后起原生 worker：给了 `--num-workers` 同 `bench worker-pool`，
  没给同 `bench worker`（原生单 worker 不带定时器线程，行为与原命令一致）。导入失败只记一行跳过。
- `pearnly_cowork/hooks.py`：`pearnly_worker_preload`（对账长任务模块与每分钟定时任务模块，ADR-009 产品自己声明）。
- `compose.yml`：会 fork 的 5 个 worker（erp-worker、dms-worker、dms-sync-worker、cowork-worker、cowork-long-worker）改用这个入口，
  Cowork 两条带 `--preload pearnly_cowork`；三个识别 worker（不 fork）不动。测试机 override 的 cowork-long-worker 同步。
- `capacity.py`：新入口与 worker-pool 一样记进程数和连接数（数值不变）；`check-production-capacity.py` 加一条核对。
- `frappe.Dockerfile`：frappe / erpnext 在源码 COPY 之前预编译（这层构建缓存一直复用，约 2 秒、多 40MB），自有 App 安装后预编译。

## 核对

- 预导入的 29 个模块在没有 Site 的父进程里全部导入成功，前后 socket 数 0 → 0（不开数据库 / Redis 连接），约 1.2 秒、父进程 RSS 约 190MB（fork 共享）。
- 父进程里加载的 260 个 pearnly 模块逐个查模块顶层代码：没有读 `frappe.conf / local / db / cache`、取 Site 配置或模块级翻译
  （`files/service.MESSAGES` 是 lambda，调用时才翻译）。
- 真 worker 冒烟（本地 Redis，原生 RQ 任务）：单 worker 与 pool 两种都把任务跑完，每个任务不同 pid（照旧 fork），任务进程里
  `recon_center.jobs`、`frappe.database.query`、`safe_exec` 已在；原生 `bench worker` 对照组都不在。
- `test_worker.py` 4 项（清单来自产品 hooks、导入失败跳过、单 worker / pool 两种启动）；加进 `ci-candidate-checks.sh`。

## 顺带：结果页补算的长事务（压测第 4 条）

压测锁采样 17:08 看到一个事务开了 287–289 秒、无锁等待。补算 `view_store.backfill`（每次 Cowork 激活后台跑一次，第二次激活
16:57 起跑）只在真正重建一份时由 `put` 提交；已是最新的跳过时不写、不提交，整个补算就开着同一个读快照。现在逐套逐语言
`commit()` 结束快照；`test_cowork_recon_view_store` 加断言（第二遍全部跳过时也逐套提交）。下一轮压测采样带上 processlist 再确认。

## 部署与第二轮压测（2026-10-02，泰国时间）

- 与识别派发窗口的回调即释放名额、谷歌长连接、回调写入重试、直传确认 4 份合成一批：main `c14ab8a6`，
  镜像 `sha256:9c5607eb2911e9b3b2e5d9a8c0e3d7edf6427d972863f83626e084e198462146`，00:47–00:58 激活 ERP / Cowork / DMS（billing 不动）。
- 部署前在本树构建的候选镜像上跑 `ci-candidate-checks offline` 36 步全过；镜像里 frappe / erpnext / 自有 App 的 .py 全部带 .pyc。
- 回读：5 个 worker 都在新入口（预导入 17/17、Cowork 29/29），重启 0；六站 short / default / long / dms_sync 的测试任务全部跑完，
  Error Log 与定时任务失败 0；cowork-worker 跑一个空任务约 178 毫秒 CPU（部署前同样的空任务把容器打到 100%）。
- 压测窗口第二轮"半量的半量"（同场景，轮询从 5 秒改 20 秒）对第一轮：
  - 服务器 CPU 3,918 → 3,289 秒，负载峰值 56 → 42；cowork-worker 728 → 103 CPU 秒。
  - 导入占比：cowork-worker 87% → 7%，long-worker 27% → 1%。
  - 发票 232/232，端到端 p50 336 → 213–326 秒；对账系统原因失败 2 → 0，单套 p50 411–443 → 271–391 秒。
  - long-worker 内存峰值 570 MiB；Max_used_connections 未超过 126。
- 干扰：激活后的补算从 17:55 UTC 跑到 18:13 以后，和压测重叠吃了约 490 CPU 秒，还留下 79–166 秒的空闲事务（重建单套时）。
  原因是代码指纹把 `view_store.py` 自己也算了进去，本批在它里面加一行提交就让全部存档作废、补算把所有对账重算一遍。
  修法（`codex/view-backfill-scope`）：代码指纹只算影响页面的代码；部署补算只补没有存档的和数据变了的，只是代码换了的
  打开时先给旧的、后台重建（`rebuild_view(stale_ok=True)`），不在部署时整批重算。
