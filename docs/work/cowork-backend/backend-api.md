# 第一批原生后端接口与前端讨论边界

本批完成的是“公司/客户授权 → 保存完整票面草稿 → 复核保存 → 幂等完成 → 历史回读”的后端闭环。已可据此讨论第一屏录入/复核的交互，但**不是整个 Cowork 迁移完成**。未编写页面或修改现有导航；现有 `Intake Record` 页面仍是早期样板，不是这些接口的前端。

## 原生落点

| 域 | 本批实现 | 依赖及数据归属 |
| --- | --- | --- |
| access | 产品、购买功能、角色、公司和买方客户范围；原生列表/详情权限；阻断 DocShare 回退 | Core 仅提供 entitlement；当前 Site 的 User / Role / User Permission |
| masters | 公司、买方客户创建及稳定分页 | Cowork Workspace / Cowork Client；不存外部凭据、不自动开外部账套 |
| history | 完整 pages、首存 ai_raw、摘要、保存、完成、版本冲突及请求幂等 | Cowork OCR History；依赖 access 和纯 contracts |
| contracts | 来源、日期、摘要、方向、过账声明和外推选择预检 | 无 Frappe / 网络 / 数据库 |
| reconciliation / stocktake | 已核实的第一轮强配对和盘点纯计算 | 仅内部纯函数；尚无任务持久化或产品 API |

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
| OCR 上传、分票、缓存、计费、队列及失败恢复 | 只有保存合同；继续接 Core execution 和固定来源识别流程 |
| 原件、PDF 预览、附件 | 尚无下载 API；同 PDF 多票跨公司时须按允许页输出或验证整份权限，不能仅授权一条记录就放整份文件 |
| 外推、目标刷新、年度账套、权威日志 | 本批只有纯预检；连接、锁、reservation、消费者、未知结果和回读均待迁 |
| LINE、Companion | 来源映射已完成；验签、当前绑定、单次票据、租约/ACK、消息回执等仍待迁；Windows 实际运行来源待核验 |
| 对账 | 仅第一轮单号强配对。金额取整/容差与 Decimal 有已证实差异，第二轮、银行、GL/VAT 未迁 |
| 盘点 | 纯数量/导入校验/差异已迁；原生任务、条目、照片、关闭、导出等未迁 |
| 汇总表、邮件、归档、导出、删除、自动主档匹配 | 来源已登记，业务链未迁；汇总表旧桥接正式单据行为不能混进普通完成 |
| WeKan | 旧 VM 已停止；当前运行来源证据不足，未启动或移植 |
| 现有早期 Intake Record 页面 | 没有接新接口；不作为功能验收 |
| 员工界面、授权操作和各页面导航 | 本批仅后端原生 Role/User Permission；前端与控制面易用性在后续接入时核对 |

前端讨论从录入/复核这条已验证接口开始，以现有 DMS 的原生 Desk 交互为参考；不搬旧 UI。附件预览、上传识别或推送按钮必须等相应后端完成，不能用本批人工 pages 接口伪装这些能力。
