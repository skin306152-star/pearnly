# CC 引导式录入第 1 期：施工记录

- 分支 `pearnly/cc-guided-phase1`，基线集成分支 `pearnly/integration-1112` ffeb3a5d6（集成分支在新 main 上重叠后跟着变基）。
- 方案与批复：分支 `pearnly/cc-guided-entry-51673b` 的 `docs/work/2026-11-12-cc-guided-entry.md`（第 12 节批复、第 13 节交叠表）与
  `docs/work/2026-11-12-cc-guided-entry-express-coverage.md`（A 类 14 条、第 6 节机器检查、第 8 节载荷 21 接口约定）。两份文档随第 2 批带进本分支。
- 范围：业务菜单 + 后台出题的一张引导卡，Cowork 直写、Cowork 代录、ERP 代录的采购与销售。ERP 直写对话收单、复核表、结果表在第 2 期，收款在第 3 期。

## 批次

| # | 内容 | 主要文件 | 状态 |
|---|---|---|---|
| 1 | Express 必填字段登记表与检查 | `K/express_rules/contracts/required_fields.py`、`tests/test_express_required_coverage.py`、`scripts/check-express-defaults.py`（+ 基线、测试）、`write_precheck/contracts.py` 加题类 | 本提交 |
| 2 | 引导卡服务端契约 + A1–A14 出题规则 | `K/write_precheck/guide.py`（新）、两产品判定源 | 未开始 |
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

## 要主会话定的（施工计划里已发）

1. 代录里 A5 单位、A10 新物料、A11 出库成本、A13 现销科目这几题问不问（载荷新键服务端不派代录）。
2. 「没点过的预选不生效」只在引导卡生效，还是连带 LINE 一键确认与网页待处理页。
3. A1 现购 / 赊购：漏斗有把握时预选、没把握时不预选。
4. A7 第 1 期只给「只记金额不入库 / 这次不录」两项（「改明细」依赖第 2 期复核卡）。
5. 字段登记表与验收登记的衔接方式（上节）。

### 第 1 批连带回归（check-impact）

命中共用件 `express-rules`（15 个入口）、`write-precheck`（8 个入口）。本批对两者的改动只有：`write_precheck.contracts` 新增题类登记
（`KIND_GROUPS` 加五类、`new_party` 与 `party` 同样按方向分组、`PER_DOCUMENT_KINDS` 加四类），已有题类的分组、状态、展开、留单逻辑一字未改；
登记表在运行时没有调用方。所以 23 个入口逐个记「没测到（原因：行为不变，无运行时调用方）」，以离线测试佐证：
`test_write_precheck`（45）、`test_cowork_write_precheck`、`test_erp_write_precheck`、`test_companion_contract` 全过。
第 2 批出题规则接进两产品判定源时，按入口逐个实测。
