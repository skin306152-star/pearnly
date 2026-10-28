# 盘点扫码查到商品却不出录入框（polymer，2026-10-28 热修）

分支 `pearnly/stocktake-scan-lookup`（基线 origin/main `ccf056c2`；生产 `fcb65cca`，两者在盘点、扫码、库存相关文件上一致）。

## 现象

生产 polymer.pearnly.com（10-28 凌晨开户）10:53–11:02（曼谷），岳父的客户在 LINE 里用「ตรวจนับต่อเนื่อง」扫纸质单上的二维码：
绿勾出现（读码成功），录入框不出。前端日志里 `api_stock_count_lookup?count=STC-00011&code=TC001167` 每秒十几次，全是 200，
iPhone LIFF 与安卓 Chrome 155 一样。

## 根因（已用证据确认，和最初的推测不同）

最初推测是 `match()` 拿到结果后前端抛异常、被 catch 显示成「ไม่พบสินค้า」。实际不是异常：

1. **线上数据**（10-28 一次只读 SSH，`bench` 读路径，没有写）：商品 PRD-00010「HLY-002/W Oak 2.8m」单位「เส้น」，10:49 由盘点人
   `fonpolymer.polymer@pearnly.com` 新建，**勾了按台管理**（`track_serial=1`，种类 device），同时勾了批次、有效期；全站 0 条登记的台。
   商品表单这几格默认都是不勾（DocType 与 `products.js` 默认 0），是建商品时选上的。盘点 STC-00011 在 WH-00003「1000」。
   以管理员和盘点人两个身份调 `lookup_count(STC-00011, TC001167)`：都返回 `track_serial: true, unit: null, units: []`，Error Log 0 条。
2. **前端**（`stocktake-entry.js` `match()`）：`track_serial && !unit` 这一支（c2be1899「按台管理」引入）只提示一句
   「สินค้าที่ติดตามรายชิ้นให้นับทีละชิ้น」就 `scanner.resume()`；镜头还对着同一个码，读码器 0.4 秒后再交一次，于是同一个码每秒被查好几次，
   录入框永远不会出来（按台管理的商品要扫这一台自己的号，扫商品码本来就不出录入框）。
3. **同一个写法的老问题**：失败分支（catch）也是 `scanner.resume()`，查不到的码同样被反复查——08bfb419 就是这样，只是以前没碰上。

## 复现（本机样板 `qerp.localhost`，无头 Chrome）

- 数据：测试公司 SCANLAB（CO-00036）7 个仓（「1000」…「7000」）、商品挂 Express 公司只靠二维码内容 TC001167（另配条码
  8851932283953 给假摄像头用）、该仓无账、连续盘点 STC-00045。样板站点的库结构落后于挂载的 main 代码、web 进程 10-16 起没重启：
  先 `bench backup` 再只对 `qerp.localhost` `migrate`，清缓存并重启样板 ERP web / worker（挂载不变，没接管样板）。
- 假摄像头（`~/pearnly-devtools/cam/drive.mjs` + `bar.y4m`，连续盘点开相机 9 秒）：

  | 场景 | 旧代码（main） | 新代码（本分支注入） |
  |---|---|---|
  | 按台管理、这个仓一台没登记 | 查码 **20 次**，录入框不出，只提示逐台盘 | 查码 **1 次**，提示「商品名 · 逐台盘 · 这个仓还没登记这件商品的台」 |
  | 码查不到（服务端 417） | 查码 **20 次** | 查码 **1 次**，提示「ไม่พบสินค้า」 |
  | 普通商品、扫码命中、该仓无账 | 1 次，录入框出现，商品名、实际仓库「1000」 | 同左（正常路径没变） |

## 修法（按类，一次到位）

1. **读码器 `resume(code)`**（共用 `pearnly_core/public/scan/reader.js`，Desk 包装 `pearnly_core/public/desk/scan.js` 透传）：
   接着读但跳过这个码，读到别的码才放开。不带参数与原来一样，旧调用方不受影响。
2. **盘点录入**（ERP、Cowork 两份）：所有「没认出来」的出口（撞号候选取消、按台管理没扫到台、查找失败）都 `resume(code)`，
   同一个码不再被镜头反复查；手动点「查找」或回车照常再查。
3. **失败如实说**：服务端明确不认（HTTP 417）才说「ไม่พบสินค้า」；网络、服务端出错（5xx）、这页自己的脚本错一律
   「ค้นหาไม่สำเร็จ」（新词条 `stock.workbench.lookupError` / `cowork.stock.workbench.lookupError`），并记到控制台。
4. **看得见**：没认出来时除了写进弹窗，再用原生浮动提示（`frappe.show_alert`，橙色）说一遍——相机开着时弹窗里那一行在画面下面，
   手机上看不到（复现截图里提示已在屏幕外）；服务端 417 时 Frappe 自己会弹原生提示框，不重复。
   前端自身出错交给 `window.pearnlyReportError(error, "stocktake-lookup")`（服务端返回的错服务端自己记，不重复上报）。
5. **按台管理的提示说清楚**：前面加商品名；这个仓一台都没登记时补一句「คลังนี้ยังไม่มีชิ้นที่ลงทะเบียนของสินค้านี้」（新词条）。

不在本次：不替客户改商品设置（PRD-00010 的按台管理、批次、有效期是客户勾的，是否取消由客户 / 主会话定）；
LINE 手机盘点页 `www/stocktake.html` 读到一个码就关相机，没有循环，它对按台管理商品的提示另议。

## 全仓同类排查

`p.scan`（物料、入库、出库、采购、销售、开账、选择框扫码 `p.scan_link`、按台登记）读到一个码就关相机，不会循环；
收银台的 `resume` 是班次作用域，与相机无关；会「失败后接着读」的只有 ERP / Cowork 盘点录入，两份都已改。

## 为什么当时没拦住

- c2be1899 的设计与验证都按「扫车身二维码认出某一台」（已登记的台），没有「扫的是商品码」「这个仓一台没登记」「失败后相机还对着同一个码」这几种；
  它的单元测试在服务端（`tests/test_erp_serials.py`），前端盘点录入没有测试。
- 前端盘点扫码测试（`tests/test-stocktake-scanner.cjs`、`tests/test-line-stocktake.cjs`）都**不在** development.yml 里，分支 CI 不跑。
- 验收数据是车辆样例（台都登记过）；polymer 是开户当天客户自己建商品时勾了按台管理，验收没覆盖到这种数据。

补上：新测试 `tests/test-stocktake-entry-scan.cjs`（扫码命中、该仓无账、连续盘点：录入框出现、商品名、单位、实际仓库「1000」、只查一次；
按台管理没登记 / 有登记两种提示；417 / 5xx / 脚本错三种失败的提示与跳过；手动查找不受跳过影响；Cowork 两种失败），
`test-stocktake-scanner.cjs` 加 `resume(code)` 跳过用例；三份盘点扫码测试接进 development.yml 一步「Verify stocktake scanning」。
旧代码跑新测试：失败（提示里没有商品名、没有跳过）。

## 共用件连带回归（`scripts/check-impact.py`：desk-scan-core 17 个入口）

本机样板，无头 Chrome 假摄像头（`bar.y4m`），新读码器与盘点录入注入页面：

| 入口 | 结果 |
|---|---|
| erp.stock-count.entry.camera（盘点录入相机，连续盘点） | 通过：普通商品录入框出现；按台管理没登记 / 码查不到都只查 1 次 |
| erp.products.new-dialog.scan 等走 `p.scan` 的（物料新建 / 详情、入库、出库、开账、采购、销售明细、选择框扫码） | 通过：通用扫码弹窗读出 8851932283953 并关闭（这些入口共用 `p.scan`，本次没改它的代码） |
| 盘点录入开相机顺序（先显示画面再交读码器） | 通过：readyState 4、640 宽、读出码 |
| erp.line.stocktake.camera（直接用读码器的用法） | 通过：读出码；`resume(码)` 后 3 秒不再交出，`resume()` 后能重读 |
| cowork.stocktake.entry.camera | 节点测试通过；没在 Cowork 样板上开相机实测（本次只改了它的失败分支，读码同 ERP） |
| erp.cashier.camera | 通过：收银台相机就是 `p.scan`（`pearnly-pos-adapter.js`），与上一行同一次实测 |
| erp.cashier.scanner-gun | 没改到：扫码枪是键盘输入，不经过读码器 |
| erp.line.stocktake.liff-camera、cowork.line.stocktake.liff-camera、dms.liff.id-scan、shared.desk.liff-boot | 没测到：需要真手机 LINE / LIFF |
| shared.upload.take-photo、erp.stock-count.upload、cowork.stocktake.upload、erp.scan.pick-product、erp.stock-count.code | 没改到：拍照、上传、撞号候选、按码认商品的服务端逻辑都没动 |

**没验证**：真 iPhone / 安卓 LINE 里扫纸质单（测试机与手机不在本次范围）；生产上客户实际再扫一次的结果。

## 文案

新增 2 条（四语），未改已有文案；`python3 scripts/check-copy.py` 见交付说明。

## 本机测试数据

样板 `qerp.localhost`：CO-00036「SCANLAB 测试公司」、WH-00037…43、PRD-00044、STC-00045，以及 Administrator 的当前公司默认值；
迁移前备份 `20261028_111211`。验收结束后删除（见收尾）。
