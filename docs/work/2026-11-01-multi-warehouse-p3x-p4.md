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

## C 报表

## 验证

## 合入前
