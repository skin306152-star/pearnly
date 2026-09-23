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
- 原有工作树、暂存与运行组不变。原 main 被本机管理后台挂载，验证前不改 main。
- 当前云端 DMS 源码 `7128a1e5decf9b79ee19e995c0bf9ead938e5a50`；私有计费源码 `56e249760d58df08ee23f72a06e727f5faa94600`。
- 旧 Cloud Run 已退役；本批不是新迁移，固定迁移来源仍为退役前 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188` 的证据。

## 已处理 / 进行中

1. ERP 与 Cowork 翻译追加冲突：保留两侧词条，按唯一源重新生成翻译；集成提交钩子通过。
2. ERP LINE：签名 webhook 的 Guest 上下文不能直接调用员工权限接口，修为有界绑定用户上下文并恢复；补销售权限与 owner 验证。
3. 云端员工创建/重置：租户通过已有内网连接和 scoped token 调用 Control account 的受限成员身份 RPC；仅私有后端保存专用身份服务密钥。
4. 生产宿主开户脚本需随同一镜像同步，避免旧脚本继续自动安装 ERPNext。

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
- 尚未推送、生产部署、真实开户或用户验收。完成后本节追加 SHA / digest / 回读。

## 首轮上线回读发现并修复的冷启动限额

首轮镜像源码 `e83516a422e638811416db036d38925edcbffe6a`、digest
`sha256:10e12e2d02d94e93251dc06fb7471677cce0002ecb7f2607a99bf499f158eb69` 的构建、暂存、
私有服务及 DMS 原生升级均成功；两个 DMS 登录 HTTPS 200，维护模式已关闭。
但发布回读发现 `dms-ocr-dispatch` 冷启动三次 `oom → die(exitCode=137)`，不能据页面能打开宣布成功。
Docker events 给出实际 OOM 证据；当前 cgroup `memory.peak=134217728`，旧限额 128 MiB。
隔离实测 dispatch/work 导入约 58.9 MiB、Site/hook/sweep 69.6 MiB、首次原生 prune 查询约 120.6 MiB，
128 MiB 不足以覆盖冷启动与文件页缓存。三个产品共用同一调度入口，统一改成 256 MiB，
不增加 worker、不改 OCR 并发或外部调用规则。容量检查增加三个产品下限断言，10 项及架构检查通过。
按不可变发布约定重新构建镜像，宿主部署副本必须和最终镜像相同；最终 SHA、digest 与稳定回读见后续追加。

## 交接保留

本批工作树与实验环境用于候选验证；验证、合并、部署完成后检查并清理本批独占资源。
ERP/Cowork 原树属于既有任务，当前仍保留，不以本次集成为由直接删除。
