"""数字会计员第 3 期第 15 条：两层回放工具（`scripts/da-replay.py`）的配对与计分，用合成样例：

- 统计：Wilson 下限与达标线（0 错 381 张、1 错 563、2 错 726 张才到 99.0%）、合并估计；
- 只读 DBF（cp874、删除行、数值 / 日期 / 双精度列）、拒绝局域网路径；
- 判定层：合成一份 Express 账套答案（APTRN / ISVAT / GLJNL / GLJNLIT / APMAS / GLACC / ISINFO），按录入日顺序走推送同一套
  判定：同一家供应商第一张要人选（C，模拟的人按答案选、记进账套记忆），之后按记忆定（记忆的科目要最近连续 3 张同科目才算 A），
  反事实计分对上答案；科目换了的那张计错；
- 档位时间线：三套阈值并排、推错即「被改」；
- 识别闸层：与金标逐字段比（单号只差前缀不算错）、配对、原文核对；
- 期间与两份同期（10-17）：ISPRD 与 CC 桌面版 `_fiscal_window` 同口径、来源优先级、strict / estimated、按日期选账套的情形
  与 C 类原因拆分、盘点的期间检验与重叠、登记表、两份同期时各挑法的模拟；
- 商品单与上传日（10-17 第三段）：答案只认库存品 / 有库存影响的明细为走库存、PEOPLE 防撞号，明细造进票面（stkdes / opaque），
  入账方式按载荷实际写什么计分、存货逐行计分与学记忆，供应商存货历史只用录入在前的单，替公司选入账方式按此前多数，估上传日只
  重判日期核对，报表与明细多出的行与列。
纯标准库，不连库、不依赖 Frappe。
"""
import contextlib
import csv
import datetime as dt
import importlib.util
import io
import json
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))
sys.path.insert(0, str(ROOT / "apps/pearnly_cowork"))
for _name in [n for n in sys.modules if n.split(".")[0] == "pearnly_cowork"]:
    del sys.modules[_name]

_spec = importlib.util.spec_from_file_location("da_replay", ROOT / "scripts/da-replay.py")
replay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(replay)

OWN, SELLER, OTHER = "0994000333447", "0107561000013", "0105556012341"


def write_dbf(path, fields, rows, deleted=()):
    """最小的 dBase III 写法（只给测试造表）：fields [(名, 类型, 长度)]，类型 C / D / N / B。"""
    header = 32 + 32 * len(fields) + 1
    size = 1 + sum(length for _, _, length in fields)
    out = bytearray(struct.pack("<BBBBIHH20x", 0x30, 126, 10, 16, len(rows), header, size))
    for name, kind, length in fields:
        out += struct.pack("<11sc4xBB14x", name.encode("ascii"), kind.encode("ascii"), length, 2 if kind == "N" else 0)
    out += b"\x0D"
    for index, row in enumerate(rows):
        out += b"*" if index in deleted else b" "
        for name, kind, length in fields:
            value = row.get(name)
            if kind == "C":
                out += str(value or "").encode("cp874").ljust(length, b" ")[:length]
            elif kind == "D":
                out += (value.strftime("%Y%m%d") if value else "").encode("ascii").ljust(length, b" ")
            elif kind == "N":
                out += ("" if value is None else f"{Decimal(value):.2f}").encode("ascii").rjust(length, b" ")
            elif kind == "B":
                out += struct.pack("<d", float(value or 0))
    out += b"\x1A"
    pathlib.Path(path).write_bytes(bytes(out))


def make_book(folder, docs, *, stcrd=(), stmas=(), credat=None, vatprd=None):
    """一份合成账套答案：docs [(单号, 日期, 供应商码, 科目, 未税, 税, RECTYP)]。``stcrd`` 存货明细行（STCRD 列名为键）、``stmas``
    存货主档 [(码, 品名, STKTYP)]；``credat`` / ``vatprd``：{单号: 日子}，凭证录入日（默认单据日 + 2 天）与税登记税期。"""
    tables = pathlib.Path(folder) / "原表"
    tables.mkdir(parents=True)
    write_dbf(tables / "ISINFO.DBF", [("ACCNUM04", "C", 15), ("ACCNUM06", "C", 15), ("ACCNUM07", "C", 15),
                                      ("ACCNUM00", "C", 15), ("ACCNUM10", "C", 15), ("ACCNUM11", "C", 15),
                                      ("ACCNUM02", "C", 15), ("ORGNUM", "N", 5), ("TAXID", "C", 15)],
              [{"ACCNUM04": "51-01", "ACCNUM06": "11-50", "ACCNUM07": "21-01", "ACCNUM00": "11-01",
                "ACCNUM10": "21-50", "ACCNUM11": "41-01", "ACCNUM02": "11-30", "ORGNUM": 0, "TAXID": OWN}])
    write_dbf(tables / "GLACC.DBF", [("ACCNUM", "C", 15), ("ACCNAM", "C", 50)],
              [{"ACCNUM": code, "ACCNAM": name} for code, name in (
                  ("51-01", "ซื้อ"), ("52-01", "ค่าน้ำมัน"), ("52-02", "ค่าซ่อม"), ("11-50", "ภาษีซื้อ"),
                  ("21-01", "เจ้าหนี้การค้า"), ("11-01", "เงินสด"), ("41-01", "ขาย"), ("21-50", "ภาษีขาย"))])
    write_dbf(tables / "APMAS.DBF", [("SUPCOD", "C", 10), ("PRENAM", "C", 15), ("SUPNAM", "C", 60), ("TAXID", "C", 15),
                                     ("ORGNUM", "N", 5), ("ACCNUM", "C", 15)],
              [{"SUPCOD": "S001", "PRENAM": "บริษัท", "SUPNAM": "ผู้ขาย จำกัด", "TAXID": SELLER, "ORGNUM": 0,
                "ACCNUM": "21-01"},
               {"SUPCOD": "S002", "PRENAM": "บริษัท", "SUPNAM": "อีกราย จำกัด", "TAXID": OTHER, "ORGNUM": 0,
                "ACCNUM": "21-01"}])
    write_dbf(tables / "ARMAS.DBF", [("CUSCOD", "C", 10), ("CUSNAM", "C", 60), ("TAXID", "C", 15), ("ORGNUM", "N", 5)], [])
    aptrn, isvat, gljnl, lines = [], [], [], []
    for number, day, party, account, net, vat, rectyp in docs:
        net, vat = Decimal(net), Decimal(vat)
        aptrn.append({"RECTYP": rectyp, "DOCNUM": number, "DOCDAT": day, "REFNUM": "IV" + number[-6:], "SUPCOD": party,
                      "TOTAL": net, "VATAMT": vat, "NETAMT": net + vat, "DOCSTAT": "N", "USERID": "ACC"})
        isvat.append({"VATREC": "P", "DOCNUM": number, "DOCDAT": day, "REFNUM": "IV" + number[-6:],
                      "TAXID": SELLER if party == "S001" else OTHER, "ORGNUM": 0, "AMT01": net, "VAT01": vat,
                      "DOCSTAT": "N", "VATPRD": (vatprd or {}).get(number)})
        gljnl.append({"VOUCHER": number, "DOCSTAT": "N", "CREDAT": (credat or {}).get(number, day + dt.timedelta(days=2)),
                      "CREBY": "ACC"})
        lines += [{"VOUCHER": number, "ACCNUM": account, "TRNTYP": "0", "AMOUNT": net},
                  {"VOUCHER": number, "ACCNUM": "11-50", "TRNTYP": "0", "AMOUNT": vat},
                  {"VOUCHER": number, "ACCNUM": "21-01", "TRNTYP": "1", "AMOUNT": net + vat}]
    write_dbf(tables / "APTRN.DBF", [("RECTYP", "C", 1), ("DOCNUM", "C", 12), ("DOCDAT", "D", 8), ("REFNUM", "C", 15),
                                     ("SUPCOD", "C", 10), ("TOTAL", "B", 8), ("VATAMT", "B", 8), ("NETAMT", "B", 8),
                                     ("DOCSTAT", "C", 1), ("USERID", "C", 8)], aptrn)
    write_dbf(tables / "ARTRN.DBF", [("RECTYP", "C", 1), ("DOCNUM", "C", 12), ("DOCDAT", "D", 8), ("CUSCOD", "C", 10),
                                     ("TOTAL", "B", 8), ("VATAMT", "B", 8), ("NETAMT", "B", 8), ("DOCSTAT", "C", 1)], [])
    write_dbf(tables / "ISVAT.DBF", [("VATREC", "C", 1), ("DOCNUM", "C", 12), ("DOCDAT", "D", 8), ("REFNUM", "C", 15),
                                     ("TAXID", "C", 15), ("ORGNUM", "N", 5), ("AMT01", "B", 8), ("VAT01", "B", 8),
                                     ("DOCSTAT", "C", 1), ("VATPRD", "D", 8)], isvat)
    write_dbf(tables / "GLJNL.DBF", [("VOUCHER", "C", 12), ("DOCSTAT", "C", 1), ("CREDAT", "D", 8), ("CREBY", "C", 8)],
              gljnl)
    write_dbf(tables / "GLJNLIT.DBF", [("VOUCHER", "C", 12), ("ACCNUM", "C", 15), ("TRNTYP", "C", 1),
                                       ("AMOUNT", "N", 13)], lines)
    if stmas:
        write_dbf(tables / "STMAS.DBF", [("STKCOD", "C", 20), ("STKDES", "C", 50), ("STKTYP", "C", 1)],
                  [{"STKCOD": code, "STKDES": name, "STKTYP": kind} for code, name, kind in stmas])
    if stcrd:
        write_dbf(tables / "STCRD.DBF", [("STKCOD", "C", 20), ("DOCNUM", "C", 12), ("SEQNUM", "C", 3), ("PEOPLE", "C", 10),
                                         ("TRNQTY", "B", 8), ("UNITPR", "B", 8), ("TRNVAL", "B", 8), ("NETVAL", "B", 8),
                                         ("STKDES", "C", 50), ("PHYBAL", "B", 8)], list(stcrd))


class Statistics(unittest.TestCase):
    def test_the_bar_matches_the_design_sample_sizes(self):
        for errors, enough in ((0, 381), (1, 563), (2, 726)):
            self.assertGreaterEqual(replay.wilson(enough - errors, enough)[0], 0.990, errors)
            self.assertLess(replay.wilson(enough - 1 - errors, enough - 1)[0], 0.990, errors)
        self.assertTrue(replay.passes_bar(381, 381))
        self.assertFalse(replay.passes_bar(380, 380))
        self.assertFalse(replay.passes_bar(990, 1000))  # 点估计 99.0% < 99.5%
        self.assertEqual(replay.wilson(0, 0), (None, None))

    def test_combined_estimate_multiplies_both_layers(self):
        merged = replay.combined(990, 1000, 98, 100)
        self.assertAlmostEqual(merged["point"], 0.99 * 0.98)
        self.assertLess(merged["lower"], merged["point"])
        self.assertGreater(merged["upper"], merged["point"])
        self.assertIsNone(replay.combined(0, 0, 1, 1)["point"])


class ReadOnlyTables(unittest.TestCase):
    def test_reads_typed_columns_and_skips_deleted_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder, "T.DBF")
            write_dbf(path, [("CODE", "C", 6), ("DAY", "D", 8), ("AMT", "N", 10), ("DBL", "B", 8)],
                      [{"CODE": "ก01", "DAY": dt.date(2025, 3, 4), "AMT": "12.50", "DBL": 2044.77},
                       {"CODE": "X", "DAY": None, "AMT": None, "DBL": 0}], deleted=(1,))
            rows = list(replay.dbf_rows(path))
            self.assertEqual(rows, [{"CODE": "ก01", "DAY": dt.date(2025, 3, 4), "AMT": Decimal("12.50"),
                                     "DBL": Decimal("2044.77")}])
            self.assertEqual(list(replay.dbf_rows(path, ["CODE"])), [{"CODE": "ก01"}])
            self.assertEqual(replay.dbf_fields(path), ["CODE", "DAY", "AMT", "DBL"])
            self.assertEqual(list(replay.dbf_rows(pathlib.Path(folder, "NONE.DBF"))), [])

    def test_lan_paths_are_refused(self):
        for path in (r"\\Accserver\d$\ACCOUNT", "S:\\2569\\EXP69", "s:/x", "/mnt/s/EXP69"):
            with self.assertRaises(SystemExit):
                replay.refuse_lan(path)
        self.assertEqual(replay.refuse_lan("/mnt/c/PearnlyStress"), pathlib.Path("/mnt/c/PearnlyStress"))


class JudgementLayer(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        book_dir = pathlib.Path(self.folder.name, "68ACME")
        days = [dt.date(2025, 1, 10) + dt.timedelta(days=7 * index) for index in range(6)]
        docs = [(f"RR6801{index:02d}-001", day, "S001", "52-01", "1000.00", "70.00", "3")
                for index, day in enumerate(days)]
        docs.append(("RR680301-009", dt.date(2025, 3, 1), "S001", "52-02", "500.00", "35.00", "3"))  # 换了科目
        docs.append(("HP680302-001", dt.date(2025, 3, 2), "S002", "52-01", "200.00", "14.00", "1"))
        make_book(book_dir, docs)
        self.book = replay.load_book(book_dir, company="ACME", own_tax=OWN, key="C:\\PearnlyExpress\\T8ACME", periods="estimated")

    def tearDown(self):
        self.folder.cleanup()

    def test_answers_carry_party_account_payment_and_vat(self):
        doc = self.book["docs"][0]
        self.assertEqual((doc["party_code"], doc["party_tax"], doc["accounts"], doc["rectyp"], doc["register"]),
                         ("S001", SELLER, {"52-01": "1000.00"}, "3", True))
        self.assertEqual(replay.answer_values(doc), {"party": "S001", "account": "52-01", "payment": "credit",
                                                     "vat": "claim"})
        self.assertEqual(self.book["period"], (dt.date(2025, 1, 1), dt.date(2025, 12, 31)))
        self.assertEqual(self.book["defaults"]["vat_input_acc"], "11-50")

    def test_first_document_is_chosen_by_people_then_memory_decides(self):
        m = replay._modules()
        # 科目要最近连续 5 张有证据才 A（R3，10-18）：一月要有 5 张（第一张人选、其余凭记忆、一月期回读核过），从 1-03 起每周一张
        folder = pathlib.Path(self.folder.name, "68FIVE")
        days = [dt.date(2025, 1, 3) + dt.timedelta(days=7 * index) for index in range(7)]
        docs = [(f"RR6801{index:02d}-001", day, "S001", "52-01", "1000.00", "70.00", "3")
                for index, day in enumerate(days)]
        docs.append(("RR680301-009", dt.date(2025, 3, 1), "S001", "52-02", "500.00", "35.00", "3"))  # 换了科目
        docs.append(("HP680302-001", dt.date(2025, 3, 2), "S002", "52-01", "200.00", "14.00", "1"))
        make_book(folder, docs)
        book = replay.load_book(folder, company="ACME", own_tax=OWN, key="C:\\PearnlyExpress\\T8FIVE",
                                periods="estimated")
        rows, skipped = replay.judge_company(m, [book])
        self.assertEqual(dict(skipped), {})
        by_number = {row["docnum"]: row for row in rows}
        first = by_number["RR680100-001"]
        self.assertEqual(first["class"], "C")
        self.assertEqual(sorted(first["needed"]), ["account", "payment"])
        self.assertEqual(first["pushed_from"], "replanned")  # 模拟的人选完、学进记忆后照线上再出一次载荷推出去
        second = by_number["RR680101-001"]
        # 抵进项按方向推断是 A（10-16）；记忆定的科目要这家最近连续 3 张同科目才算 A：之前只有 1 张 → B。现 / 赊同（10-17）：
        # 第一张人选的先进账套记忆、推出去也是 remembered，不算证据，要等回读 → B
        self.assertEqual(second["class"], "B", second)
        self.assertEqual(second["reasons"], ["judged:account", "judged:payment"])
        self.assertFalse(second["vat_direction_only"])
        self.assertTrue(second["correct"])
        self.assertEqual(second["scored"], {"party": "ok", "account": "ok", "payment": "ok", "vat": "ok",
                                            "posting": "n/a", "item": "n/a"})
        # 连续张数只数有证据的推送（审查处理 10-16）：第一张人选的（chosen）算；之后凭记忆推的要等这期的合格回读（一月期
        # 2-24 前后）核过才算，所以一、二月里都还是 B
        for number in ("RR680102-001", "RR680103-001", "RR680104-001", "RR680105-001", "RR680106-001"):
            self.assertEqual(by_number[number]["class"], "B", number)
        changed = by_number["RR680301-009"]  # 三月：一月那五张回读核过、都是 52-01 → 科目 A，但这张会计记成 52-02
        self.assertEqual(changed["items"]["account"], "A")
        self.assertEqual(changed["items"]["payment"], "A")  # 一月那几张赊购整张回读核过：现 / 赊也攒够了
        self.assertFalse(changed["correct"])
        self.assertEqual(changed["scored"]["account"], "wrong:52-01")
        other = by_number["HP680302-001"]
        self.assertEqual(other["class"], "C")  # 新供应商：要人选

    def test_payment_and_party_memory_need_three_confirmed_pushes(self):
        """账套记忆定的现 / 赊、对方档要最近连续 3 张有证据（10-17 推广）：ASIA-FA 那种第一张人选现购、之后会计都记赊购的供应商，
        凭记忆推的现购在回读里被改，现 / 赊再也到不了 A；同税号两个档、会计中途换了档：换档后到下一次回读之前那几张仍是 A
        （记忆还撑着，已知残留），回读之后 B。"""
        folder = pathlib.Path(self.folder.name, "68SWITCH")
        days = [dt.date(2025, month, day) for month in range(1, 7) for day in (15, 22, 28)]
        docs = [(f"RR68-1{day:%m%d}", day, "S001", "52-01", "1000.00", "70.00", "3") for day in days]
        docs += [(f"HP68-2{day:%m%d}", day, "S002", "52-01", "200.00", "14.00", "1" if index == 0 else "3")
                 for index, day in enumerate(days)]
        make_book(folder, docs)
        book = replay.load_book(folder, company="SWITCH", own_tax=OWN, key="C:\\PearnlyExpress\\T8SWITCH", periods="estimated")
        book["suppliers"] = book["suppliers"] + [dict(book["suppliers"][0], code="S003")]  # 同税号两个档
        for doc in book["docs"]:
            if doc["party_code"] == "S001" and doc["date"] >= dt.date(2025, 4, 1):
                doc["party_code"] = "S003"  # 会计四月起换用另一个档
        m = replay._modules()
        # 只看证据口径：不模拟人复核时改值（第六轮口径；模拟改值见 `test_people_correcting_memory_values_feed_r1`）
        rows, _ = replay.judge_company(m, [book], review=replay.Review(()))
        readback = {period: replay.readback_day(m, period) for period in {row["period"] for row in rows}}
        cash_first = [row for row in rows if row["docnum"].startswith("HP")]
        self.assertEqual((cash_first[0]["class"], sorted(cash_first[0]["needed"])), ("C", ["account", "payment"]))
        for row in cash_first[1:]:
            self.assertEqual(row["scored"]["payment"], "wrong:cash", row["docnum"])  # 记忆推现购、会计记赊购
            self.assertNotEqual(row["items"]["payment"], "A", row["docnum"])
        switched = [row for row in rows if row["docnum"].startswith("RR")]
        self.assertIn("party", switched[0]["needed"])
        self.assertEqual(switched[1]["sources"]["party_source"], "remembered")
        # 一月那几张回读核过后（二月下旬）对方档 A；四月换档后、四月期回读之前仍是 A 但推错（残留）；之后 B
        settled = [row for row in switched if dt.date.fromisoformat(row["entered"]) > readback["2025-01"]
                   and row["period"] < "2025-04"]
        self.assertTrue(settled)
        self.assertTrue(all(row["items"]["party"] == "A" and row["scored"]["party"] == "ok" for row in settled))
        residual = [row for row in switched if row["period"] >= "2025-04"
                    and dt.date.fromisoformat(row["entered"]) < readback["2025-04"]]
        self.assertTrue(residual)
        self.assertTrue(all(row["items"]["party"] == "A" and row["scored"]["party"] == "wrong:S001"
                            for row in residual))
        after = [row for row in switched if dt.date.fromisoformat(row["entered"]) >= readback["2025-04"]]
        self.assertTrue(after)
        self.assertTrue(all(row["items"]["party"] == "B" for row in after), [row["items"] for row in after])
        # 整张记录口径：对方档推错的那几张回读里被改，同一家的科目、现 / 赊也一起断开
        self.assertTrue(all(row["items"]["account"] == "B" and row["items"]["payment"] == "B" for row in after))
        summary = replay.summarize_judge({"rows": rows, "skipped": {}, "tiers": {}})
        memory = summary["memory_sources"]
        self.assertGreaterEqual(memory["party"]["SWITCH"]["A"], len(settled) + len(residual))
        self.assertEqual(memory["party"]["SWITCH"]["A_wrong"], len(residual))
        self.assertEqual(memory["payment"]["SWITCH"].get("A_wrong", 0), 0)
        self.assertIn("purchase|party|remembered", summary["item_errors"])
        self.assertEqual(summary["pushed_from"].get("replanned"), 2)

    def test_people_correcting_memory_values_feed_r1(self):
        """模拟的人复核时改值（10-18 E1，`Review`，线上口径）：ASIA-FA 那种整份账套一起从现购换赊购——另一家供应商（S002）的
        B 提议上记忆填的现购被人改成赊购（记进账套记忆、记一条改值），这家（S001）凭换法之前攒的证据还判 A 的现购：R1 降 B。
        ``lag`` 1（默认）只认前一天及更早的改值：同一天先处理的那张挡不住当天的、挡得住之后的；0 同一天先处理的也算；不模拟时一直 A、
        推错。被改的那张按改后的值推（现 / 赊记 manual，推的时候就算证据）。"""
        folder = pathlib.Path(self.folder.name, "68FA")
        cash = [dt.date(2025, 1, 3) + dt.timedelta(days=7 * index) for index in range(13)]  # 一月到三月底每周一张现购
        docs = [(f"B1-{day:%m%d}", day, "S001", "52-01", "1000.00", "70.00", "1") for day in cash]
        docs += [(f"B1-{day:%m%d}", day, "S001", "52-01", "1000.00", "70.00", "3")
                 for day in (dt.date(2025, 4, 5), dt.date(2025, 4, 12))]                   # 四月起会计记赊购
        docs += [("A2-0105", dt.date(2025, 1, 5), "S002", "52-01", "200.00", "14.00", "1"),
                 ("A2-0405", dt.date(2025, 4, 5), "S002", "52-01", "200.00", "14.00", "3")]
        make_book(folder, docs)
        book = replay.load_book(folder, company="FA", own_tax=OWN, key="C:\\PearnlyExpress\\T8FA", periods="estimated")
        m = replay._modules()

        def run(review):
            rows, _ = replay.judge_company(m, [book], review=review)
            return {row["docnum"]: row for row in rows}
        review = replay.Review()
        rows = run(review)
        self.assertEqual((review.classes, review.lag), (frozenset({"B", "Cn", "Cr"}), 1))
        corrected = rows["A2-0405"]  # S002：记忆现购、证据只有一张 → B；会计记赊购 → 人改
        self.assertEqual((corrected["class"], corrected["review_class"], corrected["corrected"]), ("B", "B", ["payment"]))
        same_day, next_week = rows["B1-0405"], rows["B1-0412"]
        # 同一天录入的：前一天及更早才算，挡不住（A、推错）；下一周的：R1 降 B，成了 B 提议又被人改（第二条改值）
        self.assertEqual((same_day["class"], same_day["scored"]["payment"]), ("A", "wrong:cash"))
        self.assertEqual((next_week["class"], next_week["items"]["payment"]), ("B", "B"))
        self.assertEqual((next_week["memory_holds"]["payment"], next_week["corrected"]), (["overruled"], ["payment"]))
        self.assertEqual((review.summary()["events"], review.summary()["corrections"]), (2, {"B|payment": 2}))
        # 证据窗口有上限、窗口里连续张数本来就够、是 R1 挡下的：不算「上限挤掉」（10-18）
        capped = {row["docnum"]: row for row in replay.judge_company(m, [book], review=replay.Review(), recent_max=12)[0]}
        self.assertEqual((capped["B1-0412"]["class"], capped["B1-0412"]["memory_holds"]), ("B", {"payment": ["overruled"]}))
        self.assertEqual(capped["B1-0412"]["memory_cap_lost"], [])
        # 同一天先处理的也算：当天那张也挡住
        rows = run(replay.Review(lag=0))
        self.assertEqual((rows["B1-0405"]["class"], rows["B1-0405"]["memory_holds"]), ("B", {"payment": ["overruled"]}))
        # 不模拟（第六轮口径）：一直 A、推错，没有改值
        rows = run(replay.Review(()))
        self.assertEqual([rows[number]["class"] for number in ("B1-0405", "B1-0412")], ["A", "A"])
        self.assertEqual(rows["B1-0412"]["scored"]["payment"], "wrong:cash")
        self.assertEqual(rows["A2-0405"]["corrected"], [])
        # 被改的那张按改后的值记推送：现 / 赊 manual、整张对
        record = replay.pushed_record({"party": "S002", "account": "52-01", "payment": "credit"},
                                      {"supplier": {"code": "S002"}, "items_account": "52-01",
                                       "account_source": "remembered", "doctype": "HP", "doctype_src": "remembered"},
                                      {"party": "S002", "account": "52-01", "payment": "cash"}, [],
                                      corrected=["payment"])
        self.assertEqual((record["payment"], record["payment_source"], record["ok"], record["changed_kinds"]),
                         ("RR", "manual", True, []))
        wrong = replay.pushed_record({"party": "S002", "account": "*multi", "payment": "credit"},
                                     {"items_account": "52-01", "doctype": "HP"},
                                     {"party": "S003", "account": "52-01", "payment": "cash"}, [])
        self.assertEqual(wrong["changed_kinds"], ["account", "payment", "party"])
        self.assertEqual(replay.pushed_record({"account": "52-02", "payment": "credit"}, {"doctype": "HP"},
                                              {"account": "52-01", "payment": "cash"}, [], "account")["changed_kinds"],
                         ["account"])

    def test_review_visibility_and_arguments(self):
        """`Review.visible`：这份账套、这几类各自的记忆值、近 60 天，按 ``lag`` 截；``--review-classes`` 认 B / Cn / Cr 与 none；
        回放的 R1 窗口与线上同一个常量。"""
        review = replay.Review(lag=1)
        day = dt.date(2025, 4, 7)
        review.note("BK", "payment", "cash", "credit", day, "purchase|S", "B")
        review.note("BK", "party", "S1", "S2", day, "purchase|S|00000", "Cr")  # 对方档不进改值记录
        review.note("BK", "account", "52-01", "52-02", dt.date(2025, 2, 1), "purchase|S", "Cn")
        self.assertEqual(review.visible("BK", day, {"payment": "cash"}), [])
        self.assertEqual(len(review.visible("BK", day + dt.timedelta(days=1), {"payment": "cash"})), 1)
        self.assertEqual(review.visible("BK", day + dt.timedelta(days=1), {"payment": "credit"}), [])
        self.assertEqual(review.visible("OTHER", day + dt.timedelta(days=1), {"payment": "cash"}), [])
        self.assertEqual(len(review.visible("BK", dt.date(2025, 4, 2), {"account": "52-01"})), 1)   # 60 天内
        self.assertEqual(review.visible("BK", dt.date(2025, 4, 3), {"account": "52-01"}), [])       # 61 天
        self.assertEqual(len(replay.Review(lag=0).visible("BK", day, {"payment": "cash"})), 0)
        same = replay.Review(lag=0)
        same.note("BK", "payment", "cash", "credit", day, "purchase|S", "B")
        self.assertEqual(len(same.visible("BK", day, {"payment": "cash"})), 1)
        self.assertEqual(review.summary(), {"classes": ["B", "Cn", "Cr"], "lag": 1, "events": 2,
                                            "corrections": {"B|payment": 1, "Cn|account": 1, "Cr|party": 1}})
        self.assertEqual(replay.review_classes("B,Cr"), ("B", "Cr"))
        self.assertEqual(replay.review_classes("none"), ())
        self.assertEqual(replay.review_classes(""), ())
        with self.assertRaises(SystemExit):
            replay.review_classes("A")
        m = replay._modules()
        self.assertEqual((replay.OVERRIDE_DAYS, replay.OVERRIDE_KINDS),
                         (m["automation"].OVERRIDE_DAYS, m["automation"].OVERRIDE_KINDS))
        # 三条收严挡下的单：只因新三条才是 B 的、推错的
        rows = [{"class": "B", "reasons": ["judged:payment"], "memory_holds": {"payment": ["overruled"]}, "correct_eval": False},
                {"class": "B", "reasons": ["judged:account"], "memory_holds": {"account": ["streak_long", "changed_share"]},
                 "correct_eval": True},
                {"class": "B", "reasons": ["judged:account", "judged:vat"], "memory_holds": {"account": ["overruled"]},
                 "correct_eval": True},
                {"class": "B", "reasons": ["judged:account"], "memory_holds": {"account": ["streak"]}, "correct_eval": True},
                {"class": "A", "reasons": [], "memory_holds": {}, "correct_eval": True}]
        found = replay.tightening_summary(rows)
        self.assertEqual(found["tightened"], {"docs": 2, "wrong": 1, "by_code": {
            "changed_share": {"docs": 1, "wrong": 0}, "overruled": {"docs": 1, "wrong": 1},
            "streak_long": {"docs": 1, "wrong": 0}}})
        self.assertEqual(found["holds"]["account:overruled"], 1)

    def test_goods_posting_is_chosen_once_per_company_then_followed(self):
        """永续库存账套的商品单（第一轮回放后定，10-16）：第一张因没声明入账类型交人，模拟的人按答案替这家公司选一次，
        之后同类单按选的走（`goods_posting.resolve`，与推送服务同一份），posting 一项算 A。"""
        m = replay._modules()
        self.book["fingerprint"] = {"stcrd_lines": 10, "stcrd_lines_moving_stock": 9}  # 永续库存
        for doc in self.book["docs"]:
            doc["stock"] = doc["party_code"] == "S001" and doc["docnum"] != "RR680104-001"
        rows, _ = replay.judge_company(m, [self.book])
        by_number = {row["docnum"]: row for row in rows}
        posting = [row for row in rows if str(row["prepare"]).startswith("posting_needs_review")]
        self.assertEqual(len(posting), 1, [(row["docnum"], row["prepare"]) for row in rows])
        later = [row for row in rows if row["entered"] > posting[0]["entered"] and row["docnum"].startswith("RR6801")]
        self.assertTrue(later)
        for row in later:
            self.assertEqual((row["posting_from"], row["items"].get("posting")), ("company_choice", "B"), row)
            self.assertFalse(str(row["prepare"]).startswith("posting_needs_review"))
        # 同线上不预先声明（审查处理 10-16）：答案里没有存货明细的单也按公司选的「走库存」走
        self.assertEqual(by_number["HP680302-001"]["posting_from"], "company_choice")
        # 票面不造明细（``--lines none``）时入账方式按声明值计分（审查处理 10-17：按载荷计会把每张「公司选走库存」的单都算成推错，
        # 是造票面的伪影）：公司选了走库存，答案走库存的单对上，答案是服务的那张计错（真错：公司选的值与这张的记法不同）
        service = by_number["RR680104-001"]
        self.assertEqual((service["posting_from"], service["scored"].get("posting")), ("company_choice", "wrong:stock"))
        self.assertFalse(service["correct"])
        self.assertFalse(service["correct_eval"])
        stock = [row for row in later if row["scored"] and row["docnum"] != "RR680104-001"]
        self.assertEqual({row["scored"].get("posting") for row in stock}, {"ok"})
        self.assertEqual({row["items_status"] for row in later if row["scored"]}, {"empty"})
        # 入账方式不再把这些单算错（这本的 52-01 在库存车道写成连接的进货科目，是库存车道科目一项的口径，另量）
        self.assertEqual({item for row in stock for item, value in row["scored"].items() if value.startswith("wrong")},
                         {"account"})

    def test_next_year_documents_go_into_the_old_book_until_the_new_one_exists(self):
        """新年度账套还没建时，下一年的单记进旧账套的次年段（认 next_end，用户 10-16）；新年度账套一建好就该推往新的。"""
        m = replay._modules()
        # 按单据估的期间（estimated，load_book 默认）：次年段再过一年；strict 下没有 ISPRD 不报期间
        self.assertEqual((self.book["next_end"], self.book["period_origin"]), (dt.date(2026, 12, 31), "estimated"))
        self.assertEqual(replay.account_row(self.book)["attributes"]["next_end"], "2026-12-31")
        doc = dict(self.book["docs"][0], date=dt.date(2026, 2, 3), entered=dt.date(2026, 2, 5))
        old_only = replay.shaped_books(m, [self.book])
        self.assertEqual(replay.day_books(m, old_only, self.book, doc["date"]),
                         [m["identity"].book("express", "replay", replay.account_row(self.book))["key"]])
        found = replay.judge_document(m, doc, self.book, replay.Memory(), {}, [replay.account_row(self.book)],
                                      {}, replay.CompanyChoice(), old_only)
        self.assertEqual(found["outside"], "next_year")
        self.assertFalse({"period", "book"} & set(found["reasons"]), found["reasons"])
        newer = dict(self.book, key="C:\\PearnlyExpress\\T9ACME", period=(dt.date(2026, 1, 1), dt.date(2026, 12, 31)),
                     next_end=dt.date(2027, 12, 31), docs=[])
        both = replay.shaped_books(m, [self.book, newer])
        found = replay.judge_document(m, doc, self.book, replay.Memory(), {}, [replay.account_row(self.book)],
                                      {}, replay.CompanyChoice(), both)
        self.assertIn("book", found["reasons"])
        # 10-17：按 book_pick 拆，新年度账套已建归 book_other_newer（原 next_year）
        self.assertEqual((found["book_pick"], found["other_newer"], found["outside"]), ("other", True, "next_year"))
        self.assertTrue(cause("book_other_newer")(found))
        self.assertFalse(cause("next_year")(found))
        # 晚于次年段：照旧不在期间里
        late = dict(doc, date=dt.date(2027, 2, 3), entered=dt.date(2027, 2, 5))
        found = replay.judge_document(m, late, self.book, replay.Memory(), {}, [replay.account_row(self.book)],
                                      {}, replay.CompanyChoice(), old_only)
        self.assertEqual(found["outside"], "after")
        self.assertIn("period", found["reasons"])
        # 同一张单在新旧两份里都有：本年期间盖住的那份回放；只在旧的里有、新的盖不住：旧的次年段
        self.assertEqual(replay.next_year_end(dt.date(2024, 2, 29)), dt.date(2025, 2, 28))

    def test_trimmed_masters_decide_like_the_full_masters(self):
        # 回放按单只给用得着的对方主档行（省时间）：判定与全表一样
        m = replay._modules()
        extra = [{"code": f"X{index}", "name": "อื่น", "prefix": "", "tax_id": f"01055560{index:05d}",
                  "branch": "00000", "account": ""} for index in range(30)]
        self.book["suppliers"] = self.book["suppliers"] + extra
        for doc in self.book["docs"]:
            fields = replay.perfect_fields(doc, self.book)
            for remembered in ({}, {"party": {"x": "S002"}}):
                memory = {kind: {} for kind in ("party", "account", "payment", "vat")}
                memory.update(remembered)
                full = replay.book_config(self.book, full=True)
                trimmed = replay.book_config(self.book, party_tax=doc["party_tax"], keep=memory["party"].values())
                self.assertLess(len(trimmed["reported_suppliers"]), len(full["reported_suppliers"]))
                one = m["decide"].decide(fields, direction="purchase", posting_kind="service", config=full,
                                         remembered=memory)
                two = m["decide"].decide(fields, direction="purchase", posting_kind="service", config=trimmed,
                                         remembered=memory)
                self.assertEqual(one, two)
        lonely = replay._party_rows(self.book["suppliers"], "0000000000000", ())
        self.assertEqual(len(lonely), 1)  # 没有同税号的档：留一行别家的，「主档报上来了」不变

    def test_express_history_holds_only_documents_entered_before_this_one(self):
        # 审查处理 10-16：历史不含这张单自己、不含录入更晚的；历史定的科目来源记 express_history（同推送服务）
        m = replay._modules()
        purchases = replay.history_index(self.book["docs"], m["choices"].norm_name)
        docs = {doc["docnum"]: doc for doc in self.book["docs"]}
        subject = m["choices"].supplier_subject("purchase", SELLER, "")

        def judge(number):
            memory = replay.Memory()
            memory.kinds["payment"][subject] = "credit"
            return replay.judge_document(m, docs[number], self.book, memory, {}, [replay.account_row(self.book)],
                                         purchases)

        third = judge("RR680102-001")  # 之前只录了 2 张：不够「已定」，照常要人选
        self.assertIn("account", third["needed"])
        fourth = judge("RR680103-001")  # 之前 3 张都记 52-01：历史已定
        self.assertEqual((fourth["needed"], fourth["sources"]["account_source"]), ([], "express_history"))
        self.assertEqual(fourth["scored"]["account"], "ok")
        changed = judge("RR680301-009")  # 之前 6 张记 52-01、这张记 52-02：历史定成 52-01，计错
        self.assertEqual((changed["sources"]["account_source"], changed["scored"]["account"]),
                         ("express_history", "wrong:52-01"))

    def test_the_task_timeline_pushes_a_only_on_its_baseline(self):
        m = replay._modules()
        base = m["automation_rules"].THRESHOLDS

        def row(number, day, ok):
            return {"docnum": number, "class": "A", "period": day[:7], "entered": day, "correct": ok,
                    "correct_eval": ok}

        rows = [row(f"J{i}", "2025-01-20", False) for i in range(10)]
        rows += [row(f"M{i}", "2025-03-10", True) for i in range(5)]
        found = replay.timeline(m, rows, base, trigger="task")
        # 一月：新客户照推；一月的回读（截止日后）显示 A 类被改：三月改出提议
        self.assertEqual((found["auto"], found["auto_wrong"], found["proposals"]), (10, 10, 5))

    def test_book_period_prefers_the_window_with_most_documents(self):
        days = [dt.date(2024, 12, 20)] + [dt.date(2025, month, 5) for month in range(1, 13)] * 3
        self.assertEqual(replay.book_period(days), (dt.date(2025, 1, 1), dt.date(2025, 12, 31)))
        self.assertEqual(replay.book_period([]), (None, None))

    def test_timelines_count_wrong_pushes_as_changed(self):
        m = replay._modules()
        rows, _ = replay.judge_company(m, [self.book])
        lines = {name: replay.timeline(m, rows, value or m["automation_rules"].THRESHOLDS)
                 for name, value in replay.THRESHOLD_SETS.items()}
        # 只有 B 类（L0 / L1 都只出提议）：自动推 0，样本不够升档
        for value in lines.values():
            self.assertEqual((value["auto"], value["final"]), (0, "L0"))
            self.assertEqual(value["proposals"] + value["stopped"], len(rows))
        task = replay.timeline(m, rows, m["automation_rules"].THRESHOLDS, trigger="task")
        self.assertEqual(task["auto"], sum(1 for row in rows if row["class"] == "A"))

    def test_report_renders_both_layers(self):
        m = replay._modules()
        rows, skipped = replay.judge_company(m, [self.book])
        base = m["automation_rules"].THRESHOLDS
        tiers = {name: replay.timeline(m, rows, value or base) for name, value in replay.THRESHOLD_SETS.items()}
        tiers["A_task"] = replay.timeline(m, rows, base, trigger="task")
        judge = replay.summarize_judge({"rows": rows, "skipped": {"ACME": dict(skipped)}, "tiers": {"ACME": tiers}})
        self.assertEqual(judge["docs"], len(rows))
        self.assertEqual(sum(judge["c_causes"].values()), judge["classes"]["C"])
        self.assertEqual(replay.primary_cause(rows[0]), "needed")
        gate = replay.summarize_gate({"rows": [
            {"id": "1", "form": "电子PDF", "company": "ACME", "direction": "purchase", "page": 1, "text_pdf": True,
             "reasons": [], "passed": True, "entry": "enter", "paired": True, "result": {"date": "ok"}, "uncertain": [],
             "wrong": []},
            {"id": "2", "form": "照片", "company": "ACME", "direction": "purchase", "page": 1, "text_pdf": False,
             "reasons": ["image"], "passed": False, "entry": "enter", "paired": True, "result": {}, "uncertain": [],
             "wrong": []}]})
        self.assertEqual((gate["text_pdf"]["n"], gate["text_pdf"]["ok"], gate["image"]["passed"]), (1, 1, 0))
        text = replay.render(judge, gate, None)
        self.assertIn("判定层", text)
        self.assertIn("识别闸层", text)
        self.assertIn("合并估计", text)
        # 10-17：账套记忆定的三类按家统计、推送记录来源
        self.assertIn("账套记忆定的项", text)
        self.assertEqual(set(judge["memory_sources"]), {"account", "payment", "party", "vat"})
        self.assertEqual(set(judge["memory_cap_lost"]), {"account", "payment", "party"})
        self.assertEqual(sum(judge["pushed_from"].values()), len(rows))

    def test_a_document_carried_into_the_next_years_book_is_replayed_once(self):
        # Express 新年度账套由上一年复制而来，带着往年的单：只在本年期间盖住单据日期的那份账套里回放
        later = pathlib.Path(self.folder.name, "69ACME")
        make_book(later, [("RR680100-001", dt.date(2025, 1, 10), "S001", "52-01", "1000.00", "70.00", "3"),
                          *[(f"RR6901{index:02d}-001", dt.date(2026, 1, 10) + dt.timedelta(days=7 * index), "S001",
                             "52-01", "1000.00", "70.00", "3") for index in range(3)]])
        next_book = replay.load_book(later, company="ACME", own_tax=OWN, key="C:\\PearnlyExpress\\T9ACME", periods="estimated")
        self.assertEqual(next_book["period"], (dt.date(2026, 1, 1), dt.date(2026, 12, 31)))
        owner = replay.owners([self.book, next_book])
        carried = next(doc for doc in next_book["docs"] if doc["docnum"] == "RR680100-001")
        self.assertEqual(owner[replay.doc_key(carried)], self.book["key"])
        rows, skipped = replay.judge_company(replay._modules(), [self.book, next_book])
        self.assertEqual(skipped["copy_in_other_book"], 1)
        self.assertEqual(sum(1 for row in rows if row["docnum"] == "RR680100-001"), 1)

    def test_eligibility(self):
        doc = dict(self.book["docs"][0])
        self.assertIsNone(replay.eligible(doc))
        self.assertEqual(replay.eligible({**doc, "cancelled": True}), "cancelled")
        self.assertEqual(replay.eligible({**doc, "rectyp": "5"}), "rectyp_5")
        self.assertEqual(replay.eligible({**doc, "total": Decimal("0")}), "zero_amount")
        self.assertEqual(replay.eligible({**doc, "pearnly": True}), "pearnly")


class GoodsAndUpload(unittest.TestCase):
    """商品单与上传日（10-17 第三段，只在回放里量）：答案口径、明细造进票面、入账方式按载荷实际写什么计分、存货逐行计分与学记忆、
    供应商存货历史、替公司选入账方式按此前多数、估上传日只重判日期核对。"""

    STMAS = (("P01", "ปูนซีเมนต์", "0"), ("SV1", "ค่าขนส่ง", "3"))

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def line(self, number, seq, code, name, amount, people="S001", moving=0):
        return {"DOCNUM": number, "SEQNUM": str(seq), "STKCOD": code, "STKDES": name, "PEOPLE": people,
                "TRNQTY": 1, "UNITPR": amount, "TRNVAL": amount, "NETVAL": amount, "PHYBAL": moving}

    def goods_book(self, name="GOODS", **options):
        """S001 每周一张：G01–G06 走库存（库存品 P01）、G07 只有非库存品明细（运费 SV1）、G08 没有明细。"""
        days = [dt.date(2025, 1, 6) + dt.timedelta(days=7 * index) for index in range(8)]
        docs = [(f"G{index + 1:02d}", day, "S001", "51-01", "1000.00", "70.00", "3") for index, day in enumerate(days)]
        stcrd = [self.line(f"G{index:02d}", 1, "P01", "ปูนซีเมนต์", 1000, moving=1) for index in range(1, 7)]
        stcrd.append(self.line("G07", 1, "SV1", "ค่าขนส่ง", 1000))
        make_book(self.root / name, docs, stcrd=stcrd, stmas=self.STMAS, **options)
        book = replay.load_book(self.root / name, company=name, own_tax=OWN, key=f"C:\\PearnlyExpress\\{name}", periods="estimated")
        book["fingerprint"] = {"stcrd_lines": 10, "stcrd_lines_moving_stock": 9}  # 永续库存：商品单交人
        return book

    def test_only_stock_items_make_a_stock_answer(self):
        docs = [("RR01", dt.date(2025, 1, 6), "S001", "51-01", "1000.00", "70.00", "3"),
                ("RR02", dt.date(2025, 1, 7), "S001", "51-01", "1000.00", "70.00", "3"),
                ("RR03", dt.date(2025, 1, 8), "S002", "51-01", "1000.00", "70.00", "3")]
        stcrd = [self.line("RR01", 1, "SV1", "ค่าขนส่ง", 1000),
                 self.line("RR02", 2, "SV1", "", 200), self.line("RR02", 1, "P01", "ปูนซีเมนต์", 800),
                 self.line("RR03", 1, "P01", "ปูนซีเมนต์", 1000)]  # 单号撞上：PEOPLE 是 S001，不挂到 S002 的单
        make_book(self.root / "ACME", docs, stcrd=stcrd, stmas=self.STMAS)
        book = replay.load_book(self.root / "ACME", company="ACME", own_tax=OWN, key="ACME")
        found = {doc["docnum"]: doc for doc in book["docs"]}
        # 只挂非库存品（服务费 STKTYP=3）的明细不算走库存（第四轮按「有任何明细」算）；有库存品的算
        self.assertEqual((found["RR01"]["stock"], found["RR01"]["detail"]), (False, True))
        self.assertEqual((found["RR02"]["stock"], found["RR02"]["detail"]), (True, True))
        self.assertEqual((found["RR03"]["stock"], found["RR03"]["detail"]), (False, False))
        # 按 SEQNUM 排；明细没写品名的取主档名；金额 Decimal 写法
        self.assertEqual([(line.code, line.name, line.amount, line.kind) for line in found["RR02"]["lines"]],
                         [("P01", "ปูนซีเมนต์", "800.00", "stock"), ("SV1", "ค่าขนส่ง", "200.00", "non_stock")])
        self.assertEqual(book["stcrd"], {"lines": 4, "attached": 3, "people_blank": 0, "people_other": 1,
                                         "shared_docnum": 0})
        # 行序：字符列取数字，数值列（带小数位）取整，不拼成 100（审查处理 10-17）
        self.assertEqual((replay._seq("2"), replay._seq(Decimal("1.00")), replay._seq(""), replay._seq("1.00")),
                         (2, 1, 0, 1))
        self.assertEqual(replay.answer_items(found["RR02"]), ("P01",))
        moving = replay.Line("X9", "ของ", "1", "5", "5.00", "unknown", True)
        self.assertTrue(replay.stock_line(moving))  # 主档里没有、但动了库存：算

    def test_answer_lines_become_invoice_items(self):
        book = self.goods_book()
        docs = {doc["docnum"]: doc for doc in book["docs"]}
        self.assertNotIn("items", replay.perfect_fields(docs["G01"], book))  # 默认不造（与第四轮可比）
        fields = replay.perfect_fields(docs["G01"], book, "answer")
        self.assertEqual(fields["items"], [{"name": "ปูนซีเมนต์", "qty": "1.0", "price": "1000.0",
                                            "subtotal": "1000.00"}])
        opaque = replay.perfect_fields(docs["G01"], book, "answer", "opaque")["items"][0]["name"]
        self.assertTrue(opaque.startswith("L-"))
        self.assertNotEqual(opaque, "ปูนซีเมนต์")
        self.assertEqual(replay.perfect_fields(docs["G02"], book, "answer", "opaque")["items"][0]["name"], opaque)
        self.assertNotIn("items", replay.perfect_fields(docs["G08"], book, "answer"))  # 没有明细的单照旧
        # 存货主档只给同名的、记过的码与另一行；同名查找与全表一样
        config = replay.book_config(book, party_tax=SELLER, item_names=["ปูนซีเมนต์"])
        self.assertEqual([row["code"] for row in config["reported_products"]], ["P01", "SV1"])
        self.assertEqual([row["code"] for row in replay.book_config(book, item_keep=["SV1"])["reported_products"]],
                         ["SV1", "P01"])
        # 造了明细的走库存单：明细过了对账闸、载荷写库存行
        rows, _ = replay.judge_company(replay._modules(), [book], lines="answer")
        by_number = {row["docnum"]: row for row in rows}
        self.assertEqual(by_number["G03"]["items_status"], "ok")
        self.assertEqual(by_number["G03"]["posting_kind"], "stock")

    def test_posting_is_scored_by_what_the_payload_writes(self):
        book = self.goods_book()
        payload = {"direction": "purchase", "items_status": "ok", "supplier": {"code": "S001"},
                   "items": [{"name": "ปูนซีเมนต์", "item_mode": "stock_item"}, {"name": "ค่าขนส่ง", "item_mode": "stock_item",
                                                                                    "code": "SV1"}]}
        fields = replay.perfect_fields(book["docs"][0], book)
        found = replay.payload_values(payload, book, fields)
        self.assertEqual((found["posting"], found["item"]), ("stock", ("P01", "SV1")))  # 没写码的按同名在全量主档里找
        # 明细没过对账闸：CC 桌面版只写表头（等于记费用），不写 STCRD
        found = replay.payload_values(dict(payload, items_status="mismatch"), book, fields)
        self.assertEqual((found["posting"], found["item"]), ("service", ()))
        found = replay.payload_values(dict(payload, items=[{"name": "x", "item_mode": "non_stock_item"}]), book, fields)
        self.assertEqual(found["posting"], "service")
        # 不造明细（``declared``）：入账方式照这张单用的入账类型计分，载荷有没有库存行不看（审查处理 10-17）
        self.assertEqual(replay.payload_values(dict(payload, items=[]), book, fields, "stock")["posting"], "stock")
        self.assertEqual(replay.payload_values(payload, book, fields, "service")["posting"], "service")
        m = replay._modules()
        none, _ = replay.judge_company(m, [book])
        answer, _ = replay.judge_company(m, [book], lines="answer")
        none, answer = ({row["docnum"]: row for row in rows} for rows in (none, answer))
        # 不造明细：公司选了走库存，走库存的单对上、没有明细的那张计错（公司选的值与它的记法不同）
        self.assertEqual(none["G03"]["scored"]["posting"], "ok")
        self.assertEqual(none["G08"]["scored"]["posting"], "wrong:stock")
        # 档位时间线不因入账方式把走库存的单记成「被改」（`_row_ok`，审查处理 10-17）
        self.assertTrue(all(replay._row_ok(none[number]) for number in ("G03", "G04", "G05", "G06")))
        # 造了明细（stkdes）：走库存的单写进库存行，入账方式与存货都对；只有运费明细的那张要人选存货（主档没有同名库存品）
        self.assertEqual((answer["G03"]["scored"]["posting"], answer["G03"]["scored"]["item"]), ("ok", "ok"))
        self.assertEqual(answer["G03"]["items"]["item"], "A")
        self.assertEqual(answer["G07"]["class"], "C")
        self.assertIn("item", answer["G07"]["needed"])
        self.assertEqual(answer["G08"]["scored"]["posting"], "ok")

    def test_items_are_scored_and_learned_per_line(self):
        """打码品名（悲观下界）：同名查找落空，第一次见要人选存货，模拟的人按答案那一行的码记进账套记忆，之后同品名 A。"""
        book = self.goods_book()
        rows, _ = replay.judge_company(replay._modules(), [book], lines="answer", names="opaque")
        by_number = {row["docnum"]: row for row in rows}
        self.assertEqual(sorted(by_number["G01"]["needed"]), ["account", "payment"])  # 第一张：新供应商要人选
        self.assertEqual(by_number["G02"]["prepare"][:20], "posting_needs_review")  # 第二张：替公司选入账方式
        self.assertIn("item", by_number["G03"]["needed"])  # 第三张：走库存后存货第一次见
        self.assertEqual(by_number["G03"]["class"], "C")
        later = by_number["G04"]
        self.assertEqual((later["items"]["item"], later["scored"]["item"], later["scored"]["posting"]), ("A", "ok", "ok"))
        self.assertEqual(later["needed"], [])

    def test_supplier_stock_history_uses_only_documents_entered_before(self):
        m = replay._modules()
        doc = {"date": dt.date(2025, 3, 1), "cancelled": False, "pearnly": False, "stock": True}
        entries = [dict(doc, date=dt.date(2025, 3, day)) for day in (1, 2, 3)]
        found = replay.stock_history(entries + [dict(doc, pearnly=True), dict(doc, cancelled=True),
                                                dict(doc, date=dt.date(2024, 1, 1))],
                                     dt.date(2024, 3, 10), dt.date(2025, 3, 10))
        self.assertEqual(found, {"stock": {"docs": 3, "last": "2025-03-03"}, "service": {"docs": 0, "last": ""}})
        self.assertEqual(m["history_rules"].posting_settled(found, dt.date(2025, 3, 10), dt.date(2025, 3, 10))["kind"],
                         "stock")
        # 录入在这张之后的单不算（G02 晚录到四月）：G04 之前只剩 G01、G03 两张，不到 3 张
        late = self.goods_book("LATE", credat={"G02": dt.date(2025, 4, 1)})
        rows = {row["docnum"]: row for row in replay.judge_company(m, [late])[0]}
        self.assertEqual(rows["G04"]["posting_history"], "")
        self.assertEqual(rows["G05"]["posting_history"], "stock")
        book = self.goods_book()
        rows = {row["docnum"]: row for row in replay.judge_company(m, [book])[0]}
        # 不造明细：载荷写不出库存行，定成走库存的历史线上也不给 A（no_lines，审查处理 10-17）
        self.assertEqual((rows["G04"]["posting_history"], rows["G04"]["posting_tier_if_history"]), ("stock", "no_lines"))
        self.assertEqual(rows["G03"]["posting_history"], "")
        # 服务单夹进来以后两种记法都有：定不下
        self.assertEqual((rows["G07"]["posting_history"], rows["G08"]["posting_history"]), ("stock", ""))
        self.assertEqual(rows["G08"]["posting_tier_if_history"], "B")
        choices = {"GOODS": {"used": "stock", "by": "answer", "prior": None, "full": "stock"}}
        none = replay.summarize_judge({"rows": list(rows.values()), "skipped": {}, "tiers": {},
                                       "goods_posting": choices})["goods"]
        self.assertEqual((none["history"].get("A", 0), none["history"]["no_lines"]), (0, 4))
        self.assertEqual(none["companies"]["GOODS"]["disagree_used"], 2)  # G07、G08 与公司选的不同
        self.assertEqual(none["companies"]["GOODS"]["detail_only"], 1)
        # 造了明细：明细过了对账闸、真写库存行的才 A；只有运费明细的 G07 要人选存货（C，载荷没出）
        answer = {row["docnum"]: row for row in replay.judge_company(m, [book], lines="answer")[0]}
        self.assertEqual([answer[number]["posting_tier_if_history"] for number in ("G04", "G05", "G06", "G07")],
                         ["A", "A", "A", "no_lines"])
        # 不含 CC 推的（下界）：G03 起判成 B、会被自动推的单线上是 CC 写的，剔掉后 G04 之前只剩 G01、G02 两张
        self.assertEqual((answer["G04"]["posting_history_manual"], answer["G04"]["posting_tier_if_history_manual"]),
                         ("", "B"))
        goods = replay.summarize_judge({"rows": list(answer.values()), "skipped": {}, "tiers": {},
                                        "goods_posting": choices})["goods"]
        self.assertEqual((goods["history"]["A"], goods["history"]["A_wrong"], goods["history"]["no_lines"]), (3, 0, 1))
        self.assertEqual(goods["history_manual"].get("A", 0), 0)
        # 与线上同一判据（用户 10-17 接线上）：档由 `automation.posting_by_history` 给，回放不另写一套
        with mock.patch.object(m["automation"], "posting_by_history", return_value="named") as judged:
            same = {row["docnum"]: row for row in replay.judge_company(m, [book], lines="answer")[0]}
        self.assertEqual(same["G04"]["posting_tier_if_history"], "named")
        # 对方没有 13 位税号（历史按名字对上）：线上封顶 B，回放照线上的 `automation.tiers` 记 keyed（第五轮后审查）
        capped = {"posting": "B"}
        with mock.patch.object(m["automation"], "tiers", side_effect=lambda facts: dict(capped)):
            named = {row["docnum"]: row for row in replay.judge_company(m, [book], lines="answer")[0]}
        self.assertEqual([named[number]["posting_tier_if_history"] for number in ("G04", "G05", "G06", "G07")],
                         ["keyed", "keyed", "keyed", "no_lines"])
        self.assertEqual(judged.call_args.args[0].posting_from, "company_choice")
        self.assertIn("「同名查找落空、记忆理想命中」", "\n".join(replay._render_goods(
            replay.summarize_judge({"rows": list(answer.values()), "skipped": {}, "tiers": {}, "goods_posting": choices,
                                    "lines": ["answer:opaque"]}))))

    def test_conflict_counts_say_what_they_count(self):
        """回放工具 10-18（第五轮「商品单与入账方式」）：``conflict_caught`` 对全部相反的单数「公司选的确实错」，不是多出 C 的子集；
        多出的 C（原本 A / B）里真拦住的另记 ``conflict_C_caught``，报表照这个说。"""
        def row(cls, posting):
            return {"company": "G", "posting_from": "company_choice", "posting_tier_if_history": "conflict", "class": cls,
                    "reasons": [], "scored": {"posting": posting}}

        rows = [row("A", "wrong"), row("B", "ok"), row("C", "wrong"), row("C", "wrong"), row("C", "n/a")]
        history = replay.goods_summary(rows, {})["history"]
        self.assertEqual((history["conflict"], history["conflict_caught"], history["conflict_C"], history["conflict_C_caught"]),
                         (5, 3, 2, 1))
        text = "\n".join(replay._render_goods({"goods": replay.goods_summary(rows, {}), "lines": ["answer:stkdes"]}))
        self.assertIn("公司选的入账方式确实错、历史是对的 3 张（全部相反的单里数，不论原本是哪类）", text)
        self.assertIn("会多出 C 2 张（原本 A / B 的，其中真拦住入账方式错的 1）", text)
        self.assertNotIn("多出 C 2 张（其中真拦住入账方式错的 3）", text)

    def test_company_posting_follows_the_majority_entered_before(self):
        """替公司选入账方式（10-17 主口径）：第一次交人那一刻之前、会因入账方式交人的单里的多数，不看以后；不到 `PRIOR_MIN` 张
        退到全期多数（审查处理 10-17）；全期多数另作对照。X1（别家）、G01 是服务，G02 起都走库存：此前多数是服务、全期多数是库存。"""
        m = replay._modules()
        days = [dt.date(2025, 1, 6) + dt.timedelta(days=7 * index) for index in range(9)]
        docs = [("X1", days[0], "S002", "51-01", "1000.00", "70.00", "3")]
        docs += [(f"G{index:02d}", day, "S001", "51-01", "1000.00", "70.00", "3") for index, day in enumerate(days[1:], 1)]
        make_book(self.root / "PRIOR", docs, stmas=self.STMAS)
        book = replay.load_book(self.root / "PRIOR", company="PRIOR", own_tax=OWN, key="C:\\PearnlyExpress\\PRIOR",
                                periods="estimated")
        book["fingerprint"] = {"stcrd_lines": 10, "stcrd_lines_moving_stock": 9}  # 永续库存：商品单交人
        for doc in book["docs"]:
            doc["stock"] = doc["docnum"] not in ("X1", "G01")
        saved = replay.PRIOR_MIN
        replay.PRIOR_MIN = 2
        try:
            choice = {}
            rows, _ = replay.judge_company(m, [book], choice=choice)
        finally:
            replay.PRIOR_MIN = saved
        first = next(row for row in rows if str(row["prepare"]).startswith("posting_needs_review"))
        self.assertEqual(first["docnum"], "G02")
        self.assertEqual(choice, {"used": "service", "by": "prior", "prior": "service", "prior_docs": 2, "full": "stock"})
        # 默认要 10 张：此前只有 2 张，退到全期多数
        fallback = {}
        replay.judge_company(m, [book], choice=fallback)
        self.assertEqual(fallback, {"used": "stock", "by": "full_fallback", "prior": None, "prior_docs": 2,
                                    "full": "stock"})
        full = {}
        replay.judge_company(m, [book], choice_mode="full", choice=full)
        self.assertEqual((full["used"], full["by"]), ("stock", "full"))
        # 人群只数会因入账方式交人的单：费用车道的收据、周期制账套的单都不算
        found = {doc["docnum"]: doc for doc in book["docs"]}
        profiles = {}
        self.assertTrue(replay.posting_population(m, book, found["G01"], days[1], profiles))
        receipt = dict(found["G01"], vat=Decimal("0.00"), total=found["G01"]["net"])
        self.assertFalse(replay.posting_population(m, book, receipt, days[1], profiles))
        periodic = dict(book, key="PERIODIC", fingerprint={"stcrd_lines": 0, "stcrd_lines_moving_stock": 0})
        self.assertFalse(replay.posting_population(m, periodic, found["G01"], days[1], {}))

    def test_upload_proxy_takes_the_earlier_of_entry_and_vat_deadline(self):
        m = replay._modules()
        day = dt.date(2025, 1, 10)
        doc = {"date": day, "entered": day + dt.timedelta(days=120), "vat_period": dt.date(2025, 1, 1)}
        deadline = replay.vat_deadline(m, 2025, 1)
        self.assertEqual(replay.upload_proxy(m, doc), (deadline, "vat_deadline"))
        self.assertEqual(replay.readback_day(m, "2025-01"), deadline + dt.timedelta(days=1))
        later = dict(doc, vat_period=dt.date(2025, 5, 1))  # 税期在录入之后：仍按录入日
        self.assertEqual(replay.upload_proxy(m, later), (doc["entered"], "entered"))
        self.assertEqual(replay.upload_proxy(m, dict(doc, vat_period=None)), (doc["entered"], "entered"))

    def test_the_alternative_upload_only_rejudges_the_date_checks(self):
        """CREDAT 比单据晚 120 天、税期是单据当月：现行按录入日 date_stale；估上传日取申报截止日，不再 stale。只重判日期核对，
        其余原因与计分不变；报表加「录入晚 60 天」一行，明细多三列。"""
        m = replay._modules()
        days = [dt.date(2025, 1, 6) + dt.timedelta(days=7 * index) for index in range(8)]
        docs = [(f"L{index:02d}", day, "S001", "52-01", "1000.00", "70.00", "3") for index, day in enumerate(days)]
        make_book(self.root / "SLOW", docs, credat={number: day + dt.timedelta(days=120) for number, day, *_ in docs},
                  vatprd={number: day.replace(day=1) for number, day, *_ in docs})
        book = replay.load_book(self.root / "SLOW", company="SLOW", own_tax=OWN, key="C:\\PearnlyExpress\\SLOW", periods="estimated")
        self.assertEqual(book["docs"][0]["vat_period"], dt.date(2025, 1, 1))
        rows, _ = replay.judge_company(m, [book])
        dates = {"date_stale", "day_month", "date_future"}
        for row in rows:
            self.assertIn("date_stale", row["reasons"])
            self.assertEqual(row["uploaded_src"], "vat_deadline")
            alt = row.get("reasons_alt", row["reasons"])
            self.assertNotIn("date_stale", alt)
            # C 类原因除日期类外一样（变成 B 的那几张，B 只列 judged:*）
            self.assertEqual({code for code in row["reasons"] if code not in dates},
                             {code for code in alt if code not in dates and not code.startswith("judged:")}, row)
        freed = [row for row in rows if row["class"] == "C" and row["class_alt"] != "C"]
        self.assertTrue(freed)
        summary = replay.summarize_judge({"rows": rows, "skipped": {}, "tiers": {}})
        upload = summary["upload_alt"]
        self.assertEqual((upload["stale"], upload["stale_alt"]), ({"purchase": len(rows)}, {}))
        self.assertEqual(upload["freed"], {"purchase": len(freed)})
        text = "\n".join(replay._render_upload(summary))
        self.assertIn(f"上传日按凭证录入日 {len(rows)} 张", text)
        out = self.root / "out"
        (out / "judge").mkdir(parents=True)
        (out / "judge" / "SLOW.json").write_text(json.dumps({"rows": rows, "skipped": {"SLOW": {}}, "tiers": {},
                                                             "lines": "none"}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            replay.main(["report", "--out", str(out)])
        header = (out / "明细.tsv").read_text(encoding="utf-8").splitlines()[0].split("\t")
        self.assertEqual(header[12:15], ["估上传日", "来源", "类别（估上传日）"])  # 10-18 起后面另有追加的列
        self.assertIn("录入晚 60 天", (out / "报表.md").read_text(encoding="utf-8"))
        self.assertIn("商品单（按公司选入账方式的单）", (out / "报表.md").read_text(encoding="utf-8"))


def cause(key):
    return next(test for name, _label, test in replay.CAUSES if name == key)


def months(year, first=1):
    """一个会计年 12 期的起止（从 ``first`` 月起）。"""
    out = []
    for step in range(12):
        start = dt.date(year + (first - 1 + step) // 12, (first - 1 + step) % 12 + 1, 1)
        following = dt.date(start.year + start.month // 12, start.month % 12 + 1, 1)
        out.append((start, following - dt.timedelta(days=1)))
    return out


def write_isprd(path, current, following=(), skip=()):
    """ISPRD.DBF（1 行，BEG / END × 12 期 × 本年、次年两段）；``skip`` 里的 (段, 期) 留空。"""
    row = {}
    for slot, periods in (("", current), ("NY", following)):
        for index, (beg, end) in enumerate(periods, start=1):
            if (slot, index) not in skip:
                row[f"BEG{index}{slot}"], row[f"END{index}{slot}"] = beg, end
    write_dbf(path, [(name, "D", 8) for name in replay.PERIOD_FIELDS], [row])


def desktop_window(path):
    """CC 桌面版 `account_probe._fiscal_window` 读同一行的结果（本机没有 dbf 包：借一个空模块导入，读表换成回放的只读读法，
    逐期取法、取最小最大都是桌面版自己的代码）。"""
    saved = {name: module for name, module in sys.modules.items() if name == "dbf" or name.split(".")[0] == "companion"}
    sys.path.insert(0, str(ROOT / "companion/src"))
    try:
        try:
            import dbf  # noqa: F401
        except ImportError:
            import types
            sys.modules["dbf"] = types.ModuleType("dbf")
        from companion import account_probe, fiscal_period
        row = next(replay.dbf_rows(path), None) or {}
        periods = tuple(fiscal_period._read_periods(row))
        original = fiscal_period.load
        fiscal_period.load = lambda _dir: fiscal_period.FiscalCalendar(
            tuple(sorted(periods, key=lambda p: p.beg))) if periods else None
        try:
            return account_probe._fiscal_window(str(path.parent))
        finally:
            fiscal_period.load = original
    finally:
        sys.path.remove(str(ROOT / "companion/src"))
        for name in [name for name in sys.modules if name == "dbf" or name.split(".")[0] == "companion"]:
            if name not in saved:
                del sys.modules[name]
        sys.modules.update(saved)


class Periods(unittest.TestCase):
    """期间与两份同期（10-17）：期间与 CC 桌面版同口径、来源优先级、strict / estimated、按日期选账套的情形与原因拆分、
    盘点的期间检验与重叠、登记表、两份同期时各挑法的模拟。"""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_fiscal_window_matches_the_desktop_reader(self):
        cases = {
            # 11 月起账、第 12 期没读出、第 1 / 2 期起止写反顺序（乱序）、有次年段
            "A": ((lambda cy: [cy[1], cy[0], *cy[2:]])(months(2024, 11)), months(2025, 11), {("", 12), ("NY", 3)}),
            # 没有次年段：次年止留空（不按本年止 + 1 年补）
            "B": (months(2025), (), set()),
        }
        for name, (current, following, skip) in cases.items():
            path = self.root / name / "ISPRD.DBF"
            path.parent.mkdir()
            write_isprd(path, current, following, skip)
            found = replay.isprd_window(path)
            want = desktop_window(path)
            self.assertEqual(found, (dt.date.fromisoformat(want["period_start"]),
                                     dt.date.fromisoformat(want["period_end"]),
                                     dt.date.fromisoformat(want["next_end"]) if "next_end" in want else None), name)
        self.assertEqual(replay.isprd_window(self.root / "A" / "ISPRD.DBF"),
                         (dt.date(2024, 11, 1), dt.date(2025, 9, 30), dt.date(2026, 10, 31)))
        self.assertEqual(replay.isprd_window(self.root / "B" / "ISPRD.DBF")[2], None)
        self.assertIsNone(replay.isprd_window(self.root / "none" / "ISPRD.DBF"))

    def test_period_sources_in_priority_order(self):
        """答案自带 ISPRD > 只读导出 > 测试账套 > 估算（strict 不报）；同名账套按本方税号分开。"""
        docs = [("RR690101-001", dt.date(2026, 1, 10), "S001", "52-01", "100.00", "7.00", "3")]
        make_book(self.root / "69ACME", docs)
        export = (dt.date(2025, 1, 1), dt.date(2025, 12, 31), dt.date(2026, 12, 31))
        book = replay.load_book(self.root / "69ACME", company="ACME", own_tax=OWN, key="69ACME", calendar=export,
                                origin="export")
        self.assertEqual((book["period"], book["period_origin"], book["period_source"]),
                         (export[:2], "export", "ISPRD"))
        estimated = replay.load_book(self.root / "69ACME", company="ACME", own_tax=OWN, key="69ACME", periods="estimated")
        self.assertEqual((estimated["period"][0], estimated["period_origin"], estimated["estimated"]),
                         (dt.date(2026, 1, 1), "estimated", True))
        strict = replay.load_book(self.root / "69ACME", company="ACME", own_tax=OWN, key="69ACME")  # 默认 strict（同 judge）
        self.assertEqual((strict["period"], strict["next_end"], strict["period_origin"]), ((None, None), None, "none"))
        write_isprd(self.root / "69ACME" / "原表" / "ISPRD.DBF", months(2026), months(2027))
        answer = replay.load_book(self.root / "69ACME", company="ACME", own_tax=OWN, key="69ACME", calendar=export,
                                  origin="export", periods="strict")
        self.assertEqual((answer["period"], answer["next_end"], answer["period_origin"]),
                         ((dt.date(2026, 1, 1), dt.date(2026, 12, 31)), dt.date(2027, 12, 31), "answer"))
        # 只读导出与测试账套：导出优先；同名两家按税号分开，分不开又不同期的不认
        exported = self.root / "导出" / "69ACME"
        exported.mkdir(parents=True)
        write_isprd(exported / "ISPRD.DBF", months(2025), months(2026))
        found = replay.fiscal_calendars(None, [], self.root / "导出")
        self.assertEqual(found.find("69acme", OWN), (export, "export"))
        calendars = replay.Calendars()
        calendars.add("testcopy", "69ACME", OWN, export)
        calendars.add("testcopy", "69ACME", OTHER, (dt.date(2026, 1, 1), dt.date(2026, 12, 31), None))
        self.assertEqual(calendars.find("69ACME", OWN), (export, "testcopy"))
        self.assertIsNone(calendars.find("69ACME", SELLER))
        self.assertEqual(calendars.conflicts(), ["testcopy:69ACME"])
        # 通用目录名只有别家的期间（写了别的税号）：不认（审查处理 10-17，原先只要只有一个期间就认）；本方税号不知道时只认唯一的
        common = replay.Calendars()
        common.add("export", "68EXP", OTHER, export)
        self.assertIsNone(common.find("68EXP", OWN))
        self.assertEqual(common.conflicts(), ["export:68EXP"])
        self.assertEqual(common.find("68EXP", ""), (export, "export"))
        common.add("export", "68EXP", "", export)
        self.assertEqual(common.find("68EXP", OWN), (export, "export"))  # 没写税号、同名各份期间一样的照认
        with self.assertRaises(SystemExit):
            replay.fiscal_calendars(None, [], pathlib.Path("S:/2569/ISPRD"))

    def books(self, m, *, two_current=True, strict_other=False):
        """68ACME（ISPRD 2025、次年段 2026）和 69ACME：``two_current`` 时 69 的 ISPRD 也是 2025（SINCERE 那种两份同期），
        否则 2026；``strict_other`` 时 69 没有 ISPRD、strict 不报期间。"""
        days = [dt.date(2025, 1, 10) + dt.timedelta(days=7 * index) for index in range(4)]
        make_book(self.root / "68ACME", [(f"RR6801{index:02d}-001", day, "S001", "52-01", "1000.00", "70.00", "3")
                                         for index, day in enumerate(days)])
        make_book(self.root / "69ACME", [("RR680100-001", days[0], "S001", "52-01", "1000.00", "70.00", "3"),
                                         *[(f"RR6901{index:02d}-001", dt.date(2026, 2, 10) + dt.timedelta(days=7 * index),
                                            "S001", "52-01", "1000.00", "70.00", "3") for index in range(3)],
                                         ("RR700301-001", dt.date(2027, 3, 1), "S001", "52-01", "1000.00", "70.00", "3")])
        write_isprd(self.root / "68ACME" / "原表" / "ISPRD.DBF", months(2025), months(2026))
        if not strict_other:
            year = 2025 if two_current else 2026
            write_isprd(self.root / "69ACME" / "原表" / "ISPRD.DBF", months(year), months(year + 1))
        return [replay.load_book(self.root / name, company="ACME", own_tax=OWN, key=f"C:\\PearnlyExpress\\{name}",
                                 periods="strict") for name in ("68ACME", "69ACME")]

    def test_strict_periods_leave_books_without_isprd_unknown(self):
        m = replay._modules()
        old, new = self.books(m, strict_other=True)
        self.assertEqual((new["period"], new["period_origin"]), ((None, None), "none"))
        rows, _ = replay.judge_company(m, [old, new])
        mine = [row for row in rows if row["book"] == new["key"]]
        self.assertTrue(mine)
        self.assertTrue(all("period_unknown" in row["reasons"] for row in mine), [row["reasons"] for row in mine])
        self.assertTrue(all(replay.primary_cause(dict(row, needed=[])) == "period_unknown" for row in mine))
        # 旧账套的单本年里照选；推到次年段要核新年度账套，同组那份期间没报：选不出
        later = replay.judge_document(m, dict(old["docs"][0], date=dt.date(2026, 3, 3), entered=dt.date(2026, 3, 5)),
                                      old, replay.Memory(), {}, [replay.account_row(old)], {}, replay.CompanyChoice(),
                                      replay.shaped_books(m, [old, new]))
        self.assertEqual((later["book_pick"], later["none_unknown"], later["outside"]), ("none", True, "next_year"))
        self.assertIn("book_none", later["reasons"])
        self.assertEqual(replay.primary_cause(dict(later, needed=[])), "book_none_unknown")
        self.assertEqual({row["book_pick"] for row in rows if row["book"] == old["key"]}, {"self"})

    def test_book_pick_codes(self):
        m = replay._modules()
        old, new = self.books(m)
        estimated = dict(new, key="C:\\PearnlyExpress\\69ACME-BAK", period_origin="estimated", folder="69ACME-BAK")
        newer = dict(new, key="C:\\PearnlyExpress\\70ACME", period=(dt.date(2026, 1, 1), dt.date(2026, 12, 31)),
                     next_end=dt.date(2027, 12, 31))
        day = dt.date(2025, 5, 1)

        def pick(books, book, when=day):
            shaped = replay.shaped_books(m, books)
            return replay.book_pick(m, shaped, book, replay.day_books(m, shaped, book, when), when)

        self.assertEqual(pick([old], old), {"book_pick": "self"})
        found = pick([old, new], old)
        self.assertEqual((found["book_pick"], found["many_estimated"], found["many_part"], len(found["many_keys"])),
                         ("many", False, "current", 2))
        self.assertEqual((found["many_origins"], found["many_parts"]), (["answer", "answer"], ["current", "current"]))
        # 逐份候选看（审查处理 10-17，原先只看第一份）：`identity.for_day` 先认本年覆盖的、没有才认次年段，同一次选出的候选
        # 总在同一段（一份本年、一份次年段时只回本年那份）
        self.assertEqual(pick([old, new], old, dt.date(2026, 5, 1))["many_parts"], ["next", "next"])
        later = dict(new, key="C:\\PearnlyExpress\\67ACME", period=(dt.date(2024, 1, 1), dt.date(2024, 12, 31)),
                     next_end=dt.date(2025, 12, 31))
        self.assertEqual(pick([old, later], old)["book_pick"], "self")
        self.assertTrue(pick([old, estimated], old)["many_estimated"])
        self.assertEqual(pick([old, new], old, dt.date(2026, 5, 1))["many_part"], "next")
        self.assertEqual(pick([old, newer], old, dt.date(2026, 5, 1)), {"book_pick": "other", "other_newer": True})
        self.assertEqual(pick([newer, old], newer, day), {"book_pick": "other", "other_newer": False})
        self.assertEqual(pick([old], old, dt.date(2028, 1, 1)), {"book_pick": "none", "none_unknown": False})
        # 回放行带上这些字段、期间来源、入账日那一段与跨年标记
        row = replay.judge_document(m, old["docs"][0], old, replay.Memory(), {}, [replay.account_row(old)], {},
                                    replay.CompanyChoice(), replay.shaped_books(m, [old, new]))
        self.assertEqual((row["book_pick"], row["period_origin"], row["posted_part"], row["crossyear"]),
                         ("many", "answer", "", False))
        self.assertIn("book_many", row["reasons"])

    def test_causes_split_real_and_estimated_overlap(self):
        base = {"reasons": ["book_many"], "needed": [], "prepare": "", "outside": ""}
        self.assertEqual(replay.primary_cause(dict(base, book_pick="many", many_estimated=False)), "book_many_real")
        self.assertEqual(replay.primary_cause(dict(base, book_pick="many", many_estimated=True)), "book_many_estimated")
        # 候选里有 70EXP 测试账套的期间：单列，不算「真」（审查处理 10-17）
        self.assertEqual(replay.primary_cause(dict(base, book_pick="many", many_estimated=False,
                                                   many_origins=["answer", "testcopy"])), "book_many_testcopy")
        self.assertEqual(replay.primary_cause(dict(base, book_pick="many", many_estimated=False,
                                                   many_origins=["answer", "export"])), "book_many_real")
        other = dict(base, reasons=["book"], book_pick="other")
        self.assertEqual(replay.primary_cause(dict(other, other_newer=True, outside="next_year")), "book_other_newer")
        self.assertEqual(replay.primary_cause(dict(other, other_newer=False, crossyear=True)), "book_other_crossyear")
        self.assertEqual(replay.primary_cause(dict(other, other_newer=False, crossyear=False)), "book_other")
        # 期间外：入账日也在期间外是期间快照与答案不同期，只有发票日在外是补抵的真问题
        late = dict(base, reasons=["period", "book_none"], book_pick="none", none_unknown=False)
        self.assertEqual(replay.primary_cause(dict(late, outside="after", posted_part="after")), "after_snapshot")
        self.assertEqual(replay.primary_cause(dict(late, outside="after", posted_part="")), "after")
        self.assertEqual(replay.primary_cause(dict(late, outside="before", posted_part="before")), "before_snapshot")
        self.assertEqual(replay.primary_cause(dict(late, outside="before", posted_part="next_year")), "before")
        # 第四轮存档（没有 book_pick）照旧口径
        self.assertEqual(replay.primary_cause({"reasons": ["book"], "needed": [], "outside": "next_year"}), "next_year")

    def test_inventory_lists_overlaps_and_posted_outside(self):
        m = replay._modules()
        corpus = self.root / "corpus"
        answers = corpus / "有答案" / f"ACME_{OWN}" / "答案"
        answers.mkdir(parents=True)
        old, new = self.books(m)
        for name in ("68ACME", "69ACME"):
            (self.root / name).rename(answers / name)
        registry = self.root / "secure" / "SCCOMP.DBF"
        registry.parent.mkdir()
        write_dbf(registry, [("COMPCOD", "C", 5), ("COMPNAM", "C", 30), ("PATH", "C", 60)],
                  [{"COMPCOD": "1", "COMPNAM": "ACME 2568", "PATH": "S:\\2568\\EXP68\\68ACME\\"}])
        self.assertEqual(replay.registered_books(registry.parent), {"68ACME": {""}})  # 取不到税号：只按目录名认
        # 登记表旁边有那份账套的 ISINFO（``<根>/<PATH>``）：按税号认，别家同名的目录不算这家登记了（审查处理 10-17）
        other = self.root / "other"
        (other / "secure").mkdir(parents=True)
        (other / "68ACME").mkdir()
        write_dbf(other / "secure" / "SCCOMP.DBF", [("COMPCOD", "C", 5), ("PATH", "C", 60)],
                  [{"COMPCOD": "9", "PATH": "68ACME"}])
        write_dbf(other / "68ACME" / "ISINFO.DBF", [("TAXID", "C", 15)], [{"TAXID": SELLER}])
        found = replay.registered_books(other)
        self.assertEqual(found, {"68ACME": {SELLER}})
        self.assertFalse(replay.is_registered({"folder": "68ACME", "key": "68ACME", "own_tax": OWN}, found))
        self.assertTrue(replay.is_registered({"folder": "68ACME", "key": "68ACME", "own_tax": SELLER}, found))
        out = self.root / "out"
        with contextlib.redirect_stdout(io.StringIO()):
            replay.main(["inventory", "--corpus", str(corpus), "--registry", str(registry), "--out", str(out)])
        data = json.loads((out / "inventory.json").read_text(encoding="utf-8"))
        books = {row["book"]: row for row in data["books"]}
        self.assertEqual({name: (row["period_origin"], row["shared_docs"], row["registered"], row["posted_outside"])
                          for name, row in books.items()},
                         {"68ACME": ("answer", 1, True, 0), "69ACME": ("answer", 1, False, 1)})
        self.assertEqual(books["69ACME"]["last_entered"], "2027-03-03")
        # 商品单与上传日（10-17）：列名清单、录入滞后 / 税期滞后分桶、成批录入的日子、明细挂单统计
        self.assertIn("VATPRD", books["68ACME"]["columns"]["ISVAT"])
        self.assertEqual(books["68ACME"]["columns"]["STCRD"], [])
        self.assertEqual(books["68ACME"]["entry_lag"], {"<=7": 4})
        self.assertEqual(books["68ACME"]["vat_lag"], {"none": 4})
        self.assertEqual((books["68ACME"]["batch_days"], books["68ACME"]["stcrd"]), ([], {"lines": 0}))
        self.assertEqual(replay._lag_bucket(45), "<=60")
        self.assertEqual(replay._month_bucket(5), "4-6")
        overlap = data["overlaps"]["ACME"]
        self.assertEqual(len(overlap), 1)
        self.assertEqual((overlap[0]["current"], overlap[0]["next"], overlap[0]["only"]),
                         (["2025-01-01", "2025-12-31"], ["2026-01-01", "2026-12-31"], [{"current": 3}, {"next": 3}]))
        text = replay.render(None, None, data)
        self.assertIn("同分店两两期间重叠 1 对", text)
        # 给了登记表：选账套只认登记了的，没登记那份里的单选出的是另一份
        rows, _ = replay.judge_company(m, [old, new], {"68ACME": {OWN}})
        mine = [row for row in rows if row["book"] == new["key"] and row["period"].startswith("2026")]
        self.assertTrue(mine)
        self.assertTrue(all(row["book_pick"] == "other" and row["registered"] is False for row in mine))

    def test_tiebreak_on_two_books_of_the_same_year(self):
        """SINCERE 那种两份 ISPRD 都是同一年：本年的单选出两份。ask_once 按本年 / 次年段各问一次，之后照选；第一张是两份都有
        的复制单（回放在单多的 69 里），答案账套说不准，不记、下一张再问。"""
        m = replay._modules()
        old, new = self.books(m)
        rows, skipped = replay.judge_company(m, [old, new])
        self.assertEqual(skipped["copy_in_other_book"], 1)
        self.assertEqual({row["book_pick"] for row in rows if row["period"] < "2027"}, {"many"})
        self.assertTrue(all(replay.primary_cause(dict(row, needed=[])) == "book_many_real"
                            for row in rows if row["period"] < "2027"))
        found = replay.tiebreak(m, rows, [old, new])
        self.assertEqual(found["ask_once"], {"asked": 3, "shared": 1, "right": 4, "stopped": 2, "freed": 4})
        self.assertEqual(found["none"], {"shared": 1, "stopped": 6})

    def test_tiebreak_simulations(self):
        m = replay._modules()

        def book(name, entries):
            return {"key": f"C:\\X\\{name}", "folder": name, "own_tax": OWN, "branch": "00000",
                    "period": (dt.date(2025, 1, 1), dt.date(2025, 12, 31)), "next_end": None,
                    "entry_days": sorted(entries)}

        one, two, three = (book("68A", [dt.date(2025, 1, 5)]), book("69A", [dt.date(2025, 1, 3)]),
                           book("70A", [dt.date(2025, 1, 5)]))
        key = {item["folder"]: replay.book_key(m, item) for item in (one, two, three)}

        def row(owner, keys, entered, unique=True, reasons=("book_many", "needed")):
            return {"book": owner["key"], "book_pick": "many", "many_keys": sorted(key[name] for name in keys),
                    "many_part": "current", "entered": entered, "unique": unique, "class": "C",
                    "reasons": list(reasons)}

        rows = [row(one, ["68A", "69A"], "2025-01-10"), row(two, ["68A", "69A"], "2025-01-10"),
                row(one, ["68A", "69A"], "2025-01-04", reasons=("book_many",)), row(one, ["68A", "70A"], "2025-01-10"),
                row(one, ["68A", "69A"], "2025-01-11", unique=False)]
        found = replay.tiebreak(m, rows, [one, two, three])
        self.assertEqual(found["none"], {"stopped": 4, "shared": 1})
        # 最近有录入：第一张 68A 最近；第三张录入前 68A 还没录入、选了 69A；第四张两份同一天、并列停下
        self.assertEqual(found["recent_entry"], {"right": 1, "wrong": 2, "stopped": 1, "shared": 1})
        # 之前推进去的（各挑法各记各的，审查处理 10-17）：第一张没有、停下由人推进 68A；第二张选 68A 错，这张也就推进了 68A；
        # 第三张最近推进的仍是 68A、对（只差这一项、不再是 C）；第四张 68A 对
        self.assertEqual(found["recent_push"], {"stopped": 1, "wrong": 1, "right": 2, "freed": 1, "shared": 1})
        # 问一次：第一张问人（停）、记下 68A；同组第二张照选错、第三张对（只差这一项、不再是 C）；组合变了重问
        self.assertEqual(found["ask_once"], {"asked": 2, "stopped": 2, "wrong": 1, "right": 1, "freed": 1,
                                             "shared": 1})
        summary = replay.summarize_judge({"rows": [], "skipped": {}, "tiers": {}, "tiebreak": {"ACME": found},
                                          "origins": {"ACME": {"answer": 2}}, "periods": ["strict"]})
        text = "\n".join(replay._render_books(summary))
        self.assertIn("| ask_once | 1 | 1 | 2 | 2 | 25.00% | 1 | 1 |", text)
        self.assertIn("期间口径 strict", text)

    def test_detail_rows_carry_the_new_per_document_columns(self):
        """回放工具 10-18（第五轮「还差在哪」第 5 条）：小合成样本（SINCERE 那种两份同年）跑 judge + report，`明细.tsv` 旧的
        15 列顺序不变，后面追加 memory_cap_lost / book_pick / book_candidates / book_choice / posting_history /
        posting_history_manual；两份同期的行有候选与各挑法的选择，其余行这几列为空或是情形；summary 记下 reask 开关。"""
        m = replay._modules()
        corpus = self.root / "corpus"
        answers = corpus / "有答案" / f"ACME_{OWN}" / "答案"
        answers.mkdir(parents=True)
        old, new = self.books(m)
        for name in ("68ACME", "69ACME"):
            (self.root / name).rename(answers / name)
        out = self.root / "out"
        with contextlib.redirect_stdout(io.StringIO()):
            replay.main(["judge", "--corpus", str(corpus), "--out", str(out)])
            replay.main(["report", "--out", str(out)])
        with open(out / "明细.tsv", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle, delimiter="\t")
            header = next(reader)
            rows = [dict(zip(header, line)) for line in reader]
        self.assertEqual(header[:15], ["层", "公司", "账套", "单号", "方向", "期间", "录入日", "类别", "原因", "要选", "计分",
                                       "全对", "估上传日", "来源", "类别（估上传日）"])
        self.assertEqual(header[15:], ["memory_cap_lost", "book_pick", "book_candidates", "book_choice", "posting_history",
                                       "posting_history_manual"])
        self.assertTrue(rows and all(len(row) == len(header) for row in rows))
        many = [row for row in rows if row["book_pick"] == "many"]
        self.assertTrue(many)
        keys = {"68acme", "69acme"}
        for row in many:
            self.assertEqual({key.rsplit("\\", 1)[-1] for key in row["book_candidates"].split("|")}, keys)
            choice = json.loads(row["book_choice"])
            self.assertEqual(set(choice) - {"reasked"}, set(replay.TIEBREAKS))
            self.assertTrue(all(value == "" or value.rsplit("\\", 1)[-1] in keys
                                for name, value in choice.items() if name != "reasked"))
            self.assertEqual(choice["none"], "")
        self.assertTrue(any(json.loads(row["book_choice"])["ask_once"] for row in many))  # 问过一次之后照选
        for row in rows:
            if row["book_pick"] != "many":
                self.assertEqual((row["book_candidates"], row["book_choice"]), ("", ""))
            self.assertTrue(set(filter(None, row["memory_cap_lost"].split(","))) <= {"account", "payment", "party"})
            self.assertRegex(row["posting_history"], r"^((A|B|conflict|no_lines|items|named|keyed)(:(stock|service))?)?$")
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))["judge"]
        self.assertEqual(summary["reask"], ["written"])
        # 模拟的人复核时改值与三条收严（10-18 E1）：默认同推荐方案，存档记参数
        self.assertEqual((summary["review"]["classes"], summary["review"]["lag"], summary["rules"]),
                         ([["B", "Cn", "Cr"]], [1], [replay.MEMORY_RULES]))
        self.assertEqual(set(summary["tightening"]), {"holds", "tightened"})
        # 单行的取值：上限挤掉的记忆项、按公司选入账方式的档加历史定下的记法
        extra = replay.detail_extra({"memory_cap_lost": ["account", "party"], "book_pick": "self",
                                     "posting_tier_if_history": "conflict", "posting_history": "stock",
                                     "posting_tier_if_history_manual": "B", "posting_history_manual": ""})
        self.assertEqual(extra, ["account,party", "self", "", "", "conflict:stock", "B"])
        self.assertEqual(replay.detail_extra({}), [""] * len(replay.DETAIL_EXTRA))

    def test_ask_once_asks_again_after_a_newer_entry_in_the_book_not_chosen(self):
        """用户 10-18 加重问（线上 `companies.current_book`，判据同 `document_policy.current_book_newer`）：回答时记下各候选的
        最新录入，之后未被选中的那份更晚就停下再问（模拟的人按答案再选一次），选中的那份自己更晚不算。``written``（默认，同线上）
        按 ask_once 模拟里最近一次推进去的那张；``entered`` 按 Express 录入日（对照）；``off`` 不重问。"""
        m = replay._modules()

        def book(name, entries):
            return {"key": f"C:\\X\\{name}", "folder": name, "own_tax": OWN, "branch": "00000",
                    "period": (dt.date(2025, 1, 1), dt.date(2025, 12, 31)), "next_end": None,
                    "entry_days": sorted(entries)}

        one = book("68A", [dt.date(2025, 1, 9), dt.date(2025, 1, 15)])
        two = book("69A", [dt.date(2025, 1, 10), dt.date(2025, 1, 12)])
        both = sorted(replay.book_key(m, item) for item in (one, two))

        def many(owner, entered):
            return {"book": owner["key"], "book_pick": "many", "many_keys": both, "many_part": "current",
                    "entered": entered, "unique": True, "class": "C", "reasons": ["book_many", "needed"]}

        def single(owner, entered):
            return {"book": owner["key"], "book_pick": "self", "entered": entered, "class": "A", "reasons": []}

        # 第一张问人、记下 68A；第二张 69A 的单照选错；第三张只进 69A（推进去了）；第四张 69A 在回答之后有了录入 → 重问、
        # 改记 69A；第五张对；第六张只进 68A；第七张 68A（这时没被选中）更晚 → 再问
        rows = [many(one, "2025-01-10"), many(two, "2025-01-11"), single(two, "2025-01-12"), many(two, "2025-01-13"),
                many(two, "2025-01-14"), single(one, "2025-01-15"), many(two, "2025-01-16")]
        written = replay.tiebreak(m, rows, [one, two], ["ask_once"])["ask_once"]
        self.assertEqual(written, {"asked": 3, "reasked": 2, "stopped": 3, "wrong": 1, "right": 1})
        self.assertEqual(written, replay.tiebreak(m, rows, [one, two], ["ask_once"], reask="written")["ask_once"])
        # Express 录入日：69A 01-10 有录入（第二张录入之前）就重问，第二张起就选对
        entered = replay.tiebreak(m, rows, [one, two], ["ask_once"], reask="entered")["ask_once"]
        self.assertEqual(entered, {"asked": 3, "reasked": 2, "stopped": 3, "right": 2})
        off = replay.tiebreak(m, rows, [one, two], ["ask_once"], reask="off")["ask_once"]
        self.assertEqual(off, {"asked": 1, "stopped": 1, "wrong": 4})
        # 别的挑法不受影响
        self.assertEqual(replay.tiebreak(m, rows, [one, two], ["recent_push"], reask="off"),
                         replay.tiebreak(m, rows, [one, two], ["recent_push"]))
        with self.assertRaises(ValueError):
            replay.tiebreak(m, rows, [one, two], ["ask_once"], reask="always")
        # 存档与汇总记下开关（旧存档没有这一项按 off），报表写出重问次数
        out = self.root / "reask"
        (out / "judge").mkdir(parents=True)
        for name, part in (("A", {"reask": "written", "tiebreak": {"A": {"ask_once": written}}}), ("B", {})):
            (out / "judge" / f"{name}.json").write_text(json.dumps(
                {"rows": [], "skipped": {name: {}}, "tiers": {}, **part}), encoding="utf-8")
        summary = replay.summarize_judge(replay.load_judge(out))
        self.assertEqual(summary["reask"], ["off", "written"])
        self.assertIn("停下再问 2 次", "\n".join(replay._render_books(summary)))


class Scoring(unittest.TestCase):
    def test_score_items(self):
        answer = {"party": "S1", "account": "*multi", "payment": "cash"}
        self.assertEqual(replay.score(answer, {"party": "S1", "account": "52", "payment": "credit", "vat": "claim"}),
                         {"party": "ok", "account": "wrong:multi_account", "payment": "wrong:credit", "vat": "n/a",
                          "posting": "n/a", "item": "n/a"})
        self.assertEqual(replay.score({"posting": "service"}, {"posting": "stock"})["posting"], "wrong:stock")
        # 存货逐行（10-17）：答案库存行的码按行序成元组
        self.assertEqual(replay.score({"item": ("P01", "P02")}, {"item": ("P01", "P02")})["item"], "ok")
        self.assertEqual(replay.score({"item": ("P01",)}, {"item": ("new", "P02")})["item"], "wrong:new+P02")
        self.assertEqual(replay.score({"item": ("P01",)}, {"item": ()})["item"], "wrong:none")

    def test_pushed_evidence_matches_the_online_rule(self):
        """回放的「连续 3 张」与线上 `assess._recent` 同口径（审查处理 10-16；10-17 推广到现 / 赊与对方档）：凭记忆推出去的要这期
        合格回读在这张录入前核过才算，整张记录推错（与会计最终记的有任一项不同）在回读里就是被改、三类一起断开；人选的照算。"""
        m = replay._modules()
        jan = {"period": "2025-01", "entered": dt.date(2025, 1, 20), "payment": "RR", "payment_source": "remembered",
               "party": "S001", "party_source": "", "branch": "00000", "ok": True}
        log = [dict(jan, account="52-01", source="chosen"),
               dict(jan, account="52-01", source="remembered"),
               dict(jan, account="52-01", source="remembered", ok=False),
               dict(jan, account="52-01", source="remembered")]
        before = replay.pushed_evidence(m, log, dt.date(2025, 2, 1))
        self.assertEqual(before, ["52-01"])  # 一月期的回读还没做：只有人选的那张
        after = replay.pushed_evidence(m, log, dt.date(2025, 3, 10))
        self.assertEqual(after, ["52-01", "", "52-01", "52-01"])  # 第三张推错：被改、断开
        late = [dict(log[0], entered=dt.date(2025, 3, 1), source="remembered")]  # 推在回读之后：没被覆盖
        self.assertEqual(replay.pushed_evidence(m, late, dt.date(2025, 3, 10)), [])
        # 现 / 赊、对方档：同一份记录，各自的证据口径；对方档只数同一分店
        self.assertEqual(replay.pushed_evidence(m, log, dt.date(2025, 2, 1), "payment"), [])
        self.assertEqual(replay.pushed_evidence(m, log, dt.date(2025, 3, 10), "payment"), ["RR", "", "RR", "RR"])
        manual = [dict(jan, payment_source="manual", entered=dt.date(2025, 3, 1))]
        self.assertEqual(replay.pushed_evidence(m, manual, dt.date(2025, 3, 10), "payment"), ["RR"])
        other = log + [dict(jan, account="52-01", source="remembered", branch="00001")]
        self.assertEqual(replay.pushed_evidence(m, other, dt.date(2025, 3, 10), "party", "00000"),
                         ["S001", "", "S001", "S001"])
        self.assertEqual(replay.pushed_evidence(m, other, dt.date(2025, 3, 10), "party", "00001"), ["S001"])
        # 推不出这一类（None）：跳过
        unknown = [dict(jan, account=None, payment=None, party=None)] + log
        self.assertEqual(replay.pushed_evidence(m, unknown, dt.date(2025, 3, 10)), after)

    def test_pushed_record_scores_the_whole_record(self):
        """推出去的单整张对才算回读里没被改（`pushed_record`）：科目对、现 / 赊错也是被改；要人选的按答案；没载荷只比要人选的。"""
        answer = {"party": "S001", "account": "52-01", "payment": "credit", "vat": "claim"}
        payload = {"supplier": {"code": "S001"}, "items_account": "52-01", "account_source": "remembered",
                   "doctype": "HP", "doctype_src": "remembered"}
        values = {"party": "S001", "account": "52-01", "payment": "cash", "vat": "claim"}
        found = replay.pushed_record(answer, payload, values, [])
        self.assertEqual((found["ok"], found["account"], found["payment"], found["party"]),
                         (False, "52-01", "HP", "S001"))
        found = replay.pushed_record(answer, payload, values, ["payment"])
        self.assertEqual((found["ok"], found["payment"], found["payment_source"]), (True, "RR", "remembered"))
        found = replay.pushed_record(answer, None, {}, ["account"])
        self.assertEqual((found["ok"], found["account"], found["source"], found["payment"]),
                         (True, "52-01", "chosen", None))
        multi = replay.pushed_record({**answer, "account": "*multi"}, None, {}, ["account"])
        self.assertFalse(multi["ok"])

    def test_evidence_window_skips_unchecked_memory_pushes_like_online(self):
        """同线上 `assess._recent`（10-17 改取法，同一个 `automation.memory_window`）：先跳过凭记忆推出去、还没回读核过的，再在
        能当证据的里取最近的；往回最多看 `RECENT_MAX` 张推送（同线上 `automation.RECENT_SCAN_MAX`，先截、再按分店筛），0 不设上限。"""
        m = replay._modules()
        old = {"period": "2025-01", "entered": dt.date(2025, 1, 20), "account": "52-01", "source": "remembered",
               "payment": "RR", "payment_source": "remembered", "party": "S001", "party_source": "", "branch": "00000",
               "ok": True}
        fresh = dict(old, period="2025-03", entered=dt.date(2025, 3, 20), branch="00001")
        log = [old] * 3 + [fresh] * 30
        self.assertEqual((replay.RECENT_MAX, m["automation"].RECENT_SCAN_MAX), (1000, 1000))
        # 最新 30 张都还没回读核过（三月期的回读要到四月申报截止后）：跳过，更早一月期核过的三张接上
        self.assertEqual(replay.pushed_evidence(m, log, dt.date(2025, 3, 25)), ["52-01"] * 3)
        self.assertEqual(replay.pushed_evidence(m, log, dt.date(2025, 3, 25), "payment"), ["RR"] * 3)
        self.assertEqual(replay.pushed_evidence(m, log, dt.date(2025, 3, 25), "party", "00000"), ["S001"] * 3)
        self.assertEqual(replay.pushed_evidence(m, log, dt.date(2025, 3, 25), limit=0), ["52-01"] * 3)
        # 往回看的上限照样生效（先截、再筛）：只看 30 张时那三张落在外面（第五轮的旧取法就是这个结果）
        self.assertEqual(replay.pushed_evidence(m, log, dt.date(2025, 3, 25), limit=30), [])
        self.assertEqual(replay.pushed_evidence(m, log, dt.date(2025, 3, 25), "party", "00000", limit=30), [])
        # 没有任何回读核过（一月期的回读也还没做）：仍是零张
        self.assertEqual(replay.pushed_evidence(m, log, dt.date(2025, 2, 1)), [])
        # 推的时候由人定的照常算证据、回读里被改的照常断开
        chosen = dict(fresh, account="52-02", source="chosen")
        self.assertEqual(replay.pushed_evidence(m, log + [chosen], dt.date(2025, 3, 25)), ["52-02"] + ["52-01"] * 3)
        wrong = [old, dict(old, ok=False), old] + [fresh] * 30
        self.assertEqual(replay.pushed_evidence(m, wrong, dt.date(2025, 3, 25)), ["52-01", "", "52-01"])
        # 与线上同一个函数：回放造的条目直接交 `memory_window` 得到同样的序列
        entries = [{"account": "52-01", "source": "remembered", "readback": ""}] * 30 + \
            [{"account": "52-01", "source": "remembered", "readback": "confirmed"}] * 3
        self.assertEqual(m["automation"].memory_window(entries), ["52-01"] * 3)
        # 往回最多跨 6 个税期（同线上 `automation.RECENT_PERIOD_MAX`，同一个 `window_take`）：第 7 期核过的三张够不着；
        # 不给税期上限时跟 ``limit``，``limit=0`` 一并不设上限（第四轮对照口径不变）
        self.assertEqual((replay.RECENT_PERIODS, m["automation"].RECENT_PERIOD_MAX), (6, 6))
        # 别的分店（对方档只数同一分店的）这六期推的单也占税期（线上照样为它们查回读），一月期核过的三张落在第 7 期
        far = [dict(old, period="2024-01", entered=dt.date(2024, 1, 20))] * 3
        months = [dict(fresh, period=f"2024-{month:02d}", entered=dt.date(2024, month, 20)) for month in range(2, 8)]
        slow = far + months
        day = dt.date(2024, 7, 25)
        self.assertEqual(replay.pushed_evidence(m, slow, day, "party", "00000"), [])
        self.assertEqual(replay.pushed_evidence(m, slow, day, "party", "00000", limit=0), ["S001"] * 3)
        self.assertEqual(replay.pushed_evidence(m, slow, day, "party", "00000", periods=0), ["S001"] * 3)
        self.assertEqual(replay.pushed_evidence(m, slow, day, "party", "00000", periods=7), ["S001"] * 3)
        self.assertEqual(replay.pushed_evidence(m, slow[:2] + months[1:], day, "party", "00000"), ["S001"] * 2)
        self.assertEqual((replay.window_periods(1000), replay.window_periods(0), replay.window_periods(0, 4)), (6, 0, 4))
        # 判定层：上限小到凑不够 3 张时，记忆定的项是 B，记下因上限丢掉的类
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        # 一月要有 5 张（科目连续 5 张，R3，10-18）：从 1-03 起每周一张
        days = [dt.date(2025, 1, 3) + dt.timedelta(days=7 * index) for index in range(10)]
        make_book(pathlib.Path(folder.name, "CAP"), [(f"RR{index:02d}", day, "S001", "52-01", "1000.00", "70.00", "3")
                                                     for index, day in enumerate(days)])
        book = replay.load_book(pathlib.Path(folder.name, "CAP"), company="CAP", own_tax=OWN, key="CAP",
                                periods="estimated")
        loose = {row["docnum"]: row for row in replay.judge_company(m, [book])[0]}
        capped = {row["docnum"]: row for row in replay.judge_company(m, [book], recent_max=2)[0]}
        self.assertEqual(loose["RR09"]["items"]["account"], "A")
        self.assertEqual((capped["RR09"]["items"]["account"], capped["RR09"]["memory_cap_lost"]),
                         ("B", ["account", "payment"]))
        self.assertEqual(loose["RR09"]["memory_cap_lost"], [])

    def test_pushed_record_party_stock_and_scope(self):
        """推送记录（审查处理 10-17）：对方档取桌面版实际写的码（载荷没写码的按找档，新建 / 定不出记空串）；真写了库存行记
        ``stock``；整张记录也比入账方式与存货；``account`` 口径只比科目（第四轮对照）。"""
        answer = {"party": "S001", "account": "52-01", "payment": "credit", "vat": "claim", "posting": "stock",
                  "item": ("P01",)}
        payload = {"supplier": {"tax_id": SELLER}, "items_account": "52-01", "account_source": "remembered",
                   "doctype": "RR", "doctype_src": "remembered", "items_status": "ok",
                   "items": [{"name": "x", "item_mode": "stock_item", "code": "P01"}]}
        values = {"party": "S001", "account": "52-01", "payment": "credit", "vat": "claim", "posting": "stock",
                  "item": ("P01",)}
        found = replay.pushed_record(answer, payload, values, [])
        self.assertEqual((found["ok"], found["party"], found["stock"]), (True, "S001", True))
        self.assertEqual(replay.pushed_record(answer, payload, dict(values, party="new"), [])["party"], "")
        wrong = replay.pushed_record(answer, payload, dict(values, posting="service"), [])
        self.assertFalse(wrong["ok"])
        self.assertTrue(replay.pushed_record(answer, payload, dict(values, posting="service"), [], "account")["ok"])
        self.assertFalse(replay.pushed_record(answer, payload, dict(values, item=("P02",)), [])["ok"])

    def test_cc_party_follows_the_desktop_cascade(self):
        masters = [{"code": "A", "name": "ผู้ขาย", "tax_id": SELLER, "branch": "00000"},
                   {"code": "B", "name": "ผู้ขาย", "tax_id": OTHER, "branch": "00000"},
                   {"code": "C", "name": "ผู้ขาย", "tax_id": OTHER, "branch": "00001"},
                   {"code": "D", "name": "ร้านค้า", "tax_id": "", "branch": "00000"}]
        self.assertEqual(replay.cc_party(masters, SELLER, "x", "00000"), "A")
        self.assertEqual(replay.cc_party(masters, SELLER, "x", "00002"), "new")
        self.assertEqual(replay.cc_party(masters, OTHER, "ผู้ขาย", "00001"), "C")
        self.assertEqual(replay.cc_party(masters, OTHER, "ผู้ขาย", ""), "ambiguous")
        self.assertEqual(replay.cc_party(masters, "", "ร้านค้า", ""), "D")
        self.assertEqual(replay.cc_party(masters, "", "ใหม่", ""), "new")


class GateLayer(unittest.TestCase):
    GOLD = {"page": 1, "invoice_number": "IV-0012", "date": "2025-03-04", "seller_tax": SELLER, "seller_branch": "00000",
            "subtotal": "1000.00", "vat": "70.00", "total": "1070.00"}

    def test_express_answers_score_only_what_pairing_did_not_use(self):
        """Express 答案作金标：答案是靠识别结果配上的，配对用到的项是自证（self），不计分（审查处理 10-16）。"""
        fields = {"invoice_number": "IV-0012", "date": "2025-03-05", "seller_tax": SELLER, "seller_branch": "00001",
                  "subtotal": "1000.00", "vat": "70.00", "total_amount": "1070.00"}
        gold = {**self.GOLD, "matched_on": ["amount", "party_tax"]}
        found = replay.compare_gold(fields, gold, "purchase", replay.EXPRESS_GOLD)
        self.assertEqual(found, {"invoice_number": "ok", "date": "wrong", "party_tax": "self", "party_branch": "wrong",
                                 "subtotal": "self", "vat": "self", "total": "self"})
        self.assertEqual(replay.compare_gold(fields, gold, "purchase")["party_tax"], "ok")  # 独立金标照常比

    def test_vat_claim_against_the_gold_kind(self):
        """抵不抵进项（审查处理 10-16）：识别按方向推断，原文核得到完整税票与买方税号才算 A；与金标的票种口径比。"""
        m = replay._modules()
        fields = {"document_type": "tax_invoice", "buyer_tax": OWN, "buyer_name": "ACME", "vat": "70.00"}
        full = f"ใบกำกับภาษี ผู้ซื้อ {OWN} " * 5
        short = f"ใบกำกับภาษีอย่างย่อ {OWN} " * 5
        gold = {"vat": "70.00", "buyer_tax": OWN}
        self.assertEqual(replay.vat_claim(m, fields, full, gold, "tax_invoice", ""),
                         {"claims": True, "tier": "A", "gold": True, "result": "ok"})
        # 识别把简式税票读成完整税票：判抵扣，但原文是简式 → B，与金标不符
        self.assertEqual(replay.vat_claim(m, fields, short, gold, "receipt", ""),
                         {"claims": True, "tier": "B", "gold": False, "result": "wrong"})
        self.assertEqual(replay.vat_claim(m, fields, full, gold, "utility", "")["result"], "n/a")
        self.assertEqual(replay.vat_claim(m, {**fields, "document_type": "receipt"}, full, gold, "tax_invoice", ""),
                         {"claims": False, "tier": "B", "gold": True, "result": "wrong"})
        self.assertEqual(replay.vat_claim(m, {**fields, "document_type": "receipt"}, "", gold, "tax_invoice", "")["tier"],
                         "B")  # 判不抵、核不到原文：B
        self.assertEqual(replay.vat_claim(m, {**fields, "document_type": "receipt"}, short, gold, "receipt", "")["tier"],
                         "A")
        self.assertEqual(replay.vat_claim(m, {**fields, "document_type": "other"}, short, gold, "utility", "")["tier"],
                         "B")
        self.assertTrue(replay.claim_gold({"vat": "7.00"}, "", replay.EXPRESS_GOLD))
        self.assertFalse(replay.claim_gold({"vat": "0"}, "", replay.EXPRESS_GOLD))
        self.assertFalse(replay.claim_gold({"vat": "7.00"}, "tax_invoice", ""))  # 没印买方

    def test_compare_against_the_gold(self):
        fields = {"invoice_number": "0012", "date": "2025-03-04", "seller_tax": SELLER, "seller_branch": "0",
                  "subtotal": "1000", "vat": "70.00", "total_amount": "1070.001"}
        self.assertEqual(replay.compare_gold(fields, self.GOLD, "purchase"),
                         {"invoice_number": "prefix_only", "date": "ok", "party_tax": "ok", "party_branch": "ok",
                          "subtotal": "ok", "vat": "ok", "total": "ok"})
        wrong = replay.compare_gold({**fields, "date": "2025-04-03", "vat": None}, self.GOLD, "purchase")
        self.assertEqual((wrong["date"], wrong["vat"]), ("wrong", "wrong"))
        self.assertEqual(replay.compare_gold(fields, {"page": 1}, "purchase")["date"], "n/a")

    def test_pairing(self):
        golds = [{"page": 1, "invoice_number": "A1", "total": "5.00"}, self.GOLD]
        invoice = {"page": 1, "fields": {"invoice_number": "IV0012", "total_amount": "1070.00"}}
        self.assertEqual(replay.pair_gold(invoice, golds), 1)
        self.assertIsNone(replay.pair_gold({"page": 3, "fields": {"total_amount": "9"}}, golds))

    def test_gate_uses_the_text_layer_and_recognition_flags(self):
        m = replay._modules()
        fields = {"document_type": "tax_invoice", "invoice_number": "IV-0012", "date": "2025-03-04",
                  "seller_tax": SELLER, "seller_branch": "00000", "buyer_tax": OWN, "buyer_branch": "00000",
                  "subtotal": "1000.00", "vat": "70.00", "total_amount": "1070.00"}
        text = [f"ใบกำกับภาษี {SELLER} IV-0012 วันที่ 04/03/2568 รวมทั้งสิ้น 1,070.00 " + "x" * 200]
        invoice = {"page": 1, "fields": fields, "review": False, "warnings": []}
        self.assertEqual(replay.gate_invoice(m, invoice, direction="purchase", own_tax=OWN, texts=text,
                                             source_total=1), [])
        self.assertEqual(replay.gate_invoice(m, invoice, direction="purchase", own_tax=OWN, texts=None,
                                             source_total=1), ["image"])
        flagged = {**invoice, "review": True}
        self.assertIn("review_flag", replay.gate_invoice(m, flagged, direction="purchase", own_tax=OWN, texts=text,
                                                         source_total=1))
        self.assertIn("text_total", replay.gate_invoice(m, {**invoice, "fields": {**fields, "total_amount": "1170.00",
                                                                                   "subtotal": "1100.00"}},
                                                        direction="purchase", own_tax=OWN, texts=text, source_total=1))
        # 跨页的票按原件页数逐页判文字层（审查处理 10-16）：第二页没有文字层就是图片单
        two = {**invoice, "pages": 2}
        self.assertIn("image", replay.gate_invoice(m, two, direction="purchase", own_tax=OWN, texts=text + [""],
                                                   source_total=1))
        self.assertEqual(replay.gate_invoice(m, two, direction="purchase", own_tax=OWN, texts=text + ["y" * 220],
                                             source_total=1), [])

    def test_passed_but_unpaired_counts_as_wrong(self):
        row = {"id": "1", "form": "电子PDF", "company": "ACME", "direction": "purchase", "page": 1, "text_pdf": True,
               "reasons": [], "passed": True, "entry": "enter", "paired": True, "result": {"date": "ok"},
               "uncertain": [], "wrong": []}
        found = replay.summarize_gate({"rows": [row, {**row, "id": "2", "paired": False, "result": {}}]})
        self.assertEqual((found["text_pdf"]["n"], found["text_pdf"]["ok"], found["text_pdf"]["unpaired_passed"]),
                         (2, 1, 1))

    def test_the_gate_needs_the_manifest_and_writes_are_refused_on_the_lan(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            replay.main(["gate", "--gold", "/tmp/g", "--originals", "/tmp/o", "--out", "/tmp/x"])
        with self.assertRaises(SystemExit):
            replay.main(["report", "--out", "/mnt/s/EXP69/out"])
        with self.assertRaises(SystemExit):
            replay._write_json(pathlib.Path("S:/x/gate.json"), {})


if __name__ == "__main__":
    unittest.main()
