"""LINE 上要等结果的动作（``pearnly_core.line_ui.receipt``，用户 11-22）：先三个点，结果到了用这次的回复令牌回结果，等不到回兜底。

断言的是应有状态：结果先到 → 计时线程到点不回；计时先到 → 结果回不了（settle 回 False）；同一动作只回一次、令牌只用一次；
又点了一次（同一个键新一轮）旧计时器不抢新一轮的；叫醒只在有人在等、登记了接着回的函数时排任务；接着回的函数回 None 就继续等；
loading 秒数取 5 的倍数、不超过 60；记不下「待回」当场回兜底。计时器换成不睡的假计时器，不真等 20 秒。

守门（:class:`ProcessWordsGuard`）：ERP / Cowork 的 LINE 处理器拿到结果之前不回过程文字（录入中、正在读取…）——这些词条只许出现在
交给 ``line_receipt.start`` 的兜底消息里（登记的兜底函数与它们的调用方），或【查看结果】答此刻状态的地方。状态词表：网页
（`transactions.js`、`collections.js`）与 LINE 回执同一张（:class:`StatusWordTables`）。

替身 frappe（内存 Redis、记录排的任务）、替身 LINE 传输层，不连库、不出网。

    python3 tests/test_line_receipt.py
"""
import contextlib
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))


class Redis:
    """不淘汰 Redis 的替身：set / get / getdel（原子取走）/ sadd / smembers / expire。"""

    def __init__(self):
        self.values, self.sets, self.broken = {}, {}, False

    def make_key(self, key):
        return "site|" + key

    def set(self, key, value, ex=None, nx=False):
        if self.broken:
            raise ConnectionError("redis down")
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def getdel(self, key):
        return self.values.pop(key, None)

    def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    def smembers(self, key):
        return set(self.sets.get(key, ()))

    def expire(self, key, seconds):
        return True


REDIS = Redis()
ENQUEUED, ERRORS = [], []
frappe = types.ModuleType("frappe")
frappe.local = types.SimpleNamespace(lang="zh")
frappe.session = types.SimpleNamespace(user="a@x")
frappe.enqueue = lambda path, **kwargs: ENQUEUED.append((path, kwargs))
frappe.set_user = lambda user: setattr(frappe.session, "user", user)
frappe.get_attr = lambda path: RESUMERS[path]
frappe.db = types.SimpleNamespace(rollback=lambda: None)
frappe.log_error = lambda **kwargs: ERRORS.append(kwargs)
frappe.get_traceback = lambda *a, **k: "tb"
sys.modules["frappe"] = frappe


class Line(types.ModuleType):
    """`pearnly_core.line` 的记录器：回复、三个点、给计时线程用的回复都记下；同一张令牌回第二次算被拒。"""

    def __init__(self):
        super().__init__("pearnly_core.line")
        self.reset()

    def reset(self):
        self.sent, self.loading, self.used = [], [], set()

    def _send(self, token, messages):
        if token in self.used:
            return False  # LINE：用过的令牌被拒
        self.used.add(token)
        self.sent.append((token, messages))
        return True

    def reply(self, token, messages):
        return self._send(token, messages)

    def reply_sender(self, token):
        return lambda messages: self._send(token, messages)

    def start_loading(self, user_id, seconds=30):
        self.loading.append((user_id, seconds))
        return True


LINE = Line()
state = types.ModuleType("pearnly_core.execution.state")
state.store = lambda: REDIS
language = types.ModuleType("pearnly_core.execution.language")
language.use = lambda lang: contextlib.nullcontext(lang)
sys.modules.update({"pearnly_core.line": LINE, "pearnly_core.execution.state": state,
                    "pearnly_core.execution.language": language})
import pearnly_core  # noqa: E402
import pearnly_core.execution  # noqa: E402

pearnly_core.line = LINE
pearnly_core.execution.state, pearnly_core.execution.language = state, language
from pearnly_core.line_ui import receipt as line_receipt  # noqa: E402

RESUMERS = {}


class Timers:
    """不睡的计时器：start 只记下，测试里 ``fire()`` 才到点。"""

    def __init__(self):
        self.made = []

    def __call__(self, seconds, function):
        timer = types.SimpleNamespace(seconds=seconds, function=function, daemon=False, started=False)
        timer.start = lambda: setattr(timer, "started", True)
        self.made.append(timer)
        return timer

    def fire(self, index=-1):
        self.made[index].function()


FALLBACK = [{"type": "text", "text": "录入中"}]
RESULT = [{"type": "text", "text": "成功 · RE-1"}]


class LineReceiptTest(unittest.TestCase):
    def setUp(self):
        REDIS.values.clear()
        REDIS.sets.clear()
        REDIS.broken = False
        LINE.reset()
        ENQUEUED.clear()
        ERRORS.clear()
        RESUMERS.clear()
        self.timers = Timers()
        self.original = line_receipt.Timer
        line_receipt.Timer = self.timers
        self.addCleanup(setattr, line_receipt, "Timer", self.original)

    def test_the_result_first_means_the_timer_sends_nothing(self):
        self.assertTrue(line_receipt.start("erp:col:RC-1", "U1", "tok-1", FALLBACK))
        self.assertEqual(LINE.loading, [("U1", 20)])
        self.assertTrue(self.timers.made[0].daemon and self.timers.made[0].started)
        self.assertEqual(self.timers.made[0].seconds, line_receipt.WAIT_SECONDS)
        self.assertTrue(line_receipt.pending("erp:col:RC-1"))
        self.assertTrue(line_receipt.settle("erp:col:RC-1", RESULT))
        self.assertFalse(line_receipt.pending("erp:col:RC-1"))
        self.timers.fire()
        self.assertEqual(LINE.sent, [("tok-1", RESULT)])

    def test_the_timer_first_means_the_result_is_not_sent(self):
        line_receipt.start("erp:col:RC-1", "U1", "tok-1", FALLBACK)
        self.timers.fire()
        self.assertFalse(line_receipt.settle("erp:col:RC-1", RESULT))
        self.assertEqual(LINE.sent, [("tok-1", FALLBACK)])

    def test_only_one_answer_and_the_token_is_used_once(self):
        line_receipt.start("k", "U1", "tok-1", FALLBACK)
        self.assertTrue(line_receipt.settle("k", RESULT))
        self.assertFalse(line_receipt.settle("k", RESULT))
        self.timers.fire()
        self.assertEqual([token for token, _messages in LINE.sent], ["tok-1"])

    def test_a_new_round_on_the_same_key_is_not_taken_by_the_old_timer(self):
        line_receipt.start("k", "U1", "tok-1", FALLBACK)
        line_receipt.start("k", "U1", "tok-2", FALLBACK)
        self.timers.fire(0)  # 第一轮的计时器到点：不是它那一轮，不回、还给第二轮
        self.assertEqual(LINE.sent, [])
        self.assertTrue(line_receipt.pending("k"))
        self.assertTrue(line_receipt.settle("k", RESULT))
        self.assertEqual(LINE.sent, [("tok-2", RESULT)])

    def test_nothing_starts_without_a_token_or_a_key(self):
        self.assertFalse(line_receipt.start("k", "U1", "", FALLBACK))
        self.assertFalse(line_receipt.start("", "U1", "tok", FALLBACK))
        self.assertEqual((LINE.loading, self.timers.made), ([], []))

    def test_loading_seconds_are_steps_of_five_up_to_sixty(self):
        self.assertEqual([line_receipt.loading_seconds(value) for value in (0, 3, 20, 21, 55, 90)], [5, 5, 20, 25, 55, 60])

    def test_when_the_wait_cannot_be_noted_the_fallback_is_sent_at_once(self):
        REDIS.broken = True
        self.assertTrue(line_receipt.start("k", "U1", "tok-1", FALLBACK))
        self.assertEqual(LINE.sent, [("tok-1", FALLBACK)])
        self.assertEqual(self.timers.made, [])

    def test_wake_queues_a_resume_only_while_someone_waits_with_a_resumer(self):
        self.assertFalse(line_receipt.wake("k"))
        line_receipt.start("k", "U1", "tok-1", FALLBACK)
        self.assertFalse(line_receipt.wake("k"))  # 没登记接着回的函数：等兜底
        line_receipt.start("k2", "U1", "tok-2", FALLBACK, resume="product.resume", args={"name": "RC-1"},
                           topics=["cc-request:erp_masters:B1"])
        self.assertTrue(line_receipt.wake("k2"))
        self.assertEqual(ENQUEUED, [("pearnly_core.line_ui.receipt.resume",
                                     {"queue": "short", "enqueue_after_commit": True, "key": "k2"})])
        self.assertEqual(line_receipt.wake_topic("cc-request:erp_masters:B1"), 1)
        self.assertEqual(line_receipt.wake_topic("cc-request:other"), 0)

    def test_resume_runs_as_the_sender_and_settles_only_when_there_is_an_answer(self):
        seen = []
        answers = [None, RESULT]

        def resumer(name):
            seen.append((name, frappe.session.user))
            return answers.pop(0)

        RESUMERS["product.resume"] = resumer
        frappe.session.user = "a@x"
        line_receipt.start("k", "U1", "tok-1", FALLBACK, resume="product.resume", args={"name": "RC-1"})
        frappe.session.user = "Administrator"  # 回执在电脑的请求 / 后台任务里
        line_receipt.resume("k")  # 还没好：不回，接着等
        self.assertEqual(LINE.sent, [])
        self.assertTrue(line_receipt.pending("k"))
        line_receipt.resume("k")
        self.assertEqual(LINE.sent, [("tok-1", RESULT)])
        self.assertEqual(seen, [("RC-1", "a@x"), ("RC-1", "a@x")])
        self.assertEqual(frappe.session.user, "Administrator")  # 做完还原
        self.timers.fire()
        self.assertEqual(len(LINE.sent), 1)

    def test_a_failing_resumer_is_logged_and_leaves_the_fallback(self):
        RESUMERS["product.resume"] = lambda **kwargs: 1 / 0
        line_receipt.start("k", "U1", "tok-1", FALLBACK, resume="product.resume")
        line_receipt.resume("k")
        self.assertEqual(len(ERRORS), 1)
        self.timers.fire()
        self.assertEqual(LINE.sent, [("tok-1", FALLBACK)])

    def test_the_wait_record_carries_the_language_and_the_sender(self):
        frappe.local.lang, frappe.session.user = "th", "b@x"
        line_receipt.start("k", "U1", "tok-1", FALLBACK, resume="product.resume")
        found = json.loads(REDIS.values["site|pearnly:line:receipt:k"])
        self.assertEqual((found["lang"], found["user"], found["to"]), ("th", "b@x", "U1"))


import ast  # noqa: E402
import re  # noqa: E402

sys.path.insert(0, str(ROOT / "apps/pearnly_erp_portal"))
ERP = "apps/pearnly_erp_portal/pearnly_erp_portal/integrations/"
COWORK = "apps/pearnly_cowork/pearnly_cowork/line/"
# 拿到结果之前不许回的过程词条（评估 2026-11-22 第 13 栏守门）
PROCESS = {"Entering", "Reading from Express", "Reading from MR.ERP", "Recorded · pending write",
           "Reading the company list from the ERP. Tap the menu again in a moment.", "cowork.masters.reading"}
# 过程词条只许写在这些兜底函数里（文件 → 函数）
BUILDERS = {ERP + "line_cards.py": {"entering"}, ERP + "line_collection.py": {"_result_messages"},
            COWORK + "save.py": {"_entering"}, COWORK + "flow.py": {"_wait_accounts"}}
# 兜底函数（与「正在读取」那句的取法）只许这些函数用：把它交给 line_receipt.start 的，或【查看结果】答此刻状态的
CALLERS = {"entering": {"result", "_await"},  # result：只在 final（再点保存 / 查看结果）时回录入中
           "_result_messages": {"_release", "resume_release", "_show_result"},
           "_entering": {"reply", "show"}, "_wait_accounts": {"_ask_account"},
           "_wait_read": {"_pick_customer", "_answer_invoices_done"},
           "reading_text": {"_pick_customer"}}
# 这些函数必须真的交给 line_receipt.start（兜底只在等不到时发）
WAITERS = {ERP + "handlers.py": {"_await"}, ERP + "line_collection.py": {"_release", "_wait_read"},
           COWORK + "save.py": {"reply"}, COWORK + "flow.py": {"_wait_accounts"}}


def _line_files():
    return sorted([*(ROOT / ERP).glob("line_*.py"), ROOT / ERP / "handlers.py", *(ROOT / COWORK).glob("*.py")])


def _functions(tree):
    """每个函数（含嵌套）→ 它自己那一层用到的字面量、名字、属性名、调用的 line_receipt.start。"""
    out = {}

    def visit(node, owner):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(child.name, {"strings": set(), "names": set(), "starts": False})
                visit(child, child.name)
                continue
            if owner:
                info = out[owner]
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    info["strings"].add(child.value)
                elif isinstance(child, ast.Name):
                    info["names"].add(child.id)
                elif isinstance(child, ast.Attribute):
                    info["names"].add(child.attr)
                    if child.attr == "start" and isinstance(child.value, ast.Name) and child.value.id == "line_receipt":
                        info["starts"] = True
            visit(child, owner)

    visit(tree, None)
    return out


class ProcessWordsGuard(unittest.TestCase):
    def test_process_words_are_only_in_the_registered_fallbacks(self):
        for path in _line_files():
            relative = str(path.relative_to(ROOT))
            for name, info in _functions(ast.parse(path.read_text(encoding="utf-8"))).items():
                for word in info["strings"] & PROCESS:
                    with self.subTest(file=relative, function=name, word=word):
                        self.assertIn(name, BUILDERS.get(relative, set()),
                                      "拿到结果之前不回过程文字：放进交给 line_receipt.start 的兜底消息（并登记在 BUILDERS）")

    def test_the_fallbacks_are_used_only_by_waiters_and_status_answers(self):
        for path in _line_files():
            relative = str(path.relative_to(ROOT))
            for name, info in _functions(ast.parse(path.read_text(encoding="utf-8"))).items():
                for used in info["names"] & set(CALLERS):
                    if used == name:
                        continue
                    with self.subTest(file=relative, function=name, uses=used):
                        self.assertIn(name, CALLERS[used], "兜底只许交给 line_receipt.start，或在【查看结果】里答此刻状态")

    def test_the_waiters_hand_the_fallback_to_line_receipt(self):
        for relative, names in WAITERS.items():
            found = _functions(ast.parse((ROOT / relative).read_text(encoding="utf-8")))
            for name in names:
                with self.subTest(file=relative, function=name):
                    self.assertTrue(found[name]["starts"], "要等结果的入口经 line_receipt.start 发三个点、记待回、起计时")

    def test_the_guard_sees_a_process_word_replied_at_once(self):
        """守门自己的反例：webhook 里当场回「录入中」会被认出来。"""
        bad = 'def _save(token):\n    line.reply(token, [{"type": "text", "text": _("Entering")}])\n'
        found = _functions(ast.parse(bad))
        self.assertEqual(found["_save"]["strings"] & PROCESS, {"Entering"})
        self.assertNotIn("_save", set().union(*BUILDERS.values()))
        self.assertFalse(found["_save"]["starts"])


class StatusWordTables(unittest.TestCase):
    """状态词（用户 11-22：录入中 / 成功 / 失败 / 已取消）网页与 LINE 同一张表。"""

    @staticmethod
    def _js_table(path, name):
        page = (ROOT / path).read_text(encoding="utf-8")
        table = page.split(f"const {name} = {{", 1)[1].split("};", 1)[0]
        return dict(re.findall(r'(\w+):\s*"([^"]+)"', table))

    def test_documents_use_the_same_words_on_the_web_and_in_line(self):
        from pearnly_erp_portal.express import contracts

        self.assertEqual(self._js_table("apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/transactions.js", "LEDGER_WORDS"),
                         contracts.LEDGER_WORDS)
        self.assertEqual({contracts.ledger_word(state) for state in ("written", "handled")}, {"Succeeded"})
        self.assertEqual({contracts.ledger_word(state) for state in ("held", "failed", "mismatch", "detached")}, {"Failed"})
        self.assertEqual(contracts.ledger_word("queued"), "Entering")
        # LINE 等结果：排队中、停在每分钟会自己再试的原因上都还在录入；其余停下、失败、写成都是结论
        self.assertFalse(contracts.ledger_settled("queued", ""))
        self.assertFalse(contracts.ledger_settled("held", contracts.NO_WRITER))
        self.assertTrue(contracts.ledger_settled("held", "choice_needed:party"))
        self.assertTrue(contracts.ledger_settled("written", ""))
        self.assertTrue(contracts.ledger_settled("mismatch", ""))

    def test_collections_use_the_same_words_on_the_web_and_in_line(self):
        from pearnly_erp_portal.collection import contracts

        self.assertEqual(self._js_table("apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/collections.js", "STATUS_WORDS"),
                         contracts.STATUS_WORDS)
        self.assertEqual([contracts.status_word(status) for status in ("queued", "writing", "written", "failed", "withdrawn")],
                         ["Entering", "Entering", "Succeeded", "Failed", "Cancelled"])

    def test_the_line_cards_use_the_web_words(self):
        """ERP 采购 / 销售列表的 Express 列、收银记录、出入库都走 `p.ledger_word`，不再各写一张。"""
        for name in ("purchase-fields.js", "sales-fields.js", "pos-receipts.js", "stock-entry.js"):
            page = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/public/desk" / name).read_text(encoding="utf-8")
            with self.subTest(page=name):
                self.assertIn("p.ledger_word(", page)
                self.assertNotIn('"express.state.written"', page)


if __name__ == "__main__":
    unittest.main()
