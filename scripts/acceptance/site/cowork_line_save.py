"""验收场景的站点侧脚本（测试环境 testcowork 的网页容器里跑，站点要接着 LINE 测试频道）：一张已识别好的采购票（内容写死、不跑识别）
在 LINE 卡片上点「保存」→ 推送 → CC 桌面版写进验收账套 → 用同一张回复令牌回结果；把推送记录、等结果的键、这期间的 LINE 出站与站点按天
的 LINE 出站计数交回。末行打印 ACC {json}。

    env/bin/python /tmp/acc-cowork_line_save.py <站点> '<JSON 参数>'

参数：user（绑着测试 LINE 号、CC 桌面版上登录的 Cowork 员工）、connection、ledger（验收账套路径，只认它）、run、
seller_tax / seller_name / amount / vat（票面，取自验收样票库）、posting_kind（默认 service）、wait（最多等几秒，默认 120）。

做法（`pearnly_cowork.line.save.reply` 的新口径，`pearnly_core.line_ui.receipt`：loading 开 60 秒、55 秒还没结果才回「录入中 +
【查看结果】」、全程只回复不推送）：
- 票用 `history.storage.create_recognized(source="manual")` 造（同 cowork_intake_post），再记上 LINE 收票那几样（录入去向 express、
  连接、账套），等于 LINE 上识别完出了卡片；识别本身不在这条里（登记 via = "api"：卡片是进程内点的，不是真 LINE 事件）。
- 本进程里 `pearnly_core.line` 的 HTTP 出站全部截住、不发往 LINE（记下来交回：loading 开了几秒、当场回了什么）；回复令牌与 LINE 号都是
  这次现造的假值。结果落库那一刻由**别的进程**（CC 回执请求或后台任务）用同一张假令牌回——LINE 拒收它，站点记一笔 reply_rejected：
  这一单「回了一次、没推送」就由站点按天的出站计数（`line._count`）前后差证明。回执文字本身（单号写在消息里）LINE 拒收后看不到，留真机记录。
- 本进程留到 wait 秒或等结果的键被取走为止：计时线程是本进程的守护线程，进程活着时 55 秒的兜底才可能发生（发生了就截在本进程里）。
防护同 cowork_intake_post：只在 Cowork 测试租户跑、推送开关开着、站点接着 LINE 频道；这条连接上不许有排着队写别的账套的记录；
CC 在线且认得这本；写完核实际写到的路径。
"""
import base64
import json
import secrets
import sys
import time
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]
PUSH, CONNECTION, HISTORY = "Cowork Push Record", "Cowork ERP Connection", "Cowork OCR History"


def norm(text) -> str:
    return str(text or "").strip().replace("/", "\\").rstrip("\\").casefold()


class Captured:
    """本进程里发往 LINE 的请求：记下来，回 200，不出网。"""

    def __init__(self):
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None, **_kwargs):  # noqa: A002 —— 同 requests.post 的参数名
        kind = url.rsplit("/", 1)[-1] if "/v2/bot/" in url else url
        self.calls.append({"at": round(time.monotonic(), 2), "kind": kind, "body": json})
        return type("Response", (), {"status_code": 200, "text": "", "json": lambda self: {}})()


def usage() -> dict:
    """站点今天的 LINE 出站计数（`line._count`：``<日>|<种类>`` → 次数），只取今天这一天。"""
    from pearnly_core import line

    cache = frappe.cache()
    day = str(frappe.utils.now())[:10]
    raw = cache.hgetall(cache.make_key(line.USAGE_HASH)) or {}
    out = {}
    for key, value in raw.items():
        key = key.decode() if isinstance(key, bytes) else str(key)
        if key.startswith(day + "|"):
            out[key.split("|", 1)[1]] = int(value)
    return out


site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
try:
    from pearnly_core import line
    from pearnly_core.execution.replay import TEST_SITES

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")) or frappe.conf.get("pearnly_product") != "cowork":
        raise SystemExit(f"refuse: {site} is not a Cowork test tenant")
    if not frappe.conf.get("pearnly_cowork_erp_push_enabled"):
        raise SystemExit("refuse: push switch is off on this site")
    if not line.can_send():
        raise SystemExit("unmet: this site has no LINE channel (loading and the reply only run on a site wired to a LINE test channel)")
    ledger, user = params["ledger"], params["user"]
    frappe.set_user(user)

    from pearnly_core.execution import state as execution_state
    from pearnly_core.express_rules.targets.identity import normalize_express_account_key
    from pearnly_core.line_ui import receipt
    from pearnly_cowork.companion import pool
    from pearnly_cowork.connections import service as connections
    from pearnly_cowork.history import storage
    from pearnly_cowork.line import save as line_save

    conn = frappe.get_doc(CONNECTION, params["connection"])
    if conn.adapter != "express":
        raise SystemExit(f"refuse: {conn.name} is not an Express connection")
    wanted = normalize_express_account_key(ledger)
    queued = frappe.get_all(PUSH, filters={"connection": conn.name, "status": ["in", ["pending", "leased"]]},
                            fields=["name", "account_key"], limit_page_length=200)
    outside = [row.name for row in queued if normalize_express_account_key(row.account_key) != wanted]
    if outside:
        raise SystemExit(f"refuse: records queued for other books on this connection: {outside[:5]}")
    loaded = connections.read_for_push(conn.name, conn.workspace, conn.client, lock=True)
    rows = (loaded.evidence.snapshot or {}).get("account_sets") or []
    rows = json.loads(rows) if isinstance(rows, str) else rows
    row = next((item for item in rows if isinstance(item, dict) and normalize_express_account_key(item.get("source_id")) == wanted), None)
    frappe.db.rollback()
    if not row:
        raise SystemExit("the acceptance ledger is not in this connection's catalog")
    account_set = row["source_id"]
    state = pool.state(conn.name, account_set, user=user)
    if not (state.get("writers_known") and state.get("online")):
        raise SystemExit(f"CC Desktop is not online for this book: {state}")
    attributes = row.get("attributes") or {}

    today = date.today().isoformat()
    start, end = str(attributes.get("period_start") or ""), str(attributes.get("period_end") or attributes.get("next_end") or "")
    invoice_date = today if (not start or start <= today) and (not end or today <= end) else start
    amount, vat = Decimal(str(params["amount"])), Decimal(str(params["vat"]))
    fields = {"direction": "purchase", "seller_name": params["seller_name"], "seller_tax": params["seller_tax"],
              "buyer_name": attributes.get("company") or attributes.get("name") or "", "buyer_tax": attributes.get("tax_id") or "",
              "subtotal": str(amount), "vat": str(vat), "total_amount": str(amount + vat), "date": invoice_date,
              "document_type": "tax_invoice", "invoice_number": f"ACC-{params['run']}"}
    pages = [{"page_index": 1, "fields": fields}]
    posting_kind = params.get("posting_kind", "service")
    history = storage.create_recognized(conn.workspace, pages, f"acc-{secrets.token_hex(6)}", source="manual", confidence="high",
                                        ai_raw=pages, client=conn.client, posting_kind=posting_kind)
    name = history["name"]
    # 等于 LINE 上识别完出了卡片：录入去向 express、连接与账套都记在票上（同 LINE 收票）
    frappe.db.set_value(HISTORY, name, {"intake_target": "express", "push_connection": conn.name, "push_account_set": account_set},
                        update_modified=False)
    frappe.db.commit()
    result.update(history=name, account_set=account_set)

    token, line_user = "acc" + secrets.token_hex(16), "U" + secrets.token_hex(16)   # 现造的假值：发不到任何人
    wait_key = execution_state.store().make_key("pearnly:line:receipt:" + line_save.key(name))
    captured, before = Captured(), usage()
    began = time.monotonic()
    frappe.local.pearnly_line_batch = {"to": line_user}
    with patch.object(line.requests, "post", captured.post):
        line_save.reply(name, token)
        frappe.db.commit()
        noted = bool(execution_state.store().exists(wait_key))
        deadline = began + int(params.get("wait", 120))
        settled_at = None
        while time.monotonic() < deadline:
            frappe.db.rollback()
            if settled_at is None and not execution_state.store().exists(wait_key):
                settled_at = round(time.monotonic() - began, 1)
            status = frappe.db.get_value(HISTORY, name, "last_push_status")
            if settled_at is not None and status in ("success", "failed"):
                break
            time.sleep(2)
        time.sleep(int(params.get("settle", 8)))   # 结果那一回在别的进程里：给它把出站计数记上
    frappe.local.pearnly_line_batch = None
    after = usage()
    push = frappe.db.get_value(HISTORY, name, "last_push_record")
    record = {}
    if push:
        row = frappe.db.get_value(PUSH, push, ["status", "external_docnum", "reason", "verify_state", "response_json"], as_dict=True) or {}
        meta = (json.loads(row.get("response_json") or "{}") or {}).get("meta") or {}
        record = {"name": push, "status": row.get("status"), "docnum": row.get("external_docnum"), "reason": row.get("reason"),
                  "verify": row.get("verify_state"), "written_to": meta.get("account_dir"), "written": bool(meta.get("written"))}
    delta = {key: after.get(key, 0) - before.get(key, 0) for key in set(before) | set(after) if after.get(key, 0) != before.get(key, 0)}
    result.update(ok=True, record=record, noted=noted, settled_after=settled_at,
                  wait_seconds=receipt.WAIT_SECONDS, loading_seconds=receipt.LOADING_SECONDS,
                  here=[{"kind": call["kind"], "seconds": (call["body"] or {}).get("loadingSeconds"),
                         "texts": [m.get("text") for m in (call["body"] or {}).get("messages") or []]} for call in captured.calls],
                  usage_delta=delta, pushes=frappe.db.count(PUSH, {"history": name}),
                  expected={"amount": str(amount), "vat": str(vat), "total": str(amount + vat)})
except SystemExit as stop:
    if str(stop) not in ("0", "None", ""):
        result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
    frappe.destroy()
