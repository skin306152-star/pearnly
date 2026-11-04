# 需求影响评估：开始前确认卡片（写账前置条件统一判定）

- 分支 `pearnly/precheck-card`，基线 `main` = `9635c0ee1`。本次只写评估，没有改产品代码，没有跑回归实验室，没有碰测试机和生产。
- 依据的代码都在基线上核过（行号以基线为准）。统一对话框架的两个分支 `pearnly/cc-unified-feed`（U1）、`pearnly/cc-unified-ui`（U2），以及取消拦截的分支 `pearnly/drop-own-taxid-block` 都还没合入，涉及它们的地方单独标注。
- 路径缩写：`C/` = `apps/pearnly_cowork/pearnly_cowork/`，`E/` = `apps/pearnly_erp_portal/pearnly_erp_portal/`，`K/` = `apps/pearnly_core/pearnly_core/`，`F/` = `frontend/assistant/src/`。

## 0. 需求原文与理解

- **原话**（用户 11-04，对话）：
  - 「进销存服务还有一些规则可以做成和line一样提前弹卡片和code一样开始前那些需要人工确定的卡片提供选择，列如采购销售库存服务付款等等前置条件，可以先作为预选，cc先判定，直写和代录定时都一样」
  - 「你自己设计吧，录入前还是遇到再问，你决定」
- **主会话已定的原则**（任务书第 1–7 条，以此为准）：
  1. 能提前知道的，开始前一次问完。
  2. 只有事先无法知道的，遇到再问。
  3. 问一次、按公司记住、以后复用。
  4. 定时任务也走这套，不硬拦。
  5. 全入口同一套判断、同一种卡片。
  6. 卡片只收真正需要人定的事。
  7. 沿用统一对话框架里的 `operator_start` 卡片。
- **我的理解**：
  - 现在每张单在写进 Express 或 MR.ERP 前，要人定的事散落在七八个地方，大多是「推一下被退回、再问」。
  - 改成写之前由服务端把这一批单的全部要定项判一遍，每项给出预选，在一张卡片里按业务分组列出。人点一次「开始」，就全部记住并写入。
  - 直写、代录、定时任务、自动推、网页、LINE 共用同一个判定接口和同一种卡片形状。
- **不在本需求范围**：
  - 新增任何拦单规则。
  - 改写账规则本身怎么判（`express_rules.push.choices.resolve` 的判据不变，只在外面汇总、补预选）。
  - CC 桌面版发版（`companion/` 不改）。
  - ERP 单据本身的录入字段（仓库、已付 / 付款方式等，留在单据页和 LINE 的录入流程里，见 1.7）。
  - 公司页「记住的选择」卡的重做。
  - 第 3 节以外的「只有我们拦」的规则（由 `drop-own-taxid-block` 那份调查定）。

## 1. 现有全部「要人定的前置条件」盘点

### 1.0 各列的说法

| 列 | 写法 |
|---|---|
| **在哪问、何时** | 录前 = 写入动作之前；退回后 = 推送 / 写账准备退回或报错之后；待处理 = 待处理页或对话尾部；LINE = 上传前快捷回复 |
| **记住** | 记在哪张表、按什么键。Cowork 账套记忆 = `Cowork Express Choice`（连接 + 账套 + 类 + 归类键，`C/push/choices.py:53`）；Cowork 公司规则 = `Cowork Company Choice`（公司 + 类 + 归类键 + 分店，`C/companies/rules.py:1-10`）；ERP = `ERP Express Choice`（公司 + 类 + 归类键，`E/express/choices.py:20`） |
| **Express / MR.ERP 需要吗** | 缺了能不能正确写入。依据是代录配方实录的弹框（`companion/src/companion/express_operator/recipes/express-1.0.1.94.json`）和表结构 |
| **预选凭据** | 历史 = 账套里的单据历史；主档 = CC 桌面版上报的 `reported_*`；票面 = 识别结果；规则 = 代码里的固定判法；无 = 现在没有依据 |
| **归类** | 提前 = 进「开始前确认」卡片；遇到 = 运行时才知道，用同一种卡片当场问；不问 = 现在就不问，保持不问 |

### 1.1 采购

| # | 项（kind） | 现在在哪问、何时 | 记住 | Express / MR.ERP 需要吗 | 预选凭据 | 归类 |
|---|---|---|---|---|---|---|
| P1 | 供应商档 `party`（同税号几个档、分店对不上、新建） | **Cowork**：复核页提交前弹框（`C/public/desk/targets.js:422-450`）；待处理页 `ChooseRow`（`F/components/pending.tsx:353`）；CC 文字问（`cowork_push_choices`，`C/assistant/tools.py:2252`）；代录预检（`C/push/application/service.py:595`）。**ERP**：单据提交前 `express_preflight`（`E/express/api.py:187-231`）；写账准备 hold 后单据弹窗（`E/express/push.py:425-431`）；写账报错 `PARTY_CODES`（`E/express/api.py:96-110`）；代录预检（`E/express/operator.py:94-152`） | 是。Cowork 记账套记忆 + 公司规则；ERP 记 `ERP Express Choice`。归类键：方向\|税号\|分店\|名称 | Express：要。主档里找不到就会弹查找框，配方记为 blocked → `party_missing`。MR.ERP：要，对方档必须存在或按主档默认值新建 | 来源单已带档码（ERP 前台）→ 已定；同税号且分店一致的唯一档 → 预选；同税号几个档 → 预选这本账套里用得最多的那个（**新增**：近 12 个月历史计数，与科目历史同一个按需请求）；没有同税号档 → 预选「新建」 | 提前 |
| P2 | 小票 / 简易税票的税额抵不抵 `vat` | 同 P1 各处。规则见 `K/express_rules/push/choices.py:203-210`，只对小票和简易税票问 | 是，按供应商 | Express：要。决定这笔税记进项税（ISVAT）还是并进费用 | 这家供应商在账套里的历史记法 → 预选；没有历史 → 预选「不抵（记费用）」。依据：简易税票一般不能抵进项，**要会计确认**（Q7） | 提前 |
| P3 | 定时任务给的默认处理（现 / 赊、抵不抵、科目、存货、部门） | 建定时任务时设（`C/folder_intake/contracts.py:45,212-215`），值为 `ask` 表示停下问 | 按任务记，**不进账套记忆**（10-08 口径，`C/push/application/service.py:740-743` 拒收） | — | 任务默认值 → 已定（只管这个任务的单） | 不问（已定）。卡片里显示为已定，可改；改的值只对本次生效 |

### 1.2 销售

| # | 项（kind） | 现在在哪问、何时 | 记住 | Express / MR.ERP 需要吗 | 预选凭据 | 归类 |
|---|---|---|---|---|---|---|
| S1 | 客户档 `party`（销项） | 同 P1 | 同 P1 | 同 P1 | 同 P1 | 提前 |
| S2 | 收银退货单 SR 的退货科目 `refund`（只有 ERP） | 每晚汇总 hold 后，在单据弹窗问（`E/express/prepare.py:197`） | 是，整家公司一个 | Express：要。SR 借方科目，账套里查不到原生取法（`K/express_rules/push/choices.py` 文件头） | 科目表里名称含「รับคืน / ลดหนี้」的科目 → 预选（**新增**规则）；没有 → 无预选 | 提前 |
| S3 | 是否增值税登记户 `vat_registered`（只有 ERP 收银） | 每晚汇总 hold 后问（`E/express/pos_day.py:47,220-223`） | 是，记在 ERP 公司字段上（`E/express/api.py:256-258` → `E/masters/company.py:594`） | Express：要。决定日汇总拆不拆税 | 公司税号是 13 位，且这家公司有过带税的销售 → 预选「是」 | 提前 |
| S4 | 免税商品拆开写还是整张计税 `exempt_mode`（只有 ERP 收银） | 同 S3（`E/express/pos_day.py:286-291,363-370`）；账套开了逐行 VAT 就自动拆，不问 | 是，按公司 | Express：要 | 账套开了逐行 VAT → 已定；否则预选「拆开写」 | 提前 |
| S5 | 销项收入科目 | 不问。只用定时任务设的默认，没设就用连接的默认（`K/express_rules/push/choices.py` 销项一段） | — | — | 任务默认或连接默认 → 已定 | 不问 |

### 1.3 库存

| # | 项（kind） | 现在在哪问、何时 | 记住 | Express / MR.ERP 需要吗 | 预选凭据 | 归类 |
|---|---|---|---|---|---|---|
| I1 | 存货对照 `item`（票面品名对哪个 Express 存货 / 新建） | 同 P1 各处；ERP 出入库单在 `E/express/stock_entry.py:206-230` | 是。Cowork 按供应商\|品名；ERP 写完把码挂回 ERP 商品（`E/express/push.py:637`），下次不问 | Express：要。主档里没有会弹查找框，记为 `item_missing` | 行上已带码 → 已定；与 Express 存货同名 → 已定（现在就不问）；否则预选「新建」 | 提前 |
| I2 | 存货科目组 `stock_group`（账套第一次建库存品时挂哪组） | **Cowork**：LINE 上传前（`C/line/flow.py:129-142,319-396`）、新建录入弹窗（`C/public/desk/intake-choices.js:13-14`）、复核页提交前、CC `cowork_stock_group`。**ERP**：提交前、hold 后、写账报错 `GROUP_CODES`（`E/express/prepare.py:49-52,112-120`） | 是，按账套（Cowork 记在连接 `stock_acc_groups` 上，`C/connections/service.py:436-447`；ERP 按账套键） | Express：要。新建存货必须挂科目组 | 账套里最常用、而且合格的那组 → 预选（CC 桌面版没选时本来就自动用这组，`E/express/stock_entry.py:20-21`） | 提前 |
| I3 | 商品单走库存还是记费用 `goods_posting`（永续 / 混合库存账套里，没声明入账类型的商品单） | **只有 Cowork**，推送退回后问：复核页两个按钮（`C/public/desk/intake-upload.js:142,577-579`）；CC 详情里 `handling_choice_needed=goods_posting` | 是，按公司（`Cowork Workspace.document_policy` 的 `goods_posting\|<公司>` 键，`C/contracts/document_policy.py:51-53`） | Express：要。决定开库存单还是费用单 | 账套是永续制 → 预选「走库存」；定期制本来就不问 | 提前（现在是退回后才问） |
| I4 | 出入库单的科目 `account`（归类键 `stock:<单别>`，只有 ERP） | 出入库单提交前、hold 后（`E/express/stock_entry.py:277-287`） | 是，按单别 | Express：要 | 账套 ISRUN 里这个单别的默认科目 → 已定；缺了或不能记账才问 | 提前 |
| I5 | 仓库（ERP 单据上的仓库；Express 侧要有同名仓库） | ERP 单据页预填（`E/inventory/warehouses.py:159-178`）、LINE 上传前（`E/integrations/line_flow.py:426-434`）；Express 侧 `warehouse_unmapped` 不是选择项，要先在 Express 建同名仓库（`E/express/push.py:432-436`） | 按「人 × 单据类型 × 公司」记在 user default（`E/inventory/warehouses.py:146,180-191`） | Express：要先映射，但不是人选能解决的 | 已有的预填顺序 | 不进卡片：是 ERP 单据的录入字段，留在单据页和 LINE（见 1.7） |

### 1.4 服务

| # | 项（kind） | 现在在哪问、何时 | 记住 | Express / MR.ERP 需要吗 | 预选凭据 | 归类 |
|---|---|---|---|---|---|---|
| V1 | 服务类进项的费用科目 `account` | 同 P1 各处；科目表没读到时先等（`masters_wait`，`C/push/application/service.py:714-722`） | 是，按供应商。另有公司规则 | Express：要（借方科目） | Express 历史一致（同一供应商近 12 个月 ≥3 次同科目）→ 已定（`C/push/application/service.py:492-506`）；`account_suggest` 第一个建议 → 预选（`C/push/express/account_suggest.py`，现在只在复核页和待处理页预选）；都没有 → 无预选，直写时可由 CC 提议（Q4） | 提前 |
| V2 | 服务品进项税口径 `service_vat`（เกณฑ์สิทธิ / เกณฑ์เงินสด） | ERP 提交前、hold 后、写账报错 `SERVICE_VAT_CODES`；Cowork 现在不问（`ask_service_vat` 只对 ERP 开，`K/express_rules/push/choices.py` resolve 说明） | 是，供应商 × 账套 | Express：要。代录时 Express 会弹「ภาษีซื้อของสินค้าบริการ」框，配方固定点 เกณฑ์สิทธิ | 这家供应商的口径串（`srv`）→ 已定；账套多数口径 → 预选 | 提前（Cowork 新增问这一项，与 ERP 一致） |
| V3 | 一张单部分行没声明服务 / 存货 `posting_kind_needed` | 推送退回后只给一句文案（`C/i18n/messages.tsv:1516`），人去复核页改字段 | 不记，只改这一张 | Express：要 | 每行按识别的 `item_type` 预选；读不出的行按这家供应商以往的记法 | 提前（按单一项，不记住） |
| V4 | LINE 上传前「服务还是存货」 | ERP LINE（`E/integrations/line_flow.py`）、Cowork LINE 的「入账方式」（`C/line/flow.py:34`） | 只存 LINE 会话 30 分钟 | Express：要（同 V3） | 见 1.7 | LINE 原样保留，见 1.7 |

### 1.5 付款

| # | 项（kind） | 现在在哪问、何时 | 记住 | Express / MR.ERP 需要吗 | 预选凭据 | 归类 |
|---|---|---|---|---|---|---|
| F1 | 现购还是赊购 `payment`（进项；ERP 不问，载荷固定带单据的 `is_paid`，`E/express/contracts.py:340`） | Cowork 同 P1 各处；复核页改过付款方式也会按供应商记下（`C/push/application/service.py:455-461`） | 是，按供应商 | Express：要（开 RR 还是现购单） | 票面写明 → 已定（现在就是）；这家记过的 → 已定；票面是收据或简易税票 → 预选「现购」；完整税票 → 预选「赊购」（**新增**规则） | 提前 |
| F2 | 收银非现金收款科目 `receipt`（按付款方式：转账、QR、刷卡、外卖平台） | Cowork POS 导入的单在待处理和复核页问；ERP 每晚汇总 hold 后问（`E/express/prepare.py:162-168`） | 是，按付款方式。Cowork 可借同公司别的年度账套记过的值（`K/express_rules/push/choices.py` 的 `borrow_receipts`） | Express：要。每种付款方式另记一张日记账凭证 | 借来的值 → 已定；科目表里名称对得上的银行 / 清算科目 → 预选（**新增**规则） | 提前 |
| F3 | ERP 单据的已付 / 未付、付款方式 | ERP 单据页（默认「已付 · 现金」，`E/purchasing/entry.py:221-222`）；LINE 上传前（`E/integrations/line_flow.py:379-380`） | 不记 | Express 用单据的值，不需要另问 | — | 不进卡片：是 ERP 单据的录入字段（1.7） |
| F4 | 预扣税 | 没有问的地方。Express 写账规则和 CC 桌面版都不写预扣税（`K/express_rules`、`companion/` 里搜不到 WHT 字段）；MR.ERP 导入取历史税率，缺省 3%（`K/mrerp/push/xlsx/lookups.py:137-141`）；ERP 的 `default_wht_service_rate` 存了没人读（`E/purchasing/settings.py:14`） | — | Express：现在不写这项；MR.ERP：有默认值 | — | 不问。等 Express 支持付款单（PS）带预扣税时再加 |

### 1.6 账套与单据（整批共用的项）

| # | 项（kind） | 现在在哪问、何时 | 记住 | Express / MR.ERP 需要吗 | 预选凭据 | 归类 |
|---|---|---|---|---|---|---|
| B1 | 写入哪份账套：同期两份账套。Cowork 叫「现用账套」`current_book`，ERP 叫 `account_set` | **Cowork**：定时任务跟随公司账套时停下（`C/folder_intake/push.py:151-166`），自动推判成 `book_many`（`C/push/express/automation.py:586-595`），都在待处理选（`C/assistant/pending_api.py:204-260`）。**ERP**：提交前、hold 后（`E/express/target.py:42-48`） | 是。Cowork 按公司 + 分店 + 年段 + 候选组合（`C/contracts/document_policy.py:55-61`）；ERP 按公司 + 会计年度（`E/express/target.py:16-19`） | Express：要 | Cowork：最近有录入的那份（`current_book_seen`）→ 预选；ERP：`target.current` → 预选 | 提前（卡片头部的写入目标就是它） |
| B2 | 单据方向：进项还是销项 `direction_unknown` | 直写：推送退回后给文案，人在复核页改字段（`K/express_rules/push/preflight.py:236-246`、`C/public/desk/intake-upload.js:402`）；代录：资料按组（采购 / 费用 / 销项）拖动 | 直写不记；代录按对方记 `Cowork Company Choice` kind=`group`（`C/companies/rules.py:27-31`） | Express：要（开 RR 还是销售单） | 对方记过的分组 → 已定；票面税号对上工作空间或账套税号 → 已定（现在就是）；都判不出 → 无预选 | 提前（直写也改用代录的分组记忆，Q3） |
| B3 | 转账凭条、预扣税凭证录不录 `document_policy`（只有 Cowork） | 推送报错后（`C/push/application/service.py:79-82,145-147`）：复核页「录入 / 不录入」按钮（`C/public/desk/intake-upload.js:139,570-573`）；CC `cowork_choose_document_policy`（`C/assistant/tools.py:4323`） | 是，**整个租户一条**（`Cowork Workspace.document_policy`，`C/history/document_policy.py:24-25`） | 不是 Express 的要求，是用户定的口径（「录不录不写死」） | 预选「不录」，与 ERP 写死的口径一致（`E/recognition/claude.py:11`），见 Q5 | 提前（现在是退回后才问） |
| B4 | 上次结果不明的单：核对是已入账还是没入账 | 代录预检列出（`C/push/application/service.py:606-607`），U2 开始卡给两个按钮；直写在推送结果里报 `unconfirmed`，CC 再调核对工具 | 核对结果记在推送记录上 | 不核对就可能重复入账（流程锁，不是规则） | 无预选，只能人核 | 提前（两种模式都放进卡片） |
| B5 | MR.ERP 导入菜单、主档默认值（`mrerp_menu` / `mrerp_default`） | **Cowork**：新建录入弹窗（`C/public/desk/intake-folders.js:244`）、复核页提交前（`C/public/desk/intake-upload.js:771-777,890`）、待处理 `action=mrerp`、CC `cowork_mrerp_setup`。**ERP**：只能在单据 hold 后由 CC 选（`E/mrerp/push.py:76-93`）；ERP 的 MR.ERP 总开关是关的（`E/mrerp/__init__.py:13`） | 是。Cowork 记在连接上（连接 + 账套）；ERP 记在 ERP 公司上 | MR.ERP：要。菜单号和主档按公司各不相同，对不上整单被拒（`K/mrerp/menus.py:3-7`、`K/mrerp/masters.py:3-6`） | 共用规则能定的已定：只有一个菜单、排序第一、总店、多数档、参考科目（`K/mrerp/masters.py` 文件头）；定不了的 → 无预选 | 提前 |
| B6 | 部门 `department` | 不问（10-08 起），用单据簿的默认部门 | 选过的照用 | — | — | 不问 |
| B7 | 期间外、期间已锁、配方不支持这类单 | 代录运行时才报（`period_outside` 是 Express 的 Message 70）；CC 桌面版有一个能提前查的 `precheck.py`，但**没接进执行链路**（只有测试在调，`companion/tests/test_operator_parts.py:15,78`） | — | Express 自己就拒 | — | 不是选择项。卡片底部列一行「不能写 N 张」和原因，不让人选 |

### 1.7 只在单据录入上问、不进卡片的项

ERP 单据的仓库、已付 / 付款方式、服务或库存，以及 LINE 上传前的几步，是 **ERP 单据本身的录入字段**，不是「写进 Express 前要人定的事」。ERP 的规矩是「先有单、再写账」，这些在单据保存时已经定了。所以：

- 单据页照旧（仓库预填、付款默认值不变）。
- LINE 上传前的几步保留 LINE 的快捷回复形式（官方号的原生做法）。只做一件事：**已记住、不会错的不再问**（I2 存货科目组、B1 账套，见 2.5）。
- 已付 / 付款方式、服务或库存是每张单的事实，不按公司记住后跳过，以免把这张单记错。

### 1.8 遇到再问（运行时才知道）

| 情况 | 现在 | 统一后 |
|---|---|---|
| 代录运行时：`company_pick`、`negative_stock`（Express 4224 / 4091）、`license`（-330）、`login_failed`、`company_mismatch`、`network_down`、`budget`、`account_in_use`、`account_set_unavailable`、`recipe_mismatch`、`unknown_screen`、`readback_mismatch`、`crash_repeated`、`placeholder_left`、`cc_restarted`（`K/express_operator/contracts.py:20-55`） | 单张停下（`waiting`）或整批停（`BATCH_STOPPERS`） | U1 / U2 的 `operator_choose`（一张单）和 `operator_resume`（整批）卡片，**卡片外框与本卡片同一个组件**（2.1） |
| `login_missing` | U2 开始卡在执行电脑上已经显示「登录未设置〔设置〕」 | 保留在开始卡头部，不算选择项 |
| 写账时主档变了：预检时档还在、写时被删 / 改（ERP `PARTY_CODES` / `GROUP_CODES` / `SERVICE_VAT_CODES`，`E/express/prepare.py:81-90`；Cowork 同类原因） | ERP 弹单据弹窗；Cowork 记失败原因 | 同一种卡片，只含这一张单的这一项，出在那段对话里（直写、代录同样） |
| 写入冲突：另一台电脑正在写同一份账套（`ERR_WRITER_BUSY`） | 自动重排 | 不问人，照旧 |
| 科目表或主档还没读到（`masters_wait`、`waiting`） | 等电脑读完，复核页 45 秒后放弃 | 卡片上这几张显示「等电脑读账套」，读到后卡片自动刷新，不问人 |
| 选过的值在 Express 里没有了（被删 / 改） | 写账规则当没选过、重新问（`K/express_rules/push/choices.py` 文件头） | 下一次判定自然出现在卡片里，标为未定 |

### 1.9 小结

- **提前一次问完的共 20 项**：P1、P2、S1–S4、I1–I4、V1–V3、F1、F2、B1–B5。它们都能在写之前算出来，现在大多是推送退回后才问。
- **其中现在就有预选的**：P1 部分、V1、B1、I2（CC 桌面版自动用）、B5 部分。**要新加预选规则的**：P1 同税号多档、P2、S2、S3、F1、F2、B3、V3。
- **不进卡片**：P3、S5、B6 现在就不问，保持不问；I5、F3、V4 是 ERP 单据或 LINE 录入字段；F4 现在不写；B7 是 Express 自己就拒的事实，只列出。
- 第 3 节以外「只有我们拦」的规则（同税号买卖方、押金、补开、对方税号非 13 位等，`drop-own-taxid-block` 调查第 3 节）**不进卡片**（原则 6），由那份调查定去留。

## 2. 统一后的「开始前确认」卡片

### 2.1 一个组件，三种用法

U2 的 `F/components/tool-uis/operator-card.tsx` 是外框，套用对话「允许」卡片的样式；`operator-start.tsx` 是开始卡。在这基础上抽出卡片主体 `PrecheckBody`，三种用法共用：

| 用法 | 什么时候出 | 头部 | 主按钮 |
|---|---|---|---|
| 开始前（代录） | 每个代录会话都有，即 U1 的 `operator_start`，**一直出** | 写入目标（几份账套就列几行）+ 执行电脑 | 「开始」→ `express_operator.api.start`，答案一起带上 |
| 开始前（直写） | CC 要写一批单时，判定里有「预选」「未定」或「结果不明」才出；全部已定就直接写，不出卡片 | 写入目标 | 「开始」→ 存下答案，再执行这次写入（2.4） |
| 待处理（定时任务、自动推） | 这段公司 × 月份对话的尾部，有停着等人定的单就出（U2 的 `PendingInChat` 那个位置） | 写入目标 | 「开始」→ 存下答案，这些单重新排队写入 |
| 遇到再问（一张单） | 运行时冒出的那一项（1.8），即 U1 的 `operator_choose` | 单号 · 对方 | 「确认」→ 存下答案并重排这一张 |

### 2.2 卡片内容（从上到下）

1. **头部（问话）**：写入目标一行，例如「ABC 公司 · 2026 账套」。一批单写进几份账套就列几行，每行后面写张数。这一点补上 U2 只读顶层 `target`、多份账套时只剩公司名的问题（U2 `operator-shared.ts:310-313`）。
2. **未定与预选区**，按业务分组，顺序固定：账套与单据、采购、销售、库存、服务、付款。没有项的组不显示。
   - 组标题只写组名，不写数（同 W3 组标题去掉计数的口径）。
   - 每项一行：左边是对象（供应商名 / 品名 / 付款方式名），下面一行淡色写这一项是什么（「供应商档」「存货」「费用科目」……）；右边是下拉，已选好预选值。
   - 这一项影响不止一张单时，对象后面写「5 张」。
   - **未定的项**（没有预选）下拉是空的，排在本组最前面。
3. **已定区**：一行「已定 N 项」，默认收起，点开后的样子同上（分组、下拉），可以改。已记住、公司规则、Express 历史、任务默认值都算已定。
4. **结果不明区**（B4）：每张一行「单号 · 对方」加〔已入账〕〔没有入账〕两个按钮，沿用 U2 开始卡的做法。
5. **底部事实行**：「不能写 3 张」可以点开看原因（B7，期间外等），「等电脑读账套 2 张」。只写事实，没有操作。
6. **按钮**：主按钮「开始」一直能点，右边是「取消」（直写）。
   - 有未定项时也能点：依赖这些未定项的单不写，留在待处理；其余照写。卡片点完后显示一行淡色结果「写了 18 张 · 留下 2 张」。这对应原则 4「不硬拦」。
   - 不弹二次确认。

**预选怎么显示**：只靠下拉里已选好的值，加上「已定」折叠区和「未定」排在最前，三种状态就能分清。**不写来源文字**（「按历史」「按主档」），合用户「不加说明性文案」的口径。预选的理由由 CC 在出卡片那条消息里用一句话说（直写时是模型说的话，不是界面文案）。

**改了以后是否记住**：卡片上点「开始」时，所有项（含没改的预选）按各自现有的记法记住：账套记忆、公司规则、ERP Express Choice、租户口径、公司字段。下次同一项就是已定。例外：

- 定时任务默认值那几类（P3）改了只对本次生效，不进账套记忆（10-08 口径）。
- 按单的项（V3 入账类型、B4 结果不明）只改这一张。
- 不加「只这一次」开关（不加控件）。

**大列表**：科目表、存货动辄几百上千条。下拉改成带服务端搜索的选择框，按 `search_link` 的思路翻页，不一次取全（AGENTS「列表、选择与搜索」）。现在 Cowork 只给前 30 条（`C/assistant/tools.py` `cowork_push_choices` 的 `options.shown≤30`），不够用。

### 2.3 三端

| 端 | 做法 |
|---|---|
| **电脑（CC 对话页 / 桌面窗口）** | 卡片在对话流里，样子同「允许」卡片：卡内不滚动，一组超过 5 项时显示「显示更多」（卡片规矩 4–5 项，用户 10-13）。键盘：Tab 在下拉之间移动，Enter 等于「开始」 |
| **手机（CC 网页 / 主屏幕网页）** | 同一张卡片在对话流里。窄屏时每项改成上下两行：对象和类别在上，下拉占满一行在下。现在 U2 的 `--cc-op-select-w:150px; max-width:50%` 在窄屏只左右排，**没有窄屏布局**（U2 `globals.css:968,2161`），要补。触屏行高用 `--cc-tap`，按钮用 44 高的胶囊（`globals.css` 现有）。不进输入框上方的允许浮层：那个浮层只认带 `approval` 的部件，一次一项，放不下分组下拉 |
| **网页 Desk（Cowork 复核页、ERP 单据提交前）** | 遵守官方 UI：仍用 `frappe.ui.Dialog`。Section Break 按同样的六组分节；Select / Link 字段放预选值（Link 用 `get_query` 走服务端搜索）；已定项放进一个可折叠的 Section（`collapsible: 1`）。数据来自同一个判定接口（第 3 节），不再各自拼 |
| **LINE** | 见 2.5 |

### 2.4 直写怎么出卡片

直写的写入由 CC 调推送工具完成（Cowork `cowork_push_documents`，`C/assistant/tools.py:4454`；ERP `erp_push_documents`，`E/express/cc_tools.py:653`）。做法：

- 两个推送工具挂一个**按判定结果动态决定**的「允许」门。现在的门只认参数名（`K/assistant/tools.py:113` `needs_approval`）。
  - 改成：工具声明的 `approval` 可以是一个函数路径。服务端出卡片前调共用判定。有预选、未定或结果不明的项就出卡片，否则直接执行。
  - 卡片按工具名在 `tool-uis` 注册成 `PrecheckBody`，问话位置显示写入目标。
- 点「开始」：页面先调判定接口的 `answer` 存下答案，再走现有的「允许」（`toolrun.decide`）。工具照常执行，执行时再判一遍，用的已是记住的值。
- 点「取消」等同「拒绝」，交回模型的说法沿用 `DENIED_INSTRUCTION`。
- 好处：
  - 门在服务端，不靠模型自觉先调一个确认工具。
  - 卡片的持久化、重开对话后留在原处、点完的样子，都是允许卡片现成的（`K/assistant/toolrun.py`、`F/lib/approval-card.ts`）。
- 要处理的一点：`steps().gate` 现在按参数名告诉页面哪一步出过卡片。动态门要改成以执行记录（`Pearnly Assistant Tool Run`）里「出过卡片」为准。

直写里 CC 的作用：判定接口的预选只用规则和历史。对没有预选的项（例如新供应商的费用科目），CC 可以在调推送工具时带一个可选参数 `proposals`（`[{kind, subject, value}]`），作为预选放进卡片，依据由 CC 在消息里说。不多调模型、不多花钱（Q4）。

### 2.5 LINE

LINE 的形态（快捷回复最多 12 项、标签不超过 20 字，Flex 卡上限 28KB）放不下分组下拉，所以 LINE 不画这张卡片，而是**用同一个判定接口决定问什么、预选什么**：

| 位置 | 现在 | 统一后 |
|---|---|---|
| Cowork LINE 上传前（目标 → 账套 → 方向 → 入账方式 → 存货科目组） | 存货科目组已按账套记住；其余只存 30 分钟 | 判定接口给出已定的就跳过这一步（存货科目组已是这样；账套如果只有一份或有现用账套也跳过），其余照问，预选值放第一个按钮 |
| Cowork LINE 上传后：扫描自动推（`C/push/automation/sweep.py`），有要人定的就判成 C 类停下，**用户在 LINE 上不知道** | 只在 08:30 汇总里出现 | 识别完已经会推一张 Flex 卡（`C/line/cards.py:115-181`，只有「编辑」）。在这张卡上加几行预选结果（最多 4 行，其余写「还有 N 项」），再加按钮「ยืนยัน」（postback：照预选全部记住并写入）。「编辑」改为打开这段公司 × 月份的 CC 对话，看到同一张卡片。**不多发消息**，并在已有的那张卡里，不多占官方号额度 |
| ERP LINE 上传前（公司 → 对方 → 已付 → 付款方式 → 服务 / 库存 → 仓库） | ERP 单据录入字段 | 不动（1.7） |
| ERP LINE 识别后的卡（只有「编辑」，打开网页单据页提交） | 网页提交时经 `express_preflight` 问 Express 项 | 网页提交前那个弹窗改用统一判定的输出（2.3 Desk 行）。LINE 卡片不变 |

## 3. 后端：一个共用的「写账前置判定」

### 3.1 放在哪

新共用包 `K/write_precheck/`，文件头写明「多产品共用」，同 `express_operator` / `mrerp` 的做法：

| 文件 | 内容 | 依赖 |
|---|---|---|
| `contracts.py` | 纯函数：项的形状；kind → 业务组的对照（party 按方向进采购或销售）；跨单按 `(kind, subject)` 去重并记下影响哪几张；三种状态的判定与排序（已定 > 预选 > 未定）；答案展开到各张单；哪些单因为未定项要留下 | 无框架、无 IO（架构第 4 条，自动检查） |
| `api.py` | 白名单接口 `check(doc_ids, company=None, mode=…)`、`answer(company, doc_ids, answers)`；按 `pearnly_write_precheck` 钩子转给产品；权限由产品核 | Frappe |
| `products.py` | 读产品钩子（同 `express_operator/products.py` 的写法） | Frappe |

产品在自己的 `hooks.py` 声明 `pearnly_write_precheck = {"check": "...", "remember": "..."}`（ADR-009，产品自己声明）。

### 3.2 输出形状（`check` 的回包）

```
{ company, company_title,
  targets: [{account_set, account_set_title, connection, count}],      # 写入目标，沿用 U1 shapes.py 的字段
  groups: [{group: book|purchase|sales|stock|service|payment,
            items: [{key, kind, subject, label, docs: [doc_id], state: decided|proposed|open,
                     value, value_label, options? | search?: {kind}, remember: bool}]}],
  unknown: [{doc_id, ref, party, record}],                             # B4
  not_writable: [{doc_id, reason}], waiting: [{doc_id, reason}],       # B7、等电脑
  ready: [doc_id] }
```

- `key` = `(kind, subject)` 的稳定摘要，页面拿它回答。
- 各项的 `kind` / `subject` 与现在的 `needed` 完全一致（`K/express_rules/push/choices.py`），记忆的键因此不变。
- `answer` 入参 `answers=[{key, value}]`。服务端展开成每张单的 `[{kind, subject, value, label}]`，交给产品的 `remember`，回包是重新判定后的 `check`。

### 3.3 和现有机制怎么合并（不留两套）

| 现有 | 处理 |
|---|---|
| 共用写账规则 `resolve()`（`K/express_rules/push/choices.py`） | **不改判据**。只在返回里补上每项的来源（`applied` 已有 `payment_from` / `account_from`，`decided_by` 由 Cowork 的 `company_choices` 给）。新加的预选规则（1.9 列的几条）写成 `write_precheck/contracts.py` 里的纯函数，不塞进 `resolve` |
| Cowork 单张判定 `service.operator_check` / `choices_view`（`C/push/application/service.py:540-643`） | 改成产品钩子 `check` 的单张实现。**关键改动是「一次收齐」**：现在 `prepare` 遇到录不录没选（`:145-147`）、入账方式（`posting_needs_review`）、方向判不出时**先报错退出**，后面的选择项根本算不到，所以人要被问好几轮。改成这些都变成项收进同一份结果 |
| ERP 单张判定 `operator.precheck` / `api.preflight` / `prepare_only`（`E/express/operator.py:94-152`、`E/express/api.py:187-231`） | 同上。ERP 的 `prepare` 在第一个 hold 就停（`E/express/prepare.py:180-181` 账套、`:212-214` 科目组），改成收齐 |
| 代录共用层 `express_operator.products.precheck` / `submit(answers)` | `precheck` 改为转调 `write_precheck`（薄兼容，签名不变，U1 / U2 的调用方不用改）；`submit` 的 `answers` 形状不变 |
| 记忆的存取：Cowork `push/choices.py`、`companies/rules.py`、`history/document_policy.py`、`connections/service.py`（科目组）、`pending_api.choose_account_set`；ERP `express/choices.py`、`masters/company.py` | **全部不动**。产品的 `remember` 按 kind 分派到现有写入函数，权限和值的校验照现有的（值必须在账套主档里或是「新建」） |
| 「问一次」的旧入口：Cowork `push.application.api.choices / save_choices`、`pending_api.answer`、`history/document_policy.choose`、`goods_posting.choose`；ERP `express.api.answer / preflight` | 接口保留（Desk 页面、CC 桌面版无关），内部改成调同一个判定与 `remember`。旧的逐类按钮在第 5 节删 |

### 3.4 隔离与权限

- `check` 只回当前用户能推的单。Cowork 按这张票、这条连接的推送权限（`scope.require`）核；ERP 按单据所属公司的开单权核，与 `express.api.answer` 一样。别人的单不出现，也不报存在与否。
- `answer` 逐项核：这一项影响的每张单当前用户都要能推。按公司、按租户的项（B3 录不录是租户级）按现有写入函数的权限核：现在谁能选，统一后还是谁能选。服务端拦，不靠前端隐藏。
- 跨公司：一次判定只回一家公司的单。直写一批单跨了公司，就按公司各出一张卡片。

## 4. 定时任务与自动推遇到未定项

| | 现在 | 统一后 |
|---|---|---|
| 定时任务（`C/folder_intake/push.py:195-224`） | 有要选的项：票上记 `choice_needed:<kinds>`，去向 `held`，待处理里 `action=choose`；其余单照推（`C/folder_intake/pump.py:455-477`） | 判定改调 `write_precheck`（钩子同一个），停下的原因照旧；**其余单照推不变** |
| 网页上传 / LINE 扫描自动推（`C/push/automation/sweep.py:93-94`） | 有要选的项判成 C 类停下，**不记原因**；待处理满 15 分钟后显示「没推」并交给 CC（`C/companies/pending.py:83-84`），不能当场选 | C 类里因为要选项停下的，同样记 `choice_needed:<kinds>`（只记原因，不改档位判断），待处理里当场能选 |
| ERP 每晚收银汇总（`E/express/pos_day.py`） | hold 在单据上；待处理行点开去单据页（`E/express/pending.py:78-83`） | 同样进这家公司那个月的对话尾部卡片 |
| 卡片挂在哪 | 待处理页逐行问 | 这家公司、这个月的对话尾部。即 U2 已做的「待处理并进对话」（W3 块 4，`PendingInChat`）：把那段对话里停着等人定的单一起判一次，出一张卡片。没有这个月对话的，用 W3 的占位行打开后出现 |
| 怎么提醒 | 定时任务完成简报列出要选的类（`C/folder_intake/briefs.py:128,154-173`）；08:30 待处理汇总（`C/companies/pending.py:664`） | 沿用这两处，不新加通知。对话侧栏该行显示「等你处理」（同一个 `ActivityMark`，由 `requires_action` 得出）。不加新消息、不加定时任务（零常驻） |
| 预选会不会自动生效 | — | **不会**。只有已定的照写；预选要人点「开始」才算数（原则 4）。不依赖这项的单照常写 |

## 5. 对已有数据和流程的影响

### 5.1 数据

- **不加 DocType，不迁移记忆**。记忆的表、键、值全部沿用，已记住的选择统一后直接显示为「已定」。
- Cowork 直写的方向改用代录的分组记忆（`Cowork Company Choice` kind=`group`）：只是多一个读取方，表不变。
- 自动推的票从这批起会记 `choice_needed:*`。已经判过的旧票（`auto_checked_at` 已有值）不回填，下次有人碰它、或待处理重判时自然更新。只影响待处理页的显示，不影响业务。
- 票上已有的原因串（`document_policy_needed_*`、`posting_needs_review:*`、`choice_needed:*`）照旧认得，月结台账的异常码表（`C/companies/exceptions.py`）不用改。

### 5.2 删掉的旧问法（统一卡片上线、同批）

| 删 | 位置 | 被谁取代 |
|---|---|---|
| CC 工具 `cowork_push_choices`、`cowork_save_push_choices`、`erp_push_choices`、`erp_save_push_choices`，以及说话规矩里「先说出第一个建议科目、等确认再存」那段（`C/assistant/tools.py:4487-4511`） | Cowork、ERP 的 CC 工具 | 直写的卡片门（2.4）。产品地图删对应 4 行 |
| 推送结果里的 `handling_choice_needed`（录不录、入账方式），以及 CC 再调 `cowork_choose_document_policy` 选的那条路 | `C/assistant/tools.py:1983-1986` | 卡片里的 B3、I3。`cowork_choose_document_policy` 工具保留，给「改一下口径」这种对话用 |
| 复核页推送退回后的「录入 / 不录入」「走库存 / 记费用」按钮 | `C/public/desk/intake-upload.js:139-147,570-582` | 复核页提交前的统一弹窗 |
| 复核页、ERP 单据提交前各自拼的选择弹窗 | `C/public/desk/targets.js:398-450`、`E/public/desk/transactions.js:164-203` | 同一份判定输出画的 Frappe Dialog（只换数据来源和分组，仍是 Dialog） |
| 待处理页的 `ChooseRow`、`MrErpRow`（如果 U2 合入后还在） | `F/components/pending.tsx:353,466` | 对话尾部卡片 |

改动已有文案前，先把清单贴进本记录给用户看（AGENTS 文案规矩）。新增文案只有：组名六个、「已定 N 项」、「N 张」、「不能写 N 张」、「等电脑读账套 N 张」、「写了 N 张 · 留下 N 张」、LINE「ยืนยัน」，大多能沿用现有词条。

## 6. 测试与产品地图

### 6.1 单测

| 测试 | 覆盖 |
|---|---|
| `tests/test_write_precheck_contracts.py`（新，纯函数） | 分组对照；跨单去重与张数；三种状态的判定与排序；新预选规则（每条一正一反）；答案展开到单；未定项让哪些单留下；`key` 稳定 |
| `tests/test_cowork_express_preflight.py`、`test_cowork_express_choices.py`（扩） | 一次收齐（录不录 + 入账方式 + 选择项 + 方向同时出）；`remember` 分派到各个存储；记过的不再问；任务默认值不进账套记忆；权限拒绝（别人的单、别的连接） |
| `tests/test_cowork_folder_intake.py`（扩）；`sweep` 测试 | 定时任务一张停下、其余照推；自动推记 `choice_needed` |
| ERP（新 `tests/test_erp_write_precheck.py`） | 账套 + 科目组 + 对方 + 收银三项一次收齐；`remember`；开单权拒绝 |
| `tests/test_assistant.py`（扩） | 动态门：全部已定不出卡片；有未定项出卡片；点开始后用的是记住的值；拒绝；重开对话卡片留在原处 |
| `tests/test_cowork_line_intake.py`、ERP LINE（扩） | 已定的步骤跳过；识别后卡片带预选行与「ยืนยัน」；标签不超过 20 字；旧按钮诚实拒收 |
| `tests/test_cc_operator.py`、前端单测（扩） | 卡片分组、已定折叠、未定在前、改值、开始带的答案；窄屏叠排（`[data-cc-touch]`） |
| `tests/test_express_operator_shared.py`（扩） | `products.precheck` 转调后，形状与改造前逐字相同（兼容） |

### 6.2 流程（`scripts/flows/`）

| 流程 | 新 / 改 | 内容 |
|---|---|---|
| `cc.precheck.direct`（新，Cowork、ERP 各一，桌面 + 手机宽度） | 新 | 上传 3 张，其中一张是新供应商、一张是小票 → 让 CC 推 → 出卡片，分组与预选正确 → 改一项 → 开始 → 写进 C 盘 T 账套、读回一致 → 再推同一供应商的单，不出卡片 |
| `cc.precheck.pending`（新，Cowork） | 新 | 定时任务一张停在 `choice_needed`、其余写完 → 那个月对话尾部出卡片 → 开始 → 写入 |
| `cc.operator`、`cowork.pending.operator`、`cowork.operator.unknown` | 改 | 开始卡改成分组；结果不明两个按钮照旧 |
| `erp_document_express`、`cowork_intake_review` | 改 | Desk 提交前弹窗按组分节、预选值 |

### 6.3 产品地图与共用件要登记的行

- **新白名单接口**：`endpoint:pearnly_core.write_precheck.api.check`、`…answer`。调用方是 CC 卡片和两处 Desk 弹窗，规则 6 认得到。
- **新共用件**：`write-precheck`（`K/write_precheck/**`），在 `scripts/impact/shared-parts.toml` 登记入口：
  - `cowork.cc-chat.push`、`erp.cc.write`、`cc.operator.page`、`cowork.express.push`、`cowork.history`、`cowork.companies`；
  - 两个 LINE 入口；
  - 新增的 `cc.precheck.*` 流程行。
- **新同组**：`cc.precheck`，ERP 与 Cowork 同名对照（规则 5）。
- **CC 工具行**：删 `cowork.cc.tool.cowork_push_choices`、`cowork.cc.tool.cowork_save_push_choices` 及 ERP 同名两行；`cowork_push_documents`、`erp_push_documents` 两行的测试引用加 `flow:cc.precheck.direct`。
- **LINE**：Cowork 新 postback 动作（「ยืนยัน」）进 `cowork.line.postback.*`，带 `parts = ["line"]` 和单测。
- **影响清单预演**（`python3 scripts/check-impact.py --files` 按第 7 节要改的文件算）：命中 `express-rules`，要实测 13 个入口：
  - ERP：`erp.cc.write`、`erp.cc.stock-entry`、`erp.cc.pos-day`、`erp.cc.pos-refund`；
  - Cowork：`cowork.cc.channel`、`cowork.express.push`、`cowork.mrerp.push`、`cowork.companies`、`cowork.connections`、`cowork.history`、`cowork.cc-chat.push`、`cowork.cc.troubleshooting`；
  - CC：`cc.operator.page`。
  - 新共用件登记后再加上它自己的入口。
- 改了 `pearnly_core`，合入前在生产镜像跑候选离线检查（memory「共用层改动要跑候选离线检查」）。

## 7. 分块施工计划

### 7.1 依赖与先后

| 分支 | 关系 | 结论 |
|---|---|---|
| `pearnly/drop-own-taxid-block` | 它删 `account_match` 的本方税号判断，改 `service.py` 的 `_require_matching_account` 一带和单测。本需求要改同一个 `service.py`（`operator_check` / `prepare` 收齐）。它不合入的话，卡片的「不能写」行会列出被税号挡下的单 | **它先合入**。本需求 W2 从它合入后的 `main` 开 |
| `pearnly/cc-unified-feed`（U1） | U1 改 `C/push/application/service.py`（`_operator_target`）、`E/express/operator.py`（`book_target`）、`K/express_operator/api.py`（precheck 带 `targets`）、`sessions.py`。本需求改同样几个文件的预检部分 | **U1 先合入**。共用层契约 W1 只加新文件，不碰 U1 的文件，可以先做 |
| `pearnly/cc-unified-ui`（U2） | U2 写 `tool-uis/operator-*.tsx`、`operator-shared.ts`、`pending.tsx`、`globals.css`。本需求的卡片主体就从 `operator-start.tsx` 抽出 | **U2 先合入**，前端 W4 在它之上做。U2 有两处与 U1 对不上（不传 `groups`、只读顶层 `target`），建议 U2 合入前自己修掉；没修就由 W4 修 |
| 稳定性整治（memory：11-04 起停新功能 1–2 天） | 本需求是新功能 | 施工时机由主会话定（Q10） |

### 7.2 分块（按机器耗时；写代码是本窗口写，机器跑是闸门、构建、流程）

| 块 | 内容 | 主要碰的文件 | 写代码 | 机器跑 | 依赖 |
|---|---|---|---|---|---|
| W1 共用层契约 | `write_precheck/contracts.py`（分组、去重、状态、新预选规则）、`products.py`、`api.py`；纯函数单测；架构检查 | 新 `K/write_precheck/**`、`tests/test_write_precheck_contracts.py` | 3 h | 0.3 h | 无，现在就能开 |
| W2 Cowork 钩子 | 一次收齐（录不录、入账方式、方向分组记忆、现用账套、科目组、MR.ERP、选择项、服务品口径）；预选来源；`remember` 分派；`operator_check` / `choices_view` 改成转调；`sweep` 记 `choice_needed`；定时任务转调 | `C/push/application/service.py`、`C/assistant/operator_source.py`、`C/assistant/pending_api.py`、`C/push/automation/sweep.py`、`C/folder_intake/push.py`、`C/hooks.py` | 5 h | 候选离线 0.7 h | W1、drop 与 U1 合入 |
| W3 ERP 钩子 | `prepare` 收齐（账套、科目组、选择项、收银三项、库存单科目）；`remember` 分派；`operator.precheck`、`api.preflight` 转调；待处理行进对话卡片 | `E/express/prepare.py`、`E/express/operator.py`、`E/express/api.py`、`E/express/pending.py`、`E/hooks.py` | 3.5 h | 0.5 h | W1、U1 |
| W4 CC 卡片 | 从 `operator-start` 抽 `PrecheckBody`（分组、已定折叠、未定在前、张数、事实行）；可搜索选择框；窄屏叠排；`operator_start` / `operator_choose` / 待处理尾部 / 直写门四处用它；修 U2 的 `groups` / `targets` | `F/components/tool-uis/operator-*.tsx`、新 `precheck-body.tsx`、`F/components/pending.tsx`、`F/globals.css`、`F/i18n.ts` | 6 h | 两处构建 0.2 h | W1、U2 |
| W5 直写门 | `needs_approval` 支持函数门；执行记录记「出过卡片」；两产品推送工具挂门；`proposals` 参数；删四个选择工具与相关说话规矩 | `K/assistant/tools.py`、`K/assistant/toolrun.py`、`C/assistant/tools.py`、`E/express/cc_tools.py` | 2.5 h | 0.3 h | W2、W3、W4 |
| W6 Desk 弹窗 | Cowork 复核页、ERP 单据提交前改读统一输出，Frappe Dialog 分节；删退回后的逐类按钮 | `C/public/desk/targets.js`、`C/public/desk/intake-upload.js`、`E/public/desk/transactions.js` | 3 h | 流程 0.5 h | W2、W3 |
| W7 LINE | Cowork 上传前已定跳过；识别后卡片加预选行与「ยืนยัน」；postback 处理与旧按钮拒收 | `C/line/flow.py`、`C/line/cards.py`、`C/line/intake.py` | 2.5 h | 0.2 h | W2 |
| W8 流程、地图、文案 | 新流程 2 条、改 4 条；地图行；共用件登记；`check-copy --report`；改已有文案的清单 | `scripts/flows/**`、`scripts/impact/**`、`scripts/i18n/**`、本记录 | 3 h | 受影响流程约 1.5 h，全闸门 0.5 h | W2–W7 |
| 测试环境 | `testenv.sh live cowork / erp`，用户试用 | — | — | 十几秒生效；等用户 | W8 |

- **合计**：写代码约 28.5 小时，机器约 4.7 小时，另加等用户试用。
- W1 可以和 U1 / U2 并行，现在就开。
- W2 / W3 可以两个写者并行（文件不重叠）。W4 必须在 U2 合入之后。
- CI 推完 15 分钟以上查一次，不轮询。

### 7.3 风险

| 风险 | 处置 |
|---|---|
| 「一次收齐」改了 `prepare` 的退出顺序，可能让以前在第一道就退出的单走到后面的计算里出错 | 收齐只在判定路径（`check`）上做；真正写入的 `prepare` 仍按原顺序退出。测试比对「判定的项 = 逐道退回后人被问到的项之和」 |
| 动态门每次推送多跑一遍判定（每张一次 `choices_view`，一次最多 20 张） | 与现在 `_push_one` 自己跑的是同一份计算，第二次走缓存的主档；实测超过 1 秒再考虑把判定结果在同一请求内复用 |
| 预选规则新加了 8 条，判错会被人一键带过 | 预选不自动生效，要人点；卡片按项可改；科目、现 / 赊的改值进 `Cowork Express Choice Change`（R1 改值记录）可追 |
| U1 / U2 合入时间不定 | W1 先做；W2 起等它们 |

## 8. 要主会话定的问题

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| Q1 | 直写时，全部已定的一批单是否不出卡片、直接写 | 是。只在有预选、未定或结果不明时出；代录的开始卡照旧一直出 | 每次推送都弹卡片，等于多点一下 |
| Q2 | 定时任务、自动推里 CC 的预选是否要人点才生效 | 是（原则 4 的落法）：只有已定的自动写；预选等人点 | 自动套用预选的话，判错会无人值守地写进账 |
| Q3 | 直写判不出方向时，是否也按对方记住（沿用代录的分组记忆） | 是，一套判断 | 直写每次都要人在复核页改方向 |
| Q4 | 没有规则预选的项（例如新供应商的费用科目）：直写时让 CC 在调推送工具时带提议作为预选；定时任务和代录不调模型、留作未定 | 是。不多调模型、不加成本 | 定时任务这类项一直是未定，要人从空下拉里选 |
| Q5 | 转账凭条、预扣税凭证「录不录」的预选值 | 都预选「不录」，与 ERP 写死的口径一致 | 没有预选就是未定，每个租户第一次都要从空下拉选 |
| Q6 | LINE 范围：Cowork 识别后的卡片加预选行与「ยืนยัน」；ERP LINE 上传前的几步（单据字段）不动 | 是。不多发消息 | LINE 上传的单停下时，用户在 LINE 上不知道 |
| Q7 | 简易税票、小票的税额，没有历史时预选「不抵（记费用）」 | 是，但要会计确认税法口径（简易税票一般不能抵进项） | 预选「抵」可能多抵进项税 |
| Q8 | CC 桌面版 `precheck.py` 能提前查的「主档税率是 0、票面有税」`tax_setting_mismatch`：Express 不拒，但会按 0% 录、丢进项税。进不进卡片 | 本批不进：要等 CC 桌面版把这项报上来（发版改动），先由 CC 在对话里提醒；记入下一批 | 进卡片要发 CC 桌面版 |
| Q9 | ERP 没有「记住的选择」查看页（Cowork 有）。卡片里已定项可改，是否就够 | 本批够；ERP 的查看页另立需求 | 已记住的 ERP 选择要改，只能等它下次出现在卡片里 |
| Q10 | 施工时机：稳定性整治期间（11-04 起停新功能 1–2 天）只做 W1（纯新文件、不上线），其余等整治结束、U1 / U2 / drop 合入后开 | 是 | — |
| Q11 | 删掉 CC 的四个选择工具（`cowork_push_choices` 等），改由卡片门承接 | 删。留着模型会继续用文字问，与卡片两套并存 | 两套问法并存 |

## 9. 评估模板各栏对照

| 模板栏 | 结论 / 见哪节 |
|---|---|
| 1 现有同类与复用 | 扩展现有：U2 开始卡、允许卡片机制、各产品的 `needed` 与记忆存储、W3 的对话尾部待处理。新建的只有共用判定包，理由是现在两产品、六个入口各拼一份（第 3 节） |
| 2 Frappe 原生 | Desk 侧用 `frappe.ui.Dialog`、Section Break、Select / Link + `get_query` 服务端搜索；CC 前端是 ADR-005 / 007 已登记的例外，沿用现有卡片组件（2.3） |
| 3 按类通用 | 覆盖「所有写进 Express / MR.ERP 的单」：采购、销售、库存单、收银日汇总与退货、服务类，以及直写、代录、定时、自动推、网页、LINE。不覆盖 ERP 单据录入字段（1.7） |
| 4 上下游 | ERP 单据流不变（先有单、再写账）；Express 载荷不变（记忆的键与值不变，`apply_to_payload` 不动）；CC 桌面版载荷不变，不升版本；Cowork、ERP 两边同一套（第 3 节） |
| 5 隔离与权限 | 3.4 |
| 6 入口 | 2.3、2.5、第 4 节 |
| 7 共用件 | 6.3（`express-rules` 13 个入口，加新 `write-precheck`） |
| 8 存储与成本 | 不加表、不加文件、不调模型、不多发 LINE 消息、不加定时任务；判定是本站数据库计算。无新增固定月费 |
| 9 删除作废追溯 | 记忆照现有口径（删就真删，`forget`）；改值留 R1 记录 90 天；作废已写进 Express 的单不涉及 |
| 10 文案与官方 UI | 5.2 末段；改已有文案先给用户看 |
| 11 测试与验收 | 第 6 节；验收：用户在测试环境直写、代录、定时各走一遍，看卡片分组、预选、记住后第二次不再问 |
| 12 要问的 | 第 8 节 |
| 13 排期 | 第 7 节 |
