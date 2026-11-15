# 管理员拥有本租户已开通的全部功能（2026-11-15）

分支 `pearnly/admin-all-features`，基线 origin/main `19d9d0d3a`。主会话标「可直接做」。

## 现象与根因

团队管理把管理员画成全勾、不可改，但权限判据看角色：开户时发齐当时已有的功能角色，之后新增的功能只补了功能码、
没给管理员补角色，于是「画着勾却没有」。11-13 线上 7 个启用的管理员里 6 个没有收款员角色（另一窗口生产只读核对，
见共享笔记「管理员功能自由勾选-评估-11-15.md」）；任务管理 mrerp 管理员同样缺角色，只因另有单项特判才能用。
收款是 11-14 用 `ADMIN_FEATURE_CAPABILITIES` 单项补上的，任务管理靠 `menu.ADMIN_PAGES` + `work.access` 的管理员特判。

权限有三层，都按角色判：能力（`guard.has`，网页接口、LINE 菜单版式、CC 工具都走它）、侧栏 / 路由（`pearnly_navigation`
的 `any_role`）、原生表权限（列表取数走 `frappe.get_list`）。

## 修法（一处判据，三层同口径）

- `access/rules.py`：新增 `admin_held(roles, features)`——持有 ERP Admin 就视为持有每个已开通功能的功能角色；
  删掉 `ADMIN_FEATURE_CAPABILITIES` 与 `required_roles` 里的收款单项。
- `access/guard.py::has`：用 `admin_held` 补齐后再判（未开通功能的角色照旧被去掉）。
- `access/menu.py::page_roles`：每个功能页都放行 ERP Admin（没开通的功能页侧栏按功能码本来就不出）；删掉 `ADMIN_PAGES`。
- `work/access.py::allowed`：去掉管理员特判，只走 `guard.has("tasks.use")`（看板管理权仍按 `administrator`）。
- `access/setup.py::sync_roles`（迁移收尾）：已开通功能的每张表，ERP Admin 没有权限行的补一条（同那一页菜单岗位的读 / 写）；
  只补缺的、不改已有行（开户发的公司资料可写等原样保留）。
- 员工不受影响：`admin_held` 只对持有 ERP Admin 的账号补。
- Cowork、DMS 同类核对：Cowork 各域判据本来就是「功能已开通 + (岗位角色 或 Cowork Admin)」，侧栏同口径，无此不一致；
  DMS 按花名册判，团队页不画管理员全勾，无此问题。都不改。
- testerp 的 e2e 账号（ERP Admin + 两个菜单岗位）修后是全功能管理员；`scripts/e2e/` 场景没有依赖「只有部分功能」的断言，
  只在 `provision.py` 说明里写明。

## 验证

- 纯逻辑：`tests/test_erp_page_roles_match_api.py`（每页放行的角色过得了接口；管理员每个已开通功能的全部能力为真、未开通为假）。
- 镜像内：`tests/test_erp_admin_all_features.py`（`guard.has` 逐能力；未开通为假；员工不多拿；任务管理不再要特判）。
- 实验栈实测（见下方结果）：建只持有 ERP Admin + Desk User 的账号，跑迁移收尾后逐项核对能力、侧栏可见页、原生读权限。

### 实验栈实测结果（本机 `pearnly-admin-lab`，挂本工作树源码）

临时账号只给 ERP Admin + Desk User（公司归属照实验室管理员），跑一次迁移收尾 `sync_roles` 后逐项核对，用完删除：

| 站点 | 代码 | 被拒能力 | 侧栏缺页 | 收款页 | 原生列表不可读 |
|---|---|---|---|---|---|
| erp.regression | origin/main | 31 | 16 | 有（单项特判） | — |
| erp-new / erp-old | 本分支 | 0 | 0 | 有 | 0 |
| erp-new | origin/main（对照） | 31 | 16 | 有 | 0（表权限行已由上一次本分支迁移补上） |

受影响流程（`--flow`，new / old × 电脑 / 手机）：`erp.work.board-settings`、`erp.collections.refused`、`erp.web.collection`、`erp.link.ledger-first` 结果见下。

### 共用件连带回归（`check-impact.py`）

| 入口 | 结果 |
|---|---|
| `shared.shell-gate` | 通过：没开通的功能页侧栏不出（navigation 按功能码筛，本次未改）；员工权限不变（`admin_held` 只对 ERP Admin 补），`erp.collections.refused` 没收款权限的人仍被拒 |
| `erp.provisioning` | 没测到（开户要控制面真开一户）：本次不改开户发角色；迁移收尾补管理员表权限只补缺、不改已有行，实验栈三站点迁移通过 |
| erp-old | 本分支（闸门后改为循环外一次取回） | 删掉管理员在 ERP Supplier、ERP Stock Balance 的权限行后跑 `sync_roles`：两张补回（写 / 只读同菜单岗位），已有的 ERP Company 可写、Purchase Document 不动 |

受影响流程结果：`erp.work.board-settings`、`erp.collections.refused`、`erp.web.collection`、`erp.link.ledger-first` 各 new / old × 电脑 / 手机全部通过。

### 迁移收尾给管理员补的原生表权限（只补 ERP Admin 这一个角色，不动别的角色；只补管理员没有任何权限行的表）

按已开通功能取菜单目录（`access/menu.py::CATALOG`）里那个功能各页的表；业务页另加公共依赖（物料、物料分类、公司、仓库、收银终端），
任务管理只加公司（同菜单岗位口径）。「写」= read、select、write、create、report、export；「读」= read、select。
另：盘点明细（ERP Stock Count Item）permlevel 1 只读，同盘点岗位。已有行一律不改（开户发的公司资料可写等保留）。

| DocType | 权限 | 哪些功能开通时补 |
|---|---|---|
| ERP Company | 读 | 任一功能 |
| ERP Product / ERP Product Category / ERP Warehouse | 写 | 任一业务功能（物料页、库存页可写，依赖口径为读，取写） |
| ERP POS Terminal | 读 | 任一业务功能 |
| ERP Customer | 写 | clients、sales、cashier |
| ERP LINE Intake | 写 | purchase、sales |
| Purchase Document、ERP Supplier、ERP Purchase Attachment、ERP Purchase Payment | 写 | purchase |
| ERP Purchase Category、ERP Purchase Settings | 读 | purchase |
| ERP Sales Document、ERP Sales Attachment | 写 | sales |
| ERP Sale、ERP Shift | 写 | cashier |
| ERP Stock Entry | 写 | warehouses |
| ERP Stock Balance | 读 | warehouses |
| ERP Stock Opening | 写 | stockcard |
| ERP Stock Movement | 读 | stockcard |
| ERP Stock Count、ERP Stock Count Item、ERP Stock Count Entry | 写 | stocktake |
| ERP Serial No、ERP Serial Movement | 读 | 物料 / 库存 / 采购 / 销售 / 收银（本来就单独给管理员，见 sync_roles 按台记录段） |
| ERP Work Board / Card / Label / Activity / Notification / Column / Field / Mirror / Rule | 读 | tasks（写只走专用接口） |
| ERP Collection | 读 + report + export | collection（原有：sync_roles 收款段单独给管理员） |

守护：`tests/test_erp_page_roles_match_api.py::test_every_table_a_feature_declares_is_readable_by_the_admin_once_opened`——
`features.json` 里每个功能声明的表（子表除外）都必须在上表范围内（同一岗位的功能一起看），新增功能漏了就红。
