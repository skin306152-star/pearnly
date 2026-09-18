# -*- coding: utf-8 -*-
"""订车逐问**状态机**验证（不连 DMS、不写库）。

做法：只把"读会话 / 存会话 / 发卡 / 收尾"四处换成假的，**主档读取用真的**
（`dms_booking_qa_sync.masters` 走会话里那份快照），所以跑的是
状态机 → 步进 → 主档 的真实调用链。

钉住四件事：
1. **不抢上层**：全局命令（เริ่มใหม่ / เมนู）与"当前不是 booking_qa 态"一律返 False。
2. **串步按钮不跳步**：旧卡残留 / 历史卡的按钮，动作对不上当前步就**原步重问**，
   状态一个字段都不推进。
3. **翻页令牌永不写进 answers**：它只改"在看第几页"。
4. **采集链路**：取车点/车型/颜色/交车日/注册名义/凭证图 各自把答案落到 `answers`
   （或 `files`）并推进到下一步。

用法: scripts/sample-verify-dms-booking-qa-machine.sh <组> <站点>
"""


def main():
    from pearnly_dms import dms_booking_qa as machine
    from pearnly_dms import dms_qa_cards as qa_cards

    ok = []

    def ck(label, cond, detail=""):
        ok.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    # ── 把这四个出口换成假的；主档读取（sync）保持真的 ──────────────────
    sent = []
    saved = []
    previews = []
    real_to_preview = machine._to_preview  # 先留真身，下面会被换成假的
    real_qa = machine._qa                  # 同上（真身里有"老步骤归一"的逻辑）
    session = {"state": "booking_qa", "payload": {"qa": {}}}

    machine._send = lambda line_user_id, msg, reply_token="": (
        sent.append(msg) or True)
    machine._persist = lambda tenant_id, line_user_id, qa: saved.append(dict(qa))
    machine._to_preview = lambda tenant_id, line_user_id, qa, reply_token: previews.append(qa)

    def set_qa(qa):
        session["payload"]["qa"] = qa
        machine._qa = lambda tenant_id, line_user_id, sess=None: _current(sess)
        return qa

    def _current(sess):
        s = session if sess is None else sess
        if not s or s.get("state") != "booking_qa":
            return None
        qa = (s.get("payload") or {}).get("qa") or {}
        return qa if qa.get("step") else None

    machine._qa = lambda tenant_id, line_user_id, sess=None: _current(sess)

    def snapshot(rows):
        """一份只要够 `masters()` 取数的会话快照（快照本身在 sync 那层测过）。"""
        return {"endpoint_id": "EP1", "master_snapshot": {"rows": rows}}

    # 🚨 护栏：这个脚本**不许碰真实 DMS**。会话里该有的快照都齐，所以下面这两个
    # 读取器根本不该被调到；真被调到说明用例写漏了快照 —— 直接炸掉比悄悄联网好。
    from pearnly_dms.adapter import master_shared as _ms

    def _boom(*a, **k):
        raise AssertionError("用例缺快照，差点去读真实 DMS")

    _ms.get_session_masters = _boom
    _ms.get_session_paints = _boom

    def base(step, rows=None, **extra):
        qa = snapshot(rows or {})
        qa.update({"step": step, "customer": {"id": "1", "name": "คุณสมชาย"},
                   "answers": {}, "files": {"slip_mid": None}, "payments": [],
                   "pending_channel": {}, "audit": []})
        qa.update(extra)
        return set_qa(qa)

    # ── 1. 不抢上层 ────────────────────────────────────────────────────
    qa = base("place")
    ck("全局命令（เริ่มใหม่）返 False，交上层路由",
       machine.handle_text("t", "u", "เริ่มใหม่", "tok") is False)
    session["state"] = "reviewing"
    ck("当前不是 booking_qa 态 → 返 False（不越权）",
       machine.handle_text("t", "u", "อะไรก็ได้", "tok") is False)
    session["state"] = "booking_qa"

    # ── 2. 串步按钮不跳步 ──────────────────────────────────────────────
    sent.clear()
    qa = base("place", {"place_books": [["1", "A", "สาขา A"]]})
    before = dict(qa)
    got = machine.handle_postback("t", "u", "qa:car:C001", {}, "tok")
    ck("历史卡按钮（车型）在取车点步 → 吃下但不推进",
       got is True and qa["step"] == "place" and qa["answers"] == {}
       and "car" not in qa["answers"], (qa["step"], qa["answers"]))
    ck("原步重问（重发了取车点那张卡）", len(sent) == 1 and sent[0] is not None, len(sent))

    sent.clear()
    qa = base("place", {"place_books": [["1", "A", "สาขา A"]]})
    ck("不带 qa: 前缀的 postback 直接返 False",
       machine.handle_postback("t", "u", "booking:confirm", {}, "tok") is False)

    # ── 3. 翻页令牌不写进 answers ──────────────────────────────────────
    sent.clear()
    qa = base("place", {"place_books": [["1", "A", "สาขา A"]]})
    machine.handle_postback("t", "u", "qa:place:%s2" % qa_cards.PAGE_TOKEN, {}, "tok")
    ck("翻页令牌只改页码，绝不写进 answers",
       qa["answers"] == {} and qa["pages"]["place_books"] == 2, (qa["answers"], qa.get("pages")))

    # ── 4. 采集链路 ────────────────────────────────────────────────────
    sent.clear()
    qa = base("place", {"place_books": [["1", "A", "สาขา A"]]})
    machine.handle_postback("t", "u", "qa:place:1", {}, "tok")
    ck("选取车点 → 存 answers.place 并进车型搜索",
       qa["answers"]["place"] == {"id": "1", "name": "สาขา A"} and qa["step"] == "car_search",
       qa["answers"])
    ck("选完把该主档页码清掉", "place_books" not in (qa.get("pages") or {}), qa.get("pages"))

    sent.clear()
    qa = base("car_search", {"cars": [["C1", "DMAX", "2.5 VGS", "ขาว"]]})
    machine.handle_text("t", "u", "dmax", "tok")
    ck("车型搜索命中 → 命中集落会话并出结果卡（不推进状态）",
       qa["step"] == "car_search" and len(qa["car_search"]["hits"]) == 1
       and len(sent) == 1, (qa["step"], qa.get("car_search")))

    sent.clear()
    qa = base("car_search", {"cars": [["C1", "DMAX", "2.5 VGS", "ขาว"]]})
    machine.handle_text("t", "u", "zzz", "tok")
    ck("车型搜索无命中 → 出'没找到'卡且不推进",
       qa["step"] == "car_search" and "car_search" not in qa and len(sent) == 1, qa["step"])

    sent.clear()
    qa = base("car_search", {"cars": [["C1", "DMAX", "2.5 VGS", "ขาว"]]},
              car_search={"hits": [["C1", "DMAX", "2.5 VGS", "ขาว"]], "page": 0})
    # 选完车型会立刻发颜色那一步 → 颜色快照也得在会话里（护栏会拦下漏配）
    qa["paint_snapshots"] = {"C1": {"rows": [["P1", "ขาวมุก", "White Pearl"]]}}
    machine.handle_postback("t", "u", "qa:car:C1", {}, "tok")
    ck("选中车型 → answers.car 带 label、清命中集、进颜色",
       qa["answers"]["car"] == {"id": "C1", "label": "DMAX 2.5 VGS"}
       and qa["step"] == "paint" and "car_search" not in qa, qa["answers"])

    sent.clear()
    qa = base("paint", answers={"car": {"id": "C1", "label": "DMAX 2.5 VGS"}})
    # 主档行是 `[id, code, name, ...]`：显示名取第 3 列（见 `dms_qa_util.row_name`）
    qa["paint_snapshots"] = {"C1": {"rows": [["P1", "WHITE", "ขาวมุก"]]}}
    machine.handle_postback("t", "u", "qa:paint:P1", {}, "tok")
    ck("选颜色 → answers.paint 并进交车日",
       qa["answers"]["paint"] == {"id": "P1", "name": "ขาวมุก"} and qa["step"] == "date",
       qa["answers"])

    sent.clear()
    qa = base("date")
    machine.handle_postback("t", "u", "qa:date", {"date": "2026-09-20"}, "tok")
    ck("交车日 → 存佛历 dd/mm/yyyy 并进销售条件",
       qa["answers"]["delivery_date_be"] == "20/09/2569" and qa["step"] == "term",
       qa["answers"])

    sent.clear()
    qa = base("date")
    machine.handle_postback("t", "u", "qa:date", {"date": "ไม่ใช่วันที่"}, "tok")
    ck("日期解析不出 → 原步重问（不写坏答案）",
       "delivery_date_be" not in qa["answers"] and qa["step"] == "date", qa["answers"])

    sent.clear()
    qa = base("term", {"term_sales": [["1", "สด", "เงินสด"]]})
    machine.handle_postback("t", "u", "qa:term:1", {}, "tok")
    ck("选销售条件 → 进注册名义",
       qa["answers"]["term"] == {"id": "1", "name": "เงินสด"} and qa["step"] == "regis",
       qa["answers"])

    sent.clear()
    qa = base("regis", {"regis_behalfs": [["1", "ตัวเอง", "ตัวเอง"]]})
    machine.handle_postback("t", "u", "qa:regis:1", {}, "tok")
    ck("选注册名义 → 进'用客户名'那一步",
       qa["answers"]["regis"] == {"id": "1", "name": "ตัวเอง"} and qa["step"] == "regis_name",
       qa["answers"])

    sent.clear()
    qa = base("regis_name")
    machine.handle_postback("t", "u", "qa:regisname", {}, "tok")
    ck("用客户名 → 直接进付款渠道",
       qa["answers"]["regis_name"] == "คุณสมชาย" and qa["step"] == "pay_channel", qa["answers"])

    sent.clear()
    qa = base("regis_name")
    machine.handle_text("t", "u", "คุณสมหญิง", "tok")
    ck("自己打注册名 → 存下来并进付款渠道",
       qa["answers"]["regis_name"] == "คุณสมหญิง" and qa["step"] == "pay_channel", qa["answers"])

    sent.clear()
    qa = base("regis_name")
    machine.handle_text("t", "u", "ก" * 200, "tok")
    ck("注册名超长 → 原步重问，不写进去",
       "regis_name" not in qa["answers"] and qa["step"] == "regis_name", qa["step"])

    # ── 5. 凭证图 ──────────────────────────────────────────────────────
    sent.clear()
    qa = base("place", {"place_books": [["1", "A", "สาขา A"]]})
    ck("别的步收到图片 → 返 False（图片不是它要的输入）",
       machine.handle_image("t", "u", "MID1", "tok") is False)

    sent.clear()
    qa = base("slip")
    machine.handle_image("t", "u", "MID1", "tok")
    ck("开局那步（slip）收到凭证图 → 记 slip_mid 并进取车点",
       qa["files"]["slip_mid"] == "MID1" and qa["step"] == "place" and len(sent) == 1,
       (qa["files"], qa["step"]))

    sent.clear()
    previews.clear()
    qa = base("slip_after", after_slip="preview", payments=[{"channel": "transfer"}])
    machine.handle_image("t", "u", "MID2", "tok")
    ck("收尾那步（after_slip=preview）收到凭证图 → 记 slip_mid 并直接进预览",
       qa["files"]["slip_mid"] == "MID2" and qa["step"] == "pay_more"
       and len(previews) == 1 and "after_slip" not in qa, (qa["files"], qa["step"]))

    sent.clear()
    previews.clear()
    qa = base("slip_after", after_slip="pay_more")
    machine.handle_image("t", "u", "MID3", "tok")
    ck("拿完凭证回'还要加渠道吗'（按 after_slip 分流）",
       qa["step"] == "pay_more" and len(sent) == 1 and not previews,
       (qa["step"], len(sent), len(previews)))

    sent.clear()
    qa = base("slip")
    machine.handle_text("t", "u", "เงินสด", "tok")
    ck("现金声明可跳过凭证（开局那步）",
       qa["step"] == "place" and qa["files"]["slip_mid"] is None, (qa["step"], qa["files"]))

    sent.clear()
    qa = base("slip_after")
    machine.handle_text("t", "u", "เงินสด", "tok")
    ck("收尾那步打字 เงินสด **不放行**（必须先给凭证图）",
       qa["step"] == "slip_after" and len(sent) == 1, qa["step"])

    # ── 6. 按钮步收到打字 → 原步重问 ───────────────────────────────────
    sent.clear()
    qa = base("pay_channel")
    machine.handle_text("t", "u", "โอน", "tok")
    ck("按钮步收到打字 → 原步重问（不猜用户想干什么）",
       qa["step"] == "pay_channel" and len(sent) == 1, qa["step"])

    # ── 7. 旧会话兼容 + 腐败会话 ───────────────────────────────────────
    # 这一节测的是**真的 `_qa`**（归一老步骤的逻辑在里面），所以把会话存储换成假的、
    # 把 `_qa` 换回真身，别再走上面那个假 `_qa`。
    machine._qa = real_qa
    from pearnly_core import line_session as real_store

    real_get = real_store.get_session
    real_store.get_session = lambda line_user_id: session
    machine._persist = lambda tenant_id, line_user_id, qa: saved.append(dict(qa))
    session["state"] = "booking_qa"
    session["payload"]["qa"] = {"step": "pay_src", "answers": {}}
    got = real_qa("t", "u")
    ck("旧会话的 pay_src* 步骤归一到 pay_dst（老会话能继续跑）",
       got is not None and got["step"] == "pay_dst", got and got.get("step"))

    session["payload"]["qa"] = {"answers": {}}
    ck("会话里没有 step（损坏）→ 返 None 交上层", real_qa("t", "u") is None)

    session["state"] = "reviewing"
    session["payload"]["qa"] = {"step": "place"}
    ck("别的态 → 返 None（不抢）", real_qa("t", "u") is None)
    real_store.get_session = real_get

    # ── 8. 收尾：轮换 nonce + 置 booking_review ────────────────────────
    written = {}
    machine.store = type("S", (), {
        "get_session": staticmethod(lambda line_user_id: {"state": "booking_qa",
                                                          "payload": {"mode": "booking"}}),
        "set_session": staticmethod(lambda line_user_id, state, payload=None, ttl_minutes=None:
                                    written.update({"state": state, "payload": payload})),
    })
    sent.clear()
    qa = {"step": "pay_more", "answers": {}, "payments": []}
    real_to_preview("t", "u", qa, "tok")  # 走真身
    ck("收尾置 booking_review 且带上完整 qa",
       written.get("state") == "booking_review" and written["payload"]["qa"] is qa,
       written.get("state"))
    ck("收尾轮换 nonce（旧按钮的 postback 从此作废）",
       len(written["payload"].get("nonce") or "") == 16, written["payload"].get("nonce"))
    ck("收尾保留了上层原有 payload 字段（mode 不丢）",
       written["payload"].get("mode") == "booking", written["payload"].get("mode"))
    ck("收尾发出预览卡", len(sent) == 1 and sent[0] is not None)

    failed = [x for x in ok if not x]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(ok) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
