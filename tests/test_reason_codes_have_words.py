"""CC 桌面版回报的「[错误码] 原文」：每个码在 Cowork（``intake.error.<码>``）与 ERP（``express.error.<码>``，或单据页
认得的 ``pending.CODES``）都要有四语词条，不然界面上原样显示英文原文（11-09 测试机：中文界面待处理卡片出英文原因）。
不连库、不依赖 Frappe。

码从哪来：CC 桌面版 ``ERR_*`` 常量里的码，加上 ``worker._outcome_for`` 分类表里直接写的码。
MR.ERP 代录（``companion/src/companion/mrerp_operator/``）的码由 MR.ERP 那条线的 ``mrerp.error.*`` / ``intake.error.ERR_*`` 管，这里不重复。
"""
import ast
import importlib.util
import pathlib
import re
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPANION = ROOT / "companion/src/companion"
CODE = re.compile(r"^[A-Z][A-Z0-9_]{3,}$")
LANGS = ("en", "zh", "th", "ja")


def _codes() -> dict:
    found = {}
    for path in COMPANION.rglob("*.py"):
        if "mrerp_operator" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id.startswith("ERR") and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str) and CODE.match(node.value.value)):
                found.setdefault(node.value.value, f"{path.relative_to(ROOT)}:{node.lineno}")
            if isinstance(node, ast.FunctionDef) and node.name == "_outcome_for" and path.name == "worker.py":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Dict):
                        for key in sub.keys:
                            if isinstance(key, ast.Constant) and isinstance(key.value, str) and CODE.match(key.value):
                                found.setdefault(key.value, f"{path.relative_to(ROOT)}:{key.lineno}")
    return found


def _words(app: str, prefix: str) -> dict:
    rows = {}
    for line in (ROOT / f"apps/{app}/{app}/i18n/messages.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        fields = line.split("\t")
        if len(fields) == 5 and fields[4].startswith(prefix):
            rows[fields[4][len(prefix):]] = dict(zip(LANGS, fields[:4]))
    return rows


def _erp_page_codes() -> set:
    tree = ast.parse((ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/express/pending.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CODES" for t in node.targets):
            return {key.value for key in node.value.keys}
    return set()


class ReasonCodesHaveWords(unittest.TestCase):
    def test_codes_are_found(self):
        codes = _codes()
        # 认得出来源：几个已知的码都在（读法坏了这里先红，不会让下面的检查空跑）
        for known in ("ENTRY_NOT_BALANCED", "JOURNAL_BOOK_NOT_FOUND", "ITEM_GROUP_ACCOUNT_MISSING", "PERIOD_LOCKED"):
            self.assertIn(known, codes)
        self.assertGreater(len(codes), 40)

    def test_every_code_has_four_languages_in_cowork(self):
        words = _words("pearnly_cowork", "intake.error.")
        missing = []
        for code, where in sorted(_codes().items()):
            row = words.get(code) or words.get(code.lower())
            if not row or not all(row[lang].strip() for lang in LANGS):
                missing.append(f"intake.error.{code}（{where}）")
        self.assertEqual(missing, [], "Cowork 缺四语词条，界面会原样显示英文")

    def test_every_code_has_four_languages_in_erp(self):
        words, page = _words("pearnly_erp_portal", "express.error."), _erp_page_codes()
        missing = []
        for code, where in sorted(_codes().items()):
            if code in page:
                continue
            row = words.get(code)
            if not row or not all(row[lang].strip() for lang in LANGS):
                missing.append(f"express.error.{code}（{where}）")
        self.assertEqual(missing, [], "ERP 缺四语词条，界面会原样显示英文")


class UnreadAccountSetReason(unittest.TestCase):
    """CC 推送前读不到账套：改记词条 `cowork.masters.failed`；已存成英文句子的旧原因由补丁改记（11-09）。"""

    def test_old_english_reasons_become_the_glossary_key(self):
        app = ROOT / "apps/pearnly_cowork/pearnly_cowork"
        self.assertIn("pearnly_cowork.patches.v0_0_28_unread_account_set_reason", (app / "patches.txt").read_text())
        calls = []
        fake = types.SimpleNamespace(db=types.SimpleNamespace(sql=lambda query, values: calls.append(values)))
        with mock.patch.dict(sys.modules, {"frappe": fake}):
            spec = importlib.util.spec_from_file_location("reason_patch", app / "patches/v0_0_28_unread_account_set_reason.py")
            patch = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(patch)
            patch.execute()
        self.assertEqual(calls, [("cowork.masters.failed", "The computer could not read this account set (%")])
        words = _words("pearnly_cowork", "cowork.masters.")
        self.assertTrue(all(words["failed"][lang].strip() for lang in LANGS))


if __name__ == "__main__":
    unittest.main()
