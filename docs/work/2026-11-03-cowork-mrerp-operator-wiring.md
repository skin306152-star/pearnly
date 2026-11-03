# 施工记录：Cowork 的 MR.ERP 代录服务端接线 + 账号租约（ADR-026 第 3 步，Cowork 部分）

- 分支 `pearnly/cowork-mrerp-operator-wiring`，工作树 `.claude/worktrees/agent-ab04b9b27e17c4632`，基线 `8efa897a`；11-03 按主会话要求
  `git rebase --onto ddc9edb63 8efa897a` 变基到集成分支 `pearnly/mrerp-operator-server` 的 `ddc9edb63`（含 ERP 直写下沉 `2c733edd`）
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
| 全闸门 `gate.sh`（变基前，`5d03a0f2`） | 172 步通过、1 步失败：`check-i18n --strict` 拦下 `operator_draft.py` 的税率泰文（4 处）；登记进 `keep-cjk.tsv` 后 exit 0（变基后复跑见 5.2b） |
| 候选离线检查 `cand-local.sh offline`（生产镜像，变基前） | exit 0，72 个测试脚本全 OK |
| `ci-candidate-checks.sh` 比 `cand-local.sh` 多出的离线测试（同法补跑） | MR.ERP 6 个已在上行；其余 19 个文件 140 条 + 3 个模块 22 条全过 |
| `python3 scripts/check-architecture.py` | PASS（0 违规） |
| `python3 scripts/check-copy.py --report`（基线 790dc639） | 新增 2（基线分支的 `account_in_use` / `account_set_unavailable` 三语，见服务端记录 §2）、改动 0、删除 0、拦截 0、标黄 2（两个电脑表隐藏字段的 DocType 标签「MR.ERP Users on This Computer」，隐藏字段只标黄）；本段没有新文案 |
| `python3 scripts/check-impact.py` | 起初失败 3 处：`companion/mrerp_jobs.py`（调 `cc-desktop`、`mrerp-rules`）与 `push/mrerp/operator.py`（调 `mrerp-rules`）没登记。已登记进 `scripts/impact/shared-parts.toml`：`mrerp_jobs.py` 进入口 `cowork.cc.channel` 与 `cowork.mrerp.push` 的 files，`push/mrerp/operator.py`、`pearnly_core/mrerp/push/operator_draft.py` 进 `cowork.mrerp.push`；之后登记表完整（55 个共用件、201 个入口），exit 0 |
| CC 全套 | 本段没改 CC 代码，不重跑（第一段 1450 通过） |
| 本机 cowork-backend 实验栈真实站点验证 | **没测到**（原因：本段上下文到线，按任务书第 7 条先停；要做的见 §6 第 2 条） |

### 5.2b 变基到 `ddc9edb63` 之后复跑（HEAD 见回报）

- 冲突只有 `scripts/ci-candidate-checks.sh` 的 offline 列表末行（ERP 加 `tests/test_erp_mrerp_golden.py`、本任务加 `tests/test_cowork_mrerp_operator.py`），
  两条都留。`1bc9b09e`（原 `cb1c93ec` 抽取直写准备段）落在 `pearnly_core/mrerp/push/transport.py`、`routing.py`，与 ERP 下沉的
  `execute.py` / `menu_read.py` / `collect.py` / `user_data.py` 不重叠，自动合并；`8db46b1c`（原 `fa6d7a8d`）与原提交只差 `runner.py` 的导入上下文，逻辑不变。
- 生产镜像（同一 digest，五个 App 都挂工作树源码）：推送 41、采集 15、目录 8、菜单 17、`test_mrerp_rules_golden` 3、**`test_erp_mrerp_golden` 5**、
  主档 12、文件夹任务 8、导入表源码一致（2 跳过）、共用层边界 3、`cc_login` 15、`test-session-lock.py` 5、本任务新测试 10 / 46 / 14 —— 全过；
  `ci-candidate-checks.sh` 比 `cand-local.sh` 多出的 19 个文件与 3 个模块（22 条）全过。
- 本机离线：`test_mrerp_operator_draft` 10、`test_cowork_mrerp_operator_dispatch` 46、`test_cowork_assistant_tools` 236、`test_cowork_express_dispatch` 69、
  `test_mrerp_cc_login` 17、`test_express_operator_shared` 11 全过；`check-architecture` PASS（0 违规）；`check-impact` 登记表完整 exit 0；
  `check-i18n --strict` exit 0（`operator_draft.py` 的税率泰文登记进 `scripts/i18n/keep-cjk.tsv`，属写给外部系统的数据）；`check-copy` 新增 2、拦截 0、标黄 2。
- 全闸门 `gate.sh`：174 步全过（exit 0）；候选离线 `cand-local.sh offline`（生产镜像）：exit 0，72 个测试脚本全 OK。

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

1. 只有 MR.ERP、没有 Express 连接的工作空间：CC 没有 Cowork 通道密钥（密钥绑在 Express 连接上），领不到 MR.ERP 代录单。要不要给 MR.ERP 连接也发通道密钥。**主会话 11-03 已定：要做（选 A，MR.ERP 连接也能登记 CC 通道），待做，留给下一个子任务。** **11-03 已做（常量关着），见 §9.1。**
2. 开录前准备在网页请求里同步做（每张一次 MR.ERP 登录，同直写执行器每条一次）；`_submit_one` 撞写者锁整笔重来时会重新准备一次（自建幂等）。
3. 同批次录到中途作答的单：账号被本批占着时不登录准备，要等本批放开账号再交。
4. 批次停 / 任务撤回：本步靠 CC 对剩下的单回执（最后一张回执放账号）与 TTL 过期；没有新增服务端撤回入口。
5. CC 前端「MR.ERP 登录」按钮本步不做（任务书第 5 条）。
6. （第二段补测试时看到）回执后放账号的判据：`_after_ack` 在回执结果不是「领取中」时调 `release_if_done`，它排掉的是**这一张自己**；
   若这张回执后回到排队（`pending`，例如 CC 回 `waiting_lock` 类结果），本批其实还有这张没录，账号却会被放开，直写可能在 CC 再领到它之前
   登录同一账号、踢掉 CC 网页窗口的会话。下次领单时 `take` 会重新租（租不到就不发），不会写错，但可能多一次被踢。是否改成「这张回到排队时
   不放」由施工方与第 2 步一起定（离线测试 `test_after_the_receipt_the_batch_releases_its_account...` 现锁的是现状：success / pending / manual 都放）。
   **11-03 已修，见 §9.2。**

## 9. 选 A：MR.ERP 连接也登记 CC 通道 + 回执放账号修复（11-03，第三段子代理）

基线 `38899e12`。同挂全局常量 `MRERP_OPERATOR_ENABLED`（仍是 False），关着时生产零变化。

### 9.1 任务一：MR.ERP 连接也是 CC 的写账通道（选 A）

原状：MR.ERP 连接本来就能给电脑发密钥（`HELPER_ADAPTERS`），但只读文件夹——认证后心跳 / 领单 / 回执一律 403
（`http_auth` 的 `FOLDER_ONLY_ADAPTERS`）；CC 端 MR.ERP 成员（`kind = folder`）不进写账线；装了 Express 的电脑在没有 Express 连接的
工作空间，非管理员登记回 `no_connection`。所以只有 MR.ERP 的工作空间 CC 领不到 MR.ERP 代录单。

| 层 | 文件 | 改动（常量开着才生效） |
|---|---|---|
| 认证 | `companion/http_auth.py` | `folder_only_allows(operation)`：常量开着时 MR.ERP 密钥也放行 `heartbeat / lease / ack`；关着与原来的 `FOLDER_ONLY_ALLOWED` 一字不差 |
| 心跳 | `companion/heartbeat.py::_receive_operator` | MR.ERP 连接的报到只收 `offline / device / companion_version / max_payload_version / operator_targets` 进 `plan_report`，记报到时间、电脑行（版本、目标）、`mrerp_logins` / `mrerp_users`；**不进** `collector.ingest`（账套采集）、不写连接上报记录；回复只有 `connected` 与 `companion_latest`。常量关着走到这里也 403（双保险） |
| 领单 | `companion/transport.py` | `_operator_channel(credential)`（常量开着且密钥连接是 MR.ERP）：只领 `_claim_mrerp`（`mrerp_jobs.heads(own=True)` 只看这条连接），不进 Express 排头、不领主档读取；`_redeliver` / `_recover` 把这种密钥手上的记录按 MR.ERP 代录单处理；`_row_context` 对 MR.ERP 代录单一律按 `mrerp_jobs.context` 核（派给这台、是代录单），常量关着不看载荷 |
| 回执 | `transport._after_ack` | 判据改成 `mrerp_jobs.is_job(row)`（原来还要求记录连接 ≠ 投递连接，经 MR.ERP 密钥领的会误走 Express 账套登记）；Express 记录从来不是 MR.ERP 代录单，走原来那条 |
| 登记 | `companion/credentials.register` | 常量开着、没有 Express 连接、装了 Express、**非管理员**：连 MR.ERP 主连接（原来 `no_connection`）；管理员照旧新建 Express 连接；有 Express 连接时取到的仍是它 |
| CC 端 | `companion/config.polls_queue`、`gui_tray._spawn_worker`、`worker._operator_heartbeat` | MR.ERP 连接的成员（`kind = folder` 且不是 Express 池 `pool`）只在这一版会代录 MR.ERP（`OPERATOR_TARGETS` 带 `mrerp`，第 2 步）时进写账线；进了之后心跳只报版本、目标、本机授权的 MR.ERP 连接与用户名，不扫 Express。**这一版 `OPERATOR_TARGETS = ("express",)`：CC 行为不变** |

同机两条连接（写进 [ADR-026](../adr/0026-operator-targets.md) 第 5 条补充，2026-11-03 主会话审定）：**各一套密钥**，不合成一套。理由：
登记只换「这条连接上这台电脑」那一把，两把互不吊销；电脑行上报的是同一台电脑的同一份（版本、目标、用户名），不来回改；推送记录领取锁
只让一把领到同一张，`_busy` 按整台电脑查保证每台同时一件，账号租约持有者是批次，哪把领都续同一份；投递 / 重发 / 回收 / 回执跟着投递挂的
那把走。共用一套要么改 Express 工作空间的登记（违背约束 1），要么让一把密钥跨连接认证、动认证与吊销根基。

已有 Express 连接的工作空间一个字节不变的证据：常量关着时认证、心跳、登记、领单、回执的分支全部短路在常量上（`folder_only_allows`、
`_operator_channel`、`register` 的新分支都先看常量；`_row_context` 常量关着不读载荷）；`test_cowork_express_dispatch` 69 条照过、
`ExpressUnchangedWithMrErpOn` 常量开着重跑 Express 领单 23 条照过；Express 密钥的心跳两种状态都进账套采集
（`test_cowork_mrerp_operator_channel.test_express_keys_keep_the_original_report_in_both_states`）；登记原有 7 条照过。

### 9.2 任务二：回到排队的这一张也算这批还有单

`mrerp_jobs.release_if_done` 去掉 `AND name<>%s`：在回执写好新状态之后按同一事务读，这一张回到 `pending` 就算本批还有单、账号不放；
存上 / 转人工 / 撤回（都不在 `pending / leased`）且本批别无他单才放。原来锁现状的测试改名为「回执落定后交 `release_if_done` 判」，
替身查询改成照查询条件回（不在替身里代判排不排掉自己）；新加反例 `test_a_job_sent_back_to_the_queue_keeps_its_batchs_account`。
**临时把 `AND name<>%s` 加回去核过：新反例与改过的 `test_the_last_receipt...` 两条失败，还原后全过。**

### 9.3 ERP 一侧核对（ADR-025）

- **不存在同样的阻塞**：共用通道 `pearnly_core.cc_desktop.devices.register` 用 ERP 账号按「站点 + 电脑」发密钥，不挂在任何 Express
  连接上；心跳（`machine.heartbeat` → `devices.record_report`）照记版本、`operator_targets`、`mrerp_logins` / `mrerp_users`，不要求 Express。
  所以没有在共用通道一侧改代码。
- ERP 做 MR.ERP 代录时要接的（随 ERP 第 6 步，本任务不做）：
  1. 领单：`cc_desktop/jobs.py::_claim` / `_redeliver` 只按 `books.writable(device)`（Express 账套可达）取排头、`_job` 按 Express 账套写法
     换载荷、`writing_key = book_key` 当账套写者锁——MR.ERP 代录单要另走一条「只派选定电脑、不看可达、不换写法、不占账套锁、租账号」
     的领取（同 Cowork `_claim_mrerp`）。
  2. CC 端：没有 Express 数据根的电脑配对 ERP 站点（`pcc_` 码）时，`pairing_flow.run` 走 `pair_folder(pool=True)`，它只认 `exp_` 码，
     回 `pair_err_token_format`——没装 Express 的电脑现在加不进 ERP 站点。要一个不带 Express 账套的 ERP 成员（并让 `folder_worker`
     跳过共用通道成员，ERP 没有文件夹同步），再经 `polls_queue` 进写账线。
  装了 Express 的电脑连 ERP 站点今天就能登记、心跳（Express 成员），缺的只是第 1 项。

### 9.4 测试与闸门（代码与测试在 `7d446f9f`，本段只读写离线 / 生产镜像，没起实验栈）

| 项 | 结果 |
|---|---|
| `tests/test_cowork_mrerp_operator_dispatch.py`（CI 原步骤） | 46 → **73** 通过：新 `MrErpOwnChannel`（MR.ERP 密钥只领自己的、不进 Express 排头与主档读取、派单规则照旧、租不到不发、丢了的按自己的密钥重发、别的电脑过期的续 / 转结果不明、回执放账号不碰 Express 登记，另继承 `MrErpLease` 全部用例）；`Jobs` 加 `heads(own=True)` 只看本连接、回到排队的这一张不放账号 |
| `tests/test_cowork_mrerp_operator_channel.py`（新，生产镜像，进 `ci-candidate-checks.sh` offline） | 4 通过：常量关着 MR.ERP 密钥报到 403、什么都不记；开着只记报到 / 电脑行 / 登录清单 / 用户名、不进账套采集、补丁里没有账套科目所选；下线只记下线；Express 报到两种状态都走原路 |
| `tests/test_cowork_companion_register.py`（生产镜像，原在 offline 列表） | 10 → **13** 通过：只有 MR.ERP 的工作空间常量关着照旧 `no_connection`；开着非管理员连 MR.ERP 主连接；管理员两种状态都照旧新建 Express 连接 |
| `tests/test_cowork_companion_http_auth.py`（CI） | 加 `folder_only_allows` 两态：关着心跳 / 领单 / 回执一律不放、开着放；文件夹等四张表两态都放 |
| `tests/test_companion_contract.py` | 断言改读 `folder_only_allows`（第一次全闸门因旧字面断言失败 1 条，改后过） |
| `test_cowork_express_dispatch` | 69 通过（未改） |
| CC 全套 `companion/tests`（ccvenv，offscreen） | **1464 通过、46 跳过、0 失败**（新 `test_mrerp_member_channel.py` 6 条：这一版只读成员不进写账线；会代录 MR.ERP 的版本让 MR.ERP 成员进、Express 池成员不进；托盘按 `polls_queue` 建；MR.ERP 成员心跳只报 5 项、不扫 Express、到点才报、不抛；Express 成员走原心跳） |
| 反例核验（任务二） | 临时加回 `AND name<>%s`：2 条失败；还原后 73 全过 |
| 全闸门 `gate.sh`（`7d446f9f`） | **174 步全过**（exit 0）；此前一次在 `172ed7ed` 上失败 2 步：`test_companion_contract` 旧断言、本记录文末多一空行，均已改 |
| 候选离线 `cand-local.sh offline`（生产镜像 `sha256:ea24fcb9…`，代码 `172ed7ed`，之后只改了测试与文档） | exit 0，72 个测试脚本全 OK |
| `ci-candidate-checks.sh` 比 `cand-local.sh` 多出的（同法补跑） | 28 个文件全过（含新 `test_cowork_mrerp_operator_channel` 4、`test_cowork_mrerp_operator` 14、MR.ERP 直写一组、ERP 一组）+ 3 个模块 22 条全过 |
| `python3 scripts/check-architecture.py` | PASS（0 违规） |
| `python3 scripts/check-copy.py --report`（基线 `790dc639`） | 新增 2、改动 0、删除 0、拦截 0、标黄 2，与上一段相同（都是基线分支的）；**本段没有新文案** |
| `python3 scripts/check-impact.py` | exit 0，登记表完整；命中 `cc-desktop`（10 个入口）、`mrerp-rules`（4）、`cc-operator-target`（5），清单 34 个入口 |

入口实测（常量关着合入时生产上这条路不可达；「通过」只指离线 / 生产镜像测试，不是业务验收）：

| 入口 | 结果 |
|---|---|
| `cowork.cc.channel` | **通过（离线 + 生产镜像）**：上表派单 73、报到 4、登记 13、认证；**真站点没测到**（原因：没起实验栈，任务书定之后统一做；要真 MariaDB 跑 `heads(own=True)` 的 SQL、MR.ERP 密钥经真 Frappe 钩子报到 / 领单 / 回执） |
| `cowork.transaction` | **没测到**（原因：并发要实验栈；新分支都在原有保存点与 `conflicts.retried` 里，没有新的锁顺序：报到仍是 连接 → 密钥 → 电脑行） |
| `cc.desktop.write`、`cc.desktop.places`、`cc.desktop.bridge`、`cc.desktop.requests` | **通过（离线）**：CC 全套 1464；**实机没测到**（原因：这一版 `OPERATOR_TARGETS` 不带 `mrerp`，CC 行为不变；要测试机） |
| `cowork.mrerp.push`、`cowork.mrerp.catalog`、`cowork.cc.mrerp-setup`、`cowork.connections`、`cc.mrerp-login` | **通过（生产镜像）**：MR.ERP 直写一组与 `cc_login` 15 全过；真实 MR.ERP **没测到**（原因：不连任何真实 MR.ERP） |
| `cowork.express.push`、`cowork.cc-chat.push`、`cowork.assistant` | **通过（离线）**：`test_cowork_express_dispatch` 69、常量开着 Express 重跑 23；**实机没测到**（原因：要测试机 CC 与 Express） |
| `erp.*`（`cc.connect / write / masters / stock-void / companies / stock-transfer / line.chat / masters.scope / entry.choices / execution / provisioning`）、`shared.transaction` | **没测到**（原因：本段没改 ERP 与共用通道代码，ERP 一组离线测试在候选补跑里全过） |
| `cowork.line.chat`、`cowork.ocr`、`cowork.execution`、`cowork.tax-documents`、`shared.navigation`、`shared.shell-gate`、`shared.team.members`、`cc.operator.thread` | **没测到**（原因：本段改的函数不在这些入口的路径上；同一调用方文件命中） |

### 9.5 遗留

1. 实验栈真站点验证（并入 §6 第 2 条一起做）：真 MariaDB 上 `heads(own=True)`；MR.ERP 密钥经真 Frappe 机器钩子报到、领单、回执（常量打补丁开着）；
   同一台电脑两把密钥同时够得到同一张时只一把领到。
2. CC 托盘：MR.ERP 成员进写账线后，它的状态会由写账线与文件夹线程两边报（`_on_status` / `folder_status`），托盘显示谁为准随第 2 步
   打开 `mrerp` 目标时一起核。
3. Express 密钥领走的 MR.ERP 代录单，若这台电脑后来删掉了 Express 成员，那张的投递挂在 Express 连接上、只有那条连接上来领的密钥会回收
   （同 Express 记录现有口径），账号租约按 TTL 过期不受影响。
4. ERP 第 6 步要补的两件见 §9.3。
