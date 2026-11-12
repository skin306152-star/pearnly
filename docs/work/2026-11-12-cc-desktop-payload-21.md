# CC 桌面版载荷 21「引导录入」与三处写入缺陷

- 属于 [CC 引导式录入](2026-11-12-cc-guided-entry.md)（用户 11-12 批准）；依据是评估窗口的
  [Express 必填覆盖表](2026-11-12-cc-guided-entry-express-coverage.md) 第 8 节「CC 桌面版载荷接口约定」、第 2.4 节、第 5 节
  A5 / A8 / A10 / A11 / A13 / A2 / R2 / B1 / B2 / B5 / G-B6（两份文档在分支 `pearnly/cc-guided-entry-51673b`）。
- 分支 `pearnly/cc-desktop-payload-3a2e3d`，基线 `origin/main` c63458f5f，工作树 `.claude/worktrees/cc-desktop-payload-3a2e3d`。
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

行为变化（对所有入口，不看版本）：① 新建库存品「允许负库存」留空、三个换算系数写 1；② 账套不许负库存（或行上 `negallow = N`）时，
「卖随单新建的物料」的销货从「写成负数」变成整单不写、回 `STOCK_INSUFFICIENT`；③ 客户档有销售员的销售单单头带上销售员（G-A15）。
其余全部只在版本 21 的载荷上生效。B2 已由主会话改列为 G-B2′（票面号没进账，先在测试机试手填单号再定做法），仍不在本批。

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
| Express 拦负库存是拒绝不是警告（4224 / 4091） | 服务器测试账套 `70EXP\test` 实录（10-25） | `stock_guard.py` 文件头 |

**没有原生证据的（单列，没有硬写）**

- 对方类型（`SUPTYP` / `CUSTYP`）在 `ISTAB` 的哪一段：本机一套看着是 46（供应商）/ 45（客户），只有一套、名称多为空，不算证据。
  所以 `type` 只收「这本账套已有对方档用过的类型码」，别的回 `PARTY_TYPE_NOT_FOUND`；服务端的候选也应只列已有档用过的。
  查清代码表后可放宽（测试机清单第二部分第 4 项）。
- 四个单位同码时系数为 1、`NEGALLOW` 默认空：只有 1 套；62 套分布与新建物料界面默认值在测试机清单里补。
- Express 界面上新建物料时这三个系数的默认值：没实测（测试机清单）。
- 销售员只写了单头 `ARTRN.SLMCOD`。库存卡 `STCRD` 也有 `SLMCOD` 一栏、销售统计 `STSTA` 也有：原生销售单的明细行带不带销售员、
  按销售员的报表读哪张表，手上没有证据（覆盖表第 9 节只统计了单头；本机一套的单据是我们写的、全空），所以没写这两处。
  测试机清单第二部分第 6 项补统计，结果出来再定要不要补明细行。

## 验证

| 版本 | 环境 | 场景 / 命令 | 结果 |
|---|---|---|---|
| 工作树（本提交） | 本机 macOS，Python 3.11，dbf 0.99.11 | `companion`：`python -m pytest -q -p no:cacheprovider tests` | 1645 通过、74 跳过、9 失败。9 条失败是本机没装 PySide6（`test_write_dispatch` 8 条、`test_mrerp_login` 1 条），基线同样失败，要等 Windows 的 Companion checks |
| 同上 | 同上 | 新测试 `companion/tests/test_guided_payload.py` 47 条；改写 `test_stock_guard.py`（+5）、`test_stock_item_defaults.py`（3） | 新代码全过；**旧代码（c63458f5f）上**：`test_guided_payload.py` 整个文件导入失败（没有 `guided`），另 6 条逐条失败 |
| 同上 | 同上 | G-A15：`companion/tests/test_sales_salesman.py` 4 条 | 新代码全过；实现前 3 条失败（另 1 条是「没有这一栏照旧」） |
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
