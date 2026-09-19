# -*- coding: utf-8 -*-
"""订车提交登记验证（防重复建单的那一层；用真会话 + 真 Redis 锁，跑完清理）。

这一层只防一件事，但代价很大：**同一个执行体重复提交，在 DMS 里写成两张单据**。
错法（把 CAS 写成"先读再写"）会静默退化成没有保护，平时测不出来。所以这里把每条
判据都钉死，尤其是"**不许顶掉别人的草稿**"和"**顺号重试只放同一个执行体**"。

用法: scripts/sample-verify-dms-booking-attempt.sh <组> <站点>
"""


def main():
    import secrets

    from pearnly_core import line_session as store
    from pearnly_dms.booking import booking_attempt as attempt
    from pearnly_dms.contracts.client_base import DmsClientError

    ok = []

    def ck(label, cond, detail=""):
        ok.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + label + ((" - " + str(detail)) if detail else ""))

    # 用一个显眼的假 LINE 用户；开场/收尾都清干净，不留会话垃圾
    uid = "verify-attempt-" + secrets.token_hex(4)
    store.clear_session(uid)

    def new_draft(nonce):
        store.set_session(uid, "booking_review", {"nonce": nonce, "qa": {"step": "pay_more"}})

    n1 = secrets.token_hex(8)
    new_draft(n1)

    # ── 1. 没认领过（nonce 还在）不许登记 ──────────────────────────────
    ck("草稿还没被认领（nonce 还在）→ 拒绝登记（不许先写后认）",
       store.record_booking_attempt(uid, n1, "BK001", "own-1") is False)

    # ── 2. 认领之后才可以登记 ─────────────────────────────────────────
    store.consume_nonce(uid, "booking_review", n1)
    payload = (store.get_session(uid) or {}).get("payload") or {}
    ck("认领后会话里留下认领标记（记的是被消费的 nonce）",
       payload.get("nonce") is None and payload.get("booking_claim") == n1, payload)
    ck("认领的那张草稿可以登记", store.record_booking_attempt(uid, n1, "BK001", "own-1") is True)
    ck("登记内容进了会话（nonce/号/执行体）",
       ((store.get_session(uid) or {}).get("payload") or {}).get("booking_attempt")
       == {"nonce": n1, "booking_no": "BK001", "owner": "own-1"})

    # ── 3. 同一张草稿不许被第二个执行体再登记 ─────────────────────────
    ck("别的执行体不许再登记同一张草稿（这就是防重复建单）",
       store.record_booking_attempt(uid, n1, "BK002", "own-2") is False)
    ck("连自己（同 owner）也不能原号再登记一次",
       store.record_booking_attempt(uid, n1, "BK001", "own-1") is False)

    # ── 4. 顺号重试：同一个执行体、号对得上才放行 ─────────────────────
    ck("同一个执行体凭上一个号可以顺号重试",
       store.record_booking_attempt(uid, n1, "BK002", "own-1", previous_no="BK001") is True)
    ck("顺号重试后标记已更新",
       ((store.get_session(uid) or {}).get("payload") or {}).get("booking_attempt")
       == {"nonce": n1, "booking_no": "BK002", "owner": "own-1"})
    ck("拿一个对不上的旧号不许重试（别的执行体 / 别的号）",
       store.record_booking_attempt(uid, n1, "BK003", "own-1", previous_no="BK999") is False)

    # ── 5. 换过的草稿顶不掉已登记的那张 ───────────────────────────────
    n2 = secrets.token_hex(8)
    store.set_session(uid, "booking_review", {"nonce": n2})
    store.consume_nonce(uid, "booking_review", n2)
    ck("草稿换过之后（认领标记是新 nonce）旧登记不许被改写",
       store.record_booking_attempt(uid, n1, "BK009", "own-9") is False)
    ck("新草稿自己可以登记",
       store.record_booking_attempt(uid, n2, "BK010", "own-2") is True)

    # ── 6. 态不对 / 缺参数一律拒绝 ────────────────────────────────────
    uid2 = "verify-attempt-" + secrets.token_hex(4)
    store.clear_session(uid2)
    n3 = secrets.token_hex(8)
    store.set_session(uid2, "reviewing", {"nonce": n3})
    store.consume_nonce(uid2, "reviewing", n3)
    ck("不是待确认订车态（建档态）→ 拒绝登记",
       store.record_booking_attempt(uid2, n3, "BK100", "own-3") is False)
    ck("缺参（号/执行体/nonce）→ 拒绝登记",
       store.record_booking_attempt(uid2, "", "BK100", "own-3") is False
       and store.record_booking_attempt(uid2, n3, "", "own-3") is False
       and store.record_booking_attempt(uid2, n3, "BK100", "") is False)

    # ── 7. LINE 层那薄层 ─────────────────────────────────────────────
    # 这时会话里登记的是 n2 那张（第 5 节刚换过草稿）
    got = attempt.pending("t", uid, {"nonce": n2})
    ck("同一张草稿再点确认 → 直接回'结果未知'（不再提交一次）",
       bool(got) and got.get("error_code") == attempt.UNKNOWN
       and got.get("response_body", {}).get("retry_safe") is False, got and got.get("booking_no"))
    ck("换个 nonce（别的草稿）→ 没有待处理标记",
       attempt.pending("t", uid, {"nonce": "other"}) is None)

    ck("结果未知的话术带单号", "BK010" in attempt.message({"booking_no": "BK010"}))
    ck("待确认话术：有登记标记就说结果未知，否则用兜底",
       attempt.review_message({"payload": {"booking_attempt": {"booking_no": "BK010"}}}, "兜底")
       != "兜底"
       and attempt.review_message({"payload": {}}, "兜底") == "兜底")

    # 登记器：第一次成功、第二次（换号）要抛 ERR_DMS_BOOKING_ATTEMPT_BLOCKED
    uid3 = "verify-attempt-" + secrets.token_hex(4)
    store.clear_session(uid3)
    n4 = secrets.token_hex(8)
    store.set_session(uid3, "booking_review", {"nonce": n4})
    store.consume_nonce(uid3, "booking_review", n4)
    record = attempt.recorder("t", uid3, {"nonce": n4})
    record("BK200")
    ck("登记器第一次登记成功",
       ((store.get_session(uid3) or {}).get("payload") or {}).get("booking_attempt", {})
       .get("booking_no") == "BK200")
    record("BK201")  # 同一个执行体顺号重试（DMS 说这个号重复了）→ 本来就该放行
    ck("同一个执行体可以顺号重试，登记跟着往前走",
       ((store.get_session(uid3) or {}).get("payload") or {}).get("booking_attempt", {})
       .get("booking_no") == "BK201")
    try:
        # 换一个执行体（新的 recorder → 没有 previous_no）再登记同一张 → 必须拦下
        attempt.recorder("t", uid3, {"nonce": n4})("BK999")
        ck("换一个执行体再登记同一张草稿会被拦下", False, "没抛")
    except DmsClientError as exc:
        ck("换一个执行体再登记同一张草稿会被拦下",
           exc.error_code == "ERR_DMS_BOOKING_ATTEMPT_BLOCKED", exc.error_code)

    # ── 8. 收尾清理：只能清自己认领的那张 ─────────────────────────────
    ck("用错 nonce 清不动别人的草稿", store.clear_booking_attempt(uid, "wrong-nonce") is False)
    ck("自己的草稿能清掉（会话随之结束）",
       store.clear_booking_attempt(uid, n2) is True and store.get_session(uid) is None)
    ck("LINE 层的收尾走同一条路", attempt.clear_completed("t", uid3, {"nonce": n4}) is True
       and store.get_session(uid3) is None)

    store.clear_session(uid2)
    failed = [x for x in ok if not x]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(ok) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
