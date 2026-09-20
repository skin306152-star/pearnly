# -*- coding: utf-8 -*-
"""订车/客户档**浏览器编辑器**（LINE 内那一页）的验证：后端逻辑 + 票据 + 接口面。

照搬对象是老产品 `services/line_dms/booking_edit.py` / `customer_edit.py` /
`services/erp/dms_edit_snapshot.py` / `routes/line_dms_booking_edit_routes.py`
（线上基准 `501589f0`）。这里钉住的是**行为契约**：

  · 一次登录取齐（load 只登一次，paints/geo 才另登）；
  · 拿不到权威快照 → fail closed，不拿旧缓存/裁剪视图顶上；
  · save：校验全部在写之前做完 → 换新 nonce → **卡片送达才算成功** → 失败回滚；
  · 客户档 save **不写 DMS**（只查差异、发卡）；
  · 票据不是登录态：只绑 LINE 用户 + 模式，且每个请求重新判绑定与会话。

DMS 侧全部打桩（`read_edit_snapshot` / `resolve_dms_endpoint` / `geo_options` /
`recognize_lookup`）—— 这一层验的是"我们怎么用它"，不是 DMS 自身。
LINE 出口**不打桩**：本机样例站点开着本地镜像（`pearnly_line_mirror`），
消息原文落进推送日志，所以"卡片到底发了什么"是能看到、能逐字核对的。

用法: scripts/sample-verify-dms-edit-browser.sh dms <站点>
"""


def main():
    import json
    import secrets

    import frappe

    from pearnly_core import line, line_binding
    from pearnly_core import line_session as store
    from pearnly_dms.editor import booking_edit as be
    from pearnly_dms.editor import browser_api as api
    from pearnly_dms.editor import browser_ticket as tickets
    from pearnly_dms.editor import customer_edit as ce
    from pearnly_dms.editor import edit_snapshot as snap
    from pearnly_dms.line import out as dms_out

    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def code_of(fn, *args, **kwargs):
        """跑一个动作，返回它的错误码（正常跑完回 ""）。"""
        try:
            fn(*args, **kwargs)
            return ""
        except be.DmsBookingEditError as exc:
            return exc.code

    def message_of(fn, *args, **kwargs):
        """跑一个 whitelist 接口，返回（错误码, HTTP 状态）。"""
        try:
            fn(*args, **kwargs)
            return "", 200
        except Exception as exc:  # noqa: BLE001
            return str(exc), getattr(exc, "http_status_code", None)

    # ── 打桩：DMS 侧 ───────────────────────────────────────────────────
    MASTERS = {
        "place_books": [["PL1", "PB1", "สาขาหลัก"]],
        "cars": [["C1", "DMAX", "2.5"], ["C2", "MUX", "1.9"], ["C3", "CITY", "1.5"]],
        "term_sales": [["T1", "TS1", "เงินสด"]],
        "regis_behalfs": [["R1", "RB1", "ตนเอง"]],
        "prefixes": [["17", "P17", "นาย"], ["18", "P18", "น.ส."]],
        # 五类银行目录：公司收款账户有账号/支行；四类付款银行里 source_banks 是**权威空目录**
        # 公司收款账户列的含义：[id, 代码, 名称, 支行, 账号]（列序照线上 company_bank_label）
        "company_banks": [["B1", "KB", "KBank", "สาขาสีลม", "123456"]],
        "source_banks": [],
        "cheque_banks": [["B2", "KB", "KBank"]],
        "cashier_banks": [["B3", "SCB", "SCB"]],
        "card_banks": [["B4", "VISA", "VISA"]],
        "advisors": [],
        "employees": [],
    }
    PAINTS = {"C1": [["PT1", "P1", "แดง"]], "C2": [["PT2", "P2", "น้ำเงิน"]]}
    GEO = {
        "provinces": [["10", "P10", "กรุงเทพมหานคร"]],
        "districts": [["1001", "D1001", "บางรัก"]],
        "subdistricts": [["100101", "S100101", "สีลม"]],
        "zipcodes": [["10500", "Z10500", "10500"]],
    }

    calls = {"snapshot": []}

    def fake_snapshot(endpoint, *, car_ids=(), customer=None, customer_id=""):
        calls["snapshot"].append(
            {"endpoint": endpoint, "car_ids": tuple(car_ids), "customer_id": customer_id}
        )
        return {
            "masters": dict(MASTERS),
            # `C3` 专门用来模拟"这一车型的颜色**这次没读到**"（None ≠ 空表）。
            "paints": {
                str(cid): (None if str(cid) == "C3" else list(PAINTS.get(str(cid), [])))
                for cid in car_ids
            },
            "prefixes": [list(r) for r in MASTERS["prefixes"]],
            "geo": {k: [list(r) for r in v] for k, v in GEO.items()},
            "customer": {},
            "resolved_customer": {"zipcode": "10500", "province_name": "กรุงเทพมหานคร"},
        }

    be.read_edit_snapshot = fake_snapshot
    be.resolve_dms_endpoint = lambda user, eid: {"id": "EP1", "name": "EP1"}
    ce.resolve_dms_endpoint = lambda user, eid: {"id": "EP1", "name": "EP1"}
    be._geo_options = lambda endpoint, *, level, parent_id="": {
        "ok": True,
        "options": [list(r) for r in GEO.get(level, [])],
    }
    ce._geo_options = be._geo_options

    # ── 站点必须开着本地镜像（不然 save 那条链在本机永远失败） ─────────
    if not line.mirroring():
        print("FAIL 本站点没开本地镜像（pearnly_line_mirror=1）—— save 那条链在本机必失败")
        print("RESULT: FAIL")
        return
    check("本机样例站点开着本地镜像（LINE 消息落推送日志）", line.mirroring())

    def mirror_count():
        return frappe.db.count("Pearnly Push Log", {"trigger": "line_mirror"})

    def last_mirror():
        name = frappe.db.get_value(
            "Pearnly Push Log", {"trigger": "line_mirror"}, "name", order_by="creation desc"
        )
        if not name:
            return {}
        raw = frappe.db.get_value("Pearnly Push Log", name, "response_body") or "{}"
        try:
            return json.loads(raw)
        except ValueError:
            return {}

    # ── 夹具：一个绑定 + 一份订车草稿 ─────────────────────────────────
    LINE_UID = "verify-edit-" + secrets.token_hex(4)
    USER = "onestop-761900@sample.local"
    NONCE = secrets.token_hex(8)
    QA = {
        "endpoint_id": "EP1",
        "step": "pay_more",
        "customer": {"id": "CUST1", "name": "คุณสมชาย"},
        "advisor": {"id": "A1", "name": "dmstest"},
        "draft": {
            "people_id": "1101700998118",
            "prefix_id": "17",
            "prefix_name": "นาย",
            "name": "คุณสมชาย",
            "birthday_be": "15/05/2530",
            "phone": "0811111111",
            "house_no": "99/1",
            "province_id": "10",
            "district_id": "1001",
            "subdistrict_id": "100101",
            "zipcode_id": "10500",
        },
        "answers": {
            "place": {"id": "PL1", "name": "สาขาหลัก"},
            "car": {"id": "C1", "label": "DMAX 2.5"},
            "paint": {"id": "PT1", "name": "แดง"},
            "delivery_date_be": "20/09/2569",
            "term": {"id": "T1", "name": "เงินสด"},
            "regis": {"id": "R1", "name": "ตนเอง"},
            "regis_name": "คุณสมชาย",
        },
        "files": {"id_card_mid": "MID-ID", "slip_mid": None},
        "payments": [{"channel": "cash", "amount": "5000.00", "extra": {}}],
    }

    def submitted(**over):
        """编辑器 POST 上来的形状（默认就是原样不动）。"""
        customer = {k: str(QA["draft"].get(k) or "") for k in be.CUSTOMER_FIELDS}
        customer["prefix_id"] = "17"
        customer.update(
            province_name="กรุงเทพมหานคร",
            district_name="บางรัก",
            subdistrict_name="สีลม",
            zipcode="10500",
        )
        out = {
            "customer": customer,
            "answers": {
                "place_id": "PL1",
                "car_id": "C1",
                "paint_id": "PT1",
                "delivery_date_be": "20/09/2569",
                "term_id": "T1",
                "regis_id": "R1",
                "regis_name": "คุณสมชาย",
            },
            "payments": [{"channel": "cash", "amount": "5000.00", "extra": {}}],
            "keep_files": {"id_card": True, "slip": True},
        }
        out.update(over)
        return out

    original_user = frappe.session.user
    # 记账只清"本轮自己产生"的那些：先记下开工时已有的名字，收工时只删新出现的。
    mirrored_before = {
        row[0] for row in frappe.db.sql("select name from `tabPearnly Push Log` where `trigger`='line_mirror'")
    }
    try:
        line_binding.bind(LINE_UID, USER, "verify")
        store.clear_session(LINE_UID)
        store.set_session(LINE_UID, be.STATE, {"nonce": NONCE, "qa": QA, "endpoint_id": "EP1"})

        # ── 1. 快照层（纯逻辑） ────────────────────────────────────────
        check("没客户资料 → 一级地址都不读", snap.geo_levels(None) == ())
        check("只知府 → 读到府 + 县",
              snap.geo_levels({"province_id": "10"})
              == (("provinces", ""), ("districts", "10")))
        check("四级齐全 → 四级都读",
              snap.geo_levels(
                  {"province_id": "10", "district_id": "1001", "subdistrict_id": "100101"}
              )
              == (("provinces", ""), ("districts", "10"),
                  ("subdistricts", "1001"), ("zipcodes", "100101")))
        merged = snap._merge_customer_defaults(
            {"name": "草稿名"}, {"name": "DMS 名", "zipcode_name": "10500", "phone": "081"}
        )
        check("草稿里已有的值永远优先（不被 DMS 现值盖掉）", merged["name"] == "草稿名", merged)
        check("草稿缺的从 DMS 现值补，且 zipcode_name 落到 zipcode",
              merged["phone"] == "081" and merged["zipcode"] == "10500", merged)

        # ── 2. load ────────────────────────────────────────────────────
        calls["snapshot"].clear()
        model = be.load(LINE_UID, NONCE)
        call = calls["snapshot"][0] if calls["snapshot"] else {}
        check("load 只登录 DMS 一次（主档 + 选中车型颜色在同一份快照里）",
              len(calls["snapshot"]) == 1, len(calls["snapshot"]))
        check("快照请求带上了草稿里选中的车型", call.get("car_ids") == ("C1",), call)
        check("快照请求带上了当前客户号（要按身份复核）",
              call.get("customer_id") == "CUST1", call)
        masters = model["masters"]
        check("主档成形：place/car/paint/term/regis/prefix 都在",
              all(k in masters for k in ("places", "cars", "paints", "terms", "regis", "prefixes")),
              sorted(masters))
        check("选项是 {id,label} 形状（前端不解析二维数组）",
              all(set(o) >= {"id", "label"} for o in masters["cars"]), masters["cars"])
        check("车型标签用「代码 名称」", masters["cars"][0]["label"] == "DMAX 2.5", masters["cars"])
        bank = masters["company_banks"][0]
        check("公司收款账户带账号与支行（要显示给客户看）",
              bank.get("account_no") == "123456" and bank.get("branch_name") == "สาขาสีลม", bank)
        check("权威为空的那几类付款银行 → 进 manual_banks（前端改成手填银行名）",
              model["manual_banks"] == ["source_banks"], model["manual_banks"])
        check("company_banks 永不进 manual_banks", "company_banks" not in model["manual_banks"])
        check("首屏带回地址级联（不再为四级地址登四次）",
              set(model["geo"]) == {"provinces", "districts", "subdistricts", "zipcodes"},
              sorted(model["geo"]))
        check("身份证草稿里缺的值从 DMS 权威直读补齐",
              model["form"]["customer"]["zipcode"] == "10500",
              model["form"]["customer"].get("zipcode"))
        check("附件两行按草稿回显（身份证有、凭证没有）",
              model["form"]["files"] == {"id_card": True, "slip": False},
              model["form"]["files"])

        # ── 3. 守卫 ────────────────────────────────────────────────────
        check("nonce 对不上 → expired(409)",
              code_of(be.load, LINE_UID, "错的") == "dms_booking.expired")
        store.set_session(LINE_UID, "reviewing", {"nonce": NONCE, "qa": QA})
        check("会话态不对（订车编辑器读到客户档态）→ expired",
              code_of(be.load, LINE_UID, NONCE) == "dms_booking.expired")
        store.set_session(LINE_UID, be.STATE, {"nonce": NONCE, "qa": QA, "endpoint_id": "EP1"})
        check("没绑定的 LINE 用户 → not_bound(403)",
              code_of(be.load, "verify-edit-nobody", NONCE) == "dms_booking.not_bound")
        be.resolve_dms_endpoint = lambda user, eid: None
        check("端点被停用/不存在 → no_endpoint(409)",
              code_of(be.load, LINE_UID, NONCE) == "dms_booking.no_endpoint")
        be.resolve_dms_endpoint = lambda user, eid: {"id": "EP1", "name": "EP1"}
        be.read_edit_snapshot = lambda *a, **k: None
        check("权威快照读不到 → master_unavailable(503)（不拿旧缓存顶上）",
              code_of(be.load, LINE_UID, NONCE) == "dms_booking.master_unavailable")
        be.read_edit_snapshot = fake_snapshot
        no_prefix = dict(MASTERS, prefixes=[])
        be.read_edit_snapshot = lambda *a, **k: {
            **fake_snapshot(*a, **k), "masters": no_prefix, "prefixes": [],
        }
        check("主档缺称谓 → master_unavailable（半个目录判不出选项还在不在）",
              code_of(be.load, LINE_UID, NONCE) == "dms_booking.master_unavailable")
        be.read_edit_snapshot = fake_snapshot

        # ── 4. paints / geo ────────────────────────────────────────────
        rows = be.paints(LINE_UID, NONCE, "C2")
        check("换车型 → 只回该车型的颜色",
              [r["id"] for r in rows] == ["PT2"], rows)
        check("颜色取不到（这次没读到）→ master_unavailable，不当「这车没颜色」",
              code_of(lambda: be.paints(LINE_UID, NONCE, "C9"))
              == "dms_booking.invalid_master")
        check("颜色读失败（None）→ master_unavailable（读失败 ≠ 空表）",
              code_of(be.paints, LINE_UID, NONCE, "C3") == "dms_booking.master_unavailable",
              code_of(be.paints, LINE_UID, NONCE, "C3"))
        check("非法层级 → invalid_geo",
              code_of(be.geo, LINE_UID, NONCE, "cities") == "dms_booking.invalid_geo")
        be._geo_options = lambda endpoint, *, level, parent_id="": {"ok": False}
        check("级联读不到 → geo_unavailable(503)",
              code_of(be.geo, LINE_UID, NONCE, "districts", "10")
              == "dms_booking.geo_unavailable")
        be._geo_options = lambda endpoint, *, level, parent_id="": {
            "ok": True, "options": [list(r) for r in GEO.get(level, [])],
        }

        # ── 5. save：校验全部在写之前 ──────────────────────────────────
        def bad(field, value, code):
            payload = submitted()
            payload["customer"][field] = value
            got = code_of(be.save, LINE_UID, NONCE, payload)
            sess = ((store.get_session(LINE_UID) or {}).get("payload") or {}).get("nonce")
            check("客户字段 %s 非法 → %s，且**什么都没写**（nonce 还是旧的）"
                  % (field, code), got == code and sess == NONCE, got)

        bad("people_id", "123", "dms_booking.invalid_people_id")
        bad("name", "  ", "dms_booking.invalid_name")
        bad("birthday_be", "31/13/2530", "dms_booking.invalid_birthday")
        bad("house_no", "", "dms_booking.invalid_house_no")
        bad("zipcode_id", "", "dms_booking.invalid_zipcode_id")

        payload = submitted()
        payload["answers"]["delivery_date_be"] = "32/01/2569"
        got = code_of(be.save, LINE_UID, NONCE, payload)
        check("交车日非法 → invalid_delivery 且不写", got == "dms_booking.invalid_delivery", got)
        payload = submitted()
        payload["answers"]["regis_name"] = ""
        got = code_of(be.save, LINE_UID, NONCE, payload)
        check("登记人姓名空 → invalid_regis_name 且不写",
              got == "dms_booking.invalid_regis_name", got)
        payload = submitted()
        payload["answers"]["car_id"] = "C2"  # 车换了，颜色还是 C1 的红
        check("换车型后颜色必须属于新车型（拿老选项提交被挡）",
              code_of(be.save, LINE_UID, NONCE, payload) == "dms_booking.invalid_master")
        payload = submitted()
        payload["payments"] = [
            {"channel": "cash", "amount": "5000", "extra": {}},
            {"channel": "cash", "amount": "1", "extra": {}},
        ]
        got = code_of(be.save, LINE_UID, NONCE, payload)
        check("同一渠道加两次 → duplicate_payment（透传付款层错误码）",
              got == "dms_booking.duplicate_payment", got)
        payload = submitted()
        payload["payments"] = []
        got = code_of(be.save, LINE_UID, NONCE, payload)
        check("一笔付款都没有 → payment_required", got == "dms_booking.payment_required", got)
        payload = submitted()
        payload["payments"] = [{"channel": "transfer", "amount": "5000", "extra": {"dst_id": "B1"}}]
        got = code_of(be.save, LINE_UID, NONCE, payload)
        check("转账但没有凭证 → slip_required", got == "dms_booking.slip_required", got)
        with_slip = dict(QA, files={"id_card_mid": "MID-ID", "slip_mid": "MID-SLIP"})
        store.set_session(LINE_UID, be.STATE, {"nonce": NONCE, "qa": with_slip, "endpoint_id": "EP1"})
        payload = submitted()
        got = code_of(be.save, LINE_UID, NONCE, payload)
        check("有凭证却没有转账渠道 → slip_without_transfer",
              got == "dms_booking.slip_without_transfer", got)
        # 凭证可以被显式去掉（keep_files.slip=False）——去掉之后就不该再拦
        payload = submitted(payments=[{"channel": "cash", "amount": "5000", "extra": {}}],
                            keep_files={"id_card": True, "slip": False})
        got = code_of(be.save, LINE_UID, NONCE, payload)
        check("显式去掉凭证后，现金单能存下去（不是无条件拦）", got == "", got)
        store.set_session(LINE_UID, be.STATE, {"nonce": NONCE, "qa": QA, "endpoint_id": "EP1"})

        # ── 6. save：成功路径 ──────────────────────────────────────────
        before = mirror_count()
        payload = submitted()
        payload["customer"]["phone"] = "0899999999"  # 改一格 → customer_dirty
        new_nonce = be.save(LINE_UID, NONCE, payload)
        check("save 返回**轮换后的新 nonce**",
              bool(new_nonce) and new_nonce != NONCE, new_nonce)
        sess = store.get_session(LINE_UID) or {}
        stored = sess.get("payload") or {}
        check("会话里的 nonce 也换成了新的", (stored.get("nonce")) == new_nonce, stored.get("nonce"))
        check("会话态仍是 booking_review（旧卡作废、新卡生效）",
              sess.get("state") == "booking_review", sess.get("state"))
        check("新版预览卡**真的发出去了**（本机镜像计数 +1）",
              mirror_count() == before + 1, (before, mirror_count()))
        mirror = last_mirror()
        card = (mirror.get("messages") or [{}])[0]
        check("发出去的是 flex 卡，不是纯文本",
              card.get("type") == "flex", card.get("type"))
        datas = [
            b["action"].get("data", "")
            for b in card["contents"]["footer"]["contents"]
        ]
        check("卡上的确认按钮带**新** nonce（旧 nonce 再也确认不了）",
              any(new_nonce in d for d in datas) and not any(NONCE in d for d in datas), datas)
        check("卡片正文用的是预算好的 summary（不是空壳）",
              any("0899999999" in json.dumps(row, ensure_ascii=False)
                  for row in card["contents"]["body"]["contents"]),
              card["contents"]["body"]["contents"][:2])
        answers = (stored.get("qa") or {}).get("answers") or {}
        check("答案按权威主档重新映射（车/色/地点/条件/登记都带 label）",
              answers["car"] == {"id": "C1", "label": "DMAX 2.5"}
              and answers["paint"]["name"] == "แดง"
              and answers["place"]["name"] == "สาขาหลัก"
              and answers["term"]["name"] == "เงินสด"
              and answers["regis"]["name"] == "ตนเอง", answers)
        check("网页端改过客户资料 → 记上 customer_dirty",
              bool((stored.get("qa") or {}).get("customer_dirty")))
        check("主档与颜色快照都落进 qa（后面建单要按它复核）",
              bool((stored.get("qa") or {}).get("master_snapshot"))
              and "C1" in ((stored.get("qa") or {}).get("paint_snapshots") or {}),
              sorted(((stored.get("qa") or {}).get("paint_snapshots") or {}).keys()))
        check("审计留痕：记了一条 browser_edit",
              {"step": "browser_edit", "input": "saved"}
              in ((stored.get("qa") or {}).get("audit") or []))
        check("客户姓名/称谓从主档回填（province_name 等派生值写回草稿）",
              stored["qa"]["draft"]["zipcode"] == "10500"
              and stored["qa"]["draft"]["province_name"] == "กรุงเทพมหานคร",
              {k: stored["qa"]["draft"].get(k) for k in ("zipcode", "province_name")})

        # 接着用新 nonce 再来一次：第二次保存也必须能用（页面能连改两遍）
        again = be.save(LINE_UID, new_nonce, submitted())
        check("用新 nonce 能再存一次（用户来回改不会被锁死）",
              bool(again) and again != new_nonce, again)

        # ── 7. save：卡片发失败要回滚 ─────────────────────────────────
        payload = submitted()
        payload["customer"]["phone"] = "0877777777"
        store.set_session(LINE_UID, be.STATE, {"nonce": NONCE, "qa": QA, "endpoint_id": "EP1"})
        real_push = line.push
        line.push = lambda to, messages: False
        try:
            got = code_of(be.save, LINE_UID, NONCE, payload)
        finally:
            line.push = real_push
        restored = ((store.get_session(LINE_UID) or {}).get("payload") or {})
        check("卡片没发出去 → preview_send_failed(503)",
              got == "dms_booking.preview_send_failed", got)
        check("发失败要**回滚**：旧 nonce 恢复，用户还能重试同一份草稿",
              restored.get("nonce") == NONCE, restored.get("nonce"))
        check("回滚后草稿内容没被改坏（手机号还是旧的）",
              (restored.get("qa") or {}).get("draft", {}).get("phone") == "0811111111",
              (restored.get("qa") or {}).get("draft", {}).get("phone"))

        # ── 8. 客户档编辑器 ────────────────────────────────────────────
        CID_NONCE = secrets.token_hex(8)
        store.set_session(
            LINE_UID,
            ce.STATE,
            {
                "nonce": CID_NONCE,
                "endpoint_id": "EP1",
                "draft": dict(QA["draft"]),
                "id_card": {"prefix_name": "นาย"},
                "customer_id": "CUST1",
            },
        )
        cmodel = ce.load(LINE_UID, CID_NONCE)
        check("客户档 load 只回客户资料 + 称谓 + 地址（没有订车那些字段）",
              set(cmodel) == {"form", "masters", "geo"}
              and set(cmodel["masters"]) == {"prefixes"}, sorted(cmodel))
        check("客户档 load 把称谓从主档解出 id（OCR 的泰文 → 主档行）",
              cmodel["form"]["customer"]["prefix_id"] == "17",
              cmodel["form"]["customer"].get("prefix_id"))
        check("订车态的非ce 在客户档编辑器里不认（两个编辑器各管自己的态）",
              code_of(ce.load, LINE_UID, NONCE) == "dms_booking.expired")

        no_prefix_draft = {
            k: v for k, v in QA["draft"].items() if k not in ("prefix_id", "prefix_name")
        }
        store.set_session(
            LINE_UID,
            ce.STATE,
            {
                "nonce": CID_NONCE,
                "endpoint_id": "EP1",
                "draft": no_prefix_draft,
                "id_card": {"prefix_name": "ไม่ทราบ称谓"},
                "customer_id": "CUST1",
            },
        )
        unmapped = ce.load(LINE_UID, CID_NONCE)
        check("称谓在主档里找不到 → 留空让用户选，**不拿一个错 id 顶上**",
              unmapped["form"]["customer"]["prefix_id"] == "",
              unmapped["form"]["customer"].get("prefix_id"))

        lookups = []
        ce.recognize_lookup = lambda endpoint, **kw: (
            lookups.append(kw),
            {
                "ok": True,
                "scenario": "exact",
                "match": {
                    "found": True,
                    "customer_id": "CUST1",
                    "current_fields": {**QA["draft"], "phone": "0811111111"},
                },
                "field_diffs": [],
                "candidates": [],
                "geo": GEO,
                "prefixes": MASTERS["prefixes"],
            },
        )[1]
        store.set_session(
            LINE_UID,
            ce.STATE,
            {
                "nonce": CID_NONCE,
                "endpoint_id": "EP1",
                "draft": dict(QA["draft"]),
                "id_card": {"prefix_name": "นาย"},
                "customer_id": "CUST1",
                "mode": "customer",
            },
        )
        before = mirror_count()
        payload = submitted()
        payload["customer"]["phone"] = "0866666666"
        cnew = ce.save(LINE_UID, CID_NONCE, payload)
        check("客户档 save 也轮换 nonce", bool(cnew) and cnew != CID_NONCE, cnew)
        check("客户档 save **不写 DMS**：只调只读的查重接口",
              len(lookups) == 1 and "people_id" in lookups[0], lookups)
        check("客户档 save 同样以「卡片送达」为准（镜像 +1）",
              mirror_count() == before + 1, (before, mirror_count()))
        cstored = (store.get_session(LINE_UID) or {}).get("payload") or {}
        check("改过的电话进了草稿与 summary",
              cstored["draft"]["phone"] == "0866666666"
              and cstored["summary"]["phone"] == "0866666666",
              cstored.get("summary"))
        check("差异记进 field_diffs（差异卡要逐条显示旧→新）",
              any(d.get("field") == "phone" for d in (cstored.get("field_diffs") or [])),
              cstored.get("field_diffs"))
        check("客户档 save 后会话态还是 reviewing",
              (store.get_session(LINE_UID) or {}).get("state") == "reviewing")

        # 无差异 → 出「资料一致」卡而不是差异卡
        store.set_session(
            LINE_UID,
            ce.STATE,
            {
                "nonce": CID_NONCE,
                "endpoint_id": "EP1",
                "draft": dict(QA["draft"]),
                "id_card": {"prefix_name": "นาย"},
                "customer_id": "CUST1",
                "mode": "customer",
            },
        )
        payload = submitted()
        ce.save(LINE_UID, CID_NONCE, payload)
        same = (store.get_session(LINE_UID) or {}).get("payload") or {}
        check("资料一致 → 不发差异卡（field_diffs 空、无 display_diffs）",
              not same.get("field_diffs") and not same.get("display_diffs"),
              same.get("field_diffs"))

        # ── 9. 票据 ────────────────────────────────────────────────────
        ticket = tickets.issue(LINE_UID, "customer")
        resolved = tickets.resolve(ticket)
        check("票据往返：LINE 用户 + 模式都取回来",
              resolved == {"line_user_id": LINE_UID, "mode": "customer", "user": USER,
                           "binding_revision": str(line_binding.binding_of(LINE_UID).modified)},
              bool(resolved))
        check("乱编的票据 → None（fail closed）", tickets.resolve("nope") is None)
        check("空票据 → None", tickets.resolve("") is None)
        check("模式归一：只认 customer，其余当订车",
              (tickets.normalize_mode("customer"), tickets.normalize_mode("booking"),
               tickets.normalize_mode("乱写")) == ("customer", "booking", "booking"))
        tickets.revoke(ticket)
        check("作废后立刻取不到", tickets.resolve(ticket) is None)

        # ── 10. 接口面 ─────────────────────────────────────────────────
        try:
            config = api.browser_config()
        except AttributeError as exc:
            # `bench console` 里没有真实请求，`frappe.local.session_obj` 不存在 ——
            # csrf 只能由**真实请求**下发（这正是它该有的样子）。那一条放在 HTTP 层验。
            check("console 里取不到 csrf 是预期（它只在真实请求里有）",
                  "session_obj" in str(exc), exc)
            config = {"liff_id": line.liff_id(), "available": bool(line.liff_id())}
        check("browser_config：本机没配 LIFF → available=False（不是报错）",
              config["available"] is False and config["liff_id"] == "", config)

        frappe.set_user("Guest")
        msg, status = message_of(api.browser_auth)
        check("未验身份（游客且没 id_token）→ line_auth_required(401)",
              msg == "dms_booking.line_auth_required" and status == 401, (msg, status))
        frappe.set_user(original_user)

        msg, status = message_of(api.browser_auth)
        check("已登录站点但账号名下没有 LINE 绑定 → not_bound(403)",
              msg == "dms_booking.not_bound" and status == 403, (msg, status))

        frappe.set_user(USER)
        try:
            auth = api.browser_auth(draft="x", editor="booking")
            check("站点会话回落：拿到票据", bool(auth["ticket"]) and auth["mode"] == "booking", auth)
            store.set_session(
                LINE_UID, be.STATE, {"nonce": NONCE, "qa": QA, "endpoint_id": "EP1"}
            )
            draft = api.browser_draft(auth["ticket"], NONCE)
            check("browser_draft 走订车编辑器（返回 form/masters/geo）",
                  set(draft) >= {"form", "masters", "geo"}, sorted(draft))
            msg, status = message_of(api.browser_draft, auth["ticket"], "错的nonce")
            check("nonce 不对 → expired(409)", msg == "dms_booking.expired" and status == 409,
                  (msg, status))
            msg, status = message_of(api.browser_draft, "伪造票据", NONCE)
            check("伪造票据 → line_auth_required(401)（不泄漏票据是否存在）",
                  msg == "dms_booking.line_auth_required" and status == 401, (msg, status))

            customer_ticket = api.browser_auth(editor="customer")["ticket"]
            msg, status = message_of(api.browser_paints, customer_ticket, NONCE, "C2")
            check("客户档票据调「换车型颜色」→ expired(409)（模式守卫）",
                  msg == "dms_booking.expired" and status == 409, (msg, status))
            before = mirror_count()
            saved = api.browser_save(auth["ticket"], NONCE, submitted())
            check("browser_save 回新 nonce 且卡片已发",
                  saved["nonce"] != NONCE and mirror_count() == before + 1, saved)

            texts = api.browser_text(["dms.edit.save", "dms.edit.expired"])
            check("browser_text 一次给全四语",
                  set(texts["dms.edit.save"]) == {"th", "en", "zh", "ja"}, sorted(texts["dms.edit.save"]))
            check("泰文就是老产品那句（逐字搬的）",
                  texts["dms.edit.save"]["th"] == "บันทึกและส่งสรุปใหม่",
                  texts["dms.edit.save"]["th"])
            check("只服务 dms.* 命名空间（游客调不到别人的翻译）",
                  api.browser_text(["frappe.core.x", "dms.edit.save"]).keys()
                  == {"dms.edit.save"})
            check("页面要用的每条文案都四语齐全（缺译不许静默）",
                  all(len(v) == 4 for v in api.browser_text(
                      ["dms.edit.loading", "dms.edit.failed", "dms.edit.liffUnavailable",
                       "dms.card.TXT_EDIT_UNAVAILABLE"]).values()))
        finally:
            frappe.set_user(original_user)

        # ── 11. 本地镜像的两道锁 ──────────────────────────────────────
        check("本机没配 LINE 凭据 + 开了开关 → 镜像生效", line.mirroring())
        saved_conf = {k: frappe.conf.get(f"pearnly_line_{k}")
                      for k in ("channel_secret", "channel_access_token")}
        try:
            frappe.conf["pearnly_line_channel_secret"] = "x"
            frappe.conf["pearnly_line_channel_access_token"] = "y"
            check("**配了 LINE 凭据就绝不镜像**（误开也吞不掉真消息）",
                  line.mirroring() is False)
        finally:
            for key, value in saved_conf.items():
                if value is None:
                    frappe.conf.pop(f"pearnly_line_{key}", None)
                else:
                    frappe.conf[f"pearnly_line_{key}"] = value
        check("镜像记账用的是独立 trigger（本地日志一眼可分）",
              frappe.db.exists("Pearnly Push Log", {"trigger": "line_mirror"}))
    finally:
        store.clear_session(LINE_UID)
        line_binding.unbind(LINE_UID)
        created = [
            row[0]
            for row in frappe.db.sql(
                "select name from `tabPearnly Push Log` where `trigger`='line_mirror'"
            )
            if row[0] not in mirrored_before
        ]
        for name in created:
            frappe.db.delete("Pearnly Push Log", name)
        frappe.db.commit()
        print("  （清理：删掉本轮产生的 %d 条镜像日志）" % len(created))

    failed = [x for x in results if not x[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
