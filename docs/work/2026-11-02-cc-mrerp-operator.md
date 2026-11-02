# CC 端 MR.ERP 代录执行层（MR.ERP 代录第 2 步）施工记录

- 评估：[MR.ERP 代录与 ERP 左侧公司](2026-11-02-mrerp-operator-and-erp-company-评估.md) §13 第 2 步（§0 事实 3–6、§4、§5、§8、§9、§14 全部、§12.1 定案）；
  决定见 [ADR-026](../adr/0026-operator-targets.md)；第 1 步分路见 [代录目标分路](2026-11-02-cc-operator-target.md)。
- 分支 `pearnly/cc-mrerp-operator`，工作树 `.claude/worktrees/cc-mrerp-operator`，基线 `9be471c5`（= origin/main）。
- **本分支在第 0 步真站点探路通过之前不合入 main**（主会话定）。本步只做不依赖真站点的部分；没连任何真实 MR.ERP。
- 默认行为与 main 完全一致：开关 `PEARNLY_CC_MRERP_OPERATOR` 不设 = 心跳只报 `express`、宿主不装 `mrerp`、派来的 MR.ERP 单回「不支持」、
  不导入 `mrerp_operator`；配方全部「待探路核对」，就算开关打开，没核对过的单种也回「不支持」（第二个开关 `PEARNLY_CC_MRERP_UNVERIFIED`
  只给假站点回放与第 0 步探路）。

## 结构

```
写账线 worker._handle_operator_task（Express 以外目标：不找账套、不拿 PEARNLY.LCK、不建主档，第 1 步已分路）
  └─ express_operator.host.Host.handle：按 operator.target 取装配函数
        express → express_factory（原样）
        mrerp   → mrerp_factory（只在开关打开时装）→ mrerp_operator.host.assemble
                    ├─ 共用：batch.Runner · BatchStore · evidence · contract（原因代码与级别）· helper 接口 · probe.ProbeStore
                    └─ MR.ERP 四样 + 三处口径：session · engine · book · web_desk(+web_driver) ；draft_type · before · find_saved
批次线程（托盘进程，不碰 Qt）── ProcessDesk ── 本机带口令的管道 ──► companion.exe --mrerp-desk（每批一个子进程）
                                                                   └─ web_driver.Driver：QtWebEngine 原生窗口（默认不显示）
```

**和评估 §14.2 的一处不同（有意）**：评估写「CC 窗口本来开着时共用同一个内核主进程」。10-18 轻量化以后托盘进程不加载网页内核
（`window_link.py`：网页内核只活在 `--window` 窗口进程里），批次线程在托盘进程里。若在托盘进程里起网页内核，录过一批后网页内核的
动态库与浏览器侧线程就常驻托盘（卸不掉），违背「不常驻网页内核」。所以 MR.ERP 页面开在**每批一个的子进程**（同一个 `companion.exe`
加 `--mrerp-desk`），批次线程经 `multiprocessing.connection`（Windows 命名管道 / 其他系统本机套接字，一次性 32 字节口令做 HMAC 挑战）
一问一答；录完关窗、子进程退出、渲染进程随之退出。子进程里「所有网页内核调用排到界面线程执行并等结果」由 `web_driver.UiCall` 做
（管道线程只活在子进程里，录完即退，不是托盘的常驻线程）。

## 每个模块做了什么

| 文件 | 内容 |
|---|---|
| `companion/src/companion/mrerp_operator/__init__.py` | 包说明（ADR-026、评估 §14） |
| `web_driver.py` | 子进程里的网页内核：无名 `QWebEngineProfile()`（off-the-record，内存缓存、不存 Cookie）；关插件、禁脚本开窗；原生窗口默认不显示、`show` 才弹出；页面设为可见状态（收起照跑，待探路核对）；脚本只在 `ApplicationWorld` 跑、结果 `JSON.stringify`；接管 `alert / confirm / prompt`（不弹出，文字进 `dialogs`；confirm 默认回取消，可预设下一次）；只许本站跳转、不开新窗口；页面控制台不进日志；人工输入：在窗口与渲染部件上挂事件过滤（点、键、滚轮计数，**不挂整个应用的过滤**——PySide 下那样会碰到析构中的对象崩溃，本机实测复现过）；`close` 先删页面再删资料；`UiCall` 把调用排进界面线程；`child_main` 子进程入口（口令读完即从环境删掉，不留给渲染进程）；`LocalDesk` 同进程版（测试用） |
| `web_desk.py` | 批次线程这一侧，不导入 Qt：`ProcessDesk`（拉起子进程、连管道、一问一答加锁、`close` 等退出不退就结束它；CC 窗口要实时画面 / 查看时只等 0.5 秒、等不到回 `busy`，不卡窗口）；`WebPage`：打开并等加载、等条件（中途有人在窗口里动就重新计时）、填格（聚焦 → 写值 → `input / change` → 失焦；浏览器不发焦点事件时补发同名事件；密码这种 `secret` 不回读值）、勾选、点击、读格子 / 属性 / 文字 / 片段、选择框（bshlistbox：聚焦格子 → 搜索框打码 + keyup → 点第一个看得见的列与码完全相同的那一行，不改隐藏值；没有 / 不止一行都不选）、预设 confirm、截图、弹出窗口 |
| `session.py` | 按需登录（`ensure_open` 只核能不能开，真要用页面才开窗、登录）；登录前核对当前页确是这个网址的 `login/login.php`、表单提交到 `checklogin.php`，才从本机凭据取（目标名 `Pearnly CC/MR.ERP/<主机>/<连接号>`）；只试一次，MR.ERP 拒绝 → `login_failed` 不再试；没存 → `login_missing`；选账套点 `mainmenu.php?comidyear=…` 那条链接，读底栏 `Company :` / `Database Name :` 与载荷核对，不符 → `company_mismatch`；识别被踢（踢人弹框文字或回到登录页）→ 不自动重登；被踢后关窗不走 `logout.php`（它会删这个账号的在用记录，把正在用的人踢掉）；`reachable` 只读打开一次登录页 |
| `engine.py` | 一张单：列表（菜单号）→ Add → 核新增页格子都在 → 表头（单号格只读 = 自动编号就用页面的；日期；现金勾选；税率 / 对方 / 分支 / 部门 / 工作 / 员工按选择框；对方选完等页面带出信用天数）→ 明细逐行（不够行点「เพิ่ม」；选商品后等页面自己取回的标准成本价落定再填数量、单价；读回本行金额）→ 折扣 → 现销填收款 → 读页面合计与草稿对上 → Save（页面自己的必填检查拦下 = 没提交、什么都没写）→ 提交后读「Save Success」→ 列表找回这张 → 查看页逐字段核对 → 记单号 → 查看页截图存证。异常：被踢（存档前 = 必须人工；存档后 = 结果不明）、网络断 / 子进程没了（存档后 = 结果不明，交批次等恢复、续录前查）、拦下（放弃 = 回列表，不存）、读回不符（不改，必须人工）、存成了两张（列出单号交人）、认不出的弹框（有能看文字的助手且判「继续」才继续，否则必须人工）；`probe` 开新增页读格子、不存 |
| `book.py` | 列表解析与服务端回读同口径（`parse_rows / find / listing_problem`，另带查看页编号）；`before`：开录前在本菜单列表按含税合计搜一次，记下同日同额已有的单（开录前清单）；`find_saved` / `find_new`：之后新出现、查看页核对得上对方码 / 对方单号 / 我们填的单号的那一张；`verify`：查看页逐字段核对；`UnsureStore`：点了存档、结果不明就停下的单记在本机（单据号 → 最早那份开录前清单），这张换一批 / CC 重启后再来也先查、查到不重录 |
| `recipe.py` + `recipes/mrerp-2026.11-src.json` | 四种单（赊购货 aptran、赊购费用 aptran、赊销 artran、现销 arse）的格子 id、按钮、单号格、查看页地址、收款区、已知弹框；全部按源码取、全部 `verified: false`；`supported` 默认只认核对过的；`unverified()` 列出待核对项 |
| `draft.py` | MR.ERP 草稿与载荷 `mrerp` 块的形状（第 3 步站点按这个出）；合计按页面 `calcgrandtotal` 同算法；佛历日期 |
| `presence.py` | 只看 MR.ERP 窗口里的人工输入计数；涨了就暂停（`user_active`，证据进汇报），30 秒没人动或点「继续」再接着；等的时候点停止抛 `Stopped` |
| `login.py` | 「授权登录」原生小窗（MR.ERP 用户名、密码），存 Windows 凭据管理器（`express_operator.credentials` 同一套）；`stored / user_of（只回用户名）/ forget` |
| `host.py` | `assemble`：装 `Runner`（`no_lock`、`reachable=session.reachable`、探路缓存 `mrerp-probe.json`、结果不明记 `mrerp-unsure.json`、截图 `evidence/`）；`settings_of / draft_of`；`DeskView`（实时画面、查看）；两个开关 |

宿主与共用件的接线（小改动）：

| 文件 | 改动 |
|---|---|
| `express_operator/batch.py` | `Runner` 加三个可选注入 `draft_type / before / find_saved`，不传就是 Express 原来的（`Draft`、`book.counts()`、`B.find_saved(rows_since)`）——Express 行为不变 |
| `express_operator/host.py` | `mrerp_factory`（按需导入 `mrerp_operator`）、`OPTIONAL_ASSEMBLERS`、`enabled_assemblers()`（与心跳同一个开关）；`handle` 用装配函数自带的 `draft_of / settings_of`（Express 没有，照旧 `from_payload` 与传入的设置） |
| `payload_contract.py` | `MRERP_OPERATOR_ENV`、`mrerp_operator_enabled()`、`operator_targets()`（默认 = `OPERATOR_TARGETS` = `("express",)`） |
| `worker.py` | 心跳报 `operator_targets()`（默认与原来相同） |
| `main.py` | `--mrerp-desk` → `mrerp_operator.web_driver.child_main` |
| `tomo_bridge.py` | `operator_authorize / operator_forget` 带 `target=mrerp`（与 `url`、`connection`）时走 MR.ERP 登录小窗；不带照旧 Express |
| `i18n.py` | 新词条 `op_mrerp_login_title`（MR.ERP 登录 / เข้าสู่ระบบ MR.ERP），用户名、密码沿用 `op_login_user / op_login_password` |
| `packaging/companion.spec` | 隐藏导入加 `mrerp_operator` 各模块、配方数据文件；排除名单不改、不加依赖（网页内核本来就在包里） |
| `scripts/impact/shared-parts.toml` | 新入口 `cc.desktop.operator.mrerp`（offline），挂到共用件 `cc-operator-target` |
| `.github/workflows/development.yml` | 加一步 `tests/test_mrerp_operator_readback_parity.py` |

## 源码里查到、影响设计的两件事（第 0 步要实测）

1. **被踢时 MR.ERP 照样写库**：`component/php/checkuseruse.php` 发现会话号对不上只打出 `alert + location=logoutkick.php` 的脚本，
   **没有 `exit`**，`allsave.php` 接着往下 `INSERT` 并提交。所以「点了存档才发现被踢」= 结果不明（可能已存），按崩了留「正在录」、
   本机记下，续录前先查；「存档前被踢」（打开新增页就被踢）= 什么都没写。
2. **浏览器内核会自动重发断在半路的 POST**：假站点「写了库、回包前断开」时，网页内核在新连接上把 `allsave.php` 又发了一次
   （本机日志两次 `POST /aptran/allsave.php`），假站点写了两张。真站点若在写库后断开也可能落两张。处理：找回时同日同额、核对得上的
   新单不止一张 → 不删不改，列出两个单号交人（`readback_mismatch`）。

## 配方里「待探路核对」的项（60 项，全部 `verified: false`）

`login`；`list`；三个模块 `modules.aptran / artran / arse` 本身与表头每一格：`txtdocnum`、`txtdate`、`chkcash`、`txtothvatrate`、
对方格（`txtapmas` / `txtarmas`）、`txttopcode`（现销没有）、`txtvatnum`、`txtvatdate`、`txtdeliverydate`、`txtothcombrh`、`txtothdept_h`、
`txtothsuborder_h`、`txtemp`、`txtothcombrhcus`；三个模块的明细 `lines`；现销收款 `modules.arse.receipt`；四个单种 `forms.purchase /
purchase_expense / sales_credit / sales_cash`；六条弹框规则 `dialogs.kicked / not_logged_in / bad_login / period / required / save_error`。
（`python3 -c "from companion.mrerp_operator import recipe; print(recipe.load().unverified())"` 列全。）

## 第 0 步探路要核对的清单（录制机 + TEST2019，只读为主，存一张后由人删）

| # | 怎么核 | 核什么 | 预期 |
|---|---|---|---|
| 1 | 开关打开，CC 测试包在录制机上 `--mrerp-desk` 起一次，打开登录页 | 登录页地址、`txtusers / txtpasswords / btnsubmit`、表单 action | `login/login.php`、三格都在、提交到 `checklogin.php` |
| 2 | 用专用账号登录 | 成功后跳哪；输错一次的弹框文字 | `selectdb.php`；「User หรือ Password ไม่ถูกต้อง」 |
| 3 | 选账套页 | TEST2019 那条链接的写法；进去后底栏 | `mainmenu.php?comidyear=N&seldb=1`；底栏 `Company : …`、`Database Name : …` 与目录采集的名字一致 |
| 4 | 四个菜单号各开列表 | `btnadd`、`txtsearch`、`btnsearch`、`#showdata` 行、查看链接 | 都在；行的前四格与服务端回读一致（拿真页面跑 `book.parse_listing` 与 `readback.parse_rows`） |
| 5 | 各开一次新增页（探路，不存） | 配方 `required_ids()` 每一格在不在、单号格是否只读（自动编号）、表头哪些格有默认值（分支 / 部门 / 工作 / 员工 / 对方分支） | 全在；记下各菜单是否自动编号 |
| 6 | 选择框 | 对方、商品、税率、仓的清单：聚焦后是否弹出 `#bshlistbox`、搜索框 keyup 能否筛、第一可见列是不是码；收起窗口时聚焦还弹不弹 | 能弹、能筛、第一可见列 = 码（税率 = 名称） |
| 7 | 选完对方 / 商品 | 页面带出的信用天数、标准成本价多久回来；明细的部门 / 工作 / 仓是否随表头默认带出 | 3 秒内（`timing.settle_s`）；默认带出（不带出就要补配方） |
| 8 | 日期格 | 只读 + 日历；直接写值后期间检查（`checkothperiod`）还跑不跑、是否要自己点日历 | 不跑就改成点日历的日子 |
| 9 | 录一张赊购（测试公司）、存 | 「Save Success」、一秒后回列表；存档前是否落库（填到一半关窗，列表里没有） | 存档前不落库；存后列表查得到 |
| 10 | 查看页 `allform.php?id=N&status=vie` | 单号 = `txtdoctype + txtdocnum`、各格可读、明细行 id 从 1 起 | 与配方一致 |
| 11 | 现销表单 | `txtcashpay`、`txttotalrec`、收款科目槽是否必填 | 填现金 = 含税合计即可存（否则补配方） |
| 12 | 同一账号在别处登录一次 | 本窗口下一页的弹框文字、跳到哪 | 「"User" ดังกล่าวมีผู้ Login ซ้ำ…」→ `logoutkick.php` → `login.php` |
| 13 | 收起窗口、锁屏各录一张 | 计时器、网络、焦点事件是否照常 | 照常（不照常改屏幕外放置） |
| 14 | `alert / confirm` | 是否都被截获、不弹出 | 都截获 |
| 15 | 实测资源 | 子进程 + 渲染进程（+ Windows 上可能有的 GPU 进程）内存、CPU；关窗后进程 | 记真数；关窗后全退 |
| 16 | 断网（拔网线）在存档那一下 | 浏览器内核是否自动重发 POST、真站点是否落两张 | 记结果（找回逻辑已按会落两张处理） |

## 离线测试与闸门（本机实跑，11-02）

环境：暂存目录 Python 3.12 venv（PySide6 / PySide6_Addons 6.11.1、pytest 8.3.3、httpx、dbf、aenum，与 `packaging/requirements-build.txt` 同版本），
`QT_QPA_PLATFORM=offscreen`，macOS 本机网页内核能跑。

新增测试：

- `companion/tests/test_mrerp_operator_replay.py`（真网页内核 + 假站点，13 条）：四种单录入并核对（一批登录一次、录完退出登录、截图只在存档后、
  关窗）；读回不符转人工且不改已存的单；存档那一下被踢 → 整批停、不重登、不退出登录、留「正在录」，换一批再派来先查到、不重录；存档前被踢
  → 什么都没写；写库后断网 → 恢复后重新登录、列表里找到、不重录；存档那一下断网（没到库）→ 等恢复后只录一张；浏览器重发成两张 → 交人；
  MR.ERP 拒收（存档出错）→ 没写、交人；放弃不留单；认不出的弹框交人；有人在 MR.ERP 窗口里打字 → 暂停到没人动再接着；密码不进日志 / 回执 /
  批次文件 / 探路缓存 / 截图清单，凭据只在登录页取一次、目标名带网址与连接号；没核对的配方不开窗、不登录。
- `companion/tests/test_mrerp_operator_process.py`（3 条）：开关关着时托盘不导入 `mrerp_operator`、不导入网页内核、只报 express；开关打开
  也是领到单才导入；子进程版一整张录完：子进程退出、它的渲染进程都退出。
- `companion/tests/test_mrerp_operator_parts.py`（30 条，纯逻辑，不起网页内核）：配方、草稿、凭据目标名、底栏解析、被踢 / 登录被拒后不再登录、
  被踢后关窗不退出登录、列表表头行、结果不明保留最早清单、没清单就是没存、不支持的不开页面、引擎不开页面的两条、弹框规则、文字助手、
  有人在用三条、`Runner` 三个注入、默认 / 开关下的装配与心跳、宿主用目标自带的草稿与设置、子进程命令、打包清单、窗口要画面不排队。
- `tests/test_mrerp_operator_readback_parity.py`（4 条）：同一份 HTML，CC 与服务端回读的行、找单、列表能不能用一致。

| 检查 | 结果 |
|---|---|
| CC 全套 `companion/tests`（上表环境） | 见下方「最终数字」 |
| 新包测试 replay / process / parts | 13 / 3 / 30 通过 |
| `tests/test_mrerp_operator_readback_parity.py` | 4 通过 |

（闸门、契约测试、CI 的最终数字在下面「最终数字」一节回填。）

Windows 的 Companion checks（`.github/workflows/companion.yml`，测试机 runner、`QT_QPA_PLATFORM=offscreen`）：回放与子进程测试先在子进程里
试起一次网页内核，起不来（没有桌面的服务账户可能起不来）就整组跳过，纯逻辑那 30 条照跑。

## 轻量实测（本机 macOS，假站点，一批 4 张）

| 项 | 数 |
|---|---|
| 不录时托盘进程 | 不导入 `mrerp_operator`、不导入网页内核（测试守住）；本次测量进程录完 4 张后仍没有网页内核模块，RSS 34 MB |
| 录时进程 | 子进程（浏览器侧）+ 1 个 `QtWebEngineProcess`（渲染） |
| 录时内存（两者合计） | 峰值 341 MB，平均 323 MB（macOS 数，含 Qt 与网页内核本体；Windows 真数等第 0 步） |
| 录时 CPU（两者合计，0.5 秒采样平均） | 4.2 % |
| 每张用时（假站点，含页面模拟的 0.06–0.25 秒异步） | 6.2–6.4 秒；4 张 29.4 秒 |
| 关窗后 | 子进程退出码 0，子进程树里没有剩余进程 |

真机 Windows 的内存、CPU、每张用时、收起 / 锁屏下是否降速等第 0 步。

## 要主会话定的事（不问用户）

1. **被踢用什么原因代码**：现在用已有的 `login_failed`（必须人工、整批停），细节写 `kicked: …`。前端文案是「自动登录失败」类，和「账号在别处
   登录」不完全对。建议第 3 步加新原因 `account_in_use`（CC 契约、共用层契约、前端三语、`tests/test_cc_operator.py` 计数一起改），本步按任务书
   不动前端与共用层。
2. **已停批次里同批号再派来的单**：Express 宿主现有行为是「批次已停 → 这张记停止跳过」，MR.ERP 同样。MR.ERP 这边靠本机 `mrerp-unsure.json`
   兜住「换一批 / CC 重启后再来先查」；同批号续录的口径与 Express 一起由第 3 步定。
3. **开关什么时候默认打开**：第 0 步通过、配方逐项改成 `verified: true` 后，由第 3 步（Cowork 接线）一起把 `operator_targets()` 默认带上 `mrerp`。

## 剩余项

- 第 0 步真站点探路（上表 16 项，等 TEST2019 授权），核对后把配方逐项改 `verified: true`。
- 第 3 步：站点按 `draft.py` 的 `mrerp` 块出载荷；同账号预检（`login.user_of` 只给用户名）、账号租约；CC 页面「MR.ERP 登录」按钮调桥
  `operator_authorize {target: "mrerp", url, connection}`；断开连接时 `operator_forget` 同参数。
- 看文字的 AI 助手（`helper.judge_text`）随站点 CC 机器通道接；现在认不出的弹框一律必须人工。
