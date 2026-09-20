# 原生 Push 账本与本地领取/回执（第八批）

本包建立 Cowork 本 Site 唯一推送账本，支持冻结准备、内部激活、互斥领取、带 attempt 凭据的回执、未知结果保护和撤销尚未发送的准备。产品的 `prepared` 只表示本地准备成功，不能显示为 ERP 已入账。本批没有 Companion 身份验证器、真实运输、公开领取/ACK RPC、队列扫描器或 scheduler 消费者；原生验证注入的回执全部是标明的合成结果。

## 基准、归属与数据

- 工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`；分支 `codex/cowork-backend-migration`；第八批开工 HEAD `0e5cd94e84f444c2246b57b4838263163c9cdfc3`。
- 主任务开工重核线上源码仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，完整 endpoint 配置摘要与第七批一致。读取固定源码 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。交付前复核及配置证据由主任务总记录承载。
- App/业务域：`pearnly_cowork.push.native`。纯身份/冻结合同在 `contracts.py`，Frappe 持久化在 `service.py`，通用文档防绕过在 `documents.py`。
- 新建 `Cowork Push Record`、`Cowork Push Attempt`、`Cowork Push Writer Lock`。不复用 Core OCR Work、不写 Core Push Log、不读 ERP Site 数据库或旧 PostgreSQL。
- Record 保存原 actor、公司/客户、History/Connection 版本、完整目标和载荷、检查结果、业务防重身份与当前状态。Attempt 每次真正领取单独保存 owner、generation、递增 fence、随机令牌的 SHA256、起止时间和回执。Writer Lock 是长期行，租约释放只清 holder；未知结果保留 holder，不因时间到就释放。

| 固定来源 | 保持的合同 | 原生落点 |
| --- | --- | --- |
| `services/erp/confirmed_push_reservation.py` | 原目标冻结；成功→活跃→未知的阻断；未知结果不直接再投 | `reserve`、`lookup_request`、`recover_expired` |
| `services/erp/push_dedup_store.py` | 同票已有成功优先；同 owner/endpoint/account、精确票号和卖方名的跨 History 成功防重 | `document_identity`、`business_identity`、`reserve` 及领取前复核 |
| `services/erp/express_push/agent_store.py`、`agent_lease_store.py` | 120 秒租约；ACK 各 stage；legacy waiting_lock 不消耗 wire attempt；失败三次人工；成功不被晚失败覆盖 | 复用已迁 `push.lifecycle.acknowledgement`，由原生事务执行 |
| `services/erp/shared_express_agent_queue.py` | managed generation/attempt 验证；managed waiting_lock 推进 wire attempt | 同一原生 attempt 层按冻结 branch 保持 wire 差异 |
| `services/erp/internal_push_guard.py` | `erp_web`、`line_erp` 属内部 ERP，不能外推 | 复用 `contracts.push.require_external_history`，从当前 History 来源校验 |

## 调用与权限

`ReservationContext` 是值对象，不是授权凭据。`reserve` 自身再次读取 Connection 当前行、产品能力/角色/显式用户授权及公司/客户范围，再读取当前 History。服务 API 不能接受客户端自报 mappings、银行/供应商主档或 prior_docnum。实际请求组装在独立 `push.application`，原键权限走 Source/Batch 已受理来源，不使用可编辑字段做授权。

```python
from pearnly_cowork.push.native.service import (
    ReservationContext, lookup_request, reserve, read_success,
    activate, lease, ack, recover_expired, cancel_prepared,
)

# 先由应用服务锁当前 connection，取得实际 actor/范围后构造。
context = ReservationContext(actor, workspace, client, history_name, history_version,
    connection_name, account_set, connection_revision=connection_revision)
old = lookup_request(context, request_key)
# 有 old 时比较原 request_intent，重放原冻结结果；不按当前票/配置重算。
result = reserve(context=context, request_key=request_key,
    frozen_target=server_target, frozen_payload=server_payload,
    disposition="prepared", checks=server_checks)
```

- `lookup_request(context, request_key)`、`read_success(context=..., history=...)` 返回内部完整投影。`project` 的 `frozen_target/frozen_payload/response_body` 可能有连接内部配置，不能原样对外；应用 API 必须选择安全字段。
- `lookup_request` 允许 `context.account_key=''`，用于当前配置变化后仍找到原幂等请求。reserve、读取成功、writer/document identity 都拒绝空账套。
- 相同 workspace+actor+request_key 的冻结意图/载荷必须相同。请求已有账本时返回原结果；另一 key 被已有成功/活跃/未知阻断时返回 `blocked_by/blocked_reason`，不新建伪成功记录。
- 激活和领取必须同时满足当前 feature flag、原 actor 的当前授权、当前 History/Connection 版本，以及 Record 冻结的 actor/版本/branch/generation。用新版本 context 不能激活旧载荷。
- 领取前同时重验当前目录证据；connection revision 未变也不允许使用已被刷新取代的旧 proof。当前快照明确 inactive 或 not-writable 的默认账套同样拒绝，不重选目标或重算载荷。有效旧 attempt 的 ACK 仍可记录。
- 内部 `trusted_adapter_context` 是未注册的 Python 对象能力注入缝，不是实际 Companion 认证。普通 `AdapterContext(...)` 或 Administrator 角色不能替代该对象能力。未来运输必须先验证真实安装的写者身份，且必须在数据库事务提交领取后才发出载荷。
- 每个模块方法参与调用方事务，没有 commit。锁顺序是 connection→history→排序 writer locks→record/attempt。目标 JSON 只用于不可变 writer 路由读取，决定状态、版本、去重和授权的读取都是 current lock；业务键用 SHA256 精确比较，避免 MariaDB 默认 collation 把不同文本混为一票。
- 三个 DocType 的 controller 防止通用保存、删除、改名；真实 object capability 不能用 `flags=True` 仿冒。has_permission/query hook 明确抛拒绝，包含 Administrator 和 DocShare 回退；DocShare 写入与预置 active share 都拒绝。

## 状态与有意保护变化

| 状态 | 含义 | 后续 |
| --- | --- | --- |
| prepared | 已冻结且从未领取，未发生外部业务 | 内部激活或原 actor 按 revision 撤销 |
| pending | 仅内部激活，等待互斥领取 | 合法领取才创建 Attempt |
| leased | 一次授权尝试正在持有端点/账套锁 | 同 attempt 的可信 ACK，或过期转未知 |
| manual | 预检需人工、已知拒绝、失败耗尽，或 `result_unknown=true` | 未知必须保留锁和旧 attempt；不能自动重发 |
| success | 可信适配器报告成功的原始回执 | 终态；晚失败不回退，不代表本批已有真实 ERP 验证 |
| cancelled | 原 actor 撤销从未领取的 prepared | 保留所有冻结证据，释放 active key；允许新 key 重新准备 |

明确区别于旧系统：

1. 旧默认 legacy 链没有全面耐久 reserve；本批把全部原生准备纳入统一唯一账本和同票 active key，不能把它称为原样搬迁。
2. 所有 legacy/managed 领取均使用独立 fence+随机令牌。legacy `waiting_lock` 保持旧 wire attempt 不增长，但下一次领取的 fence 必须增长，旧令牌不能 ACK 新尝试。
3. 所有已领取超时均标未知并保留锁；没有沿用普通 legacy 到期自动重领。缺少真实客户端查重和外部回读证据时，不能自动把未知当失败。
4. ACK 的有效 stage 是权威；不允许 `meta.stage` 的旧值覆盖实际 ACK。保持原 stage 优先于 success 布尔的规则，终态成功必须先验证当前 attempt token，之后才幂等返回。
   实际来源 ACK 只在 success 保存 line_modes；legacy success 派生 fallback_count/created_masters，managed 不派生，已按真实入口保留。failed/rolled_back 的 helper 虽可收 line_modes，真实调用会丢弃，不能按 helper 能力错误扩大合同。
5. 接收已授权 attempt 的回执不因原 actor 后来被撤权或全局开关关闭而丢弃；该路径不授予新外写权限。未经过有效领取，管理员也不能制造成功。
6. 增加原 actor+精确 record revision 的 prepared 撤销。pending、leased、unknown、success、已领取过的记录一律不能由此撤销。
7. writer identity 包含端点锁和同公司/root/account 锁，跨客户、跨 Connection 指向同一已声明路径时保守互斥。不证明不同公司/不同别名路径的同一物理设备已统一；真正设备/写者身份与 Companion 本地锁仍待迁移。
8. 同 History 的新平台防重跨 actor 生效；跨 History 业务键保持原 owner、精确票号和卖方名，并附新公司/客户范围。账套使用已迁 Express 规范化，不使用 Unicode casefold；SQL 查失败不降级为无成功记录。
9. 新的已授权准备遇到 history/config 版本已过时，或旧 actor 当前已经确定无权的预留时，只有 `prepared`、fence=0、无 active_attempt、查无任何 Attempt 且非未知才能原子取消并重新准备。角色、用户启停、公司/客户授权撤销不一定改变 Connection revision，因此以当前已锁连接调用旧 actor 的实际授权函数，只把明确 PermissionError 视为撤权；数据库或未知异常不作为撤权。旧记录保留全部冻结证据，reason 为 `push.preparation_superseded`，原生 `modified_by` 记录实际新操作者；新记录失败则整次事务回滚。pending/leased/unknown 不适用，也不凭操作者改变就取消版本仍有效、旧 actor 仍获授权的记录。

## 验证与限制

离线合同 `tests/test_cowork_push_native_contract.py` 12 项已通过：请求/业务/文档/writer 身份、空账套边界、ß/SS、冻结载荷/意图、状态/gen、UTC、JSON 非有限和 DocType 唯一约束，以及无公开原生 RPC。

```bash
PYTHONPATH=apps/pearnly_cowork:tests python3 -m unittest test_cowork_push_native_contract -q
python3 scripts/check-architecture.py
```

`scripts/cowork-push-native-lab-verify.py` 24 项为真实 Site 事务验证，要求 `PEARNLY_COWORK_BACKEND_LAB=1`、允许名单中的 Site、专属 marker 和本任务挂载。它复用原生准备的合成 fixture；每个用例独立合成 root，同一互斥用例内部仍共享 root。并发用两个独立数据库进程及旧 RR 快照，测试同/不同 key 准备与双领取。其它场景覆盖 writer 互斥、旧 token、ACK 幂等/失败耗尽/人工、未知保留锁及晚 ACK、撤权/停用、修改后不能重新激活、目录证据失效、撤销与撤权后安全接替、跨 History 防重、事务中断回滚、通用权限与分享，以及 legacy/managed 每种 stage 的真实入口回执格式对照。脚本只输出测试摘要，不打印 token 或载荷；保留带 Synthetic 标识的已提交 lab fixture，不清理共享数据。

第一轮 22 项真实 Site 验证有 11 failure + 1 error，没有标为通过：并发用例提交的 holder 与后续用例共用公司/root/account，正确阻挡了后续领取；非默认 DATAB fixture 缺独立 mapping，被源规则正确判 `no_purchase_account`；并发旧 RR 快照触发 MariaDB 1020。已分别修复夹具隔离、补齐目标 mapping，并在合成领取进程的完整本地事务边界增加最多三次仅 QueryDeadlockError 的 rollback→重新读取/授权/领取。内部 service 保持抛出冲突，不能在子步骤吞错或回滚调用方其它工作；未来真实运输调用方也必须先拥有整个本地事务，再采用该模式，不能覆盖任何外部请求或未知结果。增加无 revision 变化的撤权测试后的 23 项由主任务真实 Site 复跑通过（7.970s）。最终回执格式修正另补一项 branch/stage 对照，24 项结果待主任务复跑回填。

真实 Site 安装、重复迁移、原生用例和应用装配结果由主任务运行并回填；此处不把“脚本已编写”写成已通过。外部 Companion/MR.ERP、真实建单/回读、部署、前端与用户验收均未执行。managed 分支的纯规则已在第七批差分验证；第八批当前配置入口不创建真实 managed 身份，不把合成测试称作 managed 客户端连接验收。

旧 `agent_store._mirror_history_status`、managed `_mirror_history` 的 History 状态镜像，以及 `_bump_created_stock_masters` 和成功通知分发，尚未连接本包。Record 是唯一权威；后续 History 查询/表格导出必须从它取实际状态，或实现有一致性验证的派生字段，不能沿旧 pending 显示假状态。本批保留原回执元数据，不因模拟 ACK 修改目录库存画像，也不发送通知；这部分明确登记为后续功能链，未称完整 ACK 外部链已迁。


最终现场补记：ACK响应格式修正后的 **24项原生测试全部通过，7.997s**，日志 `/tmp/cowork-eighth-native-complete.log`；包含23项既有边界与新增12组branch/stage响应组合。主任务同时确认连接21项、应用18项、往返15项通过；外部ERP与完整ACK副作用链仍未执行。
