# 第 3 期：自动化档位、推后核对、语料回放（数字会计员，2026-10-16 设计稿）

依据 [施工计划](2026-10-15-digital-accountant-plan.md)「自动化档位」与「验证」两节、可靠性原则 1–3；建在第 1 期月结台账 / 统一异常清单、
第 2 期客户规则（[第 2 期记录](2026-10-16-customer-rules-phase2.md)）与多电脑 T5「谁的活谁的电脑做」（[ADR-021](../adr/0021-cc-desktop-multiple-computers.md)
第 50–57 条）之上。分支 `pearnly/da-phase3`，基于 `pearnly/multi-desktop` `5c11dc5b`。本稿只读勘察后起草，**未施工**；只有末尾「要用户定」一项要用户拍板。

## 现状（勘察结论）

- 推送都由人发起：网页复核页「提交」（`public/desk/intake-upload.js` `submit`）、待处理当场选（`assistant/pending_api.answer`）、
  CC `cowork_push_documents`（`assistant/tools._push_one`）。网页上传与 LINE 只把去向抄到票上（`ocr/execution/workflow._copy_targets`），不推。
- 唯一无人值守的推送是定时任务（`folder_intake/push.push_one` → `_push_to`）：`choices_view` 没有要选的项就 `submit` 并确认，
  不看识别把握、不看每项是按什么定的。
- 每项按什么定的已经有记录：冻结目标 `decided_by`（`push/express/company_choices.finish`），载荷 `doctype_src` / `account_source`
  （`mapper.py:341–343`、`sales_mapper.py:333–335`），经 `pushed.PAYMENT_FROM` / `ACCOUNT_FROM` 交给 CC。
- 确定性硬闸已有：期间 / 分店 / 本方税号（`targets/account_match.conflict`）、单据防呆（`doc_sanity.check_document`：税号校验位、
  未来日期、贷项、押金…）、科目表白名单与映射（`preflight_express`）。识别质量 2026-09-27 起**不拦**人发起的推送
  （`preflight._grade` 的 `require_confidence=False`），前提是人先复核。
- 识别把握已存在票上：`Cowork OCR History.confidence`（`ocr/validation/gates.bucket_confidence`：auto / yellow_confirm / needs_review）、`review_reasons`。
- 写后：CC 桌面版 `dbf_writer._verify` / `dbf_sales._verify` 只核本次追加的行（`_table_sizes` / `since`）的含税额、借贷平、税务行在不在，
  不对整单回滚。科目、对方、单别、日期、参考号只在按月回读 `readback/diff.compare` 时才比（要人或 CC 发起）。

## 定稿条目

1. **每张单分三类**（按单判；纯函数新文件 `push/express/automation.py` 的 `classify`，不连库）：
   - **A 规则定**：每一项都由人或规则定，并且通过下面第 2 条的全部核对。
   - **B CC 判断**：通过第 2 条全部核对，但至少一项是 CC 按数据推断的。
   - **C 要人**：有要人选的项、任一条核对不过、或被硬闸拦下。

   各项来源的归类（来源词沿用 `pushed.PAYMENT_FROM` / `ACCOUNT_FROM` / `decided_by`）：

   | 项 | A | B | C |
   |---|---|---|---|
   | 对方档 | 账套记忆 / 公司规则；同税号同分店只有一个档 | 没有同税号档，按票面新建 | 同税号几个档、分店对不上（`needed`） |
   | 存货 | 账套记忆；Express 有同名存货（归一后全等） | — | 其余（`needed`） |
   | 费用 / 收入科目 | 任务默认、账套记忆、公司规则、Express 历史已定（同供应商近 12 个月 ≥3 次全记同一科目，`_settled_account`，用户 10-15 定）；销项用连接上人设的默认收入科目（`config_default`） | 分类映射（`category_map`，分类是识别读出的）；进项用连接默认科目；这家供应商历史里排第一的建议（`account_suggest`，至少有 1 次记录） | 只有名称 / 大类建议，或没有建议（`needed`） |
   | 现 / 赊 | 票面写明（`explicit`）、票种口径（`doc_type`：收据即已付）、账套记忆、任务默认、公司规则 | 供应商档案、银行流水佐证、固定默认（`profile` / `bank` / `config_default`） | （`needed`） |
   | 抵不抵进项、部门、POS 收款科目 | 账套记忆、任务默认、公司规则、同公司别年度借来的（`_borrow_receipts`）；部门不记或单据簿默认 | — | （`needed`） |

2. **自动推前的核对**（全部确定性，只作内部信号，不出提示文案；用户 10-07「录入口不加识别后提示」）：
   - 硬闸照旧：`account_match`、`doc_sanity`、`preflight_express` 各项为 ready。拦下的单就是 C 类，原因照现有记在票上。
   - 识别把握是 `auto`；`yellow_confirm`、`needs_review` 或带 `review_reasons` 的归 C。
   - 金额算得通：`common.amounts` 能拆出 (未税, 税, 含税)，且税额 = 未税 × 7% ±0.01；免税单税额为 0。
   - 正式税票的对方税号 13 位，校验位对。
   - 单据日期落在推往账套的会计期间里、不晚于今天，同 `account_match` 与 `doc_sanity`。
   - 重复单号**不拦**，照第 1 期只在异常清单列 `duplicate_number`（计划可靠性原则 1；用户「推送不查重不拦」）。
3. **档位按公司判，决定哪类自动推**：

   | 档位 | A 类 | B 类 | C 类 |
   |---|---|---|---|
   | L0 只提议（新公司起点） | 提议，人推 | 提议，人推 | 照现有停下 |
   | L1 按规则 | **自动推** | 提议，人推 | 照现有停下 |
   | L2 整月 | **自动推** | **自动推**，每张列一条 `cc_judged` | 照现有停下 |

   - L2 本期只做到「整月能做成草稿」：A、B 都自动推，异常清单列齐。签字、锁月、对账与报税接进月结是第 6 期。
   - 「提议」：票上记下 CC 准备好的做法，待处理和「本月」卡里一键推送。
   - 档位按公司主档算，不按租户、不按功能开关（用户「功能不按租户开关」）。所有公司起点都是 L0，只按第 7、8 条的数据升档。
4. **谁触发自动推**（CC 对话和待处理里人点的推送是人发起的，不看档位，照现在推）：
   - **定时任务**：接在 `folder_intake/push._push_to` 里 `choices_view` 之后、`application.submit` 之前。
     - 新函数 `application.assess` 判出类别，按这家公司的档位决定推还是记成提议。
     - 推送身份、读文件夹的电脑（`route_device`）、离线排队都照 ADR-021 第 51–53、57 条不变。
   - **网页上传 / LINE**：在现有每 5 分钟的 cron 里加 `push.automation.sweep`（不新增云上常驻）。
     - 只扫这样的票：有推送去向（Express）、没人动过（`edit_count = 0`、没有推送状态和原因）、识别完已满 `pending.SETTLE_MINUTES`（15 分钟，
       留给上传人自己复核）、近 3 天、还没判过（`auto_checked_at` 为空）。每轮最多 200 张。
     - 以上传人的身份推（同定时任务「以负责人身份」），写的电脑是他自己登录过 CC 的电脑（第 50 条）。
     - 他一台电脑都没有（`writer_state` = MISSING）就不推，记 `no_computer`，这张单在待处理里显示「未推送」。
   - **MR.ERP**：本期不进档位，定时任务照现在推（语料与回读都是 Express）。
5. **自动推的边界**：
   - 只推没人动过的单，只推一次；判过、记成提议的单，后来升档也不回头再推。
   - 有人改了这张单（`edit_count` 增加）就作废提议，交给人。
   - 自动推的单在冻结目标里加 `automation {class, tier, trigger: task|upload|line, reasons}`。
   - 下发给电脑时 `attended = false`（`companion/route_contracts.attended` 加一条：有 `automation` 的不算有人值守），
     不弹「账套被占用」询问框，等账套空出来再写（同第 55 条定时任务）。
6. **推后核对，只核本次写入的行**：
   - **CC 桌面版**：`_verify` 已经按 `since` 读出本次追加的行。把读到的行作为 `written_rows` 放进确认回执
     （表头的单号、日期、RECTYP、对方码、REFNUM、未税 / 税 / 含税、DOCSTAT；GLJNLIT 本单号各行的科目 / 借贷 / 金额；ISVAT 的 AMT01 / VAT01；
     POS 同次写的转账凭证行）。`common.sanitize_push_meta` 加白名单，每单最多 200 行。
   - **服务端**：`push/native/service.ack` 成功后经新钩子 `cowork_push_written`（readback 登记，push 不导入 readback）调 `readback/written.check`。
     预期用 `pushed.project`（同月度回读）；比较复用 `diff.compare` 的逐项比对和 `diff._written` 的科目换法（现金科目、购货科目），
     只比这一张，不出直接录入。
   - 结果写进推送记录的新字段 `verify_state`（ok / mismatch / unverified）与 `verify_json`（差在哪几项）。
     旧版 CC 桌面版不带 `written_rows` 时记 unverified，由月度回读覆盖。
   - 核不上的单进异常清单新码 `written_mismatch`（核对段，action，去处 open），照「推送不查重不拦」不回滚、不重推；
     是自动推的还立刻触发降档（第 8 条）。人推的单同样核、同样列，只是不影响档位。
7. **提议与「被改」怎么记**（升降档的数据）：
   - 判完类别就在票上冻结 `auto_proposal`：每一项的值、现 / 赊、科目、抵进项，以及日期、单号、对方税号、三项金额。
   - 统计时（纯函数 `automation.agreed`）拿它和这张单推送成功的载荷（`pushed.project` + `pushed.written`）比。
   - 算作**被改**的情况：
     - 人推前改过票面（`edit_count > 0`）或另选了某一项；
     - 推后核对 `mismatch`；
     - 月度回读里这张单 `changed` / `deleted` / `voided` / `missing`（`readback.diff`）。
   - 每月统计按公司算：A / B 类提议各多少张、被改多少张，自动推多少张、被改多少张、核不上多少张。
     单据范围同月结台账（`ledger._documents`），回读改动经新钩子 `cowork_company_month_changes` 从 readback 取。
8. **升降档评估**（阈值见末尾「要用户定」，写成一张常量表 `companies/automation.THRESHOLDS`，不按租户设）：
   - **升档**：上个月（或几个月）每份 Express 账套的回读做完时，经 readback 新钩子 `cowork_readback_ready` 评估这家公司，一次升一档。
     **没回读完的月份不能升**：Express 里人改过的只有回读看得到；第 1 期已有的 `readback_needed` 会提醒。
   - **降档**：
     - 自动推的单核不上，在 `ack` 之后马上降。
     - 回读发现自动推的单被改超过阈值，在回读完成时降。
     - 人在「本月」卡点「改为只提议」，降到 L0。
   - 降档后要等降档之后开始的完整月重新达标才能再升。
   - GET 里不写库：评估只在回执和回读处理这两条写路径里做，台账只读存下的档位。
9. **存储**（原生模型同步建表、加字段；**本期无数据补丁**，没有行的公司就是 L0）：
   - 新表 `Cowork Company Automation`：一家公司一行（company 唯一），字段 workspace、tier、since、reason（码）、evidence_json（评估用的月度统计）、
     set_by（`cc` / 用户）、history_json（最近 24 次升降）。守卫同 `Cowork Company Choice`：`permission_query` / `has_permission` 拒绝、
     控制器守卫、拒绝分享、进 `companies.access.DOCTYPES`；删主档时一起删。
   - `Cowork OCR History` 加字段 `auto_class`、`auto_tier`、`auto_reasons`（码，≤140 字）、`auto_proposal`（JSON）、`auto_checked_at`（建索引，给扫描用）。
     写入走 `history/push_state` 新函数 `record_assessment`，用 `set_value(update_modified=False)`，不升 `record_version`，免得和人的编辑撞版本。
   - `Cowork Push Record` 加 `verify_state`、`verify_json`（结果字段，身份字段照旧不可改）；`automation` 放在冻结目标 JSON 里，不加列。
10. **权限**：
    - 自动推：推的那一刻照现有判据重核推送人（任务负责人 / 上传人）的录入权限与连接授权（`application.prepare`），不过就不推、留给人。
    - 看档位：同月结台账读权限；看月度统计要录入权限。
    - 改为只提议：录入权限，并且有这家任一条 Express 连接的使用授权（同第 2 期删规则）。档位只能由数据升，人不能手动升。
    - 一键推送提议：走待处理现有路径（`pending_api` → `tools._push_one`，录入权限 + 连接授权 + 单据读取范围），算人发起。
11. **接入点**（一个提交一件事；第 1 个是机械搬移，单独提交）：
    - 把 `application._decisions` 的判定核心抽成纯函数 `push/express/decide.py`，推送服务和语料回放共用，免得两套判定走样。
    - `application.assess`：同 `choices_view` 只看不记（结束回滚），多回 `applied`、`decided_by`、载荷事实、预检原因、类别。
    - `application` 新钩子 `cowork_push_company_tier`（`companies.automation.tier_for_push`，按目录这一行认公司，同第 2 期 `for_push`）；
      钩子出错按 L0，宁可少自动。
    - 定时任务接入：`folder_intake/push._push_to`。
    - 扫描：`push/automation/sweep.py`，挂进 `hooks.scheduler_events` 的 `*/5` 列表。
    - `pending.classify` 加 `cc_proposal` 一组（判过、A / B 类、没人动、没推），`FIELDS` 加 `edit_count`、`auto_class`；
      C 类核对不过的照旧落 `not_pushed`。
    - `exceptions.CODES` 新增三个码：
      - `cc_proposal`：录账段，action，去处新增 `push`；
      - `cc_judged`：录账段，info，去处 open（L2 自动推的 B 类，只列不催，签字在第 6 期）；
      - `written_mismatch`：核对段，action，去处 open。
    - `ledger.month` 录账段多一个计数 `auto`；返回值多一项 `automation {tier, since, reason, can_lower}`。
    - 新接口 `companies.api.push_proposals(company, period)`：每次最多 20 张，同 `OUTCOMES_MAX`；`companies.api.lower_automation(company)`。
    - 回读：`readback/written.py`；`readback.service._process` 结束时调 `cowork_readback_ready`；提供 `cowork_company_month_changes`。
    - CC 桌面版：`dbf_writer._verify`、`dbf_sales._verify`、`pos_journal.verify` 回执带 `written_rows`，`tests/test_companion_contract.py` 锁形状。
12. **界面**（建在新外壳，照 [CC 外壳设计](2026-10-16-cc-shell-design.md)）：
    - 会话栏「本月」卡与公司页概览共用同一组件，卡头下加一行「自动推送：只提议 / 按规则 / 整月」。降过档时同一行跟一个短原因
      （核不上 / 被改 / 人改的），按码翻译。卡的 ··· 菜单加「改为只提议」，单击生效，没有确认步骤（用户口径「无确认步骤」）。
    - 录账段的计数写「已推 N（自动 M）」。
    - 异常清单里 `cc_proposal` 一行沿用第 2 期 ChooseRow 的「推送」（`co.pushNow`）；组标题旁加「全部推送」，调 `push_proposals`。
      `written_mismatch`、`cc_judged` 交给 CC，预填、不自动发送。
    - 手机端同一组件，照 ERP 手机端。不加说明性文案。
13. **CC**：
    - `cowork_company_profile` 加 `automation`：档位、自何时、原因、近两月统计。
    - 推送结果的 `written`（`push.application.service._entered`）加 `automation`（类别、档位、触发）和 `verify`（ok / mismatch / unverified）。
      CC 能说「这张按规则自动推、推后核对一致」。
    - GUIDANCE 加一句：档位只由数据升；问到为什么没自动推时，按 `auto_reasons` 照实说。
    - 不新增改档位的 CC 工具。
14. **新文案**（en / zh / th 同批，服务端码进 `messages.tsv`，前端进翻译入口）：
    - 「自动推送」及三个档名「只提议 / 按规则 / 整月」。档名算产品用词，测试窗口点验时给用户看。
    - 「改为只提议」「全部推送」。
    - 三个异常码的标签：「待推送」「CC 判断推送」「写入与预期不符」。
    - 降档原因三句。
15. **语料回放工具** `scripts/da-replay.py`：离线、零费用，只导入纯函数（`decide`、`automation`、`preflight` / `prepare`、`readback.diff`、
    `history_rules.settled`、CC 桌面版写入代码），不连站点、不调识别、不调模型。分三步：
    - `pack`（生产机一次性容器，只读挂载）：按语料 `清单.tsv` 的 sha256，从已有的识别结果里取出这批文件的字段，
      生成 `识别结果.jsonl`（只有字段、把握、复核原因，没有原件）。
      - 来源：测试租户回放库 `pearnly-ocr-replay`、`/tmp/pearnly-eval/<轮次>/results.jsonl`、测试机 `金标-2026-10` 的结果。
      - 放到测试机 `C:\PearnlyStress\语料\06-录入推送\_回放\`，不进仓库。
      - 先出覆盖表：每家每月有现成结果的张数。**没有结果的不补识别**，按「人录的」处理。要补跑须用户另批小样本，并在脚本里设计费硬上限。
    - `run`（测试机 WSL）：每家按月份顺序模拟。
      - 账套工作副本建在 `C:\PearnlyExpress\70EXP\_replay\<轮次>\`，从清空的 T 账套复制，原 T 账套不动。局域网 Accserver / S 盘不读不写，
        脚本拒绝 `\\` 和 `S:` 开头的路径。
      - 主档用 CC 桌面版主档读取代码从副本读。Express 历史用答案里前 12 个月的分录。
      - 档位起点 L0。模拟的人：L0 和 C 类的单按答案推，人的选择记进账套记忆，同当场选。自动推的单用 CC 桌面版写入代码写进副本，再做第 6 条核对。
      - 推完和答案配对（同 `eval-invoices.py`：单号 / 对方税号 / 金额 / 日期至少两项吻合且唯一）。比现 / 赊单别、对方码、日期、参考号、三项金额、
        科目（答案原表里有 GLJNLIT 的才比）、抵进项；答案里没有的项不计分，单列。
      - 每月底按「要用户定」的三套阈值各自评估升降档，并排报告。`--no-write` 只跑判定，用来快速改了再跑。
    - 报表：`报表.md` + `明细.tsv`，写在 `_回放\<轮次>\`。
      - 每家和合计：覆盖率、A / B / C 占比、自动推占比、自动推准确率（一张单所有计分项都对才算对）及 95% 下限、
        各项错误分布、异常码计数、核不上张数、三套阈值下的档位时间线。
      - 配不上答案的自动推一律按错计，另列张数。
      - **达标**：自动推（L1 的 A；有公司到 L2 时另算 B）准确率点估计 ≥99.5%，且 95% 下限 ≥99.0%。没达标按类修（不针对某家），再跑。
16. **测试**：
    - `automation.classify` 来源 × 方向矩阵与各核对。
    - 档位允许表；`tier_for_push` 出错按 L0。
    - 升降档：样本下限、没回读不升、核不上即降、人降后要重新满月。
    - `folder_intake/push`：L0 记提议、L1 推 A 不推 B、C 不变、MR.ERP 不变。
    - 扫描：等满 15 分钟、只扫没人动的、以上传人身份、没权限跳过、幂等、每轮上限、没有电脑记 `no_computer`。
    - `written.check`：现金 / 购货科目换法、只比本次行、同号旧单不算、旧版 unverified。
    - 待处理、台账、异常码与 `tests/fixtures/cc-month-contract.json`；CC 桌面版回执契约。
    - 前端：「本月」卡档位行、推送 / 全部推送、改为只提议、手机宽度。
    - 回放工具的配对与计分用合成样例做单测。
    - 闸门：`check-architecture`、`check-i18n --strict`，全闸门 `gate.sh`，改到 `pearnly_core` 时跑候选离线检查。
17. **上线顺序**：
    1. 本分支写完：全闸门、分支 CI。
    2. 回放达标（先 `--no-write`，再带写入），结果记进本文件。
    3. 测试机真机点验界面。
    4. 合入 main，按批次部署：先服务端，再发 CC 桌面版正式版（带 `written_rows`）。
    5. testcowork 选覆盖率最高的一家整月试点，用压缩时间（同「不等现实时间」）连跑 2–3 个月：L0 月由人在待处理推、回读、升档，再 L1 月自动推、回读。
       - 识别走回放：试点文件夹只放有现成结果的件，零费用。
       - 只写 C 盘 T 账套，推完从服务端核对实际写入路径。
    6. 真实客户：部署后自动生效，全部从 L0 起。最快要一个完整月加回读才可能升档，晚于试点，所以试点没过时，有时间修好再让任何真实公司升档。
18. **有意变化**：
    - 定时任务推 Express 不再「没有要选的就推」，改按公司档位：新公司（L0）全部变成提议，B / C 类以前会推、现在停下。现在没有真实客户。
    - 网页上传、LINE 的单在 L1 / L2 公司可能不点「提交」就被推（没人动、识别后满 15 分钟）。
    - 09-27「识别质量不拦推送」对人发起的推送不变；无人值守的推送把识别把握当内部闸，不出文案。
    - 自动推的单不弹「账套被占用」询问框；确认回执多 `written_rows`，推送记录多核对结果。
19. **挪到后面**：
    - 第 4 期：从纠正中学、「要不要记住」、`rule_conflict`。L0 期间人当场选的照旧记进账套记忆，这就是本期的「学习」。
    - 第 5 期：本月缺单。
    - 第 6 期：签字、锁月、对账与报税接进月结、L2 的签字；`cc_judged` 到时随签字了结。
    - 不做：MR.ERP 档位、自动发起月度回读（回读只由发起人自己的电脑读，第 54 条；由人或 CC 发起，`readback_needed` 提醒）、改档位的 CC 工具、按租户开关。

工作量（机器耗时）：写代码约 12 小时（含 CC 桌面版回执约 2 小时）；回放每轮判定约 10 分钟、带写入约 1 小时；
要等人的：阈值拍板、真机点验、试点里 L0 月的人工推送。

## 要用户定：升档阈值

**「被改」**：CC 的提议或自动推的单出现下面任一情况：
- 人推前改过票面或另选了某一项；
- 推后核对不一致；
- 月度回读发现这张单在 Express 里被改、删、作废或找不到。

**另有两条前提，三个选项都一样**：没回读完的月份不能升档；任何时候自动推的单出现一张核不上，立刻降到 L0。

| | **建议 A** | B 稳（计划原建议） | C 快 |
|---|---|---|---|
| L0 → L1 | 最近 **1 个完整月**：A 类提议 ≥20 张，被改 ≤1%（向下取整，不到 100 张即 0 张） | 连续 **2 个完整月**，每月同 A 的条件 | 1 个完整月：A 类 ≥10 张，被改 ≤2% |
| L1 → L2 | 在 L1 满 **2 个完整月**：自动推的被改 ≤0.5%；B 类提议每月 ≥20 张、被改 ≤1% | 在 L1 满 3 个完整月，条件同 A | 在 L1 满 1 个完整月，自动推的被改 ≤1% |
| 降一档 | 本月自动推的被改 ≥2 张且 >1% | 同 A | 本月被改 ≥3 张且 >2% |
| 新客户最早 | 第 2 个月起按规则自动推 | 第 3 个月起 | 第 2 个月起 |

**建议 A 的理由**：
- 第 15 条的回放在 62 家上证明「A 类整体 ≥99.5%」，每家的升档门槛只需防这家特有的问题，不必单靠一家的少量单据去证明 99.5%。
- 1 个月正好是计划写的「新客户第一个月只学习」。
- 每月至少 20 张，免得几张单就升档。不到 100 张时要求 0 张被改，因为这时改 1 张就是 1%–5%。
- L2 会自动推 CC 判断的单，所以多看一个月。降档要快。
- B 更稳，但新客户要多手推一个月。C 快，但样本小，误升的机会大。

**回放会把 A / B / C 三套在 62 家上的结果并排列出来**（每家几个月升到哪档、自动推多少、错多少），用户可以看完数据再选。
确定后写进 `companies/automation.THRESHOLDS`，所有租户同一套。
