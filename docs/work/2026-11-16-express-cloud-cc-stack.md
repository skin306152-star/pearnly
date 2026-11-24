# 云端 Express：CC 合流分支记录（2026-11-16）

- 分支 `pearnly/express-cloud-cc`，基于 main `4879255e9`。按顺序含：步 B 统一联网客户端（`net.client`）、文件隧道、`--cloud` 云端模式、云端精简包、桥侧连接（连接窗口、非脚本设置、宿主、设置页一行）、代录凭据加密交接（cryptography 46.0.3）、设备密钥一次性配对、中转改成有请求才触发、云端侧接线与安装脚本。
- 不进 1.12.22；进 1.12.23 或更后，由发版时定。合入前与 CC 桌面版 1.12.23 窗口约定：它先合（`queue_client.py`、`tests/test_companion_contract.py`），本分支后合、负责变基。

## 检查（本机）
- companion 全套：2306 通过、63 跳过（11-16 ATL 宿主改法后；改前 2274 通过）；`tests/test_companion_contract.py` 118 通过；`check-architecture`、`check-impact`、`check-copy`、`check-product-map` 通过。
- Windows 真机：**没测**。11-16 测试机1 预检（从源码跑，临时目录）：远程桌面控件能建出、高级设置第 9 版各属性可设可读；**阻塞**：PySide6 的 `QAxWidget` 不提供 `queryInterface`，拿不到控件接口，非脚本两项（只映射隧道盘、网络级身份验证）设不了，窗口按设计拒绝连接。
- 11-16 改法（已离线实现，待 Windows 预检）：连接窗口改用系统 ATL 宿主 + comtypes（`cloud_link/ax_host.py`）。atl.dll（系统目录全路径）`AtlAxWinInit`，在 CC 可见窗口下建 `AtlAxWin` 子窗口、按 CLSID 建 `MsRdpClient10NotSafeForScripting`（Learn 上 `IMsRdpClientNonScriptable3` 的支持列表不含 MsTscAx 系列，所以不再用 `MsTscAx.MsTscAx`），`AtlAxGetControl` 取 IUnknown；属性与方法经 comtypes IDispatch，事件经连接点接 `IMsTscAxEvents` 六个；非脚本两项仍由 `nonscriptable.apply`（ctypes 按 vtable）做，新增每项读回核对，读回不符也不连。QAxWidget 路径已删。comtypes 1.4.17（2026-09-21 发布）钉进 `requirements-build.txt`，主包带上、云端精简包仍排除；不连云端时不导入 comtypes / ax_host / window / nonscriptable（`tests/test_cloud_lazy_import.py` 子进程守住）。接口 IID / 序号 / dispid 与 Learn、类型库 IDL 逐项核对一致（核对表在云端 Express 代录凭据交接做法页）。
- `check-impact` 列出的实测入口全部「没测到（需 Windows）」：步 B 的 `cc.desktop.requests`、`cc.desktop.places`、`cc.desktop.operator`、`cc.mrerp-login`、`cc.desktop.bridge`；桥侧的 `cc.desktop.cloud-express`；云端侧见 `2026-11-16-express-cloud-install-command.md`。原因：同一 Windows 用户下第二份 CC 会抢单实例锁，下次实验栈时换 Windows 用户测。

## 公开仓库扫描（推送前）
- 11-16 仓库改为公开。推送前用本机扫描脚本（只扫相对 main 新增的行，规则含内网 IP、私钥、令牌 / 密码赋值、.rdp 原文行、Bearer 令牌，另加不入库的本机字典：云端主机地址、账号名、主机名、目录名、内部机器名）。
- 本分支首次扫描命中 186 处（测试夹具与注释里的云端主机、账号、域、主机名、端口），已全部换成占位（`198.51.100.7`、`CLOUDUSER-01`、`example`、`CLOUD-HOST` 等）；测试里故意写的假密码 / 假令牌逐行标注 `scan: fake`；测试里的办公室服务器名换成通用名。
- 复扫结果：**拦截 0 · 提示 0**；提交说明命中 0。
- 已推送过的旧分支处理见主会话记录（三条远端分支已删、运行记录已删、清除请求草稿在本机）。

## 11-23 变基到 main（`pearnly/express-cloud-cc-rebased`）
- 基线 origin/main `b77bbb488`（含 CC 桌面版 1.12.23）；29 个提交逐个变基，原分支 `pearnly/express-cloud-cc`（`ccf540cc6`）保留。
- 冲突 6 个文件，以 main 已定口径为准、本分支只补自己的新增：
  - `tests/test_companion_contract.py`：main 新增的 `LedgerChanges`、`BookQuestions` 与本分支的 `OneNetworkExit` 三类都保留（各测各的）。
  - `companion/src/companion/tomo_bridge.py`：构造参数保留 main 的 `on_attention`（1.12.23 ⑨），后面接本分支的 `cloud_links` / `cloud_new` / `session_rdp` / `cloud_session` / `ask_login`。
  - `companion/src/companion/express_operator/session.py`：保留 main 的 `uid`，加本分支的 `ensure_login`。
  - `scripts/impact/shared-parts.toml`（4 次）：取 main 一行，再补本提交相对它自己父提交新增 / 改动的项；每次核对「解后相对 main 的变化」与「原提交相对父提交的变化」逐共用件逐字段相同。
  - `tests/flows/not_flows.toml`、`tests/test_cc_idle_motion.py`：两边各加的条目都保留。
- 逐提交公开扫描：变基后中间 17 个提交里有 10 个仍带云端主机、账号等原值（后面两个提交才换成占位 / 标 `scan: fake`；原分支同样如此）。按那两个提交的逐行替换，把占位提前到引入那一行的提交里（只换命中扫描规则的行），最终树与改写前逐字节相同。改写后 29 个提交逐个「拦截 0 · 提示 0」，提交说明命中 0。
- 本机检查（HEAD 树）：全闸门 GATE EXIT 0；生产镜像候选离线 113 组 LANE EXIT 0；companion 全套 2409 通过、63 跳过（Mac，Windows 专属用例跳过）；契约 + 云端设置行 + 空闲动效 170 通过；`check-impact` 登记表完整，离线入口 `cc.operator.page`、`cc.desktop.operator`、`cc.desktop.operator.mrerp` 的离线用例通过，其余入口仍是「没测到（需 Windows / 用户到场）」。
- 不合入 main：本分支改 `companion-release.yml`（云端精简包与 `latest.json` 的 `cloud` 项），排在 CC 桌面版 1.12.24 发出之后，由主会话定时机；合入后第一次发版要回读 `cloud` 项与公网 sha256。
- 11-23 主会话定：同线两条旧分支也叠到本分支。`pearnly/express-cloud-source-mark`（服务端收心跳 `link {kind: cloud, via, host}`、两个产品存到电脑行上；CC 一侧 `queue_client.py` 已在发这个键，没有它服务端不认）无冲突叠上；`pearnly/express-cloud-notes`（评估、实测清单、实测记录、施工短版与只读探测脚本，`cloud_link` 代码注释引用这几份评估）无冲突叠上，逐提交公开扫描拦截 0。
- 11-23 与 `pearnly/mrerp-connect-inline`（随 CC 1.12.24）的接缝：那条分支把托盘里所有要人输账号密码的原生小窗改成经 CC 窗口进程弹
  （以 CC 窗口为父、模态、置前，`window_link.WindowHost.login`，用户 11-23「弹窗在 CC 后面看不到」），桥的构造参数叫 `ask_login`，签名是
  `(语言, 标题词条, 预填用户名)`；本分支桥里已有同名参数 `ask_login`（云端账号密码窗 `_ask_cloud_login`，签名 `(语言, 预填用户名)`），
  合入时会撞在同一个构造参数上。按类收成一套：云端账号密码窗也走那条（标题词条用云端的，`_ask_cloud_login` 改成经注入的
  `ask_login` 由窗口进程弹），删掉本分支自己的同名参数，守护测试 `companion/tests/test_native_dialogs_front.py` 会拦住托盘里直接弹的小窗。

## 11-24 变基到 main `d8510e61a`（CC 桌面版 1.12.24 已发出）
- 35 个提交逐个变基到 `d8510e61a`，冲突只在 `scripts/impact/shared-parts.toml`（各共用件的 `not_entries` 取 main 一行、补本提交新增项）、
  `companion/src/companion/tomo_bridge.py`（构造参数）与 `frontend/assistant/src/i18n.ts`（设置页词条：main 已删 `mrerpAuthorize`、改成
  `username` / `password`，本分支只补 `cloudExpress` / `address` / `account`）。
- 接缝按上一节收成一套：桥只有 main 的 `ask_login(语言, 标题词条, 预填用户名)`，托盘里由 `WindowHost.login` 在 CC 窗口进程弹。云端账号窗
  用标题 `cloud_login_title`；云端代录的 Express 登录（`cloud_operator_authorize`）原来另有一个 `ask_express_login` 参数、在托盘里直接弹
  `login_dialog.ask`，同样改走 `ask_login`（标题 `op_login_title`，同本地代录「授权登录」）。删掉 `_ask_cloud_login`、`_ask_express_login`
  与两个同名构造参数；云端测试注入三参数的 `ask_login`；`companion/tests/test_native_dialogs_front.py` 通过（桥里直接弹小窗的只剩单进程兜底）。
- 服务端 ↔ CC 的两条约定补进 `tests/test_companion_contract.py` 的 `CloudExpressWire`：云端 CC 心跳的 `link` 正是站点
  `link_contracts.stored` 收的形状、本地 CC 不带这个键（电脑行不动）；`receive-companion.py` 写进 `latest.json` 的 `cloud` 项正是桥
  `cloud_package.parse_entry` 读的形状，没有这一项桥不下载。
- 对没用云端的现有电脑：心跳、`state()`、联网客户端关键字都与 main 相同（契约 `OneNetworkExit`、`test_cloud_settings_link` 的逐字节比较、
  `test_cloud_lazy_import` 子进程守住启动 / 托盘 / 空闲 / 对话页不导入 comtypes 与云端窗口模块）；CC 启动不自动连云端。可见的变化只有一处：
  1.12.25 起「设置 → 连接」多一行「云端 Express（未连接）」，不点「连接」就什么都不做。服务端两个电脑表各加三个隐藏只读字段
  （`link_kind` / `link_via` / `link_host`，原生迁移），本地电脑永远为空。安装包多带 comtypes（只在连云端时导入）。
- 11-24 测试机1 ATL 宿主只读预检（从 af5e31c9a 源码 + 测试机自带 Python 3.11 / PySide6，comtypes 1.4.17 放临时目录不安装；APPDATA 指临时目录，没碰 CC 与 CC 档案；skin3 一次性计划任务）：
  建出 `MsRdpClient10NotSafeForScripting`（`AtlAxWin` 子窗口 + `AtlAxGetControl`），`AdvancedSettings9` 可用；`apply_settings` 全部写入（文档用地址 198.51.100.7、假密码，**没有调用连接**，`Connected` 读回 0）；
  `nonscriptable.apply` 开 CredSSP 并只映射临时 `subst` 的 Y:，`read_back` 读回 CredSSP 开、盘 C / S 未映射、Y 映射。`RedirectDrives` 读回为 True（单个盘开了映射，控件总开关随之显示开；逐盘读回只有隧道盘）。
  关闭后控件销毁；临时目录、计划任务、subst 收尾回读 0。11-16 的 QAxWidget 阻塞已解。真连云端仍待用户到场。
- 发版口径（11-24 主会话）：合入 main 不等于发 1.12.25。先从 main 打测试版装测试机1，用户到场把云端首测走通，再发正式版。
