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
   | 对方档 | 账套记忆 / 公司规则；票面 13 位税号 + 分店在主档里只对上一个档 | 没有同税号档，按票面新建 | 同税号几个档、分店对不上；**对方税号不是 13 位，或这份账套主档没报上来**（`choices.resolve` 不列对方、交 CC 桌面版按名字找） |
   | 存货 | 账套记忆；Express 有同名存货（归一后全等） | — | 其余 |
   | 费用 / 收入科目 | 任务默认、账套记忆、公司规则、Express 历史已定（第 12 条 v2 历史，近 12 个月 ≥3 张全记同一科目）；销项用人设的默认收入科目 | 分类映射、进项连接默认科目、这家历史排第一的建议；**v1 历史的「已定」** | 只有名称 / 大类建议，或没有 |
   | 现 / 赊 | 票面写明、票种口径（收据即已付）、账套记忆、任务默认、公司规则 | 供应商档案、银行流水佐证、固定默认 | 其余 |
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
  （字段 0 错、3 张非票据），合并约 47%，都没达标；C 类 84% 是永续库存账套商品单没声明入账类型；建议 A 下 3 家升 L1。
  与设计的出入：答案就在测试机且齐，不搬数据、没核生产机副本；识别闸层用测试机金标一轮的按记录结果（lab 库没有金标批次、正被测试窗口用，
  没重跑 `ocr-replay-corpus`），分档没报；写入层未做；「62 家」按 39 家公司 / 158 份账套报。按类修的候选与模拟偏差见结果文件。
