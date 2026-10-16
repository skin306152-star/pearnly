"""统一异常清单（数字会计员第 1 期）：一套稳定的码，「待处理」与月结台账共用；页面按码翻译，服务端只给码。纯函数，不做 IO。

码表 `CODES`：码 → (段, 严重度, 去处)。
- 段（`STAGES`）：收单 intake / 录账 entry / 核对 verify（Express 回读）/ 报税 tax。第 1 期不出对账与签字两段（第 6 期）。
- 严重度：action（要人处理）、warn（要看一眼）、info（只列、不催）。段的状态只看 action / warn（`stage_state`）。
- 去处（页面点开去哪）：choose 当场选、push 当场推（CC 准备好的提议，数字会计员第 3 期）、open 交给 CC 看这张单、
  task 定时任务、readback 回读、draft 申报草稿。
- 自动推送（第 3 期第 11 条）：``cc_proposal`` 提议（A / B 类在 L0 / L1 冻结了 CC 准备好的做法，人点「推送」）、
  ``cc_judged`` 整月档自动推的 B 类（CC 按数据判断的项，只列，随第 6 期签字了结）、``written_mismatch`` 推后核对写入与预期
  不符（不回滚、不重推，交给 CC 看）。
- 单据层的码在待处理的粗分（`pending.classify` 的 `REASONS`，即 ``group``）里再细分：推送拦下的分店 / 税号 / 期间不符按
  `targets.account_match.CONFLICT_MESSAGES` 认回码，其余停下 / 失败是 push_manual / push_failed。
- 重复单号只列（info）：推送照写不拦（用户定「推送不查重不拦」）。
- 保留不实现：`RESERVED`（本月缺单第 5 期、规则冲突第 4 期、重开月份第 6 期）。
"""
from __future__ import annotations

from pearnly_cowork.companies import identity, pending
from pearnly_cowork.targets import account_match
from pearnly_cowork.targets.identity import normalize_express_account_key

STAGES = ("intake", "entry", "verify", "tax")
STATES = ("empty", "open", "attention", "clear")
ACTION, WARN, INFO = "action", "warn", "info"
SEVERITIES = (ACTION, WARN, INFO)
CODES = {
    "choice_needed": ("entry", ACTION, "choose"),
    "result_unknown": ("entry", ACTION, "open"),
    account_match.TAX_MISMATCH: ("entry", ACTION, "open"),
    account_match.PERIOD_MISMATCH: ("entry", ACTION, "open"),
    account_match.BRANCH_MISMATCH: ("entry", ACTION, "open"),
    "push_manual": ("entry", ACTION, "open"),
    "push_failed": ("entry", ACTION, "open"),
    "not_pushed": ("entry", ACTION, "open"),
    "cc_proposal": ("entry", ACTION, "push"),
    "cc_judged": ("entry", INFO, "open"),
    "written_mismatch": ("verify", ACTION, "open"),
    "duplicate_number": ("intake", INFO, "open"),
    "task_folder_missing": ("intake", ACTION, "task"),
    "intake_file_failed": ("intake", ACTION, "task"),
    "pos_layout_needed": ("intake", ACTION, "task"),
    "not_invoice": ("intake", INFO, "task"),
    "express_changed": ("verify", INFO, "readback"),
    "express_deleted": ("verify", WARN, "readback"),
    "express_voided": ("verify", INFO, "readback"),
    "express_missing": ("verify", WARN, "readback"),
    "express_direct": ("verify", INFO, "readback"),
    "readback_needed": ("verify", INFO, "readback"),
    "tax_issue": ("tax", WARN, "draft"),  # 严重度随草稿自己的问题级别（`TAX_LEVELS`）
    "tax_draft_stale": ("tax", WARN, "draft"),
}
RESERVED = ("missing_document", "rule_conflict", "month_reopened")
CONFLICTS = (account_match.TAX_MISMATCH, account_match.PERIOD_MISMATCH, account_match.BRANCH_MISMATCH)
_CONFLICT_TEXTS = {text: code for code, text in account_match.CONFLICT_MESSAGES.items()}
TAX_LEVELS = {"error": ACTION, "warn": WARN, "info": INFO}
EXPRESS_CHANGES = ("changed", "deleted", "voided", "missing", "direct")
FILE_CODES = {"failed": "intake_file_failed", "pos_layout_needed": "pos_layout_needed", "not_invoice": "not_invoice"}
WAITING = ("choice_needed", "result_unknown", "manual", "failed")


def conflict_code(reason) -> str:
    """推送拦下的原因是不是「账套与票面对不上」：存的是 `CONFLICT_MESSAGES` 的原句（推送记原因时还原成原文），
    或直接是码（`码` / `码:细节` / `[码] …`）。是就回码，不是回空串。"""
    text = str(reason or "").strip()
    if text in _CONFLICT_TEXTS:
        return _CONFLICT_TEXTS[text]
    head = text.lstrip("[").split("]", 1)[0].split(":", 1)[0].strip().lower()
    return head if head in CONFLICTS else ""


def document_code(row, cutoff, unknown=frozenset()):
    """一张单的 (稳定码, 待处理粗分)；不用处理回 None。粗分同 `pending.classify`。"""
    group = pending.classify(row, cutoff, unknown)
    if not group:
        return None
    if group in ("manual", "failed"):
        return (conflict_code(row.get("last_push_reason")) or ("push_manual" if group == "manual" else "push_failed"),
                group)
    return group, group


def item(code, **fields) -> dict:
    """一条异常：`{code, stage, severity, action, group, document, …}`；``fields`` 可覆盖严重度与去处。"""
    stage, severity, action = CODES[code]
    out = {"code": code, "stage": stage, "severity": severity, "action": action, "group": None, "document": None}
    out.update(fields)
    return out


def party_tax(row, company_tax="") -> str:
    """对方税号：本方是卖方就是买方，否则是卖方（同 `pending.counterparty`）。"""
    mine = identity.digits(company_tax)
    if mine and identity.digits(row.get("seller_tax")) == mine:
        return identity.digits(row.get("buyer_tax"))
    return identity.digits(row.get("seller_tax"))


def number_key(value) -> str:
    return "".join(str(value or "").split()).casefold()


def month_duplicates(rows, company_tax="", live=None) -> dict:
    """月内重复单号：同一对方税号 + 同一单号出现不止一张 → ``{单据: [同号的其他单据]}``；另加识别时查重判为同号的
    （``duplicate_check_status = matched`` 且 ``duplicate_level = exact``，带 ``duplicate_match``）。识别查重的另两级
    （likely 同日同卖方同额、party 同对方同日同额）不是同号，不算；``live`` 给了就只认被匹配那张还在的（月内的单据一定在），
    被匹配的那张删了不再标。没有单号或对方税号的不比。"""
    groups = {}
    for row in rows:
        number, party = number_key(row.get("invoice_no")), party_tax(row, company_tax)
        if number and party:
            groups.setdefault((party, number), []).append(row.get("name"))
    out = {}
    for names in groups.values():
        if len(names) > 1:
            for name in names:
                out[name] = [other for other in names if other != name]
    here = {row.get("name") for row in rows}
    for row in rows:
        match = exact_match(row)
        if not match or (live is not None and match not in here and match not in live):
            continue
        found = out.setdefault(row.get("name"), [])
        if match not in found:
            found.append(match)
    return out


def exact_match(row) -> str:
    """识别查重判为同号（exact）的那张；不是同号的回空串。"""
    match = row.get("duplicate_match")
    if row.get("duplicate_check_status") != "matched" or row.get("duplicate_level") != "exact":
        return ""
    return match if match and match != row.get("name") else ""


def readback_items(sets, ended, needed=True) -> list:
    """回读的发现（Express 里改了、删了、作废了、找不到 CC 推的单，或直接在 Express 记的）；月份已结束、还没有完成的回读的
    账套给 readback_needed——``needed`` 为假（这个月没推过、也没有申报草稿，没有可核对的）时不给。
    ``sets``：`readback.service.month_status` 的形状。"""
    out = []
    for found in sets or ():
        where = {"connection": found.get("connection"), "account_set_key": found.get("key"),
                 "label": found.get("label") or "", "readback": found.get("readback")}
        if found.get("status") == "ready":
            counted = found.get("counts") or {}
            for change in EXPRESS_CHANGES:
                if int(counted.get(change) or 0) > 0:
                    out.append(item(f"express_{change}", count=int(counted[change]), **where))
        elif ended and needed and found.get("status") != "reading":
            out.append(item("readback_needed", **where))
    return out


def tax_items(drafts, latest) -> list:
    """申报草稿的问题（按草稿自己的问题级别）与过期的 ภ.พ.30 草稿（出草稿用的回读不是这本账套最近完成的那份）。
    ``drafts``：`tax.service.month_drafts` 的 ``drafts``；``latest``：``{(连接, 账套键小写): 最近完成的回读}``。"""
    out = []
    for draft in drafts or ():
        where = {"draft": draft.get("name"), "kind": draft.get("kind"), "branch": draft.get("branch") or ""}
        for issue in draft.get("issues") or ():
            out.append(item("tax_issue", severity=TAX_LEVELS.get(issue.get("level"), WARN), issue=issue.get("code"),
                            issue_label=str(issue.get("label") or ""), count=int(issue.get("count") or 0), **where))
        if draft.get("kind") == "vat_return" and draft.get("source_ref"):
            key = (draft.get("connection") or "",
                   (normalize_express_account_key(draft.get("account_set")) or "").casefold())
            newest = latest.get(key)
            if newest and newest != draft["source_ref"]:
                out.append(item("tax_draft_stale", readback=newest, **where))
    return out


def file_items(files, tasks) -> list:
    """定时任务的文件（`folder_intake.rules.month_files`）与文件夹不在了的任务。``tasks``：``{任务: {title, missing}}``。"""
    out = []
    for name, task in tasks.items():
        if task.get("missing"):
            out.append(item("task_folder_missing", task=name, title=task.get("title") or ""))
    for name, counted in (files or {}).items():
        for state, code in FILE_CODES.items():
            if int(counted.get(state) or 0) > 0:
                out.append(item(code, task=name, title=(tasks.get(name) or {}).get("title") or "",
                                count=int(counted[state])))
    return out


def weight(found) -> int:
    return int(found.get("count") or 1)


def tally(items) -> dict:
    """``{码: 件数}``（汇总的条目按 ``count`` 算）。"""
    out = {}
    for found in items:
        out[found["code"]] = out.get(found["code"], 0) + weight(found)
    return out


def severities(items) -> dict:
    """``{action, warn, info: 条数}``（一条一行，汇总的也算一条）：页面卡头与组标题的数，清单截断了也是全数。"""
    out = dict.fromkeys(SEVERITIES, 0)
    for found in items:
        out[found["severity"]] = out.get(found["severity"], 0) + 1
    return out


def ordered(items) -> list:
    """要人处理的在前（action → warn → info），同级按段（收单 → 录账 → 核对 → 报税），再按日期新 → 旧。"""
    rank = {severity: index for index, severity in enumerate(SEVERITIES)}
    stage = {name: index for index, name in enumerate(STAGES)}
    by_date = sorted(items, key=lambda found: str(found.get("date") or ""), reverse=True)
    return sorted(by_date, key=lambda found: (rank.get(found["severity"], 9), stage.get(found["stage"], 9)))


def stage_state(total, items, open_=False) -> str:
    """一段的状态：有 action / warn 的异常 → attention；什么都没有 → empty；还在进行（本月没完、在推、还没回读）→ open；
    否则 clear。"""
    if any(found["severity"] in (ACTION, WARN) for found in items):
        return "attention"
    if not total and not items:
        return "empty"
    return "open" if open_ else "clear"


def period_ended(period, today) -> bool:
    """这个月（`YYYY-MM`）过完了没有（``today``：`date`）。"""
    return period < f"{today.year:04d}-{today.month:02d}"


__all__ = ["CODES", "RESERVED", "STAGES", "STATES", "conflict_code", "document_code", "file_items", "item",
           "exact_match", "month_duplicates", "ordered", "period_ended", "readback_items", "severities", "stage_state", "tally", "tax_items"]
