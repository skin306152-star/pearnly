# SSD Nodes 空环境切换

基线：`7415c1e`；工作树 `ssdnodes-migration/pearnly`，分支 `codex/ssdnodes-migration`。

## 用户决策

- 已购买 SSD Nodes；授权配置新服务器并更新所有相关项目配置。
- 用户明确选择 2：新环境重新开户，不迁移旧租户、账户、钱包及业务数据。旧服务器与本地历史不删除，不重放旧任务。
- 三产品共用身份、计费及 OCR 基础能力，各产品权限、数据、原生队列隔离。控制面继续只在本机运行。

## 当前目标

公开配置唯一入口：`infrastructure/production/target.json`。
SSD Nodes Singapore，服务 50948，IPv4 `107.155.65.74`，IPv6 `2602:ff16:5:115d::1`，Ubuntu 24.04 LTS，8 vCPU、32095 MiB、473 GiB 文件系统。首次更新后回读为 24.04.5 LTS，内核 `6.8.0-139-generic`。
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
- 系统重启后的[投递 35516862807](https://github.com/skin306152-star/pearnly/actions/runs/35516862807) 成功，镜像源码标签仍为 `56e2497`；后续 SSH/邮件导出脚本不改变应用镜像。

## 基础设施回读

- Cloudflare `auth.pearnly.com` 与 `*.pearnly.com` A 记录已从旧 IP 改到 `107.155.65.74`，DNS only；公共解析回读正确。apex/www 的旧独立 Cloud Run 网站、work 的独立服务、MX/TXT 保留。
- auth HTTPS 证书签发成功，到期 2026-12-19；Nginx TLS 配置校验通过，Certbot timer 与续期 reload hook 已配置。
- SSH 已核验主机指纹，仅密钥登录；operator、开户、镜像投递、隧道职责分开。数据库和身份管理接口只在容器网络或回环地址暴露。
- 本机控制面原生数据库、配置和公私文件备份已保存到本机私有目录 `~/.config/pearnly-production/backups/before-ssdnodes-20260920/`，未入仓。
- 统一登录 realm 已配置并回读：用户名/邮箱登录、重置密码、四语言、Pearnly 主题、无自助注册/社交登录；控制面服务账号查询用户/客户端成功。浏览器已显示正式品牌登录页与图片。
- 公网 OIDC discovery 200 且 issuer 正确；admin、master realm、health 均为 404。
- 当前邮件来源是本机原生 Email Account（`smtp-config.py --control`），From/Reply-To 为 `hello@pearnly.com`。新机 SMTP TLS 认证与 NOOP 成功，未发邮件；旧 `.env.local` 仍是历史 Gmail 发件地址，不能再作为新机正式配置来源。
- 新机独立凭据已保存到本机私有备份 `~/.config/pearnly-production/backups/ssdnodes-20260920/initial-secrets.tar.gz`（0600），未入仓。
- 系统安全更新完成，`dpkg --audit` 无输出；重启后 SSH 密钥、Docker/containerd、Nginx、Certbot timer 和基础容器自动恢复，防火墙仍只开放 22/80/443。
- 重启后 OIDC discovery 再次返回 200。新机应用容器使用只读 OCR 凭据成功取得服务账号认证；未执行模型推理，没有据此认定 OCR 业务或承载上限通过。
- 私有计费 Site `billing.internal` 已通过原生安装、迁移并启动；运行镜像标签 `56e2497`，无重启或 OOM。Desk、通用资源 API、匿名计费调用均被拒绝；管理凭据读取成功。Tenant、Wallet、Credit Transaction、Topup Request 实读均为 0。
- 本机控制面已验证私有身份/计费后切换：`production-ssdnodes` 是唯一默认目标，`production-contabo` 的 executor 为 retired；旧目标 API 路由移除。旧部署 `DEP-2026-0248`（`testdms.pearnly.com`）已原生保存为 suspended 并留审计说明，没有删除历史。
- 控制面定向验证通过：目标路由、本地样板 URL 保留、正式 LINE 地址计算、载荷无 realm 管理密钥。旧目标和新目标的可领取任务均为空。正式 LINE 地址验证使用替换后的部署读取，并非真机 LINE 验收。
- 本机仅重启 control 的 web/worker/scheduler 以读取新配置；生产隧道与开户 LaunchAgent 已运行。受限开户 SSH 真实连接新机，并拒绝未支持的操作，没有创建租户。
- 计费原生数据库/配置/公私文件、Keycloak 数据库、服务器配置及凭据已备份至本机私有目录 `~/.config/pearnly-production/backups/ssdnodes-20260920/foundation-20260920.tar.gz`（0600）。两端 SHA-256 一致：`6062148d21cb550b3cf03009e7db9eb6634be7ec06ca251b24199672e5cfe7a4`。这是初始快照，未配置定期异地备份，未执行恢复演练。

## 交付与剩余验收边界

- 新服务器共享基础服务、本机控制连接、CI 投递和开户执行器已配置完成。尚未创建任何新租户，产品运行组将在正式开户时启用。
- 用户随后明确要求删除旧测试租户，已按下节清理其本地关联记录，`testdms` 域名前缀现已释放。新服务器仍是空环境，需要重新开户。
- 不执行真实 LINE 消息、付费 OCR 或第三方建单；新租户需要重新开户后单独验收。此任务不提供负载极限或真机业务通过结论。
- 本任务未改变旧独立网站和 WeKan 的服务归属，也未删除旧服务器数据。

## 首次启动问题

- CI 传送发现 `receive-image.py` Git 执行位缺失，旧主机曾依赖手动 chmod；已补齐执行位后重试，仅重跑失败投递。
- MariaDB 官方 entrypoint 首次临时服务器启动超过其 30 秒等待窗口，基础表已建但账户/Keycloak 库未完成；不把后续 healthy 误认为初始化完成。按官方初始化函数核查和恢复，新环境无业务数据。
- 已先备份未完成的系统库，再调用镜像自带 `docker_setup_db`；恢复 root 密码、Keycloak 库/用户、官方 healthcheck 用户后认证回读通过。没有改官方镜像或放宽认证。
- 首次投递在下载完成、解压长时间无输出时 SSH broken pipe；为投递及共享 SSH 入口添加有界保活，避免长任务被中间网络断开，保留主机指纹校验。
- OCR 服务账号文件采用容器应用 UID 1000、0600 权限；宿主机凭据目录仍为 root 0700，应用只读挂载。
- 后续投递还遇到系统安全更新重启 containerd（14:16:39 UTC），日志对应解压 RPC connection reset。先按官方 SIGTERM 语义等待自动更新完成当前包，再批量收尾系统更新、重启空环境，之后重新投递；没有杀 dpkg 或修改应用镜像来掩盖失败。
- 首次计费 HTTP 检查早于 Gunicorn 监听端口，得到 connection refused；日志确认首次导入完成并监听后再次运行，访问边界和空账本检查通过。没有将容器 started 当作服务 ready。

## 后续授权：删除旧测试租户与版本核对

- 用户明确要求删除旧记录，并追问所有改动是否已送到新服务器。此次授权替代前述保留此测试租户本地记录的决定，不删除旧服务器磁盘，也不执行新服务器租户拆除。
- 现场只有旧 Contabo 租户 `T-2026-0246`（test），发号单 `ISS-2026-0245`，部署 `DEP-2026-0248`。用户点击删除后生成 `PR-2026-0249`，状态 pending；旧目标已 retired，`next_steps` 有意不派发其拆除步骤，因此无法通过常规在线拆除完成。
- 清理前使用 Frappe 原生 `backup --with-files`，快照保存在本机私有目录 `~/.config/pearnly-production/backups/retired-tenant-20260920/`（目录 0700、文件 0600）。
- 在本机维护进程中锁定并核验以上唯一租户、发号单、部署、退休目标和待删除请求，调用既有 `teardown.purge_records` 完成本地原生删除。没有将远程步骤伪报为 done，没有调用身份服务或新计费服务的删除 API。LINE 渠道删除 hook 因目标 retired 不排发配置。
- 已删除：该租户、发号单及产品子表、部署、原开户请求 `PR-2026-0247`、删除请求 `PR-2026-0249`、1 条 LINE 渠道、1 条充值申请。无本地钱包、扣费流水、订阅或 Identity 关联行。逐项回读不存在，`testdms` 前缀及域名均不再占用，新默认目标仍为 `production-ssdnodes`。
- 版本现场核对：新机 active image 和运行计费容器均为 digest `f67d0b72…` / 源码 `56e2497`；`git diff 56e2497..ed678c6 -- apps frontend infrastructure/images` 为空，两个业务工作树均干净且其提交已包含在 main。故此前业务改动已包含在新机镜像，但 DMS/ERP/Cowork 产品组尚未启用，不等于租户业务已上线。
- 后续运维差异只有 CI 保活、接收脚本执行位、本机 SSH/SMTP 导出工具及文档。CI/本机已使用新版本；此次也同步了新机上的 SSH/SMTP 工具副本，并逐文件 SHA-256 回读一致。没有因仅运维脚本/文档差异重新构建或重启业务镜像。
