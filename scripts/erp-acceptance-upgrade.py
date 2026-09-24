"""Preserve synthetic legacy data/account grants across an immutable image upgrade.

Run --seed on the fixed production baseline, then --check after candidate migrate.
Dedicated local acceptance only; no external calls or existing-data deletion.
"""
import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

import frappe

ROOT = Path("/home/frappe/frappe-bench/sites")
STATE = ROOT / ".erp-acceptance-upgrade.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("seed", "check"))
    args = parser.parse_args()
    assert os.environ.get("PEARNLY_ERP_ACCEPTANCE") == "1" and (ROOT / ".erp-acceptance").is_file()
    os.chdir(ROOT)
    frappe.init(site="erp-pos-a.localhost", sites_path=str(ROOT))
    frappe.connect()
    assert frappe.conf.pearnly_erp_acceptance
    frappe.set_user("Administrator")
    if args.action == "seed":
        assert not STATE.exists(), "Upgrade fixtures already exist"
        from pearnly_core.provisioning import ensure_feature_roles
        from pearnly_control.features import roles_payload, codes_of
        from pearnly_erp_portal.masters import company
        from pearnly_erp_portal.purchasing import service, suppliers
        from pearnly_erp_portal.products import service as products

        ensure_feature_roles(json.dumps(roles_payload("erp", codes_of("erp"))), admin_user="Administrator")
        prefix = "upgrade" + uuid4().hex[:8]
        target = company.create_company(company_name=prefix + " company", pos_enabled=True)
        company.set_active_company(target["name"])
        product = products.create_product(product_name=prefix + " item", unit="pcs")["name"]
        supplier = suppliers.create_supplier(supplier_name=prefix + " supplier", tax_id=prefix)["name"]
        draft = service.create_purchase(company=target["name"], supplier=supplier,
            supplier_name=prefix + " supplier", doc_date="2026-09-24", doc_no=prefix, has_vat=False,
            lines=[{"product": product, "description": prefix + " item", "qty": 2, "unit_price": 10}])
        user = prefix + "@example.test"
        frappe.get_doc({"doctype": "User", "email": user, "username": prefix,
            "first_name": prefix, "send_welcome_email": 0,
            "roles": [{"role": "Desk User"}, {"role": "ERP Purchase"}]}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "User Permission", "user": user, "allow": "ERP Company",
            "for_value": target["name"], "apply_to_all_doctypes": 0}).insert(ignore_permissions=True)
        company.set_active_company(target["name"], user=user)
        frappe.db.commit()
        state = {"company": target["name"], "warehouse": target["warehouse"], "product": product,
                 "supplier": supplier, "draft": draft["name"], "user": user, "doc_no": prefix}
        STATE.write_text(json.dumps(state, indent=2))
        print("PASS seeded legacy draft, product, supplier, user and bounded company grant")
    else:
        state = json.loads(STATE.read_text())
        from pearnly_erp_portal.access import menu
        from pearnly_erp_portal.masters import company
        doc = frappe.get_doc("Purchase Document", state["draft"])
        assert doc.status == "draft" and doc.company == state["company"]
        assert doc.supplier == state["supplier"] and doc.doc_no == state["doc_no"]
        assert doc.lines[0].product == state["product"] and doc.lines[0].qty == 2
        held = set(frappe.get_roles(state["user"]))
        assert {menu.role("purchase"), menu.role("suppliers")} <= held
        assert "ERP Purchase" not in held and menu.role("sales") not in held
        assert company.allowed_companies(state["user"]) == [state["company"]]
        assert company.active_company(state["user"]) == state["company"]
        print("PASS upgrade preserves legacy draft, IDs, quantities, company defaults and bounded menu grants")
    frappe.destroy()


if __name__ == "__main__":
    main()
