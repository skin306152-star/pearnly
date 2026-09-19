# -*- coding: utf-8 -*-
"""审批流的卡片层 + 流程层验证。

这一族的价值在"**以批准人自己的凭据执行**"与"**不许重复执行**"，两件事都静默：

· 执行用错凭据 → DMS 审计记错人（合规问题，界面上看不出来）；
· 认领不原子 / 没处理"抢不到" → 同一张申请被写两遍。

所以这里把提审、选人、改派、批、拒、执行、通知每条分叉都过一遍；需要外部依赖的地方
（花名册、存储、DMS 写入、推送）全换假的，**状态话术与卡片结构用真的**。

用法: scripts/sample-verify-dms-approval-flow.sh <组> <站点>
"""


def main():
    import re

    from pearnly_core import line_session as store
    from pearnly_dms import dms_approval_cards as ac
    from pearnly_dms import dms_approval_flow as af
    from pearnly_dms import dms_cards as cards

    ok = []

    def ck(label, cond, detail=""):
        ok.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + label + ((" - " + str(detail)) if detail else ""))

    replied, pushed, sent, queued = [], [], [], []
    af._reply = lambda reply_token, text: replied.append(text)
    af._push = lambda line_user_id, text: pushed.append((line_user_id, text))
    # 卡片既可以"回"也可以"推"：`dms_out.send` 有 reply_token 就回、没有就推。
    # 这里把两者都记下来（含 reply_token），断言时按收件人与有没有 token 分辨。
    af._send = lambda line_user_id, msg, reply_token="": sent.append(
        (line_user_id, reply_token, msg))
    af._start_loading = lambda line_user_id: None
    af._enqueue = lambda job, fn, *args: queued.append((job, fn.__name__, args))
    logs = []
    af._write_push_log = lambda **kw: logs.append(kw)

    # ── 1. 卡片层：结构与"不带 nonce" ────────────────────────────────────
    src = open(ac.__file__, encoding="utf-8").read()
    ck("卡片层里一个泰文字符都不留（含内联那 6 条）",
       not sorted(set(re.findall(r"[\u0e00-\u0e7f]+", src))))

    # 本命名空间自己的"防漏键"（`cards` 那道守卫只管 `dms.card.*`，不跨界）
    from pearnly_dms import i18n

    used = set(re.findall(r'_t\(\s*"([A-Za-z_][A-Za-z0-9_]*)"', src))
    used |= {"TXT_PICK_APPROVER", "BTN_ALL_APPROVERS", "BTN_CHANGE_APPROVER", "BTN_APPROVE",
             "BTN_REJECT", "TXT_NO_APPROVERS", "TXT_SENT_WAIT", "TXT_REQ_STALE",
             "TXT_REQ_EXPIRED", "TXT_REQ_APPROVED_ADMIN", "TXT_REQ_APPROVED_SALES",
             "TXT_REQ_REJECTED_ADMIN", "TXT_REQ_REJECTED_SALES", "TXT_EXEC_FAIL_ADMIN",
             "TXT_APPROVER_NO_ENDPOINT", "TXT_NOT_APPROVER", "TXT_PROCESSING",
             "TXT_SALES_FALLBACK", "LBL_APPROVER", "LBL_REQUESTER", "LBL_CUSTOMER",
             "TXT_PICK_APPROVER_SUB", "LBL_SENT_TO", "TITLE_REQUEST"}
    missing = sorted(k for k in used
                     if i18n.t("dms.card.approval." + k, lang="th").startswith("dms.card.approval."))
    ck("审批用到的文案键在包里都有（共查 %d 个）" % len(used), not missing, missing)

    picker = ac.picker_card("R1", [{"user_id": "u1", "display_name": "ผู้จัดการ"},
                                   {"user_id": "u2", "display_name": "หัวหน้า"}])
    data = [b["action"]["data"] for b in picker["contents"]["footer"]["contents"]]
    ck("选人卡：首项固定'发给全部'，后面逐个管理员",
       data[0] == "action=approval_target&req=R1&aid=all"
       and data[1] == "action=approval_target&req=R1&aid=u1", data)
    ck("选人卡最多 6 个管理员键（LINE 气泡体积）",
       len(ac.picker_card("R1", [{"user_id": "u%d" % i, "display_name": "a%d" % i}
                                 for i in range(10)])["contents"]["footer"]["contents"]) == 7)

    request = ac.request_card("R1", "พนักงานขาย", "คุณสมชาย", "C1",
                              [{"label": "ชื่อ", "old": "ก", "new": "ข"}])
    req_data = [b["action"]["data"] for b in request["contents"]["footer"]["contents"]]
    ck("审批卡：批/拒两个键，**只带申请号、不带 nonce**（一次性靠存储的原子认领）",
       req_data == ["action=approval_approve&req=R1", "action=approval_reject&req=R1"],
       req_data)
    ck("审批卡上写着谁提的、哪个客户、逐条新旧",
       "พนักงานขาย" in str(request) and "คุณสมชาย (C1)" in str(request)
       and "ชื่อ" in str(request))
    waiting = ac.waiting_card("R1", "ผู้จัดการ")
    ck("等待卡：发给谁 + 一个'改派'键",
       "ผู้จัดการ" in str(waiting)
       and waiting["contents"]["footer"]["contents"][0]["action"]["data"]
       == "action=approval_retarget&req=R1")
    ck("等不到指定人时等待卡也能显示'发给全部'",
       ac._t("BTN_ALL_APPROVERS") in str(ac.waiting_card("R1", None)))

    # ── 2. 审批策略与差异卡的主按钮 ─────────────────────────────────────
    ck("没登记过的改动类型**默认要审**（fail-closed）",
       af.requires_approval("新动作") is True and af.requires_approval("credit_auth") is True)
    ck("客户档案改写按泰方拍板**不审**（销售直写）",
       af.requires_approval("customer_profile") is False)

    def roster_as(role):
        return type("R", (), {
            "get_profile": staticmethod(lambda u: {"dms_role": role, "display_name": "x"}),
            "list_profiles": staticmethod(lambda: []),
            "line_user_id_of": staticmethod(lambda u: ""),
            "ADMIN_ROLES": ("DMS Admin",),
        })

    diff = [{"label": "a", "old": "1", "new": "2"}]
    # 按当前策略（客户档案**不审**）：销售拿到的是"去设置"，不是提审卡 —— 与线上一致。
    af.roster_store = roster_as("sales")
    card_, needs = af.exact_diff_card("u", diff, False, "n1", {})
    ck("销售 + 没配管理员凭据 + 该类改动不审 → 不给主按钮（提示去设置）",
       needs is False and "action=update" not in str(card_), needs)
    card_direct, needs_direct = af.exact_diff_card("u", diff, True, "n1", {})
    ck("销售 + 租户配了管理员凭据 → 直写（借老板凭据落地，不审）",
       needs_direct is False and "action=update" in str(card_direct), needs_direct)

    # 把策略打开（"信用授权上线时在此登记 True"）→ 提审那条链必须立刻可用：
    # 这一条证明链路是**接好的、待开的**，不是断的。
    af.APPROVAL_POLICY["customer_profile"] = True
    try:
        card_appr, needs_appr = af.exact_diff_card("u", diff, False, "n1", {})
        ck("策略一开（销售 + 要审）→ 出提审卡并标记'走审批'",
           needs_appr is True and ("action=%s" % cards.ACT_SUBMIT_APPROVAL) in str(card_appr),
           (needs_appr, cards.ACT_SUBMIT_APPROVAL))
    finally:
        af.APPROVAL_POLICY["customer_profile"] = False
    af.roster_store = type("R", (), {
        "get_profile": staticmethod(lambda u: {"dms_role": "admin", "display_name": "x"}),
        "list_profiles": staticmethod(lambda: []),
        "line_user_id_of": staticmethod(lambda u: ""),
        "ADMIN_ROLES": ("DMS Admin",),
    })
    card2, needs2 = af.exact_diff_card("u", [{"label": "a", "old": "1", "new": "2"}],
                                       False, "n1", {})
    ck("管理员 → 直写（不走审批）",
       needs2 is False and "action=update" in str(card2), needs2)
    af.roster_store = type("R", (), {
        "get_profile": staticmethod(lambda u: None),
        "list_profiles": staticmethod(lambda: []),
        "line_user_id_of": staticmethod(lambda u: ""),
        "ADMIN_ROLES": ("DMS Admin",),
    })
    card3, needs3 = af.exact_diff_card("u", [{"label": "a", "old": "1", "new": "2"}],
                                       False, "n1", {})
    ck("既不是销售也没有写权 → 不给主按钮（只提示去设置）",
       needs3 is False and "action=update" not in str(card3), needs3)

    # ── 3. 审批人名录与"能不能批" ───────────────────────────────────────
    profiles = [
        {"user_id": "a1", "dms_role": "admin", "status": "active", "line_user_id": "L1",
         "display_name": "ผู้จัดการ"},
        {"user_id": "a2", "dms_role": "admin", "status": "inactive", "line_user_id": "L2",
         "display_name": "停用的"},
        {"user_id": "a3", "dms_role": "admin", "status": "active", "line_user_id": "",
         "display_name": "没绑 LINE 的"},
        {"user_id": "s1", "dms_role": "sales", "status": "active", "line_user_id": "L3",
         "display_name": "销售"},
    ]
    af.roster_store = type("R", (), {
        "get_profile": staticmethod(lambda u: next((p for p in profiles if p["user_id"] == u), None)),
        "list_profiles": staticmethod(lambda: profiles),
        "line_user_id_of": staticmethod(
            lambda u: next((p["line_user_id"] for p in profiles if p["user_id"] == u), "")),
        "ADMIN_ROLES": ("DMS Admin",),
    })
    ck("能收审批卡的管理员：管理员 + 启用 + 已绑 LINE（三个条件都要）",
       [a["user_id"] for a in af._bound_approvers()] == ["a1"],
       af._bound_approvers())
    ck("能批：有管理员档案即可；停用的不行；销售不行",
       af._approver_eligible("a1") is True and af._approver_eligible("a2") is False
       and af._approver_eligible("s1") is False)
    af.frappe = type("F", (), {"get_roles": staticmethod(lambda u: ["DMS Admin"])})
    ck("没有档案但本身是租户管理员（老板）也能批",
       af._approver_eligible("老板") is True)

    # ── 4. 提审 ────────────────────────────────────────────────────────
    import secrets

    uid = "verify-approval-" + secrets.token_hex(4)
    store.clear_session(uid)
    binding = {"tenant_id": "one761900.localhost", "user_id": "s1", "line_user_id": uid}
    created = []
    real_store = af.approval_store
    af.approval_store = type("S", (), {
        "create_request": staticmethod(
            lambda operator, **kw: (created.append(kw), "R9")[1]),
        "get_request": staticmethod(lambda rid: None),
        "set_target": staticmethod(lambda rid, t: True),
        "claim": staticmethod(lambda rid, who: None),
        "finish": staticmethod(lambda rid, st: True),
    })

    replied.clear()
    store.set_session(uid, "reviewing", {"nonce": "n1", "scenario": "other"})
    af._submit(binding, uid, "tok", {"nonce": "n1"})
    ck("提审：草稿不是'有差异'那种 → 过期话术（不建申请）",
       not created and replied[-1] == cards.TXT_EXPIRED, replied[-1:])

    replied.clear()
    store.set_session(uid, "reviewing", {"nonce": "n1", "scenario": "exact_diff",
                                         "approval": True})
    saved = af.roster_store
    af.roster_store = type("R", (), {"list_profiles": staticmethod(lambda: []),
                                     "get_profile": staticmethod(lambda u: None),
                                     "line_user_id_of": staticmethod(lambda u: ""),
                                     "ADMIN_ROLES": ("DMS Admin",)})
    af._submit(binding, uid, "tok", {"nonce": "n1"})
    ck("一个能收卡的管理员都没有 → 明说并指路（不让销售以为提上去了）",
       replied[-1] == ac.TXT_NO_APPROVERS, replied[-1:])
    af.roster_store = saved

    replied.clear()
    sent.clear()
    created.clear()
    store.set_session(uid, "reviewing", {"nonce": "n2", "scenario": "exact_diff",
                                         "approval": True, "endpoint_id": "EP1",
                                         "customer_id": "C1", "field_diffs": [{"field": "name"}],
                                         "display_diffs": [{"label": "ชื่อ", "old": "a", "new": "b"}],
                                         "draft": {"name": "คุณสมชาย"}})
    af._submit(binding, uid, "tok", {"nonce": "n2"})
    ck("提审成功：建申请（带差异与草稿快照）→ 清会话 → 发选人卡",
       created and created[-1]["customer_id"] == "C1"
       and created[-1]["draft"]["fields"]["name"] == "คุณสมชาย"
       and created[-1]["draft"]["display_diffs"][0]["label"] == "ชื่อ"
       and store.get_session(uid) is None and sent and sent[-1][2]["type"] == "flex",
       created[-1] if created else None)
    ck("提审时 nonce 被消费掉（同一张卡再点必失效）",
       store.consume_nonce(uid, "reviewing", "n2") is None)

    # ── 5. 选人 / 改派 ─────────────────────────────────────────────────
    replied.clear()
    af.approval_store = type("S", (), {
        "get_request": staticmethod(lambda rid: {"id": "R9", "operator_user_id": "别人",
                                                 "status": "pending"}),
        "set_target": staticmethod(lambda rid, t: True),
    })
    af._target(binding, uid, "tok", {"req": "R9", "aid": "all"})
    ck("不是自己的单 → 过期话术（防拿别人的申请号乱点）",
       replied[-1] == cards.TXT_EXPIRED, replied[-1:])

    replied.clear()
    af.approval_store = type("S", (), {
        "get_request": staticmethod(lambda rid: {"id": "R9", "operator_user_id": "s1",
                                                 "status": "approved"}),
    })
    af._target(binding, uid, "tok", {"req": "R9", "aid": "all"})
    ck("已经批过的单 → 按状态回话（不让销售干等）",
       replied[-1] == ac.TXT_REQ_APPROVED_SALES, replied[-1:])

    replied.clear()
    sent.clear()
    pushed.clear()
    af.approval_store = type("S", (), {
        "get_request": staticmethod(lambda rid: {"id": "R9", "operator_user_id": "s1",
                                                 "status": "pending", "customer_name": "คุณสมชาย",
                                                 "customer_id": "C1", "field_diffs": []}),
        "set_target": staticmethod(lambda rid, t: True),
    })
    af._target(binding, uid, "tok", {"req": "R9", "aid": "没这个管理员"})
    ck("选的人已停用/解绑 → 让销售重选（不静默失败）",
       replied[-1] == ac.TXT_NO_APPROVERS and not pushed, replied[-1:])

    replied.clear()
    sent.clear()
    pushed.clear()
    af._target(binding, uid, "tok", {"req": "R9", "aid": "a1"})
    ck("指定审批人：申请卡**推给那个管理员**，等待卡回给销售",
       len(sent) == 2 and sent[0][0] == "L1" and sent[0][1] == ""
       and sent[1][0] == uid and sent[1][1] == "tok"
       and sent[1][2]["contents"]["footer"]["contents"][0]["action"]["data"]
       == "action=approval_retarget&req=R9",
       [(x[0], x[1]) for x in sent])

    replied.clear()
    sent.clear()
    pushed.clear()
    af._target(binding, uid, "tok", {"req": "R9", "aid": "all"})
    ck("发给全部：推给所有能收卡的管理员，等待卡显示'发给全部'",
       [x[0] for x in sent if not x[1]] == ["L1"]
       and ac._t("BTN_ALL_APPROVERS") in str(sent[-1][2]),
       [(x[0], x[1]) for x in sent])

    sent.clear()
    af._retarget(binding, uid, "tok", {"req": "R9"})
    ck("改派：重新发一张选人卡", sent and "approval_target" in str(sent[-1][2]))

    # ── 6. 批 / 拒 ─────────────────────────────────────────────────────
    replied.clear()
    af.roster_store = type("R", (), {
        "get_profile": staticmethod(lambda u: {"dms_role": "sales", "status": "active"}),
        "list_profiles": staticmethod(lambda: profiles),
        "line_user_id_of": staticmethod(lambda u: "L9"),
        "ADMIN_ROLES": ("DMS Admin",)})
    af._decide(binding, uid, "tok", {"req": "R9"}, approve=True)
    ck("销售自己批不了 → 明确回'你没有审批权'",
       replied[-1] == ac.TXT_NOT_APPROVER, replied[-1:])

    replied.clear()
    holder = {"v": {"id": "R9", "operator_user_id": "s1", "status": "processing"}}
    af.roster_store = saved  # a1 = 管理员
    af.approval_store = type("S", (), {
        "claim": staticmethod(lambda rid, who: None),
        "get_request": staticmethod(lambda rid: holder["v"]),
        "finish": staticmethod(lambda rid, st: True),
    })
    af._decide({"tenant_id": "t", "user_id": "a1"}, uid, "tok", {"req": "R9"}, approve=True)
    ck("抢不到认领（别人在处理）→ 说'这单已经被处理了'",
       replied[-1] == ac.TXT_REQ_STALE, replied[-1:])

    replied.clear()
    holder["v"] = {"id": "R9", "operator_user_id": "s1", "status": "expired"}
    af._decide({"tenant_id": "t", "user_id": "a1"}, uid, "tok", {"req": "R9"}, approve=True)
    ck("抢不到且已过期 → 说'过期了请重来'", replied[-1] == ac.TXT_REQ_EXPIRED, replied[-1:])

    replied.clear()
    pushed.clear()
    finished = []
    af.approval_store = type("S", (), {
        "claim": staticmethod(lambda rid, who: {"id": "R9", "operator_user_id": "s1",
                                                "customer_name": "คุณสมชาย"}),
        "get_request": staticmethod(lambda rid: None),
        "finish": staticmethod(lambda rid, st: finished.append(st) or True),
    })
    af._decide({"tenant_id": "t", "user_id": "a1"}, uid, "tok", {"req": "R9"}, approve=False)
    ck("拒绝：落 rejected + 告诉管理员 + **通知销售**",
       finished == ["rejected"] and replied[-1] == ac.TXT_REQ_REJECTED_ADMIN
       # 申请人是 s1，按名单映射它的 LINE 是 L3（不是我先前以为的 L9）
       and pushed and pushed[-1] == ("L3", ac.TXT_REQ_REJECTED_SALES), (finished, pushed))

    queued.clear()
    replied.clear()
    af._decide({"tenant_id": "t", "user_id": "a1"}, uid, "tok", {"req": "R9"}, approve=True)
    ck("批准：先回'正在处理'，执行丢后台（要重新登录 DMS）",
       replied[-1] == ac.TXT_PROCESSING and queued and queued[-1][1] == "_execute_approved",
       (replied[-1:], queued and queued[-1][:2]))

    # ── 7. 执行：凭据、台账、成功/失败 ──────────────────────────────────
    pushed.clear()
    finished.clear()
    af.roster_store = type("R", (), {
        "get_profile": staticmethod(lambda u: {"dms_role": "sales", "status": "active"}),
        "list_profiles": staticmethod(lambda: profiles),
        "line_user_id_of": staticmethod(lambda u: "L9"),
        "ADMIN_ROLES": ("DMS Admin",)})
    af._execute_approved({"tenant_id": "t", "user_id": "a1"}, uid,
                         {"id": "R9", "operator_user_id": "s1"})
    ck("认领之后权限被收回 → 退回待批（不让单卡在'执行中'）",
       finished == ["pending"] and not pushed, finished)

    pushed.clear()
    finished.clear()
    af.roster_store = saved
    af.resolve_dms_endpoint = lambda user, eid: None
    af._execute_approved({"tenant_id": "t", "user_id": "a1"}, uid,
                         {"id": "R9", "operator_user_id": "s1"})
    ck("批准人没配 DMS 连接 → 退回待批 + 明说（不吞掉）",
       finished == ["pending"] and pushed and pushed[-1][1] == ac.TXT_APPROVER_NO_ENDPOINT,
       (finished, pushed[-1:]))

    pushed.clear()
    finished.clear()
    logs.clear()
    seen = {}
    af.resolve_dms_endpoint = lambda user, eid: {"id": "EP1"}

    def fake_write(endpoint, *, fields, mode, customer_id, addresses=None):
        seen.update({"endpoint": endpoint, "fields": fields, "mode": mode,
                     "customer_id": customer_id, "addresses": addresses})
        return {"success": True, "customer_id": "C1", "elapsed_ms": 12}

    af._dms_intake = type("I", (), {"push_idcard_fields": staticmethod(fake_write)})
    af._execute_approved({"tenant_id": "t", "user_id": "a1"}, uid, {
        "id": "R9", "operator_user_id": "s1", "customer_id": "C1", "customer_name": "คุณสมชาย",
        "draft": {"fields": {"people_id": "1100", "name": "คุณสมชาย"}},
        "field_diffs": [{"field": "phone", "new": "0899999999"}],
    })
    ck("执行用的是**批准人**解析出来的连接（审计才显示真实批准人）",
       seen.get("endpoint") == {"id": "EP1"}, seen.get("endpoint"))
    ck("写入字段 = 必填两项 + 逐条新值，模式是覆盖写",
       seen["fields"] == {"people_id": "1100", "name": "คุณสมชาย", "phone": "0899999999"}
       and seen["mode"] == "overwrite" and seen["customer_id"] == "C1", seen["fields"])
    ck("成功 → 落台账 + 状态已批 + 通知批准人与销售",
       logs and logs[-1]["success"] is True and finished == ["approved"]
       and pushed and pushed[0] == (uid, ac.TXT_REQ_APPROVED_ADMIN)
       and pushed[-1] == ("L3", ac.TXT_REQ_APPROVED_SALES), (finished, pushed))

    pushed.clear()
    finished.clear()
    logs.clear()
    af._dms_intake = type("I", (), {"push_idcard_fields": staticmethod(
        lambda *a, **k: {"success": False, "error_code": "ERR_DMS_TECHNICAL",
                         "error_friendly": {"th": "DMS ไม่รับ"}})})
    af._execute_approved({"tenant_id": "t", "user_id": "a1"}, uid, {
        "id": "R9", "operator_user_id": "s1", "customer_id": "C1",
        "draft": {"fields": {}}, "field_diffs": [],
    })
    ck("写失败 → **回炉待批**（可再批）+ 如实告诉管理员（带 DMS 的原因）",
       finished == ["pending"] and pushed and "DMS ไม่รับ" in pushed[-1][1]
       and ac.TXT_EXEC_FAIL_ADMIN in pushed[-1][1], (finished, pushed[-1:]))
    ck("失败的也落了台账（failed）", logs and logs[-1]["success"] is False)

    # ── 8. 小工具 ──────────────────────────────────────────────────────
    ck("展示差异优先用当时存的展示层，缺了用原始差异 + 字段标签兜底",
       af._display_diffs({"draft": {"display_diffs": [{"label": "X"}]}}) == [{"label": "X"}]
       and af._display_diffs({"field_diffs": [{"field": "name", "old": "a", "new": "b"}]})[0]["label"]
       == cards.FIELD_LABELS_TH.get("name"), af._display_diffs({"field_diffs": []}))
    af.roster_store = type("R", (), {
        "get_profile": staticmethod(lambda u: None), "list_profiles": staticmethod(lambda: []),
        "line_user_id_of": staticmethod(lambda u: ""), "ADMIN_ROLES": ()})
    ck("没有档案时申请人显示成'销售'兜底（不是空白）",
       af._operator_display_name("s9") == ac.TXT_SALES_FALLBACK, af._operator_display_name("s9"))
    pushed.clear()
    af._notify_operator({"operator_user_id": "s9"}, "结果")
    ck("申请人没绑 LINE → 不推送（不报错）", not pushed)

    store.clear_session(uid)

    # ── 9. 接线：差异卡按策略解主按钮 + postback 派发 ──────────────────
    from pearnly_dms import dms_flow

    hits = []
    real_pb = af.handle_postback
    af.handle_postback = lambda binding, luid, token, action, pb, sess: hits.append(action)
    try:
        dms_flow.handle_postback(binding, uid, "tok",
                                 {"data": "action=%s&nonce=n1" % cards.ACT_SUBMIT_APPROVAL})
        ck("postback：提审动作归审批层（不消费 nonce，一次性靠存储认领）",
           hits == [cards.ACT_SUBMIT_APPROVAL], hits)
        hits.clear()
        dms_flow.handle_postback(binding, uid, "tok",
                                 {"data": "action=%s&req=R1" % cards.ACT_APPROVAL_APPROVE})
        ck("postback：批准/拒绝也归审批层", hits == [cards.ACT_APPROVAL_APPROVE], hits)
        hits.clear()
        store.set_session(uid, "reviewing", {"nonce": "n9"})
        dms_flow.handle_postback(binding, uid, "tok", {"data": "action=update&nonce=n9"})
        ck("postback：写档动作仍归本文件（审批层不抢）", not hits, hits)
    finally:
        af.handle_postback = real_pb
        store.clear_session(uid)

    failed = [x for x in ok if not x]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(ok) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
