# 需求影响评估：CC 引导式 · Express 读回（P3 第 1 类）

> 总表 `docs/work/2026-11-16-cc-guided-master-table.md` 的 CW-D-RB-L / CW-D-RB-C 两格。基线 `origin/main` = `9e0e102ab`；引擎按 `origin/pearnly/cc-guided-collection`（e590f7fb4）读。

## 0. 需求原文与理解

- 原话：用户 11-14「引导式为主、对话为辅；每个功能都要有引导式」，11-16「覆盖所有」（经主会话任务书）。
- 理解：Cowork 版 CC 对话里，输入框上方业务菜单点「读回」（或说一句），一张卡问齐 → 确定 → 电脑只读读回这本 Express 账套这个月 → 结果卡给四本表（总账、销项税报表、进项税报表、变动表）放进对话。不靠打字。
- 不在范围：读回本身的读法、工作簿版式、电脑端（CC 桌面版）一律不动；对账、增值税怎么用读回结果是后两类的事；删除读回（`cowork_express_readback_delete`）放到「删除类走允许卡」一起做（总表 N5），本类不做。

## 1. 现有同类与复用

- 服务函数：`pearnly_cowork/readback/service.py` 的 `request(connection, account_set_key, period, accounts)`、`status(name)`、`file(name, kind)`——CC 工具 `cowork_express_readback`（`assistant/tools.py:3861`）调的就是这一组；网页没有读回入口（`readback/` 里没有 whitelist）。
- 把工作簿放进对话：`assistant/tools.py:3823 _readback_files`（`assistant.files.add_to_conversation`，同一对话同一份只放一次）。
- 账套 / 月份缺省：`assistant/tools.py:2097 _book_defaults`（本对话账套、对话月份）；账套题照收款引导 `collection/guide.py book_options / book_apply`（`assistant.ledger.choose(by="page")`）。
- 结论：**全部复用**。新建的只有一个声明模块与一组薄转调函数（`readback/cc_guide.py`），不另查库、不另写规则。`_readback_files` 从 `assistant/tools.py` 挪成 `readback` 域里的公共函数，工具与引导共用（机械搬移，单独一个提交）。

## 2. Frappe 原生能力

- 引导运行记录用已有 DocType `Pearnly Assistant Guide Run`；回读记录 `Cowork Express Readback` 不变。不涉及 Desk 页面。
- 前端在 `frontend/assistant`（ADR-019 例外），用标准组件库与 globals.css；本类不新写前端组件（等分支 3 的通用题卡 / 结果卡）。

## 3. 按类通用

- 类：「不写账本、只读一本账套一个月」的引导。同类还有增值税草稿（读回结果的下游）；对账三类的「总账 / 销项税报表从 Express 读回」一步调本类同一组函数。
- 本类把「账套 + 月份」两题做成可复用的选项 / 默认值函数（`book_options`、`book_default`、`month_default`），增值税、对账直接引用，不各写一份。

## 4. 上下游数据

- Cowork 内：读回结果给对账（总账、销项税报表）、增值税草稿（进项 / 销项）用；只保留同一账套同一月最新一份（现状不变）。
- Express：只读，Express 开着也能读，不写账本、不扣费（工具说明）。
- CC 桌面版：载荷不变（`request_service.create(KIND, …)` 原样）；电脑没连 / 要更新时由 `request_service.waiting` 回原因，引导里显示成题目下的事实行（同收款「读回没到」）。
- 云端 Express：同一组函数，连接换成云端那条；`capable()` 判不能办时菜单不列（云端线交付后放开）。MR.ERP：不能读回，账套题只列 Express 账套；本对话账套是 MR.ERP 时卡上一行事实（沿用已有词条 `cowork.readback.not_express` 的译文）。
- ERP / DMS：不涉及（ERP 版 CC 没有读回工具）。

## 5. 隔离与权限

- 菜单：`allowed(user)` = 有 Cowork「录入」功能（`scope.require("intake")`，同工具）；服务端每一步再由 `readback.service` 自己核（`scope.require`、账套属本对话公司由写入闸 `write_scope` 核，模型或页面给的别家账套一律拒）。
- 读回结果只发起人能推进（`status` 只在 `actor == session.user` 时推进），别人发起的同一份只回现状；引导运行只认本人（`run._locked`）。
- 不按公司隔开的口径（用户 11-16）只说对账、盘点等工作空间对象；读回按账套走，账套属本对话公司，不变。

## 6. 入口

- CC 对话：业务菜单「读回」+ 说一句（模型只打开引导、不经手账套 / 月份值）。手机端同一张卡（375 宽）。
- 网页、LINE、拍照：读回本来没有网页 / LINE 入口，不新增。

## 7. 共用件与连带回归

- 改到的共用件：Cowork `hooks.py` 的 `pearnly_guides`（追加一项）；`assistant/tools.py`（`_readback_files` 挪出、工具改调新位置）。开工时贴 `scripts/check-impact.py --files` 输出；`assistant/tools.py` 若在登记表里按清单逐入口实测。
- 不碰引擎文件（`pearnly_core/assistant/guide/*`）。
- 回归：`tests/test_cc_guide_registry`（声明形状、点路径存在由 `scripts/check-cc-guides.py` 核）、新 `tests/test_cowork_readback_guide.py`；原有读回工具测试照跑。

## 8. 存储与成本

- 不新增存储（读回工作簿现状就存）；引导运行每次一行，几 KB。不调识别、不发 LINE、不新增固定月费。

## 9. 删除、作废、追溯

- 引导运行随删对话删（`run.forget`，分支 3 接）；读回删除走允许卡另做（N5）。

## 10. 文案与官方 UI

- 新增词条（四语 th / zh / en / ja，过 `python3 scripts/check-copy.py`）：菜单「读回」`guide.business.readback`、开场一句 `guide.open.readback`、两步标题 `guide.step.readback.book`、`guide.step.readback.period`。拟用字（中文）：读回 /「选择账套和月份。」/ 账套 / 月份——沿用已有「账套」「月份」词条的译法。
- 结果卡的状态字沿用已有读回词条（在读、电脑没连、要更新、已读回）；不改已有文案。

## 11. 测试与验收

- 离线：声明形状；账套缺省（有本对话账套不问、没有就问、MR.ERP 账套给事实不给选项）；月份缺省 = 对话月份、改月份失效重开（`expires: ledger, company, period`）；确定只调一次 `request`（双击回上次结果）；结果在读回 `wait`、电脑没连给事实、读完给四个文件；别家账套被拒。
- 流程（接线之后）：实验栈假智能体，菜单点读回 → 确定 → 结果卡在读（替身电脑）→ 读完四个文件；产品地图加 `cowork.cc.action.guide-readback`。
- 真模型：说「读一下上个月的账」→ 只打开引导、月份由卡上选。

## 12. 要问用户 / 主会话的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 读回放在业务菜单上，还是只作为对账 / 增值税里的一步 | 两处都有：菜单一项（会计也单独用读回核 Express 改动），对账 / 增值税里一步调同一组函数 | 只做一处则另一处回到打字 |
| 2 | 科目前缀（只读某几个科目）要不要问 | 不问（读全总账）。引擎现在没有「可不答」的步骤（`required: false` 的步骤不答会一直停在问），也没有实测需求 | 要问就得先让引导式窗口加「可选步骤」 |

## 13. 工作量与排期

- 步骤：① `_readback_files` 挪进域里（机械搬移）② `readback/cc_guide.py` 声明 + 转调 + 离线测试 ③ 术语表四语 ④ 全闸门、候选离线 → 推分支。写码 3–4 小时、机器约 1 小时（闸门 + 候选）。
- 接线档（分支 3 之后）：流程一条 + 真模型一段，约 1 / 1 小时。
- 依赖：服务端不等任何人；结果卡显示文件依赖引擎 N2（先在结果里带 `files`，页面接上前不显示）。
