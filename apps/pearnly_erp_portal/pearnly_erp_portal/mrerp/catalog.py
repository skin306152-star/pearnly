"""MR.ERP 账套目录与 ERP 公司选账本（评估 §14.6）：管理员在服务端刷新这条连接能进的账套（后台任务，网络那一半是共用层
`pearnly_core.mrerp.collect`，与 Cowork 采集同一份），给某家 ERP 公司选一份当账本。

一家公司只有一个账本：选了 MR.ERP 就不能同时勾着「接 Express」（先在公司上取消 Express）；选账本时记「接上时刻」
（``mrerp_linked_at``），之后开始的采购 / 销售单才导入（同 Express「接上之后开始的单」口径，`express.push.counts_from`）。
目录是连接上的一份快照（``catalog_json``，`pearnly_core.mrerp.catalog.account_sets` 的形状），列账套在服务端按页切。
"""
import json
import re
from urllib.parse import urlsplit

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_core.mrerp import catalog as mrerp_catalog

from pearnly_erp_portal.express import contracts as express_contracts
from pearnly_erp_portal.mrerp import connection as connections

COMPANY = "ERP Company"
JOB_TIMEOUT_SECONDS = 240
PAGE_MAX = 100


def refresh(name, *, manual):
    """申请重读这条连接的账套目录（只限管理员；同一连接只排一件，提交后可见）。

    ``manual``：人点的（`api.request_refresh / refresh_catalog`，11-11）—— 采集时先清被踢标记、这次照常登录；由调用入口显式给。"""
    connections.require_admin()
    if not frappe.db.exists(connections.CONNECTION, name):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    frappe.enqueue("pearnly_erp_portal.mrerp.catalog.run", queue="short", job_id=f"erp-mrerp-catalog::{name}",
                   deduplicate=True, enqueue_after_commit=True, timeout=JOB_TIMEOUT_SECONDS, connection=name,
                   manual=bool(manual))
    return {"connection": name, "requested_at": str(now_datetime())}


def run(connection, manual=False):
    """（后台任务）登录、列账套、补期间与税号（不持行锁）→ 存目录快照或错误码。永不抛到队列。"""
    if not connections.mrerp.enabled():
        return
    from pearnly_core.mrerp import collect as mrerp_collect  # 连网的共用层只在采集任务里载入（同 `push.run`）

    lease = connections.lease(connection)
    frappe.db.commit()
    if not lease:
        return
    # 后台任务自己不算人点的：只有排任务的入口说了是才算（旧任务没带这个参数照不算）
    result = mrerp_collect.collect(lease, manual=bool(manual))
    if result.get("error_code"):
        frappe.db.set_value(connections.CONNECTION, connection, "catalog_error", str(result["error_code"])[:140],
                            update_modified=False)
    else:
        frappe.db.set_value(connections.CONNECTION, connection, {
            "catalog_json": json.dumps(mrerp_catalog.account_sets(result["rows"]), ensure_ascii=False),
            "catalog_at": now_datetime(), "catalog_error": ""}, update_modified=False)
        # 目录里可能多了没接进 ERP 的账套：同 Express 账套名单变化，排一次「有没有新的要人勾」（`express.companies.announce`）
        from pearnly_erp_portal.express import companies as ledger_companies

        ledger_companies.books_changed(None)
    frappe.db.commit()


def _sets(name) -> list:
    try:
        rows = json.loads(frappe.db.get_value(connections.CONNECTION, name, "catalog_json") or "[]")
    except ValueError:
        rows = []
    return [row for row in rows if isinstance(row, dict) and row.get("source_id")]


def account_sets(name, start=0, page_length=20, search=None) -> dict:
    """这条连接的账套（只限管理员），服务端按页切：``{rows: [{key, label, company, period_start, period_end, tax_id}], total}``。"""
    connections.require_admin()
    text = str(search or "").strip().lower()
    rows = [{"key": row["source_id"], "label": row.get("label") or row["source_id"],
             **{k: v for k, v in (row.get("attributes") or {}).items()
                if k in ("company", "period_start", "period_end", "tax_id")}} for row in _sets(name)]
    if text:
        rows = [row for row in rows if text in f"{row['label']} {row.get('company') or ''}".lower()]
    start, size = max(int(start or 0), 0), min(max(int(page_length or 20), 1), PAGE_MAX)
    return {"rows": rows[start:start + size], "total": len(rows)}


def choose(company, connection, account_set):
    """这家公司的账本改成这条连接上的这份 MR.ERP 账套（只限管理员）。账套必须在目录里；公司还勾着 Express 就拒。"""
    connections.require_admin()
    row = frappe.db.get_value(COMPANY, company, ["name", "express_enabled", "mrerp_enabled", "mrerp_connection",
                                                 "mrerp_account_set", "mrerp_company"], as_dict=True)
    if not row or row.express_enabled:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    key = str(account_set or "").strip()
    if key not in {item["source_id"] for item in _sets(connection)}:
        frappe.throw(_("Invalid request"))
    # 接的是这份账套所属的那家 MR.ERP 公司（各年度账套归一家，按单据日期选那一本：`pick`）；``mrerp_account_set`` 记人选的
    # 那本（写账一侧切到 `pick` 之前照旧读它）
    found = next(item for item in _sets(connection) if item["source_id"] == key)
    ident = identity(connection, found.get("attributes") or {})
    values = {"mrerp_enabled": 1, "mrerp_connection": connection, "mrerp_account_set": key, "mrerp_company": ident}
    if not row.mrerp_enabled or row.get("mrerp_company") != ident:
        values["mrerp_linked_at"] = now_datetime()  # 换了公司重记：之前开始的单不导入
    frappe.db.set_value(COMPANY, company, values)
    # 今天同期两本、选的是其中一本：记进写单同一份记忆（公司 × 会计年度，`express.choices`），主档整读与写单都用这一本
    picked = pick(company, frappe.utils.nowdate())
    if key in {option["code"] for option in picked.get("options") or ()}:
        from pearnly_erp_portal.express import choices

        choices.remember(company, choices.ACCOUNT_SET, picked["subject"], key, found.get("label") or key)
    # 人点的（从连接添加 / 公司页选账套，选同一本也算一次重读）：整读一次主档进 ERP（`mrerp.masters`，排在 erp_masters），
    # 先清这个账号的被踢标记；做完推给本人
    from pearnly_erp_portal.mrerp import masters as mrerp_masters

    mrerp_masters.queue(company, notify=frappe.session.user, manual=True)
    return values


def company_view(company):
    """公司页的 MR.ERP 一节（`masters.company.get_profile` → ``mrerp``）。只在开关开着、本人是 ERP 管理员、公司没接 Express、
    站点有主连接（CC 设置 → 连接的 MR.ERP 一行建的）时给，否则 ``None``（页面不出这一节）：
    ``{connection, account_set, label, linked}``——现在用的账套（没用 MR.ERP 时空）与它在目录里的名字。"""
    if not connections.mrerp.enabled() or not connections.is_admin():
        return None
    row = frappe.db.get_value(COMPANY, company, ["express_enabled", "mrerp_enabled", "mrerp_connection",
                                                 "mrerp_account_set"], as_dict=True)
    main = connections.main_name()
    if not row or row.express_enabled or not main:
        return None
    linked = bool(row.mrerp_enabled)
    connection = (row.mrerp_connection or main) if linked else main
    key = str(row.mrerp_account_set or "") if linked else ""
    label = next((item.get("label") or key for item in _sets(connection) if item["source_id"] == key), key) if key else ""
    return {"connection": connection, "account_set": key, "label": label, "linked": linked}


def detach(company):
    """这家公司不再用 MR.ERP 当账本（只限管理员）：之后提交的单不导入；已导入的不动。"""
    connections.require_admin()
    frappe.db.set_value(COMPANY, company, {"mrerp_enabled": 0, "mrerp_linked_at": None})


# ── 各年度账套归成一家公司（公司接入第 3 批，主会话 11-14 定：开关打开前做对，同 Express 按公司接、按日期选那一本）──────────
#
# MR.ERP 选公司页一行是一家公司（tbl_comid），行里每个链接是它的一个年度库（tbl_comidyear；`login/selectdb.php`）；目录按行读出
# ``company``（简称）/ ``company_name``（全称）。认同一家（:func:`identity`）：总店登记了有效税号 → ``tax:<税号>``（同 Express 按税号
# 认）；否则 → ``row:<站点主机>|<简称>|<全称>``——**不带连接号、不带账号**：换了 MR.ERP 账号、连接删了重建，只要还是同一个 MR.ERP
# 站点上的同一行，就认成同一家，不另建公司（`tests/test_erp_mrerp_onboarding.py` 锁住）。一个 ERP 租户只接一个客户的账号，同站点
# 同简称同全称撞成一家的风险只在本租户里。

_CODE_PREFIX = re.compile(r"^[0-9][0-9\-./]*\s+")


def _norm(value) -> str:
    return " ".join(str(value or "").split()).casefold()


def site_host(connection) -> str:
    """这条连接的 MR.ERP 站点主机名（认同一家公司用；循环里按连接先取一次再传给 :func:`identity`）。"""
    url = frappe.db.get_value(connections.CONNECTION, connection, "system_url") if connection else ""
    return (urlsplit(str(url or connections.DEFAULT_URL)).hostname or "").lower()


def identity(connection, attributes, host=None) -> str:
    """``host``：调用方按连接取好的 :func:`site_host`（逐行算标识时不再每行查一次连接）。"""
    tax = express_contracts.tax13((attributes or {}).get("tax_id"))
    if tax:
        return f"tax:{tax}"
    host = site_host(connection) if host is None else host
    return f"row:{host}|{_norm((attributes or {}).get('company'))}|{_norm((attributes or {}).get('company_name'))}"


def legal_name(attributes, fallback="") -> str:
    """建公司用的名称：MR.ERP 全称去掉前面的内部编号（如 ``1010-01-000006 ``）；没有全称用简称。"""
    full = _CODE_PREFIX.sub("", str((attributes or {}).get("company_name") or "").strip()).strip()
    return (full or str((attributes or {}).get("company") or "").strip() or fallback)[:200]


def _book(connection, item) -> dict:
    attributes = item.get("attributes") or {}
    return {"key": item["source_id"], "label": item.get("label") or item["source_id"], "connection": connection,
            "period_start": str(attributes.get("period_start") or "")[:10],
            "period_end": str(attributes.get("period_end") or "")[:10], "next_end": None,
            "comidyear": str(attributes.get("comidyear") or "")}


def books_of(company) -> list:
    """这家公司（接着 MR.ERP 的）各年度账套：所有启用连接的目录里与它同一家的（:func:`identity`），公司现用的那条连接排前。
    没记公司标识的旧行（只记了一本）回那一本。``company`` 是公司名或带 ``mrerp_*`` 字段的行。"""
    row = company if isinstance(company, dict) else frappe.db.get_value(
        COMPANY, company, ["name", "mrerp_connection", "mrerp_account_set", "mrerp_company"], as_dict=True)
    if not row:
        return []
    names = frappe.get_all(connections.CONNECTION, filters={"enabled": 1}, pluck="name", order_by="creation asc",
                           limit_page_length=20)  # bounded: 一个租户的 MR.ERP 连接（一般一条）
    names.sort(key=lambda name: name != row.get("mrerp_connection"))
    found, seen = [], set()
    for name in names:
        host = site_host(name)  # loop-query: 一个租户的 MR.ERP 连接（最多 20 条），每条取一次
        for item in _sets(name):  # loop-query: 同上，每条连接读一次自己的目录快照
            ident = identity(name, item.get("attributes") or {}, host)  # loop-query: 传了 host，不再查库
            wanted = ident == row.get("mrerp_company") if row.get("mrerp_company") \
                else (name, item["source_id"]) == (row.get("mrerp_connection"), row.get("mrerp_account_set"))
            if wanted and item["source_id"] not in seen:
                seen.add(item["source_id"])
                found.append(_book(name, item))
    return found


def pick(company, day, wanted=None) -> dict:
    """单据该写进这家公司的哪一本 MR.ERP 账套（接缝：写账一侧 `mrerp.push` 调；签名与行为见评估文档接缝一节，定了不改）。

    回 ``{book, reason, options, subject}``，与 `express.target.pick` 同形状、同原因码（`express.contracts`）：
    - 按单据日期选，规则与 Express 同一条（`account_match.for_day`：本年期间覆盖 → 否则次年段）；**期间读不到的年度库不参与按日期选**；
    - 一本都不覆盖 → ``NO_BOOK``（给人看的一句 :func:`no_book_text`，同收款 / Express 那两句的写法）；
    - 同期两本 → 这家公司这个会计年度选过就用选的（Express 同一个选择与记忆：`express.choices` 的 ``account_set``），没选过
      ``SEVERAL_BOOKS`` 带 ``options`` 与 ``subject``（选一次后记在同一处）；
    - ``wanted``（CC 对话 / 代录定了的那本账套键）：就用这家名单里的那一本；不在名单里 → ``NO_BOOK``；空串 → 对话账套未定。"""
    from pearnly_core.express_rules.targets import account_match
    from pearnly_erp_portal.express import choices, conversation, target

    every = books_of(company)
    name = company["name"] if isinstance(company, dict) else company
    if wanted is not None:
        if not wanted:
            return {"book": None, "reason": conversation.LEDGER_NEEDED, "options": [], "subject": ""}
        book = next((item for item in every if item["key"] == wanted), None)
        return {"book": book, "reason": "" if book else express_contracts.NO_BOOK, "options": [], "subject": ""}
    found = account_match.for_day(every, str(day or "")[:10],
                                  lambda item: (item.get("period_start"), item.get("period_end"), item.get("next_end")))
    if not found:
        return {"book": None, "reason": express_contracts.NO_BOOK, "options": [], "subject": ""}
    if len(found) == 1:
        return {"book": found[0], "reason": "", "options": [], "subject": ""}
    subject = target.year_subject(day, found[0])
    chosen = choices.chosen(name, choices.ACCOUNT_SET, subject)
    book = next((item for item in found if item["key"] == chosen), None)
    if book is not None:
        return {"book": book, "reason": "", "options": [], "subject": subject}
    return {"book": None, "reason": express_contracts.SEVERAL_BOOKS, "subject": subject,
            "options": [{"code": item["key"], "name": item["label"]} for item in found]}


def current(company, day) -> dict | None:
    """主档整读读哪一本：今天该写的那本（同期两本按选过的；没选过回 None，主档整读停下等人在公司页选一次——不默认读第一本，
    11-16 同 Express `masters_choice`）；没有覆盖今天的（新年度库还没建或期间读不到）读期间最近的那本，再没有就取年度库号最大的
    那本（同 Express `target.current`：主档按公司认）。"""
    picked = pick(company, day)
    if picked["book"]:
        return picked["book"]
    if picked["reason"] == express_contracts.SEVERAL_BOOKS:
        return None
    every = books_of(company)
    if not every:
        return None
    return max(every, key=lambda item: (item["period_start"], int(item["comidyear"]) if item["comidyear"].isdigit() else -1))


def no_book_text(company, day) -> str:
    """「没有账套」给人看的一句：沿用收款 / Express 那两句的写法（`express.target.no_book_text`，用户 11-14 定），说出日期与这家公司
    各年度库的期间；期间读不到的年度库照列账套名、不写日期。一本都没有 → 没有已连接的 MR.ERP 账套。"""
    every = books_of(company)
    if not every:
        return _("This company has no connected MR.ERP account set")
    from pearnly_erp_portal.express import target

    return target.no_book_text({}, day, every)


__all__ = ["account_sets", "pick", "books_of", "choose", "company_view", "current", "detach", "identity", "legal_name",
           "no_book_text", "refresh", "run"]
