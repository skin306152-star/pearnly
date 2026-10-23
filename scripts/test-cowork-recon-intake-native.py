"""Cowork 2026-09-29 修复（录入工作台、银行 / 收入对账、销项税核查）：在真实 Frappe Site 上走一遍服务端。

副作用：只写指定的本地 / 测试 Site；建一个临时 Cowork 管理员、几张对账任务与两条录入记录。整段在一个事务里，
`frappe.db.commit` 在运行期间换成空操作（结果页「保存」接口自带提交），结束时 `frappe.db.rollback()` 全部撤掉。
不连外部、不调模型、不推送 ERP、不发消息。退出码非 0 = 有断言失败。

用法（在装有本工作树代码的 bench 容器里，站点目录下）：
    ../env/bin/python /tmp/test-cowork-recon-intake-native.py <site>
"""
import json
import sys
from decimal import Decimal

import frappe

STAMP = "recon-intake-0929"


def user(email, roles):
    doc = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0],
                          "send_welcome_email": 0, "enabled": 1,
                          "roles": [{"role": role} for role in roles]})
    doc.flags.no_welcome_mail = True
    return doc.insert(ignore_permissions=True).name


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"{label} {detail}")
    print(f"  ok  {label}")


def main(site):
    frappe.init(site=site)
    frappe.connect()
    real_commit = frappe.db.commit
    frappe.db.commit = lambda *args, **kwargs: None
    try:
        run()
        print("RESULT: PASS")
    finally:
        frappe.db.commit = real_commit
        frappe.db.rollback()
        frappe.destroy()


def run():
    from pearnly_cowork.team import setup

    assert frappe.conf.get("pearnly_product") == "cowork", "site is not a Cowork site"
    setup.sync_roles()
    workspace = frappe.get_all("Cowork Workspace", filters={"enabled": 1}, pluck="name", limit_page_length=1)
    assert workspace, "no enabled Cowork Workspace on this site"
    admin = user(f"{STAMP}@example.invalid", ["Desk User", "Cowork Admin"])
    frappe.set_user(admin)
    counts = {doctype: frappe.db.count(doctype) for doctype in (
        "Cowork Bank Reconciliation", "Cowork Income Reconciliation", "Cowork OCR History")}
    print("bank"); bank(workspace[0])
    print("income"); income(workspace[0])
    print("vat check"); vat_check()
    print("intake"); intake(workspace[0])
    print(f"site counts before (rolled back at the end): {counts}")


def statement(day, description, withdrawal, deposit, balance):
    return {"date": day, "description": description, "withdrawal": withdrawal, "deposit": deposit,
            "balance": balance, "confidence": "high", "balance_ok": "", "autocorrected": False}


def ledger(day, number, debit, credit, balance=None):
    return {"date": day, "doc_no": number, "account_code": "1111", "description": "", "debit": debit,
            "credit": credit, "balance": balance}


def bank(workspace):
    from pearnly_cowork.reconciliation import bank_recon_contracts, bank_recon_service, revision
    from pearnly_cowork.recon_center import sheets

    header = {"stmt_opening": "1000", "stmt_closing": "1400", "gl_opening": "1000", "gl_closing": "1400",
              "bank_code": "kbank", "gl_account_code": ""}
    # 同一天先扣手续费 100、再存入 500（匹配）；总账另有一笔记错又冲回（借 70、借 −70）
    rows = [statement("2026-01-31", "FEE", "100", "0", "900"), statement("2026-01-31", "DEPOSIT", "0", "500", "1400")]
    gl = [ledger("2026-01-31", "RV1", "500", "0"), ledger("2026-01-31", "PV1", "0", "100"),
          ledger("2026-01-31", "JV1", "70", "0"), ledger("2026-01-31", "JV2", "-70", "0")]
    created = bank_recon_service.create(workspace, f"{STAMP} bank", f"{STAMP}-bank", rows, gl, header)
    doc = frappe.get_doc("Cowork Bank Reconciliation", created["name"])
    stored = json.loads(doc.inputs_json or "{}")
    check("bank: inputs stored in file order", [row["description"] for row in stored["statement"]] == ["FEE", "DEPOSIT"])
    check("bank: negative debit stored as a credit", (stored["gl"][3]["debit"], stored["gl"][3]["credit"]) in
          (("0", "70"), ("0", "70.00"), ("0", "70.0")), stored["gl"][3])
    before = json.loads(doc.summary_json)
    page = sheets.load("bank", doc.name, "th")
    _rows, head, _results, summary = revision.bank_compute(doc, page["inputs"])
    check("bank: opening the page keeps the stored closing", head["stmt_closing"] == Decimal("1400"), head["stmt_closing"])
    check("bank: page summary equals the stored summary", summary["formula_diff"] == before["formula_diff"]
          and summary["stmt_closing"] == before["stmt_closing"], (summary["formula_diff"], before["formula_diff"]))
    edited = json.loads(json.dumps(page["inputs"]))
    edited["gl"][1]["doc_no"] = "PV0001"
    revision.bank_save(doc, edited)
    doc.reload()
    after = json.loads(doc.inputs_json)
    check("bank: changing a number keeps the ledger balances", [row["balance"] for row in after["gl"]] ==
          [row["balance"] for row in stored["gl"]], [row["balance"] for row in after["gl"]])
    check("bank: saving without amount changes keeps the result", json.loads(doc.summary_json)["formula_diff"]
          == before["formula_diff"])
    # 2026-09-29 之前建的任务（没存原始输入）：还原的行同一天会重排，没改过就不动期末
    frappe.db.set_value("Cowork Bank Reconciliation", doc.name, "inputs_json", "")
    doc.reload()
    old = revision.bank_inputs(doc)
    _rows, head, _results, _summary = revision.bank_compute(doc, old)
    check("bank: an old task opened unchanged keeps its closing", head["stmt_closing"] == Decimal("1400"), head["stmt_closing"])

    # 待复核任务：清空一行余额后检查 / 确认不再弹系统错误
    review = bank_recon_service.create(workspace, f"{STAMP} review", f"{STAMP}-review", rows, gl, header,
                                       _review={"low_conf_count": 1, "amount_fixed_count": 0})
    lines = bank_recon_service.review(review["name"])["rows"]
    lines[0]["balance"] = None
    try:
        bank_recon_service.check_review(review["name"], lines)
        raise AssertionError("the review dialog should refuse a blank balance")
    except frappe.ValidationError:
        frappe.clear_last_message()
        frappe.local.message_log = []
        print("  ok  bank review dialog: a blank balance is refused with a message (existing guard)")
    # 结果页那条路：待复核任务在复核表里清空一行余额后点「保存」—— 原来 NoneType.quantize 系统错误
    review_doc = frappe.get_doc("Cowork Bank Reconciliation", review["name"])
    page = sheets.load("bank", review_doc.name, "th")
    edited = json.loads(json.dumps(page["inputs"]))
    edited["statement"][0]["balance"] = None
    try:
        revision.bank_save(review_doc, edited)
        raise AssertionError("saving a blank balance should be refused")
    except frappe.ValidationError as error:
        frappe.clear_last_message()
        frappe.local.message_log = []
        check("bank review page: a blank balance is refused with the balance message, not a crash",
              not isinstance(error, AttributeError), repr(error))
    review_doc = frappe.get_doc("Cowork Bank Reconciliation", review["name"])
    stored_review = json.loads(review_doc.inputs_json or "{}")
    check("bank review: the task keeps its inputs", len(stored_review.get("statement") or []) == 2)
    assert bank_recon_contracts  # imported for the site's contract module


def income(workspace):
    from pearnly_cowork.reconciliation import income_recon_service, revision
    from pearnly_cowork.recon_center import sheets

    def gl(number, account, debit, credit):
        return {"doc_no": number, "norm_doc_no": "", "date": "01/06/2569", "account_code": account,
                "description": "", "debit": debit, "credit": credit}

    def vat(number, amount):
        return {"report_ref_no": number, "report_reference_no": "", "report_invoice_no": number,
                "report_date": "2026-06-01", "report_buyer_name": "ACME", "report_amount_pre_vat": amount}

    gl_rows = [gl("IV001", "4110", 0, 1000), gl("IV001", "4190", 50, 0), gl("IV002", "4110", 0, 1000),
               gl("IV003", "4110", 0, 700), gl("IV003", "4120", 0, 300), gl("IV004", "4110", 0, 200)]
    vat_rows = [vat("IV001", 950), vat("IV002", 600), vat("IV002", 400), vat("IV003", 1000), vat("IV005", 80)]
    created = income_recon_service.create(workspace, f"{STAMP} income", f"{STAMP}-income", gl_rows, vat_rows,
                                          {"gl_account_code": "4", "amount_tolerance": "0.01"})
    doc = frappe.get_doc("Cowork Income Reconciliation", created["name"])
    check("income: ledger inputs stored", len(json.loads(doc.inputs_json)["gl"]) == 6)
    rows = frappe.get_all("Cowork Income Reconciliation Row", filters={"task": doc.name},
                          fields=["doc_no", "vat_amount", "has_gl_match", "gl_amount", "diff", "account_codes"],
                          order_by="row_index asc")
    diffs = {(row.doc_no, float(row.vat_amount)): float(row.diff or 0) for row in rows if row.has_gl_match}
    check("income: discount on the same voucher is netted", diffs[("IV001", 950.0)] == 0.0, diffs)
    check("income: one number on two VAT lines has no false difference",
          diffs[("IV002", 600.0)] == 0.0 and diffs[("IV002", 400.0)] == 0.0, diffs)
    summary = json.loads(doc.summary_json)
    ladder = (summary["gl_total"] - summary["gl_only_credit"] + summary["gl_only_debit"] + summary["vat_only_positive"]
              + summary["vat_only_negative"] + sum(diffs.values()))
    check("income: the reconciliation ladder adds up", round(ladder, 2) == round(summary["vat_total"], 2),
          (ladder, summary["vat_total"]))
    page = sheets.load("income", doc.name, "th")
    _rows, results, page_summary = revision.income_compute(doc, page["inputs"])
    check("income: the page equals the stored result", page_summary.gl_total == summary["gl_total"]
          and sorted(float(row.diff or 0) for row in results) == sorted(float(row.diff or 0) for row in rows),
          (page_summary.gl_total, summary["gl_total"]))
    check("income: every account is kept on the page",
          any(row.account_codes == "4110,4120" for row in results), [row.account_codes for row in results])
    edited = json.loads(json.dumps(page["inputs"]))
    target = next(index for index, row in enumerate(edited["vat"]) if row["doc_no"] == "IV002" and float(row["vat_amount"]) == 400)
    edited["vat"][target]["gl_amount"] = 300
    revision.income_save(doc, edited)
    doc.reload()
    saved = json.loads(doc.summary_json)
    check("income: an edited ledger amount is applied once", round(saved["gl_total"], 2) == round(summary["gl_total"] - 100, 2),
          (saved["gl_total"], summary["gl_total"]))
    # 旧任务（2026-09-29 之前建、没存原始输入）：旧口径下同一单号的每一行都带着整张凭证的总账金额。
    # 还原时同一单号只算一次（原来算两遍，页面总账多出一张凭证）。
    legacy = income_recon_service.create(workspace, f"{STAMP} income old", f"{STAMP}-income-old", gl_rows, vat_rows,
                                         {"gl_account_code": "4", "amount_tolerance": "0.01"})
    old_doc = frappe.get_doc("Cowork Income Reconciliation", legacy["name"])
    frappe.db.set_value("Cowork Income Reconciliation", old_doc.name, "inputs_json", "")
    for name in frappe.get_all("Cowork Income Reconciliation Row", filters={"task": old_doc.name, "doc_no": "IV002"},
                               pluck="name"):
        frappe.db.set_value("Cowork Income Reconciliation Row", name, "gl_amount", 1000)
    old_doc.reload()
    unchanged = revision.income_inputs(old_doc)
    _rows, _results, kept = revision.income_compute(old_doc, unchanged)
    check("income: an old task opened unchanged shows its stored result", kept.gl_total == summary["gl_total"])
    edited = json.loads(json.dumps(unchanged))
    edited["vat"][0]["customer_name"] = "ACME CO"
    _rows, old_results, old_summary = revision.income_compute(old_doc, edited)
    check("income: an edited old task counts each voucher once", round(old_summary.gl_total, 2) == round(summary["gl_total"], 2),
          (old_summary.gl_total, summary["gl_total"]))
    check("income: an old task keeps every account", any(row.account_codes == "4110,4120" for row in old_results),
          [row.account_codes for row in old_results])


def vat_check():
    from pearnly_cowork.reconciliation import vat_check_core, vat_check_service, vat_parse

    report = vat_parse.excel_rows([
        ["Date", "Invoice No.", "Customer", "Tax ID", "Amount", "VAT", "Total"],
        ["01/06/2026", 1001.0, "ABC", 105556012345, 1000.0, 70.0, 1070.0],
        ["", "", "Grand Total", "", 1000.0, 70.0, 1070.0]])
    check("vat: the total row is not an invoice", len(report["rows"]) == 1, report["rows"])
    row = report["rows"][0]
    check("vat: numeric cells read as printed", row["report_invoice_no"] == "1001"
          and row["report_buyer_tax_id"] == "0105556012345", row)
    invoices_sheet = vat_parse.excel_rows([
        ["วันที่", "เลขที่ใบกำกับ", "ชื่อผู้ซื้อ", "เลขประจำตัวผู้เสียภาษี", "มูลค่าสินค้า", "ภาษีมูลค่าเพิ่ม"],
        ["01/06/2569", "1001", "ABC", "0105556012345", "1,000.00", "70.00"]])
    invoices = [vat_check_core.report_row_to_invoice(item, "invoices.xlsx") for item in invoices_sheet["rows"]]
    inputs = vat_check_service._inputs({"invoices": invoices, "report_rows": report["rows"], "seller_name": "Seller",
                                        "seller_tax_id": "0105556099999", "period_year": 2026, "period_month": 6})
    _content, summary = vat_check_service.compute(inputs, "th")
    check("vat: the invoice and the report line agree", int(summary.get("n_ok") or 0) == 1,
          {key: summary.get(key) for key in ("n_ok", "n_diff", "diff_amount_total")})


def intake(workspace):
    from pearnly_core.express_rules.contracts.history import HistoryContractError
    from pearnly_cowork.history import storage
    from pearnly_core.express_rules.push.history import flatten_history_for_mrerp

    pages = [{"fields": {"invoice_number": "IV69/09-001", "date_raw": "01/06/2569", "total_amount": "1070",
                         "seller_name": "ACME", "subtotal": "1000", "vat": "70"}},
             {"fields": {"invoice_number": "IV69/09-001", "date_raw": "28-Sep-26", "date": "2026-09-28",
                         "total_amount": "1070"}}]
    record = storage.create_recognized(workspace, pages, f"{STAMP}-pages", source="cowork_line", confidence="high",
                                       ai_raw=None)
    edited = json.loads(json.dumps(record["pages"]))
    edited[0]["fields"]["total_amount"] = "1071"
    saved = storage.update_pages(record["name"], json.dumps(edited), record["record_version"])
    check("intake: a page with an odd date no longer blocks the save", saved["pages"][0]["fields"]["total_amount"] == "1071")
    edited = json.loads(json.dumps(saved["pages"]))
    edited[1]["fields"]["date_raw"] = "Sep 28th"
    try:
        storage.update_pages(record["name"], json.dumps(edited), saved["record_version"])
        raise AssertionError("an edited unreadable date should still be refused")
    except (frappe.ValidationError, HistoryContractError):
        frappe.clear_last_message()
        frappe.local.message_log = []
        print("  ok  intake: an edited unreadable date is still refused")

    copy_first = [{"is_copy": True, "fields": {"invoice_number": "IV69/09-002", "total_amount": "535",
                                               "seller_name": "ACME", "date_raw": "02/06/2569"}},
                  {"fields": {}}]
    second = storage.create_recognized(workspace, copy_first, f"{STAMP}-copy", source="cowork_line", confidence="high",
                                       ai_raw=None)
    flat = flatten_history_for_mrerp(storage.project(frappe.get_doc("Cowork OCR History", second["name"])))
    check("intake: the push reads the page the review page shows", flat["fields"].get("invoice_number") == "IV69/09-002",
          flat["fields"])


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except AssertionError as error:
        import traceback
        line = traceback.extract_tb(error.__traceback__)[-1]
        print(f"RESULT: FAIL line {line.lineno}: {line.line} {error}")
        raise SystemExit(1)
