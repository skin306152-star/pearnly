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
  Ubuntu 24.04 / amd64）。**线上当前镜像于本次开工后只读读回**：
  `active-image.json` 与 dms/erp 两组 = `sha256:a11eb9e36bae…`（源码 revision `dd81a3f`），
  **billing-web 仍是 `sha256:d60900539548…`（旧一版，漂移）**；cowork 组线上未启用。
  文档历史值 `d6090053…` 不作为当前依据。
- 注意：`~/.ssh/config` 的 `pearnly-prod` 别名指向已退役的 Contabo 地址 `66.42.49.213`，
  主机密钥已变化，本任务未使用该别名。
- 那台 Windows 的 sshd 是收紧配置（仅密钥、`AllowUsers macadmin`、`AllowTcpForwarding no`、
  `GatewayPorts no`），本任务没有改动它。

## 设备侧事实（实测）

- `DESKTOP-MN2BRKR`，Windows 11（10.0.26200），x86_64，15.8 GB 内存；清理后 C 盘可用 175.75 GB。
- WSL2 已启用，安装 `Ubuntu-24.04.5 LTS`；`/etc/wsl.conf` 默认启用 systemd。
- WSL 内核侧安装与正式机同源的 docker ce（`bootstrap-host.sh` 同一 GPG 校验与 noble 源）：
  docker 29.8.1、compose v5.5.1。
- `.wslconfig` 已设 `memory=11GB / processors=6 / swap=2GB`，实测 WSL 可见 10 GB / 2 GB swap。
- 私有镜像：用户授予 `read:packages` 后，经 CI 同一个强制命令脚本
  `infrastructure/production/receive-image.py` 拉取成功；凭据只走 stdin 与临时
  `DOCKER_CONFIG`，测试机上不留长期凭据。

## 本批产出

- [`infrastructure/testenv/`](../../infrastructure/testenv/README.md)：栈 = `production/compose.yml` 原样
  + `override.yml`（只换卷名、挂测试脚本），环境文件 `env.example`，宿主准备 `wsl-setup.sh`，
  设备侧入口 `run.sh`，站点初始化 `site-init.py`。
- [`scripts/testenv.sh`](../../scripts/testenv.sh)：Mac 侧驱动（sync/env/pull/base/up/sites/logs/urls/down/reset）。

## 已验证

- `compose config` 在测试机上解析通过（栈定义与正式同源）。
- 整栈实测起来：22 个容器 + keycloak + mariadb + 双 Redis；产品容器与
  `mariadb:11.8@sha256:79d59758…`、`redis:6.2-alpine@sha256:ad151300…`、
  `keycloak:26.7.4@sha256:82a77884…` 均 healthy 或稳定 Up；产品容器镜像 =
  线上同一 digest `sha256:a11eb9e3…`。
- 三个测试站点建立并 migrate 通过（`sites dms erp cowork`）：
  `dms.test.localhost`（frappe + pearnly_core + pearnly_dms）、
  `erp.test.localhost`（+ pearnly_erp_portal）、`cowork.test.localhost`（+ pearnly_cowork），
  Frappe 均为 `16.33.0`。
- 入口冒烟（测试机内部按 Host 头）：三个站点 `/` 与 `/login` 返回 302（跳登录）、
  `/api/method/ping` 返回 200。

## 未完成 / 下一步

1. Mac 侧浏览器通路：那台机器的 sshd 禁了 TCP 转发。要么按需放开本地转发，
   要么靠隧道（下一条）提供 https 入口。**待用户决定，未擅自改 sshd。**
2. 隧道（供 LINE 测试租户回调与手机 HTTPS）：需要用户选定提供方与域名，尚未开始。
3. `billing.internal` 站点尚未建（线上由 `bootstrap-billing.py` 建），因此计费/身份链未接。
4. 测试租户的 LINE 频道凭据与 OIDC/计费接线未配；按需再开。
5. 浏览器真实操作、LINE 真实回调、真机验收均未执行。

## 证据分层

实现：本批文件。测试：`compose config`、整栈 up、三站点建站+migrate、入口 200 冒烟。
部署：未执行（测试机不是正式环境，未动正式服务器）。外部业务结果：未产生。
用户验收：未确认。
