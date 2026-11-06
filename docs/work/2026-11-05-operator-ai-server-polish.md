# 代录转人工说明 / 保存选项缺归类键（2026-11-05 起，11-06 完成）

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
- ERP 同类路径（11-06 核）：`express.precheck.remember` → `save_answers` → `save` 按 (kind, subject) 对这张单此刻要问的项，缺
  subject 的答案对不上任何项、跳过（回 saved 0），不抛 `connection.config_invalid`，没有「请检查 ERP 连接配置」这个误导；不改。
- CC 里的显示：对话工具出错按 `str(frappe 异常)` 交回（`assistant.tools.run`），`_()` 按用户语言取词条，中文为「缺少要记住的对象」。

## 1. 转人工说明在卡片上看不全（11-06 已改）

CC 1.12.15 起回执是 `[OPERATOR_<原因>] ai: <结论> · <步骤>: <问题>`，最多 2000 字。一路截断点与改法：

| 层 | 原来 | 改后 |
| --- | --- | --- |
| 回执规则 `cc_desktop/wire/ack_rules.py`（Cowork 推送账本与共用机器通道同一套） | 原因截 500 | `reason_text`：`OPERATOR_*` 留到 `OPERATOR_REASON_MAX` = 2100，其它仍 500（旧线上对照测试不变） |
| Cowork 推送记录 `Cowork Push Record.reason` | Data(500)，超长保存报错 | Small Text（原生迁移，部署 migrate） |
| Cowork 票上 `last_push_reason`（`history/push_state.mirror`） | 截 500 | 截 `OPERATOR_REASON_MAX` |
| ERP 单据 `ledger_reason`（`express/push.py` 写账件回写） | 截 500 | 截 `OPERATOR_REASON_MAX`（字段本就是 Small Text） |
| 代录任务单据行 `Pearnly Express Operator Task Document.reason` | Data(140)，`tasks.py` 三处 `[:140]` | Small Text（migrate），截 `contracts.REASON_MAX`（= 2100，测试守住与回执规则一致） |
| 事件源 `express_operator/feed.py` | 只留原因码 | 另给 `detail`（`reason_detail`：`[OPERATOR_X]` 后的原文，去掉旧版 CC 开头的本机截图路径）：单据结果、要人处理卡片（operator_choose）、整批停卡片（operator_resume） |
| 前端 `frontend/assistant` 卡片 | 只显示原因文案 | 原因下面照原文显示 `detail`（`.cc-op-detail`，分行、长词换行） |

不做：`sanitize_push_meta` 放行 `operator.evidence`（CC 本机路径，网页用不上）。报告 CSV 的原因列仍按原因码出文案。

测试：`tests/test_cc_desktop_channel.py`（代录回执整段留、其它截 500、一路字段类型与上限一致）、`tests/test_express_operator_feed.py::DetailTests`
（1400+ 字结论进单据行、单据结果、选择卡；旧版路径去掉；产品原因码无说明；整批停卡片带说明）。

## 2. 后台关鼠标（CC 分支 `pearnly/cc-operator-ai-polish`）

后台做法的接手请求里电脑工具集带 `configs`，11 个鼠标类成员 `enabled: false`（`helper.MOUSE_MEMBERS`）。11-06 用评测密钥在甲骨文直调
Haiku 5.5：带 configs 回 200，输入 token 4550 → 2888（鼠标成员定义不再下发）。服务端转发原样转 `tools`，不用改。

## 闸门

本地按 `.github/workflows/development.yml` 逐条全跑（见交付说明）；`check-impact.py` 登记 `history/push_state.py` 进 `cowork.cc.channel`。
