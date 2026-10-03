# -*- coding: utf-8 -*-
"""LINE 状态机端到端验证：**用合成事件驱动整条建档流**。

做法：把 LINE 传输层拦掉（reply/push 记下来、下载图片给真图），后台任务改成同步执行，
然后按真实事件顺序喂进去：

    follow → 绑定 → 发身份证照片 → 输手机号 → 点[บันทึกลูกค้าใหม่] → 回执

这条链上**真的会 OCR（真 Vertex）、真的查重、真的写 DMS**（测试账号）。
验的是状态机本身：状态怎么流转、nonce 有没有防住双击、卡片的按钮对不对。

真机（真手机 + 真 LINE 通道）是另一层验收，这里不冒充。
"""


def main():
    import json
    import os
    from urllib.parse import parse_qs

    import frappe

    from pearnly_core import line, line_binding
    from pearnly_dms.intake import flow as dms_flow
    from pearnly_dms.line import handlers as line_handlers

    IMG = "/tmp/dmscheck/idcard.jpg"
    LINE_UID = "U_verify_flow_0001"
    # 每轮换一个手机号：保证**一定出现差异**，这样在"客户已存在"的轮次里
    # 走的是"更新"分支而不是"资料一致（零写入）"，写路径每轮都被真验到。
    import time as _t

    phone = "08" + str(int(_t.time()) % 100000000).zfill(8)
    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def note(label, detail):
        print("  " + label + ": " + str(detail))

    if not os.path.exists(IMG):
        print("FAIL 缺样本图 %s" % IMG)
        print("RESULT: FAIL")
        return

    # ── 拦掉真实 LINE：记下发出去的东西，图片给真样本 ────────────────────
    sent = []
    line.reply = lambda token, msgs: (sent.append(("reply", msgs)), True)[1]
    line.push = lambda to, msgs: (sent.append(("push", msgs)), True)[1]
    line.get_profile = lambda uid: {"displayName": "verify"}
    with open(IMG, "rb") as fh:
        _img_bytes = fh.read()
    line.download_message_content = lambda mid, **_: _img_bytes
    # 后台任务同步执行：否则要等 worker，断言不了
    dms_flow._enqueue = lambda job, fn, *args: fn(*args)

    def last_text():
        for kind, msgs in reversed(sent):
            for m in msgs:
                if m.get("type") == "text":
                    return m.get("text", "")
        return ""

    def last_card():
        for kind, msgs in reversed(sent):
            for m in msgs:
                if m.get("type") == "flex":
                    return m
        return None

    def event(etype, **kw):
        ev = {"type": etype, "source": {"userId": LINE_UID}, "replyToken": "rtok"}
        ev.update(kw)
        return ev

    try:
        line_binding.unbind(LINE_UID)

        # ── 1. 绑定 ────────────────────────────────────────────────────
        bound_user = "Administrator"
        code = line_binding.generate_bind_code(bound_user)["code"]
        line_handlers.handle(event(
            "message", message={"type": "text", "text": code}))
        check("发绑定码 → 绑定成功",
              line_binding.user_of(LINE_UID) == bound_user,
              line_binding.user_of(LINE_UID))

        # ── 2. 未绑定发图会被提示（不静默丢） ───────────────────────────
        line_binding.unbind(LINE_UID)
        line_handlers.handle(event("message", message={"type": "image", "id": "mid-x"}))
        check("未绑定发图 → 明确提示要绑定", "绑定" in last_text(), last_text())
        line_binding.bind(LINE_UID, bound_user, "verify")

        # ── 3. 发身份证 → 真 OCR → 要手机号 ───────────────────────────
        sent.clear()
        line_handlers.handle(event("message", message={"type": "image", "id": "mid-1"}))
        check("发身份证 → 提示输手机号",
              "เบอร์โทร" in last_text(), last_text())
        sess = frappe.get_all("Pearnly LINE Session", filters={"line_user_id": LINE_UID},
                              fields=["state", "payload"])
        check("会话进 collecting 且存了身份证",
              bool(sess) and sess[0]["state"] == "collecting" and "id_card" in (sess[0]["payload"] or ""),
              sess[0]["state"] if sess else "(无会话)")

        # ── 4. 输手机号 → 真查重 → 出确认卡 ───────────────────────────
        sent.clear()
        line_handlers.handle(event(
            "message", message={"type": "text", "text": phone}))
        card = last_card()
        check("输手机号 → 出确认卡", card is not None, card and card.get("altText"))
        sess = frappe.get_all("Pearnly LINE Session", filters={"line_user_id": LINE_UID},
                              fields=["state", "payload"])
        check("会话进 reviewing", bool(sess) and sess[0]["state"] == "reviewing",
              sess[0]["state"] if sess else "(无会话)")
        payload = json.loads(sess[0]["payload"] or "{}") if sess else {}
        check("卡上复述的是**真 OCR 出来的**姓名",
              "สมชาย" in json.dumps(payload.get("summary") or {}, ensure_ascii=False),
              payload.get("summary", {}).get("name"))
        note("场景判定", payload.get("scenario"))
        note("手机号进了 draft", payload.get("draft", {}).get("phone"))
        check("手机号透传进 draft（不在我们这边改写格式）",
              payload.get("draft", {}).get("phone") == phone,
              payload.get("draft", {}).get("phone"))

        btns = card["contents"]["footer"]["contents"]
        datas = [parse_qs(b["action"]["data"]) for b in btns]
        nonce = datas[0].get("nonce", [""])[0]
        acts = [d.get("action", [""])[0] for d in datas]
        # 场景决定主按钮：新客 → 建档；老客有差异 → 更新；老客无差异 → 只有[用旧资料]
        primary = next((a for a in acts if a in ("create_new", "update")), None)
        check("确认卡主按钮与场景相符",
              (payload.get("scenario") == "none" and "create_new" in acts)
              or (payload.get("scenario") != "none" and primary in ("update", "create_new")),
              {payload.get("scenario"): acts})
        check("卡上带 nonce", bool(nonce), nonce)
        check("写路径本轮会被真验到（有主按钮）", primary is not None, acts)
        if primary is None:
            print("RESULT: FAIL（本轮没有可写的分支，测试无法验证写路径）")
            return
        confirm_data = next(b["action"]["data"] for b, d in zip(btns, datas)
                            if d.get("action", [""])[0] == primary)

        # ── 5. 点确认 → 真写 DMS ──────────────────────────────────────
        sent.clear()
        line_handlers.handle(event("postback", postback={"data": confirm_data}))
        # 注意：确认之后会推**两条**（回执 + 收尾提示），所以不能用 last_text 判回执
        receipt = ""
        for kind, msgs in sent:
            for m in msgs:
                if m.get("type") == "text" and "บันทึกสำเร็จ" in m.get("text", ""):
                    receipt = m["text"]
        check("点确认 → 回执说保存成功", bool(receipt), [t for _, ms in sent for t in
                                                   [m.get("text", "") for m in ms]][:2])
        check("回执带客户号", "รหัสลูกค้า" in receipt, receipt.replace("\n", " / "))
        note("回执", receipt.replace("\n", " / "))
        sess_after = frappe.get_all("Pearnly LINE Session",
                                    filters={"line_user_id": LINE_UID}, fields=["name"])
        check("写成功后会话被清（不会残留一张旧卡能重放）", not sess_after, sess_after)

        # ── 6. 重放同一个确认（防双击双写） ────────────────────────────
        sent.clear()
        line_handlers.handle(event("postback", postback={"data": confirm_data}))
        check("重放同一个确认 → 说过期，**不重复写**",
              last_text() == "รายการหมดอายุ เริ่มใหม่อีกครั้ง", last_text())

        # ── 7. 乱点一个不存在的 nonce ──────────────────────────────────
        sent.clear()
        line_handlers.handle(event("postback", postback={
            "data": "action=create_new&nonce=deadbeef"}))
        check("乱点旧卡 → 说过期", last_text() == "รายการหมดอายุ เริ่มใหม่อีกครั้ง",
              last_text())

        # ── 8. 全局命令 ────────────────────────────────────────────────
        sent.clear()
        line_handlers.handle(event("message", message={"type": "text", "text": "เริ่มใหม่"}))
        check("「เริ่มใหม่」→ 清会话并提示", "เริ่มรายการใหม่" in last_text(), last_text())
    finally:
        line_binding.unbind(LINE_UID)
        from pearnly_core import line_session as ls
        ls.clear_session(LINE_UID)

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
