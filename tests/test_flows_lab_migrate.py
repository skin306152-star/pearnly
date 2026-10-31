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


if __name__ == "__main__":
    unittest.main()
