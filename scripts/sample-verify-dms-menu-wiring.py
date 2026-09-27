# -*- coding: utf-8 -*-
"""菜单层 + 接线验证：入口怎么弹、选完走哪、落档后分叉、以及路由的分支顺序。

菜单层本身是"薄"的，**风险都在接线**：分支顺序错了（比如"含数字→手机号"抢在逐问前面）、
或者落档后的分叉没接上，症状都是"用户卡在某一步、没有任何反应"。所以这里既验菜单层
自己的行为，也验 `dms_text_router` / `dms_flow` 的**分发顺序**。

出口全换记录器、外部依赖（订车逐问开工、查重、建单执行体）全换假的；**会话用真的**。

用法: scripts/sample-verify-dms-menu-wiring.sh <组> <站点>
"""


def main():
    import secrets

    from pearnly_core import line_session as store
    from pearnly_dms.booking.conversation import booking_qa as booking_qa
    from pearnly_dms.line import cards as cards
    from pearnly_dms.menu import menu_cards as menu_cards
    from pearnly_dms.menu import menu_flow as menu_flow
    from pearnly_dms.line import text_router as router

    ok = []

    def ck(label, cond, detail=""):
        ok.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + label + ((" - " + str(detail)) if detail else ""))

    pushed, replied, sent = [], [], []
    menu_flow._push = lambda line_user_id, text: pushed.append(text)
    menu_flow._reply = lambda reply_token, text: replied.append(text)
    menu_flow._send = lambda line_user_id, msg, reply_token="": sent.append(msg)

    started = []
    booking_qa.start = lambda tenant_id, line_user_id, **kw: started.append(
        {"tenant_id": tenant_id, "line_user_id": line_user_id, **kw}
    )
    booking_qa.send_step = lambda *a, **k: None

    router_replies = []
    from pearnly_core import line as _line

    _line.reply = lambda reply_token, msgs: router_replies.append(msgs) or True
    router.line = _line

    uid = "verify-menu-" + secrets.token_hex(4)
    store.clear_session(uid)
    binding = {"tenant_id": "one761900.localhost", "user_id": "user@x", "line_user_id": uid}

    # ── 1. 菜单卡本身 ──────────────────────────────────────────────────
    card = menu_cards.menu_card()
    blob = str(card)
    ck("菜单卡是 flex 且用的是泰文标题", card["type"] == "flex" and card["altText"],
       card.get("altText"))
    ck("默认卡只有 1 建档 / 2 订车（网页入口与查询都不画）",
       cards.ACT_MENU_CUSTOMER in blob and cards.ACT_MENU_BOOKING in blob
       and cards.ACT_MENU_QUERY not in blob, [k for k in (cards.ACT_MENU_QUERY,) if k in blob])
    ck("图标指向**本站点自己**的地址（不再写死别人域名）",
       menu_cards.icon_base().endswith("/assets/pearnly_dms/images/menu")
       and "pearnly.com" not in menu_cards.icon_base(), menu_cards.icon_base())
    # 门在 `menu_flow.can_query`（**不是**卡片构造器：构造器本来就该听参数）。
    # 查询那族已落地 → 现在就看花名册那一栏：给了画第 5 项，没给不画。
    from pearnly_dms.query import query_access as query_access

    real_can = query_access.can_query
    allowed = {"v": True}
    query_access.can_query = lambda binding: allowed["v"]
    try:
        menu_flow.query_access = query_access
        ck("花名册给了查询权 → 入口画出第 5 项；没给 → 不画",
           menu_flow.can_query({}) is True
           and cards.ACT_MENU_QUERY in str(
               menu_cards.menu_card(can_query=menu_flow.can_query({})))
           and (allowed.update(v=False) or menu_flow.can_query({}) is False)
           and cards.ACT_MENU_QUERY not in str(
               menu_cards.menu_card(can_query=menu_flow.can_query({}))),
           (allowed["v"], menu_flow.can_query({})))
    finally:
        query_access.can_query = real_can

    # ── 2. 弹菜单 ─────────────────────────────────────────────────────
    sent.clear()
    sess = {"state": "collecting", "payload": {"id_card": {"name": "ก"}, "phone": "0812345678",
                                               "endpoint_id": "EP1", "mode": "booking"}}
    menu_flow.open_menu(binding, uid, "tok", sess)
    ck("弹菜单 → 落到 menu 态并保留已收料（含 mode/endpoint）",
       (store.get_session(uid) or {}).get("state") == "menu"
       and (store.get_session(uid) or {})["payload"].get("phone") == "0812345678"
       and (store.get_session(uid) or {})["payload"].get("mode") == "booking",
       (store.get_session(uid) or {}).get("payload"))
    ck("เมนู 只发卡不寒暄", len(sent) == 1)

    sent.clear()
    menu_flow.open_menu(binding, uid, "tok", None, greet=True)
    ck("问候语进场先来一句欢迎再出卡", len(sent) == 2
       and sent[0].get("type") == "text" and sent[0]["text"] == cards.TXT_MENU_GREETING, sent[:1])

    # ── 3. 选菜单项 ───────────────────────────────────────────────────
    sent.clear()
    sess = {"state": "menu", "payload": {}}
    ck("别的态的文本不被菜单吃掉", menu_flow.handle_choice(binding, uid, "tok",
                                                          {"state": "collecting", "payload": {}},
                                                          "1") is False)
    ck("手机号不会被当成菜单键", menu_flow.handle_choice(binding, uid, "tok", sess, "0812345678")
       is False)
    # 查询那族已落地：menu 态打 '5' 要**交给查询层**（权限门在它里面，不在这里重复判）
    opened = []
    import pearnly_dms.query.query_flow as query_flow

    real_open = query_flow.open_query
    query_flow.open_query = lambda binding, luid, token: opened.append(luid)
    try:
        ck("menu 态打 '5' → 交给查询层（它自己会过权限门）",
           menu_flow.handle_choice(binding, uid, "tok", sess, "5") is True and opened == [uid],
           opened)
    finally:
        query_flow.open_query = real_open

    replied.clear()
    menu_flow.handle_choice(binding, uid, "tok", sess, "1")
    got = store.get_session(uid) or {}
    ck("选 1（只建档）→ mode=customer 回 collecting，并提示发身份证",
       got.get("state") == "collecting" and got["payload"].get("mode") == "customer"
       and replied[-1] == cards.TXT_MENU_SEND_CARD, (got.get("payload"), replied[-1:]))

    replied.clear()
    menu_flow.handle_choice(binding, uid, "tok", {"state": "menu", "payload": {}}, "2")
    ck("选 2（订车）→ mode=booking",
       (store.get_session(uid) or {}).get("payload", {}).get("mode") == "booking")

    replied.clear()
    menu_flow._enqueue_flow = None
    from pearnly_dms.intake import flow as flow_mod

    enq = []
    flow_mod._enqueue = lambda job, fn, *args: enq.append((job, fn.__name__, args))
    menu_flow.handle_choice(
        binding, uid, "tok",
        {"state": "menu", "payload": {"id_card": {"name": "ก"}, "phone": "0812345678",
                                      "endpoint_id": "EP1", "mode": "booking"}},
        "1",
    )
    ck("已在菜单里收齐料的用户选 1 → 直接查重（不让他重拍一次）",
       len(enq) == 1 and enq[0][1] == "run_dedup"
       and enq[0][2][2] == {"name": "ก"} and enq[0][2][3] == "0812345678", enq and enq[0][:2])

    # ── 4. 菜单 postback：续订车 / 重拍 ───────────────────────────────
    replied.clear()
    started.clear()
    menu_flow._continue_booking(binding, uid, "tok", {"cid": "别的客户"}, {"payload": {}})
    ck("续订车的客户号对不上 → 过期话术、不开局",
       not started and replied[-1] == cards.TXT_EXPIRED, replied[-1:])

    replied.clear()
    menu_flow._continue_booking(
        binding, uid, "tok", {"cid": "C9"},
        {"payload": {"customer_id": "C9", "endpoint_id": "EP1", "name": "คุณสมชาย",
                     "draft": {"phone": "081"}, "id_card_mid": "MID1"}},
    )
    ck("续订车开局把身份证附件源与草稿一起带过去（逐问要挂附件）",
       started and started[-1]["customer_id"] == "C9" and started[-1]["id_card_mid"] == "MID1"
       and started[-1]["draft"] == {"phone": "081"}, started[-1:] and started[-1]["draft"])

    replied.clear()
    store.set_session(uid, "reviewing", {"nonce": "n-re", "phone": "0812345678",
                                         "mode": "booking", "endpoint_id": "EP1"})
    menu_flow._retake(binding, uid, "tok", {"nonce": "错的"}, store.get_session(uid))
    ck("重拍：nonce 不对 → 过期话术、会话不动",
       replied[-1] == cards.TXT_EXPIRED
       and (store.get_session(uid) or {}).get("state") == "reviewing", replied[-1:])

    replied.clear()
    menu_flow._retake(binding, uid, "tok", {"nonce": "n-re"}, store.get_session(uid))
    got = store.get_session(uid) or {}
    ck("重拍：清掉身份证、留 phone/mode/endpoint 回 collecting",
       got.get("state") == "collecting" and got["payload"].get("phone") == "0812345678"
       and got["payload"].get("mode") == "booking" and "id_card" not in got["payload"], got)

    # ── 5. 落档后的分叉 ──────────────────────────────────────────────
    started.clear()
    pushed.clear()
    store.set_session(uid, "collecting", {"id_card_mid": "MID2", "mode": "booking"})
    menu_flow.after_customer_saved(binding, uid, endpoint_id="EP1", customer_id="C1",
                                   draft={"name": "คุณสมชาย"}, mode="booking", name="คุณสมชาย")
    ck("mode=booking → 接着开订车逐问（且不落档就清会话）",
       started and started[-1]["id_card_mid"] == "MID2" and store.get_session(uid) is not None,
       started[-1:])

    started.clear()
    pushed.clear()
    store.set_session(uid, "collecting", {"mode": "customer"})
    menu_flow.after_customer_saved(binding, uid, endpoint_id="EP1", customer_id="C1",
                                   draft={"name": "คุณสมชาย"}, mode="customer")
    ck("mode=customer（菜单 1 只建档）→ 收尾不提订车，会话结束，不在保存回执后再补一句",
       not started and store.get_session(uid) is None and not pushed, pushed[-1:])

    pushed.clear()
    menu_flow.after_customer_saved(binding, uid, endpoint_id="EP1", customer_id="C1",
                                   draft={}, mode="customer", same_data=True)
    ck("零写入路径（同资料点保持）话术不谎称已保存",
       pushed[-1] == cards.TXT_DONE_SAME, pushed[-1:])

    started.clear()
    store.set_session(uid, "collecting", {})
    menu_flow.after_customer_saved(binding, uid, endpoint_id="EP1", customer_id="C1",
                                   draft={"name": "จากดราฟต์"}, mode="booking", name="")
    ck("零写入路径不传姓名 → 回退草稿里的姓名（照旧选车入口语义）",
       started and started[-1]["customer_name"] == "จากดราฟต์",
       started[-1].get("customer_name") if started else None)

    # ── 6. 路由的分支顺序 ────────────────────────────────────────────
    calls = []
    router.menu_flow = type("M", (), {
        "open_menu": staticmethod(lambda *a, **k: calls.append(("menu", k.get("greet")))),
        "handle_choice": staticmethod(lambda *a, **k: False),
        "can_query": staticmethod(lambda b: False),
    })
    router.booking_qa = type("B", (), {
        "handle_text": staticmethod(lambda *a, **k: calls.append(("qa_text", a[2])) or True),
    })
    router.menu_cards = type("C", (), {
        "menu_card": staticmethod(lambda **k: {"type": "flex", "altText": "m"}),
    })

    store.clear_session(uid)
    router_replies.clear()
    router.route(binding, uid, "tok", "เมนู")
    ck("เมนู → 弹菜单（不是寒暄版）", calls == [("menu", False)], calls)

    calls.clear()
    router.route(binding, uid, "tok", "สวัสดี")
    ck("问候语 → 弹菜单（寒暄版）", calls == [("menu", True)], calls)

    calls.clear()
    router_replies.clear()
    router.route(binding, uid, "tok", "อะไรก็ได้")
    ck("无会话 → 弹菜单卡引路（取代旧引导文本）",
       not calls and router_replies and router_replies[-1][0]["type"] == "flex",
       router_replies[-1:])

    calls.clear()
    store.set_session(uid, "booking_qa", {"qa": {"step": "pay_amount"}})
    router.route(binding, uid, "tok", "5000")
    ck("逐问态先把文本交给状态机（**抢在'含数字→手机号'前面**）",
       calls == [("qa_text", "5000")], calls)

    calls.clear()
    router_replies.clear()
    store.set_session(uid, "booking_review", {"nonce": "n1"})
    router.route(binding, uid, "tok", "隨便打點什麼")
    ck("待确认态收到文本 → 只提醒点确认/丢弃，不覆写会话",
       not calls and router_replies[-1][0]["text"] == router.qa_cards.TXT_CONFIRM_ABOVE,
       router_replies[-1:])
    ck("待确认态的会话没被文本覆写",
       (store.get_session(uid) or {}).get("state") == "booking_review"
       and (store.get_session(uid) or {})["payload"].get("nonce") == "n1")

    router_replies.clear()
    store.set_session(uid, "booking_review",
                      {"nonce": None, "booking_claim": "n0", "booking_attempt": {
                          "nonce": "n0", "booking_no": "BK001", "owner": "o"}})
    router.route(binding, uid, "tok", "隨便打點什麼")
    ck("登记过的待确认态 → 说结果未知（不让人以为还能再点确认）",
       "BK001" in router_replies[-1][0]["text"], router_replies[-1:])

    store.clear_session(uid)

    # ── 7. `dms_flow` 的 postback 分发顺序 ───────────────────────────
    from pearnly_dms.booking.conversation import booking_flow as booking_flow
    from pearnly_dms.intake import flow as flow

    hits = []
    flow_mod_qa = booking_qa
    real_qa_pb = booking_qa.handle_postback
    real_bf_pb = booking_flow.handle_postback
    real_menu_pb = menu_flow.handle_postback
    booking_qa.handle_postback = lambda tenant, luid, data, params, token: hits.append(
        ("qa", data, params))
    booking_flow.handle_postback = lambda binding, luid, token, action, pb: hits.append(
        ("booking", action))
    menu_flow.handle_postback = lambda binding, luid, token, action, pb, sess: hits.append(
        ("menu", action))
    try:
        flow.handle_postback(binding, uid, "tok", {"data": "qa:pay:cash"})
        ck("`qa:` 前缀的按钮直接归逐问状态机",
           hits == [("qa", "qa:pay:cash", {})], hits)

        hits.clear()
        flow.handle_postback(binding, uid, "tok",
                             {"data": "action=confirm_booking&nonce=n1", "params": {"date": "x"}})
        ck("预览确认/取消/重试归建单执行体",
           hits == [("booking", cards.ACT_CONFIRM_BOOKING)], hits)

        hits.clear()
        store.set_session(uid, "menu", {})
        flow.handle_postback(binding, uid, "tok", {"data": "action=menu_booking"})
        ck("菜单项归菜单层（且排在写档守卫之前，不消费 nonce）",
           hits == [("menu", cards.ACT_MENU_BOOKING)], hits)

        hits.clear()
        flow.handle_postback(binding, uid, "tok", {"data": "action=reset"})
        ck("reset 仍归本文件的会话清理（不发消息给别的层）", not hits, hits)
    finally:
        booking_qa.handle_postback = real_qa_pb
        booking_flow.handle_postback = real_bf_pb
        menu_flow.handle_postback = real_menu_pb

    store.clear_session(uid)
    failed = [x for x in ok if not x]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(ok) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
