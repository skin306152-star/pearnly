# 第十二批：机器领取 / ACK HTTP 入口来源与限定审查

本分工负责既有范围的生产来源只读取证，以及本批 HTTP 鉴权和线协议的独立审查；不改业务、不访问生产 poll/ACK、不接 Windows 客户机或 ERP、不改 DMS。工作树为 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，开工实际 HEAD `8abf444b6ee953d485cee9e5f7691e6a47244965`，开工检查暂存区和工作树均干净。主任务所述 main `1a0585e` 不作为本分工的代码基线，也未由本分工操作。

## 开工 gate

**source 与 runtime 相对第十一批 delivery 均 PASS，可以继续新原生 Connection / 新凭据范围的本地 HTTP 接线。第八→第九批旧配置差异 HOLD 和真实 Windows 身份缺口继续保留。**

| 开工证据 | 实际 UTC | canonical evidence SHA256 | JSON 文件 SHA256 |
| --- | --- | --- | --- |
| `evidence/baseline-twelfth-start.json` | `2026-09-21T06:41:47.689309+00:00` | `07a3b0e7118fb60002618fc0f7fa28d849765f916bdea28366a07aad8a1b939d` | `5e3ae38f0c60777dd59431344093fc93c577a051b8eab579837867e2f46d96f2` |
| `evidence/push-runtime-twelfth-start.json` | `2026-09-21T06:42:16.015868+00:00` | `8fbb2f8d7491572a2ba057e343d2cb3f4658ffca5c7b646d2843424950392e6f` | `5633c4cb530222db5cbb7a75ad1a7c8a1714a9b7dcf0a8b0ae5240313b210fc3` |

环境仍为 project `pearnly`、region `asia-southeast1`。Web 100% `pearnly-web-ec2e5c957e6b-s3`，Worker 100% `pearnly-worker-ec2e5c957e6b-s3`；相同不可变镜像 `asia-southeast1-docker.pkg.dev/pearnly/pearnly-app/app@sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226`。成功 GitHub run `35321281376` 与固定部署 ledger 映射到完整源码 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，不是只依赖 revision 名中的短 SHA。

`baseline-eleventh-delivery` → `baseline-twelfth-start` 的 source compare PASS；对应 runtime compare 为 `settings_unchanged=true`、`observations_changed=false`。既有投影仍为全量 60 个 endpoint config 集合摘要 `5ee9e6604b3c6dee19ab3227d8484fad`，7 个 enabled Express 连接全部 legacy generation 0、managed 0。仅复用了已审计的 source/runtime collect-readonly，两个采集器自 `0e5cd94` 后未改；未增加查询类别。Secret 仅按固定数字版本在 collector 内存使用，凭据、路径、端点 ID 和业务 payload 不输出。两份证据逐字复制至 evidence，原件与归档均只读，没有覆盖历史文件。

当前稳定不解释第八→第九批全量配置摘要为何改变，不授权旧 owner、目录、默认账套、凭据或历史状态导入。已存 `companion_version` 是客户端声明，不是 EXE / 签名 / 构建到源码的闭环；本轮没有新增这类生产读取。边界沿[第十一来源审查](eleventh-source-review.md)和[第九 HOLD](ninth-source-review.md)。本节为开工证据，独立交付复采见末节。

## 固定来源与 HTTP 合同差异

固定来源路径相对 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。本轮已将下列三个文件逐字与 `git show` 的上述 SHA 比对：

| 文件 | SHA256 |
| --- | --- |
| `routes/erp_agent.py` | `bf253777feb90eeb2c3d9f0bccdc5715a49ac8026a8a9710105b77a8106044bb` |
| `services/erp/express_push/agent_store.py` | `03d86de7de82761f33f386913d3d32a93874953a237a8151e02c4e5e3000c9bb` |
| `services/erp/express_push/agent_lease_store.py` | `1279f909b3e9525f806994e6043a562bea22bfb2cfefe07dc34f7c4b7954ee22` |

| 合同 | 固定旧服务器 | 本批接线边界 |
| --- | --- | --- |
| 路径与响应 | `routes/erp_agent.py:294–298,352–371` 的 `/api/erp/agent/lease`、`/api/erp/agent/ack`，POST，直接 JSON | 两个精确 `/api/method/pearnly_cowork.companion.queue_api.{lease,acknowledge}`；原生 Frappe 响应封套。不能称旧 Windows 无需调整即可使用。 |
| 总闸 / 身份 | `:43–56,281–291,315–349`，总闸与 enabled 限制 lease 和 ACK，legacy token 失败才尝试 managed | 派发沿当前有效凭据/总闸/原 actor 授权；ACK 只收其历史 Delivery，允许撤销/停闸后的旧结果。此为第十一批明确保护扩展，不照搬旧入口将旧结果一并拒掉。 |
| owner / attempt | `:286,320` 的 agent_id/default；旧 legacy 没有单独 attempt token | 请求 agent_id 不能决定 native owner；私有 Delivery 绑定 credential、Attempt、fence，不能接受裸 Record 名替代句柄。 |
| 领取量与恢复 | max 默认 10、1..50；旧普通过期可能重领 | 单 Connection 写者锁限制至多一个未决作业；prepared 不自动激活，未知保锁。内部已落实的队首隔离与旧结果重放继续复用，不另造 HTTP 状态机。 |
| 校验与失败 | Pydantic 参数规整；旧 ACK 非 success/failed 返回 400，日志冲突转 409 | 新严格 JSON / UUID 等有意差异沿第十一合同；HTTP 层应给稳定状态和安全错误，不把数据库异常当空队列。 |
| 成功后的外层效果 | `:361–370` 安排 LINE 通知，agent_store 另镜像 History/库存计数 | HTTP 开放不代表这些未接效果已实现；仍分别列为缺口，不发送外部通知。 |

## 鉴权接线前的限定审查点

现有第十批 `http_auth` 只允许 heartbeat 的精确 POST。扩展应按有限方法表选择 dispatch/receipt，仍拒绝 v1/v2 别名、后缀、cmd/sid、非 POST、混合浏览器 session/cookie、override 或 Server Script 接管；不能按 companion 前缀普遍放行。`before_login` 必须早于 cmd=login 的会话副作用，私有 Bearer envelope 仍在原生 OAuth/API key 阶段前从请求头移除，Recorder、Cookie、no-store 和异常日志保护须覆盖三个精确入口。

已向主任务提示：ReceiptContext 原本不含 issuer，而原生 IP 限制在 auth_hooks 之后按当前 session.user 执行。历史回执应绑定历史 Credential 的原 issuer，保留原生 IP 校验；允许 disabled issuer 的窄回执不能通过 Guest / Administrator 代替、跳过 IP、恢复普通登录或赋予新领取权。Receipt context 必须仅可由 acknowledge 消费，不能被 heartbeat/lease 的通用 current_request 接受。任何本地数据库冲突重试都需要完整重验相应能力，不能把一个已失效 dispatch context 重用为授权。

本节记录接线前的限定设计检查；候选代码、真实 WSGI 验证与交付复采结论见下文，不仅凭设计意图报告通过。后续审查未扩展扫描或生产查询。

## 候选代码首次限定复核

已只读检查 `companion/http_auth.py`、`receipt_auth.py`、新 `queue_api.py`，以及对应 lease/ACK 原生 WSGI 脚本；没有改业务或运行 Site。`METHODS/PATHS` 是三个固定方法的集合，候选检测可以识别并拒绝别名/后缀，但真正 envelope 和私有 handler 校验仍只接受精确路径及 POST，没有开放任意 companion 前缀。

`_PendingRequest` 与 `_BoundRequest` 都绑定 operation、原 request 对象和真实对象 authority。`current_request(operation)` 同时核精确路径、操作、Site、session issuer 及上下文类型，ACK 限 ReceiptContext，其它两入口限 AgentContext；heartbeat 继续使用其默认 operation。因此历史回执身份不能用于新领取/心跳，也不能借普通人类 API key 或直接调用取得该私有能力。override/server script 在赋机器身份前按选定的精确方法检查。

`ReceiptContext.issuer` 来自被 Delivery 绑定的历史 Credential，且 revalidate 时再次比较；auth hook 仍对该原 issuer 调用 set_user，没有引入 Guest/Administrator 替身，也未更改原生 IP 调用链。最终 ACK 原生 HTTP 测试已验证 disabled issuer 原 IP 限制、角色撤销与全局关闭仅允许历史结果，证据见末段验证汇总；没有只凭测试源码声称 IP 验证通过。

`queue_api` 在服务调用内分别重验派发/回执能力，数据库冲突只允许最多三次完整本地重试；返回 job/ACK 前显式 commit，commit/after_commit 的错误不回到服务重试，不宣称 rollback 可证明外写未发生。现有 attempt secret 解密仍仅在内部 ACK，Permission/Validation/未知异常转为不触发原生 Error Log 快照的安全错误类型，并保留稳定 403/409/503。

发现并已由主任务修复一处确定残口：初版 `_run` 的 QueryDeadlockError 分支直接调用 rollback；rollback 自己失败时不会被同级 except Exception 捕获，原异常链可逃到 Frappe 错误处理，带出解密后回执 frame。最终 `queue_api.py:20–31` 对回滚失败单独转换安全 503，立即停止且不再运行服务。本分工用当前源码真实 `_run` AST 做零 IO 定向执行，故障轨迹为 `service → rollback → safe_busy`，返回的异常属于 AuthenticationError 子类，没有 commit 或第二次服务调用。此项代码残口关闭；生产凭据未用于测试，真实 WSGI/日志断言仍由主任务统一执行。

截至首次限定源码复核，未发现其它确定的权限或事务旁路；后续故障验证与修复结论如下，未将测试文件存在当作已运行。

## 最后错误响应复核

回滚失败已由主任务真实 WSGI 定向验证：解密后故障、显式 rollback 再失败时仅执行一次 ACK，返回安全 503，无 Error Log 快照或秘密响应，Record/Attempt/写者锁保持原状态；日志 `twelfth-queue-ack-edge.log` 中该用例通过。原生日志与接口响应是两条不同通道，不能只禁 traceback 就认为秘密已清理。

主任务随后用 `frappe.throw(secret, ValidationError/PermissionError)` 复现第二处泄漏：即使被换为安全异常类型，先前排入 message_log 的消息仍由原生 v1 `_server_messages` 序列化。已读该日志的两个失败子场景，并核最终 `http_auth._private_error`：清空 `frappe.local.message_log`、删除 `flags.error_message`，再由 `_deny/_deny_permission/reject_request/raise_busy` 输出稳定错误。对应锁定框架 `utils/response.py:200–209` 正是这两个独立响应来源；traceback/debug 仍由 disable_traceback 保护，不修改普通用户请求的消息机制。`twelfth-queue-ack-messages-fixed.log` 的同一真实用例 **1 项通过、0.647 秒**，覆盖 403/409 两子场景，没有改弱断言。

本次复核进一步报告同一消息通道的成功重试分支：若第一次 QueryDeadlockError 经 frappe.throw 排队消息，rollback 并不清 Python 的 message_log，第二次成功后仍可能输出旧 `_server_messages`。真实 `_run` AST 零 IO 复现了两次 service、成功结果但保留原生消息。主任务已新增 `clear_private_messages`，在 auth retry、queue retry 的 rollback 成功后，以及 queue commit 成功返回前清除两条提示通道。本分工再次对当前真实 `_run` AST 注入首次秘密提示、error_message 和 commit 后新提示，轨迹为 `service → rollback → clear → service → commit → clear`，成功时两通道均为空。最终真实 ACK 组已验证重试前消息为空、最终响应无秘密、仅一份成功 Attempt；另有三种入口的认证成功重试用例通过。该项实现及本地验证均已关闭，不由首尾生产 gate 代替。

已读 `twelfth-queue-http-final.log`：15 项真实 WSGI 测试通过（6.098 秒），包含原生 message 封套、精确方法/别名拒绝、同请求操作隔离、当前权限、提交前后故障与两进程旧快照竞争。该数字不叠加早期重复运行；全部原生最终汇总仍由主任务统一记录。

已读 ACK 首轮汇总 `twelfth-queue-ack-http-final.log`：23 项中有 4 项在合成 fixture 阶段触发 `QueueOverloaded`（650 个积累的后台任务），该轮没有全通过。主任务报告先核对归属，再仅删除本实验 push Site、create_contact、用户为 `cw-*@example.invalid` 的 454 个辅助任务，保留其它 196 个（含其它 lab Site）；其 ID 集合 SHA256 为 `56b078bc884312116cb0f6c1938fed11f69037d8346b0101126cd242d0162bb1`。该操作由主任务执行，本分工没有独立操作或重做清理。已只读核对 fixture 修正：仅合成建用户及改 API secret 时临时使用安装/导入 flags，同步创建 Contact，进入真实 WSGI 前已退出局部 flags 并 destroy 旧请求上下文；没有给受测 HTTP 开绕过标志。

最终实际日志 `twelfth-queue-ack-http-complete.log` 为 **24/24 通过，7.772 秒**，包含最初未完成的用例与新增成功重试消息清理。`twelfth-auth-retry-messages.log` 为 **1/1 通过，0.507 秒**，其三个子场景分别运行 lease、heartbeat、ACK，在认证第一次 `frappe.throw(token, QueryDeadlockError)` 后第二次成功，响应无两种原生提示字段。连同上述 15 项，共 **40 项新增 HTTP 方法级用例**，重复运行与子场景不累加。本分工核对日志及断言，没有自行重复 Site 测试；剩余共享 heartbeat/transport/credential/collector 回归由主任务统一报告。限定最终审查没有遗留的确定代码缺陷。

## 交付只读复核

既有两个 collector、参数及 SQL 投影范围未改；未访问生产 poll/ACK、未增加查询类别、未取客户 payload/端点 ID/路径或输出秘密。以下文件独立新建，与 `/tmp/pearnly-cowork-migration-20260920/` 原件逐字相同，归档和原件均设 0444，不覆盖开工或历史证据。

| 交付证据 | 实际 UTC | canonical evidence SHA256 | JSON 文件 SHA256 |
| --- | --- | --- | --- |
| `evidence/baseline-twelfth-delivery.json` | `2026-09-21T07:07:18.269884+00:00` | `f0ce597a73ab5a0f2e31a959e1ed9f7ed9c820589632fe5e6594340f59487e0a` | `dd451070db58038fc091b9cdcd973d6565c1bd7eaf91f53e0d2843e51d11120c` |
| `evidence/push-runtime-twelfth-delivery.json` | `2026-09-21T07:08:23.175341+00:00` | `7e3cde2853d973f80469b25c043af2c63739c7df6a058f99b71ee2d2e3e997c3` | `57ef1d3f9745a368c28d158abcf968b6468be79c91f2808569d035374f579adc` |

第十二 start→delivery、第十一 delivery→本次 delivery 的两组 source compare 均 PASS；两组 runtime compare 均为 `settings_unchanged=true`、`observations_changed=false`。实际流量 revision、不可变 digest、完整源码 SHA、已覆盖配置和聚合观测均未变化。此结论限于现有采集范围，继续保留第八→第九旧数据 HOLD；不证明 Windows 客户端能直接理解新 URL 或原生 message 封套，不代签外部 ERP / 真机验收。

本分工仅新增本批四份证据及此审查文档，没有业务实现、Site 写入、提交、推送或部署。
