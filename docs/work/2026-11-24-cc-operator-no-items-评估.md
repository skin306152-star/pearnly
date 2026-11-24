# 需求影响评估：代录遇到没有明细行的票 + 代录中途停下的统一收尾（2026-11-24）

分支 `pearnly/operator-empty-items-841db1`，基线 origin/main `40a62017e`。窗口 local_ac1aafb6（代录缺陷）。
证据目录 `~/pearnly-devtools/operator-empty-items-1124/`（复现脚本 `harness/`、日志与截图 `evidence/`）。

## 结论（一屏）

**复现（测试机2，69EXP\TEST，本仓源码装同一套代录，不接 AI）**
- 无明细 RR：停在「明细 0 行、合计对不上」→ 转人工 → **放弃流程把单取消了，账套一行没多，Express 已退出**。L3 看到的「单据开着没保存」是 AI 在放弃之前写的一句话，不是最后状态（L3 那两批的批次文件也是 finished、无空单头）。
- **但「停止」这条路不收尾**：人在 Express 里动了 → 代录暂停 → 在 CC 里点停止，代码直接关 Express，账套留下一张半截单（测试 RR0000309：单头金额 0 + 1 行明细，没有凭证、没有进项税）。同一条路还有：AI 预算用完、工具层报错、其它没接住的异常。

**(a) 没有明细的票怎么录**
- Express 界面里「จำนวนเงิน」框不能手填（实测光标直接跳过），人录不出「只有单头」的进货单。
- S 盘 16 本 2568 年真实账套（只读）：当年人录的进货单里，明细是「品码 = 费用科目码」一行的 RR 4361 张、HP 2311 张；只有单头的当年人录单 0 张（只有单头的 4000 多张全是年结从往年结转进来的）。
- 所以录法：**明细录一行「编码 = 这张单的科目」的服务品，数量 1、单价 = 税前额**（科目就是开始前卡片选的、直写记分录用的那个；主档没有这件就用直写同一个函数先建）。采购 RR / HP、销售 IV / HS 同一规则。
- 直写对同一张票现在写的是「只有单头」（无明细行、NXTSEQ '1'），和原生、和代录都对不上——要不要一起改，见第 12 节问题 1。

**(b) 统一收尾规则：离开 Express 前单据窗口不许停在编辑中**
1. 保存键按下前，任何原因停下（认不出、缺数据、点了停止、预算用完、工具报错、任何异常）→ 一律走现有放弃流程（取消；有明细时取消即存档 → 作废两道闸）。
2. 关 Express 的每一处（批次收尾、闲置退出、建主档前退出、断网恢复重开）先看单据窗口：还在编辑中就先放弃；放弃不干净（认不出的框）就不关、按「留着等人」报。
3. 保存后出事、业务选择框等人答：照现行（读账套判，不碰 Express，回执带 held）——这是唯一允许 Express 开着的状态。

**影响**：只改 CC 桌面版代录（`express_operator` 的 engine / batch / masters），服务端、载荷、文案都不动；随下一个 CC 版本。离线测试 + 测试机2 69EXP\TEST 实测，不花模型钱。

## 0. 需求原文与理解

- 来源：11-24 L3 核心集首跑（local_e30170ef）发现、主会话派单：Cowork 代录遇到只有税前 / 税额、没有明细（`items_status=empty`）的采购票，报 `OPERATOR_UNKNOWN_SCREEN` 停在 Express 新建的空明细 RR 上。
- 用户口径：按不会用又乱用的用户设计、优雅兜底；Express 不拦的我们不拦；按类通用修；只写测试账套；CC 靠模型判断，代码只守管钱底线。
- 理解：(a) 没有明细的买卖单代录要能录完，结果与直写、与原生一致；(b) 代录任何原因中途停下，客户的 Express 和账套都要是干净的。
- 不在范围：直写其它形状、代录的其它单种（收款不走代录）、MR.ERP 代录（另一套引擎，本次不动）、服务端卡片与文案。

## 1. 复现记录与现有同类

### 复现（测试机2 aksar 桌面会话，C:\PearnlyOperFix，本仓源码 `express_factory` 同一套装配，不接 AI；账套 P:\69EXP\TEST）

| 次 | 草稿 | 结果 | 账套 / Express |
|---|---|---|---|
| A | RR、T001、无明细、税前 500 / 税 35 / 含税 535 | `before_save` 合计对不上 → AI 没接（同 L3 是 AI 也判不了）→ `unknown_screen` → 放弃：取消（0 行时 Express 不写任何东西），单号格读到的是上一张已存的 RR690616-001，两道闸拦住没作废 | 行数不变；Express 已退出 |
| B | RR、1 行 53-05-07-00 × 500，录到 `before_save` 时注入「人点停止」（`Stopped`） | 批次停、这张「正在录」，**没走放弃**，批次收尾直接给 Express 发 WM_CLOSE | **APTRN +1（RR0000309：金额 0、DOCSTAT N）、STCRD +1，GLJNL / ISVAT 没有** = 半截单，同 11-14 IV0000045 的形状 |
| N1 | 原生对照：表头走完、明细留空，想在「จำนวนเงิน」框填 500 | 光标从表头直接跳到「หักส่วนลด」，「จำนวนเงิน」不进焦点、点也点不进（只读，由明细合计算出）；误打进折扣框的数已按放弃取消 | 行数不变 |

L3 当时（测试机2 `operator\batches\j3m7kdks8n.json`、`dgqmphtrou.json`）：两批都是 finished、held 空、voided / placeholders 空，停住截图是放弃前拍的。即 L3 那条路径最后是干净的；「当前窗口保持原样」是 AI 接手时的原话。

### 原生证据（只读）
- P:\69EXP 6 本：korn 等账套的无明细 RR 都是 NXTSEQ -1、日期在往年；TEST / TEST2 的无明细单全是作废单。
- S:\2568\EXP68 前 16 本（2025 年度），未作废、非 PEARNLY 写的：

| | 当年（2025） | 往年（结转进来） |
|---|---|---|
| RR 有明细·全是科目码品 | 4361 | 171 |
| RR 有明细·非库存品 | 3906 | 6 |
| RR 有明细·库存 / 混合 | 8739 | 2743 |
| RR 无明细 | 13（NXTSEQ 空，异常残单） | **2938（全是 NXTSEQ -1）** |
| HP 有明细·全是科目码品 | 2311 | 164 |
| IV 无明细 | 5 | 1065（NXTSEQ -1） |

  结论：NXTSEQ -1 的无明细单是 Express 年结结转生成的，不是人在单据窗口录的；人录服务 / 费用类买卖单就是明细录「编码 = 科目码」的服务品。直写 `direct_account` 行（`dbf_detail._account_line` → `product_master.ensure_account_item`）11-02 已按这个原生写法做。
- 直写同一张无明细票：`dbf_writer.write_purchase` 在 `items_status != ok` 时只写单头 + 凭证 + 进项税，STCRD 0 行、NXTSEQ '1'（`dbf_fields` 默认）；销货 `dbf_sales` 同。这个形状在当年人录单里没有。

### 现有同类与复用
- 代录开工前建主档 `express_operator/masters.py`：已经给每行建「挂在科目上的服务品」（文件头写明「会计手录费用单用的是挂在费用科目上的服务品」），只是票没有明细行时一行都不生成 → 草稿空明细。**扩展它**：没有明细行时按单头补一行。
- 服务端费用车道 `pearnly_core/express_rules/push/mapper._expense_detail` 已有「单行收尾」先例（名 ค่าใช้จ่าย、数量 1、含税全额）。
- 放弃流程 `engine.abandon`（取消 → 0 行不写 / 有行即存档 → 作废两道闸）、`_after_save_trouble`（保存后读账套判）、`held`（留着等人）都现成，**复用**，只补没走到它的出口。

## 2. Frappe 原生能力

不涉及：改的是 CC 桌面版（Windows 客户端）里操作 Express 的代码，不碰 Frappe 页面与 DocType。

## 3. 按类通用

- (a) 类：代录的四种买卖单 RR / HP / IV / HS，载荷没有明细行的（`items_status` 为 empty / incomplete / mismatch，或人在卡片选了「只记金额」`detail_choice=amount_only`）。采购挂 `items_account`（采购 / 费用科目），销售挂 `items_account`（收入科目），同一段代码。
- (b) 类：代录引擎所有中途出口（见第 4 节表），Express 一套；MR.ERP 代录是另一套引擎（网页），本次不动，记为后续核对项。

## 4. 上下游数据

### (b) 出口清单：现在怎么收尾、会不会留开着的单 / 半张单

| # | 出口 | 现在 | 留下什么 | 改后 |
|---|---|---|---|---|
| 1 | 预检拦下（不支持、缺对方 / 商品、期间） | 不开 Express | 无 | 不变 |
| 2 | 开 Express / 登录 / 核公司失败 | 没有单据窗口；宿主收尾退出 | 无 | 不变 |
| 3 | 保存前转人工（认不出、AI 判不了、菜单定不下来、人动过画面对不上） | 放弃：取消 / 作废两道闸 | 干净（复现 A）；作废闸拦下时留一张完整存好的单，回执 placeholders 报人 | 不变 |
| 4 | Express 业务拦单（负库存、期间） | 同 3 | 同 3 | 不变 |
| 5 | **点停止（暂停等人期间）** | 不放弃，直接 WM_CLOSE | **半截单**（复现 B） | 先放弃再关 |
| 6 | **AI 预算用完（BudgetStop）** | 同 5（批次兜底异常） | 半截单 / 开着的单被关 | 先放弃再关 |
| 7 | **工具层报错、其它没接住的异常（保存前）** | 同 5 | 同上 | 先放弃再关 |
| 8 | 服务器读写错（-70）/ Express 没了 | 不点；恢复后关掉重开 | Express 没了：无；-70 时编辑中的单被关 | 恢复后先放弃再关 |
| 9 | 保存后出事 | 读账套：完整回单号；半截报 partial_saved，Express 留着（held） | 账套里的半截单由人处理（已有口径） | 不变 |
| 10 | 录入中途业务选择框（dialog_choice） | Express 留着、对话出卡，答完放弃重录 | 开着的单等人（已定口径 11-14） | 不变 |
| 11 | CC 重启时有单在录 | `recover_orphan` 放弃 | 干净 | 不变 |
| 12 | 读回不符 | 存好的单报人 | 一张与票不符的有效单（11-09：写错单不做原生作废） | 不变 |

改法要点：`Engine.run` 对保存键按下前的**任何**异常都先走放弃再往上抛 / 记录（`Stopped` 仍回「停止」，但单已收拾、不再停在「正在录」）；`Runner._close`（及它的所有调用方）关 Express 前看单据窗口是否在编辑中，在就放弃，放弃不干净就不关、记 held。放弃时不再理会「停止」标记（否则放弃自己的按键又被 Stopped 打断）。

### 其它
- Express 映射：(a) 代录写出的 = 单头（同直写）+ 1 行服务品明细（编码 = 科目码，同直写 direct_account 行）+ 凭证 + 进项税。与直写只差「直写没有明细行」，见问题 1。
- CC 载荷：不变（不升载荷版本）。`items_account`、`base_amount` 载荷里已有。
- 服务端 / Cowork / ERP / LINE：不涉及代码；代录回执原因、`meta.operator` 键不变。

## 5. 隔离与权限

不涉及：只在客户自己电脑上、对这一批选中的账套操作；建服务品主档走直写同一套三道闸（账套名、写盘目录、账套公司）与备份还原；作废两道闸不变。

## 6. 入口

- 代录入口：Cowork 网页 / CC 对话交代录、ERP 代录、定时任务代录，都经 CC 写账线到同一个宿主，一处改全覆盖。
- 不涉及相机、LINE、手机。

## 7. 共用件与连带回归

`python3 scripts/check-impact.py --files companion/src/companion/express_operator/{engine,batch,masters,session}.py`：
- `cc.desktop.operator`（离线：假 Express 四种买卖单、弹窗、放弃作废、断点续录、锁屏暂停；建主档 tests/test_operator_masters.py + 测试机 T 账套回归；实机读回逐字段一致）
- `cc.desktop.write`（自动）
两项都要跑；新增测试见第 11 节。

## 8. 存储与成本

- 每张无明细票可能新建 1 件服务品主档（编码 = 科目码，一个科目一件，复用）——与直写 direct_account 相同。
- 无外部调用、无模型花费（放弃流程与补行都不用 AI）；无新增固定月费。

## 9. 删除、作废、追溯

- 改后放弃一律走 Express 原生作废（作废两道闸不变）；不删单、不按 Esc。
- 已经留在客户账套里的半截单不追溯处理（旧版本 CC 留下的会在再交时被读账套认出、报 partial_saved / placeholders，已有口径）。
- 本次复现留在 69EXP\TEST 的 RR0000309：施工复验时用 Express 原生作废收拾。

## 10. 文案与官方 UI

不涉及：不新增、不改任何文案；原因码沿用现有（stopped、budget、unknown_screen…）。

## 11. 测试与验收

- 离线（companion/tests，假 Express）：
  - 无明细 RR / HP / IV / HS：建主档补 1 行（编码 = items_account、数量 1、单价 = 税前），录完读回一致；`items_account` 空 → 预检 item_missing、不开单。
  - 出口 5 / 6 / 7 / 8：录到明细后分别抛 Stopped、BudgetStop、Unexpected、RuntimeError、-70 恢复 → 都调了放弃、Express 不在编辑中、批次 / 回执原因与现在一致（停止 = stopped、预算 = budget…）、placeholders 报对。
  - `_close` 前单据在编辑中 → 先放弃；放弃遇认不出的框 → 不关、held。
  - 现有 test_operator_* 全过（held、partial_saved、续录、作废闸不回归）。
- 测试机2 实测（69EXP\TEST，复现脚本复用，不接 AI）：无明细 RR、HP、IV 各一张录成、逐字段读回；注入停止 / 预算 / 异常各一次 → 账套只多作废单或不变、Express 已退出；作废 RR0000309。
- 端到端：请 L3 窗口把 `acc.cowork.operator.purchase.express` 改回原来不带明细的样票再跑一次（或我起独立 Mac 栈跑这一条，约 0.3 美元真模型）。
- 验收：主会话看测试机2 读回表与 L3 结果；用户不用动手。

## 12. 要问主会话的点

| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| 1 | 直写无明细票现在「只有单头」，代录照原生会多 1 行服务品明细。直写要不要也改成同样 1 行？ | 要改，但另起一步：代录这次先做（只动 CC 代录）；直写改动是三产品共用写账（Cowork / ERP / LINE / 定时任务所有无明细票），单独一个分支、62 套离线回归后上 | 不改：同一张票直写和代录在明细上不一致（单头、凭证、进项税一致） |
| 2 | 点「停止」时正在录的那张：收拾掉（放弃，再交从头录）还是保留半截等续录？ | 收拾掉：留在 Express 里的半截在关 Express 时一定变成账套里的半截单；放弃后再交照现行先读账套、从头录 | 保留：继续出现复现 B 那种半截单 |
| 3 | 补的那一行用哪件服务品：编码 = 科目码（直写 direct_account 同一个 `ensure_account_item`），还是按票上的品名建？ | 编码 = 科目码：原生当年 6600+ 张单这么录，直写也是它；票上没有品名可用 | — |

## 13. 工作量与排期

1. (b) 统一收尾：engine / batch 改 + 离线测试（约 1 小时写 + 15 分钟跑）。
2. (a) 无明细补行：masters / draft 改 + 离线测试（约 40 分钟）。
3. 测试机2 实测（约 40 分钟，办公室服务器 20:40 前）。
4. 全闸门、候选离线、companion 测试、public-scan、code-review medium（约 1 小时机器时间）。
5. 问题 1 若定「改直写」：另起一步，约 2 小时 + 62 套离线回归。

随下一个 CC 版本（1.12.25 已排给云端首测 / 轮询降量，本修跟 1.12.25 同车还是 1.12.26 由主会话定）；服务端不用部署。

## 施工记录（主会话 11-24 审定后）

主会话批：(a)(b) 都做；② 点停止那张收拾掉；③ 补行用「编码 = 科目码」的服务品；① 直写另起一步先评估、不改；跟 1.12.25 同车。

### 改了什么（只动 CC 代录与原因码登记，载荷不变）
- `express_operator/engine.py`：`Engine._leave` 是保存前停下的**唯一收尾入口**——转人工、拦单、停止、预算用完（原因 `budget`）、工具层报错、
  没接住的异常（原因 `unknown_screen` + 整批停）都经它放弃；`run` 只剩 `except Crash` / `except Exception → _leave` 两个出口。放弃途中
  不再因「停止」「一直有人在点」中断（`_settling`）；放弃后单据还在编辑也算没收干净。收不干净且单据还开着 → 原因 `left_open`、`held`。
  新增 `unsaved_open`（只读：单据窗口还在编辑吗；Express 没了 / -70 框开着当没有）与 `settle_open`（关 Express 前收尾，接管来的 Express
  以主窗口作基准窗口）。人点停止时 `run` 回 `outcome=stopped`（不再往外抛）。
- `express_operator/batch.py`：`Runner._close` 是唯一关 Express 的地方，关之前 `_leave_clean`——单据还在编辑就先放弃，收不干净不关、那张
  记 `left_open` 并落盘 `held`；断网恢复重开、CC 重启收拾上次留下的 Express 也改走它。`stopped` 结局记停止跳过（`recheck`，再交先读账套）。
- `express_operator/masters.py`：没有明细行的买卖单（RR / HP / IV / HS，`items_status` ≠ ok）补一行 `items_account` 的服务品
  （编码 = 科目码，没有就用直写同一个 `ensure_account_item` 建；同码是库存品不补，预检照旧停）、数量 1、单价 = 税前（按含税价录时含税）；
  补的那行不进回执 `line_codes`。
- 原因码 `left_open`（HUMAN）：CC / 服务端两份契约、`stop_exits`（act `cancel_in_express`，p2）、页面原因表；**新文案（四语，check-copy 新增 2、拦截 0）**：
  en「Unsaved document open in Express」/ zh「Express 里有一张未保存的单开着」/ th「มีเอกสารที่ยังไม่บันทึกเปิดค้างใน Express」/ ja「Express に未保存の伝票が開いたままです」。
  在哪台电脑、哪本账套照旧由回执 `meta.operator.held` 带。

### 验证
- 离线：新 `companion/tests/test_operator_clean_exit.py` 24 条（7 种出口 × RR / IV 不留编辑中的单与半截单；收尾途中再遇停止照收；
  批次点停止；`_close` 前先放弃；收不干净不关、`left_open`；**守门**：别处直接关 Express、`run` 多出不经 `_leave` 的出口就红；
  无明细补行与边界）。同一文件放到 origin/main 源码上 18 条红（原本就对的 6 条绿）。CC 全量 2478 过 / 64 跳过。
  服务端：test_express_operator_shared、test_companion_contract（138）、test_cc_operator、test_stop_exits、check-stop-exits 都过。
- 测试机2（本仓源码同一套装配、不接 AI、P:\69EXP\TEST，13:42–13:48）：

| 场景 | 结果 |
|---|---|
| 无明细 RR（税前 500 / 税 35，科目 53-05-07-00 已有服务品） | 存成 RR0000310，读回一致；单头 / 明细 1 行 / 凭证 / 进项税各 +1；Express 已退 |
| 无明细 IV（科目 42-08-00-00，账套里没有这件服务品） | 建服务品 42-08-00-00（重建索引）→ 存成 IV0000033，读回一致 |
| 录到合计前注入「停止」 | 批次停、这张停止跳过；有明细时取消即存档 → 作废 RR0000311；Express 已退；无半截单 |
| 录到合计前注入异常 | 这张 unknown_screen、整批停；作废 RR0000312；Express 已退；无半截单 |
| RR0000309（复现留下的半截单） | 浏览状态「上一张」（命令 53509）走到这张 → 作废命令 → 确认：单头 DOCSTAT C、明细删除标记（原生作废形状） |

- 逐字段（`evidence/impl/rowsC.txt`）：代录的 RR0000310 与直写的科目行单 RR690616-001 同形状——单头 AMOUNT / NETVAL / VATAMT / NETAMT、NXTSEQ 1、
  FLGVAT 2、SRV_VATTYP 1；STCRD 一行 STKCOD = 53-05-07-00、数量 1、UNITPR = 税前；GLJNL 04；ISVAT AMT01 / VAT01。差别只在两种入账方式本来就不同
  的记录栏（CMPLAPP、CHGDAT、CREBY、ORGNUM、STKDES 取主档名 / 直写取票上品名）。
- 测试机2 收尾：C:\PearnlyOperFix 已删、计划任务已删、CC 未动（13:05 起同一进程）；69EXP\TEST 留测试单 RR0000310、IV0000033（有效）与三张作废单。
- 没花模型钱。

### 剩余
- 端到端：主会话安排 L3 窗口用原来不带明细的样票重跑 `acc.cowork.operator.purchase.express`。
- ① 直写无明细票的事实评估（另起一步，只读统计 S 盘前先问主会话）。
- MR.ERP 代录是另一套引擎，本次未核对同类收尾。

## 11-24 晚：拆成两步（主会话）
- L3 在 TEST2 上重跑无明细样票后，主会话把 3fa4f0463 从列车撤下，让「统一收尾」先单独合。本分支 `pearnly/operator-clean-exit`（基线 origin/main c6fd4d177）只含统一收尾：engine / batch / contract、原因码 `left_open` 与四语文案、守门测试；**不含** `masters.py` 的无明细补行（另一步修完再交）。
- L3 看到的「多一行 51-03-01-00」查实是 GLJNLIT 里带删除标记的行：Express 每次在界面里存进货单都先写一行进货科目再打删除标记（TEST 里人录的 RR0000300 / 301 同样有；TEST 账套 RR 删除行 310 条），有效分录 3 行、借贷平衡，与直写相同。
- 补行那一步要先解决的是建档形状：原生「编码 = 科目码」的服务品是类型 5、费用科目组（如 คช）、单位多为 คง 或空；CC 建档（直写 11-02 起同一个 `ensure_account_item`）是类型 3、科目组和单位照抄账套模板（FG、กก）。
