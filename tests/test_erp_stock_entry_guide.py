"""ERP 库存四单的引导式（出库、调拨、物料调整、入库；声明 `inventory.cc_stock_guides` + 产品函数 `inventory.stock_guide`）：
用共用引擎 `assistant.guide.run` 真实现走一遍。评估：``docs/work/2026-11-19-cc-guided-stock-entries-评估.md`` 第 11 节。

不连库、不出网：替身 frappe（仓、物料、结存、单据、File 放内存表）；建单服务（`inventory.api.api_create_stock_entry`）、写账前置判定
（`express.api.preflight`）、按台（`inventory.serials`）、对话文件（`assistant.files`）、原件层（`originals.registered`）、当前公司、
权限都是替身。建单服务按 ERP 界面窗口「按行选仓」之后的载荷形状收（行上带 ``warehouse``；调拨单头一出一进）。

断言的是应有状态：
- 菜单：四项各按自己的功能出；代录不出；只对接了 Express 的公司出（MR.ERP、没接账本、还没挂公司都不出，点了也开不了）；
- 步骤：用途只出库、入库有；调入仓只调拨有；增加 / 减少只调整有；单价只入库与调整增加有；日期进「已定」（对话月份里的今天 / 月末）；
- 选项：出库 / 调拨只列有结存的「物料 · 仓库」（调拨只列调出仓的），按原码核；调入仓不能等于调出仓；仓与物料都是推荐排第一、不预选；
- 按台：出库、调整减少逐行选在库的台、入库登记新台、入库退库收回已售的台；台数不等于数量不收；交服务的行带着台；
- 账套：缺簿时问一次（照网页弹窗同一个判定）、记下后第二次不问；不缺就进「已定」；
- 附件：对话里交的文件确定时存成私有登记 File、随建单一起挂（同一键只存一次），超过上限不收；
- 确定前不建单（草稿只是字符串）；确定只建一张（双击、重放、同一键已建过回那一张）；服务拒了退回对应那一步并回滚；
  换了对话公司，引导失效；结果卡：ERP 单号 + Express 写入状态与单号。

    python3 tests/test_erp_stock_entry_guide.py
"""
import contextlib
import datetime
import importlib
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_erp_portal"):
    sys.path.insert(0, str(ROOT / "apps" / app))

HOOKS, LOGGED, STATE, TABLES = {}, [], {}, {}
frappe = types.ModuleType("frappe")
frappe._ = lambda text: text
frappe.get_hooks = lambda name: HOOKS.get(name, [])


def _get_attr(path):
    module, _dot, attr = path.rpartition(".")
    return getattr(importlib.import_module(module), attr)


frappe.get_attr = _get_attr
frappe.log_error = lambda title="", message="": LOGGED.append(title)
frappe.session = types.SimpleNamespace(user="a@x")
frappe.local = types.SimpleNamespace(site="s1")
frappe.DoesNotExistError = LookupError
frappe.PermissionError = PermissionError
frappe.ValidationError = ValueError
frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
frappe.clear_messages = lambda: None


def _throw(message, exc=ValueError):
    raise exc(message)


frappe.throw = _throw


# ── 内存表与原生查询替身（只认这里用到的几种写法）──────────────────────────────


def _conditions(filters):
    if isinstance(filters, dict):
        return [(key, *(value if isinstance(value, list) else ["=", value])) for key, value in filters.items()]
    return [tuple(item) for item in filters or ()]


def _test(row, field, op, value):
    have = row.get(field)
    if op == "=":
        return have == value
    if op == "in":
        return have in value
    if op == ">":
        return have is not None and float(have) > float(value)
    if op == "like":
        return str(value).strip("%").casefold() in str(have or "").casefold()
    raise AssertionError(op)


def get_all(doctype, filters=None, fields=None, or_filters=None, order_by=None, limit_page_length=None, pluck=None, **_kwargs):
    rows = [row for row in TABLES.get(doctype, []) if all(_test(row, *cond) for cond in _conditions(filters))]
    if or_filters:
        rows = [row for row in rows if any(_test(row, *cond) for cond in _conditions(or_filters))]
    if order_by and order_by.startswith("product_name"):
        rows.sort(key=lambda row: row.get("product_name") or "")
    rows = rows[:limit_page_length] if limit_page_length else rows
    if pluck:
        return [row[pluck] for row in rows]
    return [{key: row.get(key) for key in fields} if fields else dict(row) for row in rows]


def get_value(doctype, filters, fields=None, as_dict=False):
    filters = filters if isinstance(filters, dict) else {"name": filters}
    rows = get_all(doctype, filters=filters)
    if not rows:
        return None
    row = rows[0]
    if isinstance(fields, (list, tuple)):
        return {key: row.get(key) for key in fields} if as_dict else [row.get(key) for key in fields]
    return row.get(fields or "name")


frappe.get_all = get_all
frappe.get_list = get_all
frappe.db = types.SimpleNamespace(commit=lambda: None, rollback=lambda: STATE["rollbacks"].append(1), get_value=get_value)


def _flt(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


frappe.utils = types.SimpleNamespace(get_datetime=lambda value: value, flt=_flt, nowdate=lambda: STATE["today"],
                                     now_datetime=lambda: datetime.datetime(2026, 11, 19, 10, 5, 0))
frappe.publish_realtime = lambda *args, **kwargs: None
sys.modules["frappe"] = frappe


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


_module("pearnly_core.assistant.contracts", REALTIME_EVENT="pearnly_assistant_event")
_module("pearnly_core.assistant.write_plan", _iso=lambda moment: moment.isoformat() + "Z")
_module("pearnly_erp_portal.access.guard", has=lambda capability, user=None: capability in STATE["caps"],
        cost_visible=lambda user=None: STATE["cost_visible"])


@contextlib.contextmanager
def _acting(company, user=None):
    STATE["acting"].append(company or "CO-1")
    try:
        yield company or None
    finally:
        STATE["acting"].pop()


_module("pearnly_erp_portal.masters.company", acting=_acting,
        active_company=lambda user=None: STATE["acting"][-1] if STATE["acting"] else "CO-1")
_module("pearnly_erp_portal.inventory.warehouses",
        names_of=lambda company=None, include_inactive=True: [row["name"] for row in TABLES["ERP Warehouse"]
                                                               if row["company"] == (STATE["acting"][-1] if STATE["acting"] else "CO-1")
                                                               and (include_inactive or row["is_active"])],
        label=lambda row: " · ".join(part for part in (row.get("warehouse_code"), row.get("warehouse_name")) if part),
        prefill_for=lambda kind, company, user=None, context=None: STATE["prefills"].append((kind, company)) or {
            "warehouse": STATE["last_warehouse"], "reason": "last"})
_module("pearnly_erp_portal.inventory.documents", PURPOSES={"in": ("return", "other"), "out": ("internal_use", "damage", "other")},
        default_unit_cost=lambda product, warehouse, unit=None: {"P1": "12.50"}.get(product))
_module("pearnly_erp_portal.inventory.attachments", LIMIT=10)
_module("pearnly_erp_portal.products.service", SEARCH_FIELDS=("product_name", "internal_code"))
_module("pearnly_erp_portal.express.push", STOCK="ERP Stock Entry")


def _search_units(doctype=None, txt="", searchfield=None, start=0, page_len=20, filters=None):
    wanted = json.loads(filters) if isinstance(filters, str) else (filters or {})
    status = ["sold", "issued"] if wanted.get("status") == "returnable" else [wanted.get("status") or "in_stock"]
    rows = [row for row in UNITS if row["product"] == wanted.get("product") and row["status"] in status
            and (not wanted.get("warehouse") or row["warehouse"] == wanted["warehouse"])]
    return [(row["name"], row["serial_no"], "") for row in rows][start:start + page_len]


_module("pearnly_erp_portal.inventory.serials", RETURNABLE="returnable", IN_STOCK="in_stock", search_units=_search_units,
        tracked=lambda products, company: {name for name in products if name == "P2"})


def _preflight(doctype, payload, answers=None, name=None):
    STATE["preflights"].append((doctype, json.loads(payload), json.loads(answers) if answers else None))
    if answers:
        STATE["book_saved"] = True
        return {"needed": []}
    if STATE["needs_book"] and not STATE["book_saved"]:
        return {"needed": [{"kind": "doc_book", "subject": "stock|out", "label": "Book",
                            "options": [{"code": "JU", "name": "JU Stock"}, {"code": "JV", "name": "JV Other"}], "current": "JU"}]}
    return {"needed": []}


_module("pearnly_erp_portal.express.api", preflight=_preflight)


def _create(**kwargs):
    STATE["created"].append(kwargs)
    if STATE.get("refuse"):
        raise ValueError(STATE["refuse"])
    number = len(TABLES["ERP Stock Entry"]) + 1
    row = {"name": f"SE{number}", "doc_no": f"IS{number}", "status": "submitted", "dedupe_key": kwargs.get("dedupe_key"),
           "ledger_state": "queued", "ledger_docnum": "", "ledger_reason": ""}
    TABLES["ERP Stock Entry"].append(row)
    return {"name": row["name"], "doc_no": row["doc_no"], "status": row["status"]}


_module("pearnly_erp_portal.inventory.api", api_create_stock_entry=_create)


def _attachment(file_id):
    if file_id not in STATE["chat_files"]:
        raise ValueError("This attachment is not in this conversation.")
    STATE["read_files"].append(file_id)
    return f"{file_id}.png", b"png"


_module("pearnly_core.assistant.files", attachment=_attachment)


def _put(original_id, filename, content, **fields):
    row = {"name": f"FILE-{len(TABLES['File']) + 1}", "file_name": original_id + ".bin", "owner": frappe.session.user}
    TABLES["File"].append(row)
    return types.SimpleNamespace(name=row["name"])


_module("pearnly_core.originals.registered", put=_put)


def _original_id(direction, request_key):
    import hashlib
    import re

    STATE["original_ids"].append((direction, request_key))
    assert re.fullmatch(r"[A-Za-z0-9-]{8,64}", request_key), request_key   # 同网页上传的幂等键写法
    return "ERO-" + hashlib.sha256(f"{direction}|{request_key}".encode()).hexdigest()[:48]


_module("pearnly_erp_portal.recognition.originals", original_id=_original_id)
_module("stock_guide_test_ledger", of=lambda session_id: STATE["ledger_kind"])

from pearnly_core.assistant.guide import registry, run  # noqa: E402
from pearnly_erp_portal.inventory import cc_stock_guides, stock_guide  # noqa: E402

SESSION = "S1"
UNITS = [{"name": "U1", "serial_no": "VIN0001", "product": "P2", "warehouse": "WH1", "status": "in_stock"},
         {"name": "U2", "serial_no": "VIN0002", "product": "P2", "warehouse": "WH1", "status": "in_stock"},
         {"name": "U9", "serial_no": "VIN0009", "product": "P2", "warehouse": "WH1", "status": "sold"}]
P1_WH1, P1_WH2, P2_WH1 = "n:WH1|P1", "n:WH2|P1", "s:WH1|P2"


class Doc(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


def _codes(found):
    return {key: value["code"] for key, value in (found.get("refused") or {}).items()}


class Base(unittest.TestCase):
    def setUp(self):
        STATE.clear()
        STATE.update(caps={"stock.out", "stock.transfer", "stock.adjust", "stock.in"}, cost_visible=True, acting=[], prefills=[],
                     last_warehouse="", rollbacks=[], created=[], preflights=[], needs_book=False, book_saved=False,
                     chat_files={"F1", "F2"}, read_files=[], original_ids=[], ledger_kind="express_local", today="2026-11-19")
        TABLES.clear()
        TABLES.update({
            "ERP Warehouse": [{"name": "WH1", "warehouse_name": "Main", "warehouse_code": "01", "express_code": "", "company": "CO-1",
                               "is_active": 1},
                              {"name": "WH2", "warehouse_name": "Back", "warehouse_code": "02", "express_code": "", "company": "CO-1",
                               "is_active": 1},
                              {"name": "WH9", "warehouse_name": "Other", "warehouse_code": "09", "express_code": "", "company": "CO-2",
                               "is_active": 1}],
            "ERP Product": [{"name": "P1", "product_name": "Apple", "internal_code": "A01", "base_unit": "pcs", "unit": "pcs",
                             "is_active": 1, "serial_kind": ""},
                            {"name": "P2", "product_name": "Bike", "internal_code": "B01", "base_unit": "unit", "unit": "unit",
                             "is_active": 1, "serial_kind": "vehicle"},
                            {"name": "P3", "product_name": "Cherry", "internal_code": "C01", "base_unit": "kg", "unit": "kg",
                             "is_active": 0, "serial_kind": ""}],
            "ERP Stock Balance": [{"warehouse": "WH1", "product": "P1", "qty_on_hand": 10},
                                  {"warehouse": "WH2", "product": "P1", "qty_on_hand": 3},
                                  {"warehouse": "WH1", "product": "P2", "qty_on_hand": 2},
                                  {"warehouse": "WH2", "product": "P3", "qty_on_hand": 0}],
            "ERP Stock Entry": [], "File": [],
            "ERP Company": [{"name": "CO-1", "company_name": "Flow Trading"}],
        })
        HOOKS.clear()
        HOOKS.update({"pearnly_guides": ["pearnly_erp_portal.inventory.cc_stock_guides.GUIDES"],
                      "pearnly_guide_ledger": ["stock_guide_test_ledger.of"]})
        self.docs, self.clock = {}, [0]
        self.now = {"session_id": SESSION, "mode": "chat", "company": "CO-1", "ledger": "BOOK", "period": "2026-11",
                    "stopped_at": None}
        self.attached = {"F1", "F2"}
        self._saved = {name: getattr(run, name) for name in
                       ("_insert", "_locked", "_save", "_open_runs", "_conversation", "_announce", "_commit_files")}
        run._insert, run._locked, run._save, run._open_runs, run._conversation, run._announce = (
            self.insert, self.locked, self.save, self.open_runs, lambda session_id: dict(self.now), lambda doc: None)
        run._commit_files = lambda session_id, by_step: next(
            (key for key, ids in by_step.items() if any(i not in self.attached for i in ids)), "")
        self.addCleanup(lambda: [setattr(run, name, value) for name, value in self._saved.items()])

    def tick(self):
        self.clock[0] += 1
        return datetime.datetime(2026, 11, 19, 10, 0, self.clock[0])

    def insert(self, values):
        doc = Doc(values, name=f"RUN{len(self.docs) + 1}", modified=self.tick())
        self.docs[doc.name] = doc
        return doc

    def locked(self, name):
        doc = self.docs.get(name)
        return doc if doc and doc.user == frappe.session.user else None

    def save(self, doc):
        doc.modified = self.tick()

    def open_runs(self, session_id):
        return [d.name for d in self.docs.values() if d.session_id == session_id and d.status in run.OPEN]

    @staticmethod
    def questions(found):
        return {q["key"]: q for q in found["card"]["questions"]}

    @staticmethod
    def codes(question):
        return [row["code"] for row in question["options"]]

    def walk(self, business, *answers):
        name = run.start(SESSION, business)["run"]
        found = None
        for given in answers:
            found = run.answer(name, given)
        return name, found


class Declaration(Base):
    def test_four_menu_items_in_more_for_direct_write_on_express_with_every_path_defined(self):
        guides = {guide["id"]: guide for guide in cc_stock_guides.GUIDES}
        self.assertEqual(list(guides), ["stock_out", "stock_transfer", "stock_adjust", "stock_in"])
        self.assertEqual([guide["order"] for guide in guides.values()], [61, 62, 63, 64])
        for guide in guides.values():
            self.assertEqual((guide["icon"], guide["modes"], guide["ledgers"], guide["source"], guide["writes"]),
                             ("boxes", ["chat"], ["express_local", "express_cloud"], "form", True))
            self.assertNotIn("tool", guide)
            self.assertIn("review", guide)
            for key in ("allowed", "draft", "review", "confirm", "result", "note"):
                self.assertTrue(callable(_get_attr(guide[key])), guide[key])
            for step in guide["steps"]:
                for key in ("options", "default", "check", "apply"):
                    if key in step:
                        self.assertTrue(callable(_get_attr(step[key])), step[key])
            rules = {step["key"]: step["rule"] for step in guide["steps"]}
            # 钱和对象每次明选（不记不预选）；日期、账套后台定
            self.assertTrue(all(rules[key] == "always" for key in rules if key not in ("day", "book")), guide["id"])
            self.assertEqual((rules["day"], rules["book"]), ("auto", "auto"))
        keys = {name: [step["key"] for step in guide["steps"]] for name, guide in guides.items()}
        self.assertEqual([name for name, found in keys.items() if "purpose" in found], ["stock_out", "stock_in"])
        self.assertEqual([name for name, found in keys.items() if "to_warehouse" in found], ["stock_transfer"])
        self.assertEqual([name for name, found in keys.items() if "plus" in found and "minus" in found], ["stock_adjust"])
        self.assertEqual([name for name, found in keys.items() if "prices" in found], ["stock_adjust", "stock_in"])

    def test_each_item_follows_its_own_function_and_none_in_hands_on_entry(self):
        self.assertEqual(registry.offered(), {"chat": ["stock_out", "stock_transfer", "stock_adjust", "stock_in"], "operator": []})
        STATE["caps"] = {"stock.out"}
        self.assertEqual(registry.offered(), {"chat": ["stock_out"], "operator": []})
        STATE["caps"] = set()
        self.assertEqual(registry.offered(), {"chat": [], "operator": []})

    def test_only_companies_on_express_see_them_and_others_cannot_start_them(self):
        for kind, shown in (("express_local", True), ("express_cloud", True), ("mrerp", False), ("none", False),
                            ("no_company", False)):
            STATE["ledger_kind"] = kind
            self.assertEqual(registry.offered(registry.ledger_of(SESSION))["chat"],
                             ["stock_out", "stock_transfer", "stock_adjust", "stock_in"] if shown else [], kind)
        STATE["ledger_kind"] = "none"   # 纯 ERP 公司（没接 Express）：菜单上没有，直接开也拒
        with self.assertRaises(ValueError):
            run.start(SESSION, "stock_out")
        self.assertEqual(self.docs, {})


class Steps(Base):
    def test_first_cards_by_direction_and_the_day_is_decided(self):
        expected = {"stock_out": ["purpose", "warehouse", "rows", "note", "file"],
                    "stock_transfer": ["warehouse", "note", "file"],
                    "stock_adjust": ["warehouse", "plus", "minus", "note", "file"],
                    "stock_in": ["purpose", "warehouse", "rows", "note", "file"]}
        for business, keys in expected.items():
            found = run.start(SESSION, business)
            self.assertEqual(list(self.questions(found)), keys, business)
            decided = {item["key"]: item["value"] for item in found["card"]["decided"]}
            # 调整的增加 / 减少都可不答：还没有物料时账套判定没有要问的，先进「已定」
            self.assertEqual(decided, {"day": "2026-11-19", **({"book": {"ready": "1"}} if business == "stock_adjust" else {})}, business)

    def test_the_day_is_the_month_end_of_another_month_and_must_stay_in_that_month(self):
        self.now["period"] = "2026-10"
        name = run.start(SESSION, "stock_out")["run"]
        self.assertEqual(stock_guide.day_default({"period": "2026-10"})["value"], "2026-10-31")
        self.assertEqual(_codes(run.answer(name, {"day": "2026-11-02"})), {"day": "stock_day_period"})
        self.assertNotIn("refused", run.answer(name, {"day": "2026-10-05"}))

    def test_purposes_are_those_of_the_web_page(self):
        found = run.start(SESSION, "stock_out")
        self.assertEqual(self.codes(self.questions(found)["purpose"]), ["internal_use", "damage", "other"])
        found = run.start(SESSION, "stock_in")
        self.assertEqual(self.codes(self.questions(found)["purpose"]), ["return", "other"])


class Options(Base):
    def test_out_lists_every_active_product_by_warehouse_like_the_web_page_without_stock(self):
        STATE["last_warehouse"] = "WH2"
        found = run.start(SESSION, "stock_out")
        warehouse = self.questions(found)["warehouse"]
        # 推荐只排第一、不预选（没有 value），选项只有本公司的仓
        self.assertEqual((self.codes(warehouse), warehouse.get("preselected"), "value" in warehouse), (["WH2", "WH1"], "WH2", False))
        rows = self.questions(found)["rows"]
        # 同网页出库页的物料下拉：在架物料都能选（结存 0 也能出，服务端允许负库存），默认仓的排前面
        self.assertEqual(self.codes(rows), [P1_WH2, "s:WH2|P2", P1_WH1, P2_WH1])
        # 网页出库页不取结存：选项只有「物料 · 仓库」，没有结存
        self.assertEqual(rows["options"][0], {"code": P1_WH2, "name": "Apple · 02 · Back"})
        self.assertEqual(STATE["prefills"][0], ("stock_out", "CO-1"))
        self.assertTrue(STATE["acting"] == [])  # 每次都在本对话公司下执行，出来还原

    def test_a_product_without_stock_can_be_issued(self):
        name = run.start(SESSION, "stock_out")["run"]
        self.assertNotIn("refused", run.answer(name, {"warehouse": "WH2", "rows": [{"code": "s:WH2|P2", "amount": "1"}]}))

    def test_codes_are_checked_against_this_company_and_tracking(self):
        name = run.start(SESSION, "stock_out")["run"]
        for code in ("n:WH9|P1", "n:WH2|P3", "n:WH1|P2", "s:WH1|P1", "n:WH1|P9", "WH1|P1"):
            self.assertEqual(_codes(run.answer(name, {"rows": [{"code": code, "amount": "1"}]})), {"rows": "not offered"}, code)
        self.assertEqual(STATE["created"], [])

    def test_search_finds_by_name_or_code(self):
        ctx = {"business": "stock_out", "company": "CO-1", "answers": {"warehouse": "WH1"}}
        self.assertEqual([row["code"] for row in stock_guide.item_options(ctx, txt="b01")["options"]], [P2_WH1, "s:WH2|P2"])
        ctx["business"] = "stock_in"
        self.assertEqual([row["code"] for row in stock_guide.item_options(ctx, txt="apple")["options"]], [P1_WH1, P1_WH2])
        page = stock_guide.item_options(ctx, start=0, page_length=3)
        self.assertEqual(([row["code"] for row in page["options"]], page["more"]), ([P1_WH1, "s:WH1|P2", P1_WH2], True))

    def test_transfer_lists_only_the_source_warehouse_and_another_destination(self):
        name, found = self.walk("stock_transfer", {"warehouse": "WH1"})
        self.assertEqual(self.codes(self.questions(found)["to_warehouse"]), ["WH2"])
        self.assertEqual(self.codes(self.questions(found)["rows"]), [P1_WH1, P2_WH1])
        self.assertEqual(_codes(run.answer(name, {"to_warehouse": "WH1"})), {"to_warehouse": "not offered"})
        self.assertEqual(_codes(run.answer(name, {"rows": [{"code": P1_WH2, "amount": "1"}]})), {"rows": "not offered"})


class Walk(Base):
    def out_to_review(self, **extra):
        return self.walk("stock_out", {"warehouse": "WH1", "rows": [{"code": P1_WH1, "amount": "2"}, {"code": P1_WH2, "amount": "1"}],
                                       "purpose": "damage", "note": "broken", **extra})

    def test_review_then_confirm_creates_and_submits_one_entry_with_a_warehouse_per_line(self):
        name, found = self.out_to_review()
        self.assertEqual(found["status"], "reviewing")
        self.assertEqual(STATE["created"], [])                    # 确定之前不建单
        self.assertTrue(self.docs[name].draft.startswith("out@"))
        lines = {line["key"]: line["value"] for line in found["review"]["lines"]}
        self.assertEqual((lines["day"], lines["warehouse"], lines["purpose"], lines["note"]),
                         ("2026-11-19", "01 · Main", "stock.purpose.damage", "broken"))
        rows = found["review"]["tables"][0]["rows"]
        self.assertEqual([(row["name"], row["amount"]) for row in rows], [("Apple · 01 · Main", "2"), ("Apple · 02 · Back", "1")])
        done = run.confirm(name, found["version"])
        self.assertEqual(done["status"], "done")
        made = STATE["created"]
        self.assertEqual(len(made), 1)
        self.assertEqual({key: made[0][key] for key in ("direction", "posting_date", "warehouse", "purpose", "reason", "submit")},
                         {"direction": "out", "posting_date": "2026-11-19", "warehouse": "WH1", "purpose": "damage",
                          "reason": "broken", "submit": 1})
        self.assertEqual(made[0]["lines"], [{"product": "P1", "qty": 2.0, "warehouse": "WH1"},
                                            {"product": "P1", "qty": 1.0, "warehouse": "WH2"}])
        self.assertEqual(made[0]["dedupe_key"], f"cc:guide:{name}:{found['version']}")
        self.assertEqual(made[0]["attachments"], [])
        # 结果卡：ERP 单号；排队写 Express 时页面过一会儿再取
        self.assertEqual((done["result"]["status"], done["result"]["docnum"], done["result"]["wait"]), ("queued", "IS1", True))
        TABLES["ERP Stock Entry"][0].update(ledger_state="written", ledger_docnum="OU6900001")
        self.assertEqual({key: run.view(self.docs[name])["result"][key] for key in ("status", "docnum", "ledger_docnum")},
                         {"status": "entered", "docnum": "IS1 · OU6900001", "ledger_docnum": "OU6900001"})
        TABLES["ERP Stock Entry"][0].update(ledger_state="failed", ledger_reason="express.locked")
        self.assertEqual((run.view(self.docs[name])["result"]["status"], run.view(self.docs[name])["result"]["reason_text"]),
                         ("failed", "express.locked"))

    def test_the_same_confirm_twice_and_a_replayed_key_create_one_entry(self):
        name, found = self.out_to_review()
        run.confirm(name, found["version"])
        again = run.confirm(name, found["version"])
        self.assertEqual((len(STATE["created"]), again["result"]["docnum"]), (1, "IS1"))
        # 确定办到一半（单已建好、运行没记下）再来一次同一键：回那一张，不再建
        consent = f"guide:{name}:{found['version']}"
        ctx = {"business": "stock_out", "company": "CO-1", "run": name, "answers": {}}
        replay = stock_guide.confirm(ctx, {"day": "2026-11-19", "rows": []}, "", consent)
        self.assertEqual((replay["name"], len(STATE["created"])), ("SE1", 1))

    def test_changing_an_answer_in_review_makes_a_new_draft_and_still_no_entry(self):
        name, found = self.out_to_review()
        first = self.docs[name].draft
        found = run.answer(name, {"rows": [{"code": P1_WH1, "amount": "5"}]}, found["version"])
        self.assertEqual(found["status"], "reviewing")
        self.assertNotEqual(self.docs[name].draft, first)
        self.assertEqual(STATE["created"], [])

    def test_a_refusal_by_the_service_sends_back_to_that_step_and_rolls_back(self):
        name, found = self.walk("stock_transfer", {"warehouse": "WH1"}, {"to_warehouse": "WH2", "rows": [{"code": P1_WH1, "amount": "1"}]})
        self.assertEqual(found["status"], "reviewing")
        made = None
        STATE["refuse"] = "The destination warehouse must differ from the source warehouse"
        made = run.confirm(name, found["version"])
        self.assertEqual((made["outcome"], made["status"]), ("refused", "asking"))
        self.assertEqual(list(made["card"]["returned"]), ["to_warehouse"])
        self.assertIn("to_warehouse", self.questions(made))
        self.assertEqual(STATE["rollbacks"], [1])
        self.assertEqual(STATE["created"][0]["to_warehouse"], "WH2")
        self.assertNotIn("purpose", STATE["created"][0])

    def test_other_refusals_go_back_to_the_products(self):
        name, found = self.out_to_review()
        STATE["refuse"] = "Quantity cannot be 0"
        made = run.confirm(name, found["version"])
        self.assertEqual(list(made["card"]["returned"]), ["rows"])

    def test_a_new_conversation_company_ends_the_guide(self):
        name, _found = self.walk("stock_out", {"warehouse": "WH1"})
        self.now["company"] = "CO-2"
        found = run.answer(name, {"rows": [{"code": P1_WH1, "amount": "1"}]})
        self.assertEqual((found["status"], found["reason"]), ("expired", "company"))
        self.assertEqual(STATE["created"], [])


class Book(Base):
    def test_a_missing_book_is_asked_once_and_remembered_for_the_company(self):
        STATE["needs_book"] = True
        name, found = self.walk("stock_out", {"warehouse": "WH1", "rows": [{"code": P1_WH1, "amount": "1"}]})
        book = self.questions(found)["book"]
        self.assertEqual([(q["key"], q["input"], q.get("preselected")) for q in book["questions"]], [("q1", "choice", "JU")])
        self.assertEqual(_codes(run.answer(name, {"book": {"q1": "ZZ"}})), {"book": "not offered"})
        found = run.answer(name, {"book": {"q1": "JU"}})
        self.assertEqual(found["status"], "reviewing")
        answered = [entry for entry in STATE["preflights"] if entry[2]]
        self.assertEqual(answered[0][0], "ERP Stock Entry")
        self.assertEqual(answered[0][2], [{"kind": "doc_book", "subject": "stock|out", "value": "JU"}])
        self.assertEqual((answered[0][1]["direction"], answered[0][1]["lines"]), ("out", [{"product": "P1"}]))
        # 第二次：记住了，不再问（进「已定」）
        _name, found = self.walk("stock_out", {"warehouse": "WH1", "rows": [{"code": P1_WH1, "amount": "1"}]})
        self.assertEqual(found["status"], "reviewing")

    def test_no_missing_book_is_decided(self):
        name, found = self.walk("stock_in", {"warehouse": "WH1"})
        self.assertNotIn("book", self.questions(found))
        found = run.answer(name, {"rows": [{"code": P1_WH1, "amount": "1"}]})
        self.assertIn("book", {item["key"] for item in found["card"]["decided"]})
        self.assertIn("prices", self.questions(found))


class Units(Base):
    def test_out_picks_in_stock_units_one_per_quantity(self):
        name, found = self.walk("stock_out", {"warehouse": "WH1", "rows": [{"code": P2_WH1, "amount": "2"}]})
        units = self.questions(found)["units"]["questions"]
        self.assertEqual([(q["key"], q["input"], [row["code"] for row in q["options"]]) for q in units], [("r1", "multi", ["U1", "U2"])])
        self.assertEqual(_codes(run.answer(name, {"units": {"r1": ["U1"]}})), {"units": "stock_units_mismatch"})
        self.assertEqual(_codes(run.answer(name, {"units": {"r1": ["U9", "U1"]}})), {"units": "not offered"})
        found = run.answer(name, {"units": {"r1": ["U1", "U2"]}})
        self.assertEqual(found["status"], "reviewing")
        self.assertEqual(found["review"]["tables"][0]["rows"][0]["note"], "U1, U2")
        run.confirm(name, found["version"])
        self.assertEqual(STATE["created"][0]["lines"], [{"product": "P2", "qty": 2.0, "warehouse": "WH1",
                                                         "serials": [{"serial": "U1"}, {"serial": "U2"}]}])

    def test_in_registers_new_units_and_a_return_takes_back_sold_ones(self):
        name, found = self.walk("stock_in", {"warehouse": "WH1", "rows": [{"code": P2_WH1, "amount": "2"}]})
        units = self.questions(found)["units"]["questions"]
        # 同网页登记弹窗：每台问主号（必填）、副号、颜色
        self.assertEqual([(q["key"], q["input"], q["required"]) for q in units],
                         [("r1_1", "text", True), ("r1_1_secondary", "text", False), ("r1_1_color", "text", False),
                          ("r1_2", "text", True), ("r1_2_secondary", "text", False), ("r1_2_color", "text", False)])
        self.assertTrue(units[0]["title"].startswith("serial.vehicle.primary 1 · Bike"))
        self.assertTrue(units[1]["title"].startswith("serial.vehicle.secondary 1 · Bike"))
        self.assertTrue(units[2]["title"].startswith("serial.color 1 · Bike"))
        self.assertEqual(_codes(run.answer(name, {"units": {"r1_1": "VINX", "r1_2": "vinx"}})), {"units": "stock_units_mismatch"})
        found = run.answer(name, {"units": {"r1_1": "VIN1001", "r1_1_secondary": "ENG1", "r1_1_color": "Red", "r1_2": "VIN1002"},
                                  "prices": [{"code": P2_WH1, "amount": "800"}]})
        self.assertEqual(found["status"], "reviewing")
        run.confirm(name, found["version"])
        self.assertEqual(STATE["created"][0]["lines"][0]["serials"],
                         [{"serial_no": "VIN1001", "secondary_no": "ENG1", "color": "Red"},
                          {"serial_no": "VIN1002", "secondary_no": "", "color": ""}])
        name, found = self.walk("stock_in", {"purpose": "return", "warehouse": "WH1", "rows": [{"code": P2_WH1, "amount": "1"}]})
        units = self.questions(found)["units"]["questions"]
        self.assertEqual([[row["code"] for row in q["options"]] for q in units], [["U9"]])

    def test_adjust_registers_increases_and_picks_decreases(self):
        name, found = self.walk("stock_adjust", {"warehouse": "WH1", "plus": [{"code": P2_WH1, "amount": "1"}],
                                                 "minus": [{"code": P2_WH1, "amount": "1"}, {"code": P1_WH1, "amount": "3"}]})
        asked = self.questions(found)
        self.assertEqual([q["key"] for q in asked["plus_units"]["questions"]], ["r1_1", "r1_1_secondary", "r1_1_color"])
        self.assertEqual([(q["key"], q["input"]) for q in asked["minus_units"]["questions"]], [("r1", "multi")])
        self.assertIn("prices", asked)
        found = run.answer(name, {"plus_units": {"r1_1": "VIN2001"}, "minus_units": {"r1": ["U2"]}, "prices": [{"code": P2_WH1, "amount": "900"}]})
        self.assertEqual(found["status"], "reviewing")
        run.confirm(name, found["version"])
        self.assertEqual(STATE["created"][0]["lines"], [
            {"product": "P2", "qty": 1.0, "warehouse": "WH1", "unit_cost": "900", "unit_cost_manual": 1, "serials": [{"serial_no": "VIN2001", "secondary_no": "", "color": ""}]},
            {"product": "P2", "qty": -1.0, "warehouse": "WH1", "serials": [{"serial": "U2"}]},
            {"product": "P1", "qty": -3.0, "warehouse": "WH1"}])


class Adjust(Base):
    def test_nothing_to_adjust_is_sent_back_to_the_increase(self):
        name, found = self.walk("stock_adjust", {"warehouse": "WH1"})
        self.assertEqual(found["status"], "asking")
        self.assertEqual(found["card"]["returned"]["plus"]["reason"], "stock_no_lines")
        found = run.answer(name, {"minus": [{"code": P1_WH1, "amount": "4"}]})
        self.assertEqual(found["status"], "reviewing")
        run.confirm(name, found["version"])
        self.assertEqual(STATE["created"][0]["lines"], [{"product": "P1", "qty": -4.0, "warehouse": "WH1"}])

    def test_prices_only_for_increases_and_recommended_only_to_people_who_see_costs(self):
        name, found = self.walk("stock_adjust", {"warehouse": "WH1", "plus": [{"code": P1_WH2, "amount": "2"}]})
        prices = self.questions(found)["prices"]
        self.assertEqual((self.codes(prices), prices["preselected"], "value" in prices),
                         ([P1_WH2], [{"code": P1_WH2, "amount": "12.50"}], False))
        self.assertEqual(_codes(run.answer(name, {"prices": [{"code": P1_WH1, "amount": "3"}]})), {"prices": "not offered"})
        found = run.answer(name, {"prices": [{"code": P1_WH2, "amount": "0"}]})   # 0 收（赠品等）
        self.assertEqual(found["status"], "reviewing")
        STATE["cost_visible"] = False
        name, found = self.walk("stock_adjust", {"warehouse": "WH1", "plus": [{"code": P1_WH2, "amount": "2"}]})
        self.assertNotIn("preselected", self.questions(found)["prices"])


class Files(Base):
    def test_conversation_files_are_stored_once_and_bound_with_the_entry(self):
        name, found = self.walk("stock_in", {"warehouse": "WH1", "rows": [{"code": P1_WH1, "amount": "1"}], "file": ["F1", "F2"]},
                                {"prices": [{"code": P1_WH1, "amount": "10"}]})
        self.assertEqual(found["status"], "reviewing")
        self.assertEqual(TABLES["File"], [])                       # 确定之前不存
        run.confirm(name, found["version"])
        self.assertEqual(STATE["created"][0]["attachments"], ["FILE-1", "FILE-2"])
        self.assertEqual(STATE["created"][0]["lines"], [{"product": "P1", "qty": 1.0, "warehouse": "WH1", "unit_cost": "10",
                                                         "unit_cost_manual": 1}])
        self.assertTrue(all(row["file_name"].startswith("ERO-") and row["owner"] == "a@x" for row in TABLES["File"]))
        # 原件 id 走网页上传同一个公开入口（方向 stock_in，幂等键由这次引导 + 对话文件编号算出）
        self.assertEqual([direction for direction, _key in STATE["original_ids"]], ["stock_in", "stock_in"])
        # 同一次引导同一份文件再存一次：认出上次那条，不再读、不再存
        ctx = {"business": "stock_in", "run": name, "session": SESSION}
        self.assertEqual(stock_guide._stored_files(ctx, ["F1"]), ["FILE-1"])
        self.assertEqual((len(TABLES["File"]), STATE["read_files"]), (2, ["F1", "F2"]))

    def test_files_not_in_this_conversation_and_too_many_are_refused(self):
        name = run.start(SESSION, "stock_out")["run"]
        self.assertEqual(_codes(run.answer(name, {"file": ["F9"]})), {"file": "not in this conversation"})
        self.attached = {f"F{n}" for n in range(11)}
        self.assertEqual(_codes(run.answer(name, {"file": [f"F{n}" for n in range(11)]})), {"file": "stock_files_limit"})


if __name__ == "__main__":
    unittest.main()
