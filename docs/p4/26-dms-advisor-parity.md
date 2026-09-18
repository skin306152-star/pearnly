# DMS 顾问归属按线上返工：现抓名册 + 钉死必须实时存在

日期：2026-09-18。承接 [25-dms-cards-i18n.md](25-dms-cards-i18n.md)。
线上基准 `501589f0`。这是剩余批次里的**第 2 批**。

## 一、又抓到一处"搬了旧状态"

新平台 `dms_advisor.py` 里有两处是从**本机旧副本（`e4505459`）**逐字搬的，
线上 `b78697f3`（2026-09-12）已经改掉。那次提交的主题写得很直白：

> **实时校验完整主档关系与变更，禁止旧值及首项兜底**

顾问栏直接决定**月底提成算给谁**（它是我们 POST 上去的，真机取证"填谁存谁"），
所以这两处不改就是记错钱：

| | 旧写法（我搬的） | 线上（改成） |
|---|---|---|
| 无传入快照时 | 先读缓存，不中才现抓 | **一律** `get_masters(force_refresh=True, require_complete=True)` |
| 钉死的 id 不在名册里 | 用配置里存的 `advisor_name` **照常放行** | **拦下**（`None`），名字一律按 id 从实时名册重解 |

第二行是钱窟窿：名册里已删除/改名的人，会继续被填进提成栏。

线上对应契约（`tests/unit/test_dms_advisor_match.py`）：

```
test_pinned_blocks_when_live_fetch_fails     名册取数失败 → 不得用旧配置放行
test_live_bundle_refreshes_pinned_name       存的名字不作数，取实时值
test_live_bundle_blocks_deleted_pinned_advisor  名册里没了 → 拦
test_forced_refresh_keeps_exact_layer_first  现抓，且精确层仍优先
```

## 二、改了什么

`apps/pearnly_dms/pearnly_dms/dms_advisor.py`：

1. `resolve_operator_advisor`：无传入快照 → 一律现抓（`force_refresh=True,
   require_complete=True`），抓不到就是空表 → 走拦截。**不认缓存**：拿一份可能
   缺行/陈旧的名单去匹配，等于把人记错。
2. `_pinned_advisor`：id 必须在**当前**名册里；文字取**实时**值；
   **读取失败不得拿存下来的配置放行**。

顺带清掉两处已无用的读取（`advisor_name` 兜底、`read_fresh_masters`）。

## 三、验证（25/25 通过）

新增 `scripts/sample-verify-dms-advisor.py`（+ `.sh` 包装），
用例按线上 `tests/unit/test_dms_advisor_match.py` 迁移：

```bash
scripts/sample-verify-dms-advisor.sh dms     # 合计: 通过 25 / 失败 0
```

其中"名册取数失败 → 拦截"与"钉死时名字取实时值"两条**在修复前必然失败** ——
正是本次的漂移点，不是补几条走过场的断言。

覆盖：启发层（code 列 / name 列 / 大小写 / 层间优先 / 多命中弃权 / 空值）、
精确层（员工表命中即定论 / 员工没顾问资格要拦不许猜 / 空员工表回落启发层）、
端点解析（现抓参数 / 解不出账号就拦 / 钉死四态）。

## 四、已核对为等价的（未改）

`dms_employees.py` 与线上 `services/erp/dms_employees.py` **逐行等价**
（只重写了注释）：`dt::`/`ndt::` 结果体判据、`None`（取数失败）与 `[]`（真没人）
必须分得开、`_fetch_via_admin` 只作"配没配"判据、`match_by_login` 只比 login 列。
用脚本去掉注释与 docstring 后做逐行比对，除架构适配外无差异。

## 五、两处**架构差异**（不是漂移，记下来免得下回又当 bug 查）

| | 老产品 | 新平台 | 为什么可以不同 |
|---|---|---|---|
| 登录名存放 | `config.username_enc`，Fernet 解密 | `Pearnly ERP Endpoint.username` 是 **Data（明文）**，密码走 Frappe `Password` → `__Auth` | 老产品是**单库多租户**（带 `tenant_id`），一个口子漏就跨租户；新平台**一站一租户**（独立站点 + 独立库），租户内读到的本来就是自家账号。要不要加固是另一个决定，不在这批偷偷改 |
| 端点形态 | `ep["config"]` dict | Frappe DocType 文档 | 平台不同，语义等价 |

## 六、本批**没做**的（记成缺口，别当已完成）

**花名册保存钉死归属时缺一层校验。** 老产品 `services/dms_roster/advisors.py`：

```
list_options(老板端点)  → 只外露 [id, code, name]（tel 是员工私人号码，不下发）
                        取数失败 → None（≠ 空名册，前端要能分开"没人"和"取不到"）
_resolve_pin(owner, id) → 不在名册 → dms_roster.invalid_advisor
                          取数失败 → dms_roster.advisors_unavailable
                          名字一律服务端按 id 解，不信客户端传的
merge_into_config       → booking_defaults **按键改写**，不整包覆盖
                          （同 dict 里还住着 delivery_days 等租户配置）
```

新平台 `roster.py` 直接收下调用方给的 `advisor_id` + `advisor_name`，没有这层。

**危害有限，所以排后面**：下单侧 `_pinned_advisor` 这次已按实时名册重解，
错的 id **不会算错提成**，只会在下单时被拦 —— 属于"早点告诉人"（UX），
不是钱窟窿。已在该文件两处写上明确注释，不会静默。

## 七、下一批

第 3 批：**客户写入链**（`client_ops` / `client_forms` / `company_banks` /
`docno` / `intake`）。同样先做逐行比对，**只认线上代码，不认注释**。
