# P2.01 Keycloak 身份服务收口

日期：2026-09-17。环境：本机独立虚拟机 `pearnly` 的样板。

## 已建的两个 realm

| realm | 用途 | 关键开关 |
|---|---|---|
| `pearnly-platform` | 平台/后台身份，**只在开发机** | 公开注册关、重复邮箱关、暴力破解防护开 |
| `pearnly-business` | 业务/租户身份 | 公开注册关、重复邮箱关、暴力破解防护开、允许自助改密 |

**平台与业务用两个独立 realm**，因此 issuer 不同。业务站点只接受 `pearnly-business` 的 issuer——这就是"平台账号不能进业务站点"的实现方式，不需要额外的拦截代码。

这正是用户 2026-09-17 的要求：平台管理员只管后台，租户是租户，不为此加复杂机制。

## 实测关掉的口子

| 口子 | 验证方式 | 结果 |
|---|---|---|
| 用户自助注册 | `POST /realms/pearnly-business/protocol/openid-connect/registrations` | **400**，且 realm 上 `registrationAllowed=false` |
| 密码授权（Resource Owner Password） | `POST …/token` with `grant_type=password` | **401 `unauthorized_client`**；客户端 `directAccessGrantsEnabled=false` |
| 动态客户端注册 | `POST /realms/pearnly-business/clients-registrations/openid-connect`（伪造第三方客户端） | **403**，被默认的 `Trusted Hosts` 策略拒绝 |
| 外部身份提供方 | `GET identity-provider/instances` | **空**，没有配置任何外部登录源 |
| 重复邮箱 | realm 设置 | `duplicateEmailsAllowed=false` |
| 改用户名 | realm 设置 | `editUsernameAllowed=false`（用户名是稳定标识，不许改） |

## 业务 OIDC 客户端（示例：cowork-a）

| 项 | 值 |
|---|---|
| clientId | `cowork-a` |
| 类型 | 机密客户端（有 secret），**不是** public client |
| 授权码流程 | 开 |
| 隐式流程 | 关 |
| 密码授权 | 关 |
| 服务账号 | 关（业务客户端不需要机器身份） |
| PKCE | 强制 `S256` |
| 重定向白名单 | `http://127.0.0.1:8101/*`、`http://cowork-a.localhost/*` |

**每个部署一个独立客户端**，回调白名单逐个登记——不共享客户端，也不放开宽泛重定向。

## 残留项（不掩饰）

1. **动态客户端注册目前是"被策略挡住"，不是"显式关闭"**。默认 `Trusted Hosts` 策略返回 403，属间接防护；后续要显式收口这个策略再复验一次。
2. `grant_types_supported` 在 realm 发现文档里仍列出 `password`（Keycloak 的全局广告），实际被客户端级开关拦住（已实测 401）。判断以客户端实测为准，不以广告列表为准。
3. 尚未接入 Frappe：站点目前还是原生账号密码登录，**没有走 Keycloak**。这是 P2 下一步。
4. 尚未验证"同一账号同时打开三个产品""未勾选产品拒绝""平台账号拒绝业务站点"这三条退出条件——**一条都没验**。

## 下一步（P2 余下）

1. Frappe 站点接 OIDC：以 `issuer + subject` 绑定本地用户，**禁用按邮箱自动开户**。
2. 移除 Cowork 的 Google/LINE 网页登录按钮与旧的注册链接。
3. 原生 Frappe 会话 + 受控会话登记；不同产品、不同设备不互踢。
4. 跑 P2 的三条退出条件。
