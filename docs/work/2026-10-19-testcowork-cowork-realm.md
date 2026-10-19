# 2026-10-19 testcowork 改到 Cowork 号池（Keycloak realm `pearnly-cowork`）

状态：**10-20 已在生产切换完成**（随 23471f87 部署后，泰国时间 04:50–05:00）：R1–R4 只读核对全符合、S1 备份、S2 搬池、S3 执行器跑完、S4–S6 回读符合，
站点签 `pearnly-cowork`、LINE 绑定 `linked`；过程与回读见 `docs/work/2026-10-20-release-23471f87.md`。待用户：网页、测试机与录制机 CC 重新登录一次，
123@123.com 的密码由管理员在「团队管理」重设；业务池旧号不删（U4 一周后再定）。

## 1. 现状（只读代码与记录推断；标 ★ 的部署时要在生产只读核对）

| 项 | 现状 | 依据 |
| --- | --- | --- |
| erpcowork | 签到 `pearnly-cowork` | 10-18 成员身份记录（10-04 生产查实） |
| testcowork | 签到业务池 `pearnly-business`（09-26 拆池前开的户；后来 `set_oidc` 的校验只认产品自己的池，所以它现在连 `set_oidc` 都重跑不了） | 同上；`site-step.py` 旧 `set_oidc` |
| `pearnly-cowork` 在生产已建 ★ | 已建：erpcowork 签在那里；甲骨文守望每 5 分钟探它的 openid 配置；10-04 回读 Cowork 池有 `pearnly-line-*` 专用身份 | `infrastructure/oracle-watch/targets.json`、10-04 LINE 登录记录 |
| Cowork 池里的客户端 ★ | 推断有：`pearnly-control`（开户管理，`bootstrap-auth.py --realm pearnly-cowork`）、`pearnly-admin` / `pearnly-password-check`（开户目标上的 Cowork 池那组）、`pearnly-member-identity`（成员服务）、`pearnly-ai`（fatcc.com 入口，两个池各一个）、`erpcowork.pearnly.com`；**没有** `testcowork.pearnly.com` | 各配置脚本；`configure-ai-entry.py` |
| Cowork 池的身份提供者 ★ | 推断只有隐藏的 `line-2010411313`（全平台一个 LINE Login 频道），池级设置（拒绝未关联首登、登录来源作用域、改密前重新验证）已由 erpcowork 的 `set_line` 配好；LINE 后台已登记 Cowork 池回调（用户 09-26） | `site-step.py line_login()`、记忆「Cowork 号池与 LINE 登录」 |
| `LINE_LOGIN_PRODUCTS` | **已含 cowork**（09-30 起），不用再改 | `provisioning.py` |
| testcowork 的 LINE 入口 | `set_line` 按产品组配账号池，testcowork 早就把 `line-2010411313` 配在 Cowork 池；它现在登录用的是业务池里同名的那个入口（ERP 配的） | `site-step.py` `REALMS[group]` |
| 测试账号 | 开户管理员 `pearnlyai@gmail.com`（显示名 testcowork）：测试机 CC、内置浏览器、用户 Chrome 用；团队成员 `123@123.com`：录制机 CC、A14 手机用（10-18 加的，控制面登记记 `realm = pearnly-business`）★ 有没有别的号 | 测试窗口 10-18 晚交接、10-18 成员身份记录 |
| fatcc.com 登记 ★ | testcowork 的「租户站点」登记签发方是业务池（10-11 按开户实际建号的池补登记） | `identity_pools.site_pool`、`assistant_entry.backfill` |

## 2. 切换后受影响的地方

| 入口 | 影响 | 处理 |
| --- | --- | --- |
| 站点登录（网页、CC 对话页） | 站点改认 Cowork 池签发的身份；已登录的站点会话照旧有效到过期；之后登录跳 Cowork 池的登录页，要输一次密码 | 管理员与成员在 Cowork 池里要有号（第 3 节 S3） |
| fatcc.com 入口 | 浏览器记着上次的业务池：第一次打开先静默进业务池 → 找不到 testcowork → 显示「没有可用的工作空间 / 返回」；点返回重填账号就去 Cowork 池 | 一次性；★ 若 `pearnlyai@gmail.com` 在业务池还开着别的公司，入口总按记住的那个池进，testcowork 就从入口里看不到（见风险 R1） |
| CC 桌面版 | 配对、电脑登记、密钥按站点本地账号（邮箱）记，**不受影响**；窗口里的网页要重新登录一次（同上） | 测试机（pearnlyai）、录制机（123@123.com）各登一次 |
| LINE 免密登录 | LINE 关联挂在旧池的身份上：切换时站点把生效的 LINE 绑定改回待同步，每小时对账在 Cowork 池重挂；这一小时内 LINE 免密会被拒、显示「请用密码登录」 | 想马上好就手动跑一次对账（S6） |
| 团队管理 | 成员的号搬进 Cowork 池时**不带密码**；老板在团队管理里给成员重设密码后成员才能登 | 用户自己在页面上设（S7） |
| 识别、推送、Express、定时任务、计费 | 不受影响（都按站点本地账号与租户记） | — |
| 同组其他租户（erpcowork） | 不重启 Cowork 产品，不受影响 | — |

## 3. 切换步骤

### 前提

1. 本分支随下一批部署上线：计费站点（`pearnly_control` 的成员服务与管理通道，随 `activate-foundation.sh`）、部署工具同步
   （`site-step.py`）、本机管理后台跟上 main（`account/repool.py`）。产品镜像不用为它单独激活。
2. testcowork 切在线上（测试机 lab 已 `lab-stop`），切换时段里没人用 testcowork 跑测试。
3. 用户同意动生产身份（第 5 节 U1–U3 定好）。

### R：只读核对（一次 SSH 做完，不循环探测）

| 编号 | 在哪 | 命令 | 期望 / 不符就停 |
| --- | --- | --- | --- |
| R1 | 生产主机 | `python3 /opt/pearnly/deploy/scripts/production/inspect-identity-pools.py --site testcowork.pearnly.com --site erpcowork.pearnly.com --email pearnlyai@gmail.com --email 123@123.com` | 两个池都在；Cowork 池：`reset_password_allowed`、`login_with_email` 为真、`smtp_configured` 真、`login_theme` 与业务池相同、`line_realm_settings: ok`、`identity_providers` 只有 `line-2010411313`、`erpcowork` 客户端在、`testcowork` 客户端不在、两个邮箱在 Cowork 池都是 0（不是 0 就停：绝不按邮箱认领） |
| R2 | 生产 cowork-web | `bench --site testcowork.pearnly.com execute frappe.get_all --kwargs "{'doctype': 'Pearnly Identity', 'filters': {'status': 'active'}, 'fields': ['user', 'issuer']}"`，再查 `Pearnly LINE Binding`（`status`、`login_link`、`user`） | 有身份的人只有管理员和控制面登记过的成员；多出来的人（登记表之前建的）搬不过去，先停下问用户 |
| R3 | 生产 billing-web | `bench --site billing.internal execute frappe.get_all` 查 `Pearnly Member Identity`（testcowork 租户）、`Pearnly LINE Login Link`（同租户）、`Pearnly Tenant Site`（`owner_email` = `pearnlyai@gmail.com`，以及成员所在租户） | 记下行数；`pearnlyai@gmail.com` 在业务池还有别的站点 → 风险 R1，问用户 |
| R4 | 本机管理后台 | `docker --context colima-pearnly exec pearnly-sample-control-bench-1 bench --site control.localhost execute pearnly_control.account.repool.plan --kwargs "{'deployment': '<testcowork 的 DEP-…>'}"` | `current_pool: business`、`owner.blocked: false`、`owner.initial_password_kept` 看 U1、成员都是 `create`、要排的步骤清单；全程只读（计费服务 dry_run） |

### S：切换

| 编号 | 做什么 | 用什么 | 回读 |
| --- | --- | --- | --- |
| S1 | testcowork 整站备份（含 `site_config.json`） | 与产品激活前同一种站点备份 | 备份文件名记进本记录 |
| S2 | 搬池 | `bench --site control.localhost execute pearnly_control.account.repool.move --kwargs "{'deployment': 'DEP-…', 'pool': 'cowork', 'confirm': 'testcowork.pearnly.com', 'owner_password': '<U1>'}"`：Cowork 池建 `testcowork.pearnly.com` 客户端（回调、退出跳转、后端退出同开户）→ 建管理员号（发号单记 `cowork_identity_subject`）→ 计费服务把成员放进 Cowork 池（不带密码）、旧池 LINE 关联记录删掉、fatcc.com 登记与管理员身份改成 Cowork 池 → 开户计划上排 `set_oidc`、`bind_user`、每个成员一步 `bind_user`（只绑不建） | 返回值里 `members`（created / adopted）、`queued`、`line_links_released` |
| S3 | 跑排好的站点步骤 | 本机开户执行器 `scripts/production/provision-runner.py`（常驻就自动跑；否则 `--once` 跑到没有待办）；`set_oidc` 发现签发方变了会把生效的 LINE 绑定改回待同步，并在站点记下来源池 `pearnly_oidc_origin_issuer` | 开户计划里这几步 `done`；R1 再跑一次：Cowork 池有 `testcowork` 客户端、两个邮箱各 1 |
| S4 | 站点配置回读 | cowork-web 里 `env/bin/python -c "import json; c = json.load(open('sites/testcowork.pearnly.com/site_config.json')); print({k: c.get(k) for k in ('pearnly_oidc_issuer', 'pearnly_oidc_origin_issuer', 'pearnly_oidc_client_id')})"`（只取这三项，不打印密钥） | `…/realms/pearnly-cowork`、来源池是业务池；`Pearnly Identity` 多出管理员与成员在新签发方下的两行，旧行还在 |
| S5 | 外部回读 | 不带会话访问 `https://testcowork.pearnly.com/api/method/pearnly_core.identity.begin_login`，看 302 去 `auth.pearnly.com/realms/pearnly-cowork/…`、`client_id=testcowork.pearnly.com` | 不是 Cowork 池 → 配置缓存还没过（1–2 分钟）或步骤没跑 |
| S6 | LINE 关联马上对齐（可选，不做就等每小时对账） | `bench --site testcowork.pearnly.com execute pearnly_core.account.line_login.reconcile` | 绑定 `login_link = linked`；计费服务 `Pearnly LINE Login Link` 里 testcowork 的行 `realm = pearnly-cowork` |
| S7 | 测试账号准备（用户做） | 见下 | — |
| S8 | 验收（用户手机 / 测试机） | 见第 4 节 | — |

**测试账号怎么准备（密码一律不由我们输入）**

- `pearnlyai@gmail.com`（管理员）：U1 选「初始密码」时，Cowork 池里的号用开户单上 testcowork 开户时的初始密码（与发号时一样由系统带过去），
  用户用它登录；不记得或选「不设密码」时，用户在 Cowork 池登录页点「忘记密码」，用 Gmail 收信自己设。
- `123@123.com`（成员）：搬过去的号没有密码。用户用管理员登录 testcowork →「团队管理」→ 这个成员「重设密码」自己填，
  然后录制机 CC、A14 手机用新密码各登一次。
- 业务池里原来的两个号不删（回退要用）；之后要不要删见 U4。

### 回退

`repool.move(..., pool='business', confirm='testcowork.pearnly.com')`：业务池里原来的客户端、号、站点上旧签发方的身份绑定都在，
所以回退只是把站点签发方改回业务池（控制面只认本工具搬过的站点，站点上 `set_oidc` 也只在记着来源池是业务池时才放行）、成员登记按归属证明找回原来的号、
fatcc.com 登记改回业务池，再删掉这次在 Cowork 池建的管理员号（只按发号单记着的那一个）。成员在 Cowork 池的号留着不影响，
再搬时按归属证明认回来。最坏情况用 S1 的备份恢复站点配置。

### 测试机 lab（导入的 testcowork）

lab 那份是导入时的副本：签业务池、用业务池那个客户端的密钥、账本副本里成员也记在业务池。业务池那些都不删，所以**不同步 lab 也照样能登**，
只是测的是搬之前的身份。建议下一次要在 lab 上测登录 / LINE / 团队管理时 `PEARNLY_TESTENV_REFRESH=1 lab-start cowork`
重新导入（会丢掉 lab 上自己造的数据）；只测别的功能不用刷新（U3）。

## 4. 验收项（给测试窗口）

| 项 | 谁做 | 期望 |
| --- | --- | --- |
| 网页登录 testcowork（pearnlyai） | 测试机内置浏览器 | 跳 Cowork 池登录页，登进去是原来的工作台、数据都在 |
| fatcc.com 进 CC | 测试机 | 第一次可能先「没有可用的工作空间」，返回重填账号后进 testcowork 的 /assistant；第二次直接进 |
| CC 桌面版 | 测试机（pearnlyai）、录制机（123@123.com） | 重新登录一次；配对、电脑列表、定时任务照旧，不用重新连接 |
| 团队管理重设成员密码 | 用户 | 成功；123@123.com 用新密码能登，手机待处理页照旧 |
| LINE 免密（菜单「库存盘点 / 任务管理」） | **用户手机** | 对账后免密进 testcowork |
| 改密入口、退出 | 测试机 | 账户页是 Cowork 池的；退出回到登录 / fatcc.com |
| erpcowork、testerp | 外部回读 | 不受影响（ping 200、登录跳各自原来的池） |

## 5. 风险与要用户定的

风险：

- **R1 同一邮箱在两个池**：fatcc.com 只进记住的那个池。若 R3 查到 `pearnlyai@gmail.com` 在业务池还有别的公司，搬过之后从入口进不到 testcowork
  （直接开 `testcowork.pearnly.com/assistant` 不受影响）。入口改成跨池列公司是另一项改动，这次没做。
- **R2 没登记过的人**：站点上有身份、但既不是开户管理员也不在成员登记里的号（登记表上线前建的），搬不过去、搬后登不上。R2 有这种人就先停。
- **R3 切换窗口**：站点配置在进程里缓存 1–2 分钟（不重启，免得踢 erpcowork），这期间个别登录会报签发方不对，重试即可。
- **R4 LINE 免密**：对账前最多一小时用不了，密码登录不受影响。
- **R5 登录页样式**：Cowork 池的登录主题若与业务池不同（R1 看 `login_theme`），用户看到的登录页会不一样；登录页冻结，不改，只报告。
- **R6 lab 不一致**：见第 3 节；刷新会丢 lab 数据。

要用户定：

- **U1 管理员密码**：①（建议）用开户单上的初始密码，用户不用操作；② 不设密码，用户自己走「忘记密码」。
- **U2 什么时候切**：建议随下一批部署上线后、测试机 lab 空着时切，切完当场走第 4 节。
- **U3 lab 要不要刷新**：建议等下次要在 lab 上测登录相关时再刷新。
- **U4 旧号清理**：业务池里 testcowork 的客户端与两个旧号，稳定一周后再定删不删（若 R3 显示 `pearnlyai@gmail.com` 在业务池还有别的公司，那个号不能删）。

要「消息含义理解」窗口配合：

- 约切换时段（它管服务器与测试租户切 Claude 实测）；切换期间不在 testcowork 上跑测试，lab 保持停着。
- R1–R3 的只读核对、S1 备份、S4–S6 回读在生产上执行，谁执行先约好，一次 SSH 做完。
- 切完后它在测试机（pearnlyai）、录制机（123@123.com）上的 CC 要重新登录一次。

## 6. 代码改动（本分支）

| 层 | 改动 |
| --- | --- |
| 本机管理后台 `pearnly_control/account/repool.py`（新） | `plan`（只读）与 `move`（搬去 Cowork 池 / 回退），见文件头；拒绝：不是云端 Cowork 部署、租户不在用、`confirm` 不是站点域名、站点有步骤在跑、站点开户就在 Cowork 池（不搬，也不能「回退」）、回退的站点不是本工具搬过的（`set_oidc` 载荷没记 `moved_from`）、Cowork 池已有同邮箱而发号单没记着的号、计费服务拒绝；校验都在第一次写之前 |
| `provisioning._ensure_oidc_client` | 可选 `pool`：客户端建在指定池（默认仍是产品自己的池，开户行为不变） |
| 计费服务 `member_service.move_members` + 管理通道 `move_members` | 团队成员搬池（沿用 / 新建不带密码的号，登记改记新池与新身份，旧池号不删）；旧池 LINE 关联记录删掉、没人用的专用身份释放；`dry_run` 只读 |
| `member_provider.create` | `password=None` 时建不带密码的号 |
| `billing_remote.move_members` | 管理通道调用 |
| `site-step.py` | `set_oidc` 校验抽成纯函数 `oidc_config`：仍只认产品组自己的池，唯一例外是签在业务池的老 Cowork 站点（可留、可搬、搬后可回）；换池时记下来源池，并把生效的 LINE 绑定改回待同步；`bind_user` 带 `member` 时只给已有账号绑身份、不建号、不加角色 |
| `scripts/production/inspect-identity-pools.py`（新） | R1 只读核对，只发 GET、不打印身份编号与密钥 |
| 测试 | `scripts/production/test-site-step-oidc.py`（7 条，进 development.yml）、`test_member_service.TestMoveMembers`（6 条）、`test_repool`（9 条，进候选镜像离线检查） |

不改任何界面文案，不碰控制面发号页。

## 7. 验证

| 时间 | 代码 | 环境 | 场景 | 结果 |
| --- | --- | --- | --- | --- |
| 10-19 | 本分支 | 本机 Python 3.12 | `test-site-step-oidc.py` 与原有 4 个站点步骤 / 开户代理测试 | 通过 |
| 10-19 | `26130d31` | 本机 Python 3.12 | 全闸门 `gate.sh`（development.yml 同名步骤，含新加的 `test-site-step-oidc.py`）、`check-architecture.py`、`check-copy.py --report` | 107 步全过；架构 0 违规；文案相对起点新增 0、改动 0 |
| 10-19 | 本分支 | 生产镜像 `bcb5e240` + 本工作树五个 App 源码，断网 | `tests/test_cloud_onboarding.py`、`tests/test_teardown_members.py`、`tests/test_line_channel_setup.py` | 通过 |
| 10-19 | 本分支 | 生产镜像 `bcb5e240` + 本工作树五个 App 源码，断网 | `test_member_service`（43）、`test_repool`（9）、`test_identity_pools.TestPools`、`pearnly_erp_portal.team.test_identity` | 61 条通过 |

| 层级 | 状态 |
| --- | --- |
| 实现 | 完成（本分支） |
| 本地验证 | 见上表（没改 `pearnly_core`，没跑整套候选离线检查，只跑了受影响的模块） |
| 部署 | 未做（随下一批） |
| 生产切换 / 外部业务结果 / 用户验收 | 未做（第 3、4 节） |
