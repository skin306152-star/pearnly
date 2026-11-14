"""收款进 CC 的写入计划（第 4 步）：CC 对话里录的收款，放行与单据同一套「复述 → 用户下一句同意 → 开始」
（共用层 `pearnly_core.assistant.write_plan`，评估 §12-1、审定结论①）。

共用层的计划按产品的写账前置判定（`hooks.pearnly_write_precheck` 的 ``check``）逐张判、记下每张的判定摘要，开始时再判一遍、
摘要变了不写。收款在这里给出那一张：ERP 的判定钩子（`express.precheck.check`）遇到 ``R:`` 开头的单号交给 :func:`check`。

- **单号带版本**：``R:<收款记录>@<草稿版本>``（:func:`doc_id`）。CC 复述的是这个版本；草稿之后改过（网页「收款」页弹窗、LINE、
  CC 自己再改），同一个单号判成写不了（``collection.changed``），计划开始时就不写；放行（`cc_tools.push`）也只按单号里的版本放
  （`service.release` 再核一次），不按模型另给的版本。
- **判定**：草稿、版本对得上、确认卡规则全过（凭证齐、发票余额此刻还够）→ 照写（没有要人定的项，``planned`` 带确认卡内容，内容
  一变摘要就变）；主档还在读 → 等电脑；其余 → 写不了并给原因码（`contracts` / `service` 的码）。
- **本对话账套**（A 线 11-12）：在 CC 对话里判时，草稿冻结的账套要就是本对话账套；之后在右栏换了账套（或这笔是 LINE 录在别的账套里的），
  写不了（``collection.book_not_conversation``）。账套里的发票各本各的，不改投。本对话账套未定回 ``ledger_needed``。
- **权限**：本人能用的公司里的收款，不分谁录的（用户 11-14；别家公司的不回、不报存在与否，同单据）；要 ``collection.write``。
"""
import frappe

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.collection import contracts
from pearnly_erp_portal.express import contracts as express_contracts
from pearnly_erp_portal.masters import company as companies

PREFIX = "R"
WRITE = "collection.write"  # 同 `service.WRITE`（本模块由单据的判定钩子导入，服务层用到时才导入）
CHECK = "check"
NOT_WRITABLE = "not_writable"
WAITING = "waiting"  # 同共用层 `write_precheck.contracts` 的单据状态（这里不导入共用层的判定契约）
CONNECTION = "express"


def doc_id(name, version) -> str:
    """收款记录 + 草稿版本 → 写入计划里的单号。"""
    return f"{PREFIX}:{name}@{int(version or 0)}"


def parse(value) -> tuple:
    """``R:<名>@<版本>`` → (名, 版本)；不是收款的单号回 (``""``, None)。"""
    prefix, sep, rest = str(value or "").partition(":")
    name, at, version = rest.rpartition("@")
    if prefix != PREFIX or not sep or not at or not name or not version.isdigit():
        return "", None
    return name, int(version)


def mine(value) -> bool:
    return str(value or "").startswith(PREFIX + ":")


def allowed(user, company) -> bool:
    """收款员（``collection.write``）对本人可用的这家公司可以建计划。"""
    return bool(user) and user != "Guest" and guard.has(WRITE, user) and company in companies.allowed_companies(user)


def _planned(card) -> dict:
    """确认卡要复述的内容（判定摘要里带上：内容一变摘要就变）。"""
    keep = ("name", "version", "company_name", "book", "customer", "customer_name", "doc_date", "invoices", "cash",
            "methods", "transfer", "discount", "total", "salesman", "department")
    return {key: card.get(key) for key in keep}


def _other_book(company, book_key) -> str:
    """在 CC 对话里：草稿的账套不是本对话账套回原因码，是（或不在对话里、对话挂的不是这家）回空串。"""
    from pearnly_erp_portal.collection import service
    from pearnly_erp_portal.express import conversation

    pin = conversation.pinned({"name": company})
    if not pin:
        return ""
    if not pin["key"]:
        return service.LEDGER_NEEDED
    return "" if pin["key"] == book_key else service.BOOK_NOT_CONVERSATION


def view(company, value) -> dict | None:
    """一张收款的判定（`write_precheck.contracts.merge` 的输入）；不是本人这家公司的回 None。"""
    from pearnly_erp_portal.collection import service

    name, version = parse(value)
    if not name or not guard.has(WRITE):
        return None
    try:
        card = service.card(name)
    except frappe.PermissionError:
        return None
    if card["company"] != company:
        return None
    book_key = frappe.db.get_value(service.DOCTYPE, name, "book_key") or ""
    out = {"doc_id": value, "ref": name, "party": card.get("customer_name") or "", "record": name,
           "target": {"connection": CONNECTION, "connection_title": "Express", "account_set": book_key,
                      "account_set_title": card.get("book") or ""}}
    if card["status"] != contracts.DRAFT:
        return {**out, "state": NOT_WRITABLE, "reason": contracts.NOT_DRAFT}
    if card["version"] != version:
        return {**out, "state": NOT_WRITABLE, "reason": contracts.CHANGED}
    other = _other_book(company, book_key)
    if other:
        return {**out, "state": NOT_WRITABLE, "reason": other}
    if not card.get("ready"):
        problem = card.get("problem") or contracts.CHANGED
        return {**out, "state": WAITING if problem == express_contracts.READING else NOT_WRITABLE, "reason": problem}
    return {**out, "state": CHECK, "items": [], "planned": _planned(card)}


def check(company, doc_ids) -> dict:
    """``R:`` 单号这一批的判定（`express.precheck.check` 交过来的那几张）。"""
    found = [view(company, value) for value in doc_ids if mine(value)] if any(mine(value) for value in doc_ids) else []
    return {"documents": [item for item in found if item]}


__all__ = ["PREFIX", "allowed", "check", "doc_id", "mine", "parse", "view"]
