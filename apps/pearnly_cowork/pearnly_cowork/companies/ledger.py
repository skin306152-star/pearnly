"""月结台账（数字会计员第 1 期）：一家公司一个月走到哪一步、哪里要人处理。每次请求现算，不存。

- 四段：收单 intake（单据日期在本月、归这家的识别记录，定时任务（含跟着这家公司走当年账套的，T1）与它们这个月登记的文件）、录账 entry（推送结果）、
  核对 verify（Express 回读，只有 Express 公司有）、报税 tax（申报草稿）。每段给计数与状态（`exceptions.stage_state`）。
- 统一异常清单：码见 `exceptions.CODES`，与「待处理」同一套；只给码与数据，文案在页面。``items`` 最多 ``limit`` 条，
  ``total`` / ``counts``（按码，汇总的按件数）/ ``severities``（按严重度的条数）都按全部算，页面卡头与组标题用它们。
- 单据：一次有界查询（原生行权限 `get_list`，按单据日期在本月、本方税号或推往这家的账套），最多 `LEDGER_MAX` 张，超了
  ``truncated``；没有单据日期的另查一次（本月上传的）。取回后用 `pending.company_of` 再认一次归属（同待处理）。
  其余各段一段一次查询：结果不明的推送记录、定时任务、任务文件（`folder_intake.rules.month_files`）、回读
  （hooks ``cowork_company_month_readback``，companies 不导入 readback）、申报草稿（`tax.service.month_drafts`）。
- 权限：读同主档页（`scope.require_read`）；定时任务与核对两段要录入权限（没有给 None），回读另逐条连接核（能用这条
  连接的人都看得到，用户 2026-10-15 定）；报税一段要报税权限（没有给 None）。第 1 期没有写接口、不出对账与签字两段。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.companies import exceptions, identity, pending, service
from pearnly_cowork.folder_intake import agent
from pearnly_cowork.folder_intake import readers as folder_readers
from pearnly_cowork.folder_intake.access import RULE
from pearnly_cowork.targets.identity import normalize_express_account_key

HISTORY = pending.HISTORY
LEDGER_MAX = 5000
LIMIT_MAX = 200
READBACK_HOOK = "cowork_company_month_readback"
FIELDS = pending.FIELDS + ["duplicate_check_status", "duplicate_level", "duplicate_match"]
FOLDER_MISSING = ("root_missing", "folder_missing")


def _period(value) -> str:
    text = service._period(value)
    if not text:
        frappe.throw(_("cowork.company.period_invalid"))
    return text


def _limit(value) -> int:
    try:
        return max(1, min(int(value or LIMIT_MAX), LIMIT_MAX))
    except (TypeError, ValueError):
        return LIMIT_MAX


def bounds(period) -> tuple[date, date]:
    """这个月的第一天与下个月的第一天。"""
    year, month = int(period[:4]), int(period[5:7])
    return date(year, month, 1), date(year + month // 12, month % 12 + 1, 1)


# ---------------- 读 ----------------


def _spellings(key) -> set:
    """账套键在单据上可能的写法（库里比较不分大小写）：反斜杠 / 斜杠、末尾带不带分隔符。取回后仍按
    `pending.company_of` 规范化再认，这里只是别在查询里漏掉。"""
    base = normalize_express_account_key(key) or str(key)
    slashed = base.replace("\\", "/")
    return {base, base + "\\", slashed, slashed + "/"}


def _documents(company, sets, first, after):
    """(本月单据, 没有单据日期、本月上传的单据, 是否截断)：本方税号是买方或卖方，或推往这家的账套（库里比较不分大小写），
    取回后按 `pending.company_of` 只留归这家的。"""
    or_filters = []
    if company.tax_id:
        or_filters += [["buyer_tax", "=", company.tax_id], ["seller_tax", "=", company.tax_id]]
    keys = sorted({spelled for item in sets if item.get("key") for spelled in _spellings(item["key"])})
    if keys:
        or_filters.append(["push_account_set", "in", keys])
    if not or_filters:
        return [], [], False
    last = after - timedelta(days=1)
    dated = frappe.get_list(HISTORY, filters={"invoice_date": ["between", [first.isoformat(), last.isoformat()]]},
                            or_filters=or_filters, fields=FIELDS, order_by="invoice_date desc, name desc",
                            limit_page_length=LEDGER_MAX + 1)
    undated = frappe.get_list(HISTORY, filters={"invoice_date": ["is", "not set"],
                                                "creation": ["between", [f"{first.isoformat()} 00:00:00",
                                                                         f"{last.isoformat()} 23:59:59.999999"]]},
                              or_filters=or_filters, fields=FIELDS, order_by="creation desc, name desc",
                              limit_page_length=LEDGER_MAX + 1)
    truncated = len(dated) > LEDGER_MAX or len(undated) > LEDGER_MAX
    by_set, by_tax = service.attribution()

    def mine(rows):
        return [row for row in rows[:LEDGER_MAX] if pending.company_of(row, by_set, by_tax) == company.name]

    return mine(dated), mine(undated), truncated


def _follows(row, company, covering) -> bool:
    """这条任务跟着这家公司走（T1，`folder_intake.follow`）、这个月在它的连接上有这家覆盖这个月的账套（任务记了分店按分店筛，
    同 `identity.for_branch`）：跟随的任务存下的是建任务那年的账套，到点按单据日期推往当年那份，新年度的月份里照样算这家的。"""
    if not company or str(row.get("company") or "") != company:
        return False
    books = [item for item in covering if item["connection"] == row.connection]
    return bool(identity.for_branch(books, row.get("company_branch") or ""))


def _without_periods(sets) -> list:
    """账套都没报会计期间时（MR.ERP 的目录只有公司与年度库名，没有起止日期；旧版 CC 桌面版的 Express 也没有）说不出哪份覆盖
    这个月：定时任务按这家公司的全部账套算（同公司页任务卡 `service.tasks_of`），文件照旧只数本月的。有一份报了就不用它。"""
    return list(sets) if sets and not any(item.get("period_start") for item in sets) else []


def _tasks(covering, company="") -> dict | None:
    """覆盖这个月的账套上的定时任务（同 `service.tasks_of` 的判据），加上跟着这家公司走、这个月有账套可推的（`_follows`）：
    ``{任务: {title, enabled, missing}}``；``missing``：启用着、电脑报文件夹不在了。没有录入权限回 None。"""
    if not scope.allowed("intake"):
        return None
    wanted = {(item["connection"], item["key"].casefold()) for item in covering}
    usable = sorted({item["connection"] for item in covering})
    if not usable:
        return {}
    rows = frappe.get_all(RULE, filters={"connection": ["in", usable]},
                          fields=["name", "title", "enabled", "connection", "reader", "reader_device", "account_set_key",
                                  "company", "company_branch"],
                          order_by="creation asc", ignore_permissions=True, limit_page_length=0)
    states = service._cache().setdefault("agents", {})
    out = {}
    for row in rows:
        key = normalize_express_account_key(row.account_set_key) or str(row.account_set_key or "")
        if (row.connection, key.casefold()) not in wanted and not _follows(row, company, covering):
            continue
        # 读者 =（电脑，成员连接）（ADR-021 第 4 批）：按任务那台电脑报的本机情况看文件夹在不在，同 `service.tasks_of`
        reader = folder_readers.ref(row)
        if reader not in states:
            states[reader] = folder_readers.state(*folder_readers.of(row))
        health = (states[reader].get("health") or {}).get(row.name) if agent.fresh(states[reader]) else None
        out[row.name] = {"title": row.title or "", "enabled": bool(row.enabled),
                         "missing": bool(row.enabled and health in FOLDER_MISSING)}
    return out


def _files(tasks, first, after) -> dict:
    from pearnly_cowork.folder_intake import rules as folder_rules

    if not tasks:
        return {}
    return folder_rules.month_files(list(tasks), datetime.combine(first, datetime.min.time()),
                                    datetime.combine(after, datetime.min.time()))


def _readback(sets, period):
    """回读一段（hooks，companies 不导入 readback）：没装、没有录入权限、出错都回 None（这一段不显示）。"""
    for path in frappe.get_hooks(READBACK_HOOK) or []:
        try:
            return frappe.get_attr(path)([{"connection": item["connection"], "key": item["key"],
                                           "label": item.get("label") or ""} for item in sets], period)
        except frappe.PermissionError:
            frappe.clear_messages()
            return None
        except Exception:  # noqa: BLE001 — 回读一段读不出不挡整页
            frappe.log_error(title="Cowork month readback failed", message=frappe.get_traceback()[-1500:])
            frappe.clear_messages()
            return None
    return None


def _drafts(company, period):
    from pearnly_cowork.tax import access as tax_access
    from pearnly_cowork.tax import service as tax_service

    if not tax_access.allowed():
        return None
    return tax_service.month_drafts(company.tax_id or "", period)


# ---------------- 拼 ----------------


def _document_item(row, code, group, company_tax) -> dict:
    from pearnly_cowork.push import export_facts

    fields = {"group": group, "document": row.name, "invoice_no": row.get("invoice_no") or "",
              "counterparty": pending.counterparty(row, company_tax), "amount": pending.amount(row.get("total_amount")),
              "date": str(row.get("invoice_date") or ""), "filename": row.get("filename") or "",
              "reason_label": pending._reason_label(group, row.get("last_push_reason"), row),
              "detail": export_facts.reason_code(row.get("last_push_reason"))
              if code in ("push_manual", "push_failed") else ""}
    if code == "cc_proposal":
        # CC 准备好的提议：当场「推送」（同待处理，数字会计员第 3 期第 12 条）
        fields["choice"] = {"connection": row.push_connection, "account_set_key": row.get("push_account_set") or "",
                            "kinds": []}
    elif pending.mrerp_setup_stop(row):
        # MR.ERP 停在要选导入菜单 / 默认值（码是 choice_needed）：同待处理页，当场选完重推（2026-10-19）
        fields["action"] = "mrerp"
        fields["choice"] = {"connection": row.push_connection, "account_set_key": row.get("push_account_set") or "",
                            "kinds": []}
    elif code == "choice_needed":
        if row.get("push_connection"):
            fields["choice"] = {"connection": row.push_connection, "account_set_key": row.get("push_account_set") or "",
                                "kinds": pending.choice_kinds(row.get("last_push_reason"))}
        else:
            fields["action"] = "open"
    elif pending.account_set_stop(row):
        # 定时任务跟随公司当年账套停在「待选账套」（T1）、自动推前判出多份同期账套：同待处理页，当场换账套重推（第 6 批）
        fields["action"] = "account_set"
        fields["choice"] = {"connection": row.push_connection, "account_set_key": row.get("push_account_set") or "",
                            "kinds": []}
    return exceptions.item(code, **fields)


def _written(rows) -> dict:
    """推送成功的单的推送记录：``{记录名: {"automation": 冻结的自动推（人推的为 None）, "verify": 推后核对}}``（数字会计员
    第 3 期第 6、11 条）。一次查询；冻结目标只在票上判过自动推（``auto_class``）时才取（只有它们可能是自动推的）。"""
    names = [row.last_push_record for row in rows if row.get("last_push_status") == "success" and row.get("last_push_record")]
    if not names:
        return {}
    auto = {row.last_push_record for row in rows if row.get("auto_class") and row.get("last_push_record")}
    found = frappe.get_all(pending.PUSH_RECORD, filters={"name": ["in", names]},
                           fields=["name", "verify_state", "verify_json"], limit_page_length=0)
    targets = {}
    if auto:
        targets = {row.name: row.target_json for row in frappe.get_all(
            pending.PUSH_RECORD, filters={"name": ["in", sorted(auto)]}, fields=["name", "target_json"],
            limit_page_length=0)}
    out = {}
    for row in found:
        target = _json(targets.get(row.name))
        automation = target.get("automation") if isinstance(target.get("automation"), dict) else None
        verify = _json(row.verify_json)
        out[row.name] = {"automation": automation, "verify": row.verify_state or "",
                         "fields": list(verify.get("fields") or [])}
    return out


def _json(value) -> dict:
    try:
        found = json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _pushed_items(row, record, company_tax) -> list:
    """推成功的单还要列的：写入与预期不符（``written_mismatch``，人推的也列），整月档自动推的 B 类（``cc_judged``）。"""
    out = []
    fields = {"document": row.name, "invoice_no": row.get("invoice_no") or "",
              "counterparty": pending.counterparty(row, company_tax), "amount": pending.amount(row.get("total_amount")),
              "date": str(row.get("invoice_date") or ""), "filename": row.get("filename") or ""}
    if record.get("verify") == "mismatch":
        out.append(exceptions.item("written_mismatch", detail=",".join(record.get("fields") or ()), **fields))
    if (record.get("automation") or {}).get("class") == "B":
        out.append(exceptions.item("cc_judged", **fields))
    return out


def _entry(rows, company_tax):
    """录账一段的计数与单据层的异常。``auto``：推成功的单里自动推的张数（「已推 N（自动 M）」）。"""
    cutoff, unknown = pending._cutoff(), pending._unknown(rows)
    counted = {"pushed": 0, "auto": 0, "in_flight": 0, "waiting": 0, "not_pushed": 0, "recognized_only": 0}
    items = []
    written = _written(rows)
    for row in rows:
        status = str(row.get("last_push_status") or "")
        found = exceptions.document_code(row, cutoff, unknown)
        if found:
            code, group = found
            counted["not_pushed" if group == "not_pushed" else "waiting"] += 1
            items.append(_document_item(row, code, group, company_tax))
        elif status == "success":
            counted["pushed"] += 1
            record = written.get(row.get("last_push_record")) or {}
            counted["auto"] += int(bool(record.get("automation")))
            items.extend(_pushed_items(row, record, company_tax))
        elif status == "pending" or row.get("intake_target") in pending.TARGETS:
            counted["in_flight"] += 1  # 在推，或刚上传还在路上（`pending.SETTLE_MINUTES`）
        else:
            counted["recognized_only"] += 1
    return counted, items


def _live_matches(rows) -> dict:
    """识别查重判为同号、被匹配的那张不在本月的：一次查询取回还在的（删了的不再标重复），``{单据: 行}``——同号的一组
    交给 CC 核对时要写出它的单号、对方、金额与日期。"""
    here = {row.name for row in rows}
    wanted = sorted({match for match in map(exceptions.exact_match, rows) if match and match not in here})
    if not wanted:
        return {}
    found = frappe.get_list(HISTORY, filters={"name": ["in", wanted]}, fields=FIELDS, limit_page_length=len(wanted))
    return {row.name: row for row in found}


def _line(row, company_tax) -> dict:
    """同号一组里的一张：单号、对方、金额、日期（交给 CC 时按这些找，不写内部记录名）；四项都没有时用文件名。"""
    return {"document": row.name, "invoice_no": row.get("invoice_no") or "",
            "counterparty": pending.counterparty(row, company_tax), "amount": pending.amount(row.get("total_amount")),
            "date": str(row.get("invoice_date") or ""), "filename": row.get("filename") or ""}


def _duplicates(rows, company_tax) -> list:
    """月内重复单号：同号的几张（同对方同号，或识别查重判为同号的）合成一条（测试窗口第 3 轮：一张一行时同一组列两遍）。
    一条带 ``count``（张数）与 ``documents``（每张的单号、对方、金额、日期；不在本月的被匹配那张也在里面），
    其余字段取这组里排在最前的一张（本月单据的顺序：单据日期新 → 旧）。"""
    live = _live_matches(rows)
    found = exceptions.month_duplicates(rows, company_tax, set(live))
    linked = {}
    for name, others in found.items():
        for other in others:
            linked.setdefault(name, set()).add(other)
            linked.setdefault(other, set()).add(name)
    known = {**live, **{row.name: row for row in rows}}
    order = [row.name for row in rows] + sorted(name for name in linked if name not in {row.name for row in rows})
    rank = {name: index for index, name in enumerate(order)}
    out, seen = [], set()
    for row in rows:
        if row.name not in linked or row.name in seen:
            continue
        group, todo = set(), [row.name]
        while todo:
            name = todo.pop()
            if name not in group:
                group.add(name)
                todo.extend(linked.get(name, ()))
        seen |= group
        names = sorted((name for name in group if name in known), key=lambda name: rank.get(name, len(order)))
        lines = [_line(known[name], company_tax) for name in names]
        first = lines[0]
        out.append(exceptions.item(
            "duplicate_number", document=first["document"], invoice_no=first["invoice_no"],
            counterparty=first["counterparty"], date=first["date"], count=len(lines),
            others=[line["document"] for line in lines[1:]], documents=lines))
    return out


def _tax_view(drafts, items) -> dict:
    flagged = {}
    for found in items:
        if found["code"] == "tax_draft_stale":
            flagged[found["draft"]] = True
    out = {"vat": [], "wht": [], "deadlines": drafts["deadlines"]}
    for draft in drafts["drafts"]:
        out["vat" if draft["kind"] == "vat_return" else "wht"].append({
            "draft": draft["name"], "branch": draft["branch"], "updated_at": service.when(draft["updated_at"]),
            "tax_total": draft["tax_total"], "issues": sum(issue["count"] for issue in draft["issues"]),
            "stale": bool(flagged.get(draft["name"]))})
    return out


def month(company, period, limit=None) -> dict:
    """一家公司一个月的台账（文件头）。"""
    service.require_read()
    period, limit = _period(period), _limit(limit)
    row = service._company_row(company)
    sets = service.books(row, None)
    row = service._company_row(company)  # 可能刚补了税号
    covering = service._covering(sets, period)
    first, after = bounds(period)
    ended = exceptions.period_ended(period, frappe.utils.now_datetime().date())
    tax_id = row.tax_id or ""

    dated, undated, truncated = _documents(row, sets, first, after)
    documents = dated + undated
    counted, entry_items = _entry(documents, tax_id)
    tasks = _tasks(covering or _without_periods(sets), row.name)
    files = _files(tasks, first, after) if tasks else {}
    intake_items = _duplicates(documents, tax_id) + exceptions.file_items(files, tasks or {})

    drafts = _drafts(row, period)
    # 测试窗口第 3 轮：什么都没有的月份（没收到单、没推过、没有申报草稿）也报「还没回读」，「本月」卡就一直不隐藏。
    # 没有可核对的就不催回读；核对一段只按推过的单算，没推过（也没有回读发现）时是 empty。复审第 3 轮按派单口径收紧：
    # 收到了单但一张没推的月份也不催（核对一段为空；收单 / 录账两段照常列这些单，卡片不会被误藏）。有申报草稿时照旧催
    # （草稿要用回读出）；已有的回读发现（含直接在 Express 记的单）不论推没推都照常报
    pushed = counted["pushed"] + counted["in_flight"]
    needed = pushed > 0 or bool((drafts or {}).get("drafts"))

    verify = None
    verify_items = []
    if row.adapter == "express" and scope.allowed("intake"):
        express = [item for item in covering if item.get("adapter") == "express"]
        found = _readback(express, period)
        if found is not None:
            verify_items = exceptions.readback_items(found, ended, needed)
            waiting = any(item["status"] != "ready" or item.get("newer") == "reading" for item in found)
            # 推后核对写入与预期不符（`written_mismatch`，第 3 期第 6 条）也是核对一段要人看的
            written = [item for item in entry_items if item["stage"] == "verify"]
            verify = {"sets": found, "state": exceptions.stage_state(len(found) if pushed else 0,
                                                                     verify_items + written, not ended or waiting)}

    tax = None
    tax_items = []
    if drafts is not None:
        latest = {(item["connection"], item["key"].casefold()): item["readback"]
                  for item in (verify or {}).get("sets") or () if item.get("status") == "ready"}
        tax_items = exceptions.tax_items(drafts["drafts"], latest)
        tax = _tax_view(drafts, tax_items)
        tax["state"] = exceptions.stage_state(len(drafts["drafts"]), tax_items, not ended)

    file_totals = {code: sum(int(found.get(code) or 0) for found in files.values())
                   for code in ("failed", "not_invoice", "pos_layout_needed")} if tasks is not None else None
    intake = {"received": len(dated), "recognized_only": counted["recognized_only"], "no_date": len(undated),
              "tasks": ({"count": len(tasks), "enabled": sum(1 for task in tasks.values() if task["enabled"]),
                         "attention": sum(1 for task in tasks.values() if task["missing"])}
                        if tasks is not None else None),
              "files": file_totals}
    # 单据超过上限只算了前 `LEDGER_MAX` 张（``truncated``）：收单、录账两段不能说已完成
    intake["state"] = exceptions.stage_state(len(documents) + sum((file_totals or {}).values()), intake_items,
                                             not ended or truncated)
    entry = {key: counted[key] for key in ("pushed", "in_flight", "waiting", "not_pushed")}
    entry_only = [found for found in entry_items if found["stage"] == "entry"]
    entry["state"] = exceptions.stage_state(sum(entry.values()), entry_only,
                                            not ended or truncated or counted["in_flight"] > 0)
    entry["auto"] = counted["auto"]

    items = exceptions.ordered(intake_items + entry_items + verify_items + tax_items)
    return {"company": row.name, "period": period, "ended": ended, "automation": _automation(row),
            "generated_at": service.when(str(frappe.utils.now_datetime())), "truncated": truncated,
            "stages": {"intake": intake, "entry": entry, "verify": verify, "tax": tax},
            "exceptions": {"items": items[:limit], "total": len(items), "counts": exceptions.tally(items),
                           "severities": exceptions.severities(items)}}


def _automation(row):
    """这家的自动推送档位（数字会计员第 3 期第 11、12 条）：``{tier, since, reason}``；只有 Express 公司有，其余 None。
    读权限同台账（统计另由 CC 看，`automation.view`）。"""
    if row.adapter != "express":
        return None
    from pearnly_cowork.companies import automation

    found = automation.view(row.name)
    return {key: found[key] for key in ("tier", "since", "reason")}


__all__ = ["LEDGER_MAX", "bounds", "month", "proposals"]
