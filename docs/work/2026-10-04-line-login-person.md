# 2026-10-04 LINE 免密登录：每个 LINE 一个专用身份，站点按绑定认人

状态：实现与本地检查进行中（分支 `codex/line-login-person`，基线 `12119fb6` = 当时 `origin/main`，
工作树 `../pearnly-wt-line-login`）。用户 2026-10-04 授权"按这个修"。

## 现象与证据（生产只读，2026-10-04 21:00–21:30 曼谷时间）

- 用户在 testcowork 的 LINE 菜单点「库存盘点 / 任务管理」：第一次 417「未捕获异常」，之后每次 403，改用密码登录也 403。
- 前端访问日志：`line_login.enter` 302 → `identity.callback` 同一秒 499 + 417（LINE 内置浏览器把回调发了两次，
  第二次 state 已被消费）→ 之后的回调全是 403。
- 403 来自 `identity._resolve_user`：身份服务经 LINE 入口认出的是 **testerp 的账号**
  （业务池里这个 LINE 关联在 testerp 的 `aksarapak.aa@gmail.com` 上），testcowork 不认识这个身份。
  被拒后身份服务会话没结束，单点登录一直带回同一身份，所以密码登录也进不去。
- testcowork 的绑定 `login_link = failed`：同一账号池里一个 LINE 只能挂一个账号，第二个租户的账号挂不上；
  而且 testcowork 是 Cowork 产品却签到业务池，控制面按产品把关联发去了 Cowork 池，那边根本没有这个身份；
  testcowork 的账号在控制面也从没登记过（早于身份登记机制）。
- 全部 6 个生产站点：经 LINE 登录建立的会话 **0 次**（`Pearnly Session.identity_provider` 有写入代码，`sessions.py`）。
  mrerp / testerp / erpcowork 的关联状态是 linked，但从未有人真正走通过。DMS 两站不开 LINE 登录（按设计）。
- 所有频道（含 LINE Login 频道 2010411313）都在 Provider "Pearnly" 下，LINE 用户号一致 —— Provider 不是原因
  （@pearnly 与 B DMS 两个 Messaging 频道都能查到同一个用户号的资料）。
- LIFF：DMS 两站落地地址正确；testcowork 写成 `https://testcowork.pearnly/liff/dms-booking`（少 `.com`）、
  testerp 填成 DMS 地址。已在 LINE Developers 改为站点根并回读（用户授权）。开户手册教 ERP/Cowork 填 `/desk`，
  而 ERP 的 LIFF 页在 `/stocktake`，`/desk` 不是它的前缀。

## 结构性修法

| 层 | 改动 |
| --- | --- |
| 身份服务（控制面建） | 每个 LINE 在每个账号池一个 **LINE 专用身份**：用户名 `pearnly-line-` + 归属证明（服务器密钥 HMAC）前 32 位，占位邮箱 `@line.invalid`（池要求有邮箱，否则登录弹补全资料），无密码、无必需动作，`pearnly_member_owner` 存归属证明。它独占该入口下这条 LINE 的关联；原来直接挂在租户账号上的（本平台登记过的账号）迁走，未知持有人一律 conflict 不抢。 |
| 控制面 `member_service` | `link-line` / `unlink-line` 带站点签发方，按**站点实际所在池**选池（`member_provider.pool_prefix`，只认已配置的池）；关联记录加 `realm`，按（池, 入口, LINE）计数，最后一个站点解绑时删专用身份（它的身份服务会话随之结束）；同一池同一 LINE 的建/删用命名锁串行；租户删除时一并释放。 |
| 站点 `identity.callback` | LINE 来源（`pearnly_idp`）按本站生效绑定认人（`line_login.resolve_line_login`），不按 subject；其余照旧按 (issuer, subject)。任何被拒：原生说明页 + "请登录"按钮（经身份服务注销后回登录），不再卡在单点登录里。state 取用改成锁内换"已用"标记：重复回调已登录就去目标页，未登录就重新走一遍。LINE 登录不保存专用身份的刷新令牌、不用它的用户名和语言。 |
| 站点补丁 | `v0_0_6_line_login_person`：生效绑定标 `pending`，每小时对账让控制面建专用身份、迁走旧关联。控制面补丁 `v0_0_3_line_link_realm` 按产品补 `realm`。 |
| 开户 | `set_line` 读回 LIFF 落地地址（LINE Login 频道临时令牌，只读）：DMS 必须是 `/liff/dms-booking`，ERP/Cowork 必须在本站下；"Use webhook" 没开、LIFF 不对时，执行器只把写给开户人的那一行提示带回开户单（`PEARNLY_STEP_HINT`），其余输出仍留服务器受限日志。手册 ERP/Cowork 改填站点根。 |

## 上线后要做（部署后，本窗口执行）

1. 控制面补登记 testcowork 的账号身份（`register_owner(<testcowork 租户>, <其 Pearnly Identity subject>)`），
   之前它从没登记过，关联请求一律 not_permitted。
2. 各站点跑一次 `pearnly_core.account.line_login.reconcile`（或等每小时对账），回读：绑定 `linked`，
   业务池 / Cowork 池里每个 LINE 只由 `pearnly-line-*` 持有，租户账号身上没有 LINE 关联。
3. 真机：用户在 testcowork、testerp 各点一次 LINE 菜单，免密进入（需用户手机）。

## 验证

| 版本 | 环境 | 场景 | 结果 |
| --- | --- | --- | --- |
| 工作树 | 本机 Python 3.12 | development.yml 全部闸门（34 条单行 + 2 条多行） | PASS |
| 工作树 | 生产镜像 `4e80514d` + 本分支五个 App 源码，断网 2G | `test_member_service`、`test_line_login`、新增 `test_identity_callback`（43 条） | PASS |
| 工作树 | 真实 Keycloak 26.7.4 + 假 LINE + 两个真实 Frappe 站点 + 控制面（`scripts/line-login-lab.sh all`，独立 colima 环境） | A–M 共 40 项，含新增 M1–M6「同一 LINE、两个租户、两个不同账号」与 G2–G4「被拒说明页、按钮注销后不再卡住」 | 40/40 PASS |
