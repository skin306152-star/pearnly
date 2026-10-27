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

## 10-27 下午：工具修正与复跑

### 工具修正（上面上午的数字要按此重看）

- **快照盲区**：上午用「露出来的页面容器是不是旧的」判断「没打开」。我们自己的产品页会复用已有容器，所以大部分产品页被误记成没打开：ERP 352 次产品页访问里，只有 25 次拿到内容。
  改按框架每次换页都发的 `page-change` 计数判断；探针另记「露出来的是哪个页面容器 / 是不是原生列表或表单」（`view`），可选每页截图（`SMOKE_SHOTS`）。
  `leaks.py` 判「打开了」改看露出来的是不是框架原生视图；读被测源码的声明（`PEARNLY_REGRESSION_SRC`）；放行声明的框架路由和本人个人页。
- **ERP 岗位账号**：上午的 ERP 岗位账号用的是旧粗角色（`ERP Purchase` 等），不是团队管理真实发的 `ERP Menu <菜单>` 角色，上面 ERP 的逐岗位数字偏低。
  `lab_init.py` 已改成真实岗位；`lab_seed_erp.py` 走 `team.set_member_roles` 改岗位，只开 A 公司。
- **实验室 PDF**：容器里解析站点名（compose `extra_hosts`）；站点 `host_name` 带容器端口。
- 新夹具 / 检查：
  - `lab_seed_erp.py`：两家公司，各类单据走正式接口建，A 的单由本人开；
  - `print_check.py`：按岗位开打印页和 PDF；
  - `lab_check_p1.py`：多仓 P1 服务端；
  - `ui_steps.mjs`：页面里按步骤执行。

### 复核结论（都在本机回归实验室，镜像 fb1037ad）

| 对象 | 结论 | 要点 |
|---|---|---|
| 无守卫接口修复 b139d19d（已合 main 0e2a827e） | 通过 | 4 个无守卫入口已删；矩阵 5354 格只剩登记项；`sample-verify-foundation.py` 改调 `test_connection` 已在 main |
| native-buttons a162b649（已合 main f3ab95b5） | 通过 | 页头入口全部有登记；快捷键只剩 Ctrl+S；个人页只剩「保存」 |
| 打印第二批 bfb4b033 → ab823298（已合 main） | 通过 | 8 岗位 × 17 张单的打印矩阵；拒打 403；两页补「Print」声明；check-entries 改成按页核 |
| native-routes 9a7f343d → 957aaed2（= 6c8860b9） | 0 漏出，通过 | 17 岗位，ERP 904、Cowork 858、DMS 252 页次；声明外能打开的地址 0；没声明的 pearnly- 地址都回本岗位首页 |
| 多仓 P1 a3db0b6e | 2 条应修 | 销售 / 出库单头库位不按清单核，可从不存在的库位出货（实测 ZZ-99 变 -1）；`requeue_unmapped` 不按公司筛、只取全站最早 100 张，会漏推。另 4 条建议，见给开发窗口的结论 |

数据：`~/pearnly-devtools/regression-baseline/2026-10-27/{unguarded-fix,print,routes,routes2,p1}/`。

### 待办

- 过期的页面测试（`tests/test_stock_opening_ui.js`、`test-stock-entry.cjs`、`test-product-multi.cjs`、`test-product-scan.cjs`）：都是测试桩缺新全局（`frappe.boot.pearnly_express`、`frappe.model` 下的结构、`p.location_query`），起不来，没有一条业务断言在跑。
  这几页正是多仓 P1 要改的，等 P1 合入 main 后在 main 上修桩、接进 CI。实验室在库用例（`erp-pos-lab-inventory / purchasing / sales-verify`、`erp-data-flow-lab-verify`）同时逐个判断，修或删，并写明原因。
- Cowork 39 张表收紧之后，用接口矩阵复测 Cowork（get_list 当守卫的前提）。
- B 第二步：inconclusive 接口补夹具（现在有两家公司的业务夹具了）。

## 10-27 晚：部署前最终验收（结论：可部署）

范围：main 6c8860b9 跑全套。在它之上只做差异复核加受影响入口实测的有：5afc3755（LINE 菜单发布锁、开户单占位邮箱）、team-placeholder-email f9517a09、ocr-self-check-lines 29d7280c。
环境：本机回归实验室，镜像 fb1037ad。数据：`~/pearnly-devtools/regression-baseline/2026-10-27/predeploy/`。

| 项 | 结果 |
|---|---|
| ① 17 岗位页面快照（6c8860b9） | 声明外能打开的地址：三产品都是 0；没声明的 pearnly- 地址都回本岗位首页；页头入口 ERP 1657、Cowork 42、DMS 5，全部有登记、有码；全局快捷键只剩 Ctrl+S；个人页只有「保存」。ERP 那一路中途网页进程被实验室 3G 内存上限杀掉（`oom_kill 1`，实验室容量问题），重启后补跑 tasks / none，落地页和上一轮逐个一致 |
| ② 三产品接口权限矩阵（6c8860b9） | 5354 格；无守卫正常返回只剩登记的本人 / 公开项，外加无害的 `refuse_on_native_account`；`locations.api_location_options` 是追踪器漏认 `guard.has` 造成的误报（已补进追踪器） |
| ③ 角色卫生（生产只读，8 个业务站点，含新开的 polymer） | 全部通过：除 Administrator 外没人带 System Manager，业务账号都有 Desk User；8 个站点「初始设置」都已完成（f9517a09 的设置未完成放行在生产上不会触发） |
| ④ 关键业务流（6c8860b9） | 正式接口走一遍：采购入库、采购订单、报价、销售、作废、收银开班 / 卖 / 退 / 交班（现金差 0）、盘点开 / 点 / 过账，状态都对；打印矩阵 128 格与已通过那轮逐格一致；多仓 P0 四条断言都过 |
| 5afc3755 | 菜单发布：ERP、Cowork 85 次请求，DMS 26 次，全部同一个连接，锁都是 900 秒；绑定码发码 → 用码 → 绑上 → 解绑三产品都过；团队新建成员、普通岗位新建被拒；LINE webhook 模拟三产品 × 管理员 / 员工 6 组全过；`test_line_menu_publish_lock` 2/2、`test_cloud_onboarding` 22/22 |
| team-placeholder-email f9517a09 | ERP / Cowork 各 5 条：留空生成 `<登录名>.<前缀>@pearnly.com`、手填转小写、两种非法邮箱被拒、撞名被拒；`test_team_placeholder_email` 3/3；`test-native-actions.cjs` 通过 |
| ocr-self-check 29d7280c | 代码复核：无明细不升级；price_mode 只在一边对上且税额大于 0 时由数字定；combine 只替换被点名字段和 Haiku 没读出的字段，其余情况整份用 Opus 的。单元：consistency 16/16、rules 23/23、products 通过。在甲骨文用 549 份录制读法离线重放（不付费），升级率、每页成本、最终仍不自洽，与记录逐项一致 |

发现（不是这批引入的，不阻塞）：**采购订单会入库**。采购表单对所有类型都发 `update_stock: 1`，服务端默认也是入库，所以选「采购订单」提交就把货记进库存。这段代码是 09-22 的 28f13cf3，线上一直如此。实验室里三张采购订单各入了 3 件。要用户定口径。

没测到：真 LINE 手机端（菜单真建出、相机 LIFF）、控制面站点、真 Express、付费识别（部署后在 testerp 上传 LI-00029 核一次）、窄屏。
