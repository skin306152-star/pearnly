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
  `companies.tasks.books_of`），`choose_account_set` 只收其中一份：抄到票上（`push_state.retarget`）再按同一条路重推，
  推送照旧过 `account_match` 的期间、税号、分店硬闸与定时任务的默认处理（`push.fixed_for`）。
"""
import json

import frappe
from frappe import _

from pearnly_cowork.access import scope
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

    result = tools._push_one(name)
    frappe.db.commit()
    out = {"document": name, "status": str(result.get("status") or "")}
    if result.get("reason"):
        out["reason"] = str(result["reason"])[:300]
    return out


def _row(doc):
    return doc if isinstance(doc, dict) else doc.as_dict()


@frappe.whitelist(methods=["GET"])
def account_sets(document):
    scope.require("intake")
    name = str(document or "")
    from pearnly_cowork.companies import pending

    return {"document": name, "account_sets": pending.account_sets_for(_row(storage.read(name)))}


@frappe.whitelist(methods=["POST"])
def choose_account_set(document, account_set_key):
    """换到会计员选的那份账套并重推这一张；回同 `answer`。只收 `account_sets` 列出的那几份（别的回
    ``cowork.folder.account_set_invalid``）。"""
    scope.require("intake")
    name = str(document or "")
    from pearnly_cowork.companies import pending

    doc = storage.read(name)
    picked = pending.account_set_pick(_row(doc), account_set_key)
    if not picked:
        frappe.throw(_("cowork.folder.account_set_invalid"))
    push_state.retarget(name, doc.push_connection, picked["key"])
    frappe.db.commit()
    from pearnly_cowork.assistant import tools  # CC 工具模块很大：只在真要推时加载

    result = tools._push_one(name, doc.push_connection, picked["key"])
    frappe.db.commit()
    out = {"document": name, "status": str(result.get("status") or "")}
    if result.get("reason"):
        out["reason"] = str(result["reason"])[:300]
    return out
