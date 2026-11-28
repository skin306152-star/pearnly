# 云端 Express 第二阶段施工记录（2026-11-25 起）

- 评估：`docs/work/2026-11-24-express-cloud-stage2-assessment.md`（主会话 11-24 审定；施工顺序按其第 12、13 节）。
- 分支 `pearnly/express-cloud-stage2-build`，基线 `origin/main` **4eb0b2499**；先带上 D1/D2 修复（`pearnly/express-cloud-fixes` 的两个提交，cherry-pick），列车进 main 后变基去重。
- CC 改动随 **1.12.26**；1.12.25 发出前不合进 main。发版顺序：先部署服务端，再发 CC。
- 我们登录云端主机只在 20:00–07:00（泰国时间），由主会话约；不是产品功能。

## 证据层级的写法

每段都分四层写：**离线**（Mac 上单元 / 集成测试，Windows 专属调用都是假的）· **假环境**（本机或测试机上用真文件系统 / 真 Windows 行为但没有远程桌面）· **测试机1**（真 Windows + 测试版 CC，未连云端）· **真机**（测试机1 连云端主机）。
11-24 的教训：`\\tsclient` 只在假目录测过、真机直接挂。所以凡是在云端会话里才有的行为（隧道盘、重定向盘上的目录通知、云端凭据管理器、启动文件夹）一律标「真机没测到」，直到夜间真机最小闭环做过。

## 段 ①：D3 云端成员（阻塞项）

做法（评估 4.1）：
- 桥：`CloudHost.publish_members()` 把这台云端电脑登记了的站点（站点、通道、令牌里的端点号；**不含令牌**）用当前隧道会话密钥加密写 `ctl\members.json`。时点：每次换会话（`_rotate`，含 start）后；对话页交回新站点令牌（`tomo_bridge._cloud_token`）后。停宿主时连同其它状态文件一起删。
- 加密：新模块 `cloud_link/ctl_files.py`（AES-256-GCM，密钥由会话密钥经 HKDF 按种类派生，附加数据含种类 / 会话号 / 编号）。同一模块预留 `status.json`、`rescan.json`、`add-place.json` 三种（段 ② 用）。
- 云端：`cloud_side.check_ctl` 先换会话、再读成员文件（`take_members`）→ `cloud_link/cloud_members.sync` 按下发建 / 改 / 删 `cloud-<站点>` 成员：地址 `https://<站点>`，令牌只放占位 `<前缀><端点号>_cloud-relay`（前缀定通道，请求经隧道时桥丢掉云端的 `Authorization` 换真令牌）；账套先用本机已有成员的数据根，没有就照「第一次连接」自动找（云端模式只找本机盘），找不到先不建、结果码 `no_books`。只动 `cloud-` 开头的成员。
- 云端没有成员时不退出：`main._run_dbf` 云端模式直接进 `step_cloud_loop`，等隧道 → 等成员（`CloudLink.wait_change`）→ 跑直写；成员变了（`generation` 变）停下直写循环、重读成员再起。原守护测试「云端无成员以 2 退出」改为「等待、不退出」；`--headless` 无成员仍以 2 退出。
- 认电脑：云端模式下 `device_link.device_key()` 取配对存下的设备密钥（`cloud_side.paired_device_key`，同桥 `LinkStore.device_key`、对话页登记用的那把），不在云端另生成一把（否则 Cowork 的认电脑会报另一把密钥）。
- 云端精简包 `hiddenimports` 补 `cloud_members`、`ctl_files`。

证据：
| 项 | 离线 | 假环境 | 测试机1 | 真机 |
| --- | --- | --- | --- | --- |
| ctl 文件加解密、换会话 / 改密文 / 改种类拒绝、内容无令牌 | ✅ `tests/test_cloud_members.py` | — | — | — |
| 桥起会话 / 交回令牌即下发、停时删 | ✅（假窗口、假盘符） | — | 没测到 | 没测到 |
| 云端建 / 改 / 删成员、找不到账套不建、别的成员不碰 | ✅（真 `ProfileStore` + 假保护器、假探测） | — | — | 没测到（云端 DPAPI、真账套探测） |
| 云端无成员等待不退出（整包、拦 Qt） | ✅ `tests/test_cloud_package.py`（Mac 上跑整个启动入口，`\\tsclient` 不存在，进「等待隧道」） | — | — | 没测到 |
| 成员变了直写循环重载 | ✅ `tests/test_cloud_mode.py` | — | — | 没测到 |
| 云端读到成员文件的时延（`ctl` 目录通知是秒级还是 60 秒兜底，清单 d） | — | — | — | 没测到（夜间真机看云端日志「云端成员 · 结果=」与桥日志「云端成员已下发」的时间差） |
| 心跳经隧道到 testerp、报账套 | — | — | — | 没测到（真机最小闭环 V2） |

审查（code-review medium，D3 提交）：发现 6 条，真问题 3 条已修（重新下发清掉云端 401 标记；桥连换两次会话时成员文件不丢；端点号形状不认识时照样下发站点），3 条小问题记下不改（部分站点建不了时报 `no_books`；成员重载时直写循环发一次离线；测试补断言已修）。

## 段 ②：D4 找账套、状态文件、真「重新查找」、手填添加

做法（评估 4.2）：
- 云端找账套（`cloud_link/cloud_books.py`）：在本地「第一次连接」的找法上，另加直接探 `<本机盘>:\<云端用户名>` 与其下一层（不列盘根；11-24 首测受限账号列 `D:\` 只看得到「Help Express」，数据目录在 `D:\<用户名样的目录>\ExpressI-Cloud`）。D3 建成员默认也用这一套。评估里的「读运行中 Express 进程的工作目录」本段没做（取不到的机会大、受限账号下要真机看），真机若用户名目录也找不到再补。
- 状态文件 `ctl\status.json`（云端写、桥读后删，会话加密）：云端 CC 版本、成员结果码、各数据根与账套数、最近查找结果码与时间、最近「添加文件夹」结果（带编号）。云端在换会话后、成员处理后、命令做完后立刻写；平时随 ctl 监视每分钟兜底那一次，5 分钟才看一次、内容变了才写（空闲不多写）。
- 桥：中转每看一遍顺带读状态文件，存成宿主 `cloud_state`（只在内存，停宿主清空）并通知设置页；`cloud_status` 的 `places` 从它取（原来写死空列表），另回 `cloud {version, members, scan, scanned_at, add}`。
- 「重新查找」`cloud_rescan`、「添加文件夹」`cloud_add_place {path}`：桥写 `rescan.json` / `add-place.json`（会话加密、带编号），回编号；云端在后台线程一件一件做（不挡监视线程换会话），见过的编号不再做；手填路径要是绝对路径、在、不在 `\\tsclient` 下、下面找得到登记表（`not_allowed` / `bad_path` / `not_found` / `ok`）。已有成员就把数据根写进成员（`apply_roots`），没有成员就按最近一次下发的站点再建。结果随下一份 `cloud_status` 回来，页面按编号对上（页面在段 ⑤ 做）。

证据：
| 项 | 离线 | 假环境 | 测试机1 | 真机 |
| --- | --- | --- | --- | --- |
| 用户名目录探测（只列自己那层、怪用户名拒绝、列不了跳过） | ✅ `tests/test_cloud_books.py` | — | 没测到 | 没测到（**受限账号下列 `D:\<用户名>` 是否被允许是关键未知**，V3 夜间在云端 PowerShell 先 `Get-ChildItem D:\$env:USERNAME` 只读看） |
| 手填路径校验、不许 `\\tsclient` | ✅ | — | — | 没测到 |
| 状态文件加密、内容变了才写、5 分钟节奏、换会话立刻写 | ✅ | — | — | 没测到 |
| 桥读状态、设置页 places / cloud、停宿主清空、旧会话不认 | ✅（假窗口） | — | 没测到 | 没测到 |
| 命令一次一做、编号去重、旧会话丢弃 | ✅ | — | — | 没测到 |
| 实际在云端主机上找到 ExpressI-Cloud、账套数对得上 | — | — | — | 没测到（真机最小闭环 V3） |

## 段 ④（先于 ③）：D5 输一次记住、启动自动连、弹窗收密码、代录登录

段 ③ 自动安装要先过 V1（云端会话里控件能不能发 Unicode 字，夜间验），所以先做不依赖它的段 ④。

做法（评估 4.7）：
- 桥能力 `connect_dialog`（共用连接弹窗收密码；名字不带 cloud，没连云端的电脑 state 里不出现 cloud 字样的守护照旧）。
- `cloud_connect`：带 `inline: true` 时用弹窗里填的 `password`；没填就用本机已存的（同一主机、同一账号；端口等改动照存），没存回 `password_required`，页面在弹窗里显示密码框。不带 `inline` 的旧页面照旧弹原生小窗、页面交的密码不看。
- `cloud_status` 多回 `remembered`（本机存着密码）与登录失败时的 `reason`（`password` 账号或密码不对 / `password_change` 密码过期要改 / `account` 账号停用锁定受限 / 空）。
- 启动自动连：托盘起来 5 秒后，存有云端连接且本机存着密码就起宿主（开机、重启、升级同一条路）；没连过云端的电脑不导入 cloud_link。
- 被挤下线：会话停在「被别处登录挤下」时记进 `cloud_link.json`（`halted: replaced`），CC 重启不自动连（否则开机就把正在用的人挤掉）；人点「连接 / 重新登录」连上后清掉。普通断线照旧退避重连。
- 「代录登录」：`cloud_operator_authorize` 带 `inline: true` 收页面弹窗里的用户名、密码，只存桥本机（同原来的原生小窗那条），不回密码。

证据：
| 项 | 离线 | 假环境 | 测试机1 | 真机 |
| --- | --- | --- | --- | --- |
| 弹窗收密码 / 用已存的 / password_required / 旧页面照旧 | ✅ `tests/test_cloud_remember.py`（假凭据管理器） | — | 没测到（Windows 凭据管理器真读写） | 没测到 |
| 登录失败原因归类 | ✅（按原因名） | — | — | 没测到（真机要故意输错一次看原因码） |
| 启动自动连、已在连不重复、没存密码 / 被挤不连 | ✅（假宿主） | — | 没测到（测试机1 装测试版后重启 CC 看任务栏出现连接窗口） | 没测到（V6） |
| 被挤标记记下与清掉 | ✅ | — | — | 没测到（真机要另一处登录同账号，只在夜间、主会话批） |
| 代录登录只存桥本机 | ✅ | — | 没测到 | 没测到 |
| 页面（弹窗、「···」、原因文案） | — | — | — | 段 ⑤ 前端做 |

## 段 ⑤：服务端（账本来源、电脑组规则）与连接页前端

服务端（评估 4.8、4.10）：
- ERP 账本目录（`express.companies.catalog`）每组带 `source`：报这组的电脑全是云端那份（`link_kind = cloud`）才算 `cloud`，有本地的就算 `local`。`cc_service._catalog` 把 `cloud` 那组放进 `express_cloud` 标签；已建的、账套只由云端报的公司在 CC 选公司里也归「云端 Express」。「从连接添加」与待建清单的标签加 `express_cloud`。原来两处的云端标签是占位（不取数），现在取数。
- Cowork：账本目录目前没有按电脑区分的字段（可达行在池里），本段只做 ERP；Cowork 的「云端 Express」标签仍要等它的目录带上报告电脑——**未做，记为剩余项**。
- 电脑组：死记录规则放在共用契约 `cc_desktop.contracts.dead`（从没报过账本、不在线满 7 天）。ERP 电脑列表不列死记录、带 `last_seen` 与云端 `link`、不在线的能管公司的人可移除（`masters.cc_companies.remove_computer`，在线的拒，产品声明 `connect.remove_computer`）；共用机器通道登记新电脑时顺带退役死记录（`cc_desktop.devices.retire_dead`，`retired_by = auto`，回来走现有 rejoin）。Cowork 电脑列表不列 Express 上的死记录、同一台挂 Express 与 MR.ERP 两条连接的合成一行（`adapters`）。**Cowork 登记时顺带退役死记录未做**：Cowork 的移除要按「连接 → 密钥 → 电脑」顺序加锁吊销各把密钥（`credentials.remove_device`），放进登记事务里有锁序风险，单列剩余项。

前端（按用户 11-24 审定的草图）：
- 设置 → 连接：三组「站点 → 账本 → 电脑」；站点行去掉下面的「Express」小字。账本组：MR.ERP（授权过：名下授权人、「● 已连接 + ···」重新登录 / 授权详情；没授权：「连接」）→ 本地 Express（名下「N 个文件夹」，文件夹默认收起、点行展开；没找到 Express 时直接展开）→ 云端 Express（名下云端主机名，「● 状态 + ···」；展开：云端报的数据文件夹与账套数、「重新查找」「添加文件夹…」手填路径弹窗；「···」：重新登录、代录登录、云端安装命令兜底、主机 · 账号与云端 CC 版本、断开）。
- 连接弹窗：新桌面版（`connect_dialog`）地址、用户名、密码一个弹窗，点一次「连接」，弹窗里显示「正在连接…」、连上即关、登录失败写原因、被挤写「账号已在别处登录」；记住了的密码框显示圆点占位、留空即用存的。旧桌面版照旧弹原生小窗。
- 电脑组：名下写连着哪几种（本地 Express · MR.ERP），云端那台写「云端 Express · 经 <桥电脑名>」，同名两条补版本；移除接口按产品声明。
- 「Express」改名「本地 Express」：CC 设置、CC 选公司标签、ERP「从连接添加」标签（用户 11-24 当面同意）。原来这三处是写死的产品名、不是词条，改成新词条后 check-copy 记为新增（不触发 R-CHG），所以 `copy-approved.tsv` 不需要登记；同意依据记在这里。
- 草图没覆盖、这次也没加的：自动安装的「正在安装」（段 ③，等 V1）。

文案（`check-copy --report` 新增 18 条，拦截 0）：站点 / 账本 / 本地 Express / N 个文件夹 / N 个账套 / 重新登录 / 代录登录 / 路径 / 未找到 Express / 路径不可用 / 用户名或密码不正确 / 密码已过期 / 账号不可用 / 经 <电脑名>，四语齐。

证据：
| 项 | 离线 | 假环境 | 测试机1 | 真机 |
| --- | --- | --- | --- | --- |
| ERP 账本来源与云端标签、电脑列表规则、移除、登记退役死记录 | ✅ `tests/test_erp_cc_companies.py`、`tests/test_cc_desktop_channel.py`（仿 frappe） | 候选离线（生产镜像）见提交说明 | 没测到 | 没测到 |
| Cowork 电脑合一行、死记录 | 要真 frappe：`tests/test_cowork_folder_computers.py` 在候选离线里跑 | 同左 | 没测到 | 没测到 |
| 连接页三组、改名、收起、弹窗、电脑行小字 | ✅ `tests/test_cc_cloud_express.py`、`tests/test_assistant_ui.py`（node 渲染）、`tests/test_mrerp_login_pages.py`、前端闸门、构建 | 受影响流程 cc.settings / cc.companies / erp.company.connect 等**没跑**（本机实验栈） | 没测到 | 没测到 |

审查（段 ⑤ 自审，medium 做法）：真问题 2 条已修——「报过账本」改为按电脑分组查询（账套多时不被行数上限截断而误判死记录）；连接弹窗重新登录时不被提交前的旧「已连接」状态立刻关掉。

## 剩余（本窗口没做，原因见交接）
- 段 ③ 自动安装与自升级（V1 前置，夜间）；断开时连带删云端安装目录与启动项（与段 ③ 一起）。
- Cowork 登记时顺带退役死记录：**不做，只做显示侧**（死记录不列、离线的「···」可移除，主会话 11-25 定）。锁顺序风险：Cowork 移除一台电脑（`credentials.remove_device`）要按「连接 → 密钥 → 电脑」逐条加锁后吊销它每把密钥、删可达行、冻结的推送转人工；登记新电脑（`credentials` 的登记路径）此时已锁着新电脑所在连接与密钥行，若在同一事务里再去锁死记录所在的别的连接，加锁顺序就与并发的「移除电脑」「心跳改可达行」相反，可能死锁，而且登记失败会让真人连不上。要做得单独起一个不在登记事务里的清理（例如管理员在「电脑」列表手动移除，或登记提交后另排一次只读找、逐台按 remove_device 的锁序做），先不做。
- 受影响流程没跑；真机全部没测到。

## 段 ⑤ 补：Cowork「云端 Express」标签取数（11-25 主会话要求）
- 共用约定 `cc_desktop.wire.link_contracts.source_of / adapter_of`：报一本账（或同身份一组）的电脑全是云端那份（电脑行 `link_kind = cloud`）才算 `cloud` → 标签 `express_cloud`；有一台本地的或不知道都算本地。ERP 目录与 Cowork 都走它，`tests/test_companion_contract.py` 钉住「CC 心跳 link → 电脑行 link_kind → source_of → 页面标签」这一条线。
- Cowork：`connections.pool.sources(连接)` 一次查询可达行 join 电脑行得每份账套的来源（`connections.service.account_sources` 对外）；`companies.service._catalog` 按身份组判，全云端的组标签 `express_cloud`；选公司里已建、账套只由云端报的公司也归云端标签。离线测 `tests/test_cowork_companies.py`。

## 连接页用户审定（11-25）
- 截图（`~/pearnly-devtools/handoff-1125/cloud-connect-shots/`，cc-visual 假服务端 + 桌面版桥桩拍的真构建页面）经主会话转用户：**「可以」**（云端行展开、「···」菜单、一体化弹窗与各状态、电脑组定稿）。用户追问「云端前面加个图标干啥」→ 去掉云端行前的图标（没经审的装饰图标一律不加）。
- 同批按主会话意见改：「代录登录」→ 界面术语「实操登录」（zh 实操登录 / en Hands-on sign-in / ja 代行入力のサインイン / th 沿用 บันทึกแทน 一系）；账本组三行左侧对齐（MR.ERP 留与展开箭头同宽的空位）；云端行登录失败时右侧状态写桥给的同一个原因（与弹窗同源 `reason`），不再只写泛泛的「Express 登录失败」。
- 在线的电脑行没有「···」是有意的：服务端只对不在线的记录给移除（评估 4.10，在线的先在那台电脑上断开）。
- **待办（段 ③）**：「云端安装命令」是自动安装做好前的兜底；自动安装完成后从「···」去掉，或只在自动安装失败时出现，不留常驻入口。

## 段 ③ 设计（11-25 express-cloud-stage2-b 窗口；V1 没验，只定设计、不写依赖 V1 的代码）
三件事，按是否依赖 V1（云端会话里控件能否发 Unicode 字）分开：

| 件 | 依赖 V1 | 通道 | 做法 | 离线可测 |
| --- | --- | --- | --- | --- |
| a 首次安装 | **是** | 桥 → 远程桌面键盘 | 隧道盘 `cc\` 照旧由 `cloud_package.prepare` 备好；连接成功、桥已写 `ctl\session.json`，却 60 秒内既没有云端 `ctl\status.json`（已装的云端 CC 会报）也没有配对请求 `ctl\pair-req.json`（`device_pairing`）时，桥对控件发「Ctrl+Esc → 打 powershell → 回车 → 打 `install_command(盘符, 令牌)` → 回车」；按 V1 结果二选一：V1 过 → 用 Unicode 逐字发（键盘布局无关）；V1 不过 → 不自动，保留现兜底（「···」→「云端安装命令」+ 弹窗写原因）。令牌一次性、只在内存里拼进按键序列。 | 状态机（等 status / 配对请求 → 发键 → 等配对确认 `pair-ack.json` → 超时转兜底）和按键序列生成可离线测；发键本身只能真机 |
| b 自升级 | 否 | 隧道盘 `cc\`（已有） | 桥每次起宿主已跑 `cloud_package.refresh`（版本变了才重放 `cc\`）。云端那份 CC 每次看 `ctl`（`cloud_side.check_ctl`）顺带读 `cc\cloud.json`：比自己新、且自己装在 `%LOCALAPPDATA%\Pearnly\CC-Cloud\<版本>\companion.exe`，就拷包到本机、核大小与 sha256、解压到 `CC-Cloud\<新版本>`（与在跑的不同目录，不怕占用）；再起一个隐藏的 `powershell -Command`（内联命令，不跑脚本文件、执行策略不管）：等本进程退出 → 启动文件夹里**指向我们目录的** `CC.lnk` 改指新版本 → 起新版本 `--cloud`；本进程退出。旧版本目录留着；失败的版本不反复试。`cloud_link/cloud_self.py` | 全部离线测（`tests/test_cloud_self.py`）；小进程真改快捷方式、真重启只能真机 |
| c 断开连带删 | 否 | `ctl\`（同 members.json 的会话密钥信封） | 主会话 11-24 定「删」、11-25 定「只删我们自己装的那个目录（路径写死校验 + 目录内有我们的标记文件才删），别的一概不碰，删前后各回读一次」。桥「断开」还连着时先在后台写 `ctl\uninstall.json`、等同一编号回执 `ctl\uninstalled.json` 最多 20 秒（`CloudHost.uninstall_cloud`），再回界面线程停宿主、后台删连接（页面仍等同一个 job）。云端收到后校验三条：路径 = `%LOCALAPPDATA%\Pearnly\CC-Cloud`、本进程在它下面一层的版本目录、标记文件 `.pearnly-cc-cloud` 内容对（安装脚本写、11-25 前装的由云端 CC 启动时补）；对了才起小进程：等本进程退出 → 删指向我们目录的 `CC.lnk` → 再核一次标记 → 删该目录 → 回读写一行到目录外 `%LOCALAPPDATA%\Pearnly\cc-cloud-uninstall.txt`。回执带删前回读（目录在不在）；本进程退出。超时照常断开，只记日志（不加新文案）。配置、凭据、别的目录不动。 | 信封、校验、回执、桥等待与超时、断开顺序离线测；小进程真删只能真机 |

界面：a 做好后「云端安装命令」从「···」去掉、只在自动安装失败时随原因出现（上面已记）；a 进行中行上状态只用现有的「正在连接」，不新增文案；c 没有新增文案（超时只记日志）。
已知限制：桥没连着时点「断开」（连接窗口已关、没连上）没有通道请云端删，云端安装留着，下次连上也不补发；等回执的 20 秒里人又点「连接」，以后点的为准，这次断开不停新连接、不删连接。
code-review（medium，段 ③ b / c）：发现 6 / 真 6 / 修 5（第 6 条是上面的已知限制，记下不改）——小进程工作目录在要删的目录里、等旧进程 120 秒超时就接着做、等回执时重连被停掉、zip 别的异常反复重试、中间一步出错变 KeyError。
主会话 11-25 定：b、c 在 1.12.26 内先做（已做，证据层级「只离线」，夜间步骤单各加一步真机验）；a 等 V1 夜间结果。

## 测试机1 真 Windows A 组（11-25 16:20–16:50，express-cloud-stage2-b 窗口）
做法：线上站点对云端行是收起的（`cc_desktop/cloud_entry.py`），CC 窗口点不到「连接」，所以 A1 / A2 / A6 / A7 用分支源码（3dff988af）在 skin3 下以一次性计划任务跑 `settings_link.connect` 与 `CloudHost`（真凭据管理器、真 subst、真远程桌面控件，地址 192.0.2.10 不通）；A4 / A5 用装上的测试包（run 45576788253，ref 28e0524ee）真启动。用户 11-24 的真云端两条凭据（`TERMSRV/192.0.2.20`、`Pearnly CC/cloud/192.0.2.20`）全程原样在：目标名按主机分，且上机前 `cloud_link.json` 不存在（`connect` 只删已存的别的连接）。

| # | 结果 | 证据 |
| --- | --- | --- |
| A1 凭据管理器存取 | ✅ 真 Windows 验过 | `cmdkey` 多 `TERMSRV/192.0.2.10`、`Pearnly CC/cloud/192.0.2.10`；`cloud_link.json` 只有 `links`、`schema_version`，不含密码 |
| A2 记住密码 | ✅ | 同主机同账号密码留空 → `ok`；换账号留空 → `password_required` |
| A3 登录失败原因 | 没测到 | 测试机1 是家庭版、没有远程桌面主机；并进夜间 |
| A4 启动 5 秒自动连 | ✅ | 测试包 16:40:34 起，16:40:41 日志「启动时自动连云端」→ 换新会话、成员已下发（站点数 0）→ 隧道盘 Z:；不弹密码；连不通走 `disc_516 → dropped` 60 秒后重连。连接窗口最小化在任务栏**没截图**（没测到） |
| A5 被挤标记不自动连 | ✅ | 改 `halted: replaced` 重启 → 「云端上次被别处登录挤下线，启动时不自动连」，没起宿主 |
| A6 隧道盘与撤盘 | ✅ | 起宿主 Z: → `%LOCALAPPDATA%\Pearnly\cloud-tunnel\192.0.2.10`；停后撤掉、`ctl` 清空 |
| A7 members.json 加密 | ✅ | `ctl` 有 `session.json`、`members.json`；`members.json` 只有 v / kind / epoch / id / nonce / ct，没有站点明文 |
| A8 连接页 | 以本机截图代替 | 主会话定不 lab-start；用 `cloud-connect-shots/` 18 张定稿；真机新页面并进夜间 |

缺陷（真机发现、已修）：CC 被强行结束（任务管理器 / 崩溃 / 安装覆盖）后隧道盘 Z: 不撤、留到注销；下次启动不自动连（没存密码 / 被挤）或没连着就断开时也不撤。修：`cloud_link.host.release_stale_drive`（只撤指向本连接隧道根的那个盘符），启动不连与没连着断开两处调用；要连时照旧由宿主接着用。离线测 `tests/test_cloud_remember.py`。真机复验并进夜间。

## 11-28 夜间真机后的修复（express-cloud-stage2 窗口）

真机结果与证据见 `~/pearnly-devtools/handoff-1128/express-cloud-stage2.md` 第四节。以下按提交：

| 提交 | 问题（11-28 真机） | 改法 | 证据层级 |
| --- | --- | --- | --- |
| 8e3dffa0d ① | 安装脚本 / 启动项 / 会话文件都不带桥名，心跳不带 link，云端电脑显示「本地 Express」、ERP 云端标签空 | 缺 `--cloud-via` 时取远程桌面会话客户端名（`HKCU\Volatile Environment\<会话号>\CLIENTNAME`，再环境变量；`Console` 不算） | 离线；真机确认 CLIENTNAME=DESKTOP-BRIDGE1 |
| 4805628cf ② | 桥重启后隧道盘重新映射，`CtlWatch` 的 `watcher.wait` 抛错、线程静默退出 → 自升级 / 卸载 / 成员再也收不到；云端 CC 等会话退避最长 15 分钟 | 监视出错重开、线程不死；等会话时有隧道盘就盯 `ctl`，没有最多 30 秒探一次 | 离线 |
| b56132684 ③ | 线上清单带云端包后，测试构建的桥配对 / 重连 / 重启都把隧道盘 `cc\` 放成线上包 | 测试构建随包带云端包（`release.ps1 -BundleCloud` → `cloud-bundle\`），`cloud_package` 有随包就只用它；正式发布不带 | 离线 |
| e3beb64de / 8574fb33a ⑨a | 「断开连接」删了用户存的远程桌面密码与连接（实操登录那条反而留着） | 用户定：断开只断开（留连接与两条密码，记 `halted=user`，启动不自动连，「重新登录」免输）；删凭据是单独的「移除」（见下） | 离线 |
| 2a3a1b0b5 ⑨b | 自升级 / 卸载小进程在本进程退出后什么都没做（同一脚本手动跑成功） | 小进程改用 CREATE_NO_WINDOW + NEW_PROCESS_GROUP + BREAKAWAY_FROM_JOB（不许脱出就去掉），去掉 `-WindowStyle Hidden`；起止写 trace（`cc-cloud-upgrade.txt` / `cc-cloud-uninstall.txt`） | 离线；真机待验 |
| 77e2597b2 ⑨d | 登记时退役死记录 `retired_by="auto"` 过不了 User 链接校验，一台都没退役 | 记在 `Administrator` | 离线 |
| 8bd5dfc10 ⑨c | 云端 Z1(TEST) 与 Express 空白模板 DAT 同税号、同分店、同年度 → 选公司后账套默认落 DAT；在选择器里改成 Z1 下次又回 DAT | 两本按账套键（book_key，按目录区分）各是一个选项，默认绝不落在空白模板（数据文件夹 `DAT`）上；人在选择器里选过的那一本按公司记住（同月多本覆盖记在该年度下，都不覆盖记在 `uncovered` 下），之后作默认；拿不准（同月多本覆盖且没记过）照旧不给默认、要人选 | 离线 |

### ⑨c 判据（主会话 11-28 审过）
- 空白模板只按 Express 的通用事实判：账套数据目录是 Express 自带的空白数据目录 `DAT`（路径最后一段，不分大小写），不看显示名称（名称客户可改、语言不同）。判据只在一处：`pearnly_erp_portal/masters/cc_projects.py::_blank_template`，测试 `tests/test_cc_conversation_ledger_erp.py::test_the_express_blank_template_is_never_the_default`。
- 本地与云端同一套：`ledgers()` 按税号 + 分店取全部报上来的账套，不分本地 / 云端。本地同样会出现——11-28 选择器里同一身份下还列着 `\\fileserver\d$\ACCOUNT\70EXP\DAT`、`…\69EXP\DAT`（都是「Z.ข้อมูลเปล่า…」），所以本地 Express 也受益。

### 新入口「移除」（云端 Express 行「···」）
- 用途：删掉这台电脑上的云端连接（连接记录、远程桌面密码 `TERMSRV/<主机>` 与 `Pearnly CC/cloud/<主机>`、实操登录 `Pearnly CC/cloud-express/<主机>/default`）；连着时先同「断开」一样请云端删安装、等回执。
- 实现入口：页面 `frontend/assistant/src/components/cloud-express.tsx`（`forget` → `api.call("cloud_forget")`）；桥 `tomo_bridge._cloud_settings`（`cloud_forget` 与 `cloud_disconnect` 同一流程，多删凭据）；`settings_link.disconnect(forget_operator=…)` → `LinkStore.remove`。
- 出不出：桥 `cloud_status` 回 `forget: true` 才出（同其它云端能力不进 FEATURES）；旧桌面版不出。
- 点击去向：留在连接页，行回到未连接（没有连接记录时是「连接」按钮）。
- 所需配置 / 角色：无（本机）。文案复用已有 `st.remove`（四语已有：移除 / Remove / นำออก / 削除），没有新增文案。
- 验证：`tests/test_cc_cloud_express.py`、`tests/test_companion_contract.py`、companion `test_cloud_host.py` / `test_cloud_settings_link.py`；真机待第二次夜间（断开后重新登录免输、重启不自动连、移除后三条凭据都没了）。产品地图 erp / cowork 两行已补说明。
