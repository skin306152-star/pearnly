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
  后台做法可用、过同一套守卫）；目标文字里带 `menu_tree` 的这一段（子菜单候选）与草稿单（付款方式现金 / 转账、单据种类），由模型选。
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
- 真正在 Express 里存单的验收：accserver 上有没有「现购是子菜单」的测试账套可写？没有的话本次只验到「菜单选对、窗口对上」，存单部分沿用 1→2 的普通公司。
- 子菜单多个候选时由 AI 按草稿单选（例如现金 → `ทั่วไป`，转账 → `โอนผ่าน TMB`），选完记住本账套；要不要改成「开始前预选卡片里问一次」由预选卡片窗口定（同④一并考虑）。
