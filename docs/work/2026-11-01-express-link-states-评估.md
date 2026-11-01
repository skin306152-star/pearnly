# 「接 Express」状态规则：核实与改法评估

> 来源：主会话 11-01 派单（用户要求「这个要设计好」）：把「接 Express」的状态规则核实成一张表并用测试锁住。
> 状态：① 没勾「接 Express」；② 勾了但 CC 还没报上这家公司的账套；③ 接上；④ 中途切换。入口：盘点（含盘点中新建物料、过账写 TK）、
> 入库 JU、出库 OU、调拨 RL、采购、销售、收银日汇总 HS 与退货、期初。
> 主会话先定两条口径：a) 勾选 / 连上之前做的单据，接上后不补推，从接上那一刻起算；b) 跨越切换的单据按单据开始时的状态定（盘点按开盘点那一刻）。
> 基线：main `2d484b99`（生产 c9e28ace 之后）。核实方法：逐个入口读代码、对照现有测试；标「推论」的没有测试或实测证明。
> 路径简写：`erp/` = `apps/pearnly_erp_portal/pearnly_erp_portal/`，`core/` = `apps/pearnly_core/pearnly_core/`，`desk/` = `erp/public/desk/`；
> `direct::X` = `tests/test_erp_express_direct.py` 里的测试 X。

**结论先说**：
- 现在不是按开始时判：每张单只在「可以写了」那一刻（提交 / 盘点过账 / 每晚汇总）判一次 `linked`，排上以后写到底，断开也不停（0 节、4.b）。
- 口径 a 大体成立（没有补推旧单的逻辑），但有四处反证：收银重新接上会补推断开期间的小票；采购「修改」接上前的旧单会推新单；
  库存对齐对旧单口径不一（出入库 / 盘点 / 小票叠加，采购 / 销售 / 期初被覆盖）；断开不停已排队的单（4.a）。
- 核出一个本批缺陷，已修：接上时盘点里新建的物料已挂本公司、还没有码，写完 TK 后 `link_products` 跳过它，编码回填不上
  （多公司租户同步时会另建同名物料）。改为同客户 / 供应商回填（`link_party`）的条件，补测试
  `direct::test_a_product_made_for_this_company_without_a_code_takes_it`。
- 建议的结构性改法见 5 节：公司上记「接上时刻」，单据按开始时刻与它比；断开立即停。要主会话定的四点在 5.4。

## 0. 判据与公共机制（所有入口共用）

| 项 | 实际行为 | 证据 | 测试 |
|---|---|---|---|
| connected(company) | 公司有 13 位税号，且 `Pearnly CC Book` 里有这个税号 + 分店的账套行（任何期间、任何电脑，不看电脑在不在线） | `erp/express/push.py:42-45`；`core/cc_desktop/books.py:96-116` | 无测试（只在别的测试里被 mock） |
| connected 何时变 False | 只有电脑重报名单时这份账套不在里面（`books.replace` 删行），或电脑退役/移除（`drop_device`）。电脑关机/离线**不会**变 False | `core/cc_desktop/books.py:19-48` | 未核实 core 侧测试 |
| linked(company) | `express_enabled` 勾着 且 connected | `erp/express/push.py:48-50` | 无测试 |
| 是否排写账（唯一判 linked 的点） | `push.queue` 在单据"可以写了"那一刻调一次：已有 `express_state` 不动；`_eligible` 按 **此刻** linked 判；该写就置 `express_state=queued`、`express_reason=""`，事务提交后排 `push.send` | `push.py:60-68`、`:71-87` | 无测试 |
| send / poll 不再判 linked | `send` 只看 status、`express_job`、`express_state∈(queued,held)`，**不看 express_enabled，也不看 connected**；`poll` 每分钟对 `express_state∈(queued,held)` 且状态 ready 的单调 `send`（`queued` 未交队列的，或 held 且原因在 RETRY_REASONS 的） | `push.py:267-328`、`:578-593`；`erp/express/contracts.py:28,48` | 无测试 |
| 账套没了怎么停 | `send`→`prepare`→`target.pick`：找不到账套 → `held` + `express.account_set_missing`（NO_BOOK，属于 RETRY_REASONS，每分钟重试）；有账套没电脑能写 → `held` + `express.no_writer`（也重试）；主档没读到 → `held` + `express.masters_reading`（重试） | `erp/express/target.py:34-51`；`push.py:302-308`；`contracts.py:31-48` | `direct::test_holds_until_the_computer_has_read_the_masters`、`direct::test_two_account_sets_for_the_same_year_are_chosen_once` |
| 已交进写账队列的件 | `send` 见 `express_job` 已有就直接返回；那件留在共用写账队列里，由电脑领走写入。断开 / 取消勾选都不撤它 | `push.py:273`；`masters/company.py:515-522` | 无测试；队列件在账套被删后会不会被领走：**未核实**（`core/cc_desktop/jobs` 未读） |
| 要人选的弹出 | 准备时停在 `choice_needed:*` 且原因变了 → `publish_realtime(pearnly_erp_express_needed)` 推给建单人；电脑转人工且是对方档/科目组/服务税/关账/库存不足 → 同样推 | `push.py:306-308`、`:409-414`；`desk/transactions.js` 监听 | 无测试 |
| 页面总开关 | 启动数据 `frappe.boot.pearnly_express = linked(当前公司)`（登录时算，切公司时按 `express_linked` 重算）；它为假时：不做提交前预检、列表不出 Express 列、单据不出「Express」按钮 | `erp/masters/boot.py:21-30,44-45`；`erp/masters/company.py:146-151`；`desk/company-menu.js:94`；`desk/transactions.js:149-150`；`desk/purchase-form.js:251`、`desk/sales-form.js:226`、`desk/stock-entry.js:288`、`desk/stocktake.js:208` | `tests/test_erp_boot_items.py::test_express_follows_the_current_company`、`::test_express_check_failure_defaults_to_not_linked` |
| 单据上的 Express 按钮 | 还要求 `doc.express_state` 非空：接上前做的单（state 空）接上后也没有按钮 | 同上各 js 行 | 无测试 |
| 提交前预检 preflight | 前端仅在 `boot.pearnly_express` 为真时调；服务端 `if not push.linked(company): return {"needed": []}`。只有采购、销售、出入库/调拨三个页面调；盘点、收银日汇总、期初不调 | `erp/express/api.py:166-208`（`:177`）；`desk/purchase-form.js:202`、`desk/sales-form.js:176`、`desk/stock-entry.js:311,363` | 回归流程 `scripts/flows/erp_express_new_item.py`（new 数据集问「物料」、old 数据集什么都不问）；无单元测试 |
| 取消勾选的两条路 | ① 公司资料保存 `update_company`：要取消且 **connected** → 抛 `Disconnect Express first`；没连上可以直接取消。② 「断开」按钮 `disconnect_express`：不管连没连上直接 `express_enabled=0`；**不撤排队件、不改单据状态** | `erp/masters/company.py:643-650`、`:515-522`；`desk/company.js:263-280`（连上时勾选框只读、给「断开」） | 回归流程 `scripts/flows/erp_company_settings.py`（只看页面三种状态）；无单元测试 |
| 自动勾选 | CC 报来**新**税号+分店的账套 → 后台建公司且 `express_enabled=1`；已有公司不改勾选。补丁 `v1_17` 一次性把有 `express_book` 或 connected 的公司勾上 | `erp/express/companies.py:58-85`（`:71`）；`erp/patches/v1_17_company_settings.py` | `direct::test_books_are_grouped_by_tax_and_branch_and_the_latest_name_is_taken`、`::test_only_connected_computers_that_report_the_chosen_roots_count` |
| 定时任务 | 每分钟 `push.poll`、`masters.advance`；每 10 分钟 `masters.check_marks`；每小时 `masters.sync_all`；每天 01:20 `pos_day.nightly`。**没有任何"接上那一刻扫旧单/补推"的任务**；doc_events 里没有 Express 相关钩子 | `erp/hooks.py:347-359`、`:558-574` | — |

状态取值：`express_state ∈ {空, queued, held, failed, written, mismatch, handled}`（`contracts.py:24-28`）；原因码见 `contracts.py:30-48`。

---


## 1. 总表（入口 × 状态）

图例：**写**=会排写账件；**不写**=`express_state` 保持空、什么都不排；时机写在括号里。④ 各列的"开始"指建单/开盘点，"判定点"指 `push.queue` 被调的那一刻。

| 入口（判定点） | ① 没勾 | ② 勾了未连上 | ③ 接上 | ④a 开始没接 → 判定点已接 | ④b 开始已接 → 判定点前取消勾选 | ④c 开始已接 → 判定点前 CC 不再报账套 | ④d 已排队后才取消勾选 / CC 断开 / 断开后取消勾选 |
|---|---|---|---|---|---|---|---|
| 盘点 TK（过账 `post_count`） | 不写 | 不写 | 写（过账时） | 写（按过账时判）；途中新建物料 `express_company=None` | 不写；途中新建物料已挂本公司 | 不写，且之后 CC 回来也不补 | 照写（见 0 节：send 不复核） |
| 入库 JU（提交） | 不写 | 不写 | 写（提交时） | 草稿在接上后提交 → 写 | 草稿在取消后提交 → 不写 | 不写，不补 | 照写 / 停 NO_BOOK 每分钟重试 |
| 出库 OU（提交） | 同 JU | 同 JU | 同 JU | 同 JU | 同 JU | 同 JU | 同 JU |
| 调拨 RL（提交） | 同 JU | 同 JU | 同 JU | 同 JU | 同 JU | 同 JU | 同 JU |
| 采购（提交 `entry.post` / 旧 `service` 草稿→提交） | 不写 | 不写 | 写（提交时） | 草稿接上后提交 → 写；接上后「修改」旧单 → 新单写 | 不写 | 不写，不补 | 照写 / 停 NO_BOOK |
| 销售（提交 `entry.post`，只收据 receipt） | 不写 | 不写 | 写（提交时） | 草稿接上后提交 → 写 | 不写 | 不写，不补 | 照写 / 停 NO_BOOK |
| 收银日汇总 HS / 退货 SR（每晚 01:20） | 不汇总 | 不汇总 | 汇总并写 | 按小票日期 ≥ `express_pos_from` | 当晚不汇总；小票留着，重新接上后补汇总 | 同左 | 已建好的 HS/SR 照写 / 停 NO_BOOK |
| 期初（预览/提交时） | 允许录 | 允许录 | 拒（「Opening stock is read from Express」） | 接上后录 → 拒；接上前录的 → 同步时被 Express 结存覆盖 | 允许 | 允许 | — |

---


## 2. 逐格证据

### 2.1 盘点（`erp/inventory/count.py`；TK 由 `erp/express/stock_entry.py:count_document`）

- **公司**：按盘点单头仓库的公司（`count.py:162-164`；`stock_entry.py:57-59`）。
- **开盘点** `create_count`（`count.py:171-205`）：不判 linked，不记任何 Express 字段（`ERP Stock Count` 上只有 `express_state/docnum/reason/sent_at/job/book/needed`，没有"开始时状态"字段）。
- **盘点中新建物料** `create_product_here`（`count.py:398-453`）：`express_company = company if linked(company) else None`（`:448`）——按**建物料那一刻**判，不按开盘点那一刻。无单元测试（回归流程 `scripts/flows/erp_stocktake_new_product.py` 只看建档与记数，不看 express_company）。
- **过账** `post_count`（`count.py:820-909`）：每个差异 `create_entry(ref_type="count", submit=True, commit=False)`（`:883-891`）→ 生成的出入库单在 `_posted` 里也调 `push.queue`，但 `ref_type=count` 在 `SKIPPED_REFS`，不逐张写（`documents.py:339-347`；`stock_entry.py:43,62-64`）。整份盘点置 `Posted` 后 `express_push.queue(COUNT, name)`（`:904`）→ **过账时**判 linked。
- **写什么**：`send` 时 `count_document` 把这份盘点生成的 submitted 出入库单按「存货 × Express 仓」轧差成一张 TK（`stock_entry.py:148-183`）；全部轧成 0 → `express_state` 清空、不写（`push.py:284-288`）。没有 Express 编码的物料进 `unmapped_items`；存货表没读到 → `held` + `express.item_unmapped`；读到了 → 按物料问一次（`choice_needed:item`，主题 `stock|<物料>`，同名存货不问），选新建的行带 `new_ok`、载荷 15（`stock_entry.py:186-212,231-269`）。
- **页面**：盘点没有提交前预检；停下要人选的靠实时推送弹窗；盘点页「Express」按钮要 `task.express_state && boot.pearnly_express`（`desk/stocktake.js:208`）。
- **写完回填**：`job_changed` → `link_products`。原来跳过 `express_company` 已有值的物料（`push.py:483`），接上时盘点里建的物料（已挂本公司、没有码）回填不上；**11-01 已修**，同 `link_party` 放开「已归这家公司、还没有码」的档，测试 `direct::test_a_product_made_for_this_company_without_a_code_takes_it`。
- 测试：`direct::test_a_count_nets_its_entries_into_one_count_document`、`::test_a_product_express_does_not_have_is_asked_once_and_new_marks_the_line`、`::test_a_same_name_item_or_a_code_on_the_line_is_not_asked`、`::test_new_items_carry_the_stock_group_chosen_for_the_book`、`::test_without_the_item_list_a_count_with_new_items_still_holds`、`::test_stock_documents_count_and_pos_day_link_the_codes_the_computer_used`（只测 `express_company` 为空的物料）、`::test_an_entry_from_a_count_sent_to_express_cannot_be_voided_on_its_own`。`post_count` 调 queue、linked 判定：无测试。

| 状态 | 写不写 / 何时 | 停在哪 | 页面 | 新建物料 express_company |
|---|---|---|---|---|
| ① / ② | 过账时 `_eligible` 为假，不写，`express_state` 空 | — | 无 Express 按钮 | None（本地档，各公司可见） |
| ③ | 过账时排；`send` 后台准备 | 缺编码且没读存货表：`held/express.item_unmapped`；要选：`held/choice_needed:item`（或 `:account`）；仓没码：`held/express.warehouse_unmapped` | 无预检；弹窗靠实时推送 | 本公司 |
| ④a 开盘点没接、过账已接 | 写（按过账时） | 途中新建物料没编码 → 同上问「物料」 | 同③ | None；写完后 `link_products` 会认领（若没在别家公司仓里动过） |
| ④b 开盘点已接、过账前「断开」 | 不写，`express_state` 空 | — | 无按钮 | 已挂本公司（断开后仍只本公司可见） |
| ④c 开盘点已接、过账时 CC 不再报账套 | 不写；之后 CC 回来**不补**（没有任何扫旧单） | — | — | 已挂本公司 |
| ④d 过账已排队后断开 / 取消 | 照写（`send`/`poll` 不复核 express_enabled）；CC 不报账套时停 `held/express.account_set_missing` 每分钟重试，CC 回来就写 | 同左 | 断开后 `boot.pearnly_express` 为假，按钮/列都隐藏，看不到停下的单 | — |

附：开盘点没接、中途接上并首次同步 → 同步按 Express 结存调整 ERP 库存，会让已盘物料 `stock_changed`，过账被拒「Stock changed during counting」需重盘（`count.py:768,833-834`；同步写调整流水 `erp/express/masters.py:814-857`）。推论，无测试。

### 2.2 入库 JU / 出库 OU / 调拨 RL（`erp/inventory/documents.py`；`erp/express/stock_entry.py`）

- **公司**：单头仓库的公司（`stock_entry.py:57-59`）。
- **判定点**：`_posted` 置 submitted 后 `queue`（`documents.py:339-347`）。直接过账（`create_entry(submit=True)`，`:92-159`）= 建单那一刻；草稿后提交（`submit_entry`，`:350-360`；`update_entry(submit=...)`，`:254-`）= 提交那一刻。草稿创建不判。
- **不写的类型**：采购自动入库（`ref_type=purchase`）、盘点生成（`ref_type=count`）（`stock_entry.py:43,62-64`）。
- **单别**：出库 OU、入库 JU、调拨 RL（`stock_entry.py:67-72`）。
- **预检**：提交前 `p.express_preflight("ERP Stock Entry", ...)`（`desk/stock-entry.js:311,363`），服务端 `stock_entry.prepare(form_body(...))` 问科目（`account`，主题 `stock:<单别>`）和 Express 里没有的物料（`item`）。预检用的公司是 `document_company(payload.company)`，出入库页没传 company → 当前公司（`api.py:175`；`masters/company.py:220-228`）；仓库也限当前公司（`warehouses.required`），两者一致的前提下没问题——仓库解析细节（`warehouses.resolve`）**未核实**。
- **作废**：`void_entry` → `push.void`：已写进去（written/mismatch 且有单号）→ 排 Express 原生作废；排队中 → 撤排队件；没写过（state 空）→ 只在 ERP 作废（`documents.py:363-384`；`push.py:334-362`）。`push.void` 不判 linked：断开后作废已写的单照样排作废件。
- 测试：`direct::test_kinds_and_what_is_not_written`、`::test_a_ready_payload_names_the_book_the_lines_and_the_version`、`::test_holds_until_the_masters_are_read_and_asks_once_between_two_books`、`::test_a_transfer_is_a_native_rl_with_both_locations_and_no_cost`、`::test_a_transfer_holds_only_when_the_computer_reported_no_rl_book`、`::test_the_form_asks_for_the_lines_before_submit`、`::test_a_default_account_that_cannot_be_posted_is_chosen_once_per_company`、StockVoid 类 4 个（`test_a_written_entry_queues_a_void_...`、`test_voiding_before_the_computer_wrote_it_cancels_the_queued_job`、`test_a_write_that_lands_after_the_void_is_voided_at_once`、`test_an_entry_never_written_is_only_voided_in_erp`）、UnmappedWarehouse 2 个；回归流程 `scripts/flows/erp_express_new_item.py`（new/old 两套）。queue 时的 linked 判定：无单元测试。

| 状态 | 写不写 / 何时 | 停在哪 | 页面 | 新建主档 |
|---|---|---|---|---|
| ① / ② | 不写 | — | 不预检、无 Express 列/按钮 | 出入库不建物料 |
| ③ | 提交时排 | 科目：`held/choice_needed:account`；物料：`choice_needed:item`；账套无该单别：`held/express.stock_book_missing`；仓没码：`held/express.warehouse_unmapped` | 提交前预检会问科目、物料 | — |
| ④a 草稿接上前建、接上后提交 | 写 | 同③ | 预检取决于页面打开时的 boot（接上后没刷新页面就不预检，提交后停下再推送弹窗） | — |
| ④b 草稿接上时建、断开后提交 | 不写 | — | — | — |
| ④c 提交时 CC 不报账套 | 不写，不补 | — | — | — |
| ④d 已排队后切换 | 照写 / 停 NO_BOOK 重试 | `held/express.account_set_missing` | 断开后隐藏 | — |

### 2.3 采购（`erp/purchasing/entry.py`、`erp/purchasing/service.py`）

- **判定点**：`post` 里 `_post`（含 finalize）之后、提交事务前 `queue`（`entry.py:122-148`，`:143`）；旧入口草稿→提交同样 `queue`（`service.py:325-328`）。只写 `doc_kind ∈ (purchase_invoice, expense, goods, service)`（`contracts.py:50`；`push.py:67-68`）。
- **随单建供应商**：`express_company = company if linked else None`（`entry.py:69-74`），与 queue 同一事务、同一时刻判。
- **随单建物料**：`products.purchase.resolve` 建本地档，**不看 linked，不设 express_company**（`erp/products/purchase.py:29-47`）。写完由 `link_products` 认领（只被这张单用过的本地档）。
- **预检**：`desk/purchase-form.js:202`（带 company）。
- **作废 / 修改**：`cancel` / `_cancel` 只在 ERP 冲库存、改 void，**不排 Express 作废**（`entry.py:283-299`；`push.void` 只认出入库单，`push.py:336-337`）。「修改」（Amend）= 作废原单 + 提交新单（`entry.py:158,206`），任何已提交的采购单都给这个菜单，不看 Express 状态（`desk/purchase-form.js:250-255`）→ 新单按提交时 linked 再排一次。
- 测试：`direct::test_purchase_and_sales_payloads_from_the_shared_rules`、`::test_a_service_purchase_asks_for_the_expense_account`、`::test_zero_rate_lines_of_a_taxed_purchase_are_marked_exempt` 等 Prepare / MixedRatePurchase 类；`LinkParty` 3 个、`LinkProducts` 3 个；回归流程 `scripts/flows/erp_document_express.py`、`erp_express_service_vat.py`。queue/linked、作废、修改：无测试。

| 状态 | 写不写 | 停在哪 | 页面 | 新建供应商 / 物料 |
|---|---|---|---|---|
| ① / ② | 不写 | — | 不预检 | 供应商 None；物料本地 |
| ③ | 提交时排 | 要人选：`held/choice_needed:*`（科目、对方档、账套…）；仓没码：`held/express.warehouse_unmapped` | 提交前预检会问 | 供应商挂本公司；物料本地，写完认领挂码 |
| ④a 草稿接上前建、接上后提交 | 写 | 同③ | 同 2.2 ④a | 供应商按提交时（挂本公司） |
| ④a′ 接上前已提交的旧单，接上后「修改」 | 新单写进 Express（旧单不推，新单按修改内容推） | 同③ | — | — |
| ④b / ④c | 不写，不补 | — | — | 供应商 None |
| ④d 已排队后切换 | 照写 / 停 NO_BOOK | — | 断开后隐藏 | — |

### 2.4 销售（`erp/sales/entry.py`）

- **判定点**：`post` 里 `queue`（`entry.py:148-172`，`:167`）。只写 `doc_kind = receipt`（`contracts.py:51`）。
- **随单建客户**：`owner = company if linked else None`（`entry.py:136-142`），与 queue 同一事务。散客、未知买方不建。
- **物料**：销售不建物料（`entry.py:244-269`）。
- **预检**：`desk/sales-form.js:176`。
- **作废**：`cancel` 只在 ERP（`entry.py:327-339`），不排 Express 作废。
- **打印**：销售单标题/Express 单号按**打印时** linked（`erp/printing/context.py:277-288`）：接上前的单接上后打印 → 标题变成"送货单"、Express 单号空；写进去后断开再打印 → 变回非税收据、不印 Express 单号。测试：`tests/test_erp_printing.py::test_titles_never_claim_a_tax_invoice`（只测 `contracts.sales_title`，不测 context 取 linked）。
- 测试：同采购的 Prepare / LinkParty 类；`direct::test_a_customer_is_found_by_the_buyer_tax_id_before_the_misread_name` 等 CompanyMasters 类。queue/linked：无测试。

| 状态 | 写不写 | 停在哪 | 页面 | 新建客户 |
|---|---|---|---|---|
| ① / ② | 不写 | — | 不预检 | None |
| ③ | 提交时排 | 同采购 | 预检会问 | 本公司 |
| ④a 草稿接上后提交 | 写 | 同③ | 同上 | 本公司 |
| ④b / ④c | 不写，不补 | — | 打印按当时 linked | None |
| ④d | 照写 / 停 NO_BOOK | — | 断开后隐藏 | — |

### 2.5 收银日汇总 HS / 退货 SR（`erp/express/pos_day.py`）

- **谁被汇总**：每天 01:20 `nightly` 只对 `masters.linked_companies()`（启用、13 位税号、linked）（`pos_day.py:154-166`；`masters.py:66-69`）。HS 收：完成的销售小票 + 同日卖同日退且原票未汇总的退货，且 **`DATE(sold_at) >= 公司.express_pos_from`**；`express_pos_from` 为空的公司一张都不汇总（`pos_day.py:45-57`）。SR 收：原小票已进某张 HS、自己还没汇总的退货，**不看起始日**（`pos_day.py:58-60,81-93`）。
- **起始日 `express_pos_from` 怎么来**：主档同步第一次落库（`masters.apply`）时若为空就设成 `nowdate()`（`masters.py:410-412`）；补丁 `v1_14_pos_day_start` 给已有 `express_book` 的公司设成上线当天。**之后永不清空、不随断开 / 重新接上更新**（全仓只有这两处写它）。
- **判定点**：建 HS/SR 后 `push.queue`（`pos_day.py:121`），此刻再判一次 linked。
- **退货等原单**：SR 的原 HS 没写进去 → `held/express.pos_refund_waiting`（重试）；原 HS 是人手工处理 → `express.pos_refund_original_manual`（`pos_day.py:176-185`）。
- **作废小票**：已进汇总的不许作废，走退货（`erp/cashier/sales.py:497-499`）。
- **建单人**：系统建的汇总归能写这份账套的员工（`push.py:530-543`）。
- 测试：PosDaySend、PosDayExits、PosDayActor、PosDaySummaries 类（如 `direct::test_a_receipt_is_in_at_most_one_summary_and_late_receipts_only_go_into_the_follow_up`、`::test_a_refund_after_the_day_summary_becomes_a_credit_note_against_that_hs`）。`nightly`、`pending_days` 的起始日过滤、`express_pos_from` 的设定：**无测试**（`direct` 里只在夹具出现一次 `express_pos_from`，`:552`）。

| 状态 | 汇总 / 写 | 停在哪 | 页面 |
|---|---|---|---|
| ① / ② | 不汇总、不写；小票留着 `express_summary` 空 | — | 收银小票列表 Express 列按 boot 隐藏（`desk/pos-receipts.js:136`） |
| ③ | 每晚汇总昨天及以前、日期 ≥ 起始日的小票，排写 | 负数：`held/express.pos_net_negative`；不知是否 VAT 登记户：`held/choice_needed:vat_registered`；收款科目/退货科目等：`choice_needed:*` | 汇总单上问；可「再写一次」「已在 Express 手工处理」 |
| ④a 小票在接上前卖 | 接上前日期的不汇总（起始日 = 首次同步落库那**一天**，当天接上前卖的小票也算进去）；原票在起始日前的退货不写 | — | — |
| ④b / ④c 接上后断开 / CC 不报 | 断开期间每晚跳过这家公司；小票攒着 | — | — |
| ④b′ 断开后**重新接上** | 起始日还是第一次的那天 → 断开期间的小票在下一晚起按天补汇总写进 Express（每晚最多 62 天）；断开期间的跨天退货也会补成 SR | 同③ | — |
| ④d 汇总单已排队后切换 | 照写 / 停 NO_BOOK | — | 断开后隐藏 |

### 2.6 期初（`erp/inventory/opening_batch.py`）

- `_context`：`linked(当前公司)` → 抛 `PermissionError("Opening stock is read from Express")`（`opening_batch.py:63-87`，`:74`）；预览、提交（`_submit` 里两次 `_context`，`:326-337`）、旧接口 `upsert_one`、模板下载都走它。
- 接上前录的期初：流水 `ref_type=opening` 在库存对齐里"不算"（`masters.py:702,733`）→ 第一次同步时被 Express 各仓结存覆盖。
- 断开后：`linked` 为假，又能录期初。
- 前端：`desk/inventory.js:23`、`desk/products.js:225` 按 `p.express_company()` 隐藏期初/入库入口。
- 测试：`tests/test_opening_batch.py::test_a_company_linked_to_express_takes_its_opening_from_express`。

| ① / ② | ③ | ④ |
|---|---|---|
| 允许录 | 预览/提交都拒 | 判在每次预览和提交；接上前录的被同步覆盖 |

---


## 3. 其他用到 linked / connected / express_enabled 的地方

| 位置 | 用哪个判据、何时 | 影响 | 测试 |
|---|---|---|---|
| `erp/inventory/warehouses.py:234-237` `create_warehouse` | linked，建仓时 | 接上的公司不能在 ERP 新建仓（`EXPRESS_FIRST`），仓由主档同步来 | `tests/test_erp_locations.py::test_a_company_linked_to_express_takes_its_warehouses_from_express` |
| `erp/masters/company.py:84-95` `ensure_warehouse` | `express_enabled` **或** connected | 两者有一个就不自动建默认仓 | `tests/test_erp_default_company_warehouse.py::test_express_companies_and_companies_with_warehouses_are_left_alone` |
| `erp/masters/company.py:146-151` `company_options` | linked（只算当前公司） | 启动数据 `express_linked` → 前端 `p.express_company()`（`desk/transactions.js:102`）隐藏建仓（`desk/warehouses.js:24,198`）、期初/入库入口（`desk/inventory.js:23`、`desk/products.js:225`） | `tests/test_erp_boot_items.py` 间接 |
| `erp/masters/company.py:394-401` 公司页 | connected / linked | `express_connected` 决定勾选框只读与「断开」；`can_sync_express = team.manage 且 linked` 才有「同步 Express」与同步状态区 | 回归流程 `scripts/flows/erp_company_settings.py` |
| `erp/masters/company.py:643-650` `update_company` | connected | 连着时取消勾选被拒 | 无单元测试 |
| `erp/masters/company.py:515-522` `disconnect_express` | 不判 | 只置 `express_enabled=0`，不撤排队件、不清单据状态、不清 `express_pos_from` | 无测试 |
| `erp/masters/boot.py:21-30` | linked（当前公司） | `frappe.boot.pearnly_express`：预检、Express 列/按钮总开关 | `tests/test_erp_boot_items.py` 2 个 |
| `erp/printing/context.py:277-288` | linked，**打印时** | 销售单标题（送货单 / 非税收据）与是否印 Express 单号 | 无（只测标题合同） |
| `erp/express/api.py:177` `preflight` | linked，提交前 | 没接 → 不问 | 无单元测试 |
| `erp/express/api.py:211-280` `answer` / `retry` / `mark_handled` | **不判** | 断开后接口仍可用（页面按钮已隐藏），调用会 `push.send` 再写 | 无 |
| `erp/express/masters.py:66-69` `linked_companies` | linked | `nightly`、`sync_all`（每小时）、`check_marks`（每 10 分钟）只轮到接上的公司 | `direct::test_the_hourly_sync_only_takes_old_computers_and_stale_companies`、`::test_only_a_changed_mark_or_a_new_book_starts_a_full_sync`（mock linked_companies） |
| `erp/express/masters.py:97-138` `advance`、`:203-213` `start` | **不判** | 已在排队/在读的同步（例如写单回执后 `masters.queue`，`push.py:408`）断开后照样落库、照样按 Express 对齐库存 | `direct::test_automatic_syncs_start_a_few_at_a_time_and_land_what_was_read` 等（不涉及 linked） |
| `erp/express/masters.py:705-785` `_pending`（库存对齐） | 按单据 `express_state` | 见 4.a 反证 3 | `direct::test_pending_counts_every_erp_move_not_yet_in_express` |
| `erp/express/companies.py:71` | 建公司时 `express_enabled=1` | CC 报新税号就建成"已勾" | 见 0 节 |
| `erp/patches/v1_17_company_settings.py:16` | connected / express_book | 一次性补勾 | 无 |

---


## 4. 两条口径核实

### a) "勾选 / 连上之前做的单据，接上后不补推，从接上那一刻起算"

**大体成立的部分（证据）**
1. 没有任何"接上时扫旧单"的逻辑：hooks 没有 Express 的 doc_events；定时任务里 `push.poll` 只看 `express_state ∈ (queued, held)` 的单（`push.py:582`），接上前的单 `express_state` 为空，永远轮不到（`hooks.py:347-359,558-574`）。
2. 「同步 Express」按钮（`can_sync_express` → `api.sync_company` → `masters.start`）只读主档、落主档与库存，不排任何单据写账（`api.py:27-32`；`masters.py:203-213,388-416`）。
3. 采购/销售/出入库的单只在"可以写了"那一刻判一次；接上前已提交的单之后再付款、打印、作废都不再 `queue`。
4. 出入库单接上前做的，接上后作废：`express_state` 空 → 只在 ERP 作废（`push.py:351-352`），测试 `direct::test_an_entry_never_written_is_only_voided_in_erp`。
5. 收银：起始日前的小票不进 HS，起始日前原票的退货不写（`pos_day.py:45-60`）。

**反证 / 例外**
1. **收银重新接上会补推**：`express_pos_from` 只在第一次落库时设、永不更新（`masters.py:410-412`）。断开（或 CC 不报账套）期间每晚跳过，重新接上后从原起始日起所有未汇总的小票按天补写（每晚最多 62 天，`pos_day.py:31,65-70`）。"从接上那一刻"实际是"从**第一次**主档同步落库那**一天**（整天）起"。当天接上前卖的小票也会进 HS。
2. **采购「修改」接上前的旧单**：作废原单 + 提交新单，新单按提交时 linked 推进 Express（`purchasing/entry.py:158,206`；菜单不看 Express 状态 `desk/purchase-form.js:255`）。同样，已写进 Express 的采购单「修改」后会再推一张新单，Express 里的原单不作废（采购/销售没有 Express 作废）。
3. **库存对齐对旧单口径不一致**（`masters.py:705-785`，测试明确断言了出入库单那一条）：
   - 接上前的出入库单、盘点生成单、收银小票（`ref_type` stock_entry / count / sale / refund）没写进 Express，被当成"还没进 Express 的 ERP 变动"**永久**叠加在 Express 结存上（`direct::test_pending_counts_every_erp_move_not_yet_in_express` 里 "B 段之前开的、没写成的：算"）；
   - 接上前的采购/销售原始流水只在 `express_state ∈ (queued, held)` 时算，旧单（state 空）**不算**，被 Express 覆盖；
   - 期初不算，被覆盖；
   - 作废冲销流水（`ref_type=void`）一律算（只有出入库单在 Express 作废完的除外）→ 接上前的采购/销售接上后（或接上前）作废，原单不算、冲销算，ERP 比 Express 少这批数量（推论，测试只覆盖"写进去之后作废"那一种）。
   结论：单据不补推，但旧单对 ERP 结存的影响因单据类型而异，不是统一"从接上那一刻起算"。
4. **断开不停已排队的单**：`disconnect_express` 只改勾选；`send` / `poll` / `answer` / `retry` 不复核 `express_enabled`。断开时排队中 / 停在可重试原因的单，只要 CC 还报着账套，下一分钟照写；已交进写账队列的件照样被电脑领走。断开后页面隐藏 Express 列与按钮，用户看不到这些单（`push.py:267-328,578-593`；`desk/transactions.js:149-150`）。
5. **CC 短暂不报账套正好碰上提交**：那一刻 linked 为假 → 单据永远不写，CC 回来也不补（与"接上前"同待遇）。电脑离线不影响（账套行仍在）。
6. 已写进的出入库单断开后再作废，`push.void` 不判 linked，照样排 Express 作废件（`push.py:334-362`）。

### b) "跨越切换的单据按单据开始时的状态定（盘点按开盘点那一刻）"

**现在每个入口判 linked 的时刻**

| 入口 | 判"写不写"的时刻 | 判主档归属的时刻 | 后续是否复核 |
|---|---|---|---|
| 盘点 | 过账 `post_count`（`count.py:904`） | 新建物料：建物料那一刻（`count.py:448`） | 否 |
| 出入库 / 调拨 | 提交（`documents.py:346`；草稿 `submit_entry`） | — | 否 |
| 采购 | 提交（`purchasing/entry.py:143`；`service.py:328`） | 供应商：提交；物料：从不挂 | 否 |
| 销售 | 提交（`sales/entry.py:167`） | 客户：提交 | 否 |
| 收银 HS/SR | 每晚建汇总时（`nightly` + `queue`） | — | 否；小票按 `express_pos_from` 日期 |
| 期初 | 每次预览 / 提交 | — | — |
| 打印 | 每次打印 | — | — |
| 预检 / 页面开关 | 页面加载（boot）+ 预检请求时 | — | — |
| 排队后的 send / poll | **不判**（只看账套在不在） | — | — |

**单据上没有任何记录"开始时状态"的字段**：五种单据的 express 字段只有 `express_state / express_docnum / express_reason / express_sent_at / express_job / express_book / express_needed`（出入库另有作废三栏），初值都空（各 DocType json）；`ERP Stock Count` 建单时不写任何 express 字段。`express_company` 只在物料/客户/供应商主档上。

**结论**：现在是"按单据**可以写那一刻**（提交 / 过账）定写不写，一旦排上就写到底"，不是按开始时。盘点不按开盘点那一刻。唯一接近"开始时"的是收银小票按日期 ≥ 起始日，但起始日不随重连更新（见 a 反证 1）。

**特别核实：盘点开始没接、中途建物料、过账时已接**
- 物料 `express_company=None`（本地档），过账时排 TK；物料没 Express 编码 → 存货表没读到停 `express.item_unmapped`，读到了问一次「物料」（同名存货不问）→ 选新建 → `new_ok` 写进 Express → 回执后 `link_products` 认领（这件物料没在别家公司的仓里动过库存才认）（`push.py:461-492`）。这一路是通的（`direct::test_stock_documents_count_and_pos_day_link_the_codes_the_computer_used` 覆盖认领那一步）。
- 反过来"盘点时已接、建的物料挂了本公司"：原来 `link_products` 跳过 `express_company` 已有值的物料（`push.py:483`），电脑建好的 Express 编码不回填，之后主档同步在多公司租户会按 Express 编码另建一条同名物料、ERP 结存留在旧档上（`masters.py:525-543,619-668`）。**11-01 已修**（见 2.1），`count.py` 文档串（原写「盘点调整停下交人」，是 P3x 之前的行为）同时改正。

---

## 5. 建议改法（改动前报主会话，未施工）

### 5.1 一个判据：公司的「接上时刻」，单据按开始时刻比

不在每张单上加「开始时接没接」字段（初稿的做法）：那样每种单据都要加字段、补历史，还解决不了「接上前的旧单在库存对齐里怎么算」。
改为在公司上记一个时刻，所有入口同一条判断：

- `ERP Company.express_linked_at`（Datetime，只读）：这家公司「接上那一刻」。勾选且第一次主档同步落库（`masters.apply`，即现在设
  `express_pos_from` 的同一处）时写入当前时刻；**断开 / 取消勾选时清空**；重新接上时重新写入。`express_pos_from` 改为由它派生（取日期），
  不再单独维护——这就消掉了 4.a 反证 1（重新接上补推断开期间的小票）。
- 单据的「开始时刻」：盘点 = 开盘点（`ERP Stock Count.creation`）；出入库 / 调拨、采购、销售 = 建单（含草稿，`creation`）；
  采购「修改」出的新单 = 原单的开始时刻（`amended_from` 链上最早那张）；收银小票 = 卖出时刻（`sold_at`，现在已按日期这样做）。
- `push._eligible`：`express_enabled` 且 `express_linked_at` 有值 且 单据开始时刻 ≥ `express_linked_at` → 排写账；**不再要求此刻 connected**。
  提交时 CC 正好没报账套的单照排，停在 `express.account_set_missing` 每分钟重试，CC 回来就写（现在是永久漏写，4.a 反证 5）。
- 随单 / 盘点里建的主档归属（`count.create_product_here`、`purchasing/entry._supplier`、`sales/entry._buyer`）同一判断：
  单据开始时刻 ≥ `express_linked_at` 才挂本公司。

### 5.2 断开优先于开始时状态

「开始已接、过账前断开」按开始时状态本该写，但断开是人明确说「不再推」：建议断开立即停。
- `disconnect_express` / 取消勾选：清 `express_linked_at`；这家公司 `express_state ∈ (queued, held)` 的单置回空并撤掉还没被电脑领走的写账件
  （`jobs.cancel`，同出入库作废撤排队件的现有做法）；已被领走、正在写的不撤，结果照常回来。
- `send` / `poll` / `answer` / `retry` 开头复核 `express_enabled`（现在都不看，4.a 反证 4）。
- 已写进 Express 的单断开后再作废：现在照排 Express 作废件（`push.void` 不判）。建议保留——那张单确实在 Express 里，作废要同步过去。

### 5.3 库存对齐与页面按同一判据

- `masters._pending`（库存对齐时叠加「还没进 Express 的 ERP 变动」）：只叠加开始时刻 ≥ `express_linked_at`、还没写成的单据的流水；
  开始时刻更早的一律不叠加（Express 结存覆盖，会计自己在 Express 里处理过或不处理）。统一现在的三种口径（4.a 反证 3）。
- 打印（`printing/context._sales`）：标题与 Express 单号按这张单有没有 `express_docnum`，不按打印时 `linked`。
- 单据上的 Express 按钮 / 列表 Express 列：按单据自己的 `express_state`，不按当前公司此刻 `linked`；断开后停下的单也看得到
  （5.2 之后断开时停下的单已清空，剩下的是已写成 / 已作废的）。
- 期初、建仓：仍按此刻 `linked`（这两项是「接上了就以 Express 为准」的规则，不是单据跨切换的问题）。

### 5.4 要主会话定的点

1. 用公司「接上时刻」+ 单据开始时刻（5.1），代替逐单记「开始时状态」。建议：同意。
2. 断开优先：断开时撤掉这家公司排队中 / 停下的单（5.2）。建议：同意。反方案是按开始时状态照写，断开只管之后开始的单。
3. 采购「修改」接上前的旧单：新单继承原单的开始时刻，不推（5.1）。建议：同意（原单会计可能已手录进 Express，推新单会重复）。
4. 库存对齐统一为「开始时刻早于接上时刻的单不叠加」（5.3）。建议：同意。影响：现在接上前做过出入库 / 盘点、还没手录进 Express 的公司，
   下一次同步后 ERP 结存会回到 Express 的数（现在是 Express + 这些旧单）。

### 5.5 影响与迁移

- 新字段 `express_linked_at` 随 DocType json 原生迁移；补丁给现在已接的公司填 `express_pos_from` 当天 00:00（没有就填补丁运行时刻）。
  这样已写过的单、正在排队的单开始时刻都晚于它，行为不变；补丁前已存在、从没写过的旧单维持不推。
- 改到的共用件：`push._eligible` / `queue` / `send` / `poll`（全部 Express 写账入口）、`masters._pending`（库存对齐）、
  `count.create_product_here`、采购 / 销售随单建档、`pos_day`、`printing.context`、几个 Desk 页面的 Express 按钮条件。
  按共用件连带回归闸门：`erp.cc.*`、`erp.document.express`、`erp.stocktake.*`、`erp.express.*`、收银汇总相关流程重跑。
- 测试（离线，`tests/test_erp_express_direct.py` 新增一组「接 Express 状态」）：对 1 节总表每一格各一条——① / ② 不排；③ 排；
  ④a 开始早于接上不排、开始晚于接上排；④b 开始已接、断开后不排且排队的被撤；④c 提交时 CC 不报账套照排、停 NO_BOOK；
  盘点新建物料按开盘点时刻归属；`express_linked_at` 写入 / 清空 / 重新写入；收银起始日跟随；`_pending` 只叠加接上后开始的单；
  采购修改继承开始时刻；打印按 `express_docnum`。

## 6. 现有测试清单（覆盖这些路径的）

单元测试（`tests/`）
- `tests/test_erp_express_direct.py`（2170 行，主力）：Prepare 类（准备、停下原因、两份账套选一次）、MasterSync 类（同步调度、`linked_companies` 被 mock）、AutoCompanies 类（按账套建公司）、StockVoid 类（出入库作废）、PosDaySend / PosDayExits / PosDayActor / PosDaySummaries 类（收银汇总与写账）、UnmappedWarehouse 类、LinkParty / LinkProducts 类（回填编码；只测 `express_company` 为空的物料）、StockAlignment 类（`_pending` 口径）、StockEntries 类（OU/JU/TK/RL 单别、准备、盘点轧差、新物料问一次、预检 form_body）。
- `tests/test_erp_boot_items.py`：启动数据 `pearnly_express` 跟当前公司、出错默认未接。
- `tests/test_erp_locations.py::test_a_company_linked_to_express_takes_its_warehouses_from_express`：接上不能建仓。
- `tests/test_opening_batch.py::test_a_company_linked_to_express_takes_its_opening_from_express`：接上不能录期初。
- `tests/test_erp_default_company_warehouse.py::test_express_companies_and_companies_with_warehouses_are_left_alone`：勾了或连上不自动建仓。
- `tests/test_erp_printing.py::test_titles_never_claim_a_tax_invoice`：只测标题合同。
- `tests/test_erp_express_locks.py`、`tests/test_erp_codes_per_company.py`、`tests/test_erp_print_security.py`：主档按 `express_company` 隔离（与接不接无关的部分）。

回归流程（`scripts/flows/`，在实验栈跑，非单元测试）
- `erp_express_new_item.py`：new（已接）出入库预检问物料、载荷 15、回填编码；old（未接）不问。
- `erp_company_settings.py`：公司页「接 Express」三种状态（勾选只读、断开、下载）。
- `erp_document_express.py`、`erp_express_service_vat.py`：单据 Express 弹窗与服务税。
- `erp_purchase_create.py`、`erp_stock_transfer.py`：跑时临时把 `express_enabled` 置 0（不测接 Express 行为）。
- `scripts/erp-express-linked-lab-fixture.py`：实验站把公司标成已接（`linked` 由假变真、建仓被拒、同步落仓库）。

**无测试的关键路径**：`push.connected/linked/_eligible/queue`；`send`/`poll` 不复核 linked；`disconnect_express`；`update_company` 的 `DISCONNECT_FIRST`（只有页面流程）；`count.create_product_here` 的 `express_company`；`post_count` 调 queue；`pos_day.nightly`、`pending_days` 起始日过滤、`express_pos_from` 的设定与重连；`api.preflight` 的 linked 门；`printing.context` 按打印时 linked；采购「修改」与 Express；`answer`/`retry` 在断开后的行为。


## 7. 未核实

- 共用写账队列（`core/cc_desktop/jobs`）里已排的件在账套被删 / 公司断开后会不会被电脑领走、领走后怎么报：未读。
- `warehouses.resolve` 不带公司时是否限定当前公司（影响出入库预检用当前公司是否总与仓库公司一致）：未读。
- 实际站点上「断开」后排队单是否真被写入：只按代码推论，未实测。
- 盘点里按接上状态新建的物料写进 Express 后的主档同步结果（重复建档）：推论，未实测。
