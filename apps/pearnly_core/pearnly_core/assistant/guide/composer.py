"""输入框里交来的票据进单据引导（多产品共用，11-28 testerp「点采购后用输入框 + 上传，不进识别」）。

按单据办的业务（采购、销售，声明 ``source: documents``）的识别只有一个入口：单据引导交文件那一题（`run.answer` → `files.commit(via="guide")`
→ 产品 ``intake``）。用户不点卡上的上传、直接在输入框挂文件发出，原来直写会话只记「已交出」、什么都不识别（ERP 直写的模型也没有识别工具），
停在「文件在会话里」。这里把它接住，直写与代录、ERP 与 Cowork 同一套判据：

- 选了按单据的业务（菜单上点过）：发出的这几份交进这段对话这项业务的单据引导——有开着、还在交文件那一步的同业务引导就交进它，
  没有开着的同业务引导就开一次（开新的会照旧取消别的业务那条）；同业务引导已过了交文件（在识别 / 题卡 / 复核）不动它，由模型照常处理。
  开引导在发出前（同一请求，便宜），交文件（存原件、交识别要走网络）在发出后由后台做（:func:`hand_in`，同代录 ``files.start_sent``）；
  交不成卡停在交文件那一题，用户在卡上再交。识别按对话文件编号去重（产品 ``intake`` 的请求键），模型再调识别工具也不会再扣一次。
- 没选业务、这个产品这个模式有按单据的业务：不猜方向，告诉模型问一次是哪项业务，再用 ``pearnly_guide`` 带 ``business`` 与 ``files`` 开
  （:func:`open_with_files`，打字路径同一个入口）。
- 代录会话：发出即由代录任务识别（``files.start_sent``），开始卡照旧，这里不接。

识别完照引导走题卡、复核、确定，确定前不写账（11-09「识别后必须复核再录」）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

import frappe

from pearnly_core.assistant.guide import declaration as d
from pearnly_core.assistant.guide import registry

HAND_JOB = "pearnly_core.assistant.guide.composer.hand_in"
FILES_MAX = 20   # 一次交几份（同产品 intake 的上限）


def documents_businesses(session_id: str, mode: str) -> List[str]:
    """这段对话此刻（本人、这个模式、这家公司的账本种类）能开的按单据办的业务（菜单同一份，`guided.offered_for`）。"""
    from pearnly_core.assistant import guided

    offered = guided.offered_for(session_id).get(mode, [])
    return [guide["id"] for guide in registry.declared()
            if guide["id"] in offered and guide.get("source") == d.DOCUMENTS
            and all(guide.get(key) for key in d.DOCUMENT_HOOKS)]


def _run_taking_files(session_id: str, business: str) -> str | None:
    """开着、同业务、还在交文件那一步的单据引导（交进它）→ 运行号；没有开着的同业务引导 → ""（要开新的）；
    同业务引导已过交文件 → None（不动它）。"""
    from pearnly_core.assistant.guide import documents, run

    for name in run._open_runs(session_id):
        doc = frappe.get_doc(run.DOCTYPE, name)  # loop-query: 一段对话同一时刻最多一条开着的引导（_open_runs 有界）
        if doc.business != business:
            continue
        state = documents.state(doc)
        return name if not state.get("preset") and not state["batches"] else None
    return ""


def plan(row: Mapping[str, Any], text: str) -> Dict[str, Any]:
    """发出前（`sessions.send`）：这句带着的本人上传文件怎么接。回 ``{"business", "run", "files"}``（交进这次引导，引导已开好）/
    ``{"ask": [业务…], "files"}``（没选业务，叫模型问一次）/ ``{"busy": 业务, "files"}``（同业务的卡还开着、已过交文件）/
    ``{}``（不接）。出错不挡发消息，只记日志。"""
    if row.get("operator_task"):
        return {}
    from pearnly_core.assistant import files

    ids = files.ids_in(row, text)[:FILES_MAX]
    if not ids:
        return {}
    try:
        offered = documents_businesses(row.session_id, "chat")
        business = str(row.get("business") or "")
        if business in offered:
            name = _run_taking_files(row.session_id, business)
            if name is None:
                # 同业务的卡还开着、已过交文件（上一张在识别 / 题卡 / 复核）：不另开（会把那张作废），叫模型请用户先办完或取消再交
                return {"busy": business, "files": ids}
            if not name:
                from pearnly_core.assistant.guide import run

                name = str(run.start(row.session_id, business).get("run") or "")
            return {"business": business, "run": name, "files": ids} if name else {}
        if not business and offered:
            return {"ask": offered, "files": ids}
    except Exception:  # noqa: BLE001 — 接不住就照旧发消息（模型按说明处理），不让这句发不出
        frappe.db.rollback()
        frappe.log_error(title="Assistant composer files to guide failed")
    return {}


def queue(planned: Mapping[str, Any]) -> None:
    """发出后：排后台把这几份交进那次引导（提交后才排，任务里读得到引导与文件留存）。"""
    if not planned.get("run"):
        return
    frappe.enqueue(HAND_JOB, queue="default", enqueue_after_commit=True, run_name=planned["run"],
                   file_ids=list(planned["files"]), user=frappe.session.user,
                   lang=getattr(frappe.local, "lang", None) or "")


def hand_in(run_name: str, file_ids: List[str], user: str, lang: str = "") -> None:
    """后台任务：以发消息的人、按那时的界面语言，把输入框交的几份当作这次引导交文件那一题的回答（`run.answer`，唯一入口：
    登记交出、产品 ``intake`` 交识别）。引导已不在交文件（用户先在卡上交了、取消了）就不交；交不成只记日志，卡停在交文件那一题。"""
    from pearnly_core.assistant.guide import documents, run

    frappe.set_user(user)
    if lang:
        from pearnly_core.execution import language

        language.settle(lang)
    try:
        found = run.answer(run_name, {documents.FILE_KEY: list(file_ids)})
        frappe.db.commit()
        if found.get("outcome"):
            frappe.logger("pearnly.assistant").info({"event": "composer_files_not_taken", "run": run_name,
                                                     "outcome": found.get("outcome"), "refused": found.get("refused")})
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title="Assistant composer files hand-in failed")


def open_with_files(session_id: str, business: str, refs) -> Dict[str, Any]:
    """打字路径（``pearnly_guide`` 带 ``business`` 与 ``files``）：在这段对话开这项业务的单据引导，并把这几份当场交进去识别。
    回给模型的结果 ``{status: opened, run, files} | {status: refused, reason}``（英文原因码，模型照实转述）。"""
    from pearnly_core.assistant import files, sessions
    from pearnly_core.assistant.guide import documents, run

    row = sessions.owned(session_id)
    if row.get("operator_task"):
        # 代录会话：发出即由代录任务识别（`files.start_sent`），不另开引导再识别一遍
        return {"status": "refused", "reason": "operator_takes_files_on_send"}
    if business not in documents_businesses(row.session_id, "chat"):
        return {"status": "refused", "reason": "business_not_offered", "offered": documents_businesses(row.session_id, "chat")}
    given = [str(ref) for ref in refs or () if str(ref or "").strip()][:FILES_MAX]
    ids = list(dict.fromkeys(files.resolve(ref) for ref in given))  # loop-query: 模型给的文件，最多 FILES_MAX 份，逐份按本对话认编号
    if not ids:
        return {"status": "refused", "reason": "no_files"}
    name = _run_taking_files(row.session_id, business)
    if not name:
        name = str(run.start(row.session_id, business).get("run") or "")
    found = run.answer(name, {documents.FILE_KEY: ids})
    if found.get("outcome"):
        return {"status": "refused", "reason": "files_not_taken", "run": name}
    return {"status": "opened", "run": name, "files": ids}


__all__ = ["HAND_JOB", "documents_businesses", "hand_in", "open_with_files", "plan", "queue"]
