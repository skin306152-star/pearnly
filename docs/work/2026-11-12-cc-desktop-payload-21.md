# CC 桌面版载荷 21「引导录入」与三处写入缺陷

- 属于 [CC 引导式录入](2026-11-12-cc-guided-entry.md)（用户 11-12 批准）；依据是评估窗口的
  [Express 必填覆盖表](2026-11-12-cc-guided-entry-express-coverage.md) 第 8 节「CC 桌面版载荷接口约定」、第 2.4 节、第 5 节
  A5 / A8 / A10 / A11 / A13 / A2 / R2 / B1 / B2 / B5 / G-B6（两份文档在分支 `pearnly/cc-guided-entry-51673b`）。
- 分支 `pearnly/cc-desktop-payload-3a2e3d`，工作树 `.claude/worktrees/cc-desktop-payload-3a2e3d`。原基线 `origin/main` c63458f5f；11-12 晚按主会话
  变基到 `pearnly/express-busy-probe-2`（它已变基到 `origin/main` 702580300），CC 桌面版版本号与那一支同为 1.12.21，README 一个条目合写。
- 状态：**本次范围完成（代码与离线证据）**；测试机实写、62 套原生回放没做（清单见
  [测试机清单](2026-11-12-cc-desktop-payload-21-testpc-checklist.md)）；没合入、没发版，版本号与 README 条目等主会话定。
- 分工：服务端出题、前端卡片、读 `unit_guessed`、四语词条由引导式第 1 期窗口做；本任务只做 CC 桌面版（`companion/`）与服务端里
  属于载荷版本 / 回执键 / 原因码名单的最小部分。

## 主会话 11-12 定的口径

不做过渡版；只有带新键的载荷才盖 21，不带新键的行为逐字节不变；G-B6 对所有入口生效，已建成 Y 的档不回改；Express 不拦的不拦、
Express 拦的不绕过；先服务端后 CC 桌面版，同一次发布。对约定的七条补充（都已批准，引导式窗口写进约定第 8 节）：

1. 要人定一题时候选走回执 `meta.choice`：`{code, line, name, stkcod, units: [{code, factor, role}], unit_table: [码], balance,
   cash_account, lines: [...]}`，按原因码只带相关项；第一行的各项在顶层，全部要定的行在 `lines`（一张单几行同一类一次带回）。
2. 服务端的版本常量、新键名单、盖版本、原因码名单由本任务加，引导式窗口只引用。
3. 显式给了 `unit_code`、它不是这件物料的单位、又没 `factor`：不带 `guided` 也回 `UNIT_CHOICE`（显式键不回落）。
4. `cash_account` 出现在 POS 日汇总 / 退货单上：`PAYLOAD_INVALID`。`CASH_ACCOUNT_CHOICE` 的候选科目不由 CC 带，只带账套现金科目码。
5. `STOCK_INSUFFICIENT` 原文格式不变（「物料@仓 need 要 have 有」）；随单新建的行物料那一格写品名、行尾加 `new`、排在后面；
   另加结构化的 `meta.shortages = [{stkcod, loccod, need, have, name?, new?}]`，两产品显示新建的行用它。
6. 成功回执 `line_modes` 的 `name` / `created` / `unit_guessed` / `unit_code` 键不变；服务端读 `unit_guessed` 归引导式窗口。
7. 代录（`entry_mode = operator`）的单带了新键：CC 端也拒收 `PAYLOAD_INVALID`（转人工）兜底。

## 做了什么

| 项 | 结果 | 落点 |
|---|---|---|
| 版本 21、新键名单、原因码名单 | 已做 | `companion/src/companion/payload_contract.py`；服务端 `express_rules/push/common.py`（`GUIDED_PAYLOAD_VERSION`、`GUIDED_KEYS`、`guided_keys_used`、`finalize_payload` 盖 21） |
| 版本 ≤ 20 不读新键 | 已做 | `guided_keys.py`（`active` / `items` / `header` / `party`；纯函数、不依赖 dbf），两个适配层只经它取新键 |
| 新键类型与取值校验 | 已做 | `guided_keys.shape_problem`，接在 `validate_purchase_payload` / `validate_sales_payload` 末尾 |
| 写前只读核对（值不在账套 → 拒收；引导模式缺省 → `*_CHOICE`） | 已做 | `guided.py`（`invalid_values`、`choices_needed`），接在 `dbf_writer.write_purchase`、`dbf_sales.write_sale` 动账套之前 |
| `unit_code` + `factor`（A5） | 已做 | `guided_keys.items` 把行的 `unit` 换成单位码，各写路照旧读 `unit`（库存行、非库存行都生效）；换算仍由 `stock_units.resolve` |
| `acccod` / `stkgrp`（A10）、`negallow`（G-B6） | 已做 | `product_master.ensure_stock_item` 新参数；`stock_purchase`、`stock_sale._sale_basis` 传行上的值 |
| `cost_choice` / `cost`（A11） | 已做 | `stock_sale.write_stock_lines`：没有成本基础的行按人填的每库存单位成本结转（同「按标准成本结转」那一支），回执 `reason = cost_given`；账里有成本的不用它、回执带 `cost_ignored` |
| `due_date_be`（R2） | 已做 | `dbf_writer._write_all`、`dbf_sales._write_all`：到期日写它、`PAYTRM` = 到期日 − 单据日 |
| `cash_account`（A13、A2） | 已做 | 现购贷它、现销借它；引导模式下账套现金科目不能记账而没给 → `CASH_ACCOUNT_CHOICE` |
| 对方块 `type` / `payterm`（A8） | 已做（`type` 的合法值见「没有原生证据的」） | `master_create.ensure_supplier` / `ensure_customer` |
| B1 新库存品换算系数照抄模板 | 已做，所有入口 | `product_master`：`_STOCK_CLONE_FIELDS` 去掉三个系数，新建写 1 |
| G-B6 新建库存品写死允许负库存、销货不核随单新建的行 | 已做，所有入口 | `product_master`（`NEGALLOW` 留空）；`stock_guard.shortages` 把随单新建的行按结存 0 核（库存单 / 调拨照旧走自己的 `new_item_shortages`） |
| 回执：新建的行带品名与新建标记 | 已做 | 成功：`line_modes` 原有 `name` / `created`；库存不足：`meta.shortages` 的 `name` / `new`；`worker._push_meta` 带 `choice` / `shortages`；服务端 `sanitize_push_meta` 放行这两个键（`clean_choice_meta`、`clean_shortages_meta`） |
| 原因码归类 | 已做 | CC `worker._outcome_for`（六个码 `needs_review`）；服务端 `cc_desktop/wire/ack_rules.py`：七个码进 `NEEDS_PERSON_CODES`，三个 `*_CHOICE` 另列 `CHOICE_CODES` |
| B5 `unit_guessed` 没人读 | CC 侧已做：引导模式不再猜（`UNIT_CHOICE`）；非引导回执键不变。服务端读出并记录**没做**（归引导式窗口） | — |
| B2 代录销售单不填「อ้างอิง」 | **没做**：约定要求先在测试机确认这一格在 IV / HS 表单上的位置、长度、能否保存，确认前不改配方 | 测试机清单第三部分 |
| G-B3（五个码白重试）、G-B4 | 没做：主会话定并入第 1 期（引导式窗口） | — |
| **G-A15 销售单的销售员**（主会话 11-12 追加，另一个提交） | 已做，所有入口：写 IV / HS 时读客户档 `ARMAS.SLMCOD` 写进单头 `ARTRN.SLMCOD`；客户档没有、没有这一栏、客户是这张单新建的 → 留空。不问、不显示、不加载荷键 | `account_rules.party_salesman`；`dbf_sales._write_all` |
| **负库存要批准（D-7，并进 G-B6）**（主会话 11-12 追加） | 已做，所有入口（销货、库存单、调拨、作废）：负库存核对同时读 `ISINFO.NEGAUTH`，非空按「不许负库存」核；走 `STOCK_INSUFFICIENT`，回执 `meta.approval = true`（只在「账套允许负库存但要批准」是原因时带；账套本身不许的不带） | `stock_guard.negative_allowed` / `approval_needed` / `refusal`（四条写路共用一个出口，都带 `meta.shortages`）；`write_result.approval`；`worker._push_meta`；服务端 `sanitize_push_meta` 放行 `approval` |
| **缺供应商发票号不再拒收（D-5）**（主会话 11-12 追加） | 已做，所有入口：去掉采购适配层的 `MISSING_REF_NO`；`ref_no` 为空照写，单头 `APTRN.REFNUM`、进项税记录 `ISVAT.REFNUM`、库存卡 `STCRD.REFNUM` 留空，单号照旧自编（单别 + 佛历日期 + 序号，本来就与发票号无关）。销售单的票面号照旧必填。判重口径与复核卡标出归服务端第 1 期 | `purchase_adapter.validate_purchase_payload` |

行为变化（对所有入口，不看版本）：① 新建库存品「允许负库存」留空、三个换算系数写 1；② 账套不许负库存（或行上 `negallow = N`）时，
「卖随单新建的物料」的销货从「写成负数」变成整单不写、回 `STOCK_INSUFFICIENT`；③ 客户档有销售员的销售单单头带上销售员（G-A15）；
④ 账套填了「扣成负库存要批准」的，库存不够的销货 / 领用 / 调拨 / 作废从「写成负数」变成整单不写；⑤ 没有供应商发票号的进货单从拒收变成照写。
其余全部只在版本 21 的载荷上生效。B2 已由主会话改列为 G-B2′（票面号没进账，先在测试机试手填单号再定做法），仍不在本批。

## 科目余额标「待重算」（GLBAL，主会话 11-12 追加；所有写分录的直写）

事实（共享写入实验窗口 11-12 在正版 Express 上用 Process Monitor 抓到、前后文件比对证实；共享笔记「共享写入实验-11-12/GLBAL核实/」与 P0）：
Express 保存一张单时不累加 GLBAL 的金额，而是对每行分录的 GLBAL 行（键 = ACCNUM + DEPCOD）把 `CALSTA`（24 位）里这一期那一位置 `'0'`
（待重算）；VFP 行锁、整行写回、解锁；金额、期初、LY / NY 栏不动，与借贷方向无关。我们以前不标——69EXP\TEST 里我们写的 RE0000006
三个科目 11 月不在 GLBAL 里、CALSTA 全 1，Express 自己录的 RR0000094 四行被标了 0；NLTTEST 只有我们写过分录的三格没进余额。

做法：共用的一步 `companion/src/companion/glbal_mark.py`（`mark(account_dir, entries)`），七条写分录的路径在写完 GLJNLIT、关表之后调它：
采购 `dbf_writer._write_all`、销售 `dbf_sales._write_all`、退货 `dbf_refund`、POS 转账凭证 `pos_journal`、库存单 `dbf_stock`、作废 `dbf_void`
（改的是原单分录行，同样标）、收款 `dbf_receipt`。

- 期间映射：ISPRD 的本年度（CY）段第 k 期 → 第 k 位（实证：1 月起账的账套 11 月单标第 11 位）；**下一年度（NY）段第 k 期 → 第 12 + k 位**
  （11-12 晚放开，依据 62 套统计：S 盘副本 1033 套里第 13–24 位出现 0 的 2388 格，2386 格正是 NY 那一期有分录的位置；本年度同样
  「有 0 的格都有分录」）。两个年度都不在的日期不写、回报。
- 只把 `'1'` 改成 `'0'`；`'0'` 已是待重算不用改；**空格（这一期还没算过）照原生不碰、不报**；别的值不写、回报。
- **对应行不存在：照原生追加一行**（科目码、部门照填，项目、计算状态全空格，金额栏全 0），只在这种情形重建 GLBAL 的索引并读回这一行。
  （11-12 晚接手窗口改，`pearnly/cc-desktop-verify-7f2d4c` 4576865b6；依据见下面「11-12 晚测试机2 实证」。）
- 不写的格记进 `glbal_mark.collecting()`（`worker._handle_dbf_task` 包住一次写入），写成后随回执 `meta.glbal_unmarked = ["科目|部门|期: 原因", …]`
  回云端（服务端 `sanitize_push_meta` 放行），表示这张单要在 Express 里跑一次重算。
- 写完立即读回要标的位，不是 `'0'` 就抛错、整单回滚。GLBAL 进了写前备份（`S.BACKUP_TABLES`、`dbf_receipt.BACKUP_TABLES`、库存单与作废的
  `_backup`）和占用探测清单（`worker._write_tables`；调拨不写分录，不带）；常见情形**不进重建索引**——只改已有行的一个字符；
  只有追加了行的那一单才重建 GLBAL 的索引（`dbf_writer._reindex`，失败整单回滚，备份含 CDX）。
- 没做 VFP 行锁：别的表我们也不加 VFP 记录锁，靠写前占用探测（GLBAL 的 DBF 与 CDX 都拿得到独占才写）与 `PEARNLY.LCK` 保证没人同时在写。

线索（不是证据，没据此写）：本机 SINCTEST 副本的 GLBAL 里 CALSTA 第 19、20 位有 `'0'`（这本账套 CY = 2025、NY = 2026，第 19 / 20 位对应
NY 第 7 / 8 期），看起来 NY 段就是第 13–24 位；等实验窗口 a 阶段的证据再放开。

测试：`companion/tests/test_glbal_mark.py` 11 条（实现前全红）：采购写完只有分录科目那几行的那一位变、别的行别的字节逐字节不变；
销售、24 位都有值的行；（11-12 晚改）下一年度标第 12 + k 位、两个年度都不在的不写并回报、空格位不写不报、行不存在照原生追加；账套没有 GLBAL 照写并回报；读回不符整单回滚（GLBAL 还原）；回执带
`glbal_unmarked`；各方向的探测清单含 GLBAL、调拨不含。契约测试加一条（七条写路都调同一步、服务端放行）。

回放（序列 **D**：SINCTEST 的本年度 2568-10 月进销 4 张）：旧代码与新代码写出的全部表只差 `GLBAL.CALSTA` 8 格（第 10 位 1 → 0，
正是这 4 张单分录用到的 8 个科目），别的逐字节不变；合成账套没有 GLBAL，零差异（结果里只有 notes 文字里的账套目录名不同，是回放目录名）。
序列 A / B / C 在原来的账套上重跑结果不变（A 的日期在 SINCTEST 的下一年度，按上面的规则不写 GLBAL，所以 A 仍逐字节相同）。（11-12 晚起下一年度也标，A 的 GLBAL 会多出第 12 + k 位的 1 → 0，预期差异。）

### 11-12 晚测试机2 实证（正版 Express、`70EXP\test`，代录引擎原生录单，录前录后比 GLBAL 原始字节）

- **空格格不碰**：原生 RR0000025（2026-11-12，物料 500603，三个科目都已有 GLBAL 行、CALSTA 全空格）存完，GLBAL 逐字节不变（含表头）。
- **行不存在就追加**：原生 RR0000026（物料 01ดีเซล，存货科目 11-04-02-00 在 GLBAL 里没有行）存完，GLBAL 末尾多一行：
  未删除、`ACCNUM` 照填、`DEPCOD` / `JOBCOD` / `CALSTA` 全空格、75 个双精度金额栏全 0；表头只变行数（同一天，日期字节不变）。
  `test_glbal_mark.py::test_a_missing_row_is_appended_the_way_express_does_and_indexed` 按这一行的字节断言。
- 这本的期间特殊（本年度 1957、次年度第 12 期一直到 2056），2026 的单都在次年度段、GLBAL 从没算过，所以「次年度标哪一位」在这本分不出来；
  本年度标 0 → 重算 → 试算表（清单 33、34）与「什么触发只重算标 0 的格」改在 `69EXP\TEST2` 做（主会话批，见下）。
- **`69EXP\TEST2` 上的 ③（主会话批借用）**：TEST2 原来的 GLBAL 也没算过（12 行、全空格）。17:26 逐文件独占试开 118 个文件都没人占后，
  先在测试机2 留了 GLBAL / GLJNL / GLJNLIT 等的备份（含 CDX 与哈希，`C:\RecSetup\cc21\t2-171900`），再跑 5.A「คำนวณยอดเปลี่ยนแปลง」：
  窗口只有「งวดบัญชีจาก 1 ถึง 11」（默认从第 1 期算到当前期）与「คำนวณ」，约 10 秒弹「การคำนวณเสร็จสิ้น」；GLBAL 变成 840 行，CALSTA 全是
  `11111111111` + 13 个空格——**只算选定的期，之后的期与整个次年度都留空格**（所以空格格很常见，照原生不碰是对的）。
  - 原生 RR0000098（2026-11-12）存完：分录四个科目那一行第 11 位 1 → 0，金额不动（与抓包规则一致）。
  - 打开 6.1 报表选择窗、展开「5. รายงานบัญชี」：GLBAL 不变（不触发重算）。选到「6. งบทดลอง」后按 F5 没出预览，**试算表本身会不会顺带重算没测到**。
  - 再跑 5.A：那四格回到 1，金额正好加上这张单（借存货 10、进项税 0.70，贷应付 10.70），别的行不变。RR0000099 重复一遍结果相同。
  - 结论：标 0 的格靠 5.A 重算进余额（客户口径维持「月底跑 5.A 再看试算表」）；5.A 是否只重算标 0 的格（还是整期重算）从结果上分不出，
    对我们无影响——我们标了 0，两种都会算进去。
  - TEST2 留下：RR0000098、RR0000099（各 10.70，供应商 ซ014、物料 PN00030），GLBAL 处于「第 1–11 期全算好」状态，留给清单 33、34。
- 回放的预期差异：新代码遇到没有行的科目会多追加一行（旧代码不动 GLBAL），62 套回放里这类差异要按这一条判。

收款分支（`pearnly/collection-cc-chat`）合入时：它的写路在写完 GLJNLIT、关表之后同样 `glbal_mark.mark(account_dir, written)`
（`written` = 刚追加的 GLJNLIT 行 dict，至少有 ACCNUM、DEPCOD、VOUDAT），并把 `glbal_mark.TABLE` 加进它的写前备份；
`dbf_receipt.py` 在本分支已接上，冲突时保留这两处。

## 代录引擎：保存键按下之后出意外不再当成没录成（11-12 晚接手窗口，主会话批进本批）

**线上已有的缺陷（读代码确认）**：`engine.run` 里，保存键按下之后出现配方认不出的弹框 / 画面（`Unexpected` → `_recover` → `NeedHuman`），
以前一律按「需要人处理」收尾：单号留空、调 `abandon()`（取消 / 作废两道闸），批次记 `WAITING`。而 `Runner.add` 对再交来的 `WAITING` 单
（人在任务里点重试、站点按原批次号交回）**清掉开录前行数、换上新草稿从头重录**——前提写在它的说明里：「等人 / 跳过的都是没写成的，
重录不会重复入账」。保存后出意外的单打破了这个前提：Express 其实已存（或答完那个框后就存好），重试就录出第二张。受影响的情形：
- 存档途中 / 存好之后弹出配方没有的框（实例：服务品赊销单的销项税口径框，测试机2 IV0000043；任何新版本 Express 的新提示同理）；
- 存好但画面一时读不到单号、宽限内又没在账套里认出（慢网络盘）；
- 存进去了但读回和草稿不符（`readback_mismatch`）：单号回了，但再交来同样会从头重录；
- 另：认不出的不是弹框而是别的画面时，`abandon()` 可能对一张已存好的单点「作废」（两道闸只核「本批新建」，不核「这张是不是录对了」）。
没有拿到线上发生过的证据（没查生产日志）；有没有客户已经遇到，手上没有数据。

**改法（按类，不只这一个框）**：`engine._after_save_trouble`——保存键按下之后出的任何认不出的情况，先读账套（`_booked`：开录后新增、
对方 + 日期 + 对方单号 + 含税合计都对上的那张，最多等 `SLOW_SAVE_S`）：
- 认出来 → 报已写入 + 单号（回执里带 `screen_left` 记下 Express 上留着的东西），**不放弃、不作废、不点 Express 上的东西**；之后照常逐字段读回。
- 认不出来 → 转人工（原因照旧，多半是 `unknown_screen`），不碰 Express 上的框，回执带 `recheck`；批次据此把这张标成「再交来先读账套」
  （已有的续录机制：`Runner.add` 见到 `recheck` 改成 `ENTERING`、保留开录前行数，`_one` 先 `find_saved`：已存回单号、留了空单头报
  `placeholder_left`，都不重录）。读回不符的单同样带 `recheck`。
- 保存键按下之前的转人工、Express 按规则拦下（`Blocked`，配方认得的框、单没存）照旧放弃 + 作废两道闸。
测试：`companion/tests/test_operator_after_save.py` 3 条（旧引擎下全红）：存好后弹认不出的框 → 已写入 + 单号、不作废；存档途中弹认不出的框 →
转人工、`recheck`、框原样留着；再交来先读账套、没有重录出第二张。`test_operator_engine.py` 的「慢保存、人没碰」对照改成「读账套认出、回单号」。
没改：MR.ERP 代录（另一套引擎，是否同类没核）、服务端如何显示「已写入但 Express 上留着框」（回执 `screen_left`，目前站点不读，只进日志）。

**那个框本身（只给结论与建议，本批不实现）**：进货那一侧已有现成规则——服务品赊购单存档途中问进项税口径，配方按「เกณฑ์สิทธิ」答
（`APTRN.SRV_VATTYP='1'`，直写由 `service_vat.py` 按账套历史定）。销项的同类框（「ภาษีขายของสินค้าบริการในที่นี้เป็นแบบ」，按钮
「เกณฑ์สิทธิ / เกณฑ์เงินสด」）配方里没有。原生销项单两种都有（收款写路记过 `ARTRN.SRV_VATTYP='2'` 收付实现的发票，NLTTEST 15 张），
所以不能一律答权责：62 套统计（S 盘副本 1045 套，只读聚合）——IV `SRV_VATTYP='1'` 251 万行 / 770 套、`'2'` 120 行 / 20 套；自定前缀的销项单
`'2'` 1.7 万行 / 117 套；HS 全是 `'1'` 或 `'-'`。建议照进货的做法，先按这本账套的销项历史定（有服务品的单里的众数，约九成账套只有 `'1'`），
定不了的在引导卡上问一次、按公司记住（引导式第 1 期）；直写销项现在写死 `'1'`（`dbf_fields.audit_fields`），一并按同一规则改。
在那之前代录遇到这个框：先按上面的规则核账套（Express 没答完时这张是金额 0 的空单头 → 转人工 + `recheck`，原因如实写「Express 在问
服务品销项税口径」）。

## 62 套只读回放与原生统计（11-12 晚，测试机1 本机盘副本，子代理跑，只出聚合数；结果 `~/pearnly-devtools/cc-payload-21-replay/results-1112/`）

**新旧回放**（旧 c63458f5f 对新 218686f39；T 账套 101 本，83 本自动取到配置跑完、18 本库存品不够跳过、0 本崩溃；每本 A 37 张 / B 7 张 / C 4 张 / D 4 张）：
- A（不建物料）、D（本年度）：63 本除 GLBAL 外每一步逐字节相同；20 本只差 `ARTRN.SLMCOD`（客户档有销售员，预期）。GLBAL 只有 CALSTA 1 → 0
  （A 1385 格、D 470 格），没有别的栏变；没标上的只有「下一年度」（A 1286 格）——d33aa8f2d 起改为标第 12 + k 位。
- B（随单新建物料）：差异只在 `STMAS.NEGALLOW`（74 本）、换算系数（37 本），以及账套不许负库存时卖新物料改为拒收（多 32 张，连带后面单号顺延、
  买回那件变成新建；拒收原文多列出新物料「… have 0 new」）——都是预期。
- C（缺供应商发票号）：旧代码 332 张全拒收、新代码全照写。
- 这次回放的新代码不含 d33aa8f2d（次年度标位）与 9aecc4048（明细行销售员），这两处的预期差异：A / B / C 里次年度那几格也 1 → 0；20 本客户有销售员的
  销货明细 `STCRD.SLMCOD` 多出销售员。由各自的测试守；真机实写时再核。

**原生分布**（三组：T 账套 101 套只主档、服务器副本 99 套、S 盘副本 1033 套；百分比取 S 盘）：
| 项 | 原生 | 与实现 |
|---|---|---|
| 物料「允许负库存」 | 空 64%、N 30%、Y 6%；账套 NEGALLOW = N 40%、NEGAUTH 填了 22% | 新物料留空跟账套 ✔ |
| 四个单位同码时三个系数 | 99.1% 都是 1 | 写 1 ✔ |
| 到期日 = 单据日 + 账期 | IV 99.4%、RR 99.8% | ✔ |
| 现销借方第一行 | 现金科目 97.9%、银行 1.8% | 默认现金、可给 `cash_account` ✔ |
| 对方类型码所在代码表 | 供应商 ISTAB 46（1001 / 1003 套）、客户 45（967 / 1011） | `PARTY_TYPE_NOT_FOUND` 可放宽成「类型表里有」，留给引导式窗口定 |
| 物料分类 | ISTAB 22（1012 套都有，906 套全在） | ✔ |
| 销售员 | 单头 92% 有；有销售员的单明细 1024 万行同单头、约 4 千行空；单头 = 客户档 64% | 单头取客户档、明细同单头（9aecc4048）✔ |
| 无发票号进货单 | 进项税记录：没有 14.1 万张、空 1295、别的 9240；库存卡 REFNUM 空 35.3 万行、别的 2479 | 三处留空 ✔ |
| GLBAL 24 位 | 第 13–24 位有 0 的 2388 格中 2386 格在 NY 有分录的期；本年度同样；GLJNLIT 用到却没有 GLBAL 行的 380 行 / 5382 万行 | 次年度标位（d33aa8f2d）、缺行追加（4576865b6）✔ |
| 销项服务品口径 `ARTRN.SRV_VATTYP` | IV `'1'` 251 万行 / 770 套、`'2'` 120 行 / 20 套；自定前缀销项单 `'2'` 1.7 万行 / 117 套 | 见下「A. 服务品销项税口径」 |

## 并进载荷 21 的两项（11-12 晚，主会话定；引导式第 1 期要用，不另升版本）

### A. 服务品销项税口径

- **A① 客户档读回带 `srv`**（与 A② 同一批，引导式窗口要求：先上读回服务端就会出题）：这家在本账套 Express 人录赊销单（ARTRN，RECTYP 3、
  价格类型 1 / 2、没作废、不是 CC 写的）上出现过的 `SRV_VATTYP` 1 / 2 并集，`"0"` = 没有历史；ARTRN 读不了就不带。心跳目录
  （`catalog_probe.probe_customers`）与按需主档读取（`express_masters`）都带，同供应商的 `srv`。`service_vat.history` 改成按表通用
  （进项 APTRN 按供应商、销项 ARTRN 按客户），进项结果不变。测试 `test_service_vat_sales.py`。
- **A② 载荷 `srv_vattyp` 写进销货单**：表头带 `srv_vattyp = 2`（或 `cash_basis`）且这张单有服务品（有非库存行，或没有明细）→ 照原生收付实现记：
  单头 `SRV_VATTYP` 2、`VATDAT` 空，税那一行（SEQIT 6）贷「未到期销项税」`ISINFO.ACCNUM27`，开单不写销项税记录（读回与写后核对都按「不写」核）；
  `ACCNUM27` 没设或不能记账 → 不写、转人工（`ACCOUNT_NOT_POSTABLE`，同进货口径 2，已有词条）。不带键、带 1、全是库存品 → 与以前写死的一样。
  原生证据（11-12 晚，测试机1 S 盘副本 136 套 / 服务器副本 2 套，只读聚合，`srv2vat62.py`，结果 `results-1112/srv2vat-*.json`）：税额 > 0 的口径 2 销项单
  税贷 ACCNUM27 4124 张、ACCNUM10 3 张；开单不写销项税记录 5095 / 46；VATDAT 空 5141 / 5141；口径 1 对照：贷 ACCNUM10 141803、写记录 139599 / 9345。
  测试 `test_service_vat_sales_write.py` 4 条（旧代码下口径 2 那两条红）。没做：收款时把未到期销项税转成销项税（CC 收款写路对口径 2 的发票本来就不收，
  `express_receivables.CASH_BASIS_VAT`，主会话 11-11）；现销 HS 带口径 2 照同一规则写（原生 HS 没见过 2，服务端不会出这题）。
- 键名已和引导式窗口定（11-12 晚）：沿用 `srv_vattyp` 同名键、不进 GUIDED_KEYS；销项单带 `'2'` 时服务端单独盖 21（`'1'` 与旧版写法相同、不盖）。

### B. 代录 × 新物料（已做）

- 写账线（`worker._handle_operator_task`）：代录单只带 `OPERATOR_LINE_KEYS` = 单位码 `unit_code`、科目组 `acccod`、分类 `stkgrp`、
  允许负库存 `negallow` 这四个行键时照录（`guided.operator_allowed`）；带了表头 / 对方块的新键或别的行键（`cost_choice`、`cost`）照旧拒收转人工。
- 代录开工前建档（`express_operator/masters.prepare`）：先照直写同一套核值（`guided.invalid_values`：单位码在单位表、分类在分类表、科目组合格），
  不合格整单交人（原因码同直写：`UNIT_CODE_NOT_FOUND`、`ITEM_GROUP_NOT_FOUND`、`STOCK_ACC_GROUP_UNFIT`），账套一个字节不动、不关 Express；
  再照直写把 `unit_code` 换进 `unit`（建档、录单都按这个码）；新库存品按这一行的科目组（没给用整单的 `stock_acccod`）、分类、允许负库存建
  （`product_master.ensure_stock_item`，与直写 `stock_purchase` / `stock_sale` 同一个函数）。
- 测试：`companion/tests/test_operator_masters.py` 两条（逐栏核建出的 QUCOD / ACCCOD / STKGRP / NEGALLOW；值不在账套里交人、主档表不动）、
  `test_guided_payload.py` 一条（哪些键代录能带）——旧代码下都红；跨边 `tests/test_operator_guided_keys_contract.py`（进开发检查）：
  服务端 `finalize_payload` 收口的代录载荷盖 21 → CC 放行 → 四个键交到建档（单位码已换进 unit）、带别的新键不放行；`masters.py` 建档传四个键。

**接缝约定（请引导式窗口照抄进接口约定第 8 节，CC 桌面版窗口负责）**：
1. 代录单（`entry_mode = operator`）只可以带四个行键：`unit_code`、`acccod`、`stkgrp`、`negallow`（取值口径同直写）；服务端照常盖 21、照派，
   只派给报过支持 21 的电脑。2. 带了其余新键（表头 `guided` / `due_date_be` / `cash_account`，行上 `cost_choice` / `cost`，对方块 `type` / `payterm`）
   的代录单 CC 拒收（`PAYLOAD_INVALID`，转人工），服务端不该发。3. 值不在账套里：CC 回直写同一组原因码、整单不建不录。
4. 键名单两边各一份：CC `payload_contract.OPERATOR_LINE_KEYS`；服务端若要常量，命名 `OPERATOR_GUIDED_LINE_KEYS` 并在跨边测试里加一条相等断言。

## 不再弹「账套正被占用」询问框（11-13 真机发现，阻断性，并进 1.12.21）

**现象**（11-13 11:24–11:38，测试机1、测试机2 两台 1.12.21 集成测试包，ERP 成员连 testerp 实验栈）：测试机1 用正版 Express 打开
`69EXP\TEST` 的赊销单窗口后，两台 CC 的心跳与写账领单一起停了（日志停在 11:24:51 / 11:25:37，进程活着、线程全在等待），按需请求照常；
Express 11:36:52 关闭后也没恢复。测试机2 上 CC 开着一个「账套正被占用」框（Win32 枚举窗口只读看到，截图 `C:\RecSetup\cc21\busy-prompt.png`），
11:38:52 用界面自动化点「重试」后 1 秒内心跳恢复、随即写成收款 RE0000009（收款窗口那笔）与本任务排的三张单。

**原因**（读代码证实，不是新的按表探占用）：有人值守的单写前只试 ISRUN（`gui_tray_workers.handle_task` → `account_lock.is_account_busy`），被占就
按「等账套」回报、放回排队，再弹框并在取任务线程里 `while not stop` 每 0.2 秒等人点、没有时限（`_ask_busy`）；等的同时盖存活时标，看门狗不重启它。
心跳与领单在同一条线程，所以一起停；放回排队的单被另一台领走，同样弹框，两台都停。**这段 1.12.20 就有**（线上同样会这样，只是线上电脑前通常有人点）。

**改法（主会话 11-13 定方案甲）**：CC 不再弹这个框，取任务线程也不再自己探 ISRUN。不论有没有人在电脑前（服务端的 attended 不再起作用），
一律走写入时的按表探占用：一个字节不动、按「等账套」回报，服务端歇 30 秒再派，Express 关了自动写。询问框的代码（`BusyPrompt`、`_release_then_ask`、
终止记忆、托盘弹窗）删干净；四语词条留着、文案不改。测试：`companion/tests/test_busy_no_prompt.py` 两条——LINE 收款单 × Express 开着 → 不弹框、
取任务线程不等、账套一字不动，关了以后写成；同一本账套两台电脑 × Express 开着 → 两台都按「等账套」回报不卡，关了以后只写一次（旧代码下这两条
都卡在等人点）；`test_write_dispatch.py` 删掉只为询问框存在的 4 条、两条改掉对框的依赖，契约测试那条源码断言随之改。
真机：测试机2 11:38:52、测试机1 11:47:46.7 点「重试」后 1 秒内心跳恢复；卡着的那件已被测试机2 写成 RE0000009，测试机1 的旧租约没再写，没有重复。
**电脑前的人在哪看「在等 Express」、在哪撤回**（11-13 只读调研，82d3f7aa0）：ERP 各单据列表与单据页、我的收款、ERP / Cowork 的 CC 对话、Cowork 录入
工作台有显示；ERP LINE 单据、Cowork LINE 没有；ERP LINE 收款只在当场那条回复里说。排队中能撤回：ERP 收款（LINE 卡片、CC 对话）、ERP 出入库作废；
ERP 采购 / 销售作废不取消排着的写账件；Cowork 已放行的推送任何入口都撤不了——这几处今晚不补，记为已知缺口。

## 1.12.21 发布说明草稿（三批合一个版本；先不标发布；11-12 晚接手窗口更新）

给发布记录与部署记录用，发版时照实改日期与 SHA：

- **版本**：CC 桌面版 1.12.21，从 main 打（标签 `companion-v1.12.21`，先 staging 再正式）。随集成分支 `pearnly/integration-1112` 一起合入，
  包含三批：占用探测（`pearnly/express-busy-probe-2`）、载荷 21 与写入缺陷、科目余额标待重算（`pearnly/cc-desktop-payload-3a2e3d`），
  以及验证中修的提交（`pearnly/cc-desktop-verify-7f2d4c`，叠在载荷分支上）。同一个包里还有集成分支上别的 companion 改动（收款写路、对话账套等），
  发版前由主会话核 `companion/` 相对 1.12.20（main 6275e4999）的全部差异。
- **上线顺序**：① 部署服务端（main，`scripts/production/deploy.sh` 先 `--dry-run`）；② 紧接着发 CC 桌面版 staging → 正式；同一次发布做完。
  ①②之间没有电脑报 21 的这一小段，带新键的单停在「等电脑更新」，电脑经心跳自动更新后照写；不带新键的单照常写。服务端这次不发新键
  （引导式第 1 期还没上），所以实际上线后所有单都不带新键，新键只在第 1 期上线后才会出现。
- **对客户可见的变化**（所有入口）：
  - Express **开着用到这些表的窗口**（单据、主档、科目表、报表选择、设置、批处理窗口）时整单一个字节不动，关掉窗口后自动写；Express 停在主界面不挡。
  - 新物料不再写死「允许负库存」，跟账套；账套不许负库存（或扣成负库存要批准）时，卖新物料 / 卖超结存的单停下并说明原因（库存不足）。
  - 销售单带客户档上的销售员。
  - 没有供应商发票号的进货单照写。
  - CC 写的分录会把对应的科目余额标「待重算」，Express 重算后即进科目余额与试算表（以前要整本重算才进）。
- **线上 1.12.20 的已知问题（修复随 1.12.21）**：Express 开着单据窗口 × 有人值守的单（LINE 收款也算）× 没人点「账套正被占用」框 →
  这台电脑心跳与领单全停，放回排队的单被同账套的其他电脑领走也接着卡；点框里「重试」即恢复。1.12.21 起不再弹框。
- **部署记录要写的**：
  - 升级前旧版 CC 写过分录的账套，科目余额仍没标：客户要在 Express 跑一次「5.บัญชี → A. คำนวณยอดเปลี่ยนแปลง」后试算表才含这些单。
    从推送记录里列出 10 月以来 CC 直写过的租户 / 账套，逐个告知。
  - 旧版 CC 在升级前，Express 开着账套时每 30 秒仍会「备份 → 写 → 回滚」一轮。发版后回读各电脑的 `companion_version`，低于 1.12.21 的逐台列出，直到清零。
  - 回执里出现 `meta.glbal_unmarked` 的单（没标上的科目余额格：日期不在本年度也不在下一年度、计算状态是异常值、账套没有 GLBAL）照写成功，要人跑一次重算；部署后一周查一次线上有多少。
- **部署后核对项**：一台真实租户电脑（或测试机）升级到 1.12.21 后：① 心跳版本号；② Express 开着赊购单窗口时推一张，回执等账套、账套哈希不变；
  ③ 关窗后写进去，Express 里打开；④ 这张单分录科目的 GLBAL 那一位是 0，跑重算后试算表含它。
- **回退**：服务端回上一个发布标记；CC 回 1.12.20（版本 ≤ 20 的载荷接口没变；服务端回退后不再发 21）。回退后 1.12.21 期间写的单不受影响
  （GLBAL 标 0 只是让 Express 重算，不改金额）。
- **还欠着的验证**：见[测试机清单](2026-11-12-cc-desktop-payload-21-testpc-checklist.md)（11-12 晚按新事实改过）与本窗口结果表；服务端出题那一半等引导式第 1 期。

## 新增文案（14 条，`python3 scripts/check-copy.py --report`：新增 14、改动 0、拦截 0）

七个新原因码各一句，Cowork（`intake.error.<码>`）与 ERP（`express.error.<码>`）同文；CC 桌面版每个错误码都要有词条
（`tests/test_cowork_reason_language.py`），否则界面原样显示英文细节。引导卡上的题面与「{品名}：新物料，没有库存」等词条归引导式窗口。

| 码 | 中文 | English | ไทย | 日本語 |
|---|---|---|---|---|
| `UNIT_CHOICE` | 单位未定 | Unit not set | ยังไม่ได้กำหนดหน่วยนับ | 単位が未設定です |
| `COST_BASIS_CHOICE` | 出库成本未定 | Cost of goods sold not set | ยังไม่ได้กำหนดต้นทุนขาย | 売上原価が未設定です |
| `CASH_ACCOUNT_CHOICE` | 现金科目未定 | Cash account not set | ยังไม่ได้กำหนดบัญชีเงินสด | 現金勘定が未設定です |
| `UNIT_CODE_NOT_FOUND` | Express 中没有此单位 | Unit not found in Express | ไม่พบหน่วยนับนี้ใน Express | Express にこの単位がありません |
| `ITEM_GROUP_NOT_FOUND` | Express 中没有此物料分类 | Product group not found in Express | ไม่พบกลุ่มสินค้านี้ใน Express | Express にこの商品グループがありません |
| `PARTY_TYPE_NOT_FOUND` | Express 中没有此类型 | Type not found in Express | ไม่พบประเภทนี้ใน Express | Express にこの種別がありません |
| `INVALID_DUE_DATE` | 到期日早于单据日期 | Due date is before the document date | วันครบกำหนดอยู่ก่อนวันที่เอกสาร | 支払期日が伝票日付より前です |

共用件登记：`guided.py`、`guided_keys.py` 加进 `scripts/impact/shared-parts.toml` 的 `cc-dbf-write`。

## 约定之外自己定的细节（请引导式窗口照此对接）

- `guided` 只认布尔 `true`；`cost` 只认十进制字符串（数字类型拒收）；`payterm` 只认整数类型。
- `cost_choice` 只许出现在销货的库存行（`item_mode = stock_sale`）上；出现在别的行 → `PAYLOAD_INVALID`。`acccod` / `stkgrp` / `negallow`
  出现在不新建物料的行上不拒收、不读（服务端可以整单都带）。`acccod` 只在库存行上核是否合格。
- 「没有成本基础」= 要随单新建的物料，或已有物料总结存 ≤ 0 且标准成本 `STNPR` ≤ 0（与写单时三分支同一个判据）。
- `UNIT_CHOICE` 的判法：票面没有单位的行不问（按库存单位写）；行上的单位（文字解析出的码或 `unit_code`）是这件物料的库存单位或三个包装单位
  之一，或带了 `factor`（> 0 且 ≠ 1）→ 不问；其余问。新建库存品：单位文字解析不出、又没给 `unit_code` → 问。
- 负库存先核，过了才看单位 / 成本 / 现金科目；一次回一类原因码（单位 → 成本 → 现金科目）。
- 明细不可信（`items_status` 不是 `ok`）的单只记表头、不新建物料：随单新建的行不核负库存，也不问单位 / 成本。
- 新建现金客户档（`เงินสด`）也照 `customer.type` / `payterm` 写（给了的话）。
- 服务端读回比分录（Cowork `push/express/pushed.py`、ERP 的写后核对）要认 `cash_account`（现购贷方 / 现销借方）与 `due_date_be`；本任务没改读回。

## 原生证据

| 写法 | 证据 | 位置 |
|---|---|---|
| 到期日 = 单据日 + 账期（`DUEDAT`、`PAYTRM`） | 62 套真账只读核实（10-08） | `docs/work/2026-10-08-push-account-set-rules.md`；`account_rules.due_date` |
| 现购贷现金、现销借现金（`ISINFO.ACCNUM00`），不碰应付 / 应收 | 真账 2165 张 HP、1.4 万张 HS 无一例外（10-08） | 同上；`dbf_writer.py`、`dbf_sales.py` 原注释 |
| 包装单位：`TQUCOD` / `TFACTOR` / `XTRNQTY` = 数量 × 系数 | S 盘真账 6902ASC / 6907GIZ 只读核对（10-25） | `stock_units.py` 文件头；本任务没改换算，只换了单位从哪来 |
| 负结存出货按单位成本结转（`XUNITPR`、借成本 / 贷存货） | 真账 61EXP / 61SKBAC 负结存出货行 `XUNITPR` = `STNPR` | `stock_sale.standard_cost`；人填的成本走同一支 |
| 新建库存品 `NEGALLOW` 留空 | 本机 SINCTEST 副本（主档是原生的）：库存品 41 件，34 件空、7 件 Y、没有 N；账套 `ISINFO.NEGALLOW = Y`、`CHKNEGLEV = 0` | `~/pearnly-devtools/cc-payload-21-replay/probe.py` 的输出 |
| 四个单位同码时系数是 1 | 同上：四个单位同码的 29 件里 28 件三个系数都是 1（另 1 件计数系数 0） | 同上 |
| 物料分类在代码表 `ISTAB TABTYP = 22` | 同上：在用的 6 个分类 6 / 6 都在 22 段 | 同上；`guided.ITEM_GROUP_TABTYP` |
| 销售单单头带销售员、取自客户档 | 测试机只读统计（S 盘副本 1033 套）：赊销单 90%、现销单 93.5% 带销售员，客户档 59% 带 | 覆盖表第 9.2 节（`pearnly/cc-guided-entry-51673b` 11301ffec） |
| `ISINFO.NEGAUTH` = 扣成负库存要哪一级批准 | 界面实看（库存系统设置第 10 项「ตัดสินค้าติดลบ ขออนุมัติระดับ」，测试机 TEST 账套）；原生 22.5% 的账套填了（`5` 占 21.4%、`9` 占 1.1%） | 覆盖表第 9.3、9.5 节 |
| 进货单可以没有供应商发票号 | 测试机只读统计：原生赊购单 17%、现购单 50% 的发票号是空的 | 覆盖表第 9.2 节 |
| Express 拦负库存是拒绝不是警告（4224 / 4091） | 服务器测试账套 `70EXP\test` 实录（10-25） | `stock_guard.py` 文件头 |

**没有原生证据的（单列，没有硬写）**

- 对方类型（`SUPTYP` / `CUSTYP`）在 `ISTAB` 的哪一段：本机一套看着是 46（供应商）/ 45（客户），只有一套、名称多为空，不算证据。
  所以 `type` 只收「这本账套已有对方档用过的类型码」，别的回 `PARTY_TYPE_NOT_FOUND`；服务端的候选也应只列已有档用过的。
  查清代码表后可放宽（测试机清单第二部分第 4 项）。
- 四个单位同码时系数为 1、`NEGALLOW` 默认空：只有 1 套；62 套分布与新建物料界面默认值在测试机清单里补。
- Express 界面上新建物料时这三个系数的默认值：没实测（测试机清单）。
- 没有发票号的原生进货单，进项税记录 `ISVAT.REFNUM` 是空的还是 Express 填了别的（例如单号）：手上只有「单头发票号为空」的占比，
  没有这类原生单的逐栏样本（本机一套的进货单都是我们写的、都有发票号）。现在三处都留空——与约定 8.3 一致；测试机清单第二部分第 7 项
  找原生同类记录逐栏对照，对不上再改。
- 账套要批准（`NEGAUTH` 非空）而物料自己标了允许负库存（`STMAS.NEGALLOW = Y`）时 Express 还要不要批准：没实测。现在沿用已有规则
  「物料级盖过账套级」，这种物料不核（测试机清单第一部分第 30 项实看）。
- 销售员只写了单头 `ARTRN.SLMCOD`。库存卡 `STCRD` 也有 `SLMCOD` 一栏、销售统计 `STSTA` 也有：原生销售单的明细行带不带销售员、
  按销售员的报表读哪张表，手上没有证据（覆盖表第 9 节只统计了单头；本机一套的单据是我们写的、全空），所以没写这两处。
  测试机清单第二部分第 6 项补统计，结果出来再定要不要补明细行。

## 验证

| 版本 | 环境 | 场景 / 命令 | 结果 |
|---|---|---|---|
| 工作树（本提交） | 本机 macOS，Python 3.11，dbf 0.99.11 | `companion`：`python -m pytest -q -p no:cacheprovider tests` | 1650 通过、74 跳过、9 失败。9 条失败是本机没装 PySide6（`test_write_dispatch` 8 条、`test_mrerp_login` 1 条），基线同样失败，要等 Windows 的 Companion checks |
| 同上 | 同上 | 新测试 `companion/tests/test_guided_payload.py` 47 条；改写 `test_stock_guard.py`（+5）、`test_stock_item_defaults.py`（3） | 新代码全过；**旧代码（c63458f5f）上**：`test_guided_payload.py` 整个文件导入失败（没有 `guided`），另 6 条逐条失败 |
| 同上 | 同上 | G-A15：`companion/tests/test_sales_salesman.py` 4 条 | 新代码全过；实现前 3 条失败（另 1 条是「没有这一栏照旧」） |
| 同上 | 同上 | 要批准：`test_stock_guard.py` +2、`test_dbf_stock.py` +1；缺发票号：`test_purchase_no_invoice_number.py` 2 条、`test_purchase_adapter.py` 改 1 条 | 新代码全过；实现前这 5 处失败（另 1 条只有表头的进货单随拒收去掉才写得进） |
| 同上 | 本机 Python 3.12 | `python3 -m unittest tests.test_companion_contract tests.test_express_operator_shared` | 112 通过（新加 `GuidedPayload` 7 条 = 约定 8.5 的六条 + 回执键过服务端净化） |
| 旧 c63458f5f 对 新工作树 | 本机，三本账套副本 | 离线回放（下节） | 不带新键、不新建物料的 111 张：每一步每个文件逐字节相同 |

### 离线回放（新旧逐字段）

脚本与结果：`~/pearnly-devtools/cc-payload-21-replay/`（`replay.py` 编排、`run.py` 在指定源码树下写、`make_books.py` 造合成账套、
`summary.json` 结果）。做法：同一串载荷，分别用旧代码（`git archive c63458f5f`）和新代码写进同一本账套的两份副本，每写一张比
结果（成功 / 单号 / 错误码 / 逐行回执）和账套目录里每个文件的 sha256，最后把全部 DBF 逐表逐字段导出来比。

语料（**不是原生语料**：载荷是脚本造的，账套只有本机这一套真结构副本加两本合成账套；62 套那一遍还欠着，见测试机清单）：

| 账套 | 是什么 |
|---|---|
| SINCTEST | 本机唯一的真账套副本（`~/Documents/pearnly-backups/…20260929.zip`，167 个文件；主档原生，单据是压测时我们写的）；账套允许负库存 |
| synth-allow | 仓库测试夹具造的合成账套（两仓、STLOC、三件库存品含一件带包装单位）；允许负库存 |
| synth-forbid | 同上，`ISINFO.NEGALLOW = N` |

| 序列 | 内容 | 张数 | 结果 |
|---|---|---|---|
| A：不带新键、不新建库存品 | RR / HP / IV / HS：只有表头、服务行、按码的库存行、带仓、带来源换算率、混行、新供应商、新客户、单位解析不出、包装单位、没有单位、卖超结存、卖成负、版本 20 带杂散新键、六轮进销 | 37 张 × 3 本 = 111 | 三本都是：结果逐张相同，**每一步每个文件逐字节相同**，最后逐字段零差异。写成 37 / 37 / 28 张（synth-forbid 有 9 张新旧同样回 `STOCK_INSUFFICIENT`） |
| B：不带新键、随单新建库存品 | 进货新建、单位猜的、销货新建、新旧同单、卖刚买的、买刚卖的、之后一张普通进货 | 7 张 × 3 本 = 21 | SINCTEST、synth-allow：结果逐张相同；差异只有 `STMAS.NEGALLOW` 4 处（Y → 空，就是新建的 4 件）。synth-forbid：两张「卖新物料」的单旧代码写成负数、新代码回 `STOCK_INSUFFICIENT`（G-B6 的预期变化），其余差异都是这两张单没写带出来的 |

G-A15（销售员）另加一本账套 **SINCTEST-slm**：同一本真账套副本，只把客户 S001 的客户档销售员填成 `SL01`。序列 A 37 张都写成、
结果逐张相同，逐字段差异只有 `ARTRN.SLMCOD` 19 处（写成的 20 张销售单里 19 张是这位客户的，另 1 张是当场新建的客户、留空）；
序列 B 差异是 `ARTRN.SLMCOD` 3 处加新建物料的 `STMAS.NEGALLOW` 4 处。客户档没有销售员的另外三本，加了这项之后重跑，结果与上表完全相同。

**追加三项各自带来的差异（预期只有这三处变化，实际如此）**

| 项 | 回放 | 结果 |
|---|---|---|
| G-A15 销售员 | SINCTEST-slm（上段） | 只多 `ARTRN.SLMCOD`；客户档没有销售员的账套零差异 |
| 负库存要批准 | 新增账套 **synth-approval**：同 synth-allow，只多 `ISINFO.NEGAUTH = 5`。序列 A / B | 旧代码 37 张全写（把库存写成负数）；新代码写 28 张、拒 9 张——**拒的 9 张与 synth-forbid（账套不许负库存）拒的完全相同**，新代码在两本账套上写出的全部表逐字段相同（只差 `ISINFO` 本身）。没填 `NEGAUTH` 的账套零差异 |
| 缺供应商发票号 | 新增序列 **C**：4 张没有发票号的进货单（RR / HP，只有表头、库存行、服务行）× 5 本 = 20 张 | 旧代码 20 张全部拒收（`PAYLOAD_INVALID`，子码 `MISSING_REF_NO`）；新代码 20 张全部写成。带发票号的单（序列 A / B）零差异；「与带发票号的同一张单只差三处 `REFNUM`」由 `test_purchase_no_invoice_number.py` 守 |

加了这三项后把 A / B 在原来三本账套上重跑：结果与上表逐项相同（A 仍然每一步每个文件逐字节相同）。

B1（系数）在回放里没有差异——三本账套的模板物料系数本来就是 1；它由 `test_a_new_stock_item_does_not_copy_the_pack_factors_of_the_template` 守。

### 共用件连带回归（`scripts/check-impact.py`）

命中 `cc-desktop`、`express-rules`、`cc-dbf-write`、`cc-operator-target`。测试机今天别的窗口在用、本任务不许连，凡是要测试机或真 Express 的
入口都记「没测到」，进测试机清单；离线的已跑。

| 入口 | 结果 |
|---|---|
| `cc.desktop.write`、`erp.cc.write`、`cowork.express.push`、`erp.cc.stock-entry`（自动冒烟，要测试机 C 盘 T 账套） | 没测到（不许连测试机）。离线：companion 全量、回放 132 张 |
| `cc.desktop.operator`、`cc.desktop.operator.mrerp`、`cc.operator.page`、`erp.cc.chat-push`、`erp.mrerp.direct`（离线） | 通过：对应测试随 companion 全量与本地闸门跑（闸门结果见提交后的 CI） |
| `erp.cc.stock-void`、`erp.cc.stock-transfer`、`erp.cc.pos-day`、`erp.cc.pos-refund`、`erp.trade.line-stock`（人工，测试机） | 没测到。库存单 / 调拨 / 作废的负库存核对没改口径（`new_rows=False` 保持原行为，`test_dbf_stock`、`test_dbf_transfer`、`test_dbf_void` 通过）；POS 日汇总不新建物料 |
| `erp.cc.connect`、`erp.cc.masters`、`erp.cc.companies`、`cc.desktop.bridge`、`cc.desktop.places`、`cc.mrerp-login`（人工） | 没测到。本任务没改连接、主档读取、位置、登录的代码 |
| `cowork.cc.channel`、`cowork.mrerp.push`、`cowork.companies`、`cowork.connections`、`cowork.history`、`cowork.cc-chat.push`、`cowork.cc.troubleshooting`、`cowork.line.item-questions`（人工） | 没测到。服务端只加了常量、盖版本的一个分支、回执两个键的放行、原因码名单；不带新键的载荷 `finalize_payload` 结果不变（契约测试守） |

## 未覆盖 / 下一步

1. 测试机实写（每个新键至少一张、G-B6、B1）与 Express 界面核对——[清单](2026-11-12-cc-desktop-payload-21-testpc-checklist.md) 第一部分。
2. 62 套只读回放与原生统计——清单第二部分（可先于实写）。
3. B2 探表单后改配方——清单第三部分。
4. 版本号、`companion/README.md` 条目：等主会话告知；合并顺序建议 `pearnly/express-busy-probe` 先合、本分支变基
   （交叠：`worker.py`、`ack_rules.py`、`version.py`、README；`pearnly/collection-transfer-rule` 只在 `tests/test_companion_contract.py` 各自追加）。
5. 顺手看到、没动的：ERP 单据页解析库存不足原文的正则（`transactions.js`）在多行时把行间的逗号连在「有」那一格后面（原有问题）。
