# 批次 A：独立复查查出的计费、存档、刷新问题（2026-10-01）

压测窗口开了 4 路只读复查（范围 2bbcb4ee..e7cca8a2 已部署的全部改动），核实后按归属转来。分支 `codex/billing-review`，基线 main `e7cca8a2`。
用户约 15:30 UTC 在本窗口："部署不用等我同意，三个窗口自行协调、按批次统一部署"。

## 计费（第 1 路）

1. **扣费在等租户锁之后必定 1020**（阻塞，复查在 MariaDB 11.8.9 复现）：`billing_api.service_charge` 先 `_tenant_or_throw`（普通读，快照从这里开始）
   才排队锁租户 / 钱包；前一笔改过的钱包一读就是 1020 → `retry_service` 在计费线程里睡着重试 → 线程被占满、问余额超时。
   改：排队拿锁之前 `frappe.db.commit()` 结束快照（`service_charge` 到这里没写过东西）。没放进 `wallet_of`：开户
   （`billing_control.register`、`issues.py`）在调 `wallet_of` 之前已经写了租户，那里提交会把一半的操作先落库。
2. **台账命名抢同一行**：`Pearnly Credit Transaction` 原来 `format:CT-{YYYY}-{#####}`，每笔扣费抢 `tabSeries` 的同一行（全平台跨租户），
   部署那一秒的 41 条 1020 就落在这里。改成 `hash` 命名；已有的 CT- 号不变，没有代码按号码前缀找台账。
3. **录入页自动重交没有 `lookup_error`**：`intake-upload.js` 的 BUSY 加上它（余额不足 `insufficient_balance` 不加，那要充值）。
   顺带更正 `2026-10-01-billing-throughput.md` 里"浏览器本来会重交"的说法——原来并不会。
5. **调计费时占着事务与行锁**：
   - 提交识别（`ocr/execution/service.submit`）改成先不锁原件、算好页数 / 字数问余额，问完 `commit` 结束快照再锁原件、再核一次没被别人抢先建好
     （`_admitted`），文档类的冻结清单也挪到锁之后。
   - 识别后的记账（`settle`）先读、`commit` 放掉事务再调计费，回来锁住写结果（扣费按参考号幂等）。
   - `billing_client.status` 单独超时 (连 2 秒, 读 4 秒)（原来连、读各 10 秒）；「余额够」的结论按「租户 + 类型 + 份数 + 档位」缓存 5 秒，只缓存放行。
6. **管理员同意充值 / 调账没套重试**：`approve_local`、`admin_adjust`、`billing_control` 的 adjust 都套 `retry_service`（已提交的不重试）。
8. **对账识别"扣了钱、对账却失败、这份也不记账"**：识别任务第一次真正发扣费前记一个检查点 `…:billing_sent`（检查点单独提交）；之后重试时
   发过扣费的一定按同一参考号记完，不再因对账已结束而取消；只有从没发过扣费的、对账已结束才取消（`document_ocr.run`、`invoice_ocr.run`）。
9. 原件保护按主键锁仍可能 1020：不做局部重试——MariaDB 的 1020 让整个事务回滚，没法只重试那一条；会碰到的入口（开始对账、上传、
   直传登记）已有整笔重试。
7. 余额预占：用户经压测窗口说"可以"。涉及三个产品提交时带参考号、预占表 / 字段、过期释放，改动面大，不放进今晚的批次 A，单独做成批次 A2。

## 数据库连接核算（第 3 / 4 路）

生产只读：`max_connections` 151，`Max_used_connections` 110（12:41:11，半量压测，当时计费 2×4）。MariaDB 容器 4G，当天 00:49 OOM 过一次；
压测窗口核算固定占用约 1.28G、每个连接平均约 5M（最坏带内存临时表约 19M）。

- `capacity.py` 加连接核算：网页 workers × threads；识别进程 workers + 额外线程 × 0.2（线程等 Cloud Run / 名额 / 模型时已放掉连接，按"同时
  真正持有"算；半量实测 176 个识别线程、网页 72、计费 8 时峰值 110）；其余后台各 1；Keycloak 留 20。合计不得超过 `max_connections` 的 85%。
- 加 MariaDB 内存把关：把上限提到默认 151 以上时，「1310 MiB + max_connections × 5 MiB」不得超过 MariaDB 容器上限的 70%（4G → 约 2.8G 警戒线）。
- 计费 web 收回 2×8（扣费锁修好后事务很短）。
- 生产现值估算 166 个连接：151 下被拒（> 128），210 下放行（≤ 178）；210 时 MariaDB 估算 2360 MiB ≤ 2867。部署时 runtime.env 写
  `PEARNLY_MARIADB_MAX_CONNECTIONS=210`，并 `SET GLOBAL max_connections=210`（不重启数据库）。400 会被内存那道拒。
- `check-production-capacity.py` 的「生产现值」场景换成现在的 runtime.env（识别少进程多线程、MariaDB 4G、210），26 项通过。
- 未做：`max_statement_time` / 限制内存临时表（会误杀迁移、备份、报表这类合法长查询，先靠第⑤关监控 Memory_used，超 2.8G 回退）。

## 结果页存档（第 1 / 3 / 4 路）

- 指纹拆成「数据:代码」两段：只是代码换了（部署）就先回旧存档、后台重建一份（`refresh_later` → `sheets.rebuild_view`，按存档排一个）；
  数据变了（修改、预设、重跑）才当场现算，同一套同一语言同一时间只算一份（队列 Redis 锁，`view_store.building`）。
- 指纹里的预设只算可能影响这一套的：银行按同一家银行（`bank|<银行>|%`），收入按同一个本公司税号（`income|<税号>|%`），销项税没有。
- `activate-product.sh`：Cowork 激活后分离运行一次补算（`run -d --rm --no-deps cowork-worker … view_store.backfill`），不让激活等它。
- 存档清理收到 `recon_tasks.ReconTasks._remove`（删除、重跑替换都经过）与 `vat_check_service.delete_many`，不再有孤儿存档。
- 重跑替换后旧地址 404：替换时记「旧任务 → 新任务」（缓存 7 天），`sheets.load` 对已删除的旧任务回 `{"moved": 新任务}`，页面转过去。

## 列表刷新（第 3 路）

- `p.live` 加限频：两次刷新至少隔 3 秒（原来只去抖，事件间隔超过 0.8 秒就每个事件都整表重拉）；实时连接重连时立即刷一次。
- 录入列表、首页只认有结论的事件：每读完一页的进度推送（只带 `pages_done`）不刷；对账结束（带 `job`）、识别有结论（带 `status`）、
  推送状态变了（带 `history`）才刷。
- 录入列表快刷（5 秒）只为本人、15 分钟内推的「录入中」；同事的、等离线小助手挂着的随事件 / 60 秒兜底。列表接口多回 `owner`。

## 验证

- 镜像里：billing gate / usage counter、recon_view_store（8）、recon_scanned_docs（118）、recon_progress、recon_presets、recon_waiting、
  source_lock_scope、billing_wait、recon_speed、direct_upload、remote_ocr、opening_batch、bank_recon_contracts、intake_live 通过；
  `test_cowork_invoice_checks.py` 在 main 上也失败（预览 `cowork_previews`，不是本分支），已告诉识别派发窗口。
- `test-activate-product.py` 15 项、`check-production-capacity.py` 26 项（计费 2×8、连接 210）、`check-architecture.py` 通过；Development checks 全过（见提交）。
- 推送前补跑 `node --test tests/test-desk-live.cjs` 发现 5 项里 3 项失败、进程不退：新加的 `min_ms`（默认 3 秒）让毫秒级测试全被推迟。
  原有测试显式传 `min_ms: 0`，另加一条「两次刷新至少隔 min_ms」；6 项通过后才提交 `455d8f75`，分支 CI 通过。

## 合入与部署（2026-10-01 夜，泰国时间）

- 识别派发窗口在 `455d8f75` 之上变基 → `5f97938c`（Google 重试、派发、Workflows 预热进展），在生产镜像 cc950252 上跑完整
  `ci-candidate-checks offline` 36 步全过、`tests/test_cowork_*.py` + `test_control_*.py` 138 个文件全过；分支 CI 通过后本窗口把 main
  快进到 `5f97938c` 并推送。
- 构建 `sha256:a88d90a268b390de82df1f768b744edd991ef6c1f0afe72bb748bf15a1e8af79`（构建里的候选检查通过）→ stage → 23:14 起：
  - `runtime.env` 设 `PEARNLY_MARIADB_MAX_CONNECTIONS=210`（改前副本 `/opt/pearnly/state/runtime.env.before-5f97938c`），
    运行中的库用一次性客户端容器 `SET GLOBAL max_connections=210`（不 exec 进 mariadb），回读 210；
  - ERP 23:14–23:17、Cowork 23:17–23:23、DMS 23:23–23:26 激活，billing-web 23:26–23:27 按旧镜像备份 → migrate → 重建。
- 回读：所有业务容器在新镜像；六个域名 ping 200；各站维护模式 0；部署后各站与 billing 的 Error Log 0；max_connections 210、
  Max_used_connections 110（部署前同为 110）、MariaDB 1.96 / 4 GiB（Memory_used 约 1.55 GB）；结果页存档补建在后台跑。
- 计费并发演练（testcowork2，测试租户，每轮 24 个问余额 + 8 个扣费 + 3 个同参考号，参考号前缀 `loadtest:drill-5f97938c`）：
  1020 为 0、重复扣费 0（27 笔 = 27 个不同参考号，同参考号三发只记一笔）、单号为 hash（`pf4p2ss701` 等）；
  billing 刚重启 1 分钟时 p95 6.8–8.5 秒（冷启动），预热后两轮 p95 0.26–0.55 秒。
- 回退：上一镜像 cc950252。两处表结构改动可回退：存档指纹列回退时 Frappe 发现已有更长数据会保留 140 并提示（不报错），旧代码按指纹不符重建；
  流水单号回退后旧格式编号继续、已生成的 hash 单号保留。
- 收银台 POS：本批没动 ERP、hooks、脚本加载与打包，ERP / DMS 不用 `p.live`；生产 `product.js` 200、语法检查通过。
  本窗口浏览器在登录页、不能代输密码，未实际点开 POS，压测窗口压测时复看。
- 识别派发窗口随后修了 Vision 整体故障时的重派过密（`2bb8956a`、`d937e3b0`，只改 Cowork 识别调用与派发），自行按主线集成快进 main；
  本窗口构建 `sha256:f75584a711e65bbaed98716971fba637cc4988ef1190c8f18324adcd21f48124`，23:52–23:57 只激活 Cowork，
  回读容器在新镜像、四站 ping 200、维护模式 0。Cloud Run / Workflows、testcowork 端到端、维护与回退演练、全站开由识别派发窗口负责。
- 部署后 testcowork Error Log：1 条 1020 在 `tabPearnly OCR Checkpoint`（23:34，Workflows 预热进展回调写检查点撞车）、
  维护演练期间 3 秒内 241 条 Session Stopped，已转识别派发窗口核实。
- 清理：`codex/billing-review`、`codex/release-2026-10-01` 的工作树、本地分支、远端分支已删除并回读。
