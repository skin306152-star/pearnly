"""回归实验室的假智能体后端（scripts/flows/lab_agents.py）为流程 `cc.ask` 加的那一种（主会话 11-05）：
用户原文里有 ``[lab-ask]{…}[/lab-ask]`` 时照真实引擎发 ``agent.custom_tool_use``（ask_user，入参是中间的 JSON）并停在
requires_action；收到这一条的 ``user.custom_tool_result`` 后接着回一条带结果原文的话、再 idle。普通的话照旧回前缀 + 前 40 字；
别的工具结果只存不回。

    python3 tests/test_cc_lab_agents_ask.py
"""
import importlib.util
import json
import time
import unittest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lab_agents", ROOT / "scripts/flows/lab_agents.py")
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)
lab.OPTIONS["delay"] = 0.01


def wait_for(state, check, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with lab.LOCK:
            if check(list(state["_events"])):
                return list(state["_events"])
        time.sleep(0.01)
    raise AssertionError([e["type"] for e in state["_events"]])


def session():
    return lab.create_session({"agent": "agent_011LABtest", "environment_id": "env_011LABtest"})


def user_message(text):
    return {"type": "user.message", "content": [{"type": "text", "text": text}]}


QUESTIONS = {"questions": [{"question": "Which book?", "header": "Book", "multiSelect": False,
                            "options": [{"label": "A (Recommended)", "description": "used"}, {"label": "B", "description": ""}]}]}


class AskUserScript(unittest.TestCase):
    def test_marker_asks_and_the_answer_continues_the_turn(self):
        state = session()
        lab.receive(state, [user_message("开始 [lab-ask]" + json.dumps(QUESTIONS) + "[/lab-ask]")])
        events = wait_for(state, lambda es: any(e["type"] == "session.status_idle" for e in es))
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds, ["session.status_running", "user.message", "agent.custom_tool_use", "session.status_idle"])
        use = events[2]
        self.assertEqual(use["name"], "ask_user")
        self.assertEqual(use["input"], QUESTIONS)
        self.assertEqual(events[3]["stop_reason"], {"type": "requires_action", "event_ids": [use["id"]]})
        self.assertEqual(state["status"], "idle")
        # 别的工具结果只存，不接着回
        lab.receive(state, [{"type": "user.custom_tool_result", "custom_tool_use_id": "sevt_other", "content": []}])
        time.sleep(0.1)
        self.assertEqual([e["type"] for e in state["_events"]][-1], "user.custom_tool_result")
        # 这一条的结果交回来：running → 带结果原文的回复（不截断）→ idle（end_turn）
        result = json.dumps({"answers": [{"header": "Book", "question": "Which book?", "labels": ["A (Recommended)"]}]}, ensure_ascii=False)
        lab.receive(state, [{"type": "user.custom_tool_result", "custom_tool_use_id": use["id"], "content": [{"type": "text", "text": result}]}])
        events = wait_for(state, lambda es: es[-1]["type"] == "session.status_idle" and es[-1]["stop_reason"]["type"] == "end_turn")
        reply = [e for e in events if e["type"] == "agent.message"][-1]
        self.assertEqual(reply["content"][0]["text"], f"{lab.REPLY_PREFIX} ask_user {result}")
        self.assertIsNone(state["_asked"])

    def test_plain_messages_and_broken_markers_reply_as_before(self):
        state = session()
        lab.receive(state, [user_message("你好 [lab-ask]{not json[/lab-ask]")])
        events = wait_for(state, lambda es: any(e["type"] == "agent.message" for e in es))
        reply = [e for e in events if e["type"] == "agent.message"][0]["content"][0]["text"]
        self.assertTrue(reply.startswith(lab.REPLY_PREFIX + " 你好"))
        self.assertFalse(any(e["type"] == "agent.custom_tool_use" for e in events))



class HoldUntilReleased(unittest.TestCase):
    """[lab-hold]K[/lab-hold]：这一轮一直挂着（流程在「回复进行中」确定地做事），任一会话收到 [lab-release]K[/lab-release] 才回（11-12）。"""

    def replies(self, state):
        with lab.LOCK:
            return [e for e in state["_events"] if e["type"] == "agent.message"]

    def test_the_reply_waits_for_the_release_from_another_session(self):
        held, other = session(), session()
        lab.receive(held, [user_message("慢一点 [lab-hold]k1[/lab-hold]")])
        time.sleep(0.5)
        self.assertEqual(self.replies(held), [], "放行之前不回")
        self.assertEqual(held["status"], "running")
        lab.receive(other, [user_message("[lab-release]k1[/lab-release]")])
        wait_for(held, lambda es: es[-1]["type"] == "session.status_idle")
        self.assertEqual(len(self.replies(held)), 1)

    def test_a_stop_ends_the_held_turn_without_a_reply(self):
        held = session()
        lab.receive(held, [user_message("慢慢来 [lab-hold]k2[/lab-hold]")])
        time.sleep(0.3)
        lab.receive(held, [{"type": "user.interrupt"}])
        time.sleep(0.5)
        self.assertEqual(self.replies(held), [])
        self.assertEqual(held["status"], "idle")

if __name__ == "__main__":
    unittest.main()
