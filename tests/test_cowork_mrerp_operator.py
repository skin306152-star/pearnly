"""Cowork 的 MR.ERP 代录（ADR-026 第 1、2、6、8 条，第 3 步）在生产镜像里的部分。要 Frappe 环境（同 `test_cowork_mrerp_push.py`），
不连数据库、不连 Redis、不连真实 MR.ERP。开关是共用层全局常量 `contracts.MRERP_OPERATOR_ENABLED`（默认关），两种状态都锁：

- **直写执行器不接代录记录**：`runner.run` 见到代录载荷直接返回、不领；`dispatch_next` 在查询里排掉带选定电脑的记录、再逐条核载荷，
  排在前面的代录单不卡住同连接后面的直写；
- **产品声明的目标**：`operator_source.target` 常量关着一律 Express（MR.ERP 公司也是），开着时只有 MR.ERP 公司回 mrerp；
- **出载荷**（`service._prepare_mrerp`）：直写载荷与冻结目标不变、不带选定电脑；代录 = 直写同一份 + 代录标记 + ``operator.target = mrerp``
  （载荷 18）+ ``mrerp`` 块，记录带选定电脑；常量关着 / 没有块 / 没有选定电脑 / 块的连接不是这条 → 按请求无效拦下；
  `submit` 对 MR.ERP 代录只确认（等电脑来领），不排服务端执行任务；
- **开录前准备**（`push.mrerp.operator.stage`）：账号正被代录批次占着时不登录（会踢掉正在录的电脑）、回等账号；登录 / 业务 / 技术 /
  锁的错误回码、不带密码；账套名从连接目录里取；
- **同一套准备、只换最后一步**：真 `MrErpImport` 对假 MR.ERP 站点跑 `operator_draft.stage`，对方 / 商品照直写自建，单据一张都不导入；
  块里的单号与同一张票直写时导入表里的单号相同。
"""
import contextlib
import json
import socket
import types
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

try:
    import frappe
    import requests
    from pearnly_core.express_operator import contracts
    from pearnly_core.mrerp.push import operator_draft, routing, transport
    from pearnly_core.mrerp.push.models import MRERPAuthError, MRERPBusinessError, MRERPTechnicalError
    from pearnly_core.session_lock import SessionLockUnavailable
    from pearnly_cowork.assistant import operator_source
    from pearnly_cowork.push.application import service
    from pearnly_cowork.push.mrerp import operator as staging, runner
    from test_cowork_mrerp_push import BASE, CHOSEN, OWN, PUBLIC, TODAY, FakeMrErp, purchase, push, sales
except (ImportError, unittest.SkipTest) as exc:  # 需要 Frappe 环境（bench 的 Python）
    raise unittest.SkipTest(f"needs the Frappe bench environment: {exc}")

URL = "https://www.mrerp4sme.com"


def _throw(message, exc=None):
    raise (exc or frappe.ValidationError)(message)


def operator_payload(**extra):
    return contracts.for_operator({"histories": [], "mappings": {}}, {"id": "OPB-1", "index": 1, "total": 1},
                                  target=contracts.TARGET_MRERP) | extra


class Framework(unittest.TestCase):
    def setUp(self):
        frappe.local.flags = frappe._dict()
        frappe.local.message_log = []
        frappe.local.site = "testcowork.localhost"
        for module in (frappe, service):
            self.enterContext(patch.object(module, "_", lambda text, *a, **k: text))
        self.enterContext(patch.object(frappe, "throw", _throw))
        self.enterContext(patch.object(frappe, "clear_messages", lambda: None))


class Runner(Framework):
    def record(self, payload):
        return frappe._dict(name="CWP-1", status="pending", result_unknown=0, target_json='{"adapter": "mrerp"}',
                            actor="staff@x", payload_json=json.dumps(payload), workspace="WS", client="",
                            history="CWH-1", history_version=1, connection="CWEC-MR", account_key="12:1",
                            branch="legacy", binding_generation=0, connection_revision=1)

    def run_record(self, payload):
        row = self.record(payload)
        db = MagicMock()
        db.get_value.side_effect = lambda doctype, name, fields, as_dict=False: (
            row if isinstance(fields, list) else row.payload_json)
        lease = MagicMock(side_effect=RuntimeError("stop after the claim"))
        with patch.object(frappe, "db", db), patch.object(runner.ledger, "lease", lease), \
                patch.object(runner.ledger, "trusted_adapter_context", lambda *a, **k: None), \
                patch.object(frappe, "set_user", MagicMock()), patch.object(runner, "operator_holds", lambda key: False):
            try:
                runner.run("CWP-1")
            except RuntimeError:
                pass
        return lease.called

    def test_the_direct_runner_never_takes_an_operator_record(self):
        self.assertFalse(self.run_record(operator_payload()))
        self.assertTrue(self.run_record({"histories": [], "mappings": {}}))  # 直写照旧领

    def test_dispatch_next_skips_operator_records_and_the_direct_one_behind_them_flows(self):
        rows = [frappe._dict(name="CWP-OP", target_json='{"adapter": "mrerp"}', payload_json=json.dumps(operator_payload())),
                frappe._dict(name="CWP-D", target_json='{"adapter": "mrerp"}', payload_json="{}")]
        db = MagicMock()
        db.sql.return_value = rows
        dispatched = []
        with patch.object(frappe, "db", db), patch.object(runner, "dispatch", dispatched.append):
            runner.dispatch_next("CWEC-MR")
        self.assertEqual(dispatched, ["CWP-D"])
        self.assertIn("COALESCE(route_device,'')=''", " ".join(db.sql.call_args[0][0].split()))


class Target(Framework):
    def test_the_constant_off_keeps_every_company_on_express(self):
        db = MagicMock()
        db.get_value.return_value = "mrerp"
        with patch.object(frappe, "db", db):
            self.assertFalse(contracts.MRERP_OPERATOR_ENABLED)
            self.assertEqual(operator_source.target("CO-1"), "express")
            self.assertFalse(db.get_value.called)  # 关着连公司都不读
            with patch.object(contracts, "MRERP_OPERATOR_ENABLED", True):
                self.assertEqual(operator_source.target("CO-1"), "mrerp")
                db.get_value.return_value = "express"
                self.assertEqual(operator_source.target("CO-2"), "express")
                self.assertEqual(operator_source.target(""), "express")

    def test_the_product_hook_is_declared(self):
        from pearnly_cowork import hooks as cowork_hooks

        self.assertEqual(cowork_hooks.pearnly_express_operator["target"],
                         "pearnly_cowork.assistant.operator_source.target")


class Payload(Framework):
    def setUp(self):
        super().setUp()
        self.target = types.SimpleNamespace(account_set="12:1")
        self.frozen = {"adapter": "mrerp", "account_set": "12:1", "request_intent": None}
        self.payload = {"histories": [{"id": "CWH-1"}], "mappings": {"_own_tax_id": OWN}}
        self.reserved = []
        self.enterContext(patch.object(service, "mrerp_basis", lambda *a, **k: (self.target, dict(self.frozen),
                                                                                json.loads(json.dumps(self.payload)))))
        self.enterContext(patch.object(service, "_context", lambda doc, loaded, account: ("ctx", account)))
        self.enterContext(patch.object(service.ledger, "reserve", lambda **kw: self.reserved.append(kw) or {"name": "R"}))
        self.loaded = types.SimpleNamespace(doc=types.SimpleNamespace(name="CWEC-MR"))
        self.block = {"connection": "CWEC-MR", "doc_type": "purchase", "lines": []}

    def prepare(self, operator=None, route_device=""):
        return service._prepare_mrerp(types.SimpleNamespace(name="CWH-1"), self.loaded, "key", "push", "12:1", None, None,
                                      operator=operator, route_device=route_device)

    def operator(self, **extra):
        return {"id": "OPB-1", "index": 1, "total": 2, "start_at": 0, "target": "mrerp", "mrerp": self.block, **extra}

    def test_direct_payloads_and_frozen_targets_are_unchanged(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), patch.object(contracts, "MRERP_OPERATOR_ENABLED", enabled):
                self.reserved.clear()
                self.prepare()
                (found,), = [self.reserved]
                self.assertEqual(found["frozen_payload"], self.payload)
                self.assertEqual(found["frozen_target"], {**self.frozen, "request_intent": "push"})
                self.assertEqual(list(found["frozen_target"]), list(self.frozen))  # 键的先后不变
                self.assertEqual(found["route_device"], "")

    def test_an_operator_payload_carries_the_target_version_block_and_computer(self):
        with patch.object(contracts, "MRERP_OPERATOR_ENABLED", True):
            self.prepare(self.operator(), route_device="CCD-A")
        (found,) = self.reserved
        payload = found["frozen_payload"]
        self.assertEqual((payload["entry_mode"], contracts.target_of(payload), payload["mrerp"]),
                         ("operator", "mrerp", self.block))
        self.assertGreaterEqual(payload["payload_version"], 18)
        self.assertEqual(payload["operator"]["batch"], {"id": "OPB-1", "index": 1, "total": 2, "start_at": 0.0})
        self.assertEqual((payload["histories"], payload["mappings"]), (self.payload["histories"], self.payload["mappings"]))
        self.assertEqual(found["route_device"], "CCD-A")

    def test_an_operator_payload_is_refused_when_off_or_incomplete(self):
        cases = [(False, self.operator(), "CCD-A"), (True, self.operator(mrerp=None), "CCD-A"),
                 (True, self.operator(), ""), (True, self.operator(mrerp={**self.block, "connection": "CWEC-OTHER"}), "CCD-A")]
        for enabled, operator, device in cases:
            with self.subTest(enabled=enabled, device=device), patch.object(contracts, "MRERP_OPERATOR_ENABLED", enabled):
                with self.assertRaises(frappe.ValidationError):
                    self.prepare(operator, route_device=device)
        self.assertEqual(self.reserved, [])

    def test_submit_only_confirms_an_operator_record_and_never_queues_the_server_runner(self):
        calls = []
        with patch.object(service, "prepare", lambda *a, **k: {"status": "prepared", "name": "R", "revision": 1}), \
                patch.object(service.connections, "adapter_of", lambda name: "mrerp"), \
                patch.object(service, "confirm", lambda *a: calls.append("confirm") or {"status": "pending"}), \
                patch.object(service, "confirm_server", lambda *a: calls.append("server") or {"status": "pending"}):
            service.submit("CWH-1", "CWEC-MR", "k", 1, 1, operator=self.operator())
            service.submit("CWH-1", "CWEC-MR", "k", 1, 1)
        self.assertEqual(calls, ["confirm", "server"])


class Stage(Framework):
    def setUp(self):
        super().setUp()
        self.doc = types.SimpleNamespace(name="CWH-1", workspace="WS", client="")
        snapshot = {"account_sets": [{"source_id": "12:1", "label": "TEST2026",
                                      "attributes": {"company_name": "บริษัท เรา จำกัด"}}]}
        self.loaded = types.SimpleNamespace(doc=types.SimpleNamespace(name="CWEC-MR"),
                                            endpoint={"config": {"system_url": URL}},
                                            evidence=types.SimpleNamespace(snapshot=snapshot))
        self.held, self.logins, self.result = False, [], ({"CWH-1": {"docnum": "690912-000001"}}, {})
        payload = {"histories": [{"id": "CWH-1"}], "mappings": {}}
        self.enterContext(patch.object(staging.storage, "read", lambda name: self.doc))
        self.enterContext(patch.object(staging.connections, "read_for_push", lambda *a, **k: self.loaded))
        self.enterContext(patch.object(staging.connections, "credentials_for_use",
                                       lambda loaded: {"username": "test01", "password": "S3cret!"}))
        self.enterContext(patch.object(service, "mrerp_basis",
                                       lambda doc, loaded, key, lock=True: (types.SimpleNamespace(account_set="12:1"),
                                                                            {}, payload)))
        self.enterContext(patch.object(staging, "operator_holds", lambda key: self.held))
        self.commits = []
        self.enterContext(patch.object(frappe, "db", types.SimpleNamespace(commit=lambda: self.commits.append(1))))

        class Importer:
            def __init__(inner, **kwargs):
                self.logins.append(kwargs)

            def __enter__(inner):
                return inner

            def __exit__(inner, *exc):
                return False

        self.enterContext(patch.object(staging, "MrErpImport", Importer))

        def stage(importer, histories, mappings, **kwargs):
            if isinstance(self.result, Exception):
                raise self.result
            self.staged_with = kwargs
            return self.result

        self.enterContext(patch.object(staging.operator_draft, "stage", stage))

    def test_a_ready_document_gets_its_block_after_one_login_and_the_account_set_names(self):
        self.assertEqual(staging.stage("CWH-1", "CWEC-MR", "12:1"), {"block": {"docnum": "690912-000001"}})
        self.assertEqual(self.commits, [1])  # 对外请求前先提交、放开行锁
        (login,) = self.logins
        self.assertEqual((login["username"], login["comidyear"], login["seldb"]), ("test01", "12", "1"))
        self.assertEqual(self.staged_with["account_set"], {"comidyear": "12", "seldb": "1", "name": "TEST2026",
                                                           "company_name": "บริษัท เรา จำกัด"})
        self.assertEqual((self.staged_with["url"], self.staged_with["connection"]), (URL, "CWEC-MR"))

    def test_an_account_held_by_an_operator_batch_is_never_logged_into(self):
        self.held = True
        self.assertEqual(staging.stage("CWH-1", "CWEC-MR", "12:1"), {"status": "waiting", "reason": "account_busy"})
        self.assertEqual(self.logins, [])

    def test_errors_come_back_as_codes_without_the_password(self):
        for error, code in ((MRERPAuthError("bad login S3cret!"), "ERR_AUTH"), (MRERPBusinessError("x"), "ERR_BUSINESS"),
                            (MRERPTechnicalError("x"), "ERR_TECHNICAL"), (SessionLockUnavailable("x"), "ERR_SESSION_LOCK")):
            with self.subTest(code=code):
                self.result = error
                out = staging.stage("CWH-1", "CWEC-MR", "12:1")
                self.assertEqual(out, {"status": "failed", "reason": code})
                self.assertNotIn("S3cret", json.dumps(out))

    def test_a_held_document_reports_its_reasons(self):
        self.result = ({}, {"CWH-1": ["ERR_NO_SUPPLIER", "ERR_MRERP_MENU_CHOICE_REQUIRED"]})
        self.assertEqual(staging.stage("CWH-1", "CWEC-MR", "12:1"),
                         {"status": "failed", "reason": "ERR_NO_SUPPLIER; ERR_MRERP_MENU_CHOICE_REQUIRED"})
        self.result = ({}, {})
        self.assertEqual(staging.stage("CWH-1", "CWEC-MR", "12:1")["reason"], "ERR_UNKNOWN_UPLOAD_OUTCOME")


class SamePreparation(unittest.TestCase):
    """真 `MrErpImport` + 假 MR.ERP 站点：代录准备与直写同一套，只是不导入单据。"""

    def stage(self, site, histories):
        def send(session, method, url, **kwargs):
            response = site(session, method, url, **kwargs)
            for hook in session.hooks["response"]:
                hook(response)
            return response

        with patch.object(requests.Session, "request", autospec=True, side_effect=send), \
                patch.object(socket, "getaddrinfo", return_value=PUBLIC), \
                patch.object(transport, "account_lock", lambda *a, **k: contextlib.nullcontext(True)):
            importer = transport.MrErpImport(system_url=BASE, username="test01", password="test01", comidyear="15",
                                             seldb="1")
            with importer:
                blocks, failed = operator_draft.stage(importer, histories, {"_own_tax_id": OWN,
                                                                            "_mrerp_defaults": dict(CHOSEN)},
                                                      today=TODAY, url=BASE, connection="CWEC-MR",
                                                      account_set={"comidyear": "15", "seldb": "1"})
            return importer, blocks, failed

    def test_masters_are_created_as_for_direct_writes_and_no_document_is_imported(self):
        history = sales(posting_payment_manual="credit", client_id="7")
        site = FakeMrErp()
        importer, blocks, failed = self.stage(site, [history])
        self.assertEqual(failed, {})
        self.assertFalse(importer.import_sent)
        self.assertEqual(site.uploads, [("imparmas", ""), ("impstkmas", "")])  # 客户、商品自建；单据不导入
        block = blocks[history["id"]]
        self.assertEqual((block["doc_type"], block["menu"]), ("sales_credit", "119"))
        self.assertTrue(block["party_code"] and all(line["code"] and line["code"] != "123" for line in block["lines"]))
        # 同一张票直写：导入表里的单号就是块里的单号
        direct = FakeMrErp()
        push(direct, [history])
        self.assertEqual(direct.uploads[-1], ("impartran", "119"))
        self.assertEqual(direct.pending["invoices"], [block["docnum"]])
        self.assertEqual(block["docnum"], routing._gen.derive_mrerp_invoice_no(history))

    def test_each_kind_lands_on_its_own_menu_and_held_rows_carry_reasons(self):
        goods, cash = purchase(posting_item_type_manual="goods"), sales("a2", posting_payment_manual="cash")
        foreign = purchase("b9", currency="USD")
        site = FakeMrErp()
        _importer, blocks, failed = self.stage(site, [goods, cash, foreign])
        self.assertEqual({key: (b["doc_type"], b["menu"]) for key, b in blocks.items()},
                         {goods["id"]: ("purchase", "68"), cash["id"]: ("sales_cash", "126")})
        self.assertEqual(failed, {foreign["id"]: ["currency_not_thb:usd"]})
        self.assertFalse([upload for upload in site.uploads if upload[0] in ("impaptran", "impartran", "imparse")])


if __name__ == "__main__":
    unittest.main()
