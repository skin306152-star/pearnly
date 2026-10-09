# 失败原因按看的人的语言显示（2026-10-09）

用户 10-09：泰文录入列表里推送失败原因是英文（「The document date is outside the fiscal period…」），「除了这里还有哪些」。
基线 main `844ffc9e`，分支 `pearnly/reason-language`。

## 原因

推送失败原因在推送那一刻按后台任务的语言翻成句子存进记录（`Cowork OCR History.last_push_reason`），列表原样显示：
后台是英文，泰文 / 中文的人看到的就是英文；反过来也一样。

## 本批已改（不加新文案）

| 项 | 做法 | 位置 |
|---|---|---|
| 推送失败原因 | 存之前把翻好的句子还原成词条原文（英文原句或词条键）；网页、结果表格显示时按看的人 / 负责人的语言再翻 | `history/push_state.source_text`、`record_failure`；`intake-upload.js p.intake_push_reason`；`push/export_facts.reason_text` |
| 旧记录 | 补丁把已存成中 / 泰 / 日文句子的原因还原成原文（英文原句本来就是原文） | `patches/v0_0_15_push_reason_source` |
| 小助手回报的码大小写 | `[ENTRY_NOT_BALANCED]` 也认小写词条 `intake.error.entry_not_balanced` | 同上两处 |
| 后台语言被冲掉 | 定时任务推进与机器接口切负责人语言时标 `job.lang_resolved`（同识别、对账任务） | `folder_intake/pump._as`、`machine._as` |
| 银行对账报错显示原码 | 按「码:字段」查词条（`messages.error_text`，同 `revision.py`） | `reconciliation/bank_recon_service._check_review` |
| 定时任务文件失败码 | 存原文 | `folder_intake/pump._fail` |

## 还没改（另做 / 待批）

- 对账提示与工作簿警告按建任务的人的语言存（收入对账、增值税核查、银行对账 run_info 约 50 处读写，旧模块自带四语句子）——
  改成「词条 + 参数」存、显示时拼，单独一批做。
- 要新文案的（列给用户批）：小助手 13 个错误码没有词条、上传失败显示原始错误、ERP 提示拼英文细节、
  框架自带词（继续 / 搜索 / 上一页 / 缩放…）本仓无泰文、DMS 快照刷新几句英文、小助手日志英文细节。
- 看板默认三列标题按创建人语言存（可改名，低优先）。

## 验证

`tests/test_cowork_reason_language.py`（原文还原、按语言显示、大小写、补丁登记、后台语言）；本地闸门 0 失败，Cowork 全量与 main 相同。
