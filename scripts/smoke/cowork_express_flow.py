"""Cowork 推送写 Express 冒烟（在实验栈 Cowork 站点网页容器里跑）：造一张不花识别费的识别记录 → 走正式的提交与确认发送
→ 测试机 CC 写进 C 盘 T 账套 → 等回执与 CC 自己的读回核对。形状同 ERP 版 `express_flow.py`。

    env/bin/python /tmp/smoke-cowork_express_flow.py <站点> '<JSON 参数>'   # 末行打印 SMOKE {"ok": ..., ...}

参数（登记表入口的 args）：user = 测试机 CC 上登录过的 Cowork 录入人；connection = Cowork ERP Connection 名；
account_set = 目标账套路径（必须在 C:\\PearnlyExpress\\ 下、最后一段以 T 开头）；seller_tax / seller_name = 这份账套里已有选择记忆的
供应商；posting_kind（默认 service）；amount = 税前金额（默认 100，VAT 7%）；date（默认取账套期间内的今天或期间起始日）；
wait = 最多等几秒（默认 240）。

防护（任何一条不满足就不建单，报「没测到」或失败原因）：
- 只认测试租户站点（`execution.replay.TEST_SITES`）且本站是 Cowork、推送开关开着；
- 只写 C:\\PearnlyExpress\\ 下以 T 开头的账套：参数先核，提交后再核冻结载荷里的账套，不对就撤回准备；成功后核 CC 回报的实际路径；
- 这条连接上排着的单只要有一张不在 C 盘就拒跑（实验栈从生产导入，可能带进指向 S 盘的待推单）；
- CC 不在线（DNS 没切到实验栈）不建单；
- 识别记录用 `source=manual`（不发 LINE、不调模型、不扣费），不设推送去向（自动推送扫描不会拿它）。
不碰 S 盘 / accserver。只给测试租户用。
"""
import base64
import json
import secrets
import sys
import time
from datetime import date
from decimal import Decimal

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]  # run.py 传参用 base64

PUSH = "Cowork Push Record"
CONNECTION = "Cowork ERP Connection"
ROOT = "c:\\pearnlyexpress\\"


def c_drive_t_book(key) -> bool:
    text = str(key or "").strip().replace("/", "\\").casefold()
    return text.startswith(ROOT) and text.rstrip("\\").rsplit("\\", 1)[-1].startswith("t")


site = sys.argv[1]
params = json.loads(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else {}
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
try:
    from pearnly_core.execution.replay import TEST_SITES

    if not TEST_SITES.fullmatch(site) or frappe.conf.get("pearnly_product") != "cowork":
        raise SystemExit(f"refuse: {site} is not a Cowork test tenant")
    if not frappe.conf.get("pearnly_cowork_erp_push_enabled"):
        raise SystemExit("refuse: push switch is off on this site")
    for key in ("user", "connection", "account_set", "seller_tax", "seller_name"):
        if not params.get(key):
            raise SystemExit(f"missing arg: {key}")
    account_set = params["account_set"]
    if not c_drive_t_book(account_set):
        raise SystemExit(f"refuse: {account_set} is not a T book under C:\\PearnlyExpress")
    user = params["user"]
    frappe.set_user(user)

    from pearnly_cowork.companion import pool
    from pearnly_cowork.connections import service as connections

    conn = frappe.get_doc(CONNECTION, params["connection"])
    if conn.adapter != "express":
        raise SystemExit(f"refuse: {conn.name} is not an Express connection")
    queued = frappe.get_all(PUSH, filters={"connection": conn.name, "status": ["in", ["pending", "leased"]]},
                            fields=["name", "account_key"], limit_page_length=200)
    outside = [row.name for row in queued if not c_drive_t_book(row.account_key)]
    if outside:
        raise SystemExit(f"refuse: records queued outside C drive on this connection: {outside[:5]}")
    state = pool.state(conn.name, account_set, user=user)
    if not (state.get("writers_known") and state.get("online")):
        result.update(skipped=True, why=f"CC not online for this book: {state}")
        raise SystemExit(0)
    from pearnly_core.express_rules.targets.identity import normalize_express_account_key

    wanted = normalize_express_account_key(account_set)
    ahead = [row.name for row in queued if normalize_express_account_key(row.account_key) == wanted]

    loaded = connections.read_for_push(conn.name, conn.workspace, conn.client, lock=True)
    rows = (loaded.evidence.snapshot or {}).get("account_sets") or []
    rows = json.loads(rows) if isinstance(rows, str) else rows
    row = next((item for item in rows if isinstance(item, dict)
                and normalize_express_account_key(item.get("source_id")) == wanted), None)
    if not row:
        raise SystemExit(f"book not in the connection catalog: {account_set}")
    attributes = row.get("attributes") or {}
    frappe.db.rollback()  # 放掉连接行锁（只读了目录）

    today = date.today().isoformat()
    start, end = str(attributes.get("period_start") or ""), str(attributes.get("period_end") or attributes.get("next_end") or "")
    invoice_date = params.get("date") or (today if (not start or start <= today) and (not end or today <= end) else start)
    amount = Decimal(str(params.get("amount", 100)))
    vat = (amount * Decimal("0.07")).quantize(Decimal("0.01"))
    stamp = f"{int(time.time())}"
    fields = {"direction": "purchase", "seller_name": params["seller_name"], "seller_tax": params["seller_tax"],
              "buyer_name": attributes.get("company") or attributes.get("name") or "", "buyer_tax": attributes.get("tax_id") or "",
              "subtotal": str(amount), "vat": str(vat), "total_amount": str(amount + vat), "date": invoice_date,
              "document_type": params.get("document_type", "tax_invoice"), "invoice_number": f"SMOKE-{stamp}"}
    pages = [{"page_index": 1, "fields": fields}]
    posting_kind = params.get("posting_kind", "service")
    from pearnly_cowork.history import storage

    history = storage.create_recognized(conn.workspace, pages, f"smoke-{secrets.token_hex(6)}", source="manual",
                                        confidence="high", ai_raw=pages, client=conn.client, posting_kind=posting_kind)
    frappe.db.commit()
    history_name = history["name"]
    result["history"] = history_name

    from pearnly_cowork.push.application import api as application
    from pearnly_cowork.push.application import service as application_service

    view = application_service.choices_view(history_name, conn.name, account_set, posting_kind)
    frappe.db.rollback()
    if (view or {}).get("needed"):
        raise SystemExit(f"choices needed in this book: {[item.get('kind') for item in view['needed']]}")
    version = history["record_version"]
    revision = frappe.db.get_value(CONNECTION, conn.name, "record_revision")
    request_key = f"smoke-{secrets.token_hex(8)}"
    prepared = application.submit(history=history_name, connection=conn.name, request_key=request_key,
                                  expected_history_version=version, expected_connection_revision=revision,
                                  account_set_key=account_set, posting_kind=posting_kind)
    frappe.db.commit()
    result["prepared"] = {key: prepared.get(key) for key in ("name", "status", "reason")}
    if prepared.get("status") != "prepared":
        raise SystemExit(f"not prepared: {prepared.get('reason') or prepared.get('status')}")
    payload = json.loads(frappe.db.get_value(PUSH, prepared["name"], "payload_json") or "{}")
    # 冻结载荷里账套是文件夹名、根目录另放（account_root），拼起来才是完整路径
    folder = str(payload.get("account_set") or "")
    frozen = folder if ":" in folder else "\\".join(part.strip("\\") for part in (payload.get("account_root") or "", folder) if part)
    if not c_drive_t_book(frozen):
        application.cancel_prepared(prepared["name"], prepared.get("revision"))
        frappe.db.commit()
        raise SystemExit(f"refuse: frozen book is not on C drive: {frozen}")

    from pearnly_cowork.companion import submission

    confirmed = submission.confirm(prepared["name"], prepared.get("revision"), "confirm:" + request_key)
    frappe.db.commit()
    if (confirmed or {}).get("status") != "pending":
        raise SystemExit(f"not queued: {confirmed}")

    deadline = time.monotonic() + int(params.get("wait", 240))
    record = {}
    while time.monotonic() < deadline:
        frappe.db.rollback()  # 读别的进程（CC 回执）提交的新状态
        record = frappe.db.get_value(PUSH, prepared["name"], ["status", "external_docnum", "reason", "result_unknown",
                                                             "verify_state", "response_json"], as_dict=True) or {}
        if record.get("status") in ("success", "manual", "cancelled"):
            break
        time.sleep(5)
    response = json.loads(record.get("response_json") or "{}") if record.get("response_json") else {}
    meta = response.get("meta") or {}
    result.update(record=prepared["name"], status=record.get("status"), docnum=record.get("external_docnum"),
                  reason=record.get("reason"), verify=record.get("verify_state"), written_to=meta.get("account_dir"),
                  queued_ahead=ahead)
    result["summary"] = (f"{fields['invoice_number']} → {record.get('external_docnum') or record.get('status')}"
                         f"（CC 读回 {record.get('verify_state') or '-'}）")
    result["ok"] = (record.get("status") == "success" and bool(record.get("external_docnum"))
                    and record.get("verify_state") == "ok" and bool(meta.get("written")) and bool(meta.get("index_requery"))
                    and not meta.get("duplicate_skipped") and c_drive_t_book(meta.get("account_dir")))
except SystemExit as stop:
    if str(stop) not in ("0", "None"):
        result["why"] = str(stop)
finally:
    frappe.destroy()
print("SMOKE " + json.dumps(result, ensure_ascii=False, default=str))
