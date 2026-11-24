# 需求影响评估：CC 引导式「单据维护」（总表第 14 行，四格 ○）

> 基线 main d8510e61a。只评估，不施工；交主会话审定。

## 0. 需求原文与理解

- 来源：用户 11-12「按全覆盖总表做完」、11-14「引导式为主、覆盖所有功能才算做完」；总表第 14 行「单据维护：改识别结果、录不录、删除、导出复核表」，
  Cowork 直写 / Cowork 代录 / ERP 直写 / ERP 代录四格都是 ○（与账本无关，一组一格）。
- 理解：在 CC 对话里点「单据」，不用打字就能对**本对话公司、本对话月份**的任意一张已交进来的单做这几件事：改票面、导出复核表、删除；
  「录不录」在判定卡上问（见第 3 节）。
- 不在范围：改已进账本的单（总表第 20 行，已定不做）；新建单据（采购 / 销售引导已有）；批量改。

## 1. 现有同类与复用

- **Cowork「待处理」引导已有同一套四个操作**（`pearnly_cowork/assistant/guided/pending.py`）：处理（交采购 / 销售单据引导，从判定卡接着走）、
  改票面（交单据引导，`at="review"`，复核卡就地改）、导出复核工作簿（转调 `tools.export_review_workbook`）、删除（允许卡 + 转调
  `tools.delete_documents`，已推送的跳过）。只是清单只取「待处理」那些。
- ERP「待处理」（`express/cc_guide_pending.py`）只有「处理」：11-23 主会话定 ERP 票面回单据页改（复核行对 P: / S: 只读），没有导出 / 删除。
- 打字工具：Cowork 有 `cowork_edit_document`、`cowork_choose_document_policy`、`cowork_delete_documents`、`cowork_export_review_workbook`、
  `cowork_list_documents`；**ERP 一个都没有**（只有 `erp_list_documents` / `erp_document_detail` / `erp_push_documents`），所以 ERP 两格
  其实是「打字也办不了」，总表标 ○ 偏高，施工同提交改正。
- ERP 网页有：`purchasing.api.api_update_purchase`、`api_delete_purchase_drafts`、`api_purchase_export`，销售同名三个。
- **结论：扩展现有，不新建。** 把待处理模块改成按「清单来源」参数化（待处理 / 本对话公司本月全部单据），同一组 draft / review / confirm /
  result 函数，两条声明（`pending`、`documents`）。不另写第二套。

## 2. Frappe 原生能力

- 不涉及新界面：用 CC 引导卡（已有的题卡、复核卡、允许卡、结果卡）。清单搜索走服务端分页（`list_documents` / ERP 列表同一个查询，
  `limit_start` / `limit_page_length`），不一次取全。

## 3. 按类通用

- 类：「CC 里对已交进来的单做维护」。同类入口：待处理引导（同模块）、单据页、Cowork 录入工作台列表。
- 「录不录」不单独做操作：Cowork 的录不录是按单据种类整个工作区记一次（转账凭条 / 预扣税凭证，`document_policy`），已在采购 / 销售判定卡上
  出题（`precheck_source._policy_item`）；单据维护里的「处理」交给判定卡就覆盖它。ERP 没有录不录这个概念（见第 12 节问题 2）。
- 顺带：同一处给 `pending` 声明加 `operator` 模式，总表第 8 行两格代录 ○（Cowork / ERP 代录 · 本地）一起做掉；给两份声明补**结果步骤**，
  待处理四格 ◐ 的「没有结果步骤」一并补上（之后各配页面流程即可 ✅）。

## 4. 上下游数据

- 改票面：只改 Pearnly 里的识别结果；已进账本的不改、不重推（同 `cowork_edit_document` 说明）。ERP 走单据页同一个保存函数（若做，见问题 1）。
- 删除：Cowork 真删记录与原件（轻便工具口径，已推送的跳过）；ERP 只删草稿（同网页 `api_delete_*_drafts`），已提交 / 已写账的跳过。
- 导出：Cowork 复核工作簿；ERP 用采购 / 销售列表导出同一个函数。文件放进本对话。
- CC 桌面版：不涉及，载荷不变。代录模式下「处理」交给代录的单据引导，写入计划与代录任务照旧。
- **代录的月份口径（11-24 实测）**：代录任务交单只收这家公司本月（按发票日期）的待录单，外加本人放进这个任务的别月单
  （`express_operator.api.waiting` / Cowork `operator_source.sources`）。单据维护与待处理的清单不按月份限（待处理本来就不限），代录对话里
  选了别月的一张直接交过去，确定时会被拒「请求无效」、结果卡只写「未完成」（Cowork 代录页面流程用 6 月票在 11 月代录对话里四格都这样）。
  施工时同一处处理：代录模式下清单只列本月的单与这个任务已放进来的单，或「处理」先把这张放进任务再交（选哪种在施工前定，倾向前者，同代录开始卡口径）。
- 共用层：`pearnly_core.assistant.guide`（结果步骤、模式声明）不改契约，只多一份声明。

## 5. 隔离与权限

- 清单按本对话公司 + 月份（服务端过滤，同 `_items` 现做法）；菜单出不出按现有 `allowed`（Cowork `scope.require("intake")`；ERP 采购 / 销售能力）。
- 删除、导出、改各自调网页同一个函数，权限由那个函数拦（不靠卡片隐藏）；删除走允许卡（`consent`）。

## 6. 入口

- CC 网页 / 手机宽度 / CC 桌面版里的对话：引导卡同一套。LINE、拍照：不涉及。

## 7. 共用件与连带回归

- 动到的是两产品各自的引导模块与声明，不动登记共用件；施工时跑 `scripts/check-impact.py --files ...` 贴结果。
- 受影响流程：`erp.cc.guide.pending`、Cowork 待处理（若已有）、单据引导几条（handover 路径）。

## 8. 存储与成本

- 无新增存储、无识别调用、无固定月费。导出文件进对话附件（已有）。

## 9. 删除、作废、追溯

- 删除即真删（同现有）；已推送 / 已写账的跳过并在结果卡写明几张跳过。不做作废。

## 10. 文案与官方 UI

- 新增词条（四语）：菜单「单据」、开场一句、清单题「哪一张」（可复用待处理已有的 `guide.step.pending.*`、`guide.option.pending.*`）。
  原则：能复用就复用，不改已有文案；新词条过 `scripts/check-copy.py`。图标 SVG（沿用 lucide `file-text`）。

## 11. 测试与验收

- 离线单测：清单来源两种、四个操作的 confirm / result、删除跳过已推送、ERP 只删草稿、权限拒绝。
- 页面流程（实验栈电脑 + 手机）：Cowork 直写、Cowork 代录、ERP 直写、ERP 代录各一条（点「单据」→ 选操作 → 选单 → 复核 / 允许卡 → 结果）；
  待处理四格各补一条（同模块）。总表同提交改。

## 12. 主会话定的口径（11-24）

| # | 问题 | 定了 |
| --- | --- | --- |
| 1 | ERP 改票面 | **要放进 CC**（11-08「CC = 网页功能 + 代录」；11-23「待处理复核只读」只是当批范围）。排第二批、单独分支，不挡本批；本批 ERP 两格没有改票面就标 ◐，依据写明差这一项 |
| 2 | ERP 录不录 | 不做（网页本来就没有） |
| 3 | 清单范围 | 公司与月份都取**对话选择器当前选的**（选择器是权威输入），不写死本月 |
| 4 | 删除规则 | 不另立：与网页同一个动作完全一致（网页能删的照删，网页不许的跳过，结果卡说跳过几张、为什么）；删除走允许卡（11-12） |
| 5 | 代录月份 | 代录模式下清单只列对话月份的单与本任务已放进来的单（第一批）；别月单的处理随「代录月份」那条（主会话 (b)）统一 |

施工前核实的网页现状（11-24）：
- Cowork：录入列表勾选「删除」= `history.removal.delete`（`public/desk/intake.js` 调它），**只跳过正在推送的**，已推送的照删；
  CC 工具 `cowork_delete_documents` 调同一个函数（工具说明里「已推送的跳过」与代码不符，以代码与网页为准，同批改说明）。
  结果文案用列表已有的 `intake.delete.done / partial / none`。
- ERP：网页只有删草稿（`purchasing.api.api_delete_purchase_drafts`、`sales.api.api_delete_sale_drafts`），已提交的不能删；
  CC 收单记录（``I:``）在网页上的删除入口施工时再核，没有就不在 CC 里开。

## 13. 工作量与排期（机器耗时）

1. 共用：待处理模块按清单来源参数化 + 结果步骤（两产品各一处）：3–4 小时
2. 声明：Cowork / ERP 各加 `documents`，`pending` 加 operator；ERP 接导出、删草稿：3–4 小时
3. 单测 + 页面流程 8 条（单据 4 + 待处理 4，电脑 / 手机）：5–6 小时，实验栈跑约 2 小时
4. 总表 `--write`、`tests/test_cc_coverage.py` 期望数、闸门三步：1 小时

合计约 12–15 小时。预期总表：单据维护 4 格 ○ → ✅；待处理代录 · 本地 2 格 ○ → ✅；待处理直写 · 本地 2 格 ◐ → ✅；
MR.ERP 那几格随后按 MR.ERP 页面流程另算。

## 14. 共用件连带回归（`tools.py`，11-24 变基后）

命中的共用件：无
只改到调用方的入口：`cowork.cc-chat.push`、`cowork.express.push`、`cowork.cc.mrerp-setup`、`cowork.ocr`、`cowork.execution`、`cowork.assistant`、`cowork.tax-documents`

要实测的入口 7 个（每行填「通过 / 不通过 / 没测到（原因）」，不能用推断代替）：

| 入口 | 产品 | 位置 | 怎么点 | 预期 | 方式 | 结果 |
|---|---|---|---|---|---|---|
| `cowork.cc-chat.push` | Cowork | CC 对话页推送（让 CC 推、对话末尾的「需要处理」框、公司工具改选择） | CC 对话页（Cowork）上传单据后让 CC 推送 / 对话末尾的「需要处理」框推送 / 让 CC 改记住的选择 | 照常写进 Express、读回一致；CC 说的与写入的一致 | manual | 没测到（本分支对 tools.py 只改了 `cowork_delete_documents` 一句给模型看的工具说明，函数与返回不变；离线 `check-cc-tools`、`test_cowork_pending_guide` 守住；实测要真识别或真 Express 推送，按本改动不必要，交主会话定是否补） |
| `cowork.express.push` | Cowork | Cowork 推送 Express（全部类型） | 录入工作台 / 识别记录 → 推送：采购、销售、库存、存货科目组、付款、预检、回执；自动推送规则；POS 日汇总（导入一份含当天全退那一天的收银导出） | 照常写进 Express、读回一致。服务类进项票由自动冒烟 cowork_express_flow.py 写进测试机 C 盘 T 账套并核单号与 CC 读回；采购库存、销售、付款、POS 日汇总等其余类型仍人工逐类型推；POS 导入里一天全退（全部为 0）的那天不建票、其余天照建（10-25 起，原来整份报错） | auto：cowork_express | 没测到（本分支对 tools.py 只改了 `cowork_delete_documents` 一句给模型看的工具说明，函数与返回不变；离线 `check-cc-tools`、`test_cowork_pending_guide` 守住；实测要真识别或真 Express 推送，按本改动不必要，交主会话定是否补） |
| `cowork.cc.mrerp-setup` | Cowork | CC 对话里 MR.ERP 设置（推之前要选的导入菜单 / 默认值、MR.ERP 设置工具、待处理当场选） | CC 对话页（Cowork）让 CC 推一张 MR.ERP 单 / 改 MR.ERP 设置；「公司 · 月份」对话末尾的「需要处理」框里点停在要选菜单的单当场选 | 只问这张单方向会用到、还没选的；选完照常重推 | manual | 没测到（本分支对 tools.py 只改了 `cowork_delete_documents` 一句给模型看的工具说明，函数与返回不变；离线 `check-cc-tools`、`test_cowork_pending_guide` 守住；实测要真识别或真 Express 推送，按本改动不必要，交主会话定是否补） |
| `cowork.ocr` | Cowork | Cowork 录入识别 / 远端识别 / 对账识别 | 录入工作台上传识别；对账中心单据识别（付费识别，按小样本） | 识别成功、结果入库 | manual | 没测到（本分支对 tools.py 只改了 `cowork_delete_documents` 一句给模型看的工具说明，函数与返回不变；离线 `check-cc-tools`、`test_cowork_pending_guide` 守住；实测要真识别或真 Express 推送，按本改动不必要，交主会话定是否补） |
| `cowork.execution` | Cowork | Cowork 录入、对账、文件夹录入、CC 对话工具 | 录入工作台上传；对账上传；文件夹录入一轮；CC 对话里推送（付费识别，小样本） | 排队、识别、推送照常 | manual | 没测到（本分支对 tools.py 只改了 `cowork_delete_documents` 一句给模型看的工具说明，函数与返回不变；离线 `check-cc-tools`、`test_cowork_pending_guide` 守住；实测要真识别或真 Express 推送，按本改动不必要，交主会话定是否补） |
| `cowork.assistant` | Cowork | Cowork 的 CC 工具与公司页 | CC 对话里让 CC 推送 / 查待处理 / 改记住的选择；公司页 | 工具结果正确、说的与做的一致 | manual | 没测到（本分支对 tools.py 只改了 `cowork_delete_documents` 一句给模型看的工具说明，函数与返回不变；离线 `check-cc-tools`、`test_cowork_pending_guide` 守住；实测要真识别或真 Express 推送，按本改动不必要，交主会话定是否补） |
| `cowork.tax-documents` | Cowork | Cowork 识别、推送、报税草稿 | 录入识别；推送；报税（P.N.D.3 / 53）草稿 | 照常 | manual | 没测到（本分支对 tools.py 只改了 `cowork_delete_documents` 一句给模型看的工具说明，函数与返回不变；离线 `check-cc-tools`、`test_cowork_pending_guide` 守住；实测要真识别或真 Express 推送，按本改动不必要，交主会话定是否补） |
