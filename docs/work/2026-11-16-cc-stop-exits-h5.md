# 代录 / 直写被拦后有路可走（真机 H5，11-16）

主会话 11-16 审定 S1 → S2（方案与证据：共享笔记「代录停下出口-H5方案-11-16.md」）。分支 `pearnly/cc-stop-exits-h5`，基线 origin/main 290bc368b。
CC 桌面版一侧记为 1.12.23 范围：1.12.22 发出前不合 main，见文末「发版顺序」。

## 现象（H5）
ERP 网页建采购单（新供应商、新物料、第二个仓、赊购）→ 交代录 → CC 桌面版 10 秒后回 `STOCK_ACC_GROUP_UNFIT`。
- 69EXP\TEST 只有 2 个库存品，都挂 ST01（存货科目 14-01-03-00 办公设备）。CC 桌面版按用得最多取 ST01，过不了存货科目判据。
- 这本账套过得了判据的组有 DM、P002。
- 对话里的卡只有「重试」，不显示原因，用户无路可走。

## 根因
1. **服务端和 CC 桌面版的选组规则不一致**：
   - 服务端只在账套一个库存品都没有时判存货科目组（`express_rules/push/stock_acc_group.resolve_stock_acc_group`），并且明确不按用量众数选（69SINCER 先例）。
   - 账套里已有库存品时，服务端不判，交给 CC 桌面版按众数选。
   - 所以开始前没人问，写到一半才拦。
2. **代录对话卡只对开始前判出的「要人选」（`choice_needed:`）出题**：CC 桌面版写到一半回的码（存货科目组、对方档、服务进项口径……）一律只给「重试」。ERP 网页单据页反而有这几种的选项卡（`express/api._party_blocked`）。

## 影响评估（模板要点）
| 项 | 结论 |
|---|---|
| 同类复用 | S1 复用「账套零库存品」那条三档解析（1 个合格 → 自动；≥2 → 开始前问一次、按账套记住；0 → 不拦，照旧交 CC）；问法用开始前预选卡同一套（ERP `precheck` 已有存货科目组题与按用量预选；Cowork 补同一题）。S2 复用 ERP 单据页 `_party_blocked` 的出题，提成产品钩子 |
| Frappe 原生 | 不新增 DocType / 补丁；记住沿用两产品现有存处（ERP `choices.STOCK_GROUP`、Cowork 连接 `stock_acc_groups`） |
| 上下游 / Express / CC | 服务端判出的组随载荷 `stock_acccod` 下发，CC 桌面版各版都优先用它（`stock_template.pick`），旧版不用改。「用户选了不合格的组也照写」要 CC 桌面版改判据，等原生实录，判据做成一处可改（1.12.23） |
| 隔离权限 | 不变：按账套记住，选择入口沿用两产品现有的权限判定 |
| 各入口 | 直写（ERP 单据提交、CC 对话推送）、代录（ERP / Cowork）、定时、Cowork 放票 / 文件夹：都走同一个写账规则映射层，同一题 |
| 共用件 | `express_rules/push`（mapper、sales_mapper、stock_acc_group）、`express_operator/feed`、前端 operator-choose 卡；合入前跑连带回归闸门与候选离线检查 |
| 成本 | 零：不多发请求，判据只读心跳已报的组 |
| 作废删除 | 不涉及 |
| 文案 | 卡上显示原因：沿用两产品已有的原因词条；新增的四语直接写；改已有文案（「重试」那一行、「已发送，等待录入」）先列清单给主会话 |
| 验收 | 新增流程「代录写到一半回停下码 → 对话出原因与可选卡 → 选后续写」，先红后绿；真机重跑 H5 + 「选 ST01 照写」 |
| 待问 | 「选了不合格组照写」的原生实录（测试机，主会话排） |

## 分工（与 H3 窗口 local_3709c091 约定）
- 本分支：`express/api.py`、`express/prepare.py`、共用层 `express_rules/push/*`、`express_operator/feed.py`、前端 operator-choose 卡、Cowork 推送准备与预选卡的存货科目组题。
- H3 窗口：`express/operator.py`（intake / submit_chat）与 remember 的权限修复，它先合、本分支再变基。
- S3（停下出口登记表里 41 个未归类码清零，`scripts/stop-exits-baseline.json`）：引导式窗口。

## 发版顺序（建议）
1.12.22 已在 main：先从 main 打 1.12.22 发正式版。本分支里 CC 桌面版那一块（`companion/`）等 1.12.22 发出后再合 main，记为 1.12.23。
服务端那一块（S1 判据、S2 出口）对旧版 CC 也成立，可以先合、随下一批部署。所以本分支拆两次合入：服务端先合，CC 桌面版那一提交后合。

## 11-20 对齐 main 的选组规则（主会话定）
- 新物料挂哪个存货科目组一律用 main 的 `stock_acc_group.new_item_group`（0c4891a39：用心跳报的件数证明挂得最多的组是合格组，证明不了才按合格名单 1 个自动用、几个开始前问、0 个停下）。ERP `prepare.py`、Cowork `service.py`、`card_save`、`precheck_source` 都已在调它。
- 本分支原来的 `for_new_items`（mapper / sales_mapper 里另判、Cowork 推送准备另停、Cowork 预选卡另出一题）是同一问题的第二套规则，变基到 main 6b641f42f 时整段去掉，用 main 的版本；对应的 Cowork 单测改动也去掉（main 已有同一判据的测试）。
- 保留：电脑写到一半以「要人选一次」的码停下（对方档疑似重复 / 分店、存货科目组、服务品进项口径）时，ERP 开始卡与代录卡列出那一项（`api.blocked_needed`、`precheck._with_blocked`），代录卡上说产品那一句原因（`feed` 的 `reason_text`）。
- 流程 `cc.operator.stop-choice` 按新规则改断言：账套报了库存品总数，能证明挂得最多的组合格 → 开始前不问、载荷不带组；证明不了 → 问一次，只列合格的，推荐挂得最多的。
