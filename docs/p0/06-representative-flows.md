# P0.06 代表流程（四个入口各一条）

按计划要求，四个入口各记一条现状流程。基线 `pearnly-app @ ecdacdba`，只读。

每条流程给出：入口 → 主链 → 数据 → 外部系统。证据为文件与行号。

## 一、网页 / ERP：采购建单 → 过账 → 推送

| 环节 | 位置 |
|---|---|
| 建单 | `POST /api/purchase/docs`（`routes/purchase_routes.py:87`） |
| 服务 | `services/purchase/documents.py`、`services/purchase/docs.py` |
| 过账 | `POST /api/purchase/docs/{doc_id}/post`（`:180`）→ `services/purchase/posting.py` |
| 会计挂点 | `posting.py:60,146` 调 `acct_hooks.enqueue_posting`，`:258` 用 `void_or_reverse`，`:322` 用 `void_for_source`。**会计不迁移后，这四个挂点改为空动作** |
| 库存 | `posting.py:17` 引入 `services/inventory.ledger`（库存台账，**保留**，与会计账簿是两套） |
| 付款 | `POST /api/purchase/docs/{doc_id}/pay`（`:201`）、`/payment-status`（`:215`） |
| 作废与更正 | `/void`（`:230`）、`/correct`（`:248`） |
| 数据 | `purchase_docs`、`purchase_lines`、`purchase_attachments`、`inventory_transactions` |
| 外部 | 推送状态落 `erp_push_logs`；端点与凭据在 `erp_endpoints`、`erp_oauth_tokens` |

**保留判定**：主流程保留；仅去掉生成凭证那一步。付款状态是业务事实，保留。

## 二、LINE：Cowork 绑定与录入

| 环节 | 位置 |
|---|---|
| Webhook 入口 | `routes/cowork_line_webhook_routes.py` |
| 绑定 | `routes/cowork_line_binding_routes.py`（`/api/cowork-line/*`，含 `/liff/cowork-connect`、`/liff/cowork-live`） |
| 接入 | `routes/cowork_line_intake_routes.py`（含 `/liff/cowork-intake`） |
| 服务 | `services/cowork_line/`（34 个文件），含 `push.py`、`push_dedup.py`、`push_recovery.py` |
| 数据 | `line_webhook_events`、`line_message_refs`、`line_bindings`、`line_binding_codes`、`line_action_nonces`、`line_chat_history`、`line_pending_*`、`line_funnel_events` |
| 外部 | LINE 渠道凭据（Secret Manager `pearnly-line-dms-a`、`pearnly-line-dms-b`） |

**保留判定**：保留。`push_dedup`/`push_recovery` 是幂等与恢复机制，按计划不得在迁移中关闭。

## 三、WeKan：协作桥

| 环节 | 位置 |
|---|---|
| 入口 | `routes/work_bridge_routes.py`（`/api/work/*` 11 条 + `/work` 页面） |
| 服务 | `services/work_bridge/`（6 个文件） |
| 数据 | 桥侧无独立表；看板数据在 WeKan 的 Mongo 里 |
| 外部 | WeKan 服务地址与共享桥接 secret；票据与父会话绑定 |

**保留判定（2026-09-21 覆盖）**：**不迁移**。用户决定团队协作功能不搬、搬了删掉、后期自行开发；本节只作为旧系统代表流程的历史记录保留。原判定"按计划作为 Cowork 内部功能纳入本仓构建"及其 E10/E11 两条硬要求一并作废。已迁部分见 [协作功能移除记录](../work/cowork-backend/collaboration-removal.md)。

## 四、Companion：本地 Agent 出站拉取

| 环节 | 位置 |
|---|---|
| 凭据 | `POST /api/erp/endpoints/{endpoint_id}/agent-token`（`routes/erp_agent.py:191`） |
| 心跳 | `POST /api/erp/agent/heartbeat`（`:223`） |
| 取任务 | `POST /api/erp/agent/lease`（`:294`） |
| 确认 | `POST /api/erp/agent/ack`（`:352`） |
| 服务 | `services/erp/`（156 个文件），含 `endpoint_identity.py`、`session_lock.py` |
| 数据 | `erp_endpoints`、`erp_oauth_tokens`、`erp_push_logs` |
| 外部 | Companion 仓的 `pairing.py`、`bridge/cloud_jobs.py`；账套在本机选择，写角色校验在 Companion 侧 |

**保留判定**：保留。计划 E16/E17 要求：拆库后跨库锁不再互斥，**没有可靠锁时拒绝写**，且域名、租户、凭据、协议版本要两仓同步。

## 这四个入口对 P1 的直接含义

- 四个入口**都要在样板里存在**，不能只搭网页。
- LINE 与 Companion 依赖外部真实系统（LINE 渠道、Windows 机器），**样板阶段只能验到协议与拒绝路径**，真机验收留到 P7。
- `erp_push_logs` 是推送状态唯一来源，样板与测试都必须以它为完成判据，不能以 HTTP 200 代替。
