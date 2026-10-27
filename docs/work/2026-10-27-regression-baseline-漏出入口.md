# 第 1 步：逐岗位「漏出入口」清单（页面 DOM 快照自动产出）

> 2026-10-27 验收窗口。本机回归实验室：线上同版镜像 fb1037ad + `main` 4561884f 源码（还没有 work-followups 的外壳白名单），
> 站点按正式开户同一套 `finalize_site` / `ensure_feature_roles` 建，17 个岗位账号（ERP 8、Cowork 6、DMS 3），界面中文。
> 每个账号打开登记表全部页面 + 个人页 + 20 个常见框架路由 + 本产品全部 DocType 列表，并顺站内链接走（同类页最多 2 个实例）。
> 原始结果：`~/pearnly-devtools/regression-baseline/2026-10-26/`、`2026-10-27/`（JSONL）。工具：`scripts/smoke/inventory.py` + `scripts/regression/leaks.py --matrix`。

## 结论

1. **线上 testcowork 个人页漏按钮的根因是角色，不是页面**：那个租户管理员带 System Manager（10-20 换号池时加上，work-followups 收回中）。
   按正式开户建的账号，三产品所有岗位的个人页都只有「保存」，没有「权限 / 创建用户电子邮件 / 冒充」。生产 7 个业务站点只读核过，只有 testcowork 这一个账号如此。
2. **全部岗位（含只有 Desk User 的「无岗位」账号）都能直接打开一组框架页面**：联系人、数据面板（含新建表单）、事件、文件、模块定义（列表与表单）、备注、通知日志、报表列表与报表表单、待办、网站、工作区（列表与表单）。
   页面上带框架的「添加 / 报表视图 / 看板视图 / 数据面板视图 / 已保存的筛选器 / 自定义快速筛选」等菜单。这些都不在任何产品声明里。
3. **本产品 DocType 的原生列表直接输地址就能打开**：ERP 31 张、Cowork 65 张、DMS 4 张，岗位越多能开的越多；
   其中 Cowork 39 张内部表（Express 凭据、ERP 连接、推送锁等）DocType 上给了 All / Guest 读，所有岗位都能打开列表外壳。
   这些表有 `has_permission` / `permission_query_conditions` 在服务端拒记录（例 `companion.documents.native_permission` → deny），**数据拿不到，但列表外壳和框架菜单在**；数据层面由 B 的接口矩阵逐张核。
4. **用户列表 `/desk/user`**：ERP 6 个业务岗位、Cowork 5 个、DMS 3 个都能打开，带「添加用户」。
5. **全局入口（每页都在）**：
   - 快捷键：ctrl+g 全局搜索、ctrl+k Awesomebar、shift+t 打开控制台、shift+ctrl+g 换主题、shift+ctrl+r 清缓存、shift+/ 快捷键列表、ctrl+/ 侧栏、ctrl+p 打印、ctrl+s 主操作。
     work-followups 的白名单只留 ctrl+s，其余运行时注销。
   - 顶栏搜索框在部分页出现。
   - 应用切换菜单「首页 / 网站 / 侧栏排序 / 显示 / 刷新 / 注销」：按用户 09-20 口径都是应有项。无业务岗位的账号少了「侧栏排序」，「无岗位」的 ERP 账号连「首页」也没有，这些也照实进基线。
6. 我们自己的页面上没有发现框架默认按钮（各页页头只出现本产品代码加的按钮）。

## 「声明外能打开的地址 × 岗位」表

## erp：声明外能打开的地址 × 岗位（共 49 个地址）

| 地址 | admin | products | stock | purchase | sales | cashier | tasks | none |
|---|---|---|---|---|---|---|---|---|
| `/desk/contact` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/dashboard` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/dashboard/<记录>` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/event` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/file` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/file/<记录>` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/module-def` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/module-def/<记录>` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/note` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/notification-log` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/report` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/report/<记录>` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/todo` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/website` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/workspace` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/workspace/<记录>` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/erp-company` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |  |  |
| `/desk/erp-product` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |  |  |
| `/desk/erp-serial-movement` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |  |  |
| `/desk/erp-serial-no` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |  |  |
| `/desk/user` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |  |  |
| `/desk/user/<记录>` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |  |  |
| `/desk/erp-customer` | ✓ |  |  |  | ✓ | ✓ |  |  |
| `/desk/erp-line-intake` | ✓ |  |  | ✓ | ✓ |  |  |  |
| `/desk/erp-pos-terminal` | ✓ |  |  |  |  | ✓ |  |  |
| `/desk/erp-purchase-category` | ✓ |  |  | ✓ |  |  |  |  |
| `/desk/erp-purchase-settings` | ✓ |  |  | ✓ |  |  |  |  |
| `/desk/erp-sale` | ✓ |  |  |  |  | ✓ |  |  |
| `/desk/erp-sales-document` | ✓ |  |  |  | ✓ |  |  |  |
| `/desk/erp-shift` | ✓ |  |  |  |  | ✓ |  |  |
| `/desk/erp-stock-balance` | ✓ |  | ✓ |  |  |  |  |  |
| `/desk/erp-stock-count` | ✓ |  | ✓ |  |  |  |  |  |
| `/desk/erp-stock-count-entry` | ✓ |  | ✓ |  |  |  |  |  |
| `/desk/erp-stock-count-item` | ✓ |  | ✓ |  |  |  |  |  |
| `/desk/erp-stock-entry` | ✓ |  | ✓ |  |  |  |  |  |
| `/desk/erp-stock-movement` | ✓ |  | ✓ |  |  |  |  |  |
| `/desk/erp-stock-opening` | ✓ |  | ✓ |  |  |  |  |  |
| `/desk/erp-supplier` | ✓ |  |  | ✓ |  |  |  |  |
| `/desk/erp-warehouse` | ✓ |  | ✓ |  |  |  |  |  |
| `/desk/erp-work-activity` | ✓ |  |  |  |  |  | ✓ |  |
| `/desk/erp-work-board` | ✓ |  |  |  |  |  | ✓ |  |
| `/desk/erp-work-card` | ✓ |  |  |  |  |  | ✓ |  |
| `/desk/erp-work-column` | ✓ |  |  |  |  |  | ✓ |  |
| `/desk/erp-work-field` | ✓ |  |  |  |  |  | ✓ |  |
| `/desk/erp-work-label` | ✓ |  |  |  |  |  | ✓ |  |
| `/desk/erp-work-mirror` | ✓ |  |  |  |  |  | ✓ |  |
| `/desk/erp-work-notification` | ✓ |  |  |  |  |  | ✓ |  |
| `/desk/erp-work-rule` | ✓ |  |  |  |  |  | ✓ |  |
| `/desk/purchase-document` | ✓ |  |  | ✓ |  |  |  |  |

## cowork：声明外能打开的地址 × 岗位（共 84 个地址）

| 地址 | admin | intake | recon | stocktake | work | none |
|---|---|---|---|---|---|---|
| `/desk/contact` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-bank-evidence` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-cc-desktop-device` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-companion-delivery` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-companion-request` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-company` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-company-automation` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-company-choice` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-company-folder` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-company-note` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-erp-connection` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-erp-mapping` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-erp-refresh` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-erp-snapshot` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-express-account-facts` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-express-account-set` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-express-agent-credential` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-express-choice` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-express-choice-change` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-express-reach` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-express-readback` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-folder-intake-file` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-folder-intake-rule` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-folder-intake-run` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-invoice-category` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-name-memory` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-ocr-batch` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-ocr-correction` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-pos-layout` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-push-attempt` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-push-notification` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-push-record` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-push-writer-lock` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-recon-preset` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-recon-rerun` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-supplier-category-memory` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-supplier-posting-profile` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-wht-certificate` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-wht-choice` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/cowork-workspace-import` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/dashboard` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/dashboard/<记录>` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/event` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/file` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/file/<记录>` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/module-def` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/module-def/<记录>` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/note` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/notification-log` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/report` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/report/<记录>` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/todo` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/website` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/workspace` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/workspace/<记录>` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/desk/user` | ✓ | ✓ | ✓ | ✓ | ✓ |  |
| `/desk/user/<记录>` | ✓ | ✓ | ✓ | ✓ | ✓ |  |
| `/desk/cowork-bank-reconciliation` | ✓ |  | ✓ |  |  |  |
| `/desk/cowork-bank-reconciliation-row` | ✓ |  | ✓ |  |  |  |
| `/desk/cowork-buyer-memory` | ✓ | ✓ |  |  |  |  |
| `/desk/cowork-client` | ✓ | ✓ |  |  |  |  |
| `/desk/cowork-income-reconciliation` | ✓ |  | ✓ |  |  |  |
| `/desk/cowork-income-reconciliation-row` | ✓ |  | ✓ |  |  |  |
| `/desk/cowork-ocr-history` | ✓ | ✓ |  |  |  |  |
| `/desk/cowork-product` | ✓ | ✓ |  |  |  |  |
| `/desk/cowork-source-file` | ✓ | ✓ |  |  |  |  |
| `/desk/cowork-stocktake-entry` | ✓ |  |  | ✓ |  |  |
| `/desk/cowork-stocktake-item` | ✓ |  |  | ✓ |  |  |
| `/desk/cowork-stocktake-operation` | ✓ |  |  | ✓ |  |  |
| `/desk/cowork-stocktake-task` | ✓ |  |  | ✓ |  |  |
| `/desk/cowork-vat-check` | ✓ |  | ✓ |  |  |  |
| `/desk/cowork-work-activity` | ✓ |  |  |  | ✓ |  |
| `/desk/cowork-work-board` | ✓ |  |  |  | ✓ |  |
| `/desk/cowork-work-card` | ✓ |  |  |  | ✓ |  |
| `/desk/cowork-work-column` | ✓ |  |  |  | ✓ |  |
| `/desk/cowork-work-field` | ✓ |  |  |  | ✓ |  |
| `/desk/cowork-work-label` | ✓ |  |  |  | ✓ |  |
| `/desk/cowork-work-mirror` | ✓ |  |  |  | ✓ |  |
| `/desk/cowork-work-notification` | ✓ |  |  |  | ✓ |  |
| `/desk/cowork-work-rule` | ✓ |  |  |  | ✓ |  |
| `/desk/cowork-workspace` | ✓ | ✓ |  |  |  |  |
| `/desk/intake-record` | ✓ | ✓ |  |  |  |  |
| `/desk/cowork-tax-draft` | ✓ |  |  |  |  |  |
| `/desk/cowork-workspace/<记录>` | ✓ |  |  |  |  |  |

## dms：声明外能打开的地址 × 岗位（共 22 个地址）

| 地址 | admin | operator | none |
|---|---|---|---|
| `/desk/contact` | ✓ | ✓ | ✓ |
| `/desk/dashboard` | ✓ | ✓ | ✓ |
| `/desk/dashboard/<记录>` | ✓ | ✓ | ✓ |
| `/desk/event` | ✓ | ✓ | ✓ |
| `/desk/file` | ✓ | ✓ | ✓ |
| `/desk/file/<记录>` | ✓ | ✓ | ✓ |
| `/desk/note` | ✓ | ✓ | ✓ |
| `/desk/notification-log` | ✓ | ✓ | ✓ |
| `/desk/report` | ✓ | ✓ | ✓ |
| `/desk/report/<记录>` | ✓ | ✓ | ✓ |
| `/desk/todo` | ✓ | ✓ | ✓ |
| `/desk/user` | ✓ | ✓ | ✓ |
| `/desk/user/<记录>` | ✓ | ✓ | ✓ |
| `/desk/website` | ✓ | ✓ | ✓ |
| `/desk/workspace` | ✓ | ✓ | ✓ |
| `/desk/workspace/<记录>` | ✓ | ✓ | ✓ |
| `/desk/dms-customer-link` | ✓ | ✓ |  |
| `/desk/dms-id-card-intake` | ✓ | ✓ |  |
| `/desk/dms-operator` | ✓ | ✓ |  |
| `/desk/module-def` |  | ✓ | ✓ |
| `/desk/module-def/<记录>` |  | ✓ | ✓ |
| `/desk/pearnly-erp-endpoint` | ✓ |  |  |

## B 接口权限矩阵的发现（10-27）

工具：`scripts/regression/api_matrix.py`（回归实验室站点内，按岗位账号 + Guest 走框架正式入口 `frappe.handler.execute_cmd`，
必填参数给占位值；每次调用回滚、对外网络断开、不发后台任务；同时记下调用过程中有没有调到守卫函数）。
规模：DMS 113 个接口 × 4 个账号，ERP 312 × 9，Cowork 306 × 7，共 5,402 次调用，三产品合计约 2 分钟。
结果：`~/pearnly-devtools/regression-baseline/2026-10-27/api-*.jsonl`。

「只有 Desk User、没有任何业务角色的账号」调用后**一个守卫都没调到就正常返回**的接口（三产品合计，去重）：

| 接口 | 结论 |
|---|---|
| `pearnly_dms.records.push_log.api_list` | **缺陷**：`frappe.get_all` 不核权限、筛选条件原样取自请求，任何登录账号都能列出全部 DMS 推送记录 |
| `pearnly_core.endpoints.api_list_endpoints` | **缺陷**：任何登录账号都能拿到外部连接清单（地址、登录名、管理员登录名），`owner_user` 还能指定成别人 |
| `pearnly_dms.records.push_log.api_status` | 同类：按 `reference_doctype` / `reference_name` 查推送状态，没有守卫（只回状态，风险低，随第一条一起改） |
| `pearnly_core.sessions.my_sessions` / `pearnly_core.assistant.api.push_unsubscribe` / `pearnly_cowork.recon_center.api.active` / `pearnly_erp_portal.purchasing.api.api_recognise_progress` / `pearnly_erp_portal.sales.api.api_recognise_progress` | 只读写**本人**的数据（会话、推送订阅、本人的对账任务、按「本人 + 号」存的识别进度），不算缺陷 |
| `pearnly_core.manifest.describe` / `pearnly_core.ocr.api_status` / `pearnly_dms.ocr.ocr_id_card.api_status` / `pearnly_core.password.refuse_on_native_account` | 只回产品清单 / 服务是否可用 / 固定拒绝，不含业务数据 |

另有「测不出」的（占位参数在守卫之前就解析失败，DMS 12、ERP 61、Cowork 23 个）：要业务夹具（造真数据、带真参数）补，进 B 的第二步。

### 缺陷 1：`pearnly_dms.records.push_log.api_list`

- 位置：`apps/pearnly_dms/pearnly_dms/records/push_log.py` 的 `api_list(**kwargs)`（`@frappe.whitelist()`，没有 `allow_guest`）。
- 调用：`POST /api/method/pearnly_dms.records.push_log.api_list`，参数可空；`limit` 与任意字段筛选（如 `status=failed`、`endpoint=…`）原样进 `frappe.get_all`。
- 返回：推送记录逐条的 `name, status, endpoint, trigger, reference_doctype, reference_name, business_key, http_status, attempt, retry_count, error_msg, creation`。
  实验室复现拿到：`business_key` = `ID***`（身份证号作业务键）、`error_msg` = `DM***`（含客户手机号的报错原文）。
- 谁能调：DMS 站点任何登录账号（实验室实测：只有 Desk User 的账号、DMS 操作员账号都拿到）。
- 应有：按推送记录页的口径只给 DMS 管理员（或有「推送记录」功能的岗位），并用 `frappe.get_list`（核 DocType 权限）而不是 `get_all`；筛选字段收白名单。

### 缺陷 2：`pearnly_core.endpoints.api_list_endpoints`

- 位置：`apps/pearnly_core/pearnly_core/endpoints.py` 的 `api_list_endpoints(adapter=None, owner_user=None)` → `list_for`（`frappe.get_all`）。
- 调用：`POST /api/method/pearnly_core.endpoints.api_list_endpoints`，参数可空；`owner_user=<任意账号>` 可列出别人的私有连接。
- 返回：`name, label, adapter, owner_user, system_url, enabled, is_default, auto_push, last_used_at, last_status, success_count, failure_count, username, admin_username, has_credential, usable`（不含密码）。
  实验室复现拿到：`system_url` = `ht***`、`username` = `dm***`、`admin_username` = `dm***`。
- 谁能调：装了 pearnly_core 的三个产品站点上任何登录账号（DMS 实测；ERP、Cowork 的矩阵里同样无守卫正常返回）。
- 应有：连接页的口径（DMS「连接」页只给 DMS 管理员）；`owner_user` 只能是本人或管理员指定。

### 复现步骤（本机回归实验室）

```
docker compose -p pearnly-regression-lab -f infrastructure/compose/regression-lab.yml up -d --wait
docker exec -w /home/frappe/frappe-bench/sites pearnly-regression-lab-bench-1 \
  ../env/bin/python /workspace/scripts/regression/repro_unguarded.py dms.regression.localhost none@dms.regression
```

脚本以 Administrator 造一条假连接（`https://dms.example.test`，登录名 `dms_login_demo`）和一条推送记录，再用只有 Desk User 的账号调两个接口，打印拿到的条数、字段与打码后的值；跑完回滚。

### 按类查的建议

同类 = 「`@frappe.whitelist()` 接口里直接 `frappe.get_all` / `frappe.db.get_all` / `frappe.db.sql` 回数据、函数体里没有任何守卫」。矩阵已能自动找出「无守卫正常返回」的那一类，修完后矩阵里这类必须只剩「本人数据 / 公开信息」的登记项（做法同匿名接口登记表），新增无守卫接口 CI 失败。
