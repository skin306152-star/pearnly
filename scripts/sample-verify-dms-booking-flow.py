# -*- coding: utf-8 -*-
"""预览卡确认 / 取消 / 重试那一层（建单执行体）的验证。

这一层是"提交登记"的唯一调用方，顺序错了就会**重复建单**。所以把出口全部换成记录器、
建单换成假的，**会话用真的**（跑真 `consume_nonce` / `replace_claimed_booking_payload`），
把每条分叉钉死：确认是一次性的、取消不许在登记过之后谎称"已取消"、重试只放"建单前的阻塞"。

用法: scripts/sample-verify-dms-booking-flow.sh <组> <站点>
"""


def main():
    import secrets

    from pearnly_core import line_session as store
    from pearnly_dms.booking.conversation import booking_flow as flow
    from pearnly_dms.line import cards as cards

    ok = []

    def ck(label, cond, detail=""):
        ok.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + label + ((" - " + str(detail)) if detail else ""))

    pushed, sent_structured, replied = [], [], []
    flow._push = lambda line_user_id, text: pushed.append(text)
    flow._reply = lambda reply_token, text: replied.append(text)
    flow._send = lambda line_user_id, msg, reply_token="": sent_structured.append(msg)
    queued = []
    flow._enqueue = lambda job, fn, *args: queued.append((job, fn.__name__, args))
    flow.resolve_dms_endpoint = lambda user, endpoint_id: {"id": "EP1", "name": "EP1"}
    flow._out.start_loading = lambda line_user_id, seconds=30: True

    ledger_calls = []
    flow.booking_ledger.log_booking = lambda **kw: ledger_calls.append(kw)

    book_calls = []
    book_result = {"ok": True, "booking_no": "BK001", "booking_id": "R1", "attach_ok": True}
    flow.book_in_session = lambda endpoint, payload, files, failed, on_attempt=None: (
        book_calls.append({"files": files, "failed": failed, "on_attempt": on_attempt}),
        book_result,
    )[1]

    QA = {
        "endpoint_id": "EP1",
        "step": "pay_more",
        "customer": {"id": "C1", "name": "คุณสมชาย"},
        "advisor": {"id": "A1", "name": "dmstest"},
        "draft": {"phone": "0812345678"},
        "answers": {"car": {"id": "C1", "label": "DMAX 2.5"}, "delivery_date_be": "20/09/2569"},
        "files": {"id_card_mid": "MID-ID", "slip_mid": None},
        "payments": [{"channel": "cash", "amount": "5000.00"}],
    }
    binding = {"tenant_id": "one761900.localhost", "user_id": "user@x", "line_user_id": "u"}

    uid = "verify-flow-" + secrets.token_hex(4)
    store.clear_session(uid)

    # ── 1. 模块形状 ────────────────────────────────────────────────────
    ck("处理的就是预览卡那三个动作",
       flow.BOOKING_ACTIONS == {cards.ACT_CONFIRM_BOOKING, cards.ACT_CANCEL_BOOKING,
                                cards.ACT_RETRY_BOOKING}, sorted(flow.BOOKING_ACTIONS))
    ck("重试用的草稿 TTL 是 30 分钟", flow._RETRY_TTL_MINUTES == 30)
    ck("附件两类：身份证 + 转账凭证",
       [k for k, _, _ in flow._ATTACH_KINDS] == ["id_card", "slip"], flow._ATTACH_KINDS)

    # ── 2. 确认：一次性 nonce + 丢后台 ────────────────────────────────
    queued.clear()
    n1 = secrets.token_hex(8)
    store.set_session(uid, "booking_review", {"nonce": n1, "qa": QA})
    flow.handle_postback(binding, uid, "tok", cards.ACT_CONFIRM_BOOKING, {"nonce": "错的"})
    ck("nonce 对不上 → 过期话术且**不建单**",
       not queued and replied and replied[-1] == cards.TXT_EXPIRED, (queued, replied[-1:]))

    replied.clear()
    flow.handle_postback(binding, uid, "tok", cards.ACT_CONFIRM_BOOKING, {"nonce": n1})
    ck("nonce 吻合 → 丢后台执行（webhook 立刻返回）",
       len(queued) == 1 and queued[0][1] == "_execute_booking", queued)
    ck("确认把 nonce 一次消费掉（同一张卡再点必 mismatch）",
       ((store.get_session(uid) or {}).get("payload") or {}).get("nonce") is None
       and store.consume_nonce(uid, "booking_review", n1) is None)

    # ── 3. 取消：登记过就不许说"已取消" ───────────────────────────────
    pushed.clear()
    replied.clear()
    uid2 = "verify-flow-" + secrets.token_hex(4)
    store.clear_session(uid2)
    n2 = secrets.token_hex(8)
    store.set_session(uid2, "booking_review", {"nonce": n2})
    flow.handle_postback(binding, uid2, "tok", cards.ACT_CANCEL_BOOKING, {})
    ck("没登记过 → 真的取消：清会话 + 说已取消",
       store.get_session(uid2) is None and replied[-1] == cards.TXT_BOOKING_CANCELLED,
       replied[-1:])

    replied.clear()
    uid3 = "verify-flow-" + secrets.token_hex(4)
    store.clear_session(uid3)
    n3 = secrets.token_hex(8)
    store.set_session(uid3, "booking_review", {"nonce": n3, "qa": {}})
    store.consume_nonce(uid3, "booking_review", n3)
    store.record_booking_attempt(uid3, n3, "BK777", "own-1")
    flow.handle_postback(binding, uid3, "tok", cards.ACT_CANCEL_BOOKING, {})
    ck("登记过 → 不说'已取消'，改说结果未知（并带单号）",
       replied and replied[-1] != cards.TXT_BOOKING_CANCELLED
       and "BK777" in replied[-1] and store.get_session(uid3) is not None, replied[-1:])

    # ── 4. 重试动作也走 same consume ─────────────────────────────────
    queued.clear()
    replied.clear()
    uid4 = "verify-flow-" + secrets.token_hex(4)
    store.clear_session(uid4)
    n4 = secrets.token_hex(8)
    store.set_session(uid4, "booking_review", {"nonce": n4, "qa": QA})
    flow.handle_postback(binding, uid4, "tok", cards.ACT_RETRY_BOOKING, {"nonce": n4})
    ck("重试按钮也消费 nonce 并丢后台",
       len(queued) == 1 and ((store.get_session(uid4) or {}).get("payload") or {})
       .get("nonce") is None, queued)

    # ── 5. 执行体：已登记过就不再提交 ────────────────────────────────
    pushed.clear()
    book_calls.clear()
    uid5 = "verify-flow-" + secrets.token_hex(4)
    store.clear_session(uid5)
    n5 = secrets.token_hex(8)
    store.set_session(uid5, "booking_review", {"nonce": n5, "qa": QA})
    store.consume_nonce(uid5, "booking_review", n5)
    store.record_booking_attempt(uid5, n5, "BK555", "own-1")
    flow._execute_booking(binding, uid5, {"nonce": n5, "qa": QA})
    ck("这张草稿已登记过 → 只回'结果未知'，**不再提交一次**",
       not book_calls and pushed and "BK555" in pushed[-1], pushed[-1:])

    # ── 6. 执行体：成功路径 ──────────────────────────────────────────
    pushed.clear()
    book_calls.clear()
    ledger_calls.clear()
    uid6 = "verify-flow-" + secrets.token_hex(4)
    store.clear_session(uid6)
    n6 = secrets.token_hex(8)
    store.set_session(uid6, "booking_review", {"nonce": n6, "qa": QA})
    flow._execute_booking(binding, uid6, {"nonce": n6, "qa": QA})
    ck("建单调用带上了提交登记回调（on_attempt 必须传下去）",
       book_calls and callable(book_calls[0]["on_attempt"]), bool(book_calls and book_calls[0]["on_attempt"]))
    ck("回执含单号/车型/交车日/顾问",
       pushed and all(x in pushed[-1] for x in ("BK001", "DMAX 2.5", "20/09/2569", "dmstest")),
       pushed[-1:])
    ck("成功写了台账", len(ledger_calls) == 1 and ledger_calls[0]["result"]["booking_no"] == "BK001")
    ck("成功且附件齐全 → 回执**不**提附件", "แนบไฟล์ไม่ครบ" not in pushed[-1], pushed[-1][-30:])

    # ── 7. 执行体：附件没挂全要如实说 ────────────────────────────────
    pushed.clear()
    book_result = {"ok": True, "booking_no": "BK002", "booking_id": "R2", "attach_ok": False}
    uid7 = "verify-flow-" + secrets.token_hex(4)
    store.clear_session(uid7)
    n7 = secrets.token_hex(8)
    store.set_session(uid7, "booking_review", {"nonce": n7, "qa": QA})
    flow._execute_booking(binding, uid7, {"nonce": n7, "qa": QA})
    ck("附件没挂全 → 回执如实补一句（不谎报）",
       pushed and "แนบไฟล์ไม่ครบ" in pushed[-1], pushed[-1][-40:])

    # ── 8. 执行体：结果未知 / 可重试 / 钱不够 ────────────────────────
    pushed.clear()
    sent_structured.clear()
    book_result = {"ok": False, "error_code": flow.booking_attempt.UNKNOWN,
                   "booking_no": "BK900", "error_friendly": {"th": "x"}}
    uid8 = "verify-flow-" + secrets.token_hex(4)
    store.clear_session(uid8)
    n8 = secrets.token_hex(8)
    store.set_session(uid8, "booking_review", {"nonce": n8, "qa": QA})
    flow._execute_booking(binding, uid8, {"nonce": n8, "qa": QA})
    ck("结果未知 → 只说结果未知（**不给重试卡**，重放未知写入是双单）",
       pushed and not sent_structured and "BK900" in pushed[-1], (pushed[-1:], len(sent_structured)))

    pushed.clear()
    sent_structured.clear()
    book_result = {"ok": False, "error_code": "ERR_DMS_MASTER_UNAVAILABLE",
                   "error_friendly": {"th": "master 读不到"}}
    uid9 = "verify-flow-" + secrets.token_hex(4)
    store.clear_session(uid9)
    n9 = secrets.token_hex(8)
    store.set_session(uid9, "booking_review", {"nonce": n9, "qa": QA})
    # 真实路径里 payload 是 `consume_nonce` 给出的（草稿此时已被认领）—— 重挂也只认这种草稿
    store.consume_nonce(uid9, "booking_review", n9)
    flow._execute_booking(binding, uid9, {"nonce": n9, "qa": QA})
    ck("建单前的阻塞（主档读不到）→ 出重试卡",
       len(sent_structured) == 1 and not pushed, (len(sent_structured), pushed[-1:]))
    rearmed = (store.get_session(uid9) or {}).get("payload") or {}
    ck("出重试卡时草稿被重挂成待确认（新 nonce 能再确认，认领标记已摘掉）",
       rearmed.get("nonce") and rearmed["nonce"] != n9 and rearmed.get("booking_claim") is None,
       (rearmed.get("nonce"), rearmed.get("booking_claim")))

    pushed.clear()
    sent_structured.clear()
    book_result = {"ok": False, "error_code": "ERR_DMS_PAYMENT_INCOMPLETE",
                   "error_friendly": {"th": "钱没填够"}}
    uid10 = "verify-flow-" + secrets.token_hex(4)
    store.clear_session(uid10)
    n10 = secrets.token_hex(8)
    store.set_session(uid10, "booking_review", {"nonce": n10, "qa": QA})
    store.consume_nonce(uid10, "booking_review", n10)
    flow._execute_booking(binding, uid10, {"nonce": n10, "qa": QA})
    ck("钱没填够 → 回预览卡让人补齐（不是重试卡）",
       len(sent_structured) == 1 and pushed and pushed[-1] == "钱没填够",
       (len(sent_structured), pushed[-1:]))

    # ── 9. 重试判据 ─────────────────────────────────────────────────
    ck("已经写下去过（submitted）→ 不许重试",
       flow._retryable_result({"response_body": {"submitted": True}}) is False)
    ck("有 booking_id → 不许重试",
       flow._retryable_result({"booking_id": "R1"}) is False)
    ck("主档已变更 → 不许重试（重试只会拿到同一份主档）",
       flow._retryable_result({"error_code": "ERR_DMS_MASTER_UNMATCHED"}) is False)
    ck("建单前就被挡住的那三类 → 可以重试",
       all(flow._retryable_result({"error_code": c}) for c in
           ("ERR_DMS_CONCURRENT_LOGIN", "ERR_DMS_MASTER_UNAVAILABLE", "ERR_DMS_CUSTOMER_LOOKUP")))
    ck("公司银行主档没就绪（文本判据）→ 可以重试",
       flow._retryable_result({"raw_error": "company bank master did not become ready"}))
    ck("其它错误 → 不重试",
       flow._retryable_result({"error_code": "ERR_DMS_TECHNICAL"}) is False)

    # ── 10. 附件下载 ────────────────────────────────────────────────
    flow._out.download_content = lambda mid: b"\x89PNG\r\n\x1a\n" + b"x" * 10
    got, failed = flow._download_attach_files({"files": {"id_card_mid": "M1", "slip_mid": None}})
    ck("有图的下载成 PNG 附件，没图的跳过",
       len(got) == 1 and got[0]["content_type"] == "image/png" and not failed, got)

    flow._out.download_content = lambda mid: b"\xff\xd8\xff\xe0" + b"x" * 10
    got, failed = flow._download_attach_files({"files": {"id_card_mid": "M1"}})
    ck("认不出的头按 jpeg", got and got[0]["content_type"] == "image/jpeg", got)

    flow._out.download_content = lambda mid: None
    got, failed = flow._download_attach_files({"files": {"id_card_mid": "M1", "slip_mid": "M2"}})
    ck("下载失败按附件失败如实记，不阻断建单",
       not got and len(failed) == 2 and all("error" in f for f in failed), failed)

    # ── 11. 主档变更后的回头路 ───────────────────────────────────────
    pushed.clear()
    sent_structured.clear()
    uid11 = "verify-flow-" + secrets.token_hex(4)
    store.clear_session(uid11)
    n11 = secrets.token_hex(8)
    store.set_session(uid11, "booking_review", {"nonce": n11, "qa": QA})
    store.consume_nonce(uid11, "booking_review", n11)
    flow._resume_after_master_change(
        binding, uid11, {"nonce": n11, "qa": QA},
        {"preflight": "changed", "qa": QA},
    )
    ck("主档改名 → 重发摘要 + 新预览卡（草稿仍在待确认态）",
       len(sent_structured) == 2
       and ((store.get_session(uid11) or {}).get("payload") or {}).get("nonce") is not None,
       len(sent_structured))

    uid12 = "verify-flow-" + secrets.token_hex(4)
    store.clear_session(uid12)
    n12 = secrets.token_hex(8)
    store.set_session(uid12, "booking_review", {"nonce": n12, "qa": QA})
    store.consume_nonce(uid12, "booking_review", n12)
    flow._resume_after_master_change(
        binding, uid12, {"nonce": n12, "qa": QA},
        {"preflight": "deleted", "field": "advisor", "error_code": "ERR_DMS_ADVISOR_UNMATCHED"},
    )
    ck("顾问被删 → 丢弃草稿（不能拿一份对不上的名册建单）",
       store.get_session(uid12) is None and sent_structured, len(sent_structured))

    sent_structured.clear()
    calls = []
    import pearnly_dms.booking.conversation.booking_qa as bq

    bq.send_step = lambda *a, **k: calls.append(a)
    uid13 = "verify-flow-" + secrets.token_hex(4)
    store.clear_session(uid13)
    n13 = secrets.token_hex(8)
    store.set_session(uid13, "booking_review", {"nonce": n13, "qa": QA})
    store.consume_nonce(uid13, "booking_review", n13)
    flow._resume_after_master_change(
        binding, uid13, {"nonce": n13, "qa": QA},
        {"preflight": "deleted", "field": "place", "error_code": "ERR_DMS_MASTER_UNMATCHED"},
    )
    ck("其它主档被删 → 退回该步骤重选（草稿回到逐问态）",
       (store.get_session(uid13) or {}).get("state") == "booking_qa" and len(calls) == 1,
       (store.get_session(uid13) or {}).get("state"), )

    for leftover in (uid, uid2, uid3, uid4, uid5, uid6, uid7, uid8, uid9, uid10, uid11, uid12, uid13):
        store.clear_session(leftover)

    failed = [x for x in ok if not x]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(ok) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
