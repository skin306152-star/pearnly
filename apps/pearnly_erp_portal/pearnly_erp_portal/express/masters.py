"""Express 主档与库存回读进 ERP（ERP 当前台、Express 当账本，2026-10-22；10-23 起 CC 桌面版直连 ERP，ADR-025）。

经共用机器通道请办公室电脑读这家公司现用账套的整套主档与各仓结存（按需请求 ``express_masters``，账套的公共资料：够得到它的
电脑谁先来谁读），读好后在 ERP 里自动建档 / 更新：客户、供应商、商品（库存单位、售卖 / 进货单位、售价）、仓库（挂在这家公司下）；
整份另存一份（`facts`）给准备写账载荷用。主档以 Express 为准：

- 按「这家公司 + Express 编码」认档（``express_company`` + ``express_code``）：换了年度账套编码不变，不会重建一份；
  一个 ERP 租户挂几家公司，各自的档互不相认。``express_book`` 只记最近从哪份账套来。
- ERP 里没编码的同编码 / 同税号加分店 / 同名的档，第一次同步时认领过来，不另建一份。
- Express 里没有了的档（这家公司的）在 ERP 里停用；又出现了就启用。
- Express 里有的字段完全照 Express 写（空也写），在 ERP 里不能改（`locks`）；ERP 自有字段（图片、分类等）不动。
库存对齐成「Express 结存 + 还没写进 Express 的 ERP 单的变动」，差额记一笔调整流水（来源 ``express_sync``）。

自动同步（下面除手动以外的触发）只把公司记成「排队中」（:func:`queue`），每分钟 :func:`advance` 按顺序发起读取（同时最多
:data:`OPEN_READS` 家）、读好了才排后台任务落库，后台进程不空等电脑（生产 ERP 只有一个后台进程，写单任务也在里面）。
触发：Express 主档有改动（:func:`check_marks` 每 10 分钟请电脑看一眼现用账套主档几张表的大小与修改时间，标记和上次落库时
不同、或现用账套换了（新年度）才排整套同步）、每小时兜底一次（:func:`sync_all`：没有标记的旧版 CC 桌面版、一天没同步过的）、
ERP 单写进 Express 后一次（`push.job_changed`）、管理员在公司页手动一次（`api` →
:func:`start`）、准备写账时这份账套还没读过一次（:func:`ensure`）。**落库一律在后台任务里做**（10-23 生产：手动同步在网页请求
里落一份约 1 MB 的账套超过 120 秒被杀、整笔回滚）：电脑读一本账套一般十几秒，任务里等它读完（:data:`JOB_WAIT`），再落库
（上限 :data:`LAND_LIMIT`）。落库先一次取出现有档的比对字段，只对有变化的行读出保存，每 :data:`COMMIT_EVERY` 条提交一次。
每次同步的结果记在公司上（``express_sync_state`` / ``express_sync_note``，公司页显示），失败另记 Error Log；手动发起的做完
推给发起人（:data:`EVENT`）。电脑离线 / 还没读完：这次什么都不改，下次再来。
"""
from datetime import timedelta
from decimal import Decimal
import gzip
import json
import time

import frappe
from frappe.utils import now_datetime, nowdate

from pearnly_core.cc_desktop import requests

from pearnly_erp_portal.express import contracts, facts, push, target
from pearnly_erp_portal.inventory import contracts as stock_contracts
from pearnly_erp_portal.inventory import stock

COMPANY = "ERP Company"
CUSTOMER = "ERP Customer"
SUPPLIER = "ERP Supplier"
PRODUCT = "ERP Product"
WAREHOUSE = "ERP Warehouse"
SYNC_REASON = "express_sync"
KIND = "express_masters"
MARK_KIND = "express_marks"  # CC 桌面版 1.12.0 起：只看主档几张表的文件属性，回变化标记
STALE_AFTER = 24 * 3600      # 每小时兜底：超过这么久没同步的照样整套读一次
TOL = Decimal("0.000001")
JOB_WAIT = 180      # 后台同步等电脑读完的上限（秒）
LAND_LIMIT = 3600   # 落库的上限（秒）：大账套首次落库的实测见 docs/work/2026-10-23-erp-express-sync-background.md
COMMIT_EVERY = 500  # 落库每改这么多条提交一次（任务中途出错，已落的不丢；再同步一次幂等补齐）
POLL_EVERY = 8
EVENT = "pearnly_erp_express_sync"  # 手动同步做完推给发起人：{company, state, note}
NOTIFY_KEY = "erp-express-sync-notify:"  # 缓存：哪几个人在等这家公司的同步结果
# 记在公司上的同步状态（公司页显示）
QUEUED, READING, SYNCING, READY, FAILED = "queued", "reading", "syncing", "ready", "failed"
OFFLINE, OUTDATED = "offline", "outdated"
# 自动同步同时在读的公司最多这么多：电脑一本一本读（大账套约 50 秒），6 本约 5 分钟，在 10 分钟领取时限内；3 家时电脑常空等
# （10-24 实验栈 41 家重同步 60 分钟）
OPEN_READS = 6


def linked_companies():
    """接了 Express 的公司：启用、CC 桌面版报过它的账套。"""
    rows = frappe.get_all(COMPANY, filters={"is_active": 1}, fields=["name", "tax_id", "branch"])
    return [row for row in rows if contracts.tax13(row.tax_id) and push.linked(row.name)]


def queue(company):
    """自动同步排上号（新建的公司、主档有改动、写单后、每小时兜底）：只记「排队中」，由每分钟的 :func:`advance` 按顺序发起读取、
    读好了排后台任务落库；不占着后台进程等电脑。已经排着 / 在读 / 在落库的不动（落库被部署、重启打断的由 :func:`advance` 收回）。"""
    state = frappe.db.get_value(COMPANY, company, "express_sync_state")
    if state not in (QUEUED, READING, SYNCING):
        _record(company, {"state": QUEUED})


def _reachable(row) -> bool:
    """这家公司现用的账套此刻有能读的电脑（在线、版本够）。"""
    book = target.current(row, nowdate())
    return book is not None and requests.waiting(KIND, book["key"]) is None


def _job_id(name, company):
    return f"erp-express-{name}:{frappe.local.site}:{company}"


def _landing(company) -> bool:
    """这家公司的同步 / 落库任务还排着或在跑。"""
    from frappe.utils.background_jobs import is_job_enqueued

    return any(is_job_enqueued(_job_id(name, company)) for name in ("sync", "land"))


def advance():
    """（每分钟）推进自动同步：在读的读好了就排后台任务落库（请求过期了重发，电脑读失败就记失败与原因）；在读的不满
    :data:`OPEN_READS` 家就让排队最久的几家发起读取（没同步过的先来）；停在「同步中」、后台却已没有它的任务的（落库被部署、
    重启打断）排回队里。没有要推进的就一条查询。"""
    rows = frappe.get_all(COMPANY, filters={"express_sync_state": ["in", [QUEUED, READING, SYNCING, OFFLINE, OUTDATED]],
                                            "is_active": 1},
                          fields=["name", "tax_id", "branch", "express_sync_state"],
                          order_by="express_synced_at asc, name asc", limit_page_length=0)
    recheck = now_datetime().minute % 5 == 0  # 「未连接 / 要更新」的每 5 分钟看一次电脑回来没有（整夜关机时几十家）
    for row in rows:
        if row.express_sync_state == SYNCING and not _landing(row.name):
            _record(row.name, {"state": QUEUED})
            row.express_sync_state = QUEUED
        elif row.express_sync_state in (OFFLINE, OUTDATED) and recheck and _reachable(row):
            # 电脑回来了（或升级了）：排回队里，不等标记变化 / 一天兜底（10-24 验收：上线后还一直显示「未连接」）
            _record(row.name, {"state": QUEUED})
            row.express_sync_state = QUEUED
    reading = 0
    for row in rows:
        if row.express_sync_state != READING:
            continue
        found = _request(row.name)
        if found["state"] != READING:
            _record(row.name, found)
        elif found["made"]["status"] == "done":
            enqueue(row.name, wait=0)
        elif found["made"]["status"] == "requested" and (gone := requests.waiting(KIND, found["book"]["key"])):
            # 发出去后够得到的电脑都下线了：说原因，不干等到领取时限（10 分钟）；电脑回来时上面那段会排回队里
            _record(row.name, {"state": "outdated" if gone.get("reason") == "update_needed" else OFFLINE})
        else:
            reading += 1
    for row in rows:
        if row.express_sync_state != QUEUED or reading >= OPEN_READS:
            continue
        found = _request(row.name, fresh=True)
        _record(row.name, found)
        reading += found["state"] == READING


def enqueue(company, notify=None, wait=JOB_WAIT):
    """排一次后台同步（同一家公司排着的只留一件）。``notify``：做完推给这个用户（手动发起的）；``wait``：等电脑读完的上限
    （:func:`advance` 排的是已经读好的，不等）。"""
    if notify:
        # 记下要通知谁：自动同步那件已经排着时这件会按任务号去重掉，做完由那件一并推（10-24 审查）
        frappe.cache.hset(NOTIFY_KEY + company, notify, 1)
    frappe.enqueue("pearnly_erp_portal.express.masters.sync", queue="long", timeout=JOB_WAIT + LAND_LIMIT,
                   enqueue_after_commit=True, job_id=_job_id("sync", company), deduplicate=True, company=company,
                   wait=wait)


def _notify(company, result, extra=None):
    """手动发起过同步的人：推这次的结果（`company.js` 收到就提示并重画）。"""
    users = set(frappe.cache.hkeys(NOTIFY_KEY + company) or []) | ({extra} if extra else set())
    frappe.cache.delete_value(NOTIFY_KEY + company)
    for user in users:
        user = user.decode() if isinstance(user, bytes) else user
        frappe.publish_realtime(EVENT, {"company": company, "state": result.get("state"), "note": result.get("note") or ""},
                                user=user, after_commit=False)


def sync_all():
    """（每小时）兜底：没有变化标记的（电脑上是旧版 CC 桌面版，或还没同步过）、一天没同步过的，排整套同步；其余靠
    :func:`check_marks` 发现改动。"""
    names = [row.name for row in linked_companies()]
    if not names:
        return
    edge = now_datetime() - timedelta(seconds=STALE_AFTER)
    for row in frappe.get_all(COMPANY, filters={"name": ["in", names]},
                              fields=["name", "express_mark", "express_synced_at"]):
        if not row.express_mark or not row.express_synced_at or row.express_synced_at < edge:
            queue(row.name)


def check_marks():
    """（每 10 分钟）每家接了 Express 的公司：取上一次请电脑看的标记，和上次落库时的不同（主档改了）、或现用账套换了
    （新年度账套报上来了）就排整套同步；再发下一次看标记的请求。电脑不在线 / 旧版 CC 桌面版不认：不建，靠每小时兜底。"""
    for row in linked_companies():
        book = target.current(row, nowdate())
        if book is None:
            continue
        key = "erp_marks:" + book["key"]
        made = requests.find(MARK_KIND, key)
        if made is not None and made["status"] != "done":
            continue  # 上一次的还在排 / 在读
        if made is not None:
            seen = _mark(made)
            last = frappe.db.get_value(COMPANY, row.name, ["express_mark", "express_book"], as_dict=True) or {}
            if seen and (seen != last.get("express_mark") or (last.get("express_book") or "") != book["label"][:140]):
                queue(row.name)
        if requests.waiting(MARK_KIND, book["key"]) is None:
            requests.create(MARK_KIND, key, _params(book), book_key=book["key"], actor=frappe.session.user)


def _mark(made):
    content, _meta = requests.result(made["request"])
    requests.drop(made["request"])
    try:
        return str(json.loads(gzip.decompress(content or b"")).get("mark") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def start(company):
    """管理员在公司页点「同步 Express」：请求里只看能不能读、发起读取，不等电脑、不落库；读好、落库交后台任务（做完推给
    本人）。回 ``{"state": reading | offline | outdated | <原因码>}``。"""
    result = _request(company, fresh=True)
    if result.get("state") == READING:
        _record(company, {"state": READING})
        enqueue(company, notify=frappe.session.user)
    else:
        _record(company, result)
    return result


def sync(company, wait=0, notify=None):
    """（后台任务）同步一家公司：电脑还在读时每隔几秒再问，最多等 ``wait`` 秒；读好了落库。结果记在公司上，出错记 Error Log，
    ``notify`` 给了就推给这个用户。回 ``{"state": ready | reading | offline | outdated | failed | <原因码>, ...}``。"""
    deadline = time.monotonic() + max(0, int(wait or 0))
    try:
        while True:
            result = _sync_once(company)
            if result.get("state") != READING or time.monotonic() + POLL_EVERY > deadline:
                break
            frappe.db.commit()  # 不带着事务等
            time.sleep(POLL_EVERY)
    except Exception as error:  # noqa: BLE001  任何失败都要留下看得见的原因，不静默
        frappe.db.rollback()
        frappe.log_error(title="ERP Express master sync failed", message=frappe.get_traceback()[-3000:])
        result = {"state": FAILED, "note": f"{type(error).__name__}: {error}"[:500]}
    _record(company, result)
    frappe.db.commit()
    if result.get("state") != READING:
        _notify(company, result, notify)
        _next()
    return result


def _next():
    """落完一家就接着推进（读好的排落库、空出的名额发起下一家），不等下一分钟的 :func:`advance`。"""
    try:
        advance()
        frappe.db.commit()
    except Exception:  # noqa: BLE001  推进出错不影响这一家的结果，下一分钟的定时任务照常再来
        frappe.db.rollback()
        frappe.log_error(title="ERP Express master sync advance failed", message=frappe.get_traceback()[-3000:])


def _record(company, result):
    """这次同步的结果记在公司上（公司页显示）：读取中 / 落库中 / 已同步 / 电脑离线 / CC 桌面版要更新；其余是失败，原因码或
    出错信息记在说明里。"""
    state = result.get("state") or FAILED
    if state not in (QUEUED, READING, SYNCING, READY, "offline", "outdated"):
        note, state = result.get("note") or state, FAILED
    else:
        note = ""
    frappe.db.set_value(COMPANY, company, {"express_sync_state": state, "express_sync_note": note[:500] or None},
                        update_modified=False)


def _request_key(book):
    return "erp_masters:" + book["key"]


def _params(book):
    # 位置领单时换成领取那台电脑的写法（`request_contracts.localize`）
    return {"account_set": book["path"], "account_root": book.get("root") or ""}


def ensure(company, book):
    """准备写账时这份账套还没读过主档：请电脑读一次（已经在读、没有电脑能读都不建）；读好了就在这里存成账套资料（准备
    这一次就接着用），是这家公司现用的那份再排后台任务落进 ERP 主档（准备可能在网页请求里，不在这里落库）。同期几份账套时
    人选的那份不一定是主档同步读的那份（`target.current`），只靠同步落地会一直停在「正在读取」（10-23 验收第 3 轮）。"""
    made = requests.find(KIND, _request_key(book))
    if made is not None and made["status"] == "done":
        _land(company, book, made, inline=False)
    elif made is None and requests.waiting(KIND, book["key"]) is None:
        requests.create(KIND, _request_key(book), _params(book), book_key=book["key"], actor=frappe.session.user)


def _land(company, book, made, inline=True):
    """电脑读好的整套主档：存成这份账套的资料（`facts`），是这家公司现用的那份就落进 ERP 主档 —— ``inline`` 在这里落（已经
    在后台任务里），否则排后台任务 :func:`land` 落。回落了几条，或原因码。"""
    content, _meta = requests.result(made["request"])
    requests.drop(made["request"])
    try:
        data = json.loads(gzip.decompress(content or b""))
    except (OSError, ValueError):
        return {"state": "masters_invalid"}
    facts.save(book["key"], data)
    row = frappe.db.get_value(COMPANY, company, ["name", "tax_id", "branch"], as_dict=True)
    current = target.current(row, nowdate()) if row else None
    if not current or current["key"] != book["key"]:
        return {"state": READY, "account_set": book["key"], "label": book["label"]}
    if not inline:
        frappe.enqueue("pearnly_erp_portal.express.masters.land", queue="long", timeout=LAND_LIMIT,
                       enqueue_after_commit=True, job_id=_job_id("land", company),
                       deduplicate=True, company=company, book_key=book["key"], label=book["label"])
        return {"state": SYNCING, "account_set": book["key"], "label": book["label"]}
    _record(company, {"state": SYNCING})
    frappe.db.commit()  # 公司页看得到「落库中」
    counts = apply(company, book["label"], data)
    return {"state": READY, "account_set": book["key"], "label": book["label"], **counts}


def land(company, book_key, label):
    """（后台任务）把已存的账套资料落进 ERP 主档（:func:`ensure` 读好后排的）。结果记在公司上，出错记 Error Log。"""
    try:
        data = facts.load(book_key)
        if data is None:
            return {"state": "masters_invalid"}
        _record(company, {"state": SYNCING})
        frappe.db.commit()
        result = {"state": READY, **apply(company, label, data)}
    except Exception as error:  # noqa: BLE001
        frappe.db.rollback()
        frappe.log_error(title="ERP Express master sync failed", message=frappe.get_traceback()[-3000:])
        result = {"state": FAILED, "note": f"{type(error).__name__}: {error}"[:500]}
    _record(company, result)
    frappe.db.commit()
    _notify(company, result)
    _next()
    return result


def _request(company, fresh=False):
    """这家公司现用的那份账套读到哪一步了：没有请求就发起（够得到的电脑都不在线 / 太旧则不建）。上一件电脑读失败了：``fresh``
    （新一轮同步）就重发，否则回失败原因码（不每分钟重发、不占着在读的名额）。回
    ``{"state": reading | done | offline | outdated | <原因码>, "book", "made"}``。"""
    row = frappe.db.get_value(COMPANY, company, ["name", "tax_id", "branch"], as_dict=True)
    if not row or not contracts.tax13(row.tax_id):
        return {"state": "company_tax_missing"}
    book = target.current(row, nowdate())
    if book is None:
        return {"state": "account_set_missing"}
    made = requests.find(KIND, _request_key(book))
    if made is None and not fresh:
        last = requests.latest(KIND, _request_key(book))
        if last is not None and last["status"] == "failed":
            return {"state": last.get("code") or "masters_failed"}
    if made is None:
        waiting = requests.waiting(KIND, book["key"])
        if waiting:  # 够得到这份账套的电脑都不在线，或 CC 桌面版太旧不认这种请求：不建，过一会儿再来
            return {"state": "outdated" if waiting.get("reason") == "update_needed" else "offline"}
        made = requests.create(KIND, _request_key(book), _params(book), book_key=book["key"],
                               actor=frappe.session.user)
    return {"state": READING, "book": book, "made": made}


def _sync_once(company):
    found = _request(company)
    if found["state"] != READING:
        return found
    if found["made"]["status"] != "done":
        return {"state": READING}
    return _land(company, found["book"], found["made"])


def apply(company, book, data):
    """把读好的整套主档落进 ERP（幂等：再落一次不改任何东西）。回各类新建 / 更新 / 停用的条数。"""
    frappe.flags.express_sync_written = 0
    counts = {}
    counts["customers"] = _parties(CUSTOMER, company, book, data.get("customers"), _customer_fields)
    counts["suppliers"] = _parties(SUPPLIER, company, book, data.get("suppliers"), _supplier_fields)
    counts["products"] = _products(company, book, data)
    warehouses, counts["warehouses"] = _warehouses(company, book, data)
    counts["stock"] = _stock(company, warehouses, data.get("stock"))
    values = {"express_book": book[:140], "express_synced_at": now_datetime()}
    if data.get("mark"):  # 旧版 CC 桌面版读的没有标记：不清掉原来的（清掉了下一轮看标记必不同，又整套读一遍）
        values["express_mark"] = data["mark"]
    values.update(_profile(company, data.get("settings") or {}))
    frappe.db.set_value(COMPANY, company, values, update_modified=False)
    frappe.db.commit()
    return counts


def _profile(company, settings):
    """公司没填的地址、电话取账套设置里登记的（自动建的公司第一次同步时填上）；填过的不动（人改的为准）。"""
    have = frappe.db.get_value(COMPANY, company, ["address", "phone"], as_dict=True) or {}
    found = {"address": str(settings.get("address") or "").strip(), "phone": str(settings.get("phone") or "").strip()[:50]}
    return {field: value for field, value in found.items() if value and not (have.get(field) or "").strip()}


# ── 写入 ─────────────────────────────────────────────────────────────────────


def _same(value):
    """比对用：空值一律当空串，数字按数值。"""
    if value in (None, "") or value is False:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return str(value)


def _unit_rows(doc):
    return [{"unit_name": row.unit_name, "factor": str(contracts.d(row.factor_to_base).normalize())}
            for row in doc.get("units") or []]


def _differs(current, values):
    """预先取出的现有值（``current``，含商品的 ``units``）与要写的值有没有不同：没有就不用读出、保存这一行。"""
    for key, value in values.items():
        if key == "units":
            if current.get("units") != value:
                return True
        elif _same(current.get(key)) != _same(value):
            return True
    return False


def _written():
    """落库改了 / 建了一条：每 :data:`COMMIT_EVERY` 条提交一次（大账套首次落库几千条，不攒成一个大事务）。"""
    frappe.flags.express_sync_written = (frappe.flags.express_sync_written or 0) + 1
    if frappe.flags.express_sync_written % COMMIT_EVERY == 0:
        frappe.db.commit()
    return 1


def _upsert(doctype, name, values, current=None):
    """有就只改不一样的字段，没有就建。回 1 表示改了 / 建了。单行出错只记日志，不挡整次同步。
    ``current``：预先一次取出的这一行现有值（:func:`_mine`），一样就直接跳过，不读出整份文档。"""
    if name and current is not None and not _differs(current, values):
        return 0
    try:
        if name:
            doc = frappe.get_doc(doctype, name)
            diff = {key: value for key, value in values.items() if key != "units" and _same(doc.get(key)) != _same(value)}
            units = values.get("units")
            if units is not None and _unit_rows(doc) != units:
                doc.set("units", [{"unit_name": row["unit_name"], "factor_to_base": float(row["factor"])} for row in units])
                diff["units"] = True
            if not diff:
                return 0
            doc.update({key: value for key, value in diff.items() if key != "units"})
            doc.flags.express_sync = True
            doc.save(ignore_permissions=True)
            return _written()
        record = {key: value for key, value in values.items() if key != "units"}
        doc = frappe.get_doc({"doctype": doctype, **record})
        for row in values.get("units") or []:
            doc.append("units", {"unit_name": row["unit_name"], "factor_to_base": float(row["factor"])})
        doc.flags.express_sync = True
        doc.insert(ignore_permissions=True)
        return _written()
    except frappe.ValidationError:
        frappe.clear_messages()
        frappe.log_error(title="ERP Express master sync row failed", message=frappe.get_traceback()[-1500:])
        return 0


def _claim(claimed, *finders):
    """这一行 Express 档落到 ERP 哪一条：按编码、税号 + 分店、名称依次认（认到了就不再往下找）；一条 ERP 档一次同步只给
    一行 Express 档，已被前面的行认走的跳过（否则两行认到同一条，后一行把前一行的编码改掉，前一行就丢了）。"""
    for find in finders:
        name = find()
        if name and name not in claimed:
            claimed.add(name)
            return name
    return None


def _retire(doctype, names, current=None):
    """这家公司在 Express 里已经没有了的档：停用（同步自己写，不受锁）。"""
    changed = 0
    for name in names:
        changed += _upsert(doctype, name, {"is_active": 0}, (current or {}).get(name))
    return changed


def _mine(doctype, company, fields):
    """ERP 现有的档：这家公司同步来的按编码认；没归属却带编码的（旧数据）按编码也认；其余是可认领的。``fields`` 是要比对的
    字段，一次取出（:func:`_upsert` 据此跳过没变的行）。回 (按编码认, 这家公司的全部档名（同编码多条的都在，没认领到的停用）,
    可认领的, ``{档名: 现有值}``)。"""
    rows = _scoped(doctype, company, list(dict.fromkeys(["name", "express_code", "express_company", *fields])))
    current = {row.name: row for row in rows}
    by_code = {}
    for row in sorted(rows, key=lambda item: item.express_company != company):  # 同编码几条时认最早的那条
        if row.express_code and row.express_company in (company, None, ""):
            by_code.setdefault(row.express_code, row.name)
    mine = [row for row in rows if row.express_code and row.express_company == company]
    for row in mine:  # 1.11.0 之前的 CC 把编码里的不换行空格读成了普通空格：照普通空格也认回原来那条
        by_code.setdefault(_loose(row.express_code), row.name)
    # 没挂 Express 的本地档：只在租户只有一家公司时认领。几家公司时本地档各家共用，认领进一家就从别家的列表、选择里消失了
    # （主档按公司隔离，10-24 审查），这时 Express 的档另建一条
    single = len(frappe.get_all(COMPANY, filters={"is_active": 1}, limit_page_length=2)) <= 1
    free = [row for row in rows if not row.express_code] if single else []
    return by_code, [row.name for row in mine], free, current


def _scoped(doctype, company, fields):
    """这家公司同步来的档 + 本地档（别家公司的对认档没用；上百家公司时不把全租户的档都读进来）。按建档先后。"""
    rows = frappe.get_all(doctype, filters={"express_company": company}, fields=fields, order_by="creation asc",
                          limit_page_length=0)
    return rows + frappe.get_all(doctype, filters={"express_company": ["is", "not set"]}, fields=fields,
                                 order_by="creation asc", limit_page_length=0)


def _first(seen, code):
    """同一本账套里同一个编码只认第一行（Express 的表不管编码重不重；10-24 测试机：重的那行每次同步都多建一条档）。按原样比：
    只差不换行空格 / 普通空格的是 Express 里两件不同的档（写单按原样认编码），不能并成一件。"""
    if code in seen:
        return False
    seen.add(code)
    return True


def _loose(code):
    return str(code or "").replace("\xa0", " ")


# ── 客户 / 供应商 ─────────────────────────────────────────────────────────────


def _customer_fields(item):
    # 与 ERP 客户保存时的归一同一口径（个人不存税号与分店，总店不存分店号），免得每次同步都当成有改动
    company = bool(item["tax_id"])
    return {"customer_name": item["name"], "tax_id": item["tax_id"] if company else None,
            "branch_no": item["branch"] if company and not item["head"] else None,
            "branch_type": ("hq" if item["head"] else "branch") if company else None,
            "party_type": "company" if company else "person",
            "address": item["address"] or None, "contact_phone": item["phone"] or None}


def _supplier_fields(item):
    return {"supplier_name": item["name"], "tax_id": item["tax_id"] or None,
            "branch_no": None if item["head"] else item["branch"],
            "branch_type": "head_office" if item["head"] else "branch",
            "address": item["address"] or None, "phone": item["phone"] or None}


PARTY_FIELDS = {CUSTOMER: ("customer_name", "tax_id", "branch_no", "branch_type", "party_type", "address", "contact_phone"),
                SUPPLIER: ("supplier_name", "tax_id", "branch_no", "branch_type", "address", "phone")}
SYNC_FIELDS = ("express_book", "is_active")


def _parties(doctype, company, book, rows, fields_of):
    name_field = "customer_name" if doctype == CUSTOMER else "supplier_name"
    by_code, ours, free, current = _mine(doctype, company, [*PARTY_FIELDS[doctype], *SYNC_FIELDS])
    free_tax, free_name = {}, {}
    for row in free:
        if contracts.tax13(row.tax_id):
            free_tax.setdefault((row.tax_id, contracts.branch5(row.branch_no)), row.name)
        free_name.setdefault(str(row.get(name_field) or "").strip().casefold(), row.name)
    changed, claimed, seen = 0, set(), set()
    for raw in contracts.rows(rows):
        item = contracts.party(raw)
        if not _first(seen, item["code"]):
            continue
        values = {**fields_of(item), "express_code": item["code"], "express_company": company,
                  "express_book": book[:140], "is_active": 1}
        name = _claim(claimed, lambda: by_code.get(item["code"]), lambda: by_code.get(_loose(item["code"])),
                      lambda: free_tax.pop((item["tax_id"], item["branch"]), None) if item["tax_id"] else None,
                      lambda: free_name.pop(item["name"].casefold(), None))
        changed += _upsert(doctype, name, values, current.get(name))
    return changed + _retire(doctype, [name for name in ours if name not in claimed], current)


# ── 商品 ─────────────────────────────────────────────────────────────────────

PRODUCT_FIELDS = ("code", "barcode", "product_name", "base_unit", "unit", "express_stock", "unit_price", "price_set",
                  *SYNC_FIELDS)


def _products(company, book, data):
    from pearnly_erp_portal.products import service as products

    units = contracts.unit_names(data)
    by_code, ours, free, current = _mine(PRODUCT, company, PRODUCT_FIELDS)
    for row in frappe.db.sql(f"""select unit.parent, unit.unit_name, unit.factor_to_base
            from `tabERP Product Unit` unit join `tab{PRODUCT}` product on product.name = unit.parent
            where unit.parenttype = %s and (product.express_company is null or product.express_company = ''
                or product.express_company = %s)
            order by unit.parent asc, unit.idx asc""", (PRODUCT, company), as_dict=True):
        if row.parent in current:  # 与 `_unit_rows` 同一写法，比对时与要写的单位表直接比
            current[row.parent].setdefault("units", []).append(
                {"unit_name": row.unit_name, "factor": str(contracts.d(row.factor_to_base).normalize())})
    for row in current.values():
        row.setdefault("units", [])
    free_code = {row.code: row.name for row in free if row.code}
    free_name = {}
    for row in free:
        free_name.setdefault(str(row.product_name or "").strip().casefold(), row.name)
    # 条码在这家公司看得到的范围里（本公司的档 + 本地档）不能重（`products.service.taken`）；同一本账套里两件同条码的
    # （Express 允许）后一件不写条码，写过的随写随记
    taken_barcodes = {row.barcode: row.name for row in current.values() if row.barcode}
    changed, claimed, seen = 0, set(), set()
    for raw in contracts.rows(data.get("products")):
        item = contracts.product(raw, units)
        if not _first(seen, item["code"]):
            continue
        name = _claim(claimed, lambda: by_code.get(item["code"]), lambda: by_code.get(_loose(item["code"])),
                      lambda: free_code.pop(item["code"], None),
                      lambda: free_name.pop(item["name"].casefold(), None))
        values = {"product_name": item["name"], "base_unit": item["base_unit"], "unit": item["unit"],
                  "units": [{"unit_name": row["unit_name"], "factor": str(contracts.d(row["factor"]).normalize())}
                            for row in item["units"]],
                  "express_code": item["code"], "express_company": company, "express_book": book[:140],
                  "express_stock": int(item["stock"]), "is_active": 1}
        if not name and not products.taken("code", item["code"], owner=company):
            # 问库而不是在内存里比：按库的排序规则比（泰文声调符号等会被当成一样），内存逐字比会漏
            values["code"] = item["code"]
        # 售价、条码完全照 Express（空就清空；条码被别的商品占着时不写，免得撞唯一键）
        values.update(unit_price=float(item["price"] or 0), price_set=1 if item["price"] else 0)
        if not item["barcode"]:
            values["barcode"] = None
        elif taken_barcodes.get(item["barcode"], name) == name:
            values["barcode"] = item["barcode"]
            taken_barcodes[item["barcode"]] = name or ("new", item["code"])
        changed += _upsert(PRODUCT, name, values, current.get(name))
    return changed + _retire(PRODUCT, [name for name in ours if name not in claimed], current)


# ── 仓库与库存 ───────────────────────────────────────────────────────────────


def _warehouses(company, book, data):
    """这家公司的仓库按 Express 仓库码建 / 认领 / 改名 / 停用：回 (``{仓库码: ERP 仓库}``, 改动条数)。
    第一次同步时，这家公司唯一一个没编码的仓库认作 Express 的主仓（ERP 开户自带一个仓库）。"""
    existing = frappe.get_all(WAREHOUSE, filters={"company": company},
                              fields=["name", "warehouse_name", "express_code"], limit_page_length=0)
    by_code = {row.express_code: row.name for row in existing if row.express_code}
    free = [row for row in existing if not row.express_code]
    free_name = {str(row.warehouse_name or "").strip().casefold(): row.name for row in free}
    main = str((data.get("settings") or {}).get("mainloc") or "").strip()
    found, changed = {}, 0
    for raw in contracts.rows(data.get("warehouses")):
        code, label = str(raw["code"]).strip()[:4], str(raw.get("name") or raw["code"]).strip()[:140]
        name = by_code.get(code) or free_name.pop(label.casefold(), None)
        if not name and code == main and len(free) == 1 and free[0].name in free_name.values():
            name = free[0].name
            free_name = {key: value for key, value in free_name.items() if value != name}
        changed += _upsert(WAREHOUSE, name, {"warehouse_name": label, "express_code": code, "express_book": book[:140],
                                             "company": company, "is_active": 1})
        found[code] = name or frappe.db.get_value(WAREHOUSE, {"company": company, "express_code": code}, "name")
    changed += _retire(WAREHOUSE, [name for code, name in by_code.items() if code not in found])
    return {code: name for code, name in found.items() if name}, changed


def _pending(company, products, warehouses):
    """还没写进 Express 的 ERP 单已经过了的库存变动：``{(存货码, 仓库码): 带符号基本单位数量}``。"""
    refs = []
    for doctype, ref_type in (("ERP Sales Document", "ERP Sales Document"), ("Purchase Document", "purchase")):
        names = frappe.get_all(doctype, filters={"company": company, "status": "submitted",
                                                 "express_state": ["in", contracts.OPEN_STATES]},
                               pluck="name", limit_page_length=0)
        refs.extend((ref_type, name) for name in names)
    if not refs:
        return {}
    code_of = {name: code for code, name in products.items()}
    loc_of = {name: code for code, name in warehouses.items()}
    pending = {}
    for ref_type in {ref for ref, _ in refs}:
        ids = [name for ref, name in refs if ref == ref_type]
        for row in frappe.get_all(stock.MOVEMENT, filters={"ref_type": ref_type, "ref_id": ["in", ids]},
                                  fields=["product", "warehouse", "qty_base"], limit_page_length=0):
            key = (code_of.get(row.product), loc_of.get(row.warehouse))
            if key[0] and key[1]:
                pending[key] = pending.get(key, Decimal(0)) + contracts.d(row.qty_base)
    return pending


def _stock(company, warehouses, stock_rows):
    """ERP 结存对齐 Express：每个（Express 存货 × Express 仓库）差多少记一笔调整流水。回调整的笔数。
    只认这家公司同步来的库存品（编码在公司内唯一）。"""
    if not warehouses:
        return 0
    products = {row.express_code: row.name for row in frappe.get_all(
        PRODUCT, filters={"express_company": company, "express_code": ["is", "set"], "express_stock": 1},
        fields=["name", "express_code"], order_by="creation asc", limit_page_length=0)}
    targets = contracts.stock_targets(stock_rows, _pending(company, products, warehouses))
    current = {}
    for row in frappe.get_all(stock.BALANCE, filters={"warehouse": ["in", list(warehouses.values())]},
                              fields=["product", "warehouse", "qty_on_hand"], limit_page_length=0):
        key = (row.product, row.warehouse)
        current[key] = current.get(key, Decimal(0)) + contracts.d(row.qty_on_hand)
    wanted = {}
    for (code, loccod), qty in targets.items():
        if code in products and loccod in warehouses:
            wanted[(products[code], warehouses[loccod])] = qty
    linked = set(products.values())
    for key in current:
        if key[0] in linked and key not in wanted:
            wanted[key] = Decimal(0)  # Express 里这个仓已经没有了
    stamp = now_datetime().strftime("%Y%m%d%H%M%S")
    moved = 0
    for (product, warehouse), qty in wanted.items():
        delta = qty - current.get((product, warehouse), Decimal(0))
        if abs(delta) <= TOL:
            continue
        try:
            stock.apply_movement(product=product, warehouse=warehouse, movement_type=stock_contracts.ADJUST,
                                 qty=float(delta), posting_date=nowdate(), ref_type=SYNC_REASON, reason=SYNC_REASON,
                                 client_uuid=f"{SYNC_REASON}:{stamp}:{product}:{warehouse}"[:140],
                                 internal=True, allow_inactive=True, company=company)
            moved += 1
        except frappe.ValidationError:
            frappe.clear_messages()
            frappe.log_error(title="ERP Express stock sync failed", message=frappe.get_traceback()[-1500:])
    return moved
