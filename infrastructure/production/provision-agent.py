#!/usr/bin/env python3
"""Root-owned forced SSH command. Fixed operations; no remote shell or paths."""
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import urllib.request

ROOT = Path("/opt/pearnly/deploy")
STATE = Path("/opt/pearnly/state/sites")
APPS = {"dms": "pearnly_dms", "erp": "pearnly_erp_portal", "cowork": "pearnly_cowork"}
PORTS = {"dms": 8183, "erp": 8182, "cowork": 8181}
RESERVED = {"auth", "www", "work", "api", "admin", "mail", "control", "billing", "cowork", "erp", "dms"}
ACTIONS = {"new_site", "install_app", "set_oidc", "set_billing", "set_ocr", "set_line",
           "finalize_site", "bind_user", "set_roles", "set_workspace", "restart_web", "publish_site",
           "verify_site", "drop_site"}


def validate(job):
    action, p = job.get("action"), job.get("payload", {})
    if action not in ACTIONS or p.get("group") not in APPS:
        raise ValueError("Unsupported action or product")
    site = p.get("site", "")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.pearnly\.com", site):
        raise ValueError("Invalid tenant domain")
    if site.split(".")[0] in RESERVED or site.split(".")[0].isdigit():
        raise ValueError("Reserved tenant domain")
    for key, pattern in (("tenant", r"T-\d{4}-\d+"), ("deployment", r"DEP-\d{4}-\d+")):
        if not re.fullmatch(pattern, p.get(key, "")):
            raise ValueError("Invalid owner identity")
    if action == "install_app" and p.get("app") not in {"pearnly_core", APPS[p["group"]]}:
        raise ValueError("Unexpected application")
    return action, p


def run(args, *, data=None, timeout=1800):
    result = subprocess.run(args, input=data, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        # Keep sensitive subprocess output out of the control plane and user log.
        log = Path("/opt/pearnly/state/last-provision-error.log")
        log.write_text(result.stdout + "\n" + result.stderr)
        log.chmod(0o600)
        raise RuntimeError("Server operation failed; inspect the restricted server log")
    return result.stdout


def image():
    images = json.loads((ROOT / "active-image.json").read_text())
    value = images[0]
    if not re.fullmatch(r"ghcr.io/skin306152-star/pearnly/frappe@sha256:[a-f0-9]{64}", value):
        raise ValueError("No verified production image")
    os.environ["PEARNLY_IMAGE"] = value
    return value


def compose(group, *args):
    return run(["docker", "compose", "--env-file", "/opt/pearnly/secrets/runtime.env", "-f",
                str(ROOT / "infrastructure/production/compose.yml"), "--profile", group, *args])


def site_step(job):
    group = job["payload"]["group"]
    return run(["docker", "run", "--rm", "-i", "--user", "0", "--network", "pearnly-production_runtime",
        "-v", f"pearnly-production_sites-{group}:/home/frappe/frappe-bench/sites",
        "-v", f"{ROOT}/infrastructure/production/site-step.py:/run/site-step.py:ro",
        "-v", "/opt/pearnly/secrets/runtime.env:/run/secrets/runtime.env:ro",
        "-v", "/opt/pearnly/secrets/ocr.json:/run/secrets/ocr.json:ro",
        "--entrypoint", "/home/frappe/frappe-bench/env/bin/python", image(), "/run/site-step.py"],
        data=json.dumps(job))


def nginx(site, group, tls=False):
    acme = "location ^~ /.well-known/acme-challenge/ { root /var/www/letsencrypt; }"
    http = f"server {{ listen 80; server_name {site}; {acme} location / {{ return 301 https://$host$request_uri; }} }}\n"
    if not tls:
        return http
    return http + f"""server {{
    listen 443 ssl;
    server_name {site};
    ssl_certificate /etc/letsencrypt/live/{site}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{site}/privkey.pem;
    client_max_body_size 30m;
    location / {{
        proxy_pass http://127.0.0.1:{PORTS[group]};
        proxy_set_header Host {site};
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Frappe-Site-Name {site};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 120s;
    }}
}}
"""


def publish(p):
    site, group = p["site"], p["group"]
    if "194.233.74.40" not in {x[4][0] for x in socket.getaddrinfo(site, 443, socket.AF_INET)}:
        raise ValueError("Tenant DNS does not point to this server")
    path = Path(f"/etc/nginx/conf.d/pearnly-tenant-{site}.conf")
    cert = Path(f"/etc/letsencrypt/live/{site}/fullchain.pem")
    path.write_text(nginx(site, group, cert.exists()))
    run(["nginx", "-t"])
    run(["systemctl", "reload", "nginx"])
    if not cert.exists():
        run(["certbot", "certonly", "--webroot", "-w", "/var/www/letsencrypt", "--non-interactive",
             "--agree-tos", "--email", "skinzihao@gmail.com", "--cert-name", site, "-d", site], timeout=300)
    path.write_text(nginx(site, group, True))
    run(["nginx", "-t"])
    run(["systemctl", "reload", "nginx"])
    with urllib.request.urlopen(f"https://{site}/api/method/ping", timeout=20) as response:
        if json.load(response).get("message") != "pong":
            raise RuntimeError("Tenant HTTPS health check failed")


def main():
    if os.environ.get("SSH_ORIGINAL_COMMAND", "provision") != "provision":
        raise ValueError("Only the provisioning protocol is accepted")
    job = json.loads(sys.stdin.read(2_000_001))
    action, p = validate(job)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with Path("/opt/pearnly/state/provision.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = STATE / (p["site"] + ".json")
        owner = {key: p[key] for key in ("site", "group", "tenant", "deployment")}
        if manifest.exists():
            recorded = json.loads(manifest.read_text())
            deleted = recorded.pop("deleted", False)
            if deleted and action == "new_site":
                manifest.write_text(json.dumps(owner))
            elif recorded != owner:
                raise ValueError("Tenant ownership mismatch")
            elif deleted and action == "drop_site":
                return {"status": "done"}
        elif action != "new_site":
            raise ValueError("Tenant has not been reserved by this control plane")
        else:
            # Reserve before creation; retry may only resume the same deployment.
            manifest.write_text(json.dumps(owner))
            manifest.chmod(0o600)
        image()
        if action == "restart_web":
            compose(p["group"], "up", "-d", *[p["group"] + "-" + suffix for suffix in
                    ("web", "worker", "scheduler", "socketio", "frontend")])
        elif action == "drop_site":
            path = Path(f"/etc/nginx/conf.d/pearnly-tenant-{p['site']}.conf")
            path.unlink(missing_ok=True)
            run(["nginx", "-t"])
            run(["systemctl", "reload", "nginx"])
            # Pause this product's queue while its exact Site/database is removed.
            # Other product queues use independent Redis databases.
            compose(p["group"], "stop", "-t", "120", p["group"] + "-scheduler", p["group"] + "-worker")
            try:
                site_step(job)
            finally:
                compose(p["group"], "start", p["group"] + "-worker", p["group"] + "-scheduler")
            if Path(f"/etc/letsencrypt/live/{p['site']}").exists():
                run(["certbot", "delete", "--non-interactive", "--cert-name", p["site"]])
            manifest.write_text(json.dumps({**owner, "deleted": True}))
        elif action == "publish_site":
            publish(p)
        elif action == "set_line" and not p.get("configured"):
            return {"status": "skipped"}
        else:
            site_step(job)
        return {"status": "done"}


if __name__ == "__main__":
    try:
        print(json.dumps(main()))
    except Exception as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        sys.exit(1)
