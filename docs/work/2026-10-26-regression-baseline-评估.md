# 需求影响评估：回归升级为验收网（测试金字塔 + 变更选测）

> 分支 `pearnly/regression-baseline`（基线 `origin/main` 4561884f）。验收窗口 10-26 起草，交主会话审定后分工施工。
> 证据：仓库静态统计（命令见附录 A）、线上测试租户只读页面快照（Administrator，10-26）、本机回归实验室（线上同版镜像 fb1037ad + main 源码）。没做的写「未做」。

## 0. 需求原文与理解

- 用户原话（经主会话，10-26）：「回归测试其实就是随便点点能不能用？这纯属是扯淡和敷衍」。起因是 testcowork 个人页漏出框架原生的「权限」「创建用户电子邮件」按钮，冒烟没拦住。
- 主会话两条任务书（10-26）合并：用标准工程方法——测试金字塔 + 变更选测；A 结构防线、B 接口权限矩阵（主力）、C 页面 DOM 快照基线、D 少量端到端、E 变更选测两档、F 现有资产照用；先交方案，第 1 步「漏出入口清单」用 C 的自动快照产出，不手点。
- 我的理解：现在的冒烟只证明「页面打得开、不报错、够快」，不证明「页面上出现的东西是我们承接的」「数据结果对」「没权限的人被拒」。要把这三件事变成机器判定，多一个、少一个都失败，且每次改动只跑受影响的那部分。
- 不在本需求范围：修那两个按钮本身（work-followups 的外壳白名单在做）；控制面（主会话说不算）；付费识别、真 LINE、真手机、Windows 上 CC 桌面版界面（仍交验收按清单实测）；像素级截图比对。

## 1. 现有同类与复用（F：现有覆盖到哪、缺口在哪）

| 资产 | 现状（实数） | 覆盖到 | 缺口 |
|---|---|---|---|
| 测试文件 | 507 个：`tests/` 373、`companion/` 96、`apps/` 内 36、`scripts/` 2 | 纯逻辑、契约、离线流程为主 | 绝大多数不起站点、不按角色真调接口 |
| 权限纯规则 | `test_erp_portal_access.py` 106 例；`test_native_desk_contract.py` 24 例（菜单声明与原生 Page 权限对得上） | 岗位 → 能力映射、菜单声明 | 不执行接口本身：某个接口漏写守卫测不出来 |
| 公司隔离 | `test_cowork_scope_queries` 6 例、`test_dms_read_scope` 4 例，另有 `test_cowork_no_company_scope`、`test_cowork_source_lock_scope` 等零散几份 | 个别查询 | 没有「全部接口 × 跨公司」的系统覆盖 |
| 静态闸门 | `check-architecture.py`、`check-unbounded-queries.py`、`check-copy.py`、i18n 检查 | 包边界、无上限查询、文案 | 不管「页面上出现的按钮 / 菜单是否登记」 |
| 自动冒烟 | 登记表 45 个共用件、163 个入口（auto 49 / manual 113）；探针 page / scan / line / express | 页面能打开、无 JS 异常、时限、扫码有画面、LINE 回复、写 Express 读回 | 不比页面内容；一个账号跑；不分岗位、不分单据状态 |
| 候选镜像检查 | `ci-candidate-checks.sh`：offline / DMS flow lab / DMS snapshot lab 三条并行 | 候选镜像离线测试、DMS 真站点流程 | ERP、Cowork 没有真站点实验室 |
| Express 回归 | 101 本账套副本离线回归（`express-stloc / stock-entry / refund-regression.py`） | 写 Express 的落库形状 | 照用 |
| 线上只读冒烟 | `run.py --target production --readonly` | 上线后页面能开 | 同上，不比内容 |

结论：资产多，但集中在「逻辑对」与「能打开」两层，缺「接口按岗位真执行」与「页面应有状态」两层。方案在现有工具上扩展（冒烟驱动、登记表、check-impact、候选检查都沿用），不另起一套。

## 2. Frappe 原生能力（锁定 Frappe 16.33.0）

- 接口：whitelisted 函数可在站点进程里 `frappe.set_user()` 后直接调用，`frappe.PermissionError` 判拒；不经 HTTP 也能测权限（快）。HTTP 层另抽一小组核会话、CSRF、匿名。
- 页面：页头按钮、「…」菜单由 `page.add_*` / `frm.add_custom_button` 渲染进 DOM，菜单项预渲染（本次实测能读）；应用切换、用户菜单点开才渲染（`frappe.ui.create_menu`），快照里点开读项、不点项。
- 测试站点：`bench new-site` + `install-app` + `allow_tests`；DMS 两个实验室已是这个做法，ERP / Cowork 照搬。
- 不用：Cypress（锁定镜像不带）；用已有无头 Chrome 驱动（CDP，`scripts/smoke/drive.mjs` 同法），不引新依赖。

## 3. 按类通用

- 类 1「页面上的可点入口」：所有 Desk 页面、原生表单 / 列表 / 报表、个人页、团队管理、公司页、手机页、LINE 页，同一套快照规则。
- 类 2「白名单接口」：四个 App 全部 whitelisted 函数（core 67、ERP 245、Cowork 240、DMS 46，共 598；其中允许匿名 34 个单列）。
- 类 3「产品入口声明」：菜单（已有 `pearnly_navigation`，ADR-009）之外，页头按钮、菜单项、导航项也进声明；同一份声明喂外壳白名单（运行时拦）、静态检查（CI 拦）、快照基线（页面比对）。

## 4. 上下游数据

- 声明是单一来源：各产品 `hooks.py` 的 `pearnly_navigation` 每页加 `actions`（语义码）。外壳白名单、静态检查、快照比对三处只读它，不各存一份。
- 基线文件：`tests/baseline/pages/<产品>/<岗位>/<页>.<状态>.json`（只存操作区：按钮、菜单、导航、关键字段的语义码与可见性），进仓库、走评审。
- 不影响 Express、CC 桌面版载荷、生产数据。快照与矩阵在一次性实验室里跑（本机或 CI）；线上只读子集只用 Administrator。

## 5. 隔离与权限

- B 的矩阵本身就是权限测试：每个接口 × 每个岗位账号 × 本公司 / 别家公司。期望值从代码推出：接口里 `guard.require("<能力>")` 静态提取 → 能力 → 岗位（`access/rules.py` 等现成映射）；没有守卫的接口必须在声明里写明「匿名 / 仅登录 / 仅管理员」及原因，否则检查失败。
- 实验室账号与数据只在实验室；线上快照不给真实成员建会话（会挤掉他们的登录），`inventory.py` 已在线上拒绝非 Administrator。

## 6. 入口：网页、手机、LINE、拍照

- 网页 Desk：C 覆盖，桌面宽度 + 手机宽度（375，移动 UA）各一套快照。
- LINE / LIFF 页（`www/stocktake.html`、票据页）：C 里用 Guest + 票据会话打开取快照；真 LINE 推送仍是 manual。
- 拍照：不改相机，沿用现有 scan 探针。

## 7. 共用件与连带回归

- 新增的是测试设施，不改产品代码。`check-impact.py` 扩展（E）：改动文件 → 受影响的 whitelisted 函数、页面路由（按 JS 文件）、业务流（按登记），出「本次要跑」清单。
- 新探针 `inventory`、驱动 `inventory.mjs`、实验室 `regression-lab.yml` 登记进 `shared-parts.toml` 的冒烟设施一组。

## 8. 存储与成本

- 基线 JSON：约 33 个 Desk 页 + 约 155 个 DocType 列表 / 表单，× 岗位（ERP 8、Cowork 6、DMS 3）× 状态（最多 3），去掉无权限组合估 1,500 份 × 约 3 KB ≈ 5 MB。
- 无外部调用、无固定月费；CI 多一条候选检查并行车道。

## 9. 删除、作废、追溯

- 基线改动 = 产品入口改动：PR 里基线 diff 必须附任务记录链接并经主会话 / 验收审；比对失败输出「多了 / 少了 / 变了」清单 + 截图（PNG 只存 CI 产物）。
- 状态维度覆盖「草稿 / 已提交 / 已作废」：作废后还能点「编辑」就是缺陷。

## 10. 文案与官方 UI

- 不新增界面文案。基线用语义码而不是界面文字，三种语言同一份基线；文字由 `check-copy` 管。

## 11. 测试与验收

见第 13 节各期完成标准；第 1 步清单见附录 B。

## 12. 要问用户 / 客户的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 页面快照比对失败是挡合入，还是先出差异待审？ | 多出框架默认入口直接挡；其它差异先出清单待审，基线建齐两周后全部改为挡 | 一上来全挡会因基线初建不全频繁误挡 |
| 2 | 允许匿名的 34 个接口要不要逐个在声明里写明理由？ | 要，写明并加「匿名可调、但只做它声明的事」的用例 | 匿名接口是最大的暴露面 |
| 3 | 全量跑在 GitHub 候选检查里还是本机？ | 候选检查里（与 DMS 两个实验室并行）；本机只跑变更选测 | 本机全量要 1 小时以上（arm64 上模拟 amd64） |

## 13. 方案、分工与排期（机器耗时）

### 13.1 金字塔分层

| 层 | 内容 | 跑在哪 | 每次改动 | 全量 |
|---|---|---|---|---|
| A 结构 | 外壳白名单（运行时拦框架默认入口）+ 入口声明静态检查：代码里每个 `add_custom_button` / `add_menu_item` / `set_primary_action` / `set_secondary_action` / `add_inner_button` / `add_action_item` / 导航项都必须在声明里（现有调用点 ERP 73、Cowork 38、DMS 6、core 8） | 本地 + 分支 CI | 秒级 | 秒级 |
| B 接口 | 598 个白名单接口 × 岗位 × 公司隔离的权限矩阵（自动生成）+ 业务接口结果断言（写对、作废回滚、幂等） | 实验室站点进程内 | 受影响接口 + 核心集 | 全部 |
| C 页面 | DOM 快照：操作区 × 岗位 × 状态 × 桌面 / 手机，比对基线 | 实验室 + 无头 Chrome，三产品并行 | 受影响页面 + 首页 / 个人页 / 团队 | 全部 |
| D 端到端 | 每产品 3–5 条，断言最终数据（13.5） | 实验室；写 Express 那条仍用测试机 | 受影响的流 | 全部 |
| E 选测 | `check-impact.py` 扩展：文件 → 接口 / 页面 / 流 | 本地 | — | — |

### 13.2 A：入口声明 + 静态检查（与外壳白名单共用一份声明）

1. 声明格式：各产品 `pearnly_navigation` 每页下加 `actions`（语义码，如 `purchase.new`、`purchase.void`）；框架默认入口按「不在声明 = 隐藏」处理。
2. `scripts/check-entries.py`：扫 JS 里上述调用，取按钮的语义键（`__()` 里的键或 `data-action`），对不上声明就失败；存量先生成一次声明（只增不减），以后新增必须登记。
3. 与 work-followups 的外壳白名单对齐成同一份声明。

### 13.3 B：接口权限矩阵 + 业务断言

1. 生成器 `scripts/regression/api_matrix.py`：AST 扫全部 `@frappe.whitelist`，提取函数路径、是否匿名、调用的 `guard.require(...)` 与本产品 access 函数，推出「应允许的岗位」；没有守卫又没在声明里写明的直接失败。
2. 执行器在实验室站点里对每个接口 × 每个岗位账号用 `frappe.set_user` 调用：
   - 无权岗位：必须在校验参数之前抛 `PermissionError`（参数给空值即可）；
   - 有权岗位：断言「没被权限拒」（参数错误算通过权限层）；
   - 跨公司：带别家公司的单据名 / 公司参数调用读写接口，必须被拒或查不到。
3. 业务断言（每产品挑高频接口）：
   - ERP：采购入库、销售出库、出入库、盘点过账、作废回滚、幂等重放；
   - Cowork：录入写库、推送入队、读回状态、对账任务；
   - DMS：匹配、推送记录。
   每条断言库存数、单据状态、流水条数。
4. 跑法：一个站点进程内循环，约 598 × 岗位约 6 ≈ 3,600 次调用，估 3–5 分钟。

### 13.4 C：页面 DOM 快照基线（第 1 步已在用）

1. 已写好（本分支）：
   - `scripts/smoke/probes/inventory.js`：读操作区；点开应用切换 / 用户菜单读项，不点项，链接只记去向不点。
   - `scripts/smoke/inventory.mjs`：一个无头 Chrome 带会话依次打开，顺站内链接走，同类页最多看 2 个实例。
   - `scripts/smoke/inventory.py`：按产品 × 岗位建会话，输出 JSONL；`--target local|lab|production`，线上只许 Administrator；`--doctypes` 把本产品全部 DocType 列表页加进来。
   - `infrastructure/compose/regression-lab.yml` + `scripts/regression/lab_init.py` / `lab_users.py`：本机回归实验室，线上同版镜像，三个产品站点，每岗位一个中文界面账号。
2. 待补：
   - 状态维度造数（每类单据草稿 / 已提交 / 已作废各一张）；
   - 手机宽度；
   - 基线写出与比对（`--compare`）；
   - 并行（每产品一个进程）。
3. 实测耗时：线上 testcowork Administrator 48 页约 3 分钟（每页 3–4 秒，含读操作区与点开两个菜单）。按 17 个岗位账号 × 平均 40 页、三产品并行，估全量 15–20 分钟。

### 13.5 D：端到端（每产品 3–5 条，断言最终数据）

- ERP：
  - 采购入第二仓 → 调拨 → 销售出 → 盘点含新发现 → 库存按仓数对（与多仓方案验收脚本合并）；
  - 收银开班 → 卖 → 退 → 交班金额对；
  - 采购写 Express → CC 读回（测试机，沿用现有 express 探针）。
- Cowork：
  - 上传（用固定识别结果回放，不调付费识别）→ 审核 → 推送入队 → CC 读回；
  - 银行对账任务 → 结果行数与金额。
- DMS：沿用 DMS flow lab 现有流程，补最终数据断言。

### 13.6 E：变更选测两档

- 每次改动：`check-impact.py --plan` 出「受影响接口 / 页面 / 流」+ 必跑核心集（首页、个人页、团队管理、各产品主业务流 1 条、匿名接口全集），估 5–10 分钟。
- 全量：合入 main 前在候选检查里并行跑 B + C + D，估 25–35 分钟（与现有三条车道并行，阶段总时长取最慢一条）。

### 13.7 分工与机器耗时

| 块 | 谁做 | 依赖 | 机器耗时 |
|---|---|---|---|
| A 外壳白名单 | work-followups（已在做） | — | 已排 |
| A 入口声明格式 + 静态检查 | work-followups（同一份声明） | 白名单 | 约 2 小时 |
| B 生成器 + 权限矩阵执行器 + 实验室站点 | 验收窗口 | 回归实验室（已起） | 约 4 小时 |
| B 业务断言 ERP | erp-cc-direct（开发窗口） | 执行器 | 约 3 小时 |
| B 业务断言 Cowork | erp-cowork | 执行器 | 约 3 小时 |
| B 业务断言 DMS | 验收窗口 | 执行器 | 约 1 小时 |
| C 状态造数 + 基线写出 / 比对 + 手机宽度 + 并行 | 验收窗口 | 第 1 步清单 | 约 4 小时 |
| D 端到端 | 各产品窗口（ERP 并入多仓验收脚本） | B、C | ERP 3、Cowork 3、DMS 1 小时 |
| E check-impact 扩展 + 候选检查车道 | 验收窗口 | B、C | 约 2 小时 |
| 首次基线评审 | 主会话 + 验收 | C | 等人 |

合计约 26 小时机器时间（不含已在做的白名单与等人），三个窗口并行。

## 附录 A：数字从哪来

- 白名单接口：`grep -rn "@frappe.whitelist" apps/<app> --include='*.py' | grep -v /test | wc -l`；匿名：同上数 `allow_guest=True`。
- DocType：`find apps/<app> -path "*doctype/*/*.json"` 按目录去重（core 20、ERP 55、Cowork 73、DMS 7）。
- 自定义按钮调用点：`grep -rn "add_custom_button\|add_menu_item\|set_primary_action\|set_secondary_action\|add_inner_button\|add_action_item" apps --include='*.js'`。
- 测试文件：`find . \( -name 'test_*.py' -o -name 'test-*.cjs' \)`。

## 附录 B：第 1 步「漏出入口清单」

见同目录 `2026-10-26-regression-baseline-漏出入口.md`（快照自动产出）。
