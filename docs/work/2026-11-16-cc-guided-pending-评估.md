# 需求影响评估：CC 引导式 · 待处理 / 结果核对（Cowork）

> 总表 CW-D-PD-L / PD-C / PD-M 三格。ERP-D-PD 在总表上归 guided-erp（B 组），见第 12 节第 1 条。基线同盘点。

## 0. 需求原文与理解

- 原话：同盘点（用户 11-14 / 11-16「引导式覆盖所有功能」；主会话 11-16 成熟流程优先）。
- 理解：CC 对话里点「待处理」，一张卡问齐：做什么（处理一项 / 导出复核工作簿 / 删除）→ 处理一项时：哪一项（待处理清单，同侧栏）→ 怎么处理（按这一项的类别出：补选要人选的项 / 推送 CC 准备好的提议 / 选 MR.ERP 导入菜单与默认值 / 换账套 / 结果不明核对「已入账（可填单号）/ 没入账」）→ 那一类要选的值 → 复核 → 确定。
- 不在范围：待处理页、复核页、推送服务、判定规则都不动；「改票面」（识别值就地改）依赖 A 组 P2-1 的单据版复核卡，那张卡进 main 后这里加一个分支直接用，本类先不做。

## 1. 现有同类与复用

- **网页待处理页完整**：清单 `companies.pending.pending(company, limit, search)`（每项带 `action`：choose / push / mrerp / account_set / open 与推送去向）；当场处理全在 `assistant/pending_api.py`：`answer(document, items)`（补选并重推，交空 = 推提议）、`proposal_detail`、`account_sets / choose_account_set`、`mrerp_needs / mrerp_answer`；可选项 `push.application.api.choices`（复核页、CC 同一套）；结果不明 `push.application.api.resolve_unknown(record, entered, docnum)`。
- CC 工具：`cowork_pending`、`cowork_resolve_push_result`、`cowork_delete_documents`、`cowork_export_review_workbook`（`assistant/tools.py:2676` 起，删除走 `history.removal.delete`，导出走 `roundtrip.service.export`）。
- 结论：**全部复用**，新写 `assistant/guided/pending.py`（声明 + 薄转调）。不改 `companies/pending.py`、`pending_api.py`、`tools.py`。

## 2. Frappe 原生能力

- 不涉及。

## 3. 按类通用

- 一个菜单项「待处理」。「怎么处理」一题的选项按这一项的 `action` 算（同页面展开那一行出的按钮），之后要选的值用 **N1a 子题**（`questions`：补选的各类、MR.ERP 的菜单 / 默认值，题目和选项都来自上面那几个接口），换账套是一道单选，结果不明是两项 + 单号（文本，可空）。
- 本地 / 云端 / MR.ERP 三格同一份声明：MR.ERP 的「选导入菜单与默认值」只在这一项是 MR.ERP 停下时出；云端随云端线放开。

## 4. 上下游数据

- 补选、推提议、换账套、MR.ERP 作答之后都由 `pending_api` 按 CC 推送同一条路重推（Express 交 CC 桌面版写入；归代录的单只记选择不推），结果卡回推送状态（已交出 / 还要选 / 推不成 + 原因 / 等电脑）；之后的写账结论照现有实时事件进待处理与对话。
- 结果不明：只改推送记录的结论，不碰账本（同页面）。
- 清单按本对话公司过滤（`pending(company=对话公司)`，同侧栏点公司看到的）。

## 5. 隔离与权限

- 菜单：有录入功能（`scope.require("intake")`）的人。清单只列本人读取范围内的（服务现状）；每个处理接口照旧核录入权限、单据读取范围、连接推送授权。
- 删除只删没推过的（服务现状），允许卡列出要删的单。

## 6. 入口

- CC 对话业务菜单「待处理」。网页待处理页、CC 侧栏「待处理」、打字工具不动。

## 7. 共用件与连带回归

- 新文件 `assistant/guided/pending.py`；hooks 追加；术语表追加；`shared-parts.toml` 登记。调用 `pending_api` 的公开函数，不改它。
- 依赖引擎能力：**N1a**（子题，已在 engine-n）、**N5 + 条件允许卡**（删除）、N2（导出文件）。

## 8. 存储与成本

- 不新增。重推可能触发识别外的写账（无额外费用）。

## 9. 删除、作废、追溯

- 删除同列表：真删记录与原件，已推送的跳过；允许卡。

## 10. 文案与官方 UI

- 新增：`guide.business.pending`「待处理」（沿用侧栏已有译文）、`guide.open.pending`、`guide.step.pending.*`。「怎么处理」各项名沿用待处理页按钮已有词条（推送、换账套、已入账、没入账…），补选各类的标题沿用 `cowork.choice.*`。不改已有文案。

## 11. 测试与验收

- 离线：清单按对话公司、搜索；「怎么处理」按类别只出对应项；补选子题的题目与选项来自 `choices`、按原样核；MR.ERP 子题来自 `mrerp_needs`；换账套只收 `account_sets` 给的；结果不明两项 + 单号；确定各转调一次、重放不重推；删除允许卡、导出文件进对话；归代录的单只记选择。
- 流程（接线后）：实验栈造一张停在要人选的单 → 引导补选 → 重推。

## 12. 要问主会话的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 任务书写「待处理（Cowork 与 ERP）」，总表 ERP-D-PD 归 guided-erp | ERP 待处理留给 guided-erp（它在做 ERP 直写与待处理，同一组 `erp_pending` 函数），我只做 Cowork | 两边都做会重复 |
| 2 | 「改票面」等 A 组 P2-1 单据版复核卡 | 先不做，P2-1 进 main 后补一个分支（约 1 小时） | — |
| 3 | 清单按本对话公司过滤 | 按（同侧栏点公司） | 不过滤则列工作空间全部 |

## 13. 工作量与排期

- 写码 4–5 小时、机器 1.5 小时；排在定时任务之后。
