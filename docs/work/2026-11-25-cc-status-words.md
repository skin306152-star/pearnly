# CC 单据状态词统一（2026-11-25）

分支 `pearnly/cc-status-words`，基 `pearnly/cc-guide-reason-text` 变基到 main 4eb0b2499 后的结果。

## 用户口径

用户 11-24 同意（主会话转达「可以」）：已入账 → 成功、进行中 → 录入中、未完成 → 失败、已跳过不变，四语同步。
与 LINE 回执、录入口状态词（录入中 / 成功 / 失败 / 已取消）同一套词与译文。

## 文案改动（已同意）

| 来源·键 | 中文 | English | ไทย | 日本語 |
|---|---|---|---|---|
| assistant·OPERATOR.doc.saved | 已入账 → 成功 | Posted → Succeeded | บันทึกบัญชีแล้ว → สำเร็จ | 記帳済み → 成功 |
| assistant·OPERATOR.doc.entering | 进行中 → 录入中 | In progress → Entering | กำลังทำ → กำลังบันทึก | 進行中 → 入力中 |
| assistant·OPERATOR.doc.notDone | 未完成 → 失败 | Not done → Failed | ยังไม่เสร็จ → ไม่สำเร็จ | 未完了 → 失敗 |
| pearnly_core·operator.report.state.saved | 已入账 → 成功 | Posted → Succeeded | บันทึกบัญชีแล้ว → สำเร็จ | 記帳済み → 成功 |
| pearnly_core·operator.report.state.notDone | 未完成 → 失败 | Not done → Failed | ยังไม่เสร็จ → ไม่สำเร็จ | 未完了 → 失敗 |
| pearnly_core·operator.end.not_done | 未完成 {0} 张：{1} → 失败 {0} 张：{1} | {0} not done → {0} failed | ยังไม่เสร็จ → ไม่สำเร็จ | 未完了 → 失敗 |

译文与 `intake.push.pending`（录入中）、`connect.records.success / failed`、`home.status.ok / failed` 一致。

## 改到的出现处

同一组词 `op.doc.*` 覆盖：引导结果卡（guide-review）、代录开始卡 / 资料清单卡（operator-start）、代录步骤（operator-steps）、
资料清单「列出快照」里已离开待录清单的单（`goneLabel`：成功 · 单号 / 失败 / 已跳过 / 等待）；服务端代录报告 `feed.py` 用
`operator.report.state.*` 与 `operator.end.not_done`。

## 不改的（不是单据状态词）

- 「结果不明」卡的两个按钮「已入账 / 没有入账」是人在 Express 核对后的回答，不是状态：按钮原先借用 `op.doc.saved`，
  改为独立词 `OPERATOR.entered`（沿用原文「已入账」四语），流程 `cowork_operator_unknown` 断言不变。
- `guide.q.operator_again`（问题标题「已入账」，选项 不入账 / 再写一次）、`guide.option.operator_resolve.entered`、
  `guide.reason.already_entered`（已跳过的原因）：描述的是「账本里已有这张」，保持原文。
- 任务 / 对话状态（`state.running`、`chatRunning` 等「进行中」）是整件任务的状态（照 Code 任务），不是单据状态，不改。
