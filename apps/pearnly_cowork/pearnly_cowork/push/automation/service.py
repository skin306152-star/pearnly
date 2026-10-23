"""自动推送在推送侧的取数（数字会计员第 3 期第 3–5、7 条）：这家此刻的档位、按单据日期该推往的账套、领单重核的指纹、替这张单
干活的电脑能不能回写入核对、测试站点的路径守卫、人推提议时与提议比对。规则都在纯函数 `push.automation.rules`。

push 域不导入 companies：档位、按日期选账套、公司规则、核不上即降、推过的单在合格回读里的结果都经框架钩子
（``cowork_push_company_tier`` / ``cowork_push_day_books`` / ``cowork_push_company_choices`` / ``cowork_push_verify_mismatch`` /
``cowork_push_readback_marks``）。取不到一律按 L0、按「选不出」，
只会让这张单多交给人一次，不会多推。
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import frappe

from pearnly_cowork.connections import pool as account_pool
from pearnly_cowork.push import choices as choice_store
from pearnly_cowork.push.automation import rules
from pearnly_core.express_rules.push import choices as choice_rules
from pearnly_cowork.push.express import company_choices

TIER_HOOK = "cowork_push_company_tier"
DAY_HOOK = "cowork_push_day_books"
RULES_HOOK = "cowork_push_company_choices"
MISMATCH_HOOK = "cowork_push_verify_mismatch"
MARKS_HOOK = "cowork_push_readback_marks"


def _log(title):
    frappe.log_error(title=title, message=frappe.get_traceback()[-1500:])
    frappe.clear_messages()


def tier(connection, account_set, account_row) -> dict:
    """这份账套所属公司此刻的档位：``{"company", "tier", "readback", "baseline"}``；没建主档、没有行按 L0、新客户；钩子出错
    按 L0、基线不推（出错记日志）。``baseline`` 是定时任务 A 类在 L0 的基线（`rules.action`）。"""
    for path in frappe.get_hooks(TIER_HOOK) or []:
        try:
            found = frappe.get_attr(path)(connection, account_set, account_row)
        except Exception:  # noqa: BLE001 — 取不到档位按 L0：只会少推，不会多推
            _log("Cowork push company tier failed")
            return {"company": "", "tier": rules.L0, "readback": "", "baseline": "tier_unknown"}
        if found:
            return {"company": str(found.get("company") or ""), "tier": rules.tier_of(found.get("tier")),
                    "readback": str(found.get("readback") or ""),
                    "baseline": str(found.get("baseline") or rules.BASELINE_NEW)}
    return {"company": "", "tier": rules.L0, "readback": "", "baseline": rules.BASELINE_NEW}


def day_books(connection, account_row, day, catalog):
    """按这张单的日期该推往哪份账套（T1 同一判据，`companies.automation.day_books`）：规范键列表；认不出、出错回 None。"""
    if not day:
        return None
    for path in frappe.get_hooks(DAY_HOOK) or []:
        try:
            found = frappe.get_attr(path)(connection, account_row, str(day)[:10], catalog)
        except Exception:  # noqa: BLE001 — 选不出就交给人
            _log("Cowork push day books failed")
            return None
        if found is not None:
            return list(found)
    return None


def readback_marks(connection, account_set, records, *, detail=False) -> dict:
    """几张推成功的单在合格回读里的结果 ``{推送记录: confirmed|changed}``（`companies.automation.readback_marks`）；``detail`` 时
    ``{推送记录: {"readback", "changed": [类] | None}}``（R2 的回读改动比例要知道改了哪几类）。取不到、出错回 {}（凭记忆推的单都不算
    证据，只会少算 A）。"""
    if not records:
        return {}
    for path in frappe.get_hooks(MARKS_HOOK) or []:
        try:
            found = frappe.get_attr(path)(connection, account_set, records, **({"detail": True} if detail else {}))
        except Exception:  # noqa: BLE001 — 取不到就当没核过
            _log("Cowork push readback marks failed")
            return {}
        if isinstance(found, dict):
            return found
    return {}


def company_rules(connection, account_set, account_row) -> tuple:
    """这家公司的规则与这份账套的分店：(规则, 分店)；没有或出错回 ((), "")（出错时指纹照样算，只是少一项）。"""
    for path in frappe.get_hooks(RULES_HOOK) or []:
        try:
            found = frappe.get_attr(path)(connection, account_set, account_row)
        except Exception:  # noqa: BLE001
            _log("Cowork push company choices failed")
            return (), ""
        if found:
            return tuple(found.get("rules") or ()), str(found.get("branch") or "")
    return (), ""


def memory(connection, account_set, account_row, scope=None) -> str:
    """这张单用得着的账套记忆与公司规则（``scope``，`rules.subjects`）的版本指纹（领单重核的一项）。"""
    found, _branch = company_rules(connection, account_set, account_row)
    return rules.fingerprint(choice_store.remembered(connection, account_set), found, scope)


def current_hash(record_version, connection, account_set, account_row, scope=None, overrule=None) -> str:
    """此刻的 ``automation_hash``：票的版本、这家当前档位、这张单用得着的账套记忆与公司规则的指纹，加上账套记忆定的科目 / 现 / 赊
    此刻被同账套的人改值挡下的类（``overrule``，`overruled_kinds`；准备时与领单时同一个函数）。"""
    return rules.automation_hash(record_version, tier(connection, account_set, account_row)["tier"],
                                 memory(connection, account_set, account_row, scope),
                                 overruled_kinds(connection, account_set, overrule))


def overruled_kinds(connection, account_set, overrule, today=None) -> list:
    """R1（10-18）：``overrule`` ``{类: {"value", "since"}}``（自动推判 A 时冻结的账套记忆值与证据时刻，`rules.frozen`）里，此刻
    这份账套近 `automation.OVERRIDE_DAYS` 天有人把同一个记忆值改掉、且晚于 ``since`` 的类（`automation.overruled`）。一次查询
    （`push.choices.overrides`）；没有 ``overrule`` 不查库。"""
    from pearnly_cowork.push.express import automation as judging  # 只有带 overrule 的自动推才用得着

    wanted = {kind: item for kind, item in (overrule or {}).items()
              if kind in judging.OVERRIDE_KINDS and isinstance(item, dict) and item.get("value")}
    if not wanted:
        return []
    found = choice_store.overrides(connection, account_set, {kind: item["value"] for kind, item in wanted.items()})
    today = today or frappe.utils.getdate(frappe.utils.nowdate())
    return sorted(kind for kind, item in wanted.items()
                  if judging.overruled(kind, item["value"], item.get("since"), found, today))


def verify_capable(connection, account_key, *, actor, route_device="") -> bool:
    """替这张单干活、能写这份账套的电脑（定时任务是读文件夹的那台，其余是录入人登录过 CC 的电脑；同 `has_writer`）都能回写入
    核对（CC 桌面版 ≥ `rules.VERIFY_MIN_VERSION`）：自动推只派给它们，有旧版就改出提议（第 5 条）。只读。"""
    if not connection or not account_key:
        return False
    doc = frappe.get_doc("Cowork ERP Connection", connection)
    peers = account_pool.owned(account_pool.presence(doc.name), actor, device=route_device or "")
    writers = account_pool.writers(doc, account_key, peers=peers) if peers else []
    return rules.verify_capable((peers.get(device) or {}).get("version") for device in writers)


def test_site_ok(account_key) -> bool:
    """测试站点（站点名合 `TEST_SITES`）上自动推只写 ``C:\\PearnlyExpress\\`` 下的账套；正式站点不看。"""
    from pearnly_core.execution.replay import TEST_SITES

    site = str(getattr(frappe.local, "site", "") or "")
    return not TEST_SITES.fullmatch(site) or rules.test_path_ok(account_key)


def mismatch(connection, account_set, account_row):
    """自动推的单写入核不上：告诉公司档位立刻降到 L0（第 8 条）。出错只记日志，不挡回执。"""
    for path in frappe.get_hooks(MISMATCH_HOOK) or []:
        try:
            frappe.db.savepoint("cowork_push_verify_mismatch")
            frappe.get_attr(path)(connection, account_set, account_row)
        except Exception:  # noqa: BLE001
            frappe.db.rollback(save_point="cowork_push_verify_mismatch")
            _log("Cowork push verify mismatch hook failed")


# ── 人推提议时的比对（第 7 条） ─────────────────────────────────────────────────


def _utc(value):
    """库里站点时区的裸时间 → UTC 裸时间。"""
    if not value:
        return None
    zone = ZoneInfo(frappe.utils.get_system_timezone())
    return frappe.utils.get_datetime(value).replace(tzinfo=zone).astimezone(dt.timezone.utc).replace(tzinfo=None)


# 载荷里这几项的来源是人定的规则（任务默认、公司规则）：值与提议不同就是人改过规则来纠正提议，不是提议过时
HUMAN_RULE_SOURCES = ("task_default", "company_rule")


def chosen_kinds(connection, account_set, fields, direction, payload, since, account_row=None) -> set:
    """这次推送里人定的类别：提议之后（``since``，UTC）在这份账套里选过的（按对方记的归类键）、提议之后改过的公司规则（这份
    账套适用的那条，``account_row`` 认公司与分店），加上载荷里来源是人的（付款方式 ``doctype_src`` = manual / 任务默认 / 公司规则、
    费用科目 ``account_source`` = chosen / 任务默认 / 公司规则、抵进项 ``item_src = manual``）。只有来源不是人（Express 历史重读、
    分类映射……）的变化才算提议过时（审查处理 10-16：改公司规则、任务默认纠正提议原先被记成过时、不进样本）。"""
    payload = payload if isinstance(payload, dict) else {}
    found = set()
    if payload.get("doctype_src") in ("manual", *HUMAN_RULE_SOURCES):
        found.add("payment")
    if payload.get("account_source") in ("chosen", *HUMAN_RULE_SOURCES):
        found.add("account")
    if payload.get("item_src") == "manual":
        found.add("vat")
    side = "buyer" if direction == "sales" else "seller"
    name, tax, branch = fields.get(f"{side}_name"), fields.get(f"{side}_tax"), fields.get(f"{side}_branch")
    supplier = choice_rules.supplier_subject(direction, tax, name)
    subjects = {"party": choice_rules.party_subject(direction, tax, branch, name), "account": supplier,
                "payment": supplier, "vat": supplier, "department": "*"}
    since = frappe.utils.get_datetime(since) if since else None
    if not since:
        return found
    for kind, subject in subjects.items():
        at = _utc(choice_store.chosen_at(connection, account_set, kind, subject))
        if at and at >= since:
            found.add(kind)
    rules_found, branch = company_rules(connection, account_set, account_row) if account_row is not None else ((), "")
    picked = company_choices.pick(rules_found, branch)
    for kind, subject in subjects.items():
        rule = (picked.get(kind) or {}).get(subject) or {}
        at = _utc(rule.get("at")) if rule.get("at") else None
        if at and at >= since:
            found.add(kind)
    return found


__all__ = ["chosen_kinds", "company_rules", "current_hash", "day_books", "memory", "mismatch", "overruled_kinds",
           "test_site_ok", "tier",
           "verify_capable"]
