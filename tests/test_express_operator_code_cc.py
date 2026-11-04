"""Code 版 CC 的后端（评估 docs/work/2026-11-04-cc-code-style.md 第 4、5、6 节，主会话 11-04 审定）：

- B1 任务上传记录（``express_operator.uploads.record`` / ``api.intake(task=…)``）：先核对本人、同公司、请求键、没记满再交识别；
  同一请求键只记一行；产品不收上传不记；
- B2 ``task_uploads``：每份上传的状态——排队中、识别中 n/m、已识别（识别出的单在不在任务月份、在不在资料里）、失败（带码）、
  读不到；只给本人；``batches`` 只现读在途的几份；Cowork 的识别记录怎么折成这几种状态（``operator_source.upload_state``）；
- B3 待处理按「公司 · 月份」（``assistant.contracts.pending_months`` + ``sessions.pending_months``）：本人这家这个月最近的对话；
- B4 预检写进哪份账套：产品给原料、共用层合成 ``targets`` / ``target``；ERP 按准备选出的账套、Cowork 按推送准备冻结的账套。
B5（识别完的实时事件）沿用共用识别队列已有的 ``pearnly_ocr_work``，这里核对它确实在有结论时推给提交人。

    python3 tests/test_express_operator_code_cc.py
"""
import ast
import datetime
import importlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "apps/pearnly_core/pearnly_core"


def _load(name, path, modules=None):
    with mock.patch.dict(sys.modules, modules or {}):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


class Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Throw(Exception):
    pass


class PermissionError_(Throw):
    pass


class DoesNotExistError_(Throw):
    pass


class ValidationError_(Throw):
    pass


def fake_frappe(state):
    frappe = types.ModuleType("frappe")
    frappe._dict = Row
    frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError = (
        PermissionError_, DoesNotExistError_, ValidationError_)
    frappe.session = types.SimpleNamespace(user="a@example.com")
    frappe.conf = {"pearnly_product": "cowork"}
    frappe._ = lambda text: text
    frappe.whitelist = lambda **kwargs: (lambda fn: fn)
    frappe.request = None
    frappe.clear_messages = lambda: None

    def throw(message, exc=None):
        raise (exc or ValidationError_)(message)

    frappe.throw = throw

    def match(row, filters):
        def one(value, wanted):
            if isinstance(wanted, list) and wanted and wanted[0] == "in":
                return value in wanted[1]
            if isinstance(wanted, list) and wanted == ["is", "not set"]:
                return not value
            return value == wanted
        return all(one(row.get(k), v) for k, v in filters.items())

    class Doc(Row):
        def __init__(self, data):
            super().__init__(data)
            self["flags"] = types.SimpleNamespace(ignore_permissions=False)
            for key in ("documents", "uploads"):
                self[key] = [Row(item) for item in data.get(key) or []]

        def append(self, key, value):
            row = Row(value)
            self[key].append(row)
            return row

        def set(self, key, value):
            self[key] = [Row(item) for item in value] if key in ("documents", "uploads") else value

        def insert(self):
            state["seq"] += 1
            self.update(name=f"t{state['seq']:03d}", owner=frappe.session.user, starred=0, creation=state["now"],
                        modified=state["now"])
            state["tasks"].append(self)
            return self

        def save(self):
            assert self.flags.ignore_permissions
            state["saves"] += 1

    def get_doc(arg, name=None):
        if isinstance(arg, dict):
            return Doc(arg)
        return next(row for row in state["tasks"] if row["name"] == name)

    def get_value(doctype, filters, field, for_update=False, as_dict=False):
        found = [row for row in state["tasks"] if match(row, filters)]
        return found[0][field] if found else None

    def get_all(doctype, filters=None, fields=None, order_by="", limit_page_length=None, limit_start=0, **kwargs):
        state["queries"].append((doctype, dict(filters or {}), order_by, limit_page_length))
        rows = [row for row in state["tables"].get(doctype, []) if match(row, filters or {})]
        if "modified desc" in order_by:
            rows.sort(key=lambda row: (row.get("modified"), row.get("name")), reverse=True)
        return [Row({f: row.get(f) for f in fields}) for row in rows][:limit_page_length or None]

    frappe.get_doc, frappe.get_all = get_doc, get_all
    frappe.publish_realtime = lambda event, message, user=None, after_commit=False: state["pushed"].append(
        (event, message, user, after_commit))
    frappe.db = types.SimpleNamespace(get_value=get_value, get_all=get_all)
    frappe.parse_json = lambda text: __import__("json").loads(text) if isinstance(text, str) else text
    frappe.as_json = lambda value: __import__("json").dumps(value, default=str)
    frappe.get_hooks = lambda name: state["hooks"].get(name)
    frappe.get_attr = lambda path: state["attrs"][path]
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = lambda: state["now"]
    frappe.utils = utils
    return frappe, utils


STATE = {"tasks": [], "seq": 0, "saves": 0, "pushed": [], "queries": [], "tables": {}, "hooks": {}, "attrs": {},
         "now": datetime.datetime(2026, 11, 4, 16, 0)}
frappe, frappe_utils = fake_frappe(STATE)
sys.modules["frappe"], sys.modules["frappe.utils"] = frappe, frappe_utils
for path in (ROOT / "apps/pearnly_core",):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
for name in [key for key in sys.modules if key == "pearnly_core" or key.startswith("pearnly_core.")]:
    sys.modules.pop(name)  # 别的测试装过替身也不影响：这里用真的共用层（frappe 是上面的替身）
shapes = importlib.import_module("pearnly_core.express_operator.shapes")
products = importlib.import_module("pearnly_core.express_operator.products")
tasks = importlib.import_module("pearnly_core.express_operator.tasks")
uploads = importlib.import_module("pearnly_core.express_operator.uploads")
api = importlib.import_module("pearnly_core.express_operator.api")
assistant_contracts = importlib.import_module("pearnly_core.assistant.contracts")


class Product:
    """产品钩子的替身（同 Cowork 声明的那几项）。"""

    def __init__(self):
        self.allowed = {"CO-1", "CO-2"}
        self.intakes, self.asked, self.status, self.accept = [], [], {}, True
        self.precheck_rows = []

    def install(self, *, intake=True):
        hooks = {"allowed": "p.allowed", "sources": "p.sources", "precheck": "p.precheck", "uploads": "p.uploads"}
        if intake:
            hooks["intake"] = "p.intake"
        STATE["hooks"] = {"pearnly_express_operator": hooks}
        STATE["attrs"] = {"p.allowed": lambda user, company: company in self.allowed, "p.sources": lambda *a: {},
                          "p.intake": self.intake, "p.uploads": self.uploads,
                          "p.precheck": lambda company, ids: {"documents": [dict(row) for row in self.precheck_rows]}}

    def intake(self, company, filename, content, request_key):
        self.intakes.append((company, filename, len(content), request_key))
        if not self.accept:
            return {"accepted": False, "code": "no_target"}
        return {"accepted": True, "source": f"SRC-{request_key}", "name": f"B-{request_key}", "page_count": 12,
                "state": "queued"}

    def uploads(self, company, period, batches):
        self.asked.append((company, period, list(batches)))
        return {batch: self.status[batch] for batch in batches if batch in self.status}


class File:
    def __init__(self, name, content):
        self.filename, self.stream = name, types.SimpleNamespace(read=lambda size: content)


def upload(company, key, task="", name="bill.pdf", content=b"%PDF-12pages"):
    frappe.request = types.SimpleNamespace(files={"file": File(name, content)})
    try:
        return api.intake(company, key, task)
    finally:
        frappe.request = None


class Base(unittest.TestCase):
    def setUp(self):
        STATE.update(tasks=[], seq=0, saves=0, pushed=[], queries=[], tables={})
        frappe.session.user = "a@example.com"
        frappe.conf["pearnly_product"] = "cowork"
        self.product = Product()
        self.product.install()


class UploadRecordTests(Base):
    """B1：上传一份就在任务上记一行；先挡下的不交识别。"""

    def test_upload_in_a_task_records_one_row_and_answers_with_it(self):
        task = tasks.create("CO-1", "2026-10")["name"]
        out = upload("CO-1", "k1", task)
        self.assertEqual(self.product.intakes, [("CO-1", "bill.pdf", 12, "k1")])
        self.assertEqual(out["upload"], {"request_key": "k1", "source": "SRC-k1", "filename": "bill.pdf", "size": 12,
                                         "batch": "B-k1", "uploaded_at": "2026-11-04 16:00:00", "state": "queued",
                                         "pages_done": 0, "page_count": 12, "code": "", "documents": []})
        doc = frappe.get_doc("Pearnly Express Operator Task", task)
        self.assertEqual([(row.request_key, row.batch, row.size) for row in doc.uploads], [("k1", "B-k1", 12)])
        self.assertEqual(STATE["pushed"][-1][0], tasks.EVENT)  # 别的设备上的任务跟着更新
        # 同一请求键再交（页面重试同一份）：产品照常收（照常识别、照常扣费由产品定），任务上仍是一行
        upload("CO-1", "k1", task)
        self.assertEqual(len(doc.uploads), 1)
        # 同一份文件换个请求键再传：照常上传、照常识别，多一张自己的卡片，不查重不拦
        upload("CO-1", "k2", task)
        self.assertEqual([row.request_key for row in doc.uploads], ["k1", "k2"])
        self.assertEqual(len(self.product.intakes), 3)

    def test_without_a_task_nothing_is_recorded_as_before(self):
        out = upload("CO-1", "k1")
        self.assertNotIn("upload", out)
        self.assertEqual(STATE["tasks"], [])

    def test_wrong_task_company_or_key_is_refused_before_recognition(self):
        task = tasks.create("CO-1", "2026-10")["name"]
        with self.assertRaises(ValidationError_):
            upload("CO-2", "k1", task)  # 别家公司的任务
        with self.assertRaises(ValidationError_):
            upload("CO-1", "", task)  # 没有请求键
        with self.assertRaises(ValidationError_):
            upload("CO-1", "k" * 129, task)
        with self.assertRaises(DoesNotExistError_):
            upload("CO-1", "k1", "t999")  # 没有这个任务
        self.assertEqual(self.product.intakes, [])  # 挡下的不交识别、不扣费

    def test_only_the_creator_and_only_with_the_product_permission(self):
        task = tasks.create("CO-1", "2026-10")["name"]
        frappe.session.user = "b@example.com"
        with self.assertRaises(DoesNotExistError_):
            upload("CO-1", "k1", task)  # 别人的任务一律「找不到」
        frappe.session.user = "a@example.com"
        self.product.allowed = {"CO-2"}
        with self.assertRaises(PermissionError_):
            upload("CO-1", "k1", task)  # 这家公司现在不能代录
        frappe.conf["pearnly_product"] = "erp"
        self.product.allowed = {"CO-1"}
        with self.assertRaises(DoesNotExistError_):
            upload("CO-1", "k1", task)  # 别的产品站点看不到
        self.assertEqual(self.product.intakes, [])

    def test_not_accepted_or_unsupported_is_not_recorded(self):
        task = tasks.create("CO-1", "2026-10")["name"]
        self.product.accept = False
        self.assertEqual(upload("CO-1", "k1", task), {"accepted": False, "code": "no_target"})
        self.product.install(intake=False)  # ERP：没声明 intake
        self.assertEqual(upload("CO-1", "k2", task), {"accepted": False, "code": "unsupported"})
        self.assertEqual(frappe.get_doc("Pearnly Express Operator Task", task).uploads, [])

    def test_a_full_task_takes_no_new_upload_but_a_retry_of_a_recorded_one(self):
        task = tasks.create("CO-1", "2026-10")["name"]
        doc = frappe.get_doc("Pearnly Express Operator Task", task)
        doc["uploads"] = [Row(request_key=f"k{i}", batch=f"B-k{i}") for i in range(shapes.UPLOADS_MAX)]
        with self.assertRaises(ValidationError_):
            upload("CO-1", "new", task)
        upload("CO-1", "k3", task)  # 记过的那一份重试照收
        self.assertEqual(len(doc.uploads), shapes.UPLOADS_MAX)

    def test_deleting_the_task_takes_its_uploads_with_it(self):
        dt = (CORE / "pearnly_core/doctype/pearnly_express_operator_task/pearnly_express_operator_task.json").read_text()
        child = (CORE / "pearnly_core/doctype/pearnly_express_operator_task_upload/"
                 "pearnly_express_operator_task_upload.json").read_text()
        self.assertIn('"options": "Pearnly Express Operator Task Upload"', dt)  # 子表：删任务连子表一起删（原生）
        self.assertIn('"istable": 1', child)
        for field in ("request_key", "source", "filename", "size", "batch", "uploaded_at"):
            self.assertIn(f'"fieldname": "{field}"', child)


class UploadStatusTests(Base):
    """B2：每份上传此刻的状态，五种都能显示（上传中在页面，其余四种来自这里）。"""

    def setUp(self):
        super().setUp()
        self.task = tasks.create("CO-1", "2026-10")["name"]
        for key in ("q", "r", "d", "f", "x"):
            upload("CO-1", key, self.task)

    def test_each_state_comes_back_in_upload_order(self):
        self.product.status = {
            "B-q": {"state": "queued", "page_count": 12, "code": "insufficient_balance"},
            "B-r": {"state": "reading", "pages_done": 3, "page_count": 12},
            "B-d": {"state": "done", "pages_done": 0, "page_count": 12, "documents": [
                {"doc_id": "h1", "ref": "IV-1", "date": "2026-10-03", "party": "Makro", "waiting": True},
                {"doc_id": "h2", "ref": "IV-2", "date": "2026-09-30", "party": "Makro", "waiting": True},
                {"doc_id": "h3", "ref": "IV-3", "date": "2026-10-09", "party": "Lotus", "waiting": False},
                {"doc_id": "h4", "ref": "", "date": "", "party": "", "waiting": True}]},
            "B-f": {"state": "failed", "page_count": 1, "code": "provider_failed"},
        }  # B-x：产品读不到（删了、没权限了）
        out = api.task_uploads(self.task)
        self.assertEqual((out["task"], out["period"]), (self.task, "2026-10"))
        by = {row["request_key"]: row for row in out["uploads"]}
        self.assertEqual([row["request_key"] for row in out["uploads"]], ["q", "r", "d", "f", "x"])
        self.assertEqual((by["q"]["state"], by["q"]["code"]), ("queued", "insufficient_balance"))
        self.assertEqual((by["r"]["state"], by["r"]["pages_done"], by["r"]["page_count"]), ("reading", 3, 12))
        self.assertEqual((by["d"]["state"], by["d"]["pages_done"]), ("done", 12))
        self.assertEqual([(d["doc_id"], d["in_period"], d["listed"]) for d in by["d"]["documents"]],
                         [("h1", True, True), ("h2", False, False), ("h3", True, False), ("h4", False, False)])
        self.assertEqual((by["f"]["state"], by["f"]["code"]), ("failed", "provider_failed"))
        self.assertEqual((by["x"]["state"], by["x"]["code"]), ("failed", shapes.UNAVAILABLE))
        self.assertEqual(self.product.asked[-1], ("CO-1", "2026-10", ["B-q", "B-r", "B-d", "B-f", "B-x"]))

    def test_only_the_named_batches_are_read_again(self):
        self.product.status = {"B-r": {"state": "reading", "pages_done": 5, "page_count": 12}}
        out = api.task_uploads(self.task, '["B-r"]')
        self.assertEqual(self.product.asked[-1], ("CO-1", "2026-10", ["B-r"]))
        by = {row["request_key"]: row for row in out["uploads"]}
        self.assertEqual((by["r"]["state"], by["r"]["pages_done"]), ("reading", 5))
        self.assertEqual({by[key]["state"] for key in ("q", "d", "f", "x")}, {""})  # 列出但没现读
        with self.assertRaises(ValidationError_):
            api.task_uploads(self.task, '{"B-r": 1}')

    def test_only_the_creator_reads_the_uploads(self):
        frappe.session.user = "b@example.com"
        with self.assertRaises(DoesNotExistError_):
            api.task_uploads(self.task)
        frappe.session.user = "Guest"
        with self.assertRaises(PermissionError_):
            api.task_uploads(self.task)
        self.assertEqual(self.product.asked, [])

    def test_shape_rules(self):
        self.assertEqual(shapes.upload_view(Row(), {"state": "nonsense"}, "2026-10")["state"], "failed")
        row = shapes.upload_view(Row(), {"state": "reading", "pages_done": 99, "page_count": 3}, "2026-10")
        self.assertEqual((row["pages_done"], row["page_count"]), (3, 3))  # 读到第几页不超过总页数
        row = shapes.upload_view(Row(), {"state": "done", "page_count": 2, "code": "x", "documents": [{"ref": "no id"}]}, "")
        self.assertEqual((row["code"], row["documents"]), ("", []))


class CoworkUploadStateTests(unittest.TestCase):
    """Cowork 的识别记录折成上传状态（与录入工作台等候页同一个判据），识别出的单带「在不在资料里」。"""

    @classmethod
    def setUpClass(cls):
        scope = types.ModuleType("pearnly_cowork.access.scope")
        access = types.ModuleType("pearnly_cowork.access")
        access.scope = scope
        cls.source = _load("cowork_operator_source_code_cc",
                           ROOT / "apps/pearnly_cowork/pearnly_cowork/assistant/operator_source.py",
                           {"pearnly_cowork": types.ModuleType("pearnly_cowork"), "pearnly_cowork.access": access,
                            "pearnly_cowork.access.scope": scope})

    def test_phases_and_queue_states_map_to_the_card_states(self):
        state = self.source.upload_state
        self.assertEqual(state({"state": "queued", "page_count": 12, "pages_done": 0, "reading": False})["state"], "queued")
        self.assertEqual(state({"state": "deferred", "page_count": 12, "last_code": "insufficient_balance"}),
                         {"state": "queued", "pages_done": 0, "page_count": 12, "code": "insufficient_balance"})
        self.assertEqual(state({"state": "running", "page_count": 12, "pages_done": 3, "reading": True}),
                         {"state": "reading", "pages_done": 3, "page_count": 12, "code": ""})
        self.assertEqual(state({"state": "saved", "page_count": 12, "pages_done": 12})["state"], "done")
        self.assertEqual(state({"state": "not_invoice", "page_count": 1})["state"], "done")
        for failed in ("failed", "uncertain", "cancelled"):
            self.assertEqual(state({"state": failed, "page_count": 1, "last_code": ""}),
                             {"state": "failed", "pages_done": 0, "page_count": 1, "code": failed})
        self.assertEqual(state({"state": "failed", "page_count": 1, "last_code": "provider_failed"})["code"],
                         "provider_failed")

    def test_uploads_reads_each_batch_and_the_recognized_documents(self):
        projects = {"B1": {"state": "saved", "page_count": 2, "history_records": ["h1", "h2"]},
                    "B2": {"state": "queued", "page_count": 3, "reading": False}}

        def detail(name):
            if name == "gone":
                raise DoesNotExistError_(name)
            if name == "theirs":
                raise PermissionError_(name)
            return projects[name]

        ocr = types.ModuleType("pearnly_cowork.ocr.execution.service")
        ocr.detail = detail
        tools = types.ModuleType("pearnly_cowork.assistant.tools")
        tools.PUSHED_OR_ON_ITS_WAY = ["pending", "success"]
        history = types.ModuleType("pearnly_core.express_rules.contracts.history")
        history.primary_page_fields = lambda pages: pages[0]["fields"] if pages else {}
        STATE["tables"] = {"Cowork OCR History": [
            Row(name="h1", invoice_no="IV-1", invoice_date=datetime.date(2026, 10, 3), seller_name="Makro",
                buyer_name="Us", last_push_status=None, pages=[{"fields": {"direction": "purchase"}}]),
            Row(name="h2", invoice_no="IV-2", invoice_date=datetime.date(2026, 10, 4), seller_name="Us",
                buyer_name="Shop", last_push_status="success", pages=[{"fields": {"direction": "sales"}}])]}
        parents = {name: types.ModuleType(name) for name in (
            "pearnly_cowork", "pearnly_cowork.assistant", "pearnly_cowork.ocr", "pearnly_cowork.ocr.execution",
            "pearnly_core.express_rules", "pearnly_core.express_rules.contracts")}
        parents["pearnly_cowork.assistant"].tools = tools
        parents["pearnly_cowork.ocr.execution"].service = ocr
        parents["pearnly_core.express_rules.contracts"].history = history
        with mock.patch.dict(sys.modules, {**parents, "pearnly_cowork.ocr.execution.service": ocr,
                                           "pearnly_cowork.assistant.tools": tools,
                                           "pearnly_core.express_rules.contracts.history": history}):
            out = self.source.uploads("CO-1", "2026-10", ["B1", "B2", "gone", "theirs", "B1"])
        self.assertEqual(set(out), {"B1", "B2"})  # 读不到的不回
        self.assertEqual(out["B2"]["state"], "queued")
        self.assertEqual(out["B1"]["documents"], [
            {"doc_id": "h1", "ref": "IV-1", "date": "2026-10-03", "party": "Makro", "waiting": True},
            {"doc_id": "h2", "ref": "IV-2", "date": "2026-10-04", "party": "Shop", "waiting": False}])


class ProgressEventTests(unittest.TestCase):
    """B5：识别排队、开始读、读到第几页、有结论都已推给提交人（同一个事件，`reference` 是识别任务号 = 上传的 `batch`），
    这次不另加推送。"""

    def test_recognition_pushes_reading_pages_and_conclusion_to_the_submitter(self):
        store = (ROOT / "apps/pearnly_cowork/pearnly_cowork/ocr/execution/store.py").read_text(encoding="utf-8")
        work = (CORE / "execution/work.py").read_text(encoding="utf-8")
        self.assertIn('"reading": True', store)
        self.assertIn('"pages_done": page - 1', store)
        self.assertIn('frappe.publish_realtime("pearnly_ocr_work", {"kind": owner.kind, "reference": owner.reference,', work)
        self.assertIn('"status": status}, user=owner.user', work)
        self.assertIn("_push(name, status)", work)
        workflow = (ROOT / "apps/pearnly_cowork/pearnly_cowork/ocr/execution/workflow.py").read_text(encoding="utf-8")
        self.assertIn("core_work.notify_result()", workflow)  # 结果入库就推「有结论」


class PrecheckTargetTests(Base):
    """B4：预检回包带写进哪份账套，共用层合成一份或几份。"""

    def test_one_account_set_for_every_document(self):
        target = {"account_set": "S:\\69EXP\\ABC", "account_set_title": "ABC 69", "account_set_path": "S:\\69EXP\\ABC",
                  "connection": "C1", "connection_title": "Office"}
        self.product.precheck_rows = [{"doc_id": "h1", "state": "ready", "target": target},
                                      {"doc_id": "h2", "state": "choose", "needed": [], "target": target},
                                      {"doc_id": "h3", "state": "blocked", "message": "x"}]
        out = api.precheck("CO-1", '["h1", "h2", "h3"]')
        self.assertEqual(out["target"], {**target, "count": 2})
        self.assertEqual(out["targets"], [out["target"]])
        self.assertEqual(out["documents"][0]["target"], target)
        self.assertNotIn("target", out["documents"][2])  # 判不出的单不带

    def test_several_account_sets_are_listed_and_target_is_none(self):
        one = {"account_set": "A", "account_set_title": "", "connection": "C1"}
        two = {"account_set": "B", "account_set_title": "Book B", "connection": "C1", "writers": ["pc-2", "pc-1", "pc-2"]}
        self.product.precheck_rows = [{"doc_id": "h1", "state": "ready", "target": one},
                                      {"doc_id": "h2", "state": "ready", "target": two},
                                      {"doc_id": "h3", "state": "ready", "target": two},
                                      {"doc_id": "h4", "state": "blocked", "target": None}]
        out = api.precheck("CO-1", ["h1", "h2", "h3", "h4"])
        self.assertIsNone(out["target"])
        self.assertEqual([(t["account_set"], t["account_set_title"], t["count"]) for t in out["targets"]],
                         [("B", "Book B", 2), ("A", "A", 1)])
        self.assertEqual(out["targets"][0]["writers"], ["pc-1", "pc-2"])
        self.assertIsNone(out["documents"][3]["target"])

    def test_no_target_and_no_permission(self):
        self.product.precheck_rows = [{"doc_id": "h1", "state": "blocked"}]
        out = api.precheck("CO-1", ["h1"])
        self.assertEqual((out["targets"], out["target"]), ([], None))
        with self.assertRaises(PermissionError_):
            api.precheck("CO-9", ["h1"])

    def test_erp_takes_the_account_set_its_preparation_picked(self):
        stubs = {name: types.ModuleType(name) for name in (
            "pearnly_erp_portal", "pearnly_erp_portal.access", "pearnly_erp_portal.access.guard",
            "pearnly_erp_portal.express", "pearnly_erp_portal.express.contracts", "pearnly_erp_portal.express.push",
            "pearnly_erp_portal.express.api", "pearnly_erp_portal.express.facts", "pearnly_erp_portal.masters",
            "pearnly_erp_portal.masters.company", "pearnly_core.cc_desktop", "pearnly_core.cc_desktop.jobs")}
        guard = stubs["pearnly_erp_portal.access.guard"]
        guard.has = lambda capability, user=None: True
        guard.scope_filters = lambda: {}
        stubs["pearnly_erp_portal.access"].guard = guard
        contracts = stubs["pearnly_erp_portal.express.contracts"]
        contracts.CHOICE, contracts.READING = "choice_needed", "reading"
        push = stubs["pearnly_erp_portal.express.push"]
        push.PURCHASE, push.SALES = "Pearnly Purchase", "Pearnly Sales"
        push.company_of = lambda doctype, name: "CO-1"
        book = {"key": "BK-69", "label": "ABC 69", "path": "S:\\69EXP\\ABC", "writers": ["pc-1"]}
        prepared = {"PUR-1": {"payload": {}, "book": book}, "PUR-2": {"hold": "choice_needed:item", "book": book,
                                                                       "needed": []},
                    "PUR-3": {"hold": "express.account_set_missing"}}
        push.prepare_only = lambda doctype, name: prepared[name]
        stubs["pearnly_erp_portal.express.api"]._options = lambda item, data: []
        stubs["pearnly_erp_portal.express.facts"].load = lambda key: None
        express = stubs["pearnly_erp_portal.express"]
        express.contracts, express.push = contracts, push
        express.api, express.facts = stubs["pearnly_erp_portal.express.api"], stubs["pearnly_erp_portal.express.facts"]
        stubs["pearnly_erp_portal.masters"].company = stubs["pearnly_erp_portal.masters.company"]
        stubs["pearnly_core.cc_desktop"].jobs = stubs["pearnly_core.cc_desktop.jobs"]
        operator = _load("erp_operator_code_cc", ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/express/operator.py",
                         stubs)
        with mock.patch.dict(sys.modules, stubs), mock.patch.object(frappe.db, "get_value", lambda *a, **k: None):
            out = operator.precheck("CO-1", ["P:PUR-1", "P:PUR-2", "P:PUR-3", "X:bad"])
        by = {row["doc_id"]: row for row in out["documents"]}
        expected = {"account_set": "BK-69", "account_set_title": "ABC 69", "account_set_path": "S:\\69EXP\\ABC",
                    "connection": "", "connection_title": "", "writers": ["pc-1"]}
        self.assertEqual((by["P:PUR-1"]["state"], by["P:PUR-1"]["target"]), ("ready", expected))
        self.assertEqual((by["P:PUR-2"]["state"], by["P:PUR-2"]["target"]), ("choose", expected))
        self.assertNotIn("target", by["P:PUR-3"])  # 没选出账套
        self.assertEqual((by["X:bad"]["state"], by["X:bad"]["reason"]), ("blocked", "not_permitted"))
        self.assertIsNone(operator.book_target({"label": "no key"}))
        self.assertEqual(operator.book_target({"key": "K"})["account_set_title"], "K")

    def test_cowork_takes_the_account_set_its_push_preparation_froze(self):
        """`operator_check` 的账套与推送准备同一个 `_express_context`；每种结论都带上，挡下的（如票面税号与账套不符）也带。"""
        path = ROOT / "apps/pearnly_cowork/pearnly_cowork/push/application/service.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        found = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        check = ast.get_source_segment(source, found["operator_check"])
        self.assertIn('found = _express_context(doc, loaded, doc.push_account_set or None)\n        target = _operator_target(',
                      check)
        self.assertLess(check.index("target = _operator_target("), check.index("_require_matching_account("))
        for line in ('return {"state": "ready", "target": target}', '"target": target}',
                     '**({"target": target} if target else {})', 'return {"state": "choose", "needed": needed, "target": target}'):
            self.assertIn(line, check)
        namespace = {}
        exec(compile(ast.Module(body=[found["_operator_target"]], type_ignores=[]), str(path), "exec"), namespace)
        doc = Row(push_connection="C1")
        loaded = types.SimpleNamespace(doc=Row(label="Office Express"))
        made = namespace["_operator_target"](doc, loaded, {
            "target": types.SimpleNamespace(account_set="S:\\69EXP\\ABC"),
            "account_row": {"label": "ABC 69", "attributes": {"path": "S:\\69EXP\\ABC"}}})
        self.assertEqual(made, {"account_set": "S:\\69EXP\\ABC", "account_set_title": "ABC 69",
                                "account_set_path": "S:\\69EXP\\ABC", "connection": "C1",
                                "connection_title": "Office Express"})
        bare = namespace["_operator_target"](doc, types.SimpleNamespace(doc=Row()), {
            "target": types.SimpleNamespace(account_set="K"), "account_row": None})
        self.assertEqual((bare["account_set_title"], bare["account_set_path"], bare["connection_title"]), ("K", "", ""))


class PendingMonthTests(unittest.TestCase):
    """B3：待处理按「公司 · 月份」，带本人这家这个月最近的对话。"""

    def setUp(self):
        STATE.update(queries=[], tables={})
        frappe.session.user = "a@example.com"
        self.engine = "claude"
        config = types.SimpleNamespace(engine_code=lambda: self.engine)
        package = types.ModuleType("pearnly_core.assistant")
        modules = {"pearnly_core.assistant": package}
        for name in ("config", "context", "contracts", "engine", "engines", "notices", "prefs", "projects", "relay",
                     "sidebar", "titles", "toolrun", "tools"):
            setattr(package, name, types.SimpleNamespace())
        package.config, package.contracts = config, assistant_contracts
        package.context = types.SimpleNamespace(SESSION="Pearnly Assistant Session")
        self.sessions = _load("assistant_sessions_code_cc", CORE / "assistant/sessions.py", modules)

    def test_counts_by_company_and_month(self):
        items = [{"company": "CO-1", "month": "2026-10"}, {"company": "CO-1", "month": "2026-10"},
                 {"company": "CO-1", "month": "2026-09"}, {"company": "CO-2", "month": "2026-10"},
                 {"company": None, "month": "2026-10"}, {"company": "CO-1", "month": "2026-1"}]
        self.assertEqual(assistant_contracts.pending_months(items), [
            {"company": "CO-1", "month": "2026-10", "count": 2}, {"company": "CO-1", "month": "2026-09", "count": 1},
            {"company": "CO-2", "month": "2026-10", "count": 1}])
        self.assertEqual(assistant_contracts.pending_months([]), [])

    def test_latest_own_open_conversation_of_that_company_and_month(self):
        session = lambda sid, project, period, modified, **extra: Row(  # noqa: E731
            name="r" + sid, session_id=sid, project=project, period=period, user=extra.get("user", "a@example.com"),
            engine_code=extra.get("engine", "claude"), archived=extra.get("archived", 0),
            operator_task=extra.get("operator_task"), modified=modified)
        STATE["tables"]["Pearnly Assistant Session"] = [
            session("old", "CO-1", "2026-10", "2026-10-01"), session("new", "CO-1", "2026-10", "2026-10-20"),
            session("archived", "CO-1", "2026-10", "2026-10-30", archived=1),
            session("task", "CO-1", "2026-10", "2026-10-31", operator_task="t001"),
            session("theirs", "CO-2", "2026-10", "2026-10-31", user="b@example.com"),
            session("engine", "CO-2", "2026-10", "2026-10-31", engine="other"),
            session("sep", "CO-1", "2026-09", "2026-09-05")]
        items = [{"company": "CO-1", "month": "2026-10"}, {"company": "CO-1", "month": "2026-09"},
                 {"company": "CO-2", "month": "2026-10"}]
        self.assertEqual(self.sessions.pending_months(items), [
            {"company": "CO-1", "month": "2026-10", "count": 1, "conversation": "new"},
            {"company": "CO-1", "month": "2026-09", "count": 1, "conversation": "sep"},
            {"company": "CO-2", "month": "2026-10", "count": 1, "conversation": ""}])
        doctype, filters, order, limit = STATE["queries"][-1]
        self.assertEqual((doctype, order, limit), ("Pearnly Assistant Session", "modified desc, name desc",
                                                   self.sessions.MONTH_SCAN))  # 一次查询、有上限
        self.assertEqual(filters["user"], "a@example.com")

    def test_no_user_or_no_engine_means_no_conversation_and_no_query(self):
        items = [{"company": "CO-1", "month": "2026-10"}]
        frappe.session.user = "Guest"
        self.assertEqual(self.sessions.pending_months(items)[0]["conversation"], "")
        frappe.session.user = "a@example.com"
        self.engine = ""
        self.assertEqual(self.sessions.pending_months(items)[0]["conversation"], "")
        self.assertEqual(self.sessions.pending_months([]), [])
        self.assertEqual(STATE["queries"], [])


if __name__ == "__main__":
    unittest.main()
