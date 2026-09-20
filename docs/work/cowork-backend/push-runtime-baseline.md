# ERP 推送运行基准：第七批

固定旧生产源码 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`；新平台起点 `407ad132b2e69c871bcb272e2a7a361b6363d295`。本包只新增只读采集器、离线校验和本文，不实施推送业务，不执行外部写入、真实账单、应用启动或数据库迁移。来源静态路径和行号均相对 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。

## 已取得的事实与界限

原始 v1 `/tmp/pearnly-cowork-migration-20260920/push-runtime-seventh-start.json` 已采集；证据 SHA256 为 `111119d806e9daed01724baeec825453432078020717e21d484a4a7fe723573a`。采集前后实际流量均对应第七批固定 source baseline，Web/Worker 数据库配置摘要一致。v1 只绑定当时列出的端点 config 键，不能追溯声称采集过完整 config。

完整 config 覆盖从 **2026-09-20 20:49:47 UTC** 的补充 v2 基准开始：`/tmp/pearnly-cowork-migration-20260920/push-runtime-seventh-expanded-start.json`，证据 SHA256 `638c13bf1b7572bb1a649f2ef481deef4ed0301871be025bdb27fe4926fa84f1`。来源仍是同一 SHA，采集首尾流量核对通过；Web/Worker 各 60 个端点的完整配置集合摘要均为 `461d6540e21d14f3a41443ad720d5605`。原 v1 文件及 checksum 未变。此补充 start 不是交付末尾复核，也不能证明 v1 与补充 v2 之间原先未覆盖字段没有变化；交付比较须再采一份 v2。

- 两服务 `ERP_PUSH_ENABLED=true`、`ERP_SELLER_ROUTING=1`。`ERP_BRIDGE_ENABLED`、轮询/重试/推送 timeout 未显式设置；适用默认值须按固定源码解释。`DATABASE_URL`、`PEARNLY_KMS_KEY`、seller-routing allowlist 仅记录存在，不输出内容。
- 动态 `erp_shared_express_endpoint`、`erp_target_projection` 均 enabled/all；但 60 个现存端点全部 `binding_generation=0`。managed 开关开启不等于已有 managed 运行端点。
- Express 启用 7 个；6 个存在 agent token hash、上报 payload version 1；自报 Companion 版本为 1.1.59×1、1.1.67×1、1.1.77×3，2 个缺版本。没有 managed 端点。这里只证明数据库所存的版本声明及协议能力，legacy 心跳只投影是否存在，**不证明这几台机器当前在线**。
- Express 历史聚合：success 18、manual 18、failed 1、skipped_dup 1；这些组近 7 天数量均为 0。MR.ERP 启用 4、停用 3。无记录 ID、企业名、账号、业务路径、票据或载荷进入证据。
- `public.erp_bridges`、`public.bridge_jobs` 未发现所需字段，证据逐列登记 missing。此处不能据此把桥式写入当作当前生产已启用能力；采集器绝不调用来源的 `ensure_tables/heal` 补表。

这些事实足以选择**旧 legacy Express 的服务端协议**作为有边界的本地迁移起点；不证明已闭合客户机执行程序、DBF/CDX 写入、防重实现、外部落账或实际重推验收。当前客户端版本也不能由 Web/Worker 的 image digest 推导。

## 采集与比较

入口为 `scripts/cowork-push-runtime-baseline.py`，只有 `collect-readonly` 会访问云端。`verify`、`compare` 默认只读已有 JSON。需要旧 venv 的 python-dotenv/psycopg2 与 gcloud，不能 import 旧应用启动模块。

```sh
/Users/skin/Developer/Pearnly/pearnly-app/venv/bin/python scripts/cowork-push-runtime-baseline.py collect-readonly \
  --baseline /tmp/pearnly-cowork-migration-20260920/baseline-seventh-start.json \
  --output /tmp/pearnly-cowork-migration-20260920/push-runtime-seventh-expanded-start.json
python3 scripts/cowork-push-runtime-baseline.py verify /tmp/pearnly-cowork-migration-20260920/push-runtime-seventh-expanded-start.json
python3 scripts/cowork-push-runtime-baseline.py compare FIRST.json LAST.json
```

输出已存在时拒绝覆盖。当前 collect 写 v2；verify 兼容 v1，compare 只允许相同 schema，不允许把 v1 的部分 config 覆盖与 v2 完整 config 覆盖比较为同一事实。collect 使用 source baseline 已验证的不可变 revision、数字版本的 runtime secret 引用；秘密 payload 只在进程内解读，显式 Cloud Run env 覆盖 dotenv。拒绝 `${...}` 插值和另一路相关 secret 引用，不把本机环境当生产环境。当前引用名称是 `pearnly-web-env` 和 `pearnly-worker-env`；数字版本由 source baseline 固定。本脚本不会输出秘密值、异常原文或连接字符串。

数据库只运行 `READ ONLY / REPEATABLE READ` 事务、8 秒 statement timeout，结束 rollback/close。先从 public schema 元数据核对表/列，再执行静态参数化 SELECT；缺列记录 missing，不创建表。只读身份包括：

- 非敏感 env、秘密存在标记、固定 secret 引用与源码身份。
- 平台设置 enabled/rollout；allowlist 只输出数量和数据库内生成的成员集合摘要。
- v2 端点配置集合摘要：数据库内绑定 endpoint/tenant/workspace、adapter、enabled、generation、shared/revoked、bound account/profile、agent_version，以及完整 `e.config` JSON（所有已有及未知键、嵌套内容、JSON null 均保留）。原 ID、token hash、凭据、路径、映射、目录内容不越过数据库边界，只返回集合数量和数据库内 MD5 摘要。v1 仅绑定 agent_token_hash/method/account_set/account_dir/account_root/system_url/comidyear/seldb/max_payload_version/companion_version/mapping。
- 桥如存在，数据库内对角色、生效角色、books、版本、secret hash 计算集合摘要，只返回摘要及总数。

设置摘要是**变化探测器**，不是签名或客户端代码证明；它不是整个 ERP 配置/主数据快照。v2 在数据库内摘要 config 中的目录上报、画像/自动化/方向/科目兜底等内容，但不会取出它们，故能检查集合是否变化，不能解释其实际业务含义。目录关联表、产品/客户/科目独立主数据表、供应商档案、银行/支付证据、票据和成功响应 body 仍不读取；涉及它们的后续迁移需另建最小授权合同。源码 hash、不可变 secret 数字版本和当前采集方式共同限定本证据。

滚动 observations 单独保存：规范化版本/协议分布、存在性、managed fresh/绑定匹配数量、推送状态/近 7 天/租约/重试数量、桥在线和任务数量。compare 对设置 identity 漂移拒绝；仅观察值变化返回 `observations_changed=true`。v2 不剔除任何 config 键，因此 config 内心跳时间或目录快照发生变化也会拒绝比较；这只是完整 config 改变，不能单独推断功能开关改变。缺表保持 missing 时的比较通过也**不代表补齐了那部分证据**。生产 scheduler/Cloud Tasks 是否暂停需要独立只读状态，不能拿 ERP_RETRY_TICK_SEC 或历史 queued 状态证明重试正在运行。

## 固定源码的执行链与危险遗漏

| 环节 | 固定来源 | 必须保留或明确修正的语义 |
| --- | --- | --- |
| 提交与账号选择 | `routes/erp_push_log_routes.py:43-58`；`services/erp/confirmed_push.py:175-286` | 当前身份/权限、记录范围、选定端点与账套、目录刷新证据。请求不是可任意指定目标的外部写指令。 |
| 服务端预留 | `confirmed_push_reservation.py:175-327,339-434` | 先锁 endpoint 并记录 target_intent；同账套/记录查 success、active、unknown。先落 retrying reservation，再外部调用；CAS 失败或结果不明落 manual，不能当可安全重试。 |
| 真正状态源 | `push_retry.py:96-124`；`express_push/enqueue.py:72-139` | Express enqueue 只是 pending 或 manual；只有回执可落 success。history.last_push_status 是镜像，不可另造第二套成功事实。MR.ERP 的 skipped_dup 是中性状态，不等于拿到了可供 PRN 追溯的成功凭证。 |
| PRN 上一版凭证 | `express_push/prior_doc.py:34-94,97-115` | 旧 fields.history_id 优先、否则当前 id；JOIN history 租户后查最新 success 的 response.express_docnum/meta.docnum。旧实现查库错误返回 None，且没有 endpoint/账套约束；这是来源的缺口，不能照搬并宣称完整防重。新链必须使用不可变原键、当前授权、目标身份和权威回执；查询失败应明确停止或进入未知。 |
| 云端去重 | `push_dedup_store.py:13-66`；`shared_express_push.py:210-240,342-465` | legacy 按 user/endpoint/account/history 查询 success；managed 在锁内按 tenant/workspace/endpoint/account/history 查询 active/success。并发重复不能靠一次先查再写。 |
| legacy 领取 | `routes/erp_agent.py:248-298`；`express_push/agent_lease_store.py:39-64`；`agent_store.py:206-249` | pending + 账套白名单 + SKIP LOCKED；租约 120 秒，返回原 log_id。普通租约过期可重领，真正的重复写保护依赖 Companion 本地 find_existing。 |
| 确认跳过防重的失联 | `agent_store.py:144-220` | duplicate_confirmed 关闭本地防重，已领取后超时必须 manual/confirmed_push_unacked，不能自动重投。分不清是否已写，必须如实保留未知结果。 |
| legacy ACK | `agent_store.py:338-480` | 校验 endpoint 和 lease_owner；success 终态幂等。waiting_lock 释放租约回 pending、不增加失败次数；needs_mapping/needs_review 立即 manual；failed/rolled_back 达 3 次转 manual。不得将等待锁、明确回滚、未知结果混成同一重试。 |
| managed 领取/ACK | `shared_express_agent_queue.py:59-152,183-256,295-426` | token hash、租户/客户 active、binding_generation、bound/live profile 一致且心跳 <180 秒；签名 m1 handle 绑定 endpoint/log/attempt，过期重领推进 attempt，ACK CAS 拒绝陈旧结果。这段源码存在，当前生产聚合却未发现 managed 端点。 |
| 云端重试与原目标 | `push_retry.py:145-174,196-224`；`background_loops.py:49-157`；`retry_target.py:23-50` | 60/300/1800 秒、最多 3 次；错误分类决定是否重试；重试更新原日志，必须从原 request 恢复账套，不能拿 endpoint 当前默认账套改投。只查 due 并不证明任务调度器正在触发。 |
| 人工重试限制 | `routes/erp_push_log_routes.py:156-218` | 旧入口只拒绝 status=success，直接重跑其它状态；这并非完整的 unknown 恢复协议。迁移不能给所有 manual/unknown 状态一个通用“重试”按钮，须先核对是否写入及当前写者。 |
| 桥写者与迟到回执 | `bridge/write_gate.py:38-103`；`bridge/store.py:260-313,336-379,414-434` | 生效写者唯一闸、write 租约 300 秒/队列 TTL 600 秒；确认放行丢结果 expired，不重投。已 leased 的 write 等待超时不能直接宣称没写；expired 写活迟到结果仍保存 docnum。当前无桥表证据，不当作已运行分支。 |
| MR.ERP 实际适配与锁 | `erp_push_adapters.py:28-97`；`session_lock.py:38-120` | 发票适配恒用 HTTP，MRERP_TRANSPORT 只见旧注释，不作为运行开关；Cloud Run 的会话锁要求事务固定的 PostgreSQL advisory lock，取锁失败必须拒绝。新平台不能改成单进程锁或本机失败放行。 |

配置来源：`express_push/__init__.py:24-26` 读取 ERP_PUSH_ENABLED；`bridge/__init__.py:13-36` 读取桥开关与 poll hold；`erp_payload.py:23` timeout；`auto_push.py:182-198` seller routing；`platform_settings/store.py:104-134` 与 `shared_express_flag.py:14-24` 定义动态 allowlist；`core/kms_helper.py:10-28` 定义 PEARNLY_KMS_KEY（仅记录存在）。`services/startup.py:309-317` 在云角色不启动旧循环，云端 maintenance 实际由 `cloud_tasks/handlers.py:54-70` 调用 retry tick。

库存路不是另一个环境开关：`express_push/__init__.py:76-82` 的 `stock_lane_enabled` 恒 False，限制的是画像推断/覆盖的自动库存路径；`mapper.py:121-125,214-224` 仍让显式批次/行 `posting_kind=stock` 优先选择库存行。不得把常量 False 解释成所有库存单都不能生成。端点 `directions/autonomy/posting_profile/catalog_fingerprint/reported_account_sets/reported_accounts/express_root` 等影响实际分支，现在纳入完整 config 摘要，但未向应用导入真实配置或验收外部落账。

## Companion 版本闭合仍缺什么

`routes/erp_agent.py:169-175` 保存客户端自报 payload/Companion 版本，`express_push/common.py:246-254` 当前 payload_version=1；`prior_doc.py:21-24` 说明完整进销项 prior_docnum 支持至少 1.1.48，但仅上报一个足够大的版本号不能证明运行了该实现。`deployment/cloud-run/verify_release.py:107-131` 比较安装包下载与 GCS 对象及 generation，证明发布物一致，也不能证明客户机安装/运行同一 EXE。

生产客户端需另取得正在运行二进制的版本及 SHA256、可信 release/build 记录到 Companion 源码 SHA 的映射，再固定其写者锁、路径限制、prior_docnum/find_existing、备份/回滚、索引重建及回查逻辑。禁止从本地 Companion HEAD、最新可下载 EXE 或 Web SHA 代推。此包没有读取或更改客户机配置，也没有启动它。

## 验证

17 项离线测试通过：env 脱敏、固定覆盖顺序、拒绝插值/独立秘密源、readonly 数据库失败不泄露并 rollback/close、缺表登记、完整结构/checksum/时间顺序、相同人数换成员摘要漂移、滚动观察变化不误判；新增完整 config 数据库内聚合、同数量 config 摘要改变拒绝、旧 schema 离线验证但拒绝跨版本比较、未知 schema 拒绝。本包未重新执行业务 Site/DMS 测试，未部署、未做真实 ERP 重推验收。

实际 v1 与 v2 文件均通过严格 validator，v2 自比较通过、v1→v2 比较按预期拒绝；自比较仅测试离线入口，不作为首尾不变证据。架构检查 0 违规。另只读复核 root 的 29 项 lifecycle/receipt 定向测试通过，包括已成功 legacy ACK 不降级、Express 技术失败 60 秒/数据错误不排重试、失联 finalize 只接受持久化且未被领取的已知状态；这仍是纯规则证据，未建立原生队列或执行外推。
