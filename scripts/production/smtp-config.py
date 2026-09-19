#!/usr/bin/env python3
"""Emit ONLY authorized SMTP fields for piping to bootstrap-auth.py --smtp.

Do not run without a pipe: output includes the existing SMTP app password.
"""
import json
from pathlib import Path
import shlex
import sys

if sys.stdout.isatty():
    raise SystemExit("Pipe SMTP configuration directly to the server; do not print credentials")
values = {}
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
