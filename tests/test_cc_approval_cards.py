"""CC 对话里删除、结束、撤回、立即运行这几类工具先出「允许 / 拒绝」卡片（用户 11-12；总设计 12 / 12.1 节）。

两产品 9 个工具：Cowork ``cowork_delete_documents``、``cowork_express_readback_delete``、``cowork_recon_delete``、
``cowork_tax_draft_delete``、``cowork_stocktake_close``、``cowork_stocktake_delete``、``cowork_run_folder_task``；ERP
``erp_stocktake_close``、``erp_collection_withdraw``。

这里用产品源码里**真实的工具声明**（按语法树读出，不导入产品）加共用层**真实的** ``tools`` / ``toolrun``（替身同
``test_assistant``），逐个工具走三条：

1. 模型调用后不执行，停在等「允许」；
2. 点「允许」后执行一次，执行的就是卡片预览时的那份参数（卡上列的那几条），不多不少；
3. 点「拒绝」不执行，交回模型「没得到同意」。

另有一条假智能体流程：说「删掉这两张」→ 出卡 → 拒绝 → 两张都还在；再说一次 → 允许 → 两张删掉。
卡片上列的内容（名字而不是编号）在各产品的工具测试里（``test_cowork_assistant_tools.ApprovalCardTests``、
``test_erp_cc_stocktake``、``test_erp_collection_cc``）。
"""
import ast
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_assistant as base  # noqa: E402  装好共用层替身（frappe、引擎）

ROOT = base.ROOT
tools = sys.modules["pearnly_core.assistant.tools"]
toolrun = sys.modules["pearnly_core.assistant.toolrun"]

SOURCES = {
    "apps/pearnly_cowork/pearnly_cowork/assistant/tools.py": (
        "cowork_delete_documents", "cowork_express_readback_delete", "cowork_recon_delete", "cowork_tax_draft_delete",
        "cowork_stocktake_close", "cowork_stocktake_delete", "cowork_run_folder_task"),
    "apps/pearnly_erp_portal/pearnly_erp_portal/inventory/cc_tools.py": ("erp_stocktake_close",),
    "apps/pearnly_erp_portal/pearnly_erp_portal/collection/cc_tools.py": ("erp_collection_withdraw",),
}
# 每个工具一份典型参数（模型会给的样子）
INPUTS = {
    "cowork_delete_documents": {"documents": ["H1", "H2"]},
    "cowork_express_readback_delete": {"readback": "r" * 32},
    "cowork_recon_delete": {"kind": "bank", "tasks": ["CBR-1", "CBR-2"]},
    "cowork_tax_draft_delete": {"drafts": ["CTD-1"]},
    "cowork_stocktake_close": {"count": "ST-1"},
    "cowork_stocktake_delete": {"counts": ["ST-1", "ST-2"]},
    "cowork_run_folder_task": {"task": "R1"},
    "erp_stocktake_close": {"count": "ESC-1"},
    "erp_collection_withdraw": {"collection": "COL-1"},
}


def _declarations() -> dict:
    """产品源码里这些工具的声明（只取字面量的键：name / approval / ask / preview / handler / modes）。"""
    found = {}
    for path, names in SOURCES.items():
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}
            name = keys.get("name")
            if not isinstance(name, ast.Constant) or name.value not in names:
                continue
            found[name.value] = {key: ast.literal_eval(keys[key]) for key in
                                 ("name", "label", "approval", "ask", "note", "preview", "handler", "modes") if key in keys}
    return found


DECLARED = _declarations()


class _Db:
    """执行记录表（内存）：execute 插入、decide 抢占与回写都落在 `runs`。"""

    def __init__(self):
        self.runs = {}
        self._cursor = types.SimpleNamespace(rowcount=0)

    def rollback(self):
        pass

    commit = rollback

    def get_value(self, doctype, filters, field=None, as_dict=False, **kw):
        if doctype == "User":
            return 1  # 会话主人在用
        if doctype == toolrun.SESSION:
            return None  # 直写会话
        if isinstance(filters, dict):
            for name, row in self.runs.items():
                if all(row.get(k) == v for k, v in filters.items()):
                    return row[field] if field != "name" else name
            return None
        row = self.runs[filters]
        if isinstance(field, (list, tuple)):
            return types.SimpleNamespace(**{f: row.get(f) for f in field}) if as_dict else [row.get(f) for f in field]
        return row.get(field)

    def sql(self, query, values):
        status, _modified, name = values
        hit = self.runs[name]["status"] == "awaiting_approval"
        if hit:
            self.runs[name]["status"] = status
        self._cursor = types.SimpleNamespace(rowcount=int(hit))

    def set_value(self, doctype, name, field, value=None):
        self.runs[name].update(field if isinstance(field, dict) else {field: value})


class ApprovalCardFlowTests(unittest.TestCase):
    def setUp(self):
        self.db = _Db()
        self.ran, self.handed = [], []
        self.store = {"H1": "Thai Steel", "H2": "Siam Paper"}  # 假智能体流程里的两张单据

        def get_doc(values):
            def insert(ignore_permissions=False):
                self.db.runs[f"run{len(self.db.runs) + 1}"] = dict(values)
            return types.SimpleNamespace(insert=insert)

        def handler(name):
            def run(tool_input):
                self.ran.append((name, tool_input))
                if name == "cowork_delete_documents":
                    for doc in tool_input["documents"]:
                        self.store.pop(doc, None)
                return json.dumps({"status": "done"})
            return run

        handlers = {tool["handler"]: handler(name) for name, tool in DECLARED.items()}
        previews = {tool["preview"]: (lambda data: [{"field": "Delete", "before": "", "after": "x"}])
                    for tool in DECLARED.values()}
        previews.update({tool["note"]: (lambda data: "Flow Co · Flow 69") for tool in DECLARED.values() if tool.get("note")})
        patches = [
            mock.patch.object(tools, "declared", lambda: list(DECLARED.values())),
            mock.patch.object(toolrun.frappe, "db", self.db, create=True),
            mock.patch.object(toolrun.frappe, "get_doc", get_doc, create=True),
            mock.patch.object(toolrun.frappe, "get_attr", lambda path: {**handlers, **previews}[path], create=True),
            mock.patch.object(toolrun.frappe, "session", types.SimpleNamespace(user="owner@x.test"), create=True),
            mock.patch.object(toolrun.frappe, "local", types.SimpleNamespace(cache={}, lang="en"), create=True),
            mock.patch.object(toolrun, "_use_lang", lambda conversation: None),
            mock.patch.object(toolrun, "fresh", lambda: None),
            mock.patch.object(toolrun, "_announce", lambda *a: None),
            mock.patch.object(toolrun, "_publish_run", lambda *a, **k: None),
            mock.patch.object(toolrun, "_hand_back", lambda conv, remote, use_id, text, err: self.handed.append(
                (use_id, json.loads(text)))),
            mock.patch.object(toolrun.waits, "requested", lambda: None),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _call(self, use_id, name, tool_input):
        return toolrun.execute("conv", "sesn", {"id": use_id, "type": "agent.custom_tool_use", "name": name,
                                                "input": tool_input})

    def test_every_listed_tool_is_declared_with_a_card(self):
        self.assertEqual(set(DECLARED), set(INPUTS))
        for name, tool in DECLARED.items():
            with self.subTest(name):
                self.assertIs(tool["approval"], True)
                self.assertTrue(tool["ask"].endswith("?"), tool["ask"])
                # 卡片预览函数与执行函数在同一个模块（产品自己的工具文件），不另起一套
                self.assertEqual(tool["preview"].rpartition(".")[0], tool["handler"].rpartition(".")[0])
                self.assertTrue(tools.needs_approval(name, INPUTS[name]))
                view = tools.approval_view(name, INPUTS[name])
                self.assertEqual(view["ask"], tool["ask"])
                self.assertEqual(view["preview"], [{"field": "Delete", "before": "", "after": "x"}])
                if tool.get("note"):  # 有事实的那一行（公司、账套）：函数也在产品自己的工具文件里
                    self.assertEqual(tool["note"].rpartition(".")[0], tool["handler"].rpartition(".")[0])
                    self.assertEqual(view["note"], "Flow Co · Flow 69")
                else:
                    self.assertNotIn("note", view)
        # 立即运行最需要这一行（会识别并推进哪本账套）；撤回、结束写公司
        for name in ("cowork_run_folder_task", "cowork_delete_documents", "erp_stocktake_close", "erp_collection_withdraw"):
            self.assertIn("note", DECLARED[name], name)

    def test_nothing_runs_before_allow_and_allow_runs_exactly_what_was_previewed(self):
        for index, (name, tool_input) in enumerate(INPUTS.items()):
            with self.subTest(name):
                use_id = f"sevt_a{index}"
                self.assertEqual(self._call(use_id, name, tool_input), "awaiting_approval")
                self.assertEqual(self.ran, [])  # ① 没点「允许」不执行
                run = next(row for row in self.db.runs.values() if row["tool_use_id"] == use_id)
                self.assertEqual(json.loads(run["tool_input"]), tool_input)  # 卡片预览的就是存下的这份
                self.assertEqual(toolrun.decide("conv", "sesn", use_id, True), "done")
                self.assertEqual(self.ran, [(name, tool_input)])  # ② 执行一次、参数原样（卡上列的那几条）
                self.assertEqual(toolrun.decide("conv", "sesn", use_id, True), "not_waiting")  # 再点不再执行
                self.assertEqual(len(self.ran), 1)
                self.ran.clear()

    def test_deny_runs_nothing_and_tells_the_model(self):
        for index, (name, tool_input) in enumerate(INPUTS.items()):
            with self.subTest(name):
                use_id = f"sevt_d{index}"
                self.assertEqual(self._call(use_id, name, tool_input), "awaiting_approval")
                self.assertEqual(toolrun.decide("conv", "sesn", use_id, False), "denied")
                self.assertEqual(self.ran, [])  # ③ 拒绝不执行
                self.assertEqual(self.handed[-1][0], use_id)
                self.assertEqual(self.handed[-1][1]["status"], "denied")
                self.assertEqual(toolrun.decide("conv", "sesn", use_id, True), "not_waiting")  # 拒绝后再点「允许」也不执行
                self.assertEqual(self.ran, [])

    def test_the_fact_line_shows_only_when_the_tool_gives_one(self):
        """问句下面那一行事实（`note`，用户 11-14 照 Claude 桌面应用的权限确认框）：工具给了才出；回空、出错、没声明都不出。"""
        tool = {"name": "t_note", "approval": True, "ask": "Delete?", "note": "x.note", "preview": "x.preview", "handler": "x.run"}
        plain = {"name": "t_plain", "approval": True, "ask": "Delete?", "preview": "x.preview", "handler": "x.run"}
        answers = {"x.preview": lambda data: [{"field": "Delete", "before": "", "after": "IV-1"}]}

        def boom(data):
            raise RuntimeError("note failed")

        logged = []
        with mock.patch.object(tools, "declared", lambda: [tool, plain]), \
                mock.patch.object(tools.frappe, "log_error", lambda **kw: logged.append(kw), create=True), \
                mock.patch.object(tools.frappe, "clear_messages", lambda: None, create=True):
            for given, expected in (("T9 Sincer · 70EXP T.9", "T9 Sincer · 70EXP T.9"), ("  ", None), (None, None)):
                with mock.patch.object(tools.frappe, "get_attr", lambda path, given=given: {
                        **answers, "x.note": lambda data: given}[path], create=True):
                    view = tools.approval_view("t_note", {})
                self.assertEqual(view.get("note"), expected, given)
                self.assertEqual(view["preview"], [{"field": "Delete", "before": "", "after": "IV-1"}])
            with mock.patch.object(tools.frappe, "get_attr", lambda path: {**answers, "x.note": boom}[path], create=True):
                self.assertNotIn("note", tools.approval_view("t_note", {}))  # 算不出：那一行不出，卡片照出
            self.assertEqual(len(logged), 1)
            with mock.patch.object(tools.frappe, "get_attr", lambda path: answers[path], create=True):
                self.assertNotIn("note", tools.approval_view("t_plain", {}))  # 没声明不出

    def test_fake_agent_delete_two_documents_deny_then_allow(self):
        """用户：「删掉这两张」→ 出卡 → 拒绝 → 两张都还在；再说一次 → 允许 → 两张删掉，只删这两张。"""
        self.store["H3"] = "Other"
        ask = {"documents": ["H1", "H2"]}
        self.assertEqual(self._call("sevt_1", "cowork_delete_documents", ask), "awaiting_approval")
        self.assertEqual(toolrun.decide("conv", "sesn", "sevt_1", False), "denied")
        self.assertEqual(sorted(self.store), ["H1", "H2", "H3"])
        self.assertEqual(self._call("sevt_2", "cowork_delete_documents", ask), "awaiting_approval")
        self.assertEqual(sorted(self.store), ["H1", "H2", "H3"])  # 卡片挂着时仍没删
        self.assertEqual(toolrun.decide("conv", "sesn", "sevt_2", True), "done")
        self.assertEqual(sorted(self.store), ["H3"])
        self.assertEqual(self.ran, [("cowork_delete_documents", ask)])


if __name__ == "__main__":
    unittest.main()
