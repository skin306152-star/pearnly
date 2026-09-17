# P2.02 站点接入统一身份：按 (issuer, subject) 绑定

日期：2026-09-17。环境：样板站点 `cowork-a.localhost`。

## 实现位置

自有 App `pearnly_core`（本仓 `apps/pearnly_core/`），用本仓 `infrastructure/images/frappe.Dockerfile` 打进镜像。

| 文件 | 作用 |
|---|---|
| `pearnly_core/identity.py` | 授权码登录全流程：state、nonce、PKCE(S256)、JWKS 签名校验、issuer/audience 校验、按绑定取用户、建原生会话 |
| `doctype/pearnly_identity/` | 绑定表：`(issuer, subject) → 本地账号`，带复合唯一校验 |

### 三条硬规则（都写进代码注释了）

1. **本地账号由 (issuer, subject) 唯一决定**，不按邮箱匹配。Frappe 原生流程在 `frappe/utils/oauth.py:239` 是 `user = get_email(data)`——按邮箱找人，邮箱一改或两家声明同一个邮箱就会认错人，所以没有采用。
2. **绝不自动开户**。查不到绑定就抛权限错误，没有"兜底建号"分支。
3. **业务站点只接受配好的那一个 issuer**，平台 realm 的 issuer 不同，天然被拒。

## 端到端实测（正例）

脚本 `scripts/sample-oidc-e2e.sh`，完整走浏览器同款流程：

```
[1] 站点发起登录 → 重定向到身份服务
[2] 打开登录页 → 解析表单
[3] 提交账号密码 → 身份服务回跳到本站回调（带 code+state）
[4] 回调校验通过 → 建立本地会话 → 跳转 /app
[5] 用该会话查当前用户 → {"message":"tenant-admin@sample.local"}
```

即：**身份服务里的 `tenant-admin` 通过绑定记录登入了本地账号 `tenant-admin@sample.local`**。这一路能通，说明 state、nonce、PKCE、签名、issuer、audience 校验全部通过（任何一项不过都会抛错）。

## 反面实测（关键）

新建一个身份服务账号 `stranger`，**不做任何绑定**，跑同一条流程：

| 环节 | 结果 |
|---|---|
| 身份服务登录 | **成功**（Keycloak 不认识我们的邀请制） |
| 回跳到本站回调 | 发生 |
| 回调之后 | **没有建立会话**，后续请求仍被判为未登录 |
| 站点账号表 | **没有多出任何账号**，仍是原来的 5 个种子账号 |

结论：**"身份服务认证通过"不等于"能进本站"**；必须被邀请；绝不自动开户。这正是要的行为。

## 过程中踩到的三个坑（都已固化）

1. **签发方必须在浏览器侧和容器侧是同一个字符串。** Keycloak 不设固定 hostname 时会按请求的 Host 生成 `iss`，容器内调令牌端点拿到的 `iss` 与浏览器侧不一致，校验必然失败。改为固定 `--hostname=http://keycloak:8080`；宿主机侧用 Host 头访问。真实部署用正常域名即可，不需要这一招。
2. **Keycloak 26 会给资料不全的用户挂 `VERIFY_PROFILE` 必填动作**，登录后会跳到补资料页而不是回调，表现为"莫名多一个跳转"。建用户时给上姓名即可。
3. **基础镜像里的 `sites/apps.txt` 没有结尾换行**，追加会拼成 `erpnextpearnly_core`，报 `ModuleNotFoundError`。写入时必须重写整个文件。

## 尚未验证（不掩饰）

- 平台 realm 的账号走业务站点那条路径**没有单独跑一遍**（issuer 不同会拒，但缺实测）。
- "同一账号同时打开三个产品、互不踢线"未验。
- "未勾选产品拒绝"未验——产品清单尚未实现。
- 现有 app 仓里 Cowork 的 Google/LINE 网页登录按钮与旧注册链接**尚未移除**（那是旧仓库的代码，等 P3 迁前端时处理）。
- 会话登记、改密撤销、全局退出、管理员交接**均未实现**。
