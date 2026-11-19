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
            if not self.tables.get("ISRUN"):   # 转账方式经 SHORTNAM 指到 BKMAS 的户头，才记银行流水（同原生、同 CC plan_bank）
                self.add("ISRUN", DOCTYP="ZR", PREFIX="TR", SHORTNAM="BB")
                self.add("BKMAS", BNKACC="BB")
            self.add("BKTRN", VOUCHER=docnum, AMOUNT=transfer)
            methods = [{"prefix": "TR", "role": "transfer", "amount": f"{transfer:.2f}"}]
        head = self.tables["ARTRN"][0]
        head.update(RCVAMT=self.received, REMAMT=self.total - self.received, CMPLAPP="Y" if self.received == self.total else "")
        return {"name": name, "status": "written", "docnum": docnum, "source": source, "customer": self.customer, "cash": f"{cash:.2f}",
                "total": f"{amount:.2f}", "methods": methods, "invoices": [{"docnum": self.invoice, "amount": f"{amount:.2f}"}],
                "balances": {self.invoice: f"{self.total - self.received:.2f}"}, "job": {"status": "success", "verify_state": ""},
                "book_paths": [r"\\accserver\ACCOUNT\69EXP\TEST2"]}

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
                   "job": {"status": "success", "verify_state": ""}, "book_paths": [params["ledger"]]}   # 库存 / 调拨写入时逐表读回，不交 verify
            if kind == "transfer":
                doc["to_warehouse_code"] = "02"
            if kind == "count":
                doc.update(count="CNT-1", closed="Closed", draft_created=True, count_status="Posted",
                           draft_again=[True, self.break_at == "second-draft"])
            docs.append(doc)
        return {"ok": True, "company": "CO-1", "item_code": "P001", "warehouse_code": "01", "warehouses": ["01", "02"], "docs": docs}

    def cowork_intake_post(self, params):
        detail = params.get("detail")
        if detail:   # A7：引导卡问「明细」；只记金额写表头、不写存货明细；不入账停在 items_mismatch
            asked = ["party", "account"] if self.break_at == "no-detail-question" else ["detail", "party", "account"]
            out = {"ok": True, "history": "H-A7", "card_kinds": asked, "detail": {"asked": True, "value": detail},
                   "prepared": {"name": "PR-A7", "status": "manual" if detail == "skip" else "prepared",
                                "reason": "items_mismatch" if detail == "skip" else ""}}
            if detail == "skip":
                if self.break_at == "skip-wrote":
                    self.add("APTRN", DOCNUM="RR691119-002", NETAMT=107.0, VATAMT=7.0, AMOUNT=100.0)
                return out
            docnum = "RR691119-001"
            self.add("APTRN", DOCNUM=docnum, SUPCOD="T001", NETAMT=107.0, VATAMT=7.0, AMOUNT=100.0)
            self.add("ISVAT", DOCNUM=docnum)
            self.add("GLJNL", VOUCHER=docnum)
            if self.break_at == "stock-lines":
                self.add("STCRD", DOCNUM=docnum, STKCOD="P001", TRNQTY=1)
            out["record"] = {"name": "PR-A7", "status": "success", "docnum": docnum, "verify": "ok", "written": True,
                             "duplicate_skipped": False, "written_to": params["ledger"]}
            return out
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
        if params.get("need_switch") and self.break_at == "switch-in-code-off":
            return {"ok": False, "why": "refuse-switch: MRERP_LEDGER_ENABLED is off in the tested source"}
        if kind == "clear_kick":
            self.kick_cleared = True
            return {"ok": True, "cleared": True}
        if kind == "held_kicked":
            self.pushing = "mrerp"
            first = {"ledger_state": "failed", "ledger_reason": "ERR_ACCOUNT_IN_USE", "ledger_docnum": ""}
            return {"ok": True, "doc": "PUR-7", "number": "PE2569-000007", "company": params["company"], "first": first, "state": first}
        shapes = {"menu_choice": "purchase_goods", "retry": "purchase_goods", "switch_off": "purchase_goods", "tax_purchase": "purchase_goods",
                  "readback": "purchase_goods", "void_importing": "sales_credit", "tax_sales": "sales_credit"}
        shape = shapes.get(kind, kind)
        if kind == "purchase_nobook":
            held = self.break_at != "imported-anyway"
            return {"ok": True, "doc": "PUR-9", "company": params["company"],
                    "state": {"ledger_state": "held" if held else "written", "ledger_reason": "express.account_set_missing" if held else "",
                              "ledger_docnum": "" if held else "PG1", "ledger_target": "mrerp", "grand_total": total, "vat_amount": vat}}
        if kind == "purchase_novat":
            vat = 0.0
        kind = shape
        journal = {"purchase_goods": [("5110-01", 300, 0), ("1161-11", vat, 0), ("2120-03", 0, total)],
                   "purchase_expense": [("5390-09", total, 0), ("2120-03", 0, total)],
                   "sales_credit": [("1130-01", total, 0), ("2160-12", 0, vat), ("4110-01", 0, 300)],
                   "sales_cash": [("1111-02", total, 0), ("2160-12", 0, vat), ("4110-01", 0, 300)],
                   "purchase_cash": [("5110-01", 300, 0), ("1161-11", vat, 0), ("1111-02", 0, total)],
                   "sales_discount": [("1130-01", total, 0), ("2160-12", 0, vat), ("4110-01", 0, 300)],
                   "purchase_novat": [("5110-01", total, 0), ("2120-03", 0, total)]}[kind]
        if self.break_at == "unbalanced":
            journal = journal[:-1]
        docnum = "PG691115-000001"
        state = {"status": "submitted", "ledger_state": "written", "ledger_target": "mrerp", "ledger_docnum": docnum,
                 "ledger_book": None if self.break_at == "no-book" else params["account_set"], "grand_total": total, "vat_amount": vat}
        out = {"ok": True, "doc": "PUR-1", "state": state, "company": params["company"],
               "read": {"listed": 2 if self.break_at == "listed-twice" else 1, "form": {"txtdoctype": docnum[:2], "txtdocnum": docnum[2:]}, "journals": 1, "journal_menu": 248,
                        "journal": [{"account": code, "debit": f"{debit:,.2f}" if debit else "", "credit": f"{credit:,.2f}" if credit else ""}
                                    for code, debit, credit in journal]}}
        if params.get("resend"):
            out["resend"] = {"send": "already_queued", "state": dict(state, ledger_docnum="PG691115-000002" if self.break_at == "renumbered" else docnum)}
        if params["kind"] in ("purchase_goods", "sales_credit"):   # 称谓分栏读回
            prefix, core = ("บริษัท", "เอซีซี 1 จำกัด") if params["kind"] == "purchase_goods" else ("หจก.", "เอซีซี 1")
            out["party"] = {"name": "x", "tax_id": "1", "prefix": prefix, "core": core}
            out["read"]["party"] = {"found": True, "prefix": "" if self.break_at == "prefix-lost" else prefix,
                                    "name": core if self.break_at != "prefix-lost" else "บริษัท " + core}
        return self._mrerp_more(params, out, state, total, vat)

    def _mrerp_more(self, params, out, state, total, vat):
        """第 6 批各种类在替身里的样子（正确世界 + 各自的缺陷开关）。"""
        kind = params["kind"]
        if kind == "menu_choice":
            if self.break_at == "not-held":
                out["first"] = dict(state)
                return out
            out["first"] = {"ledger_state": "held", "ledger_reason": "choice_needed:mrerp", "ledger_docnum": ""}
            out["menu"] = "68"
            out["choice"] = {"offered": ["68", "454"], "menu_after": None if self.break_at == "menu-forgotten" else "68"}
        elif kind == "retry":
            out["first"] = {"ledger_state": "held", "ledger_reason": "mrerp.login_missing", "ledger_docnum": ""}
            out["retry"] = {"can_retry": self.break_at != "retry-hidden"}
        elif kind == "switch_off":
            queued = {"ledger_state": "written" if self.break_at == "switch-ignored" else "queued",
                      "ledger_docnum": "PG1" if self.break_at == "switch-ignored" else ""}
            company = {"express_enabled": 0, "mrerp_enabled": 1, "mrerp_account_set": "6:1"}
            out["off"] = {"handles": False, "state": queued, "company_before": company, "company_after": company}
            out["on_again"] = {"handles": True}
        elif kind == "void_importing":
            out["import_outcome"] = {"status": "imported", "docnum": state["ledger_docnum"]}
            if self.break_at != "void-clean":
                state.update(status="void", ledger_state="held", ledger_reason="express.voided_after_write")
        elif kind in ("tax_purchase", "tax_sales"):
            invoice_no = "ACC-1-0"
            out["read"]["tax"] = {"invoice_no": invoice_no, "pending": [[invoice_no]], "passed": 200, "left_pending": 0,
                                  "report": [] if self.break_at == "tax-missing" else [["1", invoice_no, f"{total - vat:,.2f}", f"{vat:,.2f}"]]}
        elif kind == "readback" and getattr(self, "pushing", "") == "mrerp":
            out["doc"] = params["doc"]
        return out

    def erp_push_held(self, params):
        kind = params["kind"]
        self.pushing, queued = kind, params.get("leave") == "queued"
        self.operating = queued
        expected = {"supplier_code": "V001", "item_code": "P001", "qty": 1, "price": 10,
                    "totals": {"subtotal": 10.0, "vat_amount": 0.7, "grand_total": 10.7}} if kind == "purchase" else \
            {"new_customer": True, "buyer": "ร้านเอซีซี 1", "prename": "ร้าน", "core_name": "เอซีซี 1",
             "totals": {"subtotal": 1000.0, "vat_amount": 70.0, "grand_total": self.total}}
        return {"ok": True, "company": "CO-1", "doctype": "Purchase Document" if kind == "purchase" else "ERP Sales Document", "doc": "DOC-1",
                "doc_id": "P:DOC-1", "number": "PE2569-000001", "taken": self.break_at == "taken", "expected": expected,
                "jobs": 0 if queued else 1, "state": {"ledger_state": "queued" if queued else "failed", "ledger_docnum": ""}}

    def erp_operator_sources(self, params):
        """替身：代录对话开之前这家公司这个月待录的单（leftover：前几格残留了一张）。"""
        rows = [{"doc_id": "P:DOC-1", "kind": getattr(self, "pushing", "purchase"), "ref": "PE2569-000001", "date": "2026-11-17"}]
        if self.break_at == "leftover":
            rows.append({"doc_id": "S:OLD-9", "kind": "sales", "ref": "SI2569-000009", "date": "2026-11-16"})
        return {"ok": True, "total": len(rows), "documents": rows}

    def erp_doc_state(self, params):
        kind = getattr(self, "pushing", "purchase")
        docnum = "HP691115-001" if kind == "purchase" else self.invoice
        operator = (self.break_at == "operator-job") != bool(getattr(self, "operating", False))
        return {"ok": True, "state": {"ledger_state": "written", "ledger_docnum": docnum}, "book_paths": [r"\\accserver\ACCOUNT\69EXP\TEST2"],
                "job": {"status": "success", "verify_state": "ok", "request_key": "erp:operator:T:x" if operator else f"erp:{kind}:DOC-1#2"},
                "party": {"express_code": "" if self.break_at == "masters-unlinked" else "C001", "express_company": "CO-1"}, "jobs": 3 if self.break_at == "extra-job" else 1 if getattr(self, "operating", False) else 2}

    def push_written(self):
        """替身「CC 桌面版」：对话里点了写入之后把那张停下的单写进替身账套。"""
        if self.pushing == "mrerp":
            return   # MR.ERP 不是 Express 账套：替身里导入由 readback 那一步交回
        if self.pushing == "purchase":
            self.add("APTRN", DOCNUM="HP691115-001", SUPCOD="V001", NETAMT=10.7, VATAMT=0.7)
            self.add("GLJNL", VOUCHER="HP691115-001")
        else:
            self.erp_sales_post({"buyer": "ร้านเอซีซี 1", "ledger": ""})

    def erp_collection_lookup(self, params):
        return {"ok": True, "records": [dict(item, consent_ref="guide:RUN1:4") for item in getattr(self, "cc_records", [])]}

    def talk(self):
        """替身对话：推送场景说一句就出写入计划、点「写入」写成；收款（11-19 起）说一句只开收款卡，卡上答完、点「写入」时
        「CC 桌面版」把收款单写进替身账套。"""
        world, record = self, sys.modules["cc_lines"].Record

        class Talk:
            session_id, site, said, clicked, stage = "S-1", "erp.test", [], None, 0

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
                collecting = not getattr(world, "pushing", "")
                reply = "已写入 Express：RE0000001（发票 IV691115-001）" if Talk.clicked else "已打开收款卡片" if collecting else "计划如下"
                if getattr(world, "pushing", "") and Talk.clicked:
                    reply = "已写入 Express：" + ("HP691115-001" if world.pushing == "purchase" else world.invoice)
                    if world.pushing == "mrerp":
                        reply = "已写入 MR.ERP：PG691115-000001"
                if world.break_at == "wrong-reply" and Talk.clicked:
                    reply = "已写入 Express：RE0000009"
                tools = [] if world.break_at == "no-open" or not collecting else [{"id": "t1", "name": "erp_collection_guide", "at": 2.0, "input": {}, "status": "ok"}]
                if world.break_at == "old-path" and collecting:
                    tools.append({"id": "t2", "name": "erp_collection_invoices", "at": 2.1, "input": {}, "status": "ok"})
                if collecting:
                    plan = {**plan, "id": "RUN1", "topic": "guide:collection", "guide": True,
                            "status": "done" if Talk.clicked else "asking"}
                return record(turns=users + [{"role": "assistant", "text": reply, "at": 4.0}], tools=tools, cards=[plan], writes=[], stops=[])

            def guide(self, business, seconds=120):
                if world.break_at == "no-open":
                    return None
                spoken = world.customer.replace(NBSP, " ")
                if Talk.stage == 0:
                    first = {"key": "customer", "input": "choice", "options": [{"code": world.customer, "name": "A"}],
                             "query": "" if world.break_at == "no-query" else spoken}
                    return {"run": "RUN1", "status": "asking", "version": 1, "card": {"questions": [first], "answers": {}}}
                if Talk.stage == 1:
                    return {"run": "RUN1", "status": "asking", "version": 2, "card": {"questions": [
                        {"key": "invoices", "input": "amounts", "options": [{"code": world.invoice, "amount": "1070"}]},
                        {"key": "methods", "input": "split", "options": [{"code": "cash"}]}], "answers": {"customer": world.customer}}}
                return {"run": "RUN1", "status": "reviewing", "version": 3, "review": {"total": "400.00"}}

            def guide_search(self, run, key, txt):
                return {"options": [{"code": world.customer}]}

            def guide_answer(self, run, answers, version):
                Talk.stage += 1
                return {"run": run, "status": "asking", "version": version + 1}

            def guide_confirm(self, run, version):
                Talk.clicked = 5.0
                written = world._receipt("COL-CC", 400.0, 0.0, "cc")
                if world.break_at == "refused-by-desktop":
                    written.update(status="failed", docnum="")
                world.cc_records = [written]
                return {"run": run, "status": "done", "version": version + 1}

            def click(self, card, choice):
                Talk.clicked = 5.0
                if getattr(world, "pushing", ""):
                    world.push_written()
                    return True
                written = world._receipt("COL-CC", 400.0, 0.0, "cc")
                if world.break_at == "refused-by-desktop":
                    written.update(status="failed", docnum="")
                world.cc_records = [written]
                return True

            def cost(self):
                return 0.3

            def evidence(self):
                return {"session_id": "S-1", "events": []}

        Talk.said, Talk.clicked, Talk.stage = [], None, 0
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

    # ── 第 5 批（修复前红）：break_at 是「还没修」的那种世界
    def _purchase(self, docnum, supplier, item, new=False, group="DM"):
        self.add("APTRN", DOCNUM=docnum, SUPCOD=supplier, NETAMT=10.7, VATAMT=0.7)
        self.add("STCRD", DOCNUM=docnum, STKCOD=item, TRNQTY=1.0)
        self.add("ISVAT", DOCNUM=docnum)
        self.add("GLJNL", VOUCHER=docnum)
        if new:
            self.add("STMAS", STKCOD=item, ACCCOD="ST01" if self.break_at == "h5-unasked" else group)
            self.add("APMAS", SUPCOD=supplier)

    def erp_operator_card(self, params):
        kind, docnum = params["kind"], "HP691116-001"
        if kind == "stock_group" and self.break_at == "h5-fit-one":
            return {"ok": False, "why": "unmet: the acceptance book has 1 fit stock account group(s); asking before the start needs two or more"}
        doc_id = "I:LI-1" if kind == "intake" else "P:PUR-9"
        items = [{"kind": "payment", "subject": "pay", "state": "proposed", "value": "cash", "options": ["cash", "transfer"]}]
        if kind != "intake":
            items.append({"kind": "item", "subject": "ACC Item", "state": "proposed", "value": "__new__", "options": []})
        asked_group = kind != "intake" and self.break_at != "h5-unasked"
        rounds = [{"listed": self.break_at != "h3-drop", "not_writable": [], "waiting": [], "items": items}]
        if asked_group:
            rounds.append({"listed": True, "not_writable": [], "waiting": [],
                           "items": [{"kind": "stock_group", "subject": "BOOK", "state": "proposed", "value": "DM", "options": ["DM", "P002", "ST01"]}]})
        out = {"ok": True, "company": "CO-1", "task": "TASK-1", "doc_id": doc_id, "book": "B", "rounds": rounds, "card": rounds[0],
               "unanswered": [], "expand_errors": [], "fit_groups": ["DM", "P002"],
               "remembered": {"ok": False, "error": "PermissionError"} if self.break_at == "h3-403" else {"ok": True, "saved": 1},
               "card_after": {"listed": True, "items": []}, "group_chosen": "DM" if asked_group else "",
               "handed": {"batch": "TASK-1", "results": [{"doc_id": doc_id, "status": "queued"}]}}
        if kind == "intake":
            out["intake"] = "LI-1"
        if self.break_at in ("h3-drop", "h3-403"):
            return {**out, "doc": "", "state": {}}
        if self.break_at == "h5-unasked":
            # 修复前：开始前不问，电脑按用得最多的组建档、不合格就写到一半停下，卡上只有「重试」
            return {**out, "doc": "PUR-9", "state": {"ledger_state": "held", "ledger_reason": "[STOCK_ACC_GROUP_UNFIT] ST01", "ledger_job": "J-1"},
                    "stopped_card": {"listed": True, "items": []}, "job": {"status": "manual"}}
        new = kind != "intake"
        supplier, item = ("V900", "N900") if kind == "new_masters" else ("V001", "N900" if new else "P001")
        self._purchase(docnum, supplier, item, new=new)
        linked = self.break_at != "unlinked"
        out.update(doc="PUR-9", state={"ledger_state": "written", "ledger_docnum": docnum}, book_paths=[params["ledger"]], jobs=1,
                   job={"status": "success", "verify_state": "ok", "request_key": "erp:operator:TASK-1:x"},
                   task_view={"status": "finished", "documents": [{"doc_id": doc_id, "state": "saved", "docnum": docnum, "reason": ""}]},
                   expected={"supplier_code": supplier, "item_code": item, "qty": 1, "price": 10,
                             "totals": {"subtotal": 10.0, "vat_amount": 0.7, "grand_total": 10.7}})
        if kind == "new_masters":
            # 修复前：回执的码在 meta.operator.masters，服务端不读 → 单上的档没挂码，主档同步按码另建一条
            out.update(made_supplier="SUP-9", made_item="PRD-9", sync_state="ready",
                       made_codes={"supplier": "V900" if linked else "", "item": "N900" if linked else ""},
                       suppliers_new=[{"name": "SUP-9", "express_code": "V900" if linked else None}]
                       + ([] if linked else [{"name": "SUP-10", "express_code": "V900"}]),
                       products_new=[{"name": "PRD-9", "express_code": "N900" if linked else None}]
                       + ([] if linked else [{"name": "PRD-10", "express_code": "N900"}]))
        return out

    def erp_operator_no_computer(self, params):
        if self.break_at == "has-office":
            return {"ok": False, "why": "unmet: an office computer covers this company (acc.erp@maclab.test)"}
        fixed, words = self.break_at != "no-hook", "CC 桌面版未连接"
        return {"ok": True, "company": "CO-NOPC", "doc": "PUR-N", "state": {"ledger_state": "queued", "ledger_job": "J-N"},
                "job": {"name": "J-N", "status": "queued", "actor": params["user"]}, "words": words,
                "doc_note": "express.cc_desktop_not_connected",
                "computer_state": "express.cc_desktop_not_connected" if fixed else "<no computer_state hook>",
                "status": {"status": "queued", "no_computer": words if fixed else None},
                "feed": {"says_not_connected": fixed, "waiting_computer": fixed}, "cleaned": {"doc": "PUR-N", "job": "J-N", "task": "T"}}

    def erp_office_actor(self, params):
        office, phone = params["user"], params["phone_user"]
        if self.break_at == "owner-actor":
            # 修复前（11-17 定稿前）：没有替写，归开单人的件没有电脑来领，一直「CC 桌面版未连接」
            return {"ok": True, "company": "CO-1", "doc": "PUR-M", "expected_actor": phone,
                    "first_job": {"name": "J-M", "status": "queued", "actor": phone}, "note": "express.cc_desktop_not_connected",
                    "state": {"ledger_state": "queued", "ledger_job": "J-M"}, "job": {"status": "queued", "actor": phone}}
        self._purchase("HP691116-002", "V001", "P001")
        return {"ok": True, "company": "CO-1", "doc": "PUR-M", "expected_actor": phone, "note": "",
                "first_job": {"name": "J-M", "status": "queued", "actor": phone}, "owner": phone,
                "state": {"ledger_state": "written", "ledger_docnum": "HP691116-002"}, "book_paths": [params["ledger"]],
                "job": {"status": "success", "verify_state": "ok", "actor": phone, "writer": office},
                "expected": {"supplier_code": "V001", "item_code": "P001", "qty": 1, "price": 10,
                             "totals": {"subtotal": 10.0, "vat_amount": 0.7, "grand_total": 10.7}}}

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


class MisuseWorld(World):
    """收款不正常使用的替身：账套能被「占着」（ledger.py hold）、测试机的 CC 桌面版能停 / 起；站点脚本 erp_collection_misuse.py 的四种 mode。"""

    def __init__(self, break_at=""):
        super().__init__(break_at)
        self.holding, self.cc_running, self.pending = False, True, []

    def book(self):
        book, world = super().book(), self

        def probe():
            return {"ok": True, "busy": ["ISRUN.DBF"] if world.holding else []}

        def hold(seconds):
            world.holding = world.break_at != "no-hold"
            return {"ok": True, "held": ["ISRUN.DBF"], "seconds": seconds}

        book.probe, book.hold = probe, hold
        return book

    def windows(self):
        world = self

        def cc(mode, *args, timeout=300):
            world.calls.append(f"cc-{mode}")
            world.cc_running = mode == "start" or (mode == "state" and world.cc_running)
            return {"ok": True, "mode": mode, "running": world.cc_running, "beat": "[heartbeat] ok" if world.cc_running else ""}

        return types.SimpleNamespace(work="C:/PearnlyAcceptance", cc=cc)

    def erp_collection_misuse(self, params):
        mode = params["mode"]
        if mode == "warm":   # 离线那格停 CC 之前：电脑在线时把发票读进快照
            return {"ok": True, "company": "CO-1", "invoice": {"docnum": params.get("invoice"), "available": "1070.00"}}
        if mode == "submit":
            blocked = self.holding or not self.cc_running
            records = []
            for index, item in enumerate(params["entries"]):
                name = f"COL-{index + 1}"
                self.pending.append((name, float(item["cash"]), item["entry"]))
                leaked = not blocked or self.break_at == "written-while-blocked"
                records.append({"name": name, "entry": item["entry"], "cash": item["cash"], "looks": 30, "written_seen": leaked,
                                "waiting_seen": self.holding and self.break_at != "not-waiting", "leased_seen": False,
                                "last": {"status": "written" if leaked else "queued", "docnum": "", "jobs": 1, "waiting": self.holding}})
            return {"ok": True, "company": "CO-1", "records": records}
        if mode == "settle":
            self.holding = False
            out = []
            for name, cash, entry in self.pending:
                record = self._receipt(name, cash, 0.0, entry)
                record["jobs"] = 2 if self.break_at == "two-jobs" else 1
                out.append(record)
            return {"ok": True, "company": "CO-1", "records": out}
        if mode == "line_redelivered":
            again = self.break_at == "redelivered-twice"
            record = self._receipt("COL-LINE", 300.0, 0.0, "line")
            if again:
                self._receipt("COL-LINE", 300.0, 0.0, "line")
            record["jobs"] = 2 if again else 1
            return {"ok": True, "company": "CO-1", "card": {"name": "COL-LINE", "version": 1}, "jobs_after_taps": record["jobs"],
                    "consent": "line:e1", "record": record,
                    "deliveries": [{"event": "e1", "claimed": True, "handled": True, "replies": [{"kind": "reply"}]},
                                   {"event": "e1", "claimed": again, "handled": True if again else None, "replies": []},
                                   {"event": "e2", "claimed": True, "handled": True, "replies": []}]}
        if mode == "line_unbound":
            wrote = self.break_at == "unbound-wrote"
            taps = [{"event": event, "handled": True, "replies": [{"kind": "reply"}], "texts": [text], "expect": text}
                    for event, text in (("old", "请先绑定 ERP 账号"), ("start", "请先绑定 ERP 账号"), ("text", "码不对"))]
            if self.break_at == "unbound-silent":
                taps[0].update(texts=[], replies=[])
            return {"ok": True, "company": "CO-1", "taps": taps, "card": {"name": "COL-LINE", "version": 1},
                    "after": {"status": "queued" if wrote else "draft", "version": 1, "job": "J-1" if wrote else "", "docnum": "",
                              "consent": "line:old" if wrote else "", "jobs": 1 if wrote else 0},
                    "collections": {"before": 1, "after": 1}, "stranger_session": False, "record": {"status": "withdrawn"}}
        raise AssertionError(mode)


class ChainScenarioTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        keys = {"PEARNLY_LOCK_DIR": self.folder.name + "/locks", "PEARNLY_ACC_ERP_USER": "acc.erp@maclab.test",
                "PEARNLY_ACC_ERP_TAX": "1234567890123", "PEARNLY_ACC_COWORK_USER": "acc@cowork.test",
                "PEARNLY_ACC_COWORK_CONNECTION": "CONN-1", "PEARNLY_ACC_MRERP_COMPANY": "CO-MR",
                "PEARNLY_ACC_ERP_NOPC_USER": "nopc@maclab.test", "PEARNLY_ACC_ERP_NOPC_TAX": "9999999999994",
                "PEARNLY_ACC_ERP_PHONE_USER": "phone@maclab.test", "PEARNLY_ACC_BASELINE": r"C:\PearnlyAcceptance\baselines\TEST2@2026-11-13"}
        self.old = {key: os.environ.get(key) for key in keys}
        os.environ.update(keys)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in self.old.items()])
        self.runner = load("acceptance_run_for_chain", "scripts/acceptance/run.py")
        sys.modules.pop("acc_erp_chain", None)

    def go(self, scenario_id, break_at=""):
        world = World(break_at)
        fixes = (".operator.intake.", ".operator.stock.", ".operator.masters.", ".operator.no.")
        module = "erp_operator_fixes.py" if any(part in scenario_id for part in fixes) else \
            "erp_office_actor.py" if ".office.actor." in scenario_id else \
            "cowork_intake_express.py" if ".cowork." in scenario_id else \
            "erp_operator_talk.py" if ".operator.talk." in scenario_id else "erp_operator_express.py" if ".operator." in scenario_id else "erp_mrerp.py" if ".mrerp." in scenario_id else \
            "erp_cc_push.py" if ".cc.push." in scenario_id else "erp_cc_collection.py" if ".cc." in scenario_id else \
            "erp_company_connect.py" if ".company." in scenario_id or scenario_id.endswith(".denied") else \
            "erp_stock_express.py" if ".stock." in scenario_id else \
            "erp_more_express.py" if ".pos." in scenario_id or ".cancel." in scenario_id else "erp_collection_express.py"
        targets = types.SimpleNamespace(ledger=lambda host, path: world.book(), site=lambda target: world,
                                        windows=lambda host: types.SimpleNamespace(work="C:/PearnlyAcceptance"))
        scenario = {"id": scenario_id, "module": f"scripts/acceptance/scenarios/{module}", "resources": [], "budget_usd": 1 if ".cc." in scenario_id or ".talk." in scenario_id else 0}
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
                 "acc.cowork.intake.express": 1, "acc.cowork.intake.express.twice": 1, "acc.cowork.intake.detail.amount_only": 1,
                 "acc.erp.pos.day.express": 1, "acc.erp.purchase.cancel.express": 1, "acc.erp.sales.cancel.express": 1,
                 "acc.erp.sales.credit.express": 1, "acc.erp.collection.three": 4, "acc.erp.collection.nbsp": 2,
                 "acc.erp.collection.web.twice": 2, "acc.erp.collection.line.edit": 2, "acc.erp.collection.line.refused": 2}
        for scenario_id, docs in wrote.items():
            result, world = self.go(scenario_id)
            self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""), scenario_id)
            self.assertEqual((len(result["facts"]["docnums"]), world.calls[-1]), (docs, "restore"), scenario_id)
        result, world = self.go("acc.cowork.intake.detail.skip")   # 不入账：不写，没有单号
        self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""))
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
                 ("acc.cowork.intake.detail.amount_only", "stock-lines", "没有存货明细行"),
                 ("acc.cowork.intake.detail.amount_only", "no-detail-question", "问了「明细」"),
                 ("acc.cowork.intake.detail.skip", "skip-wrote", "与基线逐文件一致"),
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

    def go_misuse(self, scenario_id, break_at=""):
        world = MisuseWorld(break_at)
        targets = types.SimpleNamespace(ledger=lambda host, path: world.book(), site=lambda target: world, windows=lambda host: world.windows())
        scenario = {"id": scenario_id, "module": "scripts/acceptance/scenarios/erp_collection_misuse.py", "resources": [], "budget_usd": 0}
        return self.runner.run_one(scenario, pathlib.Path(self.folder.name) / "run", "win-test", False, targets=targets), world

    def test_collection_misuse_scenarios_pass_on_a_correct_world_and_restore_the_baseline(self):
        for scenario_id, docs in (("acc.erp.collection.busy", 4), ("acc.erp.collection.offline", 4),
                                  ("acc.erp.collection.line.redelivered", 2), ("acc.erp.collection.line.unbound", 1)):
            result, world = self.go_misuse(scenario_id)
            self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""), scenario_id)
            self.assertEqual((len(result["facts"]["docnums"]), world.calls[-1]), (docs, "restore"), scenario_id)
        result, world = self.go_misuse("acc.erp.collection.offline")
        self.assertEqual([call for call in world.calls if call.startswith("cc-")], ["cc-stop", "cc-start"])   # 停了就起回来
        self.assertTrue(world.cc_running)

    def test_collection_misuse_scenarios_fail_when_the_product_misbehaves(self):
        cases = [("acc.erp.collection.busy", "written-while-blocked", "没有写成"), ("acc.erp.collection.busy", "not-waiting", "等 Express 关闭"),
                 ("acc.erp.collection.offline", "written-while-blocked", "没有写成"), ("acc.erp.collection.offline", "two-jobs", "只一件写账件"),
                 ("acc.erp.collection.line.redelivered", "redelivered-twice", "补发的同一事件号"),
                 ("acc.erp.collection.line.unbound", "unbound-wrote", "没有写账件"),
                 ("acc.erp.collection.line.unbound", "unbound-silent", "只回了那一句")]
        for scenario_id, break_at, words in cases:
            result, world = self.go_misuse(scenario_id, break_at)
            self.assertEqual(result["outcome"], self.runner.FAIL, (scenario_id, break_at, result["detail"]))
            self.assertIn(words, result["detail"], (scenario_id, break_at))
            self.assertEqual(world.calls[-1], "restore", (scenario_id, break_at))
        result, world = self.go_misuse("acc.erp.collection.offline", "two-jobs")
        self.assertTrue(world.cc_running)   # 半路不通过也把 CC 桌面版起回来了

    def test_collection_misuse_without_its_scene_is_not_run(self):
        result, _ = self.go_misuse("acc.erp.collection.busy", "no-hold")
        self.assertEqual(result["outcome"], self.runner.NOT_RUN)
        self.assertIn("占用没造起来", result["detail"])
        result, _ = self.go_misuse("acc.erp.collection.books")
        self.assertEqual(result["outcome"], self.runner.NOT_RUN)
        self.assertIn("留桩", result["detail"])

    def test_the_rest_of_mrerp_passes_on_a_correct_world_and_fails_on_each_defect(self):
        for kind in ("menu.choice", "retry", "void.importing", "switch.off", "tax.purchase", "tax.sales", "cc.push"):
            result, world = self.go(f"acc.erp.mrerp.{kind}")
            self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""), kind)
            self.assertEqual(result["facts"]["docnums"], ["PG691115-000001"], kind)
        for kind, break_at, words in (("menu.choice", "menu-forgotten", "又记着原来那个"), ("retry", "retry-hidden", "再写一次"),
                                      ("switch.off", "switch-ignored", "仍排着"), ("void.importing", "void-clean", "作废后写进了"),
                                      ("purchase.goods", "prefix-lost", "称谓栏"), ("sales.credit", "prefix-lost", "称谓栏"),
                                      ("tax.purchase", "tax-missing", "税报告"), ("tax.sales", "tax-missing", "税报告")):
            result, _ = self.go(f"acc.erp.mrerp.{kind}", break_at)
            self.assertEqual(result["outcome"], self.runner.FAIL, (kind, break_at, result["detail"]))
            self.assertIn(words, result["detail"], (kind, break_at))
        result, _ = self.go("acc.erp.mrerp.menu.choice", "not-held")
        self.assertEqual(result["outcome"], self.runner.NOT_RUN)
        result, world = self.go("acc.erp.mrerp.cc.push", "switch-in-code-off")
        self.assertEqual(result["outcome"], self.runner.NOT_RUN)
        self.assertIn("refuse-switch", result["detail"])
        self.assertFalse(getattr(world, "kick_cleared", False))   # 没造单就没记被踢，收尾不用清

    def test_documents_imported_into_mrerp_are_read_back_by_number(self):
        for kind in ("purchase.goods", "purchase.expense", "sales.credit", "sales.cash", "twice", "purchase.cash", "sales.discount",
                     "purchase.novat"):
            result, world = self.go(f"acc.erp.mrerp.{kind}")
            self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""), kind)
            self.assertEqual((result["facts"]["docnums"], world.calls.count("restore")), (["PG691115-000001"], 0), kind)
        result, _ = self.go("acc.erp.mrerp.purchase.nobook")
        self.assertEqual((result["outcome"], result["detail"], result["facts"]["docnums"]), (self.runner.PASS, "", []))
        result, _ = self.go("acc.erp.mrerp.purchase.nobook", "imported-anyway")
        self.assertEqual(result["outcome"], self.runner.FAIL)
        self.assertIn("没有账套", result["detail"])
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
        for break_at, words in (("nbsp", "逐字符原样"), ("refused-by-desktop", "收款状态"), ("wrong-reply", "收款单号"), ("no-open", "开卡工具"),
                                ("old-path", "打字只开卡"), ("no-query", "预填")):
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

    def test_a_stopped_document_is_sent_again_from_the_cc_chat(self):
        for kind in ("purchase", "sales"):
            result, world = self.go(f"acc.erp.cc.push.{kind}")
            self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""), kind)
            self.assertEqual((len(result["facts"]["docnums"]), world.calls[-1]), (1, "restore"), kind)
        for break_at, words in (("operator-job", "直写"), ("extra-job", "只多一件"), ("wrong-reply", "单号与账本一致")):
            result, world = self.go("acc.erp.cc.push.purchase", break_at)
            self.assertEqual(result["outcome"], self.runner.FAIL, break_at)
            self.assertIn(words, result["detail"], break_at)
        result, _ = self.go("acc.erp.cc.push.purchase", "taken")
        self.assertEqual(result["outcome"], self.runner.NOT_RUN)

    def test_an_order_in_the_operator_chat_is_entered_once_and_read_back(self):
        result, world = self.go("acc.erp.operator.talk.purchase")
        self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""))
        self.assertEqual((result["facts"]["docnums"], world.calls[-1]), (["HP691115-001"], "restore"))
        for break_at, words in (("operator-job", "代录交的"), ("extra-job", "只有这一件")):
            result, _ = self.go("acc.erp.operator.talk.purchase", break_at)
            self.assertEqual(result["outcome"], self.runner.FAIL, break_at)
            self.assertIn(words, result["detail"], break_at)

    def test_a_sales_order_in_the_operator_chat_is_entered_once_and_read_back(self):
        """第 7 批 ERP-O-S-L：代录对话录一张赊销单；开对话前这个月有别的待录单记没测到（不开对话、不碰账套之外的东西），
        代录回执的码没挂回 ERP 客户（首跑缺陷 2）判不通过在客户编码。"""
        result, world = self.go("acc.erp.operator.talk.sales")
        self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""))
        self.assertEqual((result["facts"]["docnums"], world.calls[-1]), (["IV691115-001"], "restore"))
        self.assertIn("erp_operator_sources.py", world.calls)
        for break_at, words in (("operator-job", "代录交的"), ("extra-job", "只有这一件"), ("masters-unlinked", "挂回了 Express 客户编码")):
            result, world = self.go("acc.erp.operator.talk.sales", break_at)
            self.assertEqual(result["outcome"], self.runner.FAIL, break_at)
            self.assertIn(words, result["detail"], break_at)
            self.assertEqual(world.calls[-1], "restore", break_at)
        for scenario_id in ("acc.erp.operator.talk.sales", "acc.erp.operator.talk.purchase"):
            result, world = self.go(scenario_id, "leftover")
            self.assertEqual(result["outcome"], self.runner.NOT_RUN, scenario_id)
            self.assertIn("S:OLD-9", result["detail"], scenario_id)
            self.assertNotIn("erp_doc_state.py", world.calls, scenario_id)

    def test_the_1116_fix_scenarios_pass_once_fixed_and_fail_while_not(self):
        """第 5 批（修复前红）：假世界里「修好」时通过、「没修」时不通过（断言指到那个缺陷）；写账套的跑完恢复基线。"""
        for scenario_id, docs in (("acc.erp.operator.intake.purchase", 1), ("acc.erp.operator.stock.group", 1),
                                  ("acc.erp.operator.masters.link", 1), ("acc.erp.office.actor.purchase", 1)):
            result, world = self.go(scenario_id)
            self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""), scenario_id)
            self.assertEqual((len(result["facts"]["docnums"]), world.calls[-1]), (docs, "restore"), scenario_id)
        result, world = self.go("acc.erp.operator.no.computer")
        self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""))
        self.assertNotIn("restore", world.calls)   # 不写账套：不碰基线
        for scenario_id, break_at, words in (("acc.erp.operator.intake.purchase", "h3-drop", "认得这张单"),
                                             ("acc.erp.operator.intake.purchase", "h3-403", "没有权限"),
                                             ("acc.erp.operator.stock.group", "h5-unasked", "写账状态"),
                                             ("acc.erp.operator.masters.link", "unlinked", "挂上了账本里实际用的码"),
                                             ("acc.erp.operator.no.computer", "no-hook", "未连接"),
                                             ("acc.erp.office.actor.purchase", "owner-actor", "写账状态")):
            result, world = self.go(scenario_id, break_at)
            self.assertEqual(result["outcome"], self.runner.FAIL, (scenario_id, break_at, result["detail"]))
            self.assertIn(words, result["detail"], (scenario_id, break_at))
            if ".no.computer" not in scenario_id:
                self.assertEqual(world.calls[-1], "restore", (scenario_id, break_at))
        result, _ = self.go("acc.erp.operator.stock.group", "h5-unasked")
        failed = [check["name"] for step in result["steps"] for check in step["checks"] if not check["ok"]]
        for name in ("开始前问了物料组", "停下的件带可选项"):
            self.assertIn(name, failed)

    def test_the_1116_fix_scenarios_are_not_run_without_their_scene(self):
        for scenario_id, break_at, words in (("acc.erp.operator.stock.group", "h5-fit-one", "fit stock account group"),
                                             ("acc.erp.operator.no.computer", "has-office", "office computer")):
            result, _ = self.go(scenario_id, break_at)
            self.assertEqual(result["outcome"], self.runner.NOT_RUN, scenario_id)
            self.assertIn(words, result["detail"], scenario_id)
        for scenario_id, name in (("acc.erp.operator.no.computer", "PEARNLY_ACC_ERP_NOPC_USER"),
                                  ("acc.erp.office.actor.purchase", "PEARNLY_ACC_ERP_PHONE_USER")):
            os.environ.pop(name)
            result, world = self.go(scenario_id)
            self.assertEqual(result["outcome"], self.runner.NOT_RUN, scenario_id)
            self.assertIn(name, result["detail"], scenario_id)
            self.assertEqual([call for call in world.calls if call.endswith(".py")], [], scenario_id)

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

    def test_tags_differ_across_runs_on_one_commit_and_across_scenarios(self):
        # 11-16：标记曾取运行编号末尾数字（= 提交号），同一提交跑两次同一格开出同一个单号；一组共用运行编号时各格也要不同
        from pathlib import Path
        from types import SimpleNamespace
        load("acceptance_run_for_chain_tags", "scripts/acceptance/run.py")
        chain = load("acc_erp_chain_tags", "scripts/acceptance/erp_chain.py")

        def ctx(run, scenario):
            return SimpleNamespace(folder=Path("/x") / run / scenario, attempt=1, scenario={"id": scenario})

        one = chain.tag(ctx("20261116-183802-29c4187a3", "acc.erp.operator.purchase.express"))
        two = chain.tag(ctx("20261116-184511-29c4187a3", "acc.erp.operator.purchase.express"))
        other = chain.tag(ctx("20261116-183802-29c4187a3", "acc.erp.purchase.express"))
        self.assertNotEqual(one, two)
        self.assertNotEqual(one, other)
        self.assertTrue(one.isdigit())

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
