#!/usr/bin/python3
"""Run once as root on the new server; never overwrite existing secrets."""
import os
from pathlib import Path
import secrets

root = Path("/opt/pearnly/secrets")
root.mkdir(mode=0o700, parents=True, exist_ok=True)
path = root / "runtime.env"
if path.exists():
    raise SystemExit("Secrets already exist; kept unchanged")
values = {
    "AUTH_HOST": "auth.pearnly.com",
    "DB_ROOT_PASSWORD": secrets.token_urlsafe(40),
    "KEYCLOAK_DB_PASSWORD": secrets.token_urlsafe(40),
    "KEYCLOAK_ADMIN_PASSWORD": secrets.token_urlsafe(40),
    "BILLING_ROOT_SECRET": secrets.token_urlsafe(40),
    "BILLING_MANAGEMENT_SECRET": secrets.token_urlsafe(40),
    "SITE_ADMIN_PASSWORD": secrets.token_urlsafe(40),
}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as handle:
    handle.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
print("Production secrets created; values not printed")
