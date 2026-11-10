# 需求影响评估：本公司税号问一次、记住（CC 对话入口）

> A 线第 3 棒，2026-11-10。主会话派（11-10 线上 testcowork 代录 t4n5b25940 撞出），口径「缺信息问一次、记住」。审定前不施工。

## 0. 需求原文与理解

- 来源：主会话 11-10 派单。线上 testcowork 公司 r4qq1vnne6（บริษัท Pearnly จำกัด）`tax_id` 空、工作空间 f91cnn1j0q `tax_id` 空，
  账套目录也没报税号 → 推送判方向的三个锚点全空，单据停在 `direction_unknown`（预检里 `check_reason=subject_unbound`）。
- 理解：CC 对话里写账（直写或代录）遇到「本公司税号一个都没有、判不出进项还是销项」时，卡片问一次「本公司是票上的哪一方」，
  答案记成这家公司的税号和分店；之后推送判方向用它，这家公司不再问。
- 不在范围：
  - 有税号但票上两边都对不上（`ambiguous`，别家的票）：照旧按对方问「资料分组」（现有 `direction` 项），不改。
  - 不改 Express 账套里的公司资料（THITAX），不改工作空间税号，不做公司页的税号编辑入口。
  - LINE 收票、文件夹定时：不加卡片（同复核卡口径，只做 CC 对话入口）；但记下的税号推送时全入口都用（锚点是共用的）。

## 1. 现有同类与复用

- 方向判定：`pearnly_core.express_rules.contracts.push.resolve_account_direction`（声明 → 本方税号 → 账套目录税号），
  Cowork 调用点 `push/application/service.py` 的 `prepare` / `_express_context` / `mrerp_basis`、`push/application/assess.py`、
  `assistant/tools.py::_mrerp_direction`；② `pearnly/cc-direction-judged` 新加的 `tools.judged_direction` 同一判据。
- 卡片：共用契约 `pearnly_core.write_precheck.contracts`（`KIND_GROUPS`），Cowork `assistant/precheck_source.document` / `remember`，
  ERP `express/precheck` + `express/operator.intake_items`。前端提问卡 `ask-shared.ts`（`kindName` 读 `op.precheck.kind[kind]`）。
- 公司主档已有 `tax_id` / `branch` 两栏：Cowork `Cowork Company`（账套目录报了税号时 `companies.service._tax_fill` 自动补）、
  ERP `ERP Company`（公司页可改，`masters.company` 走 `tax_ids.require_valid`）。
- 结论：扩展现有。新加一类卡片项 `own_party`，记法写现有两栏，锚点在现有判定里加一级，不新建表、不新建入口。

## 2. Frappe 原生能力

- 只写 DocType 现有字段（`frappe.db.set_value` / `doc.save` 走原生校验），卡片用 CC 现有提问卡。不涉及新原生组件。

## 3. 按类通用

- 这一类是「写账判方向缺本方税号」。覆盖：Cowork 直写、Cowork 代录（同一个 `precheck_source.document`）、Cowork MR.ERP 推送
  （`mrerp_basis` 的 `_own_tax_id` 同样补上公司税号）、ERP 对话识别（见第 4 节 ERP 一段）。
- 锚点一处改、全入口生效：Cowork 推送的本方税号从「工作空间税号」改为「工作空间税号，没有用这份账套所属公司的税号」，
  所有调用 `resolve_account_direction` 的地方经同一个取值函数（推送域不导入 companies，走钩子，同 `cowork_push_company_choices`）。

## 4. 上下游数据

- Cowork：记下公司税号后，这家的单据归属（`pending.company_of` 的 `by_tax`）、月结台账取单（`companies.ledger` 按 buyer/seller 税号）、
  税务草稿（`tax.month_drafts(company.tax_id)`）也会按这个税号认单——这是正确的连带（这些原来因为没税号都取不到）。
  公司身份键 `identity` 不改（仍按名称），以后账套目录报了税号、和记下的不同：`_tax_fill` 只补空的，不覆盖；不同时以记下的为准，记日志。
- Express：不写 Express；载荷不变；CC 桌面版不涉及。
- ERP：对话里没说采购 / 销售的票先按采购读，卖方是本公司（`checks.issued_by(result, own_tax)`）再改按销售读（`integrations.intake._read_chat`）。
  ERP 公司没税号时这一步认不出，**销售票会被当成采购单建档**。ERP 的同类做法：对话识别时这家 ERP 公司没税号、方向是猜的，
  识别完的单在开始卡 / 预检里问一次「本公司是票上的哪一方」，记进 `ERP Company.tax_id/branch`（走公司页同一校验）；
  答案说本公司是卖方的那几张：按现有「改按销售再读一次」处理（费用在我们，同 `_read_chat` 现行口径），采购草稿作废。
- MR.ERP：只受锚点影响（`_own_tax_id`），执行层不动；D 线文件不碰。

## 5. 隔离与权限

- 记税号要有改公司主档的权限：Cowork 用 `companies.service.require_write`（与改名同一档），ERP 用公司页改资料的同一权限；服务端拦。
- 只写 CC 对话所挂的那家公司（卡片 subject = 公司编号，答案只认这一家；单据不归这家的不出这一项）。
- 撞号：选的税号已被同租户另一家 Cowork 公司占用（常见于选错了一方，对方也是本所客户）→ 不写，回已有原因
  `workspace.tax_id_duplicate`；ERP 同理用公司页的重复校验。

## 6. 入口

- 网页（电脑 / 手机）的 CC 对话：直写开始卡、代录开始卡同一张提问卡。LINE、拍照录入、文件夹定时：不加卡片（第 0 节）。

## 7. 共用件与连带回归

- 改共用层：`pearnly_core.write_precheck.contracts`（加一类）→ 跑候选离线。前端提问卡只加一条词条，不改组件。
- 受影响流程（实测）：`cc.operator`、`cowork.operator.*`、`cowork.precheck.api`、`cowork.ticket-once`、`cc.chat`、ERP `cc.ticket-once`。
- 施工后跑 `scripts/check-impact.py --files` 出清单贴进本任务记录。

## 8. 存储与成本

- 每家公司多记两栏，零存储成本。ERP 改按销售重读：每张约 ฿0.2（按现行口径由我们承担，只在答案说本公司是卖方的票上发生，且每家只发生在第一次问之前那一批）。无固定月费。

## 9. 删除、作废、追溯

- 记错了：Cowork 公司页没有税号编辑入口，改法见第 12 节第 3 问。ERP 公司页可改。删公司时两栏随公司一起删。
- 已经按错方向写进 Express 的单：不管（11-09「写错的单不管」）。

## 10. 文案与官方 UI

- 新增词条 1 条：卡片分类名 `op.precheck.kind.own_party`，四语：本公司 / Our company / บริษัทของเรา / 自社。
- 选项文字用票上原文「名称 · 税号」，不加说明文案；「其他」可输入 13 位税号（可带 `/分店`）。过 `scripts/check-copy.py`。

## 11. 测试与验收

- 先写旧代码会红的测试：
  1. 公司、工作空间、账套目录都没税号 → 预检出 `own_party` 一项（选项 = 这批票上出现的税号，按出现张数排；每张都有的那个预选）。
  2. 记答案 → 写进 `Cowork Company.tax_id/branch`；同一家第二批不再出这一项；推送判方向用它（进项 / 销项都判对）。
  3. 撞号、权限不够、税号不合法 → 不写、回原因。
  4. 有税号但两边都对不上 → 仍是原来的 `direction` 项（不回归）。
  5. ERP：公司没税号 + 对话猜方向 → 出这一项；答本公司是卖方的票改按销售。
- 实验栈：新开户数据里造一家没税号的公司走代录 + 直写各一遍（假识别，不花钱）。

## 12. 要问主会话的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 选项给什么 | 这批票上出现过的税号（名称 · 税号），按张数排，每张都出现的那个预选；「其他」可手输 | 只给卖方 / 买方两个固定选项的话，一批多张票时答案对不同票指向不同公司 |
| 2 | ERP 这一批一起做还是另开一小批 | 另开一小批紧接着做（Cowork 先合）：ERP 要动 `express/operator.py`，正被 wait-masters 和第 1 批身份两个分支改，叠在一起风险大 | 一起做要等两个分支都合入才能开工 |
| 3 | Cowork 记错了怎么改 | 这一批不加入口；CC 对话里用户说「税号记错了」时，CC 用同一个记的接口改（只改这一栏，要改公司权限） | 不给改法就只能找我们改库 |

## 13. 工作量与排期

- 文件和 ①（precheck_source / tools / service / write_precheck 契约 / 提问卡）② （tools）重叠：先在 main 上写测试和实现，
  ① ② 合入后变基再过闸门、候选、流程，排号合入。
- 机器耗时：写代码约 1.5 小时；gate 约 25 分钟、候选离线约 40 分钟、受影响流程约 40 分钟。ERP 小批另约 1.5 小时 + 同样的闸门。

## 14. 主会话审定（11-10）与施工记录

审定：第 12 节三点都按建议——选项用这批票上出现过的税号（名称 · 税号，按张数排，每张都有的预选，「其他」可手输）；
ERP 另开一小批，Cowork 合入后紧接着做（ERP 销售票被当采购建档一并修）；不加改错入口，记错了 CC 用同一个「记住」接口改，**改动留痕**（谁、什么时候、从什么改成什么）。

本批（Cowork）改动：

| 落点 | 做了什么 |
|---|---|
| `pearnly_core/write_precheck/own_party.py`（新） | 纯函数：一张票可选的两方、整批合成（按张数排、每张都有的预选）、答案解析（选项编码 / 手输 13 位税号可带分店） |
| `write_precheck/contracts.py` | `KIND_GROUPS` 登记 `own_party` → 账套与单据组 |
| `pearnly_cowork/assistant/precheck_source.py` | 方向判不出且工作空间、账套目录、公司都没税号、票能归到公司、票上至少一方税号有效 → 出 `own_party` 一项（归类键 = 公司）；`check` 按公司合成一题；`remember` 只认对话所挂的公司，交 `companies.service.set_own_tax` |
| `pearnly_cowork/companies/service.py` | `set_own_tax`：录入权限、税号校验、撞号拒收、身份键不变；这一笔写入留 Version（`access.write(versioned=True)`，DocType 开 `track_changes`） |
| `pearnly_cowork/companies/rules.py`、`hooks.py` | 钩子 `cowork_push_company_tax` → `rules.tax_for_push`：这份账套所属公司记下的税号 |
| `pearnly_cowork/push/application/service.py`、`assess.py` | 新 `_book_tax`：账套目录报的税号，没有时用公司记下的；`prepare`、`_express_context`（多回 `account_tax`）、试算、自动推判定都用它。冻结目标里的 `own_tax_id`（工作空间税号）不变，电脑领单时的核对不受影响 |
| `frontend/assistant/src/i18n.ts` | 卡片分类名 `op.precheck.kind.own_party` 四语：本公司 / This company / บริษัทนี้ / 自社（文案闸门：新增 1 条） |

不在本批：MR.ERP 推送判方向（预检对 MR.ERP 先返回、不出卡片，锚点按 MR.ERP 自己登记的税号，D 线）；② `tools.judged_direction`（`pearnly/cc-direction-judged` 未合入）合入后在这里补上公司税号；ERP 小批。

测试（旧代码先红）：`tests/test_write_precheck.py::OwnParty`、`tests/test_cowork_write_precheck.py`（无税号出题、有锚点照旧问分组、整批合成一题、记住只写本公司、撞号）、
`tests/test_cowork_companies.py::test_the_own_tax_answer_is_remembered_on_the_company_with_a_change_record`、
`tests/test_cowork_push_direction.py::test_without_a_book_tax_id_the_tax_id_remembered_on_the_company_decides`。
