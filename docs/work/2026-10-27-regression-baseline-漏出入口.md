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
