# -*- coding: utf-8 -*-
"""DMS 编排层验证（B 部分）：**对真系统只读验证「查重」这一步**。

这是"识别 → 查重"那一半：拿一张身份证的字段进去，应该回来

· 命中/未命中的判定，
· 已存在客户的**全字段**（给面板显示历史资料），
· 字段差异清单（给面板标红让操作员逐条确认），
· 地址解析出的主档 id + 府/县/区/邮编选项（给面板的级联下拉），
· 相似候选（没命中时按姓名找）。

**只读**：不建客户、不建单、不改任何东西。

用真客户：`ลูกค้า ทดสอบ ราย 1`（客户号 59，证号 1234567890123）——
这是测试账号里就有的数据。
"""


def main():
    import frappe

    from pearnly_dms import dms_intake

    ENDPOINT_LABEL = "MR.ERP DMS（测试账号）"
    TEST_PEOPLE_ID = "1234567890123"   # 测试客户的证号
    TEST_NAME = "ลูกค้า ทดสอบ ราย 1"
    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def note(label, detail):
        print("  " + label + ": " + str(detail))

    name = frappe.db.get_value("Pearnly ERP Endpoint", {"label": ENDPOINT_LABEL}, "name")
    check("找到端点 %s" % ENDPOINT_LABEL, bool(name), name)
    if not name:
        print("RESULT: FAIL")
        return

    # ── 查重（命中已存在客户） ────────────────────────────────────────
    out = dms_intake.recognize_lookup(
        name,
        people_id=TEST_PEOPLE_ID,
        name=TEST_NAME,
        ocr_address={
            "house_no": "99/1", "moo": "3",
            "province": "กรุงเทพมหานคร", "district": "บางรัก",
            "subdistrict": "บางรัก", "zipcode": "10500",
        },
        phone="0812345678",
    )
    check("查重调用成功（没抛异常）", out.get("ok") is True,
          out.get("error_code") or "")
    if not out.get("ok"):
        note("原始错误", out.get("raw_error"))
        print("RESULT: FAIL")
        return

    check("场景判定 = exact（证号命中）", out.get("scenario") == "exact", out.get("scenario"))
    match = out.get("match") or {}
    check("命中了客户", match.get("found") is True, match.get("customer_id"))
    fields = match.get("current_fields") or {}
    check("回带客户全字段（姓名）", bool(fields.get("name")), fields.get("name"))
    check("回带客户全字段（三套地址）",
          any(k.startswith("house_no") for k in fields),
          sorted(k for k in fields if k.startswith("house_no")))
    note("命中的客户号", match.get("customer_id"))
    note("命中的姓名", fields.get("name"))

    diffs = out.get("field_diffs") or []
    check("字段差异清单是可渲染的结构",
          isinstance(diffs, list) and all({"field", "old", "new"} <= set(x) for x in diffs),
          diffs[:4] if diffs else "（无差异：这次给的地址和 DMS 里一致）")
    if diffs:
        note("差异字段", [x["field"] for x in diffs])
    check("people_id 不在差异里（主键身份）",
          all(x.get("field") != "people_id" for x in diffs))

    geo = out.get("geo") or {}
    check("地址级联：府选项", len(geo.get("provinces") or []) > 0,
          "%d 项" % len(geo.get("provinces") or []))
    check("地址级联：县选项已按府取到", len(geo.get("districts") or []) > 0,
          "%d 项" % len(geo.get("districts") or []))
    check("地址级联：区选项已按县取到", len(geo.get("subdistricts") or []) > 0,
          "%d 项" % len(geo.get("subdistricts") or []))
    check("地址级联：邮编已按区取到", len(geo.get("zipcodes") or []) > 0,
          "%d 项" % len(geo.get("zipcodes") or []))
    selected = geo.get("selected") or {}
    check("地址解析出了主档 id（面板要用它做默认选中）",
          bool(selected.get("province_id")) and bool(selected.get("district_id")),
          selected)
    note("OCR 文本 → 主档 id", selected)
    check("称谓选项（面板下拉要用）", len(out.get("prefixes") or []) > 0,
          [p[1] for p in (out.get("prefixes") or [])][:4])
    cands = out.get("candidates") or []
    check("候选清单带满分项", bool(cands) and cands[0].get("score") == 100, cands[:1])

    # ── 相似候选（查一个不存在的证号，按姓名找） ───────────────────────
    similar = dms_intake.recognize_lookup(
        name,
        people_id="9999999999999",
        name="ทดสอบ",
        ocr_address={},
    )
    check("查不存在证号 → 不报错", similar.get("ok") is True, similar.get("error_code"))
    if similar.get("ok"):
        check("落到 similar 或 none（不是 exact）",
              similar.get("scenario") in ("similar", "none"), similar.get("scenario"))
        if similar.get("scenario") == "similar":
            top = (similar.get("candidates") or [{}])[0]
            check("相似候选带分值", isinstance(top.get("score"), int), top)
            note("相似候选首位", {k: top.get(k) for k in ("customer_id", "name", "score")})

    # ── 载入指定客户全字段 + 级联代理 ─────────────────────────────────
    cf = dms_intake.customer_fields(name, customer_id=str(match.get("customer_id") or "59"))
    check("载入客户全字段成功", cf.get("ok") is True and bool(cf.get("current_fields")),
          cf.get("error_code") or sorted((cf.get("current_fields") or {}).keys())[:5])
    pid = selected.get("province_id")
    if pid:
        geo2 = dms_intake.geo_options(name, level="districts", parent_id=pid)
        check("分支级联代理可用", geo2.get("ok") is True and len(geo2.get("options") or []) > 0,
              "%d 项" % len(geo2.get("options") or []))

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
