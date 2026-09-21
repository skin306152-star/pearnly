# 第九批：线上来源与运行配置核验

本记录只覆盖只读取证与独立审查。工作树为 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，开工 HEAD 实际核对为 `e23b17afa0e881d2c89de7fe930c17d26358c3f5`，采集前正常及暂存区均无改动。没有操作主目录、生产部署、外部业务写入或客户机；本包拥有本文、`scripts/cowork-push-runtime-drift-audit.py`、`tests/test_cowork_push_runtime_drift.py` 及 `/tmp` 下的新证据文件。

## 开工 gate 结论

**源码身份比较通过，完整运行配置身份比较未通过。补充 v1 同口径核验仅放行端点/token 认证身份子切片；目录及其余配置范围继续暂停。**

来源采集证明 Web/Worker 仍运行第八批相同源码与镜像。但两个服务的端点配置身份集合摘要同时变化；不能因为源码未变，沿用第八批“相关运行配置未变”的结论，也不能在没有明细证据时将变化解释为心跳。

## 已取得的证据

| 项目 | 第九批 start |
| --- | --- |
| 生产环境 | GCP project `pearnly`，region `asia-southeast1` |
| 来源核验时间 | `2026-09-21T04:38:30.493234+00:00`（UTC） |
| 来源文件 | `/tmp/pearnly-cowork-migration-20260920/baseline-ninth-start.json` |
| 来源 checksum | `1bf8b32e0d4e84128a3b8641450e40923b5bfc1470c1d76fda5a5d11607506c7` |
| 源码 SHA | `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188` |
| Web 实际流量 | 100% `pearnly-web-ec2e5c957e6b-s3` |
| Worker 实际流量 | 100% `pearnly-worker-ec2e5c957e6b-s3` |
| 共同不可变镜像 | `asia-southeast1-docker.pkg.dev/pearnly/pearnly-app/app@sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226` |
| 映射依据 | GitHub run `35321281376` 的 completed/success/head_sha、固定 Git 部署账本、两实际 revision digest 相互一致；不靠 revision 名称里的短 SHA 推断 |
| 固定只读源码 | `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b` |
| 运行配置核验时间 | `2026-09-21T04:38:57.788778+00:00`（UTC） |
| 运行配置文件 | `/tmp/pearnly-cowork-migration-20260920/push-runtime-ninth-start.json`，schema v2 |
| 运行 checksum | `1e3089d071dd49a9973651cb376dbe71e1d66ce690335568109e57da95aa5dc7` |

重新阅读了既有采集器后执行 `cowork-migration-baseline.py collect-readonly` 与 `cowork-push-runtime-baseline.py collect-readonly`。来源只读 Cloud Run/GitHub 元数据与固定 Git 账本；runtime 使用同一来源证据固定的数字版 secret 引用，payload 仅在进程内解析，不输出或落盘秘密。数据库事务为 READ ONLY / REPEATABLE READ，8 秒 statement timeout，结束 rollback/close，不执行补表。两采集器均执行首尾流量核对，没有覆盖既有证据。

## 与第八批交付的实际差异

来源 compare 对照 `baseline-eighth-delivery.json`（checksum `61ab9d88c93942de9a42bbedd1e5bd7d2a0f62a1e1f726c6928bdfe9b1e1e640`）返回 PASS。运行 compare 对照 `push-runtime-eighth-delivery.json`（checksum `54e821dd71557b0360c96d546ab9c513512526c9093ae344a60a2800de6eb699`）返回 `push_runtime_identity_changed`。

随后对两份 runtime 证据的整个 `components` 进行离线递归比较，**唯有以下两个叶字段不同**：

| 路径 | 第八批 delivery | 第九批 start |
| --- | --- | --- |
| `pearnly-web.database.identity.endpoints.configuration_digest` | `461d6540e21d14f3a41443ad720d5605` | `5ee9e6604b3c6dee19ab3227d8484fad` |
| `pearnly-worker.database.identity.endpoints.configuration_digest` | `461d6540e21d14f3a41443ad720d5605` | `5ee9e6604b3c6dee19ab3227d8484fad` |

端点总数仍为各 60；两服务当前摘要相互一致。其余组件字段逐项相等：env 白名单、secret 引用与存在标记、动态开关和 allowlist 摘要、缺列记录、bridge identity，以及完整 endpoint/push log/bridge/bridge job observations。这里的“相等”仅指既有脱敏投影覆盖到的字段，不等于生产没有发生任何其它变化。

此摘要包含端点身份列（endpoint/tenant/workspace、adapter、enabled、generation、shared/revoked、bound account/profile、agent version）及完整 `config` JSON。摘要变化只能定位到该集合，不能区分配置值、目录/协议缓存、凭据或身份映射中的哪一项变化。旧证据未保存逐字段或逐 key 摘要，不能从一个旧 hash 反推出变化键。滚动 observations 相等也不足以证明该差异不影响本批行为。

## 已执行的最小补充调查

root 在完整 gate 失败后授权独立只读补查；没有改写第七、第八或第九批原 start 证据。实际补采范围只包含精确 v1 集合摘要、当前 v2 集合摘要、adapter 数量及 `updated_at` 辅助聚合，没有追加读取 config 值、端点 ID、企业名称、业务路径或票据。

| 项目 | 补充证据 |
| --- | --- |
| 文件 | `/tmp/pearnly-cowork-migration-20260920/push-runtime-ninth-drift-v1.json` |
| 时间 | `2026-09-21T04:49:00.283076+00:00` |
| checksum | `d313d78c34621288ae9e6d31dad2006ca4ac2a7615a495e8eed51c216840b28f` |
| 对照原 v1 | `push-runtime-seventh-start.json`，checksum `111119d806e9daed01724baeec825453432078020717e21d484a4a7fe723573a` |
| 精确 SQL UTF-8 SHA256 | `9c215511d04f442259f459c926ec3ed8047521318d3774683a53373ccd0c82c7` |
| 两服务当前 v1 | 均为 count `60`，digest `67c022c7edb856033715c51bacf02b22`；与原 v1 相等 |
| 两服务当前 v2 | 均为 count `60`，digest `5ee9e6604b3c6dee19ab3227d8484fad`；仍与第八批 delivery 不同 |

v1 SQL 与键顺序从本任务原始 `Add File` 工具输入恢复，逐字核对后再运行；没有从 v2 猜测 JSON 键或序列化顺序。恢复记录为本代理 session `rollout-2026-09-20T23-08-04-01a0bf93-1d72-7921-9ddc-87bfd360659a.jsonl` 第 2752 行的原始创建工具调用；回归测试固定该 SQL 字节 hash 及键顺序。

v1 覆盖的列只有：`id/tenant_id/workspace_client_id/adapter/enabled/binding_generation/shared_scope/revoked_at IS NOT NULL/bound_account_set/bound_profile_key/agent_version`；config 键只有 `agent_token_hash/method/account_set/account_dir/account_root/system_url/comidyear/seldb/max_payload_version/companion_version/mapping`。相等结论允许端点 ID、adapter、enabled、generation、token hash 所依赖的认证子切片沿用来源；**不覆盖 `user_id`、`express_root`、其它 config 键，也不证明全部 owner、目标、目录或凭据内容未变。** 这仍是 SQL 集合摘要比较，不是逐行明文核对、端点来源导入或客户机版本证明。

全量 60 行实际分布为 Express 7、FlowAccount 1、MRERP 7、`mrerp_dms` 45；原 v1/v2 `FROM erp_endpoints e` 无 Cowork 条件。旧 observations 把未在 allowlist 的 `mrerp_dms` 归为 `other`，补采 adapter 聚合才明确显示它。不得用全量摘要变化直接归因到 Cowork，也不得因为 DMS 行多而排除 Cowork 变化。

`updated_at` 列存在且类型为 `timestamp with time zone`。以第八批 delivery 的 `2026-09-20T21:44:20.181640+00:00` 为截止，Express 有 1 行 updated_at 较新，最早/最晚均为 `2026-09-21T01:15:29.763365+00:00`；其它 adapter 为 0。**该列不是完整更新日志，不用于确定变更来源或排除变化。** 新脚本对缺列、无时区列或非时间列仅记录不可比较，不猜测时区。

固定源码中能改变 v2 config 摘要、而不必改变现有 observations 的明确路径：

| 固定源码位置（均相对 `legacy-ec2e5c957e6b`） | 更新内容与证据边界 |
| --- | --- |
| `services/erp/express_push/agent_reporting.py:424`（SQL 434–448） | 心跳更新 `agent_last_seen_at`，可更新 device；不维护 `updated_at`。已有心跳存在标记可保持不变。 |
| `services/erp/express_push/agent_account_sets.py:67`（SQL 75–79） | 整体替换 reported account sets 与 seen_at；不维护 `updated_at`。 |
| `services/erp/express_push/agent_reporting.py:51`（SQL 64–68）、`:150` | 科目与存货科目组及 seen_at；不维护 `updated_at`。 |
| `services/erp/express_push/agent_reporting.py:209`（SQL 223–230）、`:370` | 商品/客户目录、catalog fingerprint/seen_at，及成功 ACK 后 stock master count；不维护 `updated_at`。 |
| `services/erp/express_push/agent_reporting.py:259`（SQL 267–269） | 顶层 config patch；不维护 `updated_at`。 |
| `services/erp/push_store.py:257`（SQL 267–269、307–312） | legacy endpoint 的通用 config 替换；也不自动维护 `updated_at`。 |

上表只证明存在多种合理变更路径，没有证明线上此次走了哪条。完整 v2 漂移仍未定位。原先提出的按 config 类别细分摘要/key 存在与类型调查未执行；即使未来补采，也只能成为新的时点基准，不能从旧全量 hash 反推历史分组摘要，不能改写第八批覆盖范围。

## 诊断脚本验证

11 项零云端、零业务 DB 定向测试通过：精确原 v1 SQL/键顺序绑定、只读事务/参数化/rollback/close、异常脱敏、只允许聚合投影、缺失或无时区 updated_at、v1 相等但保留 v2 差异、v1 不同不宣称通过、首尾流量漂移拒绝、组件快照不同拒绝、篡改输入拒绝、输出防覆盖。补采后新增的投影校验又对既有补充证据离线验证；没有为重复验证再次访问生产。`python3 scripts/check-architecture.py` 通过，0 violations；本包三文件 whitespace 检查通过。

## 身份子切片独立只读审查

审查范围为 `companion/{contracts,credentials,documents,api}.py`、Credential DocType schema、其 native hooks 及既有 `connections/access.py` 的当前权限入口；固定来源对照 `agent_store.py:68–120`、`routes/erp_agent.py:43–56,191–219`。这里只审新凭据底座，不扩到仍 hold 的目录、heartbeat、poll 或 ACK。

发现并由身份实现作者修正两处确定问题：

1. 旧 token 发行有全局推送开关，新 `issue/authenticate/revalidate` 初版未检查。现三入口第一步均调用 `_enabled()`；独立提取实际 AST，以关闸 fake 配置运行，均在任何数据库或业务 helper 前拒绝。`status/revoke` 保持可用供管理者查看与撤销，这是明确有意变化，不是旧全接口 404 的逐项等价。
2. 初版发行别名可占满全部 1 MiB 回执空间；合成 6,241 个合法形状 exists 回执、共 1,048,489 字节后，首次撤销追加会抛 `companion.receipt_limit`，外层保存点将撤销一起回滚。现发行/exists 为首次撤销预留 1 KiB；独立以缩小上限的同一实际合同填满发行容量，首次撤销仍成功，零 IO 复核通过。已撤销行额外新回执达上限可拒绝，不会恢复凭据。

另外已要求显式 `save(ignore_version=True)`，实现已补并关闭 schema `track_changes`，避免仅设 flags 被原生 `_save` 参数重置。当前代码中 Connection→Credential 的锁顺序、范围/issuer 的当前读、显式 allowed_users、真实对象 context capability、generation CAS、重放先重新管理授权、native CRUD/share 拒绝、响应私有标记均已静态核对。此时没有剩余确定的 P1/P2；不将静态或纯边界验证写成真实 Site 并发/权限/WSGI 已通过。

## 审查与交付状态

root 已按上述窄 gate 安排 token 凭据底座；独立只读审查及两项问题关闭情况见上节。目录域仍 hold。本取证包未改业务代码、启动 Site、运行业务回归或访问真实 ERP；没有宣称第九批整体移植完成。

root 通知交付复核后，已独立新采三份 delivery 证据，均经过首尾实际流量与不可变 revision 核对；没有覆盖 start 或早期 evidence。源码映射仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188` 和本页记录的原 digest。

| 比较 | 实际结果与范围 |
| --- | --- |
| 来源 ninth start → ninth delivery | PASS；记录的 Web/Worker 线上源码、digest 和相关不可变引用未变。 |
| ERP runtime v2 ninth start → ninth delivery | `settings_unchanged=true, observations_changed=false`。 |
| ERP runtime v2 eighth delivery → ninth delivery | 仍为 `push_runtime_identity_changed`；完整集合摘要从 `461d6540e21d14f3a41443ad720d5605` 到 `5ee9e6604b3c6dee19ab3227d8484fad` 的历史差异仍未解释，不关闭目录域 HOLD。 |
| 精确 v1 seventh start → ninth delivery | 两组件均 PASS，count 60、digest `67c022c7edb856033715c51bacf02b22`。 |
| 精确 v1 ninth drift start → ninth drift delivery | 两组件的 v1 摘要、v2 摘要、adapter 数量逐项相等。认证子切片的覆盖边界不扩大。 |

drift delivery 的 `previous_runtime` 刻意绑定第九批 start，`updated_since=2026-09-21T04:38:57.788778+00:00`，该截止后的 updated_at 数量各 adapter 均 0。drift start 原来使用第八批 delivery 截止；两份时间窗口不同，不能把此项 1→0 写成记录被撤销，也不能用 0 排除未维护 updated_at 的其它更新。

六份证据均已从 `/tmp/pearnly-cowork-migration-20260920` 原样拷贝到本目录的 `evidence/`；逐份核对 envelope canonical SHA256 与字节完全一致，目标使用独占新建/只读权限。下表 checksum 是 canonical `evidence` body 的 SHA256，不是带缩进整份文件的 hash。

| 已归档文件 | 采集时间（UTC） | canonical SHA256 |
| --- | --- | --- |
| `evidence/baseline-ninth-start.json` | `2026-09-21T04:38:30.493234+00:00` | `1bf8b32e0d4e84128a3b8641450e40923b5bfc1470c1d76fda5a5d11607506c7` |
| `evidence/baseline-ninth-delivery.json` | `2026-09-21T05:05:35.628006+00:00` | `15e7fd1ed39cb0c597f6de5431ac02e2e2bddd2b02a30e1434647718f5228e3b` |
| `evidence/push-runtime-ninth-start.json` | `2026-09-21T04:38:57.788778+00:00` | `1e3089d071dd49a9973651cb376dbe71e1d66ce690335568109e57da95aa5dc7` |
| `evidence/push-runtime-ninth-delivery.json` | `2026-09-21T05:05:47.911874+00:00` | `29c1276b7f64d97963fc916d0fd0aab2d803041cecc561d532f9a59e56523678` |
| `evidence/push-runtime-ninth-drift-v1.json` | `2026-09-21T04:49:00.283076+00:00` | `d313d78c34621288ae9e6d31dad2006ca4ac2a7615a495e8eed51c216840b28f` |
| `evidence/push-runtime-ninth-drift-delivery.json` | `2026-09-21T05:06:20.853401+00:00` | `a2239ae9c66d6c05ef32d02b7b057d1c856a63a3a6ef4981b05f514092c90802` |

本节仅交付来源/配置核验；业务 Site 测试、Windows/Companion 二进制身份、真实外推结果和用户验收均不能从这些脱敏摘要推出。本分工没有暂存或提交文件。
