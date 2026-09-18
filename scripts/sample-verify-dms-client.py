# -*- coding: utf-8 -*-
"""DMS 业务客户端验证（A 部分）：**用假管道验纯逻辑**，不起浏览器、不连网络。

为什么先做这一半：业务客户端被设计成"不认识 Playwright"，塞个假管道就能跑。
所以那些真正容易写错的规则 —— 主档钉了 id 就不许回落、单号撞重复要顺号、
同渠道订金不许合并、员工表"空"和"取不到"必须分开 —— 都能在这里确定性验完。
真系统那一半见 `sample-verify-dms-client-real.sh`。

用法（在容器里，不需要 frappe：这批模块不 import frappe）：
    /home/frappe/frappe-bench/env/bin/python /tmp/dmscheck/verify_client.py
"""


class FakeTransport:
    """假管道：按 URL 子串分发，记录每次调用。业务客户端只认 get/post。"""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def post(self, url, data=None, files=None, timeout_ms=None):
        self.calls.append((url, dict(data or {})))
        for fragment, handler in self.routes.items():
            if fragment in url:
                body = handler(dict(data or {}))
                return Resp(200, body, (body or "").encode())
        return Resp(200, "", b"")

    def get(self, url, timeout_ms=None):
        self.calls.append((url, {}))
        return Resp(200, "", b"")


from pearnly_dms.dms_transport import Resp  # noqa: E402


def main():
    import json

    from pearnly_dms import dms_docno, dms_employees, dms_payments
    from pearnly_dms.dms_client import DmsClient, DmsClientError
    from pearnly_dms.dms_models import BookingDefaults

    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def raises(fn, exc_type):
        try:
            fn()
        except exc_type as exc:
            # 把 error_code 一起带出来 —— 校验"报的是哪种错"比"报没报错"更关键
            code = getattr(exc, "error_code", "")
            return True, (("[%s] " % code) if code else "") + str(exc)[:120]
        except Exception as exc:  # noqa: BLE001
            return False, "抛的是 %s: %s" % (type(exc).__name__, str(exc)[:100])
        return False, "没抛"

    # ══ 1. 单号：顺号与撞号识别 ══════════════════════════════════════════
    check("bump_docno 保持位宽", dms_docno.bump_docno("BK2606000001") == "BK2606000002",
          dms_docno.bump_docno("BK2606000001"))
    check("bump_docno 无数字尾就补 1", dms_docno.bump_docno("BK") == "BK1")
    check("识别单号重复报错", dms_docno.is_duplicate_docno_error('err::"เลขที่ใบจอง" ซ้ำ'))
    check("别的 err 不算单号重复", not dms_docno.is_duplicate_docno_error("err::something else"))
    check("listing_docnos 只认同形号",
          dms_docno.listing_docnos("BK2606000007 BK2606000008 BK99", "BK2606", 6)
          == {"BK2606000007", "BK2606000008"})

    # ══ 2. 订金渠道映射 ══════════════════════════════════════════════════
    fields = dms_payments.payment_form_fields((
        {"channel": "cash", "amount": "5000.00"},
        {"channel": "transfer", "amount": "1500.5", "extra": {"src": "KBANK 1234567890"}},
    ))
    check("订金合计正确", fields.get("txtearnestmoney") == "6500.50", fields.get("txtearnestmoney"))
    check("现金字段", fields.get("txtmoneycash") == "5000.00", fields.get("txtmoneycash"))
    check("转账字段", fields.get("txtmoneytfmon") == "1500.50", fields.get("txtmoneytfmon"))
    check("转账来源拆成银行名+账号",
          fields.get("txtbanknametffrom") == "KBANK"
          and fields.get("txtaccountnumtffrom") == "1234567890",
          (fields.get("txtbanknametffrom"), fields.get("txtaccountnumtffrom")))
    check("未知渠道必须报错（不许静默丢钱）",
          *raises(lambda: dms_payments.payment_form_fields(
              ({"channel": "crypto", "amount": "1"},)), ValueError))
    check("同渠道重复必须报错（不许拼成两笔）",
          *raises(lambda: dms_payments.payment_form_fields(
              ({"channel": "cash", "amount": "1"}, {"channel": "cash", "amount": "2"})), ValueError))
    check("空 payments → 空 dict（保留表单默认）", dms_payments.payment_form_fields(()) == {})

    # ══ 3. 员工表：空 vs 取不到必须分得开 ════════════════════════════════
    row = '<div data-val="337"><p>WKC99</p><p>wkuser99</p><p>สมชาย</p></div>'
    parsed = dms_employees.parse_employee_rows(row)
    check("员工行解析",
          parsed == [{"id": "337", "code": "WKC99", "login": "wkuser99", "name": "สมชาย"}],
          parsed)
    check("空 body → 空列表（员工表真的没人）", dms_employees.parse_employee_rows("") == [])
    check("ndt:: 无行 → 空列表", dms_employees.parse_employee_rows("ndt::no rows") == [])
    check("有内容却解不出 → None（取数挂了，不是没人）",
          dms_employees.parse_employee_rows("<html>login page</html>") is None,
          dms_employees.parse_employee_rows("<html>login page</html>"))
    emps = [{"id": "1", "code": "wkuser99", "login": "someone_else", "name": "A"},
            {"id": "2", "code": "WKC99", "login": "wkuser99", "name": "B"}]
    check("员工匹配只比 login 列（编号撞名不算）",
          dms_employees.match_by_login(emps, "wkuser99") == emps[1],
          dms_employees.match_by_login(emps, "wkuser99"))
    check("多命中 → None（猜谁都可能记错提成）",
          dms_employees.match_by_login(emps + [emps[1]], "wkuser99") is None)

    # ══ 4. 表单解析与地址归一 ════════════════════════════════════════════
    form = (
        '<form>'
        '<input name="txtcuscode" value="C0001">'
        '<input name="txtcusname" value="">'
        '<select name="selprefix"><option value="">-- เลือก --</option>'
        '<option value="17">นาย</option></select>'
        '<input type="checkbox" name="chk1">'
        '<input type="checkbox" name="chk2" checked>'
        '<input type="submit" name="btnsave" value="save">'
        '<textarea name="txtnote">hi there</textarea>'
        '</form>'
    )
    cl = DmsClient(FakeTransport(), "https://example.test/dms/")
    d = cl._parse_form_defaults(form)
    check("表单默认值：普通 input", d.get("txtcuscode") == "C0001")
    # ⚠️ 这个行为照抄老产品：`_selected_value` 没有 selected 时取【第一个 option 的值】，
    # 而真实表单第一项通常是 `<option value="">-- เลือก --</option>` 占位 → 得到空串。
    # 所以"下拉没选"会以空串形式回到 data 里 —— 注意：**不再靠补第一项蒙混过去**，
    # 而是被 `_guard_required_selects`（= `dms_customer_geo.validate_customer_selects`）
    # 拦下来要求显式选择。见本文件第 5 节的拦截用例。
    check("表单默认值：没有 selected 时取第一项（占位为空串，照老产品行为）",
          d.get("selprefix") == "", repr(d.get("selprefix")))
    d_sel = cl._parse_form_defaults(
        '<select name="selprefix"><option value="">--</option>'
        '<option value="17" selected>นาย</option></select>')
    check("表单默认值：有 selected 就取它", d_sel.get("selprefix") == "17", d_sel.get("selprefix"))
    check("表单默认值：没勾的勾选框不写", "chk1" not in d)
    check("表单默认值：勾了的有", "chk2" in d)
    check("表单默认值：提交按钮不算数据", "btnsave" not in d)
    check("表单默认值：textarea", d.get("txtnote") == "hi there", d.get("txtnote"))
    check("地区名前缀被剥掉", cl._norm_geo("จังหวัดกรุงเทพมหานคร") == "กรุงเทพมหานคร",
          cl._norm_geo("จังหวัดกรุงเทพมหานคร"))
    opts = cl._parse_options('<option value="">--</option><option value="10">กรุงเทพ</option>')
    check("下拉选项丢掉空 value 占位", opts == [["10", "กรุงเทพ"]], opts)
    rows_body = (
        '<div data-val="651" onclick="ctllistdata(this)"><div class="detaildata">'
        '<div><p>C0001</p><p>สมชาย</p></div>'
        '<div><p>1000000730317</p></div></div><div class="statuscf"></div></div>'
    )
    parsed_rows = cl._parse_customer_rows(rows_body)
    check("客户搜索结果解析",
          bool(parsed_rows) and parsed_rows[0]["customer_id"] == "651"
          and parsed_rows[0]["people_id"] == "1000000730317",
          parsed_rows)

    # ══ 5. 主档：钉了 id 就不许回落（这条最贵） ══════════════════════════
    masters = FakeTransport(routes={
        "drfcbc/component/bshsd.php": lambda data: json.dumps(
            [["7", "CAMRY", "Camry", "0", "Toyota"]] if data.get("elemname") == "txtcar"
            else [["3", "ADV1", "Sales One", "0812345678"]] if data.get("elemname") == "txtusers"
            else []
        ),
    })
    cl2 = DmsClient(masters, "https://example.test/dms/")
    ref = cl2._ref_from_default("txtcar", "7", "", "")
    check("钉了 id → 取到那一行", ref.id == "7" and ref.name == "Camry", ref)
    ok, detail = raises(lambda: cl2._ref_from_default("txtcar", "999", "", ""), DmsClientError)
    check("钉的 id 不在主档 → 报错，绝不悄悄回落首行", ok, detail)
    check("报错码是 UNMATCHED（重试没意义）", "ERR_DMS_MASTER_UNMATCHED" in detail, detail)
    ok, detail = raises(lambda: cl2._advisor_ref_strict(BookingDefaults()), DmsClientError)
    check("顾问没钉死 → 报错（不能把提成记到别人头上）", ok, detail)

    # 主档翻页：满页继续翻，短页收尾
    def paged_rows(data):
        amt = int(data.get("bshsdamt", 200))
        page = int(data.get("bshsdcurrpage", 1))
        start = 1 + (page - 1) * amt
        stop = min(1 + page * amt, 251)
        return json.dumps([["%d" % i, "C%d" % i, "N%d" % i] for i in range(start, stop)])

    paged = FakeTransport(routes={"drfcbc/component/bshsd.php": paged_rows})
    cl3 = DmsClient(paged, "https://example.test/dms/")
    allrows = cl3._bshsd_all("txtcar", page_size=200)
    check("主档翻页取全（250 行要翻两页）",
          allrows is not None and len(allrows) == 250, len(allrows) if allrows else allrows)
    check("翻页只用 2 次请求",
          sum(1 for u, _ in paged.calls if "bshsd.php" in u) == 2,
          sum(1 for u, _ in paged.calls if "bshsd.php" in u))

    broken = FakeTransport(routes={"drfcbc/component/bshsd.php": lambda data: "<html>error</html>"})
    cl4 = DmsClient(broken, "https://example.test/dms/")
    check("主档取数失败 → None（不能当成名册里没这个人）",
          cl4._bshsd_all("txtcar") is None, cl4._bshsd_all("txtcar"))

    # ══ 6. 建档：幂等转覆盖 / 写库后必须回读复核 ══════════════════════════
    state = {"name": ""}

    # 新建表单**自带一组默认地址**（P1/D1/S1/Z1）：不清掉，客户就会带着一个
    # 没人选过的地址被存进 DMS。这里就用这组默认值当陷阱。
    def cus_form(data):
        nm = state["name"] if data.get("status") == "e" else ""
        return ('<form><input name="txtcuscode" value="C0002">'
                '<input name="txtcusname" value="%s">'
                '<select name="selprefix"><option value="17">นาย</option></select>'
                # 表单带「全量选项」+ 一组「预选默认值」——真实表单就是这样，
                # 坑在预选的那组：不清掉就会被当成客户自己填的地址。
                '<select name="selprovinces">'
                '<option value="P1" selected>กทม</option>'
                '<option value="P9">เชียงใหม่</option></select>'
                '<select name="seldistricts">'
                '<option value="D1" selected>เขต 1</option>'
                '<option value="D9">เขต 9</option></select>'
                '<select name="selsubdistricts">'
                '<option value="S1" selected>แขวง 1</option>'
                '<option value="S9">แขวง 9</option></select>'
                '<select name="selzipcodes">'
                '<option value="Z1" selected>10100</option>'
                '<option value="Z9">10900</option></select>'
                '</form>' % nm)

    # 实时级联：请求哪个父级就只认那个父级下的子项（对不上要能拦下来）
    CHILD_ROUTES = {
        "cus/component/listdistricts.php": {"P9": ("D9", "เขต 9")},
        "cus/component/listsubdistricts.php": {"D9": ("S9", "แขวง 9")},
        "cus/component/listzipcodes.php": {"S9": ("Z9", "10900")},
    }

    def _child_routes():
        out = {}
        for path, by_parent in CHILD_ROUTES.items():
            parent = {"listdistricts.php": "selprovinces",
                      "listsubdistricts.php": "seldistricts",
                      "listzipcodes.php": "selsubdistricts"}[path.split("/")[-1]]

            def handler(data, by_parent=by_parent, parent=parent):
                hit = by_parent.get(str(data.get(parent) or ""))
                return ('<option value="%s">%s</option>' % hit) if hit else ""

            out[path] = handler
        return out

    # 面板选好的地址（P9/D9/S9/Z9）—— 与表单自带的 P1/D1/S1/Z1 不同，
    # 存下去的是哪个，一眼能看出来。
    GOOD_ADDR = {"": {"province_id": "P9", "district_id": "D9",
                      "subdistrict_id": "S9", "zipcode_id": "Z9"}}
    GOOD_FIELDS = {"people_id": "1000000730317", "name": "สมชาย ใจดี", "prefix_id": "17"}

    saves = FakeTransport(routes={
        "cus/form.php": cus_form,
        "cus/component/showdata.php": lambda data: (
            '<div data-val="777" onclick="ctllistdata">x</div>'
            if "1000000730317" in (data.get("sd") or "") else ""),
        "cus/new.php": lambda data: (state.update(name=data.get("txtcusname", "")), "")[1],
        "cus/edit.php": lambda data: (state.update(name=data.get("txtcusname", "")), "")[1],
        **_child_routes(),
    })
    cl5 = DmsClient(saves, "https://example.test/dms/")
    cid, converted = cl5.save_customer(
        fields=GOOD_FIELDS, mode="create", addresses=GOOD_ADDR)
    check("create 时发现已存在 → 转覆盖，并如实回传 converted=True",
          cid == "777" and converted is True, (cid, converted))
    check("转覆盖时走的是 edit.php",
          any("cus/edit.php" in u for u, _ in saves.calls),
          [u.split("/dms/")[-1] for u, _ in saves.calls])

    # 面板选的地址要盖过表单自带默认值（这次转覆盖，走 edit.php）。
    # 取"真正提交那次"——保存后还有一次复核重读表单，别把那次当提交。
    posted = dict(next(d for u, d in saves.calls
                       if "cus/new.php" in u or "cus/edit.php" in u))
    check("存下去的是面板选的地址（不是表单自带的 P1/D1/S1/Z1）",
          posted.get("selprovinces") == "P9" and posted.get("selzipcodes") == "Z9",
          {k: posted.get(k) for k in ("selprovinces", "seldistricts",
                                      "selsubdistricts", "selzipcodes")})

    # 规则 1 直接验：新建前必须把表单自带的默认值清空（否则没选地址的客户
    # 会带着 P1/D1/S1/Z1 被存进 DMS），而且**只清下拉、别动别的东西**。
    from pearnly_dms import dms_customer_geo as geo  # noqa: E402

    defaults = {"selprefix": "17", "selprovinces": "P1", "seldistricts": "D1",
                "selsubdistricts": "S1", "selzipcodes": "Z1",
                "selprovinces_ct": "P1", "txtcusname": "x", "txtpeopleid": "1"}
    geo.clear_customer_select_defaults(defaults)
    check("新建前清掉表单自带的地址默认值（含 _ct/_sd 各套）",
          not any(defaults[k] for k in ("selprefix", "selprovinces", "seldistricts",
                                        "selsubdistricts", "selzipcodes", "selprovinces_ct")),
          {k: v for k, v in defaults.items() if v})
    check("清默认值时不动客户姓名/证号", 
          defaults["txtcusname"] == "x" and defaults["txtpeopleid"] == "1")

    def _save_with(fields, addresses=None):
        t = FakeTransport(routes={
            "cus/form.php": cus_form,
            "cus/component/showdata.php": lambda data: '<div data-val="779" onclick="x">x</div>',
            "cus/new.php": lambda data: "",
            **_child_routes(),
        })
        cl = DmsClient(t, "https://example.test/dms/")
        return raises(lambda: cl.save_customer(
            fields=fields, mode="create", addresses=addresses), DmsClientError), t

    bad_prefix, _ = _save_with({**GOOD_FIELDS, "prefix_id": "999"}, GOOD_ADDR)
    check("称谓不在名册里 → 拦（不猜）", bad_prefix[0], bad_prefix[1])
    bad_prov, _ = _save_with(GOOD_FIELDS, {"": {**GOOD_ADDR[""], "province_id": "PX"}})
    check("省不在实时名册里 → 拦", bad_prov[0], bad_prov[1])
    bad_child, _ = _save_with(GOOD_FIELDS,
                              {"": {**GOOD_ADDR[""], "district_id": "D1"}})
    check("县不属于所选省 → 拦（父级链对不上）", bad_child[0], bad_child[1])

    noread = FakeTransport(routes={
        "cus/form.php": cus_form,
        "cus/component/showdata.php": lambda data: "",
        "cus/new.php": lambda data: "",
        **_child_routes(),
    })
    cl6 = DmsClient(noread, "https://example.test/dms/")
    ok, detail = raises(lambda: cl6.save_customer(
        fields=GOOD_FIELDS, mode="create", addresses=GOOD_ADDR), DmsClientError)
    check("create 成功码但搜不到 → 报错（PHP 200 不算写库成功）", ok, detail)

    # ══ 7. 附件：回读校验必须能发现"没真挂上" ════════════════════════════
    book_form = ('<form><input name="stsel" value="e"><input name="idsel" value="901">'
                 '<input name="txtdocno" value="BK2606000001">'
                 '<input name="fulcurrname1" value="old.png"></form>')
    att = FakeTransport(routes={
        "drfcbc/form.php": lambda data: book_form,
        "drfcbc/edit.php": lambda data: "",
    })
    cl7 = DmsClient(att, "https://example.test/dms/")
    r = cl7.attach_booking_files(booking_id="901", files=[
        {"display_name": "id.jpg", "filename": "id.jpg",
         "content_type": "image/jpeg", "content": b"x"},
    ])
    check("附件回读校验能发现没挂上", r["ok"] is False and r["attached"] == 0, r)

    failed = [x for x in results if not x[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
