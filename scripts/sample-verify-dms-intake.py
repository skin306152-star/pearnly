# -*- coding: utf-8 -*-
"""DMS 编排层验证（A 部分）：纯逻辑，不起浏览器、不连网络。

覆盖那些"看着是小问题、其实是钱"的规则：

· 身份证 mod-11 校验（只降信心、不阻断）
· 客户字段 diff（空值 = 没这条信息，绝不覆盖；电话按纯数字比；people_id 永不进 diff）
· 顾问归属匹配（精确层优先、多命中弃权、钉死优先）
· 公司银行转账校验（账户在主档里没了 → 必须报错，不许拿旧值提交）
· 错误码 → 多语言文案（认不出的码不许把英文堆栈丢给用户）

用法（在容器里）：
    /home/frappe/frappe-bench/env/bin/python /tmp/dmscheck/verify_intake.py
"""


def main():
    from pearnly_dms import dms_advisor, dms_company_banks as banks
    from pearnly_dms.dms_client_base import DmsClientError
    from pearnly_dms.dms_customer_diff import diff_customer_fields
    from pearnly_dms.dms_errors import friendly
    from pearnly_dms.dms_id_validate import is_valid_thai_id, normalize_thai_id
    from pearnly_dms.dms_intake import score_candidates

    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def raises(fn, exc_type):
        try:
            fn()
        except exc_type as exc:
            code = getattr(exc, "error_code", "")
            return True, (("[%s] " % code) if code else "") + str(exc)[:110]
        except Exception as exc:  # noqa: BLE001
            return False, "抛的是 %s" % type(exc).__name__
        return False, "没抛"

    # ══ 1. 身份证校验 ════════════════════════════════════════════════════
    # 用真值算出来的号：1234567890123 的校验位不对，所以这里手算一个合法的
    base12 = "123456789012"
    total = sum(int(base12[i]) * (13 - i) for i in range(12))
    good = base12 + str((11 - (total % 11)) % 10)
    check("合法号通过 mod-11 校验", is_valid_thai_id(good), good)
    check("改一位就通不过", not is_valid_thai_id(good[:12] + str((int(good[12]) + 1) % 10)))
    check("不是 13 位一律不过", not is_valid_thai_id("123"))
    check("带空格/连字符也认（先归一）", is_valid_thai_id(good[:1] + " " + good[1:]), good)
    thai = good.translate(str.maketrans("0123456789", "๐๑๒๓๔๕๖๗๘๙"))
    check("泰文数字也认", normalize_thai_id(thai) == good, normalize_thai_id(thai))
    check("非 ASCII 数字不放行（防其它 Unicode 数字混进来）",
          not is_valid_thai_id("١٢٣٤٥٦٧٨٩٠١٢٣"))

    # ══ 2. 客户字段 diff ═════════════════════════════════════════════════
    cur = {"name": "สมชาย ใจดี", "phone": "081-234-5678", "house_no": "99/1",
           "province_id": "10", "people_id": "111"}
    d = diff_customer_fields(cur, {"name": "สมชาย ใจดี", "phone": "0812345678"})
    check("电话按纯数字比 → 假差异不报", d == [], d)
    d = diff_customer_fields(cur, {"name": "สมชาย ใจดี", "house_no": "99/2"})
    check("真的变了才报", d == [{"field": "house_no", "old": "99/1", "new": "99/2"}], d)
    d = diff_customer_fields(cur, {"name": "", "house_no": "  ", "soi": None})
    check("空值/缺键 = 没信息，绝不覆盖", d == [], d)
    d = diff_customer_fields(cur, {"people_id": "999", "name": "X"})
    check("people_id 永不进 diff（主键身份）",
          [x["field"] for x in d] == ["name"], d)
    d = diff_customer_fields(cur, {"name": "สมชาย    ใจดี"})
    check("连续空格折一 → 不算变化", d == [], d)

    # ══ 3. 相似候选打分 ══════════════════════════════════════════════════
    rows = [
        {"customer_id": "1", "name": "สมชาย ใจดี", "people_id": "111"},
        {"customer_id": "2", "name": "สมหญิง แสนสุข", "people_id": "222"},
    ]
    scored = score_candidates(rows, people_id="222", name="ไม่ตรงใคร")
    check("证号完全一致直接满分", scored[0]["customer_id"] == "2" and scored[0]["score"] == 100,
          scored)
    check("按分降序", scored[0]["score"] >= scored[1]["score"], [s["score"] for s in scored])

    # ══ 4. 顾问归属匹配 ══════════════════════════════════════════════════
    advisors = [[11, "wkuser99", "สมชาย", "081"], [12, "other", "wkuser99", "082"]]
    check("启发层：code 列命中",
          dms_advisor.match_advisor(advisors, "wkuser99") == {"id": "11", "name": "สมชาย"},
          dms_advisor.match_advisor(advisors, "wkuser99"))
    dup = [[11, "dup", "A", ""], [12, "dup2", "B", ""]] + [[13, "x", "dup", ""]]
    check("启发层：同层多命中 → 弃权（不猜提成归属）",
          dms_advisor.match_advisor([[11, "dup", "A"], [12, "dup", "B"]], "dup") is None)
    masters = {
        "advisors": [[11, "wkuser99", "สมชาย"], [12, "nunu", "หนู"]],
        "employees": [{"id": "11", "code": "E01", "login": "wkuser99", "name": "สมชาย"}],
    }
    check("精确层：员工表认出人 → 归属对应顾问",
          dms_advisor.match_in_masters(masters, "wkuser99") == {"id": "11", "name": "สมชาย"},
          dms_advisor.match_in_masters(masters, "wkuser99"))
    # 员工在员工表里，但 id 不在顾问下拉 → 拦截（不是回落启发层）
    bad = {"advisors": [[99, "wkuser99", "wrong"]],
           "employees": [{"id": "11", "code": "E01", "login": "wkuser99", "name": "X"}]}
    check("精确层认出但不在顾问下拉 → 拦截，不回落启发层",
          dms_advisor.match_in_masters(bad, "wkuser99") is None,
          dms_advisor.match_in_masters(bad, "wkuser99"))

    # ══ 5. 公司银行转账校验 ══════════════════════════════════════════════
    class _Cl:
        def __init__(self, rows):
            self._rows = rows

        def _bshsd_all(self, elem, **kw):
            return self._rows

    class _Ad:
        def __init__(self, rows):
            self._rows = rows

        def client(self):
            return _Cl(self._rows)

    row = ["77", "KBANK", "Kasikorn", "Silom", "123-4-56789-0"]
    ok_pay = banks.validate_company_bank_payments(_Ad([row]), [
        {"channel": "transfer", "amount": "1000", "extra": {"dst_id": "77"}}])
    check("转账目的地按主档现值覆盖",
          ok_pay[0]["extra"]["dst_account_no"] == "123-4-56789-0"
          and ok_pay[0]["extra"]["dst_bank_name"] == "Kasikorn",
          ok_pay[0]["extra"])
    ok, detail = raises(lambda: banks.validate_company_bank_payments(_Ad([row]), [
        {"channel": "transfer", "amount": "1000", "extra": {"dst_id": "999"}}]), DmsClientError)
    check("已选账户不在主档里 → 报错，绝不拿旧值提交", ok, detail)
    check("没有转账就不去多打接口",
          banks.validate_company_bank_payments(_Ad(None), [{"channel": "cash", "amount": "1"}])
          == [{"channel": "cash", "amount": "1"}])
    check("公司银行展示值", "Kasikorn" in banks.company_bank_label(row), banks.company_bank_label(row))
    check("没有 id 的脏行被丢弃",
          banks.normalize_company_bank_rows([{"details": ["x"]}, ["9", "A", "B"]])
          == [["9", "A", "B", "", ""]],
          banks.normalize_company_bank_rows([{"details": ["x"]}, ["9", "A", "B"]]))

    # ══ 6. 错误码文案 ════════════════════════════════════════════════════
    f = friendly("ERR_DMS_ADVISOR_REQUIRED")
    check("已知码有中/泰/英三种话",
          all(f.get(k) for k in ("zh", "th", "en")), sorted(f))
    check("认不出的码 → 回「服务器内部错误」，不把英文堆栈丢给用户",
          friendly("ERR_NOT_A_REAL_CODE") == friendly("ERR_UNEXPECTED"))

    failed = [x for x in results if not x[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
