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

- `companion/src/companion/always_on.py`（新）：`KeepAwake` 在专用线程上 `SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)`，屏幕照常关、系统不睡，关掉或退出即恢复；`lock_screen()`。
- `gui_tray.py`：常驻模式开着就保持不睡眠；开机自启（`--background`）进来先锁屏。
- `gui_tray_workers.py`：常驻模式不弹账套占用询问框，交给写入时的占用检查按 `waiting_lock` 回报，空出来自动再写。
- `config.py` / `profile_store.py`：全局设置 `always_on`（旧配置文件读作关）。
- `desktop_settings.py` / `tomo_bridge.py`：`set_always_on`（打开时一并打开开机自启、立即保持不睡眠、重启取任务线程），快照带 `always_on`。
- 对话页设置 → 此电脑 → 系统：「常驻模式」开关（旧版桌面端不报这一项就不显示）。
- 小助手版本 1.4.2。

## 验证

- 本地：小助手全部测试 596 通过 / 42 跳过（Qt 界面类在 CI 的 Windows 跑）；新增 `tests/test_always_on.py`、`test_desktop_settings.py` 两条。
- 待做：CI（Companion checks，Windows）通过并出安装包 → 装到办公室测试机 → 打开常驻模式后 `powercfg /requests` 看到 companion 的 SYSTEM 请求、开机自启后锁屏、账套被占用时不弹框且任务显示「等账套」、关掉后请求消失。
- 真机：用户在手机上让 CC 录一张票，测试机锁着屏完成写入。

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

- 工作树：`/Users/skin/pearnly-cc-always-on`（分支 `pearnly/cc-always-on`），未合并、未推送。
