# ERP 代录放票走通（H3 / H4，2026-11-16）

分支 `pearnly/erp-operator-intake`，基线 origin/main 290bc368b。评估：[2026-11-16-erp-operator-intake-评估.md](2026-11-16-erp-operator-intake-评估.md)。

## 根因

ERP 代录里放进来的票识别后是收单记录 `I:<记录>`，开录时才提交成 ERP 单。11-08（cb66cbb44）只把 `I:` 接进代录钩子（sources / precheck / search / submit，答案随开始交）。
11-11（33817b8bf）改成「卡片答案走 remember、开始只走写入计划 check → hand_in（不带答案）」后，ERP 写账判定钩子 `express/precheck.py` 的 check / remember 从没认过 `I:`：
remember 抛「没有权限」（H3 的 403），check 静默丢掉（写入计划判 not_found），开录时也不再有答案写回记录。CC 准备中查任务只得到「还没录」，看不到放进来的票。

## 约定（与引导式窗口、CC 桌面版窗口共用，别各写各的）

1. **ERP 单据编号按前缀分派，一处解析**：`express/precheck.py` 的 `target_of(company, doc_id)` 是写账判定（check / remember / search / resume）唯一的解析入口，回
   `("document", (doctype, 名))`、`("intake", 收单记录)`、`("collection", doc_id)`、`("missing", None)`（不是本人的 / 找不到，照旧不说存在与否）、
   `("unknown", None)`（本产品不认的编号种类）。代录钩子（`operator.py`）对 `P:` / `S:` / `I:` 用同一套判据。新加一种编号要在这里登记，不在各函数里各判一次。
2. **收单记录 `I:` 是写账判定里的一等单据**：check 回的卡片视图与代录预检同一份（`operator.intake_view`，带 `direction`，引导卡按方向比对可直接用）；
   remember 先把收单记录自己的几项（付款 `erp-intake-pay:`、行物料 / 仓库 `erp-intake:<记录>…`）写回记录，其余 Express 项按它将成为的单据类型核值、记下；
   记过以后这条记录的付款项来源是 `remembered`（已定），不再是预选。已提交的 `I:` 按提交出的单据判。
3. **待录清单给模型**：共用层 `express_operator_status` 在任务准备中（还没开始）时回 `waiting`（这次代录待录的单：编号、类型、日期、对方、票号、合计，别的月份的带 `other_month`，同写入计划用的产品 `sources`）
   与 `uploads`（任务里放进来的每一份：文件名、识别状态、识别出的单），两产品同一套。CC 据此知道票已放进来、走写入计划，不叫用户换入口。
4. **认不出的编号种类单独报码**：写账判定 check 对 `unknown` 回 `{state: not_writable, reason: "unknown_document"}` 并记错误日志
   （标题 `Write precheck: unknown document kind`）；remember / search 对 `unknown` 记同一条日志、回「Invalid request」；`missing` 照旧「没有权限」/ 不回。
   停下码、原因码都是给模型与日志的，不新增界面文案。H5 的「停下码 → 原因 + 卡片」若要给 `unknown_document` 配话，走 `reason_text` 那条，不在这里加。

## 施工中查出的同链路问题（一并修）

- 收单记录的行物料或仓库还没定时，写入那条准备先抛「请选择仓库」，整张卡片 / 写入计划直接报错：改为先问记录自己的题，定了再判 Express 那几项（`operator.intake_found`）。
- 还没提交的销售收单记录载荷里没有单号，含税销售票预检一律判「缺票号」（H4 会卡在这里）：判定时用将分配的号（`numbering.preview`，不占号）。
- 付款按对方记住过的，卡片上标「已记住」而不是预选。

## 未连接一态（主会话 11-16 追加，两产品一套）

- 产品钩子 `computer_state(company, user)`（ERP 按 `cc_desktop.jobs.writers`，Cowork 按电脑池按人）回单据页 / 去向已有的原因码，经 `reason_text` 出词条，不新增文案。
- 代录进度（整段状态行与每张排队单的步骤行）写「等待电脑 · CC 桌面版未连接」等；`express_operator_status` 回 `no_computer`；写入计划开始后没有可用电脑时不再对模型说「已交给电脑」。
- 流程 `erp.cc.operator.no-computer`：发单人没有电脑 → 开始后单据行显示「等待电脑 · CC 桌面版未连接」、CC 拿到未连接那句。Cowork 侧由单测覆盖（`test_cowork_companies.py`）。

## 证据（全部在本机自建实验栈 pearnly-lab-co8，镜像 = 线上 02eebaa4）

- 修前红（应用代码退回 290bc368b、流程为最终版）：`erp.cc.operator.intake` 卡片作答报「没有权限」、写入计划跳过 `I:LI-…` 原因 not_found；`erp.cc.operator.intake.sales` 卡片不出题、写入计划 not_found。0/2。
- 修后绿：两条 2/2；加上 `erp.cc.operator.no-computer` 与同站点先跑的 `erp.cc.conversation-ledger`，5/5。截图与结果在主会话共享笔记「H3-流程证据-11-16」。
- 全闸门（a726c3e63）：GATE EXIT 0，260 步。
- 候选离线（124d53078，之后只改了流程脚本）：LANE EXIT 0，111 组。
- 受影响流程 `--changed --base origin/main`（124d53078）：10 条 21 格，18 过；3 格不过是新流程在同站点取到了别家公司的物料（流程问题，a726c3e63 修），修后重跑 5/5。产品地图多列的 4 条（两产品个人资料保存、团队批量停用）16/16。
- 实验栈污染一事：施工中一次探测脚本出错留下了账套主档，使 `cc.operator` 的单被排进写账队列（2 格不过）；已删栈重建，干净栈上 `cc.operator` 4/4。

## 连带回归（check-impact，本分支全部改动）

| 入口 | 类别 | 结果 |
|---|---|---|
| erp.cc.chat-push、erp.cc.stocktake、erp.cc.collection、cc.operator.page | offline | 通过（全闸门 / 候选离线里的离线用例） |
| cc.operator、cc.operator.replay、cc.sidebar-modes、erp.cc.conversation-ledger 等 --changed 选中的流程 | 流程 | 通过（见上） |
| erp.cc.write、erp.page.collections、cowork.express.push | auto | 没测到：自动冒烟要对测试租户跑，本次不碰测试机 |
| control.assistant、shared.assistant、cowork.assistant、cc.assistant、cc.operator.thread、erp.cc.companies、cowork.cc-chat.push、cowork.cc.troubleshooting、erp.line.chat、cowork.line.chat、erp.cc.connect、shared.navigation、erp.masters.scope、cowork.execution、erp.execution、erp.provisioning | manual | 没测到：真机清单，H3 / H4 真机复验由主会话排 |

## 没做 / 待查

- CC 问答卡上「只有搜索」的物料题（行没认出物料时）在卡片上不出预选项、搜索框不出候选：服务端搜索接口实测正常，前端问答卡另有问题，未查，不在本次范围（H3 真机那张票的行已认出物料，没问到这题）。
- ERP 代录交单不带 `route_device`、普通单据写账人：另一条分支按「写账人改走 office_actor」评估施工。
