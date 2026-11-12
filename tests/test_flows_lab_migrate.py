"""Offline: the regression lab migrates site by site and stops when a migration loses DocTypes (scripts/flows/run.py)."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("flows_run", ROOT / "scripts/flows/run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


def done(stdout="", code=0):
    return SimpleNamespace(stdout=stdout, stderr="boom" if code else "", returncode=code)


class LabMigrate(unittest.TestCase):
    def lab(self, lose="", fail=""):
        lab = run.Lab()
        calls, migrated = [], set()
        lab.exec = lambda *args, **kw: done("assets\nerp-new.regression.localhost\nerp-old.regression.localhost\n")

        def bench(*args, timeout=0):
            calls.append(args)
            site = args[2] if args[0] == "bench" else args[-1]
            if args[0] == "bench":
                migrated.add(site)
                return done(code=1 if site == fail else 0)
            names = ["DocType", "ERP Product", "ERP Stock Entry"]
            if site == lose and site in migrated:
                names = names[:1]
            return done("Linking fresh assets...\nDOCTYPES " + json.dumps(names) + "\n")
        lab.bench = bench
        return lab, calls

    def test_each_site_is_counted_around_its_own_migration(self):
        lab, calls = self.lab()
        lab.migrate()
        steps = [("migrate " if c[0] == "bench" else "count ") + (c[2] if c[0] == "bench" else c[-1]) for c in calls]
        self.assertEqual(steps, ["count erp-new.regression.localhost", "migrate erp-new.regression.localhost",
                                 "count erp-new.regression.localhost", "count erp-old.regression.localhost",
                                 "migrate erp-old.regression.localhost", "count erp-old.regression.localhost"])

    def test_lost_doctypes_stop_before_the_next_site(self):
        lab, calls = self.lab(lose="erp-new.regression.localhost")
        with self.assertRaises(SystemExit) as stopped:
            lab.migrate()
        self.assertIn("少了 2 个 DocType", str(stopped.exception))
        self.assertIn("ERP Product, ERP Stock Entry", str(stopped.exception))
        self.assertFalse(any("erp-old.regression.localhost" in c for c in calls))

    def test_failed_migration_stops(self):
        lab, calls = self.lab(fail="erp-new.regression.localhost")
        with self.assertRaises(SystemExit) as stopped:
            lab.migrate()
        self.assertIn("迁移失败", str(stopped.exception))

    def test_a_failure_says_the_exit_code_and_what_it_printed(self):
        # 11-12：失败只打了 stderr，stderr 为空时一个字的原因都没有（bench 把错误打在 stdout；被另一个 run.py 重启容器杀掉时两边都空）
        lab = run.Lab()
        lab.exec = lambda *args, **kw: done("erp-new.regression.localhost\n")
        lab.bench = lambda *args, timeout=0: SimpleNamespace(stdout="Traceback ...\nModuleNotFoundError: x", stderr="", returncode=1)
        with self.assertRaises(SystemExit) as stopped:
            lab.migrate()
        self.assertIn("退出码 1", str(stopped.exception))
        self.assertIn("ModuleNotFoundError: x", str(stopped.exception))

    def test_killed_by_a_container_restart_says_so(self):
        lab = run.Lab()
        lab.exec = lambda *args, **kw: done("erp-new.regression.localhost\n")
        lab.bench = lambda *args, timeout=0: SimpleNamespace(stdout="", stderr="", returncode=137)
        with self.assertRaises(SystemExit) as stopped:
            lab.migrate()
        self.assertIn("退出码 137", str(stopped.exception))
        self.assertIn("另一个 run.py", str(stopped.exception))


class LabLock(unittest.TestCase):
    """同一套实验栈同一时刻只让一个 run.py 用：每次都先重启 bench 容器、再迁移，两个同时跑会互相把对方的迁移杀掉（11-12）。"""

    def test_a_second_run_on_the_same_lab_stops_at_once(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            first = run.lab_lock(folder)
            with self.assertRaises(SystemExit) as stopped:
                run.lab_lock(folder)
            self.assertIn("另一个 run.py", str(stopped.exception))
            self.assertIn(str(__import__("os").getpid()), str(stopped.exception))
            first.close()
            run.lab_lock(folder).close()  # 前一个结束（进程退出即放锁）后照常拿得到


if __name__ == "__main__":
    unittest.main()
