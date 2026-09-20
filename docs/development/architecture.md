# 框架架构与模块边界

适用所有窗口、所有自有 App、旧功能迁移和未来新增功能。采用 Frappe 的扩展方式，不复制 ERPNext 的产品功能。根 `AGENTS.md` 是执行入口，本文件是设计与审查依据。

## 开发前必须确定

每个工作包在记录中写清：所属 App / 业务域、公开入口、数据归属、依赖方向、外部副作用与验证方法。小修可在提交说明中完成。没有明确归属时先沿调用链确认，不把代码随手加到根目录或“通用工具”中。

1. 自有能力放自有 App。`hooks.py`、`modules.txt`、`patches.txt`、`public/`、`templates/`、`www/`、`translations/` 保持框架约定；DocType 留在 `<app>/<module>/doctype/`。`config/` 是框架可用的配置包，空着不表示要放业务代码。
2. 业务实现按职责进域包；入口负责解析请求、验证权限、调用服务，不能再堆一套业务流程。既有根模块在检查基线中登记，不授权继续往其中堆新业务。
3. 纯契约、金额/字段校验不依赖 Frappe、HTTP、浏览器或上层流程。外部协议和重试归适配层；流程编排归所属业务域；界面和 LINE 只负责交互。不要为分层而复制同一套规则。
4. 跨域调用须有明确入口、数据契约和负责人（所在域）；不新增循环依赖，不用延迟导入掩盖新循环，不用 `sys.path`、`sys.modules` 或导入钩子伪装结构。既有循环在触及该链时单独设计解除，不能借搬目录改业务行为。
5. 数据结构、权限、安装和升级使用原生 DocType / hooks / fixtures / patches。业务校验在服务端；前端隐藏选项不能代替校验。原生账务、库存职责不得另外重造一套。
6. 对外 RPC、webhook、排队任务、持久化 dotted path 是兼容契约。移动前登记，必要时保留只有显式导出的薄入口；内部代码直接引用新域。删除兼容入口前必须有调用方升级、历史队列清空及回滚窗口结束的证据。
7. 迁移先核对旧项目实际线上版本，按本仓职责重新落位。禁止以旧仓文件布局或过期本地源码作为新平台架构。迁移前后线上版本复核按 [迁移基准](migration-baseline.md)。

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
| `records` | 推送历史的列表移除与审计 | `manage.delete_records`；只改展示标记，保留权威推送凭据 |
| `query` | 查询权限、记录/排行及结果卡片 | `query_flow`；只读路径不承担写入 |
| `menu`、`line` | 菜单、事件路由、公共卡片与消息输出 | `line.handlers.handle`，共享输出 `line.out` |
| `ocr` | OCR 调用与身份证归一化 | `ocr_service` / `ocr_id_card` |
| `operators` | 操作员、销售身份与绑定 | `roster`；保留管理员权限 |

这次是保持行为的模块归位，已把纯契约与运行依赖分开。既有 `client` 到 `booking`、`intake` 的业务协议协作，以及会话流程间的延迟导入仍存在；不能据此宣称已完成严格无环分层。下一次改动相关链时，要围绕真实职责调整并补对应回归，不能再整批搬回根目录。

## 本地控制面与云端开户

管理后台继续运行在本机。正式开户只面向 `pearnly_target_runtime` 中明确配置 `executor=production`、且存在对应 Target 记录的云端目标。SSH 隧道的回环地址不等于本地租户。

- 下拉框/API 只提供云端目标；新增或重开发号单、身份创建、开通计划和待办领取均受服务端约束。
- 缺配置、未知目标、本地目标拒绝；旧任务不得自动改指向默认云端。
- 历史目标/发号/部署记录保留可追溯，不因隐藏目标而删库。历史撤销流程属于独立授权的生命周期操作。
- 开发和隔离测试可以在本地建测试环境，但不得接入正式开户选择器，也不得借测试绕过生产鉴权、写者锁或计费边界。

## 自动执行与人工审查

运行 `python3 scripts/check-architecture.py`：检查全部 App 的根模块基线、Frappe 必备文件、DMS 薄兼容入口、内部旧路径引用、纯契约依赖和新增跨域依赖。已存在的依赖方向登记在 `architecture-policy.json`，新增方向必须说明契约并审查，不能自动扩充。增加领域、例外或改变基线必须同时说明责任边界与理由，不能只改基线让检查变绿。

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

Cowork第七批 `targets` 是无IO的目录/目标证据合同；`push.express` 是显式输入的进销项准备闭包，单向依赖 `contracts.push`、`targets.identity` 与 `push.receipts` 的纯解包；`push.lifecycle/retry/receipts` 定义状态、恢复判据和回执投影。两种置信规则分别服务OCR字段证据与费用过账门槛，不能互换。以上不依赖Frappe、HTTP、环境变量、数据库或工作队列；日期和授权目录由后续服务端注入，不反向依赖识别执行或页面。没有注册ERP写入消费者，不能用共享OCR Work装载外部订单。原生连接、目录收集、写者锁与持久账本尚待装配，本批纯 `ready` 不是能力交付或外部成功证明。见[运行证据与执行缺口](../work/cowork-backend/push-runtime-baseline.md)。

MariaDB 工作单是任务事实；RQ/延迟集合仅为可修复投递记录。各产品 bench 的原生队列保持隔离，容量使用同一个非淘汰 Redis DB6，按租户统一核算、按供应商项目/区域/模型调速。等待不占 OCR worker，网络结果未知不自动重做付费识别。此队列不得接入外部订单写入；DMS 写者锁与订单恢复仍由 booking/adapter 负责。

`infrastructure/production/capacity.py` 在正式开户或激活前按候选 Compose 的内存硬限额计入所有现有产品及宿主预留，拒绝超额，不以空闲 RSS 推断容量。上线预算与证据见 [共享 OCR 容量](../operations/shared-ocr-capacity.md)。
