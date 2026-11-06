# Cowork 按行选物料与库存 —— 施工记录（11-06）

评估与审定：分支 `pearnly/line-item-stock-choice-770877` 的 `docs/work/2026-11-06-erp-line-item-stock-choice-评估.md` 第 15 节
（主会话 11-06 审定 C1–C10 全按建议；ERP 部分另开分支，等库存整改合入后做）。

分支 `pearnly/cowork-line-item`，**叠在 `pearnly/cc-mixed-lines` 上**（混行单要新版 CC 才写得进 Express，必须先合 CC 那条）。

## 做了什么

| 项 | 提交 | 落点 |
|---|---|---|
| C8 CC 改单不丢行上过账类型 | 4c6688b8d | `C/assistant/tools.py` `edit_document` / `_kept_kinds`；详情回每行过账类型 |
| 共用写账规则按行定 | 84c235529 | `K/express_rules/push/choices.py`：`EXPENSE`、`resolve(undeclared_lines=…)`、`line_kinds`、`with_line_kinds`、混行单问费用科目；`mapper.py` / `sales_mapper.py` 定行前写上 |
| Cowork 判定 / 记忆 / 卡片 | fd5772758 | `C/push/application/service.py` `_per_line`、`save_choices` 收「记费用」、`products_all`；`C/assistant/precheck_source.py`；`C/public/desk/targets.js`；CC 前端「记费用」；`K/write_precheck/api.py`、`K/assistant/ask.py` 搜索类 |
| CC 工具金样本 | 4ba7d7fb0 | `tests/fixtures/assistant/cowork-push-tools-golden.json` |
| 网页 | 9a756a98e | 新建录入弹窗不再整批选服务 / 存货；复核页「批量编辑」原生 Dialog + Table |
| LINE | 549f37cf3 | 上传前去掉「入账方式」与随后的存货科目组；识别后按行追问 `C/line/items.py`（推送侧经钩子 `cowork_line_items` → `C/push/application/line_items.py`） |
| 登记与测试 | ba747bda3 | 产品地图、共用件登记表、Cowork 关注 / 取消关注与 ERP 取消关注的单测、基线减项 |
| 流程用例 | 6694a6638 | `scripts/flows/cowork_intake_bulk_kind.py`（`cowork.intake.bulk-kind`） |

代录与定时 / 自动推不另改代码：共用规则里「这一行记过的」排在整批声明（定时任务的入账方式、代录资料区的分组）之前，
没记过的行照整批；代录每行本来就按 `item_mode` 建库存或非库存档（`companion/.../express_operator/masters.py`）。

## 行为变化（如实）

- 永续 / 混合库存账套里没声明过账类型、公司也没选过整张入账方式的单：以前整张问「走库存 / 记费用」，现在按行问存货对照（存货 / 新建存货 / 记费用），
  名字对上目录的行直接定；按行问了就不再出整张那一问。周期制账套、声明过的、公司选过的照旧。
- Express 目标上传前不再选服务 / 存货（网页新建弹窗、LINE）：周期制账套里这类单按记账画像写非库存（以前选「存货」会写库存行）。
- 混行单（同一张有库存行和费用行）有费用行时会问费用科目（以前有库存行就不问）。
- 混行单盖载荷 19，只派给新版 CC（`pearnly/cc-mixed-lines`）。
- LINE 追问的答案记进账套记忆；同一张票在扫描自动推判过（识别后约 15 分钟）之后才答的，留在待处理里由人推（扫描只判一次）。
- 推送前要选存货科目组的（账套一件库存品都没有），LINE 里不再问，停在待处理 / 网页提交前问。

## 验证

- 单测：`tests/test_express_rules_line_choice.py`、`tests/test_cowork_line_items.py`（已登记 development.yml）、`tests/test_cowork_write_precheck.py` 新增 4 条、
  `tests/test_cowork_assistant_tools.py` 新增 1 条；`tests/test_cowork_line_intake.py`、ERP `integrations/test_line_intake.py` 新增的要 Frappe，跑在候选离线检查里。
- 本地全闸门、实验栈流程、候选离线检查：结果见交付消息（本文件末尾补）。
- **没做**：真机 LINE（三星）、测试机真 Express 写入、原生混行证据（主会话安排）。
