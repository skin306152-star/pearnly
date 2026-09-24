#!/usr/bin/env python3
"""No network or database writes: domain, application and routing contracts."""
import importlib.util
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[2] / "infrastructure/production/provision-agent.py"
spec = importlib.util.spec_from_file_location("provision_agent", path)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


def job_payload():
    return {"action": "new_site", "payload": {"site": "acme-dms.pearnly.com", "group": "dms",
            "tenant": "T-2026-9999", "deployment": "DEP-2026-9999"}}


class MaintenanceContainer(unittest.TestCase):
    """Provisioning steps share one restricted maintenance container instead of one per step."""

    DIGEST = "ghcr.io/skin306152-star/pearnly/frappe@sha256:" + "a" * 64
    NAME = "pearnly-maintenance-dms"

    def harness(self, running=None, label=None, started=None):
        commands = []
        state = {"running": running, "label": label, "started": started}

        def inspect(name, template):
            templates = {"{{.State.Running}}": state["running"],
                         '{{index .Config.Labels "pearnly.image"}}': state["label"],
                         "{{.State.StartedAt}}": state["started"]}
            self.assertIn(template, templates, "unexpected docker inspect template")
            return templates[template]

        def record(args, **kwargs):
            commands.append(list(args))
            return subprocess.CompletedProcess(args, 0, "", "")

        return commands, [
            patch.object(agent, "image", return_value=self.DIGEST),
            patch.object(agent, "_inspect", side_effect=inspect),
            patch.object(agent, "check_capacity"),
            patch.object(agent, "run", side_effect=lambda args, **kw: (commands.append(list(args)), "SITE_STEP_OK")[1]),
            patch.object(agent.subprocess, "run", side_effect=record),
        ]

    def test_first_step_builds_one_restricted_reusable_container(self):
        commands, patches = self.harness()
        with patches[0], patches[1], patches[2] as capacity, patches[3], patches[4]:
            self.assertEqual(agent.ensure_maintenance("dms"), self.NAME)
        created = commands[0]
        self.assertEqual(created[:5], ["docker", "run", "-d", "--name", self.NAME])
        self.assertEqual(created[5], "--user")
        for flag in ("0", "--memory", "1g", "--memory-swap", "1g", "--network", agent.POD_NETWORK,
                     "pearnly.image=" + self.DIGEST, "pearnly.role=maintenance", "pearnly.group=dms"):
            self.assertIn(flag, created)
        for mount in ("pearnly-production_sites-dms:/home/frappe/frappe-bench/sites",
                      "site-step.py:/run/site-step.py:ro", "runtime.env:/run/secrets/runtime.env:ro",
                      "ocr.json:/run/secrets/ocr.json:ro"):
            self.assertTrue(any(str(item).endswith(mount) for item in created), mount)
        self.assertEqual(created[-5:], ["--entrypoint", "/bin/sh", self.DIGEST, "-c", "sleep infinity"])
        self.assertEqual(capacity.call_count, 1, "capacity admission belongs to the container, not to each step")

    def test_live_container_with_the_current_image_is_reused(self):
        started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        commands, patches = self.harness(running="true", label=self.DIGEST, started=started)
        with patches[0], patches[1], patches[2] as capacity, patches[3], patches[4]:
            self.assertEqual(agent.ensure_maintenance("dms"), self.NAME)
        self.assertEqual(commands, [], "reuse must not create, remove or re-check anything")
        self.assertEqual(capacity.call_count, 0)

    def test_stopped_stale_or_expired_container_is_replaced(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=agent.MAINTENANCE_MAX_AGE_HOURS + 1)
               ).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        for running, label, started, why in [("false", self.DIGEST, None, "stopped"),
                                             ("true", "ghcr.io/other@sha256:" + "b" * 64, None, "different image"),
                                             ("true", self.DIGEST, old, "expired")]:
            with self.subTest(why=why):
                commands, patches = self.harness(running=running, label=label, started=started)
                with patches[0], patches[1], patches[2], patches[3], patches[4]:
                    agent.ensure_maintenance("dms")
                self.assertIn(["docker", "rm", "-f", self.NAME], commands)
                self.assertTrue(any(command[:4] == ["docker", "run", "-d", "--name"] for command in commands))

    def test_steps_exec_inside_and_the_finished_deployment_cleans_up(self):
        commands, patches = self.harness()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            agent.site_step({**job_payload(), "action": "finalize_site"})
            self.assertEqual(commands[1][:4], ["docker", "exec", "-i", self.NAME])
            self.assertEqual(commands[1][-1], "/run/site-step.py")
            self.assertNotIn(["docker", "rm", "-f", self.NAME], commands)
            agent.site_step({**job_payload(), "action": "verify_site"})
        self.assertEqual(commands[-1], ["docker", "rm", "-f", self.NAME])

    def test_teardown_is_not_blocked_by_capacity_admission(self):
        commands, patches = self.harness()
        with patches[0], patches[1], patches[2] as capacity, patches[3], patches[4]:
            agent.site_step({**job_payload(), "action": "drop_site"})
        self.assertEqual(capacity.call_count, 0, "teardown frees memory; it must not be gated on admission")


class Contracts(unittest.TestCase):
    def test_sync_lane_is_in_every_dms_worker_lifecycle_and_not_other_products(self):
        for suffixes in (("web", "worker", "scheduler"),
                         ("scheduler", "worker", "ocr-worker", "ocr-dispatch"),
                         ("worker", "ocr-worker", "ocr-dispatch", "scheduler")):
            self.assertIn("dms-sync-worker", agent.services("dms", suffixes))
            for group in ("erp", "cowork"):
                self.assertEqual(agent.services(group, suffixes), [group + "-" + item for item in suffixes])
        with patch.object(agent, "compose") as compose:
            agent.restart_group("dms")
            self.assertIn("dms-sync-worker", compose.call_args.args)

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
