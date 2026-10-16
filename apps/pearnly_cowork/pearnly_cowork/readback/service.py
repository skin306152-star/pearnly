"""Express 回读的服务（2026-10 CC 闭环 L1）：请那台电脑读一本账套的一个月、收结果、出四份工作簿与差异、留存、取用、删除。

用户 2026-10-13 定：只在用户请求时按「账套 + 月份」读取，存为站点私有文件；同一人、同一连接、同一账套、同一月只留最新一份，
发起人可删除。发起的判据同推送：录入权限 + 这条连接的使用授权（`companion.request_service.create` 再核一次）。
不调模型、不扣费、不写 Express。

谁看得到（用户 2026-10-15 夜改）：能用这条 Express 连接的人（录入权限 + 这条连接的使用授权，逐条连接核）都看得到这本账套
这个月的回读结果，并看到是谁、何时发起的；没有这条连接使用权的人照样看不到（当作不存在）。删除仍只给发起人；还在读的那份
只由发起人的查询推进（按需请求只认发起人）。税务草稿与月结台账取「这本账套这个月最近一次完成的回读」，不分发起人。

- ``request(connection, account_set_key, period, accounts=None)``：同一份还在读就回那一份；同一请求号（人、连接、账套、
  月、科目）的按需请求还在等、在做或做完没取走就接着等它（`request_service.find`，不问电脑在不在）；否则够得到这份账套的
  电脑此刻都没来领按需请求（离线 / CC 桌面版太旧）回 ``{"status": "waiting_for_cc", "reason"}``，什么都不建；能领就带上
  推送过的单据号建一份 → ``reading``。请求不定向、带这份账套的规范键：只由发起人自己登录过 CC 的、够得到它的电脑领
  （ADR-021 第 54 条，用户 10-16「谁的活谁的电脑做」；他有电脑却都够不到这份账套回 ``reason: unreachable``，不转给同事的
  电脑），参数里的账套位置换成领取那台的写法（ADR-021 第 4 批，`request_contracts`）。
- ``status(name)``：还在读就看请求：电脑做完了就当场处理（gunzip → `contracts.parse` → 推送记录再按本月的单据、凭证与
  税务记录的单号补查 → `diff.compare` → 四份工作簿 → 留存 → ready，删掉同一份的旧回读），失败 / 过期 / 处理出错记 failed；
  电脑没来领回 waiting_for_cc。
- ``file(name, kind)`` → (文件名, 内容)；``data(name)``：校验过的读账结果；``delete(name)``：连文件一起删。
- ``vat_records(connection, account_set_key, year, month)``：给税务草稿（L4 / L5）的进销项记录，这本账套这个月最近一次完成的
  回读（谁发起的都算）；没有回 None（调用方提示先回读）。
- ``month_status(sets, period)``：月结台账「核对」一段（hooks ``cowork_company_month_readback``，companies 不导入本域）：
  每本账套这个月最近的回读状态、发起人与差异计数。
"""
import hashlib
import json
import ntpath
import re
import zlib
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import get_datetime

from pearnly_cowork.access import scope
from pearnly_cowork.companion import devices, request_service
from pearnly_cowork.connections import access as connection_access
from pearnly_cowork.connections import service as connections
from pearnly_cowork.push.application import service as push_service
from pearnly_cowork.readback import contracts, diff, storage, workbooks
from pearnly_cowork.readback.access import READBACK, read, write
from pearnly_cowork.targets.identity import normalize_express_account_key

CONNECTION = "Cowork ERP Connection"
KIND = "express_read"
DATA_VERSION = 1
FILE_KINDS = ("general_ledger", "sales_vat", "purchase_vat", "changes")
CHANGES_KEPT = 200
# 解压后的读账结果最多这么大（防压缩包解开过大）
RAW_MAX_BYTES = 400 * 1024 * 1024
BANGKOK = ZoneInfo("Asia/Bangkok")
_NAME = re.compile(r"^[0-9a-f]{32}$")


def _now():
    """库里一律存 UTC（裸时间）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _error(code, exc=None):
    frappe.throw(_(code), exc or frappe.ValidationError)


def _json(value, default=None):
    try:
        parsed = json.loads(value) if value else default
    except (TypeError, ValueError):
        return default
    return parsed if parsed is not None else default


def _lookup(actor, connection, key, period):
    return hashlib.sha256("\x1f".join((actor, connection, key, period)).encode("utf-8")).hexdigest()


def _latest(lookup, statuses):
    """本人这本账套这个月最近的一份（发起时接着等自己还在读的那份）。"""
    marks = ", ".join(["%s"] * len(statuses))
    found = frappe.db.sql(f"""SELECT name FROM `tab{READBACK}` WHERE lookup = %s AND actor = %s
        AND status IN ({marks}) ORDER BY requested_at DESC, name DESC LIMIT 1""",
                          (lookup, frappe.session.user, *statuses))
    return found[0][0] if found else None


def _account(catalog, account_set_key):
    """连接目录里的这本账套：{key, path, root, label, folder}；不在（或不是 Express 目录）就报错。"""
    wanted = normalize_express_account_key(account_set_key)
    for row in (catalog or {}).get("account_sets") or []:
        if wanted and normalize_express_account_key(row.get("source_id")) == wanted:
            attributes = row.get("attributes") or {}
            path = str(attributes.get("path") or row.get("source_id") or "")
            return {"key": wanted, "path": path, "root": str(attributes.get("root") or ""),
                    "label": str(row.get("label") or "")[:200],
                    "folder": ntpath.basename(path.replace("/", "\\").rstrip("\\"))}
    _error("cowork.readback.account_missing")


def _request_key(lookup, accounts):
    """按需请求号：同一人、同一连接、同一账套、同一月、同一组总账科目是同一件（`request_service.find` 据此找回）。"""
    seed = json.dumps([lookup, list(accounts)], ensure_ascii=False)
    return "readback:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:48]


def _holder(lookup, request):
    """本人还在读、等着这件按需请求的那份回读。"""
    found = frappe.db.sql(f"""SELECT name FROM `tab{READBACK}` WHERE lookup = %s AND actor = %s AND request = %s
        AND status = 'reading' ORDER BY requested_at DESC LIMIT 1""", (lookup, frappe.session.user, request))
    return found[0][0] if found else None


def _known_version(connection):
    """池里电脑报过的最新 CC 桌面版版本（它们从没来领过按需请求时据此分辨离线与要升级）。

    以前读连接配置里的 ``companion_version``，那个键从来不在配置里，永远是空（ADR-021 第 2 批改读电脑行）。"""
    try:
        return devices.newest_version(connection)
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
        frappe.clear_messages()
        return ""


# ── 发起 ────────────────────────────────────────────────────────────────────


def request(connection, account_set_key, period, accounts=None) -> dict:
    scope.require("intake")
    try:
        when, only = contracts.period(period), contracts.prefixes(accounts)
    except contracts.ContractError as error:
        _error(error.code)
    if not isinstance(connection, str) or not connection or len(connection) > 140:
        _error("cowork.readback.invalid")
    catalog = connections.targets(connection)
    if connections.adapter_of(connection) != "express":
        _error("cowork.readback.not_express")
    account = _account(catalog, account_set_key)
    actor = frappe.session.user
    lookup = _lookup(actor, connection, account["key"], when)
    current = _latest(lookup, ("reading",))
    if current and _json(read(current).params_json, {}).get("accounts") == list(only):
        found = status(current)  # 同一份还在读：回它，不让电脑再读一遍
        if found["status"] != "failed":
            return found
    # 照按需请求的做法（`request_contracts` 文件头）：先找同一请求号还没了结的那一件（电脑正在读、传它时可能好一阵
    # 不来领，不能因此判离线），找到就接着等；没有再问电脑在不在，最后才建
    key = _request_key(lookup, only)
    made = request_service.find(KIND, connection, key)
    holder = _holder(lookup, made["request"]) if made else None
    if holder:
        found = status(holder)
        if found["status"] != "failed":
            return found
        made = None  # 那份处理失败时请求已一并删掉
    if not made:
        waiting = request_service.waiting(connection, KIND, _known_version(connection), account_key=account["key"],
                                          user=actor)
        if waiting:
            return waiting
        first, last = contracts.month_range(when)
        pushed = push_service.pushed_express(connection, account["key"], first, last)
        # 点名推送过的单号与 POS 日汇总票同一次写入的转账凭证号（会计改到别的月也读得到）
        named = [item["docnum"] for item in pushed]
        named += [written["voucher"] for item in pushed for written in item.get("vouchers") or []]
        try:
            params = contracts.read_params(account["path"], account["root"], when, named)
        except contracts.ContractError as error:
            _error(error.code)
        made = request_service.create(KIND, connection, params, key, account_key=account["key"])
    name = uuid4().hex
    row = frappe.new_doc(READBACK)
    row.update({"readback_id": name, "lookup": lookup, "actor": actor, "connection": connection,
                "account_set_key": account["key"], "account_label": account["label"], "period": when,
                "status": "reading", "request": made["request"], "requested_at": _now(), "data_version": 0,
                "params_json": json.dumps({"accounts": list(only), "folder": account["folder"]}, ensure_ascii=False)})
    write(row)
    return view(row)


# ── 状态与处理 ──────────────────────────────────────────────────────────────


def _newest_ready(connection, key, period):
    """这本账套这个月最近一次完成的回读（谁发起的都算；调用方已核这条连接的使用权）。"""
    found = frappe.db.sql(f"""SELECT name FROM `tab{READBACK}` WHERE connection = %s AND account_set_key = %s
        AND period = %s AND status = 'ready' ORDER BY ready_at DESC, name DESC LIMIT 1""", (connection, key, period))
    return found[0][0] if found else None


def _row(name):
    if not isinstance(name, str) or not _NAME.fullmatch(name) or not frappe.db.exists(READBACK, name):
        _error("cowork.readback.missing", frappe.DoesNotExistError)
    return read(name)


def _usable(connection) -> bool:
    """当前用户能不能用这条连接（录入权限 + 连接使用授权）。只读的可见性检查：授权读本事务快照、不加锁（``lock=False``，
    同 `connections` 的只读视图；台账在这之前已跑过别的查询，加锁读 `tabUser` 会撞 MariaDB 11.8 的 1020）。"""
    try:
        scope.require("intake")
        connection_access.require_connection(frappe.get_doc(CONNECTION, connection), lock=False)
    except (frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError):
        frappe.clear_messages()
        return False
    return True


def _own(name):
    """发起人自己的一份（删除用）。"""
    row = _row(name)
    if row.actor != frappe.session.user:
        _error("cowork.readback.missing", frappe.DoesNotExistError)
    return row


def _visible(name):
    """看得到的一份：能用这条连接的人都看得到（文件头「谁看得到」）；看不到的当作不存在。"""
    row = _row(name)
    if not _usable(row.connection):
        _error("cowork.readback.missing", frappe.DoesNotExistError)
    return row


def _sharing(row):
    """另一份还在读、等着同一件按需请求的回读。模型同时发两次一样的回读时，第二次建请求撞上唯一键、拿到第一次建的那一件
    （`request_service.create`），两份回读共用一件请求：先了结的一份不能删掉它，否则另一份就读不到了。"""
    found = frappe.db.sql(f"""SELECT name FROM `tab{READBACK}` WHERE lookup = %s AND actor = %s AND request = %s
        AND status = 'reading' AND name != %s LIMIT 1""", (row.lookup, row.actor, row.request, row.name))
    return bool(found)


def _drop_request(row):
    if not row.request or _sharing(row):
        return  # 共用这件请求的另一份还在读：留给它，最后一份了结时再删
    try:
        request_service.drop(row.request)
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
        frappe.clear_messages()


def _fail(name, code):
    row = read(name, for_update=True)
    if row.status != "reading":
        return row
    row.update({"status": "failed", "error_code": str(code or "failed")[:140], "ready_at": _now()})
    write(row)
    _drop_request(row)
    return row


def status(name) -> dict:
    """这份回读此刻的样子（还在读时顺带看电脑做完没有，做完当场处理；别人发起、还在读的只回现状）。"""
    row = _visible(name)
    if row.status != "reading" or row.actor != frappe.session.user:
        return view(row)
    try:
        current = request_service.status(row.request)
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
        frappe.clear_messages()
        return view(_fail(row.name, "request_missing"))
    if current["status"] == "done":
        return view(_process(row.name))
    if current["status"] in ("failed", "expired"):
        return view(_fail(row.name, current["code"] or current["status"]))
    out = view(row)
    if current["status"] == "requested":
        waiting = request_service.waiting(row.connection, KIND, _known_version(row.connection),
                                          account_key=row.account_set_key, user=row.actor)
        if waiting:
            out.update(waiting)
    return out


def _gunzip(content):
    try:
        reader = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = reader.decompress(content, RAW_MAX_BYTES + 1)
    except zlib.error as error:
        raise contracts.ContractError("cowork.readback.data_invalid") from error
    if len(raw) > RAW_MAX_BYTES or reader.unconsumed_tail:
        raise contracts.ContractError("cowork.readback.data_invalid")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise contracts.ContractError("cowork.readback.data_invalid") from error


def _filenames(folder, period):
    tail = re.sub(r"[^A-Za-z0-9_-]+", "_", str(folder or ""))[:40].strip("_") or "express"
    return {"general_ledger": f"{tail}-GL-{period}.xlsx", "sales_vat": f"{tail}-sales-VAT-{period}.xlsx",
            "purchase_vat": f"{tail}-purchase-VAT-{period}.xlsx", "changes": f"{tail}-changes-{period}.xlsx"}


def _read_at(value):
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return _now()


def _labels():
    """差异表的表名、列名与值，按请求人的语言。"""
    return {"sheet_changes": _("cowork.readback.sheet.changes"), "sheet_direct": _("cowork.readback.sheet.direct"),
            "docnum": _("cowork.readback.col.docnum"), "direction": _("cowork.readback.col.direction"),
            "change": _("cowork.readback.col.change"), "field": _("cowork.readback.col.field"),
            "pushed": _("cowork.readback.col.pushed"), "now": _("cowork.readback.col.now"),
            "date": _("cowork.recon.col.date"), "kind": _("cowork.readback.col.kind"),
            "party": _("cowork.readback.col.party"), "before_vat": _("cowork.recon.col.preVat"),
            "vat": _("cowork.recon.col.vat"), "total": _("cowork.recon.col.total"),
            "entered_by": _("cowork.readback.col.entered_by"),
            "directions": {"purchase": _("intake.dir.purchase"), "sales": _("intake.dir.sales")},
            "changes": {code: _("cowork.readback.change." + code) for code in diff.CHANGES},
            "fields": {"date": _("cowork.recon.col.date"), "kind": _("cowork.readback.col.kind"),
                       "party": _("cowork.readback.col.party"), "ref_no": _("cowork.readback.field.ref_no"),
                       "before_vat": _("cowork.recon.col.preVat"), "vat": _("cowork.recon.col.vat"),
                       "total": _("cowork.recon.col.total"), "vat_record": _("cowork.readback.field.vat_record"),
                       "vat_before_vat": _("cowork.readback.field.vat_before_vat"),
                       "vat_amount": _("cowork.readback.field.vat_amount"),
                       "entries": _("cowork.readback.field.entries")}}


def _process(name):
    """电脑交回的读账结果 → 四份工作簿 + 差异 + 留存 → ready。锁住这一行再判状态：同时来两次只处理一次。之后全是确定性的
    计算：交回的内容不合规记 failed（码见 `contracts`），其余任何意外也记 failed（process_failed，留错误日志），不让这一份
    一直停在 reading。"""
    row = read(name, for_update=True)
    if row.status != "reading":
        return row
    try:
        content, _meta = request_service.result(row.request)
    except (frappe.ValidationError, frappe.DoesNotExistError, OSError):
        frappe.clear_messages()
        return _fail(row.name, "result_missing")
    try:
        raw = _gunzip(content)
        _build(row, raw, contracts.parse(raw, row.period))
    except contracts.ContractError as error:
        storage.drop(row.name)
        return _fail(row.name, error.code)
    except Exception:  # noqa: BLE001 — 见上
        frappe.log_error(title="Cowork readback processing failed", message=frappe.get_traceback()[-1500:])
        frappe.clear_messages()
        storage.drop(row.name)
        return _fail(row.name, "process_failed")
    write(row)
    _drop_request(row)
    _drop_older(row)
    return row


def _build(row, raw, data):
    """出四份工作簿、存读账结果、把摘要与差异写进 ``row``（不存库，调用方存）。"""
    params = _json(row.params_json, {})
    first, last = contracts.month_range(row.period)
    # 推送记录：单据日在本月的，加上会计改到本月的单与本月税务记录的单（顺延抵扣的进项要带识别记录）
    pushed = push_service.pushed_express(row.connection, row.account_set_key, first, last,
                                         sorted(set(diff.month_numbers(data)) | set(diff.tax_numbers(data))))
    found = diff.compare(pushed, data)
    accounts = contracts.ledger(data, contracts.prefixes(params.get("accounts")))
    sales, purchases = contracts.vat_rows(data, "S"), contracts.vat_rows(data, "P")
    read_at = _read_at(data["read_at"])
    printed = read_at.replace(tzinfo=timezone.utc).astimezone(BANGKOK)
    names = _filenames(params.get("folder"), row.period)
    for kind, content in (
            ("general_ledger", workbooks.general_ledger(data["company"]["name"], row.period, accounts,
                                                        printed.date())),
            ("sales_vat", workbooks.vat_report("S", data["company"], row.period, sales)),
            ("purchase_vat", workbooks.vat_report("P", data["company"], row.period, purchases)),
            ("changes", workbooks.changes(found, _labels()))):
        storage.put(row.name, names[kind], content)
    storage.put_data(row.name, {"readback": raw, "history": {item["docnum"]: item["history"]
                                                             for item in pushed if item.get("history")}})
    summary = {
        "company": data["company"]["name"], "tax_id": data["company"]["tax_id"],
        "account_set": row.account_label or data["account_set"], "period": row.period,
        "read_at": printed.strftime("%Y-%m-%d %H:%M"),
        "general_ledger": {"accounts": len(accounts), "lines": sum(len(item["lines"]) for item in accounts),
                           "debit": f"{sum((item['debit'] for item in accounts), contracts.ZERO):.2f}",
                           "credit": f"{sum((item['credit'] for item in accounts), contracts.ZERO):.2f}",
                           **({"accounts_filter": params["accounts"]} if params.get("accounts") else {})},
        "sales_vat": contracts.totals(sales), "purchase_vat": contracts.totals(purchases),
        "pushed": found["counts"], "warnings": contracts.warnings(data)}
    row.update({"status": "ready", "summary_json": json.dumps(summary, ensure_ascii=False),
                "changes_json": json.dumps({"changes": found["changes"][:CHANGES_KEPT],
                                            "direct": found["direct"][:CHANGES_KEPT]}, ensure_ascii=False),
                "files_json": json.dumps(names, ensure_ascii=False), "read_at": read_at, "ready_at": _now(),
                "data_version": DATA_VERSION})


def _drop_older(row):
    """同一人、同一连接、同一账套、同一月只留最新一份：比这一份早发起的连文件删（提交以后再删文件）；比它晚发起、
    还在读的那份不动（等它读完再删这一份）；与这一份共用同一件请求、还在读的那份也不动（它读完自己出结果）。"""
    for (name,) in frappe.db.sql(f"""SELECT name FROM `tab{READBACK}` WHERE lookup = %s AND actor = %s AND name != %s
            AND requested_at <= %s LIMIT 50""", (row.lookup, row.actor, row.name, row.requested_at)):
        older = read(name)
        if older.status == "reading" and older.request and older.request == row.request:
            continue
        _remove(older)


def _remove(row):
    if row.status == "reading":
        _drop_request(row)
    frappe.db.delete(READBACK, {"name": row.name})
    name = row.name
    frappe.db.after_commit.add(lambda: storage.drop(name))


def _utc(value) -> str:
    """库里的 UTC 裸时间 → `2026-10-15T02:00:00Z`；空回空串。"""
    return get_datetime(value).strftime("%Y-%m-%dT%H:%M:%SZ") if value else ""


def view(row) -> dict:
    out = {"readback": row.name, "status": row.status, "period": row.period,
           "account_set": row.account_label or row.account_set_key,
           "requested_by": row.actor or "", "requested_at": _utc(row.requested_at)}
    if row.status == "failed":
        out["code"] = row.error_code or "failed"
    if row.status == "ready":
        changes = _json(row.changes_json, {})
        out.update({"summary": _json(row.summary_json, {}), "changes": changes.get("changes") or [],
                    "direct": changes.get("direct") or [], "files": sorted(_json(row.files_json, {}))})
    return out


# ── 取用与删除 ──────────────────────────────────────────────────────────────


def _ready(name):
    row = _visible(name)
    if row.status != "ready":
        _error("cowork.readback.not_ready")
    return row


def file(name, kind) -> tuple:
    """一份工作簿（文件名, 内容）：general_ledger / sales_vat / purchase_vat / changes。"""
    row = _ready(name)
    filename = _json(row.files_json, {}).get(kind) if kind in FILE_KINDS else None
    content = storage.read(row.name, filename) if filename else None
    if content is None:
        _error("cowork.readback.missing", frappe.DoesNotExistError)
    return filename, content


def data(name) -> dict:
    """校验过的读账结果（`contracts.parse` 的形状）另带 ``history``：推送过的单号 → 识别记录。"""
    row = _ready(name)
    stored = storage.read_data(row.name)
    if not stored:
        _error("cowork.readback.missing", frappe.DoesNotExistError)
    return {**contracts.parse(stored["readback"], row.period), "history": stored.get("history") or {}}


def delete(name) -> dict:
    """发起人删掉一份回读（连同四份工作簿与读账结果）。已经放进对话的文件留在对话里。"""
    row = _own(name)
    _remove(row)
    return {"readback": name, "deleted": True}


def vat_records(connection, account_set_key, year, month):
    """税务草稿用：这条连接、这本账套、这个月最近一次完成的回读（谁发起的都算）里的进销项记录；没有回 None。

    ``{"ref", "read_at", "header": {"company", "tax_id", "branch"}, "records": [{"side": "P|S", "rectyp",
    "period": "YYYY-MM", "tax_date", "doc_date", "docnum", "refnum", "name", "prefix", "tax_id", "branch",
    "amount": "0.00", "vat": "0.00", "status", "deleted": false, "history": "<推送过的才有>"}]}``"""
    scope.require("intake")
    connection_access.require_connection(frappe.get_doc(CONNECTION, connection))
    try:
        when = contracts.period(f"{int(year):04d}-{int(month):02d}")
    except (TypeError, ValueError, contracts.ContractError):
        _error("cowork.readback.period_invalid")
    key = normalize_express_account_key(account_set_key)
    name = _newest_ready(connection, key, when) if key else None
    stored = storage.read_data(name) if name else None
    if not stored:
        return None
    row = read(name)
    found = contracts.parse(stored["readback"], when)
    return {"ref": name, "read_at": found["read_at"] or (get_datetime(row.read_at).strftime("%Y-%m-%dT%H:%M:%SZ")
                                                         if row.read_at else ""),
            "header": {"company": found["company"]["name"], "tax_id": found["company"]["tax_id"],
                       "branch": found["company"]["branch"]},
            "records": contracts.vat_records(found, stored.get("history"))}


# ── 月结台账「核对」一段 ──────────────────────────────────────────────────────

MONTH_SCAN_MAX = 500
MONTH_COUNTS = ("pushed", "unchanged", *diff.CHANGES, "direct")


def _fullname(user) -> str:
    try:
        return frappe.utils.get_fullname(user) or user
    except Exception:  # noqa: BLE001 — 显示名取不到就用账号
        return user


def month_status(sets, period):
    """月结台账「核对」一段（hooks ``cowork_company_month_readback``）：给的 Express 账套（``[{connection, key, label}]``）
    这个月最近的回读。没有录入权限回 None；用不了的连接上的账套不列（逐条连接核）。一次查询取这个月这些连接上的回读。

    ``[{"connection", "key", "label", "status": none|reading|ready|failed, "readback", "requested_by",
    "requested_by_name", "requested_at", "ready_at", "counts": {pushed, unchanged, changed, deleted, missing, voided,
    direct} | None, "newer": reading|failed|None}]``：有完成的回读就给完成时间最晚的那份（``newer`` 是最近发起、还没
    完成或失败的那份的状态）；没有就给最近发起的一份；一份都没有是 ``none``。"""
    if not scope.allowed("intake"):
        return None
    try:
        when = contracts.period(period)
    except contracts.ContractError:
        _error("cowork.readback.period_invalid")
    wanted, usable = [], {}
    for item in sets or ():
        connection = str((item or {}).get("connection") or "")
        key = normalize_express_account_key((item or {}).get("key"))
        if not connection or not key:
            continue
        if connection not in usable:
            usable[connection] = _usable(connection)
        if usable[connection]:
            wanted.append((connection, key, str(item.get("label") or "")))
    if not wanted:
        return []
    connections_ = sorted({connection for connection, _key, _label in wanted})
    marks = ", ".join(["%s"] * len(connections_))
    rows = frappe.db.sql(f"""SELECT name, actor, connection, account_set_key, status, summary_json, requested_at,
        ready_at FROM `tab{READBACK}` WHERE period = %s AND connection IN ({marks})
        ORDER BY requested_at DESC, name DESC LIMIT {MONTH_SCAN_MAX}""", (when, *connections_), as_dict=True)
    grouped = {}
    for row in rows:
        grouped.setdefault((row.connection, normalize_express_account_key(row.account_set_key).casefold()), []).append(row)
    out, names = [], {}
    for connection, key, label in wanted:
        found = grouped.get((connection, key.casefold())) or []
        # 完成的那份按完成时间取最新（同 `_newest_ready`、税务草稿取数）：早发起、晚完成的才是草稿用的那份
        ready = max((row for row in found if row.status == "ready"), default=None,
                    key=lambda row: (get_datetime(row.ready_at) if row.ready_at else datetime.min, row.name))
        shown = ready or (found[0] if found else None)
        newer = found[0].status if ready and found[0] is not ready and found[0].status in ("reading", "failed") \
            else None
        item = {"connection": connection, "key": key, "label": label, "status": shown.status if shown else "none",
                "readback": shown.name if shown else None, "requested_by": shown.actor if shown else "",
                "requested_by_name": "", "requested_at": _utc(shown.requested_at) if shown else "",
                "ready_at": _utc(shown.ready_at) if shown and shown.status == "ready" else "",
                "counts": None, "newer": newer}
        if shown:
            if shown.actor not in names:
                names[shown.actor] = _fullname(shown.actor)
            item["requested_by_name"] = names[shown.actor]
        if ready:
            counted = _json(ready.summary_json, {}).get("pushed") or {}
            item["counts"] = {name: int(counted.get(name) or 0) for name in MONTH_COUNTS}
        out.append(item)
    return out


__all__ = ["data", "delete", "file", "month_status", "request", "status", "vat_records", "view"]
