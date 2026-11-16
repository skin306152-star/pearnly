# 需求影响评估：CC 引导式 · 设置类（Cowork 公司资料 / 看板 / MR.ERP 设置 / 科目组）与 ERP 库存报表、公司资料、公司接入

> 总表 CW-D-CO、CW-D-WK、CW-D-MS、CW-D-SG-L / SG-C，ERP-D-SC、ERP-D-CO、ERP-D-ADD。基线同盘点。一类一份声明，一并评估。

## 0. 需求原文与理解

- 原话：同盘点（用户 11-14 / 11-16「引导式覆盖所有功能」；主会话 11-16 成熟流程优先、删除走允许卡）。
- 理解（每项一张设置卡，选项全来自产品已有接口）：
  - **Cowork 公司资料**：做什么（加笔记 / 改笔记 / 删笔记 / 设为全公司规则 / 删规则）→ 笔记（单选，改、删时）+ 正文（文本）或规则（单选，来自公司资料里的「记住的选择」）→ 复核 → 确定；删除走允许卡。公司 = 对话公司。
  - **Cowork 任务看板**：做什么（建卡 / 改卡 / 移动 / 完成或取消完成 / 评论）→ 看板（默认看板预先定下）→ 卡片（服务端搜索）→ 列表 / 各字段（标题、说明、开始、到期、提醒、重复，都可选）/ 评论正文 → 确定。
  - **MR.ERP 设置、存货科目组**：不上菜单，推送前冒出来（总表原文）。是采购 / 销售判定卡里的题类 `mrerp_menu / mrerp_default / stock_group`（ERP 判定卡已有同题类），不是独立的引导。
  - **ERP 收发存报表**：报表样式（合并全部仓 / 汇总 / 分仓）→ 格式（Excel / PDF）→ 起止日期（推荐本对话月份首末日）→ 物料范围、仓范围（可空，仓范围只在分仓时问）→ 确定 → 文件进对话。只读。
  - **ERP 公司资料**：加 / 改 / 删笔记（同 Cowork 那一张的笔记部分），对话公司。
  - **ERP 公司接入**：连接里还没进 ERP 的公司（多选，服务端搜索）→ 复核（勾的清单）→ 确定 → 已添加 / 同步中。
- 不在范围：看板的清单、附件、标签、成员、自动化（打字和看板页照旧）；从一张单据新建公司规则（要从单据算对方，属于采购 / 销售判定卡）；公司页、看板页、ERP 公司页、收发存页本身都不动。

## 1. 现有同类与复用

| 格 | 网页现有 | 引导转调 | 新写 |
| --- | --- | --- | --- |
| CW-D-CO | 公司页笔记、规则（`companies/api.note_save / note_delete / choice_promote / choice_forget`，CC 工具 `cowork_company_*` 同一组） | 同左；选项来自 `companies.service.profile` | `assistant/guided/company.py` |
| CW-D-WK | 看板全套（`work/api.py`：`board / create / update / move / toggle_done / comment / search`） | 同左 | `assistant/guided/work.py` |
| CW-D-MS / SG | 待处理页 MR.ERP 待设（`pending_api.mrerp_needs / mrerp_answer`）、连接页科目组（`connections/service.stock_acc_group / choose_stock_acc_group`） | 同左 | 判定卡题类（见第 12 节第 1 条） |
| ERP-D-SC | 收发存页导出（`inventory/card_export.build`，CC 工具 `erp_stock_card_export` 同一个） | 同左 | `inventory/cc_guide.py` 追加一份 + `inventory/guide_card.py` |
| ERP-D-CO | ERP 公司页笔记（`masters/service.note_save / note_delete`，CC 工具 `erp_company_note_*`） | 同左 | `masters/cc_guide.py` + `masters/guide.py` |
| ERP-D-ADD | 公司页「从连接添加」（`masters/service.pending / create_many`，CC 工具 `erp_connection_companies / erp_company_add`） | 同左 | 同上文件里第二份声明 |

结论：**全部复用**，不改业务代码。

## 2. Frappe 原生能力

- 不涉及。

## 3. 按类通用

- Cowork 与 ERP 的「公司资料 · 笔记」用同一业务 id `company`、同一套步骤 key（`action / note / text`）；Cowork 多两项规则动作。
- 「做什么 + 对象 + 值」的形状与盘点、定时任务一致；删除类统一走允许卡（等同一个按动作区分的扩展）。

## 4. 上下游数据

- 公司规则：设为全公司后推送判定照现有规则套用（`saved_choices`）；删规则后推送回到账套的选择或再问。
- 看板：改卡要带卡片的 `modified`（服务端乐观锁，别处改过就退回卡片那一步，原因照服务）。
- 收发存：只读；权限、成本可见、范围由 `card_export.build` 核。
- 公司接入：建好排主档同步（服务现状），结果卡「同步中」照服务回包。与公司接入窗口 local_3709c091 对齐：它改 `masters/service.pending / create_many` 时，引导跟着用同一个，不另写。

## 5. 隔离与权限

- Cowork 公司资料：`service.require_write`；公司 = 对话公司。看板：看板权限照 `work/api`（不按公司隔开，用户 11-16）。
- ERP 公司资料：写权限同 CC 工具；收发存：库存读取 + 成本可见照服务；公司接入：只给能建公司的人（ERP 管理员，同公司页）。

## 6. 入口

- CC 对话业务菜单各一项：「公司资料」「任务看板」（Cowork），「收发存报表」「公司资料」「添加公司」（ERP）。MR.ERP 设置、科目组不上菜单。网页各页、打字工具不动。

## 7. 共用件与连带回归

- 新文件见第 1 节；两产品 hooks 追加；术语表追加；`shared-parts.toml` 登记（ERP 公司接入如命中公司接入的共用件，补 not_entries 并说明）。
- 依赖引擎：N2（收发存文件）、N5 + 条件允许卡（删笔记、删规则）、N9（看板可选字段）。

## 8. 存储与成本

- 不新增。

## 9. 删除、作废、追溯

- 删笔记、删规则走允许卡，复核卡列出删的那一条；服务照旧记谁、何时（现状）。

## 10. 文案与官方 UI

- 新增：各业务 `guide.business.* / guide.open.* / guide.step.*`；动作与字段名沿用公司页、看板页、收发存页已有词条（「设为全公司」「完成」「评论」「合并全部仓」…）。不改已有文案。

## 11. 测试与验收

- 每份声明一份离线测试（照盘点）：选项来源、按原样核、可选字段、删除允许卡、服务拒了退回哪一步；ERP 都在对话公司下执行；公司接入只给管理员、多选按原样核、确定一次建完。

## 12. 要问主会话的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | MR.ERP 设置、科目组是采购 / 销售判定卡里的题类，出题源 `assistant/precheck_source.py` 是引导式窗口（A 组）的文件 | 我在新文件 `assistant/guided/push_setup.py` 写选项与作答函数（转调 `pending_api.mrerp_needs / mrerp_answer`、`connections.service.stock_acc_group / choose_stock_acc_group`），A 组在 `precheck_source` 按题类接一行 | 由我改 `precheck_source` 就要 A 组让出那个文件 |
| 2 | 看板「改卡」字段：只做标题、说明、日期、提醒、重复（清单、标签、成员、附件不做） | 这样（其余照打字和看板页） | 全做约 +3 小时 |
| 3 | ERP 公司接入与 local_3709c091 的分工 | 我写声明和测试，接口变化跟它 | — |
| 4 | 公司资料里「从单据新建规则」不进这张卡（属于采购 / 销售判定卡） | 不进 | — |

## 13. 工作量与排期

- 写码：Cowork 公司资料 2–3、看板 4–5、MR.ERP 设置 + 科目组 2–3（题类函数）、ERP 收发存 2、ERP 公司资料 1–2、公司接入 1–2，合计约 13–17 小时；机器约 3 小时。
- 顺序：ERP 公司资料 + 公司接入（小，先交）→ Cowork 公司资料 → 收发存 → 看板 → MR.ERP 设置 / 科目组（等第 12 节第 1 条）。
