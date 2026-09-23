# ERP / Cowork / DMS 统一发布与云端开户接线

## 当前目标与边界

用户授权修复其余发布阻塞、合并推送并部署；**盘点附件归属权限本批明确不处理**。
管理后台仍在本机，正式开户只给云端。不得给租户全局身份管理员密钥。
LINE 按实现、隔离验证、上线配置、真实消息验收分别报告；不主动发 LINE、付费 OCR 或创建真实 DMS 订单。
不新增 Cowork 前端或未授权 LINE 功能。

## 基线与归属

- 独立工作树：`/Users/skin/.codex/worktrees/unified-release-onboarding/pearnly`。
- 本批分支：`codex/unified-release-onboarding`。
- 集成前 main：`fba555e1b31936966196873f71a2b9407dde5d3a`。
- ERP：`6d31edc7ff6a0a306e31dbb7b3704ec9d797e574`，包含采购、盘点及 POS 分支。
- Cowork：`669e079368fc9adeaff4dbbcba03dcd6017fc856`。
- 双分支集成候选：`7d8634ad33c2720162ad8ff9c31db570a58386c6`。
- 开工时保留其它工作树、暂存与预览组。原 main 被本机管理后台挂载，先完成隔离验证，再停本次需更新的本机 Control/qERP 进程、备份、合并和迁移。
- 发布前云端 DMS 源码 `7128a1e5decf9b79ee19e995c0bf9ead938e5a50`；私有计费源码 `56e249760d58df08ee23f72a06e727f5faa94600`。
- 旧 Cloud Run 已退役；本批不是新迁移，固定迁移来源仍为退役前 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188` 的证据。

## 已处理

1. ERP 与 Cowork 翻译追加冲突：保留两侧词条，按唯一源重新生成翻译；集成提交钩子通过。
2. ERP LINE：签名 webhook 的 Guest 上下文不能直接调用员工权限接口，修为有界绑定用户上下文并恢复；补销售权限与 owner 验证。
3. 云端员工创建/重置：租户通过已有内网连接和 scoped token 调用 Control account 的受限成员身份 RPC；仅私有后端保存专用身份服务密钥。
4. 生产宿主开户脚本需随同一镜像同步，避免旧脚本继续自动安装 ERPNext。
5. ERP 的敏感创建/重置请求禁止进入原生 Recorder；完整原生录制验证包括成功、拒绝、GET、REST 别名，不存密码。
6. DMS reply token 的持久任务消费者参数同步；ERP LINE 失败文件保存使用 savepoint 回滚，图片/文件归属保持实际绑定员工。

## 身份边界

私有 `billing.internal` 复用运行载体，不把身份业务放入计费模块：仅允许
`POST /api/method/pearnly_control.account.member_api.execute`，该 API 自行验证租户、产品、动作。
Keycloak 专用客户端只管理用户，不管理客户端；归属属性 `pearnly_member_owner` 仅管理员可读写。
参考 [Keycloak 用户属性权限](https://www.keycloak.org/docs/latest/server_admin/) 与
[26.7.4 Admin API](https://www.keycloak.org/docs-api/26.7.4/rest-api/index.html)。
租户删除沿既有操作员确认流程：计划前预检全部员工归属并冻结云端员工变更；只按已核实 subject 删除，404 幂等，远端失败保留映射用于恢复。被其它租户发号单复用的身份先拒绝排拆站步骤，不能误删共享 realm 用户。删除验证只对实验室身份执行。

## 验证与发布状态

- 集成翻译生成、`git diff --check`、提交钩子架构检查：通过。
- ERP LINE 10 项无网络真实 Frappe 回归：通过，尚非真实 LINE 消息验收。
- LINE 手机盘点页 GET 参数、同源解码器、连续扫码/相机释放/幂等重试：5 组 Node 行为测试通过，非真实手机验收。
- DMS 新 reply token 入持久任务后，注册消费者仍为三参数，发现实际 TypeError；补可选参数透传，兼容既有无令牌任务。发布入口 4 组及运行链 36 项通过；保留真实注册消费者断言。
- 卡片错误文案测试修正为真实 `dms.err` 命名空间，直接核对四语字典，并验证缺英文不能被泰语回落掩盖。
- 架构 0 违规；DMS 离线 77/45/42/43/60/26；云端开户边界 17 项；发布脚本 7 项；员工离线 18 项；删除边界 3 项：通过。
- 翻译生成与前端词条检查通过；全仓中文常量严格检查仍为 **FAIL：498 条**，逐条字面量对比均已存在于合并基线 `7d8634a`，没有扩大忽略或修改这批既有常量。Cowork 前端尚未接通，不据此宣称全仓四语验收。
- 独占内部实验环境 `pearnly-release-lab`：四 App 站点原生新装/migrate通过，不安装 ERPNext。固定 Frappe 16.33.0 / Keycloak 26.7.4，无主机端口、无外网、无真实凭据。
- DMS 从线上源码 `7128a1e5` 原生升级：success/retrying/pending 三条记录共 16 字段逐项保留；Push Log 模块 Core→DMS，领域读取正常。
- DMS 原生数据库及公开/私有文件备份→修改→恢复：全部回读一致。
- 真实隔离身份链：用户名/邮箱登录、员工管理、无本地密码、超时恢复、跨租户/跨产品拒绝、并发幂等、只读身份字段、敏感请求不进 Recorder、删除失败恢复通过。
- 发布私有服务新增旧镜像备份和排空顺序，Docker 130 秒 / Gunicorn graceful-timeout 120 秒；失败不自动重开端点。
- 真实 ERP LINE 验证：两新 Site、11 个签名 Guest 事件，实际数据库/File/事件幂等回读；采购/销售确认与库存变更、方向权限、跨 Site、文件失败回滚、错误签名和停用渠道拒绝通过。0 次真实外部请求。
- 源码已合回 main 并推送；部署与资源收尾状态见下节。没有创建真实租户，没有真实消息/付费 OCR/外部 DMS 写单或用户验收。

## 首轮上线回读发现并修复的冷启动限额

首轮镜像源码 `e83516a422e638811416db036d38925edcbffe6a`、digest
`sha256:10e12e2d02d94e93251dc06fb7471677cce0002ecb7f2607a99bf499f158eb69` 的构建、暂存、
私有服务及 DMS 原生升级均成功；两个 DMS 登录 HTTPS 200，维护模式已关闭。
但发布回读发现 `dms-ocr-dispatch` 冷启动三次 `oom → die(exitCode=137)`，不能据页面能打开宣布成功。
Docker events 给出实际 OOM 证据；当前 cgroup `memory.peak=134217728`，旧限额 128 MiB。
隔离实测 dispatch/work 导入约 58.9 MiB、Site/hook/sweep 69.6 MiB、首次原生 prune 查询约 120.6 MiB，
128 MiB 不足以覆盖冷启动与文件页缓存。三个产品共用同一调度入口，统一改成 256 MiB，
不增加 worker、不改 OCR 并发或外部调用规则。容量检查增加三个产品下限断言，10 项及架构检查通过。
按不可变发布约定重新构建镜像，宿主部署副本和最终镜像逐文件核对一致；最终 SHA、digest 与稳定回读见下节。
256 MiB 真实冷启动复验（锁定 Frappe + 本批 5 App 源码 + 默认 entrypoint）首次 sweep/prune 成功：
cgroup 峰值 151.47 MiB、稳定 133.17 MiB，`max/oom/oom_kill=0`，无重启，退出 0；原 128 MiB 确实不足。
等待新镜像期间仅临时对生产 DMS dispatcher 使用 `docker update --memory 256m --memory-swap 512m`，
不扩大并发、不改业务代码；最终已由提交的 Compose 256 MiB 替代这项临时调整。

## 最终发布回读（2026-09-24）

- 镜像源码/主线业务版本：`6a13b98261ebe94c8a15a7899c8eee028ad65c0b`，已合回 main 并推送。后续本记录及 Cowork 入口的文档收尾提交不改应用或部署源码。
- 最终镜像：`ghcr.io/skin306152-star/pearnly/frappe@sha256:d60900539548cdd9e3f67a41b3ddcf7bed8e73455de5bb144c999fad5f00393e`。
- [构建 35920336586](https://github.com/skin306152-star/pearnly/actions/runs/35920336586) 与 [暂存 35920875115](https://github.com/skin306152-star/pearnly/actions/runs/35920875115) 成功。首轮构建/暂存是 35917883233 / 35918496035，不当成最终发布。
- Foundation 与 DMS 激活脚本均退出 0；私有服务及两个 DMS Site 备份/migrate 成功，维护模式关闭。
- `billing-web` 与 DMS 的 web/worker/ocr-worker/ocr-dispatch/scheduler/socketio/frontend 共 8 容器，运行镜像 digest 和标签源码均一致，重启数为 0、无 OOM；`active-image.json` 同步最终 digest，正式开户会用这一版。
- 生产 dispatcher 已由 Compose 持久采用 256 MiB，cgroup 冷启动峰值 143.64 MiB，`max/oom/oom_kill` 全 0，取代临时 docker update。最终容量预算 7424 MiB，宿主 32095 MiB，保留 3209 MiB。
- `ris.pearnly.com` / `testdms.pearnly.com` HTTPS `/login` 200、Keycloak OIDC discovery 200；两站只安装 Frappe/Core/DMS，Push Log 模块原生归属 DMS。HTTP 可访问不代替真实业务验收。
- 私有账号/计费边界检查通过；本机 Control 实际身份只读查询与计费请求成功，正式待执行任务 0。
- 原 `com.pearnly.production-provisioner` LaunchAgent 已恢复为 running，未新建第二套执行器；共享数据库、Redis、Keycloak 未替换实例。没有为测试新建真实租户或员工。
- 最终 DMS 镜像与两站 LINE 配置存在性回读通过。按 Site 曼谷时间 04:00 后的生产 OCR Work 只读元数据：ris 无新工作；testdms 两条（04:07:37、04:11:14）均 succeeded、各尝试 1 次、无错误码。没有读取票据内容/参数或重放；只证明工作流完成，不代表识别质量、第三方业务结果或用户验收。

## 管理后台与 LINE 的实际边界

- 本机管理后台、qERP 迁移成功并重启；新前端产物来自 CI 验证镜像，不使用旧分支构建。两站 `/login` HTTP 200。
- 管理后台正式目标 `production-ssdnodes`，受限 SSH 无效任务在执行前拒绝；实际私有身份查询与带管理凭据的计费只读请求通过。没有云端待执行任务。
- 正式员工身份服务已配置专用客户端和管理员归属属性；私有端点 GET/匿名 POST 拒绝。只读回读为 2 租户、2 钱包、2 充值申请，员工身份/操作表为 0；账本流水首次 13、最终私有服务切换后 15，不能把在线业务账本写成发布期间冻结不变。本批没有真实员工开户。
- 当前正式部署只有 DMS 两站，没有 ERP/Cowork 正式租户。ERP/Cowork 源码和原生开户流程进入统一镜像，不伪造一个生产租户来证明“已上线”。
- 旧 `qerp.localhost` 是本地样板身份/非私有本地 Control 配置，不自动转投云端；员工新增/重置不以它作为云端验收。现有数据、登录和权限保留，正式员工链以隔离真实身份服务验证为准。
- DMS：Control 与两生产站的 Channel ID/Secret/Token/LIFF 配置齐全、镜像模式关闭。两站各一次正确签名空 `events=[]` 返回 200，一次错误签名返回 400；没有事件/任务或外发，错误签名各留下 1 条预期诊断日志。未从 LINE 控制台复核外部 Webhook/菜单，不把本站接口通过写成真实消息验收。
- ERP：绑定、采购/销售图片和 PDF 收取、页面确认及正式开户 `set_line` 下发已实现。采购/销售用文字切方向；未新增卡片菜单。
- `/stocktake` 手机页、LIFF 票据和扫码已实现；ERP LINE 菜单发布尚未实现，也没有真实 ERP 官方号/LIFF 配置或手机验收。ERP LIFF 应指向本产品 `/stocktake`，不能沿用 DMS `/liff/dms-booking`。
- Cowork 没有新增 LINE 业务，继续遵守此前裁剪；盘点附件归属权限明确延期，未更改。

## 备份与恢复材料（保留，非临时垃圾）

- 生产发布前完整工具/运行配置/密钥/身份数据库备份：`/opt/pearnly/backups/unified-release-20260924.K5e3d5`，仅管理员可读。
- 原生 Site 数据库/公开与私有文件备份：billing `20260924_035550-billing_internal`、ris `20260924_040027-ris_pearnly_com`、testdms `20260924_040031-testdms_pearnly_com`，各自 Site `private/backups/`。
- 最终限额修正镜像切换前又保留原生备份：billing `20260924_041652-billing_internal`、ris `20260924_041929-ris_pearnly_com`、testdms `20260924_041931-testdms_pearnly_com`。
- 本机 Control 原生备份 `20260924_022151-control_localhost`、qERP `20260924_035152-qerp_localhost`，各自持久 Site 卷。
- 本机旧前端备份 `/Users/skin/.config/pearnly-production/frontend-backup-20260924.VhXf8C`。下一次用户确认新版本业务验收及恢复窗口结束后再评估保留期；本批不删除真实数据/凭据备份。
- 最终前端产物与镜像逐文件 SHA256 一致（8 文件）；此前目录中额外残留的 17 个旧构建文件逐个核对与发布前备份一致后移至系统废纸篓，备份也保留，可以恢复。

## 交接保留

本批工作树与实验环境用于候选验证；验证、合并、部署完成后只清理本批独占资源，实际清理结果见本节末。
ERP/Cowork 原树属于既有任务，当前仍保留，不以本次集成为由直接删除。

只读核实：下列 5 个旧分支的提交均已包含于本地主线/远端 main，各树 tracked/staged/untracked 干净，远端旧任务分支均不存在。
不再以“尚未合并”为保留理由；实际保留原因如下。默认 Docker context 不可连接，已审计两个可用 context 的运行及停止容器。

| 工作树 / 本地分支（均在 `/Users/skin/.codex/worktrees/<名称>/pearnly`） | 所属任务、保留原因与解除条件 |
| --- | --- |
| `cowork-backend-migration` / `codex/cowork-backend-migration` | Cowork 后端实验：停止的 `pearnly-cowork-backend-lab-bench-1` 仍有 7 个 bind。下一次 Cowork 前端接入时复核，退役/改挂载并保留实验数据后再删树。32 个缓存目录随树保留。 |
| `erp-pos-migration` / `codex/erp-pos-migration` | ERP POS 开户实验：运行的 `pearnly-erp-pos-provision-lab-maintenance-1` 有 3 个实际源码/脚本 bind。由该实验退役或转挂载后复核。 |
| `erp-purchase-records` / `codex/erp-purchase-records` | 采购原挂载回滚点：无 ignored、无实际 bind/进程占用；但旧约定保留回滚点，DB/Redis 已交由 sales 使用。由销售预览任务确认统一版本已接替回滚点时复核，只删源码，不停共享 DB/Redis。 |
| `erp-sales-records` / `codex/erp-sales-records` | ERP 销售预览：8147 的 frontend/socketio/web/bench 4 容器仍各有 7 个 bind；用户要求保留预览。构建产物/node_modules 也在用，移交预览后复核。 |
| `stocktake-workbench` / `codex/stocktake-workbench` | 盘点预览：独立 context 有 13 个运行容器及本 context 停止 lab 引用；用户明确保留 8136/8126 与样板数据。解除预览要求、替换源码与主题/配置挂载后复核。 |

生产旧镜像及首轮候选镜像与上述备份一并保留作为发布恢复检查点；用户业务验收及恢复窗口结束后再按实际引用评估，不在本轮删除发布恢复材料。

### 本批独占资源收尾

- 已删除 `pearnly-release-lab` 的 4 容器、3 命名卷、2 匿名卷和内部网络；删除前逐一核对没有其它容器使用。只有本批虚构租户/实验数据，不能从已删卷恢复，可按保留的脚本重建；未删任何生产、旧样板或预览数据。
- 镜像提取临时容器均已删除；`/tmp/pearnly-release-artifact.Z6b8y9` 与 17 个旧前端构建文件已移至系统废纸篓，可恢复。生产镜像/备份和本机正式前端产物继续保留。
- 两个可用 Docker context 已确认没有容器再挂载本批工作树；工作树无 ignored、无符号链接，只有本批收尾文档待提交。
- 本批 `codex/unified-release-onboarding` 及同名工作树只用于提交这份收尾记录，不作为下一轮入口；该提交合回 main、推送并回读后，按远端精确 SHA lease 删除任务远端分支，再非强制删除本地工作树和分支。最后删除结果及含本记录的 main SHA 随本次最终交付回读，不另造一份状态卡。
- 其余 5 个旧任务资源按上表**交接保留**；没有笼统宣称全仓/全部预览已清理。
