# ERP 仓位编码（11-07）

- 分支 `pearnly/location-code-1107`，基线 origin/main `2774af6a9`。需求：用户经主会话 11-07「仓位加编码栏」。
- 评估：[2026-11-07-location-code-评估.md](2026-11-07-location-code-评估.md)，主会话 11-07 审定，第 12 节四条全按建议：
  ①下拉显示「编码 · 名称 · 说明」（有说明才带）②只读显示这次不带编码 ③物料「默认仓位」不动 ④库存导出、盘点导出加一列，收发存不加。

## 改了什么

| 处 | 改动 |
|---|---|
| 子表 `ERP Warehouse Location` | 加 Data 字段 `location_code`（标签「仓位编码」，长 30，可空）；DocType JSON 随源码，`bench migrate` 同步表结构，旧行为空，不需要补丁 |
| 唯一 | 同一仓库里不能重（不分大小写，空编码不算）：`locations.api_save_locations` 与 `ERP Warehouse` 控制器 validate 都核（任何保存路径都拦），报「仓位编码重复: X」；别的仓同编码可以 |
| 选仓位的下拉（`locations.api_location_options`，所有选仓位的地方共用：采购 / 销售行、出入库 / 调拨 / 物料调整行、期初、盘点录入，网页与 LINE LIFF 同一页） | 按编码或名称模糊搜（服务端，仍最多 50 个）；选项「编码 · 名称 · 说明」，没有的段不写；**值仍是仓位名称**，单据、流水、结存存法不变 |
| 仓库页仓位清单（`warehouses.js`） | 表格多一列「仓位编码」，保存时一起交 |
| LINE 识别后问仓位（`integrations/line_items.py`） | 按钮「编码 · 名称」（照旧截 20 字），点下去记名称；打字回复编码也认 |
| 导入（期初 `opening_batch`、盘点 Excel `count._upsert_import_entry`） | 仓位格写编码也认（`positions.resolve`：先按本仓清单的编码认，认到换成名称；与某仓位名称相同时按名称） |
| 导出（库存导出、盘点导出汇总表） | 「仓位」后面加「仓位编码」一列（`positions.with_codes`）；收发存不加；盘点表再导入时认不出的列照旧忽略 |
| 打印 | 不涉及（7 个打印格式都不含仓位） |
| Express / CC 桌面版 / Cowork | 不涉及（Express 没有仓位；Cowork 盘点不用这张子表） |

## 文案（新增，没有改已有的）

| 键 / 英文 | 中文 | 泰文 | 日文 |
|---|---|---|---|
| `stock.location.code` / Location code | 仓位编码 | รหัสตำแหน่ง | ロケーションコード |
| Duplicate location code | 仓位编码重复 | รหัสตำแหน่งซ้ำ | ロケーションコードが重複しています |

`check-copy.py`：新增 2、改动 0、拦截 0。

## 检查

| 层 | 结果 |
|---|---|
| 单测 `tests/test_erp_locations.py` | 12 条通过：同仓重复编码被拒（不分大小写）、超长被拒、空编码多行可以、别的仓同编码可以、控制器同一判据；下拉「编码 · 名称 · 说明」、按编码 / 名称搜、值是名称；导入按编码认、名称优先；导出两处带编码列、列在「仓位」后面 |
| 流程（实验栈 pearnly-smallfix-lab，`run.py --changed --migrate`，按产品地图选 19 条 67 格，跑在 146a51ec3） | 67/67 通过：第一遍 66/67，`erp.stock-transfer [new / desktop]` 900 秒超时（没有断言失败，模拟架构下的慢格），`--rerun-failed` 补跑通过。其中 `erp.warehouses.codes` 新老 × 电脑手机 4 格：写编码保存、选项「编码 · 名称 · 说明」、按编码搜到、库里记下；`erp.stocktake.places`、`erp.exports`、`erp.products.opening`、`erp.stock.excel-units` 等用到选仓位、导出、导入的都过。之后的提交只有两条新词的翻译包与文档 |
| 生产镜像候选离线检查（`cand-local.sh offline`，镜像 1560bb95） | 102 组、LANE EXIT 0。第一遍抓到 `integrations/test_line_intake.py` 的替身没换新加的 `positions.codes`（离线时直接查库报错）：补了替身，并加一条用例：问仓位按钮「编码 · 名称」、点下去记名称、打字回复编码（不分大小写）认出仓位 |
| 真机 | [2026-11-07-device-location-code.md](2026-11-07-device-location-code.md)：LINE 问仓位按钮、LINE 盘点相机两行，上线后在 testerp 补 |

## 共用件连带回归

`check-impact` 命中的共用件入口按上面流程覆盖：页面类（`erp.page.*`、单据详情、扫码）随 67 格流程通过；
`erp.line.stocktake.liff-camera` 与 LINE 识别后问仓位只能真机，见真机记录，上线后在 testerp 补。
