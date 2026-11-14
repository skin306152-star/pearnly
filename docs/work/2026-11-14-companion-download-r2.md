# CC 桌面版安装包走 R2（2026-11-14 晚）

分支 `pearnly/companion-download-r2`，基线 origin/main 855178b60。主会话任务书（用户 11-14 晚授权）：今晚让线上「下载 CC 桌面版安装包」走 R2，
官网下载与已装版本的自动更新都变快；验证并留一条命令回退。

## 1. 背景与来源

- 安装包约 154MB，原来只从新加坡源站（宿主机 nginx）发，无 CDN、无断点续传；11-12 起办公室到源站的线路晚上大量丢包，下载每秒几 KB 到几十 KB。
- 内容来自「线上访问速度治根」窗口分支 `pearnly/prod-speed-2-5237c8` 的提交 222f7105b（原样摘取为本分支第一个提交）：
  `infrastructure/production/landing.conf` 的两段 302、`.github/workflows/companion-release.yml` 的「先传 R2」步骤、`companion/README.md` 的说明。
  该分支其余内容（bbr 登记、11-13 / 11-14 速度记录）不在本分支，由它自己合入。
- 配置与流水线必须同一批进 main：302 生效后，发布流水线若没有「先传 R2」，新版 `latest.json` 指向的地址会跳到 R2 上不存在的文件，自动更新全部 404。
  **302 在生产生效到本分支合入 main 之间，不得发 CC 正式版。**

## 2. 改动

| 文件 | 内容 |
|---|---|
| `infrastructure/production/landing.conf` | `/downloads/companion/` 段内嵌两条正则：版本固定地址 `releases/<版本>/<40 位提交>/CC-Setup.exe` → 302 到 `media.pearnly.com` 同路径（查询串原样）；固定名 `CC-Setup.exe`、`PearnlyCompanion-Setup.exe` → 302 到 `media.pearnly.com/downloads/companion/CC-Setup.exe`。`latest.json`、`staging/`、`/static/companion/latest.json` 与其它文件不动 |
| `.github/workflows/companion-release.yml` | 正式版先把安装包传 R2（版本路径 + 固定名）、从公网读回比对 sha256，再交 `receive-companion.py` 改 `latest.json`；R2 只留最近 5 版；缺 R2 密钥时停在这一步 |
| `companion/README.md` | 发布流水线与地址表的说明 |

提供方：安装包与 `latest.json` 由生产宿主机 nginx 给（`/etc/nginx/conf.d/pearnly-landing.conf` = 本仓 `landing.conf`，目录 `/var/www/pearnly-downloads/companion/`），不经任何容器。
生效方式：`nginx -t` 通过后 `systemctl reload nginx`；不重建、不碰容器与其它站点配置。源站本机那份安装包照旧写、照旧全留。

## 3. 上线前证据

- 更新器：`companion/src/companion/updater.py` 取 `latest.json` 与下载安装包都是 `httpx.Client(follow_redirects=True)`，不限域名（速度窗口核过旧仓库起的全部版本）。
- R2 现有：`media.pearnly.com/downloads/companion/releases/1.12.21/67f84cffb483660434e71c049009a9e42b0ffe36/CC-Setup.exe` 与固定名，
  大小 153,874,063，与源站 `latest.json`（版本 1.12.21、sha256 `08586456…e4b9`）一致；版本路径回 `immutable`，支持分段请求，经 Cloudflare 曼谷节点。
- 仓库内引用：官网（`website/content.py`）、ERP 公司页、服务端 `cc_desktop/release.py`、更新器的下载页都用固定名；版本固定地址只出现在 `latest.json`。
  改名前的旧版本路径是 `…/PearnlyCompanion-Setup.exe`，不匹配规则，仍由本机给。
- 本机 nginx 容器演练（nginx:1.27-alpine，挂本分支 `landing.conf`）：`nginx -t` 通过；`latest.json` 与 `/static/companion/latest.json` 200；
  固定名两个（带不带查询串）302 到 R2 固定名；版本固定地址（带 / 不带 `?v=`）302 到 R2 同路径且查询串原样；`staging/…/CC-Setup.exe` 200 不跳；
  版本目录下旧名 404、目录 403、不存在文件 404（与改前一致）。

## 4. 已知限制

- R2 上只有最近 5 版（今晚只有 1.12.21）。更早版本的 `CC-Setup.exe` 直链会跳到 R2 的 404；自动更新只取 `latest.json` 里那一版，不受影响。
- 固定名经 Cloudflare 回 `max-age=14400`：发新版后 4 小时内，网页「下载 CC 桌面版」可能拿到上一版（装上后自动更新会补到最新）。自动更新用版本固定地址，不受影响。
- 合入前若有人跑 `scripts/publish-website.sh --nginx`，会用 main 的旧配置覆盖 302（等于回退）。

## 5. 上线、验证与回退

（上线后补：生效时间、生产配置与本分支是否逐字一致、各点验证证据、回退干跑。）
