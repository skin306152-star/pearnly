#!/usr/bin/env python3
"""Run with the existing operator key. A queue agent never receives a shell."""
from pathlib import Path
import re
import sys

public = sys.stdin.read().strip()
if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?", public):
    raise SystemExit("Expected one Ed25519 public key")
path = Path("/root/.ssh/authorized_keys")
lines = path.read_text().splitlines()
key = public.split()[1]
line = 'restrict,command="/opt/pearnly/deploy/infrastructure/production/provision-agent.py" ' + public
lines = [item for item in lines if key not in item.split()]
lines.append(line)
path.write_text("\n".join(lines) + "\n")
path.chmod(0o600)
print("Provisioning key authorized for the fixed protocol only")
