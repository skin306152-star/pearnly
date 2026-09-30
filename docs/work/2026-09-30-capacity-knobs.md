# 生产容量可调（2026-09-30）

- 分支：`codex/prod-capacity-knobs`，基线 `16373bff`。
- 目的：用户要求压测服务器并发极限后"定一个数值、留安全空间"。先把能调的量从写死改成 runtime.env 可调，
  **默认值全部等于原值，上线不改行为**；数值由压测窗口测完再定。
- 分工（与「旧项目迁移后的用户体验问题」窗口 2026-09-30 约定）：它改共用层 `pearnly_core/execution`、
  各 OCR 客户端和 Document AI 三区域切换；本任务只动 compose、容量检查和开户配置下发，不碰那些文件。两批一起上线。

## 改了什么

| 位置 | 变化 |
|---|---|
| `infrastructure/production/compose.yml` | 识别进程（三产品）、Cowork 对账进程、网页进程（三产品）的进程数与内存上限读 runtime.env，变量见文件头「可调容量」。计费后台不变。 |
| `infrastructure/production/capacity.py` | 启动前按每个进程的最低内存把关（识别 512 MiB、对账 768 MiB、网页 256 MiB，按原值折算）；只加进程不加内存会被拒绝。总量仍须装得下宿主。 |
| `infrastructure/production/site-step.py` | `pearnly_ocr_budget` 读 `PEARNLY_OCR_BUDGET`（JSON，只覆盖给出的键，默认 `{"concurrency": 8, "tenant_concurrency": 3, "rpm": 120}`）；`PEARNLY_DOCUMENT_AI_REGIONS`（JSON 列表 `[{location, processor, version, rpm}]`，顺序即优先顺序）有值才写 `pearnly_document_ai_regions`，没值不写、用代码默认。 |

## 压测后怎么调（每步仍需用户同意）

1. 在 `/opt/pearnly/secrets/runtime.env` 写对应变量（进程数和内存成对改）。
2. `python3 /opt/pearnly/deploy/infrastructure/production/capacity.py --product <p>`（需 `PEARNLY_IMAGE`）先过容量检查。
3. 按 `scripts/production/activate-product.sh` 的维护流程重建该产品容器。
4. `PEARNLY_OCR_BUDGET` / `PEARNLY_DOCUMENT_AI_REGIONS` 只在跑开户步骤时写进 common_site_config；
   不开新户时要让三个产品同时生效，需对三组各跑一次写配置（`bench set-config -g ... --parse`），
   三组必须一字不差，否则 budget 报 policy_mismatch。去掉 `PEARNLY_DOCUMENT_AI_REGIONS` 不会自动删除已写的键。

## 验证

- `scripts/check-production-capacity.py`：默认值等于原值；调大（进程+内存）放行；只加进程、进程为 0 拒绝；超出宿主拒绝。
- `scripts/production/test-site-step-capacity.py`：默认限额不变、只覆盖给出的键、非法值拒绝；区域有值才下发、缺字段/重复拒绝。
- 未部署；未在生产跑。

## 2026-10-01 压测后：放宽容量检查（用户选 a）

- 生产压测（NOFORK 上线后）：识别卡在进程数、CPU 不再是瓶颈（cowork 6 进程峰值 1302MiB，约 210MB / 进程）。
  目标值：cowork-ocr 12 / 4g、erp-ocr 12 / 4g、dms-ocr 4 / 2g、cowork-web 6 / 1.5g，其余不变。
- `capacity.py`：
  - 上限之和 + 宿主保留 ≤ 物理内存 × 1.3（原 1 倍）；
  - 执行时 MemAvailable ≥ max(8 GiB, 物理内存 25%) + 本步临时内存（原 = 宿主保留 + 临时）；
  - 每进程下限：识别 512 → 320MiB（NOFORK 实测 210 的 1.5 倍），对账 768 → 512MiB，网页 256 不变。
- 更正：09-30 说"只剩约 342MiB"把当时还在跑的 `pearnly-maintenance-erp`（1024MiB）算进了生产。删掉后生产上限合计
  27520MiB，这组目标值（28544 + 3209 ≤ 32095）在 1 倍规则下也装得下，真正拦它的是旧的每进程 512MiB 下限。
- 风险：合成发票比真实大图 / 多页 PDF 小；NOFORK 下每进程内存停在处理过的最大一单。上线后用真实大图在 12 进程下复压，
  识别容器超过上限 70% 就回调 runtime.env。

## 2026-10-01 真实大文件 OOM：识别下限 320 → 450，cowork / erp 识别 8g

- pA 真实大文件回归时 cowork-ocr-worker 顶到 3.975 / 4GiB，`memory.events` oom_kill 2：NOFORK 下每个进程停在处理过的最大那一单，
  真实大文件每进程 330–390MB（合成发票只有约 210MB）。
- 生产（用户点头）：`PEARNLY_COWORK_OCR_MEM` / `PEARNLY_ERP_OCR_MEM` 4g → 8g，进程数 12 不变；备份 `runtime.env.bak-20261001-ocr8g`。
  上限合计 + 宿主保留 39945 / 41724（1.3 倍）。
- `capacity.py` 识别每进程下限 320 → 450。按 450 算，cowork 32 进程要约 14g，在 1.3 倍规则下放不下，今晚的进程数要重定。
- 定期释放高水位：Frappe v16 `RQ_MAX_JOBS = 5000` 是模块常量，没有配置能改；建议由共用层 after_job 按进程 RSS 超阈值自发 SIGTERM
  （rq 收尾后退出、WorkerPool 补起），已交修复窗口评估。

## 2026-10-01 用户选 A，今晚执行（待做）

- mariadb 上限 2g → 3g：compose 改为 `PEARNLY_MARIADB_MEM`（默认 2g）；线上用 `docker update --memory 3g --memory-swap 3g` 不重启生效，
  同时 runtime.env 写 `PEARNLY_MARIADB_MEM=3g`，保证以后重建仍是 3g。max_connections 300 已于 10-01 用 SET GLOBAL 生效。
- cowork-ocr-worker 12 → 18 进程，上限保持 8g（18 × 450 = 8100）；erp 12 / 8g、dms 4 / 2g 不变。
- 合计（上限 + 宿主保留）40969 / 41724。单租户上限按「≤ 进程数 − 4」取 12–16，等压测窗口的 24/40 结果定。
