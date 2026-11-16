# 需求影响评估：ERP 代录放票走通（H3 / H4 缺陷修复）

> 主会话 11-16 审定根因与修法（「可以做」），本页按模板补齐。任务记录与约定见 [2026-11-16-erp-operator-intake.md](2026-11-16-erp-operator-intake.md)。

## 0. 需求原文与理解

- 来源：11-16 真机 H3（被测头 f7d403580，e2eerp，ERP CC 代录选公司 Pearnly，放一张采购票说「录进 Express 69EXP\TEST」）：识别成了收单记录 LI-198706，开始前卡片作答 403，写入计划判「未找到」，CC 说没有上传功能。H4（销售放票）同因未测。
- 理解：ERP CC 代录里放进来的票（收单记录 `I:<记录>`）要能走完「开始前卡片作答 → 写入计划列入 → 开始 → 提交成 ERP 单 → 进写账队列」，与 Cowork 同一条路。
- 不在范围：H5（停下码 → 原因 + 卡片，CC 桌面版窗口在做）；引导式卡片（引导式窗口）；收款 `R:`、MR.ERP 代录（开关关着）；识别本身。

## 1. 现有同类与复用

- 已有：`express/operator.py` 的 `_intake_precheck`（卡片视图）、`apply_intake_answers`（答案写回记录）、`_submit_intake`（开录时提交）、`intake_search`；`express/precheck.py` 的 `save` / `save_answers` / `collect_form`。
- 结论：扩展现有，不新建。写账前置判定钩子按前缀分派，`I:` 复用上面这些函数。

## 2. Frappe 原生能力

- 不涉及界面新组件；服务端用 Frappe 现有的 `frappe.throw` / `log_error`。

## 3. 按类通用

- 类：「产品写账判定钩子认得的单据编号种类」。ERP 有 `P:`、`S:`、`R:`、`I:` 等；代录钩子与写账判定钩子必须认同一套（这次缺陷就是两套不一致）。
- 同类一起覆盖：ERP `check` / `remember` / `search` / `resume`、`operator.precheck` 对认不出的编号种类的处理；Cowork 的单据编号是记录名、没有前缀种类，找不到时是 DoesNotExist，不属于此类（不改）。
- 「找不到就报没有权限」：写账判定与代录接口里，**编号种类本产品不认**（契约缺口）单独记错误日志、回 `unknown_document`，与「不是本人的单」（照旧不说存在与否）分开。

## 4. 上下游数据

- ERP 内：收单记录在开录时用网页同一个入账函数提交成采购 / 销售单（`intake.submit_chat`），之后与网页建的单相同（库存、对账照旧）。卡片答案先写回收单记录（付款、各行物料、仓库），Express 那几项照旧记进公司选择。
- Express：载荷不变；开录后照 `push.send` 交代录。
- CC 桌面版：载荷不变，不升版本。
- Cowork：共用层 `express_operator.tools.status` 准备中时多回待录清单与上传识别状态，两产品同一套（Cowork 的待录清单本来就含放进来的票）。

## 5. 隔离与权限

- 收单记录只认本人放的、对话来的、这家公司的、本人有该方向开单权的（`operator._intake` 原判据不变）；不是本人的照旧不回、作答报没有权限。
- 待录清单经产品 `sources`（同写入计划），只给任务主人（`tasks.read` 已限本人）。

## 6. 入口

- CC 代录对话（网页 / 手机宽度）：本次修复的入口。
- CC 直写对话：不涉及（直写不收 `I:`）。
- 网页待处理卡片、LINE：不涉及（收单记录只来自 CC 代录；LINE 收单有自己的编辑页，提交后才是单据）。

## 7. 共用件与连带回归

`python3 scripts/check-impact.py --files apps/pearnly_erp_portal/pearnly_erp_portal/express/precheck.py apps/pearnly_erp_portal/pearnly_erp_portal/express/operator.py apps/pearnly_core/pearnly_core/express_operator/tools.py`：

- erp.cc.write（ERP 单据写 Express）
- cc.operator.page（代录会话）
- erp.cc.collection（ERP CC 收款）
- cc.operator.thread（代录对话工具）

逐个实测结果记在任务记录「连带回归」一节。

## 8. 存储与成本

- 不新增存储与外部调用；流程用实验栈假识别结果，不花识别费。

## 9. 删除、作废、追溯

- 不涉及：收单记录与提交出的单据的删除 / 作废照旧。

## 10. 文案与官方 UI

- 不新增、不改界面文案。`unknown_document` 只给模型与日志（写入计划的跳过原因同其他原因码，不经 `_()`）；作答时认不出的编号种类回已有的「Invalid request」。

## 11. 测试与验收

- 单测：`I:` 的 check / remember（付款、物料、仓库、Express 项）、认不出的种类报码与日志、准备中状态工具回待录与上传。
- 新流程 `erp.cc.operator.intake`：采购、销售各一格，修前红（403 / not_found）、修后绿，两次结果留证据。
- 真机 H3 / H4 复验由主会话排。

## 12. 要问用户 / 客户的点

无（修缺陷，口径沿用 11-08 代录放文件定案）。

## 13. 工作量与排期

1. 约定与评估（本页）→ 2. 新流程修前跑红 → 3. 四项修法 + 单测 → 4. 新流程转绿 → 5. 全闸门、候选离线、并集流程 → 6. 推送、CI。机器时间约 4 小时。
