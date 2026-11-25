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

结果（11-25 cc-coverage-4，本机实验栈 pearnly-cov3-lab，电脑 / 手机）：四条 8 格全过（盘点、公司资料在 17c97b471 上过；看板、定时任务在本次修复后过）。

### 看板、定时任务两条当初没过的原因（cc-coverage-4 拿证据重查）

- 看板：**产品问题**。截图停在「列表」题、零个选项、提交灰着。看板一步是后台定下的（`rule: auto` + 默认看板），
  共用引擎给产品的 `ctx["answers"]` 只有用户亲手答的、没有后台定下的值，`list_options` 拿到的看板是空的 → `board_lists("")` 什么都没有，
  用户卡死（实验栈默认看板已有三列，库里核过）。按同类引导已有做法修（定时任务 `_book`、收款 `book_default`：答过的，否则默认值）：
  `work._board(ctx)`，列表、卡片、成员、标签选项都用它；单测替身改成「看板不对就没有列表」并断言列表选项与复核行列表名（修复前 2 条红）。
- 定时任务：**流程脚本问题**，两处。① `verify` 打印裸 `false`，`lab.python` 只认 JSON 对象 / 数组行，报「lab python failed」且报错为空
  （手机那格收尾扫到了本次建的对话与引导运行，说明备数与页面都跑过）；② 「删除」是第 7 项，在「显示更多」后面，通用作答片段没点开就找不到。
  改：核对打印对象；`cowork_cc_guide_form_lib.answer` 要选的项不在时先点「显示更多」（`cc-guide-more`，最多 5 次）再找。
- 看板流程收尾另补删自建看板的颜色标签（原先被 run.py 跳过 on_trash 强删、记警告）。

已知问题（记下没改）：共用引擎 `_ctx` 不带后台定下的值，产品各自写「答过的或默认」兜底（work / folder_task / collection 三处同形）；
引擎层统一要动 `guide/run.py`（共用件，连带全部引导），另开一件评估。

## 对照主会话五条

1. 结果卡用现有组件，回包没有的字段不显示 —— 是（form_result 只带回包里有的，测试 `test_guide_form_result`）。
2. 失败原因走 `guide/reasons.py` —— 是。
3. 回包约定写成「单据」「待处理」能并进来的形状，C3 登记 —— 见上。
4. 删除类写清删了几条；添加公司 · 本地的「同步中」在 C2 用 `state_text`。
5. 流程断言比对服务端应有状态 —— 见各流程 verify。
