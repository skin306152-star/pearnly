# ERP 模板下载修复与扫码盘点核查

- 当前要求：删除盘点列表右上角重复的模板按钮，保留新建/导入弹窗；核查商品、库存、盘点的模板/文件下载和旧项目连续扫码盘点迁移情况。
- 基线：本地 `main@55ecbf07e88efc7340dfb36bfdd1151431a412ce`；工作树 `/Users/skin/.codex/worktrees/erp-template-audit/pearnly`，分支 `codex/erp-template-audit`。本地开发，未授权推送或生产部署。
- 归属：`pearnly_erp_portal/public/desk` 的现有下载交互；不改变后端数据、权限、导入契约或新增扫码流程。本次扫码部分为只读核查。

## 下载链路

根因已在本地浏览器复现：点击新建弹窗里的盘点模板后，控制台抛 `TypeError: frappe.utils.get_url is not a function`，没有发出下载请求。当前安装的 Frappe 前端没有此方法，三个页面的全部五处下载调用均使用它。历史记录中的接口测试不能证明这些按钮有效。

修复：移除重复的列表模板按钮；五处下载改用同源相对地址和浏览器原生 `window.location.assign`，附件响应保留当前页面，不依赖弹出新窗口。

| 入口 | 服务端实现 | 当前判断 |
| --- | --- | --- |
| 商品 → 导入 → 下载模板 | `products.service.api_import_template`，14 列 XLSX | 已有前后端，下载调用已修，待浏览器复验 |
| 库存 → 导出 | `inventory.api.api_stock_export`，筛选命中的全部库存 CSV | 已有前后端，下载调用已修，待浏览器复验 |
| 新建盘点 → 上传导入 → 盘点模板 | `inventory.api.api_stock_count_template`，当前结存 XLSX | 已有前后端，下载调用已修，待浏览器复验 |
| 进行中的盘点 → 导入 → 盘点模板 | 同上，带 `name` 按任务快照导出 | 已有前后端，下载调用已修，待浏览器复验 |
| 盘点详情 → 导出 | `inventory.api.api_stock_count_export`，汇总+明细 XLSX | 已有前后端，下载调用已修，待浏览器复验 |
| 商品清单导出 / 库存导入模板 | 当前原生 Desk 页面没有这些入口 | 不能把现有的商品导入模板、库存导出算作这两种能力 |
| 差异看板独立报表导出 / PDF / 打印 | 租户没有原生导出权限，页面裁剪打印/PDF等管理菜单 | 当前未开放；盘点详情导出是另一入口 |

## 扫码：只迁了部分，连续流程缺失

- 旧线上只读核验：2026-09-22，GCP `pearnly` / `asia-southeast1`，Web 100% 流量仍在 `pearnly-web-ec2e5c957e6b-s3`，digest `sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226`。固定 `501589f0d7426fff2c24584d55e2732e68961063:docs/deployment/MIGRATION_STATUS.md` 映射完整源码为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，用 `git show` 只读核对，未修改来源仓。
- 旧代码 `static/stocktake/ui.js` 在桌面和手机任务页均挂 `PearnlyStocktakeCounter`；`counter.js` 的 `save()` 成功后若在相机模式会重新 `scan()`，形成“扫码 → 填数量 → 保存 → 自动扫下一件”。`camera.js` 复用条码引擎并增加二维码，包含 WASM 解码加载、多帧确认等。
- 新桌面 `public/desk/stocktake.js` 只有按行“记一笔”，没有调用 `p.scan`。`desk/scan.js` 目前供商品条码/二维码字段使用，商品扫码存在不等于盘点扫码已接通。
- 新独立手机页 `www/stocktake.html` 有相机/查询/保存界面，但扫到即 `stopCamera()`；保存后只重新查询当前商品，没有自动开启下一轮相机。因此连续盘点未迁。
- 手机页另有实质断点：`call(method, args, post)` 的 GET 分支没有把 `args` 编进查询串，`openTask(name)`、`lookup(name, code)` 的参数不会发出。接口存在不能代表手机页闭环可用。
- 手机页只调用浏览器 `BarcodeDetector`，旧 WASM 解码兼容层未迁；是否有扫码能力取决于真实浏览器支持，不能照旧记录将浏览器品牌或版本一概写成支持。
- 当前 ERP Desk 没有 `/stocktake` 手机页入口，App 内没有指向它的 LINE 菜单配置实现；手机页还依赖有效 LINE 绑定/LIFF 配置。未进行真 LINE 或真摄像头验收。
- 结论：后端记录、查码接口和一部分手机代码已迁；桌面扫码入口、手机调用闭环、保存后连续扫码及解码兼容没有完整迁移。不是仅缺一个按钮。此次未擅自开始补迁。

## 验证与收尾

- 架构检查、三个 JS 语法检查和 diff 检查通过；仅原生 Desk JS 改动，没有更改翻译或 standalone frontend。
- 本地运行确认：Docker context `colima-pearnly`，Compose `pearnly-sample`，Site `qerp.localhost`；ERP Web/Bench 镜像 `sha256:90c1d545d215d6081965740013dd8af84337ca23c5fde5c36acdf162cd4655c3`，源码挂载来自 `/Users/skin/pearnly/apps`，Site 数据卷 `pearnly-sample_sites-erp`。
- 浏览器复验、下载内容检查和收尾待完成。没有修改业务记录，没有外部写入。
