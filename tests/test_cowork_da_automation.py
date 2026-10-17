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
from pearnly_cowork.push.express import choices as choice_rules  # noqa: E402
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
                account_recent=["5310-01", "5310-01", "5310-01"])
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

    def test_a_remembered_account_needs_three_in_a_row(self):
        """账套记忆按供应商记一个科目：这家在这份账套里最近连续至少 3 张同科目才算 A，否则 B（第一轮回放后定，10-16）。"""
        def tier(recent):
            return self.tier("account", payload=_payload(account_source="remembered"), account_recent=recent)
        self.assertEqual(tier(["5310-01"] * 3), "A")
        self.assertEqual(tier(["5310-01"] * 3 + ["5320-01"]), "A")      # 更早的不同不影响
        self.assertEqual(tier(["5310-01", "5310-01"]), "B")             # 还不够 3 张
        self.assertEqual(tier(["5310-01", "5320-01", "5310-01", "5310-01"]), "B")  # 中间断了
        self.assertEqual(tier(["5320-01"] * 3), "B")                     # 最近都记别的科目
        self.assertEqual(tier(["*multi", "5310-01", "5310-01", "5310-01"]), "B")   # 一单多科目也断开
        self.assertEqual(tier([]), "B")
        self.assertEqual(tier(None), "B")                                # 没取（旧调用方）按 0 张
        # 只管账套记忆：任务默认、本票选的、公司规则不看连续张数
        for source in ("task_default", "chosen", "company_rule"):
            self.assertEqual(self.tier("account", payload=_payload(account_source=source), account_recent=[]), "A",
                             source)
        self.assertEqual(auto.MEMORY_STREAK, 3)
        self.assertEqual(auto.memory_streak(["a", "a", "b", "a"], "a"), 2)
        self.assertEqual(auto.memory_streak(["a"], ""), 0)
        self.assertTrue(auto.memory_settled(["a", "a", "a"], "a"))

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
