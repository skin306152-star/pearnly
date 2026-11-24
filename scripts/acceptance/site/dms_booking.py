"""验收场景的站点侧脚本（**真写外部 DMS**，在测试环境 testdms 的 DMS 网页容器里跑）：用 LINE「确认建单」同一个入口
（`booking.service.book_in_session`）在测试账号 DMS-TEST 上真建一张带赠品的订车单 → 只读读回原单 → 照 DMS 网页原生「ยกเลิก」
同一请求作废 → 读回作废态。末行打印 ACC {json}。

    env/bin/python /tmp/acc-dms_booking.py <站点> '<JSON 参数>'

授权：用户 11-23「可以」（主会话转达）——只在测试账号 DMS-TEST 上建、建完原生作废，作废失败判不通过，不留测试单。
所以这里写前三道核对，任何一道不过就不建单：站点是测试租户（testdms*.pearnly.com）；端点登录的 DMS 地址的主机名 = 参数 dms_host；
端点是参数 user 名下的 mrerp_dms 端点（与 LINE 那条同一个解析）。

参数：user（操作员 Frappe 账号，如 sale02 那位）、endpoint_id（可空：取该账号名下唯一的端点）、dms_host（DMS-TEST 的主机名）、
people_id（已有测试客户的证号）、customer_name、car_id（带两行赠品的测试车型，默认 2）、
freebies（[[商品 id, 数量], …]，默认 [["4","1"],["18","2"]]：照 11-23 真机把第二行改成 2）、days（交车日距今天数，默认 7）、run（标记）。
结果：booking_no / booking_id、建后读回（赠品数组、客户、车型）、作废前表单核对、作废请求的回包摘要、作废后读回。
不回客户字段与任意 HTML，只回比对要用的值。
"""
import base64
import datetime
import hashlib
import json
import re
import sys
from urllib.parse import urlsplit

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]

site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
FORM = "drfcbc/form.php"
ARRAYS = ("fbproductval[]", "txtfbproduct[]", "txtfbquantity[]", "txtfbunits[]", "txtfbprice[]", "txtfbamount[]", "fbunitsval[]")


def arrays(form):
    return {key: list(form.get(key) or []) for key in ARRAYS}


def logged_in(endpoint, fn):
    from pearnly_dms.adapter.operations import run_logged_in
    out = run_logged_in(endpoint, fn)
    if isinstance(out, dict) and out.get("ok") is False:
        raise RuntimeError(f"DMS 会话失败：{out.get('error_code')} {str(out.get('raw_error') or '')[:200]}")
    return out


def list_rows(cl, booking_no):
    """原生列表按单号搜（同网页列表的搜索），回 [(id, 单元格…)]。ftd 不带：作废的单也要看得到。"""
    from pearnly_dms.booking.booking_readback import parse_row_cells
    body = cl._post_text("drfcbc/component/showdata.php",
                         {"sdtamt": "10", "sdtpage": "1", "sd": booking_no, "selcolsort": "1", "selcolsorttype": "1"})
    allo = re.findall(r'data-allo="([^"]*)"', body or "")
    return [[row_id, list(cells)] for row_id, cells in parse_row_cells(body)], allo


try:
    from pearnly_core.execution.replay import TEST_SITES
    from pearnly_dms.booking.service import book_in_session
    from pearnly_dms.booking.conversation.qa_util import car_label, find_row, row_name
    from pearnly_dms.masters import advisor as dms_advisor
    from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint
    from pearnly_dms.snapshots.directory import Directory

    if not (TEST_SITES.fullmatch(site) and "dms" in site):
        raise SystemExit(f"refuse: {site} 不是 DMS 测试租户")
    user, host = str(params.get("user") or ""), str(params.get("dms_host") or "").lower()
    if not (user and host):
        raise SystemExit("缺参数 user / dms_host")
    resolved = resolve_dms_endpoint(user, params.get("endpoint_id") or None)
    if not resolved:
        raise SystemExit(f"{user} 名下解析不到可用的 DMS 端点（{params.get('endpoint_id') or '未指定'}）")
    endpoint = resolved["name"]
    frappe.set_user(user)

    # ── 核对一：端点登录的就是 DMS-TEST（只读登录一次，不读业务数据） ──
    seen = logged_in(endpoint, lambda cl, adapter: {"host": (urlsplit(cl.base_url).hostname or "").lower()})
    result["dms_host_ok"] = seen.get("host") == host
    if not result["dms_host_ok"]:
        raise SystemExit(f"refuse: 端点 {endpoint} 登录的 DMS 主机不是登记的测试账号（{seen.get('host')}）")

    # ── 备选择（同 LINE 逐问：主档取本地快照，顾问按操作员 DMS 账号匹配） ──
    directory = Directory(endpoint)
    masters = directory.masters()
    result["snapshot"] = masters.get("_snapshot")
    car_id = str(params.get("car_id") or "2")
    car_row = find_row(masters.get("cars"), car_id)
    if car_row is None:
        raise SystemExit(f"快照里没有测试车型 {car_id}（快照：{masters.get('_snapshot')}）")
    paints = directory.paints(car_id)
    if not paints:
        raise SystemExit(f"测试车型 {car_id} 在快照里没有颜色")
    firsts = {}
    for key in ("place_books", "term_sales", "regis_behalfs"):
        rows = masters.get(key) or []
        if not rows:
            raise SystemExit(f"快照主档 {key} 是空的")
        firsts[key] = rows[0]
    advisor, dms_user = dms_advisor.resolve_operator_advisor(endpoint, masters=masters)
    if not (advisor and advisor.get("id")):
        raise SystemExit(f"操作员 DMS 账号 {dms_user!r} 在顾问名册里没对上")

    from pearnly_dms.intake import service as dms_intake
    found = dms_intake.recognize_lookup(endpoint, people_id=str(params["people_id"]), name=str(params.get("customer_name") or ""),
                                        ocr_address={})
    customer_id = str((found.get("match") or {}).get("customer_id") or "") if found.get("ok") else ""
    if not customer_id:
        raise SystemExit(f"按证号查不到测试客户（{found.get('error_code') or 'no match'}）")
    result["customer_id"] = customer_id

    freebies = [[str(pid), str(qty)] for pid, qty in (params.get("freebies") or [["4", "1"], ["18", "2"]])]
    delivery = (datetime.date.today() + datetime.timedelta(days=int(params.get("days") or 7)))
    delivery_be = delivery.strftime("%d/%m/") + str(delivery.year + 543)
    payload = {"nonce": "acc-" + str(params.get("run") or "dms"), "qa": {
        "endpoint_id": endpoint,
        "user_id": user,
        "advisor": advisor,
        "customer": {"id": customer_id, "name": str(params.get("customer_name") or "")},
        "answers": {
            "place": {"id": str(firsts["place_books"][0]), "name": row_name(firsts["place_books"])},
            "car": {"id": car_id, "label": car_label(car_row)},
            "paint": {"id": str(paints[0][0]), "name": row_name(paints[0])},
            "term": {"id": str(firsts["term_sales"][0]), "name": row_name(firsts["term_sales"])},
            "regis": {"id": str(firsts["regis_behalfs"][0]), "name": row_name(firsts["regis_behalfs"])},
            "delivery_date_be": delivery_be,
            "regis_name": "",
        },
        "car_freebies": {"car_id": car_id, "edited": True,
                         "rows": [{"product_id": pid, "qty": qty} for pid, qty in freebies]},
        # 逐问里 draft 带着身份证上的证号（建单时按它核对客户资料卡，`booking_customer.card_from_customer`）；这里没跑识别，照测试客户的证号给
        "draft": {"people_id": str(params["people_id"])}, "payments": [], "customer_dirty": False,
    }}
    result["asked"] = {"car_id": car_id, "freebies": freebies, "delivery_be": delivery_be}

    # ── 建单（与 LINE「确认」同一个入口：本地快照复核 → 建单 → 回读核对） ──
    # cancel_only：上一次建了单却没作废成（{booking_no, booking_id}）——不再建，只补作废与读回
    leftover = params.get("cancel_only") or {}
    booked = ({"ok": True, "booking_no": str(leftover["booking_no"]), "booking_id": str(leftover["booking_id"])}
              if leftover else book_in_session(endpoint, payload))
    result["booked"] = {key: booked.get(key) for key in ("ok", "booking_no", "booking_id", "error_code", "preflight", "field")}
    if not booked.get("ok"):
        result["ok"] = True  # 站点侧走完了；建不成由场景判不通过
        raise SystemExit("__done__")
    booking_no, booking_id = str(booked.get("booking_no") or ""), str(booked.get("booking_id") or "")
    result.update(ok=True, endpoint=endpoint)  # 单已建：后面任何一步出错都要把单号交回去（场景判不通过、当场补作废），不能报成没测到

    # ── 读回原单（只读）：单号、客户、车型、赠品数组 ──
    def read_back(cl, adapter):
        form = cl._parse_booking_form(cl._post_text(FORM, {"status": "e", "id": booking_id}))
        from pearnly_dms.booking.booking_readback import docno_equal
        return {"docno": booking_no if docno_equal(booking_no, form) else form.get("txtdocno"), "id": form.get("idsel"), "car": form.get("txtcar") or form.get("selcar"),
                "customer": form.get("txtcus") or form.get("idcus"), "arrays": arrays(form),
                "keys_sha": hashlib.sha256("|".join(sorted(form)).encode()).hexdigest()[:12]}
    try:
        result["readback"] = logged_in(endpoint, read_back)
    except Exception as failed:  # noqa: BLE001 —— 读回失败也照样去作废
        result["after_error"] = f"读回：{failed}"

    # ── 原生作废：列表「ยกเลิก」= form.php?status=c → 「ยืนยันยกเลิก」把整张 #frm 提交 cancel.php ──
    def cancel(cl, adapter):
        form = cl._parse_booking_form(cl._post_text(FORM, {"status": "c", "id": booking_id}))
        from pearnly_dms.booking.booking_readback import docno_equal
        # 单号按产品的比法（原生把前缀与号码分两栏：txtdocno 只是号码部分，11-24 首跑整号直比没对上、没作废）
        guard = {"stsel": form.get("stsel"), "idsel": form.get("idsel"), "docno": booking_no if docno_equal(booking_no, form) else form.get("txtdocno"),
                 "freebie_rows": len(form.get("fbproductval[]") or [])}
        if guard["stsel"] != "c" or guard["idsel"] != booking_id or guard["docno"] != booking_no:
            return {"guard": guard, "sent": False}
        resp = cl.transport.post(cl._url("drfcbc/cancel.php"), data=form)
        body = resp.text or ""
        return {"guard": guard, "sent": True, "status": resp.status_code, "body_len": len(body.strip()),
                "body_err": body.strip()[:80] if body.strip().startswith("err") else ""}
    try:
        result["cancel"] = logged_in(endpoint, cancel)
    except Exception as failed:  # noqa: BLE001
        result["after_error"] = f"作废：{failed}"

    # ── 作废后读回：列表该单状态、可做动作 ──
    def after(cl, adapter):
        rows, allo = list_rows(cl, booking_no)
        mine = [row for row in rows if str(row[0]) == booking_id]
        active = cl.search_booking(booking_no)  # 默认列表（未作废）里还搜不搜得到
        return {"rows": mine, "allo": allo[:5], "active_id": active}
    try:
        result["after"] = logged_in(endpoint, after)
    except Exception as failed:  # noqa: BLE001
        result["after_error"] = f"作废后读回：{failed}"
except SystemExit as stop:
    if str(stop) != "__done__":
        result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
