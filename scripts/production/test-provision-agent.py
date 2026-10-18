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

        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        nginx_dir = patch.object(agent, "NGINX_CONF_DIR", Path(empty.name))
        nginx_dir.start()
        self.addCleanup(nginx_dir.stop)
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

    def run_job(self, action, *, live, restart=False, fail=False):
        """跑一张工单（经 `main`）：站点是否已对外由临时 nginx 目录里有没有它的配置决定；返回记下的 docker 命令。"""
        commands, patches = self.harness()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            conf = root / "nginx"
            conf.mkdir()
            # install_app 要带装哪个 App；set_line 没配渠道时代理直接跳过（skipped），这里给配好的
            extra = {"install_app": {"app": "pearnly_dms"}, "set_line": {"configured": True}}.get(action, {})
            payload = {**job_payload()["payload"], "restart": restart, **extra}
            if live:
                (conf / f"pearnly-tenant-{payload['site']}.conf").write_text("server {}\n")
            (root / (payload["site"] + ".json")).write_text(json.dumps(
                {key: payload[key] for key in ("site", "group", "tenant", "deployment")}))
            original_open = Path.open

            def local_lock(path, *args, **kwargs):
                if str(path) == "/opt/pearnly/state/provision.lock":
                    path = root / "provision.lock"
                return original_open(path, *args, **kwargs)

            def step(args, **kwargs):
                commands.append(list(args))
                if fail and args[:2] == ["docker", "exec"]:
                    raise RuntimeError("Server operation failed; inspect the restricted server log")
                return "SITE_STEP_OK"

            job = {"action": action, "payload": payload}
            with patches[0], patches[1], patches[2], patches[4], \
                 patch.object(agent, "run", side_effect=step), patch.object(agent, "NGINX_CONF_DIR", conf), \
                 patch.object(agent, "STATE", root), patch.object(Path, "open", local_lock), \
                 patch.object(agent, "restart_group") as restart_group, patch.object(agent, "publish") as publish, \
                 patch.object(sys, "stdin", io.StringIO(json.dumps(job))), \
                 patch.dict(os.environ, SSH_ORIGINAL_COMMAND="provision"):
                if fail:
                    with self.assertRaises(RuntimeError):
                        agent.main()
                else:
                    self.assertEqual(agent.main(), {"status": "done"})
            self.assertEqual(restart_group.call_count, 1 if restart and not fail else 0)
            self.assertEqual(publish.call_count, 1 if action == "publish_site" else 0)
        return commands

    def test_a_lone_edit_to_a_live_site_removes_the_container_when_done(self):
        # 站点已对外后的单独改站点工单（改识别引擎重排的 set_ocr、全平台下发的 set_assistant、运维改渠道带 restart），
        # 带不带 restart 做完都收（用户 2026-10-18：10-12 起的 pearnly-maintenance-cowork、当晚又留下 -erp、-dms）
        for action, restart in (("set_ocr", False), ("set_assistant", False), ("set_line", True)):
            commands = self.run_job(action, live=True, restart=restart)
            self.assertIn(["docker", "exec", "-i", self.NAME, agent.BENCH_PYTHON, "/run/site-step.py"], commands, action)
            self.assertEqual(commands[-1], ["docker", "rm", "-f", self.NAME], action)
        # 已对外的站点改动失败也收，不为一次失败留容器
        self.assertEqual(self.run_job("set_ocr", live=True, fail=True)[-1], ["docker", "rm", "-f", self.NAME])

    def test_steps_before_publishing_share_the_container_and_publishing_removes_it(self):
        # 开通流程中途（站点还没对外）：容器留给同一轮后面的步骤复用，失败的也留着等重试
        for action in ("install_app", "set_ocr", "set_assistant", "finalize_site"):
            self.assertNotIn(["docker", "rm", "-f", self.NAME], self.run_job(action, live=False), action)
        self.assertNotIn(["docker", "rm", "-f", self.NAME], self.run_job("set_ocr", live=False, fail=True))
        # 现在的开通流程没有 verify_site：最后一个远端步骤 publish_site 做完就收
        self.assertEqual(self.run_job("publish_site", live=False)[-1], ["docker", "rm", "-f", self.NAME])

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

    def test_real_client_ip_reaches_frappe(self):
        """宿主机只传它自己看到的来源；容器内 nginx 只信任本机与 Docker 网段的 X-Real-IP（2026-10-02 全站都记成 172.18.0.1）。"""
        config = agent.nginx("acme.pearnly.com", "dms", True)
        self.assertIn("proxy_set_header X-Real-IP $remote_addr;", config)
        self.assertIn("proxy_set_header X-Forwarded-For $remote_addr;", config)
        self.assertNotIn("$proxy_add_x_forwarded_for", config)
        template = (path.parents[2] / "infrastructure/production/frappe.conf.template").read_text()
        self.assertIn("set_real_ip_from 172.16.0.0/12;", template)
        self.assertIn("real_ip_header X-Real-IP;", template)
        self.assertNotIn("$proxy_add_x_forwarded_for", template)

    def test_timing_log_records_path_without_query(self):
        """宿主机按请求记耗时：只记 $uri（不含查询参数），格式在租户配置之前单独定义。"""
        config = agent.nginx("acme.pearnly.com", "dms", True)
        self.assertIn("access_log /var/log/nginx/pearnly-timing.log pearnly_timing;", config)
        self.assertIn("$uri", agent.TIMING_FORMAT_TEXT)
        self.assertNotIn("$request_uri", agent.TIMING_FORMAT_TEXT)
        self.assertNotIn("$args", agent.TIMING_FORMAT_TEXT)
        self.assertIn("rt=$request_time urt=$upstream_response_time", agent.TIMING_FORMAT_TEXT)
        self.assertLess(agent.TIMING_FORMAT.name, "pearnly-tenant-")
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "timing.conf"
            agent.ensure_timing_format(target)
            self.assertEqual(target.read_text(), agent.TIMING_FORMAT_TEXT)

    def test_refresh_rewrites_only_live_tenant_configs(self):
        """部署时重写已开通站点的宿主机配置：只改内容不同的，已删除或没证书的不动。"""
        import tempfile
        refresh_spec = importlib.util.spec_from_file_location("refresh", path.parents[2] / "scripts/production/refresh-tenant-nginx.py")
        refresh = importlib.util.module_from_spec(refresh_spec)
        refresh_spec.loader.exec_module(refresh)
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            state, conf, certs = root / "state", root / "conf", root / "certs"
            for d in (state, conf):
                d.mkdir()
            sites = {"old.pearnly.com": ("cowork", False, True, "old"), "same.pearnly.com": ("erp", False, True, None),
                     "gone.pearnly.com": ("dms", True, True, "old"), "nocert.pearnly.com": ("dms", False, False, "old")}
            for site, (group, deleted, cert, content) in sites.items():
                (state / f"{site}.json").write_text(json.dumps({"site": site, "group": group, **({"deleted": True} if deleted else {})}))
                if cert:
                    (certs / site).mkdir(parents=True)
                    (certs / site / "fullchain.pem").write_text("x")
                (conf / f"pearnly-tenant-{site}.conf").write_text(content or refresh.agent.nginx(site, group, True))
            changes = refresh.plan(state, conf, certs)
            self.assertEqual([p.name for p, _ in changes], ["pearnly-tenant-old.pearnly.com.conf"])
            self.assertIn("X-Real-IP $remote_addr", changes[0][1])

    def test_publish_rejects_previous_server_dns_before_writing(self):
        with patch.object(agent, "ROOT", path.parents[2]), \
             patch.object(agent.socket, "getaddrinfo", return_value=[(None, None, None, None, ("194.233.74.40", 443))]), \
             patch.object(Path, "write_text") as write:
            with self.assertRaisesRegex(ValueError, "DNS"):
                agent.publish({"site": "acme.pearnly.com", "group": "dms"})
            write.assert_not_called()

    def test_only_the_step_hint_reaches_the_control_plane(self):
        failed = subprocess.CompletedProcess([], 1, stdout="secret-ish noise\n" + agent.STEP_HINT
                                             + "LINE 后台没开 Use webhook\n", stderr="trace with token")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(agent, "Path", lambda value: Path(tmp) / Path(value).name), \
                patch.object(agent.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "^LINE 后台没开 Use webhook$"):
                agent.run(["docker", "exec"])
            failed.stdout = "no hint here"
            with self.assertRaisesRegex(RuntimeError, "restricted server log"):
                agent.run(["docker", "exec"])


if __name__ == "__main__":
    unittest.main()
