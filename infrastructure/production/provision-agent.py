#!/usr/bin/env python3
"""Root-owned forced SSH command. Fixed operations; no remote shell or paths."""
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
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


def check_capacity(group, transient_mib=0):
    return run([sys.executable, str(ROOT / "infrastructure/production/capacity.py"),
                "--product", group, "--transient-mib", str(transient_mib)], timeout=30)


def restart_group(group):
    """Restart one product's app processes.

    Why this is not optional after a config write: Frappe caches `site_config.json`
    inside each web process (`frappe.config._cached_get_site_config`, a process-local
    cache with a coarse TTL). Writing a key is therefore **not** visible to a running
    worker right away — measured on this host, a changed secret kept being accepted by
    the tenant webhook for up to about two minutes. For turning a channel off that
    window is a security gap: the credentials are gone from disk while the endpoint
    still verifies the old secret. So every operator-visible config change restarts the
    product here and takes effect immediately.
    """
    # `compose up -d` is a no-op when nothing about the containers changed, which is
    # exactly our case (same image, same volumes) — it left the old processes running
    # and the new configuration unread. `restart` is the operation that actually gives
    # the product fresh processes.
    compose(group, "restart", "-t", "30", *[group + "-" + suffix for suffix in
            ("web", "worker", "ocr-worker", "ocr-dispatch", "scheduler", "socketio", "frontend")])


def site_step(job):
    group = job["payload"]["group"]
    if job["action"] != "drop_site":
        check_capacity(group, transient_mib=1024)
    return run(["docker", "run", "--rm", "-i", "--user", "0", "--memory", "1g", "--memory-swap", "1g", "--network", "pearnly-production_runtime",
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
    target = json.loads((ROOT / "infrastructure/production/target.json").read_text())
    address = str(ipaddress.IPv4Address(target["public_ipv4"]))
    if address not in {x[4][0] for x in socket.getaddrinfo(site, 443, socket.AF_INET)}:
        raise ValueError("Tenant DNS does not point to this server")
    path = Path(f"/etc/nginx/conf.d/pearnly-tenant-{site}.conf")
    cert = Path(f"/etc/letsencrypt/live/{site}/fullchain.pem")
    Path("/var/www/letsencrypt").mkdir(parents=True, exist_ok=True, mode=0o755)
    path.write_text(nginx(site, group, cert.exists()))
    run(["nginx", "-t"])
    run(["systemctl", "reload", "nginx"])
    if not cert.exists():
        run(["certbot", "certonly", "--webroot", "-w", "/var/www/letsencrypt", "--non-interactive",
             "--agree-tos", "--email", "hello@pearnly.com", "--cert-name", site, "-d", site], timeout=300)
    path.write_text(nginx(site, group, True))
    run(["nginx", "-t"])
    run(["systemctl", "reload", "nginx"])
    # `systemctl reload nginx` 一返回只说明信号发出去了，**不代表新配置已经开始服务新连接**。
    # 紧接着做 HTTPS 自检会撞进那个窗口：拿到的握手对不上这张证书（实测报
    # TLSV1_UNRECOGNIZED_NAME），于是这一步被记成失败、整条开通链停在这里 —— 而站点
    # 其实已经完全就绪（2026-09-19 实测：报失败的站点用 curl 访问是 200）。
    # 所以自检要重试；只有一直不通才算真失败。
    last_error = None
    for _ in range(6):
        try:
            with urllib.request.urlopen(f"https://{site}/api/method/ping", timeout=20) as response:
                if json.load(response).get("message") == "pong":
                    return
                last_error = "unexpected response body"
        except Exception as error:  # noqa: BLE001 - 任何传输/证书层面的抖动都值得重试
            last_error = f"{type(error).__name__}: {error}"
        time.sleep(5)
    raise RuntimeError(f"Tenant HTTPS health check failed after retries: {last_error}")


def main():
    if os.environ.get("SSH_ORIGINAL_COMMAND", "provision") != "provision":
        raise ValueError("Only the provisioning protocol is accepted")
    job = json.loads(sys.stdin.read(2_000_001))
    action, p = validate(job)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with Path("/opt/pearnly/state/provision.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        image()
        if action in ("new_site", "restart_web"):
            check_capacity(p["group"], transient_mib=1024 if action == "new_site" else 0)
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
        if action == "restart_web":
            # 新站点**不需要重启**：Frappe 是"按站点"读 `site_config.json` 的，而这个站点
            # 从未被 web 进程加载过，不存在读到旧副本的问题。这里只要保证产品组在跑即可。
            # 曾经这里放的是真重启 —— 那会把**该产品下所有已有租户**一起踢下线，开一个新
            # 租户的代价变成"别人抖一下"（用户 2026-09-19 指出）。
            compose(p["group"], "up", "-d", *[p["group"] + "-" + suffix for suffix in
                    ("web", "worker", "ocr-worker", "ocr-dispatch", "scheduler", "socketio", "frontend")])
        elif action == "drop_site":
            path = Path(f"/etc/nginx/conf.d/pearnly-tenant-{p['site']}.conf")
            path.unlink(missing_ok=True)
            run(["nginx", "-t"])
            run(["systemctl", "reload", "nginx"])
            # Pause this product's queue while its exact Site/database is removed.
            # Other product queues use independent Redis databases.
            compose(p["group"], "stop", "-t", "120", *[p["group"] + "-" + suffix for suffix in ("scheduler", "worker", "ocr-worker", "ocr-dispatch")])
            try:
                site_step(job)
            finally:
                compose(p["group"], "start", *[p["group"] + "-" + suffix for suffix in ("worker", "ocr-worker", "ocr-dispatch", "scheduler")])
            if Path(f"/etc/letsencrypt/live/{p['site']}").exists():
                run(["certbot", "delete", "--non-interactive", "--cert-name", p["site"]])
            manifest.write_text(json.dumps({**owner, "deleted": True}))
        elif action == "publish_site":
            publish(p)
        elif action == "set_line" and not p.get("configured") and not p.get("clear_credentials"):
            return {"status": "skipped"}
        else:
            site_step(job)
            # 重启是**按工单显式要求**的，不是默认动作。
            #
            # 只有"改一个已经在服务的站点"才需要它：Frappe 在 web 进程内缓存
            # `site_config.json`（见 `restart_group` 的说明），运行中的站点会继续按旧副本
            # 应答最长一分钟。而同一轮开通里刚建的站点没被加载过，没有旧副本。
            #
            # 所以控制面只在**运维人员改已有站点的渠道**时带上 `restart`；开通流程不带 ——
            # 否则每开一个租户都要把该产品组的其他租户一起重启一遍。
            if p.get("restart"):
                restart_group(p["group"])
        return {"status": "done"}


if __name__ == "__main__":
    try:
        print(json.dumps(main()))
    except Exception as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        sys.exit(1)
