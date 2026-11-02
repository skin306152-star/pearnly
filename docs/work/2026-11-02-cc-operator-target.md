# 代录目标分路（MR.ERP 代录第 1 步）施工记录

- 评估：[MR.ERP 代录与 ERP 左侧公司](2026-11-02-mrerp-operator-and-erp-company-评估.md) §13 第 1 步（§0、§4、§5、§7、§14.1、§14.3、§14.4、§12.1 定案）；
  决定写进 [ADR-026](../adr/0026-operator-targets.md)。
- 分支 `pearnly/cc-operator-target`，工作树 `.claude/worktrees/cc-operator-target`，基线 `a5ea574f`（= origin/main `343f9b78` + 评估文档）。
- 本步目标：给代录加「执行目标」（`express` / `mrerp`），为第 2、3 步铺路；**对 Express 代录与所有直写零行为变化**；MR.ERP 此时没有执行层，
  只做分路与诚实拒收。

## 改了什么

| 层 | 文件 | 改动 |
|---|---|---|
| 共用层契约 | `apps/pearnly_core/pearnly_core/express_operator/contracts.py` | `TARGET_EXPRESS / TARGET_MRERP / TARGETS`、`MRERP_OPERATOR_PAYLOAD_VERSION = 18`、`target_of(payload)`；`for_operator(..., target="express")`：Express 不写目标键、载荷与改动前逐字节相同，`mrerp` 写 `operator.target`、版本至少 18，认不出的目标 `ValueError` |
| 共用层产品钩子 | `express_operator/products.py`、`api.py`、`__init__.py` | 可选声明 `target`（`(company) -> "express" / "mrerp"`），没声明 = Express，认不出的值按请求无效拦下；`products.submit`（开始、录到中途作答都走它）把目标带进批次信息 `batch["target"]`。两产品都没声明，行为不变；Cowork 的 `operator_express_only` 拦截保留，ERP 照旧 |
| CC 契约 | `companion/src/companion/express_operator/contract.py`、`payload_contract.py`、`express_operator/__init__.py` | 同值的目标常量与 `target_of`；载荷版本支持集加 18、`MRERP_OPERATOR_PAYLOAD_VERSION = 18`；`OPERATOR_TARGETS = ("express",)`（心跳报的能力） |
| CC 写账线 | `companion/src/companion/worker.py` | `_handle_dbf_task`：代录载荷且目标不是 Express → 不找 Express 账套，直接交 `_handle_operator_task(…, None)`；`_handle_operator_task` 按目标分路：Express 原样（一行逻辑不改），别的目标交 `_handle_operator_target`（不拿 `PEARNLY.LCK`、不进 Express 建主档、不要求本机有 Express）；回执口径抽成 `_ack_operator`（两条共用，逻辑不变） |
| CC 宿主 | `companion/src/companion/express_operator/host.py` | 原来的 `default_factory` 原样改名 `express_factory`（旧名保留指向它）；`ASSEMBLERS = {"express": express_factory}`；`Host.handle` 按 `operator.target` 取装配函数，没有的目标不开批次、不建批次文件、不碰账本，回 `human / unsupported`（写账线报 `OPERATOR_UNSUPPORTED`，界面沿用「此类单据暂不支持」，没新增文案） |
| CC 心跳 | `worker.py`、`queue_client.py` | 心跳报 `operator_targets`（本步 `["express"]`） |
| 派单纯规则 | `apps/pearnly_core/pearnly_core/cc_desktop/wire/route_contracts.py` | `Peer.targets`（旧版 None = 只有 Express）、`Job.target`（默认 Express）、`stored_targets / targets_of / operates`；`decide`：Express 不变；别的目标走 `_decide_chosen`——只给 `route_device` 那台、它报过这个目标、支持载荷版本，不看账套可达、不黏住；不会这个目标 → `no_target`（不派、单子等它），报了目标但版本不够 → `needs_update` |
| 共用通道 | `cc_desktop/devices.py`、`machine.py`、`Pearnly CC Device` 新字段 `operator_targets` | 心跳存目标（认不出 / 不报保留上次），`peers()` 带进 `Peer.targets` |
| Cowork 通道 | `connections/reports.py`、`companion/devices.py`、`companion/credentials.py`、`companion/transport.py`、`Cowork CC Desktop Device` 新字段 `operator_targets` | 心跳计划收 `operator_targets`（变了才写）、存在电脑行上、`pool_peers` 带出 `targets`、领单的 `Peer` 带上 |
| 文案 | 两个 App 的 `i18n/messages.tsv` + 生成的翻译包 | 新增字段标签「Operator Ledgers / 实操账本」（四语），未改已有文案 |
| 登记 | `scripts/impact/shared-parts.toml` | 新共用件 `cc-operator-target`（入口 `cc.desktop.operator / cc.desktop.write / cc.desktop.places / cowork.cc.channel / erp.cc.connect`）；`cowork.cc.channel` 补 `companion/credentials.py` |
| 文档 | `docs/adr/0026-operator-targets.md`、`docs/adr/README.md` | ADR-026 |

## 有意变化

- CC 桌面版认载荷 18、心跳最高版本从 17 变 18，多报 `operator_targets`；旧服务端忽略这个键。
- 服务端两个设备表各多一列 `operator_targets`（只读，Frappe 原生迁移加列，无数据补丁）。
- `for_operator` 认不出的目标抛 `ValueError`；`products.target_of` 产品回认不出的值按请求无效拦下（今天没有产品声明，走不到）。

## 本步不做（随后续步骤）

- 两条通道的**领单查询**仍只按账套可达取排头（`jobs._heads`、Cowork `queue_heads`）；MR.ERP 代录单今天不会产生，它存在哪里、怎么取头、
  账号租约，随第 3 步（Cowork）/ 第 6 步（ERP）接上纯规则 `decide` 的目标分支。
- CC 端 MR.ERP 执行层（网页窗口、登录、配方、读回）是第 2 步；那时 `ASSEMBLERS` 与 `OPERATOR_TARGETS` 才加 `mrerp`。
- 包名 `express_operator` 不改（ADR-026 选项 C），两处包头已注明目标不只 Express。

## 测试与闸门（本机实跑，11-02）

新增测试：

- `tests/test_express_operator_shared.py`：两端目标常量同值、派单规则的 `EXPRESS` 同值、载荷 18 两端同号且是 CC 最高版本；**Express 载荷黄金样本**
  （改动前 `a5ea574f` 的 `for_operator` 对固定输入的输出，排序键两份 + 不排序键一份），默认 / 显式 `target=express` / 批次里多带 `target` 都逐字节相同；
  MR.ERP 载荷带 `operator.target` 与版本 18、新 CC 认、只认到 17 的旧版不认；认不出的目标抛错；产品钩子 `target_of`（没声明 = Express、
  声明 mrerp、认不出拦下）与 `submit` 把目标带进批次。
- `tests/test_cc_desktop_channel.py`（`OperatorTargets`）：心跳存目标、旧版不报为 None、乱写的不记；`machine._heartbeat` 请求体落到电脑行；
  派单：MR.ERP 单只给 `route_device` 且报了 mrerp 的那台（不看可达），别的电脑 / 没选定电脑不派，旧版与只报 Express 的不派（`no_target`），
  版本不够「需更新」；Express 单不看目标、行为不变；报了目标的电脑照常领 Express 单。
- `tests/test_cowork_companion_reports.py`、`tests/test_cowork_cc_desktop_devices.py`、`tests/test_cowork_express_dispatch.py`：Cowork 心跳计划收目标
  （变了才写）、电脑行存取、`pool_peers` 带出、领单 `Peer.targets` 带上，Express 照常领。
- `companion/tests/test_operator_host.py`：宿主按目标分路（MR.ERP 回 unsupported、不开批次不建批次文件；Express 照旧；注入 mrerp 装配后按目标取）；
  `ASSEMBLERS` 与心跳 `OPERATOR_TARGETS` 一致；写账线 MR.ERP 不读账套、不拿锁、不建主档、非 Windows 也回 `OPERATOR_UNSUPPORTED`；
  Express 目标（含显式 `target=express`）照旧要 Windows 与 Express 程序；只认到 17 的旧版对 MR.ERP 载荷回 `ERR_PAYLOAD_VERSION`。
- 版本顺延（不是删断言）：`test_payload_contract.py`、`test_dbf_stock.py`、`test_worker_payload_gate.py` 里「未来版本」由 18 改 19，支持集加 18，
  心跳断言最高版本 18 并加 `operator_targets == ["express"]`。

| 检查 | 结果 |
|---|---|
| CC 全套 `companion/tests`（暂存目录 Python 3.12 环境：pytest、httpx、dbf、aenum、PySide6_Essentials，`QT_QPA_PLATFORM=offscreen`） | 1426 通过、46 跳过、0 失败 |
| `tests/test_express_operator_shared.py` | 11 通过 |
| `tests/test_cc_desktop_channel.py` | 29 通过 |
| `tests/test_cowork_express_dispatch.py` | 69 通过 |
| `tests/test_cowork_cc_desktop_devices.py` | 71 通过 |
| `tests/test_cowork_companion_reports.py` | 25 通过 |
| `tests/test_express_operator_tasks.py` | 12 通过 |
| `tests/test_companion_contract.py` | 86 通过 |
| `tests/test_cc_operator.py` | 通过（全闸门内） |
| 本地全闸门（`~/pearnly-devtools/gate.sh`，照 `.github/workflows/development.yml` 全部 run 步骤） | 163 / 163 通过 |
| `python3 scripts/check-architecture.py` | PASS（0 违规） |
| `python3 scripts/check-copy.py --report` | 新增 2（同一词条两个 App）· 改动 0 · 拦截 0 · 标黄 0，清单见下 |
| `python3 scripts/check-impact.py` | 登记表完整（54 个共用件、196 个入口）；命中 `cc-desktop`、`cc-dbf-write`、`cc-operator-target` |
| `python3 scripts/i18n-build.py --check`、`git diff --check` | 通过 |
| 生产镜像候选离线检查（`cand-local.sh offline`，镜像 `ghcr.io/skin306152-star/pearnly/frappe@sha256:ea24fcb9…`，本机按 digest 拉取） | exit 0；72 组全部 OK，共 1427 个用例 |

文案清单（`check-copy.py --report`）：

| 来源·键 | 角色 | 中文 | English | ไทย | 日本語 |
|---|---|---|---|---|---|
| pearnly_core·Operator Ledgers | 标签 | 实操账本 | Operator Ledgers | ระบบบัญชีที่บันทึกแทนได้ | 代行入力先の帳簿 |
| pearnly_cowork·Operator Ledgers | 标签 | 实操账本 | Operator Ledgers | ระบบบัญชีที่บันทึกแทนได้ | 代行入力先の帳簿 |

流程用例：本机自建实验栈 `pearnly-optarget-lab`（端口 18871–18873，挂本工作树），`scripts/flows/run.py --changed --base origin/main --post-status`：
命中 1 条 `cc.operator`（覆盖 `express_operator/products.py`），new / old 两套数据 2 / 2 通过（268 秒），状态已回写提交 `3389aba8`；
跑完 `docker compose down -v` 删掉栈与数据卷，无残留容器。

## 入口实测（`check-impact.py` 清单，15 个）

本步对 Express 代录与直写零行为变化；需要测试机 / 录制机的实机项本步没有授权去动（任务书不碰测试机），按「没测到」如实记，离线覆盖写在原因里。

| 入口 | 方式 | 结果 |
|---|---|---|
| `erp.cc.connect` | manual | 没测到（实机要测试机 CC 配对，本步不动测试机）；离线：共用通道心跳存目标、`machine._heartbeat` 请求体测试通过 |
| `erp.cc.write` | auto：express | 没测到（冒烟要测试机 C 盘 T 账套）；离线：写账线 Express 直写路径测试全过，载荷不变（黄金样本） |
| `erp.cc.masters` | manual | 没测到（实机）；本步未改主档同步代码，`test_cc_desktop_channel` 全过 |
| `erp.cc.stock-void` | manual | 没测到（实机）；CC `test_dbf_void` 等全过 |
| `cowork.cc.channel` | manual | 没测到（实机推一张要 testcowork + 测试机）；离线：Cowork 心跳 / 设备 / 派单测试全过 |
| `cc.operator.page` | offline | 通过（`tests/test_cc_operator.py`，全闸门内） |
| `cc.desktop.write` | auto：express | 没测到（冒烟要测试机写 C 盘）；离线：CC 全套 1426 通过，旧版拒收新载荷有测试 |
| `cc.desktop.operator` | offline | 通过（`companion/tests/test_operator_*` 全过，含新增按目标分路）；实机 rec-laptop 没测到（本步不动录制机） |
| `erp.cc.stock-entry` | auto：express | 没测到（测试机）；CC `test_dbf_stock` 全过 |
| `erp.cc.pos-day` | manual | 没测到（实机）；本步未改 |
| `erp.cc.pos-refund` | manual | 没测到（实机）；本步未改 |
| `erp.cc.stock-transfer` | manual | 没测到（实机）；CC 调拨测试全过 |
| `cc.desktop.places` | manual | 没测到（实机）；离线：心跳带 `operator_targets` 的测试通过 |
| `cowork.connections` | manual | 没测到（实机）；`test_cowork_companion_reports` 全过 |
| `shared.transaction` | manual | 没测到（并发实机）；本步未改事务重试 |

## 剩余项

- 上面「没测到」的实机入口：等主会话安排测试机 / 录制机（CC 测试包验证心跳多报 `operator_targets`、Express 代录与直写照旧）。
- 流程用例：已跑，见下行。
- 第 2 步起：CC 端 MR.ERP 执行层，加 `ASSEMBLERS["mrerp"]` 与 `OPERATOR_TARGETS`；第 3 步把 MR.ERP 代录单接进 Cowork 领单查询。
