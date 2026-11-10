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
本机实验栈 pearnly-cardparity-lab 挂本分支源码（11-10，变基到 c9e9eedf9 后 f60ecf0dd），线上同版镜像。

shared.navigation 实测（erp-new / erp-old 两个站点，逐个启用账号以本人身份算 `visible_pages` / `sidebar_pages` / `user_menu` / `entry_route`，
同一进程里把页面权限换回改前写法再算一遍对比；另建一个只有 ERP Admin + 采购 + 公司岗位的临时账号，同 testerp 的 e2eerp，测完删掉）：

| 账号 | 改前 → 改后 |
|---|---|
| Administrator、admin（管理员 + 全部功能角色）、stock / purchase / sales / cashier / products / tasks / none 员工 | 两站点各 9 个：可见页、侧栏、左下角菜单、进入页完全相同 |
| 临时账号（ERP Admin + 采购 + 公司岗位） | 少了 15 个点进去会被接口拒的业务页（库存、收发存、盘点、物料、销售、收银等）；侧栏剩首页、采购、任务管理；左下角菜单（公司、团队）和进入页不变 |

浏览器流程（`run.py --flow`，新旧数据 × 电脑 / 手机）16/16：erp.field-help（侧栏 / 导航声明）、erp.company.actions（公司页）、erp.stocktake.adjust（盘点岗进物料调整）、erp.stock-card。`--changed` 匹配不到流程（hooks / rules 没有流程登记 covers），按改动挑的这四条。

影响清单（`check-impact.py --base origin/main`：没有命中共用件，只命中 hooks.py / rules.py 的调用方入口 10 个）：

| 入口 | 结果 | 依据 / 原因 |
|---|---|---|
| shared.navigation | 通过 | 上表逐账号对比 + 流程 erp.field-help；只拿管理员的账号少了点不进的页，其余账号与 main 相同 |
| erp.cc.chat-push | 通过（离线） | Development checks 与本地闸门全过；本分支只放宽 company.read（能改公司就能读），公司范围仍按 `allowed_companies` |
| erp.cc.stocktake | 通过（离线） | 同上；盘点能力判据未改 |
| erp.masters.scope | 没测到（人工） | 主档隔离判据（公司范围）未改；本分支只动页面放行角色与 company.read |
| erp.provisioning | 没测到（控制面开户） | 开户写的岗位不变；页面放行按岗位，正式老板开户全有功能角色（11-09 只读核查：正式租户受影响账号 0） |
| erp.cc.write | 没测到（测试机 Express） | 写 Express 的判据未改 |
| erp.cc.connect | 没测到（测试机 CC） | 配对 / 心跳不走页面权限 |
| erp.line.chat | 没测到（三星真机） | LINE 不走 Desk 页面权限 |
| erp.execution | 没测到（付费识别） | 识别不走页面权限；识别预算规矩下不跑 |
| cc.operator.thread | 没测到（测试机 CC） | 代录对话不走页面权限 |
