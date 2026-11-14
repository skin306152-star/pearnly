# CC 前端与交互欠账总表（2026-11-14，只读盘点）

- 任务：把用户两个月里定过的 CC 前端 / 交互必做项收成一张表，逐项核现状，对到 11-14「引导式为主、对话为辅」的新方向，给相关分支处置建议。只读，不改产品代码、不合入、不删分支。
- 分支 `pearnly/cc-frontend-audit-373405`，基线 origin/main = **93476cbec**。
- 上线判定：生产服务端 = **6f58f0b48**（`docs/work/2026-11-13-release-6f58f0b4.md`），是否上线用 `git merge-base --is-ancestor <提交> 6f58f0b48` 逐个核；CC 桌面版正式版 = 标签 `companion-v1.12.21`。
  - 6f58f0b48 → 93476cbec 之间 `frontend/assistant/`、`companion/` **零改动**，`apps/pearnly_core/pearnly_core/assistant/` 只差 `toolrun.py` 一处收款小修（`git diff --stat 6f58f0b48 HEAD -- …`）。所以 CC 前端与桌面版「main 上的状态 = 线上状态」，「已合入未上线」只有两条服务端收款项。
  - 「已上线」只说明代码在线上版本里，不等于真机或业务验收通过。
- 路径缩写：`F/` = `frontend/assistant/src/`，`K/` = `apps/pearnly_core/pearnly_core/`，`C/` = `apps/pearnly_cowork/pearnly_cowork/`，`E/` = `apps/pearnly_erp_portal/pearnly_erp_portal/`。
- 引导式各期取自本地分支 `pearnly/guided-3-0cc915`（b9260e2f6，**未推远端**）的 `docs/work/2026-11-14-cc-guided-coverage-plan.md` §3（只引用）：
  **合入后即做**（第 1 期欠项）→ **P1** ERP 收款 → **P2** ERP 采购 / 销售（直写收单 + 复核表 + 结果表，ERP 代录同卡）→ **P3** Cowork 常规功能（对账、POS、盘点、预扣税、增值税、读回）→ **P4** 其余（定时任务、待处理、公司资料、看板、MR.ERP 设置、科目组）。

## 0. 来源

| 类 | 范围 | 子表（scratchpad 原始稿，每条带证据） |
|---|---|---|
| 记忆 | `~/.claude/projects/-Users-skin-pearnly/memory/` 里 CC 前端 / 交互口径约 40 份原文 | 86 条（M01–M86） |
| docs/work 早期 | 10-10 至 11-08 的 CC / assistant / operator / 前端记录 | 74 条（E01–E74） |
| docs/work 近期 | 11-09 至 11-14（含 11-09 对照 Claude 审计、批次 A、乱用设计、开始卡、UI 套件、前端选型、11-12 各项、发布记录、收款两份）+ 未合入分支 `cc-coverage-matrix-eccdec`、`cc-review-card`、`cc-card-expiry` 里的记录 | 60 条（L01–L60） |
| 共享笔记 + 交接 | `side-session-notes/` 四个目录 36 份（各主题以最新交接为准）+ `~/pearnly-devtools/main-handoff-*` 13 份 + playbook | 48 条（N01–N48） |
| 分支 | `worktrees-audit.sh` + `git branch -a`，CC / assistant / aline / guided / operator / review / ledger / collection-cc / erp-cc 相关 40 条 | 见第 4 节 |

四路原始共 268 条（记忆 86、早期 74、近期 60、笔记 48），大量重复（同一要求在记忆、评估、交接里各出现一次）。按「一个可验证的界面或交互要求」去重合并后得到下面的表。原始编号写在「出处」列，方便回查。

## 1. 结论数字

| 状态 | 条数 | 说明 |
|---|---|---|
| 已上线 | 114 | 第 4 节，只列编号与证据 |
| 已合入未上线 | 2 | U01、U02（收款，服务端） |
| 做了一半停着 | 15 | 分支上有代码没进 main（B49 无分支：代码已上线、开关关着） |
| 没开始 | 49 | |
| 没核实 | 8 | 写明为什么没核实 |
| 已被取代 | 17 | 第 3 节，写明被哪条取代 |
| **合计** | **205** | 欠账（做了一半 + 没开始 + 没核实）= **72**（B01–B72） |

欠账 72 条的去向：

| 去向 | 条数 | 编号 |
|---|---|---|
| 与界面形态无关，可随上线车走 | 45 | B04 B11–B13 B19–B23 B26–B37 B39 B40 B42 B44 B47 B49–B52 B54–B64 B67 B70–B72 |
| 引导式本体（第 1 期，在 guided-3） | 1 | B01 |
| 引导式·合入后即做（第 1 期欠项） | 7 | B02 B03 B05（界面） B06 B07 B24 B25 |
| 引导式 P1（ERP 收款） | 3 | B14–B16 |
| 引导式 P2（ERP 采购 / 销售） | 6 | B08（卡） B09 B10 B17 B48 B53 |
| 引导式 P3 / P4 | 2 | B18 B41 |
| 先要主会话 / 用户定 | 8 | B38 B43 B45 B46 B65 B66 B68 B69 |

**现在按对话形态单独做、引导式铺开会返工的（9 条）**：B24 提问卡浮在输入框上、B25 提问卡自动翻页 / 编号 / 窄屏、B16 收款复述账套名、B15 收款写成补一句、B41 乱用模式主动引导、B43 代录任务卡头部状态行、B45 现在开始 / 指定时刻、B48 MR.ERP 代录登录按钮、B05 直写确认 120 分钟失效的**界面**部分。这些都等 `guided-3` 合入后在新卡上做。
另外已上线、但引导式铺开时要重排的对话界面：代录开始卡（L27/L28/L54）、输入框上方「写入」快捷回复一行（L29）、「需要处理」框与识别进度行（E 表备注）。

## 2. 欠账表（做了一半 / 没开始 / 没核实 / 已合入未上线）

去向写法：「随车」= 与界面形态无关，可随上线车走；「引导·即做 / P1 / P2 / P3 / P4」= 并进引导式该期；「待定」= 先要定口径。
「返工」列：✕ = 现在按对话形态单独做，引导式铺开会返工。

### 2.1 引导式本体与它直接依赖的

| # | 要求 | 出处 | 状态 | 证据 | 去向 | 返工 |
|---|---|---|---|---|---|---|
| B01 | 引导式第 1 期：输入框上方业务菜单（采购 / 销售 / 收款），选了 CC 发一句要交什么；引导卡服务端出题、一张卡问完、推荐预选、点了才算、已定收起可改、「不用问」不画空卡；提问卡与引导卡合成一个组件（Questionnaire）；大列表题去服务端搜 | 用户 11-12 / 11-14；M01 M04 M24 N06–N09 L58 L59 | 做了一半停着 | 本地 `pearnly/guided-3-0cc915` b9260e2f6（28 个提交，**远端没有**；`origin/pearnly/cc-guided-phase1` 97cc633bf 是它的祖先）：`F/components/tool-uis/guide-card.tsx`、`K/write_precheck/guide.py`、`F/components/assistant-ui/thread.tsx:180,195`；main 上没有 guide-card.tsx | 引导·本体 | — |
| B02 | 代录开始卡并进引导卡（同一张卡），保住「账套已定不问、只显示名称」（3bb13af77） | guided-phase1 记录第 4 批；N04 M20 | 没开始 | guided-3 `docs/work/2026-11-12-cc-guided-phase1.md` 批次表第 4 批「未开始」；覆盖计划 §2 注 3 | 引导·即做 | — |
| B03 | 引导卡失效置灰（出了新同题卡或所属计划 / 任务失效时「已失效」） | 覆盖计划 §3；N03 | 没开始 | guided-3 的 `card-phase.ts` 无 expired；依赖 B04 | 引导·即做 | — |
| B04 | 代录任务 7 天没动作、或同公司新任务已含同一张票就失效；卡片「已失效 / หมดอายุ / Expired / 期限切れ」，和「已取消」分开，交单服务端拒 | 乱用设计 2.3、卡片有效期规则 11-13；L36 M59 N02 | 做了一半停着 | 远端 `pearnly/cc-card-expiry` caf75dc65：ff592e52e（`express_operator/tasks.py` 判定）、4e658d47d（`operator-card.tsx`、`card-phase.ts`、四语）、30283dfc3（规范草稿 `docs/development/card-expiry.md`，main 没有）；基线落后 main 约 199 个提交，与 main 冲突 | 随车（先于 B03） | — |
| B05 | 直写写入计划的「写入」和写入类允许卡出卡 120 分钟失效：编辑不续期、过期服务端拒、页面「已失效」、不自动重出 | 卡片有效期规则-对照与整改-11-13 C 节（主会话定）；N01 L37 | 没开始 | `K/assistant/write_plan.py` 无任何时间 / 过期判断（grep expir/timedelta/minutes 为空）；`F/components/tool-uis/card-phase.ts` 无 expired | 判定随车；界面随引导·即做 | ✕（界面部分） |
| B06 | 引导卡 A8 新对方、A10 新物料两题（类型 / 科目组 / 分类按众数预选收起，单位露出） | 引导式-CC读回四表-约定草稿-11-13；N05 | 没开始 | CC 桌面版主档读回四张小表没开工；`required_fields.OPEN_GAPS` 仍记缺口（guided-3） | 引导·即做（要发 CC 桌面版） | — |
| B07 | CC 复述不把刚在引导卡上答的说成「之前为这家记下的选择」 | 引导式第1期-放行一页-11-14 已知问题；N10 | 没开始 | 放行一页列为不阻塞，无修复提交 | 引导·即做 | — |
| B08 | 识别后复核卡：一批一张、上面要人定的题、下面票据表；标出单号 / 重复 / 税号 / 金额 / 日期问题格，「全部 / 有标记 / 要人定」筛选，每页 20 行服务端分页；点格就地改（存票面）、即时校验、标红不拦、改完重算；确定才写 | 用户 11-09 硬要求（review-before-record）；乱用设计第 0 层；L33–L35 M25 N11 N12 | 做了一半停着 | 远端 `pearnly/cc-review-card` 8aec2404f（3 个提交，只有服务端纯契约 `write_precheck/review.py`、`tests/test_review_card.py`，main 都没有），无接口、无前端；现在写前只有写入计划一句复述 | 契约随车；卡在引导·P2（P1 收款复核也用它） | — |
| B09 | 复核卡与开始前要人定的问题合在一处一次办完 | review-before-record 11-09；M26 | 没开始 | 依赖 B08 | 引导·P2 | — |
| B10 | 每批写完一张结果卡 / 汇总（已入账 · 已跳过 · 失败 · 等你选，点开逐行），直写多张同形 | 乱用设计 4.1；L46 M61 N16 | 没开始 | main 无直写汇总组件，只有代录词条 `op.doc.skipped` | 引导·P2（结果表） | — |
| B11 | 同一张票到哪里都同一套状态词（待复核 / 未写 / 排队中 / 已入账·单号 / 结果不明 / 失败 / 已跳过），直写推送步骤有「已跳过」 | 乱用设计 1.2 1.3；L47 | 没开始 | main 无统一 `ticket_state` | 随车（服务端） | — |
| B12 | 一张票一个身份：直写代录都不重复录；CC 问之前先查账本（录过的问「跳过 / 再录」、不预选） | 用户 11-10 裁定；decisions-in-conversation；M31 | 做了一半停着 | 远端 `pearnly/cc-ticket-identity-3` e8bb74653（5 个提交，`express_rules/contracts/ticket_identity.py` main 没有）；main 只有 `ledger_tickets.check_present`（7b19dd2a8），写入计划没调用；与 main 冲突（63 个文件） | 随车（引导卡「自动定的不问」靠它） | — |
| B13 | 公司 / 工作空间 / 账套目录都没税号时，卡片问一次「哪边是本公司」；ERP 代录收单 5 个修复 | A线-本公司税号问一次-影响评估-已审-11-10 | 做了一半停着 | 远端 `pearnly/cc-own-tax-once-2`（8c45944a6、1627a7701、cf3bcac96，main 无 `write_precheck/own_party.py`）+ `pearnly/erp-cc-sales-tickets` 的 681c8a0cb、66dd512c1、d78ee9438、5f8c684eb、119af943d；都与 main 冲突 | 随车（题由引导卡显示） | — |

### 2.2 引导式后续各期（按用户顺序）

| # | 要求 | 出处 | 状态 | 证据 | 去向 | 返工 |
|---|---|---|---|---|---|---|
| B14 | ERP 收款引导卡：客户 → 未收发票 → 方式 → 凭证（卡上交，走 `files.commit`）→ 折扣 / 部门 / 销售员 → 复核 → 写入 | 覆盖计划 §3 P1；N13 | 没开始 | 覆盖计划 §2「收款 ○⁵：现在是对话 + 确认 + 写入计划，没有引导卡」 | 引导·P1 | — |
| B15 | ERP 版 CC 收款写成后在对话里主动补一句结果 | collection-cc-chat「口径与交接」；L49 | 没开始 | `E/collection/push.py:30` TELL 只有 line | 引导·P1（结果卡） | ✕ |
| B16 | 收款复述只念账套名，不念「名称 · 位置路径」 | 收款闭环-进度-11-14；放行一页已知问题；N23 | 没开始 | 记录存 `target.option_name`；无修复提交 | 引导·P1 | ✕ |
| B17 | ERP 直写对话收单 + 复核表 + 结果表（采购 / 销售），ERP 代录同卡 | 覆盖计划 §3 P2；N14 | 没开始 | 覆盖计划 §2「ERP 直写 ○⁴」 | 引导·P2 | — |
| B18 | Cowork 常规功能与其余功能都要有引导（对账、POS、盘点、预扣税、增值税、读回；定时任务、待处理、公司资料、看板、MR.ERP 设置、科目组），现在只能打字办的工具见覆盖计划 §4 | 用户 11-14「引导式覆盖所有功能」；M03 N15 | 没开始 | 覆盖计划 §2 全是 ○ | 引导·P3 / P4 | — |
| B53 | ERP 代录目标不写死 Express；引导卡出题源补 MR.ERP 必填与可选来源（付款方式、对方档、分店、币种） | 覆盖计划 §2 注 2、§3 P2 | 没开始 | 覆盖计划：「ERP 的 MR.ERP 开关未开、ERP 代录目标写死 Express」；出题源目前只按 Express 必填登记表 | 引导·P2（依赖 MR.ERP 写账线） | — |

### 2.3 与界面形态无关、可随上线车走

| # | 要求 | 出处 | 状态 | 证据 | 去向 | 返工 |
|---|---|---|---|---|---|---|
| B19 | 对话里的删除类（删识别单据、删回读、删对账、删报税草稿、删盘点 / 结束盘点）各加允许 / 拒绝卡，列清删哪几条；代录 retry 也走允许卡 | 用户 11-12（cc-scope-decisions 第 4 项）；L57 M65 N17 | 没开始 | `C/assistant/tools.py:4930-4934` `cowork_delete_documents` 说明仍写「no extra confirmation step」；其余 4 个删除工具无 `"approval"`；允许卡组件现成（`F/components/tool-uis/approval-bar.tsx`） | 随车（P4 用同一允许卡，不返工）；约 10 条新文案先给用户看 | — |
| B20 | 「立即运行定时任务」改走写入计划（先复述、用户点头再跑，复述写出会写进的账套） | 用户 11-12；L56 M66 N18 | 没开始 | `C/assistant/tools.py:3503` run_folder_task 直接调 `rules.run_now`；`write_plan.py` 无此模式 | 随车（服务端写入计划）；确认按钮随引导 | — |
| B21 | Cowork 已放行（排队中）的推送可撤回：录入详情页「撤回」、CC 撤回工具、正在写时「正在写入，无法撤回」、删单前先撤 | A线-Cowork已放行推送撤回-现状与改法-11-13；N22 | 没开始 | `C/push/native/service.py:1067` 只有 `cancel_prepared`；`tools.py:4905` 仍写 already_pending 不再推；约 5–6 条新文案 | 随车 | — |
| B22 | CC 回复不带账套内部编号（给模型的说明不给 id）；账套名缺失时开始卡等三处回退成位置、不回退 book_key | A线-交接-11-13晚 第 4 条；第8棒进度 §2；N19 N20 | 做了一半停着（id 那半没开始） | `K/assistant/ledger.py:267`（SET_NOTE 带 `(id {id})`）、`:282-283`、`:293`；回退那半在远端 `pearnly/aline-start-card-names-wip` 57eb1dd18（闸门没跑；main `express_operator/shapes.py:95` 仍 `or out["account_set"]`） | 随车 | — |
| B23 | 代录只收当月的单时说清原因，不只回「没排进计划」 | A线-交接-11-13晚 第 5 条；N21 | 没开始 | `write_plan.py:216,285` 回 nothing_to_write | 随车 | — |
| B26 | 对话标题提速：发出后约 3 秒出摘要标题（现约 55–60 秒） | 审计 #6、批次 A ⑦ 第二段；L08 N25 | 没开始 | `K/assistant/titles.py` 11-09 后只有写回冲突修复 ef1e718d3、用量记账 72ce3ae9f | 随车 | — |
| B27 | 已发消息能「重试」，「编辑后重答」先查引擎能否截断 | 审计 #5（交互批 B）；L09 | 没开始 | `F/components/assistant-ui/thread.tsx:817` 注释「做不到原样替换就不摆」 | 随车（外壳） | — |
| B28 | 侧栏搜索搜正文，带摘录与高亮（先估服务端成本） | 审计 #8；L10 | 没开始 | `F/components/assistant-ui/thread-list.tsx:163-181` 只按标题过滤 | 随车 | — |
| B29 | 快捷键一组（新建、⌘K 搜索、加文件、↑ 重用上一句）+ 快捷键表 ⌘/ | 审计 #9；L11 | 没开始 | 现有只有 ⌘B（组件库自带）、代录 Ctrl+N、设置 Ctrl+,、切模式、侧栏 Ctrl+数字 | 随车 | — |
| B30 | 打开对话用灰色骨架占位，替掉「吉祥物 + 正在加载消息」 | 审计 #12；L12 | 没开始 | `F/assistant.tsx:206-214` ThreadLoading | 随车 | — |
| B31 | 侧栏两个同名公司组的组头补「· 分店 00012」 | 审计「顺手看到」；L14 | 没开始 | `sidebar-view.ts`、`sidebar-group.tsx` 无按分店区分 | 随车 | — |
| B32 | 输入框附件显示真实上传进度（按字节细进度条），上传中发出后气泡里接着显示 | 上传研究 U1 U4（主会话 11-09 审定）、code-style §5；E41 L17 N26 | 没开始 | `F/lib/managed-agents/attachments.ts:125` `progress: 0.3` 写死；`components/ui/progress.tsx` 业务没用 | 随车 | — |
| B33 | PDF 传完，输入框方块换第一页缩略图（同 Claude） | 上传研究 U1b；E42 | 没开始 | `F/components/assistant-ui/attachment.tsx:143-150` AttachmentThumb 只给图片画面；第一页小图只用在发出后的消息卡（`:326`） | 随车 | — |
| B34 | 上传失败能重试的点方块重试 | 上传研究 U5、code-style §13 Q9；E44 | 没开始 | `attachment.tsx:190-210` 出错只有原因 + 移除；「原因手机可见」那半已做 | 随车 | — |
| B35 | 输入框正常附件方块（图片、上传中）整块换标准组件 Attachment | cc-composer-send-保留项 第 1 条；N27 | 没开始 | `attachment.tsx:190-210` 只有出错卡用 Attachment；原定归引导式第 1 期，guided-3 没改这个文件 | 随车 | — |
| B36 | 代录对话选中就上传、点发送才识别；方块只显示上传中 / 上传失败；没发就移除的不识别不扣费 | 上传后不提前识别-一屏-11-14；上传研究 U3；E43 N28 | 做了一半停着 | 本地 `pearnly/cc-upload-read-on-send-c21d68` 6ee01f0ec（1 个提交，远端无，验证未填）；main `attachment.tsx:165-178` 仍在方块上转「排队中 / 识别中」 | 随车（引导卡交文件按它的接缝 `files.commit`，**合入先后要和 guided-3 定**） | — |
| B37 | 存量手写控件「改到哪换到哪」换成 `components/ui` 标准组件（基线 337 处 / 34 个文件，含直接用 radix Dialog 的公司选择窗、MR.ERP 连接、删除确认等，停止后「重试」按钮） | 用户 11-12（frontend-standard-practice、ui-kit 评估）；L52 M12 E54 L05 | 做了一半停着 | 闸门已上线（`scripts/check-frontend-kit.py`，a1e08d24a），基线 340→337；`F/components/company-picker.tsx:18`、`mrerp-connect.tsx:13` 仍 import radix；guided-3 换了 ask-card / guide-card | 随车（各期改到哪换到哪） | — |
| B39 | 所有题目和选项四语、不出代码原值，加全量枚举检查（缺词条就红） | 乱用设计 3.4；L45 N31 | 没开始 | `tests/` 无枚举预检项 × 选项码的检查；guided-3 改为服务端按本轮语言出题，可能部分覆盖（没核实） | 随车 | — |
| B40 | 页面转圈 60 秒没进展、同一张卡 5 秒连点 3 次以上要上报 | 乱用设计 6.1；L50 M64 N29 | 没开始 | `F/lib/caught.ts` 只报异常 | 随车 | — |
| B42 | 每日「已写 / 跳过 / 可疑」清单给会计 | 乱用设计 5.5；M62 | 没开始 | 全仓含分支搜不到实现，只在设计提交 88f2de75a | 随车 | — |
| B44 | 代录暂停原因在所有设备都看得到（CC 桌面版心跳带上） | code-style §14.7（11-04 审定「记入下一批」）；E23 | 没开始 | `F/components/operator/bridge.ts:37-41` 只在执行电脑那一页合成；companion 心跳无暂停原因 | 随车（要发 CC 桌面版） | — |
| B47 | 代录「需要你」时同时发系统通知 | 代录产品评估 10-26 §10③；E59 | 没核实 | `express_operator` 包无 `notices.post`；直写回执提醒在 `K/assistant/push_followup.py:78`，代录是否经它没查清 | 随车 | — |
| B49 | MR.ERP 代录开关打开（真站点探路、配方核成 verified） | cc-mrerp-operator；E70 | 做了一半停着（无分支，代码在 main / 生产 / 1.12.21，开关关着） | `K/express_operator/contracts.py:73` `MRERP_OPERATOR_ENABLED = False`；11-09 探路四个列表页打不开，等岳父设菜单 | 随车（外部等人） | — |
| B50 | ERP 员工浏览器里也有只读「连接」栏、公司选择窗空时也有「连接」按钮，按产品声明不按产品名写死 | ERP-Cowork-CC 差异盘点 #12 P2-2；E39 N33 | 没开始 | `F/components/settings.tsx:114` `product === "cowork"`；`F/lib/desktop.ts:54` 同样 | 随车 | — |
| B51 | ERP 右栏「记住的选择」卡（能看能删），服务端补 forget | 差异盘点 #9 P2-4；E50 N32 | 没开始 | `E/masters/cc_projects.py:27` SECTIONS 无 choices；`E/express/choices.py` 无 forget | 随车 | — |
| B52 | ERP 对话里交代「某费用记到某科目」按公司记住 | express-operator-dialog-评估 10-29；E74 | 没开始 | Cowork 有 `C/assistant/company_tools.py:678`；`E/masters/cc_tools.py:208-228` 只有 profile / note | 随车（文档写「另议」，见待定） | — |
| B54 | 代录存单遇不认识的弹框：Express 不关、留给人，回报带半截单号；新原因四语；未完成单显示单号 | 11-14 交接「当天已定」；N35 | 做了一半停着 | 远端 `pearnly/cc-desktop-half-doc-1ee3e3` 18a14ddf4（6 个提交）：68c7625c0（四语、`operator-shared.ts`）、193dcde30 / d811e9419 / 18a14ddf4（CC）；测试机真跑没做 | 随车（服务端先上、CC 后发） | — |
| B55 | CC 桌面版位置能只选其中几本账套，心跳只报所选 | 11-13 晚交接 十二；N36 | 做了一半停着 | 同上分支 193dcde30（`companion/src/companion/express_places.py`、`test_place_pick_books.py`） | 随车 | — |
| B56 | 换位置后启动缓存不再整份上报范围外账套 | CC桌面版-交接-11-13晚 §4；N37 | 做了一半停着 | 同上分支（places_scope） | 随车 | — |
| B57 | 代录打开的 Express 窗口超出屏幕时自动缩放、挪回工作区 | 11-13 / 11-14 交接；N38 | 做了一半停着 | 同上分支 193dcde30、fbb323db2（`express_operator/desk.py`、`win32_desk.py`、`test_operator_fit.py`）；150% 缩放真机没验 | 随车 | — |
| B58 | CC 桌面版更新：托盘显示下载进度、断点续传、停滞重连 | CC桌面版-更新分发要点-11-13；用户「一个多小时不像话」；N39 | 没开始 | `companion/src/companion/gui_tray.py` 无进度 | 随车（要发 CC 桌面版） | — |
| B59 | CC 安装包改从 R2 下载 | 线上访问速度-一屏-11-14；N40 | 做了一半停着 | 远端 `pearnly/prod-speed-2-5237c8` 222f7105b；等用户建 R2 令牌与 3 个 GitHub 密钥 | 随车（外部等人） | — |
| B60 | ERP 侧「CC 桌面版未连接」改「未连接电脑」（Cowork 10-18 已改） | user-decisions-2026-10-18；M70 | 没开始 | `E/express/pending.py:73`、`E/public/desk/transactions.js:137`、`E/integrations/line_collection.py:415`、`E/i18n/messages.tsv:1361` | 随车（改已有文案，先给用户看） | — |
| B61 | 只剩一个位置时「×」有标准禁用样式 | next-batch-items 10-26；M86 | 没开始 | `F/components/settings.tsx:580` 有 disabled，`F/globals.css:1145-1152` `.cc-row-btn` 无 `:disabled` | 随车 | — |
| B62 | 一直开着的电脑拿到焦点时也核对对话列表（别处刚置顶的即时进「已置顶」） | cc-pin-order-race「已知限制」；E14 | 没开始 | `F/lib/managed-agents/list-sync.ts:16,249` 只听 visibilitychange；开发窗口自定取舍，未经用户 | 随车（影响小） | — |
| B63 | ERP 版 CC 补齐 ERP 网页每个动作（建单、改单、作废、收付款、盘点、库存查询、看板等约 36 类），含 ERP CC 查库存 | 用户 11-08（cc-equals-web-plus-operator）；CC全覆盖总表一屏缺口 3；N34 | 没开始（库存查询做了一半） | 一屏摘要「补齐 ERP 没人在做」；远端 `pearnly/erp-cc-stock-lookup` 3d356bbc9（`inventory/cc_tools.py` 无库存卡，与 main 冲突） | 工具随车；引导随 P2 / P4 | — |
| B64 | CC 不在沙箱里自己读票据，一律交识别工具；没有识别工具就说一句 | 11-08 B 线；分支 cc-no-self-read | 做了一半停着 | 远端 `pearnly/cc-no-self-read` 3edfc4cf6；main `K/assistant/definition.py` 无此段；与 main 冲突 | 随车（模型指令，对话为辅也要） | — |

### 2.4 属于对话界面、要等引导卡或先定口径

| # | 要求 | 出处 | 状态 | 证据 | 去向 | 返工 |
|---|---|---|---|---|---|---|
| B24 | 提问卡在电脑和手机上都浮在输入框正上方，对话流里只留「正在提问」小标签 | 审计第五、六节（主会话 11-09 定）；批次 A A7；L22 N24 | 没开始 | `F/components/tool-uis/approval-sheet.tsx:5,24` 写明 ask_user 不浮；i18n 无「正在提问」 | 引导·即做（在新卡上做） | ✕ |
| B25 | 提问卡单选点一下自动翻页、选项前加编号、窄屏翻页行不另占一行 | 审计卡片四 ①②⑤；L19–L21 | 没开始（窄屏没核实） | `F/components/tool-uis/ask-card.tsx:165-170` 要点「下一步」；无序号；guided-3 已把 ask-card 改成 Questionnaire | 引导·即做 | ✕ |
| B41 | 认出乱用模式（逐页截图逐张发、卡住的任务、过期卡片）主动引导，同一问题只问一次带推荐 | misuse-graceful-handling 11-08；设计 2.1 2.2；M58 | 没开始 | 搜不到「整份 PDF 一次发」类引导 | 引导·P3/P4 各期内（换成卡片 / 状态） | ✕ |
| B43 | 代录任务卡头部固定一行写在等什么（等你选 N 题 / 录入中 n/N / 已完成），执行电脑离线时灰态、「连接中断，正在重连…」 | 乱用设计 2.2；代录评估；L39 E66 | 没开始（头部状态行没核实） | 只找到「等待电脑 · 电脑名」；其余状态与离线灰态没找到；评估写明开始后页面拿不到执行电脑在线状态 | 待定（和 11-04「不新造结束框、任务清单」要对一下） | ✕ |
| B45 | 代录开始方式可选「现在开始 / 指定时刻开始」 | 代录产品评估 10-26 §12.1 定案 1；E57 | 没开始 | `K/express_operator/tasks.py:389` 批次 start_at 写死 0；`operator-start.tsx` 无此项 | 待定（11-10 起改成对话里同意开始，随 B02 一起定） | ✕ |
| B46 | 进行中可观看：实时画面（operator_frame）+「查看」把 Express / MR.ERP 窗口提到前面（operator_show） | 代录产品评估 10-26 §10③；E58 | 没开始 | 桥已有 `companion/src/companion/tomo_bridge.py:51`；前端 `F/components/operator/bridge.ts:85-94` 只调 state / control | 待定（11-04 去掉代录右栏后没人再定） | — |
| B48 | MR.ERP 代录在 CC 页面的「MR.ERP 登录」按钮（operator_authorize target=mrerp；断开时 operator_forget） | cc-mrerp-operator「剩余项」第 3 步；E69 | 没开始 | `F/components/tool-uis/operator-shared.ts:275-280` authorizeHere 不带 target；桌面端已能按 target 转（`tomo_bridge.py:197`） | 引导·P2（MR.ERP 列） | ✕ |

### 2.5 口径待定与没核实

| # | 要求 | 出处 | 状态 | 证据 / 为什么没核实 | 去向 |
|---|---|---|---|---|---|
| B38 | 「记住的选择」收起时写「代码 名称」（如 BIG C-002 ซอยประชุม），与 CC 说的一致 | 乱用设计 3.3（主会话 11-09 定）；L44 N30 | 没开始 | `F/components/company-cards.tsx:662-674` CODE_KINDS 收起只写代码，是 10-17 第 10 轮「280 宽放不下」的口径；两条冲突 | 待定 |
| B65 | 11-10「卡片不放开始，一句话下令」与引导卡「点『写入』或说一句开始、答案随允许交」措辞是否一致 | decisions-in-conversation；M32；`guide-card.tsx:5`（guided-3） | 没核实 | 口径对照项，不是代码欠账 | 待定 |
| B66 | 公司主档后续：批量添加、拖放移入、同税号 Express 与 MR.ERP 合一家、公司级参考文件、合并申报汇总 | 10-15 handoff-cc T6；cc-company-master「下一批」；E16 | 没开始 | `company-picker.tsx` 无多选；文档只写「按用户需要排」，未见用户批；ERP「从连接添加」另列 | 待定（多半由公司接入重做取代） |
| B67 | 归不上公司的待处理单在侧栏最上面出一行「未归属公司」 | code-style §12 Q4 审定；E22 | 没核实 | `projects.tsx:11` 注释说放在 SidebarError 那块，`thread-list.tsx:771-804` 没找到独立实现；要页面实看 | 随车 |
| B68 | 截 Claude 桌面版 Code 页，核对上传卡下面那一行、失败行排法、出错、手机的样子 | code-style §12 Q12、code-run-ui「待块 0 截图」；E25 | 没核实 | 仓库无完成记录；要用户在场授权电脑控制 | 待定 |
| B69 | 三处开发窗口自定的口径：会话栏并排 / 浮层分界（对话最窄 24rem + 280）、CC 桌面版标题栏 40 | cc-shell-rebuild:93,113；E17 | 没核实 | 没找到用户确认记录 | 待定（低） |
| B70 | 选项卡一行里供应商名不要连写两遍 | 代录界面两处待改；E64 | 没核实 | 取决于服务端 item.label，要实测 | 随车 |
| B71 | 代录中途要选的单挪到最后、其余照录；交代按公司记住 | cc-express-ai-operator-direction 10-29；M77 | 没核实 | 没找到对应代码说明 | 随车 |
| B72 | 图标所有尺寸用带胡须原版 | tomo-product-name 10-10；M71 | 没核实 | `F/brand/cc-icon.svg`、`public/icon-*.png` 没逐张看图 | 随车 |


### 2.6 已合入未上线

| # | 要求 | 证据 |
|---|---|---|
| U01 | ERP 收款：CC 列本公司全部收款、可按「谁录的」筛、能改能撤回同事录的；写前被拒的退回草稿，CC 里可改了再放行 | d0190e4e0、a724dc175（main 有，6f58f0b48 没有）；L60 N47 |
| U02 | 「没有覆盖此日期的 Express 账套」改成写出单据日期和每本账套的期间（收款、ERP 单据页、待处理） | 91e700a45；N48 |

## 3. 已被取代（17 条）

| 原要求 | 出处 | 被哪条取代 |
|---|---|---|
| 主档页做「公司」导航下的管理页、公司页同对话页版式 | E02（10-16） | 11-07 删左侧公司入口与公司页（25aed08b7，已上线） |
| 删除对话不确认 | E12（10-16 开发自定） | 用户 10-17「删对话先弹一次确认」（`F/components/delete-chats.tsx`，478fa8f97） |
| 代录点公司每次建一个准备中任务 | E30（11-04） | 11-04 统一框架：选公司进新建页、不建任务 |
| 代录方块传完接着显示识别进度 | E52（11-06） | 11-08 U3 + 11-14「发送才识别」（B36） |
| 代录一步一卡向导（公司→期间→单据→预检→电脑→开始） | E56（10-26） | 11-04 卡同允许卡、11-10 对话里同意才开始；11-14 引导卡将再定（B02） |
| 完成汇报：导出 docx/pdf、逐张截图、「重录失败的」按钮 | E60（10-26） | 用户 11-04「不新造结束框、任务清单」 |
| ERP 对话写工具出「允许」卡 | E71（11-02） | 11-10 写入计划复述 + 对话里同意（33817b8bf） |
| ERP CC「添加公司」打开 Desk 新建页 | E72（11-02） | 11-07 对话里点公司弹选择窗；11-13「从连接添加」另列 |
| 卡开着打字 = 交当前题「其他」 | L24（11-09） | 11-12 打字让卡作废、整句当回答（3e270fff5） |
| 直写推送排队界面显示电脑名 | L41（11-13） | 主会话 11-13 定由 CC 在回复里说（874612e9e） |
| 复核卡「已录默认不勾、点开始才写」 | M29（11-09） | 11-10 不做默认勾选、卡上无「开始」 |
| 录入口无确认步骤 / 不加识别后提示（CC 部分） | M30（10-02、09-26） | 11-09 识别后必须复核（Cowork Desk 录入页仍按原口径） |
| 连接页 MR.ERP、Express 两张卡 | M39（11-07 上午） | 同日收窄：Express 不做卡片（M38） |
| 已写过的默认跳过并标单号 | M57（11-08） | 11-10 先核账本、问「跳过 / 再录」、不预选（B12） |
| 写错可一键 Express 原生作废 | M60（11-08） | 11-09「写错的单不管」 |
| 代录右侧面板五块 | M78（10-27） | 11-04 代录并进对话（eddc835fd） |
| 实操保留对话框照 Claude 底部一行、非主角 | M79（10-29） | 11-04 一套界面 |

## 4. 已上线（114 条，只列编号与证据要点）

逐条证据在 scratchpad 原始稿；这里按主题合并，括号里是原始编号。全部提交已逐个核为 6f58f0b48 或 companion-v1.12.21 的祖先。

- **外壳 / 密度 / 键盘（13）**：右栏公司卡（E01 M06，442e3d985）· 密度照 Code（E03 M07，`globals.css:552`）· 键盘这批（E04 M08，beb8b5519）· 组标题纯 Code（E05 M09）· 悬停揭示（M10）· 步骤组按类摘要（E06）· 去侧栏定时任务行（E07）· 卡头摘要常显（E08）· 首页动画停（E13）· 滚动条悬停才出（E28）· 「执行中」（E29）· 左上直写 / 代录切换（E55 M76）· 模式只给自己的工具、两个界面（M56）
- **侧栏 / 对话列表（11）**：拖动统一（E09 M51，fa2d9499b）· 本公司内排序 / 置顶（M52 M53，f11cdf5d0）· 空对话不进侧栏（E10）· 已有对话不能换公司（E11）· 侧栏一框架两模式（E34 M41 M18）· 待处理入口删（E21 M16）· 切模式离开（E27）· 删除先确认（M54，478fa8f97）· 新建 / 切换时侧栏滚到并高亮（L07）· 标题 = 首句摘要（M40）· 网址带对话编号刷新回原处（L01）
- **公司 / 连接 / 账套（14）**：点公司弹选择窗（E35 M37）· 删公司入口与公司页（E36 M36）· 公司页功能搬右栏（E37）· MR.ERP 连接（E38 M38，76e528aec）· 月结（M14）· 记住的选择卡加入账方式、现用账套（M55）· 已记住不出题（M22）· 选择器是权威输入（M74）· 常显本次账套（E24）· 右栏账套下拉（L30）· 新建页账套胶囊（L31，a9f115c46）· 不露 choice_needed 原因码（L32）· 位置行（E51）· 直写右栏保留（M17）
- **输入框 / 上传 / 发送（12）**：草稿与附件刷新还在（L02）· 回复中回车排队（L03）· 首轮逐字（L04）· 停止保留半段 + 重试（L05）· 电脑跟随滚动（L06 M45）· 拒收原因四语当场说（L15）· 失败卡不挡发送（L16 N42）· 挂卡也能打字（L23 N42）· 新对话贴文件不跳（N43）· 上传状态卡片（M19）· 附件方块尺寸（M48）· 输入框底部一行（E61）
- **识别等待（5）**：超时不只回一句（E45）· 挂着等时输入框不锁（E46 M75）· 挂起即推进度（E47）· 第 x / y 页（L18，a58460089）· 旧对话段读不到时一句说明（L53）
- **提问卡 / 开始卡 / 写入计划（14）**：提问卡照 Claude Code（M21，0691f8241）· 跳过 = 依赖它的单不录（M23）· show_if 随答收放（L25）· 题目用本轮语言（L26 N46，c42710e69）· 开始前一张卡、CC 预选（M20）· 开始卡排第一句之后、最多 5 行（L27）· 卡上无「开始」、一句话下令（L28 M32，33817b8bf）· 开始卡账套已定不问、只显示名称（L54 N44，3bb13af77）· 写前按写入计划复述（M33）· 「写入」快捷回复（L29 M35 N41，c80c1e45f）· 随时说停（M34）· 别的设备也能停（E26）· 打字对话保留为辅（M02）· 写错不做原生作废（M28）
- **代录过程（12）**：右栏删、并进对话（E20 M15，eddc835fd）· 一套界面（M18）· 选项卡（E62）· 不露内部编号（E63）· 状态复用实时状态行（E65）· 单张重试 / 整批继续（E67）· 转人工照原文 detail（E68）· 子菜单中途问一次（E49）· 等电脑排队「等待 CC 桌面版」（L43）· 「等待电脑 · 电脑名」「您的电脑离线」（L40 L42 N45，03f31bb34）· 代录不挂收款工具（M05）
- **ERP 版 CC（3）**：识别对话附件（E53）· 待处理补收银日汇总等（E73）· ERP 员工 Windows 浏览器提示下载（E40）
- **结论回报（2）**：直写结论晚到在原对话补一句（L48，e209b4e20）· 前端失败上报（M63）
- **手机 / 安装（8）**：手机新对话公司 / 月份 / 分店位置（M42）· 已有对话「…」（M43）· 输入框收成一行（M44）· 按钮尺寸照 Claude（M46）· 顶栏两行标题（M47）· 安卓可安装（M49）· 安装入口按系统区分（M50）· 「添加到程序坞」× 记住（L13）
- **规范 / 文案 / 品牌（9）**：样式只走变量 + 闸门（E18 M11）· 组件库与「新增手写控件」闸门（L51，存量见 B37）· SVG 图标（M13）· CC 网页四语（M67）· 桌面版四语（M68）· 不叫小助手（M69）· Cowork「未连接电脑」（M70，ERP 侧见 B60）· 胖橘形象（M72）· 退出不出确认页（M73）
- **CC 桌面版（11）**：托盘提醒按公司打开（E15 M84）· 设置电脑列表（E19）· 同站点链接（E31）· 托盘回上次对话（E32）· 老电脑先显示图标（E33）· 轻量（M80）· 一键安装（M81）· 只在点「检查更新」时查（M82）· 防睡眠两开关（M83）· 加新位置不改主根（M85）；另：不再弹「账套正被占用」框、按等账套排队（L55，7e9b52d8a）

## 5. 分支处置建议（只建议，不删）

40 条相关分支（本地与远端同名同 SHA 算一条）：**可删 11 / 摘提交或继续 14 / 整支作废 15**。「已在主线」按提交说明完全相同找到 main 上的改写版，没有逐行比对内容。

**可删（内容已在主线）**：`aline-collection-not-yours`、`aline-flow-ledger-ids`、`aline-start-card-ledger`、`cc-conversation-ledger`、`cc-reply-language`、`erp-void-cancels-ledger-job`（都 = main 33a3ec5d5）；`cc-11214-25c613`（本地，8 个都进）；`cc-book-identity`（33a3ec5d5 / d73133b14 / 59a1e3db2 / 583e01f84 / db082e59b）；`cc-ledger-check`（6d6329a08）；`collection-cc-chat`（053fd7b50 / 50d572a4a / 12e82264c / e1775c8f4）；`collection-on-ledger` 本地与远端（8488276d0 等）。

**摘提交续做 / 继续本支**：

| 分支 | 摘什么 | 对应欠账 | 与 main |
|---|---|---|---|
| `guided-3-0cc915`（本地） | 继续本支；先推远端 | B01 | 能干净合并 |
| `cc-review-card` | 86b3d2073、47dda6bd2、8aec2404f | B08 | 能干净合并 |
| `cc-card-expiry` | ff592e52e、4e658d47d、caf75dc65（+ 30283dfc3 规范草稿）；ff10344c0 已进 | B04 → B03 | 冲突，在新 main 上重做 |
| `cc-ticket-identity-3` | 59308acbd、2f5c9ed85、1659528fe、05d561262、e8bb74653 | B12 | 冲突（63 个文件） |
| `cc-own-tax-once-2` | 8c45944a6、1627a7701、cf3bcac96（b6310c1fc 已进 b011e6c28） | B13 | 冲突 |
| `erp-cc-sales-tickets` | 只摘 681c8a0cb、66dd512c1、d78ee9438、5f8c684eb、119af943d，其余 24 个被 -3 / own-tax-2 取代 | B13 | 冲突 |
| `aline-start-card-names-wip` | 57eb1dd18（丢掉其中 main 已有的 cherry-pick） | B22 | 能干净合并 |
| `cc-coverage-matrix-eccdec` | 532cf6d8c、6a24e3862（纯文档，引导式计划当总表引用） | — | 能干净合并 |
| `cc-no-self-read` | 3edfc4cf6，按现 `definition.py` 重做 | B64 | 冲突 |
| `erp-cc-stock-lookup` | 3d356bbc9 | B63 | 冲突 |
| `cc-result-unknown-unified` | 9c01e1580、9eb5b712f；先确认 ADR-027 §6 还有效（ERP 网页 desk，不是 CC） | — | 冲突（42 个文件） |
| `cc-desktop-half-doc-1ee3e3` | 继续本支（今天在做） | B54–B57 | 能干净合并 |
| `cc-upload-read-on-send-c21d68`（本地） | 继续本支；**与 guided-3 定合入先后** | B36 | 能干净合并 |
| `mrerp-ledger-dialog`（本地） | 继续本支（不属于 CC，名字带 ledger） | — | 基线 = 当前 main |

**整支作废**：`cc-guided-phase1`（本地 45d22d673）、远端 `cc-guided-phase1`（97cc633bf，是 guided-3 祖先，guided-3 推远端后删）、`guided-2-8ff7d9` = 远端 `cc-guided-phase1-wip`、`guided-2-pre-rebase`（都已在 guided-3）；`cc-guided-entry-51673b`（两份文档已带进 guided-3，静态预览页被真引导卡取代；工作树只有未跟踪 `.claude/`）；`cc-ticket-identity`、`cc-ticket-identity-2`、游离 b0b49c6e3（被 -3 取代）；`cc-own-tax-once`（被 -2 取代）；`cc-party-once-scenario`；`erp-cc-edit-document`（重复副本）；`erp-void-ledger-gaps`（已在 `erp-void-gaps-1114` 重做）；`cc-to-dsh-assess-c90da6`（11-07 放弃 dsh）；`express-operator-pilot-6bffe4`（想留档只摘报告 efb059d17）；`worktree-agent-a1b8249b13f8d37e4`（10-27 原型，**工作树里 25 个文件已暂存未提交**，删前要人确认）；`mixed-operators-parens`（纯格式）。

## 6. 排期建议与要定的事

1. **合入先后**：`cc-upload-read-on-send`（B36，引导卡交文件按它的接缝）→ `guided-3`（B01，先推远端）→ `cc-card-expiry` 摘 3 个提交在新 main 上重做（B04）→ 引导·即做（B02、B03、B05 界面、B06、B07、B24、B25）。`cc-review-card` 契约（B08）在 P1 收款复核前合。
2. **随车可以马上排的**（不怕引导式返工）：B19 删除类允许卡、B20 立即运行走写入计划、B21 撤回、B22 不带内部编号、B23 当月原因、B26 标题提速、B32–B35 上传四项、B50 / B51 ERP 两处写死、B54–B57 半截单那支、B60 ERP 旧文案、B61。其中 B19、B21、B60 有新文案 / 改已有文案，要先给用户看。
3. **要定**（主会话可定的先定，涉及口径的给用户一次）：B38 记住的选择收起写法（10-17 只写代码 vs 11-09 代码 + 名称）；B43 代录任务卡头部状态行是否算「新造任务清单」；B46 实时画面 / 「查看」还要不要；B45 指定时刻开始是否随引导开始卡做；B65 「一句话下令」与引导卡「点写入」措辞；B66 公司主档后续是否被公司接入重做取代；B68 截 Claude Code 核样子要用户在场；`worktree-agent-a1b8249b…` 25 个暂存文件能否丢。

## 7. 限制

- 只读盘点：没跑界面、没连测试机与生产；「已上线」只证明代码在线上版本里。
- 去重靠人工合并，原始 268 条与合并后编号的对照写在每行「出处」列；scratchpad 原始稿（items-memory / items-docs-early / items-docs-late / items-notes / branches）未入库，需要时可再出。
- 「已在主线」靠提交说明完全相同判断，没逐行比对改写后的内容。
- 没读：ADR-027 提议原文、前端选型与组件库两个截图目录、A 线 11-09 / 11-10 第 3–5 棒交接、`local_74a4f119` 笔记目录。
