# 本地样板地址

日期：2026-09-17。

## 为什么不能用 127.0.0.1 打开

**Frappe 是按域名找站点的**：请求进来先看 `Host`，再去 `sites/` 里找同名目录。
用 `http://127.0.0.1:8104/...` 打开，它会去找一个叫 `127.0.0.1` 的站点，找不到就回
“127.0.0.1 does not exist”。这是预期行为，不是页面没做好。

必须用**站点域名**访问。这些域名在本机自动解析到 127.0.0.1（macOS 对 `.localhost`
的处理），不需要改 hosts 文件。

## 能用的地址（实测全部 200）

| 用途 | 地址 |
|---|---|
| **控制面·发号** | http://control.localhost:8104/pearnly-issue |
| Cowork·租户 A | http://cowork-a.localhost:8101/login |
| Cowork·租户 B | http://cowork-b.localhost:8101/login |
| ERP·租户 A | http://erp-a.localhost:8102/login |
| ERP·租户 B | http://erp-b.localhost:8102/login |
| DMS·租户 A | http://dms-a.localhost:8103/login |
| DMS·租户 B | http://dms-b.localhost:8103/login |

端口是按运行组分的：8101 cowork、8102 erp、8103 dms、8104 control。同一个组里的两个
站点共用端口，靠**域名**区分——这正是"一个部署一个域名"的形态。

## 登录账号（样板用）

| 站点 | 账号 | 密码 |
|---|---|---|
| 控制面 | `owner`（平台 realm） | `KcLocal123!` |
| 业务站点 | `tenant-admin`（业务 realm） | `KcLocal123!` |

注意：平台账号在业务站点上**进不去**（两个 realm 隔离），这是设计要的。

## 浏览器里看到的"未登录就跳登录页"

业务站点是 Frappe 自带登录页。我们自己的统一身份入口是
`/api/method/pearnly_core.identity.begin_login`，登录后会跳转到身份服务。
产品界面还没做，所以现在先用 Frappe 自带页面验证会话与绑定。
