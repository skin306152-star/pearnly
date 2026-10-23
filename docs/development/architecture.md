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
| `snapshots` | 本地完整读代际、私有分批进度、凭据/员工范围与显式手动派发 | 只依赖纯契约/原生框架，不登录、不依赖业务消费者；见 ADR-010/011 |
| `editor` | 浏览器编辑、票据、快照及保存 | `browser_api`；保留票据与权限校验 |
| `records` | 推送台账本身（状态、租约、重试、收口）与推送历史的列表移除、审计 | `push_log`（`record` / `claim_for_push` / `status_of`）、`manage.delete_records`；只改展示标记，保留权威推送凭据 |
| `query` | 查询权限、记录/排行及结果卡片 | `query_flow`；只读路径不承担写入 |
| `menu`、`line` | 菜单、事件路由、公共卡片与消息输出 | `line.handlers.handle`，共享输出 `line.out` |
| `ocr` | OCR 调用与身份证归一化 | `ocr_service` / `ocr_id_card` |
| `operators` | 操作员、销售身份与绑定 | `roster`；保留管理员权限 |

这次是保持行为的模块归位，已把纯契约与运行依赖分开。既有 `client` 到 `booking`、`intake` 的业务协议协作，以及会话流程间的延迟导入仍存在；不能据此宣称已完成严格无环分层。下一次改动相关链时，要围绕真实职责调整并补对应回归，不能再整批搬回根目录。

### DMS 后台读快照（2026-09-24）

读取架构见 [ADR-010](../adr/0010-dms-background-read-snapshots.md)、替代触发策略的 [ADR-011](../adr/0011-dms-manual-read-snapshots.md) 与会话复用/白天增量/账号池的 [ADR-014](../adr/0014-dms-session-reuse-live-sync.md)：手动建立完整快照，交互只读本地，客户写入和最终订车实时复核。采集器在 DMS hooks 声明，手动请求域通过框架钩子派发；不反向导入业务。contracts 的 html/unicodedata 仅纯字符串正规化，无 IO；不扩大违规基线。

手动请求直接进入原生 `dms_sync` 队列，不再注册 Scheduled Job Type 的队列 mixin，也不修改 scheduler tick。旧读档入口无请求 token 时无副作用返回；业务恢复任务继续使用原生调度和既有队列，不修改 Frappe 源码。

### 推送台账归 DMS（2026-09-21）

`Pearnly Push Log` 原先在 `pearnly_core`（表与 `push_log.py` 服务层）。核对全部调用方后按 [ADR-009](../adr/0009-product-owned-declarations.md) 搬进 DMS：表与控制器进 DMS 的模块目录，服务层进 `records` 域（`records/push_log.py`）。判据是"谁在用"：只有 DMS 写和读它（身份证录入、订车、推送记录页；审批 2026-10-05 已删），共用层唯一的用处是"本机样例站点把本该发去 LINE 的消息记一条"。

那一步改成**共用层发钩子、产品记账**：`pearnly_core.line._mirror` 按 `pearnly_line_mirror` 派发，DMS 在 `hooks.py` 注册 `pearnly_dms.records.push_log.line_mirror`。共用层因此不再引用任何产品表，也没装 DMS 的站点仍按镜像语义"当作已发送"，只是不记账。表名与字段一字未改（`tabPearnly Push Log` 不变），所以既有数据在迁移后原样保留——这一点在隔离 DMS 站点上用"迁移前写入、迁移后回读"验证过。

## Cowork 的实际职责

包根：`apps/pearnly_cowork/pearnly_cowork/`。域依赖方向登记在 `architecture-policy.json` 的 `cowork_domain_dependencies`，自动检查拦截未登记的新方向；新增方向必须同时说明契约、归属与理由。

| 域 | 负责什么 | 关键入口 / 约束 |
| --- | --- | --- |
| `contracts` | 与框架无关的数据与呈现契约（错误单行文案、单据口径、查重等；推送方向、票面页面、明细行、POS 日汇总 2026-10-23 起在共用层 `pearnly_core.express_rules.contracts`，ADR-025） | 无框架、IO、模型依赖；Cowork 内部的全域共享纯契约 |
| `access` | 作用域与共享权限判据 | 只给判据，不承载业务流程 |
| `masters` | 主档输入、工作区与主档契约 | `masters.api` / `push_inputs`；写入口在服务端 |
| `tabular` | 共享的内存文件读取叶子：表格/CSV/PDF 字节到值 | masters 的账套导入与 reconciliation 的银行/GL 共用；不依赖业务域、框架、网络或磁盘路径 |
| `transaction`（已搬到共用层 `pearnly_core.transaction`，ADR-025） | 共享的本地事务冲突重来叶子（10-20 从 `push.application.conflicts` 搬出）：1020 / 1213 整笔回滚、退避后重来，一共 4 次 | `conflicts.retried`；只依赖 Frappe 的数据库，不依赖任何业务域；用尽抛不带数据库原文的 `Exhausted`，各入口按自己原有的失败形状接住；只在调用方自己的事务边界（白名单入口、机器方法、按步提交的编排）上用 |
| `ocr` | 抽取：引擎、表引擎、校验闸、执行批次与计费、反馈 | `engine.runner`（本切片只承接 `invoice`/`auto`）、`execution.service`；依赖 `contracts` 与 `pearnly_core` 的计量 |
| `reconciliation` | 银行与收入对账：解码、配对、原生落库、导出 | `bank_recon_service` / `income_recon_service`；收调用方已解码的内容，子进程不读文件、不调模型；收入对账里票面号与 Express 单号不同的已推单据（票号超出单号宽度，CC 桌面版自编号）经钩子 `cowork_recon_known_numbers`（`push.application.service.known_numbers`，只读推送记录）取对应关系，不导入 push |
| `roundtrip` | 往返单契约与解析 | `service` / `parser` |
| `tax` | 报税草稿（2026-10-13）：ภ.พ.30 与进项可抵扣核查、ภ.ง.ด.3 / ภ.ง.ด.53 | `service` 是唯一入口（CC 工具 `cowork_vat_draft` / `cowork_wht_draft` / `cowork_wht_answer` / `cowork_tax_draft_delete`，没有 Desk 页面）；纯计算在 `periods` / `pp30` / `input_vat` / `wht`（不连库、不读文件）；ภ.พ.30 只读 Express 回读（`readback.service.vat_records`，函数内延迟导入），预扣税只读识别记录与 50 ทวิ 参考记录（`history`）；草稿表 `Cowork Tax Draft` 只经 `service` 写（对账同款写入闸，只存各栏、合计与问题计数，不存行级明细；写前只锁这份草稿、不锁工作空间行），所得类型记忆 `Cowork WHT Choice` 只经 `memory` 写；确定性、零费用，不写 Express、不代为申报；功能码 `tax_drafts`、角色 Cowork Tax Filing |
| `push` | 推送装配、试算、幂等与写者锁 | `application.service` / `native`；外部写只在目标域。写者锁口径随记录冻结（ADR-021 D6）：Express 新记录 `lock_scheme: 2` 只取账套锁（规范根 + 规范键）与按内容键的公司锁，不再锁整条连接（`native.contracts.writer_identities`）；准备证据 v2 不绑电脑，领取时只核本单载荷用到的映射、画像与主档行（纯函数 `express_rules.push.referenced.used_facts`，共用层），下发副本的位置换成领取电脑的写法（纯函数 `express_rules.push.wire_location`，共用层）；自动推送（数字会计员第 3 期）：纯规则 `push.express.automation`（每张单 A / B / C）与 `push.automation.rules`（档位允许表、边界、提议、领单指纹），判定 `application.assess`（只看不记），网页上传 / LINE 的扫描 `push.automation.sweep`（每 5 分钟），公司档位、按日期选账套、核不上即降经钩子 `cowork_push_company_tier` / `cowork_push_day_books` / `cowork_push_verify_mismatch`，定时任务的票经 `cowork_push_task_histories` 排除；账套记忆被人改值的记录（10-18 E1 的 R1）是私有表 `Cowork Express Choice Change`，只经 `push.choices.record` / `forget` 写、`overrides` 读，留 90 天、写时顺手删旧的；push 不导入 companies / folder_intake / readback |
| `connections`、`targets` | 外部连接配置、刷新、快照与推送目标；Express 连接是多台电脑的池（ADR-021）：账套登记、可达与账套资料（`pool`），身份认定纯函数在 `targets.identity` | 连接校验在服务端；本地目标不参与开户；三张池表 `Cowork Express Account Set` / `Reach` / `Account Facts` 只经 `pool` 写，电脑编号由 companion 当不透明字符串传入（`member.Member`），connections 不依赖 companion；要各台电脑在不在线、版本时经钩子 `cowork_pool_presence` 取（`pool.presence`）。每种 ERP 的主连接（最早建、启用、不挂客户）只由 `service.main_connections` 解析，`records` 标 `main`，电脑登记、Desk、LINE、CC（含定时任务页）都按它认；池里「能写的电脑」只由 `pool.writers` 判（确认的 `has_writer` 与 `companion.pool.state` 共用）；连接的使用名单只由 `access.listed` 判（Express 池 `all_members` 全所可用，D2），用连接、连接列表、看推送记录共用；设置里的「全所可用」开关与名单只经 `service.set_members` 改（只限管理员、只改这两项、不升 `record_revision`，ADR-021 第 6 批），名单候选只经 `service.member_choices`（`access.assignees`，与 `require_assignee` 同一组角色） |
| `companion` | 伴生端采集、投递与心跳（服务端这一侧；CC 桌面版（Windows 端）本体在仓库根的 `companion/`）；按需请求通道（2026-10）：服务端请电脑读账套、列 / 取已授权文件夹里的文件、读账套近 12 个月「供应商 → 常用科目」 | 只消费上述域的公开入口，不直连数据库表；机器接口地址与 CC 桌面版的约定见 `tests/test_companion_contract.py`。按需请求：私有表 `Cowork Companion Request` 只经 `request_service` 写（`find` / `create` / `status` / `result` / `drop` / `presence` / `waiting` 给发起它的业务域，`lease_next` / `complete` 给两条机器方法，登记在 `http_auth.REQUEST_METHODS`）；纯规则与加一种请求的做法在 `request_contracts`；结果字节存站点私有目录（`request_store`），取走即删；结果一般只给发起人，`SHARED_KINDS`（科目建议的历史）属于连接与账套，此刻能用这条连接的人都能取；与推送队列、写者锁、看门狗互不相干；多台电脑（ADR-021 第 4 批）：读文件夹的定向给持有根目录的电脑（`target_device`），读账套的带规范键（`account_key`）交给够得到它的电脑（回读只给发起人自己登录过 CC 的电脑，科目历史谁先来谁读；ADR-021 第 54 条），通用位置键（`LOCATION_KEYS`）换成领取那台的写法（`localize`），在场按电脑记。多台电脑（ADR-021）：私有表 `Cowork CC Desktop Device` 只经 `devices` / `device_documents` 写，一台电脑是连接（池）里的一个成员，密钥按电脑（`credentials`，`active_identity` 两种公式），按 `token_digest` 认证；`register` v2 登记电脑，`device_api` 的 `hello` / `leave` 登记在 `http_auth.DEVICE_METHODS`；报文纯规则在 `device_contracts`。写账分派（第 3 批；10-16「谁的活谁的电脑做」，ADR-021 第 50–55 条）：纯规则在共用层 `pearnly_core.cc_desktop.wire.route_contracts`（归属 `owns`——定时任务冻结的读文件夹电脑 `route_device`、其余是录入人登录过 CC 的电脑 `signed_in_users`，没有登录记录的电脑过渡期谁的都写，判据在共用层 `cc_desktop.wire.route_contracts.serves` / `connections.pool.owned`；按账套黏住、载荷版本；没有档位与换手），`transport.lease` 按它领取、每台同时一件，各份账套的排头只由 `queue_heads` 取、只在这台能写的单里取（分派与 `pool.state` 的「需更新」共用一个查询）；「Express 现在能不能选 / 能不能写」只问 `pool.state` / `pool.for_user`，按当前用户自己的电脑算（`api.online` 形状兼容；第 6 批加 `computers` 分清「没有能写的电脑」与「CC 桌面版未连接」），确认的 `push.native.service.has_writer` 按录入人 / 读文件夹的电脑同一判据。第 6 批：设置「电脑」列表的移除只经 `credentials.remove_device`（登记人或管理员；先按名字顺序锁它有密钥或可达行的各条连接，再锁密钥，最后电脑行），被移除的电脑 `register` 只回 `removed`，人点「连接」带 `rejoin` 才作为新电脑回来；推送记录由哪台电脑写入，push 经钩子 `cowork_device_labels`（`devices.labels`）取电脑名，不导入本域 |
| `history`、`files`、`stocktake` | 历史、源文件守卫、盘点 | 各自 `service` / `api`；租户把预扣税凭证设成「不录入」时，识别存档同一事务里由 `history.certificates.keep_skipped` 存 50 ทวิ 参考记录（`Cowork WHT Certificate`，只给报税草稿读：不成票、不进录入待办、不推送、不改计费，用户 2026-10-13） |
| `readback` | Express 回读（2026-10 L1）：用户在 CC 对话里要某本账套某个月的账，经按需请求只读读回，出总账、销项 / 进项税报（Express 报表版式，对账解析器原样接受）与「推送以后改过哪些」的差异表 | 只经 `companion.request_service` 请电脑读、经 `push.application.service.pushed_express` 取推送记录、经 `connections` 认账套；纯规则（校验、期初、总账、税报行、税务草稿记录）在 `contracts`，比对在 `diff`，泰文版式在 `layout`；私有表 `Cowork Express Readback` 只经 `service` 写，文件在站点私有目录（`storage`）；同一人同一连接同一账套同一月只留最新一份，发起人可删；`service.vat_records` 给税务草稿；科目建议 B（2026-10-15）：`history` 经按需请求 `account_history` 请电脑读一本账套近 12 个月进项单据借方行的「供应商 → 常用科目」次数（纯规则在 `history_rules`），按连接 + 账套存站点私有目录（`private/cowork_account_history/`），一天内不重读、电脑离线或 CC 桌面版太旧时安静地没有；推送服务经钩子 `cowork_push_account_history` 取（push 不导入 readback，同 `cowork_push_fixed_choices`），要人选费用科目时与会计员在对话里说的类别、科目名称与大类一起由纯函数 `push.express.account_suggest` 排出最多 2 个建议（`suggested`），仍要人确认；不调模型、不扣费、不写 Express；处理完一份回读在同一事务里叫钩子 `cowork_readback_ready`（公司自动推送档位的评估，出错只回滚它自己的保存点），`month_changes` 经 `cowork_company_month_changes` 给档位证据（每条推送记录在最新完成回读里的变化） |
| `folder_intake` | 文件夹自动录入（2026-10-07）：规则、CC 桌面版文件夹接口、后台推进、每张发票的结果表格 | 只调用 `files` / `ocr.execution` / `pos_import.service` / `push.application` / `roundtrip.export` 的公开入口，不改它们；推往 Express 的销售任务另收 POS 导出（Excel / CSV，同步下发 `extensions`），不交识别、按天建票，结果表格每份导出一张（每天一行）；三张私有表只经本域写；所有 Cowork 租户都有，不设站点开关（用户 2026-10-07）；2026-10-12 起新任务处理文件夹里已有的文件（`include_existing`，同步下发 `existing`，老任务照旧拍底）；任务、运行、文件进度变了经 `live` 在提交后推 `cowork_folder_changed`；提醒（负责人选的时刻，最多 3 个，默认前一天 08:00 / 14:00）与完成简报（`reminders` / `briefs`，每 5 分钟由 `schedule.notices` 触发）只算何时发、发给谁、写什么，统一经 `notify` 调用共用层 `pearnly_core.assistant.notices`，不调模型、不扣费；同一连接同一账套的存货销售等存货进货推送有了结论再准备（最多 12 小时）；`fetch`（2026-10 L2）经 `companion.request_service` 请读文件夹的电脑列文件、交回一份文件（只读、不识别、不扣费），判据同定时任务表单，类型与大小同对话附件；公司主档的单据文件夹（ADR-020）：`company_folders` 按「公司 × 电脑」各一行（`Cowork Company Folder`，`reader_device` 按根目录认出的电脑），状态读时现算，公司编号在本域只是不透明串、不导入 `companies`；一台电脑最多 200 个根目录、文件夹名合计 2 万个（`MAX_ROOTS` / `FOLDERS_TOTAL`，与 CC 桌面版由契约测试比对），快速同步 `roots_only`，回复带 `max_roots` 与 `roots_in_use`；`schedule.notices` 另叫 hooks `cowork_scheduled_notices` 声明的入口（「待处理」早上汇总）；读者 =（电脑，成员连接）（ADR-021 第 4 批）只由 `readers` 解析（对外编号「电脑|连接」，只给连接的旧写法按根目录认电脑，电脑改挂经 hooks `cowork_reader_device_moved` 转移），任务、文件、运行、公司文件夹记 `reader_device`，同步只下发这台电脑这个成员的任务，每台最多 50 个，提醒与简报按电脑分，推送限流按推送连接 + 账套；跟随公司当年账套（T1）：任务记公司主档与分店（`company` / `company_branch`，保存时由服务端反查、补丁 `v0_0_23` 给旧任务回填），`follow` 按单据日期经 hooks 取这家公司覆盖那天的账套，取不到 / 多于一份 / 没有电脑够得到（`push.native.service.has_writer`）就停下等人（原因码 `contracts.ACCOUNT_SET_STOPS`），选到的经 `history.push_state.retarget` 抄到票上；没记公司或主档已删照旧用固定账套；这类任务的限流与先后按（推送连接，公司 + 分店）（`pump._lane`） |
| `pos_import` | POS 导出按天录销售（2026-10-13，交接 L9）：读收银机导出的 Excel / CSV，按「公司 + 表头指纹」记住认好的列（`Cowork POS Layout`，问一次），逐日汇总成现金客户的简易税票，一天一张票 | 纯规则在 `contracts`（认列、Decimal 逐日对平、无税额列按 7/107 四舍五入、尾差落税前额）与共用纯契约 `contracts/pos.py`（付款方式事实、单号区间）；`service` 只经 `files` 建原件与逻辑分段清单（`pos_day`）、`history.recognition.persist_batch` 建票，不识别、不调模型、不扣费；推送走现有推送链（载荷 3：HS 借现金，每种非现金付款方式同一次写入一张普通日记账凭证「借 收款科目 / 贷 现金」，收款科目是推送选择 `receipt`）；只推 Express；`folder_intake` 与 `assistant` 调它，它不反向依赖 |
| `assistant` | 智能体工具（ADR-019）：CC 桌面版状态与账套、单据推送、文件夹任务、推送结果、从已授权文件夹列 / 取文件、回读 Express 账套、报税草稿、POS 导出按天录销售、公司主档（ADR-020，`company_tools`：列出公司、读公司资料、经「允许」记 / 删知识库） | 只声明工具（`pearnly_assistant_tools`）与自己的说话规矩（`pearnly_assistant_guidance`，如账套按公司名说）并调用 `connections` / `push.application` / `folder_intake` / `history` / `pos_import` / `readback` / `tax` 的公开入口，以当前用户身份执行，不另写表、不绕过权限；推送原因交给模型前经 `push.export_facts.reason_text` 按用户语言翻成一句话（同网页与结果表格，筛选仍按原值）；点了某份账套的结果在键前带上账套目录里给人看的名字 `account_label`（同录入下拉）；从文件夹取来的文件以 `origin="folder"` 放进对话（识别工具只拒收 `origin="tool"` 的工具产出）；回读出的四份工作簿以 `origin="tool"` 放进对话，同一段对话同一份回读只放一次 |
| `companies` | 公司主档（ADR-020，2026-10-15）：全所一家一份的客户公司、身份与分店、知识库、记住的选择汇总、按电脑的单据文件夹与月份子文件夹、给 CC 的公司说明、待处理与早上汇总 | 只能从账套目录建，身份由服务端算（`identity`：有效税号 → `tax:` 不带分店；Express 无税号 → 连接 + 名称；MR.ERP → 连接 + 代码）；账套、分店、任务、草稿、选择都按各自现有的键现算，不迁数据、不加外键；目录经 `connections.service.account_set_facts`（只给本域，`targets()` 不放宽）；选择的读删改只经 `push.choices` / `pos_import.layouts` / `reconciliation.preset_store` 的公开入口（MR.ERP 账套记住的导入菜单与默认值存在连接上，经 `connections.service.mrerp_remembered` 读、`choose_mrerp_menu` / `choose_mrerp_default` 改，2026-10-19）；Express 历史学到的科目经 hooks `cowork_company_history_choices` / `cowork_company_history_forget` 读删（科目建议那一包提供，没装就空）；经 hooks `pearnly_assistant_projects` 把 `provider` 交给共用层；两张私有表只经本域写；`folder_intake` 不反向依赖它；定时任务跟随公司当年账套（T1，2026-10-15）：`tasks` 经 hooks `cowork_task_company`（保存任务时所选账套属于唯一哪家主档、哪个分店）/ `cowork_task_books`（这家公司在任务连接上覆盖某天的账套，纯规则 `identity.for_day` / `for_branch`）给 `folder_intake.follow`，按当前用户能用的连接现算、每次先清请求内缓存（后台推进换着负责人推）；自动推送档位（第 3 期）：私有表 `Cowork Company Automation` 只经 `automation` 写（没有行即 L0、无开关、无手动升降），证据与升降纯规则在 `automation_rules`（阈值常量表 `THRESHOLDS`），只在回读处理完（readback 经 `cowork_readback_ready` 叫）与写入核不上两条写路径上评估；回读变化经 `cowork_company_month_changes` 从 readback 取 |
| `work` | 任务管理：多块看板（成员、可见范围、看板权限）、卡片、标签、评论与活动、附件（原生私有 File） | `contracts`（含看板权限矩阵）/ `access`（判据）/ `boards` / `service` / `api`；只依赖 `access`；原生资源只读且按看板过滤，写入走 `documents` 写入闸 |
| `config`、`patches`、`pearnly_cowork/`（DocType 控制器与桌面页） | 框架区：模块清单、数据升级、DocType 编排 | 不进域依赖表；控制器是编排层，可按需调用各域 |

已登记的既有环（债务，本次不扩围）：`ocr ↔ roundtrip`、`files ↔ ocr`、`history ↔ ocr`（后两组各有一条是函数内延迟导入）。它们来自旧项目同一批抽取与往返流程的相互调用，搬迁时按原职责落位；下次改到这些链时，要按真实职责把方向拆开并补回归，不能用新的延迟导入扩大环。`ocr` 与 `reconciliation` 之间没有也不应有依赖：两侧只通过 `contracts` 共享纯契约，抽取产物由调用方以数据形式交出。

### ERP 当前台、Express 当账本（2026-10-22；10-23 起 CC 桌面版直连 ERP，ADR-025）

Pearnly ERP 的 `express` 域：采购 / 销售单在唯一提交点（`sales.entry.post`、`purchasing.entry.post`，及旧的 `purchasing.service.set_status`）
提交前经 `express.push.queue` 标「排队中」（本公司的账套有 CC 桌面版报过才算接了 Express，由数据定、不是开关），事务提交后在后台
`push.send`：`target.pick` 按税号 + 分店 + 单据日期在共用通道记的账套里选（同期两份按公司按年度问一次），`prepare` 用共用的 Express
写账规则（`pearnly_core.express_rules`，与 Cowork 推送同一份）和电脑读回的账套资料（`facts`）备好载荷，交进共用机器通道的写账
队列（录入人 = 开单的人）。要人拿主意的项停在单据上，在 ERP 里选一次（`api.answer`，按公司记在 `ERP Express Choice`，`choices`）；
回执由共用通道在同一事务里交回 `push.job_changed`（HTTP 成功、交进队列都不算写入），单据上存状态、Express 单号与原因；会自己好的
停下原因每分钟 `push.poll` 再准备一次。主档与库存以 Express 为准：`express.masters` 每小时、写入后、管理员手动时经按需请求
``express_masters`` 请电脑读整套主档，按 Express 编码建 / 认领 / 更新客户、供应商、商品、本公司仓库，结存对齐成「Express 结存 +
未写入单据的变动」，差额记调整流水（`express_sync`），映射来的档凡 Express 有的字段只读（`locks`）。纯规则在 `express.contracts`。
完全不经过 Cowork；ERP 不导入 Cowork。

### CC 桌面版共用机器通道与 Express 写账规则（ADR-025，2026-10-23）

共用层两包，文件头写明多产品共用：

- `pearnly_core.express_rules`：Express 写账规则（载荷、预检、要人选的项、票面契约、账套键与按日期选账套、存货科目组），由 Cowork
  `push.express` / `contracts` / `targets` / `push.receipts` 原样搬来，纯函数；Cowork 推送与 ERP 直连都调它。Cowork 自己的规则（自动推送
  档位、费用科目建议、公司规则、商品入账方式）仍在 `pearnly_cowork.push.express`。
- `pearnly_core.cc_desktop`：`wire` 是 CC 桌面版线上约定（领单 / 回执、按需请求、写账分派、回执口径 `ack_rules`、上报净化
  `report_rules`），Cowork 的 `companion` 与本通道共用；其余是给 ERP 用的精简运行时：电脑登记（`devices`，对话页带设备密钥、Frappe
  会话）、机器认证（`http_auth`，只认 ``pcc_`` 令牌与精确路径，与 Cowork 的 ``exp_`` 互不相认）、账套可达（`books`）、写账队列（`jobs`：
  谁的活谁的电脑做、站点内同一账套同时一件（``writing_key`` 唯一约束）、每台同时一件、回执口径同 Cowork、租约过期钉回原来那台）、
  按需请求（`requests` / `store`）、机器方法（`machine`）。四张表 `Pearnly CC Device / Book / Job / Request` 只经本域写（`documents`）。
  产品经 hooks ``pearnly_cc_desktop`` 声明谁能登记电脑、回执交给谁、会请哪几种按需请求（`products`）；共用层不认识产品的表。
- 跨站点（Cowork 与 ERP 写同一份账套）的写者互斥在电脑那一侧：CC 桌面版写期间锁住账套目录里的 ``PEARNLY.LCK``。
- 登记的债：Cowork 仍用自己的 `companion` 通道，迁到共用通道的条件见 ADR-025「后果与验证」。
- `pearnly_core.transaction.conflicts`（本地事务撞 1020 / 1213 整笔重来）同批由 Cowork 搬来，两边共用。

## 票种与业务处理的共用口径（ADR-012）

票据的票种（完整税票 / 简式税票 / 收据 / 贷项通知单 …）与由它决定的业务处理（抵不抵进项、默认付款、要不要人看）只在
`pearnly_core.tax_documents` 定义一次：`codes` 是码表与提示词片段，`rules` 是票种判定与采购决策表，纯契约、只依赖标准库。
ERP 采购识别、Cowork 的识别枚举与 Express / MR.ERP 推送判据都调用它，产品里不再写票种字符串判断；改规则先加
`tests/fixtures/tax-documents/cases.json` 用例。详见 [ADR-012](../adr/0012-shared-tax-document-rules.md)。简式税票票面价含 VAT 的拆税
（`rules.included_vat`，2026-10-15）也在这里，Cowork 在识别存档时逐页调用（`ocr.pages.split_included_vat`）。

## 识别读法（ADR-013）

ERP 与 Cowork 发票识别都按 [ADR-013](../adr/0013-ocr-read-strategy.md)：便宜模型读 → 产品代码做确定性修复 → 只在金额 / 结构对不上时
同一模型带提示复核 → 仍不过「原图 + Vision 原文」再读（ERP 采购另按需用原文核对税号 / 买方）→ 还不过标红。
读的模型：ERP 固定 `gemini-3.1-flash-lite`（3.8 升级只是站点开关 `pearnly_erp_ocr_escalation`，默认关）；Cowork 电子 PDF 走文字层、
`gemini-3.1-flash-lite`，扫描件 / 照片的看图直读按站点配置 `OCR_DIRECT_MODEL`（`pearnly_cowork_ocr_environment`）选模型，
2026-10-02 过金标闸门后四个 Cowork 租户已写 `gemini-3.8-flash`（[记录](../work/2026-10-02-direct-model-3.8.md)；3.8 优惠价到 2026-12-31）。
Cowork 3.8 视觉复核 `OCR_LAYER3_ENABLED` 仍默认关。识别前都用 `ocr.orient` 本地转正。各产品只共用这套先后，字段、检查与结果格式各自保留。
改模型、提示词或规则先用 `scripts/ocr-policy-bench.py` / `ocr-policy-report.py` 在金标语料上比较，并过 [识别改动上线闸门](verification.md#识别改动上线闸门)（`scripts/eval-invoices.py`）。

**Claude 识别路线**（[ADR-022](../adr/0022-claude-ocr-route.md)）：站点配置 `pearnly_ocr_engine` = `claude` 的租户，三个产品的全部识别入口（Cowork 录入 / 表格文件 / 销项税核查发票 / 对账三类文档、ERP 采购销售、DMS 身份证）改走共用层 `pearnly_core.claude_ocr`（Haiku→Opus，硬性自检升级），只调 Anthropic、不调谷歌；共用层 `provider.post` 与 Cowork 交 Cloud Run 的 `forward` 对这类站点拒绝任何谷歌调用，新加识别入口必须先看路线（源码守卫在 `tests/test_claude_ocr_rules.py`）；运行在甲骨文 `claude-ocr` 服务，连不上时生产本机直调。引擎由控制面租户表单 / 开户单选择（`set_ocr` 写入）。改它的提示词、规则先过闸门：`scripts/claude-ocr/run-gate.sh` 让语料原件走产品正式代码（结果见 [任务记录](../work/2026-10-17-claude-ocr-route.md)）。这类租户的原件存甲骨文，见下面「统一原件层」。

**统一原件层**（[ADR-024](../adr/0024-shared-originals-layer.md)，2026-10-18）：三个产品所有识别入口收原件、读原件、删原件都走共用层 `pearnly_core.originals`，不再各自落文件。

- **存放按租户路线**（`store.where`）：选了 Claude → 甲骨文（`oracle`，经 Cloudflare 隧道 `originals.pearnly.com`）；谷歌租户 → 谷歌存储（`gcs`，核对 / 预览由 Cloud Run 做）；两样都没配的站点照旧存本机。存放处连不上就抛 `Deferred`：页面过几秒再来，队列重试，不退回生产存。
- **原件引用**：`oracle://<站点>/originals/<id><扩展名>#<sha256>`、`gs://<桶>/<站点>/originals/<id><扩展名>#<版本号>`；`<id>` 为 Cowork `CWS-` / ERP `ERO-` 加 48 位十六进制。
- **两种接法**：
  - 有自己记录表的（Cowork 录入台的 `Cowork Source File`）把引用存进 `storage_url`。
  - 要一条 Frappe File 的（ERP 附件、ERP LINE 收单、Cowork 对账临时文件）用**登记文件** `originals.registered`：File 名 `<id>.bin`，内容只有不到 1KB 的登记，读内容 / 原文件名 / sha256 一律经 `registered.*`。
    - 打开它的 `file_url` 时，`before_request` 钩子先按 Frappe 私有文件规则核权限，再跳转到签名下载地址。
    - File 扩展 `RegisteredFile` 在最后一条登记删掉时排任务删原件；复制附件只复制登记。
- **入口**：
  - 浏览器直传：`begin_upload` → PUT → `finish_upload`，用于 Cowork 录入台、ERP 采购 / 销售上传框（`erp-originals.js`）。
  - 生产手上已有的字节：`store.put` / `registered.put`，用于 LINE、CC 工具、POS、对账提交、iPhone 照片转 JPEG 后。
- **识别按引用读，生产不经手字节**：
  - Claude 路线：发票整份读 `client.read_original`；对账文档逐页读 `read_original(page=n)`，甲骨文用 `pages.frame` 出页，与生产 `PageSource.frame` 逐字节相同；ERP 多附件读 `read_originals`，甲骨文先转正再出页，与 ERP 在生产上先 `ocr.orient` 的做法相同。
  - 谷歌路线：发票、对账文档请求只带 `contract.original`，Cloud Run 按版本号读、核对 sha256。
  - 表格、电子 PDF 仍在生产解析：用 `read` 取回，不存。
- **预览**：甲骨文的由甲骨文画（签名地址）；谷歌存储的由 Cloud Run 画（`store.render`）。
- **例外**：DMS 身份证不留存，识别时直接交字节。

## 本地控制面与云端开户

管理后台继续运行在本机。正式开户只面向 `pearnly_target_runtime` 中明确配置 `executor=production`、且存在对应 Target 记录的云端目标。SSH 隧道的回环地址不等于本地租户。

云端产品的员工身份由 `control.account` 提供受限 RPC，复用私有 `billing.internal` 的运行载体和租户/产品 scoped token，但业务不进入计费域。`core.account.member_client` 仅负责该固定协议的 HTTP 调用，产品 `team` 域先校验员工管理权限。私有站点只额外开放该 POST 路径，不提供管理 Desk、通用 DocType API 或任意管理员操作；管理后台仍在本机。专用 Keycloak 客户端只具备用户管理权限，其密钥与归属签名密钥绝不下发租户。原生成员身份/操作记录持久化归属与幂等状态，已有全局邮箱/用户名不能被其它租户接管。详见 [本批记录](../work/2026-09-24-unified-release-onboarding.md)。

- 下拉框/API 只提供云端目标；新增或重开发号单、身份创建、开通计划和待办领取均受服务端约束。
- 缺配置、未知目标、本地目标拒绝；旧任务不得自动改指向默认云端。
- 历史目标/发号/部署记录保留可追溯，不因隐藏目标而删库。历史撤销流程属于独立授权的生命周期操作。
- 开发和隔离测试可以在本地建测试环境，但不得接入正式开户选择器，也不得借测试绕过生产鉴权、写者锁或计费边界。

## 自动执行与人工审查

运行 `python3 scripts/check-architecture.py`：检查全部 App 的根模块基线、Frappe 必备文件、DMS 薄兼容入口、内部旧路径引用、纯契约依赖和新增跨域依赖。DMS 与 Cowork 已存在的域依赖方向分别登记在 `architecture-policy.json` 的 `dms_domain_dependencies` 与 `cowork_domain_dependencies`，新增方向必须说明契约并审查，不能自动扩充。增加领域、例外或改变基线必须同时说明责任边界与理由，不能只改基线让检查变绿。

`python3 scripts/check-dms-offline.py` 只执行显式列出的已审计零 IO 检查（含完整快照读取器），检查失败标记和退出码。完整业务 / Site / 真机验收另按影响选择。

本地运行 `sh scripts/install-dev-hooks.sh`，从长期主目录安装仓库级共享 `core.hooksPath`。提交钩子导出实际暂存快照后检查，其他窗口和工作树也使用同一钩子，不受它们是否重新读取规则影响。旧分支缺检查脚本时拒绝提交并提示同步规则；不覆盖已有钩子。该配置仅属于本仓，不改全局 Git 配置。新克隆需要安装一次；人工禁用钩子仍然属于违规，Git hook 不是权限隔离机制。

`Development checks` 对 push / PR 运行架构和离线契约检查；生产镜像构建也先检查架构，再在候选 Frappe 环境验证开户限制与旧入口。GitHub 是否强制禁止合并取决于仓库分支保护，未配置保护不能称为不可绕过的闸门。

根 `AGENTS.md` 会作为该仓库后续任务的开发指令。已经运行的窗口可能保留旧上下文；它们需要重新读取当前文件或同步主线。文档不能远程替换已运行任务的上下文，自动检查负责发现代码层面的违规。

参考：[Frappe App 结构](https://docs.frappe.io/framework/user/en/basics/apps)、[创建 App](https://docs.frappe.io/framework/user/en/tutorial/create-an-app)、[hooks](https://docs.frappe.io/framework/user/en/python-api/hooks)。业务域分组和云端开户限制是本项目的约定，不冒称为上游强制目录规范。

### 操作员连接同步（2026-09-20 LINE 修复）

`operators.endpoints` 负责花名册到本人 Endpoint 的事务内同步，通过原生 DocType 更新和共享连接 `on_update` hook 触发；不调用外部 DMS。公司配置与管理员读凭据继承到员工连接，员工销售密码和提成归属保持独立。新增 `operators → contracts` 仅复用纯常量 `PERSONAL_BOOKING_DEFAULT_KEYS`，防止继承老板的顾问身份；contracts 无框架、IO 或反向依赖，不产生循环。原生迁移补齐已有操作员连接，停用/删除保留 Endpoint 与 User 审计关联并撤销使用权。

`account` 域恢复旧线上 LINE 菜单 3/4：依赖 `editor.browser_ticket` 的独立 account scope 与 `ocr.resolve_dms_endpoint` 的本人连接判据；不反向依赖 account。凭据修改走 DMS Operator 原生保存及上述同步 hook。保存前经 `adapter.account_probe.probe(本人连接地址, 本人用户名, 新密码, "login")` 试登录一次（登上即登出，不读写资料，与花名册 operators → adapter 同一入口），失败不存；登记 account → adapter 单向依赖，adapter 不引用 account，不产生循环（2026-10-13）。门户只消费 60 秒单次票据并提交给配置的 HTTPS DMS。LINE 绑定变更以 core hook 通知 menu；menu 读取 query 权限，异步同步已经发布的菜单，不在文档保存时创建或切换默认菜单。

## 通用界面框架

所有迁移与新功能遵循 ADR-005/007：产品 Web 与管理后台复用同一版本原生 Frappe Desk，页面/表单/列表/弹窗/提示/音效使用原生 API。品牌色仅用于强调，保留官方白底、深色正文与布局。业务 API、角色、购买功能和鉴权边界保持服务端约束，不授予客户平台超管。

只有 LINE 票据编辑器保留独立 Vue + Frappe UI，因为它使用一次性票据而非 Desk 登录。Keycloak 与邮件分别保留自己的原生认证主题、邮件兼容模板。此例外不得扩展成第二套产品 Web 壳。

智能体对话页 `/assistant` 是第二个限定例外（[ADR-019](../adr/0019-assistant-foundation.md)）：React + assistant-ui，只在 `frontend/assistant/`，只服务这一个入口。

### 智能体底座（ADR-019，2026-10-10）

`pearnly_core.assistant` 是三产品共用底座：`contracts` 纯约定；`engines` 是引擎目录（唯一登记处，纯数据）与适配器接口 `engines/base.py`，各厂商实现放 `engines/<code>.py`；`config` 读本站的引擎、模型、密钥与产品；`engine` 取当前适配器并缓存其远端资源；`definition` 从工具声明生成与厂商无关的智能体说明，共用说明不认识产品，产品的说话规矩经 `pearnly_assistant_guidance` 接在后面；`tools` 汇总共用与产品声明（`pearnly_assistant_tools`）并在服务端以当前用户身份执行；`sessions` 做归属判定与会话读写；`api` 只是白名单入口。`files` 把对话里的文件在本站私有目录留一份（用户上传；2026-10 起工具产出 `origin="tool"` 与产品从文件夹取来的 `origin="folder"` 也留），工具经 `attachment` 取用、经 `origin` 查来源，删对话时一起删。会话归属表 `Pearnly Assistant Session`、工具执行记录 `Pearnly Assistant Tool Run` 在 Core。对话提醒 `notices` 与表 `Pearnly Assistant Notice` 也在 Core：产品只经 `notices.post` 发提醒（类型、标题、正文、手机推送有效期都由产品传入，共用层不认识产品的提醒类型）；提醒不写进引擎会话，事件 `pearnly.notice`（编号 `pnotice_` 开头）只在站点与对话页之间，用户下次发话时与当地日期合成批末唯一一条系统消息带给智能体；`sessions` 单向依赖 `notices`。手机 / 电脑浏览器推送：`webpush` 是纯加密与校验（RFC 8291 aes128gcm、RFC 8292 VAPID ES256，只用标准库和随基础镜像带的 cryptography）；`push` 管本站 VAPID 密钥（`Pearnly Assistant Push Key`，单例，私钥用密码字段）、设备订阅（`Pearnly Assistant Push Subscription`，按推送地址 sha256 唯一，同一设备换人登录时覆盖）和发送；推送地址只认 FCM、苹果、Mozilla、微软几家推送服务，不跟随跳转，只有 404/410 才删订阅。对话归在哪家（ADR-020）：会话表 `project` / `period` / `project_scope` / `project_noted`，`projects` 只认产品经 hooks `pearnly_assistant_projects` 声明的 provider（`KIND / PERIOD / SCOPE / allowed / view / context`），没有声明的产品 `kind = None`、一切照旧；`sessions` → `projects` → `sidebar` 单向依赖，实时事件 `pearnly.project` 逐人推；没挂对话的提醒可带 `view`（点开去哪一页）。产品不得在自己 App 里另起会话或直连模型厂商，共用层除适配器外不得出现厂商名、密钥格式或模型名；产品工具只调本产品已有服务，不绕过其权限、套账与数据范围校验。

侧栏标题与多设备同步（2026-10-15，教程第 7、11 条）：`titles` 管侧栏标题——首条消息原文先当临时标题，第一轮结束后（推送线程 `relay` 看到结束时排；没看到的由下一条消息或读运行状态时补排）用引擎目录的 `title_model` 概括一次，按显示宽度卡长度（`contracts.make_title`），会话表 `title_source` 记来源（`message` / `summarizing` / `summary` / `user`），用户手动改过名的不再自动覆盖。`sidebar` 在新建、改名（含自动标题）、星标、归档、删除时经对话事件的同一个实时事件名（`contracts.REALTIME_EVENT`，信封 `session_id` 为空、事件 `pearnly.session`）只推给会话主人，对话页 `list-sync.ts` 并进侧栏，回到前台时再核对一次第一页，不另开定时。依赖方向：`sessions` → `titles` / `sidebar`，`relay` → `titles` → `sidebar`；`titles`、`sidebar` 不导入 `sessions`、`relay`。旧排队任务的点路径 `sessions.retitle` 保留为薄兼容入口。

### 操作员提成归属的读取边界（2026-09-20）

`operators.advisors` 的下拉选项只读手动发布的 `company` 快照，缺缓存或范围失效不回退实时读取。保存非空顾问 ID 才经 `adapter.operations.run_logged_in(authoritative_read=True)` 窄读原生顾问 selector 的全部分页，并按实时 ID 取名；不再调用旧全量 `masters_cache`。返回前以当前锁定读重验同一启用管理员，并核对唯一主连接及凭据范围，失败拒绝保存，不提交调用方事务。登记的单向依赖改为 operators → snapshots/adapter；没有反向引用 roster，也没有外部业务写入。顾问业务校验归 operators，抓取与账号互斥仍归 adapter。

### 推送记录管理的边界（2026-09-20）

`records.access` 提供 DMS 推送历史的产品/角色/功能权限和当前 Site 内的记录范围，`query.records` 只读复用这些定义，因此登记 query → records 单向依赖。`records.manage` 用 POST 原子校验选中记录并记录移除人/时间；原生 Push Log 的业务字段、状态、重试和客户查重凭据保留。此依赖不指向 query、外部适配器或业务写入，不增加循环，也不授予通用 DocType 删除权限。

### 首页记录删除的边界（2026-09-20）

`core.payments.history` 提供选中项校验和原生记录展示删除标记；产品 `billing_www.delete_records` 必须先校验公司管理员。OCR 记录限定当前产品 Site；用量和充值记录经既有受鉴权的 `billing_client` 发送给 `control.payments.history`，由计费端固定 DocType 并按服务凭据对应租户逐项加锁校验。Control 单向依赖 Core 的内部记录标记方法；Core 不导入 Control。请求不能指定 DocType 或租户。

三种原生记录增加隐藏只读 `records_deleted_at/by`。产品列表和导出不再展示被删除项；钱包、阶梯页数、计费幂等、充值审核及原始凭据继续读取原行。删除不退款、不取消待审核充值、不授予通用 DocType 删除权限。首页仅复用现有原生列表的选择和确认行为，不另造表格或分页。

### 已成功订车的后续交付

`booking.delivery` 使用自有原生 `DMS Booking Delivery` 持久化已经独立回读确认的订单结果、原身份版本和稳定回执key。先保存后释放当前LINE草稿，后台只补附件及LINE回执，不调用建单入口。回执不等附件：缺附件时回执照常当场发，之后后台只补附件（补齐不发消息），补满6次仍失败才发一次“单号+附件失败”提示并转Review（2026-10-13有意变化，见[建单执行体](../p4/44-booking-flow.md)）。其权限仅System Manager只读，不新增产品导航；未核实订单仍保留原提交标记。`booking.attachments` 负责已选择消息的内容获取，`client.attachments` 负责原生完整表单上传与逐件回读，依赖方向不反转。

### 三产品共享识别执行

Core 的 `execution` 域负责持久工作单、容量准入、供应商并发/速率、输入与模型检查点、原生 RQ 投递及有界修复。产品通过 `pearnly_ocr_handlers` 和失败通知 hook 登记自己的幂等工作流；Core 不导入 DMS/ERP/Cowork 的业务模块。DMS、Cowork（`cowork.invoice` 等）与 ERP 的 LINE 收单（`erp.line_intake`）都已接入并在生产运行；ERP 网页上传识别 2026-10-02 起是 `ocr` 队列里的普通后台任务（`recognition.background`，进度与结果存不淘汰的状态存储，页面推送优先、轮询兜底：在线 20 秒、断开 5 秒），不走共享工作单。

Cowork `ocr.execution`单向依赖engine/table_engine/inputs_runtime/table_inputs/providers、files/history/feedback与Core；history保存单向调用feedback，feedback通过本Site原生记录检查来源，不反向导入history流程。Batch固定actor/公司/原件/日期/策略/纠错快照，Core Work只存Batch引用。新controller与Work mixin阻断原生更新/分享绕开付费检查点，不给共享Work增加客户read权限。`ocr.engine`、schema、validation及纠错model保持纯计算。细节见[执行合同](../work/cowork-backend/invoice-execution.md)。 表格字节解析归table_inputs，纯L2编排复用engine；files持有不可变逻辑区段清单，history的逻辑索引不授予PDF预览。Batch分别固定结果单元数与字符计费单位，旧PDF/image任务字段向后兼容；完整性与通用模型边界见[表格执行](../work/cowork-backend/table-engine.md)。


Cowork第六批 `roundtrip` 域提供固定复核工作簿契约、字节解析、导出和旧记录权限边界；ocr.execution在入队前选择确定性回导，复用Core工作单与计费；roundtrip不反向依赖执行编排。roundtrip.contracts/results只复用OCR schema/pages纯值，intake复用有界table_inputs；files的manifest新增roundtrip_document单位，每个解析单据对应源sheet/行，不把工作表数当票数。导出只消费授权History，外部ERP事实尚未接入，不新增前端入口。纯规约共用同一份spec/money，不能在读写端各算一套。见[往返原生合同](../work/cowork-backend/roundtrip-native.md)。

Cowork第七批 `targets` 是无IO的目录/目标证据合同；`push.express` 是显式输入的进销项准备闭包，单向依赖 `contracts.push`、`targets.identity` 与 `push.receipts` 的纯解包；`push.lifecycle/retry/receipts` 定义状态、恢复判据和回执投影。两种置信规则分别服务OCR字段证据与费用过账门槛，不能互换。以上不依赖Frappe、HTTP、环境变量、数据库或工作队列；日期和授权目录由后续服务端注入，不反向依赖识别执行或页面。没有注册ERP写入消费者，不能用共享OCR Work装载外部订单。第八批已装配原生连接、推送准备和持久账本；真实目录收集及外部消费者仍未接通，纯 `ready` 不是外部成功证明。见[运行证据与执行缺口](../work/cowork-backend/push-runtime-baseline.md)。

Cowork 发票（图片 / PDF）在 Cloud Run 上执行（[ADR-015](../adr/0015-ocr-on-cloud-run.md)）：`ocr.remote.forward` 在识别工作单里按份拿名额、把冻结配置与原件交给不公开的 Cloud Run 服务 `cowork-ocr`；生产经 Cloud Tasks 派发（站点配置 `pearnly_cowork_ocr_tasks`，4 个队列分片），Cloud Run 读完回调唤醒工作单，之后复核、存结果、推送、扣费在服务器上完成——结果一入库先通知等候页再扣费，入库事务内范围权限只查一次。识别进程是 `pearnly_core.execution.ocr_worker.OcrWorker`：从队列取到识别任务直接交空闲线程，RQ 线程不再先做一遍完整站点初始化；进程数与线程数由 `infrastructure/production/sizing.py` 按机器规格算（生产 8 × 12，见 [生产容量](production-capacity.md)）。`ocr.remote.runner/gcs/app` 只在不含 Frappe 的镜像里运行同一份引擎，检查点与租约存新加坡存储桶（7 天删除）。`contract` 为两边共用的纯约定；`runner` 不导入 Frappe、不连数据库或额度 Redis；`pearnly_cowork_ocr_remote` 关掉即回本机执行、`pearnly_cowork_ocr_tasks` 关掉即回按行等回复。

MariaDB 工作单是任务事实；RQ/延迟集合仅为可修复投递记录。各产品 bench 的原生队列保持隔离，容量使用同一个非淘汰 Redis DB6，按租户统一核算、按供应商项目/区域/模型调速。等待不占 OCR worker，网络结果未知不自动重做付费识别。此队列不得接入外部订单写入；DMS 写者锁与订单恢复仍由 booking/adapter 负责。

`infrastructure/production/capacity.py` 在正式开户或激活前按候选 Compose 的内存硬限额计入所有现有产品及宿主预留，拒绝超额，不以空闲 RSS 推断容量。当前生产的进程、线程、识别名额、Redis 与 Cloud Run / Cloud Tasks 参数见 [生产容量](production-capacity.md)；共享准入与恢复规则的最初设计和证据见 [共享 OCR 容量](../operations/shared-ocr-capacity.md)。

### Cowork 连接与推送账本（2026-09-21）

`connections` 域持有原生 Connection/Snapshot/Refresh、显式连接授予、当前目录与可信准备资料；依赖 `targets` 纯合同和 Core 权益，不反向引用推送。Connection 是配置、刷新、快照安装和预留共用的事务锁；目录、用户、角色与公司范围使用当前读取。凭据只进原生 Password，专用查询不返回原文。没有安装真实采集器，公开请求不能上传权威快照。

`push.application` 是专用服务端装配入口：先连接、后票据加锁，重验当前权限和版本，解析不可变 Source/Batch 的回导原键，读取本地成功账本，再调用纯 Express 准备并保存冻结结果。公司税号来自 Workspace，客户端不能替换公司或凭证来源。只返回准备状态、诊断与回执摘要，不返回运输载荷或凭据。RPC 只在无外部IO的本地事务发生数据库冲突时有界重试；内部组合调用必须直接使用 service。重试统一经共用的 `transaction.conflicts`（1020 / 1213 整笔回滚、退避，用尽抛 `Exhausted`），人推入口经 `push.application.conflicts`（用尽给 `push.native_lock_conflict`），自己按步提交的人推编排（`assistant.tools._push_one`：准备一笔、确认一笔）按自己的事务边界也经它。

`push.native` 单向依赖 connections、history/access 和纯 targets/lifecycle/receipts；以三个原生 DocType 持久化 Record、Attempt、Writer Lock。记录及载荷不可变、当前版本放行、端点与公司/账套互斥、随机尝试令牌摘要及递增序号共同防重；旧派发结果未知时保留写锁，不自动重领。过时准备仅在从未派发的证据完整时可撤销替换；真实旧尝试的迟到回执仍可记录。所有框架 CRUD、分享入口拒绝绕过专用域服务。

截至第八批没有公开 activate/lease/ACK、队列消费者、scheduler、Companion或MRERP运输入口；第十至十二批的后续入口见下节。内部 AdapterContext 只是服务端装配约束，不是客户机身份认证，不能宣称跨Site/物理设备写者锁已验收。新Record的 prepared 只代表本地准备，不是排队、发送或入账成功。未添加菜单、页面或其它产品能力入口；依赖及剩余外推边界见[原生连接](../work/cowork-backend/connections-native.md)和[原生推送](../work/cowork-backend/push-native.md)。

### Cowork 凭据与盘点导出（2026-09-21）

`companion` 单向依赖 `connections.access` 的当前授权；contracts 保持纯计算。原生 Express Agent Credential 每次发行一行，摘要与末四位代替明文，Connection→Credential 顺序锁保护轮换、撤销、请求回执和代际唯一性。机器上下文必须来自内部认证的对象能力，写前重新核对当前身份与授权；截至第九批只有人工管理RPC及内部认证，没有auth_hooks、心跳、目录采集或运输入口，不创建平台User。推送总闸同样限制发行和派发认证，关闭后仍可管理撤销。旧客户端实际运行文件来源与全量配置差异尚未闭合，不能据本地凭据完成而切换真实旧客户端或迁入旧业务状态。

`stocktake.export` 只消费同域service.report提供的已授权全量锁定快照，依赖既有Decimal数量与比较合同，输出固定泰语复核工作簿协议；不读取ERP目录或文件，不重新实现权限。专用GET下载沿原生私有响应和Access Log，照片和legacy模式尚未承接。两项均不增加菜单/页面。详见[身份合同](../work/cowork-backend/companion-identity-contract.md)和[盘点导出](../work/cowork-backend/stocktake-export.md)。

### Cowork 机器心跳及回执接口（第十至十二批）

`companion.http_auth` 只接受三个精确 POST RPC：`heartbeat.report`、`queue_api.lease`、`queue_api.acknowledge`；2026-10-07 另列一张表 `FOLDER_METHODS`，追加文件夹自动录入的四个精确 POST RPC（`folder_intake.machine` 的 `sync` / `begin` / `finish` / `workbook_file`），同一套信封、同一份配对码身份，原三个的路径、校验与身份逐字不变；2026-10 再列 `REQUEST_METHODS`（`companion.request_api` 的 `next_request` / `complete_request`，按需请求），MR.ERP 的只读文件夹配对码也能调（`lease_next` 按连接的 ERP 只给文件夹的那几种）。2026-10-15 再列 `DEVICE_METHODS`（`companion.device_api` 的 `hello` / `leave`，认电脑与电脑自己断开，ADR-021），四张表互不重叠，MR.ERP 的只读文件夹密钥能调的集合是 `FOLDER_ONLY_ALLOWED`（文件夹、按需请求、认电脑）。每个请求绑定独立操作类型和请求对象，机器身份不能继承到通用 RPC、资源 CRUD、别名或浏览器会话；保留原生 issuer IP 限制。新原生 Connection / 新凭据的报告单向调用 `connections.collector`，目录刷新不自动授予推送准备资料。

`companion.transport` 单向调用 `push.native` 账本，不包含 HTTP 或提交。私有 Delivery 把线协议 UUID 绑定到本 Site/Connection/历史 Credential/Attempt/fence；原始 attempt secret 使用原生 Password 密文，不编码进句柄。`receipt_auth` 的窄能力可在停闸、撤权或轮换后接收原历史任务结果，不能领取新任务或写报告，也不跳过原 issuer 的 IP 限制；它与当前派发能力互不通用。

`queue_api` 为薄事务入口，显式提交成功才返回原生 Frappe `message` 封套；只对提交前本地死锁有限重试并重验权限（1020 / 1213 经 `transaction.conflicts`，与心跳、机器认证、认电脑、按需请求、电脑登记 / 移除同一机制，用尽各回原有的「忙」/ 错误），提交或回滚失败不重跑任务。错误堆栈、Recorder 和原生消息列表不得带出 Bearer 或 attempt secret。没有后台外部写者、真实 Windows 接线或 ERP 调用，prepared 不由 poll 激活；人工激活、准备事实、History/库存/通知副作用仍待独立承接。实际旧客户端路径及顶层 JSON 协议需要适配，不能据这些本地接口称为直接兼容。见[HTTP 边界](../work/cowork-backend/companion-queue-http-boundary.md)和[第十二来源审查](../work/cowork-backend/twelfth-source-review.md)。

### Cowork 人工确认与 ACK 本地联动（第十三至十四批）

第十三批 `connections.preparation` 从原生数据域采集准备资料并逐类绑定机器缓存来源，`companion.submission` 以原actor/current权限及凭据实现人工确认；poll不自动激活。唯一明确例外（用户 2026-10-07）：`folder_intake` 的自动录入规则——规则负责人启用规则即视为对该规则文件夹票的常设确认，由后台以负责人身份调用同一组 `push.application.service`（`choices_view` / `submit` / `confirm` / `cancel_prepared`），判据、账本与 CC 桌面版队列不变；网页手动推送与 poll 仍不自动激活。2026-10-12（F6）：`push.application.service` 在调用方没给 `fixed` 时按框架钩子 `cowork_push_fixed_choices` 取「这张票已定的选择」（提供者签名 `(history, connection, account_set) -> {kind: value}`，只读、取不到回 `{}` 不抛错；合并后去掉 `party`，只对这张票生效、不写进账套记忆）。Cowork 登记 `folder_intake.push.fixed_for`：定时任务的票推往该任务的连接与账套时回任务默认处理。push 域因此在运行时用到 folder_intake，但不导入它（与 `cowork_ocr_recognized` 同一做法）；`prepare`、`choices_view`、`save_choices`（拒收被任务默认值决定的类别）统一经 `_fixed`。第十四批 `push.native.completion` 在同一ACK事务中单向调用 `history.push_state` 和 `connections.stock_effects`，没有反向依赖。History是原账本的窄状态投影，不改变人工审核版本；库存增量只影响匹配账号/root/原报告凭据的画像。

唯一 `Cowork Push Notification` 为push域私有原生事件，继承通用CRUD/分享拒绝，仅LINE来源首次成功保存held素材；无发送消费者或接收人权限。失败回滚账本、投影、计数、事件和锁释放，重复ACK不重放副作用。复核表仅通过既有History范围读取服务器状态；单号和主档动作事实待后续接入。详见[第十四批合同](../work/cowork-backend/fourteenth-source-review.md)。以上替代前述旧批次中相应待办，不代表真实Windows接线、完整通知功能或独立ERP回读已验收。

### Cowork 复核表的回执事实（第十五批）

`roundtrip.service` 单向调用 `push.export_facts`，后者只读取本站原生 Push Record 并通过 `connections.service.read_for_log` 检查当前日志功能、角色、公司/客户和连接授予；按原 actor / History 选最新成功。History 的最新推送状态与最后成功回执分开，不用失败回执覆盖原成功单号。`push.export_actions` 为纯解析，复用既有库存科目组合同，不依赖 Frappe 或 IO；没有新的反向依赖。

追加的库存组显示名称由 connections 域按回执的账号/root/报告凭据来源提供，不读取密码。票据版本变化后保留 ERP 引用，但不把旧行新建标记、方向和主档清单套入新票面。通用账本 CRUD/分享限制保持；无新 DocType、API、菜单或外部出口。详见[第十五批合同](../work/cowork-backend/fifteenth-source-review.md)。

### Cowork 可选旧客户端运输入口（第十六批）

`companion.wsgi:application` 显式包装原生 `frappe.app.application`；同域 `legacy_wire` 只把三个精确旧 POST URL 映射到既有心跳/lease/ACK RPC，并将成功的 `message` 字典解包为旧客户端顶层 JSON。其它请求原样交给原生应用；不注册新的 hook、角色或身份，也不复制业务校验/事务/重试。原生应用完成提交和清理后才输出适配响应，异常成功封套返回稳定502，不当空队列或成功回执处理。

默认基础设施仍使用原生入口，本包没有启用监听或切换 Windows。固定旧 QueueClient 经真实 httpx WSGI 运输在隔离 Site 验证，Windows EXE 的54个业务模块及 launcher 与固定源码编译结果一致；实际机器连新平台、物理写者互斥和独立ERP回读未验收。详见[第十六批合同](../work/cowork-backend/sixteenth-source-review.md)。

### DMS 连接页手动读取（2026-09-24）

`connections.snapshots` 单向调用 `snapshots.manual` 的手动请求与本地状态服务。RPC 每次校验 DMS Admin/System Manager，只解析本站唯一主连接，不接受调用方指定 Endpoint、读取账号或操作员；GET 只返回分项状态与时间，不排队、不读取外部 DMS。POST 限车辆、客户、地址、公司四组，去重、当前作用域与后台读取由 snapshots 域负责。连接页仅保留原生连接信息和“更新数据”入口，不展示快照资料、进度、数量、时间或错误码字段。原生 Dialog 选择资料后用 `frappe.dom.freeze/unfreeze` 全屏显示“数据更新中...”并等待本次请求的终态，只提示结果；空闲不轮询，活动任务的本地状态轮询不触发外部读取。请求标识不匹配、状态异常或离页时必须解除遮罩，不能把旧代际成功当作本次成功。

四组完整原生资料采集归 `client.catalogue_reader` 与同域 schema，adapter 只编排有界会话、私有检查点和发布。`snapshots.directory` 组合各组已完成的 LINE 投影，不把原始公司/车辆表单或员工目录扩散到公开界面；车辆颜色只依赖车辆组，称谓只依赖客户组。没有新增域依赖或后台周期扫描，最终提交的实时权限、状态和写入校验不变。
