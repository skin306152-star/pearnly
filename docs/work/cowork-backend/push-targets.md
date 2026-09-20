# Cowork ERP 目标纯合同（第七批）

本批只交付**纯目标选择和证据判据**，没有原生连接 CRUD、目录刷新任务/消费者、公开 API、Companion 配对或外部推送。函数返回可冻结目标不等于连接在线、目录真实可用、已受理任务或 ERP 已写入；不得把没有消费者的 pending 字段当成功能交付。

新平台开工基线为 `407ad132b2e69c871bcb272e2a7a361b6363d295`。主任务核对旧生产仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，固定源 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。第七批运行证据 `push-runtime-seventh-start.json` 的 SHA-256 为 `111119d806e9daed01724baeec825453432078020717e21d484a4a7fe723573a`；60 个端点均为 legacy、managed=0，`erp_shared_express_endpoint` / `erp_target_projection` 均 enabled/all。保留 managed 分支的纯选择语义，不据此宣称 managed 运行验收。

## 归属与来源

本域位于 `apps/pearnly_cowork/pearnly_cowork/targets`。只有标准库和域内依赖，无 Frappe/FastAPI/数据库/环境/文件/网络/时钟调用；不依赖 push、Core 或前端。`push.express` 可单向复用 `targets.identity` 两个叶子函数。

| 固定源 | 新文件 | 保持内容 |
| --- | --- | --- |
| `services/erp/express_account_identity.py:16` | `identity.py` | Express 路径归一化、真实 path/root 配对，不把数据目录父路径当程序目录证据 |
| `services/erp/line_target_choice.py:16` | `choice.py` | opaque account reference、按 key/ref 选可写项、原标签规则、切账套清旧科目和主档缓存 |
| `services/erp/line_target_projection.py:42-229` | `choices.py` | MR.ERP 年/库选择；Express 投影优先、reported 回落、当前默认补项、路径去重、可写性、mapping；同时保留原版本比较函数 |
| `services/erp/target_projection_contract.py:1-293` | `projection.py` | 完整归一化/限额/敏感 key 检查/稳定 hash；账户、六类主档、表单 schema、capabilities 均保留 |
| `services/erp/target_catalog_evidence.py:119-431` | `evidence.py` | 源选择/刷新回执的全部有效/拒绝分支；DB 查询改为显式注入对应行 |
| `services/erp/selected_account.py:18-278` | `selection.py` | 原默认/非默认/服务端已验证 LINE choice 回落；proof gate；可写账套列表；切入选定账套 |
| `services/erp/target_projection_store.py:42` | `freeze.py` 中薄组合 | evidence 接受 JSON 原值，而原 load_state 在选择前解析 account_sets；组合保持这层字节到值的适配 |

`contracts.py` / `freeze.py` 是新平台组合合同，不冒称旧 source 原函数。source HTTPException 替换成无传输依赖的 `TargetSelectionError`，保留 `code`、`reason`、`detail`、`status_code` 元数据；未来公开入口决定 Frappe 翻译与响应，不把错误码硬编码成中文业务响应。

## 调用入口与信任边界

低层入口与原字典字段一致，便于未来原生 adapter 显式映射：

```python
from pearnly_cowork.targets.projection import normalize_projection
from pearnly_cowork.targets.choices import account_choices_for_endpoint
from pearnly_cowork.targets.choice import endpoint_with_account_choice
from pearnly_cowork.targets.evidence import validate_selection, validate_refresh_receipt
from pearnly_cowork.targets.selection import (
    require_catalog_evidence, resolve_account_choice,
    allowed_express_account_keys, resolve_endpoint_account,
)
from pearnly_cowork.targets import TargetAccess, TargetEvidence, freeze_target
```

`normalize_projection(raw)` 完整消费源 `adapter/account_set_key/observed_at/collector/account_sets/masters/form_schema/capabilities`；返回 `NormalizedProjection`。`observed_at` 必须由服务端观察者注入带时区时间，归一 UTC；不存在 import-time 年份/当前时间。源 freshness 判据是当前 head 状态和版本，**没有目录年龄 TTL**，本批不发明基于 now 的过期规则。稳定 hash 按排序后的业务内容计算，不包含 collector/observed_at；通用 JSON number 按源格式保留，仅用于目录元数据/哈希，不执行金额运算。

`resolve_account_choice(endpoint, *, tenant_id, user_id, state=None, account_set_key=None, trusted_account_config=None)` 的 state 是服务端投影 store DTO，形如 `{"snapshot": {"account_sets": [...]}}`。它本身不执行 fresh/active 权限审查，也不查询真实连接；先调用 `require_catalog_evidence` 或使用 `freeze_target`。只把有 key 的字典传入不构成信任。`trusted_account_config` 专指**已在服务器验证过的 LINE 选择**，不能暴露为普通客户端 config/account_dir/mapping 参数。

`validate_selection` / `require_catalog_evidence` 用 `latest_refresh` 和 `snapshot` 替代旧 cursor。服务必须在当前授权上下文、同一连接绑定锁和事务内读取：

- latest_refresh：本 tenant+endpoint、scope=`@endpoint` 下按 requested_at/created_at/id 排序的**最后一个请求**；字段 id/status/account_set_key/adapter/result_revision。
- snapshot：本 tenant+endpoint、scope=`@endpoint` 下**请求的 revision** 联接 current head；字段 snapshot_id/adapter/account_sets/head_snapshot_id/head_revision/head_status。
- `validate_refresh_receipt` 的 latest_refresh 还含 head_status/head_revision/snapshot_adapter；取最新请求和当前 head，不许拿任意旧成功行冒充。

原查询里的 tenant/endpoint/scope 过滤、latest 排序、锁与授权现在由未来 IO adapter 承担。纯函数无法验证这些行真的来自数据库。`TargetEvidence` 的上下文标签仅防止误混已加载结果，不是签名或授权凭证。

推荐未来写入前的单一组合入口：

```python
target = freeze_target(
    authorized_endpoint,
    access=TargetAccess(tenant_id, user_id, endpoint_id, workspace_client_id, authorized=True),
    evidence=TargetEvidence(tenant_id, user_id, endpoint_id, latest_refresh, snapshot),
    account_set_key=selected_key, request_id=refresh_id, revision=projection_revision,
)
```

`TargetAccess` 必须来自当次服务端 Site/功能/角色/公司客户/端点权限检查；不得从 JSON 反序列化后直接信任。组合会拒绝 denied、空/不匹配上下文、disabled 或非 MR.ERP/Express adapter，并在同一份 detached 快照上依次做 proof 和 account resolution。未通过不能冻结。连接原 dict 的 `id/config/binding_generation/bound_profile_key` 等字段保持源命名；未来 Frappe name/Link/client/connection_version 映射由原生 adapter 负责，不在纯层编造值。

FrozenTarget 是 frozen dataclass，仅包含 tenant_id/user_id/endpoint_id/workspace_client_id、adapter、account_set、root_key、binding_generation、bound_profile_key、catalog_refresh_request_id、catalog_projection_revision、proof_required、account_config_json。`root_key` 优先取源 proof 的 root；仅已判定 `bound_default` 且无需刷新时，允许取服务端原 config 明确配置的 express_root，并与最终配置 canonical 一致。选择后 config 中 raw express_root 单独保留在账户配置内。账户配置只保留年/库/路径/公司/行号/程序目录及六个 mapping 槽，JSON 字符串不可变，每次 `account_config()` 返回 detached dict；不包含账号密码/令牌。它没有 write status、lease、设备认证或连接探测结论；源端点没有 authoritative device 字段时不生成假 device。

`account_config()` 不是完整 payload 配置。后续消费者不得直接把它 overlay 到原端点 config 后发送，否则会留下原账套 reported_* 主档。受理时必须用 `endpoint_with_account_choice` 先执行切账套清理，再基于同一已验证目录 snapshot 准备并冻结完整 payload；重试使用已冻结 payload，不重新拼装混合配置。

## 必须保留的源细节

- MR.ERP 默认年/库是服务端 config 的 comidyear/seldb，源回落 6/1；这是账户选择语义，不等于可写校验或某一真实公司承诺。
- Express 默认：generation>0 优先 bound_account_set，否则优先 config.account_set/account_dir；支持映射盘符/UNC 的源字符串归一，不根据税号推断账套。
- **默认绑定目标可免非默认目录证明**，源会直接返回 bound_default，不检查快照 fresh；不能把这句话扩大成默认连接已在线/权限已验。非默认必须同时满足最后请求 succeeded、请求/revision一致、head=fresh、snapshot/head ID与版本一致、adapter一致、所选账户存在且 active，Express 还要求 writable 与已知 root不冲突。
- evidence 的 root 配对只认明确上报；源 choices 在展示/配置 root 缺失时却会回落 `ntpath.dirname(account_dir)`。两层行为保持原样并差分；父目录展示值不因此成为 proof。
- **组合层一致性要求**：Express 证据 root 与最终 config.express_root 必须非空且按源 Windows 路径规则归一后相等，缺失/不一致分别返回 `catalog_refresh_invalid` / `frozen_root_missing`、`frozen_root_mismatch`。**保持默认目标无需目录刷新**：仅 `proof_required=False` 的已判定默认分支，源 proof 未填 root 时可使用服务端原 config.express_root；不要求 snapshot/reported，不从数据目录猜 root，实际未明确配置程序目录仍拒绝。普通 legacy 默认和 managed bound 默认都覆盖。非默认不准用绑定 root 当所选账户证明，须在所选 snapshot 的 root、明确 reported path/root 配对或服务端 trusted choice 的 root 中找到同一显式 root，否则 `frozen_root_unproven`。仅绑定 root 回落与父目录推导恰好相同也不足。此前组合误拒有明确程序目录的默认目标已修复，不把功能缩水称为保护性变化。
- **组合层账户一致性**：proof.account_set_key 必须与最终配置选出的 account 一致；Express 按源 Windows 规则归一，MRERP 年/库各组件按源 reservation 规则先 `str(...).strip()` 再拼接。冲突为 `catalog_refresh_invalid` / `frozen_account_mismatch`。固定源可复现 managed bound=B/config.account_set=A、trusted choice 仅给 account_dir=B 时，旧 proof 接受 B 而应用 choice 后 account_set 仍 A；冻结拒绝这种结果，不改底层源算法。上述 root/账户矛盾拒绝是组合层明确差异，普通默认行为继续对齐。
- 目录选项层、source resolver 与证据层职责不同。只列出某项、只通过 resolve、只返回原生 Link 都不能代替 proof gate。
- 切换不同 Express 数据目录时清除 revenue_acc/ar_acc/vat_output_acc/fallback_acc/ap_acc/vat_input_acc，以及 reported_accounts/products/customers/stock_acc_groups 和 catalog_fingerprint；再从选定 choice.mapping 只填上述六槽。其他配置沿源保留，不把旧主档带入新账套。
- server-trusted LINE fallback 沿源保留。但组合入口对非默认依然先要求目录证明，不能用 trusted_account_config 绕过；有同名但不可写项时也拒绝。
- 源 `resolve_endpoint_account` 末尾专取 account_set/account_dir，因此只适合 Express 流程；MR.ERP 应使用 choice + comidyear:seldb（组合已这样处理），不悄悄修成另一种同名语义。

## 与后续原生接入的边界

现有 Core Endpoint 没有公司/客户、目录 revision、绑定 generation/device 等 Cowork 字段；Core `api_test_connection` 只查配置。Core Push Log 的 claim/finish、自行 commit 和未 scoped RPC 不能直接承担新外部写入；Core execution/work 明确只接受 OCR，不挂 Cowork 外部订单 writer。

后续最小原生闭环仍需 Connection / Target Snapshot / Target Refresh（或等价原生结构），具备真实刷新消费者和凭据/SSRF/授权边界；刷新结果原子推进版本并拒绝旧 generation 回执。然后在权威推送账本的同一受理事务冻结 target/payload/source receipt，重试沿原目标，未知外部结果不自动再写。设备/Companion源码身份、真实业务回读、目录在线性和 native list/REST/share 权限均未由本批完成。不能以新增 contract 或状态字段代替这层交付。

## 验证证据

运行环境为本机 `/opt/homebrew/bin/python3.14`，版本 **3.14.7**；只用标准库。先前 lab bench 本批未运行，本任务没有启动/停止/migrate、连接 Site、访问真实目录或调用外部 API。macOS 默认 `python3` 是 3.9，一次错误测试入口已改为锁定 3.14.7，未以旧运行时替代目标证据。

- `tests/test_cowork_targets.py`：**28 项通过**。覆盖路径与真实 root 配对、投影优先/去重/默认/managed分支、缺 proof/请求替换/版本替换/只读/停用/root冲突、切账套缓存清理、伪造选择、冻结上下文/授权拒绝/原始 JSON适配/脱密/不可变、完整目录/schema/capabilities、hash稳定、目录限额/敏感key/时间校验，以及组合 root 缺失/不一致/无显式配对拒绝、三种显式来源 canonical 一致通过、legacy/managed 默认无需目录刷新、证明与最终账户冲突拒绝和 MRERP 带空白年/库通过。
- `scripts/cowork-targets-source-parity.py`：七文件 SHA-256 绑定到上述固定源码，AST 仅移除旧 app imports，数据访问只用注入合成 Cursor 行；不导入旧应用。**7,156** 次比较全部通过：identity 2,000；choices 500；reset 500；selection 1,250；selection evidence 1,900；refresh receipt 500；完整 projection 500；require-proof→resolve→冻结组合 6（含 legacy/managed 默认无需刷新）。正反路径比较完整返回 dict 或冲突 status/detail，不放宽消息/金额精度；源哈希见脚本 HASHES。
- 另列 **6 个组合有意拒绝案例**：证明旧 proof 和 choice 均会接受，而新 freeze 拒绝不一致 root、实际配置/依据 root 缺失、只由 bound/dirname 推导的 root，以及 Express/MRERP 证明与最终账户冲突；输出 `intentional_composition_rejections=6`，不计入正常行为的 7,156 次比较。
- 脚本支持互斥 `--source SNAPSHOT` / `--source-stdin`。后者从 stdin 读取 JSON `{ "services/erp/filename.py": "sourceText" }`，七个所需文件缺失或哈希变化仍拒绝；可由主机 `subprocess.run(..., input=json.dumps(source_texts))` 向无网络候选镜像输送源码，避免依赖 Colima 未共享的主机 `/tmp` 挂载。标准输入模式的输入运输及缺失源码、篡改源码、非 object 输入三条拒绝路径已单独验证；与路径模式执行相同差分函数。本轮更新后路径模式 7,156 比较和 6 个有意拒绝通过，候选镜像标准输入复验由 root 汇总。
- 架构检查 **0 violations**；手工 AST 核对 targets import/调用中无 Frappe/FastAPI/HTTP/文件/时钟；`git diff --check` 通过。后续仅文档变动不重复业务全套。

新增 wrapper 的授权/上下文拒绝码 `erp.target_access_denied`、`erp.target_context_invalid` 暂为内部合同；没有可见消费者，本批不把未接入词条或菜单作为功能完成。原既有错误码/源 reason 完整保留。以上证明纯计算与源合同一致，**不证明真实目录已经刷新、连接已经通过测试、或外部推送已经迁完**。
