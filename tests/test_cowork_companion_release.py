"""最新发布版本随心跳回复带给小助手（2026-10-09）：只读服务器上的 latest.json，不联网、不定时。"""
from pathlib import Path
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"


def _release():
    spec = importlib.util.spec_from_file_location("companion_release_under_test", Path(__file__).resolve().parents[1] / "apps/pearnly_core/pearnly_core/cc_desktop/release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LatestRelease(unittest.TestCase):
    def setUp(self):
        self.release = _release()
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "latest.json")

    def tearDown(self):
        self.dir.cleanup()

    def write(self, value, stamp):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(value if isinstance(value, str) else json.dumps(value))
        os.utime(self.path, ns=(stamp, stamp))

    def test_reads_what_the_release_script_wrote_and_follows_a_new_release(self):
        self.write({"version": "1.3.22", "url": "/downloads/companion/releases/1.3.22/abc/PearnlyCompanion-Setup.exe?v=abc",
                    "sha256": "x", "size": 1}, 1_000)
        self.assertEqual(self.release.latest(self.path), {
            "version": "1.3.22", "url": "/downloads/companion/releases/1.3.22/abc/PearnlyCompanion-Setup.exe?v=abc"})
        self.write({"version": "1.3.23", "url": "/downloads/companion/releases/1.3.23/def/x.exe"}, 2_000)
        self.assertEqual(self.release.latest(self.path)["version"], "1.3.23")

    def test_missing_or_odd_file_means_nothing_is_sent(self):
        self.assertIsNone(self.release.latest(self.path))
        for stamp, value in enumerate(["{not json", [], {"version": "abc", "url": "/downloads/companion/x"},
                                       {"version": "1.3.22", "url": "https://elsewhere/x.exe"}], start=10):
            self.write(value, stamp * 1_000)
            self.assertIsNone(self.release.latest(self.path), value)

    def test_heartbeat_reply_and_compose_mount_agree(self):
        heartbeat = (APP / "companion/heartbeat.py").read_text()
        self.assertIn("reply['companion_latest'] = latest", heartbeat)
        compose = (ROOT / "infrastructure/production/compose.yml").read_text()
        receiver = (ROOT / "infrastructure/production/receive-companion.py").read_text()
        self.assertIn('ROOT = Path("/var/www/pearnly-downloads/companion")', receiver)
        self.assertIn('latest_file = ROOT / "latest.json"', receiver)
        mount = "/var/www/pearnly-downloads/companion:" + os.path.dirname(self.release.RELEASE_FILE) + ":ro"
        self.assertIn(mount, compose)
        # 小助手认的就是这个字段名
        worker = (ROOT / "companion/src/companion/worker.py").read_text()
        self.assertIn('hb.get("companion_latest")', worker)


if __name__ == "__main__":
    unittest.main()
