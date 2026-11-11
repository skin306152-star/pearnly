# 收款第 4 步：ERP 版 CC 对话里录收款（2026-11-11）

评估与审定：[收款评估](2026-11-11-collection-receipt-assessment.md)（§12-1 放行统一、审定结论①②）；第 2、3 步记录：
[收款 LINE 入口](2026-11-11-collection-line.md)。分支 `pearnly/collection-cc-chat`（从 `pearnly/collection-line` 开，变基到 `origin/main` 6275e4999）。

## 做了什么

- 工具（`collection/cc_tools.py`，hooks `pearnly_assistant_tools` / `pearnly_assistant_step_names`，两种会话都声明可用）：
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

## 需要共用层配合（未改，交主会话）

1. **代录会话里放不了收款**（审定结论②要求两模式都能用）：`write_plan.make` 在代录会话里只取这个任务的待录清单、忽略 `documents`，
   `start` 一律交代录。收款工具在代录会话里能查、建草稿、补凭证、查结果、撤回；`erp_collection_push` 回 `operator_conversation`（草稿留着）。
   需要共用层：代录会话里允许建一份「直写」计划（例如 `make` 认产品声明的直写单号前缀、这类单走直写分支），或产品可声明的「直写单」接法。
2. CC 录的收款写成后不会主动在对话里补一句结果（`collection.push.TELL` 只有 LINE）；现在靠 `erp_collection_push` 等 25 秒 + `erp_collection_status`。
   要主动补结果需接共用层对话推送（同直写 `push_followup`）。

## 新文案（四语，步骤名，`i18n/messages.tsv`）

做完 / 进行中各 10 条：查找了收款客户 / 正在查找收款客户、查看了未收清的发票、查看了收款方式、保存了收款草稿、附上了转账凭证、
查看了收款、录入了收款、查看了收款结果、查看了收款列表、撤回了收款（各配「正在…」）。`check-copy.py`：新增 20、拦截 0、标黄 0。

## 验证

见交付报告（分支 SHA、闸门、候选离线）。真模型场景本轮不跑（额度另批）。
