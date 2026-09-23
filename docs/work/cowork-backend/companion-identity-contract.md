# Companion 服务端身份底座与协议边界（第九批）

本记录保存固定旧源码中的 token、heartbeat、poll、ACK 调用合同。本批已实现本地凭据发行、默认 exists、明确 reset、撤销、脱敏状态及内部认证/当前权限复验，只有人类管理 RPC。**没有 auth_hooks、heartbeat、目录消费者、推送领取/ACK 或客户端；`exp_` 不能登录或调用普通 RPC。** 第八批 attempt fencing 保持不变。

## 当前证据与工作归属

- 记录日期：2026-09-21；工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`；本轮 HEAD `e23b17afa0e881d2c89de7fe930c17d26358c3f5`。
- 写本文前的工作树**不是干净状态**：`docs/work/2026-09-20-cowork-backend-migration.md` 已被主任务修改；另有其它分工的未跟踪 `docs/work/cowork-backend/ninth-source-review.md`。本分工不修改、暂存或清理其它分工的文件；实现限于下列独占路径。
- 主任务第九批源码 gate 通过，源码仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。窄身份 v1 摘要两服务均为 60 行/hash `67c022c7edb856033715c51bacf02b22`，与第七批相同；补充证据 checksum `d313d78c34621288ae9e6d31dad2006ca4ac2a7615a495e8eed51c216840b28f`。覆盖 endpoint ID、adapter、enabled、binding generation、token hash；**不覆盖旧 user_id/express_root，完整目录 v2 的漂移仍 HOLD**。本批是新 Site 本地身份合同，不迁入旧发行人或根目录。
- 固定只读目录：`/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。以下函数与 SHA256 均从该目录读取；没有访问生产、客户机、外部业务或运行 Site。
- 第八批运行证据曾记录现存 60 个端点全部 generation=0，managed 数量为 0。这是上一批采集时的聚合，不是本轮重新确认的在线状态；managed 功能开关开启及源码存在，都不能证明已有实际 managed 客户端。
- 本文依赖方向：身份归连接域，目录采集只消费已认证的机器上下文；推送状态仍由 `push.native` 的 Record/Attempt/Writer Lock 唯一持有。此切片不反向把推送流程放入身份或目录模块。

## 固定来源与校验值

除首行外，文件路径均相对旧仓 `services/erp/`。

| 来源文件 | SHA256 |
| --- | --- |
| `routes/erp_agent.py`（相对旧仓根） | `bf253777feb90eeb2c3d9f0bccdc5715a49ac8026a8a9710105b77a8106044bb` |
| `express_push/agent_store.py` | `03d86de7de82761f33f386913d3d32a93874953a237a8151e02c4e5e3000c9bb` |
| `express_push/agent_reporting.py` | `c1ff3a33724ffb302c94e7c4ee44a1ecd7ba7753ca71c35f72a09c3247bf3a65` |
| `express_push/agent_account_sets.py` | `4a8861d46e748349e0a6b24e1b79b3d6ee404d6426505285476f5d9f2f4bb5a8` |
| `express_push/agent_lease_store.py` | `1279f909b3e9525f806994e6043a562bea22bfb2cfefe07dc34f7c4b7954ee22` |
| `express_push/connection_identity.py` | `0dda45c56bddbb853594d3c7f98def7ab33befcae1df604912be4e4094a7d747` |
| `shared_express_agent_auth.py` | `01e05889facda13f67dd413bcf55973c61858a093b3d7890744ad660e8b86fe5` |
| `shared_express_agent_queue.py` | `f7d5484d01d4fac2d1fce001fd21b428b0c98f82f5b4e2ad879a7c5c7bfa11ab` |
| `shared_express_live.py` | `4929d475deb100e721a947071ba11185296ea8342b534bf10645cc671fbedb20` |
| `express_target_projection.py` | `04cc019472c3bcdf5a776507c24ece1cd29f920f3062f64409a2a9c6008dc478` |
| `target_refresh.py` | `33eb4ee62e0d762965c83adcbd6c100edcc50f1649c7aa94d44d1099ba0a53ff` |

## 实际入口合同

| 入口或函数 | 来源位置 | 必须保持或明确登记的语义 |
| --- | --- | --- |
| `_require_enabled` | `routes/erp_agent.py:43-45` | 旧 `ERP_PUSH_ENABLED` 关闭时所有四类路由均 404；未来 ACK 接收与停用的区别必须沿第八批已有决定明确登记，不能默改。 |
| `erp_agent_token` | 同文件 `191-219` | 网页会话、ERP 入口及 push 权限；默认不旋转；reset 明确废旧铸新；已存在只回 exists/tail，明文仅首次返回。 |
| `set_agent_token`、`authenticate` | `agent_store.py:68-120` | `exp_<endpoint_id>_<token_urlsafe(32)>`；只存整串 SHA256 和尾四位；`compare_digest` 比对。默认 token 长效，没有按 heartbeat 自动续期/换密钥。 |
| `get_express_endpoint` | `agent_store.py:44-65` | legacy 认证只查 adapter=express 且 binding_generation=0。发行 helper 本身没有相同 generation 条件，不能据此混用 managed 的发行/绑定生命周期。 |
| `_run_agent_heartbeat` | `erp_agent.py:117-187` | 先 legacy authenticate；失败才进入 managed heartbeat。有效但停用的 legacy 端点返回 403，不回落其它身份。 |
| `erp_agent_heartbeat` | `erp_agent.py:223-240` | POST，Bearer 头；旧客户端空 body、非法 JSON 或非对象 body 按空对象处理。不能漏迁其后完整的目录/配置数据处理。 |
| `connection_identity` | `connection_identity.py:13-25` | 机器可读安全身份只有 endpoint_id/name、所属 Pearnly 账号；不回连接配置、token hash 或目录原始数据。新平台账号展示需要当前已授权用户映射，不能读取任意 User。 |
| `erp_agent_lease` | `erp_agent.py:243-298` | max 默认 10、1..50；legacy agent_id 空值回退 default；返回 lease_seconds=120、jobs 及可选 master_refresh。实际账套判据来自授权集合与 root，文件头 DATAT 注释不能替代运行规则。 |
| `erp_agent_ack` | `erp_agent.py:301-370` | result 仅 success/failed，否则 400；有效 outcome 优先。失败返回对应 409/managed 错误码；成功可触发后续通知分发。 |

### 心跳不只是在线状态

legacy 心跳按真实调用顺序处理：

1. offline 为真时直接置离线并返回，不刷新 last_seen、不接收目录、不领取 refresh。普通心跳使用服务端 UTC 更新 last_seen，设备名最多 60 字符。
2. `account_sets` 出现且非 None 才写整组账套；`agent_account_sets._sanitize_account_sets` 最多 50 行，并保留每账套 mapping、root、path、writable 等。`accounts` 同理，最多 3000 行，code 必填，已知字段最长 120。
3. 六项科目 mapping 只合并非空码，最长 40；空上报不能清掉已存映射。所选账套整组要求 account_set 非空，只合并报告到的非空字段，包含 account_dir/company/row/express_root。
4. catalog 必须为对象才处理；商品/客户分别最多 20000 行，已知字段最长 200。catalog 出现时缺 products/customers 按空数组替换；空 fingerprint 保留旧 fingerprint；isacc_candidates 缺席不清旧候选。
5. max_payload_version 仅可转换为 >=1 的整数才写；legacy companion_version 非空且不超过 20 字符才写。它们是客户端声明，不是已验证运行 EXE 的身份。
6. `express_target_projection.ingest_express_heartbeat` 发布目录投影并处理 refresh 回报；然后可能附加下一份 master_refresh 请求。函数 `241-265` 明确区分缺 account_sets、明确空扫描和缺 catalog；空扫描 `294-322` 记录 ERR_ACCOUNT_SET_EMPTY，不伪造新鲜空目录。

来源旧 reporting helper 多次独立 commit 且不少异常被转换为 False/0；因此 heartbeat 的 `ok=true` 不能单独证明全部目录已成功保存。新平台应将组件状态、缺席/空值和完整性显式保存；若改为同事务失败回滚，必须作为有意变化登记，不能把旧失败吞掉再声称等价。

目录/refresh 详细迁移由对应分工负责：`target_refresh.py` 的请求、领取及完成不是单纯创建 requested 行；包括 endpoint/account scope、过期、supersede 和回报匹配。当前原生 Connection/Snapshot/Refresh 可复用，但浏览器或未认证 body 不能直接调用可信 install seam。供应商/银行/服务端 mapping 等准备来源与 Agent 目录报告分开，不能自动把 heartbeat 标成 preparation.complete。

### legacy 与 managed 不能合并认证

- `shared_express_agent_auth.py:33-63` 的 managed token 解析要求标准 UUID、允许的 secret 字符、总长 <=1024、固定 64 位十六进制摘要；这与 legacy 仅拆 `exp_` 的 parser 不相同。
- managed heartbeat 的 `shared_express_live.py:134-199` 走专用认证/范围上下文和 generation CAS，记录 live_account_set/profile、版本、服务端时间，并返回 unbound/ready/mismatch/needs_attention/offline。只看到 connected=true 不等于 profile_ready=true。
- managed queue 的 `shared_express_agent_queue.py:96-152` 另查租户 active/warning、公司 active、shared_scope、未撤销、generation>0、bound/live profile 相等；心跳年龄必须满足 `-5 <= age < 180`。这是 managed 派发合同，不能给 legacy 虚构此证明，也不能只靠一个“managed 已启用”开关跳过。
- 上一批未观察到 managed 实际端点，只能作为优先切片依据，不能据此删除 managed 功能或将其静默降为 legacy。本段身份切片若仅实现 legacy，managed 必须明确未迁。

## 为什么本段不能直接开放旧 poll/ACK

旧 legacy job 的 log_id 是原始日志 ID，ACK 只带同一个 log_id 与 agent_id；`agent_store.ack` 校验 endpoint 和 lease_owner，没有独立 attempt secret。普通租约过期允许重领，其重复写保护依赖客户端本地 find_existing；duplicate_confirmed 则例外，不重投，超时落人工。

managed 在 `shared_express_agent_queue.py:59-93` 使用 `m1_` 句柄：version + UUID log_id + uint64 attempt，通过 token digest 派生 HMAC 密钥并绑定 endpoint，截取 16 字节签名。`ack_managed` 还以 owner/attempt/generation/status 做条件更新。它只是固定源中另一条服务器协议，不能证明旧 legacy 客户端已支持新平台任意 log_id。

第八批原生账本对所有分支都要求独立 fence + 随机 attempt token。直接把 legacy 裸 log_id 映射到“当前 attempt”，或仅凭 agent_id 填入内部 trusted_adapter_context，会允许旧回执作用于新尝试，属于保护倒退。

后续只能选择并验证一种方案：新协议显式携带 fence/attempt token；或能保持这两项保护的 opaque job handle，并证明实际客户端仅原样回传且不会按 UUID、长度或数据库列类型解析。服务器 AckRequest 把 log_id 标成 str，不足以证明客户端二进制兼容。当前不选定、不实现这条运输桥。

## 已实现的原生身份底座

只新增 **Cowork Express Agent Credential**，一次发行一行，保留轮换与撤销记录，不新增真实平台 User：

| 字段组 | 已实现内容/约束 |
| --- | --- |
| 归属 | Connection、workspace/client、issuer User、branch、binding_generation；由服务端读取，不接受机器 body 改归属 |
| 凭据 | token_generation、整串 token SHA256（唯一）、tail4；不存明文或 Password 副本 |
| 当前有效性 | active_key 为规范 Connection 身份的哈希且 unique；撤销置 NULL，Connection 锁内保证同连接只一份 active 凭据 |
| 审计 | issued_at/by、revoked_at/by/reason、发行 request key 摘要；旧发行记录不删除 |
| 幂等 | 每代保留 request_receipts；只存 request/intent SHA256 和结果，不存请求明文，旧代回执不会因 reset 丢失 |

默认再次请求应返回 exists/tail，不旋转；reset 在同一 Connection→Credential 事务里废旧建新。一次性明文若网络返回丢失，幂等重放不能悄悄再生成新 token；应只返回已存在，另一次明确 reset 才换密钥。发行/重放 DTO 不包含摘要或冻结配置。token 形式可沿 legacy `exp_<native connection name>_<secret>`，但原生名称不是旧 UUID；这是原生身份映射的有意变化，不作为 managed 或旧客户端兼容证明。

内部机器上下文固定：`AgentContext(site, connection, issuer, credential_id, token_generation, branch, binding_generation, private_object_capability)`。普通 dict、dataclass 字段、session 用户或 `authorized=True` 都不是采集授权。目录服务消费这个上下文，不复制 token 认证规则。

### Frappe auth_hooks 的受限桥

主任务已只读核对候选 Frappe：Bearer 认证结束仍为 Guest 时请求失败。因此拟采用 auth_hooks，仅对指定 heartbeat RPC 做机器身份桥；这仍是设计，没有安装 hook。

- 只允许精确 heartbeat RPC 路径和 POST；`exp_` 出现在其它路径、其它 HTTP 方法必须明确拒绝，不能因请求同时带有效 cookie 而跳过校验。其它产品、CRUD、通用 RPC、API v2 均不继承这一凭据。
- 拒绝 query/form/JSON 的 cmd 覆盖及替代路由；实际执行的 handler 必须就是已验证路径。token 只来自 Authorization，不能增设 body/query token 后门。
- 认证后仅 request-local `set_user(issuer)` 通过框架身份闸；不使用 Administrator，不创建登录 session、不发行 Cookie，不修改长期登录状态。
- heartbeat handler 必须另查真实对象能力 AgentContext。只有普通 cookie/API key、即使用户有业务权限，也不能假装已认证机器提交目录。
- 写前在 Connection→Credential 锁内重查当前 key/generation、启用/撤销、issuer 的产品能力/角色/公司客户范围/显式 allowed_users，防止 auth 到写入之间的轮换或撤权。issuer 不从 body 或请求用户名取得。
- 发行入口走既有明确管理权限；机器 heartbeat 不获得通用 DocType 写权。凭据表同样拒绝 native CRUD/read/share 回退，日志、异常和 DTO 不输出 token 或 digest。

未来受限身份桥仍需验证精确路由、cmd 覆盖、混合 cookie、API v2 和其它 RPC/CRUD 拒绝，以及 heartbeat 缺席/空值、离线、版本观测和 refresh 回报。它们不属于本批身份底座的已实现/已验证范围。

## 未实现、未验证与交付界限

本批完成凭据服务与内部 authenticate/revalidate；完整身份桥、auth_hooks、heartbeat、refresh 消费者仍未实现，目录 v2 漂移范围仍未获移植放行。没有在线观测字段，不以 token active 状态冒充设备在线或可派发。

真实 Windows 运行 EXE 的版本/SHA256、发布物到 Companion 源码的映射、设备身份、客户端路径限制/写者锁、DBF 备份与回滚、索引重建、prior_docnum/find_existing 和业务回读仍未闭合。客户端自报版本、模拟 heartbeat、HTTP 200 或本地 Site 测试都不能替代这些证据。

本段不开放 push poll/ACK、不实现客户端、不访问生产、不进行外部写入、不部署。History 状态投影、库存画像计数和通知等上一批登记的剩余 ACK 副作用，也不会因本次保存身份文档被标成已迁。


## 当前接口、落点与权限

| 入口 | 参数与返回 | 当前边界 |
| --- | --- | --- |
| `companion.api.issue` POST | connection、request_key、reset=False、expected_generation=None；仅新发行返回一次 token | 默认 active 仅 exists；reset 必须精确 expected_generation；同 request/actor/connection 变更 intent 拒绝 |
| `companion.api.revoke` POST | connection、request_key、expected_generation、reason='' | 当前管理权限；关闭连接/总闸后仍可撤销，不要求已被取消的 use grant；旧代成功撤销回执重放不撤销新代 |
| `companion.api.status` GET | connection | 只返回 generation、active/revoked/missing、tail、issuer、时间和连接 enabled；不含 token digest/config/目录 |
| `companion.credentials.authenticate` 内部 | token → 私有对象能力 AgentContext | 不 whitelist、不修改 session，不代表注册机器 RPC |
| `companion.credentials.revalidate` 内部 | AgentContext → 当前上下文 | 写前重锁连接和凭据，复查当前 issuer、范围、显式授权和代数 |

实现位于 `apps/pearnly_cowork/pearnly_cowork/companion/` 的 contracts/documents/credentials/api；原生结构位于 `pearnly_cowork/doctype/cowork_express_agent_credential/`。pure contracts 只依赖标准库；service 单向消费现有 connections.access，不导入目录或 push 业务。主任务登记 has_permission、permission_query_conditions 与 DocShare validate，默认 Frappe CRUD/read/share/rename/delete 即使 Administrator 也无权绕过专用服务。预先种入有效 DocShare 会拒绝插入该凭据；权限全零的历史留行不阻止。

发行人必须同时有真实 Cowork Admin、master_data/intake 功能、启用账户/公司/客户、连接 enabled 和 allowed_users 显式使用授权。管理员角色按现有 Cowork 范围规则拥有公司管理权，User Permission 的减少本身不会撤销仍有效的 Cowork Admin；公司/客户停用、角色删除、User 停用、连接使用授权撤销会即时使内部认证失败。`Administrator` 的框架超级用户身份本身不代替真实 Cowork Admin 角色。

`pearnly_cowork_erp_push_enabled` 的本平台既有规则只接受 True/1；issue/authenticate/revalidate 每次检查。status/revoke 在关闸后仍允许当前管理者查看与撤销，这是明确的撤销可用性改进。managed/generation>0、非 Express、非 Cowork Site 均拒绝，不默降分支。

## 耐久、并发与有意变化

- Connection→Credential 当前读和数据库唯一键保证每连接单 active、一代单记录。独立 token_generation 不复用连接配置 revision 或 binding_generation。reset 将旧代撤销与新代插入放在同一事务；插入失败全部回滚。服务内部无 commit/整事务重试；管理 API 只对本地 QueryDeadlockError 做最多三次完整回滚重试。
- request 操作身份包含 Site/actor/connection/request_key；不同代的回执均可查回。默认 exists 新别名也持久化，避免未来 reset 后老请求意外发行。receipt 仅 SHA256 intent/result，不存原 request_key 或明文。每代上限 1 MiB，发行/exists 提前保留 1 KiB，确保首次撤销及永久回执不会被别名占满阻断。已撤销后无界追加新撤销请求仍可能达上限，但不会复活凭据。
- token 摘要沿旧 SHA256/constant-time 比对；本地 `CWEC-...` 名称替代旧 endpoint ID，解析字符和大小有界。不是迁入旧密钥，也不承诺旧 Windows 客户端接受新名称；实际二进制仍待证明。
- 所有凭据表字段服务端只写；track_changes=0，save 显式 ignore_version=True，避免摘要和回执复制到 Version。管理响应设置 cowork_private_response，由现有 after_request 生成 Cache-Control: no-store、Pragma: no-cache 和 nosniff；错误/重放也设置。无消息发送或 token 日志。

## 可复现验证

离线命令（工作树根）：

```sh
PYTHONPATH=apps/pearnly_cowork:tests python3 -m unittest test_cowork_companion_credentials -v
python3 scripts/check-architecture.py
```

本分工已执行：16 项纯合同测试通过；架构检查 0 violations；新增 Python 语法通过。主任务另外执行固定源 hash/auth 244 个差分样本，结果由主记录绑定候选证据。

真实 Site 脚本为 `scripts/cowork-companion-credentials-lab-verify.py`，只能在带 `.cowork-backend-lab` 标记、指定 `PEARNLY_COWORK_BACKEND_LAB=1` 与 `PEARNLY_COWORK_TEST_SITE=cowork-backend-companion.localhost` 的独立 bench 内运行。它会写合成用户、公司、连接、凭据并保留有标签的实验数据；并发与 WSGI 用例显式提交，HTTP 用例临时改本站测试配置后恢复。无外部网络、无心跳、无真实 ERP。覆盖 20 项：默认/重放/CAS 轮换、撤销、撤权、总闸、假上下文、跨连接、native CRUD/share、无 Version、回执满时撤销、故障回滚、并发 RR 快照与真实 WSGI。脚本已完成，Site 运行由主任务统一执行；其结果以主记录为准。
