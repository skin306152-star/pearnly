# documents 源出题进对话口径（11-23）

## 现象与根因

- 现象（实验栈，Cowork 直写销售）：交进来的销售票在引导卡上判「可写」、不出题，直接到复核；点「立即录入」后结果「已跳过」，票上原因
  `choice_needed:payment,account`（付款方式、收入科目）。
- 根因：`assistant/guided.py` 约定「出题、记答案、开始前判定、写账四步必须同一口径（在对话里 = 引导口径：没点过的预选不生效、判不出的出题）」。
  documents 源的出题与开始前核单是页面接口（`guide_view` / `guide_start` 等），不是工具调用，没进对话，判定走网页口径（规则默认的付款、
  收入科目直接算定）；确定后推送在对话里（`guide_documents.confirm` 带对话）走引导口径 → 要人选 → 跳过。
  同一张票同一账套实测：不带对话 `needed=[]`，带对话 `needed=[payment, account]`。

## 修法（主会话 11-23 批准）

- 共用层一处：`pearnly_core.assistant.guide.documents` 的 `view / answer / edit / confirm / result / search` 用 `in_conversation` 包一层，
  在 `guided.within(ctx["session"])` 里做；`card_for` 出题、`check_handed`（加可选 `session`，`run.start` 传这次的对话）同样在对话里判。
  出了这几步还原，不串到别的请求。两产品（Cowork、ERP）、直写与代录同一处生效。
- 不改 `express/guide_documents.py`、不改 `documents.confirm` 签名。
- 同类核查：第 1 期打字开卡 `guide_tool` 本来就在工具调用里（对话里）；代录开始卡 `express_operator.guide_start` 经 `run.start` / `stage`，
  现在一并在对话里；LINE、网页、定时任务不经 documents 源，不变。

## 守门测试（`tests/test_cc_guide_documents.py` `SameConversation`，修前两条均红）

1. 页面驱动的一次引导：开始前核单、出题、记答案、复核、确定（判定、写入计划、产品写入）每一步调判定时都在这次引导的对话里，出来还原。
2. 卡上判「可写」那一次与确定后写入那一次是同一口径（同一段对话）。

## 页面流程与总表

- 本分支只交共用层修复与守门测试（先于叠车合入，ERP 直写待处理等引导依赖它）。
- 本机实验栈（叠车之上的源码）：Cowork 直写销售新流程修前卡上不出题、确定后被跳过，修后电脑 / 手机两格过；Cowork 采购、第 1 期引导卡仍过。
  ERP 受影响流程以 GitHub 干净实验栈的 `pearnly/flows` 为准（本机栈 ERP 站点被一次中途停掉的运行弄脏，换回 main 代码同样不过，正在重建）。
- 续（11-23，基 main 2e006a7bc）：新流程 `cowork.cc.guide.sales`（同 `cowork_cc_guide_docs_lib`，销售什么都不记）：题卡问收入科目、付款方式 → 复核 → 立即录入 → 结果已交出，本机实验栈电脑 / 手机两格通过；总表「销售 · Cowork 直写 · 本地」half → done（✅6 → ✅7）。
