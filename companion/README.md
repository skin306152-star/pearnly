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
| 自动更新 | `https://pearnly.com/downloads/companion/latest.json`：服务器读本机这份、随心跳回复带给小助手（`pearnly_cowork/companion/release.py`；不定时查，用户 2026-10-09）；托盘「检查更新」时小助手自己查一次。旧版查的 `/static/companion/latest.json` 指向同一份 |

## 服务器一次性准备（已做则跳过）

- nginx：`infrastructure/production/landing.conf` 装为 `/etc/nginx/conf.d/pearnly-landing.conf`，`sh reload-nginx.sh`。
- 发布密钥：生成一把专用 ed25519 密钥，公钥以
  `restrict,command="/opt/pearnly/deploy/infrastructure/production/receive-companion.py" ssh-ed25519 … pearnly-companion-publisher`
  加进 root 的 `authorized_keys`；私钥存为仓库 Actions 密钥 `COMPANION_PUBLISH_SSH_KEY`（`PRODUCTION_KNOWN_HOSTS` 与镜像发布共用）。
  接收脚本随生产镜像的部署目录同步（`check-deploy-image` 要求同源）。

安装包没有代码签名，Windows 下载时可能提示「未知发布者」；要去掉需另购代码签名证书。
