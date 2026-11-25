# C 堆：表单引导的结果步骤（C1 = 共用函数 + Cowork 4 格，2026-11-25）

分支 `pearnly/cc-form-result`，基 `pearnly/cc-status-words`（含 cc-guide-reason-text 的共用原因表 `guide/reasons.py`）。
方案主会话 11-25 批（C1 → C2 → C3），五条要求见下「对照」。

## 做了什么

- 共用层新增 `pearnly_core/assistant/guide/form_result.py`：表单引导（`source: form`）的结果步骤，只照确定回包画结果卡，回包没有的字段不显示。
  回包约定：`status`（entered 成功 / held 已跳过 / failed 失败）、`label`（给人看的名字）、`files`、`deleted` / `skipped` / `skip_reason(_text)`、
  `reason` / `reason_text`（码走 `guide.reasons`，不另写映射、不露 Error Log 编号）、`state_text`（办成后的事实状态，如「同步中」）、
  `next` / `marks` / `questions` / `wait` / `total` 原样带过去；产品其余字段原样留在结果里（页面不画）。结果卡用现有组件（`GuideResultCard`），不新做样式。
- Cowork 四份声明加 `"result": "pearnly_core.assistant.guide.form_result.result"`：盘点、公司资料、任务看板、定时任务。
  确定回包补 `label`，不再把内部编号当名字：盘点 → 盘点名（新建时用填的名字）；公司资料 → 那条备注 / 规则的名字（加备注用正文），删除类带 `deleted: 1`；
  看板 → 卡片标题；定时任务原本就是任务名，删除带条数。
- 删除类结果写清删了几条（`intake.delete.done`），删除前的允许卡不变（只有「允许」）。

## C3（排在 C2 之后）

「单据」「待处理」两份现有 result（`pearnly_cowork.assistant.guided.pending.result`、`pearnly_erp_portal.express.guide_pending.result`）回的是同一形状
（docnum / files / deleted / skipped / reason_text / next），C3 把它们并进 `form_result`（状态从 ready 统一成 entered，页面状态词「成功」），不留两套。

## 页面流程（本机实验栈，电脑 / 手机）

- `cowork.cc.guide.stocktake`：下载模板 → 结果卡成功带 stocktake-template.xlsx；对话里服务端那份同名。
- `cowork.cc.guide.company`：添加备注 → 结果卡成功带正文；公司备注里真有这一条。
- `cowork.cc.guide.work`：添加卡片 → 结果卡成功带标题；库里这张卡标题一致、只一张。
- `cowork.cc.guide.folder-task`：删除一条定时任务 → 允许 → 结果卡已删除 1 条；任务表里这条真没了。
  「立即运行」在实验栈只能验到「已排上」（没有 CC 桌面版来领），不在流程里。

结果：（跑完补）

## 对照主会话五条

1. 结果卡用现有组件，回包没有的字段不显示 —— 是（form_result 只带回包里有的，测试 `test_guide_form_result`）。
2. 失败原因走 `guide/reasons.py` —— 是。
3. 回包约定写成「单据」「待处理」能并进来的形状，C3 登记 —— 见上。
4. 删除类写清删了几条；添加公司 · 本地的「同步中」在 C2 用 `state_text`。
5. 流程断言比对服务端应有状态 —— 见各流程 verify。
