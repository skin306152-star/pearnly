# -*- coding: utf-8 -*-
"""订车回读与提交验证（纯逻辑，零 IO，不打真 DMS）。

这是整条链最不能出错的一环：**写出去的单是业务凭证**。重发会多一张单，
不核实就报成功是谎报。所以这里逐条钉：

· 单号等价要吃"前缀 + 数字主体"两格（只比一格必得假阴性）；
· 判据分三层：身份 → 关键 → 展示镜像（展示差异**不能**推翻结论）；
· `on_attempt` 必须在**跨过写边界之前**调用（POST 后崩溃不能变成第二张单）；
· **只有明确的单号重复**才允许重写；结果不明 → `OUTCOME_UNKNOWN` 且 `retry_safe=False`。

用法（容器里）：
    /home/frappe/frappe-bench/env/bin/python /tmp/dmscheck/verify_booking_readback.py
"""


class FakeTransport:
    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []
        self.events = []

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


from pearnly_dms.dms_transport import Resp  # noqa: E402


def main():
    from pearnly_dms import dms_booking_submit as submit
    from pearnly_dms import dms_booking_readback as rb
    from pearnly_dms.dms_client import DmsClient, DmsClientError

    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def raises(fn, exc_type=DmsClientError):
        try:
            return (False, "没抛"), fn()
        except exc_type as exc:
            code = getattr(exc, "error_code", "")
            return (True, (("[%s] " % code) if code else "") + str(exc)[:120]), exc
        except Exception as exc:  # noqa: BLE001
            return (False, "抛的是 %s" % type(exc).__name__), exc

    # ══ 1. 单号等价：两格拼接，不做形状推断 ═════════════════════════════
    check("逐字相同 → 同一张单", rb.docno_equal("BK000002609000007", {"txtdocno": "BK000002609000007"}))
    check("前缀 + 主体精确拼接 → 同一张单",
          rb.docno_equal("BK000002609000007",
                         {"txtprefixautonum": "BK", "txtdocno": "000002609000007"}))
    check("前缀不对 → 不是这张单",
          not rb.docno_equal("BK000002609000007",
                             {"txtprefixautonum": "XX", "txtdocno": "000002609000007"}))
    check("主体不对 → 不是这张单",
          not rb.docno_equal("BK000002609000007",
                             {"txtprefixautonum": "BK", "txtdocno": "000002609000008"}))
    check("缺前缀格 → 不是这张单（不靠猜）",
          not rb.docno_equal("BK000002609000007", {"txtdocno": "000002609000007"}))
    check("前缀不是 ASCII 也能拼（不做字母假设）",
          rb.docno_equal("ปี2569-0007", {"txtprefixautonum": "ปี2569-", "txtdocno": "0007"}))

    # ══ 2. 值等价：身份证去噪 / 金额按 Decimal / 其余折叠空白 ═══════════
    check("身份证去噪后相等",
          rb.same_value("txtpeopleid", "1-0000-73031-70", "100007303170"))
    check("金额 1,000.00 == 1000", rb.same_value("txtmoneycash", "1,000.00", "1000"))
    check("金额不同就是不相等", not rb.same_value("txtmoneycash", "1000", "1000.01"))
    check("其它字段折叠空白", rb.same_value("txtcus", "สมชาย   ใจดี", "สมชาย ใจดี"))

    # ══ 3. 三层判据 ═════════════════════════════════════════════════════
    check("idsel 不参与比对", rb.field_spec("idsel") is None)
    check("未知字段不参与比对（不放宽）", rb.field_spec("txtwhatever") is None)
    check("身份字段优先于关键", rb.field_spec("cusval") == "identity")
    check("关键字段：组织归属两侧", rb.field_spec("team_sellval") == "critical")
    check("审批经理链算关键（不是展示）", rb.field_spec("usersposi3_book") == "critical")
    check("展示镜像：地址文本等", rb.field_spec("txtprovinces") == "display")

    SUB = {"txtdocno": "BK1", "cusval": "77", "txtpeopleid": "100007303170",
           "usersval": "7", "carval": "3", "carpaintval": "P1",
           "txtprice": "500000", "txtusers": "สมชาย"}

    def form(**over):
        f = {"idsel": "R1", "txtdocno": "BK1", "cusval": "77",
             "txtpeopleid": "100007303170", "usersval": "7", "carval": "3",
             "carpaintval": "P1", "txtprice": "500000.00", "txtusers": "สมชาย"}
        f.update(over)
        return f

    check("全对 → verified（金额格式不同也算对）",
          rb.evaluate_candidate(SUB, form(), "R1").status == rb.STAGE_VERIFIED)
    m = rb.evaluate_candidate(SUB, form(idsel="R2"), "R1")
    check("idsel 对不上 → 不是这行", m.status == rb.STAGE_IDENTITY_MISMATCH, m.diagnostic_fields)
    m = rb.evaluate_candidate(SUB, form(carval=""), "R1")
    check("身份字段缺失 → identity_mismatch（不拿模糊命中顶替）",
          m.status == rb.STAGE_IDENTITY_MISMATCH and "carval" in m.diagnostic_fields,
          m.diagnostic_fields)
    m = rb.evaluate_candidate(SUB, form(usersval="9"), "R1")
    check("顾问被换 → identity_mismatch（提成归属变了）",
          m.status == rb.STAGE_IDENTITY_MISMATCH and "usersval" in m.diagnostic_fields,
          m.diagnostic_fields)
    m = rb.evaluate_candidate(SUB, form(txtprice="499999"), "R1")
    check("金额对不上 → critical_mismatch", m.status == rb.STAGE_CRITICAL_MISMATCH,
          m.diagnostic_fields)
    m = rb.evaluate_candidate(SUB, form(txtusers="名字被 DMS 规范过"), "R1")
    check("只有展示差异 → 仍然 verified + 脱敏告警",
          m.status == rb.STAGE_VERIFIED and m.warnings == ("txtusers",), (m.status, m.warnings))
    m = rb.evaluate_candidate(SUB, form(txtusers="旺财汽车-ZZZ"), "R1")
    check("告警里只有字段名、没有值",
          m.warnings == ("txtusers",) and "旺财汽车-ZZZ" not in str(m.warnings), m.warnings)

    # ══ 4. 分页 / 定因合并 / 退避 ═══════════════════════════════════════
    body = '<div data-val="a">x</div><div data-val="b">y</div><div data-val="a">z</div>'
    check("行 id 去重且保序", rb.parse_row_ids(body) == ("a", "b"), rb.parse_row_ids(body))
    check("少于整页 = 最后一页", rb.page_is_last(body, 30) is True)
    check("满页 = 还有下一页",
          rb.page_is_last("".join('<div data-val="%d">x</div>' % i for i in range(30)), 30) is False)
    check("确定性的失败优先于「没搜到」",
          rb.merge_stage(rb.STAGE_SEARCH_EMPTY, rb.STAGE_CRITICAL_MISMATCH)
          == rb.STAGE_CRITICAL_MISMATCH)
    check("歧义优先于关键不符",
          rb.merge_stage(rb.STAGE_CRITICAL_MISMATCH, rb.STAGE_AMBIGUOUS) == rb.STAGE_AMBIGUOUS)
    check("退避封顶、不无限重试",
          rb.readback_backoff_seconds(1) == 1.5 and rb.readback_backoff_seconds(99) == 3.0
          and rb.max_readback_extra_seconds() == 4.5, rb.max_readback_extra_seconds())

    # ══ 5. 提交：只许明确重复号重写；结果不明绝不重发 ═══════════════════
    def form_html(fields):
        return "<form>" + "".join(
            '<input name="%s" value="%s">' % (k, v) for k, v in fields.items()
        ) + "</form>"

    # 提交身份（不含单号；单号由 submit 拼进去）
    IDENT = {"cusval": "77", "txtpeopleid": "100007303170",
             "usersval": "7", "carval": "3", "carpaintval": "P1"}
    # 回读表单由这个可变字典渲染 —— 单号变了要跟着变（顺号重写那条用例）
    FORM = {"idsel": "R1", "txtdocno": "BK1", **IDENT}

    def set_form(**over):
        FORM.clear()
        FORM.update({"idsel": "R1", "txtdocno": "BK1", **IDENT})
        FORM.update(over)

    def make_client(new_handler, showdata='<div data-val="R1">x</div>'):
        t = FakeTransport({
            "drfcbc/new.php": new_handler,
            "drfcbc/component/showdata.php": lambda d: showdata,
            # 真实表单会回显**它自己那条记录**的 idsel —— 所以两张内容相同的单
            # 各自都能对上，歧义才可能出现。
            "drfcbc/form.php": lambda d: form_html({**FORM, "idsel": str(d.get("id") or "")}),
            "drfcbc/view.php": lambda d: "",
        })
        return DmsClient(t, "https://x/dms/"), t

    # 5.1 成功路径
    set_form()
    cl, t = make_client(lambda d: "")
    bid, docno = submit.submit_booking(cl, IDENT, "BK1")
    check("提交后回读确认 → 返回 (id, 单号)", (bid, docno) == ("R1", "BK1"), (bid, docno))
    check("回读全程不重发 new.php（只 POST 一次）",
          sum(1 for m, u, _, _ in t.calls if "drfcbc/new.php" in u) == 1)

    # 5.2 on_attempt 必须在跨过写边界之前
    set_form()
    order = []
    cl, t = make_client(lambda d: (order.append("POST"), "")[1])
    submit.submit_booking(cl, IDENT, "BK1", on_attempt=lambda dn: order.append("attempt:" + dn))
    check("on_attempt 在 POST 之前调用（POST 后崩溃不能变成第二张单）",
          order == ["attempt:BK1", "POST"], order)

    # 5.2b 建单入口要把 on_attempt **透传**下去
    # 判据本身在上面钉过；这里防的是"半路丢了回调" —— `book_in_session` 是 LINE 那条
    # 链唯一进建单的门，漏透传等于登记永远不生效，而症状只在超时/重发时才出现。
    import ast

    from pearnly_dms import dms_booking as booking_mod

    tree = ast.parse(open(booking_mod.__file__, encoding="utf-8").read())
    forwarded = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "book_in_session"):
            continue
        params = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call)
                    and getattr(sub.func, "attr", "") == "create_booking_via_form"):
                forwarded = "on_attempt" in params and any(
                    k.arg == "on_attempt" for k in sub.keywords)
    check("book_in_session 把 on_attempt 透传给建单调用", forwarded)

    # 5.3 明确的重复号 → 顺号重写一次
    seen = []

    def dup_handler(d):
        seen.append(d.get("txtdocno"))
        return 'err::"เลขที่ใบจอง" ซ้ำ' if len(seen) == 1 else ""

    set_form(idsel="R9", txtdocno="BK2606000002")  # 顺号后的那张单
    cl, t = make_client(dup_handler, showdata='<div data-val="R9">x</div>')
    bid, docno = submit.submit_booking(cl, IDENT, "BK2606000001")
    check("明确重复号 → 顺号重写（BK2606000002）", docno == "BK2606000002", (seen, docno))

    # 5.4 非重复的 err:: → 直接报错，不重写
    set_form()
    cl, t = make_client(lambda d: "err::something else")
    (ok, detail), _ = raises(lambda: submit.submit_booking(cl, IDENT, "BK1"))
    check("非重复的 err:: → 直接报错，不重写（不重发）", ok and "ERR_DMS_IMPORT" in detail, detail)
    check("非重复错误只 POST 一次",
          sum(1 for m, u, _, _ in t.calls if "drfcbc/new.php" in u) == 1)

    # 5.5 传输异常（可能已提交）→ 回读；回读不出来 → OUTCOME_UNKNOWN 且不许重发
    def boom(d):
        raise TimeoutError("connection reset after send")

    cl, t = make_client(boom, showdata="")
    exc = None
    try:
        submit.submit_booking(cl, IDENT, "BK1")
    except submit.DmsBookingOutcomeUnknown as e:
        exc = e
    check("超时/断连 → 抛 OUTCOME_UNKNOWN（不谎报成功）", exc is not None)
    check("OUTCOME_UNKNOWN 标着 submitted=True / retry_safe=False",
          exc is not None and exc.response_body.get("submitted") is True
          and exc.response_body.get("retry_safe") is False, exc.response_body if exc else None)
    check("OUTCOME_UNKNOWN 带定因阶段与只读来源（可排障）",
          exc is not None and exc.response_body.get("readback_stage") == "search_empty"
          and exc.response_body.get("readback_source") in ("sales", "admin_unavailable"),
          exc.response_body if exc else None)
    check("异常路径也不重发 new.php",
          sum(1 for m, u, _, _ in t.calls if "drfcbc/new.php" in u) == 1)
    # 对方「回过话」但结果核不出来时：只留 sha256 作证据，不落任意 HTML/PII。
    set_form()
    cl, t = make_client(lambda d: "<html>secret customer stuff</html>", showdata="")
    exc = None
    try:
        submit.submit_booking(cl, IDENT, "BK1")
    except submit.DmsBookingOutcomeUnknown as e:
        exc = e
    rb_ = exc.response_body if exc else {}
    check("回读不出来时留 sha256 作证据（64 位十六进制）",
          len(rb_.get("response_sha256") or "") == 64, rb_.get("response_sha256"))
    check("证据里不留原始 HTML/PII",
          "secret customer stuff" not in str(rb_) and "<html>" not in str(rb_), rb_)

    # 5.6 回读搜到两张完全相同的单 → 歧义，不许随便挑
    cl, t = make_client(
        boom, showdata='<div data-val="R1">x</div><div data-val="R2">x</div>')
    exc = None
    try:
        submit.submit_booking(cl, IDENT, "BK1")
    except submit.DmsBookingOutcomeUnknown as e:
        exc = e
    check("两张完全匹配 → 报歧义（不随便挑一张）",
          exc is not None and exc.response_body.get("readback_stage") == rb.STAGE_AMBIGUOUS,
          exc.response_body if exc else None)

    failed = [x for x in results if not x[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
