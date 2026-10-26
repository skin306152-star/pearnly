"""离线自检：不连测试机、不调 API、不花钱。

    cd scripts/express-operator && python3 -m pilot.selftest

覆盖：价格换算（含 Haiku 超 10 万 token 档）、预算上限与每 10 美元报告点、DBF 读取、读回核对、
执行器在假 Express 上的各种情形（弹窗认得出 / 认不出、陌生弹窗、Haiku 搞不定升级 Opus、全程看图、
Express 报 -70、负库存被拦、录错由读回查出、到预算上限）、语料筛选与换主档、代理的 PNG 编码。
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import tempfile
import unittest
import zlib
from decimal import Decimal
from pathlib import Path

from . import compare as C
from . import corpus, dbfread, pricing
from . import engine as E
from .draft import Draft, Line, samples
from .fake_express import FakeClaude, FakeExpress
from .ledger import BudgetStop, Ledger

HERE = Path(__file__).resolve().parent
FLOWS = json.loads((HERE / "flows.json").read_text(encoding="utf-8"))


def run_docs(drafts, mode="haiku_fixed", ledger=None, **fake):
    haiku_gives_up = fake.pop("haiku_gives_up", False)
    blind = fake.pop("blind", False)
    sim = FakeExpress(FLOWS, **fake)
    claude = FakeClaude(sim, haiku_gives_up=haiku_gives_up, blind=blind)
    tmp = Path(tempfile.mkdtemp())
    ledger = ledger or Ledger(tmp / "ledger.jsonl")
    eng = E.Engine(sim, claude, ledger, FLOWS, "BOOK", mode, tmp, log=lambda *_: None)
    out = []
    for d in drafts:
        claude.draft = d
        sim.current_doc_id = d.doc_id
        out.append(eng.run(d))
    return out, sim, claude, ledger


class Pricing(unittest.TestCase):
    def test_opus(self):
        u = {"input_tokens": 10_000, "output_tokens": 1_000, "cache_read_input_tokens": 50_000,
             "cache_creation_input_tokens": 4_000}
        # 10k×4 + 4k×5 + 50k×0.2 + 1k×20 = 40000+20000+10000+20000 = 90000 /1e6
        self.assertEqual(pricing.cost(pricing.OPUS, u), Decimal("0.090000"))

    def test_haiku_tiers(self):
        small = {"input_tokens": 90_000, "output_tokens": 1_000}
        big = {"input_tokens": 90_000, "output_tokens": 1_000, "cache_read_input_tokens": 20_000}
        self.assertEqual(pricing.cost(pricing.HAIKU, small), Decimal("0.009500"))
        # 超 10 万（含缓存读）整次按高价：90k×0.5 + 20k×0.05 + 1k×2.5
        self.assertEqual(pricing.cost(pricing.HAIKU, big), Decimal("0.048500"))

    def test_cache_ttl_split(self):
        u = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 3000,
             "cache_creation": {"ephemeral_5m_input_tokens": 1000, "ephemeral_1h_input_tokens": 2000}}
        self.assertEqual(pricing.cost(pricing.OPUS, u), Decimal("0.021000"))


class Budget(unittest.TestCase):
    def test_cap_and_milestones(self):
        tmp = Path(tempfile.mkdtemp())
        led = Ledger(tmp / "l.jsonl", hard_cap=Decimal("25"))
        usage = {"input_tokens": 1_000_000, "output_tokens": 300_000}  # Opus：4 + 6 = 10 美元
        led.record(model=pricing.OPUS, usage=usage, purpose="t", doc_id="d", mode="m", seconds=1)
        self.assertEqual(led.total, Decimal("10"))
        self.assertEqual(led.pending_milestone(), Decimal("10"))
        led.ack()
        self.assertIsNone(led.pending_milestone())
        led.record(model=pricing.OPUS, usage=usage, purpose="t", doc_id="d", mode="m", seconds=1)
        self.assertEqual(Ledger(tmp / "l.jsonl").total, Decimal("20"))  # 跨运行累计
        with self.assertRaises(BudgetStop):
            led.guard(pricing.OPUS, 1_000_000, 100_000)  # 20 + 4 + 2 > 25


class Dbf(unittest.TestCase):
    def test_roundtrip(self):
        specs = [("DOCNUM", "C", 12, 0), ("DOCDAT", "D", 8, 0), ("NETAMT", "B", 8, 0), ("TRNQTY", "N", 12, 2),
                 ("CUSNAM", "C", 30, 0)]
        rec = [{"DOCNUM": "IV0000001", "DOCDAT": dt.date(2026, 10, 1), "NETAMT": 107.0, "TRNQTY": 3,
                "CUSNAM": "บริษัท ทดสอบ"}, {"DOCNUM": "IV0000002"}]
        data = dbfread.write_table(specs, rec, deleted={1})
        rows = list(dbfread.rows(data))
        self.assertEqual(rows[0]["DOCNUM"], "IV0000001")
        self.assertEqual(rows[0]["DOCDAT"], dt.date(2026, 10, 1))
        self.assertEqual(rows[0]["NETAMT"], 107.0)
        self.assertEqual(rows[0]["TRNQTY"], 3.0)
        self.assertEqual(rows[0]["CUSNAM"], "บริษัท ทดสอบ")
        self.assertTrue(rows[1]["_del"])
        self.assertEqual([r["_row"] for r in dbfread.rows(data, since=1)], [1])
        self.assertEqual(dbfread.count(data), 2)


class Compare(unittest.TestCase):
    def rows(self, **head):
        h = {"_row": 0, "_del": False, "DOCNUM": "RR0000001", "DOCDAT": "2026-10-20", "SUPCOD": "P001",
             "REFNUM": "INV-1", "NETVAL": 100.0, "VATAMT": 7.0, "NETAMT": 107.0, **head}
        return {"APTRN": [h], "STCRD": [{"_row": 0, "_del": False, "DOCNUM": "RR0000001", "SEQNUM": "1",
                                         "STKCOD": "A1", "LOCCOD": "01", "TRNQTY": 2.0, "UNITPR": 50.0,
                                         "TRNVAL": 100.0}],
                "ISVAT": [{"_row": 0, "_del": False, "DOCNUM": "RR0000001", "AMT01": 100.0, "VAT01": 7.0}]}

    def draft(self):
        return Draft("x", "RR", "2026-10-20", "P001", [Line("A1", "2", "50")], ref_no="INV-1")

    def test_ok(self):
        self.assertEqual(C.compare(self.draft(), self.rows())["diffs"], [])

    def test_diffs(self):
        r = C.compare(self.draft(), self.rows(SUPCOD="P002", NETAMT=108.0))
        self.assertEqual({d["field"] for d in r["diffs"]}, {"party", "total"})

    def test_blank_head_left_by_block(self):
        rows = self.rows()
        rows["APTRN"].insert(0, {"_row": 0, "_del": False, "DOCNUM": "RR0000000", "NETAMT": 0.0})
        r = C.compare(self.draft(), rows)
        self.assertEqual(r["docnum"], "RR0000001")
        self.assertIn("extra_heads", {d["field"] for d in r["diffs"]})


class EngineOnFake(unittest.TestCase):
    def test_happy_path_no_screenshots(self):
        recs, sim, claude, _ = run_docs(samples())
        for r in recs:
            self.assertTrue(r["readback_ok"], (r["doc_id"], r["diffs"], r["fallbacks"], r["stuck_at"]))
            self.assertTrue(r["first_try"], r)
            self.assertEqual(r["shots_ai"], 0, r["layers"])  # 第 2、3 层全判断得了，一张截图都不给 AI
            self.assertEqual(r["calls"], 0)
            self.assertGreater(r["layers"]["win32"], 0)
        iv = next(r for r in recs if r["doc_type"] == "IV")
        self.assertIn("4079", [e.get("name") for e in iv["events"] if e["kind"] == "dialog"])
        self.assertEqual([r["docnum"] for r in recs], ["RR0000001", "HP0000001", "IV0000001", "HS0000001"])
        self.assertEqual(sim.trap_saves, 0)  # 从来没靠 Esc / 取消存档
        self.assertEqual(sim.unit_mult, "1.000")  # 单位框的倍数没被单价覆盖

    def test_uia_layer(self):
        recs, _, _, _ = run_docs(samples()[:1], readable="uia")
        self.assertTrue(recs[0]["readback_ok"])
        self.assertGreater(recs[0]["layers"]["uia"], 0)
        self.assertEqual(recs[0]["shots_ai"], 0)

    def test_unreadable_cells_fall_back_to_one_presave_screenshot(self):
        recs, _, _, _ = run_docs(samples()[:2], readable="none")
        for r in recs:
            self.assertTrue(r["readback_ok"], r["diffs"])
            self.assertEqual(r["layers"].get("vision"), 2)  # 保存前看一眼；保存后读不到单号再看一眼

    def test_party_default_incl_price_is_overridden(self):
        d = Draft("INCL", "RR", "2026-05-02", "P001", [Line("501780", "1", "2853.50")], ref_no="INV252")
        recs, sim, _, _ = run_docs([d], inject={"incl_party:P001": True})
        self.assertTrue(recs[0]["readback_ok"], recs[0]["diffs"])
        self.assertEqual(sim.book["APTRN"][0]["NETAMT"], 3053.25)

    def test_dup_ref_and_4108_are_only_reminders(self):
        a = Draft("D1", "RR", "2026-05-02", "P001", [Line("501780", "1", "100")], ref_no="SAME")
        b = Draft("D2", "RR", "2026-05-03", "P001", [Line("501780", "1", "100")], ref_no="SAME")
        recs, _, _, _ = run_docs([a, b], inject={"warn4108:P001": True})
        self.assertTrue(all(r["readback_ok"] for r in recs))
        names = [e.get("name") for e in recs[1]["events"] if e["kind"] == "dialog"]
        self.assertIn("dup_ref", names)
        self.assertIn("4108", names)
        self.assertEqual(recs[1]["shots_ai"], 0)

    def test_hidden_dialog_needs_ai(self):
        recs, _, claude, _ = run_docs(samples(), dialogs_visible=False)
        iv = next(r for r in recs if r["doc_type"] == "IV")
        self.assertTrue(iv["readback_ok"], (iv["diffs"], iv["stuck_at"]))
        self.assertFalse(iv["first_try"])
        self.assertEqual(iv["fallbacks"][0]["resolved_by"], pricing.HAIKU)

    def test_typo_caught_by_cell_read(self):
        d = Draft("TYPO", "IV", "2026-10-22", "5001", [Line("501780", "5", "10.00")])
        recs, _, _, _ = run_docs([d], inject={"typo_qty:TYPO": "50"})
        self.assertTrue(recs[0]["readback_ok"], recs[0])
        self.assertEqual(recs[0]["fallbacks"][0]["where"], "line1.qty")

    def test_unknown_party_picker_goes_to_ai(self):
        d = Draft("BADP", "RR", "2026-10-20", "NOPE", [Line("501780", "1", "10.00")], ref_no="X1")
        recs, _, claude, _ = run_docs([d])
        self.assertTrue(recs[0]["fallbacks"])
        self.assertIn(f"cu:{pricing.HAIKU}", claude.calls)

    def test_unknown_dialog_then_escalate_to_opus(self):
        d = samples()[0]
        recs, _, claude, _ = run_docs([d], inject={"party:P001": {"text": "ข้อความใหม่ 9999"}}, haiku_gives_up=True)
        r = recs[0]
        self.assertTrue(r["readback_ok"], (r["diffs"], r["stuck_at"]))
        self.assertEqual(r["fallbacks"][0]["resolved_by"], pricing.OPUS)
        self.assertIn("认不出的弹窗", r["fallbacks"][0]["problem"])

    def test_opus_fixed_uses_only_opus(self):
        recs, _, claude, _ = run_docs(samples(), mode="opus_fixed", dialogs_visible=False)
        self.assertTrue(claude.calls and all(pricing.OPUS in c for c in claude.calls))

    def test_vision(self):
        recs, _, claude, _ = run_docs(samples()[:2], mode="opus_vision")
        for r in recs:
            self.assertTrue(r["readback_ok"], (r["diffs"], r["stuck_at"]))
            self.assertEqual(r["checkpoints"], 0)
            self.assertGreater(r["cu_turns"], 3)

    def test_smb_crash_stops(self):
        d = samples()[2]
        recs, _, _, _ = run_docs([d], inject={"crash_on_save:5001": True})
        self.assertEqual(recs[0]["outcome"], "crash")
        self.assertIn("-70", recs[0]["stuck_at"])

    def test_negative_stock_block_is_abandoned_and_voided(self):
        d = samples()[2]
        recs, sim, _, _ = run_docs([d], inject={"block_item:501780": True})
        self.assertEqual(recs[0]["outcome"], "blocked")
        self.assertFalse(sim.editing)
        heads = [r for r in sim.book["ARTRN"] if r["DOCSTAT"] != "C"]
        self.assertEqual(heads, [])  # 取消时被 Express 存了的，也已作废

    def test_readback_catches_what_checks_missed(self):
        d = Draft("TYPO", "IV", "2026-10-22", "5001", [Line("501780", "5", "10.00")])
        recs, _, _, _ = run_docs([d], inject={"typo_qty:TYPO": "50"}, blind=True, readable="none")
        self.assertFalse(recs[0]["readback_ok"])
        self.assertIn("line1.qty", {x["field"] for x in recs[0]["diffs"]})

    def test_answer_and_total_cost(self):
        ok = Draft("A1", "RR", "2026-10-20", "P001", [Line("501780", "1", "1000.00")], ref_no="IV-77",
                   ocr_usd="0.0063", answer={"date": "2026-10-20", "ref": "IV-77", "net": "1000", "vat": "70",
                                             "total": "1070"})
        bad = Draft("A2", "RR", "2026-10-20", "P001", [Line("501780", "1", "1000.00")], ref_no="IV-78",
                    answer={"date": "2026-10-20", "ref": "IV-78", "net": "1100", "vat": "77", "total": "1177"})
        recs, _, _, _ = run_docs([ok, bad])
        self.assertTrue(recs[0]["answer_ok"])
        self.assertEqual(recs[0]["usd_total"], "0.0063")
        self.assertFalse(recs[1]["answer_ok"])
        self.assertEqual({d["field"] for d in recs[1]["answer_diffs"]}, {"net", "vat", "total"})

    def test_budget_stop(self):
        tmp = Path(tempfile.mkdtemp())
        led = Ledger(tmp / "l.jsonl", hard_cap=Decimal("0.000001"))
        recs, _, _, _ = run_docs(samples()[:1], ledger=led, readable="none")
        self.assertEqual(recs[0]["outcome"], "budget")

    def test_costs_are_recorded(self):
        recs, _, _, led = run_docs(samples(), dialogs_visible=False, readable="none")
        self.assertEqual(sum(Decimal(r["usd"]) for r in recs), led.total)
        self.assertTrue(all(r["calls"] == r["checkpoints"] + r["cu_turns"] for r in recs))


class Corpus(unittest.TestCase):
    def test_usable_and_build(self):
        root = Path(tempfile.mkdtemp())
        book = root / "ACME_0105555000001" / "答案" / "T9ACME" / "原表"
        book.mkdir(parents=True)
        head = [("DOCNUM", "C", 12, 0), ("DOCDAT", "D", 8, 0), ("SUPCOD", "C", 10, 0), ("REFNUM", "C", 15, 0),
                ("FLGVAT", "C", 1, 0), ("NETVAL", "B", 8, 0), ("VATAMT", "B", 8, 0), ("NETAMT", "B", 8, 0),
                ("DISCAMT", "B", 8, 0), ("DOCSTAT", "C", 1, 0)]
        (book / "APTRN.DBF").write_bytes(dbfread.write_table(head, [
            {"DOCNUM": "RR6810-001", "DOCDAT": dt.date(2025, 10, 5), "SUPCOD": "S9", "REFNUM": "IV-77", "FLGVAT": "2",
             "NETVAL": 150.0, "VATAMT": 10.5, "NETAMT": 160.5, "DOCSTAT": "N"},
            {"DOCNUM": "RR6810-002", "DOCDAT": dt.date(2025, 10, 6), "SUPCOD": "S9", "FLGVAT": "2",
             "NETVAL": 90.0, "VATAMT": 6.3, "NETAMT": 96.3, "DISCAMT": 10.0, "DOCSTAT": "N"}]))
        line = [("DOCNUM", "C", 12, 0), ("SEQNUM", "C", 3, 0), ("STKCOD", "C", 20, 0), ("TRNQTY", "B", 8, 0),
                ("UNITPR", "B", 8, 0), ("TRNVAL", "B", 8, 0), ("DISCAMT", "B", 8, 0)]
        (book / "STCRD.DBF").write_bytes(dbfread.write_table(line, [
            {"DOCNUM": "RR6810-001", "SEQNUM": "1", "STKCOD": "X1", "TRNQTY": 3, "UNITPR": 50.0, "TRNVAL": 150.0},
            {"DOCNUM": "RR6810-002", "SEQNUM": "1", "STKCOD": "X2", "TRNQTY": 1, "UNITPR": 100.0, "TRNVAL": 100.0}]))
        survey = corpus.survey(root)
        self.assertEqual(survey["books"][0]["book"], "T9ACME")
        masters = {"APMAS": [{"SUPCOD": "P001"}, {"SUPCOD": "P002"}], "ARMAS": [{"CUSCOD": "C1"}],
                   "STMAS": [{"STKCOD": "501780", "STKTYP": "0"}], "period": {"beg": "2026-01-01", "end": "2026-12-31"}}
        drafts = corpus.build(root, masters, per_type=5)
        self.assertEqual(len(drafts), 1)  # 带折扣的第二张被筛掉
        d = drafts[0]
        self.assertEqual((d.doc_type, d.ref_no, d.lines[0].item_code, d.lines[0].qty), ("RR", "IV-77", "501780", "3"))
        self.assertIn(d.party_code, ("P001", "P002"))
        self.assertEqual(d.date, "2026-10-05")
        self.assertEqual(d.totals()["total"], Decimal("160.50"))


class AgentBits(unittest.TestCase):
    def test_png_encoder(self):
        from . import agent_win as A
        png = A._png(2, 1, [bytes([255, 0, 0, 0, 255, 0])])
        self.assertTrue(png.startswith(b"\x89PNG"))
        idat = png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8]
        self.assertEqual(zlib.decompress(idat), b"\0" + bytes([255, 0, 0, 0, 255, 0]))

    def test_login_module_imports(self):
        from . import cc_login  # noqa: F401 — Windows 以外只检查能导入


if __name__ == "__main__":
    unittest.main(verbosity=1)
