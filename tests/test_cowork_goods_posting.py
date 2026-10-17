"""商品单入账方式按公司选一次（数字会计员第 3 期第一轮回放后，用户 2026-10-16「按公司问一次记住」）。

永续 / 混合库存账套里没声明入账类型的商品单，映射器照「铁律」交人（`posting_needs_review:*`）。这家公司选过「走库存 / 记费用」
后，同样情形的单按选定值当作声明了入账类型；没选过的照旧交人。存储与问法同转账凭条 / 预扣税凭证的「选一次」
（工作空间 ``document_policy``，键 ``goods_posting|<公司身份>``；复核页推送原因旁两个按钮；CC 同一个工具）。

- 纯规则 `push.express.goods_posting`：会不会交人与映射器同一判据、选过的照用、公司身份与 `companies.identity` 一致；
- 存储 `contracts.document_policy`：按公司的键与值、认不出的不留；
- 写入口 `push.application.goods_posting.choose`：按推往账套的目录行认公司，权限经推送同一条读取，只记 Express。
纯函数部分不依赖 Frappe；写入口用替身。
"""
import datetime as dt
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))
sys.path.insert(0, str(ROOT / "apps/pearnly_cowork"))
for _name in [n for n in sys.modules if n.split(".")[0] == "pearnly_cowork"]:
    del sys.modules[_name]

from pearnly_cowork.companies import identity  # noqa: E402
from pearnly_cowork.contracts import document_policy as policy  # noqa: E402
from pearnly_cowork.push.express import goods_posting  # noqa: E402
from pearnly_cowork.push.express.mapper import build_express_payload  # noqa: E402
from pearnly_cowork.push.express.posting_profile import ESCALATE_REASON_PREFIX  # noqa: E402

OWN, SELLER = "0994000333447", "0107561000013"
TODAY = dt.date(2026, 10, 20)
PERPETUAL = {"stcrd_lines": 10, "stcrd_lines_moving_stock": 9}
MIXED = {"stcrd_lines": 10, "stcrd_lines_moving_stock": 4}
NONE = {"stcrd_lines": 10, "stcrd_lines_moving_stock": 0}


def goods(**over):
    """进项完整税票（印了税额、写明买方）：商品单车道。"""
    fields = {"document_type": "tax_invoice", "direction": "purchase", "seller_name": "ผู้ขาย", "seller_tax": SELLER,
              "buyer_name": "ACME", "buyer_tax": OWN, "invoice_number": "IV-1", "date": "2026-10-10",
              "subtotal": "1000.00", "vat": "70.00", "total_amount": "1070.00"}
    fields.update(over)
    return fields


def config(fingerprint=PERPETUAL, **over):
    found = {"account_set": "C:\\PearnlyExpress\\69ACME", "fallback_acc": "51-01", "ap_acc": "21-01",
             "vat_input_acc": "11-50", "catalog_fingerprint": dict(fingerprint)}
    found.update(over)
    return found


def row(tax=OWN, company="บริษัท เอซีเอ็มอี จำกัด", branch="00000"):
    return {"source_id": "C:\\PearnlyExpress\\69ACME", "attributes": {"tax_id": tax, "company": company,
                                                                      "branch": branch}}


class Blocked(unittest.TestCase):
    """会不会交人：与映射器的「永续 / 混合库存、没声明 → 交人」逐条一致。"""

    def blocked(self, fields=None, *, direction="purchase", cfg=None, posting_kind=None):
        return goods_posting.blocked(fields or goods(), direction=direction, config=cfg or config(),
                                     posting_kind=posting_kind, today=TODAY)

    def test_undeclared_goods_in_a_perpetual_or_mixed_book(self):
        self.assertTrue(self.blocked())
        self.assertTrue(self.blocked(cfg=config(MIXED)))
        self.assertTrue(self.blocked(direction="sales", fields=goods(direction="sales", seller_tax=OWN, buyer_tax=SELLER)))

    def test_what_never_stops_here(self):
        self.assertFalse(self.blocked(posting_kind="service"))                       # 本票 / 本批声明了
        self.assertFalse(self.blocked(posting_kind="stock"))
        self.assertFalse(self.blocked(goods(items=[{"name": "A", "posting_kind": "stock"}])))  # 明细行逐行声明
        self.assertFalse(self.blocked(cfg=config(NONE)))                              # 不管库存的账套
        self.assertFalse(self.blocked(cfg=config(fingerprint={})))                    # 指纹没报：照旧按非库存
        self.assertFalse(self.blocked(goods(document_type="receipt")))                # 进项费用单不动库存
        # 会计确认过的画像（连接上锁的覆盖）不拦就不拦
        self.assertFalse(self.blocked(cfg=config(posting_profile={"posting_mode": "non_stock"})))

    def test_the_mapper_stops_exactly_when_blocked_says_so(self):
        for fields, cfg in ((goods(), config()), (goods(), config(NONE)), (goods(document_type="receipt"), config())):
            result = build_express_payload({"fields": fields}, config=cfg, today=TODAY, posting_kind=None)
            stopped = not result.ok and str(result.reason).startswith(ESCALATE_REASON_PREFIX)
            self.assertEqual(stopped, self.blocked(fields, cfg=cfg), (fields["document_type"], cfg["catalog_fingerprint"]))


class SameLaneAsTheMapper(unittest.TestCase):
    """`blocked` 与映射器用同一个车道判据（`field_rules.lane_verdict`），含这家选过的「小票 / 简易税票抵不抵进项」
    （审查处理 10-16：选了抵进项的小票在永续账套里映射器交人，原先 `blocked` 说不交，公司选过的入账方式永远用不上）。"""

    def test_blocked_agrees_with_the_mapper_under_a_remembered_vat_choice(self):
        receipt = goods(document_type="receipt", buyer_tax="", buyer_name="")
        for fields in (receipt, goods(), goods(document_type="simplified_tax_invoice")):
            for vat in (None, "claim", "expense"):
                cfg = config(choices={"vat": vat} if vat else {})
                result = build_express_payload({"fields": fields}, config=cfg, today=TODAY, posting_kind=None)
                stopped = not result.ok and str(result.reason).startswith(ESCALATE_REASON_PREFIX)
                self.assertEqual(stopped, goods_posting.blocked(fields, direction="purchase", config=cfg,
                                                                posting_kind=None, today=TODAY, vat_choice=vat),
                                 (fields["document_type"], vat))

    def test_a_claimed_receipt_follows_the_company_choice(self):
        receipt = goods(document_type="receipt")
        self.assertEqual(goods_posting.resolve(receipt, direction="purchase", config=config(), posting_kind=None,
                                               chosen="service", today=TODAY), (None, ""))
        kind, source = goods_posting.resolve(receipt, direction="purchase", config=config(), posting_kind=None,
                                             chosen="service", today=TODAY, vat_choice="claim")
        self.assertEqual((kind, source), ("service", goods_posting.COMPANY_CHOICE))
        result = build_express_payload({"fields": receipt}, config=config(choices={"vat": "claim"}), today=TODAY,
                                       posting_kind=kind)
        self.assertFalse(str(result.reason or "").startswith(ESCALATE_REASON_PREFIX), result.reason)


class ServiceUnderAStockChoice(unittest.TestCase):
    """作用面：会交人的不只是商品（进项所有可抵进项的完整税票、销项全部单）。公司选「走库存」后一张服务费（房租）也走库存车道；
    要保证它推不成 A：库存行要有着落（记过的存货码或 Express 同名存货），没有就要人选存货（C）；科目只是连接默认（B）。"""

    def judge(self, items, products):
        from pearnly_cowork.push.express import automation, decide

        fields = goods(seller_name="เจ้าของอาคาร", invoice_number="R-1", subtotal="30000.00", vat="2100.00",
                       total_amount="32100.00", posting_payment_manual="credit")
        if items:
            fields["items"] = items
        cfg = config(reported_products=products, reported_suppliers=[{"code": "S1", "tax_id": SELLER, "branch": "00000"}])
        kind, source = goods_posting.resolve(fields, direction="purchase", config=cfg, posting_kind=None,
                                             chosen="stock", today=TODAY)
        self.assertEqual((kind, source), ("stock", goods_posting.COMPANY_CHOICE))
        decision, _sources = decide.decide(fields, direction="purchase", posting_kind=kind, config=cfg, remembered={})
        payload = None
        if not decision["needed"]:
            cfg["choices"] = decision["applied"]
            result = build_express_payload({"fields": fields}, config=cfg, today=TODAY, posting_kind=kind)
            payload = result.payload if result.ok else None
        text = "ใบกำกับภาษี 0107561000013 0994000333447 R-1 10/10/2026 32,100.00 " * 10
        facts = automation.Facts(direction="purchase", fields=fields, decision=decision, payload=payload, config=cfg,
                                 today=TODAY, ready=payload is not None, posting_from=source, text=text,
                                 page_texts=[text], pages=[{"fields": fields}], account=row(), catalog=[row()],
                                 day_books=[row()["source_id"].lower()], uploaded=TODAY)
        return decision, payload, automation.classify(facts)

    def test_a_service_line_is_never_a_under_a_stock_choice(self):
        rent = [{"name": "ค่าเช่า", "subtotal": "30000.00"}]
        decision, _payload, verdict = self.judge(rent, [{"code": "P1", "name": "สินค้า A"}])
        self.assertIn("item", [item["kind"] for item in decision["needed"]])  # 要人选存货
        self.assertEqual(verdict.cls, "C")
        _decision, payload, verdict = self.judge(rent, [])  # 账套没报存货主档：不问，但存货一项 C
        self.assertEqual([item["item_mode"] for item in payload["items"]], ["stock_item"])
        self.assertEqual((verdict.cls, verdict.items.get("item")), ("C", "C"))
        _decision, payload, verdict = self.judge(None, [{"code": "P1", "name": "สินค้า A"}])  # 没读出明细行
        self.assertEqual(payload["items"], [])
        self.assertNotEqual(verdict.cls, "A")
        self.assertEqual(verdict.items.get("account"), "B")  # 库存车道的科目是连接默认


class History(unittest.TestCase):
    """这家供应商在 Express 的历史决定公司选的入账方式一项的档（用户 10-17）：近 12 个月会计亲手录 ≥3 张、只有一种记法，与公司
    选的一致、载荷正好按它写 → A；相反 → C（``conflict:posting``）；没有 / 不够 → B。值仍取公司选的。经映射器出真载荷。"""

    STOCK = [{"code": "P1", "name": "กระดาษ A4", "kind": "stock"}]

    def judge(self, chosen, history, items=None, products=(), **over):
        from pearnly_cowork.push.express import automation, decide

        fields = goods(posting_payment_manual="credit", **over)
        if items:
            fields["items"] = items
        cfg = config(reported_products=list(products),
                     reported_suppliers=[{"code": "S1", "tax_id": SELLER, "branch": "00000"}])
        kind, source = goods_posting.resolve(fields, direction="purchase", config=cfg, posting_kind=None,
                                             chosen=chosen, today=TODAY)
        self.assertEqual((kind, source), (chosen, goods_posting.COMPANY_CHOICE))
        # 记费用的单要费用科目：这家记过（账套记忆）
        remembered = {"account": {f"purchase|{SELLER}" if over.get("seller_tax") is None else "purchase|name:ผู้ขาย":
                                  "53-01"}}
        decision, _sources = decide.decide(fields, direction="purchase", posting_kind=kind, config=cfg,
                                           remembered=remembered)
        self.assertEqual(decision["needed"], [])
        cfg["choices"] = decision["applied"]
        result = build_express_payload({"fields": fields}, config=cfg, today=TODAY, posting_kind=kind)
        payload = result.payload if result.ok else None
        self.assertIsNotNone(payload)
        text = "ใบกำกับภาษี 0107561000013 0994000333447 IV-1 10/10/2026 1,070.00 " * 10
        facts = automation.Facts(direction="purchase", fields=fields, decision=decision, payload=payload, config=cfg,
                                 today=TODAY, ready=payload is not None, posting_from=source, posting_kind=kind,
                                 posting_history=history, text=text, page_texts=[text], pages=[{"fields": fields}],
                                 account=row(), catalog=[row()], day_books=[row()["source_id"].lower()], uploaded=TODAY)
        return automation.posting_by_history(facts), automation.classify(facts)

    def test_stock_history_agrees_with_a_stock_choice_only_when_stock_lines_are_written(self):
        paper = [{"name": "กระดาษ A4", "qty": "10", "price": "100.00", "subtotal": "1000.00"}]
        found, verdict = self.judge("stock", "stock", paper, self.STOCK)
        self.assertEqual((found, verdict.items["posting"], verdict.items["item"]), ("A", "A", "A"))
        found, verdict = self.judge("stock", "stock")  # 没读出明细行：CC 桌面版只写表头
        self.assertEqual((found, verdict.items["posting"]), ("no_lines", "B"))
        found, verdict = self.judge("stock", "", paper, self.STOCK)  # 没有历史
        self.assertEqual((found, verdict.items["posting"]), ("B", "B"))

    def test_service_history_needs_no_stock_named_line(self):
        found, verdict = self.judge("service", "service")
        self.assertEqual((found, verdict.items["posting"]), ("A", "A"))
        paper = [{"name": "กระดาษ A4", "qty": "10", "price": "100.00", "subtotal": "1000.00"}]
        found, verdict = self.judge("service", "service", paper, self.STOCK)  # 票面品名与库存品同名：可能是第一次进货
        self.assertEqual((found, verdict.items["posting"]), ("named", "B"))

    def test_history_against_the_choice_goes_to_a_person(self):
        found, verdict = self.judge("service", "stock")
        self.assertEqual((found, verdict.cls, verdict.items["posting"]), ("conflict", "C", "C"))
        self.assertIn("conflict:posting", verdict.reasons)
        self.assertNotIn("item:posting", verdict.reasons)

    def test_a_supplier_without_a_tax_id_is_at_most_b(self):
        found, verdict = self.judge("service", "service", seller_tax="")
        self.assertEqual((found, verdict.items["posting"]), ("A", "B"))  # 历史按名字对上：判据成立，档位封顶 B
        # 按名字对上、历史相反：照样交人（保守口径，第五轮后审查定；同名的别家最多多交一张给人）
        found, verdict = self.judge("service", "stock", seller_tax="")
        self.assertEqual((found, verdict.cls), ("conflict", "C"))
        self.assertIn("conflict:posting", verdict.reasons)


class Resolve(unittest.TestCase):
    def resolve(self, chosen, *, posting_kind=None, fields=None, cfg=None):
        return goods_posting.resolve(fields or goods(), direction="purchase", config=cfg or config(),
                                     posting_kind=posting_kind, chosen=chosen, today=TODAY)

    def test_a_company_choice_is_used_only_where_the_document_would_stop(self):
        self.assertEqual(self.resolve("stock"), ("stock", goods_posting.COMPANY_CHOICE))
        self.assertEqual(self.resolve("service"), ("service", goods_posting.COMPANY_CHOICE))
        self.assertEqual(self.resolve(None), (None, ""))                  # 没选过：照旧交人
        self.assertEqual(self.resolve("stock", cfg=config(NONE)), (None, ""))  # 不会交人：照旧按画像
        self.assertEqual(self.resolve("stock", fields=goods(document_type="receipt")), (None, ""))
        self.assertEqual(self.resolve("bogus"), (None, ""))

    def test_a_declaration_wins_over_the_company_choice(self):
        self.assertEqual(self.resolve("stock", posting_kind="service"), ("service", goods_posting.DECLARED))
        self.assertEqual(self.resolve(None, posting_kind="STOCK"), ("stock", goods_posting.DECLARED))

    def test_the_mapper_goes_on_with_the_chosen_kind(self):
        kind, _source = self.resolve("service")
        result = build_express_payload({"fields": goods()}, config=config(), today=TODAY, posting_kind=kind)
        self.assertFalse(str(result.reason or "").startswith(ESCALATE_REASON_PREFIX), result.reason)


class CompanyKey(unittest.TestCase):
    def test_the_same_identity_as_the_company_master(self):
        for item in (row(), row(branch="00012"), row(tax=""), row(tax="0994000333448"), row(tax="", company="")):
            mine = identity.company_identity("express", "EXP1", item)
            self.assertEqual(goods_posting.company_key("EXP1", item), (mine or {}).get("key", ""), item)
        # 同税号各分店、各年度是一家
        self.assertEqual(goods_posting.company_key("EXP1", row(branch="00012")), f"tax:{OWN}")
        self.assertEqual(goods_posting.company_key("", row()), "")
        self.assertEqual(goods_posting.company_key("EXP1", None), "")


class Storage(unittest.TestCase):
    def test_per_company_keys_share_the_document_policy_field(self):
        key = policy.goods_posting_key(f"tax:{OWN}")
        self.assertEqual(key, f"goods_posting|tax:{OWN}")
        raw = {"payment_evidence": "enter", key: "stock", "goods_posting|tax:1": "bogus", "other": "stock",
               "goods_posting|" + "x" * 300: "stock"}
        self.assertEqual(policy.normalize(raw), {"payment_evidence": "enter", key: "stock"})
        self.assertEqual(policy.goods_posting(policy.normalize(raw), f"tax:{OWN}"), "stock")
        self.assertIsNone(policy.goods_posting({}, f"tax:{OWN}"))
        self.assertIsNone(policy.goods_posting({key: "stock"}, ""))
        self.assertEqual(policy.goods_posting_key(""), "")
        # 转账凭条 / 预扣税凭证的门槛不受影响
        self.assertIsNone(policy.push_block({"document_type": "tax_invoice"}, {key: "stock"}))

    def test_company_keys_pick_only_this_companys_posting_and_account_sets_in_use(self):
        # 主档页「记住的选择」按公司列这两类（用户 2026-10-17）：别家、按租户记的、形状不对的都不算
        me = f"tax:{OWN}"
        head = policy.current_book_key(me, "", "cy", ["a", "b"])
        branch = policy.current_book_key(me, "00012", "ny", ["c", "d"])
        other = policy.current_book_key(me + "9", "", "cy", ["a", "b"])
        raw = {f"goods_posting|{me}": "stock", head: "a", branch: "d", other: "a", "payment_evidence": "enter",
               f"goods_posting|{me}9": "service", f"current_book|{me}||xy|0123456789ab": "a"}
        self.assertEqual(policy.company_keys(raw, me), {f"goods_posting|{me}": "stock", head: "a", branch: "d"})
        self.assertEqual((policy.current_book_parts(head, me), policy.current_book_parts(branch, me)),
                         (("", "cy"), ("00012", "ny")))
        for key in (other, f"goods_posting|{me}", f"current_book|{me}|1|2|cy|0123456789ab", f"current_book|{me}||cy|0"):
            self.assertIsNone(policy.current_book_parts(key, me))
        self.assertIsNone(policy.current_book_parts(head, ""))
        self.assertEqual(policy.company_keys(raw, ""), {})

    def test_marks_of_an_answer_decide_when_to_ask_again(self):
        """用户 10-18 加重问：回答时记下各候选的最新录入（``current_book_seen|…``，同一组换前缀）；之后未被选中的那份更晚才重问，
        选中的那份自己更晚不算；旧存储没有、认不得、记的不是这一组候选都当没有（照旧用回答）。"""
        me = f"tax:{OWN}"
        head = policy.current_book_key(me, "", "cy", ["a", "b"])
        seen_key = policy.current_book_seen_key(head)
        self.assertEqual(seen_key, "current_book_seen" + head[len("current_book"):])
        self.assertEqual((policy.current_book_seen_key("goods_posting|x"), policy.current_book_seen_key(None)), ("", ""))
        long_key = "current_book|" + "x" * 224  # 本身 237 字放得下，换了前缀 242 字超长：不记标记（回答照记）
        self.assertEqual((len(long_key) <= policy.GOODS_POSTING_KEY_MAX, policy.current_book_seen_key(long_key)), (True, ""))
        # 写法：ISO 时刻到微秒，日期按零点，空、认不出为空串
        self.assertEqual(policy.book_mark(dt.datetime(2026, 10, 1, 8, 0, 5)), "2026-10-01T08:00:05.000000")
        self.assertEqual(policy.book_mark(dt.date(2026, 10, 1)), "2026-10-01T00:00:00.000000")
        self.assertEqual(policy.book_mark("2026-10-01 08:00:05.5"), "2026-10-01T08:00:05.500000")
        self.assertEqual([policy.book_mark(value) for value in (None, "", "yesterday")], ["", "", ""])
        value = policy.book_marks({"b": "2026-10-02T09:00:00", "a": dt.datetime(2026, 10, 1, 8, 0)})
        self.assertRegex(value, r"^[0-9a-f]{8}=2026-10-0\dT0\d:00:00\.000000,[0-9a-f]{8}=2026-10-0\dT0\d:00:00\.000000$")
        self.assertEqual(policy.book_marks({}), "")
        stored = policy.normalize({head: "a", seen_key: value, policy.current_book_seen_key(head) + "x": "bad",
                                   "current_book_seen|y": "zz=1"})
        self.assertEqual(stored, {head: "a", seen_key: value})
        seen = policy.current_book_seen(stored, head, ["a", "b"])
        self.assertEqual(seen, {"a": "2026-10-01T08:00:00.000000", "b": "2026-10-02T09:00:00.000000"})
        # 记的不是这一组候选、旧存储没有：None（调用方照旧用回答）
        self.assertIsNone(policy.current_book_seen(stored, head, ["a", "c"]))
        self.assertIsNone(policy.current_book_seen({head: "a"}, head, ["a", "b"]))
        self.assertIsNone(policy.current_book_seen({head: "a", seen_key: "nonsense"}, head, ["a", "b"]))
        # 选了 a：b 更晚才重问；a 自己更晚不算；此刻没有标记不算；回答时没有录入的按最早算
        self.assertFalse(policy.current_book_newer(seen, {"a": "2026-10-09T00:00:00", "b": "2026-10-02T09:00:00"}, "a"))
        self.assertTrue(policy.current_book_newer(seen, {"a": "", "b": "2026-10-02T09:00:01"}, "a"))
        self.assertFalse(policy.current_book_newer(seen, {"b": ""}, "a"))
        self.assertTrue(policy.current_book_newer({"a": "", "b": ""}, {"b": "2020-01-01T00:00:00"}, "a"))
        self.assertFalse(policy.current_book_newer({"a": "", "b": ""}, {"c": "2030-01-01T00:00:00"}, "a"))


class Choose(unittest.TestCase):
    """写入口：按这张票推往的账套认公司，记进工作空间同一个字段。"""

    def load(self, adapter="express", rows=None, doc=None):
        seen = {"recorded": [], "read": []}

        class Thrown(Exception):
            pass

        def throw(message, *args):
            raise Thrown(message)

        frappe = types.ModuleType("frappe")
        frappe._ = lambda text, *a: text
        frappe.throw = throw
        doc = doc or types.SimpleNamespace(name="H1", workspace="W1", client="", push_connection="EXP1",
                                           push_account_set="C:\\PearnlyExpress\\69ACME")
        loaded = types.SimpleNamespace(endpoint={"adapter": adapter}, doc=types.SimpleNamespace(name="EXP1"),
                                       evidence=types.SimpleNamespace(snapshot={"account_sets": json.dumps(
                                           [row()] if rows is None else rows)}))

        def read_for_push(connection, workspace, client, lock):
            seen["read"].append((connection, workspace, client, lock))
            return loaded

        modules = {
            "frappe": frappe,
            "pearnly_cowork.access": types.SimpleNamespace(scope=types.SimpleNamespace(
                require=lambda feature: seen.setdefault("scope", feature))),
            "pearnly_cowork.connections": types.SimpleNamespace(service=types.SimpleNamespace(read_for_push=read_for_push)),
            "pearnly_cowork.history": types.SimpleNamespace(
                document_policy=types.SimpleNamespace(record=lambda *a: seen["recorded"].append(a),
                                                      read=lambda workspace: seen.get("stored", {}),
                                                      forget=lambda *a: seen.setdefault("forgot", []).append(a) or 1),
                storage=types.SimpleNamespace(read=lambda name: doc))}
        with mock.patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location("goods_posting_choose", APP / "push/application/goods_posting.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module, seen, Thrown

    def test_records_the_company_of_the_target_account_set(self):
        module, seen, _thrown = self.load()
        self.assertEqual(module.choose("H1", "stock"), {"goods_posting": "stock"})
        self.assertEqual(seen["recorded"], [("W1", f"goods_posting|tax:{OWN}", "stock")])
        self.assertEqual((seen["scope"], seen["read"]), ("intake", [("EXP1", "W1", "", True)]))
        # 复核页提交的那份账套优先于票上记的去向（按键归一后找目录行）
        module.choose("H1", "service", "EXP1", "c:/pearnlyexpress/69acme/")
        self.assertEqual(seen["recorded"][-1], ("W1", f"goods_posting|tax:{OWN}", "service"))

    def test_refuses_what_it_cannot_tie_to_a_company(self):
        module, seen, thrown = self.load()
        for value in ("", "enter", "Stock"):
            with self.assertRaises(thrown):
                module.choose("H1", value)
        module, seen, thrown = self.load(adapter="mrerp")
        with self.assertRaises(thrown):
            module.choose("H1", "stock")
        module, seen, thrown = self.load(rows=[])
        with self.assertRaises(thrown):
            module.choose("H1", "stock")  # 目录里没有这份账套
        module, seen, thrown = self.load(doc=types.SimpleNamespace(name="H1", workspace="W1", client="",
                                                                    push_connection="", push_account_set=""))
        with self.assertRaises(thrown):
            module.choose("H1", "stock")  # 不知道推往哪
        self.assertEqual(seen["recorded"], [])

    def test_the_company_page_sets_and_forgets_only_this_companys_keys(self):
        # 主档页改入账方式、删入账方式或现用账套（companies 不导入 history，经这里写）；这家的身份可以有两个（主档身份、
        # 按名称建档时的名称身份），主档身份在前
        module, seen, thrown = self.load()
        me, alias = f"tax:{OWN}", "name:express:EXP1:acme"
        self.assertEqual(module.set_company("W1", [me, alias], "service"), {"goods_posting": "service"})
        self.assertEqual((seen["recorded"], seen["scope"]), ([("W1", f"goods_posting|{me}", "service")], "intake"))
        seen["stored"] = {f"goods_posting|{alias}": "stock"}  # 旧年度按名称身份记过的一起改
        module.set_company("W1", [me, alias], "stock")
        self.assertEqual(seen["recorded"][1:], [("W1", f"goods_posting|{me}", "stock"), ("W1", f"goods_posting|{alias}", "stock")])
        module.set_company("W1", me, "service")  # 只给一个身份
        self.assertEqual(seen["recorded"][3:], [("W1", f"goods_posting|{me}", "service")])
        for workspace, identities, value in (("W1", [me], "enter"), ("W1", [], "stock"), ("W1", [""], "stock"),
                                             ("", [me], "stock")):
            with self.assertRaises(thrown):
                module.set_company(workspace, identities, value)
        book = policy.current_book_key(me, "", "cy", ["a", "b"])
        self.assertEqual(module.forget_company("W1", [me, alias], [book]), 1)
        self.assertEqual(module.forget_company("W1", [me, alias], [f"goods_posting|{me}", f"goods_posting|{alias}"]), 2)
        for key in ("payment_evidence", f"goods_posting|{me}9", policy.current_book_key(me + "9", "", "cy", ["a", "b"]), ""):
            with self.assertRaises(thrown):  # 有一个不是这家的就整个不删
                module.forget_company("W1", [me, alias], [f"goods_posting|{me}", key])
        with self.assertRaises(thrown):
            module.forget_company("W1", [me], [])
        self.assertEqual(seen["forgot"], [("W1", book), ("W1", f"goods_posting|{me}"), ("W1", f"goods_posting|{alias}")])


if __name__ == "__main__":
    unittest.main()
