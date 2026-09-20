"""Authorized external reads only: login and master contracts, no customer/order writes.

Print aggregate evidence only. Credentials and master/customer values stay private.
"""
import json
import os
import time

import frappe

os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="dms-line-lab.localhost", sites_path=".")
frappe.connect()
frappe.set_user("Administrator")

from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint
from pearnly_dms.intake.service import run_logged_in
from pearnly_dms.masters.company_banks import fetch_payment_bank_masters

try:
    operator = frappe.get_doc("DMS Operator", {"display_name": "E2E DMS operator"})
    endpoint = resolve_dms_endpoint(operator.user, None)
    if not endpoint:
        raise SystemExit("The authorized test operator has no enabled owned connection")
    started = time.monotonic()

    def read(client, adapter):
        masters = client.fetch_masters(strict=True)
        banks = fetch_payment_bank_masters(adapter, client=client)
        return {"ok": True, "master_counts": {key: len(value) for key, value in masters.items()},
                "bank_contract": {key: len(value) if isinstance(value, (list, dict)) else type(value).__name__
                                  for key, value in banks.items()}}

    result = run_logged_in(endpoint, read, authoritative_read=True)
    safe = {key: result[key] for key in ("ok", "error_code", "master_counts", "bank_contract") if key in result}
    safe["elapsed_seconds"] = round(time.monotonic() - started, 2)
    safe["external_business_writes"] = 0
    print(json.dumps(safe, ensure_ascii=False))
    if not result.get("ok"):
        # Details remain in the private Site, not tool output or tracked evidence.
        path = "dms-line-lab.localhost/private/line-lab-probe-error.json"
        with open(path, "w") as handle:
            json.dump(result, handle, ensure_ascii=False)
        os.chmod(path, 0o600)
        raise SystemExit(1)
finally:
    frappe.destroy()
