"""Real-Site acceptance on synthetic Site B in the immutable candidate container.

Creates uniquely prefixed business fixtures and preserves them for diagnosis.
No external calls or cleanup of existing records. All four modules must complete;
skipped tests are a failure. Optional arguments select qualified tests for diagnosis.
"""
import os
from pathlib import Path
import sys
import unittest

import frappe

ROOT = Path("/home/frappe/frappe-bench/sites")
MODULES = ("test_entry_permissions", "test_entry_company_integrity",
           "test_entry_flow_acceptance", "test_native_boundaries")


def main():
    assert os.environ.get("PEARNLY_ERP_ACCEPTANCE") == "1"
    assert (ROOT / ".erp-acceptance").is_file()
    os.chdir(ROOT)
    frappe.init(site="erp-pos-b.localhost", sites_path=str(ROOT))
    frappe.connect()
    assert frappe.conf.pearnly_erp_acceptance and frappe.conf.db_host == "db"
    assert not frappe.conf.get("pearnly_oidc_issuer"), "Business fixtures must remain local"
    frappe.flags.mute_emails = True
    # Repeated fixture batches exceed Frappe's 60-users/hour import allowance.
    # Only this test process gets a higher creation limit; web and Site config keep
    # production limits. Authorization, document validation and identity stay real.
    frappe.conf.throttle_user_limit = 1000
    frappe.set_user("Administrator")
    names = sys.argv[1:] or ["pearnly_erp_portal.access." + name for name in MODULES]
    if any(not any(name == "pearnly_erp_portal.access." + module or
                   name.startswith("pearnly_erp_portal.access." + module + ".")
                   for module in MODULES) for name in names):
        raise RuntimeError("Only acceptance regression modules may be selected")
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
    frappe.destroy()
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
