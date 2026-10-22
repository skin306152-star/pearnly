"""网关的流程：ERP 单据 → 识别记录 → 现有推送；按 ERP 单据查推送结果；按公司取 Express 整套主档。

调用方（`auth.run`）已切成网关服务账号。和文件夹自动推送（`folder_intake.push`）同一条路：先看还要不要人选
（``choices_view``），都定了再「准备」并「确认」；推送规则、账本、派发、回读核对一行不改。

- 公司按税号认（`companies.service.with_tax_id`，只认 Express 的主档），推往这家公司在 Express 主连接上覆盖单据日期的
  那份账套（`companies.tasks.books_of`：同期几份时按这家选过的现用账套，没选过时写不进的不算）。取不到 / 多于一份就停在
  事务所待处理里，原因记在票上，ERP 显示「停在事务所」。
- 由哪台电脑写：ERP 的人没有自己的电脑，单由够得到这份账套、能写的那台办公室电脑写（推送记录上冻结 ``route_device``，
  同定时任务；上次写它的那台优先）。那台关着也照样排队，开机后写；一台都没有就停下。
- 主档：按需请求 ``express_masters``（账套的公共资料，够得到的电脑谁先来谁读），读好的结果原样交给 ERP 一次后删掉。
"""
import gzip
import json

import frappe

from pearnly_cowork.companies import service as companies
from pearnly_cowork.companies import tasks as company_tasks
from pearnly_cowork.companion import request_service
from pearnly_cowork.connections import pool as account_pool
from pearnly_cowork.connections import service as connections
from pearnly_cowork.erp_gateway import contracts
from pearnly_cowork.erp_gateway.contracts import GatewayError
from pearnly_cowork.history import push_state, storage
from pearnly_cowork.push.application import service as application
from pearnly_cowork.targets.identity import normalize_express_account_key

HISTORY = "Cowork OCR History"
RECORD = "Cowork Push Record"
CONNECTION = "Cowork ERP Connection"
COMPANY = "Cowork Company"
WORKSPACE = "Cowork Workspace"
MASTERS_KIND = "express_masters"
REFS_MAX = 100

NO_COMPANY = "erp_gateway.company_unknown"
NO_CONNECTION = "erp_gateway.express_not_connected"
NO_BOOK = "erp_gateway.account_set_missing"
SEVERAL_BOOKS = "erp_gateway.account_set_several"
NO_WRITER = "push.no_writer"


# ── 公司、连接、账套 ──────────────────────────────────────────────────────────


def _company(company):
    """ERP 的公司（税号 + 分店）→ (工作空间, Cowork 公司主档, Express 主连接)。"""
    names = [name for name in companies.with_tax_id(company["tax_id"])
             if frappe.db.get_value(COMPANY, name, "adapter") == "express"]
    if not names:
        raise GatewayError(NO_COMPANY)
    name = names[0]
    workspace = frappe.db.get_value(COMPANY, name, "workspace")
    connection = connections.main_connections(workspace).get("express")
    if not connection:
        raise GatewayError(NO_CONNECTION)
    return workspace, name, connection


def _books(company_name, connection, day, branch):
    books = company_tasks.books_of(company_name, connection, day, branch) or []
    return [book for book in books if book.get("key")]


def _writer(connection, key):
    """替 ERP 的单写这份账套的电脑：上次写它的那台（还够得到、能写）优先，否则够得到它、能写的第一台；一台都没有回空串。"""
    devices = account_pool.reachers(connection, key, writable=True)
    if not devices:
        return ""
    last = getattr(account_pool.registrations_of(connection, [key]).get(normalize_express_account_key(key)),
                   "last_writer_device", "")
    return last if last in devices else devices[0]


def company(client, body):
    """ERP 这家公司在事务所里对应哪份账套（给 ERP 设置页与主档同步看）。"""
    del client
    target = _target(body)
    return {"company": target["company"], "account_set": target["key"], "label": target["label"]}


def _target(body):
    if not isinstance(body, dict) or not isinstance(body.get("company"), dict):
        raise GatewayError("erp_gateway.company_invalid")
    tax = "".join(ch for ch in str(body["company"].get("tax_id") or "") if ch.isdigit())
    if len(tax) != 13:
        raise GatewayError("erp_gateway.company_tax_missing")
    branch = contracts._branch(body["company"].get("branch"))
    day = contracts._date(body.get("date") or frappe.utils.today())
    workspace, name, connection = _company({"tax_id": tax})
    books = _books(name, connection, day, branch)
    if not books:
        raise GatewayError(NO_BOOK)
    if len(books) > 1:
        raise GatewayError(SEVERAL_BOOKS)
    return {"workspace": workspace, "company": name, "connection": connection, "key": books[0]["key"],
            "label": books[0].get("label") or books[0]["key"]}


# ── 交单 ──────────────────────────────────────────────────────────────────────


def submit(client, body):
    doc = contracts.document(body)
    workspace, name, connection = _company(doc["company"])
    request_key = contracts.request_key(client, doc)
    created = storage.create_gateway(workspace, contracts.pages(doc), request_key)
    history = created["name"]
    if created.get("last_push_record") or created.get("last_push_reason"):
        return {"ref": doc["ref"], **_outcome(history)}  # 交过了：照现在的结果回，不再推一次
    books = _books(name, connection, doc["date"], doc["company"]["branch"])
    if len(books) != 1:
        return _hold(doc, history, NO_BOOK if not books else SEVERAL_BOOKS, connection=connection)
    key = books[0]["key"]
    going = {"connection": connection, "account_set": key}
    frappe.db.commit()  # 票先落下；choices_view 只看不记，结束时整笔回滚
    view = application.choices_view(history, connection, key, None, fixed={})
    if view.get("wait"):
        return _hold(doc, history, view["wait"].get("message") or "erp_gateway.masters_reading", **going)
    if view.get("needed"):
        kinds = ",".join(dict.fromkeys(item["kind"] for item in view["needed"]))
        return _hold(doc, history, "choice_needed:" + kinds, **going)
    device = _writer(connection, key)
    if not device:
        return _hold(doc, history, NO_WRITER, **going)
    version = frappe.db.get_value(HISTORY, history, "record_version")
    revision = frappe.db.get_value(CONNECTION, connection, "record_revision")
    result = application.submit(history, connection, request_key, version, revision, key, None, None, None,
                                fixed={}, route_device=device)
    frappe.db.commit()
    if result.get("status") == "prepared":
        try:
            application.confirm(result["name"], result["revision"], "confirm:" + request_key)
            frappe.db.commit()
        except (frappe.ValidationError, frappe.PermissionError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            application.submission_failed(history, str(error))
            frappe.db.commit()
    return {"ref": doc["ref"], **_outcome(history)}


def _hold(doc, history, reason, **going):
    push_state.record_failure(history, reason, **going)
    frappe.db.commit()
    return {"ref": doc["ref"], **_outcome(history)}


# ── 结果 ──────────────────────────────────────────────────────────────────────


def _outcome(history):
    row = frappe.db.get_value(HISTORY, history, ["last_push_status", "last_push_reason", "last_push_record",
                                                 "push_account_set"], as_dict=True) or {}
    record = {}
    if row.get("last_push_record"):
        record = frappe.db.get_value(RECORD, row["last_push_record"], ["status", "external_docnum", "verify_state",
                                                                         "reason", "account_key"], as_dict=True) or {}
    reason = str(row.get("last_push_reason") or record.get("reason") or "")
    state = contracts.state_of(str(row.get("last_push_status") or ""), reason, str(record.get("status") or ""),
                               str(record.get("external_docnum") or ""), str(record.get("verify_state") or ""))
    return {"state": state, "docnum": str(record.get("external_docnum") or ""),
            "reason": "" if state in (contracts.WRITTEN, contracts.QUEUED) else reason[:300],
            "account_set": str(record.get("account_key") or row.get("push_account_set") or ""), "history": history}


def status(client, body):
    """按 ERP 单据查结果：``{"documents": [{"ref", "direction"}]}`` → ``{"documents": [{ref, state, docnum, reason,
    account_set}]}``；没交过的不在结果里。"""
    wanted = body.get("documents") if isinstance(body, dict) else None
    if not isinstance(wanted, list) or len(wanted) > REFS_MAX:
        raise GatewayError("erp_gateway.document_invalid")
    workspaces = frappe.get_all(WORKSPACE, pluck="name")
    found = []
    for item in wanted:
        if not isinstance(item, dict) or item.get("direction") not in contracts.DIRECTIONS:
            raise GatewayError("erp_gateway.document_invalid")
        ref = contracts._text(item.get("ref"), contracts.REF_MAX, required=True)
        key = contracts.request_key(client, {"ref": ref, "direction": item["direction"]})
        for workspace in workspaces:
            history = storage.gateway_name(workspace, key)
            if frappe.db.exists(HISTORY, history):
                storage.read(history)  # 网关账号仍要有这张票的读取范围
                found.append({"ref": ref, **_outcome(history)})
                break
    return {"documents": found}


# ── 主档 ──────────────────────────────────────────────────────────────────────


def _account(connection, key):
    catalog = connections.targets(connection)
    wanted = normalize_express_account_key(key)
    for row in (catalog or {}).get("account_sets") or []:
        if normalize_express_account_key(row.get("source_id")) == wanted:
            attributes = row.get("attributes") or {}
            return {"account_set": str(attributes.get("path") or row.get("source_id") or ""),
                    "account_root": str(attributes.get("root") or "")}
    raise GatewayError(NO_BOOK)


def masters(client, body):
    """这家公司现用账套的 Express 整套主档与各仓结存。读好了回 ``{"state": "ready", "data": {...}}`` 并删掉这件请求；
    还在读回 ``reading``；电脑离线 / CC 桌面版太旧回 ``offline`` / ``outdated``（ERP 过一会儿再问）。"""
    del client
    target = _target(body)
    connection, key = target["connection"], target["key"]
    request_key = "erp_masters:" + connection + ":" + normalize_express_account_key(key)
    head = {"company": target["company"], "account_set": key, "label": target["label"]}
    made = request_service.find(MASTERS_KIND, connection, request_key)
    if made is None:
        waiting = request_service.waiting(connection, MASTERS_KIND, account_key=key)
        if waiting:  # 够得到这份账套的电脑都不在线，或 CC 桌面版太旧不认这种请求：不建，ERP 过一会儿再问
            return {**head, "state": "outdated" if waiting.get("reason") == "update_needed" else "offline"}
        made = request_service.create(MASTERS_KIND, connection, _account(connection, key), request_key,
                                      account_key=key)
    if made["status"] != "done":
        return {**head, "state": "reading"}
    content, _meta = request_service.result(made["request"])
    request_service.drop(made["request"])
    try:
        data = json.loads(gzip.decompress(content))
    except (OSError, ValueError):
        raise GatewayError("erp_gateway.masters_invalid") from None
    return {**head, "state": "ready", "data": data}
