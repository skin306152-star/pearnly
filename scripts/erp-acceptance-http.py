"""Real nginx/gunicorn/API/upload acceptance; dedicated synthetic stack only.

Creates task-prefixed fixtures without deleting existing data. No external calls.
Secrets stay in memory; prints only scenario names and document identifiers.
Run inside the acceptance bench after upgrading its immutable application image.
"""
import io
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote
from uuid import uuid4

import frappe
import requests
from PIL import Image

ROOT = Path("/home/frappe/frappe-bench/sites")
# Site A exercises real OIDC; native fixture accounts belong to Site B.
SITE = sys.argv[1] if len(sys.argv) > 1 else "erp-pos-b.localhost"
BASE = "http://erp-frontend:8080"


def main():
    assert os.environ.get("PEARNLY_ERP_ACCEPTANCE") == "1"
    assert SITE in ("erp-pos-a.localhost", "erp-pos-b.localhost")
    other_site = "erp-pos-b.localhost" if SITE == "erp-pos-a.localhost" else "erp-pos-a.localhost"
    assert (ROOT / ".erp-acceptance").read_text().strip() == "pearnly-erp-acceptance"
    os.chdir(ROOT)
    frappe.init(site=SITE, sites_path=str(ROOT))
    frappe.connect()
    assert frappe.conf.pearnly_erp_acceptance and frappe.conf.db_host == "db"
    assert not frappe.conf.get("pearnly_oidc_issuer"), "HTTP synthetic accounts require the local-account Site"
    # Fixture-only creation limit in this bench process, never in web/Site config.
    frappe.conf.throttle_user_limit = 1000
    frappe.set_user("Administrator")
    from frappe.core.doctype.user.user import generate_keys
    from pearnly_erp_portal.access import menu
    from pearnly_erp_portal.access.setup import sync_roles
    from pearnly_erp_portal.masters import company, customers
    from pearnly_erp_portal.products import service as products
    from pearnly_erp_portal.inventory import stock
    from pearnly_erp_portal.team import service as team

    sync_roles()
    prefix = "http" + uuid4().hex[:10]
    a = company.create_company(company_name=prefix + " A", pos_enabled=True)
    b = company.create_company(company_name=prefix + " B", pos_enabled=True)
    company.set_active_company(a["name"])
    product = products.create_product(product_name=prefix + " item", unit="pcs", default_cost=10)["name"]
    customer = customers.create_customer(customer_name=prefix + " customer", party_type="person")["name"]

    def account(suffix, keys):
        email = prefix + suffix + "@example.test"
        team.add_member(username=prefix + suffix, email=email, password="acceptance-local-only",
                        roles=[menu.role(key) for key in keys], companies=[a["name"]])
        secret = generate_keys(email)["api_secret"]
        api_key = frappe.db.get_value("User", email, "api_key")
        company.set_active_company(a["name"], user=email)
        return {"Host": SITE, "Authorization": "token " + api_key + ":" + secret}

    buyer = account("buyer", ["purchase", "sales"])
    inventory = account("stock", ["inventory"])
    operator = account("operator", ["stock-in", "warehouses", "company"])
    from pearnly_erp_portal.inventory import documents
    company.set_active_company(b["name"])
    documents.create_entry(direction="in", posting_date="2026-09-24", warehouse=b["warehouse"],
                           lines=[{"product": product, "qty": 9, "unit_cost": 31}], submit=True)
    foreign_entry = documents.create_entry(direction="out", posting_date="2026-09-24", warehouse=b["warehouse"],
                           lines=[{"product": product, "qty": 1, "unit_cost": 31}], submit=True)["name"]
    foreign_terminal = frappe.db.get_value("ERP POS Terminal", {"company": b["name"]}, "name")
    assert foreign_terminal
    foreign = frappe.db.get_value("ERP Stock Balance", {"product": product, "warehouse": b["warehouse"]}, "name")
    company.set_active_company(a["name"])
    frappe.db.commit()
    checks = []

    def request(path, *, headers=buyer, method="GET", **kwargs):
        return requests.request(method, BASE + path, headers=headers, timeout=120, **kwargs)

    def rpc(method, payload, *, headers=buyer):
        response = request("/api/method/" + method, headers=headers, method="POST", json=payload)
        assert response.status_code == 200, (method, response.status_code, response.text[:400])
        return response.json()["message"]

    def reload():
        frappe.db.rollback()
        frappe.clear_cache()

    def qty():
        reload()
        return stock.current_balance(product, a["warehouse"]).qty

    def native_write_denied(doctype, name, changes):
        reload()
        before = frappe.get_doc(doctype, name).as_dict()
        response = request("/api/resource/" + quote(doctype, safe="") + "/" + quote(name, safe=""),
                           headers=operator, method="PUT", json=changes)
        assert response.status_code == 403, ("native write boundary", doctype, response.status_code)
        reload()
        assert frappe.get_doc(doctype, name).as_dict() == before, ("native write changed document", doctype, name)

    picture = io.BytesIO()
    Image.new("RGB", (37, 53), tuple(bytes.fromhex(uuid4().hex)[:3])).save(picture, "PNG")
    upload = request("/api/method/upload_file", method="POST", data={"is_private": "1"},
                     files={"file": (prefix + ".png", picture.getvalue(), "image/png")})
    assert upload.status_code == 200, (upload.status_code, upload.text[:400])
    evidence = upload.json()["message"]
    assert evidence["is_private"] == 1 and evidence["file_url"].startswith("/private/files/")
    purchase_payload = {"supplier_name": prefix + " supplier", "supplier_tax_id": prefix,
        "supplier_branch_type": "branch", "supplier_branch_no": "0001", "supplier_address": "Acceptance address",
        "company": a["name"], "doc_no": prefix, "doc_date": "2026-09-24", "currency": "THB", "fx_rate": 1,
        "has_vat": 0, "attachments": [{"file": evidence["name"], "kind": "bill"}],
        "lines": [{"product": product, "description": prefix + " item", "qty": 2, "unit_price": 10, "unit": "pcs"}]}
    purchase_method = "pearnly_erp_portal.purchasing.api.api_post_purchase"
    key = uuid4().hex
    purchase = rpc(purchase_method, {"payload": purchase_payload, "request_id": key})
    replay = rpc(purchase_method, {"payload": purchase_payload, "request_id": key})
    assert replay["name"] == purchase["name"] and replay["duplicate"] and qty() == 2
    doc = frappe.get_doc("Purchase Document", purchase["name"])
    assert doc.company == a["name"] and doc.lines[0].warehouse == a["warehouse"]
    assert doc.lines[0].product == product and doc.supplier and doc.supplier_branch_no == "0001"
    assert doc.attachments[0].file == evidence["name"]
    assert frappe.db.get_value("File", evidence["name"], "attached_to_name") == doc.name
    assert request(evidence["file_url"]).status_code == 200
    assert request(evidence["file_url"], headers=inventory).status_code == 403
    assert request(evidence["file_url"], headers={"Host": SITE}).status_code in (401, 403)
    checks.append("real private multipart upload, supplier/product/company/file links and purchase idempotency")

    sale_payload = {"company": a["name"], "doc_kind": "receipt", "buyer_name": prefix + " customer",
        "doc_date": "2026-09-24", "has_vat": 0, "vat_rate": 0, "is_paid": 1,
        "lines": [{"product": product, "description": prefix + " item", "qty": 1, "unit_price": 20}]}
    sale_key = uuid4().hex
    sale_method = "pearnly_erp_portal.sales.api.api_post_sale"
    sale = rpc(sale_method, {"payload": sale_payload, "request_id": sale_key})
    assert rpc(sale_method, {"payload": sale_payload, "request_id": sale_key})["duplicate"] and qty() == 1
    sale_doc = frappe.get_doc("ERP Sales Document", sale["name"])
    assert sale_doc.customer == customer and sale_doc.company == a["name"] and sale_doc.lines[0].warehouse == a["warehouse"]
    checks.append("sale customer link, stock deduction and replay")

    for headers in (buyer, inventory):
        response = request("/api/resource/ERP%20Stock%20Balance/" + quote(foreign), headers=headers)
        assert response.status_code == 403, ("foreign native document", response.status_code)
        response = request("/api/resource/ERP%20Stock%20Balance", headers=headers, params={
            "fields": json.dumps(["name", "warehouse", "qty_on_hand", "unit_cost"]),
            "filters": json.dumps({"warehouse": b["warehouse"]})})
        assert response.status_code == 403 or (response.status_code == 200 and not response.json()["data"])
        response = request("/api/method/frappe.desk.reportview.export_query", headers=headers, params={
            "doctype": "ERP Stock Balance", "file_format_type": "CSV", "fields": json.dumps(["name", "unit_cost"]),
            "filters": json.dumps({"warehouse": b["warehouse"]})})
        assert response.status_code == 403 or (response.status_code == 200 and foreign not in response.text)
    own_stock = request("/api/resource/ERP%20Stock%20Balance", headers=inventory, params={
        "fields": json.dumps(["name", "warehouse", "qty_on_hand"]), "filters": json.dumps({"product": product})})
    assert own_stock.status_code == 200 and len(own_stock.json()["data"]) == 1
    assert own_stock.json()["data"][0]["warehouse"] == a["warehouse"]
    checks.append("native REST document/list/export company boundaries with permitted inventory control")

    # The exact authenticated current-tenant credential must never authenticate to the other Site,
    # even if autoname strings happen to coincide between separate databases.
    for path in ("/api/resource/Purchase%20Document/" + quote(doc.name),
                 "/api/resource/ERP%20Sales%20Document/" + quote(sale_doc.name),
                 "/api/resource/ERP%20Product/" + quote(product),
                 "/api/resource/ERP%20Supplier/" + quote(doc.supplier),
                 "/api/resource/ERP%20Customer/" + quote(sale_doc.customer),
                 evidence["file_url"]):
        response = request(path, headers={**buyer, "Host": other_site})
        assert response.status_code in (401, 403), ("tenant boundary", path, response.status_code)
    checks.append("six exact business/file routes reject identity on the other tenant")

    rpc("pearnly_erp_portal.sales.api.api_cancel_sale", {"name": sale["name"]})
    assert qty() == 2
    rpc("pearnly_erp_portal.purchasing.api.api_cancel_purchase", {"name": purchase["name"]})
    assert qty() == 0
    rpc("pearnly_erp_portal.purchasing.api.api_cancel_purchase", {"name": purchase["name"]})
    assert qty() == 0
    checks.append("HTTP cancellation reverses original warehouse once")

    native_write_denied("ERP Warehouse", b["warehouse"], {"company": a["name"]})
    native_write_denied("ERP POS Terminal", foreign_terminal,
                        {"company": a["name"], "warehouse": a["warehouse"]})
    native_write_denied("ERP Stock Entry", foreign_entry,
                        {"warehouse": a["warehouse"], "direction": "in", "status": "draft"})
    checks.append("native REST PUT rejects foreign warehouse/terminal/entry retargeting and preserves saved records")

    # Create this after the purchase/sale quantity assertions so their expected balances stay exact.
    own_entry = documents.create_entry(direction="in", posting_date="2026-09-24", warehouse=a["warehouse"],
                           lines=[{"product": product, "qty": 3, "unit_cost": 10}], submit=True)["name"]
    own_line = frappe.get_doc("ERP Stock Entry", own_entry).lines[0]
    native_write_denied("ERP Stock Entry", own_entry, {"status": "draft"})
    native_write_denied("ERP Stock Entry", own_entry, {"lines": [{"name": own_line.name,
        "product": product, "warehouse": a["warehouse"], "qty": 99, "unit": "pcs", "unit_cost": 10}]})
    assert qty() == 3
    checks.append("native REST PUT rejects submitted entry status and quantity edits with unchanged stock")

    renamed = prefix + "-forbidden-rename"
    before = frappe.get_doc("ERP Stock Entry", own_entry).as_dict()
    response = request("/api/method/frappe.handler.run_doc_method", headers=operator, method="POST", json={
        "method": "rename", "dt": "ERP Stock Entry", "dn": own_entry,
        "args": {"name": renamed, "validate_rename": False}})
    assert response.status_code == 403, ("native rename boundary", response.status_code)
    reload()
    assert frappe.db.exists("ERP Stock Entry", own_entry)
    assert not frappe.db.exists("ERP Stock Entry", renamed)
    assert frappe.get_doc("ERP Stock Entry", own_entry).as_dict() == before
    assert request("/api/resource/ERP%20Stock%20Entry/" + quote(own_entry, safe=""), headers=operator).status_code == 200
    assert request("/api/resource/ERP%20Stock%20Entry/" + quote(renamed, safe=""), headers=operator).status_code == 404
    assert qty() == 3
    checks.append("native rename(validate_rename=False) returns 403 and HTTP rollback preserves original name and stock")
    frappe.destroy()
    print(json.dumps({"checks": checks, "purchase": purchase["name"], "sale": sale["name"],
                      "result": "PASS"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
