# 给 CC 的账套期间只写本年段（2026-11-16）

分支 `pearnly/operator-period-source`，基线 origin/main `9e0e102ab`。来源：真机记录 f7d403580 待判第 9 条（NLTTEST）。

## 现象与原因
收款页显示 NLTTEST 会计期间 2024-01-01–2025-12-31，ERP 代录会话里 CC 却说「会计期间是 2024-01-01 至 2024-12-31」。

- 服务端判据本来一致：代录 / 直写（`express.target.pick` → `account_match.conflict`）与收款（`collection.service.periods`）都用
  CC 桌面版报的 Express ISPRD「本年起 ~ 次年止」（`period_start`..`next_end`）。2026-11-14 的单被跳过是对的（Express 原生也拒，Message#70）。
- 错在给 CC 的公司说明（提示词）：ERP `masters/cc_context._set_line` 与 Cowork `companies/context._set_line` 每本账套只写
  `period_start..period_end`（本年段），CC 照着说。ERP `cc_service.covering` 也只认本年段（Cowork 早已含次年段）。
- 月份选择器不按期间限制月份，不涉及。

## 改动（提示词改动，改前改后原句）
账套行里的期间（两处同一写法），以 NLTTEST 为例：

- 改前：`- "9.บริษัท นิว ลิงค์ (ไทยแลนด์) จำกัด" — account_set_key "…", 2024-01-01..2024-12-31`
- 改后：`- "9.บริษัท นิว ลิงค์ (ไทยแลนด์) จำกัด" — account_set_key "…", 2024-01-01..2025-12-31`

只改日期段的止日（没报次年段的账套照旧写本年止），句子其余部分不变。ERP `covering` 改用 `account_match.for_month`
（与写账按日期选账套、Cowork 主档页同一条：本年段优先，新年度账套没建时用次年段）。

## 验证
- 新测试：`test_erp_cc_companies`（两年期账套下 2025-06 的说明写 2024-01-01..2025-12-31、`covering` 认这本；新年度账套已建就用新的；
  月份写错回空）；`test_cowork_companies`（次年段写进说明）。
- 文案闸门（提交钩子）：新增 0 · 改动 0。
- 全闸门、候选离线、流程：见提交说明 / 主会话汇报。
