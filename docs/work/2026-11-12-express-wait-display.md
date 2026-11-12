# 各处显示「在等 Express 关闭」（2026-11-12）

分支 `pearnly/express-wait-display`，基于 `pearnly/express-busy-probe`（`3f3e9e8ac`）。主会话 11-12 批准方案后施工。进行中，未合入。

## 做法

- 不加字段、不另存：真相是排队中的写账记录上的原因码 `ACCOUNT_BUSY_LOCKED` 与上次回报时间（上一支留下的）。读的时候算，上次回报超过 90 秒不再算（`ack_rules.waiting_for_express`）；被领走、写成、转人工后自然消失。不抄进票 / 单据的失败原因栏。
- 共用查询：共用通道 `pearnly_core.cc_desktop.jobs.express_wait(name)`、`express_wait_many(names)`、`book_express_wait(book_key)`；Cowork 账本 `pearnly_cowork.push.express_wait.many / one / account / mark`。

## 改已有文案（用户 11-12：「文案统一用「写入」吧」，主会话转）

两个词条 `pearnly_cowork:intake.error.ACCOUNT_BUSY_LOCKED`、`pearnly_erp_portal:express.error.ACCOUNT_BUSY_LOCKED`，四语相同：

| 语言 | 改前 | 改后 |
|---|---|---|
| zh | Express 正在使用此账套，关闭后将自动推送 | Express 正在使用此账套，关闭后将自动写入 |
| en | Express has this account set open. It is sent automatically once Express is closed | Express has this account set open. It is written automatically once Express is closed |
| th | Express กำลังเปิดชุดบัญชีนี้อยู่ ระบบจะส่งให้อัตโนมัติเมื่อปิด Express | Express กำลังเปิดชุดบัญชีนี้อยู่ ระบบจะบันทึกให้อัตโนมัติเมื่อปิด Express |
| ja | Express でこの会計データが開かれています。Express を閉じると自動で送信されます | Express でこの会計データが開かれています。Express を閉じると自動で書き込まれます |

英 / 泰 / 日按现有词条里「写入」的译法对齐：en written（`Written At`、`cannot be written to Express`）、th บันทึก（`บันทึกลง Express`、`เวลาบันทึก`）、ja 書き込み（`書き込み日時`、`Express への書き込み`）。

没改、列给主会话的相近句子：
- CC 桌面版（要随新版发，`companion/src/companion/i18n.py`）：`busy_body`「账套正被 Express 占用。关闭正在使用该账套的 Express 后点击「重试」，或点击「终止推送」。」、`busy_abort`「终止推送」、`st_waiting_lock`「正在等待 Express 释放账套…」。
- 同一组里的另一句（Cowork 与 ERP 词条表）：「另一台电脑正在写入此账套，完成后将自动推送」。
- 全仓同义文案清单另出，范围由用户定。

## 各处显示（主会话 11-12 定的口径）

| 位置 | 做法 | 组件 | 提交 |
|---|---|---|---|
| Cowork 录入列表 | 接口给这一页在等的行补只读字段 `push_waiting`；状态仍「录入中」，悬浮说明是这句 | 官方状态药丸的 title（失败原因同一位置） | `4ebd46423` |
| Cowork 录入详情 | 顶部一行提示 | 官方 `form-message blue` | `4ebd46423` |
| Cowork 的 CC 对话 | 推送结果、单据列表、推送结果查询另加 `waiting` 字段；说明里一句英文 | 只加字段 | `7a3b370d2` |
| Cowork 定时任务 | 只在 CC 的 `cowork_folder_tasks` 工具结果里读时加 `results.waiting`（账套级）；网页任务列表不改；不算「需要处理」 | 只加字段，不走 24 小时缓存 | `720e2d4c0` |
| ERP 采购 / 销售 / 出入库（含调拨、调整）/ 收银小票列表 | 接口补 `ledger_waiting`；Express 栏仍「排队中」，悬浮说明是这句 | 共用列表现成的 title | `8c1b1c4f9` |
| ERP 单据「Express」弹窗 | 「原因」一栏显示这句（含作废那一件在等） | 现有弹窗的原因栏 | `8c1b1c4f9` |
| ERP 的 CC 对话 | 三个工具另加 `waiting` 字段；说明里同一句英文 | 只加字段 | `a5489dd6f` |

手机 / 窄屏：两边列表在手机宽度都没有现成位置（状态栏 / Express 栏隐藏），不自造控件。Cowork 靠详情提示行；ERP 按弹窗交付（点进单据 → Express），不给 ERP 详情新加提示行。收银台「本班销售」不接。

给收款窗口的约定（主会话转；本分支不改收款分支）：`pearnly_core.cc_desktop.jobs.express_wait(name)` → `{"waiting": bool, "since": ISO UTC | None}`；`express_wait_many(names)`（列表用）→ `{任务名: 上次回报时刻}`，只含此刻在等的那几件（不在等的不出现，不是 `{name: bool}`，按「名字在不在里面」判）；`book_express_wait(book_key)` → 同 `express_wait`。只读、查不到回不在等、90 秒过期。（集成分支 integration-1112 核对：收款 `collection/service.py` 取 `set(express_wait_many(names))`、ERP `express/waiting.py` 与 `cc_tools.py` 用 `in` / `.get`，都按此用。）

## 限制

- 当天第一笔发出时还不知道占用，只能回「已记录 · 待写入」；同一本账套之后 90 秒内的单才带得上这句；电脑 90 秒没有新的回报又回到不知道。
- Cowork CC 对话里 `_concluded` 那条的 `waiting` 不带 90 秒过期（那个出口没有回报时间）；其余出口都有。
- 定时任务的 `results.waiting` 是账套级：在等的是别人手动推的票时也会带。
- ERP 四个列表接口只有「调用了标记函数」的源码断言，标记函数本身有功能测试。

## 验证

- 离线：`check-architecture`、`check-copy`（拦截 0，报告 `~/pearnly-devtools/express-wait-display-copy-report.md`）、`check-product-map`、`check-unbounded-queries`、`check-test-coverage` 通过；`run-offline-tests` 63 / 65（没过的两条是本机没有 frappe）；`test_cc_desktop_channel` 37、`test_cowork_express_wait` 4、`test_cowork_assistant_tools` 257、`test_erp_express_direct` 187、`test_erp_cc_chat_push` 24、两个新的 node 测试通过。
- 本机实验栈实看（自建 `pearnly-ewait-lab`，线上同版镜像，用完已删并回读；`720e2d4c0`）：17 张截图在共享笔记 `local_40940693…/express-wait-display-截图-11-12/`，逐张看过。状态是造的：Cowork 的推送记录由产品函数真实生成后改原因与时间；ERP 的写账件用 `jobs.enqueue` 建出再改库（实验栈没有 CC 桌面版，单据走不到真的排队）。

| 显示位置（同一段代码渲染的入口归并） | 电脑 | 手机 | 对应入口 |
|---|---|---|---|
| Cowork 录入列表悬浮 | 通过 | 通过（按预期看不到） | `cowork.page.intake`、`cowork.history` |
| Cowork 录入详情提示行 | 通过 | 通过 | `cowork.intake.upload`、`cowork.intake.viewer` |
| ERP 列表 Express 栏悬浮（采购、销售、入库、收银小票各一张） | 通过 | 同机制，看不到 | `erp.page.purchase`、`erp.page.sales`、`erp.page.stock-in`、`erp.page.pos-receipts`；出库 / 调拨 / 调整同上一处，没单独截 |
| ERP 单据 Express 弹窗原因栏 | 通过 | 通过 | `erp.detail.purchase.submitted`；销售 / 出入库详情同上一处 |
| 过期对照（91 秒前）：Cowork 列表与详情、ERP 列表与弹窗 | 通过（不再显示） | — | 同上 |
| 英文、泰文（ERP 弹窗） | 通过 | — | — |
| 页面脚本异常 | 17 次开页全无；站点 Error Log 0 → 0 | | |

没测到：作废那一件在等的弹窗分支、日文界面、CC 对话里的 `waiting`（要真模型）、定时任务工具字段的真实 Site；影响清单里与显示无关的入口（上传、识别、LINE、打印、扫码等，只因同文件被命中）没有逐个点，靠离线测试与整套检查；真机在 express-busy-probe 那一批一起核。

实看时的观察（不是本分支引入）：Cowork 详情电脑宽度下提示行在左栏约 220px、折成两行并紧贴下面的「单据号」，与红色失败提示共用同一容器，是既有排版；Cowork 手机列表第二行买方名被截成「• …」，两种状态下都一样。

## 已知问题（不在本分支修）

三个 node 测试文件没接进 CI：`tests/test-stock-entry.cjs` 用 node 直接跑报 `p.express_used is not a function`（改动前就有的旧桩缺口）；`tests/test-product-multi.cjs`、`tests/test-product-scan.cjs` 也跑不过，原因没看（按最后修改早于本分支推断为既有，没在 main 上验证）。
