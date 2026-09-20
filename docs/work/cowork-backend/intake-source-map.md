# Cowork 录入、识别历史与外部投递来源合同

日期：2026-09-20。所属 App：`pearnly_cowork`。本文件记录固定来源代码映射及纯合同实现，不是整项功能完成或业务验收记录。

## 取证范围

- 新平台基线：`1d7832e6c5c767b310542b9022750992ec9957b0`，任务分支 `codex/cowork-backend-migration`。
- 旧平台固定源码：`ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。
- 只读源码快照：`/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。下文旧路径均相对此快照，行号绑定该 SHA。
- 父任务已现场核验旧 Web/worker 分别承接 100% 流量，镜像 digest 均为 `sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226`；本分工未自行重查线上配置，发布前需按任务记录重新核验。
- 本分工只读旧源代码及测试定义，未读取真实业务数据、调用外部业务、启动容器或运行旧项目测试。获父任务追加授权后，仅写本文件、新平台 `contracts/history.py`、合同包入口及 `tests/test_cowork_history_contract.py`。

## 第一条后端主线

第一批应建立“有授权的公司账套 → 独立草稿及原识别基线 → 保存完整票面 → 幂等完成 → 历史按相同权限读回”。外部识别、推送消费者、Companion 与 LINE 连接未实现时，不暴露能伪造成功的接口。

不能把现有 `Intake Record` 的 `counterparty_name + net_amount + vat_amount + grand_total` 骨架视为旧业务合同。旧平台的业务载体是每张发票的完整 `pages`，摘要仅服务列表；旧 UI 文件名含 `dms-intake` 也不代表这条发票业务属于 DMS。

建议分包及前端接入条件：

| 包 | 后端范围 | 可以给前端使用的条件 |
| --- | --- | --- |
| A 录入与历史 | scope、来源合同、完整 pages、不可变首存基线、保存/确认/列表/详情；原件授权引用 | 真实 Site 中成功、越权、跨账套、重复确认、失败回滚均有证据；可先接人工录入与已有识别结果复核 |
| B 识别接入 | 对接 Core `execution`，文件验证、分组、公司归属、计费与检查点、识别结果保存 | 本地供应商替身与固定票样验证完整响应，不能把伪造 pages 当成 OCR 迁移完成 |
| C 推送合同 | 连接与年度账套投影、单据预检、持久投递意图、权威日志、重试与未知结果 | 无外部写条件下完成适配合同与状态机；真投递独立授权/验收 |
| D Companion | 配对密钥、心跳、账套/科目上报、lease/ack、租约及去重 | 用隔离 Agent 替身证明权限与重放安全；Windows 会计软件的真实写入单列未验收 |
| E LINE | 独立绑定/会话、附件下载入口、草稿编辑、目标刷新、确认、回执 | 复用 A–D 服务而非再造保存/推送；验签/错票据/解绑/重复事件通过后才接页面 |
| F 其它收料 | 邮件、汇总表回导、导出、归档与后续对账引用 | 每种入口独立保留来源和副作用边界，不能因 A 通过而一起标完成 |

这些是分包建议，不表示本次已完成或已授权外部执行。

## 实际入口和依赖

| 能力 | 旧入口/主实现 | 当前合同及新落点建议 |
| --- | --- | --- |
| Web 同步识别 | `routes/ocr_recognize_routes.py:26` → `services/ocr/recognize/core.py:65` | multipart `file/client_id/workspace_client_id/posting_kind/direction`；草稿识别；落 Cowork `ocr` 域，队列/供应商配额复用 Core |
| Web 异步识别 | `routes/ocr_jobs_routes.py:31,91` → `services/ocr/jobs/handler.py` | submit 返回 job id，查询完成结果与同步响应同形；不要复制旧 Cloud Tasks 部署架构 |
| 历史列表/详情/保存 | `routes/history_routes.py:52,85,218` → `services/ocr_history/queries.py`、`mutations.py` | 独立 `history` 域，所有单记录及批量入口用同一 scope；不是只隐藏导航 |
| 完成草稿 | `routes/history_routes.py:103` → `mutations.py:161` | `ids` 1–500，旧 Cowork 仅把 `staged` 翻为 false；不是发票过账或外部写成功 |
| 现/赊、货/费裁决 | `routes/history_routes.py:292` → `posting_manual.py` | payment=`cash/credit`，item_type=`goods/expense`；省略键不动，显式 null 清除人工裁决 |
| 公司/买方归属 | `routes/history_assign_routes.py:41,76`、`services/workspace/document_assignment.py` | workspace 与 client 是两条关系；公司归属有方向与税号校验 |
| 原件/PDF页图 | `routes/history_routes.py:383,410` | 授权读取、访问审计；一份源 PDF 可被多张记录引用，删除不能误删其它记录的共享原件 |
| 手动外推 | `routes/erp_push_log_routes.py:25,43` → `services/erp/confirmed_push.py:160` | 目标端点、文档公司、年度账套和刷新证据全部服务端再校验；落 Cowork `push`、`connections` 与适配器域 |
| 日志/重试 | `routes/erp_push_log_routes.py:86,129,156,226` | `erp_push_logs` 是真值；重试更新原日志并加次数，不造一条新成功行掩盖旧失败 |
| 端点和主档投影 | `routes/erp_endpoints_routes.py`、`erp_mappings_routes.py`、`erp_target_projection_routes.py:126` | `endpoint_id` 不等于年度账套；保存目标选择时保留投影 revision / refresh request id |
| Companion | `routes/companion_installer_routes.py:20`、`routes/erp_agent.py:191,223,294,352` | 下载、配对、心跳、领取、回执；只允许 Agent 出站，不从云端主动连入客户机 |
| LINE 识别 | `services/cowork_line/webhook_documents.py:66` | 验当前绑定/目标后下载文件，调用相同识别核，`source=cowork_line`、`staged=True` |
| LINE 草稿 | `routes/cowork_line_intake_routes.py:147,157,201,263,276,286,296` → `services/cowork_line/intake.py` | 读、主档刷新/查询、整批保存、确认、丢弃、原件；API 参数不能信任浏览器自报身份 |
| 邮件 | `routes/email_ingest_routes.py` → `services/email_ingest/email_ingest_pipeline.py:150` | `source=email`、保留 `source_ref`；外部读取/自动推送与手工草稿是不同执行边界 |
| 汇总表导入 | `routes/summary_import_routes.py:29,73,86,111` → `services/summary_import/commit.py` | parse→validate→commit；`summary_table_batch` 来源存在额外正式单据副作用，单列核实，不能搭便车迁入普通确认 |

架构依赖建议：`contracts` 无 Frappe/IO；`history/intake/ocr/push` 各自拥有业务服务；Web/LINE 只作入口；`connections/companion/adapters` 拥有外部协议；共享身份、票据基础、计费和执行器调用 Core 的公开契约。Core 不导入 Cowork，Cowork 不依赖 DMS 业务包。

## 数据字段必须分清

| 字段组 | 真实旧字段 | 保持的含义 |
| --- | --- | --- |
| 租户/操作者 | `tenant_id`, `user_id` | 认证层确定；不能让普通调用方指定任意 tenant/owner |
| 做哪家公司账 | `workspace_client_id` | 文档所属主体；不等于当前浏览器选中的公司，更不等于买方客户 |
| 买方客户 | `client_id` | 客户关系及员工客户可见范围；不得替代账套 scope |
| 连接所在公司 | `connection_workspace_client_id` | LINE 目标可能与票面最终账套不同；`intake.py:123` 特别保留这个字段 |
| 外部年度账套 | `endpoint_id`, `account_set_key/account_set`, `account_root` | MR.ERP 的年度/数据库选择与 Express 根目录/账套都需校验；不得任取首项 |
| 目标新鲜度 | `target_refresh_request_id`, `target_projection_revision`；LINE 对应 `catalog_refresh_request_id/revision` | 用户最后选择的主档版本；不是任意字符串装饰 |
| 原票与复核 | `pages`、`ai_raw`、`fields_edited_at`、`edit_count` | pages 可编辑；ai_raw 首存不可覆盖；保留页号、复制/重复标记和其它证据 |
| 多票来源 | `source_pdf_id`, `source_page_indices`, `source_index`, `source_total`, `page_count` | 一文件可拆 N 发票，一发票可有多页；不能仅保留第一条 |
| 文件 | `filename`, `file_hash`, `file_size_kb`, `pdf_storage_path`, `pdf_size_bytes` | hash 是文件缓存键，不是业务推送幂等键；新平台用受权 File 引用替换旧路径 |
| 识别摘要 | `invoice_no`, `invoice_date`, `seller_name`, `total_amount`, `confidence` | 从 pages 派生；摘要不是票面唯一事实 |
| 草稿/识别质量 | `staged`；列表派生 `confirmed/pending/failed` | `confirmed` 旧列表表示高置信且票号金额齐，并不表示用户确认，更不表示推送成功 |
| 归档/审计 | `archive_name`, `category_tag`, `seller_name_official`, `seller_name_verified` | 官方核验名与原识别名并存，不覆盖原票；编辑审计不能由前端伪造 |
| 过账声明 | history `posting_kind`，fields `direction`, `posting_payment_manual`, `posting_item_type_manual` | 人工声明与推导结果可追溯，不能把未声明替换成默认库存/服务 |
| 推送 | log reference、`status`, `request_body`, `response_body`, `retry_count/attempt`, `next_retry_at`, `lease_owner/expires_at` | 权威投递记录；history 的推送状态仅镜像 |

字段来源：`services/ocr_history/mutations.py:319-355`、`queries.py:186-232`；分票保存见 `services/ocr/recognize/persist.py:178-186,262-293`。原新平台骨架字段如需保留兼容，应明确只是投影，不能在保存时反向覆盖 pages。

### pages 的票面合同

常见复核字段包括 `invoice_number`（读旧数据时兼容 `invoice_no`）、`date_raw`、`date`、`seller_name/seller_tax`、`buyer_name/buyer_tax`、`subtotal`、`vat`、`total_amount`、`wht_amount`、`discount`、`document_type`、`category`、`direction`、`items`。明细包含名称/描述、数量、单价/金额、单位及适配器需要的商品/科目等选择。不可仅依据此表裁掉未列出的合法 OCR 字段。

- `src/home/dms-intake-review-fields.ts:9-51`：purchase 对手方是 seller，sales 对手方是 buyer；仅 sales 的 `simplified_tax_invoice/receipt` 可省匿名买方税号。复核黄色缺字段提示和服务端拒绝不是同一强度，迁移时明确各自用途。
- `routes/history_routes.py:193-226`：编辑票面 `date_raw` 后服务端重算公历 `date`；无法识别或把佛历年份直接塞进 date 要拒绝。日期不能依赖客户端语言猜测。
- `services/erp/express_push/direction.py:34-99`：显式方向优先，别名 income→sales、expense→purchase；其次用自家税号与票面双方判定；两边都中、都不中、无有效税号保持 ambiguous。批级方向不覆盖逐票已有方向。
- `services/erp/express_push/posting_kind.py:1-13,46-67`：只认 service/stock，None 是未声明；显式参数优先于票上声明；故意没有账套级默认值。
- 金额计算迁移到纯 Decimal 契约，但保留票面原值和外部系统映射语义。`services/erp/invoice_amounts.py` 的整单/明细校验实际被 `intake_bridge/purchase_leg.py`、`sales_leg.py` 正式建单链使用；不能不加区分地把它升级为所有 Cowork 草稿保存的强制校验。

## 来源与产品边界

| 来源或入口 | 固定源码事实 | 新平台处理 |
| --- | --- | --- |
| Cowork Web 上传 | `services/ocr/entrypoints.py:33-35` 返回 `manual`；history 列表 `source=upload` 只是筛选别名，`list_status.py:83-85` 转成 manual | 保存 canonical source，前端“上传”标签不能改写来源合同 |
| Cowork LINE | `webhook_documents.py:97-106` 明确 `cowork_line` 且 staged=True | 不自动当作旧 `line` 或 `line_bot`；由受信 LINE 入口设置来源 |
| 邮件 | `email_ingest_pipeline.py:150-165` 使用 email + source_ref | 保留单独后台入口、来源关联、去重与副作用策略 |
| ERP Web / ERP LINE | `erp_web` / `line_erp` | 不属于 Cowork 外推主线；`internal_push_guard.py:5-15` 在用户及记录两层拒绝外推 |
| 普通 Cowork 确认 | `erp_confirmation_access.py:42-50` 仅允许 ERP entry 建正式单据；Cowork 前端 `review-convert.ts:225-260` 跳过转换 | 不迁入 purchase_docs/sales_documents 建单；完成只使草稿进入历史 |
| DMS 身份证/订车 | `erp_push.py:313-327` 拒绝 `mrerp_dms` 作为发票目标 | DMS 已有业务保持独立，不能因文件名含 dms-intake 迁错 |
| 汇总表 | `summary_import/commit.py:2-15,45-77` 仍写 summary_table_batch 后桥接正式单据 | 与普通录入有真实行为差异，单独列为归属/迁移项；不能偷偷删除或移入通用确认 |
| workorder_classify 等历史来源 | `ocr_history/list_status.py:65-70` 在聚合中排除工单双写；列表与汇总本来不同 | 先登记真实入口及产品归属，不把旧源全部字符串白名单化并开放写入 |

## 公司账套与权限

1. 入口身份→当前产品 entitlement→用户角色/范围→文档所属公司，沿同一合同传递到列表、详情、原件、修改、确认、导出、日志和重试。Site 隔离不能代替同 Site 多公司/成员范围。
2. `services/workspace/document_assignment.py:20-41`：purchase 以 buyer 为自家主体，sales 以 seller 为主体；根据税号/名称匹配本租户公司。多候选或查询失败拒绝，不能默认第一条。
3. `services/ocr/recognize/workspace_assignment.py:26-39,206`：自动建立公司需 `settings.workspace.manage` 且不为 assigned 范围；整批先检查再创建。同名部分缺税号的同批票合并，但两个不同非空税号不能合并。
4. `confirmed_push.py:223-237`：推送以历史持久化 workspace 为准；请求传来的 workspace 不一致返回 `erp.history_workspace_changed`。前端公司切换不能把旧草稿重新挂到另一家公司。
5. `intake.py:24-53`：LINE 草稿必须属于当前 tenant + LINE 身份会话 + actor，并仍 staged；整批保存必须完整、顺序匹配 history_ids，不能混入其它草稿。
6. 旧代码存在过渡宽松点：history 列表 `queries.py:83-86` 包含 workspace IS NULL；详情路由 `history_routes.py:85-95` 未传 workspace；`workspace_context.py:131-158` 仍有默认账套回落。不能把这些描述为已具备严格隔离。新平台缺失 scope 拒绝、未归属隔离收件区、全入口统一权限是有意收紧，需写入变更记录并覆盖正反测试。

## 确认、重复及状态的真实语义

- Web 主线 `src/home/dms-intake-invoice-submit.ts:184-241` 先保存所有编辑值，成功后 commit 草稿，然后按用户选择仅完成、导出、推送或两者。保存/commit 失败必须回到复核；不能继续推送。
- Cowork 页面“确认”在旧端一部分是浏览器态，最终完成才真正 PUT pages + commit。新平台将确认原子化持久化可以作为可靠性改进，但不得混入正式 ERP 建单。
- `mutations.py:161-188`：commit 幂等，只翻仍为 staged 的可见记录。新 API 应明确返回实际完成/已完成/失败范围；批量存在越权或缺失记录时不能靠数目差假装全部完成。
- 识别质量状态是 `confidence + invoice_no/total_amount/seller_name` 派生（`list_status.py:3-16`）。新模型若有人工审核状态，需与质量和 push status 分开，不共用一个 status 字段装所有含义。
- OCR 重复检测 `persist.py:188-247` 只是 exact/likely 警告，不阻止录入。文件缓存 `hash_dedup.py:24-59` 只用非草稿、有效、鲜度窗口内、授权范围内结果；`recognize/core.py:141-148` 对交互式草稿不直接复用旧 history id。不能给 `file_hash` 全局唯一后拒绝用户再次上传。
- 业务外推去重需 endpoint + account_set + actor + history/票号卖方，见 `push_dedup_store.py:13-58`、`cowork_line/push_dedup.py:8-29`；不能仅按发票号码全站唯一，也不能换年度后错误拦截。
- 新选择的年度账套需要服务端刷新证据和投递 reservation（`confirmed_push.py:254-285`）；外部结果未知或落日志结果不明返回 manual / push_result_unknown，不能自动重做（`:31-137`）。
- 手工重试更新同一条原日志、加 retry_count、清既有自动重试计划（`erp_push_log_routes.py:156-218`）。成功日志拒绝重推。`skipped_dup` 是中性结果，不是假失败也不代表新建成功。

## Companion 与 LINE 不可省略的后端合同

Companion 旧实现 `express_push/agent_store.py:2-9,68-94`：token 明文只返回一次，库内 SHA-256；已有配对非 reset 不旋转。`routes/erp_agent.py:223-229` 心跳上报 account_sets、accounts 等主档；前端根据投影选账套。

`routes/erp_agent.py:243-298`：lease 1–50 条、120 秒、endpoint/账套白名单及租约 owner 校验。`agent_store.py:338-358`：success 终态幂等；waiting_lock 释放租约而不烧次数；needs_mapping/needs_review 转 manual；failed/rolled_back 按上限处理。`agent_store.py:27-37,144-149`：明确放行重复写的 duplicate_confirmed 载荷不允许租约超时自动重投；收不到 ACK 必须进入人工未知结果。

LINE `routes/cowork_line_intake_routes.py:32-45` 保存合同包含 records、两种 workspace、endpoint、direction、adapter、account_root/set、catalog 刷新证据、posting_kind/payment。`intake.py:227-275` 确认前刷新当前目标并核验所有原记录；只有 committed 等于去重 history 总数才清会话。返回 saved 与 push_ok/status 分开，pending/queued 不能显示“已入账”。

`services/line_platform/draft_validation.py:20-55` 当前服务端门：不是发票、重复标记、日期、方向相关头字段、非空 items、明细名称和数量；Cowork 不强制 ERP 的逐行 posting_kind。目标特定预检仍保留：`document_preflight.py:76-106,171-187` 的 Express 必须声明 stock/service；MR.ERP 必须明确 cash/credit，目前 purchase+cash 明确为未验证而阻止，不能迁移时悄悄开放。

## 已发现的源侧风险和待补范围

- `src/home/dms-intake-invoice-submit.ts:245-260`、`review-convert.ts:188-212` 用无界 Promise.all 保存批量历史，后端新合同宜提供有界批处理/逐项结果，不能让前端失败丢项。这不是本轮改前端授权。
- history 来源筛选只把 upload→manual，其 line 筛选不自动涵盖新的 cowork_line（`list_status.py:83-85`）；新平台应定义通道展示映射，保留 canonical source，避免继续漏看。
- 旧 `ai_raw` 是首存基线，不保证等同供应商未经分组的原始网络响应；raw_pages、分组 pages、编辑 pages 的含义需分别记录，不能声称保留了未保存的原始响应。
- 字段、routes 与测试清单只能证明实现路径存在，不能证明每个路径在固定生产配置中启用。本分工未核验 feature flags、真实业务数据、外部账套及 Windows 执行结果。
- 邮件/导出/汇总回导、保留期、归档模板、科目学习、官方名核验、异常记录、LINE 回执补发仍是迁移清单项；只读映射不等于本批实现。

可借用的旧测试场景定义包括 `tests/unit/test_history_routes_contract.py`、`test_ocr_history_workspace_read.py`、`test_document_workspace_assignment.py`、`test_posting_kind_resolution.py`、`test_cowork_line_intake.py`、`test_cowork_line_push.py`。需按新平台真实 DocType/事务/API 改写关键断言；不能简单复制旧 mocks 当 Site 验收。

## 本批已落纯合同与明确差异

新落点 `apps/pearnly_cowork/pearnly_cowork/contracts/history.py`，不依赖 Frappe、数据库、时钟或外部服务；运行服务拥有权限、事务和原生持久化。

| 新接口 | 来源与保持规则 |
| --- | --- |
| `normalize_source` | 识别 `manual/cowork_line/summary_table_batch`；本批公开创建只放行 manual；明确拒绝 erp_web/line_erp。email 等真实旧入口未迁移时不开放伪造来源 |
| `prepare_pages` | 深拷贝完整 pages，保留副本/重复标记、所有合法 JSON 未知字段和不完整票面。沿旧 history PUT 从 date_raw 派生公历 date，未提供 date_raw 时保留既有 date 并挡佛历 ISO 年 |
| `extract_summary_fields` | 保持优先非副本/非重复首个有摘要页，再按 4 个关键字段计数回落；不拼接多页摘要、不重算总额、不将 invoice_no 别名当 invoice_number |
| `list_status` | 原 SQL 的 NULL 判定及精确 confidence='high'；不等同人工审核、commit、ERP 过账或推送结果 |
| `initial_history` | 顶层摘要、list_status、pages、ai_raw、source、confidence、posting_kind、staged、source_page_indices。ai_raw 支持原 JSON 结构或字符串；显式 None 首存取调用者原 pages 的独立快照，日期派生不改它 |
| `commit_history` | 独立深拷贝并且只将 staged 变 false；幂等；没有正式单据创建或外部动作 |
| `source_page_indices` | `invoice_grouper.py:269-288` 的原文件 1-based page_index，缺失按页顺序推导并排序；不改原 pages，不混用 LINE 预览的 zero-based 索引 |
| `clean_summary_fields` | 单独复用 `summary_import/commit.py:31-42`：仅剥根级下划线键，已有 direction 优先，否则接 _direction；不用于普通 pages，不附带正式单据桥接 |

明确不声称逐字兼容的改进：

- 旧摘要金额用 float，新接口使用有限 Decimal 的字符串或 None，保留精度（如 `9007199254740993.01`），非有限金额文本不算有效摘要；原票字段不因此修改。这是 API 金额类型的有意变化，前端需按字符串消费。
- 旧两位年份缺省读取曼谷当天，本纯合同要求调用服务注入 `anchor_year`；不提供时报 `history.date_anchor_required`。四位日期不依赖当前时钟。
- 非 JSON 数据、非字典 page/fields、非正整数 page_index 等畸形结构提前明确报错，避免源代码下游类型崩溃；不增加税号、明细、发票号、金额、方向等草稿必填业务门。
- 旧 insert 可存空 pages 以表示失败识别，合同保留；旧 PUT 拒绝空 pages，运行更新入口单独保留此门。人工创建默认 staged=True 是新入口选择，内部迁移可明确 staged=False。
- 首存 ai_raw=None 时取调用者原 pages（日期派生前）；提供 ai_raw 时不改变结构/字符串类型。更新入口不得再调用首存方法覆盖 ai_raw。

离线证据：`PYTHONPATH=apps/pearnly_cowork python3 -m unittest discover -s tests -p test_cowork_history_contract.py` 20 项通过，其中 332 组摘要组合与冻结旧算法比较；另从固定快照直接 AST 提取旧纯函数，320 组摘要、791 组日期/月份名差分通过（金额按数值比较，非逐字类型一致）。`python3 scripts/check-architecture.py` 通过，0 violations。未启动 Site；这些证据不等于权限/事务/文件服务/外部识别可用。

## 附件与下载的独立来源审查

| 入口 | 必需身份与关联 | 真实行为 |
| --- | --- | --- |
| Web `/api/history/{record_id}/pdf`，`history_routes.py:383-407` | 当前登录 user、plan 历史权限、`tenant_record_scope`；history id 服务端查留底路径 | 404 区分未关联/文件缺失；解密后 `application/pdf`，RFC 5987 中文/泰文文件名；成功访问记审计 |
| Web `/api/history/{record_id}/page/{page}.png`，`:410-440` | 同上 | page 参数传给 1-based renderer，私有缓存 300 秒，X-Page-Count 返回整个 PDF 页数 |
| LINE `/api/cowork-line/intake/draft/{draft_id}/records/{history_id}/page/{page}.png`，`cowork_line_intake_routes.py:296-317` | 独立 bearer scope/audience；draft id；当前绑定 membership/tenant/user 与 token 一致；当前 session nonce；history id 必须在该会话的 history_ids | LINE URL 使用 zero-based 页号，负数 404，传 renderer 前 +1；只读本会话引用的留底 |

需持久化或由授权记录提供的真实字段是 `history_id`、tenant/user、workspace/client、`filename`、`pdf_storage_path`、`pdf_size_bytes`、`source_pdf_id`、`source_page_indices`、`source_index`、`source_total`、pages 中的 `page_index/page_number`。新平台宜用私有原生 File 的服务器引用替代可写入的裸路径，但不能仅用 filename 或前端传来的 URL 关联文件。共享文件可有多条 history 引用，附件归属与每条 history 的公司权限必须一同核实。

必须写入后续文件包验收的源侧差异/风险：

1. `queries.py:318-358` 取件只返回 path/size/filename；Web 路由未传 workspace。对 Cowork，`team_access.py:179-199` 默认返回租户共享范围，不能据此声称原文件已按公司/客户限制。新平台应复用 history 同一严格权限合同，权限撤销后旧文件链接也必须失效。
2. LINE `_draft_identity`（`:116-144`）重读当前绑定、20 分钟 token 和 nonce；`intake.pdf_info:306-315` 校验会话 history 集合后直接取文件，未复用 `_assert_owned_staged`。新实现宜统一核对记录 actor、workspace、staged，并避免签发通用 File 下载能力扩大一次性票据范围。
3. `pdf_utils.py:95-120` 将越界页号夹到首/末页，且读取一张分组 history 可渲染整个共享 PDF，未以 source_page_indices 限页。若同一原 PDF 的发票分配不同公司，按整份 PDF 开放会泄露别家公司页；新平台需明确按允许页生成受限预览，或验证整份文件涉及的所有公司权限，不能仅隐藏前端翻页。
4. `mutations.py` 首存留底路径为空，后台才生成和回填（`persist.py:274-280`）；“已完成识别”不等于“文件可下载”。新服务需返回真实待生成/可用/失败状态，且回填针对明确 history id 集合与身份，不扫描相同文件名猜关联。
5. “PDF 留底”不总是上传字节：`pdf_backfill.py:20-49` 可加入搜索文字层，失败才回退原文件；LINE 图片先转 PDF（`line_erp/intake.py:10-44`，Cowork `intake.py:354-363` 当前复用）。若新平台保留原件和派生 PDF，须分开记录用途/散列，不能把派生件称为字节级原件。
6. `pdf_storage.py:39-83` 使用服务器生成相对路径、可选密封存储、读取统一解密；明文 size 是展示口径。迁移采用原生私有 File 时仍须明确静态加密与备份方案，不能直接公开旧存储目录或复用旧路径前缀校验。
7. `history_routes.py:353-365,460-472` 删除前检查全部 history 仍否引用共享留底；删除记录不等于立即删文件。新平台需要同等引用保护；本批未实现删除/文件存储，也未执行任何真实删除。
8. Web 下载成功记录 `OCR_PDF_VIEWED`，页图记录 requested page；LINE 页图当前未调用同一审计函数。原生新接口应记录当前调用者、记录、文件/页和票据来源，不能把下载 HTTP 200 当权限验收完成。

本文件交付来源映射与纯合同；运行实现、真实 Site、外部业务、浏览器与用户验收分别由主任务记录回填，不能据此标记 Cowork 已迁移完成。
