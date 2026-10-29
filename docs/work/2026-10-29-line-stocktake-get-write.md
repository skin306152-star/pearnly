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
