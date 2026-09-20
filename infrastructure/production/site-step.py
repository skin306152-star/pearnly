#!/usr/bin/env python3
"""Private one-shot maintenance container; secrets arrive on stdin, not argv."""
import json
import os
from pathlib import Path
import secrets
import shutil
import sys


def main():
    job = json.load(sys.stdin)
    action, p = job["action"], job["payload"]
    site, group = p["site"], p["group"]
    runtime = dict(line.split("=", 1) for line in Path("/run/secrets/runtime.env").read_text().splitlines() if "=" in line)
    os.setgid(1000)
    os.setuid(1000)
    os.chdir("/home/frappe/frappe-bench/sites")
    common = Path("common_site_config.json")
    values = json.loads(common.read_text()) if common.exists() else {}
    queue_db = {"dms": 1, "erp": 2, "cowork": 3}[group]
    values.update(db_host="mariadb", db_port=3306, redis_cache="redis://redis-cache:6379",
                  redis_queue=f"redis://redis-queue:6379/{queue_db}", redis_socketio=f"redis://redis-queue:6379/{queue_db}",
                  socketio_port=9000, serve_default_site=False,
                  pearnly_ocr_budget_redis="redis://redis-queue:6379/6",
                  workers={**values.get("workers", {}), "ocr": {"timeout": 600}})
    common.write_text(json.dumps(values))
    Path("apps.txt").write_text("\n".join(["frappe", "erpnext", "pearnly_core", "pearnly_dms",
                                        "pearnly_cowork", "pearnly_erp_portal"]) + "\n")
    import frappe
    from frappe.installer import _new_site, install_app, update_site_config
    owner = {key: p[key] for key in ("site", "group", "tenant", "deployment")}
    Path(".pearnly-owners").mkdir(exist_ok=True, mode=0o700)
    marker = Path(".pearnly-owners", site + ".json")
    if Path(site).exists():
        if not marker.exists() or json.loads(marker.read_text()) != owner:
            raise ValueError("Existing Site is not owned by this deployment")
    if action == "drop_site":
        path = Path(site)
        if path.exists():
            # Validated single-site path, exact native database identity, no backup.
            frappe.init(site=site)
            frappe.flags.root_login = "root"
            frappe.flags.root_password = runtime["DB_ROOT_PASSWORD"]
            from frappe.database import drop_user_and_database
            drop_user_and_database(frappe.conf.db_name, frappe.conf.db_user)
            frappe.destroy()
            shutil.rmtree(path)
        marker.unlink(missing_ok=True)
        assert not path.exists()
        print("SITE_STEP_OK")
        return
    if action == "new_site" and not Path(site, "site_config.json").exists():
        marker.write_text(json.dumps(owner))
        marker.chmod(0o600)
        frappe.init(site=site, new_site=True)
        _new_site(db_name=None, site=site, db_type="mariadb", db_host="mariadb",
                  db_root_username="root", db_root_password=runtime["DB_ROOT_PASSWORD"],
                  admin_password=secrets.token_urlsafe(40), mariadb_user_host_login_scope="%")
        frappe.destroy()
    frappe.init(site=site)
    frappe.connect()
    frappe.set_user("Administrator")

    def config(**kwargs):
        for key, value in kwargs.items():
            update_site_config(key, value)
            frappe.conf[key] = value
        frappe.clear_cache()

    if action == "new_site":
        config(host_name="https://" + site, developer_mode=0, maintenance_mode=0,
               pause_scheduler=0, enable_scheduler=True)
    elif action == "install_app":
        apps = [p["app"]]
        if p["app"] == "pearnly_erp_portal":
            apps.insert(0, "erpnext")
        for app in apps:
            if app not in frappe.get_installed_apps():
                install_app(app)
    elif action == "set_oidc":
        assert p["issuer"] == "https://auth.pearnly.com/realms/pearnly-business"
        assert p["client_id"] == site and p["native_account"] is True
        assert p["redirect_uri"] == "https://" + site + "/api/method/pearnly_core.identity.callback"
        assert not any(p.get(key) for key in ("admin_client_secret", "password_check_client_secret"))
        config(pearnly_oidc_issuer=p["issuer"], pearnly_oidc_client_id=p["client_id"],
               pearnly_oidc_client_secret=p["client_secret"], pearnly_oidc_redirect_uri=p["redirect_uri"],
               pearnly_product=group, pearnly_native_account=True)
    elif action == "set_billing":
        assert p["api"] == "http://billing-web:8000" and p["host_header"] == "billing.internal"
        config(pearnly_tenant=p["tenant"], pearnly_billing_api=p["api"],
               pearnly_billing_host_header=p["host_header"], pearnly_billing_token=p["token"])
    elif action == "set_ocr":
        assert Path("/run/secrets/ocr.json").is_file()
        config(pearnly_ocr_backend="vertex", pearnly_ocr_credentials="/run/secrets/ocr.json",
               pearnly_ocr_project="pearnly", pearnly_ocr_location="asia-southeast1")
    elif action == "set_line":
        if p.get("clear_credentials"):
            config(pearnly_line_channel_id="", pearnly_line_channel_secret="",
                   pearnly_line_channel_access_token="", pearnly_line_liff_id="")
        else:
            assert p["configured"] and p["channel_secret"] and p["channel_access_token"]
            config(pearnly_line_channel_id=p["channel_id"], pearnly_line_channel_secret=p["channel_secret"],
                   pearnly_line_channel_access_token=p["channel_access_token"],
                   pearnly_line_liff_id=p.get("liff_id", ""), pearnly_line_product=group)
    elif action == "finalize_site":
        from pearnly_core.provisioning import finalize_site
        finalize_site()
        # site_config.enable_scheduler is not the native scheduler switch.
        # Finalization is an explicit activation step, after apps are installed.
        from frappe.utils.scheduler import enable_scheduler
        enable_scheduler()
    elif action == "bind_user":
        from pearnly_core.provisioning import ensure_local_user
        ensure_local_user(p["email"], p["full_name"], p["subject"], p["deployment"])
    elif action == "set_roles":
        from pearnly_core.provisioning import ensure_feature_roles
        ensure_feature_roles(json.dumps(p), admin_user=p["admin_user"])
    elif action == "set_workspace":
        from pearnly_core.provisioning import ensure_product_workspace
        ensure_product_workspace(json.dumps(p))
    elif action == "verify_site":
        assert frappe.conf.pearnly_tenant == p["tenant"]
        assert frappe.conf.pearnly_product == group
        assert frappe.conf.pearnly_native_account
        assert not frappe.conf.get("pearnly_oidc_admin_client_secret")
        assert frappe.db.get_single_value("System Settings", "disable_user_pass_login")
        from frappe.utils.scheduler import is_scheduler_inactive
        assert not is_scheduler_inactive(verbose=False)
        assert frappe.db.count("Pearnly Identity", {"status": "active"}) > 0
        from pearnly_core.billing_client import wallet
        wallet()
    else:
        raise ValueError("Unsupported Site action")
    frappe.db.commit()
    frappe.destroy()
    print("SITE_STEP_OK")


if __name__ == "__main__":
    main()
