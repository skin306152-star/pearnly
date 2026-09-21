# 第十批：当前来源、新报告协议与历史配置差异的边界

本分工只做开工/交付来源核验和独立审查。工作树为 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，开工实际 HEAD 为 `70383cdf5e8f45129997c8e773e0bcf3772422a9`；采集前工作树与暂存区均干净。没有改主目录、DMS、生产或客户机；没有调用外部写入、部署、目录回报或付费服务。

## 开工核验结果

**当下源码及既有运行配置投影与第九批 delivery 一致，可以作为第十批新原生连接报告协议的开工依据。第八到第九批的完整配置历史差异仍未解释，不关闭旧数据迁入范围的 HOLD。**

| 项目 | 本次证据 |
| --- | --- |
| 环境 | GCP project `pearnly`、region `asia-southeast1` |
| 来源时间 | `2026-09-21T05:15:37.779329+00:00` |
| 来源文件 / canonical SHA256 | `evidence/baseline-tenth-start.json` / `61b2cb4e24163c8b531be211db8c892a8945d5310fe2d2d18ec7efdb5ad9aa9d` |
| 实际流量 | Web 100% `pearnly-web-ec2e5c957e6b-s3`；Worker 100% `pearnly-worker-ec2e5c957e6b-s3` |
| 镜像 | `asia-southeast1-docker.pkg.dev/pearnly/pearnly-app/app@sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226` |
| 完整源码 | `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188` |
| 映射依据 | GitHub run `35321281376` completed/success/head_sha、固定部署账本与两实际 revision digest 相符；不从短 SHA 名称猜测 |
| 固定只读快照 | `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b` |
| runtime 时间 | `2026-09-21T05:16:08.035736+00:00` |
| runtime 文件 / canonical SHA256 | `evidence/push-runtime-tenth-start.json` / `970d8f75f0894e0c8fb1c578b7d9f4ca2f01e326eb8aa74a4b2960097ca9bf9a` |

按既有安全采集器仅执行两次明确的 `collect-readonly`：来源元数据以及固定数字版 runtime secret 的内存白名单投影/只读 SQL 聚合。没有修改采集器、读取真实票据或将 secret payload 落盘。数据库为 READ ONLY / REPEATABLE READ，结束 rollback/close；首尾重新核对实际流量。原件新建于 `/tmp/pearnly-cowork-migration-20260920`，两个归档副本逐字节相同，使用独占新建和 0444 权限，未覆盖旧证据。

实际 compare：

- `baseline-ninth-delivery.json` → `baseline-tenth-start.json`：PASS，来源身份未变。
- `push-runtime-ninth-delivery.json` → `push-runtime-tenth-start.json`：`settings_unchanged=true, observations_changed=false`。
- 两服务当前端点全量摘要仍为 count `60`、`5ee9e6604b3c6dee19ab3227d8484fad`；Express 合计 7 个，全部 legacy。旧 collector 中其余部分为 MRERP/FlowAccount/other；第九批另采证明其中含 45 个 `mrerp_dms`，本轮未把该历史分类当成新查询结果。
- 当前安全 env 投影含 `ERP_PUSH_ENABLED=true`、`ERP_SELLER_ROUTING=1`；相关动态行 `erp_shared_express_endpoint` 与 `erp_target_projection` 均 enabled/all。这里证明实际投影值，不证明 managed 已有客户机，或每个 token/目录均有可迁映射。
- 本轮未重新执行精确 v1 补充采集。第九批 v1 来源及窄身份结论继续保留其原时间；本轮直接证据是完整 v2 及其它已登记投影未变。

## 对迁移规则的适用解释

`docs/development/migration-baseline.md:7,17–23` 要求核对**当时**承接流量的代码、影响功能的配置与调用合同；`:27–32` 明确不永久固定在某个历史版本，发生变化应检查受影响范围、更新合同并复验。`:48` 禁止证据不足时声称完成或与旧线上一致。它没有要求在历史差异无法归因时永远禁止基于当前已确认来源的所有新代码，也没有授权忽略差异直接继承旧数据。

第八 → 第九批 v2 摘要 `461d6540e21d14f3a41443ad720d5605` → `5ee9e6604b3c6dee19ab3227d8484fad` 的根因仍未知；当前稳定不能追溯证明它只是心跳、属于 DMS、或不影响旧目录。原 [第九批记录](ninth-source-review.md) 和原失败证据完整保留。

root 已决定本轮采用以下明确范围：

| 可继续的本地实现 | 仍不包括或不得宣称 |
| --- | --- |
| 新原生 Connection、当前授权的公司/客户/actor、新发行 token 的精确报告 HTTP 桥 | 不继承旧平台 owner/user_id、旧 token、旧 endpoint 名称或权限 |
| 按当前固定源码处理新输入的缺席/空值、offline、目录字段归一化、版本声明与刷新回报 | 不导入旧 reported 目录、已选账套、refresh 队列、缓存或任何既有目录事实 |
| 在新 Site 的授权范围中形成可验证的目录快照，以合成输入测试当前合同 | 不说当前生产各客户目录与新 Site 一致，不把伪造/模拟报告当实际客户机证据 |
| 对原生事务、完整性、权限、幂等作必要适配，并逐项记录有意变化 | 不顺便开放推送 poll/ACK、弱化第八批 attempt fencing，或切换真实客户端 |

这是以第十批当前有效基准开始的新输入协议实现，不是追溯补签第八/九批旧目录迁移。当前全量 v2 连同源码、开关与实际分支投影已足够支撑此限定的本地工作；root 决定本轮不增加生产读取。交付时仍需重新核对本批来源，才能交付实际完成的服务端范围。

## 固定源中的实际依赖

以下路径均相对固定旧快照，说明为何新协议和旧数据迁入需要不同证据：

| 来源位置 | 依赖与迁移边界 |
| --- | --- |
| `routes/erp_agent.py:43–56,117–187` | 全局开关、token、enabled、legacy/managed 分派；offline 提前返回。新协议不可因有效 cookie 跳过机器身份。 |
| `services/erp/express_push/connection_identity.py:13–19` | 旧输出读取 endpoint.user_id 对应 username/email；新原生连接必须用当前授权 actor 的安全身份，不能按旧记录猜 owner。 |
| `routes/erp_agent.py:138–185` | 已选账套比较、缺省 method、缺席 catalog、版本协商依赖已有 config；在新连接使用明确原生初值，再接收新报告。 |
| `services/erp/express_push/agent_reporting.py:250–255,295–352` | 已选账套包含 `express_root`；六项科目映射是顶层字段，不仅是名为 `mapping` 的 JSON 键。不能把它们归为无关心跳。 |
| `services/erp/express_target_projection.py:241–365` | 缺席/明确空扫描区分；选定或 refresh 目标需在上报账套集合中；旧写入依赖 endpoint 及 owner 的 tenant。新实现必须只消费当前认证原生 scope。 |
| `services/erp/target_refresh.py:27–150,272–383` | 刷新有持久请求、scope、supersede、租约及完成匹配。新协议必须跑完这些行为，不能仅保存 requested/received 字段。 |

目录内容会影响后续目标可用性、商品/客户复用和过账画像；其摘要变化不是纯显示变化。空白新 Site 不允许借“没有旧数据”漏掉同一固定源对未来新报告的过滤/绑定/刷新合同。managed 分支和实际 Companion 二进制兼容性仍应单独登记，不能由 legacy 新 token 验证推导。

## 后续最小分组投影建议（未实施、未采集）

如以后需要把滚动心跳和业务配置变化区分，建议新增独立 schema 的只读投影，绑定当次 source/runtime canonical hash、SQL hash、字段白名单版本与采集时间；保留全量 v2 对照。不能改写第七 v1 或第八/九 v2，也不能用新分组 hash 回推它们当时的分组值。

优先仅对固定 `adapter='express'` 范围聚合，记录端点数和集合摘要以隔离 DMS；用数据库端按 endpoint id 稳定排序的集合摘要，不返回逐行 ID、名字、用户标识、路径、token/hash 原值或目录内容。建议分四组而不是将所有 reported 字段都当无关观测：

1. **身份/授权**：endpoint id、adapter、enabled、binding_generation、tenant/workspace、user_id、revoked/shared/bound profile，以及 token hash 的服务器端摘要；必要时把 owner 关联 tenant/active 的存在与集合摘要纳入。新增 user_id 覆盖从新采集点起生效，不能追溯扩展旧 v1/v2。
2. **影响行为的设置**：method、已选 account_set/dir/company/row/express_root、六项顶层科目映射、协议能力，以及所有未被明确分到其它组的 config 字段。未知键不能因没列白名单而消失；其原值不输出。必要非敏感枚举只输出固定 allowlist 分类，未知值归 invalid/other。
3. **业务目录状态**：reported account sets/accounts/products/customers/stock groups 与 catalog fingerprint 分组集合摘要；可附数组存在数、类型/条数聚合，不回传内容。变化需按受影响链判断，不能直接当无关滚动观测忽略。
4. **短期观测**：明确的 heartbeat/seen_at、设备声明、刷新观测时间与状态计数。只输出安全分类/聚合。不能仅凭 updated_at 排除其它变更；固定源码多条 config 更新不维护它。

如推进**旧目录或刷新数据迁入**，还需另行核对 `erp_target_projection_*` 和 `erp_target_refresh_requests` 的实际 schema、身份/范围映射与当前状态；仅 endpoint config 摘要不覆盖这些表。新协议本轮不读取或导入这些生产数据，新原生结构与刷新行为通过本地 Site 证据验证。

以上是后续证据范围建议，本轮未改任何 collector、未增加第三次生产查询、未实现分组 SQL，也没有声称取得了其中的新字段证据。

## 当前状态

本分工已完成开工及交付两组来源证据、范围分析和限定源码独立审查。交付采集时本地 HEAD 仍为 `70383cdf5e8f45129997c8e773e0bcf3772422a9`，本批改动待主任务提交；交付提交号由主记录补充。本记录不代签真实客户机或外部 ERP 验收。

审查使用锁定镜像 `sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9` 的 Frappe 实际源码，以一次性无网络、只读、无 Site/卷挂载的 Python 进程读取源文件；没有启动 bench、业务 Site 或数据库。已核对调用顺序：

- `app.py:239–245` 先解析 form_dict、建立 HTTPRequest，再执行 before_request；`auth.py:130–147` 的 cmd/login 更早触发 before_login，因此不能只靠 auth_hooks 拦截。
- `auth.py:629–652` 先原生 OAuth/API key 再 auth_hooks，随后仍验证 issuer IP 限制；机器入口应限于精确 POST，不借 cookie/API key 成功绕过机器身份。
- `__init__.py:382–395` 的 set_user 会原地改 local.session 并清 form_dict；`app.py:470–472` 会把 session_obj.update 放入响应后队列。桥的 request-local session 分离、session_obj=None 以及恢复已冻结 body 是必要边界。
- `api/v1.py:35–41` 会添加内部 cmd；`handler.py:65–86` 可在白名单前应用方法 override/server script。桥当前拒绝客户端 raw cmd/sid、路径别名、混合 sid cookie、覆盖 handler 与 server script，并在实际 handler 重验真实对象能力。

初版静态主链未发现 cmd/login/cookie 扩权；发现一个具体诊断旁路并已交实现作者：启用原生 Recorder 时，`recorder.py:345–346` 复制 Authorization/body，`:63–85` 记录完整 SQL；`auth.py:682` 在自定义 hook 前以完整 exp_ token 查询 OAuth Bearer Token；最终 `recorder.py:410–417` 写诊断 Redis。只设置 no-store/清 Cookie 不能防止这一记录。

作者已使用原生 `do_not_record` 包裹窄 `_discard_recording`，仅在 before_login/before_request/authenticate_request/after_request 的机器候选分支调用。独立只读复核与固定 `recorder.py:434–440` 的 cleanup/delete 行为一致，可阻止此请求的后续 SQL 捕获与 after_response dump；普通请求分支没有禁用 Recorder。代码层问题已关闭，真实 WSGI/Recorder 缓存、Session/Cookie 回归仍由主任务执行，本文此时不提前记现场通过。

随后只读核对了最新 `http_auth.py`、`heartbeat.py` 与 16 项 `cowork-companion-heartbeat-lab-verify.py`：脚本以 `buffered=True` 实际触发 after_response，Recorder 断言含普通 ping 的阳性对照；还覆盖真实 Sessions/Redis 前后比对、Session.update 不调用、login 提前拒绝、raw cmd/sid、cookie/API key 不替代机器能力、方法 override/server script、token 轮换/权限重验及本地失败回滚。当前未发现剩余确定的 P1/P2；这里只确认代码与测试场景有效，未自行运行 Site 或代签现场结果。

追加复核 `after_request` 的机器候选 3xx 收口：Core 提前对 `/`、`/login` 返回重定向时，只将该机器请求改为 401、移除 Location/Set-Cookie 并设置私有响应；普通浏览器流程不进入此分支。未发现由此增加身份权限；现场证据仍由主任务执行。

## 目录与刷新装配的独立审查

只读审查范围为 `connections/{collector,refresh,snapshots,state}.py` 和 `service.py` 本批差异，交叉读取固定源 `routes/erp_agent.py:100–114`、`services/erp/target_refresh.py:27–150,272–428`。没有运行 Site 或改业务实现。

- `state` 的名称校验、锁定读取、scope 键与原服务实现一致；`install_snapshot` 保留原 request/current head/revision/scope 核验。`snapshots.publish` 仍将目录和准备证据分开，报告不补造 preparation。
- 原报告组件缺席不擦除缓存；明确空目录保留最后一次快照并记错误；空 catalog 的规范目录与 raw cache 分别更新。普通报告不完成租约，完成必须匹配独立 lease handle、当前 credential/generation、连接版本、scope 与有效期。
- 配置字段实际变化清空当前 heads 并令旧版本刷新失败；同连接 token 轮换不能完成前代租约。requested 合并、leased supersede、过期与 newer-request rerun 均在 Connection 锁内处理。这里只确认代码链及合成测试场景，尚未独立取得并发现场结果。

审查发现并已交 root 的确定差异：

1. **P2，领取目标错误回落已存账套，已修复并复核。** 审查时 `collector.py:65` 使用 `result.account_set`，而 `reports.py` 的该响应字段会回落已有配置。固定源 `_offer_master_refresh` 明确只用本次 `body.account_set or body.account_dir or ''`。默认 A、pending B 时，空报告旧实现可领取 B，或仅 `account_dir=B` 应领取 B；初版装配却按 A 过滤，导致 B 等待，甚至领取错误 A 任务。修复后 `ReportPlan.lease_selection` 单独携带本次领取选择，collector 只使用该字段；展示响应继续保留旧回落行为。独立零 IO 执行当前 `plan_report` 的空报告、dir-only、两字段优先级、空白 account_set 共 4 场景通过；实际领取回归由主任务新增原生场景执行。
2. **公开可用性布尔判断不一致，已修复并复核。** 三个 DTO 原用配置 `is True`，身份/推送开关接受整数 `1`；因此配置为 `1` 时实际入口可用但 `collector_available=False`。当前三处均改为相同 `in (True, 1)` 判断，并仅报告 legacy `binding_generation=0` 为可用。从实际 `service.py` AST 提取 `_refresh_dto` 零 IO 执行六种开关输入与 managed generation 共 7 场景通过。

上述两项代码层关闭，限定范围内未发现其它确定的 P1/P2。此判断不覆盖旧数据导入、真实 Companion 客户端、外部 ERP 效果或尚未实施的准备资料采集。

## 交付前来源复核与归档

只执行既有 `cowork-migration-baseline.py collect-readonly` 与 `cowork-push-runtime-baseline.py collect-readonly`；参数、只读事务、数字版 Secret 引用、内存凭据与脱敏投影边界均沿用开工方法，未改 collector、未额外读取真实目录或业务 payload。

| 交付证据 | UTC 时间 | canonical SHA256 |
| --- | --- | --- |
| `evidence/baseline-tenth-delivery.json` | `2026-09-21T05:37:20.607583+00:00` | `f5cfda4cf3682cd7ef83d9a8bc69a572113c502f3e3b75f853f7f49d2038b0bc` |
| `evidence/push-runtime-tenth-delivery.json` | `2026-09-21T05:37:56.186689+00:00` | `3008ff7f52604b42f5b0cd873ad53c186a35544fd7661bd2dc47636f099e2e36` |

- 本批 source start → delivery：PASS，Web/Worker 实际流量、完整 SHA、镜像 digest 与相关不可变配置引用一致。
- 本批 runtime start → delivery、第九批 runtime delivery → 本批 delivery：均 `settings_unchanged=true`、`observations_changed=false`。
- 第八批 runtime delivery → 本批 delivery：仍为 `push_runtime_identity_changed`。第八到第九批历史全配置差异 HOLD 继续保留，未被本次稳定复核覆盖或重新解释。
- 两份交付证据与 `/tmp/pearnly-cowork-migration-20260920/` 原件逐字一致；归档和原件设只读。两份开工证据同样已归档，本批共四份证据，没有覆盖历史文件。

本地升级环境另行区分：主任务明确报告原实验容器及卷已不存在，本轮是从 `70383cdf` 固定源码 archive 在独立新卷重建合成 fixture，再升级与重复迁移并核对原列哈希。该信息来自主任务现场记录，本分工未操作 Site；不得写成第八/九批旧卷或真实业务数据连续升级通过。真实 Site 用例数量及最终提交由主任务在总记录登记，不由来源核验推导。

## 最后一次 HTTP 诊断边界复审

交付基准归档后，主任务通过真实 WSGI 发现另一条诊断泄露路径：即使关闭 Recorder，原生 `Error Log` 的 `with_context` 仍可能记录 `validate_auth.authorization_header` 中的 token。本节是对此修复的追加限定审查，没有再次采生产，没有扩大业务或权限范围。

实现改为在精确机器 `before_request` 校验原 Bearer/POST/路径/Guest/cookie/raw cmd/sid 后，存入当前请求私有、`repr=False` 的 `_PendingRequest`，随后移除本请求的 `HTTP_AUTHORIZATION`。`auth_hooks` 必须匹配同一个 request 对象和真实 `_AUTHORITY`，再调用现有凭据服务；任何结束路径在 `finally` 清空 envelope。授予身份后的 `_BoundRequest` 同样不在 repr 中暴露正文。该步骤只改变机器请求的凭据交接，不在 before_request 中授予身份。

独立重新读取锁定 Frappe 镜像源确认：`auth.py:633` 读取请求头、`:636–638` 仅在头的拆分长度为 2 时执行 OAuth/API-key 解析；`:640` 始终执行 auth_hooks，`:651–652` 仍按 Guest → issuer 的身份变化调用原生 `validate_ip_address`。因此清除机器头不会跳过 IP 限制。HTTP 桥没有捕获或忽略 hooks 返回后的原生 IP 失败；普通非机器候选在 before_request/auth_hooks 两入口均原样返回，不修改 header、session 或异常处理。

追加审查发现两条具体残口，已由实现作者修复，当前代码复核关闭：

1. `credentials.authenticate` 的入口 `_enabled()` 位于其 try 外；关闭总闸时抛 `PermissionError`。开发模式会令原生 `app.py:448` 对 403 也生成快照，而 `utils/error.py:20–25` 的排除集合不包含 `PermissionError`，认证局部变量仍可能含 token。当前机器认证边界改抛 `_MachinePermission(AuthenticationError)`，保留 HTTP 403，不重试权限拒绝，使用原生认证错误快照豁免；不改变普通用户的 PermissionError。
2. 私有交接的 `deepcopy(payload)` 原在安全边界外。合法深层 JSON 可以完成解析，却在复制时触发 `RecursionError`，从而令 before_request 的 token 局部变量进入 500 快照。独立零 IO 复现了 JSON 550 层可解析、deepcopy 失败。当前整个机器交接包在窄异常边界内：认证拒绝保持拒绝，权限走上述安全 403，意外异常仅记录 stage/exception type 并返回 `_ReportBusy(AuthenticationError)` 的 503，不记录原异常对象或 token。

实现作者及主任务报告四项真实 WSGI 定向通过：机器 OAuth/API-key 解析不调用且 issuer 原生 IP 校验实际执行；原生 IP 500 保留诊断而上下文不含 token；总闸关闭 + developer_mode 返回 403 且无 Error Log；实际 550 层 JSON 的 deepcopy 失败返回 503 且无 Error Log。这里只将该现场结果明确归属于主任务执行；本分工只读复核了实现、锁定框架顺序与定向测试，未再次运行 Site。最终整组 HTTP/原生用例及交付提交号以主任务总记录为准。

最后限定结论：上述凭据交接和诊断残口均已关闭，未发现新增权限或绕过原生 IP 的路径；第八→第九批旧配置 HOLD、真实客户机和外部 ERP 未验收的边界不变。
