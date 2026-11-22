"""Cowork 收银日汇总（POS 导出）的引导式（总表 CW-D-POS，`assistant.guided.pos`）：用共用引擎 `assistant.guide.run` 真实现走一遍。

替身沿用对账引导的测试（`test_cowork_recon_guide`：对话账套、写入闸、对话文件），另加 POS 服务（`pos_import.service.preview / admit`）
与 CC 工具的原件转换（`assistant.tools._pos_source`）的替身。

断言的是应有状态（主会话 11-16 批的口径）：
- 有录入功能的人才见「收银日汇总」；本对话账套不是 Express 交不了导出；一次一份；
- 版式认得出 / 记住过：不出认列题；认不出：出子题（列从表头选、日月顺序、含不含税、每个没见过的状态值一题），选项都来自
  这份导出；答案拼成 `preview` 的形状，**状态值按原值交回**；
- **认列答案只来自卡上**：交给 POS 服务的 ``answers`` 恰好是卡上点的（守住 P3 盘点 #31，以前由模型填）；答了不在选项里的不收；
- 导出带商品明细才问入账方式，否则按金额记、不问；
- 复核卡每天一行、合计对；确定按天建票一次（双击不建两次），版式此刻对不上退回认列那一步；结果给建好的票。

    python3 tests/test_cowork_pos_guide.py
"""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_cowork_recon_guide as R  # noqa: E402  装好 frappe 与对话、文件的替身

STATE, run, SESSION = R.STATE, R.run, R.SESSION
HEADERS = ["วันที่", "เลขที่", "ยอดรวม", "VAT", "สถานะ"]
QUESTIONS = [{"kind": "column", "field": "receipt", "options": ["เลขที่", "เลขที่บิล"]},
             {"kind": "date_order", "options": ["dmy", "mdy"], "samples": ["03/04/2026"]},
             {"kind": "amount_basis", "column": "ยอดรวม", "options": ["inclusive", "exclusive"]},
             {"kind": "status_values", "values": ["ปกติ", "ยกเลิกบิล"], "options": ["sale", "void", "refund"]}]
DAYS = [{"date": "2026-10-01", "range": "R1-R9", "gross": "1070.00", "vat": "70.00"},
        {"date": "2026-10-02", "range": "R10-R12", "gross": "535.00", "vat": "35.00"}]


def _preview(source, *, workspace, connection, account_set_key, answers=None, posting_kind=None):
    STATE["previews"].append(answers)
    layout = {"columns": {"date": "วันที่", "receipt": "เลขที่", "amount": "ยอดรวม",
                          **({"item": "สินค้า"} if STATE.get("items") else {})}}
    if STATE.get("known") or (answers and _complete(answers)):
        return {"status": "ready", "layout": layout, "days": [dict(day) for day in DAYS]}
    return {"status": "layout_needed", "headers": HEADERS, "questions": [dict(q) for q in QUESTIONS]}


def _complete(answers):
    return {"columns", "date_order", "amount_basis", "status_values"} <= set(answers) \
        and set(answers["status_values"]) == {"ปกติ", "ยกเลิกบิล"}


def _admit(source, key, *, workspace, connection, account_set_key, client=None, posting_kind=None):
    STATE["admitted"].append((source, key, connection, account_set_key, posting_kind))
    if STATE.get("forgot"):
        return {"status": "layout_needed", "questions": QUESTIONS}
    return {"status": "ok", "histories": ["H1", "H2"],
            "days": [{"document": f"H{n}", **day} for n, day in enumerate(DAYS, 1)]}


R._module("pearnly_cowork.pos_import")
R._module("pearnly_cowork.pos_import.service", preview=_preview, admit=_admit)
R._module("pearnly_cowork.access.scope", require=lambda capability: None if STATE.get("intake", True) else (_ for _ in ()).throw(PermissionError("x")),
          default_workspace=lambda: "WS1")
R._module("pearnly_cowork.assistant.tools", _pos_source=lambda file_id: (
    {"file": file_id, "filename": f"{file_id}.xlsx", "source": f"SRC-{file_id}", "key": f"assistant:pos:{file_id}"}
    if file_id in STATE["attachments"] else {"file": file_id, "status": "not_found"}))
R.frappe.PermissionError = PermissionError

from pearnly_cowork.assistant.guided import pos  # noqa: E402

ANSWERS = {"column.receipt": "เลขที่", "date_order": "dmy", "amount_basis": "inclusive", "status.1": "sale", "status.2": "void"}


def _codes(found):
    """拒收照引擎形状 {格: {code, text}}（main 8172605d2）：只比码。"""
    return {key: value["code"] for key, value in found["refused"].items()}


class Base(R.Base):
    def setUp(self):
        super().setUp()
        STATE.update(previews=[], admitted=[], intake=True)
        R.HOOKS["pearnly_guides"] = ["pearnly_cowork.assistant.guided.pos.GUIDES"]

    def uploaded(self):
        name = run.start(SESSION, "pos")["run"]
        return name, run.answer(name, {"export": ["F1"]})

    def reviewed(self):
        name, _found = self.uploaded()
        return name, run.answer(name, {"columns": dict(ANSWERS)})


class Menu(Base):
    def test_people_with_intake_see_it(self):
        self.assertEqual(R.registry.offered()["chat"], ["pos"])
        STATE["intake"] = False
        self.assertEqual(R.registry.offered()["chat"], [])


class Export(Base):
    def test_one_export_from_this_conversation_into_an_express_book(self):
        name = run.start(SESSION, "pos")["run"]
        self.assertEqual(_codes(run.answer(name, {"export": ["F1", "F2"]})), {"export": "pos.type_unsupported"})
        self.assertEqual(_codes(run.answer(name, {"export": ["F9"]})), {"export": "pos.file_unreadable"})
        STATE["current"] = R.MRERP_ID
        self.assertEqual(_codes(run.answer(name, {"export": ["F1"]})), {"export": "pos.not_express"})


class Columns(Base):
    def test_a_known_layout_asks_nothing_and_goes_to_review(self):
        STATE["known"] = True
        _name, found = self.uploaded()
        self.assertEqual(found["status"], run.REVIEWING)
        self.assertEqual([row["code"] for row in found["review"]["tables"][0]["rows"]], ["2026-10-01", "2026-10-02"])
        self.assertEqual(found["review"]["total"], "1605.00")

    def test_an_unknown_layout_asks_its_questions_with_options_from_this_export(self):
        _name, found = self.uploaded()
        subs = self.asked(found)["columns"]["questions"]
        self.assertEqual([sub["key"] for sub in subs],
                         ["column.receipt", "date_order", "amount_basis", "status.1", "status.2"])
        self.assertEqual(subs[0]["options"], [{"code": "เลขที่", "name": "เลขที่"}, {"code": "เลขที่บิล", "name": "เลขที่บิล"}])
        self.assertEqual((subs[0]["title"], subs[1]["note"], subs[2]["title"], subs[3]["title"]),
                         ("Receipt No.", "03/04/2026", "ยอดรวม", "ปกติ"))
        self.assertEqual([row["code"] for row in subs[3]["options"]], ["sale", "void", "refund"])

    def test_the_answers_given_to_the_pos_service_are_exactly_the_ones_clicked_on_the_card(self):
        name, found = self.reviewed()
        self.assertEqual(found["status"], run.REVIEWING)
        given = [answers for answers in STATE["previews"] if answers]
        self.assertTrue(given)
        for answers in given:
            self.assertEqual(answers, {"columns": {"receipt": "เลขที่"}, "date_order": "dmy", "amount_basis": "inclusive",
                                       "status_values": {"ปกติ": "sale", "ยกเลิกบิล": "void"}})

    def test_an_answer_not_among_this_exports_options_is_not_taken(self):
        name, _found = self.uploaded()
        refused = run.answer(name, {"columns": {**ANSWERS, "column.receipt": "Bill No"}})
        self.assertEqual(_codes(refused), {"columns": "not offered"})
        self.assertFalse([answers for answers in STATE["previews"] if answers])

    def test_answers_that_leave_a_question_open_are_not_taken(self):
        name, _found = self.uploaded()
        partial = {key: value for key, value in ANSWERS.items() if key != "status.2"}
        self.assertEqual(_codes(run.answer(name, {"columns": partial})), {"columns": "incomplete"})


class Posting(Base):
    def test_without_item_lines_it_is_recorded_by_amount_without_asking(self):
        _name, found = self.reviewed()
        self.assertEqual(found["status"], run.REVIEWING)

    def test_with_item_lines_the_posting_is_asked(self):
        STATE["items"] = True
        name, found = self.reviewed()
        self.assertEqual([row["code"] for row in self.asked(found)["posting_kind"]["options"]], ["service", "stock"])
        self.assertEqual(run.answer(name, {"posting_kind": "stock"})["status"], run.REVIEWING)


class Confirm(Base):
    def test_confirming_enters_one_document_per_day_once(self):
        name, found = self.reviewed()
        done = run.confirm(name, found["version"])
        self.assertEqual(STATE["admitted"], [("SRC-F1", "assistant:pos:F1", "CONN-E", "2026", "service")])
        self.assertEqual((done["result"]["status"], done["result"]["documents"]), ("ready", ["H1", "H2"]))
        run.confirm(name, found["version"])
        self.assertEqual(len(STATE["admitted"]), 1)

    def test_a_layout_forgotten_before_confirm_goes_back_to_the_columns(self):
        name, found = self.reviewed()
        STATE["forgot"] = True
        back = run.confirm(name, found["version"])
        self.assertEqual((back["outcome"], back["card"]["returned"]["columns"]["reason"]), (run.REFUSED, "pos.layout_invalid"))


if __name__ == "__main__":
    unittest.main()
