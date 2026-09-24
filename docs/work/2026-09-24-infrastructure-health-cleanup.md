# 基础设施体检与清理（2026-09-24）

用户要求：检查服务器、数据库、Cloudflare、git 的健康度与状态，找出已不存在/不再依赖的东西，
清理或停掉仍占资源的项；DMS 相关在进行中，不动。

## 开工状态（只读读回）

- 服务器 `107.155.65.74`（SSD Nodes，Ubuntu 24.04 / amd64）：uptime 3 天 17 小时，内存 31 GiB 用 3.7 GiB，
  磁盘 34 GB / 473 GB（8%），负载约 1.0。19 个容器全在跑，无停止/僵尸容器，无失败 systemd 单元。
- 数据库：4 个租户库（各 13–14 MB）与 4 个站点一一对应，无孤儿库；`mysql` 9 MB、`keycloak` 4.5 MB；
  无 MariaDB 之外的历史库。存在 `root@%`（镜像默认，未对外暴露端口，仅容器网络内可达）。
- Cloudflare：`auth` / `*.pearnly.com` 指向服务器；`pearnly.com`（+`www`）仍是指向**已退役 Cloud Run** 的代理记录；
  `work.pearnly.com` 指向已下线机器；存在废弃隧道 `pearnly-erp-dev`（长期 down）与 5 个从未使用的隧道令牌。
- git：本地 `main` 领先远端 7 个提交；远端保留一个已合并分支；10 个工作树中 9 个分支已并入 main，
  `codex/dms-five-minute-snapshots` 未并入（其它窗口在进行，未动）。
- 本机：`~/.colima` 占 64 GB（三个 profile，其中 `pearnly-erp-acceptance` 已停止）。
- 旧 Contabo IP `66.42.49.213` 已**不属于本团队**（主机密钥与本机记录不符、公钥被拒、系统版本不同），
  说明原机器已下线回收；`~/.ssh/config` 的 `pearnly-prod` 别名与 known_hosts 记录属过期配置。

## 已执行

| 项 | 动作 | 结果 |
| --- | --- | --- |
| 主域名 404 | `pearnly.com` / `www` 解析从退役 Cloud Run 改为指向服务器（DNS-only），新增 nginx 站点 + 静态占位页 + Let's Encrypt 证书 | 两个域名均 200，证书 `pearnly.com`+`www` 有效至 2026-12-23；原记录（CNAME → run.app，proxied）已留档可回退 |
| 死记录 | 删除 `work.pearnly.com`（指向已下线机器） | 已删 |
| 废弃隧道 | 删除长期 down 的 `pearnly-erp-dev` | 已删 |
| 服务器旧镜像 | `docker image prune -a --filter until=48h`（只删 48 小时前未使用的镜像） | 镜像 21→9，回收 **16.95 GB**；磁盘占用 34 GB→19 GB |
| 服务器匿名卷 | `docker volume prune`（只删未使用的匿名卷） | 卷 99→21，回收 1.32 MB |
| git | 推送本地 `main` 到远端 | `3cee765..90b5cb3`，`main` 与 `origin/main` 一致 |
| ssh 配置 | `pearnly` / `pearnly-prod` 别名改指真实生产机（107.155.65.74 + operator 密钥），删除过期 known_hosts 记录 | `ssh pearnly` 实测落到生产机；`~/.ssh/config.bak-*`、`known_hosts.old` 保留 |
| 本机 Colima | 两个运行中 profile 的构建缓存与悬空镜像 | 回收约 2.94 GB（构建缓存） |

执行后复核：19 个容器全部仍在运行；`pearnly.com`、`www`、`testdms`、`testerp`、`ris`、`auth` 均正常响应
（站点 302 跳登录属预期）。

## 未处理 / 待用户决定

- **5 个从未使用的 Cloudflare 隧道令牌**：当前令牌无 User API Tokens 权限，需在面板删除。
- `codex/dms-five-minute-snapshots`（未并入 main）与其它窗口的工作树：按用户要求 DMS 相关不动。
- 远端已合并分支 `origin/codex/dms-line-full-flow-fix`：DMS 相关，本次未删。
- 本机已停止的 Colima profile `pearnly-erp-acceptance`（约 8.3 GB 磁盘）：归属其它窗口，未确认前保留。
- 服务器镜像仍保留 4.27 GB 可回收（近 48 小时内的历史版本，作为快速回退目标有意保留）。

## 证据分层

实现：解析、nginx 站点、静态占位页、清理命令。测试：公网访问与容器状态读回（见上）。
部署：本次改的是正式服务器解析与 nginx（已读回生效）。外部业务结果：未产生。
用户验收：未确认。

## 追加（用户确认后执行）

- **删除 5 个从未使用的 Cloudflare 隧道令牌**（面板操作，保留在用的 `pearnly-testenv-switch`
  与无关的 `pearnly-fix` / `Pearnly`）。账号内隧道现在只剩 `pearnly-testenv`（healthy，4 连接）。
- **删除本机已停止的 Colima 配置 `pearnly-erp-acceptance`**，并清掉其遗留的 8.3 GB 孤儿磁盘；
  `~/.colima` 从 64 GB 降到 **54 GB**。运行中的 `pearnly`、`pearnly-stocktake` 两个配置未动。
- **旧 Cloud Run 已确认不存在**：`gcloud run services list --project pearnly --region asia-southeast1`
  返回 0 项，说明退役后没有残留服务在计费。
- **找到未迁移的旧宣传页**：`/Users/skin/Developer/Pearnly/pearnly-app/static/landing/portal.dc.html`
  （28 个文件、12 MB，含 th/en/zh/ja 四语文案、auth 背景图、猫咪插画、tour 场景与样式；
  GitHub 上该目录有提交历史）。当前 `pearnly.com` 先用最简占位页，宣传页待后续重做时以它为基础。
- 执行后复核：`pearnly.com` 200，`testdms` / `testerp` 302（跳登录属预期），测试环境隧道服务 active。
