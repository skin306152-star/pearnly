# 需求影响评估：MR.ERP「连接」弹窗里直接输用户名密码；CC 原生窗弹出时置前（2026-11-23）

> 状态：主会话 11-23 审定（第 5 节两点：#2 #3 只置前；第 4 节剩余风险接受），已施工，分支 `pearnly/mrerp-connect-inline`（基于 main d14379be9），随 CC 1.12.24。

## 0. 需求原文与理解

用户 11-23 在测试机1 授权 MR.ERP：「点了后那个弹窗在他妈大后面，不主动切到前面都看不到，傻逼设计吗，第一个弹窗不行吗，非要再设计一个单独的输入框干啥」。
主会话定：① CC 桌面版里「连接 MR.ERP」弹窗直接放用户名 / 密码，点「连接」一步保存 + 授权，不再弹原生「MR.ERP 登录」小窗；浏览器 / 手机照旧不可授权；② 凡 CC 仍要弹的原生窗，弹出时置前并取得焦点，按类修、加测试。

## 1. 根因（为什么在后面）

CC 桌面版是两个进程（`window_link.py`，10-18 轻量化）：托盘进程跑桥（`tomo_bridge`，`gui_tray.py:786` 建桥时 `window=lambda: None`），CC 窗口与对话页在另一个 `--window` 进程。
`login_dialog.ask` 在**托盘进程**里弹**无父窗口**的 QDialog；托盘此刻不是前台进程，Windows 不许它抢前台 → 小窗落在 CC 窗口后面、只闪任务栏。
同类里只有「选文件夹」早就绕开了：托盘经管道请窗口进程弹（`WindowHost.pick`，以 CC 窗口为父、模态），所以它在前面。

## 2. 原生窗全仓清单（谁弹、在哪个进程、现在会不会在后面）

| # | 窗 | 入口 | 进程 | 现状 |
|---|---|---|---|---|
| 1 | MR.ERP 授权（`tomo_bridge._mrerp_authorize` → `login_dialog.ask`） | 设置 → 连接 → MR.ERP「连接」 | 托盘 | **在后面**（用户这次）；按①改成页面内输入，不再弹 |
| 2 | Express 授权登录（`tomo_bridge` → `login_dialog.ask_and_store`） | 设置 → 实操 / 代录「授权登录」 | 托盘 | **同样在后面** |
| 3 | MR.ERP 代录登录（`mrerp_operator.login.ask_and_store`） | 实操页 MR.ERP 第 2 步 | 托盘 | **同样在后面** |
| 4 | 选文件夹（`QFileDialog`） | 添加文件夹 / Express 位置 | 窗口（经 `WindowHost.pick`） | 正常（父窗口 = CC 窗口） |
| 5 | 「已经在运行」提示（`main.py` QMessageBox） | 第二次双击 CC | 新起的进程（用户刚启动，有前台权） | 正常，顺手加置前 |
| 6 | MR.ERP 代录网页窗（`mrerp_operator/web_driver`） | 代录时 | 子进程 | 已 `raise_` + `activateWindow` |
| 7 | 云端 Express 账号密码窗（`cloud_link` 的 `_ask_cloud_login`） | 设置 → 连接 → 云端 Express | 托盘 | 不在 main（在 `pearnly/express-cloud-cc-rebased`），**同样会在后面**；那条分支合入时套同一个修法 |

## 3. 方案

**按类修（②）**：托盘里所有要人输入的原生窗，一律经管道请**窗口进程**弹，以 CC 窗口为父、模态、`raise_` + `activateWindow`（同 #4 选文件夹那条现成路）。新增一个通用请求 `login`（标题键、预填用户名 → `{user, password}` 或取消），`login_dialog.ask` 在托盘进程里改走它；窗口没开时先拉起窗口再弹（同 `open`）。`main.py` 的提示框加 `WindowStaysOnTopHint` + `activateWindow`。
测试：companion 单测 —— 托盘进程不直接建 QDialog（守住全部调用点，包括以后新加的：源码里 QDialog / QMessageBox / QInputDialog / QFileDialog 只许出现在窗口进程模块与登记的例外里）；`WindowHost.login` 走管道、取消回 None、窗口没了回 None；窗口进程那头以 CC 窗口为父、调了 raise / activate。契约测试补管道新请求名。

**页面内输入（①）**：`mrerp-connect.tsx` 弹窗：系统地址 + 用户名 + 密码，一个「连接」按钮：存地址（管理员，服务端判，同现在）→ 要一次性票（同现在）→ 经桥 `mrerp_authorize` 带票 + 用户名 + 密码 → 桥走现有 `mrerp_login.authorize`（先经机器通道交站点、站点收下再存本机凭据管理器）→ 成功关弹窗。浏览器 / 手机：两格和「连接」灰（没有桥），不另加说明。旧版 CC 桌面版（没有新能力）：照旧提示更新。原来单独的「在 CC 桌面版中授权」按钮去掉。

## 4. 安全口径（重点）

- **变化只有一处**：以前密码**从不进页面**（只在 CC 原生小窗）；现在密码在对话页里输入，页面脚本短暂持有，经 QWebChannel 交本机桥。
- **不变的**：服务端收到的东西不变（今天原生小窗输的密码同样经机器通道交给站点：管理员的写进连接的 Password 字段，成员的只存本机）；本机存储不变（凭据管理器同一个目标名）；仍要一次性票（绑人、绑连接、绑这台电脑的设备密钥），票不对桥直接拒；只有 CC 桌面版里有桥，浏览器、手机里页面根本交不出去。
- **新路径上的守护**：页面不经 HTTP 把密码发给服务端（只走桥），`postMethod` 里不出现密码；输入框 `type=password`、`autocomplete="new-password"`；提交后立即清空 state；不进 localStorage、不进错误上报（`reportCaught` 只带错误类型）；桥收下后不回显、不写日志（沿用 `mrerp_login` 现有做法）；本机管道只本用户、带一次性口令（现有）。
- **剩下的风险**：对话页若被注入脚本（XSS），能读到正在输入的密码——以前读不到。对话页是我们自己的站点页面、已有 CSP 与官方组件；这一条请主会话知悉后定。
- 加测试：前端单测断言密码不进 `postMethod` 参数、提交后清空、浏览器里两格禁用；companion 单测断言 `mrerp_authorize` 新形状（票 + 用户名 + 密码）缺一拒绝、密码不进日志；契约测试补桥动作的新字段。

## 5. 要你定的点

1. #2 Express 授权登录、#3 MR.ERP 代录登录：只按②置前（原生窗不变）？还是也照①挪进页面？建议**只置前**：这两处没有「第一个弹窗」，用户这次说的是 MR.ERP 连接那一处；挪进页面会多两处页面持有密码。
2. 第 4 节「剩下的风险」可接受吗（这是①的代价）。

## 6. 其余

- 文案：新增「用户名」「密码」（官方简短，四语）；删「在 CC 桌面版中授权」（不再有这个按钮）。原生小窗的标题、按钮词条留给 #2 #3 用。
- 共用件：`cc-desktop-bridge`、`cc-assistant-settings` 命中，按清单逐入口实测；测试机1 不动（主会话 11-23），真机验收随 1.12.24 测试版。
- 工作量：写代码约 2 小时；单测、契约、前端测试约 1 小时；闸门 + 候选 + 受影响流程约 1 小时机器时间。

## 7. 施工结果

- CC 本机：`login_dialog.ask` 有父窗口时模态，弹出时 `raise_` + `activateWindow`；新增 `local_ask` / `Ask`，`ask_and_store`（Express、MR.ERP
  代录两处）收调用方的 `asker`。桥 `TomoBridge` 收 `ask_login`（托盘建桥注入 `WindowHost.login`），`mrerp_authorize` 收票 + 用户名 + 密码
  （只给一样回 `login_invalid`；旧页面只带票照旧经 `ask_login` 弹窗），能力表加 `mrerp_login_inline`。管道新请求 `login`：托盘交出前台权，
  窗口进程先把 CC 窗口拿到前面、以它为父弹，回用户名与密码，请求里不带密码；`operator_authorize` / `mrerp_authorize` 等人、不设上限。
  「已在运行」提示加置顶与取得焦点。
- 对话页：`mrerp-connect.tsx` 弹窗加用户名、密码两格（`type=password`、`autoComplete="new-password"`），「连接」先存地址再要票、经本机桥授权；
  交出前清空密码 state；管理员成功即关、使用成员在弹窗里多一行本机这次的「已授权」；浏览器里两格灰，旧桌面版提示更新。
  `mrerp-login.tsx` 只留状态行与 `authorizeConnection`（要票只带连接号与设备密钥）。
- 守护与测试：`companion/tests/test_native_dialogs_front.py`（原生小窗只许出现在能到最前面的地方，桥的两处 `ask_and_store` 都传注入的
  `asker`，小窗弹出时 raise / activate / 模态于父窗口，「已在运行」提示置顶）；`test_window_process.py`（管道 `login` 来回、窗口进程以
  CC 窗口为父弹、两个授权方法不设上限）；`test_mrerp_login.py`（页面交来的登录直接交出、不弹窗、日志里没有密码；旧页面兜底弹窗；
  只给一样不收；注入的 asker）；`tests/test_mrerp_login_pages.py` 改成新口径（整页只有这一个密码框、密码不进网络请求 / 存储 / 错误上报 /
  控制台、只经本机桥）；契约测试加能力名与桥参数名两边一致；流程 `cowork.mrerp-login` 改看两格在浏览器里灰。
- ADR-026 第 7 条加 11-23 修订。云端 Express 分支上的账号密码窗（`_ask_cloud_login`）合入时套同一修法（经 `ask_login` / 窗口进程弹），
  已记在云端分支记录。
