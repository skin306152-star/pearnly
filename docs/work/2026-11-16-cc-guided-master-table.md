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

## 一句话现状（11-16）

范围内 65 格：真机 0、真模型 2、服务端 26、没做 37；引擎（题卡、复核、确定、失效）已在 main，**所有格进「接线」档都卡在分支 3 接线**（引导式窗口在做）；
大多数格在 LINE 或网页上已有同一流程，CC 这边主要是「转调同一组服务函数 + 卡片」，复用后的估时见第 1.5 节。

**按现在的并行分组，各组做完的时间合计**（写码 h / 机器 h，机器 = 闸门、候选离线、流程、真模型；各组同时跑）：

| 组 | 负责 | 写码 | 机器 | 另需 |
|---|---|---|---|---|
| A 引擎接线与采购 / 销售、代录（已拆 5 线，见 6.2） | A1 引导式 local_38ec22a7 · A2 guided-frontend local_1f7ac454 · A3 guided-four-questions local_d99fabda · A4 guided-erp · A5 cc-desktop-takeover local_27c53845 | 50–64（关键路径 A1 13–16） | 20–21 | 测试机实机 6–8；CC 桌面版一侧另估 |
| B ERP 采购 / 销售直写、ERP 待处理 | guided-erp local_f0882fd0 | 12–16 | 4.5 | — |
| C1 读回、对账三类、增值税、日汇总、预扣税 + 引擎通用能力 N1–N10 + 总表 | 本窗口（P3） | 余 12–15（读回、对账服务端已推）+ 引擎已推 | 5 | 接线后每类流程 + 真模型约 1 / 1 |
| C2 盘点、定时任务、待处理、设置类（公司资料 / 看板 / MR.ERP 设置 / 科目组 / 连接） | guided-cowork-2 local_3b58bd71 | 25–30 | 7–8 | — |
| D ERP 盘点、库存报表、公司资料、公司接入 | guided-cowork-2 local_3b58bd71（C2 之后） | 6–9 | 2.5 | — |
| E 云端 Express 19 格 | 云端线 local_8d6eed55 | 共同前置 G0 通道约 8（机器）；之后每格在 Z1 真写 / 跑一次 0.3–1 | | 用户到场两次；代录另要 Express 登录账号（第 6.1 节） |
| F MR.ERP 12 格（依赖它的） | MR.ERP 线 | 每格放开约 0.5–1 / 0.5（写法由 MR.ERP 线另估） | | 真站点探路 2 |

最长的一条是 A（关键路径：分支 3 接线 → 分支 6 → P2-1 → P2-2 → P2-3）；C、D 的服务端不等 A，接线档等 A 的分支 3。

## 0. 数字（可核对）

| 项 | 格数 |
|---|---|
| **范围内总格数** | **65**（Cowork 直写 37 · Cowork 代录 6 · ERP 直写 16 · ERP 代录 6） |
| 真机通过 | 0 |
| 真模型通过 | 2（Cowork 直写 采购 / 销售 × 本地 Express；第 1 期，11-14 真模型，在合入列车上、未进 main） |
| 流程通过 | 0 |
| 接线 | 0（分支 3 接线未开工；第 1 期那两格走的是 `pearnly_guide` 工具卡，不是引导运行） |
| 服务端 | 26（ERP 直写采购 / 销售 × 本地 2 格 11-16 18:30 guided-erp 报（未闭环）；C2 / D 组 11-16 18:05 报 9 格：Cowork / ERP 盘点、MR.ERP 设置、科目组 × 本地、Cowork / ERP 公司资料、公司接入、库存报表、看板，其中 2 格未闭环（MR.ERP 设置、看板）；Cowork / ERP 代录 采购 / 销售 × 本地 Express 4 格：引导卡出题、开始卡未并；ERP 收款 × 本地 Express 1 格：分支 4 未进 main；Cowork 读回 × 本地 1 格、对账三类 × 本地 / MR.ERP 6 格、增值税 × 本地 1 格、收银日汇总 × 本地 1 格（未闭环：推送接单据引导要等 documents 源进 main）、预扣税 1 格（未闭环：待确认行逐行改）：11-16 分支已推） |
| 没做 | 37（其中云端 Express 19 格整列 ⇢ 云端线；MR.ERP 18 格里 12 格 ⇢ MR.ERP 线） |
| 范围外（不计入 65） | 第 2 节逐条写依据 |

状态只取一档，取已达到的最高一档：没做 → 服务端（声明 + 服务函数 + 离线测试）→ 接线（对话页能点着走完）→ 流程通过（实验栈假智能体）→ 真模型通过 → 真机通过（测试机 / 真 Express / 真 MR.ERP）。
「列车上」= 已推分支、CI 绿、排在合入列车，未进 main；进 main 之前不算交付。

记号：账本 本 = 本地 Express、云 = 云端 Express、MR = MR.ERP、— = 不分账本（这项业务不读也不写账本）。
「入口」两种都要有：业务菜单一项（`registry.menu`）+ 说一句（模型只能打开引导、把说的话当搜索词，同收款「打字改走引导」）。下表「入口」列只写菜单名与图标。

## 1. 总表（范围内 65 格）

### 1.1 Cowork · 直写（37 格）

| 格 | 业务 | 账本 | 入口（菜单 · 图标） | 要问的题 · 选项来源 | 自动定的 | 复核卡 | 结果卡 | 现在 | 缺什么 | 依赖 | 估时 写码 / 机器 h | 负责 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CW-D-P-L | 采购 | 本 | 采购 · shopping-cart | 交票（file）→ 判定卡全部题（`precheck_source.document`：录不录、方向、待选账套、商品单入账、对方 / 存货 / 科目 / 现赊 / 抵扣 / 服务品税口径 / 仓） | 对话公司、本对话账套、记住的选择、Express 历史 | 单据版复核卡（识别值就地改，接 `write_precheck/review.py`） | 每张一行：已入账（单号）/ 留下（原因） | **真模型通过**（列车上）；菜单路径服务端（documents 源）`pearnly/cc-guide-documents` 042f39436，列车 F，未接线 | 识别值可改的复核卡；A8 新对方、A10 新物料两题；`cowork_edit_document`、`direction / posting_kind / payment` 参数退场（P3 盘点第 4 节 1–5 条）；四题：Cowork 全部入口都不问（缺 CC 心跳补物料单位 / 结存 / 负库存设置 / ACCNUM00 + Cowork 判定卡接线），见 1.6 | 接线（分支 3）；P2-1 单据版复核 / 结果卡；CC 桌面版四张小表（A8 / A10） | 已做；余项算在 P2-1 | 引导式 local_38ec22a7 |
| CW-D-P-C | 采购 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端账套的写账通路 | 云端 Express 线（T1–T6 只读实测未做） | 声明不变；`capable()` 判格 1 / 1 | 云端线 → 引导式 |
| CW-D-P-M | 采购 | MR | 同上 | 同上 + MR.ERP 导入菜单 / 默认值两题（现在判定卡不出，P3 盘点 #23） | 同上 | 同上 | 同上 | 没做 | MR.ERP 必填登记；判定卡补 `mrerp_menu / mrerp_default` 题类（ERP 已有）；`payment` 参数退场（#24） | MR.ERP 写账线 | 4–6 / 2 | 引导式 + MR.ERP 线 |
| CW-D-S-L | 销售 | 本 | 销售 · tag | 同采购 | 同采购 | 同采购 | 同采购 | **真模型通过**（列车上）；菜单路径服务端（documents 源）`pearnly/cc-guide-documents` 042f39436，列车 F，未接线 | 同 CW-D-P-L；同 CW-D-P-L 四题现状，见 1.6 | 同 CW-D-P-L | 同上 | 引导式 |
| CW-D-S-C | 销售 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 同 CW-D-P-C | 云端线 | 1 / 1 | 云端线 → 引导式 |
| CW-D-S-M | 销售 | MR | 同上 | 同上 + MR.ERP 现销 / 赊销（#24，现在是模型参数） | 同上 | 同上 | 同上 | 没做 | 同 CW-D-P-M | MR.ERP 线 | 计入 CW-D-P-M | 引导式 + MR.ERP 线 |
| CW-D-BR-L | 银行对账 | 本 | 银行对账 · landmark | 月份（month）→ 银行流水（file，卡上交）→ 总账（choice：「从 Express 读回」/ 交文件）→ 结果出来后的问题（动态题，`recon_center/presets.answer` 的候选） | 账套 = 本对话账套；月份默认当月（用户 11-16）；银行、科目、期间由文件算出 | 开始前一张：两侧文件 + 扫描件按页付费那一行事实 | 对账结果（状态、差异数）+ 工作簿进对话；在跑时「等」 | **服务端**（`pearnly/cc-guided-recon` d88ac7a46，`assistant/guided/recon.py`，19 条离线测试；CI 待查） | 声明 + 服务函数（转调 `recon_center/api.start`、`jobs.status`、`presets.answer`）；动态题、结果卡带文件（引擎新能力 N1、N2） | 读回（RB-L）先做；接线；N1 / N2 | 8–10 / 2（三类对账共用一份，见下两行） | **本窗口（P3）** |
| CW-D-BR-C | 银行对账 | 云 | 同上 | 同上（总账从云端读回） | 同上 | 同上 | 同上 | 没做 | 云端读回 | 云端线 | 0.5 / 0.5 | 本窗口 ← 云端线 |
| CW-D-BR-M | 银行对账 | MR | 同上 | 同上，总账只能交文件（MR.ERP 无读回） | 同上 | 同上 | 同上 | **服务端**（`pearnly/cc-guided-recon` d88ac7a46，`assistant/guided/recon.py`，19 条离线测试；CI 待查） | 「从 Express 读回」选项按账本隐藏 | 无（MR.ERP 线不用动） | 计入 BR-L | 本窗口 |
| CW-D-IR-L | 收入对账 | 本 | 收入对账 · scale | 月份 → 总账（读回 / 交文件）→ 销项税报表（读回 / 交文件）→ 动态题 | 同银行对账 | 同上 | 同上 | **服务端**（`pearnly/cc-guided-recon` d88ac7a46，`assistant/guided/recon.py`，19 条离线测试；CI 待查） | 同 BR-L（kind income） | 同 BR-L | 计入 BR-L（+2 / 0.5） | 本窗口 |
| CW-D-IR-C | 收入对账 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端读回 | 云端线 | 计入 BR-C | 本窗口 ← 云端线 |
| CW-D-IR-M | 收入对账 | MR | 同上 | 两侧都交文件 | 同上 | 同上 | 同上 | **服务端**（`pearnly/cc-guided-recon` d88ac7a46，`assistant/guided/recon.py`，19 条离线测试；CI 待查） | 同 BR-M | 无 | 计入 | 本窗口 |
| CW-D-VC-L | 销项税核查 | 本 | 销项税核查 · receipt | 月份 → 销项税报表（读回 / 交文件）→ 销售发票（file） | 同上 | 同上 | 同上 | **服务端**（`pearnly/cc-guided-recon` d88ac7a46，`assistant/guided/recon.py`，19 条离线测试；CI 待查） | 同 BR-L（kind vat；`cowork_recon_*` 的第三种，覆盖计划漏列） | 同 BR-L | 计入 BR-L（+2 / 0.5） | 本窗口 |
| CW-D-VC-C | 销项税核查 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端读回 | 云端线 | 计入 | 本窗口 ← 云端线 |
| CW-D-VC-M | 销项税核查 | MR | 同上 | 两侧都交文件 | 同上 | 同上 | 同上 | **服务端**（`pearnly/cc-guided-recon` d88ac7a46，`assistant/guided/recon.py`，19 条离线测试；CI 待查） | 同 BR-M | 无 | 计入 | 本窗口 |
| CW-D-POS-L | 收银日汇总 | 本 | 收银日汇总 · store | POS 导出（file）→ 认列（动态题：每列从表头选、日月顺序、含不含税、状态值；按公司记住，`pos_import/service.preview` → `contracts.apply_answers`）→ 走库存 / 记费用（有明细时） | 账套 = 本对话账套；记过的版式不再问 | 每天一行：日期、首末票号、金额、税额；收款科目题（kind receipt，判定卡已有） | 每天一张：已入账 / 留下 | **服务端（未闭环）**（`pearnly/cc-guided-pos` 7418018a8，`assistant/guided/pos.py`，11 条离线测试；建好票后接单据引导推送，等 documents 源进 main） | 声明 + 服务函数；认列题由模型填 `answers` 改成卡上选（P3 盘点 #31）；动态题 N1 | 接线；N1 | 6–8 / 2 | 本窗口 |
| CW-D-POS-C | 收银日汇总 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端写账 | 云端线 | 0.5 / 0.5 | 本窗口 ← 云端线 |
| CW-D-POS-M | 收银日汇总 | MR | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | POS 推送现在只接 Express（`cowork_pos_import` 说明） | MR.ERP 线（MR.ERP 的日汇总单型未定） | 待 MR.ERP 线估 | 本窗口 ← MR.ERP 线 |
| CW-D-ST | 盘点 | — | 盘点 · clipboard-list | 新建：模板（结果卡给模板文件）→ Excel（file）→ 标题（text，可空）；记数：盘点单（choice）→ 物料（choice，搜索码 / 条码）+ 数量（number）可连续多行；结束 / 删除走允许卡 | 仓 / 库位默认账面位置 | 差异表（账面、实盘、差异，只列有差异的） | 差异工作簿进对话 | **服务端**（`pearnly/cc-guided-stocktake` 7e43c8d67：结束、删除用允许卡） | 连续录多行（引擎新能力 N4）；删除 / 结束走允许卡（N5）；不写账本 | 接线；N4、N5 | 6–8 / 2 | guided-cowork-2 local_3b58bd71 |
| CW-D-VAT-L | 增值税 ภ.พ.30 | 本 | 增值税 · percent | 月份 → 免税销售（number，可空）→ 上期留抵（number，默认上月草稿第 12 格）→ 零税额行去向（choice） | 账套 = 本对话账套；没有读回时先跑读回（同一张卡里「等」） | 第 1–12 格 + 检查项（不能抵的行） | 草稿工作簿进对话；「草稿」不说已申报 | **服务端**（`pearnly/cc-guided-vat` 845ee1b08，`assistant/guided/vat.py`，9 条离线测试；CI 待查）；结果卡列各项检查与数目（只读：栏数等于 Express 记录，改在 Express、再回读重出；`cc-guided-wht-review` 5125f1b03） | 声明 + 服务函数（转调 `tax.service.vat_draft`）；结果带文件 N2 | 读回 RB-L；接线 | 4–6 / 1.5 | 本窗口 |
| CW-D-VAT-C | 增值税 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端读回 | 云端线 | 0.5 / 0.5 | 本窗口 ← 云端线 |
| CW-D-VAT-M | 增值税 | MR | 同上 | 同上 | — | 同上 | 同上 | 没做 | MR.ERP 侧没有销项 / 进项来源（工具说明「not MR.ERP」） | MR.ERP 线 | 待 MR.ERP 线估 | 本窗口 ← MR.ERP 线 |
| CW-D-WHT | 预扣税 ภ.ง.ด.3 / 53 | — | 预扣税 · receipt | 月份 → 分店（choice，有分店才问）→ 草稿出来后：收款方收入类型（动态题，按工作空间记住）、待确认的行算不算（multi） | 付款公司 = 对话公司（现在给了税号就不核，P3 盘点 #41，要改成只认对话公司） | 两张表的行（收款方、类型、税率、基数、税额） | 草稿工作簿进对话 | **服务端（未闭环：复核卡改一格要 guided-erp 的 run.edit 对表单源分派 + A2 行内可改复核表，进 main 后在声明加 edit）**（`pearnly/cc-guided-wht` a68f22e7b + `cc-guided-wht-review` b553ac423：出草稿 → 复核卡两张表逐行可改〔未定所得类型的收款方、待确认行 8 格〕→ 确定出工作簿；用户 11-16 定在 CC 里逐行复核、网页不另加页面） | 动态题 N1；记住范围加「整个工作空间」N3；行修改（#43）放复核卡 | 接线；N1、N3 | 6–8 / 2 | 本窗口 |
| CW-D-RB-L | Express 读回 | 本 | 读回 · download | 账套（本对话账套是 Express 就不问）→ 月份（推荐对话月份、点了才算）；科目前缀不问（主会话 11-16） | 账套 = 本对话账套 | — （只读） | 四本表进对话；在读「等」；电脑没连 / 要更新一行事实 | **服务端**（`pearnly/cc-guided-readback` 5cf37c2d3，`assistant/guided/readback.py`，17 条离线测试；CI 待查） | 接线；结果卡带文件 N2、确定回包 N10（`run.py`）；删除走允许卡 N5 | 接线（A 组分支 3） | 余 1 / 1（接线后流程 + 真模型） | 本窗口 |
| CW-D-RB-C | Express 读回 | 云 | 同上 | 同上 | 同上 | — | 同上 | 没做 | 云端读回通路 | 云端线 | 0.5 / 0.5 | 本窗口 ← 云端线 |
| CW-D-FT-L | 文件夹定时任务 | 本 | 定时任务 · folder-clock | 新建 / 改哪个（choice：新建或已有任务）→ 电脑与文件夹（choice ×2，`folder_intake/api.options`）→ 方向、入账类型（choice）→ 默认处理（现赊、抵扣、科目〔搜索〕、存货、部门）→ 日程（number 日、time）→ 提醒 | 账套 = 本对话账套；改任务时各步默认 = 任务现值 | 一张设置卡（各项，点回那一步） | 已保存；启停 / 立即运行 / 删除另走允许卡 | 没做（卡在 `assistant/tools.py` 与 cowork-folder-task-scope 分支交叠，等主会话定） | 声明 + 服务函数；时间输入（N8）；立即运行要过写入闸 + 允许卡（#53 现在都没有）；删除允许卡 N5 | 接线；N5、N8 | 8–10 / 2 | guided-cowork-2 local_3b58bd71 |
| CW-D-FT-C | 文件夹定时任务 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做（卡在 `assistant/tools.py` 与 cowork-folder-task-scope 分支交叠，等主会话定） | 云端写账 | 云端线 | 0.5 / 0.5 | guided-cowork-2 local_3b58bd71 |
| CW-D-FT-M | 文件夹定时任务 | MR | 同上 | 同上 + MR.ERP 现赊（payment） | 同上 | 同上 | 同上 | 没做（卡在 `assistant/tools.py` 与 cowork-folder-task-scope 分支交叠，等主会话定） | MR.ERP 先定菜单 / 默认值才建得了（#49） | 无（服务端已有 MR.ERP 任务） | 计入 FT-L（+1） | guided-cowork-2 local_3b58bd71 |
| CW-D-PD-L | 待处理 / 结果核对 | 本 | 待处理 · list-checks | 哪一项（choice，待处理清单）→ 按类：结果不明「已入账 / 没入账」+ 单号；改票面（复核卡）；删单（允许卡）；导出复核工作簿（multi） | 清单来自 `companies/api.pending`（同侧栏） | 单据版复核卡（同 P / S） | 每项一行 | 没做（服务端已写、未推：`pearnly/cc-guided-pending` 2c6fd7c37 本地，叠在 cc-guide-documents 上，等 engine-n 与 documents 源进 main 后变基推送；推了才计入服务端）；改票面走单据源复核卡，另有导出复核工作簿、删除（允许卡） | `cowork_resolve_push_result`、`cowork_delete_documents`、`cowork_edit_document` 现在没有允许卡、值经模型（#10、#26、#28）；改走卡上 | 接线；P2-1 复核卡；N5 | 6–8 / 2 | guided-cowork-2 local_3b58bd71 |
| CW-D-PD-C | 待处理 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端推送结果 | 云端线 | 0.5 / 0.5 | guided-cowork-2 local_3b58bd71 |
| CW-D-PD-M | 待处理 | MR | 同上 | 同上 + MR.ERP 设置类待处理（`pending_api.mrerp_needs`） | 同上 | 同上 | 同上 | 没做（服务端已写、未推：`pearnly/cc-guided-pending` 2c6fd7c37 本地，叠在 cc-guide-documents 上，等 engine-n 与 documents 源进 main 后变基推送；推了才计入服务端）；改票面走单据源复核卡，另有导出复核工作簿、删除（允许卡） | 同 PD-L | 无 | 计入 PD-L | guided-cowork-2 local_3b58bd71 |
| CW-D-CO | 公司资料 / 知识库 / 记住的选择 | — | 公司资料 · building | 做什么（choice：加 / 改 / 删笔记、存 / 删一条规则）→ 笔记（text）或规则类（choice）+ 值（choice 搜索，按工作月份那本账套主档） | 公司 = 对话公司 | — | 已保存 | **服务端**（`pearnly/cc-guided-erp-company` ca7c6a349：删笔记、删规则用允许卡） | 声明 + 服务函数；删除允许卡 N5 | 接线；N5 | 4–6 / 1 | guided-cowork-2 local_3b58bd71 |
| CW-D-WK | 任务看板 | — | 看板 · kanban | 看板（choice）→ 做什么（建卡 / 改 / 移动 / 完成 / 评论）→ 卡片（choice 搜索）→ 字段（text / date / choice） | 默认看板 | — | 已保存 | **服务端（未闭环：清单、标签、成员、附件，约 3–4 h）**（`pearnly/cc-guided-work`） | 声明 + 服务函数（转调 `work/api`） | 接线 | 6–8 / 1.5 | guided-cowork-2 local_3b58bd71 |
| CW-D-MS | MR.ERP 设置 | MR | （不上菜单，推送时冒出来）· settings | 每类单的导入菜单（choice）、主档默认值（choice 搜索） | 记住的按账套不再问 | — | 已保存 | **服务端（未闭环：`precheck_source` 接一行，H5 进 main 后由先动它的人接）**（`pearnly/cc-guided-push-setup`） | 判定卡补题类 `mrerp_menu / mrerp_default`（ERP 判定卡已有）；现在是模型 + 允许卡 | 引导式（判定卡题类在 `precheck_source`） | 3–4 / 1 | guided-cowork-2 local_3b58bd71 |
| CW-D-SG-L | 存货科目组 | 本 | （不上菜单，推送时冒出来）· boxes | 科目组（choice，`connections/service.stock_acc_group`） | 记住的按账套不再问 | — | 已保存 | **服务端**（判定卡的存货科目组题在 H5 `cc-stop-exits-h5` 的 `precheck_source`，进 main 后算数；`push_setup` 里的科目组只给定时任务用） | 判定卡补 `stock_group` 题类（ERP 已有）；现在是模型 + 允许卡 | 同 MS | 2–3 / 0.5 | guided-cowork-2 local_3b58bd71 |
| CW-D-SG-C | 存货科目组 | 云 | 同上 | 同上 | 同上 | — | 同上 | 没做 | 云端账套主档 | 云端线 | 0.5 / 0.5 | guided-cowork-2 local_3b58bd71 |
| CW-D-CN | 连接与账套 | — | 连接 · settings | 哪个连接（choice）→ 重读账套（确定） | — | — | 电脑在线 / 账套数 / 读到哪天 | 没做 | 声明 + 服务函数（`cowork_companion_status`、`cowork_refresh_account_sets`） | 接线 | 2 / 0.5 | guided-cowork-2 local_3b58bd71 |

### 1.2 Cowork · 代录（6 格）

| 格 | 业务 | 账本 | 要问的题 · 选项来源 | 自动定的 | 复核卡 | 结果卡 | 现在 | 缺什么 | 依赖 | 估时 | 负责 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| CW-O-P-L | 采购 | 本 | 同 CW-D-P-L + 同票已写「再录 / 跳过」、子菜单（`operator_form`）、中途框（做法 B） | 同直写；任务的公司与账套 | 单据版复核卡 | 每张一行 + 半截单 | **服务端（未闭环：等 wire 与流程）**（菜单路径产品函数用同一 documents 源，`pearnly/cc-guide-documents` 042f39436，列车 F；打字路径第 1 期题卡不变） | 分支 6 开始卡并卡；P2-1；P2-2 单位 / 现金科目 / 成本口径 / 负库存四题；P2-3 中途带回；同 CW-D-P-L 四题现状（Cowork 不问），见 1.6 | 接线；CC 桌面版新版（P2-3） | 见衔接规划：分支 6 4–6 / 2，P2-1 14–18 / 5–6，P2-2 10–12 / 4，P2-3 6–8 / 3 | 引导式 local_38ec22a7 |
| CW-O-P-C | 采购 | 云 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端代录通路 | 云端线 | 1 / 1 | 云端线 → 引导式 |
| CW-O-P-M | 采购 | MR | 同上 | 同上 | 同上 | 同上 | 没做 | `MRERP_OPERATOR_ENABLED = False`，交单回 `operator_express_only`；MR.ERP 5 个框要真站点探路 | MR.ERP 线 | 不另计 / 实机 2 | MR.ERP 线 → 引导式 |
| CW-O-S-L | 销售 | 本 | 同 CW-O-P-L | 同上 | 同上 | 同上 | **服务端（未闭环：等 wire 与流程）**（菜单路径产品函数用同一 documents 源，`pearnly/cc-guide-documents` 042f39436，列车 F；打字路径第 1 期题卡不变） | 同 CW-O-P-L；同 CW-D-P-L 四题现状（Cowork 不问），见 1.6 | 同上 | 同上 | 引导式 |
| CW-O-S-C | 销售 | 云 | 同上 | 同上 | 同上 | 同上 | 没做 | 同 CW-O-P-C | 云端线 | 同上 | 云端线 → 引导式 |
| CW-O-S-M | 销售 | MR | 同上 | 同上 | 同上 | 同上 | 没做 | 同 CW-O-P-M | MR.ERP 线 | 同上 | MR.ERP 线 → 引导式 |

### 1.3 ERP · 直写（16 格）

| 格 | 业务 | 账本 | 入口 | 要问的题 · 选项来源 | 自动定的 | 复核卡 | 结果卡 | 现在 | 缺什么 | 依赖 | 估时 | 负责 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ERP-D-P-L | 采购 | 本 | 采购（菜单 11-14 先拿掉，P2 整链验过再放） | 交票 → 识别 → 收单记录 `I:` → 判定卡（`write_precheck` + `intake_items`：付款、对方、物料、按行入库 / 仓位、科目、做不做）；复核里改印刷合计（`erp_set_printed_total`） | 公司、本对话账套、记住的 | 单据版复核卡 | 每张一行 | **服务端（未闭环：菜单 modes 加 chat 先给用户看；接线 / 流程 / 真模型等 wire）**（`pearnly/cc-guide-documents` 042f39436，列车 F：交文件→识别→题卡→复核→确定提交 ERP 单排直写→结果，付款点了才算） | 直写对话不识别附件（P2-4 新能力）；确定 = 提交 ERP 单；四题：直写开始前确认卡全问（缺题名词条）；网页提交前弹窗问单位、负库存、现金科目，不问出库成本；后台直写（网页直接提交 / 定时 / 自动推）不问只套记住的（缺 guided + 回执转题），见 1.6 | 接线；P2-1 | 8–10 / 3（P2-4） | guided-erp |
| ERP-D-P-C | 采购 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端写账 | 云端线 | 1 / 1 | 云端线 → guided-erp |
| ERP-D-P-M | 采购 | MR | 同上 | 同上 + MR.ERP 菜单 / 默认值 | 同上 | 同上 | 同上 | 没做 | ERP 的 MR.ERP 开关未开；MR.ERP 必填登记（付款方式、对方档、分店、币种） | MR.ERP 线 | 待 MR.ERP 线估 | guided-erp + MR.ERP 线 |
| ERP-D-S-L | 销售 | 本 | 销售 | 同采购 | 同上 | 同上 | 同上 | **服务端（未闭环：菜单 modes 加 chat 先给用户看；接线 / 流程 / 真模型等 wire）**（`pearnly/cc-guide-documents` 042f39436，列车 F：交文件→识别→题卡→复核→确定提交 ERP 单排直写→结果，付款点了才算） | 同 ERP-D-P-L；同 ERP-D-P-L 四题现状，见 1.6 | 同上 | 计入 P | guided-erp |
| ERP-D-S-C | 销售 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 同上 | 云端线 | 1 / 1 | 云端线 → guided-erp |
| ERP-D-S-M | 销售 | MR | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 同 ERP-D-P-M | MR.ERP 线 | 计入 | guided-erp + MR.ERP 线 |
| ERP-D-COL-L | 收款 | 本 | 收款 · wallet | 公司 → 日期（能录就不问）→ 账套 → 客户（搜索）→ 未收发票（金额多选）→ 现金 / 转账 / 折扣分摊 → 每种转账一张凭证 | 公司、日期、账套、销售员 / 部门 | 收款复核卡（`collection/guide.review`） | RE 号 / 排队 / 未入账 | **服务端**（分支 4 e590f7fb4，未进 main） | 接线；打字收款改走引导（用户 11-16 定，分支 3 之后） | 接线 | 已做；余项在分支 3 | 引导式 local_38ec22a7 |
| ERP-D-COL-C | 收款 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端 RE 写法 | 云端线 | 1 / 1 | 云端线 → 引导式 |
| ERP-D-COL-M | 收款 | MR | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | MR.ERP 收款写法未定 | MR.ERP 线 | 待 MR.ERP 线估 | MR.ERP 线 → 引导式 |
| ERP-D-ST | 盘点 | — | 盘点 · clipboard-list | 新建：仓（choice）→ 标题；记数：盘点单 → 物料（搜索码 / 条码）+ 数量，连续多行；结束（允许卡）→ 生成调整单（确定） | 仓位默认账面位置 | 差异表 | 调整单草稿号；调整单写账走 ERP 单据直写（采购 / 销售同一条推送） | **服务端**（`pearnly/cc-guided-stocktake` 7e43c8d67 + `cc-guided-stocktake-new-product` bd343663f：允许卡、扫到未知码当场新建物料） | N4、N5 | 接线 | 6–8 / 2 | guided-cowork-2 local_3b58bd71 |
| ERP-D-SC | 库存收发报表 | — | 库存报表 · file-spreadsheet | 期间（month）→ 仓（choice，可空） | — | — | 报表文件进对话 | **服务端**（`pearnly/cc-guided-stock-card`） | N2 | 接线 | 2 / 0.5 | guided-cowork-2 local_3b58bd71 |
| ERP-D-PD-L | 待处理 / 结果核对 | 本 | 待处理 · list-checks | 哪一项（choice，`erp_pending`）→ 再写一次 / 结果不明「已入账 / 没入账」 | — | 单据版复核卡 | 每项一行 | 没做（服务端已写、未推：`pearnly/cc-guided-pending` 2c6fd7c37 本地，叠在 cc-guide-documents 上，等 engine-n 与 documents 源进 main 后变基推送；推了才计入服务端）；ERP 只列采购单、销售单，票面在单据页改（documents 源对 P: / S: 只读） | `erp_resolve_push_result` 值经模型；改走卡上 | 接线；P2-1 | 4–6 / 1.5 | guided-cowork-2 local_3b58bd71（11-16 从 guided-erp 转来；依赖 cc-guide-documents 进 main：交给单据源 run.start(doc_ids)） |
| ERP-D-PD-C | 待处理 | 云 | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | 云端推送结果 | 云端线 | 0.5 / 0.5 | guided-cowork-2 local_3b58bd71（11-16 从 guided-erp 转来；依赖 cc-guide-documents 进 main：交给单据源 run.start(doc_ids)） |
| ERP-D-PD-M | 待处理 | MR | 同上 | 同上 | 同上 | 同上 | 同上 | 没做 | ERP 的 MR.ERP 开关未开 | MR.ERP 线 | 计入 | guided-cowork-2 local_3b58bd71（11-16 从 guided-erp 转来；依赖 cc-guide-documents 进 main：交给单据源 run.start(doc_ids)） |
| ERP-D-CO | 公司资料 / 知识库 | — | 公司资料 · building | 加 / 改 / 删笔记（text；删走允许卡） | 公司 = 对话公司 | — | 已保存 | **服务端**（`pearnly/cc-guided-erp-company` ca7c6a349：删笔记、删规则用允许卡） | N5 | 接线 | 2–3 / 0.5 | guided-cowork-2 local_3b58bd71 |
| ERP-D-ADD | 公司接入（从连接添加） | — | 添加公司 · building-plus | 连接里还没进 ERP 的公司（multi，`erp_connection_companies`） | 主档随公司带入 | 勾选清单 | 已添加 / 同步中 | **服务端**（`pearnly/cc-guided-erp-company` ca7c6a349） | — | 接线；公司接入线（`erp_company_add`） | 2–3 / 0.5 | guided-cowork-2 local_3b58bd71（与 local_3709c091 对齐） |

### 1.4 ERP · 代录（6 格）

| 格 | 业务 | 账本 | 现在 | 缺什么 | 依赖 | 负责 |
|---|---|---|---|---|---|---|
| ERP-O-P-L | 采购 | 本 | **服务端（未闭环：api.py 入口、提示词、前端换卡同批等 wire）**（A4：开始卡 / 预选卡 / 引导卡合一、再录 / 结果不明 / 改月份成题、确定交 hand_in，`pearnly/cc-guide-documents` 042f39436，列车 F） | 同 CW-O-P-L（分支 6、P2-1/2/3）；四题：代录开始前问单位、负库存（成本、现金科目由 Express 界面定）；代录单位框还没按答案点（CC 桌面版 NOT_WIRED），见 1.6 | 接线；CC 桌面版新版 | 引导式 local_38ec22a7 |
| ERP-O-P-C | 采购 | 云 | 没做 | 云端代录通路 | 云端线 | 云端线 → 引导式 |
| ERP-O-P-M | 采购 | MR | 没做 | ERP 代录目标写死 Express；MR.ERP 代录关着 | MR.ERP 线 | MR.ERP 线 → 引导式 |
| ERP-O-S-L | 销售 | 本 | **服务端（未闭环：api.py 入口、提示词、前端换卡同批等 wire）**（A4：开始卡 / 预选卡 / 引导卡合一、再录 / 结果不明 / 改月份成题、确定交 hand_in，`pearnly/cc-guide-documents` 042f39436，列车 F） | 同 ERP-O-P-L；同 ERP-O-P-L 四题现状，见 1.6 | 同上 | 引导式 |
| ERP-O-S-C | 销售 | 云 | 没做 | 同 ERP-O-P-C | 云端线 | 云端线 → 引导式 |
| ERP-O-S-M | 销售 | MR | 没做 | 同 ERP-O-P-M | MR.ERP 线 | MR.ERP 线 → 引导式 |

题、自动定、复核、结果同 1.2；估时在衔接规划里与 Cowork 代录共用一份（同一套声明、`modes: operator`）。

### 1.5 LINE / 网页上已有的同一流程 · 可复用多少 · 真正要新做的（每业务一行，同业务各账本格相同）

「真正要新做」不含两样所有格共用、不重复计的：分支 3 接线（A 组）与引擎通用能力 N1–N9（C 组，第 4 节）。估时 = 复用后剩下的，替换 1.1–1.4 里的原估时。

| 业务（格） | LINE / 网页已有的同一流程 | 出题与判定代码在哪 | 能直接复用 | 真正要新做 | 复用后估时 写码 / 机器 h |
|---|---|---|---|---|---|
| 采购 / 销售 · Cowork（CW-D/O-P/S） | **LINE 有**：「发单据进 ERP」选目标 → 账套 → 进销项 → 发票 → 识别后按行追问存货 / 仓（`line/flow.py`、`line/items.py`）；**网页有**：新建录入弹窗 + 复核页 + 待处理 | `precheck_source.document`（判定卡全部题）、`choices.resolve`、`write_precheck`、`line/items.py` | 出题、判定、记忆、推送全部复用（第 1 期已接成 `pearnly_guide` 卡） | 单据版复核卡、结果卡（A 组 P2-1）；识别时 `direction / posting_kind / payment` 模型参数退场；**缺口（A3 四题窗口 11-16）**：单位、负库存、出库成本、现金科目四题这次只进 CC 引导，LINE 不出（行为不变：静默默认 / 写时拦）；LINE 补出题（快捷按钮 + 「填换算 / 填成本」数字回复 + 记住）估 6–8 / 2，判据复用 `express_rules/push/book_questions.py` | 已计入 A 组 |
| 采购 / 销售 · ERP（ERP-D/O-P/S） | **LINE 有**：采购 / 销售格选公司 → 发票 → 逐题选（`integrations/line_flow.py`、`line_items.py`）；**网页有**：采购 / 销售单据页 | `write_precheck`、`express/precheck.py`、`intake_items`、`choices` | 同上；代录收单 `I:` 已有 | ERP 直写放票识别（P2-4，网页 / LINE 有、CC 直写没有）+ 卡；同上四题 LINE 缺口 | 已计入 A / B 组 |
| 收款 · ERP（ERP-D-COL） | **LINE 有**：公司 → 日期 → 客户 → 勾发票 → 方式 → 金额 → 凭证 → 确认卡（`integrations/line_collection.py`）；**网页有**：新建收款弹窗 | `collection.service`（三处同一组） | 全部（`collection/guide.py` 已转调完） | 只剩接线 | 已计入 A 组 |
| 读回（CW-D-RB） | 都没有（`readback/` 无 whitelist，只有 CC 工具） | `readback/service.request / status / file`；工具里 `_readback_files` | 服务函数、放进对话的做法 | 声明 + 薄转调 | **2–3 / 1** |
| 银行对账、收入对账、销项税核查（BR / IR / VC） | **网页有**成熟流程：对账中心「新建」两侧文件 → 后台任务 → 结果页的问题与预设 → 导出（`recon_center/api.py start / status / tasks / detail / delete`、`presets.answer`） | `recon_center/jobs.start`、`rerun.questions`、`presets.valid` | 开始、进度、结果问题、预设、导出全部复用 | 声明（三类一份，kind 一题）+ 文件题 + 结果问题接 N1 | **5–6 / 1.5**（原 8–10 / 2） |
| 收银日汇总（POS） | 网页没有（只有 CC 与文件夹定时任务 `folder_intake/pump.py` 用）；LINE 没有 | `pos_import/service.preview` → `contracts.apply_answers`（认列题已是服务端出） | 认列题、版式记忆、按天建单、推送判定 | 声明 + 认列题接 N1 | **4–5 / 1.5**（原 6–8 / 2） |
| 盘点 · Cowork（CW-D-ST） | **网页有**：盘点页新建（Excel）→ 扫码记数 → 复核 → 结束 → 导出（`stocktake/desk_api.py` 15 个接口）；**LINE 有**：富菜单「盘点」用 LIFF 打开同一网页 | `desk_api.api_create_stock_count / lookup / add_entry / review / close / export` | 全部接口 | 声明 + 连续记数接 N4、结束 / 删除接 N5 | **4–5 / 1.5**（原 6–8 / 2） |
| 盘点 · ERP（ERP-D-ST） | **网页有**：盘点页；**LINE 有**：富菜单第 3 格、手机盘点页（`inventory/line_mobile.py`，与桌面同一批接口） | `inventory` 盘点服务（CC 工具 `erp_stocktake_*` 已转调） | 全部 | 声明 + 生成调整单一步；N4 / N5 与 Cowork 共用 | **3–4 / 1**（原 6–8 / 2） |
| 增值税 ภ.พ.30（VAT） | 网页没有入口（`tax/` 无 whitelist，只在公司页汇总里显示）；LINE 没有 | `tax.service.vat_draft`（检查项已是服务端算） | 草稿、检查、工作簿全部 | 声明 + 「先读回」一步复用读回函数 | **3–4 / 1**（原 4–6 / 1.5） |
| 预扣税 ภ.ง.ด.3 / 53（WHT） | 网页没有；LINE 没有 | `tax.service.wht_draft`、`tax.service.answer`（收入类型题已是服务端出） | 草稿、问题、记忆 | 声明 + 问题接 N1、工作空间记住 N3；付款公司只认对话公司（现不核，要改） | **5–6 / 1.5**（原 6–8 / 2） |
| 文件夹定时任务（FT） | **网页有**完整表单：电脑 / 文件夹 → 去向 → 默认处理 → 日程提醒；启停、立即运行、删除、运行记录（`folder_intake/api.py` 8 个接口） | `folder_intake/rules.options / save`、`contracts.defaults / schedule` | 表单选项、保存校验、启停、运行、删除 | 声明（新建 / 改两条路）+ 时间输入 N8；立即运行补写入闸 | **5–6 / 1.5**（原 8–10 / 2） |
| 待处理 / 结果核对（PD，Cowork） | **网页有**：待处理页（账套待选、MR.ERP 待设、结果不明核对）+ 复核页（改票面、删单） | `companies/api.pending`、`pending_api.account_sets / mrerp_needs / mrerp_answer`、`push/application/api.resolve_unknown`、`history/api.save / delete` | 清单、各类处理函数 | 声明（按类分支）+ 复核卡复用 A 组单据版 | **4–5 / 1.5**（原 6–8 / 2） |
| 待处理（ERP-D-PD） | **网页有**：ERP 待处理（Express 窗口「重试」、结果不明核对） | `erp_pending` 同一份、`express` 推送服务 | 同上 | 声明 + 卡 | **3–4 / 1**（原 4–6 / 1.5，B 组） |
| 公司资料 / 知识库 / 记住的选择（CW-D-CO、ERP-D-CO） | **网页有**：公司页笔记、规则（`companies/api.note_save / note_delete / choice_set / choice_promote / choice_forget`）；ERP 公司页笔记 | 同左 | 全部 | 声明 + 删除接 N5 | Cowork **2–3 / 0.5**；ERP **1–2 / 0.5** |
| 任务看板（WK） | **网页有**：看板全套（`work/api.py` 50 多个接口）；LINE 用 LIFF 打开同一网页 | `work/api.create / update / move / toggle_done / comment / search` | 全部 | 声明（建卡、改、移动、完成、评论五条） | **4–5 / 1**（原 6–8 / 1.5） |
| MR.ERP 设置（CW-D-MS） | **网页有**：待处理页 MR.ERP 待设（`pending_api.mrerp_needs / mrerp_answer`） | `connections/service.mrerp_menu / choose_mrerp_menu / choose_mrerp_default` | 全部 | 判定卡加题类（ERP 已有同题类，照抄） | **2 / 0.5**（原 3–4 / 1） |
| 存货科目组（CW-D-SG） | **网页有**：连接页（`connections/api.stock_acc_group / choose_stock_acc_group`） | 同左 | 全部 | 判定卡加题类（ERP 已有） | **1–2 / 0.5**（原 2–3 / 0.5） |
| 连接与账套（CW-D-CN） | **网页有**：连接页 | `connections` 服务 | 全部 | 声明 | **1–2 / 0.5** |
| 库存收发报表（ERP-D-SC） | **网页有**：库存页「导出」 | `erp_stock_card_export` 同一服务 | 全部 | 声明 + 结果带文件 N2 | **1 / 0.5** |
| 公司接入（ERP-D-ADD） | **网页有**：「从连接添加」勾选窗（11-13 用户认可） | `erp_connection_companies / erp_company_add` 同一服务 | 全部 | 声明 | **1–2 / 0.5** |

复用不了、要从服务函数起做卡的只有四类：读回、日汇总、增值税、预扣税（网页 / LINE 都没有入口，但服务函数与服务端出题都已有，只差声明）。
**C 组顺序按「已有成熟流程优先」调整**（读回仍第一，主会话已定）：读回 → 对账三类 → 盘点（Cowork + ERP 一起，共用 N4）→ 定时任务 → 待处理 → 公司资料 / 看板 / MR.ERP 设置 / 科目组 / 连接 → 增值税 → 日汇总 → 预扣税 → D 组其余（库存报表、公司接入）。

### 1.6 写账四题（单位、负库存、出库成本、现金科目）各入口现状（A3 四题窗口 11-16，**不因此升档**）

判据 `express_rules/push/book_questions.py`（分支 `pearnly/cc-precheck-book-questions`）；ERP 接线在 `pearnly/cc-precheck-book-wiring`（未合入）；详表在 `docs/work/2026-11-16-cc-precheck-book-questions.md` 末尾。

| 入口 | 单位 | 负库存 | 出库成本 | 现金科目 | 缺什么 |
|---|---|---|---|---|---|
| ERP CC 直写开始前确认卡 | 问 | 问 | 问 | 问 | 题名词条（guided-frontend 在加） |
| ERP 网页采购 / 销售提交前弹窗 | 问 | 问（只给「允许」） | 不问（表单未存，答案挂不到单上；网页直接提交的销售单照旧不结转） | 问 | — |
| ERP 代录开始前（含收单记录 I:） | 问 | 问 | Express 界面定 | Express 界面定 | 代录单位框还没按答案点（CC 桌面版 NOT_WIRED，1.12.23 / 24） |
| ERP 后台直写（网页直接提交未经确认卡、定时、自动推） | 不问，只套记住的 | 同左 | 同左 | 同左 | guided:true + CC 回执 *_CHOICE 转题（单据页 / 代录卡同一出口），都没接；没记住的照旧静默默认或写时拦 |
| ERP LINE | 不问 | 不问 | 不问 | 不问 | LINE 出题（见 1.5 采购 / 销售行，估 6–8 / 2） |
| Cowork 全部入口 | 不问 | 不问 | 不问 | 不问 | CC 心跳补物料单位 / 结存 / 负库存设置 / ACCNUM00（cc-desktop-takeover 认领）+ Cowork 判定卡接线（排在 H5、盘点窗口之后） |
| MR.ERP | 不涉及 | | | | |

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

## 3. 查询类工具 → 归属格（主会话 11-16 认：归到格里当选项来源 / 结果取数，不单独做卡；打字查询照旧直接答）

| 工具 | 归属格 | 在引导里的用处 |
|---|---|---|
| `pearnly_whoami` | 全部 | 不用（身份由会话定） |
| `pearnly_tax_id_lookup` | CW/ERP 采购 / 销售 | A8 新对方题的取数 |
| `cowork_list_documents`、`cowork_document_detail`、`cowork_document_original`、`cowork_recognition_results`、`cowork_intake_options` | CW-D/O-P/S | 复核卡取数、识别「等」、账套选项 |
| `cowork_push_results` | CW-D-PD | 待处理清单与结果卡 |
| `cowork_companies`、`cowork_company_profile` | CW-D-CO | 公司 / 笔记 / 规则选项 |
| `cowork_recon_list`、`cowork_recon_status`、`cowork_recon_result` | CW-D-BR / IR / VC | 结果卡、在跑「等」、结果问题来源 |
| `cowork_stocktake_list`、`cowork_stocktake_report`、`cowork_stocktake_template` | CW-D-ST | 盘点单选项、差异表、模板文件 |
| `cowork_folder_tasks`、`cowork_folder_task_runs`、`cowork_folder_task_options`、`cowork_folder_files` | CW-D-FT | 任务选项、运行记录、电脑 / 文件夹选项 |
| `cowork_express_readback_status` | CW-D-RB | 结果卡「等」 |
| `cowork_work_boards`、`cowork_work_board`、`cowork_work_card`、`cowork_work_search` | CW-D-WK | 看板 / 列表 / 卡片选项与搜索 |
| `cowork_companion_status` | CW-D-CN | 电脑在线事实行 |
| `erp_list_documents`、`erp_document_detail`、`erp_document_original` | ERP-D/O-P/S | 复核卡取数 |
| `erp_push_results` | ERP-D-PD | 待处理清单与结果卡 |
| `erp_companies`、`erp_company_profile` | ERP-D-CO | 公司 / 笔记选项 |
| `erp_connection_companies` | ERP-D-ADD | 勾选清单 |
| `erp_stocktake_list`、`erp_stocktake_report` | ERP-D-ST | 盘点单选项、差异表 |
| `erp_collection_customers / invoices / methods / card / status / list` | ERP-D-COL | 打字路径的查询；引导里由 `collection/guide.py` 直接调同一组服务 |
| `express_operator_status` | 代录 12 格 | 进度状态行 |

共 42 个；其余 69 个（会写、会删、会触发的）都在第 5 节对到了格。

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
| C1 读回、对账、增值税、日汇总、预扣税 + 引擎 | **本窗口**（P3） | `pearnly_cowork/assistant/guided/{readback,recon,vat,pos,wht}.py`、`assistant/readback_files.py`、`readback/service.py` 只读查询；引擎 `assistant/guide/declaration.py`、`steps.py`（`run.py` 等分支 3 进 main 后变基） | CW-D-RB / BR / IR / VC / VAT / POS / WHT | 服务端已推的：读回、对账；合入顺序 分支 3 → engine-n → readback → recon |
| C2 盘点、定时任务、待处理、设置类 | guided-cowork-2 local_3b58bd71 | `pearnly_cowork/assistant/guided/` 下各自的文件、ERP `inventory` / `masters` 新的引导模块 | CW-D-ST / FT / PD / CO / WK / MS / SG / CN | 用 engine-n 的能力；consent 按答案区分由本窗口在 engine-n 上加 |
| D ERP 非写账类 | guided-cowork-2 local_3b58bd71（C2 之后） | ERP `inventory/cc_guide.py`、`masters/cc_guide.py`（新） | ERP-D-ST、SC、CO、ADD | 同 C；ADD 与公司接入窗口 local_3709c091 对齐 |
| E 云端 Express | 云端线 local_8d6eed55 | 云端通路（`companion/` 精简包、隧道、配对） | 19 个「云」格 | G0 通道 → Z1 真写；引导侧零改动，格的声明不变（第 6.1 节） |
| F MR.ERP | MR.ERP 写账线 | MR.ERP 写账 | 18 个「MR」格里 12 个依赖它（另 6 格 BR / IR / VC / FT / PD / MS 不依赖） | MR.ERP 必填登记、收款写法、代录开关、POS / 增值税来源 |
| G CC 桌面版 | CC 桌面版窗口 | `companion/` | 代录 P2-3、A8 / A10 小表 | 新版发布 |

**C1 顺序（本窗口）**：读回 ✓ → 对账三类 ✓ → 增值税 ✓ → 收银日汇总 ✓（未闭环）→ 预扣税 ✓（未闭环）→ 之后：预扣税复核（待确认行逐行改，要 A2 行内可改复核卡）、收银日汇总衔接 documents 源。**C2（guided-cowork-2）**：盘点 → 定时任务 → 待处理 → 设置类 → D 组。
（已按第 1.5 节改成「成熟流程优先」：读回 → 对账三类 → 盘点 → 定时任务 → 待处理 → 公司资料 / 看板 / 设置类 → 增值税 → 日汇总 → 预扣税。）

### 6.1 云端列（云端 Express 窗口 local_8d6eed55 11-16 结论，全文见共享笔记「云端Express-总表云端列-11-16.md」）

- **云端格的业务全部跟对应的本地 Express 格走，引导侧零改动**：云端那份 CC 在服务端眼里是一台普通 Express 电脑（心跳多一个 `link {kind: cloud}` 的显示属性），账套身份、主档、必填登记、原因码同一份；`capable()` 照本地规则判。云端格做完通道后只能跟到本地格同一状态。
- 共同前置 **G0 云端通道**（约 8 小时机器，要用户到场两次）+ 每格在测试云端 Z1 上真写 / 跑一次。
- 代录格另欠四样：Express 登录账号（用户给一次）、配方版本核对（不一致补配方 3–4 小时）、连接窗口最小化后云端是否还刷新（实测 1 小时）、凭据经隧道加密交给云端 CC（约 3 小时）。主会话已确认云端代录做（云端 CC 在自己的会话里点），代录格不标「不做」。
- 各格原因（照云端窗口原文）：直写各格「等云端通道 G0；业务同本地格；Z1 真写一张」；代录各格「等云端通道 G0 + Express 登录账号 + 配方版本核对 + 最小化刷新实测；业务同本地格」；ERP 公司接入「等云端通道 + 公司接入线的列电脑接口」（本表 ERP-D-ADD 不分账本，这一条记在该格的依赖里）。
- 另欠一条（云端窗口 11-16 第六节）：同一种产品连多个站点时（例：两个 Cowork 站点）只走第一个——隧道协议里没有站点字段；修法是请求带站点标识、中转按它选令牌，约 2 小时机器，实机与 G0-4 一起验。常见的一个 Cowork + 一个 ERP 不受影响。
- 格数口径：云端窗口按 `scripts/impact/cc-coverage.toml` 数 24 格（那张表另拆了「随单新建对方 / 物料」「出入库 / 调拨 / 物料调整」等行）；本表按 CC 工具对格，云端 19 格。两边对应关系：随单新建属本表采购 / 销售格内的题（A8 / A10）；出入库 / 调拨在 ERP 版 CC 没有工具（不成格，打字也办不了，见第 2 节）。

### 6.2 A 组拆成几条并行线（主会话 11-16 要求：关键路径 50–64 小时压在一个窗口上）

| 线 | 内容 | 碰的文件（独占） | 依赖 | 写码 / 机器 h | 建议谁做 |
|---|---|---|---|---|---|
| A1 关键路径 | 分支 3 服务端 S1–S6（菜单读注册表、`run.feed` 并进事件、`guide/api.py` 白名单接口、打字路径开引导、失效）+ cc-toolrun-conflicts（部署硬条件）+ 打字收款改走引导 | `assistant/guide/api.py`（新）、`sessions.py`、`guided.py`、`toolrun.py`、`guide_tool.py`（与 guided-erp 约定先后）、`collection/cc_tools.py` | 第 1 期进 main | 13–16 / 5 | 引导式 local_38ec22a7（留它手上） |
| A2 前端 | 分支 3 前端 F1–F5（业务菜单读服务端、引导运行卡、交文件、失效置灰）+ **单据版复核卡 / 结果卡通用组件** + 新输入的前端（questions、time、amounts 搜索与零、允许卡确定、结果卡文件 / 追问） | `frontend/assistant/**/guide-*`、`lib/business.ts`、`i18n.ts` 里引导段 | 接口形状已定（`run.view` 文档 + N 约定），可先用替身数据做、A1 进 main 后接真接口 | 14–18 / 5 | **新开一个前端窗口**（现在就能开工） |
| A3 补四题服务端 | P2-2：单位、现金科目、成本口径、负库存——服务端出题 + 载荷下发（直写、代录同一题） | `express_rules/*`（出题与载荷）、`write_precheck/*` 的出题部分（`review.py` 归 guided-erp） | 不等 A1；单位框、负库存要 CC 桌面版先给原生证据（G 组） | 10–12 / 4 | 新开一个窗口，或备用窗口 |
| A4 开始卡并卡 | 分支 6：代录开始卡、预选卡、引导卡三张合一（`documents` 出题源的代录分支）+ P2-1 代录声明 `modes: operator` | `express_operator/api.py` 的预检、`intake_items`、`write_plan.py`（与 guided-erp 约定先后） | guided-erp 的 `assistant/guide/documents.py` 出题源 | 10–14 / 4 | **交 guided-erp 顺延**（同一出题源、同几份文件，分两个窗口反而撞文件） |
| A5 中途带回服务端 | P2-3 站点一半：没登记的框（做法 B）、被打断「继续 / 停止」的卡、指令、按公司记住 | `express_operator/choice_points.py`、`stop_exits.py`、代录指令 | 不等 A1；CC 桌面版新版（G 组）才能实测 | 6–8 / 3 + 测试机实机 4–6 | CC 桌面版窗口一并做（两头一起改、一起测） |

结论：A 组最多拆成 **5 条并行线**，关键路径从 50–64 小时降到 A1 的 13–16 小时；A2、A3 现在就能开工，A4 跟 guided-erp 的出题源走，A5 跟 CC 桌面版新版走。A1 进 main 是 A2 接真接口、所有格进「接线」档的唯一共同前提。

与任务书建议的差别只有一处：**读回提前到第一个**——对账三类与增值税的「总账 / 销项税报表从 Express 读回」一步直接调读回的服务函数，先做读回这两类不返工；读回本身最小（3–4 小时）。
C 组合计约写码 60–75 小时、机器 15–18 小时（不含接线后的前端与流程，那部分每类约 1–2 / 1 小时）。

### 6.3 各窗口报数入口

- **唯一的表**：本文件（分支 `pearnly/cc-guided-master-table`，共享笔记同名一份）。只有 P3 窗口改它，别的窗口不直接改。
- 谁让哪一格换了状态（没做 → 服务端 → 接线 → 流程通过 → 真模型通过 → 真机通过），就把**变化的那几行**（格号、新状态、分支 / 提交、证据一句）用消息发 P3 窗口；P3 窗口改表、推送，并把最新的四个数（真机 / 真模型 / 服务端或以上 / 没做）发主会话一行。
- 各窗口负责的格：A1 引导式（采购 / 销售、代录、收款的接线档）；A2 guided-frontend（不单独成格，进「接线」档的前提）；A3 guided-four-questions、A4 guided-erp（ERP-D-P/S、ERP-D-PD 与代录开始卡）；A5 cc-desktop-takeover；C1 本窗口；C2 / D guided-cowork-2；E 云端 Express；F MR.ERP 写账。

## 7. 主会话 11-16 已定

1. 查询类工具归到格里（第 3 节附表核对无遗漏）。
2. D 组 4 格归本窗口，排在 C 组之后；公司接入格与 local_3709c091 对齐。
3. C 组读回第一；其后按「已有成熟流程优先」（第 1.5 节末）。
4. 声明放各自域包（`<域>/cc_guide.py`），hooks 的 `pearnly_guides` 只追加。
5. 读回菜单单列一项 + 对账 / 增值税里一步共用；科目前缀不问。
6. 引擎通用能力 N1–N9 由本窗口在共用层写、引擎窗口只审接口（约定见共享笔记「引导式-引擎通用能力N1-N9-约定-11-16.md」）；N9 先修。
7. 每次有格状态变化就更新本表、把变化的那几行发主会话。
