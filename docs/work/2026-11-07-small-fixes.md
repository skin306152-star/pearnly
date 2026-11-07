# 小件清理（11-07）

- 分支 `pearnly/small-fixes-1107-3128b7`，工作树 `.claude/worktrees/post-deploy-verify-1107-89a47b`，基线 `8e5cb5724`，
  合入前变基到 origin/main `1e99aa4d4`。主会话 local_3d36d798 派：积压的独立小缺陷与老待办逐件做掉。
- 每件：查根因 → 修 → 测试（还原修复就失败）→ 闸门；合入前发主会话。

## 1. 物料调整草稿「单位」为空

- 现象：线上 testerp 盘点「device-1107-test」结束后「按盘点结果调整物料」，草稿 IA2569-000001 明细「单位」一格空（物料 QA1101-item / P-000008）。
- 根因：盘点生成调整单（`inventory/count.py` 的 `adjustment_draft`）铺的行只有物料、仓、库位、数量、估值，不带 `unit`；
  服务端把空单位当库存单位换算（`stock.resolve_factor`），所以数量入账没错，只是单据上看不到、写 Express 也没有单位。
  手工新建时选物料本来就带出单位（`transactions.js` 的 `lines_editor` 选物料 → `unit: item.unit || item.base_unit`）。
- 修法（按类）：`inventory/documents.py` 建单、改单的入口统一过 `_with_units`：行上没写单位的补物料的库存单位（`base_unit`，没有退回 `unit`），
  所有服务端自己生成的出入库 / 调整单行都会带上；老单（含修复前的草稿）打开时 `get_entry` 对空单位显示库存单位。换算不变。
- 测试：`tests/test_erp_stock_adjust.py::LineUnits`；流程 `erp.stocktake.adjust` 加断言「生成的草稿行显示物料的库存单位」——
  去掉 `_with_units` 与显示兜底后这一格不通过（看到 `[""]`），恢复后通过。
- 线上那张草稿 IA2569-000001 部署后打开会显示库存单位（显示兜底）；库里那一行的 `unit` 仍是空，提交时照库存单位入账，不另改数据。

## 2. 测试环境会改到线上 CC 智能体

- 根因：适配器按「pearnly 管理 + 产品」在账号里找智能体，说明不同就 update（`anthropic_managed_agents._agent`）。测试机导入的是线上租户的
  site_config（同一把 Anthropic 密钥、同一个站点名），一跑测试分支就把线上「CC (Pearnly cowork)」改成测试分支的说明；线上缓存键没变，
  之后新开的线上会话就绑在测试版说明上，直到缓存过期。
- 方案（先告知主会话）：按部署环境分开。生产 compose 设 `PEARNLY_DEPLOYMENT=${PEARNLY_DEPLOYMENT:-production}`；测试机
  `infrastructure/testenv/run.sh` 导出 `testenv`（壳环境优先于 env 文件）；本机 ERP 验收栈 `acceptance`、盘点预览 `preview`；其余没设的当 `dev`。
  非生产的智能体名字加后缀「[testenv]」、元数据带 `pearnly_deployment`，按它匹配；生产的名字与元数据保持原样。
- 证据：同一份说明、`PEARNLY_DEPLOYMENT=production`，main 与本分支的 `spec()` 一字不差（部署后不会更新、也不会新建线上智能体）；
  testenv / 没设时分别是「CC (Pearnly cowork) [testenv]」「… [dev]」。测试 `tests/test_assistant.py::PrepareTests`（测试环境不更新线上那个、
  生产不认测试环境那个、生产名字与元数据不变、只有生产 compose 默认 production）；去掉按环境匹配后两条不通过。
- 测试机下次 `lab-start` / `live` 起栈后会在同一账号里新建一个「[testenv]」智能体（一次），之后测试机只改它。

## 3. 删死代码 `save_document_choices`

- 全仓（含 companion/、frontend/、scripts/）核过：只有定义，没有调用方（唯一调用方 `cowork_save_push_choices` 11-05 已删）。
- 一并删了只有它传的 `service.save_choices(document_payment=...)`：CC 卡片按票写现 / 赊一直走 `service._document_payment`
  （`assistant/precheck_source._set_document_payment`），测试改成直接测它。
- `push/application/api.py` 登记进 `tests/flows/not_flows.toml`（要接 Express 账套与 CC 桌面版，实验栈跑不了；离线由推送测试守住）。

## 4. DMS webhook 测试状态 0

- 来源：11-05 生产只读核对（`docs/work/2026-11-05-product-map-next.md`），LINE 对 ris、testdms 两个 DMS 频道发测试事件回「状态码 0」。
- 查了：LINE 的测试事件是空事件数组、要 2 秒内回 200，状态码 0 = LINE 没拿到回应（连不上或超时）。
  11-06 22:43 部署时同一项（「dms：站点 2 个，配了 LINE 2 个」）没报，两家都通过；本机实验栈 DMS / ERP / Cowork 对签名的空事件都在 10 毫秒内回 200；
  三个产品同一条 `pearnly_core.line.webhook`，空事件不进产品处理器。结论：11-05 那次是一时的，产品代码没问题。
- 修的是检查：生产机段没成功时记下 LINE 给的 `reason` / `detail`、隔 3 秒再试一次；报告写出状态码、原因、试了几次
  （`scripts/production/test-check-external-config.py`）。

## 5. 端到端「打印 PDF」

- 场景 `e2e.erp.print-pdf`：e2e ERP 账号登 testerp，打开最新一张采购单的原生打印页（采购收货单格式），点「获取PDF」：
  链接不带 `pdf_generator=None`、回 200 `application/pdf`、文件头 `%PDF-`。线上 08f32a3b1 电脑宽度跑通过（两次，9–19 秒）。
- 副作用：打开打印页照 Frappe 原生规则在 testerp 记一条 Access Log；不改单据。
- 产品地图 `erp.desk.pearnly-purchase.action.Print` 引用它。

## 6. 「没测试」存量基线

- 364 → 244（本批减 120 条；只删补上测试的，没有新加）：
  - **CC 工具（Cowork 33 个、ERP 代录 2 个，全部清完）**：已有直接断言结果的测试、地图行没挂上的 14 个（删单据、删定时任务、删盘点、原件、复核工作簿、
    任务运行记录、POS 导入与认列、对账导出 / 列表 / 状态、盘点记数与报告、看板）直接引用；新写测试：结束盘点、删除对账（含拒绝、空列表、错误类别）、
    看板卡片 6 个工具（查、建、改带 modified、移动 / 完成、评论、搜索）与别人改过不覆盖、对账记住的口径（列 / 改 / 删、拒绝）、回答对账问题
    （重跑 / 还剩的问题 / 拒绝）、立即跑文件夹任务、盘点模板与差异表文件名；盘点列表、看板列表引用已有的结果测试；代录两个工具引用
    `test_express_operator_tasks` 的工具测试。
  - **页头动作（逐条核过流程真点了那颗按钮、并断言了结果才挂）**：ERP 采购提交、销售提交、入库新建 / 提交、调拨新建 / 提交、物料新建 / 提交 / Excel 导入、
    新建公司、新建供应商 / 客户、开始盘点、结束盘点；ERP 销售 / 采购 / 库存 / 盘点 / 首页账本导出（`erp.exports`）；Cowork 首页账本与盘点导出、
    录入复核「保存」、银行对账预设、银行 / 收入对账导出；CC 深链接（ERP `cc.operator.replay` 开 `?c=`，Cowork `cowork.pending.operator` 开 `?view=&company=`）。
  - 核出来不算的：采购列表「新建」（`erp.purchase.create` 是直接跳到新建页，没点按钮，已退回基线）、团队「新建」（流程只开弹窗看字段，员工是按接口建的）；
    CC 的设置 / 附件 / 删对话在 Cowork 那几行没有流程（`cc.*` 流程只在 ERP 站点跑）。

## 检查

| 层 | 结果 |
|---|---|
| 单元测试（改到的文件） | 通过 |
| 本地全闸门（`gate.sh`，201 步） | 全过 |
| 生产镜像候选离线检查（`ci-candidate-checks.sh offline`，镜像 1560bb95 挂本分支五个 App） | 102 组通过；`tests/test_line_search_and_loading.py::ErpLastWarehouse` 临时跳过——main 3bf45089b 删了 `line_flow._last_warehouse` 而测试还在调，是 main 现有问题，已报主会话 |
| 流程（本机实验栈 pearnly-smallfix-lab，`run.py --changed`） | cc.chat、erp.stock-entry.scan、erp.stock-transfer、erp.stock-adjust 新 / 老 × 电脑 / 手机 16/16；erp.stocktake.adjust 4/4 |
| e2e（testerp） | `e2e.erp.print-pdf` 通过 |

## 共用件连带回归（`check-impact`）

改到登记的共用件 `assistant`（智能体适配器）；`inventory/documents.py`、`push/application/service.py` 是共用件的调用方。

| 入口 | 结果 |
|---|---|
| `cc.assistant` / `shared.assistant` | 通过：流程 cc.chat 16 格里的 4 格（新老 × 电脑手机）走真实适配器 + 假智能体服务，新建对话、收发、落库；实验栈没设 `PEARNLY_DEPLOYMENT`，按 dev 新建了自己的智能体 |
| `control.assistant`、`cowork.assistant`、`cc.operator.thread`、`erp.cc.companies`、`cowork.cc-chat.push`、`cowork.cc.troubleshooting` | 没测到（要真智能体 / 生产入口）：生产这一改的智能体配置与 main 一字不差（离线比对），线上行为不变；部署后的 e2e chat-one 覆盖 |
| `erp.cc.chat-push` | 通过（offline：随全闸门的单测） |
| `erp.cc.stock-entry`、`erp.cc.stock-transfer`、`erp.stock-count.adjust` | ERP 一侧通过：erp.stock-entry.scan、erp.stock-transfer、erp.stock-adjust、erp.stocktake.adjust 全格；写 Express 没测到（要测试机），补的单位是库存单位，`express/stock_entry.base_unit` 对空单位与库存单位同样处理，写入的内容不变 |
| `erp.cc.stock-void` | 没测到（要测试机 Express）；本次只在建单 / 改单补单位，作废不经过这段 |
| `cowork.express.push`、`cowork.mrerp.push` | 没测到（要测试机）；`save_choices` 只删了没人传的参数，复核页那条路行为不变（`test_cowork_push_fixed`） |
| `shared.ocr.pipeline`、`shared.execution` | 没测到；这两个只因 `documents.py` 的调用链命中，本次改动不碰识别与执行层 |
