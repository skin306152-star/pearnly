# 第八批：生产来源核验与事务接线审查

本记录只覆盖只读生产取证与独立审查；不执行生产写入、部署、真实推送或客户机操作。新平台开工 HEAD 为 `0e5cd94e84f444c2246b57b4838263163c9cdfc3`，工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`；采集前工作树干净。固定旧源码为 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`；下文 `routes/` 路径相对此目录，旧 ERP 模块短路径均相对其中 `services/erp/`，明确标“新”的路径相对新 App 包。

## 开工证据

| 项目 | 已核对事实 |
| --- | --- |
| 环境 | GCP project `pearnly`，region `asia-southeast1` |
| 源码 | `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188` |
| Web 流量 | 100% `pearnly-web-ec2e5c957e6b-s3` |
| Worker 流量 | 100% `pearnly-worker-ec2e5c957e6b-s3` |
| 共同镜像 | `asia-southeast1-docker.pkg.dev/pearnly/pearnly-app/app@sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226` |
| 源码映射依据 | GitHub run `35321281376` 的 completed/success/head_sha，与固定 Git 部署账本及两 revision 的实际 digest 一致；不是只读 revision 名称中的短 SHA |
| 来源采集时间 | `2026-09-20T21:15:04.212705+00:00`（UTC） |
| 来源证据 | `/tmp/pearnly-cowork-migration-20260920/baseline-eighth-start.json` |
| 来源 checksum | `95f1e96fd6560046d7d88db9b90a80b739ec38aca331a619e78d3e9e0935f1f1` |
| 运行配置时间 | `2026-09-20T21:15:27.630487+00:00`（UTC） |
| 运行证据 | `/tmp/pearnly-cowork-migration-20260920/push-runtime-eighth-start.json`，schema v2 |
| 运行 checksum | `1389e17789dbe9416021d16383b880034f9ed847e5a06101549c79d411342983` |
| 端点集合 | Web/Worker 均 60 个；完整 config 集合摘要 `461d6540e21d14f3a41443ad720d5605` |

来源 compare 对照第七批交付 `baseline-seventh-delivery.json`（checksum `2b66ac9fe67b7ba68c1a6780e78ee4b62bf3d034a54c2842f60191aad578d3df`）通过。运行 compare 对照第七批 v2 交付 `push-runtime-seventh-delivery.json`（checksum `93d9019400e052a32f7dcfcd21486cde6a22188260f22b194d85ee272578f199`）返回 `settings_unchanged=true, observations_changed=false`。两个采集器均在采集首尾重读流量；既有证据没有覆盖或修改。

本次重新读取既有采集器的实际实现后执行：来源只读 Git/Cloud Run/GitHub；运行配置使用同一来源证据固定的数字版本 runtime secret，仅在内存解析白名单和数据库连接参数。PostgreSQL 使用 READ ONLY / REPEATABLE READ、8 秒 statement timeout、结束 rollback/close；完整 endpoint config 仅在数据库内计算摘要，未返回配置、凭据、端点 ID、业务路径或客户数据。

结论：第八批可按已声明的本地服务端范围开工。仍不能由此证明正在运行的 Companion 二进制/source SHA、DBF/CDX 外部写者、真实落账或目录关联表/供应商/银行业务数据已完成迁移。完整 config 摘要包含其中保存的心跳和目录快照，变化只代表 config 改变，不能单独解释功能原因。当前生产端点仍为 legacy generation 0；managed 源码存在不代表生产已运行该分支。

## 固定来源契约与新平台最小路径

| 契约 | 固定来源证据 | 新原生接线必须落实 |
| --- | --- | --- |
| 产品与购买资格 | `routes/erp_push_log_routes.py:43-58`；`routes/erp_routes_access.py:13-18` | 会话 actor、当前 Cowork Site、购买功能、角色在每个 RPC 重验；能看历史不自动得到外推或连接管理权限。 |
| 可见连接不等于可管理或可推送 | `routes/erp_endpoints_routes.py:47-71`；`shared_express_access.py:26-65` | 列表输出安全 DTO；连接权限与操作权限分别检查，不把列表里看到的名字当授权令牌，不把原始 config/凭据返回普通用户。 |
| 原有成员目标与记录范围 | `team_access.py:179-218,326-415` | 明确新 Site/公司/客户权限映射；旧 ERP 成员固定分配目标、仅自己创建历史是特定入口语义，不把它无差别扩展成 Cowork 全站授权或直接复制旧用户 UUID。 |
| 预留前重验连接 | `confirmed_push_reservation.py:31-66,197-218` | 连接锁内重读当前 enabled、owner/grant、actor 有效性、绑定 generation；`TargetAccess` 只是纯值，不能接受客户端伪造或复用旧请求的许可。 |
| 记录归属与内部来源 | `confirmed_push.py:223-241` | 以服务器 History 的公司/客户为准，拒绝请求范围不符；拒绝内部 ERP 来源外推。采用受版本控制的历史快照，不能先检查旧版本再以新版本组装。 |
| 目标证据 | `target_catalog_evidence.py:129-239`；`selected_account.py:140-225` | 非默认目标要求最新 succeeded endpoint refresh、对应 revision、fresh head、同 snapshot、active/writable 与程序目录配对。证据读取与 choice 读取来自同一锁内状态，不能用户上传目录自证。 |
| 目录发布 | `target_projection_store.py:101-188,325-425` | 锁连接后更新 head；相同 source hash 不增加内容 revision，仍更新 freshness。快照不可变，旧请求不能引用后来 head 或借旧成功 refresh 掩盖新失败。 |
| 冻结目标 | 新 `targets/freeze.py:16-52,83-118` | 已选 account 必须等于已证明 account；程序目录明确且一致。默认分支仅可使用服务器明确配置根目录；非默认不得用数据目录父级冒充证明。保存的扁平 account_config 不是 `endpoint_with_account_choice` 所需的嵌套 choice 形状。 |
| 并发预留 | `legacy_generation.py:10-36`；`confirmed_push_reservation.py:222-327` | 默认/非默认、同/不同 request key 都要在同连接锁内检查成功、active、unknown，再插入唯一权威预留。幂等 key 唯一索引不能代替同 history/目标的跨 key 防重。 |
| 提交与出站顺序 | `confirmed_push.py:269-286`；`confirmed_push_reservation.py:289-327` | 先提交耐久 reservation 才可执行外部写入；只有准备结果不能叫 queued/success。第八批若没有外部写者，则明确保留“待执行”，不能注册通向真实旧 ERP 的出口。 |
| 结果与未知状态 | `confirmed_push.py:31-137`；`confirmed_push_reservation.py:330-434` | 回执 CAS 只能改原 reservation、固定 source/account/target_intent；失联或结果不明进入人工未知，不能自动重写。日志是成功权威，History 状态只是镜像。 |
| PRN 原历史 | `express_push/prior_doc.py:34-115`；新 `roundtrip/access.py:9-20`、`push/receipts.py:264-283` | 来源键从可信原件/批次取得，当前重验原历史范围；原凭证按同 endpoint/account 查权威成功日志。查询失败不能当从未推送；旧 UUID 没有真实映射时拒绝。 |

最小本地完整路径应是：专用 POST 读取当前 actor/购买/角色 → 校验并锁住连接与 History → 锁内重读版本、授权、目录 head/refresh/snapshot → 冻结一致目标和明确上下文 → 预检 → 同锁下防重/未知结果检查 → 创建原生预留及安全回读。纯准备函数、目录导入函数与原生 CRUD 本身都不构成这个路径。

## 不能遗漏的差异与验证场景

1. **不是所有旧 legacy 入口都有耐久预留。** `confirmed_push.py:269-286` 只对非默认且 `proof_required` 的选择走 reservation；默认 legacy 仍有 `:305-310` 先查成功后直接执行的旧路径。若新平台统一使用原生原子预留，这是明确的安全修复，不能声称只是逐字保持旧行为。
2. **MariaDB 的旧事务快照不是当前授权/防重状态。** 第八批应以两个真实连接建立 RR 快照后并发提交，验证等待连接锁的一方使用 current read 重查 History、连接、head、成功/active/unknown；仅跑串行同 key 重放不足以证明不会建两份。
3. **配置、主数据与证据各有归属。** 切换账号后旧科目和目录缓存必须清除；上下文里的公司税号、映射、供应商/银行证据、prior_docnum 必须与这次 actor/公司/客户/目标一致。不得把一份通过预检的 JSON 当跨记录、跨连接的可复用许可。
4. **原生旁路必须与业务入口同边界。** 连接、快照、预留的受保护字段仅由真正服务端 capability 写入；`ignore_validate`、JSON flags、DocShare/everyone、原生 list/get/export/Version 不得泄露或伪造授权、凭据、快照来源、成功回执。已有 History/File 的守卫不会自动覆盖新 DocType。
5. **恢复不是重新认定“未写”。** 异常、超时、任务取消、权限撤销后的恢复只能读原记录并重新校验当前范围；未知结果不得普通重试；成功记录不能被迟到失败 ACK 降级。不同目标的成功日志不能用于 PRN 原凭证，也不能让日志展示折叠隐藏的旧 success 失去防重效力。

本节是接线要求与风险清单，不冒称第八批原生实现或对应现场测试已经完成。开工状态只有上一批的纯 targets/push 合同；本轮新增业务代码由 root 实现并统一验证，本审查不修改他人文件、不启动或迁移 Site。

## 交付复核

2026-09-20 UTC 在 root 明确通知后重新执行既有只读采集器，输出新文件，没有覆盖 start 或第七批证据。

| 项目 | 第八批 delivery 证据 |
| --- | --- |
| 来源采集时间 | `2026-09-20T21:43:55.689011+00:00` |
| 来源文件 | `/tmp/pearnly-cowork-migration-20260920/baseline-eighth-delivery.json` |
| 来源 checksum | `61ab9d88c93942de9a42bbedd1e5bd7d2a0f62a1e1f726c6928bdfe9b1e1e640` |
| 运行配置采集时间 | `2026-09-20T21:44:20.181640+00:00` |
| 运行配置文件 | `/tmp/pearnly-cowork-migration-20260920/push-runtime-eighth-delivery.json` |
| 运行 checksum | `54e821dd71557b0360c96d546ab9c513512526c9093ae344a60a2800de6eb699` |

`cowork-migration-baseline.py compare` 对本批 start/delivery 返回 PASS；Web/Worker 仍承接相同流量 revision、镜像 digest、源码 SHA 与不可变配置引用。`cowork-push-runtime-baseline.py compare` 返回 `settings_unchanged=true, observations_changed=false`。来源仍为上文 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`；完整端点 config 摘要与观察投影在已记录范围内均未漂移。两次采集仍执行首尾流量核对、固定 secret 数字版本与只读数据库事务；没有生产写入、部署或客户机访问。

该结论只证明采集范围内的线上来源没有变化，不能代替新代码的事务、权限、真实 ERP 写入或用户验收。交付采集发生在本批最后的本地修复期间；本地候选 SHA、最终测试数量与提交状态由主任务记录绑定，不把尚未完成的修复写成已通过。

## 本批接线审查记录

只读检查了 `push/application/{service,provenance,api}.py`、`push/native/{service,contracts,documents}.py`、`connections/{service,access,controller,model}.py` 与相关纯合同。PRN 来源链实际使用 Source 的不可变 manifest、绑定 History、唯一 saved OCR Batch 及冻结 result 交叉核验；可编辑 `pages.fields.history_id` 不控制原凭证查询。主体使用 Workspace 税号，Client 保持买方范围；Express 投影只取首个原件页，不再次合并已汇总的商品行。

审查发现并已在代码中复核的修复：

- `express_prior_number` 独立保留旧防重查询的 `express_docnum` / `meta.docnum` 回落口径，显示单号合同保持独立。曾使用实际纯函数做零 IO 合成断言，证实旧接线会漏掉仅 metadata 有单号的成功回执。
- 精确 account preparation head 优先于 endpoint head；存在 pending 精确 head 时不回落旧 endpoint 包，避免双 head 误报或使用过期准备包。
- 公司税号改用 current read，并冻结进预留的证据；专用 RPC 在本地事务边界最多尝试三次，内部服务不隐式重试整个调用方事务。
- 空 account context 仅供请求重放查询；reserve、document/business identity 与成功查询仍要求非空账套。ERP 总开关在 activate/lease 的 dispatch 边界再次检查，ACK 接收独立于该开关。

最后一轮进一步报告了以下三个组合问题。随后已只读复核实际修复，并用实际函数 AST 运行零 IO 定向场景；这些验证不替代 root 正在执行的真实 Site 测试：

1. **冻结 context 绑定已修复。** `_dispatch_record` 在 activate/lease 两处同时校验预留原 actor、History version、Connection revision、branch 与 generation。真实 AST 合成测试确认更新 actor/任一版本/branch-generation 均拒绝；正常原 context 通过。ACK 不调用这道新派发守卫，保留旧 attempt 合法迟到结果入口。
2. **当前目录撤销已修复。** activate/lease 两处 `_dispatch_target` 重验原 proof 身份、最新 head/refresh/snapshot 与冻结目标，并拒绝当前目录显式 inactive/nonwritable；不会替换冻结 payload。真实 AST 加实际纯 `freeze_target` 验证正常证据通过，active/writable 撤销均拒绝。
3. **撤权后的 neverdispatched 替换已修复。** `_supersede_pristine` 在当前新请求授权及目标校验、连接/记录锁内，把已过期版本或明确失权原 actor 的 prepared 改为 cancelled，原因 `push.preparation_superseded`，保留原 payload 与 actor、以 modified_by/时间记录操作人。它严格要求 `result_unknown=0`、`fence=0`、无 active_attempt，另当前读确认没有任何 Attempt 行；AST 验证 pending/leased/unknown/fence/active_attempt/已有 Attempt 均不被替换。History/Connection 版本相同时，会对已锁 Connection 调用 `connections.access.require_connection(..., actor=row.actor)`，以 current read 复核原用户启用状态、角色、公司/客户授权与连接 allow-list，仅捕获 `frappe.PermissionError` 为明确失权。追加实际 AST 验证：原授权仍有效不替换，明确 PermissionError 才替换，数据库/其它异常原样上抛且记录不变。这也覆盖原先仅禁用用户或撤角色/公司授权而版本不变的缺口。

至此，本轮三项审查问题在限定源码与零 IO 定向复核范围内关闭，没有遗留确定的阻断项。真实 Site 的最新 23 项回归由 root 统一执行，本记录不把尚未收到的运行结果写成通过；生产来源 compare 与外部业务结果仍按前述边界分别报告。

对当前代码的其它限定检查未发现新的应用层不一致：失联转 unknown 保留写者锁、迟到 ACK 校验 attempt token/fence、成功回执不被迟到失败降级、同 actor/目标的跨 History 发票号与卖方成功防重均有实际实现入口。本审查没有启动 Site、执行本地数据库写入或真实 adapter；最终现场测试由 root/实现方统一执行，不能把静态结论当作已完成外部推送。


主任务现场结果补记：最后三项修复已纳入23项真实Site账本测试，7.970s、退出0；连接21项、应用18项亦通过。随后仅补ACK响应字段的固定源码一致性，最终数量与结果以本任务主记录为准。源码审查、合成Site验证和外部业务验收仍分层记录。
