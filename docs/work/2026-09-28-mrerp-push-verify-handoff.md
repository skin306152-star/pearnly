# 交接：MR.ERP 推送假成功（2026-09-28）

交出方：窗口「Take over Express stress test findings」（同时在做 Cowork 识别 / Express 推送修复，分支 `codex/cowork-review-reasons`）。
接手方：新窗口，只做本文件范围。背景见同分支 [Express 压测交接](2026-09-28-express-stress-handoff.md)「三」「五」。

## 用户口径（2026-09-28）

- 按类通用修复，不针对某公司 / 菜单号打补丁；有多种可能的，让人选一次、系统记住，选项失效就停下问人，不静默换、不假成功。
- **不动 MR.ERP 的任何数据和设置**；外部写入要用户明确授权。test01 账号只能有一个会话，需要 MR.ERP 页面时请「DMS产品视频脚本」窗口去抓，自己不要登录。
- 不改 DMS 代码、不部署 DMS。
- 做完在测试环境验证没问题后按「主线集成」合入 main；**生产部署由用户统一安排在最后一次做**（与 Cowork 修复一起），本窗口合入后不单独部署。

## 现象

Cowork 显示成功的 MR.ERP 单据进了没关联日记账的菜单，MR.ERP 网页看不到、不入账。

## 根因（已核实，基于 main `0a4d4427`）

1. `apps/pearnly_cowork/pearnly_cowork/push/mrerp/modules.py:36-65` 写死菜单：赊销 118、现金销售 125、采购 67、费用 453（照搬旧线上 `ec2e5c957e6b`）。
   MR.ERP 菜单号是各公司自己的（`menu/menuinsert.php:89` 取全局最大 +1），写死必错。
2. MR.ERP 导入页 `<select name=selmenu>` 按「公司 + 用户权限」现生成（`impaptran/formupload.php:128`，impartran / imparse 同 `:128`，
   `idlinkidmenu` 13 = 采购、5 = 赊销、4 = 现金销售）；`data-chklac=1` 表示**当前年度**已在 `tbl_linkaccount` 关联日记账（`:136-145`，按年度查 `:37`）。
   模板默认关联：采购 66/68/70/72/74、销售 116/117/119/120/122/124/126（`stderp2020.sql:2718-2732`），67/453/118/125 都不在。
   该公司（test01 · TEST2019）实际选项：采购 68、454；赊销 119；现金销售 126，全部已关联。
3. `importpc.php` 服务端不校验 `selmenu`（只有前端 `formupload.js:9` 拦）；没关联时单号前缀和日记账为空、仍回 `"1"`
   （impaptran `:116-120`、`:794-798`、`:1050`；impartran `:154`、`:864`、`:1107`；imparse `:187`、`:1295`、`:1573`；`tbl_acglh.idmenu` 可空）。
4. 成功判定：`push/mrerp/transport.py:314-316` 收到 `"1"` 即整批成功、不回读；`runner.py:154-157` 把「单号已存在」算成功
   （MR.ERP 查重 `importpc.php:147` 只在同一菜单 + 带前缀单号内）；`models.py:75-79` 记下的单号是自拼的（销项加 SI、采购不加），不是 MR.ERP 实际单号。

MR.ERP 源码（只读）：`/Users/skin/mrerp-source/src`；建库模板 `stderp2020.sql` 在其附近。

## 方案（用户已同意方向）

1. **菜单选择**：选定 MR.ERP 公司 + 年度后读三个导入页的选项，只用 `data-chklac=1` 的；某类只有一个合格选项自动用，多个时在连接 / 账套设置里让人选一次
   （一般货品 / 费用各推哪个），按「连接 + 年度 + 单据类型」记住（关联按年度存，`menu/menuupdate.php:118-191`）。
2. **每次推送核对**：`transport.py:239` 本来就先 GET 导入页，直接解析这次响应的选项核对「所选菜单仍在且已关联」；失效则该账套的票停下，
   列表明确提示「MR.ERP 菜单设置变了，请重新选择」（三语词条），重选后续推；不自动换菜单。
3. **回读才算成功**：导入后先 GET `allview.php?idmenu=N`（菜单写进会话，`sessionmenu.php:3-8`；showdata 不收 idmenu 参数），
   再 POST `component/showdata.php`，带 `searchdataval`、`showdata_numrows`（前端 30）、`showdata_pages=1`（缺分页参数会出错）。
   - 实际单号 = 菜单前缀 + 我方单号（`:798`，前缀可能为空、owner 可改）；showdata 模糊匹配（`showdata.php:25`、`:56`），
     **按「结尾完全等于我方单号」判定**，前缀从回读结果取来记下实际单号。
   - allview 页出现「กรุณากำหนดการเชื่อมโยง "สมุดรายวัน"」也不算成功（没关联的菜单用 showdata 照样查得到，回读单独查不出本次问题）。
   - 查不到 → 「结果不明，需人工核对」；「已存在」也要在正确菜单查到才算成功。
   - 回 `"2"`（逐行报告）的路径、`routing.py:184-198` 混合批次也要按组逐张回读。
4. 税务库查重「已存在于进项 / 销项税数据库」（`:179`）不在 `retry.py:44-47` 匹配里 → 转人工。
5. 并发：单号表无唯一索引、查重先查后插；上传文件按用户号存 `filesimport/impaptran/{idus}.xlsx`（`uploadexcel.php:11`），
   同一 MR.ERP 用户同时在浏览器导入会互相覆盖——在说明与连接提示里写清「推送账号不要同时人工导入」。
6. 已推进 67 / 453 / 118 / 125 的旧单（今天 690922-524602、690923-960576、690925-863020、690926-424358、690927-534899；
   09-26 的 690916-887770、690915-963946、690914-177791、690911-059566、453 下 690908-536693 等；销项 SI690923-841904 → 118、SI690924-093665 → 125）：
   列清单，Cowork 里改成「需人工核对」（原生迁移 / patch，不手工改库）；**MR.ERP 里清不清理由用户定，不要动**。
   换到正确菜单后这些票重推不会触发「已存在」，会重复入账——重推前必须由人确认。
7. 迁移记录登记为有意变化（旧线上同样写死）：`docs/work/cowork-backend/` 相应来源映射。

## 离线测试夹具

`/Users/skin/Downloads/pearnly-cowork-video/mrerp-fixtures/`：三个导入页（1/2/3）、68 列表空 / 有数据（4a/4b/4c）、67 未关联提示（5a）、67 下查到旧单（5b）。
无 PHPSESSID / cookie / token，但 1/2/3 含内部用户号 idus=15，1/2/3/4a/4c/5a/5b 含 test01，4c、5b 含真实供应商名；
1、2、3、4a、5a 被注入过 `pearnly-compact-list` 模糊样式（不是服务器原样）。**入库前打码、删注入样式。**

## 与 Cowork 修复窗口的共享点（避免冲突）

Cowork 修复窗口会改：推送失败原因的显示（`history/push_state.py`、`public/desk/intake*.js`）、原因码词条（`pearnly_cowork/i18n/messages.tsv`）、
退回后按原因修改再推（`push/retry.py`、`push/lifecycle.py` 的转人工分类）。本窗口：
- 只改 `push/mrerp/`、连接设置里 MR.ERP 菜单选择相关、自己的测试与词条行；
- 新的原因码（菜单失效、结果不明等）**只追加词条行**，显示走 Cowork 窗口建立的「原因码 → 词条」机制（它先合入 main，本窗口变基后接上）；
- `retry.py` 的匹配规则如需改，先和 Cowork 窗口对一下再动。

## 验证与交付

- 离线：解析三个导入页选项、选项失效、回读查到 / 查不到 / 前缀改写 / 未关联提示、`"2"` 路径、旧单转人工的补丁。
- 本仓检查：`python3 scripts/check-architecture.py`、受影响的 `tests/test_cowork_*mrerp*`；不跑真推送。
- 真实 MR.ERP 验证只在用户授权后、由「DMS产品视频脚本」窗口配合；不在 MR.ERP 里新建或删除单据，除非用户明确同意。
- 合入 main 后告诉 Cowork 修复窗口与用户；生产部署由用户最后统一安排。
