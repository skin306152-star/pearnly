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

## 第 3 批（服务端）：引导卡入口 `pearnly_guide`

- **工具**：共用 CC 工具 `pearnly_guide`（`K/assistant/tools.py`，要等人作答 `approval`，新声明项 `prepare`）。模型只给 `{company, doc_ids, hint?}`。
  出卡前（`toolrun.execute` → `tools.prepare` → `assistant/guide_tool.prepare`，按这段对话现算，共用规则自动走引导口径）服务端调
  `write_precheck.api.card` → `guide.build`，按本轮语言翻好题目 / 选项 / 已定项来源，**把卡存成这次调用的输入**；全部已定就不出卡，直接交回
  `{status: nothing_to_ask, writable, planned}`（不是出错）。
- **作答**：页面随确认交回 `[{key, value, doc_id?}]`（同 `ask_user` 走 `sessions.confirm` → `toolrun.decide`）。`decide` 先 `guide_tool.check`
  核答案（键在卡上、值在选项里；对不上抛 `ask.AnswerError`，卡不被占掉），抢到卡之后才 `guide_tool.result` 记下：
  `write_precheck.api.record(guided=True)`——不补记没点过的预选（`KEPT_PROPOSALS` 只在网页 / LINE 生效），留单按 `guide.writable` / `held_back`。
  产品那边不收（选过的码刚在 Express 被删）回 `refused`，模型照实说、再出卡。关掉卡片 = 一项不记、卡上的单都留下。
  用户在聊天里口头作答走 `pearnly_precheck_answer`，CC 对话里同样按引导口径记（`tool_answer` 传 `guided`）。
- **模型不再出业务题**：`ask_user` 出卡前核对换成 `guide_tool.refuse_ask`——形状照旧核；引导卡挂着时拒；题目带业务搜索（科目 / 物料 / 对方）时拒并叫用
  `pearnly_guide`。工具说明删掉「可以自加问题」「把预选 / 未定项拼成 ask_user」「把选项名映射回编码」，`pearnly_precheck` 改成只读解释、`pearnly_write_plan`
  的「先定」改成用 `pearnly_guide`。
- **文字**：共用层术语表 `pearnly_core/i18n/messages.tsv` 新增 `guide.q.*`（18）、`guide.option.*`（17）、`guide.source.*`（8）与工具名「Asked before writing」，四语；
  翻译包重新生成；`check-copy` 无拦截。
- **产品地图**：新增 `cowork.cc.tool.pearnly_guide`、`erp.cc.tool.pearnly_guide`。
- 测试：`tests/test_cc_guide_tool.py` 11 条（出卡带服务端文字、不用问不出卡、答案核对只核不记、记答案走引导口径、产品不收不记、关卡全留、`ask_user` 四种拒绝、说明里不再有自出题）；
  `test_assistant.py` 加「引导卡先核后记、答错不占卡、关卡算跳过」并按有意变化改了三处（`ask_user` 的拒绝核对路径、步骤名两张表加 `pearnly_guide`）。
  这一批测试是和实现一起写的，没有单独先红（如实记）。`toolrun.execute` 的「出卡前准备」分支只有说明与流程测试能覆盖，流程测试要回归实验室（今天不起，待排）。
- 还没做：Cowork 产品工具说明里让模型自己问的地方（`C/assistant/tools.py` 去向 / 待选账套 / 补页 / 不在本月 / MR.ERP 菜单、`C/companies/context.py`、`C/assistant/write_scope.py`）
  ——其中账套、公司两类归 A 线账套接缝（约定：账套题由引导卡出，本对话账套已定不出），等 A 线约定落文档后一起改；前端渲染引导卡（第 6 批）。

## 第 6 批（部分）：前端引导卡 + 流程用例

- `frontend/assistant/src/components/tool-uis/guide-card.tsx`：引导卡，只照服务端的卡渲染、交回答案，不出题、不翻译。标准组件库：`Card` + `Questionnaire`
  （一题一页、第 n 项共 m 项、跳过 / 下一步 / 提交）+ `Badge`（推荐，只在「怎么录」题的推荐项）+ `Collapsible`（已定 n 项，展开后有小列表的用 `NativeSelect` 可改）+ `Item`。
  预选：「怎么录」题按服务端预选值默认选中（`preselectOf`），点「提交」才算；「做不做」题不选。交回 `[{key, value}]`：跳过的不交，已定项只交改过的。
  答完一行「已答 n 项」，没答就没了「已取消」。登记进工具表与「单独成卡」集合，手机上不浮成允许卡（`approval-sheet` 排除，常量在 `guide-shared.ts`）。
  服务端 `guide.build` 的已定项带上选项（不标推荐），供展开可改。前端词条 `guide.*` 四语（推荐 / 已定 n 项 / 已答 n 项 / 某家 · n 张）。
- 测试：`tests/test_cc_guide_card.py`（交回规则、预选规则、服务端文字照画、推荐徽章只一个、已定 n 项、只用标准组件、登记）；`test_cc_ask.py`、`test_assistant_ui.py`
  按有意变化改了两处源码断言（单独成卡集合与允许浮层都加上引导卡）。静态渲染测不到默认勾选（组件挂载后才写到输入框），所以预选抽成纯函数测；
  真点击由流程用例测。
- 流程用例 `cowork.cc.guide`（`scripts/flows/cc_guide.py`，电脑 + 手机）：假智能体调 `pearnly_guide` → 服务端出卡（付款方式中文题、「赊账」带推荐并默认选中）
  → 提交 → 模型收到 answered、这张单能写 → 再调一次付款方式不再问；核对执行记录里的卡是服务端出的、整段没有 `ask_user`、付款按供应商记进账套记忆、造的数据清掉。
  **还没在回归实验室跑过**（今天实验室排给别的窗口，要用先问主会话）。
- 还没做：业务菜单（输入框上方采购 / 销售 / 收款按钮、占位文字按业务变）、大列表题的「其他」服务端搜索（现在只列服务端给的小列表选项）、代录开始卡改渲染同一张卡、
  两处改已有文案（「(推荐)」后缀改徽章是旧提问卡那边的，随代录开始卡并卡一起改）。

## 第 6 批（续）：业务菜单 + 引导卡截图

- **截图**（真组件在展示页 `kit.html?page=guide` 渲染，示例数据，文字取服务端中文词条原文）：本目录 `2026-11-12-cc-guided-phase1/` 下
  `引导卡-电脑-推荐默认选中.png`、`引导卡-电脑-已定展开.png`、`引导卡-手机-推荐默认选中.png`、`引导卡-手机-已定展开.png`。
- **业务菜单**（评估 6.1）：输入框上方与「写入」同一行（`thread.tsx` `BusinessMenu`，`// map: guide-menu`），同一刻只出一种：有写入计划在等出「写入」、
  在跑时都不出、其余出菜单。只列产品 × 模式真能做的：产品在 hooks 声明 `pearnly_guide_businesses`——Cowork 直写 / 代录、ERP 代录 = 采购 / 销售；
  ERP 直写 = 采购 / 销售 / 收款（主会话 11-13：三种都要，收款只在能做的地方出）。描边按钮 + lucide 图标（ShoppingCart / Tag / Wallet）；
  选中后变实底带「×」，点了取消。不是必经之路。
- 选了业务：占位字换成「上传采购票据 / 上传销售票据 / 输入客户名称或编号」（四语），上传用输入框本来的入口。
- **记在会话上**：`Pearnly Assistant Session` 加 `business`（Select，原生迁移随 DocType JSON），白名单 `assistant.api.businesses`（只读清单）、
  `assistant.api.business`（只认本人本对话、值要在本模式清单里），会话摘要带 `business`；前端 `lib/business.ts`：有会话直接交、没收下撤回，
  新建页先记本页、会话建好补交，侧栏列表读到的记下。
- **还没接上的**：服务端按会话业务定方向、「选了采购却传了销售票」那一题（评估 6.3 第 2 行，去留类不预选）——要 `write_precheck.api.card`
  回每张单的判定方向，引导卡按会话业务比对后加一道 `direction` 题；下一步做。收款点了以后由 ERP 收款工具（收款分支已在集成分支）接，表单卡在第 3 期。
- 测试：`tests/test_cc_guide_menu.py`（菜单位置与互斥、产品清单、选中 / 取消、占位字四语、本页记 / 补交 / 列表记下）、`test_cc_guide_tool.py` 加业务清单三条；
  流程 `cowork.cc.guide` 加「业务菜单」一步（Cowork 只有采购销售、点采购占位字变、× 取消）。产品地图加 `cowork.cc.action.guide-menu`、`erp.cc.action.guide-menu`；
  展示页登记为无流程（只开发用）；术语表补 DocType 标签「Business」四语。

## 引导卡版式（用户 11-13）与「选了采购却传了销售票」

- 版式按 Claude 的提问卡重做（用户看截图后指出）：卡头深一档（题目左、翻页器右，首末项箭头变淡不消失）、通栏行 + 细分隔线、圆点 + 选项名（推荐标记）+ 灰色说明行、
  允许自己填的题末尾「其他」、「已定 n 项」在选项下按钮上、右下「跳过」+「下一步 / 提交」带快捷键。全部是 `questionnaire.tsx` 的部件；外观写成问卷部件的卡片外观
  `.cc-ui-questionnaire-card`（globals.css 标准组件库块，数值同旧提问卡）。服务端：每个选项给说明文字键（推荐来由 `guide.why.*` / 选项含义 `guide.desc.*`，四语），
  每题标 `free`（只有出库成本这类许自己填）。截图重出：`引导卡-电脑-第1项.png`、`引导卡-电脑-最后一项-已定展开.png`、`引导卡-手机-第1项.png`、`引导卡-手机-最后一项-已定展开.png`。
- 旧提问卡 `ask-card.tsx` 版式本来就是这个样子，但是手写控件；换成同一套部件要连三处入口一起在实验室实测——先后已问主会话。
- 业务不一致（评估 6.3 第 2 行）：`write_precheck.api.card` 回每张单的方向；引导卡按会话业务比对，方向不一致的几张在卡最前面出一道「做不做」题（不预选）：
  按票面写入 / 不写入；没点或答「不写入」那几张留下。**与方案的差别**：方案还有「按另一种业务录」，要产品能把一张票强制按另一方向写，两产品现在都没有这个能力，
  第 1 期不出这一项（已报主会话）。测试 `test_cc_guide_tool.py` `BusinessMismatch` 三条。

## A4：账套记账画像未知时出「走库存 / 记费用」

Cowork 判定源（`assistant/precheck_source.py`）：CC 对话里（引导模式）、这本账套还没报记账画像（`posting_profile` 的 `inventory_usage == "unknown"`）、
采购单有商品行时，不再静默按非库存——出「走库存 / 记费用」题，预选「记费用」（原来的默认，怎么录类，点了才算），按公司记住（同已有这一题）。其余入口照旧。
测试 `test_cowork_write_precheck.py` 加一条（不在 CC 对话不问；在 CC 对话问、预选记费用）。ERP 的商品单由 ERP 单据行上的「入库」勾选定，不走这一题。

## 账套题由引导卡出（A 线接缝约定落地）

- 照 `docs/work/2026-11-12-cc-book-identity.md`「账套题改由引导卡承担」：本对话账套未定时，引导卡最前面一道「做不做」题（不预选），候选就是共用层
  `ledger.view` 那一份（同右栏、同 `pearnly_conversation_ledger`），覆盖本月的带一行说明；答了只经 `ledger.choose(session, id, by="page")` 存进会话
  `project_ledger`（引导卡不另存、不交给产品记）；没答或没存上这批单都留下；已定不出。
- 给模型的说明：`ledger.NEEDED_NOTE` 不再叫模型用 ask_user 问，改为调 `pearnly_guide`；Cowork 工具说明 `needs_choice` 与「账套未定停下的单」两处同样改成调
  `pearnly_guide`、不自己问（黄金样本 `cowork-push-tools-golden.json` 随之更新一句）。`test_cc_conversation_ledger.py` 按新说法改断言（A 线的测试，已告知）。
- 测试：`test_cc_guide_tool.py` `LedgerQuestion` 三条（未定才出、不预选、候选同共用层；已定不出；答了经 `ledger.choose`、不交产品、没答全留）。
  约定第 6 条的跨边链条（卡答的账套 = 会话 `project_ledger` = 写入计划 `ledger` = 写账落点）：卡到 `ledger.choose` 由本测试钉住，会话到写入计划由 A 线已有的
  `test_write_plan` 钉住；整条在流程 `cowork.cc.guide` 里跑（实验室时段待排）。
- 还没改的「让模型自己问」：识别去向（`cowork_targets` 说明里「没有本对话账套时问一次」——Cowork 现在都有本对话账套，基本走不到）、月份文件夹（文件夹录入，不是写账题）。

## 11-13 主会话定：A8 / A10 等读回、「按另一种业务录」缺产品能力

- 评估与覆盖表两份文档（连示意图目录）从评估分支带进本分支；覆盖表第 5 节加「缺产品能力 / 缺数据」：G-P1（按另一种业务录）、G-A8′、G-A10′。
- 登记表新增 `OPEN_GAPS`：`express:APMAS` 的 SUPTYP / ACCNUM、`express:ARMAS` 的 CUSTYP / ACCNUM、`express:STMAS` 的 QUCOD / ACCCOD / STKGRP，等 CC 桌面版主档读回四张小表
  （约定草稿在共享笔记「引导式-CC读回四表-约定草稿-11-13.md」，CC 桌面版窗口负责，今晚部署后做）。覆盖测试 `OpenGapsHonest`：判定源里没有出 new_party / new_item 题的代码时，
  这几格必须在缺口里；出题器落地后必须删缺口。
- 闸门修正：`assistant.api.businesses` 登记进无守卫接口清单（只读站点配置）；`test_cc_guide_menu` 接进 CI；`test_cc_run_indicator`、`test_assistant_ui` 两处源码断言按有意变化补上业务菜单与引导卡快捷键；
  `precheck_source.py` 登记为共用件 assistant 的调用方。
- A 线卡片失效（`pearnly/cc-card-expiry`，还没进集成分支）：进来后引导卡按 `cardPhase` 的 `expired` 分开显示「已失效」（同 operator-card），待接。

## 提问卡与引导卡合成一个组件（主会话 11-13 选 A）

- `tool-uis/ask-card.tsx` 的 `AskCard` 改用问卷部件画（`Questionnaire` / `QuestionnaireItem` / `QuestionnaireTitle` / `QuestionnaireDescription` / `QuestionnaireChoices` /
  `QuestionnaireChoice` / `QuestionnaireChoiceDescription` / `QuestionnaireActions` + 标准按钮 + `Badge`），外观是问卷部件的卡片外观 `.cc-ui-questionnaire-card`；
  翻页、带 show_if 的题、多选、「其他」服务端搜索、答完 / 已取消的样子、快捷键都照旧由它管。旧的手写选项行（`OptionRow`）删掉，前端手写控件基线 337 → 328。
- 推荐：选项名不再带「(推荐)」后缀，改独立「推荐」徽章（用户 11-12 批准的改已有文案第 1 条）；推荐判定认服务端 / 页面标的 `recommended`，也照旧认模型写的后缀，
  并补上日文「おすすめ」（审计 D-4：以前日文不预选）。
- 引导卡 `GuideCardBody` 现在就是 `AskCard`（`askQuestionsOf` 把服务端的卡换成提问卡的题：推荐项排第一、说明行、题目下一行写哪家几张、不许自己填的题不出「其他」；
  `guideAnswersOf` 按选项编码换回 `[{key, value}]`），「已定 n 项」作为卡里的附加区（`extra`）。两张卡同一个组件、同一种版式。
- 测试：`test_cc_ask.py` 渲染断言改成问卷部件的标记（选中写在行上 `data-checked`、复选框按 `<input type="checkbox">` 数、推荐徽章、主按钮）；`test_cc_guide_card.py` 按一页一题重写
  并加映射 / 换回的纯函数断言；`test_assistant_ui.py` 快捷键清单回到一处。流程 `cc_ask.py`、`cowork_pending_operator.py` 读选中改读 `data-checked`，`cc_ask` 判定改成「名字不带后缀、带徽章」；
  `cc_guide.py` 按提问卡结构读题、点主按钮。
- 截图（触屏模拟重截，手机主按钮不画 ⌘↵）：本目录 `引导卡-电脑-第1项.png`、`引导卡-电脑-最后一项-已定展开.png`、`引导卡-手机-第1项.png`、`引导卡-手机-最后一项-已定展开.png`。
- **还没验证（如实）**：以上只过了单测与前端检查。共用件 `ask-card` 的四处入口（`ask_user`、代录开始卡、代录中途卡、待处理框）要在回归实验室逐入口实测：
  流程 `cowork.cc.ask`、`cowork.pending.operator`、`cc.operator`、`cowork.cc.guide`、`cc.send-unblocked`、`cowork.cc.conversation-ledger`（主会话排在 11-14 上午第一个时段）。

## 11-14 收口验证（窗口 guided-3）

- **基线**：远端 `pearnly/cc-guided-phase1-wip` d6040b2ec（23 个提交）变基到 main a838abe54，无冲突；新头加 1 个修正提交（下）。
  旧分支 `pearnly/cc-guided-entry-51673b` 的两份评估文档已全在本分支（本分支多 8 行），可删。
- **本机实验栈**（`pearnly-guided-lab`，自起自删，编排默认镜像）六条流程 `--jobs 2` 合跑：
  - 变基后头 e9d20a0fb：**16/16 一次通过**（605 秒）——11-13 的两处不过（引导卡取卡时序、账套断言旧编号）都已消失。
  - 修正后头 e4f7af1c7：最后一轮 **16/16**，其中 `cowork.pending.operator` 新开户 / 电脑一格第一步驱动浏览器断开（`debug connection closed … browser hung`，
    没走到业务断言），按流程说明 `--rerun-failed` 只补跑这一格后通过（`reran 1`）。之前两轮 `cc.operator` 新开户两格不过，是我跑真模型收款格时在 `erp-new`
    留下的数据（一段对话与 2 份写入计划、一张排队收款单、一份原本不存在的账套资料文件），清掉后过，不是代码问题。
  - 顺带发现（不在本任务改）：`scripts/flows/collection_lib.restore` 只在账套原来有资料时才还原，原来没有就把造数据写的收款类型等资料留在站点上；
    排在 `erp.line.collection` 之后的 `cc.operator`（新开户）会因此看不到开始卡目标。按登记表顺序全套碰不到，`--order reverse` 会碰到。
- **候选离线**（生产镜像 054aa603 = 11-13 线上版，头 e9d20a0fb）：111 组，LANE EXIT 0。
- **真模型三格**（6QAA、Opus 5.5、本机实验栈、脚本 `~/pearnly-devtools/guided-real/guided_real.py` 按会话真实用量设每格上限；台账 `round12/ocr-spend.md`）：
  合计 **1.43 美元**（上限 3）。没有 CC 桌面版：写到推送 / 排队为止。
  | 格 | 结果 | 状态链 |
  |---|---|---|
  | Cowork 直写 · 采购 | 通过（0.38；首次 0.30 是驱动脚本超时） | 业务菜单「采购」→ 模型调 `pearnly_guide`（没调 ask_user）→ 服务端出「付款方式」、「赊账」推荐并默认选中 → 提交 → 写入计划 awaiting、无推送记录 → 点「写入」→ 计划 started、推送记录建在开始之后（无电脑，记录取消，CC 照实说） |
  | Cowork 直写 · 销售 | 通过（0.13；前两次 0.09 + 0.32 查出下面两处缺陷与实验室数据缺收入科目） | 卡上把推荐「赊账」**改选「现金」** → 账套记忆记 cash、复述「现金销售」→ 计划 awaiting 无推送 → 点「写入」才推送 |
  | ERP 直写 · 收款 | 通过（0.21） | 「收款」→ 草稿现金 1000、计划 awaiting、状态 draft → 说「改成 600」→ 同一张草稿第 2 版、旧计划 replaced → 点「写入」→ queued、cash 600、`confirmed_at` 在点击后、`consent_ref` 指本对话第 2 句 |
- **真模型查出并修掉的两处**（提交 "nothing to ask and none writable"，先红后绿：`test_cc_guide_tool` 1 条、`test_cc_guide_card` 4 条断言）：
  1. `pearnly_guide` 不用问时，对话里仍画一张卡写「已答 0 项」→ 服务端回 `nothing_to_ask` 时页面不画卡。
  2. 不用问、但一张都写不了（都在等电脑读科目表）时，给模型的说明仍是「都定好了，下一步建写入计划」→ 改为照实说原因、不建计划；有能写的仍叫建计划并说明哪些被留下。
     （复跑里模型仍试了一次 `pearnly_write_plan`，服务端回 `nothing_to_write`、不建计划，兜得住。）
- **已知问题（不阻塞，未修）**：CC 复述把刚在引导卡上答的付款方式说成「之前为这家记下的选择」（卡答案存进账套记忆后来源显示成已记住）；
  收款复述里账套名显示成「名称 · 位置路径」（A 线位置回退，不是内部编号）。
- **没测到**：真写进 Express（无 CC 桌面版）、代录模式真模型、手机宽度真模型、ERP 采购 / 销售（ERP 实验栈单据都在等电脑读主档）。
- **选业务后 CC 发一句要什么**（用户 11-14 经主会话：要文件是 CC 在对话里说，不是只换输入框提示）：采购「请上传采购票据。」、销售「请上传销售票据。」、
  收款「请提供客户和收款金额。」（原拟句带「我」，被文案闸门按第一人称拦下改掉），四语。页面在消息之后画（同回复样式、不带 data-role，不进模型历史）；
  会话记 `business_at`（新列，原生迁移；新建页先选的按会话建立时刻），选之后交过文件（收款：说过话）、输入框挂着附件、正在发带文件的一句时不出；取消收起、换业务只一条、刷新按服务端重算。
  模型侧每条消息的系统说明带 `<conversation_business>`（`contracts.business_note`）。测试：`test_cc_guide_menu`（纯函数 13 条断言、位置、四语）、`test_cc_guide_tool`（记时刻、给模型的说明）；
  流程 `cowork.cc.guide` 加两条断言（新建页选 / 换 / 取消；会话里选、刷新、取消、服务端读回）。
- 覆盖表与后续各期计划：`2026-11-14-cc-guided-coverage-plan.md`（放行之后排，主会话 11-14）。

### 文案：引导卡里的「写入」换成「入账」（11-14，待用户过目）

9 处都是本分支新增、从没上线（相对 main 是新增）；提交钩子按本分支上一个提交比，判成「改已有文案」，故登记 `copy-approved.tsv`。
依据两条：用户既有口径「新文案不用录入、写入」；用户 10-27 定的代录界面用词「已入账 / 需要处理」（现有词条 `operator.report.state.saved`：已入账 / Posted / บันทึกบัญชีแล้ว / 記帳済み，沿用同一个动词）。主会话 11-14 确认按此登记，同时把清单给用户看，有异议再改。

| 键 | 中文（旧 → 新） | English | ไทย | 日本語 |
|---|---|---|---|---|
| guide.q.document_policy | 是否写入 → 是否入账 | Post | บันทึกบัญชีหรือไม่ | 記帳 |
| guide.option.document_policy.enter | 写入 → 入账 | Post | บันทึกบัญชี | 記帳する |
| guide.option.document_policy.skip | 不写入 → 不入账 | Skip | ไม่บันทึกบัญชี | 記帳しない |
| guide.option.detail.skip | 不写入 → 不入账 | Skip | ไม่บันทึกบัญชี | 記帳しない |
| guide.option.business.skip | 不写入 → 不入账 | Skip | ไม่บันทึกบัญชี | 記帳しない |
| guide.desc.detail.skip | **已删除**（用户 11-16：选项「不入账」下面的说明与选项本身重复，不再有说明行） | — | — | — |
| guide.desc.document_policy.skip | **已删除**（用户 11-16：选项「不入账」下面的说明与选项本身重复，不再有说明行） | — | — | — |
| guide.option.business.follow | 按票面写入 → 按票面入账 | Post as on the document | บันทึกบัญชีตามเอกสาร | 書類どおりに記帳 |
| （步骤名，源串 Asked before writing → Asked before posting） | 写入前已询问 → 入账前已询问 | Asked before posting | ถามก่อนบันทึกบัญชีแล้ว | 記帳前に確認しました |

没动的：写入计划上「写入」按钮等 main 已有词条。

- **ERP 直写菜单先只留「收款」**（主会话 11-14）：ERP 直写采购 / 销售整链（传票 → 识别 → 引导卡 → 复核 → 写入）没有流程或真模型证据，按「菜单只列此刻真能办完的」先不列，第 2 期验过再放出；ERP 代录仍是采购 / 销售；打字照旧能办。
  截图（实验栈真页面，四语 × 手机浅色 / 手机深色 / 电脑）：本目录 `2026-11-12-cc-guided-phase1/业务提示/`。

## 11-15 收尾合入（P0 窗口接手）

- 业务菜单改用单独的 `data-slot="cc-business-menu"`（不再占 `cc-quick-replies`），`cc_guide.py` 与 `test_cc_guide_menu.py` 同步。
- 本机实验栈 `pearnly-guided-p0-lab`（线上镜像 02eebaa4，头 055a46369）：`cowork.cc.record-reply` + 六条流程 `--jobs 2` 合跑 **18/18**（record-reply 新旧数据都过）。
- `cowork.cc.guide [new/desktop]` 与 `cowork.cc.ask` 并行（`--jobs 2`）连跑 4 轮，4/4 通过，「第二次调用为空」没有复现；不改代码。


### 文案：11-16 用户过目结果

- 9 处「写入 → 入账」同意。
- 选项说明行删掉与选项本身重复的两处：`guide.desc.detail.skip`、`guide.desc.document_policy.skip`（「本次不入账」）；选项「不入账」下面不再有说明行（没有词条时说明行本来就不出）。
  其余保留：只记表头与分录不入库、在 Express 新建（对方 / 物料各一）、记为现购 / 现销、记为赊购 / 赊销。登记表 `copy-approved.tsv` 同步删两行。
- 文案闸报的「标黄 12 处」不是说明行：是四种语言翻译包里各 3 行「写入」旧写法的残留（Asked before writing / Not written this time / Write as on the document，术语表里已改成「入账」，生成器不删旧行），已删，标黄 0。
- 截图 `docs/work/2026-11-12-cc-guided-phase1/` 里明细题「不入账」下面那行「本次不入账」是旧样子，展示页 `kit/guide.tsx` 已同步去掉。
