# 员工姓名（岳父 10-28 清单第 1 条，2026-10-30 起；10-31 全覆盖）

评估与批复见 `docs/work/2026-10-30-father-three-items-评估.md` 第二项（主会话 10-30 批：名必填、姓可空；存量不动；单据显示当前名字）。
10-31 用户追加（主会话转原话）：「现在显示用户名的地方，改完后必须全部覆盖，包括 CC、LINE 那边，也是一样，要覆盖完整」——
全仓清点人名显示点、一律走共用显示名、加检查拦新代码、逐入口实测；没全覆盖不合入。

## 口径

- 一个人显示成什么只由 `pearnly_core/account/names.py` 决定：`display_name(user)` / `display_names(users)`（一批一条查询）。
  有姓名用姓名（原生名 + 姓拼出的全名），没填过的老账号是开户时的用户名（存量不动，本人填了才变）。
- 页面不自己查名字（`frappe.user_info(...).fullname`、会话快照）：接口给好显示名那一格，页面只显示它。
- 登录名（用户名）只在要它的地方显示：团队页「用户名」一栏、我的资料「用户名」格（只读）、改密码、开户单、欢迎邮件。

## 改了什么

### 第一批（10-30，原窗口）
- 共用层：`names.py`（`display_name`、`clean`：名必填、姓可空）；登录只把身份服务的用户名投到 `username`，不再改写名、清空姓；
  我的资料名 / 姓可改、用户名只读；建员工把名 / 姓送到身份服务（站点 → 控制面 → Keycloak）。
- ERP / Cowork 团队页新建 / 编辑带名姓，名单「姓名」「用户名」两栏；收银员（汇总屏、交班导出）；Cowork LINE 绑定回执。
- 文案新增 3 条（「Enter the first name」「First Name」「Last Name」）。

### 第二批（10-31，全覆盖）
全仓清点见共享笔记 `erp-dev-2-姓名显示点清单.md`（主会话的笔记目录）；下表是「现在 → 改后」：

| 产品 | 入口 | 原来显示 | 改后 |
| --- | --- | --- | --- |
| ERP | 盘点详情记录表「盘点人」、盘点导出记录页 / 照片说明、分区盘点人、录入弹窗「已有人记过」 | 账号（邮箱）；分区靠浏览器缓存查名，查不到是邮箱 | 接口 `counted_by_name`（`inventory/count.py`） |
| ERP | 热敏小票、交班单「พนักงาน」 | 读 `full_name` | `display_name`（`printing/context.py`） |
| ERP | 交班记录列表与详情「收银员」 | 浏览器缓存查名，查不到是邮箱 | 接口 `cashier_name`（`cashier/shifts.py`、`pos-shifts.js`） |
| ERP / Cowork | 任务看板成员、负责人、@ 名单、筛选 | 读 `full_name` | `display_names`（`work/boards.py`） |
| ERP / Cowork | 看板活动、评论作者、「分派了谁」 | 成员名单反查，已不在看板的人是邮箱 | 接口给每条活动的 `names`（`work/service.py`、`work.js`） |
| ERP / Cowork | 铃铛提醒、在线弹出「谁…」 | `get_fullname` | `display_name`（`work/notify.py`） |
| ERP | LINE 绑定回执「已绑定到 X」 | 读 `full_name` | `display_name`（`integrations/handlers.py`） |
| ERP / Cowork | 团队页编辑弹窗标题 | 邮箱 | 姓名（`team.js`） |
| Cowork | 盘点记录 / 导出「盘点人 / 修改人」 | SQL 拼 `full_name` | `display_names`（`stocktake/service.py`）；页面去掉自己查名 |
| Cowork | 对账预设「由谁设」 | `get_fullname` | `display_name`（`reconciliation/preset_store.py`） |
| Cowork / CC | CC 设置电脑「成员名单」 | `full_name` | `display_names`（`connections/access.py`） |
| Cowork / CC | CC 月结台账「谁发起回读」、回读状态 | 私有 `_fullname`；状态只有账号 | `display_name`；状态补 `requested_by_name`（`readback/service.py`） |
| Cowork / CC | CC 公司卡选择项「谁设的」 | **账号（邮箱）** | 接口 `set_by_name`（`companies/choices.py`、`rules.py`、`company-cards.tsx`） |
| Cowork / CC | CC 对话里的公司工具（记住的选择、规则） | 给模型账号 | 只给 `set_by_name`（`assistant/company_tools.py`） |
| Cowork / CC | CC 对话里的看板工具（负责人） | 给模型邮箱 | 给姓名（`assistant/tools.py`） |
| 共用 / CC | CC 问候「早上好，X」、头像缩写、侧栏用户按钮、手机头像标签；CC 对话 whoami | `get_fullname` | `display_name`（`shell.py`、`assistant/tools.py`） |
| DMS | 推送记录「操作员」、LINE 绑定回执 / 帮助里的名字 | 花名册名，没有再读 `full_name`（没有是空） | 花名册名优先，没有用 `display_name`（`query/records.py`、`line/handlers.py`） |

保留 / 不涉及：团队页「用户名」一栏、我的资料用户名格、改密码、开户单、欢迎邮件（登录名本身）；DMS 花名册（自有姓名）；
CC 桌面版（不显示人名，内嵌网页随 CC 网页）；客户证件上的姓名（DMS 身份证、客户档，不是账号）；官方 POS 发小票邮件的发件人
（框架自己取）；框架原生的时间线 / 评论 / Assigned To 头像（读原生全名，自动跟随）。

### 防回退检查
`scripts/check-person-names.py`（分支 CI 同名步骤，测试 `tests/test_check_person_names.py`）：新代码在服务端读 `full_name`、调
`get_fullname` / `user_info`，或在页面查 `frappe.user_info(...).fullname`、`user_fullname`，就拦下；确实不是显示人名的写
`person-name: 原因`。存量基线 `scripts/person-names-baseline.json` 只剩 10 处、6 个文件，都是「建号时写初始姓名 / 客户证件姓名 /
框架发件人」，只许减少。

## 验证（基线 `origin/main` `0836f929`）

- 离线（生产镜像 `e9aa959b`）：候选 offline 车道**全部通过**（含 `pearnly_core.account.test_names`、`team.test_identity`、
  `test_member_service`、`test_identity_callback`）；受影响测试 `test_cowork_companies`（公司卡 set_by_name）、
  `test_cowork_assistant_tools`、`test_cowork_readback`、`test_cowork_connection_read_locks`、`test_dms_recording_fixes`、
  `test_assistant`、`test_assistant_ui`、`test_erp_large_lists`、`test_check_impact`、`test_check_person_names` 通过；本地闸门同 CI。
- 流程（本机 verify-lab，`--changed`，16 条 57 格）：50 格通过；`erp.stock-entry.scan` 4 格是已知测试脚本问题（多仓分支
  `36d94714` 修，见共享「已知问题与结论」），与本分支无关；`cc.person-names` 电脑 2 格是我的判定太严（按钮里显示的就是
  「สมชาย… ใจดี · ERP」），`erp.team.full-name` 新开户电脑 1 格是收尾删测试员工撞锁——两处流程脚本已改后单独重跑：
  `cc.person-names` 4/4 通过（问候带名、电脑侧栏按钮 / 手机头像标签是全名）；`erp.team.full-name` 3/4 通过、新开户电脑那格页面步骤
  超时 900 秒（本机同时跑着别的任务），单独再跑通过。
- 实验栈实测（不出网、不碰真实绑定）：
  - LINE：模拟 LINE 号走真实绑定处理器、回复截住——ERP 两套、Cowork 两套回执都是「已绑定到 สมหญิง ทดสอบ」（姓名），收尾解绑、还原姓名；
  - 打印：小票、交班单取数的收银员是姓名；交班记录接口 `cashier_name`、盘点记录接口 `counted_by_name` 是姓名
    （老账号是用户名「admin」「stock」，不再是邮箱）。

## 共用件连带回归（`scripts/check-impact.py`，命中 desk-user、erp-printing、erp-count-code、login-session、assistant，67 个入口）

| 入口 | 结果 |
| --- | --- |
| erp.user-profile / cowork.user-profile | 通过（流程 `erp.team.full-name` / `cowork.team.full-name` 的「我的资料」格：名、姓可改，用户名只读） |
| erp.page.team / cowork.page.team | 通过（同上：新建弹窗名必填、名单显示姓名、编辑改名） |
| erp.page.cashier / erp.cashier.search | 通过（流程 `erp.cashier.first-sale`） |
| erp.page.pos-shifts / erp.print.pos-shift / erp.print.pos-receipt | 交班记录：通过（流程 `erp.pos.shift`）；打印取数收银员姓名：通过（实验栈取数）；热敏纸实打：没测到（要打印机） |
| erp.page.tasks / cowork.page.work | 通过（流程 `erp.work.board` / `cowork.work.board`） |
| erp.page.stock-count / erp.stock-count.code / erp.stock-count.new-product / erp.stock-count.upload | 通过（流程 `erp.stocktake.scan`、`erp.stocktake.new-product`、`erp.stock.excel-units`） |
| cowork.page.stocktake / cowork.stocktake.upload | 盘点列表：通过（流程 `cowork.stocktake.list`）；上传：没测到（本次只改盘点人显示） |
| erp.line.binding / cowork.line.binding | 通过（实验栈模拟绑定，回执姓名）；真机：没测到（真 LINE 要测试机 + 三星） |
| dms.line.binding | 没测到（实验栈没起 DMS 站点；离线 `test_dms_recording_fixes` 通过） |
| cc.assistant / shared.assistant（CC 问候、头像） | 通过（流程 `cc.person-names` 两套数据 × 电脑 / 手机：问候带「名」、侧栏用户按钮 / 手机头像标签是全名） |
| cowork.assistant / cowork.companies / cowork.cc-chat.push（CC 公司卡与工具） | 公司卡界面：没测到（实验栈 Cowork 数据没有公司，公司要先接 Express 账套）；接口与工具给姓名：离线通过 |
| cc.settings.connection | 没测到（电脑成员名单要接了电脑的 Cowork；离线 `test_cowork_connection_read_locks` 通过） |
| shared.team.members / shared.login | 离线通过（名姓送身份服务、登录不碰姓名）；真 Keycloak 建号 / 登录：没测到（实验栈没有身份服务，要测试环境） |
| erp.exports / cowork.exports | 盘点导出盘点人：通过（实验栈）；其余导出未改，没测到 |
| 其余（打印采购 / 销售 / 出入库 / 盘点单、相机、扫码枪、附件、LINE 聊天与模拟 webhook、Express 推送、识别、导航、开通闸、开户、浏览器文案、记录按公司、按台弹窗、收发存排序、CC 桌面版桥） | 没测到（这些入口经过同一文件的别的路径，本次只换了人名来源；自动冒烟在测试机实验栈，动测试机先问主会话） |

## 限制
- 存量账号显示用户名（姓名没填）——口径如此；本人在「我的资料」填了才变。
- 公司卡界面、真 LINE、真身份服务登录 / 建号、热敏打印没在本机实测，见上表；业务验收（岳父看团队列表与单据人名）待测试窗口。

## 补跑（10-31 恢复本机流程，基线 main 最新）

原「待转 GitHub 流程」三条在变基后的分支代码上补跑：
- `erp.exports` 4/4 通过。本机最初 0/4 是实验栈没接计费控制面（首页接口报「Billing is not wired up」，按钮不出），main 上同样不通过；
  跑 `scripts/regression/lab_billing.py` 接上后通过——环境问题，结论在共享「已知问题与结论」。
- `cowork.exports` 4/4 通过（Cowork 盘点导出的盘点人 / 修改人为姓名）。
- `erp.stock-entry.scan` 3/4 通过，新开户 / 电脑一格后几项「page lost」，单独重跑通过（偶发）。
其余 `--changed` 流程在变基前全过；变基只带进 main 的改动（流程登记表合并），按主会话口径不重跑，以 CI 为准。
