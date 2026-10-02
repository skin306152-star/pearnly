"""Site, entitlement and role checks for Cowork.

API callers never supply a tenant. Cowork has no company isolation (user
2026-09-26): every record belongs to the Site's one internal ownership row
(`Cowork Workspace`, never shown), and access is purchased feature + role.
The `workspace` / `client` checks below only keep references consistent.
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


def granted(features, user=None):
    """这个人现在能用其中哪几项功能（站点是 Cowork、买了这项、账号启用、有这项的角色或 Cowork Admin）。

    几项一起判时账号是否启用只查一次（2026-10-01 生产压测：一份发票的识别与存结果里这条查询出现 20 多次，大多是
    「几项里有没有一项能用」逐项各查一遍）。每项的判据与原来逐项调 ``allowed`` 完全相同。"""
    user = user or frappe.session.user
    if not (user and user != "Guest" and frappe.conf.get("pearnly_product") == "cowork"):
        return set()
    bought = set(entitlements()).intersection(features)
    if not bought:
        return set()
    enabled = frappe.db.sql("select enabled from `tabUser` where name=%s", (user,))
    if not (enabled and enabled[0][0]):
        return set()
    roles = set(frappe.get_roles(user))
    return {feature for feature in bought if {ROLES[feature], "Cowork Admin"} & roles}


def allowed(feature, user=None):
    return feature in granted((feature,), user)


def allowed_any(features, user=None):
    """``any(allowed(f) for f in features)``，账号只查一次。"""
    return bool(granted(features, user))


def require(feature):
    if not allowed(feature):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def require_read():
    if not allowed_any(READ_FEATURES):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def administrator(user=None):
    return "Cowork Admin" in frappe.get_roles(user or frappe.session.user)


def contains(workspace, client=None, user=None):
    # 只核对引用一致（归属存在且启用、客户属于它），不按人分公司 / 客户。热路径上一份发票要核十几次：直接写参数化查询，
    # 不经查询构建器每次拼一遍 SQL（判据同 frappe.db.exists）。
    if not workspace or not frappe.db.sql("select 1 from `tabCowork Workspace` where name=%s and enabled=1 limit 1",
                                          (workspace,)):
        return False
    if client and not frappe.db.sql("""select 1 from `tabCowork Client` where name=%s and workspace=%s and enabled=1
            limit 1""", (client, workspace)):
        return False
    return True


def require_scope(workspace, client=None):
    if not contains(workspace, client):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def default_workspace() -> str:
    """本站点那一份内部归属，所有记录都挂在它上面。

    产品口径（用户 2026-09-26）：Cowork 新平台**不做公司数据隔离**，界面和权限里都没有公司。
    记录上的 `workspace` 只是站点内部的归属键（防重、写者锁、幂等键都以它为锚，不能随手换），
    所以这里保证站点里总有一份：取最早建的启用行；一份都没有就建一份（`ensure_site_workspace`）。
    旧站点若留下多份，新记录和各列表都只用最早那份（上线前按站点核对份数）。
    """
    return ensure_site_workspace()


def ensure_site_workspace() -> str:
    rows = frappe.get_all("Cowork Workspace", filters={"enabled": 1},
                          pluck="name", order_by="creation asc, name asc", limit_page_length=1)
    if rows:
        return rows[0]
    # 并发的第一次请求在 DocType 元数据行上排队，只建一份（与公司资料写入同一把锁）。
    frappe.db.get_value("DocType", "Cowork Workspace", "name", for_update=True)
    rows = frappe.get_all("Cowork Workspace", filters={"enabled": 1},
                          pluck="name", order_by="creation asc, name asc", limit_page_length=1)
    if rows:
        return rows[0]
    doc = frappe.get_doc({"doctype": "Cowork Workspace", "display_name": frappe.local.site, "enabled": 1})
    doc.flags.site_workspace = True
    return doc.insert(ignore_permissions=True).name


def ensure_on_migrate():
    """装好 / 升级后先把归属行建好，第一次业务请求就不必现建。只在 Cowork 业务站点做。"""
    if frappe.conf.get("pearnly_product") == "cowork":
        ensure_site_workspace()


def history_permission(doc, ptype=None, user=None, **kwargs):
    feature_ok = allowed_any(READ_FEATURES, user) if ptype in READ_TYPES else allowed("intake", user)
    return _decision(feature_ok and ptype not in {"delete", "share", "submit", "cancel"}
                and contains(doc.workspace, doc.client, user))


def history_query(user=None):
    _reject_share_fallback("Cowork OCR History", user)
    if not allowed_any(READ_FEATURES, user):
        return "1=0"
    table = "`tabCowork OCR History`"
    return _scope_query(table, user, client=True)


def workspace_permission(doc, ptype=None, user=None, **kwargs):
    if ptype in READ_TYPES:
        return _decision(allowed_any((*READ_FEATURES, "master_data"), user))
    return _decision(ptype not in {"delete", "share"} and allowed("master_data", user) and administrator(user))


def workspace_query(user=None):
    _reject_share_fallback("Cowork Workspace", user)
    if not allowed_any((*READ_FEATURES, "master_data"), user):
        return "1=0"
    return ""


def client_permission(doc, ptype=None, user=None, **kwargs):
    if ptype in READ_TYPES:
        return _decision(allowed_any((*READ_FEATURES, "master_data"), user)
                    and contains(doc.workspace, doc.name, user))
    return _decision(ptype not in {"delete", "share"} and allowed("master_data", user)
                and administrator(user) and contains(doc.workspace, user=user))


def client_query(user=None):
    _reject_share_fallback("Cowork Client", user)
    if not allowed_any((*READ_FEATURES, "master_data"), user):
        return "1=0"
    table = "`tabCowork Client`"
    return f"{table}.`enabled`=1 AND " + _scope_query(table, user)


def _scope_query(table, user, client=False):
    # 只挡引用失效的行（归属 / 客户被停用），不按人过滤。
    # 启用的工作区先在这里查出来、按字面值放进条件（每次现查，不缓存）：原来写成 ``workspace IN (SELECT … enabled=1)`` 子查询，
    # MariaDB 把它改成以工作区表驱动的半连接，记录表只能整表扫再排序——录入工作台列表 1.1 万行时每次约 64 毫秒、压测并发下
    # 0.5–1.7 秒，是 2026-10-02 m4 / m5 慢查询的大头。按字面值写，列表按修改时间索引顺序只读 20 行（实测 0.24 毫秒）。
    workspaces = frappe.get_all("Cowork Workspace", filters={"enabled": 1}, pluck="name", order_by="name asc")
    if not workspaces:
        return "1=0"
    clauses = [f"{table}.`workspace` IN ({', '.join(frappe.db.escape(name) for name in workspaces)})"]
    if client:
        clauses.append(f"(COALESCE({table}.`client`, '')='' OR {table}.`client` IN "
                       f"(SELECT name FROM `tabCowork Client` WHERE enabled=1 AND workspace={table}.`workspace`))")
    return " AND ".join(clauses)
