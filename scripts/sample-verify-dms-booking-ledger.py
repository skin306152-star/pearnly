# -*- coding: utf-8 -*-
"""订车台账验证（不写真库：用假的 `frappe.new_doc` 接住那次写入）。

台账要钉住的两条：**结果如实记**（成功 success/200、失败 failed/0 + 错误码）、
**台账失败不能影响业务结果**（写台账炸了，用户那单已经推成功，不能因此报错）。
外加一条容易照抄错的：`trigger` 只能填本表的选项（manual/line/auto）。

用法（容器里）：
    /home/frappe/frappe-bench/env/bin/python /tmp/dmscheck/verify_booking_ledger.py
"""


def main():
    from decimal import Decimal

    from pearnly_dms import dms_booking_ledger as ledger
    from pearnly_dms.dms_qa_util import deposit_total

    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    # ══ 1. 订金合计：Decimal，不是 float ══════════════════════════════
    check("订金合计用 Decimal（0.1 + 0.2 正好是 0.3）",
          deposit_total([{"amount": "0.1"}, {"amount": "0.2"}]) == Decimal("0.3"),
          deposit_total([{"amount": "0.1"}, {"amount": "0.2"}]))
    check("空 / 缺金额 → 0", deposit_total([]) == 0 and deposit_total([{}]) == 0)

    # ══ 2. 台账正文：推了什么 ═════════════════════════════════════════
    qa = {
        "customer": {"id": "77", "name": "สมชาย"},
        "advisor": {"id": "7", "name": "สมชาย ใจดี"},
        "answers": {"car": {"id": "3"}, "paint": {"id": "P1"},
                    "place": {"id": "B1"}, "term": {"id": "T1"},
                    "regis": {"id": "R1"}, "regis_name": "ตัวเอง"},
        "payments": [{"channel": "transfer", "amount": "1500.00"},
                     {"channel": "cash", "amount": "500.00"}],
        "files": {"slip_mid": "MID-1"},
        "master_snapshot": {"version": "v9", "captured_at": "2026-09-19T10:00:00Z"},
        "master_validation": {"validated_at": "2026-09-19T10:05:00Z"},
    }
    body = ledger.booking_log_request_body(qa)
    check("正文标了来源与用途（line_dms / booking）",
          body["trigger"] == "line_dms" and body["mode"] == "booking", (body["trigger"], body["mode"]))
    check("客户/车/颜色/店/付款方式/登记人都记下",
          body["customer_id"] == "77" and body["car_id"] == "3" and body["paint_id"] == "P1"
          and body["qa"]["place_id"] == "B1" and body["qa"]["term_id"] == "T1"
          and body["qa"]["regis_name"] == "ตัวเอง", body["qa"])
    check("顾问 id 与名字同层（核提成不用回查主档）",
          body["advisor_id"] == "7" and body["advisor_name"] == "สมชาย ใจดี",
          (body["advisor_id"], body["advisor_name"]))
    check("订金按渠道列出并给了合计",
          body["qa"]["earnest_total"] == "2000.00" and len(body["qa"]["payments"]) == 2,
          body["qa"]["earnest_total"])
    check("记了有没有附凭证", body["qa"]["slip_attached"] is True)
    check("记了主档快照版本与复核时间",
          body["qa"]["master_snapshot_version"] == "v9"
          and body["qa"]["master_validated_at"].startswith("2026-09-19"), body["qa"])
    check("正文里不含订金以外的敏感明细（只留摘要）",
          set(body["qa"]["payments"][0]) == {"channel", "amount"}, body["qa"]["payments"][0])

    # ══ 3. 落库结果：如实 ═════════════════════════════════════════════
    ok_body = ledger.booking_log_response_body(
        {"ok": True, "booking_id": "901", "booking_no": "BK000002609000007",
         "attach_ok": True, "attached": 2, "attach_failed": []})
    check("成功：记下单号与单据 id、附件挂了几件",
          ok_body["booking_no"] == "BK000002609000007" and ok_body["booking_id"] == "901"
          and ok_body["attached"] == 2, ok_body)
    bad_body = ledger.booking_log_response_body(
        {"ok": False, "response_body": {"raw_error": "DMS 拒了"}, "error_code": "ERR_DMS_IMPORT"})
    check("失败：如实留原始错误（不吞）", bad_body["raw_error"] == "DMS 拒了", bad_body)

    # ══ 4. 写入路径：字段取值合法 + 失败不影响业务结果 ═══════════════════
    import json
    import os
    import sys
    import types

    import pearnly_core

    # 这个脚本是独立跑的（没有站点上下文）。要验"我填的取值在不在选项里"，
    # 直接读 DocType 定义文件比开一个站点更直接。
    dt_dir = os.path.join(os.path.dirname(pearnly_core.__file__), "pearnly_core",
                          "doctype", "pearnly_push_log")
    meta = json.load(open(os.path.join(dt_dir, "pearnly_push_log.json"), encoding="utf-8"))
    opts = {f["fieldname"]: (f.get("options") or "").split("\n")
            for f in meta["fields"] if f.get("fieldname") in ("trigger", "status")}
    trigger_options, status_options = opts["trigger"], opts["status"]
    check("trigger 的取值在本表选项里（线上那个 id_card 在本表不存在）",
          "line" in trigger_options, trigger_options)

    written = {}

    class _Doc:
        def __init__(self):
            self.inserted = False
            self.name = "LOG-0001"  # 真 Frappe 文档 insert 后会有 name

        def insert(self, **kw):
            self.inserted = True

    # 给 `log_booking` 里那句函数内 `import frappe` 换上一个假的模块：
    # 这样能验"写了哪些字段"，又不会真往站点库里插一行。
    real_frappe = sys.modules.get("frappe")
    fake = types.ModuleType("frappe")
    fake.db = types.SimpleNamespace(commit=lambda *a, **k: None)
    fake.new_doc = lambda dt: (_Doc())
    sys.modules["frappe"] = fake
    try:
        doc = _Doc()
        fake.new_doc = lambda dt: (written.update({"doctype": dt}), doc)[1]
        ledger.log_booking(
            endpoint="EP-1",
            payload={"qa": qa},
            result={"ok": True, "booking_no": "BK1", "elapsed_ms": 1234},
        )
        check("真写路径：写的是本平台的推送台账表", written.get("doctype") == ledger.LOG,
              written.get("doctype"))
        check("成功记 success + 200 + 单号进业务键",
              doc.status == "success" and doc.http_status == 200
              and doc.business_key == "BK1" and doc.inserted is True,
              (doc.status, doc.http_status, doc.business_key))

        # 失败时如实记 failed / 0 / 错误码
        doc = _Doc()
        fake.new_doc = lambda dt: doc
        ledger.log_booking(endpoint="EP-1", payload={"qa": qa},
                           result={"ok": False, "error_code": "ERR_DMS_IMPORT"})
        check("失败记 failed + http_status=0 + 错误码",
              doc.status == "failed" and doc.http_status == 0
              and doc.error_msg == "ERR_DMS_IMPORT",
              (doc.status, doc.http_status, doc.error_msg))
        check("状态取值在本表选项里", doc.status in status_options, doc.status)

        # 台账炸了 → 返回 None，不往上抛
        def _boom(dt):
            raise RuntimeError("ledger down")

        fake.new_doc = _boom
        got = ledger.log_booking(endpoint="EP-1", payload={"qa": qa}, result={"ok": True})
        check("台账写入失败 → 吞掉并返回 None（不影响已推成功的业务结果）", got is None, got)
    finally:
        if real_frappe is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = real_frappe

    failed = [x for x in results if not x[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
