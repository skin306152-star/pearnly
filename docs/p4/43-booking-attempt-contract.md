# `booking_attempt` 的契约：四条定死，然后实现

日期：2026-09-19。承接 [34-booking-attempt-needs-design.md](34-booking-attempt-needs-design.md)。
线上基准 `501589f0`。

34 号说这一层"不能照抄，要先定设计"，并列了四个问题。本文把它们逐条定死，
然后按这套契约实现（`pearnly_core/line_session.py` 的两个受守卫写入 +
`dms_booking_attempt.py` 的 LINE 层薄壳）。

## 一、它到底防什么（先说人话）

确认建单要跨一条**写边界**：请求发出去之后、结果读回来之前，中间可能崩、可能超时。
如果这期间再发一次，DMS 里就会出现**两张业务单据** —— 销售月底对不上账。

所以规矩是：**写 DMS 之前先把"我要提交这张草稿"登记进会话**。
登记成功 = 可以写；登记失败 = **绝不写**（宁可少写一张，不可多写一张）。

## 二、四条契约

### 1. 登记落在哪一层、键是什么

**落在会话里**（`payload["booking_attempt"]`），键就三个：`nonce` / `booking_no` / `owner`。

老产品还有 `tenant_id` / `channel_key` 两列参与 WHERE，新平台不需要：**一个站点就是一家
租户、一条 OA**，而会话本身就是按 `line_user_id` 存的 —— "这条会话是谁的"由存储位置
回答，不再靠额外两列去筛。

于是 `(nonce, owner)` 这两个键在新平台上**仍然够用**：`nonce` 认"是不是当初那张草稿"，
`owner` 认"是不是同一个执行体"。

### 2. `previous_no` 的语义

**同一个执行体在顺号重试时的那一次放行。**

场景：DMS 说这个单号重复 → 执行体取下一个号再提交一次。这属于"同一次提交"，
不该被自己刚写下的登记挡住。判据是**两个都对上**：既有标记的 `owner` 等于本次 `owner`，
且既有标记的 `booking_no` 等于本次传入的 `previous_no`。

对不上（别的执行体，或号不是它刚写的那个）→ 拒绝：**别的草稿顶不掉已登记的那张**。

### 3. 认领标记（老产品的 `_booking_claim`）从哪来

**由 `line_session.consume_nonce` 在消费 nonce 时留下**，且**只在 `booking_review` 态**：

```python
claimed = {**payload, "nonce": None}
if expect_state == "booking_review":
    claimed["booking_claim"] = nonce      # 老产品的 `_booking_claim`，语义逐字相同
```

为什么放在那儿：老产品的 CAS 要求 `nonce IS NULL AND _booking_claim = <nonce>`，
也就是"**这张草稿已经被认领过、而且认领的就是它**"。这个标记只有"消费 nonce"那一刻
才知道，放在别处就得再读一次会话、还会出现"消费了但没标记"的中间态。

建档那条链（`reviewing` 态）**不留**这个标记 —— 它不建单，不需要。

### 4. `clear_booking_attempt` 的"只能清自己那张"

**四条判据全中才清**：还在 `booking_review` 态、`nonce` 已被消费、
`booking_claim` 就是这个 `nonce`、且登记用的也是这个 `nonce`。任一条不符 → 不动。

满足后**把会话整个删掉**（老产品是同一条 DELETE）—— 这张草稿的使命结束了。

## 三、机制换了，保证没换

| | 老产品 | 新平台 |
|---|---|---|
| 存储 | Postgres `dms_line_sessions`（jsonb） | Frappe DocType `Pearnly LINE Session` |
| 原子性 | 一条 `UPDATE ... WHERE <全部判据>` 的 CAS | **Redis 锁（`SET NX PX`，粒度 `line_user_id`）+ 锁内读改写** |
| 外层序列化 | `binding_state.lock_scope` | 同一把 Redis 锁（一个用户同时只可能有一次提交） |

锁内是"先读会话 → 逐条判据 → 再写回"，**判据与线上那条 WHERE 一一对应**，四条都在：
态、`nonce is None`、`booking_claim == nonce`、以及"没登记过 / 同一个执行体顺号"。
抢不到锁（说明另一个执行体正在登记同一张）**直接拒绝**，不放行。

⚠️ 这里沿用 `adapter/master_shared` 那套 Redis 租约写法，**包括那个坑**：
Redis 取回来是 `bytes`，比较前必须解码，否则"只释放自己的锁"会永远判成不是自己的、
锁删不掉（那边真踩过）。

## 四、验证：`sample-verify-dms-booking-attempt.py`（23 项）

用**真会话 + 真 Redis 锁**跑（收尾清干净，不留会话垃圾），把每条判据钉死：

· 草稿**还没被认领**（nonce 还在）→ 拒绝（不许先写后认）。
· 认领之后才有认领标记，且能登记；登记内容逐字段核对。
· **别的执行体不许再登记同一张**（这就是防重复建单）；连自己原号再登记也不行。
· **同一个执行体凭上一个号可以顺号重试**；号对不上则拒绝。
· **换过草稿之后，旧登记不许被改写**；新草稿自己可以登记。
· 态不对（建档态）、缺参 → 一律拒绝。
· LINE 层：同一张草稿再点确认 → 直接回"结果未知"（带单号、`retry_safe=False`）；
  换个 nonce → 没有待处理标记；话术有登记标记就不说"请确认"。
· 收尾：用错 nonce 清不动别人的草稿；自己的草稿能清掉。

## 五、下一步

这一层现在是**可用的**，但还没有调用方 —— 消费者是 `booking_flow`（建单执行体），
它调用 `recorder(...)` 作为提交前登记、成功后 `clear_completed`、结果未知时
`message(...)`。**没有调用方时这一层不会被触发**，所以下一步就是搬 `booking_flow`
并把 LINE 那条链（拍卡 → 建档 → 订车逐问 → 预览 → 确认 → 建单 → 推送）接起来。

全套验证：**477 项**全绿（15 个脚本）。
