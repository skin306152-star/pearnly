# ERP 模板下载修复与扫码盘点核查

- 当前要求：删除盘点列表右上角重复的模板按钮，保留新建/导入弹窗；核查商品、库存、盘点的模板/文件下载和旧项目连续扫码盘点迁移情况。
- 基线：本地 `main@55ecbf07e88efc7340dfb36bfdd1151431a412ce`；工作树 `/Users/skin/.codex/worktrees/erp-template-audit/pearnly`，分支 `codex/erp-template-audit`。本地开发，未授权推送或生产部署。
- 归属：`pearnly_erp_portal/public/desk` 的现有下载交互及 `inventory/api.py` 导出表头；不改变后端数据、权限、导入契约或新增扫码流程。本次扫码部分为只读核查。

## 下载链路

根因已在本地浏览器复现：点击新建弹窗里的盘点模板后，控制台抛 `TypeError: frappe.utils.get_url is not a function`，没有发出下载请求。当前安装的 Frappe 前端没有此方法，三个页面的全部五处下载调用均使用它。历史记录中的接口测试不能证明这些按钮有效。

修复：移除重复的列表模板按钮；五处下载改用同源相对地址和浏览器原生 `window.location.assign`，附件响应保留当前页面，不依赖弹出新窗口。

| 入口 | 服务端实现 | 当前判断 |
| --- | --- | --- |
| 商品 → 导入 → 下载模板 | `products.service.api_import_template`，14 列 XLSX | 修复后点击返回 200 附件；5155 bytes；文件内容校验通过 |
| 库存 → 导出 | `inventory.api.api_stock_export`，筛选命中的全部库存 CSV | 修复后点击返回 200 附件；244 bytes；UTF-8 BOM、9 列、2 条数据校验通过 |
| 新建盘点 → 上传导入 → 盘点模板 | `inventory.api.api_stock_count_template`，当前结存 XLSX | 修复后点击返回 200 附件；5337 bytes；8 列、2 条数据校验通过 |
| 进行中的盘点 → 导入 → 盘点模板 | 同上，带 `name` 按任务快照导出 | 修复后点击返回 200 附件；`STC-00023-template.xlsx`、5334 bytes；8 列、2 条数据校验通过 |
| 盘点详情 → 导出 | `inventory.api.api_stock_count_export`，汇总+明细 XLSX | 修复后点击返回 200 附件；`STC-00023.xlsx`、5993 bytes；双表结构校验通过 |
| 商品清单导出 / 库存导入模板 | 当前原生 Desk 页面没有这些入口 | 不能把现有的商品导入模板、库存导出算作这两种能力 |
| 差异看板独立报表导出 / PDF / 打印 | 租户没有原生导出权限，页面裁剪打印/PDF等管理菜单 | 当前未开放；盘点详情导出是另一入口 |

内容核查另发现明细表状态表头泄漏 `stock.count.entryState`（语言包无此键），已改用页面已有的 `stock.col.status`。中/英/泰/日四种语言生成真实 `STC-00019` 导出后均为正确状态标题，所有单元格均无 `stock.*` 原始键。另登记存量缺口：盘点导出汇总表未带库位列，明细表有库位；本次未改报表字段契约。

## 扫码：只迁了部分，连续流程缺失

- 旧线上只读核验：2026-09-22，GCP `pearnly` / `asia-southeast1`，Web 100% 流量仍在 `pearnly-web-ec2e5c957e6b-s3`，digest `sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226`。固定 `501589f0d7426fff2c24584d55e2732e68961063:docs/deployment/MIGRATION_STATUS.md` 映射完整源码为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，用 `git show` 只读核对，未修改来源仓。
- 旧代码 `static/stocktake/ui.js` 在桌面和手机任务页均挂 `PearnlyStocktakeCounter`；`counter.js` 的 `save()` 成功后若在相机模式会重新 `scan()`，形成“扫码 → 填数量 → 保存 → 自动扫下一件”。`camera.js` 复用条码引擎并增加二维码，包含 WASM 解码加载、多帧确认等。
- 新桌面 `public/desk/stocktake.js` 只有按行“记一笔”，没有调用 `p.scan`。`desk/scan.js` 目前供商品条码/二维码字段使用，商品扫码存在不等于盘点扫码已接通。
- 新独立手机页 `www/stocktake.html` 有相机/查询/保存界面，但扫到即 `stopCamera()`；保存后只重新查询当前商品，没有自动开启下一轮相机。因此连续盘点未迁。
- 手机页另有实质断点：`call(method, args, post)` 的 GET 分支没有把 `args` 编进查询串，`openTask(name)`、`lookup(name, code)` 的参数不会发出。接口存在不能代表手机页闭环可用。
- 手机页只调用浏览器 `BarcodeDetector`，旧 WASM 解码兼容层未迁；是否有扫码能力取决于真实浏览器支持，不能照旧记录将浏览器品牌或版本一概写成支持。
- 当前 ERP Desk 没有 `/stocktake` 手机页入口，App 内未找到指向它的默认 LINE 菜单实现；手机页还依赖有效 LINE 绑定/LIFF 配置。未读取或修改外部 OA 菜单，未进行真 LINE 或真摄像头验收。
- 结论：后端记录、查码接口和一部分手机代码已迁；桌面扫码入口、手机调用闭环、保存后连续扫码及解码兼容没有完整迁移。不是仅缺一个按钮。此次未擅自开始补迁。

## 验证与收尾

- 架构检查、三个 JS 与 Python 语法检查和 diff 检查通过；没有更改翻译包或 standalone frontend。
- 本地运行确认：Docker context `colima-pearnly`，Compose `pearnly-sample`，Site `qerp.localhost`；ERP Web/Bench 镜像 `sha256:90c1d545d215d6081965740013dd8af84337ca23c5fde5c36acdf162cd4655c3`，源码挂载来自 `/Users/skin/pearnly/apps`，Site 数据卷 `pearnly-sample_sites-erp`。
- 浏览器逐项点击五个入口，均发出正确同源 GET，回包为 200 + `Content-Disposition: attachment`，页面保持原位；1280px 盘点列表只剩刷新/新建，弹窗模板保留。内置自动化浏览器在收到附件后以 `ERR_BLOCKED_BY_CLIENT` 拦截下载最终保存，因此这部分证据到附件响应为止，不冒称文件已经保存到用户磁盘。
- 文件内容另在实际 qerp Site 的独立只读进程中用 `owner@qerp.local` 权限生成，并以 openpyxl/csv 解析验证上述表头、列数、行数、双表结构；结束 rollback，无测试文件、导入或盘点记录写入。XLSX 因生成时间不同，压缩字节数可能略有变化。
- 实现提交 `aeffd98`、导出表头修复 `869146d` 已快进合入本地 main，并重载本地 ERP Web。核验远端 main 为 `ac33e0c7362915ae314295cb26f7748cb5589de0`，远端任务分支不存在；未推送、未部署生产。
- 收尾盘点：任务工作树无未保存/ignored 内容、符号链接、进程或容器挂载，独有提交已被本地主线包含；本记录提交合回后删除本任务工作树和本地分支并回读。未生成测试下载文件或临时资源。
- 保留本地 ERP 服务和浏览器盘点页面供用户查看；另外两份迁移工作树属其他任务，不处理。扫码缺口留待后续工作包按上述当前线上基准补齐；用户验收待确认。
