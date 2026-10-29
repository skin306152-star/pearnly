# 手机盘点空仓保存 404：GET 接口写库被回滚（2026-10-29 热修）

分支 `pearnly/line-stocktake-post`，基线 origin/main `8fd0fa4d`（生产同版）。

## 现象与根因

逐入口排查窗口在 main 8fd0fa4d 上发现：手机盘点页 `/stocktake`（LINE 里打开那页），在空仓（没有期初）的盘点里，
认出商品、录入卡也出来了，点「保存」时 `api_line_stocktake_add` 回 404，实盘记不上；有存量的仓正常。

`inventory/line_mobile.py` 的 `api_line_stocktake_lookup` 是 GET 接口。快照里没有这件商品时，它当场 `counts.ensure_item` 登记一行；
Frappe 不提交 GET 请求，这一行被回滚。保存带着这一行的编号去找 → `DoesNotExistError`（404「แถวนี้ไม่ได้อยู่ในการตรวจนับนี้」）。
引入：53a725f6（09-22）。有存量的仓快照里本来就有这一行，所以不受影响。

## 修法

1. `api_line_stocktake_lookup` 改 `@frappe.whitelist(allow_guest=True, methods=["POST"])`；手机页 `call(..., true)` 走 POST（票据在请求体里）。
   不靠在 GET 里手动 commit。
2. 调用方核对：手机页 `www/stocktake.html`（唯一的浏览器调用方）；`tests/test-line-stocktake.cjs`（GET 编码那段改用仍是 GET 的
   `api_line_stocktake_task`，连续盘点那段改查请求体）；`scripts/erp-pos-lab-stock-count-verify.py` 直接调 Python 函数，不受请求方式影响；
   CC 桌面版、LIFF 其他页面都不调它。
3. 防再犯：`scripts/check-get-writes.py`（接进 development.yml「Endpoints reachable by GET do not write」）。
   沿着 GET 可达的 `@frappe.whitelist` 接口调到的本仓函数往下追（最多 6 层），找到写库动作（insert / save / set_value / 写语句 SQL /
   删除 / enqueue / commit / defaults 写入等）就拦；openpyxl / PIL 写内存不算。确属预期的登记 `scripts/get-writes-exempt.tsv`
   （三列：函数、接口|经过、原因），登记了却不再命中的也报错（基线只许缩小）。测试 `tests/test_check_get_writes.py`
   （合成应用：经辅助函数写库被拦、POST 不拦、写内存工作簿不拦、「经过」豁免截断；把真代码的查码改回 GET 必须被拦）。

## 全仓排查（GET 可达却写库）

第一版扫出 65 个；去掉误报（openpyxl / PIL 写内存）与两条「经过」（`warehouses.resolve` 只在 `create=True` 时新建，应用里全是 False；
`execution.pool.release_db` 的 commit 只为在等外部服务前放开连接）后，存量 44 条全部登记进基线：

| 类别 | 条数 | 说明 |
|---|---|---|
| 导出排队 `exports.service.deliver` | 13 | GET 里建导出任务并手动 commit 才生效；改 POST 要连 `p.export_file` 等调用方一起改 |
| 收银「顺手建默认收银台」`terminals.ensure_default` | 8 | 读接口里建的会被回滚，同这次的缺陷 |
| Cowork「顺手建站点工作空间」`ensure_site_workspace` | 6 | 同上 |
| GET 里手动 commit | 10 | 计费 `service_token`、Cowork 公司详情 / 月份、对账任务恢复、助手 events 等 |
| 其他 GET 写库 | 6 | `document_express` 的 set_value、助手 push_key、计费钱包、`bind_identity` 等 |
| 按协议必须 GET | 1 | 身份服务登录回调 `identity.callback`（浏览器重定向回来，state 一次性校验） |

「待改」的 43 条按类别整改（牵涉 ERP / Cowork / 控制面 / 共用层和大量调用方，部署要四个产品），范围由主会话定；
整改一条删一条，新增的一律拦。

## 第 2 步：GET 里顺手建 / 写、会被回滚的 17 条（分支 `pearnly/get-write-rollback`，叠在第 1 步之上）

先在本机样板上按新开户实测（ERP `qerp.localhost`，公司 CO-00056「FRESHLAB 新开户」、一个仓、没有收银台）：
8 个收银读接口都正常返回空结果，收银台数仍是 0（读接口里建的那台被回滚）；开班（写路径）才建出默认收银台，之后读接口照常读到。
即**用户看得见的失败没有**，但每次打开收银台都白建一台、白锁一次。其余几条同理：结果对，写的东西被回滚。

| 接口（条数） | 原来 GET 里写的 | 改法 |
|---|---|---|
| ERP 收银 8 个读接口（`api_current_shift` / `api_counter_products` / `api_counter_lookup` / `api_list_shifts` / `api_count_shifts` / `api_list_sales` / `api_sale_total` / `api_current_sales`） | `terminals.resolve` → `ensure_default` 建默认收银台 | 新增只读 `terminals.default_of` / `resolve_read`，读接口（`shifts.current_shift`、`_shift_filters`、`counter.warehouse_for`）用它；没有收银台时班次为空、商品区按公司唯一仓取（同建收银台时的规则）。建收银台只在开班、卖货（`resolve`） |
| Cowork 6 个读接口（对账任务列表、对账预设、盘点列表 / 合计 / 模板、文件夹任务列表） | `default_workspace` → `ensure_site_workspace` 建站点归属 | 新增只读 `scope.existing_workspace`；还没有归属时返回空结果（归属在安装 / 迁移与写路径上建） |
| Cowork `pending_api.account_sets`（待选账套） | `service.books` 给按名称建的主档补存税号 | 新增只读 `service.listed_books` / `tasks.listed_books_of`：要补的税号只在本次结果里用上、不存；打开主档页（`books`）照常补存 |
| 控制面 `billing_api.admin_summary` | 静态上经 `wallet_of(create=False)` | 新增只读 `billing.existing_wallet`，问余额（`gate`）与 `wallet_summary` 用它；`wallet_of` 只剩加锁 / 建钱包一条路 |
| ERP `express.api.document_express`（单据上的 Express 状态） | 转人工要人选时 `push.document` → 日汇总重算小票税额、存合计 | 改 `methods=["POST"]`，单据页 `transactions.js` 的 `p.express` 用 POST（唯一调用方） |
| 共用层 `identity.bind_identity` | 建身份绑定（本来就手动 commit） | 改 `methods=["POST"]`；调用方都是 Python 直接调（开户、样板配置、验收脚本），不受影响 |

`assistant.api.push_key`、`recon_center.sheets.load` 复核后本来就在 GET 里手动 commit，归入「GET 里手动 commit」那类（第 3 步）。
整改后豁免表 44 → 26 条：导出排队 13、GET 里手动 commit 12、按协议必须 GET 1（`identity.callback`）。

牵涉的产品：ERP（`pearnly_erp_portal`）、Cowork（`pearnly_cowork`）、控制面（`pearnly_control`）、共用层（`pearnly_core`，只改接口方法）。
部署用 `scripts/production/deploy.sh` 一次全激活。

### 验证（第 2 步）

- 样板实测（一次性容器只读挂本分支源码、同一个样板库，事务最后整体回滚，记 `frappe.db.transaction_writes`）：
  ERP 收银 8 个读接口在「有收银台」「这家公司一台收银台都没有」两种情况下写库次数都是 0、收银台数仍是 0；
  同一脚本跑旧代码（样板 web，main）：没有收银台时第一次读接口写库 2 次、事务里多出一台收银台。
- 单元：`tests/test_control_billing_gate.py`（问余额只走 `existing_wallet`、不碰 `wallet_of`）、`test_cowork_folder_task_batch.py`
  等受影响测试；本地完整闸门（同 development.yml）全过；`scripts/check-get-writes.py` PASS（接口豁免 26 条）。
- 没验证：Cowork「站点还没有归属」只在新站点安装前那一刻出现，样板站点都已有归属，按代码与单元测试核对；
  `document_express` 改 POST 后单据页 Express 弹窗没在浏览器里点（调用方只有这一处，`p.call` 第三参给 POST）。

## 验证

- 单元 / 节点：`tests/test_check_get_writes.py` 4 项、`tests/test-line-stocktake.cjs` 通过；`scripts/check-get-writes.py` PASS。
- 端到端（本机样板 `qerp.localhost`，HTTP 与无头 Chrome `--mute-audio`、跑完即退）：空仓（没有期初的仓）盘点、带唯一条码的商品、
  管理员的测试 LINE 绑定与票据。
  - 旧代码（样板 web，main 8fd0fa4d）：GET 查码 200 返回行 STC-ITEM-00050 → 保存 404「แถวนี้ไม่ได้อยู่ในการตรวจนับนี้」，实盘「—」。
  - 新代码（临时 web 容器挂本分支源码、同一个样板库）：GET 查码 403；POST 查码 → 保存 200（STC-ENTRY-00051 数量 2）；
    手机页上输码 → 查询 → 数量 3 → 保存：提示「已保存」，实盘数量 3，记录行出现。
  - 截图：`~/pearnly-devtools/line-stocktake-post/旧代码-空仓保存404.png`、`新代码-空仓保存成功.png`。
- 没验证：真手机 LINE / LIFF 里扫码保存（逐入口窗口的 flows 实验栈另行验收）。

## 同页顺带修：保存后抬头不刷新

手机页保存后，抬头「已盘点 x / y」不变，要退出重进这份盘点才更新（抬头只在打开盘点时取一次）。主会话 10-29 定同一分支修掉：
查码接口 `api_line_stocktake_lookup` 回带这份盘点此刻的抬头（`counts.get_count`），页面每次查码都用它刷新——保存、撤销后
页面本来就再查一次码，扫到新商品登记新行时「共 y 行」也跟着变。`tests/test-line-stocktake.cjs` 断言保存后计数加 1、
查码没带的抬头字段（标题）保留；旧页面跑这条断言失败。

## 本机测试资源

样板 `qerp.localhost`：CO-00046「SCANLAB2 空仓测试」、WH-00047、PRD-00048、STC-00049 / 52 / 53、管理员的测试 LINE 绑定 `U-scanlab2-test`、
测试会话；临时容器 `pearnly-getwrites-preview`（127.0.0.1:8199）。收尾时删除。
第 2 步：CO-00056「FRESHLAB 新开户」、WH-00057、TERM-00058、SHIFT-2026-00059；实测脚本 `~/pearnly-devtools/get-write-rollback/`。
