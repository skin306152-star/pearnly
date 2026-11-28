"""ERP 版 CC 的对话直写工具（评估 2026-11-02 §13 第 9 步，`pearnly_erp_portal.express.cc_tools`）的离线检查：

- 声明：工具由 ERP 自己在 hooks 的 ``pearnly_assistant_tools`` 声明（ADR-009），名字都是 ``erp_``，步骤名有四语译文；ERP 的工具
  汇总里没有别的产品的工具；要不要「允许」照 Cowork 同名工具（都不要）。
- 与 Cowork 共用的部分引用共用层同一份（`pearnly_core.assistant.direct_push`）：按公司的范围与翻页参数、记选择的项、结果不明的
  核对参数与说法、说话规矩的通用段。
- 权限（与 ERP 单据页同一判据）：看不到的公司、没有这类单据开单权的、员工只看 / 只推本人录入的；写别家的单要显式给 company。
- 列单：服务端分页（每类按页取、总数单独计），按推送状态与种类筛，五种写 Express 的单都列。
- 交直写只走 ERP 现有那条路：单据上的「再写一次」（`api.retry`）或提交时排直写的同一个函数（`push.send`）；要人选的不推；
  代录中的单不接；结果不明的不再推。记选择走单据上「选一次」（`api.answer`）。结果不明的核对走单据页对这种情形的「再写一次」。
- 推送后等结论：入账带 Express 单号；电脑那头没人写的不等。

不连库：Frappe 与 ERP 的单据页入口（`express.api`）、写账（`express.push`）、写账队列、权限判据用内存替身；状态码、原因文案、
共用层的说法用真的。
"""
from __future__ import annotations

import ast
import datetime
import importlib
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
ERP = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal"
COWORK = ROOT / "apps/pearnly_cowork/pearnly_cowork"
for path in (ROOT / "apps/pearnly_core", ROOT / "apps/pearnly_erp_portal"):
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
            if op == "is" and operand == "set" and not value:
                return False
            if op == "between" and not (operand[0] <= str(value or "")[:len(operand[0])] <= operand[1]):
                return False
            if op == "like" and operand.strip("%").lower() not in str(value or "").lower():
                return False
        elif value != wanted:
            return False
    return True


class Db:
    def __init__(self):
        self.tables, self.commits, self.rollbacks = {}, 0, 0
        self.on_rollback = None

    def table(self, doctype):
        return self.tables.setdefault(doctype, {})

    def get_value(self, doctype, name, fields=None, as_dict=False):
        row = self.table(doctype).get(name)
        if row is None:
            return None
        if isinstance(fields, (list, tuple)):
            picked = Row({field: row.get(field) for field in fields})
            return picked if as_dict else tuple(picked.values())
        return row.get(fields or "name")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1
        if self.on_rollback:
            self.on_rollback()


STATE = types.SimpleNamespace(db=Db(), queries=[])


def get_all(doctype, filters=None, fields=None, or_filters=None, order_by=None, limit_start=0, limit_page_length=None,
            pluck=None, **kwargs):
    STATE.queries.append({"doctype": doctype, "filters": filters, "or_filters": or_filters, "fields": fields,
                          "limit_start": limit_start, "limit_page_length": limit_page_length})
    rows = [Row(row) for row in STATE.db.table(doctype).values() if _match(row, filters)]
    if or_filters:
        rows = [row for row in rows if any(_match(row, {field: [op, value]}) for field, op, value in or_filters)]
    if fields and isinstance(fields[0], dict):
        return [Row(total=len(rows))]
    if order_by:
        key = order_by.split()[0]
        rows.sort(key=lambda row: (str(row.get(key) or ""), row.get("name")), reverse=True)
    rows = rows[limit_start:limit_start + limit_page_length if limit_page_length else None]
    if pluck:
        return [row.get(pluck) for row in rows]
    return [Row({field: row.get(field) for field in fields}) if fields else row for row in rows]


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
frappe.clear_messages = lambda: None
frappe.utils = types.ModuleType("frappe.utils")
frappe.utils.get_system_timezone = lambda: "Asia/Bangkok"
frappe.utils.now_datetime = datetime.datetime.now
frappe.utils.get_datetime = lambda value: value
frappe.utils.password = types.ModuleType("frappe.utils.password")
frappe.utils.password.get_decrypted_password = lambda *args, **kwargs: ""
frappe.__path__ = []  # 当包：MR.ERP 账本（`mrerp`）的模块从 frappe.utils 导入
sys.modules.update({"frappe": frappe, "frappe.utils": frappe.utils, "frappe.utils.password": frappe.utils.password})


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    parent, _sep, child = name.rpartition(".")
    if parent in sys.modules:
        setattr(sys.modules[parent], child, module)
    return module


# ---------------- ERP 的判据与入口（替身，按真的写法） ----------------

PURCHASE, SALES = "Purchase Document", "ERP Sales Document"
STOCK, POS_DAY, COUNT = "ERP Stock Entry", "ERP POS Day Summary", "ERP Stock Count"
CAPS = {"boss@x": {"purchase.read", "purchase.write", "sales.read", "sales.write", "stock.read", "stock.write",
                   "cashier.read", "cashier.write", "count.read", "count.write", "company.read"},
        "staff@x": {"purchase.read", "purchase.write", "company.read"},
        "viewer@x": {"purchase.read", "sales.read", "company.read"}}
STAFF = {"staff@x"}
ALLOWED = {"boss@x": ["CO-1", "CO-2"], "staff@x": ["CO-1"], "viewer@x": ["CO-1"]}


def _require(capability, user=None):
    if capability not in CAPS.get(user or frappe.session.user, set()):
        raise PermissionError_(capability)


guard = _module("pearnly_erp_portal.access.guard",
                has=lambda capability, user=None: capability in CAPS.get(user or frappe.session.user, set()),
                require=_require,
                scope_filters=lambda user=None: {"owner": user or frappe.session.user}
                if (user or frappe.session.user) in STAFF else {})
importlib.import_module("pearnly_erp_portal.access").guard = guard
sys.modules["pearnly_erp_portal.access.guard"] = guard

LOST = "cc_desktop.result_unknown"
JOBS = {}
# 此刻在等 Express 关闭的写账件（`jobs.express_wait_many` 的替身：真的按上次回报 90 秒内算，见 test_cc_desktop_channel）
WAITING, WAIT_CALLS = set(), []
SENTENCE = "Express 正在使用此账套，关闭后将自动写入"


def _express_wait_many(names, moment=None):
    WAIT_CALLS.append(sorted(names))
    return {name: "2026-11-12T03:00:00Z" for name in names if name in WAITING}


jobs = _module("pearnly_core.cc_desktop.jobs", LOST=LOST, find_by_name=lambda name: dict(JOBS[name]) if name in JOBS else None,
               express_wait_many=_express_wait_many)
importlib.import_module("pearnly_core.cc_desktop").jobs = jobs
# 电脑报来的账套名单（「没有账套」那句列出各本期间时读，`express.target.books_of`）：这里没有电脑报过
books = _module("pearnly_core.cc_desktop.books", for_company=lambda tax, branch=None, **kwargs: [],
                for_day=lambda found, day: [])
importlib.import_module("pearnly_core.cc_desktop").books = books

CALLS = []
ENABLED = {"CO-1": True, "CO-2": True}
OPERATOR = set()
WAREHOUSES = {"WH-1": "CO-1", "WH-2": "CO-2"}


def _company_of(doctype, name):
    row = STATE.db.table(doctype).get(name) or {}
    return WAREHOUSES.get(row.get("warehouse")) if doctype in (STOCK, COUNT) else row.get("company")


def _send(doctype, name, operator=None):
    CALLS.append(("send", doctype, name))
    STATE.db.table(doctype)[name].update(ledger_state="queued", ledger_job="J-" + name, ledger_reason="")
    JOBS["J-" + name] = {"job": "J-" + name, "status": "queued", "request_key": "erp:x:" + name, "reason": ""}
    return {"status": "sent"}


push = _module("pearnly_erp_portal.express.push", PURCHASE=PURCHASE, SALES=SALES, STOCK=STOCK, POS_DAY=POS_DAY,
               COUNT=COUNT, ready=lambda doctype: "Posted" if doctype == COUNT else "submitted",
               enabled=lambda company: ENABLED.get(company, False), company_of=_company_of,
               operator_owned=lambda doctype, name: name in OPERATOR, send=_send)

CAPABILITIES = {PURCHASE: ("purchase.read", "purchase.write"), SALES: ("sales.read", "sales.write"),
                STOCK: ("stock.read", "stock.write"), POS_DAY: ("cashier.read", "cashier.write"),
                COUNT: ("count.read", "count.write")}


def _document(doctype, name, write=False):
    """同 `express.api._document`：这类单据的查看权 / 开单权、公司在本人可见公司里。"""
    if doctype not in CAPABILITIES or not isinstance(name, str):
        raise PermissionError_("Not Permitted")
    _require(CAPABILITIES[doctype][1 if write else 0])
    row = STATE.db.table(doctype).get(name)
    if row:
        row = Row(row, company=_company_of(doctype, name))
    if not row or row.company not in ALLOWED.get(frappe.session.user, []):
        raise PermissionError_("Not Permitted")
    return row


def _view(doctype, row):
    """同 `express.api._view` 的要点：停下要人选的列 needed（带可选值），转人工的可「再写一次」。"""
    job = JOBS.get(row.ledger_job) if row.ledger_job else None
    needed = list(row.get("needed") or []) if row.ledger_state == "held" else []
    return {"needed": needed, "can_answer": bool(needed),
            "can_retry": bool(job and job["status"] == "manual" and not needed)}


def _retry(doctype, name):
    CALLS.append(("retry", doctype, name))
    row = _document(doctype, name, write=True)
    job = JOBS.get(row.ledger_job)
    if not job or job["status"] != "manual":
        raise PermissionError_("Not Permitted")
    job.update(status="queued", reason="")
    STATE.db.table(doctype)[name].update(ledger_state="queued", ledger_reason="")


def _answer(doctype, name, answers):
    CALLS.append(("answer", doctype, name, answers))
    row = _document(doctype, name, write=True)
    asked = {(item["kind"], item["subject"]): item for item in row.get("needed") or []}
    for given in answers:
        item = asked.get((given["kind"], given["subject"]))
        if not item or given["value"] not in {option["code"] for option in item["options"]}:
            raise ValidationError("Invalid request")
    STATE.db.table(doctype)[name].update(needed=None, ledger_reason="")
    _send(doctype, name)


def _remember_party_account(doctype, row, value):
    """同 `express.api.remember_party_account` 的出口（真的核科目、记下在 test_erp_express_direct.PartyAccount 测）。"""
    CALLS.append(("remember_party_account", doctype, row.name, value))
    return {"status": "saved", "company": row.company, "party": row.supplier_name, "subject": "purchase|x",
            "account": str(value).strip(), "account_name": "Freight"}


api = _module("pearnly_erp_portal.express.api", CAPABILITIES=CAPABILITIES, _document=_document, _view=_view,
              retry=_retry, answer=_answer, remember_party_account=_remember_party_account,
              resolve_unknown=lambda doctype, name, entered, docnum=None: CALLS.append(("resolve_unknown", doctype, name, entered, docnum)))
_module("pearnly_erp_portal.inventory.warehouses",
        names_of=lambda company=None: [name for name, owner in WAREHOUSES.items() if owner == company])

FILED = {"company": ""}


def _resolve(wanted, filed="", write=False):
    wanted = str(wanted or "").strip()
    if not wanted:
        return filed, None
    return (wanted, None) if wanted in ALLOWED.get(frappe.session.user, []) else ("", "unknown company: pass an id from erp_companies")


_module("pearnly_erp_portal.masters.cc_tools", filed=lambda: FILED["company"], resolve=_resolve)
_module("pearnly_erp_portal.masters.cc_service", visible=lambda user=None: list(ALLOWED.get(frappe.session.user, [])))

direct_push = importlib.import_module("pearnly_core.assistant.direct_push")
E = importlib.import_module("pearnly_erp_portal.express.contracts")
pending = importlib.import_module("pearnly_erp_portal.express.pending")
tools = importlib.import_module("pearnly_erp_portal.express.cc_tools")


def doc(doctype, name, company="CO-1", state="held", reason="", owner="boss@x", day="2026-10-20", **extra):
    row = {"name": name, "status": "Posted" if doctype == COUNT else "submitted", "owner": owner,
           "ledger_state": state, "ledger_reason": reason, "ledger_docnum": "", "ledger_job": None,
           "ledger_sent_at": None, **extra}
    date_field = {PURCHASE: "doc_date", SALES: "doc_date", STOCK: "posting_date", POS_DAY: "day", COUNT: "posted_at"}[doctype]
    row[date_field] = day
    if doctype in (STOCK, COUNT):
        row["warehouse"] = "WH-1" if company == "CO-1" else "WH-2"
    else:
        row["company"] = company
    if doctype == PURCHASE:
        row.update(system_no=name + "-NO", supplier_name="Thai Steel", grand_total="1070.00")
    if doctype == SALES:
        row.update(system_no=name + "-NO", buyer_name="Walk-in", grand_total="535.00")
    STATE.db.table(doctype)[name] = row
    return row


def reset():
    STATE.db.tables.clear()
    STATE.db.on_rollback = None
    STATE.db.rollbacks = STATE.db.commits = 0
    STATE.queries.clear()
    CALLS.clear()
    JOBS.clear()
    WAITING.clear()
    WAIT_CALLS.clear()
    OPERATOR.clear()
    ENABLED.update({"CO-1": True, "CO-2": True})
    FILED["company"] = "CO-1"
    frappe.session.user = "boss@x"
    frappe.local = types.SimpleNamespace(pearnly_assistant={"session_id": "s1", "may_wait": False})
    STATE.db.table("ERP Company").update({"CO-1": {"name": "CO-1", "company_name": "Siam Trading"},
                                          "CO-2": {"name": "CO-2", "company_name": "Korat Branch"}})


def call(fn, **values):
    return json.loads(fn(values))


def translated():
    """「在等 Express 关闭」那句按用户语言给（`pending.reason_label` 的词条翻译换成中文那一句）。"""
    return mock.patch.object(pending, "_", lambda text, *args, **kwargs:
                             SENTENCE if text == "express.error.ACCOUNT_BUSY_LOCKED" else text)


NEEDED = [{"kind": "account", "subject": "Fuel", "label": "Fuel",
           "options": [{"code": f"53-0{i}", "name": f"Expense {i}"} for i in range(40)]}]


class DeclarationTests(unittest.TestCase):
    def test_erp_declares_its_own_tools_with_four_language_step_names(self):
        hooks = (ERP / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('"pearnly_erp_portal.express.cc_tools.TOOLS"', hooks)
        self.assertIn('"pearnly_erp_portal.express.cc_tools.GUIDANCE"', hooks)
        self.assertIn('"pearnly_erp_portal.express.cc_tools.STEP_NAMES"', hooks)
        terms = {}
        for line in (ERP / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()[1:]:
            cells = line.split("\t")
            terms[cells[0]] = cells[1:4]
        names = [tool["name"] for tool in tools.TOOLS]
        self.assertEqual(sorted(names), sorted(tools.STEP_NAMES))
        self.assertEqual(set(names), {"erp_list_documents", "erp_push_documents", "erp_resolve_push_result", "erp_push_results",
                                      "erp_pending", "erp_document_detail", "erp_document_original",
                                      "erp_set_printed_total", "erp_party_account_save"})
        for tool in tools.TOOLS:
            module, _sep, func = tool["handler"].rpartition(".")
            self.assertEqual(module, "pearnly_erp_portal.express.cc_tools")
            self.assertTrue(callable(getattr(tools, func)), func)
            for text in (tool["label"], tools.STEP_NAMES[tool["name"]]):
                self.assertTrue(all(terms.get(text, [""])[:3]), f"{text} needs zh / th / ja")

    def test_the_words_are_cowork_s_and_so_is_whether_to_ask_allow(self):
        cowork = {}
        for line in (COWORK / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()[1:]:
            cells = line.split("\t")
            cowork[cells[0]] = cells[1:4]
        erp = {line.split("\t")[0]: line.split("\t")[1:4]
               for line in (ERP / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()[1:]}
        # 「待处理」在 Cowork 的公司工具里（`company_tools.cowork_pending`），其余在 `tools`
        source = "\n".join((COWORK / path).read_text(encoding="utf-8")
                            for path in ("assistant/tools.py", "assistant/company_tools.py"))
        for tool in tools.TOOLS:
            if tool["name"] in ("erp_set_printed_total", "erp_party_account_save"):
                continue  # 只有 ERP 有（Cowork 改票面用 cowork_edit_document、记公司选择用 cowork_company_choice_save）
            twin = "cowork_" + tool["name"][len("erp_"):]
            start = source.index(f'{{"name": "{twin}", "label": "{tool["label"]}"')  # 同名工具、同一个步骤名
            end = source.find('{"name": "cowork_', start + 10)
            if twin == "cowork_resolve_push_result":
                # A 线 11-15：Cowork 记成「没入账」后会照常再推（可能重复入账），先出「允许」卡片；ERP 记成「没入账」交回写账电脑
                # 按单号去 Express 查、有就记单号、没有才写一次（同单据上「再写一次」），不会重复，照旧不要卡片
                self.assertIn('"approval": True', source[start:end], twin)
            else:
                self.assertNotIn('"approval"', source[start:end], twin)  # Cowork 这几个不要「允许」，ERP 照做
            self.assertNotIn("approval", tool)
            for text in (tool["label"], tools.STEP_NAMES[tool["name"]]):
                self.assertEqual(erp[text], cowork[text], text)  # 译文与 Cowork 同一份

    def test_shared_parts_come_from_the_core(self):
        by_name = {tool["name"]: tool for tool in tools.TOOLS}
        self.assertEqual(by_name["erp_resolve_push_result"]["input_schema"]["required"],
                         direct_push.RESOLVE_SCHEMA["required"])
        # 账本叫法跟 MR.ERP 开关走（11-22 起开着：「Express or MR.ERP」）
        self.assertEqual(tools.PROGRAM, "Express or MR.ERP")
        self.assertTrue(by_name["erp_resolve_push_result"]["description"].startswith(
            "When a push result is unknown (Express or MR.ERP may or may not have it), record what the user found"))
        self.assertIn(direct_push.unconfirmed_rule(tools.PROGRAM, "erp_resolve_push_result"),
                      by_name["erp_push_documents"]["description"])
        props = by_name["erp_list_documents"]["input_schema"]["properties"]
        self.assertEqual({key: props[key] for key in ("company", "all_companies", "offset", "limit")},
                         {**direct_push.scope_props("erp_companies", "documents", "documents"),
                          **direct_push.page_props(direct_push.LIST_MAX)})
        for line in (direct_push.GUIDANCE_TIMES, direct_push.GUIDANCE_BAHT, direct_push.GUIDANCE_THIS_MONTH,
                     direct_push.GUIDANCE_TABLE_ROWS, direct_push.GUIDANCE_ENTERED_ONLY_BY_RECEIPT):
            self.assertIn(line, tools.GUIDANCE)
        # 线上 11-10（testcowork 代录 t4n5b25940）：识别存好、还没写进 Express，CC 说「已登记为 Pearnly 的采购单」。只有拿到写入回执
        # （单号）才说已录，两个产品同一句
        self.assertIn("direct_push.GUIDANCE_ENTERED_ONLY_BY_RECEIPT", (COWORK / "assistant/tools.py").read_text(encoding="utf-8"))
        for words in ("docnum", "recognized", "not entered"):
            self.assertIn(words, direct_push.GUIDANCE_ENTERED_ONLY_BY_RECEIPT)
        # 按单号、对方、金额、日期找单：与 Cowork 同一句的 ERP 版（盘点 2026-11-08 P1-3），ERP 单据没有文件名可搜
        find = direct_push.guidance_find_documents("erp_list_documents")
        self.assertIn(find, tools.GUIDANCE)
        self.assertIn("erp_list_documents search set to its invoice number (or the counterparty when there is no number)",
                      find)
        self.assertNotIn("file name", find)
        # 11-22 真模型 8 段：ERP 每段开头都多查一次待处理、回复多一句「目前没有待处理的单据」（Cowork 同一句共用提示）。待处理在对话下面
        # 已经列着：不主动查、不主动提、没有也不说；用户问或与所问有关时才查
        trouble = direct_push.guidance_troubleshooting("erp_pending")
        self.assertIn(trouble, tools.GUIDANCE)
        self.assertNotIn("first message", trouble)
        self.assertIn("do not check them with erp_pending or mention them on your own, and never say that there are none", trouble)
        self.assertIn('direct_push.guidance_troubleshooting("cowork_pending")', (COWORK / "assistant/tools.py").read_text(encoding="utf-8"))
        cowork = (COWORK / "assistant/tools.py").read_text(encoding="utf-8")
        for used in ("direct_push.resolve_description(", "direct_push.unconfirmed_rule(", "direct_push.scope_props(",
                     "direct_push.RESOLVE_SCHEMA", "direct_push.GUIDANCE_TIMES",
                     "direct_push.cc_desktop(", "_json = direct_push.dumps", "direct_push.guidance_find_documents("):
            self.assertIn(used, cowork)  # Cowork 引用同一份（字节一致由 test_cowork_push_tools_golden 锁住）

    def test_the_erp_tool_list_only_has_tools_erp_declares(self):
        tree = ast.parse((ERP / "hooks.py").read_text(encoding="utf-8"))
        paths = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                     and any(getattr(t, "id", "") == "pearnly_assistant_tools" for t in node.targets))
        self.assertTrue(all(path.startswith(("pearnly_erp_portal.", "pearnly_core.")) for path in paths), paths)
        self.assertFalse(any("cowork" in path for path in paths))
        self.assertTrue(all(tool["name"].startswith("erp_") for tool in tools.TOOLS))
        cowork_hooks = (COWORK / "hooks.py").read_text(encoding="utf-8")
        self.assertNotIn("pearnly_erp_portal.express.cc_tools", cowork_hooks)

    def test_pushing_only_goes_through_the_existing_erp_path(self):
        source = (ERP / "express/cc_tools.py").read_text(encoding="utf-8")
        for used in ("api.retry(", "push.send(", "api._document(", "push.operator_owned("):
            self.assertIn(used, source)
        for forbidden in ("jobs.enqueue", "jobs.cancel", "jobs.retry", "frappe.db.set_value", "frappe.db.sql", ".insert(",
                          ".save(", "ignore_permissions", "prepare.prepare", "choices.remember"):
            self.assertNotIn(forbidden, source)


class PendingToolTests(unittest.TestCase):
    """2026-11-03「CC 通用疑难处理」：erp_pending 与侧栏「待处理」同一个后端（`express.pending`）；识别来的单带 checks（复核码、
    票面合计、识别读到的合计、单据合计），CC 据此找根因，不另起一套判断。"""

    def setUp(self):
        reset()
        from unittest import mock
        companies = types.ModuleType("pearnly_erp_portal.masters.company")
        companies.allowed_companies = lambda user: list(ALLOWED.get(user, []))
        patcher = mock.patch.dict(sys.modules, {"pearnly_erp_portal.masters.company": companies})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pending_items_come_from_the_sidebar_backend_with_the_printed_total_checks(self):
        doc(PURCHASE, "PUR-7", reason=E.PRINTED_TOTAL_MISSING, ocr_review='{"codes":["total_missing"],"total":null}',
            printed_total=0)
        doc(PURCHASE, "PUR-8", state="written")  # 写进去了：不在待处理
        doc(PURCHASE, "PUR-9", company="CO-2", reason=E.PRINTED_TOTAL_MISMATCH)  # 别家：本对话挂的是 CO-1
        out = call(tools.pending_items)
        self.assertEqual((out["scope"], out["count"]), ("CO-1", 1))
        item = out["items"][0]
        self.assertEqual((item["document"], item["reason_code"]), ("P:PUR-7", E.PRINTED_TOTAL_MISSING))
        self.assertEqual(item["checks"], {"recognition": ["total_missing"], "printed_total": 0, "read_total": None,
                                          "document_total": "1070.00"})
        self.assertEqual(out["count"], pending.pending("CO-1")["count"])
        everything = call(tools.pending_items, all_companies=True)
        self.assertEqual(sorted(row["document"] for row in everything["items"]), ["P:PUR-7", "P:PUR-9"])

    def test_no_book_names_the_date_and_each_book_period_reading_the_books_once_per_company(self):
        """「没有账套」说出日期与已连接各本的期间（用户 11-14）；两张单同一家公司，账套名单只读一次（不逐行查）。"""
        from unittest import mock

        STATE.db.table("ERP Company")["CO-1"] = {"name": "CO-1", "tax_id": "0105555000001", "branch": ""}
        asked = []
        nlt = {"key": "k", "label": "NLTTEST", "period_start": "2024-01-01", "period_end": "2024-12-31",
               "next_end": "2025-12-31"}
        with mock.patch.object(books, "for_company", side_effect=lambda tax, branch=None, **kw: asked.append(tax) or [nlt]):
            doc(SALES, "SD-5", reason=E.NO_BOOK, day="2026-11-13")
            doc(PURCHASE, "PUR-6", reason=E.NO_BOOK, day="2026-11-14")
            labels = {item["document"]: item["reason"] for item in call(tools.pending_items)["items"]}
        self.assertEqual(labels, {
            "S:SD-5": "2026-11-13 is outside the periods of the connected account sets: NLTTEST 2024-01-01–2025-12-31",
            "P:PUR-6": "2026-11-14 is outside the periods of the connected account sets: NLTTEST 2024-01-01–2025-12-31"})
        self.assertEqual(asked, ["0105555000001"])


class PrintedTotalToolTests(unittest.TestCase):
    """CC 补票面合计（只这一栏，已提交的单也能改）：必须带依据；原生版本记录 + 依据记录；只改数，推送交回写入计划（A 线 B3，
    原来停在票面合计上的单当场重新写账、不过计划）；别家的单不改；写进账本的不能改；单据详情的 checks 说清为什么停下。"""

    def setUp(self):
        reset()
        from unittest import mock
        self.made = []
        STATE.db.set_value = lambda doctype, name, field, value=None, update_modified=True: \
            STATE.db.table(doctype)[name].update(field if isinstance(field, dict) else {field: value})
        self.addCleanup(lambda: delattr(STATE.db, "set_value"))
        for name, value in (("get_doc", lambda values: types.SimpleNamespace(
                insert=lambda ignore_permissions=False: self.made.append(values))),
                            ("as_json", lambda value: json.dumps(value, default=str)),
                            ("parse_json", json.loads)):
            patcher = mock.patch.object(frappe, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_cc_sets_the_printed_total_with_a_basis_and_the_document_waits_for_the_write_plan(self):
        row = doc(PURCHASE, "PUR-7", reason=E.PRINTED_TOTAL_MISSING, ocr_review='{"codes":["total_missing"],"total":null}',
                  printed_total=0)
        refused = call(tools.set_printed_total, document="P:PUR-7", value="1070.00")
        self.assertEqual(refused["status"], "not_saved")  # 没有依据不改
        self.assertEqual(row["printed_total"], 0)
        out = call(tools.set_printed_total, document="P:PUR-7", value="1,070.00", basis="page 2 of the original")
        self.assertEqual((out["status"], out["printed_total"], out["released"]), ("saved", 1070.0, False))
        self.assertEqual(CALLS, [])  # 只改数：不当场写账（B3），推送由写入计划来
        self.assertEqual((row["ledger_state"], row["ledger_reason"]), ("held", E.PRINTED_TOTAL_MISSING))
        self.assertIn("pearnly_write_plan", out["note"])
        version, = [made for made in self.made if made["doctype"] == "Version"]
        self.assertEqual(json.loads(version["data"]), {"changed": [["printed_total", 0.0, 1070.0]]})
        basis, = [made for made in self.made if made["doctype"] == "Comment"]
        self.assertEqual(json.loads(basis["content"])["basis"], "page 2 of the original")

    def test_detail_says_why_a_recognized_document_is_held(self):
        from unittest import mock
        row = doc(PURCHASE, "PUR-7", reason=E.PRINTED_TOTAL_MISSING, ocr_review='{"codes":["total_missing"],"total":null}',
                  printed_total=0, net_payable="1070.00", subtotal="1000.00", vat_amount="70.00", doc_no="IV-7",
                  lines=[Row(description="Soap", qty=1, unit_price=1000, line_total=1000)],
                  attachments=[Row(file="F1", kind="bill"), Row(file="F2", kind="bill", generated=1)])
        with mock.patch.object(frappe, "get_doc", lambda doctype, name: Row(STATE.db.table(doctype)[name]), create=True):
            out = call(tools.document_detail, document="P:PUR-7")
        self.assertEqual((out["document"], out["total"], out["printed_total"], out["pages"]), ("P:PUR-7", "1070.00", 0, 1))
        self.assertEqual(out["checks"], {"recognition": ["total_missing"], "printed_hold": E.PRINTED_TOTAL_MISSING,
                                         "push": E.PRINTED_TOTAL_MISSING})
        self.assertEqual(out["lines"][0]["amount"], 1000)
        self.assertTrue(out["url"].endswith("/PUR-7"))
        self.assertEqual(row["ledger_state"], "held")

    def test_another_companys_document_is_not_changed(self):
        doc(PURCHASE, "PUR-9", company="CO-2", reason=E.PRINTED_TOTAL_MISSING, ocr_review="{}", printed_total=0)
        out = call(tools.set_printed_total, document="P:PUR-9", value="1070", basis="x")
        self.assertEqual((out["status"], out["reason"], out["company"]), ("not_saved", "other_company", "CO-2"))
        self.assertEqual(STATE.db.table(PURCHASE)["PUR-9"]["printed_total"], 0)

    def test_a_written_document_is_not_changed(self):
        doc(PURCHASE, "PUR-8", state="written", ocr_review="{}", printed_total=100)
        out = call(tools.set_printed_total, document="P:PUR-8", value="1070", basis="x")
        self.assertEqual(out["status"], "not_saved")
        self.assertEqual(STATE.db.table(PURCHASE)["PUR-8"]["printed_total"], 100)


class PartyAccountToolTests(unittest.TestCase):
    """欠账 B52：用户在 CC 里说「这家供应商的单记到某科目」，CC 照单据页的写权限与公司闸取单，核科目、记下交
    `api.remember_party_account`（与单据上「选一次」同一张选择表）；要「允许」；别家的单、没有开单权的不记。"""

    def setUp(self):
        reset()
        patcher = mock.patch.object(frappe, "get_doc", lambda doctype, name: Row(STATE.db.table(doctype)[name]), create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cc_saves_the_account_through_the_document_service_after_allow(self):
        doc(PURCHASE, "PUR-7", reason=E.PRINTED_TOTAL_MISSING)
        by_name = {tool["name"]: tool for tool in tools.TOOLS}
        self.assertTrue(by_name["erp_party_account_save"]["approval"])
        self.assertEqual(call(tools.party_account_save, document="P:PUR-7")["status"], "not_saved")  # 没给科目
        out = call(tools.party_account_save, document="P:PUR-7", value="52-08-00-00")
        self.assertEqual((out["document"], out["status"], out["account"], out["account_name"]),
                         ("P:PUR-7", "saved", "52-08-00-00", "Freight"))
        self.assertNotIn("subject", out)
        self.assertEqual(CALLS, [("remember_party_account", PURCHASE, "PUR-7", "52-08-00-00")])

    def test_another_companys_or_a_read_only_users_document_is_not_remembered(self):
        doc(PURCHASE, "PUR-9", company="CO-2")
        out = call(tools.party_account_save, document="P:PUR-9", value="52-08-00-00")
        self.assertEqual((out["status"], out["reason"], out["company"]), ("not_saved", "other_company", "CO-2"))
        frappe.session.user = "viewer@x"  # 只有查看权
        doc(PURCHASE, "PUR-7")
        self.assertEqual(call(tools.party_account_save, document="P:PUR-7", value="52-08-00-00")["status"],
                         direct_push.FAILED)
        self.assertEqual(CALLS, [])


class ListTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_this_conversation_lists_the_documents_recognized_from_its_files_whatever_their_date(self):
        # 11-10 线上压测 S1（同 Cowork）：本对话附件识别出的单不按工作月份筛、还没推过的也列；别的对话、别人传的不列
        doc(PURCHASE, "PUR-1", state=None, day="2026-06-03")
        doc(SALES, "SAL-1", state=tools.contracts.WRITTEN, day="2026-07-09")
        doc(PURCHASE, "PUR-2", day="2026-10-20")
        intake = STATE.db.table("ERP LINE Intake")
        intake.update({
            "LI-1": {"name": "LI-1", "source": "cc", "session_id": "s1", "sent_by": "boss@x", "purchase": "PUR-1"},
            "LI-2": {"name": "LI-2", "source": "cc", "session_id": "s1", "sent_by": "boss@x", "sales_document": "SAL-1"},
            "LI-3": {"name": "LI-3", "source": "cc", "session_id": "s2", "sent_by": "boss@x", "purchase": "PUR-2"},
            "LI-4": {"name": "LI-4", "source": "line", "session_id": "s1", "sent_by": "boss@x", "purchase": "PUR-2"},
            # 11-20 线上代录 H3：识别好、还没提交成单据的（开始前卡片已列出），CC 只查已提交的单，说「还没识别成单据」
            "LI-5": {"name": "LI-5", "source": "cc", "session_id": "s1", "sent_by": "boss@x", "status": "ready",
                     "company": "CO-1"},
            "LI-6": {"name": "LI-6", "source": "cc", "session_id": "s2", "sent_by": "boss@x", "status": "ready",
                     "company": "CO-1"}})
        stub = types.ModuleType("pearnly_erp_portal.integrations.intake")
        stub.DOCTYPE, stub.SOURCE_CC, stub.READY = "ERP LINE Intake", "cc", "ready"
        stub.chat_records = lambda names: [{"record": name, "status": "ready", "direction": "purchase", "company": "CO-1",
                                            "doc_no": "TS6911-1120H3", "doc_date": "2026-11-20", "party": "Shop H",
                                            "totals": {"grand_total": "107.00"}} for name in names]
        patcher = mock.patch.dict(sys.modules, {"pearnly_erp_portal.integrations.intake": stub})
        patcher.start()
        self.addCleanup(patcher.stop)
        out = call(tools.list_documents, this_conversation=True)
        self.assertEqual(sorted(row["name"] for row in out["documents"]), ["P:PUR-1", "S:SAL-1"])
        self.assertTrue(out["scope"]["this_conversation"])
        self.assertEqual(out["recognized_not_submitted"], [{"doc_id": "I:LI-5", "kind": "purchase", "company": "CO-1",
                                                            "invoice_no": "TS6911-1120H3", "date": "2026-11-20",
                                                            "party": "Shop H", "total": "107.00"}])
        self.assertNotIn("recognized_not_submitted", call(tools.list_documents))  # 不问本对话的不列
        self.assertEqual([row["name"] for row in call(tools.list_documents, this_conversation=True,
                                                        push_state="success")["documents"]], ["S:SAL-1"])
        intake.clear()
        self.assertEqual(call(tools.list_documents, this_conversation=True)["documents"], [])
        listed = next(t for t in tools.TOOLS if t["name"] == "erp_list_documents")
        self.assertIn("this_conversation", listed["input_schema"]["properties"])

    def test_lists_this_conversation_s_company_paged_on_the_server(self):
        doc(PURCHASE, "PUR-1", state="written", ledger_docnum="RR6910-001", day="2026-10-21")
        doc(PURCHASE, "PUR-2", reason="choice_needed:account", day="2026-10-19")
        doc(SALES, "SD-1", state="queued", day="2026-10-22")
        doc(STOCK, "STE-1", state="held", reason=E.NO_BOOK, day="2026-10-20")
        doc(POS_DAY, "PDS-1", state="failed", reason="x", day="2026-10-18")
        doc(COUNT, "STC-1", state="written", ledger_docnum="TK6910-001", day="2026-10-17 09:00:00")
        doc(PURCHASE, "PUR-9", company="CO-2", day="2026-10-23")  # 别家：本对话挂的是 CO-1
        doc(PURCHASE, "PUR-0", state=None, day="2026-10-24")  # 不写 Express 的单
        first = call(tools.list_documents, limit=2)
        self.assertEqual([row["name"] for row in first["documents"]], ["S:SD-1", "P:PUR-1"])
        self.assertEqual((first["total"], first["next_offset"], first["scope"]), (6, 2, "CO-1"))
        self.assertEqual(first["documents"][1]["written"], {"docnum": "RR6910-001"})
        self.assertEqual(first["documents"][1]["last_push_status"], "success")
        listed = [query for query in STATE.queries if not isinstance((query["fields"] or [{}])[0], dict)]
        self.assertTrue(all(query["limit_page_length"] == 2 for query in listed))  # 每类只取到这一页为止
        counted = [query for query in STATE.queries if isinstance((query["fields"] or [""])[0], dict)]
        self.assertTrue(all(query["limit_page_length"] == 1 for query in counted))
        rest = call(tools.list_documents, offset=2, limit=50)
        self.assertEqual([row["name"] for row in rest["documents"]], ["M:STE-1", "P:PUR-2", "D:PDS-1", "C:STC-1"])
        self.assertIsNone(rest["next_offset"])
        self.assertEqual(rest["documents"][0]["company"], "CO-1")  # 出入库单按仓库的公司

    def test_pending_rows_waiting_for_express_carry_waiting_from_one_query(self):
        # 有人开着这本账套的 Express：排队中的单另带 waiting（现成那句、按用户语言），其余字段与结构不变
        doc(SALES, "SD-1", state="queued", ledger_job="J1", day="2026-10-22")
        doc(SALES, "SD-2", state="queued", ledger_job="J2", day="2026-10-21")
        doc(PURCHASE, "PUR-3", reason="[PERIOD_LOCKED] closed", ledger_job="J3", day="2026-10-20")
        doc(PURCHASE, "PUR-4", state="queued", day="2026-10-19")   # 还没交出去：没有写账件
        before = call(tools.list_documents)["documents"]
        self.assertTrue(all("waiting" not in row for row in before))
        WAITING.update({"J1", "J3"})
        WAIT_CALLS.clear()
        with translated():
            for fn in (tools.list_documents, tools.push_results):
                rows = {row["name"]: row for row in call(fn)["documents"]}
                self.assertEqual(rows["S:SD-1"]["waiting"], SENTENCE)
                self.assertEqual((rows["S:SD-1"]["last_push_status"], "reason" in rows["S:SD-1"]), ("pending", False))
                self.assertTrue(all("waiting" not in rows[name] for name in ("S:SD-2", "P:PUR-3", "P:PUR-4")))
                # 只多这一个字段
                self.assertEqual([{k: v for k, v in row.items() if k != "waiting"} for row in rows.values()], before)
        self.assertEqual(WAIT_CALLS, [["J1", "J2"], ["J1", "J2"]])   # 一页一次查完，只问排队中、有写账件的
        self.assertEqual(STATE.db.table(SALES)["SD-1"]["ledger_reason"], "")   # 单据上的原因栏不写
        # 这一页没有排队中的单：不问
        WAIT_CALLS.clear()
        call(tools.list_documents, kind="purchase", push_state="waiting")
        self.assertEqual(WAIT_CALLS, [])

    def test_filters_by_push_state_kind_and_search(self):
        doc(PURCHASE, "PUR-1", state="written", ledger_docnum="RR6910-001")
        doc(PURCHASE, "PUR-2", reason="choice_needed:account")
        doc(SALES, "SD-1", state="queued")
        self.assertEqual([r["name"] for r in call(tools.list_documents, push_state="waiting")["documents"]], ["P:PUR-2"])
        self.assertEqual([r["name"] for r in call(tools.push_results, push_state="success")["documents"]], ["P:PUR-1"])
        self.assertEqual([r["name"] for r in call(tools.list_documents, kind="sales")["documents"]], ["S:SD-1"])
        self.assertEqual([r["name"] for r in call(tools.list_documents, search="RR6910")["documents"]], ["P:PUR-1"])

    def test_people_see_only_their_companies_kinds_and_own_documents(self):
        doc(PURCHASE, "PUR-1", owner="boss@x")
        doc(PURCHASE, "PUR-2", owner="staff@x")
        doc(SALES, "SD-1", owner="staff@x")
        doc(PURCHASE, "PUR-9", company="CO-2", owner="staff@x")
        frappe.session.user = "staff@x"
        self.assertEqual([r["name"] for r in call(tools.list_documents)["documents"]], ["P:PUR-2"])  # 无销售读权
        self.assertEqual(call(tools.list_documents, company="CO-2")["status"], "failed")  # 看不到的公司
        frappe.session.user = "boss@x"
        self.assertEqual({r["name"] for r in call(tools.list_documents, all_companies=True)["documents"]},
                         {"P:PUR-1", "P:PUR-2", "S:SD-1", "P:PUR-9"})


class PushTests(unittest.TestCase):
    def setUp(self):
        reset()
        # 这里看推送本身：用户已同意写入计划（计划的门由共用层 write_plan 的测试锁住）
        patcher = mock.patch.object(direct_push, "permitted", lambda names: (list(names), []))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_each_state_goes_the_way_the_document_page_does(self):
        doc(PURCHASE, "PUR-1", state="written", ledger_docnum="RR6910-001")
        doc(PURCHASE, "PUR-2", state="queued", ledger_job="J2")
        JOBS["J2"] = {"job": "J2", "status": "leased", "request_key": "erp:purchase:PUR-2", "reason": ""}
        doc(PURCHASE, "PUR-3", reason="choice_needed:account", needed=NEEDED)
        doc(PURCHASE, "PUR-4", reason="[PERIOD_LOCKED] closed", ledger_job="J4")
        JOBS["J4"] = {"job": "J4", "status": "manual", "request_key": "erp:purchase:PUR-4", "reason": "[PERIOD_LOCKED]"}
        doc(SALES, "SD-5", reason=E.NO_BOOK)
        doc(PURCHASE, "PUR-6", reason="", ledger_job="J6")
        JOBS["J6"] = {"job": "J6", "status": "manual", "request_key": "erp:operator:b:PUR-6", "reason": ""}
        OPERATOR.add("PUR-6")
        doc(PURCHASE, "PUR-7", reason=LOST, ledger_job="J7")
        JOBS["J7"] = {"job": "J7", "status": "manual", "request_key": "erp:purchase:PUR-7", "reason": LOST}
        out = call(tools.push_documents, documents=["P:PUR-1", "P:PUR-2", "P:PUR-3", "P:PUR-4", "S:SD-5", "P:PUR-6",
                                                    "P:PUR-7", "P:NOPE", "R:COL261111-AB12@1"])
        by = {item["document"]: item for item in out["results"]}
        self.assertEqual(by["P:PUR-1"], {"document": "P:PUR-1", "status": "already_success", "docnum": "RR6910-001"})
        self.assertEqual(by["P:PUR-2"]["status"], "already_pending")
        self.assertEqual(by["P:PUR-3"]["status"], "needs_choice")
        self.assertEqual(by["P:PUR-4"]["status"], "queued")  # 单据上的「再写一次」
        self.assertEqual(by["S:SD-5"]["status"], "queued")  # 提交时排直写的同一个函数
        self.assertEqual((by["P:PUR-6"]["status"], by["P:PUR-6"]["reason_code"]), ("with_operator", E.OPERATOR_OWNED))
        self.assertEqual(by["P:PUR-7"]["status"], "unconfirmed")  # 结果不明：不再推
        self.assertEqual(by["P:NOPE"]["status"], "not_found")
        self.assertIn("erp_collection_guide", by["R:COL261111-AB12@1"]["note"])  # 收款只在收款卡上录
        self.assertEqual(CALLS, [("retry", PURCHASE, "PUR-4"), ("send", SALES, "SD-5")])
        self.assertIn("erp_push_results", out["note"])

    def test_a_queued_document_waiting_for_express_says_so(self):
        doc(PURCHASE, "PUR-2", state="queued", ledger_job="J2")   # 早已交出去的
        JOBS["J2"] = {"job": "J2", "status": "queued", "request_key": "erp:purchase:PUR-2", "reason": "ACCOUNT_BUSY_LOCKED"}
        doc(SALES, "SD-5", reason=E.NO_BOOK)                       # 这次交出去、到点仍在排队
        doc(SALES, "SD-6", reason=E.NO_BOOK)
        WAITING.update({"J2", "J-SD-5"})
        with translated():
            out = call(tools.push_documents, documents=["P:PUR-2", "S:SD-5", "S:SD-6"])
        self.assertEqual(out["results"], [
            {"document": "P:PUR-2", "status": "already_pending", "waiting": SENTENCE},
            {"document": "S:SD-5", "status": "queued", "waiting": SENTENCE},
            {"document": "S:SD-6", "status": "queued"}])
        self.assertIn("erp_push_results", out["note"])
        # 电脑关了 Express、写进去了：不再带
        WAITING.clear()
        STATE.db.table(SALES)["SD-5"].update(ledger_state="written", ledger_docnum="HS6910-001")
        with translated():
            self.assertEqual(tools._outcome(tools.KINDS["sales"], "SD-5"),
                             {"status": "entered", "docnum": "HS6910-001"})

    def test_a_queued_job_waiting_for_a_computer_that_is_off_names_that_computer(self):
        # 11-25 两台电脑实测：钉在关着的原电脑上时页面只写「录入中」。CC 对话里的推送结论照单据页同一句说出在等哪台
        doc(SALES, "SD-7", state="queued", ledger_job="J7")
        STATE.db.table(SALES)["SD-7"].update(ledger_reason="express.waiting_computer:DESKTOP-TEST02")
        with translated():
            out = tools._outcome(tools.KINDS["sales"], "SD-7")
        self.assertEqual(out["status"], "queued")
        self.assertEqual((out["cc_desktop_online"], out["waiting_computer"]), (False, "DESKTOP-TEST02"))
        self.assertTrue(out["note"].endswith(" · DESKTOP-TEST02"))
        STATE.db.table(SALES)["SD-7"].update(ledger_reason="express.waiting_computer")   # 名称取不到：只说等待电脑
        with translated():
            bare = tools._outcome(tools.KINDS["sales"], "SD-7")
        self.assertEqual((bare["cc_desktop_online"], bare["waiting_computer"]), (False, ""))
        self.assertNotIn(" · ", bare["note"])

    def test_the_model_is_told_what_waiting_means_in_one_sentence(self):
        by_name = {tool["name"]: tool for tool in tools.TOOLS}
        for name in ("erp_push_documents", "erp_push_results", "erp_list_documents"):
            self.assertEqual(by_name[name]["description"].count(tools.EXPRESS_WAIT_NOTE), 1, name)
        # 与 Cowork 同一句（Cowork 的工具说明由它的 golden 锁住）
        cowork = (COWORK / "assistant/tools.py").read_text(encoding="utf-8")
        start = cowork.index("EXPRESS_WAIT_NOTE = (")
        tree = ast.parse(cowork[start:cowork.index("\n\n", start)])
        self.assertEqual(ast.literal_eval(tree.body[0].value), tools.EXPRESS_WAIT_NOTE)

    def test_waits_for_the_express_number(self):
        doc(SALES, "SD-5", reason=E.NO_BOOK)
        frappe.local.pearnly_assistant["may_wait"] = True
        reads = []

        def computer_writes():
            reads.append(1)
            if len(reads) == 2:
                STATE.db.table(SALES)["SD-5"].update(ledger_state="written", ledger_docnum="HS6910-001")

        STATE.db.on_rollback = computer_writes
        tools.time.sleep, saved = (lambda seconds: None), tools.time.sleep
        try:
            out = call(tools.push_documents, documents=["S:SD-5"])
        finally:
            tools.time.sleep = saved
        self.assertEqual(out["results"], [{"document": "S:SD-5", "status": "entered", "docnum": "HS6910-001"}])
        self.assertNotIn("note", out)

    def test_an_offline_computer_is_not_waited_for(self):
        doc(SALES, "SD-5", reason=E.NO_BOOK)
        frappe.local.pearnly_assistant["may_wait"] = True
        STATE.db.on_rollback = lambda: STATE.db.table(SALES)["SD-5"].update(ledger_reason=E.OFFLINE)
        out = call(tools.push_documents, documents=["S:SD-5"])
        self.assertEqual(out["results"], [{"document": "S:SD-5", "status": "queued", "cc_desktop_online": False}])
        self.assertEqual(STATE.db.rollbacks, 1)

    def test_rights_company_and_owner_are_checked_on_the_server(self):
        doc(PURCHASE, "PUR-2", reason=E.NO_BOOK, owner="boss@x")
        doc(PURCHASE, "PUR-3", reason=E.NO_BOOK, owner="staff@x")
        doc(PURCHASE, "PUR-9", company="CO-2", reason=E.NO_BOOK)
        frappe.session.user = "viewer@x"
        self.assertEqual(call(tools.push_documents, documents=["P:PUR-2"])["results"][0],
                         {"document": "P:PUR-2", "status": "failed", "reason_code": "not_permitted"})
        frappe.session.user = "staff@x"
        self.assertEqual(call(tools.push_documents, documents=["P:PUR-2", "P:PUR-9"])["results"],
                         [{"document": "P:PUR-2", "status": "not_found"}, {"document": "P:PUR-9", "status": "not_found"}])
        self.assertEqual(CALLS, [])
        self.assertEqual(call(tools.push_documents, documents=["P:PUR-3"])["results"][0]["status"], "queued")
        frappe.session.user = "boss@x"
        other = call(tools.push_documents, documents=["P:PUR-9"])["results"][0]
        self.assertEqual((other["status"], other["reason_code"], other["company"]), ("failed", "other_company", "CO-2"))
        # CC 只写本对话公司的单（用户 11-10）：点名别家也不写（旧口径「显式给 company 就放行」作废）
        sent = list(CALLS)
        named = call(tools.push_documents, documents=["P:PUR-9"], company="CO-2")["results"][0]
        self.assertEqual((named["status"], named["reason_code"]), ("failed", "other_company"))
        self.assertEqual(CALLS, sent)

    def test_a_conversation_without_a_company_writes_nothing(self):
        doc(PURCHASE, "PUR-2", reason=E.NO_BOOK)
        FILED["company"] = ""
        item = call(tools.push_documents, documents=["P:PUR-2"], company="CO-1")["results"][0]
        self.assertEqual((item["status"], item["reason_code"]), ("failed", "no_company"))
        self.assertEqual(CALLS, [])
        # 说明里告诉 CC：问用户录到哪家、把对话挂到用户选的那家再推（主会话 11-10，同 Cowork）
        push = next(t for t in tools.TOOLS if t["name"] == "erp_push_documents")
        self.assertIn("pearnly_file_conversation", push["description"])

    def test_a_disconnected_company_is_not_written(self):
        doc(PURCHASE, "PUR-2", reason=E.NO_BOOK)
        ENABLED["CO-1"] = False
        item = call(tools.push_documents, documents=["P:PUR-2"])["results"][0]
        self.assertEqual((item["status"], item["reason_code"]), ("failed", E.DISCONNECTED))
        self.assertEqual(CALLS, [])


class LedgerGateTests(unittest.TestCase):
    """本对话账套（A 线 11-12）：ERP 写哪本由服务端定（写账准备只写本对话账套，见 test_cc_conversation_ledger_erp）；本对话账套
    未定时直写不推，回 ledger_needed（形状同 Cowork `write_scope.needed`：reason、note、可选的几本），问一次、设好再建计划。"""

    def setUp(self):
        reset()
        patcher = mock.patch.object(direct_push, "permitted", lambda names: (list(names), []))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pin = None
        conversation = importlib.import_module("pearnly_erp_portal.express.conversation")
        patcher = mock.patch.object(conversation, "pinned", lambda row, task=None: self.pin if row["name"] == "CO-1" else None)
        patcher.start()
        self.addCleanup(patcher.stop)
        doc(SALES, "SD-5", reason=E.NO_BOOK)

    def test_an_undecided_conversation_account_set_writes_nothing_and_offers_the_books(self):
        options = [{"id": "BK8", "label": "T.8 · S:\\68", "covers": True}, {"id": "BK9", "label": "T.9", "covers": True}]
        self.pin = {"status": "needed", "key": "", "label": "", "options": options}
        item = call(tools.push_documents, documents=["S:SD-5"])["results"][0]
        self.assertEqual((item["status"], item["reason"], item["reason_code"]), ("failed", "ledger_needed", "ledger_needed"))
        # 只给名称，不给账套键（内部编号，CC 会复述给人）；CC 按名称设本对话账套（A 线 11-15）
        self.assertEqual([(book["label"], book["covers"]) for book in item["allowed_account_sets"]],
                         [("T.8 · S:\\68", True), ("T.9", True)])
        self.assertNotIn("BK8", json.dumps(item))
        self.assertIn("pearnly_conversation_ledger", item["note"])
        self.assertEqual(CALLS, [])

    def test_a_decided_one_or_none_goes_the_usual_way(self):
        for pin in ({"status": "set", "key": "BK8", "label": "T.8", "options": []}, None):
            CALLS.clear()
            doc(SALES, "SD-5", reason=E.NO_BOOK)
            self.pin = pin
            self.assertEqual(call(tools.push_documents, documents=["S:SD-5"])["results"][0]["status"], "queued")
            self.assertEqual(CALLS, [("send", SALES, "SD-5")])

    def test_a_question_left_by_the_web_is_prepared_again_in_the_conversation(self):
        """网页按单据日期准备时停在要人选（同期两本选哪本，``ledger_needed`` 记着那一问）的单：对话里本对话账套已定，计划按它判过
        能写，推的时候按本对话账套重新准备（`push.send`），不拿网页那一问回 needs_choice（11-12 流程 erp.cc.conversation-ledger
        撞出：计划 ready、推回 needs_choice）。真还要人选，`push.send` 会照实再停下。没在对话里、本对话账套未定的照旧。"""
        asked = [{"kind": "account_set", "subject": "year:2025-01-01",
                  "options": [{"code": "BK8", "name": "T.8"}, {"code": "BK9", "name": "T.9"}]}]
        doc(PURCHASE, "PUR-3", reason="choice_needed:account_set", needed=asked)
        self.pin = {"status": "set", "key": "BK8", "label": "T.8", "options": []}
        self.assertEqual(call(tools.push_documents, documents=["P:PUR-3"])["results"][0]["status"], "queued")
        self.assertEqual(CALLS, [("send", PURCHASE, "PUR-3")])
        CALLS.clear()
        doc(PURCHASE, "PUR-3", reason="choice_needed:account_set", needed=asked)
        self.pin = None
        self.assertEqual(call(tools.push_documents, documents=["P:PUR-3"])["results"][0]["status"], "needs_choice")
        self.assertEqual(CALLS, [])

    ASKED = [{"kind": "account_set", "subject": "year:2025-01-01",
              "options": [{"code": "BK8", "name": "T.8"}, {"code": "BK9", "name": "T.9"}]}]

    def _company_patch(self):
        companies = types.ModuleType("pearnly_erp_portal.masters.company")
        companies.allowed_companies = lambda user: list(ALLOWED.get(user, []))
        patcher = mock.patch.dict(sys.modules, {"pearnly_erp_portal.masters.company": companies})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _seen_by_the_model(self, document):
        """列单、单据详情、待处理三个工具交给模型的这张单（测试机1 11-12 ERP 代录：模型照列单的原因码说「choice_needed:account_set」、
        照网页那一问建议改投 TEST2）。"""
        self._company_patch()
        listed = next(row for row in call(tools.list_documents)["documents"] if row["name"] == document)
        with mock.patch.object(frappe, "get_doc", lambda doctype, name: Row(STATE.db.table(doctype)[name]), create=True):
            detail = call(tools.document_detail, document=document)
        pending_rows = [row for row in call(tools.pending_items)["items"] if row["document"] == document]
        return listed, detail, pending_rows

    def _the_web_left_a_question(self, session):
        """网页按单据日期准备时停在「同期两本选哪本」（没选出账套、没交进写账件）；此刻在 ``session`` 这段对话里、本对话账套是 T.8。"""
        frappe.local.pearnly_assistant = {"session_id": session, "may_wait": False}
        doc(PURCHASE, "PUR-3", reason="choice_needed:account_set", needed=self.ASKED, ledger_book=None)
        self.pin = {"status": "set", "key": "BK8", "label": "T.8", "options": []}
        listed, detail, pending_rows = self._seen_by_the_model("P:PUR-3")
        # 那一问不再给模型看：不当作停下要人选、不带原因码、不列网页那一问的选项（写的时候按 T.8 重新准备）
        self.assertNotIn("reason_code", listed)
        self.assertNotIn("reason", listed)
        self.assertEqual((detail["push_state"], detail["push_reason"]), ("", ""))
        self.assertNotIn("choice_needed", json.dumps(detail))
        self.assertEqual(pending_rows, [])
        # 不在对话里（网页、侧栏待处理背后）：照旧是要人选的那一问，原因给的是话、不是码
        frappe.local.pearnly_assistant = {"session_id": "", "may_wait": False}
        self.pin = None
        listed, detail, pending_rows = self._seen_by_the_model("P:PUR-3")
        self.assertEqual((listed["reason"], listed["reason_code"]), ("express.reason.choiceNeeded", "choice_needed:account_set"))
        self.assertEqual(detail["push_reason"], "express.reason.choiceNeeded")
        self.assertEqual([row["reason"] for row in pending_rows], ["express.reason.choiceNeeded"])

    def test_a_question_left_by_the_web_is_not_shown_to_the_model_in_a_direct_conversation(self):
        self._the_web_left_a_question("sesn_1")

    def test_a_question_left_by_the_web_is_not_shown_to_the_model_in_an_operator_conversation(self):
        # 代录会话（op_<任务号>）：同一套工具，同一口径
        self._the_web_left_a_question("op_task1")

    def test_operator_task_reasons_reach_the_model_as_words(self):
        # express_operator_status 交给模型的原因（共用层 products.reason_text）：ERP 声明的就是列单同一句话
        hooks = (ERP / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('"reason_text": "pearnly_erp_portal.express.cc_tools.reason_words",', hooks)
        self.assertEqual(tools.reason_words("choice_needed:account_set"), "express.reason.choiceNeeded")
        self.assertEqual(tools.reason_words(E.NO_WRITER), pending.reason_label(E.HELD, E.NO_WRITER))
        self.assertEqual(tools.reason_words(""), "")

    def test_a_real_question_prepared_in_the_conversation_book_still_asks(self):
        """在本对话账套里准备过、真要人选（如费用科目）的单（``ledger_book`` 就是本对话账套）：照实回 needs_choice、不重推；原因给话。
        网页按另一本准备的那一问（``ledger_book`` 不是这本）才重新准备。"""
        doc(PURCHASE, "PUR-4", reason="choice_needed:account", needed=NEEDED, ledger_book="BK8")
        self.pin = {"status": "set", "key": "BK8", "label": "T.8", "options": []}
        item = call(tools.push_documents, documents=["P:PUR-4"])["results"][0]
        self.assertEqual((item["status"], item["reason"]), ("needs_choice", "express.reason.choiceNeeded"))
        self.assertEqual(CALLS, [])
        doc(PURCHASE, "PUR-4", reason="choice_needed:account", needed=NEEDED, ledger_book="BK9")
        self.assertEqual(call(tools.push_documents, documents=["P:PUR-4"])["results"][0]["status"], "queued")
        self.assertEqual(CALLS, [("send", PURCHASE, "PUR-4")])

    def test_the_tool_says_every_document_goes_into_the_conversation_account_set(self):
        push = next(t for t in tools.TOOLS if t["name"] == "erp_push_documents")
        self.assertIn("this conversation's account set, whatever its date", push["description"])
        self.assertNotIn("resume true", push["description"])  # B1：resume 在对话里不推，答完重新建计划
        self.assertNotIn("has not reported this company's account set", push["description"])  # 没有账套不算等电脑


class ResolveTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.permit = {"allowed": True}
        patcher = mock.patch.object(direct_push, "permitted", lambda names: (list(names), []) if self.permit["allowed"]
                                    else ([], [{"document": name, "status": "not_pushed", "reason": "not_started",
                                                "note": "plan"} for name in names]))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_writing_after_checking_needs_the_write_plan(self):
        """B4：「核了没有就写」也是写入：不在用户同意了的计划里不交回电脑。"""
        doc(PURCHASE, "PUR-7", reason=LOST, ledger_job="J7")
        JOBS["J7"] = {"job": "J7", "status": "manual", "request_key": "erp:purchase:PUR-7", "reason": LOST}
        self.permit["allowed"] = False
        out = call(tools.resolve_push_result, document="P:PUR-7", entered=False)
        self.assertEqual((out["status"], out["reason"]), ("not_pushed", "not_started"))
        self.assertEqual(CALLS, [])

    def test_only_an_unknown_result_is_sent_back_to_the_computer_that_wrote_it(self):
        doc(PURCHASE, "PUR-4", reason="[PERIOD_LOCKED] closed", ledger_job="J4")
        JOBS["J4"] = {"job": "J4", "status": "manual", "request_key": "erp:purchase:PUR-4", "reason": "[PERIOD_LOCKED]"}
        self.assertEqual(call(tools.resolve_push_result, document="P:PUR-4", entered=False),
                         direct_push.not_saved("P:PUR-4", direct_push.NO_PUSH_TO_CONFIRM))
        self.assertEqual(CALLS, [])
        doc(PURCHASE, "PUR-7", reason=LOST, ledger_job="J7")
        JOBS["J7"] = {"job": "J7", "status": "manual", "request_key": "erp:purchase:PUR-7", "reason": LOST}
        out = call(tools.resolve_push_result, document="P:PUR-7", entered=True, docnum="RR6910-007")
        self.assertEqual(out, {"document": "P:PUR-7", "status": "saved", "push": {"status": "queued"}})
        self.assertEqual(CALLS, [("retry", PURCHASE, "PUR-7")])
        self.assertEqual(call(tools.resolve_push_result, document="P:NOPE", entered=True),
                         direct_push.not_found("P:NOPE"))

    def test_an_mrerp_unknown_is_recorded_as_the_user_found_it(self):
        """11-28 用户批准：账本是 MR.ERP 的结果不明，MR.ERP 不能按号查重——照用户在 MR.ERP 看到的记下（同单据弹窗「已在 MR.ERP 核对」），
        只在同意了的写入计划里；entered 不是布尔不收。"""
        with mock.patch.object(tools.mrerp_push, "handles", lambda company: company == "CO-1"):
            doc(PURCHASE, "PUR-8", reason=tools.mrerp_contracts.UNKNOWN + ": ERR_TECHNICAL")
            out = call(tools.resolve_push_result, document="P:PUR-8", entered="yes")
            self.assertEqual(out["status"], "not_saved")
            self.assertEqual(CALLS, [])
            out = call(tools.resolve_push_result, document="P:PUR-8", entered=True, docnum="PG691128-1")
            self.assertEqual(out["status"], "saved")
            self.assertEqual(CALLS, [("resolve_unknown", PURCHASE, "PUR-8", "1", "PG691128-1")])
            self.permit["allowed"] = False
            out = call(tools.resolve_push_result, document="P:PUR-8", entered=False)
            self.assertEqual((out["status"], out["reason"]), ("not_pushed", "not_started"))
            self.assertEqual(len(CALLS), 1)


class PushAgainTests(unittest.TestCase):
    """MR.ERP 单停在会随再推重试的原因上（登录没授权、账号被占……原因可能早已不在）：工具输出带 ``push_again``，说明里告诉 CC 照常建计划
    推送、不按旧原因拒（11-16 testerp T11：登录已补回，CC 照存着的「未授权登录」先拒、再多问一次，第三轮才推）。"""

    def setUp(self):
        reset()
        patcher = mock.patch.object(tools.mrerp_push, "handles", lambda company: company == "CO-M")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_retryable_stops_are_marked_and_the_others_are_not(self):
        held = tools.contracts.HELD
        mrerp = tools.mrerp_contracts
        for reason in (mrerp.NO_LOGIN, mrerp.NOT_CONFIGURED, "ERR_MRERP_ACCOUNT_IN_USE: signed in elsewhere",
                       "express.account_set_missing"):
            self.assertTrue(tools.push_again("CO-M", held, reason), reason)
        for reason in (mrerp.UNKNOWN, mrerp.UNKNOWN + ": readback", mrerp.CHOICE, tools.contracts.VOIDED_AFTER_WRITE, ""):
            self.assertFalse(tools.push_again("CO-M", held, reason), reason)
        # 不是停住、不是 MR.ERP 账本的公司：不带
        self.assertFalse(tools.push_again("CO-M", tools.contracts.QUEUED, mrerp.NO_LOGIN))
        self.assertFalse(tools.push_again("CO-1", held, mrerp.NO_LOGIN))

    def test_the_list_note_tells_the_model_to_push_instead_of_refusing(self):
        self.assertIn("push_again", tools.ROW_NOTE)
        self.assertIn("without asking again", tools.ROW_NOTE)


class TimeTests(unittest.TestCase):
    def test_push_times_are_local(self):
        reset()
        doc(PURCHASE, "PUR-1", state="queued", ledger_job="J1", ledger_sent_at=datetime.datetime(2026, 10, 20, 15, 40, 9))
        row = call(tools.list_documents)["documents"][0]
        self.assertEqual(row["last_pushed_at"], "2026-10-20 15:40")


if __name__ == "__main__":
    unittest.main()
