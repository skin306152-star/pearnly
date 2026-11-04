# 产品地图：影响评估（主会话 11-04 已审定）

## 审定结论（主会话 11-04，以本节为准，覆盖下文对应建议）

1. 文件放 `scripts/impact/product-map/`；一期不合并 shared-parts，二期改成共用件入口从地图推出。
2. **「黄」不算有测试**（用户口径：回归要断言业务结果，不能只看能不能打开）。基线按真实数字立（评估粗数 185），只减不增。
   人工清单或真机清单只对标了「只能真机」的行有效，而且必须写明不能自动化的理由；这类行自动进部署后的真机清单。
   「上线必验」的行要么是绿，要么有探针。
3. 部署前配置对比：「应一致」类有差异就拦部署，不留绕过开关；其余只报告。密钥类只核有没有，不比值、不打印。
4. 95 个找不到调用方的接口：一期只分诊；待删的列清单交主会话，删除另走批次。
5. 页头动作单独成行、自动生成；只继承页面快照的算无测试，进基线。
6. 看板：按需 `ssh -L` 看静态页，零常驻。
7. F 块等 cc-entry-fixes 窗口把三项缺陷和方案 B 都合入后由主会话通知再开；C 块完成时告诉主会话，由它定 E 块是否另开窗口并行。
8. 基线清零先补 11-03 出事的三处：LINE 卡片「แก้ไข」（逐个正式租户的探针）、切换站点、添加公司；后两处的测试由 cc-entry-fixes 窗口在修复时补，
   地图登记时引用它们的测试 id，不重复写。
9. 每块走全闸门 → 推分支 → Development checks 绿 → 快进合入 main，合入后报一次；新增检查步骤实测耗时写进报告，目标 5 秒以内。

## 施工记录

| 块 | 提交 | 合入 main | 新增检查耗时 | 备注 |
|---|---|---|---|---|
| A 发现器 + 行校验 | 6d750c8f9 | 已合入（11-04） | CI 上那步约 1 秒 | 308 个锚点、52 个配置键 + 1 个前缀族；本地全闸门 180 步通过 |
| B 地图数据 | 8199d5547 | 已合入（11-04） | — | 327 行（ERP 153、Cowork 174）；基线 390 条「行@端」（200 行），页头动作 131 行占 262 条 |
| C 闸门 | 268702bfe | 已合入（11-04） | CI 上那步约 5 秒（含自测） | 本地全闸门 181 步通过；之后去掉重复核真实地图的自测，自测 0.05 秒，闸门本机 0.67 秒 |
| E 部署接线 | 5fe8fa287 | 已合入（11-04） | 两个新测试步本机共约 0.2 秒（不跑站点） | 导出随镜像；部署前配置对比（演练、构建前、激活前）；回读逐租户上线必验探针；本批真机单 |
| E 合入 | 5fe8fa287 | 已合入（11-04） | CI 上两步各约 1 秒 | — |
| D 接口分诊 | d863f8d31 | 已合入（11-04） | 闸门本机 0.67 → 1.2 秒；CI 上那步约 4 秒 | 见下方「D 块」 |
| D 核实拼名调用 | 63b1a3792 | 待合入 | 闸门本机约 1.5 秒 | 51 个里 8 个真拼名（补识别规则），43 个归兼容 / 待删 |
| G 看板雏形 | dccb5a8f3 | 已合入，11-04 按用户口径撤掉 | — | 用户：「我看不懂，我也用不上，我的产品100%全是ai做的，所以我的要求是一定要达到目的」 |
| G 改：对照组机器检查 | 见本块提交 | 待合入 | 闸门本机 1.35 秒 | 36 组（自动成组 + 手写）；规则 5 缺格 / 比兄弟弱进 sibling_baseline.txt，现存 11 条；影响清单列同组兄弟 |
| F CC 前端注释 | 见本块提交 | 待合入 | 闸门本机约 1.4 秒 | 10 处 `// map:` 注释对上 19 行外壳动作；5 处非入口跳转登记原因；去掉 UNSCANNED_KINDS |

> 基线：`c9057f6f4`（= 远端 main = 生产 release-20261103-1328）。分支 `pearnly/product-map-885dc7`，工作树 `.claude/worktrees/product-map-885dc7`。
> 依据：主会话手册 `~/pearnly-devtools/main-session-playbook.md` 第三、六节；交接 `main-handoff-2026-11-03.md` 第一节；本仓 AGENTS / verification / architecture。
> 盘点只读：没改代码、没起实验室、没碰测试机和生产。粗数脚本在本机临时目录，不进仓库；数字都绑定上面那个提交。

## 结论先说

1. **地图放 `scripts/impact/product-map/`，和 `shared-parts.toml` 同一个体系、分文件并列**：地图行引用共用件 id 和已有的 205 个测试入口，不复制它们；二期再把共用件的 `entries` 改成从地图推出来，只留一份。
2. **一行 = 用户能碰到的一个入口**（页面、页头按钮、LINE 菜单格 / 按钮 / 卡片 / 事件、CC 工具、代录钩子、CC 视图与外壳动作）。白名单接口和 site_config 键不单独成行，自动挂在入口下面当「代码锚点」和「配置依赖」。
3. **第一期（ERP + Cowork 的网页 Desk、LINE、CC 对话、CC 代录）现有 317 个入口**：有业务断言的 132 个；只有打开检查 / 页面快照 / 人工清单的 129 个；**什么都没有的 56 个**。56 个就是「无测试」的初始基线，只许减少。**11-03 出事的三处都在没测或只有人工清单的那一栏**：LINE 卡片「แก้ไข」只有人工清单；CC 切换账号、ERP 添加公司什么测试都没有。
4. 闸门 `scripts/check-product-map.py` 是纯静态检查（预计几秒），作为一步加进现有的 Development checks（已经跑在 free-arm-2），不新开 runner、不加费用。
5. 部署接线分两段，扩展现有的 `deploy-readback.sh` / `check-liff-endpoints.py`：**部署前**对比每个正式租户和同产品测试租户的配置；**部署后**对每个正式租户，把标了「上线必验」的入口不登录走一遍（只读）。11-03 那种「只在 testerp 测过」的配置差异，部署前就会报出来。
6. 第一期分 7 块施工，机器耗时合计约 11–12 小时（不含等用户的时间）。CC 前端那一块排在另一个窗口合入 `frontend/assistant` 之后再做。

---

## 0. 需求原文与理解

- 用户原话（11-03 晚）：「所有产品所有功能如何治根杜绝」。主会话手册 3.1 定的目的：一张随代码自动保持准确的「所有用户能碰到的入口」清单，回答三件事——**这次改动影响哪些地方、每个地方靠什么测试兜住、上线后要在哪些真实租户上验**。
- 要堵的四个根因（手册 3.1）：① 不知道改动会碰到哪里；② 测的不是客户真正在用的（配置不同）；③ 只测了想到的情况；④ 两个产品、两个模式做法不一致。
- 不在本评估范围：不修 11-03 的三个缺陷（另一个窗口在修，修的同时登记进地图）；二、三期（网页巡检、CC 桌面端巡检、真机自动化）只写方向；DMS 和控制面放第二期。

## 1. 现有三套机制（只读盘点）

| 机制 | 现在管什么 | 规模（c9057f6f4） | 管不到的 |
|---|---|---|---|
| `scripts/impact/shared-parts.toml` + `scripts/check-impact.py`（CI 同名步骤，只出清单） | 改到登记的共用件时，列出要实测的入口（怎么点、预期、auto / manual / offline）；按特征串反查调用方，有调用方没登记就失败 | 56 个共用件，205 个入口：自动冒烟 60、人工 140、离线 5 | 不碰共用件的入口不在表里；入口不记租户配置依赖；manual 不要求有自动测试；不知道正式租户；入口和产品功能没有一一对应（一个文件服务几个入口时会漏挂，verification.md 第 7 条自己也写了） |
| `tests/flows/registry.toml` + `scripts/flows/coverage.py`（CI） | 改到「产品入口文件」（页面 JS、www、前端单页、带白名单的模块）却没有流程 `covers` 它 → 失败；部署闸只认全套 `pearnly/flows` | 67 条流程（ERP 46、Cowork 18、DMS 3）；两种宽度都跑的 47 条，只跑电脑的 14 条，只跑手机的 6 条 | 按**文件**算覆盖：文件被某条流程覆盖，就算里面所有接口都测过了；不知道入口在哪个渠道、哪一端；没有配置和租户 |
| `tests/flows/behind_baseline.txt` | 入口背后两层的模块里，开拦截时还没有流程的存量：改到只提醒，只许减少 | 156 条 | 同上，按模块算 |
| 相关的其他检查 | `check-entries.py` + `tests/baseline/page-entries/*.json`（页头按钮要声明，按岗位比对页面快照）；`tests/endpoint_guards.py`（枚举全部白名单接口，查有没有守卫）；`scripts/smoke/run.py --readonly`（线上测试租户只读冒烟） | — | 页面快照只核按钮「出不出现」，不核点了对不对 |
| 部署回读 `deploy-readback.sh`（在生产机跑） | 容器镜像、维护模式、Error Log、四个域名的 ping；每个配了 LIFF 的站点调 `check-liff-endpoints.py`（落地地址形状 + 从 LINE 进网页不登录走一遍） | 11-03 起 9 个站点 | **只有 LIFF 这一项是逐租户检查**；其他配置（识别引擎、功能目录、LINE 频道、CC 入口）没有正式租户与测试租户的对比 |

结论：三套机制都按**文件 / 模块**组织，没有一张按「用户能碰到的入口」组织的表，也没有租户维度。地图不推翻它们，而是把它们串起来：**入口 → 文件（自动） → 共用件（引用 shared-parts） → 测试（引用流程 / 已有入口 / 单测 / 真机清单） → 配置 → 租户**。

## 2. 第一期现有入口粗数（真实数字）

口径：
- **有业务断言**：有流程用例（对页面结果和落库下断言）；或者有离线测试按名字引用到它（模拟 LINE webhook、单测）。
- **只有打开 / 快照 / 人工**：只有页面打开冒烟（只核能打开、无报错）、页面入口快照（只核按钮出现），或者 shared-parts 里标了 manual 的人工清单。
- **没有**：上面都没有。

| 入口类型 | ERP | Cowork | 共用 | 合计 | 有业务断言 | 只有打开 / 快照 / 人工 | 没有 |
|---|---|---|---|---|---|---|---|
| Desk 页面（导航里的） | 19 | 8 | — | 27 | 21 | 6（ERP 出库、收银小票、交班、客户；Cowork 收入对账、销项核对：只有打开冒烟） | 0 |
| Desk 页面（不在导航：推送记录、对账、指南、历史） | 4 | — | — | 4 | 0 | 4 | 0 |
| 页头动作（`pearnly_navigation.actions` 声明的码） | 77 | 54 | — | 131 | 17 | 114（只有页面快照） | 0 |
| LINE（富菜单格、流程按钮、卡片链接、发图识别、关注 / 绑定、从 LINE 进网页） | 9 | 8 | 2 | 19 | 14 | 4（**两个产品的卡片「แก้ไข」**、Cowork 发图识别、LINE 进网页） | 1（webhook 验签与分发） |
| CC 工具（对话里能调的） | 14 | 70 | 4 | 88 | 57 | 0 | 31（Cowork 29、共用 2） |
| CC 代录钩子（`pearnly_express_operator`） | 6 | 10 | — | 16 | 16（文件级，由 `cc.operator` / `cowork.operator.unknown` 覆盖，逐个钩子待核） | 0 | 0 |
| CC 视图（对话 / 定时任务 / 公司 / 公司页 / 待处理 / 代录） | 6 | 6 | — | 12 | 5 | 1（ERP 公司页待核） | 6（ERP 定时任务、待处理；Cowork 对话、定时任务、公司、公司页） |
| CC 外壳动作（手工列的：设置、切换账号、添加公司、退出、两种深链接、上传附件、删对话、公司页「在产品打开」、MR.ERP 授权） | 10 | 10 | — | 20 | 2（ERP 设置、Cowork MR.ERP 授权） | 0 | 18（**含切换账号 ×2、添加公司 ×2**） |
| **合计** | | | | **317** | **132** | **129** | **56** |

挂在入口下面的锚点（不单独成行）：

| 锚点 | 数量 | 说明 |
|---|---|---|
| 白名单接口 | 601（ERP 270、Cowork 244、共用 87） | 测试里按名字引用到的 363；被流程打开的页面调用的 128；只是所在文件被流程覆盖的 62；都没有的 48 |
| 其中前端找不到调用方 | 187 | 92 个由 LINE / CC 工具 / CC 桌面版 / Python 调用（能挂到对应入口）；**95 个全仓都找不到调用方**（ERP 73、Cowork 7、共用 15）：有的是保留的兼容入口（如 `inventory.api.api_submit_stock_entry`，旧记录写明保留），有的是前缀拼接扫描器认不出，也可能有死接口。一期逐个分诊 |
| site_config 键 | 直接读的 52 个；另有按前缀拼出来读的整族（如 `pearnly_line_*` 经 `line._conf()`），正则扫不到 | 现在只有 `pearnly_line_liff_id` 做逐租户检查 |

数字的误差：「按名字引用」偏宽（名字出现在测试里不等于断言了行为）；「CC 外壳动作」是手工列的（前端还没有能扫的声明），实际只会更多。施工第 B 块逐行核准后，基线以施工时的真实数字为准，评估里的数字只用来定规模。

## 3. 一行地图的字段

一行 = 一个用户能碰到的入口。同一入口在电脑和手机上都有时不拆两行，用 `ends` 列出，测试按端分别算颜色。

| 字段 | 必填 | 谁写 | 说明 |
|---|---|---|---|
| `id` | 是 | 机器生成，人可改名 | `<产品>.<渠道>.<入口>`，如 `erp.line.card-edit`、`cowork.cc.view.pending`、`erp.desk.pearnly-sales` |
| `product` | 是 | 机器 | ERP / Cowork（二期加 DMS / 控制面）；共用层入口写「共用」并在 `products` 里列出服务哪几个产品 |
| `channel` | 是 | 机器 | `desk`（网页 Desk）/ `line`（菜单、按钮、卡片、事件、LIFF 页）/ `cc-chat` / `cc-operator` / `cc-desktop`（三期） |
| `ends` | 是 | 机器给默认，人改 | `["电脑","手机"]` 的子集；LINE 默认只有手机 |
| `kind` | 是 | 机器 | 发现来源：`nav-page`、`page-action`、`page`（不在导航）、`report`、`line-menu`、`line-postback`、`line-link`、`line-event`、`liff-page`、`cc-tool`、`cc-operator-hook`、`cc-view`、`cc-action` |
| `anchor` | 是 | 机器 | 发现器给的稳定键，如 `nav:erp:pearnly-sales`、`action:erp:pearnly-sales:Export`、`tool:erp_push_documents`、`line:erp:postback:a=mode:purchase`、`line:erp:link:/desk/{root}/line/{doc}`。闸门用它判「未登记 / 已过期」 |
| `where` | 是 | 人（机器给草稿） | 人话：在哪、怎么点到。和 shared-parts 的 `where` / `steps` 同一写法 |
| `files` | — | **机器推出，不手写** | 入口文件 + 调用链挂上来的接口文件；影响分析用 |
| `parts` | 否 | 机器建议，人确认 | 用到的共用件 id（必须存在于 `shared-parts.toml`）。机器按「入口文件 ∩ 共用件调用方」给建议，人确认，避免同一文件多入口漏挂 |
| `config` | 否 | 人 | 依赖的租户配置：site_config 键（`site:pearnly_line_liff_id`）、LINE / LIFF 设置（`liff:endpoint`、`line:webhook`）、功能目录（`feature:<id>`）、Keycloak（`kc:<realm>:remember-me`）、站外入口（`ext:fatcc.com`）。部署前后的对比按这里取键 |
| `tests` | 至少一项，或进基线 | 人（机器自动补能推出的） | 引用，不复制：`flow:<id>`（可带 `@desktop` / `@mobile`）、`impact:<shared-parts 入口 id>`、`unit:<路径>[::<名字>]`、`device:<本行 device 段>`、`readback:<检查名>`。机器会自动补：流程起始页等于页面地址的、shared-parts 入口地址或文件对得上的 |
| `device` | 否 | 人 | 真机清单（步骤 + 预期 + 用什么设备：三星 LINE / iPhone / 测试机 CC）。不另建文件，就写在行里，部署时按影响清单汇总成本批的真机单 |
| `must_verify` | 否 | 人 | 是否「上线必验」。是的话必须有 `probe`（能自动只读检查的）或 `device`（不能自动的） |
| `probe` | 否 | 人 | 正式租户只读探针：`http`（地址、浏览器标识、预期状态 / 跳到哪、不许出现 4xx/5xx 的跳数）、`liff`（现有检查）、`assistant-gate`（CC 入口跳转）。不登录、不写 |
| `group` | 否 | 人 | 对照组键：同一功能在 ERP / Cowork、对话 / 代录、电脑 / 手机的几行用同一个键，如 `cc.add-company` |
| `na` | 否 | 人 | 对照组里确实不该有的格写原因，如 `{ "cowork" = "Cowork 没有收银" }` |
| `incidents` | 否 | 人 | 出事回填：`["2026-11-03 LIFF 子路径 404"]`，补了哪条测试写在 `tests` |

示例（11-03 两起事故放进地图会是什么样）：

```toml
[[row]]
id = "erp.line.card-edit"
product = "ERP"
channel = "line"
ends = ["手机"]
kind = "line-link"
anchor = "line:erp:link:/desk/{root}/line/{doc}"
where = "LINE 里识别后的卡片，点「แก้ไข」打开单据编辑页"
parts = ["line", "line-session"]
config = ["site:pearnly_line_liff_id", "liff:endpoint"]
tests = ["impact:erp.line.card-edit", "readback:liff"]
must_verify = true
probe = { type = "liff" }
device = "三星 LINE：发一张票 → 卡片点「แก้ไข」→ 打开的是这张单的编辑页，不是 404"
group = "line.card-edit"
incidents = ["2026-11-03 mrerp 卡片 แก้ไข 404（LIFF 落地带子路径）"]

[[row]]
id = "cowork.cc.action.switch-account"   # ERP 一行同 group
product = "Cowork"
channel = "cc-chat"
ends = ["电脑", "手机"]
kind = "cc-action"
anchor = "cc:action:switch-account"
where = "CC 左下角站点列表，点另一个站点"
config = ["ext:fatcc.com", "kc:pearnly-cowork:remember-me"]
tests = []                                # 现在没有 → 红，在基线里；修复时补流程
must_verify = true
probe = { type = "assistant-gate", site = "{另一站点}" }
group = "cc.switch-account"
incidents = ["2026-11-03 切换账号第一次落到 Keycloak 登录页"]
```

有了这两行：改 Cookie 解码的那批，影响清单会列出「切换账号：红，没有测试」；改 LIFF 那批，部署前对比会报出 mrerp 的 LIFF 落地地址和 testerp 不同。

## 4. 文件放哪、和 shared-parts 的关系

```
scripts/impact/
  shared-parts.toml            # 不动：共用件定义 + 205 个细粒度测试入口
  product-map/
    erp.toml  cowork.toml  shared.toml     # 一期；二期加 dms.toml、control.toml
    not_entries.toml           # 发现到但不算入口的（内部接口、兼容保留、待删），逐条写原因，只许按条登记
    untested_baseline.txt      # 「没有测试」的存量 id，只许减少
    config.toml                # site_config 键分类（见第 8 节）与前缀族声明
scripts/impact/product_map.py  # 发现器 + 加载 / 校验（check-product-map 与部署脚本共用）
scripts/check-product-map.py   # 闸门
```

- 放 `scripts/impact/` 不放 `docs/`：这是给机器读的闸门数据，和 shared-parts 同一个地方、同一种 TOML 写法；`docs/` 只放说明。
- **按产品分文件**：多个窗口并行开发时，各改各产品的文件，减少冲突。
- **并列、互相引用，不合并文件**（一期）：地图行 `parts` 引用共用件 id，`tests` 引用 `impact:<入口 id>`；`check-impact.py` 的影响清单里加一段「命中的地图入口」。二期再把 `part.entries` 改成从地图行的 `parts` 推出来，shared-parts 只留共用件定义和测试步骤，表就只有一份。整治期不动那份 2514 行、几个窗口都在加的文件。
- 和流程登记表：`covers`（文件）照旧给覆盖闸用；地图行里的 `flow:<id>` 由机器按流程起始页自动补，人只补推不出的。

## 5. 自动发现：每类从哪扫、扫不到怎么办

| 入口类型 | 从哪扫 | 扫不到的怎么办 |
|---|---|---|
| `pearnly_navigation` 页面、页头动作、路由 | 各产品 `hooks.py`，用 AST 读字面量（`check-entries.py` 已有同样读法，复用） | 按条件出现的页（收银台按公司开关）写 `condition`；页头码由 check-entries 保证已声明 |
| Desk Page / 报表 / Workspace | `**/page/<名>/<名>.json`、`**/report/*/*.json`；Workspace 目前一个都没有，扫描照样留着 | 共用层里给多个产品用的页按 `pearnly_navigation` 判归属 |
| `frappe.whitelist` | 复用 `tests/endpoint_guards.py` 的枚举；按调用链挂到入口：前端文件里的完整路径、`const API = "x."` 加名字的拼接、`call("…")` 字面量；Python 里 CC 工具实现、LINE 处理器的导入 | 挂不上的进 `not_entries.toml`，分三类写原因：内部（只给钩子 / 队列调）、兼容保留（写保留到什么时候）、待删（列给主会话，删除另走批次）。新增接口挂不上就失败 |
| LINE 菜单 / 按钮 / 卡片 / 事件 / LIFF 页 | 富菜单 `FUNCTIONS` 与 `payload()`；`"a=…"` 形式的 postback 字面量；`line_login.entry_url(...)` 的参数（f 字符串归一成 `{root}` / `{doc}`）；处理器里的 `etype == "follow"` 等事件；`www/**/*.html` 与 `/liff/` 页 | 动态拼的 postback 和链接，在同一行或上一行写 `# map: <行 id>`（同 check-entries 的 `// entry:` 注释做法） |
| CC 工具、代录钩子 | `pearnly_assistant_tools` 指向的 `TOOLS` 列表（AST 读 `"name"`）；`pearnly_express_operator` 的键 | 都是声明式的，能全扫 |
| CC 前端视图、侧栏、外壳动作 | 视图：`frontend/assistant/src/lib/nav.ts` 的 `View` 类型；深链接参数从同一文件读 | 侧栏按钮、`window.open`、换站点这些没有可扫的声明：在处理函数上加 `// map: <行 id>`，发现器要求每个 `window.open` / `location.assign` / 侧栏项都带注释。**要改 `frontend/assistant`，排在另一个窗口合入之后**（第 F 块） |
| site_config 键 | AST 扫 `frappe.conf.get("k")`、`frappe.conf.k`、`conf["k"]`、`update_site_config("k")`、模块常量；前缀族在 `config.toml` 里声明包装函数（如 `pearnly_core.line._conf` → `pearnly_line_*`） | 不在 site_config 里的设置（LIFF 落地地址、LINE「Use webhook」、Keycloak 记住我、站外入口）手写进行的 `config`，并给出读法（现有 LIFF 读法；没有读法的标「只能真机」） |

## 6. 闸门 `scripts/check-product-map.py`

四条规则（离线、不连网、只读）：

1. **未登记**：发现到的入口锚点不在任何一行、也不在 `not_entries.toml` → 失败。新入口必须同一提交登记，并写测试。
2. **已过期**：行的锚点在代码里找不到 → 失败（删功能要同时删行）。同时核引用：`flow:` 的流程 id、`impact:` 的入口 id、`parts` 的共用件 id、`unit:` 的文件和名字必须都存在，任何一个不在也算过期。
3. **没有测试**：`tests` 为空 → 失败，除非 id 在 `untested_baseline.txt`。基线只许减少：新加 id 失败；已经有测试的 id 还留在基线里也失败（照 `behind_baseline.txt` 的做法）。另外：`must_verify = true` 的行必须有 `probe` 或 `device`，否则失败。
4. **影响清单**：相对 merge-base 的改动 → 命中的行（按 `files`、`parts`、`config` 键）。输出 Markdown：每行的颜色、要跑的流程（可以直接交给 `scripts/flows/run.py --flow`）、要点的 shared-parts 入口、本批真机单、部署后要逐租户核的「上线必验」行、对照组里缺的格。写进 CI 摘要，不判失败（同 check-impact）。

颜色（报告和看板用，闸门只按第 3 条判）：绿 = 有业务断言；黄 = 只有打开 / 快照 / 人工清单；红 = 没有。按端分别算：只跑电脑宽度的流程，手机那一端还是黄或红。

接入：
- `development.yml` 的 `architecture-and-offline` 里，紧挨 `check-impact` 加一步：`python3 tests/test_check_product_map.py && python3 scripts/check-product-map.py --markdown --out "$GITHUB_STEP_SUMMARY"`。纯静态检查，目标 5 秒以内（发现器一次读完全部 tracked 文件；check-impact 现在也是这样）。超过 10 秒就先优化，不加进去。
- 本地闸门：写进 verification.md「推送前跑与 CI 同名的闸门」那张清单。不加进提交钩子：提交钩子现在只跑架构和文案两道闸，保持快。
- 文档：verification.md 加「产品地图」一节，共用件连带回归闸门那一节引用它；AGENTS「验证与审查」加一句：新入口同一提交登记进地图。

## 7. 部署接线

现在：`deploy.sh`（本机）把脚本同步到生产机 `/opt/pearnly/deploy`，`deploy-run.sh` 最后跑 `deploy-readback.sh`，它在 web 容器里列出每个站点的 site_config，再把配了 LIFF 的站点交给 `check-liff-endpoints.py`。

加的东西（都只读、不登录、不要密钥）：
1. **导出**：`deploy.sh` 在打包时跑 `check-product-map.py --export`，把发布提交的「上线必验」行、探针、配置期望写成一个 JSON，随脚本一起同步过去。生产机上不需要仓库。
2. **部署前配置对比**（`deploy.sh --dry-run` 和正式部署激活前都跑）：在 web 容器里读每个站点的 site_config（不进数据库容器），按 `config.toml` 的分类比较正式租户和同产品的测试租户：
   - **应一致**（识别引擎、功能目录、各种开关）：值的摘要要相同；
   - **形状规则**（LIFF 落地地址必须是站点根、CC 入口地址必须是 fatcc.com）：每个租户各自满足；
   - **只核有没有**（频道密钥、LIFF ID 这类各租户本来就不同的）：只看存在、不比值、不打印值。
   有差异就逐项打印。是拦部署还是只报告，见第 12 节第 3 问。
3. **部署后逐租户入口检查**：新脚本 `scripts/production/check-tenant-entries.py`，把 `check-liff-endpoints.py` 的走法推广成几种探针：`http`（指定浏览器标识打开入口地址，核状态 / 跳转去向、站内每一跳不能是 4xx / 5xx）、`liff`（调现有脚本）、`assistant-gate`（CC 入口不登录，核跳到哪）。在 `deploy-readback.sh` 里接在 LIFF 检查后面，对每个正式租户 × 每个「上线必验」行跑一遍。正式租户只做入口检查，不登录、不写；登录后的检查只在测试租户做（二期由 free-arm-2 的无头浏览器做）。
4. **本批真机单**：影响清单里命中、又只能真机的行（`device`），回读最后打印成清单，交主会话排三星 / 测试机。

## 8. 跨产品、跨模式、跨端对照

- 用 `group` 把同一功能的几行串起来；报告和看板出一张矩阵：行 = 功能（group），列 = ERP / Cowork × 对话 / 代录 × 电脑 / 手机，格子 = 绿 / 黄 / 红 / 缺 / 不适用（`na` 写了原因的）。
- 「缺」格（没有行、也没写 `na`）一期只在报告里标出，不拦；二期对新加的对照组拦。
- 机器判断不了「两个产品行为一样不一样」，只能露出事实：两行的锚点文件不同（例：ERP 添加公司走 `window.open`，Cowork 走弹窗）、测试强度不同、一边有 `incidents` 另一边没有。人看矩阵决定要不要统一。11-03 的「代录模式公司页」「ERP 添加公司跳网页」在这张表上会是同一 group 里实现不同、测试一红一绿。

## 9. 引擎、看板、分期（自动化架构）

- **一期**：闸门和影响报告就跑在现有的 Development checks 里（free-arm-2 的三个 runner），只多几秒；不新开 runner、不加定时任务、不加费用。看板雏形 = `check-product-map.py --html` 出一个静态页（全量地图红黄绿、影响清单、对照矩阵），CI 里落在 free-arm-2 的本地目录（runner 是自己的机器，不占 GitHub 存储），要看时 `ssh -L` 按需起一个静态服务，或拷回本机打开。零常驻。
- **二期**（方向）：DMS、控制面入地图；部署后网页巡检 ②：部署脚本结束时触发 free-arm-2 上的一个低优先级 runner（单独标签，systemd 限 1 核、4G 内存，同一时间只跑一个），无头浏览器在测试租户按地图跑电脑 / 手机宽度 / 模拟 LINE 内置浏览器 / WebKit，截图和上次比；正式租户仍只做入口检查。看板要不要改成常驻服务，届时再定。
- **三期**（方向）：③ CC 桌面端巡检接测试机 runner；④ 三星经 adb 自动跑 LINE 菜单、卡片、LIFF、对着测试机屏幕扫码；本机 iOS 模拟器跑 Safari 渲染和软键盘。iPhone 上的 LINE 和相机仍要真 iPhone。Mac 醒着时由主会话在部署前后触发。

## 10. 第一期范围与施工顺序（机器耗时，小时级）

范围：ERP 与 Cowork 的网页 Desk、LINE、CC 对话、CC 代录（含共用层里服务它们的入口）。DMS 第二期。

| 块 | 内容 | 依赖 | 机器耗时 |
|---|---|---|---|
| A | `product_map.py`：地图格式、加载校验，发现器（导航页 / 页头动作 / 页面 / 报表、LINE、CC 工具、代录钩子、CC 视图、site_config 键）；离线单测 | — | 约 2 小时 |
| B | 初始导入：生成 `erp.toml` / `cowork.toml` / `shared.toml` 约 317 行；自动补能推出的测试；人工补关键行的 `config` / `must_verify` / `probe` / `group` / `incidents`（LINE、LIFF、登录、切换账号、添加公司、CC 外壳，约 60 行）；生成 `untested_baseline.txt`（预计 56 条上下） | A | 约 2 小时 |
| C | `check-product-map.py` 四条规则 + 影响清单（并进 check-impact 输出）+ `tests/test_check_product_map.py` + `development.yml` 一步 + 文档；推分支跑 CI | A、B | 约 1.5 小时 + CI 约 15 分钟 |
| E | 部署接线：`--export`、部署前配置对比、`check-tenant-entries.py`、`deploy-readback.sh` 接线、离线测试；真实回读随下一次部署由主会话跑 | A（格式定了即可，和 D 可并行） | 约 2 小时 |
| D | 白名单接口挂靠：调用链解析；95 个找不到调用方的逐个分诊进 `not_entries.toml`（删除只列清单，不在本期做） | C | 约 2 小时 |
| G | 对照组矩阵 + `--html` 看板雏形 + free-arm-2 落盘 | C | 约 1 小时 |
| F | CC 前端入口：在 `frontend/assistant` 加 `// map:` 注释，发现器扫它，补 CC 外壳动作的真实行（替换第 2 节手工列的 20 个） | 另一个窗口合入 CC 外壳 / 侧栏 / 登录入口之后 | 约 1 小时 |

合计约 11.5 小时机器耗时（不含等 CI、等审）。A→B→C 是同一批文件，一个写者顺序做；E 碰的是 `scripts/production/`，C 之后可以另开一个窗口并行。每块独立提交，C 完成就能合入并开始拦。

不碰：CC 外壳、侧栏、登录入口、`frontend/assistant`（F 块除外，且要等）、`companion/`；不改产品行为；整治期不加新功能。

## 11. 测试与验收

- 闸门自测（`tests/test_check_product_map.py`，离线）：四条规则各有成功和失败用例（新入口没登记、删了入口没删行、引用的流程不存在、基线新加 / 该删没删、上线必验没有探针）；发现器对每类入口各有样例。
- 真实性抽查：随机抽 20 行，人对照页面和代码核「位置、测试引用、配置」是否属实，记进本任务记录。
- 部署接线：`check-tenant-entries.py` 用假的 HTTP 响应做离线测试（照 `test-check-liff-endpoints.py`）；真实只读运行随下一次部署由主会话做，结果记进发布记录。
- 验收看什么：主会话审影响清单是否能直接用来排测试、部署前对比能不能复现 11-03 的 LIFF 差异（拿 11-03 之前的配置形状做离线用例）。

## 12. 要主会话定的点

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | 地图放 `scripts/impact/product-map/`（和 shared-parts 并列、互相引用），还是 `docs/product-map/` | `scripts/impact/product-map/`，一期不合并 shared-parts，二期把 `part.entries` 改成从地图推出 | A 块开不了工 |
| 2 | 「黄」（只有打开 / 快照 / 人工清单）算不算过第 3 条 | 一期算过，报告和看板标黄；「上线必验」行另要求有探针或真机清单 | 不算的话基线从 56 涨到 185 |
| 3 | 部署前配置对比，「应一致」类有差异时拦部署还是只报告 | 拦（没有绕过开关，改配置或改地图里的期望再部署）；「只核有没有」和形状以外的只报告 | E 块的退出码怎么定 |
| 4 | 95 个全仓找不到调用方的白名单接口 | 一期只分诊登记（内部 / 兼容保留 / 待删），待删的列清单交主会话，删除另走批次 | D 块的范围 |
| 5 | 页头动作 131 行：单独成行，还是只当页面的子项 | 单独成行（自动生成），测试默认继承页面快照（黄）；流程里点到的升绿 | 行数和基线规模 |
| 6 | 看板雏形 | 按需 `ssh -L` 看静态页，零常驻；常驻服务二期再定 | G 块怎么做 |
| 7 | F 块要在 `frontend/assistant` 加注释 | 等另一个窗口合入后再做，主会话通知开工 | CC 外壳动作在那之前是手工行 |

## 13. 其余评估栏

- **复用**：发现器复用 `check-entries.py` 的 hooks 读法和 `tests/endpoint_guards.py` 的接口枚举；部署探针复用 `check-liff-endpoints.py`；基线机制照 `behind_baseline.txt`；影响清单并进 `check-impact.py` 的输出。
- **Frappe 原生**：不涉及产品界面和 DocType；只读 Frappe 的声明文件（hooks、page / report JSON）。
- **按类通用**：按入口类型发现，一类一个发现器，不按某个页面、某个租户单独写。
- **上下游 / Express / CC 桌面版载荷**：不涉及，不改业务数据和载荷。
- **隔离与权限**：部署检查只读、不登录；site_config 的密钥类只核有没有，不比值、不打印。
- **存储与成本**：地图文件几百 KB；CI 多几秒；不加 runner、不加定时任务、不加固定月费。
- **删除 / 作废**：删入口要同时删行（规则 2 强制）；「待删接口」只列清单，不在本期删。
- **文案**：不涉及界面文案；脚本输出是给开发看的中文，不进产品词典。

## D 块：白名单接口分诊（11-04）

闸门规则：一期范围（共用层、ERP、Cowork）602 个白名单接口里，找不到调用方的（前端、Python、CC 桌面版、站外配置、部署脚本都没调；测试和流程用例不算）
要么删掉，要么登记进 `not_entries.toml` 并写原因；有了调用方闸门会报「该删」。影响清单也会把「改到入口调用的接口」算进命中。

11-04 晚核实了原先归为「拼名调用」的 51 个：8 个确实是拼名调用（上传的 `${method}_begin` / `_finish` 6 个、导出模板串 `${u.api}api_…_export` 2 个），
补了两条识别规则后自动认出、已从登记里删掉；其余 43 个前端并没有拼名调用，按证据归进兼容保留或待删。现在登记 91 个：

| 分类 | 个数 | 说明 |
|---|---|---|
| compat | 47 | 产品里没有调用方，文档提到（保留的兼容入口或旧功能）——待核实保留期限 |
| delete | 44 | 待删候选：产品里没有调用方，只有测试在调或全仓都没有 |

待删候选清单（交主会话；删除另走批次，删的同一提交删掉对应条目）：

- `pearnly_core.manifest.describe`：产品里没有调用方，只有测试 scripts/flows/cc_person_names.py 在调（待删候选）
- `pearnly_core.ocr.api_selftest`：全仓没有调用方，测试与文档也没提到（待删候选）
- `pearnly_core.sessions.logout_here`：产品里没有调用方，只有测试 tests/baseline/unguarded_endpoints.toml 在调（待删候选）
- `pearnly_core.sessions.my_sessions`：产品里没有调用方，只有测试 tests/baseline/unguarded_endpoints.toml 在调（待删候选）
- `pearnly_cowork.files.guard.optimize_file`：全仓没有调用方，测试与文档也没提到（待删候选）
- `pearnly_cowork.masters.api.clients`：产品里没有调用方，只有测试 apps/pearnly_control/pearnly_control/account/test_identity_pools.py 在调（待删候选）
- `pearnly_cowork.masters.api.create_client`：产品里没有调用方，只有测试 tests/test_cowork_client_records_source_parity.py 在调（待删候选）
- `pearnly_cowork.masters.api.products`：产品里没有调用方，只有测试 apps/pearnly_core/pearnly_core/account/test_line_login.py 在调（待删候选）
- `pearnly_cowork.masters.api.roster`：产品里没有调用方，只有测试 apps/pearnly_dms/pearnly_dms/operators/test_line_account.py 在调（待删候选）
- `pearnly_cowork.masters.api.supplier_profiles`：产品里没有调用方，只有测试 scripts/flows/cowork_operator_lib.py 在调（待删候选）
- `pearnly_cowork.masters.api.workspaces`：产品里没有调用方，只有测试 scripts/flows/cowork_recon_presets.py 在调（待删候选）
- `pearnly_cowork.stocktake.api.add_entry`：产品里没有调用方，只有测试 apps/pearnly_erp_portal/pearnly_erp_portal/access/test_entry_permissions.py 在调（待删候选）
- `pearnly_cowork.stocktake.api.close`：产品里没有调用方，只有测试 apps/pearnly_core/pearnly_core/execution/test_pool.py 在调（待删候选）
- `pearnly_cowork.stocktake.api.create`：产品里没有调用方，只有测试 apps/pearnly_control/pearnly_control/account/test_member_service.py 在调（待删候选）
- `pearnly_cowork.stocktake.api.edit_entry`：产品里没有调用方，只有测试 tests/test_erp_stocktake_photos.py 在调（待删候选）
- `pearnly_cowork.stocktake.api.export`：产品里没有调用方，只有测试 scripts/flows/cowork_exports.py 在调（待删候选）
- `pearnly_cowork.stocktake.api.report`：产品里没有调用方，只有测试 apps/pearnly_control/pearnly_control/account/test_repool.py 在调（待删候选）
- `pearnly_erp_portal.integrations.api.api_line_bot`：11-04 核实：前端没有拼名调用，全仓没有调用方，测试与文档也没提到（待删候选）
- `pearnly_erp_portal.inventory.api.api_count`：11-04 核实：前端没有拼名调用，产品里没有调用方，只有测试 apps/pearnly_erp_portal/pearnly_erp_portal/access/test_entry_permissions.py 在调（待删候选）
- `pearnly_erp_portal.inventory.api.api_list_openings`：11-04 核实：前端没有拼名调用，产品里没有调用方，只有测试 apps/pearnly_erp_portal/pearnly_erp_portal/access/test_entry_flow_acceptance.py 在调（待删候选）
- `pearnly_erp_portal.inventory.api.api_list_warehouses`：11-04 核实：前端没有拼名调用，产品里没有调用方，只有测试 apps/pearnly_erp_portal/pearnly_erp_portal/access/test_entry_flow_acceptance.py 在调（待删候选）
- `pearnly_erp_portal.inventory.api.api_rebuild_balance`：11-04 核实：前端没有拼名调用，全仓没有调用方，测试与文档也没提到（待删候选）
- `pearnly_erp_portal.inventory.api.api_stock_count_entries`：11-04 核实：前端没有拼名调用，产品里没有调用方，只有测试 apps/pearnly_erp_portal/pearnly_erp_portal/access/test_entry_flow_acceptance.py 在调（待删候选）
- `pearnly_erp_portal.inventory.api.api_stock_count_entry_total`：11-04 核实：前端没有拼名调用，全仓没有调用方，测试与文档也没提到（待删候选）
- `pearnly_erp_portal.inventory.api.api_stock_count_import`：11-04 核实：前端没有拼名调用，产品里没有调用方，只有测试 apps/pearnly_erp_portal/pearnly_erp_portal/access/test_entry_flow_acceptance.py 在调（待删候选）
- `pearnly_erp_portal.inventory.api.api_stock_count_item_total`：11-04 核实：前端没有拼名调用，全仓没有调用方，测试与文档也没提到（待删候选）
- `pearnly_erp_portal.inventory.api.api_stock_count_items`：11-04 核实：前端没有拼名调用，产品里没有调用方，只有测试 apps/pearnly_erp_portal/pearnly_erp_portal/access/test_entry_flow_acceptance.py 在调（待删候选）
- `pearnly_erp_portal.inventory.api.api_stock_count_post`：11-04 核实：前端没有拼名调用，产品里没有调用方，只有测试 apps/pearnly_erp_portal/pearnly_erp_portal/access/test_entry_flow_acceptance.py 在调（待删候选）
- `pearnly_erp_portal.inventory.api.api_upsert_opening`：11-04 核实：前端没有拼名调用，产品里没有调用方，只有测试 apps/pearnly_erp_portal/pearnly_erp_portal/access/test_entry_flow_acceptance.py 在调（待删候选）
- `pearnly_erp_portal.inventory.serials.api_unit_history`：11-04 核实：前端没有拼名调用，全仓没有调用方，测试与文档也没提到（待删候选）
- `pearnly_erp_portal.masters.api.api_company_terminals`：11-04 核实：前端没有拼名调用，产品里没有调用方，只有测试 apps/pearnly_erp_portal/pearnly_erp_portal/access/test_entry_flow_acceptance.py 在调（待删候选）
- `pearnly_erp_portal.masters.api.api_create_company_terminal`：11-04 核实：前端没有拼名调用，全仓没有调用方，测试与文档也没提到（待删候选）
- `pearnly_erp_portal.masters.api.api_set_customer_active`：11-04 核实：前端没有拼名调用，全仓没有调用方，测试与文档也没提到（待删候选）
- `pearnly_erp_portal.masters.api.api_update_company_terminal`：11-04 核实：前端没有拼名调用，全仓没有调用方，测试与文档也没提到（待删候选）
- `pearnly_erp_portal.mrerp.api.account_sets`：产品里没有调用方，只有测试 scripts/flows/cowork_mrerp_login.py 在调（待删候选）
- `pearnly_erp_portal.mrerp.api.choose`：产品里没有调用方，只有测试 scripts/flows/cowork_pending_operator.py 在调（待删候选）
- `pearnly_erp_portal.mrerp.api.company_choices`：产品里没有调用方，只有测试 tests/flows/behind_baseline.txt 在调（待删候选）
- `pearnly_erp_portal.mrerp.api.configure`：产品里没有调用方，只有测试 scripts/flows/cowork_mrerp_login.py 在调（待删候选）
- `pearnly_erp_portal.mrerp.api.connection_view`：全仓没有调用方，测试与文档也没提到（待删候选）
- `pearnly_erp_portal.mrerp.api.detach`：产品里没有调用方，只有测试 tests/test-desk-place-and-switch.cjs 在调（待删候选）
- `pearnly_erp_portal.mrerp.api.refresh_catalog`：全仓没有调用方，测试与文档也没提到（待删候选）
- `pearnly_erp_portal.products.service.api_add_unit`：11-04 核实：前端没有拼名调用，全仓没有调用方，测试与文档也没提到（待删候选）
- `pearnly_erp_portal.purchasing.api.api_purchase_category`：11-04 核实：前端没有拼名调用，全仓没有调用方，测试与文档也没提到（待删候选）
- `pearnly_erp_portal.team.api.api_set_member_companies`：11-04 核实：前端没有拼名调用，全仓没有调用方，测试与文档也没提到（待删候选）

## 证明达到目的：事故回放（11-04，主会话要求）

用户口径：「一定要达到目的」。拿 11-03 的三起事故和近一个月在生产或真机上出过的事，逐条问三道关能不能拦住：
**合入闸门**（产品地图规则 1–5、流程覆盖闸、共用件清单、单测，加上部署闸要求的全套流程）、**部署前配置对比**、**部署后逐租户探针**。
来源：`~/pearnly-devtools/main-handoff-2026-10-30.md` 流水账、记忆里的事故条目、流程登记表里注明事故的流程、今天的生产只读核对。
「能」= 不靠人记得，机器会报出来并停下；「部分」= 能报出一部分，或只提醒不拦；「不能」= 三道关都看不到。

| # | 事故 | 合入闸门 | 部署前对比 | 部署后探针 | 结论 | 差在哪 |
|---|---|---|---|---|---|---|
| 1 | 11-03 mrerp / erpcowork LINE 卡片「แก้ไข」404（LIFF 落地带子路径） | 不能：代码没错，根因在 LINE 那边的 LIFF 设置；10-23 dd0e09af0 让所有 LINE 链接经 LIFF 时，影响清单会列出 LINE 的上线必验行，但只提醒 | 不能：LIFF 落地地址不在 site_config | **能**：回读里的 LIFF 检查和卡片路径探针，会逐个正式租户照 LINE 的拼法走一遍（离线测试复现了 404）；dd0e09af0 部署当天就会报出 | 部署后拦住 | 应该在部署前就拦：配置对比阶段没查 LIFF 落地地址 |
| 2 | 11-03 CC 切换账号第一次落到 Keycloak 登录页 | 部分：两行现在有单测 `SiteSwitchTests`（`?site=` 的跳转判断），回退能拦；当时的根因是 fatcc.com 入口 Cookie 解码加账号池静默登录，属于跨站、真浏览器的流程，单测碰不到 | 不能：Keycloak「记住我」设置（`kc:` 依赖）不在对比里 | 部分：assistant-gate 只核不登录时转到 `?site=`（今天生产 5 站不通过，因为还没部署这批），走不到静默登录那一步 | 部分 | 没有「登录后切换站点」的自动端到端测试；Keycloak 设置不在对比里 |
| 3 | 11-03 ERP 版 CC「添加公司」跳系统浏览器、要重新登录 | 部分：ERP 一行引用流程 `cc.companies`，断言在 CC 里弹窗、不开新窗口，网页一侧回退能拦；另一半根因在 CC 桌面版把新窗口交给系统浏览器（已由 aa91bb5cc 修），流程跑在浏览器里，测不到 CC 桌面版；Cowork 那行仍是红、在基线里 | 不涉及 | 不涉及 | 部分 | CC 桌面版窗口行为没有自动测试；CC 外壳动作还是手工行（F 块补扫描） |
| 4 | 10-28 polymer 手机盘点扫码事故 | **能**：流程 `erp.stocktake.scan`（手机宽度、假摄像头）；改到盘点文件，分支跑受影响流程，部署闸要全套 | — | — | 能 | — |
| 5 | 10-23 扫码弹窗全黑（`set_df_property` 换掉了 `<video>`） | 部分：带假摄像头的流程覆盖盘点、出入库、销售、LINE 盘点、Cowork 盘点；新建商品弹窗扫码、收银台相机等仍是共用件清单里的人工项。地图按页面成行，同一页上几个相机入口共用一格颜色，看不出哪个入口没测 | — | — | 部分 | 相机入口没有逐个成行、逐个有自动测试 |
| 6 | 10-15 改盘点扫码，其它入口全部黑屏（共用件） | 部分：共用件清单和地图影响清单会列出全部扫码入口和颜色，但只提醒、不拦；有流程的会被分支的受影响流程跑到 | — | — | 部分 | 影响清单里红 / 黄的入口不强制处理 |
| 7 | 10-22 手工重建 web 漏挂 frontend，全站 502 约 3 分钟 | 不能：手工操作不经过闸门 | 不能：不经过 deploy.sh | 不能：回读只在部署时跑 | 不能 | 手工生产操作没有强制回读 |
| 8 | 10-29 Cowork 对账：GET 里建归属被回滚 | **能**：流程 `cowork.recon.tasks` 和 `cowork.stocktake.list`（断言读接口不写库） | — | — | 能 | — |
| 9 | 10-29 LINE 手机盘点空仓保存 404 | **能**：流程 `erp.line.stocktake.phone` | — | — | 能 | — |
| 10 | 10 月改脚本加载方式，弄坏了 ERP 收银台 POS | **能**：流程 `erp.cashier.first-sale`、`erp.pos.shift`，前提是实验室里有前端构建产物（run.py 跑前会核） | — | — | 能 | — |
| 11 | 10-03 生产剖析脚本把 testdms 用户的 LINE 绑定顶掉 | 不能：运维脚本不在产品地图里，而且是 DMS（二期） | 不能 | 不能 | 不能 | 生产上跑的临时脚本没有登记，也不限制只用测试账号 |
| 12 | 11-01 真机：LINE 盘点相机扫到未知码，点「新建物料」没反应 | 部分：`erp.line.stocktake` 覆盖手输未知码再新建，`erp.line.stocktake.phone` 带假摄像头；「相机扫到未知码 → 新建」这个组合是否被断言，待逐条核 | — | — | 部分 | 同上：入口粒度只到页面 |
| 13 | 11-04 ccdemo 识别配置没跟上全租户切换（今天发现） | — | **能**：BLOCK 两项，下一次部署会在构建前被拦住 | — | 能 | — |

**合计 13 起：能 5，部分 6，不能 2。**「能」的 5 起都是这几天新加的流程或部署前对比拦住的；在 11-03 之前，这 13 起里没有一起会被机器拦住。

### 待补项（拦不住或只拦住一部分的）

| # | 待补 | 对应事故 | 建议做法 | 成本 |
|---|---|---|---|---|
| A | 部署前也核 LIFF 落地地址，不只部署后 | 1 | deploy.sh 的配置对比那一步顺带跑 `check-liff-endpoints.py`（本机只读 HTTP，不要密钥），落地地址不对就拦部署 | 零 |
| B | 登录后的流程（切换站点、静默登录、添加公司回来）自动走一遍 | 2、3 | 二期无头浏览器，在 free-arm-2 上用测试租户的测试账号，部署后由事件触发 | 零月费，要做 |
| C | CC 桌面版窗口行为（新窗口、托盘、代录）自动测 | 3 | 三期：测试机 runner 巡检；F 块先把 CC 外壳动作纳入扫描 | 零月费 |
| D | 影响清单里命中红 / 黄的入口不拦 | 6 | 建议：改动命中「上线必验」行或共用件入口、且该入口某端是红 / 黄时，闸门要求同一提交补测试，或在提交说明里写「已真机」并附记录路径——要主会话定口径 | 零 |
| E | 相机 / 扫码入口按页面成行，看不出哪个入口没测 | 5、12 | 把共用件 `desk-scan-core` 的 17 个入口落成地图行（kind 加 `scan`），测试引用 `scripts/smoke` 的 scan 探针或带 camera 的流程 | 零 |
| F | 手工生产操作（重建容器、临时脚本）不经过部署脚本 | 7、11 | 规矩：手工动过生产就单独跑一次 `deploy-readback.sh`；生产临时脚本只许测试账号（记忆里已有，未进流程文档） | 零 |
| G | 站外设置（Keycloak realm、LINE 控制台）不在配置对比里 | 1、2 | 对比加只读读法：Keycloak 用 admin 接口读 realm 的记住我时长（凭据已在生产机），LINE 用 liff.line.me 公开页（同 A） | 零 |
