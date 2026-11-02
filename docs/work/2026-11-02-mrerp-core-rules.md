# MR.ERP 规则下沉共用层（评估 §13 第 5 步）

- 依据：[需求影响评估](2026-11-02-mrerp-operator-and-erp-company-评估.md) §0、§1、§12.1 定案、§13 第 5 步、§14.6；先例 [ADR-025](../adr/0025-cc-desktop-direct-product-sites.md)「决定 1」。
- 分支 `pearnly/mrerp-core-rules`，工作树 `.claude/worktrees/mrerp-core-rules`；基线 `a5ea574f`（= `origin/main` `343f9b78` + 评估文档）。
- 目的：ERP（`pearnly_erp_portal`，按 ADR-009 不能导入 Cowork）第 6 步要把 MR.ERP 当账本（导入直写 + 代录），先把 MR.ERP 与产品无关的部分
  搬进 `pearnly_core.mrerp`。**纯机械搬移，逻辑一字未改，Cowork 行为零变化。** 不碰真实 MR.ERP 站点。

## 结果

`pearnly_core.mrerp`（包说明第一段写明多产品共用，ADR-009 / ADR-025 / 评估 §14.6）：

| 新位置 | 原位置 | 内容 |
|---|---|---|
| `session` | `connections/mrerp_session.py` | 登录会话、选账套、`lock_key`（一个账号一把锁）、`public_host` |
| `urls` | `connections/model.py` 的 `validate_url` 函数体 | 系统地址存储语法校验（新文件，函数体原样） |
| `catalog` | `connections/mrerp_catalog.py` | 选公司页 → 公司 / 年度目录、目录快照 |
| `master_reader` | `connections/mrerp_master_reader.py` | 主档读取（请求函数注入） |
| `needs` | `connections/mrerp_needs.py` | 推之前按方向挑要人选的菜单 / 默认值 |
| `menus` / `masters` | `targets/mrerp_menus.py` / `targets/mrerp_masters.py` | 导入菜单、主档默认值的纯规则 |
| `push.duplicates` | `push/retry.py` 的 `is_already_pushed_error` | 「单号已存在」判定（新文件，函数体原样） |
| `push.{account_valve, autocreate, defaults, models, modules, preview_parse, readback, report_parse, report_parser, routing, transport}` | `push/mrerp/` 同名 | 方向判定、模块表、对方 / 商品自建、默认值、预览 / 报告 / 回读解析、导入 5 步 |
| `push.xlsx`（含 `templates/` 六个官方模板） | `push/mrerp/xlsx/` | 导入表生成 |

留在 Cowork（与 Cowork 的表、排队或白名单绑定）：

| 模块 | 留下的原因 |
|---|---|
| `push/mrerp/runner.py` | 读写 `Cowork Push Record`、推送账本（`push.native`）、取连接凭据；`frappe.enqueue("pearnly_cowork.push.mrerp.runner.run")`；hooks 的 `scheduler_events`（`runner.sweep`）与 `pearnly_worker_preload` 按点路径引用 |
| `connections/mrerp_collector.py` | 账套目录采集：读连接、解密密码、写快照 / 刷新记录（Cowork 表），`frappe.enqueue` 点路径 `connections.mrerp_collector.run` |
| `connections/mrerp_menu_reader.py` | 读导入菜单与主档：写连接上的菜单 / 主档选项，`frappe.enqueue` 点路径 `connections.mrerp_menu_reader.run` |
| `connections/api.py`、`assistant/pending_api.py` 的白名单方法 | 对外 RPC（`connections.api.mrerp_menu`、`assistant.pending_api.mrerp_needs` / `mrerp_answer`），路径不动 |
| `connections/model.py` 的 `validate_url` | 保留，改为调用共用层并照旧抛 `connection.url_invalid`（Cowork 连接配置校验用） |
| `push/retry.py` | 推送状态分类（Express 也用），`is_already_pushed_error` 从共用层导入后照旧是本模块的对外名 |

## 依赖盘点（搬移前）

| 模块（原位置） | 依赖 | 判定 |
|---|---|---|
| `connections/mrerp_session.py` | requests、socket、ipaddress、re、urllib；Cowork `connections.model.validate_url` / `ConnectionError`（纯函数）；`mrerp_catalog` | 搬。地址校验先把函数体下沉为 `pearnly_core.mrerp.urls`，Cowork `model.validate_url` 改为调用它（前置小调整） |
| `connections/mrerp_catalog.py` | html、re、datetime、`pearnly_core.tax_documents.amounts` | 搬 |
| `connections/mrerp_master_reader.py` | json、typing、`targets.mrerp_masters`；HTTP 由调用方注入 | 搬 |
| `connections/mrerp_needs.py` | `targets.mrerp_menus` / `mrerp_masters` | 搬（纯；ERP 第 6 步停在单据上请人选时同样要用） |
| `connections/mrerp_collector.py` | frappe、`get_decrypted_password`、Cowork `access` / `model` / `refresh` / `snapshots` / `state`、`targets.projection`、enqueue 点路径 | 留 |
| `connections/mrerp_menu_reader.py` | frappe、密码、Cowork `access` / `model` / `service` / `state`、enqueue 点路径 | 留 |
| `targets/mrerp_menus.py`、`targets/mrerp_masters.py` | 只用标准库 | 搬 |
| `push/mrerp/account_valve.py`、`models.py`、`modules.py`、`preview_parse.py`、`readback.py`、`report_parse.py` | 标准库 | 搬 |
| `push/mrerp/report_parser.py` | openpyxl | 搬 |
| `push/mrerp/autocreate.py`、`defaults.py` | 标准库、`xlsx`、`master_reader`、`mrerp_masters`；只经 adapter 参数发请求，不碰表 | 搬 |
| `push/mrerp/routing.py` | `pearnly_core.express_rules`、`tax_documents`、`models` / `modules` / `xlsx` | 搬 |
| `push/mrerp/transport.py` | `pearnly_core.session_lock`、`express_rules`、session、master_reader、上面各模块；Cowork `push.retry.is_already_pushed_error`（纯函数）；不碰表 | 搬。重号判定先下沉为 `pearnly_core.mrerp.push.duplicates`，`push.retry` 改为从那里导入（前置小调整） |
| `push/mrerp/xlsx/*`（含模板） | 标准库、openpyxl、`express_rules` | 搬（模板按 `__file__` 相对路径找，随包一起搬） |
| `push/mrerp/runner.py` | frappe、`Cowork Push Record`、`connections.service`、`push.native`、`push.retry`、hooks 点路径 | 留 |

搬移后 `pearnly_core.mrerp` 只依赖标准库、requests / openpyxl、`pearnly_core.mrerp` / `express_rules` / `tax_documents`，`push.transport` 另用
`pearnly_core.session_lock`；不导入 Frappe 与任何产品（`tests/test_mrerp_core_boundary.py` 锁住，CI 跑）。没有新循环：共用层不反向依赖 Cowork，
Cowork 原先 `push → connections`（transport 用会话）这条跨域依赖随之消失。

## 字符串调用方核对

逐个 grep 了 `apps/`（含 `hooks.py`、`patches/`、`public/`、`www/`）、`frontend/assistant/src`、`tests/`、`scripts/`、`infrastructure/`：

| 字符串 | 位置 | 处理 |
|---|---|---|
| `pearnly_cowork.push.mrerp.runner.sweep` | `hooks.py` `scheduler_events`（库里的 Scheduled Job Type 也存这个） | runner 留下，不变 |
| `pearnly_cowork.push.mrerp.runner` | `hooks.py` `pearnly_worker_preload` | 不变 |
| `pearnly_cowork.push.mrerp.runner.run`、`connections.mrerp_collector.run`、`connections.mrerp_menu_reader.run` | `frappe.enqueue`（RQ 里排着的任务存这个） | 三个模块都留下，不变 |
| `pearnly_cowork.assistant.pending_api.mrerp_needs` / `mrerp_answer` | `frontend/assistant/src/lib/companies.ts`、测试 | 白名单方法留下，不变 |
| `pearnly_cowork.connections.api.mrerp_menu` | `public/desk/targets.js` | 不变 |
| `patches/v0_0_7_mrerp_legacy_menu_review`、`v0_0_24_mrerp_task_company` | `patches.txt` | 不导入被搬模块，不变 |
| 被搬模块的路径 | `scripts/i18n/keep-cjk.tsv`（17 行）、`scripts/impact/shared-parts.toml`（7 处） | 换成新路径（豁免内容不变） |
| 被搬模块 | 测试按文件路径 / 模块名载入（`test_cowork_push_direction`、`assistant_tools`、`companies`、`mrerp_masters` 等） | 换成新路径，断言一条未改；`tests/moved_modules.py` 补对照 |

被搬模块没有一个是对外 RPC、排队任务或持久化的点路径，所以按先例不留旧路径壳。生产镜像里核对过：被改的 Cowork 模块与 `pearnly_core.mrerp`
全部 46 个模块都能导入，hooks 的预载与定时路径、三个排队路径、三个白名单方法都解析得到，`model.validate_url` 照旧抛 `connection.url_invalid`。

## 提交

| 提交 | 内容 |
|---|---|
| `a6464daa` test | **搬移前**生成并提交的黄金样本 `tests/fixtures/mrerp/golden-rules.json` 与 `tests/test_mrerp_rules_golden.py`；候选检查离线车道加跑 MR.ERP 推送 / 采集 / 目录 / 菜单测试与这把锁 |
| `cda528f9` refactor | 前置小调整：地址校验 → `pearnly_core.mrerp.urls`，重号判定 → `pearnly_core.mrerp.push.duplicates`（函数体原样，Cowork 两处改为调用）；失效已久的 `test_cowork_push_purity` 修好（ADR-025 搬走 `receipts.py` 与 `express_rules` 后没跟上，原本不在 CI） |
| `30f8c40c` refactor | **纯搬移**：`git mv` + 只改 import 行（git 认作全部重命名），测试只改载入路径 |
| `1e77e00d` test | 登记共用件 `mrerp-rules` 与两个新入口，共用层边界测试进 CI，架构文档补「MR.ERP 规则（共用层）」 |
| `5422e2ac` docs | 被搬文件与 Cowork 调用方注释里的旧路径换成新位置（只改字符串常量，ast 比对代码不变） |
| （本记录所在提交）docs | 本施工记录；全闸门查出的两处补登记：`duplicates.py` 的泰文原文进 `keep-cjk.tsv`，注释提到新位置的 `companies/pending.py`、`push/application/service.py` 登记进入口 |

## 逐字节相同（黄金样本）

- 搬移前的代码（`a6464daa`）在生产镜像里生成：8 张导入表（赊销、现销现金、现销转账、赊购货品、赊购费用、客户 / 供应商 / 商品主档）直接调生成器；
  15 次推送经 Cowork 执行入口 `runner.execute` 对假 MR.ERP 站点跑（自建客户与商品、已映射客户、现销、转账收款、采购货 / 费按所选菜单、只有一个
  已关联菜单、按账套税号判方向、多个菜单要先选、重号回读到、报告逐行失败、主档对不上请重选、受理了却查不到、科目没选停下、密码错），记下每个
  请求的方法、路径、参数、表单、请求头、上传文件，与回执（去掉耗时毫秒）；另记 5 张票的单号推导、预检与单据类型。
- xlsx 的 zip 成员带写入时刻：生成时注入固定时间（`zipfile` 的时钟换成 2026-09-26 09:30:00），生成器里只影响提示的「今天」固定为 2026-09-26。
  每份 xlsx 记成员级摘要（成员名、顺序、压缩方式、时间、解压后字节）与整份文件字节摘要；整份字节随 zlib 实现变，黄金样本记了 zlib 版本，同版本才比整份字节。
- 生成两次（`PYTHONHASHSEED` 0 与 12345）结果一字不差，再用 777 跑测试通过：不受哈希随机影响。
- 搬移后（`30f8c40c` 起）同一生产镜像里 `test_mrerp_rules_golden.py` 3 项全过（整份字节也相同）。

## 测试与闸门

生产镜像：本机没有 `sha256:ea24fcb9…`（`docker images --digests` 与各镜像的 RepoDigests 都查不到，按任务书没有去拉），以下镜像里的检查都用本机
最新的生产镜像 `ghcr.io/skin306152-star/pearnly/frappe@sha256:fd60caffccbb43e677121d770f4987d4438f5cc5ab8336e29af69ec1af2e389e`（10-31 构建），
挂上工作树的 apps 跑（同候选检查 `offline_py` 的挂法）。

| 检查 | 结果 |
|---|---|
| 搬移前后对比：256 个测试文件（全部 `test_cowork_*` 与引用 MR.ERP / 连接 / 推送 / CC 工具的测试），生产镜像与本机 python3 各跑一遍 | 两边退出码逐个相同，唯一变化是 `test_cowork_push_purity` 由失败变通过（本批修好）；MR.ERP 用例数不变：push 41、collector 15、catalog 8、menus 17、masters 12、push_direction 28 |
| `test_mrerp_rules_golden.py`（生产镜像） | 通过 |
| `test_mrerp_core_boundary.py`、`test_cowork_push_purity.py` | 通过 |
| 导入冒烟（生产镜像，无站点） | 46 个模块可导入，点路径与白名单方法都解析得到 |
| `scripts/check-architecture.py` | PASS（0 违规，没有扩大基线） |
| 本地全闸门（`~/pearnly-devtools/gate.sh`，照 `development.yml` 每一步） | 第一遍 161 步通过、2 步不通过（`check-i18n --strict`：新文件 `duplicates.py` 的泰文原文没进豁免表；`check-impact` / `test_check_impact`：注释提到新位置的两个文件没登记），补登记后复跑见下行 |
| 全闸门复跑（`SKIP_NPM_CI=1`，依赖第一遍已装） | 164 步全部通过，退出码 0 |
| 候选检查离线车道（`WT=… IMAGE=fd60caffccbb bash ~/pearnly-devtools/cand-local.sh offline`） | 通过（退出码 0，72 组全部 OK）；本机副本是旧清单，本批加进 `scripts/ci-candidate-checks.sh` 的 MR.ERP 测试另在同一镜像里跑过（上面的对比） |
| `scripts/check-unbounded-queries.py`、`check-architecture.py`、`check-impact.py` | 都在全闸门里通过（影响清单见下节） |
| 分支 Development checks | `62e36d55` 上通过（run 41425776372，7 分 42 秒）；本行所在的记录提交只改文档 |

## 入口实测（`scripts/check-impact.py`）

改动命中共用件 `mrerp-rules`（新登记）；其余是只改到调用方的入口。这批只换导入路径，Cowork 行为零变化由单元测试与黄金样本证明；按任务书不连真实
MR.ERP 站点、不动测试机，所以要点真实 MR.ERP / Express 的入口都没有实测。

| 入口 | 结果 |
|---|---|
| `cowork.mrerp.push` | 离线通过（假 MR.ERP 站点：`test_cowork_mrerp_push` 41 项、黄金样本 15 次推送逐字节相同、`runner` 与定时巡检路径解析）；真站点没测到（任务书不许连 mrerp4sme.com） |
| `cowork.mrerp.catalog` | 离线通过（`test_cowork_mrerp_collector` 15 项、`test_cowork_mrerp_catalog` 8 项、`test_cowork_mrerp_menus` 17 项、`test_cowork_connection_read_locks`）；真站点没测到（同上） |
| `cowork.cc.mrerp-setup` | 离线通过（`test_cowork_assistant_tools`、`test_cowork_companies` 的 MR.ERP 公司与待处理当场选、`test_cc_month_ledger`）；对话页实测没测到（要真 MR.ERP 连接） |
| `cowork.express.push` | 没测到（自动冒烟要写测试机 C 盘账套，任务书不许动测试机）；本批没改 Express 路径，只有 `push/retry.py` 的重号判定改为从共用层导入，`test_cowork_push_source_parity` 所在的分类核对、`test_cowork_push_lifecycle` 通过 |
| `cowork.companies` | 离线通过（`test_cowork_companies` 全部，含 MR.ERP 公司与待处理当场选）；页面实测没测到（命中原因：`companies/pending.py` 只改了注释） |
| `cowork.connections` | 离线通过（`test_cowork_connections`、`test_cowork_connection_read_locks`、`test_cowork_any_set_catalog`、`test_cowork_express_account_sets`）；页面实测没测到 |
| `cowork.cc-chat.push` | 离线通过（`test_cowork_assistant_tools`、`test_cowork_companies`）；对话页实测没测到 |
| `shared.shell-gate` | 没测到（只因 `assistant/tools.py` 注释与导入行变动被列入，功能清单与开通闸代码未改） |
| `cowork.ocr`、`cowork.execution` | 没测到（付费识别；命中原因同上：`assistant/tools.py`、`push/application/service.py` 只改了导入 / 注释） |
| `cowork.assistant` | 离线通过（`test_cowork_assistant_tools`、`test_cowork_companies`）；页面实测没测到 |
| `cowork.tax-documents` | 离线通过（`test_tax_documents` 在全闸门里；`catalog`、`routing` 只换位置）；页面实测没测到 |

## 剩余项

- ERP 接 MR.ERP（第 6 步）直接调 `pearnly_core.mrerp`；Cowork 留下的 runner / 采集 / 菜单读取是 Cowork 自己的流程，ERP 那一半另写（评估 §14.6）。
- `scripts/ci-candidate-checks.sh` 的离线车道这批加了 MR.ERP 测试；本机副本 `~/pearnly-devtools/cand-local.sh` 是旧清单，没有它们（本批另外单独跑过）。
- 上述「没测到」的入口要在部署批次里由验收窗口按清单点；这批不需要单独部署（只是代码位置变化）。
