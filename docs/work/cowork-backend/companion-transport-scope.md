# Companion 领取与回执迁移边界（第十一批）

目前可以继续建设新平台本地的领取与回执服务，但不能把旧裸 `log_id` 直接接到原生账本，也不能把服务端测试等同真实 Companion 已可写 ERP。最小完整切片应是：已明确激活的冻结 Record → 当前授权/目标复验 → 持久 Attempt 和写者锁 → 仅本次领取有效的私有句柄 → ACK 原子回执及本地副作用。真实客户端、写盘和外部回读另行验收。

## 证据与本次范围

- 新平台工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，本次开始 HEAD `addf40ae4c6ef04df58962225c5c7f1d29e658e7`，开始时无未提交改动。
- 只读来源 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`，对应固定 SHA `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。本文只证明该固定源的实际调用关系；第十一批 fresh baseline 由主任务另行采集；本分工先完成只读审计，随后只按主任务的限定授权实施下述 Delivery 私有映射。运行基准与完整运输装配由主记录承载。
- 历史采集曾观察到 legacy generation 0、managed 0；这是历史运行证据，不以它代替本轮生产配置，也不把 managed 源码的存在称作实际运行。
- 本分工的业务改动仅是下文 Delivery 映射、原生 DocType/controller 与其权限 hooks；未调用生产、客户端或 ERP，未启动/迁移 Site，未执行本地 Site 写入、外部通知、提交或部署。
- App/域：未来编排属于 `pearnly_cowork.companion`，账本仍唯一属于 `push.native`，目录与主档观察仍归 `connections`。不另建 Core Work/Push Log，不跨 Site 读 ERP 数据库。

## 固定源真实调用链

旧路由由 `routes/registry.py:44,198` 登记。`routes/erp_agent.py` 的模块注释仍写仅 DATAT，但实际 `express_push/__init__.py:34-73` 已允许端点默认账套和该端点报告的 writable 账套；迁移必须以执行代码为准。

| 入口/步骤 | 固定源定位 | 真实规则 |
| --- | --- | --- |
| 人工 Express 准备 | `express_push/enqueue.py:72-139` | 首先拒绝内部 ERP 来源，执行预检与映射；明细 mismatch/incomplete 转 manual，成功准备返回 pending/HTTP 202 哨兵，不是实际入账 |
| 明确目标的预留 | `confirmed_push_reservation.py:175-327,339-438` | 冻结目标并优先判断 success→active→unknown；600 秒的 `confirmed:legacy` 是派发预留，不能误用成 Agent 的 120 秒领取租约 |
| Agent 领取 | `routes/erp_agent.py:243-298` | POST `/api/erp/agent/lease`，总闸先检查；`max` 默认 10、1..50；先尝试 legacy token，再尝试 managed；并非长轮询 |
| legacy 身份 | `agent_store.py:44-65,106-120` | token 指向 Express endpoint，必须 generation 0，常量时间比摘要；路由再检查 enabled。`agent_id` 缺省/空白回 `default` |
| legacy 原子领取 | `agent_store.py:206-252`、`agent_lease_store.py:16-65` | 端点绑定锁与 generation 0 锁；pending、同端点、授权账套、排除内部来源；按创建时间、SKIP LOCKED 取最多 50 条；写 owner/120 秒租约 |
| legacy 返回后置账套检查 | `routes/erp_agent.py:254-278` | 对已领取 payload 再核 `account_set/account_root`；不符跳过返回，已写租约仍留到后续处理；响应可附 `master_refresh` |
| managed 身份/领取 | `shared_express_agent_queue.py:96-152,183-267` | 当前活跃租户/客户、shared_scope、generation>0、未撤销；bound/live profile 相等；last_seen 年龄须在 [-5,180) 秒；目标账套和 payload generation 都匹配 |
| ACK | `routes/erp_agent.py:301-371` | 总闸先检查，`result` 只允许 success/failed；legacy 与 managed 分流；成功结果可触发 LINE 通知投递 |
| legacy ACK | `agent_store.py:255-295,338-483` | 同端点锁记录；先返回既有 success 幂等结果，再检查 owner；没有 attempt token 或 lease 到期检查 |
| managed ACK | `shared_express_agent_queue.py:295-430` | 验 HMAC 句柄、端点、owner、pending、attempt、payload generation；无匹配返回 `{ok:false,stale:true}`；同样不按租约到期直接拒 ACK |

表内省略的目录前缀为 `services/erp/`；`agent_store.py`、`agent_lease_store.py` 位于其 `express_push/` 下。

内置 `/erp` 边界有三道实际保护：`internal_push_guard.py:5-15` 拒绝 `erp_web/line_erp` 和入口身份 `entry=erp`；准备入口 `enqueue.py:86-88` 再检查；两个领取 SQL (`agent_lease_store.py:44-46`、`shared_express_agent_queue.py:206-208`) 排除这两类 History。新平台应继续从服务端 History 查来源，不能信任请求 payload 自报 `source`。

## 线协议与载荷字段

旧领取请求是 `{max:10, agent_id?:string}`，响应是 `{ok:true, lease_seconds:120, jobs:[...]}`。每个 job 固定 `log_id/history_id/invoice_no/payload`，可另带 `master_refresh`。legacy 的 `log_id` 是日志 UUID；managed 则是 `m1_` 句柄：版本字节 + log UUID + 64 位 attempt，加 endpoint UUID 和 token digest 为密钥的截断 16 字节 HMAC (`shared_express_agent_queue.py:23-93`)。旧服务端注释称该外形服务 Companion 1.1.64；这不是已验证客户端 EXE 证据。

旧 ACK 请求字段全部为：`log_id`、必需 `result`、可选 `express_docnum/error/agent_id/line_modes/outcome/meta` (`routes/erp_agent.py:301-312`)。`outcome` 在六个受支持值内时优先于 result；无效或缺失时回落 result。`line_modes` 只在成功的实际调用中存入回执，不能因为底层 helper 可接受它就扩大失败分支合同。

进销项 payload 在本轮只运输已冻结内容，不从当前票据或配置重生成：

| 字段簇 | 必须保留的键 |
| --- | --- |
| 路由/版本/来源 | `payload_version`（源当前为 1）、`direction`、`doctype`、`account_set`、`source` |
| 日期/外部引用/往返 | `docdate_be`、`vat_period_be`、`ref_no`、条件 `prior_docnum` |
| 金额/对方/分录 | `supplier` 或 `customer`、`vat_rate/base_amount/vat_amount/total_amount`、`lines` |
| 明细和采信依据 | `items/items_status/items_account/items_line_sum`、`account_source/account_review`、`doctype_src/doc_lane/item_src` |
| 库存条件字段 | `stock_acccod/vat_capitalized/opening_stock` |
| 预检追加的目标字段 | `account_root/account_dir/account_company/account_set_row` |

定位：`payload_keys.py:20-50`，`common.py:248-255`，`preflight.py:341-356,424-437`。`WRITE_PAYLOAD_KEYS` **不包含**预检追加的四个目标字段；旧 `bridge_contract.py` 只是另一个仓库白名单的镜像，不能代替真实客户端接收验证。managed 还由 `shared_express_push.py:428` 加 `managed_generation`。运输层不能为了过白名单静默删字段或擅自升级 payload_version。

`duplicate_confirmed` 是另一种敏感写入意图：它关闭客户端查重保护，旧队列因而只允许领取一次；并非普通“用户已确认”标志。新平台没有新增授予此行为的入口，不能由机器或浏览器自行追加。会计手录 ar_receipt/ap_payment/gl_journal/stock_adjust 有独立字段表（`payload_keys.py:53-141`、`bridge_contract.py:113-160`）；第七批纯包未迁这些 mapper，不能让一个通用运输接口暗中把它们列作本批已支持。

ACK `meta` 的原白名单（`common.py:381-423`）：字符串 `companion_version/account_dir/account_dir_resolved/doc_type/docnum/party_code/stage/error_code`，每项最多 200 字；布尔 `created_customer/created_supplier/index_requery/rolled_back/written/duplicate_skipped`；列表 `tables_written/created_masters/cdx_failed_tables`，最多 40 项、每项 60 字。布尔按旧 `bool(value)` 转换，不凭注释宣称严格类型。legacy success 另由 line_modes 派生 direct_account fallback_count 和缺省 created_masters；managed 不派生。

## 租约、回执与未知状态不能混为一套

| 情况 | 旧 legacy | 旧 managed | 新原生已落实/后续要求 |
| --- | --- | --- | --- |
| 初次领取 | owner+120 秒，无 attempt 凭证 | m1_ 绑定 attempt；generation 必须匹配 | 每次均递增 fence、生成随机 attempt_token，只存摘要 |
| 普通过期 | 允许重领，依赖客户端 find_existing 防重 | 允许重领，attempt 增加以废旧 handle | 所有已领取过期→manual/result_unknown，保留写者锁；不得自动重发 |
| duplicate_confirmed 过期 | 独立 sweep→manual/confirmed_push_unacked，并镜像 History | 不重领，但该队列没有调用 legacy sweep，可能留 pending 不可领取 | 同统一 unknown 保护；不能复制 managed 卡 pending 的缺口 |
| success | 终态幂等在 owner 检查前 | 重复 success 已非 pending，返回 stale | 先验有效当前 attempt，再返回保存的 success；晚失败不覆盖成功 |
| waiting_lock | pending，wire attempt 不变，不计失败 | pending，wire attempt+1，废旧 handle | 保持 branch 计数差异，下一次 fence 仍必须增长 |
| needs_mapping/needs_review | manual，立即停重试 | manual，立即停重试 | 复用已迁生命周期，不假称发送成功 |
| failed/rolled_back | `int(attempt or 1)`≥3 转 manual，否则 pending；attempt+1 | 原 attempt≥3 转 manual，否则 pending；attempt+1 | 保持实际计数，不能把文案“重试三次”再实现另一套；另记录 failure_count |
| ACK 晚到 | 无 expiry 检查，须 owner；被 sweep 清 owner 后会拒绝 | 无 expiry 检查，须当前 pending/attempt | unknown 中的有效原 attempt 可收真实迟到 ACK，更新后才释放写锁 |
| 重复非成功 ACK | owner 已清，通常 lease_mismatch | pending/owner/attempt 已变，stale | 同 finished attempt + 同摘要可重放；不同摘要拒冲突 |

旧来源定位：`agent_store.py:144-252,338-483`、`shared_express_agent_queue.py:183-267,309-425`。新来源：`push/lifecycle.py:83-106`、`push/native/service.py:391-428,443-547`。旧普通超时自动重发改为统一 unknown 是第八批已登记的保护变化；缺真实客户端幂等与物理写者证明时，不回退此保护。

## 账本之外的实际副作用

必须逐项迁移或明确留下缺口，不能只接 ACK 更新 Record 就报业务完整：

1. **History 推送终态与时间**：legacy `agent_store.py:314-335,394,446,472-473` 在 success/manual 镜像；unknown sweep 也镜像 (`189-195`)。managed `shared_express_agent_queue.py:283-292,413-414` 严格要求命中 History 一行。新 native 当前只更新 Record/Attempt/Lock，没有相应 History 镜像。未来镜像必须与当前有效状态匹配，不能让旧失败覆盖较新成功。
2. **库存主档观察计数**：legacy success 从 `line_modes` 数 mode=stock_item 且 created 的行 (`agent_store.py:299-311,395`)，调用 `agent_reporting.py:355-389` 即时增加 catalog_fingerprint.stock_master_count，避免等半小时扫描；managed ACK 没有此调用。未来由 connections 提供幂等观察入口，不能重放 ACK 重复加数，也不能把机器反馈冒充完整主档快照。
3. **成功 LINE 回执**：`routes/erp_agent.py:361-370` 将 notification_log_id 投 Cloud Tasks 或 BackgroundTasks；`line_push_notification.py:90-119` 再读成功日志，只有 `cowork_line/line_erp` 来源有绑定收件人 (`29-47`)，实际 `push_text` 是外部发送。新平台暂未迁此链。可先保存唯一待通知事件，但不得把未发送标作已通知；不因通知失败回退 ERP 成功。
4. **更早的派发统计**：`confirmed_push.py:139-145` 在预留派发结束更新 endpoint stats/History，并可能通知；它不等于后续 Agent ACK 回调，不能给 ACK 额外重复统计。本文未将整个旧统计体系当成已迁。
5. **真实 ERP 写入与复核**：云端 ACK 只接收客户端声称的 outcome/docnum/meta；源注释描述备份、DBF 写入、CDX 重建、查重和索引回读，但本仓固定源不含当时实际运行 EXE 的证明。`meta.written/index_requery/rolled_back` 不是服务器独立回读证据。

## 新平台接线前具体缺口

- 第十批 HTTP 桥只允许精确 `companion.heartbeat.report`；`companion/http_auth.py:19-46` 并未开放领取/回执。新增入口需要明确方法白名单，同样覆盖 before_login、私有 token handoff、cmd/sid/别名、Recorder/no-store、IP 校验与上下文单次消费，不能改为任意 companion 前缀放行。
- `push.native.service.trusted_adapter_context:297-320` 是内部注入缝，不是机器鉴权；其 owner/generation 不能直接来自请求 agent_id。未来 owner 至少绑定当前 Site、Connection、Credential/token generation 与设备声明；agent_id 只作受约束标签。现有 Attempt 尚未单独记录 Credential ID/token generation，需明确不可变绑定方式和审计字段。
- 派发应从服务端 Record 构造 ReservationContext，重查原 actor 当前权限及冻结 History/Connection/branch/generation、目录 proof/writable，而不是把当前机器 issuer 当票据 actor。现有 `_dispatch_context/_dispatch_record/_dispatch_target` (`native/service.py:323-365`) 已有这些保护，运输层必须复用。
- ACK 授权不能简单复用 heartbeat 全部资格：native `_receipt_scope:431-440` 有意允许原票据 actor 后来撤权/总闸关闭后收回原 attempt 结果；而 `credentials.authenticate/revalidate:236-273` 仍检查总闸、发行人当前权限和活跃凭据。必须定义**只可回执、不能领新载荷**的窄授权及旋转/撤销后处理，不准为兼容直接关闭通用鉴权。发行人和原票据 actor 是两个身份。
- `native.lease` 返回内部完整 `record/target/payload`；`project` 包含配置与检查证据。机器响应只投影实际写入必需的 frozen payload、句柄、票号等，不返回完整 frozen_target/config/授权主档/其它记录。attempt 明文凭证不能进日志/Version。
- 当前 public prepare 只到 prepared；必须存在明确的人工/服务端激活意图才能变 pending。不能把机器第一次 poll 偷偷定义成“同意发送全部 prepared”。
- 本 Site 的端点锁和同公司/root/account 锁不是物理设备唯一写者证明；不同公司/路径别名是否指同一 DBF 库仍待设备身份与本地锁验证。凭据认证只能证明持有有效密钥，不能证明正在运行受认可二进制。
- `brg_` `/api/erp/bridge/*` 是另一套独立租户桥、表、角色与长轮询协议 (`routes/erp_bridge_routes.py:2-16`)；不可混入 exp_ 领取，也不可据此宣称读写桥已迁移。

## 建议最小实施包（分工设计）

只读阶段提出以下顺序，必须在 fresh baseline PASS 与具体归属授权后实施。本分工仅获授权完成下文 Delivery 映射；这里的 transport 路径为设计建议，不能据本分工文档认定其它代理的运输服务已经验收。

1. `companion/transport/contracts.py`：纯 Lease/Ack 输入边界、受限 agent_id、结果/元数据合同、外发 DTO；复用 push.lifecycle/common，禁止复制状态机。保持旧字段和金额类型，新增限制逐项登记。
2. `companion/transport/handles.py`：以 `log_id` 字段承载不透明领取句柄，绑定 Site/Connection/Credential generation/Attempt/fence。它必须能还原到服务端已持久的领取，不接受裸 Record 名、旧 UUID 或只靠 agent_id。可使用已有随机 attempt_token，加严格版本化编码/摘要；无需为此另建一份推送事实账本。
3. `companion/transport/service.py`：当前机器身份→锁 Connection→有限、确定顺序扫描已明确 pending 的记录→重查原人类 actor/目标→native.lease。批次最多 50，端点/账套锁会让后续同写者记录等待；不得为填满 max 绕锁。一次 HTTP 领取的本地事务必须成功提交后客户端才得到载荷，提交失败不发送；提交成功但响应丢失按 unknown，不能用自动重领补偿。DB 冲突可在纯本地完整事务边界有界重试，不能包真实外写重试。
4. `companion/transport/receipts.py`：验证句柄、当前允许的回执身份、冻结归属与 fence，调用 native.ack/recover_expired；同事务更新准确的 History 投影与唯一观察/通知事件。通知出口仍禁用且明确待迁。只暴露安全结果，不把 supplied success 当独立 ERP 核验。
5. 在以上本地链验证后，才将精确 lease/ack RPC 接入现有机器桥，保留普通人类认证不变。若本轮仍缺客户端 gate，可完成内部服务、纯合同和合成 WSGI 接线验证，但产品记录必须明确“未连接真实客户端”，不能启用生产消费者。

建议验证矩阵：固定源码 lease/ACK 差分（两个 branch 六 outcomes、零/临界 attempt、无效 outcome、success 幂等）；真实 Site 并发领取与请求丢失、凭据/人类权限/目标变化、总闸关闭收旧 ACK、未知保锁、过期旧句柄、重复和冲突回执、跨公司/Connection 句柄、内部 ERP 来源拒绝；每次成功副作用恰好一次。最后单独验证实际客户端读取新 URL/message 封套、句柄回显、字段白名单、账套与本地写锁、写后回读及恢复。后者当前没有执行，也不属于此次只读任务。

## 固定源哈希

以下 SHA256 是本轮只读计算，绑定上文来源文本；不替代生产运行 gate。

| 相对固定源码路径 | SHA256 |
| --- | --- |
| `routes/erp_agent.py` | `bf253777feb90eeb2c3d9f0bccdc5715a49ac8026a8a9710105b77a8106044bb` |
| `services/erp/express_push/agent_store.py` | `03d86de7de82761f33f386913d3d32a93874953a237a8151e02c4e5e3000c9bb` |
| `services/erp/express_push/agent_lease_store.py` | `1279f909b3e9525f806994e6043a562bea22bfb2cfefe07dc34f7c4b7954ee22` |
| `services/erp/shared_express_agent_queue.py` | `f7d5484d01d4fac2d1fce001fd21b428b0c98f82f5b4e2ad879a7c5c7bfa11ab` |
| `services/erp/shared_express_agent_auth.py` | `01e05889facda13f67dd413bcf55973c61858a093b3d7890744ad660e8b86fe5` |
| `services/erp/legacy_generation.py` | `47d7273d538161b82b92a6b58d077ca1e513b268f574a4231e08b4f6e0322468` |
| `services/erp/express_push/common.py` | `e00c6410c711208cf2bcbf20579d9d67e365b2957aa9d90a3cd9f68e4fe09f58` |
| `services/erp/express_push/__init__.py` | `127d05cb51b7c6218abe5befde8f6500ec4f88ea6a2aedb014b6f066d1c1e1d3` |
| `services/erp/express_push/preflight.py` | `5b5857f297b59c35227d35a40091af0969d870bad71aad735a12ed2f1019f407` |
| `services/erp/express_push/enqueue.py` | `234aaf0675ccd9e05ff697230b2f404574a088775129d5cb7a3a220a745d637a` |
| `services/erp/express_push/payload_keys.py` | `5c605a36641b879f33a167afa151fcc3eca0e6d543df837fad60a86e16425ef1` |
| `services/erp/express_push/bridge_contract.py` | `bb82c1fe3ba88c32294e0df3d7a26865710396181200458d47a67d1d825b57b9` |
| `services/erp/express_push/agent_reporting.py` | `c1ff3a33724ffb302c94e7c4ee44a1ecd7ba7753ca71c35f72a09c3247bf3a65` |
| `services/erp/internal_push_guard.py` | `b64ba33ae6a1dd8fda9319aaf485b0280125c6aff2b17b42c058f963a356f14c` |
| `services/erp/confirmed_push.py` | `550cef504360acb444c9a8304d22139de462030d22cc0cf0c7b330eeaaa6aac8` |
| `services/erp/confirmed_push_reservation.py` | `0ce2b8ad49862e9b6ac26a8b4e5af89f1b7bf41fd674af01d5233bf1acb49e2d` |
| `services/erp/line_push_notification.py` | `d8b189e0d04165bcf0946409bd6d9d6134fcc14b8cc1998d3846d2a139196d95` |
| `routes/erp_bridge_routes.py` | `bdaa44c440804eac14deef425ed38e55e0116da7626edcf689a4346724bd1a9f` |

来源验证：只读核对上述路径/行号和新平台 HEAD；18 个来源文件 SHA256 全部回验，文档检查通过。以下实现仍不报告外部运输功能完成。


## 本分工已实现的 Delivery 私有映射

限定落点为 `companion/delivery_store.py`、`companion/delivery_documents.py`、原生 `Cowork Companion Delivery` DocType 及它在 hooks 中的 permission/query/DocShare 条目。不改 credentials、push.native 状态机、运输服务或测试；新依赖只沿 Companion → 本 Site Push Record/Attempt，不产生反向依赖。

| 接口 | 内部合同 |
| --- | --- |
| `owner(credential)` | 仅计算标签，不是鉴权；SHA256 绑定 Site、Credential ID 与 token_generation，返回稳定短字符串，不能用客户端 agent_id 替代 |
| `create(*, credential, claim)` | 重新验证真实私有 AgentContext；claim 必须是有效 native.lease 成功结果；由 record.name 锁读真实 Record.active_attempt，再锁 Attempt，检查公司/客户/连接、branch/generation、leased、fence、owner 与 attempt token 摘要；返回原生 Document |
| `get(handle, *, lock=True)` | 当前 Cowork Site 的 UUID 句柄查找，默认当前行锁；内部调用者负责机器授权，不提供 RPC |
| `secret(doc)` | 从该句柄的持久 Password 解密；不采信传入 Document 上被替换的内存字段，不返回公共 DTO |

DocType 的 13 个不可变字段为 `record_id/site_name/connection/workspace/client/credential_id/token_generation/push_record/push_attempt/fence/owner_id/issued_at/attempt_secret`。name=随机 UUID record_id（不是秘密）；record_id 与 push_attempt 各自唯一；credential_id、push_record、push_attempt 为原生 Link；issued_at 为 UTC；attempt_secret 为 hidden/read_only/no_copy Password。所有字段只读且 no_copy，track_changes/track_seen/track_views 均关闭。

写入使用仅模块持有的真实 object capability，返回后清除该 flag；controller 只允许新建，已有行不能保存、重命名或删除。通用 read/list/write/share 一律抛权限错误，包含 Administrator 和 DocShare 回退；新建前检查预置 active DocShare。Document repr/str 仅含公开 UUID，绝不包含凭证或字段集合。

已只读核实固定 Frappe 16.33.0 候选镜像的行为：`model/document.py:480-497` 在 autoname 后、db_insert 前执行 `_validate`；`:838` 调用 `_save_passwords`；`model/base_document.py:1349-1369` 将 Password 写入加密 `__Auth` 并立即用星号替换 Document 字段；`utils/password.py:51-71` 没有 commit。因此 Delivery 创建错误可能发生在 __Auth 已写、DocType 尚未插入之时，**调用方必须把 native.lease 与 create 放在同一可回滚事务/保存点**，不能捕获创建错误后提交租约或孤立密码。store 不 commit、不自做全事务重试、不输出 attempt_token。

稳定错误码为 `companion.delivery_invalid`、`companion.delivery_not_found`；通用访问拒绝文案为 `Use the Cowork Companion service to access deliveries.`。重复同 Attempt 插入由数据库唯一约束拒绝，不创建第二个 handle，也不回写旧凭证。

本分工已执行 3 个 Python 模块 AST 检查、13 字段 schema 不变量检查、架构检查（0 violations）和 diff 检查，均通过。未自行运行 Site；原生 Password/__Auth 加密回读、重复 Attempt、generic HTTP/DocShare 拒绝和租约+映射失败回滚由主任务统一验证。静态检查不能替代这组运行验证。

## 第十一批最终内部接线

`companion.transport.lease` 已把当前 sealed AgentContext 接到原生队列；`receipt_auth` 提供独立、仅限既有Delivery范围的历史凭据能力，`transport.acknowledge`消费后调用唯一native账本。均无whitelist、worker、scheduler或外部IO。HTTP仍只开放第十批心跳；人工prepare仍停prepared，无公开激活入口。这里更新前述“未来/需实现”项的内部完成范围，不把HTTP和实际客户端缺口标完。

所有 native派发验证来自原Record actor与冻结值；请求agent_id不是owner，owner绑定Site/Credential代数。冻结payload逐字结构保留。一次poll解析旧max合同但只返回至多一个job，保持已登记endpoint写者锁。50条候选内只有确定撤权或冻结失效落manual并保存原因；并发已被领取的候选略过，未知/数据库失败不隔离。陈旧RR视图可能稍后poll才发现新工作；外层必须拥有新事务、失败回滚和回复前提交边界。

ACK接收仅确认原credential+Delivery，保留总闸/撤权/轮换后的旧回执权，仍由native检查当前结构scope、fence、owner和token；不恢复新派发权。最小回执投影保留旧success、等待锁、人工和失败次数形状；结果已被确定隔离后同值重放返回manual/idempotent，明确是本地扩展。密钥、fence、原人权限与target/checks不进入机器回执。

实证：40项新增真实Site、62项既有回归、739项离线通过；旧固定函数候选环境差分910一致+17明确输入收紧，Pydantic版本差异仍披露。新装/升级/重复迁移，原生加密、唯一约束、通用HTTP权限与44次翻译查询均验证。完整证据及未完成项以[工作包第十一批](../2026-09-20-cowork-backend-migration.md)和[清单](evidence/eleventh-local-validation.json)为准；没有真实ERP、Windows或LINE验收。
