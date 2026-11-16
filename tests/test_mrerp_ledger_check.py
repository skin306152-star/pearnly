"""核账本的 MR.ERP 一侧（`pearnly_core.mrerp.ledger_check`，两产品 hooks ``pearnly_ledger_check["mrerp"]``；D 线 11-11）。

- 按推送记录里的 MR.ERP 单号（不是票号）在列表里找：一字不差才算在；``menu`` 先查，再查这份账套其余已关联的菜单；
- 走共用会话入口、不算人点的：有被踢标记不发请求、核的时候被踢就停、不清标记；接上推送留下的会话不再登录；
- gone 先关着（`GONE_ENABLED`）：打开时也只有每个候选菜单都核完整、一张都没有才算删了。
"""
import ast
import contextlib
import json
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

try:
    from pearnly_core.mrerp import ledger_check
    from test_cowork_mrerp_push import FakeMrErp, _StateRedis, execute, sales
    from test_mrerp_account_session import LEASE, site_calls
except (ImportError, unittest.SkipTest) as exc:  # 需要 Frappe 环境（bench 的 Python）
    raise unittest.SkipTest(f"needs the Frappe bench environment: {exc}")

ROOT = pathlib.Path(__file__).resolve().parents[1]
ACCOUNT = "15:1"
OPTIONS = json.dumps({ACCOUNT: {"observed_at": "2026-11-11T05:23:38", "pages": {
    "impaptran": [{"idmenu": "68", "label": "ซื้อ", "linked": True}, {"idmenu": "454", "label": "ซื้อ-ค่าใช้จ่าย", "linked": True},
                  {"idmenu": "67", "label": "ซื้อ-เดิม", "linked": False}],
    "impartran": [{"idmenu": "119", "label": "ขายเชื่อ", "linked": True}],
    "imparse": [{"idmenu": "126", "label": "ขายเงินสด", "linked": True}]}}})


def item(doc_id="H1", docnum="PN691110-948431", direction="purchase", **extra):
    return {"doc_id": doc_id, "docnum": docnum, "direction": direction, **extra}


class LedgerCheck(_StateRedis):
    def run_check(self, site, items, options=OPTIONS):
        with site_calls(site):
            return ledger_check.check(LEASE, ACCOUNT, items, menu_options=options, wait=60)

    def lists(self, site):
        return [call for call in site.calls if call[1].endswith("/allview.php")]

    def test_a_document_still_listed_is_present_by_its_mrerp_number(self):
        site = FakeMrErp(password=LEASE["password"], listed={"454": ["PN691110-948431"]})
        site.password_used = LEASE["password"]  # 与共用假会话同一份假凭据
        out = self.run_check(site, [item(menu="454")])
        self.assertEqual(out, {"H1": {"state": "present", "docnum": "PN691110-948431"}})
        self.assertEqual(len(self.lists(site)), 1)  # 带来的菜单先查，查到就停

    def test_without_the_menu_it_looks_in_every_linked_menu_of_that_direction(self):
        site = FakeMrErp(listed={"454": ["PN691110-948431"]})
        out = self.run_check(site, [item()])
        self.assertEqual(out["H1"]["state"], "present")
        menus = [path for _method, path in self.lists(site)]
        self.assertTrue(all(path == "/aptran/allview.php" for path in menus))  # 进项只查 aptran，不查没关联的 67

    def test_a_partial_number_or_the_invoice_number_does_not_count(self):
        site = FakeMrErp(listed={"454": ["PN691110-9484310"], "68": ["PGTST-6911-0004"]})
        self.assertEqual(self.run_check(site, [item(), item("H2", docnum="TST-6911-0004")]), {})

    def test_gone_stays_unknown_until_it_is_switched_on(self):
        site = FakeMrErp()
        self.assertFalse(ledger_check.GONE_ENABLED)
        self.assertEqual(self.run_check(site, [item()]), {})
        with patch.object(ledger_check, "GONE_ENABLED", True):
            self.assertEqual(self.run_check(FakeMrErp(), [item()])["H1"]["state"], "gone")

    def test_gone_needs_every_candidate_menu_readable(self):
        with patch.object(ledger_check, "GONE_ENABLED", True):
            site = FakeMrErp(unlinked_listing={"68"})
            self.assertEqual(self.run_check(site, [item()]), {})
            self.assertEqual(self.run_check(FakeMrErp(), [item()], options="{}"), {})  # 没读到过菜单：无从核起

    def test_sales_look_in_credit_and_cash_sales(self):
        site = FakeMrErp(listed={"126": ["SE691110-000001"]})
        out = self.run_check(site, [item(docnum="SE691110-000001", direction="sales")])
        self.assertEqual(out["H1"]["state"], "present")
        self.assertIn(("GET", "/arse/allview.php"), site.calls)

    def test_it_reuses_the_push_session_and_never_clears_a_kicked_mark(self):
        site = FakeMrErp(password=LEASE["password"], listed={"68": ["PG691110-000001"]})
        site.password_used = LEASE["password"]  # 与共用假会话同一份假凭据
        self.assertEqual(execute(site, [sales("c1")])["status"], "success")
        before = len(self.logins(site))
        self.run_check(site, [item(docnum="PG691110-000001")])
        self.assertEqual(len(self.logins(site)), before)  # 接上推送的会话，不再登录、不踢会计
        self.kick.mark(self.account())
        quiet = FakeMrErp(listed={"68": ["PG691110-000001"]})
        self.assertEqual(self.run_check(quiet, [item(docnum="PG691110-000001")]), {})
        self.assertEqual(quiet.calls, [])
        self.assertTrue(self.kick.state(self.account()))  # 核账本不是人点的：标记留着

    def test_a_kick_while_checking_stops_and_marks_the_account(self):
        site = FakeMrErp(password=LEASE["password"], listed={"454": ["PN691110-948431"]})
        site.password_used = LEASE["password"]  # 与共用假会话同一份假凭据
        execute(site, [sales("c2")])
        site.kick = True
        self.assertEqual(self.run_check(site, [item()]), {})
        self.assertTrue(self.kick.state(self.account()))
        self.assertEqual(self.cached(), {})


class Registered(unittest.TestCase):
    def test_both_products_add_mrerp_to_their_one_ledger_check_hook(self):
        expected = {"pearnly_cowork": "pearnly_cowork.push.mrerp.ledger_check.check",
                    "pearnly_erp_portal": "pearnly_erp_portal.mrerp.ledger_check.check"}
        for app, path in expected.items():
            with self.subTest(app=app):
                tree = ast.parse((ROOT / f"apps/{app}/{app}/hooks.py").read_text(encoding="utf-8"))
                values = [node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                          and any(getattr(target, "id", "") == "pearnly_ledger_check" for target in node.targets)]
                self.assertEqual(len(values), 1)  # 同一个字典，不另起一行
                hook = ast.literal_eval(values[0])
                self.assertEqual(hook["mrerp"], path)
                self.assertIn("express", hook)
                module, _, name = path.rpartition(".")
                source = (ROOT / "apps" / app / (module.replace(".", "/") + ".py")).read_text(encoding="utf-8")
                self.assertIn(f"def {name}(connection, account_set_key, items, *, ledger=", source)


if __name__ == "__main__":
    unittest.main()
