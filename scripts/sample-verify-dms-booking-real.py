# -*- coding: utf-8 -*-
"""DMS 建单验证（**真写**）：走完「复核主档 → 建订车单 → 挂附件」。

这是"推送"那一半。照老项目的流程：逐问选定的车/颜色/分店/付款条件覆盖端点默认；
顾问按操作员的 DMS 账号匹配（`dmstest` 本来就在顾问名册里，所以自动归属，**不用人工配**）；
确认瞬间拿实时主档复核一遍，然后才建单；建完在同一会话挂附件。

约束：只对测试账号；客户用 `PEARNLY TEST` 那个（带前缀，便于清理）；
附件用我们仓库里的合成身份证图（不是真人数据）。

**会真的建出一张订车单**，编号会打出来。
"""


def main():
    import os

    import frappe

    from pearnly_dms import dms_advisor, dms_intake
    from pearnly_dms.dms_booking import book_in_session
    from pearnly_dms.dms_company_banks import fetch_company_banks
    from pearnly_dms.dms_qa_util import car_label, row_name

    ENDPOINT_LABEL = "MR.ERP DMS（测试账号）"
    PEOPLE_ID = "9990000000014"          # 上一轮建的那个测试客户
    NAME = "PEARNLY TEST 20260918"
    ID_CARD_IMG = "/tmp/dmscheck/idcard.jpg"
    addr_ids = {"province_id": "1", "district_id": "4", "subdistrict_id": "29", "zipcode_id": "147"}
    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def note(label, detail):
        print("  " + label + ": " + str(detail))

    endpoint = frappe.db.get_value("Pearnly ERP Endpoint", {"label": ENDPOINT_LABEL}, "name")
    check("找到端点 %s" % ENDPOINT_LABEL, bool(endpoint), endpoint)
    if not endpoint:
        print("RESULT: FAIL")
        return

    # ── 1. 登录取实时主档（对应逐问开局的取数） ──────────────────────────
    def _prep(cl, adapter):
        masters = {**cl.fetch_masters(strict=True), "company_banks": fetch_company_banks(adapter)}
        car_id = str((masters.get("cars") or [[None]])[0][0])
        paints = cl._bshsd_all("txtcarpaint", idcar=car_id) if car_id else []
        return {"masters": masters, "car_id": car_id, "paints": paints or []}

    prep = dms_intake.run_logged_in(endpoint, _prep)
    check("取实时主档成功", prep.get("ok") is not False, prep.get("error_code") or "")
    if prep.get("ok") is False:
        note("原始错误", prep.get("raw_error"))
        print("RESULT: FAIL")
        return
    masters = prep["masters"]
    for key, label in (("cars", "车型"), ("place_books", "订车地点"),
                       ("term_sales", "付款条件"), ("regis_behalfs", "登记代办"),
                       ("company_banks", "公司银行")):
        check("主档 %s 有可选行" % label, len(masters.get(key) or []) > 0,
              "%d 行" % len(masters.get(key) or []))
    check("车型有颜色可选", len(prep.get("paints") or []) > 0, "%d 个颜色" % len(prep.get("paints") or []))

    # ── 2. 顾问归属（按操作员 DMS 账号自动匹配） ─────────────────────────
    advisor, dms_user = dms_advisor.resolve_operator_advisor(endpoint, masters=masters)
    check("顾问自动归属成功（不用人工配）", bool(advisor and advisor.get("id")),
          advisor or "没匹配上")
    note("按 DMS 账号匹配的顾问", advisor)
    if not (advisor and advisor.get("id")):
        print("RESULT: FAIL")
        return

    # ── 3. 客户档落定（老流程：逐问答完先落客户，再订车） ────────────────
    fields = {
        "people_id": PEOPLE_ID, "name": NAME, "prefix_id": "17",
        "birthday_be": "01/01/2530", "phone": "0800000000",
        "house_no": "99/1", "moo": "3", "road": "สุขุมวิท", **addr_ids,
    }
    found = dms_intake.recognize_lookup(endpoint, people_id=PEOPLE_ID, name=NAME, ocr_address={})
    cid0 = str((found.get("match") or {}).get("customer_id") or "") if found.get("ok") else ""
    check("先按证号查到测试客户", bool(cid0), cid0 or (found.get("error_code") or "查不到"))
    if not cid0:
        print("RESULT: FAIL")
        return
    saved = dms_intake.push_idcard_fields(
        endpoint, fields=fields, mode="overwrite", customer_id=cid0,
        addresses={"": fields, "_ct": fields, "_sd": fields},
    )
    check("客户档写成功", saved.get("ok") is True, saved.get("error_code") or saved.get("raw_error"))
    if not saved.get("ok"):
        print("RESULT: FAIL")
        return
    customer_id = str(saved.get("customer_id") or "")
    note("客户号", customer_id)

    # ── 4. 组 payload（形状与逐问状态机产出的一致） ──────────────────────
    car_row = (masters.get("cars") or [])[0]
    paint_row = (prep.get("paints") or [])[0]
    place_row = (masters.get("place_books") or [])[0]
    term_row = (masters.get("term_sales") or [])[0]
    regis_row = (masters.get("regis_behalfs") or [])[0]
    today_be = frappe.utils.getdate().strftime("%d/%m/%Y")
    payload = {
        "qa": {
            "advisor": advisor,
            "answers": {
                # ⚠️ **id 和标签都要给** —— 真逐问在选中时就同时存了标签。
                #    只给 id 的话，复核层会把"标签从空补成真值"当成主档变化，
                #    于是拦下来要求重新确认（这正是它该有的行为，只是我第一版载荷写错了）。
                "place": {"id": str(place_row[0]), "name": row_name(place_row)},
                "car": {"id": str(car_row[0]), "label": car_label(car_row)},
                "paint": {"id": str(paint_row[0]), "name": row_name(paint_row)},
                "term": {"id": str(term_row[0]), "name": row_name(term_row)},
                "regis": {"id": str(regis_row[0]), "name": row_name(regis_row)},
                "delivery_date_be": today_be,
                "regis_name": "",
            },
            "customer": {"id": customer_id, "name": NAME},
            "draft": fields,
            "payments": [],
            "customer_dirty": False,
        }
    }
    note("选中的车型", car_row[:3])
    note("选中的颜色", paint_row[:3])
    note("交车日（佛历）", today_be)

    # ── 5. 建单 + 挂附件 ────────────────────────────────────────────────
    attach = []
    if os.path.exists(ID_CARD_IMG):
        with open(ID_CARD_IMG, "rb") as fh:
            attach = [{"display_name": "สำเนาบัตรประชาชน", "filename": "idcard.jpg",
                       "content_type": "image/jpeg", "content": fh.read()}]
        note("附件", "สำเนาบัตรประชาชน (%d 字节)" % len(attach[0]["content"]))
    else:
        note("附件", "缺 %s，这次不挂附件" % ID_CARD_IMG)

    booked = book_in_session(endpoint, payload, attach_files=attach)
    check("建单调用成功", booked.get("ok") is True,
          booked.get("error_code") or booked.get("preflight") or booked.get("raw_error") or "")
    if not booked.get("ok"):
        note("派单前的复核结果", booked.get("preflight") or "（无）")
        note("退回到哪一步", booked.get("field") or "（无）")
        print("RESULT: FAIL")
        return
    booking_no = str(booked.get("booking_no") or "")
    booking_id = str(booked.get("booking_id") or "")
    note("订车单号", booking_no)
    note("订车单 id", booking_id)
    check("DMS autonum 出了单号", bool(booking_no), booking_no)
    check("附件挂上了", booked.get("attach_ok") is True,
          {"attached": booked.get("attached"), "failed": booked.get("attach_failed")})

    # ── 6. 回读：单号能搜到，且客户对得上 ────────────────────────────────
    def _readback(cl, adapter):
        return {"found": cl.search_booking(booking_no)}

    rb = dms_intake.run_logged_in(endpoint, _readback)
    check("回读：按单号能搜到这张单", rb.get("found") == booking_id,
          "%s vs %s" % (rb.get("found"), booking_id))

    print("")
    print("⚠️ 本次在测试 DMS 里建了订车单 %s（id %s，客户 %s）。"
          "清理需在 DMS 里删单/删客户（删除路径我们还没搬也没验过，没自动删）。"
          % (booking_no, booking_id, customer_id))
    failed = [x for x in results if not x[1]]
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
