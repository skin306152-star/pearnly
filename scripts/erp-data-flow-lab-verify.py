"""Exercise the linked ERP flow in an isolated erp-pos laboratory only.

Creates synthetic products, users and business documents in the dedicated lab Site.
No network clients, external orders or messages. Each assertion fails the process;
fixtures stay for inspection and are removed with this task's dedicated lab volumes.
"""
import importlib.util
import json
from pathlib import Path
import sys
import uuid

import frappe

spec = importlib.util.spec_from_file_location("fixtures", Path(__file__).with_name("erp-pos-lab-products-verify.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def main():
    fixtures.connect()
    frappe.set_user("Administrator")
    sys.path.insert(0, fixtures.CONTROL_APP)
    from pearnly_control.features import codes_of, roles_payload
    from pearnly_core.provisioning import ensure_feature_roles
    from pearnly_erp_portal.products import service as products
    from pearnly_erp_portal.purchasing import service as purchase
    from pearnly_erp_portal.inventory import stock, documents, count, report, warehouses, api
    from pearnly_erp_portal.sales import service as sales
    from pearnly_erp_portal.cashier import sales as pos, shifts, terminals

    if not frappe.db.exists("User", fixtures.OWNER):
        fixtures.ensure_member(fixtures.OWNER, [])
    ensure_feature_roles(json.dumps(roles_payload("erp", codes_of("erp"))), admin_user=fixtures.OWNER)
    buyer = "flow-purchase@example.com"
    cashier = "flow-cashier@example.com"
    seller = "flow-sales@example.com"
    fixtures.ensure_member(buyer, ["ERP Purchase"])
    fixtures.ensure_member(cashier, ["ERP Cashier"])
    fixtures.ensure_member(seller, ["ERP Sales"])
    fixtures.ensure_member(fixtures.OUTSIDER, [])
    frappe.set_user(fixtures.OWNER)
    run = "FLOW-" + uuid.uuid4().hex[:10]
    warehouse_name = run + " 自填仓库"
    item = products.create_product(product_name=run, unit="件", unit_price=10, default_cost=6,
                                   default_warehouse=warehouse_name, default_location="A-01")["name"]
    assert products.get_product(item)["default_location"] == "A-01"
    assert products.get_product(item)["default_cost"] == 6

    def line(qty, location=None):
        value = {"product": item, "description": run, "qty": qty, "unit": "件", "unit_price": 10}
        if location is not None:
            value["location"] = location
        return value

    frappe.set_user(buyer)
    assert products.get_product(item)["name"] == item
    assert products.get_product(item)["default_cost"] is None
    from frappe.client import get as native_get
    native = native_get("ERP Product", item)
    assert native.get("default_cost") in (None, 0) and native.get("cost_set") in (None, 0)
    order = purchase.create_purchase(supplier_name="Local test", doc_date="2026-09-22", has_vat="0",
                                     lines=[line(10), line(7, "B-02")])["name"]
    assert purchase.get_purchase(order)["grand_total"] == 170
    purchase.set_status(order, "submitted", receive_stock=True)
    wh = warehouses.resolve(warehouse_name, create=False)
    assert stock.current_balance(item, wh, "A-01").qty == 10
    assert stock.current_balance(item, wh, "B-02").qty == 7
    assert stock.current_balance(item, wh).qty == 0
    assert purchase.get_purchase(order)["stock_posted_at"]
    assert purchase.get_purchase(order)["lines"][1]["product"] == item

    frappe.set_user(fixtures.OWNER)
    entry = documents.create_entry(direction="out", posting_date="2026-09-22", lines=[line(2)])["name"]
    documents.submit_entry(entry)
    assert stock.current_balance(item, wh, "A-01").qty == 8
    products.update_product(item, default_location="C-03")
    documents.void_entry(entry)
    assert stock.current_balance(item, wh, "A-01").qty == 10
    assert stock.current_balance(item, wh, "C-03").qty == 0

    frappe.set_user(seller)
    assert products.get_product(item)["name"] == item
    sold_line = {**line(3, "B-02"), "warehouse": warehouse_name}
    doc = sales.create_document(doc_kind="tax_invoice_simple", doc_date="2026-09-22",
                                 buyer_name="Local customer", lines=[sold_line], has_vat="0")["name"]
    assert sales.get_document(doc)["grand_total"] == 30
    sales.issue_document(doc)
    assert stock.current_balance(item, wh, "B-02").qty == 4
    sales.void_document(doc)
    assert stock.current_balance(item, wh, "B-02").qty == 7

    frappe.set_user(cashier)
    assert products.get_product(item)["name"] == item
    terminal = terminals.create_terminal(terminal_name=run)["name"]
    shift = shifts.open_shift(terminal=terminal)["name"]
    token = uuid.uuid4().hex
    sale = pos.create_sale(lines=[{**line(2, "A-01"), "warehouse": wh}, {**line(3, "B-02"), "warehouse": wh}],
                           payments=[{"method": "cash", "amount": 50}], shift=shift, client_uuid=token, has_vat="0")
    replay = pos.create_sale(lines=[line(99)], payments=[], shift=shift, client_uuid=token, has_vat="0")
    assert replay["name"] == sale["name"] and replay["deduped"]
    assert stock.current_balance(item, wh, "A-01").qty == 8
    assert stock.current_balance(item, wh, "B-02").qty == 4
    original = pos.get_sale(sale["name"])
    refund_token = uuid.uuid4().hex
    refund = pos.refund(sale["name"], lines=[{"original_line": original["lines"][1]["name"], "qty": 1}],
                        payments=[{"method": "cash", "amount": 10}], client_uuid=refund_token)
    assert stock.current_balance(item, wh, "B-02").qty == 5
    assert stock.current_balance(item, wh, "A-01").qty == 8
    assert pos.refund(sale["name"], client_uuid=refund_token)["name"] == refund["name"]
    try:
        pos.refund(sale["name"], lines=[{"original_line": original["lines"][1]["name"], "qty": 3}],
                   payments=[{"method": "cash", "amount": 30}])
    except frappe.ValidationError:
        frappe.db.rollback()
    else:
        raise AssertionError("over-refund accepted")
    pos.void_sale(refund["name"])
    assert stock.current_balance(item, wh, "B-02").qty == 4
    pos.void_sale(sale["name"])
    assert stock.current_balance(item, wh, "A-01").qty == 10
    assert stock.current_balance(item, wh, "B-02").qty == 7

    frappe.set_user(fixtures.OWNER)
    task = count.create_count(title=run, warehouse=warehouse_name)["name"]
    rows = count.list_items(count=task)
    assert {(r["location"], r["book_qty"]) for r in rows} == {("A-01", 10), ("B-02", 7)}
    target = next(r for r in rows if r["location"] == "A-01")
    count.add_entry(count=task, item=target["name"], qty=9, request_id=run)
    count.post_count(task)
    assert stock.current_balance(item, wh, "A-01").qty == 9
    assert stock.current_balance(item, wh, "B-02").qty == 7
    assert stock.rebuild_balance(item, wh, "A-01")["qty_on_hand"] == 9
    card = report.card(product=item, warehouse=wh, location="A-01", date_from="1900-01-01", date_to="2099-01-01")
    assert card["closing"]["qty"] == 9
    assert report.card(product=item, warehouse=wh, date_from="1900-01-01", date_to="2099-01-01")["closing"]["qty"] == 16
    exported = api._csv(stock.stock_overview(warehouse=wh, include_zero=True, limit=0)).decode("utf-8-sig")
    assert "A-01" in exported and "B-02" in exported
    assert {r["location"] for r in count.template_rows(warehouse=warehouse_name)} == {"A-01", "B-02"}

    # Upgrade legacy NULL positions twice: identifiers, quantities and settings survive.
    legacy = products.create_product(product_name=run + " legacy", unit="件")["name"]
    stock.apply_movement(product=legacy, warehouse=wh, movement_type="stock_in", qty=4)
    legacy_name = "BAL-" + legacy + "-" + wh
    frappe.db.sql("update `tabERP Stock Balance` set location=NULL where name=%s", (legacy_name,))
    from pearnly_erp_portal.patches import v1_2_storage_positions
    frappe.set_user("Administrator")
    v1_2_storage_positions.execute()
    v1_2_storage_positions.execute()
    frappe.set_user(fixtures.OWNER)
    assert frappe.db.get_value("ERP Stock Balance", legacy_name, "location") == ""
    assert stock.current_balance(legacy, wh).qty == 4
    assert products.get_product(item)["default_location"] == "C-03"

    frappe.set_user(buyer)
    purchase.set_status(order, "void")
    assert stock.current_balance(item, wh, "A-01").qty == -1
    assert stock.current_balance(item, wh, "B-02").qty == 0
    try:
        products.update_product(item, product_name="Denied")
    except frappe.PermissionError:
        frappe.db.rollback()
    else:
        raise AssertionError("purchase role changed the catalog")
    frappe.set_user(fixtures.OUTSIDER)
    try:
        api.api_receive(lines=[line(1)], warehouse=wh, internal=True)
    except frappe.PermissionError:
        frappe.db.rollback()
    else:
        raise AssertionError("outsider received stock")
    frappe.set_user("Guest")
    try:
        products.get_product(item)
    except frappe.AuthenticationError:
        pass
    else:
        raise AssertionError("guest read catalog")
    frappe.destroy()
    fixtures.connect(fixtures.OTHER_SITE)
    assert not frappe.db.exists("ERP Product", item)
    frappe.destroy()
    print(json.dumps({"result": "PASS", "run": run, "checks": [
        "free-text warehouse", "product defaults", "two independent positions", "purchase-only receipt",
        "stock entry reversal after default edit", "sales issue and void", "POS sale dedupe",
        "partial refund source line", "refund dedupe and over-refund rejection", "refund and sale void",
        "count leaves uncounted position intact", "ledger rebuild and card", "template and export locations",
        "legacy NULL position upgrade repeated", "HTTP boolean strings", "purchase void", "read-only catalog roles", "unprivileged and guest rejection", "tenant isolation"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
