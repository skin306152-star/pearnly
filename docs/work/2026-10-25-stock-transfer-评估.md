# 需求影响评估：ERP 调拨单（仓间调拨，写进 Express 为 RL）

> 按「需求影响评估」模板填写，交主会话审定后施工。基线：B 段分支 `pearnly/stock-b3-pos` `1874e658`。`P/` = `apps/pearnly_erp_portal/pearnly_erp_portal/`，
> `CC/` = `companion/src/companion/`。依据：服务器测试账套第 6 条实录（共享笔记「服务器test账套-录单结果」）；主会话 10-25：S 盘 30 本账套在用 RL，属于标准 ERP 能力。

## 0. 需求原文与理解

- 来源：主会话审 B 段剩余设计（10-25）定：「ERP 调拨单：S 盘有 30 本账套在用 RL，属于标准 ERP 能力。按需求影响评估模板写一份评估交我；B 段先把 CC 写法备好」。
- 理解：仓管把一批货从一个仓搬到另一个仓（同一家公司），ERP 里开一张调拨单，两个仓的库存一出一入；接了 Express 的公司写成 Express 的调拨单 RL。
- 不在范围：跨公司调拨（不同税号 / 分店之间是买卖，不是调拨）；库位（仓内位置）之间的移动（车辆评估里的库位另议）；在途仓 / 两步调拨（发出、到货分开确认）。

## 1. 现有同类与复用

| 同类 | 位置 | 结论 |
| --- | --- | --- |
| 出入库单 | `ERP Stock Entry`（direction in / out、用途、明细子表、提交 / 作废、IR / IS 发号），页面 `P/public/desk/stock-entry.js` | **扩展**：加 direction `transfer` + 入库仓栏，复用单据、明细、发号、提交、作废、列表与权限 |
| 库存流水 | `P/inventory/stock.py apply_movement / apply_lines`（movement_type：stock_in / stock_out / adjust / count…） | 加一种 `transfer_out` / `transfer_in`（或复用 stock_out / stock_in 带 ref），一张单记一出一入 |
| 盘点里的位置移动 | `P/inventory/count.py` 里「Position transfers」只是同仓内位置的成本处理 | 不复用 |
| CC 写库存单 | `CC/dbf_stock.py`（OU / JU / TK，STTRN + STCRD + STLOC + 凭证） | **扩展**：加调拨（RL），不写凭证 |

## 2. Frappe 原生能力

- 单据仍是自有 DocType（ERPNext 不装），用原生字段（Link 到 ERP Warehouse）、原生子表、原生列表 / 筛选（FilterArea、服务端分页）、原生 Dialog / FieldGroup。
- 页面沿用出入库页的写法（`p.list` + `p.dialog` + 整页新建），不新做 UI 框架。

## 3. 按类通用

- 一类 = **库存单据**（入库、出库、调拨、盘点调整）。调拨与入库 / 出库共用同一 DocType、同一套提交 / 作废 / 附件（附件评估里的「库存单附图片」同时覆盖调拨）/ 打印（打印评估里库存单据的模板同风格加一份「调拨单」）。
- 不覆盖：采购 / 销售单（不是库存单据）。

## 4. 上下游数据

- ERP 单据流：提交 → 出库仓减、入库仓加（同数量、同成本：按出库仓移动均价出、入库仓照这个成本入），收发存两个仓各一行；作废 → 两行反向；同步对齐（`P/express/masters._pending`）把「已写进 Express 的调拨」挪进已写来源（同 B1 的做法）。
- Express 映射（实录 RL0000001）：STCRD 一对行——调出仓 POSOPR 4（PEOPLE = 调入仓）、调入仓 POSOPR 3（PEOPLE = 调出仓），同 DOCNUM / SEQNUM，MLOTNUM 指向来源批次，
  无金额，REIMBURSE = `\x00`；STTRN 一行（POSOPR 4、LOCCOD 调出、PEOPLE 调入）；STLOC 两仓各增减；STMAS 不变；ISRUN RL +1；**不写凭证**。
  打开调拨菜单 Express 先弹 2810、保存弹 43（这本 RL 没设日记账）——说明有的账套给 RL 配了日记账，施工前读 30 本在用账套的 ISRUN RL 行，确认配了日记账的账套 RL 写不写凭证。
- 负库存：调出仓按 `stock_guard` 同一套规则核（账套 / 商品级 NEGALLOW、CHKNEGLEV），调入不核。
- CC 载荷：库存单载荷加 `doctype = RL`、表头 `to_loccod`，归在第 7 版（与作废同一版，CC 1.13.0）；旧版诚实拒收。
- Cowork / DMS：不涉及（Cowork 没有调拨入口；共用写账规则 `express_rules` 不动，CC 直接写库存单）。

## 5. 隔离与权限

- 出库仓、入库仓必须属于同一家公司（服务端校验，前端只列这家公司的仓）；跨公司拒绝。
- 权限：开调拨要同时有 `stock.out`（出库仓）与 `stock.in`（入库仓）；查看按两个仓任一可见；服务端 `has_permission` / `permission_query_conditions` 扩展到入库仓字段。

## 6. 入口：网页、手机、LINE、拍照

- 网页 / 手机：库存菜单加「调拨」（与入库、出库并列），页面同出入库页；扫码选商品复用共用扫码（`erp.stock-in.item-scan` 同一套）。
- LINE：不做（现在 LINE 不做库存单）。拍照：随附件评估一起覆盖。

## 7. 共用件与连带回归

- 动到：`P/inventory/documents.py`（出入库单服务）、`P/inventory/stock.py`（流水）、`P/express/push.py` / `stock_entry.py`（写 Express）、`CC/dbf_stock.py`（cc-dbf-write）、扫码共用件（新调用方）。
- 新入口登记：`erp.page.stock-transfer`（页面冒烟）、`erp.cc.stock-transfer`（写 Express，`scripts/smoke/express_flow.py` 加 kind = stock_transfer）、`erp.stock-transfer.item-scan`。
- 回归：入库 / 出库 / 盘点页冒烟、收发存与库存一览、同步对齐、101 本 T 账套副本离线回归（写 RL 后 STLOC 两仓、STMAS 不变、其他表不动）。

## 8. 存储与成本

- 每张单几行数据，无文件、无外部调用、无新增月费。

## 9. 删除、作废、追溯

- 草稿可删，已提交只能作废（同出入库单）；作废 → ERP 两行反向流水；已写进 Express 的走 B 段 ① 的作废原单（RL 作废写法待实录，列待测）。

## 10. 文案与官方 UI

- 新增词条：「调拨」/ โอนย้ายระหว่างคลัง、「调出仓」「调入仓」，按官方风格新写；菜单项一条。
- 官方组件：Page、Dialog、FieldGroup、Grid、Link；无自定义样式。

## 11. 测试与验收

- 单元：同公司校验、权限（缺一边能力拒绝）、负库存按调出仓核、流水一出一入同成本、作废反向、载荷形状。
- 离线回归：101 本副本写 RL 再读回。
- 实验栈：ERP 开一张调拨 → 写 C 盘 T 账套 → Express 打开 RL 看两仓；页面冒烟。
- 验收复核；用户看手机上开调拨是否顺手。

## 12. 要问用户 / 客户的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 要不要「在途」（发出和到货分两步确认）？ | 不要，一步完成（Express RL 也是一步） | 两步要多一个状态与在途仓 |
| 2 | 调拨单要不要审批？ | 不要 | 审批要加流程 |
| 3 | 给 RL 配了日记账的账套，调拨要不要写凭证？ | 照那本账套的 Express 设置（施工前读 ISRUN 确认） | 凭证写不写 |

## 13. 工作量与排期

1. ERP：DocType 扩展 + 服务 + 流水 + 权限 + 页面：写代码约 3 小时，单测与闸门约 1 小时。
2. CC：RL 写法（与 B 段作废同版）+ 101 本副本回归：约 2 小时 + 机器 0.5 小时。
3. 实验栈端到端 + 页面冒烟：约 1 小时。
依赖：B 段 ① 作废合入后再做（共用作废与载荷第 7 版）；打印、附件评估里的库存单模板 / 附件区一起覆盖调拨。
