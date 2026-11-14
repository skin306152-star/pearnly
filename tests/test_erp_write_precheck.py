"""ERP 的写账前置判定（开始前确认卡片 W3，`express.precheck`；评估 docs/work/2026-11-04-precheck-card.md 7.2）：不连库的检查。

- 一次收齐：同期两份账套 + 服务类费用科目一遍列出（写入那条准备仍在第一道「选账套」就停，判定不改它、也不记任何值）；
- 收银三项：增值税登记户没有把握时照样往下判出收款科目，登记户答「是」才多出免税商品写法、预选「拆开写」；
- 每项的选项：预选排第一、标 ``recommended`` 带依据 ``note``；大列表只给预选那一个和搜索类型，搜索服务端分页、存货第一条是新建；
- 记住：答案按单据页同一套核对（不是这一项能选的值整批拒），记下后写入那条准备照记住的写，载荷与直接记住时逐字相同；
- 权限：别家公司、没有开单权、员工不是本人录入的单不回；记别人的单拒；待处理里要人选的行带判定单号。
"""
import copy
import sys
import types
import unittest
from unittest import mock

import test_erp_express_direct as base  # noqa: E402  共用替身与账套样本（同时装好 frappe 替身）

from pearnly_core.express_rules.push import choices as C  # noqa: E402
from pearnly_core.write_precheck import contracts as card  # noqa: E402
from pearnly_erp_portal.express import choices, contracts as E, facts, pos_day, prepare  # noqa: E402

fake, frappe = base.fake, base.frappe
COPY = r"C:\PearnlyExpress\70EXP\T9COPY"
SERVICE = [{"name": "ค่าขนส่ง", "code": "", "unit": "", "qty": "1", "price": "300", "amount": "300.00", "loccod": "",
            "stock": False}]


class Precheck(base.PushModule):
    def setUp(self):
        super().setUp()
        sys.modules.pop("pearnly_erp_portal.express.precheck", None)
        self.addCleanup(sys.modules.pop, "pearnly_erp_portal.express.precheck", None)
        from pearnly_erp_portal.express import precheck
        self.precheck = precheck
        fake.STATE.db.table("ERP Company")["CO-1"].update(company_name="นิภา", tax_id=base.OWN, branch="00012")
        self.row = {"name": "CO-1", "company_name": "นิภา", "tax_id": base.OWN, "branch": "00012"}
        for patch in (mock.patch.object(self.push, "_company", lambda name: dict(self.row)),
                      mock.patch.dict(sys.modules, {"pearnly_erp_portal.express.masters":
                                                    types.SimpleNamespace(ensure=lambda company, chosen: None)})):
            patch.start()
            self.addCleanup(patch.stop)

    def two_books(self):
        first, second = self.computer([base.book(), base.book(path=COPY, uid="vsn:1/9")])
        for key in (first, second):
            self.masters(key)
        return first, second

    def write_prepare(self, body):
        """写入那条准备（`push.send` 调的），不经判定。"""
        return prepare.prepare(copy.deepcopy(body), self.row, lambda company, chosen: None)

    def collect(self, body, given=None):
        return self.precheck.collect(self.push.PURCHASE, "CO-1", lambda: copy.deepcopy(body), given)


class Collect(Precheck):
    def test_two_books_and_the_expense_account_come_in_one_pass_without_remembering(self):
        first, _second = self.two_books()
        body = base.doc(items=SERVICE)
        held = self.write_prepare(body)
        self.assertEqual(held["hold"], "choice_needed:account_set")  # 写入那条：仍在第一道停
        found = self.collect(body)
        self.assertEqual([item["kind"] for item in found["items"]], ["account_set", "account"])
        book, account = found["items"]
        # 账套：预选这一天有电脑能写的那份，排第一、标推荐、带依据
        self.assertEqual((book["value"], book["source"]), (first, card.RULE))
        self.assertEqual(book["options"][0]["code"], first)
        self.assertTrue(book["options"][0]["note"])
        self.assertNotIn("note", book["options"][1])  # 「推荐」由共用层按预选值标（W2 `contracts.normalize_item`）
        # 费用科目：没有把握，未定；科目表 10 条直接给
        self.assertEqual(account["value"], "")
        self.assertEqual(len(account["options"]), len(base.ACCOUNTS))
        self.assertNotIn("search", account)
        # 判定什么都不记，写入那条结论不变
        self.assertEqual(choices.remembered("CO-1"), {})
        self.assertEqual(self.write_prepare(body), held)
        merged = card.merge([{"doc_id": "P:PI-1", "items": found["items"]}])
        self.assertEqual(merged["counts"], {"open": 1, "proposed": 1, "decided": 0})
        self.assertEqual(card.held_back(merged), ["P:PI-1"])

    def test_answers_are_remembered_and_the_payload_is_the_same_as_remembering_directly(self):
        first, second = self.two_books()
        body = base.doc(items=SERVICE)
        found = self.collect(body)
        answers = [{"kind": item["kind"], "subject": item["subject"],
                    "value": second if item["kind"] == "account_set" else "52-08-00-00"} for item in found["items"]]
        with self.assertRaises(frappe.ValidationError):  # 不是这一项能选的值：整批拒、什么都不记
            self.precheck.save("CO-1", self.collect(body, self.precheck.given_of(answers)),
                               [answers[0], {**answers[1], "value": "99-99-99-99"}])
        self.assertEqual(choices.remembered("CO-1"), {})
        saved = self.precheck.save("CO-1", self.collect(body, self.precheck.given_of(answers)), answers)
        self.assertEqual(saved, 2)
        via_card = self.write_prepare(body)
        self.assertEqual(via_card["book"]["key"], second)
        self.assertEqual(self.collect(body)["items"], [])  # 记住的不再出题
        # 同样两项直接按单据页「选一次」的记法记：载荷逐字相同
        fake.STATE.db.table(choices.CHOICE).clear()
        for answer in answers:
            choices.remember("CO-1", answer["kind"], answer["subject"], answer["value"])
        self.assertEqual(self.write_prepare(body)["payload"], via_card["payload"])
        self.assertIn("52-08-00-00", [line["acc"] for line in via_card["payload"]["lines"] if line["side"] == "D"])

    def test_a_record_already_remembered_is_not_asked_and_a_ready_document_has_no_items(self):
        key, = self.computer([base.book()])
        self.masters(key)
        self.assertEqual(self.collect(base.doc())["items"], [])
        self.assertIn("payload", self.collect(base.doc())["result"])

    def test_party_with_several_records_recommends_the_same_branch(self):
        key, = self.computer([base.book()])
        suppliers = [{"code": "B004", "name": "BANGCHAK", "tax_id": base.SUPPLIER, "branch": "00000"},
                     {"code": "B007", "name": "BANGCHAK สาขา 7", "tax_id": base.SUPPLIER, "branch": "00007"}]
        self.masters(key, suppliers=suppliers)
        body = base.doc(party={"code": "", "name": "BANGCHAK", "tax_id": base.SUPPLIER, "branch": "00007"})
        party, = [item for item in self.collect(body)["items"] if item["kind"] == "party"]
        self.assertEqual(party["value"], "B007")
        self.assertEqual([row["code"] for row in party["options"]][:1], ["B007"])
        self.assertTrue(party["options"][0]["note"])
        self.assertIn(C.NEW, [row["code"] for row in party["options"]])

    def test_a_big_list_gives_only_the_recommended_option_and_a_search_kind(self):
        key, = self.computer([base.book()])
        products = [{"code": f"P{index:03d}", "name": f"สินค้า {index}", "kind": "stock"} for index in range(40)]
        self.masters(key, products=products)
        lines = [{"name": "ของใหม่", "code": "", "unit": "ชิ้น", "qty": "1", "price": "300", "amount": "300.00",
                  "loccod": "02", "stock": True}]
        item, = [entry for entry in self.collect(base.doc(items=lines))["items"] if entry["kind"] == "item"]
        self.assertEqual((item["search"], item["value"]), ("products", C.NEW))
        self.assertNotIn("options", item)  # 大列表不给选项，只给预选值与搜索类型
        normalized = card.normalize_item(item)
        self.assertEqual((normalized["state"], normalized["value"]), (card.PROPOSED, C.NEW))

    def test_search_pages_on_the_server_by_the_documents_book_and_new_comes_first(self):
        key, = self.computer([base.book()])
        products = [{"code": f"P{index:03d}", "name": f"สินค้า {index}", "kind": "stock"} for index in range(40)]
        self.masters(key, products=products)
        fake.STATE.db.table(self.push.PURCHASE)["PI-1"] = {"name": "PI-1", "company": "CO-1", "owner": "staff@x",
                                                           "doc_date": "2026-10-20", "ledger_book": None}
        with mock.patch.object(self.precheck.guard, "has", lambda capability, user=None: True), \
                mock.patch.object(self.precheck.guard, "scope_filters", lambda user=None: {}), \
                mock.patch.object(self.precheck.companies, "allowed_companies", lambda user=None: ["CO-1"]):
            first = self.precheck.search("CO-1", "P:PI-1", "products", "", 0, 10)
            self.assertEqual([row["code"] for row in first["options"]][:2], [C.NEW, "P000"])
            self.assertTrue(first["more"])
            found = self.precheck.search("CO-1", "P:PI-1", "products", "สินค้า 3", 0, 50)
            self.assertEqual([row["code"] for row in found["options"]], ["P003", *[f"P{n:03d}" for n in range(30, 40)]])
            self.assertFalse(found["more"])
            parties = self.precheck.search("CO-1", "P:PI-1", "parties", "B00", 0, 10)
            self.assertEqual([row["code"] for row in parties["options"]], ["B004", "B005"])
            accounts = self.precheck.search("CO-1", "P:PI-1", "accounts", "", 0, 50)["options"]
            self.assertEqual(accounts[0]["code"][:1], "5")  # 进项：费用类在前
            with self.assertRaises(frappe.ValidationError):
                self.precheck.search("CO-1", "P:PI-1", "departments", "", 0, 10)
            with self.assertRaises(frappe.PermissionError):  # 别家公司的单
                self.precheck.search("CO-1", "P:PI-9", "products", "", 0, 10)
        with mock.patch.object(self.precheck.companies, "allowed_companies", lambda user=None: ["CO-2"]), \
                self.assertRaises(frappe.PermissionError):
            self.precheck.search("CO-1", "P:PI-1", "products", "", 0, 10)


class PosDay(Precheck):
    """收银日汇总：写入那条准备先停「是不是增值税登记户」，再停「免税商品怎么写」，最后才到收款科目。判定一遍列出。"""

    def pos_body(self):
        sales = base.doc(direction="sales", paid=True, party={"code": "C01", "name": "ร้านป้าแดง", "tax_id": "", "branch": ""},
                         pos={"first": "R0001", "last": "R0009", "count": 9, "payments": [["โอน", "100.00"]]})

        def body():
            registered = pos_day._registered("CO-1")
            if not registered:
                return {"ref": "x", "hold": f"{E.CHOICE}:{pos_day.VAT_REGISTERED}",
                        "needed": [{"kind": pos_day.VAT_REGISTERED, "subject": "company", "label": "",
                                    "options": [{"code": "yes", "name": ""}, {"code": "no", "name": ""}]}]}
            if registered == "yes" and not choices.chosen("CO-1", pos_day.EXEMPT_MODE, "company"):
                return {"ref": "x", "hold": f"{E.CHOICE}:{pos_day.EXEMPT_MODE}",
                        "needed": [{"kind": pos_day.EXEMPT_MODE, "subject": "company", "label": "",
                                    "options": [{"code": pos_day.EXEMPT_SPLIT, "name": ""},
                                                {"code": pos_day.EXEMPT_WHOLE, "name": ""}]}]}
            return copy.deepcopy(sales)
        return body

    def test_the_three_cashier_items_come_in_one_pass(self):
        key, = self.computer([base.book()])
        self.masters(key)
        with mock.patch.object(pos_day.companies, "vat_registered", lambda company, lookup=False: ""):
            found = self.precheck.collect(self.push.POS_DAY, "CO-1", self.pos_body())
            self.assertEqual([item["kind"] for item in found["items"]], [pos_day.VAT_REGISTERED, "receipt"])
            registered = found["items"][0]
            self.assertEqual(registered["value"], "")  # 没卖过带税商品：不预选，按「不是」往下判出收款科目
            answered = self.precheck.collect(self.push.POS_DAY, "CO-1", self.pos_body(),
                                             {pos_day.VAT_REGISTERED: {"company": "yes"}})
            kinds = [item["kind"] for item in answered["items"]]
            self.assertEqual(kinds, [pos_day.VAT_REGISTERED, pos_day.EXEMPT_MODE, "receipt"])
            exempt = answered["items"][1]
            self.assertEqual((exempt["value"], exempt["options"][0]["code"]), (pos_day.EXEMPT_SPLIT, pos_day.EXEMPT_SPLIT))
            # 判定之外：登记户照旧不知道，写入那条仍先停在这一问
            self.assertEqual(pos_day._registered("CO-1"), "")

    def test_the_registered_answer_is_kept_on_the_company(self):
        key, = self.computer([base.book()])
        self.masters(key)
        kept = []
        with mock.patch.object(pos_day.companies, "vat_registered", lambda company, lookup=False: ""), \
                mock.patch.object(self.precheck.companies, "set_vat_registered", lambda company, value: kept.append(value)):
            found = self.precheck.collect(self.push.POS_DAY, "CO-1", self.pos_body(),
                                          {pos_day.VAT_REGISTERED: {"company": "no"}})
            self.precheck.save("CO-1", found, [{"kind": pos_day.VAT_REGISTERED, "subject": "company", "value": "no"}])
        self.assertEqual(kept, ["no"])
        self.assertEqual(choices.remembered("CO-1"), {})


class Access(Precheck):
    def rows(self):
        table = fake.STATE.db.table(self.push.PURCHASE)
        table["PI-1"] = {"name": "PI-1", "company": "CO-1", "owner": "staff@x", "system_no": "PE-1", "supplier_name": "S"}
        table["PI-2"] = {"name": "PI-2", "company": "CO-2", "owner": "staff@x"}
        table["PI-3"] = {"name": "PI-3", "company": "CO-1", "owner": "other@x"}

    def test_only_the_users_own_writable_documents_are_judged(self):
        self.rows()
        seen = []
        with mock.patch.object(self.precheck.guard, "has", lambda capability, user=None: capability == "purchase.write"), \
                mock.patch.object(self.precheck.guard, "scope_filters", lambda user=None: {"owner": "staff@x"}), \
                mock.patch.object(self.precheck.companies, "allowed_companies", lambda user=None: ["CO-1"]), \
                mock.patch.object(self.precheck, "document_view",
                                  lambda doc_id, doctype, name, found=None: seen.append(doc_id) or {"doc_id": doc_id}):
            out = self.precheck.check("CO-1", ["P:PI-1", "P:PI-2", "P:PI-3", "S:SI-1", "X:1"])
            self.assertEqual(out["company_title"], "นิภา")
            self.assertEqual(seen, ["P:PI-1"])  # 别家公司、别人录入的、没有开单权的、认不得的单号：不回
            with self.assertRaises(frappe.PermissionError):
                self.precheck.remember("CO-1", {"P:PI-3": [{"kind": "account", "subject": "x", "value": "1"}]})
        with mock.patch.object(self.precheck.companies, "allowed_companies", lambda user=None: ["CO-2"]):
            self.assertEqual(self.precheck.check("CO-1", ["P:PI-1"]), {"company_title": "", "documents": []})

    def test_the_guard_needs_the_company_and_any_ledger_write_capability(self):
        with mock.patch.object(self.precheck.companies, "allowed_companies", lambda user=None: ["CO-1"]):
            with mock.patch.object(self.precheck.guard, "has", lambda capability, user=None: capability == "cashier.write"):
                self.assertTrue(self.precheck.allowed("staff@x", "CO-1"))
                self.assertFalse(self.precheck.allowed("staff@x", "CO-2"))
                self.assertFalse(self.precheck.allowed("Guest", "CO-1"))
            with mock.patch.object(self.precheck.guard, "has", lambda capability, user=None: capability == "purchase.read"):
                self.assertFalse(self.precheck.allowed("staff@x", "CO-1"))

    def test_collections_go_to_the_collection_plan_and_collectors_may_plan(self):
        # 收款第 4 步：CC 里录的收款（R:<记录>@<版本>）进写入计划，由 `collection.plan` 判；只有收款岗位的人也能建计划
        from pearnly_erp_portal.collection import plan

        self.rows()
        seen = []
        with mock.patch.object(self.precheck.guard, "has", lambda capability, user=None: True), \
                mock.patch.object(self.precheck.guard, "scope_filters", lambda user=None: {}), \
                mock.patch.object(self.precheck.companies, "allowed_companies", lambda user=None: ["CO-1"]), \
                mock.patch.object(plan, "check", lambda company, ids: seen.append((company, ids)) or {
                    "documents": [{"doc_id": ids[0], "state": "check", "items": []}]}), \
                mock.patch.object(self.precheck, "document_view",
                                  lambda doc_id, doctype, name, found=None: {"doc_id": doc_id}):
            out = self.precheck.check("CO-1", ["R:COL261111-AB12@2", "P:PI-1"])
        self.assertEqual(seen, [("CO-1", ["R:COL261111-AB12@2"])])
        self.assertEqual([doc["doc_id"] for doc in out["documents"]], ["R:COL261111-AB12@2", "P:PI-1"])
        with mock.patch.object(self.precheck.companies, "allowed_companies", lambda user=None: ["CO-1"]), \
                mock.patch.object(self.precheck.guard, "has", lambda capability, user=None: capability == "collection.write"):
            self.assertTrue(self.precheck.allowed("collector@x", "CO-1"))
            self.assertFalse(self.precheck.allowed("collector@x", "CO-2"))

    def test_a_disconnected_company_writes_nothing(self):
        self.rows()
        fake.STATE.db.table("ERP Company")["CO-1"]["express_enabled"] = 0
        with mock.patch.object(self.precheck.guard, "has", lambda capability, user=None: True), \
                mock.patch.object(self.precheck.guard, "scope_filters", lambda user=None: {}), \
                mock.patch.object(self.precheck.companies, "allowed_companies", lambda user=None: ["CO-1"]):
            out = self.precheck.check("CO-1", ["P:PI-1"])
        self.assertEqual(out["documents"], [{"doc_id": "P:PI-1", "state": card.NOT_WRITABLE, "reason": E.DISCONNECTED}])

    def test_document_states_for_written_queued_and_unknown(self):
        self.rows()
        docs = fake.STATE.db.table(self.push.PURCHASE)
        jobs_by_name = {"J1": {"status": "manual", "reason": self.precheck.jobs.LOST},
                        "J2": {"status": "queued", "reason": ""}}
        with mock.patch.object(self.precheck.jobs, "find_by_name", lambda name: jobs_by_name.get(name)):
            docs["PI-1"].update(ledger_state=E.WRITTEN, ledger_job=None)
            self.assertEqual(self.precheck.document_view("P:PI-1", self.push.PURCHASE, "PI-1")["reason"],
                             self.precheck.WRITTEN)
            docs["PI-1"].update(ledger_state=E.HELD, ledger_job="J1")
            self.assertEqual(self.precheck.document_view("P:PI-1", self.push.PURCHASE, "PI-1")["state"], card.UNKNOWN)
            docs["PI-1"].update(ledger_job="J2")
            view = self.precheck.document_view("P:PI-1", self.push.PURCHASE, "PI-1")
            self.assertEqual((view["state"], view["reason"], view["ref"], view["party"]),
                             (card.NOT_WRITABLE, self.precheck.ALREADY_QUEUED, "PE-1", "S"))

    def test_resume_sends_again_only_documents_held_on_a_choice(self):
        self.rows()
        docs = fake.STATE.db.table(self.push.PURCHASE)
        docs["PI-1"].update(ledger_state=E.HELD, ledger_reason="choice_needed:account", ledger_job=None)
        docs["PI-4"] = {"name": "PI-4", "company": "CO-1", "owner": "staff@x", "ledger_state": E.HELD,
                        "ledger_reason": E.NO_WRITER, "ledger_job": None}
        docs["PI-5"] = {"name": "PI-5", "company": "CO-1", "owner": "staff@x", "ledger_state": E.HELD,
                        "ledger_reason": "choice_needed:party", "ledger_job": "J5"}
        sent = []
        with mock.patch.object(self.precheck.guard, "has", lambda capability, user=None: True), \
                mock.patch.object(self.precheck.guard, "scope_filters", lambda user=None: {}), \
                mock.patch.object(self.precheck.companies, "allowed_companies", lambda user=None: ["CO-1"]), \
                mock.patch.object(self.push, "operator_owned", lambda doctype, name: False), \
                mock.patch.object(self.push, "_set", lambda doctype, name, values: docs[name].update(values)), \
                mock.patch.object(frappe.db, "commit", lambda: None, create=True), \
                mock.patch.object(self.push, "send", lambda doctype, name, operator=None: sent.append(name)
                                  or {"status": "sent"}):
            out = self.precheck.resume("CO-1", ["P:PI-1", "P:PI-2", "P:PI-4", "P:PI-5"])
        self.assertEqual(out, [{"doc_id": "P:PI-1", "status": "sent", "reason": ""}])
        self.assertEqual(sent, ["PI-1"])  # 别家公司的、停在别的原因的、已交写账件的不动
        self.assertEqual((docs["PI-1"]["ledger_reason"], docs["PI-1"]["ledger_needed"]), ("", None))

    def test_a_pending_row_that_waits_for_a_choice_carries_its_precheck_id(self):
        from pearnly_erp_portal.express import pending

        row = {"name": "PI-1", "company": "CO-1", "doctype": self.push.PURCHASE, "ledger_state": E.HELD,
               "ledger_reason": "choice_needed:account", "number": "PE-1", "party": "S", "total": None,
               "date": "2026-10-20", "direction": ""}
        self.assertEqual(pending._item(row)["doc_id"], "P:PI-1")
        self.assertEqual(pending._item(row)["action"], "open")
        self.assertNotIn("doc_id", pending._item({**row, "ledger_reason": E.NO_WRITER}))
        self.assertEqual(self.precheck.doc_id_of(self.push.POS_DAY, "D1"), "D:D1")


def mrerp_views(menus=("purchase",), defaults=("supplier_type",)):
    """MR.ERP 菜单 / 主档视图（`mrerp.choices.views` 的形状）：``menus`` / ``defaults`` 里的要人选，其余已选好。"""
    kinds = {kind: {"options": [{"idmenu": "68", "label": "ซื้อ"}, {"idmenu": "454", "label": "ซื้อ2"}],
                    "needed": kind in menus} for kind in ("purchase", "purchase_expense", "sales_credit", "sales_cash")}
    fields = {field: {"options": [{"code": f"{field[:3]}001", "name": "A"}, {"code": f"{field[:3]}002", "name": "B"}],
                      "needed": field in defaults}
              for field in ("supplier_type", "ap_account", "customer_type", "ar_account", "bank_account")}
    return {"known": True, "kinds": kinds}, {"known": True, "fields": fields}


class MrErp(Precheck):
    """ERP 公司选了 MR.ERP 账套（主会话 11-05：旧工具 erp_push_choices / erp_save_push_choices 删掉后，MR.ERP 的导入菜单与主档默认值
    也经共用卡片问）：总开关开着才出这两类题；答了记在公司上（`mrerp.choices.choose`），停在要选的单记下后排一次导入。"""

    def setUp(self):
        super().setUp()
        fake.STATE.db.table("ERP Company")["CO-1"].update(mrerp_enabled=1, express_enabled=0, mrerp_connection="MR1",
                                                          mrerp_account_set="12:3")
        docs = fake.STATE.db.table(self.push.PURCHASE)
        docs["PI-1"] = {"name": "PI-1", "company": "CO-1", "owner": "staff@x", "system_no": "PE-1", "supplier_name": "S",
                        "ledger_state": E.HELD, "ledger_reason": self.precheck.mrerp_contracts.CHOICE, "ledger_job": None}
        self.chosen, self.sent, self.viewed = [], [], []
        for patch in (
                mock.patch.object(self.precheck.guard, "has", lambda capability, user=None: True),
                mock.patch.object(self.precheck.guard, "scope_filters", lambda user=None: {}),
                mock.patch.object(self.precheck.companies, "allowed_companies", lambda user=None: ["CO-1"]),
                mock.patch.object(self.push, "operator_owned", lambda doctype, name: False),
                mock.patch.object(self.precheck.mrerp_choices, "views",
                                  lambda row: self.viewed.append(row.mrerp_account_set) or mrerp_views()),
                mock.patch.object(self.precheck.mrerp_choices, "choose",
                                  lambda company, kind, subject, value: self.chosen.append((company, kind, subject, value))
                                  or {"picked": value}),
                mock.patch.object(self.precheck.mrerp_push, "send",
                                  lambda doctype, name: self.sent.append(name) or {"status": "sent"})):
            patch.start()
            self.addCleanup(patch.stop)

    def switch(self, on):
        # 打在判定实际用的那个模块上（别的测试可能重载过 `pearnly_erp_portal.mrerp`）
        patch = mock.patch.object(self.precheck.mrerp_push.mrerp, "MRERP_LEDGER_ENABLED", on)
        patch.start()
        self.addCleanup(patch.stop)

    def test_menus_and_defaults_are_asked_answered_remembered_and_the_import_queued(self):
        self.switch(True)
        out = self.precheck.check("CO-1", ["P:PI-1"])
        found, = out["documents"]
        self.assertEqual(found["state"], card.CHECK)
        self.assertEqual([(item["kind"], item["subject"], [o["code"] for o in item["options"]]) for item in found["items"]],
                         [("mrerp_menu", "purchase", ["68", "454"]), ("mrerp_default", "supplier_type", ["sup001", "sup002"])])
        merged = card.merge(out["documents"])
        self.assertTrue(card.needs_card(merged))
        self.assertEqual([group["group"] for group in merged["groups"]], ["book"])
        # 选的值不在读到的候选里：整批不记
        with self.assertRaises(Exception):
            self.precheck.remember("CO-1", {"P:PI-1": [{"kind": "mrerp_menu", "subject": "purchase", "value": "999"}]})
        self.assertEqual(self.chosen, [])
        answers = [{"kind": "mrerp_menu", "subject": "purchase", "value": "454", "label": "ซื้อ2", "remember": True},
                   {"kind": "mrerp_default", "subject": "supplier_type", "value": "sup002", "label": "B", "remember": True}]
        self.assertEqual(self.precheck.remember("CO-1", {"P:PI-1": answers}), {"saved": 2})
        self.assertEqual(self.chosen, [("CO-1", "mrerp_menu", "purchase", "454"),
                                       ("CO-1", "mrerp_default", "supplier_type", "sup002")])
        # 停在要选导入菜单的单：记下后排一次导入（同单据上「选一次」）；别的原因停下的不动
        fake.STATE.db.table(self.push.PURCHASE)["PI-2"] = {
            "name": "PI-2", "company": "CO-1", "owner": "staff@x", "ledger_state": E.HELD,
            "ledger_reason": self.precheck.mrerp_contracts.NOT_CONFIGURED, "ledger_job": None}
        self.assertEqual(self.precheck.resume("CO-1", ["P:PI-1", "P:PI-2"]),
                         [{"doc_id": "P:PI-1", "status": "sent", "reason": ""}])
        self.assertEqual(self.sent, ["PI-1"])

    def test_the_switch_off_asks_nothing_about_mrerp(self):
        self.switch(False)
        out = self.precheck.check("CO-1", ["P:PI-1"])
        self.assertEqual(out["documents"], [{"doc_id": "P:PI-1", "state": card.NOT_WRITABLE, "reason": E.DISCONNECTED}])
        self.assertEqual(self.viewed, [])  # 关着时不读 MR.ERP 的菜单
        self.assertEqual(self.precheck.resume("CO-1", ["P:PI-1"]), [])
        self.assertEqual(self.sent, [])

    def test_imported_queued_unknown_and_unsupported_documents(self):
        self.switch(True)
        docs = fake.STATE.db.table(self.push.PURCHASE)
        docs["PI-1"].update(ledger_state=E.WRITTEN)
        self.assertEqual(self.precheck.mrerp_view("P:PI-1", self.push.PURCHASE, "PI-1")["reason"], self.precheck.WRITTEN)
        docs["PI-1"].update(ledger_state=E.QUEUED, ledger_reason="")
        self.assertEqual(self.precheck.mrerp_view("P:PI-1", self.push.PURCHASE, "PI-1")["reason"],
                         self.precheck.ALREADY_QUEUED)
        docs["PI-1"].update(ledger_state=E.HELD, ledger_reason=self.precheck.mrerp_contracts.UNKNOWN)
        self.assertEqual(self.precheck.mrerp_view("P:PI-1", self.push.PURCHASE, "PI-1")["state"], card.UNKNOWN)
        # MR.ERP 不导的单据（收银日汇总、出入库、盘点）写不了，不问菜单
        self.assertIsNone(self.precheck.mrerp_needed("CO-1", self.push.POS_DAY))


class Declared(unittest.TestCase):
    def test_the_product_declares_the_precheck_hook(self):
        source = (base.ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/hooks.py").read_text(encoding="utf-8")
        for key in ("allowed", "check", "remember", "search", "resume"):
            self.assertIn(f'"{key}": "pearnly_erp_portal.express.precheck.{key}"', source)


if __name__ == "__main__":
    unittest.main()
