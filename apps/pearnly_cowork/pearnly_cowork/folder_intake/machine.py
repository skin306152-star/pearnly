"""小助手的文件夹录入机器接口（配对码身份，四个精确 POST 方法，登记在 `companion.http_auth.FOLDER_METHODS`）。

本模块只放这四个机器方法（机器鉴权按路径前缀认请求，网页用的接口在 `api.py`）。每个请求只做一件小事：
鉴权后先提交、放掉鉴权时锁住的连接行（不与同一台小助手的心跳 / 领任务 / 回执争锁），重活（核对原件、建原件、
受理识别、推送）全部交给后台的 `pump`，以规则负责人的身份做。业务上的「不行」一律回 200 + ``{ok: false, code}``，
只有身份 / 总闸问题才是 401 / 403，小助手据此区分「连接坏了」和「这份文件不收」。
"""
import base64
import hashlib
import time
from contextlib import contextmanager
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import get_datetime, get_system_timezone, now_datetime

from pearnly_cowork.access import scope
from pearnly_cowork.companion import http_auth
from pearnly_cowork.connections import access as connection_access
from pearnly_cowork.folder_intake import agent, contracts, inbox, settings, workbook
from pearnly_cowork.folder_intake.access import FILE, RULE, RUN, read, write

CONNECTION = "Cowork ERP Connection"
# 文件夹录入协议版本：小助手据此判断服务端认不认这些接口（旧服务端回 401，小助手照旧空转）。
PROTOCOL = 1


def _epoch(value):
    if not value:
        return None
    return get_datetime(value).replace(tzinfo=ZoneInfo(get_system_timezone())).timestamp()


def _released(operation):
    """取本次机器请求的身份与报文，随即提交：放掉鉴权阶段锁住的连接行。"""
    context, body = http_auth.current_request(operation)
    frappe.local.cowork_private_response = True
    frappe.db.commit()
    return context, body


def _fail(code):
    http_auth.clear_private_messages()
    return {"ok": False, "code": code}


@contextmanager
def _as(user):
    """以规则负责人的身份做一件事，做完还原成配对签发人（机器请求的会话已与浏览器脱钩，见 `http_auth`）。
    语言也换成负责人的网页语言：结果表格与文件名按这个人的语言出（用户 2026-10-08）。"""
    from frappe.translate import get_user_lang

    previous, previous_lang = frappe.session.user, getattr(frappe.local, "lang", None)
    frappe.set_user(user)
    frappe.local.lang = get_user_lang(user)
    try:
        yield
    finally:
        frappe.set_user(previous)
        frappe.local.lang = previous_lang


def _rule(name, context):
    if not isinstance(name, str) or not name or len(name) > 140 or not frappe.db.exists(RULE, name):
        raise contracts.ContractError("cowork.folder.rule_missing")
    rule = read(RULE, name)
    # 认的是「读文件夹的小助手」（`reader`）：MR.ERP 任务可以由同一工作空间里任一台小助手读（2026-10-09）。
    if (rule.reader or rule.connection) != context.connection:
        raise contracts.ContractError("cowork.folder.rule_missing")
    return rule


def owner_ready(rule):
    """负责人此刻仍能用这条连接录入（与网页同一判据）；不行就不建原件、不扣费、不推送。"""
    try:
        if not scope.allowed("intake", rule.rule_owner):
            return False
        connection_access.require_connection(frappe.get_doc(CONNECTION, rule.connection), actor=rule.rule_owner)
        return True
    except (frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError):
        return False


def _guarded(operation, handler):
    context, body = _released(operation)
    try:
        result = handler(context, body)
        frappe.db.commit()
        http_auth.clear_private_messages()
        return {"ok": True, "protocol": PROTOCOL, **result}
    except contracts.ContractError as error:
        frappe.db.rollback()
        return _fail(error.code)
    except frappe.PermissionError:
        # 负责人此刻没有录入权限 / 连接使用授权：这份文件不收，配对本身照常（不回 403，免得小助手当成掉线）。
        frappe.db.rollback()
        return _fail("cowork.folder.owner_not_ready")
    except (frappe.ValidationError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        return _fail(str(error)[:200] or "cowork.folder.invalid")


# ── 同步：上报本机情况与运行记录，取规则、立即运行请求与在途文件的状态 ──────────────────


def _sync(context, body):
    # 这条连接是哪种 ERP：MR.ERP 的小助手成员只读文件夹（小助手配对时先问一次 `probe`，见 companion/pairing.py）。
    connection = frappe.db.get_value(CONNECTION, context.connection, ["adapter", "label"], as_dict=True) or {}
    identity = {"adapter": connection.get("adapter") or "", "label": connection.get("label") or ""}
    if body.get("probe") is True:
        # 只问身份：不记本机情况、不取定时任务（配对时还没有授权文件夹，不能把已有的清掉）。
        return {"server_time": time.time(), "rules": [], "entries": {}, **identity}
    roots = contracts.roots(body.get("roots"))
    agent.remember(context.connection, roots=roots, version=body.get("version"),
                   health=contracts.health(body.get("rule_health")), device=contracts.device(body.get("device")))
    for row in (body.get("runs") or [])[:50]:
        try:
            _record_run(context, row)
        except contracts.ContractError:
            # 规则已不在 / 记录不合形状：这一条不收，其余照收（不让一条坏记录堵住小助手每一次同步）。
            continue
    rules = []
    for name in frappe.db.sql_list(f"""SELECT name FROM `tab{RULE}` WHERE enabled = 1
            AND IFNULL(NULLIF(reader, ''), connection) = %s ORDER BY creation""", (context.connection,)):
        rule = read(RULE, name)
        rules.append({"name": rule.name, "revision": int(rule.rule_revision or 0), "title": rule.title,
                      "root_id": rule.root_id, "folder": rule.folder or "", "run_day": int(rule.run_day or 0),
                      "run_time": rule.run_time, "active_since": _epoch(rule.active_since),
                      "run_request": int(rule.run_request or 0), "ready": owner_ready(rule)})
    entries = {}
    for name in contracts.watch(body.get("watch")):
        if frappe.db.exists(FILE, name):
            entry = read(FILE, name)
            if entry.connection == context.connection:
                entries[name] = workbook.status(entry)
    return {"server_time": time.time(), "rules": rules, "entries": entries, **identity}


def _record_run(context, row):
    if not isinstance(row, dict):
        raise contracts.ContractError("cowork.folder.invalid")
    rule = _rule(row.get("rule"), context)
    key = hashlib.sha256(f"{rule.name}\0{contracts.run_key(row.get('run_key'))}".encode()).hexdigest()
    doc = read(RUN, key, for_update=True) if frappe.db.exists(RUN, key) else frappe.new_doc(RUN)
    if doc.is_new():
        trigger = row.get("trigger") if row.get("trigger") in ("schedule", "manual", "catch_up") else "schedule"
        doc.update({"input_key": key, "rule": rule.name, "connection": context.connection, "trigger": trigger,
                    "started_at": now_datetime()})
    counts = {field: row.get(field) for field in ("found", "new_files", "skipped", "upload_failed")}
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise contracts.ContractError("cowork.folder.invalid")
    doc.update(counts)
    if row.get("scanned"):
        doc.scanned_at = doc.scanned_at or now_datetime()
    note = row.get("note")
    doc.note = contracts.text(note, maximum=500, optional=True) if note else doc.note
    write(doc)
    return doc.name


@frappe.whitelist(methods=["POST"])
def sync(**ignored):
    return _guarded("folder_sync", _sync)


# ── 登记一份文件：给出上传方式（签名直传原件桶，或经本接口分块） ─────────────────────────


def _begin(context, body):
    values = settings.config()
    rule = _rule(body.get("rule"), context)
    if not rule.enabled:
        return {"skip": "cowork.folder.rule_disabled"}
    relpath = contracts.relpath(body.get("relpath"))
    filename = contracts.filename(body.get("filename"))
    digest = contracts.sha256(body.get("sha256"))
    size = contracts.byte_size(body.get("byte_size"), 100 * 1024 * 1024)
    run = None
    if body.get("run_key"):
        run = hashlib.sha256(f"{rule.name}\0{contracts.run_key(body['run_key'])}".encode()).hexdigest()
    key = contracts.entry_key(rule.name, relpath, digest)
    if frappe.db.exists(FILE, key):
        entry = read(FILE, key, for_update=True)
        if entry.state != "registered":
            return {"entry": entry.name, "state": entry.state}
    else:
        if not owner_ready(rule):
            return {"skip": "cowork.folder.owner_not_ready"}
        entry = frappe.new_doc(FILE)
        # 文件记在读它的那台小助手名下（`reader`）：之后上传完成、取表格都由它来，按它核身份。
        entry.update({"input_key": key, "rule": rule.name, "connection": context.connection, "run": run,
                      "relpath": relpath, "filename": filename, "byte_size": size, "sha256": digest,
                      "actor": rule.rule_owner, "state": "registered", "registered_at": now_datetime()})
        write(entry)
    target = _upload_target(rule, entry)
    if target.get("direct"):
        entry.route = "direct"
        write(entry)
        if target.get("done"):
            return {"entry": entry.name, "state": "registered", "route": "direct", "uploaded": True}
        return {"entry": entry.name, "state": "registered", "route": "direct",
                "url": target["url"], "headers": target.get("headers") or {}}
    inline_max = values["inline_max_mib"] * 1024 * 1024
    if size > inline_max:
        entry.state, entry.last_code, entry.finished_at = "failed", "cowork.folder.too_large", now_datetime()
        write(entry)
        return {"entry": entry.name, "state": "failed", "code": entry.last_code}
    entry.route = "inline"
    write(entry)
    return {"entry": entry.name, "state": "registered", "route": "inline", "max_bytes": inline_max}


def request_key(entry_name):
    return "folder:" + entry_name


def _upload_target(rule, entry):
    from pearnly_cowork.files import service as originals

    point = "cowork_folder_target"
    frappe.db.savepoint(point)
    with _as(entry.actor):
        try:
            return originals.begin_direct(rule.workspace, entry.filename, int(entry.byte_size),
                                          request_key(entry.name), rule.client or None)
        except frappe.ValidationError:
            # 直传条件不满足（类型 / 大小 / 原件桶未开）：改走经本接口上传，由受理时按原规则再核一遍。
            frappe.db.rollback(save_point=point)
            frappe.clear_messages()
            return {"direct": 0}


@frappe.whitelist(methods=["POST"])
def begin(**ignored):
    return _guarded("folder_begin", _begin)


# ── 上传完成：直传的只记一笔；经本接口的原件先落在站点私有目录，等受理时建原件 ────────────────


def _finish(context, body):
    name = contracts.sha256(body.get("entry"))
    if not frappe.db.exists(FILE, name):
        raise contracts.ContractError("cowork.folder.entry_missing")
    entry = read(FILE, name, for_update=True)
    if entry.connection != context.connection:
        raise contracts.ContractError("cowork.folder.entry_missing")
    if entry.state != "registered":
        return {"entry": entry.name, "state": entry.state}
    if entry.route == "inline":
        encoded = body.get("content")
        if not isinstance(encoded, str) or len(encoded) > (settings.config()["inline_max_mib"] * 1024 * 1024 * 4) // 3 + 8:
            raise contracts.ContractError("cowork.folder.size_invalid")
        try:
            content = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise contracts.ContractError("cowork.folder.invalid") from error
        if len(content) != int(entry.byte_size) or hashlib.sha256(content).hexdigest() != entry.sha256:
            raise contracts.ContractError("cowork.folder.content_mismatch")
        inbox.put(entry.name, content)
    elif entry.route != "direct":
        raise contracts.ContractError("cowork.folder.invalid")
    entry.state = "uploaded"
    write(entry)
    from pearnly_cowork.folder_intake import pump

    pump.kick()
    return {"entry": entry.name, "state": entry.state}


@frappe.whitelist(methods=["POST"])
def finish(**ignored):
    return _guarded("folder_finish", _finish)


# ── 结果表格：一张发票一份，与网页导出的复核表同一模板 ───────────────────────────────


def _workbook(context, body):
    name = contracts.sha256(body.get("entry"))
    if not frappe.db.exists(FILE, name):
        raise contracts.ContractError("cowork.folder.entry_missing")
    entry = read(FILE, name)
    if entry.connection != context.connection:
        raise contracts.ContractError("cowork.folder.entry_missing")
    history = body.get("history")
    with _as(entry.actor):
        filename, content, revision = workbook.render(entry, history)
    folder = contracts.result_folder(frappe.db.get_value(RULE, entry.rule, "result_folder"))
    return {"entry": entry.name, "history": history, "filename": filename, "revision": revision,
            "result_folder": folder, "content": base64.b64encode(content).decode("ascii")}


@frappe.whitelist(methods=["POST"])
def workbook_file(**ignored):
    return _guarded("folder_workbook", _workbook)


__all__ = ["begin", "finish", "owner_ready", "request_key", "sync", "workbook_file"]
