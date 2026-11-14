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

## 5. 上线（2026-11-14 19:53，UTC 12:53:19 reload）

主会话定：本地全闸门 + 候选离线过了就按分支同一份内容先上生产，合入排 11-15 上午第一位。
本地全闸门 246 步全过、候选离线 111 组 `LANE EXIT 0`（分支 566ac953a）。脚本 `~/pearnly-devtools/prod-speed/r2-cutover-final.sh`，主会话审过后一次 SSH，退出码 0：

- 切换前生产 `/etc/nginx/conf.d/pearnly-landing.conf` sha256 `fb7939e6…f821` = main 855178b60 的 `landing.conf`（生产与 main 原本一致）。
- 备份 `/etc/nginx/pearnly-landing.conf.before-r2-20261114T125319Z` → 整份装入本分支的 `landing.conf` → `nginx -t` 通过 → reload。
- 切换后 sha256 `c80bd82e…a893`，与本分支文件 diff 为空。
- 回环验证：`latest.json` 与 `/static/companion/latest.json` 200、内容校验值前后相同；两个固定名与版本固定地址 302、Location 正确；官网首页 200。

**生产先于 main**：差异只有 `landing.conf` 下载段里新增的 13 行（4 行注释 + 两段 302 + 1 个空行）。本分支合入 main 后一致。
合入前：不发 CC 正式版（main 的流水线还不会先传 R2）、不跑 `scripts/publish-website.sh --nginx`（会用 main 旧配置覆盖）——主会话已通知各窗口并写进交接。

## 6. 验证（生效后）

| 从哪里 | 地址 | 结果 |
|---|---|---|
| 甲骨文新加坡 | 固定名 `CC-Setup.exe` | 302 → R2 固定名；跟随后 153,874,063 字节、sha256 `08586456…e4b9`，3.6 秒 |
| 甲骨文新加坡 | 旧名 `PearnlyCompanion-Setup.exe` | 302 → R2 固定名；同上，1.7 秒 |
| 甲骨文新加坡 | `latest.json` 里的版本固定地址（带 `?v=`） | 302 → R2 同路径同查询串；同上，4.4 秒 |
| 甲骨文新加坡 / 办公室 | `latest.json`、`/static/companion/latest.json` | 200，内容 sha256 `cbc44150…0942`，与切换前相同 |
| 办公室（本机） | 固定名 | 302 → R2；整包 63 秒（2.4MB/s），大小与 sha256 一致；Cloudflare 曼谷节点 |
| 办公室（本机） | companion-v1.12.21 更新器原样代码（`check_update` + `download_installer`，httpx 0.28.1） | 查到 1.12.21 的版本固定地址 → 首跳 302 → 整包 91 秒（1.7MB/s），大小与 sha256 和 `latest.json` 一致 |
| 官网 | `/cowork/` 页的下载按钮 `href="/downloads/companion/CC-Setup.exe"` | 就是固定名，302 到 R2 |

没做：测试机2 上已装程序的只读核对（主会话说可不碰；已用同一标签的更新器代码在本机对线上地址跑过）；没有真的装一次更新。
切换前同线路从源站下载：11-13 / 11-14 晚实测每秒几 KB 到几十 KB，整包约 45 分钟以上。

## 7. 近 7 天被请求过的安装包地址（切换时只读统计，次数）

`latest.json` 35、固定名 29（另有小写 `cc-setup.exe` 5，本来就 404）、旧名 4；版本固定地址：1.12.21 ×15、1.12.20 ×7、1.12.19 ×5、1.12.17 ×4、1.12.15 ×2、1.1.77 目录 ×1；staging 9（不跳转）。
非 1.12.21 的版本地址是那几版当时作为最新版被自动更新取的；现在 `latest.json` 只指 1.12.21。这些旧版直链现在会跳到 R2 的 404（主会话定：今晚不为它改规则）。源站本机 51 个版本目录都还在。

## 8. 回退

一条命令：`bash ~/pearnly-devtools/prod-speed/r2-cutover-rollback.sh`（干跑加 `DRY=1`；连生产前照规矩先问主会话）。
它在服务器上跑 `/root/pearnly-r2-302-rollback.sh`：核对备份 sha256 = 切换前 → 复制回去 → `nginx -t` → reload；下载回到源站本机那份。
干跑已在切换那次 SSH 里执行一次并通过；真回退在本机容器演练过（校验值回到 main 那份、固定名回 200）。
没有服务器上的脚本时等价做法：`cp -a /etc/nginx/pearnly-landing.conf.before-r2-20261114T125319Z /etc/nginx/conf.d/pearnly-landing.conf && nginx -t && systemctl reload nginx`。

## 9. 以后发版要注意

- 本分支合入后，正式发布自动先传 R2 并读回，再改 `latest.json`；需要仓库密钥 `R2_ACCOUNT_ID`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`（已加）。
- 合入前若必须发版：先把新包放上 R2 的版本路径与固定名并读回，再发。
- 固定名 4 小时缓存：发版后 4 小时内网页下载可能是上一版。要消除，需在 Cloudflare 把该区域「浏览器缓存 TTL」改为遵循源站标头（要用户操作，未做）。
- R2 清理规则：流水线每次正式发布后只留最近 5 个版本目录（免费额度 10GB，每版约 154MB），更早的直链 404；源站本机全留。

## 10. 收尾状态

- 分支 `pearnly/companion-download-r2` 已推，等 Development checks 与 Companion checks（测试机1，明早跑）；合入由主会话排 11-15 上午第一位。**交接保留**：工作树 `.claude/worktrees/r2-cutover-de26dc`、本地与远端分支，合入后删除。
- 服务器上保留：备份文件与 `/root/pearnly-r2-302-rollback.sh`（合入并稳定后可删）。
