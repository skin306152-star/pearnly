# SSD Nodes 空环境切换

基线：`7415c1e`；工作树 `ssdnodes-migration/pearnly`，分支 `codex/ssdnodes-migration`。

## 用户决策

- 已购买 SSD Nodes；授权配置新服务器并更新所有相关项目配置。
- 用户明确选择 2：新环境重新开户，不迁移旧租户、账户、钱包及业务数据。旧服务器与本地历史不删除，不重放旧任务。
- 三产品共用身份、计费及 OCR 基础能力，各产品权限、数据、原生队列隔离。控制面继续只在本机运行。

## 当前目标

公开配置唯一入口：`infrastructure/production/target.json`。
SSD Nodes Singapore，服务 50948，IPv4 `107.155.65.74`，IPv6 `2602:ff16:5:115d::1`，Ubuntu 24.04.3 LTS，8 vCPU、32095 MiB、473 GiB 文件系统。
SSH 已使用现有 operator 密钥验证；只开放 22/80/443。Docker、Compose、Nginx、Certbot 已安装。

旧 Contabo `194.233.74.40` 的提供商回读为 `suspended=true, reason=Withdrawal`；保留退款流程及全部旧数据。

## 实现边界

- CI 镜像传送、Mac SSH 隧道、开户 runner、服务器域名验证统一读取公开目标配置。
- 本地桥在私有身份与计费服务验证成功后设新默认目标；旧目标标记 retired，保留历史，不自动转移旧开户单。
- 旧目标全部待办（含删除）不发给任一 runner；未配置或目标不匹配的生产 runner 不取任务。
- 凭据不入仓；新服务器生成独立数据库、身份管理与计费密钥。复用已有授权的邮件/OCR 服务凭据不涉及迁移业务数据。

## 实现与检查证据

- 应用提交 `56e249760d58df08ee23f72a06e727f5faa94600` 已合回 main 并推送；投递脚本执行位补充在 `997ce13`，不改变应用镜像。
- 架构检查零违规；DMS 离线 207 项；开户边界 17 项（候选 Frappe 镜像、无网络）；provision-agent 5 项通过。既有检查没有因后续文档修改重复运行。
- [镜像构建 35514615006](https://github.com/skin306152-star/pearnly/actions/runs/35514615006) 和 [Development checks 35514699642](https://github.com/skin306152-star/pearnly/actions/runs/35514699642) 通过。
- amd64 镜像：`ghcr.io/skin306152-star/pearnly/frappe@sha256:f67d0b72e9615f3c5ea182bd989b5f428a738fa0db79e559060283ce1e3df844`。

## 基础设施回读

- Cloudflare `auth.pearnly.com` 与 `*.pearnly.com` A 记录已从旧 IP 改到 `107.155.65.74`，DNS only；公共解析回读正确。apex/www 的旧独立 Cloud Run 网站、work 的独立服务、MX/TXT 保留。
- auth HTTPS 证书签发成功，到期 2026-12-19；Nginx TLS 配置校验通过，Certbot timer 与续期 reload hook 已配置。
- SSH 已核验主机指纹，仅密钥登录；operator、开户、镜像投递、隧道职责分开。数据库和身份管理接口只在容器网络或回环地址暴露。
- 本机控制面原生数据库、配置和公私文件备份已保存到本机私有目录 `~/.config/pearnly-production/backups/before-ssdnodes-20260920/`，未入仓。

## 剩余验收边界

- 待完成：私有身份/计费连通、匿名拒绝、空账本；本机新目标切换与 runner 恢复。
- 不执行真实 LINE 消息、付费 OCR 或第三方建单；新租户需要重新开户后单独验收。此任务不提供负载极限或真机业务通过结论。

## 首次启动问题

- CI 传送发现 `receive-image.py` Git 执行位缺失，旧主机曾依赖手动 chmod；已补齐执行位后重试，仅重跑失败投递。
- MariaDB 官方 entrypoint 首次临时服务器启动超过其 30 秒等待窗口，基础表已建但账户/Keycloak 库未完成；不把后续 healthy 误认为初始化完成。按官方初始化函数核查和恢复，新环境无业务数据。
- 已先备份未完成的系统库，再调用镜像自带 `docker_setup_db`；恢复 root 密码、Keycloak 库/用户、官方 healthcheck 用户后认证回读通过。没有改官方镜像或放宽认证。
- 首次投递在下载完成、解压长时间无输出时 SSH broken pipe；为投递及共享 SSH 入口添加有界保活，避免长任务被中间网络断开，保留主机指纹校验。
- OCR 服务账号文件采用容器应用 UID 1000、0600 权限；宿主机凭据目录仍为 root 0700，应用只读挂载。
