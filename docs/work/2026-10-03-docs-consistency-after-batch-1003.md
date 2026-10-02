# batch-1003 之后的开发文档一致性清理（2026-10-03）

纯文档任务。目标：以当前 `origin/main` 代码为准，把开发文档里过时、误导、互相矛盾的描述删掉或改正；不新增规则、不扩写；
`docs/work/` 下已有的历史记录不动（它们是当时的记录）。

- 工作树 `/Users/skin/pearnly/.claude/worktrees/frosty-hypatia-3d5fec`，分支 `codex/m8-replay-record`，HEAD `e7043302`
  （与 `origin/main` 的合并基线 `5240ce8b`，分支上只有一笔文档提交）。本次改动未提交。
- 核对方法：先读 `docs/development/*`、`docs/adr/*`、`docs/production/*`、`docs/operations/*`、版本矩阵、`CONTRIBUTING.md`、`AGENTS.md`、
  各 README；再对 10-01 到 10-03 落地的每一项用 `git grep` 找旧说法，对照代码或提交后改正。
- 检查：`git diff --check` 通过；新加的仓库相对链接逐个确认文件存在。

## 改动清单

| 文件 | 删了什么 / 改了什么 | 依据 |
| --- | --- | --- |
| `docs/development/architecture.md`「识别读法（ADR-013）」 | 删「`gemini-3.1-flash-lite` 读 … 3.8 只是站点开关（默认关）」这种一概而论的写法；改成：ERP 固定 flash-lite（3.8 升级只是 `pearnly_erp_ocr_escalation`，默认关）；Cowork 电子 PDF 走文字层 flash-lite，扫描件 / 照片看图直读按站点配置 `OCR_DIRECT_MODEL`，四个 Cowork 租户已写 `gemini-3.8-flash`（优惠价到 2026-12-31）；`OCR_LAYER3_ENABLED` 仍默认关；改读法先过上线闸门（`eval-invoices.py`） | `apps/pearnly_cowork/pearnly_cowork/ocr/engine/config.py`（`OCR_DIRECT_MODEL`、`OCR_LAYER3_ENABLED`）、`apps/pearnly_erp_portal/pearnly_erp_portal/recognition/policy.py`；提交 dd1c41fa、bfd22900；`docs/work/2026-10-02-direct-model-3.8.md`、`2026-10-03-release-batch-1003.md`（部署回读 4 站 3.8） |
| `docs/development/architecture.md`「三产品共享识别执行」 | 删「Cowork … 未部署、未做付费模型验收；ERP 仍预留。不能据此宣称整产品 OCR 迁移或线上验收完成」；改成 DMS、Cowork、ERP LINE 收单（`erp.line_intake`）都已接入并在生产运行；ERP 网页上传识别是 `ocr` 队列的普通后台任务（推送优先、轮询兜底：在线 20 秒、断开 5 秒），不走共享工作单 | `apps/pearnly_erp_portal/pearnly_erp_portal/hooks.py:337`（`pearnly_ocr_handlers`）、`recognition/background.py`（`frappe.enqueue(... queue="ocr")`）；提交 7b9e745a、b91181de |
| `docs/development/architecture.md`「Cowork 发票在 Cloud Run」段 | 删「可在 Cloud Run 上执行 … 按行读回进度与结果」这个只写转发路径的描述；补生产现状：Cloud Tasks 派发（`pearnly_cowork_ocr_tasks`，4 个队列分片）、回调唤醒工作单、结果一入库先通知等候页再扣费、入库事务内范围权限只查一次、识别进程 `OcrWorker` 从队列直接交线程不再做完整站点初始化、进程 × 线程由 `sizing.py` 算（生产 8 × 12）；两个开关各自关掉回到什么 | `apps/pearnly_cowork/pearnly_cowork/ocr/remote/tasks.py`（`shards`）、`apps/pearnly_core/pearnly_core/execution/ocr_worker.py`、`infrastructure/production/sizing.py`；提交 432e562e、e421a263、1024f3bc、0e9c828b、c9c1d605；`docs/work/2026-10-02-release-batch-next.md`（4 队列上线） |
| `docs/development/architecture.md` 容量段 | 「上线预算与证据见共享 OCR 容量」改为：当前生产参数见 `production-capacity.md`，共享 OCR 容量只作最初设计与证据 | 两份文档内容对照（旧文写全局 4 在途 / 单租户 2，新文写识别名额 160） |
| `docs/development/production-capacity.md` | 表头「2026-10-02 下一批起」改「2026-10-03 batch-1003 起生产现值」；「可以等」表里「redis-cache 内存 现 128 MB 够用」改为已按主机内存自动算（生产 256mb，m8 淘汰 0）——原来与同页第 2 节的公式自相矛盾 | `infrastructure/production/sizing.py:64-68`；`docs/work/2026-10-03-release-batch-1003.md`（部署回读）、`2026-10-03-m8-replay-stress-and-closeout.md`（淘汰 0） |
| `docs/adr/0013-ocr-read-strategy.md` 头部 | 加一条「后续（2026-10-02）」：Cowork 扫描件 / 照片直读改 3.8（`OCR_DIRECT_MODEL`），电子 PDF、ERP、DMS 不变；两个 3.8 复核 / 升级开关仍默认关。正文决定与实测数字不动 | 同上第一行 |
| `docs/adr/0015-ocr-on-cloud-run.md` | ① 决定 2 补「结果一入库先通知等候页、再扣费；入库事务内范围权限只查一次」；② 决定 4「单次 600 秒」改为 1800 秒并注明何时改；③ 删「ERP 网页识别是同步请求」，改为 10-02 起后台任务、推送优先、轮询兜底；④ 任务派发补充末尾加「生产现状」：代码默认关但生产 10-02 起开着、4 个队列、`OcrWorker` 不再做完整站点初始化 | `scripts/cloud-run/deploy-cowork-ocr.sh:23`（`--timeout 1800`，由 61da2039 改入）；提交 7b9e745a、b91181de、1024f3bc、0e9c828b、432e562e、e421a263；`docs/work/2026-10-02-erp-recognise-background.md` |
| `docs/development/verification.md` 识别闸门 | 「压测窗口的 100 人压测同样省费用：对账从 15 套减到 5 套」改为：发票识别用回放（`scripts/ocr-replay.py`，`pearnly_ocr_replay`，不调谷歌、识别费 0，只认 test* 系列站点，架构检查拦误开，原件池与回放库在生产机）+ 对账 5 套；闸 ② 去掉「由压测窗口」（该窗口已收工） | `scripts/ocr-replay.py` 文件头、`scripts/check-architecture.py:66-90`、`apps/pearnly_core/pearnly_core/execution/replay.py`；提交 0d129bd4、d478b605；`docs/work/2026-10-03-m8-replay-stress-and-closeout.md` |
| `docs/development/test-environment.md` | 「两个整站导入的测试租户」改三个（加 `testcowork`）；「`lab-start` 产品名 dms、erp，不带=两个都动」改为 dms / erp / cowork、不带默认 dms；「隧道同时带两个域名」「DMS 与 ERP 共用导出通道」「这两个测试租户」「两个测试域名都切回」改为三个 / 被切的产品。其余（含 10-02 发现 `cowork-ocr-test` 已不存在那段）保持 | `scripts/testenv.sh:40`（默认 `dms`）、`:288`、`:296`；`scripts/testenv-cloudflare.sh:26`（三个主机名） |
| `infrastructure/testenv/README.md` | 差异表：数据卷名改为按产品 `pearnly-testenv-<app>_sites-*`；域名行补测试租户用自己的 `test<app>.pearnly.com` 切解析；删「OCR 凭据 占位空凭据，不产生付费调用」，改为测试机自 09-24 起是线上真凭据、识别是真实付费调用、只用于人工验收；删「本机 Keycloak 与测试租户」，改为测试租户走线上 Keycloak、账本是导入副本、只有空白站点用本机 Keycloak。用法示例从旧的无产品名 `up`/`sites` 改成 `lab-start` / `live` / `lab-stop` | `infrastructure/testenv/override.yml:10-22`（卷名含 `TESTENV_PROJECT`）；`docs/development/test-environment.md`「凭据与边界」「一个产品一套栈」；`scripts/testenv.sh` usage |
| `scripts/README.md` | `testenv.sh` 边界补 `live` / `live-off`；`testenv-cloudflare.sh` 补 `testcowork`；新增三行：`ocr-replay.py`（只给测试租户、零识别费、误开被拒）、`eval-invoices.py`（`run` 真识别计费，`gold` / `subset` / `l1` 零费用）、`draw-dms-line-menu-originals.py` → `render-dms-line-menus.py`（离线幂等、顺序、何时重跑） | 各脚本文件头；`scripts/eval-invoices.py:972-1002` 子命令 |
| `infrastructure/README.md` | 补两条运行定义：容量参数由 `sizing.py` 算、`capacity.py` 把关；每日异地备份 timer / 脚本 / 桶 / 保留期 / binlog 7 天。末行「OCR 容量规则见共享 OCR 容量」改为当前参数看 `production-capacity.md`、共享 OCR 容量只作最初设计 | `infrastructure/production/sizing.py`、`capacity.py`、`pearnly-offsite-backup.{service,timer}`、`scripts/production/offsite-backup.sh`；提交 629806c7、b4b02d54；`docs/work/2026-10-02-offsite-backup.md` |
| `docs/operations/shared-ocr-capacity.md` | 开头「历史生产只有 DMS …尚未迁入」加「写成时」限定；表前加一段引文：表中「初始设置」是 2026-09 起步预算，当前参数以 `production-capacity.md` 为准、执行位置见 ADR-015，规则本身仍适用。表与证据原文不动 | 与 `production-capacity.md`、ADR-015 对照 |
| `docs/production/01-foundation-20260919.md` | 「首个正式租户前仍要完成」清单前加一句：这是 2026-09-19 时点清单、原文保留；第 5 项异地备份已于 10-02 上线并指向记录。清单本身不改 | `docs/work/2026-10-02-offsite-backup.md`；提交 629806c7、4ca70daf（恢复演练） |

## 核对过、未发现旧说法（不用改）

- 识别进程「4 个」、缓存「128mb」只剩 `docs/p1/01-version-matrix.md` 的 T0 纸面预算表（2026-09-17 历史实测记录，文件头已声明是历史）和 `production-capacity.md`「可以等」那一行（已改）。
- 「Cowork 识别在服务器本机同步跑」：开发文档范围内没有；ADR-015 / 架构文档里剩下的「本机识别」都是指开关关掉时的回退路径或表格 / 复核表回导，属实。
- DMS 查询「เร็ว ๆ นี้」占位、「压测要真调谷歌」、「备份只在部署时本机」：只出现在 `docs/work/` 历史记录（当时的现状描述），不在本次范围。
- 用户定的不做项（复核收窄、多页逐页出结果、Cloudflare 加速、LINE 计时日志）：开发文档范围内没有把它们列成待办；只在 `docs/work/2026-10-02-cowork-speed-next.md`「未做 / 待定」和 `2026-10-02-review-narrowing.md`（文内已写「不做，以后也不排」）里，均为历史记录，不动。
- bootinfo 翻译表按语言共享、锁和票据在不淘汰的 Redis、HEIC → JPEG（`pi-heif 1.4.0` 已在版本矩阵）、导出共用组件、LINE 加载动画、宿主机 nginx 真实 IP 与 `pearnly-timing.log`、`--skip-search-index`、DMS 菜单 6 与图文菜单 3+3 / 3+2：开发文档里没有与之矛盾的旧说法；本次只在 `scripts/README.md` 登记了画图脚本，其余不另写。
- `AGENTS.md`、`CONTRIBUTING.md`、`README.md`、五个 App README、`docs/development/{workflow,records,migration-baseline,agents,current-state}.md`、ADR-016：核对后无需改。

## 未动、需人工核对

- `docs/development/test-environment.md`「当前状态与交接（2026-09-26）」表里的卷名、镜像 SHA 是当时快照；Cowork 一节的「独立账号池 `pearnly-cowork` 还没在线上建」是否仍属实未核（LINE 窗口的事），没改。
- `docs/development/verification.md`「`check-dms-offline.py`（207 条离线业务断言）」的条数未重新数，没改。
- `docs/adr/README.md` 对 ADR-003 / 004 的待定稿说明、`docs/p1/01-version-matrix.md` 的 12 GB 目标机与 WeKan 预算：历史记录，按 `records.md` 不改旧状态句。
- `docs/operations/shared-ocr-capacity.md` 表中「供应商速率 30 请求/分钟」「排队全局 300 / 单租户 50」等具体值是否仍是生产 `pearnly_ocr_budget` 的现值，没有在生产上读回，只加了「以 production-capacity 为准」的限定。
