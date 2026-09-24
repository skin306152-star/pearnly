# Windows 验证环境（testenv）

## 目标与边界

用户要求：把隔离局域网上那台 Windows 机器搭成**与线上一致的验证环境**，让 Mac 改完能快速在真实环境验证，
通过后再部署；不占用正式服务器资源。

边界：这台机器不接正式账本、不占用正式域名、不产生付费 OCR 调用、不写正式数据；
正式服务器的容量、部署与切换不在本任务范围。是否上线由用户在验证通过后另行决定。

## 基线与归属

- 工作树：`/Users/skin/.codex/worktrees/windows-testenv/pearnly`，分支 `codex/windows-testenv`。
- 基线：`main` = `57039ca`（开工时工作树干净；其它 9 个工作树未改动）。
- 正式环境事实来源：`infrastructure/production/target.json`（SSD Nodes `107.155.65.74`，
  Ubuntu 24.04 / amd64）与 `docs/work/2026-09-24-unified-release-onboarding.md` 记录的当前镜像
  `ghcr.io/skin306152-star/pearnly/frappe@sha256:d60900539548cdd9e3f67a41b3ddcf7bed8e73455de5bb144c999fad5f00393e`。
- 注意：`~/.ssh/config` 的 `pearnly-prod` 别名指向已退役的 Contabo 地址 `66.42.49.213`，
  主机密钥已变化，本任务未使用该别名。

## 设备侧事实（实测）

- `DESKTOP-MN2BRKR`，Windows 11（10.0.26200），x86_64，15.8 GB 内存；清理后 C 盘可用 175.75 GB。
- WSL2 已启用，安装 `Ubuntu-24.04.5 LTS`；`/etc/wsl.conf` 默认启用 systemd。
- WSL 内核侧安装与正式机同源的 docker ce（`bootstrap-host.sh` 同一 GPG 校验与 noble 源）：
  docker 29.8.1、compose v5.5.1。
- `.wslconfig` 已设 `memory=11GB / processors=6 / swap=2GB`，实测 WSL 可见 10 GB / 2 GB swap。
- 私有镜像需要 `read:packages` 凭据：当前 Mac 的 gh 令牌不含该 scope，`docker pull` 返回
  `unauthorized`；拉取通道改用 CI 同一个强制命令脚本 `infrastructure/production/receive-image.py`，
  凭据只经 stdin 与临时 `DOCKER_CONFIG`，测试机不留长期凭据（**待用户授予 scope 后验证**）。

## 本批产出

- [`infrastructure/testenv/`](../../infrastructure/testenv/README.md)：栈 = `production/compose.yml` 原样
  + `override.yml`（只换卷名、挂测试脚本），环境文件 `env.example`，宿主准备 `wsl-setup.sh`，
  设备侧入口 `run.sh`，站点初始化 `site-init.py`。
- [`scripts/testenv.sh`](../../scripts/testenv.sh)：Mac 侧驱动（sync/env/pull/base/up/sites/logs/urls/down/reset）。

## 已验证

- `compose config` 在测试机上解析通过（栈定义与正式同源）。
- 基础层实测启动成功且全部 healthy：`mariadb:11.8@sha256:79d59758…`、
  `redis:6.2-alpine@sha256:ad151300…`（与正式 compose 同一 digest），卷 `pearnly-testenv_*` 独立建立。
- 私有镜像拉取**未通过**（缺 scope），仅机制联通到 `docker pull` 一步。

## 未完成 / 下一步

1. 用户授予 `read:packages` 后拉取正式 digest，起 `keycloak` + `billing-web` + 三个产品组。
2. 建测试站点（`sites dms|erp|cowork`）并 migrate，读回版本与健康。
3. 隧道（供 LINE 测试租户回调与手机 HTTPS）与测试租户频道配置：需要用户选定提供方与域名。
4. 一键验收脚本 `scripts/stage-verify-*.py` 按验证矩阵落地。

## 证据分层

实现：本批文件。测试：`compose config`、基础层 healthy 实测。部署：未执行。
外部业务结果：未产生。用户验收：未确认。
