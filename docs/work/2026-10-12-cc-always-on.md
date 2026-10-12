# CC 的常驻电脑（方案 A）· 2026-10-12

## 目标

办公室电脑都关着、人在外面，也能在手机上让 CC 把单据录进公司的 Express。用户 2026-10-12 选方案 A：办公室放一台常开的小主机当「CC 的电脑」，Express 数据文件夹放在它上面做共享盘，小助手常驻在上面；其余电脑可以全部关机。先做小助手的「常驻模式」。

## 现状核对（代码证据）

- 小助手直接写账套 DBF、自己重建 CDX，不打开 Express、不需要有人在（`companion/src/companion/dbf_writer.py`、`cdx_reindex.py`）。
- 录入任务在服务器排队，小助手每 3 秒来取（`queue_client.py`、`multi_profile_worker.py`），CC 推送的工具在小助手离线时照样排队（`apps/pearnly_cowork/pearnly_cowork/assistant/tools.py` 回 `companion_online:false`）。
- MR.ERP 由服务器直接推送，不经小助手。
- 卡点：Express 数据所在电脑必须开着；小助手是登录后启动的托盘程序，电脑睡眠 / 没登录就不跑；账套被占用时弹「重试 / 终止」框，没人点会卡住，3 分钟后看门狗重启当成用户终止（`USER_ABORTED_BUSY`）。

## 方案取舍

不做「以系统身份、不登录就跑」的服务：凭据按 Windows 用户 DPAPI 加密、映射盘符在后台看不见、系统身份访问共享盘常被拒、现有 `--headless` 缺文件夹定时任务 / 更新 / 自愈且有成员缺账套就退出、用户登录后还会出现第二个写入者——每项都要重写，且常驻电脑会和其他电脑跑两套代码。

采用：常驻电脑开机**自动登录一个专用 Windows 账号**（在 Windows 里设一次，密码由用户自己输入），小助手随登录启动，同一套代码；「常驻模式」只补无人值守缺的部分。

## 本次改动（分支 `pearnly/cc-always-on`，基于 `pearnly/cc-claude-ui` 3d7edb61）

- `companion/src/companion/always_on.py`（新）：`KeepAwake` 在专用线程上 `SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED|ES_DISPLAY_REQUIRED)`，关掉或退出即恢复，不改电源设置；`lock_screen()`。锁屏界面因此一直亮着（见下面实测）。
- `gui_tray.py`：常驻模式开着就保持不睡眠；开机自启（`--background`）进来先锁屏。
- `gui_tray_workers.py`：常驻模式不弹账套占用询问框，交给写入时的占用检查按 `waiting_lock` 回报，空出来自动再写。
- `config.py` / `profile_store.py`：全局设置 `always_on`（旧配置文件读作关）。
- `desktop_settings.py` / `tomo_bridge.py`：`set_always_on`（打开时一并打开开机自启、立即保持不睡眠、重启取任务线程），快照带 `always_on`。
- 对话页设置 → 此电脑 → 系统：「常驻模式」开关（旧版桌面端不报这一项就不显示）。
- 小助手版本 1.4.2。

## 现代待机实测（2026-10-12，办公室测试机 lenovo-office-lan，S0 现代待机，插电，交流电关屏 / 睡眠均为「从不」）

用与常驻模式同一个调用的小脚本，以 skin3（Interactive、Limited）计划任务持有请求，Mac 每 20 秒 SSH 探测，读 System 日志 Kernel-Power 506/507：

| 持有 | 动作 | 结果 |
|---|---|---|
| 系统（ES_SYSTEM_REQUIRED） | 广播 SC_MONITORPOWER 关屏 | 16:49:18 进入现代待机（原因 SC_MONITORPOWER / SleepButton），16:53:25 键盘唤醒；期间 SSH 超时 |
| 系统 + 屏幕（+ES_DISPLAY_REQUIRED） | LockWorkStation 锁屏（同常驻模式开机） | 16:54:17 锁屏后到 16:58:34 每次探测在线，日志无 506 |

结论：只保持系统不够（现代待机关屏即待机，锁屏约 1 分钟会关屏）；改为系统 + 屏幕。强制关屏 / 按电源键 / 合盖属人为操作，任何请求都拦不住。测试任务、脚本与请求已清除（`powercfg /requests` 无残留）。

## 验证

- 本地：小助手全部测试 596 通过 / 42 跳过（Qt 界面类在 CI 的 Windows 跑）；新增 `tests/test_always_on.py`、`test_desktop_settings.py` 两条。
- CI：Companion checks（Windows 测试 + 打包）与 Development checks 均通过（9acb268d）。
- 测试机实装（2026-10-12 17:12）：以 skin3 静默安装 1.4.2（安装包 sha256 f55576df…c8ca 与 CI 产物一致），打开常驻模式（companion.json `always_on: true`，原配置备份在 `C:\Users\Public\pearnly-test\companion.json.before-always-on`）后按开机自启方式（`--background`）重启：`powercfg /requests` 的 DISPLAY 与 SYSTEM 均为 companion.exe；日志两条「保持不睡眠」「开机后已锁屏」；LogonUI 于 17:26:54 与 CC 同秒启动（已锁屏）。
- 原待做：CI（Companion checks，Windows）通过并出安装包 → 装到办公室测试机 → 打开常驻模式后 `powercfg /requests` 看到 companion 的 SYSTEM 请求、开机自启后锁屏、账套被占用时不弹框且任务显示「等账套」、关掉后请求消失。
- 真机：用户在手机上让 CC 录一张票，测试机锁着屏完成写入。

## 安装入口按设备区分（同分支，2026-10-12 晚）

用途：在哪台设备打开 CC 网页，就给那台设备的按钮（欢迎页一条可关提示 + 用户菜单一项）；已装、已在桌面版或已装成应用打开时不出现。实现在 `frontend/assistant/src/lib/install.ts`（判断）与 `components/install-prompt.tsx`（按钮、步骤）。

| 设备 | 按钮 | 点了以后 |
|---|---|---|
| Windows（Cowork） | 下载 CC 桌面版 | 下载 `CC-Setup.exe` |
| 安卓、Linux、非 Cowork 的 Windows（Chrome / Edge） | 安装 CC | 浏览器自带安装确认，一步 |
| Mac Chrome / Edge | 添加到程序坞 | 浏览器自带安装确认，一步 |
| Mac Safari 17+ | 添加到程序坞 | 两步：文件 → 添加到程序坞 |
| iPhone Safari 26+ | 添加到主屏幕 | 三步：地址栏左边 ≡ → 共享 → 查看更多 → 添加到主屏幕 |
| iPhone Chrome | 添加到主屏幕 | 三步：地址栏右边分享 → 查看更多 → 添加到主屏幕 |
| 其他 iPhone / iPad 浏览器 | 添加到主屏幕 | 两步：分享 → 添加到主屏幕 |
| LINE 内置浏览器 | 在浏览器中打开 | 带 `openExternalBrowser=1` 跳到默认浏览器 |

实测（用户 iPhone 镜像 + 本机 Mac）：
- iPhone 的 Safari 和 Chrome 都**不能由网页调起「添加到主屏幕」**：网页弹出的系统分享面板（`navigator.share`）展开「查看更多」后也没有这一项，只有浏览器自己的菜单里有，所以 iPhone 改回按浏览器给准确步骤。两种浏览器的步骤已在手机上逐步核对，页面识别正确。
- Mac Safari：显示「添加到程序坞」，Safari「文件」菜单里确有「添加到程序坞…」。Mac Chrome：一点弹出 Chrome「安装应用」；取消后 Chrome 会再给一次，再点仍能弹出。修了一处：安装确认每页只能弹一次，原来弹过后按钮还在但点了没反应，现在用过即收起，浏览器再给时再出现。
- 安卓：内置浏览器模拟安卓 + 模拟安装事件，显示「安装 CC」+ 下载图标，点后调起安装、按钮收起；**真机未测**（待用户安卓手机）。LINE 跳出未实测。
- 顺带：`www/assistant.html` 的注释改成 Jinja 注释，渲染时去掉，查看源代码不再看到中文注释；`csrf_token` 那一串是每个登录会话的防伪造校验码，正常。

## 常驻电脑的一次性设置（到时与用户一起做）

1. 小主机：主板设「来电自动开机」（停电恢复后自己开机）。
2. 建专用 Windows 账号，设自动登录（Windows 自带 netplwiz 或微软 Sysinternals Autologon；密码由用户自己输入）。
3. Express 数据文件夹搬到小主机并共享（下班时间、先完整备份）；会计电脑的 Express 改指向新共享。
4. 小主机装小助手、登录公司账号连接，设置里打开「常驻模式」。
5. Windows 更新的「使用时段」避开白天；更新重启后会自动登录、小助手随之恢复。

## 未做 / 下一步

- 服务器还不知道哪台是常驻电脑：心跳加 `always_on`（要动 `plan_report` 契约与 `tests/test_companion_contract.py`），CC 才能说「常驻电脑离线」而不只是「小助手离线」。
- 硬件采购需用户同意（方案 A 已选，型号待定）。

## 收尾状态

- 2026-10-12 晚随 f518964c 合入 main 并上线（见 [发布记录](2026-10-12-release-f518964c.md)）；工作树 `pearnly-cc-always-on`、`pearnly-cc-claude-ui` 及其本地 / 远端分支在收尾时删除。
- 测试机保留：`C:\Users\Public\pearnly-test\companion.json.before-always-on`（常驻模式前的原配置，等用户决定测试机是否继续常驻）；测试机 CC 1.4.3 常驻模式仍开。
