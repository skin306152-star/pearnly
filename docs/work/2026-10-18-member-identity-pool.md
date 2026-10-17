# 2026-10-18 Cowork 团队加成员：按站点实际签到的账号池建号

状态：实现与本地检查完成，待开发窗口集成（分支 `pearnly/member-identity-cowork`，基线 `909b858c` = 当时 `origin/main`，
工作树 `.claude/worktrees/member-identity-cowork`）。未推送、未合并、未部署。

## 现象（生产只读，开发窗口 2026-10-18）

- 03:05 用户在 testcowork「团队管理」加成员，两次都是 417，站点只显示「请稍后重试」。
- 没有建出号：控制面 `Pearnly Member Identity` 登记数仍是 1（唯一一条是 erp 产品）。

## 两处根因

1. **登记表拒收 cowork**（930299ae 已修）：登记表 `product` 选项只有 erp，成员服务按 cowork 写登记时被表校验拒绝
   （`Product cannot be "cowork"`），所有 Cowork 租户加成员都失败。
2. **建号按产品选池，不看站点实际签到的池**（本记录）：testcowork 是 Cowork 产品，却签到业务池 `pearnly-business`
   （老租户，2026-10-04 查实；erpcowork → `pearnly-cowork`，mrerp / testerp / ris / testdms → `pearnly-business`）。
   站点 `member_client._call`（create / reset-password）不带签发方，控制面 `execute → create / reset_password` 用
   `MemberProvider(product)` 按产品选池（cowork → `pearnly-cowork`）。只修第 1 处，testcowork 加的成员会建在 Cowork 池，
   人却在业务池登录，登不进去。LINE 关联那条路 10-04 已按池修过（`pool_prefix(product, issuer)`、`realm_prefix(realm)`、1088ded0），
   这次照同一做法统一。

## 改法

| 层 | 改动 |
| --- | --- |
| 站点 `pearnly_core/account/member_client.py` | create / reset-password 与 LINE 关联一样带本站 `pearnly_oidc_issuer`（`_issuer()`）。 |
| 控制面 `member_service.execute` | 把签发方传给 `create` / `reset_password`（接口 `member_api.execute` 本来就收 `issuer`）。 |
| `create` | `pool_prefix(product, issuer)` 选池：只比账号池名（站点给公开地址、控制面配内部地址），只认已配置的两个池，否则 `not_permitted`，在写任何登记前拒绝；没带签发方按产品（老站点 / 旧客户端）。新登记记下池名 `realm`（存池名，不存地址）。已有登记的重入比对把池也算进去：同一请求换了池 → `conflict`，不会在另一个池里建号；同池重试照旧续上。 |
| `reset_password` | 按登记记着的池改（`realm_prefix(row.realm)`）；没记池的老登记按产品默认的池，与改动前一样。站点带了签发方时，只能改它自己签到的那个池里的号，否则 `not_permitted`（在记改密意图之前）；没带签发方照登记。 |
| 租户删除 `_purge_preflight` / `purge_members` | 每条登记按记着的池（老登记按产品）取统一身份连接，按池复用；记着的池已不在配置里 → 失败关闭（`unavailable`），租户不冻结。 |
| 登记表 `Pearnly Member Identity` | 加只读字段 `realm`（Data，标签沿用 LINE 关联表的 "Identity Realm"），`modified` 改为 `2026-10-18 04:00:00`，migrate 同步。 |

不改任何界面文案。

**不回填老登记的池**：生产现在只有 1 条 erp 登记，它是改动前按 `MemberProvider("erp")` 建在业务池的，"没记池按产品默认"正是它所在的池；
回填不改变任何结果，回退兜底本来也要保留，所以不加补丁。以后若改了产品默认的池，再用原生 patch 按当时的池补齐。

## 验证

| 时间 | 代码 | 环境 | 场景 / 命令 | 结果 |
| --- | --- | --- | --- | --- |
| 10-18 | 只加测试、实现未改（基线 930299ae） | 本机 Python 3.12 | `tests/test_member_identity_products.py` | 新增 3 条失败：登记表没有 `realm`、站点 `_call` 不带 `issuer`、`execute` 不把签发方传给 create |
| 10-18 | 同上 | 生产镜像 `ebba5a40` + 本工作树五个 App 源码，断网 | `pearnly_control.account.test_member_service`、`pearnly_erp_portal.team.test_identity` | 新增两池用例 9 条中 8 条失败（Cowork 站点签到业务池却建进 Cowork 池、未记池、改密 / 清理找错池、未知池照建、换池重入照建、记着的池不在配置里仍改密）；站点侧 2 条失败（请求里没有 `issuer`）。"老登记按产品"那条改前就通过——它是保持旧行为的护栏 |
| 10-18 | 实现后 | 同上 | 同上 | 4/4、43/43 通过 |
| 10-18 | 实现后 | 本机 Python 3.12 | `check-architecture.py`、`check-copy.py --report`、全闸门 `gate.sh`（development.yml 同名步骤） | 架构 PASS 0 违规；文案新增 0、改动 0（"Identity Realm" 是已有词条）；全闸门 91 步全过 |
| 10-18 | 实现后 | 本机 colima，生产镜像 `ebba5a40`，断网 | 候选离线检查 `cand-local.sh offline` | 72 步全过（含 `test_member_service`、`test_identity` 所在的 220 条单元测试组、`test_teardown_members`），退出码 0 |

两池用例（`TestMemberPools`）用真实 `MemberProvider` + 两个内存统一身份池 + 内存登记表：
Cowork 站点签到业务池 → 号在业务池、登记记 `pearnly-business`、重试不建第二个号；签到 Cowork 池 → 号在 Cowork 池；
不带签发方 → 按产品选池并记池；改密码与租户删除按记着的池；老登记（无池）按产品；未知池在写之前拒绝；
另一池的站点改密被拒；换池重入 `conflict`；记着的池不在配置里失败关闭。

`tests/test_release_member_identity.py`（真实 Keycloak 发布实验室，需 `PEARNLY_RELEASE_LAB=1`，不在候选离线检查里）同步补了
"登记记池、未知池拒绝"的断言，并修正早已过时的 `_member_key(tenant, email)` 调用（9-26 分池后要带产品）。**本次未运行**。

| 层级 | 状态 | 依据与未覆盖项 |
| --- | --- | --- |
| 实现 | 完成 | 本分支提交 |
| 本地验证 | 通过 | 上表 |
| CI | 未执行 | 未推送 |
| 部署 | 未执行 | 见下 |
| 外部业务结果 / 用户验收 | 未执行 | 部署后请用户在 testcowork 重试加成员并用新号登录 |

## 部署注意

- 控制面（计费站点）有表结构变化：随基础层 `activate-foundation.sh`（计费备份 + migrate、换 billing-web）同步 `realm` 列；
  漏了基础层，billing-web 停在旧代码，问题不消失。站点侧只是代码（`pearnly_core`），ERP / Cowork 产品激活即可，不需要站点迁移。
- 先后顺序都安全：只换了站点 → 旧控制面忽略签发方，照旧按产品；只换了控制面 → 站点不带签发方，按产品，与现在一样。两边都换了才生效。
  migrate 先于换 billing-web，新代码不会读到没有 `realm` 列的表。
- 部署后：请用户在 testcowork 重试加成员，回读控制面登记 2 条、新登记 `realm = pearnly-business`、号在业务池、用新号能登录 testcowork。
  erpcowork 不需要动（签到 Cowork 池，与产品默认一致）。
- 回退：billing 回上一镜像即可，`realm` 列留着不影响旧代码；回退后 testcowork 新加的号仍在业务池，旧代码改密会按产品去 Cowork 池找而失败，需要时再前滚。
