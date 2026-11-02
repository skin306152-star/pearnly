# CC 端 MR.ERP 代录执行层（MR.ERP 代录第 2 步）施工记录

- 评估：[MR.ERP 代录与 ERP 左侧公司](2026-11-02-mrerp-operator-and-erp-company-评估.md) §13 第 2 步（§0 事实 3–6、§4、§5、§8、§9、§14 全部、§12.1 定案）；
  决定见 [ADR-026](../adr/0026-operator-targets.md)；第 1 步分路见 [代录目标分路](2026-11-02-cc-operator-target.md)。
- 分支 `pearnly/cc-mrerp-operator`，工作树 `.claude/worktrees/cc-mrerp-operator`，基线 `9be471c5`（= origin/main）。
- **本分支在第 0 步真站点探路通过之前不合入 main**（主会话定）。
- **线上测试账号等岳父开，开好后先只读核对表单（打开表单、读格子，不存），再录少量单；测试站 11-02 搭过当天删除，原因与线上不一致，其上结果不作数。**（用户 11-02 定：在那之前线上 mrerp4sme.com 一律不碰，只读也不碰；本分支继续不合入，默认开关不开。）本步只做不依赖真站点的部分；没连任何真实 MR.ERP。
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
                                                                   └─ web_driver.Driver：QtWebEngine 原生窗口（同 Express：开在屏幕上、不抢焦点）
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
| `web_driver.py` | 子进程里的网页内核：无名 `QWebEngineProfile()`（off-the-record，内存缓存、不存 Cookie）；关插件、禁脚本开窗；原生窗口同 Express 代录开在屏幕上（`WA_ShowWithoutActivating`：不抢前台、不抢焦点），`show`（桥 `operator_show`）才切到最前面；页面始终按「看得见」跑（每次加载完、每次查看状态时设回可见：人藏起 / 最小化窗口也全速）；脚本只在 `ApplicationWorld` 跑、结果 `JSON.stringify`；接管 `alert / confirm / prompt`（不弹出，文字进 `dialogs`；confirm 默认回取消，可预设下一次）；只许本站跳转、不开新窗口；页面控制台不进日志；人工输入：在窗口与渲染部件上挂事件过滤（点、键、滚轮计数，**不挂整个应用的过滤**——PySide 下那样会碰到析构中的对象崩溃，本机实测复现过）；`close` 先删页面再删资料；`UiCall` 把调用排进界面线程；`child_main` 子进程入口（口令读完即从环境删掉，不留给渲染进程）；`LocalDesk` 同进程版（测试用） |
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
| `tomo_bridge.py` | **不改**（11-02 主会话：归第 10 步「MR.ERP 凭据由 CC 授权」子代理独占）。第一版提交里加过 `operator_authorize / operator_forget` 的 `target=mrerp` 分支，已还原成基线；要的桥方法写在下面「要主会话协调的事」 |
| `i18n.py` | 新词条 `op_mrerp_login_title`（MR.ERP 登录 / เข้าสู่ระบบ MR.ERP），用户名、密码沿用 `op_login_user / op_login_password` |
| `packaging/companion.spec` | 隐藏导入加 `mrerp_operator` 各模块、配方数据文件；排除名单不改、不加依赖（网页内核本来就在包里） |
| `scripts/impact/shared-parts.toml` | 新入口 `cc.desktop.operator.mrerp`（offline），挂到共用件 `cc-operator-target` |
| `.github/workflows/development.yml` | 加一步 `tests/test_mrerp_operator_readback_parity.py` |

## 11-02 真站点实测（mrerp4sme.com，账号 test01，主会话转来）写进了配方与会话

| 项 | 实测 | 落到哪里 |
|---|---|---|
| 登录页 | `login/login.php` 的 `txtusers / txtpasswords / btnsubmit` 能用；提交后到 `login/selectdb.php`，约 0.4–0.5 秒 | 配方 `login.checked`（`page / user_id / password_id / submit_id / select_page` 标「11-02 真站点实测」） |
| 选账套 | 链接形如 `mainmenu.php?comidyear=<n>&seldb=1`，文字就是账套名（例如 TEST2019） | 配方 `login.account_link` 标实测；会话点之前先核链接文字 = 载荷账套名，不符 `company_mismatch`、不点进去 |
| 被踢 | 第二个会话登录后，旧会话下一次打开页面弹「"User" ดังกล่าวมีผู้ Login ซ้ำ จะทำการออกจากระบบโดยอัตโนมัติ」，然后回到 `login/login.php` | 配方 `dialogs.kicked` 改 `verified: true`；会话按这段文字加「回到登录页」判定（原来就这样判） |
| 账套库不存在 | 主菜单页正文「Connect Database Server Error : Unknown database '<库名>'」 | 配方 `login.account_set_missing` 标实测；会话识别后整批停，原因暂复用 `company_mismatch`（细节 `account set cannot be opened: unknown database <库名>`），见「要主会话定的事」 |
| 资源（Mac、PySide6 6.11.1） | 主进程约 320 MB；每多一个会话多一个网页内核进程，约 100–115 MB | 记在下面「轻量实测」 |

其余格子（表单 action、底栏、退出登录、表单 / 列表 / 查看页、其余弹框）仍「待探路核对」；底栏改成读得到才核（账套名已按链接文字核过）。

## 与 Express 代录的对照（主会话 11-02：要做到和看 Express 代录一样——人看得到真实界面一格一格地填、一张一张地存）

先读的 Express 实际做法：`express_operator/host.py`（`show` / `frame` / `control`）、`bg_desk.py`（后台发消息、`PrintWindow` 截图）、
`win32_desk.py::show_main`、`presence.py`、`evidence.py`、`tomo_bridge.py::_operator`、`frontend/assistant/src/components/operator/`（`live-source.ts`
只调 `operator_state / operator_control / operator_authorize`）。

| 项 | Express 代录 | MR.ERP 代录 | 一致？ | 说明 |
|---|---|---|---|---|
| 录的时候窗口在不在屏幕上 | 代录照常启动 `ExpressI.exe`（普通窗口，在屏幕上，可能在别的窗口后面）；后台发消息，从不抢前台 | `--mrerp-desk` 子进程开一个普通窗口在屏幕上，`WA_ShowWithoutActivating`：不抢前台、不抢焦点；页面里一格一格地填、点 Save、回列表、开查看页，人看得到 | 一致 | 第一版是默认收起，按本条改成开在屏幕上 |
| CC 实操页工作区默认显示 | 任务列表、进度、每张状态、要处理的项（`operator_state` 的批次快照）；不显示实时画面 | 同一个工作区、同一份批次快照（同一个 `Runner.snapshot`，`operator_state` 原样带出），不用改前端 | 一致 | 前端零改动 |
| 「查看」 | 桥 `operator_show` → `Host.show` → `desk.show_main`（Express 主窗口切到最前面）；页面目前没有调这个桥方法 | 同一个桥方法 → `Host.show` → `DeskView.show_main` → 子进程 `show`（MR.ERP 窗口切到最前面）；等批次手上的动作最多 0.5 秒，不卡 CC 窗口 | 一致 | 页面将来加按钮时两种目标同一个调用 |
| 实时画面 | 桥 `operator_frame` → `Host.frame` → `bg_desk` 的 `PrintWindow`（只画 Express 主窗口，被挡也能截）；页面目前没有轮询这个桥方法 | 同一个桥方法 → `Host.frame` → `DeskView.screenshot` → 子进程 `view.grab()`（只画 MR.ERP 窗口）；同样等不到 0.5 秒就这次不给 | 一致 | 截图频率、「只在页面看得见时刷新」现在两种目标都没有（页面没轮询），以后加在页面一处、两种目标共用 |
| 进度 | `Runner` 落盘、`snapshot_of` | 同一个 `Runner` / `BatchStore` / `snapshot_of` | 一致 | |
| 每张存证截图 | 存档后截一张，`Evidence` 存本机 30 天，汇报里「回看」 | 存档并核对后截查看页一张，同一个 `Evidence`、同一个到期清理；登录页不截 | 一致 | |
| 有人在用 | 后台做法：只有人把本批的 Express 切到最前面又在动键盘鼠标才暂停（低级键鼠钩子，排除 CC 自己发的）；锁屏、用别的程序不算；恢复 = 连续 30 秒没人动或点「继续」；状态 `paused / user_active`，证据进汇报 | 窗口部件上的事件过滤：在 MR.ERP 窗口里点、打字、滚轮才算，只是鼠标移过不算（窗口在最前面时移动也算，同 Express 的「切到前面又在动」）；我们只跑页面脚本、不发 Qt 输入事件，所以不用排除自己；恢复、状态、证据、「继续」（`Host.control resume → presence.resume_now`）同一套 | 一致 | 证据多一个 `source: mrerp_window` |
| 锁屏照录 | 后台发消息不经过输入桌面，锁屏照录 | 只跑页面脚本、不碰键盘鼠标，锁屏不影响操作本身；页面始终设为「看得见」防降速 | 待第 0 步实测 | 本机只能在 offscreen 下测（下一行）；真 Windows 锁屏下网页内核会不会把页面当看不见、截图能不能出画面，第 0 步实测 |
| 窗口收起 / 最小化 / 被挡住照录 | 照录（发消息、`PrintWindow`） | 本机实测（offscreen）：窗口藏起时 20 毫秒的页面计时器只走约 0.9 次/秒（网页内核降速），设回「看得见」后 50 次/秒；最小化、放在屏幕外都是 50 次/秒。所以每次加载完、批次每次看页面状态时都把页面设回「看得见」（测试守住：藏起后两秒仍走几十次） | 本机一致；真机待第 0 步 | 真 Windows 上最小化 / 被完全挡住时网页内核另有的「看不见」判定第 0 步实测；不行按评估 §14.2 改成放屏幕外而不是最小化 |

## 源码里查到、影响设计的两件事（第 0 步要实测）

1. **被踢时 MR.ERP 照样写库**：`component/php/checkuseruse.php` 发现会话号对不上只打出 `alert + location=logoutkick.php` 的脚本，
   **没有 `exit`**，`allsave.php` 接着往下 `INSERT` 并提交。所以「点了存档才发现被踢」= 结果不明（可能已存），按崩了留「正在录」、
   本机记下，续录前先查；「存档前被踢」（打开新增页就被踢）= 什么都没写。
2. **浏览器内核会自动重发断在半路的 POST**：假站点「写了库、回包前断开」时，网页内核在新连接上把 `allsave.php` 又发了一次，
   假站点写了两张。**已从源头关掉**（主会话 11-02 要求先试源头），做法与证据见下一节；关掉的开关在配方里，关不掉 / 关掉后嫌慢时的退路
   仍在：找回时同日同额、核对得上的新单不止一张 → 不删不改，列出单号交人（`readback_mismatch`）。

### 存档请求不被自动重发：做法与证据

- **原因**（Chromium 网络层 `HttpNetworkTransaction` 的重发规则）：请求走的是**复用的长连接**、连接在收到回包头之前断了，就在新连接上
  再发一次，不分 GET / POST。新开的连接上断了不重发。
- **复现**（假站点给每个请求记「在它那条连接上是第几个请求」和请求头 `Connection`，`_fake_mrerp.py::sockets`）：不加处理时，存档
  POST 走的是一条已用过 18 次的长连接，回包前断开后，网页内核在一条新连接（第 1 个请求）上又发了一次，假站点两张。
- **做法**：MR.ERP 窗口的浏览器资料挂一个请求拦截器（`QWebEngineUrlRequestInterceptor`，`web_driver._NoReuse`），每个请求带
  `Connection: close`：服务器回完就关连接，连接池里没有可复用的长连接，所以存档 POST 永远走新连接，断了也不会被重发。点「Save」的方式
  不变（还是页面自己的按钮、页面自己的提交）。开关在配方 `save.no_connection_reuse`（默认 `true`），会话开窗后按配方告诉子进程。
- **证据**（`test_mrerp_operator_replay.py`）：
  - `test_a_save_cut_off_after_the_write_is_not_sent_again_by_the_browser`：默认做法下同样「写了库、回包前断开」，存档 POST 只有一次、
    在它那条连接上是第 1 个请求、请求头 `Connection: close`，全程没有一个请求走复用的连接；假站点一张，续录在列表里找到它，结果「已存」；
  - `test_with_connection_reuse_the_browser_sends_the_save_twice_and_it_is_reported_not_hidden`：配方关掉这一项时照旧复现两次 POST
    （第一次在复用的连接上、第二次在新连接上）、两张，结果交人、两个单号都列出——退路仍有效。
- **代价与限制**（第 0 步核）：每个请求多一次建连，HTTPS 还多一次 TLS 握手，真站点每张多几百毫秒级（假站点在本机看不出差别）；
  真站点若走 HTTP/2，`Connection` 头不起作用（HTTP/2 多路复用，重发规则另算），要核协议；嫌慢可把配方这一项关掉，退回「不止一张交人」。

## 配方里「待探路核对」的项（11-02 真站点实测后剩下的）

`login` 里没实测的键（表单 action、底栏与两个标签、主菜单 / 退出 / 登录页清单）；`list`；三个模块 `modules.aptran / artran / arse` 本身与表头每一格：`txtdocnum`、`txtdate`、`chkcash`、`txtothvatrate`、
对方格（`txtapmas` / `txtarmas`）、`txttopcode`（现销没有）、`txtvatnum`、`txtvatdate`、`txtdeliverydate`、`txtothcombrh`、`txtothdept_h`、
`txtothsuborder_h`、`txtemp`、`txtothcombrhcus`；三个模块的明细 `lines`；现销收款 `modules.arse.receipt`；四个单种 `forms.purchase /
purchase_expense / sales_credit / sales_cash`；五条弹框规则 `dialogs.not_logged_in / bad_login / period / required / save_error`（`dialogs.kicked` 已实测）。
（`python3 -c "from companion.mrerp_operator import recipe; print(recipe.load().unverified())"` 列全。）

## 第 0 步探路要核对的清单（录制机 + TEST2019，只读为主，存一张后由人删）

**线上测试账号等岳父开，开好后先只读核对表单（打开表单、读格子，不存），再录少量单；测试站 11-02 搭过当天删除，原因与线上不一致，其上结果不作数。**（用户 11-02 定：在那之前线上 mrerp4sme.com 一律不碰，只读也不碰；本分支继续不合入，默认开关不开。）

| # | 怎么核 | 核什么 | 预期 |
|---|---|---|---|
| 1 | 开关打开，CC 测试包在录制机上 `--mrerp-desk` 起一次，打开登录页 | 登录页地址、`txtusers / txtpasswords / btnsubmit`、表单 action | `login/login.php`、三格都在、提交到 `checklogin.php`（三格 11-02 已实测；这里只核表单 action 与 CC 子进程里打得开） |
| 2 | 用专用账号登录 | 成功后跳哪；输错一次的弹框文字 | `selectdb.php`；「User หรือ Password ไม่ถูกต้อง」 |
| 3 | 选账套页 | TEST2019 那条链接的写法；进去后底栏 | `mainmenu.php?comidyear=N&seldb=1`；底栏 `Company : …`、`Database Name : …` 与目录采集的名字一致（链接写法与文字 = 账套名 11-02 已实测；这里核底栏） |
| 4 | 四个菜单号各开列表 | `btnadd`、`txtsearch`、`btnsearch`、`#showdata` 行、查看链接 | 都在；行的前四格与服务端回读一致（拿真页面跑 `book.parse_listing` 与 `readback.parse_rows`） |
| 5 | 各开一次新增页（探路，不存） | 配方 `required_ids()` 每一格在不在、单号格是否只读（自动编号）、表头哪些格有默认值（分支 / 部门 / 工作 / 员工 / 对方分支） | 全在；记下各菜单是否自动编号 |
| 6 | 选择框 | 对方、商品、税率、仓的清单：聚焦后是否弹出 `#bshlistbox`、搜索框 keyup 能否筛、第一可见列是不是码；收起窗口时聚焦还弹不弹 | 能弹、能筛、第一可见列 = 码（税率 = 名称） |
| 7 | 选完对方 / 商品 | 页面带出的信用天数、标准成本价多久回来；明细的部门 / 工作 / 仓是否随表头默认带出 | 3 秒内（`timing.settle_s`）；默认带出（不带出就要补配方） |
| 8 | 日期格 | 只读 + 日历；直接写值后期间检查（`checkothperiod`）还跑不跑、是否要自己点日历 | 不跑就改成点日历的日子 |
| 9 | 录一张赊购（测试公司）、存 | 「Save Success」、一秒后回列表；存档前是否落库（填到一半关窗，列表里没有） | 存档前不落库；存后列表查得到 |
| 10 | 查看页 `allform.php?id=N&status=vie` | 单号 = `txtdoctype + txtdocnum`、各格可读、明细行 id 从 1 起 | 与配方一致 |
| 11 | 现销表单 | `txtcashpay`、`txttotalrec`、收款科目槽是否必填 | 填现金 = 含税合计即可存（否则补配方） |
| 12 | 同一账号在别处登录一次 | 本窗口下一页的弹框文字、跳到哪 | 「"User" ดังกล่าวมีผู้ Login ซ้ำ…」→ `logoutkick.php` → `login.php`（11-02 已实测；这里只核 CC 窗口里截获到、整批停、不重登） |
| 13 | 窗口开在屏幕上录一张；再最小化、被别的窗口完全挡住、锁屏各录一张 | 开窗是否抢前台 / 焦点；计时器、网络、焦点事件是否照常；`operator_frame` 截图是否有画面 | 不抢前台；照常（不照常改放屏幕外而不是最小化）；有画面 |
| 14 | `alert / confirm` | 是否都被截获、不弹出 | 都截获 |
| 15 | 实测资源 | 子进程 + 渲染进程（+ Windows 上可能有的 GPU 进程）内存、CPU；关窗后进程 | 记真数；关窗后全退 |
| 16 | 被踢后点存档：用同一账号在别处登录后，回到 CC 这边的新增页填好一张点「Save」 | 列表里有没有这张（源码看 `checkuseruse.php` 打出踢人脚本后不停） | 照样落库；CC 这边结果不明、整批停、不重登，换人处理后再派来先查到、不重录 |
| 17 | 存档半路断网：点「Save」的同时断网（拔网线 / 关 Wi-Fi 几秒），先在配方 `save.no_connection_reuse=false` 下做一次，再在默认 `true` 下做一次 | 网页内核会不会重发存档 POST、真站点落几张；站点是 HTTP/1.1 还是 HTTP/2；默认做法下每张多花多少时间 | `false` 时可能落两张（交人）；`true` 时不重发、最多一张（HTTP/2 则另议）；耗时可接受就保持 `true` |

## 离线测试与闸门（本机实跑，11-02）

环境：暂存目录 Python 3.12 venv（PySide6 / PySide6_Addons 6.11.1、pytest 8.3.3、httpx、dbf、aenum，与 `packaging/requirements-build.txt` 同版本），
`QT_QPA_PLATFORM=offscreen`，macOS 本机网页内核能跑。

新增测试：

- `companion/tests/test_mrerp_operator_replay.py`（真网页内核 + 假站点，16 条）：四种单录入并核对（一批登录一次、录完退出登录、截图只在存档后、
  关窗）；读回不符转人工且不改已存的单；存档那一下被踢 → 整批停、不重登、不退出登录、留「正在录」，换一批再派来先查到、不重录；存档前被踢
  → 什么都没写；写库后断网 → 恢复后重新登录、列表里找到、不重录；存档那一下断网（没到库）→ 等恢复后只录一张；浏览器重发成两张 → 交人；
  MR.ERP 拒收（存档出错）→ 没写、交人；放弃不留单；认不出的弹框交人；有人在 MR.ERP 窗口里打字 → 暂停到没人动再接着；密码不进日志 / 回执 /
  批次文件 / 探路缓存 / 截图清单，凭据只在登录页取一次、目标名带网址与连接号；没核对的配方不开窗、不登录；账套库不存在整批停；
  账套链接文字对不上不点进去；窗口开在屏幕上不抢焦点、藏起后页面计时器仍全速。
- `companion/tests/test_mrerp_operator_process.py`（3 条）：开关关着时托盘不导入 `mrerp_operator`、不导入网页内核、只报 express；开关打开
  也是领到单才导入；子进程版一整张录完：子进程退出、它的渲染进程都退出。
- `companion/tests/test_mrerp_operator_parts.py`（30 条，纯逻辑，不起网页内核）：配方、草稿、凭据目标名、底栏解析、被踢 / 登录被拒后不再登录、
  被踢后关窗不退出登录、列表表头行、结果不明保留最早清单、没清单就是没存、不支持的不开页面、引擎不开页面的两条、弹框规则、文字助手、
  有人在用三条、`Runner` 三个注入、默认 / 开关下的装配与心跳、宿主用目标自带的草稿与设置、子进程命令、打包清单、窗口要画面不排队。
- `tests/test_mrerp_operator_readback_parity.py`（4 条）：同一份 HTML，CC 与服务端回读的行、找单、列表能不能用一致。

| 检查 | 结果 |
|---|---|
| CC 全套 `companion/tests`（上表环境，提交 `2413f354`） | 1476 通过、42 跳过、0 失败（第 1 步时 1426 通过、46 跳过；多出的是本步新测试，跳过少了是这个环境装了网页内核） |
| 之后引擎小修（存档后要人 / 点停止也记「结果不明」、存档后已跳回列表也算存上）复跑受影响的 `test_mrerp_operator_*` + `test_operator_host` + `test_operator_batch` | 90 通过 |
| 新包测试 replay / process / parts | 13 / 3 / 30 通过 |
| 11-02 补充（真站点实测写进配方与会话、窗口开在屏幕上、还原 `tomo_bridge.py`、Python 3.11 的 f-string 修正）后：CC 全套 | 1479 通过、42 跳过、0 失败 |
| 同上：新包测试 replay / process / parts | 16 / 3 / 30 通过（49） |
| 11-02 再补：存档请求不被自动重发（拦截器 + 配方开关）后：新包测试 replay / process / parts | 17 / 3 / 30 通过；CC 全套 1464 通过、59 跳过（外层这 17 条回放记跳过、子进程里全过）；本地全闸门 166 / 166（含 `npm ci`） |
| 回放改到单独子进程后：CC 全套 | 1464 通过、58 跳过、0 失败（外层 1 条拉起子进程，子进程里 16 条回放全过；外层这 16 条记跳过） |
| `tests/test_mrerp_operator_readback_parity.py` | 4 通过 |
| `tests/test_companion_contract.py` | 86 通过 |
| `tests/test_express_operator_shared.py` | 11 通过 |
| 本地全闸门（`~/pearnly-devtools/gate.sh`，照 `development.yml` 全部 run 步骤） | 166 / 166 通过（第一遍 `SKIP_NPM_CI=1` 时 4 步因本工作树没装前端依赖失败：`frontend/assistant` 构建、`test_assistant_ui.py`、`test_cc_operator.py`、`test-dms-editor-snapshot.cjs`；`npm ci` 后这 4 步重跑通过，前端本步没改） |
| `python3 scripts/check-architecture.py` | PASS（0 违规） |
| `python3 scripts/flows/coverage.py`（基线 `9be471c5`） | 通过（改到入口文件 0 个） |
| `python3 scripts/check-copy.py --report` | 新增 1 · 改动 0 · 拦截 0 · 标黄 0，清单见下 |
| `python3 scripts/check-impact.py` | 登记表完整（55 个共用件、200 个入口）；命中 `cc-dbf-write`、`cc-desktop-bridge`、`cc-operator-target` |
| `git diff --check` | 通过 |

文案清单（`check-copy.py --report`）：

| 来源·键 | 角色 | 中文 | ไทย |
|---|---|---|---|
| companion·op_mrerp_login_title | 标题 | MR.ERP 登录 | เข้าสู่ระบบ MR.ERP |

（CC 桌面版托盘文案只有中、泰两语，`companion/src/companion/i18n.py`；用户名、密码两格沿用已有词条。）

### 入口实测（`check-impact.py --files` 本步改动，15 个）

本步对 Express 代录、直写与心跳默认行为零变化；要测试机 / 录制机的实机项本步没有授权去动，按「没测到」如实记，离线覆盖写在原因里。

| 入口 | 方式 | 结果 |
|---|---|---|
| `cc.desktop.operator.mrerp` | offline | 通过（replay 13、process 3、parts 30、parity 4）；实机没测到（第 0 步，等 TEST2019 授权） |
| `cc.desktop.operator` | offline | 通过（CC 全套含 `test_operator_*` 全过；`Runner` 三个注入不传 = 原行为） |
| `cc.operator.page` | offline | 通过（`tests/test_cc_operator.py`，全闸门内；前端未改） |
| `cc.desktop.bridge` | manual | 没测到（实机要测试机 CC）；桥只多了 `target=mrerp` 分支，不带照旧 |
| `cc.settings.connection` | auto | 没测到（冒烟要实验栈 + 测试机）；本步未改设置页 |
| `cc.desktop.write` | auto | 没测到（写 C 盘 T 账套要测试机）；写账线只改心跳一行（默认值相同，`test_operator_host` 断言 `["express"]`） |
| `cc.desktop.places` | manual | 没测到（实机）；心跳默认 `operator_targets` 不变 |
| `cowork.cc.channel` | manual | 没测到（实机）；服务端未改 |
| `erp.cc.connect` | manual | 没测到（实机）；服务端未改 |
| `erp.cc.write` / `erp.cc.stock-entry` | auto | 没测到（测试机）；命中只因 `worker.py` 心跳一行，直写路径未改 |
| `erp.cc.stock-void` / `erp.cc.pos-day` / `erp.cc.pos-refund` / `erp.cc.stock-transfer` | manual | 没测到（实机）；同上，直写路径未改 |

分支 CI 第一次（提交 `044ed206` / `01727774`）：Development checks 通过；Companion checks 在收集阶段失败——`companion/tests/_fake_mrerp.py`
一处 f-string 表达式里带反斜杠，Windows 上的 Python 3.11 不认（本机 3.12 认）。已改，并用 Python 3.11 逐个编译了本步新增 / 改动的文件。

分支 CI 第二次（提交 `8b8b3101`）：Development checks 通过；Companion checks 1490 通过、30 跳过、1 失败——Windows 上网页内核回放确实跑起来了
（没跳过），但同一 pytest 进程里排在后面的 `tests/test_window_process.py::test_the_tray_logs_when_the_open_window_stops_and_starts_painting`
多收到一条前一个测试留下的「本地管道断开处理失败」日志。网页内核进了进程卸不掉、会改变同进程后面 Qt 测试的事件时序；改成回放整组在
**单独一个 pytest 子进程**里跑（外层一条测试拉起、看结果），主测试进程不再加载网页内核。没有改 `test_window_process.py`。

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
| 11-02 真站点实测（主会话转来，Mac、PySide6 6.11.1） | 主进程约 320 MB；每多一个会话多一个网页内核进程，约 100–115 MB |

真机 Windows 的内存、CPU、每张用时、收起 / 锁屏下是否降速等第 0 步。

## 要主会话协调的事（文件归属：`tomo_bridge.py`、`express_operator/credentials.py`、`login_dialog.py`、`frontend/assistant/src/lib/desktop.ts` 归第 10 步）

本步只调用、不改这几个文件。第 10 步需要接上的（MR.ERP 这边已备好被调的函数，`companion/src/companion/mrerp_operator/login.py`）：

1. 桥 `operator_authorize`：带 `{target: "mrerp", url, connection}` 时调 `mrerp_operator.login.ask_and_store(url, connection, lang)`（原生小窗，标题词条
   `op_mrerp_login_title` 已加）；不带 `target` 照旧 Express；
2. 桥 `operator_forget`：同参数调 `mrerp_operator.login.forget(url, connection)`（断开连接 / 移除电脑时）；
3. 桥 `operator_state`：多带「这台电脑存了哪些 MR.ERP 连接的登录」（`mrerp_operator.login.stored(url, connection)`；站点做同账号预检要用户名时
   `login.user_of`，只回用户名）；
4. 凭据目标名定为 `Pearnly CC/MR.ERP/<主机>/<连接号>`（`session.credential_target`），存取用 `express_operator.credentials` 的 `store / read / exists / forget`
   原样（不改它）。第 10 步若改了存取方式或目标名，告诉我改 `session.credential_target` 一处。

## 要主会话定的事（不问用户）

1. **被踢用什么原因代码**：现在用已有的 `login_failed`（必须人工、整批停），细节写 `kicked: …`。前端文案是「自动登录失败」类，和「账号在别处
   登录」不完全对。建议第 3 步加新原因 `account_in_use`（CC 契约、共用层契约、前端三语、`tests/test_cc_operator.py` 计数一起改），本步按任务书
   不动前端与共用层。
2. **已停批次里同批号再派来的单**：Express 宿主现有行为是「批次已停 → 这张记停止跳过」，MR.ERP 同样。MR.ERP 这边靠本机 `mrerp-unsure.json`
   兜住「换一批 / CC 重启后再来先查」；同批号续录的口径与 Express 一起由第 3 步定。
3. **账套打不开用什么原因代码**：账套库不存在（「Connect Database Server Error : Unknown database …」）暂复用 `company_mismatch`（整批停），前端文案
   「公司与任务不符」不贴切；建议与第 1 条一起加 `account_set_unavailable`。
4. **开关什么时候默认打开**：第 0 步通过、配方逐项改成 `verified: true` 后，由第 3 步（Cowork 接线）一起把 `operator_targets()` 默认带上 `mrerp`。

## 剩余项

- 第 0 步真站点探路（上表 16 项，等 TEST2019 授权），核对后把配方逐项改 `verified: true`。
- 第 3 步：站点按 `draft.py` 的 `mrerp` 块出载荷；同账号预检（`login.user_of` 只给用户名）、账号租约；CC 页面「MR.ERP 登录」按钮调桥
  `operator_authorize {target: "mrerp", url, connection}`；断开连接时 `operator_forget` 同参数。
- 看文字的 AI 助手（`helper.judge_text`）随站点 CC 机器通道接；现在认不出的弹框一律必须人工。
