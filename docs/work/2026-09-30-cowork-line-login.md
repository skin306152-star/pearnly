# 2026-09-30 Cowork 接入 LINE 免密登录

状态：已合入 main，未部署；部署随「旧项目迁移后的用户体验问题」窗口统一部署（两窗口 2026-09-30 已约定）。

- 分支 `codex/cowork-line-login`，基线 `2c7f9a6e`（= 当时 `origin/main`），工作树 `.claude/worktrees/cowork-line-login`。
- 起因（用户）："line 的免密也要做，以后每开一个租户我都要为这个租户做一个吗？"——不用：全平台一个 LINE Login 频道
  （`2010411313`，密钥在控制面开户目标上只填一次），每个租户只要一个挂在该频道下的 LIFF（开户单里本来就填）。

## 为什么 Cowork 一直没有

| 缺口 | 证据 |
| --- | --- |
| 开户只给 ERP 下发 LINE 登录密钥 | `pearnly_control/provisioning.py` `LINE_LOGIN_PRODUCTS = ("erp",)`；erpcowork `pearnly_line_login_idp` 为空 |
| Cowork 账号池从没做过 realm 级设置 | 生产只读 `verify_realm`：`pearnly-business` OK、有 `line-2010411313`；`pearnly-cowork` "Login source scope is not a realm default"、无任何 LINE 入口 |
| Cowork 发到 LINE 的网页链接是直链 | `line/menu.py`（库存盘点、任务管理）、`line/cards.py`（打开录入单）用 `get_url()` 直拼，不经 `line_login.entry_url` |

站点侧（`pearnly_core.account.line_login`）、建号服务（`member_service.link_line`，`PRODUCTS` 含 cowork）、
Keycloak 回调登记（用户 09-26 已在 LINE 后台登记 pearnly-cowork 回调）都不分产品，无需改。

## 修改

- `pearnly_control/provisioning.py`：`LINE_LOGIN_PRODUCTS = ("erp", "cowork")`。
- `infrastructure/production/site-step.py` `line_login()`：`configure_site` 前先 `configure_realm`（幂等），账号池级设置随开户自动对齐，
  不再靠运维手跑 `configure-line-login.py --realm …`。
- `pearnly_cowork/line/menu.py`、`line/cards.py`：网页链接改走 `line_login.entry_url`（与 ERP 卡片 / 菜单同一入口）。
  菜单内容变了，按内容散列发布时会建新的一套（旧的不删，发布后重挂已绑定用户）。

## 部署后要做（统一部署之后，由本窗口或部署窗口执行，需用户点头）

1. 控制面对 erpcowork 的 LINE 渠道重排 `set_line`（`provisioning.queue_cloud_line_config(channel)`），开户执行器跑一遍：
   配 `pearnly-cowork` realm 级设置 → 建 `line-2010411313` 入口 → 站点写 `pearnly_line_login_idp` → 发布新菜单 → Webhook 对齐。
2. 回读：`verify_realm(pearnly-cowork)` 通过；erpcowork `enabled_alias()` 非空；LINE 内置浏览器 UA 打开菜单链接 302 到
   `auth.pearnly.com` 且带 `kc_idp_hint=line-2010411313`。
3. 真机：LINE 里点「库存盘点」免密进网页（需用户手机）。

## 验证

| 版本 | 环境 | 场景 | 结果 |
| --- | --- | --- | --- |
| 工作树 | 本机 | development.yml 24 条闸门 | PASS |
| 工作树 | 生产 Cowork 镜像（五个 App 源码只读覆盖，断网、2G） | `ci-candidate-checks.sh offline`，含新增 `test_line_channel_setup.LineLoginSecret`、`test_cowork_line_intake` 菜单 / 卡片链接断言 | PASS（镜像 `4ff689c1`） |
