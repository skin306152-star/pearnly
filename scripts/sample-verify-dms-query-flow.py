# -*- coding: utf-8 -*-
"""查资料流程层验证：双重权限、四路维度、看板条件、本地读取、翻页。

这一层最要紧的是**权限的二次校验**：动作入口查一次（没权限连菜单都进不去），
**发送结果之前再查一次** —— 即使本地读取也可能遇到并发收权；
少了第二次，刚被收权的人还会收到一份数据。这条是静默的，界面上看不出来。

出口和同步调用换记录器，读数据换假的，**会话用真的**；无外部读取。

用法: scripts/sample-verify-dms-query-flow.sh <组> <站点>
"""


def main():
    import secrets

    from pearnly_core import line_session as store
    from pearnly_dms.line import cards as cards
    from pearnly_dms.query import query_cards as qc
    from pearnly_dms.query import query_flow as qf
    from pearnly_dms.query import sales_readback as rb

    ok = []

    def ck(label, cond, detail=""):
        ok.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + label + ((" - " + str(detail)) if detail else ""))

    replied, pushed, sent, queued = [], [], [], []
    qf._reply = lambda reply_token, text: replied.append(text)
    qf._push = lambda line_user_id, text, reply_token="": pushed.append(text)
    qf._send = lambda line_user_id, msg, reply_token="": sent.append(msg)
    real_records, real_top = qf._run_records, qf._run_top
    record_spy = lambda *args: queued.append(("records", "_run_records", args))
    top_spy = lambda *args: queued.append(("top", "_run_top", args))
    qf._run_records, qf._run_top = record_spy, top_spy
    qf.current_binding = lambda *args: True
    rb.query_capabilities = lambda user: {"ok": True, "top_sales": True}

    allowed = {"v": True}
    qf._can_query = lambda binding: allowed["v"]

    uid = "verify-query-" + secrets.token_hex(4)
    store.clear_session(uid)
    binding = {"tenant_id": "one761900.localhost", "user_id": "user@x", "line_user_id": uid}

    # ── 1. 形状 ────────────────────────────────────────────────────────
    ck("动作码 = 菜单项 + 卡片那 7 个",
       qf.QUERY_ACTIONS == {cards.ACT_MENU_QUERY, *qc.QUERY_ACTION_NAMES},
       sorted(qf.QUERY_ACTIONS))
    ck("要打字的维度 8 个（与卡片层的提示表一致）",
       qf._TEXT_FIELDS == {"advisor", "vehicle", "customer", "color", "booking_date",
                           "delivery_date", "sales_doc_no", "engine_no"}
       and qf._TEXT_FIELDS == set(qc._PROMPTS), sorted(qf._TEXT_FIELDS))
    ck("自定义期间的格式：`起-止 条数`（日期可带空格）",
       bool(qf._DATE_RE.match("01/08/2569-31/08/2569 15"))
       and bool(qf._DATE_RE.match("01/08/2569 - 31/08/2569 5"))
       and not qf._DATE_RE.match("01/08/2569 15"))

    # ── 2. 权限门 ──────────────────────────────────────────────────────
    replied.clear()
    allowed["v"] = False
    qf.open_query(binding, uid, "tok")
    ck("没权限 → 回拒答话术，且**不落会话**",
       replied[-1] == qc.TXT_DENIED and store.get_session(uid) is None, replied[-1:])

    replied.clear()
    sent.clear()
    allowed["v"] = True
    qf.open_query(binding, uid, "tok")
    ck("有权限 → 落 query_menu 态并出'选类别'卡",
       (store.get_session(uid) or {}).get("state") == "query_menu"
       and sent and sent[-1]["type"] == "text", (store.get_session(uid) or {}).get("state"))

    replied.clear()
    allowed["v"] = False
    qf.handle_postback(binding, uid, "tok", qc.ACT_QUERY_DIMENSION, {"dimension": "latest"}, None)
    ck("没权限时点维度按钮 → 拒答（不读数据）", replied[-1] == qc.TXT_DENIED, replied[-1:])

    ck("菜单项也走同一个门（没权限进不去）",
       (lambda: (replied.clear(), qf.handle_postback(binding, uid, "tok",
                                                     cards.ACT_MENU_QUERY, {}, None),
                 replied[-1] == qc.TXT_DENIED)[-1])())
    allowed["v"] = True

    # ── 3. 四路维度 ────────────────────────────────────────────────────
    replied.clear()
    qf._pick_type(binding, uid, "tok", "inventory")
    ck("类别 2/3（还没开）→ 回'这一版还没开'", replied[-1] == qc.TXT_COMING_SOON, replied[-1:])
    qf._pick_type(binding, uid, "tok", "sales")
    ck("选销售记录 → 落 query_sales 态并出维度卡",
       (store.get_session(uid) or {}).get("state") == "query_sales", (store.get_session(uid) or {}).get("state"))

    queued.clear()
    qf._pick_dimension(binding, uid, "tok", "latest")
    ck("'最新一览' → 直接查（记录码 + active）",
       queued and queued[-1][0] == "records"
       and queued[-1][2][2]["field"] == "booking_date"
       and queued[-1][2][2]["status"] == "active", queued and queued[-1][2][2])

    sent.clear()
    qf._pick_dimension(binding, uid, "tok", "vehicle")
    sess = store.get_session(uid) or {}
    ck("要打字的维度 → 落 input 态并把字段记下，然后出提示",
       sess.get("state") == "query_sales_input" and sess["payload"].get("field") == "vehicle"
       and sent and sent[-1]["text"] == qc._t("ASK_VEHICLE"), (sess.get("payload"), len(sent)))

    sent.clear()
    qf._pick_dimension(binding, uid, "tok", "finance")
    ck("金融/分配/合同 → 出状态卡（不落新态）", sent and sent[-1]["type"] == "text", len(sent))

    sent.clear()
    qf._pick_dimension(binding, uid, "tok", "top")
    ck("'畅销榜' → 落 query_top_group 态并出分组卡",
       (store.get_session(uid) or {}).get("state") == "query_top_group"
       and sent and sent[-1]["type"] == "text")

    # ── 4. 看板条件：分组 / 指标 / 期间 ────────────────────────────────
    qf._pick_top_group(binding, uid, "tok", "没这类")
    ck("认不出的分组 → 落到默认'按车型'（不是报错）",
       (store.get_session(uid) or {})["payload"].get("group") == "model"
       and (store.get_session(uid) or {}).get("state") == "query_top_metric")

    qf._pick_top_group(binding, uid, "tok", "subtype")
    qf._pick_top_metric(binding, uid, "tok", store.get_session(uid), "没这指标")
    ck("认不出的指标 → 落到默认'按台数'，且**保住已选的分组**",
       (store.get_session(uid) or {})["payload"] == {"group": "subtype", "metric": "quantity"},
       (store.get_session(uid) or {})["payload"])

    sent.clear()
    qf._pick_top_period(binding, uid, "tok", store.get_session(uid), {"period": "custom"})
    ck("自定义期间 → 落 query_top_custom 态并出带例子的提示",
       (store.get_session(uid) or {}).get("state") == "query_top_custom"
       and sent and "01/08/2569" in sent[-1]["text"])

    queued.clear()
    qf._pick_top_period(binding, uid, "tok", store.get_session(uid),
                        {"period": "month", "limit": "99"})
    p = queued[-1][2][2]
    from datetime import date

    today = date.today()
    ck("本月 → 起 '01/本月/佛历'、止'今天/佛历'，且条数夹到 30",
       p["date_from"] == "01/%02d/%d" % (today.month, today.year + 543)
       and p["date_to"] == "%02d/%02d/%d" % (today.day, today.month, today.year + 543)
       and p["limit"] == 30, (p["date_from"], p["date_to"], p["limit"]))

    queued.clear()
    qf._pick_top_period(binding, uid, "tok", store.get_session(uid), {"period": "year", "limit": "0"})
    p = queued[-1][2][2]
    ck("本年 → 起 '01/01/佛历'，条数夹到 1",
       p["date_from"] == "01/01/%d" % (today.year + 543) and p["limit"] == 1, (p["date_from"], p["limit"]))

    # ── 5. 会话态 + 同步本地调用（两条路） ──────────────────────────────
    queued.clear()
    qf._begin_records(binding, uid, field="customer", query="สมชาย", status="ready", limit=99, page=0)
    sess = store.get_session(uid) or {}
    ck("记录：条件进会话、进 query_results 态、直接调用本地读取",
       sess.get("state") == "query_results" and sess["payload"]["query_kind"] == "records"
       and queued[-1][1] == "_run_records", (sess.get("state"), queued[-1][:2]))
    ck("⚠️ 记录的页大小上限是 **10**（看板才是 30），页码至少 1",
       sess["payload"]["limit"] == 10 and sess["payload"]["page"] == 1, sess["payload"])

    queued.clear()
    qf._begin_top(binding, uid, group="model", metric="amount",
                  date_from="01/09/2569", date_to="18/09/2569", limit=99)
    sess = store.get_session(uid) or {}
    ck("看板：条件进会话、直接调用本地读取，页大小上限 30",
       sess["payload"]["query_kind"] == "top" and sess["payload"]["limit"] == 30
       and queued[-1][1] == "_run_top", sess["payload"])

    # ── 6. 真实结果处理器：**发结果前再查一次权限** ────────────────────
    qf._run_records, qf._run_top = real_records, real_top
    store.set_session(uid, "query_results", {"query_kind": "records", "field": "booking_no",
                      "query": "", "status": "active", "limit": 10, "page": 1})
    sent.clear()
    pushed.clear()
    allowed["v"] = False
    qf._run_records(binding, uid, {"field": "booking_no", "query": "", "status": "active",
                                   "limit": 10, "page": 1})
    ck("读取之前没权限 → 静默不发（连拒答都不发，避免骚扰）",
       not sent and not pushed, (len(sent), len(pushed)))

    allowed["v"] = True
    calls = {"n": 0}

    def flip_after_read(*args, **kwargs):
        """模拟本地读取期间被收权：读之前有、读之后没。"""
        calls["n"] += 1
        allowed["v"] = False
        return {"ok": True, "rows": [{"booking_no": "BK1"}]}

    qf.sales_readback.fetch_sales_records = flip_after_read
    sent.clear()
    pushed.clear()
    qf._run_records(binding, uid, {"field": "booking_no", "query": "", "status": "active",
                                   "limit": 10, "page": 1})
    ck("读完这几秒里权限被收回 → **只发拒答，不发数据**（这就是第二次校验的用处）",
       pushed and pushed[-1] == qc.TXT_DENIED and not sent, (pushed[-1:], len(sent)))

    allowed["v"] = True
    qf.sales_readback.fetch_sales_records = lambda *a, **k: {"ok": False, "error_code": "ERR_NO_CREDS"}
    sent.clear()
    pushed.clear()
    qf._run_records(binding, uid, {"field": "booking_no", "query": "", "status": "active",
                                   "limit": 10, "page": 1})
    ck("读不到：没有可用账号 → 直说没账号（不拿'读失败'糊弄）",
       pushed and pushed[-1] == qc.TXT_NO_ENDPOINT, pushed[-1:])

    qf.sales_readback.fetch_sales_records = lambda *a, **k: {
        "ok": False, "error_code": "ERR_DMS_TECHNICAL", "error_friendly": {"th": "读不到啦"}}
    pushed.clear()
    qf._run_records(binding, uid, {"field": "booking_no", "query": "", "status": "active",
                                   "limit": 10, "page": 1})
    ck("读失败：有友好话术就用它", pushed and pushed[-1] == "读不到啦", pushed[-1:])

    qf.sales_readback.fetch_sales_records = lambda *a, **k: {"ok": False,
                                                            "error_code": "ERR_UNEXPECTED"}
    pushed.clear()
    qf._run_records(binding, uid, {"field": "booking_no", "query": "", "status": "active",
                                   "limit": 10, "page": 1})
    ck("读失败：没有友好话术 → 兜底'读不到请重试'", pushed and pushed[-1] == qc.TXT_QUERY_FAILED)

    board = {"ok": True, "rows": [{"booking_no": "BK9"}], "page": 1, "limit": 10}
    qf.sales_readback.fetch_sales_records = lambda *a, **k: board
    sent.clear()
    qf._run_records(binding, uid, {"field": "booking_no", "query": "", "status": "active",
                                   "limit": 10, "page": 1})
    ck("读成功 → 发记录看板（调用者未传 reply token 的兼容路径）",
       len(sent) == 1 and sent[-1]["type"] == "flex", len(sent))

    qf.sales_readback.fetch_top_sales = lambda *a, **k: {"ok": True, "metric": "quantity",
                                                        "rows": [{"label": "A", "value": 1}]}
    store.set_session(uid, "query_results", {"query_kind": "top", "group": "model",
                      "metric": "quantity", "date_from": "01/09/2569", "date_to": "24/09/2569", "limit": 10, "page": 1})
    sent.clear()
    qf._run_top(binding, uid, {"group": "model", "metric": "quantity", "date_from": "01/09/2569",
                               "date_to": "24/09/2569", "limit": 10, "page": 1})
    ck("看板任务成功 → 推畅销卡", len(sent) == 1 and sent[-1]["type"] == "flex", len(sent))

    # ── 7. 打字那两条路（记录同步调用参数） ──────────────────────────────
    qf._run_records, qf._run_top = record_spy, top_spy
    queued.clear()
    store.set_session(uid, "query_sales_input", {"field": "customer"})
    ck("打字态：普通文本进查询（带上字段与 active）",
       qf.handle_text(binding, uid, "tok", store.get_session(uid), "คุณสมชาย") is True
       and queued and queued[-1][2][2]["query"] == "คุณสมชาย"
       and queued[-1][2][2]["field"] == "customer", queued and queued[-1][2][2])

    queued.clear()
    sent.clear()
    store.set_session(uid, "query_sales_input", {"field": "customer"})
    qf.handle_text(binding, uid, "tok", store.get_session(uid), "   ")
    ck("打字态：空输入 → 原样重问（不拿空串去查一整页）",
       not queued and sent and sent[-1]["text"] == qc._t("ASK_CUSTOMER"), len(sent))

    ck("别的态不接管文本（交上层）",
       qf.handle_text(binding, uid, "tok", {"state": "reviewing", "payload": {}}, "随便") is False)

    queued.clear()
    store.set_session(uid, "query_top_custom", {"group": "type", "metric": "amount"})
    qf.handle_text(binding, uid, "tok", store.get_session(uid), "01/08/2569-31/08/2569 15")
    p = queued[-1][2][2]
    ck("自定义期间：解析出起止与条数，并带上之前选的分组/指标",
       p["date_from"] == "01/08/2569" and p["date_to"] == "31/08/2569" and p["limit"] == 15
       and p["group"] == "type" and p["metric"] == "amount", p)

    queued.clear()
    sent.clear()
    # ⚠️ 上一例成功后会话已被推进到 query_results（那是它的正常行为）——
    # 要测"格式不对"必须先回到"等打字"那个态，否则根本走不到解析那一步。
    store.set_session(uid, "query_top_custom", {"group": "type", "metric": "amount"})
    qf.handle_text(binding, uid, "tok", store.get_session(uid), "8/8/2569 15")
    ck("自定义期间：格式不对 → 重问（不拿半截日期去查）", not queued and sent, len(sent))
    queued.clear()
    sent.clear()
    store.set_session(uid, "query_top_custom", {"group": "type", "metric": "amount"})
    qf.handle_text(binding, uid, "tok", store.get_session(uid), "01/08/2569-31/08/2569 99")
    ck("自定义期间：条数超 30 → 重问（不在服务端偷偷夹）", not queued and sent, len(sent))

    # ── 8. 翻页 ────────────────────────────────────────────────────────
    replied.clear()
    qf._paginate(binding, uid, "tok", {"state": "query_sales", "payload": {}}, {"kind": "records"})
    ck("会话不在结果态 → 说过期、让人重来", replied[-1] == qc.TXT_EXPIRED, replied[-1:])

    replied.clear()
    qf._paginate(binding, uid, "tok",
                 {"state": "query_results", "payload": {"query_kind": "records"}},
                 {"kind": "top"})
    ck("卡片上的类别和会话里对不上 → 说过期（免得翻上一批结果的页）",
       replied[-1] == qc.TXT_EXPIRED, replied[-1:])

    queued.clear()
    qf._paginate(binding, uid, "tok",
                 {"state": "query_results",
                  "payload": {"query_kind": "records", "field": "customer", "query": "ก",
                              "status": "ready", "limit": 10, "page": 1}},
                 {"kind": "records", "direction": "next"})
    ck("下一页：页码 +1 且条件原样带过去",
       queued[-1][2][2]["page"] == 2 and queued[-1][2][2]["query"] == "ก"
       and queued[-1][2][2]["status"] == "ready", queued[-1][2][2])

    queued.clear()
    qf._paginate(binding, uid, "tok",
                 {"state": "query_results",
                  "payload": {"query_kind": "records", "field": "customer", "query": "ก",
                              "status": "ready", "limit": 10, "page": 1}},
                 {"kind": "records", "direction": "prev"})
    ck("第一页再点上一页 → 停在第一页（不变成第 0 页）",
       queued[-1][2][2]["page"] == 1, queued[-1][2][2]["page"])

    queued.clear()
    qf._paginate(binding, uid, "tok",
                 {"state": "query_results",
                  "payload": {"query_kind": "top", "group": "model", "metric": "amount",
                              "date_from": "01/09/2569", "date_to": "24/09/2569", "limit": 10, "page": 2}},
                 {"kind": "top", "direction": "refresh"})
    ck("重读：页码不动、条件原样",
       queued[-1][1] == "_run_top" and queued[-1][2][2]["page"] == 2
       and queued[-1][2][2]["metric"] == "amount", queued[-1][2][2])

    # ── 9. 模块里不许留泰文 ────────────────────────────────────────────
    import re as _re

    src = open(qf.__file__, encoding="utf-8").read()
    thai = sorted(set(_re.findall(r"[\u0e00-\u0e7f]+", src)))
    ck("流程层里一个泰文字符都不留（分页过期那句也已进包）", not thai, thai[:5])

    # ── 10. 接线：菜单第 5 项 / 单字 5 / 路由 / postback 分发 ──────────
    from pearnly_dms.intake import flow as dms_flow
    from pearnly_dms.menu import menu_cards as mc
    from pearnly_dms.menu import menu_flow as mf
    from pearnly_dms.line import text_router as router

    readonly = {"v": False}

    class FakeAccess:
        @staticmethod
        def can_query(binding):
            return readonly["v"]

    mf.query_access = FakeAccess
    ck("门开着以后：花名册给了权限就画第 5 项，没给就不画",
       readonly["v"] is False and mf.can_query({}) is False
       and (readonly.update(v=True) or mf.can_query({}) is True)
       and cards.ACT_MENU_QUERY in str(mc.menu_card(can_query=mf.can_query({})))
       and cards.ACT_MENU_QUERY not in str(mc.menu_card(can_query=False)),
       (mf.QUERY_FLOW_LANDED, mf.can_query({})))

    opened = []
    qf.open_query = lambda binding, luid, token: opened.append((luid, token))
    mf.handle_choice(binding, uid, "tok", {"state": "menu", "payload": {}}, "5")
    ck("menu 态打单字 5 → 真的进查询", len(opened) == 1, opened)
    readonly["v"] = False

    routed = []
    router.query_flow = type("Q", (), {
        "handle_text": staticmethod(lambda *a, **k: routed.append(a[4]) or a[4] == "找这个"),
    })
    router.menu_flow = type("M", (), {
        "open_menu": staticmethod(lambda *a, **k: None),
        "handle_choice": staticmethod(lambda *a, **k: False),
        "can_query": staticmethod(lambda b: False),
    })
    router.menu_cards = type("C", (), {"menu_card": staticmethod(lambda **k: {"type": "flex"})})
    store.set_session(uid, "query_top_custom", {"group": "model", "metric": "quantity"})
    before = store.get_session(uid)
    router.route(binding, uid, "tok", "找这个")
    ck("路由：查询态的文本文本先归查询（排在订车之前）", routed == ["找这个"], routed)
    store.set_session(uid, "reviewing", {"nonce": "n"})
    router_replies = []
    router.line = type("L", (), {"reply": staticmethod(lambda t, m: router_replies.append(m))})
    router.route(binding, uid, "tok", "别的文本")
    # 第二段文本**会先经过查询层**（路由顺序就是这样），查询层不认识这个态 → 返 False →
    # 路由继续走后面的分支（这里是"待确认态"提醒）。所以要两件事同时成立。
    ck("路由：别的态先经过查询层但被它拒掉，然后照旧走后面的分支",
       routed == ["找这个", "别的文本"] and router_replies, (routed, len(router_replies)))

    hits = []
    real_query_pb = qf.handle_postback
    real_menu_pb = mf.handle_postback
    qf.handle_postback = lambda binding, luid, token, action, values, sess: hits.append(
        ("query", action))
    mf.handle_postback = lambda *a, **k: hits.append(("menu", a[3]))
    try:
        dms_flow.handle_postback(binding, uid, "tok",
                                 {"data": "action=%s&dimension=top" % qc.ACT_QUERY_DIMENSION})
        ck("postback：查询动作归查询层（自己的权限门在里面）",
           hits == [("query", qc.ACT_QUERY_DIMENSION)], hits)
        hits.clear()
        store.set_session(uid, "menu", {})
        dms_flow.handle_postback(binding, uid, "tok", {"data": "action=%s" % cards.ACT_MENU_QUERY})
        ck("postback：菜单里的'查资料'也归查询层（不是菜单层）",
           hits == [("query", cards.ACT_MENU_QUERY)], hits)
    finally:
        qf.handle_postback = real_query_pb
        mf.handle_postback = real_menu_pb

    store.clear_session(uid)
    failed = [x for x in ok if not x]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(ok) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
