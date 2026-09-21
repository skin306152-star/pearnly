# SSD Nodes 空环境切换

> 状态更新：上个窗口按用户要求停手并留下交接；接手窗口已按用户「只处理问题、不扩大设计和改动、只修 bug」的指示修完 **H01/H02/H04/H05/H07/H08/H09/H13/H15** 的代码部分，见文末「本轮修复」。仍未合并、未推送、未部署；H06 的存量密文、H10/H12 的线上菜单发布以及 H16/H17 的账目与显示问题仍在原地。

> 追加：用户随后要求「开户慢的问题一起处理」，接手窗口在新机上做了只读诊断 + 一次性计时，确认**一半是我们自己每步重复加的开销（已改掉），一半是这台机器的磁盘写延迟（代码改不了）**，见文末「H14 开户慢：自己排查的结果」。

> 再追加：用户要求「DMS 容器对齐到最新版（含本轮改动）并拉起来」。已把 main 推到 `8f1851f`、构建并通过投递把 DMS 产品组切到新镜像，工具副本与本机 runner 也已对齐，见文末「DMS 对齐到 8f1851f（已上线）」。

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

## 首次重新开户与速度诊断

用户要求等本次全部开户完成后诊断每一步变慢的原因。本节为现场诊断，不把建议写成已经实施；代码基线仍为 `1d7832e`，镜像仍为上述 `56e2497`。只有本节记录修改，未改正式服务参数或开户逻辑。

- 新发号单 `ISS-2026-0250`、请求 `PR-2026-0252`、部署 `DEP-2026-0253`：14/14 全部 done，控制面计时 20:38:28 至 20:51:37，共 789 秒。DMS 运行组已启动，正式租户 `testdms.pearnly.com` 的 HTTPS ping 为 200，原生身份与计费校验通过；这不是 LINE、OCR 或第三方 DMS 业务验收。
- 对照来自删除前原生数据库备份 `retired-tenant-20260920`，只解析旧 `DEP-2026-0248` 步骤和时间，不恢复数据库。旧第一步至最后一步约 303 秒，新同口径约 781 秒。旧 set_line 后来被重新配置，结束时间已覆盖，不能用该行计算首次开通耗时。

| 步骤 | 旧机秒数 | 新机秒数 |
| --- | ---: | ---: |
| new_site | 123.8 | 265.0 |
| install_core | 20.4 | 51.7 |
| install_product | 14.8 | 44.6 |
| set_oidc | 10.7 | 23.7 |
| restart_web | 4.0 | 34.9 |
| set_billing | 9.4 | 39.0 |
| set_ocr | 8.7 | 23.6 |
| finalize_site | 10.8 | 42.6 |
| set_line | 后续重跑覆盖 | 28.5 |
| bind_user | 13.2 | 40.4 |
| set_roles | 14.0 | 47.1 |
| set_workspace | 12.0 | 66.8 |
| publish_site | 14.4 | 21.2 |
| mark_active | 10.8 | 25.3 |

### 已测量的开销与边界

- 新机是 x86_64 / KVM，CPU 为 Xeon Silver 4214 2.20GHz。建站时 CPU 大部分空闲、内存约 2/32 GiB；维护容器无 CPU quota，cgroup throttled 为 0，内存没有 OOM。资源未满不能证明单次执行速度正常，撤回此前仅凭空闲率作出的“正常”判断。
- 一次 SSH 空命令 1.98 秒；现有容器 exec 空命令 0.84 秒；新建/运行/退出空容器 6.48 秒。匹配开户的现有 sites 卷后分段测量：create 1.10 秒、start/wait 4.33 秒、remove 0.43 秒，共 5.85 秒。该探针只执行 true，sites 卷只读，命名探针及其匿名 logs 卷已删除，没有删除命名卷。
- 只读 Frappe 导入、Site 连接、20 次 SELECT 1 和 Redis ping：现有容器总 4.11 秒，新临时容器总 9.28 秒；其中导入 Frappe 1.67–2.88 秒。另一条完整只读验收路径导入 3.58 秒、首次 System Settings 读取 3.06 秒，内部钱包读取 0.24 秒。容量核查本身约 2.07 秒，保留该保护。
- 因此每步独立 SSH、Docker run、框架加载有确定重复成本。仅将临时执行容器复用，在这次样本下每步有约 5 秒可节省空间；不能声称仅此能消除全部约 8 分钟差距。新机第一次启动整个 DMS 运行组与旧机运行组已存在也不完全同条件。
- 使用宿主机同一 ext4 文件系统的自动删除临时文件，仅写 24 次 4 KiB 并逐次 fsync（共 96 KiB）：中位 19.42 ms、最大 56.68 ms；容器启动阶段块设备 214 次写累计 4185 ms。同步写入延迟是建表/提交变慢的重要嫌疑，但不是数据库提交耗时的直接测量，不能据此声称全部延迟来自磁盘或证明商家使用机械硬盘。
- ext4 启用 discard，fstrim.timer 也已启用。探针各阶段 discard 累计仅 9–18 ms，不支持把在线 TRIM 认定为本次主要原因，因此未修改挂载选项。
- MariaDB buffer pool 1 GiB，737750 次逻辑读 / 764 次物理读；没有 redo log wait、Slow_queries 为 0，建站采样没有锁等待，完成后累计只有 1 次 row-lock wait。没有证据支持盲目加大缓存。performance_schema 为 OFF，缺少历史语句/文件等待分解；未重启数据库开启它。
- 服务端回环访问正式 HTTPS ping 约 0.21–0.43 秒，身份 discovery 约 0.44 秒；这是轻量端点读数，不代表桌面页面、真实用户网络或并发容量验收。未执行 LINE 消息、收费 OCR、业务建单或全套负载测试。

### 建议处理顺序（未实施）

1. 在当前受限 SSH、站点归属校验、容量检查及逐步幂等回执下，复用仅供开户的维护容器，避免每步创建/销毁；不向产品 web 容器授予数据库 root 凭据。进一步合并同一轮配置写入须保留每一步状态和失败恢复。
2. 核验服务器供应商的同步写入延迟和节点性能；若同一轻量样本持续偏高，要求供应商排查存储或迁移节点。旧机已停用，当前没有同版本、同缓存条件的双机基准，暂不能精确拆分单核与存储各占多少。
3. 保持 MariaDB `innodb_flush_log_at_trx_commit=1` 的持久性；不通过降低订单/账务落盘保证换取开户速度。也不凭 32 GiB 内存增大 worker 数或承诺并发容量。
4. 后续用户要求实施时，只改开户执行链，并用受控的独立验收 Site 验证重试/恢复和完整开通；不要重开或覆盖本次已成功的正式租户。

参考：[Docker exec](https://docs.docker.com/reference/cli/docker/container/exec/)、[MariaDB InnoDB 系统变量](https://mariadb.com/docs/server/server-usage/storage-engines/innodb/innodb-system-variables)、[Linux fstrim](https://man7.org/linux/man-pages/man8/fstrim.8.html)。本节诊断记录保留在 `codex/ssdnodes-migration` 工作树，尚未合并或推送；正式应用没有因为此次诊断重新发布。

## 新租户个人资料与 DMS 凭据故障诊断

用户提供四张正式租户截图，要求诊断显示不一致、之前要求收拢的账户功能缺失及 DMS 解密报错。本节仅诊断；未修改生产配置、密钥、凭据、业务数据或部署。基线 `1d7832e`，正式镜像仍为 `56e2497`。

### 现场证据与结论

1. **个人资料功能没有在新开户落地。** 正式 Site 的 User `Custom Field` 与 `Property Setter` 都为零；`pearnly_company`、`pearnly_domain`、`pearnly_change_password` 不存在，first_name 仍标为 First Name，mute_sounds 仍在原生 Settings 页签。浏览器 onload 已收到正确 company/domain，native_account=true，因此不是缺少公司数据或身份开关。正式 `account/layout.py`、`public/desk/user.js`、hooks.py 与主线文件 SHA-256 逐个相同；不是这三个源码漏投递。`ensure_profile_fields` 只挂 after_migrate；正式开户 install_app 后设置产品配置，再 finalize_site，未执行这个布局初始化。旧实验 Site 已初始化不能替代新租户验收。
2. **语言/时区所在的整列被本方脚本误隐藏。** 浏览器现场：全页刷新后两字段可见；点击表单刷新、等待响应完成后两字段不可见，表单未脏。两字段实际位于原生 `column_break_11`，而 user.js 白名单只保留 `column_break0`，把前者 df.hidden 设为 1。初次绘制还未将列的隐藏状态更新到 DOM，后续原生 layout.refresh 将其变为 `hide-control`，所以同一页表现不同。是布局结构与刷新时序缺陷，不能用要求用户清浏览器缓存结案。
3. **公司 DMS 连接的两项密文确实无法解密。** 对正式 `__Auth` 只读检查，EP-00001 的 password/admin_password 用当前文件中的站点密钥解密均为 InvalidToken；EP-00002 的 password 可解密。未输出密钥、密码或完整密文。EP-00001 两项密文的 Fernet 时间为 15:26:51 UTC，站点配置文件最后修改为 15:26:52.188 UTC。nginx 日志对应 15:26:51 test_credentials 200、save_connection 200，15:26:52 get_connection 200 且返回体包含错误消息。没有证据表明是第三方账号密码输错，也不是恢复旧租户数据库造成。
4. **首次密钥生成有跨请求/进程覆盖窗口。** 新开户未在发布前创建 encryption_key。锁定 Frappe 的 get_encryption_key 根据请求内 conf 判断缺失，再生成并写文件；请求站点配置有进程内 60 秒缓存，写入清除只作用于本进程。另一个持有无密钥旧快照的请求可再次生成并覆盖。仅在本地自动清理临时目录、无数据库/网络的隔离复现中，两个旧快照顺序调用真实原生函数，第二个密钥替换第一个，第一份密文随即无法解密。两环境 password.py SHA-256 相同（08394257…）。该机制与正式一秒内的写入/读取顺序吻合；没有保存当时各 worker 内存快照，不能把具体 worker 调度当作已采集历史证据。
5. **连接状态和异常呈现误导。** get_connection 只要存在 Endpoint 行便返回 connected=true，没有结合启用状态及凭据可解密性。连接向导的绿色成功提示使用保存前的 test_credentials 结果；后续 refresh 读取持久化密码失败，却仍展示“已连接”。Frappe 的 raise_exception=False 会吞 ValidationError，但 decrypt 内 frappe.throw 已将消息入队，因此普通读取也弹出框架技术细节；花名册读取关联连接会重复出现同一错误。
6. **开户完成检查覆盖不足。** verify_site 只校验产品/租户/身份、禁本地密码、scheduler 和钱包；没有校验账户扩展字段/排列、站点密钥提前固定及保存后跨进程读取凭据。14/14 done 不能证明这些产品能力完整。旧环境升级验证和镜像源码一致也不能替代全新开户后的业务验收。

### 修复顺序与边界（尚未实施）

- 新站点在 web/worker 接流量前固定唯一加密密钥；已有站点只校验、不重新生成替换。确认当前损坏凭据有原密钥备份时才恢复，否则经用户正常输入重新保存；不得直接换整站密钥，避免破坏已经可用的其他密文。
- 账户布局初始化接入原生新开户收尾与升级两条路径，幂等补齐字段、用户名标签及静音/改密位置；在产品配置已设置后执行。白名单保留可见字段的真实原生分栏/分节/页签结构，使首次加载、表单刷新、路由返回一致。
- 凭据读取失败应保留内部诊断、返回简洁且准确的不可用状态。连接向导须区分保存前探针成功与保存后实际可用；不得再凭记录存在显示已连接。检查花名册、LINE 及后台推送共用读取链。
- 补一组定向的新租户初始化/双进程密钥稳定性/账户两种刷新路径检查，不重复全套业务测试；最终部署与用户业务验收单独记录。

本节没有运行真实 LINE、付费 OCR、第三方 DMS 登录或建单；仅浏览器导航/刷新、服务器只读查询、源码核对及本地临时目录复现。诊断记录与上一节速度记录仍留在迁移工作树，未合并推送。

## LINE 新旧入口混用与网页下拉菜单核对

用户随后补充手机编辑器、聊天菜单/底部菜单、网页公司下拉菜单截图，要求查明为什么与上一版及已要求的效果不一致。以下仍为诊断，没有发布菜单、改 LIFF、发消息、登录第三方 DMS 或提交业务数据。

### 已从当前外部配置确认

- DMS-TEST 的 Messaging API `channel/webhook/endpoint` GET 返回 active=true，地址为 `https://testdms.pearnly.com/api/method/pearnly_core.line.webhook`；新站点配置的 LIFF 为 `2010411313-6iTLjpWn`。LINE Developers 实际页面显示此 LIFF 的 Endpoint 为 `https://testdms.pearnly.com/liff/dms-booking`，scope 为 openid，配置本身正确。
- 同一 OA 的 `user/all/richmenu` GET 仍返回旧菜单 `richmenu-1aa682af66e6a7b64c3350c1131166ef`，名称 `pearnly-dms-basic-v4-provider-liff-dms_a`，只有四个入口。当前唯一已绑定用户的独立菜单 GET 为 404，因而使用该默认菜单。菜单列表里的五份菜单均为旧 v1/v3/v4，未见新平台按内容散列命名的菜单。
- 旧默认菜单第 3 项实际 URL 是 `https://pearnly.com/home/dms-booking?portal=dms&channel=dms_a&openExternalBrowser=1`；第 4 项实际 URL 是 `https://liff.line.me/2010411313-K4TWQwYo/dms-booking?credentials=dms&channel=dms_a`。LINE Developers 页面确认后一个 LIFF 名称为 Pearnly Purchase、Endpoint 为 `https://pearnly.com/home`。它与新 DMS LIFF 不同，直接解释截图中仍打开旧域名、绿色主按钮及旧凭据表单。
- 新租户 `pearnly_dms_rich_menus` 配置不存在。当前已绑定用户的服务端 can_query=true，但未关联查询菜单。`request_sync`/`sync` 在缺少已发布菜单配置时直接返回，所以勾选查询权限后聊天卡片可出现第 5 项，而底部仍只有四项；这不证明查询业务本身已完成真机验收。
- `site-step.py` 的 set_line 只写 channel/secret/token/liff 配置；开户与 verify_site 没有发布/核对 OA 默认菜单及已绑定用户菜单的步骤。这次产品消息入口切换了，持续菜单发布遗漏，代码存在不能代替外部配置落地。

### 前端版本与下拉菜单

- 正式 product.js、menu/rich_menu.py、menu/links.py 与本地主线逐文件 SHA-256 一致。新站点公开提供的编辑器 index.html、index.2iTnqFA0.js、index.CRgWimy8.css 均为 200，且字节散列与当前运行镜像一致；本次截图中的旧编辑器来自旧入口，不能归因于新站点没投递这三个产物。
- 当前 AccountPage/LineLayout 使用白底深色文字、原生 Frappe UI 控件和紫色 pn-primary；截图旧页面有绿色 P 图标、绿色保存按钮及再次确认密码，这些不属于当前 AccountPage 模板。这里确认的是源码/产物与路由，尚未替用户完成真实 LINE 登录后的新页面操作验收。
- 网页公司下拉菜单在当前 product.js 明确设为“侧栏排序、刷新、注销”。与 14f0bb6 相比，5b7181a 去掉主题/Display，是用户此前要求取消无权限主题入口对应的变化。不能将这项有意变化当成随机版本回退；其配套“静音、改密码、公司信息收进用户详细信息”因上一节的新开户初始化缺失而没有落地。

### 必须分别闭环的缺口

1. OA 默认菜单与权限菜单发布、关联和回读，确保聊天菜单/底部菜单的同名动作最终进入同一租户与同一版本。
2. 验证入口 1–5 的真实目的地，尤其 3/4、查询权限变化及编辑器白底/黑字/紫色主按钮；不应通过修改共用的 Pearnly Purchase LIFF 来掩盖 DMS 菜单错误。
3. 新开户/升级两条路径补齐账户字段和原生布局；修复刷新后语言/时区整列隐藏。
4. 提前固定新站点密钥，处理现有损坏凭据，准确展示连接状态；不可把连接测试成功当作保存后的实际可用。
5. 发布完成检查纳入以上外部配置及新租户初始化回读。源码、镜像、14/14 开户步骤和业务入口一致性是不同证据，当前不标记为整体迁移验收完成。

当前只更新本任务诊断记录，未修改正式代码/配置，未运行全套测试；未合并或推送本节。新增证据来自当前 LINE GET、Developers 控制台、站点只读配置/权限和静态资源核对。

## 停止施工后的完整交接（2026-09-20，最新有效状态）

用户最后指示：**“你别搞了，你把全部问题列出来，我让下一个窗口修。分支保留着，你的改动保留着，让下个窗口接得上。”**

本窗口停止业务修改、外部配置写入和部署，只整理本交接。接手窗口按用户的继续指示施工；不要因本文里的历史部署授权自动在本窗口恢复执行。未删除或清理任何工作树、分支、实验数据或生产资源，没有停止正常运行的产品服务、SSH 隧道或开户常驻服务。

### 分支、差异与版本

- 本任务工作树：`/Users/skin/.codex/worktrees/ssdnodes-migration/pearnly`。
- 分支：`codex/ssdnodes-migration`；HEAD 为 `1d7832e6c5c767b310542b9022750992ec9957b0`。
- 本次交接现场 `main`、`origin/main`、`origin/codex/ssdnodes-migration` 均为同一 SHA。主目录 `/Users/skin/pearnly` 干净。
- 本轮诊断开始后没有业务源码补丁。唯一未提交改动是本文件：交接前已有 94 行诊断追加，本次继续追加交接和顶部提示。暂存区为空，未提交、未合并、未推送这些诊断与交接。
- 此前已完成并已部署的迁机实现仍在上述提交历史中，不能把“本轮没有业务补丁”理解成此前迁机没有做。
- 其他任务工作树保留不动：`auth-flow-fixes/pearnly`（`14f0bb6`）、`dms-line-diagnosis/pearnly`（`7415c1e`）。它们的挂载/数据不因 main 已包含提交就可删除。
- 最近生产核对：镜像源码 `56e249760d58df08ee23f72a06e727f5faa94600`，digest `sha256:f67d0b72e9615f3c5ea182bd989b5f428a738fa0db79e559060283ce1e3df844`。相关 App/前端差异及关键文件散列已在前文核对，新的故障修复尚未发布。接手后重新只读确认实际运行版本，不拿旧记录代替。

### 最新产品要求（覆盖较早相反要求）

1. 公司标题的下拉菜单按原生顺序：**首页、网站、分隔线、显示、刷新、分隔线、注销**。原“桌面”改“首页”，进入当前产品的订阅首页（现有 `/desk/pearnly-home`）；网站打开 `https://pearnly.com`，保留网站图标；帮助去掉。不要把首页指向旧 `/home` 或控制面。
2. “显示”及其原生主题能力恢复并真正可用，不能仅放回无权限入口。此前“删除主题入口”的要求被此次明确恢复要求替代。原生其他显示能力应保留；当前自定义顶层“侧栏排序”不在用户指定的新排列中。
3. 用户资料：用户名、公司/开户信息、语言、时区、静音、改密码归入用户详细信息；去掉中间名/姓等无关字段，不把设置和改密码另列入口。邮箱不可随用户名变化重新绑定，身份关联保持 issuer/sub。
4. LINE 客户编辑、订车编辑和本人凭据页同一套白底深色字、仅主按钮紫色、官方控件及与网页一致的尺寸；手机适配复用框架。新版链接必须实际打开新版，不以资源存在代替。
5. 绑定码纯六位数字。绑定成功提示和网页操作人显示用户名/姓名，系统 ID 只留审计。
6. 按量价格以用户最后纠正为准：每自然月前 200 页 THB 1.50，之后 THB 0.75（不是 1.00）。字符规则每 50 字符 THB 0.01，向上取整。去掉对用户无关的“曼谷/高精档”等文案，金额来自同一计费规则。
7. 只有 DMS-TEST 可供 LINE 验收；先本地必要闭环，再新服务器部署及真机验收。不要要求用户另建测试 OA。不重复已通过且无新修改影响的整套压测。

### 全部待办清单：已确认缺陷、最新待查、历史验收缺口分开

下列项目全部纳入接手范围。标为“历史本地通过”的项目不要求重写或无条件重测，但不能据此认定本次新服务器/新租户已验收。E01–E85 的完整历史清单见后面的关联记录，未在此删除任何旧待验项。

| 编号 | 问题/要求 | 当前证据与剩余工作 |
| --- | --- | --- |
| H01 | 用户资料每次打开内容不同，语言/时区消失 | 已复现。user.js 隐藏了真实父列 `column_break_11`；首次绘制和后续 layout.refresh 不一致。按可见字段保留真实页签/分节/分列祖先，验证首次打开、表单刷新、路由返回一致。 |
| H02 | 公司、域名、静音、改密码缺失；姓名标签没改成用户名 | 已确认新 Site 没有 User Custom Field/Property Setter；初始化只在 after_migrate，新开户没有执行。新建与升级都应幂等落地，不能只修一个存量站。 |
| H03 | 改用户名、改密码、邮箱登录同步 | 旧实验有真实本地 Keycloak/HTTP 通过记录；新租户入口目前因 H02 不完整。补新租户实际链路验证，保持邮箱/sub，不借修改第三方 DMS 密码完成 Pearnly 改密。 |
| H04 | 公司标题下拉菜单回退/缺显示和网站入口 | 已确认 current product.js 只有侧栏排序、刷新、注销。按上方最新菜单精确恢复；原生 theme API 为本人 desk_theme 更新，仍需以普通租户角色确认点击与保存有效。 |
| H05 | 网站图标与新租户品牌图标 | 网站菜单图标及 https://pearnly.com 去向待改；此前要求租户浏览器默认用 Pearnly favicon 需新开户回读，不把公司字母头像与 favicon 混为一项。 |
| H06 | 公司 DMS 凭据解密失败 | EP-00001 password/admin_password 用现有 key 均 InvalidToken，EP-00002 password 可解。先找可靠原 key/同一账户的有效凭据来源；缺来源则明确需要重新输入。不得换整站 key、重置第三方密码或把未知密码强行填入。 |
| H07 | 新站点首次 encryption_key 竞争覆盖 | 已隔离复现原生按旧 conf 快照再次生成覆盖。建站接流量前固定 key；现有 key 只校验不替换。验证跨进程读写稳定，新开户验收应包含此项。 |
| H08 | 解密失败却显示“已连接/连接正常” | 已确认 connected 只看 Endpoint 是否存在，向导用保存前探针结果报成功。改为启用状态与实际可用凭据/保存后回读一致；不以记录存在判断连接。 |
| H09 | 连接和操作员页弹框泄露框架技术错误、重复报错 | raise_exception=False 仍残留 decrypt 的消息队列。统一相关凭据读取/错误呈现，覆盖连接、花名册、主档、LINE、本人的外部凭据页和后台任务，不吞业务失败。 |
| H10 | 聊天菜单和底部菜单不一致、缺查询项 | OA 默认还是旧四项菜单；唯一绑定用户没有独立菜单，服务端 can_query=true；Site 缺 `pearnly_dms_rich_menus`。发布新版 basic/query 菜单，按权限绑定并回读；解绑/权限变化也须同步。 |
| H11 | LINE 打开的编辑器仍为旧绿色页面 | 已确认底部菜单 3/4 指向旧 pearnly.com 公共路径/旧 Purchase LIFF；新站 JS/CSS 与镜像一致。修真实入口后验客户、订车、本人凭据三页，不盲目重发静态包结案。 |
| H12 | LINE 公共域名/LIFF/菜单入口切换不全 | 当前 webhook 和新 DMS LIFF Endpoint 正确；具体新旧 URL 见下一节。只更新 DMS 对应入口，不能修改共用 Purchase LIFF 或 apex/www 归属来掩盖菜单错误。 |
| H13 | 开户没有发布并验证 LINE 持久菜单 | set_line 只写凭据，verify_site 没有外部菜单回读。将幂等发布和权限关联接到站点 HTTPS 已可用之后，确保未来新 OA/新租户不会重犯。 |
| H14 | 新开户每步很慢 | 14 步约 789 秒，旧同口径约 303 秒。实测每步 Docker 新建约 5 秒额外成本，框架导入也重复；同步写延迟偏高但未证明全部来自硬件。优化维护容器复用/配置写入，保留容量、归属、逐步回执和恢复能力，不能给业务容器 DB root 凭据。 |
| H15 | 开户“14/14”缺少产品完整性检查 | 现验收缺账户布局、稳定密钥、保存后凭据、LINE 外部菜单。补新租户初始化/升级差异的必要检查，不能用老实验 Site 已存在字段充当新建成功。 |
| H16 | 首页记录时间显示不完整（最新截图） | 用户新报，尚未复现。截图行值可见到秒，具体视口/列宽/截断处未定位；检查原生列表列宽、窄屏、时区与完整值显示，勿先猜改日期格式。 |
| H17 | 2026-09-20 22:43:53 身份证识别扣费 THB 0.00，摘要为“—” | 尚未查账。截图显示 M 当前套餐、1 页、结果“成功”、余额 750，同页有 -250 套餐费。套餐额度抵扣是可能解释而非结论。需关联 OCR 任务、用量 reference、套餐页数变化、账本和外部推送记录，明确识别成功/失败、计费/额度、推送结果分别是什么；不能凭 0 元判定未识别或推送失败。 |
| H18 | A 档之前 ×3、4.50 与报价不一致 | 历史已定位新仓误加售价乘数，候选已去除并有真账本检查，现镜像包含该历史改动。仍需核对新站实际规则与 H17；供应商成本和租户售价分开。原旧租户两笔共多扣 6 THB 的历史未冲正，不能移记到新空环境租户。 |
| H19 | 套餐/无套餐/字符/密集件/并发重复扣费规则 | 旧清单有 97 项计费和 HTTP 快照冲突定向检查。当前新机需只对有证据的偏差核查；续订是否恢复自动扣款历史未定，不自行恢复自动扣费。 |
| H20 | LINE 原生 loading 初期不出现、后期才出现 | 历史已改调用时机及 HTTP 202 处理，本地通过；DMS-TEST 真机表现未闭环。不要用模拟 loading 宣称官方客户端通过。 |
| H21 | OA 官方首次欢迎语重复 | 历史任务没有改官方开关。仍需关闭官方欢迎语，保留必要绑定引导，并以后台回读/真机 follow 证明。 |
| H22 | 绑定回执/推送记录显示系统 ID、六位码及撤销行为 | 历史姓名投影、数字码、过期/防猜/重绑已有本地检查；新 OA 验证实际展示与绑定变化，不输出内部 operators.local。 |
| H23 | 编辑按钮、取消/清空/保存重开、所有编辑字段真实推送 | 历史客户/订车本地真实 DMS 回读通过，仍需新入口验收。保持已编辑/主动清空值，不回填旧值；逐字段清单关联 below，不宣称任意未来字段自动映射。 |
| H24 | 订单确认后没回执、疑似没推送 | 最早手机 05:29 那笔没有重放或成功证据；最新 H17 同样未对账。先查真实外部单号和详情，再处理附件/消息补发，禁止为了显示成功伪报或盲目再次建单。 |
| H25 | LINE 菜单 1–5 全部能力和查询权限 | 历史有真建客、订车、本人门户登录、凭据保存、查询状态对照；生产新路由和损坏凭据会影响实际行为。按本人权限核实，管理员查询不能借给销售；非空排行仍缺外部业务证据。 |
| H26 | DMS 车型/颜色等主档增删改同步与字段映射 | 历史自建主档 CRUD/实时缓存/提交前失效守卫已验证，夹具已精确清理；非空主管/汇款方槽、每一种车型派生属性变化未逐项另建单。避免把当前支持字段说成未来全字段自动兼容。 |
| H27 | Web 首页/推送记录/操作员列表、刷新/勾选删除一致 | 历史已有原生列表及单选/全选、选中右上角删除、软删除保留审计实现和本地证据。最新需修 H16；保持刷新小图标、选择不被自动刷新打断，不因 UI 删除影响账本/余额。 |
| H28 | 三 App 共享 OCR 并发、队列、恢复与容量 | 已有共享限流/公平性/独立 OCR worker/恢复等代码与本地测量；32 GB 新机未测极限，真实供应商吞吐未由替身压测证明。不要承诺最大人数或无限并发；ERP/Cowork 实际 OCR 入口未完整迁入，不能称三产品业务全通。 |
| H29 | 手机适配与正式文案 | 旧桌面/390px本地检查通过，但旧 LINE 入口仍让用户看到错误皮肤。修入口后按实际页面检查；计费文案同源，无“曼谷/高精档”展示，语言/尺寸遵守原生能力。 |
| H30 | 统一部署、外部验证、用户验收 | 以上本轮新增缺陷未修复未部署。合回 main、推送、CI 镜像、生产 digest、原生迁移、静态产物、外部菜单回读分别留证；DMS 真落单/手机回执/真实登录不得由健康检查代签。生产密码重置邮件送达、定期异地备份/恢复演练仍是历史边界，不能宣称已完成。 |

H11 表中的旧公共路径即 `pearnly.com`；它不是新租户编辑器地址。新增截图只作为报告，不等于已经查到 H16/H17 根因。

### LINE 精确配置交接（此前只读核验，未修改）

- OA：DMS-TEST，Messaging API channel `2011511433`。
- 已正确：Webhook `https://testdms.pearnly.com/api/method/pearnly_core.line.webhook`，active=true。
- 已正确：新 DMS LIFF `2010411313-6iTLjpWn`，Endpoint `https://testdms.pearnly.com/liff/dms-booking`，openid。
- 新平台动作：菜单 3 进入本租户门户交接（`action=portal`）；菜单 4 用上面新 LIFF（`action=credentials`）。客户/订车票据编辑也使用新平台入口。
- 仍错误：OA 默认 `richmenu-1aa682af66e6a7b64c3350c1131166ef`（`pearnly-dms-basic-v4-provider-liff-dms_a`）第 3 项是 `https://pearnly.com/home/dms-booking?portal=dms&channel=dms_a&openExternalBrowser=1`。
- 仍错误：其第 4 项是 `https://liff.line.me/2010411313-K4TWQwYo/dms-booking?credentials=dms&channel=dms_a`；后者属于 **Pearnly Purchase**，Endpoint `https://pearnly.com/home`。不能全局改这个 LIFF；替换 DMS 菜单指向才是本任务边界。
- 当前租户没有 `pearnly_dms_rich_menus`，导致普通同步直接返回。需要发布新菜单并回读 default/per-user。旧菜单暂未删，接手须先完成替换与关联验证，不先批量删除。

### 实现落点与建议顺序（仅方案，尚未动手）

1. 先处理密钥和凭据：`infrastructure/production/site-step.py`、`apps/pearnly_core/pearnly_core/provisioning.py`；共享凭据契约参考 `pearnly_core/endpoints.py`。DMS 受影响读取在 `connections/api.py`、`operators/endpoints.py`、`masters/masters_cache.py`、`adapter/session.py`、`account/api.py`。防止引入 adapter→connections 的循环依赖；`pearnly_core/password.py` 是 Pearnly 身份密码逻辑，不能仅因文件名相同塞入第三方密文处理。
2. 账户布局：`account/layout.py`、`hooks.py`、`public/desk/user.js`；新开户 finalize 在产品配置设好后调用现有幂等布局初始化，旧站仍走 native migrate。父布局可按元数据动态取实际祖先。
3. 原生菜单：`public/desk/product.js`；复用锁定 Frappe SidebarHeader `get_display_siblings()` 与 ThemeSwitcher、clear_cache，不改上游源码或自建主题控件。
4. LINE：`apps/pearnly_dms/pearnly_dms/menu/rich_menu.py` 的 publish/reconcile/sync 与 `menu/links.py`。发布门槛应放在真实 HTTPS 已可用、正确产品/归属/凭据已确认之后；不向用户额外发测试消息来验证菜单。
5. 开户速度：`infrastructure/production/provision-agent.py`、`site-step.py`、`scripts/production/provision-runner.py`。可复用单独 root 维护容器，限定镜像/挂载/所有权、寿命、内存和最后清理，仍每步骤独立回执/原生执行；不能改成向常驻 web 提供 root 凭据。性能改善需要新测量，不能把每步约 5 秒节省说成解决全部 8 分钟差额。
6. 最新账目/时间：`public/desk/home.js`、`billing_www.py`、Control payments 与 OCR execution 记录。先只读关联这笔 usage/OCR/reference/Subscription/Push Log/Delivery 后再改显示；必要时将“套餐额度内，余额扣费 0”准确表达，但未经核实不要先写定结论。
7. 最后按有修改的影响面做架构、DMS 离线和定向检查。新建初始化必须用独立验收 Site，不覆盖 testdms；UI 首开/刷新/窄屏及外部菜单回读为必要场景，不无条件再跑 E01–E85 全套。

### 运行连接与环境归属

- 新服务器：`107.155.65.74`，SSH 使用 `/Users/skin/.config/pearnly-production/operator`，`IdentitiesOnly=yes`、`BatchMode=yes`、`StrictHostKeyChecking=yes`。凭据不写本文、不输出运行 env 或 Site key。
- 生产根：`/opt/pearnly/deploy`，`active-image.json` 是数组结构；私有配置在 `/opt/pearnly/secrets`。Compose 项目 `pearnly-production`，DMS 容器包括 `dms-web`、普通 worker、OCR worker、OCR dispatch、scheduler、socketio、frontend，另有私有 billing/Keycloak/MariaDB/Redis。
- 正式 Site：`testdms.pearnly.com`；新 issue `ISS-2026-0250`、request `PR-2026-0252`、deployment `DEP-2026-0253`。旧 `DEP-2026-0248` 已退休删除本地记录，不能把旧账目/绑定重新导入新站。
- 已知本地 Docker context `colima-pearnly`。`pearnly-dms-line-lab-site-1` 的 App 挂载来自 **dms-line-diagnosis 工作树**；控制面 `pearnly-sample-control-bench-1` 从 **main** 挂载。不要从迁移工作树擅自接管共享卷/源目录或覆盖其他任务代码。
- 发布入口：`.github/workflows/build-production.yml`、`stage-production.yml` 与 `scripts/production/activate-product.sh`。此前投递成功不等于本次新修复上线。实际执行前读脚本与当前运行状态，按既有备份/维护/660秒业务退出边界执行。
- 本次交接未启动发布或业务修复命令；正常长期服务保留。未建立自动继续施工的定时任务。

### 历史证据与不能遗漏的细项

- [E01–E85 完整清单与分层证据](2026-09-20-dms-line-production-diagnosis.md)：包含 loading、欢迎语、姓名、绑定、1–5、会话/权限、附件与回执、查询、账户、UI、计费、共享预算与恢复。其顶部仍记录旧 Contabo 发布阻塞；**该服务器状态已被本文新 SSD Nodes 事实取代**，内部“未提交/未部署”等分时段描述不能直接当当前 Git 状态。
- [字段映射清册](2026-09-20-dms-line-field-map.md)：客户28项、编辑/订单104项、派生43项的实际回读边界，以及仍缺非空样本/属性变更的槽位。不要漏掉主动清空、三套地址复制、佛历日期、收款六渠道和两类附件。
- [共享 OCR 容量与恢复](../operations/shared-ocr-capacity.md)：当前起步预算全局4在途、单租户2、每模型桶30请求/分钟、全局排队300/租户50/最长30分钟；这些是应用配置，**不是供应商保底配额或新机承载上限**。有真实单笔 OCR 和模拟供应商负载的分别说明。
- [原生 Desk/身份的较早验收](2026-09-20-auth-flow-fixes.md)、[早期部署](../releases/2026-09-20-product-desk.md) 只证明其当时版本，不能覆盖这次新站初始化缺陷。
- 本次最新截图：`codex-clipboard-e522ac71-3bb9-4f18-8986-78718bdec136.png`、`codex-clipboard-5205fe53-54cd-4de1-9d2f-50313d0136ac.png`，原文件在用户提供的 macOS 临时目录；敏感原始资料不复制入仓。

交接验证限 Git 状态及本文件 `git diff --check`；没有因文档追加运行架构/业务/OCR/并发测试。下一窗口应从本文件的最新要求、H01–H30 和当前 Git/生产只读核对开始，而不是从旧状态卡重新部署旧候选。

## 本轮修复（接手窗口，只改代码）

用户指示：**只处理问题，不扩大设计和改动，不擅自添加东西，只修 bug。** 因此本轮只动 H01–H30 里能落到代码上的缺陷，不做设计调整、不加功能、不动生产、不发布菜单、不发 LINE、不跑付费 OCR，也没有创建租户。基线仍是 `1d7832e`。

| 编号 | 改了什么 | 落点 |
| --- | --- | --- |
| H01 | 个人资料不再只留一份"可见字段名单"，而是按字段顺序算出可见字段**真实所在的页签/分节/分列**并一起保留；改完补一次原生布局刷新，让首次绘制与表单刷新看到同一份内容 | `apps/pearnly_core/pearnly_core/public/desk/user.js` |
| H02 | 建站收尾显式执行账户布局初始化（幂等）。原因：该初始化只挂 `after_migrate`，而新开户不走 migrate，正式租户上 Custom Field / Property Setter 全为 0 | `apps/pearnly_core/pearnly_core/provisioning.py` |
| H04 H05 | 公司标题下拉菜单按用户指定的原生顺序恢复：首页（`/desk/pearnly-home`）、网站（`https://pearnly.com`，保留网站图标）、分隔线、**侧栏排序**（用户 2026-09-20 明确保留，放原生"编辑侧栏"的位置即第一条分隔线之后）、显示（原生主题/全宽/侧栏三项）、刷新、分隔线、注销。标题里那份菜单不渲染分隔线行（原生那份没有该行、照搬会多出一行 `undefined`） | `apps/pearnly_core/pearnly_core/public/desk/product.js` |
| H07 | 新站点在建站步骤就固定 `encryption_key`，且**只在缺失时**写；已有站点只校验不重生成，避免换掉整站密钥让现在能解的密文一起失效 | `infrastructure/production/site-step.py` |
| H08 | 连接状态改成"现在真能用"：启用 **且** 这组账号的密码解得开才算 `connected`；保存后回读，读不回来直接报错，不再"记录存在就显示已连接" | `apps/pearnly_dms/pearnly_dms/connections/api.py` |
| H09 | 新增共用凭据读取口：解不开密文只回空串，并丢掉 Frappe 解密时排队的那条框架消息（`raise_exception=False` 只吞异常、不吞消息，这才是弹框泄露技术细节的来源）。连接、适配器登录、主档缓存作用域、操作员与连接同步、花名册、本人门户都改走这一个口 | `apps/pearnly_core/pearnly_core/endpoints.py` + DMS 六处调用点 |
| H13 H15 | `set_line` 在写渠道配置后幂等发布持久菜单（只在 DMS 且配了 LIFF 时）；入口检查 `verify_site` 增加：站点密钥已固定、账户布局字段与标签存在、已配 LIFF 的租户其 OA 默认菜单就是本租户发布的菜单 | `infrastructure/production/site-step.py`、`apps/pearnly_dms/pearnly_dms/menu/rich_menu.py` |

新增文案一条（保存后凭据读不回来）已按流程进术语表并生成四语包：`scripts/i18n/messages.tsv` 与 `apps/pearnly_dms/pearnly_dms/translations/*.csv`。

### 本地证据

- `node tests/test-native-desk.cjs`：在原有用例上补了个人资料字段祖先可见性与公司菜单断言，通过（含"显示"项仍拿原生主题/全宽/侧栏动作、首页去 `/desk/pearnly-home`、网站打开 `https://pearnly.com`、分隔线不会渲染成 `undefined` 行）。
- `python3 scripts/check-architecture.py` 0 违规；`tests/test_architecture_guard.py`、`tests/test_native_desk_contract.py`、`scripts/check-dms-offline.py`（client / booking-foundation / booking-payments / booking-readback 全通过）、`scripts/i18n-build.py --check`、`tests/test_welcome_email.py`、`scripts/production/test-provision-agent.py`、`scripts/check-production-capacity.py`、`scripts/production/test-activate-product.py`、`node tests/test-roster-selection.cjs`、`node tests/test-records-selection.cjs`、`node scripts/production/test-issue-actions.cjs` 均通过；`git diff --check` 干净。
- 凭据读取与连接可用性另做了一次性本地桩校验（临时目录，不入仓）：解密失败回空串且框架消息队列保持为空、`has_credential` 不把读不出的凭据算成已配置、`get_connection` 只在"启用且凭据可解"时为 true、停用为 false。
- **未跑**：真实 Site、浏览器/窄屏、LINE 真机、付费 OCR、生产部署；`tests/test_dms_entrypoints.py`、`tests/check_*_runtime.py` 需要容器内的 Frappe 与数据库，本轮未跑。

### 仍未处理（留给需要现场或授权的动作）

- H06 的存量损坏密文：需要原密钥备份，否则要用户重新输入；本轮只保证不再发生（H07）并且界面如实显示（H08）。
- H10/H12/H13 的**线上**动作：现有租户（`testdms.pearnly.com`）的 OA 默认菜单仍是旧菜单，发布是对 LINE 的外部写入，需要单独授权。
- H16（首页时间显示不完整）与 H17（0.00 扣费）：都要现场截图/账目与 OCR、用量、推送记录关联，未复现前不改日期格式或金额显示。
- H20–H29 的真机与业务验收、H30 的合并/推送/部署。

## H14 开户慢：自己排查的结果（2026-09-20，接手窗口）

用户要求「把这个问题一起处理了」，并质疑「是服务器问题吗、硬件升级了反而更慢？」。以下是**在新机上现量的数据**（只读查询 + 一次性计时探针，探针文件与临时容器都已清掉，没改任何生产配置）。

### 一、不是 CPU、不是内存

- `vmstat 1 4` 采样里 **steal 全为 0**（没有宿主机超售抢 CPU），无 swap（Swap 0），32 GiB 里 25 GiB 空闲，load ≈0.7。
- 压力指标 PSI：cpu some avg10=1.34；io **full avg10=2.41 / avg60=3.32** —— 机器基本空闲，但仍有 I/O 停顿。
- 单核能力是 Xeon Silver 4214 @2.20 GHz（8 vCPU，1 核/线程）。它不是"比旧机差很多"的那一项。

### 二、磁盘写路径是这台机器的短板（可测量的部分）

- 设备：QEMU `virtio-scsi` 卷（`sda`，经 SCSI 控制器挂载），内核把它标成 **rotational=1**、`write_cache=write through`，文件系统 ext4 挂在 `/dev/sda2`（`rw,relatime,discard`）。
- 4 KiB 逐次写 + fsync x24：**中位 8.00 ms，最大 13.51 ms**（同口径昨天记录是 19.42 ms，说明它是波动的）。
- 4 KiB `O_DIRECT` 随机写 x24：中位 **3.62 ms**（≈280 IOPS）。
- 顺序直写 200 MiB：**103 MB/s**；顺序直读 200 MiB：586 MB/s。
- 结论：开户里最重的 `new_site`（新机 265.0 s / 旧机 123.8 s）就是几千次建表与提交，全部压在这个 fsync 延迟上；`set_workspace` 66.8 s、`install_core` 51.7 s 同理。**这部分不是我们的代码能改的**，只能由供应商换存储/换节点，或接受。

### 三、我们自己每步重复加的开销（已改）

同一批测量（新机，同一台机器同一镜像）：

| 动作 | 全新容器（每步都建） | 复用已有容器 | 每步可省 |
| --- | ---: | ---: | ---: |
| 空转 `true` | 3.55 s | 0.74 s | 2.8 s |
| `import frappe` | 10.32 s | 4.76 s | 5.6 s |

另外：每步一次容量检查（约 2–3 s）、每步一次控制面桥接往返（0.47 s ×2）、步与步之间固定 1 s 空转。

改动（只动宿主机与本机工具，不动业务代码、不动数据库参数）：

1. `infrastructure/production/provision-agent.py`：整轮开户**复用一个受限维护容器**（同镜像 digest、只读密钥、同一 sites 卷、uid 0、内存 1 GiB 上限、只跑 `sleep`），每步仍是独立的 `site-step.py` 进程，逐步回执与失败恢复不变；镜像变了、容器停了或超过 12 小时就重建；`verify_site`/`drop_site` 收尾删除。
2. 同文件：容量准入从"每步一次"改成"每个容器生命周期一次"（保护不变，重复开销去掉）；拆除（`drop_site`）不做容量准入，与旧行为一致。
3. `scripts/production/provision-runner.py`：一步做完立刻取下一步，不再空转 1 秒；失败退让 1 秒、队列空才睡 5 秒。

### 四、彩排数据（真实主机、真实镜像、既有租户的只读步骤）

同一步骤重复 4 次 + 一次 `verify_site`，两种路径交替跑：

- 旧路径（每步一个全新容器）：8.3 / 7.7 / 7.3 / 8.7 s，稳态平均 **≈8.0 s**。
- 新路径（复用容器）：12.1 / 5.0 / 3.8 / 4.3 s，稳态 **≈4.4 s**（第一步含建容器与容量检查）。
- 按 14 步推算：容器与容量检查合计省 **约 1.5–2 分钟**（789 s → 660 s 量级），**其余 5 分钟以上在磁盘上**。

### 五、明确不做的取舍

- 不降 `innodb_flush_log_at_trx_commit`（MariaDB 里它是全局变量，会同时影响在跑的账务/身份数据）。
- 不关 `innodb_doublewrite`（撕裂页保护）。
- 不把数据库或站点卷放到内存盘。
- `innodb_log_file_size` 调大是**安全候选**（只增加崩溃恢复时间，不降持久性），但没有基准证明收益前不改。

### 六、部署顺序（这一点必须一起看）

- `provision-agent.py`、`provision-runner.py` 只影响宿主机与本机，可以独立先上；runner 是 launchd 常驻进程，跑的是**主检出** `/Users/skin/pearnly/scripts/production/provision-runner.py`，所以要合回 `main` 才生效。
- `site-step.py` 这边的改动（H07/H13/H15）**依赖新镜像**：它调用 `pearnly_dms.menu.rich_menu.published_state` 与新的 `finalize_site` 账户布局初始化，镜像里没有这两个东西时 `set_line` 会直接失败。所以工具同步必须和"构镜像→投递→激活"同一次做。
- 真实收益要到下一次开户才有端到端数字（需要你触发一次开户）；本文件里的数字是同一台机器上的分步实测与彩排，不是整轮验收。

## DMS 对齐到 8f1851f（已上线，2026-09-20）

用户要求「dms 这边的容器对齐到最新版，包括你的改动，拉起来我看看，等下再做开新租户测试」。执行过程与回读如下（全部有命令回读，不是"应该没问题"）。

### 发布链

- main 快进到 `8f1851f`（= 本任务分支的 3 个提交：H01/H02/H04/H05/H07/H08/H09/H13/H15 + 菜单保留 + H14 执行链），已推送。
- 构建 [run 35523469754](https://github.com/skin306152-star/pearnly/actions/runs/35523469754) 成功（含架构检查、前端构建、镜像内 `test_cloud_onboarding.py` / `test_dms_entrypoints.py` 与运行期自检）。
- 镜像：`ghcr.io/skin306152-star/pearnly/frappe@sha256:5346ad0b737827bc3d1c408ca61851d29a94e4bbba870bf83a7f006be4bda271`，镜像内 revision 标签回读为 `8f1851fcd4e8f4ae05473bdfc761bfd186690f5a`。
- 投递 [run 35523736225](https://github.com/skin306152-star/pearnly/actions/runs/35523736225) 成功（受限镜像接收器只拉取、不启动）。
- 激活：`bash scripts/production/activate-product.sh <image> dms`，3 分 37 秒，输出 `Product dms activated with verified image.`（含维护模式开关、旧镜像整站备份、`bench migrate`、7 个容器重建）。激活前先逐字比对过服务器上的 `activate-product.sh` 与 `compose.yml`，与仓库 SHA-256 一致。

### 发布后必须补的两件事（都做了）

- **`active-image.json` 指向新 digest**：`activate-product.sh` 不写这个文件，只有 `activate-foundation.sh` 写。不补的话，下一次任何 `compose up -d`（例如开户里的 `restart_web`）会用旧的 `PEARNLY_IMAGE` 重建容器，把这次改动**悄悄回退**。
- **宿主机工具副本同步**：`infrastructure/production/provision-agent.py`、`site-step.py` 已覆盖到 `/opt/pearnly/deploy/...`，与本地 SHA-256 逐文件一致（`31d1da5d…` / `17818b7d…`），`provision-agent.py` 的执行位保留（755）。
- 本机生产 runner（launchd `com.pearnly.production-provisioner`，跑的是**主检出**的 `scripts/production/provision-runner.py`）已重启：pid 42526 → 75791，新的"步间不空转"逻辑生效。

### 回读

- 7 个 DMS 容器全部为 `5346ad0b7378`：frontend / web / worker / ocr-worker / ocr-dispatch / socketio / scheduler。`billing-web` 仍是 `f67d0b72e961`（基础层这次没有激活，属于有意保持）。
- `https://testdms.pearnly.com/api/method/ping` → `{"message":"pong"}`；`/desk` 与 `/login` 302 到统一身份；`/liff/dms-booking` 200；`/assets/pearnly_core/desk/home.js` 200。
- 浏览器会加载到的 `assets/pearnly_core/desk/product.js` 已含 `reorder-sidebar` / `https://pearnly.com` / `/desk/pearnly-home`，`user.js` 已含 `pearnlyProfileScope`。
- 站点状态：`maintenance_mode=0`、调度器 active、`encryption_key` 存在、LINE LIFF 已配。
- **账户布局也已补到既有租户**：激活时的 `bench migrate` 触发了 `after_migrate`，User 的三个 Custom Field 与 `first_name` 标签 Property Setter、field_order 现在都存在 —— H02 的修复对"新开户"和"升级"两条路径都落地了。
- 仍为空的是 `pearnly_dms_rich_menus`：LINE 持久菜单还没发布（H10/H13 的**线上动作**，属于对 LINE 的外部写入，未做）。
- DMS 各容器发布后 8 分钟内没有 traceback/异常。

### 一个观察（不影响使用，值得盯一次）

`dms-ocr-dispatch` 在发布窗口内（16:54–16:57）重启了 7 次，之后自 16:57:27 起稳定运行至今（进程在跑、CPU 时间在涨、OCR 队列为空、日志无报错）。窗口内正是维护模式开着、容器被重建的时段，而 `execution/dispatch.py` 本身在 `maintenance_mode` 下不派发、启动早期连 Redis 失败会在循环外退出——这解释了重启的形状，但本次没有更细的证据，属于**下次发布时再核对一次**的观察项，不是本轮的代码改动引入的。

### 本轮未做（保持边界）

- 基础层（billing / keycloak / mariadb）没有重新激活，仍跑旧镜像。
- LINE 菜单发布、H06 存量密文处理、真机与业务验收、新租户开户测试：都还没做；开新租户测试用当前这套（新镜像 + 新工具 + 新 runner）即可。

## 2026-09-21 用户报障三例 + 新旧差异清单（第 1 批修复：本地完成，**未部署**）

用户明确：`6/7/9/10/11` 不做，其余做；**改完先停、不部署**，等 SSD Nodes 主机迁移结果。

### 基准与对账卡口（新增）

- **唯一老基准是 `origin/master` 上的 `501589f0`**（2026-09-18，当时真在服务线上流量的那版）。
  本地老仓库的 `master` 落后 12 天、`services/line_dms` 少 15 个文件，**拿它当基准会误报一大堆差异**
  （本轮排查先踩过这个坑）。
- 新增 `scripts/check-dms-parity.py` + `scripts/dms-parity-expectations.json`：以 `501589f0` 为基准，
  对老 `services/line_dms`（含登记的重命名落点）逐个模块做 **AST 级**对比（去注释/文档字符串/import），
  把"已复核过的差异"冻成指纹；差异一变即失败退出。基准不在本机（CI）时跳过、不阻断。

### 三个报障的定论（2026-09-21 生产只读排查，均有现场证据）

1. **有档案的客户被判成"新客户"，看不到资料差异对比** —— 查重这条读没借管理员账号。
   证据：客户 `115` 在 DMS 真实存在（证号 `1103702587460`，直读 `cus/form.php` 读到）；
   用 `sale02` 搜证号/姓名/手机号/客户号**全部** 返回 DMS 的"无数据"（`ndt::`），
   用 `dmstest` 搜同一证号**命中 115**。老基准里 `recognize_lookup` / `customer_fields` /
   `geo_mrerp_dms` 三处都是 `authoritative_read=True`，新仓这三处丢了（自 2026-09-18 首版起就没带）。
2. **编辑页地址首屏空白、点一下才显示** —— 值在、选项表没到。服务端只返回"有父级的层级"的表
   （实测：只给府 → 府+县；什么都没有 → 一张表都没有）；前端 `hydrateGeo()` 只兜底县/镇/邮编，
   **府那一级不兜底**（老页面是 `bundled.provinces || await geo('provinces','')`），
   而下拉框找不到选项就显示空白，用户一交互级联把选项补上才"映射出来"。
3. **登录 DMS 要点两次** —— 菜单3 的 SPA 页多留了一个按钮：第一次点击只跳到中转页，
   中转页还有第二个按钮才真打开。老页面在 LIFF 侧自动换链接，只在中转页点一次。
4. **网络断连被兜成"服务器内部错误"**（2026-09-20 01:50 那次 `socket hang up`）——
   读请求断连没有归类成 `ERR_DMS_TECHNICAL`（"DMS 连接超时 — 请重试"），一路冒到最外层变
   `ERR_UNEXPECTED`，也不重试。老项目同样代码、同样会这样（近 30 天云端日志 0 次同类，属低频）。

### 本轮改动

| 文件 | 改动 | 为什么 |
| --- | --- | --- |
| `apps/pearnly_dms/pearnly_dms/intake/service.py` | `recognize_lookup` / `customer_fields` / `geo_options` 三处读加回 `authoritative_read=True`（写路径 `push_idcard_fields` 不动） | 报障 1；与老基准的三处对齐 |
| `apps/pearnly_dms/pearnly_dms/client/transport.py` | 传输层断连：**读**路径（`component/`、`/form.php`）重试一次后归类 `ERR_DMS_TECHNICAL`；**写**路径绝不重试（连接断了不代表没落库）；非传输类异常原样抛出 | 报障 4 |
| `frontend/src/pages/dms/EditorPage.vue` | `hydrateGeo()` 补上"府"、四级都兜底；操作条 `justify-content: flex-end`（按钮移到右下角） | 报障 2 + 用户要求 |
| `frontend/src/pages/dms/AccountPage.vue` | 门户模式改为**挂载即自动换取链接并跳转**（不再留第二个按钮、不再显示账号框）；账号维护页保留账号/密码表单 | 报障 3 |
| `translations/{th,en,zh,ja}.csv` | 保存按钮文案 → 保存 / Save / บันทึก / 保存（原为"保存并发送新版预览"） | 用户要求 |
| `apps/pearnly_dms/pearnly_dms/line/handlers.py` | 解绑词补回老 DMS 通道的泰文 `ยกเลิกการเชื่อมต่อ`；改正"老产品只认一个中文词"的错误注释 | 老用户按旧话术打字必须能解绑 |
| `intake/flow.py` / `line/text_router.py` | 改正"菜单层/审批流/查询未搬"的过期注释（实际都已接线，并写明落点） | 注释与代码不符会误导后续维护 |
| `scripts/sample-verify-dms-edit.py`、`scripts/sample-verify-dms-edit-page.mjs` | 断言与选择器跟着新文案改（`text="Save"` / `text="保存"`） | 保持检查有效 |

### 用户明确不做（本轮不碰）

`6` 按租户 LINE 灰度闸（新架构一站一租户，站点级/开通功能级已天然隔离）；
`7` 车型/银行目录"已取消"项过滤（已实测：DMS 里 `byd/f3` 状态就是 `ยกเลิก`，被 `active_models` 静默滤掉）；
`9` 订车逐问中图片消息的判断位置；`10` postback 分发顺序；`11` 新站点台账为空（数据属性，不是 bug）。

### 本地验证

| 检查 | 结果 |
| --- | --- |
| `python3 scripts/check-architecture.py` | PASS（0 违规） |
| `python3 scripts/check-dms-offline.py` | 全 PASS（client 77、booking-foundation 45、booking-payments 42、booking-readback 43 …） |
| `python3 tests/test_architecture_guard.py` / `test_native_desk_contract.py` | 9 OK / 15 OK |
| `python3 scripts/check-i18n.py` / `check-frontend-i18n.py` | 0 问题 / 0 问题 |
| 前端 `vue-tsc --noEmit` / `vite build` | 0 错 / 构建通过 |
| `python3 scripts/check-dms-parity.py` | 与老产品差异**没有漂移**（47 个模块登记在案） |

**未做**：镜像构建、部署、真机与业务验收 —— 用户 2026-09-21 要求"改完先停、不部署"。
下次部署后要真机验的四条：①有档案的客户出"差异对比卡"；②编辑页地址首屏就有值；
③登录 DMS 一次点击；④断连提示为"DMS 连接超时 — 请重试"且自动重试过一次。

## 2026-09-21 第二批：真机报障四条 + 主档新鲜度方案（本地完成，未部署）

用户 2026-09-21 真机验收第一批后报障四条并确认方案：
**批 1（本次全做）** 图一编辑入口、差异卡地址显示、图三门户外站、后台预热；
**批 2（本次一起做）** 编辑页改走"读走有界新鲜共享快照 + 写前权威复核"。

### 报障与定论

1. **点 [แก้ไข] 多弹一张卡**（图一）——**回归**：`diff_card` 的编辑按钮写成了裸 postback，
   没走 `_edit_button()`；而老基准（`501589f0:services/line_dms/cards.py` 的 `diff_card`）
   用的是 `_edit_button(nonce)`（能拿深链就用 `uri` 动作）。其它卡（新客户/资料一致/订车预览）
   本来就是一次点击直开编辑页，所以只有差异卡犯这个毛病。
2. **差异卡"旧值"印出裸 id**（用户要求拿结果对原件）——卡上 `อำเภอ 151 / ตำบล 1082 /
   รหัสไปรษณีย์ 1084`，原件是 `ขลุง / ขลุง / 22110`（已在生产只读查证：DMS 主档 115 的
   省表属 จันทบุรี，级联查得 151=ขลุง、1082=ขลุง、1084=22110）。
   原因：`display_diffs`/`geo_name` 只在**新地址那一支**的选项表里反查（省表是全量所以省没露馅）。
3. **登录 DMS 报"这个 OA 还没启用"**（图三）——服务器访问日志：`20:48:53` 打开
   `?action=portal&openExternalBrowser=1`，UA 是 Chrome(CriOS)，随后
   `account.api.authenticate` → **401** → 页面显示 `dms.edit.liffUnavailable`。
   原因：入口把 `openExternalBrowser=1` 加在**LIFF 页本身**上，LINE 于是把我们的 LIFF 页丢进
   系统浏览器 —— 那里没有 LIFF、拿不到 id_token。老产品是"先在 LINE 内换票、再带票出站"。
4. **编辑页首屏 12.3 秒、且不复用**（实测：紧接着再读一次仍 12.8 秒）；订车主档快照热时 0.0 秒。
   老产品口径（`docs/deployment/MIGRATION_STATUS.md:202`）：会话 90 秒、后台 45 秒预热、
   12 小时兜底 + 写前权威复核。
5. **后台预热没搬**：`adapter/master_shared.py` 的 `needs_refresh()`/`warm_cached_paints()`
   都在，但**全仓无调用**，scheduler 也没有这一项 —— 冷启动的直接原因。

### 改动

| 文件 | 改动 |
| --- | --- |
| `line/cards.py` | `diff_card` 的 [แก้ไข] 改回 `_edit_button(nonce)`（深链，一次点击进编辑页） |
| `intake/service.py` | 差异卡额外并进**主档那一支**的县/镇/邮编级联（`_merge_geo_rows`，按 id 去重，同支不重复取） |
| `menu/links.py` | 门户入口改走 LIFF（去掉 `openExternalBrowser=1`），"出站"交给页面 |
| `adapter/warm.py`（新） | 主档后台预热：每分钟只查新鲜度，超过 45 秒才派发刷新 + 颜色预热（口径照老产品 `dms_master_refresh.sweep`），只刷**共享范围** |
| `hooks.py` | 注册 `* * * * *` → `pearnly_dms.adapter.warm.sweep` |
| `editor/edit_snapshot.py` | 主档与预热颜色改从**共享热快照**取（拿不到才退回现场抓全量）；客户现值与地址级联仍实时读；写前复核不变 |
| `frontend/src/lib/liff.ts`（新） | LIFF SDK 装载/初始化/带票出站，编辑页与账号页共用 |
| `frontend/src/pages/dms/AccountPage.vue` | 门户改"LINE 内验身份 → 换票 → `openWindow({external:true})` 带票出站" |
| `docs/development/architecture-policy.json` | `editor` 允许依赖 `adapter`（编辑页读共享主档快照；架构检查先拦下、复核后登记） |

### 本地验证

| 检查 | 结果 |
| --- | --- |
| `python3 scripts/check-architecture.py` | PASS（0 违规；`masters→adapter` 的反向依赖已按规则改到 `adapter/warm.py`） |
| `python3 tests/test_architecture_guard.py` | 9 OK |
| `python3 scripts/check-dms-offline.py` | 全 PASS（booking-foundation 45 / payments 42 / readback 43 …） |
| 前端 `vue-tsc --noEmit` / `vite build` | 0 错 / 构建通过（AccountPage 产物 hash 已变） |
| `python3 scripts/check-dms-parity.py` | 复核 `cards.py` 差异（118→116 行）后 `--update`，现"没有漂移" |

**未做**：镜像构建、部署、真机验收。部署后要真机验：①差异卡的 [แก้ไข] 一次点击进编辑页；
②差异卡旧值显示 ขลุง/ขลุง/22110 而不是 151/1082/1084；③菜单3 在 LINE 内一次点击后跳到系统浏览器；
④编辑页首屏时间（预期从 12.3 秒降到数秒级，且后台预热生效后更稳）。

### 已部署（2026-09-21，用户要求合并部署）

- `main` = `75ac5b2`；镜像 `sha256:ab5ea30091d3…`；7 个 DMS 容器已切到该镜像，维护模式已关、调度已开。
- **部署中发现并修掉我自己的一个 bug**：`warm.py` 从 `masters/` 挪到 `adapter/` 后，
  排队字符串仍写 `pearnly_dms.masters.warm.refresh` —— scheduler 按新路径注册成功、所以
  看起来正常，但每次刷新都在 worker 里 `ModuleNotFoundError`（只有运行到那一步才发现）。
  已修，并新增 `tests/test_queue_targets.py`：**所有交给 enqueue/get_attr/run_guarded 的
  点分路径字符串必须在仓库里有文件、且定义了该属性**（故意不 import，dev CI 没有 frappe；
  先反向验证过"塞回旧路径必 FAIL"），已接进 Development checks。
- 另有一条构建失败也在这轮修掉：卡片契约守卫仍按"编辑按钮是 postback"断言，
  已改成断言它是**深链**（uri + `editor=customer`），正好把这次的回归钉住。

### 部署后实测（生产只读）

| 项 | 迁移前 | 第一次部署后 | 本次部署后 |
| --- | --- | --- | --- |
| 编辑页首屏权威读 | 12.3s / 12.8s（不复用） | 12.3s | **10.0s / 8.8s** |
| 共享主档快照 | 冷启动需现场全量 | 12.3s | **0.01s（预热命中）** |
| 后台预热任务 | 无（`needs_refresh` 无调用） | 无 | **每分钟检查、过期才刷新；两个连接 Job OK** |

逐段拆解（同一次登录内实测）：起浏览器 2.1s + 登录 1.5s + 读客户 0.6s + 登出关浏览器 0.4s = **4.6s**；
`run_logged_in` 空跑仍要 **6.5s**（比裸流程多约 2s，在锁/客户端构造/收尾检查里）。
**结论：编辑页剩下的时间主要是"每个请求一次真实 DMS 登录（含登出）"这个既有架构**，
不是映射问题 —— 老产品同一端点实测 5.2s，也是同一取舍。主档那部分已经不再是瓶颈。

下一步若还要更快，只有两条路（需用户拍板，未做）：
（a）在一次编辑会话内**复用**一个已登录的 DMS 会话（会触碰"退出即登出"的铁律，要有租约与上限）；
（b）把 `run_logged_in` 里多出来的约 2s 收掉（先定位是哪一段）。

## 2026-09-21 本机收尾（DMS 残留清理）

用户要求把 DMS 残留、工作树、本机容器（除管理后台）全部释放。实际执行：

- **删除（DMS 残留）**：`pearnly-dms-line-lab-*` 12 个容器 + 19 个卷 + `pearnly-dms-line-lab_default` 网络 +
  `~/.config/pearnly-dms-line-lab` + 悬空 keycloak 镜像（756MB）。
  **注意**：lab 用的镜像与管理后台是同一张（`pearnly/frappe-sample:16.34.1` = `90c1d545d215`），已核对引用后保留。
- **工作树**：`codex/dms-line-diagnosis`（323M）与 `codex/ssdnodes-migration`（18M）**先确认已并入 main** 再 `git worktree remove`；
  `codex/cowork-backend-migration`（未并入、有未提交改动）与 `codex/auth-flow-fixes`（另一窗口）保留。
- **停掉+删掉的其它 lab**：`pearnly-auth-preview-*` 7 个、`pearnly-cowork-backend-lab-*` 3 个（含数据卷）。
- **备份**：`~/.config/pearnly-migration` 14GB（9-05 的 mirror/legacy tar/venv + 9-18 的 sql）按用户要求删除并清空废纸篓。
  `~/.config/pearnly/backups`（2.8M，控制面自己写的备份）保留。
- **Colima VM**：内存 12GiB → **6GiB**（`colima stop && colima start --memory 6`）。管理后台在其中，
  重启后需手动起 `pearnly-sample-mariadb-1` 与两个 redis（三个都是 `restart: no`；其余 6 个是 `unless-stopped` 会自启）。
  实测：VM 常驻内存 **21.7GB → 3.0GB**，VM 内用量 0.9GB/6GiB；控制面真查库通过；`runner_bridge` 探针返回 `[]`（开户链路正常）。
- **保留运行**：管理后台 9 个容器、`com.pearnly.production-provisioner` / `provision-runner`（开户要用）、
  `com.pearnly.production-tunnel`（KeepAlive，现监听 127.0.0.1:18180/18184；日志里的连接失败/退出码 255 都是主机迁移与本次重启窗口内的旧记录）。
- 顺带发现：本机 **没有** 残留的 Playwright/Chrome 僵尸进程；`~/.Trash` 受 macOS TCC 保护，
  只能通过 Finder 清空（已用 `osascript` 清空，Finder 回报 0 项）。
