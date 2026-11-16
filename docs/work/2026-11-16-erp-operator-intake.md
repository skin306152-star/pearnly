# ERP 代录放票走通（H3 / H4，2026-11-16）

分支 `pearnly/erp-operator-intake`，基线 origin/main 290bc368b。评估：[2026-11-16-erp-operator-intake-评估.md](2026-11-16-erp-operator-intake-评估.md)。

## 根因

ERP 代录里放进来的票识别后是收单记录 `I:<记录>`，开录时才提交成 ERP 单。11-08（cb66cbb44）只把 `I:` 接进代录钩子（sources / precheck / search / submit，答案随开始交）。
11-11（33817b8bf）改成「卡片答案走 remember、开始只走写入计划 check → hand_in（不带答案）」后，ERP 写账判定钩子 `express/precheck.py` 的 check / remember 从没认过 `I:`：
remember 抛「没有权限」（H3 的 403），check 静默丢掉（写入计划判 not_found），开录时也不再有答案写回记录。CC 准备中查任务只得到「还没录」，看不到放进来的票。

## 约定（与引导式窗口、CC 桌面版窗口共用，别各写各的）

1. **ERP 单据编号按前缀分派，一处解析**：`express/precheck.py` 的 `target_of(company, doc_id)` 是写账判定（check / remember / search / resume）唯一的解析入口，回
   `("document", (doctype, 名))`、`("intake", 收单记录)`、`("collection", doc_id)`、`("missing", None)`（不是本人的 / 找不到，照旧不说存在与否）、
   `("unknown", None)`（本产品不认的编号种类）。代录钩子（`operator.py`）对 `P:` / `S:` / `I:` 用同一套判据。新加一种编号要在这里登记，不在各函数里各判一次。
2. **收单记录 `I:` 是写账判定里的一等单据**：check 回的卡片视图与代录预检同一份（`operator.intake_view`，带 `direction`，引导卡按方向比对可直接用）；
   remember 先把收单记录自己的几项（付款 `erp-intake-pay:`、行物料 / 仓库 `erp-intake:<记录>…`）写回记录，其余 Express 项按它将成为的单据类型核值、记下；
   记过以后这条记录的付款项来源是 `remembered`（已定），不再是预选。已提交的 `I:` 按提交出的单据判。
3. **待录清单给模型**：共用层 `express_operator_status` 在任务准备中（还没开始）时回 `waiting`（这次代录待录的单：编号、类型、日期、对方、票号、合计，别的月份的带 `other_month`，同写入计划用的产品 `sources`）
   与 `uploads`（任务里放进来的每一份：文件名、识别状态、识别出的单），两产品同一套。CC 据此知道票已放进来、走写入计划，不叫用户换入口。
4. **认不出的编号种类单独报码**：写账判定 check 对 `unknown` 回 `{state: not_writable, reason: "unknown_document"}` 并记错误日志
   （标题 `Write precheck: unknown document kind`）；remember / search 对 `unknown` 记同一条日志、回「Invalid request」；`missing` 照旧「没有权限」/ 不回。
   停下码、原因码都是给模型与日志的，不新增界面文案。H5 的「停下码 → 原因 + 卡片」若要给 `unknown_document` 配话，走 `reason_text` 那条，不在这里加。

## 证据

（施工中补：新流程修前红、修后绿；全闸门、候选离线、并集流程、CI）

## 连带回归（check-impact）

| 入口 | 结果 |
|---|---|
| erp.cc.write | 待测 |
| cc.operator.page | 待测 |
| erp.cc.collection | 待测 |
| cc.operator.thread | 待测 |
