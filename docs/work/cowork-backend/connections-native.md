# Cowork 原生连接与目录存储（第八批）

本批把连接配置、显式使用权、可信目录的版本与刷新申请落到自有 Frappe DocType。提供可调用的原生后端服务；没有前端菜单，没有 MRERP 网络采集器、Express Companion 身份/心跳消费者、外部连接测试或订单写入。`requested` 仅表示持久化申请；`collector_available=false`，不能宣传成已刷新或 ERP 已连通。

## 基准与范围

- 新树开工 `0e5cd94e84f444c2246b57b4838263163c9cdfc3`，路径 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`。
- root 开工线上 gate PASS：旧承流版本仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，完整 endpoint-config 摘要与第七批交付一致；读取固定快照，不以本机旧项目 HEAD 替代线上证据。
- `baseline-eighth-start.json` SHA256：`44f3d0135297822ef31314dbfbbe5a4a750c94998e6701da55554d1291cc4aaf`。
- `push-runtime-eighth-start.json` SHA256：`092b9b9de41a0b63014b0a0689a353c08b8eadfc18f254435ec3638e72a55089`。
- 所属 App 为 `pearnly_cowork`；新域 `connections` 单向依赖既有 `targets` 纯合同、Core feature读取、Frappe原生持久化/Password。push application/native单向调用连接服务；连接域不导入推送流程。

| 来源 | 新落点/保留点 |
|---|---|
| `routes/erp_endpoints_routes.py` | `connections.api/service/model`：配置、启停、凭据不回显、服务端范围；不迁 DMS/Webhook/Flowaccount |
| `services/erp/endpoint_config.py` | DTO不含明文或密文凭据，仅has标记；不再展示token头尾 |
| `services/erp/target_projection_store.py` | 原生Snapshot append-only、Connection内当前head、同内容不增版本、完整body保存；纯normalize仍是targets/projection唯一入口 |
| `services/erp/target_refresh.py` | 原生Refresh保存申请、最新请求与配置版本约束；采集器/lease/实际调度尚未接 |
| `routes/erp_target_projection_routes.py` | `targets(name)`只读安全目录、`request_refresh/refresh_status`；任何刷新事实都不可由浏览器上传 |
| `services/erp/selected_account.py` / `target_catalog_evidence.py` | `read_for_push`生成当前同事务 TargetAccess/TargetEvidence；账户/root规则复用第七批纯层 |

上述源码哈希已在本次只读审计核对：endpoint routes `f85eb11542bd183ca09918aeafd7538c9019bdd0e14a888562e6d06fbb07682a`，projection routes `d1625f3748d6a397b0933e06415a95e4fd75983833734ae05a32d900899bc6ac`，refresh `33eb4ee62e0d762965c83adcbd6c100edcc50f1649c7aa94d44d1099ba0a53ff`，projection store `cfb97b650fb45595073ecc576f34e167d233fb6ed3ccd9bcaa3c0b6a136be63d`。

## 原生数据和权限

三表均在 `pearnly_cowork/doctype`：

为持久保存合并前后所有幂等键，Connection增加隐藏只读`refresh_request_aliases`（label: Refresh Request Aliases），由同一连接锁事务维护operation UUID→request_id/request_hash。需原生migrate新增字段；更新配置清head但不清alias，旧revision的旧key明确拒绝，不可被重新绑定到新head。升级前无alias的历史请求仍按其原operation UUID恢复并补齐alias。alias没有通用CRUD/API出口；历史保留/归档策略尚未实现，不自动删除审计关联。

- **Cowork ERP Connection**：workspace/client 原生Link、adapter、label、enabled、record_revision、allowed_users、脱敏配置JSON；三种原生Password字段；binding_generation/bound_account_set/bound_profile_key只允许内部维护，公开创建固定legacy generation=0。保存更新锁连接、检查expected_revision；配置更新清除当前heads，旧Snapshot/Refresh仍保留。
- **Cowork ERP Snapshot**：connection/revision/generation/scope、projection_revision、source_hash/content_hash、normalized body、可选preparation、UTC观测与安装时间。业务服务只新增，通用修改和删除拒绝。
- **Cowork ERP Refresh**：UUID request_id、connection/revision/generation/scope/actor、requested/succeeded/failed（历史superseded值仅保留兼容）、结果版本和UTC时间。当前无collector领取接口；只有可信内部install可完成申请。

配置管理需要当前 `master_data` + 实际 `Cowork Admin` 角色；普通使用需要 `intake` + `Cowork Intake`/`Cowork Admin`。日志服务单独校验 `push_log` + `Cowork Push Log`/`Cowork Admin`，日志权限不会授予准备/刷新权。Admin只有intake而无master_data时，可按普通使用路径读取已分配连接，不能修改配置。

每次检查本Site产品、feature、User.enabled、真实Has Role、公司/客户启用与User Permission、Connection.allowed_users；无浏览器传入authorized布尔。所有使用者，包括Cowork Admin，都必须显式列入allowed_users；管理权可看配置，不等于推送使用权。此为新原生明确授权边界，不照搬旧个人owner/shared入口混合规则。没有额外授予平台Administrator客户业务角色；隔离测试建立实际Cowork Admin合成账号。

公司/客户精确匹配，包括明确无客户的范围。源 `workspace_client_id` 是做账公司，映射 `doc.workspace`，不是买方 `doc.client`。endpoint与TargetAccess的workspace_client_id都使用workspace。框架Site就是租户，不接收客户端tenant。

三类专用DocType不开放通用list/get/save/REST/report/share。All/Guest只读metadata用于确保Frappe16不会在无role read时直接走legacy DocShare列表；query hook和permission hook始终抛拒绝，controller实体object capability只授予内部write，JSON flags无法伪造。禁止DocShare新增/更新，既有share也不能恢复native读。root登记hooks，本域不修改共享Core。

## 接口

`connections.api`：

- POST `configure(workspace,adapter,request_key,label,config,client=None,users=None,name=None,expected_revision=None,enabled=True,credentials=None)`。新建默认users为当前管理员，更新未传users保留已有授予；config为明确的完整管理配置。credentials独立 `{username,password,token}`；缺席保留、空串清除、拒绝掩码当密码。创建按site/actor/scope/request_key稳定命名；同key异内容拒绝。更新按revision防覆盖。
- GET `detail(name)` / `records(workspace,client=None,start=0,limit=50)`：安全配置DTO、已配置标记、版本和`collector_available=false`，不含凭据、目录原始数据或preparation。
- GET `targets(name)`：当前endpoint head status、refresh_status/request_id、revision、白名单account_sets属性。pending可见但不冒充fresh，选择时仍必须过targets证据闸。不会返回供应商、银行、完整collector body或prep。
- POST `request_refresh(name,request_key,account_set_key=None)`；GET `refresh_status(name,request_id)`。默认endpoint目录范围，Express账户scope按源Windows规则归一。相同key重放原请求及其当前结果，配置版本变化或同key异scope拒绝。同scope/connection revision/generation仍requested时，不同key合并到原request_id并刷新requested_at，符合固定源target_refresh.py:111-122；完成后新key才新建。

内部服务（均未whitelist）：

```python
loaded = read_for_push(connection_id, workspace, client=None, actor=None, lock=True)
# LoadedConnection(doc, endpoint, access, evidence, revision)
target = freeze_target(loaded.endpoint, access=loaded.access, evidence=loaded.evidence, ...)
context = preparation_for_target(loaded, target)
```

`read_for_push`先`FOR UPDATE`锁Connection；之后head、Snapshot、Refresh均current read。所有install/refresh/config改动遵守同一连接锁，无内部commit，推送统一connection→history→writer/record顺序。授权User/role/scope也用current读取，不依赖之前列表或缓存授权。`preparation_for_target`重新读取同锁连接并校验版本，不能用提交前遗留LoadedConnection跨版本准备。

`read_for_log(connection_id,workspace=None,client=None,actor=None,lock=False)`只做当前日志权限/范围/显式用户检查并返回DTO；可读停用连接的历史日志。`credentials_for_use(loaded)`只供未来已授权出站适配器，重新校验连接版本后读原生Password；不对外RPC开放。

`install_snapshot(name,observation,request_id,expected_revision,preparation=None)`只接受可信服务端collector结果：先锁连接，检查启用、配置/绑定版本、请求是该scope最新请求、adapter一致，复用targets规范化。无RPC、无客户端证明、无collector伪造。相同projection+prep内容不增加版本；同目录但prep变动必须产生新版本。旧请求、旧配置回执不能安装，已完成请求不可换内容。快照正文上限16MiB、prep上限8MiB，属于本地持久化资源限制，超限明确拒绝。

## 完整的准备上下文

可信preparation最小形状：

```python
{
  "complete": True,
  "account_set": "C:\\BOOK", "root_key": "D:\\APP",
  "mappings": {"accounts": [], "clients": []},
  "supplier_profiles": {}, "bank_index": [], "reported_config": {}
}
```

- mappings沿源：accounts条目含erp_type/pearnly_category/erp_code；clients条目含client_id/erp_type/erp_code。明确两个空数组才表示已查询无映射，未提供与`{}`不等价。
- supplier_profiles按本workspace/client范围的卖方税号索引单条profile（default_payment cash/credit等）；root按当前票据seller tax选取，不能拿买方客户税号代替公司税号。
- bank_index沿源为direction OUT/IN、Decimal文本amount、ISO tx_date；空列表只能表示已完成采集无记录。公开API不能提交complete=true。
- reported_config仅允许reported_accounts/products/customers/stock_acc_groups、catalog_fingerprint、max_payload_version、companion_version。禁止覆盖账户/root、凭据、六科目默认及reported_account_sets选择证据。
- 精确account head优先endpoint head；精确head一旦存在但pending/stale/incomplete，不回落旧endpoint数据。读取必须与当前connection_revision/generation、目标account/root一致。缺少、未完成或错配明确拒绝。

默认目标免目录refresh的源规则仍保留，但**映射/供应商/银行完整采集是另一道数据就绪门**。目前没有真实collector，因此生产连接不能靠默认空值假装准备资料齐全。只有隔离lab内部合成安装用于验证。

Root使用完整endpoint，先`endpoint_with_account_choice`清旧账套缓存，再overlay上述受限per-account observations，最后冻结完整payload。`FrozenTarget.account_config()`只含身份/六科目，不能代替完整配置，也不能直接overlay旧配置重放。

## 配置来源覆盖与剩余功能

固定源AST检查了Express `__init__/mapper/sales_mapper/preflight/posting_profile/stock_acc_group/autonomy` 中config/cfg的直接和`(config or {})`读键：共21个，无未登记消费键。

| 消费键 | 当前来源 |
|---|---|
| account_set/account_dir/express_root/account_company/account_set_row；六科目；default_doctype/stock_acccod/directions/autonomy/posting_profile | 管理员配置，服务端存储；选择与切换仍走targets |
| reported_accounts/reported_stock_acc_groups/catalog_fingerprint/max_payload_version | 可信preparation，禁止浏览器写入 |
| reported_account_sets | 已归目录Snapshot/targets证据，不允许prep覆写；清配置与选择目录分开 |

`posting_profile`真实类型是dict，模式non_stock/direct_account/stock/manual_review、库存用法none/perpetual/mixed/unknown。不是字符串。源autonomy routes和纯函数均为manual/standard/auto；不是assist/full。directions兼容数组和逗号字符串。MRERP来源读取system_url/username(_enc)/password(_enc)/comidyear/seldb，默认URL仍`https://www.mrerp4sme.com`；凭据映射原生Password，HTTP/HTTPS显式URL可存。

新原生API拒绝未知配置键，并不宣称旧任意config字典全部已迁。DMS凭据、Webhook/Flowaccount、旧client_ids路由、is_default/auto_push自动派发、MRERP凭据去重复用、Companion注册/绑定变更/心跳、测试连接、旧collector/租约/失败重试仍未实现。待处理刷新合并行为按旧来源保留；初版不同key替换requested的实现已纠正，不能作为未获授权的有意差异。

URL目前只做存储语法、字面private/metadata地址检查；没有DNS解析或连网。旧SSRF实现会检查DNS且记录rebinding残留。**新出站适配器未接入，未来必须完成DNS/IP绑定和重定向校验后才能连接；保存配置不是外部安全检测或连通性证明。**

## 验证

- 主机Python3.14.7：`tests/test_cowork_connections.py` 9项通过，覆盖源码真实配置类型、凭据/运行事实输入拒绝、URL字面检查、请求身份隔离、完整空上下文和账户覆写拒绝。
- root在第七批同候选镜像、隔离内网、只读本树挂载的新push Site执行原生脚本：首次15项全通过，1.633s，日志`/tmp/cowork-eighth-connections.log`。后来新增“安全目录只读”“Admin仅intake”和“仅RPC本地冲突有界重试”的3项，之后补充待处理合并/历史alias重放、revision和scope隔离3项，当前脚本21项，待root原生migrate及定向复验；本代理没有运行lab。
- RPC边界仅对QueryDeadlockError最多尝试3次，先整段本地rollback；权限/校验拒绝不重试。内部service没有自动rollback，避免取消上层较大事务。故障注入检查三次上限、权限拒绝不重试、本地insert后冲突重试仍仅一行，不将这一测试称为所有并发场景验收。
- 原生用合成用户/公司/配置/目录，不读取真实业务数据、不发ERP/模型/LINE请求。验证原生Password、真实授权撤销、幂等、版本和scope、Snapshot事务、无collector pending、通用native/legacy share拒绝。
- 架构检查0违规、AST语法、diff检查通过；最后新增的只读接口将由root统一复验。安装/重复migrate/已有行保留/整链prepared→账本由root总记录，不把本文件的连接单项测试写成全部迁移验收。

## 交给root的词条清单

本域没有改语言包。新增15个服务端错误码（纯错误动态翻译由API或service处理）：

| msgid | 建议中文 |
|---|---|
| connection.adapter_invalid | 不支持此ERP连接类型。 |
| connection.config_invalid | 连接配置无效，请检查后重试。 |
| connection.disabled | 此连接已停用。 |
| connection.lock_required | 读取推送目标前必须锁定连接。 |
| connection.not_found | 找不到此连接或目录记录。 |
| connection.preparation_incomplete | 此账套的推送准备资料尚未完整获取。 |
| connection.preparation_invalid | 推送准备资料与当前连接或账套不一致。 |
| connection.refresh_stale | 此刷新请求已过期，请重新申请刷新。 |
| connection.request_conflict | 此请求编号已用于其他内容。 |
| connection.request_key_invalid | 请求编号无效。 |
| connection.revision_conflict | 连接已更新，请重新读取后再试。 |
| connection.scope_immutable | 连接的公司、客户和类型不能直接修改。 |
| connection.snapshot_invalid | 目录快照与当前连接不一致。 |
| connection.snapshot_too_large | 目录快照超过16MiB限制。 |
| connection.url_invalid | ERP连接地址无效或不允许访问。 |

DocType/字段label共40个，需与已有词典差集后新增，不重复改变已有msgid：Account Set Key、Adapter、Allowed Users、Binding Generation、Bound Account Set、Bound Profile Key、Buyer Client、Company、Completed At (UTC)、Connection Configuration、Connection Name、Connection Revision、Content Hash、Cowork ERP Connection、Cowork ERP Refresh、Cowork ERP Snapshot、Creation Request Hash、Current Directory Heads、Directory Revision、Directory Scope Key、Directory Scope Kind、Directory Snapshot、ERP Connection、ERP Password、ERP Token、ERP Username、Enabled、Installed At (UTC)、Last Request Hash、Last Request Key、Observed At (UTC)、Preparation Evidence、Record Key、Refresh Request Aliases、Refresh Status、Request Hash、Requested At (UTC)、Requested By、Result Directory Revision、Source Hash。状态requested/succeeded/failed（历史superseded值仅保留兼容）沿全站通用含义，不将全局词条改成仅本域语义。


第八批最终验证补记：刷新请求合并修复后，root 在候选镜像的 `cowork-backend-push.localhost` 再次 migrate 并运行全部 **21项原生测试，2.134s，通过**（`/tmp/cowork-eighth-connections-final.log`）。包含同范围不同key合并、完成后的旧key重放、配置变化不重绑，以及新增目录DTO、Admin授权分支、本地事务重试；纯9项计入本批全部652项离线测试。新增字段通过原生结构升级；无真实collector或连通验收。上述词条已纳入唯一术语表与Cowork四语CSV，本批全部116新增词条的464次原生查询相等。
