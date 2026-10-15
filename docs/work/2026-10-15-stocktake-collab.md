# 库存盘点：多人分区、手机看差异、相机报错分类（ERP + Cowork）

- 用户口径（2026-10-14 / 10-15）：真机演示后提三项——安卓相机打不开、多人分区协作、手机看不全差异；
  「库存盘点 erp 和 cowork 都要同步」；相机「一定要行，不是拍照兜底」。
- 基线 `origin/main a32c8d66`；分支 `pearnly/stocktake-collab`；工作树 `/Users/skin/pearnly-wt/stocktake-collab`。
- 归属：共用扫码器（`pearnly_core/public/desk/scan.js`）、ERP 与 Cowork 的盘点页 / 录入弹窗 / 盘点后端。

## 相机（共用，ERP 与 Cowork 同一份）

现象：一台安卓手机点扫码只显示「请在 HTTPS 页面允许相机」；换另一台三星 A14（安卓 13、Chrome 106）正常。
USB 远程调试读回：该机相机权限 granted、原生 BarcodeDetector 支持全部格式、前后镜头都在——代码在安卓上可用，
第一台的具体原因（权限曾被拒 / 系统未给浏览器相机权限 / LINE 内置浏览器 / 被占用）用户暂不再查。

改法（不做拍照兜底）：

- 先要相机、识别器并行加载：识别器慢或坏不再被当成「相机打不开」，且相机打开后识别器失败会关掉相机。
- 指定后置镜头起不来（Overconstrained / NotReadable / Abort / NotFound）：按名字挑后置镜头重开，再不行要系统默认镜头；
  权限被拒不重试。
- `play()` 被打断重播一次；被拦自动播放时点一下画面再播，不算失败。
- 报错按原因分 7 类给对应提示（`p.scan_failure` / `p.scan_failure_text`，词条在 `pearnly_core` 术语表）：
  不支持、App 内置浏览器、权限被拒、被占用、没有相机、识别器加载失败、其它。
- 调用方：ERP / Cowork 盘点录入弹窗、ERP 扫码弹窗。旧词条 `stock.workbench.cameraFailed`、`product.scan.denied`、
  `cowork.stock.workbench.cameraFailed` 已无调用，从术语表删去。
- 未动：ERP 的 LINE 盘点页 `www/stocktake.html` 自带一份相机代码（LIFF 内运行），本批没改。

## 多人分区协作

用途：一份盘点多人同时做，每人负责不同「仓库 / 类别」。不做指派：谁在盘由有效记录推出。

- 类别来源：Cowork 上传表新增可选列「商品类别」（四语表头都认，模板末列；`Cowork Stocktake Item.category`）；
  ERP 取商品主档 `category_id`。
- 页面（两边同一份改动）：筛选栏多「仓库」「类别」两个原生 Select，只在这份盘点里多于一个值时出现；
  选了范围，表格与页头进度（已盘 / 行数 / 差异）只算这一片；上次在这份盘点上选的范围记在本机浏览器。
- 没选范围且不止一个区时，表格上方列「各区进度」：区域、已盘 / 行数、差异、盘点人；点一行切到那个区。
- 实时：任何人记录 / 改 / 作废 / 重盘 / 结束，服务端按人推 `pearnly_stocktake_changed`（只带盘点号，
  只推给能看盘点的人，同一请求只推一次）；正在看这份盘点的页面 0.8 秒防抖后重取，列表页刷新进度数。
  Cowork `stocktake/live.py`，ERP `inventory/count_live.py`。
- 录入弹窗：扫到的货在本次盘点里已有有效记录时，摆出「已记录：谁 · 仓库 / 位置 · 数量」（改已录记录时不显示）。

## 手机看差异

原因：只在渲染时判断一次 767px；横屏宽度超过后给电脑版 11 列定宽，且转屏不重画。
改法：三档——竖屏（<576）四列、横屏 / 平板（<992）六列（物料、账面位置、账面、实际、差异、状态），都按比例铺满；
电脑全列。跨档（转屏）时重画。各区进度表同样分档。

## 验证

- 本地：全量 CI 同名闸门 78 项通过；`tests/test-stocktake-scanner.cjs`（新增失败分类与后置镜头重试）、
  `tests/test_cowork_stocktake_upload.py`（新增类别列）通过。
- `tests/test-product-scan.cjs` 在 main 上原本就失败（`p.link_editors` 未定义），不在 CI，本批未动。
- 测试机实测：待做（见下）。

## 待办 / 交接

- 测试机 `lab-start` cowork / erp 实测，再交用户试用；用户通过后合入 main、按批次部署。
