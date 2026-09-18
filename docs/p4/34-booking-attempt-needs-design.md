# `booking_attempt` 不能照抄：先定设计，再动手

日期：2026-09-18。承接 [33-authoritative-read-and-banks.md](33-authoritative-read-and-banks.md)。
本文是**调查结论**，不是实现记录。

## 一、本来打算做什么

`create_booking_via_form` 有个 `on_attempt` 回调，线上用它在**跨过写边界之前**把
"我要提交这张单"登记下来（见 `dms_booking_submit.submit_booking` 的注释：
POST 之后崩溃，不能让下一个 worker 的投递变成第二张业务单据）。

线上那份登记在 `services/line_dms/booking_attempt.py`（61 行）+ 
`services/line_dms/session_store.py` 的 `record_booking_attempt` / `clear_booking_attempt`。

## 二、为什么不能照抄（三条，都有出处）

### 1. 它是 LINE 层的模块，不是 DMS 客户端层的

它 import 的是 `services.line_dms.session_store` / `store`，产出的是给 LINE 用户的
话术（`message()` / `review_message()`）。**它的消费者是 LINE 的建单执行体**，
而那一层新平台还没搬。

### 2. 那个"原子登记"是一条精心构造的 Postgres CAS

```sql
UPDATE dms_line_sessions SET payload = payload || jsonb_build_object('booking_attempt', ...)
WHERE tenant_id=... AND channel_key=... AND line_user_id=...
  AND state='booking_review' AND expires_at>now()
  AND payload->>'nonce' IS NULL AND payload->>'_booking_claim'=...
  AND ((previous_no IS NULL AND NOT (payload ? 'booking_attempt'))
       OR (payload->'booking_attempt'->>'owner'=... AND ...->>'booking_no'=...))
```

一条语句里同时管住：**只登记被消费掉的那张草稿**、**同一执行体只能推进号一次**、
**换过的草稿不能被顶掉**、**过期草稿不算**。外面还套了 `binding_state.lock_scope`
做序列化。

新平台的会话存在 **Frappe DocType `Pearnly LINE Session`** 上（`pearnly_core/line_session.py`），
没有 `tenant_id` / `channel_key`（一站一租户），也没有 `_booking_claim` 这个字段。
所以这不是"改几个 import"，是**在新存储上重新实现同一套保证**。

### 3. 它的错法代价是双单

这段代码唯一的作用就是防止**重复提交产生两张业务单据**。抄错（比如把 CAS 写成
"先读再写"）会静默退化成没有保护 —— 而症状只在并发或崩溃时才出现，平时测不出来。
所以它属于**必须先把契约写清楚再动手**的那一类。

## 三、现在**不阻塞**任何东西

查过了：`dms_booking.book_in_session` 目前只被 `sample-verify-dms-booking-real.py`
调用，**LINE 流程还没走到建单那一步**（菜单层与订车逐问都未搬，
`dms_flow.py` 里写明"到建档为止"）。所以 `on_attempt` 现在**没有生产消费者** ——
这批不做不会留下半个功能。

## 四、动手前要定下来的四件事

1. **在哪一层登记。** 新平台没有多 OA / 多租户列，`(nonce, owner)` 这两个键是否
   仍然够用？还是要再加上 `line_user_id`+站点？
2. **`previous_no` 的语义。** 线上用它放行"同一个执行体顺号重试"的那一次推进
   （`owner` + `booking_no` 都匹配才放过）。在新存储上等价物是什么？
3. **`_booking_claim` 从哪来。** 线上是草稿被消费时打的标记（`nonce IS NULL`
   表示已被消费）。新平台草稿层（`dms_draft.py`）要不要补这个标记？
4. **`clear_booking_attempt` 的"只能清自己那张"** 怎么落。

## 五、建议的顺序

先搬 **订车逐问 + 菜单层**（线上 9 个文件约 2800 行）—— 那一步会把
`on_attempt` 的**消费者**带进来，届时"登记什么、什么时候清"才有真实上下文可依；
再实现登记本体。反过来先做登记，会在没有调用方的情况下凭空定契约。

## 六、这一轮的其他状态

未提交代码改动（本轮只做了调查）。上一轮的成果仍在：

- 全套 **277** 项验证全绿（cards 34、advisor 25、intake 33、draft 21、client 77、
  booking-foundation 45、booking-readback 42）。
- 订车链已接通：`client_ops` → 原生取号 → 载荷/组织 → 提交 → 回读。
- 仍**只有纯逻辑层证据**；`booking-real` 会往真 DMS 写单，需要用户授权才跑。
