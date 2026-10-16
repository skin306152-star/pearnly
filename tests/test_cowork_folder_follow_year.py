"""定时任务跨年度自动换账套（T1，2026-10-15）的离线检查。

任务记下公司主档与分店，到点按单据日期取这家公司覆盖那天的账套；取不到、多于一份、没有电脑够得到就停下等人。
这里把真实的 `companies.tasks` / `companies.service` / `companies.identity` / `folder_intake.follow` /
`folder_intake.push` 串起来跑（Frappe、连接目录、推送服务、写账池用内存替身，沿用 `test_cowork_companies` 的那一套），
覆盖成功、停下与权限三种路径，以及旧任务补公司的补丁。

注意 Express 测试数据里 T8、T9 的会计期间相同（都是 2025 年）：用例不假定一个年度只有一份账套。
"""
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_cowork_companies as env  # noqa: E402  内存里的 Frappe 与连接目录替身（导入时装好）

ROOT, APP = env.ROOT, env.APP
frappe, connections, service, identity = env.frappe, env.connections, env.service, env.identity
tasks = importlib.import_module("pearnly_cowork.companies.tasks")
folder_contracts = importlib.import_module("pearnly_cowork.folder_intake.contracts")
pending = env.pending
HISTORY, RULE, FILE = "Cowork OCR History", "Cowork Folder Intake Rule", "Cowork Folder Intake File"
TAX = env.TAX
HOOKS = {"cowork_task_company": ["pearnly_cowork.companies.tasks.company_of"],
         "cowork_task_books": ["pearnly_cowork.companies.tasks.books_of"]}
# 同一家公司三个年度：T7 = 2024，T8 / T9 = 2025（同期，测试账套就是这样），Y26 = 2026
T7, T8, T9, Y26 = "S:\\70EXP\\T7SINCER", "S:\\70EXP\\T8SINCER", "S:\\70EXP\\T9SINCER", "S:\\70EXP\\Y26SINCER"


def _key(path):
    return path.lower()


def row(path, label, start, end, **extra):
    return env.express_row(path, label, start=start, end=end, year=label[-4:], **extra)


def _load(name, path, modules):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class Pool:
    """写账池替身：哪些账套有电脑够得到（`push.native.service.has_writer`）、各份账套读到的科目 / 部门。"""

    def __init__(self):
        self.writers, self.masters, self.asked = set(), {}, []

    def has_writer(self, connection, key, *, actor, route_device=""):
        self.asked.append((connection, key, actor, route_device))
        return (connection, key) in self.writers

    def reported_masters(self, doc, key):
        return self.masters.get(key)


class Push:
    """推送服务替身：记下每次推往的账套与这次带的默认值；票上的原因 / 去向（`push_state`）也记下。"""

    def __init__(self):
        self.submitted, self.failures, self.retargeted, self.views = [], [], [], []
        self.writer_state = "ready"

    def choices_view(self, history, connection, account_set, posting_kind, fixed=None):
        self.views.append(account_set)
        return {"needed": [], "account_set": account_set}

    def assess(self, history, connection, account_set, posting_kind, **kwargs):
        """推 Express 之前的判定（第 3 期）：这里只看换账套，票都判成 A 类、照推。"""
        return {"assessed": True, "class": "A", "tier": "L0", "action": "push", "codes": "", "proposal": None,
                "checked_at": None, "automation": {"class": "A", "tier": "L0", "trigger": "task", "reasons": []}}

    def submit(self, history, connection, key, version, revision, account_set, *args, fixed=None, route_device="",
               automation=None):
        self.submitted.append((history, account_set, fixed))
        self.routes = getattr(self, "routes", []) + [route_device]
        return {"status": "prepared", "name": f"CWP-{len(self.submitted)}", "revision": 1}

    def record_failure(self, history, reason, **going):
        self.failures.append((history, reason, going))

    def retarget(self, history, connection, account_set):
        self.retargeted.append((history, connection, account_set))


def load_follow(pool):
    preparation = types.SimpleNamespace(reported_masters=pool.reported_masters)
    return _load("t1_folder_follow", APP / "folder_intake/follow.py", {
        "pearnly_cowork.connections": types.SimpleNamespace(service=connections, access=env.connection_access,
                                                            preparation=preparation),
        "pearnly_cowork.connections.preparation": preparation,
        "pearnly_cowork.push.native": types.SimpleNamespace(service=types.SimpleNamespace(has_writer=pool.has_writer)),
        "pearnly_cowork.push.native.service": types.SimpleNamespace(has_writer=pool.has_writer)})


def load_push(follow, fake):
    confirm = types.SimpleNamespace(confirm=lambda *args: None)
    destinations = types.SimpleNamespace(of=lambda adapter: confirm, ready=lambda adapter, result: True,
                                         READY="ready", UNREACHABLE="unreachable", MISSING="missing",
                                         writer_state=lambda adapter, connection, device="": fake.writer_state)
    application = types.SimpleNamespace(choices_view=fake.choices_view, submit=fake.submit,
                                        submission_failed=lambda *a: None, cancel_prepared=lambda *a: None,
                                        assess=fake.assess)
    push_state = types.SimpleNamespace(record_failure=fake.record_failure, retarget=fake.retarget,
                                       record_assessment=lambda history, **kwargs: None)
    return _load("t1_folder_push", APP / "folder_intake/push.py", {
        "pearnly_cowork.folder_intake.destinations": destinations,
        "pearnly_cowork.folder_intake.follow": follow,
        "pearnly_cowork.history": types.SimpleNamespace(push_state=push_state),
        "pearnly_cowork.history.push_state": push_state,
        "pearnly_cowork.push.application": types.SimpleNamespace(service=application),
        "pearnly_cowork.push.application.service": application})


class Base(unittest.TestCase):
    CATALOG = (row(T7, "SINCER 2567", "2024-01-01", "2024-12-31"),
               row(T8, "SINCER 2568", "2025-01-01", "2025-12-31"),
               row(Y26, "SINCER 2569", "2026-01-01", "2026-12-31"))
    SEED = T8

    def setUp(self):
        env.reset(C1=[dict(item) for item in self.CATALOG])
        frappe.hooks = dict(HOOKS)
        frappe.db.tables["Cowork ERP Connection"]["C1"].update(adapter="express", record_revision=1)
        self.company = env.make("C1", self.SEED)["id"]
        env.fresh_request()
        self.pool, self.fake = Pool(), Push()
        self.pool.writers = {("C1", _key(path)) for path in (T7, T8, T9, Y26)}
        self.follow = load_follow(self.pool)
        self.push = load_push(self.follow, self.fake)

    def rule(self, **extra):
        values = {"name": "R1", "connection": "C1", "account_set_key": T8, "company": self.company,
                  "company_branch": "00000", "posting_kind": "service", "defaults_json": "{}"}
        values.update(extra)
        return env.Row(values)

    def ticket(self, name, day):
        frappe.db.tables.setdefault(HISTORY, {})[name] = env.Row(
            name=name, invoice_date=day, edit_count=0, last_push_status="", last_push_record="", record_version=1,
            source_file="SF-" + name)
        return name

    def send(self, rule, history, final=True, entry=None):
        env.fresh_request()
        entry = entry or types.SimpleNamespace(name="E1", filename="bill.pdf")
        return self.push.push_one(entry, rule, history, 0, final=final)


class CompanyOfTests(Base):
    """保存任务时由服务端按所选账套反查公司与分店（`companies.tasks.company_of`）。"""

    def test_an_account_set_of_one_company_names_it_and_its_branch(self):
        self.assertEqual(tasks.company_of("C1", T7), {"company": self.company, "branch": "00000"})
        self.assertEqual(tasks.company_of("C1", T8.lower() + "\\"), {"company": self.company, "branch": "00000"})

    def test_unknown_account_sets_and_connections_are_not_guessed(self):
        self.assertIsNone(tasks.company_of("C1", "S:\\70EXP\\NOPE"))
        self.assertIsNone(tasks.company_of("C2", T8))
        self.assertIsNone(tasks.company_of("C1", ""))

    def test_an_account_set_belonging_to_two_companies_stays_fixed(self):
        """没有税号的账套：同一连接同名称按名称建的主档有两家（库里已有的脏数据）→ 对不上唯一一家，不记。"""
        env.reset(C1=[row(T8, "NONAME 2568", "2025-01-01", "2025-12-31", tax=None, company="ร้านไม่มีเลข")])
        first = env.make("C1", T8)["id"]
        frappe.db.tables["Cowork Company"]["dup"] = env.Row(dict(frappe.db.tables["Cowork Company"][first],
                                                                 name="dup", identity="name:other"))
        env.fresh_request()
        self.assertIsNone(tasks.company_of("C1", T8))

    def test_a_person_who_cannot_use_the_connection_gets_nothing(self):
        connections.users["C1"] = {"someone@example.com"}
        env.fresh_request()
        self.assertIsNone(tasks.company_of("C1", T8))
        self.assertIsNone(tasks.books_of(self.company, "C1", "2025-05-01") or None)

    def test_switching_task_owners_in_one_background_run_never_reuses_the_last_owners_catalog(self):
        """后台推进在同一个任务里换着负责人推（`pump._as` 不清 companies 的请求内缓存）。"""
        connections.users["C1"] = {"a@example.com"}
        self.assertEqual(len(tasks.books_of(self.company, "C1")), 3)
        frappe.session.user = "b@example.com"  # 不清缓存直接换人
        self.assertEqual(tasks.books_of(self.company, "C1"), [])
        self.assertIsNone(tasks.company_of("C1", T8))

    def test_a_deleted_company_has_no_books(self):
        self.assertIsNone(tasks.books_of("gone", "C1", "2025-05-01"))
        self.assertEqual([item["key"] for item in tasks.books_of(self.company, "C1", "2025-05-01")], [_key(T8)])


class PureRuleTests(unittest.TestCase):
    """`identity.for_day` / `identity.for_branch`：覆盖那天、按分店。"""

    def book(self, key, start, end, branch="00000"):
        return {"key": key, "period_start": start, "period_end": end, "branch": branch}

    def test_the_day_must_fall_in_the_fiscal_period(self):
        books = [self.book("a", "2025-01-01", "2025-12-31"), self.book("b", "2026-01-01", "2026-12-31")]
        self.assertEqual([item["key"] for item in identity.for_day(books, "2025-12-31")], ["a"])
        self.assertEqual([item["key"] for item in identity.for_day(books, "2026-01-01")], ["b"])
        self.assertEqual(identity.for_day(books, "2027-01-01"), [])
        self.assertEqual(identity.for_day(books, ""), [])
        self.assertEqual(identity.for_day(books, "15/01/2026"), [])
        self.assertEqual([item["key"] for item in identity.for_day(books, date(2026, 3, 1))], ["b"])

    def test_a_period_starting_mid_month_is_checked_to_the_day(self):
        books = [self.book("a", "2024-07-16", "2025-07-15"), self.book("b", "2025-07-16", "2026-07-15")]
        self.assertEqual([item["key"] for item in identity.for_day(books, "2025-07-10")], ["a"])
        self.assertEqual([item["key"] for item in identity.for_day(books, "2025-07-20")], ["b"])

    def test_next_year_goes_into_the_old_book_until_the_new_one_exists(self):
        """用户 2026-10-16：新年度账套还没建时，下一年的单记进旧账套的次年段（认 next_end）；建好就推往新账套；两份同期停下。"""
        old = dict(self.book("a", "2025-01-01", "2025-12-31"), next_end="2026-12-31")
        new = dict(self.book("b", "2026-01-01", "2026-12-31"), next_end="2027-12-31")
        twin = dict(self.book("c", "2025-01-01", "2025-12-31"), next_end="2026-12-31")
        keys = lambda found: [item["key"] for item in found]  # noqa: E731
        self.assertEqual(keys(identity.for_day([old, new], "2026-03-01")), ["b"])        # 新账套已建
        self.assertEqual(keys(identity.for_day([old], "2026-03-01")), ["a"])             # 未建：旧账套次年段
        self.assertEqual(keys(identity.for_day([old, twin], "2026-03-01")), ["a", "c"])  # 两份同期：调用方停下
        self.assertEqual(identity.for_day([old], "2027-01-01"), [])                     # 晚于次年段
        self.assertEqual(identity.for_day([self.book("a", "2025-01-01", "2025-12-31")], "2026-03-01"), [])  # 没报次年段

    def test_books_without_a_period_never_cover_a_day(self):
        self.assertEqual(identity.for_day([self.book("a", "", "")], "2025-01-01"), [])

    def test_branch_filter(self):
        books = [self.book("hq", "2026-01-01", "2026-12-31"), self.book("b12", "2026-01-01", "2026-12-31", "00012"),
                 self.book("old", "2024-01-01", "2024-12-31", "")]
        self.assertEqual([item["key"] for item in identity.for_branch(books, "00012")], ["b12"])
        self.assertEqual([item["key"] for item in identity.for_branch(books, "")], ["hq", "b12", "old"])
        # 这家公司只报过一个分店号：没报分店号的旧年度账套也算它的
        single = [self.book("hq", "2026-01-01", "2026-12-31"), self.book("old", "2024-01-01", "2024-12-31", "")]
        self.assertEqual([item["key"] for item in identity.for_branch(single, "00000")], ["hq", "old"])


class CrossYearPushTests(Base):
    def test_two_years_push_each_ticket_into_its_own_year(self):
        rule = self.rule()
        old, new = self.ticket("H1", date(2025, 12, 20)), self.ticket("H2", date(2026, 1, 5))
        first, second = self.send(rule, old), self.send(rule, new)
        self.assertEqual([item[1] for item in self.fake.submitted], [_key(T8), _key(Y26)])
        self.assertEqual((first["status"], first["account_set"], first["account_label"]),
                         ("sent", _key(T8), "SINCER 2568"))
        self.assertEqual((second["status"], second["account_set"], second["account_label"]),
                         ("sent", _key(Y26), "SINCER 2569"))
        # 选到的账套抄到票上（票上、CC、待处理页按实际那份显示）
        self.assertEqual(self.fake.retargeted, [("H1", "C1", _key(T8)), ("H2", "C1", _key(Y26))])
        # 推送仍走同一个推送服务：期间、税号、分店的硬闸在 `account_match`，这里不放宽也不绕过
        self.assertEqual(self.fake.views, [_key(T8), _key(Y26)])

    def test_only_the_old_year_on_file_stops_the_new_year_ticket(self):
        env.reset(C1=[dict(self.CATALOG[0]), dict(self.CATALOG[1])])
        frappe.hooks = dict(HOOKS)
        frappe.db.tables["Cowork ERP Connection"]["C1"].update(adapter="express", record_revision=1)
        company = env.make("C1", T8)["id"]
        result = self.send(self.rule(company=company), self.ticket("H3", date(2026, 2, 1)))
        self.assertEqual(result, {"status": "held", "reason": folder_contracts.ACCOUNT_SET_NONE})
        self.assertEqual(self.fake.failures, [("H3", folder_contracts.ACCOUNT_SET_NONE, {})])
        self.assertEqual((self.fake.submitted, self.fake.retargeted), ([], []))

    def test_two_account_sets_for_the_same_period_stop(self):
        """Express 测试数据：T8 与 T9 都是 2025 年。不按名称、不按新旧去猜。"""
        env.reset(C1=[dict(item) for item in self.CATALOG] + [row(T9, "SINCER 2568 (2)", "2025-01-01", "2025-12-31")])
        frappe.hooks = dict(HOOKS)
        frappe.db.tables["Cowork ERP Connection"]["C1"].update(adapter="express", record_revision=1)
        company = env.make("C1", T8)["id"]
        result = self.send(self.rule(company=company), self.ticket("H4", date(2025, 6, 30)))
        self.assertEqual(result, {"status": "held", "reason": folder_contracts.ACCOUNT_SET_AMBIGUOUS})
        self.assertEqual(self.fake.submitted, [])
        # 2026 年的票照样推往唯一的那份
        self.assertEqual(self.send(self.rule(company=company), self.ticket("H5", date(2026, 6, 30)))["account_set"],
                         _key(Y26))

    def test_next_year_ticket_without_a_new_book_goes_into_the_old_books_next_year(self):
        """有意变化（用户 2026-10-16）：原先「没有覆盖这一天的账套就停下」；现在新年度账套还没建时推往旧账套的次年段。"""
        env.reset(C1=[dict(self.CATALOG[0]), row(T8, "SINCER 2568", "2025-01-01", "2025-12-31", next_end="2026-12-31")])
        frappe.hooks = dict(HOOKS)
        frappe.db.tables["Cowork ERP Connection"]["C1"].update(adapter="express", record_revision=1)
        company = env.make("C1", T8)["id"]
        result = self.send(self.rule(company=company), self.ticket("H3", date(2026, 2, 1)))
        self.assertEqual((result["status"], result["account_set"]), ("sent", _key(T8)))
        self.assertEqual(self.fake.submitted[-1][1], _key(T8))

    def test_next_year_ticket_goes_to_the_new_book_once_it_exists(self):
        catalog = [dict(self.CATALOG[0]), row(T8, "SINCER 2568", "2025-01-01", "2025-12-31", next_end="2026-12-31"),
                   row(Y26, "SINCER 2569", "2026-01-01", "2026-12-31", next_end="2027-12-31")]
        env.reset(C1=catalog)
        frappe.hooks = dict(HOOKS)
        frappe.db.tables["Cowork ERP Connection"]["C1"].update(adapter="express", record_revision=1)
        company = env.make("C1", T8)["id"]
        result = self.send(self.rule(company=company), self.ticket("H3", date(2026, 2, 1)))
        self.assertEqual((result["status"], result["account_set"]), ("sent", _key(Y26)))

    def test_two_old_books_with_the_same_next_year_stop(self):
        catalog = [row(T8, "SINCER 2568", "2025-01-01", "2025-12-31", next_end="2026-12-31"),
                   row(T9, "SINCER 2568 (2)", "2025-01-01", "2025-12-31", next_end="2026-12-31")]
        env.reset(C1=catalog)
        frappe.hooks = dict(HOOKS)
        frappe.db.tables["Cowork ERP Connection"]["C1"].update(adapter="express", record_revision=1)
        company = env.make("C1", T8)["id"]
        result = self.send(self.rule(company=company), self.ticket("H3", date(2026, 2, 1)))
        self.assertEqual(result, {"status": "held", "reason": folder_contracts.ACCOUNT_SET_AMBIGUOUS})
        self.assertEqual(self.fake.submitted, [])

    def test_a_ticket_without_a_date_stops(self):
        result = self.send(self.rule(), self.ticket("H6", None))
        self.assertEqual(result["reason"], folder_contracts.ACCOUNT_SET_NONE)
        self.assertEqual(self.fake.submitted, [])

    def test_an_unreachable_account_set_stops_with_its_name(self):
        self.pool.writers.discard(("C1", _key(Y26)))
        result = self.send(self.rule(), self.ticket("H7", date(2026, 3, 3)))
        self.assertEqual(result, {"status": "held", "reason": folder_contracts.ACCOUNT_SET_UNREACHABLE,
                                  "account_set": _key(Y26), "account_label": "SINCER 2569"})
        self.assertEqual(self.fake.failures, [("H7", folder_contracts.ACCOUNT_SET_UNREACHABLE,
                                               {"connection": "C1", "account_set": _key(Y26)})])
        self.assertEqual(self.fake.submitted, [])
        # 负责人一台电脑都没有（电脑没连上）：不是这份账套的问题，过一会儿再推；最后一次照样停下
        self.fake.writer_state = "missing"
        self.fake.failures.clear()
        self.assertEqual(self.send(self.rule(), "H7", final=False), {"status": "retry", "reason": self.push.OFFLINE})
        self.assertEqual(self.fake.failures, [])
        self.assertEqual(self.send(self.rule(), "H7", final=True)["status"], "held")
        # 读文件夹的那台在、却一份能写的账套都够不到（10-16 审查）：不是暂时的，第一次就停在「访问不到」，不重试
        self.fake.writer_state = "unreachable"
        self.assertEqual(self.send(self.rule(), "H7", final=False)["reason"], folder_contracts.ACCOUNT_SET_UNREACHABLE)

    def test_reachability_is_judged_by_the_computer_that_reads_the_folder(self):
        # 用户 10-16（ADR-021 第 51 条）：跟到的账套够不够得到按读这个文件夹的那台电脑判（只有它写），推送也冻结它；
        # 旧任务没记电脑时按负责人（当前身份）自己的电脑
        self.send(self.rule(reader_device="CCD-R"), self.ticket("H8", date(2026, 3, 3)))
        self.assertEqual(self.pool.asked[-1], ("C1", _key(Y26), frappe.session.user, "CCD-R"))
        self.assertEqual(self.fake.routes[-1], "CCD-R")   # 文件行没记电脑（替身 entry 没有）：用任务上的
        self.send(self.rule(), self.ticket("H9", date(2026, 3, 3)))
        self.assertEqual((self.pool.asked[-1][3], self.fake.routes[-1]), ("", ""))
        # 10-16 审查：文件行记的电脑优先于任务上的（任务改由别的电脑读，在途的票仍由读它的那台写），判够不够得到与冻结的是同一台
        self.send(self.rule(reader_device="CCD-NEW"), self.ticket("H10", date(2026, 3, 3)),
                  entry=types.SimpleNamespace(name="E-OLD", filename="b.pdf", reader_device="CCD-OLD"))
        self.assertEqual((self.pool.asked[-1][3], self.fake.routes[-1]), ("CCD-OLD", "CCD-OLD"))

    def test_a_catalog_row_marked_not_writable_is_unreachable(self):
        env.reset(C1=[dict(self.CATALOG[1]), row(Y26, "SINCER 2569", "2026-01-01", "2026-12-31", writable=False)])
        frappe.hooks = dict(HOOKS)
        frappe.db.tables["Cowork ERP Connection"]["C1"].update(adapter="express", record_revision=1)
        company = env.make("C1", T8)["id"]
        result = self.send(self.rule(company=company), self.ticket("H8", date(2026, 3, 3)))
        self.assertEqual(result["reason"], folder_contracts.ACCOUNT_SET_UNREACHABLE)

    def test_a_task_owner_who_lost_the_connection_stops_instead_of_guessing(self):
        connections.users["C1"] = {"someone@example.com"}
        result = self.send(self.rule(), self.ticket("H9", date(2025, 3, 3)))
        self.assertEqual(result["reason"], folder_contracts.ACCOUNT_SET_NONE)
        self.assertEqual(self.fake.submitted, [])


class BranchTests(Base):
    CATALOG = (row(Y26, "SINCER 2569", "2026-01-01", "2026-12-31"),
               row("S:\\70EXP\\B12", "SINCER สาขา 12 2569", "2026-01-01", "2026-12-31", branch="00012"))
    SEED = Y26

    def test_the_task_branch_picks_that_branch_set(self):
        self.pool.writers.add(("C1", _key("S:\\70EXP\\B12")))
        result = self.send(self.rule(account_set_key="S:\\70EXP\\B12", company_branch="00012"),
                           self.ticket("H1", date(2026, 5, 1)))
        self.assertEqual(result["account_set"], _key("S:\\70EXP\\B12"))
        result = self.send(self.rule(account_set_key=Y26, company_branch="00000"), self.ticket("H2", date(2026, 5, 1)))
        self.assertEqual(result["account_set"], _key(Y26))
        self.assertEqual(tasks.company_of("C1", "S:\\70EXP\\B12"), {"company": self.company, "branch": "00012"})

    def test_no_branch_on_a_company_with_two_branches_stops(self):
        result = self.send(self.rule(company_branch=""), self.ticket("H3", date(2026, 5, 1)))
        self.assertEqual(result["reason"], folder_contracts.ACCOUNT_SET_AMBIGUOUS)


class OldTaskTests(Base):
    def test_a_task_without_a_company_pushes_to_its_fixed_account_set(self):
        called = []
        frappe.hooks = {"cowork_task_books": ["t1_spy.books"], "cowork_task_company": ["t1_spy.company"]}
        sys.modules["t1_spy"] = types.SimpleNamespace(books=lambda *a: called.append(a), company=lambda *a: called.append(a))
        try:
            result = self.send(self.rule(company="", company_branch=""), self.ticket("H1", date(2026, 5, 1)))
        finally:
            sys.modules.pop("t1_spy", None)
        self.assertEqual(result, {"status": "sent", "record": "CWP-1"})  # 去向里没有新加的两项
        self.assertEqual([item[1] for item in self.fake.submitted], [T8])  # 2026 的票照旧推往固定的 2568（硬闸会拦）
        self.assertEqual((called, self.fake.retargeted, self.pool.asked), ([], [], []))

    def test_a_deleted_company_falls_back_to_the_fixed_account_set(self):
        result = self.send(self.rule(company="gone"), self.ticket("H2", date(2025, 5, 1)))
        self.assertEqual([item[1] for item in self.fake.submitted], [T8])
        self.assertNotIn("account_set", result)


class DefaultsTests(Base):
    def test_task_defaults_follow_only_where_the_codes_exist(self):
        rule = self.rule(defaults_json=json.dumps({"account": "5300", "payment": "cash", "department": "HQ"}))
        self.pool.masters = {_key(Y26): {"reported_accounts": [{"code": "5300"}], "reported_departments": []}}
        self.send(rule, self.ticket("H1", date(2026, 5, 1)))  # 新年度：科目在、部门不在 → 部门不用
        self.send(rule, self.ticket("H2", date(2025, 5, 1)))  # 任务存下的那份：原样
        self.pool.masters = {}
        self.send(rule, self.ticket("H3", date(2026, 5, 2)))  # 新年度科目表还没读到：科目、部门都先不用
        fixed = [item[2] for item in self.fake.submitted]
        self.assertEqual(fixed[0], {"account": "5300", "payment": "cash"})
        self.assertEqual(fixed[1], {"account": "5300", "payment": "cash", "department": "HQ"})
        self.assertEqual(fixed[2], {"payment": "cash"})

    def test_a_human_repush_into_another_year_of_the_company_keeps_the_task_defaults(self):
        rule = self.rule(defaults_json=json.dumps({"payment": "credit"}))
        frappe.db.tables[RULE] = {"R1": env.Row(rule)}
        frappe.db.tables[FILE] = {"F1": env.Row(name="F1", rule="R1", source_file="SF-H1", histories_json='["H1"]')}
        self.ticket("H1", date(2026, 5, 1))
        env.fresh_request()
        self.assertEqual(self.push.fixed_for("H1", "C1", Y26), {"payment": "credit"})
        self.assertEqual(self.push.fixed_for("H1", "C1", "S:\\70EXP\\OTHERCO"), {})
        frappe.db.tables[RULE]["R1"].update(company="")
        self.assertEqual(self.push.fixed_for("H1", "C1", Y26), {})  # 不跟随的任务：只认自己的那份


class TicketTargetTests(unittest.TestCase):
    def test_retarget_replaces_the_recognised_target_once(self):
        env.reset()
        frappe.db.tables[HISTORY] = {"H1": env.Row(name="H1", push_connection="C1", push_account_set=T8)}
        writes = []
        original = frappe.db.set_value
        frappe.db.set_value = lambda *a, **k: writes.append(a) or original(*a, **k)
        push_state = _load("t1_push_state", APP / "history/push_state.py", {})
        push_state.retarget("H1", "C1", _key(Y26))
        push_state.retarget("H1", "C1", _key(Y26))
        push_state.retarget("H1", "", _key(Y26))
        self.assertEqual(frappe.db.tables[HISTORY]["H1"]["push_account_set"], _key(Y26))
        self.assertEqual(len(writes), 1)


class ResultTests(unittest.TestCase):
    def test_stopped_tickets_count_as_waiting_for_an_account_set(self):
        for code in folder_contracts.ACCOUNT_SET_STOPS:
            self.assertEqual(folder_contracts.ticket_state({"status": "held", "reason": code}, ""),
                             ("stopped", "account_set"))
        self.assertEqual(folder_contracts.ticket_state({"status": "held", "reason": "choice_needed:account"}, ""),
                         ("stopped", "choice:account"))
        self.assertEqual(folder_contracts.ticket_state({"status": "held", "reason": code}, "success"), ("pushed", ""))

    def test_results_name_the_account_sets_actually_used(self):
        files = [{"outcome_json": json.dumps({"H1": {"status": "sent", "account_set": "a", "account_label": "ABC 2568"},
                                              "H2": {"status": "sent", "account_set": "b", "account_label": "ABC 2569"},
                                              "H3": {"status": "sent", "account_set": "b", "account_label": "ABC 2569"},
                                              "_retry": {"H4": 1}, "H5": {"status": "sent", "record": "x"}})}]
        self.assertEqual(folder_contracts.account_sets(files), [
            {"account_set_key": "b", "account_label": "ABC 2569", "tickets": 2},
            {"account_set_key": "a", "account_label": "ABC 2568", "tickets": 1}])
        self.assertEqual(folder_contracts.account_sets([{"outcome_json": '{"H1": {"status": "sent"}}'}]), [])

    def test_reason_codes_have_three_languages(self):
        import csv

        with open(APP / "i18n/messages.tsv", encoding="utf-8") as handle:
            rows = {line["key"]: line for line in csv.DictReader(handle, delimiter="\t") if line.get("key")}
        for code in folder_contracts.ACCOUNT_SET_STOPS:
            self.assertTrue(all(rows[code][lang].strip() for lang in ("en", "zh", "th", "ja")), code)
        source = (APP / "folder_intake/briefs.py").read_text(encoding="utf-8")
        self.assertIn('found["stop_reasons"].get("account_set")', source)


class PatchTests(Base):
    """旧任务补公司（`v0_0_23_folder_task_company`）：唯一对上一家才补，对不上留空，可重复执行。"""

    def run_patch(self):
        rules = frappe.db.tables[RULE]
        frappe.db.has_column = lambda doctype, column: True
        frappe.db.sql = lambda query, values=None, as_dict=False: [
            env.Row(name=item["name"], connection=item["connection"], account_set_key=item["account_set_key"],
                    rule_owner=item["rule_owner"]) for item in sorted(rules.values(), key=lambda x: x["name"])
            if not item.get("company") and item.get("account_set_key") and item.get("rule_owner")
            and frappe.db.tables["Cowork ERP Connection"].get(item["connection"], {}).get("adapter") == "express"]
        writes = []
        original = frappe.db.set_value
        frappe.db.set_value = lambda *a, **k: writes.append(a) or original(*a, **k)
        patch = _load("t1_patch", APP / "patches/v0_0_23_folder_task_company.py", {})
        patch.execute()
        frappe.db.set_value = original
        return writes

    def test_backfill_is_unique_only_and_repeatable(self):
        frappe.db.tables["Cowork ERP Connection"]["M1"].update(adapter="mrerp")
        frappe.db.tables[RULE] = {
            "R1": env.Row(name="R1", connection="C1", account_set_key=T7, rule_owner="a@example.com", company=""),
            "R2": env.Row(name="R2", connection="C1", account_set_key="S:\\70EXP\\GONE", rule_owner="a@example.com",
                          company=""),
            "R3": env.Row(name="R3", connection="M1", account_set_key="2569", rule_owner="a@example.com", company=""),
            "R4": env.Row(name="R4", connection="C1", account_set_key=Y26, rule_owner="nobody@example.com", company=""),
            "R5": env.Row(name="R5", connection="C1", account_set_key=T8, rule_owner="a@example.com", company="KEEP",
                          company_branch="00000")}
        connections.users["C1"] = {"a@example.com"}  # R4 的负责人用不了这条连接：读不到目录，留空
        first = self.run_patch()
        rules = frappe.db.tables[RULE]
        self.assertEqual({name: (item.get("company"), item.get("company_branch")) for name, item in rules.items()}, {
            "R1": (self.company, "00000"), "R2": ("", None), "R3": ("", None), "R4": ("", None), "R5": ("KEEP", "00000")})
        self.assertEqual(len(first), 1)
        self.assertEqual(self.run_patch(), [])  # 再跑一次：没有新写入，结果不变
        self.assertEqual(rules["R1"]["company"], self.company)
        self.assertEqual(frappe.session.user, "a@example.com")  # 按负责人算完还原身份

    def test_patch_is_listed_after_the_model_sync(self):
        listed = (APP / "patches.txt").read_text(encoding="utf-8")
        self.assertGreater(listed.index("v0_0_23_folder_task_company"), listed.index("[post_model_sync]"))
        doctype = json.loads((APP / "pearnly_cowork/doctype/cowork_folder_intake_rule/cowork_folder_intake_rule.json")
                             .read_text(encoding="utf-8"))
        fields = {item["fieldname"]: item for item in doctype["fields"]}
        self.assertEqual((fields["company"]["fieldtype"], fields["company"]["read_only"]), ("Data", 1))
        self.assertEqual(fields["company_branch"]["read_only"], 1)
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        for name, targets in HOOKS.items():
            self.assertIn(f"{name} = {json.dumps(targets)}", hooks)


if __name__ == "__main__":
    unittest.main()
