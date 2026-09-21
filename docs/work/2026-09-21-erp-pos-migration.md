# 工作包：ERP 与 POS 迁移到新平台

## 目标和范围

- 目标 / 完成条件：把用户确认保留的 14 项 ERP 功能迁到新平台 `pearnly_erp_portal`，逐项具备实现与本地验证证据；租户隔离与员工权限隔离在真实测试 Site 上通过（两租户、老板与收银员两类账号）；一笔 POS 销售只落一笔销售记录并只产生一次库存影响。业务行为以旧项目当时承接生产流量的版本为基准。
- 本次不做：不迁旧 POS 收银后端，不迁会计、报税、工单、AI 管家、Daily、工资、邮件接入（见 `docs/p0/04-scope-and-drop-list.md` 不迁移清单）；不部署、不推送、不合回主线。
- 本次范围状态：进行中
- 收尾状态：施工中，尚未收尾
- 分支 / 基线 SHA / 工作树 / 集成目标：`codex/erp-pos-migration`，基线 `ac33e0c7362915ae314295cb26f7748cb5589de0`，工作树 `/Users/skin/.codex/worktrees/erp-pos-migration/pearnly`，集成目标 `main`
- 合并 / 推送 / 远端回读状态：均未执行（本任务为本地任务，未获推送或部署授权）
- 负责人 / 关联任务或 PR：本窗口；并行任务为另一窗口的 `codex/cowork-backend-migration`（不相干）
- 允许修改的路径 / 数据和运行资源归属：本工作树内 `apps/pearnly_erp_portal`，必要时 `apps/pearnly_core`、`infrastructure/`、`scripts/`、`docs/work/`；不修改 Cowork 工作树，不重启或接管 `pearnly-sample` 与 `pearnly-cowork-backend-lab`
- 必要外部操作及当前授权：无。本批只做只读核对与文档

## 迁移映射

- 首次线上核验：2026-09-21 17:30 Asia/Bangkok（只读 `gcloud run`）。
  - GCP 项目 `pearnly` / `asia-southeast1`；`pearnly-web` 100% 流量 → `pearnly-web-ec2e5c957e6b-s3`
  - `pearnly-worker` 100% 流量 → `pearnly-worker-ec2e5c957e6b-s3`
  - 两者镜像 digest 均为 `sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226`
- 完整源码 SHA / 映射依据 / 只读快照：`ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。映射依据为固定提交 `501589f0d7426fff2c24584d55e2732e68961063` 的 `docs/deployment/MIGRATION_STATUS.md`（其中记录同一 revision / digest / 完整 SHA）。本批只看读该 SHA 的对象：在来源仓 `/Users/skin/Developer/Pearnly/pearnly-app` 用 `git show ec2e5c957e6b:<path>` 读取，未 reset、pull 或改动来源工作树（该目录仍有其他任务的未提交内容与旧 HEAD）。
- 相关运行配置：本次未核对旧线上开关、后台任务与前端产物归属 → 待补，未完成前不得标记任一功能“与旧线上一致”。
- 交付前线上复查：尚未执行（任务未到交付）。

### 截图 14 项与旧入口对照（首版）

旧 ERP 入口由 `src/home/nav-presets.ts` 的 `ERP_PRESET` 决定显隐，标签在 `src/home/app-shell-sidebar-html.ts` 与 `src/home/clients-navigation.ts`。

| 截图项目 | 旧入口 route | 旧后端（初步定位） | 新落点 | 状态 |
| --- | --- | --- | --- | --- |
| 商品数据 | `sales-products` | `routes/products_routes.py`（11 个端点，`/api/sales/products`） | `pearnly-products` | 待迁移 |
| 库存 | `inventory` | `routes/inventory_routes.py`（6 个端点） | `pearnly-inventory` | 待迁移 |
| 入库 | `stock-in` | 库存/单据系列，具体端点待核对 | `pearnly-stock-in` | 待迁移 |
| 出库 | `stock-out` | 库存/单据系列，具体端点待核对 | `pearnly-stock-out` | 待迁移 |
| 收发存报表 | `stock-card` | `routes/stock_card_routes.py`（4 个端点，`/api/stockcard`） | `pearnly-stock-card` | 待迁移；权限码原挂 accounting，须改挂 inventory |
| 采购记录 | `purchase` | `routes/purchase_routes.py`（19 个端点） | `pearnly-purchase` | 新平台仅有早期切片，须按旧线上重做 |
| 供应商 | `purchase-suppliers` | `routes/purchase_config_routes.py`（13 个端点） | `pearnly-suppliers` | 待迁移 |
| 采购设置 | `purchase-settings` | `routes/purchase_config_routes.py`（13 个端点） | `pearnly-buy-settings` | 待迁移 |
| 销售记录 | `sales-records` | `routes/sales_routes.py`（15 个端点），另有导出 2、设置 2 | `pearnly-sales` | 待迁移 |
| 客户 | `buyer-clients` | `routes/clients_routes.py`（6 个端点），归属待核对 | `pearnly-clients` | 用户已确认需要，与「公司主档」分列 |
| 公司主档 | `clients`（ERP 入口下改标签为「公司主档」） | `routes/clients_routes.py`（6 个端点），归属待核对 | 新目录缺此项，需新建独立页面 | 用户已确认需要 |
| 公司资料 | `company` | 待核对（`company-profile.ts` 对应服务端） | `pearnly-company` | 待迁移 |
| 团队管理 | `erp-team` | `routes/erp_team_routes.py`（7 个端点，`/api/erp/team`） | `pearnly-team` | 待迁移 |
| 集成 | `integrations` | `routes/erp_endpoints_routes.py`（8 个端点）等 | `pearnly-integrations` | 待迁移 |

### 本批核对到的三项关键差异

1. 新平台 ERP 目录同样是 14 项，但内容是旧截图 14 项去掉「公司主档」再加上「首页」（见 `apps/pearnly_core/pearnly_core/desk/navigation.py` 的 `PAGES['erp']`）。**项目数相同不等于范围一致**，不能按名称对齐。
2. 旧 ERP 里「客户」与「公司主档」是两个不同页面：`clients-navigation.ts` 在 erp/pos 入口下把 `clients` 路由整体改标签为「公司主档」（去掉原页的页签栏），并额外显示 `buyer-clients` 作为「客户」（销售买方名录）。新目录只有一页 `pearnly-clients`。**用户 2026-09-21 确认两者都要**，因此新平台必须在主数据组有独立的「公司主档」，销售组有「客户」，不能合并成一页。

### 开通流程为什么会装 ERPNext（2026-09-21 只读核对）

结论：这是开通服务端的**遗留默认动作，没有任何代码依赖在支撑它**，而且与本产品自己的清单互相矛盾。

证据：

- `infrastructure/production/site-step.py:87`：`install_app` 分支里，只要请求安装 `pearnly_erp_portal`，就自动在前面插入 `erpnext`；同文件还会把它写进 `apps.txt`。该行由提交 `c6f7bc6`（2026-09-19，接通本地控制面与生产 Site）引入。
- `infrastructure/production/provision-agent.py` 的 `validate()` 只允许安装 `pearnly_core` 或该产品自己的 App；`erpnext` 会被直接拒绝，`scripts/production/test-provision-agent.py:139` 正是这样断言的。也就是说控制面从来没有请求过 ERPNext，是服务端步骤自己加上的。
- `apps/` 全目录只有 `pearnly_core/pearnly_core/provisioning.py:122` 提到 `erpnext`，且只是把 `Installed Application.is_setup_complete` 里的 frappe/erpnext 标记为完成（没有它这一句就是空更新）。没有任何模块 import ERPNext，`hooks.py` 的 `required_apps` 也全部为空。
- 与本产品合同冲突：`apps/pearnly_erp_portal/pearnly_erp_portal/product_manifest.py` 的 `must_not` 第一条就是「默认安装 ERPNext 全套业务」。
- 实测：`pearnly-erp-pos-lab` 只装 `pearnly_core` + `pearnly_erp_portal`，安装与 `migrate` 均正常，两租户站点 `list-apps` 不含 erpnext。

需要提醒的影响：装了 ERPNext 的租户站点会多出它自己的模块、工作区与桌面图标，与「产品导航只投影已实现且有权限的入口」的要求相冲突；后续还要跟着它的补丁与升级走。原来的用意应该是更早阶段「原生 ERPNext 打底 + Pearnly 外壳」的方向，方向改成「后端走 Pearnly」之后这行没被一起收掉。

### 去掉自动安装 ERPNext（用户 2026-09-21：不要它）

改动：`infrastructure/production/site-step.py` 的 `install_app` 分支不再插入 `erpnext`，只装请求的那一个 App；注释里写明原因与用户决定。

刻意**没有**动的两处，以及理由：

- `apps.txt` 仍保留 `erpnext`。它只是 bench 的应用清单，不代表安装。已有 ERP 站点若装过 ERPNext，清单里留着才不会让它的 `bench migrate` 出问题；等确认没有任何站点装着它再清。
- `pearnly_core/provisioning.py:122` 仍把 `erpnext` 写进 `is_setup_complete` 的更新语句。没有这个 App 时那条更新匹配 0 行，是空操作，属于同类遗留，改动面不值得扩大。

复验（跑的是**改动后的真实脚本**，不是仿制逻辑）：

- 新增 `infrastructure/compose/erp-pos-provision-lab.yml`：服务名与生产一致（`mariadb` / `redis-cache` / `redis-queue`），维护容器同样以 root 启动、由脚本自己降权到 uid 1000，并把生产脚本挂成 `/run/site-step.py`。
- 新增 `scripts/erp-pos-provision-lab-verify.py`：按 provision-agent 的方式把 job JSON 从 stdin 送进去，依次执行 `new_site` → `install_app pearnly_core` → `install_app pearnly_erp_portal` → `finalize_site`，每步要求 `SITE_STEP_OK`，再读回站点并跑一次 `bench migrate`。
- 结果：站点装出 `['frappe', 'pearnly_core', 'pearnly_erp_portal']`，**没有 erpnext**；`migrate` 退出码 0；`problems` 为空。
- 对照：把改动前（`ac33e0c`）的同一脚本挂进去跑一遍，装出来的是 `['erpnext', 'frappe', 'pearnly_erp_portal']` —— 说明这个复验确实能发现 ERPNext，不是因为测试看不见。对照站点随后用 `drop_site` 删除，实验室只剩受验站点。
3. 旧 ERP 的「集成」是 ERP LINE 绑定与录单相关入口（`erp_endpoints`、`erp_team` 等），与 Cowork 域的外部 ERP 推送不是同一件事，不得混为一谈。

## 变更与决定

### 批次计划

按批次施工，每批自带验证与提交，做完一批再开下一批。

| 批次 | 内容 | 状态 |
| --- | --- | --- |
| 第一批 | 线上基准、14 项范围与入口对照、隔离实验室、权限缺口复现 | 已交付 `37d96a7` |
| 第二批 | ERP 服务端权限基线：岗位 / 能力 / 取数范围，采购接口按权限取数 | 本批 |
| 第三批 | 员工与岗位（团队管理服务端）：新增员工、发功能角色、同一登录口与导航投影 | 本批 |
| 第四批 | 商品数据（商品主数据 + 单位 + 成本遮蔽） | 本批 |
| 第五批 | 采购记录、供应商、采购设置 | 本批 |
| 第五批 B | 销售记录 | 待开 |
| 第六批 | 库存、入库、出库、收发存统一口径 | 待开 |
| 第七批 | POS 收银台与交接班（后端走 Pearnly） | 待开 |
| 第八批 | 收尾：全部页面与前端（前端重构）+ 语言包 + 浏览器与真机验收 + 历史数据演练 + 整合验证 | 待开（用户要求前端留到最后） |

- 最终变化：新增本记录、ERP/POS 本地实验室定义（`infrastructure/compose/erp-pos-lab.yml`、`scripts/erp-pos-lab-init.py`）与权限探针（`scripts/erp-pos-lab-permission-probe.py`）。无产品业务代码变更。
- 重要决定：POS 借 ERPNext 的前端体验与交互，但业务后端仍走 Pearnly 的商品、库存、收发存与销售；收银员与老板共用 ERP 登录口，登录后按岗位只看到收银台、交接班与本人销售。此边界待写入 ADR 时另开记录，本轮仅在工作包登记。
- 依赖与回退影响：无代码变更，无回退面。

## 本地实验环境（本批建成）

`pearnly-erp-pos-lab`：自有 Compose project、自有数据库/Site 卷、内网无外联、不发布端口，与 `pearnly-sample`、`pearnly-cowork-backend-lab` 相互独立。运行定义见 [erp-pos-lab.yml](../../infrastructure/compose/erp-pos-lab.yml)，安装脚本见 [erp-pos-lab-init.py](../../scripts/erp-pos-lab-init.py)。

刻意**不安装 erpnext**，只装 `pearnly_core` 与 `pearnly_erp_portal`；这样一旦某条链暗中依赖 ERPNext 业务 DocType，安装或运行会直接失败而不被掩盖。两个站点 `erp-pos-a.localhost` / `erp-pos-b.localhost` 即两租户隔离夹具。

## 第二批：ERP 服务端权限基线

落点：`apps/pearnly_erp_portal/pearnly_erp_portal/access/`。

- `rules.py`：**纯逻辑**（不导入 Frappe / IO / 上层流程）。功能 → 角色、能力 → 功能、`all` / `own` 取数范围。角色名与开通侧 `features.py` 的 `PRODUCT_ADMIN_ROLE` / `ENFORCEMENT` 完全一致，改名会在离线测试里直接失败而不是静默失权。
- `guard.py`：服务端判据。`require(capability)` 区分未登录（`AuthenticationError`）与无权限（`PermissionError`）；`scope_filters()` 返回取数范围（管理员空过滤、员工限本人 `owner`）。框架超管 `Administrator` 单独处理，产品界面不会发给客户。
- `purchase.py`：`_require_user()`（只校验登录）换成 `guard.require(...)`；列表与汇总从会绕过权限的 `frappe.get_all` 改成跟随角色与用户权限的 `frappe.get_list`，并叠加岗位范围。汇总的取值改成对 dict/对象都成立，顺带消掉一处潜在属性访问问题。

设计口径来自旧线上既有约定：功能角色只在功能开通时发放（没开通就没有角色），员工只看本人录入的记录、数据仍落老板账套。

## 第三批：员工与岗位（团队管理服务端）

落点：`access/rules.py` 扩展 + 新增 `team/`（`service.py` / `api.py`）。

- 岗位目录：功能角色从只有 `purchase` 扩到 `purchase / sales / stock / products / cashier` 五个，**但只有本站点上真实存在的角色可发放**（没开通就不建角色，因此天然只列买了的岗位）。平台角色与产品管理员角色永不外发。
- 管理员专属能力 `team.manage`：与功能能力分开，只有 `ERP Admin` 能管团队。
- 接口：列出成员、新增员工、改岗位、停用/启用在 `team.service`，`team.api` 只做参数解析；判断全在服务端。
- 口径沿用旧线上：员工至少要有 1 个岗位（空岗位被拒）；老板本人与平台账号不可通过这扇门修改。

### 遇到的真实约束与决定

核心给 `User` 注册了 `pearnly_core.desk.permissions.user_permission`：产品站点上非超管
只能访问**自己**那条用户记录。这在文档级权限检查里对"别人的 User"一律返回 False，
于是租户管理员连新增员工都会被框架挡下（实测：`has_permission("User","create")` 为真，
但带文档的检查为假，`insert()` 抛 `PermissionError`）。

决定：产品自己的判据放在 `team.service`（只有 `team.manage` 能进、只能发本站点已开通的
功能角色、不能改老板），写用户时显式 `ignore_permissions=True` —— 与开通侧
`ensure_local_user` 建账号的做法一致。

没有选的做法：去改核心那个钩子让产品管理员能管别人。它会同时放开 DMS/Cowork 的
User 访问面，而且另一个窗口正在改 Cowork，改动面太大且需要它们的验收。若后续判断
应该由核心统一放行，再单独立项。

### 第三批验证

- 离线规则测试 12 项通过（新增：团队能力仅管理员、只有本站点存在的角色可发放、没有已购岗位时无可发放角色）。
- 实验室真实 Site：老板新增员工成功并落岗位；员工拿到岗位后能取数且**只看到本人**；发 `System Manager` 被拒；发本站点没有的 `ERP Sales` 被拒；空岗位被拒；改老板被拒；员工管理团队被拒；停用后 `enabled` 落库为 0。
- 导航投影（`pearnly_core.desk.navigation.visible_pages("erp")`）：老板与员工都能看到 `pearnly-purchase`，无岗位账号只有 `pearnly-home` —— 「按权限分配、进去只看得到的入口」在导航层成立。
- 未验（明确留白）：停用后的**会话级**拦截属于框架登录闸，进程内 `set_user` 不走登录检查，本批不用它冒充已验证，留到有 HTTP 服务的批次。收银台与交接班的页面本身在第六批。

### 收银含在 ERP（用户 2026-09-21 定）

结论：收银不作为单独购买的功能，含在 ERP 里。依据是控制面既有约定「开通不分功能模版，选了产品就把该产品功能全开」（`features.codes_of`），所以列进 ERP 目录即等于送给每个 ERP 租户。

改动：

- `pearnly_control/features.py`：ERP 目录加 `cashier`（`default_on: True`）；`ENFORCEMENT` 加 `("erp","cashier"): ("ERP Cashier", [])`；`roles_payload` 改为**有角色就进 payload**（原来要求 `doctypes` 非空，会把"岗位先于表存在"的情况整条丢掉）。
- `pearnly_core/provisioning.py`：`ensure_feature_roles` 也创建没有可挡表的岗位角色，否则团队管理勾不到「收银员」。
- `pearnly_erp_portal/product_manifest.py`：features 补 `cashier`，与目录互为镜像。

刻意保留的边界：收银角色现在**只有岗位、没有可挡的表**（POS 表在第六批）。它不会因此多给任何数据访问权限——角色本身不授权，等 POS 表落地再按同一份目录补 DocType。

验证（实验室直接用控制面自己发布的清单开通，脚本里不另抄角色表）：控制面发出的角色为 `[ERP Cashier, ERP Purchase]`；站点上 `ERP Cashier` 角色已建；可发放岗位为 `[ERP Purchase, ERP Cashier]`；老板开通后自动带上 `ERP Cashier`（这是"老板自己也能收银"的依据）；老板能把「收银员」发给员工且成功落岗位。

## 第四批：商品数据

落点：新增 `products/`（`contracts.py` 纯规则 / `service.py` / `api.py`），DocType `ERP Product` + 子表 `ERP Product Unit`。

- 字段与旧线上 `routes/products_routes.py` 的 ProductCreate 对齐：名称、编码、条码、QR、单位、售价、VAT、图片、低库存阈值、参考成本，另加 `avg_cost`（由库存移动维护，此处只读）。长度上限照旧，迁进来的数据不会被莫名拒绝。
- **软删除**：停用 `is_active=0` 即旧线上的"删除"，历史单据继续指向它；列表默认不含停用品。
- 编码/条码同站点唯一（站点即租户边界），撞码分别报自己的错。旧线上撞码后可以按编码复活停用品；新平台因唯一索引不允许重复，提示"编码已存在"，让用户先把旧品重新上架——**这是与旧线的有意差异**，已登记。
- 单位：换算系数必须为正；同一时间只有一个默认销售单位（DocType 校验里收口，避免收银台取到两个价格）。

### 一个必须说清楚的实现差异：`price_set`

旧线上 `unit_price` / `default_cost` 是可空列，"没设价"与"真的 ฿0"是两回事（旧线上为此出过事故：扫码就地建品建出来的商品全部 ฿0 可售）。Frappe 的 Currency 列**不能存 NULL**，实测空值落库就是 `0.0`，所以这个差别必须另立标记。

处理：加一个 `price_set` 勾选字段，由服务层与价格一起写；`0` + `price_set=0` 表示"从未设价"，`0` + `price_set=1` 表示"真的是 ฿0"。收银台的零元闸（第七批）读这个标记而不是猜数字。

未决：`default_cost` 的同类区别留到第六批库存成本口径一起定（成本未知不能变成零是那批的验收项之一），本批不假装已经处理。

### 岗位 → 表权限的对齐（本批踩到的坑）

能力判据过了但取数仍被拒：`ERP Stock` 岗位在 `ERP Product` 上没有 DocType 读权限。修法是把控制面的"只读覆盖"从 `(产品, 表)` 改成 `(产品, 表, 角色)`，再给 `("erp","ERP Product","ERP Stock")` 只读权限——否则会把商品岗位自己也一起降成只读。

同时 `products.view` 从只认商品岗位改为**商品岗位或库存岗位**（库存做入库/出库要先看得到商品），`products.manage` 仍只给商品岗位（库存岗位只读）。

## 第五批：采购记录、供应商、采购设置

落点：`purchase.py` 退成**薄兼容入口**（只保留旧函数名与旧返回格式），业务全进新域
`purchasing/`：`contracts.py` 纯规则、`service.py` 单据、`suppliers.py` 供应商、
`settings.py` 采购设置、`api.py` 接口。新增 DocType `ERP Supplier`、`ERP Purchase Payment`
（付款子表）、`ERP Purchase Settings`（单例），并给 `Purchase Document` 补 `supplier`、
`source`、`payments`、`void_reason`、`voided_at`、`dedupe_ref`。

- **金额口径只有一份**：规则在 `purchasing/contracts.py`，由 `Purchase Document.validate()`
  调用。网页、LINE、识别入库三条路径都用它，不再各算一遍。旧线上实测过的样例已固化成
  离线断言：小计 1000、VAT 70、WHT 30 → 净应付 1040。
- **流转**：草稿 → 提交 → 作废；提交后不能改回草稿，也不能再编辑。非法流转一律拒绝。
- **付款**：提交后才能登记；多笔累加进 `paid_amount`；超过净应付被拒；付款状态跟着
  金额走（0 / 部分 / 已付）。**已付款的单据不能直接作废**，必须先把付款冲掉，否则账上
  会留下无主的钱。
- **供应商**：建档 + 停用（不删）。建单时手写供应商名若与档案**精确同名会自动关联**，
  否则去重会认成两家。
- **两把去重锁分开**：`dedupe_key` 是外部幂等键（识别图片指纹/调用方请求号），始终生效；
  `dedupe_ref` 是"供应商 + 单据号"，受采购设置 `dedupe_block` 控制。第一版把两者写成
  一个字段，结果显式传幂等键时"供应商+单据号"那道锁被绕过——实验室里暴露后拆成两列。

### 有意不做与留到后面的

- **过账到库存**：表在库存批次，`auto_stock_in` 默认关；提交只推进状态，并**不假装已经
  扣过库存**。库存批次把联动接进来后这个开关才生效。
- **生成会计凭证**：会计不迁移，采购提交不产生凭证（旧线上那 4 个挂点在这里等于空）。
- 附件、代开发票/预扣税凭证打印、费用科目与关键词规则（旧线上 `expense_rules`）未迁，
  属采购设置的后续增量。

## 验证

| 时间 | 代码 SHA / 未提交差异 | 环境与数据归属 | 命令或场景 | 预期 / 实际 / 退出码 | 结论和证据 |
| --- | --- | --- | --- | --- | --- |
| 2026-09-21 17:30 +07 | 只读，无代码变更 | GCP `pearnly`（只读） | `gcloud run services/revisions describe pearnly-web`、`pearnly-worker` | 预期 100% 流量与既有 digest 不变 / 实际一致 / 0 | 旧线上身份与上一轮一致，可继续作为迁移基准 |
| 2026-09-21 17:4x +07 | `ac33e0c` + 未提交实验定义 | 本地 `pearnly-erp-pos-lab`（自有卷） | `docker compose up -d` + `erp-pos-lab-init.py` | 预期两站点安装成功且不含 erpnext / 实际一致 / 0 | 两租户 Site 建成；`list-apps` 仅 frappe+pearnly_core+pearnly_erp_portal，可安装可迁移，未依赖 ERPNext 业务应用 |
| 2026-09-21 17:4x +07 | 同上 | 站点 `erp-pos-a.localhost` 合成数据 | `erp-pos-lab-permission-probe.py` | 见下 | **权限缺口已复现** |
| 2026-09-21 18:2x +07 | 未提交（第二批改动） | 主机 Python，无 Site | `python3 tests/test_erp_portal_access.py -v` | 9 项通过 / 实际 9 项通过 / 0 | 规则纯逻辑与角色名一致性 |
| 2026-09-21 18:3x +07 | 同上 | 实验室两站点，合成用户与单据 | `erp-pos-lab-permission-verify.py` | 期望 `problems` 为空 / 实际为空 / 0 | 见下：岗位与范围在服务端生效 |
| 2026-09-21 18:3x +07 | 同上 | 本仓 | `python3 scripts/check-architecture.py` | PASS / 0 违规 / 0 | — |
| 2026-09-21 18:3x +07 | 同上 | 本仓与 `ac33e0c` 只读对比树 | `check-i18n.py --strict`、`check-frontend-i18n.py --strict` | 增量应为 0 / 实际两处均为 95 条、前端 0 条 / 0 | 严格 i18n 存量为 95 条，本批零新增 |
| 2026-09-21 18:5x +07 | 未提交（开通改动 + 实验室） | 本地 `pearnly-erp-pos-provision-lab`，服务名对齐生产 | `erp-pos-provision-lab-verify.py` | 装出 Core + ERP 且无 erpnext、`migrate` 成功 / 实际一致 / 0 | 生产开通路径本地复验通过 |
| 2026-09-21 18:5x +07 | `ac33e0c` 的开通脚本（只读对照） | 同上 | 同一套 job 序列 | 预期出现 erpnext / 实际 `['erpnext','frappe','pearnly_erp_portal']` | 对照证明复验对 ERPNext 敏感；对照站点已 drop |
| 2026-09-21 19:2x +07 | 未提交（第三批改动） | 主机 Python，无 Site | `python3 tests/test_erp_portal_access.py` | 12 项通过 / 实际 12 项通过 / 0 | 岗位目录与团队能力规则 |
| 2026-09-21 19:2x +07 | 同上 | 实验室站点 A，合成账号 | `erp-pos-lab-team-verify.py` | 期望 `problems` 为空 / 实际为空 / 0 | 见「第三批验证」 |
| 2026-09-21 19:2x +07 | 同上 | 本仓 | 架构检查、`check-i18n --strict`、前端 i18n | 架构 0 违规、i18n 不新增 / 实际 0 违规、仍 95 条、前端 0 条 / 0 | — |
| 2026-09-21 19:5x +07 | 未提交（收银含 ERP） | 实验室站点 A，用控制面发布的清单开通 | `erp-pos-lab-team-verify.py` | 期望 `problems` 为空、可发放岗位含收银员 / 实际为空、`[ERP Purchase, ERP Cashier]` / 0 | 岗位先于表存在也建得出来 |
| 2026-09-21 20:2x +07 | 未提交（第四批） | 主机 Python，无 Site | `tests/test_erp_portal_access.py` | 20 项通过 / 实际通过 / 0 | 商品纯规则 + 成本可见性 + 岗位映射 |
| 2026-09-21 20:2x +07 | 同上 | 两个实验室站点，迁移后真实行为 | `erp-pos-lab-products-verify.py` | 期望 `problems` 为空 / 实际为空 / 0 | 建品、撞码、成本遮蔽、软删除、跨租户 |
| 2026-09-21 20:2x +07 | 同上 | 实验室站点 A | 回归 `erp-pos-lab-permission-verify.py`、`erp-pos-lab-team-verify.py` | 两处 `problems` 为空 / 实际为空 / 0 | 岗位映射改动未打破前两批 |
| 2026-09-21 21:0x +07 | 未提交（第五批） | 主机 Python，无 Site | `tests/test_erp_portal_access.py` | 27 项通过 / 实际通过 / 0 | 新增采购金额、流转、去重键断言 |
| 2026-09-21 21:0x +07 | 同上 | 两个实验室站点 | `erp-pos-lab-purchasing-verify.py` | 期望 `problems` 为空 / 实际为空 / 0 | 供应商、设置、建单金额、重复单据、分笔付款、作废、岗位与范围 |
| 2026-09-21 21:0x +07 | 同上 | 实验室站点 A | 四个探针全部回归 | 全部 `problems` 为空 / 实际为空 / 0 | 第五批未打破前四批 |

探针实际输出（合成数据，非生产）：老板账号建单成功（`PUR-2026-00001`）；收银员账号角色仅 `All/Desk User/Guest`，`has_permission("Purchase Document","read")` 为 `false`，**但 `api_list_purchases` 仍返回 1 行完整采购单**。原因是接口只校验“已登录”，列表走 `frappe.get_all` 而不做权限过滤。另一站点 `erp-pos-b.localhost` 采购单行数为 0，看不到 A 站的单据，租户隔离在结构上成立。

第二批验证结果（`problems` 为空，退出码 0）：

- 老板（`ERP Admin` + `ERP Purchase`，开通后系统已收回其 `System Manager`）：可建单、列表看到本店全部采购单、汇总正常。
- 员工（仅 `ERP Purchase` + `Desk User`）：可建单，**列表只看到本人 1 条**，看不到老板那 1 条。
- 无关账号（仅 `Desk User`）：列表与建单都被拒（`PermissionError`），不再返回数据。
- 未登录（`Guest`）：`AuthenticationError`。
- 重跑第一批探针，同一路径从「返回 1 行」变成 `PermissionError: Not permitted` —— 旧缺口已关闭。
- 跨租户：站点 B 采购单行数仍为 0。

| 层级 | 状态 | 依据与未覆盖项 |
| --- | --- | --- |
| 实现 | 未执行 | 尚无产品代码 |
| 本地验证 | 部分通过 | 已验（第二批）：权限规则离线 9 项、实验室岗位与范围行为、旧缺口已关、跨租户不可见、架构 0 违规、i18n 零新增。未验：其余 13 项业务功能、POS 流程、团队管理界面、语言包新词条、浏览器与真机 |
| CI | 未执行 | 未推送 |
| 部署 | 未执行 | 未获授权 |
| 外部业务结果 | 不适用 | 本批不产生外部写入 |
| 用户 / 真机验收 | 未执行 | 待实现后另验 |

## 审查与交接

- 自审 / 独立审查：本批为只读核对与记录，未做独立审查。
- 剩余问题 / 阻塞原因：已接入能力判据的只有 `purchase` 与 `products`，库存/销售/收银的表与接口在后续批次；团队管理、商品、采购都还只有服务端。停用账号的会话级拦截未验；旧线上运行开关与后台任务未核对；`default_cost` 的"未设价"区别留到库存批次；采购附件与费用科目规则未迁。
- 迁移口径差异（有意，已登记）：旧线上"同一账套+供应商+单据号"去重在**账套**层；新平台站点即租户边界，因此按站点内判重。旧线上撞码可复活停用品，新平台要求先重新上架。
- 前端：**全部页面与前端留到最后一批**（用户 2026-09-21 明确：前端要重构，留到最后）。此前批次只做服务端与数据。
- 开通改动：去掉自动安装 ERPNext 已完成并在本地复验；未部署，正式环境尚未执行。
- 已定（用户 2026-09-21）：收银含在 ERP，不作为单独购买的功能；见上「收银含在 ERP」。
- 下一步：第三批 B（团队管理页面 + 公司主档与客户两个独立页面），随后第四至第七批。
