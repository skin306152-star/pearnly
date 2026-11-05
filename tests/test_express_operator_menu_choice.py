"""代录子菜单多个候选的站点一侧（评估 `docs/work/2026-11-05-cc-operator-menu-fallback-评估.md` 修法 D）：

- 共用纯函数 `pearnly_core.express_operator.menu_choice`：付款文字认现金 / 转账（各家银行泰文名与缩写、大小写、空、两类都有）、
  读回执原因里的候选、候选的值；只导入标准库；
- 代录载荷 ``operator.form_choice`` / ``operator.pay_route``：有值才写键（没有时与以前逐字节相同），MR.ERP 不带；CC 桌面版按它们
  选得出唯一一项，CC 停下时写的原因原文站点读得回候选；
- 预选卡片登记了 ``operator_form``（账套与单据组），能合并、作答；
- Cowork 选择记忆认 ``operator_form``，但不混进写账规则的记忆与公司主档页；推送准备交代录时载荷带这两项。
不连库、不依赖 Frappe。

    python3 tests/test_express_operator_menu_choice.py
"""
import ast
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))
sys.path.insert(0, str(ROOT / "companion/src"))

from pearnly_core.express_operator import contracts  # noqa: E402
from pearnly_core.express_rules.push import common  # noqa: E402
from pearnly_core.express_operator import menu_choice as M  # noqa: E402
from pearnly_core.write_precheck import contracts as card  # noqa: E402

MODULE = ROOT / "apps/pearnly_core/pearnly_core/express_operator/menu_choice.py"
SERVICE = ROOT / "apps/pearnly_cowork/pearnly_cowork/push/application/service.py"
CHOICES = ROOT / "apps/pearnly_cowork/pearnly_cowork/push/choices.py"
OPTIONS = ["ซื้อเงินสด-ทั่วไป", "ซื้อโดยโอนผ่าน TMB 2-01792-3"]
BATCH = {"id": "OP-1", "index": 1, "total": 2, "start_at": 0, "doc": "h1", "company": "CO1"}


class PayRoute(unittest.TestCase):
    def test_cash(self):
        for text in ("cash", "CASH", "Cash ", "เงินสด", "ชำระเงินสด", "จ่ายเงินสด 500 บาท", "paid by cash"):
            with self.subTest(text=text):
                self.assertEqual(M.pay_route(text), M.CASH)

    def test_transfer(self):
        for text in ("transfer", "Bank Transfer", "โอน", "โอนเงิน", "เงินโอน", "ธนาคาร", "PromptPay", "prompt pay",
                     "พร้อมเพย์", "QR", "qr code", "mobile banking"):
            with self.subTest(text=text):
                self.assertEqual(M.pay_route(text), M.TRANSFER)

    def test_every_thai_bank_name_and_short_name(self):
        for text in ("TMB", "ttb", "TTB 2-01792-3", "KBANK", "KBank", "กสิกรไทย", "ธ.กสิกร", "SCB", "ไทยพาณิชย์",
                     "BBL", "กรุงเทพ", "KTB", "กรุงไทย", "BAY", "กรุงศรีอยุธยา", "GSB", "ออมสิน", "UOB", "ยูโอบี",
                     "CIMB", "ทหารไทยธนชาต", "KKP", "เกียรตินาคินภัทร", "TISCO", "LH Bank", "แลนด์ แอนด์ เฮ้าส์",
                     "GHB", "ธ.ก.ส.", "ICBC"):
            with self.subTest(text=text):
                self.assertEqual(M.pay_route(text), M.TRANSFER)

    def test_unknown_empty_and_both_give_nothing(self):
        for texts in ((), ("",), (None,), ("   ",), ("card",), ("บัตรเครดิต",), ("credit",), ("เช็ค",),
                      ("cash", "transfer"), ("เงินสด/โอน",), ("cash", "KBANK"), ("cashier",), ("ebay",)):
            with self.subTest(texts=texts):
                self.assertEqual(M.pay_route(*texts), "")

    def test_several_parts_read_together(self):
        self.assertEqual(M.pay_route("", None, "transfer", "โอนผ่าน KBANK"), M.TRANSFER)
        self.assertEqual(M.pay_route("cash", "", None), M.CASH)

    def test_short_names_are_whole_words(self):
        # KBANK 不因含 bank 另算一次（仍是转账）；bay / scb 不撞进别的词
        self.assertEqual(M.pay_route("kbank"), M.TRANSFER)
        self.assertEqual(M.pay_route("baystreet shop"), "")
        self.assertEqual(M.pay_route("scba"), "")


class Receipt(unittest.TestCase):
    """候选走结构化回执：CC 宿主 ``host._result`` 在 menu_choice 时 meta 带 ``doc_type`` 与 ``menu_options``，worker 放进
    ``meta.operator``；共用回执净化只放行这两项（可选键，不带时结果逐字节不变），站点从推送记录存下的回执读。"""

    def test_the_meta_cc_sends_survives_the_receipt_and_reads_back(self):
        host = (ROOT / "companion/src/companion/express_operator/host.py").read_text(encoding="utf-8")
        self.assertIn("meta.update(doc_type=doc.doc_type, menu_options=list(doc.menu_options))", host)
        worker = (ROOT / "companion/src/companion/worker.py").read_text(encoding="utf-8")
        self.assertIn('meta = {"operator": {**(result.get("meta") or {})', worker)
        cc_meta = {"operator": {"batch": "OP-1", "docnum": "", "voided": [], "placeholders": [], "reports": [],
                                "seconds": 3, "reason": "menu_choice", "doc_type": "HP", "menu_options": OPTIONS}}
        clean = common.sanitize_push_meta(cc_meta)
        self.assertEqual(clean, {"operator": {"doc_type": "HP", "menu_options": OPTIONS}})  # 别的 operator 键照旧丢掉
        self.assertEqual(M.stopped_at({"ok": False, "meta": {**clean, "stage": "needs_review"}}), ("HP", OPTIONS))

    def test_receipts_without_the_keys_are_unchanged(self):
        """不带这两项的旧回执（直写、ERP、旧版 CC 的代录）：净化结果与改动前（476e55879）逐字节相同，期望值是那一版的输出。"""
        olds = (({}, "{}"),
                ({"companion_version": "1.12.14", "doc_type": "purchase", "written": True, "tables_written": ["APTRN"]},
                 '{"companion_version": "1.12.14", "doc_type": "purchase", "tables_written": ["APTRN"], "written": true}'),
                ({"docnum": "RR1", "verify": {"state": "ok", "fields": [], "party": {}}},
                 '{"docnum": "RR1", "verify": {"fields": [], "party": {}, "state": "ok"}}'),
                ({"operator": {"batch": "OP-1", "docnum": "HP1", "voided": ["HP0"], "reason": ""}}, "{}"),
                ({"operator": {"doc_type": "", "menu_options": []}}, "{}"), ({"operator": "x"}, "{}"), ({"operator": None}, "{}"),
                ({"notes": {"pos_vouchers": ["JV1"]}, "stage": "x", "created_masters": ["P1"]},
                 '{"created_masters": ["P1"], "pos_vouchers": ["JV1"], "stage": "x"}'))
        for raw, before in olds:
            with self.subTest(raw=raw):
                self.assertEqual(json.dumps(common.sanitize_push_meta(raw), sort_keys=True, ensure_ascii=False), before)

    def test_over_the_limits_is_cut_without_an_error(self):
        raw = {"operator": {"doc_type": "  hp-very-long-type ", "menu_options": ["  a  b ", "", 7, None, "x" * 500]
                            + [f"m{n}" for n in range(30)]}}
        found = common.clean_operator_meta(raw["operator"])
        self.assertEqual(found["doc_type"], "hp-very-lo")
        self.assertEqual(len(found["menu_options"]), common.OPERATOR_OPTIONS_MAX)
        self.assertEqual(found["menu_options"][:2], ["a b", "x" * common.OPERATOR_OPTION_LEN])
        self.assertEqual(common.sanitize_push_meta(raw)["operator"], found)

    def test_reading_the_stored_receipt(self):
        for response in (None, {}, {"meta": {}}, {"meta": {"operator": {"doc_type": "HP"}}},
                         {"meta": {"operator": {"menu_options": OPTIONS}}}, {"meta": {"operator": "x"}}):
            with self.subTest(response=response):
                self.assertEqual(M.stopped_at(response), ("", []))
        found = M.stopped_at({"meta": {"operator": {"doc_type": "rr", "menu_options": ["ซื้อ  A", "ซื้อ A", "ซื้อ B"]}}})
        self.assertEqual(found, ("RR", ["ซื้อ A", "ซื้อ B"]))

    def test_a_long_option_is_cut_to_what_the_store_keeps_and_cc_still_finds_it(self):
        from companion.express_operator import menu as cc_menu

        long = "ซื้อโดยโอนผ่าน ธนาคารกรุงศรีอยุธยา บัญชีออมทรัพย์ สาขาบุรีรัมย์ เลขที่ 123-4-56789-0"
        code = M.option_code(long)
        self.assertEqual(len(code), M.CHOICE_MAX)
        self.assertEqual(M.stopped_at({"meta": {"operator": {"doc_type": "HP", "menu_options": [long]}}}), ("HP", [code]))
        self.assertEqual(cc_menu.pick(["ซื้อเงินสด-ทั่วไป", long], code, ""), long)


class Payload(unittest.TestCase):
    def test_keys_only_when_there_is_a_value(self):
        plain = contracts.for_operator({"doctype": "HP", "payload_version": 3}, BATCH)
        self.assertNotIn("form_choice", plain["operator"])
        self.assertNotIn("pay_route", plain["operator"])
        self.assertEqual(plain, contracts.for_operator({"doctype": "HP", "payload_version": 3}, BATCH, form_choice="  ",
                                                       pay_route="later"))
        found = contracts.for_operator({"doctype": "HP"}, BATCH, form_choice=" ซื้อโดยโอนผ่าน  TMB ", pay_route="Transfer")
        self.assertEqual((found["operator"]["form_choice"], found["operator"]["pay_route"]),
                         ("ซื้อโดยโอนผ่าน TMB", "transfer"))
        self.assertEqual(found["payload_version"], contracts.OPERATOR_PAYLOAD_VERSION)  # 不升版本

    def test_mrerp_does_not_carry_them(self):
        found = contracts.for_operator({}, BATCH, target=contracts.TARGET_MRERP, form_choice="x", pay_route="cash")
        self.assertNotIn("form_choice", found["operator"])
        self.assertNotIn("pay_route", found["operator"])

    def test_cc_reads_them_and_picks_one_option(self):
        from companion.express_operator import menu as cc_menu
        from companion.express_operator.draft import from_payload

        # 销项单种（进项会顺带导入直写的 DBF 模块，离线环境没有那个依赖）：读这两项与单种无关
        payload = contracts.for_operator({"doctype": "IV", "docdate_be": "691105", "customer": {"code": "C1"}, "items": []},
                                         BATCH, pay_route=M.pay_route("โอนผ่าน TMB"))
        draft = from_payload("h1", payload)
        self.assertEqual((draft.form_choice, draft.pay_route), ("", "transfer"))
        self.assertEqual(cc_menu.pick(OPTIONS, draft.form_choice, draft.pay_route), OPTIONS[1])
        self.assertEqual(cc_menu.pick(OPTIONS, "", M.pay_route("เงินสด")), OPTIONS[0])
        remembered = contracts.for_operator({"doctype": "IV", "docdate_be": "691105", "items": []}, BATCH,
                                            form_choice=M.option_code(OPTIONS[0]), pay_route="transfer")
        draft = from_payload("h1", remembered)
        self.assertEqual(cc_menu.pick(OPTIONS, draft.form_choice, draft.pay_route), OPTIONS[0])  # 记住的优先


class Card(unittest.TestCase):
    def item(self, value="", source=""):
        return {"kind": M.KIND, "subject": "HP", "label": "HP", "value": value, "source": source,
                "options": [{"code": M.option_code(o), "name": ""} for o in OPTIONS]}

    def test_operator_form_is_in_the_book_group_and_answers_expand(self):
        self.assertEqual(card.KIND_GROUPS[M.KIND], card.BOOK)
        merged = card.merge([{"doc_id": "h1", "items": [self.item()]}, {"doc_id": "h2", "items": [self.item()]}])
        [item] = card.items_of(merged)
        self.assertEqual((item["group"], item["state"], item["docs"]), (card.BOOK, card.OPEN, ["h1", "h2"]))
        expanded, errors = card.expand(merged, [{"key": item["key"], "value": OPTIONS[1]}])
        self.assertEqual(errors, [])
        self.assertEqual(expanded["h2"], [{"kind": M.KIND, "subject": "HP", "value": OPTIONS[1], "label": "HP",
                                           "remember": True}])
        _e, errors = card.expand(merged, [{"key": item["key"], "value": "ซื้อเงินเชื่อ"}])
        self.assertEqual([e["code"] for e in errors], [card.ERR_NOT_OFFERED])

    def test_a_remembered_option_is_decided(self):
        merged = card.merge([{"doc_id": "h1", "items": [self.item(OPTIONS[0], card.REMEMBERED)]}])
        self.assertEqual(card.items_of(merged)[0]["state"], card.DECIDED)


class Boundary(unittest.TestCase):
    def test_pure_and_shared(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        names = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        names |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        self.assertLessEqual(names, {"__future__", "re", "typing", "unicodedata"})
        self.assertIn("多产品共用", ast.get_docstring(tree))


def _function(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(path.read_text(encoding="utf-8"), node)


class CoworkPayload(unittest.TestCase):
    def test_prepare_hands_both_to_the_operator_payload(self):
        body = _function(SERVICE, "prepare")
        block = body.split('if operator and prepared.disposition == "ready":', 1)[1].split("row = ledger.reserve", 1)[0]
        self.assertIn("choice_store.operator_form(loaded.doc.name, target.account_set,", block)
        self.assertIn('payload.get("doctype")', block)
        self.assertIn("pay_route=operator_pay_route(fields)", block)

    def test_pay_route_reads_the_payment_method_and_cash_tendered(self):
        namespace = {}
        exec(compile(ast.parse(_function(SERVICE, "operator_pay_route")), str(SERVICE), "exec"), namespace)
        route = namespace["operator_pay_route"]
        self.assertEqual(route({"payment_method": "transfer"}), M.TRANSFER)
        self.assertEqual(route({"payment_method": "qr"}), M.TRANSFER)
        self.assertEqual(route({"payment_method": "cash"}), M.CASH)
        self.assertEqual(route({"payment_method": "", "cash_amount": "1,000.00"}), M.CASH)
        self.assertEqual(route({"change_amount": "20"}), M.CASH)
        self.assertEqual(route({"payment_method": "transfer", "cash_amount": "500"}), "")  # 两类都有：不猜
        self.assertEqual(route({"payment_method": "card", "cash_amount": "x"}), "")
        self.assertEqual(route({"posting_payment_manual": "cash"}), "")  # 现购 / 赊购不是付款途径
        self.assertEqual(route(None), "")


ROWS = [{"kind": "payment", "subject": "purchase|0105555000011", "value": "cash"},
        {"kind": "account", "subject": "purchase|0105555000011", "value": "5100"},
        {"kind": M.KIND, "subject": "HP", "value": OPTIONS[1]}]


class CoworkStore(unittest.TestCase):
    def load(self, rows=(), table=None):
        calls = []

        def sql(query, values=(), as_dict=False):
            query = " ".join(query.split())
            calls.append((query, values))
            if table is None:
                return list(rows)
            found = [row for row in table if "kind!=%s" not in query or row["kind"] != values[2]]
            return [types.SimpleNamespace(**row) for row in found]

        frappe = types.ModuleType("frappe")
        frappe.__dict__.update(_=lambda t: t, throw=lambda *a, **k: (_ for _ in ()).throw(ValueError(a[0])),
                               PermissionError=PermissionError, db=types.SimpleNamespace(sql=sql))
        utils = types.ModuleType("frappe.utils")
        utils.now_datetime = lambda: None
        automation = types.SimpleNamespace(OVERRIDE_DAYS=60, OVERRIDE_KINDS=("account", "payment"))
        modules = {"frappe": frappe, "frappe.utils": utils, "pearnly_cowork": types.SimpleNamespace(),
                   "pearnly_cowork.push": types.SimpleNamespace(), "pearnly_cowork.push.express": types.SimpleNamespace(),
                   "pearnly_cowork.push.express.automation": automation}
        patcher = mock.patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        spec = importlib.util.spec_from_file_location("cowork_choices_under_test", CHOICES)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, calls

    def test_the_store_takes_the_kind_and_the_doctype_offers_it(self):
        module, _calls = self.load()
        self.assertIn(M.KIND, module.KINDS)
        meta = json.loads((ROOT / "apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_express_choice/"
                                  "cowork_express_choice.json").read_text(encoding="utf-8"))
        kind = next(field for field in meta["fields"] if field["fieldname"] == "kind")
        self.assertEqual(set(kind["options"].split("\n")), set(module.KINDS))

    def test_operator_form_reads_one_value_per_book_and_doc_type(self):
        module, calls = self.load(rows=[(OPTIONS[1],)])
        self.assertEqual(module.operator_form("C1", "P:\\69EXP\\T9", "hp"), OPTIONS[1])
        query, values = calls[-1]
        self.assertIn("kind=%s AND subject=%s", query)
        self.assertEqual((values[0], values[2], values[3]), ("C1", M.KIND, "HP"))
        self.assertEqual(module.operator_form("C1", "", "HP"), "")
        self.assertEqual(module.operator_form("C1", "P:\\69EXP\\T9", ""), "")

    def test_the_company_page_lists_it(self):
        module, calls = self.load()
        module.of_sets("C1", ["P:\\69EXP\\T9"])
        self.assertNotIn("kind!=", calls[-1][0])  # 公司主档页「记住的选择」列出、可删可改

    def test_push_decisions_and_the_automatic_fingerprint_do_not_see_it(self):
        """推送判定（`decide` 的 ``remembered``）与自动推指纹（`automation.rules.fingerprint`）都只吃 `remembered`：库里多一条
        代录子菜单，它的输出与指纹逐字相同；不排除的话指纹就会变（证明这条测试管用）。"""
        rules = _load_rules()
        with_menu, _calls = self.load(table=ROWS)
        without, _calls = self.load(table=ROWS[:2])
        got, plain = with_menu.remembered("C1", "T9"), without.remembered("C1", "T9")
        self.assertEqual(got, plain)
        self.assertNotIn(M.KIND, got)
        self.assertEqual(rules.fingerprint(got, ()), rules.fingerprint(plain, ()))
        leaked = {**plain, M.KIND: {"HP": OPTIONS[1]}}
        self.assertNotEqual(rules.fingerprint(leaked, ()), rules.fingerprint(plain, ()))

    def test_menu_stops_read_the_latest_candidates_per_book_and_doc_type(self):
        def receipt(doc_type, options):
            return json.dumps({"ok": False, "meta": {"operator": {"doc_type": doc_type, "menu_options": options},
                                                     "stage": "needs_review"}}, ensure_ascii=False)

        rows = [types.SimpleNamespace(account_key="p:\\69exp\\t9", response_json=receipt("HP", OPTIONS)),
                types.SimpleNamespace(account_key="P:\\69EXP\\T9", response_json=receipt("HP", ["old"])),
                types.SimpleNamespace(account_key="P:\\OTHER", response_json=receipt("HP", ["x", "y"])),
                types.SimpleNamespace(account_key="P:\\69EXP\\T9", response_json="{broken"),
                types.SimpleNamespace(account_key="P:\\69EXP\\T9", response_json=receipt("RR", ["a", "b"]))]
        module, calls = self.load(rows=rows)
        found = module.menu_stops("C1", ["P:\\69EXP\\T9"])
        key = module.normalize_express_account_key("P:\\69EXP\\T9")
        self.assertEqual(found, {(key, "HP"): OPTIONS, (key, "RR"): ["a", "b"]})  # 新 → 旧，每种单取最近一次
        query, values = calls[-1]
        self.assertIn("status='manual' AND reason LIKE %s ORDER BY created_at DESC LIMIT %s", query)
        self.assertEqual(values, ("C1", "[OPERATOR\\_MENU\\_CHOICE]%", module.MENU_SCAN_MAX))
        self.assertEqual(module.menu_stops("C1", []), {})


def _load_rules():
    spec = importlib.util.spec_from_file_location(
        "cowork_automation_rules_under_test", ROOT / "apps/pearnly_cowork/pearnly_cowork/push/automation/rules.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CompanyPage(unittest.TestCase):
    """公司主档页「记住的选择」：代录子菜单一行（不能设为全公司），可选项是这份账套这种单最近一次停下的候选，没有候选只能删。"""

    def load(self):
        source = (ROOT / "apps/pearnly_cowork/pearnly_cowork/companies/choices.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {"encode", "decode", "group_of", "subject_name", "tax_in", "merge_express", "menu_options"}
        body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
        import base64
        import binascii

        frappe = types.SimpleNamespace(throw=lambda *a, **k: (_ for _ in ()).throw(ValueError(a[0])), DoesNotExistError=LookupError)
        namespace = {"base64": base64, "binascii": binascii, "json": json, "frappe": frappe, "_": lambda t: t,
                     "EDITABLE": {"payment": ("cash", "credit")},
                     "express_choices": types.SimpleNamespace(OPERATOR_FORM=M.KIND)}
        exec(compile(ast.Module(body=body, type_ignores=[]), "choices.py", "exec"), namespace)
        return types.SimpleNamespace(**namespace)

    def test_a_menu_row_offers_the_latest_candidates_with_the_remembered_one_first(self):
        page = self.load()
        rows = [{"kind": M.KIND, "subject": "HP", "label": "HP", "value": OPTIONS[1], "account_set": "p:\\69exp\\t9",
                 "chosen_by": "a@x", "chosen_at": "2026-11-05"},
                {"kind": "payment", "subject": "purchase|0105555000011", "label": "ร้าน A", "value": "cash",
                 "account_set": "p:\\69exp\\t9"}]
        found = page.merge_express(rows, "C1", {})
        menu = next(row for row in found if row["kind"] == M.KIND)
        self.assertEqual((menu["subject_label"], menu["value"], menu["promotable"]), ("HP", OPTIONS[1], False))
        self.assertTrue(next(row for row in found if row["kind"] == "payment")["promotable"])
        page.menu_options(found, rows, {("p:\\69exp\\t9", "HP"): OPTIONS})
        self.assertEqual((menu["options"], menu["editable"]), ([OPTIONS[1], OPTIONS[0]], True))
        self.assertNotIn("options", next(row for row in found if row["kind"] == "payment"))
        page.menu_options(found, rows, {})
        self.assertEqual((menu["options"], menu["editable"]), ([], False))  # 没有候选：只能删

    def test_forget_set_and_promote_routes(self):
        source = (ROOT / "apps/pearnly_cowork/pearnly_cowork/companies/choices.py").read_text(encoding="utf-8")
        forget = _function(ROOT / "apps/pearnly_cowork/pearnly_cowork/companies/choices.py", "forget")
        self.assertIn('parts[2] in express_choices.KINDS', forget)  # 删照账套记忆那条路
        set_value = _function(ROOT / "apps/pearnly_cowork/pearnly_cowork/companies/choices.py", "set_value")
        self.assertIn("parts[2] == express_choices.OPERATOR_FORM", set_value)
        menu = _function(ROOT / "apps/pearnly_cowork/pearnly_cowork/companies/choices.py", "_set_menu")
        self.assertIn("express_choices.menu_stops(connection, keys)", menu)
        self.assertIn('frappe.throw(_("cowork.company.choice_not_editable"))', menu)  # 不在候选里的值拒收
        promote = _function(ROOT / "apps/pearnly_cowork/pearnly_cowork/companies/choices.py", "promote")
        self.assertIn('parts[2] in KINDS', promote)  # 只认写账规则的类：代录子菜单不能设为全公司
        self.assertIn("from pearnly_core.express_rules.push.choices import KINDS", source)


if __name__ == "__main__":
    unittest.main()
