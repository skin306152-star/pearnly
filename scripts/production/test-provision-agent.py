#!/usr/bin/env python3
"""No network or database writes: domain, application and routing contracts."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[2] / "infrastructure/production/provision-agent.py"
spec = importlib.util.spec_from_file_location("provision_agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class Contracts(unittest.TestCase):
    def test_forced_ssh_entry_is_executable_and_rejects_invalid_job(self):
        self.assertTrue(os.access(path, os.X_OK), "Forced SSH command must retain its Git executable bit")
        result = subprocess.run([str(path)], input="{}", text=True, capture_output=True,
                                env=dict(os.environ, SSH_ORIGINAL_COMMAND="provision"), timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout),
                         {"status": "failed", "error": "Unsupported action or product"})


    def job(self, **overrides):
        return {"action": "new_site", "payload": {
            "site": "acme-dms.pearnly.com", "group": "dms", "tenant": "T-2026-9999",
            "deployment": "DEP-2026-9999", **overrides}}

    def test_domain_and_owner(self):
        self.assertEqual(agent.validate(self.job())[0], "new_site")
        for site in ["auth.pearnly.com", "work.pearnly.com", "www.pearnly.com", "pearnly.com",
                     "testdms.localhost", "x.y.pearnly.com", "x.pearnly.com;id", "../pearnly.com",
                     "a" * 64 + ".pearnly.com", "123.pearnly.com"]:
            with self.subTest(site=site), self.assertRaises(ValueError):
                agent.validate(self.job(site=site))
        for values in [{"tenant": ""}, {"deployment": "../root"}, {"group": "control"}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                agent.validate(self.job(**values))

    def test_app_allowlist(self):
        job = self.job()
        job["action"] = "install_app"
        for app in ["pearnly_core", "pearnly_dms"]:
            job["payload"]["app"] = app
            agent.validate(job)
        for app in ["pearnly_control", "erpnext", "pearnly_cowork", "x;id"]:
            job["payload"]["app"] = app
            with self.assertRaises(ValueError):
                agent.validate(job)

    def test_exact_routing(self):
        config = agent.nginx("acme.pearnly.com", "dms", True)
        self.assertIn("proxy_set_header Host acme.pearnly.com;", config)
        self.assertIn("proxy_set_header X-Frappe-Site-Name acme.pearnly.com;", config)
        self.assertIn("proxy_pass http://127.0.0.1:8183;", config)
        self.assertNotIn("proxy_set_header Host $host", config)
        self.assertNotIn("8180", config)

    def test_publish_rejects_previous_server_dns_before_writing(self):
        with patch.object(agent, "ROOT", path.parents[2]), \
             patch.object(agent.socket, "getaddrinfo", return_value=[(None, None, None, None, ("194.233.74.40", 443))]), \
             patch.object(Path, "write_text") as write:
            with self.assertRaisesRegex(ValueError, "DNS"):
                agent.publish({"site": "acme.pearnly.com", "group": "dms"})
            write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
