# 代录转人工说明 / 保存选项缺归类键（2026-11-05，WIP）

分支 `pearnly/operator-ai-server-polish`，基线 `40f299bbc`（origin/main）。办公室 19:00 断电，18:35 前先交 WIP。

## 2. 保存选项缺 subject → 「请检查 ERP 连接配置」（已修，f9a0e27a7）

- 根因：`push/choices.py:95` 空 subject 抛 `connection.config_invalid`；`service.save_choices`（`push/application/service.py:713`）
  把调用方给的 subject 原样交给它。真机用的生产版（`release-20261104-1453`）还有 `cowork_save_push_choices`；main 上
  `ad2fedbb1`（11-05 15:33）已删掉这个工具，改走 `pearnly_precheck_answer` → `precheck_source.remember`（:384），但它把
  模型回的 kind/subject 原样交给 `save_choices`，缺 subject 时同样回 config_invalid。
- 修法：共用处 `save_choices` 对 payment / vat（归类键由这张单的对方唯一确定）缺 subject 时按单补
  （`_own_subject`，同票上人改付款方式记的取法 `supplier_subject(direction, 对方税号, 对方名)`）；补不了回新词条
  `cowork.choice.subject_missing`（缺少要记住的对象）；`record()` 空 subject 也回这个码。带 subject 的旧调用不变。
- 测试：`tests/test_cowork_push_fixed.py::OwnSubject` 3 条（补键、保留给的键、补不了回新码且不记）。
- 未做：ERP 预选卡答案路径是否有同类缺口未核。

## 1. 转人工说明在卡片上看不全（只查了一半，未改）

已查清的层：

| 层 | 位置 | 存 / 截 |
| --- | --- | --- |
| 回执净化 | `pearnly_core/express_rules/push/common.py:468` `sanitize_push_meta` | 白名单字符串键各截 200；没有 `evidence` |
| Cowork 推送记录 | `cowork_push_record.json:229` reason | Data，length 500 |
| Cowork 单据 | `cowork_ocr_history.json:341` last_push_reason | Small Text |
| ERP 单据 | `erp_*` ledger_reason | Small Text |
| CC 写账件 | `pearnly_cc_job.json:165` reason | Small Text |
| 代录任务单据行 | `pearnly_express_operator_task_document.json:55` reason | Data（140）；`tasks.py:201/231/346` 写入时 `[:140]` |
| 事件源 | `express_operator/feed.py:85` `reason_code` | `[OPERATOR_X] 原文` 只留码 `x`，原文整段丢掉；卡片只能按码显示「无法识别的 Express 画面」 |

待做：
1. 机器通道 ack（`cc_desktop/jobs.py:333 acknowledge`）→ 推送记录 / 写账件这一段截多少未核完。
2. 任务单据行 reason Data → Small Text（DocType 改动，配 migrate），去掉 `[:140]`。
3. `feed.py` 在单据结果 / 选择卡里另带 `detail`（`[OPERATOR_X]` 后的原文，去掉开头本机路径 `C:\…\need-human.png · `），
   前端 `frontend/assistant/` 卡片显示。
4. `sanitize_push_meta` 可选放行 `operator.evidence`（≤300，不给网页显示）。
5. 测试：1500 字 `ai:` 说明全链路不截；旧 CC 带路径的不显示路径；净化旧回执逐字节不变。

## 闸门

已跑：`check-i18n.py --strict`、`check-copy.py`（新增 1，拦截 0、标黄 0）、提交钩子架构检查 PASS、相关离线测试。
未跑：`gate.sh` 全闸门、`check-impact.py`、`check-product-map.py`。
