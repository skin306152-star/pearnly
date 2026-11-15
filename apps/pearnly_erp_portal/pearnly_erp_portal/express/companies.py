"""CC 桌面版报来的账套 → ERP 公司（用户 10-23：「CC 这边选择 Express 后，这些数据主动回报同步到 ERP」）。

**公司接入重做第 2 批（2026-11-14，用户定 A 方案）：不再按账套自动建公司。** 以前一台电脑的账套名单有变化就把位置下全部
有税号的账套按「税号 + 分店」建成公司、各自整本同步（testerp 因此有 66 家公司、59 家从没用过，客户 8.5 万条）。现在：

- 名单有变化（`pearnly_core.cc_desktop.books.replace` → 钩子 :func:`books_changed`）只排一件 :func:`announce`：算出连接里还没
  接进 ERP 的公司（Express 账套与 MR.ERP 账套目录，同 CC 选公司弹窗 `masters.cc_service._catalog`），有新的就推实时事件
  :data:`EVENT`，ERP 网页给能建公司的人弹「选择要用的公司」（`public/desk/company-connect.js`），勾谁建谁（`cc_service.create`）；
  不在线的人下次打开 ERP 时由启动数据带出（`masters.boot`）。
- 已经接着 Express 的公司：电脑报上它的账套后马上排一次同步（不用人点「同步 Express」，也不等每 10 分钟看标记）。

哪些账套报给这个 ERP：CC 里「Express 位置」勾给 ERP 的那些（同一台电脑也连着 Cowork 时默认不勾，要人勾；只连 ERP 的电脑全给），
见 docs/work/2026-11-07-cc-shared-computer-isolation-评估.md。**只看还连着、按成员报账套的电脑**（CC 桌面版 :data:`SCOPED_FROM`
起、令牌没作废、没退役）：更早的版本报的是位置下全部账套，电脑断开（`devices.leave`）后名单也还留着。

没有有效 13 位税号的账套不在目录里：ERP 认账套（选账套、写单、同步）全靠税号 + 分店（评估 §3.4，第 4 批再做）。
"""
import frappe

from pearnly_core.cc_desktop.wire import request_contracts

from pearnly_erp_portal.express import contracts
from pearnly_erp_portal.inventory import warehouses

COMPANY = "ERP Company"
BOOK = "Pearnly CC Book"
DEVICE = "Pearnly CC Device"
SCOPED_FROM = "1.12.17"  # 起这一版 CC 桌面版的 ERP 成员只报勾给它的「Express 位置」下的账套（companion express_places.places_for）
COMPANIES_MAX = 2000  # 一个租户的公司（同 `cc_service.COMPANY_MAX`）
DEVICES_MAX = 500    # 一个租户连着的电脑（候选只看在用的几台）
BOOKS_MAX = 5000     # 这些电脑报的账套行（一台几十份）


EVENT = "pearnly_erp_new_ledgers"   # 连接里有还没接进 ERP 的公司：{count}；页面只对能建公司的人弹窗
PENDING_KEY = "erp-new-ledgers"      # 站点缓存：还要人勾的账套标识（:func:`announce` 算好，启动数据只读它）
PENDING_TTL = 6 * 3600
WAITING_SYNC = (None, "", "offline", "outdated")  # 接着 Express、还没读成的公司：电脑报上账套后马上排一次同步


def books_changed(device):
    """（共用通道钩子，心跳的事务里叫）一台电脑的账套名单有变化：排一次 :func:`announce`（同站点只留一件，提交后才排）。"""
    from pearnly_erp_portal.express import masters

    frappe.enqueue("pearnly_erp_portal.express.companies.announce", queue=masters.lane(), timeout=600,
                   enqueue_after_commit=True, job_id=f"erp-express-companies:{frappe.local.site}", deduplicate=True)


def pending(refresh=False) -> list:
    """还要人勾的账套标识（缓存；没有就现算）。"""
    found = None if refresh else frappe.cache.get_value(PENDING_KEY)
    if found is None:
        from pearnly_erp_portal.masters import cc_service

        found = cc_service.pending_identities()
        frappe.cache.set_value(PENDING_KEY, found, expires_in_sec=PENDING_TTL)
    return list(found)


def announce():
    """（后台任务）账套名单或 MR.ERP 目录变了之后：重算还要人勾的公司，比上次多了就推 :data:`EVENT`；接着 Express、电脑这时
    才报上账套的公司排一次同步。回 ``{"pending": 几家, "new": 几家, "queued": [公司]}``。"""
    from pearnly_erp_portal.express import masters, push

    before = set(frappe.cache.get_value(PENDING_KEY) or [])
    now = pending(refresh=True)
    fresh = [identity for identity in now if identity not in before]
    queued = []
    for row in frappe.get_all(COMPANY, filters={"express_enabled": 1, "is_active": 1},
                              fields=["name", "express_sync_state"], limit_page_length=COMPANIES_MAX):
        if row.express_sync_state in WAITING_SYNC and push.connected(row.name):
            masters.queue(row.name)
            queued.append(row.name)
    frappe.db.commit()
    if fresh:
        frappe.publish_realtime(EVENT, {"count": len(now)}, after_commit=True)
    return {"pending": len(now), "new": len(fresh), "queued": queued}


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


def build(tax, branch, name) -> str:
    """按一组账套建一家公司（后台补建与 CC「添加公司」同一个做法）：回公司编号。新建的由调用方排主档同步。"""
    # 按账套建的公司就是接了 Express 的（用户 10-29：已连 Express 的公司设为「接 Express」，类型默认普通）
    doc = frappe.get_doc({"doctype": COMPANY, "company_name": name[:200], "tax_id": tax, "branch": branch,
                          "is_active": 1, "pos_enabled": 0, "express_enabled": 1, "company_type": "general"})
    doc.insert(ignore_permissions=True)
    # 先给一个仓（同开户建公司）：首次同步前就能开单；同步时这个仓按 Express 的主仓码认领（`masters._warehouses`）
    warehouses.ensure_for_company(doc.name, "Main")
    return doc.name


def devices() -> list:
    """能据以建公司的电脑（:func:`scoped`）：连着、按成员报账套的那几台。"""
    return scoped(frappe.get_all(DEVICE, fields=["name", "token_digest", "retired_at", "companion_version"],
                                 limit_page_length=DEVICES_MAX))


def online_devices() -> list:
    """:func:`devices` 里此刻在线的（最近一次心跳 / 领单在 `cc_desktop.contracts.ONLINE_SECONDS` 内）。"""
    from datetime import datetime, timezone

    from pearnly_core.cc_desktop import contracts as desktop_contracts

    found = devices()
    if not found:
        return []
    moment = datetime.now(timezone.utc).replace(tzinfo=None)  # 表里的时间存 UTC（同 `cc_desktop.devices.now`）
    seen = frappe.get_all(DEVICE, filters={"name": ["in", found]}, fields=["name", "last_seen_at"],
                          limit_page_length=DEVICES_MAX)
    return [row.name for row in seen if desktop_contracts.online(row.last_seen_at, moment)]


def folder_of(root) -> str:
    """账套所在 Express 文件夹的名字（电脑报的根目录最后一段，如 ``69EXP``；同 CC 选公司弹窗第二行那一段）。"""
    parts = [part for part in str(root or "").replace("\\", "/").split("/") if part.strip()]
    return parts[-1].strip() if parts else ""


def catalog() -> dict:
    """在用电脑报的账套按「税号 + 分店」分组（同 :func:`ensure` 的口径）：``{(税号, 分店): {"name": 公司名, "years": [年度],
    "folders": [文件夹]}}``，年度新的在前。CC 里「添加公司」与 ERP「从连接添加」的候选从这里来。"""
    found = devices()
    if not found:
        return {}
    rows = frappe.get_all(BOOK, filters={"device": ["in", found]},
                          fields=["company", "label", "tax_id", "branch", "period_start", "period_end", "root"],
                          limit_page_length=BOOKS_MAX)
    years, folders = {}, {}
    for row in rows:
        tax = contracts.tax13(row.get("tax_id"))
        if not tax:
            continue
        key = (tax, contracts.branch5(row.get("branch")))
        year = str(row.get("period_start") or "")[:4]
        if year:
            years.setdefault(key, set()).add(year)
        folder = folder_of(row.get("root"))
        if folder:
            folders.setdefault(key, set()).add(folder)
    return {key: {"name": name, "years": sorted(years.get(key, ()), reverse=True),
                  "folders": sorted(folders.get(key, ()))} for key, name in groups(rows).items()}
