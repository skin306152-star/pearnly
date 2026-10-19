"""公司的自动推送档位（数字会计员第 3 期第 3、7–10 条，设计见 docs/work/2026-10-16-da-phase3-design.md）。

- 一家一行 `Cowork Company Automation`（只经本模块写，守卫同公司规则）：档位、自何时、原因码、评估用的月度统计（``evidence``）、
  最近 24 次升降（``history``）。**没有行的公司就是 L0**，不补数据。档位按公司主档算、不按租户、没有开关与手动升降按钮。
- 推送经钩子 ``cowork_push_company_tier``（:func:`tier_for_push`，按目录这一行认公司，同第 2 期 `rules.for_push`）取这家此刻的
  档位与最近一次合格回读覆盖的期间；push 域不导入 companies。T1 的「按单据日期选出的那份账套」经 ``cowork_push_day_books``
  （:func:`day_books`）。账套记忆定的科目、现 / 赊、对方档要的「合格回读覆盖、没被改」经 ``cowork_push_readback_marks``（:func:`readback_marks`）。
- 只在两条写路径上评估（GET 不写库）：回读处理完（readback 经钩子 ``cowork_readback_ready`` 叫 :func:`on_readback`）按这期统计
  升降一档；自动推的单写入核不上（推送回执经钩子 ``cowork_push_verify_mismatch`` 叫 :func:`on_mismatch`）立刻降到 L0。
- 统计（`automation_rules.tally`）：单据范围是这家各本 Express 账套里单据日期在这期、推送成功的单；回读里的变化经钩子
  ``cowork_company_month_changes`` 从 readback 取（companies 不导入 readback）。
- 看档位同月结台账的读权限；看月度统计另要录入权限（:func:`view`）。
"""
from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import get_datetime

from pearnly_cowork.access import scope
from pearnly_cowork.companies import automation_rules, current_book, identity, service, tasks
from pearnly_cowork.companies.access import AUTOMATION, read, write
from pearnly_cowork.companies.rules import account_company
from pearnly_cowork.push.automation import clock
from pearnly_cowork.push.automation import rules as tier_rules
from pearnly_cowork.targets.identity import normalize_express_account_key

THRESHOLDS = automation_rules.THRESHOLDS
CHANGES_HOOK = "cowork_company_month_changes"
BANGKOK = ZoneInfo("Asia/Bangkok")
# 评估时保留最近这么多期的统计（L1 → L2 要看最近 2 期）
PERIODS_KEPT = 6
RECORD_SCAN_MAX = 5000
PUSHED_EARLIER_DAYS = 92


def _json(value, default):
    try:
        found = json.loads(value) if value else default
    except (TypeError, ValueError):
        return default
    return found if isinstance(found, type(default)) else default


def _row(company):
    return frappe.db.get_value(AUTOMATION, {"company": company},
                               ["name", "tier", "since", "reason", "evidence_json", "history_json"], as_dict=True)


def state(company) -> dict:
    """这家此刻的档位：``{tier, since, reason, readback, periods, history}``（没有行是 L0）。不核权限，调用方核。"""
    row = _row(company) if company else None
    if not row:
        return {"tier": tier_rules.L0, "since": None, "reason": "", "readback": "", "periods": {}, "history": []}
    evidence = _json(row.evidence_json, {})
    return {"tier": tier_rules.tier_of(row.tier), "since": row.since, "reason": row.reason or "",
            "readback": str(evidence.get("readback") or ""), "periods": evidence.get("periods") or {},
            "history": _json(row.history_json, [])}


def _utc(value) -> str:
    return get_datetime(value).strftime("%Y-%m-%dT%H:%M:%SZ") if value else ""


def view(company) -> dict:
    """给页面与 CC 的档位（同月结台账读权限）：``{tier, since, reason, stats}``；``stats``（近两期统计）要录入权限，没有为 None。"""
    scope.require_read()
    service.get(company)
    found = state(company)
    out = {"tier": found["tier"], "since": _utc(found["since"]), "reason": found["reason"], "stats": None}
    if scope.allowed("intake"):
        recent = sorted(found["periods"])[-2:]
        out["stats"] = {period: found["periods"][period] for period in recent}
    return out


# ---------------- 推送侧的钩子 ----------------


def tier_for_push(connection, account_set, account_row) -> dict | None:
    """推送的钩子（``cowork_push_company_tier``）：这份账套所属公司此刻的档位 ``{"company", "tier", "readback"}``（``readback``
    是最近一次合格回读覆盖的期间 `YYYY-MM`，没有为空串）；目录里没有这一行、没建主档回 None（按 L0）。出错由推送那边接住按 L0。
    ``baseline``：定时任务 A 类在 L0 的基线（`automation_rules.baseline`）。推送服务已核过录入权限与这条连接的使用授权；
    档位全所共用。"""
    del account_set  # 归属由目录这一行定
    name, _branch = account_company(connection, account_row)
    if not name:
        return None
    found = state(name)
    since = get_datetime(found["since"]).date() if found["since"] else None
    return {"company": name, "tier": found["tier"], "readback": found["readback"],
            "baseline": automation_rules.baseline(found["tier"], since, found["periods"], found["reason"])}


def day_scope(connection, account_row, day, catalog=None) -> dict | None:
    """按这张单的日期，这家公司（这个分店）在这条连接上覆盖那天的账套，还没按「现用的那份」筛：``{"workspace", "identity",
    "books"}``（``books`` 是 `identity.book` 的样子）。建了主档的同定时任务（`tasks.books_of`），身份取主档上的；没建主档的按
    目录里同一身份的行现算（``catalog`` 是这条连接的账套目录）。工作空间与身份只在选出多份时才查（只有那时要问「现用的那份」），
    否则为空。认不出公司回 None。只读。"""
    name, branch = account_company(connection, account_row)
    if name:
        found = tasks.books_of(name, connection, day, branch, remembered=False)
        if found is None:
            return None
        company = (frappe.db.get_value("Cowork Company", name, ["identity", "workspace"], as_dict=True)
                   if len(found) > 1 else None) or {}
        return {"workspace": company.get("workspace"), "identity": company.get("identity"), "books": found}
    mine = identity.company_identity("express", connection, account_row)
    if not mine:
        return None
    books = [identity.book("express", connection, row) for row in catalog or ()
             if isinstance(row, dict) and row.get("active", True) is not False
             and (identity.company_identity("express", connection, row) or {}).get("key") == mine["key"]]
    books = identity.for_day(identity.for_branch(books, mine["branch"]), day)
    workspace = frappe.db.get_value("Cowork ERP Connection", connection, "workspace") if len(books) > 1 else None
    return {"workspace": workspace, "identity": mine["key"], "books": books}


def day_books(connection, account_row, day, catalog=None) -> list | None:
    """推送的钩子（``cowork_push_day_books``）：按这张单的日期，这家公司（这个分店）在这条连接上该推往哪份账套 —— 与定时任务
    跟随公司当年账套（T1，`tasks.books_of`）同一判据 → 规范账套键列表（正好一份才算选得出）。选出多份同期时按这家公司选过的
    现用账套只留那份（`current_book.remembered`，用户 2026-10-17）。认不出公司回 None。只读。"""
    found = day_scope(connection, account_row, day, catalog)
    if found is None:
        return None
    return [item["key"] for item in current_book.remembered(found["workspace"], found["identity"], day, found["books"])]


# ---------------- 写 ----------------


def _set(company, tier, reason, *, evidence=None, period="", restart=False):
    """存这家的档位（同档只更新证据）；档位变了记一次升降。``restart``：同档也从现在重新起算（L0 上自动推核不上：定时任务的
    基线推送要等之后的完整期证据）。调用方在写路径上、负责提交。"""
    workspace = frappe.db.get_value("Cowork Company", company, "workspace")
    if not workspace:
        return None
    existing = frappe.db.get_value(AUTOMATION, {"company": company}, "name")
    if existing:
        doc = read(AUTOMATION, existing, for_update=True)
    else:
        doc = frappe.new_doc(AUTOMATION)
        doc.update({"company": company, "workspace": workspace, "tier": tier_rules.L0})
    before = tier_rules.tier_of(doc.tier)
    if evidence is not None:
        doc.evidence_json = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    if tier != before or restart:
        now = clock.now()  # 测试租户压缩时间试点：记模拟的那一天（之后只认这天以后开始的完整期）
        doc.tier, doc.since, doc.reason = tier, now, reason
        doc.history_json = json.dumps(automation_rules.keep_history(
            _json(doc.history_json, []), automation_rules.history_entry(before, tier, reason, now, period)),
            ensure_ascii=False)
    write(doc)
    return doc


def _deadline(period) -> dt.date:
    """这一期 ภ.พ.30 的截止日：电子申报有延期时取电子申报的，否则纸质。"""
    from pearnly_cowork.tax import periods as tax_periods  # 只在评估时用（看档位、给 CC 的资料不加载报税）

    found = tax_periods.deadlines("pp30", int(period[:4]), int(period[5:7]))
    return found.get("efiling") or found["paper"]


def _bangkok_day(value) -> dt.date | None:
    """库里的 UTC 裸时间 → 曼谷日期。"""
    if not value:
        return None
    return get_datetime(value).replace(tzinfo=dt.timezone.utc).astimezone(BANGKOK).date()


def _changes(sets, period) -> list:
    out = []
    for path in frappe.get_hooks(CHANGES_HOOK) or []:
        out.extend(frappe.get_attr(path)(sets, period) or [])
    return out


def _records(connection, key, first, last):
    """这本账套里单据日期在这期、推送成功的单（推送记录与票，一次查询）。"""
    since = dt.datetime.combine(first, dt.time()) - dt.timedelta(days=PUSHED_EARLIER_DAYS)
    return frappe.db.sql("""SELECT r.name, r.history, r.target_json, r.verify_state, r.completed_at,
        h.auto_class FROM `tabCowork Push Record` r JOIN `tabCowork OCR History` h ON h.name = r.history
        WHERE r.status = 'success' AND r.connection = %s AND r.account_key = %s AND r.creation >= %s
        AND h.invoice_date BETWEEN %s AND %s ORDER BY r.name LIMIT %s""",
                         (connection, key, since, first.isoformat(), last.isoformat(), RECORD_SCAN_MAX), as_dict=True)


def documents(company, period) -> tuple:
    """这家这一期的单（`automation_rules.tally` 的输入）与这期有没有合格回读：(单, 合格)。不核权限。"""
    from pearnly_cowork.tax import periods as tax_periods

    first, last = tax_periods.bounds(int(period[:4]), int(period[5:7]))
    books = [item for item in service.books(service.get(company), None) if item.get("adapter") == "express"]
    sets = [{"connection": item["connection"], "key": item["key"], "label": item.get("label") or ""}
            for item in identity.covering(books, period)]
    if not sets:
        return [], False
    deadline = _deadline(period)
    readbacks = {(item.get("connection"), normalize_express_account_key(item.get("key")).casefold()): item
                 for item in _changes(sets, period) if isinstance(item, dict)}
    out, qualified = [], False
    for item in sets:
        key = normalize_express_account_key(item["key"])
        found = readbacks.get((item["connection"], key.casefold())) or {}
        requested = get_datetime(found["requested_at"]) if found.get("requested_at") else None
        good = automation_rules.qualified(_bangkok_day(requested), deadline)
        qualified = qualified or good
        changes = found.get("changes") if isinstance(found.get("changes"), dict) else {}
        for row in _records(item["connection"], key, first, last):
            target = _json(row.target_json, {})
            automated = target.get("automation") if isinstance(target.get("automation"), dict) else None
            outcome = target.get("proposal") if isinstance(target.get("proposal"), dict) else None
            cls = (automated or outcome or {}).get("class") or row.auto_class or ""
            _good, mark = _covered(found, row.completed_at, deadline)
            covered = bool(mark)
            change = changes.get(row.name) or ("changed" if covered and found.get("truncated") else "")
            out.append({"record": row.name, "history": row.history, "cls": cls, "automated": bool(automated),
                        "proposal": bool(outcome), "covered": covered, "readback": change,
                        "verify": row.verify_state or "", "outcome": outcome})
    return out, qualified


def _covered(found, record_completed, deadline):
    """一张推送记录被这期的回读覆盖成什么：``(合格, 结果)``，结果是 ``confirmed``（覆盖、没被改）/ ``changed``（被改、删、作废、
    找不到，或差异表截断）/ 空（回读不合格、或推在回读发起之后）。`documents` 与 `readback_marks` 同一口径。"""
    requested = get_datetime(found["requested_at"]) if found.get("requested_at") else None
    good = automation_rules.qualified(_bangkok_day(requested), deadline)
    if not (good and requested and record_completed and get_datetime(record_completed) <= requested):
        return good, ""
    return good, "changed" if found.get("record_change") or found.get("truncated") else "confirmed"


def readback_marks(connection, account_set, records, detail=False) -> dict:
    """推送的钩子（``cowork_push_readback_marks``）：这份账套里几张推成功的单在合格回读里的结果 ``{推送记录: confirmed|changed}``
    （没被合格回读覆盖的不列）。``detail``：结果换成 ``{推送记录: {"readback": confirmed|changed, "changed": [改了的类] | None}}``
    （科目 / 现 / 赊 / 对方档改了哪几类，`readback.diff.changed_kinds`；confirmed 为空表，只知道整张被改（差异表截断）为 None），
    自动推判档 R2 的回读改动比例用（10-18）。``records``：``[{record, invoice_date, completed_at}]``。账套记忆定的科目、现 / 赊、对方档都要
    最近连续 3 张有证据的同值才算 A（`push.express.automation.memory_evidence`）：凭记忆推出去的单要被合格回读覆盖、没被改才算
    证据。按整张记录：差异比对（`readback.diff`）比日期、现 / 赊（单据类型）、对方、单号、金额、VAT，没写库存行的单再比分录科目
    （真写了库存行的单不比分录，调用方对这种单的科目不认 confirmed），任一被改就是 changed，三类一起断开。每期的回读在同一个
    请求 / 后台任务里只取一次（`_month_found`，扫描一批单时各张共用）。查询数随 ``records`` 里不同的税期数（每期一次
    `readback.service.month_changes`，最多读 `MONTH_SCAN_MAX` 行、只解析这份账套那一行的 2 段 JSON）：推送一侧（`assess._recent`）
    一张单至多给 `push.express.automation.RECENT_PERIOD_MAX` 个不同的税期（审查处理 10-17）。只读，不核权限（推送服务已核过录入权限与连接授权）。"""
    key = normalize_express_account_key(account_set)
    if not connection or not key:
        return {}
    by_period = {}
    for item in records or ():
        period = tier_rules.period_of(str((item or {}).get("invoice_date") or "")[:7])
        if period and item.get("record"):
            by_period.setdefault(period, []).append(item)
    out = {}
    for period, items in by_period.items():
        found = _month_found(connection, key, period)
        if not found:
            continue
        deadline = _deadline(period)
        changes = found.get("changes") if isinstance(found.get("changes"), dict) else {}
        kinds = found.get("kinds") if isinstance(found.get("kinds"), dict) else {}
        for item in items:
            _good, mark = _covered({**found, "record_change": changes.get(item["record"])}, item.get("completed_at"),
                                   deadline)
            if not mark:
                continue
            if not detail:
                out[item["record"]] = mark
                continue
            known = changes.get(item["record"]) and not found.get("truncated") and item["record"] in kinds
            out[item["record"]] = {"readback": mark, "changed": [] if mark == "confirmed" else
                                   list(kinds[item["record"]]) if known else None}
    return out


def _month_found(connection, key, period):
    """这份账套这一期完成时间最晚的那份回读（`_changes` 的一行，没有回 None），按 (连接, 账套, 期) 缓存在 ``frappe.local``：
    定时任务 / 扫描一轮判几十张同一账套的单，各张的「最近几张推送」多落在相同的几期，不重复扫回读表（审查处理 10-17）。
    缓存只活在这一个请求 / 后台任务里；这期间新完成的回读要到下一轮才看得到（只会少认证据、少断开，下一轮补上）。"""
    local = getattr(frappe, "local", None)
    cache = getattr(local, "cowork_readback_month_cache", None) if local is not None else None
    if cache is None:
        cache = {}
        if local is not None:
            try:
                local.cowork_readback_month_cache = cache
            except AttributeError:
                pass
    slot = (connection, key.casefold(), period)
    if slot not in cache:
        cache[slot] = next((row for row in _changes([{"connection": connection, "key": key, "label": ""}], period)
                            if isinstance(row, dict) and row.get("connection") == connection
                            and normalize_express_account_key(row.get("key")).casefold() == key.casefold()), None)
    return cache[slot]


def on_readback(connection, account_set_key, period, readback=None) -> dict | None:
    """回读处理完（钩子 ``cowork_readback_ready``，`readback.service._process` 结束时在同一事务里调）：这份账套所属公司这一期的
    统计重算一次，按合格回读覆盖的完整期升降一档。不是 Express 公司、没建主档、这期回读不合格只更新证据或什么都不做。
    回 ``{"company", "tier", "changed"}`` 或 None。出错由调用方接住（不挡回读）。"""
    del readback
    period = tier_rules.period_of(period)
    key = normalize_express_account_key(account_set_key)
    if not period or not key or not connection:
        return None
    row = next((item for item in service.facts(connection).get("account_sets") or []
                if isinstance(item, dict) and normalize_express_account_key(item.get("source_id")) == key), None)
    company, _branch = account_company(connection, row) if row else (None, "")
    if not company:
        return None
    docs, qualified = documents(company, period)
    current = state(company)
    if not qualified:
        return {"company": company, "tier": current["tier"], "changed": False}
    periods = {**current["periods"], period: automation_rules.tally(docs)}
    periods = {name: periods[name] for name in sorted(periods)[-PERIODS_KEPT:]}
    evidence = {"periods": periods, "readback": max(filter(None, [current["readback"], *periods]))}
    since = get_datetime(current["since"]).date() if current["since"] else None
    tier, reason = automation_rules.evaluate(current["tier"], since, periods)
    _set(company, tier, reason or current["reason"], evidence=evidence, period=period)
    return {"company": company, "tier": tier, "changed": tier != current["tier"]}


def on_mismatch(connection, account_set, account_row) -> dict | None:
    """自动推的单写入核不上（推送回执，钩子 ``cowork_push_verify_mismatch``）：这家立刻降到 L0（原因 ``written_mismatch``）；
    已在 L0 的（定时任务基线推的）也从现在重新起算，之后的完整期证据达标前定时任务 A 类只出提议。人推的单核不上不影响档位
    （调用方只对带 ``automation`` 的记录调）。没建主档回 None。"""
    del account_set
    company, _branch = account_company(connection, account_row)
    if not company:
        return None
    before = state(company)["tier"]
    _set(company, tier_rules.L0, "written_mismatch", restart=True)
    return {"company": company, "tier": tier_rules.L0, "changed": before != tier_rules.L0}


__all__ = ["THRESHOLDS", "day_books", "day_scope", "documents", "on_mismatch", "on_readback", "readback_marks", "state",
           "tier_for_push", "view"]
