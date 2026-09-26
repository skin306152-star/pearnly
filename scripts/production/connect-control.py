#!/usr/bin/env python3
"""Read only the authorized cloud control secrets; pipe straight into local Docker."""
import json
import subprocess
from target import load_target, ssh_command

remote = """import json
from pathlib import Path
runtime=dict(line.split('=',1) for line in Path('/opt/pearnly/secrets/runtime.env').read_text().splitlines() if '=' in line)
cowork=Path('/opt/pearnly/secrets/control-identity-cowork.json')
print(json.dumps({'identity':json.loads(Path('/opt/pearnly/secrets/control-identity.json').read_text()),'billing_token':runtime['BILLING_MANAGEMENT_SECRET'],
                  'cowork_identity':json.loads(cowork.read_text()) if cowork.exists() else None}))
"""
result = subprocess.run(ssh_command("operator") + ["python3 -"],
                        input=remote, text=True, capture_output=True, check=True)
payload = {"action": "setup", "args": json.loads(result.stdout)}
destination = load_target()
payload["args"]["target"] = destination["target_name"]
payload["args"]["replaces_target"] = destination.get("replaces_target")
result = subprocess.run(["docker", "--context", "colima-pearnly", "exec", "-i",
    "pearnly-sample-control-bench-1", "/home/frappe/frappe-bench/env/bin/python", "-m",
    "pearnly_control.runner_bridge"], input=json.dumps(payload), text=True, capture_output=True)
if result.returncode:
    error = result.stderr
    secrets = [payload["args"]["billing_token"], payload["args"]["identity"]["client_secret"],
               (payload["args"].get("cowork_identity") or {}).get("client_secret")]
    for secret in filter(None, secrets):
        error = error.replace(secret, "[redacted]")
    raise SystemExit(error[-4000:])
print("Production target connected; secrets transferred without logging")
