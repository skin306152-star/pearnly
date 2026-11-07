# ERP 按行选物料与库存 —— 施工记录（11-07）

评估与审定：分支 `pearnly/line-item-stock-choice-770877` 的 `docs/work/2026-11-06-erp-line-item-stock-choice-评估.md`
（第 12 节 14 条按建议定；#4 按行记住不再问，#9 不加 CC 建单工具，#14 从合入库存整改后的 main 开）。Cowork 部分见
`pearnly/cowork-line-item` 的 `docs/work/2026-11-06-cowork-line-item-choice.md`。

分支 `pearnly/erp-line-item-choice`：从 `origin/pearnly/cowork-line-item` 开、变基到 `origin/main` = 08f32a3b1（含 11-06 晚已部署的 ERP 库存整改
与 CC 1.12.15），无冲突；11-07 混行窗口把 `pearnly/cc-mixed-lines` 合入 main（a9348a5d6）、`pearnly/cowork-line-item` 换底到 108f49aca 后，
本分支随之 `rebase --onto` 两次（只差 CC 桌面版与文档，产品代码、脚本、测试与验证时一字不差）。合入顺序：cowork-line-item → 本分支。
共用层 `pearnly_core` 本分支**没有改**（只复用 Cowork 分支里的 `item_subject` / `norm_name` 与 cc-mixed-lines 的载荷 19）。

## 做了什么

| 项 | 落点 |
|---|---|
| 预选规则（评估 4.2 的 8 条，纯函数） | `products/line_rules.py`：人选 → 不可抵扣增值税行 → 记忆 → 各家对方结论一致 → 认出的物料 → Express 非库存品只记账 → 识别说服务 / 费用单 → 新建并入库（未定）；卡片标签 |
| 明细物料记忆 | 新 DocType `ERP Line Item Memory`，只经 `products/line_memory.py` 读写；键 = 公司 × `item_subject`（`方向\|对方税号或名称\|规范品名`，与 ERP Express Choice 的 `item` 同一个键，评估 15.3 / C1）；记物料、入库与否、仓位、最后一张单；提交成功后按最终值覆盖，作废不撤销；记住的物料停用 / 是别家公司 Express 的档当没记过 |
| 销售行类型 | `ERP Sales Document Line.item_type`（goods = 出库 / service = 只记账，同采购）；补丁 `v1_26_sales_line_item_type` 按「这张单有这件物料的出库流水」回填，不动库存 |
| 采购提交 | 没给类型的行按预选补；`update_stock` 由行推出（有一行入库就是 1，旧接口明确传 0 照旧整张不入库）；随单新建的物料回填 `origin_doctype / origin_name`；提交后学习记忆 |
| 销售提交 | 只出 `goods` 行；收据里勾了出库、认不到物料的行随单新建并出库（评估 12 节 #2，与 LINE 一致），报价单只认不建；只记账的行不按名认物料、不出库；随单新建记来源；提交后学习 |
| LINE「新建」 | 行上 `new_item`：人在 LINE 里选了新建，就不按品名认（同名几件时也新建），`products/purchase.py` / `products/sales.py` |
| 识别后预选（网页） | 采购 / 销售识别回包每行按记忆 / 认出的物料预选（`recognise(prefill=True)`），页面照预选显示；LINE 按收单前选的对方另算（`prefill=False`） |
| LINE 上传前 | 去掉「服务 / 库存商品」和仓库两步（`line_flow.py`），摘要不再写类别 |
| LINE 识别后追问 | `integrations/line_items.py`：每行预选存在票上（`ERP LINE Intake.line_choices`，不随会话过期）；只有未定的行随卡片同一次发送追问「全部新建并入库（销售：出库）/ 全部只记账 / 逐项选择 / 取消」，逐项「新建并入库 / 只记账 / 选已有物料（打名称或编码找）」；有入库行、单头仓定不下来才问仓库（≤12 个按钮，多了打字找），同一批后面几张沿用（排着的那张回更新后的卡片）；仓建了仓位清单、入库行有没仓位的问一次仓位（+「不填」）；问完回更新后的卡片；一批一次问一张；取消只停这张（照预选）；旧按钮、别人的票无效；webhook 里按绑定账号查物料、算卡片金额（`handlers._as`） |
| LINE 卡片 | 整张一行「物料类型：库存商品 · 仓库」去掉；有入库行时列「仓库」；每行明细后标「入库 / 出库 / 只记账 / 新建 · 入库 / 新建 · 出库」；改版前识别的票照旧 |
| LINE 建单 | `build_payload` 按行带入库与否、物料、新建、仓位，有入库行才带单头仓，不再交整张开关；改版前的票照旧按 `item_kind`；提交不再在 LINE 这层替销售建物料（提交函数统一做） |
| 网页采购 / 销售 | 行主区「入库」/「出库」勾选（原生 Check，替掉收起区的「物料类型」下拉）；新行默认勾，费用单默认不勾、改成费用时没选物料的行取消；选了 Express 非库存品自动取消；明细下面「批量编辑」→ 原生 Dialog + Table：单头仓库、「全部」（入库 / 只记账）、每行勾选与仓位（Autocomplete 按单头仓清单），保存写回各行、不提交（`transactions.js` `p.trade.bulk_lines`）；不再交 `update_stock` / `auto_create_product` |
| Express 推送判据（评估 4.4） | `express/push.py::_lines`：有 Express 码的库存品只在 `goods` 行算库存行，只记账行写成非库存行、不带存货码；没码的行一律看 `item_type`（销售以前有物料就出库）；表单预检 `form_document` 销售同口径、收据里认不到的出库行按库存品准备；`link_products` 不把电脑回的码挂到只记账行的物料上 |
| 选择接口 | `masters/selection.py` 物料选项多给 `express_stock`（页面认 Express 非库存品用） |
| 文案 | 新增 16 条（四语，泰文按钮 ≤20 字）；删 `Service or stock item?`、`Stock item`；10.2 四条改动都是删除 / 换控件，没有改已有 msgid 的中文，`check-copy` 改动 0，不用登记 `copy-approved.tsv` |

未做（评估里写了、本批不做或没必要）：
- 批量编辑弹窗没有「物料」「单价」两列：物料在每行的物料格选（有搜索、新建、扫码），单价就是行上的单价（成本 = 行金额 ÷ 数量，#7）；弹窗只管入库与否和仓位。
- 行上不记「预选来源」字段（#13）：预选来源只在预选结果里（单测断言用），没加不显示的列。
- CC 对话不加建单工具（#9）。

## 行为变化（如实）

- LINE 上传前少两步；识别后多一条提问（只在有定不了的行、或要选仓 / 仓位时）。改版前识别、还没提交的票照旧。
- 网页销售：勾了出库、认不到物料的行现在随单新建物料并出库（以前不建、不出库）；不勾的行有物料也不出库（以前有物料就出库）。
- LINE 销售选「服务」时名字对上已有物料的行照样出库的旧问题（评估旁见）随按行选择消失。
- 只记账的行挂着 Express 存货时，写 Express 是非库存行（按名建 / 复用非库存品；CC 只在非库存类型 STKTYP 3/4/5 里按名找，不会挑中同名库存品，`companion/src/companion/product_master.py:82`）。
- 采购单整张 `update_stock` 由行推出：全是只记账的单 `update_stock = 0`，修改时不再带出旧值决定入不入库。
- 消息额度：提问与卡片放在同一次回复（超 5 条或令牌失效时同一次推送）里，按钮作答都是回复；LINE 官方计数规则本次没有重新查文档。

## 验证

- 单测（不连库，已登记 development.yml）：`tests/test_erp_line_item_choice.py` 16 条（8 条预选顺序、标签、记忆按公司隔离 / 覆盖 / 停用不套 / 结论一致才套、整张预选写行）；
  `tests/test_erp_express_direct.py` 新 `LineStockKinds` 5 条（Express 存货只记账 → 非库存不带码、销售按出库勾选、采购按入库勾选、非库存品保留码、混行载荷 19），全文件 179 条通过。
- 系统路径不按当前账号核公司（`tests/test_erp_system_paths_company_scope.py`）：第一轮全闸门拦下识别任务里取预填仓走到 `document_company`，
  改成按这张票的公司与发单人取（`warehouses.prefill_for`、`company.terminal_warehouse`，网页 `prefill` 照旧先核公司再调它），没有加安全边登记。
- 要 Frappe 的：`integrations/test_line_intake.py`（生产镜像 fb1037ad 底座、工作树代码挂进去、断网跑）97 条通过，含新 `ChosenPayload` 3 条、`LineItems` 8 条（全定不问、四个按钮、全部新建回卡片、逐项 + 只记账 + 选已有物料打字找、仓库只问一次且同批沿用、仓位一次 + 不填、一批一次一张 + 取消接下一张、别人的票无效）、卡片标签与仓库行，上传前流程测试按少两步改。
- 实验栈 `pearnly-lineitem-lab`（本机，生产镜像 fb1037ad + 本工作树，`lab_init` 全部站点 migrate 通过，含新 DocType 与回填补丁）：
  新流程 `erp.trade.line-stock`（采购）、`erp.trade.line-stock-sales`（销售）两套数据 × 电脑 / 手机 **8 格全过**：三行（已有物料 / 运费 / 新品名）默认都勾、批量编辑列三行与单头仓、「全部」入库后取消运费行、保存写回、提交；库里类型依次 入库 / 只记账 / 入库，只有第一、三行有流水，运费行不建物料，新物料记着来源单据，三行都记进记忆。
  新流程 `erp.line.card-lines`（LINE 卡片「编辑」打开的采购页：实验室直接建已识别的收单记录、跑每行预选，页面三行勾选 勾 / 不勾 / 勾、
  认出已有物料、带单头仓，提交后只有入库行有流水、记进记忆）4 格全过。
  `run.py --changed --base origin/main`（按提交后的改动算范围）：**26 条流程、两套数据 × 电脑 / 手机 94/94 通过**（cc.chat、cc.operator、
  cc.operator.replay、cowork.* 7 条、erp.company.create、erp.document.express、erp.express.new-item-stock、erp.express.service_vat、
  erp.line.card-lines、erp.products.create、erp.purchase.create、erp.sales.scan、erp.stock-adjust、erp.stock-entry.scan、erp.stock-in.units、
  erp.stock-transfer、erp.trade.line-stock、erp.trade.line-stock-sales、erp.warehouses.codes、erp.warehouses.rules；交接说的 cc.operator.replay
  这回也过了，前面的流程已建出第二张采购单）。结果 `~/pearnly-devtools/flows-erp-line-item-changed2.json`。
- 本地全闸门 `gate.sh`（照 development.yml 逐步）：第一轮 199 过 2 不过（上面系统路径一处、流程覆盖一处：新模块 `line_items.py` 要有流程兜底 → 加了
  `erp.line.card-lines`，并按闸门要求从 `tests/flows/behind_baseline.txt` 删掉已被流程覆盖的 7 个存量模块），修完整套重跑 **201 步全过**。
- 架构 `check-architecture.py` PASS；文案 `check-copy.py` 拦截 0；产品地图通过（新行 `erp.line.postback.erp_item`）；共用件 `check-impact.py` 登记完整（新入口 `erp.line.item-questions`、`erp.trade.line-stock`）；大数据量闸门通过。
- **没测到**：LINE 模拟 webhook 冒烟（实验栈站点没配 LINE 频道）；识别后追问的端到端（要付费识别）；三星真机 LINE；测试机真 Express 写入混行单；原生混行证据 5 条（主会话安排）；生产镜像候选离线检查整条（本分支没改 `pearnly_core`；但 `integrations/test_line_intake.py` 这份要 Frappe 的已在生产镜像 fb1037ad 底座上单独跑过，97 条通过）；
  批量编辑弹窗没有截图存证（流程断言了弹窗行数、单头仓、写回）。

## 共用件连带回归（`check-impact.py` 清单，记结果）

| 入口 | 结果 |
|---|---|
| erp.trade.line-stock（新） | 通过：实验栈流程 8 格 |
| erp.line.item-questions（新） | 没测到真机（要付费识别 + 真 LINE）；单测 8 条 + 建单 3 条通过 |
| erp.line.intake.submit / erp.line.webhook-sim / erp.line.chat | webhook-sim 没测到（实验栈无 LINE 频道）；chat 要三星真机，主会话安排 |
| erp.entry.choices / erp.purchase.item-scan / erp.sales.item-scan | 通过：erp.purchase.create、erp.sales.scan 各 4 格 |
| erp.line.card-edit（卡片编辑页） | 通过：erp.line.card-lines 4 格（实验室建的收单记录，未经真 LINE） |
| 预填仓（warehouses.prefill 拆层） | 通过：erp.warehouses.rules、erp.stock-*、erp.purchase.create 等；`tests/test_erp_locations.py` |
| erp.cc.write（混行写入） | 没测到真 Express；推送判据单测通过；CC 混行写入已随 a9348a5d6 合入 main |
| 其余（主档隔离、打印、收银按码找物料等，经 selection / products 连带） | 本分支对 selection 只多给一个字段 `express_stock`；物料新建、按码认相关流程通过；主档隔离、打印、收银没有单独再点（没测到） |
