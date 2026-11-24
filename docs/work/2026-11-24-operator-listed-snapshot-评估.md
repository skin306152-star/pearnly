# 需求影响评估：代录列出的单被直写写掉后如实显示「已录入」（不再时有时无）

## 0. 需求原文与理解
- 主会话 11-24（转 L3 首跑问题 1）：口径「列出阶段不锁；点开始确认即占用，直写不得再碰；已被直写写掉的单在卡上如实显示已录入，不许时有时无」。
  修法批准：代录任务列出时把单号快照进任务，按快照逐张读现状；已被直写的显示「已录入 · 单号」并在开始时跳过，sources 只补新单；Cowork / ERP 同一套。
- 现状（已核）：点开始后有三道（行锁 + ledger_job + operator_owned），不会重复入账；差在列出后被直写写掉的单从 `products.sources` 现查结果里消失。
- 不在范围：不加列出阶段的锁；不改直写 poll；不改开始后的占用；不改 Cowork / ERP 的 sources 判据。

## 1. 现有同类与复用
- `express_operator.api.waiting` 已有 skipped 原因 `already_entered`（同票已写进 Express，写入计划 `SKIP_ENTERED`、stop_exits 已有说法）→ 复用这个原因码。
- `products.status(company, doc_ids)`（两产品都声明）回 saved / queued / not_done + docnum → 复用读现状。
- 状态词复用 feed `STATE_KEY`「saved」（已录入）。

## 2. Frappe 原生能力
- 任务 DocType `Pearnly Express Operator Task` 加一个隐藏 Long Text 字段 `listed`（JSON：{单号: 分组}，封顶 500 条；新字段默认空，原生迁移，不需要 patch）。

## 3. 按类通用
- 共用层一处：`express_operator`（tools._preparing、api.waiting、write_plan 取单）；Cowork 与 ERP 走同一套，不各写一份。

## 4. 上下游数据
- 只读产品现状（status），不写单据、不排写账件；载荷不变，CC 桌面版不变。

## 5. 隔离与权限
- 快照只存本任务、本人；读现状走产品 `status`（各产品自己的权限判据）。

## 6. 入口
- CC 代录对话（网页 / 手机 / CC 桌面版同一个 /assistant 前端）：
  - 资料清单卡（`frontend/assistant/.../operator-steps.tsx` OperatorSourcesStep）与开始卡（`operator-start.tsx`）现在都按 `api.documents(company, period)`
    现查，不带任务号——**要在卡上显示「已入账 · 单号」必须改前端**：`documents` 带上 `task`，服务端把快照里已被直写的单作为一行（`entered: 单号`、不可拖）补进
    第一页；DocItem 照已有状态词 `op.doc.saved`（已入账）显示。前端改动要重建 SPA（实时源码盖不到，验证用镜像或 `PEARNLY_TESTENV_LOCAL_SPA=1`）。
  - 模型侧：express_operator_status 准备中多一个 `entered` 列表（给模型的英文说明，不是界面文案）；写入计划 skipped 带 `already_entered` + 单号；开始时跳过。
  - 网页 ERP / Cowork 单据页、LINE 不涉及。

## 7. 共用件与连带回归
- 改 `pearnly_core.express_operator` 与 `assistant.write_plan`：跑 check-impact，按清单逐入口实测；候选离线检查（共用层）。

## 8. 存储与成本
- 每个任务一列 JSON（单号列表，封顶 SOURCES_MAX=500 条）；无外部调用、无费用。

## 9. 删除、作废、追溯
- 随任务删；快照不影响单据作废。

## 10. 文案
- 沿用已有状态词与 already_entered 说法；若写入计划 / 准备中要新句子，四语直接写并过 check-copy；改已有文案先列清单。

## 11. 验收
- 离线：tools._preparing / api.waiting / write_plan 三处单测（列出 → 被直写 → entered 带单号、开始跳过；新单补进；status 读不到不丢）。
- L3：`acc.erp.operator.talk.sales` 改成「列出 → 等直写写掉 → 卡上显示已录入 · 单号、写入计划跳过」，Mac 栈 + 测试机实测；code-review medium。

## 12. 待问
- 无（口径主会话已定）。
