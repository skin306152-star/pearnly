# 取消「本方税号与所选账套不一致」拦截：调查记录（2026-11-04）

- 分支 `pearnly/drop-own-taxid-block`，基线 `origin/main` = `013f4ac55`。
- 第 1–2 节是调查结果（调查时的代码行号以基线为准）；第 3、5 节记最终处理。用户 11-04 先后三次定了口径：
  1. 「都取消，exp 不拦我们也不要拦，直写，代录 cowork erp，line 网页端，全部同步」：本方税号、分店不符、自动推的 `own_tax` 核对都取消。
  2. MR.ERP 的「匹配闸」实际只管方向判不出：保留，改名「方向闸」，不改行为（主会话转用户定）。
  3. 「只要 exp 不拦，mr.erp 不拦，我们就不要拦」：第 3 节另外 6 条「只有我们拦」的规则逐条核对 Express 和 MR.ERP，两边都不拦的取消。
- 没合入 main，等主会话看过 diff 再合。
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

## 3. 预检路径上所有会拦单的规则（含最终处理）

范围：
- Cowork 写 Express：直写（`prepare`）、代录开始前检查（`operator_check` 加上桌面端 `companion/express_operator/precheck.py`）、代录交单（同样走 `prepare`）、自动推判定（`assess`）。
- ERP 写 Express：走同一份 `preflight_express`。
- MR.ERP：走 `mrerp/push/routing.py` + `transport.py`，单据防呆用的是同一份 `doc_sanity`。

权限、并发版本号、幂等不算业务规则，表里不列（`scope.require`、`request_conflict`、`storage.conflict`、`TargetSelectionError`）。

依据：
- (a) 代录配方 `companion/src/companion/express_operator/recipes/express-1.0.1.94.json`：服务器版 Express 1.0.1.94 实录过的弹框（10-26 / 10-29 实机）。
- (b) Express 表结构 `companion/src/companion/table_specs.py`。
- (c) Express 会计期间窗 `companion/src/companion/fiscal_period.py`：本年（CY）加次年（NY）两段，共 24 期。
- (d) MR.ERP 只有我们自己的代码可看：导入表生成 `mrerp/push/xlsx/generator.py`（注释说校验顺序照 MR.ERP 服务端）、建档 `mrerp/push/autocreate.py`、已知拒绝清单 `mrerp/push/user_data.py`。MR.ERP 没有实机拒绝记录的，表里写「没有拒绝记录」。
- (e) 没有依据的写「不知道」。

| # | 规则（原因码） | Express 也拒吗 | MR.ERP 也拒吗 | 最终处理 |
|---|---|---|---|---|
| 1 | **本方税号不符** `account_tax_mismatch` | **不拒**。(b) 单头 APTRN / ARTRN 和进项税 ISVAT 只记对方，本方税号只在 ISINFO。(a) RR 表头实录的格里没有买方 / 本方税号，实录弹框里也没有这类提示 | 不涉及（这条只核 Express） | **已取消**：直写、代录预检、代录交单、自动推判定全部取消。码、两句文案及翻译、月结异常码、CC 前端标签一起删 |
| 2 | 分店不符 `account_branch_mismatch` | **不拒**。(b) 单据上的 ORGNUM 是对方分店（`dbf_writer.py:591`），单据上没有本方分店 | 不涉及 | **已取消**，做法同第 1 条。对话分店钩子 `cowork_push_work_branch` 和 `companies.provider.work_branch` 一起删 |
| 3 | 日期在会计期间外 `account_period_mismatch` | **也拒**。(a) Message#. 70「วันที่ไม่อยู่ในงวด」 | 不涉及 | 保留 |
| 4 | 次年段，但新年度账套已建 `account_period_mismatch` | **不拒**。(c) Express 的期间窗含次年段，Message 70 不触发 | 不涉及 | **已取消**：删了硬闸里的 `newer_book_covers`。按日期选账套（`for_day`，用于定时任务、代录、自动推）照旧先选新年度账套；只是人手选了旧账套去推次年段的单，不再拦 |
| 5 | 单据口径没选 / 选了不录 | 不拒 | 不拒 | 保留：转账凭条、预扣税凭证录不录，是用户按公司选一次的口径（缺必要信息，问一次） |
| 6 | 归代录的单直写不接 `push.operator_owned`；上次结果不明 `push.result_unknown` | 不涉及（流程锁） | 不涉及 | 保留：流程锁，防重复入账 |
| 7 | 改单缺原单号 `push.prior_document_number_missing` | 不涉及 | 不涉及 | 保留 |
| 8 | 账套白名单 `account_set_not_allowed` | 不涉及（安全） | 不涉及 | 保留：安全 |
| 9 | 方向判不出 `direction_unknown` | 不涉及：不知道方向，就不知道开哪种单 | MR.ERP 同样要先定导入模块 | 保留（缺必要信息，问一次）。MR.ERP 原「匹配闸」改名「方向闸」`direction_unsettled`，原因码 `ERR_ACCOUNT_SET_MISMATCH` 改为 `ERR_DIRECTION_UNKNOWN`。方向明确的别家票照常写 |
| 10 | 方向没开 `direction_not_enabled` | 不涉及（连接配置） | 不涉及 | 保留 |
| 11 | 外币 `currency_not_thb` | 不知道 | 不知道 | 保留：不在这次 6 条里；管钱底线，否则会把外币金额当泰铢记 |
| 12 | 买卖方同税号 `seller_buyer_same_tax` | **不拒**：(b) 单据上没有买方税号可比 | **没有拒绝记录**：(d) 生成代码和已知拒绝清单里都没有 | **已取消**。没写明方向时，Express 照旧停在第 9 条（两边同税号判不出方向）。MR.ERP 方向闸第 ② 种情况补上「两边同一个税号」，照样停下问方向；以前这种单靠这条防呆拦住，否则会落进默认的赊销模块 |
| 13 | 贷项 / 退货单 `credit_note` | Express 有自己的减债 / 退货单，我们会当正向单写 | 同左 | 保留：不在这次 6 条里 |
| 14 | 押金 / 定金 `deposit_receipt` | **不拒**（Express 当普通单录） | **没有拒绝记录** | **已取消** |
| 15 | 古董日期 `date_implausible`（2000 年前） | **也拒**（落不进任何期间，Message 70） | — | 保留 |
| 16 | 未来日期 `date_future` | **不知道**：(a) 配方里没有这类弹框，(c) 期间窗也看不出期间内的未来日期收不收，没有实录 | (d) 我们的导入表生成对晚于今天 30 天以上的单报 `ERR_DATE_FUTURE`，注释说照 MR.ERP 服务端顺序，但没有实机拒绝记录 | **保留，标「待测试机实测」**（测法见第 4 节第 2 条）。自动推的 `date_future` 核对跟着保留 |
| 17 | 补开 / 倒签 `date_reissued`（ใบแทน 等） | **不拒** | **没有拒绝记录** | **已取消** |
| 18 | 对方税号不是 13 位 `tax_id_invalid` | **不拒**：(b) TAXID 是 C(15) 自由文本。超过 15 位时，CC 桌面版写账自己的长度检查会拒（`dbf_writer.py:81-90`，对应 Express 列宽），这条照旧 | **没有拒绝记录**：(d) 建档按原数字写，最多 20 位 | **已取消**。自动推的 `party_tax` 核对（正式税票对方税号要 13 位且校验位对，才算 A / B）只是档位信号，不拦人推，这次没动，见第 6 节 |
| 19 | 金额不闭合、缺总额、完整税票只读到总额 `amounts_not_consistent` | 不拒（Express 按明细自己算） | — | 保留：管钱底线，金额护栏 |
| 20 | 缺单号 `low_confidence:invoice_no_missing` | 不知道 | (d) `ERR_NO_INVOICE_NO` | 保留：不在这次 6 条里 |
| 21 | 缺日期 `bad_or_missing_date` | 也拒（日期必填） | 也拒 | 保留 |
| 22 | 金额缺失或为 0、正式票缺卖方 | 卖方：也拒（(a) 主档里找不到会弹查找框）；金额：不知道 | (d) `ERR_NO_TOTAL_AMOUNT` | 保留 |
| 23 | 缺科目配置、分录不平 `entry_not_balanced` | 分录不平也拒 | — | 保留：管钱底线 |
| 24 | 科目不在科目表 `account_not_in_chart` | 同类的码 Express 会弹查找框 | — | 保留 |
| 25 | 要人选（`choice_needed:*`）、科目表没读到、入账类型要选 | 不涉及（缺信息） | 同左 | 保留：问一次 |
| 26 | 桌面端代录预检 `unsupported`、`party_missing`、`item_missing` | `party_missing`、`item_missing` 也拒（(a) 查找框） | — | 保留 |
| 27 | 桌面端：主档税率是 0、票面有税 `tax_setting_mismatch` | 不拒（会按 0% 录，进项税就丢了） | — | 保留，作为要人定的一项：不在这次 6 条里 |
| 28 | 桌面端：疑似重复 `duplicate_ref` | 不拒（1029 弹框按 ตกลง 继续） | — | 本来就只提示不拦 |
| 29 | 负库存、授权 | 也拒（(a) 4224 / 4091、-330） | — | 是 Express 自己拦的，不是我们的规则 |

## 4. Express 本身怎么处理「单据本方税号 ≠ 账套公司税号」

- **代码和实录依据（有）**：Express 的单据和进项税记录里没有本方税号的位置（见第 3 节第 1 条，依据 (a)(b)），录单时也没有地方输入或核对买方税号。所以从结构上，Express **不会**按单据上的本方税号拦单：打开哪家公司的账套，单就记在哪家，进项税也进这家公司的进项税报表。
- **专门实测（没有）**。要实测的话，做法如下（测试机 C 盘的 Express 可以自由写；动测试机前先问主会话，并登记占用）：
  1. 在测试机打开 C 盘 Express（`C:\PearnlyExpress`）里的一份 T 账套，记下它 ISINFO 里的公司名和税号。
  2. 在「RR - ซื้อเงินเชื่อ」录一张买方是别家的税票（例如 BJC 那批，买方 0135556012961）：选对方、填 เลขที่บิล、日期（在期间内）、一行明细，保存。**同一次顺带测第 16 条**：再录一张日期晚于工作日期、但仍在期间内的单，看会不会弹框。
  3. 每一步截图，看三件事：① 表头有没有本方 / 买方税号格；② 保存时有没有弹框；③ 当月「รายงานภาษีซื้อ」里这张是不是列在本账套公司名下。
  4. 录完按 Express 原生流程作废这张（或者直接用一次性的 T 账套），不留测试数据。
- **风险（主会话和用户已知悉）**：Express 不拦，不等于这样做账一定对。BJC 那批确实是别家公司的票，现在会照常录进 มานะชัยบริการ，进项税也算到它头上。泰国税法一般不允许用抬头不是本公司的税票抵扣进项税（ภาษีซื้อต้องห้าม），具体条文没有核。用户定的口径是：CC 在对话里凭判断提醒，规则层不拦；自动推也照推。

## 5. 实际改动（分支 `pearnly/drop-own-taxid-block`）

- **共用层 Express 规则** `express_rules/targets/account_match.py`
  - `conflict(adapter, account, *, fields)` 只剩一条：日期不在整个会计期间（本年 + 次年）里。
  - 删掉 `TAX_MISMATCH`、`BRANCH_MISMATCH` 和两句文案，以及 `_other_company`、`_valid`、`company_branches`、`wrong_branch`、`newer_book_covers`、`_same_company`、`_row_span`。
  - `for_day`、`for_month`、`fiscal_part`、`printed_branch` 不变。
- **共用层单据防呆** `express_rules/push/doc_sanity.py`
  - 删掉 `seller_buyer_same_tax`、`deposit_receipt`、`date_reissued`、`tax_id_invalid` 四条。
  - `check_document` 去掉不再用的 `direction` 参数，调用方 `preflight.py` 和 MR.ERP 的 `routing._doc_sanity_reason` 同步改。
- **MR.ERP** `mrerp/push/routing.py`、`transport.py`
  - 匹配闸改名为方向闸：`direction_unsettled`、`DIRECTION_UNKNOWN_CODE = "ERR_DIRECTION_UNKNOWN"`、`_hold_unsettled`。
  - 方向明确的别家票照常写。
  - 第 ② 种情况补上「两边同一个税号」（原来由第 12 条防呆拦）。
- **Cowork 推送** `push/application/service.py`
  - `_require_matching_account(loaded, target, fields, invoice_date)` 只核期间；删掉 `_work_branch`。
  - 代录开始前检查 `operator_check` 用的是同一处，一起生效。
  - `assess.py` 同步新签名。
- **自动推** `push/express/automation.py`
  - 删掉 `own_tax`、`branch` 两条核对，删掉 `_own`、`_own_mismatch`、`_wrong_branch`。
  - 档位里的「本方」`own` 项删掉。以前票面没印本方的收据最高只到 B，现在不再因此降档。
  - `Facts.catalog` 不再有人用，删掉；`assess.py`、`scripts/da-replay.py` 同步改。
- **复核原因** `history/review.py`
  - 删掉 `own_tax` 原因和对应参数；`history/storage.py` 不再取工作空间税号。
  - 旧票上已存的 `own_tax` 复核原因，下次写票时会重算掉。
- **钩子与异常码表**
  - `hooks.py` 删掉 `cowork_push_work_branch`；`companies/provider.py` 删掉 `work_branch`。
  - `companies/exceptions.py` 的 `CODES` / `CONFLICTS` 只留 `account_period_mismatch`。
- **文案**
  - 删掉两句拦截文案（本方税号、分店）。
  - 删掉 `intake.error.tax_id_invalid`、`seller_buyer_same_tax`、`deposit_receipt`、`date_reissued` 四条：`messages.tsv` 和四个 `translations/*.csv` 都删，含英文原文行。
  - 删掉 `copy-approved.tsv` 里分店文案的登记行。
  - `check-copy.py` 结果：新增 0、改动 0、删除 8。
- **CC 前端**（主会话同意）
  - 拣入 U2 的 `3c1a07d15`，删三处「税号不符」标签。
  - 本分支再删三处「分店不符」标签（`frontend/assistant/src/i18n.ts`，英 / 中 / 泰各一处）。前端别处没动。
- **契约** `tests/fixtures/cc-month-contract.json`：用 `MonthLedgerTests` 同一段取数，按服务端真实输出重新生成。例子里的停单从本方税号换成了期间不符。
- **回放脚本** `scripts/da-replay.py`：删掉「本方税号」原因那一行；识别闸不再需要本方税号清单，`--manifest` 参数删掉。
- **保留、不受影响**
  - 判进项还是销项仍用本方税号（`contracts.push.resolve_account_direction`）。
  - 待处理里按本方税号判单据归哪家公司（`companies/pending.py`）。
  - 识别纠错 `claude_ocr.rules.fix_own_tax`。
  - ERP 采购识别时剔除本公司自己开出的票（`recognition/checks.issued_by`）。
  - CC 桌面版写账前的一致性闸（目录 ISINFO 公司名 == 所选账套）：防写错目录，不看票面。
- **历史数据**：旧票上记着的旧原因原句（本方税号 / 分店），在月结台账里归为 `push_manual` / `push_failed`（单测有断言），再推一次就会通过。

### 单测（断言改为放行，没有删断言凑通过）

| 路径 | 断言 |
|---|---|
| 直写 | `test_cowork_push_direction.PrepareDirection.test_a_document_naming_another_buyer_goes_into_the_chosen_set`：进项、销项的别家票都照常准备推送；没写方向的交给方向判定。`BranchNotChecked`：别的分店照推，不再取对话分店。另有 `test_cowork_targets.AccountConflictTests`、`test_cowork_assistant_tools` 的规则用例 |
| 代录 | `test_cowork_push_direction.test_the_operator_precheck_lets_another_buyer_through`：别家买方的单，`operator_check` 回 ready；同一条路上期间外的单照旧 blocked |
| 自动推 | `test_cowork_da_automation.test_own_tax_is_not_checked`、`test_branch_is_not_checked`：本方税号是别家、分店不同，都是 A 类、没有原因 |
| MR.ERP | `test_cowork_push_direction.MrErpDirection.test_another_companys_document_with_a_known_direction_goes_in`、`test_an_undecidable_direction_still_stops_to_ask`；`test_cowork_mrerp_push.test_another_companys_invoice_with_a_declared_direction_is_imported`（在假 MR.ERP 站点里真导入成功）、`test_only_an_undecidable_direction_stops` |
| 防呆 4 条 | `test_cowork_express_doc_sanity`：同税号、押金、倒签、短税号都返回 None；外币、贷项、未来日期、古董日期照旧拦 |
| 次年段 | `test_cowork_push_direction.NextYearPeriod.test_the_hard_gate_only_stops_dates_outside_the_whole_fiscal_window` |
| 月结 / 复核 | `test_cowork_companies`（异常码表、旧原句归类、契约）、`test_cc_month_ledger`（码数 22，例子换成期间不符）、`test_cowork_review_reasons` |

### 影响清单与入口验证

- `python3 scripts/check-impact.py`：命中共用件 `express-rules`、`mrerp-rules`，要实测 23 个入口。
- `check-product-map.py` 退出码 0：规则 6（命中的上线必验行、带共用件的行、出过事的行有红 / 黄）没有失败项，不需要补地图测试。
- 这次不碰测试机、回归实验室和生产（任务书规矩），所以下表所有入口都**没有实机测**。

| 入口 | 结果 |
|---|---|
| `cowork.express.push`、`cowork.cc-chat.push`、`cc.operator.page`（代录预检）、`cowork.companies`、`cowork.history` | 离线单测通过（见上表）；实机没测 |
| `cowork.mrerp.push`、`erp.mrerp.direct`、`cowork.mrerp.catalog`、`cowork.cc.mrerp-setup` | 离线：`test_cowork_mrerp_push`（假 MR.ERP 站点）在候选离线检查里跑；实机没测 |
| `erp.cc.write`、`erp.cc.stock-entry`、`erp.cc.pos-day`、`erp.cc.pos-refund` | 离线：`test_erp_express_direct` 通过（ERP 本来没有本方税号闸，只受防呆 4 条取消的影响）；实机没测 |
| `cowork.cc.channel`、`cowork.connections`、`cowork.line.chat`、`cowork.execution`、`cowork.assistant`、`cowork.cc.troubleshooting`、`shared.navigation`、`shared.team.members`、`shared.tax-documents`、`cowork.tax-documents` | 只经 `hooks.py` 或共用件间接命中，这次没改它们的行为；靠全闸门和候选离线检查覆盖；实机没测 |

本地闸门、候选离线检查和 CI 的结果见第 7 节。

## 6. 剩下要定的

1. 第 16 条未来日期：等测试机实测 Express 收不收期间内的未来日期，再定要不要取消。
2. 自动推里其余几个只决定「自动推还是交给人」的档位信号（`party_tax` 对方税号校验、`text_*` 原文核对、`void`、`day_month` 等）这次没动。按「不留交给人这一档」的口径要不要也放开，请主会话定。
3. 第 4 节的 Express 实测要不要做。
