# -*- coding: utf-8 -*-
"""浏览器编辑器页面验收用的夹具：建草稿 / 发票据 / 回读结果 / 清理。

为什么要有它：那一页（`/assets/pearnly_core/pearnly-app/index.html#/dms/editor`）认的是
**一次性票据**，不是站点会话。所以自动化验收必须先造出一份真草稿、真绑定，再发一张票 ——
页面拿票就能自己跑起来，不需要人去走一遍 LINE。

三种模式（由环境变量 `MODE` 选）：

  · `prepare`  —— 建绑定 + 草稿会话，发票据；打印 `FIXTURE {json}`
  · `inspect`  —— 回读会话与推送日志；打印 `STATE {json}`（页面点过保存之后要对的就是它）
  · `cleanup`  —— 删掉本轮造的绑定 / 会话 / 镜像日志

用法见 `scripts/sample-verify-dms-edit-page.sh`。
"""


def main():
    import json
    import os
    import secrets

    import frappe

    from pearnly_core import line_binding
    from pearnly_core import line_session as store
    from pearnly_dms import dms_booking_edit as be
    from pearnly_dms import dms_browser_ticket as tickets
    from pearnly_dms.dms_intake import run_logged_in
    from pearnly_dms.dms_ocr_service import resolve_dms_endpoint

    mode = (os.environ.get("MODE") or "prepare").strip()
    line_uid = (os.environ.get("LINE_UID") or "").strip()
    user = (os.environ.get("SITE_USER") or "").strip()
    nonce = (os.environ.get("NONCE") or "").strip()

    if mode in ("prepare", "prepare-customer", "handoff", "handoff-customer"):
        # **用真实主档造草稿**：选项 id 全都取自当下这一次真登录读到的 DMS 主档。
        # 拿假 id 造的草稿，页面能打开、一保存就会被服务端的权威复核挡下
        # （那正是它该有的行为），验收就变成"验了个假的"。
        endpoint = resolve_dms_endpoint(user, None)
        if not endpoint:
            print("FAIL 站点没有可用的 mrerp_dms 端点")
            raise SystemExit(1)

        def _read(client, _adapter):
            masters = client.fetch_masters(strict=True)
            cars = masters.get("cars") or []
            if not cars:
                return {"ok": False, "why": "DMS 车型主档是空的"}
            car_id = str(cars[0][0])
            paints = client._bshsd_all("txtcarpaint", idcar=car_id) or []
            provinces = client.list_geo("provinces", "")
            province_id = str(provinces[0][0]) if provinces else ""
            districts = client.list_geo("districts", province_id) if province_id else []
            district_id = str(districts[0][0]) if districts else ""
            subs = client.list_geo("subdistricts", district_id) if district_id else []
            sub_id = str(subs[0][0]) if subs else ""
            zips = client.list_geo("zipcodes", sub_id) if sub_id else []
            zip_id = str(zips[0][0]) if zips else ""
            return {
                "ok": True,
                "masters": masters,
                "car": cars[0],
                "paint": paints[0] if paints else None,
                "prefix": (masters.get("prefixes") or [[None]])[0],
                "place": (masters.get("place_books") or [None])[0],
                "term": (masters.get("term_sales") or [None])[0],
                "regis": (masters.get("regis_behalfs") or [None])[0],
                "geo": {"province_id": province_id, "district_id": district_id,
                        "subdistrict_id": sub_id, "zipcode_id": zip_id},
            }

        real = run_logged_in(endpoint, _read)
        if not isinstance(real, dict) or real.get("ok") is not True:
            print("FAIL 读 DMS 主档失败：%s" % json.dumps(real, ensure_ascii=False, default=str)[:400])
            raise SystemExit(1)
        if not real["paint"]:
            print("FAIL 第一个车型没有任何颜色，造不出可保存的草稿")
            raise SystemExit(1)

        def name_of(row):
            return str(row[2]) if row and len(row) > 2 and row[2] else str(row[0])

        car_row, paint_row = real["car"], real["paint"]
        qa = {
            "endpoint_id": endpoint["id"],
            "step": "pay_more",
            "customer": {"id": "", "name": "คุณสมชาย"},
            "advisor": {"id": "A1", "name": "dmstest"},
            "draft": {
                "people_id": "1101700998118",
                "prefix_id": str(real["prefix"][0]),
                "prefix_name": name_of(real["prefix"]),
                "name": "คุณสมชาย",
                "birthday_be": "15/05/2530",
                "phone": "0811111111",
                "house_no": "99/1",
                **real["geo"],
            },
            "answers": {
                "place": {"id": str(real["place"][0]), "name": name_of(real["place"])},
                "car": {"id": str(car_row[0]), "label": ("%s %s" % (car_row[1], car_row[2])).strip()},
                "paint": {"id": str(paint_row[0]), "name": name_of(paint_row)},
                "delivery_date_be": "20/09/2569",
                "term": {"id": str(real["term"][0]), "name": name_of(real["term"])},
                "regis": {"id": str(real["regis"][0]), "name": name_of(real["regis"])},
                "regis_name": "คุณสมชาย",
            },
            "files": {"id_card_mid": "MID-ID", "slip_mid": None},
            "payments": [{"channel": "cash", "amount": "5000.00", "extra": {}}],
        }
        customer_mode = mode in ("prepare-customer", "handoff-customer")
        line_uid = "verify-page-" + secrets.token_hex(4)
        nonce = secrets.token_hex(8)
        line_binding.bind(line_uid, user, "page-verify")
        if customer_mode:
            # 客户档那一态是 `reviewing`：只有客户资料，没有订车的答案/付款/附件。
            store.set_session(
                line_uid,
                "reviewing",
                {
                    "nonce": nonce,
                    "endpoint_id": endpoint["id"],
                    "draft": qa["draft"],
                    "id_card": {"prefix_name": qa["draft"]["prefix_name"]},
                    "customer_id": "",
                    "mode": "customer",
                },
            )
        else:
            store.set_session(line_uid, be.STATE, {"nonce": nonce, "qa": qa,
                                                   "endpoint_id": endpoint["id"]})
        ticket = tickets.issue(line_uid, "customer" if customer_mode else "booking")
        payload = {
                    "line_user_id": line_uid,
                    "nonce": nonce,
                    "ticket": ticket,
                    "user": user,
                    "endpoint": endpoint["id"],
                    "car_label": qa["answers"]["car"]["label"],
                    "paint_label": qa["answers"]["paint"]["name"],
                    "prefix_id": qa["draft"]["prefix_id"],
                    "province_id": real["geo"]["province_id"],
        }
        if mode.startswith("handoff"):
            # 给人点的那条链：站点地址 + `/liff/dms-booking`（本机没 LIFF，走的是网页回落）。
            # 打开那一页时会用**你自己的站点会话**换一张新票据，再接给前端页面。
            base = (os.environ.get("PUBLIC_BASE") or "").rstrip("/")
            query = "draft=%s%s" % (nonce, "&editor=customer" if customer_mode else "")
            payload["open_url"] = "%s/liff/dms-booking?%s" % (base, query)
        print("FIXTURE " + json.dumps(payload, ensure_ascii=False))
        return

    if mode == "inspect":
        sess = store.get_session(line_uid) or {}
        payload = sess.get("payload") or {}
        qa = payload.get("qa") or {}
        # 订车那一态把草稿放在 `qa.draft`，客户档那一态直接放 `draft`（老产品同形）。
        draft = qa.get("draft") or payload.get("draft") or {}
        rows = frappe.db.sql(
            "select name, response_body from `tabPearnly Push Log` "
            "where `trigger`='line_mirror' order by creation desc limit 1",
            as_dict=True,
        )
        card = {}
        if rows:
            try:
                card = json.loads(rows[0]["response_body"] or "{}")
            except ValueError:
                card = {}
        messages = card.get("messages") or [{}]
        footer = ((messages[0].get("contents") or {}).get("footer") or {}).get("contents") or []
        print(
            "STATE "
            + json.dumps(
                {
                    "state": sess.get("state") or "",
                    "nonce": payload.get("nonce") or "",
                    "phone": draft.get("phone") or "",
                    "house_no": draft.get("house_no") or "",
                    "amount": (qa.get("payments") or [{}])[0].get("amount") or "",
                    "mirror_total": frappe.db.count("Pearnly Push Log", {"trigger": "line_mirror"}),
                    "card_type": (messages[0] or {}).get("type") or "",
                    "card_actions": [b.get("action", {}).get("data", "") for b in footer],
                },
                ensure_ascii=False,
            )
        )
        return

    if mode == "cleanup":
        store.clear_session(line_uid)
        line_binding.unbind(line_uid)
        # 只删**本轮之后**产生的镜像日志（`SINCE` 由调用方在开工前记下）——
        # 别顺手把别人的记录清了。
        since = (os.environ.get("SINCE") or "").strip()
        if since:
            frappe.db.delete(
                "Pearnly Push Log", {"trigger": "line_mirror", "creation": (">=", since)}
            )
        frappe.db.commit()
        print("CLEANED")
        return

    raise SystemExit("unknown MODE: %s" % mode)


if __name__ == "__main__":
    main()
