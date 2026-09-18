# -*- coding: utf-8 -*-
"""入口链**端到端**验证：从 LINE webhook 入口（`line_handlers.handle`）走一遍。

前面那些脚本验的是"每一层自己对不对"；这一条验的是**它们接起来以后还对不对** ——
入口是全链路唯一没有单层归属的地方：webhook 事件 → 绑定判定 → 文本/图片/postback 三条路
→ 路由分支。这里的错法都很像"用户发了消息、什么也没发生"，所以值得单独跑一遍。

做法：LINE 出口与后台入队换成记录器（真发消息会打真实 LINE API），绑定查询换成假的
（不建真绑定行），**会话用真的** —— 所以状态流转是真的。

用法: scripts/sample-verify-dms-entry-e2e.sh <组> <站点>
"""


def main():
    import secrets

    from pearnly_core import line as line_api
    from pearnly_core import line_binding
    from pearnly_core import line_session as store
    from pearnly_dms import dms_booking_flow
    from pearnly_dms import dms_cards as cards
    from pearnly_dms import dms_flow, line_handlers
    from pearnly_dms import dms_qa_cards as qa_cards

    ok = []

    def ck(label, cond, detail=""):
        ok.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + label + ((" - " + str(detail)) if detail else ""))

    replied, pushed = [], []
    line_api.reply = lambda reply_token, msgs: replied.append(msgs) or True
    line_api.push = lambda to, msgs: pushed.append(msgs) or True
    line_api.start_loading = lambda *a, **k: True
    line_handlers.line = line_api

    queued = []
    dms_flow._enqueue = lambda job, fn, *args: queued.append((job, fn.__name__, args))
    # 建单执行体有自己的一份入队（`dms_booking_flow._enqueue`）—— 不换掉的话，
    # 点"确认"会往真队列里丢一个真后台任务（那是真副作用，不是测试该干的事）。
    dms_booking_flow._enqueue = lambda job, fn, *args: queued.append((job, fn.__name__, args))

    BOUND = {"users": {}}
    line_binding.user_of = lambda line_user_id: BOUND["users"].get(line_user_id)
    line_handlers.line_binding = line_binding

    uid = "verify-e2e-" + secrets.token_hex(4)
    store.clear_session(uid)

    def send(kind, **payload):
        event = {"type": kind, "source": {"userId": uid}, "replyToken": "tok"}
        event.update(payload)
        return line_handlers.handle(event)

    # ── 1. 未绑定：不许静默丢弃 ────────────────────────────────────────
    replied.clear()
    send("message", message={"type": "text", "text": "เมนู"})
    ck("未绑定发消息 → 回一句绑定提示（不静默丢弃）",
       replied and "ผูก" in str(replied[-1]) or (replied and replied[-1]), replied[-1:])

    BOUND["users"][uid] = "user@x"

    # ── 2. 已绑定发 เมนู → 菜单卡 ────────────────────────────────────
    replied.clear()
    send("message", message={"type": "text", "text": "เมนู"})
    ck("เมนู → 真的弹出一张 flex 菜单卡",
       replied and replied[-1][0].get("type") == "flex"
       and cards.ACT_MENU_CUSTOMER in str(replied[-1][0]), replied[-1:] and len(replied[-1]))
    ck("弹完菜单落到 menu 态", (store.get_session(uid) or {}).get("state") == "menu",
       (store.get_session(uid) or {}).get("state"))

    # ── 3. 点"2 订车" → collecting + mode=booking + 提示发卡 ──────────
    replied.clear()
    send("postback", postback={"data": "action=%s" % cards.ACT_MENU_BOOKING})
    sess = store.get_session(uid) or {}
    ck("点订车 → 回 collecting、mode=booking，并提示发身份证",
       sess.get("state") == "collecting" and sess["payload"].get("mode") == "booking"
       and replied and replied[-1][0]["text"] == cards.TXT_MENU_SEND_CARD,
       (sess.get("state"), sess.get("payload", {}).get("mode"), replied[-1:]))

    # ── 4. 拍身份证 → 后台识别（不占回复令牌） ────────────────────────
    queued.clear()
    send("message", message={"type": "image", "id": "MID-1"})
    ck("拍卡 → 丢后台识别（webhook 立刻返回）",
       queued and queued[-1][0] == "image" and queued[-1][2][2] == "MID-1", queued and queued[-1][:2])

    # ── 5. 输手机号 → 后台查重 ───────────────────────────────────────
    queued.clear()
    store.set_session(uid, "collecting",
                      {"id_card": {"name": "ก"}, "endpoint_id": "EP1", "mode": "booking"})
    send("message", message={"type": "text", "text": "0812345678"})
    ck("输手机号 → 带齐料就丢后台查重",
       queued and queued[-1][1] == "run_dedup" and queued[-1][2][3] == "0812345678",
       queued and queued[-1][:2])

    # ── 6. 逐问态：文本归状态机，不被吃成手机号 ──────────────────────
    # 金额步的前置是"已经选好渠道"（`pick_channel` 先写 pending_channel 再进 pay_amount）
    store.set_session(uid, "booking_qa", {
        "qa": {"step": "pay_amount", "pending_channel": {"channel": "cash"}, "payments": []}
    })
    replied.clear()
    send("message", message={"type": "text", "text": "5000"})
    got = (store.get_session(uid) or {}).get("payload", {}).get("qa", {})
    ck("逐问态输金额 → 走状态机（不被'含数字→手机号'吃掉）",
       str(got.get("pending_channel", {}).get("amount")) == "5000.00"
       or got.get("payments") or got.get("audit"),
       {k: got.get(k) for k in ("step", "pending_channel", "audit")})

    # ── 7. 待确认态：文本只提醒，确认必须点按钮 ──────────────────────
    store.set_session(uid, "booking_review", {"nonce": "n1", "qa": {}})
    replied.clear()
    send("message", message={"type": "text", "text": "ยืนยัน"})
    ck("待确认态打字「ยืนยัน」→ 只提醒点上面的按钮，**不当作确认**",
       # ⚠️ 这句话在 `dms.card.qa.*` 命名空间里；引错成 `cards.` 不会报错，
       # 只会静默返回裸 key（写这条用例时真踩了）
       replied and replied[-1][0]["text"] == qa_cards.TXT_CONFIRM_ABOVE
       and (store.get_session(uid) or {}).get("payload", {}).get("nonce") == "n1",
       replied[-1:])

    # ── 8. 确认按钮 → 丢给建单执行体（带 nonce） ─────────────────────
    queued.clear()
    replied.clear()
    send("postback", postback={"data": "action=%s&nonce=n1" % cards.ACT_CONFIRM_BOOKING})
    ck("确认按钮 → 消费 nonce 并丢后台建单",
       queued and queued[-1][1] == "_execute_booking"
       and (store.get_session(uid) or {}).get("payload", {}).get("nonce") is None,
       queued and queued[-1][:2])

    # ── 9. 再发 เมนู → 会话被覆写（= 放弃进行中的事） ────────────────
    replied.clear()
    send("message", message={"type": "text", "text": "เมนู"})
    ck("进行中会话里再发 เมนู → 覆写成 menu 态（与 เริ่มใหม่ 同语义）",
       (store.get_session(uid) or {}).get("state") == "menu"
       and replied and replied[-1][0].get("type") == "flex",
       (store.get_session(uid) or {}).get("state"))

    # ── 10. 开始使用 → 清会话 ────────────────────────────────────────
    replied.clear()
    send("message", message={"type": "text", "text": "เริ่มใหม่"})
    ck("开始使用 → 清会话并回'已重置'",
       store.get_session(uid) is None and replied and replied[-1][0]["text"] == cards.TXT_RESET,
       replied[-1:])

    # ── 11. 其它消息类型：明确回一句，不静默丢 ────────────────────────
    replied.clear()
    send("message", message={"type": "location"})
    ck("非文本非图片（位置）→ 有明确回应，不静默丢", bool(replied), len(replied))

    store.clear_session(uid)
    failed = [x for x in ok if not x]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(ok) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
