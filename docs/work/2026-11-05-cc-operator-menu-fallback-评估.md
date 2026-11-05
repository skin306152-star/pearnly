# 需求影响评估：代录遇到定制菜单不退出，进检查点交 AI 接手；任何异常收尾成明确状态（CC 桌面版 1.12.15）

> 主会话 local_3d36d798 11-05 派给 CC 1.12.14 发版窗口：先写评估（根因 + 修法），报主会话审定后再动 companion。

## 0. 需求原文与理解

- 原话（主会话 11-05）：①菜单差异接不住——开录前探表单、点菜单时遇到定制菜单（子菜单、名字不同），不要直接 Refused 退出；按类修：先按菜单文字找
  命令，找不到或遇到子菜单就进检查点交 AI 接手，看画面决定点哪个；AI 也定不了才转人工并截图；所有异常都要落在检查点里。②批次状态收尾：任何异常退出，
  本机批次和站点都落到明确状态（停止 / 需要处理），不能卡在 running；Express 窗口照原样处理。③「开始」卡片只取一次资料：先确认 W4 合入后还有没有。
  ④单据目标账套没入口改：交预选卡片，本窗口不做。
- 现场（11-05 测试机，CC 1.12.14 staging，C 盘 T9MANAC7）：点开始 10 秒失败，日志 `Unexpected: refused: menu item opens a submenu`；没有 AI 接手、
  没有转人工截图；本机批次停在 running、单据 pending；站点「需要处理 / 重试」（worker 按 `OPERATOR_ERROR` 回执）；Express 留在主菜单。
  这家公司的「1.ซื้อ」菜单：`2. ซื้อเงินสด` 是子菜单（`1. ซื้อเงินสด-ทั่วไป (ไม่รวม ปตท)`、`2. ซื้อโดยโอนผ่าน TMB 2-01792-3`），
  `4. ซื้อเงินเชื่อ` 是命令；配方 `express-1.0.1.94.json` 写死 HP=`1→2`、RR=`1→4→1`，两种在这家都对不上。
- 不在本范围：单据写进哪份账套（④，预选卡片）；「开始」卡片刷新（③，见第 13 节，属 W4）；Express 菜单以外的录单步骤改法。

## 根因（代码）

1. `bg_desk.menu_command(path)` 按「数字.」逐层找，最后一项是子菜单或找不到就抛 `Refused`；`desk.run` 把它变成 `{"ok": False, "refused": True}`，
   `engine.act` 再抛 `Unexpected("refused: …")`。
2. `engine.ensure_form` 直接 `self.act([{"do": "menu", …}])`，不在 `step()` / `_recover()` 里：菜单失败不进检查点，也就不进 AI 接手
   （`_recover` → `helper.take_over`）。只有菜单发出去后窗口没对上才走 `checkpoint("form_open")`。
3. 开录前探表单 `batch.run_one` → `probe` → `probe_form` → `ensure_form`，在 `Runner._one` 的 `try` 之外；`_one` 里对「引擎没接住的意外」有收尾
   （单据记 `unknown_screen`、整批停），探表单这条没有。
4. `host.handle` 只接 `NeedHuman`；其余异常一路抛到 `worker._handle_operator`，按 `OPERATOR_ERROR` 回执「需要处理」，本机批次文件没人改（停在 running）、
   没截图、没 `_finish`（Express 不退）、宿主里这批 runner 还在（下一张同批再来会接着用一个状态不明的 runner）。
5. AI 接手本身在后台做法下开不了菜单：`BackgroundDesk` 不动真鼠标、不按组合键（`Refused("background mode has no modifier keys")`），
   CU 提示里「菜单先单独按 Alt 再按数字」在后台做法用不上。所以只把菜单错误接进 `_recover` 还不够，AI 要有一个后台能用的「菜单」动作。

## 修法（1.12.15，只改 `companion/`，不改载荷、不改服务端）

A. 菜单按文字找，读得到整棵菜单
- `bg_desk`：新增 `menu_tree()`（只读：各层「文字 / 命令号 / 是否子菜单 / 是否灰」，深度 3）；`menu_command(path)` 每层先按「数字.」找，找不到再按文字（配方标题
  里的泰文名，如 `ซื้อเงินสด`）找；落到子菜单时**子菜单只有一个可用命令**就用它（同一类、无歧义），多于一个不猜，抛 `MenuAmbiguous(候选文字)`。
- 前台做法（`win32_desk`）同样提供 `menu_tree`，保持两种做法同一套动作。

B. 菜单错误一律进检查点（AI 接手），AI 有后台能用的菜单工具
- `engine.ensure_form`：菜单这一步改成 `try … except Unexpected → self._recover("form_menu", 问题+候选菜单文字, 期望「已打开 X 单据窗口」, …, reverify=opened)`。
- `helper`：AI 接手多一个自定义工具 `express_menu`（`{"path": ["1", "2", "1"]}` 或文字），走 `engine.member("express_menu")` → `desk.menu`（WM_COMMAND，
  后台做法可用、过同一套守卫）；目标文字里带 `menu_tree` 与配方里的单据窗口标题，模型按标题找对应的命令（用于改名、多一层、编号变了这类
  「文字对不上」）。**子菜单下多个同类命令（同一种单分几套单号）不让 AI 猜**，按 D 处理。
  服务端 `express_operator/ai.py` 只白名单字段、不限 `tools` 内容，不用改服务端。
- AI 选中并开出正确窗口后，把「这本账套 + 这种单 → 实际菜单路径」记进本机 `probe.json`（已有的按账套缓存），同一本下次直接用、不再花 AI；
  缓存记的是路径和看到的窗口标题，配方版本变了就作废（现有逻辑）。
- AI 定不了（`need_human` / 预算到 / 站点连不上）：照现有 `_recover` 抛 `NeedHuman("unknown_screen")`，转人工前 `_need_human_shot` 留截图，路径进说明。

C. 所有异常都收尾成明确状态
- `batch.run_one`：探表单与 `session.ensure_open` 包进同一个收尾：`NeedHuman` 照旧往上抛；其余异常 → 这张记 `WAITING / unknown_screen`（原文进 detail）、
  留截图、整批 `BATCH_STOPPED / unknown_screen`，同 `_one` 的口径（不乱点 Express 上认不出的东西）。
- `host.handle`：加一层 `except Exception` 兜底（最后防线）：同 `NeedHuman` 分支——批次停、`park`、`_finish`（正常退出 Express，同现有整批停的处理）、
  回 `{"status": "human", "reason": "unknown_screen", "detail": 原文}`，站点就按「需要处理 · 认不出的画面」显示，不再是 `OPERATOR_ERROR`。
  worker 那层的 `except Exception` 保留（宿主外的错误仍按需要处理回执）。
- 启动收尾：CC 重启时 `recover_orphans` 已会把留下的批次 / Express 收掉；本次卡在 running 的那类批次文件会被它认出（进程号在 `express.json`）。

D. 子菜单多个候选：按付款方式选，定不下来问一次、记在站点（主会话 11-05 审定后补，**服务端改动待审**）

现状核对：
- 写账口径里付款只有「现购 / 赊购」两值（`pearnly_core.express_rules.push.choices.PAYMENT_VALUES = ("cash", "credit")`），预选卡片
  （`write_precheck`）的付款项也是这两值；「现金还是哪家银行转账」目前没有任何地方定，只有单据上识别出的付款文字（Cowork 单据 `payment_method`，
  ERP 采购记录「付款方式」）。T9MANAC7 的两项（`ซื้อเงินสด-ทั่วไป`、`ซื้อโดยโอนผ่าน TMB 2-01792-3`）都是现购，区别是付款走现金还是 TMB 转账（两套单号）。
- 选择记忆（Cowork `push/choices.py` 的 `choice_store`）按「连接 + 账套 + 种类 + subject」记，连接属于公司，等同「公司 + 账套」。

方案：
1. 站点给代录载荷多带两项（`operator` 段，旧版 CC 忽略，不升载荷版本）：`form_choice`（这本账套这种单记住的子菜单文字，没有就空）、
   `pay_route`（`cash` / `transfer` / 空，按单据的付款文字认：`เงินสด`/`cash` → cash，`โอน`/`transfer`/银行名 → transfer，认不出空）。
   Cowork 在 `tools._push_one` → `service` 组 `operator` 段处补；ERP 在它的代录交单处同样补（同一个纯函数放 `pearnly_core.express_operator`）。
2. CC 桌面版遇到子菜单多个命令时依次：`form_choice` 文字对上就用；否则按 `pay_route` 在候选文字里找唯一一项（cash → 含 `เงินสด`/`ทั่วไป` 且不含 `โอน`；
   transfer → 含 `โอน`/银行）；还不唯一就停在检查点：这张记 `WAITING`、原因 `menu_choice`（新原因码，级别「必须人工」），`meta.menu_options` 带候选文字，
   **不调 AI、不转「认不出的画面」**。
3. 站点收到 `menu_choice`：代录事件源（`express_operator/feed.py`）把候选放进这张单的状态；CC 对话里模型用 `ask_user` 问一次（题目「这家这种单走哪个菜单」，
   选项就是候选文字）；答案经一个新的小工具（或并进 `pearnly_precheck_answer`，看预选卡片窗口的接口）写进 `choice_store`：种类 `operator_form`、
   subject = 单据种类（HP / RR / …）、值 = 选中的菜单文字；然后照「重试」再交，载荷带上 `form_choice`。换电脑、换人都用这一条。
4. 新原因码要三处同步（CC `contract.py` REASONS、服务端 `express_operator/contracts.py`、前端 `operator-shared.ts`，测试逐条比对）+ 一条新文案
   （原因说明，中 / 泰 / 英，官方简短，例如「选择单据菜单」），按文案闸门走。
5. 与预选卡片的关系：理想是开始前就问（预选卡片「写进哪份账套」那类），但菜单候选要 CC 读到 Express 菜单才知道；第一版按上面「录到时问一次、记住」，
   记住后以后开始前就不再问。是否把菜单树随心跳上报、并进预选卡片，交预选卡片窗口评估。

改动面：服务端 pearnly_core（纯函数 + 原因码 + 事件源）、Cowork（载荷两项、保存答案）、ERP（载荷两项、保存答案）、前端（原因码、文案）、CC 桌面版。
CC 桌面版的 A/B/C 与 D 的 CC 部分（读 `form_choice` / `pay_route`、新原因码）不依赖服务端先上线：旧服务端不带这两项时，多候选直接停 `menu_choice`
（旧服务端不认这个原因码，按「需要处理」显示）。

## 1. 现有同类与复用
- 复用：`_recover` / `take_over` / `_need_human_shot` / `probe.json` 缓存 / `NeedHuman` 收尾分支 / `desk.run` 守卫；只新增菜单读取与一个 AI 工具。

## 2. Frappe 原生能力
- 不涉及：改动只在 CC 桌面版（Windows 端），不碰站点页面与 DocType。

## 3. 按类通用
- 类：「代录按菜单开单据窗口」——RR / HP / IV / SR / 其它配方里的所有单据种类，探表单与正式录单两条路径都走 `ensure_form`，一处改全覆盖。
- 定制菜单的样子（子菜单、改名、多一层、灰掉）都按同一规则：数字 → 文字 → 唯一命令 → AI → 人工。不针对 T9MANAC7 写死。

## 4. 上下游数据
- Express：只改「怎么打开单据窗口」，录入、保存、读回、作废两道保护不变；AI 选菜单只发 WM_COMMAND 开窗口，不保存任何单。
- 载荷：不变（不升载荷版本）。站点回执口径不变（`human` + 原因码，原因码沿用 `unknown_screen`，不新增原因码、不加文案）。
- Cowork / ERP：两个站点的代录都经同一个宿主，同时受益；服务端不改。

## 5. 隔离与权限
- 不涉及：不新增接口；AI 调用仍经成员通道 `operator_ai`，费用上限每批 3 美元不变。

## 6. 入口
- 代录（CC 对话「开始」、ERP / Cowork 两边）同一路径；直写不受影响。手机、LINE 不涉及。

## 7. 共用件与连带回归
- 改 `companion/` 的代录包（`bg_desk`、`win32_desk`、`engine`、`batch`、`host`、`helper`、`probe`），不碰登记的服务端共用件；交付时跑
  `python3 scripts/check-impact.py` 核对。
- 测试（`companion/tests/`）：①假 Express 菜单（子菜单两个命令）→ 菜单错误进 `_recover`、AI 用 `express_menu` 选中 → 单据窗口打开、路径记进缓存、
  第二次不再调 AI；②子菜单只有一个命令 → 不调 AI 直接开；③AI 回 need_human → 单据 `WAITING/unknown_screen`、有截图路径、批次 `STOPPED`；
  ④探表单抛任意异常 → 同③，宿主回 `human`、runner 收尾、Express 退出；⑤原有 1→2 菜单的公司行为逐字节不变（现有用例）。
- 真机：测试机 C 盘 T9MANAC7（子菜单那家）+ 一家普通菜单的 C 盘 T 账套各录 1 张（C 盘可写），走真「开始」；C 盘 Express 开单据会 -330（授权绑 accserver），
  所以菜单这一步与 AI 选菜单在 C 盘能验到「窗口标题对上」为止；真正存单要在 accserver 上同样定制菜单的测试账套，第 13 节待问。

## 8. 存储与成本
- AI：只在菜单对不上时才调用，一本账套每种单最多一次（之后走缓存）；单次估 0.05–0.5 美元，仍在每批 3 美元上限内。无新增固定月费。

## 9. 删除、作废、追溯
- 不涉及新数据；转人工截图沿用本机证据目录与现有清理（`prune`）。

## 10. 文案
- 不新增、不改文案：原因码沿用 `unknown_screen`（已有词条「需要处理」系列）。

## 11. 验收
- 本地：`companion/tests` 全量 + 新用例；分支 Development checks + Companion checks 绿。
- 测试机：上面两本 C 盘账套各 1 张走「开始」：子菜单那家 AI 选对菜单（截图 + 日志 `takeover … resolved` + 缓存写入），第二张不再调 AI；人为让 AI 定不了时
  单据「需要处理」、有截图、批次 `stopped`、Express 已退出；普通菜单那家行为不变。

## 12. 部署
- 只发 CC 桌面版 1.12.15（staging → 测试机 → prod），服务端不动。

## 13. 待主会话定 / 已知
- ③「开始」卡片只取一次资料：W4 新卡片还没合入 main（远端只有 `pearnly/precheck-erp-15197a`，W4 在 precheck-card-ui 窗口），现在没法确认；
  当前 main 上 `operator-start.tsx` 的 `usePrep` 只在 `asking` 变 true 时 `load(0)`，后上传的单要刷新页面才进卡片。请转给 W4：资料变化（上传完成事件）时或点开始前重取一次。
- 真正在 Express 里存单的验收：主会话 11-05 定不为此改正式账套；C 盘验「菜单选对、窗口对上」，真存单用普通菜单的公司，记录里写缺口。
- 子菜单多候选：主会话 11-05 定按付款方式选、定不下来问一次记在站点，见 D；D 的服务端改动待主会话审。
- ③ 主会话 11-05 已转给 W4。

## 14. D 的服务端施工（Cowork + pearnly_core，分支 `pearnly/operator-menu-server`，基于 476e55879；主会话 11-05 二次审定后改）

- 纯函数 `pearnly_core.express_operator.menu_choice`：`pay_route(*付款文字)` → `cash` / `transfer` / 空（现金：`เงินสด`、整词 `cash`；
  转账：`โอน`、`ธนาคาร`、`พร้อมเพย์`、整词 transfer / bank / banking / promptpay / qr，泰国各家银行泰文名与缩写；两类都有、都没有 → 空）；
  `stopped_at(推送记录回执)` 读回执 `meta.operator` 的单种与候选；`option_code` 截到选择记忆的值上限 60 字（CC 按互相包含仍认得出）。
  不从原因原文解析候选（原因原文只给人看）。
- 回执净化（共用，`express_rules.push.common`）：新增可选键 `meta.operator`，`clean_operator_meta` 只放行 `doc_type`（去空白、≤10 字）
  与 `menu_options`（字符串列表，空白归一、去空，≤10 条、每条 ≤120 字，超了截掉不报错），其余 operator 键照旧丢掉。不带时净化结果与
  476e55879 逐字节相同（测试钉住那一版的输出）；ERP 共用同一个净化，加的是可选键。旧系统逐字对照脚本里的回执不带它，加注释说明是
  有意差异。Cowork 回执照旧存进推送记录 `response_json`（没加字段）。
- 载荷：`contracts.for_operator(…, form_choice=, pay_route=)` 有值才写进 `operator` 段，MR.ERP 不带，不升版本。Cowork 推送准备
  （`push.application.service.prepare`，载荷已有 `doctype` 那一步）读 `choices.operator_form(连接, 账套, 单种)` 与
  `operator_pay_route(票面字段)`（`payment_method`；印了收现金 / 找零算现金；`posting_payment_manual` 是现购 / 赊购，不看）。
- 选择记忆：Cowork `push/choices.KINDS` 加 `operator_form`，`Cowork Express Choice.kind` 下拉加同名选项（原生 DocType 改动，Select 列
  不变，要 migrate，同 service_vat 先例）。`remembered` 不返回它（推送判定、自动推指纹只吃 `remembered`，测试证明库里多一条时输出与
  指纹逐字相同）；`of_sets` 返回它。`menu_stops(连接, 账套)` 按「连接 + 状态 + 建立时间」索引取最近 50 条停在子菜单的推送记录，
  给出各（账套, 单种）最近一次的候选。
- 公司页「记住的选择」：同一种行，分区「单据菜单 / เมนูเอกสาร / Document menu」；不能设为全公司；删照账套记忆那条路；改值从下拉选，
  可选项是这份账套这种单最近一次停下时的候选（记住的在第一个），只写记过它的那份账套；没有候选（推送记录已清或从没停过）时只能删。
- 预选卡片：`write_precheck.contracts.KIND_GROUPS["operator_form"] = BOOK`；Cowork `precheck_source.document` 对停在子菜单的单
  （推送转人工、那条推送记录的回执带候选）报一项 `{kind: operator_form, subject: 单种, options: [{code: 菜单文字}]}`，记过且仍在候选里
  为已定；代录预检原来的回包同时把它列进 `needed`（中途选那张卡片照它出下拉）；`remember` 单开分支直接 `choice_store.record`（记在
  停下那条推送记录的连接与账套），值须是候选之一，不经 `service.save_choices`。
- 事件源 `feed.py` 不改：中途选的卡片自己调 `precheck` 取项。
- 测试：`tests/test_express_operator_menu_choice.py`（新，已登记 Development checks）、`tests/test_cowork_write_precheck.py`、
  `tests/test_cowork_companies.py`、`tests/test_companion_contract.py` 加用例。
- 共用件影响清单（`check-impact.py`，命中 write-precheck、cc-operator-target 等）：本次只做离线检查，入口都是「没测到（本窗口不碰测试机，
  待合入前在实验栈 / 测试机实测）」，清单见交付报告。
- ERP 待接（另一个窗口的 `express/precheck.py` 合入后）：交代录载荷时同样传 `form_choice` / `pay_route`（付款文字取 ERP 单据「付款方式」）；
  回执若经共用通道存，同一个净化已放行 `meta.operator` 两项；它的预检钩子对停在子菜单的单用 `menu_choice.stopped_at` 报 `operator_form`
  项、记住处自己定（ERP 没有 Cowork 的选择记忆表）。

### 14.1 ERP 接上（11-05，变基到 origin/main 0691f8241 后，W3 的 `express/precheck.py` 已在基线里）

- 记在哪：ERP 已有的按公司记住的选择 `ERP Express Choice`（公司 + 类 + 对象，`kind` 是 Data 列不是 Select），加一类 `operator_form`，
  对象 = 「账套 key | 单据种类（大写）」，值 = 菜单文字。**不改 DocType、不加字段、不用 migrate。** `express/choices.py`：
  `operator_subject` / `operator_form(公司, 账套, 单种)`；`remembered()` 查询排除这一类（写账准备的输入照旧）。
- 载荷：`push.send` 代录交单处 `for_operator(…, form_choice=choices.operator_form(公司, 准备选出的账套, payload.doctype),
  pay_route=push.operator_pay_route(单据))`。`operator_pay_route`：采购（`is_paid` 或已付清）/ 销售（已收清）才按单据「付款方式」
  `payment_method` 认（cash → 现金；transfer / promptpay / bank → 转账；cheque / other → 空）；没付清的不给（赊购 / 赊销；采购付款方式
  默认是现金，不算数）。主会话 11-05 补：采购的付款明细（`ERP Purchase Payment`，不算冲销行、没写方式的行）方式全一致就按明细认，
  不一致或没有明细退回单头（销售单没有付款明细，只看单头）。没记过、没付清时载荷与以前逐字节相同，不升载荷版本；直写不带。
- 回执：ERP 走共用机器通道 `cc_desktop.jobs`，回执 `meta` 原样存在写账件 `meta_json`（那一层不净化）。ERP 读时先过同一个共用净化
  `common.clean_operator_meta`，再 `menu_choice.stopped_at`。
- 预检：`express/precheck.menu_stop`（单据当前那件写账件是代录交的 `erp:operator:`、状态 manual、有账套、回执带候选）→
  `menu_item` 报 `{kind: operator_form, subject: 单种, label: 单种, value, source, options: [{code: 菜单文字, name: ""}]}`，记过且仍在候选里
  为已定（`remembered`）；`document_view` 在 CHECK 状态把它加进 items（带 direction）。代录预检原来的回包 `operator.with_menu`：还没定的
  列进 `needed`（ready → choose，原因 `choice_needed:operator_form`；choose 时追加）。
- 记住：`precheck.save_answers`（卡片 `remember` 钩子与代录交单前的 `operator._remember` 同一条）：`operator_form` 单开一支，值须是这张单
  停下时的候选、单种对上，否则整批拒（先核完再记，其它项也不记）；`remember: false` 不记；其余项照原来 `save` 收齐后核。
  W3 审看后改：这张单没停在子菜单（读不到停点）时，它带的子菜单答案跳过、不拒——旧前端把同一份答案带给批里每一张（10-30 实机同类），
  一张停、别的没停不能整批「请求无效」；两张一起交只记一次、都交出（有测试）。答案的单种不是这张停下的那种（批里别的单停在 RR / HS
  各一张、同一份答案带两项）同样跳过；只有单种对上、值不在候选里才拒。Cowork `precheck_source.remember` 按同口径改了单种对不上那条
  （跳过且不占「记过」）；Cowork 里没停在子菜单的单带子菜单答案仍拒，待主会话定是否同 ERP。停点仍直接读写账件 `meta_json`：`cc_desktop.jobs`
  没有读回执 meta 的现成函数（`view` 只给 party_code / line_codes）。
- 公司页：ERP 没有「记住的选择」显示 / 修改入口（`ERP Express Choice` 只在写账准备里读），本次不新造；要看要改时另评估。无新文案。
- 写账准备不受影响：测试证明库里多两条 `operator_form` 时 `remembered`、准备结论与载荷、预检收齐的项逐字相同。ERP 没有自动推指纹。
- 测试：`tests/test_erp_operator_menu_choice.py`（新，已登记 Development checks）；`tests/test_express_operator_code_cc.py` 的 ERP 预检替身补
  `menu_item`。影响清单（`check-impact.py`，只算 ERP 四个文件）：命中共用件无；只改到调用方的入口 `erp.cc.write`、`erp.cc.stock-void`、
  `cc.operator.page`、`erp.mrerp.direct`、`erp.entry.choices`，本窗口不碰测试机，均「没测到（待合入前在实验栈 / 测试机实测）」；离线覆盖见上。
