"""Run the real provisioning step end to end on a local laboratory host.

Drives `infrastructure/production/site-step.py` exactly as provision-agent does
(job JSON on stdin, `/run/site-step.py` inside the maintenance container) so the
new-tenant path is verified instead of a re-implementation of it. The lab services
are named mariadb / redis-cache / redis-queue to match what the step writes.

Local only: `.localhost` Site, this project's own volumes, no production database,
domain, secret or image. Requires PEARNLY_ERP_POS_PROVISION_LAB=1.
"""
import json
import os
import subprocess
import sys

PROJECT = "pearnly-erp-pos-provision-lab"
COMPOSE = "infrastructure/compose/erp-pos-provision-lab.yml"
CONTEXT = ["docker", "--context", "colima-pearnly"]
MAINTENANCE = f"{PROJECT}-maintenance-1"
PYTHON = "/home/frappe/frappe-bench/env/bin/python"
DB_ROOT_PASSWORD = "erp-pos-provision-lab-only"
SITE = "erp-pos-provision.localhost"
OWNER = {"site": SITE, "group": "erp", "tenant": "T-2026-9001", "deployment": "DEP-2026-9001"}


def sh(args, *, data=None, check=True, capture=True):
    result = subprocess.run(args, input=data, text=True,
                            capture_output=capture, timeout=1800)
    if check and result.returncode:
        raise SystemExit(f"failed: {' '.join(args)}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def compose(*args, **kwargs):
    return sh([*CONTEXT, "compose", "-f", COMPOSE, *args], **kwargs)


def step(action, **payload):
    job = {"action": action, "payload": {**OWNER, **payload}}
    out = sh([*CONTEXT, "exec", "-i", MAINTENANCE, PYTHON, "/run/site-step.py"],
             data=json.dumps(job))
    if "SITE_STEP_OK" not in out:
        raise SystemExit(f"{action}: unexpected output {out!r}")
    return out.strip().splitlines()[-1]


def site_python(code):
    # Frappe resolves `../logs` from the working directory; bench always runs in sites.
    # Read as the site owner (uid 1000), the same identity bench commands use.
    return sh([*CONTEXT, "exec", "-i", "-u", "1000", "-w", "/home/frappe/frappe-bench/sites",
               MAINTENANCE, PYTHON, "-c", code])


def main():
    if os.environ.get("PEARNLY_ERP_POS_PROVISION_LAB") != "1":
        raise SystemExit("Dedicated provisioning laboratory required")
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    compose("up", "-d")
    # The step reads the root password from this file, exactly like production.
    sh([*CONTEXT, "exec", MAINTENANCE, "sh", "-c",
        "mkdir -p /run/secrets && printf 'DB_ROOT_PASSWORD=%s\\n' > /run/secrets/runtime.env"
        % DB_ROOT_PASSWORD])

    sequence = [
        ("new_site", {}),
        ("install_app", {"app": "pearnly_core"}),
        ("install_app", {"app": "pearnly_erp_portal"}),
        ("finalize_site", {}),
    ]
    for action, payload in sequence:
        step(action, **payload)

    installed = site_python(
        "import frappe;frappe.init(site=%r,sites_path='/home/frappe/frappe-bench/sites');"
        "frappe.connect();print(','.join(sorted(frappe.get_installed_apps())))" % SITE
    ).strip()
    apps = [a for a in installed.split(",") if a]

    # The site must still be a working, migratable Site after the change.
    migrate = subprocess.run(
        [*CONTEXT, "exec", "-u", "1000", "-w", "/home/frappe/frappe-bench/sites",
         MAINTENANCE, "bench", "--site", SITE, "migrate"],
        text=True, capture_output=True, timeout=1800)
    config = json.loads(site_python(
        "import json;print(open('/home/frappe/frappe-bench/sites/%s/site_config.json').read())" % SITE))

    problems = {}
    if "erpnext" in apps:
        problems["erpnext_installed"] = apps
    for expected in ("frappe", "pearnly_core", "pearnly_erp_portal"):
        if expected not in apps:
            problems.setdefault("missing_app", []).append(expected)
    if config.get("db_host") != "mariadb":
        problems["db_host"] = config.get("db_host")
    if migrate.returncode:
        problems["migrate"] = migrate.stdout[-400:] + migrate.stderr[-400:]

    print(json.dumps({
        "site": SITE,
        "steps": [name for name, _ in sequence],
        "installed_apps": apps,
        "migrate_exit_code": migrate.returncode,
        "problems": problems,
    }, ensure_ascii=False, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
