# 白名单接口缺守卫修复（10-27，主会话派，随本批上线）

来源：验收窗口回归基线 B 的接口权限矩阵（`pearnly/regression-baseline` 53042760，
`docs/work/2026-10-27-regression-baseline-漏出入口.md`「B 接口权限矩阵的发现」，复现 `scripts/regression/repro_unguarded.py`）。
分支 `pearnly/unguarded-endpoints-fix`，基线 main 5c866e1c。

## 修了什么

| 接口 | 问题 | 改法 |
|---|---|---|
| `pearnly_dms.records.push_log.api_list` / `api_status` | 不核权限、筛选原样取请求，任何登录账号列出全部推送记录（含身份证号 business_key、报错里的客户手机号） | 删掉对外入口：仓库里没有调用方（推送记录页走 `query.records.list_records`，按花名册权限）；服务端内部的 `status_of` 照旧 |
| `pearnly_core.endpoints.api_list_endpoints` / `api_test_connection` | 任何登录账号拿外部连接清单（地址、登录名、管理员登录名），`owner_user` 能指定别人；测试连接不核权限读连接 | 删掉对外入口：没有调用方（DMS 连接页走 `pearnly_dms.connections.api`，有守卫）；`list_for` / `test_connection` 留给服务端内部 |
| `pearnly_control.line_admin.upsert_channel` / `webhook_url_of` | 按类全仓查出（矩阵没覆盖控制面）：控制面任何登录账号能改写任一部署的 LINE 渠道密钥 / 令牌（`ignore_permissions`） | 加控制面运维守卫（System Manager，同开户 / 拆除的 `issues._require_operator`） |
| `pearnly_erp_portal.inventory.api.api_next_stock_entry_number` | 按类查出：任何登录账号能看各类单据的流水号（业务量） | 只给能开出入库单 / 盘点的人（stock.in / stock.out / count.write） |

为什么删而不是加守卫：这四个对外入口是照老产品搬来的占位，仓库里（前端、CC、脚本）没有任何调用方，各产品页面都走自己有守卫的接口；
删掉对外入口就没有可被绕过的守卫，返回字段也无从泄漏。

## 按类全仓查与防再犯（CI）

- `tests/endpoint_guards.py`：从每个非匿名的 `@frappe.whitelist()` 接口出发，沿本仓代码的调用（按 import 解析；模块级函数、类方法、
  模块级实例的方法、`self.x`、`super().x`、当参数传进去的函数、按点号路径字符串交出去的函数，深度 6）找守卫。守卫口径同矩阵的
  守卫名（require / guard / has_permission / only_for / check / allowed / deny / permitted / can_），另把 `frappe.get_list`、
  直接抛 `PermissionError` 算守卫。全仓 5 个 App 共 638 个白名单接口（其中匿名 51 个另由 guest_endpoints 登记），跑一遍约 2 秒。
- `tests/test_unguarded_endpoints.py`（进 CI）：找不到守卫的只许是 `tests/baseline/unguarded_endpoints.toml` 登记的本人数据（own）/
  公开信息（public），写明理由；新增即失败，登记了但已有守卫或不在了也失败。登记 16 条：矩阵查出的 8 条本人 / 公开项，加上静态查出、
  矩阵测不出的 8 条（登出、改密码发起、文案、公钥、对账任务状态只回本人等），逐条核过代码。
- `tests/test_endpoint_guard_fixes.py`（进 CI）：查出的几处不再对外 / 控制面渠道接口别人拒、运维照常。
- 修前代码上两个用例都失败（已验）；动态的接口权限矩阵仍在回归实验室跑，静态检查是它接进 CI 的离线一半。

## 生产有没有被异常调用（只读）

一次 SSH，只看三个产品前置代理（`pearnly-production-{erp,cowork,dms}-frontend-1`）的访问日志，不进数据库容器：
- 日志从 10-25 部署重建容器起（约 2 天）；更早的已随容器重建没了，**10-25 之前是否被调用无法判断**。
- 这 2 天里 `push_log.api_list / api_status`、`endpoints.api_list_endpoints / api_test_connection` **0 次**。
- `api_next_stock_entry_number` 5 次，同一个来源 IP、全是 ERP 出入库单表单正常打开时取号（200）。
- 控制面（`line_admin`）不在生产（管理后台留在本机），生产没有暴露。

## 检查

- 本地同名闸门 134 项全过；分支 Development checks 见合入记录。候选离线检查两次被系统杀掉（137，本机同时跑着回归实验室的 17 岗位快照，
  内存不够），实验室空下来后重跑。
- 验收窗口在回归实验室复核（b139d19d）：复现脚本（DMS 站点、只有 Desk User 的账号）`push_log.api_list`、`endpoints.api_list_endpoints`
  都回「Failed to get method」；三产品接口矩阵 5,354 格与改前 5,402 格逐格比对：删掉的 4 个接口消失，`api_next_stock_entry_number`
  只有库存岗与管理员放行、其余 7 个岗位与 Guest 被拒，别的接口没有变坏；「无守卫正常返回」的全部在登记表里（下面第 2 条除外）；
  在临时副本里故意加一个用 `get_all` 的无守卫接口，`test_unguarded_endpoints` 失败并点名，改成 `get_list` 就过。
- 验收要改的 1 处已改：`scripts/sample-verify-foundation.py` 改调 `endpoints.test_connection`；全仓（apps / frontend / companion /
  scripts）再搜旧名只剩 `endpoints.py` 里的说明注释。

验收记下的不阻塞项：
1. `pearnly_core.password.refuse_on_native_account`：静态检查把直接抛权限错算守卫，矩阵在没配 `pearnly_native_account` 的站点上看到
   「无守卫正常返回」（不回数据，无害）——口径说明写进登记表抬头注释，不作为登记项（登记了反而被「已有守卫」那条判失败）。
2. 静态检查把 `get_list` 算守卫，前提是 DocType 权限本身收紧：Cowork 还有 39 张表给 All / Guest 读权限，work-followups 收紧那批合入后
   由验收用矩阵对 Cowork 复测。
3. 控制面 `line_admin` 两个接口实验室没有控制面站点，只有单元测试覆盖（别人拒、运维照常）。
