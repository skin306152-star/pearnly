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
- **一份读码器**（用户 10-15："不都是一样的原理和使用方式吗"）：开相机 / 读码 / 出错分类抽成不依赖 Frappe 的
  `pearnly_core/public/scan/reader.js`（`window.PearnlyScan`）。后台 `desk/scan.js` 只留 Frappe 界面层（画面、反馈、结果音、提示）；
  ERP 的 LINE 盘点页 `www/stocktake.html` 删掉自带的那份相机代码，改用同一个文件（经 core 新登记的 jinja 方法
  `versioned_asset` 带内容版本号引用，改了手机不读旧缓存），只留卡片界面。提示文案统一为 core 术语表 `common.scan.*`；
  在 LINE 里被拒时提示「请在手机设置里允许 LINE 使用相机」（`common.scan.in_line`）。
- 行为统一：单帧读失败不再终止读码（LINE 页原本如此，后台原来会报错停掉）；开相机即读第一帧；
  紧跟着的 stop（连点两次扫码）在要权限之前生效。

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
- 分支 CI（Development checks）run 38704151856 通过（`2ba62d65`）。
- 共用层候选离线检查：测试机生产镜像 `232bf6ea`（2bc4aad1）挂本分支 `2ba62d65` 源码，`ci-candidate-checks.sh offline` 71/71 OK、LANE EXIT 0。
- `tests/test-product-scan.cjs` 在 main 上原本就失败（`p.link_editors` 未定义），不在 CI，本批未动。
- 测试机 ERP（2026-10-15，线上镜像 `sha256:232bf6ea…`（2bc4aad1）+ 本分支实时源码，`testerp.pearnly.com` 切到测试机）：
  测试副本里给 7 个商品设类别 1 / 2，建 `STC-00061 多人分区盘点测试`（Test repository 4 行），管理员记 2 笔。
  | 场景 | 结果 |
  | --- | --- |
  | 只有一个仓 → 仓库筛选不出现；两个类别 → 类别筛选出现，空值显示「分类」 | 通过 |
  | 没选范围 → 各区进度两行（分类 1 / 分类 2、已盘 1/2、差异、盘点人）；点「分类 2」→ 表格与页头只算这一片 | 通过 |
  | 录入弹窗「已记录：Administrator · Test repository / 未填 · 73」 | 通过 |
  | 375 宽竖屏：各区小表按行数定高、明细四列「账面 / 实盘 / 差异」不截断；812×375 横屏六列铺满、转屏重画 | 通过（中途修了：框架在手机上把所有表格滚动区强制成一屏高） |
  | 实时：另一端写一笔 → 打开着的页面约 1 秒后自动刷新（分类 2 变 2/2、差异 2） | 通过 |
  | 内置浏览器无相机 → 「相机权限被拒绝…」并收起黑框 | 通过 |
- 测试机复验共用读码器：LINE 页 `testerp.pearnly.com/stocktake` 引用 `reader.js?v=8c8c8727f2c1056a`（= 本地文件 sha256 前 16 位）、HTTP 200；
  后台加载顺序 reader.js → core desk/scan.js → ERP desk/scan.js，无相机时显示「相机权限被拒绝…」中文提示。
  真机读码（后台与 LINE 内）待用户手机验。
- 测试副本已知情况（非本批）：用户 aksarapak 能看见别公司的盘点 STC-00061 但录入时报「未找到仓库」（仓库按公司隔离）。

## 待办 / 交接

- Cowork 测试机实测：等「接手 CC 夜间批」窗口部署完、验收窗口真机复验结束后通知（它们在生产 testcowork 上验收，测试机 CC 桌面版也连着它）。
- 真机：三星 A14 由验收窗口转请用户登录 testerp 并扫一次码；adb 用 9223 端口（9222 归验收窗口）。
- 部署：由「接手 CC 夜间批」窗口负责批次；本分支验完自行变基、过闸、快进 main 推送，把 SHA 告诉它。
- 交用户真机试用（ERP 已可在 testerp 试）；用户通过后合入 main、按批次部署。测完 `lab-stop erp` / `lab-stop cowork`。
