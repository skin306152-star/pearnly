# MR.ERP 推送假成功修复（2026-09-28）

分支 `codex/mrerp-push-verify`，基线 `0a4d4427`（= origin/main，= 生产 Cowork 源码）。
交接见 `codex/express-stress-0928` 分支 `docs/work/2026-09-28-mrerp-push-verify-handoff.md`；背景见同分支压测交接「三」「五」。

## 现象与根因（已核实，MR.ERP 源码只读 `/Users/skin/mrerp-source/src`）

Cowork 显示成功的 MR.ERP 单据进了没关联日记账的菜单：没有单号前缀、不入账、MR.ERP 网页列表看不到。

1. `push/mrerp/modules.py` 写死菜单 赊销 118 / 现金销售 125 / 采购 67 / 费用 453（照搬旧线上 `ec2e5c957e6b`）。
   MR.ERP 菜单号是各公司自己的（`menu/menuinsert.php:89` 取全局最大 +1）。
2. 导入页 `<select name=selmenu>` 按「公司 + 用户权限」现生成（`imp*/formupload.php:128`），`data-chklac=1` = 当前年度已在
   `tbl_linkaccount` 关联日记账（按年度查）。test01 · TEST2019 实际：采购 68、454；赊销 119；现金销售 126，全部已关联；67 / 453 / 118 / 125 都不在。
3. `importpc.php` 不校验 selmenu；没关联时单号前缀（`tbl_menu.codes`）与日记账为空，仍回 `"1"`。
4. `transport.py` 收 `"1"` 即整批成功、不回读；`runner.py` 把「单号已存在」算成功；`models.py` 记的单号是自拼的（销项加 SI）。

## 改了什么

| 位置 | 内容 |
|---|---|
| `targets/mrerp_menus.py`（新，纯函数） | 解析导入页菜单选项（`idmenu`、名称、是否已关联）；判定这次用哪个：选过的以选过的为准、失效就停（`ERR_MRERP_MENU_CHANGED`），没选过时只有一个已关联就用它，多个要人选（`ERR_MRERP_MENU_CHOICE_REQUIRED`），一个都没有（`ERR_MRERP_MENU_NOT_LINKED`），页上没有菜单下拉（`ERR_MRERP_MENU_UNREADABLE`）。按「连接 + 账套 + 单据类型」记住所选；只能选最近读到的已关联选项。 |
| `push/mrerp/readback.py`（新，纯函数） | 列表页是否提示「กรุณากำหนดการเชื่อมโยง "สมุดรายวัน"」/ 未完成设置；解析 `showdata` 行；按「以我方单号结尾、前面只多一个菜单前缀」找这张单，认不准（多行）当没找到。 |
| `push/mrerp/modules.py` | 交易单据不再有 selmenu / 回读菜单号，只留端点、模板、回读列表模块。 |
| `push/mrerp/transport.py` | 选账套后打开导入页读选项并判定菜单，定不下来就不建主档、不上传（转人工，原因码原样）；主档自建后、上传前那次必需的 formupload GET 再核一次；导入受理（`"1"` 或 report 空备注 / 「已存在」）后 GET `allview.php?idmenu=所选` + POST `component/showdata.php`（`searchdataval`、`showdata_numrows=30`、`showdata_pages=1`）逐张回读，查到才成功并记 MR.ERP 实际单号；查不到 `ERR_MRERP_READBACK_NOT_FOUND`、列表提示未关联 `ERR_MRERP_READBACK_NOT_LINKED`，都是结果不明。 |
| `push/mrerp/models.py`、`routing.py`、`runner.py` | 结果多一类 `unknown`（混批合并也带上）；删自拼单号 `bill_no_for`；runner：`unknown` → 账本 `mark_unknown`（转人工 + 结果不明，保留写者锁、不自动重推，与导入确认发出后异常同一处理），「已存在」不再单靠文字判成功；这次读到的菜单选项存回连接。 |
| `Cowork ERP Connection` 两个隐藏字段 | `mrerp_menus`（所选）、`mrerp_menu_options`（最近读到的选项 / 读取失败原因）。与连接配置分开：不升连接版本，正在推的票照常。 |
| `connections/service.py`、`api.py`、`mrerp_menu_reader.py`（新） | RPC `mrerp_menu`（视图）、`choose_mrerp_menu`（POST）、`request_mrerp_menu`（POST，后台任务登录 → 进账套 → 打开三个导入页只读，存选项）；权限同推送（可用这条连接的人）。与推送 / 目录采集共用账号锁。 |
| `push/application/service.py` | 准备 MR.ERP 推送时把这份账套选过的菜单冻结进载荷（`_mrerp_menus`）。 |
| 网页 `public/desk/targets.js`、`intake-upload.js` | 提交推 MR.ERP 前（与存货科目组同一位置，一行钩子，已与 Cowork 修复窗口约定）：这张票方向会用到的单据类型里有要选的，先重读一次导入页，再用原生弹窗一次选齐；关掉弹窗 = 这份账套的票这次不推。读不到菜单不在前端拦（推送时服务端核对并给原因）。 |
| 补丁 `patches/v0_0_7_mrerp_legacy_menu_review.py` | 修复前记成功的 MR.ERP 推送记录全部改成「待人工核对 + 结果不明」（原因 `ERR_MRERP_LEGACY_MENU_UNVERIFIED`），票据列表同步；回执、单号原样保留；逐条打印清单。结果不明占着活动键 → 同一张票不能直接重推（换到正确菜单后不会触发「已存在」，会重复入账）。**MR.ERP 里的数据不动**。 |
| 词条 `i18n/messages.tsv` | 7 个原因码 `intake.error.<码>` + 选择弹窗 7 条，四语；只追加行（显示机制归 Cowork 修复窗口）。 |
| 夹具 `tests/fixtures/mrerp/` | 三个导入页、68 列表 / 回读、67 未关联提示 / 无前缀旧单；已打码（见其 README）。 |

**有意变化**（相对旧线上 `ec2e5c957e6b`，旧线上同样写死、同样假成功）：菜单按导入页 + 人选；成功要回读；「已存在」要回读；
成功单号是 MR.ERP 实际单号（采购如 `PG690922-524602`，旧版采购记无前缀我方号、销项记 `SI`+号）。
旧迁移记录 `2026-09-26-cowork-frontend.md` 第 3 条已加指向。

## 旧错单（交接里列的，Cowork 补丁一并转人工；MR.ERP 里清不清理由用户定）

采购：690922-524602、690923-960576、690925-863020、690926-424358、690927-534899；09-26 的 690916-887770、690915-963946、
690914-177791、690911-059566；453 下 690908-536693 等。销项：SI690923-841904 → 118、SI690924-093665 → 125。
补丁按「修复前所有 MR.ERP 成功记录」处理（Cowork 里无从分辨哪张落对了），迁移时打印的清单为准。

## 已知限制

- **结果不明目前没有人工解除入口**（存量缺口，不是本次引入）：回读查不到、旧错单转人工后，同一张票不能重推，
  这份账套 / 连接的写者锁按「结果不明」保留，后面的 MR.ERP 票会等锁。「已在 ERP 核对：已入账 / 未入账」的解除入口
  **由 Cowork 修复窗口承接**（分支 `codex/cowork-review-reasons`，复核页标记，Express 与 MR.ERP 共用），它合入 main 后这批旧单即可逐张处理。
- 回读只在所选菜单里按单号查；同一菜单里有人手工录了「别的前缀 + 同号」会认不准 → 结果不明（宁可问人）。
- 某类单据换菜单（人重选）后，已经转人工的票要人重推；排队中的票用准备时冻结的选择，失效就停下。
- MR.ERP 上传文件按用户号存 `filesimport/imp*/{idus}.xlsx`：**推送账号不要同时在浏览器人工导入**（会互相覆盖）。
- 没有端到端真推验证（需用户授权、由「DMS产品视频脚本」窗口配合 test01 单会话；不在 MR.ERP 新建 / 删除单据，除非用户同意）。

## 验证

- 离线（锁定运行镜像 `pearnly-ci:runtime-067596c27f44-df29939b831c`，`--network none`，挂本工作树源码）：
  `test_cowork_mrerp_push` 26（含菜单：要选 / 失效不换 / 未关联 / 读不到 / 主档后失效；回读：`"1"` 查不到 = 不明、列表未关联 = 不明、
  `"2"` 行回读、已存在查到 / 查不到）、`test_cowork_mrerp_menus` 14（夹具解析、选择记忆、回读判定、旧单补丁）；
  受影响的 `test_cowork_connections` 9、`line_intake` 26、`mrerp_collector` 12、`mrerp_catalog` 8、`no_company_scope` 7、
  `push_lifecycle` 29、`push_native_contract` 12、`push_runtime_drift` 11、`push_contract` 19、`push_account` 6、
  `stock_acc_group_choice` 8、`targets` 39、`push_purity` 2 通过（`push_purity` 允许名单加标准库 `html`，只用 `html.unescape`）。
- `scripts/check-architecture.py` PASS；Development checks 各步本地跑过（本机 Python 3.9 跑不了的 4 个在镜像 Python 3.14 下通过）；
  `i18n-build.py --check` PASS。
- 分支 CI：Development checks run 36402939592（`architecture-and-offline` success，提交 `d912918c`）。
- 测试环境、真推：未做，见下方进度。

## 进度

- [x] 实现 + 离线测试 + 本地闸门
- [x] 分支 CI（run 36402939592 通过）
- [x] 合入 main：快进到 `d912918c` 并推送，远端回读一致；任务分支本地 / 远端已删
- [ ] 测试环境 / 真实 MR.ERP 验证（需用户授权）
- [ ] 生产部署（用户最后统一安排，本窗口不单独部署）
