"""本对话账套的共用层（A 线 11-12，R2-5：右栏显示 T.8，CC 却按票据日期另判写哪本、问了答了没记成目标）：
`pearnly_core.assistant.ledger` 与会话读写里接它的几处。

公司 + 月份 + 分店定出一本，存在会话 `project_ledger`；存的仍可选就用存的（用户选过的不被冲掉），失效了按产品默认补算；改月份时
选过的仍可选就保留，否则重算；CC 只能设用户在最近一张提问卡上选的那本；产品没声明、provider 出错一律当作没有，不挡发话。
Frappe 与引擎用 test_assistant 的替身（同一套会话表内存替身）。
"""
import datetime
import json
import sys
import types
import unittest
from unittest import mock

import test_assistant as base

frappe, contracts, _Row = base.frappe, base.contracts, base._Row
# 会话读写还导入个人排列（test_assistant 由排列的测试先载入，这里单独跑时自己载入）
setattr(sys.modules["pearnly_core.assistant"], "prefs",
        sys.modules.get("pearnly_core.assistant.prefs") or base._load("pearnly_core.assistant.prefs",
                                                                       base.CORE / "assistant/prefs.py"))
T7, T8, T9 = "EXP1|c:\\pearnlyexpress\\70exp\\t7sincer", "EXP1|c:\\pearnlyexpress\\70exp\\t8sincer", \
    "EXP1|c:\\pearnlyexpress\\70exp\\t9sincer"


class _LedgerProvider(base._Provider):
    """Cowork provider 的替身，另声明本对话账套：``books[(公司, 月份)] = (可选, 默认)``。"""

    LEDGER = True

    def __init__(self):
        super().__init__()
        self.books = {}
        self.broken = False
        self.calls = []

    def ledgers(self, ref, period, scope, user):
        self.calls.append((ref, period, scope, user))
        if self.broken:
            raise RuntimeError("catalog")
        if ref in getattr(self, "unused", ()):
            return None  # 这家不用本对话账套（ERP 不接 Express 的公司）
        options, default = self.books.get((ref, period), ([], ""))
        return {"options": [{"id": key, "label": label, "covers": covers} for key, label, covers in options],
                "default": default}


SAME_PERIOD = ([(T8, "T.8", True), (T9, "T.9", True), (T7, "T.7", False)], "")  # 同期两本、没记住现用的：未定
CHOSEN_T8 = ([(T8, "T.8", True), (T9, "T.9", True), (T7, "T.7", False)], T8)   # 记住了 T.8
ONLY_T7 = ([(T7, "T.7", True)], T7)                                            # 2024 年的月份：只有 T.7 覆盖


class LedgerTests(unittest.TestCase):
    OWNER = base.ProjectTests.OWNER

    def setUp(self):
        base.ProjectTests.setUp(self)
        self.provider = _LedgerProvider()
        sys.modules["fake_company_provider"] = self.provider
        self.ledger = sys.modules["pearnly_core.assistant.ledger"]
        self.provider.books = {("co_abc1", "2026-09"): CHOSEN_T8, ("co_abc1", "2026-10"): SAME_PERIOD,
                               ("co_abc1", "2024-05"): ONLY_T7, ("co_abc1", ""): CHOSEN_T8}
        frappe.session.user = self.OWNER
        self.row = self.db.rows[1]  # r2：挂在 co_abc1、2026-09、分店 00012
        patcher = mock.patch.object(frappe.local, "pearnly_assistant", None, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _ledger_writes(self):
        return [(name, values[self.ledger.FIELD], modified) for name, values, modified in self.db.writes
                if self.ledger.FIELD in values]

    # ── current ─────────────────────────────────────────────────────────────
    def test_a_stored_ledger_still_offered_is_kept(self):
        self.row["project_ledger"] = T9  # 用户在右栏选的 T.9（不是默认的 T.8）
        found = self.ledger.current(self.row)
        self.assertEqual((found["status"], found["id"], found["label"]), ("set", T9, "T.9"))
        self.assertEqual(self._ledger_writes(), [])
        self.assertEqual(self.provider.calls[-1], ("co_abc1", "2026-09", "00012", self.OWNER))

    def test_a_stale_ledger_is_recomputed_and_stored_without_touching_the_activity_time(self):
        self.row["project_ledger"] = "EXP1|gone"  # 账套从目录消失
        modified = str(self.row.modified)
        found = self.ledger.current(self.row)
        self.assertEqual((found["status"], found["id"]), ("set", T8))
        self.assertEqual(self._ledger_writes(), [("r2", T8, False)])
        self.assertEqual(str(self.row.modified), modified)
        # 没有默认（同期两本、没记住现用的）：未定，存的失效值清掉
        self.row.update(period="2026-10", project_ledger="EXP1|gone")
        found = self.ledger.current(self.row)
        self.assertEqual((found["status"], found["id"]), ("needed", ""))
        self.assertEqual([item["id"] for item in found["options"]], [T8, T9, T7])
        self.assertEqual(self._ledger_writes()[-1], ("r2", "", False))
        # 只看不存（GET 接口）：不写
        self.row["project_ledger"] = "EXP1|gone"
        writes = len(self.db.writes)
        self.assertEqual(self.ledger.current(self.row, store=False)["status"], "needed")
        self.assertEqual(len(self.db.writes), writes)

    def test_no_ledger_without_a_company_a_declaration_or_a_working_provider(self):
        self.assertIsNone(self.ledger.current(self.db.rows[0]))  # 「最近」
        self.provider.broken = True
        self.assertIsNone(self.ledger.current(self.row))
        self.assertIn("Assistant ledger options failed", self.logged)
        self.provider.broken = False
        # 产品声明了、但这家不用本对话账套（ERP 不接 Express 的公司回 None）：当作没有，说明里也不叫 CC 问账套
        self.provider.unused = {"co_abc1"}
        self.assertIsNone(self.ledger.current(self.row))
        self.provider.unused = set()
        plain = base._Provider()
        sys.modules["fake_company_provider"] = plain  # 没声明 LEDGER（DMS）
        self.assertIsNone(self.ledger.current(self.row))
        self.assertEqual(self._ledger_writes(), [])

    def test_an_old_conversation_gets_its_ledger_when_it_speaks_and_the_note_says_it(self):
        # 老对话（字段新加，没存）发话时：公司说明后接账套说明段，并补算存下
        self.row["project_ledger"] = ""
        text, noted = self.projects.note(self.row, self.OWNER)
        self.assertIn("<company>ABC", text)
        self.assertIn("This conversation's account set is T.8", text)
        self.assertIn("never ask which account set", text)
        self.assertIn("Never choose an account set by the document date", text)
        self.assertEqual(self._ledger_writes(), [("r2", T8, False)])
        # 账套换了：说明变了、哈希变了，重带
        self.row["project_noted"] = noted
        self.row["project_ledger"] = T9
        text2, noted2 = self.projects.note(self.row, self.OWNER)
        self.assertIn("This conversation's account set is T.9", text2)
        self.assertNotEqual(noted2, noted)
        # 未定：列出这个月的几本、问一次再用工具设
        self.row.update(period="2026-10", project_ledger="", project_noted="")
        text3, _noted = self.projects.note(self.row, self.OWNER)
        self.assertIn("not decided yet", text3)
        self.assertIn("pearnly_conversation_ledger", text3)
        self.assertIn("T.8 (id " + T8 + ", covers this month)", text3)
        # provider 出错：只带产品说明，不挡发话
        self.provider.broken = True
        text4, _noted = self.projects.note(self.row, self.OWNER)
        self.assertNotIn("conversation_account_set", text4)
        self.assertIn("<company>ABC", text4)

    # ── 建、移 ────────────────────────────────────────────────────────────────
    def test_a_new_conversation_stores_the_ledger_when_one_can_be_decided(self):
        summary, doc = base.ProjectTests._create(self, project="co_abc1", period="2026-09", scope="00012")
        self.assertEqual((doc.project_ledger, summary["ledger"]), (T8, T8))
        _summary, doc = base.ProjectTests._create(self, project="co_abc1", period="2026-10")
        self.assertEqual(doc.project_ledger, "")  # 同期两本：未定，发话前问一次
        _summary, doc = base.ProjectTests._create(self)
        self.assertEqual(doc.project_ledger, "")

    def test_moving_the_month_keeps_the_chosen_ledger_while_offered_else_recomputes(self):
        self.row["project_ledger"] = T9  # 用户选过 T.9
        with mock.patch.object(self.sessions, "owned", lambda sid, allow_archived=False: self.row):
            out = self.sessions.move("sesn_abc001", "co_abc1", period="2026-10", scope="00012")
            self.assertEqual(out["ledger"], T9)  # 新月份 T.9 仍可选：保留，不被默认冲掉
            self.assertEqual(self.db.writes[-1][1]["project_ledger"], T9)
            self.assertEqual(self.pushed[-1][0]["ledger"], T9)
            out = self.sessions.move("sesn_abc001", "co_abc1", period="2024-05", scope="00012")
            self.assertEqual(out["ledger"], T7)  # 2024 年只有 T.7：按新默认重算
            self.assertEqual(self.db.writes[-1][1]["project_ledger"], T7)
            # 右栏下拉选了一本：要在新的可选里
            out = self.sessions.move("sesn_abc001", "co_abc1", period="2026-10", scope="00012", ledger_id=T8)
            self.assertEqual((out["ledger"], self.row.project_ledger), (T8, T8))
            with self.assertRaises(frappe.ValidationError):
                self.sessions.move("sesn_abc001", "co_abc1", period="2024-05", scope="00012", ledger_id=T9)
        self.assertEqual(contracts.project_change("move", session="sesn_abc001", project="co_abc1", period="2026-10",
                                                  scope="", ledger=T8)["ledger"], T8)
        self.assertNotIn("ledger", contracts.project_change("move", session="sesn_abc001", project="co_abc1"))
        self.assertEqual(contracts.project_change("move", session="sesn_abc001", project="", ledger=T8)["ledger"], "")

    def test_the_page_reads_the_options_without_writing(self):
        self.row["project_ledger"] = ""
        self.row["engine_code"] = base.CLAUDE
        writes = len(self.db.writes)
        out = self.ledger.view("sesn_abc001")
        self.assertEqual(out["current"], T8)
        self.assertEqual([(item["id"], item["covers"]) for item in out["options"]], [(T8, True), (T9, True), (T7, False)])
        self.assertEqual(len(self.db.writes), writes)

    # ── CC 设账套 ──────────────────────────────────────────────────────────────
    def _tool(self, ledger, answers):
        frappe.local.pearnly_assistant = {"session_id": "sesn_abc001"}
        with mock.patch.object(self.sessions.toolrun, "last_answers", lambda sid: answers):
            return json.loads(self.ledger.tool_choose({"ledger": ledger}))

    def test_cc_sets_only_the_ledger_the_user_picked_on_the_latest_card(self):
        self.row.update(period="2026-10", project_ledger="")
        self.assertEqual(self._tool(T9, None), {"status": "refused", "reason": "not_chosen_by_user"})  # 没答过
        self.assertEqual(self._tool(T9, ["T.8"]), {"status": "refused", "reason": "not_chosen_by_user"})  # 选的是另一本
        self.assertEqual(self._tool("EXP1|t99", ["T.99"]), {"status": "refused", "reason": "not_an_option"})
        self.assertEqual(self._ledger_writes(), [])
        self.assertEqual(self._tool(T9, ["T.9"]), {"status": "set", "ledger": T9, "label": "T.9"})
        self.assertEqual(self._ledger_writes(), [("r2", T9, False)])
        self.assertEqual(self.pushed[-1][0], {"type": "pearnly.project", "op": "move", "session_id": "sesn_abc001",
                                              "project": "co_abc1", "period": "2026-10", "scope": "00012",
                                              "ledger": T9})
        # 名称也认（模型把名称当编号传）
        self.assertEqual(self._tool("T.8", ["T.8"])["ledger"], T8)
        # 没有本对话账套的产品 / 没挂公司的对话
        sys.modules["fake_company_provider"] = base._Provider()
        self.assertEqual(self._tool(T9, ["T.9"]), {"status": "refused", "reason": "not_available"})
        frappe.local.pearnly_assistant = None
        self.assertEqual(json.loads(self.ledger.tool_choose({"ledger": T9}))["reason"], "no_conversation")

    def test_the_tool_is_shared_by_both_products(self):
        tool = next(item for item in self.sessions.tools.CORE_TOOLS if item["name"] == "pearnly_conversation_ledger")
        self.assertEqual(tool["handler"], "pearnly_core.assistant.ledger.tool_choose")
        self.assertNotIn("modes", tool)  # 直写、代录都要（代录会话的账套同样只问一次）
        self.assertNotIn("approval", tool)
        self.assertIn("ask_user", tool["description"])

    def test_of_session_and_of_task(self):
        frappe.local.pearnly_assistant = {"session_id": "sesn_abc001"}
        self.row["project_ledger"] = T9
        self.assertEqual(self.ledger.of_session()["id"], T9)
        frappe.local.pearnly_assistant = None
        self.assertIsNone(self.ledger.of_session())
        self.db.rows.append(_Row({"name": "r9", "session_id": "op_task0009", "user": self.OWNER,
                                  "engine_code": base.CLAUDE, "archived": 0, "project": "co_abc1",
                                  "period": "2026-09", "project_ledger": "", "operator_task": "task0009",
                                  "modified": datetime.datetime(2026, 10, 15, 9, 0)}))
        self.assertEqual(self.ledger.of_task("task0009")["id"], T8)
        self.assertIsNone(self.ledger.of_task("task0404"))


if __name__ == "__main__":
    unittest.main()
