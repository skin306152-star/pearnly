# CC 桌面版（Windows，Express）

装在客户做账的 Windows 电脑上：从 Pearnly 领取待录入的单据，直接写进 Express 账套（DBF），再回报结果。
2026-09-27 起源码在本仓（原 `pearnly-companion` 仓库 v1.1.77 搬入，停用旧仓库）。服务端那一侧在
`apps/pearnly_cowork/pearnly_cowork/companion/`，两边的约定由 `tests/test_companion_contract.py` 守住。

桌面通知走 Windows 应用通知（`src/companion/toast.py`，windows-toasts，锁屏时弹的也留在通知中心），发不出来时退回托盘气泡。

## 连接方式

- 网页「连接 Express」弹窗给出**配对码** `exp_<连接>_<密钥>@<租户域名>`，点「复制」整段粘进小助手。
  小助手把 `@` 后面当作服务器（`https://<租户域名>`），前半段当密钥（`pairing.split_pairing_code`）；不写死任何域名。
- 机器接口是新平台原生地址（`queue_client.py`）：`/api/method/pearnly_cowork.companion.heartbeat.report`、
  `…queue_api.lease`、`…queue_api.acknowledge`，`Authorization: Bearer exp_…`，回应取 `message` 那一层。
  站点 ERP 推送总闸 `pearnly_cowork_erp_push_enabled` 没开时服务端拒绝（403）。
- 按需请求（1.5.0 起，`request_worker.py`）：另一条守护线程每 15 秒问一次 `…request_api.next_request`，领到就做、
  分块交回 `…request_api.complete_request`。现有种类：列 / 取已授权文件夹里的文件（`folder_fetch.py`，只认本机加过的
  根目录，挡 `..`、绝对路径与链接跳出，类型同对话附件、一份最多 30 MB）；只读读账套的一个月（`express_read.py`，只认
  推送同一组授权的账套，要读的表先复制到本机临时目录再读，不碰「等账套」，读完即删副本）。与推送线、看门狗无关。加一种请求的做法见 `request_worker.py` 文件头与服务端 `request_contracts.py`。
- 按账套读主档（心跳里的 `master_refresh`，`projection_refresh.collect_account`）：云端请读哪份就读哪份，可以不是本机
  当前选的账套（一台电脑管着很多家客户；1.3.0 起读完整目录，云端按需自动请读的门槛见服务端
  `connections/refresh.py` 的 `ANY_ACCOUNT_SET_MIN_VERSION`）。只读、不改所选账套，能读哪些与推送写入同一条规则；
  1.5.1 起读完的结果只在内存里留最近几份，也不推迟所选账套每 30 分钟的例行上报。
- 1.6.0（2026-10-15）：
  - 按需请求新增只读 `account_history`（`account_history.py`）：读一份账套近 12 个月进项单据的「供应商 → 常用科目」，
    不拿写者锁，结果属于「连接 + 账套」（服务端 `SHARED_KINDS`）；
  - 公司主档的单据文件夹（ADR-020）：对话页经桥 `pick_folder` 弹 Windows 原生选择框（不接收网页给的路径、核对已配对站点、
    拒绝整盘 / 系统目录），根目录记来源 `origin=company`，一小时没人用（服务端 `roots_in_use`）才自动收回，旧授权不收；
    新加根目录后 `roots_only` 快速同步；根目录上限跟服务端 `max_roots`（200，旧服务端按 20）；
  - 自动更新被 Windows 应用控制 / 智能应用控制拦下时识别（`update_block.py`），托盘与设置里给「下载新版」，心跳上报，
    同一版本不反复重试。
- 1.7.0（多台电脑，ADR-021 第 5 批；**服务端第 1–4、6 批上线后才发**，旧服务端上照旧能用）：
  - 设备密钥（`device_link.py`）：第一次运行生成 32 字节随机数，和配对密钥一起用当前 Windows 用户的 DPAPI 加密存在
    `companion.json` 的 `device` 里，同一份密文另存 `HKCU\Software\Pearnly\CC Desktop`（`device_key_backup.py`：降级再升级时恢复；
    解不开或系统标识 `MachineGuid` 不是这台的才换新的）；对话页经桥（`state().device`）拿到它，每次打开带它登记（对话页一侧在第 6 批）；
  - 认电脑：每个成员（Express 与 MR.ERP 只读成员）启动后各发一次 `device_api.hello`（设备密钥、电脑名、`host_names`），
    之后变了再发、没变也每 12 小时再发；回 `superseded` / `claimed_elsewhere` 的密钥本机停用、等对话页重新登记（沿用原密钥重配不解除）；「断开连接」先 `device_api.leave`
    吊销本机这把再删成员。两条方法回 401 / 403 / 404 一律当旧服务端，**从不当成配对失效**；
  - 去掉 1.4.2 起的「常驻模式」（ADR-021 第 56 条，用户 10-16）：CC 桌面版默认就在后台运行（关窗口进托盘、开机自启），
    不再防睡眠、不再开机锁屏；电脑睡了就算离线，单子排队等它醒。旧配置里的 `always_on` 读时忽略、写回时丢掉；询问框只看服务端的 `attended`；
  - 心跳与配对上报的每行账套加 `uid`（目录的卷序列号 + 64 位文件号，`GetFileInformationByHandle`，只用 `FILE_READ_ATTRIBUTES`
    打开，不用 `FileIdInfo`）与 `share`（映射盘经 `WNetGetConnectionW`，本机共享经 `LanmanServer\Shares` 换成
    `\\本机名\共享名\…`），见 `device_facts.py`；`host_names` 只报有默认网关的网卡地址（不报 WSL / Hyper-V / VPN）；
    文件夹规则的网络路径用同一套换算（以前只认持久映射）；
  - 领取请求每次带 `index_pending`（主档索引还没重建的账套文件号）；任务的 `attended=false`（替别人写）不弹询问框；
    有人值守时探的是**这张单的**账套，被占用先按「等账套」放掉租约再弹框（这台写过的单不探、不放回；「终止」按单号记）；写前用本机文件号核 `account_uid`，对不上或这台
    找不到这份账套 → 按「等账套」回报 `[ERR_ACCOUNT_UID_MISMATCH]`（单子交给别的电脑），并整套重扫一次账套；
  - 没有 Express 的电脑也能加入事务所的 Express 池（当只读文件夹的成员，永远领不到写账），之后找到 Express 用原密钥按 Express
    重新配对。
  - 只有真机能验的：真的 DPAPI、`GetFileInformationByHandle` / `WNetGetConnectionW` / 共享表 / 网卡表在 NAS 与服务器共享上的结果、
    两台电脑同一份账套的分派与写前核对，见 `docs/work/2026-10-15-multi-desktop-build.md` 第 7 批。
- 1.8.0（数字会计员第 3 期，`docs/work/2026-10-16-da-phase3-design.md`；**与服务端同批发**，服务端在它之前上线时所有自动推只出提议）：
  - 写完顺手核对（`write_verify.py`，第 6 条）：进项 / 销项写完、原有读回通过之后，拿实际要写的载荷比本次写入行的对方码、各行
    科目、日期、单别、参考号、税记录的税号与分店，另读实际挂到的对方档（APMAS / ARMAS）的税号与分店；POS 转账凭证另比日期。
    只报不回滚，回执 `meta.verify {state: ok|mismatch, fields, party}`，随 `task_journal` 存下，重发时照样带回；
  - `account_history` 结果版本 2：汇总排除 Pearnly 写的单（APTRN `USERID` / GLJNL `CREBY` = `PEARNLY`），CC 自己推的单
    不再攒成「已定」。
- 1.9.0（数字会计员第 3 期，用户 10-17；**先部署收 v3 的服务端再发**，服务端收 v1–v3，旧服务端只收 v1 / v2、会把 v3 的结果当
  不合规记失败，没有历史建议）：
  - `account_history` 结果版本 3：每家供应商另交 `posting`（近 12 个月、会计亲手录的单里走库存 / 记费用各几张、最后一张的单据
    日期）：STCRD 按 DOCNUM 挂单（PEOPLE 有值须是这家供应商），有一行库存品（STMAS `STKTYP=0`）或有库存影响的记走库存；
    STCRD / STMAS 读不到、或 STCRD 在 300 秒内扫不完时整份不交 `posting`（`diagnostics.posting` 记原因，`stcrd_seconds` 记耗时）。
    服务端拿它给按公司选入账方式的商品单定档（与这家供应商的历史一致 A、相反交人、没有照旧 B）。

## 本机测试

```bash
cd companion
python -m pip install --no-deps -r packaging/requirements-build.txt   # 与 CI 同一份精确依赖（Windows 3.11）
QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider tests
```

在 macOS / Linux 上也能跑，Windows 专属的路径与文件锁测试会自动跳过；CI（`Companion checks`）在 Windows 上跑全量。

## 发布（`.github/workflows/companion-release.yml`）

1. 改 `src/companion/version.py` 的 `VERSION`，照主线规则合入 `main`。
2. 先手动运行一次测试发布：
   `gh workflow run companion-release.yml --ref main -f target=staging`（只写 `staging/`，用户看不到）。
3. 正式发布：推标签 `companion-v<VERSION>`（或手动运行选 `prod`）。

流水线：Windows 上跑全量测试 → PyInstaller + Inno Setup 打包（`packaging/release.ps1 -BuildOnly`）→
Ubuntu 把安装包经受限 SSH 交给服务器上的 `infrastructure/production/receive-companion.py` → 从公网下载回来比对 sha256。

安装包地址（全平台一个）：

| 用途 | 地址 |
| --- | --- |
| 网页「下载 CC 桌面版」 | `https://pearnly.com/downloads/companion/CC-Setup.exe`（始终是最新正式版；改名前的 `PearnlyCompanion-Setup.exe` 同时保留同一份） |
| 各版本（不可覆盖） | `https://pearnly.com/downloads/companion/releases/<版本>/<提交>/CC-Setup.exe` |
| 自动更新 | `https://pearnly.com/downloads/companion/latest.json`：服务器读本机这份、随心跳回复带给小助手（`pearnly_cowork/companion/release.py`；不定时查，用户 2026-10-09），只读文件夹的成员没有心跳、随文件夹同步回复拿到（1.4.4 起）；托盘「检查更新」时小助手自己查一次。旧版查的 `/static/companion/latest.json` 指向同一份 |

## 文件夹自动录入：已知限制（1.4.4）

- **只读文件夹的成员要手动更新一次。** MR.ERP 的小助手成员（`kind=folder`）在 1.4.3 及更早没有心跳，也不读文件夹同步
  回复里的新版信息，收不到自动更新；1.4.4 起才从文件夹同步拿新版。上线 1.4.4 时这类电脑要手动装一次，之后自动更新。
  在这之前，它读的新任务（云端 `existing=1`）仍按旧版给已有文件拍底、不报文件夹计数（网页不显示「现有 N 份」）；
  升到 1.4.4 后旧底丢掉，下一个时刻会处理建任务前就在的文件——升级后的第一次同步就把待处理数报给任务列表与提醒。
- **一轮只在开头同步一次。** 一轮里逐份上传很久（新任务首轮、开机补跑几千份）时，这期间的对话提醒、新版提示、
  只读成员的在线状态与待处理数，要等这一轮跑完后的下一次同步；一轮超过 2 小时，网页会显示这台电脑好久没同步。
  开着 CC 窗口的人照常实时收到提醒。运行中途被叫停（自动更新、退出）不算处理过，下次启动同一时刻接着跑。
- **台账清理要云端带全部任务名。** 已删除、换到别的电脑读的任务，台账行在云端同步回复带 `rule_names`
  （这台电脑读的全部任务名，含停用的）且超过 120 天没再出现时才删；回复没带时不删（停用很久再启用的任务靠这些行
  认出处理过的文件）。

## 服务器一次性准备（已做则跳过）

- nginx：`infrastructure/production/landing.conf` 装为 `/etc/nginx/conf.d/pearnly-landing.conf`，`sh reload-nginx.sh`。
- 发布密钥：生成一把专用 ed25519 密钥，公钥以
  `restrict,command="/opt/pearnly/deploy/infrastructure/production/receive-companion.py" ssh-ed25519 … pearnly-companion-publisher`
  加进 root 的 `authorized_keys`；私钥存为仓库 Actions 密钥 `COMPANION_PUBLISH_SSH_KEY`（`PRODUCTION_KNOWN_HOSTS` 与镜像发布共用）。
  接收脚本随生产镜像的部署目录同步（`check-deploy-image` 要求同源）。

安装包没有代码签名，Windows 下载时可能提示「未知发布者」；要去掉需另购代码签名证书（用户 2026-10-08 定不买）。
开着「智能应用控制」等应用控制的电脑会拦下后台静默安装（2026-10-13 录制笔记本）。CC 桌面版不绕开这种拦截，只让人看得见、
有出路（`update_block.py`）：启动安装包时系统按策略拒绝（4551 / 577 / 1260 等错误码）记成「被拦」，托盘菜单与设置页「版本」
一行显示一句 +「下载新版」（系统浏览器打开上表的固定安装包地址，手动安装），同一版本只弹一次通知；同一版本隔 1、2、4 天
再试，之后每 7 天，重开 CC 也照这个节奏（记在 `%APPDATA%\Pearnly\update_state.json`）；心跳与文件夹同步带
`update_blocked`（版本 + UTC 时刻），网页设置「连接」那台电脑下面多一句。
