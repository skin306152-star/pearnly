"""数字会计员第 3 期第 6、13、14 条与第 3 条要点（阶段 C 服务端）：推后核对、Express 历史 v2、CC、新文案。

- 推后核对（`push/express/written_check`）：票面对方（冻结进目标的 ``party_face``）、回执 ``verify`` 的白名单、结论（照收
  CC 桌面版的 state，另拿票面比实际挂到的对方档；旧版不带 = unverified；说不符却没列项 = unreadable）；
- 回执（`push/native/service.ack`）：成功时写 ``verify_state`` / ``verify_json``，MR.ERP 不核；自动推的核不上立刻降档（钩子），
  人推的不影响档位；不回滚、不重推；
- Express 历史 v2（`readback/history_rules`）：收 v1 / v2，「已定」带版本与读的日子，判定与自动推前的判定接得上；
- 提议一张一张推（用户 10-19 定：行上不露「推送」、不设「全部推送」），没有批量推送的接口；
- 档位与原因只由数据升降：CC 有档位、没有改档位的工具；定时任务简报写「待推送」；新词条四语齐。
"""
import ast
import csv
import datetime as dt
import importlib.util
import json
import pathlib
import re
import sys
import types
import unittest
from moved_modules import moved  # noqa: E402  （ADR-025 搬进共用层的模块）
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"
for _path in (ROOT / "apps/pearnly_core", ROOT / "apps/pearnly_cowork"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
for _name in [n for n in sys.modules if n.split(".")[0] in ("pearnly_cowork", "frappe")]:
    del sys.modules[_name]

from pearnly_cowork.push.automation import rules  # noqa: E402
from pearnly_core.express_rules.push import written_check as check  # noqa: E402
from pearnly_core.express_rules.push.common import sanitize_push_meta  # noqa: E402
from pearnly_cowork.readback import history_rules  # noqa: E402

SELLER, BUYER = "0107561000013", "0994000333447"


def ns(**kwargs):
    return types.SimpleNamespace(**kwargs)


class Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


def _load(name, path, modules):
    with mock.patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


# ── 第 6 条：推后核对（纯函数） ────────────────────────────────────────────────


class WrittenCheck(unittest.TestCase):
    def test_the_face_is_the_counterparty_with_a_full_tax_id_and_a_printed_branch(self):
        fields = {"seller_tax": "0-1075-61000-01-3", "seller_branch": "สาขาที่ 00003", "buyer_tax": BUYER,
                  "buyer_branch": "สำนักงานใหญ่"}
        self.assertEqual(check.face("purchase", fields), {"tax_id": SELLER, "branch": "00003"})
        self.assertEqual(check.face("sales", fields), {"tax_id": BUYER, "branch": "00000"})
        # 没印分店不比分店；税号不是 13 位不比税号（现金客户、简易税票）
        self.assertEqual(check.face("purchase", {"seller_tax": "12345"}), {"tax_id": "", "branch": ""})

    def test_only_the_three_keys_and_known_fields_come_through(self):
        raw = {"state": "mismatch", "fields": ["refnum", "drop table", "account"], "party": {
            "tax_id": SELLER + "999999999999", "branch": "0000012", "code": "S1"}, "rows": [{"x": 1}]}
        self.assertEqual(check.clean(raw), {"state": "mismatch", "fields": ["account", "refnum"],
                                            "party": {"tax_id": (SELLER + "999999999999")[:20], "branch": "00000"}})
        for junk in (None, "ok", {"state": "fine"}, {"fields": []}):
            self.assertIsNone(check.clean(junk))
        meta = sanitize_push_meta({"docnum": "RR1", "verify": raw, "stage": "x"})
        self.assertEqual(meta["verify"]["fields"], ["account", "refnum"])
        self.assertNotIn("verify", sanitize_push_meta({"docnum": "RR1", "verify": "ok"}))

    def test_the_outcome_takes_the_desktop_state_and_compares_the_face_with_the_party(self):
        face = {"tax_id": SELLER, "branch": "00003"}
        ok = {"state": "ok", "fields": [], "party": {"tax_id": SELLER, "branch": "00003"}}
        self.assertEqual(check.outcome(ok, face), {"state": "ok", "fields": [], "party": ok["party"]})
        # 旧版 CC 桌面版不带：unverified，由月度回读覆盖
        self.assertEqual(check.outcome(None, face)["state"], "unverified")
        # 挂到的对方档税号 / 分店与票面不同：挂错对方
        wrong = {**ok, "party": {"tax_id": "0105555555555", "branch": "00001"}}
        self.assertEqual(check.outcome(wrong, face)["fields"], ["party_tax", "party_branch"])
        # 对方档没登税号也算不一致；档上没有分店列（空）不比分店
        self.assertEqual(check.outcome({**ok, "party": {"tax_id": "", "branch": ""}}, face)["fields"], ["party_tax"])
        # 票面没税号、没印分店：只看桌面端自己比的
        self.assertEqual(check.outcome(wrong, {"tax_id": "", "branch": ""})["state"], "ok")
        # 旧记录没冻结票面：同上
        self.assertEqual(check.outcome(wrong, None)["state"], "ok")
        # 桌面端比出的不符照收；说不符却没列项（或列的都认不出）当读不出
        self.assertEqual(check.outcome({**ok, "state": "mismatch", "fields": ["date"]}, face)["fields"], ["date"])
        self.assertEqual(check.outcome({"state": "mismatch", "fields": ["?"], "party": {}}, face)["fields"],
                         ["unreadable"])


# ── 第 6 条：回执 ────────────────────────────────────────────────────────────────


class Receipt(unittest.TestCase):
    def _native(self, hooked):
        stub = mock.MagicMock
        modules = {"frappe": stub(), "pearnly_cowork": types.ModuleType("pearnly_cowork")}
        for name in ("access", "access.scope", "history", "history.push_state", "connections", "connections.service",
                     "connections.access", "connections.pool", "connections.preparation", "contracts",
                     "contracts.push", "push", "push.express", "push.express.common", "push.lifecycle",
                     "push.express.referenced", "push.native", "push.native.contracts", "push.native.documents",
                     "push.native.completion", "targets", "targets.freeze", "targets.contracts", "targets.identity"):
            modules[moved("pearnly_cowork." + name)] = stub()
        modules["pearnly_cowork.push.express"].written_check = check
        modules["pearnly_core.express_rules.push.written_check"] = check
        modules["pearnly_cowork.push.native.documents"].TYPES = ("Cowork Push Record", "Cowork Push Attempt", "Lock")
        modules["pearnly_cowork.push.native.contracts"].encode = lambda value: json.dumps(value, sort_keys=True)
        module = _load("pearnly_cowork.push.native.service_da_verify", APP / "push/native/service.py", modules)
        module.call = lambda function, *args, **kwargs: function(*args, **kwargs)
        service = ns(mismatch=lambda connection, account_set, account_row: hooked.append(
            (connection, account_set, account_row)))
        patcher = mock.patch.dict(sys.modules, {"pearnly_cowork.push.automation": ns(service=service),
                                                "pearnly_cowork.push.automation.service": service})
        patcher.start()
        self.addCleanup(patcher.stop)
        return module

    def test_a_success_records_the_check_and_an_automatic_mismatch_drops_the_tier(self):
        hooked = []
        module = self._native(hooked)
        face = {"tax_id": SELLER, "branch": ""}
        row = Row(name="CWP-1", connection="C1", account_key="c:\\pearnlyexpress\\69acme")
        response = {"meta": {"verify": {"state": "ok", "fields": [], "party": {"tax_id": "0105555555555"}}}}
        self.assertEqual(module._written_check(row, {"party_face": face}, response), "mismatch")
        self.assertEqual((row.verify_state, json.loads(row.verify_json)["fields"]), ("mismatch", ["party_tax"]))
        module._automation_mismatch(row, {"automation": {"class": "A", "account_row": {"source_id": "x"}}})
        self.assertEqual(hooked, [("C1", "c:\\pearnlyexpress\\69acme", {"source_id": "x"})])
        # 旧版不带 verify：unverified；MR.ERP 不核
        row = Row(name="CWP-2")
        self.assertEqual(module._written_check(row, {"party_face": face}, {"meta": {}}), "unverified")
        self.assertEqual(row.verify_state, "unverified")
        row = Row(name="CWP-3")
        self.assertIsNone(module._written_check(row, {"adapter": "mrerp"}, {"meta": {}}))
        self.assertNotIn("verify_state", row)

    def test_only_success_is_checked_and_only_automatic_pushes_touch_the_tier(self):
        source = (APP / "push/native/service.py").read_text(encoding="utf-8")
        ack = source.split("\ndef ack(", 1)[1].split("\ndef ", 1)[0]
        success = ack.split("if transition.status == 'success':", 1)[1]
        self.assertIn("checked = _written_check(row, target, response)", success.split("_save_record", 1)[0])
        self.assertIn("if checked == written_check.MISMATCH and isinstance(target.get('automation'), dict):", ack)
        self.assertLess(ack.index("_save_record(row, timestamp)"), ack.index("_automation_mismatch(row, target)"))
        # 不回滚、不重推：核不上不改推送记录的状态
        body = source.split("\ndef _written_check(", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("row.status", body)

    def test_preparing_freezes_the_face_and_the_automatic_account_row(self):
        source = (APP / "push/application/service.py").read_text(encoding="utf-8")
        self.assertIn('frozen["party_face"] = written_check.face(direction, fields)', source)
        self.assertIn('frozen["automation"]["account_row"] = account_row', source)


# ── 第 3、11 条：Express 历史 v2 ─────────────────────────────────────────────────


class HistoryVersion(unittest.TestCase):
    RAW = {"since": "2025-10-16", "read_at": "2026-10-10T03:00:00Z",
           "suppliers": [{"name": "ร้าน ก", "tax_id": SELLER, "branch": "00000",
                          "accounts": [{"code": "53-01-00-00", "docs": 4, "last": "2026-10-01"}]}]}

    def test_both_versions_are_read_and_the_version_is_kept(self):
        self.assertEqual(history_rules.RESULT_VERSION, 3)  # v3 多带商品单入账方式（用户 10-17）
        for version in (1, 2, 3):
            self.assertEqual(history_rules.parse({**self.RAW, "version": version})["version"], version)
        for version in (4, "2", True, None):
            with self.assertRaises(Exception):
                history_rules.parse({**self.RAW, "version": version})

    def test_a_settled_account_carries_its_version_and_the_day_it_was_read(self):
        settled = {"code": "53-01-00-00", "docs": 4, "last": "2026-10-01"}
        stored = {**history_rules.parse({**self.RAW, "version": 2}), "taken_at": "2026-10-10T03:01:00Z"}
        self.assertEqual(history_rules.stamp(settled, stored), {**settled, "version": 2, "read": "2026-10-10"})
        self.assertEqual(history_rules.stamp(settled, {"read_at": "2026-09-01T00:00:00Z"})["version"], 1)  # 旧存的
        self.assertIsNone(history_rules.stamp(None, stored))
        source = (APP / "readback/history.py").read_text(encoding="utf-8")
        self.assertIn('rules.stamp(rules.settled(out["accounts"], now.date(), stored.get("taken_at")), stored)', source)

    def test_the_judgement_reads_the_day_the_history_was_read(self):
        decide = (APP / "push/express/decide.py").read_text(encoding="utf-8")
        self.assertIn('decision["history"]["read"] = str(settled["read"])[:10]', decide)
        assess = (APP / "push/application/assess.py").read_text(encoding="utf-8")
        self.assertIn('history_read=_read_day((decision.get("history") or {}).get("read")),', assess)


# ── 第 11、12 条：提议一张一张推 ─────────────────────────────────────────────────


class OneByOne(unittest.TestCase):
    def test_there_is_no_bulk_push_of_proposals(self):
        # 用户 10-19：提议行上不露「推送」、点开才有，不设「全部推送」——服务端也没有一次推一批提议的接口
        pending_api = (APP / "assistant/pending_api.py").read_text(encoding="utf-8")
        self.assertNotIn("def push_proposals", pending_api)
        self.assertNotIn("PROPOSALS_MAX", pending_api)
        ledger = (APP / "companies/ledger.py").read_text(encoding="utf-8")
        self.assertNotIn("\ndef proposals(", ledger)

    def test_companies_does_not_import_assistant(self):
        # companies 不导入 assistant（assistant 依赖 companies）
        for path in (APP / "companies").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            self.assertFalse(any(name.startswith("pearnly_cowork.assistant") for name in imported), path.name)


# ── 第 13、14 条：CC 与文案 ─────────────────────────────────────────────────────


class CcAndWords(unittest.TestCase):
    def test_cc_reads_the_tier_and_never_gets_a_tool_to_change_it(self):
        source = (APP / "assistant/company_tools.py").read_text(encoding="utf-8")
        self.assertIn('found["automation"] = automation.view(row.name)', source)
        self.assertIn('("automation", "since")', source)
        guidance = re.sub(r'"\s*\n\s*"', "", source.split("GUIDANCE = [", 1)[1].split("\n]", 1)[0])
        self.assertIn("there is no tool or setting to change it", guidance)
        self.assertIn("automation.reasons in cowork_document_detail", guidance)
        tools = (APP / "assistant/tools.py").read_text(encoding="utf-8")
        names = set()
        for path in (APP / "assistant/tools.py", APP / "assistant/company_tools.py"):
            names |= {name for name in re.findall(r'"name": "(cowork_[a-z_]+)"', path.read_text(
                encoding="utf-8"))}
        self.assertFalse([name for name in names if "tier" in name or "automation" in name])
        self.assertIn('out["automation"] = automation', tools)

    def test_the_task_brief_says_ready_to_push(self):
        briefs = (APP / "folder_intake/briefs.py").read_text(encoding="utf-8")
        self.assertIn('if found["stop_reasons"].get("review"):', briefs)
        self.assertIn('_("cowork.folder.brief.why.review")', briefs)

    def test_new_words_exist_in_every_language(self):
        with open(APP / "i18n/messages.tsv", encoding="utf-8") as handle:
            rows = {row[4]: row for row in csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
                    if len(row) >= 5 and row[4]}
        for key in ("cowork.company.pending.cc_proposal", "cowork.folder.brief.why.review"):
            self.assertTrue(all(rows[key][:4]), key)
        self.assertEqual(rows["cowork.company.pending.cc_proposal"][1], "待推送")


if __name__ == "__main__":
    unittest.main()
