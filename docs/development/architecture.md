# 框架架构与模块边界

适用所有窗口、所有自有 App、旧功能迁移和未来新增功能。采用 Frappe 的扩展方式，不复制 ERPNext 的产品功能。根 `AGENTS.md` 是执行入口，本文件是设计与审查依据。

## 开发前必须确定

每个工作包在记录中写清：所属 App / 业务域、公开入口、数据归属、依赖方向、外部副作用与验证方法。小修可在提交说明中完成。没有明确归属时先沿调用链确认，不把代码随手加到根目录或“通用工具”中。

1. 自有能力放自有 App。`hooks.py`、`modules.txt`、`patches.txt`、`public/`、`templates/`、`www/`、`translations/` 保持框架约定；DocType 留在 `<app>/<module>/doctype/`。`config/` 是框架可用的配置包，空着不表示要放业务代码。
2. **产品内容的声明归产品自己**（[ADR-009](../adr/0009-product-owned-declarations.md)）：菜单在产品的 `hooks.py`（`pearnly_navigation`）、可售卖功能目录在 `apps/<app>/<app>/features.json`、术语表在 `apps/<app>/<app>/i18n/messages.tsv`。控制面与共用层只汇总（读文件、读已装 App 的钩子），不保存产品内容的镜像。共用层保留的是运行底座与跨产品策略：OCR 引擎、浏览器层、外部连接层、身份与登录、订阅计费、原生外壳，以及首页功能、基础角色、产品顺序这类策略。
3. 业务实现按职责进域包；入口负责解析请求、验证权限、调用服务，不能再堆一套业务流程。既有根模块在检查基线中登记，不授权继续往其中堆新业务。
4. 纯契约、金额/字段校验不依赖 Frappe、HTTP、浏览器或上层流程。外部协议和重试归适配层；流程编排归所属业务域；界面和 LINE 只负责交互。不要为分层而复制同一套规则。
5. 跨域调用须有明确入口、数据契约和负责人（所在域）；不新增循环依赖，不用延迟导入掩盖新循环，不用 `sys.path`、`sys.modules` 或导入钩子伪装结构。既有循环在触及该链时单独设计解除，不能借搬目录改业务行为。
6. 数据结构、权限、安装和升级使用原生 DocType / hooks / fixtures / patches。业务校验在服务端；前端隐藏选项不能代替校验。原生账务、库存职责不得另外重造一套。
7. 对外 RPC、webhook、排队任务、持久化 dotted path 是兼容契约。移动前登记，必要时保留只有显式导出的薄入口；内部代码直接引用新域。删除兼容入口前必须有调用方升级、历史队列清空及回滚窗口结束的证据。
8. 迁移先核对旧项目实际线上版本，按本仓职责重新落位。禁止以旧仓文件布局或过期本地源码作为新平台架构。迁移前后线上版本复核按 [迁移基准](migration-baseline.md)。

## DMS 的实际职责

包根：`apps/pearnly_dms/pearnly_dms/`。73 个原实现的完整映射见 [机器可读对照](dms-module-map.json)。历史 P4 文档描述当时路径，查当前实现时通过此表定位。

| 域 | 负责什么 | 关键入口 / 约束 |
| --- | --- | --- |
| `contracts` | 数据类型、金额槽位、身份证/文本校验、错误类型 | 无框架和 IO 依赖，自动检查 |
| `client` | 外部 DMS 表单协议与业务客户端 | `gateway.DmsClient`；传输可替换以做离线验证 |
| `adapter` | 浏览器会话、权威只读会话、推送组装、主档刷新锁 | 保留账号互斥及写者限制 |
| `connections` | DMS 连接配置及验证 API | `api`；权限检查在服务端 |
| `booking` | 订车校验、载荷、幂等、提交、回读、账本 | `service.book_in_session`；一次写入后必须回读 |
| `booking/conversation` | 订车问答、卡片及确认流程 | `booking_flow`；不另造提交实现 |
| `intake` | 客户识别后录入、去重、创建/修改及会话流程 | `flow` / `service` / `id_card` |
| `masters` | 主档契约、快照、顾问、银行、员工等 | 钉住的 ID 不回落首行 |
| `editor` | 浏览器编辑、票据、快照及保存 | `browser_api`；保留票据与权限校验 |
| `approval` | 改写审批、状态锁和批准后执行 | `approval_store` / `approval_flow` |
| `records` | 推送台账本身（状态、租约、重试、收口）与推送历史的列表移除、审计 | `push_log`（`record` / `claim_for_push` / `status_of`）、`manage.delete_records`；只改展示标记，保留权威推送凭据 |
| `query` | 查询权限、记录/排行及结果卡片 | `query_flow`；只读路径不承担写入 |
| `menu`、`line` | 菜单、事件路由、公共卡片与消息输出 | `line.handlers.handle`，共享输出 `line.out` |
| `ocr` | OCR 调用与身份证归一化 | `ocr_service` / `ocr_id_card` |
| `operators` | 操作员、销售身份与绑定 | `roster`；保留管理员权限 |

这次是保持行为的模块归位，已把纯契约与运行依赖分开。既有 `client` 到 `booking`、`intake` 的业务协议协作，以及会话流程间的延迟导入仍存在；不能据此宣称已完成严格无环分层。下一次改动相关链时，要围绕真实职责调整并补对应回归，不能再整批搬回根目录。

### 推送台账归 DMS（2026-09-21）

`Pearnly Push Log` 原先在 `pearnly_core`（表与 `push_log.py` 服务层）。核对全部调用方后按 [ADR-009](../adr/0009-product-owned-declarations.md) 搬进 DMS：表与控制器进 DMS 的模块目录，服务层进 `records` 域（`records/push_log.py`）。判据是"谁在用"：只有 DMS 写和读它（身份证录入、订车、审批、推送记录页），共用层唯一的用处是"本机样例站点把本该发去 LINE 的消息记一条"。

那一步改成**共用层发钩子、产品记账**：`pearnly_core.line._mirror` 按 `pearnly_line_mirror` 派发，DMS 在 `hooks.py` 注册 `pearnly_dms.records.push_log.line_mirror`。共用层因此不再引用任何产品表，也没装 DMS 的站点仍按镜像语义"当作已发送"，只是不记账。表名与字段一字未改（`tabPearnly Push Log` 不变），所以既有数据在迁移后原样保留——这一点在隔离 DMS 站点上用"迁移前写入、迁移后回读"验证过。

## Cowork 的实际职责

包根：`apps/pearnly_cowork/pearnly_cowork/`。域依赖方向登记在 `architecture-policy.json` 的 `cowork_domain_dependencies`，自动检查拦截未登记的新方向；新增方向必须同时说明契约、归属与理由。

| 域 | 负责什么 | 关键入口 / 约束 |
| --- | --- | --- |
| `contracts` | 与框架无关的数据与呈现契约（推送方向、历史摘要、错误单行文案） | 无框架、IO、模型依赖；唯一的全域共享纯契约 |
| `access` | 作用域与共享权限判据 | 只给判据，不承载业务流程 |
| `masters` | 主档输入、工作区与主档契约 | `masters.api` / `push_inputs`；写入口在服务端 |
| `tabular` | 共享的内存文件读取叶子：表格/CSV/PDF 字节到值 | masters 的账套导入与 reconciliation 的银行/GL 共用；不依赖业务域、框架、网络或磁盘路径 |
| `ocr` | 抽取：引擎、表引擎、校验闸、执行批次与计费、反馈 | `engine.runner`（本切片只承接 `invoice`/`auto`）、`execution.service`；依赖 `contracts` 与 `pearnly_core` 的计量 |
| `reconciliation` | 银行与收入对账：解码、配对、原生落库、导出 | `bank_recon_service` / `income_recon_service`；收调用方已解码的内容，子进程不读文件、不调模型 |
| `roundtrip` | 往返单契约与解析 | `service` / `parser` |
| `push` | 推送装配、试算、幂等与写者锁 | `application.service` / `native`；外部写只在目标域 |
| `connections`、`targets` | 外部连接配置、刷新、快照与推送目标 | 连接校验在服务端；本地目标不参与开户 |
| `companion` | 伴生端采集、投递与心跳 | 只消费上述域的公开入口，不直连数据库表 |
| `history`、`files`、`stocktake` | 历史、源文件守卫、盘点 | 各自 `service` / `api` |
| `config`、`patches`、`pearnly_cowork/`（DocType 控制器与桌面页） | 框架区：模块清单、数据升级、DocType 编排 | 不进域依赖表；控制器是编排层，可按需调用各域 |

已登记的既有环（债务，本次不扩围）：`ocr ↔ roundtrip`、`files ↔ ocr`、`history ↔ ocr`（后两组各有一条是函数内延迟导入）。它们来自旧项目同一批抽取与往返流程的相互调用，搬迁时按原职责落位；下次改到这些链时，要按真实职责把方向拆开并补回归，不能用新的延迟导入扩大环。`ocr` 与 `reconciliation` 之间没有也不应有依赖：两侧只通过 `contracts` 共享纯契约，抽取产物由调用方以数据形式交出。

## 本地控制面与云端开户

管理后台继续运行在本机。正式开户只面向 `pearnly_target_runtime` 中明确配置 `executor=production`、且存在对应 Target 记录的云端目标。SSH 隧道的回环地址不等于本地租户。

云端产品的员工身份由 `control.account` 提供受限 RPC，复用私有 `billing.internal` 的运行载体和租户/产品 scoped token，但业务不进入计费域。`core.account.member_client` 仅负责该固定协议的 HTTP 调用，产品 `team` 域先校验员工管理权限。私有站点只额外开放该 POST 路径，不提供管理 Desk、通用 DocType API 或任意管理员操作；管理后台仍在本机。专用 Keycloak 客户端只具备用户管理权限，其密钥与归属签名密钥绝不下发租户。原生成员身份/操作记录持久化归属与幂等状态，已有全局邮箱/用户名不能被其它租户接管。详见 [本批记录](../work/2026-09-24-unified-release-onboarding.md)。

- 下拉框/API 只提供云端目标；新增或重开发号单、身份创建、开通计划和待办领取均受服务端约束。
- 缺配置、未知目标、本地目标拒绝；旧任务不得自动改指向默认云端。
- 历史目标/发号/部署记录保留可追溯，不因隐藏目标而删库。历史撤销流程属于独立授权的生命周期操作。
- 开发和隔离测试可以在本地建测试环境，但不得接入正式开户选择器，也不得借测试绕过生产鉴权、写者锁或计费边界。

## 自动执行与人工审查

运行 `python3 scripts/check-architecture.py`：检查全部 App 的根模块基线、Frappe 必备文件、DMS 薄兼容入口、内部旧路径引用、纯契约依赖和新增跨域依赖。DMS 与 Cowork 已存在的域依赖方向分别登记在 `architecture-policy.json` 的 `dms_domain_dependencies` 与 `cowork_domain_dependencies`，新增方向必须说明契约并审查，不能自动扩充。增加领域、例外或改变基线必须同时说明责任边界与理由，不能只改基线让检查变绿。

`python3 scripts/check-dms-offline.py` 只执行已审过的四组零 IO 检查，检查失败标记和退出码。完整业务 / Site / 真机验收另按影响选择。

本地运行 `sh scripts/install-dev-hooks.sh`，从长期主目录安装仓库级共享 `core.hooksPath`。提交钩子导出实际暂存快照后检查，其他窗口和工作树也使用同一钩子，不受它们是否重新读取规则影响。旧分支缺检查脚本时拒绝提交并提示同步规则；不覆盖已有钩子。该配置仅属于本仓，不改全局 Git 配置。新克隆需要安装一次；人工禁用钩子仍然属于违规，Git hook 不是权限隔离机制。

`Development checks` 对 push / PR 运行架构和离线契约检查；生产镜像构建也先检查架构，再在候选 Frappe 环境验证开户限制与旧入口。GitHub 是否强制禁止合并取决于仓库分支保护，未配置保护不能称为不可绕过的闸门。

根 `AGENTS.md` 会作为该仓库后续任务的开发指令。已经运行的窗口可能保留旧上下文；它们需要重新读取当前文件或同步主线。文档不能远程替换已运行任务的上下文，自动检查负责发现代码层面的违规。

参考：[Frappe App 结构](https://docs.frappe.io/framework/user/en/basics/apps)、[创建 App](https://docs.frappe.io/framework/user/en/tutorial/create-an-app)、[hooks](https://docs.frappe.io/framework/user/en/python-api/hooks)。业务域分组和云端开户限制是本项目的约定，不冒称为上游强制目录规范。

### 操作员连接同步（2026-09-20 LINE 修复）

`operators.endpoints` 负责花名册到本人 Endpoint 的事务内同步，通过原生 DocType 更新和共享连接 `on_update` hook 触发；不调用外部 DMS。公司配置与管理员读凭据继承到员工连接，员工销售密码和提成归属保持独立。新增 `operators → contracts` 仅复用纯常量 `PERSONAL_BOOKING_DEFAULT_KEYS`，防止继承老板的顾问身份；contracts 无框架、IO 或反向依赖，不产生循环。原生迁移补齐已有操作员连接，停用/删除保留 Endpoint 与 User 审计关联并撤销使用权。

`account` 域恢复旧线上 LINE 菜单 3/4：依赖 `editor.browser_ticket` 的独立 account scope 与 `ocr.resolve_dms_endpoint` 的本人连接判据；不反向依赖 account。凭据修改走 DMS Operator 原生保存及上述同步 hook。门户只消费 60 秒单次票据并提交给配置的 HTTPS DMS。LINE 绑定变更以 core hook 通知 menu；menu 读取 query 权限，异步同步已经发布的菜单，不在文档保存时创建或切换默认菜单。

## 通用界面框架

所有迁移与新功能遵循 ADR-005/007：产品 Web 与管理后台复用同一版本原生 Frappe Desk，页面/表单/列表/弹窗/提示/音效使用原生 API。品牌色仅用于强调，保留官方白底、深色正文与布局。业务 API、角色、购买功能和鉴权边界保持服务端约束，不授予客户平台超管。

只有 LINE 票据编辑器保留独立 Vue + Frappe UI，因为它使用一次性票据而非 Desk 登录。Keycloak 与邮件分别保留自己的原生认证主题、邮件兼容模板。此例外不得扩展成第二套产品 Web 壳。

### 操作员提成归属的读取边界（2026-09-20）

`operators.advisors` 通过 `masters.masters_cache.get_masters(force_refresh=True, require_complete=True)` 读取管理员端点的顾问名册，保存时按 ID 重新解析名称。此依赖只读，不向主档或外部 DMS 写入，不反向引用 roster；实际读取链 `masters_cache` → `intake.service.run_logged_in` → `adapter` → `client` 不调用 roster；其他业务流程的历史域间循环仍按既有债务记录。顾问业务校验归 operators，抓取、会话互斥和缓存仍归既有 masters/adapter。登记 operators → masters 是为恢复旧线上已具备的提成归属校验，不授权其他反向依赖或绕过刷新失败。

### 推送记录管理的边界（2026-09-20）

`records.access` 提供 DMS 推送历史的产品/角色/功能权限和当前 Site 内的记录范围，`query.records` 只读复用这些定义，因此登记 query → records 单向依赖。`records.manage` 用 POST 原子校验选中记录并记录移除人/时间；原生 Push Log 的业务字段、状态、重试和客户查重凭据保留。此依赖不指向 query、外部适配器或业务写入，不增加循环，也不授予通用 DocType 删除权限。

### 首页记录删除的边界（2026-09-20）

`core.payments.history` 提供选中项校验和原生记录展示删除标记；产品 `billing_www.delete_records` 必须先校验公司管理员。OCR 记录限定当前产品 Site；用量和充值记录经既有受鉴权的 `billing_client` 发送给 `control.payments.history`，由计费端固定 DocType 并按服务凭据对应租户逐项加锁校验。Control 单向依赖 Core 的内部记录标记方法；Core 不导入 Control。请求不能指定 DocType 或租户。

三种原生记录增加隐藏只读 `records_deleted_at/by`。产品列表和导出不再展示被删除项；钱包、阶梯页数、计费幂等、充值审核及原始凭据继续读取原行。删除不退款、不取消待审核充值、不授予通用 DocType 删除权限。首页仅复用现有原生列表的选择和确认行为，不另造表格或分页。

### 已成功订车的后续交付

`booking.delivery` 使用自有原生 `DMS Booking Delivery` 持久化已经独立回读确认的订单结果、原身份版本和稳定回执key。先保存后释放当前LINE草稿，后台只补附件及LINE回执，不调用建单入口。其权限仅System Manager只读，不新增产品导航；未核实订单仍保留原提交标记。`booking.attachments` 负责已选择消息的内容获取，`client.attachments` 负责原生完整表单上传与逐件回读，依赖方向不反转。

### 三产品共享识别执行

Core 的 `execution` 域负责持久工作单、容量准入、供应商并发/速率、输入与模型检查点、原生 RQ 投递及有界修复。产品通过 `pearnly_ocr_handlers` 和失败通知 hook 登记自己的幂等工作流；Core 不导入 DMS/ERP/Cowork 的业务模块。DMS 已接入；Cowork 现在登记 `cowork.invoice`，PDF/图片及普通表格/文本识别到原生草稿和稳定账单回执已在隔离Site以假外部出口验证，未部署、未做付费模型验收；ERP仍预留。不能据此宣称整产品 OCR 迁移或线上验收完成。

Cowork `ocr.execution`单向依赖engine/table_engine/inputs_runtime/table_inputs/providers、files/history/feedback与Core；history保存单向调用feedback，feedback通过本Site原生记录检查来源，不反向导入history流程。Batch固定actor/公司/原件/日期/策略/纠错快照，Core Work只存Batch引用。新controller与Work mixin阻断原生更新/分享绕开付费检查点，不给共享Work增加客户read权限。`ocr.engine`、schema、validation及纠错model保持纯计算。细节见[执行合同](../work/cowork-backend/invoice-execution.md)。 表格字节解析归table_inputs，纯L2编排复用engine；files持有不可变逻辑区段清单，history的逻辑索引不授予PDF预览。Batch分别固定结果单元数与字符计费单位，旧PDF/image任务字段向后兼容；完整性与通用模型边界见[表格执行](../work/cowork-backend/table-engine.md)。


Cowork第六批 `roundtrip` 域提供固定复核工作簿契约、字节解析、导出和旧记录权限边界；ocr.execution在入队前选择确定性回导，复用Core工作单与计费；roundtrip不反向依赖执行编排。roundtrip.contracts/results只复用OCR schema/pages纯值，intake复用有界table_inputs；files的manifest新增roundtrip_document单位，每个解析单据对应源sheet/行，不把工作表数当票数。导出只消费授权History，外部ERP事实尚未接入，不新增前端入口。纯规约共用同一份spec/money，不能在读写端各算一套。见[往返原生合同](../work/cowork-backend/roundtrip-native.md)。

Cowork第七批 `targets` 是无IO的目录/目标证据合同；`push.express` 是显式输入的进销项准备闭包，单向依赖 `contracts.push`、`targets.identity` 与 `push.receipts` 的纯解包；`push.lifecycle/retry/receipts` 定义状态、恢复判据和回执投影。两种置信规则分别服务OCR字段证据与费用过账门槛，不能互换。以上不依赖Frappe、HTTP、环境变量、数据库或工作队列；日期和授权目录由后续服务端注入，不反向依赖识别执行或页面。没有注册ERP写入消费者，不能用共享OCR Work装载外部订单。第八批已装配原生连接、推送准备和持久账本；真实目录收集及外部消费者仍未接通，纯 `ready` 不是外部成功证明。见[运行证据与执行缺口](../work/cowork-backend/push-runtime-baseline.md)。

MariaDB 工作单是任务事实；RQ/延迟集合仅为可修复投递记录。各产品 bench 的原生队列保持隔离，容量使用同一个非淘汰 Redis DB6，按租户统一核算、按供应商项目/区域/模型调速。等待不占 OCR worker，网络结果未知不自动重做付费识别。此队列不得接入外部订单写入；DMS 写者锁与订单恢复仍由 booking/adapter 负责。

`infrastructure/production/capacity.py` 在正式开户或激活前按候选 Compose 的内存硬限额计入所有现有产品及宿主预留，拒绝超额，不以空闲 RSS 推断容量。上线预算与证据见 [共享 OCR 容量](../operations/shared-ocr-capacity.md)。

### Cowork 连接与推送账本（2026-09-21）

`connections` 域持有原生 Connection/Snapshot/Refresh、显式连接授予、当前目录与可信准备资料；依赖 `targets` 纯合同和 Core 权益，不反向引用推送。Connection 是配置、刷新、快照安装和预留共用的事务锁；目录、用户、角色与公司范围使用当前读取。凭据只进原生 Password，专用查询不返回原文。没有安装真实采集器，公开请求不能上传权威快照。

`push.application` 是专用服务端装配入口：先连接、后票据加锁，重验当前权限和版本，解析不可变 Source/Batch 的回导原键，读取本地成功账本，再调用纯 Express 准备并保存冻结结果。公司税号来自 Workspace，客户端不能替换公司或凭证来源。只返回准备状态、诊断与回执摘要，不返回运输载荷或凭据。RPC 只在无外部IO的本地事务发生数据库冲突时有界重试；内部组合调用必须直接使用 service。

`push.native` 单向依赖 connections、history/access 和纯 targets/lifecycle/receipts；以三个原生 DocType 持久化 Record、Attempt、Writer Lock。记录及载荷不可变、当前版本放行、端点与公司/账套互斥、随机尝试令牌摘要及递增序号共同防重；旧派发结果未知时保留写锁，不自动重领。过时准备仅在从未派发的证据完整时可撤销替换；真实旧尝试的迟到回执仍可记录。所有框架 CRUD、分享入口拒绝绕过专用域服务。

截至第八批没有公开 activate/lease/ACK、队列消费者、scheduler、Companion或MRERP运输入口；第十至十二批的后续入口见下节。内部 AdapterContext 只是服务端装配约束，不是客户机身份认证，不能宣称跨Site/物理设备写者锁已验收。新Record的 prepared 只代表本地准备，不是排队、发送或入账成功。未添加菜单、页面或其它产品能力入口；依赖及剩余外推边界见[原生连接](../work/cowork-backend/connections-native.md)和[原生推送](../work/cowork-backend/push-native.md)。

### Cowork 凭据与盘点导出（2026-09-21）

`companion` 单向依赖 `connections.access` 的当前授权；contracts 保持纯计算。原生 Express Agent Credential 每次发行一行，摘要与末四位代替明文，Connection→Credential 顺序锁保护轮换、撤销、请求回执和代际唯一性。机器上下文必须来自内部认证的对象能力，写前重新核对当前身份与授权；截至第九批只有人工管理RPC及内部认证，没有auth_hooks、心跳、目录采集或运输入口，不创建平台User。推送总闸同样限制发行和派发认证，关闭后仍可管理撤销。旧客户端实际运行文件来源与全量配置差异尚未闭合，不能据本地凭据完成而切换真实旧客户端或迁入旧业务状态。

`stocktake.export` 只消费同域service.report提供的已授权全量锁定快照，依赖既有Decimal数量与比较合同，输出固定泰语复核工作簿协议；不读取ERP目录或文件，不重新实现权限。专用GET下载沿原生私有响应和Access Log，照片和legacy模式尚未承接。两项均不增加菜单/页面。详见[身份合同](../work/cowork-backend/companion-identity-contract.md)和[盘点导出](../work/cowork-backend/stocktake-export.md)。

### Cowork 机器心跳及回执接口（第十至十二批）

`companion.http_auth` 只接受三个精确 POST RPC：`heartbeat.report`、`queue_api.lease`、`queue_api.acknowledge`。每个请求绑定独立操作类型和请求对象，机器身份不能继承到通用 RPC、资源 CRUD、别名或浏览器会话；保留原生 issuer IP 限制。新原生 Connection / 新凭据的报告单向调用 `connections.collector`，目录刷新不自动授予推送准备资料。

`companion.transport` 单向调用 `push.native` 账本，不包含 HTTP 或提交。私有 Delivery 把线协议 UUID 绑定到本 Site/Connection/历史 Credential/Attempt/fence；原始 attempt secret 使用原生 Password 密文，不编码进句柄。`receipt_auth` 的窄能力可在停闸、撤权或轮换后接收原历史任务结果，不能领取新任务或写报告，也不跳过原 issuer 的 IP 限制；它与当前派发能力互不通用。

`queue_api` 为薄事务入口，显式提交成功才返回原生 Frappe `message` 封套；只对提交前本地死锁有限重试并重验权限，提交或回滚失败不重跑任务。错误堆栈、Recorder 和原生消息列表不得带出 Bearer 或 attempt secret。没有后台外部写者、真实 Windows 接线或 ERP 调用，prepared 不由 poll 激活；人工激活、准备事实、History/库存/通知副作用仍待独立承接。实际旧客户端路径及顶层 JSON 协议需要适配，不能据这些本地接口称为直接兼容。见[HTTP 边界](../work/cowork-backend/companion-queue-http-boundary.md)和[第十二来源审查](../work/cowork-backend/twelfth-source-review.md)。

### Cowork 人工确认与 ACK 本地联动（第十三至十四批）

第十三批 `connections.preparation` 从原生数据域采集准备资料并逐类绑定机器缓存来源，`companion.submission` 以原actor/current权限及凭据实现人工确认；poll不自动激活。第十四批 `push.native.completion` 在同一ACK事务中单向调用 `history.push_state` 和 `connections.stock_effects`，没有反向依赖。History是原账本的窄状态投影，不改变人工审核版本；库存增量只影响匹配账号/root/原报告凭据的画像。

唯一 `Cowork Push Notification` 为push域私有原生事件，继承通用CRUD/分享拒绝，仅LINE来源首次成功保存held素材；无发送消费者或接收人权限。失败回滚账本、投影、计数、事件和锁释放，重复ACK不重放副作用。复核表仅通过既有History范围读取服务器状态；单号和主档动作事实待后续接入。详见[第十四批合同](../work/cowork-backend/fourteenth-source-review.md)。以上替代前述旧批次中相应待办，不代表真实Windows接线、完整通知功能或独立ERP回读已验收。

### Cowork 复核表的回执事实（第十五批）

`roundtrip.service` 单向调用 `push.export_facts`，后者只读取本站原生 Push Record 并通过 `connections.service.read_for_log` 检查当前日志功能、角色、公司/客户和连接授予；按原 actor / History 选最新成功。History 的最新推送状态与最后成功回执分开，不用失败回执覆盖原成功单号。`push.export_actions` 为纯解析，复用既有库存科目组合同，不依赖 Frappe 或 IO；没有新的反向依赖。

追加的库存组显示名称由 connections 域按回执的账号/root/报告凭据来源提供，不读取密码。票据版本变化后保留 ERP 引用，但不把旧行新建标记、方向和主档清单套入新票面。通用账本 CRUD/分享限制保持；无新 DocType、API、菜单或外部出口。详见[第十五批合同](../work/cowork-backend/fifteenth-source-review.md)。

### Cowork 可选旧客户端运输入口（第十六批）

`companion.wsgi:application` 显式包装原生 `frappe.app.application`；同域 `legacy_wire` 只把三个精确旧 POST URL 映射到既有心跳/lease/ACK RPC，并将成功的 `message` 字典解包为旧客户端顶层 JSON。其它请求原样交给原生应用；不注册新的 hook、角色或身份，也不复制业务校验/事务/重试。原生应用完成提交和清理后才输出适配响应，异常成功封套返回稳定502，不当空队列或成功回执处理。

默认基础设施仍使用原生入口，本包没有启用监听或切换 Windows。固定旧 QueueClient 经真实 httpx WSGI 运输在隔离 Site 验证，Windows EXE 的54个业务模块及 launcher 与固定源码编译结果一致；实际机器连新平台、物理写者互斥和独立ERP回读未验收。详见[第十六批合同](../work/cowork-backend/sixteenth-source-review.md)。
