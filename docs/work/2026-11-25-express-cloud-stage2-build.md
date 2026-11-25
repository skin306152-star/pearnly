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
