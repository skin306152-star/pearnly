"""MR.ERP 必填覆盖（照 ``tests/test_express_required_coverage.py``）。

- 登记表 ``mrerp/contracts/required_fields.py`` 自身合规：每列 auto / ask / review 至少一个、来源与题类认得、默认带依据、同一键里列不重复；
- **写了没登记就红**：按语法树取出每个导入表生成器（``WRITERS``）的表头列表，第 i 张表第 j 列都要在对应的键里有
  ``field == "i.<列字母>"`` 的行、``label`` 与表头原文相同；登记了但生成器里没有的列也红；
- CC 桌面版 MR.ERP 代录草稿的键（``recipe.DRAFT_KEYS``，按语法树读、不导入 companion）逐个登记到它落的列，或写明落不到；
- 登记表是纯契约：只导入 ``__future__`` / ``typing`` 与 Express 登记表（复用同一组来源取值）。

    python3 tests/test_mrerp_required_coverage.py
"""
import ast
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.express_rules.contracts import required_fields as express_rf  # noqa: E402
from pearnly_core.mrerp.contracts import required_fields as rf  # noqa: E402
from pearnly_core.write_precheck import contracts as wc  # noqa: E402

MODULE = ROOT / "apps/pearnly_core/pearnly_core/mrerp/contracts/required_fields.py"
RECIPE = ROOT / "companion/src/companion/mrerp_operator/recipe.py"
HEADER_LIST = re.compile(r"^_?HEADERS(?:_(\d+))?$")


def letter(n: int) -> str:
    out = ""
    while n > 0:
        n, rest = divmod(n - 1, 26)
        out = chr(65 + rest) + out
    return out


def header_columns(path: Path) -> dict:
    """一个生成器写的全部列：{"<表序号>.<列字母>": 表头原文}。表序号取表头列表名末尾的数字，没有数字的是第 1 张。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    sheets = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        match = HEADER_LIST.match(node.targets[0].id)
        if not match:
            continue
        sheet = int(match.group(1) or 1)
        if sheet in sheets:
            raise AssertionError(f"{path}: sheet {sheet} has two header lists")
        if not isinstance(node.value, ast.List):
            raise AssertionError(f"{path}: {node.targets[0].id} is not a list literal")
        labels = []
        for element in node.value.elts:
            if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
                raise AssertionError(f"{path}: {node.targets[0].id} has a non-string header")
            labels.append(element.value)
        sheets[sheet] = labels
    return {f"{sheet}.{letter(i)}": label for sheet, labels in sheets.items() for i, label in enumerate(labels, 1)}


def draft_keys() -> set:
    """CC 桌面版 ``recipe.DRAFT_KEYS``（按语法树读，不导入 companion 包）。"""
    tree = ast.parse(RECIPE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "DRAFT_KEYS":
            return set(ast.literal_eval(node.value))
    raise AssertionError("recipe.py has no DRAFT_KEYS")


def unregistered(columns: dict, registered: dict) -> list:
    """生成器有、登记表没有或表头对不上的列。``registered``：{列: label}。"""
    out = []
    for field, label in sorted(columns.items()):
        if field not in registered:
            out.append(f"{field} {label!r} not registered")
        elif registered[field] != label:
            out.append(f"{field} label {registered[field]!r} != header {label!r}")
    return out


class RegistryShape(unittest.TestCase):
    def test_registry_is_sound(self):
        self.assertEqual(rf.problems(wc.KINDS), [])

    def test_required_keys_present(self):
        self.assertLessEqual({"mrerp:PURCHASE", "mrerp:SALES_CREDIT", "mrerp:SALES_CASH", "mrerp:APMAS", "mrerp:ARMAS",
                              "mrerp:STKMAS"}, set(rf.keys()))

    def test_literal_structure_for_acceptance(self):
        """验收按语法树读：顶层 REQUIRED_FIELDS 是字典字面量，键、每行的 field / label / ask 都是字符串字面量。"""
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "REQUIRED_FIELDS")
        self.assertIsInstance(node.value, ast.Dict)
        for key, value in zip(node.value.keys, node.value.values):
            self.assertIsInstance(key, ast.Constant)
            self.assertRegex(key.value, r"^mrerp:[A-Z_]+$")
            self.assertIsInstance(value, ast.List)
            for row in value.elts:
                self.assertIsInstance(row, ast.Dict)
                fields = {k.value: v for k, v in zip(row.keys, row.values)}
                for name in ("field", "label"):
                    self.assertIn(name, fields, key.value)
                    self.assertIsInstance(fields[name], ast.Constant, key.value)
                    self.assertIsInstance(fields[name].value, str)
                self.assertRegex(fields["field"].value, r"^\d+\.[A-Z]{1,3}$")
                if "ask" in fields:
                    self.assertIsInstance(fields["ask"], ast.Constant, key.value)
                    self.assertIsInstance(fields["ask"].value, str)

    def test_sources_are_the_express_set(self):
        self.assertEqual(rf.AUTO_SOURCES, express_rf.AUTO_SOURCES)

    def test_problems_catch_a_bad_row(self):
        """problems 本身有效：重复列、没来源、来源不认得、题类不认得、默认没依据、代录指向不存在的列都报出来。"""
        bad = {"mrerp:X": [{"field": "1.A", "label": "a", "auto": ["nowhere"], "ask": "nope", "default": {"value": "", "basis": "x"}},
                           {"field": "1.A", "label": "a"}]}
        saved = (rf.REQUIRED_FIELDS, rf.OPERATOR_KEYS)
        try:
            rf.REQUIRED_FIELDS, rf.OPERATOR_KEYS = bad, {"docnum": [["mrerp:X", "9.Z"]]}
            found = " | ".join(rf.problems(wc.KINDS))
        finally:
            rf.REQUIRED_FIELDS, rf.OPERATOR_KEYS = saved
        for text in ("registered twice", "none of auto", "unknown sources", "not a guide-card kind", "without a basis",
                     "9.Z not registered"):
            self.assertIn(text, found)

    def test_pure_contract(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                imported.add(node.module)
        self.assertLessEqual(imported, {"__future__", "typing"})


class WrittenColumnsRegistered(unittest.TestCase):
    def test_writer_modules_exist(self):
        for path in rf.WRITERS:
            self.assertTrue((ROOT / path).exists(), path)

    def test_every_written_column_is_registered(self):
        missing = []
        for path, keys in rf.WRITERS.items():
            columns = header_columns(ROOT / path)
            self.assertTrue(columns, f"{path}: no header lists found")
            for key in keys:
                registered = {row["field"]: row["label"] for row in rf.rows(key)}
                missing += [f"{path} → {key}: {text}" for text in unregistered(columns, registered)]
        self.assertEqual(missing, [], "生成器写了、登记表里没有（或表头对不上）的列")

    def test_no_registered_column_without_a_writer(self):
        extra = []
        for key in rf.keys():
            columns = {}
            for path, keys in rf.WRITERS.items():
                if key in keys:
                    columns.update(header_columns(ROOT / path))
            self.assertTrue(columns, f"{key}: no writer")
            extra += [f"{key}.{field}" for field in sorted(rf.names(key) - set(columns))]
        self.assertEqual(extra, [], "登记了、生成器里没有的列")

    def test_detector_sees_a_new_column(self):
        """检查本身有效：生成器多一列（含没数字的第 1 张表、带数字的第 2 张表）就能被取出来并报未登记。"""
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        tmp = Path(folder) / "g.py"
        tmp.write_text('_HEADERS = ["เลขที่", "วันที่"]\n'
                       'def build():\n    HEADERS_2 = ["เลขที่", "รหัสสินค้า", "ใหม่"]\n', encoding="utf-8")
        columns = header_columns(tmp)
        self.assertEqual(columns, {"1.A": "เลขที่", "1.B": "วันที่", "2.A": "เลขที่", "2.B": "รหัสสินค้า", "2.C": "ใหม่"})
        registered = {"1.A": "เลขที่", "1.B": "วันที่", "2.A": "เลขที่", "2.B": "รหัสสินค้า"}
        self.assertEqual(unregistered(columns, registered), ["2.C 'ใหม่' not registered"])
        self.assertEqual(unregistered(columns, {**registered, "2.C": "อื่น"}), ["2.C label 'อื่น' != header 'ใหม่'"])


class OperatorKeysRegistered(unittest.TestCase):
    def test_every_draft_key_lands_or_is_explained(self):
        keys = draft_keys()
        self.assertTrue(keys)
        self.assertEqual(keys - set(rf.OPERATOR_KEYS) - set(rf.NOT_COLUMNS), set(), "代录草稿键没登记落到哪一列")
        self.assertEqual((set(rf.OPERATOR_KEYS) | set(rf.NOT_COLUMNS)) - keys, set(), "登记了 DRAFT_KEYS 里没有的键")
        self.assertEqual(set(rf.OPERATOR_KEYS) & set(rf.NOT_COLUMNS), set())

    def test_targets_are_registered_rows(self):
        for name, targets in rf.OPERATOR_KEYS.items():
            self.assertTrue(targets, name)
            for key, field in targets:
                self.assertIsNotNone(rf.row(key, field), f"{name} → {key}.{field}")

    def test_not_columns_carry_a_reason(self):
        for name, reason in rf.NOT_COLUMNS.items():
            self.assertTrue(str(reason).strip(), name)


if __name__ == "__main__":
    unittest.main()
