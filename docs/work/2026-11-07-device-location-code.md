# 真机记录：仓位编码改到的两行 LINE 入口（2026-11-07，部分完成：上线后在 testerp 补真机）

分支 `pearnly/location-code-1107`，被测代码 146a51ec3（仓位编码，评估 [2026-11-07-location-code-评估.md](2026-11-07-location-code-评估.md)）。
本机实验栈 pearnly-smallfix-lab 没有 LINE 频道，这两行的真 LINE 部分合入前测不到，按 11-06 盘点相机那条的做法（dcd7ade50）先写清改了什么、
已证明什么，上线后在 testerp 用三星补测。

## `erp.line.postback.erp_item`（LINE 识别后按行追问 → 问仓位那一步）

- 这次改的：问仓位的按钮文字由「名称」改成「编码 · 名称」（仓位有编码时；按钮标签照旧截 20 字），点下去记的仍是仓位名称；
  打字回复时编码也认（`positions.resolve`），模糊搜按名称或编码。其余步骤（按行选、问仓库、取消）没动。
- 已证明（2026-11-07，146a51ec3）：`positions.label` / `positions.resolve` 单测（`tests/test_erp_locations.py`：编码换名称、不分大小写、
  名称与别的仓位编码撞了按名称、别的仓不认）；全闸门。
- 没测到：真 LINE 里按钮的样子与点选（要真官方号收图、付费识别）。
- 上线后补（testerp，三星 LINE，ERP-TEST）：先在 testerp 仓库页给一个有仓位清单的仓的两个仓位写编码 → 富菜单「ซื้อ」上传一张有物料行的票
  → 问到仓位时按钮显示「编码 · 名称」→ 点一个 → 卡片「编辑」打开的采购页那一行仓位是那个仓位的**名称**；再来一张，问仓位时打一个仓位编码回复
  → 认出那个仓位。测完把编码清掉（测试租户）。

## `erp.scan.line.stocktake.liff-camera`（LINE 富菜单「盘点」→ LIFF 相机）

- 为什么命中：改到盘点服务 `inventory/count.py`（盘点 Excel 导入的仓位格认编码）与 `inventory/positions.py`；扫码、相机、LIFF 的代码
  （`scan/reader.js`、`stocktake-entry.js`）没有改。录入屏的「实际仓位」下拉经共用的 `api_location_options`，选项改成「编码 · 名称 · 说明」。
- 已证明（2026-11-07，146a51ec3）：实验栈流程 `erp.stocktake.places`（连续盘点下拉列出这个仓的仓位、选了落库是名称）与 `erp.warehouses.codes`
  （选项显示「编码 · 名称 · 说明」、按编码搜到）——结果见任务记录。
- 没测到：LINE 原生相机授权与真机读码（这次没动这一段）。
- 上线后补（testerp，三星 LINE）：照 [11-06 记录](2026-11-06-device-erp-stocktake-liff-camera.md)「上线后补全计划」走一遍授权与读码，
  另核录入屏「实际仓位」下拉显示「编码 · 名称」、选了保存后盘点记录里是仓位名称。
