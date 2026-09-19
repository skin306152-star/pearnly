#!/usr/bin/env python3
"""Read only the authorized cloud control secrets; pipe straight into local Docker."""
import json
from pathlib import Path
import subprocess

key = str(Path.home() / ".config/pearnly-production/operator")
remote = """import json
from pathlib import Path
runtime=dict(line.split('=',1) for line in Path('/opt/pearnly/secrets/runtime.env').read_text().splitlines() if '=' in line)
print(json.dumps({'identity':json.loads(Path('/opt/pearnly/secrets/control-identity.json').read_text()),'billing_token':runtime['BILLING_MANAGEMENT_SECRET']}))
"""
result = subprocess.run(["ssh", "-i", key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                         "root@194.233.74.40", "python3 -"], input=remote, text=True, capture_output=True, check=True)
payload = {"action": "setup", "args": json.loads(result.stdout)}
result = subprocess.run(["docker", "--context", "colima-pearnly", "exec", "-i",
    "pearnly-sample-control-bench-1", "/home/frappe/frappe-bench/env/bin/python", "-m",
    "pearnly_control.runner_bridge"], input=json.dumps(payload), text=True, capture_output=True)
if result.returncode:
    error = result.stderr
    for secret in (payload["args"]["billing_token"], payload["args"]["identity"]["client_secret"]):
        error = error.replace(secret, "[redacted]")
    raise SystemExit(error[-4000:])
print("Production target connected; secrets transferred without logging")
