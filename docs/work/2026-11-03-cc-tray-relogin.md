# CC 桌面版从托盘打开每次都要重新登录（2026-11-03）

分支 `pearnly/cc-tray-relogin`，基线 `origin/main` `bebe423e9`。修两处缺陷，并按主会话追加（用户已定）给产品 realm 设上
「记住我」30 天；不改版本号、不发 CC、不部署、不动线上 realm。

## 现象

CC 桌面版从托盘打开窗口，每次都落到 fatcc.com 重新填账号密码，不回上次的对话页，也不走「记住上次、先静默确认登录」。

## 根因

1. **CC 端按域名前缀猜测试环境**：`companion/src/companion/tomo_links.py:57-64`（`resume_url`，10-10 的 `5dce9016e`，1.4.0 加入）
   把「站点 test 开头」当测试环境。入口是正式 fatcc.com 时，test 开头租户的上次对话页（如 `https://testerp.pearnly.com/assistant`）
   一律不回，每次改走 fatcc.com。`gui_tomo.py:268` 打开窗口时用它，`gui_tomo.py:389-392` 只存 `last_url`、不存当时的入口。
2. **入口站点读 Cookie 没解码**：`apps/pearnly_control/pearnly_control/assistant_entry/entry.py:128-130`（`_remembered`）直接读 Cookie
   原值。Frappe 16.33.0 写 Cookie 时 URL 编码（镜像内 `frappe/auth.py:436` `quote(value.encode("utf-8"))`），自己读时 `unquote`
   （同文件 `:413`）。上次的账号池 Cookie 实际是 `https%3A//auth.pearnly.com/realms/pearnly-business`，`entry.py:157` 过
   `contracts.issuer` 时被拒，`resume_url()` 返回空串，fatcc.com 的静默确认从 10-10 的 `25c1f2628` 起没生效过。

## 修法

1. CC 端不再按域名判断：`_remember` 存 `last_url` 时一起存当时的入口 `last_entry`（注册表 `HKCU\Software\Pearnly\Tomo`）；
   `resume_url(saved, saved_entry, env)` 入口相同（大小写、末尾 `/` 不算不同）才回上次的 `/assistant`，不同就从现在的入口进。
   **老注册表兼容**：只有 `last_url`、没有 `last_entry` 时按「入口相同」处理，回 `last_url`。理由：会话还在就直接进；会话失效时站点
   原生把访客的 `/assistant` 送去 CC 入口（`apps/pearnly_core/pearnly_core/login_redirect.py`），CC 不用自己判断；最坏情况是测试入口
   的老用户第一次回到正式站点的登录跳转，不会进错账号，下一次起就有入口记录。
2. 入口站点 `_remembered` 照 Frappe 的做法先 `unquote` 再用（账号池与站点两个 Cookie 都走它）。浏览器里已有的旧 Cookie 解码后即正确值，
   不用迁移。

## 追加：Keycloak「记住我」保持 30 天（主会话 11-03 转达，用户已定）

10-15 只把产品站点的 `session_expiry` 改成 `720:00`（`apps/pearnly_core/pearnly_core/account/session_length.py`），Keycloak realm 的
会话时长没设，仍是默认（空闲 30 分钟、最长 10 小时），站点登录跳转与 fatcc.com 静默确认到点就要重新输密码。

- 字段核对（锁定版本 26.7.4）：镜像 `quay.io/keycloak/keycloak:26.7.4` 的 `keycloak-core` 里 `RealmRepresentation` 有
  `ssoSessionIdleTimeoutRememberMe` / `ssoSessionMaxLifespanRememberMe`；26.7.4 源码 `server-spi-private/.../RealmExpiration.java`
  的 `fromRealm` 对勾了记住我的会话取 `max(普通值, 记住我值)`（空闲与最长各一项），普通会话不受影响。
- `infrastructure/production/bootstrap-auth.py`：新增 `REMEMBER_ME`（两项都是 `2592000` 秒），完整初始化的 `settings` 带上并回读断言；
  普通会话 `ssoSessionIdleTimeout` / `ssoSessionMaxLifespan` 不设、保持原值。新增 `--remember-me` 模式：只对已有 realm PUT 这两项，
  前后回读，断言两项为 30 天、普通会话与 `rememberMe` 不变；realm 没开「记住我」就不写、直接报错。不碰 SMTP、用户资料、客户端和密钥文件。
- `scripts/sample-cowork-realm.py`：本机样板从业务 realm 复制的字段加上这两项。
- **怎么应用到线上**：部署不会跑 `bootstrap-auth.py`（镜像只把它同步到主机 `/opt/pearnly/deploy/infrastructure/production/`）。
  下次部署之后，由主会话安排在生产主机上各执行一次（需 root 读 `/opt/pearnly/secrets`）：

  ```
  sudo python3 /opt/pearnly/deploy/infrastructure/production/bootstrap-auth.py --remember-me
  sudo python3 /opt/pearnly/deploy/infrastructure/production/bootstrap-auth.py --remember-me --realm pearnly-cowork
  ```

  成功各打印一行 `Remember-me sessions set to 30 days on <realm>; regular session lengths unchanged`。本任务没有在线上执行。
- 未验证：已存在的 Keycloak 会话设好后是否立即按 30 天算（源码按 realm 当前值计算，但没在真 Keycloak 上测）；没起实验栈跑真 Keycloak。
- `scripts/stocktake-acceptance-init.py` 建的是盘点验收用的临时 realm，不是产品 realm，没改。

## 测试

| 项目 | 结果 |
| --- | --- |
| `companion/tests/test_tomo_links.py`：改原第 32 行断言；新增 test 租户经正式入口回上次页面、入口不同不回、老注册表无入口记录回上次页面、窗口存取入口的源码检查 | 12 通过 |
| `tests/test_assistant_entry.py`：新增 `RememberedIssuerTests`，按 Frappe 编码写入、读回、过 `contracts.issuer` 为 True，`resume_url()` 发起静默确认；未编码值、无 Cookie、非法账号池照旧 | 28 通过；撤掉 `entry.py` 修改时新测试失败 |
| `tests/test_account_session_length.py`：新增 `KeycloakRememberMeTests`，用假 Keycloak 执行 `bootstrap-auth.py --remember-me`（两个 realm）：只 PUT 两项 30 天、普通会话不变；realm 没开记住我不写；完整初始化与样板复制带同样字段 | 6 通过；撤掉脚本修改时 3 条失败 |
| CC 全套 `companion/tests`（暂存 Python 3.12：pytest、httpx、dbf、aenum、PySide6_Essentials，offscreen） | 1472 通过、42 跳过、0 失败 |
| 架构检查 `check-architecture.py` | PASS（0 违规） |
| 全闸门 `gate.sh`（照 `development.yml`） | 两处修复后 exit 0；加入记住我、三个提交后复跑（`SKIP_NPM_CI=1`）174 步全过、exit 0 |

候选离线检查（`ci-candidate-checks.sh offline`）不含 `assistant_entry`，本次未改 `pearnly_core`，不需要跑。

共用件影响清单（`check-impact.py`）命中三项，均为**没测到**（原因：要真实入口登录与 Windows 测试机 CC，本任务不碰测试机与生产，
也没起实验栈）：`control.issue-and-entry`（fatcc.com 入口登录，离线测试覆盖）、`cc.desktop.bridge`（本次只改窗口启动地址与存的注册表值）、
`control.execution`（fatcc.com 入口）。真机验收待主会话安排发版后在测试机确认：test 租户从托盘打开回到上次对话页，fatcc.com 静默进入。

## 没做

- 线上 realm 没动，`--remember-me` 的执行由主会话安排（见上）；普通（不勾记住我）会话时长保持不变。
- 不改版本号、不发 CC 版本、不部署。
