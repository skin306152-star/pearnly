# cc-guided-* 分支排车合入（11-22 起，cc-coverage-recount 窗口）

清点与判定见共享笔记 `cc-guided-分支清点-11-22.md`（主会话 11-22 审定：车 1→5 逐车交、folder-task 车 6、stock-entries 车 7 排在库存 K1 之后）。
每车：从最新 origin/main 拣提交、按并集解登记类冲突、按 main 现行引擎口径改、同提交改 `scripts/impact/cc-coverage.toml`、全闸门 + 候选 + 受影响流程 + 公开扫描、GitHub 绿后交主会话。

## 车 1：增值税 / POS / 预扣税（分支 pearnly/cc-guided-tax-pos）

- 来源：cc-guided-vat → pos → wht → wht-review 的 7 个功能提交（不带 1b819808d 等待中的页面流程，归第 2 步）、cc-guided-p3-choice-points 的 4 个文档提交、
  cc-guided-master-table 里 readback / recon / vat / pos / wht 五份影响评估（stocktake 那份以车 2 分支里更新的为准）。
- 按 main 口径改：引导拒收是 `{格: {code, text}}`（main 8172605d2），三份单测改成只比码（同 test_erp_collection_guide 的写法）；
  三份声明都没有结果步骤，总表按规则标 ◐ 不标 ✅。产品拒收码的四语说法（`guide.refused.<码>`）没有补：现有 ERP 收款、Cowork 对账同样没有，属另一件事。
- 没带 067c6ecb9（预扣税草稿工具单独拒别家税号）：main 的 A 线 11-15 已按类做了「对象不在本对话公司名下就拒」（覆盖全部写工具，含预扣税草稿），
  这条单独的拒绝抢在通用拒绝前、回另一种形状，让 main 的 test_cowork_assistant_tools 公司范围那条红；以 main 的通用规则为准。
- 总表：POS 日汇总 · Cowork 直写本地、增值税 · Cowork 直写本地、预扣税 · Cowork 直写 ○→◐；范围内 81 格 ✅ 1 ◐ 14 ○ 22 ✗ 4。
