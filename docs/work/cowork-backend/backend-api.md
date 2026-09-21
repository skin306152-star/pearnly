# 原生后端接口与前端讨论边界

最新补充（第十六批）：旧 Companion 的可选运输入口已实现并在隔离 Site 验证，见本文末尾和[来源合同](sixteenth-source-review.md)。默认部署未启用，Windows现用配置未改；旧章节中的待办按后续批次修订读取。

本批完成的是“公司/客户授权 → 保存完整票面草稿 → 复核保存 → 幂等完成 → 历史回读”的后端闭环。已可据此讨论第一屏录入/复核的交互，但**不是整个 Cowork 迁移完成**。未编写页面或修改现有导航；现有 `Intake Record` 页面仍是早期样板，不是这些接口的前端。

2026-09-21 第四批继续接通PDF/图片识别的原生任务、检查点、授权纠错例库、整批草稿及账单回执；验证为隔离Site+假外部出口。仍按用户要求先补后端，不自行启动前端施工。[识别执行接口](invoice-execution.md)是新增RPC和状态的权威清单。

## 原生落点

| 域 | 本批实现 | 依赖及数据归属 |
| --- | --- | --- |
| access | 产品、购买功能、角色、公司和买方客户范围；原生列表/详情权限；阻断 DocShare 回退 | Core 仅提供 entitlement；当前 Site 的 User / Role / User Permission |
| masters | 公司、买方客户创建及稳定分页 | Cowork Workspace / Cowork Client；不存外部凭据、不自动开外部账套 |
| history | 完整 pages、首存 ai_raw、摘要、保存、完成、版本冲突及请求幂等 | Cowork OCR History；依赖 access 和纯 contracts |
| contracts | 来源、日期、摘要、方向、过账声明和外推选择预检 | 无 Frappe / 网络 / 数据库 |
| files / history.recognition | 精确原件、按页授权、不可变整批关联与跨操作者幂等回读 | Cowork Source File + 原生私有 File；files 不反向导入 history，关联查询只读当前 Site |
| stocktake | 原生任务/商品/扫描/回执、累计、关闭和 JSON 报告 | 四个 Cowork Stocktake DocType；不写库存账；详见盘点记录 |
| ocr | PDF/image与普通表格/文本识别、受控供应商调用、队列/检查点、冻结结果、授权纠错、原子草稿及幂等计费回执 | Cowork OCR Batch/Correction + Core Work；假外部出口下真实Site验证，未部署 |
| reconciliation | 第一轮强配对纯函数 | 对账任务持久化和后续配对/银行/GL/VAT仍未迁 |

DocType 控制器留在原生目录，hooks只登记入口。第四批Core仅修正无失败回调时的收尾；DMS、Control和前端代码未改。术语新增沿既有TSV→Frappe CSV入口，只生成Cowork语言包。

## 业务 RPC

前缀 `/api/method/pearnly_cowork.`。写入仅 POST；返回遵循 Frappe 的 `message` 包装和异常响应，不另造产品壳。

| 方法 | 参数与结果 | 配置/角色与验证 |
| --- | --- | --- |
| `masters.api.create_workspace` | display_name、可选 tax_id → 公司 name | master_data + Cowork Admin；真实 Site 原生保存 |
| `masters.api.create_client` | workspace、display_name、可选 tax_id → 买方 name | 同上，验证公司属于当前 Site |
| `masters.api.workspaces` | start、limit → 已授权公司；limit ≤ 200 | intake / ocr_history / master_data 中至少一项及对应角色或 Cowork Admin |
| `masters.api.clients` | workspace、start、limit → 已授权买方 | 公司范围 + 独立买方范围；同名按 name 再排序，已验证第 201 条可达 |
| `history.api.create` | workspace、pages、request_key、可选 client → 草稿 | intake + Cowork Intake 或 Cowork Admin；公开入口只保存 manual，不能自报 OCR 来源、置信度、owner 或首次模型结果 |
| `history.api.detail` | name → 完整票面及审核元数据 | intake / ocr_history 及角色；范围与列表相同 |
| `history.api.records` | workspace、staged、start、limit → 摘要页；limit ≤ 100 | 默认 staged=0，仅已完成；明确 staged=1 可取草稿 |
| `history.api.save` | name、完整 pages、expected_version → 更新后票面与 revision | 先锁定当前行，再核对版本与范围；不接受空 pages |
| `history.api.complete` | records=[{name, record_version}]，1–500 条 | 整批授权、排序加锁、版本核对；只翻 staged，不创建 ERP 单据、不推送；重复完成不重复写入 |

没有新增产品菜单，故不存在本批菜单点击去向；上述入口由服务层、原生资源接口和 WSGI API 测试验证。不能因为未来功能列在导航目录里就标为完成。

权限规则：

- 普通员工必须有对应功能角色和显式公司授权；记录有 buyer client 时还需该买方的独立授权。缺少授权不回落默认公司。
- 作为成员范围使用的 User Permission 必须 `apply_to_all_doctypes=1`；仅限定某一其它 DocType 的授权不能扩大成公司成员。
- Cowork Admin 可管理当前 Site 中的公司和客户，但仍受购买功能限制。System Manager 本身不替代 Cowork 产品角色。Frappe 的平台 Administrator 是受信维护账户，不授予客户。
- 原生详情拒绝时抛 PermissionError，防止框架再通过分享放行；列表遇当前用户/everyone 的遗留 DocShare 明确拒绝，需移除遗留分享后重新按公司授权。不自动删除他人的分享。
- 原生创建/更新不能携 JSON flags 绕过服务流程；只有内部对象能力可以保存。HTTP PUT 不带版本已实测拒绝，原数据未改变。

## 前端须消费的真实含义

1. `staged` 表示草稿/已完成，`quality_status` 表示识别质量，两者分开。quality_status=confirmed **不表示人工完成或外部入账**。
2. pages 是完整票据事实，包含 WHT、方向、复制/重复页及未知合法证据字段；摘要只用于列表，不能反向覆盖票面。
3. ai_raw 是首存识别快照，复核不可覆盖。手动输入的 ai_raw=null，不假装来自 OCR。
4. total_amount 摘要为 Decimal 字符串，保留金额精度；这是相对旧 float API 的明确变更。票面金额原值保留。
5. request_key 用于同一用户/公司的一次请求重放；同 key 不同内容拒绝。它不是文件 hash 或发票业务去重键。数据库唯一约束与两连接竞争已验证。
6. 保存必须使用回读 revision；发生冲突应重新加载，不能自动覆盖。批量完成失败保持草稿，不能继续显示入账成功。
7. 新公开手工录入接口没有 source/confidence 等参数。内部 `storage.create_recognized` 只负责落已验证结果，**并未调用模型或实现 OCR 队列**。

## 未迁清单及下一批

| 能力 | 当前状态 / 继续工作 |
| --- | --- |
| OCR 上传、分票、缓存、计费、队列及失败恢复 | PDF/image已接原生执行、供应商适配、文本/图片路径、授权例库、原子保存及计费回执；普通表格/文本已有逻辑区段与字符计费执行；真实付费模型/账单验收、旧例库回灌、非staged缓存仍待补；专用roundtrip已接第六批本地后端，真实ERP重推未接，见执行合同 |
| 原件、PDF 预览、附件 | 新上传原件、整份及按物理页读取已实现；跨公司/同页多票拒绝越权，原生下载/ZIP旁路已堵；旧可搜索PDF、加密文件及历史回灌尚未迁 |
| 外推、目标刷新、年度账套、权威日志 | 第八批已接原生连接、目录申请/快照、准备RPC、持久防重/attempt/写锁与本地回执；真实采集、运输消费者、外部未知回读及旧推送记录导入未迁 |
| LINE、Companion | 来源映射已完成；验签、当前绑定、单次票据、租约/ACK、消息回执等仍待迁；Windows 实际运行来源待核验 |
| 对账 | 仅第一轮单号强配对。金额取整/容差与 Decimal 有已证实差异，第二轮、银行、GL/VAT 未迁 |
| 盘点 | 原生任务/商品/扫描/回执、增改作废、关闭、JSON 报告已迁；XLSX字节适配、照片、LINE、legacy直接盘点、历史数据回灌及10,000行容量尚未验收 |
| 汇总表、邮件、归档、导出、删除、自动主档匹配 | 来源已登记，业务链未迁；汇总表旧桥接正式单据行为不能混进普通完成 |
| WeKan | 旧 VM 已停止；当前运行来源证据不足，未启动或移植 |
| 现有早期 Intake Record 页面 | 没有接新接口；不作为功能验收 |
| 员工界面、授权操作和各页面导航 | 本批仅后端原生 Role/User Permission；前端与控制面易用性在后续接入时核对 |

前端讨论从录入/复核这条已验证接口开始，以现有 DMS 的原生 Desk 交互为参考；不搬旧 UI。附件预览、上传识别或推送按钮必须等相应后端完成，不能用本批人工 pages 接口伪装这些能力。

## 第二批新增入口

`files.api.upload` 仅 POST multipart，参数 `workspace/request_key/client?` 与 `file`，返回 name、filename、kind、page_count、byte_size、sha256 等投影，不返回原生 File URL。上限沿旧固定源码为 100 MiB、PDF 1–50 个实际解析页；图片保持一页语义。PDF/图片须能实际解析，超大图片预览有额外像素上限。

`files.api.metadata(name)`、`original(name)` 校验整份权限；`page(history_name, physical_page)` 只输出已封存清单中允许的物理页 PNG。整份返回原字节并强制附件，页返回 PNG；均 `no-store/nosniff` 并调用原生 Access Log（异步日志仍需生产调度器刷新）。入口要求 intake/ocr_history 功能及对应角色、有效公司/买方范围。原生 File URL、File metadata、ZIP、修改和删除不代替这些 API。

内部 `history.recognition.persist_batch(source_file, groups, request_key)` 无 RPC 白名单，不执行 OCR；groups 包含 workspace/client、完整 pages、source、confidence、ai_raw 与 source_page_indices。完整绑定一次提交并保存生成的 history IDs；相同批次重放返回原记录，封存后不再追加或更改关联。原件初始范围与每个结果范围都必须授权。重复物理页号归一为权限集合，所有票面仍保留；History.file_hash 未用于新SHA缓存，旧首条成功结果缓存规则仍待完整OCR接线。

盘点 RPC 与输入输出见 [盘点后端](stocktake-native.md)，File 的框架适配及有意变化见 [原件守卫](files-guards.md)，识别来源与断点见 [OCR 迁移](ocr-pipeline.md)。这些入口均没有新增产品菜单。


## 第五批结构化原件合同

`ocr.api.submit` 复用现有 POST/幂等入队契约，增加普通 xlsx/xlsm/csv/tsv/docx/doc/txt 结构化解析。旧xls路由只支持以该后缀命名的OOXML，真实BIFF二进制xls仍不能解析；DOC保持旧尽力解码，不能宣传完整Word支持。识别、解析、权限和计费分别由对应域负责，不接受前端传模型、计费单位或原件清单。

- Batch详情新增 `reference_kind`（physical_page/logical_section）、`billing_kind`（pdf/excel）、`billing_units`。其中 `page_count` 是模型结果逻辑单元数；表格绝不可当PDF页数或字符收费单位。老Batch没有新字段时仅回落到旧PDF/image合同。
- `files.api.metadata` 另返 `reference_kind/reference_count/reference_unit/coverage_complete`；原件原始 `page_count=1` 文档占位不参与表格授权。History详情 `source_reference_kind` 说明 `source_page_indices` 的真实单位；document 的 `source_pdf_id` 为空，下载用 `source_file`。
- `accepted=false, code=ocr.table_*` 表示受理前拒绝；不建立模型任务、不扣费。超过30,000模型输入字符、结构资源上限和损坏文件不能误显示识别完成。第六批roundtrip专用路径见下文；单sheet的LLM提取结果仍须人工复核，未进行真实模型质量验收。
- 原件按字节校验、逻辑清单与解析投影摘要不可变。没有完整关联所有sheet或旧解析明显漏读内容时，其他用户不能据一张票据获得整份原件；上传者仍须当前公司/买方权限。文档无PDF物理页预览。

详细来源、限制和有意变化见 [表格输入](table-inputs.md)、[表格识别](table-engine.md)、[原件逻辑清单](table-source-manifest.md)。本批没有新增菜单或页面，前端尚未接入。

## 第六批复核工作簿合同

- `POST pearnly_cowork.roundtrip.api.export(names)`：1至100个当前Site历史ID，必须全部可读且同公司/客户，不跳过缺失或无权记录。直接返回固定泰文四表xlsx私有附件；没有导航入口，不能宣称UI交付。第十四批接入服务器 History 状态，第十五批接入当前 actor 的原生成功回执单号/主档动作；附加事实仍须当前 push_log 功能、角色及连接授权，未授权只保留原历史导出，不接受浏览器伪造事实。票据版本改变时不沿用旧行新建标记，详见[第十五批](fifteenth-source-review.md)。
- 仍由 `files.api.upload` 上传，再调用 `ocr.api.submit`。真实首行指纹命中后确定性解析，零模型调用；`reference_unit=roundtrip_document`，每解析单据一个逻辑编号。表名决定方向；待判留人工、不套整批方向。新建History保留旧记录键，旧记录和ERP数据均不改。
- 原记录PRN键是内容声明，须在受理、执行、保存前重验当前Site/公司/客户。旧平台UUID尚未映射时明确拒绝。不可变原件清单与冻结结果保存权威引用；将来ERP推送不能信任可编辑pages.fields.history_id替代这些证据。
- 承接旧Excel字符计费；生成新History和封存原件一次提交，计费失败只补回执。命中空表、带空格异常表名、50空行后残留数据、解析失败均返回稳定错误码，不退模型。金额不能无损往返、未绑定的多页历史明确拦截导出。
- [整链合同与有意变化](roundtrip-native.md)、[独立审计](roundtrip-parity-audit.md)。外部ERP重推、旧数据映射、其它导出格式、前端及真实业务验收仍未完成。

## 第八批连接与推送准备合同

连接域入口为 `pearnly_cowork.connections.api`，具体参数、当前授权与不泄露凭据的DTO见[原生连接](connections-native.md)。管理要求 master_data + Cowork Admin；使用要求 intake + 当前角色、公司/买方与显式连接授予。目录请求仍显示 collector_available=false，本批没有后台采集器，不能把请求成功理解为真实目录已经取得。

推送入口为 `pearnly_cowork.push.application.api`：

- POST `prepare(history,connection,request_key,expected_history_version,expected_connection_revision,account_set_key?,catalog_request_id?,catalog_revision?,posting_kind?)`：服务端读取真实历史、目录和准备资料并冻结。支持 Express；MRERP明确返回尚未接通。未启用总推送开关返回 disabled；预检待人工返回 manual；通过返回 prepared，不创建OCR Work或外部队列。
- 同一请求key与原始参数重放同一记录和冻结内容。仍重新检查当前权限以及不可变PRN原记录权限；不能用重放绕过撤权。同key换内容拒绝；不同key同单同目标受权威账本防重。历史或配置变更必须重新准备，不能靠新context放行旧载荷。
- GET `detail(name)` / `records(connection,start=0,limit=50)`：要求 push_log + 当前日志角色、公司/买方与连接授予；输出名称、状态、版本、检查项和外部单号投影，不返回冻结目标/载荷、凭据或原始响应。停用连接可在有效日志授权下查询。
- POST `cancel_prepared(name,expected_revision)`：只允许原提交者撤销本人明确从未发送的 prepared；不删除证据，不解除未知结果或已领取锁。原授权失效/票据配置过期的从未发送预留如何安全替换，见[账本合同](push-native.md)。

上述API没有页面按钮或导航。原生演练使用合成数据和内部运输替身；实际 Windows EXE 的构建来源与新平台接线、外部创建/回读/重试、MRERP运输、旧ERP成功记录导入仍缺，不能将本地准备与合成ACK计作完整外推验收。回导原键现在能查本Site同公司/买方/连接/账套成功记录；第十五批导出的ERP显示列已接本站授权账本，旧平台数据尚未导入，不能声称旧业务记录已可重推。Windows 已现场只读确认有两个 TEST 配置及加密 token，未改现用配置、未做实际 ERP 写入。

## 第十六批：可选旧 Companion 运输入口

显式 WSGI 应用 `pearnly_cowork.companion.wsgi:application` 只适配以下旧 POST 路径；默认 `frappe.app.application` 和本仓基础设施未改。本包没有新增监听端口、前端入口或安装动作。

| 旧客户端路径 | 原生承接入口 | 输出 |
| --- | --- | --- |
| `/api/erp/agent/heartbeat` | `companion.heartbeat.report` | 原生成功 `message` 解包为顶层对象 |
| `/api/erp/agent/lease` | `companion.queue_api.lease` | 顶层 `ok/jobs/lease_seconds`，保留刷新指令 |
| `/api/erp/agent/ack` | `companion.queue_api.acknowledge` | 顶层原回执，保留幂等状态 |

仅精确POST映射；URL尾斜线、别名、其它方法不获机器能力。使用原生新连接/新凭据及现有推送总闸、当前公司/角色/授予/issuer IP、历史回执窄授权，body不决定actor。准备仍需既有人工确认，poll不激活prepared。旧token/owner/路径/状态均未导入。原生RPC仍返回 `message`。

适配器不重试请求，原生完成提交和清理后才解包响应。200但封套不合法返回私有502；原生非200错误保持原状态和内容，映射路径所有响应均无Set-Cookie、no-store。既有业务和异常状态约定不变。

验证：EXE内嵌业务代码对应固定 `33af0fd`；其 QueueClient 在真实 httpx WSGI 运输中通过心跳、prepared空轮询、领取、重复ACK和撤销后历史ACK。共4个真实Site用例、3个无Site边界用例；没有新schema、菜单或框架hook。Windows到新平台的实际接线和ERP建单/独立回读仍未执行，不能把合成ACK称作入账成功。
