"""Create isolated local billing Site and test credit through normal review logic.

No production token, provisioning target, real transfer or external email is used.
"""
import json
import os
from pathlib import Path
import subprocess

import frappe

SITE = "dms-line-lab-control.localhost"
sites = Path("sites")
apps = sites / "apps.txt"
installed = apps.read_text().splitlines()
if "pearnly_control" not in installed:
    apps.write_text("\n".join(installed + ["pearnly_control"]) + "\n")
if not (sites / SITE / "site_config.json").exists():
    subprocess.run(["bench", "new-site", SITE, "--db-host", "db", "--db-root-password", "line-lab-local-only",
                    "--admin-password", "line-lab-local-only", "--mariadb-user-host-login-scope", "%"], check=True)
subprocess.run(["bench", "--site", SITE, "install-app", "pearnly_control"], check=True)
subprocess.run(["bench", "--site", SITE, "migrate"], check=True)
os.chdir(sites)
frappe.init(site=SITE, sites_path=".")
frappe.connect()
frappe.set_user("Administrator")
from pearnly_control import billing, billing_api
from pearnly_control.billing_auth import tenant_token

name = frappe.db.get_value("Pearnly Tenant", {"tenant_name": "LINE E2E laboratory"}, "name")
if not name:
    name = frappe.get_doc({"doctype": "Pearnly Tenant", "tenant_name": "LINE E2E laboratory"}).insert().name
root = billing_api.service_token()
configuration = Path(SITE) / "site_config.json"
config = json.loads(configuration.read_text())
config["pearnly_billing_scoped_tokens"] = 1
configuration.write_text(json.dumps(config, indent=2))

note = "Local E2E test credit; no real transfer"
topup = frappe.db.get_value(billing.TOPUP, {"tenant": name, "note": note}, "name")
if not topup:
    # A fresh tenant must not obtain a paid plan or OCR with zero balance.
    plan = next(p for p in billing.plans() if float(p["fee"]) > 0)
    result = billing.subscribe(name, plan["code"])
    assert result["error"] == "insufficient_balance", result
    assert not billing.gate(name, kind="pdf", units=1, tier="A")["allowed"]
    request = frappe.get_doc({"doctype": billing.TOPUP, "tenant": name, "status": "pending",
                              "requested_thb": 1000, "note": note, "payer_name": "Local laboratory"}).insert()
    assert billing.balance_of(name) == 0
    result = billing_api.approve_local(request.name, "Administrator", actual_amount_thb=1000, note=note)
    assert result["new_balance"] == 1000
    try:
        billing_api.approve_local(request.name, "Administrator", actual_amount_thb=1000)
    except frappe.ValidationError:
        frappe.db.rollback()
    else:
        raise AssertionError("A second approval must not credit twice")
    assert billing.balance_of(name) == 1000
    print("PASS zero-balance paid subscription and OCR rejected; pending credit unchanged; approval credited exactly once")

tenant_config = Path("dms-line-lab.localhost/site_config.json")
config = json.loads(tenant_config.read_text())
config.update(pearnly_tenant=name, pearnly_billing_api="http://site:8000",
              pearnly_billing_host_header=SITE, pearnly_billing_token=tenant_token(root, name, "dms"))
tenant_config.write_text(json.dumps(config, indent=2))
frappe.db.commit()
frappe.destroy()
print("Local test billing configured; no production credit or payment changed")
