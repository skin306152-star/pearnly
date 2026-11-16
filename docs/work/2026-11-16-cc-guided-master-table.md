# CC 引导式全覆盖总表（11-16）

用途：**所有窗口报「引导式做到哪」的唯一依据**。行 = 一格（产品 × 模式 × 账本 × 业务），列 = 这一格的引导流程、现在做到哪、缺什么、依赖谁、估时、负责窗口。
口径（用户 11-14、11-16）：引导式为主、对话为辅；「引导式覆盖所有功能」是做完的标准；代录只做采购 / 销售；收款只在 ERP、只做直写；其它只做直写；
Cowork 的对账、盘点、看板等工作空间对象不按公司隔开（用户 11-16）。

依据（读的是 git 对象，`origin/main` = `9e0e102ab`）：
- CC 工具：两产品 hooks `pearnly_assistant_tools` 列的 8 个模块，共 **111 个工具**（第 5 节逐个对到格，一个不漏）。
- 引导引擎与声明：main 上 `pearnly_core/assistant/guide/`（declaration / registry / run / steps）、`pearnly_cowork/assistant/guides.py`、`pearnly_erp_portal/express/cc_guides.py`；
  分支 `pearnly/cc-guided-collection`（e590f7fb4：引擎通用件 + `collection/guide.py`）、`pearnly/cc-guided-phase1`（6e7de94ed：第 1 期 `pearnly_guide` 判定卡）。
- 计划与盘点：`docs/work/2026-11-14-cc-guided-coverage-plan.md`（分支 cc-guided-phase1）、`2026-11-15-cc-guided-p2-choice-points.md`（分支 cc-guided-collection）、
  `2026-11-15-cc-guided-p3-choice-points.md`（分支 cc-guided-p3-choice-points；下文「P3 盘点 #n」指它第 1 节的行号）、共享笔记「代录×引导式-衔接规划-11-16」「引导式-分支3接线方案-11-16」。

## 0. 数字（可核对）

| 项 | 格数 |
|---|---|
| **范围内总格数** | **65**（Cowork 直写 37 · Cowork 代录 6 · ERP 直写 16 · ERP 代录 6） |
| 真机通过 | 0 |
| 真模型通过 | 2（Cowork 直写 采购 / 销售 × 本地 Express；第 1 期，11-14 真模型，在合入列车上、未进 main） |
| 流程通过 | 0 |
| 接线 | 0（分支 3 接线未开工；第 1 期那两格走的是 `pearnly_guide` 工具卡，不是引导运行） |
| 服务端 | 5（Cowork / ERP 代录 采购 / 销售 × 本地 Express 4 格：引导卡出题、开始卡未并；ERP 收款 × 本地 Express 1 格：分支 4 未进 main） |
| 没做 | 58（其中云端 Express 19 格整列 ⇢ 云端线；MR.ERP 18 格里 12 格 ⇢ MR.ERP 线） |
| 范围外（不计入 65） | 第 2 节逐条写依据 |

状态只取一档，取已达到的最高一档：没做 → 服务端（声明 + 服务函数 + 离线测试）→ 接线（对话页能点着走完）→ 流程通过（实验栈假智能体）→ 真模型通过 → 真机通过（测试机 / 真 Express / 真 MR.ERP）。
「列车上」= 已推分支、CI 绿、排在合入列车，未进 main；进 main 之前不算交付。

记号：账本 本 = 本地 Express、云 = 云端 Express、MR = MR.ERP、— = 不分账本（这项业务不读也不写账本）。
「入口」两种都要有：业务菜单一项（`registry.menu`）+ 说一句（模型只能打开引导、把说的话当搜索词，同收款「打字改走引导」）。下表「入口」列只写菜单名与图标。

## 1. 总表（范围内 65 格）

### 1.1 Cowork · 直写（37 格）

| 格 | 业务 | 账本 | 入口（菜单 · 图标） | 要问的题 · 选项来源 | 自动定的 | 复核卡 | 结果卡 | 现在 | 缺什么 | 依赖 | 估时 写码 / 机器 h | 负责 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CW-D-P-L | 采购 | 本 | 采购 · shopping-cart | 交票（file）→ 判定卡全部题（`precheck_source.document`：录不录、方向、待选账套、商品单入账、对方 / 存货 / 科目 / 现赊 / 抵扣 / 服务品税口径 / 仓） | 对话公司、本对话账套、记住的选择、Express 历史 | 单据版复核卡（识别值就地改，接 `write_precheck/review.py`） | 每张一行：已入账（单号）/ 留下（原因） | **真模型通过**（列车上） | 识别值可改的复核卡；A8 新对方、A10 新物料两题；`cowork_edit_document`、`direction / posting_kind / payment` 参数退场（P3 盘点第 4 节 1–5 条） | 接线（分支 3）；P2-1 单据版复核 / 结果卡；CC 桌面版四张小表（A8 / A10） | 已做；余项算在 P2-1 | 引导式 local_38ec22a7 |
| CW-D-P-C | 采购 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端账套的写账通路 | 云端 Express 线（T1–T6 只读实测未做） | 声明不变；`capable()` 判格 1 / 1 | 云端线 → 引导式 |
| CW-D-P-M | 采购 | MR | 同上 | 同上 + MR.ERP 导入菜单 / 默认值两题（现在判定卡不出，P3 盘点 #23） | 同上 | 同上 | 同上 | 没做 | MR.ERP 必填登记；判定卡补 `mrerp_menu / mrerp_default` 题类（ERP 已有）；`payment` 参数退场（#24） | MR.ERP 写账线 | 4–6 / 2 | 引导式 + MR.ERP 线 |
| CW-D-S-L | 销售 | 本 | 销售 · tag | 同采购 | 同采购 | 同采购 | 同采购 | **真模型通过**（列车上） | 同 CW-D-P-L | 同 CW-D-P-L | 同上 | 引导式 |
| CW-D-S-C | 销售 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 同 CW-D-P-C | 云端线 | 1 / 1 | 云端线 → 引导式 |
| CW-D-S-M | 销售 | MR | 同上 | 同上 + MR.ERP 现销 / 赊销（#24，现在是模型参数） | 同上 | 同上 | 同上 | 没做 | 同 CW-D-P-M | MR.ERP 线 | 计入 CW-D-P-M | 引导式 + MR.ERP 线 |
| CW-D-BR-L | 银行对账 | 本 | 银行对账 · landmark | 月份（month）→ 银行流水（file，卡上交）→ 总账（choice：「从 Express 读回」/ 交文件）→ 结果出来后的问题（动态题，`recon_center/presets.answer` 的候选） | 账套 = 本对话账套；月份默认当月（用户 11-16）；银行、科目、期间由文件算出 | 开始前一张：两侧文件 + 扫描件按页付费那一行事实 | 对账结果（状态、差异数）+ 工作簿进对话；在跑时「等」 | 没做 | 声明 + 服务函数（转调 `recon_center/api.start`、`jobs.status`、`presets.answer`）；动态题、结果卡带文件（引擎新能力 N1、N2） | 读回（RB-L）先做；接线；N1 / N2 | 8–10 / 2（三类对账共用一份，见下两行） | **本窗口（P3）** |
| CW-D-BR-C | 银行对账 | 云 | 同上 | 同上（总账从云端读回） | 同上 | 同上 | 同上 | 没做 | 云端读回 | 云端线 | 0.5 / 0.5 | 本窗口 ← 云端线 |
| CW-D-BR-M | 银行对账 | MR | 同上 | 同上，总账只能交文件（MR.ERP 无读回） | 同上 | 同上 | 同上 | 没做 | 「从 Express 读回」选项按账本隐藏 | 无（MR.ERP 线不用动） | 计入 BR-L | 本窗口 |
| CW-D-IR-L | 收入对账 | 本 | 收入对账 · scale | 月份 → 总账（读回 / 交文件）→ 销项税报表（读回 / 交文件）→ 动态题 | 同银行对账 | 同上 | 同上 | 没做 | 同 BR-L（kind income） | 同 BR-L | 计入 BR-L（+2 / 0.5） | 本窗口 |
| CW-D-IR-C | 收入对账 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端读回 | 云端线 | 计入 BR-C | 本窗口 ← 云端线 |
| CW-D-IR-M | 收入对账 | MR | 同上 | 两侧都交文件 | 同上 | 同上 | 同上 | 没做 | 同 BR-M | 无 | 计入 | 本窗口 |
| CW-D-VC-L | 销项税核查 | 本 | 销项税核查 · receipt | 月份 → 销项税报表（读回 / 交文件）→ 销售发票（file） | 同上 | 同上 | 同上 | 没做 | 同 BR-L（kind vat；`cowork_recon_*` 的第三种，覆盖计划漏列） | 同 BR-L | 计入 BR-L（+2 / 0.5） | 本窗口 |
| CW-D-VC-C | 销项税核查 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端读回 | 云端线 | 计入 | 本窗口 ← 云端线 |
| CW-D-VC-M | 销项税核查 | MR | 同上 | 两侧都交文件 | 同上 | 同上 | 同上 | 没做 | 同 BR-M | 无 | 计入 | 本窗口 |
| CW-D-POS-L | 收银日汇总 | 本 | 收银日汇总 · store | POS 导出（file）→ 认列（动态题：每列从表头选、日月顺序、含不含税、状态值；按公司记住，`pos_import/service.preview` → `contracts.apply_answers`）→ 走库存 / 记费用（有明细时） | 账套 = 本对话账套；记过的版式不再问 | 每天一行：日期、首末票号、金额、税额；收款科目题（kind receipt，判定卡已有） | 每天一张：已入账 / 留下 | 没做 | 声明 + 服务函数；认列题由模型填 `answers` 改成卡上选（P3 盘点 #31）；动态题 N1 | 接线；N1 | 6–8 / 2 | 本窗口 |
| CW-D-POS-C | 收银日汇总 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端写账 | 云端线 | 0.5 / 0.5 | 本窗口 ← 云端线 |
| CW-D-POS-M | 收银日汇总 | MR | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | POS 推送现在只接 Express（`cowork_pos_import` 说明） | MR.ERP 线（MR.ERP 的日汇总单型未定） | 待 MR.ERP 线估 | 本窗口 ← MR.ERP 线 |
| CW-D-ST | 盘点 | — | 盘点 · clipboard-list | 新建：模板（结果卡给模板文件）→ Excel（file）→ 标题（text，可空）；记数：盘点单（choice）→ 物料（choice，搜索码 / 条码）+ 数量（number）可连续多行；结束 / 删除走允许卡 | 仓 / 库位默认账面位置 | 差异表（账面、实盘、差异，只列有差异的） | 差异工作簿进对话 | 没做 | 连续录多行（引擎新能力 N4）；删除 / 结束走允许卡（N5）；不写账本 | 接线；N4、N5 | 6–8 / 2 | 本窗口 |
| CW-D-VAT-L | 增值税 ภ.พ.30 | 本 | 增值税 · percent | 月份 → 免税销售（number，可空）→ 上期留抵（number，默认上月草稿第 12 格）→ 零税额行去向（choice） | 账套 = 本对话账套；没有读回时先跑读回（同一张卡里「等」） | 第 1–12 格 + 检查项（不能抵的行） | 草稿工作簿进对话；「草稿」不说已申报 | 没做 | 声明 + 服务函数（转调 `tax.service.vat_draft`）；结果带文件 N2 | 读回 RB-L；接线 | 4–6 / 1.5 | 本窗口 |
| CW-D-VAT-C | 增值税 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端读回 | 云端线 | 0.5 / 0.5 | 本窗口 ← 云端线 |
| CW-D-VAT-M | 增值税 | MR | 同上 | 同上 | — | 同上 | 同上 | 没做 | MR.ERP 侧没有销项 / 进项来源（工具说明「not MR.ERP」） | MR.ERP 线 | 待 MR.ERP 线估 | 本窗口 ← MR.ERP 线 |
| CW-D-WHT | 预扣税 ภ.ง.ด.3 / 53 | — | 预扣税 · receipt | 月份 → 分店（choice，有分店才问）→ 草稿出来后：收款方收入类型（动态题，按工作空间记住）、待确认的行算不算（multi） | 付款公司 = 对话公司（现在给了税号就不核，P3 盘点 #41，要改成只认对话公司） | 两张表的行（收款方、类型、税率、基数、税额） | 草稿工作簿进对话 | 没做 | 动态题 N1；记住范围加「整个工作空间」N3；行修改（#43）放复核卡 | 接线；N1、N3 | 6–8 / 2 | 本窗口 |
| CW-D-RB-L | Express 读回 | 本 | 读回 · download | 月份 → 科目前缀（multi，可空，科目表搜索） | 账套 = 本对话账套 | — （只读） | 四本表进对话；在读「等」；电脑没连 / 要更新一行事实 | 没做 | 声明 + 服务函数；结果带文件 N2；删除走允许卡 N5 | 接线 | 3–4 / 1 | 本窗口 |
| CW-D-RB-C | Express 读回 | 云 | 同上 | 同上 | 同上 | — | 同上 | 没做 | 云端读回通路 | 云端线 | 0.5 / 0.5 | 本窗口 ← 云端线 |
| CW-D-FT-L | 文件夹定时任务 | 本 | 定时任务 · folder-clock | 新建 / 改哪个（choice：新建或已有任务）→ 电脑与文件夹（choice ×2，`folder_intake/api.options`）→ 方向、入账类型（choice）→ 默认处理（现赊、抵扣、科目〔搜索〕、存货、部门）→ 日程（number 日、time）→ 提醒 | 账套 = 本对话账套；改任务时各步默认 = 任务现值 | 一张设置卡（各项，点回那一步） | 已保存；启停 / 立即运行 / 删除另走允许卡 | 没做 | 声明 + 服务函数；时间输入（N8）；立即运行要过写入闸 + 允许卡（#53 现在都没有）；删除允许卡 N5 | 接线；N5、N8 | 8–10 / 2 | 本窗口 |
| CW-D-FT-C | 文件夹定时任务 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端写账 | 云端线 | 0.5 / 0.5 | 本窗口 ← 云端线 |
| CW-D-FT-M | 文件夹定时任务 | MR | 同上 | 同上 + MR.ERP 现赊（payment） | 同上 | 同上 | 同上 | 没做 | MR.ERP 先定菜单 / 默认值才建得了（#49） | 无（服务端已有 MR.ERP 任务） | 计入 FT-L（+1） | 本窗口 |
| CW-D-PD-L | 待处理 / 结果核对 | 本 | 待处理 · list-checks | 哪一项（choice，待处理清单）→ 按类：结果不明「已入账 / 没入账」+ 单号；改票面（复核卡）；删单（允许卡）；导出复核工作簿（multi） | 清单来自 `companies/api.pending`（同侧栏） | 单据版复核卡（同 P / S） | 每项一行 | 没做 | `cowork_resolve_push_result`、`cowork_delete_documents`、`cowork_edit_document` 现在没有允许卡、值经模型（#10、#26、#28）；改走卡上 | 接线；P2-1 复核卡；N5 | 6–8 / 2 | 本窗口 |
| CW-D-PD-C | 待处理 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端推送结果 | 云端线 | 0.5 / 0.5 | 本窗口 ← 云端线 |
| CW-D-PD-M | 待处理 | MR | 同上 | 同上 + MR.ERP 设置类待处理（`pending_api.mrerp_needs`） | 同上 | 同上 | 同上 | 没做 | 同 PD-L | 无 | 计入 PD-L | 本窗口 |
| CW-D-CO | 公司资料 / 知识库 / 记住的选择 | — | 公司资料 · building | 做什么（choice：加 / 改 / 删笔记、存 / 删一条规则）→ 笔记（text）或规则类（choice）+ 值（choice 搜索，按工作月份那本账套主档） | 公司 = 对话公司 | — | 已保存 | 没做 | 声明 + 服务函数；删除允许卡 N5 | 接线；N5 | 4–6 / 1 | 本窗口 |
| CW-D-WK | 任务看板 | — | 看板 · kanban | 看板（choice）→ 做什么（建卡 / 改 / 移动 / 完成 / 评论）→ 卡片（choice 搜索）→ 字段（text / date / choice） | 默认看板 | — | 已保存 | 没做 | 声明 + 服务函数（转调 `work/api`） | 接线 | 6–8 / 1.5 | 本窗口 |
| CW-D-MS | MR.ERP 设置 | MR | （不上菜单，推送时冒出来）· settings | 每类单的导入菜单（choice）、主档默认值（choice 搜索） | 记住的按账套不再问 | — | 已保存 | 没做 | 判定卡补题类 `mrerp_menu / mrerp_default`（ERP 判定卡已有）；现在是模型 + 允许卡 | 引导式（判定卡题类在 `precheck_source`） | 3–4 / 1 | 本窗口（与引导式约定题类形状） |
| CW-D-SG-L | 存货科目组 | 本 | （不上菜单，推送时冒出来）· boxes | 科目组（choice，`connections/service.stock_acc_group`） | 记住的按账套不再问 | — | 已保存 | 没做 | 判定卡补 `stock_group` 题类（ERP 已有）；现在是模型 + 允许卡 | 同 MS | 2–3 / 0.5 | 本窗口 |
| CW-D-SG-C | 存货科目组 | 云 | 同上 | 同上 | 同上 | — | 同上 | 没做 | 云端账套主档 | 云端线 | 0.5 / 0.5 | 本窗口 ← 云端线 |
| CW-D-CN | 连接与账套 | — | 连接 · settings | 哪个连接（choice）→ 重读账套（确定） | — | — | 电脑在线 / 账套数 / 读到哪天 | 没做 | 声明 + 服务函数（`cowork_companion_status`、`cowork_refresh_account_sets`） | 接线 | 2 / 0.5 | 本窗口 |

### 1.2 Cowork · 代录（6 格）

| 格 | 业务 | 账本 | 要问的题 · 选项来源 | 自动定的 | 复核卡 | 结果卡 | 现在 | 缺什么 | 依赖 | 估时 | 负责 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| CW-O-P-L | 采购 | 本 | 同 CW-D-P-L + 同票已写「再录 / 跳过」、子菜单（`operator_form`）、中途框（做法 B） | 同直写；任务的公司与账套 | 单据版复核卡 | 每张一行 + 半截单 | **服务端**（引导卡出题；开始卡未并） | 分支 6 开始卡并卡；P2-1；P2-2 单位 / 现金科目 / 成本口径 / 负库存四题；P2-3 中途带回 | 接线；CC 桌面版新版（P2-3） | 见衔接规划：分支 6 4–6 / 2，P2-1 14–18 / 5–6，P2-2 10–12 / 4，P2-3 6–8 / 3 | 引导式 local_38ec22a7 |
| CW-O-P-C | 采购 | 云 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端代录通路 | 云端线 | 1 / 1 | 云端线 → 引导式 |
| CW-O-P-M | 采购 | MR | 同上 | 同上 | 同上 | 同上 | 没做 | `MRERP_OPERATOR_ENABLED = False`，交单回 `operator_express_only`；MR.ERP 5 个框要真站点探路 | MR.ERP 线 | 不另计 / 实机 2 | MR.ERP 线 → 引导式 |
| CW-O-S-L | 销售 | 本 | 同 CW-O-P-L | 同上 | 同上 | 同上 | **服务端** | 同 CW-O-P-L | 同上 | 同上 | 引导式 |
| CW-O-S-C | 销售 | 云 | 同上 | 同上 | 同上 | 同上 | 没做 | 同 CW-O-P-C | 云端线 | 同上 | 云端线 → 引导式 |
| CW-O-S-M | 销售 | MR | 同上 | 同上 | 同上 | 同上 | 没做 | 同 CW-O-P-M | MR.ERP 线 | 同上 | MR.ERP 线 → 引导式 |

### 1.3 ERP · 直写（16 格）

| 格 | 业务 | 账本 | 入口 | 要问的题 · 选项来源 | 自动定的 | 复核卡 | 结果卡 | 现在 | 缺什么 | 依赖 | 估时 | 负责 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ERP-D-P-L | 采购 | 本 | 采购（菜单 11-14 先拿掉，P2 整链验过再放） | 交票 → 识别 → 收单记录 `I:` → 判定卡（`write_precheck` + `intake_items`：付款、对方、物料、按行入库 / 仓位、科目、做不做）；复核里改印刷合计（`erp_set_printed_total`） | 公司、本对话账套、记住的 | 单据版复核卡 | 每张一行 | 没做 | 直写对话不识别附件（P2-4 新能力）；确定 = 提交 ERP 单 | 接线；P2-1 | 8–10 / 3（P2-4） | guided-erp |
| ERP-D-P-C | 采购 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端写账 | 云端线 | 1 / 1 | 云端线 → guided-erp |
| ERP-D-P-M | 采购 | MR | 同上 | 同上 + MR.ERP 菜单 / 默认值 | 同上 | 同上 | 同上 | 没做 | ERP 的 MR.ERP 开关未开；MR.ERP 必填登记（付款方式、对方档、分店、币种） | MR.ERP 线 | 待 MR.ERP 线估 | guided-erp + MR.ERP 线 |
| ERP-D-S-L | 销售 | 本 | 销售 | 同采购 | 同上 | 同上 | 同上 | 没做 | 同 ERP-D-P-L | 同上 | 计入 P | guided-erp |
| ERP-D-S-C | 销售 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 同上 | 云端线 | 1 / 1 | 云端线 → guided-erp |
| ERP-D-S-M | 销售 | MR | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 同 ERP-D-P-M | MR.ERP 线 | 计入 | guided-erp + MR.ERP 线 |
| ERP-D-COL-L | 收款 | 本 | 收款 · wallet | 公司 → 日期（能录就不问）→ 账套 → 客户（搜索）→ 未收发票（金额多选）→ 现金 / 转账 / 折扣分摊 → 每种转账一张凭证 | 公司、日期、账套、销售员 / 部门 | 收款复核卡（`collection/guide.review`） | RE 号 / 排队 / 未入账 | **服务端**（分支 4 e590f7fb4，未进 main） | 接线；打字收款改走引导（用户 11-16 定，分支 3 之后） | 接线 | 已做；余项在分支 3 | 引导式 local_38ec22a7 |
| ERP-D-COL-C | 收款 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端 RE 写法 | 云端线 | 1 / 1 | 云端线 → 引导式 |
| ERP-D-COL-M | 收款 | MR | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | MR.ERP 收款写法未定 | MR.ERP 线 | 待 MR.ERP 线估 | MR.ERP 线 → 引导式 |
| ERP-D-ST | 盘点 | — | 盘点 · clipboard-list | 新建：仓（choice）→ 标题；记数：盘点单 → 物料（搜索码 / 条码）+ 数量，连续多行；结束（允许卡）→ 生成调整单（确定） | 仓位默认账面位置 | 差异表 | 调整单草稿号；调整单写账走 ERP 单据直写（采购 / 销售同一条推送） | 没做 | N4、N5 | 接线 | 6–8 / 2 | **待分配**（建议本窗口 P3 之后接，与 CW-D-ST 共用题型） |
| ERP-D-SC | 库存收发报表 | — | 库存报表 · file-spreadsheet | 期间（month）→ 仓（choice，可空） | — | — | 报表文件进对话 | 没做 | N2 | 接线 | 2 / 0.5 | 待分配（同上） |
| ERP-D-PD-L | 待处理 / 结果核对 | 本 | 待处理 · list-checks | 哪一项（choice，`erp_pending`）→ 再写一次 / 结果不明「已入账 / 没入账」 | — | 单据版复核卡 | 每项一行 | 没做 | `erp_resolve_push_result` 值经模型；改走卡上 | 接线；P2-1 | 4–6 / 1.5 | guided-erp |
| ERP-D-PD-C | 待处理 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端推送结果 | 云端线 | 0.5 / 0.5 | 云端线 → guided-erp |
| ERP-D-PD-M | 待处理 | MR | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | ERP 的 MR.ERP 开关未开 | MR.ERP 线 | 计入 | guided-erp + MR.ERP 线 |
| ERP-D-CO | 公司资料 / 知识库 | — | 公司资料 · building | 加 / 改 / 删笔记（text；删走允许卡） | 公司 = 对话公司 | — | 已保存 | 没做 | N5 | 接线 | 2–3 / 0.5 | 待分配（建议本窗口，与 CW-D-CO 同题型） |
| ERP-D-ADD | 公司接入（从连接添加） | — | 添加公司 · building-plus | 连接里还没进 ERP 的公司（multi，`erp_connection_companies`） | 主档随公司带入 | 勾选清单 | 已添加 / 同步中 | 没做 | — | 接线；公司接入线（`erp_company_add`） | 2–3 / 0.5 | 待分配（建议公司接入窗口 local_3709c091） |

### 1.4 ERP · 代录（6 格）

| 格 | 业务 | 账本 | 现在 | 缺什么 | 依赖 | 负责 |
|---|---|---|---|---|---|---|
| ERP-O-P-L | 采购 | 本 | **服务端**（引导卡出题；开始卡未并；H3 放票 `I:` 约定已定） | 同 CW-O-P-L（分支 6、P2-1/2/3） | 接线；CC 桌面版新版 | 引导式 local_38ec22a7 |
| ERP-O-P-C | 采购 | 云 | 没做 | 云端代录通路 | 云端线 | 云端线 → 引导式 |
| ERP-O-P-M | 采购 | MR | 没做 | ERP 代录目标写死 Express；MR.ERP 代录关着 | MR.ERP 线 | MR.ERP 线 → 引导式 |
| ERP-O-S-L | 销售 | 本 | **服务端** | 同 ERP-O-P-L | 同上 | 引导式 |
| ERP-O-S-C | 销售 | 云 | 没做 | 同 ERP-O-P-C | 云端线 | 云端线 → 引导式 |
| ERP-O-S-M | 销售 | MR | 没做 | 同 ERP-O-P-M | MR.ERP 线 | MR.ERP 线 → 引导式 |

题、自动定、复核、结果同 1.2；估时在衔接规划里与 Cowork 代录共用一份（同一套声明、`modes: operator`）。

## 2. 范围外（不计格，写依据）

| 组合 | 依据 |
|---|---|
| Cowork 任一模式 × 收款 | 收款只在 ERP（用户 11-14）；Cowork 没有收款服务 |
| Cowork 代录 × 采购 / 销售以外的 15 类（对账三类、日汇总、盘点、增值税、预扣税、读回、定时任务、待处理、公司资料、看板、MR.ERP 设置、科目组、连接） | 代录只做采购 / 销售（用户 11-14）。代录中途冒出的 MR.ERP 设置、科目组题按 CW-D-MS / SG 的题类出，不另成格 |
| ERP 代录 × 收款及其余 | 同上；收款只做直写 |
| ERP × 银行 / 收入对账、销项税核查、日汇总、增值税、预扣税、读回、定时任务、看板 | ERP 版 CC 没有这些工具（hooks `pearnly_assistant_tools` 的 5 个模块里没有）；ERP 的 POS 是自己的页面，不在 CC |
| ERP × MR.ERP 设置、科目组（单独成格） | ERP 判定卡已有 `mrerp_menu / mrerp_default / stock_group` 题类，属于采购 / 销售格内的题 |
| Cowork 直写 × 读回 × MR.ERP | MR.ERP 不能回读（`cowork_express_readback` 说明「MR.ERP account set cannot be read back」） |
| Cowork 直写 × 存货科目组 × MR.ERP | 存货科目组是 Express 的概念；MR.ERP 的对应项在 MR.ERP 默认值（CW-D-MS） |
| 盘点、预扣税、公司资料、看板、连接、库存报表、公司接入 × 分账本 | 不读不写账本，只算一格（「—」） |

## 3. 只读查询工具（归到格里，不另做引导）

用户只要求「每个功能都有引导」；纯查询工具在引导里是**选项来源或结果卡的取数**，不单独成一张卡。打字问照旧直接答（同收款口径「查询照旧直接问」）。
`pearnly_whoami`、`pearnly_tax_id_lookup`（对方税号查询，作为 A8 新对方题的取数）、`cowork_list_documents`、`cowork_document_detail`、`cowork_document_original`、`cowork_recognition_results`、
`cowork_push_results`、`cowork_companies`、`cowork_company_profile`、`cowork_recon_list / status / result`、`cowork_stocktake_list / report / template`、`cowork_folder_tasks / folder_task_runs / folder_task_options / folder_files`、
`cowork_express_readback_status`、`cowork_work_boards / board / card / search`、`cowork_intake_options`、`cowork_companion_status`、`erp_list_documents`、`erp_document_detail / original`、`erp_push_results`、
`erp_companies`、`erp_company_profile`、`erp_connection_companies`、`erp_stocktake_list / report`、`erp_collection_customers / invoices / methods / card / status / list`、`express_operator_status`。
**要主会话定**：这样归类是否认可（认可后这些不再单列进度）。

## 4. 引擎要补的通用能力（归引导式窗口 local_38ec22a7，本窗口发约定，不改它的文件）

| # | 能力 | 用在哪几格 | 现在 |
|---|---|---|---|
| N1 | 动态题：题目与选项在运行中由业务函数给（结果出来才知道的问题：对账结果、POS 认列、预扣税收入类型） | BR / IR / VC、POS、WHT | 声明的 `steps` 是静态的 |
| N2 | 结果卡带文件（工作簿、报表进对话，结果卡上可点开） | 对账三类、VAT、WHT、RB、ST、SC | 结果卡只有一行状态 |
| N3 | 记住范围加「整个工作空间」（预扣税收款方类别、录不录政策现在就是工作空间级） | WHT、CW-D-P/S 录不录题 | `REMEMBER_SCOPES` 只有 company / company_party / ledger |
| N4 | 连续录多行（一张卡上「物料搜索 + 数量」录一行加一行，可撤最后一行） | CW-D-ST、ERP-D-ST | 没有 |
| N5 | 删除 / 结束类的确定走允许卡样式（只有「拒绝 / 允许」） | 删除类全部（对账、盘点、草稿、读回、任务、笔记、规则、单据） | 引导确定只有「确定」 |
| N8 | 时间输入（HH:MM） | FT | `INPUTS` 没有 time；可退成 text + check |

（N6、N7 盘点时并入已有能力：在跑的长任务用结果卡 `wait`；改已有对象时默认值用 `rule: auto` 的 `default`。）

## 5. 111 个工具 → 格（逐个对到，一个不漏）

| 模块（个数） | 工具 → 格 |
|---|---|
| `pearnly_core/assistant/tools.py`（10） | `ask_user` → 所有格：业务上要人定的题改由引导卡出、`ask_user` 只留非业务的澄清（第 1 期起逐类退场）；`pearnly_precheck`、`pearnly_precheck_answer`（口头作答，退场）、`pearnly_write_plan`、`pearnly_write_start`、`pearnly_operator_month`（并进开始卡）→ 采购 / 销售各格（两产品、两模式）；`pearnly_file_conversation`、`pearnly_conversation_ledger` → 所有格的公司 / 账套题（收款引导已有同样一步）；`pearnly_whoami`、`pearnly_tax_id_lookup` → 第 3 节 |
| `pearnly_core/express_operator/tools.py`（2） | `express_operator_control` → 代录 12 格（中途「继续 / 停止」题卡，P2-3）；`express_operator_status` → 第 3 节 |
| Cowork `assistant/tools.py`（61） | 采购 / 销售（CW-D/O-P/S）：`cowork_recognize_attachments`、`cowork_intake_options`、`cowork_push_documents`、`cowork_choose_document_policy`、`cowork_edit_document`、`cowork_document_detail`、`cowork_document_original`、`cowork_recognition_results`、`cowork_list_documents`；MR.ERP 设置（CW-D-MS）：`cowork_mrerp_setup`；科目组（CW-D-SG）：`cowork_stock_group`；连接（CW-D-CN）：`cowork_companion_status`、`cowork_refresh_account_sets`；日汇总（POS）：`cowork_pos_import`、`cowork_pos_layout`；待处理（CW-D-PD）：`cowork_push_results`、`cowork_resolve_push_result`、`cowork_delete_documents`、`cowork_export_review_workbook`；读回（RB）：`cowork_express_readback`、`cowork_express_readback_status`、`cowork_express_readback_delete`；对账（BR / IR / VC，按 kind）：`cowork_recon_start`、`cowork_recon_status`、`cowork_recon_list`、`cowork_recon_result`、`cowork_recon_answer`、`cowork_recon_presets`、`cowork_recon_export`、`cowork_recon_delete`；增值税（VAT）：`cowork_vat_draft`、`cowork_tax_draft_delete`（与 WHT 共用）；预扣税（WHT）：`cowork_wht_draft`、`cowork_wht_answer`；盘点（CW-D-ST）：`cowork_stocktake_list`、`_template`、`_create`、`_report`、`_count`、`_close`、`_export`、`_delete`（8）；看板（WK）：`cowork_work_boards`、`_board`、`_card`、`_create_card`、`_update_card`、`_move_card`、`_comment`、`_search`（8）；定时任务（FT）：`cowork_folder_task_options`、`cowork_folder_files`、`cowork_folder_fetch`、`cowork_save_folder_task`、`cowork_save_folder_tasks`、`cowork_set_folder_task_enabled`、`cowork_set_folder_tasks_enabled`、`cowork_delete_folder_tasks`、`cowork_folder_tasks`、`cowork_run_folder_task`、`cowork_folder_task_runs`（11） |
| Cowork `assistant/company_tools.py`（7） | 待处理（CW-D-PD）：`cowork_pending`；公司资料（CW-D-CO）：`cowork_companies`、`cowork_company_profile`、`cowork_company_note_save`、`cowork_company_note_remove`、`cowork_company_choice_save`、`cowork_company_choice_remove` |
| ERP `collection/cc_tools.py`（10） | 收款（ERP-D-COL）：`erp_collection_customers / invoices / methods / draft / voucher / card / push / status / list / withdraw`（打字路径；引导里由 `collection/guide.py` 直接转调同一组服务） |
| ERP `express/cc_tools.py`（8） | 采购 / 销售（ERP-D/O-P/S）：`erp_list_documents`、`erp_document_detail`、`erp_document_original`、`erp_set_printed_total`；待处理（ERP-D-PD）：`erp_pending`、`erp_push_documents`（再写一次）、`erp_push_results`、`erp_resolve_push_result` |
| ERP `inventory/cc_tools.py`（7） | 盘点（ERP-D-ST）：`erp_stocktake_list / report / create / count / close / adjust`；库存报表（ERP-D-SC）：`erp_stock_card_export` |
| ERP `masters/cc_tools.py`（6） | 公司资料（ERP-D-CO）：`erp_companies`、`erp_company_profile`、`erp_company_note_save`、`erp_company_note_remove`；公司接入（ERP-D-ADD）：`erp_connection_companies`、`erp_company_add` |

合计 10 + 2 + 61 + 7 + 10 + 8 + 7 + 6 = **111**，没有对不到格的工具（脚本按源码逐个核过）。第 1 期分支上的 `pearnly_guide`（判定卡）不在 main，进 main 后归采购 / 销售各格。
覆盖计划第 4 节「只能靠打字办的事」漏了两项，本表已补：销项税核查（`cowork_recon_*` 的 kind vat）、ERP 公司接入（`erp_connection_companies / erp_company_add`）。

## 6. 并行分组（按「不碰同一批文件」）与先后

| 组 | 负责 | 碰的文件（独占） | 格 | 先后依赖 |
|---|---|---|---|---|
| A 引擎与采购 / 销售 | 引导式 local_38ec22a7 | `pearnly_core/assistant/guide/*`、`guide_tool.py`、`sessions.py`、`write_precheck/*`、`express_operator/*` 开始卡、`frontend/assistant/**/guide-*`、两产品采购 / 销售声明（`cowork/assistant/guides.py`、`erp/express/cc_guides.py`） | CW-D-P/S、CW-O-*、ERP-O-*、ERP-D-COL；N1–N8 | 第 1 期合入 → **分支 3 接线**（所有格「接线」档的前提）→ 分支 6 → P2-1 → P2-2 → P2-3 |
| B ERP 采购 / 销售直写 | guided-erp | ERP `express/precheck.py` 以外的直写收单（`integrations/intake` 直写模式、`purchasing/entry`、`sales/entry` 确定分支）、ERP 待处理 | ERP-D-P/S、ERP-D-PD | 等 A 的 P2-1（单据版复核卡 / 出题源）；服务端可先做 |
| C Cowork 常规功能（P3） | **本窗口** | 每类一个新的产品声明模块放在各自域包：`readback/cc_guide.py`、`recon_center/cc_guide.py`、`pos_import/cc_guide.py`、`stocktake/cc_guide.py`、`tax/cc_guide.py`、`folder_intake/cc_guide.py`、`history` / `companies/cc_guide.py`、`work/cc_guide.py`、`connections/cc_guide.py`；Cowork `hooks.py` 的 `pearnly_guides` 一行（只追加）、Cowork 术语表（只追加） | CW-D 除 P/S 外 31 格（含云 / MR 列） | 服务端声明与离线测试**不等接线**，可立即做；接线档等 A 的分支 3；用到 N1–N8 的那一步等 A 交付对应能力（先按约定写、测试用替身） |
| D ERP 非写账类 | 待分配（建议 C 之后由本窗口接） | ERP `inventory/cc_guide.py`、`masters/cc_guide.py`（新） | ERP-D-ST、SC、CO、ADD | 同 C；ADD 与公司接入窗口 local_3709c091 对齐 |
| E 云端 Express | 云端线 | 云端通路 | 19 个「云」格 | T1–T6 只读实测 → 通路；格的声明不变，`capable()` 放开即转档 |
| F MR.ERP | MR.ERP 写账线 | MR.ERP 写账 | 18 个「MR」格里 12 个依赖它（另 6 格 BR / IR / VC / FT / PD / MS 不依赖） | MR.ERP 必填登记、收款写法、代录开关、POS / 增值税来源 |
| G CC 桌面版 | CC 桌面版窗口 | `companion/` | 代录 P2-3、A8 / A10 小表 | 新版发布 |

**C 组施工顺序（本窗口）**：读回 → 银行对账 → 收入对账 + 销项税核查 → 增值税 → 收银日汇总 → 盘点 → 预扣税 → 定时任务 → 待处理 → 公司资料 / 看板 / MR.ERP 设置 / 科目组 / 连接。
与任务书建议的差别只有一处：**读回提前到第一个**——对账三类与增值税的「总账 / 销项税报表从 Express 读回」一步直接调读回的服务函数，先做读回这两类不返工；读回本身最小（3–4 小时）。
C 组合计约写码 60–75 小时、机器 15–18 小时（不含接线后的前端与流程，那部分每类约 1–2 / 1 小时）。

## 7. 要主会话定的事

1. 第 3 节：只读查询工具归到格里、不单独成卡，认不认。
2. 第 6 节 D 组（ERP 盘点、库存报表、公司资料、公司接入 4 格）谁做：建议本窗口在 C 组之后接，公司接入格与 local_3709c091 对齐。
3. C 组顺序把读回提前到第一个。
4. 声明放各自域包（`<域>/cc_guide.py`）而不是都堆在 `cowork/assistant/guides.py`：避免和 A 组（采购 / 销售声明在那个文件）碰同一文件；hooks 的 `pearnly_guides` 列表只追加。
