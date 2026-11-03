# 施工记录：Cowork 的 MR.ERP 代录服务端接线 + 账号租约（ADR-026 第 3 步，Cowork 部分）

- 分支 `pearnly/cowork-mrerp-operator-wiring`，工作树 `.claude/worktrees/agent-ab04b9b27e17c4632`，基线 `8efa897a`
  （`pearnly/mrerp-operator-server`：新原因 `account_in_use` / `account_set_unavailable`、服务端拒收网页 MR.ERP 密码）。
- 依据：[ADR-026](../adr/0026-operator-targets.md) 第 2、5、6、8 条；[评估](2026-11-02-mrerp-operator-and-erp-company-评估.md) §4、§5、§13 第 3 步、
  §14.1、§14.3–§14.5；[第 1 步](2026-11-02-cc-operator-target.md)；[第 10 步](2026-11-02-mrerp-cc-credentials.md)；
  [本窗口记录](2026-11-03-mrerp-operator-server.md)（分支 `pearnly/mrerp-operator-server`）；第 2 步分支 `pearnly/cc-mrerp-operator`
  的 `docs/work/2026-11-02-cc-mrerp-operator.md` 与 `mrerp_operator/draft.py`（只读）。
- 不连任何真实 MR.ERP；mrerp4sme.com 只读也不碰（用户 11-02 定）。用假站点 / 替身。
- 主会话 11-03 改动任务书第 1 条：不用站点配置开关（10-07 定功能不按租户开关），改共用层全局常量
  `pearnly_core.express_operator.contracts.MRERP_OPERATOR_ENABLED = False`；第 2 步执行层合入的那个提交统一改 True，不在任何站点单独开。

## 1. 影响评估（简）

| 项 | 结论 |
|---|---|
| 同类复用 | 出载荷复用 MR.ERP 导入直写同一套准备（`_prepare_mrerp` 的冻结、`pearnly_core.mrerp` 的分流 / 默认值 / 菜单 / 自建主档 / 行解析），不另写一套；派单复用第 1 步纯规则 `route_contracts._decide_chosen`；租约复用第 10 步 `session_lock.lease_account / renew_account / release_account` |
| Frappe 原生 | 只给两个电脑表各加一列（隐藏、只读）`mrerp_users`，原生迁移加列，不写补丁 |
| 按类通用 | 「准备成网页表单要填的值」放共用层 `pearnly_core.mrerp`（ERP 第 6 步直接用），Cowork 只接线 |
| 上下游 / Express / CC | 常量关着：Cowork 行为与 Express 载荷逐字节不变（黄金样本）。开着：MR.ERP 公司的代录单出载荷 18（`operator.target = mrerp` + `mrerp` 块），只派选定且报了 `mrerp` 的电脑；直写执行器跳过代录记录；Express 直写 / 代录不变 |
| 隔离与权限 | 领单只认同一工作空间 / 客户、派给这台电脑、录入人此刻仍有权（账本 `lease` 原有判据）；通道凭据仍按 Express 连接认证，MR.ERP 记录另核归属 |
| 各入口 | 只有「实操」入口（共用层代录 `start` / `answer`）能出 MR.ERP 代录单；对话直写、网页、LINE、定时任务不变 |
| 共用件 | `cc-operator-target`、`cowork.cc.channel`、`cc-desktop`（心跳）、`shared.execution`（session_lock 只调用、不改） |
| 成本 | 不加常驻、不加轮询；开录前服务端登录 MR.ERP 一次（同直写），无新增固定费用 |
| 作废删除 | 代录记录撤回 / 跳过走原有账本状态；账号租约在批次最后一张回执时放、否则 TTL 300 秒过期 |
| 文案 | 不加新文案（复用已有原因码 `account_busy`） |
| 验收 | 见 §6 测试；真站点项列「等岳父线上测试账号」 |
| 待问 | 见 §8 |



## 2. 实现（已写，开关常量默认关）

| 层 | 文件 | 改动 |
|---|---|---|
| 开关 | `pearnly_core/express_operator/contracts.py` | `MRERP_OPERATOR_ENABLED = False`（全平台一处，主会话 11-03） |
| 产品声明 | `pearnly_cowork/hooks.py`、`assistant/operator_source.py::target` | 声明 `target`：常量开着且公司 `adapter == mrerp` 回 `mrerp`，其余（含常量关着）回 `express`。关着时 `products.submit` 带进批次的仍是 `express`，与不声明相同 |
| 交单 | `assistant/tools.py::_submit_one`、`_operator_target` | 常量关着：判断与改动前一字不差（MR.ERP 单照旧 `operator_express_only`）。开着且公司目标 `mrerp`、这张单连接是 MR.ERP：菜单 / 默认值检查之后，先提交（放锁），调 `push/mrerp/operator.stage` 做开录前服务端准备，拿到 `mrerp` 块随批次信息交 `application.submit`，`route_device` 照传；准备失败记在票上、不交；账号被代录占着回 `waiting / account_busy`（已有自动级原因）。公司目标 `mrerp` 而单子连接是 Express 也拦（同一原因码） |
| 出载荷 | `push/application/service.py` | `_prepare_mrerp` 拆出 `mrerp_basis`（直写与代录同一份准备，`lock=False` 给开录前的只读准备用）；直写载荷 / 冻结目标的键与顺序不变。代录：`for_operator({**直写载荷, "mrerp": 块}, 批次, target="mrerp")`（载荷 18），记录带 `route_device`；常量关着、没有块、没有选定电脑、块的连接不是这条 → 按请求无效拦下。`submit`：MR.ERP 代录只 `confirm`（排着等电脑），不 `confirm_server`（不排服务端执行） |
| 开录前准备 | `push/mrerp/operator.py`（新） | 读准备 → 提交 → 账号被代录占着就不登录（会踢掉正在录的电脑）→ `MrErpImport`（同直写账号与短锁）里跑共用层 `operator_draft.stage`；错误按 `ERR_AUTH / ERR_BUSINESS / ERR_TECHNICAL / ERR_SESSION_LOCK` 回码 |
| 共用层准备 | `pearnly_core/mrerp/push/transport.py`、`routing.py`、`operator_draft.py`（新） | 机械抽出：`MrErpImport.ready_rows`（匹配闸 → 导入值 → 科目阀 → 菜单 → 自建对方 → preflight → 自建商品，原 `upload_invoice_batch` 前半）、`note_account_tax`、`routing.group_by_doc_type`（原 `route_and_upload` 分流段）；直写调用顺序、失败行顺序不变。`operator_draft.block` 按 CC 第 2 步 `draft.py` 的形状出块（四种单），`stage` 走同一套分流与准备 |
| 直写执行器 | `push/mrerp/runner.py` | `run` 跳过代录记录（`is_operator`）；`dispatch_next` 查询排掉带 `route_device` 的记录并逐条核载荷（否则排在前面的代录单会卡住同连接后面的直写） |
| 派单 / 领单 | `companion/transport.py`、`companion/mrerp_jobs.py`（新）、`companion/delivery_store.py` | 见 §3 |
| 心跳用户名 | CC `express_operator/credentials.py`（`entries`、`mrerp_users`：只读凭据枚举里的明文用户名，不读密码）、`worker.py::_mrerp_users`、`queue_client.health(mrerp_users=)`；服务端 `route_contracts.stored_users / users_of`、共用通道 `cc_desktop/devices.record_report(users=)` + `machine.py`、Cowork `companion/devices.record_mrerp_users` + `heartbeat.py`；两个电脑表加隐藏只读列 `mrerp_users`（原生迁移加列） | 旧版不报不动、报空照存、离线不改；连接号认不出 / 用户名为空或带控制字符的不记 |

## 3. 派单、领单、重发、回收、回执怎么落到 MR.ERP 推送记录上

- **找单**：`mrerp_jobs.heads(密钥, 电脑)`：同一工作空间 / 客户、`route_device` = 这台、连接是 MR.ERP（不是来领的这把密钥的 Express 连接）、
  排队中、不是结果不明、载荷是 MR.ERP 代录单，按建立先后最多 20 张。`transport.lease` 在 Express 没领到（且这台手上没有未到期的一件）
  时才看它（`_claim_mrerp`），常量关着直接不看。
- **派不派**：`route_contracts.decide(…, Job(target="mrerp"), reachers=[], …)`（第 1 步纯规则）：不是选定的这台 → 跳过；没报 `mrerp` → 跳过、
  单子等它；报了但载荷版本不够 → 转人工 `payload_version_outdated`（同 Express「没有一台支持」）。不看账套可达、不黏住。
- **核归属**：`mrerp_jobs.context`：记录与密钥同工作空间 / 客户、legacy 分支、代数 0，记录的连接是这个工作空间的 MR.ERP 连接，派给这台，
  是代录单；推送上下文与 adapter 用记录自己的连接（账本按连接核锁与权限，与直写同一套）。
- **投递**：`delivery_store.create(..., connection=MR.ERP 连接)` 只放宽记录 / 领取的连接核对；投递行仍挂在来领的密钥的连接上，
  回执认证（`receipt_auth`）原样。MR.ERP 载荷不做账套位置替换，原样交。
- **重发（`_redeliver`）与回收（`_recover`）**：两条查询改按投递记录挂的连接（`d.connection`）找——Express 记录投递连接就是记录连接，
  结果不变；MR.ERP 代录单因此也被这把密钥照 ADR-021 11-02 口径重发回原电脑 / 电脑在场续期 / 不在场转结果不明。重发前先续 / 重租账号，
  租不到先不发。
- **回执（`acknowledge`）**：按记录自己的连接建上下文与 adapter；账本 `ack` 对 `adapter = mrerp` 的目标记 `mrerp_bill_no`、
  `external_docnum`（与直写同一回执形状）。之后 `_after_ack`：MR.ERP 代录单不记 Express 账套登记，改为「这批再没有排着 / 领着的单就放开账号」。

## 4. 账号租约（ADR-026 第 8 条）

- 键 `lock_key(系统网址, 用户名)`；用户名先取这台电脑心跳报的 `mrerp_users[连接号]`，没有用站点连接上存的 `credential_username`；都没有不拦。
- 持有者 `站点:批次号`。领单时 `lease_account`（同持有者即续期）；领取在保存点里，租不到回滚这次领取、单子照旧排着。
- 续期：每次领单、`_recover` 里电脑在场延长领取时。放开：回执后这批在这条连接上没有排着 / 领着的单。电脑不在：TTL 300 秒过期，
  直写恢复；之后同一张重发回原电脑前重新租。
- 开录前服务端准备看到账号被代录占着时不登录（回 `account_busy`）：同批次录到中途作答的那一张要等这批放开账号再交（已知限制，§8）。

## 5. 测试与闸门

### 5.1 第一段（实现，上一个子代理，HEAD `6ef70a19`）

| 项 | 结果 |
|---|---|
| CC 全套 `companion/tests`（暂存 Python 3.12 环境：pytest、httpx、dbf、aenum、PySide6_Essentials，offscreen） | 1450 通过、46 跳过、0 失败（含新增 `test_mrerp_users_heartbeat.py` 4 条） |
| `tests/test_mrerp_cc_login.py`（新增共用通道心跳用户名存法、纯规则） | 17 通过 |
| 受影响离线测试：`test_cc_desktop_channel` 29、`test_cowork_assistant_tools` 230、`test_cowork_companion_lock_retry` 17、`test_cowork_companion_requests` 38、`test_cowork_express_dispatch` 69、`test_cowork_da_tiers` 74、`test_cowork_companies` 183、`test_cowork_folder_intake` 127、`test_cowork_push_*`、`test_cowork_readback` 47、`test_express_operator_shared` 11、`test_express_operator_tasks` 12、`test_companion_contract` 86、`test_cowork_cc_desktop_devices` 71（字段断言加 `mrerp_users`）、`test_cowork_express_preflight`、`test_cowork_companion_reports` | 全过 |

### 5.2 第二段（补测试与闸门，接手的子代理，11-03）

新测试（提交 `5d03a0f2`），两种常量状态都锁：

| 测试 | 在哪跑 | 锁住什么 | 结果 |
|---|---|---|---|
| `tests/test_mrerp_operator_draft.py` + 样本 `tests/fixtures/mrerp/operator-draft-golden.json`（新） | CI（`development.yml` 新步骤）、本机、生产镜像 | **服务端黄金样本**：赊购货品 / 赊购费用 / 赊销价内 / 现销散客带折扣四种单，固定输入 → 固定 `mrerp` 块（逐字符、含键的先后）；每格与导入表同源（供应商 / 客户码、商品码、单号、`SI`+单号、表头默认值与客户档覆盖、税率文字）；页面同算法合计（价外、价内逐行拆税、表头折扣、`นอกระบบ`）；`stage` 与直写同一套分流、每组交 `ready_rows`、自建的码进块、挡下带原因、没验证的模块整组转人工、出错也把模块换回 | 10 通过 |
| `tests/test_cowork_mrerp_operator_dispatch.py`（新） | CI（新步骤）、本机、生产镜像 | 在替身上加载真 `companion/transport.py` 与 `mrerp_jobs.py`：常量关着不查不派；只派选定且报了 `mrerp` 的电脑；旧版 / 只报 Express 的不领不转人工；版本不够转人工；Express 先领；没有可写 Express 账套的电脑照样领自己的；忙着不领；租不到账号回滚这次领取不发；锁被占跳下一张；领取失败回滚、只有确定的拒绝转人工；丢在手里的按记录连接重发、租不到不重发、不能重发转人工；别的电脑过期在场续领取与账号、不在转结果不明、已不归它的不动；回执后放账号、Express 账套登记不碰、`acknowledge` 按记录自己的连接建上下文；`mrerp_jobs` 归属核对 11 种错配、排头查询条件、用户名先后（电脑报的 → 站点存的 → 没有不拦）、租 / 续 / 别的批次与直写等账号、300 秒过期后直写恢复与重租、最后一张回执放账号；**常量开着时 `test_cowork_express_dispatch.Transport` 的 Express 领单用例全部重跑通过** | 46 通过（其中 Express 重跑 23） |
| `tests/test_cowork_assistant_tools.py::MrErpOperatorSubmitTests`（加在原文件，CI 原有步骤） | CI、本机 | `_submit_one`：常量关着 MR.ERP 单照旧 `operator_express_only`、不准备不交（批次写了 `mrerp` 也当 Express）；开着先提交再准备、带 `mrerp` 块与选定电脑交；准备失败记在票上不交；账号被占回等账号不记失败；账本与连接对不上两个方向都拦；直写两种状态都不带选定电脑与代录 | 6 通过（该文件 230 → 236） |
| `tests/test_cowork_mrerp_operator.py`（新，进 `scripts/ci-candidate-checks.sh` 的 offline 列表） | 生产镜像 | 直写执行器 `run` 不领代录记录、直写照旧领；`dispatch_next` 查询排掉带选定电脑的、跳过代录载荷、后面的直写照排；`operator_source.target` 关着一律 Express 且不读公司、开着只 MR.ERP 公司回 mrerp；钩子已声明；`_prepare_mrerp` 直写载荷与冻结目标（含键的先后）两种状态都不变、不带选定电脑，代录载荷带代录标记 / 目标 mrerp / 版本 ≥ 18 / 块 / 选定电脑，关着 / 没块 / 没电脑 / 块的连接不对一律拦；`submit` 代录只确认不排服务端执行；`push/mrerp/operator.stage` 对外请求前先提交、一次登录、账套名取自目录、账号被代录占着不登录、四类错误回码不带密码、挡下回原因；**真 `MrErpImport` + 假 MR.ERP 站点**：代录准备照直写自建客户与商品、单据一张都不导入，块里的单号等于同一张票直写时导入表里的单号，四种单落到各自菜单 | 14 通过 |

闸门与回归（工作树 `5d03a0f2` + 登记表改动，生产镜像 `sha256:ea24fcb9ce7238414bf166bbe439339a06b9f601cd46b8a64dfdd0f4d7c1803d`，
取自 [11-02 发布记录](2026-11-02-release-df57672a.md)，未 SSH 生产）：

| 项 | 结果 |
|---|---|
| **提交 `cb1c93ec` 机械抽取的逐字节证明**：生产镜像里 `tests/test_mrerp_rules_golden.py`（导入表成员级 + 整份文件字节摘要、推送全程每个请求与上传文件；镜像 zlib 1.2.13 与样本记录的相同，整份字节照比） | 3 通过 → 导入文件与推送请求逐字节相同 |
| MR.ERP 直写全链路回归（生产镜像，同第 10 步 §10 那一组）：推送 `test_cowork_mrerp_push` 41、采集 `collector` 15、目录 `catalog` 8、菜单 `menus` 17、黄金样本 3、主档 `masters` 12、文件夹任务 `folder_tasks_mrerp` 8、导入表源码一致 `xlsx_source_parity`（2 跳过，同第 10 步）、共用层边界 `core_boundary` 3、`test_cowork_mrerp_cc_login` 15、`scripts/test-session-lock.py` 5 | 全过 |
| 新测试在生产镜像（Python 3.14）：`test_mrerp_operator_draft` 10、`test_cowork_mrerp_operator_dispatch` 46、`test_cowork_mrerp_operator` 14 | 全过 |
| 全闸门 `gate.sh` | GATE_RESULT |
| 候选离线检查 `cand-local.sh offline`（生产镜像） | CAND_RESULT |
| `ci-candidate-checks.sh` 比 `cand-local.sh` 多出的离线测试 | EXTRA_RESULT |
| `python3 scripts/check-architecture.py` | PASS（0 违规） |
| `python3 scripts/check-copy.py --report`（基线 790dc639） | 新增 2（基线分支的 `account_in_use` / `account_set_unavailable` 三语，见服务端记录 §2）、改动 0、删除 0、拦截 0、标黄 2（两个电脑表隐藏字段的 DocType 标签「MR.ERP Users on This Computer」，隐藏字段只标黄）；本段没有新文案 |
| `python3 scripts/check-impact.py` | 起初失败 3 处：`companion/mrerp_jobs.py`（调 `cc-desktop`、`mrerp-rules`）与 `push/mrerp/operator.py`（调 `mrerp-rules`）没登记。已登记进 `scripts/impact/shared-parts.toml`：`mrerp_jobs.py` 进入口 `cowork.cc.channel` 与 `cowork.mrerp.push` 的 files，`push/mrerp/operator.py`、`pearnly_core/mrerp/push/operator_draft.py` 进 `cowork.mrerp.push`；之后登记表完整（55 个共用件、201 个入口），exit 0 |
| CC 全套 | 本段没改 CC 代码，不重跑（第一段 1450 通过） |
| 本机 cowork-backend 实验栈真实站点验证 | **没测到**（原因：本段上下文到线，按任务书第 7 条先停；要做的见 §6 第 2 条） |

### 5.3 入口实测表（`check-impact.py`，命中 `cc-desktop`、`mrerp-rules`、`cc-operator-target`，要实测 29 个）

常量 `MRERP_OPERATOR_ENABLED = False` 合入时，生产上 MR.ERP 代录这条路不可达；以下「通过」只指离线 / 生产镜像测试，不是业务验收。

| 入口 | 结果 |
|---|---|
| `cowork.cc.channel` | **通过（离线）**：`test_cowork_mrerp_operator_dispatch` 46（含常量开着时 Express 领单 23 条重跑）、`test_cowork_express_dispatch` 在全闸门内；**真站点没测到**（原因：本段没起实验栈；Cowork 推送没有自动冒烟，要测试机 CC 推一张，本任务不动测试机） |
| `cowork.mrerp.push` | **通过（离线 + 生产镜像）**：直写全链路回归全过、黄金样本逐字节相同、`test_cowork_mrerp_operator` 14；真实 MR.ERP **没测到**（原因：不连任何真实 MR.ERP，等岳父线上测试账号） |
| `cc.operator.page` | **没测到**（原因：本段没改前端与共用层代录接口；`tests/test_cc_operator.py` 在全闸门内） |
| `cc.desktop.operator` | **没测到**（原因：本段没改 CC；第一段 CC 全套 1450 通过） |
| `erp.cc.chat-push` | 通过（离线：全闸门内的 ERP 对话直写测试；本任务没改 ERP） |
| `cc.mrerp-login` | **没测到**（原因：本段没改登录；第 10 步实验栈脚本 6 项当时通过，本段没重跑） |
| `cowork.mrerp.catalog`、`cowork.cc.mrerp-setup`、`cowork.connections` | **没测到**（原因：没有可用 MR.ERP 账套、要测试机；`catalog` 8、`menus` 17 在生产镜像通过） |
| `cowork.express.push`、`cowork.cc-chat.push`、`cowork.assistant` | **没测到**（原因：要测试机 CC 与 Express；常量关着时交单判断与改动前一字不差由 `MrErpOperatorSubmitTests` 锁住，`test_cowork_assistant_tools` 236 通过） |
| `erp.cc.connect`、`erp.cc.write`、`erp.cc.masters`、`erp.cc.stock-void`、`erp.cc.companies`、`erp.cc.stock-transfer`、`cc.desktop.write`、`cc.desktop.places` | **没测到**（原因：要测试机 CC 与 Express；共用通道只加心跳用户名一列，`test_cc_desktop_channel` 29 与 `test_mrerp_cc_login` 17 通过） |
| `cowork.line.chat`、`cowork.ocr`、`cowork.execution`、`cowork.tax-documents`、`shared.navigation`、`shared.shell-gate`、`shared.team.members` | **没测到**（原因：只是同一调用方文件里别的函数改了，这些入口的代码路径本任务没碰；要测试机 / 付费识别） |
| `shared.transaction`、`cowork.transaction` | **没测到**（原因：并发压测要测试机；领取在保存点里、租不到回滚由离线测试覆盖） |

## 6. 进度与下一步

已完成：§2–§4 的代码与 CC 端测试（第一段）；§5.2 的测试、CI 接线、生产镜像回归与逐字节证明（第二段）。**还没做**：

1. 第 2 步分支 `pearnly/cc-mrerp-operator` 那一侧的「两边一致」契约测试（不归本任务改，第 2 步合入前补）：读本仓
   `tests/fixtures/mrerp/operator-draft-golden.json` 每个样本的 `block`，喂 `mrerp_operator/draft.py::from_payload`（载荷形状 =
   `{"entry_mode": "operator", "operator": {"target": "mrerp", ...}, "mrerp": block}`），核：每个字段都读得出且值不变；`totals()` 用站点给的
   `totals`（不自己重算覆盖）；`vat_rate` 的四种文字（`7 (แยก)` / `7 (รวม)` / `0` / `นอกระบบ`）在配方里都选得到；`lines[].code/qty/price/store/desc`、
   `defaults` 五个键、`party_code`、`ref_no`、`docnum`、`date` 都落到配方的对应格。块的形状要改时两边同一批改，`--write` 重写样本。
2. 本机 cowork-backend 实验栈真实站点验证（照 `scripts/cowork-mrerp-login-lab-verify.py` 写一份，跑完拆掉）：常量打补丁开着时，
   真 MariaDB 上 `_prepare_mrerp` → `ledger.reserve` → `confirm`（不是 `confirm_server`）对 MR.ERP 代录记录走得通（`ledger.confirm` 对带
   `route_device` 的 MR.ERP 记录是否另有 Express 才有的判据，本段只在替身上核过）；`mrerp_jobs.heads` 的真 SQL 在 MariaDB 上取得到、
   排得掉别的电脑与非代录单；真 Redis 上 `take` / `release_if_done` 与直写短锁互斥；两个电脑表新列 `mrerp_users` 迁移加列、心跳写入。
3. 合入前按「主线集成」变基、本地全闸门、推分支等 Development checks（本任务不推送）。

## 7. 等岳父线上测试账号

- MR.ERP 页面税率选项的文字是否就是导入表的 `7 (แยก)` / `7 (รวม)` / `0` / `นอกระบบ`（块里 `vat_rate` 按这个给）。
- 现销的表头折扣、收款科目由菜单带出是否与导入直写落库一致。
- 销项 `ref_no`（`SI` + 单号）在网页表单上填哪一格、是否必填。

## 8. 遗留问题与要主会话定的事

1. 只有 MR.ERP、没有 Express 连接的工作空间：CC 没有 Cowork 通道密钥（密钥绑在 Express 连接上），领不到 MR.ERP 代录单。要不要给 MR.ERP 连接也发通道密钥。**待定（主会话在定，本任务不做）。**
2. 开录前准备在网页请求里同步做（每张一次 MR.ERP 登录，同直写执行器每条一次）；`_submit_one` 撞写者锁整笔重来时会重新准备一次（自建幂等）。
3. 同批次录到中途作答的单：账号被本批占着时不登录准备，要等本批放开账号再交。
4. 批次停 / 任务撤回：本步靠 CC 对剩下的单回执（最后一张回执放账号）与 TTL 过期；没有新增服务端撤回入口。
5. CC 前端「MR.ERP 登录」按钮本步不做（任务书第 5 条）。
6. （第二段补测试时看到）回执后放账号的判据：`_after_ack` 在回执结果不是「领取中」时调 `release_if_done`，它排掉的是**这一张自己**；
   若这张回执后回到排队（`pending`，例如 CC 回 `waiting_lock` 类结果），本批其实还有这张没录，账号却会被放开，直写可能在 CC 再领到它之前
   登录同一账号、踢掉 CC 网页窗口的会话。下次领单时 `take` 会重新租（租不到就不发），不会写错，但可能多一次被踢。是否改成「这张回到排队时
   不放」由施工方与第 2 步一起定（离线测试 `test_after_the_receipt_the_batch_releases_its_account...` 现锁的是现状：success / pending / manual 都放）。
