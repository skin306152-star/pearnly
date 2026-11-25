# ERP 版 CC「单据」引导（总表第 14 行单据维护 · ERP 两格，2026-11-25）

分支 `pearnly/cc-erp-documents`，基 main 4eb0b2499。评估：`docs/work/2026-11-24-cc-guided-documents-评估.md`（主会话 11-24 审定，导出选 A）。

## 做了什么

- 声明 `express/cc_guide_pending.py` 加 `documents`（直写 + 代录、三种账本，与 Cowork 同业务 id、菜单字、图标、开场句键 `guide.open.documents`）。
- 产品函数 `express/guide_pending.py` 与待处理同一组（按 ctx 的业务分）：先选操作（处理 / 导出 / 删除），再选方向（采购 / 销售）。
  - 清单：对话选择器选的公司 + 月份，这个方向的全部单据；转调网页列表同一个查询 `purchasing.records.rows` / `sales.records.rows`
    （本人范围、本公司、按单据日期按月、服务端搜索翻页），在本对话公司下执行 `masters.company.acting`（收发存引导同款），网页查询不改；
    销售不列收银小票（不是 `S:` 单）。选过的码按同一份条件再查回，不在这家这个月的不回。
  - 处理：交采购 / 销售的按单据引导（同待处理）。
  - 导出：网页导出同一个工作簿函数（`purchasing.output.workbook` / `sales.output.workbook`），这家公司这个月这个方向整张列表，文件放进对话。
    评估 A 原写「给 records.query 加指定公司参数」；实际用 `acting` 达到同一效果（先核本人能用这家公司，否则拒），网页默认不变且不用改查询。
  - 删除：允许卡（只有「允许」）；网页同一个删草稿函数（`entry.delete_drafts`，整批全是草稿才删），先按同一份条件筛出草稿交给它，
    其余跳过，结果卡带网页原句「只能删除草稿单据」；一张没删标「已跳过」。
- Cowork 单据声明的开场句键从 `guide.open.pending` 换成 `guide.open.documents`（文字同为「选择操作。」；ERP 的 `guide.open.pending` 是「选择单据。」，
  同业务 id 两产品开场键须一致）。
- 没做：ERP 改票面进 CC（主会话 11-24 定第二批，单独分支），所以 ERP 两格 ◐。录不录 ERP 不做（网页没有）。

## 新增文案（四语，ERP 术语表）

guide.business.documents 单据 · guide.open.documents 选择操作。 · guide.step.pending.action 操作 · guide.step.pending.items 单据 ·
guide.option.pending.handle 处理 · guide.consent.documents.delete 要删除单据吗？（均沿用 Cowork 同键原文）；Cowork 加 guide.open.documents。

## 验证

- 离线：`tests/test_erp_pending_guide.py`（12 条：清单按公司 / 月份 / 方向、搜索翻页、别家公司拒、处理交单据引导、导出整月列表进对话、
  删除走允许卡只删草稿、一张没删已跳过）；`check-cc-guides`、`check-cc-coverage`、`check-copy`、`check-architecture`、`check-unbounded-queries`。
- 页面流程（本机实验栈 pearnly-cov3-lab，电脑 / 手机）：`erp.cc.guide.documents`（导出）、`erp.cc.guide.documents-delete`（删已提交的单 → 已跳过）。结果见下。
- 共用件：`check-impact` 没有改到登记的共用件。

## 页面流程结果

（跑完补）
