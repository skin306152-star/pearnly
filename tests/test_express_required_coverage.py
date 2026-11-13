"""Express 必填覆盖（CC 引导式录入第 1 期第一个提交；覆盖表 ``docs/work/2026-11-12-cc-guided-entry-express-coverage.md`` 第 6 节）。

- 登记表 ``express_rules/contracts/required_fields.py`` 自身合规：每个字段 auto / ask / review 至少一个、来源与题类认得、默认带依据；
- **写了没登记就红**：从 CC 桌面版写入模块（``WRITERS``）里取出实际写的全部 Express 字段（dict 字面量的键、下标赋值的键），
  逐个要求在该模块对应的单据里登记；新增写入模块或字段而没登记，失败；
- 载荷 21 的新键（``push.common.GUIDED_KEYS`` 与 CC 桌面版 ``payload_contract.GUIDED_KEYS``）逐个登记到它落的字段；
- 覆盖表 A 类缺口（G-A1…G-A14 中落在 Express 字段上的）各有一行带题类；
- 登记表是纯契约：不导入 Frappe、产品包与 IO。

    python3 tests/test_express_required_coverage.py
"""
import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))
sys.path.insert(0, str(ROOT / "companion/src"))

from pearnly_core.express_rules.contracts import required_fields as rf  # noqa: E402
from pearnly_core.express_rules.push import common  # noqa: E402
from pearnly_core.write_precheck import contracts as wc  # noqa: E402

FIELD = re.compile(r"^[A-Z][A-Z0-9_]{2,10}$")
MODULE = ROOT / "apps/pearnly_core/pearnly_core/express_rules/contracts/required_fields.py"


def written_fields(path: Path) -> set:
    """一个写入模块实际写的 Express 字段名：dict 字面量的键与下标赋值的键（大写 3–10 位）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        keys = []
        if isinstance(node, ast.Dict):
            keys = node.keys
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            keys = [node.slice]
        for key in keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str) and FIELD.match(key.value):
                found.add(key.value)
    return found


class RegistryShape(unittest.TestCase):
    def test_registry_is_sound(self):
        self.assertEqual(rf.problems(wc.KINDS), [])

    def test_literal_structure_for_acceptance(self):
        """验收按语法树读：顶层 REQUIRED_FIELDS 是字典字面量，键与每行的 ask 都是字符串字面量（约定见模块文件头）。"""
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "REQUIRED_FIELDS")
        self.assertIsInstance(node.value, ast.Dict)
        for key, value in zip(node.value.keys, node.value.values):
            self.assertIsInstance(key, ast.Constant)
            self.assertRegex(key.value, r"^(express|mrerp):[A-Z_]+$")
            self.assertIsInstance(value, ast.List)
            for row in value.elts:
                self.assertIsInstance(row, ast.Dict)
                fields = {k.value: v for k, v in zip(row.keys, row.values)}
                self.assertIn("field", fields)
                if "ask" in fields:
                    self.assertIsInstance(fields["ask"], ast.Constant, key.value)
                    self.assertIsInstance(fields["ask"].value, str)

    def test_every_field_has_auto_ask_or_review(self):
        for key in rf.keys():
            for row in rf.rows(key):
                self.assertTrue(row.get("auto") or row.get("ask") or row.get("review"), f"{key}.{row['field']}")

    def test_defaults_carry_a_basis(self):
        for key in rf.keys():
            for row in rf.rows(key):
                if row.get("default") is not None:
                    self.assertTrue(row["default"]["basis"].startswith("docs/"), f"{key}.{row['field']}")

    def test_pure_contract(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        self.assertLessEqual(imported, {"__future__", "typing"})


class WrittenFieldsRegistered(unittest.TestCase):
    def test_writer_modules_exist(self):
        for path in rf.WRITERS:
            self.assertTrue((ROOT / path).exists(), path)

    def test_every_written_field_is_registered(self):
        missing = []
        for path, keys in rf.WRITERS.items():
            registered = set().union(*(rf.names(key) for key in keys))
            for name in sorted(written_fields(ROOT / path) - registered - set(rf.NOT_FIELDS)):
                missing.append(f"{path}: {name}（登记到 {'/'.join(keys)} 之一）")
        self.assertEqual(missing, [], "CC 桌面版写了、登记表里没有的 Express 字段")

    def test_detector_sees_a_new_field(self):
        """检查本身有效：模块里多写一个字段（含带下划线的）就能被取出来（防止取法失效后一直绿）。"""
        import shutil
        import tempfile
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        tmp = Path(folder) / "w.py"
        tmp.write_text('row = {"DOCNUM": 1}\nrow["ZZNEWF"] = 2\nrow["SRV_VATTYP"] = 3\n', encoding="utf-8")
        self.assertEqual(written_fields(tmp), {"DOCNUM", "ZZNEWF", "SRV_VATTYP"})


class PayloadKeysRegistered(unittest.TestCase):
    def test_guided_keys_all_registered(self):
        keys = {key for group in common.GUIDED_KEYS.values() for key in group}
        self.assertEqual(keys - set(rf.PAYLOAD_KEYS), set(), "载荷 21 新键没登记落到哪个字段")
        self.assertEqual(set(rf.PAYLOAD_KEYS) - keys, set(), "登记了载荷 21 里没有的键")

    def test_companion_side_matches(self):
        from companion import payload_contract
        self.assertEqual(payload_contract.GUIDED_KEYS, common.GUIDED_KEYS)


class GapRowsAsk(unittest.TestCase):
    """覆盖表 A 类里落在 Express 字段上的缺口：登记行必须带题目代号（不是只靠默认或失败）。"""
    A_FIELD_GAPS = {"G-A1": "payment", "G-A2": "cash_account", "G-A3": "service_vat", "G-A5": "unit", "G-A6": "location",
                    "G-A8": "new_party", "G-A10": "new_item", "G-A12": "payment", "G-A13": "cash_account"}

    def test_a_gaps_have_ask(self):
        for gap, kind in self.A_FIELD_GAPS.items():
            found = [(key, row) for key in rf.keys() for row in rf.rows(key) if row.get("gap") == gap]
            self.assertTrue(found, gap)
            self.assertTrue(any(row.get("ask") == kind for _, row in found), f"{gap} 没有一行出 {kind} 题")
            for key, row in found:
                self.assertIsNone(row.get("default"), f"{gap} {key}.{row['field']} 不许静默默认")


class ModeGapsHonest(unittest.TestCase):
    """录入方式的缺口如实登记（主会话 11-12）：登记了缺口的字段不算已覆盖；修好的缺口必须删掉（与代码对得上）。"""

    def test_operator_new_item_gap_is_closed_with_the_code(self):
        """代录 × 新物料：CC 桌面版代录建主档已把逐行单位码 / 科目组 / 分类 / 负库存传给建档（296f02c9c），服务端代录单带这四键照 21 派——
        缺口已关，登记里不许再有它；代码若退回不传，这条红。"""
        source = (ROOT / "companion/src/companion/express_operator/masters.py").read_text(encoding="utf-8")
        self.assertTrue(all(word in source for word in ("stkgrp=", "acccod=", "negallow=")))
        self.assertNotIn("express:STMAS", rf.MODE_GAPS.get("operator", {}))

    def test_remaining_gaps_are_registered_fields(self):
        for mode, gaps in rf.MODE_GAPS.items():
            for key, gap in gaps.items():
                for field in gap["fields"]:
                    self.assertIsNotNone(rf.row(key, field), f"{mode} {key}.{field}")


class OpenGapsHonest(unittest.TestCase):
    """A8 / A10（主会话 11-13）：服务端没有出 new_party / new_item 题的代码之前，这几格必须登记在 OPEN_GAPS，不算覆盖；出题器落地后删缺口、改这条。"""

    def test_new_party_and_new_item_are_open_until_a_source_asks_them(self):
        sources = [ROOT / "apps/pearnly_cowork/pearnly_cowork/assistant/precheck_source.py",
                   ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/express/precheck.py",
                   ROOT / "apps/pearnly_core/pearnly_core/express_rules/push/choices.py"]
        asked = {kind for kind in ("new_party", "new_item")
                 if any(f'"kind": "{kind}"' in p.read_text(encoding="utf-8") for p in sources)}
        self.assertEqual(asked, set(), "出题器已落地：删 OPEN_GAPS 里对应几格并改这条测试")
        self.assertLessEqual({"SUPTYP", "ACCNUM"}, set(rf.OPEN_GAPS["express:APMAS"]["fields"]))
        self.assertLessEqual({"QUCOD", "ACCCOD", "STKGRP"}, set(rf.OPEN_GAPS["express:STMAS"]["fields"]))


if __name__ == "__main__":
    unittest.main()
