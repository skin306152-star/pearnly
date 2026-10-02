# 导出改后台任务（2026-10-02）

协调窗口（接手 Cowork 三个对账功能压测）分派，交修复窗口集成。分支 `codex/async-exports`，基线 `c5092f48`。

## 问题

ERP 销售 / 采购 / 库存 / 盘点 / 交班、Cowork 对账 / 销项核查 / 盘点 / 任务看板、账单明细的导出都是同步请求、没有时间上限：
数据多就超过网页进程 120 秒（gunicorn `--timeout=120`）被掐断，用户拿不到文件。

## 做法（共用层 `pearnly_core.exports`，只此一个窗口改）

- 每个导出交两个函数：`count`（这次要导多少行，同时做与生成**同一套**权限检查；只需判断够不够阈值，能封顶就封顶）和
  `build`（生成文件，回 `bytes` 或 `{"filename", "content"}`）。入口统一调 `exports.service.deliver(count=, build=, args=)`。
- 行数 ≤ `pearnly_export_sync_rows`（站点配置，默认 1000）：这次请求里生成并直接回文件，与原来一样，小文件不绕弯。
- 超过：建一条 `Pearnly Export`（随机 32 位十六进制名），排进 **default** 队列（不进 long：Cowork 的 long 里有等识别的对账，
  最长 25 分钟），回 `{"export_job"}`。后台以**发起人身份、发起时的语言**生成；完成 / 失败原生实时推送 `pearnly_export` 给本人。
- 文件存站点私有目录 `private/pearnly-exports/<随机键>`（0600，不建 File、不经 /files 或 /private/files），只有发起人能经
  `pearnly_core.exports.api.download` 取；**下载时再调一次 `count`**，生成后被收回权限的人拿不到。1 小时过期，调度每 10 分钟清理
  （`*/10` cron，不加常驻资源）。下载响应 `no-store`。`Pearnly Export` 原生读权限只给本人（`has_permission` / 查询条件）。
- 失败原因：业务拒绝（权限、校验、找不到）照原话给本人；程序错误只给一句「导出文件没有生成成功」，细节记错误日志。
- 前端 `frappe.pearnly.export_file(method, args)`（加在 `product.js`，不新增脚本，加载方式不变）：回的是文件就直接存；回任务就挂
  原生黄色提示等推送，推送连着每 20 秒、断开每 5 秒问一次 `api.status` 兜底；做完自动下载 + 原生完成提示（带提示音，跟账户静音），
  失败原生弹窗说原因。文案只有「正在准备文件」「文件已准备好」两句（+ 失败 / 过期两句），四语在 `pearnly_core/i18n/messages.tsv`。

## 改到的入口（12 个）与调用方

| 产品 | 接口 | 计数 / 生成 | 前端 |
| --- | --- | --- | --- |
| ERP | `sales.api.api_sales_export` | `sales.output.export_size`（取数封顶）/ `workbook` | `sales.js` |
| ERP | `purchasing.api.api_purchase_export` | `purchasing.output.export_size`（只取名字、封顶）/ `workbook` | `purchase.js` |
| ERP | `inventory.api.api_stock_export` | `inventory.api.stock_export_size` / `stock_export_file`（CSV） | `inventory.js` |
| ERP | `inventory.api.api_stock_count_export` | `stock_count_export_size`（品项数 + 记录数）/ `stock_count_export_file` | `stocktake.js` |
| ERP | `cashier.api.api_shifts_export` | `cashier.output.export_size` / `workbook` | `pos-shifts.js` |
| Cowork | `reconciliation.bank_recon_api.export` | `bank_recon_service.export_size`（含已完成检查）/ `export` | `recon.js` |
| Cowork | `reconciliation.income_recon_api.export` | `income_recon_service.export_size` / `export` | `recon.js` |
| Cowork | `recon_center.api.export_vat` | `vat_check_service.export_size`（两边行数）/ `export` | `recon.js` |
| Cowork | `stocktake.api.export` | `stocktake.service.export_size`（含旧版任务检查）/ `export` | 只有实验脚本调 |
| Cowork | `stocktake.desk_api.api_stock_count_export` | 同上 | `stocktake.js` |
| Cowork | `work.api.export` | `work.api.export_size` / `export_file`（经 `_run` 翻译输入错误） | `work.js` |
| 共用 | `billing_www.export_records`（首页账单明细） | `export_records_size`（三张表各封顶）/ `export_records_file` | `home.js` |

- 接口名、参数、HTTP 方法都不变（小的照旧回文件，实验脚本 `scripts/*-lab-verify.py` 不受影响）；访问日志、Cowork 的 `no-store` 照旧。
- **没改**：复核工作簿 `roundtrip.api.export`（前端每次最多 100 张，本来就分批）；三个模板下载（盘点表模板、期初模板、商品导入模板，
  规模有限）；原件 / 凭证下载（不是导出）；`frontend/` 的 Vue 首页（`pearnly-app` 已转原生 Desk，不再使用）。控制面本身没有表格导出，
  `billing_api.admin_slip` 是单张凭证下载。

## 验证

- `tests/test_exports.py`（产品镜像内）：共用层 11 条（当场 / 阈值 / 排队带身份语言 / 无权限不出文件不建任务 / 后台以本人身份跑并推送 /
  文件 0600 / 失败原话与程序错误不外露 / 只有本人能查能下 / 下载时再查权限 / 过期拒绝并清理 / 只跑一次 / 伪造键出不了目录）；
  12 个入口逐个的成功（小的当场、大的排队→跑完→下载）、失败（小的当场报、大的推失败原因）、无权限（不生成、不建任务），并校验每个
  入口登记的计数与生成函数都能解析到真函数、收到的参数对。
- CI 同名闸门本地全过（架构、i18n、native desk、各 python 检查；三项需 3.10+ 的在镜像里跑）；`test_exports.py` 加进候选离线车道。
- 还缺：测试机（testerp / testcowork）上逐产品点一次导出（小的、超过阈值的、断网轮询）——上测试机前跟修复窗口对时间；
  站点上的 `Pearnly Export` 建表随 migrate。控制面（本机管理后台）没有导出入口，无需点验。
