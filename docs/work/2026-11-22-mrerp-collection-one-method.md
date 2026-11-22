# MR.ERP 收款一笔只收一种钱（CC 引导卡 max_rows=1）——实测记录（2026-11-22）

依据：主会话 11-17 定「账本是 MR.ERP 时，引导卡 / 网页 / LINE 收款方式那一步直接只能选一种（转账 + 折扣仍可），服务端
`collection.mrerp_one_method` 留作兜底」；11-22 派本窗口做完并真走一遍。

## 改动（已进 main 9efd90263）

- `apps/pearnly_erp_portal/pearnly_erp_portal/collection/guide.py` 的 `method_options`：`service.methods` 回 `single`（账本是 MR.ERP）时，
  选项回 `max_rows=1`，折扣选项带 `free`（不占名额）；按码取（引擎核答案）也带。引擎超出回 `too_many_rows`，CC 前端（main 上已有，
  `guide-inputs.ts`）满一行就锁其余不带 `free` 的行。`method_check` 的 `ONE_METHOD` 不动，留作兜底。Express 账本不变。文案未改。
- 测试：`tests/test_erp_collection_guide.py` 加两条（MR.ERP 回 `max_rows=1`、折扣 `free`、Express 不限；走真引擎答两行钱被拒
  `too_many_rows`），先红后绿。

## 两个缺口核对结果（三处入口都已有，本次未改）

| 缺口 | CC 引导卡 | 网页「收款 → 新建」 | LINE 收款 |
| --- | --- | --- | --- |
| 收款导入菜单不止一个时问一次记住 | 账套那一步出「账套 · 菜单」（`book_options` / `book_apply` → `service.choose_menu`） | `collection-new.js` 的菜单下拉（`api_collection_choose_menu`） | `line_collection._ask_menu` |
| 「正在读取」按账本出字 | 发票步 `service.reading_text`；方式步走 `collection.mrerp_reading` | 同一服务码 | `service.reading_text` |
| 一笔只收一种钱 | 本次补 `max_rows=1` | 已有（`methods.single`，单个下拉） | 已有（`found.single`） |

对话模式给模型的读回说明也已按账本换字（`cc_tools._named` / `MRERP_READING_NOTE`）。

## 实测（本机实验栈 + MR.ERP 测试账套）

环境：本机实验栈 `pearnly-onemethod-lab`（回归实验室编排，源码 = main 9efd90263），站点 `erp.regression.localhost`；
`MRERP_LEDGER_ENABLED` 只在驱动脚本那个进程里打补丁打开（产品代码与线上开关未动）；MR.ERP 线上站点的专属测试账号、测试账套（账套键 6:1）。
走的是引导卡的服务端（`assistant.guide.run` 的 start / answer / search / confirm，与页面同一组函数），没有用浏览器点卡片界面；
前端「满一行锁其余行」由 main 上的 `tests/test_cc_guide_inputs.py` 守着。

准备：在测试账套写两张赊销发票（ERP 销售单经产品导入）：**SI691122-092176**（1,070.00，ERP SD-13866）、**SI691122-279344**（535.00，ERP SD-13867），
客户「หจก. ทดสอบรับชำระ OM1122」（合成税号）。公司首次推单时按产品「选一次」记下默认银行科目 1112-01。

| 检查项 | 结果 |
| --- | --- |
| 「正在读取」按 MR.ERP 出字 | 通过：选客户后发票题下事实行「Reading from MR.ERP」，`wait` 为真；读回后列出两张发票 |
| 收款方式只能填一行 | 通过：方式题带 `max_rows: 1` |
| 折扣不占名额 | 通过：折扣选项 `discount:4110-20` 带 `free: true` |
| 超行被拒 | 通过：答「现金 570 + 转账 1112-01 500」→ `refused.methods = too_many_rows` |
| 转账 + 折扣可 | 通过：答「转账 1112-01 1000 + 折扣 70」→ 接受，进到凭证题 |
| 菜单问一次记住 | 没测到：测试账套只有一个已关联日记账的收款导入菜单（169），产品不需要问（`menu_needed` 为空），这一步在本账套上不会出现；由单测 `test_the_receipt_menu_is_asked_with_the_book_and_remembered` 守着 |
| 真写 1 | 通过：SI691122-092176 收 1,070（现金 1,000 + 折扣 70）→ ERP 收款 **COL261122-EC0E** → MR.ERP 收款单 **FRCOL261122-EC0E**，状态 written、无读回差异 |
| 真写 2 | 通过：SI691122-279344 收 535（现金）→ ERP 收款 **COL261122-311A** → MR.ERP 收款单 **FRCOL261122-311A**，written |
| 写后读回 | 通过：第二张卡开时这位客户只剩 SI691122-279344；两张收完后读回未收发票为空 |

MR.ERP 测试账号的测试账套 上本次留下的单（专属测试账套，可留）：SI691122-092176、SI691122-279344、FRCOL261122-EC0E、FRCOL261122-311A。
实验栈用完已连卷删除。

真机记录 | erp.cc.tool.erp_collection_guide | 2026-11-22 | 9efd90263 | 本机实验栈 pearnly-onemethod-lab + MR.ERP 测试账套 | 通过 | MR.ERP 账本收款引导卡：读取中说「Reading from MR.ERP」；方式题 max_rows=1、折扣 free；两行钱被拒 too_many_rows；转账+折扣接受；真写 FRCOL261122-EC0E（现金 1,000 + 折扣 70）、FRCOL261122-311A（现金 535）；菜单问一次在测试账套不出现（只一个已关联菜单）没测到
