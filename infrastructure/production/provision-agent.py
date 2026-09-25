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
from datetime import datetime, timezone

ROOT = Path("/opt/pearnly/deploy")
STATE = Path("/opt/pearnly/state/sites")
APPS = {"dms": "pearnly_dms", "erp": "pearnly_erp_portal", "cowork": "pearnly_cowork"}
PORTS = {"dms": 8183, "erp": 8182, "cowork": 8181}
RESERVED = {"auth", "www", "work", "api", "admin", "mail", "control", "billing", "cowork", "erp", "dms"}
ACTIONS = {"new_site", "install_app", "set_oidc", "set_billing", "set_ocr", "set_line",
           "finalize_site", "bind_user", "set_roles", "set_workspace", "restart_web", "publish_site",
           "verify_site", "drop_site"}
MAINTENANCE_PREFIX = "pearnly-maintenance-"
MAINTENANCE_MAX_AGE_HOURS = 12
BENCH_PYTHON = "/home/frappe/frappe-bench/env/bin/python"
POD_NETWORK = "pearnly-production_runtime"


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


def image(group):
    if group not in APPS:
        raise ValueError("Unsupported product image")
    # Older deployments have one shared record. A product activation creates its
    # own pin without changing the fallback used by products not yet migrated.
    record = ROOT / f"active-image-{group}.json"
    if not record.exists():
        record = ROOT / "active-image.json"
    images = json.loads(record.read_text())
    value = images[0] if isinstance(images, list) and images else None
    if not isinstance(value, str) or not re.fullmatch(
        r"ghcr.io/skin306152-star/pearnly/frappe@sha256:[a-f0-9]{64}", value
    ):
        raise ValueError("No verified production image")
    os.environ["PEARNLY_IMAGE"] = value
    return value


def compose(group, *args):
    return run(["docker", "compose", "--env-file", "/opt/pearnly/secrets/runtime.env", "-f",
                str(ROOT / "infrastructure/production/compose.yml"), "--profile", group, *args])


def check_capacity(group, transient_mib=0):
    return run([sys.executable, str(ROOT / "infrastructure/production/capacity.py"),
                "--product", group, "--transient-mib", str(transient_mib)], timeout=30)


def services(group, suffixes):
    names = [group + "-" + suffix for suffix in suffixes]
    if group == "dms" and "worker" in suffixes:
        names.append("dms-sync-worker")
    return names


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
    compose(group, "restart", "-t", "30", *services(group,
            ("web", "worker", "ocr-worker", "ocr-dispatch", "scheduler", "socketio", "frontend")))


def site_step(job):
    group = job["payload"]["group"]
    # 拆除时不再做容量准入（旧行为一致：拆除是释放内存的那一步，不该被容量检查挡住）。
    name = ensure_maintenance(group, check=job["action"] != "drop_site")
    output = run(["docker", "exec", "-i", name, BENCH_PYTHON, "/run/site-step.py"], data=json.dumps(job))
    if job["action"] in ("verify_site", "drop_site"):
        remove_maintenance(group)
    return output


def maintenance_name(group):
    """这一步用的维护容器名：一个产品组一个，整轮开通复用。"""
    return MAINTENANCE_PREFIX + group


def _inspect(name, template):
    result = subprocess.run(["docker", "inspect", "--format", template, name],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def _maintenance_age_hours(name):
    started = _inspect(name, "{{.State.StartedAt}}")
    if not started:
        return None
    try:
        when = datetime.strptime(started[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return (datetime.now(timezone.utc).replace(tzinfo=None) - when).total_seconds() / 3600


def ensure_maintenance(group, *, check=True):
    """给这个产品组备一个**可复用**的受限维护容器，返回容器名。

    为什么要复用：每步新起一个容器的代价在本机实测是每个步骤 5 秒以上（2026-09-20
    同一批测量：一步里的 `import frappe` 在全新容器里 10.32 秒、在已有容器里 4.76 秒；
    空转一次 3.55 秒对 0.74 秒），而且每次都要冷读一遍应用树。14 步下来就是一分多钟，
    纯属自己加的开销。

    容器仍然是**同一个受限容器**：同一个已验证镜像 digest、同一份只读密钥、
    同一个 sites 卷、uid 0、内存 1 GiB 上限、只在内部网络里，没有额外权限，
    只跑 `sleep`，每一步仍是独立的 `site-step.py` 进程（逐步回执、逐步失败恢复不变）。
    寿命由三件事限住：镜像 digest 变了就重建、超过 MAINTENANCE_MAX_AGE_HOURS 就重建、
    `verify_site`/`drop_site` 收尾时删除。
    """
    name = maintenance_name(group)
    digest = image(group)
    running = _inspect(name, "{{.State.Running}}")
    label = _inspect(name, '{{index .Config.Labels "pearnly.image"}}')
    age = _maintenance_age_hours(name) if running == "true" else None
    if running == "true" and label == digest and age is not None and age < MAINTENANCE_MAX_AGE_HOURS:
        return name
    if running is not None or label is not None:
        # 旧镜像、已停止或活得太久的容器：先收掉再建，避免两个不同镜像的容器混用。
        remove_maintenance(group)
    # 容量检查跟着容器生命周期走（一次），不再每一步重算 —— 保护不变，重复开销去掉。
    if check:
        check_capacity(group, transient_mib=1024)
    run(["docker", "run", "-d", "--name", name, "--user", "0",
        "--memory", "1g", "--memory-swap", "1g", "--network", POD_NETWORK,
        "--label", "pearnly.image=" + digest, "--label", "pearnly.role=maintenance",
        "--label", "pearnly.group=" + group,
        "-v", f"pearnly-production_sites-{group}:/home/frappe/frappe-bench/sites",
        "-v", f"{ROOT}/infrastructure/production/site-step.py:/run/site-step.py:ro",
        "-v", "/opt/pearnly/secrets/runtime.env:/run/secrets/runtime.env:ro",
        "-v", "/opt/pearnly/secrets/ocr.json:/run/secrets/ocr.json:ro",
        "--entrypoint", "/bin/sh", digest, "-c", "sleep infinity"])
    return name


def remove_maintenance(group):
    """收掉这个产品组的维护容器；没有就什么也不做。"""
    subprocess.run(["docker", "rm", "-f", maintenance_name(group)], capture_output=True)


def nginx(site, group, tls=False):
    acme = "location ^~ /.well-known/acme-challenge/ { root /var/www/letsencrypt; }"
    http = f"server {{ listen 80; server_name {site}; {acme} location / {{ return 301 https://$host$request_uri; }} }}\n"
    if not tls:
        return http
    return http + f"""server {{
    listen 443 ssl http2;
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
        image(p["group"])
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
            compose(p["group"], "up", "-d", *services(p["group"],
                    ("web", "worker", "ocr-worker", "ocr-dispatch", "scheduler", "socketio", "frontend")))
        elif action == "drop_site":
            path = Path(f"/etc/nginx/conf.d/pearnly-tenant-{p['site']}.conf")
            path.unlink(missing_ok=True)
            run(["nginx", "-t"])
            run(["systemctl", "reload", "nginx"])
            # Pause this product's queue while its exact Site/database is removed.
            # Other product queues use independent Redis databases.
            compose(p["group"], "stop", "-t", "120", *services(p["group"], ("scheduler", "worker", "ocr-worker", "ocr-dispatch")))
            try:
                site_step(job)
            finally:
                compose(p["group"], "start", *services(p["group"], ("worker", "ocr-worker", "ocr-dispatch", "scheduler")))
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
