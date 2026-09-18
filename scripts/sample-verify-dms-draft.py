# -*- coding: utf-8 -*-
"""DMS 建档取值模型验证（纯逻辑，零 IO）。

`dms_draft` 是"识别结果 → 写库字段"的**唯一翻译层**。它错了，写进 DMS 的客户档就是错的，
而且错法很隐蔽（比如称谓回退错、地址 id 映不成名字）。所以单独验。

用法（容器内）：
    /home/frappe/frappe-bench/env/bin/python /tmp/dmscheck/verify_draft.py
"""


def main():
    from pearnly_dms.dms_draft import (
        build_draft,
        build_summary,
        display_diffs,
        geo_name,
        master_snapshot,
    )

    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    prefixes = [["17", "นาย"], ["18", "นาง"], ["19", "นางสาว"]]
    geo = {
        "selected": {"province_id": "1", "district_id": "4", "subdistrict_id": "29",
                     "zipcode_id": "147"},
        "text": {"house_no": "99/1", "moo": "3", "soi": "", "road": "สุขุมวิท"},
        "provinces": [["1", "กรุงเทพมหานคร"], ["2", "กระบี่"]],
        "districts": [["4", "บางรัก"]],
        "subdistricts": [["29", "บางรัก"]],
        "zipcodes": [["147", "10500"]],
    }
    card = {
        "people_id": "9990000000014",
        "name": "สมชาย ใจดี",
        "birthday_be": "01/01/2530",
        "prefix_name": "นาย",
        "address": {"house_no": "99/1", "moo": "3", "road": "สุขุมวิท"},
    }

    # ── 1. draft：键名必须与网页确认页同词表 ───────────────────────────
    d = build_draft(card, geo, prefixes, "0812345678")
    check("身份键齐全", all(k in d for k in
                            ("prefix_id", "name", "people_id", "tax_id", "birthday_be", "phone")),
          sorted(k for k in d if k in ("prefix_id", "name", "people_id", "tax_id", "birthday_be", "phone")))
    check("税务号取身份证号（个人客户）", d["tax_id"] == d["people_id"], d["tax_id"])
    check("称谓按 OCR 命中主档", d["prefix_id"] == "17", d["prefix_id"])
    check("地址 id 带上（写库用）",
          (d["province_id"], d["district_id"], d["subdistrict_id"], d["zipcode_id"])
          == ("1", "4", "29", "147"),
          (d["province_id"], d["district_id"], d["subdistrict_id"], d["zipcode_id"]))
    check("zipcode 是可读名（卡片显示用）", d["zipcode"] == "10500", d["zipcode"])

    # 称谓 OCR 认不出 → 回退主档首项（不让建档卡在"称谓空"上）
    d2 = build_draft({**card, "prefix_name": "ไม่รู้จัก"}, geo, prefixes, "")
    check("称谓认不出 → 回退主档首项", d2["prefix_id"] == "17", d2["prefix_id"])
    # OCR 没给地址文本时，geo 解析出来的文本优先
    d3 = build_draft({"people_id": "1", "address": {}}, geo, prefixes, "")
    check("地址文本取级联解析结果", d3["house_no"] == "99/1" and d3["road"] == "สุขุมวิท",
          (d3["house_no"], d3["road"]))
    check("电话可为空（不瞎编）", d3["phone"] == "", repr(d3["phone"]))

    # ── 2. geo_name：id → 可读名 ───────────────────────────────────────
    check("地址 id 映成名字", geo_name(geo, "province_id", "1") == "กรุงเทพมหานคร",
          geo_name(geo, "province_id", "1"))
    check("查不到就原样返回（不编）", geo_name(geo, "province_id", "999") == "999",
          geo_name(geo, "province_id", "999"))
    check("非地址字段不参与映射", geo_name(geo, "name", "สมชาย") == "สมชาย")
    check("空值返回空串", geo_name(geo, "province_id", "") == "")

    # ── 3. summary：卡片复述成人话 ─────────────────────────────────────
    s = build_summary(d, geo)
    check("五要素齐全",
          all(s.get(k) for k in ("people_id", "name", "birthday_be", "address", "zipcode")),
          s)
    check("地址拼成一行且带泰文前缀",
          "หมู่ 3" in s["address"] and "ต.บางรัก" in s["address"]
          and "จ.กรุงเทพมหานคร" in s["address"] and s["address"].endswith("10500"),
          s["address"])

    # ── 4. master_snapshot：保留旧资料时的快照 ──────────────────────────
    cur, summ = master_snapshot({
        "people_id": "1", "name": "A", "house_no": "9", "moo": "2",
        "subdistrict_name": "บางรัก", "district_name": "บางรัก",
        "province_name": "กรุงเทพมหานคร", "zipcode_name": "10500", "prefix_name": "นาย",
    })
    check("快照保留主档字段", cur["name"] == "A" and cur["subdistrict_name"] == "บางรัก", cur["name"])
    check("快照地址用的是主档名字（不是 id）",
          "จ.กรุงเทพมหานคร" in summ["address"] and summ["zipcode"] == "10500", summ["address"])

    # ── 5. display_diffs：给人看的差异行 ───────────────────────────────
    rows = display_diffs([
        {"field": "province_id", "old": "2", "new": "1"},
        {"field": "phone", "old": "1", "new": "0812345678"},
        {"field": "未知字段", "old": "a", "new": "b"},
    ], geo)
    check("地址字段用泰文标签", rows[0]["label"] == "จังหวัด", rows[0]["label"])
    check("地址 id 映成可读名（旧值也映）",
          rows[0]["old"] == "กระบี่" and rows[0]["new"] == "กรุงเทพมหานคร", rows[0])
    check("非地址字段原样", rows[1]["old"] == "1" and rows[1]["new"] == "0812345678", rows[1])
    check("不认识的字段用原名兜底（不隐藏差异）", rows[2]["label"] == "未知字段", rows[2]["label"])

    failed = [x for x in results if not x[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
