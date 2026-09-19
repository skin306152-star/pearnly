"""Remove only our dedicated local verification Site, never a product Site."""
import json
import os
from pathlib import Path
import subprocess

site = "verify-billing.internal"
root = Path("/home/frappe/frappe-bench")
config = json.loads((root / "sites" / site / "site_config.json").read_text())
assert config["allow_tests"] and config["pearnly_billing_only"]
env = dict(line.split("=", 1) for line in Path("/run/secrets/verify.env").read_text().splitlines() if "=" in line)
os.setgid(1000)
os.setuid(1000)
subprocess.run(["bench", "drop-site", site, "--no-backup", "--force",
                "--db-root-password", env["DB_ROOT_PASSWORD"],
                "--archived-sites-path", str(root / "sites" / "archived-verify")],
               cwd=root, check=True)
print("Dedicated verification database removed; ordinary tenants unchanged")
