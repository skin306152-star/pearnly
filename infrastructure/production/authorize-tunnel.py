#!/usr/bin/python3
"""Install one tunnel-only key read from stdin; no shell/PTY/reverse listener."""
from pathlib import Path
import re
import sys

public_key = sys.stdin.read().strip()
if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+ pearnly-production-tunnel", public_key):
    raise SystemExit("Unexpected key format")
options = ('restrict,port-forwarding,permitopen="127.0.0.1:8180",'
           'permitopen="127.0.0.1:8184",command="/bin/false" ')
path = Path("/root/.ssh/authorized_keys")
existing = path.read_text()
lines = [line for line in existing.splitlines() if public_key.split()[1] not in line]
path.write_text("\n".join(lines) + "\n" + options + public_key + "\n")
path.chmod(0o600)
print("Tunnel-only key installed; existing operator and CI keys preserved")
