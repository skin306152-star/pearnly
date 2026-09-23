# Companion 心跳报告的第十批纯合同

**纯报告规约已实现并离线验证；原生 HTTP、凭据和持久化由本批其他模块接线验证。** 新树开工审计基线 `70383cdf5e8f45129997c8e773e0bcf3772422a9`。本文件补充[目录合同](companion-catalog-contract.md)的逐键所有权与缺席语义，不替代工作包状态。第十批 source/runtime 对第九批交付的核验已由主任务确认通过，授权仅覆盖新 Site 新连接与新 token 的报告；八→九目录漂移 HOLD 不因此解除，不导入旧 owner、endpoint 或目录数据。

固定源仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。本次重新计算目录合同登记的五文件 SHA256，全部逐字一致：`express_target_projection.py`、`express_push/agent_reporting.py`、`express_push/agent_account_sets.py`、`target_refresh.py`、`mrerp_refresh_worker.py`。本模块的执行差分另绑定 route、report 和 identity 五文件，见下表。未导入旧应用、执行数据库 SQL、调用网络或运行 lab。

## 结论与范围

可以独立迁移**无 IO 报告规则**；新 Site 的 legacy heartbeat 也可在身份入口与持久化边界验证后单独接入。输入只能来自本 Site 新签发凭据对应的新连接，不迁旧 endpoint、token、owner、tenant 兜底或真实目录数据。不安装推送消费者，不把心跳成功、目录 fresh 或客户端 complete 当作 ERP 准备资料齐全。

开工时已有 `companion.credentials.authenticate/revalidate` 内部身份接口及 `AgentContext`（site/connection/workspace/client/issuer/credential_id/token_generation/branch/binding_generation/private capability）；公开 API 只有人的 issue/revoke/status，没有机器心跳。connections 有 Connection/Snapshot/Refresh 和强制带 request 的内部 install_snapshot，没有普通心跳发布或 lease 执行。本文件仅给纯层的交付证据，原生接线进度以对应模块记录为准。

## 键的权威来源

这里覆盖实际 heartbeat/report 读写键，以及新 `connections.model.CONFIG_KEYS/REPORTED_KEYS`；不是声称列完旧 endpoint 任意扩展配置。

| 类别 | 逐键范围 | 可写来源与边界 |
| --- | --- | --- |
| 服务器身份与授权 | Site/tenant、connection/endpoint ID、workspace、client、issuer/user/owner、enabled、allowed_users、adapter、branch、binding_generation、bound_account_set、bound_profile_key、record_revision | 新 Site 当前原生记录与已鉴权上下文；body 无权改变。不能迁旧 `owner_user.tenant_id` 兜底补租户，也不能从客户端设备名推导身份 |
| 凭据 | 新 Credential 的 token_digest/token_generation/token_tail/active_key/generation_key/revoked_at；旧 config 的 agent_token_hash/agent_token_tail/agent_token_created_at | 只归凭据服务，报告不得覆盖。旧 token 不导入；原生 Password 的 credential_username/password/token 同样不归报告 |
| 服务器管理设置 | system_url、comidyear、seldb、directions、autonomy、default_doctype、posting_profile、stock_acccod、seed_customer_code、seed_product_code、generic_product_code | 现有 connection 管理入口；heartbeat 不接收这些设置。旧 response 的 method 只从服务器 config 读取或回落 rpa，不从 body 写入，也不据此声称 RPA 已接 |
| 本地选择的观测配置 | account_set、account_dir、account_company、account_set_row、express_root | 来源允许已认证 legacy Companion 报告；不是服务器身份。新管理 API 目前也可配置这些键，应明确报告更新的专用入口、审计与 revision 失效规则，不能任意 overlay config |
| 本地六科目选择 | revenue_acc、ar_acc、vat_output_acc、fallback_acc、ap_acc、vat_input_acc | 顶层 mapping 的六个已知键；来源称 Companion 为选择权威。与服务端分类/客户映射表不是一回事 |
| 客户端目录缓存 | reported_account_sets、reported_accounts、reported_products、reported_customers、reported_stock_acc_groups、catalog_fingerprint | 由已认证报告的已知组件净化而来；原始缓存沿旧语义存于连接级 report_json，没有独立账套隔离。canonical snapshot 才区分 endpoint/account_set scope；两者均不与权限或 prep 证明合并 |
| 客户端协议信息 | max_payload_version、companion_version | 客户端能力报告，不是服务端许可或成功证明 |
| 服务器接收时间 | account_sets_seen_at、accounts_seen_at、catalog_seen_at、stock_acc_groups_seen_at、agent_last_seen_at | 服务器 UTC 时钟生成，body 不可指定；稳态接收不等于内容 revision 必须增长 |
| 非身份设备提示 | agent_device_name（输入 device） | 来源为客户端显示名，最长 60 字符；只能作提示，不可当物理设备认证 |
| 刷新关联输入 | master_refresh_request_id、master_refresh_scope、master_refresh_account_set、master_refresh_error | 只是完成请求的关联/结果报告，必须匹配服务器的当前租约、scope、身份、revision；不能通过提交 request_id 获得 authority |
| 非报告资料 | mappings、supplier_profiles、bank_index、complete、preparation | 均不允许 heartbeat 建立 ERP 准备证据。资料缺口仍由专属服务端 reader 解决 |

## 缺席、空值、无效列表的真实行为

路径 A 是 legacy config 镜像（`routes/erp_agent.py:140-176` → `agent_reporting/agent_account_sets`）；路径 B 是 `express_target_projection.ingest_express_heartbeat:241`。二者必须分别建模，不能先统一清洗 body 再共用。

| 报告字段 | 缺席/None | 明确空或无效值 | 正常值与注意事项 |
| --- | --- | --- | --- |
| account_sets | A 保留原 reported_account_sets；B 可用 selected account 补兼容选项 | A 对 []、非 list、没有有效项的 list 写 []；B 若报告值非 None 而无可用账套，记 ERR_ACCOUNT_SET_EMPTY 并保留原 snapshot，不出版空目录 | A 最多 50 项，需 code 或 name；B 取 path 或 code，按 Windows 身份去重，标准 projection 上限 5,000。path-only 行可能仅 B 接受；name-only 行可能仅 A 接受 |
| accounts | A 保留 reported_accounts；B 是否保留取决于 catalog 是否触发整个账套投影 | A 的 []、非 list、无有效 code 行均写 [] | A 最多 3,000 条，code/name/type 截至 120；B 另走选项规范。**catalog 存在且账套可发布时，缺席 accounts 也会使 B 的 accounts 变空，A 仍保留旧表** |
| mapping | 路由不调用存储，保留六科目 | {}、空 list/空字符串经 `(mapping or {})` 为无变化；非空 list/字符串会在源 `.get()` 抛错，不是合法清空指令 | 只合并六个非空码，strip 后截至 40，空键不能清旧码。若新入口将畸形 mapping 转稳定 4xx，须登记为错误边界适配，不写成零差异 |
| catalog | 缺席、None、非 dict：A 保留商品/客户/指纹/科目组；B 不发布 account masters | `{}` 是有效 catalog 容器：A 清 products/customers，保留旧 fingerprint/科目组；B 在账套登记且可用时发布空 products/customers/accounts | 不把空 dict 等同整个字段缺席；正常 catalog 本身不能证明扫描所有来源成功 |
| catalog.products/customers | 只在 catalog 为 dict 时消费；字段缺席或 None 经 `or []` 清为 [] | []、非 list、无有效行都清 [] | A 最多各 20,000 行，code 或 name 非空，字段截至 200；B source_id 需 code，所以 name-only 行不能从 B 反推回来 |
| catalog.fingerprint | 净化为 {}，保留旧值 | 非 dict、{}、所有值无效仍保留；负数按 0，可转 0 是有效观测并替换 | 只取 stock_master_count/stcrd_lines/stcrd_lines_moving_stock；有一个有效键即整体替换指纹，不逐子键补旧值 |
| catalog.isacc_candidates | 缺席/None 保留旧科目组 | 显式 []、非 list、无有效 acccod 行均写 [] | 最多 100；支持旧别名，fit 用源 bool 语义，used_by 归一非负整数。不能悄悄把字符串 false 改成布尔 false 并声称同源 |
| selected account 整组 | account_set 缺席或空则不更新配置；只剩 account_dir 时 B 仍可推导 selected_key | 单个空字段不清旧配置；非法 account_set_row 跳过 | 仅非空选择触发更新，其他非空字段合并；row 可 int。缺省字段触发 changed 检测并不表示最终 patch 必有变化 |
| max_payload_version | 保留旧值 | 空、不可 int、<1 保留；源 int(True)=1 属实，不自行声称严格 JSON 整数 | 有效 int 替换；未上报不等于支持最新载荷 |
| companion_version | 来源 store 不写空值，保留旧值 | str(value or '').strip 后空或长度>20保留 | 合法值替换。B collector.adapter_version 另限 200，不能强行共用 A 的 20 字符规则 |
| device | 空不更新原 agent_device_name | 经 str(body.get('device') or '') 的非空内容截至 60；不是设备认证 | 在线心跳总由服务器更新 last_seen |
| offline | 假值继续正常在线报告 | 源按 truthiness，真值立即 offline，last_seen 写源1970哨兵；不处理目录、不领取刷新 | 不能把此兼容规则悄悄解释成严格 bool；新持久形式可用显式离线状态，但要保持对外语义并登记适配 |

其他重要差异：A 会净化 account_sets 的 name_en，但 B 的账套选项不保留它；A writable 用 bool，B `_attributes` 保留输入类型。B 的 `_ensure_selected_choice` 只提供 path/writable，不产生显式 root 证明；现有 native target 冻结的 root/账套一致性闸不应放宽。A stock_master_count 还有 ACK 建品后的服务器增量 writer（`agent_reporting.bump_stock_master_count:355`），本批没有 ACK，不迁这个增量入口，也不把普通报告伪装成已建主档。

## 开工时与新快照合同的差距

1. 现 `install_snapshot` 必须有 request_id 且会完成请求；旧普通 heartbeat 没有请求也可发布目录。需要内部出版方法与 lease 完成方法分离，不能为普通心跳伪造刷新申请。
2. 标准 Snapshot.body 无完整 raw cache：缺 name-only 商品、name_en、fingerprint、stock groups、协议版本、组件 presence 与接收时刻。建议独立报告/缓存字段或记录，由已鉴权收集器维护；不要借现 preparation 容器塞 complete=true。
3. 当前 CONFIG_KEYS 允许人的所选账套/六科目配置；源机器也有这两组 writer。需显式协调版本：选择/root/六科目实际变化让旧准备失效，同内容重复报告不无谓增长配置版本；目录缓存 revision 与配置 revision 不混为一谈。
4. 目前没有 leased 和 lease 时间字段。若本段不实现刷新领取，对 master_refresh_* 必须明确不承接，不能伪造 succeeded；若接上，保持 requested 合并、leased 新请求 supersede、迟到拒绝及 requested_at>started_at 的 rerun 防线，详见目录合同。
5. 身份域已具备内部能力，但 HTTP 还没安装。应由 Companion 编排先 authenticate/revalidate 并按 Connection→Credential 锁序验证，再单向调用 connections 内部收集服务；connections 不反向依赖 Companion。

## 已落地纯接口与调用边界

入口是 `connections/reports.py` 的 `plan_report(body: Mapping, previous: Mapping, observed_at: datetime) -> ReportPlan`，不依赖 Frappe、DB、环境读取或自动当前时间。时间必须由服务器注入 aware UTC。`previous` 是当前 report cache 与当前 config 所选五字段、六科目及 method 的组合；不能用客户端提交的 previous。返回字段：

| 字段 | 内容与消费责任 |
| --- | --- |
| offline | 旧 truthiness 语义；为真时只有离线 last_seen 哨兵和 connected=false，无目录/完成请求 |
| patch | 仅 `PATCH_KEYS`；其中 `SELECTED_KEYS` 五个和 `MAPPING_KEYS` 六个允许内部服务按真实变化合并 config，其他十四个 `CACHE_KEYS` 存 report_json，不修改身份或服务器管理设置 |
| observations | 原始 endpoint/account observation 字典列表；包括 adapter、observed_at、collector、account_sets、masters、form_schema、capabilities，account 项另有 account_set_key。由调用者再走 `targets.projection.normalize_projection` 的标准容量/结构校验并发布，不把 raw dict 当已验证证明 |
| projection_error | None 或 scope/account_set/error_code；明确空扫描为 ERR_ACCOUNT_SET_EMPTY，不出版空目录，不删除旧 snapshot |
| completion | None 或 request_id/scope/account_set/error_code；成功 error_code=None。只是匹配意图，调用者必须核对当前身份、配置 revision、scope、租约和请求状态，纯函数不能使请求 succeeded |
| response | connected；在线时另 account_set、method、account_sets_received、accounts_received。method 仅取服务器 previous 或源默认 rpa，原生身份/刷新租约由调用者附加 |
| lease_selection | 只取本次 body.account_set 或 body.account_dir 或空，不能回落到 response.account_set 中展示的旧选择。离线为空。旧路由传原始字符串，领取函数随即规范身份（含 strip）；本字段提前 strip，标准身份规范仍归领取边界 |

十四个缓存字段是 reported_account_sets/account_sets_seen_at、reported_accounts/accounts_seen_at、reported_products、reported_customers、catalog_seen_at、catalog_fingerprint、reported_stock_acc_groups/stock_acc_groups_seen_at、max_payload_version、companion_version、agent_device_name、agent_last_seen_at。两条净化路径保持独立，source 的 name-only/path-only/截断/真假值差异均保留。数量上限也分层保留，不先用 raw cache 截断后的数据构造 projection。

新纯输入错误 `companion.report_invalid`：body/previous 非 Mapping、时间不是 aware UTC；另将源非空非对象 mapping 的 AttributeError 适配为稳定错误。该错误不被当作空映射成功。接收时间采用注入时刻 ISO 8601；源数据库 NOW()::text 在差分边界被固定为同一时刻，未声称与 PostgreSQL 文本格式逐字相同。

原生调用边界仅对新 Site 新连接：身份域提供当前 AgentContext；body 无权选择 Site/connection/user；服务端锁内安装报告并记录在线状态。合法目录可 fresh，但 preparation 留空，既有 `connection.preparation_incomplete` 不解除。不导入旧配置、用户、目录快照或旧 token，不从旧数据填补新连接缺项。机器 HTTP 不能顺便认证其它 RPC，也不注册 ERP poll/ACK。

## 固定源差分与实际验证

`tests/test_cowork_companion_reports_source_parity.py` 在执行任何提取定义之前核对以下完整源码字节 SHA256；支持 `--source <root>` 和 `--source-stdin`（JSON relativepath→sourceText），两种入口均已实跑。

| 固定源路径 | SHA256 |
| --- | --- |
| routes/erp_agent.py | bf253777feb90eeb2c3d9f0bccdc5715a49ac8026a8a9710105b77a8106044bb |
| services/erp/express_push/agent_account_sets.py | 4a8861d46e748349e0a6b24e1b79b3d6ee404d6426505285476f5d9f2f4bb5a8 |
| services/erp/express_push/agent_reporting.py | c1ff3a33724ffb302c94e7c4ee44a1ecd7ba7753ca71c35f72a09c3247bf3a65 |
| services/erp/express_target_projection.py | 04cc019472c3bcdf5a776507c24ece1cd29f920f3062f64409a2a9c6008dc478 |
| services/erp/express_account_identity.py | 6271c1277c12bffbad09a02514ca29255bdb68f776106d5232f4f2241b8eaa4b |

差分执行实际源 `_run_agent_heartbeat`、`_ingest_target_projection`、`_offer_master_refresh`，实际源 sanitize/selected/store 函数（包括真实 SQL 参数生成），以及完整 `ingest_express_heartbeat` 分支。只替换身份、数据库出口、时钟和投影出版出口；数据库替身严格识别已知 SQL，未知 SQL/源吞异常均令测试失败。标准 normalize_projection 在出版边界前截取原始 observation，独立标准 normalizer 的迁移证据见 targets 包。本差分没有复刻一个新“旧计划函数”，也未导入或启动旧应用。

本机 Python 3.14.7、零第三方运行依赖：

```sh
PYTHONPATH=apps/pearnly_cowork:tests /opt/homebrew/bin/python3.14 -m unittest test_cowork_companion_reports -v
PYTHONPATH=apps/pearnly_cowork:tests /opt/homebrew/bin/python3.14 tests/test_cowork_companion_reports_source_parity.py --source /tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b
python3 scripts/check-architecture.py
```

结果：19 定向测试 PASS；62 个固定/边界与 1,200 个种子随机报告共 **1,262** 组 raw cache、投影、完成请求、response 及领取筛选值差异 **0**；4 个畸形 mapping 拒绝案例单列为稳定错误适配。覆盖缺席/None/空/错类型、raw/projection 接受域、50/3,000/20,000/100 数量边界、长度、bool/int 旧转换、缺席 accounts 与空 catalog、部分指纹、不同刷新账套和错误回报，以及展示旧选择但本次没有选择/只有不同 account_dir 的领取参数。Architecture PASS（0 violations）；git diff --check PASS。未运行 lab/原生业务测试，未部署，未连接真实 Companion。

本批语言包新增 36 条：原生新增十字段 label、leased 状态、companion.report_invalid、companion.report_busy，以及公开捕获链中的 22 个 `erp.target_projection_*` 错误码和 collector_not_connected 原因。只生成 Cowork en/zh/th/ja，每语 463 项；36 新项逐值匹配 TSV，42 个本链所需 label/code 在 **Core + Cowork** 四语缺漏 0、重复 0、新跨 App 冲突 0，旧 TSV 行未改。既有 Direction/Workspace 的跨 App 译文差异没有扩大。companion.report_busy 是原生鉴权/报告异常边界的稳定文案，不将可能携带凭据的异常细节写入公开错误；纯报告函数不产生此码。主任务已在真实隔离 Site 用 `frappe._` 执行 36×4=144 次查询，全部 PASS；该原生证据由主任务提供，本模块随后静态复核新增 busy 四语与 CSV=TSV。母语措辞仍须人工验收。

固定源 observation 里的 UI metadata label 保持来源协议，本批没有新增前端或 masters 渲染入口。未来 renderer 应按实体/字段语义映射专用词条，不能直接以全局 `Account`（现译“账号”）表示会计科目，更不能覆盖全站同名词条。`Customer/Accounts/Suppliers/Units/Branches` 等源标签也不包含在上述原生 label/code 覆盖结论里；应在实际渲染入口接入时补齐专用语义键与语言验证，不改纯来源合同来制造零差分。

最终 `scripts/check-i18n.py --strict` 相对 `70383`：两侧仍为 **147 处 / 43 文件 / 135 文案**，均 exit 1；按 file/kind/text 去行号后新增 0、减少 0；keep-cjk 登记仍 23 项，检查器和忽略表字节均未变。严格全仓检查仍失败，不能与本批词条覆盖通过混写。

原生验证清单交由本批集成模块执行，仅用合成新 Site 数据：撤销/轮换凭据和权限立即失效、body 串连接拒绝、普通无请求报告、缺席不擦缓存、明确空语义、同内容去重、选择改变失效旧准备、原生 CRUD/DocShare 拒绝、目录成功但 prep 仍缺失、私有响应及错误路径不漏 token，以及租约到期/supersede/rerun/旧回报拒绝。本纯包测试不能替代这些验收。
