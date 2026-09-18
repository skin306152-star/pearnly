# -*- coding: utf-8 -*-
"""DMS 建档验证（**真写**）：把一张身份证建成 DMS 客户。

这是整条链路里唯一会**往 DMS 写数据**的验证，所以约束很硬：

· **只对测试账号**（`dmstest`）跑，绝不碰客户账号；
· 客户名带 `PEARNLY TEST` 前缀，一眼看得出是测试数据，方便删；
· 身份证号用**真正合法**的 mod-11 号（构造成明显是测试段），不是瞎编的；
· 写完**回读核对**（PHP 200 不算数），并把建出来的客户号打出来。

验三件事：

1. 建客户能成（`mode=create`，回带客户号）；
2. 回读核对：DMS 里真的能读到刚写的姓名/证号/地址；
3. **幂等**：同一个证号再推一次 `create`，必须转成 `update` 且**返回同一个客户号**
   （一个身份证 = DMS 一个客户，编号唯一）。

清理办法（手动）：在 DMS 客户列表搜 `PEARNLY TEST` 删掉即可。
"""


def make_test_people_id() -> str:
    """造一个**校验位合法**的测试证号（13 位 mod-11）。前缀用 9990 段，一眼是测试。"""
    base12 = "999000000001"
    total = sum(int(base12[i]) * (13 - i) for i in range(12))
    return base12 + str((11 - (total % 11)) % 10)


def main():
    import frappe

    from pearnly_dms import dms_intake
    from pearnly_dms.dms_id_validate import is_valid_thai_id

    ENDPOINT_LABEL = "MR.ERP DMS（测试账号）"
    # 这几个 id 是上一轮"查重"里由 OCR 文本解析出来的真实主档 id
    # （กรุงเทพมหานคร / บางรัก / บางรัก / 10500），直接复用，保证地址链有效。
    addr_ids = {
        "province_id": "1",
        "district_id": "4",
        "subdistrict_id": "29",
        "zipcode_id": "147",
    }
    people_id = make_test_people_id()
    name = "PEARNLY TEST 20260918"
    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def note(label, detail):
        print("  " + label + ": " + str(detail))

    note("测试证号", people_id)
    check("测试证号自身合法（mod-11）", is_valid_thai_id(people_id), people_id)

    endpoint = frappe.db.get_value("Pearnly ERP Endpoint", {"label": ENDPOINT_LABEL}, "name")
    check("找到端点 %s" % ENDPOINT_LABEL, bool(endpoint), endpoint)
    if not endpoint:
        print("RESULT: FAIL")
        return

    # 先确认这个号**还没被建过**，否则下面的"幂等"就验不出东西
    pre = dms_intake.recognize_lookup(
        endpoint, people_id=people_id, name=name, ocr_address={})
    check("建档前能查重（调用成功）", pre.get("ok") is True, pre.get("error_code") or "")
    pre_found = (pre.get("match") or {}).get("found") if pre.get("ok") else None
    note("建档前是否已存在", pre_found)

    fields = {
        "people_id": people_id,
        "name": name,
        "prefix_id": "17",
        "birthday_be": "01/01/2530",
        "phone": "0800000000",
        "house_no": "99/1",
        "moo": "3",
        "road": "สุขุมวิท",
    }
    fields.update(addr_ids)

    # ── 1. 建客户 ────────────────────────────────────────────────────
    out = dms_intake.push_idcard_fields(
        endpoint, fields=fields, mode="create", customer_id=None,
        addresses={"": fields, "_ct": fields, "_sd": fields},
    )
    check("建客户调用成功", out.get("ok") is True, out.get("error_code") or "")
    if not out.get("ok"):
        note("原始错误", out.get("raw_error"))
        print("RESULT: FAIL")
        return
    cid = str(out.get("customer_id") or "")
    note("建出来的客户号", cid)
    note("实际动作", out.get("mode"))
    check("回带客户号", bool(cid), cid)
    check("动作是 create 或 update（幂等转了要如实说）",
          out.get("mode") in ("create", "update"), out.get("mode"))

    # ── 2. 回读核对：PHP 200 不算数 ────────────────────────────────────
    read = dms_intake.customer_fields(endpoint, customer_id=cid)
    check("回读客户成功", read.get("ok") is True, read.get("error_code") or "")
    cf = (read.get("current_fields") or {}) if read.get("ok") else {}
    check("回读到的姓名就是刚写的",
          (cf.get("name") or "").strip() == name, repr(cf.get("name")))
    check("回读到的身份证号一致",
          (cf.get("people_id") or "").strip() == people_id, repr(cf.get("people_id")))
    check("回读到的地址写进去了",
          (cf.get("house_no") or "").strip() == "99/1", repr(cf.get("house_no")))
    check("回读到的府是主档 id（不是空）",
          bool(str(cf.get("province_id") or "").strip()), repr(cf.get("province_id")))

    # ── 3. 幂等：同证号再建一次必须转 update、且是同一个客户 ──────────────
    again = dms_intake.push_idcard_fields(
        endpoint, fields=fields, mode="create", customer_id=None,
        addresses={"": fields, "_ct": fields, "_sd": fields},
    )
    check("同证号再建一次不报错", again.get("ok") is True, again.get("error_code") or "")
    check("第二次如实回 update（不谎称新建）", again.get("mode") == "update", again.get("mode"))
    check("第二次仍是同一个客户号（一个身份证 = 一个客户）",
          str(again.get("customer_id") or "") == cid,
          "%s vs %s" % (again.get("customer_id"), cid))

    # ── 4. 再查一次重，确认库里看到的就是它 ─────────────────────────────
    post = dms_intake.recognize_lookup(
        endpoint, people_id=people_id, name=name, ocr_address={})
    if post.get("ok"):
        m = post.get("match") or {}
        check("建档后查重能命中（且是同一个客户号）",
              m.get("found") is True and str(m.get("customer_id") or "") == cid,
              (m.get("found"), m.get("customer_id")))
        check("查重场景 = exact", post.get("scenario") == "exact", post.get("scenario"))
    else:
        check("建档后查重能命中", False, post.get("error_code") or "调用失败")

    print("")
    print("⚠️ 本次在测试 DMS 里建了一个客户：%s（%s）。"
          "清理：在 DMS 客户列表搜 PEARNLY TEST 删掉。" % (name, cid))
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
