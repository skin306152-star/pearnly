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
| 客户 | `buyer-clients` | `routes/clients_routes.py`（6 个端点），归属待核对 | `pearnly-clients` | 与「公司主档」须区分 |
| 公司主档 | `clients`（ERP 入口下改标签为「公司主档」） | `routes/clients_routes.py`（6 个端点），归属待核对 | 新目录缺此项 | 待定落点 |
| 公司资料 | `company` | 待核对（`company-profile.ts` 对应服务端） | `pearnly-company` | 待迁移 |
| 团队管理 | `erp-team` | `routes/erp_team_routes.py`（7 个端点，`/api/erp/team`） | `pearnly-team` | 待迁移 |
| 集成 | `integrations` | `routes/erp_endpoints_routes.py`（8 个端点）等 | `pearnly-integrations` | 待迁移 |

### 本批核对到的三项关键差异

1. 新平台 ERP 目录同样是 14 项，但内容是旧截图 14 项去掉「公司主档」再加上「首页」（见 `apps/pearnly_core/pearnly_core/desk/navigation.py` 的 `PAGES['erp']`）。**项目数相同不等于范围一致**，不能按名称对齐。
2. 旧 ERP 里「客户」与「公司主档」是两个不同页面：`clients-navigation.ts` 在 erp/pos 入口下把 `clients` 路由整体改标签为「公司主档」，并额外显示 `buyer-clients` 作为「客户」。新目录只有一页 `pearnly-clients`（标签为「客户」），落点需要拆开或明确取舍。
3. 旧 ERP 的「集成」是 ERP LINE 绑定与录单相关入口（`erp_endpoints`、`erp_team` 等），与 Cowork 域的外部 ERP 推送不是同一件事，不得混为一谈。

## 变更与决定

### 批次计划

按批次施工，每批自带验证与提交，做完一批再开下一批。

| 批次 | 内容 | 状态 |
| --- | --- | --- |
| 第一批 | 线上基准、14 项范围与入口对照、隔离实验室、权限缺口复现 | 已交付 `37d96a7` |
| 第二批 | ERP 服务端权限基线：岗位 / 能力 / 取数范围，采购接口按权限取数 | 本批 |
| 第三批 | 团队管理与收银员：新增员工、发功能角色、收银台分配、同一登录口落地 | 待开 |
| 第四批 | 商品、库存、入库、出库、收发存统一口径 | 待开 |
| 第五批 | 采购记录、供应商、采购设置、销售记录（金额与库存联动） | 待开 |
| 第六批 | POS 收银台与交接班（借 ERPNext 前端体验，后端走 Pearnly） | 待开 |
| 第七批 | 语言包补齐、浏览器与真机验收、历史数据演练、整合验证 | 待开 |

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
- 剩余问题 / 阻塞原因：仅 `purchase` 一个功能接入了能力判据，其余功能的表与接口尚未纳入；员工拿功能角色这一步目前只在实验室脚本里模拟，产品侧的团队管理未实现；收银台与交接班的岗位（`ERP Cashier`）未定义；旧线上运行开关与后台任务未核对；公司主档落点待定。
- 下一步：按批次表开第三批（团队管理 + 收银员账号与统一登录落地），随后第四至第七批。
