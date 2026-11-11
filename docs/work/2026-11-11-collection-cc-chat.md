# 收款第 4 步：ERP 版 CC 对话里录收款（2026-11-11）

评估与审定：[收款评估](2026-11-11-collection-receipt-assessment.md)（§12-1 放行统一、审定结论①②）；第 2、3 步记录：
[收款 LINE 入口](2026-11-11-collection-line.md)。分支 `pearnly/collection-cc-chat`（从 `pearnly/collection-line` 开，变基到 `origin/main` 6275e4999）。

## 做了什么

- 工具（`collection/cc_tools.py`，hooks `pearnly_assistant_tools` / `pearnly_assistant_step_names`，每个工具 `modes: ["chat"]`：只在直写对话，代录会话的工具表里没有——代录不做收款，用户 11-11 定）：
  `erp_collection_customers`、`erp_collection_invoices`、`erp_collection_methods`、`erp_collection_draft`（建 / 改，回确认卡）、
  `erp_collection_voucher`（对话附件 → `store_voucher` + `set_voucher`）、`erp_collection_card`、`erp_collection_push`（放行）、
  `erp_collection_status`、`erp_collection_list`、`erp_collection_withdraw`。只调 `collection.service`，出错 `status: not_saved` + `reason`（码）+ `message`（译好的一句）。
- 放行走 A 线写入计划，不改共用层：草稿规则全过时结果带 `document = R:<记录>@<版本>`；ERP 的判定钩子
  `express.precheck.check` 把 `R:` 单号交给 `collection/plan.py`（草稿、版本对得上、确认卡规则全过 → 照写，判定摘要带确认卡内容；
  主档在读 → 等电脑；其余写不了带原因码）；`express.precheck.allowed` 收款员（`collection.write`）也能建计划。
  CC：草稿 → `pearnly_write_plan(documents=[document])` → 复述 → 用户下一句 → `pearnly_write_start` → `erp_collection_push`：
  `direct_push.permitted` 只放已开始计划里的那张、停过不放；按单号里的版本 `service.release`，同意 = `cc:<对话>:<用户消息序号>`；
  等结论同直写推送（写成回 RE 号，没写成回 queued）。复述后改过：开始时摘要不同不写；开始后才改：`release` 回 `changed`。
- 「录吧」：计划事件由共用层发，前端不改。
- 开始后模型若把 `R:` 单交给 `erp_push_documents`，回 `not_found` 并提示改用 `erp_collection_push`（`express/cc_tools.py`）。

## 口径与交接

- **代录不做收款，收款只在直写对话（用户 11-11 定）**：原先为代录会话写的 `operator_conversation` 拒绝分支已删（工具在代录会话里根本不出现），
  不需要共用层为代录会话加直写计划。
- **写成后在对话里主动补一句结果**：交 A 线明天做（接共用层对话推送，同直写 `push_followup`；`collection.push.TELL` 现在只有 LINE）。
  在那之前靠 `erp_collection_push` 等约 25 秒 + `erp_collection_status`。

## 新文案（四语，步骤名，`i18n/messages.tsv`）

做完 / 进行中各 10 条：查找了收款客户 / 正在查找收款客户、查看了未收清的发票、查看了收款方式、保存了收款草稿、附上了转账凭证、
查看了收款、录入了收款、查看了收款结果、查看了收款列表、撤回了收款（各配「正在…」）。`check-copy.py`：新增 20、拦截 0、标黄 0。

## 验证

见交付报告（分支 SHA、闸门、候选离线）。真模型场景本轮不跑（额度另批）。
