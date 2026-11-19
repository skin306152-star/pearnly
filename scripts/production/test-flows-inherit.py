#!/usr/bin/env python3
"""部署闸「沿用全套」的离线测试（`flows_inherit.py`）：在临时 git 仓里造首父链，假 GitHub 状态。

- 全套之后只改测试 / 文档 / 采样脚本 → 沿用，打印沿用哪个提交和文件清单；
- 改到 apps → 拦；中间夹一个改 apps 的提交（之后又只改测试）→ 拦；改依赖 / 锁文件 → 拦；
- 只跑过受影响流程的、没通过的不算全套，往回接着找；往回找不到全套 → 拦。
"""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("flows_inherit", Path(__file__).with_name("flows_inherit.py"))
inherit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inherit)

FULL = ("success", "all flows; 70 flows; datasets new,old; 70/70 runs passed; 3000s")


class FlowsInherit(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.status = {}

    def git(self, *args):
        return subprocess.run(["git", "-c", "core.quotepath=off", "-C", self.dir.name, *args], check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, *paths, status=None):
        for path in paths:
            target = Path(self.dir.name, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(target.read_text() + "x" if target.exists() else "x")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c", "--allow-empty")
        sha = self.git("rev-parse", "HEAD")
        if status:
            self.status[sha] = status
        return sha

    def decide(self, sha):
        ancestors = self.git("rev-list", "--first-parent", f"{sha}~1").split()
        return inherit.decide(sha, ancestors, self.status.get,
                              lambda base: self.git("diff", "--name-only", f"{base}..{sha}").split())

    def test_only_tests_and_docs_after_a_full_suite_inherit_it(self):
        self.commit("apps/a/x.py")
        full = self.commit("apps/a/y.py", status=FULL)
        head = self.commit("scripts/sample-verify-dms-cards.py", "docs/work/n.md", "tests/test_x.py")
        ok, note = self.decide(head)
        self.assertTrue(ok, note)
        self.assertIn(f"沿用 {full[:8]} 的全套", note)
        self.assertIn("scripts/sample-verify-dms-cards.py", note)

    def test_a_change_under_apps_is_stopped(self):
        self.commit("README.md", status=FULL)
        ok, note = self.decide(self.commit("apps/pearnly_dms/pearnly_dms/x.py", "docs/a.md"))
        self.assertFalse(ok)
        self.assertIn("apps/pearnly_dms/pearnly_dms/x.py", note)

    def test_an_apps_commit_in_between_is_stopped(self):
        self.commit("README.md", status=FULL)
        self.commit("apps/pearnly_erp_portal/x.py")
        ok, note = self.decide(self.commit("tests/test_only.py"))
        self.assertFalse(ok)
        self.assertIn("apps/pearnly_erp_portal/x.py", note)

    def test_dependencies_flows_and_runtime_definitions_need_a_full_suite(self):
        for path in ("requirements-dev.txt", "frontend/package-lock.json", "uv.lock", "infrastructure/compose/a.yml",
                     "tests/flows/registry.toml", "scripts/flows/run.py", "companion/src/x.py", "docker/Dockerfile.erp"):
            with self.subTest(path=path):
                self.assertTrue(inherit.is_product(path))
        for path in ("docs/a.md", "scripts/sample-verify-x.py", "scripts/check-copy.py", "scripts/production/deploy.sh",
                     "tests/test_x.py"):
            with self.subTest(path=path):
                self.assertFalse(inherit.is_product(path))

    def test_partial_or_failed_runs_are_skipped_and_the_older_full_suite_is_used(self):
        full = self.commit("README.md", status=FULL)
        self.commit("docs/a.md", status=("success", "flows erp.products.create; 1 flows; 4/4 runs passed"))
        self.commit("docs/b.md", status=("failure", "all flows; 70 flows; 69/70 runs passed"))
        ok, note = self.decide(self.commit("docs/c.md"))
        self.assertTrue(ok, note)
        self.assertIn(full[:8], note)

    def test_no_full_suite_found_is_stopped(self):
        self.commit("README.md", status=("success", "flows erp.x; 1 flows"))
        ok, note = self.decide(self.commit("docs/a.md"))
        self.assertFalse(ok)
        self.assertIn("找不到全套", note)


if __name__ == "__main__":
    unittest.main()
