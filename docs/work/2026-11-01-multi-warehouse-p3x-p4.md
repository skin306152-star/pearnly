# 多仓闭环 P3x（Express 端新商品）+ P4（报表跨仓合计）：施工记录

方案与口径：[评估](2026-11-01-multi-warehouse-p3x-p4-评估.md)（主会话 11-01 审定，四问按建议）。
分支 `pearnly/multi-wh-p3x-p4`，基线 origin/main `5d67b418`。顺序 A（服务端）→ B（CC）→ C（P4）。

## A 服务端

- `express/stock_entry.py`：
  - 明细行带 ERP 商品（`product`，只在服务端用，进载荷前去掉）。
  - `prepare` 里新增 `_item_choices`：
    - 行上没有 Express 编码时，这份账套有同名存货（`norm_name`）就交品名。
    - 选过的照选的走：选现有存货就带那个码，选新建就标 `new_ok`。
    - 其余的按 ERP 商品问一次（`item`，主题 `stock|<ERP 商品>`），同一件商品只问一次，和科目一起问。
    - 出库 OU、入库 JU、盘点 TK、调拨 RL 同一套。
  - 有 `new_ok` 的行时载荷盖 `NEW_ITEM_PAYLOAD_VERSION = 15`。
  - 存货表没读到（主档里没有 `products`）时不问：盘点有新商品的照旧停在 `item_unmapped`，出入库照旧交品名。
  - `form_body` 带上明细商品，提交前就能问选存货。
- `express/push.py`：
  - `send` 去掉盘点新商品的提前停下，交给 `prepare` 判断。
  - `link_products` 扩到出入库单、盘点（按交给电脑的明细序号）、收银日汇总（按品名）。这三种单不按「只被这张单用过」判断，改为「没在别家公司的仓里动过库存」（`_moved_elsewhere`）：收银卖的本地商品必然被别的小票用过，原规则永远认领不了。
  - 采购、销售的回填规则不变。
- 用例：`tests/test_erp_express_direct.py` 新增 5 个（问一次、新建盖 15、选现有、同名不问、记住的码失效再问、存货表没读到、提交前问、三种单回填与别家公司不挂），原有 4 处断言补 `product`；全文件 119 个通过。

## B CC 桌面版

- `dbf_stock` / `dbf_transfer`：认码时认不出、且行上 `new_ok is True`、没有码的行先留空，写前备份里 `create_new_items` 用 `product_master.ensure_stock_item` 建 0 余额库存品（同采购 / 销售），按建出的码认回来再写。
  - 同一张单同名两行只建一个。
  - 调拨要建档时 STMAS 也进写前备份。
  - 失败整单连新档一起回滚。
- 两个模块原来就逐行回报 `line_modes`（序号 / 品名 / 存货码），服务端据此回填，不用改。
- `payload_contract` 加 15，版本 1.12.10。原来把 15 当「未来版本」的三处测试改成 16。
- 用例：`test_dbf_stock` 2 个（盘点两行同名建一个、非 true 不建、读回失败连新档回滚）、`test_dbf_transfer` 1 个、契约 `test_the_new_item_version_and_key_match`。
- 本机暂存目录建了只装 pytest / dbf / aenum / httpx 的环境，CC 全部测试 1253 通过、42 跳过（界面类缺 PySide6）。
- **口径待主会话再定**（11-01 已问）：`ensure_stock_item` 建的档是 `NEGALLOW = Y`，`stock_guard` 又跳过 Express 里没有的商品。所以「同销售」意味着减少方向的新商品在不许负库存的账套里也会照写成负数，评估第 12 节第 1 问「由 stock_guard 停下」不成立。

## C 报表

- 库存一览（`inventory/stock.py`、`inventory.js`）：
  - 加原生下拉「显示方式：按位置 / 按物料」。按物料时没有仓库 / 仓位两列和两项筛选，也不能勾选。
  - 点按物料的一行：换回按位置，并加上这件物料的筛选（新增「物料」筛选项）。
  - 合计与状态：`contracts.product_rows` 把按位置的行合成按物料；`contracts.stock_status` 定状态：这一行没货是缺货，有货时低库存按这件物料的公司合计比（`_company_totals`：当前公司各仓按物料分组求和，不跟页面的仓库 / 仓位筛选）。
  - 标题旁的低库存 / 缺货数改为按物料数，与按物料的看法一致。
  - 合计视图仍是现有的「服务端取全量、内存切页」那套，**不是评估写的 SQL 分组分页**：这样不新增查询，无上限基线不变（库存一览本来就在基线里）。
- 导出：跟随显示方式与全部筛选。顺带修了原来**漏传仓位与数量条件**的问题（导出比列表多）。
- 收发存（`stock-card.js`）：原生筛选加「仓位」，服务端 `summary_page` 本来就收 `location`。
- 新词条 3 条：显示方式 / 按位置 / 按物料（中文用「物料」，与本页现有用词一致）。
- 用例：`tests/test_erp_printing.py` 加 1 个（状态判定、按物料合计、成本未确立 / 无权限留空、公司合计为 0 时缺货）。

## 验证

## 合入前
