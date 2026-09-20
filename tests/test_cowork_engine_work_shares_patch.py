"""Execute the native patch's actual SQL against an in-memory relational fixture."""
import sqlite3
import unittest

from pearnly_cowork.patches.v0_0_1_revoke_cowork_work_shares import revoke_cowork_work_shares


class Database:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute('CREATE TABLE "tabPearnly OCR Work" (name TEXT PRIMARY KEY, product TEXT, kind TEXT)')
        self.connection.execute('''CREATE TABLE "tabDocShare" (
            name TEXT PRIMARY KEY, share_doctype TEXT, share_name TEXT, user TEXT,
            everyone INTEGER, "read" INTEGER, "write" INTEGER, "share" INTEGER, submit INTEGER,
            modified TEXT, notify_by_email INTEGER)''')

    def sql(self, query, params):
        return self.connection.execute(query.replace("%s", "?"), params)

    def work(self, name, product, kind):
        self.connection.execute('INSERT INTO "tabPearnly OCR Work" VALUES (?,?,?)', (name, product, kind))

    def share(self, name, doctype, target, permissions=(1, 1, 1, 1), everyone=0):
        self.connection.execute('INSERT INTO "tabDocShare" VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (name, doctype, target, "synthetic@example.invalid", everyone, *permissions, "2026-09-21", 1))

    def rows(self):
        return {row[0]: row for row in self.connection.execute('SELECT * FROM "tabDocShare" ORDER BY name')}


class SharePatchTests(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.db.work("cowork-product", "cowork", "unusual.legacy")
        self.db.work("cowork-kind", "", "cowork.invoice")
        self.db.work("dms", "dms", "dms.id_card")
        self.db.work("erp", "erp", "erp.invoice")
        self.db.work("prefix-neighbor", "other", "coworker.invoice")
        self.db.share("active", "Pearnly OCR Work", "cowork-product")
        self.db.share("everyone", "Pearnly OCR Work", "cowork-kind", (1, 0, 0, 1), everyone=1)
        self.db.share("inactive", "Pearnly OCR Work", "cowork-kind", (0, 0, 0, 0))
        self.db.share("dms-share", "Pearnly OCR Work", "dms")
        self.db.share("erp-share", "Pearnly OCR Work", "erp")
        self.db.share("neighbor-share", "Pearnly OCR Work", "prefix-neighbor")
        self.db.share("other-doctype", "Other", "cowork-product")
        self.db.share("dangling", "Pearnly OCR Work", "not-created-yet")

    def tearDown(self):
        self.db.connection.close()

    def test_only_cowork_existing_work_rights_revoked_without_deleting_rows(self):
        before = self.db.rows()
        revoke_cowork_work_shares(self.db)
        after = self.db.rows()
        self.assertEqual(set(before), set(after))
        for name in before:
            if name in {"active", "everyone"}:
                self.assertEqual(after[name][5:9], (0, 0, 0, 0))
                self.assertEqual(after[name][:5], before[name][:5])
                self.assertEqual(after[name][9:], before[name][9:])
            else:
                self.assertEqual(after[name], before[name], name)

    def test_repeated_upgrade_is_idempotent(self):
        revoke_cowork_work_shares(self.db)
        first = self.db.rows()
        revoke_cowork_work_shares(self.db)
        self.assertEqual(self.db.rows(), first)


if __name__ == "__main__":
    unittest.main()
