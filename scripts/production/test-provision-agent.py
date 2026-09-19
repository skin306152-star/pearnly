#!/usr/bin/env python3
"""No network or database writes: domain, application and routing contracts."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[2] / "infrastructure/production/provision-agent.py"
spec = importlib.util.spec_from_file_location("provision_agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class Contracts(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
