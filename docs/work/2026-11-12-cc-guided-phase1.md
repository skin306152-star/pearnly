# CC 引导式录入第 1 期：施工记录

- 分支 `pearnly/cc-guided-phase1`，基线集成分支 `pearnly/integration-1112` ffeb3a5d6（集成分支在新 main 上重叠后跟着变基）。
- 方案与批复：分支 `pearnly/cc-guided-entry-51673b` 的 `docs/work/2026-11-12-cc-guided-entry.md`（第 12 节批复、第 13 节交叠表）与
  `docs/work/2026-11-12-cc-guided-entry-express-coverage.md`（A 类 14 条、第 6 节机器检查、第 8 节载荷 21 接口约定）。两份文档随第 2 批带进本分支。
- 范围：业务菜单 + 后台出题的一张引导卡，Cowork 直写、Cowork 代录、ERP 代录的采购与销售。ERP 直写对话收单、复核表、结果表在第 2 期，收款在第 3 期。

## 批次

| # | 内容 | 主要文件 | 状态 |
|---|---|---|---|
| 1 | Express 必填字段登记表与检查 | `K/express_rules/contracts/required_fields.py`、`tests/test_express_required_coverage.py`、`scripts/check-express-defaults.py`（+ 基线、测试）、`write_precheck/contracts.py` 加题类 | 本提交 |
| 2 | 引导卡服务端契约 + A1–A14 出题规则 | `K/write_precheck/guide.py`（新）、`K/express_rules/push/choices.py`、`sales_mapper.py`；两产品判定源接线待做 | 契约与共用规则已提交，产品接线未做 |
| 3 | 模型侧：删自出题说明、`ask_user` 录单流程拒绝、`pearnly_guide` 与页面作答接口 | `K/assistant/**`、`C/assistant/**`、`E/express/**` | 等 A 线账套那批进集成分支 |
| 4 | 代录开始卡改同一张卡（审计第 4 批、D-5 / D-6）、B3、B4 | `K/express_operator/**`、`ack_rules.py`、`E/express/operator.py` | 未开始 |
| 5 | 回执：`unit_guessed`、读回认 `cash_account` / `due_date_be`、`*_CHOICE` 转题、`meta.shortages` / `approval` | `cc_desktop/**`、两产品回执处理、ERP `transactions.js` | 未开始 |
| 6 | 前端：菜单、引导卡、两处改已有文案登记、流程 `cc.guide.*` | `frontend/assistant/src/**` | 未开始 |

## 第 1 批：Express 必填字段登记表

做了什么：

- `express_rules/contracts/required_fields.py`（纯契约）：赊购 RR、现购 HP、赊销 IV、现销 HS、明细行（STCRD / STLOC）、新供应商、新客户、新库存品、新服务品
  九张表，每个 Express 字段一行：自动定的来源（按顺序）、引导卡题类、复核卡列、允许的默认与依据、对应缺口编号。收款 RE 随第 3 期登记。
- `WRITERS`：CC 桌面版写入模块 → 它写哪几种单据。测试用 AST 从这些模块取出实际写的全部字段（dict 字面量的键、下标赋值的键），
  没登记就红；载荷 21 的新键（`GUIDED_KEYS`，服务端与 CC 桌面版两边）逐个登记到它落的字段。
- 覆盖表 A 类里落在 Express 字段上的缺口（A1、A2、A5、A6、A8、A10、A12、A13）各有一行带题类，且不许带静默默认。
- `write_precheck.contracts` 加五个题类：`cash_account`（A2、A13）、`unit`（A5）、`new_item`（A10）、`cost_basis`（A11）、`detail`（A7），
  以及按方向分组的 `new_party`（A8）；`detail` / `cost_basis` / `new_party` / `new_item` 只管这一张、不记住。
- `scripts/check-express-defaults.py`：扫服务端映射与 CC 桌面版适配、写入模块里的 `x.get(键, 默认)` / `x.get(键) or 默认`，
  默认是**编出来的值**（非空常量如 `1`、`"PCS"`，或常量名 / 属性）且键不在登记表 `DEFAULT_KEYS`（带依据）里的，按文件、按键计数进基线
  `scripts/express-defaults-baseline.json`，只许减少。存量 32 处（例：`dbf_detail.py` 数量缺了记 1，即缺口 G-R3）。
  与覆盖表第 6 节的写法有一处有意收窄：空值归一（`or ""`、`or 0`、`.get(k, None)`、空表）不算——第一版照写全算进去是 175 处，
  绝大多数是 `str(x.get(k) or "").strip()` 这种「没有就统一成空、后面照『没有』处理」的写法，第 2 批自己写的新代码也被它拦了 5 处，全是噪声；
  收窄后剩下的才是真把缺的值编成一个具体值的地方。
- Development checks 加一步跑上面三项。

结构按验收约定定稿（主会话 11-12 转验收窗口，`docs/development/acceptance-testing.md` §6）：顶层 `REQUIRED_FIELDS` 是字典字面量，键 `"express:RR"`、
`"express:HP"`、`"express:IV"`、`"express:HS"`、`"express:LINE"`（明细行）、`"express:APMAS"`、`"express:ARMAS"`、`"express:STMAS"`、`"express:STMAS_SERVICE"`；
每行 `field` / `auto` / `ask`（题目代号，字符串字面量，不出题不写）/ `review` / `default` / `gap` / `note`。验收登记只引用键；题目代号改名先告诉主会话。
`review` 第 1 期只登记列名，断言待第 2 期（文件头写明）。文件各管各的：本表、`check-express-defaults.py`、基线、覆盖测试归本任务；`tests/acceptance/*`、`check-acceptance.py` 归验收。

`MODE_GAPS` 如实登记某种录入方式下没覆盖的字段（主会话 11-12 第 1 条）：

- **代录 × 新物料**（`express:STMAS` 的 `QUCOD` 单位、`ACCCOD` 科目组、`STKGRP` 分类、`NEGALLOW`）：代录开工前建主档复用直写的 `product_master.ensure_stock_item`
  （`companion/.../express_operator/masters.py` `_create`），但只传票面单位文字和整单 `stock_acccod`，不传逐行 `unit_code` / `acccod` / `stkgrp` / `negallow`；
  服务端按约定也不把带载荷 21 新键的单派给代录、CC 桌面版拒收（约定 8.2a 第 5 条、8.4）。所以代录下新物料现在同样静默取众数。
  能带，两侧都要改：CC 桌面版 `masters._plan` / `_create` 读这四个行键传给 `ensure_stock_item`、代录载荷放行这四个行键；服务端代录单带这四键时盖 21 照派。
  有一条测试把登记与代码钉在一起：代录建主档的代码还没传 `stkgrp=` / `unit_code` 时缺口必须在，修好后必须删缺口。
- 代录 × A5 单位换算 / A11 出库成本（`express:LINE`）、A13 现销收款科目（`express:HS`）：主会话定第 1 期不出题，由 Express 界面自己处理。

取字段的正则原来不认带下划线的字段名，漏了 `SRV_VATTYP`；已改，并补登记（`ask = service_vat`）。新物料的库存单位 `QUCOD` 在建档里不是用字面量键写的、扫描取不到，手工登记（G-A10）。
变基到 `integration-1113` 后检查马上抓到 CC 桌面版验证分支新写的明细行销售员 `SLMCOD`（D-6），已补登记——这正是「写了没登记就红」要的效果。

先红后绿：登记表写好、题类还没加时，`test_express_required_coverage.py` 红 2 条——17 行的题类不认得（`cash_account` 等），
6 个写入字段没登记（`dbf_fields.py` 的 `CMPLDAT`，`dbf_schema.py` 行上重复表头的 `DOCNUM` / `DOCDAT` / `DEPCOD` / `REFNUM` / `NETVAL`）；补登记、加题类后绿。
取字段的写法本身也有一条测试（多写一个字段能被取出来），防止取法失效后一直绿。

还没做到（第 2 期）：覆盖表第 6 节第 2 条后两项——「每个 `ask` 字段构造一张缺值的单，断言卡上有这一类未定项」随第 2 批出题规则一起加；
「`review` 列在复核契约里存在、可改列能触发重判」等复核契约（`pearnly/cc-review-card`）接进来再加，现在只登记列名。

与验收体系的衔接（待主会话转验收窗口确认，验收分支 `pearnly/acceptance-suite-9bd015` 还没合入）：

- 同一套做法：登记一处定义、检查离线跑在 Development checks、存量进基线只许减少——与 `check-acceptance.py` 的欠账基线同一口径。
- 不另存一份入口清单：本登记表是 L0 契约层，管「字段有没有人负责」；验收管「场景齐不齐」。接法建议：验收 `tests/acceptance/rows.toml` 里
  引导卡那一行（`*.cc.action.guide-card`）的 `variants` 直接取本表出现过的题类（`ask`），由 `check-acceptance.py` 推出「每类题至少一条 `variant` 场景」；
  这样新加一类题就自动欠一条场景。等验收分支合入后在同一提交接上。

检查（本批）：`test_express_required_coverage.py` 10 条、`test_check_express_defaults.py` 7 条、`test_write_precheck.py` 45 条、`check-express-defaults.py`、
`check-architecture.py`、`check-test-coverage.py` 通过；闸门见下。

### 第 1 批连带回归（check-impact）

命中共用件 `express-rules`（15 个入口）、`write-precheck`（8 个入口）。本批对两者的改动只有：`write_precheck.contracts` 新增题类登记
（`KIND_GROUPS` 加五类、`new_party` 与 `party` 同样按方向分组、`PER_DOCUMENT_KINDS` 加四类），已有题类的分组、状态、展开、留单逻辑一字未改；
登记表在运行时没有调用方。所以 23 个入口逐个记「没测到（原因：行为不变，无运行时调用方）」，以离线测试佐证：
`test_write_precheck`（45）、`test_cowork_write_precheck`、`test_erp_write_precheck`、`test_companion_contract` 全过。
第 2 批出题规则接进两产品判定源时，按入口逐个实测。

## 第 2 批（上半）：引导卡契约与共用出题规则

做了什么：

- `write_precheck/guide.py`（纯函数）：把 `contracts.merge` 合成的卡片变成引导卡 `{version, questions, decided, facts, targets, counts}`。
  三档：已定（收进「已定 N 项」）、怎么录（推荐项排第一、`recommended`、`preselected`，点了才算）、做不做（`document_policy`、`direction`、
  `account_set`、`current_book`、`detail`、`cost_basis`：不预选、不标推荐，后台给了值也照问）。同类同归类键一题、带张数。
  题目给 `title_key = guide.q.<类>` + 参数，固定值选项给 `label_key = guide.option.<类>.<码>`，新建给 `guide.option.new`，主档选项照带名称——
  文字由接口层按本轮语言翻（第 3 批接）。
  `guide.held_back` / `writable`：**卡上没点过的预选不生效**（没答的题它的单留下）；共用的 `contracts.held_back` 不动（LINE 一键确认、网页待处理仍是旧口径，
  等主会话答第 2 问再决定要不要统一）。引导卡不给「全部接受」。
- 共用写账规则 `choices.resolve` 加 `guided`（与 `items_status`）：只有 CC 引导录入打开，LINE、网页、定时、自动推不变（测试逐条比对）。打开后按覆盖表出题：
  A1 进项付款状态是别的值也问；A3 服务品进项税口径问；A6 多仓的仓问；A7 明细对不上出「明细」题（`amount_only` / `skip`，不预选、只管这一张）；
  A9 没同税号档但有同名档出「对方」题（列同名档 + 新建）；A12 销项没有付款信号问现销 / 赊销（记过的照用）；A14 销项收入科目按账套记一个
  （归类键 `revenue`），没记过就问、连接默认只作预选。
- 销项映射认 `config["choices"]["payment"]`（引导卡答的 / 记过的），同进项；旧代码销项不会产出这个值，所以不带开关时不变。

主会话 11-12 批复后补的三处：

- A1 付款：付款判断有把握（漏斗按票种：收据 = 已付、完整税票 = 赊）作「怎么录」的预选（来源 `rule`，点了才算）；没把握不预选。销项 A12 同一个出题函数。
- **销项服务品税口径**（新增，主会话 11-12 第 1 条）：赊销带服务品（或没有明细）、有税时，按这本账套的销项历史（客户档读回的口径串并集）定；两种都有才问一次，
  按账套记一个（归类键 `sales|*|<账套>`），记过的照用。客户档没带口径串时不问——要等 CC 桌面版配合（见下「要 CC 桌面版窗口配合」）。
- 「没点过的预选不生效」只在 CC 引导卡：`guide.held_back` / `writable` 是引导卡专用入口；LINE 一键确认、网页待处理页仍走 `contracts.defaults` / `held_back` / `writable`，
  `test_write_precheck_guide.py` 的 `OtherEntriesUnchanged` 钉住它们的旧行为（预选没答照写）。

要 CC 桌面版窗口配合（接口约定第 8 节补一项，待主会话转）：① 客户档读回带服务品口径串 `srv`（同供应商档现有的 `srv`，取这家客户在本账套赊销单 ARTRN.SRV_VATTYP 的 1 / 2）；
② 销项载荷表头带 `srv_vattyp`（`1` / `2`）时写进 ARTRN.SRV_VATTYP，替代现在写死的 `1`；没带照旧。建议并进载荷 21 的 `GUIDED_KEYS["header"]`（21 还没发布）。
服务端在这两项落地前只出题不带键（题也出不来，因为客户档没有 `srv`），不会出现「答了不生效」。

先红后绿：`test_express_rules_guided.py` 先写，旧代码 13 条报错 1 条失败；销项映射那条在改映射前单独红过一次。`test_write_precheck_guide.py` 先写，没有模块时导入失败。

A 类 14 条在哪一批落地：

| 编号 | 落在 | 状态 |
|---|---|---|
| A1、A3、A6、A7、A9、A12、A14 | 共用规则 `choices.resolve(guided=True)` | 本批已做，产品判定源调用时传 `guided=True` 待第 2 批下半 |
| A4 走库存 / 记费用（指纹未知） | Cowork 判定源 `goods_posting` | 第 2 批下半 |
| A8 新建对方、A10 新物料 | 判定源出 `new_party` / `new_item`（已定项收起：类型、科目组、分类取众数；单位露出） | 第 2 批下半，要主档读回里的类型 / 分类众数 |
| A2、A5、A11、A13（现金科目不能记账、单位、出库成本、现销科目） | CC 桌面版回执 `*_CHOICE` + `meta.choice` 转题（第 5 批），A13 另在判定源按账套银行科目预先出题 | 第 5 批 |

检查：`test_express_rules_guided.py` 15 条、`test_write_precheck_guide.py` 13 条；用到写账规则的现有测试 31 个文件全过（`test_cowork_pos_xlsx` 本机缺 openpyxl 没跑）。
连带回归：命中 `express-rules`（15 入口）、`write-precheck`（8 入口）。不带 `guided` 时出题与映射逐条不变，产品没有调用方传 `guided`——23 个入口记「没测到（原因：
行为不变，开关没有调用方）」；第 2 批下半产品接线时逐入口实测。

## 主会话 11-12 批复（施工计划）

1. 代录里 A5 / A11 / A13 这期不出题；A10 查清事实后如实登记为缺口（见第 1 批 `MODE_GAPS`）；新增销项服务品税口径题（见第 2 批）。
2. 「没点过的预选不生效」只在 CC 引导卡，LINE 一键确认与网页待处理照旧（已用测试钉住）。
3. A1：有把握作预选、没把握不预选（已做）。
4. A7 第 1 期只给「只记金额不入库 / 这次不录」（已做）。
5. 复核列第 1 期只登记列名，断言待第 2 期（文件头已写明）。
6. 与验收衔接：照验收窗口的结构约定定稿（见第 1 批）。

## B3：五个原因码直接转人工

`SUPPLIER_NOT_IN_APMAS`、`CUSTOMER_NOT_IN_ARMAS`、`ERR_LOCATION`、`STOCK_IN_FAILED`、`ACCOUNT_CODE_IS_STOCK_ITEM` 进 `ack_rules.NEEDS_PERSON_CODES`：
回执带这些码直接转人工，不再按普通失败重试 3 次。先写 `test_cc_desktop_channel.py` 一条（旧代码红），加码后绿；`test_companion_contract.py` 照过。

## 代录 × 新物料：服务端四个行键照派

主会话 11-12 定：代录新物料的四个行键并进载荷 21，CC 桌面版侧由 CC 桌面版验证窗口做（`pearnly/cc-desktop-verify-7f2d4c` 8662cc14e：代录建主档按行上单位码 /
科目组 / 分类 / 负库存建，常量 `payload_contract.OPERATOR_LINE_KEYS`），接缝负责人是它，约定正文在它分支 `docs/work/2026-11-12-cc-desktop-payload-21.md`「并进载荷 21 的两项 → B」。
服务端（本提交）：`express_operator.contracts.OPERATOR_GUIDED_LINE_KEYS = (unit_code, acccod, stkgrp, negallow)`，`for_operator` 见到这四键版本至少 21 照派；
带其余载荷 21 新键（`guided`、`due_date_be`、`cash_account`、`cost_choice`、`cost`、对方块 `type` / `payterm`）的代录单直接报错不派。
测试 `tests/test_express_operator_guided_keys.py`（先红：2 失败 1 报错，后绿）。CC 桌面版那边的跨边测试 `tests/test_operator_guided_keys_contract.py` 还没推，
它进来后在里面加两边常量相等的断言。登记表 `MODE_GAPS` 里「代录 × 新物料」那条：两侧都进同一分支后删（`ModeGapsHonest` 测试届时会要求删）。

销项服务品口径的载荷键（与 CC 桌面版窗口对过）：沿用进货已有的 `srv_vattyp`，**不进 `GUIDED_KEYS`**（进货单本来就带它，放进去会把代录进货单与旧版 CC 一起拦下）；
销项单带 `srv_vattyp = "2"` 时单独盖 21，`"1"` 与旧写法相同不盖。客户档读回 `srv` 与「销项写 2」须同一批进集成分支，否则题会先出来、答了写不进。
服务端的销项带键与盖版本等 CC 桌面版「写 2」取证完成后同批做，今晚没做。

## 下一步设计（第 2 批下半 + 第 3 批，一起做）

`guided` 只有经引导卡入口才有意义，所以产品接线与 `pearnly_guide` 同一批做，不单独半截接上：

1. **入口**：新共用工具 `pearnly_guide`（`K/assistant/tools.py` 声明，`approval: True` 同 `ask_user` 走 `toolrun` 等人作答），模型只给 `{company?, doc_ids}`；
   服务端在出卡前（`toolrun._refused_before_card` 同一处，或新增「出卡前改写输入」钩子）调 `write_precheck.api.card(..., guided=True)` → `guide.build`，
   按本轮语言把 `title_key` / `label_key` / `source_key` 翻成文字（各产品 `i18n/messages.tsv` 新增 `guide.*` 词条，四语），把卡存成这次调用的输入；
   页面按卡渲染、作答直接调 `write_precheck.api.answer`（只认本人本对话挂着的那张，复用 `toolrun.decide` 的判法）；模型只收到「答了几项、哪些单留下」。
   放行用 `guide.writable`（没点过的预选不生效），写入计划 `write_plan` 判未定也换成引导卡口径。
2. **接线**：`write_precheck.products.check` 按签名多传 `guided`（同 `hint` 的做法）；Cowork `assistant/precheck_source.check` → 推送评估 → `push/express/decide.decide(guided=)`
   → `choices.resolve(guided=True)`；ERP `express/precheck.check` → `express/prepare.py:194` 同样透传。LINE、网页待处理、定时、自动推不传，行为不变（逐入口实测）。
   判定源另出：A4（Cowork `goods_posting` 指纹未知时出题）、A8 `new_party`、A10 `new_item`（类型 / 科目组 / 分类取众数作已定项收起，单位露出；需主档读回的众数）。
3. **模型侧**：`ask_user` 的 `refuse` 改成框架层函数：本对话有待写单据或挂着引导卡时拒绝并回「use pearnly_guide」；删掉工具说明里让模型自己出业务题的地方
   （`K/assistant/tools.py` `ask_user`「You may add your own questions…」、`pearnly_precheck` 的「turn … into ask_user questions」、`direct_push.py:208-210`、
   `write_plan.py:45`、`C/assistant/write_scope.py:22-30`、`C/assistant/tools.py:4385-4423,4622,4646,1411`、`C/companies/context.py:65,143`；行号以 integration-1113 为准再核）；
   账套题（`pearnly_conversation_ledger` 现在先用 `ask_user` 问）改由引导卡的 `account_set` 题（做不做类，不预选）承担——要和 A 线接手窗口（local_90f7baa4）经主会话对一次。
   假智能体流程断言：录单流程 `ask_user` 调用数 = 0、卡全部来自 `pearnly_guide`、同一归类键整段对话只问一次。

## 检查结果（11-12 晚，dda9e9161 / 4992f949c）

- 本地全闸门（4992f949c）：239 项过；`test_flows_run` 撞回归实验栈锁（发布窗口全套流程占着），环境原因，CI 上已过。
- Development checks：4992f949c、dda9e9161 success（CC 桌面版代码没改，Companion checks 未触发）。
- 候选离线（生产镜像 b3adf474，`cand-local.sh offline`，测 dda9e9161）：111 组，LANE EXIT 0。
- 流程测试（实验栈）：本批没有新增或改动入口文件，未跑；第 3、6 批接入口后跑 `cc.guide.*`。
- 真模型、测试机、手机：未做（第 ⑥ 批后由主会话排）。

保留项：`~/pearnly-devtools/cand-run-guided-phase1-fe347a`（候选离线解出的目录，每次重建；本任务收尾时删）。

## 11-13：三项接缝收口（基线 integration-1113 854d3ca46，含 CC 桌面版 296f02c9c）

- **代录 × 新物料缺口已关**：CC 桌面版代录建主档传四个行键、跨边测试 `tests/test_operator_guided_keys_contract.py` 已进来；本任务在里面加了
  「服务端 `OPERATOR_GUIDED_LINE_KEYS` 与 CC `OPERATOR_LINE_KEYS` 相同、版本号相同」一条；`MODE_GAPS` 删掉 `express:STMAS`，覆盖测试改成「缺口已关、代码退回不传就红」。
  代录剩下的登记缺口：A5 / A11（`express:LINE`）、A13（`express:HS`），主会话定第 1 期不出题。
- **销项服务品口径带键**：`apply_to_payload` 销项也带 `srv_vattyp`（只在引导卡答过 / 记过时有值）；`finalize_payload` 销项单带 `2` 盖 21，`1` 不盖；
  `srv_vattyp` 不进 `GUIDED_KEYS`（进货单早就带它）。有意变化：`tests/test_express_service_vat.py` 原来钉「销项载荷从不带口径」，按新口径改成「选了才带」。
  CC 桌面版侧已按载荷 `2` 写收付实现（`dbf_sales.py`，未到期销项税科目不能记账时整单不写转人工）。

## 第 2 批下半（部分）：两产品接上引导开关

- 共用开关 `pearnly_core.assistant.guided.active()`：这次调用在 CC 对话的工具调用里（`frappe.local.pearnly_assistant` 带对话号）就是引导模式。
  判定与写入两次都在 CC 对话里发生，所以同一个判断两边一致；LINE、网页待处理、定时、自动推不在 CC 对话里，行为不变。没有请求上下文（离线替身）按不在。
- Cowork：推送评估 `push/application/service._decisions` → `push/express/decide.decide(guided=)` → `choices.resolve`；ERP：`express/prepare.py` 同样传。
- A7 明细对账状态：调用方不给时规则按映射器的对账闸（`common.extract_line_items`）自己算。
- 测试：`test_express_rules_guided.py` 加「Cowork decide 透传」「两产品读同一开关」「状态自算」三条。透传两条是实现后补写的，没有先红过（如实记）。
- 还没做：A4（Cowork 指纹未知出「走库存 / 记费用」题）、A8 `new_party`、A10 `new_item`——要主档读回的类型 / 分类众数，留到第 3 批之后。
- 影响：CC 对话里的预检与写入从此按引导口径——在 `pearnly_guide` 入口（第 3 批）落地前，CC 仍用旧的 `pearnly_precheck` + `ask_user`，会多出几类题（明细、现销赊销、收入科目等）。
  所以本分支要等第 3 批一起合入，不能单独合。
