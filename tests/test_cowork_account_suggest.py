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
        request_service.waiting = lambda connection, kind, known="": self.calls.append(("waiting", kind, known)) \
            or dict(self.presence)
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

    def create(self, kind, connection, params, key):
        self.calls.append(("create", kind, connection, params, key))
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
        self.assertEqual(first, {"state": "reading", "accounts": [], "changed": True})
        (_, kind, connection, params, key), = self.h.made()
        self.assertEqual((kind, connection), ("account_history", "C1"))
        self.assertEqual(params, {"account_set": "S:\\EXP69\\DN", "account_root": "S:\\EXP69", "since": "2025-10-15"})
        self.assertTrue(key.startswith("account_history:"))
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
                         {"state": "none", "accounts": [], "changed": False})
        self.assertEqual(self.h.made(), [])
        self.assertNotIn("waiting", [call[0] for call in self.h.calls])

    def test_an_offline_or_old_computer_is_quietly_skipped(self):
        for reason in ("offline", "update_needed"):
            self.h.presence = {"status": "waiting_for_cc", "reason": reason}
            self.assertEqual(self.push("C1", "S:\\EXP69\\DN", SELLER, True),
                             {"state": "none", "accounts": [], "changed": False})
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
                         {"state": "none", "accounts": [], "changed": False})
        self.assertEqual(self.h.logged, ["Cowork account history failed"])


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


if __name__ == "__main__":
    unittest.main()
