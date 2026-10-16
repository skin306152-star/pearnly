"""定时任务跟随公司当年账套（T1，2026-10-15）：推一张票前，按这张票的单据日期选这次推往哪份账套。

任务存下时记了公司主档（`company`）与分店（`company_branch`，可空；见 `rules.save`），这里按单据日期在这家公司、这条连接
上取覆盖那天的账套（有分店按分店筛）：
- 正好一份、读这个文件夹的那台电脑够得到（写账分派同一判据 `push.native.service.has_writer`，ADR-021 第 51 条）→ 推往它；
- 一份都没有（含票上没有日期）、多于一份（如 Express 测试数据 T8 / T9 都是 2025 年）、或那台电脑够不到这份账套 →
  停下等人，不猜；原因码见下，三种语言在本 App 术语表。
任务没记公司（旧任务对不上公司、不是 Express）或主档已删 → 不跟随，照旧推往任务的固定账套 `account_set_key`。

公司与账套由 companies 现算，本域不导入它（companies 依赖 folder_intake）：经 hooks ``cowork_task_company`` /
``cowork_task_books``（`companies.tasks`）。推送照旧过 `account_match` 的期间、税号与分店硬闸。
"""
import frappe

from pearnly_cowork.connections import preparation
from pearnly_cowork.folder_intake import contracts
from pearnly_cowork.push.native import service as ledger
from pearnly_cowork.targets.identity import normalize_express_account_key

HISTORY = "Cowork OCR History"
CONNECTION = "Cowork ERP Connection"
NONE, AMBIGUOUS, UNREACHABLE = contracts.ACCOUNT_SET_STOPS
# 默认值里是账套主档代码的几类：跟到别的年度账套时，只在那份账套的主档里有才用
MASTER_KINDS = {"account": "reported_accounts", "department": "reported_departments"}


def _first(hook, *args):
    """第一个回了值（不是 None）的提供者；没有提供者回 None。"""
    for path in frappe.get_hooks(hook) or []:
        found = frappe.get_attr(path)(*args)
        if found is not None:
            return found
    return None


def company_of(connection, account_set_key):
    """保存任务时：所选账套属于唯一一家公司主档 → ``{"company", "branch"}``；对不上、多于一家回空的两项。"""
    found = _first("cowork_task_company", connection, account_set_key) or {}
    return {"company": str(found.get("company") or "")[:140], "branch": str(found.get("branch") or "")[:10]}


def books(rule, day=None):
    """这条任务所跟公司在任务连接上的账套（``day`` 给了只留覆盖那天的）；任务没记公司、主档已删回 None。"""
    company = getattr(rule, "company", None) or ""
    if not company:
        return None
    return _first("cowork_task_books", company, rule.connection, day, getattr(rule, "company_branch", None) or "")


def member(rule, account_set):
    """这份账套是不是这条任务跟的公司的（分店筛过）：人在复核页 / CC 重推停下的票时，任务默认处理照样套用。"""
    key = normalize_express_account_key(account_set)
    return bool(key) and any(normalize_express_account_key(item.get("key")) == key for item in books(rule) or ())


def choose(rule, history, device=None):
    """这张票这次推往哪份账套：``None`` = 不跟随（用任务的固定账套）；``{"key", "label"}`` = 选到的；
    ``{"stop": 原因码, "key"?}`` = 停下等人（够不到时带上选到的那份）。``device``：读这张票所在文件夹的电脑（`push._reader`：
    文件行记的优先，与推送冻结的 ``route_device`` 同一台，10-16 审查）；不给按任务上的。只读。"""
    if not getattr(rule, "company", None):
        return None
    day = frappe.db.get_value(HISTORY, history, "invoice_date")
    day = str(day or "")[:10]
    found = books(rule, day or None)
    if found is None:
        return None
    if not day or not found:
        return {"stop": NONE}
    if len(found) > 1:
        return {"stop": AMBIGUOUS}
    book = found[0]
    # 够不够得到按读这张票所在文件夹的那台电脑判（只有它写，ADR-021 第 51 条；旧任务没记电脑时按负责人自己的电脑）
    reader = device if device is not None else (getattr(rule, "reader_device", None) or "")
    if book.get("writable") is False or not ledger.has_writer(
            rule.connection, book["key"], actor=frappe.session.user, route_device=reader or ""):
        return {"stop": UNREACHABLE, "key": book["key"], "label": book.get("label") or book["key"]}
    return {"key": book["key"], "label": book.get("label") or book["key"]}


def fixed_in(rule, account_set, fixed):
    """任务默认值放到跟到的这份账套里：就是任务存下的那份账套原样用；别的年度账套，科目、部门代码只在那份账套读到的
    主档里有才用（没读到也先不用，推送照现有规则请电脑读、读到后再推）；其余各类原样。"""
    key = normalize_express_account_key(account_set)
    if key == normalize_express_account_key(rule.account_set_key) or not any(kind in fixed for kind in MASTER_KINDS):
        return fixed
    doc = frappe.get_doc(CONNECTION, rule.connection)
    masters = preparation.reported_masters(doc, key) or {}
    kept = dict(fixed)
    for kind, field in MASTER_KINDS.items():
        if kind not in kept:
            continue
        codes = {str(row.get("code")) for row in masters.get(field) or [] if isinstance(row, dict) and row.get("code")}
        if kept[kind] not in codes:
            kept.pop(kind)
    return kept
