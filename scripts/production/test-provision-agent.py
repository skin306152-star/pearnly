#!/usr/bin/env python3
"""No network or database writes: domain, application and routing contracts."""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
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


class ProductImages(unittest.TestCase):
    LEGACY = "ghcr.io/skin306152-star/pearnly/frappe@sha256:" + "a" * 64
    ERP = "ghcr.io/skin306152-star/pearnly/frappe@sha256:" + "b" * 64
    COWORK = "ghcr.io/skin306152-star/pearnly/frappe@sha256:" + "c" * 64

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.patch = patch.object(agent, "ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        self.write("active-image.json", [self.LEGACY])

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value))

    def test_product_pins_do_not_change_another_products_legacy_fallback(self):
        legacy_bytes = (self.root / "active-image.json").read_bytes()
        self.write("active-image-erp.json", [self.ERP])
        self.write("active-image-cowork.json", [self.COWORK])
        for group, expected in (("erp", self.ERP), ("dms", self.LEGACY),
                                ("cowork", self.COWORK), ("erp", self.ERP)):
            self.assertEqual(agent.image(group), expected)
            self.assertEqual(os.environ["PEARNLY_IMAGE"], expected)
        self.assertEqual((self.root / "active-image.json").read_bytes(), legacy_bytes)

    def test_invalid_product_pin_fails_closed_without_using_legacy(self):
        for value in ([], {}, "not a list", [None], [123], ["ghcr.io/other/frappe@sha256:" + "a" * 64],
                      ["ghcr.io/skin306152-star/pearnly/frappe:latest"], [self.ERP + "\n"]):
            with self.subTest(value=value):
                self.write("active-image-erp.json", value)
                with self.assertRaises(ValueError):
                    agent.image("erp")
        (self.root / "active-image-erp.json").write_text("not json")
        with self.assertRaises(ValueError):
            agent.image("erp")
        for group in ("../dms", "billing", "control", ""):
            with self.subTest(group=group), self.assertRaises(ValueError):
                agent.image(group)

    def test_missing_or_invalid_legacy_record_is_rejected(self):
        self.write("active-image.json", [])
        with self.assertRaises(ValueError):
            agent.image("dms")
        (self.root / "active-image.json").unlink()
        with self.assertRaises(FileNotFoundError):
            agent.image("dms")

    def test_maintenance_container_uses_the_requested_product_pin(self):
        self.write("active-image-erp.json", [self.ERP])
        self.write("active-image-cowork.json", [self.COWORK])
        commands = []
        with patch.object(agent, "_inspect", return_value=None), patch.object(agent, "check_capacity"), \
             patch.object(agent, "run", side_effect=lambda args, **kw: commands.append(list(args))):
            for group, expected in (("erp", self.ERP), ("dms", self.LEGACY), ("cowork", self.COWORK)):
                agent.ensure_maintenance(group)
                created = commands[-1]
                self.assertEqual(created[-3], expected)
                self.assertIn("pearnly.image=" + expected, created)
                self.assertIn(f"pearnly-production_sites-{group}:/home/frappe/frappe-bench/sites", created)

    def test_restart_web_selects_group_image_before_compose_even_after_another_group(self):
        self.write("active-image-erp.json", [self.ERP])
        self.write("active-image-cowork.json", [self.COWORK])
        state = self.root / "sites"
        state.mkdir()
        original_open = Path.open
        commands = []

        def local_lock(path, *args, **kwargs):
            if str(path) == "/opt/pearnly/state/provision.lock":
                path = self.root / "provision.lock"
            return original_open(path, *args, **kwargs)

        def record(args, **kwargs):
            commands.append((list(args), os.environ["PEARNLY_IMAGE"]))
            return ""

        with patch.object(agent, "STATE", state), patch.object(agent, "check_capacity"), \
             patch.object(agent, "run", side_effect=record), patch.object(Path, "open", local_lock), \
             patch.dict(os.environ, SSH_ORIGINAL_COMMAND="provision"):
            for group, expected in (("erp", self.ERP), ("dms", self.LEGACY),
                                    ("cowork", self.COWORK), ("erp", self.ERP)):
                payload = {**job_payload()["payload"], "group": group, "site": f"acme-{group}.pearnly.com"}
                (state / (payload["site"] + ".json")).write_text(json.dumps(payload))
                job = {"action": "restart_web", "payload": payload}
                with patch.object(sys, "stdin", io.StringIO(json.dumps(job))):
                    self.assertEqual(agent.main(), {"status": "done"})
                args, actual_image = commands[-1]
                self.assertEqual(actual_image, expected)
                self.assertEqual(args[args.index("--profile") + 1], group)
                expected_containers = [f"{group}-{suffix}" for suffix in
                                       ("web", "worker", "ocr-worker", "ocr-dispatch", "scheduler",
                                        "socketio", "frontend")]
                # DMS additionally runs the manual snapshot synchronization queue.
                if group == "dms":
                    expected_containers.append("dms-sync-worker")
                # Cowork runs reconciliation jobs (long queue) in their own worker pool.
                if group == "cowork":
                    expected_containers.append("cowork-long-worker")
                # 只动本产品的容器：不加 --no-deps 会顺带重建共用的数据库（2026-10-01）。
                self.assertEqual(args[args.index("up") + 1:args.index("up") + 3], ["-d", "--no-deps"])
                self.assertEqual(args[args.index("up") + 3:], expected_containers)


class Contracts(unittest.TestCase):
    def test_product_lanes_follow_every_worker_lifecycle(self):
        for suffixes in (("web", "worker", "scheduler"),
                         ("scheduler", "worker", "ocr-worker", "ocr-dispatch"),
                         ("worker", "ocr-worker", "ocr-dispatch", "scheduler")):
            self.assertIn("dms-sync-worker", agent.services("dms", suffixes))
            self.assertIn("cowork-long-worker", agent.services("cowork", suffixes))
            self.assertNotIn("dms-sync-worker", agent.services("cowork", suffixes))
            self.assertEqual(agent.services("erp", suffixes), ["erp-" + item for item in suffixes])
        self.assertEqual(agent.services("cowork", ("web", "frontend")), ["cowork-web", "cowork-frontend"])
        for group, lane in (("dms", "dms-sync-worker"), ("cowork", "cowork-long-worker")):
            with patch.object(agent, "compose") as compose:
                agent.restart_group(group)
                self.assertIn(lane, compose.call_args.args)

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
