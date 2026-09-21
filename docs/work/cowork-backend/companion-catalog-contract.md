# Companion 目录采集与刷新合同（第九批设计）

本文件只说明 Companion 报告、目录投影和刷新持久化的模块合同。整体迁移进度归[工作包记录](../2026-09-20-cowork-backend-migration.md)，已实现的原生连接能力归[连接合同](connections-native.md)。

**状态：只读设计，尚未实施。** 本次审计的新树基线为 `e23b17afa0e881d2c89de7fe930c17d26358c3f5`。第九批源码核验仍为下面的固定 SHA，但完整运行配置摘要出现漂移，第九批交付复核仍保留差异；不能将第八批 gate 或本次源码相同当作第九批运行配置 gate 通过。本目录域在 gate 确认前只保存合同，不写采集业务实现、测试或迁移结构；另行放行的凭据底座见[身份合同](companion-identity-contract.md)。

## 固定来源与哈希

固定源码 SHA：`ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。本机只读快照为 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`，下表路径均相对此 SHA 的仓库根目录；换机时应从该 SHA 重建对应源码并核对哈希。

下列五文件已在本次只读审计实际计算 SHA256，后续差分器必须先核对完整文件哈希，再提取纯函数，不能导入旧应用或触发其数据库初始化。

| 来源文件 | SHA256 |
| --- | --- |
| `services/erp/express_target_projection.py` | `04cc019472c3bcdf5a776507c24ece1cd29f920f3062f64409a2a9c6008dc478` |
| `services/erp/express_push/agent_reporting.py` | `c1ff3a33724ffb302c94e7c4ee44a1ecd7ba7753ca71c35f72a09c3247bf3a65` |
| `services/erp/express_push/agent_account_sets.py` | `4a8861d46e748349e0a6b24e1b79b3d6ee404d6426505285476f5d9f2f4bb5a8` |
| `services/erp/target_refresh.py` | `33eb4ee62e0d762965c83adcbd6c100edcc50f1649c7aa94d44d1099ba0a53ff` |
| `services/erp/mrerp_refresh_worker.py` | `a45d49502bac2b971d74e5133ec51333e11c9759c04fcd808c24bb394ed61c43` |

其他调用证据来自同一固定 SHA：`routes/erp_agent.py`、`services/erp/express_push/agent_store.py`、`services/erp/target_projection_store.py`、`services/erp/mappings_store.py`、`services/purchase/supplier_posting.py`、`services/erp/express_push/preflight.py`、`services/erp/express_push/bank_evidence.py`、`services/recon/bank_recon_v1_store.py`。它们是边界参考，不代表本批授权整包搬迁。

## 来源调用链与新平台落点

| 来源函数及行号 | 实际职责 | 当前新平台与计划落点 |
| --- | --- | --- |
| `routes/erp_agent.py:117` `_run_agent_heartbeat` | legacy token 认证；offline 短路；在线、报告和选择更新；目录投影；返回刷新领取 | Companion 编排完成内部认证和连接/凭据加锁后调用 connections 内部收集器；机器 HTTP 尚未接，不反向导入认证 |
| `routes/erp_agent.py:69` `_managed_heartbeat` | managed 身份与 live profile 处理后发布投影 | 本批最小段只设计 legacy；不能以 generation 字段存在声称 managed 身份已迁 |
| `agent_reporting.py:33/108/174/192/279` | 科目、存货科目组、商品客户、指纹、所选账套净化 | 计划提取无 IO 的报告合同；日期由调用者注入 |
| `agent_account_sets.py:34` `_sanitize_account_sets` | 旧 config 镜像目录净化 | 与 projection 选项净化分别保留；不能合并成一个会改变旧输出的净化器 |
| `express_target_projection.py:23-238` | 选项、账套身份、所选兼容、主档、schema/capabilities、观察值构造 | 计划复用这些纯规则；标准投影继续调用现有 `targets.projection.normalize_projection` |
| `express_target_projection.py:241` `ingest_express_heartbeat` | 区分缺席、空扫描、独立有效组件和刷新回报；锁 endpoint 后发布 | 计划 `connections/collector` 原生编排；不能把 DB/事务放进纯 helper |
| `target_projection_store.py:101/259` | 不可变快照发布、同内容去重、错误状态保留旧快照 | 现有 Connection/Snapshot 可复用；需区分普通报告发布与带租约的刷新完成 |
| `target_refresh.py:27/272/383` | 请求合并或替换、领取、过期、完成 | 扩展现有 `connections.service.request_refresh`，增加持久 lease 与完成防线；不使用 OCR Core Work |
| `mrerp_refresh_worker.py:32/133` | 外部读取与短事务发布分离；租约、配置指纹、重跑检查 | 只作刷新并发合同参考，本批不迁 MRERP 网络采集器 |

当前代码的复用入口为 `connections.service.request_refresh:198`、`install_snapshot:267`、`read_for_push:328`、`preparation_for_target:361`，以及 `targets.projection.normalize_projection:245`。这些行号绑定本次新树基线。

## Companion 报告与服务端资料不能混用

### Companion 能报告的事实

| 输入 | 净化或消费语义 |
| --- | --- |
| `account_sets[]` | code/name/company/tax_id/path/root/root_label/row/writable；每账套 mapping 只收六个已知科目键。旧 config 镜像限制 50 条，projection 另走其自己的规范 |
| 所选账套整组 | `account_set/account_dir/account_company/account_set_row/express_root`；没有非空 account_set 不更新；其余只合并非空字段，row 可转整数 |
| 顶层 `mapping` | revenue_acc/ar_acc/vat_output_acc/fallback_acc/ap_acc/vat_input_acc；仅合并非空码，截断至 40 字符。来源称本地小助手为选择的权威来源 |
| `accounts[]` | code/name/type，code 必需；旧镜像最多 3,000 条，字段截断至 120 字符 |
| `catalog.products[]` | code/name/kind；旧镜像 code 或 name 至少一个非空，最多 20,000 条，字段截断至 200 字符 |
| `catalog.customers[]` | code/name/tax_id/kind/branch；同上数量及长度规则 |
| `catalog.fingerprint` | stock_master_count/stcrd_lines/stcrd_lines_moving_stock，保留可转非负整数；净化后为空不覆盖旧 fingerprint |
| `catalog.isacc_candidates[]` | 存货科目组与别名、fit、used_by；最多 100 条。能否适合库存由来源 fit 表达，不由云端猜测 |
| `max_payload_version` | 可转整数且至少 1 才更新；无效值不替代旧值 |
| `companion_version/device/offline` | 协议、运行状态；offline 不发布目录、不领取刷新 |

`express_target_projection._masters` 只发布 products/customers/accounts；suppliers/units/branches 在其 form schema/capabilities 中明确标为未接入。不能因标准 schema 能容纳这些实体就虚构支持。

旧 config 镜像和标准目录投影是两个不同消费面：例如 name-only 商品可进入 reported_products，但 projection 的 source_id 仍需要 code。保留二者，不能从标准 projection 反推完整 reported_config。

### 服务器自己查询的资料

| 资料 | 固定来源及范围 | 新平台缺口 |
| --- | --- | --- |
| ERP 映射束 | `mappings_store.py:417` 返回 clients/accounts/taxes/products；Express 当前闭包消费 clients/accounts。clients 按 tenant 的映射表，accounts 按 tenant 的分类→科目映射 | 当前尚无对应原生主档/reader；Companion 六科目 mapping 不能替代此映射束 |
| 供应商档案 | `supplier_posting.py:28` 按 tenant、workspace_client_id、seller_tax_id 查询；`preflight.py:187` 仅进项挂载。返回 default_payment 等资料 | 做账公司对应新 Workspace，不是买方 Client；无 native 档案来源时不能声称查完为空 |
| 银行佐证 | `bank_evidence.py:48` 按本次票面日期确定 ±7 天窗口；`bank_recon_v1_store.py:447` 按 endpoint.user_id 与窗口查询 tx_date/direction/amount | 新仓只有纯匹配，没有原生流水来源。共享连接未来采用哪位授权主体的流水必须明确，不可任意换成当前浏览器用户 |

旧 preflight 将供应商/银行查询失败作为空证据继续推送（`preflight.py:185/209/220`，`bank_evidence.py:28-45`），是来源实际行为。第八批本地准备已设“服务端资料未收集则不放行”的明确就绪门；第九批不能借迁 collector 再悄悄改变该边界，也不能将未查询伪装成合法空结果。

当前 `connections.model.preparation:106` 的 complete、account_set、root_key、mappings、supplier_profiles、bank_index、reported_config 是内部资料合同，**不是来源真实性认证**。report body 和浏览器都不能给 complete 赋权。未来 reader 至少应能说明资料来源、授权范围、版本/读取时间，以及银行查询主体和日期窗口。一次目录刷新不能永久证明任意后续票面的银行窗口已经完整查询。

本批建议只实现目录闭环，preparation 保持缺失；完成目录刷新不自动解除 `connection.preparation_incomplete`。

## missing、empty 与发布时间

以下是固定源 `express_target_projection.py:241-377` 和 `routes/erp_agent.py:140-176` 的实际分支：

- **没有 catalog**：不发布账套级 masters，保留旧主档；仍可更新 endpoint 目录。不得把缺席变成空 products/customers。
- **catalog 是 dict**：旧 raw catalog 存储对缺失 products/customers 使用空数组并整体替换；不能把 `{}` 当作整个 catalog 缺席。isacc_candidates 只有键值非 None 时才更新；fingerprint 净化后空则保留旧值。
- **account_sets 没上报**：旧兼容逻辑可从当前 selected account 构造选项，path 来自 account_dir，writable 为 True。它不提供额外 root 证明；新平台已存在的非默认目标显式 root 闸仍生效。
- **明确上报空账套扫描**：记录 `ERR_ACCOUNT_SET_EMPTY`（或已报告错误），不发布空快照覆盖旧数据；有匹配请求则按失败完成。
- **账套级刷新**：优先用 `master_refresh_account_set` 作为 projection_key，必须是目录中登记的账套；不能把返回的 A 主档写到当前 selected B。
- **普通心跳无 request_id**：来源也会发布有效目录，不要求先捏造一次 refresh 请求。
- **有 request_id 的失败回报**：不能标记该请求成功。来源仍可能发布独立有效组件，目录组件是否有效与此请求是否成功必须分开表达。

报告收到时间由服务器 UTC 时钟提供，不能由 body 控制成功时间。旧 source_hash/组件去重规则继续复用；计划新增 reported_config 时须纳入相应内容哈希，否则 fingerprint 或版本变化可能被错误去重。

## 刷新状态与租约

来源 Express lease 为 900 秒，MRERP lease 为 180 秒（`target_refresh.py:17-18`）。本批最小段只接 Express。来源会锁 endpoint，单 endpoint 同时最多一个有效 Express lease；请求排序 endpoint 范围优先，其次当前所选账套，再按 requested_at。回包仅 request_id/account_set_key/scope_kind。

| 场景 | 固定源行为与新平台必须保留的约束 |
| --- | --- |
| active requested + 新请求 | 同 ID 合并，更新 requested_at。第八批新增幂等 alias 继续保留各 key 的原始指向 |
| active leased + 新请求 | 旧请求置 failed，error=`ERR_REFRESH_SUPERSEDED`；新建 ID。不能把它误写为旧源始终同 ID 合并 |
| 同 ID 的 requested_at 已晚于 started_at | 完成和过期路径保留 rerun 防线，回 requested、清理旧完成字段；不能发布成最终成功 |
| lease 到期且没有更新请求 | failed／`ERR_REFRESH_LEASE_EXPIRED`，旧完成拒绝 |
| 完成回报 | 匹配当前 endpoint、adapter、scope、leased 状态、owner 和未过期租约；新平台再匹配当前 connection revision、generation、有效身份 |
| 配置版本变化 | 旧 completion 不得绑定新 head；当前 alias 对旧 revision 的旧 key 继续拒绝 |

来源对 leased 新请求的处理有明确测试：`tests/unit/test_erp_target_refresh.py:104-129`；requested 合并见 `:131-159`。同 ID rerun 是另一层防御，并非前述分支的替代说明。MRERP `commit:144-154` 还在配置指纹变化或 rerun 时拒绝出版并重排，仅作设计参考。

现 Refresh 需新增 leased 状态、started_at、lease_owner、lease_expires_at、error_code；新增字段必须通过 DocType 原生 migrate。具体是否增加独立 lease 序号/票据由身份与刷新实现共同定稿。旧 Companion 回报只有 master_refresh_request_id/scope/account_set/error，没有新 lease opaque token；如新增回显要求，必须登记协议差异及真实客户端未验证，不能宣布无条件兼容。

## 候选模块与内部接口

以下均为计划，尚无代码或公开 RPC。

- `connections/collector/contracts.py`：`report_plan(body, previous_report, observed_at) -> ReportPlan`，只含净化后的选择/科目 patch、reported_config 的 present 组件、endpoint/account observation、显式错误和 refresh 回报信息。调用现有 targets 的身份和 projection 纯规则，无 Frappe/DB/网络。
- `connections/collector/service.py`：内部 `ingest_authenticated(connection_doc, report_plan)`，由 Companion 编排层先按 Connection→Credential 锁序复核当前身份，再于同一未提交事务内调用。collector 不反向导入身份域、不公开为 RPC，只处理允许的配置变化、目录出版与刷新回报；连接对象和报告计划均来自服务端，不使用浏览器传入的 tenant/connection/授权布尔。
- `connections/collector/refresh.py`：接收上述已锁连接的内部领取及完成/失败方法，持有真正 lease 状态。与既有 request_refresh 共用 Connection 锁序，不自行 rollback 上层事务。
- 计划的身份上下文由独立身份域提供：site、connection、issuer、credential_id、token_generation、branch、binding_generation 和不可 JSON 构造的内部 capability。首段 legacy generation=0；身份域负责当前 token/issuer 权限，collector 不另建认证，依赖保持 Companion → connections → targets，不能为复核而形成 connections → Companion 的反向导入。

现 `install_snapshot` 仅接受带 request 的内部安装，不能直接当普通 heartbeat 接口开放。需要内部区分“出版有效报告组件”与“完成当前刷新租约”，复用同一个快照持久化实现；不伪造请求，也不重复维护两套 snapshot 权威。

账户/root/六科目等影响发送准备的实际配置变化，应使旧准备失效，并与新报告原子发布；稳态相同报告不增长配置版本。如何从既有 configure 的“清 head”规则复用到报告更新，需在实施时定向审查，不让旧账套的 reported_* 缓存覆盖新选择。这里登记的是新平台版本约束，不声称旧 agent_reporting 已具备原子跨组件更新。

对外 targets/detail 继续只输出授权的安全目录和状态，不返回 raw report、preparation、银行、供应商或凭据。`collector_available` 应反映实际安装的采集能力，不能因存在字段而改成 True。

## 候选验证清单（未执行）

纯差分计划：哈希绑定上述五文件，AST 只提取无 IO helpers；从 stdin 接收固定源文本，避免引入旧应用。报告净化和 projection 各自比较，不借“统一净化”改变旧结果。至少覆盖别名、大小写/Windows 路径、字段截断、原有限量、无效类型、空值、selected 补选项、name-only 目录、fingerprint 与版本规则。

原生隔离 Site 的候选场景：

1. token/issuer 撤销、停用连接、错 connection/site/generation 拒绝；body 不能覆盖 AgentContext。
2. 普通心跳无请求也可发布有效目录，且不会增加伪 refresh；重复内容不涨版本。
3. missing catalog 保留旧 account masters；明确 catalog 空组件按源替换；未报告 stock groups/fingerprint 不擦旧值。
4. 空账套扫描保留旧快照并记录失败；只更新失败状态不能伪造成功版本。
5. 当前 selected B、请求 A 的回报只写 A；未知 A、scope 不符和旧 head completion 拒绝。
6. requested 合并与 alias 历史重放；leased 新请求使旧请求 failed、新建 ID；旧 completion 拒绝。
7. lease 到期、同 ID requested_at 晚于 started_at 的 rerun 防线、单 endpoint 单有效 lease。
8. 配置变更、token 轮换或身份撤销后迟到回报拒绝；重复合法 completion 的结果稳定。
9. 报告中的 selected/root/六科目变化使旧准备失效，稳态心跳不无谓涨 revision。
10. 目录 refresh 成功但服务器 mappings/profile/bank 来源未接时，准备仍拒绝；body complete=true 无权解除。
11. 通用 REST、DocShare、列表和直接方法调用不能写或读取受保护的报告、租约与原始资料。
12. 新 Site 安装和已有 Site migrate 保留原 Connection/Snapshot/Refresh、alias 与准备账本；升级幂等。

本设计不新增外部订单消费者、poll/ACK、真实 Companion 接入、MRERP 外部读取或生产调用。将来即使以上合成测试通过，也只能证明服务器本地协议与持久化，不等于客户机扫描、实际账套目录或 ERP 业务验收。

## 本次文档验证与下一步

已完成固定文件哈希与来源调用链只读核对；未运行纯差分或原生测试。当前只新增本模块合同，未修改业务、测试、DocType、hooks 或翻译。下一步先由 root 解释第九批运行配置漂移并确认 gate；再定稿身份上下文、刷新字段与接口归属，按本合同迁移并执行候选验证。整体 gate 和部署状态以工作包正本为准，不在本文件复制全局状态卡。
