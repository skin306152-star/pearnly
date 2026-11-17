# 记下的答案所有口径都用（C，主会话 11-17 定，部署前条件）

## 问题
对话里（引导口径）答过的题，单据停下后被后台 / 网页再准备（`push.poll`、提交后排队、网页「再写一次」、单据上作答、待处理重推、
收银日汇总 / 出入库 / 盘点同一条 `send`；Cowork 同一个 `choices.resolve`）时按网页口径判，共用规则 `choices.resolve` 有三处
「整个分支只在 guided 才走」，连「记过就用」也一起关掉了 → 落连接默认值 = 记错科目。

## 做法（C）：口径只管问不问，不管已答的用不用
`apps/pearnly_core/pearnly_core/express_rules/push/choices.py` `resolve`：
1. 销项收入科目（A14，键 `account|revenue`，按账套）：记过且仍在这本账套科目表里 → 所有口径都用；没记过：引导口径问，别的口径照旧落连接默认。
2. 销项现销 / 赊销（A12，键 `payment|<客户键>`）：记过 → 所有口径都用；没记过：引导口径问，别的口径照旧交 mapper。
3. 销项服务品进项税口径（键 `service_vat|<账套>`）：记过 → 所有口径都用；没记过只在引导口径问。
进项那几题（付款、小票税额、服务品、仓位、A9 同名对方）本来就是「记过就用、口径只管问」，不改。
失效：读记忆照旧先核「值还在此刻的选项里」（科目表 / 选项），主档删改了当没记过。Cowork 走同一个 `resolve`（读账套记忆），一并生效。

## 线上可见的行为变化（列入部署说明）
**网页、LINE、定时任务 / 文件夹推送、后台重试从此也会用 CC 里记住的：销项收入科目、销项现销赊销、销项服务品进项税口径**（以前一律落连接默认 / 交 mapper 按票面）。这是「问一次按公司记住」的本意。
验收加一格：CC 对话里给某客户 / 账套记住收入科目（或现赊）→ 网页推同一对方的销售票用记住的值；在「记住的选择」里删掉 → 回默认。

## 守门
`tests/test_express_rules_guided.py::GuidedOnlyGatesAsking`：`resolve` 里条件带 `guided` 的分支里不许读记忆（`chosen(`）。改前报两处（销项现赊、收入科目）。

## 先红后绿
`tests/test_express_rules_guided.py::RememberedEveryBasis`：网页口径下记过的收入科目 / 现赊 / 服务品口径照用；改前 3 failures，改后过。

## A7 明细对不上（主会话 11-17 列为部署前条件，同批交；载荷一半是 guided-erp 的 29e6ec3c6，已摘入）
- 缺陷：`resolve` 判到明细对不上时永远出题、从不读已答的值，「只记金额」也没落进载荷——引导口径里答完写账又停在「要人选」，这类单永远写不成。
- 存法：`detail` 答案存进同一份记忆（ERP `ERP Express Choice`、Cowork 账套记忆；键 `detail|方向|税号|名称|票号` = 只管这一张）。
  `write_precheck.contracts.KEPT_PER_DOCUMENT_KINDS = {"detail"}`：仍是按单类，但卡片答案 `remember=True`。
  不进「记住的选择」：Cowork `push.choices.of_sets` 排除；ERP 没有把这份记忆列给人看的清单。Cowork 记忆表 `kind` 选项加 `detail`（随 DocType JSON，迁移原生加）。
- 清理：**不挂单据写成 / 作废的回执**（回执里拿不到这张单的明细键，要改载荷与回执两端）。改为记一条新的时顺手删掉这家公司 / 这条连接 90 天前的同类（不开定时任务）；还没写的旧单到时重问一次。
- 用法：`resolve` 所有口径先读——「只记金额」→ `applied["detail"]`，`apply_to_payload` 写成表头、不入库；「这次不录」→ 载荷不动，准备那一步照旧按 `items_mismatch` / `items_incomplete` 停下（现有状态与文案，单据页 / 待处理照「留下」显示原因）；没答过：引导口径问，别的口径照旧停下。
- 守门：`tests/test_express_rules_guided.py::EveryKindAnswerIsUsed` 共用规则每一类造一张会问它的单、答一次后引导与网页口径都不再问；新加一类没造场景就失败。

## 为什么第 1 期没有用例走到「答完 A7 再写账」
第 1 期用例只断言出题（`kinds(resolve(...))` 有 detail）；引导卡流程用的票明细都对得上；写账准备用例只走网页口径——三层各测一半，「答了以后谁来用」没人测。

## 还要的流程（guided-erp 在实验栈跑；C 盘 T 账套读回由验收 local_5097f6f5 在测试机2 跑）
答「只记金额」→ 写成、回读账本是只记表头不入库；答「这次不录」→ 这张单停下不写、现有状态与文案。
