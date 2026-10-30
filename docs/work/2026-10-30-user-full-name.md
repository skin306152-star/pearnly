# 员工姓名（岳父 10-28 清单第 1 条，2026-10-30）

评估与批复见 `docs/work/2026-10-30-father-three-items-评估.md` 第二项（主会话 10-30 批：名必填、姓可空；存量不动；单据显示当前名字）。

## 改了什么

- **共用层**（`pearnly_core`）：
  - `account/names.py`：`display_name(user)`（原生全名，没有回落账号）与 `clean(first, last)`（名必填、姓可空），三产品显示人名都走它；
  - `account/profile.py`：登录时只把身份服务的用户名投到 `username`，**不再改写 first_name、清空姓**（原来每次登录都覆盖）；
  - `account/layout.py`：拿掉「first_name 改叫用户名、只读」两条属性设定（迁移时删），`username` 只读显示；公司一栏挪到姓后面；
  - `public/desk/user.js`：我的资料留用户名、名、姓（名 / 姓本人可改）；
  - `account/member_client.py` → 控制面 `member_api / member_service / member_provider`：建员工时把名 / 姓送到身份服务（firstName / lastName；没给沿用用户名，老站点不受影响）。
- **ERP**：团队页新建 / 编辑带名（必填）、姓；名单「姓名」「用户名」两栏、搜索也认姓名（`team/service.py`、`team/api.py`、`public/desk/team.js`）；收银员姓名（汇总屏「Sold by」、小票导出）改走 `display_name`（`cashier/output.py`、`cashier/sales.py`）。
- **Cowork**：团队页同 ERP；LINE 绑定回执写姓名（`line/handlers.py`）。
- **自动跟随、不用改**：小票打印收银员、班次、盘点人、任务看板、ERP LINE 绑定回执、CC 网页问候（都读原生全名）；DMS 操作员花名册（自有姓名）；CC 桌面版（不显示人名）。
- **存量**：不动——老账号的 first_name 还是开户时写的用户名，显示不变，各人填了姓名才变。
- **文案**：新增 3 条（共用层术语表）：「Enter the first name」报错、「First Name」「Last Name」标签；「Full Name」「Username」沿用已有词条。

## 共用件连带回归（`scripts/check-impact.py`）

命中 `desk-user`（2 个入口）、`login-session`（5 个入口）；三个新调用方（ERP 收银两处、Cowork LINE 回执）只用 `display_name`，登记进 login-session 的 not_entries。

| 入口 | 结果 |
| --- | --- |
| erp.user-profile / cowork.user-profile（我的资料） | 见下「验证」流程用例 `erp.team.full-name` / `cowork.team.full-name` 的「我的资料」格 |
| erp.page.team / cowork.page.team | 同上（团队页新建弹窗、名单） |
| erp.page.cashier | 待填 |
| shared.team.members（建成员同步身份服务） | 离线：控制面收到名 / 姓并交给身份服务（`test_member_service`）、站点转发（`team/test_identity`）；真身份服务：没测到（实验栈没有 Keycloak，要测试环境） |
| shared.login（OIDC 登录） | 离线：登录只投用户名、不碰姓名（`team/test_identity`）；真登录：没测到（同上） |
| control.issue-and-entry、shared.shell-gate、shared.line.login | 没测到（manual，要测试环境 / 真机；本次没改开户、开通闸、LINE 登录代码） |
| cowork.line.binding（LINE 绑定回执姓名） | 没测到（真 LINE；回执只换了显示名函数） |

## 验证

- 离线（生产镜像）：`pearnly_core.account.test_names`、`pearnly_erp_portal.team.test_identity`、`pearnly_control.account.test_member_service`、`pearnly_core.account.test_identity_callback` 通过。
- 流程用例：待填。
