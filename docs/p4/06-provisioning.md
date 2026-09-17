# P4.06 开通执行：从发号单到"租户能登进自己的站点"

日期：2026-09-18。状态：**本机样板端到端跑通**（建站 → 装 App → 配身份 → 绑人 → 收尾 → 真机登录）。

## 这件事在计划里的位置

- **P3.02 原生站点生命周期**：用原生 `new-site` / `install-app` 建立幂等开通。
- **P3.08 域名路由与源站**：站点名即域名，按 Host 认站点。
- **P4.02 开通状态机 / P4.03 受限开通代理**：分步骤、可恢复、不虚报。

关掉了之前留下的缺口："租户设完密码没有落点"。

## 分工（这是本设计最要紧的一点）

| 角色 | 在哪跑 | 负责什么 |
|---|---|---|
| **控制面**（`pearnly_control`） | control 容器 | **要做什么、做到哪了**：拆步骤、落库、收状态 |
| **受限开通代理**（`scripts/sample-provision.sh`） | 宿主机 | **真的去做**：按步骤跑原生 bench 命令 |

为什么不让控制面自己建站：控制面跑在容器里，容器里没有 docker；给它 docker socket
等于给容器 root。P1.05 要求"部署代理独立、运行进程不持 root"——这就是那条约束在本机
样板的落地方式。代理**只认识下列动作**，不接受任意命令：

```
new_site / install_app / set_config / set_oidc / restart_web / finalize_site / bind_user / mark_active
```

## 数据模型

| DocType | 作用 |
|---|---|
| `Pearnly Deployment` | **公司 × 产品 = 一个 Site**。用 `DEP-…` 标识；**域名只是绑定关系**，换域名不改 ID（P1.04 合同）。存 site_name / 运行组 / 端口 / 本机地址 / 自己的 OIDC 客户端 |
| `Pearnly Provisioning Request` | 一次开通请求：幂等键（默认用发号单号）、状态、步骤 |
| `Pearnly Provisioning Step` | 子表：step_key / 动作 / 参数 / 状态 / 输出 / 时间 |

一个部署的步骤序列（有依赖顺序）：

```
new_site → install_core → install_product → set_oidc → restart_web → finalize_site → bind_user → mark_active
```

## 三条硬规则的落实

1. **幂等**：请求按幂等键唯一。重复 plan 不会重复建资源（重跑时返回同一个请求）。
   **重跑还会补齐新增的步骤** —— 流程演进后，已经开了一半的单子能靠重跑跟上，
   不用从头再来（补完会重算请求状态，否则它会停在 done 而新步骤没人执行）。
2. **不虚报**：任一步失败 → 该步 `failed`、请求转 `failed`/`partial`，**绝不写成 done**；
   执行器遇到失败就停下来不往下跳（后面的步骤依赖它）。
3. **不跳步**：`next_steps` 只发给"同部署里前面的步骤都已完成"的那一步。

## 每部署一个 OIDC 客户端（P3 合同的硬要求）

回调地址按站点域名算，**共用客户端的话第二个租户的回调就不在白名单里，登录走不完**。
所以开通时自动建一个客户端（client_id = 站点名），形态照抄样板里已验证的那个：
机密客户端、只开授权码流、PKCE=S256、**不开密码授权**。

开通要能建客户端，控制面的开户凭据必须有 `manage-clients` / `view-clients`
（原本只有 `manage-users`，实测 403）。

## 本机身份服务的两个前置条件（都在 `scripts/sample-keycloak-local.sh`）

1. **`sslRequired=none`（仅本机）**：本机跑 http，而 realm 默认 `external`，
   Keycloak 会给 `AUTH_SESSION_ID` / `KC_RESTART` 这些登录 cookie 加 `Secure`。
   ⚠️ **生产必须保持 `external`**（有 TLS），这条不要照抄。
2. **开户凭据的客户端管理角色**（见上）。

顺带说明一个容易误判的现象：同样的流程用 **curl 跑会失败**（"Restart login cookie not found"），
用**浏览器就成功**。原因是浏览器把 `*.localhost` 当作安全上下文，会正常收发 `Secure`
cookie；curl 的"localhost 例外"范围更窄（只认 `127.0.0.1` / `localhost` 字面量），
所以拿 `xxx.localhost` 走不通。**这是工具的限制，不是系统的问题** —— 因此登录验证
一律用真实浏览器做（`scripts/sample-verify-tenant-login.mjs`）。

## 域名

站点名 = 域名（Frappe 按 Host 认站点）。基础域名是一项配置 `pearnly_base_domain`：

| 环境 | 值 | 效果 |
|---|---|---|
| 本机样板 | `localhost` | 开通出 `qc65956.localhost`，macOS 自动解析，浏览器直接打开 |
| 生产 | `pearnly.com` | 开通出 `xxx.pearnly.com`（一层，现有通配证书覆盖） |

本机用 `localhost` 是为了能**真的用浏览器点开**；生产改一个配置即可，代码不动。

## 验证证据

一次真实开通（发号单 `ISS-2026-0039`，cowork + dms 两个部署，共 16 步）：

```
[执行] DEP-2026-0042:new_site → install_core → install_product → set_oidc → restart_web → finalize_site → bind_user → mark_active
        bind_user 返回 {"created":"created","bound":"bound","role":"System Manager"}
[执行] DEP-2026-0043: 同上（dms）
```

- **装对了**：`qc65956.localhost` = frappe + pearnly_core + pearnly_cowork；
  `qd65956.localhost` = frappe + pearnly_core + pearnly_dms。
- **真机登录（真实 Chromium，走完整 OIDC）**：两个站点都 PASS ——
  站点发起 → 身份服务登录 → 回跳 → 会话落在 `prov-65956@sample.local`，
  落点是 `/desk`（**不是设置向导**），**无 JS 报错**。
  截图：`docs/p4/evidence/tenant-login.png`。
- **幂等**：重跑 `run` 输出"没有待办步骤了"，不重复建资源。
- **收尾**：`finalize_site` 返回 `{"home_page":"desktop","is_setup_complete":true}`。

## 踩到的坑（都会复发）

1. **新站点一登录就落到设置向导**：新建站点的 `desktop:home_page` 默认是 `setup-wizard`，
   不摘掉就是之前那个"整页无限重载"。开通流程**必须包含收尾步骤**（现已内置）。
2. **"设置已完成"的开关不在 System Settings**，在 `Installed Application.is_setup_complete`。
3. **`bench console` 吞多行脚本**：绑定用户那步用 console 时静默失败，还被我自己的
   grep 吞掉错误、报成了成功。改成 `bench execute` 调服务端函数，并要求返回值里
   确实有结果才算成功。
4. **变量名紧挨中文会当变量名的一部分**（`$SITE_HOST）` → bash 报 unbound variable）。
   中文注释多的脚本里一律写 `${VAR}`。
5. **改了配置必须重启 web**：gunicorn 是 `--preload`，不重启读不到新配置。

## 尚未做（不掩饰）

1. **功能级强制还没实施**：勾选的功能只是**记录**在部署上，还没有变成角色/权限去挡访问。
   而且三个产品目前**没有自己的界面**（只有接口与清单），所以现在也没有可见的东西可挡。
   下一步是 P4.04 授权管理。
2. **还没打到服务器**：现在只在本机样板建站。控制面的「开户目标」里 `provision_url`
   是给服务器留的口子，服务器就绪后才用得上。
3. **没有 TLS / 真实 DNS**：本机是 `*.localhost` + http；生产要有证书与通配解析。
4. **资源准入未实施**：P4.07 要求 `cowork.boards` 才建 WeKan 且要算资源（每实例约 2 GiB），
   现在只保证不静默减少功能，真机配额要等服务器。
5. **失败恢复只做到"停住不跳步"**：自动补偿、重试策略还没写。
