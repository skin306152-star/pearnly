"""Offline checks for DMS-specific native configuration. No Docker or Site IO."""
import copy
import json
from pathlib import Path
import runpy
import tempfile
import unittest

runtime = runpy.run_path(str(Path(__file__).with_name("configure-dms-runtime.py")))


class DmsRuntime(unittest.TestCase):
    def test_native_queue_merge_preserves_scheduler_and_all_unrelated_values(self):
        original = {"workers": {"ocr": {"timeout": 600}, "long": {"timeout": 1800},
                                "dms_sync": {"background_workers": 1}},
                    "scheduler_tick_interval": 240, "db_host": "unchanged", "sentinel": {"a": 1}}
        before = copy.deepcopy(original)
        result = runtime["configured"](original)
        self.assertEqual(original, before)
        self.assertEqual(result["scheduler_tick_interval"], 240)
        self.assertNotIn("scheduler_tick_interval", runtime["configured"]({}))
        self.assertEqual(result["workers"]["dms_sync"], {"background_workers": 1, "timeout": 420})
        for key in ("ocr", "long"):
            self.assertEqual(result["workers"][key], original["workers"][key])
        self.assertEqual(result["sentinel"], original["sentinel"])
        self.assertEqual(result["db_host"], original["db_host"])
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
            self.assertEqual(list(Path(folder).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
