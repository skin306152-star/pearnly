"""翻译包生成器（`scripts/i18n-build.py`）：no-alias 登记的键不补英文别名行；仓库里的翻译包与术语表一致。

11-16：改了术语表的写法后旧行留在翻译包里（生成器原先合并保留手工行），靠人工清；手工行积到约 770 行。
"""
from __future__ import annotations

import csv
import importlib.util
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
spec = importlib.util.spec_from_file_location("i18n_build", os.path.join(ROOT, "scripts", "i18n-build.py"))
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

APP = "pearnly_demo"


def item(en, zh, key=""):
    return {"en": en, "zh": zh, "th": zh + "-th", "ja": zh + "-ja", "key": key, "app": APP, "msgid": key or en}


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "apps", APP, APP, "translations"))
        os.makedirs(os.path.join(self.tmp.name, "apps", APP, APP, "i18n"))
        self.old_root = build.ROOT
        build.ROOT = self.tmp.name
        self.addCleanup(setattr, build, "ROOT", self.old_root)

    def pack(self, lang, rows):
        path = os.path.join(self.tmp.name, "apps", APP, APP, "translations", lang + ".csv")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            csv.writer(handle, lineterminator="\n").writerows(rows)

    def no_alias(self, *keys):
        with open(os.path.join(self.tmp.name, "apps", APP, APP, "i18n", "no-alias.txt"), "w", encoding="utf-8") as handle:
            handle.write("# 注释\n" + "".join(key + "\n" for key in keys))

    def test_keyed_rows_get_an_english_alias_unless_registered(self):
        rows = [item("Bank", "银行", key="demo.bank"), item("Count", "盘点", key="demo.count")]
        tables = build.tables({APP: rows})
        self.assertEqual(tables[(APP, "zh")]["Bank"], "银行")               # 照旧补别名行
        self.no_alias("demo.bank")
        tables = build.tables({APP: rows})
        self.assertNotIn("Bank", tables[(APP, "zh")])                       # 登记了就不补
        self.assertEqual(tables[(APP, "zh")]["demo.bank"], "银行")
        self.assertEqual(tables[(APP, "zh")]["Count"], "盘点")

    def test_a_registered_key_must_be_a_keyed_glossary_row(self):
        self.no_alias("demo.gone")
        build_apps = build.glossary_apps
        build.glossary_apps = lambda: (APP,)
        self.addCleanup(setattr, build, "glossary_apps", build_apps)
        problems = build.stale_no_alias({APP: [item("Bank", "银行", key="demo.bank")]})
        self.assertEqual(len(problems), 1)
        self.assertIn("demo.gone", problems[0])

    def test_the_repository_packs_match_the_glossaries(self):
        build.ROOT = self.old_root
        rows, problems = build.read_glossary()
        self.assertEqual(problems, [])
        by_app = {}
        for row in rows:
            by_app.setdefault(row["app"], []).append(row)
        self.assertEqual(build.stale(build.tables(by_app)) + build.stale_no_alias(by_app), [])


if __name__ == "__main__":
    unittest.main()
