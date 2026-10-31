# 盘点 / 期初 Excel：内部编码一列与按台（岳父 10-25）

评估：岳父三项评估第三项（主会话 10-30 批准全部按建议施工）。分支 `pearnly/excel-internal-code`，原窗口做第①步，erp-dev-2 接手做完②③并实测。

## 改了什么

| 步 | 内容 | 主要文件 |
| --- | --- | --- |
| ① | 盘点、期初两张模板加独立「内部编码」列（紧跟「编码」，预置文本）；导入先认内部编码；「编码」一列填系统编码；按台商品填了实盘数改为逐行报出（原来整表失败）；不带盘点下载模板按仓的公司过滤 | `inventory/workbook.py`、`opening_workbook.py`、`opening_batch.py`、`count.py` |
| ① 补 | 原提交删了 `products.contracts.count_code`，但盘点明细「编码」与 LINE 查码回执仍在调用（一跑到就报错）：恢复 | `products/contracts.py` |
| ② | 按台公司（汽车 / 电器）的盘点表：号码三列（主号、副号、颜色，叫法按公司类型）紧跟条码；按台商品一台一行（这份盘点记在这一行上的台实盘 1 + 这一位置本次还没记的在库台）；导入按主号认在库的台，实盘 1 逐台记、0 撤掉上回导入记的那条、空着只登记账面行；认不到 / 认出几台 / 与这一行商品对不上的号只报「该台不存在」，**不当场建台**；手机已逐台扫过的台不重记；重传同一张表改同一条（幂等锚 `import:<盘点>:unit:<台>`）；按台行的仓按盘点所属公司认 | `workbook.py`、`count.py`、`inventory/api.py`、`stocktake.js` |
| ③ | 按台公司的期初表同样多出号码三列；按台商品一台一行、数量必须 1；同一位置（商品 × 仓 × 库位）的几台合成一笔期初（数量 = 台数、单位成本须一致），提交时逐台登记（`serial_opening`，引用期初单）；号码表内重复、已登记、与商品的码 / 别的车架号发动机号撞，**预览就报在那一行**；普通公司或不按台的商品号码三格不看；期初页的行保留表里的内部编码与号码（之前只留固定几格，内部编码会在没选表头仓时丢失） | `opening_workbook.py`、`opening_batch.py`、`stock-opening.js` |
| 共用层 | `pearnly_core.browser_text` 登记 `serial.` 前缀（词在 ERP 术语表）：服务端原来取不到「车架号 / 发动机号 / 颜色」，表头写成词条键，带中文 / 泰文表头的表导回也认不出号码列——原有的「按台期初登记」模板一并修好 | `pearnly_core/browser_text.py` |

文案：没有新增、没有改已有文案；行级报错沿用已有的「该台不存在」「该号码已登记」「台数与数量不符」「按台管理的商品…」。

## 验证（基线 `origin/main` `6bb94966`）

- 离线（生产镜像 `e9aa959b`）：`test_stock_count_workbook`（号码列位置、文本格式、只给主号的行照读）、`test_opening_workbook`（汽车模板号码列、泰文 / 中文表头、号码须文本）、`test_opening_batch`（一台一行合成一笔、表内重号、已登记、撞码、数量须 1、同位置成本一致、普通公司号码不看）、`test_erp_locations`：通过。
- 流程（本机 verify-lab）：新流程 `erp.stock.excel-units`（老数据）通过——汽车公司建本地按台商品 → 期初三台一笔、账面 3、三台在库带发动机号颜色 → 开盘点上传「第一台 1、第二台 0、未知号 1」→ 第一台记一条导入记录、第二台不记、未知号报第 4 行「该台不存在」且没建台 → 同一张表重传不重记 → 这份盘点的模板一台一行、第一台实盘 1。`--changed` 其余受影响流程：`erp.products.opening`、`erp.stocktake.scan`、`erp.stocktake.new-product`、`erp.stock-in.units` 全过；`erp.stock-entry.scan` 4 格不通过（商品选择框搜不到），**main `6bb94966` 上单跑同样不通过**，与本分支无关，已报主会话。
- 实验栈手动核对：普通公司盘点 / 期初模板没有号码列、有内部编码列；汽车公司两张模板号码三列表头为 Chassis No / Engine No / Color；只填「内部编码 + 实盘」的表认出商品记数；中文「车架号 / 发动机号 / 颜色」、泰文「เลขตัวถัง」表头导回认出号码列；按台期初登记模板中文表头为「车架号 / 发动机号 / 颜色 / 仓库 / 仓位」。

## 共用件连带回归（`scripts/check-impact.py`）

命中 `erp-count-code`、`browser-text`。

| 入口 | 结果 |
| --- | --- |
| erp.stock-count.code（盘点按码认商品：录入、上传、LINE） | 录入：流程 `erp.stocktake.scan` 通过；上传：`erp.stock.excel-units` 与手动内部编码导入通过；LINE 手机：没测到（本次未改 LINE 页，真机要测试机） |
| erp.stock-count.upload | 上传表：通过（流程 + 手动）；凭证照片：没测到（本次未改照片路径） |
| erp.inventory.opening-upload | 通过（期初文件读回：泰文表头、内部编码；流程期初提交） |
| erp.inventory.opening.row-warehouse | 没测到页面点选（本次只加了行上的号码格，行仓逻辑未改）；预览按行仓核库位由 `test_opening_batch` 覆盖 |
| erp.inventory.opening.item-scan | 没测到（扫码填商品未改） |
| erp.products.new-dialog.opening | 通过（流程 `erp.products.opening`） |
| erp.stock-count.new-product | 通过（流程 `erp.stocktake.new-product`） |
| erp.serials.export（按台期初登记模板） | 模板表头：通过（中文「车架号 / 发动机号 / 颜色」，修复前是词条键）；按台记录导出：没测到 |
| erp.exports | 盘点模板、期初模板：通过（能打开、表头正确）；其余导出未改，没测到 |
| erp.browser-text | 盘点 / 期初表头：通过；其余页面文案：没测到（只加了 `serial.` 前缀，别的前缀不变） |
| shared / cowork / dms / cc.browser-text | 没测到（只新增 `serial.` 前缀，这些产品不用 `serial.` 键） |
| erp.page.inventory / erp.page.stock-count / 盘点详情切换两项 / 收发存排序 | 页面冒烟没跑（`scripts/smoke/` 在测试机实验栈，动测试机先问主会话）；盘点页由本地流程打开过、无前端报错 |
| erp.line.stocktake.liff-camera、erp.stock-in.attach、erp.stock-out.attach、erp.records.scope、erp.print.stock-count | 没测到（本次未改这些路径，是 `inventory/api.py` / `count.py` 同文件命中） |

## 限制

- 盘点表按台：重传时把某台的实盘从 1 改成 0 只撤导入记的那条，手机上扫过的那条不动（以现场扫的为准）。
- 真机（LINE 盘点页）、测试机实验栈冒烟没测；岳父用自己的车辆清单试一份是业务验收，待测试窗口安排。
