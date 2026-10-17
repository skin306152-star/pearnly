#!/usr/bin/env python3
"""Emit authorized SMTP fields for piping to bootstrap-auth.py --smtp.

Prefer --control: the configured native Email Account is the current source.
An explicit env file is retained for first-time setup. Output contains a secret.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

if sys.stdout.isatty():
    raise SystemExit("Pipe SMTP configuration directly to the server; do not print credentials")
values = {}
if sys.argv[1:] == ["--control"]:
    # No HTTP endpoint is added; this is an operator-only native read.
    reader = """import json, os, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='control.localhost'); frappe.connect()
try:
    name=frappe.db.get_value('Email Account', {'email_id':'hello@pearnly.com', 'enable_outgoing':1}, 'name')
    if not name: raise ValueError('Official Email Account missing')
    account=frappe.get_doc('Email Account', name)
    if int(account.smtp_port or 0) != 587 or not account.use_tls:
        raise ValueError('SMTP STARTTLS on port 587 required')
    print(json.dumps({'PEARNLY_SMTP_HOST':account.smtp_server, 'PEARNLY_SMTP_PORT':'587',
        'PEARNLY_SMTP_USER':account.login_id if account.login_id_is_different else account.email_id,
        'PEARNLY_SMTP_PASSWORD':account.get_password('password'),
        'PEARNLY_SMTP_FROM':account.email_id, 'PEARNLY_SMTP_FROM_NAME':'Pearnly'}))
finally:
    frappe.destroy()
"""
    # 本机控制面的容器名随样板版本变（2026-10-17 只有 control-web / worker / scheduler，没有 bench）：可用 PEARNLY_CONTROL_CONTAINER 指定。
    container = os.environ.get("PEARNLY_CONTROL_CONTAINER", "pearnly-sample-control-bench-1")
    result = subprocess.run(["docker", "--context", "colima-pearnly", "exec", "-i",
        container, "/home/frappe/frappe-bench/env/bin/python"],
        input=reader, capture_output=True, text=True, check=False)
    if result.returncode:
        raise SystemExit("Cannot read the current official Email Account; credentials suppressed")
    values = json.loads(result.stdout)
else:
    for line in Path(sys.argv[1]).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, raw = line.removeprefix("export ").split("=", 1)
        if key.startswith("PEARNLY_SMTP_"):
            fields = shlex.split(raw, comments=True)
            values[key] = fields[0] if fields else ""
required = ("HOST", "PORT", "USER", "PASSWORD", "FROM")
if any(not values.get("PEARNLY_SMTP_" + key) for key in required):
    raise SystemExit("Existing SMTP settings incomplete")
assert values["PEARNLY_SMTP_PORT"] == "587"
if values["PEARNLY_SMTP_FROM"] != "hello@pearnly.com":
    raise SystemExit("Official sender must be hello@pearnly.com")
json.dump({
    "host": values["PEARNLY_SMTP_HOST"], "port": "587", "ssl": "false", "starttls": "true",
    "auth": "true", "user": values["PEARNLY_SMTP_USER"], "password": values["PEARNLY_SMTP_PASSWORD"],
    "from": values["PEARNLY_SMTP_FROM"], "replyTo": values["PEARNLY_SMTP_FROM"],
    "fromDisplayName": values.get("PEARNLY_SMTP_FROM_NAME", "Pearnly"),
}, sys.stdout)
