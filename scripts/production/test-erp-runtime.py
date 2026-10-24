"""Offline checks for ERP-specific native configuration. No Docker or Site IO."""
import copy
import json
from pathlib import Path
import runpy
import tempfile
import unittest

runtime = runpy.run_path(str(Path(__file__).with_name("configure-erp-runtime.py")))


class ErpRuntime(unittest.TestCase):
    def test_minute_scheduler_and_unrelated_values_kept(self):
        original = {"workers": {"ocr": {"timeout": 600}}, "scheduler_tick_interval": 240, "db_host": "unchanged"}
        before = copy.deepcopy(original)
        result = runtime["configured"](original)
        self.assertEqual(original, before)
        self.assertEqual(result, {**original, "scheduler_tick_interval": 60})
        self.assertEqual(runtime["configured"]({}), {"scheduler_tick_interval": 60})
        self.assertEqual(runtime["configured"](result), result)

    def test_install_is_atomic_idempotent_and_preserves_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "common_site_config.json"
            path.write_text(json.dumps({"workers": {"ocr": {"timeout": 600}}, "sentinel": "keep"}))
            path.chmod(0o640)
            runtime["install"](path)
            first = path.read_bytes()
            runtime["install"](path)
            self.assertEqual(path.read_bytes(), first)
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(json.loads(first)["sentinel"], "keep")
            self.assertEqual(json.loads(first)["scheduler_tick_interval"], 60)
            self.assertEqual(list(Path(folder).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
