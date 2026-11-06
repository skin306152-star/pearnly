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
| 流程用例 | 6694a6638、5c035a440 | `scripts/flows/cowork_intake_bulk_kind.py`（`cowork.intake.bulk-kind`）；修批量编辑按钮 |
| 候选检查修正 | 7691d9cfc、4f1c2deb2 | 删随功能去掉的旧测试；通知卡片时取绑定出错兜住 |

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
- 本地全闸门（照 development.yml 逐步）：4f1c2deb2 上 197 项全过。
- 实验栈（本机 `pearnly-lineitem-lab`，生产镜像 fb1037ad 底座 + 本工作树源码）`run.py --changed` 涉及的流程：
  cc.chat、cc.operator、cowork.cc.ask、cowork.pending.operator、cowork.field-help、cowork.mrerp-login、cowork.precheck.api 全过；
  cowork.intake.review、新的 cowork.intake.bulk-kind 两套数据 × 电脑 / 手机 8 格全过（第一轮发现批量编辑按钮提前读了编辑状态把复核页弄挂，已修，5c035a44）；
  cc.operator.replay 4 格不通过：新建的实验栈每家公司只有 1 张已提交采购单、这条流程要 2 张（`need two submitted purchase documents`），本分支没动 ERP 采购建单，未处理。
- 候选离线检查（`ci-candidate-checks.sh offline`，生产镜像、候选代码挂进镜像 apps）：4f1c2deb2 上整条通过（含要 Frappe 的 Cowork / ERP LINE 测试）；
  中途发现两处：上传前存货科目组的旧测试（随功能删掉）、通知卡片时取绑定出错没兜住（已修）。
- **没测到**：LINE 模拟 webhook 冒烟（实验栈站点没配 LINE 频道，ERP 站点同样回 0 条）；识别后追问的端到端（要付费识别 + CC 上报的账套主档，实验栈没有，只有单测）；
  真机 LINE（三星）、测试机真 Express 写入、原生混行证据（主会话安排）。
