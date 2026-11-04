# 取消「本方税号与所选账套不一致」拦截：调查记录（2026-11-04）

- 分支 `pearnly/drop-own-taxid-block`，基线 `origin/main` = `013f4ac55`。
- 本次只做调查，没有改产品代码，没有跑闸门，也没有推送（主会话 11-04 转用户指示「先查清楚」）。改不改由主会话和用户看过后定。
- 起因：11-04 BJC 代录实测（报告 `side-session-notes/.../BJC代录实测-11-04/报告-BJC代录实测-11-04.md` 第一节）。
  11 张 BJC 开出的税票买方都是 บริษัท นิวเบสท์ (ปทุมธานี) จำกัด（0135556012961），推往的账套是 มานะชัยบริการ（0735527000289，
  按日期选中 C 盘 T.9）。代录开始前检查 22 张全部报「单据上的本方税号与所选账套不一致」，一张都开不了工。
  **注意：这批单确实是另一家公司的票**，不是识别把税号读错了。规则取消以后，同样的情况会照常录进 มานะชัยบริการ 的账套（含进项税），见第 4 节风险。

## 1. 规则在哪里

只有一份规则：`apps/pearnly_core/pearnly_core/express_rules/targets/account_match.py`（共用层，纯函数）。

- `conflict(adapter, account, direction, fields, catalog, work_branch)` 依次判三条，命中就回原因码：
  1. `TAX_MISMATCH = "account_tax_mismatch"`：`_other_company()`。有方向时：进项看买方税号，销项看卖方税号；它是 13 位、校验位对，
     并且和账套报的税号（目录快照 `attributes.tax_id`，CC 桌面版读 ISINFO.TAXID）不同，就拦。判不出方向时：买方是别家的有效税号，
     卖方也不是这份账套，才拦。读不出、校验位不对、账套没报税号的，都不拦。
  2. `BRANCH_MISMATCH = "account_branch_mismatch"`：同一法人在这条连接里有两个以上分店账套时，票面本方分店与所选账套不同（`wrong_branch`）。
  3. `PERIOD_MISMATCH = "account_period_mismatch"`：单据日期不在账套会计期间内；或者落在次年段，但新年度账套已经建好（`newer_book_covers`）。
- 只判 Express（`adapter == "express"`）。ERP 产品的 Express 写账不走这条规则（见第 2 节）。

## 2. 调用方（全部入口）

### 2.1 硬拦（拦下就不写）

| 调用点 | 文件:行 | 覆盖的入口 | 拦下后的样子 |
|---|---|---|---|
| `service._require_matching_account` | `apps/pearnly_cowork/pearnly_cowork/push/application/service.py:369-380` | 被 `prepare()`（:188）调用，所以 **Cowork 所有 Express 直写都过它**：复核页「提交」（`push.application.api.prepare/submit`）、CC 对话推送（`assistant.tools._push_one` / :953）、待处理页推送、定时任务文件夹录入（`folder_intake.push` :228）、网页上传与 LINE 的扫描自动推（`push.automation.sweep` :120）、**代录交单**（`assistant.operator_source.submit` → `tools._push_one(operator=…)` → `prepare`） | `frappe.throw(_(CONFLICT_MESSAGES[reason]))`。`submit()` 接住后整笔回滚，原因原句记在票上（`push_state.record_failure`），列表状态图标、复核页、待处理页显示这句 |
| `service.operator_check` | 同文件 :595-622（:619 调 `_require_matching_account`） | **代录开始前检查**（共用层 `express_operator.products.precheck` → Cowork 钩子 `assistant.operator_source.precheck`） | 回 `{"state": "blocked", "message": "单据上的本方税号与所选账套不一致，请核对账套后重新发送。"}`，CC 实操页逐张显示（BJC 截图 42a/42b） |
| `assess._assess` | `apps/pearnly_cowork/pearnly_cowork/push/application/assess.py:91` | 自动推之前的判定：定时任务（`folder_intake.push` :264）、网页上传 / LINE 扫描（`push.automation.sweep` :92）、待处理作答后重判（`assistant.pending_api` :349） | `conflict` 非空就不跑 `prepare_express`，`facts.blocked` 有值，`automation.checks` 记 `blocked`，这张不自动推，留给人推。人去推时又会被 2.1 第一行拦住 |

LINE 没有单独的推送入口：LINE 上传的票由扫描自动推（`sweep`，trigger=line）判定和推送，结果同上。批量推送（CC 一次推多张、`outcomes` 上限 20）是逐张调用 `prepare`，结果同上。

### 2.2 原因码和文案还在哪些地方被引用

| 位置 | 内容 |
|---|---|
| `apps/pearnly_cowork/pearnly_cowork/companies/exceptions.py:29,53,54,62` | 月结台账的异常码表 `CODES`（`account_tax_mismatch` → entry / action / open）；`CONFLICTS`；`_CONFLICT_TEXTS` 把票上记的原句认回原因码 |
| `apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv:1112`，`translations/{zh,en,th,ja}.csv:1856` | 文案：中「单据上的本方税号与所选账套不一致，请核对账套后重新发送。」；英 "The own tax ID on this document does not match the chosen account set. Check the account set and send it again."；泰、日各一句 |
| `frontend/assistant/src/i18n.ts:540,627,713` | CC 月结台账的标签：`account_tax_mismatch`：「Tax ID mismatch / 税号不符 / เลขภาษีไม่ตรง」。**这几行在 `pearnly/cc-unified-ui` 正在改的 `frontend/assistant/**` 范围里** |
| `tests/fixtures/cc-month-contract.json:136,282` | 月结台账契约样例 |
| 文档字符串和注释 | `service.py:370`、`history/review.py:8`、`assistant/pending_api.py:14,210`、`folder_intake/follow.py:17`、`push/express/automation.py:141,534,574`、`docs/adr/0021…:293`、多份 `docs/work/` 旧记录（旧记录不改） |
| 单测 | `tests/test_cowork_targets.py:451-478`（5 处断言 `TAX_MISMATCH`）、`tests/test_cowork_push_direction.py`（32 处，含加载真规则和原因表）、`tests/test_cowork_assistant_tools.py:4201,4210`、`tests/test_cowork_companies.py:1413,2789-2832,2963,3012`、`tests/test_cc_month_ledger.py:28-34,257,287`、`tests/test_cowork_reason_language.py:84` |

**历史数据**：已有票的 `last_push_reason` 存的是这句原文。码删掉以后，`exceptions._CONFLICT_TEXTS` 认不回这句，这些旧票在月结台账里会落到通用的 `push_manual / push_failed`。这些票再推一次就会通过，所以影响只是显示。

### 2.3 只是用到本方税号，不拦单（这些不受影响，应当保留）

| 用途 | 位置 |
|---|---|
| 判进项还是销项：没写明方向时，按工作空间税号判，再按账套税号判 | `express_rules/contracts/push.py` `detect_by_tax` / `detect_by_account` / `resolve_account_direction`；preflight `_resolve_direction`；`service.prepare` :187；CC 工具 `assistant/tools.py:810`（MR.ERP 方向） |
| 待处理里一张单归哪家公司 | `companies/pending.py`（文件头第 7 行：先按推送去向的账套，再按本方税号） |
| 识别时纠正本方税号读错的一位 | `claude_ocr/rules.py:26 fix_own_tax`、`claude_ocr/engine.py:74,85` |
| ERP 采购识别：剔除本公司自己开出的票、把买方锚定为本公司 | `pearnly_erp_portal/purchasing/recognition.py:158-172,322-352`、`recognition/checks.py:93 issued_by` |
| 推送后核对对方档（对方税号，不是本方） | `push/express/written_check.py` |

### 2.4 同类、但不是这条硬闸（另作决定）

| 位置 | 作用 | 是否拦人推 |
|---|---|---|
| `push/express/automation.py:521-527,574-577` `own_tax` | 自动推的核对：账套有 13 位税号时，票面本方税号要正好是它；正式税票没印本方也不过。不过的单不自动推，交给人 | 不拦。只决定「无人值守自动推」还是「交人」 |
| `push/express/automation.py:1081-1085` 档位 `own` | 本方这一项算 A 还是 B；对不上就不给档 | 不拦 |
| `history/review.py:175-177` 复核原因 `own_tax` | 内部质量信号（10-02 起录入口不显示）；会进 `review_reasons`，从而让自动推交人 | 不拦 |
| `pearnly_core/mrerp/push/transport.py:206-208,272-289` + `mrerp/push/routing.py:235-275` | **MR.ERP** 的匹配闸 `confirmed_account_set_mismatch`：方向相反，或票面税号都不是套账主体，这一行就不导入（`ACCOUNT_SET_MISMATCH_CODE`） | 拦，但只拦 MR.ERP，不是 Express |
| ERP 产品写 Express（`pearnly_erp_portal/express/prepare.py`、`target.py`、`operator.py`） | 按 ERP 公司自己的税号、分店和日期选账套；单据是 ERP 自己的采购 / 销售单，不比票面本方税号 | 没有这条闸，ERP 直写和代录都不受影响 |

## 3. 预检路径上所有会拦单的规则

范围是 Cowork Express 的直写（`prepare`）、代录开始前检查（`operator_check` 加上桌面端 `companion/express_operator/precheck.py`）和代录交单（还是 `prepare`）。权限、并发版本号、幂等这类保护不算业务规则，没有列进表里（`scope.require`、`request_conflict`、`storage.conflict`、`TargetSelectionError`）。

「Express 也拒吗」一列的依据：
(a) 代录配方 `companion/src/companion/express_operator/recipes/express-1.0.1.94.json`，记的是服务器版 Express 1.0.1.94 实录过的弹框（10-26 / 10-29 实机）；
(b) Express 表结构 `companion/src/companion/table_specs.py`；
(c) 没有依据的写「不知道」。

| # | 规则（原因码） | 位置 | Express 也拒吗 | 建议 |
|---|---|---|---|---|
| 1 | **本方税号不符** `account_tax_mismatch` | `account_match` 经 `_require_matching_account`、`assess` | **不拒，只有我们拦**。依据 (b)：单头 APTRN / ARTRN、进项税 ISVAT 只记对方（SUPCOD / CUSCOD、对方 TAXID）；本方税号只在账套的公司资料 ISINFO 里，每份账套一条。依据 (a)：RR 表头实录的格是 แผนก、เลขที่ใบรับสินค้า、วันที่、ผู้จำหน่าย、Bill be、สาขา#、อ้างถึงใบสั่งซื้อ、เลขที่บิล、ลวท.、หมายเหตุ、เครดิต、ครบกำหนด、ขนส่งโดย、ประเภทราคา，没有买方 / 本方税号格。实录的弹框里也没有这类提示 | **本次要取消的就是这条**（待批）。见第 4 节风险 |
| 2 | 分店不符 `account_branch_mismatch` | `account_match.wrong_branch` | **不拒，只有我们拦**。依据 (b)：单据上的 ORGNUM 是对方分店（`dbf_writer.py:591` 取供应商主档 `party_orgnum`），单据上没有本方分店。选哪个分店的账套，就等于选了哪个分店 | 和第 1 条同一类（拿「所选账套」去比票面本方）。按用户的口径建议一起取消，请主会话定 |
| 3 | 日期不在会计期间 `account_period_mismatch`（期间外） | `account_match` | **也拒**。依据 (a)：`period_outside` 弹框「Message#. 70 / วันที่ไม่อยู่ในงวด」，outcome=blocked。桌面端代录预检也查（`period_outside / period_locked`） | 保留：提前拦，省得到 Express 里才失败 |
| 4 | 次年段但新年度账套已建 `account_period_mismatch` | `account_match.newer_book_covers` | **不拒**。旧账套的次年段 Express 能录（推断：次年段在期间窗内，Message 70 不触发；没有专门实测） | 这是「该推往新账套」的去向规则，不属于「Express 会拒」的一类。代录按日期自动选账套，正常不会撞上。建议保留，请主会话定 |
| 5 | 单据口径没选 / 选了不录 | `service._document_block` → `policy_rules.push_block` | 不拒，只有我们拦 | 保留：转账凭条、预扣税凭证录不录，是用户按公司选的口径（memory「录不录不写死」） |
| 6 | 归代录的单直写不接 `push.operator_owned`；上次结果不明 `push.result_unknown` | `service.prepare` :148、`operator_check` :604 | 不涉及（流程锁） | 保留：防同一张单重复入账 |
| 7 | 改单缺原单号 `push.prior_document_number_missing` | `service.prepare` :176 | 不涉及 | 保留 |
| 8 | 账套白名单 `account_set_not_allowed` | `preflight.py` | 不涉及（安全） | 保留 |
| 9 | 方向判不出 `direction_unknown` | `preflight.py` | 不涉及：不知道方向，就不知道该开 RR 还是销售单 | 保留。注意：取消第 1 条以后，没写明方向、票面两边税号都对不上工作空间和账套的单，还是会停在这里（这是「要问进项还是销项」，不是税号闸）。代录按分组把方向写到单上（`operator_source._settle`），不受影响 |
| 10 | 方向没开 `direction_not_enabled` | `preflight.py` | 不涉及（连接配置） | 保留 |
| 11 | 外币 `currency_not_thb` | `doc_sanity.py` | 不知道 | 保留：会把外币金额当泰铢记 |
| 12 | 买卖方同税号 `seller_buyer_same_tax` | `doc_sanity.py` | 不拒，只有我们拦 | 保留：必有一方读错，方向也判不出 |
| 13 | 贷项 / 退货单 `credit_note` | `doc_sanity.py` | Express 有自己的减债 / 退货单；我们会当正向采购写，所以拦 | 保留，等支持减债单以后再改 |
| 14 | 押金 / 定金 `deposit_receipt` | `doc_sanity.py` | 不拒（Express 会当普通单录） | 只有我们拦。会计上押金不是费用。建议保留，请主会话定 |
| 15 | 古董日期 `date_implausible`（2000 年前） | `doc_sanity.py` | 也拒（落不进任何账套期间，Message 70） | 保留，与第 3 条重叠，没有坏处 |
| 16 | 未来日期 `date_future` | `doc_sanity.py` | 不知道（期间内的未来日期 Express 收不收，没有实录） | 只有我们拦的可能性大。常见原因是佛历年读错，建议保留，请主会话定 |
| 17 | 补开 / 倒签 `date_reissued`（ใบแทน 等关键词） | `doc_sanity.py` | 不拒，只有我们拦 | ใบแทน 是合法的替代税票，可以入账；风险是原票已经录过时重复。按用户口径属于可以放开的一类，请主会话定 |
| 18 | 对方税号不是 13 位 `tax_id_invalid` | `doc_sanity.py` | 不拒：TAXID 是 C(15) 自由文本 | 只有我们拦。按用户口径属于可以放开的一类；放开后进项税报表上会出现错的对方税号，主档建档也按税号认。请主会话定 |
| 19 | 金额不闭合、缺总额、完整税票只读到总额 `amounts_not_consistent` | `push/ledger.py` + `tax_documents/ledger_guard`（11-03 新护栏） | 不拒（Express 按明细自己算） | 保留：防写错金额 |
| 20 | 缺单号 `low_confidence:invoice_no_missing` | `push/ledger.py`、`confidence.py` | 不知道（เลขที่บิล 能不能空着保存，没有实录） | 保留，请主会话定 |
| 21 | 缺日期 `bad_or_missing_date` | `push/ledger.py`、`mapper.py` | 也拒（日期必填） | 保留 |
| 22 | 金额缺失或为 0 `amount_missing`、正式票缺卖方 `vendor_missing` | `confidence.py` | 卖方：Express 必须有对方档才能保存（(a) 主档找不到会弹查找框 `master_missing`）。金额：不知道 | 保留 |
| 23 | 缺科目配置 `no_account_set / no_purchase_account / no_ap_account / no_input_vat_account`、分录不平 `entry_not_balanced` | `mapper.py` | 分录不平也拒（总账要平）；缺科目就是生成不了单据 | 保留 |
| 24 | 科目不在科目表 `account_not_in_chart` | `preflight.py` | 同类的码 Express 界面会弹查找框（(a) 实录的是对方、商品；科目码没有单独实录） | 保留 |
| 25 | 要人选（`choice_needed:*`）、科目表没读到（`masters_wait`）、入账类型要选 `posting_kind_needed` | `service.prepare`、`mapper.py` | 不涉及（缺信息） | 不是拦，是问一次。保留 |
| 26 | 桌面端代录预检：`unsupported`、`party_missing`、`item_missing` | `companion/.../express_operator/precheck.py` | `party_missing`、`item_missing` 也拒（(a) 查找框 → blocked）；`unsupported` 是配方还没覆盖这类单 | 保留（已经是「直写建档 / 选已有 / 跳过」让人定） |
| 27 | 桌面端：主档税率是 0、票面有税 `tax_setting_mismatch` | 同上 | 不拒（会按主档 0% 录，进项税会丢） | 只有我们问。保留为要人定 |
| 28 | 桌面端：疑似重复 `duplicate_ref` | 同上 | 不拒（(a) 1029 弹框自动按 ตกลง 继续） | 已经只提示不拦（推送不查重不拦），不动 |
| 29 | 负库存 `negative_stock`、授权 `license` | 只在代录执行时 | 也拒（(a) 4224 / 4091、-330） | 是 Express 自己拦的，不是我们的规则 |

小结：只有我们拦、而且 Express 收的有第 1、2、4、12、14、17、18 条（第 16 条可能也是）。其中第 1、2 条都是拿「所选账套」去比票面本方，和用户说的「打开这家公司的这份账套就做账」直接冲突。其余几条是防写错金额、防重复、防读错的数据护栏，建议主会话逐条定。

## 4. Express 本身怎么处理「单据本方税号 ≠ 账套公司税号」

- **代码和实录依据（有）**：Express 的单据和进项税记录里没有本方税号的位置（第 3 节第 1 条依据 (a)(b)）。录单时也没有地方输入或核对买方税号。所以 Express **从结构上不会**按单据上的本方税号拦单。打开哪家公司的账套，单就记在哪家公司，进项税也进这家公司的进项税报表。
- **专门实测（没有）**：没有在测试机上专门录过「买方是别家」的单。照上面的结构，结论基本确定，但如果要实测，做法如下（测试机 C 盘 Express 可以自由写；动测试机前先问主会话并登记占用）：
  1. 测试机打开 C 盘 Express（`C:\PearnlyExpress`）里一份 T 账套，记下它 ISINFO 的公司名和税号。
  2. 拿一张买方是另一家公司的税票（例如这次 BJC 那批，买方 0135556012961），在「RR - ซื้อเงินเชื่อ」里照常录：选对方、填 เลขที่บิล、日期（期间内）、一行明细，保存。
  3. 看三件事，每一步截图：① 表头有没有本方 / 买方税号格；② 保存时有没有弹框；③ 当月「รายงานภาษีซื้อ」里这张是否列在本账套公司名下。
  4. 录完按 Express 原生流程作废这张（或直接用一次性 T 账套），不留测试数据。
- **风险（请主会话和用户知悉）**：Express 不拦，不等于这样做账是对的。BJC 那批确实是别家公司的票，取消规则以后会照常录进 มานะชัยบริการ，进项税也会算到它头上。
  泰国税法一般不允许用抬头不是本公司的税票抵扣进项税（ภาษีซื้อต้องห้าม）。具体条文我没有核，需要会计确认。
  用户已经定了「CC 在对话里凭判断提醒，规则层不拦」。建议保留 2.4 里自动推的 `own_tax` 核对：无人值守的自动推（定时任务、扫描）遇到这种单交给人，不自己写进去；人推和代录照用户口径放行。

## 5. 如果批准取消，改动范围（预案，还没做）

- `account_match.py`：删 `TAX_MISMATCH`、它的文案、`_other_company`，以及 `conflict()` 里的那一判；改文件头说明。如果第 2 条也批了，同样删 `BRANCH_MISMATCH` 一套。
- `companies/exceptions.py`：从 `CODES`、`CONFLICTS` 删这个码。
- 文案：删 `messages.tsv:1112` 和四个 `translations/*.csv:1856`（只删，不改别的文案）。
- `frontend/assistant/src/i18n.ts` 三行标签：**在 `pearnly/cc-unified-ui` 的范围里**，要先和主会话协调：等那个分支合入后再删，或者由那个分支顺手删。
- 文档字符串：`service.py`、`history/review.py`、`pending_api.py`、`folder_intake/follow.py`、`automation.py` 里提到「税号硬闸」的地方。
- 不动：方向判定、公司归属、识别纠错、ERP 采购识别（2.3）；2.4 的自动推核对等主会话定。
- 不碰 `express_operator/feed.py`、`sessions.py`（`pearnly/cc-unified-feed` 的范围），这次改动也用不到它们。
- 单测：把第 2.2 节列的断言改成「税号不一致照常放行」，直写（`prepare` / `_require_matching_account`）和代录（`operator_check`）各一条正向断言；分店、期间的断言保持不变。
- 影响清单（`python3 scripts/check-impact.py --files` 只读预演，按上面会改的文件算）：命中共用件 `express-rules`，要实测 15 个入口（`erp.cc.write`、`erp.cc.stock-entry`、`erp.cc.pos-day`、`erp.cc.pos-refund`、`cowork.cc.channel`、`cowork.express.push`、`cowork.mrerp.push`、`cowork.companies`、`cowork.connections`、`cowork.history`、`cowork.cc-chat.push`、`cc.operator.page`、`cowork.cc.troubleshooting`、`shared.tax-documents`、`cowork.tax-documents`）。
  产品地图（`check-product-map.py --files`）显示「没有改到地图里的入口」，所以规则 6 没有命中，不需要补地图测试。
- 改了 `pearnly_core`，合入前要在生产镜像里跑候选离线检查（memory「共用层改动要跑候选离线检查」）。

## 6. 待主会话定

1. 第 1 条（本方税号）取消：确认后施工。
2. 第 2 条（分店不符）是否一起取消。
3. 2.4 自动推的 `own_tax` 核对和复核原因 `own_tax`：建议保留，让无人值守的自动推遇到这种单交人。
4. MR.ERP 匹配闸是否也按同一口径处理（MR.ERP 本身拒不拒不知道；从注释看，导入不带卖方身份，默认是登录公司）。
5. 第 3 节里其余「只有我们拦」的几条（4、12、14、16、17、18、20）逐条定，这次都不动。
6. 是否在测试机上做第 4 节的 Express 实测。
7. CC 前端三行标签的删除时机（和 `pearnly/cc-unified-ui` 协调）。
