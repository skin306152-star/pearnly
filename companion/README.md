# Pearnly 小助手（Windows，Express）

装在客户做账的 Windows 电脑上：从 Pearnly 领取待录入的单据，直接写进 Express 账套（DBF），再回报结果。
2026-09-27 起源码在本仓（原 `pearnly-companion` 仓库 v1.1.77 搬入，停用旧仓库）。服务端那一侧在
`apps/pearnly_cowork/pearnly_cowork/companion/`，两边的约定由 `tests/test_companion_contract.py` 守住。

## 连接方式

- 网页「连接 Express」弹窗给出**配对码** `exp_<连接>_<密钥>@<租户域名>`，点「复制」整段粘进小助手。
  小助手把 `@` 后面当作服务器（`https://<租户域名>`），前半段当密钥（`pairing.split_pairing_code`）；不写死任何域名。
- 机器接口是新平台原生地址（`queue_client.py`）：`/api/method/pearnly_cowork.companion.heartbeat.report`、
  `…queue_api.lease`、`…queue_api.acknowledge`，`Authorization: Bearer exp_…`，回应取 `message` 那一层。
  站点 ERP 推送总闸 `pearnly_cowork_erp_push_enabled` 没开时服务端拒绝（403）。

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

安装包没有代码签名，Windows 下载时可能提示「未知发布者」；要去掉需另购代码签名证书。
