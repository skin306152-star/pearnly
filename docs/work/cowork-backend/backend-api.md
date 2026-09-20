# 原生后端接口与前端讨论边界

本批完成的是“公司/客户授权 → 保存完整票面草稿 → 复核保存 → 幂等完成 → 历史回读”的后端闭环。已可据此讨论第一屏录入/复核的交互，但**不是整个 Cowork 迁移完成**。未编写页面或修改现有导航；现有 `Intake Record` 页面仍是早期样板，不是这些接口的前端。

2026-09-21 继续完成原件上传/受控下载/物理页预览、识别结果整批绑定，以及盘点原生后端。下面清单已按第二批状态更新；仍按用户要求先补后端，不自行启动前端施工。

## 原生落点

| 域 | 本批实现 | 依赖及数据归属 |
| --- | --- | --- |
| access | 产品、购买功能、角色、公司和买方客户范围；原生列表/详情权限；阻断 DocShare 回退 | Core 仅提供 entitlement；当前 Site 的 User / Role / User Permission |
| masters | 公司、买方客户创建及稳定分页 | Cowork Workspace / Cowork Client；不存外部凭据、不自动开外部账套 |
| history | 完整 pages、首存 ai_raw、摘要、保存、完成、版本冲突及请求幂等 | Cowork OCR History；依赖 access 和纯 contracts |
| contracts | 来源、日期、摘要、方向、过账声明和外推选择预检 | 无 Frappe / 网络 / 数据库 |
| files / history.recognition | 精确原件、按页授权、不可变整批关联与跨操作者幂等回读 | Cowork Source File + 原生私有 File；files 不反向导入 history，关联查询只读当前 Site |
| stocktake | 原生任务/商品/扫描/回执、累计、关闭和 JSON 报告 | 四个 Cowork Stocktake DocType；不写库存账；详见盘点记录 |
| ocr / reconciliation | 输入限制、页展开、置信度及第一轮强配对纯函数 | 尚无供应商执行、完整分组或对账任务持久化 |

DocType 控制器留在原生目录，hooks 只登记入口。没有修改 Core、DMS、Control、前端或底座版本。术语新增沿既有 TSV → Frappe CSV 入口，只生成 Cowork 语言包。

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
| OCR 上传、分票、缓存、计费、队列及失败恢复 | 原件上传、输入限制、页关联、置信度和整批保存已有；供应商/ThaiInvoice/金额闸/完整分组、Core execution 接线、计费与缓存待迁，见 OCR 记录 |
| 原件、PDF 预览、附件 | 新上传原件、整份及按物理页读取已实现；跨公司/同页多票拒绝越权，原生下载/ZIP旁路已堵；旧可搜索PDF、加密文件及历史回灌尚未迁 |
| 外推、目标刷新、年度账套、权威日志 | 本批只有纯预检；连接、锁、reservation、消费者、未知结果和回读均待迁 |
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
