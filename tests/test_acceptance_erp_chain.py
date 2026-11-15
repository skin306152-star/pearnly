"""收款链路验收场景（scripts/acceptance/scenarios/erp_company_connect.py、erp_collection_express.py）整条走一遍，站点与账套都换成
替身：只验场景自己的判定——该过的过、今天修掉的那几类缺陷重现时判不通过、跑完恢复基线。真环境没跑过。"""
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
NBSP = "\xa0"


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class World:
    """替身：站点脚本回的结果 + 一本「账套」（按表存行）。场景只通过 site.run / ledger 的几个调用碰它。"""

    def __init__(self, break_at=""):
        self.break_at, self.tables, self.calls, self.restored = break_at, {}, [], 0
        self.customer, self.invoice, self.total, self.received, self.count = "C001", "IV691115-001", 1070.0, 0.0, 0

    # ── 账套
    def add(self, table, **row):
        self.tables.setdefault(table, []).append(row)

    def book(self):
        world = self

        class Book:
            def probe(self):
                return {"ok": True, "busy": []}

            def verify(self, snap):
                world.calls.append("verify")
                return {"ok": not world.tables}

            def restore(self, snap):
                world.calls.append("restore")
                world.tables.clear()
                return {"ok": True}

            def copy(self, target):
                return {"ok": True}

            def diff(self, before, after, allow=None, out=None):
                stray = ["STMAS.DBF"] if world.break_at == "stray" else []
                if allow == [] and world.break_at == "cancel-wrote":
                    stray = ["APTRN.DBF"]
                return {"ok": True, "stray": stray, "tables": {name: [0, len(rows)] for name, rows in world.tables.items()}}

            def call(self, command, folder, table, flag, where):
                key, _, value = where.partition("=")
                return {"ok": True, "rows": [row for row in world.tables.get(table, []) if str(row.get(key, "")).strip() == value.strip()]}

        return Book()

    # ── 站点
    def run(self, product, script, site, raw, timeout=600):
        params = json.loads(raw)
        self.calls.append(script.name)
        return "noise\nACC " + json.dumps(getattr(self, script.stem)(params), ensure_ascii=False)

    def erp_sales_post(self, params):
        new = bool(params.get("buyer"))
        self.customer = "C001" if new else f"นาย{NBSP}ทดสอบ"
        self.add("ARTRN", DOCNUM=self.invoice, CUSCOD=self.customer, NETAMT=self.total, RCVAMT=0.0, REMAMT=self.total, CMPLAPP="")
        self.add("ISVAT", DOCNUM=self.invoice)
        self.add("GLJNL", VOUCHER=self.invoice)
        if new:
            self.add("ARMAS", CUSCOD=self.customer, PRENAM="" if self.break_at == "prename" else "ร้าน",
                     CUSNAM=params["buyer"][len("ร้าน"):].strip())
        paid = self.break_at == "paid"
        return {"ok": True, "company": "CO-1", "doc": "SI-1", "book_paths": [params["ledger"]],
                "state": {"ledger_state": "written", "ledger_docnum": self.invoice}, "job": {"verify_state": "ok"},
                "party": {"express_code": self.customer, "express_company": "CO-1"},
                "sale": {"payment_status": "paid" if paid else "unpaid", "paid_amount": self.total if paid else 0,
                         "payment_method": "cash" if paid else None},
                "expected": {"customer_code": self.customer, "new_customer": new, "buyer": params.get("buyer") or "",
                             "prename": "ร้าน" if new else "", "core_name": (params.get("buyer") or "")[len("ร้าน"):].strip(),
                             "totals": {"subtotal": 1000.0, "vat_amount": 70.0, "grand_total": self.total}}}

    def _receipt(self, name, cash, transfer, source):
        self.count += 1
        docnum, amount = f"RE000000{self.count}", cash + transfer
        self.received += amount
        written = self.customer.replace(NBSP, " ") if self.break_at == "nbsp" else self.customer
        self.add("ARTRN", DOCNUM=docnum, CUSCOD=written, YOUREF=name, NETAMT=amount, CSHRCV=cash, CHQRCV=transfer)
        self.add("ARRCPIT", RCPNUM=docnum, DOCNUM=self.invoice, RCVAMT=amount)
        self.add("GLJNL", VOUCHER=docnum)
        methods = []
        if transfer:
            self.add("ARRCPCQ", RCPNUM=docnum, CHQNUM="TR" + docnum, RCVAMT=transfer)
            self.add("BKTRN", VOUCHER=docnum, AMOUNT=transfer)
            methods = [{"prefix": "TR", "role": "transfer", "amount": f"{transfer:.2f}"}]
        head = self.tables["ARTRN"][0]
        head.update(RCVAMT=self.received, REMAMT=self.total - self.received, CMPLAPP="Y" if self.received == self.total else "")
        return {"name": name, "status": "written", "docnum": docnum, "source": source, "customer": self.customer, "cash": f"{cash:.2f}",
                "total": f"{amount:.2f}", "methods": methods, "invoices": [{"docnum": self.invoice, "amount": f"{amount:.2f}"}],
                "balances": {self.invoice: f"{self.total - self.received:.2f}"}, "job": {"verify_state": "ok"},
                "book_paths": [r"\\accserver\d$\ACCOUNT\69EXP\TEST2"]}

    def erp_collection_post(self, params):
        spoken = params["customer"].replace(NBSP, " ") if params.get("spoken") else params["customer"]
        out = {"ok": True, "company": "CO-1", "entry": params["entry"], "methods": {"cash": True, "transfers": ["TR"], "discount": False},
               "customer": {"given": params["customer"], "spoken": spoken, "canonical": params["customer"]},
               "reads": {"count": 2, "first": {"refresh": "reading", "listed": False}, "last": {"refresh": "ready", "listed": True},
                         "ready_without_invoice": 1 if self.break_at == "stale-read" else 0}}
        left = self.total - self.received
        cash, transfer = (left, 0.0) if params.get("full") else (float(params.get("cash") or 0), float(params.get("transfer") or 0))
        variant = params.get("variant") or ""
        if variant == "refused":
            out["first"] = self._receipt("COL-WEB", left, 0.0, "web")
            if self.break_at == "double":
                self._receipt("COL-LINE", 100.0, 0.0, "line")
            out["refused"] = {"status": "invalid", "code": "collection.invoice_not_found", "draft_status": "draft"}
            out["record"] = {"name": "COL-LINE", "status": "withdrawn", "docnum": ""}
            return out
        if variant == "edit":
            cash = float(params["edit_cash"])
            out["edit"] = {"version_before": 1, "version_after": 2, "stale_release": "released" if self.break_at == "stale-card" else "changed",
                           "stale_version": 2, "status_after_stale": "draft"}
        if variant == "twice":
            out["twice"] = {"same_record": True, "records_for_request": 2 if self.break_at == "twice" else 1}
        out["record"] = self._receipt(f"COL-{self.count + 1}", cash, transfer, params["entry"])
        return out

    def erp_purchase_post(self, params):
        self.add("APTRN", DOCNUM="HP691115-001")
        return {"ok": True, "company": "CO-1", "doc": "PE-1", "book_paths": [params["ledger"]], "job": {"verify_state": "ok"},
                "state": {"ledger_state": "written", "ledger_docnum": "HP691115-001"}, "expected": {}}

    def erp_doc_cancel(self, params):
        jobs = [{"name": "J-1", "status": "success"}]
        return {"ok": True, "cancelled": {"name": params["doc"], "status": "void"}, "before": {"ledger_state": "written"},
                "after": {"ledger_state": "written", "ledger_docnum": "HP691115-001" if params["kind"] == "purchase" else self.invoice},
                "jobs_before": jobs, "jobs_after": jobs + ([{"name": "J-2", "status": "queued"}] if self.break_at == "cancel-job" else [])}

    def erp_pos_day(self, params):
        docnum, total = "HS691115-001", 100.0
        self.add("ARTRN", DOCNUM=docnum, NETAMT=total, REMAMT=0.0)
        for _index in range(1 if self.break_at == "pos-short" else 2):
            self.add("STCRD", DOCNUM=docnum, STKCOD="P001", TRNQTY=-1.0)
        return {"ok": True, "company": "CO-1", "doc": "PD-1", "again": "PD-2" if self.break_at == "pos-twice" else None,
                "book_paths": [params["ledger"]], "job": {"verify_state": "ok"}, "receipts": ["S-1", "S-2"],
                "state": {"ledger_state": "written", "ledger_docnum": docnum, "doc_kind": "HS", "sales_count": 2, "grand_total": total},
                "expected": {"item_code": "P001", "receipts": 2, "in_summary": 2, "qty": 2.0, "grand_total": total, "sold_total": total}}

    def erp_stock_post(self, params):
        prefix = {"in": "JU", "out": "OU", "adjust": "TK", "count": "TK", "transfer": "RL"}
        docs = []
        for index, step in enumerate(params["steps"]):
            kind = step["kind"]
            if kind == "void":
                docnum = docs[step["of"]]["state"]["ledger_docnum"]
                head = next(row for row in self.tables["STTRN"] if row["DOCNUM"] == docnum)
                if self.break_at != "void-kept":
                    head.update(DOCSTAT="C", TRNVAL=0.0)
                    self.tables["STCRD"] = [row for row in self.tables["STCRD"] if row["DOCNUM"] != docnum]
                docs.append({"kind": "void", "of": step["of"], "status": "void", "docnum": docnum, "name": docs[step["of"]]["name"],
                             "state": {"ledger_void_state": "voided", "ledger_docnum": docnum}, "job": {"verify_state": "ok"}, "book_paths": []})
                continue
            if kind == "transfer" and self.break_at == "one-warehouse":
                return {"ok": False, "why": "this ledger has only one Express warehouse: a transfer needs two", "reported": []}
            docnum, qty = f"{prefix[kind]}691115-00{index + 1}", float(step.get("qty", step.get("delta", 1)))
            self.add("STTRN", DOCNUM=docnum, LOCCOD="02" if self.break_at == "wrong-warehouse" else "01", DOCSTAT="N", TRNVAL=20.0,
                     PEOPLE="02" if kind == "transfer" else "")
            for sign in ((-1, 1) if kind == "transfer" else (-1,) if kind == "out" else (1,)):
                self.add("STCRD", DOCNUM=docnum, STKCOD="P001", TRNQTY=sign * (qty + (1 if self.break_at == "qty" else 0)))
            doc = {"kind": kind, "qty": qty, "name": f"SE-{index}", "state": {"ledger_state": "written", "ledger_docnum": docnum},
                   "job": {"verify_state": "ok"}, "book_paths": [params["ledger"]]}
            if kind == "transfer":
                doc["to_warehouse_code"] = "02"
            if kind == "count":
                doc.update(count="CNT-1", closed="Closed", draft_created=True, count_status="Posted",
                           draft_again=[True, self.break_at == "second-draft"])
            docs.append(doc)
        return {"ok": True, "company": "CO-1", "item_code": "P001", "warehouse_code": "01", "warehouses": ["01", "02"], "docs": docs}

    def cowork_intake_post(self, params):
        docnum, total = "HP691115-009", float(params["amount"]) + float(params["vat"])
        self.add("APTRN", DOCNUM=docnum, SUPCOD="V001" if self.break_at == "supplier" else "T001", NETAMT=total, VATAMT=float(params["vat"]))
        self.add("ISVAT", DOCNUM=docnum)
        self.add("GLJNL", VOUCHER=docnum)
        out = {"ok": True, "history": "H-1", "pushes": 1, "last": {"last_push_status": "success", "last_push_record": "PR-1"},
               "record": {"name": "PR-1", "status": "success", "docnum": docnum, "verify": "ok", "written": True, "duplicate_skipped": False,
                          "written_to": params["ledger"]},
               "expected": {"number": "ACC-1", "amount": float(params["amount"]), "vat": float(params["vat"]), "total": total}}
        if params.get("resubmit"):
            out["resubmit"] = {"status": "prepared", "name": "PR-2", "record": {"docnum": None, "duplicate_skipped": True}}
            if self.break_at == "second-doc":
                self.add("APTRN", DOCNUM="HP691115-010", SUPCOD="T001", NETAMT=total, VATAMT=35.0)
        return out

    def erp_mrerp_post(self, params):
        kind, total, vat = params["kind"], 321.0, 21.0
        journal = {"purchase_goods": [("5110-01", 300, 0), ("1161-11", vat, 0), ("2120-03", 0, total)],
                   "purchase_expense": [("5390-09", total, 0), ("2120-03", 0, total)],
                   "sales_credit": [("1130-01", total, 0), ("2160-12", 0, vat), ("4110-01", 0, 300)],
                   "sales_cash": [("1111-02", total, 0), ("2160-12", 0, vat), ("4110-01", 0, 300)]}[kind]
        if self.break_at == "unbalanced":
            journal = journal[:-1]
        docnum = "PG691115-000001"
        state = {"status": "submitted", "ledger_state": "written", "ledger_target": "mrerp", "ledger_docnum": docnum,
                 "ledger_book": None if self.break_at == "no-book" else params["account_set"], "grand_total": total, "vat_amount": vat}
        out = {"ok": True, "doc": "PUR-1", "state": state, "company": params["company"],
               "read": {"listed": 2 if self.break_at == "listed-twice" else 1, "form": {"txtdocnum": docnum}, "journals": 1, "journal_menu": 248,
                        "journal": [{"account": code, "debit": f"{debit:,.2f}" if debit else "", "credit": f"{credit:,.2f}" if credit else ""}
                                    for code, debit, credit in journal]}}
        if params.get("resend"):
            out["resend"] = {"send": "already_queued", "state": dict(state, ledger_docnum="PG691115-000002" if self.break_at == "renumbered" else docnum)}
        return out

    def erp_collection_lookup(self, params):
        return {"ok": True, "records": [dict(item, consent_ref="cc:S-1:3") for item in getattr(self, "cc_records", [])]}

    def talk(self):
        """替身对话：说一句就出写入计划；点「写入」时「CC 桌面版」把收款单写进替身账套。"""
        world, record = self, sys.modules["cc_lines"].Record

        class Talk:
            session_id, site, said, clicked = "S-1", "erp.test", [], None

            def say(self, text):
                Talk.said.append(text)

            def wait_idle(self, seconds=180):
                return None

            def record(self):
                users = [{"role": "user", "text": text, "at": 1.0 + index} for index, text in enumerate(Talk.said)]
                if world.break_at == "nagged":
                    users.append({"role": "user", "text": "刚开的，再读一次", "at": 2.5})
                plan = {"kind": "plan", "id": "p1", "at": 3.0, "topic": "p1", "count": 1, "answer": "write" if Talk.clicked else None,
                        "answered_at": Talk.clicked, "status": "started" if Talk.clicked else "awaiting"}
                reply = "已写入 Express：RE0000001（发票 IV691115-001）" if Talk.clicked else "计划如下"
                if world.break_at == "wrong-reply" and Talk.clicked:
                    reply = "已写入 Express：RE0000009"
                tools = [] if world.break_at == "no-draft" else [{"id": "t1", "name": "erp_collection_draft", "at": 2.0, "input": {}, "status": "ok"}]
                return record(turns=users + [{"role": "assistant", "text": reply, "at": 4.0}], tools=tools, cards=[plan], writes=[], stops=[])

            def click(self, card, choice):
                Talk.clicked = 5.0
                written = world._receipt("COL-CC", 400.0, 0.0, "cc")
                if world.break_at == "refused-by-desktop":
                    written.update(status="failed", docnum="")
                world.cc_records = [written]
                return True

            def cost(self):
                return 0.3

            def evidence(self):
                return {"session_id": "S-1", "events": []}

        Talk.said, Talk.clicked = [], None
        return Talk()

    def erp_operator_post(self, params):
        if self.break_at == "direct-first":
            return {"ok": True, "taken_by_direct": True, "doc": "PUR-1", "doc_id": "P:PUR-1"}
        kind = params["kind"]
        docnum = "HP691115-001" if kind == "purchase" else self.invoice
        if kind == "purchase":
            self.add("APTRN", DOCNUM=docnum, SUPCOD="V001", NETAMT=10.7, VATAMT=0.7)
            self.add("STCRD", DOCNUM=docnum, STKCOD="P001", TRNQTY=1.0)
            self.add("ISVAT", DOCNUM=docnum)
            self.add("GLJNL", VOUCHER=docnum)
            expected = {"supplier_code": "V001", "item_code": "P001", "qty": 1, "price": 10,
                        "totals": {"subtotal": 10.0, "vat_amount": 0.7, "grand_total": 10.7}}
            doc_id = "P:PUR-1"
        else:
            sale = self.erp_sales_post({"buyer": "ร้านเอซีซี 1", "ledger": params["ledger"]})
            expected, doc_id = sale["expected"], "S:SI-1"
        direct = self.break_at == "direct-job"
        return {"ok": True, "company": "CO-1", "doc": doc_id[2:], "doc_id": doc_id, "task": "TASK-1", "book_paths": [params["ledger"]],
                "state": {"ledger_state": "written", "ledger_docnum": docnum}, "expected": expected, "jobs": 2 if self.break_at == "two-jobs" else 1,
                "job": {"status": "success", "verify_state": "ok", "request_key": ("erp:purchase:" if direct else "erp:operator:TASK-1:") + "x"},
                "task_view": {"status": "finished", "documents": [{"doc_id": doc_id, "state": "not_done" if self.break_at == "task-open" else "saved",
                                                                   "docnum": docnum, "reason": ""}]}}

    def erp_denied(self, params):
        names = ["web.submit", "line.draft", "cc.draft", "collection.invoices", "collection.methods", "connect.list", "connect.rescan",
                 "connect.add"]
        outcome = {name: "denied" for name in names}
        if self.break_at == "open-door":
            outcome["connect.add"] = "allowed"
        counts = {"collections": 3, "companies": 1, "jobs": 9}
        return {"ok": True, "user": "acc.denied@x", "roles": [], "outcome": outcome, "before": counts,
                "after": {**counts, "collections": 4} if self.break_at == "left-record" else counts}

    def erp_company_connect(self, params):
        fresh = self.break_at != "not-fresh"
        if params.get("require_fresh") and not fresh:
            return {"ok": False, "why": "not a fresh site", "reported": []}
        row = {"identity": "express:1234567890123:00000", "tax_id": "1234567890123", "state": "new" if fresh else "added", "company": None}
        stuck = self.break_at == "switch"
        rounds = [{"target": target, "committed": target, "cached": "CO-9" if stuck else target, "effective": "CO-9" if stuck else target}
                  for target in ("CO-9", "CO-1", "CO-9", "CO-1")]
        extra = 1 if self.break_at == "second-company" else 0
        twice = 1 if self.break_at == "added-twice" else 0
        return {"ok": True, "name": "CO-1", "reported": [params["ledger"]],
                "before": {"connected": True, "total": 1, "row": row, "companies": 1},
                "rescan": {"devices": 1, "seconds": 8.0, "last": {"pending": 0, "online": 0}, "polls": [], "row_after": row},
                "add": {"state_before": row["state"], "blank_default": "CO-1" if fresh else None,
                        "first": {"id": "CO-1", "created": False, **({"adopted": True} if fresh else {})},
                        "again": {"id": "CO-1", "created": False}, "companies_after_first": 1 + extra, "companies_after_again": 1 + extra + twice,
                        "row_after": {**row, "state": "added", "company": "CO-1"}},
                "company": {"tax_id": "1234567890123", "is_active": 1, "express_enabled": 1, "warehouses": 1},
                "sync": {"states": ["queued", "reading", "ready"], "customers": 12, "suppliers": 3, "products": 40, "nbsp_customers": 1,
                         "facts": True, "stale_for_collection": self.break_at == "stale-facts", "receipt_types": 5, "cash_account": True},
                "reactivate": {"row_inactive": {**row, "state": "inactive"}, "companies_before": 1, "companies_after": 1,
                               "result": {"id": "CO-1", "created": False, "reactivated": True}, "row_after": {**row, "state": "added"},
                               "is_active": 1},
                "switch": {"allowed": 2, "rounds": rounds}}


class ChainScenarioTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        keys = {"PEARNLY_LOCK_DIR": self.folder.name + "/locks", "PEARNLY_ACC_ERP_USER": "acc.erp@maclab.test",
                "PEARNLY_ACC_ERP_TAX": "1234567890123", "PEARNLY_ACC_COWORK_USER": "acc@cowork.test",
                "PEARNLY_ACC_COWORK_CONNECTION": "CONN-1", "PEARNLY_ACC_MRERP_COMPANY": "CO-MR", "PEARNLY_ACC_BASELINE": r"C:\PearnlyAcceptance\baselines\TEST2@2026-11-13"}
        self.old = {key: os.environ.get(key) for key in keys}
        os.environ.update(keys)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in self.old.items()])
        self.runner = load("acceptance_run_for_chain", "scripts/acceptance/run.py")
        sys.modules.pop("acc_erp_chain", None)

    def go(self, scenario_id, break_at=""):
        world = World(break_at)
        module = "cowork_intake_express.py" if ".cowork." in scenario_id else \
            "erp_operator_express.py" if ".operator." in scenario_id else "erp_mrerp.py" if ".mrerp." in scenario_id else \
            "erp_cc_collection.py" if ".cc." in scenario_id else \
            "erp_company_connect.py" if ".company." in scenario_id or scenario_id.endswith(".denied") else \
            "erp_stock_express.py" if ".stock." in scenario_id else \
            "erp_more_express.py" if ".pos." in scenario_id or ".cancel." in scenario_id else "erp_collection_express.py"
        targets = types.SimpleNamespace(ledger=lambda host, path: world.book(), site=lambda target: world,
                                        windows=lambda host: types.SimpleNamespace(work="C:/PearnlyAcceptance"))
        scenario = {"id": scenario_id, "module": f"scripts/acceptance/scenarios/{module}", "resources": [], "budget_usd": 1 if ".cc." in scenario_id else 0}
        driver = load("cc_driver", "scripts/acceptance/cc_driver.py") if "cc_driver" not in sys.modules else sys.modules["cc_driver"]
        opened = driver.open_conversation
        driver.open_conversation = lambda *args, **kwargs: world.talk()
        try:
            return self.runner.run_one(scenario, pathlib.Path(self.folder.name) / "run", "win-test", False, targets=targets), world
        finally:
            driver.open_conversation = opened

    def test_every_scenario_passes_on_a_correct_world_and_restores_the_baseline(self):
        wrote = {"acc.erp.stock.in.express": 1, "acc.erp.stock.out.express": 2, "acc.erp.stock.adjust.express": 1,
                 "acc.erp.stock.transfer.express": 2, "acc.erp.stock.void.express": 1, "acc.erp.stock.count.express": 1,
                 "acc.cowork.intake.express": 1, "acc.cowork.intake.express.twice": 1,
                 "acc.erp.pos.day.express": 1, "acc.erp.purchase.cancel.express": 1, "acc.erp.sales.cancel.express": 1,
                 "acc.erp.sales.credit.express": 1, "acc.erp.collection.three": 4, "acc.erp.collection.nbsp": 2,
                 "acc.erp.collection.web.twice": 2, "acc.erp.collection.line.edit": 2, "acc.erp.collection.line.refused": 2}
        for scenario_id, docs in wrote.items():
            result, world = self.go(scenario_id)
            self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""), scenario_id)
            self.assertEqual((len(result["facts"]["docnums"]), world.calls[-1]), (docs, "restore"), scenario_id)
        for scenario_id in ("acc.erp.company.connect", "acc.erp.company.connect.fresh", "acc.erp.company.connect.twice"):
            result, world = self.go(scenario_id)
            self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""), scenario_id)
            self.assertEqual((result["facts"]["add_branch"], world.calls.count("restore")), ("adopted", 0))

    def test_three_entries_collect_the_same_invoice_and_the_balance_adds_up(self):
        result, world = self.go("acc.erp.collection.three")
        self.assertEqual(result["outcome"], self.runner.PASS)
        self.assertEqual([call for call in world.calls if call.endswith(".py")],
                         ["erp_sales_post.py"] + ["erp_collection_post.py"] * 3)
        self.assertEqual(world.received, 750.0)

    def test_each_defect_fixed_on_1115_fails_its_scenario_when_it_comes_back(self):
        cases = [("acc.erp.sales.credit.express", "prename", "称谓栏"), ("acc.erp.sales.credit.express", "paid", "未付款"),
                 ("acc.erp.collection.nbsp", "nbsp", "逐字符原样"), ("acc.erp.collection.three", "stale-read", "已读好"),
                 ("acc.erp.collection.web.twice", "twice", "记录只有一条"), ("acc.erp.collection.line.edit", "stale-card", "旧卡"),
                 ("acc.erp.collection.line.refused", "double", "ARTRN 多的行数"), ("acc.erp.collection.three", "stray", "预期以外"),
                 ("acc.erp.pos.day.express", "pos-twice", "第二张日汇总"), ("acc.erp.pos.day.express", "pos-short", "数量合计"),
                 ("acc.erp.purchase.cancel.express", "cancel-wrote", "没有任何文件变化"),
                 ("acc.erp.sales.cancel.express", "cancel-job", "多排写账件"),
                 ("acc.erp.stock.void.express", "void-kept", "作废后"), ("acc.erp.stock.in.express", "wrong-warehouse", "仓库码"),
                 ("acc.erp.stock.out.express", "qty", "物料与数量"), ("acc.erp.stock.count.express", "second-draft", "不新建"),
                 ("acc.erp.stock.adjust.express", "stray", "预期以外"),
                 ("acc.erp.company.connect", "switch", "三处一致"), ("acc.erp.company.connect", "stale-facts", "带收款方式"),
                 ("acc.erp.company.connect.fresh", "second-company", "接管"),
                 ("acc.erp.company.connect.twice", "added-twice", "再添加一次"),
                 ("acc.cowork.intake.express", "supplier", "供应商编码"), ("acc.cowork.intake.express.twice", "second-doc", "只多一行"),
                 ("acc.erp.denied", "open-door", "都被拒"), ("acc.erp.denied", "left-record", "都没变")]
        for scenario_id, break_at, words in cases:
            result, world = self.go(scenario_id, break_at)
            self.assertEqual(result["outcome"], self.runner.FAIL, (scenario_id, break_at, result["detail"]))
            self.assertIn(words, result["detail"], (scenario_id, break_at))
            if ".company." not in scenario_id and not scenario_id.endswith(".denied"):
                self.assertEqual(world.calls[-1], "restore", (scenario_id, break_at))

    def test_the_fresh_only_scenario_is_not_run_on_a_site_that_already_has_the_company(self):
        result, world = self.go("acc.erp.company.connect.fresh", "not-fresh")
        self.assertEqual(result["outcome"], self.runner.NOT_RUN)
        self.assertIn("not a fresh site", result["detail"])
        result, _ = self.go("acc.erp.company.connect", "not-fresh")
        self.assertEqual((result["outcome"], result["facts"]["add_branch"]), (self.runner.PASS, "added"))

    def test_documents_imported_into_mrerp_are_read_back_by_number(self):
        for kind in ("purchase.goods", "purchase.expense", "sales.credit", "sales.cash", "twice"):
            result, world = self.go(f"acc.erp.mrerp.{kind}")
            self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""), kind)
            self.assertEqual((result["facts"]["docnums"], world.calls.count("restore")), (["PG691115-000001"], 0), kind)
        for kind, break_at, words in (("purchase.goods", "unbalanced", "借方合计"), ("sales.cash", "listed-twice", "恰好一张"),
                                      ("sales.credit", "no-book", "哪本账套"), ("twice", "renumbered", "单号没变")):
            result, _ = self.go(f"acc.erp.mrerp.{kind}", break_at)
            self.assertEqual(result["outcome"], self.runner.FAIL, (kind, break_at))
            self.assertIn(words, result["detail"], (kind, break_at))

    def test_a_collection_said_in_the_cc_chat_is_written_once_and_read_back(self):
        for scenario_id in ("acc.erp.cc.collection", "acc.erp.cc.collection.nbsp"):
            result, world = self.go(scenario_id)
            self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""), scenario_id)
            self.assertEqual((len(result["facts"]["docnums"]), result["spent_usd"], world.calls[-1]), (2, 0.3, "restore"))
        for break_at, words in (("nbsp", "逐字符原样"), ("refused-by-desktop", "收款状态"), ("wrong-reply", "收款单号"), ("no-draft", "建了收款草稿")):
            result, world = self.go("acc.erp.cc.collection.nbsp", break_at)
            self.assertEqual(result["outcome"], self.runner.FAIL, break_at)
            self.assertIn(words, result["detail"], break_at)
            self.assertEqual(world.calls[-1], "restore", break_at)

    def test_documents_handed_to_the_operator_are_entered_once_and_read_back(self):
        for kind in ("purchase", "sales"):
            result, world = self.go(f"acc.erp.operator.{kind}.express")
            self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""), kind)
            self.assertEqual((len(result["facts"]["docnums"]), world.calls[-1]), (1, "restore"), kind)
        for break_at, words in (("direct-job", "代录交的"), ("two-jobs", "只有这一件"), ("task-open", "已录入")):
            result, world = self.go("acc.erp.operator.purchase.express", break_at)
            self.assertEqual(result["outcome"], self.runner.FAIL, break_at)
            self.assertIn(words, result["detail"], break_at)
            self.assertEqual(world.calls[-1], "restore", break_at)
        result, _ = self.go("acc.erp.operator.purchase.express", "direct-first")
        self.assertEqual(result["outcome"], self.runner.NOT_RUN)
        self.assertIn("直写先领走", result["detail"])
        for stub in ("cloud.purchase", "mrerp.sales"):
            result, world = self.go(f"acc.erp.operator.{stub}")
            self.assertEqual(result["outcome"], self.runner.NOT_RUN, stub)
            self.assertEqual([call for call in world.calls if call.endswith(".py")], [], stub)

    def test_an_employee_without_any_role_is_refused_everywhere(self):
        result, world = self.go("acc.erp.denied")
        self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""))
        self.assertEqual([call for call in world.calls if call.endswith(".py")], ["erp_denied.py"])

    def test_a_transfer_on_a_ledger_with_one_warehouse_is_not_run(self):
        result, _ = self.go("acc.erp.stock.transfer.express", "one-warehouse")
        self.assertEqual(result["outcome"], self.runner.NOT_RUN)
        self.assertIn("only one Express warehouse", result["detail"])

    def test_the_test_pc_lab_is_only_held_when_the_server_side_runs_there(self):
        scenario = {"resources": ["testpc2"], "site_resources": {"lab": ["testpc1"], "maclab": ["lab:pearnly-maclab-erp"]}}
        self.assertEqual(self.runner.resources_of(scenario, "lab"), ["testpc2", "testpc1"])
        self.assertEqual(self.runner.resources_of(scenario, "maclab"), ["testpc2", "lab:pearnly-maclab-erp"])
        self.assertEqual(self.runner.resources_of({"resources": ["testpc2"]}, "maclab"), ["testpc2"])
        old = os.environ.pop("PEARNLY_ACC_SITE_TARGET", None)
        try:
            self.assertEqual(self.runner.resources_of(scenario), ["testpc2", "testpc1"])   # 没说在哪：按测试机实验栈
        finally:
            if old is not None:
                os.environ["PEARNLY_ACC_SITE_TARGET"] = old

    def test_allowed_tables_come_from_the_desktop_write_lists(self):
        load("acceptance_run_for_chain_tables", "scripts/acceptance/run.py")
        chain = load("acc_erp_chain_tables", "scripts/acceptance/erp_chain.py")
        self.assertTrue({"ARTRN", "ARRCPIT", "ARRCPCQ", "BKTRN", "GLJNL", "GLJNLIT"} <= set(chain.receipt_tables()))
        self.assertTrue({"ARTRN", "ARMAS", "ISVAT"} <= set(chain.sales_tables()))
        self.assertFalse({"APTRN", "APMAS"} & set(chain.chain_tables()))
        self.assertTrue({"STTRN", "STCRD", "STMAS", "STLOC", "ISRUN"} <= set(chain.stock_tables()))
        self.assertFalse({"ARTRN", "APTRN"} & set(chain.stock_tables()))


if __name__ == "__main__":
    unittest.main()
