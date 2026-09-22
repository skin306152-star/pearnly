"""Verify the ERP LINE intake slice on the dedicated laboratory.

Real Site, synthetic data, **fake LINE transport**: media download and every reply are
replaced in-process, so no channel, no outbound call and no paid OCR is involved.
Covers: who may send, binding requirement, durable storage with a content hash, repeat
send not creating a second draft, confirm creating one purchase record (and only one),
discard, and tenant isolation.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

import frappe

CONTROL_APP = "/home/frappe/frappe-bench/apps/pearnly_control"
SITE = os.environ.get("PEARNLY_ERP_POS_LAB_SITE", "erp-pos-a.localhost")
OTHER_SITE = "erp-pos-b.localhost" if SITE != "erp-pos-b.localhost" else "erp-pos-a.localhost"
ALLOWED_SITES = {"erp-pos-a.localhost", "erp-pos-b.localhost"}
SITE_ROOT = Path("/home/frappe/frappe-bench/sites")

OWNER = "erp-pos-owner@example.com"
BUYER = "erp-pos-line-buyer@example.com"
CASHIER = "erp-pos-line-cashier@example.com"
LINE_USER = "Ulablinebbbbbbbbbbbbbbbbbbbbbbbbb1"
LINE_STRANGER = "Ulablinebbbbbbbbbbbbbbbbbbbbbbbbb9"
LINE_SELLER = "Ulablinebbbbbbbbbbbbbbbbbbbbbbbbb2"
SELLER = "erp-pos-line-seller@example.com"

def _lab_image() -> bytes:
    """A real (tiny) JPEG: Frappe re-encodes image uploads, so fake bytes would fail
    on the platform side rather than on the thing under test."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buffer, "JPEG")
    return buffer.getvalue()


def _lab_pdf() -> bytes:
    """A real (blank) PDF: Frappe parses uploads with pypdf, so fake bytes fail in the
    framework before the code under test ever sees them."""
    import io

    from pypdf import PdfWriter

    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(buffer)
    return buffer.getvalue()


IMAGE = _lab_image()
PDF = _lab_pdf()


def connect(site=SITE):
    if os.environ.get("PEARNLY_ERP_POS_LAB") != "1":
        raise SystemExit("Dedicated ERP/POS laboratory required")
    if site not in ALLOWED_SITES or not (SITE_ROOT / ".erp-pos-lab").is_file():
        raise SystemExit("Dedicated lab Site marker required")
    os.chdir(SITE_ROOT)
    frappe.init(site=site, sites_path=str(SITE_ROOT))
    frappe.connect()
    if not frappe.conf.get("pearnly_erp_pos_lab") or frappe.conf.get("db_host") != "db":
        raise SystemExit("Refusing a non-lab Site")
    frappe.flags.mute_emails = True
    return site


def as_user(user, func, *args, **kwargs):
    frappe.set_user(user)
    try:
        return True, func(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the exception type is the evidence
        return False, f"{type(exc).__name__}: {exc}"[:300]


def drop_user(email):
    if frappe.db.exists("User", email):
        frappe.delete_doc("User", email, force=True, ignore_permissions=True)
        frappe.db.commit()


def ensure_member(email, roles):
    drop_user(email)
    frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0],
                    "send_welcome_email": 0,
                    "roles": [{"role": "Desk User"}, *({"role": r} for r in roles)]
                    }).insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache(user=email)


class FakeLine:
    def __init__(self, line_module, payloads):
        self.replies = []
        self._line = line_module
        self._reply = line_module.reply
        self._download = line_module.download_message_content
        self._payloads = payloads

        def reply(reply_token, messages):
            self.replies.append(messages)
            return True

        def download(message_id, **kwargs):
            return self._payloads.get(message_id, b"")

        line_module.reply = reply
        line_module.download_message_content = download

    def restore(self):
        self._line.reply = self._reply
        self._line.download_message_content = self._download

    def last_text(self):
        return (self.replies[-1][0].get("text") if self.replies else None)

    def clear(self):
        self.replies = []


def send(line_module, line_user_id, text=None, *, message_type="image", message_id="m1",
         filename=""):
    event = {"type": "message", "source": {"userId": line_user_id},
             "replyToken": "lab-reply"}
    message = {"type": message_type, "id": message_id}
    if text is not None:
        message["type"] = "text"
        message["text"] = text
    if filename:
        message["fileName"] = filename
    event["message"] = message
    return line_module._dispatch(event)


def main():
    site = connect()
    frappe.set_user("Administrator")
    from pearnly_core import line, line_binding
    from pearnly_core.provisioning import ensure_feature_roles
    from pearnly_erp_portal.integrations import intake

    if CONTROL_APP not in sys.path:
        sys.path.insert(0, CONTROL_APP)
    from pearnly_control import features as control_features

    spec = control_features.roles_payload("erp", control_features.codes_of("erp"))
    if not frappe.db.exists("User", OWNER):
        ensure_member(OWNER, [])
    ensure_feature_roles(json.dumps(spec), admin_user=OWNER)
    ensure_member(BUYER, ["ERP Purchase"])
    ensure_member(CASHIER, ["ERP Cashier"])
    ensure_member(SELLER, ["ERP Sales"])

    # 干净起点：本脚本的草稿与绑定。
    for name in frappe.get_all("ERP LINE Intake", filters={"line_user_id": LINE_USER},
                               pluck="name"):
        frappe.delete_doc("ERP LINE Intake", name, force=True, ignore_permissions=True)
    for name in frappe.get_all("Pearnly LINE Binding",
                               filters={"line_user_id": [">=", "Ulablinebbbb"]}, pluck="name"):
        frappe.delete_doc("Pearnly LINE Binding", name, force=True, ignore_permissions=True)
    # 上一次运行留下的采购单：只清本脚本自己的（供应商名是本脚本合成的）。
    for name in frappe.get_all("Purchase Document", filters={"supplier_name": "Lab Supplier"},
                               pluck="name"):
        frappe.delete_doc("Purchase Document", name, force=True, ignore_permissions=True)
    frappe.db.commit()
    line_binding.bind(LINE_USER, BUYER, "Lab Buyer")
    line_binding.bind(LINE_SELLER, SELLER, "Lab Seller")
    # 方向存在 LINE 会话里（跨进程留存），每次跑都显式设成进项，不靠默认值。
    from pearnly_core import line_session
    line_session.set_session(LINE_USER, "erp_intake", {"direction": "purchase"})

    results = {}
    fake = FakeLine(line, {"m1": IMAGE, "m2": PDF, "m3": IMAGE, "m4": IMAGE})
    try:
        # 采购岗位的人传图：留底 + 落草稿。
        results["image_handled"] = send(line, LINE_USER, message_id="m1")
        row = frappe.db.get_value("ERP LINE Intake", {"line_user_id": LINE_USER},
                                  ["name", "status", "sent_by", "media_type", "file_hash",
                                   "source_file", "direction"], as_dict=True) or {}
        results["row_status"] = row.get("status")
        results["row_sender"] = row.get("sent_by")
        results["row_media"] = row.get("media_type")
        results["row_direction"] = row.get("direction")
        results["row_hash_matches"] = row.get("file_hash") == hashlib.sha256(IMAGE).hexdigest()
        results["row_has_file"] = bool(row.get("source_file"))
        file_row = frappe.db.get_value("File", {"attached_to_name": row.get("name")},
                                       ["file_name", "is_private"], as_dict=True) or {}
        results["file_name"] = file_row.get("file_name")
        results["file_is_private"] = file_row.get("is_private")
        results["received_reply"] = fake.last_text()

        # 同一份内容再发一次：不新建草稿。
        fake.clear()
        send(line, LINE_USER, message_id="m3")
        results["duplicate_reply"] = fake.last_text()
        results["rows_after_duplicate"] = frappe.db.count(
            "ERP LINE Intake", {"line_user_id": LINE_USER})

        # 另一份文件（PDF 文件名照 LINE 给的）。
        fake.clear()
        send(line, LINE_USER, message_type="file", message_id="m2", filename="invoice.pdf")
        second = frappe.db.get_value("ERP LINE Intake",
                                     {"line_user_id": LINE_USER, "media_type": "pdf"},
                                     ["name", "source_file"], as_dict=True) or {}
        results["pdf_row_file"] = frappe.db.get_value(
            "File", {"attached_to_name": second.get("name")}, "file_name")

        # 没有采购岗位的人传单据：拒，且不留底。
        line_binding.bind("Ulablinebbbbbbbbbbbbbbbbbbbbbbbbb8", CASHIER, "Lab Cashier")
        fake.clear()
        send(line, "Ulablinebbbbbbbbbbbbbbbbbbbbbbbbb8", message_id="m1")
        results["cashier_reply"] = fake.last_text()
        results["cashier_rows"] = frappe.db.count(
            "ERP LINE Intake", {"sent_by": CASHIER})

        # 没绑定的人传单据：先让他绑。
        fake.clear()
        send(line, LINE_STRANGER, message_id="m1")
        results["stranger_reply"] = fake.last_text()
        results["stranger_rows"] = frappe.db.count(
            "ERP LINE Intake", {"line_user_id": LINE_STRANGER})

        # 确认草稿 → 建 ERP 采购草稿（带来源与内容指纹）。
        ok, created = as_user(BUYER, intake.confirm, row.get("name"),
                              supplier_name="Lab Supplier", doc_date="2026-09-21",
                              lines=[{"description": "Lab goods", "qty": 2,
                                      "unit_price": "100"}])
        results["confirm"] = ok
        results["confirm_error"] = None if ok else created
        purchase = created.get("name") if ok else None
        results["purchase_source"] = frappe.db.get_value(
            "Purchase Document", purchase, "source") if purchase else None
        results["purchase_hash"] = frappe.db.get_value(
            "Purchase Document", purchase, "image_sha256") if purchase else None
        results["intake_after_confirm"] = frappe.db.get_value(
            "ERP LINE Intake", row.get("name"), ["status", "purchase"], as_dict=True)

        # 重复确认：返回同一张单，不再建一张。
        ok, again = as_user(BUYER, intake.confirm, row.get("name"),
                            supplier_name="Lab Supplier", doc_date="2026-09-21",
                            lines=[{"description": "Lab goods", "qty": 2,
                                    "unit_price": "100"}])
        results["confirm_again_same"] = bool(ok) and again.get("name") == purchase
        results["purchases_for_supplier"] = frappe.db.count(
            "Purchase Document", {"supplier_name": "Lab Supplier"})

        # 作废草稿：废后不能再确认。
        ok, dropped = as_user(BUYER, intake.discard, second.get("name"), "duplicate")
        results["discard"] = ok
        results["discard_status"] = frappe.db.get_value(
            "ERP LINE Intake", second.get("name"), "status")
        ok, err = as_user(BUYER, intake.confirm, second.get("name"),
                          supplier_name="Lab Supplier", doc_date="2026-09-21",
                          lines=[{"description": "x", "qty": 1, "unit_price": "1"}])
        results["confirm_after_discard"] = ok
        results["confirm_after_discard_error"] = None if ok else err

        # 岗位：收银员不能看这批草稿，采购岗位能。
        ok, err = as_user(CASHIER, intake.list_intakes)
        results["cashier_can_list"] = ok
        ok, rows = as_user(BUYER, intake.list_intakes, status=None)
        results["buyer_sees_rows"] = ok and len(rows) >= 1

        # 销售方向：只有拿到销售岗位的账号能发；切到"ขาย"后，确认建的是**销售单据**。
        # 通道侧不是用户会话（webhook 走 Administrator），先还原会话再发。
        frappe.set_user("Administrator")
        fake.clear()
        send(line, LINE_USER, "ขาย")
        send(line, LINE_USER, message_id="m4")
        results["purchase_only_refused_sales"] = fake.last_text()
        fake.clear()
        send(line, LINE_SELLER, "ขาย")
        results["direction_reply"] = fake.last_text()
        fake.clear()
        send(line, LINE_SELLER, message_id="m4")
        sales_row = frappe.db.get_value("ERP LINE Intake",
                                        {"line_user_id": LINE_SELLER, "direction": "sales"},
                                        ["name", "direction", "file_hash"], as_dict=True) or {}
        results["sales_row_direction"] = sales_row.get("direction")
        ok, made = as_user(OWNER, intake.confirm, sales_row.get("name"),
                           lines=[{"product": None, "description": "LINE sale",
                                   "qty": 1, "unit_price": "100"}],
                           doc_date="2026-09-21", buyer_name="LINE Buyer",
                           # 销售域已按用户 2026-09-23 的决定去掉税务发票，单据类型只剩
                           # 报价单与收据（见 docs/work/2026-09-23-erp-sales-records.md 的 D1）。
                           doc_kind="receipt")
        results["sales_confirm"] = ok
        results["sales_confirm_error"] = None if ok else made
        sales_doc = made.get("name") if ok else None
        results["sales_source"] = frappe.db.get_value("ERP Sales Document", sales_doc,
                                                      "source") if sales_doc else None
        results["sales_hash_matches"] = (frappe.db.get_value("ERP Sales Document", sales_doc,
                                                             "image_sha256")
                                         == sales_row.get("file_hash")) if sales_doc else None
        results["sales_intake_link"] = frappe.db.get_value(
            "ERP LINE Intake", sales_row.get("name"), "sales_document")
        results["purchase_row_untouched"] = frappe.db.get_value(
            "ERP LINE Intake", row.get("name"), "sales_document")

        results["site_a_intakes"] = frappe.db.count("ERP LINE Intake")
    finally:
        fake.restore()

    frappe.set_user("Administrator")
    frappe.destroy()
    connect(OTHER_SITE)
    results["other_site_intakes"] = frappe.db.count("ERP LINE Intake")

    expected = {
        "image_handled": True,
        "row_status": "received",
        "row_sender": BUYER,
        "row_media": "image",
        "row_direction": "purchase",
        "row_hash_matches": True,
        "row_has_file": True,
        "file_name": "line-m1.jpg",
        "file_is_private": 1,
        "rows_after_duplicate": 1,
        "cashier_rows": 0,
        "stranger_rows": 0,
        "confirm": True,
        "purchase_source": "line",
        "confirm_again_same": True,
        "purchases_for_supplier": 1,
        "discard": True,
        "discard_status": "discarded",
        "confirm_after_discard": False,
        "cashier_can_list": False,
        "buyer_sees_rows": True,
        "other_site_intakes": 0,
    }
    problems = {k: {"expected": v, "actual": results.get(k)} for k, v in expected.items()
                if results.get(k) != v}
    if results.get("purchase_hash") != hashlib.sha256(IMAGE).hexdigest():
        problems["purchase_hash"] = results.get("purchase_hash")
    # Frappe 给重名文件加随机后缀（同一份 PDF 会存多次），这里只认前缀与扩展名。
    pdf_name = str(results.get("pdf_row_file") or "")
    if not pdf_name.startswith("invoice") or not pdf_name.endswith(".pdf"):
        problems["pdf_row_file"] = results.get("pdf_row_file")
    intake_after = results.get("intake_after_confirm") or {}
    if intake_after.get("status") != "confirmed" or not intake_after.get("purchase"):
        problems["intake_after_confirm"] = intake_after
    if not str(results.get("confirm_after_discard_error") or "").startswith("ValidationError"):
        problems["confirm_after_discard_error"] = results.get("confirm_after_discard_error")
    if results.get("received_reply") is None or results.get("stranger_reply") is None:
        problems["replies_missing"] = {"received": results.get("received_reply"),
                                       "stranger": results.get("stranger_reply")}

    print(json.dumps({"site": site, "results": results, "problems": problems},
                     ensure_ascii=False, indent=2, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
