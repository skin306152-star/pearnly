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

## 5. 测试与闸门（截至停下时）

| 项 | 结果 |
|---|---|
| CC 全套 `companion/tests`（暂存 Python 3.12 环境：pytest、httpx、dbf、aenum、PySide6_Essentials，offscreen） | 1450 通过、46 跳过、0 失败（含新增 `test_mrerp_users_heartbeat.py` 4 条） |
| `tests/test_mrerp_cc_login.py`（新增共用通道心跳用户名存法、纯规则） | 17 通过 |
| 受影响离线测试：`test_cc_desktop_channel` 29、`test_cowork_assistant_tools` 230、`test_cowork_companion_lock_retry` 17、`test_cowork_companion_requests` 38、`test_cowork_express_dispatch` 69、`test_cowork_da_tiers` 74、`test_cowork_companies` 183、`test_cowork_folder_intake` 127、`test_cowork_push_*`、`test_cowork_readback` 47、`test_express_operator_shared` 11、`test_express_operator_tasks` 12、`test_companion_contract` 86、`test_cowork_cc_desktop_devices` 71（字段断言加 `mrerp_users`）、`test_cowork_express_preflight`、`test_cowork_companion_reports` | 全过 |
| `python3 scripts/check-architecture.py` | PASS（0 违规） |
| 要 Frappe 环境的（`test_cowork_mrerp_push`、`test_mrerp_rules_golden`、`test_cowork_mrerp_cc_login`） | 本机跳过，**还没在生产镜像跑**（候选离线检查未跑） |

## 6. 停下时的进度与下一步（上下文过半，按任务书第 9 条停下）

已完成：上面 §2–§4 的代码与 CC 端测试；服务端只补了心跳用户名的测试。**还没做**：

1. 新测试（建议两份）：
   - 离线 `tests/test_mrerp_operator_draft.py`：服务端黄金样本（四种单固定输入 → 固定 `mrerp` 块 JSON，`operator_draft.block`；
     `stage` 用假 importer 核分流、挡下原因、自建在 `ready_rows` 里）；加进 `.github/workflows/development.yml`。
   - 离线 `tests/test_cowork_mrerp_operator_dispatch.py`：继承 `test_cowork_express_dispatch.Transport` 的替身，换 `transport.mrerp_jobs` /
     `operator_target` / `delivery_store`：常量关着不看 MR.ERP 单；只派选定且报了 `mrerp` 的电脑、旧版 / 只报 Express 不派、版本不够转人工；
     租不到不发且回滚；Express 单优先；重发带记录连接、租不到不重发；回执后放账号。`mrerp_jobs` 本身（归属核对、用户名来源先后、
     租 / 续 / 放 / 过期）用假 `session_lock`。
   - 生产镜像 `tests/test_cowork_mrerp_operator.py`（加进 `scripts/ci-candidate-checks.sh` 的 offline 列表）：`runner.run` / `dispatch_next`
     跳过代录记录；`operator_source.target` 常量关 / 开；`tools._submit_one` 常量关着照旧 `operator_express_only`、开着调准备并带块与
     `route_device`；`service._prepare_mrerp` 代录载荷（entry_mode、target、版本 18、块、route_device）与常量关着拦下；`push/mrerp/operator.stage`
     账号占着不登录、错误回码。
2. 跑 `bash ~/pearnly-devtools/gate.sh <工作树>`、`check-copy --report`（新增两个隐藏字段标签「MR.ERP Users on This Computer」只标黄）、
   `check-impact.py` 入口表、生产镜像候选离线检查（镜像 digest 取最近记录 `ea24fcb9…` 的完整值，不 SSH 生产）、本机 cowork-backend 实验栈验证。
3. 补「两边一致」契约测试的说明（第 2 步分支要补：拿本仓 `operator_draft.block` 的黄金样本 JSON 喂 `draft.from_payload`，核每个字段读得出、
   `totals()` 用站点给的值、`vat_rate` 的文字能在配方里选到）。

## 7. 等岳父线上测试账号

- MR.ERP 页面税率选项的文字是否就是导入表的 `7 (แยก)` / `7 (รวม)` / `0` / `นอกระบบ`（块里 `vat_rate` 按这个给）。
- 现销的表头折扣、收款科目由菜单带出是否与导入直写落库一致。
- 销项 `ref_no`（`SI` + 单号）在网页表单上填哪一格、是否必填。

## 8. 遗留问题与要主会话定的事

1. 只有 MR.ERP、没有 Express 连接的工作空间：CC 没有 Cowork 通道密钥（密钥绑在 Express 连接上），领不到 MR.ERP 代录单。要不要给 MR.ERP 连接也发通道密钥。
2. 开录前准备在网页请求里同步做（每张一次 MR.ERP 登录，同直写执行器每条一次）；`_submit_one` 撞写者锁整笔重来时会重新准备一次（自建幂等）。
3. 同批次录到中途作答的单：账号被本批占着时不登录准备，要等本批放开账号再交。
4. 批次停 / 任务撤回：本步靠 CC 对剩下的单回执（最后一张回执放账号）与 TTL 过期；没有新增服务端撤回入口。
5. CC 前端「MR.ERP 登录」按钮本步不做（任务书第 5 条）。
