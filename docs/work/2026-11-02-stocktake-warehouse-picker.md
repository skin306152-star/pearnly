# 盘点录入「实际仓位」弹「未找到仓库」（11-02 三星真机）

分支 `pearnly/stocktake-warehouse-picker`（基线 `origin/main` `ee77289e`）。

## 现象与根因

- 现象：盘点录入屏认出物料后点「实际仓位」，弹「未找到仓库 / 您正在查找的资源不可用」，下拉里没有仓位。
  11-02 在生产 testerp 的 LINE 富菜单「盘点」→ QA1101 → 「沐浴露」→ 录入盘点，删掉仓位格里的字就弹（只看，没保存）。
- 根因：`stocktake-entry.js` 的 `choose_source` 把「实际仓库」（官方 Link → `ERP Warehouse`，值必须是仓库编号 `WH-…`）
  写成了**仓库名称**（`entry.warehouse_name` / `source.warehouse_name` / `lookup.warehouse_name`）。实际仓位的选项按这一格的值
  去 `inventory.locations.api_location_options` 取，服务端 `warehouses.company_of(名称)` 查不到 → `DoesNotExistError`。
  每一件都这样（不只第二件起）；保存不受影响，因为 `warehouses.required` 名称也认。这行从 `e510bf36`（盘点工作台最早一版）就在。
- 实验栈复现：新流程 `erp.stocktake.places` 在没修的代码上两件都看到「实际仓库」= `คลัง 1`、取仓位回 `DoesNotExistError`。

## 修法

- `inventory/count.py` `lookup_count` 多回一项 `warehouse`（这份盘点的仓库编号），只加字段，原有字段不变。
- `stocktake-entry.js` 三处都改取编号：`entry.warehouse` → `source.warehouse` → `lookup.warehouse`（显示名由 Link 按标题取）。

## 同类排查

| 位置 | 结论 |
|---|---|
| 入库 / 出库 / 调拨（`stock-entry.js`、`transactions.js` 的 `p.place` 与 `lines_editor`） | 单头仓库来自 `warehouses.prefill`（编号）或已存单据的编号；明细行仓位按单头 / 本行仓取，值都是编号，没问题 |
| 采购 / 销售明细（`purchase-fields.js`、`sales-fields.js`） | 行上仓库同上，来自单头编号，没问题 |
| 只读明细表（`transactions.js` `lines()` 把仓库换成名称） | 只给只读查看用（两个调用方都是 `readonly`），不取仓位选项，没问题 |
| 期初表（`stock-opening.js`） | 表头仓库是编号；导入表的仓库列经服务端预览认成编号后再写进行（`resolved.warehouse`） |
| LINE 盘点页（`www/stocktake.html`、`line_mobile.py`） | 仓库下拉值是编号（`warehouses` 的 `value`）、换仓取仓位按编号，没问题 |
| 盘点工作台（`stocktake.js`）新建盘点的仓库、记录弹窗 | 用户自己选（编号）/ 只显示名称，没问题 |

## 验证

- 流程：新加 `erp.stocktake.places`（`scripts/flows/erp_stocktake_places.py`）：在一个有仓位清单的仓上开盘点，连续盘两件，每件认出后
  「实际仓库」是仓库编号、点实际仓位不弹报错、下拉列出清单上的仓位，选仓位保存后库里两条都记在这个仓这个仓位。
  `erp.stocktake.scan` 每件认出后多断言「实际仓库」是盘点仓的编号（走账面行那条）。
- 单测：`tests/test-stocktake-entry-scan.cjs` 原来断言「实际仓库」= 仓库名称（把这个缺陷写成了预期），改成服务端同时给编号与名称、断言格里放编号。
- 三星 SM-A146P 真机：
  - 修前，生产 testerp（LINE ERP-TEST 富菜单「盘点」→ QA1101 → 沐浴露 → 录入盘点）删掉仓位格的字即弹「未找到仓库」（只看，没保存）。
  - 修后，本机实验栈 `pearnly-wh-lab`（手机 Chrome，真触摸）连续盘两件（第一件走盘点仓预填，第二件沿用上一件仓位）：实际仓位下拉都列出 FLOW-A1 / FLOW-B2、
    没有报错；保存后库里 `WH-00002 / FLOW-B2 / 5`、`WH-00002 / FLOW-A1 / 6`。
  - 截图在共享笔记 `盘点实际仓位-真机截图-11-02/`。从 testerp 的 LINE 富菜单验修后版本要等部署（本次不部署）。
- 本地闸门（照 development.yml）160 步：第一遍只有上面那条单测不过，改后四个盘点前端单测全过；分支 Development checks 通过（`913ed37f`）。
- 流程：没修的代码上 `erp.stocktake.places` 不通过（复现）；修后单跑 4/4 过；`--changed` 7 条（盘点扫码、就地新建、网址码、仓位、
  手机 LINE 盘点、按台 Excel、GS1）25/25 过，`pearnly/flows` 已回写在 `913ed37f`。

## 回写

- 实现、单测、流程、实验栈真机：完成。生产（testerp）上的修后真机：待部署后从 LINE 富菜单「盘点」再点一次实际仓位。
