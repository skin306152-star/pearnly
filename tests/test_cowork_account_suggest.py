"""科目建议 B + C（用户 2026-10-13 批准，10-15 施工）：要人选费用科目时先给最多 2 个建议，仍要会计员确认一次。

- 纯匹配（`push/express/account_suggest.py`）：只挑这套账科目表里的费用类科目（两种真实编号形状 ``5320-01`` 与
  ``53-20-01-00``，没报类型的按编号大类）；会计员在对话里说的类别排第一，其次这家供应商近 12 个月常用的，再次名称与大类；
- 历史汇总的校验与按卖方取（`readback/history_rules.py`）；
- 历史的读取与存放（`readback/history.py`）：按需请求、按连接 + 账套存、一天内不重读、电脑离线 / 旧版安静降级、读失败
  一小时内不再请；
- 推送服务把建议放进要人选的那一项（`push/application/service.py` 的 `_suggest`），经钩子取历史、不导入 readback。
不连库、不依赖 Frappe（最小替身）。
"""
import gzip
import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"
sys.path.insert(0, str(ROOT / "apps/pearnly_cowork"))
sys.path.insert(0, str(ROOT / "tests"))
for _name in [n for n in sys.modules if n.split(".")[0] == "pearnly_cowork"]:
    del sys.modules[_name]

from pearnly_cowork.push.express import account_suggest as S  # noqa: E402
from pearnly_cowork.push.express import choices as choice_rules  # noqa: E402
from pearnly_cowork.readback import contracts as readback_contracts  # noqa: E402
from pearnly_cowork.readback import history_rules as R  # noqa: E402

# Express 空白模板「ข้อมูลเปล่า」的编号形状（1111-00）与事务所模板（11-01-00-00），名称照真实科目表的写法
BLANK = [
    {"code": "1111-00", "name": "เงินสด", "type": "1"},
    {"code": "1150-01", "name": "วัสดุสำนักงานคงเหลือ", "type": "1"},  # 资产：名称对得上也不建议
    {"code": "2120-00", "name": "เจ้าหนี้การค้า", "type": "2"},
    {"code": "5310-01", "name": "ค่าไฟฟ้า", "type": "5"},
    {"code": "5310-02", "name": "ค่าน้ำประปา", "type": "5"},
    {"code": "5310-03", "name": "ค่าโทรศัพท์และอินเตอร์เน็ต", "type": "5"},
    {"code": "5320-01", "name": "ค่าเครื่องเขียนแบบพิมพ์", "type": "5"},
    {"code": "5330-01", "name": "ค่าน้ำมันเชื้อเพลิง", "type": "5"},
    {"code": "5340-01", "name": "ค่าซ่อมแซมและบำรุงรักษา", "type": "5"},
    {"code": "5350-01", "name": "ค่าส่งเสริมการขาย", "type": "5"},
    {"code": "5390-99", "name": "ค่าใช้จ่ายเบ็ดเตล็ด", "type": "5"},
]
FIRM = [
    {"code": "11-01-00-00", "name": "เงินสดและรายการเทียบเท่าเงินสด", "type": "1"},
    {"code": "53-01-00-00", "name": "ค่าเช่าสำนักงาน", "type": "5"},
    {"code": "53-02-00-00", "name": "ค่าไฟฟ้า ค่าน้ำ", "type": "5"},
    {"code": "53-05-02-00", "name": "ค่าน้ำมัน", "type": "5"},
    {"code": "53-06-00-00", "name": "ค่าขนส่ง", "type": "5"},
    {"code": "53-08-00-00", "name": "ค่าธรรมเนียมวิชาชีพ ค่าทำบัญชี", "type": "5"},
    {"code": "53-20-01-00", "name": "วัสดุสิ้นเปลืองและเครื่องเขียน", "type": "5"},
    {"code": "53-30-00-00", "name": "Software subscription", "type": "5"},
]


def codes(found):
    return [(row["code"], row["source"]) for row in found]


class ExpenseAccounts(unittest.TestCase):
    def test_only_expense_accounts_by_type_or_by_the_leading_digit(self):
        self.assertTrue(S.expense({"code": "5320-01", "type": "5"}))
        self.assertTrue(S.expense({"code": "53-20-01-00", "type": "5"}))
        self.assertFalse(S.expense({"code": "5320-01", "type": "1"}))  # 类型说了不是费用就不是
        self.assertTrue(S.expense({"code": "53-20-01-00"}))  # 旧版没报类型：按编号大类
        self.assertTrue(S.expense({"code": "5320-01", "type": ""}))
        self.assertFalse(S.expense({"code": "11-04-00-00"}) or S.expense({"code": "4110-01"}) or S.expense({}))

    def test_an_asset_with_a_matching_name_is_never_suggested(self):
        found = S.suggest({"category": "วัสดุสำนักงาน"}, accounts=BLANK, hint="วัสดุสำนักงาน")
        self.assertNotIn("1150-01", [row["code"] for row in found])


class Suggest(unittest.TestCase):
    def test_the_users_note_comes_first_then_this_suppliers_usual_account(self):
        """第 4、6 条（2026-10-13 录制）：会计员写了「อันนี้ของดาวเหนือ ค่าเครื่องเขียน」，CC 仍问科目。"""
        fields = {"seller_name": "ศรีกรุง เครื่องเขียน", "items": [{"name": "ปากกาลูกลื่น"}]}
        history = [{"code": "5390-99", "docs": 7, "last": "2026-09-30"}]
        found = S.suggest(fields, accounts=BLANK, history=history, hint="ค่าเครื่องเขียน")
        self.assertEqual(codes(found), [("5320-01", "hint"), ("5390-99", "history")])
        self.assertEqual(found[0]["name"], "ค่าเครื่องเขียนแบบพิมพ์")
        self.assertIn("ค่าเครื่องเขียน", found[0]["reason"])
        self.assertIn("7", found[1]["reason"])
        # 没有提示：历史在前，名称其次
        self.assertEqual(codes(S.suggest(fields, accounts=BLANK, history=history)),
                         [("5390-99", "history"), ("5320-01", "name")])

    def test_the_firm_chart_shape_works_the_same(self):
        found = S.suggest({"seller_name": "ร้านเครื่องเขียน", "category": "เครื่องเขียน"}, accounts=FIRM)
        self.assertEqual(codes(found), [("53-20-01-00", "name")])
        found = S.suggest({}, accounts=FIRM, hint="ค่าทำบัญชีเดือนกันยายน")
        self.assertEqual(codes(found), [("53-08-00-00", "hint")])
        found = S.suggest({"seller_name": "Kerry Express (Thailand)", "items": [{"name": "Delivery fee"}]},
                          accounts=FIRM)
        self.assertEqual(codes(found), [("53-06-00-00", "name")])
        found = S.suggest({"items": [{"name": "Microsoft 365 Business subscription"}]}, accounts=FIRM)
        self.assertEqual(codes(found), [("53-30-00-00", "name")])

    def test_utilities_are_told_apart(self):
        power = S.suggest({"seller_name": "การไฟฟ้าส่วนภูมิภาค", "category": "ค่าไฟฟ้า"}, accounts=BLANK)
        self.assertEqual(codes(power)[0], ("5310-01", "name"))
        water = S.suggest({"seller_name": "การประปาส่วนภูมิภาค", "category": "ค่าน้ำ"}, accounts=BLANK)
        self.assertEqual(codes(water)[0], ("5310-02", "name"))
        self.assertNotIn("5330-01", [row["code"] for row in water])
        # 油费（ค่าน้ำมัน 里有 ค่าน้ำ）不当成水费；促销（ค่าส่งเสริม 里有 ค่าส่ง）不当成运费
        fuel = S.suggest({"seller_name": "บางจาก", "category": "ค่าน้ำมัน", "items": [{"name": "ไฮดีเซล S"}]},
                         accounts=BLANK)
        self.assertEqual(codes(fuel), [("5330-01", "name")])
        fuel = S.suggest({"category": "ค่าน้ำมัน"}, accounts=FIRM)
        self.assertEqual(codes(fuel), [("53-05-02-00", "name")])
        self.assertEqual(S.suggest({"category": "ค่าส่งของ"}, accounts=BLANK), [])
        phone = S.suggest({"seller_name": "TRUE INTERNET CORPORATION", "category": "ค่าอินเทอร์เน็ต"}, accounts=BLANK)
        self.assertEqual(codes(phone), [("5310-03", "name")])

    def test_english_words_match_whole_words_only(self):
        accounts = [{"code": "5100", "name": "Rent", "type": "5"}, {"code": "5200", "name": "Repairs", "type": "5"}]
        self.assertEqual(S.suggest({"seller_name": "Parent Holdings Co., Ltd.", "category": "current"},
                                   accounts=accounts), [])
        self.assertEqual(codes(S.suggest({"category": "office rent"}, accounts=accounts)), [("5100", "name")])

    def test_history_only_offers_accounts_still_in_the_chart_and_at_most_two(self):
        history = [{"code": "5999-99", "docs": 9, "last": "2026-09-01"},  # 科目表里已经没有
                   {"code": "1111-00", "docs": 8, "last": "2026-09-01"},  # 不是费用科目
                   {"code": "5310-01", "docs": 3, "last": "2026-08-01"},
                   {"code": "5340-01", "docs": 2, "last": "2026-08-01"},
                   {"code": "5320-01", "docs": 1, "last": "2026-08-01"}]
        found = S.suggest({}, accounts=BLANK, history=history)
        self.assertEqual(codes(found), [("5310-01", "history"), ("5340-01", "history")])
        self.assertEqual(len(S.suggest({}, accounts=BLANK, history=history, limit=5)), 3)

    def test_nothing_matching_gives_nothing_and_ask_once_as_before(self):
        self.assertEqual(S.suggest({"seller_name": "ร้านสมใจ", "category": "อาหาร"}, accounts=BLANK), [])
        self.assertEqual(S.suggest({"category": "ไฟฟ้า"}, accounts=[]), [])
        self.assertEqual(S.suggest(None, accounts=BLANK, hint="  "), [])
        # 泛词（ค่าใช้จ่าย、บริการ、บริษัท）不算对上
        self.assertEqual(S.suggest({"seller_name": "บริษัท บริการ จำกัด", "category": "ค่าใช้จ่าย"},
                                   accounts=BLANK), [])

    def test_a_note_matching_several_accounts_puts_the_one_this_supplier_uses_first(self):
        accounts = [{"code": "5331", "name": "ค่าน้ำมันรถยนต์", "type": "5"},
                    {"code": "5332", "name": "ค่าน้ำมันเครื่องจักร", "type": "5"}]
        found = S.suggest({}, accounts=accounts, history=[{"code": "5332", "docs": 2, "last": "2026-09-01"}],
                          hint="ค่าน้ำมัน")
        self.assertEqual(codes(found), [("5332", "hint"), ("5331", "hint")])


def history_raw(**override):
    data = {"version": 1, "since": "2025-10-15", "read_at": "2026-10-15T01:00:00Z", "account_set": "DN",
            "excluded": ["1111-00"], "documents": 4,
            "suppliers": [
                {"code": "S001", "name": "ศรีกรุง เครื่องเขียน", "tax_id": "0105556012345", "branch": "00000",
                 "documents": 3, "accounts": [{"code": "5320-01", "docs": 3, "last": "2026-09-30"}]},
                {"code": "S001-2", "name": "ศรีกรุง เครื่องเขียน (สาขา 2)", "tax_id": "0105556012345",
                 "branch": "00002", "documents": 1, "accounts": [{"code": "5390-99", "docs": 1, "last": "2026-10-01"},
                                                                  {"code": "5320-01", "docs": 1, "last": "2026-10-02"}]},
                {"code": "S009", "name": "ร้านป้าแดง", "tax_id": "", "branch": "00000", "documents": 2,
                 "accounts": [{"code": "5340-01", "docs": 2, "last": "2026-08-01"}]},
                {"code": "S010", "name": "ไม่มีบัญชี", "tax_id": "", "accounts": []},
            ], "diagnostics": {}}
    data.update(override)
    return data


class HistoryRules(unittest.TestCase):
    def test_read_parameters(self):
        self.assertEqual(R.read_params(" S:\\EXP69\\DN ", "S:\\EXP69", date(2026, 10, 15)),
                         {"account_set": "S:\\EXP69\\DN", "account_root": "S:\\EXP69", "since": "2025-10-15"})
        for bad in ("", "x" * (readback_contracts.PATH_MAX + 1)):
            with self.assertRaises(readback_contracts.ContractError):
                R.read_params(bad, "", date(2026, 10, 15))

    def test_parse_keeps_what_suggestions_need(self):
        data = R.parse(history_raw())
        self.assertEqual((data["since"], data["read_at"]), ("2025-10-15", "2026-10-15T01:00:00Z"))
        self.assertEqual(len(data["suppliers"]), 3)  # 没有科目的那家不留
        self.assertEqual(data["suppliers"][0], {"name": "ศรีกรุง เครื่องเขียน", "tax_id": "0105556012345",
                                                "branch": "00000",
                                                "accounts": [{"code": "5320-01", "docs": 3, "last": "2026-09-30"}]})
        for bad in (None, [], history_raw(version=2), history_raw(read_at="yesterday"),
                    history_raw(suppliers="x"), history_raw(suppliers=[{"name": "A", "accounts": [
                        {"code": "5320-01", "docs": -1, "last": "2026-09-30"}]}]),
                    history_raw(suppliers=[{"name": "A", "accounts": [
                        {"code": "5320-01", "docs": 1, "last": "someday"}]}]),
                    history_raw(suppliers=[{"name": "A", "accounts": [{"code": "5"}] * (R.ACCOUNTS_PER_SUPPLIER + 1)}]),
                    history_raw(suppliers=[{"name": "x" * (R.TEXT_MAX + 1), "accounts": []}])):
            with self.assertRaises(readback_contracts.ContractError) as caught:
                R.parse(bad)
            self.assertEqual(caught.exception.code, R.INVALID)

    def test_the_seller_is_found_by_tax_id_across_its_records_or_by_name(self):
        data = R.parse(history_raw())
        self.assertEqual(R.supplier_accounts(data, "0-1055-56012-34-5", "whatever"),
                         [{"code": "5320-01", "docs": 4, "last": "2026-10-02"},
                          {"code": "5390-99", "docs": 1, "last": "2026-10-01"}])
        self.assertEqual(R.supplier_accounts(data, "", "ร้านป้าแดง"),
                         [{"code": "5340-01", "docs": 2, "last": "2026-08-01"}])
        self.assertEqual(R.supplier_accounts(data, "", "บริษัท ร้านป้าแดง จำกัด"),  # 法人字样不算
                         [{"code": "5340-01", "docs": 2, "last": "2026-08-01"}])
        self.assertEqual(R.supplier_accounts(data, "0999999999999", "ร้านป้าแดง"), [])  # 有税号就只按税号
        self.assertEqual(R.supplier_accounts(data, "", ""), [])
        self.assertEqual(R.supplier_accounts({}, "0105556012345", ""), [])
        self.assertIs(choice_rules.norm_name, R.norm_name)  # 与推送选择记「这家」同一种名称归一


class SettledRules(unittest.TestCase):
    """用户 2026-10-15 同意：同一供应商（按税号合并各分店）近 12 个月 ≥3 次、全记同一个科目 → 直接用；其余仍是建议。"""
    TODAY = date(2026, 10, 15)

    def test_three_or_more_on_one_account_within_a_year_is_settled(self):
        self.assertEqual(R.settled([{"code": "5320-01", "docs": 3, "last": "2026-09-30"}], self.TODAY),
                         {"code": "5320-01", "docs": 3, "last": "2026-09-30"})

    def test_fewer_than_three_two_accounts_or_too_old_is_only_a_suggestion(self):
        self.assertIsNone(R.settled([{"code": "5320-01", "docs": 2, "last": "2026-09-30"}], self.TODAY))
        self.assertIsNone(R.settled([{"code": "5320-01", "docs": 9, "last": "2026-09-30"},
                                     {"code": "5390-99", "docs": 1, "last": "2026-10-01"}], self.TODAY))
        self.assertIsNone(R.settled([{"code": "5320-01", "docs": 9, "last": "2025-10-01"}], self.TODAY))
        self.assertIsNone(R.settled([], self.TODAY))
        # 存着的那份历史一个多月没重读：不再当「近 12 个月」直接用
        rows = [{"code": "5320-01", "docs": 3, "last": "2026-09-30"}]
        self.assertIsNotNone(R.settled(rows, self.TODAY, "2026-10-01T00:00:00Z"))
        self.assertIsNone(R.settled(rows, self.TODAY, "2026-09-01T00:00:00Z"))

    def test_branches_of_one_tax_id_are_one_supplier(self):
        data = R.parse(history_raw())
        found = R.suppliers_of(data)
        self.assertEqual(found["0105556012345"]["accounts"],
                         [{"code": "5320-01", "docs": 4, "last": "2026-10-02"},
                          {"code": "5390-99", "docs": 1, "last": "2026-10-01"}])
        self.assertIn("name:ร้านป้าแดง", found)
        self.assertEqual(R.supplier_key("0-1055-56012-34-5", "x"), "0105556012345")
        self.assertEqual(R.supplier_key("", "บริษัท ร้านป้าแดง จำกัด"), "name:ร้านป้าแดง")
        self.assertEqual(R.supplier_key("", ""), "")


def settled_raw():
    """一家很一致（两个分店合计 4 张、都记 5320-01）、一家只有 2 张、一家记了两个科目。"""
    return history_raw(suppliers=[
        {"code": "S001", "name": "ศรีกรุง เครื่องเขียน", "tax_id": "0105556012345", "branch": "00000",
         "accounts": [{"code": "5320-01", "docs": 3, "last": "2026-09-30"}]},
        {"code": "S001-12", "name": "ศรีกรุง เครื่องเขียน (สาขา 12)", "tax_id": "0105556012345", "branch": "00012",
         "accounts": [{"code": "5320-01", "docs": 1, "last": "2026-10-02"}]},
        {"code": "S009", "name": "ร้านป้าแดง", "tax_id": "", "branch": "00000",
         "accounts": [{"code": "5340-01", "docs": 2, "last": "2026-08-01"}]},
        {"code": "S010", "name": "ไฟฟ้า", "tax_id": "0994000165510", "branch": "00000",
         "accounts": [{"code": "5310-01", "docs": 5, "last": "2026-09-01"},
                      {"code": "5390-99", "docs": 1, "last": "2026-09-02"}]}])


# ── 历史的读取与存放：readback/history.py ─────────────────────────────────────


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class HistoryHarness:
    def __init__(self, folder):
        self.calls, self.requests, self.presence, self.logged = [], {}, {}, []
        self.adapter = "express"
        frappe = types.ModuleType("frappe")
        for name in ("ValidationError", "PermissionError", "DoesNotExistError"):
            setattr(frappe, name, type(name, (Exception,), {}))
        frappe.clear_messages = lambda: None
        frappe.log_error = lambda title=None, message=None: self.logged.append(title)
        frappe.get_traceback = lambda: "Traceback"
        frappe.get_site_path = lambda *parts: str(pathlib.Path(folder, *parts))
        self.frappe = frappe
        request_service = types.ModuleType("pearnly_cowork.companion.request_service")
        request_service.create = self.create
        request_service.status = self.status
        request_service.result = lambda name: (self.requests[name]["content"], {})
        request_service.drop = lambda name: self.calls.append(("drop", name)) or self.requests.pop(name, None)
        self.account_keys = []  # 读账套请求带的规范键（ADR-021 第 4 批：问够得到它的电脑、按它分派）
        request_service.waiting = lambda connection, kind, known="", account_key=None: self.calls.append(
            ("waiting", kind, known)) or self.account_keys.append(("waiting", account_key)) or dict(self.presence)
        connections = types.ModuleType("pearnly_cowork.connections.service")
        connections.adapter_of = lambda name: self.adapter
        connections.targets = lambda name: {"account_sets": [{"source_id": "S:\\EXP69\\DN"}]}
        readback = types.ModuleType("pearnly_cowork.readback.service")
        readback._known_version = lambda connection: "1.5.2"
        readback._account = lambda catalog, key: {"key": key, "path": "S:\\EXP69\\DN", "root": "S:\\EXP69",
                                                  "label": "ดาวเหนือ", "folder": "DN"}
        modules = {"frappe": frappe, "pearnly_cowork.companion": types.ModuleType("pearnly_cowork.companion"),
                   "pearnly_cowork.companion.request_service": request_service,
                   "pearnly_cowork.connections": types.ModuleType("pearnly_cowork.connections"),
                   "pearnly_cowork.connections.service": connections,
                   "pearnly_cowork.readback.service": readback}
        modules["pearnly_cowork.companion"].request_service = request_service
        modules["pearnly_cowork.connections"].service = connections
        with mock.patch.dict(sys.modules, modules):
            sys.modules["pearnly_cowork.readback"].service = readback
            self.history = _load("account_history_under_test", APP / "readback/history.py")
        self.now = datetime(2026, 10, 15, 2, 0, 0)
        self.history._now = lambda: self.now

    def create(self, kind, connection, params, key, account_key=""):
        self.calls.append(("create", kind, connection, params, key))
        self.account_keys.append(("create", account_key))
        name = "r%031d" % len(self.requests)
        self.requests[name] = {"request": name, "kind": kind, "status": "requested", "code": ""}
        return dict(self.requests[name])

    def status(self, name):
        if name not in self.requests:
            raise self.frappe.DoesNotExistError(name)
        return dict(self.requests[name])

    def finish(self, name, data=None, content=None):
        self.requests[name].update(status="done", content=content if content is not None else
                                   gzip.compress(json.dumps(data or history_raw()).encode("utf-8")))

    def made(self):
        return [call for call in self.calls if call[0] == "create"]


SELLER = {"tax": "0105556012345", "name": "ศรีกรุง เครื่องเขียน"}


class History(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.h = HistoryHarness(folder.name)
        self.push = self.h.history.for_push

    def test_missing_history_is_requested_once_then_taken_and_kept_for_a_day(self):
        first = self.push("C1", "S:\\EXP69\\DN", SELLER, True)
        self.assertEqual(first, {"state": "reading", "accounts": [], "changed": True, "settled": None})
        (_, kind, connection, params, key), = self.h.made()
        self.assertEqual((kind, connection), ("account_history", "C1"))
        self.assertEqual(params, {"account_set": "S:\\EXP69\\DN", "account_root": "S:\\EXP69", "since": "2025-10-15"})
        self.assertTrue(key.startswith("account_history:"))
        # 不定向，带这份账套的规范键：问的、交给的都是够得到它的电脑（ADR-021 第 4 批）
        self.assertEqual(self.h.account_keys, [("waiting", "s:\\exp69\\dn"), ("create", "s:\\exp69\\dn")])
        # 还在读：不再建
        self.assertEqual(self.push("C1", "s:/exp69/dn/", SELLER, True)["state"], "reading")
        self.assertEqual(len(self.h.made()), 1)
        # 读完：取回、存下、删掉请求
        request = next(iter(self.h.requests))
        self.h.finish(request)
        taken = self.push("C1", "S:\\EXP69\\DN", SELLER, True)
        self.assertEqual((taken["state"], taken["changed"]), ("ready", True))
        self.assertEqual(taken["accounts"][0], {"code": "5320-01", "docs": 4, "last": "2026-10-02"})
        self.assertIn(("drop", request), self.h.calls)
        # 一天之内：照用存着的，不请电脑
        self.h.now += timedelta(hours=23)
        again = self.push("C1", "S:\\EXP69\\DN", SELLER, True)
        self.assertEqual((again["state"], again["changed"], len(self.h.made())), ("ready", False, 1))
        # 超过一天：再请一次，读完前照旧用存着的
        self.h.now += timedelta(hours=2)
        stale = self.push("C1", "S:\\EXP69\\DN", SELLER, True)
        self.assertEqual((stale["state"], len(self.h.made())), ("reading", 2))
        self.assertEqual(stale["accounts"][0]["code"], "5320-01")
        # 另一家供应商、另一本账套各是各的
        self.assertEqual(self.push("C1", "S:\\EXP69\\DN", {"tax": "", "name": "ร้านป้าแดง"})["accounts"][0]["code"],
                         "5340-01")
        self.assertEqual(self.push("C1", "S:\\EXP69\\OTHER", SELLER)["state"], "none")

    def test_preparing_a_push_only_reads_what_is_kept(self):
        self.assertEqual(self.push("C1", "S:\\EXP69\\DN", SELLER, False),
                         {"state": "none", "accounts": [], "changed": False, "settled": None})
        self.assertEqual(self.h.made(), [])
        self.assertNotIn("waiting", [call[0] for call in self.h.calls])

    def test_an_offline_or_old_computer_is_quietly_skipped(self):
        for reason in ("offline", "update_needed"):
            self.h.presence = {"status": "waiting_for_cc", "reason": reason}
            self.assertEqual(self.push("C1", "S:\\EXP69\\DN", SELLER, True),
                             {"state": "none", "accounts": [], "changed": False, "settled": None})
        self.assertEqual(self.h.made(), [])
        self.assertIn(("waiting", "account_history", "1.5.2"), self.h.calls)
        self.h.adapter = "mrerp"
        self.h.presence = {}
        self.assertEqual(self.push("M1", "ACME", SELLER, True)["state"], "none")
        self.assertEqual(self.h.made(), [])

    def test_a_failed_read_is_not_asked_again_for_an_hour(self):
        self.push("C1", "S:\\EXP69\\DN", SELLER, True)
        request = next(iter(self.h.requests))
        self.h.requests[request].update(status="failed", code="account_unreachable")
        failed = self.push("C1", "S:\\EXP69\\DN", SELLER, True)
        self.assertEqual((failed["state"], failed["changed"]), ("none", True))
        self.assertIn(("drop", request), self.h.calls)
        self.h.now += timedelta(minutes=59)
        self.assertEqual(self.push("C1", "S:\\EXP69\\DN", SELLER, True)["state"], "none")
        self.assertEqual(len(self.h.made()), 1)
        self.h.now += timedelta(minutes=2)
        self.assertEqual(self.push("C1", "S:\\EXP69\\DN", SELLER, True)["state"], "reading")
        self.assertEqual(len(self.h.made()), 2)

    def test_bad_data_or_a_vanished_request_does_not_break_the_push(self):
        self.push("C1", "S:\\EXP69\\DN", SELLER, True)
        request = next(iter(self.h.requests))
        self.h.finish(request, content=b"not gzip")
        self.assertEqual(self.push("C1", "S:\\EXP69\\DN", SELLER, True)["state"], "none")
        self.assertEqual(self.h.logged, [])
        self.h.now += timedelta(hours=2)
        self.push("C1", "S:\\EXP69\\DN", SELLER, True)
        self.h.requests.clear()  # 请求被清掉了：当没请过，再请一次
        self.assertEqual(self.push("C1", "S:\\EXP69\\DN", SELLER, True)["state"], "reading")
        self.assertEqual(len(self.h.made()), 3)

    def test_anything_unexpected_means_no_history_not_a_failed_push(self):
        self.h.history.connections.adapter_of = mock.Mock(side_effect=RuntimeError("db gone"))
        self.assertEqual(self.push("C1", "S:\\EXP69\\DN", SELLER, True),
                         {"state": "none", "accounts": [], "changed": False, "settled": None})
        self.assertEqual(self.h.logged, ["Cowork account history failed"])


class SettledHistory(unittest.TestCase):
    """历史很一致的直接用；主档页列出、删了就不再按历史用（重读历史也不冲掉）。"""
    BOOK, OLD = "S:\\EXP69\\DN", "S:\\EXP68\\DN"
    SETS = [{"connection": "C1", "adapter": "express", "key": "S:\\EXP69\\DN"},
            {"connection": "C1", "adapter": "express", "key": "S:\\EXP68\\DN"},
            {"connection": "M1", "adapter": "mrerp", "key": "ACME"}]

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.h = HistoryHarness(folder.name)
        self.history = self.h.history
        self.read(self.BOOK)

    def read(self, book, data=None):
        self.history.for_push("C1", book, SELLER, True)
        request = next(name for name, row in self.h.requests.items() if row["status"] == "requested")
        self.h.finish(request, data or settled_raw())
        self.assertEqual(self.history.for_push("C1", book, SELLER, True)["state"], "ready")

    def test_a_consistent_supplier_is_settled_others_are_suggestions(self):
        found = self.history.for_push("C1", self.BOOK, SELLER)
        self.assertEqual(found["settled"], {"code": "5320-01", "docs": 4, "last": "2026-10-02"})
        self.assertIsNone(self.history.for_push("C1", self.BOOK, {"tax": "", "name": "ร้านป้าแดง"})["settled"])
        mixed = self.history.for_push("C1", self.BOOK, {"tax": "0994000165510", "name": ""})
        self.assertIsNone(mixed["settled"])
        self.assertEqual(mixed["accounts"][0]["code"], "5310-01")  # 仍按历史建议

    def test_the_company_page_lists_settled_suppliers_and_forgetting_stops_their_use(self):
        rows = self.history.company_choices("0105536082808", self.SETS)
        self.assertEqual(rows, [{"supplier_tax": "0105556012345", "supplier_name": "ศรีกรุง เครื่องเขียน",
                                 "account": "5320-01", "account_label": "", "count": 4, "last_date": "2026-10-02"}])
        self.assertEqual(self.history.company_forget("0105536082808", "0105556012345", "5320-01", self.SETS), 2)
        self.assertEqual(self.history.company_forget("0105536082808", "0105556012345", "5320-01", self.SETS), 0)  # 幂等
        self.assertEqual(self.history.company_choices("0105536082808", self.SETS), [])
        gone = self.history.for_push("C1", self.BOOK, SELLER)
        self.assertEqual((gone["settled"], gone["accounts"]), (None, []))  # 也不再按历史建议
        # 过了一天重读：汇总换新，删过的照旧不用
        self.h.now += timedelta(days=2)
        self.read(self.BOOK)
        self.assertIsNone(self.history.for_push("C1", self.BOOK, SELLER)["settled"])
        # 别的供应商不受影响
        self.assertEqual(self.history.for_push("C1", self.BOOK, {"tax": "0994000165510", "name": ""})["accounts"][0]["code"],
                         "5310-01")
        self.assertEqual(self.history.company_forget("0105536082808", "", "5320-01", self.SETS), 0)

    def test_each_year_is_judged_on_its_own(self):
        self.read(self.OLD, history_raw(suppliers=[
            {"code": "S001", "name": "ศรีกรุง เครื่องเขียน", "tax_id": "0105556012345", "branch": "00000",
             "accounts": [{"code": "5390-99", "docs": 6, "last": "2026-01-30"}]}]))
        rows = self.history.company_choices("", self.SETS)
        self.assertEqual(sorted((row["account"], row["count"]) for row in rows), [("5320-01", 4), ("5390-99", 6)])


# ── 推送服务：push/application/service.py 的 `_suggest` 与 `choices_view` ─────


class PushService(unittest.TestCase):
    def _service(self, found):
        from test_cowork_push_fixed import _service, BOOK

        service, frappe_module, _calls = _service()
        self.book = BOOK
        self.provided = []

        def hooks(name):
            return ["p.history"] if name == "cowork_push_account_history" else []

        def provider(connection, account_set, seller, refresh):
            self.provided.append((connection, account_set, seller, refresh))
            return dict(found)

        frappe_module.get_hooks = hooks
        frappe_module.get_attr = lambda path: provider
        self.commits = []
        frappe_module.db.commit = lambda: self.commits.append(1)
        service.account_suggest = S
        return service

    def _wired(self, service, needed):
        doc = types.SimpleNamespace(name="H1", workspace="W1", client="")
        loaded = types.SimpleNamespace(endpoint={"adapter": "express"}, doc=types.SimpleNamespace(name="C1"))
        fields = {"seller_name": "ศรีกรุง เครื่องเขียน", "seller_tax": "0105556012345"}
        facts = (types.SimpleNamespace(account_set=self.book), {}, fields, "purchase", {},
                 {"reported_accounts": BLANK})
        service.scope.require = lambda *a: None
        service.storage.read = lambda history, **k: doc
        service.connections.read_for_push = lambda *a, **k: loaded
        service.connections.ensure_account_masters = lambda *a, **k: {"state": "ready", "present": True}
        service._express_facts = lambda *a: facts
        service._document_block = lambda doc: None
        service._decisions = lambda *a, **k: {"needed": [dict(item) for item in needed], "applied": {}}

    def test_the_account_item_carries_suggestions_before_the_full_list(self):
        service = self._service({"state": "ready", "accounts": [{"code": "5390-99", "docs": 5, "last": "2026-10-01"}],
                                 "changed": True})
        self._wired(service, [{"kind": "payment", "subject": "S"}, {"kind": "account", "subject": "S"}])
        view = service.choices_view("H1", "C1", self.book, None, hint="ค่าเครื่องเขียน")
        account = next(item for item in view["needed"] if item["kind"] == "account")
        self.assertEqual(codes(account["suggested"]), [("5320-01", "hint"), ("5390-99", "history")])
        self.assertNotIn("suggested", next(item for item in view["needed"] if item["kind"] == "payment"))
        self.assertEqual(len(view["options"]["accounts"]), len(BLANK))  # 完整清单照给
        self.assertEqual(self.provided, [("C1", self.book, {"tax": "0105556012345", "name": "ศรีกรุง เครื่องเขียน"},
                                          True)])
        self.assertEqual(self.commits, [1])  # 建了 / 了结了读历史的请求：当场提交（网页是 GET）
        self.assertNotIn("history", view)

    def test_history_still_being_read_is_reported_and_nothing_is_committed_without_a_change(self):
        service = self._service({"state": "reading", "accounts": [], "changed": False})
        self._wired(service, [{"kind": "account", "subject": "S"}])
        view = service.choices_view("H1", "C1", self.book, None)
        self.assertEqual((view["history"], self.commits), ("reading", []))
        self.assertEqual(codes(view["needed"][0]["suggested"]), [("5320-01", "name")])

    def test_no_account_to_choose_asks_nothing_of_the_history(self):
        service = self._service({"state": "ready", "accounts": [], "changed": False})
        self._wired(service, [{"kind": "payment", "subject": "S"}])
        service.choices_view("H1", "C1", self.book, None)
        self.assertEqual(self.provided, [])

    def test_preparing_a_push_only_reads_the_kept_history(self):
        service = self._service({"state": "ready", "accounts": [], "changed": False})
        needed = [{"kind": "account", "subject": "S"}]
        state, changed = service._suggest(needed, "C1", self.book, {"category": "ค่าไฟฟ้า"},
                                          {"reported_accounts": BLANK})
        self.assertEqual((state, changed, self.provided[0][3]), ("ready", False, False))
        self.assertEqual(codes(needed[0]["suggested"]), [("5310-01", "name")])


class PendingChoicesContract(unittest.TestCase):
    """跨端契约（tests/fixtures/cc-pending-contract.json 的 choices）：「待处理」展开一行时页面拿到的就是复核页、CC 用的
    `choices_view` 真实输出；页面测试用同一份当假数据。"""

    def test_the_view_the_page_reads_is_the_real_output(self):
        fixture = json.loads((ROOT / "tests/fixtures/cc-pending-contract.json").read_text(encoding="utf-8"))
        case = PushService("test_the_account_item_carries_suggestions_before_the_full_list")
        service = case._service({"state": "ready", "accounts": [{"code": "5390-99", "docs": 5, "last": "2026-10-01"}],
                                 "changed": True})
        subject, label = "purchase|0105556012345", "ศรีกรุง เครื่องเขียน"
        case._wired(service, [{"kind": "payment", "subject": subject, "label": label,
                               "options": [{"code": "cash"}, {"code": "credit"}], "current": ""},
                              {"kind": "account", "subject": subject, "label": label, "current": ""}])
        view = json.loads(json.dumps(service.choices_view("H1", "C1", case.book, None), ensure_ascii=False))
        self.assertEqual(view, fixture["choices"])


class PushDecisions(unittest.TestCase):
    """推送准备的选择（`_decisions`）：历史很一致的科目当作已记住的用；会计员手选过的、任务默认的优先；不一致、次数不够
    仍要人选。真的选择规则（`push/express/choices.py`），历史经钩子替身。"""
    FIELDS = {"seller_name": "ศรีกรุง เครื่องเขียน", "seller_tax": "0105556012345", "payment_status": "paid"}

    def decide(self, settled, remembered=None, fixed=None, chart=BLANK, accounts=None, since=None, chosen_at=None,
               fields=None):
        from test_cowork_push_fixed import _service, BOOK

        service, frappe_module, _calls = _service()
        self.asked = []

        def provider(connection, account_set, seller, refresh):
            self.asked.append((connection, account_set, seller, refresh))
            return {"state": "ready", "accounts": accounts or [], "changed": False, "settled": settled}

        frappe_module.get_hooks = lambda name: ["p.history"] if name == "cowork_push_account_history" else []
        frappe_module.get_attr = lambda path: provider
        service.choice_rules = choice_rules
        service.resolve_account_sourced = lambda *a: (None, "none")
        service.resolve_posting_kind = lambda kind, flat: kind
        service._pos_receipts = lambda flat: []
        self.chosen_asked = []
        service.choice_store = types.SimpleNamespace(remembered=lambda *a: {"account": dict(remembered or {})},
                                                     record=lambda *a: True,
                                                     chosen_at=lambda *a: self.chosen_asked.append(a) or chosen_at)
        return service._decisions("C1", BOOK, {}, dict(fields or self.FIELDS), "purchase", "service",
                                  {"reported_accounts": chart}, {"mappings": {}}, fixed, since=since)

    SUBJECT = "purchase|0105556012345"

    def test_a_settled_account_is_used_without_asking(self):
        decision = self.decide({"code": "5320-01", "docs": 4, "last": "2026-10-02"})
        self.assertEqual(decision["applied"]["account"], "5320-01")
        self.assertEqual([item["kind"] for item in decision["needed"]], [])
        self.assertEqual(decision["history"], {"account": "5320-01", "docs": 4})
        self.assertEqual(self.asked, [("C1", self.asked[0][1], {"tax": "0105556012345", "name": "ศรีกรุง เครื่องเขียน"},
                                       False)])  # 准备推送只看存着的

    def test_fewer_than_three_or_mixed_accounts_still_ask_once(self):
        decision = self.decide(None, accounts=[{"code": "5320-01", "docs": 2, "last": "2026-10-02"}])
        self.assertEqual([item["kind"] for item in decision["needed"]], ["account"])
        self.assertNotIn("account", decision["applied"])
        self.assertNotIn("history", decision)

    def test_a_forgotten_history_is_asked_again(self):
        # 主档页删了：钩子不再给 settled（见 SettledHistory），推送照旧问
        self.assertEqual([item["kind"] for item in self.decide(None)["needed"]], ["account"])

    def test_the_accountants_own_choice_and_the_task_default_come_first(self):
        settled = {"code": "5320-01", "docs": 9, "last": "2026-10-02"}
        decision = self.decide(settled, remembered={self.SUBJECT: "5390-99"})
        self.assertEqual((decision["applied"]["account"], self.asked), ("5390-99", []))
        self.assertNotIn("history", decision)
        decision = self.decide(settled, fixed={"account": "5310-01"})
        self.assertEqual((decision["applied"]["account"], self.asked), ("5310-01", []))

    def test_the_account_says_where_it_came_from(self):
        # 测试窗口第 2 轮（F19 后续）：Express 历史定的、任务默认、这张票传上来以后人选的、之前记过的，分开记
        import datetime as dt
        settled = {"code": "5320-01", "docs": 4, "last": "2026-10-02"}
        self.assertEqual(self.decide(settled)["applied"]["account_from"], "express_history")
        self.assertEqual(self.decide(None, fixed={"account": "5310-01"})["applied"]["account_from"], "task_default")
        uploaded = dt.datetime(2026, 10, 15, 9, 0)
        after = self.decide(None, remembered={self.SUBJECT: "5390-99"}, since=uploaded,
                            chosen_at=dt.datetime(2026, 10, 15, 9, 5))
        self.assertEqual(after["applied"]["account_from"], "chosen")
        self.assertEqual(self.chosen_asked, [("C1", self.chosen_asked[0][1], "account", self.SUBJECT)])
        before = self.decide(None, remembered={self.SUBJECT: "5390-99"}, since="2026-10-15 09:00:00.000000",
                             chosen_at=dt.datetime(2026, 9, 1, 8, 0))
        self.assertEqual(before["applied"]["account_from"], "remembered")
        # 不知道票什么时候传的（没给 since）：照「记过的」说，不查选的时间
        self.assertEqual(self.decide(None, remembered={self.SUBJECT: "5390-99"})["applied"]["account_from"], "remembered")
        self.assertEqual(self.chosen_asked, [])
        # 还要人选的不带来源
        self.assertNotIn("account_from", self.decide(None)["applied"])

    def test_chosen_is_looked_up_under_the_key_the_choice_was_saved_with(self):
        # 票面只有 seller_tax_id：选择按名字记（choices._party 只用 seller_tax），查选择时间也用同一个键
        import datetime as dt
        fields = {"seller_name": "ร้านป้าแดง", "seller_tax_id": "0105556099999", "payment_status": "paid"}
        subject = choice_rules.supplier_subject("purchase", "", "ร้านป้าแดง")
        after = self.decide(None, remembered={subject: "5390-99"}, since=dt.datetime(2026, 10, 15, 9, 0),
                            chosen_at=dt.datetime(2026, 10, 15, 9, 5), fields=fields)
        self.assertEqual((after["applied"]["account"], after["applied"]["account_from"]), ("5390-99", "chosen"))
        self.assertEqual([asked[3] for asked in self.chosen_asked], [subject])

    def test_an_account_no_longer_in_the_chart_is_not_used(self):
        decision = self.decide({"code": "9999-99", "docs": 9, "last": "2026-10-02"})
        self.assertEqual([item["kind"] for item in decision["needed"]], ["account"])



class CompanyRuleDecisions(unittest.TestCase):
    """第 2 期统一客户规则接进推送准备（`_decisions`）：公司规则经钩子 ``cowork_push_company_choices`` 垫在账套记忆之下、
    Express 历史已定之上；钩子出错这一张不用历史已定；结果带 ``decided_by``，付款由公司规则定时 ``payment_from`` 记
    ``company_rule``，经 `pushed.written` 交给 CC。"""
    FIELDS = {"seller_name": "ศรีกรุง เครื่องเขียน", "seller_tax": "0105556012345"}
    SUBJECT = "purchase|0105556012345"
    SETTLED = {"code": "5320-01", "docs": 9, "last": "2026-10-02"}

    def decide(self, rules=None, remembered=None, broken=False, fields=None, branch="00000", since=None,
               chosen_at=None, chart=BLANK):
        from test_cowork_push_fixed import _service, BOOK

        service, frappe_module, _calls = _service()
        self.asked, self.rows, self.logged = [], [], []

        def history(connection, account_set, seller, refresh):
            self.asked.append(seller)
            return {"state": "ready", "accounts": [], "changed": False, "settled": self.SETTLED}

        def company(connection, account_set, account_row):
            self.rows.append(account_row)
            if broken:
                raise RuntimeError("db")
            return {"company": "CO1", "branch": branch, "rules": rules or []} if rules is not None else None

        hooks = {"cowork_push_account_history": ["p.history"], "cowork_push_company_choices": ["p.company"]}
        frappe_module.get_hooks = lambda name: hooks.get(name, [])
        frappe_module.get_attr = lambda path: {"p.history": history, "p.company": company}[path]
        frappe_module.log_error = lambda **kw: self.logged.append(kw.get("title"))
        frappe_module.get_traceback = lambda: ""
        frappe_module.clear_messages = lambda: None
        service.choice_rules = choice_rules
        service.resolve_account_sourced = lambda *a: (None, "none")
        service.resolve_posting_kind = lambda kind, flat: kind
        service._pos_receipts = lambda flat: []
        service.choice_store = types.SimpleNamespace(remembered=lambda *a: {k: dict(v) for k, v in (remembered or {}).items()},
                                                     record=lambda *a: True, chosen_at=lambda *a: chosen_at)
        return service._decisions("C1", BOOK, {}, dict(fields or self.FIELDS), "purchase", "service",
                                  {"reported_accounts": chart}, {"mappings": {}}, None,
                                  account_row=lambda: {"source_id": BOOK}, since=since)

    def rule(self, kind, value, branch="", subject=None):
        return {"kind": kind, "subject": subject or self.SUBJECT, "value": value, "branch": branch}

    def test_a_rule_waits_for_a_chart_that_was_never_read_instead_of_the_history(self):
        # 合并审查第 2 条：定时任务跟到新年度、这份账套的科目表还没读过（T1）。公司规则的费用科目先不用，也不拿 Express
        # 历史顶替（公司规则比历史具体）；这一项照常要选，推送照现有规则请电脑读科目表、读到后再按规则推（同 fixed_in）
        paid = {**self.FIELDS, "payment_status": "paid"}
        decision = self.decide([self.rule("account", "5310-02")], fields=paid, chart=[])
        self.assertEqual([item["kind"] for item in decision["needed"]], ["account"])
        self.assertNotIn("account", decision["applied"])
        self.assertEqual((self.asked, decision.get("history")), ([], None))
        # 读到了：照规则定
        self.assertEqual(self.decide([self.rule("account", "5310-02")], fields=paid)["applied"]["account"], "5310-02")
        # 只有别家供应商的规则：这一张照旧可按历史已定
        other = self.decide([self.rule("account", "5310-02", subject="purchase|0105559999996")], fields=paid, chart=[])
        self.assertEqual((other["applied"]["account"], other["decided_by"]), ("5320-01", {"account": "express_history"}))

    def test_a_company_rule_is_used_before_the_history_and_says_so(self):
        decision = self.decide([self.rule("account", "5310-02"), self.rule("payment", "credit")])
        applied = decision["applied"]
        self.assertEqual((applied["account"], applied["payment"], applied["payment_from"], applied["account_from"]),
                         ("5310-02", "credit", "company_rule", "company_rule"))
        self.assertEqual(decision["decided_by"], {"account": "company_rule", "payment": "company_rule"})
        self.assertEqual((self.asked, decision.get("history")), ([], None))  # 规则定了，不看历史
        self.assertEqual(self.rows, [{"source_id": self.rows[0]["source_id"]}])  # 目录这一行交给钩子认公司

    def test_the_account_sets_own_choice_beats_the_rule(self):
        decision = self.decide([self.rule("account", "5310-02")], remembered={"account": {self.SUBJECT: "5310-01"}},
                               fields={**self.FIELDS, "payment_status": "paid"})
        self.assertEqual((decision["applied"]["account"], decision["decided_by"]), ("5310-01", {"account": "remembered"}))
        self.assertEqual(decision["applied"]["account_from"], "remembered")

    def test_a_rule_reports_company_rule_even_after_the_document_came_in(self):
        # 复审 1：规则值并进了 remembered，同键的账套记忆存规则时已删（chosen_at 查不到）；也不报成 remembered / chosen
        import datetime as dt
        uploaded = dt.datetime(2026, 10, 15, 9, 0)
        for chosen_at in (None, dt.datetime(2026, 10, 15, 9, 5)):
            decision = self.decide([self.rule("account", "5310-02")], fields={**self.FIELDS, "payment_status": "paid"},
                                   since=uploaded, chosen_at=chosen_at)
            self.assertEqual(decision["applied"]["account_from"], "company_rule")

    def test_without_a_rule_the_settled_history_still_applies(self):
        decision = self.decide(None, fields={**self.FIELDS, "payment_status": "paid"})
        self.assertEqual((decision["applied"]["account"], decision["decided_by"]), ("5320-01", {"account": "express_history"}))
        self.assertEqual(decision["applied"]["account_from"], "express_history")
        decision = self.decide([self.rule("account", "5310-02", "00012")], fields={**self.FIELDS, "payment_status": "paid"})
        self.assertEqual(decision["applied"]["account"], "5320-01")  # 别的分店的规则不用

    def test_rules_that_cannot_be_read_stop_the_history_for_this_document(self):
        decision = self.decide([self.rule("account", "5310-02")], broken=True,
                               fields={**self.FIELDS, "payment_status": "paid"})
        self.assertEqual([item["kind"] for item in decision["needed"]], ["account"])
        self.assertNotIn("account", decision["applied"])
        self.assertEqual(self.logged, ["Cowork push company choices failed"])

    def test_a_rule_value_not_in_this_chart_is_not_used(self):
        decision = self.decide([self.rule("account", "9999-99")], fields={**self.FIELDS, "payment_status": "paid"})
        self.assertEqual(decision["applied"]["account"], "5320-01")  # 落到历史已定

    def test_written_tells_cc_what_decided_it(self):
        from pearnly_cowork.push.express import pushed

        payload = {"direction": "purchase", "doctype": "RR", "doctype_src": "company_rule", "items_account": "5310-02",
                   "vat_amount": "7.00", "account_source": "company_rule"}
        found = pushed.written(payload, {"account": "company_rule", "payment": "company_rule", "bogus": "x",
                                         "vat": "made_up", "item": "express_history"})
        self.assertEqual(found["payment_from"], "company_rule")
        self.assertEqual((found["decided_by"], found["account_from"]),
                         ({"account": "company_rule", "payment": "company_rule", "item": "express_history"},
                          "company_rule"))
        self.assertNotIn("decided_by", pushed.written(payload))  # 改版前的记录：照旧
        # account_from 只按载荷 account_source（同第 2 轮 ACCOUNT_FROM 一套词），不从 decided_by 推
        legacy = {**payload, "account_source": "choice"}
        self.assertNotIn("account_from", pushed.written(legacy, {"account": "company_rule"}))
        self.assertNotIn("history", pushed.DECIDED_SOURCES)
        source = (APP / "push/application/service.py").read_text(encoding="utf-8")
        self.assertIn('frozen["decided_by"] = dict(decision["decided_by"])', source)
        self.assertNotIn("pearnly_cowork.companies", source)  # push 域不导入 companies
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('cowork_push_company_choices = ["pearnly_cowork.companies.rules.for_push"]', hooks)

if __name__ == "__main__":
    unittest.main()
