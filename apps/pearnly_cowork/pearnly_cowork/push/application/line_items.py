"""LINE 识别后按行追问存货对照用到的推送侧四件事（11-06 按行选物料与库存，评估 docs/work/2026-11-06-erp-line-item-stock-choice-评估.md 第 15 节 C7）。

LINE 域不导入推送 / 单据域（`architecture-policy.json` 的 `cowork_domain_dependencies`），经钩子 ``cowork_line_items`` 取本模块
（同对账经 ``cowork_recon_known_numbers`` 取推送记录的做法）。判定、记法与复核页「要选的项」、CC 卡片是同一套：

- :func:`rows`：这张票推往的 Express 账套里定不了的行（共用写账规则 ``needed`` 里 ``kind = item`` 且 ``expense``）；多仓账套还没记过
  默认仓时末尾跟一项 ``kind = location``（同一段追问里接着问一次仓，11-08；没有要问的行就不单为仓问）；
- :func:`title`：提问开头的「对方 · 单号」；
- :func:`save`：答案记进这份账套的记忆（`service.save_choices`）；
- :func:`search`：「选已有存货」打字找（这份账套的库存品与非库存品，按编码或名称包含）。

都按当前会话用户判权限（调用方已切成绑定的这个账号）；只看的几件做完回滚（读推送目标时锁了连接）。
"""
import json

import frappe

from pearnly_cowork.history import storage
from pearnly_cowork.push.application import service


def rows(history) -> list:
    """``[{kind, subject, label}]``（仓库那一项另带 ``options`` ``[{code, name}]``，主仓在前）；不是 Express 去向、账套主档还没读到、
    判不了的回空（不问，照推送时的规则走）。"""
    doc = storage.read(history)
    if (doc.get("intake_target") or "") != "express" or not doc.get("push_connection"):
        return []
    try:
        view = service.choices_view(history, doc.push_connection, doc.get("push_account_set") or None)
    except (frappe.ValidationError, frappe.PermissionError):
        frappe.clear_messages()
        return []
    finally:
        frappe.db.rollback()  # 判定只看不写；识别结果在发卡片前已提交
    if not isinstance(view, dict) or view.get("wait"):
        return []
    needed = view.get("needed") or []
    found = [{"kind": "item", "subject": item["subject"], "label": str(item.get("label") or "")[:50]}
             for item in needed if item.get("kind") == "item" and item.get("expense")]
    location = next((item for item in needed if item.get("kind") == "location"), None)
    if found and location:
        main = str(location.get("current") or "")
        options = sorted(location.get("options") or [], key=lambda row: row.get("code") != main)
        found.append({"kind": "location", "subject": location["subject"], "label": "",
                      "options": [{"code": str(row.get("code") or ""), "name": str(row.get("name") or "")}
                                  for row in options if row.get("code")]})
    return found


def title(history) -> str:
    doc = storage.read(history)
    pages = json.loads(doc.get("pages") or "[]") if isinstance(doc.get("pages"), str) else doc.get("pages") or []
    fields = next((page.get("fields") or {} for page in pages if isinstance(page, dict) and not page.get("is_copy")), {})
    party = fields.get("buyer_name") if fields.get("direction") == "sales" else fields.get("seller_name")
    return " · ".join(str(part) for part in (party, fields.get("invoice_number")) if part)


def save(history, items) -> None:
    """``items`` = ``[{kind: item | location, subject, value, label}]``：记进这张票推往的账套（同复核页「要选的项」）。"""
    doc = storage.read(history)
    service.save_choices(history, doc.push_connection, doc.get("push_account_set") or None, json.dumps(items))
    frappe.db.commit()


def search(history, text, limit) -> list:
    """这份账套的存货（库存品与非库存品）里编码或名称包含 ``text`` 的，最多 ``limit`` 条 ``[{code, name}]``。只看不写。"""
    from pearnly_cowork.connections import service as connections

    doc = storage.read(history)
    if not doc.get("push_connection"):
        return []
    try:
        loaded = connections.read_for_push(doc.push_connection, doc.workspace, doc.client, lock=True)
        found = service._express_context(doc, loaded, doc.get("push_account_set") or None)
    except (frappe.ValidationError, frappe.PermissionError):
        frappe.clear_messages()
        return []
    finally:
        frappe.db.rollback()
    needle = str(text or "").strip().casefold()
    rows_ = found["selected"]["config"].get("reported_products") or []
    matched = [{"code": str(row.get("code") or ""), "name": str(row.get("name") or "")} for row in rows_
               if isinstance(row, dict) and row.get("code")
               and (needle in str(row.get("code")).casefold() or needle in str(row.get("name") or "").casefold())]
    return matched[:limit]


__all__ = ["rows", "save", "search", "title"]
