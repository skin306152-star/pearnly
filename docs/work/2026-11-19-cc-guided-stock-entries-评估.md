# 需求影响评估：CC 引导式 · 库存四单（出库、调拨、物料调整、入库）

> 总表 `scripts/impact/cc-coverage.toml` 的 `stock_docs` 一行（ERP 直写现为 `todo:p4`、`wait:mrerp`、`wait:cloud`）。
> 基线：`origin/main` 1a91b178b；引擎与已写好的同类引导以 `origin/pearnly/cc-guided-stocktake-new-product`（盘点）、
> `origin/pearnly/cc-guided-ui-review`（菜单「更多」）为准。Express 写法以本分支 `pearnly/stock-express-parity-3afd8c`（902f9ee23，载荷 22）为准。

## 0. 需求原文与理解

- 主会话 11-19（转用户口径）：四个库存 CC 引导：
  - 只在 **ERP × 直写 × 本地 / 云端 Express** 下出现；**MR.ERP 账本或代录模式下不显示**；LINE 不做；
  - 实现要做到 Cowork 以后**只加一份声明**就能用；
  - 排在采购、销售、收款、盘点之后，收进「更多」。
- 用户 11-18 晚：取消位置 / 仓位，统一叫仓库；仓库放单头还是按行选，照 Express 原生对应菜单定。库存四单在原生里每行可选仓，所以**按行选仓**。原「D11 一单一仓」作废。
- 理解：CC 对话里点「更多 → 出库 / 调拨 / 物料调整 / 入库」，一张张题卡问齐，进复核卡，确定后转调网页同一个建单并提交的服务（`documents.create_entry(submit=True)`）。提交后照现有链路写 Express（`express_push.queue`），结果卡给 ERP 单号和 Express 单号。
- 不在范围：
  - 改网页库存四单页面、改 Express 写法（本分支已对齐原生）；
  - 作废与改已提交的单（总表 `not_now` 一行：作废 / 改已进账本的单先不做）；
  - 草稿列表里的旧草稿（网页做）；
  - 附件（网页「新建时先选附件」，引导第一版不带，见 12 节第 4 条）。

## 1. 现有同类与复用

- **网页**：`public/desk/stock-entry.js` 一个页面函数管四种方向（`in / out / transfer / adjust`）。
  - 单头：日期、仓（调拨多一个调入仓）、用途（只出库、入库有）、备注；
  - 明细：物料、数量、单位，入库和调整还有单价；
  - 提交前先跑 `p.serials.collect`（按台管理的物料选台 / 登记台），再跑 `p.express_preflight`（写账前置判定，缺簿时问一次），最后调 `api_create_stock_entry(submit=1)`。
- **服务**：`inventory.documents.create_entry`，前面是 `api.api_create_stock_entry`。
  - 已有：权限 `stock.<种类>`、仓属公司、`warehouses.remember` 记本人上次选的仓、`default_unit_cost` 默认单价、`numbering.allocate` 发号、按台、提交即过账并排队写 Express。
- **CC 打字工具**：库存四单**没有**（`inventory/cc_tools.py` 只有盘点和收发存报表）。所以引导就是 CC 里唯一的路，不存在「打字路径对拍」。声明里不写 `tool`，按约定可空。
- **同类引导**：盘点（`inventory/cc_guide.py` + `inventory/guide.py`）的写法整套照抄：
  - `_acting` 在对话公司下执行；
  - 选项分页 `_page / _only`；
  - 物料搜索转调服务端查询；
  - 幂等的 request_id 由「运行 + 版本」算；
  - 草稿 / 复核 / 确定三段。
- 结论：**全部复用服务**，新写的只有一份声明 + 一份产品函数。

## 2. Frappe 原生能力

- 不涉及 Desk；引导运行用已有的 `Pearnly Assistant Guide Run`；`ERP Stock Entry` 不变（按行选仓由 ERP 界面窗口改，见第 7 节）。

## 3. 按类通用

- **一种业务一份声明，四份声明共用一个产品函数模块。** 这样四项各自是菜单上的一项（主会话的口径是四个引导），步骤 key 和题型一致；方向由声明里点名的函数区分（`out_*`、`transfer_*`、`adjust_*`、`in_*` 薄包装同一个实现）。
  - 不做成「库存单」一项再选做什么：那样只占「更多」里一格，但多一题。四项各占一格，与网页四个菜单一一对应。**建议四项。**
- **步骤（四种共用，按方向用 `when` 或各自声明里取舍）：**

| key | 输入 | 规则 | 出库 | 调拨 | 调整 | 入库 | 来源（转调） |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `day` | date | auto（对话月份里的今天，否则月末） | ✓ | ✓ | ✓ | ✓ | 同收款 `day_default / day_check`：只许账套期间内 |
| `purpose` | choice | always，tier `how`，不预选 | ✓ | | | ✓（退库 / 其他） | `documents.PURPOSES` |
| `book` | choice | remember（company） | 按用途缺簿时 | 缺 RL 簿时 | 缺 JU 簿时 | 缺 PI 簿时 | 同网页的写账前置判定：账套里没有这本簿时问一次、按公司记住（D2）；有簿就不出题 |
| `warehouse` | choice | auto（本人上次选的 / 唯一的仓） | ✓ | 调出仓 | ✓ | ✓ | `warehouses.prefill_for` |
| `to_warehouse` | choice | always | | ✓ | | | 同公司其他启用的仓 |
| `rows` | amounts + search | always | ✓ | ✓ | 「增加」 | ✓ | 物料搜索；出库 / 调拨列的是这个仓**有结存**的物料，名字后面带结存 |
| `minus` | amounts + search | always，`required: False` | | | 「减少」 | | 同上，列有结存的 |
| `prices` | amounts | auto（`default_unit_cost`），复核里可改 | | | 增加行 | ✓ | 选项是 `rows` 已选的那几行 |
| `note` | text | always，`required: False` | ✓ | ✓ | ✓ | ✓ | 单头备注（写进 Express STKDES） |

- **按行选仓的落法**（取消一单一仓）：`warehouse` 只当「默认仓」，物料选项的码是「仓库 + 物料」，显示「物料 · 仓库 · 结存」。
  - 搜索先列默认仓，再列同公司其他仓的；
  - 一行一个仓，同一件物料在两个仓就是两行。
  - 调拨的调入仓照 Express RL 原生定：单头一个调入仓，还是每行各一个，以 ERP 界面窗口实录的原生为准；引导跟着服务端载荷走，不另定。
- **调整的正负**：引擎的金额多选只收正数（`steps._rows_problem`）。所以分「增加」「减少」两题，各自是正数，合成带符号的明细，与网页「减少输负数」写进服务的形状一致。不改引擎。
- **Cowork 以后加一份声明**：步骤 key、题型、菜单字、图标全部按业务 id `stock_out / stock_transfer / stock_adjust / stock_in` 定死；`check-cc-guides.py` 已核「同业务两产品一致」。Cowork 现在没有库存业务（总表 `out:Cowork 无库存业务`），等它有了库存服务，照同一组 key 写一份声明和一份转调函数即可，引擎和页面不动。

## 4. 上下游数据

- 确定 = `create_entry(direction, posting_date, lines, warehouse, to_warehouse, purpose, reason, submit=True)`，网页提交是同一次调用。
  - 提交即过账（流水）→ `express_push.queue` → CC 桌面版写 Express，用原生流水号（D5），簿按 D2 / D12，出库取价按 D4 方案 A；
  - REFNUM 写 ERP 单号，凭证建档人写 PEARNLY。
- **结果卡**：ERP 单号；Express 写入状态（排队 / 已写 / 失败原因）和 Express 单号，同网页列表的 `ledger_state / ledger_docnum`。状态变化由现有回执推送（同代录 `feed.receipt`），不另轮询。
- **按台管理的物料**：网页提交前逐行选台 / 登记台。引导第一版的建议见 12 节第 2 条。
- **幂等**：`dedupe_key` = 运行 + 版本（同盘点）。双击「确定」或重放只建一张。服务里已有 `dedupe_key` 字段，但 `create_entry` 只存它、不按它去重（已核）。所以产品函数在确定时先按 `dedupe_key` 查有没有已建的单，有就回那一张。
- **负库存**：出库、调拨、调整减少超过结存时，照 ERP 服务现有口径（服务拒就退回那一题，原因码四语）。Express 侧允不允许负库存，照账套的 NEGALLOW 设置（第 ① 项实测在做）。

## 5. 隔离与权限

- **菜单**：有 `stock.out / stock.transfer / stock.adjust / stock.in` 对应功能的人才见那一项（`allowed` 转 `guard.has`），四项分别判断。
- **账本种类**：声明写 `ledgers: ["express_local", "express_cloud"]`，`modes: ["chat"]`。
  - **引擎缺口**：`registry.menu(mode)` 现在只按模式和权限过滤，**还不按本对话账套的种类过滤**（模块文档原话是「在接上产品判定时加」）。不补这一步，MR.ERP 账套的对话也会出这四项。
  - 要引擎加 `menu(mode, ledger_kind)`，按声明的 `ledgers` 过滤，页面带上本对话账套的种类。归引擎负责的窗口做，或由我在本批一起补，见 12 节第 1 条。
- **公司**：一律在对话公司下执行（`masters.company.acting`）。仓和物料只列这家公司的；服务里的「仓属公司」再核一遍。
- 选项按原码核：仓在不在、启用没有；物料行属于这个仓；调入仓 ≠ 调出仓（服务已有 `SAME_WAREHOUSE`）。

## 6. 入口

- CC 对话（网页、手机网页、CC 桌面版）：业务菜单「更多」里的四项。
  - 顺序号 61–64，排在盘点 60 之后、收发存报表 70 之前；
  - 前端 `MENU_FIRST = 4`，排在第 5 位及以后的自动收进「更多」，不用为它写界面。
- 网页四个库存页面：不动。LINE：不做（主会话口径）。代录：不显示（`modes` 只写 `chat`）。MR.ERP 账套：不显示（第 5 节的引擎过滤）。

## 7. 共用件与连带回归

- 新文件：ERP `inventory/cc_stock_guides.py`（四份声明）、`inventory/stock_guide.py`（产品函数）；hooks `pearnly_guides` 追加一项；术语表追加；`scripts/impact/shared-parts.toml` 的 assistant `not_entries` 登记。
- **依赖 ERP 界面窗口的「仓位改仓库、按行选仓」**：`documents._own_warehouse` 现在把行上的仓一律换成单头的仓（一单一仓）。那个窗口改完，`create_entry` 才收行上的仓。
  - 引导按行选仓的那一步要排在它合入之后；
  - 在它合入之前，引导可以先按「单头一个仓」做，行上不带仓，合入后再放开——这会多做一版，与「不做过渡版」冲突。**建议等它合入再施工。**
- 引擎：第 5 节的账本种类过滤（`registry.menu`、页面带账套种类、`check-cc-guides.py` 不变）。改的是共用件，要按连带回归闸门把采购、销售、收款、盘点、收发存报表的菜单全部实测一遍。
- 不改 `documents.py` 的业务规则、不改 Express 写法、不改网页。

## 8. 存储与成本

- 不新增存储；无外部调用、无模型调用（引导全程不经模型）；无新增固定月费。

## 9. 删除、作废、追溯

- 引导只新建并提交。复核卡里能改、能取消，取消不留草稿：草稿只在引导运行里，确定之前不建 `ERP Stock Entry`。
- 作废已提交的单：不进引导（总表 `not_now`），去网页做；网页作废照原生写 Express 作废。
- 追溯：单据记录建单人；Express 侧 REFNUM 写 ERP 单号。

## 10. 文案与官方 UI

- 新增词条（四语，过 `check-copy.py`）：
  - `guide.business.stock_out / stock_transfer / stock_adjust / stock_in`：菜单字，沿用网页导航已有的 `nav.stockOut / nav.stockTransfer / nav.stockAdjust / nav.stockIn` 译文；
  - `guide.open.*`：四句开场；
  - `guide.step.stock.*`：步骤标题（日期、用途、簿、仓库、调入仓库、物料与数量、增加、减少、单价、备注），能沿用 `trade.*` / `stock.purpose.*` 已有词条的都沿用。
- 图标沿用白名单里已有的。白名单里没有出入库专用的，建议四项都用 `boxes`；要换别的图标需在白名单和前端图标表同一提交各加一项。
- 不改已有文案。

## 11. 测试与验收

- 离线（照盘点引导的替身写法）：
  - 菜单：四项分别按权限出；代录不出；MR.ERP 账套不出（引擎过滤）；
  - 步骤：用途只出库、入库有；调入仓只调拨有；增加 / 减少只调整有；单价只入库和调整增加有；
  - 选项：出库 / 调拨只列有结存的仓 + 物料，按原码核；调入仓不能等于调出仓；
  - 簿：账套有这本簿就不出题，缺簿时问一次、按公司记住，第二次不问；
  - 确定只建一张（双击 / 重放）；服务拒了退回对应那一题；换了对话公司，引导失效。
- 流程（实验栈假智能体）：四种各走一遍到「已提交」，读回 `ERP Stock Entry` 的方向、仓、行、价、用途。
- **真账本**：测试机 NLTTEST / 70EXP\test，四种各写一张，读回 STTRN / STCRD / GLJNL，与网页提交同一组数据的结果逐字段一致。这一步复用本分支的回归脚本 `scripts/express-stock-entry-regression.py`。
- 手机 375 宽、四语、「更多」里的位置。

## 12. 要问主会话的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 菜单按账套种类过滤（MR.ERP 不显示）是引擎的缺口，谁来补 | 引擎负责的窗口补 `registry.menu` 的 `ledgers` 过滤；它没空就我在本批补，按共用件闸门逐项实测菜单 | 不补则 MR.ERP 对话里也出这四项，点了才说办不了 |
| 2 | 按台管理的物料（出库、调拨、调整减少要选台；入库、调整增加要登记台）进不进第一版 | 进：选项函数按物料是否按台，多出一题「选台 / 登记台」（`questions` 子题，同 POS 认列），转调网页同一个 `serials` 校验。约 +3 小时 | 不进则按台物料只能去网页，要在选项里排除，会出现「搜不到」 |
| 3 | 先等 ERP 界面窗口的「按行选仓」合入再施工吗 | 等（不做过渡版） | 不等就要做一版一单一仓，之后返工 |
| 4 | 附件（网页新建时可挂附件）进不进 | 第一版不进，结果卡之后可补一步 `file`（同收款凭证） | 无 |
| 5 | 没接 Express 的 ERP 公司（纯 ERP 用库存）显示不显示 | 照口径不显示（只在本地 / 云端 Express 下出现）；如果用户本意是「MR.ERP 不显示」，没接账本的公司也可以显示 | 影响纯 ERP 客户能不能在 CC 里录库存单 |

## 13. 工作量与排期

- 写码：声明 + 产品函数 5–6 小时（四种共用一个实现）；按台一题 +3 小时（第 12 节第 2 条）；引擎账本过滤 1–2 小时（如由我补）。
- 机器：闸门 + 候选离线约 1.5 小时；流程 + 真账本四种约 1.5 小时（测试机时段要先登记）。
- 顺序：排在采购、销售、收款、盘点之后（主会话口径）；前置是 ERP 界面窗口的「仓位改仓库、按行选仓」合入，以及第 12 节第 1、2 条有定论。

## 主会话 11-19 定（覆盖第 12 节的建议）

1. **按台管理进第一版**：出库、调拨、调整减少逐行选在库的台；入库、调整增加逐台登记主号；入库「退库」收回已售 / 已出库的台。用引擎的 `questions` 子题（N1a），题目由「物料」那一步的值算出；选台转调网页同一个 `serials.search_units`，过账时 `serials.apply` 再全核。
2. **按行选仓**：物料选项的码是「仓库 + 物料」，显示「物料 · 仓库 · 结存」，一行一个仓；`warehouse` 只当默认仓。照 ERP 界面窗口分支 `pearnly/integration-erp-ui-three`（f1279e0c4）改完后的载荷写：行上带 `warehouse`；调拨单头一出一进（调出仓 `warehouse`、调入仓 `to_warehouse`，行上的仓服务端换成调出仓）。本分支不改 `documents.py`，测试用替身。
3. **附件要做**：可选的 `file` 步骤；对话里交的文件确定时存成本人私有的登记 File（同网页上传的原件 id 写法），走 `api_create_stock_entry` 的 `attachments`（先核后挂）。
4. **没接 Express 的纯 ERP 公司不显示**：声明 `ledgers: ["express_local", "express_cloud"]`；`express.conversation.guide_ledger` 对纯 ERP 公司回 `none`、对还没挂公司的对话回 `no_company`，`registry.fits_ledger` 两种都藏，直接开也拒（`run.start`）。
5. **钱和对象的题不预选、推荐排第一**（仓库、物料、单价、调入仓、用途、台）：`rule: always`，推荐值由 `default` 给（引擎 `_recommended_first`）；日期、账套后台定（`auto`）。
6. **复核卡点了确定才写**：`writes: True`、有 `review`；确定转调 `inventory.api.api_create_stock_entry(..., submit=1)`；幂等按「运行 + 版本」的 `dedupe_key`，确定前先查同键已建的单，有就回那一张。
7. 四份声明各一项菜单（顺序 61–64，图标 `boxes`），`modes` 只 `["chat"]`，不写 `tool`。
8. 结果卡：ERP 单号 + Express 写入状态与单号（同网页列表 `ledger_state / ledger_docnum`）。
9. 草稿规则：草稿只是字符串（「方向@答案摘要」），确定之前不建 `ERP Stock Entry`，不会有孤儿单。

## 施工记录（11-19，分支 `pearnly/cc-guided-stock-entries`，基线 7488ab8ef）

- 新文件：`inventory/cc_stock_guides.py`（四份声明）、`inventory/stock_guide.py`（四份共用的产品函数，方向按 `ctx["business"]`）、`tests/test_erp_stock_entry_guide.py`（真引擎 + 替身，25 条）、流程 `scripts/flows/erp_cc_guide_stock_entries.py`（登记 `erp.cc.guide.stock-entries`）。
- 追加：hooks `pearnly_guides`；ERP 术语表 42 行（菜单字沿用 `nav.stock*`，步骤名沿用 `trade.* / stock.purpose.label / serial.units / guide.step.collection.book`；新词只有「增加 / 减少」与一句「不在所选月份」）并重生成翻译包；CI 引导测试那一行；共用件 `assistant`、`erp-products-lookup`、`ocr` 的 `not_entries`。
- 与评估第 3 节步骤表的差异：
  - 出库、入库、调整的「物料」题不等默认仓（`warehouse` 只影响排序与单头默认仓），与仓库同一张卡；调拨的物料仍在调出仓之后（只列调出仓的）。
  - 单价改成必答（`always`，0 可收）：可不答的题在适用步骤都有值时会被直接跳到复核，卡上永远问不到；推荐值只给看得到成本的人。
  - 账套那一步是 `questions`（`auto`）：转调 `express.api.preflight`（网页提交前同一个判定），没有要问的进「已定」，有要问的一项一个单选子题、答了照网页记在公司名下。
  - 调整的增加 / 减少都可不答，确定前两边都空时退回「增加」（`stock_no_lines`）。
- 未做：真站点流程未跑（不起实验栈）；真账本（测试机 NLTTEST / 70EXP\test）未测；登记主号以外的副号、颜色没问（第一版只登记主号）。

## 评估作者意见与第二轮修改（11-19，总原则「网页上怎么做，引导就怎么做」）

1. **单价（已知限制，待改）**：本分支基线的引擎不收表单源的 `edit`（`declaration.problems`：「intake, uploads and edit are for documents guides」），复核卡改不了格子。按退一步的口径保留：单价题必答、0 也收，推荐值（`documents.default_unit_cost`）只给看得到成本的人（`guard.cost_visible`，同服务端判法；物料调整里看不到成本的人填的价服务端照旧不算）。**等页面接口分支 `pearnly/cc-guide-page-endpoints`（表单源 edit）进 main 后改**：单价题改成 `auto`、默认 `default_unit_cost`，复核表的单价格用 `edit(ctx, draft, row, field, value)` 改，这一格只给 `guard.cost_visible()` 为真的人。入库对看不到成本的人怎么处理（服务端照存人填的价）也要一起定。
2. **按台登记**：照网页按台登记弹窗（`public/desk/serials.js`）：每台问主号（必填）、副号、颜色（可空），交服务 `{serial_no, secondary_no, color}`；子题最多 50 个，一张单最多约 16 台按台登记。
3. **出库选物料**：同网页出入库页的物料下拉（在架物料，原生 Link 只按 `is_active` 筛），结存 0 也能出（服务端默认允许负库存）；四种共用 `item_options`，调拨只列调出仓的。
4. **结存显示**：网页出入库页的物料下拉不取结存（只调 `api_get_product` 与 `api_default_unit_cost`），所以引导也不显示结存；删掉了直接查 `ERP Stock Balance` 的那段。
5. **原件 id**：`recognition/originals.py` 开了公开入口 `original_id(direction, request_key)`（薄包装 `_original_id`，权限与幂等键核对照旧），引导调它；幂等键 = 「运行 | 对话文件编号」的 sha256 前 40 位。网页上传（同模块 `begin / finish / upload`）本来就调 `_original_id`，没有另拼。`inventory/count.py` 的盘点照片也拼了同一种写法，但那条路改成调公开入口会多一道 `count.write` 权限判断（调用方已判过），且它的测试要装 Frappe 才能跑，本次没改，留待确认。
6. **调拨**：保持单头一出一进两个仓；按行选仓等 f1279e0c4 进 main。
