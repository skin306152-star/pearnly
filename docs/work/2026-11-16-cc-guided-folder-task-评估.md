# 需求影响评估：CC 引导式 · 文件夹定时任务（Cowork）

> 总表 CW-D-FT-L / FT-C / FT-M 三格。基线同盘点（`origin/pearnly/cc-guided-readback` 51004d757）。

## 0. 需求原文与理解

- 原话：同盘点（用户 11-14 / 11-16「引导式覆盖所有功能」；主会话 11-16 成熟流程优先）。
- 理解：CC 对话里点「定时任务」，一张卡问齐：做什么（新建 / 修改 / 启停与提醒 / 立即运行 / 删除）→ 哪个任务（新建不问）→ 电脑 → 文件夹 → 去向 → 默认处理 → 日程 → 提醒 → 名称 → 设置卡复核 → 确定；立即运行、删除走允许卡。账套 = 本对话账套，不另问。
- 不在范围：任务服务、小助手同步、到点推送、网页「自动录入规则」页、CC 对话页右侧的「定时任务」页都不动；一次存几个（`cowork_save_folder_tasks`）、列文件 / 取文件进对话（`cowork_folder_files / fetch`）仍走打字（查询类不做卡）。

## 1. 现有同类与复用

- **网页有完整表单**（`folder_intake/api.py` 8 个接口 → `folder_intake/rules.py`）：`options(connection, account_set_key)`（电脑、根目录、文件夹、科目、部门、存货科目组）、`save(values, cc_session=)`、`set_enabled(name, enabled, remind=, remind_times=)`、`delete(names)`、`run_now(name)`、`listing()`、`runs(name)`；校验在 `contracts.schedule / defaults / applicable / remind_times`。
- CC 工具 `cowork_*folder*`（`assistant/tools.py:3483`、`3996` 起）转调同一组，另加两道闸：`_task_scope`（任务只能指向本对话公司的账套，`assistant.write_scope`）、`_task_mrerp_setup`（MR.ERP 账套要先定导入菜单 / 默认值）。
- 结论：**全部复用**。新写 `assistant/guided/folder_task.py`（声明 + 薄转调），两道闸照 CC 工具的同名函数调用（不复制规则）。

## 2. Frappe 原生能力

- 不涉及 Desk；规则 DocType 不变。

## 3. 按类通用

- 一个菜单项「定时任务」，第一题「做什么」分支（同盘点、对账的做法）。
- 本地 / 云端 / MR.ERP 三格同一份声明：选项函数按本对话账套的种类出题（Express 出「入账类型 + 默认处理」，MR.ERP 出「现赊」），云端随云端线放开。

## 4. 上下游数据

- 存下的任务到点由小助手跑、推送走现有写账（写入闸、判定都不变）。引导只是把网页表单的值交给 `rules.save`。
- 去向一题把「方向 + 入账类型」（Express）或「方向 + 现赊」（MR.ERP）合成一个选项码（`purchase:service`、`sales:stock`、`purchase:cash`…），后面的默认处理题用现有的 `when.in / starts` 按码出：现赊、抵扣只在 `purchase:*`；科目在 `purchase:service`、`sales:*`；存货处理在 `*:stock`；部门都有（与 `contracts.applicable` 一致）。不用新引擎能力。
- 默认处理各题 `required: False`（空 = 遇到时照现有规则停下问一次，同网页），科目、部门带服务端搜索（科目表最多 2000 行，内存分页）；科目表还没读到时题目下一行事实「正在读」并 `wait`（`options` 已会请电脑读）。
- 修改：各步 `default` = 任务现值（N7 的做法：已有 `rule` + `default`）。
- **立即运行**补两道：先过 `_task_scope`（总表 #53：现在打字路径没有写入闸、没有允许卡），再走允许卡；然后 `rules.run_now`。
- MR.ERP：账套还没定导入菜单 / 默认值时，复核卡退回并给「去做」出口（停下归类 `assistant/stop_exits.py`），等 CW-D-MS 的题类做好后改成卡上直接问。

## 5. 隔离与权限

- 菜单：有定时任务功能（`rules._require` 同一口径）才见。
- 任务选项只列本对话公司账套的任务（同 `folder_tasks` 的公司范围）；存 / 改 / 运行都过 `_task_scope`；服务端 `_connection` 照旧核连接使用权。

## 6. 入口

- CC 对话业务菜单「定时任务」；网页规则页、CC 定时任务页、打字工具不动。

## 7. 共用件与连带回归

- 新文件 `assistant/guided/folder_task.py`；hooks `pearnly_guides` 追加；术语表追加；`shared-parts.toml` 登记。不改 `folder_intake/*`、`assistant/tools.py`（只调用 `_task_scope`、`_task_mrerp_setup`；若主会话要求不调私有名，就在 tools.py 不动的前提下把这两个挪成公开名——那算改别人的文件，先问）。
- 依赖引擎能力：**N8**（时间）、**N1a**（提醒时刻：最多 3 组「提前几天 + 时刻」，用 `questions` 子题）、**N5 + 条件允许卡**（同盘点评估第 12 节第 1 条）、N9（可选步骤）。

## 8. 存储与成本

- 不新增。立即运行会识别新文件（扣费），允许卡下面一行沿用已有词条「新文件将识别（扣费）并推送」（`cowork.folder.runNow.charge`，与打字路径同一句）。

## 9. 删除、作废、追溯

- 删除同网页：连运行记录、文件进度一起删，已建的录入记录保留（服务现状）；允许卡复核列出要删的任务名。

## 10. 文案与官方 UI

- 新增：`guide.business.folder_task`「定时任务」（沿用已有译文）、`guide.open.folder_task`、`guide.step.folder_task.*`。选项与标题尽量沿用 `cowork.folder.* / intake.*` 现有词条（网页表单与 CC 定时任务页已用）。不改已有文案。

## 11. 测试与验收

- 离线：各动作分支；去向码与默认处理题的 `when`；Express / MR.ERP 出题不同；科目搜索、核码按原样；科目表在读给 `wait`；日程 / 提醒非法退回对应步（`contracts` 的原因码）；修改时默认 = 现值；本对话公司以外的任务不出、存不进；立即运行过写入闸 + 允许卡；删除允许卡；确定只存一次。
- 流程（接线后）：实验栈假智能体新建一条 → 改日程 → 立即运行 → 删除。

## 12. 要问主会话的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 允许卡按动作区分（同盘点第 12 节第 1 条） | 同一个扩展两类共用 | — |
| 2 | 引导调用 `assistant/tools.py` 里的 `_task_scope`、`_task_mrerp_setup`（私有名，同包内） | 照调（都在 assistant 域，不复制规则） | 不许就要把它们挪成公开名，动 tools.py |
| 3 | MR.ERP 账套没定菜单 / 默认值时：现在只能「去做」出口，等 CW-D-MS 题类好了再卡上问 | 这样分两步 | — |

## 13. 工作量与排期

- 写码 5–6 小时、机器 1.5 小时；排在盘点之后。
