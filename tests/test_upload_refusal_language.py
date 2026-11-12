"""上传被服务端拒收时，回给调用方的原因走词条、跟请求语言（主会话 11-12）。

页面选文件时已按服务端清单当场拒收（「文件超过 40 MB」「不支持此文件类型」「文件为空」），但服务端那条路仍有人走：CC 桌面版文件夹
放进对话、工具放进对话、直接调接口。以前 CC 契约 `contracts.upload_name` 抛的是英文原句（`unsupported file type`、`file is empty`），
`sessions._guard` 原样 `frappe.throw`，泰文 / 中文 / 日文用户看到英文。现在契约错误带词条键，`_guard` 经 `_()` 按请求语言翻：
- 不收的类型 → `upload.typeUnsupported`、空文件 → `upload.fileEmpty`（同页面那两句，四语在核心 `messages.tsv`）；
- 太大 → 现成的 `upload.tooLarge`（带上限 MB 数）。
契约本身仍是纯函数（不导入 frappe）：翻译函数由调用方传进 `contracts.refusal_text`。
其余上传口（ERP 原件、Cowork 录入、代录资料区、LINE 收单）原本就走词条，LINE 按发件人语言回。
"""
import csv
import importlib.util
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE = ROOT / "apps/pearnly_core/pearnly_core"
FRONTEND = ROOT / "frontend/assistant/src/i18n.ts"
MB = 1024 * 1024


def _contracts():
    spec = importlib.util.spec_from_file_location("refusal_contracts", CORE / "assistant/contracts.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _terms() -> dict:
    with open(CORE / "i18n/messages.tsv", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE))
    head = rows[0]
    key = head.index("key")
    return {row[key]: dict(zip(head, row)) for row in rows[1:] if len(row) > key and row[key]}


def _page(key: str) -> dict:
    """页面那份同名词条（`frontend/assistant/src/i18n.ts`，按 en / zh / th / ja 四段出现）。"""
    return dict(zip(("en", "zh", "th", "ja"), re.findall(rf'^\s*{key}: "([^"]*)",$', FRONTEND.read_text(encoding="utf-8"), re.M)))


class UploadRefusals(unittest.TestCase):
    def setUp(self):
        self.contracts = _contracts()

    def refusal(self, filename, size):
        with self.assertRaises(self.contracts.ContractError) as caught:
            self.contracts.upload_name(filename, size)
        return caught.exception

    def test_each_refusal_carries_its_term(self):
        self.assertEqual(self.refusal("photo.bmp", 10).key, "upload.typeUnsupported")
        self.assertEqual(self.refusal("noext", 10).key, "upload.typeUnsupported")
        self.assertEqual(self.refusal("empty.pdf", 0).key, "upload.fileEmpty")
        big = self.refusal("big.pdf", self.contracts.UPLOAD_MAX_BYTES + 1)
        self.assertEqual((big.key, big.values), ("upload.tooLarge", (self.contracts.UPLOAD_MAX_BYTES // MB,)))

    def test_the_text_is_the_term_in_the_request_language(self):
        zh = {key: row["zh"] for key, row in _terms().items()}
        translate = lambda key: zh.get(key, key)  # noqa: E731 —— 同 frappe._ 在中文请求里
        text = lambda error: self.contracts.refusal_text(error, translate)  # noqa: E731
        self.assertEqual(text(self.refusal("photo.bmp", 10)), "不支持此文件类型")
        self.assertEqual(text(self.refusal("empty.pdf", 0)), "文件为空")
        self.assertEqual(text(self.refusal("big.pdf", self.contracts.UPLOAD_MAX_BYTES + 1)), "文件超过 40 MB")
        # 不是上传那几句的契约错误（编号格式不对这类，只有接口调用方碰得到）照旧原句
        with self.assertRaises(self.contracts.ContractError) as caught:
            self.contracts.session_id("../x")
        self.assertEqual(text(caught.exception), "invalid session id")

    def test_the_terms_exist_in_four_languages_and_match_the_page(self):
        terms = _terms()
        for key, page_key in (("upload.typeUnsupported", "fileTypeUnsupported"), ("upload.fileEmpty", "fileEmpty")):
            with self.subTest(key):
                row = terms.get(key)
                self.assertIsNotNone(row, f"核心 messages.tsv 没有 {key}")
                self.assertEqual({lang: row[lang] for lang in ("en", "zh", "th", "ja")}, _page(page_key),
                                 "服务端拒收与页面拒收同一句话")
        self.assertIn("upload.tooLarge", terms)

    def test_the_guard_translates_through_the_term(self):
        source = (CORE / "assistant/sessions.py").read_text(encoding="utf-8")
        guard = source[source.index("def _guard("):source.index("def _user(")]
        self.assertIn("frappe.throw(contracts.refusal_text(err, _))", guard)
        self.assertNotIn("frappe.throw(str(err))", guard)
        # 契约仍是纯函数：不导入 frappe
        self.assertNotRegex((CORE / "assistant/contracts.py").read_text(encoding="utf-8"), r"(?m)^\s*(import frappe|from frappe)")


if __name__ == "__main__":
    unittest.main()
