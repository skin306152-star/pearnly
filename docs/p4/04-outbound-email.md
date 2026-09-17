# 对外发信（邀请邮件 / 忘记密码）

日期：2026-09-17。状态：**已配置并实测发送成功**。

## 为什么需要

租户开通的闭环是：控制面开号 → 用户收到**邀请邮件** → 点链接自己设密码 → 进入产品。
登录页的**忘记密码**也走同一条通道。这两条都由身份服务（Keycloak）负责发信，
而它的邮件服务器设置**默认是空的** —— 不配则一封都发不出去，且失败得很安静。

## 配置

| 项 | 值 |
|---|---|
| 发件邮箱 | `pearnlyai@gmail.com`（用户指定） |
| SMTP | `smtp.gmail.com:587`，STARTTLS（不是 465 隐式 SSL） |
| 认证方式 | Gmail **应用专用密码**（Google 已不允许程序用账号密码发信，需先开两步验证） |
| 作用范围 | 两个 realm 都配：`pearnly-business`（产品口）、`pearnly-platform`（控制口） |
| 忘记密码开关 | 两个 realm 均置 `resetPasswordAllowed=true` |

凭据放在仓库根 `.env.local`（**已被 `.gitignore` 的 `.env.*` 规则忽略，不进 Git**）。
配置脚本：`scripts/sample-configure-smtp.sh`（幂等，可重复执行）。

```bash
scripts/sample-configure-smtp.sh          # 写入配置
```

## 验证证据（分层）

**1. 凭据本身有效** —— 直连 SMTP 实测：

```
目标: smtp.gmail.com:587  账号: pearnlyai@gmail.com  密码长度: 16
STARTTLS 可用: True
AUTH 成功 ✓ 凭据有效
```

**2. 身份服务真的把邮件发出去了** —— 走 `execute-actions-email`
（与邀请邮件**同一条代码路径**），收件人指向真实信箱：

| 条件 | 接口返回 | 服务端日志 |
|---|---|---|
| **正确**密码 | **HTTP 204** | 无错误 |
| **故意写错**密码（标定实验） | **HTTP 500** | `KC-SERVICES0029: Failed to send email: … Password based SMTP connect failed` / `535-5.7.8 Username and Password not accepted` |

这组对照很关键：它证明**发信失败是会报出来的**，所以"204 + 日志无错误"不是假通过。
（最初只看"日志没报错"是不够的证据 —— Keycloak 默认日志很安静，该时段日志 0 行。）

**3. 人工确认收件** —— 唯一需要人看的一层：`pearnlyai@gmail.com` 的收件箱里
应该能收到 2 封测试邮件（标题含 "Update your password" / "设置密码" 一类）。
**这一层我没有能力验证**（读不了那个信箱），需要用户确认。

## 踩过的坑

1. **应用专用密码必须是 16 位。** 第一次给的是 15 位，Gmail 直接 `535` 拒绝。
   这类漏字符多发生在从页面复制时首尾被截，症状就是"配置看着都对但发不出去"。
2. **`smtpServer` 是 realm 下的嵌套对象**，不是扁平字段。把扁平字段当 realm
   表示发出去会得到 `400 Unrecognized field "host"`。必须包成
   `{"smtpServer": {...}}`。
3. **这个版本的 Keycloak 没有 `test-smtp-connection` 接口**（探测返回 404），
   所以验证只能靠实发 + 看日志，不能靠"测试连接"按钮。
4. **realm 的 PUT 是合并语义**（未出现的字段不动），所以只发 `smtpServer`
   不会冲掉注册开关、令牌寿命等设置 —— 已实测确认：改动前后客户端数量
   （platform 8 个 / business 14 个）与各开关均保持原样。

## 尚未解决：邮件里的链接指向哪

发信通了，但**邮件里的链接目前指向本机地址**（`cowork-a.localhost:8101` 这类）。
真实租户在外部点开是打不开的。要能用，得先有真实域名 —— 见下条。

## 阻塞项：三个域名还没注册

用户定的域名规则是 `自定义.pearnlycowork.com` / `.pearnlydms.com` / `.pearnlyerp.com`。
2026-09-17 查证：

```
pearnlycowork.com  → No match for domain（未注册）
pearnlydms.com     → No match for domain（未注册）
pearnlyerp.com     → No match for domain（未注册）
```

三个都没有 WHOIS 记录，DNS 也无任何记录。所以域名规则可以先写进设计，
但**要让租户真正打开网址访问，必须先注册这三个域名并配好解析与证书**。
