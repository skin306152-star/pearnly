"""收款（ERP 收款员在 LINE / CC 录收款 → CC 桌面版写成 Express RE，第 2 步服务端）：不连库的检查（Frappe 用内存替身）。

- 纯规则（`collection.contracts`）：收款类型只列「收钱」类（NLTTEST 30 行逐行）、金额 Decimal、组合 / 折扣 / 部分、超收拒、
  按日期分配、放行版本、载荷 v20 的形状、回执余额；
- 服务（`collection.service`）：预留扣减（两人收同一张，第二人看到扣减后的余额、超了拒；同时放行时后到的被挡）、放行只认卡片
  版本 + 明确同意、撤回（没被电脑领走前）、权限拒绝（无岗位 / 别的公司 / 别人的记录）；
- 回执（`collection.push`）：成功记 RE 号并改快照、``already_collected`` 记各张余额、普通失败、写入中、撤回后撤掉作业不改状态；
- 快照（`collection.snapshot`）：旧的读取不盖新的、按客户读只动这位客户、读完没了的删；
- 网页只读页（`collection.records` / `access`）：服务端分页与总数、当前公司、搜索、只给能看收款的人、原生读权限的条件。
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/pearnly_core"), str(ROOT / "apps/pearnly_erp_portal")]
DOCTYPES = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/doctype"


# ── Frappe 替身 ───────────────────────────────────────────────────────────────


class ValidationError(Exception):
    pass


class PermissionError_(Exception):
    pass


class Row(dict):
    def __getattr__(self, key):
        if key.startswith("__"):
            raise AttributeError(key)
        return self.get(key)


def _tables(doctype):
    folder = DOCTYPES / doctype.lower().replace(" ", "_")
    path = folder / (folder.name + ".json")
    if not path.is_file():
        return {}
    spec = json.loads(path.read_text(encoding="utf-8"))
    return {field["fieldname"]: field["options"] for field in spec["fields"] if field["fieldtype"] == "Table"}


class Store:
    def __init__(self):
        self.tables = {}
        self.clock = datetime(2026, 11, 11, 10, 0, 0)
        self.cache = {}
        self.hashes = {}
        self.seq = 0

    def table(self, doctype):
        return self.tables.setdefault(doctype, {})

    def tick(self):
        self.clock += timedelta(seconds=1)
        return self.clock


S = Store()


class Doc(Row):
    def __init__(self, doctype, values=None):
        super().__init__(values or {})
        object.__setattr__(self, "flags", types.SimpleNamespace(name_set=False))
        self["doctype"] = doctype

    def __setattr__(self, key, value):
        self[key] = value

    def set(self, field, rows):
        self[field] = [Row(row) for row in rows]

    def as_dict(self):
        return dict(self)

    def insert(self, ignore_permissions=False, set_name=None):
        if set_name:
            self["name"] = set_name
        if not self.get("name"):
            S.seq += 1
            self["name"] = f"{self.doctype}-{S.seq}"
        self.setdefault("creation", S.tick())
        self.setdefault("owner", frappe.session.user)
        if self.doctype == "File":
            self.setdefault("file_url", "/private/files/" + str(self.get("file_name")))
        self._write()
        return self

    def save(self, ignore_permissions=False):
        self._write()
        return self

    def _write(self):
        parent = {key: value for key, value in self.items() if key not in _tables(self.doctype)}
        S.table(self.doctype)[self["name"]] = deepcopy(parent)
        for field, child in _tables(self.doctype).items():
            rows = S.table(child)
            for name in [name for name, row in rows.items() if row.get("parent") == self["name"]]:
                del rows[name]
            for index, row in enumerate(self.get(field) or [], start=1):
                S.seq += 1
                rows[f"{child}-{S.seq}"] = {**deepcopy(dict(row)), "name": f"{child}-{S.seq}", "parent": self["name"],
                                            "parenttype": self.doctype, "parentfield": field, "idx": index}


def _norm(value):
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return value


def _cmp(value, op, wanted):
    if op == "=":
        return value == wanted
    if op == "!=":
        return value != wanted
    if op == "in":
        return value in wanted
    if op == "not in":
        return value not in wanted
    if op == "is":
        return value not in (None, "") if wanted == "set" else value in (None, "")
    if op == "like":
        needle = str(wanted).strip("%").lower()
        return needle in str(value or "").lower()
    left, right = _norm(value), _norm(wanted)
    if left is None:
        return False
    left, right = str(left), str(right)
    return {">=": left >= right, "<=": left <= right, ">": left > right, "<": left < right}[op]


def _match(row, filters):
    items = filters.items() if isinstance(filters, dict) else [(f[0], f[1:]) for f in filters or []]
    for key, wanted in items:
        if isinstance(wanted, (list, tuple)):
            if len(wanted) == 1:
                op, operand = "=", wanted[0]
            else:
                op, operand = wanted[0], wanted[1]
        else:
            op, operand = "=", wanted
        if not _cmp(row.get(key), op, operand):
            return False
    return True


def get_all(doctype, filters=None, or_filters=None, fields=None, pluck=None, order_by=None, limit_start=0,
            limit_page_length=None, limit=None, distinct=False):
    rows = [Row(row) for row in S.table(doctype).values() if _match(row, filters)]
    if or_filters:
        rows = [row for row in rows if any(_match(row, [condition]) for condition in or_filters)]
    if fields and isinstance(fields[0], dict):
        return [Row({"total": len(rows)})]
    for part in reversed([part.strip() for part in (order_by or "creation asc").split(",") if part.strip()]):
        column, _space, direction = part.partition(" ")
        rows.sort(key=lambda row: str(_norm(row.get(column)) or ""), reverse=direction.strip() == "desc")
    count = limit or limit_page_length
    rows = rows[int(limit_start or 0):]
    if count:
        rows = rows[:count]
    if pluck:
        return [row.get(pluck) for row in rows]
    return [Row({key: row.get(key) for key in (fields or ["name"])}) for row in rows]


def get_doc(doctype, name=None, for_update=False):
    if isinstance(doctype, dict):
        values = dict(doctype)
        return Doc(values.pop("doctype"), values)
    row = S.table(doctype).get(name)
    if row is None:
        raise ValidationError("missing " + str(name))
    doc = Doc(doctype, deepcopy(row))
    for field, child in _tables(doctype).items():
        doc[field] = sorted((Row(deepcopy(item)) for item in S.table(child).values() if item.get("parent") == name),
                            key=lambda item: item["idx"])
    return doc


class DBApi:
    def get_value(self, doctype, name, fields="name", as_dict=False, for_update=False):
        if isinstance(name, dict):
            found = get_all(doctype, filters=name, fields=["name"])
            if not found:
                return None
            name = found[0]["name"]
        row = S.table(doctype).get(name)
        if row is None:
            return None
        if isinstance(fields, (list, tuple)):
            values = Row({key: row.get(key) for key in fields})
            return values if as_dict else tuple(values.values())
        return row.get(fields)

    def set_value(self, doctype, name, field, value=None, update_modified=True):
        values = field if isinstance(field, dict) else {field: value}
        S.table(doctype)[name].update(deepcopy(values))

    def exists(self, doctype, name=None):
        if isinstance(name, dict):
            return bool(get_all(doctype, filters=name))
        return name in S.table(doctype)

    def delete(self, doctype, filters):
        for name in [n for n, row in S.table(doctype).items() if _match(row, filters)]:
            del S.table(doctype)[name]

    def sql(self, query, values=(), as_dict=False, pluck=False):
        return []  # 放行时的行锁（单线程替身不用锁）

    def escape(self, value):
        return "'" + str(value).replace("'", "\\'") + "'"

    def commit(self):
        pass

    def rollback(self):
        pass


class Cache:
    def hset(self, key, field, value):
        S.hashes.setdefault(key, {})[field] = value

    def hdel(self, key, field):
        S.hashes.get(key, {}).pop(field, None)

    def hgetall(self, key):
        return dict(S.hashes.get(key, {}))

    def get_value(self, key):
        return S.cache.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        S.cache[key] = value


def _install():
    frappe = types.ModuleType("frappe")
    utils = types.ModuleType("frappe.utils")

    def get_datetime(value):
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace("T", " "))

    utils.now_datetime = lambda: S.tick()
    utils.nowdate = lambda: "2026-11-11"
    utils.get_datetime = get_datetime
    utils.getdate = lambda value: datetime.fromisoformat(str(value)[:10]).date()
    utils.convert_utc_to_system_timezone = lambda moment: (moment + timedelta(hours=7)).replace(
        tzinfo=timezone(timedelta(hours=7)))
    utils.add_days = lambda value, days: value + timedelta(days=days)
    frappe.utils = utils
    frappe.ValidationError = ValidationError
    frappe.PermissionError = PermissionError_
    frappe.AuthenticationError = type("AuthenticationError", (Exception,), {})
    frappe.DoesNotExistError = ValidationError

    def throw(message, exc=ValidationError):
        raise exc(message)

    frappe.throw = throw
    frappe._ = lambda text: text
    frappe.get_all = get_all
    frappe.get_list = get_all
    frappe.get_doc = get_doc
    frappe.db = DBApi()
    frappe.cache = Cache()
    frappe.session = types.SimpleNamespace(user="a@x")
    frappe.local = types.SimpleNamespace(site="testerp.localhost")
    frappe.flags = types.SimpleNamespace()
    frappe.parse_json = lambda value: json.loads(value) if isinstance(value, str) else value
    frappe.log_error = lambda *a, **k: None
    frappe.whitelist = lambda **kw: (lambda fn: fn)
    frappe.get_hooks = lambda name: {}
    frappe.enqueue = lambda *a, **k: None
    frappe.publish_realtime = lambda *a, **k: None
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = object
    recorder = types.ModuleType("frappe.recorder")
    recorder.do_not_record = lambda fn: fn
    sys.modules.update({"frappe": frappe, "frappe.utils": utils, "frappe.model": model,
                        "frappe.model.document": document, "frappe.recorder": recorder})
    return frappe


frappe = _install()

# 共用层里这一批用不到真实现的几处
registered = types.ModuleType("pearnly_core.originals.registered")
registered.NAME = __import__("re").compile(r"(?:CWS|ERO)-[0-9a-f]{48}\.bin")
registered.put = lambda original_id, filename, content: None  # 没配原件层：存本机
registered.filename = lambda name: name
registered.entry = lambda doc: None
sys.modules["pearnly_core.originals.registered"] = registered

exports_service = types.ModuleType("pearnly_core.exports.service")
exports_service.sync_rows = lambda: 3
sys.modules["pearnly_core.exports.service"] = exports_service

express_push = types.ModuleType("pearnly_erp_portal.express.push")
express_push.linked = lambda company: company in ("CO-1", "CO-2")
express_push.office_actor = lambda owner, company, book_key: "office@x"
sys.modules["pearnly_erp_portal.express.push"] = express_push

from pearnly_erp_portal.collection import access, contracts as C, push, records, service, snapshot  # noqa: E402
from pearnly_erp_portal.express import desktop  # noqa: E402

# ── NLTTEST 的 30 行收款类型（评估记录「两处都没有科目的类型不给选」那张表；DOCCOD 不唯一，22 有两种）────────
NLTTEST = [
    ("01", "QR", "เช็ครับ", "1130-40", ""), ("02", "CH", "รับเงินสด", "1111-00", ""),
    ("03", "TR", "เงินโอนเข้าบัญชี SCB/ออมทรัพย์", "1113-02", "S1"), ("04", "TF", "ค่าธรรมเนียมการโอน", "5390-01", ""),
    ("05", "BB", "เงินโอนเข้าบัญชี BBL/ออมทรัพย์", "1113-01", ""), ("06", "KA", "กรรมการ", "1210-00", ""),
    ("09", "DC", "ส่วนลดเงินสด", "4100-04", ""), ("10", "TX", "ภาษีถูกหัก ณ ที่จ่าย", "1154-10", ""),
    ("11", "EX", "กำไรขาดทุนจากอัตราแลกเปลี่ยน", "4200-05", ""), ("12", "TB", "เงินโอนเข้าบัญชี BBL/กระแส", "1112-01", "B1"),
    ("13", "SC", "เงินโอนเข้าบัญชี SCB/กระแส", "1112-02", "S2"), ("14", "KB", "เงินโอนเข้าบัญชี KB/CA", "1112-03", "K1"),
    ("18", "AC", "เงินทดรองจ่าย-บจ.ซี-แคร์ฯ", "1152-02", ""), ("19", "EE", "ค่าไปรษณีย์", "5240-05", ""),
    ("20", "QB", "เช็คคืน-รอเคลียร์ริ่ง", "1130-41", ""), ("21", "HA", "ค่าใช้จ่ายอื่นๆ", "5400-01", ""),
    ("22", "SA", "หัก 3% ค่าส่งเสริมการขาย", "2132-03", ""), ("22", "22", "Credit Card K.Wanwisa", "2136-02", ""),
    ("23", "CL", "เงินยืม TUCL", "1220-01", ""), ("24", "DE", "ค่าเผื่อหนี้สงสัยจะสูญ", "1130-20", ""),
    ("25", "25", "รายได้อื่นๆ", "4200-06", ""), ("26", "AP", "ค่าส่งเสริมการขาย", "5220-02", ""),
    ("27", "RP", "รายได้รับล่วงหน้า", "2133-01", ""), ("28", "SP", "GIFT VOUCHER S&P", "4200-06", ""),
    ("29", "CC", "เงินสดย่อย", "1111-50", ""), ("30", "AB", "บัญชีพัก-เงินสดรายวัน", "1990-01", ""),
    ("31", "DL", "หนี้สูญ", "5400-02", ""), ("32", "JT", "เงินค้ำประกัน-รถร่วม", "2135-03", ""),
    ("33", "NB", "เงินยืม NBP", "1220-02", ""),
]
TYPES = [{"code": code, "prefix": prefix, "name": name, "name_en": "", "bank": bank, "account": account,
          "account_issue": ""} for code, prefix, name, account, bank in NLTTEST]
CASH_ACCOUNT = "1111-00"
LISTED = {"TR", "BB", "TB", "SC", "KB"}
BOOK = {"key": "a" * 40, "path": r"\\Accserver\d$\ACCOUNT\69EXP\NLTTEST", "root": r"\\Accserver\d$\ACCOUNT",
        "company": "NLT", "label": "NLTTEST", "writers": ["PC"], "writable": True}
FACTS = {"receipt_types": TYPES, "cash_account": CASH_ACCOUNT}


class Contracts(unittest.TestCase):
    def test_only_money_receiving_types_are_offered_to_collectors_on_the_nlttest_table(self):
        found = C.catalog(TYPES, CASH_ACCOUNT)
        self.assertEqual({item["prefix"] for item in found["transfers"]}, LISTED)
        self.assertEqual(found["discount"]["prefix"], "DC")
        self.assertTrue(found["cash"])
        reasons = {item["prefix"]: item["reason"] for item in found["excluded"]}
        self.assertEqual(reasons["QR"], C.CHEQUE)
        self.assertEqual(reasons["QB"], C.CHEQUE)
        self.assertEqual(reasons["TX"], C.WITHHOLDING)
        self.assertEqual(reasons["CH"], C.CASH_BOX)  # 现金走现金那一格
        self.assertEqual(reasons["TF"], C.NOT_MONEY)  # 转账手续费：有「โอน」但不是「โอนเข้าบัญชี」
        for prefix in ("KA", "EX", "AC", "EE", "HA", "SA", "22", "CL", "DE", "25", "AP", "RP", "SP", "CC", "AB", "DL",
                       "JT", "NB"):
            self.assertEqual(reasons[prefix], C.NOT_MONEY, prefix)
        self.assertEqual(len(found["transfers"]) + 1 + len(found["excluded"]), len(TYPES))

    def test_a_listed_kind_needs_an_account_and_a_unique_prefix(self):
        rows = [dict(TYPES[2], account="", account_issue="missing"), dict(TYPES[4], account_issue="conflict"),
                dict(TYPES[9]), dict(TYPES[9], code="99", name="เงินโอนเข้าบัญชี อื่น")]
        found = C.catalog(rows, CASH_ACCOUNT)
        self.assertEqual(found["transfers"], [])
        self.assertEqual([item["reason"] for item in found["excluded"]],
                         [C.NO_ACCOUNT, C.ACCOUNT_CONFLICT, C.DUPLICATE_PREFIX, C.DUPLICATE_PREFIX])
        two = C.catalog([TYPES[6], dict(TYPES[6], prefix="D2")], CASH_ACCOUNT)
        self.assertIsNone(two["discount"])
        self.assertFalse(C.catalog(TYPES, "")["cash"])  # 账套没有现金科目：不收现金

    def test_money_is_two_place_decimal_and_never_rounded_for_the_collector(self):
        self.assertEqual(C.money("1,000.50"), Decimal("1000.50"))
        self.assertEqual(C.money(" 5 "), Decimal("5.00"))
        for bad in ("1.005", "-1", "abc", "1e400", "NaN"):
            with self.subTest(bad=bad), self.assertRaises(C.CollectionError):
                C.money(bad)

    def rows(self):
        return {"IV1": {"available": "1000.00", "remaining": "1000.00", "doc_date": "2026-10-01", "salesman": "S1"},
                "IV2": {"available": "500.00", "remaining": "800.00", "doc_date": "2026-10-05", "salesman": "S2"}}

    def test_combined_partial_and_discount_close_to_the_invoices(self):
        catalog = C.catalog(TYPES, CASH_ACCOUNT)
        checked = C.check({"invoices": [{"docnum": "IV1", "amount": "1000"}, {"docnum": "IV2", "amount": "200"}],
                           "cash": "100", "methods": [{"prefix": "BB", "amount": "1099.76"},
                                                      {"prefix": "DC", "amount": "0.24"}]}, self.rows(), catalog)
        self.assertEqual((checked["total"], checked["cash"], checked["transfer"], checked["discount"]),
                         (Decimal("1200.00"), Decimal("100.00"), Decimal("1099.76"), Decimal("0.24")))
        self.assertEqual([row["role"] for row in checked["methods"]], [C.TRANSFER, C.DISCOUNT])
        self.assertEqual(C.summaries(checked)["invoice_summary"], "IV1 1000.00; IV2 200.00")

    def test_rule_failures_carry_a_code_and_the_numbers(self):
        catalog = C.catalog(TYPES, CASH_ACCOUNT)
        cases = [
            ({"invoices": []}, C.INVOICES_EMPTY),
            ({"invoices": [{"docnum": "IV1", "amount": 1}, {"docnum": "IV1", "amount": 1}], "cash": 2}, C.INVOICE_DUPLICATE),
            ({"invoices": [{"docnum": "IV9", "amount": 1}], "cash": 1}, C.INVOICE_NOT_FOUND),
            ({"invoices": [{"docnum": "IV2", "amount": "500.01"}], "cash": "500.01"}, C.OVER_COLLECTED),
            ({"invoices": [{"docnum": "IV1", "amount": 10}], "cash": 9}, C.AMOUNT_MISMATCH),
            ({"invoices": [{"docnum": "IV1", "amount": 10}], "methods": [{"prefix": "QR", "amount": 10}]}, C.METHOD_INVALID),
            ({"invoices": [{"docnum": "IV1", "amount": 10}], "methods": [{"prefix": "BB", "amount": 5},
                                                                          {"prefix": "BB", "amount": 5}]}, C.METHOD_DUPLICATE),
            ({"invoices": [{"docnum": "IV1", "amount": 10}], "methods": [{"prefix": "DC", "amount": 10}]}, C.DISCOUNT_ONLY),
            ({"invoices": [{"docnum": "IV1", "amount": 0}], "cash": 0}, C.AMOUNT_INVALID),
        ]
        for draft, code in cases:
            with self.subTest(code=code), self.assertRaises(C.CollectionError) as caught:
                C.check(draft, self.rows(), catalog)
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(C.CollectionError) as caught:
            C.check({"invoices": [{"docnum": "IV2", "amount": "600"}], "cash": "600"}, self.rows(), catalog)
        self.assertEqual(caught.exception.detail, {"available": {"IV2": "500.00"}})
        with self.assertRaises(C.CollectionError) as caught:
            C.check({"invoices": [{"docnum": "IV1", "amount": 10}], "cash": 10}, self.rows(), C.catalog(TYPES, ""))
        self.assertEqual(caught.exception.code, C.CASH_ACCOUNT_MISSING)
        with self.assertRaises(C.CollectionError) as caught:
            C.check({"invoices": [{"docnum": "IV1", "amount": 10}], "methods": [{"prefix": "BB", "amount": 10}]},
                    self.rows(), catalog, require_vouchers=True)
        self.assertEqual(caught.exception.code, C.VOUCHER_MISSING)

    def test_a_total_is_spread_from_the_oldest_invoice_and_the_last_may_be_partial(self):
        rows = [{"docnum": "IV2", "doc_date": "2026-10-05", "available": "500"},
                {"docnum": "IV1", "doc_date": "2026-10-01", "available": "1000"}]
        self.assertEqual(C.allocate(rows, "1200"), [{"docnum": "IV1", "amount": Decimal("1000.00")},
                                                    {"docnum": "IV2", "amount": Decimal("200.00")}])
        with self.assertRaises(C.CollectionError) as caught:
            C.allocate(rows, "1500.01")
        self.assertEqual((caught.exception.code, caught.exception.detail), (C.OVER_COLLECTED, {"available": "1500.00"}))

    def test_release_takes_only_the_shown_version_with_an_explicit_consent(self):
        self.assertEqual(C.release_check(C.DRAFT, 3, 3, "line:evt-1"), "")
        self.assertEqual(C.release_check(C.DRAFT, 3, "3", "cc:msg-9"), "")
        self.assertEqual(C.release_check(C.DRAFT, 4, 3, "line:evt-1"), C.CHANGED)
        self.assertEqual(C.release_check(C.DRAFT, 3, None, "line:evt-1"), C.CHANGED)
        self.assertEqual(C.release_check(C.DRAFT, 3, 3, ""), C.CONSENT_MISSING)
        self.assertEqual(C.release_check(C.QUEUED, 3, 3, "line:evt-1"), C.NOT_DRAFT)

    def test_payload_v20_has_the_contract_fields(self):
        body = C.payload({"ref_no": "COL261111-AB12", "doc_date": "2026-11-11", "customer": "2100883", "salesman": "",
                          "department": "", "cash": Decimal("0"), "invoices": [{"docnum": "IVP42400239",
                                                                                "amount": Decimal("9972")}],
                          "methods": [{"code": "05", "prefix": "BB", "amount": Decimal("9972")}]},
                         BOOK, {"tax_id": "0105549038811", "branch": "00000"}, user="collector@x")
        self.assertEqual({key: body[key] for key in ("direction", "payload_version", "ref_no", "company", "doc_date",
                                                      "customer", "salesman", "department", "invoices", "cash",
                                                      "methods", "user")},
                         {"direction": "receipt", "payload_version": 20, "ref_no": "COL261111-AB12",
                          "company": {"tax_id": "0105549038811", "branch": "00000"}, "doc_date": "2026-11-11",
                          "customer": "2100883", "salesman": "", "department": "",
                          "invoices": [{"docnum": "IVP42400239", "amount": "9972.00"}], "cash": "0.00",
                          "methods": [{"code": "05", "prefix": "BB", "amount": "9972.00"}], "user": "collector@x"})
        self.assertEqual((body["account_dir"], body["account_set"], body["account_root"], body["account_company"]),
                         (BOOK["path"], BOOK["path"], BOOK["root"], "NLT"))
        self.assertTrue(body["respect_period_lock"])

    def test_receipt_balances_read_both_success_and_already_collected(self):
        self.assertEqual(C.balances({"invoices": [{"docnum": "IV1", "remaining_after": "0.00"},
                                                  {"docnum": "IV2", "remaining": "29996"},
                                                  {"docnum": "IV3", "remaining": "x"}, "junk"]}),
                         {"IV1": Decimal("0.00"), "IV2": Decimal("29996.00")})
        self.assertEqual(C.balances(None), {})


# ── 服务、回执、快照、网页 ──────────────────────────────────────────────────


class FakeRequests:
    def __init__(self):
        self.made = {}
        self.results = {}

    def find(self, kind, key):
        found = self.made.get(key)
        return dict(found) if found and found["status"] in ("requested", "leased", "done") else None

    def waiting(self, kind, book_key, actor=None):
        return None

    def create(self, kind, key, params, *, book_key, actor):
        self.made[key] = {"request": "R-" + key, "status": "requested", "params": params}
        return self.made[key]

    def result(self, name):
        return self.results.pop(name, b"{}"), {}

    def drop(self, name):
        for key, row in list(self.made.items()):
            if row["request"] == name:
                del self.made[key]

    def finish(self, key, items, read_at):
        self.made[key]["status"] = "done"
        self.results["R-" + key] = json.dumps({"read_at": read_at, "items": items}).encode()


class FakeJobs:
    JOB = "Pearnly CC Job"

    def __init__(self):
        self.rows = {}

    def view(self, name):
        row = self.rows[name]
        return {"job": name, "source_doctype": row["source_doctype"], "source_name": row["source_name"],
                "status": row["status"], "docnum": row.get("docnum", ""), "reason": row.get("reason", ""),
                "book_key": row["book_key"]}

    def enqueue(self, *, source_doctype, source_name, request_key, actor, book_key, payload, payload_version=1, label=""):
        name = "J-" + source_name
        self.rows[name] = {"source_doctype": source_doctype, "source_name": source_name, "actor": actor,
                           "book_key": book_key, "payload": payload, "payload_version": payload_version,
                           "status": "queued", "request_key": request_key}
        S.table(self.JOB)[name] = {"name": name, "meta_json": "{}"}
        desktop.job_changed(self.view(name))
        return self.view(name)

    def find_by_name(self, name):
        return self.view(name) if name in self.rows else None

    def cancel(self, name, reason="cancelled"):
        self.rows[name].update(status="cancelled", reason=reason)
        desktop.job_changed(self.view(name))

    def report(self, name, status, docnum="", reason="", meta=None):
        self.rows[name].update(status=status, docnum=docnum, reason=reason)
        S.table(self.JOB)[name]["meta_json"] = json.dumps(meta or {})
        desktop.job_changed(self.view(name))


ROLES = {"a@x": {"collection.write"}, "b@x": {"collection.write"}, "boss@x": {"collection.view"},
         "sales@x": {"collection.view", "sales.read"}, "nobody@x": set()}
ALLOWED = {"a@x": ["CO-1"], "b@x": ["CO-1"], "boss@x": ["CO-1", "CO-2"], "sales@x": ["CO-1"], "nobody@x": ["CO-1"],
           "other@x": ["CO-2"]}
ROLES["other@x"] = {"collection.write"}


def _has(capability, user=None):
    return capability in ROLES.get(user or frappe.session.user, set())


def _require(capability, user=None):
    if not _has(capability, user):
        raise PermissionError_("Not permitted")


class Base(unittest.TestCase):
    def setUp(self):
        global S
        S.__init__()
        self.requests, self.jobs = FakeRequests(), FakeJobs()
        frappe.session.user = "a@x"
        S.table("ERP Company").update({
            "CO-1": {"name": "CO-1", "company_name": "นิภา", "tax_id": "0105549038811", "branch": "00000"},
            "CO-2": {"name": "CO-2", "company_name": "อื่น", "tax_id": "0105549038812", "branch": "00000"}})
        S.table("ERP Customer").update({
            "C1": {"name": "C1", "express_company": "CO-1", "express_code": "2100883", "customer_name": "ร้านป้าแดง",
                   "is_active": 1},
            "C2": {"name": "C2", "express_company": "CO-1", "express_code": "2100900", "customer_name": "ร้านลุงชัย",
                   "is_active": 1},
            "C3": {"name": "C3", "express_company": "CO-2", "express_code": "2100883", "customer_name": "อื่น",
                   "is_active": 1}})
        patches = [
            mock.patch("pearnly_erp_portal.access.guard.has", side_effect=_has),
            mock.patch("pearnly_erp_portal.access.guard.require", side_effect=_require),
            mock.patch("pearnly_erp_portal.masters.company.allowed_companies",
                       side_effect=lambda user=None: list(ALLOWED.get(user or frappe.session.user, []))),
            mock.patch("pearnly_erp_portal.masters.company.active_company", side_effect=lambda user=None: "CO-1"),
            mock.patch("pearnly_erp_portal.express.target.pick",
                       side_effect=lambda row, day: {"book": BOOK, "reason": "", "options": []}),
            mock.patch("pearnly_erp_portal.express.facts.load", side_effect=lambda key: FACTS),
            mock.patch("pearnly_core.cc_desktop.books.for_company", side_effect=lambda tax, branch: [BOOK]),
            mock.patch.object(snapshot, "requests", self.requests),
            mock.patch.object(push, "jobs", self.jobs),
            mock.patch.object(records, "display_name", side_effect=lambda user: user.split("@")[0].upper()),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        # 快照：2100883 两张、2100900 一张（电脑 03:00 UTC 读的）
        snapshot.replace("CO-1", BOOK["key"], [
            {"docnum": "IV1", "rectyp": "3", "docdat": "2026-10-01", "customer": "2100883", "salesman": "S1",
             "department": "CPI", "net": "1000.00", "received": "0.00", "remaining": "1000.00"},
            {"docnum": "IV2", "rectyp": "3", "docdat": "2026-10-05", "customer": "2100883", "salesman": "S2",
             "department": "", "net": "800.00", "received": "300.00", "remaining": "500.00"},
            {"docnum": "IV3", "rectyp": "3", "docdat": "2026-10-03", "customer": "2100900", "salesman": "",
             "department": "", "net": "50.00", "received": "0.00", "remaining": "50.00"}], "2026-11-11T03:00:00+00:00")

    def as_user(self, user):
        frappe.session.user = user

    def draft(self, amount="600", user="a@x", invoices=None, methods=None, cash=None):
        self.as_user(user)
        return service.create_draft("CO-1", "2100883", invoices or [{"docnum": "IV1", "amount": amount}],
                                    cash=amount if cash is None and not methods else (cash or 0), methods=methods)

    def available(self, user="b@x"):
        self.as_user(user)
        return {row["docnum"]: row["available"] for row in service.open_invoices("CO-1", "2100883")["items"]}


class Reservations(Base):
    def test_a_second_collector_sees_the_first_ones_reservation_and_cannot_overcollect(self):
        first = self.draft("600")
        self.assertEqual(service.release(first["name"], first["version"], "line:evt-1")["status"], "released")
        self.assertEqual(self.available("b@x"), {"IV1": "400.00", "IV2": "500.00"})
        with self.assertRaises(C.CollectionError) as caught:
            self.draft("500", user="b@x")
        self.assertEqual((caught.exception.code, caught.exception.detail), (C.OVER_COLLECTED, {"available": {"IV1": "400.00"}}))
        self.assertEqual(self.draft("400", user="b@x")["status"], C.DRAFT)

    def test_two_drafts_of_the_same_invoice_the_later_release_is_stopped(self):
        first, second = self.draft("600"), self.draft("600", user="b@x")
        self.as_user("a@x")
        self.assertEqual(service.release(first["name"], first["version"], "line:evt-1")["status"], "released")
        self.as_user("b@x")
        result = service.release(second["name"], second["version"], "line:evt-2")
        self.assertEqual((result["status"], result["code"]), ("invalid", C.OVER_COLLECTED))
        self.assertEqual(frappe.db.get_value("ERP Collection", second["name"], "status"), C.DRAFT)

    def test_drafts_do_not_reserve_and_withdrawn_or_failed_ones_release_the_amount(self):
        made = self.draft("600")
        self.assertEqual(self.available()["IV1"], "1000.00")  # 草稿不占
        self.as_user("a@x")
        service.release(made["name"], made["version"], "line:evt-1")
        self.assertEqual(self.available()["IV1"], "400.00")
        self.as_user("a@x")
        service.withdraw(made["name"])
        self.assertEqual(self.available()["IV1"], "1000.00")

    def test_a_written_receipt_stops_reserving_once_the_snapshot_has_the_after_balance(self):
        made = self.draft("600")
        service.release(made["name"], made["version"], "line:evt-1")
        self.jobs.report("J-" + made["name"], "success", docnum="RE2403080",
                         meta={"invoices": [{"docnum": "IV1", "remaining_after": "400.00"}]})
        self.assertEqual(self.available()["IV1"], "400.00")  # 快照 400，不再另扣


class Release(Base):
    def test_only_the_card_version_with_consent_releases_and_the_payload_is_v20(self):
        made = self.draft("600")
        self.assertEqual(made["version"], 1)
        self.assertTrue(made["ready"])
        changed = service.update_draft(made["name"], 1, [{"docnum": "IV1", "amount": "700"}], cash="700")
        self.assertEqual(changed["version"], 2)
        self.assertEqual(service.release(made["name"], 1, "line:evt-1"), {"status": "changed", "version": 2})
        self.assertEqual(service.release(made["name"], 2, "")["code"], C.CONSENT_MISSING)
        with self.assertRaises(C.CollectionError) as caught:
            service.update_draft(made["name"], 1, [{"docnum": "IV1", "amount": "1"}], cash="1")  # 别处刚改过
        self.assertEqual(caught.exception.code, C.CHANGED)
        result = service.release(made["name"], 2, "line:evt-1")
        self.assertEqual(result["status"], "released")
        self.assertEqual(result["card"]["status"], C.QUEUED)
        job = self.jobs.rows["J-" + made["name"]]
        self.assertEqual((job["payload_version"], job["payload"]["direction"], job["payload"]["invoices"],
                          job["payload"]["cash"], job["payload"]["ref_no"], job["actor"], job["request_key"]),
                         (20, "receipt", [{"docnum": "IV1", "amount": "700.00"}], "700.00", made["name"], "office@x",
                          "erp:receipt:" + made["name"]))
        row = frappe.db.get_value("ERP Collection", made["name"], ["consent_ref", "confirmed_at", "job"], as_dict=True)
        self.assertEqual((row.consent_ref, row.job), ("line:evt-1", "J-" + made["name"]))
        self.assertIsNotNone(row.confirmed_at)
        self.assertEqual(service.release(made["name"], 2, "line:evt-1")["status"], "not_draft")  # 再点一次不重交
        self.assertEqual(len(self.jobs.rows), 1)

    def test_a_total_is_spread_and_salesman_comes_from_the_first_invoice(self):
        self.as_user("a@x")
        made = service.create_draft("CO-1", "2100883", ["IV2", "IV1"], amount="1200", cash="1200")
        self.assertEqual([(row["docnum"], row["amount"]) for row in made["invoices"]],
                         [("IV1", "1000.00"), ("IV2", "200.00")])
        self.assertEqual(frappe.db.get_value("ERP Collection", made["name"], ["salesman", "department"]), ("S1", "CPI"))

    def test_a_transfer_needs_its_voucher_before_release(self):
        made = self.draft("600", methods=[{"prefix": "BB", "amount": "600"}])
        self.assertEqual((made["ready"], made["problem"]), (False, C.VOUCHER_MISSING))
        self.assertEqual(service.release(made["name"], 1, "line:evt-1")["code"], C.VOUCHER_MISSING)
        voucher = service.store_voucher("slip.jpg", b"jpeg")
        updated = service.set_voucher(made["name"], 1, "BB", voucher["file"])
        self.assertTrue(updated["ready"])
        self.assertEqual(frappe.db.get_value("File", voucher["file"], ["attached_to_doctype", "attached_to_name"]),
                         ("ERP Collection", made["name"]))
        self.assertEqual(service.release(made["name"], 2, "cc:msg-4")["status"], "released")
        self.as_user("b@x")
        other = service.store_voucher("x.jpg", b"jpeg")
        self.as_user("a@x")
        with self.assertRaises(C.CollectionError) as caught:  # 别人的凭证不能挂
            self.draft("10", invoices=[{"docnum": "IV2", "amount": "10"}],
                       methods=[{"prefix": "BB", "amount": "10", "voucher": other["file"]}])
        self.assertEqual(caught.exception.code, service.VOUCHER_INVALID)

    def test_the_card_names_company_book_customer_amounts_and_methods(self):
        made = self.draft("600", methods=[{"prefix": "DC", "amount": "0.24"}, {"prefix": "BB", "amount": "599.76"}],
                          cash=0)
        self.assertEqual((made["company_name"], made["book"], made["customer"], made["customer_name"], made["total"],
                          made["transfer"], made["discount"]),
                         ("นิภา", "NLTTEST · " + BOOK["path"], "2100883", "ร้านป้าแดง", "600.00", "599.76", "0.24"))
        self.assertEqual([(row["prefix"], row["role"]) for row in made["methods"]], [("DC", "discount"), ("BB", "transfer")])


class Withdraw(Base):
    def test_withdraw_before_the_computer_takes_it(self):
        made = self.draft("100")
        self.assertEqual(service.withdraw(made["name"])["status"], C.WITHDRAWN)
        queued = self.draft("100")
        service.release(queued["name"], 1, "line:evt-1")
        self.assertEqual(service.withdraw(queued["name"])["status"], C.WITHDRAWN)
        self.assertEqual(self.jobs.rows["J-" + queued["name"]]["status"], "cancelled")
        self.assertEqual(frappe.db.get_value("ERP Collection", queued["name"], "status"), C.WITHDRAWN)  # 撤掉的回执不改它

    def test_no_withdraw_while_writing_or_once_written(self):
        made = self.draft("100")
        service.release(made["name"], 1, "line:evt-1")
        self.jobs.report("J-" + made["name"], "leased")
        self.assertEqual(frappe.db.get_value("ERP Collection", made["name"], "status"), C.WRITING)
        with self.assertRaises(C.CollectionError) as caught:
            service.withdraw(made["name"])
        self.assertEqual(caught.exception.code, C.NOT_WITHDRAWABLE)
        self.jobs.report("J-" + made["name"], "success", docnum="RE2403080")
        with self.assertRaises(C.CollectionError):
            service.withdraw(made["name"])
        queued = self.draft("100")
        service.release(queued["name"], 1, "line:evt-1")
        frappe.db.set_value("ERP Collection", queued["name"], "status", C.QUEUED)
        self.jobs.rows["J-" + queued["name"]]["status"] = "leased"  # 电脑刚领走、回执还没到
        with self.assertRaises(C.CollectionError) as caught:
            service.withdraw(queued["name"])
        self.assertEqual(caught.exception.code, C.BUSY)


class Permissions(Base):
    def test_no_collector_post_no_company_no_other_peoples_records(self):
        self.as_user("nobody@x")
        for call in (lambda: service.customers("CO-1"), lambda: service.open_invoices("CO-1", "2100883"),
                     lambda: service.create_draft("CO-1", "2100883", [{"docnum": "IV1", "amount": 1}], cash=1),
                     service.companies_for):
            with self.assertRaises(PermissionError_):
                call()
        self.as_user("other@x")  # 收款员，但不能用 CO-1
        with self.assertRaises(PermissionError_):
            service.create_draft("CO-1", "2100883", [{"docnum": "IV1", "amount": 1}], cash=1)
        made = self.draft("100")
        self.as_user("b@x")  # 同公司的另一位收款员
        for call in (lambda: service.card(made["name"]), lambda: service.release(made["name"], 1, "x"),
                     lambda: service.withdraw(made["name"]), lambda: service.status(made["name"]),
                     lambda: service.update_draft(made["name"], 1, [{"docnum": "IV1", "amount": 1}], cash=1)):
            with self.assertRaises(PermissionError_):
                call()
        self.as_user("boss@x")  # 能看收款：看得到，不能放行
        self.assertEqual(service.status(made["name"])["name"], made["name"])
        with self.assertRaises(PermissionError_):
            service.release(made["name"], 1, "x")

    def test_customers_search_by_code_or_name_server_side(self):
        self.as_user("a@x")
        self.assertEqual(service.customers("CO-1", "ป้า")["items"], [{"code": "2100883", "name": "ร้านป้าแดง"}])
        self.assertEqual(service.customers("CO-1", "2100")["total"], 2)
        self.assertEqual(len(service.customers("CO-1", "", limit=1)["items"]), 1)
        self.assertEqual(service.companies_for(), [{"name": "CO-1", "company_name": "นิภา"}])

    def test_methods_listed_for_the_collector(self):
        self.as_user("a@x")
        found = service.methods("CO-1")
        self.assertTrue(found["cash"])
        self.assertEqual({item["prefix"] for item in found["transfers"]}, LISTED)
        self.assertEqual(found["discount"]["prefix"], "DC")

    def test_native_read_rights_follow_people_and_companies(self):
        made = self.draft("100")
        doc = frappe.get_doc("ERP Collection", made["name"])
        self.assertTrue(access.record_permission(doc, "read", "a@x"))
        self.assertFalse(access.record_permission(doc, "write", "a@x"))
        self.assertFalse(access.record_permission(doc, "read", "b@x"))
        self.assertTrue(access.record_permission(doc, "read", "sales@x"))
        self.assertFalse(access.record_permission(doc, "read", "nobody@x"))
        self.assertEqual(access.record_query("a@x"), "`tabERP Collection`.company in ('CO-1') and "
                                                     "`tabERP Collection`.collector = 'a@x'")
        self.assertEqual(access.record_query("boss@x"), "`tabERP Collection`.company in ('CO-1', 'CO-2')")
        self.assertEqual(access.record_query("nobody@x"), "1=0")


class Receipts(Base):
    def released(self, amount="600"):
        made = self.draft(amount)
        service.release(made["name"], 1, "line:evt-1")
        return made["name"], "J-" + made["name"]

    def test_success_records_the_re_number_and_moves_the_snapshot(self):
        name, job = self.released()
        self.jobs.report(job, "success", docnum="RE2403080",
                         meta={"invoices": [{"docnum": "IV1", "remaining_after": "400.00"}]})
        row = frappe.db.get_value("ERP Collection", name, ["status", "docnum", "reason", "written_at"], as_dict=True)
        self.assertEqual((row.status, row.docnum, row.reason), (C.WRITTEN, "RE2403080", ""))
        self.assertEqual(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV1"}, "remaining"), Decimal("400.00"))
        self.assertIn("erp_ar_open:" + BOOK["key"] + ":2100883", self.requests.made)  # 再按客户读一次

    def test_already_collected_keeps_each_current_balance(self):
        name, job = self.released()
        self.jobs.report(job, "manual", reason="[ALREADY_COLLECTED] IV1 remaining 0.00",
                         meta={"invoices": [{"docnum": "IV1", "remaining": "0.00"}]})
        row = frappe.db.get_value("ERP Collection", name, ["status", "reason", "balances"], as_dict=True)
        self.assertEqual(row.status, C.FAILED)
        self.assertTrue(row.reason.startswith("[ALREADY_COLLECTED]"))
        self.assertEqual(json.loads(row.balances), {"IV1": "0.00"})
        self.assertIsNone(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV1"}, "name"))  # 已收清：快照里没了
        self.as_user("a@x")
        self.assertEqual(service.status(name)["balances"], {"IV1": "0.00"})

    def test_other_failures_and_queue_states(self):
        name, job = self.released()
        self.jobs.report(job, "queued", reason="payload_version_outdated")
        self.assertEqual(frappe.db.get_value("ERP Collection", name, ["status", "reason"]),
                         (C.QUEUED, "payload_version_outdated"))
        self.jobs.report(job, "manual", reason="[RECEIPT_TYPE_INVALID] BB")
        self.assertEqual(frappe.db.get_value("ERP Collection", name, ["status", "reason", "balances"]),
                         (C.FAILED, "[RECEIPT_TYPE_INVALID] BB", None))
        self.assertEqual(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV1"}, "remaining"), Decimal("1000.00"))

    def test_sales_documents_still_go_to_the_document_push(self):
        with mock.patch.object(express_push, "job_changed", create=True) as document_push:
            desktop.job_changed({"source_doctype": "ERP Sales Document", "source_name": "SD-1", "status": "queued"})
        document_push.assert_called_once()


class Snapshot(Base):
    def test_an_older_read_does_not_overwrite_a_newer_one(self):
        snapshot.replace("CO-1", BOOK["key"], [{"docnum": "IV1", "customer": "2100883", "net": "1000",
                                                "received": "100", "remaining": "900"}],
                         "2026-11-11T04:00:00+00:00", customer="2100883")
        self.assertEqual(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV1"}, "remaining"), Decimal("900.00"))
        self.assertIsNone(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV2"}, "name"))  # 那次读到时 IV2 已收清
        self.assertEqual(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV3"}, "remaining"), Decimal("50.00"))
        # 整本读（03:30）比 IV1 那次按客户读（04:00）旧：IV1 不动；比 IV3 的读取（03:00）新、里面没有 IV3：IV3 删
        snapshot.replace("CO-1", BOOK["key"], [{"docnum": "IV1", "customer": "2100883", "net": "1000",
                                                "received": "0", "remaining": "1000"}], "2026-11-11T03:30:00+00:00")
        self.assertEqual(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV1"}, "remaining"), Decimal("900.00"))
        self.assertIsNone(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV3"}, "name"))
        snapshot.replace("CO-1", BOOK["key"], [], "2026-11-11T05:00:00+00:00")
        self.assertEqual(S.table(snapshot.DOCTYPE), {})

    def test_a_requested_read_lands_inline_or_on_the_next_minute(self):
        self.as_user("a@x")
        state = service.open_invoices("CO-1", "2100900")["refresh"]
        self.assertEqual(state, snapshot.READING)
        key = snapshot.request_key(BOOK["key"], "2100900")
        self.requests.finish(key, [{"docnum": "IV3", "customer": "2100900", "net": "50", "received": "20",
                                    "remaining": "30"}], "2026-11-11T06:00:00+00:00")
        snapshot.advance()
        self.assertEqual(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV3"}, "remaining"), Decimal("30.00"))
        self.assertEqual(S.hashes.get(snapshot.PENDING), {})

    def test_an_ar_mark_change_refreshes_a_book_in_use_only(self):
        self.assertFalse(snapshot.marks_seen("CO-1", BOOK, {"mark": "m1"}))  # 旧版不报 ar_mark
        self.assertFalse(snapshot.marks_seen("CO-1", BOOK, {"ar_mark": "x1"}))  # 没人录过收款
        self.draft("10")
        self.assertTrue(snapshot.marks_seen("CO-1", BOOK, {"ar_mark": "x2"}))
        self.assertIn(snapshot.request_key(BOOK["key"]), self.requests.made)
        self.assertFalse(snapshot.marks_seen("CO-1", BOOK, {"ar_mark": "x2"}))  # 没变

    def test_an_unknown_request_kind_reads_as_cc_update_needed(self):
        with mock.patch.object(self.requests, "create", side_effect=frappe.ValidationError("cc_desktop.request_invalid")):
            self.assertEqual(snapshot.request("CO-1", BOOK, "2100900"), snapshot.OUTDATED)


class WebPage(Base):
    def setUp(self):
        super().setUp()
        for index in range(5):
            made = self.draft("10", user="a@x" if index % 2 else "b@x")
            if index == 4:
                service.release(made["name"], 1, "line:evt")
        self.as_user("sales@x")

    def test_the_list_pages_on_the_server_with_a_total_and_search(self):
        first = records.page({}, start=0, limit=2)
        self.assertEqual((len(first["items"]), first["total"]), (2, 5))
        rest = records.page({}, start=4, limit=2)
        self.assertEqual(len(rest["items"]), 1)
        self.assertEqual({row["collector_name"] for row in records.rows({}, limit=10)}, {"A", "B"})
        self.assertEqual(records.total({"status": C.QUEUED}), 1)
        self.assertEqual(records.total({"q": "IV1"}), 5)
        self.assertEqual(records.total({"q": "nothing"}), 0)
        names = [row["docnum"] for row in records.rows({}, sort_by="total", sort_order="desc")]
        self.assertEqual(len(names), 5)

    def test_only_people_who_view_collections_open_the_page_and_the_export(self):
        from pearnly_erp_portal.collection import output

        self.assertEqual(output.export_size({}), 4)  # 数到「当场给」的上限 + 1 就停
        detail = records.detail(records.rows({}, limit=1)[0]["name"])
        self.assertIn(detail["collector_name"], ("A", "B"))
        for user in ("a@x", "nobody@x"):
            self.as_user(user)
            with self.assertRaises(PermissionError_):
                records.page({})

    def test_the_page_is_declared_with_the_view_roles_and_no_write_entries(self):
        hooks = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/hooks.py").read_text(encoding="utf-8")
        self.assertIn("('erp.nav.collections', 'pearnly-collections', 'receipt-text', 'sales')", hooks)
        self.assertIn('"pearnly-collections": ["Export"],', hooks)
        self.assertIn('"kinds": ["express_masters", "express_marks", "express_ar_open"]', hooks)
        page = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/collections.js").read_text(encoding="utf-8")
        self.assertNotIn("set_primary_action", page)
        self.assertNotIn("limit: 0", page)


if __name__ == "__main__":
    unittest.main()
