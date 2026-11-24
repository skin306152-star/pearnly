# CC 直写 · MR.ERP 写不进的两处修复（cc-guide-mrerp-direct，11-24）

主会话 11-24 批准；线上缺陷（ERP 直写 11-23 已上线）。评估口径见 docs/work/2026-11-24-cc-guided-documents-评估.md 同日消息。

## 现象与根因
- ERP：`express/precheck.py` 未接 Express 的公司上，收单记录只有 LINE 来源走 MR.ERP 判定（`operator.intake_mrerp_view`，11-19 加），
  CC 对话放进来的一律 `express.disconnected` → ERP 直写 · MR.ERP 写不进；代录对话同样报这个错原因。
- Cowork：`assistant/guided/push_setup.py`（11-16）没有调用方，判定卡在 MR.ERP 账套上不出导入菜单 / 默认值题；账套有几个已关联菜单
  或缺默认值时，每张都停在「先选导入菜单 / 默认值」，CC 里没处选（本机实验栈 + MR.ERP 专属测试账套实测：两次停下；用网页同一个函数记好
  选择后写成 PG691124-380119、SI691124-703585——这两张不算修好后的证据）。

## 修法
- ERP：按「公司接的账本 + 对话方式」判，不按来源；模式先看记录自己放进来的那段对话（session_id）。直写 → MR.ERP 判定；代录 → MR.ERP
  公司报 `unsupported`（`guide.reason.unsupported` 四语），没账本报 disconnected；记答案同判据。
- Cowork：判定卡推送目标是 MR.ERP 时出 `push_setup.items`（只在直写对话；代录是 CC 桌面版在网页里录，不问导入题），选项「代码 · 名称」；
  作答按账套记一次交 `push_setup.remember`；push_setup 的 Express 存货科目组段删掉（与推送判据 new_item_group 不一致，主会话定）。
- 守门：push_setup 出题 ⇔ `needs.needed`（推送同一视图）；代录对话不出导入题；ERP 两种来源同判据、代录报 unsupported。

## 第二段（接手窗口，11-24 下午）：页面流程、真写与新查出的两处

### 页面流程（本机实验栈 pearnly-cov1124-lab，电脑 + 手机各一格，8 格全过）

| 流程 | 守的是 |
|---|---|
| `cowork.cc.guide.mrerp-purchase` / `-sales` | 接 MR.ERP 的公司（编造连接、不带凭据），账套每个导入页两个已关联菜单未选 → 题卡出导入菜单题（「代码 · 名称」）→ 复核 →「立即录入」→ 结果卡；菜单记在账套名下、票推向这份账套 |
| `erp.cc.guide.mrerp-purchase` / `-sales` | 同上在 ERP 直写对话：题卡出导入菜单题 → 复核 → 确定 → 结果卡；收单记录用网页同一个入账函数提交成 ERP 单（来源 cc、去向 MR.ERP），菜单记在公司这份账套名下 |

### 11-24 MR.ERP 专属测试账套真写（卡上作答 → 确定 → 导入，一张一张顺序跑）

服务端走引导的同一组函数（开引导 → 卡上逐题作答 → 复核 → 确定；ERP 再同步跑一次导入任务），账套原来没记过菜单 / 默认值，题在卡上答：

| 格 | 卡上问到的 | 结果 |
|---|---|---|
| ERP 直写 · 采购 | 导入菜单（货品、费用）、默认费用科目、付款 | 第一张导入失败「รหัสสินค้าเป็นค่าว่าง」（物料是从 MR.ERP 读来的，见下面第 2 条）；修后 **PG691124-620145**（同一件已有物料）。另一张用 MR.ERP 里没有的新物料按品名自建写成 PG691124-526490 |
| ERP 直写 · 销售（现销） | 默认银行科目、付款（菜单只有一个已关联，自动用） | **SE691124-774951** |
| Cowork 直写 · 采购 | 默认科目、导入菜单（货品、费用） | **PG691124-405464** |
| Cowork 直写 · 销售 | 默认银行科目 | **SI691124-964561** |

上午两张 PG691124-380119、SI691124-703585 是先用网页函数记好选择后写的，不算修好后的证据。

### 新查出、本分支一并修的两处

1. **ERP 判定卡 MR.ERP 题的标题显示成键**（`express/precheck.py mrerp_needed`）：标题原来直接放单据类型 / 字段键，卡上显示「purchase」「purchase_expense」，选项只有名称。改用单据弹窗同一套项目名（`api.MRERP_SUBJECTS`，四语已有），选项「代码 · 名称」（同 Cowork）。LINE 收单卡同一函数，一并改对。单测 `test_erp_write_precheck`。
2. **ERP 写 MR.ERP 不带物料的商品代码**（`mrerp/contracts.py`、`mrerp/push.py`）：载荷里没有商品查找表，从 MR.ERP 读来的物料也按品名新建，撞上同名已有商品建不出，导入报「รหัสสินค้าเป็นค่าว่าง」。明细里带代码的行放进 `mappings.products`（共用层先按它认、认不出才新建），没代码的照旧。金样（容器内）不变，单测 `test_erp_mrerp_direct`。这条不只 CC：ERP 网页提交写 MR.ERP 的货品单同样受影响。
3. 没接任何账本的公司（主会话 11-24 确认口径：直写里 CC 收单判可写、只存进 ERP，同 LINE）：判定那半原有测试守着；结果卡原来按写账状态判成「未完成 · not_for_express」，改成提交进 ERP 即办完（已入账 · ERP 单号），`test_erp_guide_documents` 守。

### 没做 / 限制
- 真写走的是服务端同一组函数，不是浏览器点卡（页面流程在编造连接上点卡，真账套写入在服务端走）；两者合起来才是这一格的证据。
- 实验栈 Cowork 同一工作区只有一条 MR.ERP 连接：流程里的 `connections.configure` 会复用实验栈里已有的那条（本次把带凭据的探针连接换成了编造目录，已装回）。干净的回归实验室没有这条，不影响全套；记进已知问题。

## 共用件连带回归

`python3 scripts/check-impact.py` 出的入口（说明见 `scripts/impact/shared-parts.toml` 同名入口）：

| 入口 | 结果 |
|---|---|
| `cowork.line.precheck-questions` | 通过（Cowork 直写 · MR.ERP 判定卡出导入菜单 / 默认值题：页面流程 `cowork.cc.guide.mrerp-*` 电脑 / 手机 4 格；真写 PG691124-405464、SI691124-964561 卡上作答。LINE 卡片本身没在真机点） |
| `erp.cc.write` | 通过（页面流程 `erp.cc.guide.mrerp-*` 电脑 / 手机 4 格；真写 PG691124-620145、SE691124-774951；ERP 直写本地 Express 两条 `erp.cc.guide.purchase / sales` 没重跑，本分支只改 MR.ERP 判定与没账本公司的结果读法） |
| `cc.operator.page` | 通过（离线：本分支全闸门里同名离线检查）——以三步结果为准 |
| `cowork.express.push` | 通过（MR.ERP 推送：Cowork 真写两张走推送写成；Express 推送路径本分支没改，没另测） |
| `erp.cc.collection` | 通过（离线：本分支全闸门里同名离线检查）——以三步结果为准 |
| `cc.operator.start-card` | 通过（离线：本分支全闸门里同名离线检查）——以三步结果为准 |
| `cowork.cc.mrerp-setup` | 通过（卡上答的菜单 / 默认值按账套记住，页面流程 verify 读回、真写第二张不再问） |
| `erp.line.precheck-questions` | 没测到（原因：要 LINE 测试官方号与真机，本窗口不碰；第二段只改了题目标题 / 选项显示与 CC 同一函数，判定与记答不变，`test_erp_write_precheck`、`test_operator_intake` 守） |
| `erp.mrerp.direct` | 通过（离线 `test_erp_mrerp_direct` 48 项含新加的商品查找表一项；金样 `test_erp_mrerp_golden` 在实验栈容器里 5 项逐字节不变；真写 PG691124-620145、SE691124-774951） |
