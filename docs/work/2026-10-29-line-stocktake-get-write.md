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

### 流程用例（第 2 步，过 10-28 起的流程覆盖闸）

改到的入口文件按主会话 10-29 定补流程用例（不登记 not_flows 糊过去）：`cowork.recon.tasks`、`cowork.recon.presets`、
`cowork.stocktake.list`、`erp.document.express`（`scripts/flows/`，共用片段 `desk_list.py`）；`identity.py` 没有页面（开户脚本 / 后台调用、
登录回调要真身份服务），登记 `tests/flows/not_flows.toml`。Cowork 加两套数据（`datasets.py new|old cowork`：
`cowork-new` 开户后什么都没建，`cowork-old` 两份上传导入的盘点、银行与收入预设各一条），`lab_init.py` 加这两个站点，
运行器按流程的 `product` 取数据与岗位账号。
回归实验室（挂本分支源码）4 条 × 两套数据 × 电脑 / 手机共 16 格全过：第一轮 13/16，手机宽度 3 格因页内按钮被框架收进页头「…」菜单没点到
（old / 手机那格 Express「没有按钮」是假通过），流程改成先展开菜单再点（`desk_list.ACTION`）后复跑 8 格全过。
结果：`~/pearnly-devtools/get-write-rollback/flows-1.json`、`flows-2.json`、`flows-3.json`。
没测到：收银日汇总的 Express 弹窗（实验室没有日汇总单据）。

## 扫描器补盲区：当参数传出去的函数（分支 `pearnly/get-writes-scanner-callables`，基线 main c49b5743）

验收窗口 10-29 发现：ERP 和 Cowork 的 `work/api.py` 共 92 个接口都写成 `_run(service.xxx, …)`，`check-get-writes.py` 只追「被调用的函数」，
不追「当参数传出去的函数」，整片报 PASS、豁免表里也没有。例：Cowork「任务管理」页一打开 GET `work.api.board`，没有归属时经
`boards.open_board` 建归属和默认看板再手动 commit。

- 扫描器：调用的参数（位置参数、关键字参数）里是本仓函数的（`f`、`m.f`、字面量「包.模块.函数」）也按调用往下追，lambda 里的调用照旧算；
  参数和函数里赋值的名字是局部变量，不当成同名的模块函数（分页参数 `start` 不是 `recon_center.api.start()`）。
  测试加两条：写库函数当位置参数 / 关键字参数 / 字符串路径传出去都要被拦；参数同名不误报。用旧扫描器跑新测试失败。
- 新查出 17 个，按类处理：
  - 白写被回滚 3 个（Cowork `work.api.boards_list` / `card` / `notifications`）：读路径经 `scope.default_workspace` 建归属、不提交。
    改成只读的 `scope.existing_workspace`：看板列表没有归属回空、通知回空的同一形状；卡片 / 列表 / 镜像 / 评论 / 恢复看板这些
    「这条记录属于本站点归属吗」的核对一并改用只读版（没有归属就不会有记录，结果不变）。
  - GET 里手动 commit 14 个（ERP / Cowork `work.api` 的 board / archived / board_lists / board_members / rules / search 经
    `boards.open_board` 建默认看板与列表再 commit；ERP 少一个 card 路径；Cowork `folder_intake.api.options`、`push.application.api.choices`），
    登记进基线「待改」，随第 3 步一起改。
- 豁免表 26 → 40 条（新增的 14 条都是手动 commit 类）。
- 实测（回归实验室 Cowork 新开户站点，一次性容器挂源码，事务里停用归属、禁止 commit、最后回滚）：
  本分支三个读接口写库 0 次、看板列表回 `[]`、通知回 `{"rows": [], "unread": 0}`；同一脚本跑 main c49b5743：看板列表写库 1 次、事务里多出一份归属。

## 第 3 步之一：任务管理 open_board 那 12 个（分支 `pearnly/get-writes-open-board`，叠在扫描器补盲区分支上）

ERP / Cowork `work.api` 的 board / archived / board_lists / board_members / rules / search 都经 `boards.open_board`：没指定看板就
`default_board` 兜底建默认看板、`ensure_lists` 兜底建默认三列与标签，再在 GET 里手动 commit。默认看板 / 列表平时在迁移时建好
（`ensure_on_migrate`；新开户写完功能清单后跑一次初始化，1babc94a），兜底只在半开户等少数情况用得上。

- `open_board` 改成**只读**（只找已有的归属 / 默认看板，核对权限，不建）；读写接口都用它。
- 新增 `ready_board`：归属、默认看板、默认三列缺了先补上，再 `open_board`；只给 `service.board` 用。
- `work.api.board`（整块看板，页面一打开、收到变更时调的那一个）改 `methods=["POST"]`，两个产品的 `work.js` 一并改 POST。
- `default_board` 去掉手动 commit（迁移、POST 请求自己提交）。
- 基线 40 → 28 条（剩导出排队 13、手动 commit 14、按协议必须 GET 1）。
- 验证：
  - 原生脚本 `test-cowork-work-native.py` / `test-cowork-work-boards-native.py` / `test-erp-work-native.py` / `test-erp-work-boards-native.py`
    在回归实验室站点上全过（整段事务、任何提交判失败、最后回滚）；`tests/test_erp_work.py`、`test_cowork_work_boards.py` 等通过。
  - 流程用例 `erp.work.board`、`cowork.work.board`（`scripts/flows/work_board.py`）：两套数据 × 电脑 / 手机共 8 格全过
    （页面上的列 = 库里默认看板的列表、整块看板走 POST、打开后看板与列表数不变）；第一轮 erp old / 电脑那格是建 old 数据时与验收窗口
    同库并发报错（没走到页面），单格复跑通过。跑法：回归实验室被验收窗口挂着别的工作树，起了一个临时网页容器挂本分支源码、共用实验室的库与 Redis
    （`REGRESSION_LAB_CONTAINER` / `REGRESSION_LAB_PORT`），跑完已删。
  - 兜底：事务里删掉默认看板与列表后，读路径报「看板不存在」、写库 0 次；`service.board()` 补建出默认看板与三列（两个产品都测，最后回滚）。

## 同页顺带修：保存后抬头不刷新

手机页保存后，抬头「已盘点 x / y」不变，要退出重进这份盘点才更新（抬头只在打开盘点时取一次）。主会话 10-29 定同一分支修掉：
查码接口 `api_line_stocktake_lookup` 回带这份盘点此刻的抬头（`counts.get_count`），页面每次查码都用它刷新——保存、撤销后
页面本来就再查一次码，扫到新商品登记新行时「共 y 行」也跟着变。`tests/test-line-stocktake.cjs` 断言保存后计数加 1、
查码没带的抬头字段（标题）保留；旧页面跑这条断言失败。

## 本机测试资源

样板 `qerp.localhost`：CO-00046「SCANLAB2 空仓测试」、WH-00047、PRD-00048、STC-00049 / 52 / 53、管理员的测试 LINE 绑定 `U-scanlab2-test`、
测试会话；临时容器 `pearnly-getwrites-preview`（127.0.0.1:8199）。收尾时删除。
第 2 步：CO-00056「FRESHLAB 新开户」、WH-00057、TERM-00058、SHIFT-2026-00059；实测脚本 `~/pearnly-devtools/get-write-rollback/`。
