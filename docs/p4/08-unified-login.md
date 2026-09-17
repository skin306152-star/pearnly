# 统一登录口

日期：2026-09-18。状态：**已实现并用真实浏览器验证**。

## 要解决的问题（用户实测踩到）

用户打开新建租户的站点（`http://qc65956.localhost:8101`），输入我给的密码，
得到 "Invalid credentials"，以为密码错了。

**密码是对的，页面是错的。** 实测一个全新访客打开站点，拿到的是
**Frappe 自带的登录页**：

```
页面标题: Login
表单字段: login_email / login_password / forgot_email / login_with_email_link_email
按钮: Continue / Login with Email Link / Send Link / Send login link / Login with password
```

而业务账号的密码**存在身份服务里，站点上没有他们的密码**（P3 合同：业务用户
无独立本地密码登录旁路）。所以在这个页面上输入账号密码，**永远**是
Invalid credentials —— 看起来完全正常的页面，实际是条死路。

## 改法

业务站点上，未登录访客走这几个入口时一律送去身份服务：

```
/  /login  /app  /desk   →   /api/method/pearnly_core.identity.begin_login
```

登录完由 callback 带 code 回来建本地会话。三个产品共用同一套（用户要求"一个登录口"）。

### 只对业务站点生效

控制面（ADR-004）**故意**用本地账号 `earn/earn`，不进统一身份。判断依据是
站点有没有 `pearnly_product`（开通时写入，控制面没有）。实测两边都正确：

| 站点 | 打开 `/` 看到 |
|---|---|
| 业务站点 `qc65956.localhost:8101` | 身份服务登录页（"Sign in to Pearnly"） |
| 控制面 `control.localhost:8104` | Frappe 本地登录页（earn/earn 可用） |

### 顺带关掉本地密码登录旁路

业务站点上 `Administrator` 是有密码的 —— 那是条真实的绕过路径。收尾时对业务站点
置 `disable_user_pass_login = 1`（同时管住网页表单与 `/api/method/login`，
见 `frappe/auth.py:149`），并关掉 `login_with_email_link`。

## 统一登录页的形态（对照用户要求）

实测渲染出来的页面：

```
PEARNLY | Sign in to your account
Username or email  /  Password  /  Forgot Password?  /  Sign In
第三方登录: 无     注册: 无
```

与用户要求的"去掉第三方登录链接和注册、只保留账号密码 + 忘记密码"一致。
手机号登录按之前对齐的结论**不做**（Keycloak 原生不支持，不另造）。

> **后续（2026-09-18）**：上面这几行是当时的样子（身份服务自带的朴素样式）。用户随后
> 确认"登录页统一用猫那套"，于是登录页换成复用旧登录页的设计 —— 见
> [统一登录页改成"复用旧设计"](10-login-theme.md)。去掉注册/第三方登录这条不变。

## 实现要点（踩坑记录）

1. **`frappe.Redirect` 在 `before_request` 里不能用。** 它只在**网站渲染路径**被捕获
   （`frappe/website/serve.py:28`），而 `before_request` 跑在那之前 —— 抛出去会被
   通用异常处理当成 500，实测页面显示 `302: Uncaught Exception`。

   正确做法：继承 **werkzeug 的 `HTTPException`**。`frappe/app.py` 的顶层异常处理
   对它有专门分支：
   ```python
   response = e.get_response(request.environ) if isinstance(e, HTTPException) else handle_exception(e)
   ```
   在 `get_response` 里返回一个标准重定向响应即可。

2. **必须用 302，不能用 301。** 301 会被浏览器永久缓存，登录之后仍会被弹回登录页
   （表现为"清缓存才好"的假故障）。这条同样适用于之前记录的
   `/desk` 未登录跳转问题。

3. **hooks 是进程级的，不能按站点区分。** `frappe.get_hooks` 通过 Python 导入加载
   `hooks.py`，一个进程里只导入一次，所以 `hooks.py` 里读 `frappe.conf` 做站点判断
   不可靠。站点差异只能在**运行时**判断 —— 这就是为什么用 `before_request` 而不是
   静态的 `website_redirects` 规则。

## 验证证据

`scripts/sample-verify-tenant-login.mjs`（真实 Chromium）：

```
[0] 访客打开站点根 → http://keycloak.localhost:8105/realms/pearnly-business/...
    登录页: 忘记密码=true 第三方登录=false 注册=false
跳转到: .../protocol/openid-connect/auth
登录后地址: http://qc65956.localhost:8101/desk
会话里的当前用户: prov-65956@sample.local
JS 报错: 无
[PASS] 统一登录口 + 登录成功 + 落点 Desk（不是设置向导）
```

控制面同时验证未受影响（本地登录页正常）。

## 关于用户那份凭据

`prov-65956@sample.local` / `Tenant-Test-2026` **本身是好的** —— 报告进来后我先用
真实浏览器实测，确认能登进去，才去查页面。根因是入口页，不是密码。

## 界面自检（用户要求：涉及界面的自己打开进去看）

控制面各界面用真实浏览器逐个打开，**JS 报错 0**：

| 界面 | 路径 | 结果 |
|---|---|---|
| 桌面 | `/desk` | 正常 |
| 开通单 | `/desk/pearnly-issue` | 正常 |
| 租户 | `/desk/pearnly-tenant` | 正常 |
| 功能目录 | `/desk/pearnly-feature` | 24 项 |
| 部署 | `/desk/pearnly-deployment` | 2 条 |
| LINE 渠道 | `/desk/pearnly-line-channel` | 1 条 |
| 开通进度 | `/desk/pearnly-provisioning-request` | 1 条 |
| 开户目标 | `/desk/pearnly-target` | 正常 |

侧边栏「租户开通」下已有：开通单 / 租户 / 功能目录 / 开户目标 / 部署 / LINE 渠道 / 开通进度。

## 还没做

1. **登录后没有"选产品"这一步**：现在登录后直接落 `Desk`。用户若同时开通了多个产品，
   应该有个地方让他选进哪个（或按产品分别给出入口）。现在每个产品是独立域名独立站点，
   所以"选产品"实际是"打开对应域名的站点"——但**还没有一个聚合入口**。
2. **产品界面还没做**：登录后进的是 Frappe 原生 Desk，不是产品自己的界面
   （三个产品目前只有接口与清单）。
3. **LIFF / 手机端登录**未做（属于 P6）。
