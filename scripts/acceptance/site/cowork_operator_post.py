"""验收场景的站点侧脚本（在测试租户 / 实验栈的 Cowork 网页容器里跑）：一张已识别好的采购票（内容写死，不跑识别、不付费）**交给代录**
（不直写）→ CC 桌面版在那台电脑上打开 Express 界面录入 → 回执 → 把推送记录、代录任务、落点、期望值交回。末行打印 ACC {json}。

    env/bin/python /tmp/acc-cowork_operator_post.py <站点> '<JSON 参数>'

参数：user（那台电脑的 CC 桌面版上登录的 Cowork 员工；代录用他存在电脑上的 Express 登录）、connection（Express 连接）、
ledger（验收账套路径，只认它）、run、seller_tax / seller_name / amount / vat / lines（票面与明细行，取自验收样票库）、posting_kind（默认 service）、
wait（最多等几秒，默认 900：界面录入比直写慢）。

走的是代录背后的同一组服务端函数：票用 `history.storage.create_recognized(source="manual")` 造（同 cowork_intake_post），
记上对话账套 → 这本账套的 Cowork 公司（没有就按目录建，同「从连接添加」）→ 建代录任务（`express_operator.tasks.create`）→
交单开始（`express_operator.api.start`，写入计划「开始」走的就是它；Cowork 这一侧是 `assistant.operator_source.submit` → 推送同一条路、
载荷带代录标记）。登记里标 via = "api"：对话、开始前卡片、中途弹框带回对话不在这条里。
防护同 cowork_intake_post：只在 Cowork 测试租户跑；这条连接上不许有排着队、要写别的账套的记录；CC 要在线且认得这本；
写完按推送记录核实际写到的路径。
"""
import base64
import json
import secrets
import sys
import time
from datetime import date
from decimal import Decimal

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]
PUSH, CONNECTION, HISTORY = "Cowork Push Record", "Cowork ERP Connection", "Cowork OCR History"


def norm(text) -> str:
    return str(text or "").strip().replace("/", "\\").rstrip("\\").casefold()


def answer_card(company, name, period, account_set, detail="", card_wait=240, account="") -> dict:
    """像用户在引导卡上那样把这张票的卡一次答完（新建的站点 / 账套还没记过付款方式、科目、往来方时，产品先问卡，不答不写——
    11-24 首跑在刚建的 Mac 栈上两条 Cowork 场景就停在这里）：明细按 ``detail``；有预选的照预选；其余取第一个不是「新建」的选项。
    回 {asked, kinds, answers, saved}；卡上什么都不问时 asked = False、什么都不记。"""
    from pearnly_core.assistant import ledger as conversation_ledger
    from pearnly_core.assistant import sessions
    from pearnly_core.write_precheck import api as precheck

    made = sessions.create(project=company, period=period, mode="chat")
    frappe.db.commit()
    out = {"asked": False}
    try:
        frappe.local.pearnly_assistant = {"session_id": made["id"]}
        try:
            conversation_ledger.choose(made["id"], account_set, by="page")
            frappe.db.commit()
        except Exception as error:  # noqa: BLE001 —— 已定了也行
            frappe.db.rollback()
            out["ledger_choose"] = str(error)[:200]
        deadline = time.monotonic() + card_wait
        while True:   # 新接的公司先要电脑读一次科目表（waiting「正在读科目表」）：等它读完再判
            frappe.db.rollback()
            card = precheck.card(company, [name])
            if not card.get("waiting") or time.monotonic() > deadline:
                break
            time.sleep(10)
        items = [item for group in card.get("groups") or [] for item in group.get("items") or []] or list(card.get("items") or [])
        out["kinds"] = [item.get("kind") for item in items]
        answers = []
        for item in items:
            if item.get("kind") == "detail":
                value = detail
            else:
                codes = [str(option.get("code")) for option in item.get("options") or []]
                value = item.get("value") or next((code for code in codes if code != "__new__"), codes[0] if codes else "")
                if not value and item.get("search"):
                    # 可选范围太大、卡上只给搜索（科目表）：像用户在搜索框里搜指定的那个科目（默认 TEST2 的服务费用科目 53-05-07-00）；
                    # 搜不到就不答、让场景停在「要选」上，不乱挑（11-24 首跑退回第一个，挑成了现金科目 11-01-01-00）
                    wanted = str(account or "53-05-07-00")
                    found = precheck.search(company, name, item["search"], wanted, 0, 20).get("options") or []
                    value = next((str(row.get("code")) for row in found if str(row.get("code")) == wanted), "")
            if value:
                answers.append({"key": item["key"], "value": value, "kind": item.get("kind")})
        out["answers"] = [{"kind": answer["kind"], "value": answer["value"]} for answer in answers]
        if answers:
            saved = precheck.record(company, [name], [{"key": a["key"], "value": a["value"]} for a in answers], guided=True)
            frappe.db.commit()
            out.update(asked=True, saved={k: saved.get(k) for k in ("ok", "saved", "refused")} if isinstance(saved, dict) else saved)
    finally:
        frappe.local.pearnly_assistant = None
        try:
            sessions.delete(made["id"])
            frappe.db.commit()
        except Exception:  # noqa: BLE001
            frappe.db.rollback()
    return out


site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
try:
    from pearnly_core.execution.replay import TEST_SITES

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")) or frappe.conf.get("pearnly_product") != "cowork":
        raise SystemExit(f"refuse: {site} is not a Cowork test tenant")
    ledger, user = params["ledger"], params["user"]
    frappe.set_user(user)

    from pearnly_core.express_operator import api as operator_api
    from pearnly_core.express_operator import contracts as operator_contracts
    from pearnly_core.express_operator import tasks
    from pearnly_core.express_rules.targets.identity import normalize_express_account_key
    from pearnly_cowork.companies import service as cowork_companies
    from pearnly_cowork.companion import pool
    from pearnly_cowork.connections import service as connections
    from pearnly_cowork.history import storage

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
    frappe.db.rollback()  # 放掉连接行锁（只读了目录）
    result["reported"] = [item.get("source_id") for item in rows if isinstance(item, dict)][:20]
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
    if params.get("lines"):
        # 照样票的明细行造（识别出来的票一般带明细）：代录要在 Express 里一行一行录（11-24 首跑：不带明细时代录停在空明细的 RR 上）
        fields["items"] = [{"name": row["description"], "qty": str(row.get("qty") or "1"), "price": str(row["price"]),
                            "subtotal": str(Decimal(str(row["price"])) * Decimal(str(row.get("qty") or "1")))} for row in params["lines"]]
    pages = [{"page_index": 1, "fields": fields}]
    posting_kind = params.get("posting_kind", "service")
    history = storage.create_recognized(conn.workspace, pages, f"acc-{secrets.token_hex(6)}", source="manual", confidence="high",
                                        ai_raw=pages, client=conn.client, posting_kind=posting_kind)
    name = history["name"]
    # 代录按对话账套认单（同 CC 对话里放的票）：记上连接与账套
    frappe.db.set_value(HISTORY, name, {"push_connection": conn.name, "push_account_set": account_set}, update_modified=False)
    frappe.db.commit()
    seed = {"seed_connection": conn.name, "seed_account_set": account_set}
    company = frappe.db.get_value("Cowork Company", seed, "name")
    if not company:
        made = cowork_companies.create(conn.name, account_set) or {}
        frappe.db.commit()
        company = frappe.db.get_value("Cowork Company", seed, "name") or made.get("id") or made.get("name")
    if not company:
        raise SystemExit("no Cowork company for the acceptance ledger")
    result.update(history=name, company=company, account_set=account_set)

    task = tasks.create(company, invoice_date[:7])
    frappe.db.commit()
    result["card"] = answer_card(company, name, invoice_date[:7], account_set, account=params.get("account", ""))   # 新站点先问的卡照用户那样答一次；不问就什么都不记
    started = operator_api.start(company, [name], task=task["name"])
    frappe.db.commit()
    result.update(task=task["name"], started=started)
    stuck = [row for row in (started or {}).get("results") or [] if row.get("status") == "needs_choice"]
    if stuck:   # 还停在要选的项上：不进写账队列，不必空等 wait 秒（11-24 首跑空等了 16 分钟）
        params["wait"] = 0

    deadline, record = time.monotonic() + int(params.get("wait", 900)), {}
    while time.monotonic() < deadline:
        frappe.db.rollback()
        push = frappe.db.get_value(HISTORY, name, "last_push_record")
        if push:
            row = frappe.db.get_value(PUSH, push, ["status", "external_docnum", "reason", "result_unknown", "verify_state", "response_json",
                                                   "payload_json"], as_dict=True) or {}
            meta = (json.loads(row.get("response_json") or "{}") or {}).get("meta") or {}
            payload = json.loads(row.get("payload_json") or "{}") or {}
            record = {"name": push, "status": row.get("status"), "docnum": row.get("external_docnum"), "reason": row.get("reason"),
                      "verify": row.get("verify_state"), "unknown": row.get("result_unknown"), "written_to": meta.get("account_dir"),
                      "written": bool(meta.get("written")), "operator": operator_contracts.is_operator(payload),
                      # 代录回执不带 meta.account_dir（那是直写回执才有的）：落点按载荷里冻结的那本（交给代录时核过是这家公司的这本），
                      # 写没写进去由场景按单号读回账本证明（11-24 首跑：代录录成 RR0000101，这一栏读出空）
                      "frozen_book": "\\".join(part.rstrip("\\") for part in (payload.get("account_root") or "", str(payload.get("account_set") or "").strip("\\")) if part)
                      if not (":" in str(payload.get("account_set") or "") or str(payload.get("account_set") or "").startswith("\\\\")) else str(payload.get("account_set")),
                      "operator_batch": str(((payload.get("operator") or {}).get("batch") or {}).get("id") or "")}
            if record["status"] in ("success", "failed", "manual") or record["unknown"]:
                break
        time.sleep(5)
    view = tasks.read(task["name"])
    result.update(ok=True, record=record,
                  pushes=frappe.db.count(PUSH, {"history": name}),
                  task_view={"status": view.get("status"), "documents": [{key: row.get(key) for key in ("doc_id", "state", "docnum", "reason")}
                                                                        for row in view.get("documents") or []]},
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
