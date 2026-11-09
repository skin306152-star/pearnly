# ERP 侧栏 / 路由放行的角色与接口判据一致（11-09）

- 主会话 local_e395dace 派；分支 `pearnly/erp-sidebar-api-parity`，基线 `73974f2c8`（= 当时 origin/main）。
- 短评估与只读核查：共享笔记「ERP侧栏与接口权限口径-短评估-待审-11-09.md」（主会话 11-09 审定修法 A）。

## 现象与原因
testerp 的 e2e 账号 e2eerp（ERP Admin + 菜单岗位「采购」「公司」）侧栏显示库存、收发存等页，点进去「ไม่ได้รับอนุญาต / ไม่สามารถโหลดหน้านี้ได้」。
`hooks.py` 给每个菜单页的页面权限都加了 ERP Admin（侧栏、路由按 `navigation._can_open_page` 判），接口按 `access/rules.py`（「ERP 管理员不含功能权限」）判，两边口径不一致。

## 只读核查（11-09 17:26，生产 erp web 容器逐站点只读，1 次 SSH）
正式租户（gize、polymer、mrerp）受影响账号 0；testerp 只有 e2eerp（14 页）。mrerp / testerp 报的「任务管理」是误报（任务接口 `work/access.allowed` 认管理员）。

## 修法
- `access/menu.py` 新增 `page_roles(key)`：本菜单岗位 + 旧功能角色；接口本身认管理员的业务页（`ADMIN_PAGES = ("tasks",)`）再加 ERP Admin。hooks 改用它。首页、个人、团队、公司页的旧功能角色本来就是 ERP Admin，照旧。
- 同类再查出一处：公司页「写」（company.manage）认管理员、「读」（company.read）不认——只拿 ERP Admin 的账号能改公司却打不开公司页。`rules.required_roles("company.read")` 加上能改公司的角色（能改就能读）。
- 不改：团队页把锁定的管理员显示成「全部岗位」（正式老板开户时确实全有功能角色）。

## 测试
`tests/test_erp_page_roles_match_api.py`（进 Development checks）：逐页断言「页面放行的每个角色单拿着都过得了该页接口的读能力」、管理员只在接口认管理员的页放行、盘点岗进物料调整走 count.write、hooks 用 `page_roles`；改前 3 条失败、公司页那条在补 company.read 前失败。

## 影响清单（check-impact）
命中 shared.navigation（manual）等，实测结果见下（合入前补全）。
