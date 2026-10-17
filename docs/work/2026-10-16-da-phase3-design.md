# 第 3 期：自动化档位、推后核对、语料回放（数字会计员，2026-10-16 定稿设计）

依据 [施工计划](2026-10-15-digital-accountant-plan.md)「自动化档位」「验证」两节与可靠性原则 1–3；建在第 1 期月结台账 / 统一异常清单、
[第 2 期客户规则](2026-10-16-customer-rules-phase2.md)、多电脑 T5「谁的活谁的电脑做」（[ADR-021](../adr/0021-cc-desktop-multiple-computers.md) 第 50–57 条）之上。
分支 `pearnly/da-phase3`，基于 `pearnly/multi-desktop` `5c11dc5b`。先只读勘察出草稿（`62a8da30`），再做两路对抗审查（安全、可行性与测量），
按审查逐条核对代码后定稿如下；处理结果见文末「审查处理」。**未施工**；末尾「要用户定」前三项用户 10-16 已定（见该节开头），第四项视覆盖表再问。

## 现状（勘察结论，审查已订正）

- 人发起的推送：网页复核页「提交」、待处理当场选（`assistant/pending_api.answer`）、CC `cowork_push_documents`（`assistant/tools._push_one`）。网页上传与 LINE 只抄去向（`ocr/execution/workflow._copy_targets`），不推。
- 唯一无人值守的推送是定时任务（`folder_intake/push._push_to`）：`choices_view` 没有要选的项就 `submit`，不看识别结果是否要复核，也不看每项按什么定的。
- 每项按什么定的已有记录：冻结目标 `decided_by`、载荷 `doctype_src` / `account_source`、抵进项 `item_src`（`field_rules.item_verdict`），经 `pushed.PAYMENT_FROM` / `ACCOUNT_FROM` 交给 CC。
- 识别「把握」：票上 `Cowork OCR History.confidence` 是 `ocr/pages.assess_confidence` 的 high / medium / low，**只表示字段填没填**；auto / yellow_confirm / needs_review 在每页 `_confidence_band`。
  图片直读（`ocr/engine/runner`）分数最高 0.95，永远到不了 `gates.bucket_confidence` 的 0.98 auto 线；企业读法（`ocr/enterprise/adapter`）只给 yellow_confirm / needs_review。分档没有用金标校准过。
- `common.amounts` 只读到含税额时按 7% 反推未税与税额，反推出来的数必然满足 7% 勾稽。
- 写入：CC 桌面版 `dbf_writer._verify`（进项按 DOCNUM 找行）/ `dbf_sales._verify`（销项按 `since` 取本次追加行）核含税额、借贷平、ISVAT 在不在，**不符就 `_restore` 整单回滚**。
  科目、对方码、日期、单别、参考号只在按月回读 `readback/diff.compare` 时才比。Pearnly 写的行 `USERID` / `CREBY` = `PEARNLY`。
- 识别入库查重（`history/duplicates`）同号 / 同日同卖方只查本人上传的，可按人关掉，只在入库时查一次。
- 电脑领单（`preparation.revalidate(mode='lease')`）只重核路由与 `basis_hash`（映射、供应商画像、主档行），不含账套记忆、公司规则、票的版本。
- 语料答案（`scripts/eval-invoices.load_answers`）只读进货 / 销货单与进 / 销项税登记：单号、日期、对方名称 / 税号 / 分店、三项金额，**没有科目、对方码、RECTYP**。
  现成真识别结果约 933 份、27 家（两批金标），按「方向 × 形态」分层抽样，不是按家整月；共享回放库 `pearnly-ocr-replay` 10-13 时 313 条。

## 定稿条目

1. **每张单分三类**（纯函数新文件 `push/express/automation.py` 的 `classify`，不连库；回放与服务共用）：
   - **A 规则定**：每一项都由人或规则定，且第 2 条全部核对通过。
   - **B CC 判断**：第 2 条全部通过，但至少一项是 CC 按数据推断的。
   - **C 要人**：有要人选的项、任一核对不过、或被硬闸拦下。

   | 项 | A | B | C |
   |---|---|---|---|
   | 对方档 | 公司规则；账套记忆（记的正是主档唯一对上的那一档，或同税号 + 分店最近连续 3 张有证据的同一档，10-17）；票面 13 位税号 + 分店在主档里只对上一个档 | 没有同税号档，按票面新建；还没攒够连续 3 张的账套记忆、记的是新建 | 同税号几个档、分店对不上；**对方税号不是 13 位，或这份账套主档没报上来**（`choices.resolve` 不列对方、交 CC 桌面版按名字找） |
   | 存货 | 账套记忆；Express 有同名存货（归一后全等） | — | 其余 |
   | 费用 / 收入科目 | 任务默认、账套记忆、公司规则、Express 历史已定（第 12 条 v2 历史，近 12 个月 ≥3 张全记同一科目）；销项用人设的默认收入科目 | 分类映射、进项连接默认科目、这家历史排第一的建议；**v1 历史的「已定」** | 只有名称 / 大类建议，或没有 |
   | 现 / 赊 | 票面写明、票种口径（收据即已付）、账套记忆（最近连续 3 张有证据的同一记法，10-17）、任务默认、公司规则 | 供应商档案、银行流水佐证、固定默认、还没攒够连续 3 张的账套记忆 | 其余 |
   | 商品单入账方式（按公司选的） | 这家供应商的 Express 历史（v3：近 12 个月会计亲手录、不含 CC 推的 ≥3 张，只有一种记法）与公司选的一致，且载荷正好按它写：走库存有库存行、每行存货有着落；记费用没有库存行、票面 / 载荷明细品名不与库存品同名（用户 10-17） | 公司选的，供应商历史没有 / 不够 / 两种都有，或一致但载荷不按它写 | 历史与公司选的相反（`conflict:posting`，各层说法不一） |
   | 抵不抵进项、部门、POS 收款科目 | 账套记忆、任务默认、公司规则、同公司别年度借来的；部门不记或单据簿默认 | 抵进项按方向推断（`item_src = judge_direction=*`） | 其余 |

   两条通用降级：
   - **按名字作键的来源最高算 B**：对方没税号时，账套记忆、公司规则、Express 历史都以 `name:` + 归一名为键，通用店名会串到别家。
   - **各层说法不一就归 C**：任务默认、账套记忆、公司规则、近 31 天内读的 Express 历史，对同一项给的值不同（哪怕优先级能裁出一个）→ C。只作内部分流、不出提示；冲突提示与确认仍是第 4 期 `rule_conflict`。

2. **自动推前的核对**（全部确定性、零费用，只作内部信号，不出文案；用户 10-07「录入口不加识别后提示」）：
   - **硬闸照旧**：`account_match`、`doc_sanity`、`preflight_express` 全部 ready；拦下的就是 C，原因照现有记在票上。
   - **识别侧**：任一页 `_needs_manual_review`，或 `review_reasons` 非空 → C。**不拿 `_confidence_band` 当闸**（见现状），分档只在回放里按档报字段错率。
   - **票种**：文字层 PDF 才能进 A / B；**图片单一律 C**，直到回放证明图片单单独过第 15 条达标线（照片扫描件已测错误约 6%）。这是写在代码里的常量，随发布改，不是租户开关。
   - **原文对得上**（文字层 PDF）：含税额与单据日期能在文字层里找到原文，找不到 → C（现有原文核对只做了单号 `invoice_number_unseen`）。
   - **金额读出来的、不是推出来的**：正式税票要求未税、税额都是读出的，三项勾稽成立，容差沿用 `ocr/validation/math` 口径；`common.amounts` 反推出的单 → C。免税单税额为 0。
   - **对方税号**：正式税票 13 位、校验位对。
   - **日期**：落在推往那份账套的 `period_start`～`period_end` 之内（不认 `next_end`，防跨年写进去年账套）；推往的账套须正好是 T1 按这张单日期选出的那份；不晚于今天；
     不早于上传前 60 天；日、月都 ≤12 且日月对调后也落在期间内 → C。
   - **分店**：同一法人有 2 份以上分店账套时，票面须印分店号且对得上，否则 C（`account_match.wrong_branch` 在票面没印分店时放行，人推照旧）。
   - **作废 / 退货**：文字层有 ยกเลิก / VOID / ใบรับคืน → C；同一原件拆出 2 张以上票，或这张挂了证据页 → C。
   - **自动推专用的重复分流**（待用户确认，见「要用户定」2）：同对方税号 + 单号 + 含税额，在这份账套已有成功推送记录 → C，并照第 1 期列 `duplicate_number`。
     人发起的推送照旧不查重、不拦；Express 里已有同号单本身不算（自动推不读 Express）。

3. **档位按公司判，决定哪类自动推**：

   | 档位 | A 类 | B 类 | C 类 |
   |---|---|---|---|
   | L0 只提议（所有公司起点） | 提议，人推 | 提议，人推 | 照现有停下 |
   | L1 按规则 | **自动推** | 提议，人推 | 照现有停下 |
   | L2 整月 | **自动推** | **自动推**，每张列 `cc_judged` | 照现有停下 |

   - L2 本期只做到「整月能做成草稿」：A、B 都自动推、异常清单列齐。签字、锁月、对账与报税接进月结是第 6 期。
   - **L2 推 B 类有一道全局前提**：回放里 B 类单独过第 15 条达标线；没过之前代码里 L2 的 B 类仍只出提议（同第 2 条图片常量，随发布改）。
   - 档位按公司主档算，不按租户、无功能开关（用户「功能不按租户开关」）。只按第 8 条的数据升降，**没有手动升降按钮**（见审查处理 16）。
   - 「提议」：票上冻结 CC 准备好的做法，在待处理和「本月」卡里点「推送」。

4. **谁触发自动推**（CC 对话和待处理里人点的推送是人发起的，不看档位，照现在推）：
   - **定时任务**（按「要用户定」2 的建议写）：任务默认值是人定的规则。接在 `folder_intake/push._push_to` 里 `choices_view` 之后、`application.submit` 之前，
     用 `application.assess` 判类；**A 类从 L0 起就照推**（加第 2 条核对），B 类在 L0 / L1 改成提议，C 照现有。推送身份、读文件夹的电脑、离线排队照 ADR-021 第 51–53、57 条不变。
   - **网页上传 / LINE**：现有每 5 分钟的 cron 加 `push.automation.sweep`（不新增云上常驻）。只扫有 Express 去向、没人动过（`edit_count = 0`、无推送状态）、
     识别完满 `pending.SETTLE_MINUTES`（15 分钟）、近 3 天、未判过（`auto_checked_at` 空）的票，每轮最多 200 张。以上传人身份推，写的电脑是他登录过 CC 的电脑（第 50 条）；
     一台都没有（`writer_state` = MISSING）就不推、记 `no_computer`。
   - **MR.ERP**：本期不进档位，定时任务照现在「没有要选的就推」；它比 Express 的 L0 更松（不过第 2 条、`account_match` 只核 Express），这个缺口记在第 19 条。

5. **自动推的边界**：
   - 只推没人动过的单、只推一次；记成提议的，后来升档也不回头再推。有人改了票（`edit_count` 增）就作废提议，交给人。
   - 冻结目标加 `automation {class, tier, trigger: task|upload|line, reasons}`；下发时 `attended = false`（`companion/route_contracts.attended` 加一条），不弹「账套被占用」，等账套空出来再写（同第 55 条）。
   - **只派给能回第 6 条核对结果的 CC 桌面版**（按设备上报的版本）；写的电脑是旧版就转成提议。
   - **领单时重核**：带 `automation` 的记录在 `lease` 时再核票的 `record_version`、这家当前档位、账套记忆与公司规则的版本指纹（并进 `basis_hash` 之外的一项 `automation_hash`）；
     任一变了就 `cancel_prepared`，改记成提议。电脑关机几天后开机不写旧判定。
   - **没有新回读就暂停**：这家公司最近一次「合格回读」（第 7 条）覆盖的会计期间早于本单期间两个月以上，这张只出提议；`readback_needed` 照常提醒。按数据走，不是开关。
   - **测试站点的路径守卫**：站点名合 `pearnly_core.execution.replay.TEST_SITES` 时，自动推的目标账套路径不在 `C:\PearnlyExpress\` 下就不推（测试机的 CC 桌面版同时看得见 S 盘真账，同税号会归并到同一主档）。

6. **推后核对：只防写入走样与挂错对方，不防判错**：
   - 金额、借贷平、ISVAT 在不在，写入时已核、不符整单回滚，不重复。
   - **CC 桌面版**在 `_verify` 里顺手拿手上的载荷比本次写入行的对方码、各行科目（按它自己的现金 / 购货科目换法）、日期、RECTYP、REFNUM、ISVAT 的税号与分店，
     回执带 `verify {state: ok|mismatch, fields: [...], party: {tax_id, branch}}`（`party` 是实际挂到的对方档 APMAS / ARMAS 的税号与分店）。不把原始行带回服务端；`common.sanitize_push_meta` 加这三个键的白名单。
     `task_journal.remember` 一并存 `verify`，重发时 `duplicate_skipped` 的回执照样带回。
   - **服务端** `push/native/service.ack` 成功后：`verify.state` 照收；另拿**票面**的对方税号 / 分店（不取回执里的 `party_code`）比 `party`，不一致也算 mismatch。
     结果写推送记录新字段 `verify_state`（ok / mismatch / unverified）、`verify_json`；旧版 CC 桌面版不带 `verify` 记 unverified，由月度回读覆盖（自动推不会派给旧版）。
   - 不一致进异常清单 `written_mismatch`（核对段，action，去处 open）；照「推送不查重不拦」不回滚、不重推。自动推的还立刻降到 L0（第 8 条）。人推的同样核、同样列，不影响档位。
   - **判错（科目、现 / 赊、读错日期金额）这一层查不出**，靠第 2 条的分流、第 15 条回放和月度回读兜。

7. **升降档的证据**：
   - **完整月 = 单据所属会计期间**，不看墙上时间（压缩时间试点同样适用）。
   - **合格回读**：这一期的回读在这期 ภ.พ.30 截止日（`tax.periods.deadlines`）之后发起才算——会计通常报税前才在 Express 里改账，早回读会把「被改」量低。
   - **被改**：提议或自动推的单，在合格回读里 `changed` / `deleted` / `voided` / `missing`；或人推前改过票面 / 另选了某一项；或推后核对 mismatch。
   - **「人推前没改」不单独算正向证据**：样本只算被合格回读覆盖的单。所以「全部推送」与一张张点没有区别，可以保留。
   - 提议冻结 `auto_proposal`（每项的值、日期、单号、对方税号、三项金额）。人点推送时照常重判；重判出的值因规则 / 记忆变化（来源不是人）而与提议不同的，记作「过时」，不进样本。
   - 每月按公司统计（纯函数 `automation.tally`）：A / B 提议数与被改数、自动推数与被改数、核不上数。单据范围同月结台账（`ledger._documents`），回读改动经新钩子 `cowork_company_month_changes` 从 readback 取。

8. **升降档评估**（阈值见「要用户定」1，常量表 `companies/automation.THRESHOLDS`，所有租户同一套）：
   - **升**：合格回读完成时经 readback 新钩子 `cowork_readback_ready` 评估这家，一次升一档；L1→L2 还要第 3 条的全局前提。
   - **降**：自动推的单核不上 → `ack` 后立刻降到 L0；合格回读里自动推被改超阈值 → 回读完成时降一档。
   - 降档后要等降档之后开始的完整会计期间重新达标才能再升。
   - GET 不写库：评估只在回执与回读处理这两条写路径里做（备忘「Frappe GET 不提交」）。

9. **存储**（原生模型同步建表、加字段；**无数据补丁**，没有行的公司就是 L0）：
   - 新表 `Cowork Company Automation`：一家一行（company 唯一），workspace、tier、since、reason（码）、evidence_json（评估用的月度统计）、history_json（最近 24 次升降）。
     守卫同 `Cowork Company Choice`（`permission_query` / `has_permission` 拒绝、控制器守卫、拒绝分享、进 `companies.access.DOCTYPES`），删主档时一起删。
   - `Cowork OCR History` 加 `auto_class`、`auto_tier`、`auto_reasons`（码，≤140 字）、`auto_proposal`（JSON）、`auto_checked_at`（索引）。经 `history/push_state.record_assessment`
     用 `set_value(update_modified=False)` 写，不升 `record_version`。
   - `Cowork Push Record` 加 `verify_state`、`verify_json`；`automation` 放冻结目标 JSON，不加列。

10. **权限**：
    - 自动推：推的那一刻照现有判据重核推送人（任务负责人 / 上传人）的录入权限与连接授权（`application.prepare`），不过就不推、留给人。
    - 看档位：同月结台账读权限；看月度统计要录入权限。
    - 推提议：走待处理现有路径（`pending_api` → `tools._push_one`，录入权限 + 连接授权 + 单据读取范围），算人发起。

11. **接入点**（一个提交一件事；第 1 项是机械搬移，单独提交）：
    - 把 `application._decisions` 的判定核心抽成纯函数 `push/express/decide.py`，服务与回放共用。
    - `application.assess`：同 `choices_view` 只看不记（结束回滚），多回 `applied`、`decided_by`、载荷事实、预检原因、类别。
    - 钩子 `cowork_push_company_tier`（`companies.automation.tier_for_push`，按目录这一行认公司，同第 2 期 `for_push`）；出错按 L0。
    - 定时任务：`folder_intake/push._push_to`。扫描：`push/automation/sweep.py`，挂 `hooks.scheduler_events` 的 `*/5`。
    - 领单重核：`push/native/service.lease` 对带 `automation` 的记录比 `automation_hash`，变了 `cancel_prepared` 并记提议。
    - `pending.classify` 加 `cc_proposal`；`FIELDS` 加 `edit_count`、`auto_class`；C 类照旧落 `not_pushed`。
    - `exceptions.CODES` 加 `cc_proposal`（录账，action，去处新增 `push`）、`cc_judged`（录账，info，open）、`written_mismatch`（核对，action，open）。
    - `ledger.month` 录账段多计数 `auto`，返回多 `automation {tier, since, reason}`。新接口 `companies.api.push_proposals(company, period)`，每次最多 20 张（同 `OUTCOMES_MAX`）。
    - readback：`cowork_readback_ready`（`service._process` 结束时调）、`cowork_company_month_changes`。
    - CC 桌面版：`dbf_writer._verify`、`dbf_sales._verify`、`pos_journal.verify` 回 `verify`；`task_journal` 存 `verify`；
      `account_history` 升 `RESULT_VERSION = 2`，汇总里排除 `USERID = PEARNLY` 的行（CC 自己推的单不能攒成「已定」），服务端只认 v2 的「已定」为 A。
      `tests/test_companion_contract.py` 锁两处形状。

12. **界面**（建在新外壳，照 [CC 外壳设计](2026-10-16-cc-shell-design.md)）：
    - 「本月」卡与公司页概览共用组件，卡头下一行「自动推送：只提议 / 按规则 / 整月」；降过档时跟一个短原因（核不上 / 被改），按码翻译。
    - 录账段计数「已推 N（自动 M）」。
    - 异常清单 `cc_proposal` 行沿用第 2 期 ChooseRow 的「推送」；组标题旁「全部推送」调 `push_proposals`。`written_mismatch`、`cc_judged` 交给 CC，预填、不自动发送。
    - 手机端同一组件，照 ERP 手机端。不加说明性文案，没有确认步骤。

13. **CC**：`cowork_company_profile` 加 `automation`（档位、自何时、原因、近两期统计）；推送结果 `written` 加 `automation` 与 `verify`。
    GUIDANCE 加一句：档位只由数据升降；问到为什么没自动推，按 `auto_reasons` 照实说。不新增改档位的工具。

14. **新文案**（en / zh / th 同批，服务端码进 `messages.tsv`）：「自动推送」与三个档名「只提议 / 按规则 / 整月」（产品用词，点验时给用户看）、「全部推送」、
    三个异常码标签「待推送」「CC 判断推送」「写入与预期不符」、降档原因两句。

15. **测量：两层回放**（`scripts/da-replay.py`，零费用，只导入纯函数：`decide`、`automation`、`preflight` / `prepare`、`readback.diff`、`history_rules`、
    `ocr/results.prepare_history_batch`、CC 桌面版写入代码；不连站点、不调识别与模型）：
    - **第 0 步 盘点**：每家答案有哪些表；有没有 GLJNLIT 科目、对方码、RECTYP。缺科目的从 S 盘真账只读导出 APTRN / ARTRN / GLJNLIT（同 9-29 `xgl.py`、10-08 做法），
      夜里跑、最多两三路，不写。同时出「每家每期有现成识别结果的张数」覆盖表作证据。
    - **判定层**（覆盖 62 家所有月份）：答案每一行造成「识别全对」的票面字段，按家按会计期间顺序模拟：档位起点 L0，C 类和提议按答案由「模拟的人」推、人的选择记进账套记忆；
      Express 历史只用模拟期之前 12 个月的答案分录（防泄漏）；主档用 T 账套快照（含模拟期之后才建的档，结果偏乐观，报告写明）。
      在这层比三套阈值的档位时间线，量 A / B 判定准确率。
    - **识别闸层**（只用有真识别结果的件）：量「过了第 2 条识别侧与原文核对之后字段仍错」的比例，按票种（文字 PDF / 图片）和分档分别报。
      结果从共享回放库取 `result_json`（去掉 `provider_evidence`），离线重跑 `prepare_history_batch`，与生产同一套后处理；只用当前生产读法（3.8 之后）的轮次，旧读法单列。
      先在测试租户重跑一次 `scripts/ocr-replay-corpus.py`（不调谷歌）把各轮评测真识别过的件补进库，原件池写到一次性目录、跑完删。
    - **合并估计**：自动推准确率 ≈ (1 − 判定错率) × (1 − 闸后识别错率)，两层各给 95% 区间。
    - **计分**：复用 `eval-invoices.py` 的归一与分类（前缀不同、原件没印、海关单单列），对不上的设「待裁决」；按**反事实**算——所有判成 A（及 B）的单都当作被自动推一起计分，
      样本量与阈值无关。一张单所有计分项都对才算对；答案里没有的项不计分、单列。配不上答案的分两类列：会计没录（算错）和语料噪声（非票据、别年度、放错类，人工抽查定比例）。
    - **达标线**：A 类点估计 ≥99.5% 且 95% 下限 ≥99.0%（0 错要 ≥381 张、1 错 ≥563、2 错 ≥726）；B 类单独过同一条线才放开 L2 推 B；图片单单独过才放开图片。没达标按类修（不针对某家），再跑。
    - **在哪跑**：先核生产机 `/root/pearnly-corpus/06-录入推送` 答案是否齐（62 家、含科目）。齐就在生产机一次性容器跑判定层与识别闸层（`--memory 2g`、只读挂载、单个跑、跑完删），什么都不搬；
      不齐才把识别字段（不带原件）搬到测试机，按 `verification.md`「语料不出生产机」请开发窗口确认例外。写入层只证明写入忠实，用测试机现有 62 套回归做：
      工作副本放 WSL `/tmp`（不经 `/mnt/c`），原 T 账套不动；局域网 Accserver / S 盘不写，脚本拒绝 `\\` 与 `S:` 开头的路径；长任务从本机前台挂 ssh 跑，不在 WSL 里挂后台。
    - 报表 `报表.md` + `明细.tsv` 写在本机 scratchpad 与测试机 `_回放\<轮次>\`，不进仓库；结论摘要记回本文件。

16. **测试**：`classify` 来源 × 方向矩阵与第 2 条每一项；档位允许表、`tier_for_push` 出错按 L0；升降档（样本下限、合格回读时点、核不上即降、降后重新满期）；
    `folder_intake/push`（L0 推 A 记 B、C 不变、MR.ERP 不变）；扫描（满 15 分钟、只扫没人动的、上传人身份、没权限跳过、幂等、每轮上限、`no_computer`）；
    领单重核作废；测试站点路径守卫；`verify` 回执与 `party` 比对、旧版 unverified、`duplicate_skipped` 带回；`account_history` v2 排除 PEARNLY 行；
    待处理、台账、异常码与 `tests/fixtures/cc-month-contract.json`；前端档位行、推送 / 全部推送、手机宽度；回放配对与计分用合成样例单测。
    闸门：`check-architecture`、`check-i18n --strict`、全闸门 `gate.sh`；改到 `pearnly_core` 时跑候选离线检查。

17. **上线顺序**（AGENTS.md：需真实环境验证的改动，用户在测试机试用通过后才合并部署）：
    1. 本分支写完：全闸门、分支 CI。
    2. 两层回放达标，结论记进本文件。
    3. 测试机实时源码（`testenv.sh live cowork`）上：真机点验界面，并在 testcowork 选覆盖率最高的一家试点。
       - 试点任务固定写 T 账套，不用跟随公司；扫描只推票上写明 T 账套的单；第 5 条测试站点路径守卫兜底。
       - 用指定时刻压缩时间连跑 2–3 个会计期间：L0 期人在待处理推 → 过截止日后回读 → 升档 → L1 期自动推 → 回读。
       - 导入的测试租户 Frappe 不跑定时任务：扫描、定时任务、回读后的评估都手动调函数触发，写进试点步骤。
       - 识别走回放：试点文件夹只放有现成结果的件，零费用；凑不够一期 ≥阈值张数时见「要用户定」4。
    4. 用户试用通过 → 合入 main → 按批次部署：先服务端，再发 CC 桌面版正式版（带 `verify`、`account_history` v2）。
    5. 真实客户自动生效，全部从 L0 起（定时任务 A 类照推）；最快一个完整期加合格回读才可能升档。

18. **有意变化**：
    - 定时任务推 Express：A 类照推但多了第 2 条核对（以前会推的要复核件、图片单、日期可疑件改为停下）；B 类在 L0 / L1 改为提议。
    - 网页上传、LINE 的单在 L1 / L2 公司可能不点「提交」就被推（没人动、识别后满 15 分钟）。
    - 09-27「识别质量不拦推送」对人发起的推送不变；无人值守的推送把识别结果当内部闸，不出文案。
    - 自动推的单不弹「账套被占用」；回执多 `verify`，推送记录多核对结果；CC 自己推的单不再计入「Express 历史已定」。
    - **第一轮回放后（10-16）**：
      - 永续 / 混合库存账套里没声明入账类型的商品单：这家公司选过「走库存 / 记费用」就照选的推（人推、自动推一样），没选过照旧交人；
        复核页推送原因 `posting_needs_review` 旁多两个按钮，CC 同一个工具多两个值。
      - 新年度账套还没建时，下一年的单推进旧账套的次年段（T1 定时任务原先停下）；新年度账套已建时，人推往旧账套次年段的单被期间硬闸拦下
        （原先放行）。自动推的日期核对认次年段，由「推往的须是按日期选出的那份」把关。
      - 抵不抵进项按方向推断从 B 改 A；账套记忆定的科目要这家供应商在这份账套里最近连续 3 张同科目才算 A（原先记过一次就是 A）。
    - **第三轮回放后的审查处理（10-16）**：
      - 抵进项按方向推断要原文对得上才算 A：判抵扣的，文字层里核得到完整税票标题（`rules.from_title`，简式税票不算）与买方税号；
        判不抵的，识别票种与文字层标题都要正是简式税票 / 收据，否则算 B（第四轮闸层：判不抵的 10 张里 9 张会计抵了进项，识别都给「其他」票种）。
      - 商品单入账方式按公司选过的（`posting`）从 A 改 B：这类会交人的单不只是商品，第四轮按公司多数选，各家仍有一部分单与会计
        记法（有没有存货明细）不同（0%～15%，按公司多数选之后）。人推、自动推照公司选的推不变，只是 L1 不自动推这类单。
      - 「连续 3 张」只数有证据的推送：推的时候科目由人或规则定的（本票有人选、任务默认、公司规则、Express 历史已定），或凭账套记忆推出去、
        被合格回读覆盖且没被改的；回读里被改的断开。CC 自己凭记忆推出去还没回读核过的不算。
      - 存货一项按每一行算：每行都要有记过 / 规则给的存货码或 Express 同名存货才 A（原先账套里有存货主档就 A）。
      - 选过「小票抵进项」的单与映射器同一车道判据，永续账套里会交人的同样套用公司选的入账方式（原先点了按钮也推不动）。
      - 按日期选账套退到旧账套次年段前，要核得了「新年度账套还没建」：同一家同一分店里有会计期间没报全的账套、或本年起始更晚的账套，
        就选不出（T1 停下、自动推归 C）；人推的期间硬闸照「读不出不拦」不变。
      - 按月取账套（`identity.covering`：月结台账、主档页、档位统计与回读核对）同一规则：某个分店本年没有覆盖这个月的，用这个分店
        次年段含这个月的旧账套（原先次年那几个月归不到账套）。
    - **第四轮回放后（10-17，开发窗口按数据定）**：账套记忆定的现 / 赊、对方档也要「最近连续 3 张有证据的同值」才算 A，否则 B（原先记过一次就是 A；
      第四轮 A 类错 465 张里 ASIA-FA 账套记忆定的现 / 赊 260、对方档 65：记忆只记会计第一次的选择，之后不跟随）。三类各自的证据口径：
      - 科目（不变）：推的时候由人或规则定（本票有人选、任务默认、公司规则、Express 历史已定），或凭账套记忆推出去、被合格回读覆盖且没被改。
      - 现 / 赊：推的时候由本票人工裁决、任务默认、公司规则定（载荷 `doctype_src`），或被合格回读覆盖且没被改；票面写明、票种口径的单
        整条不看（记忆只用在票面没写的单上，见下面「第五轮前的审查处理」）；人在待处理页为这张单选的现 / 赊先进账套记忆、推出去也记
        `remembered`，同样要等回读。
      - 对方档：只认合格回读覆盖且没被改（来源不在载荷里）；只数同一 13 位税号 + 同一分店的推送；记的正是主档里唯一对上的那一档照 A（规则本来就给 A）；
        记的是新建（码为空）一律 B。
      - 回读按整张记录：一张推出去的单在回读里任一项被改（日期、现 / 赊、对方、单号、金额、VAT、分录科目），三类的连续一起断开。一次查询取这家
        最近 30 张推成功的单、回读只取一次（`assess._recent`）；都不是账套记忆定的不查库。
      - 销项凭记忆定的对方档不查（买方税号没有索引），按 0 张算 → B（回放销项 A 为 0，不影响第四轮 A 数；要恢复需先给识别历史加买方税号索引）。
      - 回放同口径：推出去的单按整张记录比答案定回读里改没改（原先只比科目，比线上宽）；C 类没出载荷的单，模拟的人选完、学进记忆后照线上再出一次载荷
        记进推送记录（推不出的按已知几类乐观近似，结果里 `pushed_from` 分开计）；汇总多对方档来源与「账套记忆定的三类按家 A / B / A 里错」统计。

    - **第五轮前的审查处理（10-17）**：
      - 现 / 赊：票面写明、票种口径定现 / 赊的推送整条不看——回读核过不算证据、被改也不断开（原先回读核过照算：三张写明现金、回读核过
        的收据能让记忆的现购在下一张没写付款方式的税票上升 A，正是 ASIA-FA 那一类）。证据只剩推的时候由本票人工裁决、任务默认、公司规则
        定的，和账套记忆 / 供应商档案 / 银行佐证 / 固定默认定的单被合格回读覆盖且没被改的。
      - 对方档：证据取 CC 桌面版实际写入的码（回执 `meta.party_code`，回读比的就是它；原先取载荷写定的码，CC 自己找档的单码为空、
        既不计数也不断开）；载荷写定一档、桌面版却写了另一档的按断开算。票面没印对方分店号、主档里同税号不止一档（或主档没报上来）时，
        记忆定的对方档最高 B（记忆键与连续张数都落在「总店」上，分店票会硬配到总店的档；`field_clean.clean_branch_no` 的口径）。
      - 科目：真写了库存行的单（回执 `line_modes`）回读不比分录（`readback.diff`），这种单的科目只认推的时候由人或规则定的，回读 confirmed
        不算（科目的口径原先对这种单写宽了）；`readback_marks` 的说明照实写。
      - 取数：没出载荷（要人选，本来就是 C）不查库；每期的回读在同一个请求 / 后台任务里只取一次（`companies.automation._month_found`，
        缓存在 `frappe.local`，扫描一轮判几十张同账套的单不再每张每期查一次）。多期合成一次查询没做：要改钩子 `month_changes` 的契约，
        而它的 500 行扫描上限跨期共用时会悄悄丢掉「被改」标记（漏断开，不保守）。
      - 「最新 30 张」的上限照旧：两次合格回读之间推了 30 张以上的高频供应商，最新 30 张都还没回读核过，记忆定的项一直 B。回放照线上
        先截 30 张再按分店筛（原先不设上限，偏乐观），并量出因此丢掉的 A（`memory_cap_lost`）；数大再定线上改「跳过没回读的再数」还是
        按时间窗口取。
      - **改取法（10-17，第五轮后）**：回放拆清「最新 30 张」挤掉 7,290 张 A（7,281 张推对，99.6% 在 Paisarn 888），改成先跳过凭记忆推出去、
        还没合格回读核过的推送，再在剩下能当证据的里取最近的（`automation.memory_window`，线上 `assess._recent` 与回放 `pushed_evidence` 同一个函数）；
        线上分批往前取（30、之后每批 200），凑够 `MEMORY_STREAK` 张证据就停，往回最多 `RECENT_SCAN_MAX = 1000` 张（最坏 6 次查询、1,000 行、2,000 段 JSON）。
        证据判定与连续计数不变。待甲骨文重跑 strict / estimated 验证（回放记录第五轮「改取法（10-17）」）。
      - 仍「记一次就 A」、本轮有意不推广「连续 3 张」的几类（登记为例外，回放先量）：记过的小票抵不抵进项（`item_src = choice`，回放
        `memory_sources.vat` 量 A 与 A 里错，数字出来再定）；POS 转账收款科目（POS 日汇总按导出写明的付款方式拆，回放没有 POS 语料，量不到
        前不改）；存货码（走库存的单入账方式多来自公司选的、整张最高 B，回放 answer 两遍量存货一项）；部门（用户 10-08 定记过的照用、不再问）。
      - 原因码：按日期选账套不符原来只有 `book`，拆成 `book_none`（一份都选不出）/ `book_many`（选出多份同期）/ `book`（选出另一份，
        含新年度账套已建），仍都是 C，只为从 `auto_reasons` 数出线上真实的两份同期量（`8465b480`；CC 照实说明原因码，词条不变）。
      - 用户 10-17 新定的两条，起初只在回放里量；用户 10-17 批准后都已接线上（见下两条）：①商品单可凭这家供应商在 Express 的历史升到 A
        （近 12 个月会计亲手录、不含 CC 推的、至少 3 张、全部走库存或全部无库存明细、与公司选的一致；相反交人 C；没历史仍 B）——纯函数
        `readback.history_rules.posting_settled` 与常量 `POSTING_KINDS` 已加、没有调用方（`VERSIONS` 不变），线上要 CC 桌面版
        `account_history` v3 读 STCRD 并排除 Pearnly 写入的单、服务端收 v3、`automation` 的入账方式判档按「一致 A / 相反 C / 没定下 B」
        且走库存时载荷真有库存行才 A；②两份同期账套按公司按年度问一次哪份是现用的、记住复用、组合变了再问——回放 `--tiebreak ask_once`
        模拟（线上原先 T1 与自动推遇到两份同期一律停下）。
      - **①接线上（用户 10-17 批准）**：CC 桌面版 1.9.0 `account_history` 结果版本 3，每家供应商另交 `posting`（同一批单里走库存 / 记费用
        各几张：STCRD 按 DOCNUM 挂单、PEOPLE 有值须是这家，有一行库存品 STKTYP=0 或有库存影响记走库存；STMAS / STCRD 在科目那几张表读完后
        另外复制、另外读，用整件请求租约剩下的时间，表缺、被占、复制失败、读坏、有坏行、时间不够都只是整份不交 `posting`、科目照交，
        不猜；STCRD 要从头扫到尾，大账套看 `diagnostics.posting_copy_seconds / stcrd_seconds`）。服务端 `history_rules` 收 v1–v3（`posting` 只收 v3、
        只有入账方式没有常用科目的供应商也留下），`supplier_posting` 合并各分店，`history.for_push` 多回 `posting`（`posting_settled` 加版本
        与读的日子，主档页删过的供应商不按历史用）；推送服务 `_posting_history` 只在 `posting_from = company_choice` 的进项单经同一钩子只读
        存着的那份（不请电脑），`assess` 放进 `Facts.posting_kind / posting_history`；`automation.posting_by_history` 判：一致且载荷按它写 A、
        相反 C（`conflict:posting`，不另记 `item:posting`）、其余 B（`no_lines` / `items` / `named` / 没定下），对方没有 13 位税号最高 B；值仍取
        公司选的。回放 `posting_tier_if_history` 改调同一个函数（回放主判定不带这一层，上下界照旧分两列）。
        **发版顺序**：先部署服务端（收 v3；旧桌面版交 v2 时没有 `posting`，入账方式照旧 B），再发 CC 桌面版 1.9.0。反过来时线上服务端
        （main 只收 v1）把 v3 当不合规记失败：存着的旧汇总照用、逐渐过期（31 天），电脑每小时重读一次整份（含 STCRD）。`companion_latest`
        来自服务器上的发布文件、与服务端代码版本无关，所以这个顺序只靠人遵守，没有技术闸（见下「第五轮后审查」第 10 条）。
      - **②接线上（用户 10-17 批准）**：真有两份同期账套时（如 SINCERE 68SINCER / 69SINCER 的 ISPRD 本年都是 2025）按公司问一次哪份是
        现用的。存储复用工作空间 `document_policy`（同 `goods_posting`，不新建 DocType）：键 `current_book|<公司身份>|<分店>|<cy / ny>|<候选
        规范账套键排序后的 sha1 前 12 位>`，值是选中的规范账套键（`contracts.document_policy.current_book_key / current_book`，`_known`
        认这一前缀，键长上限同 `GOODS_POSTING_KEY_MAX`）。候选组合与本年 / 次年段都在键里：新建账套、组合变了、跨到次年段就是另一个键，
        再问；不自动挑最近有录入的那份。读：`companies.current_book.remembered` 接在 `tasks.books_of`（T1）与 `automation.day_books`
        （自动推判档，拆出 `day_scope`）两处，选出多份时这一组选过、且选的仍在候选里才只留那份，否则照旧停（T1 `account_set_ambiguous`，
        自动推 `book_many` → C）；`identity.covering`（月结台账、主档页、档位统计按月取单）不接——它列单、不选推往哪份，两份都取才不漏
        人推进另一份的单。写：只经 `history.document_policy.record`，由 `assistant.pending_api.remember_current_book` 调（companies 不导入
        history、push 不导入 companies）：`companies.pending.current_book_choice` 按这张单停下时同一判据现算候选（T1 停下的按任务记的公司
        与分店；自动推停下的同判档时 `automation.day_scope`，按推送去向账套在目录里那一行认公司与分店），选的在候选里、候选多于一份才记；
        候选只取本人能用的连接，接口要录入权限、能读这张单。入口（无确认步骤，不加说明文案，没有新词条：原因沿用「这家公司有多份账套
        覆盖单据日期」，按钮就是账套名）：①复核页（Desk）停在 `account_set_ambiguous`、或自动推判出 `book_many` 的 C 类没人推过的单，原因旁
        列候选账套按钮，点了即记住并照常重推这张（`pending_api.choose_account_set`，同待处理页）；②待处理 / 月结台账：自动推判出 `book_many`
        的 C 类同 T1 停下的单一样给 `action = account_set`（原因同一句），沿用现有当场换账套组件，选一份即记住；③CC 工具
        `cowork_choose_document_policy` 多值 `current_book`（带 `account_set_key`），单据详情 `handling_choice_needed = current_book` 与
        `current_book_options`。复核页要读票上的 `auto_class / auto_reasons`：`history.storage.project` 多这两列。
        **有意变化**：T1 两份同期原先一律停下（`test_cowork_folder_follow_year` 两条用例锁住），改为「没选过停下、选过照选」；自动推同一组
        选过之后不再记 `book_many`。已经停下的单不会自动重推，要人在待处理 / 复核页点一次。多电脑记录 T1 一节第 6 条同步登记。
      - **②、①第五轮后审查（10-17，审查意见 18 条逐条处理）**：
        1. 跨分店（成立，已修）：`current_book.key_of` 候选报了两个以上不同的分店号时回空串，不问不记、照旧停下（定时任务没记分店、
           两个分店各有现用账套时，不然没印分店的收据会推进另一个分店的账套）；这一张照样能在待处理 / 复核页换账套，只是不记。
           CC 详情的 `current_book_options` 改由 `pending.current_book_options` 给，键算不出（跨分店、只有一份、认不出公司、键超长）
           不提 `current_book`（第 5 条一并修）。同一个分店时定时任务（按任务记的分店）与自动推（按推送去向那一行的分店）候选一样、
           键一样（第 11 条：已加测试，`identity.for_branch` 筛过与没筛同键）。
        2. 先记后推（成立，已修，第 4 / 12 / 16 条）：`choose_account_set` 先按停下时的候选算好要记的，**推成了**（进了账本、或停在要人
           选的项）才 `document_policy.record`；推不成不记，同一组下一张照旧停下再问。回包多 `current_book`（记住了的那份）。
        3. CC 工具 `current_book` 改走同一条（成立，已修）：停在「待选账套」的单直接调 `pending_api.choose_account_set`（推往选的那份、
           抄去向、按读它文件夹的电脑写、推成了才记），回 `{status: saved|not_saved, push_status, current_book?, reason?}`，CC 不用再推；
           工具说明同步改。改选入口：已经记住、不再停下的单，`current_book` 只改记（`pending_api.rechoose_current_book` →
           `pending.current_book_rechoice`，按推送去向那一行算候选，同自动推判档），不推。审批卡片问句用 `ask_when`（带
           `account_set_key` 时）新写一句官方短问句「Set as the account set in use? / 设为现用账套？」（四语补齐，文案闸门没有新增
           拦截与标黄；已有的「Save this choice for the company?」本身被闸门标 R-TALK，没复用）；预览对改记的单按连接的账套目录取名。
           步骤名「Choosing how a kind of document is handled」框架不能按取值换，**已知措辞偏差**，留给用户定。
        4. `book_many` 被截断（成立，已修）：`automation.reason_codes` 把 `REASONS_FIRST = ("book_many",)` 排最前，140 字截不掉；
           `pending.book_many` 文档改为「有别的 C 原因也算」（选好账套是人推，照常重判）。
        5. 复核页按钮（成立，已修）：候选账套按钮改 `btn-sm`，请求回来前整行禁用（服务端照旧按 stale 兜底）；同一行已有的「走库存 /
           记费用」等按钮仍是 `btn-xs`，不在本段改。
        6. 停下原因复用 R-TALK 的「这家公司有多份账套覆盖单据日期。」（成立，不改）：改已有文案要先给用户看，列进待用户确认的文案
           清单——这句现在还出现在复核页与待处理 / 月结台账 `book_many` 的单上。
        7. 测试缺口（成立，已补）：`preview_choose_document_policy` 的 current_book（名称 / 认不出原样写键）、CC 工具三条路、
           Desk `book_many` 判断与只在不是「仅识别」时出按钮、按钮尺寸与禁用、`book_many` 排最前、跨分店不记、改记。
        8. CC 桌面版 posting 真可选（成立，已修，审查 blocker 与两条 major）：见上①；补测试「截短的 STCRD 仍交 v3、没有 posting、
           科目照旧」「坏行整份不交」「STCRD 被占科目照交」。时限从固定 300 秒改为整件租约（900 秒）减去留给交回的 120 秒，复制也
           算在内（一块一块复制、过点即停）。没加「文件太大直接跳过」：没有实测数据定阈值，按时间截止已能兜住。**大账套（NEWLINK 等）
           的 copy / stcrd / read 秒数仍要测试机实测后再发版。**
        9. STCRD 坏行（成立，已修）：有坏行整份不交，`diagnostics.posting = stcrd_bad_rows`；STMAS 有坏行同样不交（`stmas_bad_rows`）。
        10. 发版顺序与文档（成立，已修文档）：README 与上面改为「线上服务端（main）只收 v1」「旧汇总照用、逐渐过期，电脑每小时重读」。
            可选的技术闸（`read_params` 多发服务端收的版本、桌面版不认 3 时退回 v2）没做：要同时改契约参数集合，且服务端照样要先部署，
            收益只在人没守顺序时；记为待定。
        11. 整条分支要迁移（成立，补说明）：本段没有新字段，但 `auto_class / auto_reasons` 等字段 origin/main 的 `Cowork OCR History`
            里没有，整条分支上线要跑 `bench migrate`。
        12. 按名字对上、历史相反（定口径）：保留 C（保守：同名的别家最多多交一张给人；一致时才封顶 B），已加测试。
        13. 入账方式历史在主档页删不掉（部分成立）：供应商有科目行时删那一行（`company_forget`）同时让入账方式也不按历史用；只有入账
            方式、没有科目「已定」的供应商主档页没有行可删。没补：主档页「记住的选择」现在不列 `document_policy` 一类，加一行是扩展
            界面，等用户定（用户 10-17「加上」，已由第 14 条补上）。回放 `posting_tier_if_history` 已补 13 位税号降档（判据成立但按名字对上记 `keyed`，同线上 `automation.tiers`）；
            **回放没有重跑**（要在测试机跑，`--tiebreak ask_once` 等参数照旧），`extra_A_wrong` 等数要等测试窗口。
        14. 主档页列入账方式与现用账套（用户 10-17「加上」，接第 13 条）：`companies.choices.policy_rows` 从主档所在工作空间
            （`Cowork Company.workspace`，同定时任务）的 `document_policy` 取这家的 `goods_posting|…` 与 `current_book|…`，来源 `policy`，
            类别 `posting` / `current_book`；主档页与会话栏同一张卡，小节「入账方式」「现用账套」。
            - 入账方式一行「商品单 [走库存 / 记费用]」当场改、可删。按这家各身份取（`companies.identity.identities`：主档身份，再加按
              名称建档时建档连接上的 `name:express:…` 身份 —— 旧年度没报税号的账套推送时按推往那一行现算身份，记在名称身份下），合成
              一行；改写主档身份与已记过的名称身份，删一起删（删了下一张会因此交人的单再问）。
            - 现用账套一行「本年 / 次年 · 总公司 / 分店 X → 账套名」，第二行写选中那份的年度；只删，改选沿用待处理 / CC 工具。只列还起
              作用的组合（`companies.current_book.live_keys`：按这家现在的账套全集，各连接、各分店、各日期段算出会问的键）：新建 / 删了
              账套、组合已不成立的旧键不会再被问到，不列也不清；同一分店本年在两个年度各有一组时两组都还起作用（旧年度的单仍按旧组合选），
              各列一行、年度不同。账套名与还起作用的组合按全集算（主档页的账套截到 20 份不影响）。
            - Express 历史定下的「供应商 → 商品单入账方式」（`readback.history.company_choices`，v3 起）单列小节「入账方式历史」，一行
              「供应商 → 走库存 / 记费用 · 张数 · 最后一张日期」，只删、不能提升成公司规则。与「供应商 → 科目」各删各的：删了记
              `posting|<供应商键>` / `account|<供应商键>`（只有供应商键的旧记录两样都不用）。**改了第 13 条的口径**：原先删科目行同时
              停用历史入账方式（那时入账方式没有自己的行），现在科目行只停科目；从历史提升成公司规则同样只停科目。
            - 权限：改入账方式同改公司规则（录入权限 + 覆盖这个月那份账套所在连接的授权，`choices.target_set`；没有覆盖这个月的账套
              拒绝），删同删公司规则（录入权限 + 这家任一条 Express 连接的授权）。入账方式与公司规则一样是全公司、跨连接生效的，按
              同一口径（有意的取舍：只在一条连接上有授权的人能改对另一条连接也生效的设置，公司规则本来就这样）。复核页 `choose`
              另按那张票推往的连接核。
            - 留痕：口径里不存谁 / 何时，卡上这两类不写；`history.document_policy.record / forget` 每次写、删记一行日志
              （`pearnly.cowork.document_policy`：工作空间、键、旧值 → 新值、操作人），所有写入口都经这里。
            - 删主档不清这两类（同「账套记忆、任务、草稿都不动」）：推送照样按它们执行，重建主档后卡上会再显示。有意保留，不另做询问。
            - 写经推送域 `push.application.goods_posting.set_company / forget_company`（companies 不导入 history，按身份列表核归属），
              删用 `history.document_policy.forget`（真删）。
            - 新词条（服务端四语）：`cowork.company.choice.goods / stock / service / current_year / next_year`、`cowork.company.head_office`
              （Head office / 总公司 / สำนักงานใหญ่ / 本店，同前端 `co.headOffice`）；前端 en / zh / th：`co.postStock / postService`、
              `co.kind.posting / current_book / posting_history`（入账方式历史 / Posting history / ประวัติวิธีลงบัญชี）。泰文值用
              「ลงสินค้าคงเหลือ / ลงค่าใช้จ่าย」（同 `vatExpense` 的「ลง…」写法，也与存货小节标题区分）。中文「记费用」同推送提问
              （`cowork.policy.service`）；已有的 `co.vatExpense`「计费用」用字不同，**是已有文案，待用户定**：改成「记费用」（旧「计费用」
              → 新「记费用」），没改。
            - 合并外壳分支（`pearnly/cc-shell-rebuild`）时：行样式以外壳为准（展开才出按钮，按钮 `cc-card-btn`），合并后核对 `policy`
              来源仍出两段按钮、`current_book` 没有分段，并在展开的行上补断言；本分支前端测试只断言数据与分组。`messages.tsv` 新词条
              放在 `cowork.policy.service` 后，不挨着外壳改过的 `choice_invalid`。合并前本分支的行样式在手机上点按区偏小（下拉 32px、
              删除用会话栏小按钮），不在本分支单独做手机验收。
            - CC 工具 `company_forget` 仍只删公司规则，不删这三类。

19. **挪到后面 / 不做 / 已知缺口**：
    - 第 4 期：从纠正中学、「要不要记住」、`rule_conflict` 提示。L0 期间人当场选的照旧记进账套记忆，这就是本期的「学习」。
    - 第 5 期：本月缺单。第 6 期：签字、锁月、对账与报税接进月结、L2 签字；`cc_judged` 随签字了结。
    - 法定不得抵扣的进项税（招待费、乘用车，ม.82/5）现在没有判定：有正确口径，单列后续项写死；本期回放把抵进项单独计分。
    - MR.ERP 定时任务不进档位、不过第 2 条核对，比 Express 的 L0 更松；等 MR.ERP 有回读后再纳入。
    - 不做：MR.ERP 档位、自动发起月度回读（第 54 条，由人或 CC 发起）、改档位的 CC 工具、手动升降档、按租户开关。

## 要用户定

**用户 2026-10-16 已定（前三项）**：1 升档阈值用**建议 A**（一个完整期、合格回读覆盖的 A 类 ≥20 张、被改 ≤1%）；2 定时任务按建议：任务里设好的规则算人定的规则，新客户第一个月照推 A 类、CC 推断的 B 类留给人；3 自动推前的重复分流按建议：同账套已成功推过同对方、同单号、同金额的，自动推不推、留给人，人推照旧不查重不拦。另：答案缺科目、对方编号时，用户批准从局域网 S 盘真账**只读导出**（原话「可以只读导出」），不写 S 盘任何文件。第 4 项（试点补识别）看完回放覆盖表再问。下文保留原选项与理由备查。


**1. 升档阈值**（三套共同前提：只认合格回读覆盖的单；没合格回读的期间不能升；自动推的单一张核不上立刻降到 L0；L2 推 B 类另须回放 B 类达标）

| | **建议 A** | B 稳（计划原建议） | C 快 |
|---|---|---|---|
| L0 → L1 | 最近 **1 个完整期**：合格回读覆盖的 A 类提议 ≥20 张，被改 ≤1%（向下取整，不到 100 张即 0 张） | 连续 **2 个完整期**，每期同 A | 1 个完整期：≥10 张，被改 ≤2% |
| L1 → L2 | 在 L1 满 **2 个完整期**：自动推被改 ≤0.5%；B 类提议每期 ≥20 张、被改 ≤1% | 在 L1 满 3 个完整期，同 A | 在 L1 满 1 个完整期，自动推被改 ≤1% |
| 降一档 | 一期内自动推被改 ≥2 张且 >1% | 同 A | ≥3 张且 >2% |
| 新客户最早 | 第 2 期起网页 / LINE 的 A 类自动推 | 第 3 期起 | 第 2 期起 |
| 后果 | 回放已证明 A 类整体 ≥99.5%，每家门槛只防这家特有问题；20 张下限防几张单就升 | 新客户多手推一个月 | 样本小，误升机会大 |

问用户：「升档阈值用建议 A（一个完整月、至少 20 张、没人改过就升到按规则自动推）吗？回放会先把 A / B / C 三套在 62 家上的结果并排给你看。」

**2. 定时任务怎么算**：建议任务里设好的「这一类都这样处理」算人定的规则，A 类从第一个月起照推（只多加第 2 条核对），CC 推断的 B 类改成提议、人推。
备选：定时任务也完全服从档位，新公司第一个月全部改成人推。后果：建议做法保住 10-08 已批的自动录入，只是 CC 推断的那部分要人点一次；备选更保守，但新客户的定时任务第一个月等于没用。
问用户：「定时任务里你设好的规则，新客户第一个月照样自动推、只把 CC 自己猜的那部分留给人点，可以吗？」

**3. 自动推前的重复分流**：建议同一份账套里已经成功推过同对方、同单号、同金额的单，自动推时不推、留给人（人推照旧不查重、照写）。
备选：不分流，照「推送不查重不拦」也自动推。后果：建议做法只多一次查本库推送记录，零成本；备选在同一张票被两人上传、文件夹里复制了一份时，会没人看见地写两张一样的单。
问用户：「同一张票被传了两次时，自动推只推第一张、第二张留给人点（人自己推照旧不拦），可以吗？」

**4.（视回放覆盖表再问）试点补识别**：补回放库后仍没有一家能凑够 2 个会计期、每期 ≥20 张有结果的单时，建议批一家 × 2 个月真识别（约 60–100 页，每页约 ฿0.20，脚本硬上限 ฿50）。
备选：不补，试点只验流程、不验升档。10-02 起识别预算暂停是否已解除要一并问。
问用户：「试点要不要花不超过 50 泰铢补识别一家两个月的票？」

## 审查处理

两路审查（下称 R1 安全、R2 可行性与测量）的条目逐条核过代码：

| 条目 | 处理 |
|---|---|
| R1-1 / R2-2 识别把握字段读错 | 采纳。核实 `confidence` 是字段齐全度、直读最高 0.95。闸改为 `_needs_manual_review` + `review_reasons` + 原文核对；图片单先归 C 由回放定（第 2 条），分档只报不闸。不采纳 R2 的「闸用 band == auto」：直读单永远到不了 auto。 |
| R1-2 / R2-5 推后核对兜不住判错、回滚说法错 | 采纳。订正回滚与 `since` 说法；改为 CC 桌面版对载荷就地比、回 `verify`，加实际挂到的对方税号 / 分店，服务端拿票面比；写明只防写入走样与挂错对方（第 6 条）。`duplicate_skipped` 由 journal 带回。 |
| R1-3 重复上传被推两次 | 采纳为自动推专用分流，交用户确认（要用户定 3）。草稿「`review_reasons` 有 duplicate 归 C」与「不拦」的矛盾一并消除。 |
| R1-4 读错日期金额但自洽 | 采纳：原文对得上、日月对调、早于 60 天（第 2 条）。 |
| R1-5 跨年写错账套 | 采纳：只认 `period_start`～`period_end`，且须是 T1 按日期选出的那份。 |
| R1-6 分店 / 无税号 / 主档没报 / 名字作键 | 采纳（第 1、2 条）。 |
| R1-7 规则过时或各层矛盾 | 采纳：各层说法不一归 C。 |
| R1-8 CC 推的单洗成「已定」 | 采纳。核实 Pearnly 写的行 `USERID = PEARNLY`；`account_history` v2 排除，v1 的「已定」降为 B。 |
| R1-9 抵进项推断来源 | 采纳：`judge_direction` 归 B；法定不得抵扣列后续项。 |
| R1-10 作废、退货、一拆多 | 采纳。 |
| R1-11 排队期间情况变了 | 采纳：领单时比 `automation_hash`，变了 `cancel_prepared` 记提议。 |
| R1-12 旧版 CC 桌面版无人核 | 采纳：自动推只派给新版。 |
| R1-13 / R2-9「没被改」不等于对、全部推送稀释信号 | 采纳：证据只认合格回读覆盖的单；因此保留「全部推送」。L1→L2 另加回放 B 类全局前提。不采纳「每月抽一批人点开」：加人工、无数据支撑。 |
| R1-14 没回读永不降档 | 采纳：最近合格回读早于本期两个月以上就只出提议。 |
| R1-15 重复分流口径 | 交用户（要用户定 3）。 |
| R1-16「改为只提议」近似紧急关开关 | 采纳去掉：用户 10-07「不做紧急关开关」已有口径，降档全由数据（核不上即降、回读被改即降）完成，不再问。 |
| R1-17 / R2-11 定时任务改为全部只提议 | 采纳取法 (b)，因改动已批行为交用户确认（要用户定 2）。 |
| R1-18 MR.ERP 比 L0 更松 | 采纳：列为已知缺口（第 19 条）。 |
| R1-19 一键推送重判与提议不同 | 采纳：规则 / 记忆变化导致的不同记「过时」，不进样本。 |
| R2-1 覆盖不到按家整月 | 采纳：拆判定层（答案造字段、全覆盖）与识别闸层（真结果），合并估计（第 15 条）。 |
| R2-3 样本量与计分口径 | 采纳：反事实计分、复用 eval 归一、待裁决、B 单独过线、配不上分两类。 |
| R2-4 答案无科目 | 采纳：第 0 步盘点，缺的从 S 盘只读导出；防泄漏；T 主档偏乐观写明。 |
| R2-6 数据放哪 | 采纳：答案齐就在生产机一次性容器跑，不搬；不齐才请开发窗口确认例外。 |
| R2-7 试点在合入之后、误写真账 | 采纳：试点挪到测试机实时源码、合入之前；固定 T 账套；测试站点路径守卫（按站点身份的安全边界，不是功能开关）；定时任务手动触发。 |
| R2-8 完整月按会计期间 | 采纳：合格回读须在 ภ.พ.30 截止日之后发起。 |
| R2-10 金额闸太弱 | 采纳：正式税票未税、税额须读出，容差沿用 `ocr/validation/math`。 |
| R2-12 工作量估少 | 采纳，见下。 |
| R2-13 小额补识别 | 视覆盖表再问（要用户定 4），硬上限 ฿50。 |

## 工作量（机器耗时）

- 写代码：主功能约 13 小时（判定抽取与分类 4、档位与评估 3、扫描与定时任务接入 2、推后核对与 CC 桌面版 2、界面与 CC 2）；回放工具约 7 小时。合计约 20 小时。
- 机器：补回放库约 10 分钟；判定层 + 识别闸层每轮几分钟；S 盘只读导出视缺口约 1–2 小时（夜里）；写入回归在 WSL ext4 约 0.5–1.5 小时；全闸门每次约 15 分钟。
- 等人：四项拍板、生产机答案齐不齐的核实结果、测试机真机点验、试点里 L0 期人在待处理推单。

## 进度

- **阶段 A（10-16）**：第 11 条第 1 项机械搬移 `32a878e4`（`push/express/decide.py`，推送服务只留取数，行为不变）；第 1、2 条 `06d94da6`
  （`push/express/automation.py`：`classify` / `checks` / `tiers` / `layers` / `conflicts` / `reason_codes`，纯函数不连库）；第 16 条对应测试
  `tests/test_cowork_da_automation.py`（36 条，CI 加一步）；check-i18n 登记泰文字样 `2335217e`；全闸门 92 步全过。与设计的出入（按设计意图的最小纠正）：
  - 日月对调「对调后也落在期间内」按同一组日期条件判两可（期间内、不晚于今天、不早于上传前 60 天），否则一年期账套里日 ≤12 的单全成 C；
  - 抵不抵进项只在有税额（`vat_amount` 或 `vat_capitalized` > 0）时算一项；存货由公司规则、任务默认定的也算 A（A 的定义「人或规则定」）；
  - 各层说法不一只看这张单真用了这几层定的类（`decided_by` 里有的），票面写明的现 / 赊不因下层不一归 C；
  - POS 导出、回导工作簿的票也不是文字层 PDF，照「文字层 PDF 才能进 A / B」现归 C（码 `image`），要不要放开待回放后定；
  - 文字层里找日期只认日 / 月 / 年（另认年-月-日、泰文 / 英文月名），月 / 日写法找不到 → C。
  留给后面阶段的接口：`automation.Facts` 由 `application.assess` 填——`text` 从原件 PDF 重抽文字层（库里不存，零费用）、`day_books` 按 T1 同一判据
  （`companies.identity.for_day`，经钩子）、`prior_pushes` 取这份账套成功推送（`pushed.project` 形状）、`layers` 用 `automation.layers`、
  `history_read` / `uploaded` / `source_total`；`decide` 透传历史已定的 `version`，`account_history` v2 接上之前 Express 历史一律 B；
  `reason_codes` 给 `auto_reasons`，`duplicate_number` 码给第 1 期异常清单。
- **阶段 B（10-16）**：第 9 条存储 `16ddcbe6`（`Cowork Company Automation` 一家一行、守卫同公司规则、删主档一起删；票上 `auto_*` 五列经
  `push_state.record_assessment` 写、不升版本；推送记录 `verify_state` / `verify_json`）；第 3、7、8 条 `38d219f6`（纯规则
  `push/automation/rules.py`、`companies/automation_rules.py`，`companies/automation.py` 的档位、钩子 `cowork_push_company_tier` /
  `cowork_push_day_books` / `cowork_push_verify_mismatch` / `cowork_readback_ready` / `cowork_company_month_changes`，阈值建议 A 常量表）；
  第 4、5、10 条 `3b24ff6b`（`application.assess`、定时任务 `_judge`、扫描 `push/automation/sweep.py` 挂 `*/5`、领单重核、自动推不弹框、
  测试站点路径守卫、写的电脑须能回核对）；第 16 条测试 `tests/test_cowork_da_tiers.py`（44 条，CI 加一步），既有测试只补替身；全闸门 93 步全过。
  与设计的出入（按设计意图的最小纠正）：
  - 领单重核的「账套记忆与公司规则指纹」只算这张单用得着的归类键（对方档按对方 + 分店，科目 / 付款 / 抵进项按对方，存货按对方开头，
    部门，POS 收款科目只在 POS 票上）：照字面整份账套任一选择变了都会撤掉排队中的全部自动推；
  - 领单时记录已确认（pending），`cancel_prepared` 只撤 prepared：改为 lease 里转 `cancelled`（原因 `push.automation_changed`，补四语词条），
    票清回「没推」并按载荷冻结提议；
  - L0 → L1 的「A 类 ≥20 张」把定时任务在 L0 自动推的 A 类一并算作 A 类样本（同受合格回读检验），否则以定时任务为主的公司凑不够提议数；
  - 合格回读的截止日取 ภ.พ.30 电子申报截止（有延期时 23 号），否则纸质 15 号；
  - 「没有新回读就暂停」只管靠档位放行的推送，定时任务 A 类的基线推送不受影响（否则新客户第一个月照推 A 类不成立）；
  - 没建主档的公司按目录里同一身份的行现算「按日期该推往的账套」（T1 同一判据），否则旧定时任务的票全成 C；
  - 定时任务判成提议 / C 的票：任务结局记 `held`（`automation:proposal` / `automation:review`，任务结果归「待处理 · review」，简报文案随第 14 条），
    票上不记失败原因，待处理照第 11 条落 `not_pushed`；
  - 回读差异表截断（只存 200 行、存下的单少于摘要里的变化张数）时，被它覆盖的单全当被改；
  - 扫描里没权限、判的时候出错也记 `auto_checked_at`（只判一次），留给人；测试站点路径守卫拦下的改出提议（码 `test_path`）。
  留给后面阶段的接口：阶段 C 在 `ack` 里对带 `automation` 的记录核不上时调 `push.automation.service.mismatch`（已接 `companies.automation.on_mismatch`），
  写 `verify_state` / `verify_json`（评估已读 `verify_state`）；带核对的 CC 桌面版须 ≥ `rules.VERIFY_MIN_VERSION`（1.8.0），在它发版前所有自动推
  （含定时任务 A 类）都改出提议；`account_history` v2 的「已定」把读的日子一并给，`assess` 再填 `history_read`（现为 None，各层说法不一暂不比 Express 历史）。
  阶段 D：`pending.classify` 的 `cc_proposal` 按票上 `auto_class` / `auto_proposal` / `edit_count`，`ledger.month` 的 `automation` 用
  `companies.automation.view`，降档原因码 `changed` / `written_mismatch`；试点手动触发 `push.automation.sweep.run(now=…)` 与
  `companies.automation.on_readback(连接, 账套, 期间)`。
- **阶段 C（10-16）**：第 6 条与第 11 条 CC 桌面版一项 `7f0da01b`（CC 桌面版 1.8.0 `write_verify`：进项 / 销项写完、原有读回通过后，拿写路自己换完科目
  的那份载荷比本次写入行的对方码、各行科目、日期、单别、参考号、税记录税号与分店，另读实际挂到的对方档税号与分店，回执 `meta.verify`，
  只报不回滚，`task_journal` 随 meta 存下、重发照样带回；`pos_journal.verify` 另回转账凭证日期不符；`account_history` 结果版本 2 排除
  Pearnly 写的单；服务端 `push/express/written_check`：`sanitize_push_meta` 只收三个键、准备时冻结票面对方 `party_face`、`ack` 成功时写
  `verify_state` / `verify_json`、自动推核不上经钩子立刻降 L0；`history_rules` 收 v1 / v2，「已定」带版本与读的日子，`assess` 填
  `history_read`）；第 11 条页面要的服务端、第 13 条、第 14 条服务端 `482fd64e`（`pending.classify` 的 `cc_proposal`、三个异常码、
  `ledger.month` 的 `entry.auto` 与 `automation`、`push_proposals`；CC 公司资料带档位、单据详情带判定、推送结果带 `automation` 与
  `verify`、GUIDANCE；定时任务简报「待推送」；词条四语）；第 12 条与第 14 条页面 `767b3cf7`；第 16 条对应测试（新增
  `tests/test_cowork_da_verify.py` 14 条、CI 加一步，`test_cowork_companies` / `test_cowork_assistant_tools` / `test_cc_month_ledger` /
  `test_companion_contract` 各加一组，CC 桌面版 `companion/tests/test_write_verify.py` 13 条；既有断言只按有意变化改了期望值：码表加三个码、
  录账段多 `auto`、卡头下一行多了档位行后的文字下标、`account_history` 版本 1 → 2、v2 不再算认不出的版本（改用 3）、「已定」多带版本
  与读的日子、简报词条 31 → 32）；全闸门 94 步全过。与设计的出入（按设计意图的最小纠正）：
  - 第 11 条页面要用的服务端（`pending.classify` 的 `cc_proposal`、异常码、`ledger.month` 的档位与自动推计数、`push_proposals`）阶段 B
    记给了阶段 D，但第 12 条界面离不开它们，在本阶段一并做了；阶段 D 不再重复；
  - 「全部推送」放在 `assistant.pending_api.push_proposals`，不在 `companies.api`：推送走待处理同一条路要用 `tools._push_one`，
    companies 不能导入 assistant（assistant 依赖 companies，会成环）；页面一张张推也走 `pending_api.answer`（交空的选择）；
  - CC 桌面版比的是「写路自己换完科目、要写下去的那一份」（期末盘存制换购货、现购换现金、应付换供应商档科目之后），所以大部分项只防
    写入走样（列宽、编码、找错行）；真正能抓挂错的是税记录分店（对方档的分店 vs 载荷分店）与服务端拿票面比 `party`；
  - 不符项：CC 桌面版另报 `party_master`（写进单据的对方码在主档里找不到）与 `unreadable`（读不出，宁严勿松）；POS 收款科目不符原本就
    整单回滚，不另列；服务端另比出 `party_tax` / `party_branch`。票面有 13 位税号而对方档没登税号也算不符；分店两边都有才比；
  - `account_history` v2 除 APTRN `USERID` 外，GLJNL `CREBY` = `PEARNLY` 的凭证也不算（两处都是写路写的）；
  - 自动推冻结目标里另存这份账套的目录行（`automation.account_row`）：回执是电脑身份、可能已没有连接授权，核不上降档要按它认公司；
    `party_face` 对所有 Express 推送冻结，旧记录没有就只看 CC 桌面版自己比的；
  - `written_mismatch` 属核对一段，也计进核对段的状态；提议计在录账段的「待处理」（`waiting`）；
  - 提议行在「本月」卡与待处理页都是行尾直接一个「推送」（沿用第 2 期「推送」的词与 `answer` 那条路），不用先展开；没有确认步骤；
  - 「已推 N（自动 M）」沿用录账段现有的「已录 N/总数」写法，后面加「（自动 M）」；档位行的原因只在最近一次是降档（核不上 / 被改）时写；
  - `companies/automation.py` 的 `tax.periods` 改为评估时才导入（看档位、给 CC 的资料不加载报税）；
  - 单据详情给 CC 的 `automation` 带原因码（`auto_reasons` 拆成表），CC 照实说为什么没自动推。
  CC 桌面版测试：scratchpad 临时虚拟环境（Python 3.11，PySide6、httpx、dbf、pytest）跑 `companion/` 完整 pytest，904 过、41 跳过（Windows
  专属），跑完删除。留给后面的：第 15 条回放（`scripts/da-replay.py`）与试点没做；上线须先服务端、再发 CC 桌面版 1.8.0（版本号已在
  `version.py` 改好，未发布），1.8.0 发布前自动推全部只出提议；回放的判定层可直接用 `decide.decide`、`automation.classify` 与
  `written_check`（写入层回归核 `verify` 全为 ok）。
- **阶段 D（10-16）**：第 15 条两层回放与第 0 步盘点。第 2 条纠正 `b8fdbd0e`（生产电子 PDF 照图片直读、文字层快路关着，识别链不以 `text`
  开头，原判据会把所有单判成图片单；改按原件文字层每页平均 ≥200 字认文字层 PDF，同识别取文字层的门槛，`assess` 总抽文字层）；回放工具
  `a96745e6`（`scripts/da-replay.py` 的 `inventory` / `judge` / `gate` / `report`，只读、零费用、拒绝局域网路径；测试
  `tests/test_da_replay.py` 17 条、CI 加一步）。在测试机跑完一轮，结果见 [两层回放结果](2026-10-16-da-phase3-replay.md)：答案齐（158 份账套
  都带原表，科目 / 对方码 / RECTYP 都在），**没用 S 盘**；判定层 A 类 53.0%（下限 51.8%）、B 类 93.6%，识别闸层文字层 PDF 28 张过闸
  （字段 0 错、3 张非票据），合并约 47%，都没达标；全部单的 84%（C 类的 89%，原写「C 类 84%」分母错了）是永续库存账套商品单没声明入账类型；建议 A 下 3 家升 L1。
  （首轮数字，审查处理后的第二轮见下一条。）
  与设计的出入：答案就在测试机且齐，不搬数据、没核生产机副本；识别闸层用测试机金标一轮的按记录结果（lab 库没有金标批次、正被测试窗口用，
  没重跑 `ocr-replay-corpus`），分档没报；写入层未做；「62 家」按 39 家公司 / 158 份账套报。按类修的候选与模拟偏差见结果文件。
- **审查处理（阶段 D 之后，10-16）**：对阶段 A–D 的审查 15 条逐条核实，13 条成立已修、2 条按事实改报告；判定层修复 `b7a0a82d`、回放工具 `6bc25128`，
  按修后的代码在测试机重跑两层回放（结果见 [两层回放结果](2026-10-16-da-phase3-replay.md) 第二轮，`…\_回放\2026-10-16-d2\`）；全闸门 95 步全过；
  CC 桌面版 `companion/` 完整 pytest 904 过、41 跳过（scratchpad 临时虚拟环境，跑完删）。逐条：
  1. 自动推的重复分流只查已成功的记录（高）：成立。`assess._prior` 改取这份账套同单号、除作废以外的全部推送记录（prepared / pending / leased /
     manual / success），按冻结载荷投影（`automation.prior_entry`，不要 Express 单号）；同一轮扫描里先判的那张刚排进队列，后一张归 C、记
     `duplicate_number`。人推照旧不查。
  2. 定时任务 A 类无条件照推（高）：成立，与用户 10-16「新客户第一个月照推」不符。改为基线（`companies.automation_rules.baseline`，经
     `tier_for_push` 的 ``baseline`` 给推送侧）：L0 上从没升降过、没有合格回读的是新客户照推；之后最近一个完整期 A 类被改 ≤1%、没有核不上、
     回读不旧才推，否则出提议（原因码 `changed` / `written_mismatch` / `readback_stale`）；L1 / L2 同网页上传。自动推核不上在 L0 也从现在重新起算
     （`on_mismatch` 记一次 L0 → L0），之后的完整期证据达标前定时任务 A 类只出提议。取档位出错按 L0、基线 `tier_unknown`（出提议）。
     阶段 B「没有新回读就暂停不管定时任务 A 类」只对新客户保留。没建主档的公司没有证据可积累，仍按新客户（拿不准，见下）。
  3. 文字层按整张平均字数判（中）：成立。改为逐页：每一页的文字层都要 ≥200 字；内容流里有隐形文字（`3 Tr`，扫描件 / 手机扫描 App / 复合机
     「可搜索 PDF」叠的识别文字层）的页给空串、按图片算（纯函数 `history/pdf_text.py`，生产 `history.text_layer.pages` 与回放共用）；正式税票
     另要在文字层里找得到对方 13 位税号原文（`text_tax`）。页面上是否铺满整页图片的判法没做（隐形文字已覆盖常见扫描 PDF）。
  4. 只核对方、不核本方（中）：成立。`own_tax`：推往账套有 13 位税号时，票面印的本方税号必须正是它；正式税票没印也归 C；收据 / 简式税票
     没印本方的，本方这一项算 B（`judged:own`）。账套没有 13 位税号的不列。人推照旧按 `account_match`。
  5. 销项按票种判赊、默认收入科目算 A（中）：成立，按类改。票种口径只有判成现购 / 现销（收据即已付，设计原写的规则）算 A，判成赊
     （完整税票多为赊）是推断，进项、销项都算 B；连接上的默认收入科目同进项连接默认科目，算 B。抵进项按方向推断升 A 没做（另议，见回放候选 3）。
  6. 人改公司规则 / 任务默认纠正提议被记成过时（中）：成立。`chosen_kinds` 另认提议之后改过的公司规则（`for_push` 的规则带 ``at`` = 修改时间，
     按这份账套适用的那条）、载荷来源是任务默认 / 公司规则的项；这些都算被改。规则被删掉的变化认不出（没有行），仍记过时。
  7. 领单时不再核 CC 桌面版版本（低）：成立。`AdapterContext` 带来领那台的版本（`transport._claim` 从电脑行取 `companion_version`），带
     `automation` 的记录被低于 1.8.0（或没报版本）的电脑领到时作废、改记提议（原因码 `verify_version`）。自动推 `unverified` 进异常清单没做
     （领单已拦，正常不会出现）。
  8. 回放的 Express 历史泄漏（中）：成立。只用录入早于这张单的单（`entered_on`，CREDAT 或单据日 + 7）、不含自己；补了单测（前 2 张不够
     「已定」、前 6 张 52-01 本张 52-02 定成 52-01 计错）。
  9. 回放没细分 `account_from`（中）：成立。历史定的科目记 `express_history`；报告改正「历史已定没起作用」的说法（修后 21 张、错 1）。
  10. 报告头条被模拟假设主导（中）：成立。销项现 / 赊标为本层核不了，单列、不计入准确率（原值另给）；定时任务那一行写明没模拟任务默认值、
      撤回「不安全」结论；候选 2 改成要用真识别票种核实；合并估计按新口径重算。
  11. `gate` 没要 `--manifest`、`--out` 没拦局域网（低）：成立。`--manifest` 必填并记进 `gate.json`；所有写（`--out`、`_write_json`）都过
      `refuse_lan`；文件头补参数。
  12. 配不上金标的过闸票不进分母、跨页按 1 页算（低）：成立。并进分母按错算；按 `invoice.pages` 造同样页数、逐页给文字层。
  13. 「C 类 84%」分母错（低）：成立，改成「占全部单 84%（C 类的 89%）」，阶段 D 那一条同步改。
  14. 定时任务简报把 C 类也写「待推送」（低）：成立。任务结局 `automation:review` 记 ``checked``，简报沿用待处理的「还没推送」
      （`cowork.company.pending.not_pushed`），提议仍是「待推送」；没加新词条。
  15. `write_verify` 注释指错白名单位置（低）：成立，改指 `push/express/written_check.py` 的 `COMPANION_FIELDS`。
  第二轮回放（不计销项现 / 赊）：判定层 A 类 78.55%（下限 77.25%，4,005 张）、B 类 89.17%（下限 88.68%，16,008 张）；识别闸层文字层 PDF
  92.59%（下限 76.63%，27 张）；合并约 72.7%；A 1.02%、A+B 5.07%；建议 A 2 家升 L1（自动推 200、错 53、降档 1），B 稳 0 家，C 快 2 家
  （414、错 74、降档 4）；都没到 99.5%。既有断言只按有意变化改了期望值：A 类全项表多 `own`、销项按票种判赊与默认收入科目改 B、文字层
  `text_layer` 一段字只当一页、`tier` / `tier_for_push` 多 ``baseline``、`for_push` 的规则多 ``at``、电脑行替身多回版本、文字层样例补对方税号。
  拿不准的：没建主档的公司定时任务 A 类一直按新客户照推（没有地方积累证据）；隐形文字判据可能把少数用隐形文字的电子 PDF 也算成图片（只会多交人）。
- **第一轮回放后的决定施工（10-16）** `b2cd114a`（判定层改动都在纯函数里，推送服务与 `scripts/da-replay.py` 共用；全闸门 96 步全过）：
  1. 商品单入账方式按公司选一次：纯函数 `push/express/goods_posting.py`（`blocked` 与映射器「永续 / 混合库存、没声明 → 交人」同判据、`resolve`
     声明的优先 / 公司选过的次之、`company_key` 同 `companies.identity`）；存储复用转账凭条 / 预扣税凭证那份（工作空间 `document_policy`，键
     `goods_posting|<公司身份>`，值 `stock` / `service`）；推送准备、要选的项、自动推判定共用 `service._goods_posting`；写入口
     `push.application.api.choose_goods_posting`；复核页推送原因 `posting_needs_review` 旁「走库存 / 记费用」两个按钮，CC
     `cowork_choose_document_policy` 多两个值、单据详情 `handling_choice_needed = goods_posting`；公司选过的列 `posting` = A；词条 3 条四语。
  2. 次年段：规则一处 `targets.account_match.fiscal_part` / `for_day` / `newer_book_covers`；`identity.book` 多 `next_end`、`identity.for_day`
     先本年后次年段（T1 定时任务、自动推 `day_books` 同用）；`automation` 日期核对认到次年段、由「推往的须是按日期选出的那份」把关；期间硬闸
     另拦「次年段、新年度账套已建」。T1 有意变化记在 [多电脑记录](2026-10-15-multi-desktop-build.md) T1 一节第 5 条。
  3. 抵进项按方向推断改 A。4. 账套记忆定的科目：`automation.memory_settled`（最近连续 `MEMORY_STREAK` = 3 张同科目才 A），`Facts.account_recent`
     由 `assess._recent_accounts` 取这家供应商在这份账套里最近推成功的单（按 `workspace_seller_tax_index`）。
  回放工具同步：`judge_document` 用 `goods_posting.resolve`（模拟的人在第一张 `posting_needs_review` 上按答案替这家公司选一次，`CompanyChoice`）、
  `account_recent` 取同账套这家供应商录入更早的答案科目、`day_books` 用 `identity.for_day`；账套带 `next_end`（ISPRD `END12NY`，读不到按本年止
  加一年）；C 主因「次年段」改为「次年段且新年度账套已建」，另加「晚于次年段」。没做的：领单重核的指纹不含公司入账方式选择；回放的商品单
  不造明细行，选「走库存」后这些单按连接默认科目（B），存货对照这层回放量不到；销项的按客户记科目没有（推送只按供应商记科目）。
- **第三轮回放（10-16）**：用 `938cae7c` 的工具在测试机重跑判定层与识别闸层（闸层样本 = 金标一轮 + 生产机导出里能对上 Express 答案的电子 PDF 97 份），结果见 [两层回放结果](2026-10-16-da-phase3-replay.md)「第三轮」，`…\_回放\2026-10-16-d3\`。判定层 A 类 96.63%（下限 96.28%，11,707 张；只 Paisarn 888 一家单独过线），A 2.97%、A+B 20.89%；识别闸层工具口径 79.44%、按文字层复核 98.13%（107 张）；合并约 76.8%（复核口径约 94.8%），都没到 99.5%。A 类剩下的错主要是账套记忆定的现 / 赊与对方档（只认一次人选）；C 类露出「按日期选出两份同期账套」（只差这一条 62,503 张，含回放估算期间的偏差）。按类可修的方向列在结果文件，未施工。
  （审查订正：闸层「Express 答案作金标」那组靠识别结果配对，配对用到的项是自证、还有选择偏差，98.13% 与 94.8% 撤回、不算 381 张；见下一条与结果文件第三轮的订正。）

- **第三轮之后的审查处理（10-16）**：对第一轮回放后的决定施工（`b2cd114a`）与第三轮回放的审查 15 条逐条核实，代码 `dd32f1f0`、`a8d53ec1`、
  `09a8f150`，文档本条；全闸门 96 步全过。按修后的代码重跑两层回放（第四轮，`…\_回放\2026-10-16-d4\`，结果见
  [两层回放结果](2026-10-16-da-phase3-replay.md)「第四轮」）。逐条：
  1. 按公司记的入账方式作用面比商品单宽（高）：部分成立。核实：选「走库存」后服务费的明细行在 `choices.resolve` 里对不上 Express 存货就要人选（C），
     没读出明细行时载荷没有库存行、科目是连接默认（B），审查说的「整张 A」走不到；但存货一项原先「账套里有存货主档就 A」确实太松，改为每一行都要
     有记过 / 规则给的存货码或 Express 同名存货（设计原文）。第四轮数据（按公司多数选后各家仍有 0%～15% 的单与会计记法不同）表明公司选择不是
     规则，`posting` 改 B（`a8d53ec1`）。补测试：完整税票服务费 + 公司选走库存 → 不得 A。
  2. 回放预先声明「服务」（中）：成立。回放改同线上不预先声明，`posting` 计分（公司选的 vs 答案有没有存货明细）；模拟的人按这家多数单选
     （初跑按第一张选，几家第一张恰好是服务、十几万张商品单被记成费用，是模拟的人选错，初跑结果留 `…\d4-初跑\` 对照）。
     「交人从 332,343 降到 25」只说明回放里都选过了，不是线上效果，结果文件已写明。
  3. `blocked` 没算记过的小票抵进项（中）：(a) 成立（选了抵进项的小票在永续账套里映射器交人、`blocked` 说不交，按钮永远推不动）；(b) 不成立：
     `choices.resolve` 只对收据 / 简式税票给 vat 选择，完整税票不会带 `vat=expense`。修法：车道判据抽成 `field_rules.lane_verdict`，映射器与
     `blocked` 共用；推送准备、要选的项、自动推判定改经 `service._posting_decision`（判出记过的 vat 选择改了车道就按它再定入账类型、重判）。
     补测试：三种票 × 三种 vat 选择下 `blocked` 与映射器交不交人一致。
  4. 新账套没报期间时退回旧账套次年段（中）：成立。`account_match.for_day` 退到次年段前核「新年度账套还没建」：同组有期间没报全的、或本年起始
     晚于旧账套本年止的账套就选不出（T1 停下、自动推 C）。`newer_book_covers`（人推的期间硬闸）不改：文件头口径「读不出、账套没报的都不拦」。
  5. `identity.covering` 只认本年（中）：成立。新增 `account_match.for_month`，`covering` 按分店同规则退到次年段（月结台账、主档页、档位统计、
     回读核对一并修好）。
  6. 「连续 3 张」被 CC 自己推的单攒够（中）：成立。纯函数 `automation.memory_evidence`：推的时候科目由人或规则定的（chosen / 任务默认 / 公司
     规则 / Express 历史已定）照数，凭账套记忆推的要合格回读覆盖且没被改（新钩子 `cowork_push_readback_marks` → `companies.automation.readback_marks`，
     同 `documents` 的覆盖口径），回读被改的断开；只在这张单科目来自账套记忆时取。
  7. 抵进项按方向推断改 A 的依据循环（中）：成立。判抵扣的要文字层核得到完整税票标题（`rules.from_title`）与买方税号才 A，否则 B；闸层加
     抵不抵进项计分（独立金标按看原件定的票种、Express 组按会计记没记进项税）。第四轮闸层又露出判不抵的一侧（10 张里 9 张会计抵了，识别都给
     「其他」票种），判不抵的 A 另要识别票种与标题都是收据 / 简式税票（`09a8f150`）。样本 21 张，局限写进结果文件。
  8. 新词条带说明性半句、说「商品单据」（低）：成立。三条新词条收短：原因「本公司这类单据走库存还是记费用？」，按钮「走库存（同类单据均走库存）」
     「记费用（同类单据均记费用）」（沿用「录入（同类单据均录入）」的写法），四语同改。都是本分支新加、没上线的词条；改已有词条的口径照旧要先给用户看。
  9. Express 答案作金标那组不能算识别准确率（高）：成立。回放工具对 `gold_source = express_answer` 的金标只比配对没用到的项（其余记 `self`），
     这组单列、不进合并估计、不算 381 张；另给悲观下限（同形态配不上答案的 28 张全算错，55.6%）。结果文件第三轮的 98.13% / 94.8% 撤回。
  10. 复核脚本写死进项方向（中）：成立。`textcheck2.py` 按样本方向比，重出 `闸层-Express字段不符.json`：BakeLab 7 处是销项买方分店（答案 `00001`、
      识别为空、都没印），撤回「空与 00000 同义」的建议。
  11. 回放的「连续 3 张」用会计最终科目（中）：成立，与 6 同修。回放按账套、按供应商（13 位税号）记模拟推出去的科目，按同一 `memory_evidence`
      数（合格回读在这期截止日后一天）；A 类记忆科目错率由 0.57% 变 1.26%。
  12. 镜像写成「生产同一镜像」（低）：成立。订正为 10-14 版 `232bf6ea`（导出当时生产 `104eb028`）；第四轮闸层改用 `104eb028`。
  13. 生产导出来历没进记录、脚本只在 scratchpad（低）：成立。导出与第三轮分析脚本存到测试机 `…\2026-10-16-prod-export\脚本\` 与本机
      `~/pearnly-devtools/da-phase3-prod-export-2026-10-16/`、`~/pearnly-devtools/da-phase3-replay-d3/`；结果文件补「第三轮的生产导出来历」
      （两次 SSH、只读手段、清理、与「只连一次」不符）；「没碰生产」注明只适用第一、二轮。
  14. 99.39% 是乐观上限（低）：成立，结果文件注明，实际值等推广「连续 3 张」到现 / 赊与对方档后重跑。
  15. 导出范围过大（低）：成立。测试机 `原始\` 里 testcowork 只留用到的 2 张识别单 / 2 条记录（`testcowork-已裁剪.json`），计数订正为回放库 907 +
      testcowork 2；以后导出只挂 testcowork 站点目录与 `common_site_config`、SQL 按语料 sha256 取（写进来历一节）。
  第四轮：判定层 A 类 95.91%（下限 95.53%，11,511 张），只 Paisarn 888 单独过线；B 类 17.69%；A 2.92%、A+B 20.87%；识别闸层独立金标 27 张 92.59%
  （下限 76.63%）；合并约 88.8%（73.2% ～ 94.3%）；建议 A 6 家升 L1、自动推 8,873 张错 137 张；都没到 99.5%。
  既有断言只按有意变化改了期望值（`posting` A → B、抵进项按方向推断要原文、存货一项按行、「连续 3 张」按证据、回放的 `score` 多 `posting`）。

## 第一轮回放后的决定（2026-10-16）

用户定：
1. 永续 / 混合库存账套的商品单（占全部单 84%）：**按公司问一次记住**入账方式（走库存 / 记费用），之后这家照此推；同「转账凭条、预扣税凭证按租户选一次」的做法。
2. 新年度账套还没建时，下一年的单**记进旧账套的下一年期间**（认 `next_end`，照会计实际做法）；新年度账套一建好，就改推往新账套。T1「没有覆盖这一天的账套就停下」随之改为「先找覆盖这天的，没有就用期间里含下一年这一天的旧账套」。
3. 识别闸层样本：**从生产机只读导出**已有的评测识别结果（零费用；一次性客户端容器连库只读，不进数据库容器，只连一次）。

开发窗口按数据定（技术取舍）：
4. 抵不抵进项按方向推断：回放 13,878 张 0 错，从 B 改 A。
5. 按供应商记住的科目（账套记忆）：7.6% 与会计不同，改为该供应商在这份账套里最近连续至少 3 张同科目才算 A，否则 B。

改完用同一工具重跑判定层与识别闸层。
