"""Local Docker maintenance bridge. Not whitelisted or exposed over HTTP."""
import contextlib
import io
import json
import os
import re
import sys


def dispatch(command):
    import frappe
    from pearnly_control import provisioning, teardown
    action = command["action"]
    args = command.get("args", {})
    if action == "next":
        target = frappe.conf.get("pearnly_production_target")
        # A host move must never replay another server's pending jobs.
        if not target or args.get("target") != target:
            return []
        return provisioning.next_steps(limit=1, target=target)
    if action == "report":
        return provisioning.report_step(**args)
    if action == "active":
        return provisioning.mark_deployment_active(**args)
    if action == "purge":
        return teardown.api_purge_control(**args)
    if action == "setup":
        target_name = args.get("target", "")
        if not re.fullmatch(r"production-[a-z0-9-]+", target_name):
            raise ValueError("Invalid production target")
        from frappe.installer import update_site_config
        credentials = args["identity"]
        # Verify both private services before changing the default target.
        import requests
        from pearnly_core.keycloak_admin import AdminClient
        client = AdminClient("http://host.docker.internal:18180", "pearnly-business",
                               credentials["client_id"], credentials["client_secret"])
        client.find_user_id_by_email("connect-check@pearnly.invalid")
        response = requests.post("http://host.docker.internal:18184/api/method/pearnly_control.billing_control.dispatch",
            json={"action": "topups", "payload": {"pending": True}},
            headers={"Host": "billing.internal", "X-Pearnly-Billing-Management": args["billing_token"]}, timeout=20)
        response.raise_for_status()
        target = frappe.get_doc("Pearnly Target", target_name) if frappe.db.exists("Pearnly Target", target_name) else frappe.new_doc("Pearnly Target")
        target.update({"target_name": target_name, "base_url": "https://auth.pearnly.com",
                       "realm": "pearnly-business", "admin_client_id": credentials["client_id"],
                       "admin_client_secret": credentials["client_secret"], "is_default": 1})
        target.save(ignore_permissions=True)
        frappe.db.set_value("Pearnly Target", {"name": ("!=", target_name)}, "is_default", 0)
        runtime = dict(frappe.conf.get("pearnly_target_runtime") or {})
        api_urls = dict(frappe.conf.get("pearnly_target_api_urls") or {})
        retired = args.get("replaces_target")
        if retired and retired != target_name:
            runtime[retired] = {**runtime.get(retired, {}), "executor": "retired"}
            api_urls.pop(retired, None)
        runtime[target_name] = {"executor": "production", "base_domain": "pearnly.com"}
        api_urls[target_name] = "http://host.docker.internal:18180"
        for key, value in {
            "pearnly_production_target": target_name,
            "pearnly_target_api_urls": api_urls,
            "pearnly_target_runtime": runtime,
            "pearnly_billing_remote_url": "http://host.docker.internal:18184",
            "pearnly_billing_management_token": args["billing_token"],
        }.items():
            update_site_config(key, value)
        frappe.db.commit()
        return {"target": target_name}
    raise ValueError("Unknown control bridge action")


def main():
    command = json.load(sys.stdin)
    os.chdir("/home/frappe/frappe-bench/sites")
    import frappe
    with contextlib.redirect_stdout(io.StringIO()):
        frappe.init(site="control.localhost")
        frappe.connect()
        frappe.set_user("Administrator")
        try:
            result = dispatch(command)
        finally:
            frappe.destroy()
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
