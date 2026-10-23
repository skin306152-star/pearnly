"""CC 桌面版报来的账套 → ERP 公司（用户 10-23：「CC 这边选择 Express 后，这些数据主动回报同步到 ERP」）。

共用机器通道换了一台电脑的账套名单（`pearnly_core.cc_desktop.books.replace` → 钩子 ``books_changed``）就排一个后台任务
（同站点只留一件）：账套按「税号 + 分店」分组，已有同税号同分店的公司就接上，没有就建一家（公司名取账套登记的公司名，
不做收银）；新建的每家排上主档同步（`masters.queue`，每分钟按顺序发起几家，读好了在后台落库）。哪些账套报给这个 ERP 由 CC 里
这台电脑的 ERP 成员选（选了的 Express 数据根才报），见 docs/work/2026-10-23-erp-express-auto-companies.md。

**只看还连着、按所选数据根报账套的电脑**（CC 桌面版 :data:`SCOPED_FROM` 起、令牌没作废、没退役）：1.11.0 报的是整台电脑的
账套，电脑断开（`devices.leave`）后名单也还留着；照它们建会把事务所电脑上别的客户都建进这个 ERP。

**没有有效 13 位税号的账套不建公司**：ERP 认账套（选账套、写单、同步）全靠税号 + 分店，没税号认不出归哪家，建了也写不进、
同步不了；Express 里补上税号后下一次心跳就建。停用的公司不重建、不启用（人停的）。
"""
import frappe

from pearnly_core.cc_desktop.wire import request_contracts

from pearnly_erp_portal.express import contracts

COMPANY = "ERP Company"
BOOK = "Pearnly CC Book"
DEVICE = "Pearnly CC Device"
SCOPED_FROM = "1.12.0"  # 起这一版 CC 桌面版的 ERP 成员只报人选的数据根里的账套（`worker.erp_scope`）
COMMIT_EVERY = 20


def books_changed(device):
    """（共用通道钩子，心跳的事务里叫）一台电脑的账套名单有变化：排一次建公司（同站点只留一件，提交后才排）。"""
    frappe.enqueue("pearnly_erp_portal.express.companies.ensure", queue="long", timeout=1800,
                   enqueue_after_commit=True, job_id=f"erp-express-companies:{frappe.local.site}", deduplicate=True)


def groups(rows) -> dict:
    """账套行 → ``{(税号, 分店): 公司名}``：只要有效 13 位税号的；公司名取会计期间最新的那份账套登记的。"""
    found = {}
    for row in sorted(rows, key=lambda item: str(item.get("period_end") or "")):
        tax = contracts.tax13(row.get("tax_id"))
        if not tax:
            continue
        key = (tax, contracts.branch5(row.get("branch")))
        found[key] = str(row.get("company") or row.get("label") or "").strip() or found.get(key) or tax
    return found


def missing(found, companies) -> list:
    """还没有公司的那几组：``[(税号, 分店, 公司名)]``。``companies``：现有公司（含停用的，停用的不重建）。"""
    have = {(contracts.tax13(row.get("tax_id")), contracts.branch5(row.get("branch"))) for row in companies}
    return [(tax, branch, name) for (tax, branch), name in sorted(found.items()) if (tax, branch) not in have]


def scoped(devices) -> list:
    """能据以建公司的电脑：令牌在用、没退役、CC 桌面版 :data:`SCOPED_FROM` 起。"""
    return [row["name"] for row in devices if row.get("token_digest") and not row.get("retired_at")
            and request_contracts.supports(row.get("companion_version"), SCOPED_FROM)]


def ensure():
    """（后台任务）按这些电脑报的账套补建公司，新建的排主档同步。回新建的公司。"""
    devices = scoped(frappe.get_all(DEVICE, fields=["name", "token_digest", "retired_at", "companion_version"],
                                    limit_page_length=0))
    if not devices:
        return []
    rows = frappe.get_all(BOOK, filters={"device": ["in", devices]},
                          fields=["company", "label", "tax_id", "branch", "period_end"], limit_page_length=0)
    companies = frappe.get_all(COMPANY, fields=["name", "tax_id", "branch"], limit_page_length=0)
    made = []
    for tax, branch, name in missing(groups(rows), companies):
        doc = frappe.get_doc({"doctype": COMPANY, "company_name": name[:200], "tax_id": tax, "branch": branch,
                              "is_active": 1, "pos_enabled": 0})
        doc.insert(ignore_permissions=True)
        made.append(doc.name)
        if len(made) % COMMIT_EVERY == 0:
            frappe.db.commit()
    frappe.db.commit()
    if made:
        from pearnly_erp_portal.express import masters

        for name in made:
            masters.queue(name)
        frappe.db.commit()
    return made
