# P1.05 控制 API v1、事件与错误码

日期：2026-09-17。

## 一、控制 API v1

控制面只在开发机运行（ADR-004），**不暴露到公网**。所有写操作幂等、可追踪、出错不虚报。

### 端点

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/v1/companies` | 建公司（商业客户） |
| POST | `/v1/principals` | 建账号，或把已有账号加入 |
| POST | `/v1/invitations` | 发号（初始密码单次传递，强制首登修改） |
| POST | `/v1/deployments` | 开通：公司 × 产品 → 建 Site、库、账号、绑定域名 |
| POST | `/v1/deployments/{id}/features` | 勾选/取消功能 |
| POST | `/v1/deployments/{id}/roles` | 配角色与账套范围 |
| POST | `/v1/deployments/{id}/suspend` | 暂停、恢复 |
| POST | `/v1/deployments/{id}/domains` | 改域名（改绑定，不改 ID） |
| GET | `/v1/provisioning/{request_id}` | 查开通进度（分步骤） |
| POST | `/v1/memberships/{id}/revoke` | 撤权 |

### 开通的硬性规则

1. **一次请求可勾多个产品**；任一产品失败**不得整体虚报成功**，已成功的产品不受影响。
2. 每个请求带**幂等键**；同一幂等键重复提交只建一套资源。
3. 过程落 `ProvisioningRequest` / `ProvisioningStep`，页面刷新能恢复进度。
4. 资源不足时**显式失败或排队**，不得静默减少已勾选功能、合并租户数据或无限创建常驻进程。
5. 只有启用 `cowork.boards` 时才创建 WeKan 实例（每实例约 2 GiB，是资源准入的实际闸门）。
6. 操作员**看不到明文凭据**。
7. 补偿逻辑**不得删除被复用的账号**。

## 二、事件 envelope v1

所有跨组件事件共用一层外壳，载荷各自定义：

```json
{
  "event_id": "uuid",
  "type": "erp.push.completed",
  "version": 1,
  "occurred_at": "2026-09-17T10:00:00Z",
  "actor": { "principal_id": "…", "via": "web|line|companion|system" },
  "deployment_id": "…",
  "scope_id": "…",
  "idempotency_key": "…",
  "trace_id": "…",
  "payload": {}
}
```

约定：

- 时间一律 **UTC**，显示层再转曼谷时间与佛历。
- `actor.via` 必须能区分来源渠道；LINE、Companion 不得冒充 web。
- 事件携带 `deployment_id` 与 `scope_id`，否则无法判断归属，**拒绝入队**。

## 三、任务 schema v1

自定义异步任务统一携带：

| 字段 | 说明 |
|---|---|
| `task_id` | 稳定 ID |
| `deployment_id` / `scope_id` | 归属，缺一不可 |
| `authz_version` | 授权版本；执行前复核，撤权后旧任务必须被拒 |
| `idempotency_key` | 防重复扣费、重复写入 |
| `schema_version` | 任务结构版本 |
| `attempt` / `max_attempts` | 重试控制 |

规则：

- **审批之后、执行之前**必须再查一次权限。
- 外部写入前要有唯一写者锁；**拿不到锁就拒绝，不降级放行**。
- 重试不得造成重复外部写入或重复扣费；歧义状态停止重发并转人工。

## 四、错误码

沿用现有语义（不另造同义词）：

| 错误码 | 含义 | 现有出处 |
|---|---|---|
| `module_disabled` | 模块开关关闭 → 整组 403 | `services/authz/registry.py` |
| `needs_mapping` | 外部映射缺失，需人工 | ERP 推送状态 |
| `failed` / `blocked` / `retrying` | 推送失败 / 被阻塞 / 重试中 | `erp_push_logs` 状态语义 |

新增（身份与新架构）： 

| 错误码 | 含义 |
|---|---|
| `not_invited` | 账号未被邀请 |
| `product_not_purchased` | 该产品未开通（区别于"未授权"） |
| `scope_denied` | 账套不在授权范围内 |
| `revoked` | 成员关系或会话已撤销 |
| `cross_deployment_denied` | 试图访问其它部署的数据 |
| `platform_principal_denied` | 平台账号试图进入业务 Site |
| `authz_version_stale` | 任务携带的授权版本已过期，拒绝执行 |

## 五、与现有推送状态的关系（不得降级）

ERP 推送状态**以 `erp_push_logs` 为唯一来源**。`rows=0`、`needs_mapping`、`failed`、`blocked`、`retrying` 一律**不显示为完成**；HTTP 200 不能证明外部 ERP 已写入。新架构的控制 API 与事件不得另立一套"推送成功"的判据。
