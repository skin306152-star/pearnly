"""CC 对话驱动（scripts/acceptance/cc_driver.py）离线单测：站点读回的会话事件拼成对话记录（用户的话去掉附件说明、回复、
执行了的工具、提问卡 / 允许卡 / 写入计划及其回答、喊停与「卡片挂着时打字」的区别），提问卡作答的拼法，工厂两个分支，
没有实验栈、没占锁时开对话记「没测到」而不是报错。不连实验栈、不起容器。

样例事件的形状照源码写（不是凭印象）：
- 引擎事件：scripts/flows/lab_agents.py（publish / set_status / _turns / receive：user.message、session.status_running、
  agent.message、agent.custom_tool_use、session.status_idle 带 stop_reason requires_action、user.custom_tool_result、user.interrupt；
  编号 sevt_011LAB…、processed_at 是 %Y-%m-%dT%H:%M:%S.%fZ）；
- 本站事件：apps/pearnly_core/pearnly_core/assistant/contracts.py 的 tool_run_event（pearnly.tool_run，编号 ptoolrun_<调用>_<状态>，
  时间精确到秒）、answer_event（pearnly.answer）；write_plan.py 的 event（pearnly.write_plan，pplan_<计划>_<状态>，毫秒）；
  stopped.py 的 event（pearnly.stopped）；sessions.send 的「卡片挂着时打字」（先 user.interrupt 再 user.message）；
- 提问卡交回模型的结果：ask.py 的 result（{"answers": [{header, question, labels, other?, skipped?}]}），前面带
  contracts.reply_language 加的 reply_language；
- 附件说明：frontend/assistant/src/lib/managed-agents/events.ts 的 attachmentNote。
"""
import importlib.util
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


load("acc_errors", "scripts/acceptance/acc_errors.py")
lines = load("cc_lines", "scripts/acceptance/cc_lines.py")
driver = load("cc_driver", "scripts/acceptance/cc_driver.py")


def at(second, micro=0):
    return f"2026-11-12T10:00:{second:02d}.{micro:06d}Z"


def run_event(use_id, status, second):
    return {"type": "pearnly.tool_run", "id": f"ptoolrun_{use_id}_{status}", "tool_use_id": use_id, "status": status,
            "processed_at": f"2026-11-12T10:00:{second:02d}Z"}


def plan_event(plan, status, second):
    return {"id": f"pplan_{plan}_{status}", "type": "pearnly.write_plan", "plan": plan, "status": status, "count": 1,
            "processed_at": f"2026-11-12T10:00:{second:02d}.000Z"}


def text(kind, ident, second, words, micro=0):
    return {"id": ident, "type": kind, "content": [{"type": "text", "text": words}], "processed_at": at(second, micro)}


ASK_INPUT = {"questions": [{"question": "写进哪份账套？", "header": "账套", "options": [{"label": "T.8"}, {"label": "T.9"}]},
                           {"question": "现购还是赊购？", "header": "付款", "options": [{"label": "赊购"}, {"label": "现购"}]}]}
ANSWERED = json.dumps({"reply_language": "Chinese", "answers": [
    {"header": "账套", "question": "写进哪份账套？", "labels": ["T.9"]},
    {"header": "付款", "question": "现购还是赊购？", "labels": ["赊购"]}]}, ensure_ascii=False)
NOTE = "\n\n[Attached file, mounted read-only in the sandbox:\n- 进货单.pdf → /mnt/session/uploads/upl_1a2b/进货单.pdf]"


def conversation():
    """一段对话按时间的事件（增量读了几次攒起来的样子：本站执行记录、写入计划的中间状态都在）。"""
    return [
        text("user.message", "sevt_u1", 1, "录一下" + NOTE),
        {"id": "sevt_sys1", "type": "system.message", "content": [{"type": "text", "text": "Reply in Chinese."}], "processed_at": at(1, 10)},
        {"id": "sevt_r1", "type": "session.status_running", "processed_at": at(1, 20)},
        text("agent.message", "sevt_a1", 2, "[lab-agent] 录一下"),
        # 提问卡：调 ask_user → 停在 requires_action → 本站登记等作答 → 用户点了 T.9 → 结果交回 → 模型接着说
        {"id": "sevt_ask", "type": "agent.custom_tool_use", "name": "ask_user", "input": ASK_INPUT, "processed_at": at(3, 100)},
        {"id": "sevt_i1", "type": "session.status_idle", "stop_reason": {"type": "requires_action", "event_ids": ["sevt_ask"]},
         "processed_at": at(3, 200)},
        run_event("sevt_ask", "awaiting_approval", 3),
        {"id": "sevt_res", "type": "user.custom_tool_result", "custom_tool_use_id": "sevt_ask",
         "content": [{"type": "text", "text": ANSWERED}], "is_error": False, "processed_at": at(6, 0)},
        run_event("sevt_ask", "done", 6),
        text("agent.message", "sevt_a2", 7, "[lab-agent] ask_user " + ANSWERED),
        # 要「允许」的工具：等允许 → 点了允许 → 执行完（执行时刻取点允许那一刻）
        {"id": "sevt_note", "type": "agent.custom_tool_use", "name": "cowork_company_note_save", "input": {"text": "x"},
         "processed_at": at(8, 500)},
        run_event("sevt_note", "awaiting_approval", 8),
        run_event("sevt_note", "running", 11),
        run_event("sevt_note", "done", 12),
        # 被拒绝的一次：不算执行
        {"id": "sevt_del", "type": "agent.custom_tool_use", "name": "cowork_delete_documents", "input": {}, "processed_at": at(13, 0)},
        run_event("sevt_del", "awaiting_approval", 13),
        run_event("sevt_del", "denied", 14),
        # 不要「允许」的工具：建写入计划
        {"id": "sevt_plan", "type": "agent.custom_tool_use", "name": "pearnly_write_plan", "input": {"documents": ["D1"]},
         "processed_at": at(15, 0)},
        run_event("sevt_plan", "running", 15),
        run_event("sevt_plan", "done", 16),
        plan_event("WP-1", "awaiting", 16),
        text("user.message", "sevt_u2", 20, "写入"),
        {"id": "sevt_start", "type": "agent.custom_tool_use", "name": "pearnly_write_start", "input": {"plan": "WP-1"},
         "processed_at": at(21, 0)},
        run_event("sevt_start", "done", 22),
        plan_event("WP-1", "started", 22),
        # 喊停：停止键 → user.interrupt；被停掉的半段回复
        text("user.message", "sevt_u3", 30, "盘点一下 [lab-cut]"),
        {"id": "sevt_stop", "type": "user.interrupt", "processed_at": at(32, 0)},
        {"id": "pstop_ABC", "type": "pearnly.stopped", "event_id": "sevt_half", "content": [{"type": "text", "text": "[lab-agent] [lab-cut-half] 先盘点"}],
         "processed_at": "2026-11-12T10:00:33Z"},
        # 提问卡挂着时用户直接打字：站点先停那一轮（user.interrupt）、卡片作废，再送这句——不是喊停，卡片没回答
        {"id": "sevt_ask2", "type": "agent.custom_tool_use", "name": "ask_user", "input": ASK_INPUT, "processed_at": at(40, 0)},
        run_event("sevt_ask2", "awaiting_approval", 40),
        {"id": "sevt_typed", "type": "user.interrupt", "processed_at": at(45, 0)},
        text("user.message", "sevt_u4", 45, "改写进别家", micro=400000),
        run_event("sevt_ask2", "cancelled", 45),
        text("agent.message", "sevt_a4", 46, "[lab-agent] 改写进别家"),
    ]


class RecordFromEventsTest(unittest.TestCase):
    def setUp(self):
        events = conversation()
        # 增量读回有重复、顺序不保证：倒过来再拼一遍重复的
        self.record = driver.record_from_events(list(reversed(events)) + events[:5])

    def test_turns_are_user_and_assistant_words_in_order_without_the_attachment_note(self):
        turns = self.record.turns
        self.assertEqual([(t["role"], t["text"]) for t in turns], [
            ("user", "录一下"), ("assistant", "[lab-agent] 录一下"), ("assistant", "[lab-agent] ask_user " + ANSWERED),
            ("user", "写入"), ("user", "盘点一下 [lab-cut]"), ("assistant", "[lab-agent] [lab-cut-half] 先盘点"),
            ("user", "改写进别家"), ("assistant", "[lab-agent] 改写进别家")])
        self.assertEqual(turns[0]["files"], ["进货单.pdf"])
        self.assertTrue(turns[5]["stopped"])
        self.assertEqual(turns[0]["at"], driver.epoch(at(1)))

    def test_tools_are_what_actually_ran(self):
        names = [(t["name"], t["status"]) for t in self.record.tools]
        self.assertEqual(names, [("ask_user", "done"), ("cowork_company_note_save", "done"), ("pearnly_write_plan", "done"),
                                 ("pearnly_write_start", "done"), ("ask_user", "cancelled")])
        note = next(t for t in self.record.tools if t["name"] == "cowork_company_note_save")
        self.assertEqual(note["at"], driver.epoch("2026-11-12T10:00:11Z"))   # 点允许那一刻执行，不是调用那一刻
        self.assertNotIn("cowork_delete_documents", [t["name"] for t in self.record.tools])

    def test_cards_ask_allow_plan_with_answers(self):
        cards = [(c["kind"], c["topic"], c["answer"]) for c in self.record.cards]
        self.assertEqual(cards, [("ask", "账套", "T.9"), ("allow", "cowork_company_note_save", "allow"),
                                 ("allow", "cowork_delete_documents", "deny"), ("plan", "WP-1", "write"), ("ask", "账套", None)])
        ask = self.record.cards[0]
        self.assertEqual(ask["answered_at"], driver.epoch(at(6)))
        self.assertEqual([row["labels"] for row in ask["answers"]], [["T.9"], ["赊购"]])
        plan = self.record.cards[3]
        self.assertEqual((plan["at"], plan["answered_at"]), (driver.epoch(at(16)), driver.epoch(at(22))))
        self.assertIsNone(self.record.cards[4]["answered_at"])
        self.assertEqual(self.record.writes, [])

    def test_stop_button_counts_typing_over_a_card_does_not(self):
        self.assertEqual(self.record.stops, [driver.epoch(at(32))])

    def test_the_bottom_lines_read_the_record(self):
        record = self.record
        self.assertEqual(lines.b4_forbidden_needs_card(record, ["cowork_company_note_save", "cowork_delete_documents"]), [])
        self.assertEqual(lines.questions(record, "账套"), 2)
        record.writes = [{"at": driver.epoch(at(25)), "docnum": "RR0000100", "ledger": "TEST2"}]
        self.assertEqual(lines.b1_consent_before_write(record), [])
        record.writes = [{"at": driver.epoch(at(18)), "docnum": "RR0000100", "ledger": "TEST2"}]   # 点头之前就写了
        self.assertEqual(len(lines.b1_consent_before_write(record)), 1)
        record.writes = [{"at": driver.epoch(at(35)), "docnum": "RR0000101", "ledger": "TEST2"}]   # 喊停之后还写
        self.assertEqual(lines.b6_stop_and_duplicates(record), ["喊停之后还写了 RR0000101"])

    def test_engine_side_confirmation_and_realtime_answer_are_cards_too(self):
        events = [{"id": "sevt_b", "type": "agent.tool_use", "name": "bash", "input": {}, "processed_at": at(1)},
                  {"id": "sevt_c", "type": "user.tool_confirmation", "tool_use_id": "sevt_b", "result": "deny", "processed_at": at(2)},
                  {"id": "sevt_g", "type": "agent.custom_tool_use", "name": "cowork_company_note_save", "input": {}, "processed_at": at(3)},
                  {"type": "pearnly.answer", "id": "panswer_sevt_g", "tool_use_id": "sevt_g", "result": "allow", "processed_at": at(4)}]
        record = driver.record_from_events(events)
        self.assertEqual([(c["topic"], c["answer"]) for c in record.cards], [("bash", "deny"), ("cowork_company_note_save", "allow")])
        self.assertEqual([t["name"] for t in record.tools], ["cowork_company_note_save"])
        self.assertEqual(driver.record_from_events([]).turns, [])


class AnswersTest(unittest.TestCase):
    def test_choice_maps_to_the_answers_the_card_sends(self):
        questions = ASK_INPUT["questions"]
        self.assertEqual(driver.ask_answers(questions, "T.9"), [{"labels": ["T.9"]}, {"labels": ["赊购"]}])
        self.assertEqual(driver.ask_answers(questions, 2), [{"labels": ["T.9"]}, {"labels": ["赊购"]}])
        self.assertEqual(driver.ask_answers(questions, ["T.8", "现购"]), [{"labels": ["T.8"]}, {"labels": ["现购"]}])
        self.assertEqual(driver.ask_answers(questions, "明天再说"), [{"labels": [], "other": "明天再说"}, {"labels": ["赊购"]}])
        self.assertEqual(driver.ask_answers(questions, [None, None]), [{"skipped": True}, {"skipped": True}])


def fake_docker(folder, running="true", base="http://agents:18780"):
    """替身 docker：只答 inspect（容器在不在跑、环境变量）与 port，别的一律失败。"""
    path = pathlib.Path(folder) / "docker"
    path.write_text("#!/bin/sh\ncase \"$*\" in\n"
                    f"  *State.Running*) echo {running};;\n"
                    f"  *Config.Env*) echo ANTHROPIC_BASE_URL={base};;\n"
                    "  port*) echo 127.0.0.1:9;;\n"
                    "  *) exit 1;;\nesac\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class FactoryTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.env = {key: os.environ.get(key) for key in ("PEARNLY_ACC_DOCKER", "PEARNLY_LOCK_DIR", "PEARNLY_FLOWS_LAB",
                                                         "PEARNLY_ACC_REAL_BASE", "PEARNLY_ACC_REAL_SITE", "PEARNLY_ACC_REAL_USER",
                                                         "PEARNLY_ACC_REAL_TARGET")}
        for key in list(self.env)[3:]:
            os.environ.pop(key, None)
        os.environ["PEARNLY_LOCK_DIR"] = self.folder.name + "/locks"
        os.environ["PEARNLY_FLOWS_LAB"] = "acc-test-lab"

    def tearDown(self):
        for key, value in self.env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.folder.cleanup()

    def test_real_is_not_wired_and_unknown_is_a_mistake(self):
        with self.assertRaises(sys.modules["acc_errors"].Unmet) as caught:
            driver.open_conversation("real", "cowork", "direct")
        self.assertIn("真模型驱动没配", str(caught.exception))
        with self.assertRaises(sys.modules["acc_errors"].Unmet) as caught:
            driver.open_conversation("real", "cowork", "direct", base="https://cowork.example", user="u@x", where="production")
        self.assertIn("只连实验栈", str(caught.exception))
        with self.assertRaises(sys.modules["acc_errors"].Unmet) as caught:
            driver.open_conversation("real", "cowork", "operator", base="https://cowork.example", user="u@x")
        self.assertIn("直写", str(caught.exception))
        with self.assertRaises(ValueError):
            driver.open_conversation("lab", "cowork", "direct")

    def test_evidence_hands_over_raw_events_and_keeps_the_conversation(self):
        """标准 §5.3：不通过时留的是站点读回的会话事件原文；对话留在站点上不删；这时站点读不到也把已攒的交出去。"""
        talk = object.__new__(driver.LabConversation)
        talk.site, talk.session_id, talk.keep, talk.events = "cowork-new.regression.localhost", "S-1", False, [{"id": "e1", "type": "user.message"}]
        talk.poll = lambda: talk.events.append({"id": "e2", "type": "agent.message"})
        kept = talk.evidence()
        self.assertEqual((kept["session_id"], [event["id"] for event in kept["events"]], talk.keep), ("S-1", ["e1", "e2"], True))

        def down():
            raise sys.modules["acc_errors"].Infra("站点连不上")
        talk.poll = down
        self.assertEqual((len(talk.evidence()["events"]), "连不上" in talk.evidence()["poll_error"]), (2, True))

    def test_real_talks_to_the_site_api_and_prices_model_usage(self):
        """真模型那一层：同一套站点接口（建对话、发一句、读事件、删对话），Desk 会话经站点容器里的脚本建与注销，
        费用 = 会话事件里的模型用量折算。这里的站点是本进程里起的假站点，只验驱动的调用与计费，不验模型。"""
        import http.server
        import threading
        import urllib.parse

        seen, sessions = [], []
        usage = {"type": "span.model_request_end", "id": "sevt_u1", "processed_at": "2026-11-13T02:00:02.000000Z",
                 "model_usage": {"input_tokens": 1000, "output_tokens": 200, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 4000}}
        feed = [{"type": "user.message", "id": "sevt_1", "processed_at": "2026-11-13T02:00:00.000000Z", "content": [{"type": "text", "text": "录一下"}]},
                usage,
                {"type": "agent.message", "id": "sevt_2", "processed_at": "2026-11-13T02:00:03.000000Z", "content": [{"type": "text", "text": "要录哪张？"}]}]

        class Site(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, message):
                body = json.dumps({"message": message}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                name = urllib.parse.urlsplit(self.path).path.rsplit(".", 1)[-1].rsplit("/", 1)[-1]
                seen.append(("GET", name, self.headers.get("Host"), self.headers.get("Cookie")))
                self.reply({"events": feed, "run": {"status": "idle", "parked": []}} if name == "events" else "pong")

            def do_POST(self):
                name = self.path.rsplit(".", 1)[-1]
                fields = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()))
                seen.append(("POST", name, self.headers.get("Host"), fields))
                self.reply({"id": "S-9"} if name == "create" else {})

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)

        class Remote:
            def run(self, product, script, *args, timeout=0):
                sessions.append((product, script.name, args))
                return "DROPPED" if "drop" in args else "SID sid-real-1\n"

        talk = driver.open_conversation("real", "cowork", "direct", base=f"http://127.0.0.1:{server.server_address[1]}",
                                        site="testcowork.lab", user="clerk@test", where="lab", remote=Remote())
        talk.say("录一下")
        talk.wait_idle(5)
        record = talk.record()
        self.assertEqual((talk.session_id, [turn["text"] for turn in record.turns]), ("S-9", ["录一下", "要录哪张？"]))
        self.assertEqual(talk.cost(), round((1000 * 5.0 + 200 * 25.0 + 4000 * 0.5) / 1_000_000, 4))
        talk.close()
        posts = [(name, fields) for method, name, _, fields in seen if method == "POST"]
        self.assertEqual([name for name, _ in posts], ["create", "send", "delete"])
        self.assertEqual(posts[1][1], {"session_id": "S-9", "text": "录一下"})
        self.assertEqual({host for _, _, host, _ in seen}, {"testcowork.lab"})
        self.assertIn(("GET", "events", "testcowork.lab", "sid=sid-real-1"), seen)
        self.assertEqual(sessions, [("cowork", "session.py", ("testcowork.lab", "clerk@test")),
                                    ("cowork", "session.py", ("testcowork.lab", "clerk@test", "drop", "sid-real-1"))])

    def test_lab_fake_without_a_lab_is_not_run_not_an_error(self):
        os.environ["PEARNLY_ACC_DOCKER"] = self.folder.name + "/no-docker"
        with self.assertRaises(sys.modules["acc_errors"].Unmet) as caught:
            driver.open_conversation("lab-fake", "cowork", "direct")
        self.assertIn("docker", str(caught.exception))
        os.environ["PEARNLY_ACC_DOCKER"] = fake_docker(self.folder.name, running="false")
        with self.assertRaises(sys.modules["acc_errors"].Unmet) as caught:
            driver.open_conversation("lab-fake", "cowork", "direct")
        self.assertIn("没在跑", str(caught.exception))
        with self.assertRaises(sys.modules["acc_errors"].Unmet) as caught:
            driver.open_conversation("lab-fake", "cowork", "operator")
        self.assertIn("直写", str(caught.exception))

    def test_lab_fake_refuses_a_real_model_and_an_unheld_or_busy_lab(self):
        os.environ["PEARNLY_ACC_DOCKER"] = fake_docker(self.folder.name, base="https://api.anthropic.com")
        with self.assertRaises(sys.modules["acc_errors"].Unmet) as caught:
            driver.open_conversation("lab-fake", "cowork", "direct")
        self.assertIn("不是假智能体", str(caught.exception))
        os.environ["PEARNLY_ACC_DOCKER"] = fake_docker(self.folder.name)
        with self.assertRaises(sys.modules["acc_errors"].Unmet) as caught:
            driver.open_conversation("lab-fake", "cowork", "direct")
        self.assertIn("没占住 lab:acc-test-lab", str(caught.exception))
        locks = load("acc_test_locks", "scripts/acceptance/locks.py")
        locks.acquire("lab:acc-test-lab", "someone-else", "流程全套", 5)
        with self.assertRaises(sys.modules["acc_errors"].Unmet) as caught:
            driver.open_conversation("lab-fake", "cowork", "direct")
        self.assertIn("被占用", str(caught.exception))

    def test_talk_scenarios_with_lab_fake_report_not_run_when_there_is_no_lab(self):
        runner = load("acceptance_run_for_driver", "scripts/acceptance/run.py")
        os.environ["PEARNLY_ACC_DOCKER"] = self.folder.name + "/no-docker"
        for module, extra in (("cc_talk.py", {"case": "d03.double-write", "cell": "cowork-direct", "lang": "th"}), ("cowork_cc_direct.py", {})):
            for target, words in (("lab-fake", "没有 docker"), (None, "真模型驱动没配")):
                scenario = {"id": "acc.draft", "module": f"scripts/acceptance/scenarios/{module}", "resources": [], "budget_usd": 1,
                            **extra, **({"driver": target} if target else {})}
                result = runner.run_one(scenario, pathlib.Path(self.folder.name) / "run", "win-test", False)
                self.assertEqual(result["outcome"], runner.NOT_RUN, (module, target))
                self.assertIn(words, result["detail"])


if __name__ == "__main__":
    unittest.main()
