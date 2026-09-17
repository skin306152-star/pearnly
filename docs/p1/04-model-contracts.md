# P1.04 主体与产品模型合同

日期：2026-09-17。基线 `pearnly-app @ ecdacdba`，只读核对。

本文件是**合同**：后续实现按此执行；与现状冲突处标为"必须改"，并附证据。

## 一、现状（有出处，不是转述）

| 实体 | 现状 | 出处 |
|---|---|---|
| 公司/事务所 | `tenants(id UUID)` | `services/db_migrations/authz_schema.py` |
| 账号 | `users(id UUID)` | 同上 |
| 成员关系 | `memberships(user_id, tenant_id, role_id, status)`，**带 `UNIQUE(user_id)`** | `services/membership/schema.py:44-52` |
| 角色 | `roles(id UUID, permissions JSONB)`，权限码以 JSONB 数组存 | `services/authz/registry.py` |
| 账套 | `workspace_clients(id BIGSERIAL, tenant_id, user_id, name, tax_id, erp_endpoint_id)` | `services/workspace/store.py:89-104` |
| 账套范围授权 | `member_scopes(membership_id, workspace_client_id)`，`UNIQUE(membership_id, workspace_client_id)` | `services/db_migrations/authz_schema.py:113-121` |

## 二、必须改的三处（与目标要求直接冲突）

### 1. `memberships.UNIQUE(user_id)` 必须去掉

源码里带着一句注释：「Q1 砍 M:N · UNIQUE(user_id) · 1 人 1 事务所」——**这是上一轮刻意做成"一人只能属于一家"的决定**。

而本计划的要求是"同一账号可同时使用被授权的多个产品/公司"。两者不可能同时成立。

**合同**：改为 `UNIQUE(user_id, tenant_id)`。账号与公司是 M:N，同一账号在不同公司下的角色、账套范围、状态**各自独立**——一家公司撤权不影响另一家。

### 2. `workspace_clients.id` 的 BIGSERIAL 不能当全局主键

账套 ID 现在是每库自增的 BIGINT。**拆成每租户独立数据库后，各自的序列会撞车**，跨 Site 引用会指向错误账套。

**合同**：账套改用全局稳定 ID（UUID 或 `<deployment_id>:<seq>` 形式），自增列最多作为部署内的排序序号保留。迁移期旧 BIGINT 作为 `legacy_workspace_id` 保留映射。

### 3. `member_scopes` 只能按 membership 授权，颗粒度不够

现模型只能表达"这个人能看哪些账套"，无法表达"他在这个产品里能做哪些动作"。

**合同**：范围授权保留（账套级），动作授权继续由权限码 + 角色表达；两者正交，不能互相替代。

## 三、稳定 ID 与主体定义

所有 ID 一律**不派生自邮箱、公司名、域名或自增序列**——这几样都会变。

| 主体 | 定义 | 稳定性要求 |
|---|---|---|
| `principal` | 平台账号（一个人） | 终身不变；改邮箱、改公司名都不影响 |
| `company` | 商业客户（公司/事务所） | 改名不迁移、不换 ID |
| `deployment` | 公司 × 已开通产品 = 一个 Site | 用 `deployment_id` 标识；**域名只是绑定关系，不是主键**，换域名不改 ID |
| `membership` | 账号 × 公司 | 唯一键 `(principal, company)`；**同一账号在不同公司的成员关系彼此独立** |
| `scope` | 部署内的账套/工作区 | 全局稳定 ID，见上文第 2 条 |
| `connection` | 外部连接（ERP 端点、LINE 渠道、Google 凭据） | 归 deployment 所有；凭据只存引用，不落明文 |
| `session` | 登录会话 | 用 Frappe 原生 session，外加受控会话登记；**不新建第二套万能 JWT** |

### 三个必须成立的行为

1. **一个账号可同时打开已授权的多个产品**，互不踢线。
2. **未勾选的产品拒绝进入**，且拒绝原因可区分"未购买"与"未授权"。
3. **平台身份不继承业务权限**：平台账号（管理后台用）默认不能进任何业务 Site；平台管理员也不能自动成为 WeKan 管理员（现行实现的 E11 缺陷，必须移除）。

## 四、ProductManifest（产品清单）

每个产品一份清单，是"这个产品允许做什么"的唯一来源。**运行时不得自报扩大权限**：清单之外的入口一律不注册，清单里没写的 DocType/权限码不允许使用。

```yaml
product: erp                     # cowork | erp | dms | control
version: 1
bench_group: erp                 # 运行组，与 compose 对应
apps: [pearnly_core, pearnly_erp_portal]
entry_prefixes:                  # 只允许这些前缀的路由被挂载
  - /api/v1/erp/
  - /api/v1/me/                  # 只读自身身份，跨产品一致
modules: [sales, expense, inventory, recon, receivable, pos]
features: [inventory, purchase, sales, recon, vat_compute]
foreign_keys_allowed: []         # 允许引用的其它部署对象，默认空
must_not:
  - 访问其它 deployment 的数据库
  - 使用清单外的权限码
  - 接受平台身份直接通行
```

### 与现有权限体系的对应（不另造一套）

- 权限码格式沿用 `<模块>.<资源>.<动作>`，13 组不予扩充；模块开关沿用 `tenant_modules`。
- **会计不迁移**，因此 `acct.*` 五个权限码与 `accounting` 模块一并移除。
- **收发存报表要保留**：`stockcard.report.view` 与 `stockcard.opening.manage` 当前映射到 `accounting` 模块，**改挂 `inventory`**，否则 ERP 菜单里的报表会被模块闸误关。
- `tax.filing.*`（报税）不迁移；但 **VAT 计算要留**，因为对账依赖 `services/vat`。两者是两件事。
- 6 个角色沿用：`owner`、`admin`、`accountant`、`clerk`、`viewer`、`cashier`（cashier 只走 POS 令牌——而 POS 不迁移，故 cashier 在新系统内不再需要；保留其码位以备将来）。

## 五、运行组与限额合同

进程数固定，不随请求量自动扩。

| 项 | 约定 |
|---|---|
| 每产品运行组 | web 1（gunicorn 2 worker × 4 线程）、worker 1、scheduler 1、socketio 1 |
| 全机重任务并发 | **初始 1**，即 OCR / 浏览器类任务同时只跑一个 |
| 数据库 | MariaDB 单实例，**每 Site 一个库、一个独立账号**，账号无任何跨库权限（已实测） |
| 日志与临时文件 | 每组独立卷并设大小上限；不允许无限增长 |
| 网络与凭据 | 每组独立网络命名空间与独立凭据；不共享全库读账号 |
| 内存上限 | 按 [01-version-matrix.md](01-version-matrix.md) 修正后的表；WeKan 单实例 2 GiB 是实测下限 |

**注意**：样板启动配置**不等于**身份与业务验收。样板跑通只说明组件能启动。

## 六、迁移保护（不得在迁移中关闭）

账套归属检查、LINE 验签、ERP 团队校验、Companion 本机写限制、会话票据原子消费、幂等保护——这些现有保护在切换完成前一个都不关。

## 七、隔离测试环境（已交付的部分）

### 结构

- 2 个商业客户 × 3 产品 = **6 个业务部署**：`cowork-a/b`、`erp-a/b`、`dms-a/b`，各带独立库与独立账号（已实测跨库拒绝）。
- 1 个控制面部署：`control.localhost`（只在开发机）。
- 身份服务：Keycloak 26.7.4 + 独立库。
- 协作：2 个租户各一套 WeKan + 独立 Mongo。

### 六种人设（种子脚本 `scripts/sample-seed-personas.py`）

在 `cowork-a.localhost` 建了平台管理员、租户管理员、普通员工、只读、已撤权五种账号；"未邀请者"不建号，用于反面验证。

**登录行为实测**：

| 场景 | 结果 |
|---|---|
| 员工 + 正确密码 | `200 {"message":"Logged In"}` |
| 员工 + 错误密码 | `401 Invalid login credentials` |
| 已撤权（账号停用） | `401 User disabled or missing` |
| 未邀请者（无此账号） | `401 Invalid login credentials` |
| 平台管理员 | `200` —— **这就是问题所在** |

### 诚实说明这份环境还没证明什么

1. **平台账号目前能登入业务站点**（最后一行）。"平台身份不得进业务 Site"这条要求**尚未实现**——现在用的是 Frappe 原生用户体系，没有产品级授权。这是 P2 的任务，不是样板能关掉的。
2. 六种人设目前只是 Frappe 原生账号，**没有** Principal / Membership / Scope 的产品语义，也没有"未勾选产品拒绝""错租户拒绝"的可验证行为。
3. 站点仍是裸 Frappe，未装产品 App，**没有云代码归属也无从谈起**。
4. 因此：环境结构已具备，**授权验收一项都还没有通过**。
