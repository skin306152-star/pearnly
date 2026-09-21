# 工作包：ERP 与 POS 迁移到新平台

## 目标和范围

- 目标 / 完成条件：把用户确认保留的 14 项 ERP 功能迁到新平台 `pearnly_erp_portal`，逐项具备实现与本地验证证据；租户隔离与员工权限隔离在真实测试 Site 上通过（两租户、老板与收银员两类账号）；一笔 POS 销售只落一笔销售记录并只产生一次库存影响。业务行为以旧项目当时承接生产流量的版本为基准。
- 范围裁定（用户 2026-09-21）：**按截图搬** —— 只做那张 ERP 侧栏截图里的 14 项。截图之外的旧 ERP 功能不迁（清单见下）。
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

### 按截图搬：截图之外不迁的旧 ERP 功能（用户 2026-09-21 定）

旧 ERP 侧栏里还有一批**不在 14 项截图里**的入口。逐项核对后的处置：

| 旧入口 | 处置 |
| --- | --- |
| 商品组：费用数据 | 不迁（独立台账，截图里没有） |
| 收银系统：销售报表、交易明细、收款设置、开通收银台、桌台管理 | 不迁。收银本身（收银台/班次/销售）是用户单独要求的，已交付 |
| 权限管理系统：操作记录/异常 | 不迁。同组的"收银员""切到收银台"已换形态落地（团队管理 / 共用一个 ERP 登录口按岗位分配） |
| 销售开票组：发票工作台、账套·开票资料 | 不迁。截图里的"销售记录"是**列表页**（`sales-records` → `GET /api/sales/documents`），建单工作台是另一页且不在截图内；开票资料已由"公司资料"承担 |
| 做账（凭证/科目/账簿/银行） | 不迁（P0.04 已登记） |
| 使用教程 | 不迁 |
| 事务所工具（录入工作台/识别记录/推送日志/对账中心/库存盘点） | 归 Cowork（同一个界面壳里的另一组） |

### 销售记录的对齐（用户 2026-09-21 更正，以此为准）

上面那条按截图推断的"收窄"**被用户更正**：截图里的「销售记录」在新平台就是
**上传发票识别 + 手工录入**并由此产生销售记录，不是只读列表。

对齐后的定义：

| 方向 | 内容 |
| --- | --- |
| **进（谁产生销售记录）** | ① 新 POS 收银（已交付：收银台/班次/收银销售）；② 上传发票（拍照/PDF → 识别 → 复核 → 存档）；③ 手工录入 |
| **出（销售记录影响什么）** | 只有**商品 / 库存 / 收发存报表**（开了票 = 货出门，过账成出库流水，与采购入库同源） |
| **明确不接** | 会计凭证、报税、对外 ERP 推送、账套/开票资料、银行与对账 |

已落进契约的判据：来源三种（`pos` / `ocr` / `manual`）；**只有已开出的销售凭证动货**
（报价单与草稿都不动），过账方向是出库。识别与手工录入的字段面按旧线上销项单据的
买方块 + 明细 + VAT/WHT + 单据号日期对齐（金额规则已在本批的 `compute_totals` 里）。

因此第十批 B/C 的内容改为：销售单据 DocType 与取号、开出/作废/冲正流转、**出库过账**、
上传发票的识别入口与复核、手工录入、PDF/打印与导出。原先写的"不做建单"作废。

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
| 第二批 | ERP 服务端权限基线：岗位 / 能力 / 取数范围，采购接口按权限取数 | 已交付 `32a442a` |
| 第三批 | 员工与岗位（团队管理服务端）：新增员工、发功能角色、同一登录口与导航投影 | 已交付 `6b44dbd`、`e27940c` |
| 第四批 | 商品数据（商品主数据 + 单位 + 成本遮蔽） | 已交付 `850cfce` |
| 第五批 | 采购记录、供应商、采购设置 | 已交付 `7b87c69` |
| 第五批 B | 销售记录 | 待开 |
| 第六批 | 库存、入库、出库、收发存，并把采购确认接上库存 | 已交付 `844708e` |
| 第七批 | POS 收银台、班次、交接班与销售记录 | 已交付 `28ccea3` |
| 第八批 | 公司主档、公司资料、客户（服务端） | 已交付 `e11462a` |
| 第九批 A | 集成 A：LINE 绑定（服务端 + 通道侧） | 已交付 `bf3d2e5` |
| 第九批 B | 集成 B：LINE 录单服务端（收件 → 留底 → 草稿 → 确认建采购记录） | 本批 |
| 第九批 C | 集成 C：录单的识别与确认页面 + 对外 ERP 端点清单的投影 | 待开（页面归收尾批；端点投影依赖 Cowork 连接包合回主线） |
| 第十批 A | 销售单据契约（类型 / 金额 / 连号 / 开票闸 / 泰文大写，纯规则） | 本批 |
| 第十批 B | 销售单据 DocType、取号落库、开出/作废/冲正流转 | 待开 |
| 第十批 C | PDF、审批、PromptPay、对外发送与归档 | 待开 |
| 收尾批 | 全部页面与前端（前端重构）+ 语言包 + 浏览器与真机验收 + 历史数据演练 + 整合验证 | 待开（用户要求前端留到最后） |

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

- 字段与旧线上 `routes/products_routes.py` 的 ProductCreate 对齐：名称、编码、条码、QR、单位、售价、VAT、图片、低库存阈值、参考成本，另加 `avg_cost`（由库存移动维护，此处只读）。长度上限照旧，迁进来的数据不会被莫名拒绝。（后续：`avg_cost` 与 `note` 已在收尾批 B 从 DocType 删掉，字段改成与旧线上 products 的列一一对应，见文末「收尾批 B」。）
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

### 有意不做与留到后面的（采购）

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
| 2026-09-21 22:0x +07 | 未提交（第六批） | 主机 Python，无 Site | `tests/test_erp_portal_access.py` | 40 项通过 / 实际通过 / 0 | 滚动成本、符号/回放一致性、期初切分 |
| 2026-09-21 22:0x +07 | 同上 | 两个实验室站点，迁移后真实行为 | `erp-pos-lab-inventory-verify.py` | 期望 `problems` 为空 / 实际为空 / 0 | 见「第六批验证」 |
| 2026-09-21 22:0x +07 | 同上 | 实验室站点 A | 五个探针全部回归 | 全部 `problems` 为空 / 实际为空 / 0 | 第六批未打破前五批；采购/库存脚本互相复位开关 |
| 2026-09-21 22:4x +07 | 未提交（第七批） | 主机 Python，无 Site | `tests/test_erp_portal_access.py` | 51 项通过 / 实际通过 / 0 | 付款结算、找零、班次现金 |
| 2026-09-21 22:4x +07 | 同上 | 两站点，全新 migrate 后 | `erp-pos-lab-cashier-verify.py` | 期望 `problems` 为空 / 实际为空 / 0 | 见「第七批验证」 |
| 2026-09-21 22:4x +07 | 同上 | 实验室站点 A | 六个探针全部回归 | 全部 `problems` 为空 / 实际为空 / 0 | 第七批未打破前六批 |
| 2026-09-21 18:5x +07 | 未提交（第八批） | 主机 Python，无 Site | `python3 tests/test_erp_portal_access.py` | 58 项通过 / 实际通过 / 0 | 新增买方归一化与开票缺项 |
| 2026-09-21 18:5x +07 | 同上 | 两站点，`migrate` 后真实行为 | `erp-pos-lab-masters-verify.py` | 期望 `problems` 为空 / 实际为空 / 0 | 见「第八批验证」 |
| 2026-09-21 18:5x +07 | 同上 | 实验室站点 A | 六个既有探针全部回归 | 全部 `problems` 为空 / 实际为空 / 0 | 第八批的授权改动未打破前七批 |
| 2026-09-21 18:5x +07 | 同上 | 本仓 | `python3 scripts/check-architecture.py` | PASS / 0 违规 / 0 | — |
| 2026-09-21 18:5x +07 | 同上 | 本仓 | `check-i18n.py --strict`、`check-frontend-i18n.py --strict` | 增量应为 0 / 实际仍 95 条、前端 PASS / 0 | 新增文本都在注释与文档字符串（工具按 AST 排除） |
| 2026-09-21 19:0x +07 | 未提交（第九批 A） | 主机 Python，无 Site | `python3 tests/test_erp_portal_access.py` | 62 项通过 / 实际通过 / 0 | 新增集成能力与解绑词表 |
| 2026-09-21 19:0x +07 | 同上 | 两站点，假 LINE 传输 | `erp-pos-lab-integrations-verify.py` | 期望 `problems` 为空 / 实际为空 / 0 | 见「第九批 A 验证」 |
| 2026-09-21 19:0x +07 | 同上 | 实验室站点 A | 七个既有探针全部回归 | 全部 `problems` 为空 / 实际为空 / 0 | 核心 `line.py` 与 hooks 改动未打破前八批 |
| 2026-09-21 19:0x +07 | 同上 | 本仓 | `python3 scripts/check-architecture.py` | PASS / 0 违规 / 0 | 新域 `integrations/` 通过边界检查 |
| 2026-09-21 19:0x +07 | 同上 | 本仓 | `check-i18n.py --strict`、`i18n-build.py --check` | 增量 0 / 实际仍 95 条（新文案已进包） / 0 | ERP 门户第一份翻译包由术语表生成 |
| 2026-09-21 19:3x +07 | 未提交（第九批 B） | 两站点，假 LINE 传输与本地生成的图片/PDF 夹具 | `erp-pos-lab-intake-verify.py` | 期望 `problems` 为空 / 实际为空 / 0 | 见「第九批 B 验证」 |
| 2026-09-21 19:3x +07 | 同上 | 实验室站点 A | 八个既有探针全部回归 | 全部 `problems` 为空 / 实际为空 / 0 | 新增 DocType 与授权未打破前八批 |
| 2026-09-21 19:3x +07 | 同上 | 本仓 | `check-architecture.py`、`check-i18n.py --strict` | 0 违规 / 实际 0 违规、仍 95 条 / 0 | 新增中文只有用户手打的解绑词（已登记） |
| 2026-09-21 20:0x +07 | 未提交（第十批 A） | 主机 Python，无 Site | `python3 tests/test_erp_portal_access.py` | 74 项通过 / 实际通过 / 0 | 销项金额、连号、开票闸、泰文大写 |
| 2026-09-21 20:0x +07 | 同上 | 本仓 | `check-architecture.py`、`check-i18n.py` | 0 违规 / 实际 0 违规、仍 95 条（未新增文案） / 0 | 纯规则模块不连库 |
| 2026-09-21 20:2x +07 | 未提交（第十批 A 追加） | 主机 Python，无 Site | `python3 tests/test_erp_portal_access.py` | 79 项通过 / 实际通过 / 0 | 状态×动作、红冲/补开、报价转票 |
| 2026-09-21 20:4x +07 | 未提交（销售记录对齐） | 主机 Python，无 Site | `python3 tests/test_erp_portal_access.py` | 82 项通过 / 实际通过 / 0 | 三种来源 + 只有已开出凭证过账出库 |

探针实际输出（合成数据，非生产）：老板账号建单成功（`PUR-2026-00001`）；收银员账号角色仅 `All/Desk User/Guest`，`has_permission("Purchase Document","read")` 为 `false`，**但 `api_list_purchases` 仍返回 1 行完整采购单**。原因是接口只校验“已登录”，列表走 `frappe.get_all` 而不做权限过滤。另一站点 `erp-pos-b.localhost` 采购单行数为 0，看不到 A 站的单据，租户隔离在结构上成立。

表中的时钟时间不完全可靠：第二至第七批那几行是上一段会话自己填的，与本机时钟对不上
（本工作树建于 2026-09-21 17:30，第八批实跑到 18:5x），所以按时间顺序读没有意义；
批次之间的先后以提交顺序为准确。

第二批验证结果（`problems` 为空，退出码 0）：

- 老板（`ERP Admin` + `ERP Purchase`，开通后系统已收回其 `System Manager`）：可建单、列表看到本店全部采购单、汇总正常。
- 员工（仅 `ERP Purchase` + `Desk User`）：可建单，**列表只看到本人 1 条**，看不到老板那 1 条。
- 无关账号（仅 `Desk User`）：列表与建单都被拒（`PermissionError`），不再返回数据。
- 未登录（`Guest`）：`AuthenticationError`。
- 重跑第一批探针，同一路径从「返回 1 行」变成 `PermissionError: Not permitted` —— 旧缺口已关闭。
- 跨租户：站点 B 采购单行数仍为 0。

| 层级 | 状态 | 依据与未覆盖项 |
| --- | --- | --- |
| 实现 | 部分完成 | 已实现（第二至第九批 B，全部服务端）：权限基线、团队管理、商品、采购、库存、收发存、收银台/班次/收银销售、公司主档/公司资料/客户、集成（LINE 绑定 + 录单服务端）。未实现：录单的识别与确认页面、对外 ERP 端点清单的投影，销售单据流（报价/发票/PDF/审批/PromptPay）；14 项里没有任何页面或前端 |
| 本地验证 | 部分通过 | 已验：九个域的实验室探针（权限、团队、商品、采购、库存、收银、主数据、集成绑定、录单）全部 `problems` 为空、离线规则 62 项、跨租户不可见、架构 0 违规、i18n 零新增。未验：前端、ERP 语言包（其它消息与标签仍只有英文）、浏览器与真机、历史数据演练、真实开户与真实 LINE 渠道 |
| CI | 未执行 | 未推送 |
| 部署 | 未执行 | 未获授权 |
| 外部业务结果 | 不适用 | 本批不产生外部写入 |
| 用户 / 真机验收 | 未执行 | 待实现后另验 |

## 审查与交接

- 自审 / 独立审查：本批为只读核对与记录，未做独立审查。
- 剩余问题 / 阻塞原因：14 项的服务端还剩两段 —— ①集成 B：LINE 录单（发单据 → 识别 → 入账）与对外 ERP 端点清单的投影，**后者依赖另一个窗口的 Cowork 连接包合回主线**（未合并期间按它施工会把未定契约冻进 ERP 侧）；②销售记录的单据流（报价/发票、PDF、审批、PromptPay）。所有功能目前都只有服务端，14 项里**没有任何页面或前端**，页面与前端统一留到收尾批。批号与型号、序列号/批次管理、近效期看板与库存周转报表未迁。
- 第七批范围说明：本批的"销售记录"指**收银销售**（POS 单据、退货、班次）。旧线上另有 `sales_documents`（报价/发票/收据、PDF、审批流、PromptPay）属开票侧，未在本批，登记为下一批增量。停用账号的会话级拦截未验；旧线上运行开关与后台任务未核对；`default_cost` 的"未设价"区别留到库存批次；采购附件与费用科目规则未迁。
- 迁移口径差异（有意，已登记）：旧线上"同一账套+供应商+单据号"去重在**账套**层；新平台站点即租户边界，因此按站点内判重。旧线上撞码可复活停用品，新平台要求先重新上架。
- 前端：**全部页面与前端留到最后一批**（用户 2026-09-21 明确：前端要重构，留到最后）。此前批次只做服务端与数据。
- 开通改动：去掉自动安装 ERPNext 已完成并在本地复验；未部署，正式环境尚未执行。
- 已定（用户 2026-09-21）：收银含在 ERP，不作为单独购买的功能；见上「收银含在 ERP」。
- 下一步：第十批「销售单据流」（报价/发票/PDF/审批/PromptPay，并顺手接上录单的销售方向）；第九批 C 的识别与字段预填要等 Cowork 的识别工作台合回主线、端点清单投影同理；最后收尾批做页面与前端重构 + 语言包 + 浏览器与真机验收 + 历史数据演练 + 整合验证。

## 第六批：库存、入库、出库、收发存

落点：新增 `inventory/`（`contracts.py` 纯规则、`stock.py` 流水与结存、`documents.py` 出入库单据、
`warehouses.py` 仓库、`openings.py` 期初、`report.py` 收发存与库存看板、`api.py` 接口）。
新增 DocType：`ERP Warehouse`、`ERP Stock Entry` + 行、`ERP Stock Movement`（不可变流水）、
`ERP Stock Balance`（物化结存）、`ERP Stock Opening`。`Purchase Line` 加商品链接，
`Purchase Document` 加 `stock_posted_at`。

口径（照旧线上 `services/inventory/ledger.py` 与 `services/stockcard/rolling.py`）：

- **滚动加权平均成本**：购入按票面净单价重算均价；出库按当前结存单价、不重算均价；
  退货入按当前单价、均价不漂移。
- **成本未知就是未知**：从未确立成本的商品出库只减数量，金额与单价留空 —— 不写成 0。
  离线断言把这条钉住了。
- **流水不可变**：只追加；出入库单据作废靠反向流水，历史不删。
- **期初落成流水**：见下。

### 三处与旧线不同的决定（都是主动选择，写清楚）

1. **期初进流水，不再单独做报表基数。** 旧线上期初只进收发存报表、不进在库结存，于是
   "在库 30、收发存期末 40"两个数并存。新平台把期初写成 `opening` 流水（`ERP Stock
   Opening` 保留为录入凭据，改期初按差额补一笔调整），因此在库、收发存、成本同源，只有一个数。
2. **数量只留一个带符号值。** 第一版同时传"带符号数量"和"滚存幅度"，回放历史流水时又按
   类型推一次方向，出库符号被翻两次、重算与实时记的账对不上。现在方向只由符号表达，
   类型只决定"要不要按票面价计价"。
3. **商品岗位看不到在库一览**（旧线上同样分开：商品主数据归商品岗位，在库归库存岗位）。
   库存岗位**只读**商品主数据，这已在第三批的权限覆盖里登记。

### 采购 → 库存联动

采购设置打开 `auto_stock_in` 时，确认单据会把**带商品链接的货物行**过账成 `purchase_in`
流水，入库单位成本 = 行净额 ÷ 数量（不含 VAT，折扣已扣）；幂等锚是单据名，重复确认不重复入库。
服务类与未关联商品的行不过账。开关关闭时只记账不动货，**不假装扣过库存**。

### 第六批验证

- 离线规则 40 项通过（新增滚动成本、符号与回放一致性、期初切分、单位换算、负数校验）。
- 实验室真实 Site：仓库自动默认、期初入账后结存 10、入库 5 件 @20 → 15 件均价 13.33、
  出库 3 件后 12 件且均价不变、按"โหล=12"入库落 24 件、未定义单位被拒、盘点把 36 收敛到 30、
  出入库单据提交 +4 / 作废回到原值且流水不删、结存按流水重建与实时一致、**成本未知的结存
  cost_known=0 且金额留空**、采购开关关/开分别不动货/加 5 件、库存岗位看得到成本而商品岗位
  看不到、无岗位账号被拒、跨租户不可见。
- 五个探针（权限、团队、商品、采购、库存）全部 `problems` 为空；架构 0 违规；i18n 无新增。

## 第七批：POS 收银台、班次、交接班、销售记录

落点：新增 `cashier/`（`contracts.py` 纯规则、`terminals.py`、`shifts.py`、`sales.py`、`api.py`）。
新增 DocType：`ERP POS Terminal`、`ERP Shift`、`ERP Sale` + 行 + 付款。

收款口径照旧线上 `services/pos/payment_settlement.py` 搬：方式限定（`qr` 归一到
`promptpay`）、金额必须为正且最多两位小数、实收不得少于应收、**找零只出自现金**
（非现金多付直接拒绝）、零元单不收任何付款。

班次照旧线上 `services/pos/shift.py`：开班判断在**行锁内**完成（两个收银员同时点开班
只有一个成功）；只能看和关自己的班；交班按"备用金 + 现金实收 − 找零"算应有现金，
再算实点差额，退款按负号从现金里扣。

销售与售后：一笔收银只落一笔销售、只产生一次库存影响，幂等锚是 `client_uuid`；没有
开班的销售被拒（否则交班对不上账）；退货必须有原单、不得退超，退货把库存加回来；
作废把库存还回去并留痕，已退过的单不允许作废。收银员只看到自己的单，老板看全店。

### 两处关键决定

1. **收银员卖货不需要库存岗位。** 销售引起的出库是销售的结果，不是在"管理库存"，
   所以库存服务对销售提供了显式的 `internal=True` 通道：调用方（销售）已经过了
   `cashier.write` 这道闸，一个用户动作只过一道闸。库存调整仍然要求库存岗位。
2. **收银价默认不含 VAT。** 顾客在收银台付的就是货架标价，`has_vat` 默认 False，
   不再往上加 7%；要按不含税价开票的场景由调用方显式传 True。含税拆分属开票侧。

### 实验室环境的一个坑

实验室**没有 worker**，`bench migrate` 排的搜索索引任务不会被执行，堆到 500 条后
`frappe.enqueue` 直接抛 `Too many queued background jobs`，后续写入全被挡。处理：
实验室 `common_site_config.json` 调高 `max_queued_jobs`（生产有 worker，不受影响），
并在需要时清队列（命令写在实验室记录里）。

### 第七批验证

- 离线规则 51 项通过（新增付款结算、找零、非现金多付、零元单、班次现金与差额）。
- 实验室真实 Site：默认收银台自动创建、未开班销售被拒、开班成功且同收银台第二个班被拒、
  现金 87 收 100 找零 13、重复提交同一个幂等锚只落一单、混合付款（刷卡 50 + 现金 60）
  找零 10、非现金多付被拒、少付被拒、当班汇总（2 笔 / 现金净额 / 应有现金 637）、
  收银员只看自己的单而老板看全店、退货把库存加回且不得退超、交班算出应有现金与差额、
  别人的班关不掉、作废把库存还回去、跨租户不可见。
- 六个探针全部 `problems` 为空（含全新 migrate 后重跑）；架构 0 违规；i18n 无新增。

## 第八批：公司主档、公司资料、客户（服务端）

落点：新增 `masters/`（`contracts.py` 纯规则、`company.py`、`customers.py`、`api.py`）。
新增 DocType `ERP Company`、`ERP Customer`。

### 一张表两种用法（公司主档 / 公司资料）

旧线上「公司主档」与「公司资料」是两个页面；服务端其实是同一份主体资料：主档是"登记过
哪些公司"的列表，资料页是"默认那条"的详情。新平台沿用这个形状，不另建两张表：
`list_companies()` 给主档列表，`get_profile()` / `update_profile()` 不带名字时落在默认
公司上（没有默认公司时自动建一条空档，否则新租户第一次打开资料页是一片空白）。

- 买方归一化照旧线上 `services/sales/buyer.py` 的保险层口径：类型决定字段意义，
  匿名买家清掉税号、非公司清掉分店、公司非分店态清掉分店号，落库前就清，不留脏数据。
- 开票前检查 `required_for_invoice()` 由开票侧调用，只回答"还缺哪些字段"（匿名直接
  不合格），不在这里决定能不能开票。
- 客户与公司都**停用不删除**，历史单据继续指向它们。

### 岗位口径（本批新定的两条）

1. **客户名录归销售岗位，收银员只读。** 依据旧线上：`clients` 的增删改在
   `routes/clients_routes.py`（网页页），POS 只挑已有 `member_client_id`、自己**不建**
   买方（`services/pos/sale.py`）；新平台收银员登录后只看到收银台、交接班与本人销售，
   所以收银在客户表上只需要"选得出来"——给读，不给建、不给改。
2. **公司资料是所有业务岗位可读、只有产品管理员可改。** 它不是买来的功能，是本租户
   自己的主体资料（开单要抬头与税号）；写权限只从产品管理员走。

### 两处必须补的授权缺口（本批踩到的坑）

- **能力判据过了、表权限没过**：`company.read` / `customers.read` 覆盖了业务岗位，
  但 `ERP Company` / `ERP Customer` 在控制面 `ENFORCEMENT` 里没有对应功能码，于是
  站点上没有任何角色拿到这两张表的 DocType 权限，`frappe.get_list` 直接抛
  `PermissionError`（探针里的表现是 `company_count` 变成异常字符串）。修法是把两张表
  挂进对应功能码，并用 `READ_ONLY` 覆盖把业务岗位降成只读。
- **产品管理员没有"自己家资料"的写权限**：`ADMIN_DOCTYPES` 只有 `User` / `Contact`，
  ERP 管理员能建员工却改不了公司资料。加 `ADMIN_EXTRA_DOCTYPES`（控制面）→
  `admin_extra_doctypes`（spec）→ 站点侧 `ensure_feature_roles` 追加授权，三处一起改，
  站点侧保留原有默认清单，老调用方不受影响。
- 顺带修掉一处手工 JSON 的双重转义：`erp_customer.json` 的 `party_type` / `branch_type`
  选项写成了 `"company\\nperson…"`，落库后是**字面反斜杠 + n**，任何建客户都会报
  `Buyer Type cannot be "company"`。改为 JSON 的正常换行转义。

岗位限制同时落在**服务层**与**表上**：直连 REST 不经过服务层的 guard，只在服务层拦
等于给员工留了绕开页面的路。探针读 `Custom DocPerm` 行把这条钉住。

### 第八批验证

- 离线规则 58 项通过（新增买方归一化与开票缺项）。
- 实验室真实 Site：
  - 默认公司自动出现且资料页落在它上面；改前缀 `LAB` 生效；财年起始月 13 被拒。
  - 建第二个公司后主档列表 2 条，而资料页仍是默认那条。
  - 公司买方保留税号 `0105550000001` 与分店号 `00002`；匿名买家落库后税号与分店均为空。
  - 开票缺项：公司买方无缺；匿名买方为 `["party_type"]`。
  - 停用后默认列表看不到、带 `include_inactive` 看得到、恢复后回到列表。
  - 岗位：商品岗位不能读也不能改客户；收银员能读、不能建也不能改（`PermissionError`）；
    收银员能读公司资料、不能改；无关账号两个都读不到。
  - 表权限（读 `Custom DocPerm`）：`ERP Admin` 在公司表上 `read/write/create`；
    `ERP Sales` 与 `ERP Cashier` 只读；客户表上 `ERP Sales` 可建可改、`ERP Cashier` 只读。
  - 跨租户：站点 B 的公司与客户计数均为 0。
- 六个既有探针（权限、团队、商品、采购、库存、收银）**全部回归**为 `problems` 为空；
  架构检查 0 违规；`check-i18n --strict` 仍为存量 95 条（本批零新增）；
  `check-frontend-i18n --strict` PASS。

### 本批明确没做

- 「集成」（旧 `routes/erp_endpoints_routes.py` 等 8 个端点的 LINE 绑定与录单入口）
  登记为第九批，不在本批内假装完成。
- 公司 / 客户的页面与前端一行未写（用户明确前端留到最后一批）。
- 未部署、未推送、未合回主线；实验室改动只落在本任务自有卷上。

## 第九批 A：集成（LINE 绑定，服务端与通道侧）

落点：新增 `integrations/`（`binding.py` 网页侧、`handlers.py` 通道侧、`api.py`），
`hooks.py` 注册 `pearnly_line_handlers`，`access/rules.py` 加 `integration.read` /
`integration.manage`。**没有**新增 DocType：绑定的表是共用层既有的
`Pearnly LINE Binding` 与 `Pearnly LINE Bind Code`（`pearnly_core.line_binding`）。

### 「集成」到底是哪三块，以及为什么只做了第一块

旧线上「集成」页（`src/home/page-integrations.ts`）有三块，逐块核对来源后是这样：

1. **LINE 绑定** —— `POST/GET/DELETE /api/line/erp/binding(-code)`，就是本批做的事。
2. **对外 ERP 端点清单** —— `routes/erp_endpoints_routes.py` 的 8 个节点：适配器
   (`webhook | mr_erp | flowaccount`)、加密凭据、`auto_push`、SSRF 校验、按员工指派
   端点、共享 Express 端点。**这不是 ERP 自己的表**：它就是 Cowork 那本"推送到外部
   ERP"的连接账本，另一个窗口的工作树里已经有 `Cowork ERP Connection` /
   `Cowork ERP Mapping` / `Cowork ERP Snapshot` / 推送记录与推送写者锁。
3. **连接日志** —— 推送状态，唯一来源同样是 Cowork 的推送账本。

决定：**第二、三块不在本 App 再建一份。** 同一件事存两份必然漂移（推的是哪条连接、
哪条日志算数都会有两个答案），而且另一个窗口的连接包还没合回主线 —— 现在按它施工
等于把一个未合并的契约冻进 ERP 侧。ERP 侧要的只是"投影"：员工能看到/管理被指派的
连接。等该包合回主线，再按同一份记录接（第九批 B）。

上一段会话在记录里写过"集成是 ERP LINE 绑定与录单入口，与 Cowork 的外部 ERP 推送
不是同一件事" —— 逐行读源码后更正：**绑定与录单是 ERP 的，端点清单与日志是 Cowork 的**，
两者在旧线上是同一页的两个页签，在新平台归两个 App。

### LINE 绑定的口径（照旧线上，不改语义）

- **自己绑自己**：码只发给当前登录账号，服务层不收"绑谁"的参数。少一个参数就少一条
  越权路径（旧线上页面也只发当前账号的码）。
- **一位员工同时只有一个有效码**：再发一次，旧码立刻作废（共用层既有的行锁语义）。
- **10 分钟有效**，一次性消费；已用、过期、不存在都返回"码不合法"。
- **解绑只解自己**；聊天里打解绑词也能解（`ยกเลิกการผูก` / `ยกเลิกการเชื่อมต่อ` /
  `解绑` / `unbind`，与 DMS 通道收同一批词，老用户按旧话术打字照样能解）。

### 三处主动的改动（都与旧线上不同，写清楚）

1. **渠道没配好就不发码。** 旧线上 `new_code` 不看渠道配置：码发得出来、官方号却扫不
   进去，用户拿着一个用不了的码干等。现在 `start()` 先向 LINE 问官方号身份，问不到
   就在这里失败。
2. **官方号身份去问 LINE，不抄进配置。** 新增共用函数 `pearnly_core.line.public_account()`
   （`GET /v2/bot/info` → 名字 / Basic ID / 加好友链接）。抄进配置就会与真实账号漂移
   （换号、改 ID 后台还显示旧的，用户扫码加错人）。Basic ID 为空或问不到一律抛错，
   不返回"basic_id 为空"的空壳。
3. **录单没迁就直说。** 绑定成功之后再发消息（含图片/文件）会收到一句"这个通道还没
   开放"，而不是静默丢弃、也不是假装收下。

### 与 DMS 花名册那处同类代码的关系

`pearnly_dms/operators/line_account.py` 里有同一次 LINE 调用的另一份实现（多一个本地
二维码、错误话术按 DMS 的键）。**本次不动它**：DMS 是已稳定产品，它有自己的窗口与
测试（`operators/test_line_account.py` 直接 patch 该模块的 `requests`）。已在
`public_account()` 的注释里登记：下次动 DMS 时把它改成"调用本函数 + 自己补二维码"，
这样 LINE 调用只有一处。现在没有搬，是为了不把改动的面扩到别人正在守的模块。

### i18n

- 本批新增 8 条文案进术语表（`scripts/i18n/messages.tsv`）：6 条归
  `pearnly_erp_portal`、2 条归 `pearnly_core`，四语言齐全，`i18n-build.py` 生成了
  **`apps/pearnly_erp_portal/pearnly_erp_portal/translations/`**（该 App 的第一份包）。
  LINE 通道的话术按旧线上照搬泰文（"รหัสไม่ถูกต้อง"、"กรุณาผูกบัญชี ERP ก่อนใช้งาน"）。
- `解绑` 是**用户手打的命令词**，登记进 `scripts/i18n/keep-cjk.tsv`（与 DMS 同一条理由）。
- **仍存的缺口（归语言包批）**：ERP 门户在第二至第八批写的其它服务端消息与 DocType
  标签目前只有英文 msgid，没有 zh/th/ja 译文 —— 泰文租户会看到英文。这是收尾批语言包
  的范围，本批不半途补。

### 导航还没有这一页（有意）

`pearnly_core/desk/navigation.py` 的 ERP 目录里已有 `pearnly-integrations`，但
`AVAILABLE_PAGES` 还没放它 —— 页面本身要到最后一批才做，先放出去就是一个点不开的入口。
收尾批做 `pearnly-integrations` 页面时，同时要把它加进 `AVAILABLE_PAGES`，并把
`_can_open_page` 从"按 DocType 权限"改成按集成能力判断（现在那里是按 DocType 判断的）。

### 第九批 A 验证

- 离线规则 62 项通过（新增集成能力：任何业务岗位都能绑自己的账号；没岗位的不能；
  管理员角色本身不开这一项；解绑词表与 DMS 一致）。
- 实验室真实 Site（**假 LINE 传输**：官方号查询与所有回复在进程内替换，不出网、不碰真
  渠道）：
  - 渠道未配置时发码被拒（`ValidationError`，"LINE channel is not configured"），
    官方号身份也拒绝返回。
  - 配好渠道后发码：6 位数字、`expires_in=600`、机器人身份 `@labbot` 与加好友链接。
  - 再发一次：旧码立刻作废（消费返回 `None`）。
  - 把码发给机器人 → 绑定落库为 `active`、账号是本人，事件经**真实传输层分发**
    （`pearnly_core.line._dispatch` 读 hooks）处理，证明 `hooks.py` 的注册生效。
  - 同一个码第二次使用被拒（一次性），另一个 LINE 用户不会因此多出一条绑定。
  - 网页侧状态为已绑定 → 解绑 → 未绑定；聊天里打 `ยกเลิกการเชื่อมต่อ` 也能解绑。
  - 绑定后发图片：回"这个通道还没开放"，不静默、不谎报。
  - 没岗位的账号：状态、发码、官方号身份三处全被拒。
  - 跨租户：站点 B 的绑定行数为 0。
- 七个既有探针（权限、团队、商品、采购、库存、收银、主数据）全部回归 `problems` 为空；
  架构检查 0 违规；`check-i18n --strict` 仍为存量 95 条（本批零新增）；前端 i18n PASS。

## 第九批 B：集成（LINE 录单服务端）

落点：新增 DocType `ERP LINE Intake`、`integrations/intake.py`，`handlers.py` 接上
图片/PDF 分支，`integrations/api.py` 加草稿接口；`pearnly_control` 的
`("erp","purchase")` 授权表加上这张新表（不这样做就是上批踩过的"表权限没给"）。

### 先看清楚旧线上这一套有多大（只读核对）

旧线上 ERP LINE 是 **20 个模块**：`webhook` / `flow` / `menu_cards` / `cards` /
`selection_messages` / `draft_view` / `draft_actions` / `internal_flow` / `intake` /
`push` / `target_flow` / `target_selection` / `target_preflight` /
`catalog_refresh` / `workspace_resolution` / `rich_menu` / `out` / `store` /
`route_contract` / `catalog_refresh`，外加一个 LIFF 前端
（`static/erp-line-intake/*` + `static/dist/erp-line-intake.html`，带字段渲染与四语言）。
其中：

- `push.py` —— "Push confirmed ERP LINE records through the owner's configured target"
  （确认后**推给老板配置的外部目标**）；
- `workspace_resolution.py` —— "Cowork-compatible workspace resolution"（草稿落在
  Cowork 的套账里）；
- `target_flow` / `target_selection` / `target_preflight` / `catalog_refresh` —— 选目标、
  拉对方科目目录、校验目标可用；
- `draft_actions.py` —— 草稿状态机（`draft` / `editing`，过期就提示
  "รายการหมดอายุ"，用户得重传）。

结论：旧线上 ERP LINE 的**终点是外部目标**，草稿与会话也都是 Cowork 那一套的表。
新平台的 ERP 有自己的采购记录，所以这一批把终点改成**建 ERP 采购记录**，不推外部
（对外推送是 Cowork 的连接与推送账本 + 单独售卖的功能码 `erp_push`，默认关）。
`target_*` 与 `catalog_refresh` 是"给外部目标用"的，随端点清单那一块一起等 Cowork
的连接包，不在 ERP 侧重建。

### 本批做的（可验证的一整条服务端链）

LINE 发来图片/PDF → 校验绑定与岗位 → 下载媒体 → **留底**（Frappe `File`，私有）→
落一条 `ERP LINE Intake` 草稿（状态 `received`）→ 人在页面补齐字段后**确认** →
调采购域建单（`source=line`、`image_sha256` 带上源文件指纹）→ 草稿转 `confirmed`
并指向那张单。也可以 `discard`（留文件、写理由）。

四处决定：

1. **草稿是可列表的持久记录，不是会过期的会话。** 旧线上草稿几十钟过期就得重传；
   "员工下班前传、老板第二天确认"是常态，所以落表（会话表仍然给 LINE 对话用）。
2. **同一份文件重复发只落一条草稿**：按内容 sha256 + 发件 LINE 用户去重（旧线上用
   识别记录指纹，新平台的单据还没识别，就用文件本身）。
3. **确认只能建一次**：`dedupe_key = line-intake:<草稿名>`，重复确认返回同一张单。
4. **谁能发**：要有采购岗位。销售方向（旧线上 `direction=sales`）等第十批的销售单据流，
   本批明确拒绝而不是假装支持。

### 明确没做

- **识别（OCR）与字段预填**：OCR 是付费调用（DMS/Cowork 各自的工作台已在用），而且识别
  工作台归 Cowork 那一摊。草稿落下时只有文件与来源，字段由确认时传入。
- **确认页面**：前端，归收尾批。服务端接口已经就位（列表/详情/确认/作废）。
- **Rich Menu、菜单卡片、目标选择、科目目录刷新**：随端点清单那一块（第九批 C）。
- 没有把草稿推给任何外部系统，也没有在本批改动 Cowork 的代码。

### 第九批 B 验证

- 实验室真实 Site（**假 LINE 传输** + 本地生成的**真** JPEG/PDF 夹具；不出网、不调付费
  OCR）：
  - 采购岗位的人传图 → 草稿 `received`、发件人是本人、方向 `purchase`、文件指纹等于
    内容 sha256、私有 `File` 已留底、文件名 `line-m1.jpg`。
  - 同一份内容再发一次 → 回"已经收过了"，草稿数仍为 1。
  - 再发一份 PDF（带 LINE 给的文件名 `invoice.pdf`）→ 第二张草稿，留底文件名保持前缀
    与扩展名（Frappe 对重名文件加随机后缀）。
  - 没有采购岗位的账号传单据 → 被拒（"这个账号不能传单据"），且**没有留底**。
  - 没绑定的 LINE 用户传单据 → 先让他绑定，不落草稿。
  - 确认草稿 → 建出采购记录：`source=line`、`image_sha256` 等于源文件指纹、草稿转
    `confirmed` 并指向该单。
  - 重复确认 → 同一张单，供应商名下仍只有 1 张（不会建两张）。
  - 作废草稿 → `discarded`；作废后再确认被拒（`ValidationError`）。
  - 岗位：收银员看不到这批草稿；采购岗位看得到。
  - 跨租户：站点 B 的草稿行数为 0。
- 八个既有探针全部回归 `problems` 为空；架构 0 违规；`check-i18n --strict` 仍 95 条
  （新增中文只有用户手打的解绑词，已登记在 `keep-cjk.tsv`）。
- 夹具踩坑（已记）：Frappe 会对图片做 EXIF 剥离、对 PDF 用 pypdf 解析，所以**假字节**会在
  框架层就失败 —— 第一版探针的"留底失败"是夹具问题，不是代码问题，换成真 JPEG/PDF 后通过。

## 第十批 A：销售单据契约（纯规则）

落点：新增 `apps/pearnly_erp_portal/pearnly_erp_portal/sales/`（`contracts.py`，纯规则）。
**没有** DocType、**没有**取号落库、**没有**页面：这一批只把口径钉住。

### 为什么先做这一层

旧线上销项单据是 31 个服务模块 + 5 组路由（列表/建单/详情/PDF/改/开出/作废/删草稿/
送审/批准/驳回/贷项/借项/转单/PromptPay，另有发送、设置、业务员、导出）。其中真正
"错了月底对不出账、且外面看不出来"的，是**金额与连号**：VAT 落在折前还是折后、价内价外、
整单折扣怎么摊到应税净额、WHT 按哪个基数扣、连号什么时候归 1。这些在旧线上都是纯函数
（`totals.py` / `wht.py` / `numbering.py` / `issue_gates.py` / `baht_text.py`），所以先原样
搬过来，在没有 Site 的地方就能断言。

### 已落的口径（逐条对应旧线上实现）

- **金额**：行级折扣（百分比优先于绝对额）+ 整单折扣；VAT 落在**折后**应税净额上；整单
  折扣按行金额比例摊到应税部分；折后不为负；VAT 价内时反算（`107 @7%` → 税 7.00、净
  100.00）；WHT 按折后净额算，得出净应付（`1000 + VAT70 − WHT30 = 1040.00`）。
- **预扣税档位**：0 / 1%（运输）/ 2%（广告）/ 3%（服务·专业）/ 5%（租金），票面标签带档率。
  次语目前只有英文 `WHT`：旧线上还有个"泰+中"档，**中文那句等语言包批**，不在这里写死。
- **泰文金额大写**（จำนวนเงินตัวอักษร）：整数逐段读、超六位递归 ล้าน、个位 1 读 เอ็ด、
  十位 2 读 ยี่สิบ、无 สตางค์ 收 บาทถ้วน。
- **连号**：前缀优先级（本次显式 > 公司资料 `doc_prefix` > 类型默认）、桶键（年 / 月 /
  永不）、展示号（`INV2026-00001` / `INV202609-00007` / `INV-00012`）。
- **开出前的闸**（都在**取号之前**跑，不过就不占号 —— 占了再退就是跳号）：买方完整
  （税票类要抬头/地址/公司税号/分店号，复用 `masters.contracts` 的同一份判定）、
  面额 ≤ 0 不开、收据类未收款不开。拒因顺序固定，调用方按第一个回话。
- 报价单不是税务凭证：฿0 的报价、未收款的报价都不拦。

### 本批的边界（按用户 2026-09-21 的指示）

- **没有碰任何共享文件**：只改 `apps/pearnly_erp_portal/**` 与本任务自己的
  `tests/test_erp_portal_access.py`、工作记录。`pearnly_core`、`pearnly_cowork`、
  控制面、`scripts/i18n` 一律未动 —— 另一个窗口正在迁移，等它合并后再动。
- 因此本批**没有**新增术语表条目：新代码里的用户可见文案（例如"这份单据已开出"这类
  错误话术）留到语言包批统一进包，不半途写死。

### 第十批 A 追加：状态 × 动作、红冲/补开、报价转票

同一天把同一层里剩下的判据也落了（都是纯函数，不连库）：

- **状态 × 动作**：`draft` 可改/可删/可送审/可直接开/可打印/可转票；`pending_approval` 可改、
  可批准、可驳回、可开；`issued` 只能作废、红冲、补开、打印；`void` 只剩打印。未知状态
  **一律不给动作**（fail-closed）。服务端按这张表拒绝，前端按它显示按钮 —— 前端不自己算。
- **红冲 / 补开**：只能冲**已开出**的原单（草稿、作废都不行），贷项 `CN`、借项 `DN`，
  各自走独立连号。
- **报价转开票**：只有报价单能转，只能转成税票/税票式收据/收据；转出来的是**草稿**，
  开出时才取号（报价单不占发票号）。

### 第十批 A 验证

- 离线规则 79 项通过（新增 12 + 5 项：状态机/红冲/转票；此前的价外/价内金额、行折扣、整单折扣摊税、折扣不为负、
  泰文大写八种写法、WHT 标签、三种归零策略与桶键、前缀优先级、零元税票被拦、未收款收据
  被拦、已收款放行、报价不拦、买方不完整的税票被拦）。
- 架构检查 0 违规（纯规则层不连库、不读配置）；`check-i18n` 仍 95 条、零新增。
- 说明：本批**没有** Site 级验证 —— 它还没有落库与接口，实验室探针要等第十批 B。

### 共享文件的核对结果（为什么第十批 B 要等）

逐文件核对另一个窗口（`codex/cowork-backend-migration`，676 个文件）后确认，它**确实在改**
以下共享文件，本任务在它合并前一律不碰：

- `apps/pearnly_control/pearnly_control/features.py`（功能目录与 DocType 授权表）
- `apps/pearnly_core/**`（`desk/navigation.py`、`translations/*`、`www/pearnly-app.html`、Desk 页面与样式）
- `scripts/i18n/messages.tsv`（术语表）

影响：**第十批 B 要新增的销售单据 DocType 需要控制面那行授权**，所以在它合并之前只能继续
做纯规则层；`pearnly_core` 与术语表也同理（本批因此没有新增任何词条）。第十批 A 的两次提交
都只落在 `apps/pearnly_erp_portal/**`、本任务自己的测试文件与这份记录上。

## 收尾批 A：框架外壳与第一页（商品数据）

用户 2026-09-21 在本机看到租户站点后定的口径：**先不做业务前端，先把大框架固定**——
侧栏的父子分组与图标、左上公司、左下用户、登录页走共享底座；然后一页一页做。
同时明确：**搜索(⌘K)、通知、列表视图、已保存筛选器、过滤器都不要**，原生的东西可以放出来，
但产品真不具备的就不放。

### 侧栏：父子分组 + 图标（用户专门提醒的两点）

- **图标名必须真存在**。渲染规则是 `frappe.utils.icon(name)`：非 `es-` 前缀取
  `#icon-<name>`（lucide 精灵），`es-` 前缀取 `#<name>`（espresso）。**名字不存在就渲染空白**。
  逐条核对后发现原来有一批是空的：`stock` / `cart` / `organization` / `home` / `project` /
  `help` / `web` 全都不在精灵里。全部换成核对过的名字（`package` / `shopping-cart` /
  `building-2` / `house` / `folder-kanban` / `circle-help` / `globe`）。
- **分组用原生的 Section Break + child**：框架的 `Sidebar.find_nested_items()` 就是按
  "最后一个 Section Break + `item.child`"嵌套的，所以父项写 Section Break、子项写
  `child=1, indent=1`，不需要自建折叠控件。分组定义在 `GROUPS`（商品与库存 / 采购系统 /
  销售系统 / 主数据），与截图一致。
- **父栏一个总图标、子栏不带图标**（用户 2026-09-21 指定，参照 Desk 原生的权限/审计分组）：
  父项写 `indent=1` 有两个效果，都是原生的分支行为 ——
  ① `sidebar_item.html` 里 Section Break 只有 `indent != 1` 才画成那条细分隔线，`indent == 1`
  走「普通条目」分支，于是标题前面能放图标；
  ② `sidebar_item.js` 给**没有图标的子项**补 `list` 图标的条件是
  `!(item.child && item.parent.indent)`，父项 `indent=1` 正好关掉这条，子项于是真的没有图标。
  折叠仍走原生的 `collapsible` + 右侧 drop-icon。实测：箭头能收起/展开，子项无图标、缩进正确。
- **没做好的页面不进侧栏**：`AVAILABLE_PAGES` 是唯一开关，`_offered()` 同时看"做好了"
  "买了功能""有权限"三者。空组不显示标题（空标题会被当成坏了）。
- 分组标题要**自己翻**：原生给条目做的是 `_(item.label)`（`frappe/boot.py`），而我们在
  `extend_boot` 里新造的 Section Break 不经过那一步 —— 不自己翻就会显示 `nav.group.*` 这种键（实测）。

### 商品数据页（第一页，定样板）

- 落点 `pearnly_erp_portal/public/desk/products.js` + core 的页面注册（原来只是占位）。
- **列表用框架自带的原生渲染器**（`frappe.pearnly.list()`）：它继承框架装的 BaseList +
  ListView 渲染，所以复选框列、`第 N 项/共 M 项`、`20/100/500/2500` 分页、空状态、行高与
  移动端样式全是原生那套；而"列表视图切换 / 已保存的筛选器 / 过滤器 / 排序"属于 ListView 的
  另外几条装配路径（`setup_view_menu` / `setup_standard_filters` / `setup_filter_area`），
  按用户 2026-09-21 的口径**产品不放这些**，走 BaseList 正好天然没有。
  （第一版我写成了自绘 DataTable 表格 —— 与参考图不一致，用户当场指出后改成原生列表。）
- 复选框列留着的理由：它后面挂着**真有的功能** —— 批量启用/停用选中的商品；没有实际动作的
  选择框不该放出来。右上角主按钮平时是「新建商品」，选中行时切成批量动作（原生既有做法）。
- 取数走我们自己的接口（`api_list_products` / `api_count_products`），因此成本遮蔽仍是
  服务端裁好的那份投影；行点击进 Dialog 编辑（原生表单那侧还没做字段级成本权限）。
- 文案走 `product.*` 命名空间（`browser_text` 白名单里新增一条，指向 ERP 那个 App）。

### 过程中发现并修掉的真问题

1. **"参考成本 0.00" 是假的**：`default_cost` 是普通 Currency 列，存不下 NULL，"没设过"与
   "真的 0"在库里同一个值。加 `cost_set` 标记（与既有的 `price_set` 同一套理由），
   服务端写入时一起写，`COST_FIELDS` 遮蔽也带上它（否则看不到成本的人会读到"未设"这个假答案）。
   页面对 售价/参考成本 都按标记显示"未设"。
2. **`avg_cost` 是死字段**：DocType 注释说"由库存维护"，但全仓只有读、没有写，
   页面上永远显示 0。已从列表去掉（库存页会正确地按 `cost_known` 显示成本），
   登记为待办：要么库存写入它，要么删掉这个字段。→ 收尾批 B 已删字段（旧线上没有这一列）。
3. **三处 UI 缺陷**：分组标题显示原始键（见上）、`product.col.code/barcode/unit` 三个键
   在术语表里漏了（页面因此显示键名），都已修。

### 验证

- 站点级（`qerp.localhost`，真实租户站点）：侧栏显示 首页 / 商品与库存(商品数据) /
  采购系统(采购记录)，父项一个图标、子项无图标、缩进正确、箭头可折叠；商品页是原生列表：
  复选框列、`1 / 1` 计数、`20/100/500/2500` 分页、右上角「新建商品」主按钮，且**没有**
  列表视图/已保存筛选器/过滤器/排序；建出一条商品后出现在列表；勾选一行后主按钮切成
  「停用选中的 1 个」并出现原生「已选1条记录」；点行打开编辑对话框；参考成本显示"未设"
  而不是 ฿0.00。
- 计数接口 `api_count_products` 用 `frappe.get_list` 而不是 `frappe.db.count`：后者**绕过
  DocType 权限**，而本站点商品表是按岗位授权的。商品是主数据、量级不大，用列表长度换权限正确。
- 离线 96 项通过、架构 0 违规、i18n 仍 95 条（新增文案全部进术语表与语言包）。
- 十个实验室探针全部 `problems` 为空（单独跑；连跑会因共用站点互相影响计数）。

## 收尾批 B：商品数据页的工具栏、Excel 导入与字段对齐

用户口径（2026-09-21，逐条落实）：工具栏顺序是 **刷新 | Excel 导入 | 新建商品**（新建是页面
主按钮，永远最右）；Excel 导入是旧项目本来就支持的功能；商品字段"看旧项目是哪些字段，
不要做漏也不要做多"；**旧项目的文案一律不搬，只看字段**。

- 工具栏：`page.add_inner_button()` 两个原生按钮，新建仍是 `set_primary_action` 的主按钮。
  窄屏（<992px）时框架把 inner button 收进原生「菜单」，这是 Desk 自带行为，没有另做折叠。
- **Excel 导入**（新）：`products/workbook.py` 纯解析层（表头按中/泰/英别名认、逐行校验、
  空行跳过、行级错误），`service.import_products()` 逐行调**同一个** `create_product`
  入库 —— 导入不绕开手工录入的任何一条规则；`api_import_products(file_url)` 读回文件内容。
  前端用原生 `frappe.ui.FileUploader`（只收本机 .xlsx、5MB 上限；关掉"库/摄像头/网页链接"，
  导入读的就是客户手里那张表），失败行用 `frappe.msgprint` 按行号列出。
  一行的错不连累整批：失败的行走 `frappe.db.rollback()` 后记行级错误，其余照常入库。
- **字段对齐旧线上 `services/sales/products.py::_COLS`**（数据模型，不是旧表单）：
  `product_name(旧 name_th)/name_en/name_zh/code/barcode/qr_payload/unit/base_unit/
  category_id/unit_price/vat_applicable/image_url/min_stock/default_cost/is_active/
  track_batch/track_expiry/is_weighed`；删掉旧线上没有的 `note` 与死字段 `avg_cost`。
  保留我们自己的 `price_set`/`cost_set` 两个"设过没有"的标记（Frappe 的 Currency 列存不下
  NULL，旧线上靠可空列区分的"没设价 / 真的 ฿0"在这里只能靠标记还原）。建/改对话框共用
  一份 `product_fields()`，字段与上表一一对应。
- **编码改成系统发**（旧线上 ERP 门户里编码那格就是只读的，`services/erp/product_numbers.py`
  发 `P-` + 六位）：留空即 `contracts.next_code()` 接着已有最大号往下发（认不出的旧编码跳过
  不参与计数），建与导入都走这条；**已建成的编码不可改**（`update_product` 收到不同编码就
  报错），否则历史单据行会指向另一个货。对话框里这一格只读并标注"自动生成"。
- 单位与 `base_unit` 的联动、以及"有库存时不许改单位/停用"的闸（旧线上 `guard_change`）
  属库存域，随库存页那批处理，本批不动。

### 验证（本批）

| 时间 | 基线 | 环境 | 命令 | 期望 / 实际 / 失败 | 覆盖 |
| --- | --- | --- | --- | --- | --- |
| 2026-09-21 22:3x +07 | `2b4cf18` + 本批未提交 | 主机 Python，无 Site | `python3 tests/test_erp_portal_access.py` | 通过 / 103 项通过（6 项跳过：`openpyxl` 只在 bench 里） / 0 | 新发码规则、成本遮蔽、导入解析规则 |
| 同上 | 同上 | 本仓 | `scripts/check-architecture.py` | 0 违规 / 0 违规 / 0 | 解析层不连库、不越边界 |
| 同上 | 同上 | 本仓 | `i18n-build.py --check`、`check-i18n.py --list` | 新增文案全进术语表与语言包、改动文件真实发现 0 / 一致 / 0 | 导入的中文表头别名按"输入词"登记进 `keep-cjk.tsv` |
| 同上 | 同上 | 实验室两站点，`migrate` 后真实行为 | `erp-pos-lab-products-verify.py` | `problems` 为空 / 为空 / 0 | 发码 `P-*`、编码锁、导入（带码行照收、无码行发号、坏行只跳过自己）、字段不多不少 |
| 同上 | 同上 | 实验室站点 A | 其余九个探针逐个重跑 | 全部 `problems` 为空 / 为空 / 0 | 发码与字段改动未打破前九批 |
| 2026-09-21 22:4x +07 | 同上，站点已 `migrate` + `clear-cache` + 重启 web | `qerp.localhost`（真实租户站点） | 内置浏览器人工走查 | 见下 / 一致 / 0 | 工具栏、导入全链路、字段与只读编码 |

浏览器实测（`qerp.localhost`，1440×900）：工具栏是 **刷新 | Excel 导入 | 新建商品**；
「Excel 导入」走原生上传弹窗（只有"我的设备"，无库/摄像头），上传一张含 2 行的合成表格后
提示「已导入 1 个商品」，并把第 3 行的坏值按行号列出（`该值不是数字`），列表随即刷新出
`ทดสอบ น้ำปลา`（系统发的 `P-000002`）；「新建商品」不填编码保存得到 `P-000003`，
新建与编辑对话框都是 12 个字段、编码只读并标"自动生成"、售价与参考成本空着显示"未设"。

未做/未验：库存页那批才处理的单位联动与"有库存不可改单位/停用"；导入只认单表（多 sheet
取第一个）；上传的文件会留在租户的 File 表里（旧线上传完即弃，未处理，登记为差异）。

## 收尾批 C：模板下载、图片上传按钮、条码与二维码扫码

用户口径（2026-09-21）：商品页要能下载导入模板；条码扫描器要补上；图片要上传按钮；再做一个
二维码扫码解码。

- **模板下载**（`api_import_template`，走 `binary` 直接回文件内容，不在租户里留 `File`）：
  表头取**界面上那几句**（`product.field.*`，按站点语言渲染 —— 泰文站点下载到的就是泰文表头），
  顺序与字段顺序一致；**只有表头、不留示例行**（示例行会被当真数据导进来）。
  解析层的表头认领表因此扩成三套：字段名、历史写法（旧表格里的中/泰/英词）、界面标签（四语）。
  模板与"能认的表头"同出一张表（`workbook.FIELD_LABELS`），不会各造一套；空模板导入会明确
  报"这个文件没有数据行"，不默默无事发生。
- **图片**：`image_url` 从纯文本框换成原生 **Attach Image** 控件 —— 上传按钮、只收图片、
  缩略图预览都是框架自带的，落库仍是同一个 URL 字段（旧线上也是这个字段）。
  文件保持**私有**（也符合租户隔离），读权限靠服务端 `_attach_image()` 把它挂到这条商品上：
  不挂的话它是"私有且无归属"的 File，按框架的 `file.has_permission` 只有上传者本人读得到，
  老板传的图店员会看到裂图。外链地址不碰。（试过让它上传成公共文件：这条路对非系统管理员
  直接被框架挡下，且公共文件不符合租户隔离，已放弃。）
- **扫码**（新增 `public/desk/scan.js`，条码与二维码共用）：条码/二维码两个格子各加一个
  扫码按钮（原生输入框 + 原生按钮，图标用框架的 `es-line-camera`），点开是原生弹窗 +
  `<video>`，解码只用浏览器自带的 **BarcodeDetector** —— 桌面 Chrome/Edge、安卓 Chrome、
  Safari 17+ 都带，不引第三方 wasm 解码器（省一份下载与一份安全面）。没有这个能力的浏览器
  **不放按钮**（能力探针是异步的，页面先 `scan_ready()`）；相机在关窗、扫到、出错三种情况下
  都会停；每帧补一次 `play()`（有的浏览器会静默不开始播放）。
  扫码枪走的是同一个输入框（它就是键盘），但**打完补的那个回车不会顺手保存表单**。
- **没有搬的旧扫码件**（登记为缺口，要的话另排）：旧线 `static/scan/*` 的楔子（判"这一发是枪
  打的还是人打的"、独占订阅避免枪输入漏到别处）、成功视觉与提示音、手电筒、多帧确认、
  以及 ZXing 的 WASM 兜底（供不支持 BarcodeDetector 的浏览器）。

### 验证（本批）

| 时间 | 基线 | 环境 | 命令 | 期望 / 实际 / 失败 | 覆盖 |
| --- | --- | --- | --- | --- | --- |
| 2026-09-21 23:0x +07 | `1a9b644` + 本批未提交 | 主机 Python，无 Site | `python3 tests/test_erp_portal_access.py` | 通过 / 106 项通过（6 项跳过） / 0 | 表头三套别名、模板字段顺序、模板标签都在术语表里 |
| 同上 | 同上 | 本仓 | `check-architecture.py`、`i18n-build.py --check` | 0 违规、PASS / 一致 / 0 | 新 JS 只进 `app_include_js`，中文表头别名按输入词登记 |
| 同上 | 同上 | 实验室两站点，`migrate` 后真实行为 | `erp-pos-lab-products-verify.py` | `problems` 为空 / 为空 / 0 | 模板"下载→填一行→传回"闭环、空模板被拒、图片挂到商品、外链不碰 |
| 同上 | 同上 | 实验室站点 A | 其余九个探针逐个重跑 | 全部 `problems` 为空 / 为空 / 0 | 本批改动未打破前九批 |
| 2026-09-21 23:1x +07 | 同上 | `qerp.localhost`（真实租户站点） | 内置浏览器人工走查 | 见下 / 一致 / 0 | 扫码两条路、图片上传、模板按钮 |

浏览器实测（`qerp.localhost`，Chrome 153 / 1440×900）：

- 扫码：条码格与二维码格各有一个扫码按钮。用**canvas 伪摄像头**（画面上画好一张真二维码与
  一张真 EAN-13，`canvas.captureStream()` + 覆盖 `getUserMedia`，真实摄像头未开）走完整链路：
  二维码扫出 `PEARNLY-QR-0001` 落进「二维码内容」，EAN-13 扫出 `8850000000010` 落进「条码」，
  弹窗自己关、相机停。第一版 `video.play()` 被浏览器静默吞掉（视频停在 paused、解码器报
  "Invalid element or state"），改成每帧补 play 后复测通过。
- 图片：点「添加文件」→ 选一张 PNG → 上传成功，字段显示 `/private/files/qr-fixture.png`；
  保存后库里 `image_url` 就是它，且 `File.attached_to_doctype=ERP Product`、
  `attached_to_name=PRD-00004`（读权限跟着商品走）。
- 模板：弹窗里多一个「下载模板」按钮（服务端现生成，表头随站点语言）。
- 走查用的测试商品与上传的图片已删除，站点回到原来的 1 条商品；伪摄像头只在页面里活着，
  刷新即消失（没有落到磁盘或数据库）。

未做/未验：真实摄像头与真机（Mac 内置摄像头、安卓 Chrome、扫码枪）未验，属真机验收；
WASM 兜底与旧楔子未搬（见上）；模板只认单表（多 sheet 取第一个）。

## 收尾批 D：扫码反馈（官方声音 / 绿闪 / 灯光）、扫码业务逻辑、去掉称重

用户口径（2026-09-21）：扫码成功的声音要用平台自带的；动画与灯光效果也要；扫码的业务逻辑
要补；**称重商品不做，去掉**。

- **成功声音用平台自带的**：`frappe.utils.play_sound("submit")` —— 跟着账户的静音开关走
  （`frappe.boot.user.mute_sounds`），不再像旧项目那样自己合成一段 beep。
  挑声音时发现：frappe 的 `hooks.sounds` 里 `chime` 是**注释掉的**，选它等于没声音（页面里
  没有 `#sound-chime` 这个 `<audio>`）—— 选了 `submit`，它确实播了出来（见验证）。
- **动画与灯光**：`desk/scan.css` + 扫到那一下给画面加一帧**绿闪 + 对勾**（420ms，
  `prefers-reduced-motion` 时只留颜色不做缩放），手机上再 `navigator.vibrate(60)`。
  相机**手电**按钮只在 `track.getCapabilities().torch` 为真时出现（没有的机器不放死按钮），
  点了走 `track.applyConstraints({advanced:[{torch:true}]})`。
- **扫码的业务逻辑**：新增 `find_product` / `api_find_product` —— 扫到码**当场**查四处
  （编码、条码、二维码内容、售卖单位上的条码），只认在架商品，自己不算撞自己，
  权限与列表同一条（`products.view`）。扫到的码属于别人时当场弹原生提示：
  「这个码已经被占用 / 已经是「X」（编码 Y）」+ 一个「去改那一条」，点它就关掉当前对话框、
  打开那一条商品（旧线上也是当场拦并给"去编辑那个商品"的出路，只是它查的是三条索引）。
  手输路径不变：保存时服务端仍按唯一索引拦。
- **去掉称重**（`is_weighed`）：DocType、服务端、导入解析（表头/别名/FLAG_FIELDS）、页面字段、
  术语表一并删掉，模板表头从 17 列变 16 列。库里的旧列留着（Frappe 不删列，字段已从 meta 移除）。
- **仍然没搬的旧扫码件**（登记为缺口）：旧线 `static/scan/scan-wedge.js` 的楔子（判"这一发是
  枪打还是人打"、独占订阅防漏键）、多帧确认、ZXing 的 WASM 兜底，以及旧的成功视觉
  （那张"商品卡飞进购物车 +1"的动画是收银台的语义，商品表单用不上）。

### 验证（本批）

| 时间 | 基线 | 环境 | 命令 | 期望 / 实际 / 失败 | 覆盖 |
| --- | --- | --- | --- | --- | --- |
| 2026-09-21 23:4x +07 | `51651a2` + 本批未提交 | 主机 Python，无 Site | `python3 tests/test_erp_portal_access.py` | 通过 / 106 项通过（6 项跳过） / 0 | 表头三套别名与模板顺序（称重已消失） |
| 同上 | 同上 | 本仓 | `check-architecture.py`、`i18n-build.py --check` | 0 违规、PASS / 一致 / 0 | 新 JS/CSS 只进 hooks 清单 |
| 同上 | 同上 | 实验室两站点，`migrate` 后真实行为 | `erp-pos-lab-products-verify.py` | `problems` 为空 / 为空 / 0 | 查重在四处的命中与"自己不算撞自己"、无码回空、无岗位查不了、字段集与模板列数（16） |
| 同上 | 同上 | 实验室站点 A | 其余九个探针逐个重跑 | 全部 `problems` 为空 / 为空 / 0 | 本批改动未打破前九批 |
| 2026-09-21 23:5x +07 | 同上 | `qerp.localhost`（真实租户站点） | 内置浏览器人工走查 | 见下 / 一致 / 0 | 声音、绿闪、手电、查重与"去改那一条"、称重字段消失 |

浏览器实测（`qerp.localhost`，Chrome 153；仍用 canvas 伪摄像头，真实摄像头未开）：

- 平台声音确实响：把 `frappe.utils.play_sound` 包了一层记录，扫到那一刻收到 `["submit"]`，
  且 `#sound-submit` 的 `play()` 返回成功（不是被静音/被拦）。
- 绿闪与对勾：扫到后扫码窗的 `.pearnly-scan-stage` 带上 `pearnly-scan-done`，`scan.css` 已加载。
- 手电：伪摄像头自报 `torch: true` 时底部出现「灯光」，点一下发出
  `{"advanced":[{"torch":true}]}`，按钮转成主色；不报能力时**没有**这个按钮。
- 查重：扫一个已属于别的商品的二维码，当场弹「这个码已经被占用 / 已经是「ทดสอบdup」（编码 —）」
  并给「去改那一条」，点它关掉建品表单、打开那一条商品的编辑表单。
- 称重商品那一格在整个表单里已经没有。
- 走查造的临时商品已删除，站点回到原来的 1 条商品；伪摄像头只活在页面里，刷新即消失。

未做/未验：真实摄像头、真机震动、扫码枪三类真机验收；旧楔子与 WASM 兜底未搬（见上）。
