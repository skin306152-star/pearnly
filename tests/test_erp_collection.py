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
import re
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
        self.locked = []
        self.after_commit, self.after_rollback = [], []
        self.serial = []  # 拿过的账套串行锁（`snapshot._hold`）：[(键, 动作)]

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


def _ci(value):
    """库的排序规则 utf8mb4_unicode_ci 的近似：不分大小写，不换行空格与空格相等（11-15 真机根因之一）。"""
    return str(value).replace("\xa0", " ").casefold()


def _cmp(value, op, wanted):
    if op == "=":
        if isinstance(value, str) and isinstance(wanted, str):
            return _ci(value) == _ci(wanted)
        return value == wanted
    if op == "!=":
        return value != wanted
    if op in ("in", "not in"):  # 同 = 按库的排序规则比
        found = any(_cmp(value, "=", item) for item in wanted)
        return found if op == "in" else not found
    if op == "is":
        return value not in (None, "") if wanted == "set" else value in (None, "")
    if op == "like":  # % 是通配（转义的 \\% \\_ 按字面），整串比
        import re

        parts = [re.escape(_ci(part.replace("\\%", "%").replace("\\_", "_")))
                 for part in re.split(r"(?<!\\)%", str(wanted))]
        return re.fullmatch(".*".join(parts), _ci(value or ""), re.S) is not None
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


class Flags(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, key):
        self.pop(key, None)


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
        if "for update" in query.lower():
            S.locked.append(query.split("`tab", 1)[-1].split("`", 1)[0])  # 放行时的行锁：记下锁的顺序（单线程替身不用锁）
        return []

    def escape(self, value):
        return "'" + str(value).replace("'", "\\'") + "'"

    # 提交 / 回滚后的回调（`snapshot` 的串行锁、发起记录在提交后清，`requests.take` 提交后删文件）：同 Frappe，提交跑 after_commit、
    # 清掉 after_rollback；回滚反过来
    after_commit = types.SimpleNamespace(add=lambda function: S.after_commit.append(function))
    after_rollback = types.SimpleNamespace(add=lambda function: S.after_rollback.append(function))

    def savepoint(self, point):
        pass

    def commit(self):
        S.after_rollback.clear()
        while S.after_commit:
            S.after_commit.pop(0)()

    def rollback(self, save_point=None):
        if save_point:
            return
        S.after_commit.clear()
        while S.after_rollback:
            S.after_rollback.pop(0)()


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

    def delete_value(self, key):
        S.cache.pop(key, None)


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
    frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})  # 整笔重来接的冲突（`transaction.conflicts`）

    def throw(message, exc=ValidationError):
        raise exc(message)

    frappe.throw = throw
    frappe.validate_and_sanitize_search_inputs = lambda function: function
    frappe._ = lambda text: text
    frappe.get_all = get_all
    frappe.get_list = get_all
    # 加锁读（`collection.service._rows`、`snapshot.rows(locked=True)`）：替身里与普通读同一份数据；记下谁加锁读过
    frappe.qb = types.SimpleNamespace(get_query=lambda doctype, fields=None, filters=None, order_by=None, limit=None,
                                      for_update=False: types.SimpleNamespace(run=lambda as_dict=False: (
                                          S.locked.append(doctype) if for_update else None,
                                          [row for row in get_all(doctype, filters=filters, fields=fields,
                                                                        order_by=order_by, limit=limit)])[1]))
    frappe.get_doc = get_doc
    frappe.db = DBApi()
    frappe.cache = Cache()
    frappe.session = types.SimpleNamespace(user="a@x")
    frappe.local = types.SimpleNamespace(site="testerp.localhost")
    frappe.flags = Flags()  # 同 Frappe 的 _dict：属性、.get 两种读法（`transaction.conflicts` 用 .get）
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
from pearnly_erp_portal.express import contracts as express_contracts, desktop, facts, target as express_target  # noqa: E402

REAL_PICK = express_target.pick  # 选账套的真规则（Base 里换成固定一本；Books 换回真的）

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
        "company": "NLT", "label": "NLTTEST", "writers": ["PC"], "writable": True,
        "period_start": "2026-01-01", "period_end": "2026-12-31"}  # CC 报的账套都带会计期间
FACTS = {"receipt_types": TYPES, "cash_account": CASH_ACCOUNT}
# 新版 CC 按账套自身结构报的科目类别（`payment_methods.account_classes`，NLTTEST 本机只读副本的 GLACC / BKMAS / ISINFO 算出）
NLTTEST_CLASSES = {"1111-00": "cash", "1111-50": "cash_group", "1112-01": "bank", "1112-02": "bank", "1112-03": "bank",
                   "1113-01": "bank", "1113-02": "bank"}
CLASSED = [dict(row, account_class=NLTTEST_CLASSES.get(row["account"], "other")) for row in TYPES]

# 69EXP\TEST 的收款类型（11-12 实测：三种储蓄存款的名称没有「โอนเข้าบัญชี」、也没关联银行，旧规则一种转账都不给）。科目类别取同模板、
# 同科目号的 T9SINCER 科目表实测（统计 docs/work/2026-11-12-collection-transfer-rule-stats.md）；TR 关联的 S1 不在 BKMAS，没有科目
TEST69 = [
    ("01", "QR", "เช็ครับ", "Cheque", "11-02-02-00", "", "other"),
    ("02", "CH", "รับเงินสด", "Cash Received", "11-01-01-00", "", "cash"),
    ("03", "TR", "เงินโอนเข้าบัญชี (NBD)", "Bank Transfer", "", "S1", ""),
    ("04", "TF", "ค่าธรรมเนียมการโอน", "Bank charge", "53-02-02-00", "", "other"),
    ("07", "VS", "บัตร Visa", "Visa card", "11-02-02-00", "", "other"),
    ("09", "DC", "ส่วนลดเงินสด", "Cash Discount", "41-03-00-00", "", "other"),
    ("10", "TX", "ภาษีถูกหัก ณ ที่จ่าย", "Tax A/R", "11-05-01-02", "", "other"),
    ("11", "EX", "กำไรขาดทุนจากอัตราแลกเปลี่ยน", "Exchange rate P/L", "53-01-04-00", "", "other"),
    ("12", "CQ", "เงินฝากออมทรัพย์กรุงเทพ", "BBL SAVING", "11-01-03-01", "", "bank"),
    ("13", "SU", "บัญชีพัก-ลูกหนี้การค้า", "SUSPENSE ACCOUNT RECEIVABLE", "11-02-01-01", "", "other"),
    ("14", "SC", "เงินฝากออมทรัพย์-SCB", "SCB  SAVING", "11-01-03-05", "", "bank"),
    ("15", "OA", "ลูกหนี้เงินยืมกรรมการ", "ลูกหนี้เงินยืมกรรมการ", "15-07-00-00", "", "other"),
    ("16", "SS", "ค่าใช้จ่ายล่งเสริมการขาย", "ค่าใช้จ่ายล่งเสริมการขาย", "52-07-00-00", "", "other"),
    ("17", "OR", "รายได้อื่นๆ", "รายได้อื่นๆ", "42-05-00-00", "", "other"),
    ("18", "WH", "ภาษีเงินได้หัก ณ ที่จ่ายค้างจ่", "Withholding tax", "21-03-03-00", "", "other"),
    ("19", "19", "เงินโอนเกิน(ขาด) 7-11", "เงินโอนเกิน(ขาด) 7-11", "53-05-14-00", "", "other"),
    ("20", "20", "ค่าใช้จ่ายอื่นๆ", "", "53-05-02-00", "", "other"),
    ("21", "21", "ส่วนลดรับ", "ส่วนลดรับ", "51-03-02-00", "", "other"),
    ("22", "22", "ส่วนลดจ่าย", "ส่วนลดจ่าย", "41-03-00-00", "", "other"),
    ("23", "23", "เงินฝากออมทรัพย์ กสิกรไทย", "เงินฝากออมทรัพย์ กสิกรไทย", "11-01-03-06", "", "bank"),
]
TYPES69 = [{"code": code, "prefix": prefix, "name": name, "name_en": name_en, "bank": bank, "account": account,
            "account_issue": "" if account else "missing", "account_class": klass}
           for code, prefix, name, name_en, account, bank, klass in TEST69]
CASH69 = "11-01-01-00"


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

    def test_deposit_accounts_are_transfers_by_their_account_class_whatever_the_name(self):
        found = C.catalog(TYPES69, CASH69)
        self.assertEqual([item["prefix"] for item in found["transfers"]], ["CQ", "SC", "23"])
        self.assertEqual(found["discount"]["prefix"], "DC")
        reasons = {item["prefix"]: item["reason"] for item in found["excluded"]}
        self.assertEqual((reasons["QR"], reasons["TX"], reasons["WH"], reasons["CH"], reasons["TR"]),
                         (C.CHEQUE, C.WITHHOLDING, C.WITHHOLDING, C.CASH_BOX, C.NO_ACCOUNT))
        for prefix in ("TF", "VS", "EX", "SU", "OA", "SS", "OR", "19", "20", "21", "22"):
            self.assertEqual(reasons[prefix], C.NOT_MONEY, prefix)
        # 老版 CC 不报科目类别：照旧按名称判（这三种仍不列，升级 CC 后才列）
        old = C.catalog([{k: v for k, v in row.items() if k != "account_class"} for row in TYPES69], CASH69)
        self.assertEqual(old["transfers"], [])

    def test_the_name_rules_still_hold_on_deposit_accounts_and_cash_kinds_are_never_transfers(self):
        def verdict(name, klass, account="1112-01", name_en=""):
            return C.classify({"prefix": "X", "name": name, "name_en": name_en, "account": account, "account_class": klass},
                              CASH_ACCOUNT)

        self.assertEqual(verdict("เช็ครับ", "bank"), {"role": "", "reason": C.CHEQUE})  # 支票记进存款科目的也不列
        self.assertEqual(verdict("กระแสรายวัน ธ.กรุงเทพ", "bank", name_en="Cheque")["reason"], C.CHEQUE)
        self.assertEqual(verdict("ภาษีถูกหัก ณ ที่จ่าย", "bank")["reason"], C.WITHHOLDING)
        self.assertEqual(verdict("ส่วนลดเงินสด", "other", "4100-04"), {"role": C.DISCOUNT, "reason": ""})
        self.assertEqual(verdict("รับเงินสด", "cash", CASH_ACCOUNT)["reason"], C.CASH_BOX)
        self.assertEqual(verdict("เงินสดย่อย", "cash_group", "1111-50")["reason"], C.NOT_MONEY)  # 零用金
        self.assertEqual(verdict("รับเงินสด", "cash_group", "11-01-01-02")["reason"], C.NOT_MONEY)  # 另一个现金科目
        self.assertEqual(verdict("ค่าธรรมเนียมการโอน", "other", "5390-01")["reason"], C.NOT_MONEY)
        # 名称说转账、科目却是关联公司借款（T7/T8/T9 GISP·GIZP 的 GZ / GS）：科目为准，不列
        self.assertEqual(verdict("เงินโอนเข้าบัญชี GIZE", "other", "1210-01")["reason"], C.NOT_MONEY)
        # 名称只作辅助：现金组里不在 BKMAS、直接挂在组下的科目，名称说转账才列
        self.assertEqual(verdict("เงินโอนเข้าบัญชี KTB", "cash_group", "112110")["role"], C.TRANSFER)
        self.assertEqual(verdict("Bank Transfer-S/A BBL", "bank", "1113-05")["role"], C.TRANSFER)
        # 没有可用科目（类别空）照名称判，再按没有科目不列
        self.assertEqual(verdict("เงินโอนเข้าบัญชี (NBD)", "", "")["reason"], C.NO_ACCOUNT)

    def test_nlttest_lists_the_same_kinds_with_account_classes(self):
        self.assertEqual(C.catalog(CLASSED, CASH_ACCOUNT), C.catalog(TYPES, CASH_ACCOUNT))

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

    # ── 抵减单（退货 / 减项进收款，docs/work/2026-11-28-credit-note-assessment.md）──

    def offset_rows(self):
        return {**self.rows(), "SR1": {"available": "300.00", "remaining": "300.00", "doc_date": "2026-10-03", "rectyp": "5"},
                "AI1": {"available": "50.00", "remaining": "80.00", "doc_date": "2026-09-01", "rectyp": "0"}}

    def test_a_credit_note_comes_off_the_total_and_lines_keep_invoices_first(self):
        catalog = C.catalog(TYPES, CASH_ACCOUNT)
        checked = C.check({"invoices": [{"docnum": "SR1", "amount": "300"}, {"docnum": "IV1", "amount": "1000"},
                                        {"docnum": "AI1", "amount": "20"}], "cash": "680"}, self.offset_rows(), catalog)
        self.assertEqual((checked["total"], checked["offset"], checked["cash"]),
                         (Decimal("680.00"), Decimal("320.00"), Decimal("680.00")))
        self.assertEqual([(row["docnum"], row["rectyp"]) for row in checked["invoices"]], [("IV1", ""), ("SR1", "5"), ("AI1", "0")])
        self.assertEqual(C.summaries(checked)["invoice_summary"], "IV1 1000.00; SR1 -300.00; AI1 -20.00")

    def test_a_full_offset_needs_no_money_and_an_offset_never_stands_alone(self):
        catalog = C.catalog(TYPES, CASH_ACCOUNT)
        settled = C.check({"invoices": [{"docnum": "IV2", "amount": "300"}, {"docnum": "SR1", "amount": "300"}]},
                          self.offset_rows(), catalog)
        self.assertEqual((settled["total"], settled["methods"], settled["cash"]), (Decimal("0.00"), [], Decimal("0.00")))
        cases = [
            ({"invoices": [{"docnum": "SR1", "amount": "300"}]}, C.INVOICES_EMPTY),
            ({"invoices": [{"docnum": "IV2", "amount": "300"}, {"docnum": "SR1", "amount": "100"}],
              "methods": [{"prefix": "TR", "amount": "-200"}]}, C.AMOUNT_INVALID),
            ({"invoices": [{"docnum": "IV2", "amount": "100"}, {"docnum": "SR1", "amount": "300"}], "cash": "-150"},
             C.AMOUNT_MISMATCH),
            ({"invoices": [{"docnum": "IV2", "amount": "300"}, {"docnum": "SR1", "amount": "100"}], "cash": "-200"},
             C.AMOUNT_INVALID),
            ({"invoices": [{"docnum": "IV2", "amount": "100"}, {"docnum": "SR1", "amount": "300.01"}]}, C.OVER_COLLECTED),
            ({"invoices": [{"docnum": "IV2", "amount": "300.24"}, {"docnum": "SR1", "amount": "300"}],
              "methods": [{"prefix": "DC", "amount": "0.24"}]}, C.DISCOUNT_ONLY),
        ]
        for draft, code in cases:
            with self.subTest(code=code), self.assertRaises(C.CollectionError) as caught:
                C.check(draft, self.offset_rows(), catalog)
            self.assertEqual(caught.exception.code, code)

    def test_offsets_above_the_invoices_pay_the_difference_back_in_cash_or_by_transfer(self):
        # 原生 69EXP 测试账套 TEST 的 RE0000034 / RE0000036（现金负数）、RE0000035（转账行负数）；支票 Express 不收负数、本来就不列
        catalog = C.catalog(TYPES, CASH_ACCOUNT)
        draft = {"invoices": [{"docnum": "IV2", "amount": "100"}, {"docnum": "SR1", "amount": "300"}]}
        for cash in ("", "-200", "-200.00"):
            with self.subTest(cash=cash):
                checked = C.check({**draft, "cash": cash}, self.offset_rows(), catalog)
                self.assertEqual((checked["total"], checked["cash"], checked["methods"]),
                                 (Decimal("-200.00"), Decimal("-200.00"), []))
        # 转账退（原生 RE0000035，转账行负数）
        checked = C.check({**draft, "methods": [{"prefix": "TR", "amount": "-200"}]}, self.offset_rows(), catalog)
        self.assertEqual((checked["cash"], [(row["prefix"], row["amount"]) for row in checked["methods"]]),
                         (Decimal("0.00"), [("TR", Decimal("-200.00"))]))
        for methods, cash in (([{"prefix": "DC", "amount": "1"}], "-201"), ([{"prefix": "TR", "amount": "100"}], "-300")):
            with self.subTest(methods=methods), self.assertRaises(C.CollectionError) as caught:
                C.check({**draft, "methods": methods, "cash": cash}, self.offset_rows(), catalog)
            self.assertEqual(caught.exception.code, C.OFFSETS_EXCEED)
        with self.assertRaises(C.CollectionError) as caught:
            C.check(draft, self.offset_rows(), C.catalog(TYPES, ""))
        self.assertEqual(caught.exception.code, C.CASH_ACCOUNT_MISSING)

    def test_a_total_leaves_ticked_offsets_whole_and_spreads_over_the_invoices(self):
        rows = [{"docnum": "IV2", "doc_date": "2026-10-05", "available": "500"},
                {"docnum": "IV1", "doc_date": "2026-10-01", "available": "1000"},
                {"docnum": "SR1", "doc_date": "2026-09-01", "available": "300", "rectyp": "5"}]
        self.assertEqual(C.allocate(rows, "900"), [{"docnum": "SR1", "amount": Decimal("300.00")},
                                                   {"docnum": "IV1", "amount": Decimal("1000.00")},
                                                   {"docnum": "IV2", "amount": Decimal("200.00")}])

    def test_the_payload_with_offsets_is_v23_and_without_stays_v20(self):
        record = {"ref_no": "COL261128-AB12", "doc_date": "2026-11-28", "customer": "NLT6010018", "salesman": "",
                  "department": "", "cash": Decimal("0"), "methods": [],
                  "invoices": [{"docnum": "IV1", "amount": Decimal("100"), "rectyp": "3"},
                               {"docnum": "SRTEST01", "amount": Decimal("100"), "rectyp": "5"}]}
        body = C.payload(record, BOOK, {"tax_id": "0105549038811", "branch": "00000"})
        self.assertEqual((body["payload_version"], body["invoices"], body["offsets"]),
                         (C.OFFSETS_PAYLOAD_VERSION, [{"docnum": "IV1", "amount": "100.00"}],
                          [{"docnum": "SRTEST01", "rectyp": "5", "amount": "100.00"}]))
        plain = C.payload({**record, "invoices": record["invoices"][:1]}, BOOK, {"tax_id": "1", "branch": "00000"})
        self.assertEqual(plain["payload_version"], C.PAYLOAD_VERSION)
        self.assertNotIn("offsets", plain)

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

    def waiting(self, kind, book_key, actor=None, params=None):
        return None

    def create(self, kind, key, params, *, book_key, actor):
        self.made[key] = {"request": "R-" + key, "status": "requested", "params": params}
        return self.made[key]

    def result(self, name):
        return self.results.pop(name, b"{}"), {}

    def take(self, name):
        """同真的 `requests.take`：读好了才取走（取走即删行；没读好、已被取走回 None）。"""
        row = next((row for row in self.made.values() if row["request"] == name), None)
        if row is None or row["status"] != "done":
            return None
        content = self.result(name)
        self.drop(name)
        return content

    def drop(self, name):
        for key, row in list(self.made.items()):
            if row["request"] == name:
                del self.made[key]

    def finish(self, key, items, read_at, looked_up=None):
        self.made[key]["status"] = "done"
        data = {"read_at": read_at, "items": items}
        if looked_up is not None and "docnums" in self.made[key]["params"]:  # 电脑只在请求带单号时报
            data["looked_up"] = looked_up
        self.results["R-" + key] = json.dumps(data).encode()


class FakeLock:
    """账套串行锁（`snapshot._hold` 用不淘汰的 Redis）的替身：记下拿、放。"""
    def __init__(self, key):
        self.key = key

    def acquire(self):
        S.serial.append((self.key, "acquire"))
        return True

    def release(self):
        S.serial.append((self.key, "release"))


FAKE_STATE = types.SimpleNamespace(make_key=lambda key: key, lock=lambda key, **kw: FakeLock(key))


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
        # 同真的：按请求号去重；请求号带次数（写前被拒、退回草稿再放行）是另一件，投递号 J-<记录号>-<次数>
        name = "J-" + request_key.split(":", 2)[-1].replace(":", "-")
        if name in self.rows:
            return self.view(name)
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

    def retry(self, name):
        # 同真的 `cc_desktop.jobs.retry`：转人工的再排，结果不明的钉回原电脑
        self.rows[name].update(status="queued", reason="", pinned=self.rows[name].get("reason") == C.EXPRESS_UNKNOWN)
        desktop.job_changed(self.view(name))


# 用户 11-14：只有一个「收款」权限（`rules`：collection.view 与 collection.write 同一判据）——boss 是管理员（默认有收款），
# sales 只有销售岗位、没勾收款：什么都看不到
ROLES = {"a@x": {"collection.write"}, "b@x": {"collection.write"}, "boss@x": {"collection.write"},
         "sales@x": {"sales.read"}, "nobody@x": set()}
ALLOWED = {"a@x": ["CO-1"], "b@x": ["CO-1"], "boss@x": ["CO-1", "CO-2"], "sales@x": ["CO-1"], "nobody@x": ["CO-1"],
           "other@x": ["CO-2"]}
ROLES["other@x"] = {"collection.write"}


def _has(capability, user=None):
    capability = "collection.write" if capability == "collection.view" else capability  # 同一个「收款」权限
    return capability in ROLES.get(user or frappe.session.user, set())


def _require(capability, user=None):
    if not _has(capability, user):
        raise PermissionError_("Not permitted")


def _ledger_rows(doctype, company, text, *, start=0, limit=20, filters=None, ledger_only=False):
    """`masters.link_search.rows` 的替身（真查询是原生 SQL，另有流程守）：这家的账本客户，名称 / 编码 / 税号包含所打的字。"""
    words = str(text or "").split()
    found = []
    for row in sorted(S.table("ERP Customer").values(), key=lambda row: (row.get("customer_name"), row["name"])):
        hay = " ".join(str(row.get(key) or "") for key in ("name", "customer_name", "express_code", "tax_id"))
        if row.get("express_company") == company and row.get("express_code") and all(word in hay for word in words):
            found.append(Row(name=row["name"], title=row["customer_name"], express_code=row["express_code"],
                             tax_id=row.get("tax_id") or ""))
    return found[start:start + limit]


class Base(unittest.TestCase):
    def setUp(self):
        global S
        S.__init__()
        frappe.local.__dict__.pop("erp_ar_serial", None)  # 本事务拿着的账套串行锁（真站点每个请求一份新的 frappe.local）
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
                       side_effect=lambda row, day, wanted=None: {"book": BOOK, "reason": "", "options": []}),
            mock.patch("pearnly_erp_portal.express.facts.load", side_effect=lambda key: FACTS),
            mock.patch("pearnly_core.cc_desktop.books.for_company", side_effect=lambda tax, branch: [BOOK]),
            mock.patch.object(snapshot, "requests", self.requests),
            mock.patch.object(snapshot, "_state", return_value=FAKE_STATE),
            mock.patch.object(push, "jobs", self.jobs),
            mock.patch.object(records, "display_name", side_effect=lambda user: user.split("@")[0].upper()),
            mock.patch("pearnly_erp_portal.masters.link_search.rows", side_effect=_ledger_rows),
            mock.patch("pearnly_erp_portal.masters.link_search.count",
                       side_effect=lambda *a, **k: len(_ledger_rows(*a, **{**k, "start": 0, "limit": 10 ** 6}))),
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

    def test_release_judges_balances_and_reservations_with_locked_reads(self):
        # 11-28 B ②：关快照隔离后普通读走拿发票锁之前建的读视图，看不到前一笔刚放行的预留（两笔收同一张）；改草稿、列未收清不加锁
        made = self.draft("600")
        S.locked.clear()
        service.release(made["name"], made["version"], "line:evt-1")
        self.assertTrue({"ERP Receivable Snapshot", "ERP Collection Invoice"} <= set(S.locked), S.locked)
        S.locked.clear()
        self.draft("100", user="b@x")
        self.assertEqual(S.locked, [])

    def test_release_locks_the_customer_rows_before_its_own_record_and_others_reservations(self):
        # 退货减项 × 11-28 B ②：同一位客户两笔同时放行，先锁各自几张、再锁整位客户 / 别人记录的预留会互等（实验栈 1213）；
        # 固定顺序：这位客户的快照行（发票与抵减单一起）→ 这一笔 → 发票行锁 → 加锁读余额与预留
        made = self.draft("600")
        S.locked.clear()
        service.release(made["name"], made["version"], "line:evt-1")
        self.assertEqual(S.locked[:3], ["ERP Receivable Snapshot", "ERP Collection", "ERP Receivable Snapshot"], S.locked)

    def test_a_credit_note_ticked_in_two_drafts_is_taken_once_the_later_release_is_stopped(self):
        # 退货减项 × 11-28 B ②：减项单同发票一样进放行的加锁读与预留，两笔收款都勾满同一张，后放行的那笔超收、仍是草稿
        snapshot.replace("CO-1", BOOK["key"], [
            {"docnum": "IV1", "rectyp": "3", "docdat": "2026-10-01", "customer": "2100883", "salesman": "S1",
             "department": "CPI", "net": "1000.00", "received": "0.00", "remaining": "1000.00"},
            {"docnum": "IV2", "rectyp": "3", "docdat": "2026-10-05", "customer": "2100883", "salesman": "S2",
             "department": "", "net": "800.00", "received": "300.00", "remaining": "500.00"},
            {"docnum": "SR1", "rectyp": "5", "docdat": "2026-10-06", "customer": "2100883", "salesman": "S1",
             "department": "", "net": "300.00", "received": "0.00", "remaining": "300.00"}], "2026-11-11T03:00:00+00:00",
            customer="2100883")
        first = self.draft(invoices=[{"docnum": "IV1", "amount": "1000"}, {"docnum": "SR1", "amount": "300"}], cash="700")
        second = self.draft(invoices=[{"docnum": "IV2", "amount": "500"}, {"docnum": "SR1", "amount": "300"}], cash="200",
                            user="b@x")
        self.as_user("a@x")
        S.locked.clear()
        self.assertEqual(service.release(first["name"], first["version"], "line:evt-1")["status"], "released")
        self.assertTrue({"ERP Receivable Snapshot", "ERP Collection Invoice"} <= set(S.locked), S.locked)
        self.as_user("b@x")
        result = service.release(second["name"], second["version"], "line:evt-2")
        self.assertEqual((result["status"], result["code"]), ("invalid", C.OVER_COLLECTED))
        self.assertEqual(result["detail"], {"available": {"SR1": "0.00"}})
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
                         (20, "receipt", [{"docnum": "IV1", "amount": "700.00"}], "700.00", made["name"], "a@x",
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


class WebCreate(Base):
    """ERP 网页「新建」（11-14）：弹窗提交即放行，同一套规则（`service.submit_web` → create_draft + release）。"""

    def submit(self, amount="600", request="r-1", **extra):
        from pearnly_erp_portal.collection import api

        return api.api_collection_submit("CO-1", "2100883", json.dumps([{"docnum": "IV1", "amount": amount}]),
                                         request, cash=extra.pop("cash", amount), **extra)

    def test_a_submit_records_and_releases_once_per_dialog(self):
        self.as_user("a@x")
        made = self.submit()
        row = S.table("ERP Collection")[made["name"]]
        self.assertEqual((made["status"], row["source"], row["consent_ref"], self.jobs.rows["J-" + made["name"]]["status"]),
                         (C.QUEUED, "web", "web:r-1", "queued"))
        self.assertEqual(self.submit()["name"], made["name"])  # 连点 / 重发：同一个弹窗只建一笔
        self.assertEqual(len(S.table("ERP Collection")), 1)
        self.assertEqual(self.available()["IV1"], "400.00")  # 与 LINE / CC 同样占着余额
        self.as_user("a@x")
        self.assertNotEqual(self.submit("100", request="r-2")["name"], made["name"])  # 新开一次弹窗是新的一笔

    def test_the_same_rules_refuse_with_the_same_sentence(self):
        self.as_user("a@x")
        with self.assertRaises(frappe.ValidationError) as caught:
            self.submit("1200")  # 超过未收
        self.assertIn("More than the amount due", str(caught.exception))
        with self.assertRaises(frappe.ValidationError) as caught:  # 转账没附凭证：不放行（请求整体回滚）
            self.submit("600", cash=0, methods=json.dumps([{"prefix": "BB", "amount": "600"}]))
        self.assertIn("Send a photo of the transfer slip", str(caught.exception))

    def test_only_people_with_the_collection_permission_can_submit(self):
        for user in ("nobody@x", "sales@x"):
            self.as_user(user)
            with self.assertRaises(PermissionError_):
                self.submit()
        from pearnly_erp_portal.collection import records

        for user in ("a@x", "boss@x"):  # 勾了收款的、管理员：看得到也能新建（同一个权限）
            self.as_user(user)
            self.assertTrue(records.page({})["can_create"])

    def test_a_person_given_company_a_cannot_record_for_company_b(self):
        """团队管理只给了 A 公司的员工（主会话 11-14）：弹窗下拉里没有 B，带 B 调接口被拒（服务端判）。"""
        from pearnly_erp_portal.collection import api

        self.as_user("a@x")  # 只给了 CO-1
        self.assertEqual([row["value"] for row in api.api_collection_new_options()["companies"]], ["CO-1"])
        with self.assertRaises(PermissionError_):
            api.api_collection_submit("CO-2", "2100883", json.dumps([{"docnum": "IV1", "amount": "10"}]), "r-b", cash="10")
        with self.assertRaises(PermissionError_):
            api.api_collection_customer_search("2100", "CO-2")

    def test_the_customer_box_searches_on_the_server_one_page_at_a_time(self):
        from pearnly_erp_portal.collection import api

        self.as_user("a@x")
        found = api.api_collection_customer_search("2100", "CO-1")
        self.assertEqual(found[0]["value"], "2100883")
        self.assertLessEqual(len(found), 20)
        self.assertEqual(api.api_collection_customer_search("2100", None), [])

    def test_the_collections_page_withdraws_like_line_for_anyone_with_the_permission(self):
        from pearnly_erp_portal.collection import api

        self.as_user("a@x")
        made = self.submit()
        self.as_user("b@x")  # 同公司、勾了收款的另一个人（用户 11-14：不分谁录的）
        self.assertEqual(api.api_collection_withdraw(made["name"])["status"], C.WITHDRAWN)
        self.assertEqual(self.jobs.rows["J-" + made["name"]]["status"], "cancelled")
        self.as_user("other@x")  # 别家公司的人：撤不了、也看不到
        with self.assertRaises(PermissionError_):
            api.api_collection_withdraw(made["name"])


class Withdraw(Base):
    def test_withdraw_before_the_computer_takes_it(self):
        made = self.draft("100")
        self.assertEqual(service.withdraw(made["name"])["status"], C.WITHDRAWN)
        queued = self.draft("100")
        service.release(queued["name"], 1, "line:evt-1")
        self.assertEqual(service.withdraw(queued["name"])["status"], C.WITHDRAWN)
        self.assertEqual(self.jobs.rows["J-" + queued["name"]]["status"], "cancelled")
        self.assertEqual(frappe.db.get_value("ERP Collection", queued["name"], "status"), C.WITHDRAWN)  # 撤掉的回执不改它

    def test_a_status_change_tells_people_who_see_the_collections_page_by_record_name_only(self):
        """11-15 真机：「待写入」写完不自己变。回执改了状态，推给有「收款·查看」的人一个只带单号的事件，收款页据此刷新。"""
        made = self.draft("100")
        sent = []
        users = {"boss@example.com": True, "sales@example.com": False}
        real_get_all = frappe.get_all

        def get_all(doctype, *args, **kwargs):
            return sorted(users) if doctype == "User" else real_get_all(doctype, *args, **kwargs)

        with mock.patch.object(frappe, "get_all", get_all), \
                mock.patch.object(frappe, "publish_realtime", lambda event, data, **kw: sent.append((event, data, kw.get("user")))), \
                mock.patch("pearnly_erp_portal.access.guard.has", lambda capability, user=None: users.get(user, False)):
            frappe.flags.pearnly_collection_live = None
            service.release(made["name"], 1, "line:evt-1")
            frappe.flags.pearnly_collection_live = None
            self.jobs.report("J-" + made["name"], "success", docnum="RE2403080")
        self.assertTrue(sent)
        self.assertEqual({(event, json.dumps(data), user) for event, data, user in sent},
                         {(push.LIVE_EVENT, json.dumps({"name": made["name"]}), "boss@example.com")})

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


class ResultUnknown(Base):
    """主会话 11-25 定 A：写进 Express 结果不明（CC 桌面版写到一半断了）的不说失败、不撤回，只给「重试」——同一个共用写账队列
    `cc_desktop.jobs.retry` 钉回原电脑再排，CC 桌面版按参照号查重（写过的回原号、一行不写）。"""

    def lost(self):
        made = self.draft("100")
        service.release(made["name"], 1, "line:evt-1")
        self.jobs.report("J-" + made["name"], "manual", reason=C.EXPRESS_UNKNOWN)
        return made["name"]

    def test_the_lost_reason_is_the_shared_queue_one(self):
        jobs = (ROOT / "apps/pearnly_core/pearnly_core/cc_desktop/jobs.py").read_text(encoding="utf-8")
        self.assertIn(f'LOST = "{C.EXPRESS_UNKNOWN}"', jobs)

    def test_it_is_result_unknown_not_failed_and_only_retry_is_offered(self):
        name = self.lost()
        view = service.status(name)
        self.assertEqual(view["status"], C.FAILED)
        self.assertEqual((view["result_unknown"], view["retryable"]), (True, True))
        self.assertEqual(C.status_word(view["status"], view["reason"]), "Result unknown")
        self.assertEqual(C.status_word(C.FAILED, "[already_collected] x"), "Failed")
        with self.assertRaises(C.CollectionError) as caught:
            service.withdraw(name)
        self.assertEqual(caught.exception.code, C.NOT_WITHDRAWABLE)
        self.assertEqual(self.jobs.rows["J-" + name]["status"], "manual")  # 撤回没动写账件

    def test_retry_requeues_the_same_job_on_the_same_computer(self):
        name = self.lost()
        view = service.retry(name)
        self.assertEqual(view["status"], C.QUEUED)
        self.assertEqual(len(self.jobs.rows), 1)  # 同一件写账，不另排
        self.assertEqual((self.jobs.rows["J-" + name]["status"], self.jobs.rows["J-" + name]["pinned"]), ("queued", True))
        self.assertEqual(frappe.db.get_value("ERP Collection", name, "reason"), "")

    def test_retry_is_only_for_result_unknown(self):
        made = self.draft("100")
        service.release(made["name"], 1, "line:evt-1")
        self.jobs.report("J-" + made["name"], "manual", reason="[already_collected] IV1")
        for name in (made["name"], self.draft("100")["name"]):
            with self.subTest(name=name), self.assertRaises(C.CollectionError) as caught:
                service.retry(name)
            self.assertEqual(caught.exception.code, C.NOT_RETRYABLE)

    def test_the_status_filter_lists_result_unknown_apart_from_failed(self):
        """主会话 11-28：筛选也是显示——「结果不明」单列一档、「失败」不含它；服务端条件（不在浏览器里过滤），判据同状态词。"""
        self.assertEqual(C.status_filters([["company", "=", "CO-1"], ["status", "=", "result_unknown"]]),
                         [["company", "=", "CO-1"], ["status", "=", "failed"], ["reason", "like", C.UNKNOWN_LIKE]])
        self.assertEqual(C.status_filters([["status", "=", "failed"]]),
                         [["status", "=", "failed"], ["reason", "not like", C.UNKNOWN_LIKE]])
        self.assertEqual(C.status_filters([["status", "in", ["failed", "result_unknown", "queued"]]]),
                         [["status", "in", ["failed", "queued"]]])
        for reason in C.UNKNOWN_REASONS:  # 库里 LIKE 认得的正是状态词认的那两种
            self.assertIn(C.UNKNOWN_LIKE.strip("%"), reason + ": x")
        meta = json.loads((ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/doctype/erp_collection/"
                                  "erp_collection.json").read_text(encoding="utf-8"))
        options = next(f["options"] for f in meta["fields"] if f["fieldname"] == "status").split("\n")
        self.assertIn(C.RESULT_UNKNOWN_STATUS, options)  # 原生筛选的下拉里有这一档
        records = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/collection/records.py").read_text(encoding="utf-8")
        self.assertIn("return contracts.status_filters(filters), or_filters", records)

    def test_the_page_offers_retry_instead_of_withdraw_and_shows_the_word(self):
        page = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/collections.js").read_text(encoding="utf-8")
        web = re.findall(r'"([^"]+)"', page.split("const UNKNOWN_REASONS = [", 1)[1].split("];", 1)[0])
        self.assertEqual(tuple(web), C.UNKNOWN_REASONS)
        self.assertIn("const can_withdraw = !can_retry && ", page)
        self.assertIn('api + "api_collection_retry"', page)


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
        self.as_user("other@x")  # 别家公司的收款岗位：看不到、动不了
        for call in (lambda: service.card(made["name"]), lambda: service.release(made["name"], 1, "x"),
                     lambda: service.withdraw(made["name"]), lambda: service.status(made["name"]),
                     lambda: service.update_draft(made["name"], 1, [{"docnum": "IV1", "amount": 1}], cash=1)):
            with self.assertRaises(PermissionError_):
                call()
        self.as_user("sales@x")  # 只有销售岗位、没勾收款（用户 11-14）：看不到
        with self.assertRaises(PermissionError_):
            service.status(made["name"])

    def test_anyone_with_the_permission_in_the_company_edits_releases_and_withdraws_any_collection(self):
        """用户 11-14：勾了收款的人（或管理员）能改 / 放行 / 撤回本公司还没写进 Express 的任何一笔，不分谁录的。"""
        made = self.draft("100")
        self.as_user("b@x")  # 同公司另一位
        self.assertEqual(service.card(made["name"])["name"], made["name"])
        changed = service.update_draft(made["name"], 1, [{"docnum": "IV1", "amount": "90"}], cash="90")
        self.assertEqual(changed["version"], 2)
        self.as_user("boss@x")  # 管理员
        self.assertEqual(service.release(made["name"], 2, "web:x")["status"], "released")
        self.assertEqual(service.withdraw(made["name"])["status"], C.WITHDRAWN)
        self.assertEqual(S.table("ERP Collection")[made["name"]]["collector"], "a@x")  # 谁录的照旧记着

    def test_two_people_editing_the_same_draft_the_later_one_is_refused_by_the_version(self):
        made = self.draft("100")
        self.as_user("a@x")
        service.update_draft(made["name"], 1, [{"docnum": "IV1", "amount": "80"}], cash="80")
        self.as_user("b@x")  # 打开时也是版本 1
        with self.assertRaises(C.CollectionError) as caught:
            service.update_draft(made["name"], 1, [{"docnum": "IV1", "amount": "70"}], cash="70")
        self.assertEqual((caught.exception.code, caught.exception.detail), (C.CHANGED, {"version": 2}))
        self.assertEqual(service.release(made["name"], 1, "web:y"), {"status": "changed", "version": 2})

    def test_customers_search_by_code_or_name_server_side(self):
        self.as_user("a@x")
        self.assertEqual(service.customers("CO-1", "ป้า")["items"], [{"code": "2100883", "name": "ร้านป้าแดง", "tax_id": ""}])
        self.assertEqual(service.customers("CO-1", "2100")["total"], 2)
        self.assertEqual(len(service.customers("CO-1", "", limit=1)["items"]), 1)
        self.assertEqual(service.companies_for(), [{"name": "CO-1", "company_name": "นิภา"}])
        # 11-15 线上：账套没报会计期间的公司选了也录不了（问不出收款日期），不列出来
        with mock.patch.dict(BOOK, {"period_start": "", "period_end": ""}):
            self.assertEqual(service.companies_for(), [])

    def test_customers_search_finds_the_customer_of_an_open_invoice_number(self):
        # 11-20 真模型：「客户 <码> 付了 <发票号>」CC 开卡带的是发票号或「码 + 发票号」，客户题曾为空。对不上客户时按未收发票快照认
        self.as_user("a@x")
        mine = [{"code": "2100883", "name": "ร้านป้าแดง", "tax_id": ""}]
        self.assertEqual(service.customers("CO-1", "IV2"), {"items": mine, "total": 1})
        self.assertEqual(service.customers("CO-1", "2100883 IV1")["items"], mine)
        self.assertEqual(service.customers("CO-1", "iv3")["items"][0]["code"], "2100900")
        self.assertEqual(service.customers("CO-1", "IV1 IV3")["total"], 2)       # 两张发票两位客户：都列
        self.assertEqual(service.customers("CO-1", "IV1 IV2")["total"], 1)       # 同一位客户只列一次
        self.assertEqual(service.customers("CO-1", "IV1 IV3", start=1, limit=1)["items"][0]["code"], "2100900")
        self.assertEqual(service.customers("CO-1", "IV9"), {"items": [], "total": 0})   # 快照里没有：照旧空
        self.assertEqual(service.customers("CO-1", "IV")["total"], 0)           # 单号只精确对，不按一部分
        # 别家公司的快照不认；能对上客户时不走发票（照旧按客户搜）
        snapshot.replace("CO-2", "OTHER-BOOK", [{"docnum": "IVX", "customer": "2100883", "net": "1", "remaining": "1"}],
                         "2026-11-11T03:00:00+00:00")
        self.assertEqual(service.customers("CO-1", "IVX")["total"], 0)
        self.assertEqual(service.customers("CO-1", "ป้า")["items"], mine)

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
        self.assertTrue(access.record_permission(doc, "read", "b@x"))  # 同公司勾了收款的：看全部（用户 11-14）
        self.assertFalse(access.record_permission(doc, "read", "sales@x"))  # 只有销售岗位：看不到
        self.assertFalse(access.record_permission(doc, "read", "other@x"))  # 别家公司
        self.assertFalse(access.record_permission(doc, "read", "nobody@x"))
        self.assertEqual(access.record_query("a@x"), "`tabERP Collection`.company in ('CO-1')")
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
        self.assertIn(snapshot.request_key(BOOK["key"], "2100883"), self.requests.made)  # 再按客户读一次

    def test_already_collected_keeps_each_current_balance(self):
        """CC 回的码照原样是小写（`dbf_receipt.ERR_ALREADY_COLLECTED`，回执 ``[already_collected] …``；11-14 前服务端比的是大写、
        从没对上过）。写前就拒、一行没写：退回草稿带原因（卡片有效期规则 A9），余额照记。"""
        name, job = self.released()
        self.jobs.report(job, "manual", reason="[already_collected] IV1 remaining 0.00",
                         meta={"invoices": [{"docnum": "IV1", "remaining": "0.00"}]})
        row = frappe.db.get_value("ERP Collection", name, ["status", "reason", "balances", "draft_version", "attempt"],
                                  as_dict=True)
        self.assertEqual((row.status, row.draft_version, row.attempt), (C.DRAFT, 2, 1))
        self.assertTrue(row.reason.startswith("[already_collected]"))
        self.assertEqual(json.loads(row.balances), {"IV1": "0.00"})
        self.assertIsNone(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV1"}, "name"))  # 已收清：快照里没了
        self.as_user("a@x")
        self.assertEqual(service.status(name)["balances"], {"IV1": "0.00"})

    def test_a_cash_basis_service_invoice_comes_back_as_invoice_not_found_and_is_reread(self):
        """主会话 11-11（更正）：CC 不列服务品现金制发票（SRV_VATTYP=2），写入时遇到回 ``[INVOICE_NOT_FOUND]``、证据里
        ``problem="cash_basis_vat"``，没有新码：照原因记失败，按客户再读一次未收清发票；没带余额就不记余额。"""
        name, job = self.released()
        self.assertNotIn(snapshot.request_key(BOOK["key"], "2100883"), self.requests.made)
        self.jobs.report(job, "manual", reason="[invoice_not_found] IV1", meta={"problem": "cash_basis_vat"})
        row = frappe.db.get_value("ERP Collection", name, ["status", "reason", "balances"], as_dict=True)
        self.assertEqual((row.status, row.reason, row.balances), (C.DRAFT, "[invoice_not_found] IV1", None))
        self.assertIn(snapshot.request_key(BOOK["key"], "2100883"), self.requests.made)
        self.assertEqual(C.UNCOLLECTABLE, ("already_collected", "invoice_not_found"))

    def test_other_failures_and_queue_states(self):
        name, job = self.released()
        self.jobs.report(job, "queued", reason="payload_version_outdated")
        # 排队里的原因不抄到收款记录（11-12 约定：在不在等 Express 关闭读的时候问共用通道，见 ExpressWaiting）
        self.assertEqual(frappe.db.get_value("ERP Collection", name, ["status", "reason"]), (C.QUEUED, ""))
        self.jobs.report(job, "manual", reason="[WRITE_FAILED] ARTRN locked")  # 写到一半回滚：不还回，照旧失败
        self.assertEqual(frappe.db.get_value("ERP Collection", name, ["status", "reason", "balances"]),
                         (C.FAILED, "[WRITE_FAILED] ARTRN locked", None))
        self.assertEqual(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV1"}, "remaining"), Decimal("1000.00"))

    def test_a_refusal_before_writing_returns_the_draft_and_a_second_release_is_a_new_request(self):
        """写前就拒（7 个码，主会话 11-14 审定）：退回草稿、版本 +1；改了再放行是新的写账件，请求号带次数，YOUREF 仍是记录号；
        上一件的回执（转人工的被重排、迟到）不再改这笔收款。"""
        name, job = self.released()
        self.jobs.report(job, "manual", reason="[receipt_type_invalid] BB")
        self.as_user("a@x")
        view = service.status(name)
        self.assertEqual((view["status"], view["version"], view["returned"], view["reason"]),
                         (C.DRAFT, 2, True, "[receipt_type_invalid] BB"))
        self.assertEqual(service.release(name, 1, "line:evt-2"), {"status": "changed", "version": 2})  # 旧卡的版本放不了
        self.assertEqual(service.release(name, 2, "line:evt-3")["status"], "released")
        again = "J-" + name + "-1"
        self.assertEqual((self.jobs.rows[again]["request_key"], self.jobs.rows[again]["payload"]["ref_no"]),
                         ("erp:receipt:" + name + ":1", name))
        self.assertEqual(frappe.db.get_value("ERP Collection", name, ["status", "job", "reason"]), (C.QUEUED, again, ""))
        self.jobs.report(job, "success", docnum="RE-OLD")  # 上一件迟到的回执：不改
        self.assertEqual(frappe.db.get_value("ERP Collection", name, ["status", "docnum"]), (C.QUEUED, None))
        self.jobs.report(again, "manual", reason="[already_collected] IV1", meta={"invoices": [{"docnum": "IV1", "remaining": "0.00"}]})
        self.assertEqual(frappe.db.get_value("ERP Collection", name, ["status", "draft_version", "attempt"]), (C.DRAFT, 3, 2))
        self.assertEqual(service.withdraw(name)["status"], C.WITHDRAWN)  # 退回的草稿照样能撤回

    def test_only_the_eight_codes_cc_refuses_before_writing_are_returned(self):
        self.assertEqual(C.RETURNABLE, ("already_collected", "invoice_not_found", "receipt_type_invalid",
                                        "cash_account_missing", "deposit_account_missing", "JOURNAL_BOOK_NOT_FOUND", "CUSTOMER_NOT_IN_ARMAS",
                                        "ACCOUNT_NOT_POSTABLE"))
        for reason in ("[WRITE_FAILED] x", "[VERIFY_FAILED] x", "cc_desktop.result_unknown", "[PERIOD_LOCKED] x"):
            name, job = self.released("10")
            self.jobs.report(job, "manual", reason=reason)
            self.assertEqual(frappe.db.get_value("ERP Collection", name, "status"), C.FAILED, reason)

    def test_sales_documents_still_go_to_the_document_push(self):
        with mock.patch.object(express_push, "job_changed", create=True) as document_push:
            desktop.job_changed({"source_doctype": "ERP Sales Document", "source_name": "SD-1", "status": "queued"})
        document_push.assert_called_once()


class Staff(Base):
    """RE 抬头的销售员与部门不留空给 CC 回退单据簿（主会话 11-11）：默认第一张发票的，那张没有部门用 RE 单据簿的默认部门；
    收款员在编辑里改了就用改的（改过的之后改金额、补凭证都沿用），给的值要在这本账套里。"""

    def setUp(self):
        super().setUp()
        FACTS.update(receipt_run={"prefix": "RE", "next": "2403080", "department": "HQ"},
                     salesmen=[{"code": "S1", "name": "A"}, {"code": "S2", "name": "B"}, {"code": "S9", "name": "C"},
                               {"code": "S0", "name": "old", "inactive": True}],
                     departments=[{"code": "CPI", "name": "CPI"}, {"code": "HQ", "name": "HQ"}, {"code": "BKK", "name": "BKK"}])
        self.addCleanup(lambda: [FACTS.pop(key, None) for key in ("receipt_run", "salesmen", "departments")])

    def payload(self, name, version=None):
        service.release(name, version or service.card(name)["version"], "line:evt")
        return self.jobs.rows["J-" + name]["payload"]

    def test_defaults_come_from_the_first_invoice(self):
        made = self.draft("600")  # IV1：销售员 S1、部门 CPI
        payload = self.payload(made["name"])
        self.assertEqual((payload["salesman"], payload["department"]), ("S1", "CPI"))

    def test_a_first_invoice_without_a_department_takes_the_receipt_book_default(self):
        made = self.draft(invoices=[{"docnum": "IV2", "amount": "500"}], cash="500")  # IV2：S2，没有部门
        self.assertEqual((made["salesman"], made["department"]), ("S2", "HQ"))
        payload = self.payload(made["name"])
        self.assertEqual((payload["salesman"], payload["department"]), ("S2", "HQ"))

    def test_edited_values_are_kept_through_later_changes_and_unedited_ones_follow_the_first_invoice(self):
        made = self.draft("600")
        changed = service.update_draft(made["name"], 1, [{"docnum": "IV1", "amount": "600"}], cash="600",
                                       salesman="S9")
        self.assertEqual((changed["salesman"], changed["department"]), ("S9", "CPI"))
        # 换成先收 IV2：没改过的部门跟着新的第一张（IV2 没有部门 → 单据簿默认），改过的销售员沿用
        again = service.update_draft(made["name"], 2, [{"docnum": "IV2", "amount": "500"}, {"docnum": "IV1", "amount": "100"}],
                                     cash="600")
        self.assertEqual((again["salesman"], again["department"]), ("S9", "HQ"))
        payload = self.payload(made["name"])
        self.assertEqual((payload["salesman"], payload["department"]), ("S9", "HQ"))

    def test_edited_values_must_be_in_the_account_set(self):
        made = self.draft("600")
        for kwargs, code in (({"salesman": "S0"}, C.SALESMAN_INVALID), ({"salesman": "NOPE"}, C.SALESMAN_INVALID),
                             ({"department": "XX"}, C.DEPARTMENT_INVALID)):
            with self.subTest(kwargs=kwargs), self.assertRaises(C.CollectionError) as caught:
                service.update_draft(made["name"], 1, [{"docnum": "IV1", "amount": "600"}], cash="600", **kwargs)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(service.update_draft(made["name"], 1, [{"docnum": "IV1", "amount": "600"}], cash="600",
                                              department="BKK")["department"], "BKK")
        self.assertEqual(service.staff_choices(made["name"])["salesmen"],
                         [{"code": "S1", "name": "A"}, {"code": "S2", "name": "B"}, {"code": "S9", "name": "C"}])

    def test_the_rule_on_its_own(self):
        self.assertEqual(C.staff({"salesman": "S1", "department": ""}, "HQ"), {"salesman": "S1", "department": "HQ"})
        self.assertEqual(C.staff({"salesman": "S1", "department": "CPI"}, "HQ", department=""),
                         {"salesman": "S1", "department": "CPI"})  # 清空部门也不留空
        previous = {"salesman": "S9", "department": "CPI", "default_salesman": "S1", "default_department": "CPI"}
        self.assertEqual(C.staff({"salesman": "S2", "department": "BKK"}, "HQ", previous=previous),
                         {"salesman": "S9", "department": "BKK"})


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

    def test_a_read_without_offsets_keeps_the_credit_notes_a_newer_cc_listed(self):
        """11-28：旧版 CC 桌面版读回只有发票（没带 ``offsets``）：快照里已有的抵减单不当「已没有」删；带了的照常对齐。"""
        sr = {"docnum": "SR1", "rectyp": "5", "customer": "2100883", "net": "300", "received": "0", "remaining": "300"}
        iv = {"docnum": "IV1", "rectyp": "3", "customer": "2100883", "net": "1000", "received": "0", "remaining": "1000"}
        snapshot.replace("CO-1", BOOK["key"], [iv, sr], "2026-11-11T04:00:00+00:00", customer="2100883")
        snapshot.replace("CO-1", BOOK["key"], [iv], "2026-11-11T04:10:00+00:00", customer="2100883", offsets=False)
        self.assertEqual(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "SR1"}, "remaining"), Decimal("300.00"))
        snapshot.replace("CO-1", BOOK["key"], [iv], "2026-11-11T04:20:00+00:00", customer="2100883")
        self.assertIsNone(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "SR1"}, "name"))

    def test_an_older_whole_book_read_does_not_bring_back_an_invoice_a_newer_read_found_gone(self):
        """11-15：按客户读（04:00）发现 IV2 已收清删掉；晚到的整本读是 03:30 的、里面还有 IV2——不插回（之前会显示到下次读回）。
        写后回执把 IV1 收清（05:00）同理；比它们新的整本读里又有这张（重开 / 撤销），照常插回。"""
        snapshot.replace("CO-1", BOOK["key"], [{"docnum": "IV1", "customer": "2100883", "net": "1000",
                                                "received": "0", "remaining": "1000"}],
                         "2026-11-11T04:00:00+00:00", customer="2100883")
        self.assertIsNone(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV2"}, "name"))
        whole = [{"docnum": "IV1", "customer": "2100883", "net": "1000", "received": "0", "remaining": "1000"},
                 {"docnum": "IV2", "customer": "2100883", "net": "800", "received": "300", "remaining": "500"},
                 {"docnum": "IV3", "customer": "2100900", "net": "50", "received": "0", "remaining": "50"}]
        snapshot.replace("CO-1", BOOK["key"], whole, "2026-11-11T03:30:00+00:00")
        self.assertIsNone(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV2"}, "name"))
        snapshot.apply_balances(BOOK["key"], {"IV1": Decimal("0")}, "2026-11-11T05:00:00+00:00")
        snapshot.replace("CO-1", BOOK["key"], whole, "2026-11-11T04:30:00+00:00")
        self.assertIsNone(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV1"}, "name"))
        self.assertEqual(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV3"}, "remaining"), Decimal("50.00"))
        snapshot.replace("CO-1", BOOK["key"], whole, "2026-11-11T06:00:00+00:00")
        self.assertEqual(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV1"}, "remaining"), Decimal("1000.00"))
        self.assertEqual(frappe.db.get_value(snapshot.DOCTYPE, {"docnum": "IV2"}, "remaining"), Decimal("500.00"))

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

    def test_a_customer_with_no_open_invoice_reads_as_ready_after_the_minute_lands_it(self):
        """11-20 线上缺陷⑦：没有未收发票的客户，每分钟的 advance 落库后删了请求，人点「刷新」又新发一件、回「正在读取」，
        永远说不到「没有未收发票」。advance 刚落库的那件下一次算读好了（只算一次），再点就重读一次。"""
        self.assertEqual(snapshot.request("CO-1", BOOK, "NLT0314"), snapshot.READING)
        key = snapshot.request_key(BOOK["key"], "NLT0314")
        self.requests.finish(key, [], "2026-11-11T06:00:00+00:00")
        snapshot.advance()
        self.assertNotIn(key, self.requests.made)
        self.assertEqual(snapshot.request("CO-1", BOOK, "NLT0314"), snapshot.READY)
        self.assertEqual(snapshot.rows(BOOK["key"], "NLT0314"), [])
        self.assertNotIn(key, self.requests.made)  # 没有新发
        self.assertEqual(snapshot.request("CO-1", BOOK, "NLT0314"), snapshot.READING)  # 只算一次：再问就重读
        self.assertIn(key, self.requests.made)

    def test_choosing_the_company_in_line_warms_up_a_book_never_read_and_the_first_list_uses_it(self):
        """11-15 真机：第一次读客户发票要点「刷新」。选定公司时请电脑整本读一次（只给还没有快照的账套），选好客户时读好了就直接列出。"""
        self.as_user("a@x")
        service.warm("CO-1")
        self.assertNotIn(snapshot.request_key(BOOK["key"]), self.requests.made)  # 读过的账套不整本再读
        snapshot.replace("CO-1", BOOK["key"], [], "2026-11-11T04:00:00+00:00")
        service.warm("CO-1")
        key = snapshot.request_key(BOOK["key"])
        self.assertIn(key, self.requests.made)
        service.warm("CO-1")  # 在读的不重发
        self.assertEqual(len([k for k in self.requests.made if k == key]), 1)
        self.requests.finish(key, [{"docnum": "IV9", "customer": "2100900", "net": "70", "received": "0",
                                    "remaining": "70"}], "2026-11-11T05:00:00+00:00")
        found = service.open_invoices("CO-1", "2100900")
        self.assertEqual([row["docnum"] for row in found["items"]], ["IV9"])

    def test_an_ar_mark_change_refreshes_a_book_in_use_only(self):
        self.assertFalse(snapshot.marks_seen("CO-1", BOOK, {"mark": "m1"}))  # 旧版不报 ar_mark
        self.assertFalse(snapshot.marks_seen("CO-1", BOOK, {"ar_mark": "x1"}))  # 没人录过收款
        self.draft("10")
        self.assertTrue(snapshot.marks_seen("CO-1", BOOK, {"ar_mark": "x2"}))
        self.assertIn(snapshot.request_key(BOOK["key"]), self.requests.made)
        self.assertFalse(snapshot.marks_seen("CO-1", BOOK, {"ar_mark": "x2"}))  # 没变

    def test_a_thai_customer_code_makes_a_request_key_the_channel_accepts(self):
        # 11-12 三星实测：客户编号「นาย ทดสอบ」（泰文 + 不换行空格）拼进请求号被共用通道拒（只认字母数字），按客户读一次永远发不出去
        from pearnly_core.cc_desktop.wire import request_contracts
        for code in ("นาย\xa0ทดสอบ", "DT-จ004", "ว-0001", "2100883"):
            key = snapshot.request_key(BOOK["key"], code)
            self.assertEqual(request_contracts.request_key(key), key)
            self.assertEqual(snapshot.request_key(BOOK["key"], code), key)  # 同一位客户同一个号
        self.assertNotEqual(snapshot.request_key(BOOK["key"], "DT-จ004"), snapshot.request_key(BOOK["key"], "DT-จ005"))
        self.assertEqual(snapshot.request_key(BOOK["key"]), "erp_ar_open:" + BOOK["key"])  # 整本读不变

    def test_an_unknown_request_kind_reads_as_cc_update_needed(self):
        with mock.patch.object(self.requests, "create", side_effect=frappe.ValidationError("cc_desktop.request_invalid")):
            self.assertEqual(snapshot.request("CO-1", BOOK, "2100900"), snapshot.OUTDATED)



class SnapshotConcurrency(Base):
    """11-28 并发冲突 ④ C1 / A2 #7：一本账套一张发票只有一行、同一件读回只落一次、写后回执按加锁读的 net 算。"""

    def rows_of(self, docnum):
        return [row for row in S.table(snapshot.DOCTYPE).values() if row["docnum"] == docnum]

    def test_the_same_finished_read_lands_once_when_two_paths_take_it(self):
        # 网页 / LINE / CC 当场落（request）与每分钟落（advance）同时拿到同一件「读好了」：排他取走，后到的那路什么都不落
        key = snapshot.request_key(BOOK["key"], "2100900")
        self.assertEqual(snapshot.request("CO-1", BOOK, "2100900"), snapshot.READING)
        self.requests.finish(key, [{"docnum": "IV7", "customer": "2100900", "net": "70", "received": "0",
                                    "remaining": "70"}], "2026-11-11T06:00:00+00:00")
        made = self.requests.find(snapshot.KIND, key)
        self.assertEqual(snapshot.land("CO-1", BOOK["key"], made, "2100900", key), 1)
        self.assertIsNone(snapshot.land("CO-1", BOOK["key"], made, "2100900", key))  # 另一路已取走
        self.assertEqual(len(self.rows_of("IV7")), 1)

    def test_a_request_whose_read_another_path_just_took_says_reading_not_ready(self):
        # 另一路落的行这笔事务的普通读看不到：不说「读好了」（会列成没有未收发票），说「正在读取」
        key = snapshot.request_key(BOOK["key"], "2100900")
        snapshot.request("CO-1", BOOK, "2100900")
        self.requests.finish(key, [], "2026-11-11T06:00:00+00:00")
        with mock.patch.object(self.requests, "take", return_value=None):
            self.assertEqual(snapshot.request("CO-1", BOOK, "2100900"), snapshot.READING)

    def test_the_pending_mark_is_cleared_only_after_commit(self):
        key = snapshot.request_key(BOOK["key"], "2100900")
        snapshot.request("CO-1", BOOK, "2100900")
        self.requests.finish(key, [], "2026-11-11T06:00:00+00:00")
        snapshot.land("CO-1", BOOK["key"], self.requests.find(snapshot.KIND, key), "2100900", key)
        self.assertIn(key, S.hashes[snapshot.PENDING])
        frappe.db.rollback()  # 回滚：请求行与文件都还在（`requests.take`），每分钟照样再取
        self.assertIn(key, S.hashes[snapshot.PENDING])
        snapshot.request("CO-1", BOOK, "2100900")
        self.requests.finish(key, [], "2026-11-11T06:01:00+00:00")
        snapshot.land("CO-1", BOOK["key"], self.requests.find(snapshot.KIND, key), "2100900", key)
        frappe.db.commit()
        self.assertNotIn(key, S.hashes[snapshot.PENDING])

    def test_landing_holds_the_book_lock_until_commit_or_rollback_and_takes_it_once_per_transaction(self):
        frappe.db.commit()
        S.serial.clear()
        lock_key = snapshot.SERIAL + BOOK["key"]
        snapshot.replace("CO-1", BOOK["key"], [], "2026-11-11T06:00:00+00:00", customer="2100900")
        snapshot.apply_balances(BOOK["key"], {"IV1": Decimal("100")})
        self.assertEqual(S.serial, [(lock_key, "acquire")])  # 同一笔事务第二次不再拿
        frappe.db.commit()
        self.assertEqual(S.serial, [(lock_key, "acquire"), (lock_key, "release")])
        S.serial.clear()
        snapshot.apply_balances(BOOK["key"], {"IV1": Decimal("90")})
        frappe.db.rollback()
        self.assertEqual(S.serial, [(lock_key, "acquire"), (lock_key, "release")])

    def test_an_invoice_under_another_customer_is_updated_not_inserted_again(self):
        # Express 里这张改了客户（或两位客户只差空白）：按客户读到它时改那一行，一本账套一张发票只有一行
        snapshot.replace("CO-1", BOOK["key"], [{"docnum": "IV3", "customer": "2100883", "net": "50", "received": "0",
                                                "remaining": "40"}], "2026-11-11T06:00:00+00:00", customer="2100883")
        rows = self.rows_of("IV3")
        self.assertEqual([(row["customer"], row["remaining"]) for row in rows], [("2100883", Decimal("40.00"))])

    def test_an_insert_that_hits_the_unique_index_becomes_an_update(self):
        """另一路刚插了同一张（这一路的加锁读之后）：撞唯一索引退回保存点，按单号加锁读出那一行、旧的不盖新的。"""
        unique = type("UniqueValidationError", (ValidationError,), {})
        original = Doc.insert

        def racing(competitor_read_at):
            def insert(doc, ignore_permissions=False, set_name=None):
                if doc.doctype == snapshot.DOCTYPE and doc.get("docnum") == "IV8":
                    S.table(snapshot.DOCTYPE)["RACE"] = {"name": "RACE", "company": "CO-1", "book_key": BOOK["key"],
                                                          "docnum": "IV8", "customer": "2100900", "net": Decimal("80"),
                                                          "received": Decimal("0"), "remaining": Decimal("80"),
                                                          "read_at": datetime.fromisoformat(competitor_read_at)}
                    frappe.local.message_log = ["IV8 must be unique"]
                    raise unique("ERP Receivable Snapshot", "x", "Duplicate entry")
                return original(doc, ignore_permissions, set_name)
            return insert

        item = [{"docnum": "IV8", "customer": "2100900", "net": "80", "received": "30", "remaining": "50"}]
        with mock.patch.object(frappe, "UniqueValidationError", unique, create=True), \
                mock.patch.object(Doc, "insert", racing("2026-11-11 14:00:00")):
            frappe.local.message_log = []
            self.assertEqual(snapshot.replace("CO-1", BOOK["key"], item, "2026-11-11T06:00:00+00:00"), 0)
        self.assertEqual([row["remaining"] for row in self.rows_of("IV8")], [Decimal("80")])  # 那一路的读取（14:00）比这次（06:00 UTC = 13:00）新：不盖
        self.assertEqual(frappe.local.message_log, [])  # 「必须唯一」不带给用户
        del S.table(snapshot.DOCTYPE)["RACE"]
        with mock.patch.object(frappe, "UniqueValidationError", unique, create=True), \
                mock.patch.object(Doc, "insert", racing("2026-11-11 10:00:00")):
            self.assertEqual(snapshot.replace("CO-1", BOOK["key"], item, "2026-11-11T06:00:00+00:00"), 1)
        self.assertEqual([row["remaining"] for row in self.rows_of("IV8")], [Decimal("50.00")])

    def test_balances_after_a_write_use_the_locked_net(self):
        # 写后回执在 CC 的事务里、读视图早就建了：net 按加锁读取（另一路刚把 net 从 1000 改成 1200 并提交）
        S.table(snapshot.DOCTYPE)[self.rows_of("IV1")[0]["name"]]["net"] = Decimal("1200.00")
        S.locked.clear()
        with mock.patch.object(frappe.db, "get_value", side_effect=AssertionError("plain read")):
            snapshot.apply_balances(BOOK["key"], {"IV1": Decimal("700"), "IV2": Decimal("0")}, "2026-11-11T06:00:00+00:00")
        self.assertEqual(S.locked, [snapshot.DOCTYPE])
        row = self.rows_of("IV1")[0]
        self.assertEqual((row["remaining"], row["received"]), (Decimal("700"), Decimal("500.00")))
        self.assertEqual(self.rows_of("IV2"), [])


class SnapshotUniquePatch(unittest.TestCase):
    """补丁 `v1_35_receivable_snapshot_unique`：建唯一索引前每组重复留读取时刻最新的一行，删其余并记日志；可重复执行。"""

    def test_duplicates_keep_the_latest_read_and_a_second_run_changes_nothing(self):
        import importlib

        patch = importlib.import_module("pearnly_erp_portal.patches.v1_35_receivable_snapshot_unique")
        rows = {"A": ("BK", "IV1", "2026-11-11 03:00:00"), "B": ("BK", "IV1", "2026-11-11 05:00:00"),
                "C": ("BK", "IV1", None), "D": ("BK", "IV2", "2026-11-11 05:00:00"), "E": ("BK2", "IV1", "2026-11-11 01:00:00")}

        def sql(query, values=()):
            groups = {}
            for book_key, docnum, _at in rows.values():
                groups[(book_key, docnum)] = groups.get((book_key, docnum), 0) + 1
            return [key for key, count in groups.items() if count > 1]

        def sql_list(query, values=()):
            # 同补丁的排序：读取时刻空的排最后，其余新的在前
            found = [(name, at) for name, (book_key, docnum, at) in rows.items() if (book_key, docnum) == tuple(values)]
            dated = sorted((pair for pair in found if pair[1]), key=lambda pair: pair[1], reverse=True)
            return [name for name, _ in dated] + [name for name, at in found if not at]

        def delete(doctype, filters):
            for name in filters["name"][1]:
                rows.pop(name)

        logged, unique = [], []
        module = sys.modules[
            "pearnly_erp_portal.pearnly_erp_portal.doctype.erp_receivable_snapshot.erp_receivable_snapshot"] = types.ModuleType("x")
        module.on_doctype_update = lambda: unique.append(1)
        try:
            with mock.patch.object(frappe.db, "sql", side_effect=sql), \
                    mock.patch.object(frappe.db, "sql_list", side_effect=sql_list, create=True), \
                    mock.patch.object(frappe.db, "table_exists", return_value=True, create=True), \
                    mock.patch.object(frappe.db, "delete", side_effect=delete), \
                    mock.patch.object(frappe, "log_error", side_effect=lambda **kw: logged.append(kw)):
                patch.execute()
                self.assertEqual(sorted(rows), ["B", "D", "E"])
                self.assertEqual(len(logged), 1)
                self.assertIn("2", logged[0]["message"])
                patch.execute()
                self.assertEqual(sorted(rows), ["B", "D", "E"])
                self.assertEqual(len(logged), 1)
                self.assertEqual(len(unique), 2)
        finally:
            sys.modules.pop("pearnly_erp_portal.pearnly_erp_portal.doctype.erp_receivable_snapshot.erp_receivable_snapshot", None)


class WebPage(Base):
    def setUp(self):
        super().setUp()
        for index in range(5):
            made = self.draft("10", user="a@x" if index % 2 else "b@x")
            if index == 4:
                service.release(made["name"], 1, "line:evt")
        self.as_user("a@x")  # 用户 11-14：勾了收款的人看本公司全部（不分谁录的）

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
        for user in ("sales@x", "nobody@x"):  # 只有销售岗位、没勾收款的不再看得到（用户 11-14）
            self.as_user(user)
            with self.assertRaises(PermissionError_):
                records.page({})

    def test_a_missing_or_other_company_collection_reads_as_not_found(self):
        # 11-15 线上：打开不存在的收款报「没有权限」。不存在的、别家公司的一样说找不到（不透露编号在不在）；没收款权限的照旧拒
        name = records.rows({}, limit=1)[0]["name"]
        for missing in ("COL-NOPE", ""):
            with self.assertRaises(ValidationError) as caught:
                records.detail(missing)
            self.assertNotIsInstance(caught.exception, PermissionError_)
        self.as_user("other@x")  # 有收款权限、只能用 CO-2，这一笔是 CO-1 的
        with self.assertRaises(ValidationError):
            records.detail(name)
        self.as_user("nobody@x")
        with self.assertRaises(PermissionError_):
            records.detail(name)

    def test_the_page_is_declared_with_the_view_roles_and_no_write_entries(self):
        hooks = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/hooks.py").read_text(encoding="utf-8")
        # 用户 11-14：侧栏「收款」看「收款」功能、只给收款岗位与管理员；「我的收款」去掉（旧地址留一页转过来，不进菜单）
        self.assertIn("('erp.nav.collections', 'pearnly-collections', 'receipt-text', 'collection')", hooks)
        self.assertIn('pearnly_navigation[0]["permissions"]["pearnly-collections"] = {"any_role": [_rules.COLLECTOR_ROLE, _rules.ADMIN_ROLE]}', hooks)
        self.assertNotIn("erp.nav.myCollections", hooks)
        self.assertNotIn("'pearnly-team', 'pearnly-my-collections'", hooks)
        self.assertIn('"pearnly-collections": ["Export", "New"],', hooks)  # 11-14：能录收款的人有「新建」（外壳白名单）
        self.assertIn('"kinds": ["express_masters", "express_marks", "express_doc_check", "express_ar_open"]', hooks)
        page = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/collections.js").read_text(encoding="utf-8")
        # 11-14 用户：网页也能新建。「新建」只在服务端说能录收款时出现（`records.page` 的 can_create）
        self.assertIn("out?.can_create && !creating", page)
        self.assertIn("p.collection_new(", page)
        self.assertEqual(page.count("page.set_primary_action"), 1)  # 页头只有「新建」（撤回在弹窗里）
        self.assertNotIn("limit: 0", page)


# 同期两份与跨年（A10 / A11，11-12）：选账套用真规则 `express.target.pick`，电脑报来的账套换成下面几本
TEST26 = dict(BOOK, key="b" * 40, path=r"\\Accserver\d$\ACCOUNT\69EXP\TEST", label="TEST", period_start="2026-01-01",
              period_end="2026-12-31", next_end="2027-12-31")
TEST26_COPY = dict(TEST26, key="c" * 40, path=r"C:\PearnlyExpress\TEST", root=r"C:\PearnlyExpress")
TEST25 = dict(BOOK, key="d" * 40, path=r"\\Accserver\d$\ACCOUNT\69EXP\TEST25", label="TEST25", period_start="2025-01-01",
              period_end="2025-12-31", next_end="2026-12-31")
OPEN = [{"docnum": "IV1", "rectyp": "3", "docdat": "2025-12-20", "customer": "2100883", "salesman": "S1",
         "department": "CPI", "net": "1000.00", "received": "0.00", "remaining": "1000.00"}]


class TwoBooks(Base):
    """同期两本（69EXP\\TEST 与它的 C 盘副本）；``self.books`` 改了就是电脑此刻报来的。只有准备，没有用例。"""

    def setUp(self):
        super().setUp()
        self.books = [TEST26, TEST26_COPY]
        for patch in (mock.patch("pearnly_erp_portal.express.target.pick", side_effect=REAL_PICK),
                      mock.patch("pearnly_core.cc_desktop.books.for_company", side_effect=lambda tax, branch: self.books)):
            patch.start()
            self.addCleanup(patch.stop)
        for book in (TEST26, TEST26_COPY, TEST25):
            snapshot.replace("CO-1", book["key"], OPEN, "2026-11-11T03:00:00+00:00")


class Books(TwoBooks):
    def test_two_books_for_the_same_year_are_asked_once_answered_and_remembered(self):
        with self.assertRaises(C.CollectionError) as caught:
            service.open_invoices("CO-1", "2100883")
        self.assertEqual(caught.exception.code, "express.account_set_several")
        asked = service.book_options("CO-1")
        self.assertEqual([row["code"] for row in asked["options"]], [TEST26["key"], TEST26_COPY["key"]])
        self.assertEqual(service.choose_book("CO-1", TEST26_COPY["key"]), {"chosen": TEST26_COPY["key"]})
        self.assertIsNone(service.book_options("CO-1"))  # 记住了：不再问
        self.assertEqual(service.open_invoices("CO-1", "2100883")["book"], TEST26_COPY["key"])
        made = self.draft("100")
        self.assertEqual(S.table("ERP Collection")[made["name"]]["book_key"], TEST26_COPY["key"])
        # 与 ERP 单据同一份记忆（按公司 × 会计年度）：网页选过的这里照用，反之亦然
        from pearnly_erp_portal.express import choices

        self.assertEqual(choices.chosen("CO-1", choices.ACCOUNT_SET, "year:2026-01-01"), TEST26_COPY["key"])

    def test_choosing_a_book_that_is_not_offered_is_refused(self):
        with self.assertRaises(frappe.ValidationError):
            service.choose_book("CO-1", TEST25["key"])
        self.books = [TEST26]
        self.assertIsNone(service.book_options("CO-1"))  # 只有一本：不问
        self.assertEqual(service.choose_book("CO-1", TEST26["key"]), {"chosen": TEST26["key"]})

    def test_the_edit_dialog_uses_the_drafts_own_book_not_the_book_of_its_date(self):
        from pearnly_erp_portal.collection import api

        self.books = [TEST26]
        made = self.draft("100")
        self.books = [TEST26, TEST26_COPY]  # 之后又报来同期一本：按日期再选会停在「同期两份」
        opened = api.api_collection_card(made["name"])
        self.assertEqual((opened["available"], [row["prefix"] for row in opened["methods"]["transfers"]][:1]),
                         ({"IV1": "1000.00"}, ["TR"]))


    def test_a_day_outside_every_period_asks_for_the_collection_date(self):
        """11-15 用户：今天不在已连接账套的期间里不拒，问收款日期（同 Express 里把工作日期改进期间再录）；一本都没连才说没有账套。
        11-13 testerp 只报了 2024 年度的账套时，原来只回一句「不在已连接账套的期间内」。"""
        from pearnly_erp_portal.collection import texts

        nlt = dict(BOOK, period_start="2024-01-01", period_end="2024-12-31", next_end="2025-12-31")
        self.books = [nlt]
        with self.assertRaises(C.CollectionError) as caught:
            service.open_invoices("CO-1", "2100883")
        self.assertEqual(caught.exception.code, service.DAY_NEEDED)
        self.assertEqual([(row["start"], row["end"], row["last"]) for row in caught.exception.detail["periods"]],
                         [("2024-01-01", "2025-12-31", "2024-12-31")])
        self.assertEqual(texts.explain(caught.exception), "Collection Date · Accounting period: 2024-01-01–2025-12-31 NLTTEST")
        self.books = []
        with self.assertRaises(C.CollectionError) as caught:
            service.open_invoices("CO-1", "2100883")
        self.assertEqual(caught.exception.code, "express.account_set_missing")
        self.assertEqual(texts.explain(caught.exception), "This company has no connected Express account set")


OLD = dict(BOOK, key="e" * 40, path=r"\\Accserver\d$\ACCOUNT\70EXP\OLD", label="OLD", period_start="2024-01-01",
           period_end="2024-12-31", next_end="2025-12-31")


class CollectionDate(TwoBooks):
    """今天不在任何一本账套的期间里（11-15 线上 testerp นิว ลิงค์ 只接了 2024–2025 那本）：不拒，问收款日期，账套跟着日期走；
    人选的日期一路写到载荷（放行不改回当天）；改草稿只能在冻结那本的期间里；当天在期间里流程不变。"""

    def setUp(self):
        super().setUp()
        self.books = [OLD]
        snapshot.replace("CO-1", OLD["key"], OPEN, "2026-11-11T03:00:00+00:00")
        self.as_user("a@x")

    def test_today_outside_every_period_asks_with_the_ranges(self):
        asked = service.day_options("CO-1")
        self.assertEqual(asked["day"], "2026-11-11")
        self.assertEqual([(row["start"], row["end"], row["last"], row["short"]) for row in asked["periods"]],
                         [("2024-01-01", "2025-12-31", "2024-12-31", "OLD")])
        self.assertIsNone(service.day_options("CO-1", "2024-12-31"))
        self.assertIsNone(service.day_options("CO-1", "31/12/2567"))  # 泰国写法、佛历
        self.assertEqual(service.day_options("CO-1", "2026-01-05")["periods"][0]["key"], OLD["key"])  # 期间外：再问
        self.assertEqual(service.day_options("CO-1", "abc")["day"], "")

    def test_today_inside_a_period_does_not_ask(self):
        self.books = [OLD, TEST26]
        self.assertIsNone(service.day_options("CO-1"))
        self.assertEqual(service.open_invoices("CO-1", "2100883")["book"], TEST26["key"])
        # 收老账套的欠款：选老账套期间里的日期，账套跟着换
        self.assertEqual(service.open_invoices("CO-1", "2100883", "2024-12-31")["book"], OLD["key"])

    def test_the_chosen_date_is_written_as_chosen(self):
        made = service.create_draft("CO-1", "2100883", [{"docnum": "IV1", "amount": "100"}], cash="100",
                                    doc_date="2024-12-31")
        row = S.table("ERP Collection")[made["name"]]
        self.assertEqual((row["book_key"], str(row["doc_date"]), row["date_chosen"]), (OLD["key"], "2024-12-31", 1))
        self.assertEqual(service.release(made["name"], made["version"], "line:evt-1")["status"], "released")
        job = self.jobs.rows["J-" + made["name"]]
        self.assertEqual(job["payload"]["doc_date"], "2024-12-31")
        self.assertEqual(str(S.table("ERP Collection")[made["name"]]["doc_date"]), "2024-12-31")

    def test_without_a_date_the_draft_asks(self):
        with self.assertRaises(C.CollectionError) as caught:
            service.create_draft("CO-1", "2100883", [{"docnum": "IV1", "amount": "100"}], cash="100")
        self.assertEqual(caught.exception.code, service.DAY_NEEDED)
        with self.assertRaises(C.CollectionError) as caught:
            service.methods("CO-1")
        self.assertEqual(caught.exception.code, service.DAY_NEEDED)
        self.assertTrue(service.methods("CO-1", "2024-06-30")["cash"])

    def test_editing_the_date_stays_inside_the_drafts_book(self):
        made = service.create_draft("CO-1", "2100883", [{"docnum": "IV1", "amount": "100"}], cash="100",
                                    doc_date="2024-12-31")
        card = service.update_draft(made["name"], made["version"], [{"docnum": "IV1", "amount": "100"}], cash="100",
                                    doc_date="2025-03-01")
        self.assertEqual(card["doc_date"], "2025-03-01")
        with self.assertRaises(C.CollectionError) as caught:
            service.update_draft(made["name"], card["version"], [{"docnum": "IV1", "amount": "100"}], cash="100",
                                 doc_date="2026-11-11")
        self.assertEqual(caught.exception.code, service.DAY_NEEDED)
        self.assertEqual([row["key"] for row in caught.exception.detail["periods"]], [OLD["key"]])
        self.assertEqual([(row["start"], row["end"]) for row in service.edit_choices(made["name"])["periods"]],
                         [("2024-01-01", "2025-12-31")])

    def test_a_default_date_still_moves_to_the_release_day(self):
        self.books = [TEST26]
        made = service.create_draft("CO-1", "2100883", [{"docnum": "IV1", "amount": "100"}], cash="100")
        self.assertEqual(S.table("ERP Collection")[made["name"]]["date_chosen"], 0)
        with mock.patch.object(service, "nowdate", lambda: "2026-11-12"):
            service.release(made["name"], made["version"], "line:evt-1")
        self.assertEqual(str(S.table("ERP Collection")[made["name"]]["doc_date"]), "2026-11-12")

    def test_period_locked_reads_as_the_express_words(self):
        from pearnly_erp_portal.collection import texts

        self.assertEqual(texts.reason("[PERIOD_LOCKED] accounting period of 2024-12-31 is locked in Express"),
                         "express.reason.periodLocked")
        self.assertEqual(texts.reason("[X] other"), "other")
        self.assertEqual(texts.reason("[deposit_account_missing] deposits 300 taken off but no deposit account"),
                         "Deposit is not set up for this account set")


class Explained(unittest.TestCase):
    def test_every_refusal_a_collector_can_meet_has_its_own_words(self):
        # 11-12 前账套、公司、客户、张数上限这几种只说「Could not record this collection」
        from pearnly_erp_portal.collection import texts

        codes = [value for key, value in vars(C).items() if key.isupper() and isinstance(value, str)
                 and value.startswith("collection.") and value != C.CONSENT_MISSING]
        codes += [service.NO_COMPANY, service.NO_CUSTOMER, service.CC_UPDATE, service.VOUCHER_INVALID,
                  service.LEDGER_NEEDED, service.DAY_NEEDED, "express.account_set_several", "express.company_tax_missing",
                  "express.account_set_missing", "express.masters_reading"]
        for code in codes:
            self.assertNotEqual(texts.explain(C.CollectionError(code, {})), "Could not record this collection", code)
        self.assertEqual(texts.explain(C.CollectionError(C.INVOICES_TOO_MANY)), "At most 50 invoices")


def _wait_queries_present():
    from pearnly_core.cc_desktop import jobs

    return all(callable(getattr(jobs, name, None)) for name in ("express_wait_many", "book_express_wait"))


class ExpressWaitContract(unittest.TestCase):
    """约定本身（express-wait-display 11-12）：那支合入集成分支后这里才跑，钉住收款用到的两个只读查询的样子。"""

    @unittest.skipUnless(_wait_queries_present(), "express-wait-display 还没合入（收款排在它后面合）")
    def test_the_shared_channel_answers_the_two_queries_collections_use(self):
        import inspect

        from pearnly_core.cc_desktop import jobs

        self.assertEqual(list(inspect.signature(jobs.express_wait_many).parameters)[:1], ["names"])
        self.assertEqual(list(inspect.signature(jobs.book_express_wait).parameters)[:1], ["book_key"])
        self.assertEqual(jobs.express_wait_many([]), {})
        self.assertEqual(jobs.book_express_wait(""), {"waiting": False, "since": None})


class ExpressWaiting(Base):
    """「我的收款」里在等 Express 关闭的那几笔（约定：`cc_desktop.jobs.express_wait_many` / `book_express_wait`，
    express-wait-display 11-12；那支未合入前用替身）。"""

    def setUp(self):
        super().setUp()
        self.waiting = set()
        for patch in (mock.patch("pearnly_core.cc_desktop.jobs.express_wait_many", create=True,
                                 side_effect=lambda names, moment=None: {n: "2026-11-11T05:00:00" for n in names
                                                                         if n in self.waiting}),
                      mock.patch("pearnly_core.cc_desktop.jobs.book_express_wait", create=True,
                                 side_effect=lambda key, moment=None: {"waiting": bool(self.waiting), "since": None})):
            patch.start()
            self.addCleanup(patch.stop)

    def released(self):
        made = self.draft("100")
        service.release(made["name"], 1, "line:evt")
        return made["name"]

    def test_my_list_marks_only_queued_collections_waiting_for_express(self):
        first, second = self.released(), self.released()
        self.waiting = {"J-" + first}
        self.jobs.report("J-" + first, "queued", reason="ACCOUNT_BUSY_LOCKED")
        self.as_user("a@x")
        marked = {row["name"]: row["express_wait"] for row in service.listed("CO-1")["items"]}
        self.assertEqual(marked, {first: True, second: False})
        self.assertEqual(S.table("ERP Collection")[first]["reason"], "")  # 原因码不抄到收款记录
        self.jobs.report("J-" + first, "success", docnum="RE1")
        self.assertFalse({row["name"]: row["express_wait"] for row in service.listed("CO-1")["items"]}[first])

    def test_the_page_shows_the_existing_sentence_for_waiting_rows_only(self):
        page = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/collections.js").read_text(encoding="utf-8")
        self.assertIn('row?.status === "queued" && row?.express_wait ? __("express.error.ACCOUNT_BUSY_LOCKED") : ""', page)
        self.assertIn("title: (value, row) => waiting(row)", page)  # 列表悬浮说明
        self.assertIn("[status_label(data.status, data.reason), waiting(data)]", page)  # 手机没有悬浮：点开后的内容里也有

    def test_a_card_link_to_a_record_that_is_no_longer_a_draft_says_the_card_expired(self):
        # 卡片有效期 A6：LINE 卡【编辑】打开的那一笔已放行 / 撤回 / 被新卡作废：先说此卡片已失效（同 DMS 编辑页）；列表里点开的照旧只列状态
        page = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/collections.js").read_text(encoding="utf-8")
        self.assertIn('...(from_card ? [{fieldtype: "HTML", fieldname: "expired",', page)
        self.assertIn('__("This card has expired. Use the latest card in LINE.")', page)
        self.assertIn("await open(rest[0], true)", page)
        self.assertIn('if (data.status === "draft") return edit(name);', page)  # 还是草稿：直接改
        self.assertIn("on_open: row => void settle(open(row.name))", page)
        # 旧地址「我的收款」（已发出的 LINE 卡）原样转到这一页的那一笔
        legacy = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/page/pearnly_my_collections/"
                  "pearnly_my_collections.js").read_text(encoding="utf-8")
        self.assertIn('frappe.set_route(["pearnly-collections", ...frappe.get_route().slice(1)]);', legacy)
        self.assertIn('frappe.show_not_permitted("pearnly-my-collections");', legacy)  # 进不了「收款」的不转
        terms = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/i18n/messages.tsv").read_text(encoding="utf-8")
        self.assertIn("This card has expired. Use the latest card in LINE.\t此卡片已失效，请使用 LINE 中最新的卡片。", terms)

    def test_the_opened_record_and_the_release_say_it_too(self):
        from pearnly_erp_portal.collection import api

        name = self.released()
        self.waiting = {"J-" + name}
        self.assertTrue(api.api_collection_card(name)["card"]["express_wait"])
        self.assertTrue(service.express_waiting(name))
        self.waiting = set()
        self.assertFalse(service.express_waiting(name))


class StaleFacts(Base):
    """资料是旧版 CC 读的（缺收款项，11-15 线上 testerp TEST 停在 10-27）：请电脑重读、回「正在读取」，不说「需更新」；
    够得到这本的在线电脑都比带收款的版本旧才说「需更新」；重读落好就照常列方式。三处共用 `facts.stale_for_collection`。"""

    OLD = {"customers": [], "salesmen_missing": True}

    def setUp(self):
        super().setUp()
        import pearnly_erp_portal.express as express_pkg

        self.current = dict(self.OLD)
        self.ensured = []
        self.reason = None
        fake = types.SimpleNamespace(ensure=lambda company, book: self._ensure(company, book))
        for patch in (mock.patch("pearnly_erp_portal.express.facts.load", side_effect=lambda key: self.current),
                      mock.patch.dict(sys.modules, {"pearnly_erp_portal.express.masters": fake}),
                      mock.patch.object(express_pkg, "masters", fake, create=True),
                      mock.patch.object(service.requests, "waiting",
                                        side_effect=lambda kind, key, actor=None: self.reason and {"reason": self.reason})):
            patch.start()
            self.addCleanup(patch.stop)
        self.as_user("a@x")

    def _ensure(self, company, book):
        self.ensured.append((company, book["key"]))

    def test_the_rule_is_one_place(self):
        self.assertTrue(facts.stale_for_collection(None))
        self.assertTrue(facts.stale_for_collection({"customers": []}))
        self.assertFalse(facts.stale_for_collection({"receipt_types": []}))

    def test_old_facts_ask_the_computer_to_read_again_instead_of_update(self):
        with self.assertRaises(C.CollectionError) as caught:
            service.methods("CO-1")
        self.assertEqual(caught.exception.code, express_contracts.READING)
        self.assertEqual(self.ensured, [("CO-1", BOOK["key"])])

    def test_update_needed_only_when_every_computer_is_too_old(self):
        self.reason = "update_needed"
        with self.assertRaises(C.CollectionError) as caught:
            service.methods("CO-1")
        self.assertEqual(caught.exception.code, service.CC_UPDATE)
        self.assertEqual(self.ensured, [])

    def test_the_reread_lands_and_methods_list(self):
        self._ensure = lambda company, book: self.current.update(FACTS)
        found = service.methods("CO-1")
        self.assertEqual({item["prefix"] for item in found["transfers"]}, LISTED)

    def test_old_salesmen_and_labels_are_not_used(self):
        self.current = {"salesmen": [{"code": "S1", "name": "x"}]}
        self.assertIsNone(service._choices(BOOK["key"])["salesmen"])


if __name__ == "__main__":
    unittest.main()
