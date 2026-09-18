# -*- coding: utf-8 -*-
"""LINE 会话层验证：状态 / TTL / nonce 防双写。

会话层是**所有逐问的地基** —— 它错了，后面每一步都错。而它最容易错的地方是
"守卫"：nonce 有没有真被消费掉、过期是不是真的当没有。所以这一层单独验。

不连网络、不碰 DMS，只用本站数据库。
"""


def main():
    import frappe

    from pearnly_core import line_session as ls

    results = []
    USER = "U_verify_session_0001"

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def note(label, detail):
        print("  " + label + ": " + str(detail))

    ls.clear_session(USER)

    # ── 1. 存取往返 ───────────────────────────────────────────────────
    ls.set_session(USER, "reviewing", {"nonce": "abc123", "draft": {"name": "X"}})
    sess = ls.get_session(USER)
    check("写进去能读回来", bool(sess) and sess.get("state") == "reviewing", sess and sess.get("state"))
    check("载荷往返无损",
          (sess or {}).get("payload", {}).get("nonce") == "abc123"
          and (sess or {}).get("payload", {}).get("draft", {}).get("name") == "X",
          (sess or {}).get("payload"))
    check("覆盖写会更新（upsert）",
          (ls.set_session(USER, "picking", {"a": 1}) or ls.get_session(USER)).get("state") == "picking",
          ls.get_session(USER))

    # ── 2. TTL 是状态的属性 ────────────────────────────────────────────
    check("普通态 30 分钟", ls.state_ttl_minutes("reviewing") == 30, ls.state_ttl_minutes("reviewing"))
    check("待选车/待确认订车 120 分钟（会话要活过面板 token）",
          ls.state_ttl_minutes("booking_review") == 120, ls.state_ttl_minutes("booking_review"))
    ls.set_session(USER, "booking_review", {"x": 1})
    exp = frappe.db.get_value("Pearnly LINE Session", {"line_user_id": USER}, "expires_at")
    left = frappe.utils.time_diff_in_seconds(exp, frappe.utils.now()) / 60
    check("写进去的过期时间约 120 分钟", 118 <= left <= 121, "%.1f 分钟" % left)
    note("过期时间", exp)

    # ⚠️ 0 是「立刻过期」这个合法值，不能被当成"没传"而回落默认 TTL
    ls.set_session(USER, "reviewing", {"y": 1}, ttl_minutes=0)
    check("ttl_minutes=0 → 立刻过期（0 是合法值，不是「没传」）",
          ls.get_session(USER) is None, ls.get_session(USER))

    # ── 3. verify_nonce：只校验不消费 ──────────────────────────────────
    ls.set_session(USER, "reviewing", {"nonce": "n1"})
    sess = ls.get_session(USER)
    check("状态对 + nonce 对 → 通过", ls.verify_nonce(sess, "n1", "reviewing") is True)
    check("nonce 不对 → 不通过", ls.verify_nonce(sess, "WRONG", "reviewing") is False)
    check("状态不对 → 不通过", ls.verify_nonce(sess, "n1", "booking_review") is False)
    check("空 nonce → 不通过", ls.verify_nonce(sess, "", "reviewing") is False)
    check("只校验不消费（nonce 还在）",
          (ls.get_session(USER) or {}).get("payload", {}).get("nonce") == "n1",
          (ls.get_session(USER) or {}).get("payload"))

    # ── 4. consume_nonce：一次性，防双击双写 ───────────────────────────
    first = ls.consume_nonce(USER, "reviewing", "n1")
    check("第一次确认 → 返回 payload", bool(first) and first.get("nonce") == "n1",
          first and first.get("nonce"))
    second = ls.consume_nonce(USER, "reviewing", "n1")
    check("同一个 nonce 第二次 → None（防双击双写）", second is None, second)
    check("nonce 被清成 None 而不是删键（后续比较必然不等）",
          (ls.get_session(USER) or {}).get("payload", {}).get("nonce", "MISSING") is None,
          (ls.get_session(USER) or {}).get("payload"))

    # ── 5. 态不符时不消费 ──────────────────────────────────────────────
    ls.set_session(USER, "picking", {"nonce": "n2"})
    check("态不符 → 不消费也不返回",
          ls.consume_nonce(USER, "booking_review", "n2") is None)
    check("态不符时 nonce 没被吃掉",
          (ls.get_session(USER) or {}).get("payload", {}).get("nonce") == "n2",
          (ls.get_session(USER) or {}).get("payload"))

    # ── 6. replace_review_payload：受守卫地换草稿 ──────────────────────
    ls.set_session(USER, "booking_review", {"nonce": "r1", "car": "A"})
    check("nonce 不符 → 拒绝替换",
          ls.replace_review_payload(USER, "WRONG", {"nonce": "r2", "car": "B"}) is False)
    check("拒绝后原草稿没变",
          (ls.get_session(USER) or {}).get("payload", {}).get("car") == "A",
          (ls.get_session(USER) or {}).get("payload"))
    check("nonce 相符 → 替换成功并轮换 nonce",
          ls.replace_review_payload(USER, "r1", {"nonce": "r2", "car": "B"}) is True)
    check("替换后是新草稿",
          (ls.get_session(USER) or {}).get("payload", {}).get("car") == "B"
          and (ls.get_session(USER) or {}).get("payload", {}).get("nonce") == "r2",
          (ls.get_session(USER) or {}).get("payload"))

    # ── 7. 清理 ────────────────────────────────────────────────────────
    ls.clear_session(USER)
    check("清会话后读不到", ls.get_session(USER) is None)

    failed = [x for x in results if not x[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    import sys

    import frappe

    site = sys.argv[1] if len(sys.argv) > 1 else "qd65956.localhost"
    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    try:
        main()
    finally:
        frappe.destroy()
