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
import io
import json
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

        def remove(self, row):
            for key in ("documents", "uploads"):
                self[key] = [item for item in self[key] if item is not row]

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
    def set_value(doctype, name, field, value=None, update_modified=True):
        for row in state["tasks"]:
            if row["name"] == name:
                row[field] = value

    frappe.db = types.SimpleNamespace(get_value=get_value, get_all=get_all, set_value=set_value,
                                      commit=lambda: state.setdefault("commits", []).append(1),
                                      rollback=lambda: state.setdefault("rollbacks", []).append(1))
    frappe.local = types.SimpleNamespace(site="test.site", lang="th")
    frappe.log_error = lambda **kwargs: state.setdefault("errors", []).append(kwargs.get("title"))

    class Lock:
        def __init__(self, key, **kwargs):
            state.setdefault("locks", []).append(key)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    frappe.cache = types.SimpleNamespace(lock=Lock)
    frappe.parse_json = lambda text: __import__("json").loads(text) if isinstance(text, str) else text
    frappe.as_json = lambda value: __import__("json").dumps(value, default=str)
    frappe.get_hooks = lambda name: state["hooks"].get(name)
    frappe.get_attr = lambda path: state["attrs"][path]
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = lambda: state["now"]
    utils.get_system_timezone = lambda: "Asia/Bangkok"
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
        if getattr(self, "deferred", False):
            from pearnly_core.execution.errors import Deferred

            raise Deferred("oracle_unreachable", 10)
        if getattr(self, "broken", False):
            raise RuntimeError("startxref not found")
        if not self.accept:
            return {"accepted": False, "code": "no_target"}
        return {"accepted": True, "source": f"SRC-{request_key}", "name": f"B-{request_key}", "page_count": 12,
                "state": "queued"}

    def uploads(self, company, period, batches):
        self.asked.append((company, period, list(batches)))
        return {batch: self.status[batch] for batch in batches if batch in self.status}


class File:
    def __init__(self, name, content):
        # 同真的上传流：读到结尾回 b""（11-12 起资料区按块落盘，ADR-028 第 2 步）
        self.filename, self.stream = name, io.BytesIO(content)


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
                                         # 上传时刻给页面的是 RFC3339 UTC（库里是曼谷时间 16:00）
                                         "batch": "B-k1", "uploaded_at": "2026-11-04T09:00:00Z", "state": "queued",
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

    def test_an_unreachable_store_answers_a_retry_sentence_instead_of_a_server_error(self):
        """11-06 测试机：原件存放处（甲骨文）一时连不上时产品抛 Deferred，以前没接住、页面收到 500；现在回「暂时无法上传，
        请稍后重试。」（页面把文件放回输入框，可重发），任务上不记这一份。"""
        task = tasks.create("CO-1", "2026-10")["name"]
        self.product.deferred = True
        with self.assertRaises(ValidationError_) as caught:
            upload("CO-1", "k1", task)
        self.assertEqual(str(caught.exception), "upload.deferred")
        self.assertEqual(frappe.get_doc("Pearnly Express Operator Task", task).uploads, [])
        self.product.deferred = False
        self.assertTrue(upload("CO-1", "k1", task)["accepted"])  # 连得上了，同一份重发照常收

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


class ConversationUploadTests(Base):
    """代录会话输入框里的文件（2026-11-06「选中就上传」；11-14 用户：上传是为了点发送不用等，识别要等点了发送才开始）：
    对话的上传口只在任务上记一行「待发送」（``uploads.hold``：不交识别、不扣费、没有上传时刻，对话里不出卡片）；带着它的消息发出时
    记上时刻（``uploads.sent``，卡片排在这条消息后面），之后才交产品识别（``uploads.start``，后台或识别工具当场）。没发出就移除的
    删掉那一行（``uploads.drop``），从头到尾不识别。"""

    def setUp(self):
        super().setUp()
        self.task = tasks.create("CO-1", "2026-10")["name"]
        self.doc = frappe.get_doc("Pearnly Express Operator Task", self.task)
        before = STATE["now"]
        self.addCleanup(STATE.__setitem__, "now", before)

    @staticmethod
    def note(*keys):
        return "看看\n\n[Attached file, mounted read-only in the sandbox:\n" + "".join(
            f"- {key}.pdf → /mnt/session/uploads/{key}/{key}.pdf\n" for key in keys) + "]"

    def send(self, text):
        """输入框点发送（``sessions.send`` 同样两步：认出消息带着的几份 ``files.ids_in``，交给唯一入口 ``files.commit`` → ``commit``）。"""
        keys = [row.request_key for row in self.doc.uploads if f"/uploads/{row.request_key}/" in text]
        return uploads.commit(self.task, keys)

    def shown(self):
        feed = importlib.import_module("pearnly_core.express_operator.feed")
        return [e["input"]["filename"] for e in feed.project(self.doc, None, "UTC") if e.get("name") == feed.UPLOAD]

    def test_a_file_in_the_composer_is_only_held_until_the_message_is_sent(self):
        view = uploads.hold(self.task, "bill.pdf", 6, "upl_1")
        self.assertEqual(self.product.intakes, [])  # 选中只上传：不交识别、不扣费
        self.assertEqual((view["batch"], view["uploaded_at"]), ("", ""))
        self.assertEqual([(r.request_key, r.batch, r.uploaded_at) for r in self.doc.uploads], [("upl_1", "", None)])
        self.assertEqual(self.shown(), [])  # 还在输入框里：对话里没有上传卡片
        self.assertEqual(uploads.read(self.task)["uploads"], [])  # 也不算任务里的上传
        self.assertEqual(uploads.start(self.task, "upl_1", b"%PDF-x"), "")  # 没发出的交不了识别
        self.assertEqual(self.product.intakes, [])
        self.assertEqual(self.send("看看这张"), [])  # 不带它的消息不算
        STATE["now"] = datetime.datetime(2026, 11, 4, 16, 5)
        self.assertEqual(self.send(self.note("upl_1")), ["upl_1"])
        self.assertEqual(self.shown(), ["bill.pdf"])
        self.assertEqual(self.doc.uploads[0].uploaded_at, STATE["now"])  # 发出那一刻：卡片排在这条消息后面
        # 发出了、后台还没交上：卡片是「排队中」，不是「读不到」
        self.assertEqual(uploads.read(self.task)["uploads"][0]["state"], "queued")
        feed = importlib.import_module("pearnly_core.express_operator.feed")
        self.assertEqual([v["state"] for v in feed.upload_states(self.doc).values()], ["queued"])
        self.assertTrue(uploads.waiting(self.task, "upl_1"))
        # 这时才交识别
        self.assertEqual(uploads.start(self.task, "upl_1", b"%PDF-x"), "B-upl_1")
        self.assertEqual(self.product.intakes, [("CO-1", "bill.pdf", 6, "upl_1")])
        self.assertEqual((self.doc.uploads[0].batch, self.doc.uploads[0].source), ("B-upl_1", "SRC-upl_1"))
        self.assertFalse(uploads.waiting(self.task, "upl_1"))
        # 再来一次（后台与识别工具都来了）：回原来那个，不再交
        self.assertEqual(uploads.start(self.task, "upl_1", b"%PDF-x"), "B-upl_1")
        self.assertEqual(len(self.product.intakes), 1)
        self.assertEqual(uploads.batch_of(self.task, "upl_1"), "B-upl_1")
        self.assertEqual(self.send(self.note("upl_1")), [])  # 再发一遍同一句不重记

    def test_commit_is_the_one_way_in_and_can_be_called_again(self):
        # 接缝（引导卡上提交与输入框点发送同一个入口）：交出才记时刻；再交同几份不重记时刻，还没交上识别的照样回（后台重排无害）
        uploads.hold(self.task, "a.pdf", 6, "upl_1")
        uploads.hold(self.task, "b.pdf", 6, "upl_2")
        self.assertEqual(uploads.commit(self.task, []), [])
        self.assertEqual(uploads.commit(self.task, ["upl_9"]), [])  # 不认识的不算
        STATE["now"] = datetime.datetime(2026, 11, 4, 16, 5)
        self.assertEqual(uploads.commit(self.task, ["upl_1"]), ["upl_1"])
        first = self.doc.uploads[0].uploaded_at
        STATE["now"] = datetime.datetime(2026, 11, 4, 16, 9)
        self.assertEqual(uploads.commit(self.task, ["upl_1"]), ["upl_1"])
        self.assertEqual(self.doc.uploads[0].uploaded_at, first)
        self.assertIsNone(self.doc.uploads[1].uploaded_at)  # 没交出的那份还是「待发送」
        self.assertEqual(self.product.intakes, [])  # 入口本身不识别：提交后由后台交
        uploads.start(self.task, "upl_1", b"x")
        self.assertEqual(uploads.commit(self.task, ["upl_1"]), [])
        self.assertEqual(len(self.product.intakes), 1)

    def test_a_file_removed_before_sending_is_never_recognized(self):
        uploads.hold(self.task, "a.pdf", 6, "upl_1")
        uploads.hold(self.task, "b.pdf", 6, "upl_2")
        self.assertTrue(uploads.drop(self.task, "upl_1"))
        self.assertEqual([r.request_key for r in self.doc.uploads], ["upl_2"])
        self.assertFalse(uploads.drop(self.task, "upl_1"))  # 已删的、不认识的
        self.assertEqual(self.send(self.note("upl_1", "upl_2")), ["upl_2"])
        self.assertFalse(uploads.drop(self.task, "upl_2"))  # 已发出的不删
        self.assertEqual(uploads.start(self.task, "upl_1", b"x"), "")
        self.assertEqual(uploads.start(self.task, "upl_2", b"x"), "B-upl_2")
        self.assertEqual([i[3] for i in self.product.intakes], ["upl_2"])  # 移除的那份从头到尾没交识别

    def test_several_files_in_one_message_each_start_once(self):
        for key in ("upl_1", "upl_2", "upl_3"):
            uploads.hold(self.task, f"{key}.pdf", 6, key)
        self.assertEqual(self.send(self.note("upl_1", "upl_3")), ["upl_1", "upl_3"])
        for key in ("upl_1", "upl_3", "upl_1"):
            uploads.start(self.task, key, b"x")
        self.assertEqual([i[3] for i in self.product.intakes], ["upl_1", "upl_3"])
        self.assertEqual([r.batch for r in self.doc.uploads], ["B-upl_1", "", "B-upl_3"])  # 没带上的那份还在输入框里

    def test_a_start_that_fails_shows_failed_and_can_start_again(self):
        uploads.hold(self.task, "bill.pdf", 6, "upl_1")
        self.send(self.note("upl_1"))
        self.product.deferred = True  # 原件存放处一时连不上
        self.assertEqual(uploads.start(self.task, "upl_1", b"x"), "")
        row = uploads.read(self.task)["uploads"][0]
        self.assertEqual((row["state"], row["code"]), ("failed", "upload.deferred"))
        self.product.deferred = False
        self.product.accept = False  # 产品不收（例如还没选账套）
        uploads.start(self.task, "upl_1", b"x")
        self.assertEqual(uploads.read(self.task)["uploads"][0]["code"], "no_target")
        self.product.allowed = {"CO-2"}  # 发出前这家公司不能代录了
        self.assertEqual(uploads.start(self.task, "upl_1", b"x"), "")
        self.assertEqual(uploads.read(self.task)["uploads"][0]["code"], "start_refused")
        self.product.allowed = {"CO-1"}
        self.product.broken = True  # 产品读文件出了意外（11-14 实验栈：坏 PDF 让那一行一直停在排队中）
        self.assertEqual(uploads.start(self.task, "upl_1", b"x"), "")
        self.assertEqual(uploads.read(self.task)["uploads"][0]["code"], "start_failed")
        self.product.broken = False
        self.product.accept = True  # 下次再来照样重交
        self.assertEqual(uploads.start(self.task, "upl_1", b"x"), "B-upl_1")
        self.assertEqual(self.doc.uploads[0].start_code, "")

    def test_hold_refuses_like_intake_and_skips_what_the_product_does_not_take(self):
        self.product.install(intake=False)  # 产品不收上传：文件照常只进对话，任务上不记
        self.assertIsNone(uploads.hold(self.task, "bill.pdf", 1, "upl_2"))
        self.assertEqual(self.doc.uploads, [])
        self.product.install()
        self.product.allowed = {"CO-2"}
        self.assertIsNone(uploads.hold(self.task, "bill.pdf", 1, "upl_3"))  # 这家公司现在不能代录：不记
        self.product.allowed = {"CO-1"}
        frappe.session.user = "b@example.com"
        with self.assertRaises(DoesNotExistError_):
            uploads.hold(self.task, "bill.pdf", 1, "upl_4")  # 别人的任务
        with self.assertRaises(DoesNotExistError_):
            uploads.drop(self.task, "upl_4")
        self.assertEqual(self.product.intakes, [])


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
        # 方向按 `tools.judged_direction`（声明的优先，没声明按推送同一套税号判据；判据本身在 test_cowork_assistant_tools）
        tools.direction_anchors = lambda: None
        tools.judged_direction = lambda page, *args, **kwargs: str((page or {}).get("direction") or "")
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


class CoworkElsewhereTests(unittest.TestCase):
    """A 线 11-15：这个月以外还等着录的单按月计数，与资料区同一个列表（`tools.list_documents` 的 waiting、按公司账套），只换日期。"""

    def test_other_months_are_counted_before_and_after_this_month_page_by_page(self):
        scope = types.ModuleType("pearnly_cowork.access.scope")
        access = types.ModuleType("pearnly_cowork.access")
        access.scope = scope
        source = _load("cowork_operator_source_elsewhere",
                       ROOT / "apps/pearnly_cowork/pearnly_cowork/assistant/operator_source.py",
                       {"pearnly_cowork": types.ModuleType("pearnly_cowork"), "pearnly_cowork.access": access,
                        "pearnly_cowork.access.scope": scope})
        calls = []
        pages = {("to", 0): ([{"invoice_date": "2026-06-02"}, {"invoice_date": "2026-06-30"}], 2),
                 ("to", 2): ([{"invoice_date": "2026-05-09"}, {"invoice_date": None}], None),
                 ("from", 0): ([{"invoice_date": "2026-11-01"}], None)}

        def list_documents(given):
            calls.append(given)
            side = "to" if "invoice_date_to" in given else "from"
            rows, following = pages[(side, given["offset"])]
            return json.dumps({"documents": rows, "next_offset": following})

        tools = types.ModuleType("pearnly_cowork.assistant.tools")
        tools.LIST_MAX, tools.list_documents = 2, list_documents
        parents = {"pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.assistant": types.ModuleType("pearnly_cowork.assistant")}
        parents["pearnly_cowork.assistant"].tools = tools
        with mock.patch.dict(sys.modules, {**parents, "pearnly_cowork.assistant.tools": tools}):
            out = source.elsewhere("CO-1", "2026-10")
            self.assertEqual(out, [{"month": "2026-11", "count": 1}, {"month": "2026-06", "count": 2},
                                   {"month": "2026-05", "count": 1}])
            self.assertEqual([(c.get("invoice_date_to"), c.get("invoice_date_from"), c["offset"]) for c in calls],
                             [("2026-09-30", None, 0), ("2026-09-30", None, 2), (None, "2026-11-01", 0)])
            self.assertTrue(all(c["company"] == "CO-1" and c["push_state"] == "waiting" for c in calls))
            tools.list_documents = lambda given: json.dumps({"status": "failed", "reason": "no_company"})
            self.assertEqual(source.elsewhere("CO-1", "2026-10"), [])


class SharedUploadStateTests(unittest.TestCase):
    """A 批 1：上传卡片状态的判法在共用层一份（``shapes.upload_state``），Cowork（识别记录）与 ERP（收单记录 + 共用识别队列，
    ``shapes.work_state``）都用它，两边同样的情形得到同样的卡片。"""

    @classmethod
    def setUpClass(cls):
        cls.shapes = _load("operator_shapes_code_cc", ROOT / "apps/pearnly_core/pearnly_core/express_operator/shapes.py")

    def test_erp_intake_states_match_the_cowork_card(self):
        work = self.shapes.work_state
        self.assertEqual(work("queued", "queued"), {"state": "queued", "pages_done": 0, "page_count": 0, "code": ""})
        self.assertEqual(work("queued", "running")["state"], "reading")
        self.assertEqual(work("ready", "succeeded")["state"], "done")
        self.assertEqual(work("confirmed", "succeeded")["state"], "done")
        self.assertEqual(work("failed", "succeeded", "This is not a purchase or sales document"),
                         {"state": "failed", "pages_done": 0, "page_count": 0, "code": "This is not a purchase or sales document"})
        for failed in ("failed", "uncertain", "cancelled"):
            self.assertEqual(work("queued", failed), {"state": "failed", "pages_done": 0, "page_count": 0, "code": failed})
        self.assertEqual(work("failed", "", "")["code"], "unavailable")
        cowork = _load("cowork_operator_source_shared",
                       ROOT / "apps/pearnly_cowork/pearnly_cowork/assistant/operator_source.py",
                       {"pearnly_cowork": types.ModuleType("pearnly_cowork"),
                        "pearnly_cowork.access": types.SimpleNamespace(scope=types.ModuleType("scope")),
                        "pearnly_cowork.access.scope": types.ModuleType("scope"),
                        "pearnly_core.express_operator.shapes": self.shapes,
                        "pearnly_core.express_operator": types.SimpleNamespace(shapes=self.shapes)})
        source = (ROOT / "apps/pearnly_cowork/pearnly_cowork/assistant/operator_source.py").read_text(encoding="utf-8")
        self.assertIn("shapes.upload_state(", source)  # Cowork 也走共用的判法
        self.assertEqual(cowork.upload_state({"state": "uncertain", "page_count": 1}), work("queued", "uncertain") | {"page_count": 1})

    def test_only_a_product_that_declares_regroup_lets_a_document_be_dragged(self):
        """A 批 2：资料区能不能拖、能拖到哪几组由站点给（``moves``）；产品没声明改分组（ERP）一律不能拖（原来页面照样让拖，
        放下才报错弹回）。只认已知的组、去掉自己这组。"""
        moves = self.shapes.moves
        row = {"group": "purchase", "moves": ["expense", "sales", "purchase", "nowhere", "sales"]}
        self.assertEqual(moves(row, True), ["expense", "sales"])
        self.assertEqual(moves(row, False), [])
        self.assertEqual(moves({"group": "purchase"}, True), [])
        api = (ROOT / "apps/pearnly_core/pearnly_core/express_operator/api.py").read_text(encoding="utf-8")
        self.assertIn("shapes.moves(row, movable)", api)
        page = (ROOT / "frontend/assistant/src/components/tool-uis/operator-steps.tsx").read_text(encoding="utf-8")
        self.assertIn("disabled: !doc.moves?.length", page)
        self.assertNotIn("ENTRY_GROUPS", page)  # 页面不再自己定哪几组能拖


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
        one = {"account_set": "A", "account_set_title": "", "account_set_path": "S:\\A", "connection": "C1"}
        two = {"account_set": "B", "account_set_title": "Book B", "connection": "C1", "writers": ["pc-2", "pc-1", "pc-2"]}
        self.product.precheck_rows = [{"doc_id": "h1", "state": "ready", "target": one},
                                      {"doc_id": "h2", "state": "ready", "target": two},
                                      {"doc_id": "h3", "state": "ready", "target": two},
                                      {"doc_id": "h4", "state": "blocked", "target": None}]
        out = api.precheck("CO-1", ["h1", "h2", "h3", "h4"])
        self.assertIsNone(out["target"])
        self.assertEqual([(t["account_set"], t["account_set_title"], t["count"]) for t in out["targets"]],
                         [("B", "Book B", 2), ("A", "S:\\A", 1)])  # 没有名称写位置，不写账套键（内部编号）
        self.assertEqual(out["targets"][0]["writers"], ["pc-1", "pc-2"])
        self.assertIsNone(out["documents"][3]["target"])

    def test_no_target_and_no_permission(self):
        self.product.precheck_rows = [{"doc_id": "h1", "state": "blocked"}]
        out = api.precheck("CO-1", ["h1"])
        self.assertEqual((out["targets"], out["target"]), ([], None))
        with self.assertRaises(PermissionError_):
            api.precheck("CO-9", ["h1"])

    def test_the_start_card_precheck_runs_in_the_task_conversation(self):
        # 测试机1 11-13：代录开始卡按网页口径（按单据日期）预检，本对话账套已定还问「账套」、选项露内部编号。页面带上任务号时，
        # 预检在这个任务的对话里做（产品按本对话账套准备，同对话里的工具），不再出账套那一题；只认本人这个任务、同一家公司
        seen = []
        if not hasattr(frappe, "local"):
            frappe.local = types.SimpleNamespace()
            self.addCleanup(delattr, frappe, "local")
        STATE["attrs"]["p.precheck"] = lambda company, ids: seen.append(
            dict(getattr(frappe.local, "pearnly_assistant", None) or {})) or {"documents": []}
        before = getattr(frappe.local, "pearnly_assistant", None)
        with mock.patch.object(api.tasks, "read", lambda name: {"company": "CO-1", "session_id": "op_" + name}):
            api.precheck("CO-1", ["h1"], task="t1")
            api.precheck("CO-1", ["h1"])  # 没给任务：照旧（网页口径）
            with self.assertRaises(Throw):
                api.precheck("CO-2", ["h1"], task="t1")  # 任务不是这家公司的
        self.assertEqual([item.get("session_id") for item in seen], ["op_t1", None])
        self.assertEqual(getattr(frappe.local, "pearnly_assistant", None), before)  # 用完还原

    def test_the_start_card_answers_are_saved_in_the_same_task_conversation(self):
        # 11-17 回归（erp.cc.operator.intake.sales）：开始卡在任务对话里预检，引导口径出了收入科目题；记答案时不在那段对话里，
        # 按网页口径重判没有这一题，答案静默丢掉，写入计划仍判未定。记答案与预检同一段对话
        seen = []
        if not hasattr(frappe, "local"):
            frappe.local = types.SimpleNamespace()
            self.addCleanup(delattr, frappe, "local")
        stub = types.ModuleType("pearnly_core.write_precheck.products")
        stub.remember = lambda company, answers: seen.append(
            dict(getattr(frappe.local, "pearnly_assistant", None) or {})) or {"saved": 1}
        package = sys.modules.get("pearnly_core.write_precheck") or types.ModuleType("pearnly_core.write_precheck")
        before = getattr(frappe.local, "pearnly_assistant", None)
        with mock.patch.dict(sys.modules, {"pearnly_core.write_precheck": package, "pearnly_core.write_precheck.products": stub}), \
                mock.patch.object(package, "products", stub, create=True), \
                mock.patch.object(api.tasks, "read", lambda name: {"company": "CO-1", "session_id": "op_" + name}):
            api.remember("CO-1", {"h1": [{"kind": "account", "subject": "revenue", "value": "41-01"}]}, task="t1")
            api.remember("CO-1", {"h1": [{"kind": "account", "subject": "revenue", "value": "41-01"}]})  # 网页口径照旧
            with self.assertRaises(Throw):
                api.remember("CO-2", {"h1": []}, task="t1")  # 任务不是这家公司的
        self.assertEqual([item.get("session_id") for item in seen], ["op_t1", None])
        self.assertEqual(getattr(frappe.local, "pearnly_assistant", None), before)

    def test_erp_takes_the_account_set_its_preparation_picked(self):
        stubs = {name: types.ModuleType(name) for name in (
            "pearnly_erp_portal", "pearnly_erp_portal.access", "pearnly_erp_portal.access.guard",
            "pearnly_erp_portal.express", "pearnly_erp_portal.express.contracts", "pearnly_erp_portal.express.push",
            "pearnly_erp_portal.express.api", "pearnly_erp_portal.express.facts", "pearnly_erp_portal.express.precheck",
            "pearnly_erp_portal.masters",
            "pearnly_erp_portal.masters.company", "pearnly_core.cc_desktop", "pearnly_core.cc_desktop.jobs")}
        guard = stubs["pearnly_erp_portal.access.guard"]
        guard.has = lambda capability, user=None: True
        guard.scope_filters = lambda: {}
        stubs["pearnly_erp_portal.access"].guard = guard
        contracts = stubs["pearnly_erp_portal.express.contracts"]
        contracts.CHOICE, contracts.READING = "choice_needed", "reading"
        contracts.UNKNOWN_DOCUMENT, contracts.UNKNOWN_DOCUMENT_LOG = "unknown_document", "Write precheck: unknown document kind"
        push = stubs["pearnly_erp_portal.express.push"]
        push.PURCHASE, push.SALES = "Pearnly Purchase", "Pearnly Sales"
        push.company_of = lambda doctype, name: "CO-1"
        book = {"key": "BK-69", "label": "ABC 69", "path": "S:\\69EXP\\ABC", "writers": ["pc-1"]}
        prepared = {"PUR-1": {"payload": {}, "book": book}, "PUR-2": {"hold": "choice_needed:item", "book": book,
                                                                       "needed": []},
                    "PUR-3": {"hold": "express.account_set_missing"}}
        # 写账前置判定（11-05）：同一次准备的结论原样交回，这里只看写进哪份账套
        precheck = stubs["pearnly_erp_portal.express.precheck"]
        precheck.collect_document = lambda doctype, name: {"result": prepared[name], "items": [], "data": None}
        precheck.document_view = lambda doc_id, doctype, name, found=None: {"doc_id": doc_id}
        precheck.menu_item = lambda company, doctype, name: None  # 没停在定制菜单的子菜单（修法 D 见 test_erp_operator_menu_choice）
        precheck.dialog_item = lambda company, doctype, name: None  # 没停在中途框（1.12.23 ③）
        stubs["pearnly_erp_portal.express.api"]._options = lambda item, data: []
        stubs["pearnly_erp_portal.express.facts"].load = lambda key: None
        express = stubs["pearnly_erp_portal.express"]
        express.contracts, express.push = contracts, push
        express.api, express.facts = stubs["pearnly_erp_portal.express.api"], stubs["pearnly_erp_portal.express.facts"]
        express.precheck = precheck
        stubs["pearnly_erp_portal.masters"].company = stubs["pearnly_erp_portal.masters.company"]
        stubs["pearnly_core.cc_desktop"].jobs = stubs["pearnly_core.cc_desktop.jobs"]
        operator = _load("erp_operator_code_cc", ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/express/operator.py",
                         stubs)
        logged = []
        with mock.patch.dict(sys.modules, stubs), mock.patch.object(frappe.db, "get_value", lambda *a, **k: None), \
                mock.patch.object(frappe, "log_error", lambda title=None, message=None, **kw: logged.append(title), create=True):
            out = operator.precheck("CO-1", ["P:PUR-1", "P:PUR-2", "P:PUR-3", "X:bad"])
        by = {row["doc_id"]: row for row in out["documents"]}
        expected = {"account_set": "BK-69", "account_set_title": "ABC 69", "account_set_path": "S:\\69EXP\\ABC",
                    "connection": "", "connection_title": "", "writers": ["pc-1"]}
        self.assertEqual((by["P:PUR-1"]["state"], by["P:PUR-1"]["target"]), ("ready", expected))
        self.assertEqual((by["P:PUR-2"]["state"], by["P:PUR-2"]["target"]), ("choose", expected))
        self.assertNotIn("target", by["P:PUR-3"])  # 没选出账套
        # 认不得的编号种类是契约缺口：单独报码、记日志，不当成没有权限（11-16 H3「找不到就报没有权限」按类）
        self.assertEqual((by["X:bad"]["state"], by["X:bad"]["reason"]), ("blocked", contracts.UNKNOWN_DOCUMENT))
        self.assertEqual(logged, [contracts.UNKNOWN_DOCUMENT_LOG])
        self.assertIsNone(operator.book_target({"label": "no key"}))
        # 没有账套名写位置，没有位置就空着；账套键（book_key）是内部编号，不当名称（测试机1 11-13）
        self.assertEqual(operator.book_target({"key": "K", "path": "S:\\K"})["account_set_title"], "S:\\K")
        self.assertEqual(operator.book_target({"key": "K"})["account_set_title"], "")

    def test_cowork_takes_the_account_set_its_push_preparation_froze(self):
        """代录预检（`assistant.precheck_source.operator_check`）的账套与推送准备同一个 `_express_context`；每种结论都带上，挡下的（如票面税号与账套不符）也带。"""
        path = ROOT / "apps/pearnly_cowork/pearnly_cowork/push/application/service.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        found = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        # 代录预检在写账前置判定里（`assistant.precheck_source`）：取数、账套与挡单照旧走推送服务这几个函数
        self.assertNotIn("operator_check", found)
        checking = (ROOT / "apps/pearnly_cowork/pearnly_cowork/assistant/precheck_source.py").read_text(encoding="utf-8")
        parts = {node.name: ast.get_source_segment(checking, node) for node in ast.parse(checking).body
                 if isinstance(node, ast.FunctionDef)}
        self.assertIn('return document(history, posting_kind)["legacy"]', parts["operator_check"])
        check = parts["document"] + parts["_legacy"]
        self.assertIn('found = service._express_context(doc, loaded, doc.push_account_set or None)\n'
                      '        target = service._operator_target(', check)
        self.assertLess(check.index("target = service._operator_target("), check.index("_require_matching_account("))
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
        self.assertEqual((bare["account_set_title"], bare["account_set_path"], bare["connection_title"]), ("", "", ""))
        pathed = namespace["_operator_target"](doc, types.SimpleNamespace(doc=Row()), {
            "target": types.SimpleNamespace(account_set="K"), "account_row": {"label": "", "attributes": {"path": "S:\\K"}}})
        self.assertEqual(pathed["account_set_title"], "S:\\K")  # 没有名称写位置，不写账套键


class PendingMonthTests(unittest.TestCase):
    """B3：待处理按「公司 · 月份」，带本人这家这个月最近的对话。"""

    def setUp(self):
        STATE.update(queries=[], tables={})
        frappe.session.user = "a@example.com"
        self.engine = "claude"
        config = types.SimpleNamespace(engine_code=lambda: self.engine)
        package = types.ModuleType("pearnly_core.assistant")
        modules = {"pearnly_core.assistant": package}
        for name in ("ask", "config", "context", "contracts", "engine", "engines", "notices", "prefs", "projects", "relay",
                     "sidebar", "titles", "toolrun", "tools", "waits"):
            setattr(package, name, types.SimpleNamespace())
        package.config, package.contracts = config, assistant_contracts
        package.context = types.SimpleNamespace(SESSION="Pearnly Assistant Session")
        self.sessions = _load("assistant_sessions_code_cc", CORE / "assistant/sessions.py", modules)

    def test_counts_by_company_and_month(self):
        items = [{"company": "CO-1", "month": "2026-10"}, {"company": "CO-1", "month": "2026-10"},
                 {"company": "CO-1", "month": "2026-09"}, {"company": "CO-2", "month": "2026-10"},
                 {"company": None, "month": "2026-10"}, {"company": "CO-1", "month": "2026-1"}]
        self.assertEqual(assistant_contracts.pending_months(items), [
            {"company": "CO-1", "month": "2026-10", "count": 2, "parties": [], "files": []},
            {"company": "CO-1", "month": "2026-09", "count": 1, "parties": [], "files": []},
            {"company": "CO-2", "month": "2026-10", "count": 1, "parties": [], "files": []}])
        self.assertEqual(assistant_contracts.pending_months([]), [])

    def test_placeholder_title_sources(self):
        # 侧栏三处 11-07：占位行按内容起名（对方名称，张数多的在前，最多 3 个；没有对方时第一个文件名），不写死月份
        items = [{"company": "CO-1", "month": "2026-10", "counterparty": "Makro", "filename": "a.pdf"},
                 {"company": "CO-1", "month": "2026-10", "counterparty": " BJC  Big C ", "filename": "b.pdf"},
                 {"company": "CO-1", "month": "2026-10", "counterparty": "BJC Big C", "filename": ""},
                 {"company": "CO-1", "month": "2026-10", "counterparty": "Lotus"},
                 {"company": "CO-1", "month": "2026-10", "counterparty": "7-Eleven"},
                 {"company": "CO-2", "month": "2026-10", "counterparty": "", "filename": "scan 01.jpg"}]
        out = assistant_contracts.pending_months(items)
        self.assertEqual(out[0]["parties"], ["BJC Big C", "Makro", "Lotus"])
        self.assertEqual(out[0]["files"], ["a.pdf"])
        self.assertEqual((out[1]["parties"], out[1]["files"]), ([], ["scan 01.jpg"]))

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
            {"company": "CO-1", "month": "2026-10", "count": 1, "parties": [], "files": [], "conversation": "new"},
            {"company": "CO-1", "month": "2026-09", "count": 1, "parties": [], "files": [], "conversation": "sep"},
            {"company": "CO-2", "month": "2026-10", "count": 1, "parties": [], "files": [], "conversation": ""}])
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


class StartTakesTheWholeListTests(Base):
    """第 4 项（主会话 11-10）：开始卡的资料一页 50 张，点开始只交页面已加载的那几页，第 51 张起悄悄漏掉（发布窗口全量流程
    实验栈积到 51 张、只交 50）。开始由服务端按这家公司这个月的完整待录清单交：去掉人移到「不入账」的、结果不明还没标的、
    同一张票已写进 Express 的（人点了「再录一次」的照交），不看页面加载了几页。"""

    TOTAL = 120

    def setUp(self):
        super().setUp()
        self.submitted = []
        rows = [{"doc_id": f"D{n:03d}", "group": "skip" if n == 7 else "purchase"} for n in range(1, self.TOTAL + 1)]
        states = {"D010": {"state": "blocked", "reason": tasks.RESULT_UNKNOWN}, "D060": {"state": "written", "docnum": "RR1"},
                  "D061": {"state": "written", "docnum": "RR2"}}

        def sources(company, period, offset, limit):
            page = rows[offset:offset + limit]
            following = offset + len(page)
            return {"documents": [dict(row) for row in page], "next_offset": following if following < len(rows) else None,
                    "total": len(rows)}

        def submit(company, doc_ids, batch, computer, answers):
            self.submitted.append((company, list(doc_ids), dict(batch)))
            return [{"doc_id": doc_id, "status": "sent"} for doc_id in doc_ids]

        STATE["hooks"]["pearnly_express_operator"].update(sources="p.sources", submit="p.submit", precheck="p.precheck")
        STATE["attrs"].update({"p.sources": sources, "p.submit": submit,
                               "p.precheck": lambda company, ids: {"documents": [
                                   {"doc_id": doc_id, **states.get(doc_id, {"state": "ready"})} for doc_id in ids]}})
        self.task = tasks.create("CO-1", "2026-10")["name"]

    def test_every_document_waiting_this_month_is_handed_in_not_only_the_loaded_page(self):
        # 11-11 一句话下令：交哪些单由写入计划按 `waiting` 的完整清单定（D061 用户说了再录一次），开始时整份交给任务
        wanted = [f"D{n:03d}" for n in range(1, self.TOTAL + 1) if n not in (7, 10, 60)]
        planned, _groups, skipped = api.waiting("CO-1", "2026-10", ["D061"])
        self.assertEqual(planned, wanted)  # 第 51 张起也在
        self.assertEqual(sorted(item["doc_id"] for item in skipped), ["D007", "D010", "D060"])
        out = api.hand_in("CO-1", self.task, planned, again=["D061"])
        company, handed, batch = self.submitted[-1]
        self.assertEqual(handed, wanted)  # 第 51 张起也交了；D061 用户说了再录一次照交
        self.assertEqual(batch["total"], len(wanted))
        self.assertEqual(batch["again"], ["D061"])
        self.assertEqual(len(out["results"]), len(wanted))
        doc = frappe.get_doc("Pearnly Express Operator Task", self.task)
        self.assertEqual(len(doc.documents), len(wanted))

    def test_a_task_hands_in_only_documents_still_waiting_on_the_whole_list(self):
        # 计划里有、开始前已不在待录清单里的（期间被移到不入账等）不交
        api.hand_in("CO-1", self.task, ["D001", "D007", "D200"])
        self.assertEqual(self.submitted[-1][1], ["D001"])

    def test_an_expired_task_is_not_handed_in(self):
        # B04：已失效的代录任务（7 天没动作 / 新任务已含同一张票）交单服务端拒，一张都不交
        planned, _groups, _skipped = api.waiting("CO-1", "2026-10", [])
        with mock.patch.object(tasks, "expired", return_value=True):
            with self.assertRaises(ValidationError_):
                api.hand_in("CO-1", self.task, planned)
        self.assertEqual(self.submitted, [])

    def test_the_page_cannot_start_writing(self):
        # 用户 11-10 / 主会话批复②：开始只由用户在对话里一句话下令（写入计划），页面没有开始接口
        source = (ROOT / "apps/pearnly_core/pearnly_core/express_operator/api.py").read_text(encoding="utf-8")
        head = source[:source.index("\ndef start(")]
        self.assertFalse(head.rstrip().endswith(")") and "@frappe.whitelist" in head.rstrip().splitlines()[-1])
        self.assertIn("\ndef hand_in(", source)
        shared = (ROOT / "frontend/assistant/src/components/tool-uis/operator-shared.ts").read_text(encoding="utf-8")
        self.assertNotIn('API + "start"', shared)

    def test_nothing_waiting_is_refused_instead_of_starting_an_empty_task(self):
        STATE["attrs"]["p.sources"] = lambda company, period, offset, limit: {"documents": [], "next_offset": None}
        with self.assertRaises(ValidationError_):
            api.start("CO-1", ["D001"], task=self.task)
        self.assertEqual(self.submitted, [])



class ListedDocumentsStayListedTests(Base):
    """代录列出快照（主会话 11-24）：列出阶段不锁；列出过的单之后被直写写进账本，资料清单、给模型的准备中清单、写入计划都如实给出
    「已入账 · 单号」、开始时跳过，不从清单里凭空消失（L3 首跑：ERP 每分钟那一轮把排队中的单直写掉，代录清单时有时无）。"""

    def setUp(self):
        super().setUp()
        self.submitted = []
        self.rows = [{"doc_id": f"D{n}", "group": "purchase"} for n in range(1, 4)]
        self.state = {}

        def sources(company, period, offset, limit):
            page = self.rows[offset:offset + limit]
            following = offset + len(page)
            return {"documents": [dict(row) for row in page], "next_offset": following if following < len(self.rows) else None,
                    "total": len(self.rows)}

        def status(company, doc_ids):
            return [{"doc_id": doc_id, **({"ref": f"PO-{doc_id}", "party": "T001"} if doc_id in self.state else {}),
                     **self.state.get(doc_id, {"state": "queued"})} for doc_id in doc_ids]

        def submit(company, doc_ids, batch, computer, answers):
            self.submitted.append(list(doc_ids))
            return [{"doc_id": doc_id, "status": "sent"} for doc_id in doc_ids]

        STATE["hooks"]["pearnly_express_operator"].update(sources="p.sources", submit="p.submit", precheck="p.precheck",
                                                          status="p.status")
        STATE["attrs"].update({"p.sources": sources, "p.submit": submit, "p.status": status,
                               "p.precheck": lambda company, ids: {"documents": [{"doc_id": i, "state": "ready"} for i in ids]}})
        self.task = tasks.create("CO-1", "2026-10")["name"]

    def written_directly(self, doc_id, docnum):
        # 产品 sources 只给还没交进写账的单：直写写成后它从现查里消失，status 说已入账
        self.rows = [row for row in self.rows if row["doc_id"] != doc_id]
        self.state[doc_id] = {"state": "saved", "docnum": docnum}

    def test_a_listed_document_written_directly_shows_as_entered_with_its_number_and_is_skipped(self):
        planned, _groups, skipped = api.waiting("CO-1", "2026-10", [], task=self.task)
        self.assertEqual(planned, ["D1", "D2", "D3"])
        self.written_directly("D2", "RR0000101")
        planned, _groups, skipped = api.waiting("CO-1", "2026-10", [], task=self.task)
        self.assertEqual(planned, ["D1", "D3"])
        self.assertEqual(skipped, [{"doc_id": "D2", "reason": "already_entered", "docnum": "RR0000101"}])
        page = api.documents("CO-1", "2026-10", task=self.task)
        self.assertEqual([row["doc_id"] for row in page["documents"]], ["D1", "D3"])
        self.assertEqual(page["entered"], [{"doc_id": "D2", "group": "purchase", "ref": "PO-D2", "party": "T001",
                                            "docnum": "RR0000101", "entered": "RR0000101", "moves": []}])
        api.hand_in("CO-1", self.task, ["D1", "D2", "D3"])
        self.assertEqual(self.submitted[-1], ["D1", "D3"])  # 已被直写的不再交代录

    def test_the_model_sees_entered_documents_while_preparing(self):
        tools = importlib.import_module("pearnly_core.express_operator.tools")
        view = tasks.read(self.task)
        self.assertNotIn("entered", tools._preparing(view))
        self.written_directly("D3", "RR0000102")
        out = tools._preparing(view)
        self.assertEqual([row["doc_id"] for row in out["waiting"]], ["D1", "D2"])
        self.assertEqual(out["entered"], [{"doc_id": "D3", "ref": "PO-D3", "party": "T001", "docnum": "RR0000102"}])

    def test_gone_documents_that_are_not_entered_are_not_added_and_new_ones_still_come_in(self):
        api.documents("CO-1", "2026-10", task=self.task)
        self.rows = [row for row in self.rows if row["doc_id"] != "D1"] + [{"doc_id": "D9", "group": "sales"}]
        self.state["D1"] = {"state": "not_done", "docnum": ""}   # 没录成 / 取消 / 在别处排着：不当成已入账
        page = api.documents("CO-1", "2026-10", task=self.task)
        self.assertEqual([row["doc_id"] for row in page["documents"]], ["D2", "D3", "D9"])
        self.assertEqual(page.get("entered"), [])

    def test_only_the_first_page_carries_entered_and_without_a_task_nothing_is_remembered(self):
        api.documents("CO-1", "2026-10", task=self.task)
        self.written_directly("D1", "RR1")
        self.assertNotIn("entered", api.documents("CO-1", "2026-10", offset=1, task=self.task))
        self.assertNotIn("entered", api.documents("CO-1", "2026-10"))
        doc = frappe.get_doc("Pearnly Express Operator Task", self.task)
        self.assertEqual(json.loads(doc.get("listed")), {"D1": "purchase", "D2": "purchase", "D3": "purchase"})

    def test_documents_on_later_pages_are_not_read_back_one_by_one(self):
        # code-review 11-24：只拿一页比时，后几页的单会被当成「不在了」逐张读现状；先按全月清单核，只读真离开的
        self.rows = [{"doc_id": f"D{n}", "group": "purchase"} for n in range(1, 8)]
        api.documents("CO-1", "2026-10", limit=3, task=self.task)
        api.documents("CO-1", "2026-10", offset=3, limit=4, task=self.task)
        asked = []
        status = STATE["attrs"]["p.status"]
        STATE["attrs"]["p.status"] = lambda company, ids: asked.append(list(ids)) or status(company, ids)
        self.written_directly("D6", "RR6")
        page = api.documents("CO-1", "2026-10", limit=3, task=self.task)
        self.assertEqual(asked, [["D6"]])
        self.assertEqual([row["doc_id"] for row in page["entered"]], ["D6"])

    def test_changing_the_month_or_company_clears_what_was_listed(self):
        api.documents("CO-1", "2026-10", task=self.task)
        tasks.update(self.task, period="2026-11")
        self.assertFalse(frappe.get_doc("Pearnly Express Operator Task", self.task).get("listed"))
        self.written_directly("D1", "RR1")   # 十月那张被写掉：十一月的清单里不算「列出过」
        self.assertEqual(api.documents("CO-1", "2026-11", task=self.task).get("entered"), [])

    def test_a_started_task_no_longer_records_what_it_lists(self):
        api.hand_in("CO-1", self.task, ["D1", "D2", "D3"])
        doc = frappe.get_doc("Pearnly Express Operator Task", self.task)
        before = doc.get("listed")
        self.rows.append({"doc_id": "D4", "group": "purchase"})
        api.documents("CO-1", "2026-10", task=self.task)
        self.assertEqual(frappe.get_doc("Pearnly Express Operator Task", self.task).get("listed"), before)

if __name__ == "__main__":
    unittest.main()
