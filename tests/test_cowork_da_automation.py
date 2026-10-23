"""数字会计员第 3 期第 1、2 条：每张单分 A / B / C（`push/express/automation.py`）与判定核心（`push/express/decide.py`）。

- 第 1 条：每一项（对方档、存货、费用 / 收入科目、现 / 赊、抵进项、部门、POS 收款科目）按来源 × 方向分档；按名字作键最高 B；
  各层说法不一归 C；
- 第 2 条：自动推前的核对全部确定性、只出原因码：硬闸、识别侧、票种、原文、金额读出、对方税号、日期、分店、作废 / 拆分 / 证据页、
  自动推专用的重复分流。
纯函数，不连库、不依赖 Frappe。
"""
import ast
import dataclasses
import datetime as dt
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))
sys.path.insert(0, str(ROOT / "apps/pearnly_cowork"))
for _name in [n for n in sys.modules if n.split(".")[0] == "pearnly_cowork"]:
    del sys.modules[_name]

from pearnly_core.tax_documents.amounts import valid_tax_id  # noqa: E402
from pearnly_cowork.push.express import automation as auto  # noqa: E402
from pearnly_core.express_rules.push import choices as choice_rules  # noqa: E402
from pearnly_cowork.push.express import decide as decide_rules  # noqa: E402
from pearnly_cowork.push.express.pushed import fit_ref_no  # noqa: E402

SELLER, OWN, OTHER = "0107561000013", "0994000333447", "0105556012341"
BOOK = r"C:\PearnlyExpress\69ACME"
BOOK_OLD = r"C:\PearnlyExpress\68ACME"


def _fields(**over):
    fields = {"document_type": "tax_invoice", "direction": "purchase", "seller_name": "บริษัท ผู้ขาย จำกัด",
              "seller_tax": SELLER, "seller_branch": "00000", "buyer_name": "ACME", "buyer_tax": OWN,
              "buyer_branch": "00000", "invoice_number": "IV69-001", "date": "2026-10-20",
              "subtotal": "1000.00", "vat": "70.00", "total_amount": "1070.00"}
    fields.update(over)
    return fields


def _payload(**over):
    payload = {"direction": "purchase", "doctype": "RR", "account_set": BOOK, "ref_no": "IV69-001",
               "supplier": {"code": "S001", "tax_id": SELLER, "name": "ผู้ขาย"}, "base_amount": "1000.00",
               "vat_amount": "70.00", "total_amount": "1070.00", "items": [], "items_account": "5310-01",
               "account_source": "remembered", "doctype_src": "explicit", "doc_lane": "goods", "item_src": "choice"}
    payload.update(over)
    return payload


ACCOUNT = {"source_id": BOOK, "attributes": {"tax_id": OWN, "branch": "00000", "period_start": "2026-01-01",
                                             "period_end": "2026-12-31", "next_end": "2027-12-31"}}
TEXT = ("ใบกำกับภาษี ผู้ขาย เลขประจำตัวผู้เสียภาษี 0107561000013 ผู้ซื้อ ACME 0994000333447 เลขที่ IV69-001 "
        "วันที่ 20/10/2569 รวมเงิน 1,000.00 ภาษี 70.00 รวมทั้งสิ้น 1,070.00")


def _facts(**over):
    fields = over.pop("fields", None) or _fields()
    base = dict(direction="purchase", fields=fields, payload=_payload(),
                decision={"applied": {"account": "5310-01"}, "needed": [],
                          "decided_by": {"account": "remembered", "vat": "remembered"}},
                config={"reported_suppliers": [{"code": "S001", "tax_id": SELLER, "branch": "00000"}],
                        "reported_accounts": [{"code": "5310-01"}]},
                today=dt.date(2026, 10, 25), ready=True, blocked="",
                pages=[{"_layer_chain": ["text", "L2"], "fields": fields, "_needs_manual_review": False}],
                review_reasons=[], text=TEXT, account=ACCOUNT, catalog=[ACCOUNT], day_books=[BOOK],
                uploaded=dt.date(2026, 10, 22), source_total=1, prior_pushes=[],
                # 账套记忆定的科目要最近连续 5 张有证据的同科目（R3，10-18；现 / 赊、对方档仍 3 张）
                account_recent=["5310-01"] * 5)
    base.update(over)
    return auto.Facts(**base)


class Baseline(unittest.TestCase):
    def test_the_fixture_tax_ids_are_valid(self):
        self.assertTrue(all(len(tax) == 13 and valid_tax_id(tax) for tax in (SELLER, OWN, OTHER)))

    def test_a_clean_text_pdf_decided_by_rules_is_a(self):
        verdict = auto.classify(_facts())
        self.assertEqual((verdict.cls, verdict.reasons), ("A", ()))
        self.assertEqual(verdict.items, {"party": "A", "account": "A", "payment": "A", "vat": "A", "department": "A",
                                         "own": "A"})


class ItemTiers(unittest.TestCase):
    """第 1 条：来源 × 方向矩阵。"""

    def tier(self, kind, **over):
        return auto.tiers(_facts(**over)).get(kind)

    def test_party(self):
        # 账套记忆 / 公司规则定的对方档：A
        self.assertEqual(self.tier("party", decision={"needed": [], "decided_by": {"party": "company_rule"}},
                                   config={}), "A")
        # 票面 13 位税号 + 分店在主档里只对上一个档：A；没有同税号档、按票面新建：B
        self.assertEqual(self.tier("party"), "A")
        self.assertEqual(self.tier("party", config={"reported_suppliers": [{"code": "S9", "tax_id": OTHER}]}), "B")
        # 同税号几个档、分店对不上、对方税号不是 13 位、主档没报上来：C
        two = [{"code": "S1", "tax_id": SELLER, "branch": "00000"}, {"code": "S2", "tax_id": SELLER, "branch": "00001"}]
        self.assertEqual(self.tier("party", config={"reported_suppliers": two}), "C")
        self.assertEqual(self.tier("party", config={"reported_suppliers": [{"code": "S1", "tax_id": SELLER,
                                                                            "branch": "00002"}]}), "C")
        self.assertEqual(self.tier("party", fields=_fields(seller_tax="")), "C")
        self.assertEqual(self.tier("party", config={}), "C")

    def test_sales_party_looks_at_the_buyer_and_the_customer_master(self):
        fields = _fields(direction="sales", seller_tax=OWN, buyer_tax=SELLER, buyer_branch="00000")
        facts = _facts(direction="sales", fields=fields,
                       payload=_payload(direction="sales", doctype="IV", customer={"tax_id": SELLER},
                                        account_source="config_default", doctype_src="doc_type"),
                       config={"reported_customers": [{"code": "C1", "tax_id": SELLER, "branch": "00000"}]})
        # 默认收入科目、按票种判赊都是兜底 / 推断：B（审查处理 10-16，第 15 条回放）
        self.assertEqual(auto.tiers(facts), {"party": "A", "account": "B", "payment": "B", "department": "A", "own": "A"})

    def test_purchase_account_sources(self):
        for source, tier in (("task_default", "A"), ("remembered", "A"), ("chosen", "A"), ("company_rule", "A"),
                             ("category_map", "B"), ("config_default", "B"), ("choice", "C"), ("", "C")):
            self.assertEqual(self.tier("account", payload=_payload(account_source=source)), tier, source)

    def test_a_remembered_account_needs_five_in_a_row(self):
        """账套记忆按供应商记一个科目：这家在这份账套里最近连续至少 5 张同科目才算 A，否则 B（第一轮回放后定 3 张，10-16；
        10-18 E1 的 R3 科目改 5 张：第六轮科目错 117 项里「混着记」「一单多科目」的窗口里刚好凑到 3 张同科目）。现 / 赊、对方档仍 3 张。"""
        def tier(recent):
            return self.tier("account", payload=_payload(account_source="remembered"), account_recent=recent)
        self.assertEqual(tier(["5310-01"] * 5), "A")
        self.assertEqual(tier(["5310-01"] * 5 + ["5320-01"]), "A")      # 更早的不同不影响
        self.assertEqual(tier(["5310-01"] * 4), "B")                     # 还不够 5 张
        self.assertEqual(tier(["5310-01"] * 3), "B")                     # 原先 3 张就 A（R3 之前）
        self.assertEqual(tier(["5310-01", "5320-01"] + ["5310-01"] * 4), "B")  # 中间断了
        self.assertEqual(tier(["5320-01"] * 5), "B")                     # 最近都记别的科目
        self.assertEqual(tier(["*multi"] + ["5310-01"] * 5), "B")       # 一单多科目也断开
        self.assertEqual(tier([]), "B")
        self.assertEqual(tier(None), "B")                                # 没取（旧调用方）按 0 张
        # 只管账套记忆：任务默认、本票选的、公司规则不看连续张数
        for source in ("task_default", "chosen", "company_rule"):
            self.assertEqual(self.tier("account", payload=_payload(account_source=source), account_recent=[]), "A",
                             source)
        self.assertEqual(auto.MEMORY_STREAK, 3)
        self.assertEqual(auto.MEMORY_STREAKS, {"account": 5, "payment": 3, "party": 3})
        self.assertEqual((auto.streak_for("account"), auto.streak_for("payment"), auto.streak_for("party"),
                          auto.streak_for(None)), (5, 3, 3, 3))
        self.assertEqual(auto.memory_streak(["a", "a", "b", "a"], "a"), 2)
        self.assertEqual(auto.memory_streak(["a"], ""), 0)
        self.assertTrue(auto.memory_settled(["a", "a", "a"], "a"))       # 不给类按 3 张
        self.assertTrue(auto.memory_settled(["a", "a", "a"], "a", "payment"))
        self.assertFalse(auto.memory_settled(["a", "a", "a"], "a", "account"))
        self.assertTrue(auto.memory_settled(["a"] * 5, "a", "account"))
        # 账套记忆定的科目为什么不是 A（回放按码统计）：不到 3 张 streak、到 3 张不到 5 张 streak_long
        facts = _facts(payload=_payload(account_source="remembered"), account_recent=["5310-01"] * 3)
        self.assertEqual(auto.memory_holds(facts, "account"), ("streak_long",))
        self.assertEqual(auto.memory_holds(dataclasses.replace(facts, account_recent=["5310-01"]), "account"), ("streak",))
        self.assertEqual(auto.memory_holds(dataclasses.replace(facts, account_recent=["5310-01"] * 5), "account"), ())

    def test_the_streak_counts_only_pushes_with_evidence(self):
        """连续张数只数有证据的单（`memory_evidence`，审查处理 10-16）：推的时候科目由人或规则定的照记；凭账套记忆推出去的
        要合格回读覆盖、没被改；回读里被改的断开；凭记忆推、还没回读核过的跳过（CC 自己推的单攒不成「连续 3 张」）。"""
        def entry(source="remembered", readback="", account="5310-01"):
            return {"account": account, "source": source, "readback": readback}
        evidence = auto.memory_evidence
        self.assertEqual(evidence([entry()] * 5), [])
        self.assertEqual(evidence([entry(readback="confirmed")] * 3), ["5310-01"] * 3)
        self.assertEqual(evidence([entry(source=source) for source in ("chosen", "task_default", "company_rule",
                                                                       "express_history")]), ["5310-01"] * 4)
        mixed = [entry(), entry(readback="confirmed"), entry(), entry(source="chosen"), entry(readback="confirmed")]
        self.assertEqual(evidence(mixed), ["5310-01"] * 3)
        self.assertTrue(auto.memory_settled(evidence(mixed), "5310-01"))
        # 回读里被改的：断开（哪怕推的时候是人选的）
        broken = [entry(readback="confirmed"), entry(source="chosen", readback="changed"), entry(readback="confirmed"),
                  entry(readback="confirmed")]
        self.assertEqual(evidence(broken), ["5310-01", "", "5310-01", "5310-01"])
        self.assertFalse(auto.memory_settled(evidence(broken), "5310-01"))
        self.assertEqual(evidence([None, "x", entry(source="category_map", readback="")]), [])
        self.assertEqual(evidence([entry(account="")]), [])
        self.assertEqual(evidence([entry(account="", source="chosen")]), [""])  # 科目照旧：没记科目也断开

    def test_the_evidence_window_is_counted_among_pushes_with_evidence(self):
        """证据窗口（`memory_window`，10-17 改取法）：凭记忆推出去还没回读核过的先跳过，再在能当证据的里取最近的——最新几百张
        没核过、更早三张核过的照样定下；往回最多看 ``scan`` 张（先截、再按分店筛，0 不设上限）；值为 None 的跳过；证据判定
        与 `memory_evidence` 一字不差。"""
        def entry(source="remembered", readback="", account="5310-01", branch="00000"):
            return {"account": account, "source": source, "readback": readback, "party": "S1", "branch": branch}
        unchecked = [entry()] * 600
        checked = [entry(readback="confirmed")] * 3
        self.assertEqual(auto.memory_window(unchecked + checked), ["5310-01"] * 3)
        self.assertTrue(auto.memory_settled(auto.memory_window(unchecked + checked), "5310-01"))
        self.assertEqual(auto.memory_window(unchecked), [])  # 没有任何回读：仍是零张
        self.assertEqual(auto.memory_window(unchecked + checked, scan=30), [])  # 旧取法（截最新 30 张再跳过）
        self.assertEqual(auto.memory_window(unchecked + checked, scan=0), ["5310-01"] * 3)
        self.assertEqual(auto.RECENT_SCAN_MAX, 1000)
        self.assertEqual(auto.memory_window([entry()] * 1000 + checked), [])  # 上限之外的够不着
        self.assertEqual(auto.memory_window([entry()] * 997 + checked), ["5310-01"] * 3)
        broken = unchecked + [checked[0], entry(readback="changed"), checked[0]]
        self.assertEqual(auto.memory_window(broken), ["5310-01", "", "5310-01"])
        mixed = [entry(readback="confirmed", branch="00001")] * 3 + checked
        self.assertEqual(auto.memory_window(mixed, "party", branch="00000"), ["S1"] * 3)
        self.assertEqual(auto.memory_window(mixed, "party", branch="00000", scan=3), [])  # 先截、再筛
        self.assertEqual(auto.memory_window([{**checked[0], "account": None}] + checked), ["5310-01"] * 3)
        for kind in ("account", "payment", "party"):
            rows = [{**entry(readback=readback), "payment": "RR"} for readback in ("", "confirmed", "changed", "")]
            self.assertEqual(auto.memory_window(rows, kind), auto.memory_evidence(rows, kind))

    def test_the_evidence_window_stops_at_the_period_cap(self):
        """证据窗口按税期截（审查处理 10-17，`window_take` / `RECENT_PERIOD_MAX`）：线上每个税期另查一次回读，所以往回最多跨 6 个
        不同的税期——碰到第 7 个就截掉；税期为空的不算；``seen`` 带着前几批已占的税期；0 不设上限。截只决定候选，证据判定不变。"""
        def entry(period, readback="", source="remembered"):
            return {"account": "5310-01", "source": source, "readback": readback, "period": period}
        self.assertEqual(auto.RECENT_PERIOD_MAX, 6)
        months = [f"2026-{month:02d}" for month in range(9, 0, -1)]  # 2026-09 往回 9 期
        rows = [entry(month) for month in months for _ in range(20)]
        self.assertEqual(auto.window_take(rows, set()), 120)
        self.assertEqual(auto.window_take(rows, set(), periods=0), len(rows))
        self.assertEqual(auto.window_take(rows, set(), periods=2), 40)
        seen = set()
        self.assertEqual(auto.window_take(rows[:30], seen), 30)  # 第一批：两期
        self.assertEqual(seen, {"2026-09", "2026-08"})
        self.assertEqual(auto.window_take(rows[30:], seen), 90)  # 下一批接着数，合计还是 6 期
        self.assertEqual(len(seen), 6)
        self.assertEqual(auto.window_take([entry("")] * 5 + rows, set()), 125)  # 没有单据日期的不占税期
        # 第 6 期核过的三张：在窗口里；第 7 期的够不着（0 不设上限时够得着）
        sixth = [entry(month) for month in months[:5] for _ in range(20)] + [entry(months[5], "confirmed")] * 3
        self.assertEqual(auto.memory_window(sixth), ["5310-01"] * 3)
        seventh = [entry(month) for month in months[:6] for _ in range(20)] + [entry(months[6], "confirmed")] * 3
        self.assertEqual(auto.memory_window(seventh), [])
        self.assertEqual(auto.memory_window(seventh, periods=0), ["5310-01"] * 3)
        # 截在税期上，跟证据判定无关：同一期里被改的照样断开、人定的照样算
        broken = [entry("2026-09", "confirmed"), entry("2026-09", "changed"), entry("2026-08", source="chosen")]
        self.assertEqual(auto.memory_window(broken), auto.memory_evidence(broken))

    def test_payment_and_party_evidence(self):
        """现 / 赊、对方档的证据口径（10-17）：现 / 赊推的时候由本票人工裁决、任务默认、公司规则定的照记，凭记忆（含人在待处理页
        选的，推出去也是 remembered）、票面写明、票种口径要回读核过；对方档只认回读；被改的断开；值为空的跳过。"""
        def pay(source="remembered", readback="", value="RR"):
            return {"payment": value, "payment_source": source, "readback": readback}

        def party(readback="", value="S2", source=""):
            return {"party": value, "party_source": source, "readback": readback}
        evidence = auto.memory_evidence
        self.assertEqual(evidence([pay(source=source) for source in ("manual", "task_default", "company_rule")],
                                  "payment"), ["RR"] * 3)
        self.assertEqual(evidence([pay(source=source) for source in ("remembered", "explicit", "doc_type", "profile")],
                                  "payment"), [])
        self.assertEqual(evidence([pay(readback="confirmed"), pay(source="explicit", readback="confirmed"),
                                   pay(readback="changed"), pay(source="manual", readback="changed")], "payment"),
                         ["RR", "", ""])
        # 票面写明 / 票种口径定的整条不看（审查处理 10-17）：回读核过不算、被改也不断开 —— 三张写明现金、回读核过的收据
        # 不能让记忆的现购在下一张没写付款方式的税票上升 A
        receipts = [pay(source=source, readback=readback, value="HP") for source in ("explicit", "doc_type")
                    for readback in ("confirmed", "changed")]
        self.assertEqual(evidence(receipts, "payment"), [])
        self.assertFalse(auto.memory_settled(evidence([pay(source="explicit", readback="confirmed", value="HP")] * 3,
                                                      "payment"), "HP"))
        self.assertEqual(evidence([pay(readback="confirmed"), pay(source="explicit", readback="changed"),
                                   pay(readback="confirmed"), pay(readback="confirmed")], "payment"), ["RR"] * 3)
        self.assertEqual(evidence([pay(source=source, readback="confirmed") for source in
                                   ("profile", "bank", "config_default", "")], "payment"), ["RR"] * 4)
        self.assertEqual(evidence([pay(value=""), pay(value="", readback="confirmed"), pay(source="manual")],
                                  "payment"), ["RR"])
        self.assertEqual(evidence([party(), party(source="company_rule"), party(readback="confirmed"),
                                   party(readback="changed"), party(value="", readback="confirmed")], "party"),
                         ["S2", ""])
        # 桌面版写入的码与载荷写定的不同（``party_changed``）：断开；真写了库存行的单回读不比分录，科目的 confirmed 不算
        self.assertEqual(evidence([party(readback="confirmed"), dict(party(), party_changed=True)], "party"), ["S2", ""])
        stock = {"account": "5310-01", "source": "remembered", "readback": "confirmed", "stock": True}
        self.assertEqual(evidence([stock, dict(stock, source="chosen"), dict(stock, readback="changed")]),
                         ["5310-01", ""])
        self.assertEqual(evidence([dict(stock, payment="RR", payment_source="remembered")], "payment"), ["RR"])
        self.assertEqual(auto.EVIDENCE_BY_KIND["account"], auto.EVIDENCE_SOURCES)
        self.assertEqual(auto.PARTY_EVIDENCE_SOURCES, frozenset())
        # 科目的结果不传 kind 与传 "account" 一样
        rows = [{"account": "5310-01", "source": "chosen", "payment": "RR", "readback": ""}]
        self.assertEqual(evidence(rows), evidence(rows, "account"))

    def test_a_remembered_payment_needs_three_in_a_row(self):
        """账套记忆定的现 / 赊同科目一样：这家最近连续至少 3 张有证据的同一记法才算 A，否则 B（10-17 推广，第四轮 ASIA-FA 推现购、
        会计记赊购 260 张）。"""
        def tier(recent, **over):
            return self.tier("payment", payload=_payload(**{"doctype": "RR", "doctype_src": "remembered", **over}),
                             payment_recent=recent)
        self.assertEqual(tier(["RR"] * 3), "A")
        self.assertEqual(tier(["RR", "RR"]), "B")
        self.assertEqual(tier(["HP"] * 3), "B")
        self.assertEqual(tier(["RR", "HP", "RR", "RR"]), "B")
        self.assertEqual(tier(["", "RR", "RR", "RR"]), "B")  # 回读里被改的断开
        self.assertEqual(tier([]), "B")
        self.assertEqual(tier(None), "B")
        self.assertEqual(tier(["HP"] * 3, doctype="hp"), "A")  # 记法按大写比
        # 本票人工裁决、任务默认、公司规则不看连续张数；票种口径收据即已付照 A
        for source in ("manual", "task_default", "company_rule"):
            self.assertEqual(self.tier("payment", payload=_payload(doctype_src=source), payment_recent=[]), "A", source)
        self.assertEqual(self.tier("payment", payload=_payload(doctype_src="doc_type", doctype="HP"),
                                   payment_recent=[]), "A")

    def test_a_remembered_party_needs_three_in_a_row(self):
        """账套记忆定的对方档：记的正是主档里唯一对上的那一档照 A；否则要同税号 + 分店最近连续 3 张有证据的同一档才 A；记的是
        新建（码为空）一律 B；公司规则照 A（10-17 推广，第四轮 ASIA-FA 对方档错 65 张）。"""
        remembered = {"needed": [], "decided_by": {"party": "remembered"}}
        two = [{"code": "S1", "tax_id": SELLER, "branch": "00000"}, {"code": "S2", "tax_id": SELLER, "branch": "00000"}]

        def tier(code, recent, config=None, **over):
            supplier = {"code": code, "tax_id": SELLER, "name": "ผู้ขาย"}
            return self.tier("party", decision=remembered, payload=_payload(supplier=supplier), party_recent=recent,
                             config=config if config is not None else {"reported_suppliers": two}, **over)
        # 主档唯一对上、记的就是那一档：A，不看连续张数
        self.assertEqual(tier("S001", None, {"reported_suppliers": [{"code": "S001", "tax_id": SELLER,
                                                                     "branch": "00000"}]}), "A")
        self.assertEqual(tier("S9", None, {"reported_suppliers": [{"code": "S001", "tax_id": SELLER,
                                                                   "branch": "00000"}]}), "B")
        # 同税号几个档：记的那一档要最近连续 3 张有证据
        self.assertEqual(tier("S2", ["S2"] * 3), "A")
        self.assertEqual(tier("S2", ["S2", "S2"]), "B")
        self.assertEqual(tier("S2", ["S1"] * 3), "B")
        self.assertEqual(tier("S2", ["S2", "", "S2", "S2"]), "B")
        self.assertEqual(tier("S2", None), "B")
        self.assertEqual(tier("", [""] * 3), "B")  # 记的是新建
        # 票面没印分店号（审查处理 10-17）：同税号几个档时连续张数落在「总店」上，最高 B；同税号只有一档照旧
        unprinted = _fields(seller_branch="")
        self.assertEqual(tier("S2", ["S2"] * 3, fields=unprinted), "B")
        self.assertEqual(tier("S2", ["S2"] * 3, fields=_fields(seller_branch="สำนักงานใหญ่")), "A")
        one = {"reported_suppliers": [{"code": "S001", "tax_id": SELLER, "branch": "00000"}]}
        self.assertEqual(tier("S001", None, one, fields=unprinted), "A")
        self.assertEqual(tier("S001", ["S001"] * 3, {}, fields=unprinted), "B")  # 主档没报上来：分不清
        # 公司规则定的照 A
        self.assertEqual(self.tier("party", decision={"needed": [], "decided_by": {"party": "company_rule"}},
                                   config={"reported_suppliers": two}), "A")
        # 销项：记忆定的对方档、主档有歧义、没取连续张数（销项不查）→ B
        fields = _fields(direction="sales", seller_tax=OWN, buyer_tax=SELLER, buyer_branch="00000")
        facts = _facts(direction="sales", fields=fields, decision=remembered,
                       payload=_payload(direction="sales", doctype="IV", customer={"code": "C2", "tax_id": SELLER},
                                        account_source="config_default", doctype_src="doc_type"),
                       config={"reported_customers": [{"code": "C1", "tax_id": SELLER, "branch": "00000"},
                                                      {"code": "C2", "tax_id": SELLER, "branch": "00000"}]})
        self.assertEqual(auto.tiers(facts)["party"], "B")

    def test_express_history_is_a_only_from_the_version_without_pearnly_rows(self):
        def tier(history):
            return self.tier("account", payload=_payload(account_source="express_history"),
                             decision={"needed": [], "decided_by": {"account": "express_history"}, **history})
        self.assertEqual(tier({}), "B")
        self.assertEqual(tier({"history": {"account": "5310-01", "docs": 4}}), "B")
        self.assertEqual(tier({"history": {"account": "5310-01", "docs": 4, "version": 1}}), "B")
        self.assertEqual(tier({"history": {"account": "5310-01", "docs": 4, "version": 2}}), "A")

    def test_sales_revenue_account_default_is_b_and_rules_are_a(self):
        # 连接上的默认收入科目是兜底（同进项连接默认科目），第 15 条回放 44% 与会计记的不符：B（审查处理 10-16）
        sales = dict(direction="sales", fields=_fields(direction="sales", seller_tax=OWN, buyer_tax=SELLER))
        self.assertEqual(self.tier("account", payload=_payload(direction="sales", account_source="config_default"),
                                   **sales), "B")
        for source in ("task_default", "remembered", "chosen", "company_rule"):
            self.assertEqual(self.tier("account", payload=_payload(direction="sales", account_source=source), **sales),
                             "A", source)
        self.assertEqual(self.tier("account", payload=_payload(direction="sales", account_source="category_map"),
                                   **sales), "B")

    def test_payment_sources(self):
        # 账套记忆定的现 / 赊要连续 3 张有证据（10-17，`test_a_remembered_payment_needs_three_in_a_row`）：没取按 0 张，B
        for source, tier in (("manual", "A"), ("explicit", "A"), ("remembered", "B"),
                             ("task_default", "A"), ("company_rule", "A"), ("profile", "B"), ("bank", "B"),
                             ("config_default", "B"), ("", "C")):
            self.assertEqual(self.tier("payment", payload=_payload(doctype_src=source)), tier, source)
        # 票种口径：收据即已付（现购 / 现销）是规则，A；完整税票判赊是推断，B（审查处理 10-16）
        for doctype, tier in (("HP", "A"), ("HS", "A"), ("RR", "B"), ("IV", "B")):
            self.assertEqual(self.tier("payment", payload=_payload(doctype_src="doc_type", doctype=doctype)), tier,
                             doctype)
        # POS 日汇总：按导出写明的付款方式拆，收款科目要人或规则定
        pos = _payload(direction="sales", pos={"transfers": [{"acc": "1120", "amount": "50.00"}]})
        self.assertEqual(self.tier("payment", payload=pos), "A")
        self.assertEqual(self.tier("receipt", payload=pos), "C")
        self.assertEqual(self.tier("receipt", payload=pos, decision={"needed": [], "decided_by": {"receipt": "remembered"}}),
                         "A")

    def test_input_vat(self):
        self.assertEqual(self.tier("vat", payload=_payload(item_src="manual")), "A")
        self.assertEqual(self.tier("vat", payload=_payload(item_src="choice")), "A")
        # 按方向推断（全平台共用的法定口径）：第一轮回放 13,878 张 0 错，从 B 改 A（10-16）；原文要对得上（审查处理 10-16）
        claim = _payload(item_src="judge_direction=purchase_invoice")
        expense = _payload(item_src="judge_direction=expense", vat_amount="0.00", vat_capitalized="70.00")
        self.assertEqual(self.tier("vat", payload=claim), "A")
        self.assertEqual(self.tier("vat", payload=expense, fields=_fields(document_type="receipt"),
                                   text=TEXT.replace("ใบกำกับภาษี", "ใบเสร็จรับเงิน")), "A")
        # 识别判抵扣，原文却是简式税票 / 收据、或没印买方税号、文字层是空的：CC 判断（B）
        for text in (TEXT.replace("ใบกำกับภาษี", "ใบกำกับภาษีอย่างย่อ"), TEXT.replace("ใบกำกับภาษี", "ใบเสร็จรับเงิน"),
                     TEXT.replace(OWN, ""), ""):
            self.assertEqual(self.tier("vat", payload=claim, text=text), "B", text[:20])
        # 识别判不抵（记费用），原文却是印着买方税号的完整税票：B；核不到原文（乱码、没有文字层）照 A，不多抵税
        self.assertEqual(self.tier("vat", payload=expense), "B")
        # 判不抵：标题要正是简式税票 / 收据才算 A；核不到原文（乱码、没有文字层、标题印成别的）算 B（第四轮回放 9/10 会计抵了）
        short = _fields(document_type="simplified_tax_invoice")
        self.assertEqual(self.tier("vat", payload=expense, fields=short,
                                   text=TEXT.replace("ใบกำกับภาษี", "ใบกำกับภาษีอย่างย่อ")), "A")
        for text in ("", TEXT.replace("ใบกำกับภาษี", "ใบแจ้งหนี้")):
            self.assertEqual(self.tier("vat", payload=expense, fields=short, text=text), "B", text[:20])
        # 识别给「其他」票种（水电费收据这类，会计常抵进项）：标题是收据也算 B（第四轮闸层）
        self.assertEqual(self.tier("vat", payload=expense, fields=_fields(document_type="other"),
                                   text=TEXT.replace("ใบกำกับภาษี", "ใบเสร็จรับเงิน")), "B")
        self.assertEqual(self.tier("vat", payload=_payload(item_src="")), "C")
        # 没有税额：抵不抵无从说起，不列
        self.assertIsNone(self.tier("vat", payload=_payload(item_src="judge_direction=expense", vat_amount="0.00")))
        # 销项不列
        self.assertIsNone(self.tier("vat", direction="sales", payload=_payload(direction="sales")))

    def test_stock_items(self):
        stock = _payload(items=[{"name": "A", "item_mode": "stock_item"}])
        self.assertEqual(self.tier("item", payload=stock,
                                   config={"reported_products": [{"code": "P1", "name": "A", "kind": "stock"}]}), "A")
        self.assertEqual(self.tier("item", payload=stock, config={}), "C")
        # 每一行都要有着落：记过 / 规则给的存货码（按归一品名），或 Express 有同名存货；账套里「有存货主档」不算
        # （审查处理 10-16：选「走库存」的公司里一张服务费也会出库存行）
        self.assertEqual(self.tier("item", payload=stock, config={},
                                   decision={"needed": [], "applied": {"items": {"a": "P1"}},
                                             "decided_by": {"item": "remembered"}}), "A")
        self.assertEqual(self.tier("item", payload=stock, config={},
                                   decision={"needed": [], "decided_by": {"item": "remembered"}}), "C")
        rent = _payload(items=[{"name": "ค่าเช่า", "item_mode": "stock_item"}])
        self.assertEqual(self.tier("item", payload=rent,
                                   config={"reported_products": [{"code": "P1", "name": "A", "kind": "stock"}]}), "C")
        two = _payload(items=[{"name": "A", "item_mode": "stock_item"}, {"name": "ค่าเช่า", "item_mode": "stock_item"}])
        self.assertEqual(self.tier("item", payload=two,
                                   config={"reported_products": [{"code": "P1", "name": "A", "kind": "stock"}]}), "C")
        self.assertIsNone(self.tier("item"))

    def test_department_is_always_by_rule(self):
        self.assertEqual(self.tier("department"), "A")
        self.assertEqual(self.tier("department", decision={"needed": [], "decided_by": {"department": "remembered"}}),
                         "A")

    def test_name_keyed_sources_are_at_most_b(self):
        # 对方没税号：账套记忆、公司规则、Express 历史以名字为键；票面写明、任务默认不受影响
        fields = _fields(seller_tax="", document_type="receipt")
        found = auto.tiers(_facts(fields=fields, decision={"needed": [], "decided_by": {
            "party": "remembered", "account": "remembered", "vat": "remembered"}},
            payload=_payload(doctype_src="remembered")))
        self.assertEqual((found["party"], found["account"], found["payment"], found["vat"]), ("B", "B", "B", "B"))
        found = auto.tiers(_facts(fields=fields, payload=_payload(account_source="task_default", doctype_src="explicit",
                                                                  item_src="manual")))
        self.assertEqual((found["account"], found["payment"], found["vat"]), ("A", "A", "A"))

    def test_a_cc_inference_makes_b_and_lists_it(self):
        verdict = auto.classify(_facts(payload=_payload(account_source="category_map",
                                                        item_src="judge_direction=purchase_invoice")))
        self.assertEqual((verdict.cls, verdict.reasons), ("B", ("judged:account",)))

    def test_the_company_goods_posting_choice_is_a_cc_judgement(self):
        """永续 / 混合库存账套的商品单：这家公司选过的入账方式（`goods_posting`）只是这家多数单的做法，没有这家供应商的 Express
        历史（v2 及以前、不够 3 张、两种记法都有）时列 posting = B（审查处理 10-16，第四轮回放各家 1%～6% 的单与会计记法不同）。"""
        self.assertEqual(self.tier("posting", posting_from="company_choice", posting_kind="service"), "B")
        self.assertIsNone(self.tier("posting", posting_from="declared", posting_history="service"))
        self.assertIsNone(self.tier("posting"))
        verdict = auto.classify(_facts(posting_from="company_choice", posting_kind="service"))
        self.assertEqual((verdict.cls, verdict.reasons), ("B", ("judged:posting",)))

    def test_the_suppliers_express_history_settles_the_company_goods_posting(self):
        """这家供应商的 Express 历史（近 12 个月会计亲手录 ≥3 张、只有一种记法）与公司选的一致、载荷正好按它写 → A；相反 → C
        ``conflict:posting``（照各层说法不一交人，不另记 ``item:posting``）；值仍取公司选的（用户 10-17）。"""
        chosen = dict(posting_from="company_choice", posting_kind="service")
        verdict = auto.classify(_facts(posting_history="service", **chosen))
        self.assertEqual((verdict.cls, verdict.items["posting"]), ("A", "A"))
        self.assertEqual(auto.posting_by_history(_facts(posting_history="service", **chosen)), "A")
        verdict = auto.classify(_facts(posting_history="stock", **chosen))
        self.assertEqual((verdict.cls, verdict.items["posting"], verdict.reasons), ("C", "C", ("conflict:posting",)))
        # 对方没有 13 位税号：历史按名字对，最高 B
        nameless = _facts(posting_history="service", fields=_fields(seller_tax="", document_type="receipt"), **chosen)
        self.assertEqual(auto.tiers(nameless)["posting"], "B")
        # 不是按公司选的（本票声明）：这一层不适用
        self.assertEqual(auto.posting_by_history(_facts(posting_from="declared", posting_kind="service",
                                                        posting_history="stock")), "")

    def test_a_settled_posting_must_match_what_the_payload_writes(self):
        """一致也要载荷正好按它写：走库存要有库存行、每行存货有着落；记费用不能有库存行，票面 / 载荷明细的品名也不能与这份
        账套的库存品同名（第一次进货）。"""
        stock = dict(posting_from="company_choice", posting_kind="stock", posting_history="stock")
        line = {"name": "กระดาษ A4", "amount": "1000.00", "item_mode": "stock_item"}
        products = {"reported_suppliers": [{"code": "S001", "tax_id": SELLER, "branch": "00000"}],
                    "reported_accounts": [{"code": "5310-01"}],
                    "reported_products": [{"code": "P1", "name": "กระดาษ A4", "kind": "stock"}]}
        self.assertEqual(auto.posting_by_history(_facts(**stock)), "no_lines")  # 没读出明细：只写表头
        self.assertEqual(auto.posting_by_history(_facts(payload=_payload(items=[line]), config=products, **stock)), "A")
        self.assertEqual(auto.posting_by_history(_facts(payload=_payload(items=[{**line, "name": "ค่าขนส่ง"}]),
                                                        config=products, **stock)), "items")
        self.assertEqual(self.tier("posting", payload=_payload(items=[line]), **stock), "B")  # 没报存货主档：存货没着落
        service = dict(posting_from="company_choice", posting_kind="service", posting_history="service")
        self.assertEqual(auto.posting_by_history(_facts(config=products, **service)), "A")
        self.assertEqual(auto.posting_by_history(_facts(config=products, fields=_fields(items=[{"name": "กระดาษ  a4"}]),
                                                        **service)), "named")
        self.assertEqual(auto.posting_by_history(_facts(payload=_payload(items=[line]), **service)), "named")
        self.assertEqual(self.tier("posting", config=products, fields=_fields(items=[{"name": "กระดาษ A4"}]), **service),
                         "B")

    def test_a_c_item_makes_the_document_c(self):
        verdict = auto.classify(_facts(config={}))
        self.assertEqual((verdict.cls, verdict.reasons), ("C", ("item:party",)))


class MemoryTightening(unittest.TestCase):
    """10-18 E1 的三条（第六轮 strict A 类错 176 张全是账套记忆定的项）：R1 同账套记忆值被人改、R2 回读改动比例（科目）、R3 科目
    连续 5 张（见 `test_a_remembered_account_needs_five_in_a_row`）。改前（只看连续 3 张）这些单都是 A。"""

    SINCE = dt.datetime(2026, 10, 1, 9, 0)

    def payment(self, **over):
        base = dict(payload=_payload(doctype="HP", doctype_src="remembered"), payment_recent=["HP"] * 3,
                    memory_since={"payment": self.SINCE})
        base.update(over)
        return auto.tiers(_facts(**base))["payment"]

    def account(self, **over):
        base = dict(payload=_payload(account_source="remembered"), account_recent=["5310-01"] * 5,
                    memory_since={"account": self.SINCE})
        base.update(over)
        return auto.tiers(_facts(**base))["account"]

    def test_r1_a_memory_value_people_changed_in_the_same_account_set_is_b(self):
        """R1：这份账套近 60 天有人把账套记忆填的 v 改成别的（别家供应商也算），而这家记忆也是 v、最近 3 张证据里有早于那次改动的
        → 降 B。ASIA-FA 那种整份账套一起从现购换赊购：前几张停下的单被人改了，同期别家还凭旧证据判 A。"""
        changed = {"kind": "payment", "old": "cash", "new": "credit", "at": dt.datetime(2026, 10, 5, 10, 0),
                   "subject": "purchase|0105556012341"}
        self.assertEqual(self.payment(), "A")
        self.assertEqual(self.payment(overrides=[changed]), "B")
        verdict = auto.classify(_facts(payload=_payload(doctype="HP", doctype_src="remembered"), payment_recent=["HP"] * 3,
                                       memory_since={"payment": self.SINCE}, overrides=[changed]))
        self.assertEqual((verdict.cls, verdict.reasons), ("B", ("judged:payment",)))
        # 改的是别的值、别的类、改成的就是 v：不算
        self.assertEqual(self.payment(overrides=[{**changed, "old": "credit", "new": "cash"}]), "A")
        self.assertEqual(self.payment(overrides=[{**changed, "kind": "account", "old": "cash"}]), "A")
        self.assertEqual(self.payment(overrides=[{**changed, "new": "cash"}]), "A")
        # 改在这家最近 3 张证据之前（证据是改法之后攒的）、超过 60 天、在今天之后：不算
        self.assertEqual(self.payment(overrides=[{**changed, "at": dt.datetime(2026, 9, 30, 18, 0)}]), "A")
        self.assertEqual(self.payment(overrides=[{**changed, "at": self.SINCE}]), "A")
        self.assertEqual(self.payment(today=dt.date(2026, 12, 5), overrides=[changed]), "A")       # 61 天
        self.assertEqual(self.payment(today=dt.date(2026, 12, 4), overrides=[changed]), "B")       # 60 天
        self.assertEqual(self.payment(today=dt.date(2026, 10, 4), overrides=[changed]), "A")
        # 测试租户压缩时间试点（`push.automation.clock`）：判单据日子的今天是模拟的，R1 的「近 60 天」照真实今天（``now``）比改值记录
        self.assertEqual(self.payment(today=dt.date(2025, 9, 16), now=dt.date(2026, 10, 25), overrides=[changed]), "B")
        self.assertEqual(self.payment(today=dt.date(2025, 9, 16), overrides=[changed]), "A")  # 拿模拟日子比就漏了
        # 没给证据时刻（取不到）：不判；票面写明 / 人工裁决的现 / 赊不看
        self.assertEqual(self.payment(memory_since={}, overrides=[changed]), "A")
        self.assertEqual(self.payment(payload=_payload(doctype="HP", doctype_src="manual"), overrides=[changed]), "A")
        # 科目同一条：记忆的科目被人改掉
        moved = {"kind": "account", "old": "5310-01", "new": "5320-01", "at": dt.datetime(2026, 10, 3, 8, 0)}
        self.assertEqual(self.account(), "A")
        self.assertEqual(self.account(overrides=[moved]), "B")
        self.assertEqual(self.account(overrides=[{**moved, "old": "5399"}]), "A")
        self.assertEqual(auto.memory_holds(_facts(payload=_payload(account_source="remembered"),
                                                  account_recent=["5310-01"] * 5, memory_since={"account": self.SINCE},
                                                  overrides=[moved]), "account"), ("overruled",))
        # 回放用日子：同一天不算晚于（只认改在证据之后的那天及以后）；日子与时刻混着时按日子比
        day = dt.date(2026, 10, 1)
        self.assertFalse(auto.overruled("account", "5310-01", day, [{**moved, "at": day}], dt.date(2026, 10, 25)))
        self.assertTrue(auto.overruled("account", "5310-01", day, [{**moved, "at": dt.date(2026, 10, 2)}],
                                       dt.date(2026, 10, 25)))
        self.assertTrue(auto.overruled("account", "5310-01", "2026-10-01T09:00:00", [moved], dt.date(2026, 10, 25)))
        self.assertFalse(auto.overruled("party", "S1", day, [{**moved, "kind": "party", "old": "S1"}],
                                        dt.date(2026, 10, 25)))  # 对方档不上 R1
        self.assertEqual((auto.OVERRIDE_KINDS, auto.OVERRIDE_DAYS, auto.OVERRIDE_EVIDENCE), (("account", "payment"), 60, 3))
        self.assertEqual(auto.memory_value("payment", {"doctype": "rr"}), "credit")
        self.assertEqual(auto.memory_value("account", {"items_account": " 5310-01 "}), "5310-01")

    def test_r2_an_account_changed_in_more_than_a_fifth_of_the_readbacks_is_b(self):
        """R2（只管科目）：证据窗口里有合格回读结果的推送中，科目被会计改的超过 20% → 科目降 B（「混着记」「一单多科目」：NEWLINK
        一单多科目与单科目交替，窗口里刚好凑到 5 张同科目也不算定下）。正好 20% 不降；没有结果的不算。"""
        self.assertEqual(self.account(account_share=(1, 5)), "A")
        self.assertEqual(self.account(account_share=(2, 9)), "B")
        self.assertEqual(self.account(account_share=(0, 0)), "A")
        self.assertEqual(self.account(account_share=None), "A")
        self.assertEqual(auto.memory_holds(_facts(payload=_payload(account_source="remembered"),
                                                  account_recent=["5310-01"] * 5, account_share=(3, 4)), "account"),
                         ("changed_share",))
        # 现 / 赊不看这一条
        self.assertEqual(self.payment(account_share=(9, 9)), "A")
        self.assertEqual(auto.ACCOUNT_CHANGE_PERCENT, 20)
        self.assertFalse(auto.share_exceeded((1, 5)))
        self.assertTrue(auto.share_exceeded((1, 4)))

        def entry(readback, kinds=(), **over):
            return {"account": "5310-01", "source": "remembered", "readback": readback, "changed_kinds": kinds,
                    "payment": "RR", "payment_source": "remembered", **over}
        rows = [entry("confirmed"), entry("changed", ["account"]), entry("changed", ["payment"]), entry(""),
                entry("changed", None), entry("confirmed", stock=True), entry("changed", ["account"], stock=True)]
        # 只改了现 / 赊的不算科目被改；整张被改（不知道改了哪几类）算；还没回读的、写了库存行的不算有结果
        self.assertEqual(auto.change_share(rows), (2, 4))
        self.assertEqual(auto.change_share(rows, "payment"), (2, 6))  # 库存行不影响现 / 赊
        self.assertEqual(auto.change_share([entry("changed", ["payment"], payment_source="explicit")], "payment"), (0, 0))
        # 同一个窗口（往回 1,000 张、6 个税期，先截）
        many = [entry("")] * 1000 + [entry("changed", ["account"])]
        self.assertEqual(auto.change_share(many), (0, 0))
        self.assertEqual(auto.change_share(many, scan=0), (1, 1))

    def test_memory_since_is_the_oldest_of_the_newest_three_pieces_of_evidence(self):
        """R1 比的「这家最近 3 张证据」：证据窗口里最前面 3 张证据（跳过没核过的记忆推送）的最早时刻；不够 3 张、有断开的、缺时刻
        的回 None。"""
        def entry(day, readback="confirmed", source="remembered"):
            return {"account": "5310-01", "source": source, "readback": readback, "at": dt.datetime(2026, 9, day, 10)}
        rows = [entry(20, ""), entry(19), entry(18, source="chosen", readback=""), entry(15), entry(2)]
        self.assertEqual(auto.memory_since(rows), dt.datetime(2026, 9, 15, 10))
        self.assertIsNone(auto.memory_since(rows[:3]))
        self.assertIsNone(auto.memory_since([entry(19), entry(18, "changed"), entry(15)]))
        self.assertIsNone(auto.memory_since([entry(19), {**entry(18), "at": None}, entry(15)]))
        self.assertEqual(auto.memory_since([{**entry(19), "at": dt.date(2026, 9, 19)}, entry(18), entry(15)]),
                         dt.date(2026, 9, 15))  # 日子与时刻混着按日子
        pay = [{"payment": "HP", "payment_source": "manual", "readback": "", "at": dt.date(2026, 9, day)}
               for day in (9, 8, 7)]
        self.assertEqual(auto.memory_since(pay, "payment"), dt.date(2026, 9, 7))


class Conflicts(unittest.TestCase):
    """各层说法不一归 C（哪怕优先级能裁出一个）；只看这张单真用了这几层的类，Express 历史只认 31 天内读的。"""

    def test_layers_collects_each_source(self):
        fields = _fields()
        subject = choice_rules.supplier_subject("purchase", SELLER, fields["seller_name"])
        found = auto.layers(fields, direction="purchase", fixed={"account": "5310-01", "party": "S9"},
                            remembered={"account": {subject: "5390-99"}, "payment": {subject: "cash"}},
                            rules=[{"kind": "account", "subject": subject, "value": "5310-02", "branch": ""},
                                   {"kind": "payment", "subject": subject, "value": "cash", "branch": "00001"}],
                            branch="00000", history="5320-01")
        self.assertEqual(found, {"account": {"task_default": "5310-01", "remembered": "5390-99",
                                             "company_rule": "5310-02", "express_history": "5320-01"},
                                 "payment": {"remembered": "cash"}})

    def test_disagreeing_layers_make_c(self):
        layers = {"account": {"task_default": "5310-01", "remembered": "5390-99"}, "payment": {"remembered": "cash"}}
        verdict = auto.classify(_facts(layers=layers))
        self.assertEqual((verdict.cls, verdict.reasons), ("C", ("conflict:account",)))
        # 一致的不算；这张单没用这几层定的类不算
        self.assertEqual(auto.conflicts(_facts(layers={"account": {"task_default": "5310-01",
                                                                   "remembered": "5310-01"}})), [])
        self.assertEqual(auto.conflicts(_facts(layers={"payment": {"task_default": "cash", "remembered": "credit"}})),
                         [])

    def test_only_a_fresh_history_counts(self):
        layers = {"account": {"remembered": "5310-01", "express_history": "5320-01"}}
        self.assertEqual(auto.conflicts(_facts(layers=layers)), [])
        self.assertEqual(auto.conflicts(_facts(layers=layers, history_read=dt.date(2026, 9, 1))), [])
        self.assertEqual(auto.conflicts(_facts(layers=layers, history_read=dt.date(2026, 10, 1))), ["account"])
        # 模拟的今天（试点）不拿来比 Express 历史读的真实日子：按真实今天（``now``）
        self.assertEqual(auto.conflicts(_facts(layers=layers, history_read=dt.date(2026, 10, 1),
                                               today=dt.date(2025, 9, 16), now=dt.date(2026, 10, 25))), ["account"])
        self.assertEqual(auto.conflicts(_facts(layers=layers, history_read=dt.date(2026, 10, 1),
                                               today=dt.date(2025, 9, 16), now=dt.date(2026, 12, 1))), [])


class Checks(unittest.TestCase):
    """第 2 条每一项。"""

    def reasons(self, **over):
        return auto.checks(_facts(**over))

    def test_a_clean_document_passes(self):
        self.assertEqual(self.reasons(), [])

    def test_hard_gates_and_choices(self):
        self.assertIn("blocked", self.reasons(blocked="account_period_mismatch"))
        self.assertIn("not_ready", self.reasons(ready=False))
        self.assertIn("not_ready", self.reasons(payload=None))
        self.assertIn("needed", self.reasons(decision={"needed": [{"kind": "account"}], "decided_by": {}}))

    def test_recognition_side(self):
        fields = _fields()
        self.assertIn("review_flag", self.reasons(pages=[{"_layer_chain": ["text", "L2"], "fields": fields,
                                                          "_needs_manual_review": True}]))
        self.assertIn("review_reasons", self.reasons(review_reasons=[{"code": "duplicate"}]))
        # 分档不是闸：auto 以外的档照样能过
        self.assertEqual(self.reasons(pages=[{"_layer_chain": ["text", "L2"], "fields": fields,
                                              "_confidence_band": "yellow_confirm"}]), [])

    def test_images_are_c_until_the_replay_says_otherwise(self):
        self.assertFalse(auto.IMAGES_ALLOWED)
        self.assertIn("image", self.reasons(pages=[{"_layer_chain": ["direct", "L1", "L2"], "fields": _fields()}],
                                            text=None))
        self.assertIn("image", self.reasons(pages=[{"_layer_chain": ["text", "L2"]}, {"_layer_chain": ["L1", "L2"]}]))
        self.assertIn("image", self.reasons(pages=[]))

    def test_an_electronic_pdf_read_like_an_image_counts_by_its_text_layer(self):
        # 生产的文字层快路关着：电子 PDF 照图片直读（识别链不以 text 开头），按原件文字层的字数认（同识别取文字层的门槛）
        self.assertEqual(auto.TEXT_CHARS_PER_PAGE, 200)
        source = (APP / "ocr/inputs_runtime/source.py").read_text(encoding="utf-8")
        self.assertIn("sum(map(len, texts)) < 200 * page_count", source)
        direct = [{"_layer_chain": ["direct"], "fields": _fields()}]
        long_text = TEXT + " " * 5 + "x" * (200 - len(TEXT))
        self.assertEqual(self.reasons(pages=direct, text=long_text), [])
        self.assertIn("image", self.reasons(pages=direct, text=TEXT))
        self.assertIn("image", self.reasons(pages=direct * 2, text=long_text))
        self.assertTrue(auto.text_layer(direct * 2, [long_text, long_text]))
        self.assertFalse(auto.text_layer([], long_text))

    def test_every_page_needs_its_own_text_layer(self):
        # 逐页判（审查处理 10-16）：扫描发票页 + 长文字附页、一段字只当一页、隐形识别文字层的页（给空串）都不算
        direct = [{"_layer_chain": ["direct"], "fields": _fields()}]
        long_text = TEXT + " " + "x" * 200
        self.assertFalse(auto.text_layer(direct * 2, ["", "y" * 450]))
        self.assertFalse(auto.text_layer(direct * 2, "y" * 450))
        self.assertFalse(auto.text_layer(direct * 2, [long_text]))
        self.assertIn("image", self.reasons(pages=direct * 2, text="y" * 450 + TEXT, page_texts=["", "y" * 450 + TEXT]))
        self.assertEqual(self.reasons(pages=direct * 2, text=long_text * 2, page_texts=[long_text, long_text]), [])

    def test_a_formal_tax_invoice_shows_the_party_tax_id_in_the_text_layer(self):
        self.assertIn("text_tax", self.reasons(text=TEXT.replace(SELLER, "")))
        self.assertEqual(self.reasons(text=TEXT.replace(SELLER, "0 1075 61000 01 3")), [])
        self.assertEqual(self.reasons(text=TEXT.replace(SELLER, "0-1075-61000-01-3")), [])
        self.assertIn("text_tax", self.reasons(text=TEXT.replace(SELLER, "10107561000013")))
        # 收据不要求
        self.assertNotIn("text_tax", self.reasons(fields=_fields(document_type="receipt"), text=TEXT.replace(SELLER, "")))
        self.assertTrue(auto.tax_in_text(SELLER, "TAX ID " + SELLER))
        self.assertFalse(auto.tax_in_text("123", "123"))

    def test_own_tax_must_be_the_account_sets(self):
        # 本方税号读错一位（校验位不过）、印的是别家、正式税票没印：自动推不放行（人推照旧按 account_match）
        self.assertIn("own_tax", self.reasons(fields=_fields(buyer_tax="0994000333448")))
        self.assertIn("own_tax", self.reasons(fields=_fields(buyer_tax=OTHER)))
        self.assertIn("own_tax", self.reasons(fields=_fields(buyer_tax="")))
        # 收据 / 简式税票不印本方：本方这一项算 CC 判断（B）
        receipt = _fields(buyer_tax="", document_type="receipt")
        self.assertNotIn("own_tax", self.reasons(fields=receipt))
        self.assertEqual(auto.tiers(_facts(fields=receipt))["own"], "B")
        verdict = auto.classify(_facts(fields=receipt))
        self.assertEqual((verdict.cls, verdict.reasons), ("B", ("judged:own",)))
        # 销项看卖方（本方）；账套没有 13 位税号核不了，不列
        sales = _fields(direction="sales", seller_tax=OTHER, buyer_tax=SELLER)
        self.assertIn("own_tax", self.reasons(direction="sales", fields=sales,
                                              payload=_payload(direction="sales", doctype="HS")))
        no_tax = {"source_id": BOOK, "attributes": {**ACCOUNT["attributes"], "tax_id": ""}}
        self.assertNotIn("own_tax", self.reasons(fields=_fields(buyer_tax=OTHER), account=no_tax, catalog=[no_tax]))
        self.assertNotIn("own", auto.tiers(_facts(account=no_tax, catalog=[no_tax])))

    def test_the_text_layer_must_show_the_total_and_the_date(self):
        self.assertIn("text_missing", self.reasons(text=None))
        self.assertIn("text_missing", self.reasons(text="  "))
        self.assertEqual(self.reasons(text=f"Tax ID {SELLER} Total 1070.00 Date 20 ต.ค. 2569"), [])
        self.assertEqual(self.reasons(text=f"Tax ID {SELLER} Grand total 1,070 dated October 20, 2026"), [])
        self.assertEqual(self.reasons(text=f"Tax ID {SELLER} Grand total 1,070.00 dated 20-Oct-26"), [])
        self.assertIn("text_total", self.reasons(text="รวม 1,070.50 วันที่ 20/10/2569"))
        self.assertIn("text_total", self.reasons(text="รวม 11,070.00 วันที่ 20/10/2569"))
        self.assertIn("text_date", self.reasons(text="รวม 1,070.00 วันที่ 10/20/2026"))
        self.assertIn("text_date", self.reasons(text="รวม 1,070.00 วันที่ 21/10/2569"))

    def test_text_helpers(self):
        self.assertTrue(auto.amount_in_text("1070", "x 1,070.00, y"))
        self.assertFalse(auto.amount_in_text("70", "1,070.00"))
        self.assertFalse(auto.amount_in_text("34", "12,34"))
        day = dt.date(2026, 1, 15)
        for text in ("15/01/2569", "15.1.69", "15-01-2026", "2026-01-15", "15 มกราคม 2569", "15 ม.ค. 69",
                     "15 Jan 2026", "January 15, 2026"):
            self.assertTrue(auto.date_in_text(day, text), text)
        self.assertFalse(auto.date_in_text(day, "market 15, 2026"))
        self.assertFalse(auto.date_in_text(None, "15/01/2569"))

    def test_amounts_are_read_not_inferred(self):
        # 只读到含税额（common.amounts 按 7% 反推）
        self.assertIn("amount_inferred", self.reasons(fields=_fields(subtotal="", vat="")))
        # 识别后按 7/107 拆的、补出的折扣
        pages = [{"_layer_chain": ["text", "L2"], "fields": _fields(),
                  "_extraction_audit": {"vat_included": {"subtotal": "1070", "vat": "0"}}}]
        self.assertIn("amount_inferred", self.reasons(pages=pages))
        pages = [{"_layer_chain": ["text", "L2"], "fields": _fields(),
                  "_validation_warnings": ["discount_inferred: 50"]}]
        self.assertIn("amount_inferred", self.reasons(pages=pages))
        # 正式税票：未税、税额都要读到且勾稽成立（容差同识别校验 0.5）；免税单税额为 0 可以
        self.assertIn("amount_math", self.reasons(fields=_fields(vat="")))
        self.assertIn("amount_math", self.reasons(fields=_fields(vat="75.00")))
        self.assertEqual(self.reasons(fields=_fields(vat="70.40")), [])
        self.assertEqual(self.reasons(fields=_fields(subtotal="1070.00", vat="0")), [])
        self.assertEqual(self.reasons(fields=_fields(subtotal="1100.00", discount="100.00")), [])
        self.assertEqual(auto.AMOUNT_TOLERANCE, auto.Decimal("0.5"))

    def test_the_tolerance_matches_the_recognition_check(self):
        source = (APP / "ocr/engine/config.py").read_text(encoding="utf-8")
        self.assertIn('amount_tolerance=decimal("OCR_PIPELINE_AMOUNT_TOL", ".5"', source)

    def test_party_tax_on_a_formal_tax_invoice(self):
        self.assertIn("party_tax", self.reasons(fields=_fields(seller_tax="0107561000011")))
        self.assertIn("party_tax", self.reasons(fields=_fields(seller_tax="010756100001")))
        self.assertNotIn("party_tax", self.reasons(fields=_fields(seller_tax="", document_type="receipt")))

    def test_dates(self):
        text = lambda day: f"รวม 1,070.00 วันที่ {day}"  # noqa: E731
        self.assertIn("date_missing", self.reasons(fields=_fields(date="")))
        # 次年段（到 next_end）也在期间里：新年度账套还没建时下一年的单记进旧账套（用户 10-16）；新年度账套已建时由
        # 「推往的须是按日期选出的那份」拦（day_books 是新账套）
        next_year = dict(fields=_fields(date="2027-01-05"), text=text("05/01/2570") + " " + SELLER,
                         today=dt.date(2027, 1, 10),
                         uploaded=dt.date(2027, 1, 8))
        self.assertEqual(self.reasons(**next_year), [])
        self.assertIn("book", self.reasons(day_books=[BOOK_OLD], **next_year))
        # 晚于次年段、没报次年段：照旧不在期间里
        self.assertIn("period", self.reasons(fields=_fields(date="2028-01-05"), text=text("05/01/2571"),
                                             today=dt.date(2028, 1, 10), uploaded=dt.date(2028, 1, 8)))
        no_next = {"source_id": BOOK, "attributes": {k: v for k, v in ACCOUNT["attributes"].items() if k != "next_end"}}
        self.assertIn("period", self.reasons(account=no_next, catalog=[no_next], **next_year))
        self.assertIn("period_unknown", self.reasons(account={"source_id": BOOK, "attributes": {"tax_id": OWN}}))
        # 推往的账套须正好是按这张单日期选出的那份；选不出、选出多份、选出另一份分开记（10-17 拆码，结果都是 C）
        for day_books, code in ((None, "book_none"), ([], "book_none"), ([BOOK, BOOK_OLD], "book_many"),
                                ([BOOK_OLD, BOOK_OLD.lower()], "book"), ([BOOK_OLD], "book")):
            found = self.reasons(day_books=day_books)
            self.assertIn(code, found, day_books)
            self.assertEqual(len({"book", "book_many", "book_none"} & set(found)), 1, found)
            self.assertEqual(auto.classify(_facts(day_books=day_books)).cls, auto.C)
        self.assertEqual(self.reasons(day_books=["c:/PearnlyExpress/69ACME/"]), [])
        self.assertIn("date_future", self.reasons(today=dt.date(2026, 10, 19), uploaded=dt.date(2026, 10, 19)))
        self.assertIn("date_stale", self.reasons(uploaded=dt.date(2026, 12, 20), today=dt.date(2026, 12, 21)))
        self.assertNotIn("date_stale", self.reasons(uploaded=dt.date(2026, 12, 19), today=dt.date(2026, 12, 21)))

    def test_day_and_month_both_twelve_or_less_and_both_plausible(self):
        def reasons(day, text, today, uploaded):
            return self.reasons(fields=_fields(date=day), text=f"รวม 1,070.00 วันที่ {text}", today=today,
                                uploaded=uploaded)
        # 10-09 与 09-10 都在期间里、都在上传前 60 天内、都不晚于今天 → 两可
        self.assertIn("day_month", reasons("2026-10-09", "09/10/2569", dt.date(2026, 10, 12), dt.date(2026, 10, 11)))
        # 对调后是未来 / 太早：不两可
        self.assertNotIn("day_month", reasons("2026-03-05", "05/03/2569", dt.date(2026, 3, 8), dt.date(2026, 3, 7)))
        self.assertNotIn("day_month", reasons("2026-10-03", "03/10/2569", dt.date(2026, 10, 5), dt.date(2026, 10, 4)))
        # 日 = 月、日 > 12：不两可
        self.assertNotIn("day_month", reasons("2026-10-10", "10/10/2569", dt.date(2026, 10, 12), dt.date(2026, 10, 11)))

    def test_branch_must_be_printed_when_the_company_has_several(self):
        branch1 = {"source_id": r"C:\PearnlyExpress\69ACME1", "attributes": {
            "tax_id": OWN, "branch": "00001", "period_start": "2026-01-01", "period_end": "2026-12-31"}}
        catalog = [ACCOUNT, branch1]
        self.assertEqual(self.reasons(catalog=catalog), [])
        self.assertIn("branch", self.reasons(catalog=catalog, fields=_fields(buyer_branch="")))
        self.assertIn("branch", self.reasons(catalog=catalog, fields=_fields(buyer_branch="00001")))
        self.assertEqual(self.reasons(catalog=catalog, fields=_fields(buyer_branch="สำนักงานใหญ่")), [])
        # 只有一个分店的公司不按分店拦
        self.assertEqual(self.reasons(fields=_fields(buyer_branch="")), [])

    def test_void_split_and_evidence(self):
        self.assertIn("void", self.reasons(text=TEXT + " ยกเลิก"))
        self.assertIn("void", self.reasons(text=TEXT + " VOID"))
        self.assertIn("void", self.reasons(text=TEXT + " ใบรับคืน"))
        self.assertNotIn("void", self.reasons(text=TEXT + " avoid"))
        self.assertIn("split", self.reasons(source_total=2))
        fields = _fields()
        evidence = [{"_layer_chain": ["text", "L2"], "fields": fields},
                    {"_layer_chain": ["text", "L2"], "fields": {"is_not_invoice": True}}]
        self.assertIn("evidence", self.reasons(pages=evidence))
        attached = [{"_layer_chain": ["text", "L2"], "fields": fields,
                     "_validation_warnings": ["attachment_page: same_number IV69-001"]}]
        self.assertIn("evidence", self.reasons(pages=attached))

    def test_automatic_push_leaves_an_already_pushed_document_to_a_person(self):
        prior = {"direction": "purchase", "party_tax_id": SELLER, "ref_no": fit_ref_no("IV69-001"), "total": "1070.00"}
        self.assertIn("duplicate_number", self.reasons(prior_pushes=[prior]))
        self.assertNotIn("duplicate_number", self.reasons(prior_pushes=[{**prior, "total": "1071.00"}]))
        self.assertNotIn("duplicate_number", self.reasons(prior_pushes=[{**prior, "ref_no": "IV69-002"}]))
        self.assertNotIn("duplicate_number", self.reasons(prior_pushes=[{**prior, "party_tax_id": OTHER}]))
        self.assertNotIn("duplicate_number", self.reasons(prior_pushes=[{**prior, "direction": "sales"}]))
        # 写进 Express 时缩短过的单号照样认得（同回读）
        long_ref = "INV-2026/10-000123456"
        found = self.reasons(fields=_fields(invoice_number=long_ref), payload=_payload(ref_no=long_ref),
                             text=TEXT, prior_pushes=[{**prior, "ref_no": fit_ref_no(long_ref)}])
        self.assertIn("duplicate_number", found)

    def test_reason_codes_fit_the_column(self):
        verdict = auto.Verdict("C", tuple(f"reason_{i:03d}" for i in range(40)), {})
        text = auto.reason_codes(verdict)
        self.assertLessEqual(len(text), 140)
        self.assertTrue(text.startswith("reason_000,reason_001"))
        self.assertEqual(auto.reason_codes(auto.Verdict("A", (), {})), "")
        # 待处理 / 复核页据 book_many 当场列候选账套：原因再多也排最前、截不掉（第五轮后审查）
        many = auto.Verdict("C", (*(f"reason_{i:03d}" for i in range(40)), "book_many"), {})
        self.assertTrue(auto.reason_codes(many).startswith("book_many,reason_000"))


# 第 3 期 E11（10-19）：真实电子税票「原件 + 副本 / 收据联」几页一份。下面的形状取自测试机试点导出（BakeLab 销项 18 张，
# 脱敏）：第 1 页是税票，第 2 页起识别（Claude 路线）记成非发票、字段全空、``claude: no document starts on this page``，
# 整份副本标记 ``is_copy_or_duplicate``；判据只能看那一页自己的 PDF 文字层。
E11_NUMBER, E11_TOTAL = "INV2025080061", "414.00"
E11_FIELDS = dict(direction="sales", document_type="tax_invoice", seller_tax=OWN, seller_branch="00000",
                  buyer_tax=SELLER, buyer_branch="", invoice_number=E11_NUMBER, date="2026-10-06",
                  subtotal="386.92", vat="27.08", total_amount=E11_TOTAL)
E11_BLANK = {"buyer_tax": "", "date": None, "document_type": "other", "invoice_number": None, "is_copy_or_duplicate": True,
             "is_not_invoice": True, "notes": "claude: no document starts on this page", "seller_tax": "",
             "total_amount": None, "items": []}
E11_CHAIN = ["CLAUDE", "CLAUDE-HAIKU-5-5"]
E11_LINES = "CAKE : Longan white chocolate cake ราคาส่ง ขนาด 3 ปอนด์ แยกหน้า/ตัด 10 1 ชิ้น 386.92 ติดต่อ 081-0000000 "
E11_INVOICE = (f"ใบส่งสินค้า/ใบกำกับภาษี สำเนา เลขที่ {E11_NUMBER} วันที่ 06/10/2026 ผู้ขาย {OWN} สำนักงานใหญ่ "
               f"ลูกค้า เลขประจำตัวผู้เสียภาษี {SELLER} {E11_LINES}รวมเป็นเงิน 386.92 ภาษีมูลค่าเพิ่ม 27.08 "
               "จำนวนเงินรวมทั้งสิ้น 414.00")
E11_RECEIPT = (f"ใบเสร็จรับเงิน สำเนา เลขที่ {E11_NUMBER} วันที่ 06/10/2026 ผู้ขาย {OWN} ลูกค้า เลขประจำตัวผู้เสียภาษี "
               f"{SELLER} {E11_LINES}[X] เงินสด จำนวนเงิน 414.00 (สี่ร้อยสิบสี่บาทถ้วน) ใบแนบใบกำกับภาษี")


def _e11(*page_texts, fields=None, extra=None, **over):
    fields = _fields(**{**E11_FIELDS, **(fields or {})})
    pages = [{"_layer_chain": E11_CHAIN, "fields": fields}]
    pages += [{"_layer_chain": E11_CHAIN, "fields": {**E11_BLANK, **((extra or {}).get(index) or {})}}
              for index in range(1, len(page_texts))]
    payload = _payload(direction="sales", doctype="IV", ref_no=E11_NUMBER, total_amount=E11_TOTAL,
                       customer={"code": "C1", "tax_id": SELLER}, supplier=None)
    base = dict(direction="sales", fields=fields, pages=pages, page_texts=list(page_texts),
                text="\n".join(page_texts), payload=payload, today=dt.date(2026, 10, 25), uploaded=dt.date(2026, 10, 22))
    base.update(over)
    return _facts(**base)


class SameDocumentPages(unittest.TestCase):
    """后面的页是同一张票的再印（单号、含税额、对方税号都在那一页自己的文字层里）就不算证据页；对不上的照旧 C。"""

    def evidence(self, facts):
        return "evidence" in auto.checks(facts)

    def test_the_e11_receipt_copy_page_is_not_evidence(self):
        facts = _e11(E11_INVOICE, E11_RECEIPT)
        self.assertTrue(auto.same_document_page(facts, 1))
        self.assertEqual(auto.checks(facts), [])  # 原先 ["evidence"]（日期按试点压缩时间另给）
        # 三、四页一份（原件 + 两页收据联 / 副本）：每一页都要对得上
        self.assertFalse(self.evidence(_e11(E11_INVOICE, E11_RECEIPT, E11_RECEIPT, E11_RECEIPT)))
        self.assertTrue(self.evidence(_e11(E11_INVOICE, E11_RECEIPT, "ใบเสร็จรับเงิน รายการต่อ", E11_RECEIPT)))

    def test_pages_that_do_not_repeat_the_same_document_stay_evidence(self):
        other = E11_RECEIPT.replace(E11_NUMBER, "INV2025080062")
        cases = {
            "no text": "",                                                          # 扫描件 / 隐形识别文字层
            "another number": other,
            "longer number": E11_RECEIPT.replace(E11_NUMBER, E11_NUMBER + "1"),
            "no total": E11_RECEIPT.replace("414.00", "400.00"),
            "no party tax": E11_RECEIPT.replace(SELLER, ""),
            "credit note": E11_RECEIPT.replace("ใบเสร็จรับเงิน", "ใบลดหนี้"),
            "debit note": E11_RECEIPT + " DEBIT NOTE",
        }
        for name, text in cases.items():
            self.assertTrue(self.evidence(_e11(E11_INVOICE, text)), name)
        # 文字层与识别页对不上、只有整份连起来的一段、一拆多：不判
        self.assertTrue(self.evidence(_e11(E11_INVOICE, E11_RECEIPT, page_texts=[E11_INVOICE])))
        self.assertTrue(self.evidence(_e11(E11_INVOICE, E11_RECEIPT, page_texts=None)))
        self.assertTrue(self.evidence(_e11(E11_INVOICE, E11_RECEIPT, page_texts=E11_INVOICE + E11_RECEIPT)))
        self.assertTrue(self.evidence(_e11(E11_INVOICE, E11_RECEIPT, source_total=2)))
        # 这一页识别自己读出了另一张（单号或含税额不同）
        self.assertTrue(self.evidence(_e11(E11_INVOICE, E11_RECEIPT, extra={1: {"invoice_number": "INV2025080062"}})))
        self.assertTrue(self.evidence(_e11(E11_INVOICE, E11_RECEIPT, extra={1: {"total_amount": "400.00"}})))
        self.assertFalse(self.evidence(_e11(E11_INVOICE, E11_RECEIPT,
                                            extra={1: {"invoice_number": E11_NUMBER.lower(), "total_amount": "414"}})))
        # 对方没有 13 位税号、单号太短、含税额为 0：判不成
        self.assertTrue(self.evidence(_e11(E11_INVOICE, E11_RECEIPT, fields={"buyer_tax": ""})))
        short = E11_RECEIPT.replace(E11_NUMBER, "A12")
        self.assertTrue(self.evidence(_e11(E11_INVOICE.replace(E11_NUMBER, "A12"), short,
                                           fields={"invoice_number": "A12"}, payload=_payload(ref_no="A12"))))

    def test_a_credit_note_may_repeat_on_its_copy_page(self):
        note = E11_RECEIPT.replace("ใบเสร็จรับเงิน", "ใบลดหนี้")
        self.assertFalse(self.evidence(_e11(E11_INVOICE, note, fields={"document_type": "credit_note"})))

    def test_attachment_pages_follow_the_same_rule(self):
        """谷歌路线丢下的附属页（``attachment_page:``，记在被丢下的那一页上）：同一条判据；第一页上的照旧算证据。"""
        same = {"_validation_warnings": [f"attachment_page: same_number {E11_NUMBER}"]}
        facts = _e11(E11_INVOICE, E11_RECEIPT)
        pages = [facts.pages[0], {**facts.pages[1], **same}]
        self.assertFalse(self.evidence(dataclasses.replace(facts, pages=pages)))
        first = [{**facts.pages[0], **same}, facts.pages[1]]
        self.assertTrue(self.evidence(dataclasses.replace(facts, pages=first)))
        pages = [facts.pages[0], {**facts.pages[1], **same, "fields": {"document_type": "other"}}]
        self.assertTrue(self.evidence(dataclasses.replace(facts, pages=pages, page_texts=[E11_INVOICE, ""])))

    def test_number_in_text(self):
        self.assertTrue(auto.number_in_text("IV69-001", "เลขที่ IV69-001 วันที่"))
        self.assertTrue(auto.number_in_text("iv69-001", "No.IV69-001"))
        self.assertTrue(auto.number_in_text("IV69-001", "IV69 -001"))   # 文字层在字之间断开一个空白
        self.assertFalse(auto.number_in_text("IV69-001", "IV69-0012"))
        self.assertFalse(auto.number_in_text("IV69-001", "XIV69-001"))
        self.assertFalse(auto.number_in_text("A12", "A12"))
        self.assertFalse(auto.number_in_text("IV69-001", None))
        self.assertEqual(auto.NUMBER_MIN, 4)


class DecideCore(unittest.TestCase):
    """判定核心（第 11 条抽出的纯函数）：历史已定带上汇总版本，classify 据此分 A / B。"""

    FIELDS = {"seller_name": "ศรีกรุง", "seller_tax": OTHER, "payment_status": "paid"}

    def test_settled_history_carries_its_version(self):
        def run(settled):
            decision, sources = decide_rules.decide(self.FIELDS, direction="purchase", posting_kind="service",
                                                    config={"reported_accounts": [{"code": "5320-01"}]},
                                                    remembered={}, settle=lambda needed: settled)
            return decision, sources
        decision, sources = run({"code": "5320-01", "docs": 4})
        self.assertEqual(decision["history"], {"account": "5320-01", "docs": 4})
        self.assertEqual(sources["account"], {"purchase|" + OTHER: "express_history"})
        decision, _ = run({"code": "5320-01", "docs": 4, "version": 2})
        self.assertEqual(decision["history"], {"account": "5320-01", "docs": 4, "version": 2})
        self.assertEqual(decision["decided_by"], {"account": "express_history"})

    def test_rules_not_read_never_use_the_history(self):
        asked = []
        decision, _ = decide_rules.decide(self.FIELDS, direction="purchase", posting_kind="service",
                                          config={"reported_accounts": [{"code": "5320-01"}]}, remembered={},
                                          rules_read=False, settle=lambda needed: asked.append(needed))
        self.assertEqual(([item["kind"] for item in decision["needed"]], asked), (["account"], []))


def _cid_pdf(*pages):
    """造一份多页 PDF：每页一个 Type0 / Identity-H 字体（不嵌字形），字形码照子集字体从 3 起按首次出现顺序编，文字层靠 ToUnicode。
    ``pages`` 每项 ``(文字, ToUnicode 坏不坏)``；坏的那份 ToUnicode 第一项源码是奇数长度十六进制串（``<003>``），pypdf 报
    「Skipping broken line … Odd-length string」丢掉整行、把字形码原样给出（\x03\x04…），pymupdf 照常读——同 E11 BakeLab 副本页。"""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None]
    kids = []
    for text, broken in pages:
        codes = {}
        for char in text:
            codes.setdefault(char, len(codes) + 3)
        pairs = " ".join("<%04X> <%04X>" % (code, ord(char)) for char, code in codes.items())
        cmap = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /T def 1 begincodespacerange "
                "<0000> <FFFF> endcodespacerange\n%d beginbfchar\n%s%s\nendbfchar\nendcmap CMapName currentdict /CMap "
                "defineresource pop end end") % (len(codes) + broken, "<003> <0020> " if broken else "", pairs)
        content = "BT /F1 2 Tf 20 800 Td <%s> Tj ET" % "".join("%04X" % codes[char] for char in text)
        base = len(objects) + 1
        kids.append(base)
        objects += [
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (base + 1, base + 2),
            "<< /Type /Font /Subtype /Type0 /BaseFont /AAAAAA+Sarabun /Encoding /Identity-H /DescendantFonts [%d 0 R] "
            "/ToUnicode %d 0 R >>" % (base + 3, base + 4),
            "<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
            "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /AAAAAA+Sarabun /CIDSystemInfo << /Registry (Adobe) "
            "/Ordering (Identity) /Supplement 0 >> /CIDToGIDMap /Identity /DW 500 /FontDescriptor %d 0 R >>" % (base + 5),
            "<< /Length %d >>\nstream\n%s\nendstream" % (len(cmap.encode()), cmap),
            "<< /Type /FontDescriptor /FontName /AAAAAA+Sarabun /Flags 32 /FontBBox [0 0 1000 1000] /ItalicAngle 0 "
            "/Ascent 900 /Descent -200 /CapHeight 700 /StemV 80 >>",
        ]
    objects[1] = "<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % kid for kid in kids), len(kids))
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += ("%d 0 obj\n%s\nendobj\n" % (number, body)).encode()
    xref = len(out)
    out += ("xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)).encode()
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    return out + ("trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)).encode()


GARBAGE = "".join(chr(code) for code in (3, 4, 5, 6, 4, 7, 8, 3, 9, 10, 11, 12, 13, 14, 3, 15, 16, 17, 18))


class _FakePage:
    def __init__(self, text, content=b"BT /F1 9 Tf (x) Tj ET", warn=False):
        self.text, self.content, self.warn = text, content, warn

    def get_contents(self):
        return type("Stream", (), {"get_data": lambda _self: self.content})()

    def extract_text(self):
        if self.warn:  # 同 pypdf 6.15 `_cmap.process_cm_line`
            import logging

            logging.getLogger("pypdf._cmap").warning("Skipping broken line %(line)r: %(error)s",
                                                     {"line": b"003 0020", "error": "Odd-length string"})
        return self.text


def _fake_pypdf(*pages):
    return type(sys)("pypdf"), type("Reader", (), {"__init__": lambda self, stream: setattr(self, "pages", list(pages))})


def _fake_mupdf(texts, opened, fail=None):
    """pymupdf 的替身：``texts`` 页码 → 文字；``opened`` 记打开次数；``fail`` = "open" 打不开、页码集合 = 那几页抽不出。"""
    class Page:
        def __init__(self, number):
            self.number = number

        def get_text(self):
            if isinstance(fail, set) and self.number in fail:
                raise RuntimeError("cannot extract")
            return texts.get(self.number, "")

    class Document:
        page_count = max(texts or {0: ""})

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __getitem__(self, index):
            return Page(index + 1)

    def open_(stream=None, filetype=None):
        opened.append(filetype)
        if fail == "open":
            raise RuntimeError("cannot open")
        return Document()

    module = type(sys)("pymupdf")
    module.open = open_
    return module


class BrokenTextLayer(unittest.TestCase):
    """E11：pypdf 抽出的副本页 ToUnicode 坏了（满是 \\x03\\x04…），那一页改用 pymupdf 抽（`history.pdf_text.page_texts`，
    生产 `history.text_layer.pages` 与回放 `da-replay.py gate` 同一条）；正常的页不碰 pymupdf；pymupdf 不行时照旧，不报错。"""

    def setUp(self):
        from pearnly_cowork.history import pdf_text

        self.pdf_text = pdf_text

    def run_pages(self, pages, mupdf=None, numbers=None):
        from unittest import mock

        module, reader = _fake_pypdf(*pages)
        module.PdfReader = reader
        fallen = []
        modules = {"pypdf": module, "pymupdf": mupdf if mupdf is not None else None}
        with mock.patch.dict(sys.modules, modules):
            return self.pdf_text.page_texts(b"%PDF", numbers, fallen), fallen

    def test_the_criterion(self):
        share, broken = self.pdf_text.control_share, self.pdf_text.broken
        self.assertEqual(share(E11_RECEIPT), 0)
        self.assertEqual(share("a\tb\nc\r\fd\v"), 0)       # 制表、换行、回车、换页不算
        self.assertEqual(share(""), 0)
        self.assertEqual(share(None), 0)
        self.assertFalse(broken(E11_INVOICE, decode_error=True))  # 没有控制字符的页永远不算坏
        self.assertTrue(broken(GARBAGE))
        self.assertTrue(broken(",19\x10\x19\x1b\x14\x13 727$/\x03"))
        stray = E11_RECEIPT + "\x03"                              # 一页里个别没映射的字形（< 1%）
        self.assertLess(share(stray), self.pdf_text.BROKEN_SHARE)
        self.assertFalse(broken(stray))
        self.assertTrue(broken(stray, decode_error=True))         # 这份原件 pypdf 报过 ToUnicode 解码错误
        self.assertTrue(broken("x" * 9 + "\x03"))                 # 到 10% 就算
        self.assertFalse(broken("x" * 10 + "\x03"))

    def test_a_broken_page_falls_back_to_pymupdf_and_good_pages_stay(self):
        opened = []
        mupdf = _fake_mupdf({1: "pymupdf page 1", 2: E11_RECEIPT + "\x03", 3: "pymupdf page 3"}, opened)
        found, fallen = self.run_pages([_FakePage(E11_INVOICE), _FakePage(GARBAGE), _FakePage("y" * 300)], mupdf)
        self.assertEqual(found, [E11_INVOICE, E11_RECEIPT + "\x03", "y" * 300])
        self.assertEqual((fallen, opened), ([2], ["pdf"]))

    def test_good_pages_never_open_pymupdf(self):
        opened = []
        pages = [_FakePage(E11_INVOICE), _FakePage(E11_RECEIPT), _FakePage("", content=b"3 Tr BT (x) Tj ET")]
        found, fallen = self.run_pages(pages, _fake_mupdf({1: "other"}, opened))
        self.assertEqual(found, [E11_INVOICE, E11_RECEIPT, ""])   # 隐形识别文字层的页照旧给空串
        self.assertEqual((fallen, opened), ([], []))

    def test_pymupdf_failing_keeps_the_old_result_without_error(self):
        pages = [_FakePage(E11_INVOICE), _FakePage(GARBAGE)]
        for name, mupdf in (("missing", None), ("open", _fake_mupdf({2: E11_RECEIPT}, [], fail="open")),
                            ("page", _fake_mupdf({2: E11_RECEIPT}, [], fail={2})),
                            ("empty", _fake_mupdf({2: "  \n"}, [])),
                            ("no better", _fake_mupdf({2: GARBAGE}, []))):
            found, fallen = self.run_pages(pages, mupdf)
            self.assertEqual((found, fallen), ([E11_INVOICE, GARBAGE], []), name)

    def test_a_decode_error_counts_on_pages_with_any_control_character(self):
        stray = E11_RECEIPT + "\x03"
        mupdf = _fake_mupdf({1: "other 1", 2: E11_RECEIPT}, [])
        found, fallen = self.run_pages([_FakePage(E11_INVOICE, warn=True), _FakePage(stray)], mupdf)
        self.assertEqual((found, fallen), ([E11_INVOICE, E11_RECEIPT], [2]))
        found, fallen = self.run_pages([_FakePage(E11_INVOICE), _FakePage(stray)], mupdf)  # 没报错：个别控制字符不换
        self.assertEqual((found, fallen), ([E11_INVOICE, stray], []))

    def test_page_numbers_follow_the_request(self):
        mupdf = _fake_mupdf({1: "a", 2: "b", 3: E11_RECEIPT}, [])
        found, fallen = self.run_pages([_FakePage("one"), _FakePage("two"), _FakePage(GARBAGE)], mupdf, numbers=[3, 1, 9])
        self.assertEqual((found, fallen), ([E11_RECEIPT, "one"], [3]))

    def test_the_copy_page_read_by_pymupdf_still_has_to_match(self):
        """坏页从「读不到」变成「读得到」只是有了原文；单号、含税额、对方税号照旧要逐项对上，读出另一张的照旧算证据页。"""
        self.assertIn("evidence", auto.checks(_e11(E11_INVOICE, GARBAGE)))
        for text, evidence in ((E11_RECEIPT, False), (E11_RECEIPT.replace(E11_NUMBER, "INV2025080062"), True),
                               (E11_RECEIPT.replace("414.00", "400.00"), True), (E11_RECEIPT.replace(SELLER, ""), True)):
            found, _ = self.run_pages([_FakePage(E11_INVOICE), _FakePage(GARBAGE)], _fake_mupdf({2: text}, []))
            self.assertEqual("evidence" in auto.checks(_e11(*found)), evidence, text[-40:])

    def test_with_the_real_libraries(self):
        """真 pypdf / pymupdf（生产镜像里有；CI 没装时跳过）读造的坏 ToUnicode PDF。"""
        try:
            import pymupdf  # noqa: F401
            import pypdf
        except ImportError:
            self.skipTest("pypdf / pymupdf not installed")
        import io

        content = _cid_pdf((E11_INVOICE, False), (E11_RECEIPT, True))
        plain = [page.extract_text() for page in pypdf.PdfReader(io.BytesIO(content)).pages]
        self.assertEqual(plain[0], E11_INVOICE)
        self.assertTrue(plain[1].startswith("\x03\x04\x05"), repr(plain[1][:20]))  # 同 E11 现象
        self.assertTrue(self.pdf_text.broken(plain[1]))
        self.assertIn("evidence", auto.checks(_e11(*plain)))
        fallen = []
        found = self.pdf_text.page_texts(content, fallen=fallen)
        self.assertEqual(found[0], plain[0])                       # 好页逐字不变
        self.assertEqual(fallen, [2])
        self.assertEqual("".join(found[1].split()), "".join(E11_RECEIPT.split()))  # pymupdf 在泰文组合字前后另断空白
        self.assertNotIn("evidence", auto.checks(_e11(*found)))
        self.assertEqual(self.pdf_text.page_texts(_cid_pdf((E11_INVOICE, False), (E11_RECEIPT, False))),
                         [E11_INVOICE, E11_RECEIPT])               # 没坏的原件两页都是 pypdf 的


class Purity(unittest.TestCase):
    def test_no_framework_or_io(self):
        for name in ("automation.py", "decide.py"):
            tree = ast.parse((APP / "push/express" / name).read_text(encoding="utf-8"))
            imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
            imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            self.assertFalse({module for module in imported if module.split(".")[0] in ("frappe", "requests", "httpx")},
                             name)
            self.assertNotIn("pearnly_cowork.companies", " ".join(imported))

    def test_verdict_is_frozen(self):
        verdict = auto.classify(_facts())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            verdict.cls = "C"


if __name__ == "__main__":
    unittest.main()
