# 第十一批：Legacy Express poll/ACK 来源与客户端证据边界

本分工仅核验线上来源及既有运行投影，并只读审查固定源码；不实现业务、不访问生产 poll/ACK、不读取业务 payload，不操作 Site、Windows 客户机或 ERP。唯一任务工作树为 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`；开工实际 HEAD 为 `addf40ae4c6ef04df58962225c5c7f1d29e658e7`，检查时暂存区和工作树干净。没有改主目录或 DMS。

## 开工 gate

**source/runtime 与第十批 delivery 一致，当前固定服务器合同可作为本批开工依据。第八→第九批旧配置差异 HOLD 不关闭；本轮证据不能证明实际 Windows EXE 或旧数据迁入一致。**

| 项目 | 当前证据 |
| --- | --- |
| 环境 | project `pearnly`，region `asia-southeast1` |
| source 采集 UTC | `2026-09-21T06:03:56.049276+00:00` |
| source 归档 / canonical SHA256 | `evidence/baseline-eleventh-start.json` / `c090eac42643a2a84858f1511f144a30bb58cb696fa52ebe03df3b8984b0ef6d` |
| 实际流量 | Web 100% `pearnly-web-ec2e5c957e6b-s3`；Worker 100% `pearnly-worker-ec2e5c957e6b-s3` |
| 不可变镜像 | `asia-southeast1-docker.pkg.dev/pearnly/pearnly-app/app@sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226` |
| 完整源码 SHA | `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188` |
| 源码映射 | 成功 GitHub run `35321281376` 的完整 head SHA 与固定部署 ledger 一致；不只依赖 revision 短 SHA |
| runtime 采集 UTC | `2026-09-21T06:04:21.921805+00:00` |
| runtime 归档 / canonical SHA256 | `evidence/push-runtime-eleventh-start.json` / `97c1306458dee2997458e9a58ef8f104a8118134cb2b3eb9ead9ce296b6fa56b` |
| 与第十交付比较 | source PASS；runtime `settings_unchanged=true`、`observations_changed=false` |
| 全量 endpoint config 投影 | count `60`，集合摘要 `5ee9e6604b3c6dee19ab3227d8484fad` |

只复用了已审计的 `cowork-migration-baseline.py collect-readonly` 和 `cowork-push-runtime-baseline.py collect-readonly`，没有改脚本、扩 SQL 或增加生产读取类别。配置凭据只在既有 collector 进程内使用，SQL 事务为只读 REPEATABLE READ；输出为既有受限聚合，不含凭据明文、端点 ID、企业名、客户路径或票据。两份新证据与 `/tmp/pearnly-cowork-migration-20260920/` 原件逐字一致并设只读，没有覆盖历史证据。

第八→第九批 `461d6540e21d14f3a41443ad720d5605` → `5ee9e6604b3c6dee19ab3227d8484fad` 的原因仍无法追溯，本批稳定不能证明那次差异只是心跳或 DMS。旧 owner、目录、默认账套和历史任务的导入仍沿[第九批 HOLD](ninth-source-review.md)；新 Site 新 Connection/新凭据的实现范围沿[第十批限定决定](tenth-source-review.md)。本批交付复核见下文，未用首尾稳定关闭历史差异。

## 当前分支和客户端版本投影能证明什么

Web/Worker 读取到的相关聚合相同：`ERP_PUSH_ENABLED=true`、`ERP_SELLER_ROUTING=1`。7 个 Express 连接均 enabled、`binding_generation=0` 的 legacy 分支，managed 为 0；6 个存在 legacy token hash，1 个没有。这个聚合说明已配置分支，不证明此时存在实际 poll/ACK 流量。

| 已存 `config.companion_version` 声明 | 连接数 | 已存 payload 声明 |
| --- | ---: | --- |
| `1.1.59` | 1 | `1` |
| `1.1.67` | 1 | `1` |
| `1.1.77` | 3 | `1` |
| 缺失 | 1 | `1` |
| 缺失 | 1 | 缺失 |

`agent_version` 对这 7 个 legacy 连接均缺失。`legacy_seen_present` 合计 6 仅是配置字段存在性：collector `:178` 没有把 legacy 时间解释为当前在线窗口，不能据此宣称六台机器仍在线。`managed_fresh=0` 对 legacy 同样不是离线证明。

版本声明来自旧 `routes/erp_agent.py:169–175`，落入 `agent_reporting.py:392–402` 的字符串写入；该函数只要求非空且长度不超过 20。collector `:161–166` 将安全格式的版本分组，其余归 absent/invalid；没有记录 EXE SHA256、签名、安装路径、构建流水线或 Companion 完整源码 SHA。已允许 metadata 因而不能完成“当前运行客户端 → 不可变构建 → 固定 Companion 源码”的映射。

固定服务端的 `routes/companion_installer_routes.py:17–29` 只定义鉴权下载固定路径文件；仅有此路由不能证明当前文件存在、文件内容、发布来源或客户端已安装它。该固定 Git tree 的 `static/companion/` 未列出安装包条目，本轮没有读取或下载线上安装包。即使以后取得可下载包的哈希，也仍需要实际运行机器的构建身份才可闭合。没有读取本地 Companion HEAD 来替代这些事实。

## 线上固定服务器调用链

以下路径均相对 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。本轮已将下表涉及的 9 个关键文件逐字与 `git show ec2e5c…:<path>` 比对通过，源码快照没有仅凭目录名取得信任。

| 固定来源 | 真实外层行为与迁移关注点 |
| --- | --- |
| `services/cloud_runtime/proxy.py:11–16,99–115`；`routes/registry.py:198` | `/api/erp/*` 从 Web 代理到 Worker；真实服务器来源必须覆盖两组件。路由已被应用注册，不是未使用的 helper。 |
| `routes/erp_agent.py:43–56,281–298`；`agent_store.py:44–65,106–120` | ERP 总闸、Bearer hash、enabled、legacy generation 决定入口；legacy 认证失败才进入另一 managed 分支。停用的有效 legacy 连接不能回落别的身份。 |
| `routes/erp_agent.py:243–278` | POST `/api/erp/agent/lease`，max 默认 10、范围 1–50，owner 来自 agent_id 或 default；返回 120 秒租约和 `log_id/history_id/invoice_no/payload`，可附目录刷新任务。 |
| `agent_store.py:206–252`；`agent_lease_store.py:26–64` | 原子领取同 endpoint 的 pending，FIFO/SKIP LOCKED；账套过滤在 LIMIT 前，排除来自 `erp_web/line_erp` 的历史。普通过期租约可重领，依赖客户端写侧查重。数据库错误旧实现返回空列表，不能把它当“没有任务”的强证据。 |
| `agent_store.py:144–203` | `duplicate_confirmed` 任务已领且失联过期后落 manual，并镜像 History；不自动重投，因为客户端确认位会关闭普通去重。这是未知结果保护，不能迁成普通可重试失败。 |
| `routes/erp_agent.py:301–349`；`agent_store.py:255–271,338–401` | ACK 按 endpoint 加锁，成功终态先幂等，再比对 lease_owner。旧 legacy 没有独立 attempt secret，也没有在该 ACK 判据中检查租约到期；迁移不能为兼容旧字段而移除新平台已有的 attempt/fence。 |
| `agent_store.py:403–480` | waiting_lock 释放租约但不烧次数；needs_mapping/needs_review 留人工；failed/rolled_back 增次数，达到旧门槛转人工。只迁纯 outcome 判定不足以覆盖队列、事务、History 和回执副作用。 |
| `agent_store.py:299–335,394–400`；`routes/erp_agent.py:352–370` | 首次成功还镜像 History、增加库存主档画像计数，并在接口后段安排 LINE 通知（Cloud Tasks 或后台任务）。这些是外层效果；成功 ACK、仅持久账本或 HTTP 200 都不能替代它们的迁移验收。 |
| `common.py:246–254,371–422` | 载荷协议版本为 1；ACK outcome 和净化 meta 决定日志展示。meta 中的 companion_version 仍是客户端声明，不是构建证明。 |

`agent_store.py`、`agent_lease_store.py`、`agent_reporting.py`、`common.py` 的完整目录为 `services/erp/express_push/`。

关键文件 SHA256：

| 文件 | SHA256 |
| --- | --- |
| `services/cloud_runtime/proxy.py` | `ee35ecd20d37e3b46d7612a09e6ee5cbe1bb8028f84e0852091918f3e3f3e7ec` |
| `routes/registry.py` | `acdbcb9e7fa1b4da742f8aa0f8bcddc204b5482388fee6a29f58c39f9f26a46b` |
| `routes/erp_agent.py` | `bf253777feb90eeb2c3d9f0bccdc5715a49ac8026a8a9710105b77a8106044bb` |
| `services/erp/express_push/agent_store.py` | `03d86de7de82761f33f386913d3d32a93874953a237a8151e02c4e5e3000c9bb` |
| `services/erp/express_push/agent_lease_store.py` | `1279f909b3e9525f806994e6043a562bea22bfb2cfefe07dc34f7c4b7954ee22` |
| `services/erp/express_push/agent_reporting.py` | `c1ff3a33724ffb302c94e7c4ee44a1ecd7ba7753ca71c35f72a09c3247bf3a65` |
| `services/erp/express_push/common.py` | `e00c6410c711208cf2bcbf20579d9d67e365b2957aa9d90a3cd9f68e4fe09f58` |
| `services/erp/shared_express_agent_queue.py` | `f7d5484d01d4fac2d1fce001fd21b428b0c98f82f5b4e2ad879a7c5c7bfa11ab` |
| `routes/companion_installer_routes.py` | `60a0222fe93c9c8ac425662799f1a1b69d903f0172f69e2645e74195ce077e20` |

## 对本批协议实施的限制

固定源码可证明服务端 Legacy 接受的形状和状态规则；managed 的 `shared_express_agent_queue.py:59–93` 另有 HMAC/attempt 的 `m1_` 句柄，但当前没有 managed 实际连接。此源码分支不能证明现存 legacy 客户端会原样回显任意 opaque handle，更不能证明它接受原生 Connection 名、Frappe RPC URL/响应封套或新取消/恢复语义。

可以继续在明确的新 Site/新凭据范围内实现服务器运输合同、失败闭合与合成验证；新协议的有意变化应单列。不能以代码相似、版本号相同、模拟 HTTP 通过或本地 Companion 最新 HEAD 宣称真实旧 Windows 兼容或 ERP 业务验收。需要的后续证据是：实际运行 EXE 的不可变身份、到 Companion 固定源码/构建记录的映射，以及受控的真实客户端领取、重复/迟到 ACK、未知结果恢复与 ERP 回读；本轮没有取得，也没有自行连接客户机或触发这些外部效果。

## 内部 Delivery 方案的限定设计复核

本轮拟新增的范围是内部领取/回执服务，不增加 HTTP 路由、不改 Windows 客户端。主任务提出以私有 Delivery UUID 绑定 Site、Connection、Credential ID/generation、Record、Attempt、fence 和 owner；原生 Password 保存 attempt token，只有接收回执时解密。以下是对现有接口和拟议方案的静态结论，尚非候选代码通过或实际客户端验收。

现有 `push/native/service.py:431–440` 的 `_receipt_scope` 已明确允许原人类 actor 失权后接收既有尝试结果：它核当前 Site 及 Connection/History 的 workspace/client 结构归属，不重新授予派发权限。`ack:489–497` 在成功终态幂等返回前仍核 `_attempt` 的 fence、owner、attempt 及 token digest；`recover_expired:527–547` 保留未知状态与写者锁。因而这份方案可复用 native 接口，不需要放宽公开 DocType/RPC 权限或删掉已有 attempt 保护。

实施必须保留以下边界：

- **两个不同的机器能力。** Poll 使用 `credentials.revalidate:256–273` 检查当前有效凭据、总闸及 issuer 权限；Receipt 必须用独立的真实对象 capability，仅查 Delivery 精确绑定的历史 Credential ID、generation、digest。不能将宽松的历史回执身份转换为 `AgentContext`，也不能通过 active/latest credential 查找取代历史绑定。新 token 不因此取得旧 token 的 Delivery 回执权。
- **原人类 actor 不替换。** 领取必须从服务端 Record 还原 ReservationContext，并保留其 actor/History version/Connection revision/branch/generation。native `_dispatch_context/_dispatch_record:323–345` 已核原 actor 的当前授权及冻结版本；当前 token issuer 是另一身份，不能代替它。Receipt 同样只从不可变 Delivery/Record 还原上下文，不接收客户端提供的 scope/owner/fence。
- **撤权后只收结果，不返资料。** native `project:142–152` 和 `ack:497,524` 返回内部完整投影，含 frozen payload/target/checks 等。Receipt 必须再投影成最小 accepted/status/replayed 信息；不得向已撤销 token 透传完整 Record，也不得让成功幂等提前返回绕过 Delivery/credential 验证。
- **一笔本地事务覆盖领取。** `native.lease` 的 Record、Attempt、Lock，以及 Delivery 和 `__Auth` 的可解密凭证必须一起提交或一起回滚。Delivery/Password 写失败不能留下已领取但没有回执能力的任务。`native.atomic:57–67` 遇数据库死锁会回滚整个事务，调用方不能捕获后沿用旧 context 或继续提交部分结果；只可在明确的外层边界重做完整本地操作。
- **有限但持续的历史回执权。** 关闭总闸、禁用连接、撤销/旋转 token、撤销 issuer/actor 权限，应阻止新领取，但不丢掉它已领取的结果。此处“当前 scope”指 Connection/History 当前仍存在且 workspace/client 与绑定相等，不是重跑当前人类授权。改变 scope 或失去结构证据必须拒绝并保留未知账本，不能伪造失败终态。历史 Delivery/credential/attempt secret 不宜在未知结果解决前清除。
- **不自动补发未知写入。** 领取提交但响应丢失仍可能已经外写，沿现有设计转 unknown/manual、保锁；不得为了补偿丢失响应重新 lease。等待锁或明确失败进入下一次合法领取时必须增长 fence，旧 Delivery 无权更新新 Attempt。回执与本地 History/观察事件等副作用须同一事务、恰好一次；外部通知仍是另一个未验收环节。

必要的定向用例是：正常领取/回执；撤销、轮换、总闸关闭、issuer 与原 actor 分别失权后的旧回执和拒绝新领取；新旧 token 对错 Delivery、跨 Site/Connection/History、伪造 owner/fence；未知状态的迟到 ACK、相同重放、冲突重放、success 后的失败、下一 fence 后的旧回执；Delivery/Password 保存失败后的整笔回滚；领取提交后的响应丢失；所有回执响应均无冻结票据/目标配置泄漏。当前只是提出测试场景，没有代替实现者报告通过。

历史有效 token 加其 Delivery 仍能声称该次外写结果，这是本方案为保留迟到结果而刻意保存的窄能力。它不证明真实 EXE 身份、外部 DBF 已写成功或 ERP 已独立回读；上述生产客户端与旧数据 HOLD 继续保留。

### 实现限定读审与队首问题关闭

已只读核对 `companion/receipt_auth.py`、`transport.py`、`delivery_store.py`、`delivery_documents.py`、Delivery JSON 及相关 hooks。历史凭据回执和当前凭据派发使用不同 capability；receipt 根据不可变 Delivery 精确取历史 credential，最终仍经 native 当前 scope/attempt 检验。Delivery 创建重新检查真实 AgentContext、原 Record/Attempt、owner/fence 与 token 摘要，写入能力在 finally 清除；所有通用读取、修改、分享、改名和删除均拒绝。`queue_contracts.job/ack_response` 重建受限线协议响应，不透传 native 完整投影。没有新增公开路由或放宽通用凭据授权。

`transport._recover` 先持有 Connection/Credential，扫描没有先锁 Record，再由 native 依 History、排序写者锁、Record、Attempt 获取锁；未见新加入的反向锁序。ACK 解密时锁 Delivery 位于同 Connection 锁之后，同连接路径已串行化。恢复及候选 SQL 仍为普通一致性读；Connection 的 current read 不会重置已建立的快照。最终代码将已经失去 pending 状态的候选分类为 `LeaseUnavailable`，以锁内状态为准跳过；不靠旧查询结果派发。主任务的两个真实数据库进程先建立旧 RR 快照再同时领取，验证合计仅一个 Delivery、Attempt 和 `__Auth` 密文。该用例证明竞争安全，不证明一个旧事务总能发现其快照之后新提交的任务；新事务下一次 poll 才取得新发现视图。没有把这项范围限制写成全查询已改 current read。

初版 `transport.lease` 仅取 FIFO 第一条 pending；actor 撤权或冻结版本过期会回滚整个 poll，持续阻挡后面的合法任务。本审查向主任务报告后，最终 `transport.py:76–105` 改为每批至多扫描 50 个候选：仅捕获确定的 `DispatchRejected` 与权限拒绝，调用 `native.service.quarantine_pending:463–499` 保存 manual 和明确原因，再继续后续候选；数据库与未知异常不归类、不静默跳过。

隔离入口锁定后要求 pending、非 unknown、本记录不持有写者锁；若有旧 Attempt 必须仍绑定相同 Record/fence 且 finished，缺失指针也不能抹掉历史派发证据。不会隔离 leased/unknown，不释放其它记录持有的锁，不重新生成 payload 或改变目标。复现“激活 A→修改 A History→激活 B”已由主任务真实 Site 验证 A 落 manual、无新增 Attempt，B 正常获得唯一写者锁；另有撤权队首、拒绝隔离 leased 及未知异常整批失败的断言。因此原队首永久阻塞问题关闭，限定代码复核未发现新的确定缺陷。

本分工只读查看 `scripts/cowork-companion-transport-lab-verify.py` 及 `/tmp/pearnly-cowork-migration-20260920/eleventh-transport-native.txt`：主任务运行 **36 项通过，7.682 秒**。覆盖前述竞争、迟到 ACK、撤销/轮换/总闸、跨范围拒绝，以及密码加密、Delivery 插入、响应/刷新阶段失败后的完整回滚（含 `__Auth`）。本分工没有重复操作 Site；这些是合成原生环境证据，不是 Windows 或真实 ERP 验收。

最后追加限定复核：已完成的 waiting_lock/failed/rolled_back 回执曾让 Record 回 pending；之后因冻结版本或授权失效隔离至 manual 时，相同 ACK 仍应被确认，却不应再承诺重试。最终 `queue_contracts.ack_response:166–172` 先拒绝 unknown，仅在 `replayed is True`、manual、上述三种原 stage，以及四个精确隔离 reason 同时成立时返回 `{ok: true, status: manual, idempotent: true}`。`native.ack:553–566` 仍先核真实 attempt/fence/token，并校验 finished attempt 的 ACK 摘要；这一纯响应扩展不授新权限、不改原摘要、不重新入队或释放锁。其它有效源响应仍走原分支，没有扩大成任意 manual 幂等成功。

相关纯测试覆盖三种 stage × 四种 reason、unknown 优先拒绝、布尔真值精确性、原因尾随空格/未知值/其它状态，以及输入与隐私字段不透出。已读主任务真实日志 `eleventh-quarantined-ack.txt`：**1 项通过，0.664 秒**，用 waiting_lock 和 failed 两个子场景确认隔离后相同 ACK 返回最小 manual、Record 保持 manual、原 ack_digest 不变、未重新持锁。`eleventh-transport-additional.txt` 的另外 **3 项通过，1.087 秒**，验证同 Attempt 唯一 Delivery、原生 WSGI 拒绝通用访问及内部方法、11 个新术语 × 4 语言的 44 次解析。因此新增原生用例合计 40，子断言不另累加；未由本审查重复执行。隔离后重放是新平台有意扩展，单独记录，不混入固定源 **910 组相等 / 17 组已登记差异** 的原有差分数。限定终审未发现新的确定缺陷，也没有再次采集生产。

## 交付只读复核

仍使用开工时同一套已有 collector、参数和允许的 SQL 聚合，两个 collector 文件未改；没有增加生产查询类别。新文件独立创建，没有覆盖 start 或历史 evidence。

| 交付证据 | 实际 UTC | canonical evidence SHA256 | JSON 文件 SHA256 |
| --- | --- | --- | --- |
| `evidence/baseline-eleventh-delivery.json` | `2026-09-21T06:28:50.275588+00:00` | `cd4daa95eddaf628161dfc180565fa392cbb877fb1c2f0bba99c4472d1619494` | `df1de3f02ee2fba0acf4012f5a9e895d167e453223deaa865ef3e3a3acaf72ad` |
| `evidence/push-runtime-eleventh-delivery.json` | `2026-09-21T06:29:12.836519+00:00` | `a497892eebaa3184418ee52af637a34fd1f508ddd1f275eeeb4cdeef78f01505` | `b3548528e25a63546e59b296d9ff9eed8fe30dfa93f238841abf4699d0b8e520` |

第十一 start→delivery、第十 delivery→本次 delivery 的两组 source compare 都 PASS；两组 runtime compare 均为 `settings_unchanged=true`、`observations_changed=false`。Web/Worker 的流量 revision、digest、完整源码 SHA 以及已覆盖配置、聚合均未变化。两份交付证据已逐字归档并设只读，与 `/tmp/pearnly-cowork-migration-20260920/` 原件字节一致。

此结论只覆盖固定服务端来源及既有配置投影；不新增 Windows EXE 身份证明，不关闭第八→第九的旧数据 HOLD，不证明未实现的 History 镜像、库存计数、通知、公开激活入口及真实客户端/ERP 外写链。候选业务提交由主任务统一完成，本审查不自行提交。

本分工没有业务实现，未亲自运行业务或 Site 测试，也没有提交、推送、部署或客户验收结论；已完成本批授权的首尾取证、归档和限定独立审查。
