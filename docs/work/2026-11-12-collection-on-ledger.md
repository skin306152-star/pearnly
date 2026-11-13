# 收款接本对话账套与等 Express 显示（11-12，收款接手窗口）

- 分支 `pearnly/collection-on-ledger`，工作树 `.claude/worktrees/collection-2-de86fb`。
- 前情：收款最终分支 `pearnly/collection-cc-chat`（d1ec8d5ca，基于 c63458f5f），交接「收款-交接-11-12.md」（共享笔记 local_74a4f119…）。
- 任务：①变基到 main 702580300；②叠在 A 线 `pearnly/cc-conversation-ledger`（18f969a06）上接审计 A10 / A11；③接 express-wait-display
  的只读查询显示「在等 Express 关闭」；④交接剩下的可动手项。

## 1. 叠放关系（集成时照这个顺序）

```
origin/main 702580300
 └ A 线 cc-conversation-ledger 的 20 个提交（本地变基到 702580300，冲突只在 shared-parts.toml / registry.toml 两处登记，两边都留）
    └ 收款 collection-cc-chat 的 31 个提交（变基；registry.toml 一处冲突，两边都留；erp.exports 的 covers 补回 collection/output.py）
       └ 本窗口的提交（录吧 → 写入注释、A10 / A11、等 Express 显示、文案、导出表头）
```

- **A 线分支本身没动**：本支里那 20 个提交是本地变基的副本。集成时 A 线自己的变基版先进集成分支，本支再 `git rebase --onto <集成分支> <A 线副本的顶>`；
  两份同内容的提交 git 会自动跳过，只可能在两处登记表上再冲一次（两边都留即可）。
- **等 Express 显示没叠在 express-wait-display 上**：那支建在 express-busy-probe（旧探测）上，探测正改成 busy-probe-2，CC 版本号也在变；叠上去
  每次它们变基本支都要跟着重排。改为按约定直接调 `pearnly_core.cc_desktop.jobs.express_wait_many(names)` / `book_express_wait(book_key)`，
  测试里用替身（`mock.patch(..., create=True)`），另有一条约定测试 `ExpressWaitContract`：那支进来后自动跑、钉住两个函数的样子。
  **因此本支必须排在 express-wait-display 之后合入**（集成顺序本来如此）；单独上会在「我的收款」列表与 LINE 待写入回复处报 AttributeError。
- 实际返回值与任务书的约定差一点：`express_wait_many` 回的是 `{任务名: 上次回报时刻}`（只含在等的），不是 `{name: bool}`；本支按「名字在不在里面」用，两种都对。

## 2. A10 / A11：一笔收款只认一本账套

| 入口 | 改前 | 改后 |
|---|---|---|
| CC 对话（直写） | 列发票、列方式按今天选账套，建草稿按单据日期选；跨年两本不同。同期两本抛「同期几份」，没有作答入口 | `service.book_for` 经 `express.conversation.wanted` 取本对话账套，三步同一本，不看日期。未定时回 `ledger_needed` + 可选几本 + 说明（问一次、用 `pearnly_conversation_ledger` 设，记在会话上），形状同单据推送的 `ledger_needed` |
| CC 写入计划 | 不核账套 | `plan.view` 在对话里核：草稿冻结的账套 ≠ 本对话账套（之后在右栏换了，或 LINE 录在别的本里）→ 写不了 `collection.book_not_conversation`；未定 → `ledger_needed`。发票各本各的，不改投 |
| LINE | 同期两本卡死（`choose_book` 没有调用方） | 选完公司（只有一家时直接）先 `service.book_options`：定得下来去找客户；同期两本、没选过 → 一步「选择账套」（列表写全「名称 · 位置」，按钮序号 + 名称），选了 `choose_book` 记住，下次不问。旧按钮 / 不在列表的值拒，打字照这一步再问 |
| 网页「我的收款」编辑弹窗 | 按草稿日期重新选账套取「能收多少」和方式（同期又报来一本就停在「同期几份」） | `service.edit_choices`：一律用草稿冻结的那本 |

- **记忆复用 ERP 现成的**：`choose_book` 写的就是 `express.choices.ACCOUNT_SET`（按公司 × 会计年度，`target.year_subject`），与网页单据「选账套」
  （`express/api.answer`）、CC 对话本对话账套的默认（`masters.cc_projects.ledgers`）同一份。LINE 里选过，网页单据和新开的 CC 对话都按它；反之亦然。没另造一套。
- `choose_book` 改了签名：`(company, key, day=None)`，记忆的年度由服务端按这一天算（不再信客户端给的 subject）；已定下来的那本再选一次照回。
- 网页没有建收款的入口（只改草稿），所以网页不需要「选账套」那一步；编辑弹窗改为用冻结的那本后不会再遇到「同期几份」。

## 3. 等 Express 关闭的显示（约定见 express-wait-display 记录）

- `collection/push.py`：排队中 / 写入中**不再把任务原因码抄到收款记录**（原来照抄，合入 busy-probe 后记录上会挂着 `ACCOUNT_BUSY_LOCKED`，像失败了）；写成 / 失败照旧。
- 「我的收款」：`service.mine` 每笔多带只读 `express_wait`（待写入且写账件此刻在等，`express_wait_many` 一次查完）；列表 Express 一栏悬浮说明、
  点开已放行的那笔（手机没有悬浮）显示现成那句 `express.error.ACCOUNT_BUSY_LOCKED`，状态仍是「待写入」。
- LINE：放行后的那条当场回复（免费的回复令牌）在「已记录 · 待写入」下面多一行那句（`service.express_waiting` → `book_express_wait`）；不另推送说原因；写成后照旧推 RE 号。
- CC 写入：放行结果是 queued 且这本账套在等时，结果带 `express_wait: true`，给模型的说明加一句（让它告诉用户关闭 Express 后自动写入）。
- 文案只用现成那句，不新增不改。
- **限制（如实）**：当天第一笔放行时电脑还没去试过这本账套，不知道 Express 开着，只能回「已记录 · 待写入」；之后同一本账套的笔才在回复里带那句。
  20 秒等待之内电脑试过并回报了的，这一笔的回复里就有。

## 4. 交接剩下的可动手项

| 交接 §3 | 结论 |
|---|---|
| 1 A10、2 A11 | 本支做完（§2） |
| 3 等 Express 显示 | 本支做完（§3） |
| 4 CC 对话里写成后主动补一句 | A 线做（`push_followup` + `TELL` 加 cc）；本支没动 `push.py` 的 TELL / `_tell` 与 `cc_tools.push` 的结构 |
| 5 其他三语富菜单 | 词条核对完：收款格四张图（th / zh / en / ja）都在、字对；收款相关 105 条界面词四语齐全。真机三语没测（要改三星系统语言）→ 写进真机清单 |
| 6 发凭证后回复变泰文 | 只记录不修（已知问题：LINE 公共取语言失败时退回账号语言） |
| 7 部署后 publish / 发 CC / 三星补测 | 主会话排 |
| 8、9 新租户材料、客户说明 | 要岳父 / 客户给，不是代码 |
| 10 直写补审计列 | 建议另排，不在本支 |
| 没文案只说「Could not record」 | 补齐：同期两本 / 本对话账套未定 →「选择账套」（新）；没有税号、没有账套、找不到客户用现成句子；公司没接 Express（新）、发票超过 50 张（新）、方式超过 10 种（新）。测试 `Explained` 钉住所有收款员会遇到的码都有自己的话 |
| 顺手发现 | 收款导出的表头「Customer Code / Invoice No / Invoice Date / Prefix」ERP 没有译文（导出是英文）→ 补四语；「Sales Person」改用现成的「Salesman」 |

新增文案（官方简短、四语，`check-copy` 无拦截）：

| 英文 | 中文 | 泰文 | 日文 |
|---|---|---|---|
| Choose the account set | 选择账套 | เลือกชุดบัญชี | 会計データを選択 |
| This company does not use Express | 此公司未使用 Express | บริษัทนี้ไม่ได้ใช้ Express | この会社は Express を使用していません |
| At most {0} invoices | 最多 {0} 张发票 | ได้สูงสุด {0} ใบแจ้งหนี้ | 請求書は最大 {0} 件です |
| At most {0} collection methods | 最多 {0} 种收款方式 | ได้สูงสุด {0} วิธีรับชำระ | 入金方法は最大 {0} 件です |
| Customer Code | 客户编号 | รหัสลูกค้า | 顧客コード |
| Invoice No | 发票号码 | เลขที่ใบแจ้งหนี้ | 請求書番号 |
| Invoice Date | 发票日期 | วันที่ใบแจ้งหนี้ | 請求書の日付 |
| Prefix | 前缀 | คำนำหน้า | 接頭辞 |

改已有文案：无（注释里的「录吧」改「写入」不是界面文案）。

## 5. 科目余额 CALSTA 标记

CC 桌面版窗口的本地提交 4e88e559f（`pearnly/cc-desktop-payload-3a2e3d`，未推）已把 `glbal_mark.mark` 接进全部写 GLJNLIT 的路径，**含收款 `dbf_receipt.py`**。
本支不动 `dbf_receipt.py`、`BACKUP_TABLES`、`worker._write_tables`（本支 companion 只改 `payment_methods.py`），无冲突；收款不需要另接。
对照载荷窗口的用法说明（共享笔记「收款写路-GLBAL标记与占用探测-用法-11-12.md」）：收款写路的 `glbal_mark.mark`、GLBAL 进写前备份、占用探测按
`dbf_receipt.BACKUP_TABLES` 都已在那支里接好；它推到远端后对一遍：`dbf_receipt._write_all` 有 `written` 列表与 `glbal_mark.mark(account_dir, written)`、
`BACKUP_TABLES` 含 GLBAL、`_write_tables("receipt")` 回 `BACKUP_TABLES`，本支变基后这三处不变即可。集成后真机核一张 RE 写完后 GLBAL 对应格被标待重算（真机清单第 5 项）。

## 6. 测试

先写、在旧代码上红（提交 651c15891、本支等 Express 那一组）：
- `test_erp_collection.py`：`Books`（同期两本 → 问 / 选 / 记住 / 与单据同一份记忆；不在列表的拒；编辑弹窗用冻结的本）、`ExpressWaiting`（列表只标在等的、原因码不抄、点开那笔、页面源码）、
  `Explained`（每个码都有话）、`ExpressWaitContract`（约定，wait-display 进来后才跑）。
- `test_erp_collection_line.py`：`BookStep`（选完公司问一次、记住；旧值拒、打字再问）、`Results.test_waiting_for_express_is_said_in_the_pending_reply_only`。
- `test_erp_collection_cc.py`：`ConversationBook`（跨年三步同一本、未定在对话里问、草稿账套≠本对话账套不进计划）、`ExpressOpen`；Base 加本对话账套替身
  （叠上 A 线后写入计划要读 `ledger.of_session`，原来 7 条因此报错）。
- 改了一条旧断言：`Receipts.test_other_failures_and_queue_states` 排队中原因由「照抄」改为空（§3 有意变化）。

检查结果与共用件入口逐项见 §7。

## 7. 检查与共用件入口

- 本地 CI 同名闸门（`~/pearnly-devtools/gate.sh`，含 npm ci）：236 步 GATE EXIT 0（`~/pearnly-devtools/collection-on-ledger-gate.log`）。
- 候选离线（生产镜像 b3adf474，`cand-local.sh offline`）：109 组 LANE EXIT 0（`~/pearnly-devtools/collection-on-ledger-cand.log`）。
- 收款三组离线：`test_erp_collection.py` 51（1 条约定测试等 wait-display 进来才跑）、`test_erp_collection_line.py` 35、`test_erp_collection_cc.py` 24；
  `test_cc_conversation_ledger*.py`、`test_companion_contract.py` 通过。
- 文案：`check-copy.py` 无拦截；本支新增 8 条见 §4（`--report` 里其余新增是收款分支原有的）。
- **流程没在本机跑**：本支单独挂实验栈时「我的收款」列表与 LINE 待写入回复要调 wait-display 的查询（那支未合入）会报错，流程 `erp.line.collection`、
  `erp.page.my-collections`、`erp.collections.refused`、`erp.exports` 要在集成分支上跑（排在 wait-display 之后）。
- `check-impact.py --base <A 线副本顶>`：登记表完整（EXIT 0）。命中共用件 cc-dbf-write（来自收款分支原有的 `payment_methods.py` 改动）与收款自己的入口：

| 入口 | 结果 |
|---|---|
| erp.collection.server / erp.line.collection / erp.cc.collection（offline） | 通过（三组离线） |
| erp.cc.chat-push、cc.desktop.operator（offline） | 通过（闸门里的离线组） |
| erp.page.collections / erp.page.my-collections / erp.line.webhook-sim / cc.desktop.write / erp.cc.write / erp.cc.stock-entry（auto） | 没测到：要实验栈，且要先有 wait-display，集成分支统一跑 |
| erp.line.chat / erp.line.collection 真机、等 Express 三处显示、同期两本作答、科目余额标记（manual） | 没测到：真机清单（共享笔记「收款-接账套与等Express-真机清单-11-12.md」）交主会话排 |
| 其余 manual 入口（POS、调拨、作废、主档、导航、开户等） | 没测到：本支没改它们的代码，只因 `payment_methods.py` 落在共用件路径上被列出；随集成分支全套流程 |

## 8. 11-12 晚进度（集成分支 integration-1113 之后）

- 收款 43 个提交已叠进 `pearnly/integration-1113`（42c2c707e）。闸门里 `test_erp_collection` 一次导入失败：查明机制是那一刻读 `express` 目录的
  `stat` 与 `listdir` 同时出错、被 Python 导入器当成空目录（已在副本里逐字复现），代码与测试无关、无顺序依赖；已排除项、探针用法见共享笔记
  「test_erp_collection-导入偶发失败-查因-11-12.md」，探针 `~/pearnly-devtools/import-probe/sitecustomize.py`。本分支没有为此改代码。
- 11-13 真机回归照做清单：共享笔记「收款-接账套与等Express-真机清单-11-12.md」（同期两本、LINE 9 项、等 Express 三处显示、三语菜单、科目余额标记与重算）；
  实验栈驱动 `~/pearnly-devtools/collection-lab/collect_lab.py`（只编译检查过，明天第 0 步先 `scan` 验）、开 Express 赊销单窗口 6 分钟的计划 `hold_iv.json`。
- 科目余额那一节与 CC 桌面版验证窗口（local_becd0c1b）分工待回复。

## 9. 11-13：银行流水 BKTRN 与收款方式户头字段（接手窗口）

11-13 白天真机结果（同期两本、LINE 9 项、等 Express 三处、GLBAL 两格、旧卡 B1 复现）见共享笔记「收款-真机与CC侧-交接-11-13.md」§3，这里不重抄。

### 9.1 银行流水（管钱缺陷）
- 现象：原生存 RE 时，方式在 ISRUN ZR 经 `SHORTNAM` 指到 BKMAS 户头的，每种方式另记一行 BKTRN（类型 `bR`）；我们没记，按户头看的存款明细、银行对账会缺这些收款（总账分录照写了，科目余额不受影响）。
- 依据（本机 NLTTEST 1.0.1.94 只读副本）：这类方式行 2247 / 2247 都有 `bR` 一行、一行方式一行流水；不经 SHORTNAM（只填科目）的 0 行。字段见 `dbf_receipt.bank_row` 的说明；户名 = PRENAM + 不换行空格 + CUSNAM（没有前缀名就只有 CUSNAM），截 50 字节。`CHQSTAT 19`、`REFDOC ~` 含义没查到，照原生。
- 改法：`dbf_receipt.plan_bank`（写前判哪几种方式要记、账套没有 BKTRN 就整单不写）→ `_write_bank` 追加 → 读回核类型 / 户头 / 行编号 / 金额，不符整单回滚；BKTRN 进 `BACKUP_TABLES`（写前备份与占用探测，文件不存在两处都跳过），重建索引只在真写了时带上。
- 原生重放逐字节：用 `bank_row` 照原生单头重造 NLTTEST 每一行 `bR`，可比 2245 行中 2202 行原始字节完全一致；其余 43 行都是事后人工改过（手改方式行编号 31、改单后 CHGDAT / USERID / 日期 12），另 18 行原单已改删不可比。
- 没做（写进清单）：现销 HS 收转账、付款单 PS 要不要记流水（`bP` 存在，本批不写 PS）；69EXP\TEST 已写的 RE0000006 / 07 / 10 / 11 等缺流水、不补；线上 1.12.20 写的收款同样缺，发布记录的已知问题里记。测试机真写核对等 1.12.21 重打包。
- 和集成分支合并：`BACKUP_TABLES` 一行与 GLBAL 那次提交冲突，取 `WRITE_TABLES + ("ARMAS", "GLBAL", "BKTRN")`。

### 9.2 收款方式带户头信息（CC → 服务端，字段归本线）
- `express_masters.receipt_types[]` 新增可选 `bank_account {code, bank, short, number, branch, via}`（via：`shortnam` 经 SHORTNAM；`account` 只填科目、科目恰好是唯一一个户头的）与 `uses`（原生 RE 方式行按前缀计）。方式有问题、找不到户头、几个户头同科目的不带 `bank_account`。
- `uses` 只在同一户头被两种以上方式指到时才只读原账套 ARRCPCQ 去数（主档副本不复制这张表），其余为 0；服务端只拿它在同户头里挑一种。
- 契约：`tests/test_companion_contract.py::ReceiptContract::test_receipt_types_carry_their_bank_account_and_uses`。服务端标签与测试替身归 LINE 侧。

### 9.3 测试
- `companion/tests/test_dbf_receipt.py` 新 7 项、`test_express_receivables.py` 新 2 项；CC 全量 1720 项过（本机临时虚拟环境，Windows 专用依赖跳过的照旧 59 项）。
