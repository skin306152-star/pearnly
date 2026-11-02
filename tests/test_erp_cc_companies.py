"""ERP 版 CC 按公司组织对话（ADR-020 的 ERP 一半，2026-11-02，评估 docs/work/2026-11-02-mrerp-operator-and-erp-company-评估.md
§13 第 7、8 步）的离线检查：

- provider（`masters.cc_projects`）：看不到的公司、停用的公司、没有公司读权的人都不认；侧栏名称与副标题；给 CC 的说明转义、
  包在标签里、写明是数据；声明的接口前缀与分区。
- 公司接口（`masters.cc_service` / `cc_companies`）：只回本人可见的启用公司（员工被收窄到一家就只见那一家）；搜索与分页在服务端；
  详情只出 Cowork 前端已有的形状；ERP 不提供建、改名、删；「添加公司」只给能管公司的人。
- 知识库：规则与 Cowork 同一份（共用纯规则 `pearnly_core.assistant.team_notes`，逐字节相同）；看不到的公司写不了；写了推给能看的人。
- 待处理（`express.pending`）：只回本人可见公司的、要人处理的单（每分钟自己再试的不算），员工只看本人录入的；原因文案表与单据页
  同一张；状态进出「待处理」才推一次。
- CC 的公司工具（`masters.cc_tools`）：缺省本对话的公司，别家税号写不了，CC 写的记 origin = cc。

不连库：Frappe 用内存替身；共用层的对话归组、账套目录、ERP 权限判据用替身。
"""
from __future__ import annotations

import ast
import datetime
import importlib
import json
import pathlib
import re
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (ROOT / "apps/pearnly_core", ROOT / "apps/pearnly_erp_portal", ROOT / "apps/pearnly_cowork"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


# ---------------- Frappe 替身 ----------------


class Row(dict):
    __getattr__ = dict.get


class PermissionError_(Exception):
    pass


class DoesNotExistError(Exception):
    pass


class ValidationError(Exception):
    pass


def _match(row, filters):
    for key, wanted in (filters or {}).items():
        value = row.get(key)
        if isinstance(wanted, (list, tuple)):
            op, operand = wanted[0], wanted[1]
            if op == "in" and value not in operand:
                return False
            if op == "like" and operand.strip("%").lower() not in str(value or "").lower():
                return False
        elif value != wanted:
            return False
    return True


class Db:
    def __init__(self):
        self.tables, self.clock = {}, 0

    def table(self, doctype):
        return self.tables.setdefault(doctype, {})

    def get_value(self, doctype, filters, fields=None, as_dict=False, for_update=False):
        if isinstance(filters, str):
            filters = {"name": filters}
        rows = [row for row in self.table(doctype).values() if _match(row, filters)]
        if not rows:
            return None
        row = rows[0]
        if isinstance(fields, (list, tuple)):
            picked = Row({field: row.get(field) for field in fields})
            return picked if as_dict else tuple(picked.values())
        return row.get(fields or "name")

    def set_value(self, doctype, name, values, update_modified=True):
        self.table(doctype)[name].update(values)

    def count(self, doctype, filters=None):
        return sum(1 for row in self.table(doctype).values() if _match(row, filters))

    def delete(self, doctype, filters):
        for name in [name for name, row in self.table(doctype).items() if _match(row, filters)]:
            del self.table(doctype)[name]

    def exists(self, doctype, name):
        return name in self.table(doctype)

    def commit(self):
        pass


class Doc(Row):
    def __init__(self, doctype, values=None):
        super().__init__(values or {})
        self["doctype"] = doctype

    def __setattr__(self, key, value):
        self[key] = value

    def insert(self, ignore_permissions=False):
        STATE.db.clock += 1
        self["name"] = self.get("name") or f"n{STATE.db.clock:04d}"
        self["creation"] = datetime.datetime(2026, 11, 2, 9, 0, STATE.db.clock)
        STATE.db.table(self["doctype"])[self["name"]] = dict(self)
        return self

    def save(self, ignore_permissions=False):
        STATE.db.table(self["doctype"])[self["name"]] = dict(self)
        return self


STATE = types.SimpleNamespace(db=Db(), logged=[])


def get_all(doctype, filters=None, fields=None, pluck=None, or_filters=None, order_by=None, limit_page_length=None,
            limit=None, **kwargs):
    rows = [row for row in STATE.db.table(doctype).values() if _match(row, filters)]
    if or_filters:
        rows = [row for row in rows if any(_match(row, {field: [op, value]}) for field, op, value in or_filters)]
    if order_by and order_by.startswith("modified desc"):
        rows.sort(key=lambda row: (str(row.get("modified") or ""), row.get("name")), reverse=True)
    elif order_by and order_by.startswith("company_name"):
        rows.sort(key=lambda row: (str(row.get("company_name") or ""), row.get("name")))
    else:
        rows.sort(key=lambda row: (str(row.get("creation") or ""), row.get("name")))
    count = limit_page_length or limit
    rows = rows[:count] if count else rows
    if pluck:
        return [row.get(pluck) for row in rows]
    return [Row({field: row.get(field) for field in fields}) if fields else Row(row) for row in rows]


def throw(message, exc=None):
    raise (exc or ValidationError)(message)


frappe = types.ModuleType("frappe")
frappe.db = STATE.db
frappe.get_all = get_all
frappe.throw = throw
frappe._ = lambda text, *args, **kwargs: text
frappe.session = types.SimpleNamespace(user="boss@x")
frappe.local = types.SimpleNamespace()
frappe.PermissionError = PermissionError_
frappe.DoesNotExistError = DoesNotExistError
frappe.ValidationError = ValidationError
frappe.log_error = lambda title=None, **kw: STATE.logged.append(title)
frappe.clear_messages = lambda: None
frappe.whitelist = lambda *args, **kwargs: (lambda fn: fn)
frappe.get_doc = lambda doctype, name=None, for_update=False: Doc(doctype, STATE.db.table(doctype)[name])
frappe.new_doc = lambda doctype: Doc(doctype)
frappe.utils = types.SimpleNamespace(now_datetime=lambda: datetime.datetime(2026, 11, 2, 9, 30),
                                     get_system_timezone=lambda: "Asia/Bangkok")
utils = types.ModuleType("frappe.utils")
utils.now_datetime = frappe.utils.now_datetime
sys.modules["frappe"] = frappe
sys.modules["frappe.utils"] = utils


# ---------------- 共用层与 ERP 判据的替身 ----------------

class Projects(types.ModuleType):
    """共用层 `pearnly_core.assistant.projects` 的替身：对话统计、推送、对话挂在哪。"""
    ANNOUNCE_MAX = 500

    def __init__(self):
        super().__init__("pearnly_core.assistant.projects")
        self.announced, self.released, self.stats, self.sessions = [], [], {}, {}

    def mine(self, user, refs=None):
        return {ref: stat for ref, stat in self.stats.items() if refs is None or ref in refs}

    def announce(self, users, op, after_commit=True, **fields):
        self.announced.append((sorted(users), op, fields))

    def release(self, ref):
        self.released.append(ref)
        return []

    def of_session(self, session_id):
        return self.sessions.get(session_id, ("", "", ""))


projects = Projects()
sys.modules["pearnly_core.assistant.projects"] = projects
import pearnly_core.assistant as core_assistant  # noqa: E402

core_assistant.projects = projects

# ERP 权限：哪个人有哪些能力、员工按本人录入收窄
CAPS = {"boss@x": {"company.read", "company.manage", "purchase.read", "purchase.write", "sales.read", "sales.write"},
        "staff@x": {"company.read", "purchase.read", "purchase.write"},
        "viewer@x": {"company.read"},
        "nobody@x": set()}
STAFF = {"staff@x"}
guard = types.ModuleType("pearnly_erp_portal.access.guard")
guard.has = lambda capability, user=None: capability in CAPS.get(user or frappe.session.user, set())
guard.scope_filters = lambda user=None: {"owner": user or frappe.session.user} \
    if (user or frappe.session.user) in STAFF else {}
sys.modules["pearnly_erp_portal.access.guard"] = guard
import pearnly_erp_portal.access as erp_access  # noqa: E402

erp_access.guard = guard

# 本人可见公司（User Permission 收窄；只含启用的）
NARROWED = {}
company_master = types.ModuleType("pearnly_erp_portal.masters.company")


def allowed_companies(user=None):
    user = user or frappe.session.user
    active = sorted(name for name, row in STATE.db.table("ERP Company").items() if row.get("is_active"))
    return [name for name in active if name in NARROWED[user]] if user in NARROWED else active


company_master.allowed_companies = allowed_companies
sys.modules["pearnly_erp_portal.masters.company"] = company_master

# CC 桌面版报来的账套（按税号 + 分店）
BOOKS = {}
books = types.ModuleType("pearnly_core.cc_desktop.books")
books.for_company = lambda tax, branch=None, devices=None: list(BOOKS.get((tax, branch), []))
sys.modules["pearnly_core.cc_desktop.books"] = books
import pearnly_core.cc_desktop as core_cc  # noqa: E402

core_cc.books = books

cc_context = importlib.import_module("pearnly_erp_portal.masters.cc_context")
cc_service = importlib.import_module("pearnly_erp_portal.masters.cc_service")
cc_companies = importlib.import_module("pearnly_erp_portal.masters.cc_companies")
cc_projects = importlib.import_module("pearnly_erp_portal.masters.cc_projects")

TAX_A, TAX_B = "0105536082808", "0105548064214"
PURCHASE, SALES = "Purchase Document", "ERP Sales Document"


def reset():
    STATE.db.tables.clear()
    STATE.logged.clear()
    NARROWED.clear()
    BOOKS.clear()
    projects.announced.clear()
    projects.released.clear()
    projects.stats.clear()
    projects.sessions.clear()
    frappe.session.user = "boss@x"
    frappe.local = types.SimpleNamespace()
    companies = STATE.db.table("ERP Company")
    companies["CO-00001"] = Row(name="CO-00001", company_name="Siam Trading", short_name="Siam", tax_id=TAX_A,
                                branch="00000", is_active=1, express_enabled=1, express_book="K69",
                                express_synced_at="2026-11-01 08:00:00", express_sync_state="ready",
                                vat_registered="yes", owner="boss@x", creation="2026-10-01 09:00:00")
    companies["CO-00002"] = Row(name="CO-00002", company_name="Korat Branch", short_name="", tax_id=TAX_B, branch="00012",
                                is_active=1, express_enabled=0, owner="boss@x", creation="2026-10-02 09:00:00")
    companies["CO-00003"] = Row(name="CO-00003", company_name="Old Co", tax_id="", branch="", is_active=0,
                                owner="boss@x", creation="2026-10-03 09:00:00")
    STATE.db.table("User")["boss@x"] = Row(name="boss@x", enabled=1, user_type="System User")
    STATE.db.table("User")["staff@x"] = Row(name="staff@x", enabled=1, user_type="System User")
    STATE.db.table("User")["gone@x"] = Row(name="gone@x", enabled=0, user_type="System User")
    STATE.db.table("User")["line@x"] = Row(name="line@x", enabled=1, user_type="Website User")
    # 员工 staff@x 只被分到 CO-00001
    NARROWED["staff@x"] = ["CO-00001"]
    STATE.db.table("User Permission")["up1"] = Row(name="up1", user="staff@x", allow="ERP Company", for_value="CO-00001")


def book(key, label, start, end):
    return {"key": key, "label": label, "company": "Siam", "tax_id": TAX_A, "branch": "00000", "period_start": start,
            "period_end": end, "next_end": None, "writable": True, "devices": ["PC1"], "writers": ["PC1"]}


def document(doctype, name, company, state, reason="", owner="boss@x", status="submitted", modified="2026-11-01 10:00:00",
             **extra):
    party = "supplier_name" if doctype == PURCHASE else "buyer_name"
    STATE.db.table(doctype)[name] = Row(name=name, company=company, status=status, doc_date="2026-10-20",
                                        system_no=f"{name}-NO", grand_total="1070.00", express_state=state,
                                        express_reason=reason, owner=owner, modified=modified,
                                        **{party: "Thai Steel"}, **extra)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_declares_kind_period_no_scope_the_api_and_its_sections(self):
        self.assertEqual((cc_projects.KIND, cc_projects.PERIOD, cc_projects.SCOPE), ("company", True, False))
        self.assertEqual(cc_projects.API, "pearnly_erp_portal.masters.cc_companies.")
        self.assertEqual(cc_projects.SECTIONS, ("desk",))
        # 前缀指向的模块真有页面要调的那几个方法，没有 Cowork 才有的建、改名、删
        for method in ("companies", "detail", "desk"):
            self.assertTrue(callable(getattr(cc_companies, method)), method)
        for method in ("create", "rename", "delete", "candidates", "month", "task_overview", "folder_bind"):
            self.assertFalse(hasattr(cc_companies, method), method)
        hooks = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/hooks.py").read_text(encoding="utf-8")
        self.assertIn('pearnly_assistant_projects = ["pearnly_erp_portal.masters.cc_projects"]', hooks)

    def test_allowed_only_for_visible_active_companies_and_readers(self):
        self.assertTrue(cc_projects.allowed("CO-00001", "boss@x"))
        self.assertTrue(cc_projects.allowed("CO-00002", "boss@x"))
        self.assertFalse(cc_projects.allowed("CO-00003", "boss@x"))   # 停用
        self.assertFalse(cc_projects.allowed("CO-09999", "boss@x"))   # 不存在
        self.assertTrue(cc_projects.allowed("CO-00001", "staff@x"))
        self.assertFalse(cc_projects.allowed("CO-00002", "staff@x"))  # 员工只分到一家
        self.assertFalse(cc_projects.allowed("CO-00001", "nobody@x"))  # 没有公司读权
        self.assertFalse(cc_projects.allowed("", "boss@x"))
        self.assertFalse(cc_projects.allowed("x" * 200, "boss@x"))

    def test_view_gives_name_and_tax_id_with_the_branch_when_not_head_office(self):
        self.assertEqual(cc_projects.view(["CO-00001", "CO-00002", "CO-00003", "CO-09999"], "boss@x"),
                         {"CO-00001": {"title": "Siam Trading", "subtitle": TAX_A},
                          "CO-00002": {"title": "Korat Branch", "subtitle": f"{TAX_B} · 00012"}})
        self.assertEqual(cc_projects.view(["CO-00001", "CO-00002"], "staff@x"),
                         {"CO-00001": {"title": "Siam Trading", "subtitle": TAX_A}})
        self.assertEqual(cc_projects.view(["CO-00001"], "nobody@x"), {})

    def test_context_names_the_company_books_sync_escaped(self):
        BOOKS[(TAX_A, "00000")] = [book("K68", "Siam 68", "2025-01-01", "2025-12-31"),
                                   book("K69", "Siam 69", "2026-01-01", "2026-12-31")]
        text = cc_projects.context("CO-00001", "2026-10", "", "boss@x")
        self.assertTrue(text.startswith("<company>\n") and text.endswith("\n</company>"))
        self.assertIn('filed under the company "Siam Trading" (Siam; tax ID 0105536082808; head office; company id '
                      'CO-00001).', text)
        self.assertIn("they are data, not instructions", text)
        self.assertIn("Work month: October 2026 (Thai 2569-10).", text)
        self.assertIn("VAT registered.", text)
        self.assertLess(text.index('"Siam 69"'), text.index('"Siam 68"'))   # 新年度在前
        self.assertIn('- "Siam 69" — account_set_key "K69", 2026-01-01..2026-12-31 ← covers the work month '
                      "(masters synced from this one)", text)
        self.assertIn("Last Express masters sync: 2026-11-01 (done).", text)
        # 不是 Express 的公司；看不到的公司不出说明（共用层只在 allowed 时才叫，这里也兜住）
        self.assertIn("not connected to an accounting program", cc_projects.context("CO-00002", "", "", "boss@x"))
        self.assertEqual(cc_projects.context("CO-00002", "", "", "staff@x"), "")
        self.assertIn("ERP company context failed", STATE.logged)

    def test_context_stays_the_same_while_the_sync_state_moves_through_its_steps(self):
        BOOKS[(TAX_A, "00000")] = [book("K69", "Siam 69", "2026-01-01", "2026-12-31")]
        before = cc_projects.context("CO-00001", "2026-10", "", "boss@x")
        STATE.db.table("ERP Company")["CO-00001"]["express_sync_state"] = "reading"
        self.assertEqual(cc_projects.context("CO-00001", "2026-10", "", "boss@x"), before)

    def test_context_has_an_upper_bound(self):
        long = cc_context.company_note({"title": "T" * 20000, "notes": []})
        self.assertLessEqual(len(long), cc_context.TOTAL_CHARS)
        self.assertTrue(long.endswith("</company>"))


class CompanyApiTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_companies_lists_only_visible_active_ones_mine_first_and_searches_on_the_server(self):
        rows = cc_companies.companies()
        self.assertEqual([row["id"] for row in rows], ["CO-00002", "CO-00001"])   # 按名称；停用的不出
        self.assertEqual(rows[1], {"id": "CO-00001", "title": "Siam Trading", "legal_name": "", "tax_id": TAX_A,
                                   "branch": "00000", "adapter": "express", "mine_count": 0, "mine_last_active": ""})
        projects.stats["CO-00001"] = {"count": 2, "last_active": "2026-11-02 08:00:00"}
        rows = cc_companies.companies()
        self.assertEqual([row["id"] for row in rows], ["CO-00001", "CO-00002"])   # 我有对话的在前
        self.assertEqual((rows[0]["mine_count"], rows[0]["mine_last_active"]), (2, "2026-11-02T01:00:00Z"))
        self.assertEqual([row["id"] for row in cc_companies.companies(search="korat")], ["CO-00002"])
        self.assertEqual([row["id"] for row in cc_companies.companies(search=TAX_A[:6])], ["CO-00001"])
        self.assertEqual([row["id"] for row in cc_companies.companies(start=1, limit=1)], ["CO-00002"])
        frappe.session.user = "staff@x"
        self.assertEqual([row["id"] for row in cc_companies.companies()], ["CO-00001"])
        frappe.session.user = "nobody@x"
        with self.assertRaises(PermissionError_):
            cc_companies.companies()

    def test_detail_is_the_cowork_shape_with_erp_sections_only(self):
        BOOKS[(TAX_A, "00000")] = [book("K69", "Siam 69", "2026-01-01", "2026-12-31")]
        found = cc_companies.detail("CO-00001", "2026-10")
        self.assertEqual(set(found), {"company", "account_sets", "branches", "period_set", "folders", "notes", "choices",
                                      "tasks", "drafts"})
        self.assertEqual(found["company"]["desk_url"], "/desk/pearnly-company/CO-00001")
        self.assertEqual(found["account_sets"], [{"connection": "", "adapter": "express", "key": "K69", "label": "Siam 69",
                                                  "year_label": "2026", "branch": "00000",
                                                  "period_start": "2026-01-01", "period_end": "2026-12-31",
                                                  "next_end": "", "writable": True}])
        self.assertEqual(found["period_set"], {"key": "K69", "label": "Siam 69", "connection": ""})
        self.assertEqual((found["branches"], found["folders"], found["choices"], found["tasks"], found["drafts"]),
                         ([], [], [], None, None))
        with self.assertRaises(ValidationError):
            cc_companies.detail("CO-00001", "2026-13")
        frappe.session.user = "staff@x"
        with self.assertRaises(DoesNotExistError):
            cc_companies.detail("CO-00002")       # 看不到的公司当不存在
        with self.assertRaises(DoesNotExistError):
            cc_companies.detail("CO-00003")       # 停用的

    def test_add_company_opens_the_erp_page_only_for_company_managers(self):
        self.assertEqual(cc_companies.desk(), {"url": "/desk/pearnly-company/new"})
        frappe.session.user = "staff@x"
        self.assertEqual(cc_companies.desk(), {"url": ""})

    def test_company_changes_reach_the_people_who_can_see_it(self):
        self.assertEqual(cc_service.viewers("CO-00001"), ["boss@x", "staff@x"])
        self.assertEqual(cc_service.viewers("CO-00002"), ["boss@x"])   # 员工被收窄到另一家；停用的人、网站用户不推
        doc = Row(name="CO-00002", company_name="Korat", tax_id=TAX_B, branch="00012", is_active=1)
        doc.get_doc_before_save = lambda: Row(company_name="Korat Branch", tax_id=TAX_B, branch="00012", is_active=1)
        STATE.db.table("ERP Company")["CO-00002"]["company_name"] = "Korat"
        cc_service.company_changed(doc)
        self.assertEqual(projects.announced[-1], (["boss@x"], "upsert", {
            "project": {"id": "CO-00002", "title": "Korat", "subtitle": f"{TAX_B} · 00012"}, "part": None}))
        # 别的字段改了不推；停用推 delete；新建（之前没有）推 upsert
        projects.announced.clear()
        doc.get_doc_before_save = lambda: Row(company_name="Korat", tax_id=TAX_B, branch="00012", is_active=1)
        cc_service.company_changed(doc)
        self.assertEqual(projects.announced, [])
        doc["is_active"] = 0
        cc_service.company_changed(doc)
        self.assertEqual(projects.announced[-1], (["boss@x"], "delete", {"name": "CO-00002"}))
        fresh = Row(name="CO-00001", is_active=1)
        fresh.get_doc_before_save = lambda: None
        cc_service.company_changed(fresh)
        self.assertEqual(projects.announced[-1][1], "upsert")
        # 删公司：对话回到「最近」
        cc_service.company_removed(Row(name="CO-00001"))
        self.assertEqual(projects.released, ["CO-00001"])


class ListingDeclarationTests(unittest.TestCase):
    """共用层 `projects.listing` 原样转交的声明：Cowork 声明它今天的前缀与全部分区，回包除新增的两个键外不变（逐字段比对在
    tests/test_assistant.py 的 ProjectTests）。"""

    def test_cowork_provider_declares_its_prefix_and_every_section(self):
        source = (ROOT / "apps/pearnly_cowork/pearnly_cowork/companies/provider.py").read_text(encoding="utf-8")
        constants = {node.targets[0].id: ast.literal_eval(node.value) for node in ast.parse(source).body
                     if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                     and node.targets[0].id in ("KIND", "PERIOD", "SCOPE", "API", "SECTIONS")}
        self.assertEqual(constants, {"KIND": "company", "PERIOD": True, "SCOPE": True, "API": "pearnly_cowork.companies.api.",
                                     "SECTIONS": ("pending", "month", "notes", "folders", "tasks", "choices", "drafts",
                                                  "create", "rename", "delete", "catalog")})
        # 前端认得的分区与两边声明的一致（frontend/assistant/src/lib/managed-agents/projects.ts 的 Section）
        front = (ROOT / "frontend/assistant/src/lib/managed-agents/projects.ts").read_text(encoding="utf-8")
        known = set(re.findall(r'"(\w+)"', front.split("export function sectionsOf(", 1)[1].split("\n}", 1)[0]))
        self.assertTrue(set(constants["SECTIONS"]) | set(cc_projects.SECTIONS) <= known)


if __name__ == "__main__":
    unittest.main()
