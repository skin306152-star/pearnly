"""验收账套工具（scripts/acceptance/ledger.py）：只认测试账套；快照带哈希清单；还原逐文件一致、多出的文件挪走不删；
还原不能串账套；有人开着就不动；前后对比逐表逐行；「预期变化以外逐字节不变」。"""
import importlib.util
import json
import os
import pathlib
import struct
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("acceptance_ledger", ROOT / "scripts" / "acceptance" / "ledger.py")
ledger = importlib.util.module_from_spec(spec)
sys.modules["acceptance_ledger"] = ledger
spec.loader.exec_module(ledger)


def dbf(fields, rows):
    """最小的 DBF：fields = [(名字, 长度)]（全是字符型），rows = [(删除标记, [值…])]。"""
    width = 1 + sum(length for _, length in fields)
    header_len = 32 + 32 * len(fields) + 1
    head = bytearray(32)
    head[0] = 0x30
    struct.pack_into("<IHH", head, 4, len(rows), header_len, width)
    body = bytes(head)
    for name, length in fields:
        item = bytearray(32)
        item[: len(name)] = name.encode("latin1")
        item[11] = ord("C")
        item[16] = length
        body += bytes(item)
    body += b"\x0d"
    for deleted, values in rows:
        body += (b"*" if deleted else b" ") + b"".join(v.encode("cp874").ljust(length)[:length] for v, (_, length) in zip(values, fields))
    return body


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.book = self.root / "69EXP" / "TEST2"
        self.book.mkdir(parents=True)
        fields = [("DOCNUM", 10), ("AMOUNT", 8)]
        (self.book / "APTRN.DBF").write_bytes(dbf(fields, [(False, ["RR0000001", "100.00"])]))
        (self.book / "APTRN.CDX").write_bytes(b"index-1")
        (self.book / "STMAS.DBF").write_bytes(dbf([("STKCOD", 8)], [(False, ["A001"])]))
        (self.book / "sub").mkdir()
        (self.book / "sub" / "note.txt").write_text("x")
        self.snap = self.root / "baselines" / "TEST2@base"
        self.snap.parent.mkdir()

    def test_only_test_ledgers_are_touched(self):
        for good in ("/x/69EXP/TEST", "/x/69EXP/test2", "/x/NLTTEST", "C:\\PearnlyExpress\\70EXP\\T012"):
            self.assertTrue(ledger.is_test_ledger(pathlib.PureWindowsPath(good) if "\\" in good else pathlib.Path(good)), good)
        for bad in ("/x/69EXP/67BRTKCC", "/x/69EXP/TESTCO", "C:\\PearnlyExpress\\70EXP\\CLIENT", "C:\\ACCOUNT\\T012", "/x/TEST/real"):
            self.assertFalse(ledger.is_test_ledger(pathlib.PureWindowsPath(bad) if "\\" in bad else pathlib.Path(bad)), bad)
        real = self.root / "69EXP" / "67BRTKCC"
        real.mkdir()
        (real / "APTRN.DBF").write_bytes(b"real")
        with self.assertRaises(ledger.Refused):
            ledger.snapshot(real, self.root / "s")
        self.assertFalse((self.root / "s").exists())

    def test_snapshot_then_restore_brings_every_file_back_and_moves_extras_aside(self):
        taken = ledger.snapshot(self.book, self.snap, note="验收基线")
        self.assertEqual(taken["files"], 4)
        self.assertTrue(ledger.verify(self.book, self.snap)["ok"])
        # 写一张单、动了索引、删了一个文件、多了写互斥文件
        (self.book / "APTRN.DBF").write_bytes(dbf([("DOCNUM", 10), ("AMOUNT", 8)],
                                                   [(False, ["RR0000001", "100.00"]), (False, ["RR0000002", "55.00"])]))
        (self.book / "APTRN.CDX").write_bytes(b"index-2")
        (self.book / "sub" / "note.txt").unlink()
        (self.book / "PEARNLY.LCK").write_text("lock")
        state = ledger.verify(self.book, self.snap)
        self.assertEqual((state["mismatch"], state["missing"], state["extra"]),
                         (["APTRN.CDX", "APTRN.DBF"], ["sub/note.txt"], ["PEARNLY.LCK"]))
        quarantine = self.root / "aside"
        done = ledger.restore(self.snap, self.book, quarantine)
        self.assertTrue(done["ok"])
        self.assertEqual((done["restored"], done["moved_extra"]), (3, ["PEARNLY.LCK"]))
        self.assertTrue((quarantine / "PEARNLY.LCK").is_file())  # 挪走留查，不直接删
        self.assertEqual(ledger.digest(self.book), json.loads((self.snap / "manifest.json").read_text())["files"])

    def test_snapshot_is_never_overwritten(self):
        ledger.snapshot(self.book, self.snap)
        with self.assertRaises(ledger.Refused):
            ledger.snapshot(self.book, self.snap)

    def test_restore_refuses_another_ledger_and_a_tampered_snapshot(self):
        ledger.snapshot(self.book, self.snap)
        other = self.root / "69EXP" / "TEST"
        other.mkdir()
        (other / "APTRN.DBF").write_bytes(b"other")
        with self.assertRaises(ledger.Refused) as caught:
            ledger.restore(self.snap, other)
        self.assertIn("TEST2", str(caught.exception))
        self.assertEqual((other / "APTRN.DBF").read_bytes(), b"other")
        (self.snap / "STMAS.DBF").write_bytes(b"tampered")
        (self.book / "STMAS.DBF").write_bytes(b"changed")
        with self.assertRaises(ledger.Refused):
            ledger.restore(self.snap, self.book)
        self.assertEqual((self.book / "STMAS.DBF").read_bytes(), b"changed")

    def test_nothing_is_touched_while_someone_has_the_ledger_open(self):
        ledger.snapshot(self.book, self.snap)
        (self.book / "STMAS.DBF").write_bytes(b"changed")
        with self.assertRaises(ledger.Refused) as caught:
            ledger.restore(self.snap, self.book, probe=lambda root: ["APTRN.DBF"])
        self.assertIn("开着", str(caught.exception))
        self.assertEqual((self.book / "STMAS.DBF").read_bytes(), b"changed")
        with self.assertRaises(ledger.Refused):
            ledger.snapshot(self.book, self.root / "s2", probe=lambda root: ["APTRN.DBF"])
        self.assertFalse((self.root / "s2").exists())

    def test_diff_reports_rows_and_the_untouched_invariant(self):
        ledger.snapshot(self.book, self.snap)
        fields = [("DOCNUM", 10), ("AMOUNT", 8)]
        (self.book / "APTRN.DBF").write_bytes(dbf(fields, [(False, ["RR0000001", "120.00"]), (False, ["RR0000002", "55.00"])]))
        (self.book / "APTRN.CDX").write_bytes(b"index-2")
        result = ledger.diff(self.snap, self.book)
        table = result["tables"]["APTRN"]
        self.assertEqual((table["rows_before"], table["rows_after"]), (1, 2))
        self.assertEqual(table["rows"][0], {"row": 0, "changed": {"AMOUNT": ["100.00", "120.00"]}})
        self.assertEqual(table["rows"][1], {"row": 1, "added": {"DOCNUM": "RR0000002", "AMOUNT": "55.00"}, "deleted": False})
        self.assertEqual(ledger.untouched(result, ["APTRN"]), [])
        # 没在预期里的表变了（哪怕只是删除标记）就列出来
        (self.book / "STMAS.DBF").write_bytes(dbf([("STKCOD", 8)], [(True, ["A001"])]))
        result = ledger.diff(self.snap, self.book)
        self.assertEqual(result["tables"]["STMAS"]["rows"], [{"row": 0, "changed": {"_deleted": [False, True]}}])
        self.assertEqual(ledger.untouched(result, ["APTRN"]), ["changed:STMAS.DBF"])
        self.assertEqual(ledger.main(["diff", str(self.snap), str(self.book), "--allow", "APTRN"]), 1)
        self.assertEqual(ledger.main(["diff", str(self.snap), str(self.book), "--allow", "APTRN,STMAS"]), 0)

    def test_rows_reads_back_by_document_number_and_copy_is_read_only(self):
        found = ledger.rows(self.book, "aptrn", "DOCNUM=RR0000001")["rows"]
        self.assertEqual(found, [{"DOCNUM": "RR0000001", "AMOUNT": "100.00", "_deleted": False}])
        self.assertEqual(ledger.rows(self.book, "APTRN", "DOCNUM=RR9")["rows"], [])
        before = ledger.digest(self.book)
        self.assertEqual(ledger.copy(self.book, self.root / "after")["files"], 4)
        self.assertEqual(ledger.digest(self.book), before)
        self.assertEqual(ledger.main(["rows", "b64:" + __import__("base64").b64encode(str(self.book).encode()).decode(), "STMAS"]), 0)


if __name__ == "__main__":
    unittest.main()
