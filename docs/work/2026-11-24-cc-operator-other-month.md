# 代录别月票确定前出题（cc-operator-other-month，11-24，P4）

主会话 11-24 批准的 (b)：代录只收对话月份的单，别的月份报「请求无效」→ 确定前出题说清事实让人选。

## 根因
- 代录确定 → `documents.confirm` → `express_operator.api.hand_in` → `start(task=…)`：交哪些单由 `api.waiting(公司, 任务月份)` 定，只含本月资料加
  「本人放进这次代录任务、票面不是本月的」（资料行带 `other_month`）。一张不剩就整批 `Invalid request`。
- Cowork：代录对话里经引导卡交的文件走 `operator_source.intake`，不记进任务的上传批次，所以别月票不算「放进任务的」，确定时整批「请求无效」。
- ERP：资料本来就列出本人所有收单记录（别月的带 `other_month`），所以不报错，但不问就交了。
- 写进哪本：代录写进本对话账套（A 线）。票面日期不在它的会计期间里时 Cowork 推送停在「期间不符」（`express_rules/targets/account_match.conflict`），
  不会写进别的账套；两份同期账套时写的是本对话选定的那本，也不会静默选错。跨年度的票在这段代录对话里写不进（只能本次不录或另开对话）。

## 改了什么
- 共用代录开始题（`express_operator/guide_start.py`）：本人放进这次代录、票面不是任务月份的，每张一题「票面 X · 本对话 Y」——
  「放进本次代录照录（按票面日期）」/「本次不录」，不预选、不记住；答过照录的记进运行状态、不再问。开始前那张卡和代录对话里按菜单交的票都问。
- Cowork 引导卡在代录对话里交的文件，同资料区上传一样记进这次代录任务（记不上只记日志，不让交文件失败）。
- 代录卡自己的题（再录一次、结果不明、月份、别月）原来在卡片翻译之后才加进来，标题显示成单号、选项显示成码；现在一起翻。
- 词条四语三条（`guide.q.operator_other`、`guide.option.operator_other.follow / skip`）。
- 直写对话没有月份过滤（写入计划直写只按判定），只核了代码。

## 验证
- 离线：`test_cc_guide_documents`（照录照交、本次不录不交、答过不再问、按菜单交的也问、直写不问、题目经翻译）、`test_cowork_guide_documents`（代录对话记进任务、直写不记）。
- 页面流程（本机实验栈，电脑 + 手机）：新 `erp.cc.guide.operator-other-month` 过（上个月的收单记录：卡上先问别月题、点照录、交进代录任务一次）；
  回归 `erp.cc.guide.operator-purchase / -sales`、`cowork.cc.guide.operator-purchase`、`erp.cc.operator.intake`、`erp.cc.guide.purchase`、`cowork.cc.guide.sales` 见下表。
- 没测到：Cowork 别月票的页面流程（实验栈没有识别，引导卡交文件 → 识别那段走不了；记进任务由单测守）；跨年度票在真账套上停在期间不符（要测试机 Express）。

## 共用件连带回归

| 入口 | 结果 |
|---|---|
| `cc.operator.thread` | 通过（代录对话流程：ERP 采购 / 销售、Cowork 采购、ERP 别月票） |
| `cc.operator.start-card` | 通过（离线同名检查 + `erp.cc.operator.intake`） |
| `cc.assistant`、`shared.assistant`、`cowork.assistant`、`erp.cc.companies` | 通过（上面各条流程打开对话页、无页面报错） |
| `erp.cc.chat-push`、`erp.cc.stocktake`、`erp.cc.collection` | 通过（离线同名检查，闸门） |
| `control.assistant` | 没测到（原因：fatcc.com 入口不在本机实验栈；本分支没改登录与入口） |
| `cowork.cc-chat.push`、`cowork.cc.troubleshooting` | 没测到（原因：打字推送与自检本分支没改，只因引导共用件被列入） |
| `erp.page.collections` | 没测到（原因：网页收款页没改，只因共用词条表被列入） |

## 代码审查（code-review medium）

| 发现 | 真问题 | 修了 | 耗时 |
|---|---|---|---|
| 5 | 1（记任务失败会让已交识别的那份交文件报错） | 1 | 约 10 分钟 |

其余记下：别月题每次看卡都分页取一遍本月清单（性能，清单大时）；按 doc_ids 交进来、又不在任务里的别月单（例如待处理里别处来的）仍整批「请求无效」——
应改成这几张按留下处理、给原因，不整批抛错，另做；开始卡原有三类题从此按词条翻译（原有缺陷顺带修，流程已重跑）。

## 「方向不明的票按两个方向问」
随这条看了：MR.ERP `needs.needed(view, None)` 在方向没定时问四类菜单；推送只用一个方向。本分支没改，建议做法：方向题先答，再按方向问菜单（另做）。
