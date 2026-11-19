"""SQL 时钟闸门（scripts/check-sql-clock.py）：仓库通过；SQL 串里用数据库时钟会被认出，文档字符串、非 SQL 文字不算。"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_sql_clock", ROOT / "scripts" / "check-sql-clock.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class SqlClockGate(unittest.TestCase):
    def test_the_repository_passes(self):
        self.assertEqual(gate.main(), 0)

    def test_a_database_clock_in_sql_is_found(self):
        code = ('def put(db):\n'
                '    db.sql("UPDATE `tabX` SET modified=UTC_TIMESTAMP(6) WHERE name=%s", ("a",))\n'
                '    db.sql(f"INSERT INTO `tabY` (name, creation) VALUES (%s, NOW(6))", ("b",))\n')
        self.assertEqual([word for _line, word in gate.hits(pathlib.Path("x.py"), code)], ["UTC_TIMESTAMP", "NOW("])

    def test_docstrings_and_plain_text_do_not_count(self):
        code = ('def put(db):\n'
                '    """原先用 UTC_TIMESTAMP(6) 写 modified，现在 UPDATE 时传 frappe.utils.now()。"""\n'
                '    note = "now() is the framework clock"\n'
                '    db.sql("UPDATE `tabX` SET modified=%s WHERE name=%s", ("t", "a"))\n')
        self.assertEqual(gate.hits(pathlib.Path("x.py"), code), [])


if __name__ == "__main__":
    unittest.main()
