# 产品报表跟侧栏同一口径放行（2026-11-17）

分支 `pearnly/report-roles-follow-menu`，基线 origin/main `847fc020c`。来源：MR.ERP 窗口 11-16 实验栈手工入口——盘点差异报表
`/desk/query-report/ERP Stock Count Variance` 对测试管理员账号提示「没有权限访问报表」（共享笔记「MR.ERP-实验栈结果-11-16.md」）。

## 原因
报表登记了 ERP Stock、ERP Menu stock-count、System Manager，没有 ERP Admin。11-15 起产品管理员靠 ERP Admin 拥有本租户已开通的全部
功能，侧栏 / 路由按 `access.menu.page_roles`（本菜单岗位 + 旧岗位 + ERP Admin）放行；只有 ERP Admin、没有旧 ERP Stock 的账号与报表
角色无交集，Frappe 拒。属「管理员自动拥有全部已开通功能」的漏网。归属：可售卖功能 stocktake（盘点），页面 pearnly-stock-count。

## 按类
三个产品的自有 Report 只有这一份；Page 一律 Desk User、靠 `pearnly_navigation` 按 `page_roles` 放行，ERP 21 页都含 ERP Admin，
功能码与 features.json 对得上；Cowork 8 页、DMS 4 页放行都有登记。需修的只有这一处。

## 改动
- 报表角色补 ERP Admin（= `page_roles("stock-count")` + System Manager）。报表 JSON 没有 modified，每次迁移重导，部署即生效。
- `tests/test_erp_report_menu_roles.py`（候选检查里跑）：每张产品报表按 `ref_doctype` 归到 `menu.CATALOG` 里写这张表的菜单（没有写的
  看读的），角色必须覆盖那个菜单的 `page_roles`；归不到菜单的无主报表、角色不全都失败。修复前这条在镜像里失败（缺 ERP Admin）。

## 部署后逐租户核对（登录后权限，入口探针测不到）
- 每个正式 ERP 租户：只有管理员角色的账号打开「盘点」→ 盘点差异报表，能打开、无「没有权限」。
