# 订车逐问的四个小件：付款 / 转账 / 翻页 / 会话快照

日期：2026-09-19。承接 [37-booking-qa-batch-shape.md](37-booking-qa-batch-shape.md)。
线上基准 `501589f0`。

37 号说这批"入口是 `qa_cards` 自己，做完它，剩下的小件才能逐个落地"。
`qa_cards` 已落（[39](39-qa-cards-text-prepared.md) 文本侧 + `d523a23` 代码侧），
所以这批就是**挂在它上面的四个小件**，一次落完 —— 它们互相咬着，拆开哪个都跑不起来。

## 一、搬了什么

| 新平台 | 线上 | 行数 | 说明 |
|---|---|---|---|
| `dms_booking_qa_payment.py`（新增） | `booking_qa_payment.py` | 143 | 付款方式与凭证的一致性步进 |
| `dms_booking_qa_transfer.py`（新增） | `booking_qa_transfer.py` | 218 | 转账/支票/本票/银行卡的资料收集 |
| `dms_booking_qa_pages.py`（新增） | `booking_qa_pages.py` | 118 | 需要读主档的那几步 + 翻页 |
| `dms_booking_qa_sync.py`（新增） | `booking_qa_sync.py` | 50 | 会话内的主档快照（90 秒口径） |
| `dms_out.py`（新增） | `_out.py` 的 `_send` | — | LINE 结构化消息出口 |

这四个是**注入式步进**：状态怎么改靠 `qa` 字典，发消息/存会话靠调用方注入的回调
（`persist` / `send_step` / `reask` / `to_preview`）。好处是它们不认识平台、也不认识
LINE，因此验证不用连 DMS —— 喂假回调就能把每一步断言死。

## 二、三处**有意**与线上不同

### 1. async → 同步

线上是 FastAPI 的同步栈外面套一层 asyncio（`await persist(...)`、`_thr`）。
新平台是 Frappe 的同步栈，DMS 这个 app 里**一个 `async def` 都没有**。
照抄 `await` 会得到一段跑不起来的代码，所以全部改成直接调用，回调签名不变。

### 2. 端点不再现解

线上每轮取数前要按 LINE 绑定解一次 DMS 端点（`masters_cache.qa_endpoint` →
`dms_id_ocr.resolve_dms_endpoint`）。新平台**一个站点就是一家租户、一条 DMS 连接**，
端点就在会话里（`qa["endpoint_id"]`，绑定/建档时写入）。

解不出端点这一条**照线上保留**：抛 `ERR_DMS_MASTER_UNAVAILABLE`，发问层据此出"主档
读不到"卡，不炸会话。

### 3. LINE 出口不带 channel、不带线程包装

线上的 `_out._send(line_user_id, msg, reply_token, channel)` 自己按有没有 reply_token
分流（有 → reply，没有 → push），另外还夹着多 OA 的渠道解析与 `_thr`。
新平台一个站点一条 OA（`pearnly_core.line` 读站点配置），所以 `dms_out.send` 只留
"reply 优先、否则 push"这一个分流，渠道与线程两样都去掉。

## 三、验证：`scripts/sample-verify-dms-booking-qa-steps.py`（40 项）

钉住的是**状态机最容易长坏的地方**：

· **非法输入不许推进**：未知渠道、同一笔重复渠道、金额解析失败、越界页号、不认识的
  翻页动作 —— 一律原地重问，状态一个字段都不改。
· **收尾前的两件一致性**：有转账没凭证 → 先要凭证并记住拿完去预览；有凭证没转账 →
  出冲突卡（补转账 / 删凭证），**不直接放行到预览**。
· **银行目录两副面孔**：有行 → `pay_bank` 选；权威为空 → `pay_ref` 手工填并挂
  `MANUAL_BANK_FLAG`。目录有行那笔**不许**挂手工标记（写错 DMS 会记成手工账户）。
· **换收款账户要清旧资料**：`dst_id` 变了就丢掉旧的分行/账号，否则会推错账户。
· **翻页只翻看**：页面令牌与 DMS 的 id 分得开（id 是"选中那一行"的事）；
  车型结果页数要夹在最后一页。

写验证时踩到两处**语义**，记下来免得下次又写错断言：

1. `complete_channel` 会把待填渠道**搬进 `payments` 并清空 `pending_channel`** ——
   断言要看 `payments[0]["extra"]`，不是 `pending_channel["extra"]`。
2. `manual_banks` 是**上一步**（金额步发现目录权威为空）打的标记；脱离前一步单独构造
   `qa` 来测手工银行，测的不是真实会话。

## 四、还差什么（37 号那簇的剩余部分）

| 文件 | 行数 | 卡在哪 |
|---|---|---|
| `booking_qa.py` | 447 | 逐问状态机本体（这四个的上游，还没搬） |
| `booking_qa_start.py` | 68 | 要 LINE 回复层接口，**做机器时一并定** |
| `booking_edit.py` / `booking_flow.py` | 415 / 463 | 逐问期编辑、建单执行体 |
| 菜单层（`menu_cards` / `menu_flow` / `menu_sync` / `rich_menu` / `login_tickets`） | 251 / 225 / 114 / 243 / 190 | 会带出 `booking_attempt.on_attempt` 的消费者 |

`booking_attempt` 那四条契约仍待定，见 [34](34-booking-attempt-needs-design.md)，
**不阻塞**上面这些。

全套验证：**416 项**全绿（12 个脚本）。
