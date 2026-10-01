"""Advisory duplicate checks in this Site and the current actor's authorized scope.

Only recognized creation calls this service; manual entry and review do not.
The result is creation-time evidence, not a unique constraint or a push verdict.
No external calls, commits, or cross-Site reads.
"""
import frappe
from frappe import _
from frappe.defaults import set_user_default

from pearnly_cowork.access import scope
from pearnly_cowork.contracts import duplicates as rules

PREFERENCE = "cowork_duplicate_check_enabled"
FIELDS = ("duplicate_check_status", "duplicate_level", "duplicate_match")
TABLE = "`tabCowork OCR History`"


def enabled():
    # Read the user's own persisted value, without global defaults or a stale
    # Redis value after transaction rollback. Missing/invalid values default on.
    value = frappe.db.get_value("DefaultValue", {"parent": frappe.session.user,
        "defkey": PREFERENCE}, "defvalue")
    return value != "0"


def get_setting():
    scope.require_read()
    return {"enabled": enabled()}


def save_setting(value):
    scope.require_read()
    if type(value) is bool:
        chosen = value
    elif type(value) in (int, str) and str(value).lower() in {"0", "1", "true", "false"}:
        chosen = str(value).lower() in {"1", "true"}
    else:
        frappe.throw(_("connection.config_invalid"))
    # Serialize first writes too; the framework's DefaultValue table does not
    # have a unique (parent, defkey) constraint.
    frappe.db.get_value("User", frappe.session.user, "name", for_update=True)
    set_user_default(PREFERENCE, "1" if chosen else "0", user=frappe.session.user)
    return {"ok": True, "enabled": chosen}


def _find(workspace, client, summary):
    current = rules.terms(summary)
    # Reuse the native history query guard (feature, company/client grants,
    # enabled records and legacy DocShare rejection). Admin still only compares
    # their own records, as in the deployed user_id predicate.
    where = f"({scope.history_query()}) AND workspace=%s AND owner=%s"
    params = [workspace, frappe.session.user]
    if client:
        where += " AND (client=%s OR COALESCE(client, '')='')"
        params.append(client)
    for level in ("exact", "likely"):
        if not rules.eligible(current, level):
            continue
        # 不对字段套 LOWER()：两列都是不分大小写的排序规则（utf8mb4_unicode_ci），``x=%s`` 与 ``LOWER(x)=%s`` 取出的候选
        # 完全相同，却能用上发票号 / 日期索引（2026-10-01 生产压测：套了函数就只能沿创建时间把本公司记录逐行过一遍，
        # 每存一张票扫一遍全表，记录越多越慢）。最终是否相同仍由下面 Python 逐字核对。
        if level == "exact":
            clause, values = "invoice_no=%s", [current["invoice_no"]]
        else:
            clause = "invoice_date=%s AND seller_name=%s"
            values = [current["invoice_date"], current["seller_name"]]
        # SQL narrows candidates; Python verifies case-only text matching and
        # exact Decimal equality. MariaDB's default collation also folds accents,
        # and a DECIMAL SQL cast could truncate unusually large precise values.
        offset = 0
        while True:
            rows = frappe.db.sql(f"""SELECT name, invoice_no, invoice_date,
                seller_name, total_amount FROM {TABLE}
                WHERE {where} AND {clause}
                ORDER BY creation DESC, name DESC LIMIT 100 OFFSET %s""",
                (*params, *values, offset), as_dict=True)
            for row in rows:
                if rules.matches(current, row, level):
                    return {"duplicate_check_status": "matched", "duplicate_level": level,
                            "duplicate_match": row.name}
            if len(rows) < 100:
                break
            offset += len(rows)
    return _find_party(workspace, client, summary) or {"duplicate_check_status": "clear"}


def _find_party(workspace, client, summary):
    """第三道（有意新增，见 `contracts/duplicates.MATCH_FIELDS["party"]`）：同一对方税号 + 同日期 + 同金额。

    在当前用户看得见的本公司记录里查（同一批别人上传的照片也算），仍走原生可见性条件。"""
    from pearnly_cowork.contracts.history import primary_page_fields

    fields = primary_page_fields(summary.get("pages") or [])
    column = "buyer_tax" if fields.get("direction") == "sales" else "seller_tax"
    current = rules.party_terms(summary, fields.get(column))
    if not (current["party_tax"] and current["invoice_date"] and current["total_amount"] is not None):
        return None
    where = f"({scope.history_query()}) AND workspace=%s AND invoice_date=%s AND {column} IS NOT NULL"
    params = [workspace, current["invoice_date"]]
    if client:
        where += " AND (client=%s OR COALESCE(client, '')='')"
        params.append(client)
    rows = frappe.db.sql(f"""SELECT name, invoice_date, total_amount, {column} AS party_tax FROM {TABLE}
        WHERE {where} ORDER BY creation DESC, name DESC LIMIT 200""", params, as_dict=True)
    for row in rows:
        if rules.party_matches(current, row.party_tax, row):
            return {"duplicate_check_status": "matched", "duplicate_level": "party", "duplicate_match": row.name}
    return None


def check(workspace, client, summary):
    scope.require("intake")
    scope.require_scope(workspace, client)
    try:
        if not enabled():
            return {"duplicate_check_status": "disabled"}
        return _find(workspace, client, summary)
    except (frappe.PermissionError, frappe.QueryDeadlockError):
        raise
    except Exception as exc:
        # Advisory infrastructure failures must not discard an already-paid OCR
        # result. Do not misreport a failed check as "clear", or log invoice data.
        frappe.logger("cowork").warning("Duplicate check unavailable: %s", type(exc).__name__)
        return {"duplicate_check_status": "unavailable"}


def warning(doc):
    if doc.duplicate_check_status != "matched" or doc.duplicate_level not in rules.MATCH_FIELDS:
        return None
    # The private reference is never copied directly into API output. Recheck
    # current read permissions, including a revoked client or company grant.
    rows = frappe.get_list("Cowork OCR History", filters={"name": doc.duplicate_match},
        fields=["name", "filename", "invoice_no", "invoice_date", "seller_name", "total_amount"],
        limit_page_length=1)
    return {"level": doc.duplicate_level,
            "matched_fields": list(rules.MATCH_FIELDS[doc.duplicate_level]),
            "at_creation": True, "match": dict(rows[0]) if rows else None}
