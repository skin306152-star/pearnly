# 开户能跑通了，但"开户 → 使用"中间缺一环

日期：2026-09-18。用户问"现在能不能走开户流程实测了（除了真机 LINE）"，
实测答案是：**开户这一段能；走到建档不能，卡在租户没法自己配 DMS 连接。**

## 一、实测结果：开户这一段是真的通了

```
发号   → ISS-2026-0062 / 租户 T-2026-0063 / DMS 部署 flowdms（.pearnly.com 本机映射成 .localhost）
开通   → DEP-2026-0065 激活
```

开通执行链（`scripts/sample-provision.sh run ISS-2026-0062`）逐步都成功：

| 步骤 | 结果 |
|---|---|
| `new_site` | 建站 `flowdms.localhost` |
| `install_core` / `install_product` | 装 `pearnly_core` + `pearnly_dms` |
| `set_oidc` | 身份 issuer + 本部署客户端 + **realm 级改密凭据** |
| `restart_web` | 重启 web（gunicorn `--preload`，不重启读不到新配置） |
| `finalize_site` | `home_page=desktop`、装完标记、`language=th` |
| `bind_user` | 租户账号建好并绑定 |
| `set_roles` | 「DMS 管理员」+「Desk User」（**System Manager 被摘掉**） |
| `set_workspace` | 工作台建好、落地页指向它 |
| `mark_active` | 部署 → active |

**line 那步是 `SKIPPED`（该租户还没配 LINE 渠道）** —— 这跟"除真机 LINE"无关，
它是"这个租户压根没有渠道"，真机用的时候必须先在控制面配。

## 二、卡在哪：租户进去以后配不了 DMS 连接

新站点实测（同一容器里对比两个站）：

| | 新租户 `flowdms.localhost` | 我们手工配过的 `qd65956.localhost` |
|---|---|---|
| DMS 连接（`Pearnly ERP Endpoint`） | **0 条** | 1 条（`EP-00029`） |
| OCR 后端配置 | 未配 | `vertex` |
| LINE 渠道 | 未配 | 未配 |

**两条独立的缺口叠在一起**：

1. **没有任何东西会把 DMS 连接种进新租户**。老租户那条 `EP-00029` 是我手工
   `sample-seed-dms-endpoint.sh` 种进去的，不在开户流程里。
2. **租户管理员没权限自己配**。`Pearnly ERP Endpoint` 的角色权限只有
   `System Manager`，而开户流程 `set_roles` 恰恰把 `System Manager` **摘掉**、
   换成「DMS 管理员」。所以租户老板登进去，连这张表都看不见。

这不是 LINE 的问题，也不是 OCR 的问题 —— 是**开户流程和产品内的配置入口之间缺了一环**。

## 三、怎么补（照老项目）

老产品这里有一个**连接向导**（产品内的页面，不是 Desk 表单）：

· 用户填 DMS 地址 + 账号密码；
· 后端 `test_connection()` 真登一次、把主档下拉抓回来（车型/颜色/分店/顾问…）；
· 用户选建单默认值，保存成一条连接。

这套的**后端已经搬完并验过了**（见 [20](20-dms-booking-works.md)：
`MrerpDmsAdapter.test_connection()` + `fetch_masters()` 都跑通），缺的是**那个页面**。

所以补法是：把连接向导页做出来（前端 + 一个 whitelisted 的 test-connection 接口），
让「DMS 管理员」角色能用它建连接；顺带把这张表的读权限开给该角色
（向导页自己也会需要读）。

**这不是"重新设计规则"，是补老项目本来就有的那一环。**

## 四、当前可实测的边界（如实说）

| 环节 | 能不能实测 |
|---|---|
| 发号 → 开通 → 站点/账号/角色/工作台 | ✅ 能，本轮跑通 |
| 租户登录（统一登录页） | ✅ 能（账号已建在身份服务里） |
| **租户自己配 DMS 连接** | ❌ **卡住**（没入口 + 没权限） |
| 绑定码 → LINE 绑定 | ✅ 能（合成事件；真机要配渠道） |
| 发身份证 → 识别 → 查重 → 建档 | ✅ 能（在已配好的租户上验过；新租户要先补上一环） |
| 建单 + 附件 | ✅ 能（同上） |
