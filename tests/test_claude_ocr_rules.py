"""Claude 识别路线（ADR-022）的纯计算部分：硬性自检、Haiku→Opus 分级、结果格式，以及「三个产品每个识别入口都认 Claude 路线」
的源码守卫。不调接口、不花钱。"""
import json
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/pearnly_core"))
from pearnly_core.claude_ocr import engine, rules, spec  # noqa: E402

GOOD_TAX = "0105546015062"  # 冰厂 SINCERE，校验位对


def doc(**kw):
    base = {"page": 1, "invoice_number": "IV001", "date": "2026-05-01", "total": "107.00", "subtotal": "100.00", "vat": "7.00",
            "seller_tax": GOOD_TAX, "buyer_tax": None, "lines": []}
    base.update(kw)
    return base


class Rules(unittest.TestCase):
    def test_thai_id_check_digit(self):
        self.assertTrue(rules.thai_id_ok(GOOD_TAX))
        self.assertTrue(rules.thai_id_ok("0-1055-46015-06-2"))
        self.assertFalse(rules.thai_id_ok("0105546015063"))
        self.assertFalse(rules.thai_id_ok("010554601506"))

    def test_clean_reading_needs_no_review(self):
        self.assertEqual(rules.voucher_problems({"entry": "enter", "documents": [doc()]}, 1), [])
        self.assertEqual(rules.voucher_problems({"entry": "not_invoice", "documents": []}, 1), [])

    def test_hard_checks(self):
        found = rules.voucher_problems({"entry": "enter", "documents": [doc(total="110.00"), doc(invoice_number=None),
                                                                        doc(seller_tax="0105546015063")]}, 1)
        self.assertTrue(any("subtotal + vat != total" in w for w in found))
        self.assertTrue(any("invoice_number missing" in w for w in found))
        self.assertTrue(any("check digit" in w for w in found))

    def test_vat_included_receipt_is_normal(self):
        # 含税小票：印的 Subtotal 就是含税合计（10-17 SM 小票 44 次误升级的原因）
        self.assertEqual(rules.voucher_problems({"entry": "enter", "documents": [doc(subtotal="107.00", vat="7.00")]}, 1), [])

    def test_multi_page_documents_read_short(self):
        read = {"entry": "enter", "documents": [doc()]}
        self.assertEqual(rules.voucher_problems(read, 2), [])
        self.assertTrue(any("pages but only" in w for w in rules.voucher_problems(read, 3)))

    def test_own_tax_correction(self):
        read = {"documents": [doc(buyer_tax="0105546015082"), doc(buyer_tax="0105549999999")]}
        self.assertEqual(rules.fix_own_tax(read, GOOD_TAX), 1)
        self.assertEqual(read["documents"][0]["buyer_tax"], GOOD_TAX)
        self.assertEqual(read["documents"][1]["buyer_tax"], "0105549999999")  # 差太多：是另一家，不改
        self.assertEqual(rules.fix_own_tax(read, "0000000000000"), 0)

    def test_id_card_checks(self):
        body = "110040012345"
        valid = body + str((11 - sum(int(body[i]) * (13 - i) for i in range(12)) % 11) % 10)
        card = {"is_id_card": True, "people_id": valid, "first_name_th": "สมชาย", "last_name_th": "ใจดี", "date_of_birth_be": "01/01/2530"}
        self.assertEqual(rules.id_card_problems(card), [])
        wrong = valid[:-1] + str((int(valid[-1]) + 1) % 10)
        self.assertEqual(rules.id_card_problems(dict(card, people_id=wrong)), ["people_id fails the Thai ID check digit"])
        self.assertEqual(rules.id_card_problems(dict(card, last_name_th=None)), ["last_name_th missing"])
        self.assertEqual(rules.id_card_problems({"is_id_card": False}), [])


class Schema(unittest.TestCase):
    def test_structured_output_limits(self):
        """结构化输出里可为空（type 数组 / anyOf）的参数不能多（10-17 实测 36 个被拒），一律字符串。"""
        def unions(node):
            if isinstance(node, dict):
                here = 1 if isinstance(node.get("type"), list) or "anyOf" in node else 0
                return here + sum(unions(v) for v in node.values())
            if isinstance(node, list):
                return sum(unions(v) for v in node)
            return 0
        for kind, (_system, schema, _ask) in spec.SPECS.items():
            self.assertEqual(unions(schema), 0, kind)
            json.dumps(schema)

    def test_prompt_keeps_validated_rules(self):
        for phrase in ("hire-purchase", "Copying identifiers", "check digit", "R#", "credit", "50 ทวิ"):
            self.assertIn(phrase, spec.VOUCHER_SYSTEM)


class FakeClient:
    """按顺序回预设结果的假 Anthropic 客户端；记录每次请求。"""
    def __init__(self, *reads):
        self.reads, self.calls = list(reads), []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        read = self.reads.pop(0)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=3000)
        return SimpleNamespace(stop_reason="end_turn", usage=usage,
                               content=[SimpleNamespace(type="text", text=json.dumps(read))])


def reading(*docs, entry="enter"):
    return {"doc_kind": "tax_invoice", "entry": entry, "copy_marking": "none", "documents": list(docs), "uncertain": []}


class Engine(unittest.TestCase):
    def test_haiku_only_when_checks_pass(self):
        client = FakeClient(reading(doc()))
        out = engine.run(client, "voucher", [("image/jpeg", b"x")])
        self.assertFalse(out["escalated"])
        self.assertEqual([c["model"] for c in client.calls], [engine.HAIKU])
        call = client.calls[0]
        self.assertEqual(call["output_config"]["effort"], "low")
        self.assertEqual(call["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("thinking", call)  # Opus 5.5 关不掉思考，只用 effort 控

    def test_escalates_to_opus_with_hint(self):
        client = FakeClient(reading(doc(seller_tax="0105546015063")), reading(doc()))
        out = engine.run(client, "voucher", [("image/jpeg", b"x")])
        self.assertTrue(out["escalated"])
        self.assertEqual([c["model"] for c in client.calls], [engine.HAIKU, engine.OPUS])
        self.assertIn("check digit", client.calls[1]["messages"][0]["content"][-1]["text"])
        self.assertEqual(out["read"]["documents"][0]["seller_tax"], GOOD_TAX)
        self.assertGreater(out["usd"], 0)

    def test_blank_strings_become_none(self):
        client = FakeClient(reading(doc(buyer_tax="", notes="  ")))
        out = engine.run(client, "voucher", [("image/jpeg", b"x")])
        self.assertIsNone(out["read"]["documents"][0]["buyer_tax"])
        self.assertIsNone(out["read"]["documents"][0]["notes"])

    def test_page_cap(self):
        client = FakeClient(reading(entry="not_invoice"))
        engine.run(client, "voucher", [("image/jpeg", b"x")] * 12)
        images = [b for b in client.calls[0]["messages"][0]["content"] if b["type"] == "image"]
        self.assertEqual(len(images), engine.MAX_PAGES)


def row(date, deposit="", withdrawal="", balance=""):
    amount = deposit or withdrawal
    return {"transaction_date": date, "transaction_date_raw": date, "description": "x", "reference": "", "deposit": deposit,
            "withdrawal": withdrawal, "amount": amount, "direction": "deposit" if deposit else "withdrawal", "balance": balance}


def gl_row(debit="", credit="", balance=""):
    return {"transaction_date": "2026-05-01", "voucher_no": "JV1", "debit": debit, "credit": credit, "amount": debit or credit,
            "direction": "debit" if debit else "credit", "balance": balance}


class ReconRules(unittest.TestCase):
    def test_bank_balance_chain(self):
        page = {"opening_balance": "1000.00", "closing_balance": "1250.00",
                "entries": [row("2026-05-01", deposit="500.00", balance="1500.00"),
                            row("2026-05-02", withdrawal="100.00"),  # 这一行没印余额：金额累加到下一次印的余额
                            row("2026-05-02", withdrawal="150.00", balance="1250.00")]}
        self.assertEqual(rules.bank_problems(page), [])
        broken = json.loads(json.dumps(page))
        broken["entries"][2]["balance"] = "1260.00"
        why = rules.bank_problems(broken)
        self.assertTrue(any("entries[2].balance" in w for w in why))
        self.assertTrue(any("closing_balance" in w for w in why))
        both = json.loads(json.dumps(page))
        both["entries"][0]["withdrawal"] = "5.00"
        self.assertTrue(any("both deposit and withdrawal" in w for w in rules.bank_problems(both)))
        self.assertEqual(rules.bank_problems(None), ["no_result"])

    def test_gl_balance_follows_majority_direction(self):
        asset = {"opening_balance": "100", "entries": [gl_row(debit="50", balance="150"), gl_row(credit="20", balance="130")]}
        liability = {"opening_balance": "100", "entries": [gl_row(credit="50", balance="150"), gl_row(debit="20", balance="130")]}
        self.assertEqual(rules.gl_problems(asset), [])
        self.assertEqual(rules.gl_problems(liability), [])
        asset["entries"].append(gl_row(debit="10", balance="150"))
        self.assertEqual(rules.gl_problems(asset), ["entries[2].balance does not follow from the previous balance and this row's debit/credit"])

    def test_vat_report_rows(self):
        good = {"seller_tax": GOOD_TAX, "entries": [{"invoice_no": "IV1", "customer_tax": GOOD_TAX, "subtotal": "100.00",
                                                       "vat": "7.00", "total": "107.00"},
                                                      {"invoice_no": "IV2", "customer_tax": "0000000000000", "subtotal": "10",
                                                       "vat": "0.70", "total": ""}]}
        self.assertEqual(rules.vat_report_problems(good), [])
        bad = json.loads(json.dumps(good))
        bad["entries"][0]["total"] = "117.00"
        bad["entries"][0]["customer_tax"] = GOOD_TAX[:-1] + "3"
        self.assertEqual(len(rules.vat_report_problems(bad)), 2)


class ReconEngine(unittest.TestCase):
    def test_bank_page_escalates_on_a_broken_chain(self):
        bad = {"opening_balance": "1000.00", "entries": [row("2026-05-01", deposit="500.00", balance="1600.00")]}
        good = {"opening_balance": "1000.00", "entries": [row("2026-05-01", deposit="500.00", balance="1500.00")]}
        client = FakeClient(bad, good)
        out = engine.run(client, "bank", [("image/jpeg", b"x")])
        self.assertEqual([c["model"] for c in client.calls], [engine.HAIKU, engine.OPUS])
        self.assertEqual(out["read"]["entries"][0]["balance"], "1500.00")
        self.assertIn("entries[0].balance", client.calls[1]["messages"][0]["content"][-1]["text"])

    def test_table_text_replaces_images(self):
        client = FakeClient(reading(doc()))
        engine.run(client, "voucher", [], text="Invoice | IV001\nTotal | 107.00")
        content = client.calls[0]["messages"][0]["content"]
        self.assertEqual([b["type"] for b in content], ["text", "text"])
        self.assertIn("IV001", content[0]["text"])
        self.assertIn(" | ", spec.TEXT_INTRO)


ROOT = Path(__file__).resolve().parents[1]
# 真正发谷歌识别请求的函数（模型、读字、Document AI、交 Cloud Run 读）。
GOOGLE_CALLS = re.compile(r"\b(?:build_client|json_from_images|text_from_image|run_invoice|run_table|read_document|"
                          r"forward\.(?:recognize|send))\(")
# 不是租户站点上的识别入口：谷歌路线自己的实现（定义处、Cloud Run 那一侧）、压测回放、对账的纯读法包装。
NOT_ENTRIES = {
    "pearnly_core/pearnly_core/ocr.py", "pearnly_cowork/pearnly_cowork/ocr/providers/core.py",
    "pearnly_cowork/pearnly_cowork/ocr/providers/client.py", "pearnly_cowork/pearnly_cowork/ocr/remote/runner.py",
    "pearnly_cowork/pearnly_cowork/ocr/remote/forward.py", "pearnly_cowork/pearnly_cowork/ocr/remote/tasks.py",
    "pearnly_cowork/pearnly_cowork/ocr/engine/__init__.py", "pearnly_cowork/pearnly_cowork/ocr/engine/runner.py",
    "pearnly_cowork/pearnly_cowork/ocr/table_engine/__init__.py", "pearnly_cowork/pearnly_cowork/ocr/table_engine/runner.py",
    "pearnly_cowork/pearnly_cowork/ocr/recon_reading.py", "pearnly_cowork/pearnly_cowork/ocr/execution/claude_documents.py",
    "pearnly_cowork/pearnly_cowork/ocr/enterprise/pipeline.py",
    # 对账的「模型兜底」接口：线上读法是传进来的 ``read``（recon_center.document_ocr，已接 Claude）；这里的 model_call 只给离线测试。
    "pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_bank_reconciliation/cowork_bank_reconciliation.py",
}


class EveryEntry(unittest.TestCase):
    def test_every_google_recognition_entry_checks_the_claude_route(self):
        """三个产品里调谷歌识别的每个入口都先看站点选的路线（ADR-022）；新加入口忘了接，这里就红。"""
        missing = []
        for path in sorted((ROOT / "apps").rglob("*.py")):
            rel = path.relative_to(ROOT / "apps").as_posix()
            if "/test" in rel or rel.rsplit("/", 1)[-1].startswith("test_") or rel in NOT_ENTRIES:
                continue
            text = path.read_text(encoding="utf-8")
            calls = [line for line in text.splitlines() if GOOGLE_CALLS.search(line) and not line.lstrip().startswith(("def ", "#"))]
            if calls and "uses_claude()" not in text:
                missing.append(rel)
        self.assertEqual(missing, [])

    def test_google_exits_refuse_claude_sites(self):
        """付费谷歌调用的两个出口（共用层 provider.post、Cowork 交 Cloud Run）对选了 Claude 的站点一律拒绝。"""
        provider = (ROOT / "apps/pearnly_core/pearnly_core/execution/provider.py").read_text(encoding="utf-8")
        forward = (ROOT / "apps/pearnly_cowork/pearnly_cowork/ocr/remote/forward.py").read_text(encoding="utf-8")
        post = provider[provider.index("def post("):]
        call = forward[forward.index("def _call("):]
        self.assertIn("claude_route.refuse_google(model)", post[:post.index("while True")])
        self.assertIn('claude_route.refuse_google("cloudrun" + path)', call[:call.index("if tasks.active()")])


if __name__ == "__main__":
    unittest.main()
