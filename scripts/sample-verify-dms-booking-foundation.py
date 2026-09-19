# -*- coding: utf-8 -*-
"""订车地基验证（纯逻辑，零 IO，不打真 DMS）：权威只读闸 / 组织归属 / 订车载荷。

这三件是订车提交链的地基，错一件就是**建错单**，所以每条都按「宁可拦下也不猜」验：

· **权威只读闸**：管理员凭据权限更高，一次误写不可回滚 —— 所以「只读」必须是
  **机械约束**（路径白名单 + 带附件一律拒），不能靠调用方自觉。
· **组织归属**：分店列表的首行**不是**这个顾问的分店，组织只能按顾问 id 问出来。
· **订车载荷**：pinned id 一旦存在就不许回落首行或钉死标量 —— 落错人/车只有月底
  对账才看得出来。

用法（容器里）：
    /home/frappe/frappe-bench/env/bin/python /tmp/dmscheck/verify_booking_foundation.py
"""


class FakeTransport:
    """假管道：按 URL 子串分发，记录每次调用。"""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def post(self, url, data=None, files=None, timeout_ms=None):
        self.calls.append(("POST", url, dict(data or {}), bool(files)))
        for fragment, handler in self.routes.items():
            if fragment in url:
                body = handler(dict(data or {}))
                return Resp(200, body, (body or "").encode())
        return Resp(200, "", b"")

    def get(self, url, timeout_ms=None):
        self.calls.append(("GET", url, {}, False))
        return Resp(200, "", b"")


from pearnly_dms.client.transport import Resp  # noqa: E402


def main():
    from datetime import date

    from pearnly_dms.adapter import admin_read as dms_admin_read
    from pearnly_dms.booking import booking_org as dms_booking_org
    from pearnly_dms.booking import booking_payload as dms_booking_payload
    from pearnly_dms.client.gateway import DmsClient, DmsClientError
    from pearnly_dms.contracts.models import BookingDefaults

    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def raises(fn, exc_type=DmsClientError):
        try:
            fn()
        except exc_type as exc:
            code = getattr(exc, "error_code", "")
            return True, (("[%s] " % code) if code else "") + str(exc)[:120]
        except Exception as exc:  # noqa: BLE001
            return False, "抛的是 %s: %s" % (type(exc).__name__, str(exc)[:100])
        return False, "没抛"

    # ══ 1. 权威只读闸：只读是机械约束，不是自觉 ══════════════════════════
    inner = FakeTransport()
    gate = dms_admin_read._ReadOnlyTransport(inner)
    check("只读闸放行主档取数",
          gate.post("https://x/dms/drfcbc/component/showdata.php") is not None)
    check("只读闸放行表单回显", gate.post("https://x/dms/cus/form.php", {"status": "e"}) is not None)
    check("只读闸放行级联选项",
          gate.post("https://x/dms/cus/component/listdistricts.php") is not None)
    check("只读闸放行 GET", gate.get("https://x/dms/home/home.php") is not None)

    for path in ("cus/new.php", "cus/edit.php", "drfcbc/new.php", "drfcbc/edit.php"):
        ok, detail = raises(lambda p=path: gate.post("https://x/dms/" + p, {}))
        check("只读闸拒绝写路径 %s" % path, ok, detail)
    ok, detail = raises(lambda: gate.post("https://x/dms/cus/edit.php", {}, files={"a": 1}))
    check("只读闸拒绝带附件的任何 POST（挂附件是写）", ok, detail)

    # ══ 2. 权威只读客户端：配没配管理员要分得清，且只登录一次 ════════════
    plain = DmsClient(FakeTransport(), "https://x/dms/")
    check("没配管理员 → 原样返回销售自己的 client",
          dms_admin_read.authoritative_read_client(plain) is plain)
    ok, detail = raises(lambda: dms_admin_read.authoritative_read_client(
        plain, require_admin=True))
    check("没配管理员 → require_admin 直接拦，不许静默走销售", ok, detail)
    check("没配管理员 → admin_read_available False",
          dms_admin_read.admin_read_available(plain) is False)

    logins = []

    def admin_factory():
        logins.append(1)
        return FakeTransport()

    withadmin = DmsClient(FakeTransport(), "https://x/dms/", admin_transport=admin_factory)
    check("配了管理员 → admin_read_available True，且不触发登录",
          dms_admin_read.admin_read_available(withadmin) is True and not logins)
    reader = dms_admin_read.authoritative_read_client(withadmin)
    check("借读返回的是另一个 client（不污染销售的会话）", reader is not withadmin)
    check("借读沿用同一个 base_url", reader.base_url == withadmin.base_url)
    dms_admin_read.authoritative_read_client(withadmin)
    check("同一个读取块只解析一次管理员登录", len(logins) == 1, len(logins))

    before = withadmin.transport
    with dms_admin_read.authoritative_read_session(withadmin) as c:
        inside = c.transport
    check("只读会话退出后换回原 transport",
          withadmin.transport is before and inside is not before)
    try:
        with dms_admin_read.authoritative_read_session(withadmin):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    check("异常退出也要换回原 transport", withadmin.transport is before)

    # ══ 3. 组织归属：按顾问 id 问，不拿分店列表首行 ══════════════════════
    GOOD_ORG = '["1","กทม","2","ทีม 1","","","","","","","",""]'
    org = dms_booking_org.parse_booking_org(GOOD_ORG)
    check("解析组织：分店/组取前四格",
          (org.branch.id, org.branch.name, org.team.id) == ("1", "กทม", "2"),
          (org.branch.id, org.branch.name, org.team.id))
    check("组织字段覆盖订车/销售两侧（24 个）", len(org.form_fields) == 24, len(org.form_fields))
    check("组织字段含两侧分店与组",
          all(k in dict(org.form_fields) for k in
              ("branch_bookval", "txtbranch_sell", "team_bookval", "txtteam_sell")))
    for bad, why in (("[]", "格数不足"),
                     ("not json", "不是 JSON"),
                     ('["1","กทม"]', "格数不足"),
                     ('["1","","2","ทีม","","","","","","","",""]', "分店名缺"),
                     ('["1","กทม","2","","","","","","","","",""]', "组名缺")):
        ok, detail = raises(lambda b=bad: dms_booking_org.parse_booking_org(b))
        check("坏组织（%s）被拦" % why, ok, detail)

    ok, detail = raises(lambda: dms_booking_org.resolve_booking_org(
        DmsClient(FakeTransport(), "https://x/dms/"), "", BookingDefaults()))
    check("没钉顾问 → 直接拦，不建单", ok, detail)

    sales = DmsClient(FakeTransport({"detailbooksell": lambda d: "err::denied"}), "https://x/dms/")
    ok, detail = raises(lambda: dms_booking_org.resolve_booking_org(sales, "7", BookingDefaults()))
    check("销售读不到组织且没配管理员 → 如实失败", ok, detail)

    sales_fail = FakeTransport({"detailbooksell": lambda d: "err::denied"})
    admin_t = FakeTransport({"detailbooksell": lambda d: GOOD_ORG})
    healed = DmsClient(sales_fail, "https://x/dms/", admin_transport=lambda: admin_t)
    got = dms_booking_org.resolve_booking_org(healed, "7", BookingDefaults())
    check("销售读不到 → 借管理员只读读到组织", got.branch.id == "1", got.branch)
    check("组织是按顾问 id 问的（idusers=7）",
          any(d.get("idusers") == "7" for _, _, d, _ in admin_t.calls),
          [d for _, _, d, _ in admin_t.calls])

    # ══ 4. 订车载荷：pinned id 不许回落 ══════════════════════════════════
    rows = [["7", "S07", "สมชาย", "0812"], ["9", "S09", "阿明", "0899"]]
    ok, detail = raises(lambda: dms_booking_payload.advisor_ref_strict(
        lambda: rows, BookingDefaults()))
    check("顾问没钉死 → ERR_DMS_ADVISOR_REQUIRED（提成不能记到别人头上）", ok, detail)

    pinned = BookingDefaults(advisor_id="7")
    ref = dms_booking_payload.advisor_ref_strict(lambda: rows, pinned)
    check("钉死命中 → 取出那一行", (ref.id, ref.name) == ("7", "สมชาย"), ref)
    ok, detail = raises(lambda: dms_booking_payload.advisor_ref_strict(lambda: None, pinned))
    check("名册取数失败 → UNAVAILABLE（可重试，不是查无此人）",
          ok and "ERR_DMS_MASTER_UNAVAILABLE" in detail, detail)
    ok, detail = raises(lambda: dms_booking_payload.advisor_ref_strict(
        lambda: [["1", "S01", "别人"]], pinned))
    check("钉死的 id 不在名册 → ADVISOR_UNMATCHED（绝不回落首行）",
          ok and "ERR_DMS_ADVISOR_UNMATCHED" in detail, detail)

    page2 = [["1", "S01", "甲"], ["2", "S02", "乙"], ["3", "S03", "丙"]]
    check("第 2 页的 pinned id 也算数",
          dms_booking_payload.ref_from_rows("txtcar", page2, "3").id == "3")
    check("按 code 且唯一命中 → 放行",
          dms_booking_payload.ref_from_rows("txtcar", page2, "", "S02").id == "2")
    ok, detail = raises(lambda: dms_booking_payload.ref_from_rows("txtcar", None, "3"))
    check("主档取数失败 + 有 pin → UNAVAILABLE", "ERR_DMS_MASTER_UNAVAILABLE" in detail, detail)
    ok, detail = raises(lambda: dms_booking_payload.ref_from_rows("txtcar", page2, "999"))
    check("pin 不在主档 → UNMATCHED", "ERR_DMS_MASTER_UNMATCHED" in detail, detail)
    ok, detail = raises(lambda: dms_booking_payload.ref_from_rows(
        "txtcar", [["1", "DUP", "甲"], ["2", "DUP", "乙"]], "", "DUP"))
    check("没有 pin、code 多命中 → 拦，不猜", "ERR_DMS_MASTER_UNMATCHED" in detail, detail)
    ok, detail = raises(lambda: dms_booking_payload.ref_from_rows("txtcar", page2, ""))
    check("没有 pin 也没有 code → 拦", "ERR_DMS_MASTER_UNMATCHED" in detail, detail)

    defaults = BookingDefaults(
        advisor_id="7", car_id="3", paint_id="P1", place_book_id="B1",
        term_sale_id="T1", regis_behalf_id="R1", delivery_days=20,
    )
    masters = {"advisors": rows, "cars": page2,
               "place_books": [["B1", "", "店 1"]],
               "term_sales": [["T1", "", "现金"]],
               "regis_behalfs": [["R1", "", "自己"]]}
    payload = dms_booking_payload.build_booking_payload(
        defaults, masters=masters, paints=[["P1", "", "ขาว"]], org=org,
        today=date(2026, 9, 18),
    )
    check("装配：单号日期 = 今天（佛历）", payload.doc_date_be == "18/09/2569", payload.doc_date_be)
    check("装配：交车日 = 今天 + 端点默认天数",
          payload.delivery_date_be == "08/10/2569", payload.delivery_date_be)
    check("装配：顾问 = 钉死那个（不是首行）", payload.advisor.id == "7", payload.advisor)
    check("装配：组织字段整包进载荷（原生组织契约）",
          len(payload.organization_fields) == 24 and payload.branch.id == "1",
          len(payload.organization_fields))
    ok, detail = raises(lambda: dms_booking_payload.build_booking_payload(
        BookingDefaults(advisor_id="7", car_id="999"), masters=masters, paints=[],
        org=org, today=date(2026, 9, 18)))
    check("装配时车型 pin 落空 → 拦（不许拿首行顶上）",
          "ERR_DMS_MASTER_UNMATCHED" in detail, detail)

    failed = [x for x in results if not x[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
