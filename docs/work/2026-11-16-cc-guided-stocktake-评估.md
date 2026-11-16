# 需求影响评估：CC 引导式 · 盘点（Cowork 与 ERP）

> 总表 `docs/work/2026-11-16-cc-guided-master-table.md` 的 CW-D-ST、ERP-D-ST 两格。基线 `origin/pearnly/cc-guided-readback`（51004d757，含引擎 N 能力 40739abf2 与 `assistant/guided/` 包）。

## 0. 需求原文与理解

- 原话：用户 11-14「每个功能都要有引导式」，11-16「覆盖所有」；主会话 11-16「LINE / 网页已有成熟流程的格优先、复用」，盘点两产品一起做、尽量共用题型。
- 理解：CC 对话里点「盘点」，一张卡按「做什么」分支问齐，确定后转调盘点页同一组服务函数：
  - Cowork：模板（结果卡给模板文件）／新建（交 Excel → 标题）／记数（盘点单 → 物料 + 数量，可连续多行）／结束（允许卡）／导出差异表／删除（允许卡）。
  - ERP：新建（仓 → 标题）／记数（同上）／结束（允许卡）／生成调整单（草稿，人在调整单页提交才动库存）／删除（允许卡，只限进行中，同盘点页）。
- 不在范围：盘点算法、盘点页、LINE 手机盘点页一律不动；照片、逐笔改 / 作废记录、扫到未知码就地新建物料（页面有，CC 工具也没有）不进引导，仍在盘点页做。

## 1. 现有同类与复用

- **Cowork 网页有完整流程**（`stocktake/desk_api.py` 15 个接口）：新建（上传 Excel）→ 录入框搜物料 / 扫码记数 → 复核 → 结束 → 导出；LINE 富菜单「盘点」用 LIFF 打开同一网页。CC 工具 `cowork_stocktake_*`（`assistant/tools.py:3221` 起）已转调同一组：`upload.template / create / summaries / delete`、`desk_api.api_list_stock_counts / api_stock_count_lookup / api_stock_count_add_entry / api_stock_count_recount / api_stock_count_close / api_stock_count_review`、`service.export`、`counting.search`。
- **ERP 网页 + LINE 手机盘点页**（`inventory/line_mobile.py`，与桌面同一批接口）；CC 工具 `erp_stocktake_*`（`inventory/cc_tools.py`）转调 `inventory.count` 的 `list_counts / create_count / search_count_products / lookup_count / add_entry / recount_product / review_count / close_count / adjustment_draft / delete_count`，建单的仓走 `warehouses.prefill / required`（本人上次选的仓、唯一仓）。
- 结论：**全部复用**，不另查库、不另写规则。新写的只有两份「声明 + 薄转调」：
  - Cowork `assistant/guided/stocktake.py`（Cowork 只许 assistant 域调各业务域，同读回）；
  - ERP `inventory/cc_guide.py`（声明）+ `inventory/guide.py`（产品函数）（ERP 无域间限制，照收款放在业务域）。
- 「一份声明两产品用」：声明必须是各产品自己的纯字面量（ADR-009，点路径指本产品函数），做不到同一份；做法是**同一个业务 id `stocktake`、同菜单字 / 图标、同一套步骤 key 与题型**（`check-cc-guides.py` 核同业务两产品一致），两边函数各自转调本产品服务。

## 2. Frappe 原生能力

- 不涉及 Desk；引导运行用已有 `Pearnly Assistant Guide Run`；盘点 DocType 不变。

## 3. 按类通用

- 一份声明一个菜单项「盘点」，第一题「做什么」（`action`，choice），各分支用 `when` 分开（同对账「一项 + 选种类」）。
- 记数用 N4（`amounts` + `search` + `zero`：搜物料加一行、填数量、0 有效）；两产品共用这一题型，后面 ERP 其它「逐行记数」类（如以后的调拨）可照抄。

## 4. 上下游数据

- Cowork：盘点不连库存、不写账本、不写 Express；只写盘点任务 / 记录。
- ERP：记数、结束只动盘点单；**生成调整单只建草稿**（`adjustment_draft`，已有就回那一张），库存在人到调整单页提交时才变（岳父 11-06 口径不变）。调整单写 Express 走现有 ERP 单据直写，不在本类。
- 记数的仓 / 库位：同 CC 工具，默认这一项账面位置（Cowork 用复核快照里的行，ERP 由 `add_entry` 照页面口径带）；引导里不再问仓位（在别处找到的，去盘点页记）。
- 记法：一行一笔，同扫码累加；`how` 题「记法」默认「累加」，可改「改为此数」（= 先 `recount` 再记，同 CC 工具 `recount`）。
- 幂等：记数每行的 request_id 由「运行 + 版本 + 行码」算固定值，双击确定、重放只记一次（服务现有的操作回执重放）；Cowork 新建的单号照 CC 工具用「对话 + 附件」算 UUID（同一份表重复建只得到同一张）。

## 5. 隔离与权限

- 菜单：Cowork 有盘点功能（`stocktake.access.allowed`）才见；ERP 有 `count.write`（或 `count.read` 只读分支不出）才见。服务端每个服务函数照旧再核。
- Cowork 盘点不按公司隔开（用户 11-16：工作空间对象不分公司），选项列本工作空间全部盘点单。
- ERP 盘点属仓、仓属公司：一律在对话公司下执行（`masters.company.acting(ctx.company)`，同 CC 工具），选项只列这家公司的盘点 / 仓 / 物料。
- 选项按原样核码：盘点单在不在、是否还是进行中（记数 / 结束 / 删除只列进行中，调整单只列已结束），物料行是否属于这张盘点。

## 6. 入口

- CC 对话：业务菜单「盘点」+ 说一句（模型只打开引导）。手机同一张卡。
- 网页盘点页、LINE 富菜单 / 手机盘点页：不动。打字的 `*_stocktake_*` 工具保留（对话为辅）。

## 7. 共用件与连带回归

- 新文件：Cowork `assistant/guided/stocktake.py`；ERP `inventory/cc_guide.py`、`inventory/guide.py`；两产品 hooks `pearnly_guides` 各追加一项、术语表追加；`scripts/impact/shared-parts.toml` assistant 的 `not_entries` 登记三个新模块。不改 `stocktake/*`、`inventory/count.py`、引擎文件。
- 依赖引擎能力：**N4**（已在 engine-n）、**N2**（结果卡带文件，产品侧先带上）、**N10**（没有草稿的业务，结果用确定时的回包）、**N5 + 一个扩展（见第 12 节第 1 条）**：同一份声明里结束 / 删除要允许卡、其余要「确定」。

## 8. 存储与成本

- 不新增存储（模板 / 差异表文件放进对话，同 CC 工具），无外部调用、无付费识别（Excel 解析是本地的），无新增固定月费。

## 9. 删除、作废、追溯

- 删除走允许卡，复核卡列清删哪几张（同 CC 工具 `preview_stocktake_delete`）；Cowork 删除照现状真删（轻便工具口径）；ERP 只删进行中的（服务已拦）。
- 结束走允许卡，复核卡列已盘 / 未盘 / 有差异项数与差异表（只列有差异的），结束后只能看与导出（同页面）。

## 10. 文案与官方 UI

- 新增词条（四语，过 `check-copy.py`）：`guide.business.stocktake`「盘点」（沿用已有译文）、`guide.open.stocktake` 开场一句、`guide.step.stocktake.*` 步骤标题（做什么 / 盘点单 / 物料与数量 / 标题 / 文件 / 仓库 / 记法）。动作选项名尽量沿用盘点页按钮已有词条（新建、结束盘点、导出、删除、按盘点结果调整物料、下载模板）；记法两项「累加」「改为此数」若无现成词条则新增。
- 不改已有文案。

## 11. 测试与验收

- 离线（照读回引导的替身写法，各产品一份）：菜单权限；各动作分支的步骤与 `when`；盘点单选项按动作过滤（进行中 / 已结束）、按原样核码；物料搜索翻页、核答案只认本盘点的行；数量 0 收、负数拒；记法「改为此数」先清再记；确定只记一次（双击 / 重放）；结束 / 删除走允许卡、复核卡内容；结果：新建回单号与跳过行、导出 / 模板带文件、ERP 调整单回草稿号；对话公司换了失效（ERP）。
- 流程（接线后）：实验栈假智能体，Cowork 新建 → 记两行 → 结束 → 导出；ERP 新建 → 记一行 → 结束 → 生成调整单。
- 真模型：说「帮我盘一下点」→ 只打开引导。

## 12. 要问主会话的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 同一份声明里，结束 / 删除要允许卡、新建 / 记数要「确定」；现在 `consent` 是整份声明一个值 | 引擎小扩展：`consent` 除 `"allow"` 外允许写 `{"key": "action", "in": ["close", "delete"]}`（同 `when` 的形状，纯字面量可核），`run.view` 按当时答案给页面 `consent`。`declaration.py` 归 guided-cowork（engine-n），`run.py` 那一行随分支 3 | 不扩展就要拆成两项菜单（「盘点」+「结束 / 删除盘点」），菜单多一项 |
| 2 | ERP 引导里加「删除」（总表 ERP-D-ST 没列，CC 工具也没有；盘点页有，只限进行中） | 加（覆盖所有功能的口径，允许卡） | 不加则 ERP 删盘点只能去页面 |
| 3 | 记数时扫到系统没有的码就地新建物料（多仓闭环 10-26 口径）进不进引导 | 本批不进（CC 工具也没有，页面有）；搜索不到时题目下一行事实「去盘点页新建」不加（不加说明文案），只是搜不到 | 进则要新写「新建物料」一整套题，约 +3 小时 |

## 13. 工作量与排期

- 写码：Cowork 4–5 小时、ERP 3–4 小时（同一套题型，第二份照抄）；机器：闸门 + 候选离线约 1.5 小时；接线后流程 + 真模型约 1 小时。
- 依赖：服务端与离线测试不等人；允许卡分支等第 12 节第 1 条定；页面显示等分支 3。
