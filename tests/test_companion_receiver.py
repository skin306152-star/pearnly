"""`infrastructure/production/receive-companion.py`：CI 经受限 SSH 发布小助手安装包的唯一入口。

用临时目录代替 /var/www/pearnly-downloads，把"一行清单 + 安装包字节"从标准输入喂进去。
"""
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "infrastructure/production/receive-companion.py"
SHA = "a" * 40


def load(root):
    spec = importlib.util.spec_from_file_location("receive_companion", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = Path(root) / "companion"
    return module


def installer(tag=b""):
    return b"MZ" + b"\0" * 64 + tag


class Stdin:
    def __init__(self, data):
        self.buffer = io.BytesIO(data)


class Receiver(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.module = load(self.directory.name)
        self.root = self.module.ROOT

    def tearDown(self):
        self.directory.cleanup()

    def publish(self, body, *, target="prod", version="1.2.0", source=SHA, manifest=None):
        head = manifest or {"target": target, "version": version, "source_sha": source,
                            "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
                            "publication": "36300000000-1"}
        data = json.dumps(head).encode() + b"\n" + body
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", Stdin(data)), mock.patch.object(sys, "stdout", out):
            self.module.main()
        return json.loads(out.getvalue())

    def test_prod_writes_release_fixed_name_and_latest(self):
        body = installer(b"v120")
        result = self.publish(body)
        release = self.root / "releases/1.2.0" / SHA / "CC-Setup.exe"
        self.assertEqual(release.read_bytes(), body)
        self.assertEqual((self.root / "CC-Setup.exe").read_bytes(), body)
        # 2026-10-12 改名 CC：改名前发出去的固定下载链接也拿到同一份
        self.assertEqual((self.root / "PearnlyCompanion-Setup.exe").read_bytes(), body)
        latest = json.loads((self.root / "latest.json").read_text())
        self.assertEqual(latest["version"], "1.2.0")
        self.assertEqual(latest["url"], f"/downloads/companion/releases/1.2.0/{SHA}/CC-Setup.exe?v={SHA}")
        self.assertEqual(result["sha256"], hashlib.sha256(body).hexdigest())

    def test_staging_changes_nothing_users_see(self):
        result = self.publish(installer(b"staging"), target="staging")
        self.assertIn("/downloads/companion/staging/36300000000-1/", result["url"])
        self.assertFalse((self.root / "latest.json").exists())
        self.assertFalse((self.root / "CC-Setup.exe").exists())
        self.assertFalse((self.root / "PearnlyCompanion-Setup.exe").exists())

    def test_republishing_the_same_bytes_is_fine_different_bytes_are_refused(self):
        self.publish(installer(b"one"))
        self.publish(installer(b"one"))
        with self.assertRaises(SystemExit):
            self.publish(installer(b"two"))

    def test_prod_never_goes_backwards(self):
        self.publish(installer(b"new"), version="1.3.0")
        with self.assertRaises(SystemExit):
            self.publish(installer(b"old"), version="1.2.0", source="b" * 40)
        self.assertEqual(json.loads((self.root / "latest.json").read_text())["version"], "1.3.0")

    def test_bad_input_is_refused_and_nothing_is_published(self):
        body = installer()
        cases = [
            {"target": "prod", "version": "1.2.0", "source_sha": SHA, "sha256": "0" * 64, "size": len(body),
             "publication": "1-1"},                                  # 校验值不符
            {"target": "prod", "version": "../x", "source_sha": SHA,
             "sha256": hashlib.sha256(body).hexdigest(), "size": len(body), "publication": "1-1"},  # 路径注入
            {"target": "prod", "version": "1.2.0", "source_sha": SHA,
             "sha256": hashlib.sha256(body).hexdigest(), "size": len(body) + 5, "publication": "1-1"},  # 长度不对
        ]
        for manifest in cases:
            with self.assertRaises(SystemExit):
                self.publish(body, manifest=manifest)
        with self.assertRaises(SystemExit):
            self.publish(b"PK" + b"\0" * 64)                         # 不是 Windows 程序
        self.assertFalse((self.root / "latest.json").exists())
        self.assertFalse((self.root / "releases").exists())


if __name__ == "__main__":
    unittest.main()
