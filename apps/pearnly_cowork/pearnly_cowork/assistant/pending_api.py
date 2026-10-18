"""CC 网页「待处理」当场选（ADR-020，补充 乙）：记下会计员选的，再按 CC 推送同一条路重推这一张。

- 可选项由页面在展开那一行时取（`push.application.api.choices`，与复核页、CC 同一套判定），列表本身不读账套主档。
- 作答：`push.application.service.save_choices`（值须在这份账套主档里，定时任务默认处理定下的类别拒收），再
  `tools._push_one`（CC 的 `cowork_push_documents` 每一张走的那条：再看一次要选的项 → 提交 → Express 交 CC 桌面版写入）。
  只记选择不会自动重推：推送记录停在「要人选」，要再推一次才会进账；推出去后这张单的推送状态变了，「待处理」就不再列它。
- 要选的已由公司规则定下（第 2 期，展开时 ``needed`` 为空）：页面交空的 ``items``，这里不记、直接重推。
- 不等写账结论（CC 桌面版写入是几秒到几分钟的事，网页请求不占着等）；结论由推送状态的实时事件带回列表。
- 推送去向取这张单识别时选的连接与账套（`push_connection / push_account_set`）；权限：录入权限 + 这张单的读取范围 +
  这条连接的推送授权（都在被调的服务里判）。
- 停在「待选账套」的（定时任务跟随公司当年账套 T1：没有 / 多于一份 / 够不到单据日期的账套，ADR-021 第 6 批）：
  `account_sets` 列这张单归的公司在推送去向那条连接上覆盖单据日期的账套（与定时任务选账套同一判据
  `companies.tasks.books_of`，按出这张单的任务的公司与分店），`choose_account_set` 只收其中一份：按同一条路重推，推成了才抄到
  票上（`push_state.retarget`），推不成就放回「待选账套」能再选；推送照旧过 `account_match` 的期间、税号、分店硬闸与定时任务的
  默认处理（`push.fixed_for`）。
- 定时任务的票补选后仍由读它所在文件夹的那台电脑写（`folder_intake.push.reader_of` → 推送记录的 ``route_device``，ADR-021
  第 51 条，10-16 审查）：点按钮的会计员只用网页或手机、或账套只在那台电脑上时照样推得进去。CC 对话里主动推送仍按发起人。
"""
import json

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.contracts import document_policy
from pearnly_cowork.history import push_state, storage
from pearnly_cowork.push.application import api as application_api

ITEMS_MAX = 50


@frappe.whitelist(methods=["POST"])
def answer(document, items):
    """记下这张单要人选的项（``items``：``[{kind, subject, value, label}]``，JSON）并重推；回
    ``{"document", "status", "reason"?}``：``sent`` 已交出去（Express 由 CC 桌面版写入）、``needs_choice`` 还有要选的、
    ``failed`` 推不成（原因记在单上）、``waiting_for_cc`` 科目表还在读等。"""
    scope.require("intake")
    name = str(document or "")
    doc = storage.read(name)
    if not doc.push_connection:
        frappe.throw(_("cowork.company.pending.no_target"))
    parsed = json.loads(items) if isinstance(items, str) else items
    if not isinstance(parsed, list) or len(parsed) > ITEMS_MAX:
        frappe.throw(_("connection.config_invalid"))
    if parsed:
        application_api.save_choices(name, doc.push_connection, doc.push_account_set or None,
                                     json.dumps(parsed, ensure_ascii=False))
        frappe.db.commit()
    from pearnly_cowork.assistant import tools  # CC 工具模块很大：只在真要推时加载

    result = tools._push_one(name, route_device=_reader(name, doc.push_connection))
    frappe.db.commit()
    return _answer(name, result)


def _answer(name, result):
    """回给页面的结论：``reason`` 是原因码（页面按它判断），``reason_label`` 是给人看的话（与列表 `reason_label` 同一套词条，
    第 7 批 B7：页面原先把 ``document_policy_needed_payment_evidence`` 这样的码原样标红显示）。"""
    from pearnly_cowork.push import export_facts

    out = {"document": name, "status": str(result.get("status") or "")}
    if result.get("reason"):
        out["reason"] = str(result["reason"])[:300]
        out["reason_label"] = str(export_facts.reason_text(out["reason"]) or "")[:300]
    return out


def _reader(document, connection):
    """定时任务的票补选后仍算定时任务的活：由读它所在文件夹的那台电脑写（`folder_intake.push.reader_of`；不是定时任务的票回空，
    按点按钮的人自己的电脑写）。ADR-021 第 51 条，10-16 审查。"""
    from pearnly_cowork.folder_intake import push as folder_push

    return folder_push.reader_of(document, connection)


def _row(doc):
    return doc if isinstance(doc, dict) else doc.as_dict()


@frappe.whitelist(methods=["GET"])
def account_sets(document):
    scope.require("intake")
    name = str(document or "")
    from pearnly_cowork.companies import pending

    return {"document": name, "account_sets": pending.account_sets_for(_row(storage.read(name)))}


# 推不成、也没进账本的结论：这张单回到「待选账套」，会计员能再选一份（第 6 批审查第 4 条）
NOT_ENTERED = ("failed", "not_found")


@frappe.whitelist(methods=["POST"])
def choose_account_set(document, account_set_key):
    """换到会计员选的那份账套并重推这一张；回同 `answer`。只收 `account_sets` 列出的那几份；不在列（目录刚变、已不覆盖单据
    日期）回 ``status = "stale"``，页面重取可换的账套再让人选，不弹错误（第 6 批审查第 13 条）。

    先推、推成了（进了账本、或停在要人选科目等）才把去向抄到票上（`push_state.retarget`；停下时 `_push_one` 自己抄）。推不成
    也没进账本（`account_match` 硬闸拒了、科目表还没读到等）：票上的去向与「待选账套」原因原样放回，会计员还能再选一份，
    这次推不成的原因只回给页面显示在那一行下面（第 6 批审查第 4 条：以前先抄去向再推，失败后原因被换掉，再也选不了）。"""
    scope.require("intake")
    name = str(document or "")
    from pearnly_cowork.companies import pending

    doc = storage.read(name)
    picked = pending.account_set_pick(_row(doc), account_set_key)
    if not picked:
        return {"document": name, "status": "stale"}
    stop = {"reason": doc.last_push_reason, "connection": doc.push_connection, "account_set": doc.push_account_set,
            "record": doc.last_push_record}
    from pearnly_cowork.assistant import tools  # CC 工具模块很大：只在真要推时加载

    result = tools._push_one(name, doc.push_connection, picked["key"],
                             route_device=_reader(name, doc.push_connection))
    frappe.db.commit()
    status = str(result.get("status") or "")
    reason = str(result.get("reason") or "")
    after = frappe.db.get_value("Cowork OCR History", name, ["last_push_record"], as_dict=True) or {}
    if status in NOT_ENTERED and reason.startswith(document_policy.NEEDED_PREFIX):
        # 账套收下了，挡住它的是「这类单据录不录」（租户选一次，与选哪份账套无关）：去向记成选的这份、原因记成要选录不录，
        # 这张单从「待选账套」转成一般的待处理（交给 CC、复核页选录不录）。放回「待选账套」的话换哪份都是同一个原因，
        # 会计员在这一行永远过不去（第 7 批 B7，2026-10-18 lab）。
        push_state.record_failure(name, reason, connection=doc.push_connection, account_set=picked["key"])
    elif status in NOT_ENTERED and (after.get("last_push_record") or None) == (stop["record"] or None):
        push_state.record_failure(name, stop["reason"], connection=stop["connection"], account_set=stop["account_set"])
    elif status not in NOT_ENTERED:
        push_state.retarget(name, doc.push_connection, picked["key"])
    frappe.db.commit()
    return _answer(name, result)
