"""Site, entitlement, role and explicit company/client scope for Cowork.

API callers never supply a tenant. Native User Permission grants scope.
Cowork Admin may manage the current Site, but still needs purchased features.
"""
import frappe
from frappe import _
from pearnly_core.shell import entitlements

ROLES = {"intake": "Cowork Intake", "ocr_history": "Cowork OCR History",
         "master_data": "Cowork Master Data"}
READ_FEATURES = ("intake", "ocr_history")
READ_TYPES = {"read", "select", "report", "export", "print"}
COWORK_TYPES = ("Cowork Workspace", "Cowork Client", "Cowork OCR History")


def _decision(permitted):
    # A False hook result allows Frappe to fall back to DocShare.
    # A company/feature denial must remain final even with an old share.
    if not permitted:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return True


def reject_share(doc, method=None):
    if doc.share_doctype in COWORK_TYPES:
        frappe.throw(_("Assign Cowork access through company and client permissions."))


def _reject_share_fallback(doctype, user=None):
    # Frappe adds shared rows with OR outside permission_query_conditions.
    # Refuse the query if a legacy share could escape the scope predicate.
    user = user or frappe.session.user
    if frappe.get_all("DocShare", filters={"share_doctype": doctype},
                      or_filters={"user": user, "everyone": 1}, pluck="name", limit_page_length=1):
        frappe.throw(_("Remove legacy document shares before accessing Cowork records."),
                     frappe.PermissionError)


def allowed(feature, user=None):
    user = user or frappe.session.user
    return bool(user and user != "Guest"
                and frappe.conf.get("pearnly_product") == "cowork"
                and feature in entitlements()
                and frappe.db.get_value("User", user, "enabled")
                and {ROLES[feature], "Cowork Admin"}.intersection(frappe.get_roles(user)))


def require(feature):
    if not allowed(feature):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def require_read():
    if not any(allowed(f) for f in READ_FEATURES):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def administrator(user=None):
    return "Cowork Admin" in frappe.get_roles(user or frappe.session.user)


def grants(doctype, user=None):
    # Do not cache: revoking membership takes effect on the next operation.
    return set(frappe.get_all("User Permission", filters={
        "user": user or frappe.session.user, "allow": doctype,
        "apply_to_all_doctypes": 1,
    }, pluck="for_value"))


def contains(workspace, client=None, user=None):
    if not workspace or not frappe.db.exists("Cowork Workspace", {"name": workspace, "enabled": 1}):
        return False
    if not administrator(user) and workspace not in grants("Cowork Workspace", user):
        return False
    if client:
        if not frappe.db.exists("Cowork Client", {"name": client, "workspace": workspace, "enabled": 1}):
            return False
        if not administrator(user) and client not in grants("Cowork Client", user):
            return False
    return True


def require_scope(workspace, client=None):
    if not contains(workspace, client):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def default_workspace() -> str:
    """本站点那一份归属（公司资料），给**不经过界面选择**的调用方用。

    产品口径（用户 2026-09-25）：Cowork 不记账、只留记录，界面上不出现账套，
    所以录入这条链不该逼调用方先选一份。这里在**服务端**把它解析出来：
    站点里恰好一份就用它；一份都没有、或有多份而调用方没说用哪一份，就明确报错
    —— 不替人挑一份（挑错等于把记录挂到别人家账上）。
    """
    rows = frappe.get_all("Cowork Workspace", filters={"enabled": 1},
                          pluck="name", order_by="name", limit_page_length=2)
    if len(rows) == 1:
        return rows[0]
    frappe.throw(_("cowork.workspace_unresolved"))


def history_permission(doc, ptype=None, user=None, **kwargs):
    feature_ok = any(allowed(f, user) for f in READ_FEATURES) if ptype in READ_TYPES else allowed("intake", user)
    return _decision(feature_ok and ptype not in {"delete", "share", "submit", "cancel"}
                and contains(doc.workspace, doc.client, user))


def history_query(user=None):
    _reject_share_fallback("Cowork OCR History", user)
    if not any(allowed(f, user) for f in READ_FEATURES):
        return "1=0"
    table = "`tabCowork OCR History`"
    return _scope_query(table, user, client=True)


def workspace_permission(doc, ptype=None, user=None, **kwargs):
    if ptype in READ_TYPES:
        return _decision(any(allowed(f, user) for f in (*READ_FEATURES, "master_data"))
                    and (administrator(user) or doc.name in grants("Cowork Workspace", user)))
    return _decision(ptype not in {"delete", "share"} and allowed("master_data", user) and administrator(user))


def workspace_query(user=None):
    _reject_share_fallback("Cowork Workspace", user)
    if not any(allowed(f, user) for f in (*READ_FEATURES, "master_data")):
        return "1=0"
    if administrator(user):
        return ""
    return _in("`tabCowork Workspace`.`name`", grants("Cowork Workspace", user))


def client_permission(doc, ptype=None, user=None, **kwargs):
    if ptype in READ_TYPES:
        return _decision(any(allowed(f, user) for f in (*READ_FEATURES, "master_data"))
                    and contains(doc.workspace, doc.name, user))
    return _decision(ptype not in {"delete", "share"} and allowed("master_data", user)
                and administrator(user) and contains(doc.workspace, user=user))


def client_query(user=None):
    _reject_share_fallback("Cowork Client", user)
    if not any(allowed(f, user) for f in (*READ_FEATURES, "master_data")):
        return "1=0"
    table = "`tabCowork Client`"
    query = f"{table}.`enabled`=1 AND " + _scope_query(table, user)
    if not administrator(user):
        query += " AND " + _in(f"{table}.`name`", grants("Cowork Client", user))
    return query


def _in(column, values):
    # The hook returns a SQL fragment; column names are fixed, values DB-escaped.
    return f"{column} IN ({', '.join(frappe.db.escape(v) for v in sorted(values))})" if values else "1=0"


def _scope_query(table, user, client=False):
    clauses = [f"{table}.`workspace` IN (SELECT name FROM `tabCowork Workspace` WHERE enabled=1)"]
    if not administrator(user):
        clauses.append(_in(f"{table}.`workspace`", grants("Cowork Workspace", user)))
        if client:
            clauses.append(f"(COALESCE({table}.`client`, '')='' OR "
                           + _in(f"{table}.`client`", grants("Cowork Client", user)) + ")")
    if client:
        clauses.append(f"(COALESCE({table}.`client`, '')='' OR {table}.`client` IN "
                       f"(SELECT name FROM `tabCowork Client` WHERE enabled=1 AND workspace={table}.`workspace`))")
    return " AND ".join(clauses)
