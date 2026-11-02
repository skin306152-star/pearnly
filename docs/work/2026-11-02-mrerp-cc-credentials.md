# 施工记录：MR.ERP 登录在 CC 桌面版里授权（评估 §13 第 10 步，#9 定 (b)）

- 分支 `pearnly/mrerp-cc-credentials`，工作树 `.claude/worktrees/mrerp-cc-credentials`，基线 `9b3f66e4`（= origin/main，11-02）。
- 依据：[评估](2026-11-02-mrerp-operator-and-erp-company-评估.md) §12 第 9 条、§12.1「#9 定 (b)」、§13 第 10 步、§14.4（改写在分支
  `pearnly/mrerp-cc-auth-docs` 提交 `3edc649b`，本记录施工时还没进 main）；ADR-026 第 7、8 条（同一分支）。
- 不在本步：CC 端 MR.ERP 执行层（第 2 步，`mrerp_operator/`、`host.py`）、代录批次怎么在领单时租 / 续 / 放账号（第 3 步，调本步的
  租约函数）、ERP 的 MR.ERP 连接（第 6 步，接本步备好的共用接口与产品钩子）。不连真实 MR.ERP。

## 1. 结果

网页不再收 MR.ERP 密码。用户在 CC 桌面版原生小窗输一次，同一次输入存两处：这台电脑的 Windows 凭据管理器（代录用）、站点连接原有的
Password 字段（直写、目录采集、主档 / 菜单读取用）。站点收下了 CC 才存本机；站点从不把密码回给页面、也不下发给任何电脑。
老连接已存的密码照用，不迁移；在 CC 里重新授权就覆盖。同账号互斥：代录批次的账号租约与直写导入的短锁是同一把锁。

## 2. 流程与接口

```
对话页（CC 窗口里，Frappe 会话）                 站点                                  CC 桌面版（托盘进程）
 点「在 CC 桌面版中授权」
  ──POST mrerp_login_ticket {connection, device_key}──►  核本人能改这条连接、认出这台电脑
  ◄──────────────── {ok, ticket}（一次性，5 分钟）───────  Redis 只存票的摘要 → {站点, 通道, 连接, 人, 电脑}
  ──桥 mrerp_authorize {ticket}─────────────────────────────────────────────────────►  核对话页站点 = 这台电脑连着的成员
                                                                                      弹原生小窗收用户名、密码
                                                       ◄── POST mrerp_login {ticket, username, password}（机器令牌）
                                                       取出并作废票；核同站点、同通道、同一台电脑；
                                                       按发票的人此刻的权限再核；写 Password 字段与授权记录；
                                                       电脑行记下这条连接号
                                                       ──► {ok, connection, system_url, authorized{username, device, at}}
                                                                                      站点收下了才存凭据管理器
  ◄────────────────────────────────────────────────── {ok} / {ok:false, code} / {cancelled}（不含密码）
```

| 接口 | 身份 | 位置 |
|---|---|---|
| `POST pearnly_cowork.companion.api.mrerp_login_ticket {connection, device_key}` | Frappe 会话 + CSRF | Cowork：`companion/api.py` → `credentials.login_ticket` |
| `POST pearnly_core.cc_desktop.api.mrerp_login_ticket {connection, device_key}` | Frappe 会话 + CSRF | 共用通道（ERP）：`cc_desktop/api.py`；产品没接回 `{ok:false, code:"unsupported"}` |
| `POST pearnly_cowork.companion.credential_api.mrerp_login {ticket, username, password}` | 只认 `exp_` 机器令牌的精确 POST（`http_auth.LOGIN_METHODS`，第五张表；MR.ERP 只读文件夹密钥也放行） | Cowork |
| `POST pearnly_core.cc_desktop.machine.mrerp_login {ticket, username, password}` | 只认 `pcc_` 机器令牌的精确 POST（`http_auth.METHODS`） | 共用通道；产品经 `pearnly_cc_desktop` 的 `mrerp_login_allowed` / `mrerp_login_store` 接，没接回 `unsupported` |
| 桥 `mrerp_authorize {ticket}`（能力 `mrerp_login`） | 只在本站对话页（`allowed`），对话页站点必须是这台电脑连着的成员 | `companion/src/companion/tomo_bridge.py` |

- 一次性票：`pearnly_core/cc_desktop/login_tickets.py`（多产品共用）。票 `mrl_` + 32 字节随机数；Redis（不淘汰的状态存储）只存票的
  SHA-256 摘要做键，值是不含秘密的绑定；取出即删（一条 Lua）；本地事务撞 1020 / 1213 重来时不再取票。
- 机器回复的码：`ticket_invalid`（票过期 / 用过 / 不是这台电脑的）、`denied`（此刻没权限）、`login_invalid`（报文不对）、`unsupported`
  （产品没接）。意外出错一律 503「忙」，不带内部信息与堆栈；密码一读完就从 `form_dict` 与绑定的请求体副本里拿掉（`http_auth.drop_secret`）。
- CC 一侧的码（只回页面，不显示原文）：`not_connected`（对话页站点不是这台电脑连着的）、`network`、`unauthorized`（密钥站点不认）、
  `store_failed`（站点收下了、本机存不进去）；取消回 `cancelled`。页面一律显示「授权失败」。

## 3. 存储

| 在哪 | 存什么 | 说明 |
|---|---|---|
| 站点 `Cowork ERP Connection` | `credential_username` / `credential_password`（原有的 Password 字段，Frappe 加密存库） | 收下时直接覆盖，**不升** `record_revision`（同 `set_members`：换登录不让目录、菜单与默认值、排队的单失效） |
| 同上，新加三列（隐藏、只读） | `credential_authorized_by`（谁）、`credential_device`（哪台电脑的名字）、`credential_authorized_at`（UTC） | 页面「已授权：用户名 · 电脑 · 时间」；老连接只有用户名 |
| `Cowork CC Desktop Device` / `Pearnly CC Device` 新加一列（隐藏） | `mrerp_logins`：这台电脑已授权的 MR.ERP 连接号（逗号分隔，最多 50 条） | 收下授权时记一条；心跳 `mrerp_logins` 整份报（旧版不报不动，报空照存）；只有连接号、不报账号 |
| CC 电脑的凭据管理器 | 目标名 `Pearnly CC/MR.ERP/<MR.ERP 主机>/<连接号>` → (用户名, 密码)，`CRED_PERSIST_LOCAL_MACHINE`（DPAPI）；备注（不是秘密）写授权的 Pearnly 站点 `Pearnly CC|<站点域名>` | `express_operator/credentials.py`；目标名与第 2 步 `mrerp_operator.session.credential_target` 同一个（主会话 11-02 补充）；主机取站点回的 MR.ERP 网址，不信页面；心跳与断开按备注认站点，断开某个站点（这台电脑上不再有它的成员）时一并删 |

连接视图（`connections.service._dto`，`detail` / `records` 等）多一项 `credential: {username, device, at}`（MR.ERP 连接、有用户名时）；
从不含密码。三个 DocType 改动走原生迁移（加列），不写补丁、不迁旧数据。

## 4. 权限（服务端判，主会话 11-02 定）

| 谁 | 票的范围 | 收下时 | 本机 |
|---|---|---|---|
| 能**管理**这条连接的人（沿用原来能改连接密码的判据：Cowork Admin + 主档权益，同 `configure`） | `site` | 写进连接的 Password 字段与授权记录（直写用、影响整条连接），回 `written: true` | 站点收下后再存 |
| 这条连接**使用名单**上、能用它的成员（同 `access.require_connection`） | `local` | **不写站点**，只核权限、回 MR.ERP 网址，`written: false` | 只存自己这台电脑（给自己代录用） |
| 别人 | 拿不到票 | 交来一律 `denied` | — |

- 范围由服务端按人定（`connections.mrerp_login.scope_of`；共用通道经产品钩子 `mrerp_login_scope`），页面要什么都不算。
- 收下时按发票的人**此刻**的权限再算一次：票是 `site` 而此刻已不是管理员 → `denied`、什么都不写（不降成只存本机）；此刻已不在名单上 → `denied`。
- `connections.mrerp_login.store(..., scope)` 自己也核：`scope="site"` 而调用人不是管理员直接按无权拒，不能只靠前端隐藏。
- 页面：管理员授权后上面那行「已授权：用户名 · 电脑 · 时间」跟着变（站点的）；成员授权后站点那行不变，下面多一行同样写法的
  「已授权：用户名 · 这台电脑 · 时间」（本机的）。沿用已有新增词条，没有再加文案。
- 票里记着范围，Redis 里的值仍不含秘密。

## 5. 网页改动（两产品同一组件）

- 共用组件 `frontend/assistant/src/components/mrerp-login.tsx`（`MrErpLogin`）：显示「已授权：用户名 · 电脑 · 时间」或「未授权」，
  按钮「在 CC 桌面版中授权」。按产品的通道要票（Cowork / 共用通道两条接口）；只在 CC 桌面版窗口里可用，浏览器、手机里按钮禁用
  （同文件夹授权只在 CC 里）；没有 `mrerp_login` 能力的旧桌面版显示现有的「请更新 CC 桌面版」。ERP 第 6 步直接用它。
- CC 对话页「定时任务 → 连接 MR.ERP」（`scheduled-tasks.tsx` 的 `MrerpConnect`）：去掉「账号」「密码」两格，只留「系统网址」与授权组件；
  网址没变就不再调 `configure`（改了才升连接版本），没有连接时授权前先建一条。
- Cowork Desk「连接 MR.ERP」弹窗（`public/desk/targets.js` 的 `connect_dialog`，录入与定时任务两处用）：同样去掉两格，显示授权状态，
  「在 CC 桌面版中授权」按钮禁用（Desk 不在 CC 桌面版窗口里）。
- 服务端 `configure` 仍收 `credentials`（实验栈脚本与旧页面缓存在用）；网页已经不传。要不要在服务端也拒收 MR.ERP 的密码，留给主会话定。
- 去掉密码框的两处改动在单独提交 `d516082b`，可单独回退。

## 6. 文案清单（`check-copy --report`）

### 6.1 新增（四语 / 三语，过 check-copy，没有拦截）

| 来源·键 | 中文 | English | ไทย | 日本語 |
|---|---|---|---|---|
| assistant·SETTINGS.mrerpAuthorized | 已授权：{x} | Authorized: {x} | อนุญาตแล้ว: {x} | —（CC 网页没有日文） |
| assistant·SETTINGS.mrerpNotAuthorized | 未授权 | Not authorized | ยังไม่ได้อนุญาต | — |
| assistant·SETTINGS.mrerpAuthorize | 在 CC 桌面版中授权 | Authorize in CC Desktop | อนุญาตใน CC เดสก์ท็อป | — |
| assistant·SETTINGS.mrerpAuthorizeFailed | 授权失败 | Authorization failed | อนุญาตไม่สำเร็จ | — |
| companion·mrerp_login_title | MR.ERP 登录 | — | เข้าสู่ระบบ MR.ERP | —（CC 桌面版只有中 / 泰） |
| pearnly_core·connect.mrerp.authorized | 已授权：{0} | Authorized: {0} | อนุญาตแล้ว: {0} | 承認済み：{0} |
| pearnly_core·connect.mrerp.notAuthorized | 未授权 | Not authorized | ยังไม่ได้อนุญาต | 未承認 |
| pearnly_core·connect.mrerp.authorizeInCC | 在 CC 桌面版中授权 | Authorize in CC Desktop | อนุญาตใน CC เดสก์ท็อป | CC デスクトップで承認 |

原生小窗的「用户名」「密码」两格沿用 CC 桌面版已有的 `op_login_user` / `op_login_password`。

### 6.2 删除（界面上去掉；字典词条不删，没有执行 `--approve`）

| 位置 | 去掉的界面文字 | 词条 | 说明 |
|---|---|---|---|
| CC 对话页「连接 MR.ERP」 | 「账号」「密码」两个输入框的标签 | `tr("Account")`、`tr("Password")`（Frappe 自带 msgid，`folder_intake/api.py` 的 `TERM_WORDS` 仍列着，没删） | 页面上不再出现；词条留着不影响 |
| Cowork Desk「连接 MR.ERP」 | 「Account」「Password」两个字段 | `__("Account")`、`__("Password")`（Frappe 自带，别处还在用） | 同上 |

### 6.3 修改已有文案

无（没有改任何已有词条的字）。弹窗标题「连接 MR.ERP」、主按钮「连接」沿用原词条。

> 主会话 11-02 定：去掉两格是功能本身的直接结果，不算改已有文案，不用等用户确认；改动仍在单独提交 `d516082b`。

## 7. 同账号互斥（ADR-026 第 8 条）

- 共用层 `pearnly_core/session_lock.py` 新加 `lease_account` / `renew_account` / `release_account` / `operator_holds`：代录批次按
  「网址 + 用户名」（`mrerp.session.lock_key`）占**同一个 Redis 键**（与直写短锁 `account_lock` 同一个入口、同一个键名），值以
  `operator:` 开头，TTL 300 秒，随电脑领单续期，电脑不在就过期。同一批次重复租、续期都算它自己的；别的批次、正在导入的直写占着时
  租不到（批次等账号）。
- 直写：Cowork `push/mrerp/runner.run` 领到这条、读出凭据后，账号被代录占着（`account_busy`）就整笔撤回这次领取（记录照旧待发送，
  同写者锁的「等锁」`waiting_lock`，不算失败、不转人工），定时巡检（5 分钟）再排。目录采集、菜单读取照旧拿短锁，占着时按原来的「忙」。
- 原因口径：复用已有的「等锁」（领取回 `waiting_lock`、记录保持待发送），没有新增「等账号」文案；代录批次一侧的「等账号」显示随第 3 步。
- 原来「同账号预检拦下」取消，没有实现。
- 已知窗口：直写「看了没占 → 进导入拿短锁」之间有毫秒级空档，这时代录刚好租上，直写短锁等 180 秒后按原来的锁不上转人工（同今天两个
  直写撞同一账号）；租约由第 3 步在领单时续，到时一并核。

## 8. 老连接兼容（证据）

- 直写、目录采集、菜单读取取凭据的入口没改（`connections.service.credentials_for_use`、采集器读 Password 字段）。
- `tests/test_cowork_mrerp_cc_login.py::Store.test_an_old_connection_keeps_logging_in_and_reauthorizing_replaces_it`（生产镜像）：只有网页
  存的密码、没有授权记录的连接，`credentials_for_use` 拿到原密码；CC 授权后拿到新密码。
- 实验栈真实站点 `scripts/cowork-mrerp-login-lab-verify.py::test_old_connection_logs_in_with_its_stored_password_and_cc_authorization_replaces_it`：
  经 `configure` 存的老密码 → `read_for_push` + `credentials_for_use` 取到原密码；经 WSGI 机器方法授权后取到新密码、连接版本不变。（通过，见 §10）
- MR.ERP 直写全链路回归（推送、采集、目录、菜单、主档、黄金样本）全过，见 §10。

## 9. CC 桌面版

- `express_operator/login_dialog.py`：抽出 `ask()`（只收一次输入、不存），Express 的 `ask_and_store` 行为不变；MR.ERP 用标题「MR.ERP 登录」。
- `mrerp_login.py`（不放在代录包里：代录包不带网络库，`tests/test_operator_parts.py` 守着）：经成员通道的 `mrerp_login` 路径交给站点，
  站点收下了才 `credentials.store`；日志只记异常类型。
- `express_operator/credentials.py`：`mrerp_target(MR.ERP 网址或主机, 连接号)`（= `Pearnly CC/MR.ERP/<主机>/<连接号>`，与第 2 步同名）、
  `site_comment(站点)`（凭据备注）、`mrerp_connections(站点)` / `forget_site(站点)`（只读目标名与备注，按备注认站点；第 2 步自己存的
  不带站点备注，不算进心跳）。`store` 多一个可选的 `comment`，Express 照旧 `Pearnly CC`。
- 桥接第 2 步（主会话 11-02 补充）：`operator_authorize` / `operator_forget` 带 `{target: "mrerp", url, connection}` 时交给
  `mrerp_operator.login.ask_and_store` / `forget`；`operator_state` 在有那个包时多带 `mrerp_logins`（`login.stored()`）。本分支基线上
  **没有** `mrerp_operator` 包：不导入、不拷文件，找不到模块回 `{ok: false, code: "unsupported"}`（测试锁住）；`login.user_of`
  的接法（站点要用户名）随第 3 步定，本步没接。
- `desktop_settings.py`：`member_for(对话页站点)`（这台电脑连着这个站点的成员，暂停或密钥失效的不算）；断开连接后这台电脑不再有这个站点的
  成员时删掉本机这个站点的 MR.ERP 登录。
- `worker.py` / `queue_client.py`：心跳带 `mrerp_logins`（这个成员站点的连接号）。`channel.py` 两种通道各加 `mrerp_login` 路径。
- 桥能力 `mrerp_login`；页面据此判断（旧桌面版提示更新）。

## 10. 测试与闸门（绑定分支末端提交，本机 11-02）

| 项 | 结果 |
|---|---|
| 新增离线 `tests/test_mrerp_cc_login.py`（票、共用通道两半、精确 POST、心跳存法） | 13 通过 |
| 新增镜像内 `tests/test_cowork_mrerp_cc_login.py`（权限、写 Password 不升版本、页面不见密码、老连接兼容、机器方法、同账号互斥与执行器排队、假 MR.ERP 站点登录且错密码只试一次） | 13 通过（生产镜像 `ea24fcb9…`） |
| 新增 `tests/test_mrerp_login_pages.py`（网页无密码框、组件只传票、Desk 弹窗） | 4 通过 |
| CC 端 `companion/tests/test_mrerp_login.py`（凭据目标名与第 2 步同名、存删用替身、交站点后才存本机、错误码不含密码、桥只收票、实操登录按目标分路 / 没有第 2 步包回不支持、心跳键） | 19 通过 |
| CC 全套 `companion/tests`（暂存 Python 3.12：pytest、httpx、dbf、aenum、PySide6_Essentials，offscreen） | 1445 通过、46 跳过、0 失败 |
| 真实站点 `scripts/cowork-mrerp-login-lab-verify.py`（本机 cowork-backend 实验栈、生产镜像、真 MariaDB / Redis / WSGI） | 4 通过；同栈再跑既有 `cowork-companion-heartbeat-lab-verify.py` 26 通过。`cowork-companion-credentials-lab-verify.py`（3 失败 1 错）与 `cowork-connections-lab-verify.py`（4 失败 4 错）换成基线 9b3f66e4 源码在同一栈复跑**失败完全相同**，是这两份脚本落后于现行规则，不是本次引入 |
| 真 Redis 上的代录租约（一次性 redis 容器）：代录占着时直写短锁拿不到、别的批次租不到、放开 / 过期后直写拿得到 | 通过 |
| MR.ERP 直写全链路回归（生产镜像）：`test_cowork_mrerp_push` 41、`collector` 15、`catalog` 8、`menus` 17、`test_mrerp_rules_golden` 3、`masters` 12、`folder_tasks_mrerp` 8、`xlsx_source_parity`（2 跳过）、`test_mrerp_core_boundary` 3、`scripts/test-session-lock.py` 5 | 全过 |
| 候选离线检查 `cand-local.sh offline`（生产镜像） | 通过（exit 0） |
| 流程 `run.py --changed --base origin/main`（本机专用回归实验室，迁移后跑） | `cc.chat`、`cc.operator`、`cowork.mrerp-login` 3 条 8/8 格通过；新流程单跑两套数据也通过 |
| `check-architecture` | PASS（0 违规） |
| `check-copy --report` | 新增 8、改动 0、删除 0、拦截 0、标黄 5（5 个隐藏字段的 DocType 标签不在字典里，隐藏字段只标黄） |
| `i18n-build --check`、`check-i18n --strict` | 通过 |
| 全闸门 `gate.sh` | 170 步全过（exit 0，提交 `fcad0e37` 之后、对齐目标名之前的工作树）；对齐后只动了 CC 三个文件，CC 全套、`test_companion_contract`、架构、文案、影响、流程覆盖重跑全过 |
| 真站点（真实 MR.ERP） | **没测到**（原因：MR.ERP 测试库 test2019 / test2020 不存在；第 0 步已确认，等有可用账套再补） |

流程覆盖闸：新流程 `cowork.mrerp-login`（`scripts/flows/cowork_mrerp_login.py`）覆盖 CC 定时任务页、授权组件、Desk 连接弹窗、发票接口、
`connections/mrerp_login.py`、`login_tickets.py`；没有页面的三个机器入口登记进 `tests/flows/not_flows.toml`（共用通道会话接口 `cc_desktop/api.py`、
Cowork `credential_api.py`、`heartbeat.py`，各写原因）；`masters/workspaces.py` 因新流程覆盖到了，从 `behind_baseline.txt` 删掉（只减不增）。

## 11. 入口实测表（`check-impact.py`，命中 `cc-desktop`、`cc-desktop-bridge`）

| 入口 | 结果 |
|---|---|
| `cc.mrerp-login`（新登记） | 网页部分**通过**（流程 `cowork.mrerp-login` 两套数据）；机器部分**通过**（真实站点 WSGI 验证脚本）；CC 桌面版原生小窗与 Windows 凭据管理器**没测到**（原因：不动测试机，Windows 实机等验收窗口） |
| `cc.settings.connection` | **没测到**（原因：自动冒烟要测试机实验栈；本次没改设置页，`tests/test_assistant_ui.py` 离线通过） |
| `cc.desktop.bridge` | **没测到**（原因：Windows 测试机 CC；桥新增方法由 `companion/tests/test_mrerp_login.py`、`test_tomo_bridge.py` 离线通过） |
| `cc.operator.page` | 通过（流程 `cc.operator` 两套数据；`tests/test_cc_operator.py` 离线） |
| `cc.desktop.operator` | 通过（离线：CC 全套里的代录测试；`login_dialog` 的 Express 行为不变） |
| `erp.cc.chat-push` | 通过（离线 `gate.sh` 内的 ERP 对话直写测试） |
| `cowork.cc.channel` | **没测到**（原因：要测试机 CC 推单；心跳 / 机器认证由 `cowork-companion-heartbeat-lab-verify.py` 26 项在真实站点通过） |
| `cowork.mrerp.push` | **没测到**（原因：没有可用 MR.ERP 账套）；离线全链路回归通过 |
| `cowork.mrerp.catalog`、`cowork.connections` | **没测到**（原因同上 / 要测试机）；连接视图多一项 `credential` 由真实站点验证脚本核过 |
| `cowork.transaction`、`shared.transaction` | **没测到**（原因：并发压测要测试机）；机器方法的整笔重来只在取票之后，单测覆盖 |
| `shared.execution` | **没测到**（原因：要测试机；改动只是 `session_lock` 新函数，原 `account_lock` 不变，`test-session-lock.py` 通过） |
| `erp.cc.connect`、`erp.cc.write`、`erp.cc.masters`、`erp.cc.stock-void`、`erp.cc.companies`、`erp.cc.stock-transfer`、`cc.desktop.places`、`cc.desktop.write` | **没测到**（原因：要测试机 CC 与 Express；共用通道只加了一条方法与一列，`test_cc_desktop_channel.py` 通过） |
| `cowork.page.intake` | **没测到**（原因：自动冒烟要测试机实验栈；Desk 连接弹窗由流程 `cowork.mrerp-login` 通过） |
| `shared.shell-gate`、`shared.team.members` | **没测到**（原因：本次没改这两处，只是 `companion/api.py` 的调用方登记把它们带进清单） |

## 12. 剩余项与要主会话定的

1. ~~授权人范围~~：主会话 11-02 已定，按 §4 做完（管理员写站点，成员只存本机，别人都不行）。
2. ~~文案确认~~：主会话 11-02 定 8 条都是新增、直接写；去掉两格是功能本身的直接结果，不算改已有文案（`d516082b` 保留）。
3. 服务端拒收网页交的 MR.ERP 密码：主会话定放到第 3 步，本步不改。
4. **代录批次租 / 续 / 放账号**：本步只给了共用层函数与直写侧的排队；在 CC 领单时调用由第 3 步接。
5. **ERP 一侧**：共用接口与产品钩子已备，ERP 第 6 步声明 `mrerp_login_allowed` / `mrerp_login_store` 即可；之前回 unsupported。
6. 没装 Express 的电脑没有心跳，它的已授权清单只在授权当时记一条（断开后的删除要等它下一次授权或第 3 步的领单上报）。
7. 真实 MR.ERP 与 Windows 实机：等有可用账套与验收窗口。
