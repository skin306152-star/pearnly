"""验收场景的站点侧脚本（在测试租户的 Cowork 网页容器里跑）：一张已识别好的采购票（内容写死，不跑识别、不付费）在录入页「提交」→
推送记录 → 确认 → CC 桌面版写进 Express 验收账套 → 回执，把推送记录、单号、读回、落点账套、期望值交回。末行打印 ACC {json}。

    env/bin/python /tmp/acc-cowork_intake_post.py <站点> '<JSON 参数>'

参数：user（CC 桌面版上登录的 Cowork 员工）、connection（Express 连接）、ledger（验收账套路径，只认它）、run、
seller_tax / seller_name / amount / vat（票面，取自验收样票库）、posting_kind（默认 service）、
resubmit（真：同一张票写成后再提交一次——应回同一条推送记录或被挡，不写第二张）、wait（最多等几秒，默认 300）。

走的是录入页「提交」背后的同一组函数（`push.application.api.submit` + `companion.submission.confirm`，同 `scripts/smoke/cowork_express_flow.py`），
票用 `history.storage.create_recognized(source="manual")` 造——识别本身（上传、回放、读票）不在这条场景里，登记里标 via = "api"。
防护照冒烟那三道，落点换成验收账套：这条连接上不许有排着队、要写别的账套的记录；CC 要在线且认得这本；准备后核冻结载荷里的账套，
对不上就撤掉；写完再核实际写到的路径。只在 Cowork 测试租户跑。
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
PUSH, CONNECTION = "Cowork Push Record", "Cowork ERP Connection"


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


def cowork_companies_service():
    from pearnly_cowork.companies import service
    return service


site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
try:
    from pearnly_core.execution.replay import TEST_SITES

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")) or frappe.conf.get("pearnly_product") != "cowork":
        raise SystemExit(f"refuse: {site} is not a Cowork test tenant")
    if not frappe.conf.get("pearnly_cowork_erp_push_enabled"):
        raise SystemExit("refuse: push switch is off on this site")
    ledger, user = params["ledger"], params["user"]
    frappe.set_user(user)

    from pearnly_core.express_rules.targets.identity import normalize_express_account_key
    from pearnly_cowork.companion import pool, submission
    from pearnly_cowork.connections import service as connections
    from pearnly_cowork.history import storage
    from pearnly_cowork.push.application import api as application
    from pearnly_cowork.push.application import service as application_service

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
    number = f"ACC-{params['run']}"
    fields = {"direction": "purchase", "seller_name": params["seller_name"], "seller_tax": params["seller_tax"],
              "buyer_name": attributes.get("company") or attributes.get("name") or "", "buyer_tax": attributes.get("tax_id") or "",
              "subtotal": str(amount), "vat": str(vat), "total_amount": str(amount + vat), "date": invoice_date,
              "document_type": "tax_invoice", "invoice_number": number}
    detail = str(params.get("detail") or "")   # A7「明细对不上」：amount_only（只记金额）/ skip（不入账）
    if detail:
        # 票面明细合计 ≠ 税前额（同 tests/test_express_rules_guided.A7AmountOnlyInThePayload）：税前 100、只有一行 40
        fields["items"] = [{"name": params.get("item_name") or "ค่าบริการโฆษณา", "qty": "1", "price": "40", "subtotal": "40"}]
    pages = [{"page_index": 1, "fields": fields}]
    posting_kind = params.get("posting_kind", "service")
    history = storage.create_recognized(conn.workspace, pages, f"acc-{secrets.token_hex(6)}", source="manual", confidence="high",
                                        ai_raw=pages, client=conn.client, posting_kind=posting_kind)
    frappe.db.commit()
    name = history["name"]
    if detail:
        # 照引导卡的正路答「明细」这一题（引擎 resolve 读这条记下的答案）：这本账套的 Cowork 公司 → 一段对话会话 → 判卡 → 记答案
        from pearnly_core.assistant import ledger as conversation_ledger
        from pearnly_core.assistant import sessions
        from pearnly_core.write_precheck import api as precheck
        from pearnly_cowork.companies import service as cowork_companies

        # CC 对话里放的票，产品会把对话账套记在记录上（push_connection / push_account_set），判卡与记答案都按它；这里手造的记录照样记上
        frappe.db.set_value("Cowork OCR History", name, {"push_connection": conn.name, "push_account_set": account_set},
                            update_modified=False)
        frappe.db.commit()
        seed = {"seed_connection": conn.name, "seed_account_set": account_set}
        company = frappe.db.get_value("Cowork Company", seed, "name")
        if not company:
            made_company = cowork_companies.create(conn.name, account_set) or {}
            frappe.db.commit()
            company = frappe.db.get_value("Cowork Company", seed, "name") or made_company.get("id") or made_company.get("name")
        made = sessions.create(project=company, period=invoice_date[:7], mode="chat")
        frappe.db.commit()
        try:
            frappe.local.pearnly_assistant = {"session_id": made["id"]}
            try:
                conversation_ledger.choose(made["id"], account_set, by="page")
                frappe.db.commit()
            except Exception as error:  # noqa: BLE001 —— 已定了也行
                frappe.db.rollback()
                result["ledger_choose"] = str(error)[:200]
            deadline_card = time.monotonic() + int(params.get("card_wait", 240))
            while True:   # 新接的公司先要电脑读一次科目表（waiting「正在读科目表」）：等它读完再判
                frappe.db.rollback()
                card = precheck.card(company, [name])
                if not card.get("waiting") or time.monotonic() > deadline_card:
                    break
                time.sleep(10)
            items = [item for group in card.get("groups") or [] for item in group.get("items") or []] or list(card.get("items") or [])
            result["card_kinds"] = [item.get("kind") for item in items]
            found = next((item for item in items if item.get("kind") == "detail"), None)
            if not found:
                raise SystemExit(f"the card did not ask the detail question (kinds: {result['card_kinds']})")
            # 整张卡都答上（引导卡要一次答完才写）：明细按本格；有预选的照预选；其余取第一个不是「新建」的选项
            answers = []
            for item in items:
                if item.get("kind") == "detail":
                    value = detail
                else:
                    codes = [str(option.get("code")) for option in item.get("options") or []]
                    value = item.get("value") or next((code for code in codes if code != "__new__"), codes[0] if codes else "")
                if value:
                    answers.append({"key": item["key"], "value": value})
            result["answers"] = [{"kind": item.get("kind"), "value": answer["value"]} for item, answer in
                                 zip([i for i in items if any(a["key"] == i["key"] for a in answers)], answers)]
            saved = precheck.record(company, [name], answers, guided=True)
            frappe.db.commit()
            result["detail"] = {"asked": True, "key": found["key"], "value": detail,
                                "saved": {k: saved.get(k) for k in ("ok", "saved", "refused") if isinstance(saved, dict)}}
        finally:
            frappe.local.pearnly_assistant = None
            try:
                sessions.delete(made["id"])
                frappe.db.commit()
            except Exception:  # noqa: BLE001
                frappe.db.rollback()
    view = application_service.choices_view(name, conn.name, account_set, posting_kind)
    frappe.db.rollback()
    if (view or {}).get("needed") and not detail:
        # 新站点 / 新账套还没记过这几项：照用户在引导卡上答一次（这张票记上对话账套、这本账套的 Cowork 公司，同 detail 那段）
        frappe.db.set_value("Cowork OCR History", name, {"push_connection": conn.name, "push_account_set": account_set},
                            update_modified=False)
        frappe.db.commit()
        seed = {"seed_connection": conn.name, "seed_account_set": account_set}
        company = frappe.db.get_value("Cowork Company", seed, "name")
        if not company:
            made_company = cowork_companies_service().create(conn.name, account_set) or {}
            frappe.db.commit()
            company = frappe.db.get_value("Cowork Company", seed, "name") or made_company.get("id") or made_company.get("name")
        result["card"] = answer_card(company, name, invoice_date[:7], account_set, account=params.get("account", ""))
        view = application_service.choices_view(name, conn.name, account_set, posting_kind)
        frappe.db.rollback()
    if (view or {}).get("needed") and not detail:
        raise SystemExit(f"choices needed in this book: {[item.get('kind') for item in view['needed']]}")

    def submit(key):
        revision = frappe.db.get_value(CONNECTION, conn.name, "record_revision")
        version = frappe.db.get_value("Cowork OCR History", name, "record_version")
        out = application.submit(history=name, connection=conn.name, request_key=key, expected_history_version=version,
                                 expected_connection_revision=revision, account_set_key=account_set, posting_kind=posting_kind)
        frappe.db.commit()
        return out

    request_key = f"acc-{secrets.token_hex(8)}"
    prepared = submit(request_key)
    result["prepared"] = {key: prepared.get(key) for key in ("name", "status", "reason")}
    if detail == "skip":
        # 「不入账」：不写，照旧停在「明细对不上」（准备阶段就停下，不进写账队列）
        result.update(ok=True, history=name)
        raise SystemExit("")
    if prepared.get("status") != "prepared":
        raise SystemExit(f"not prepared: {prepared.get('reason') or prepared.get('status')}")
    payload = json.loads(frappe.db.get_value(PUSH, prepared["name"], "payload_json") or "{}")
    folder = str(payload.get("account_set") or "")
    frozen = folder if (":" in folder or folder.startswith("\\\\")) else \
        "\\".join(part.rstrip("\\") for part in (payload.get("account_root") or "", folder.strip("\\")) if part)
    if norm(frozen) != norm(ledger):
        application.cancel_prepared(prepared["name"], prepared.get("revision"))
        frappe.db.commit()
        raise SystemExit(f"refuse: the frozen book is not the acceptance ledger: {frozen}")
    confirmed = submission.confirm(prepared["name"], prepared.get("revision"), "confirm:" + request_key)
    frappe.db.commit()
    if (confirmed or {}).get("status") != "pending":
        raise SystemExit(f"not queued: {confirmed}")

    def record_of(push):
        row = frappe.db.get_value(PUSH, push, ["status", "external_docnum", "reason", "result_unknown", "verify_state", "response_json"],
                                  as_dict=True) or {}
        meta = (json.loads(row.get("response_json") or "{}") or {}).get("meta") or {}
        return {"name": push, "status": row.get("status"), "docnum": row.get("external_docnum"), "reason": row.get("reason"),
                "verify": row.get("verify_state"), "unknown": row.get("result_unknown"), "written_to": meta.get("account_dir"),
                "written": bool(meta.get("written")), "duplicate_skipped": bool(meta.get("duplicate_skipped"))}

    deadline, record = time.monotonic() + int(params.get("wait", 300)), {}
    while time.monotonic() < deadline:
        frappe.db.rollback()
        record = record_of(prepared["name"])
        if record["status"] in ("success", "manual", "cancelled"):
            break
        time.sleep(5)
    result["record"] = record
    if params.get("resubmit") and record.get("status") == "success":
        try:
            again = submit(f"acc-{secrets.token_hex(8)}")
            second = {"status": again.get("status"), "name": again.get("name"), "reason": again.get("reason")}
            if again.get("status") == "prepared" and again.get("name") != prepared["name"]:
                confirmed = submission.confirm(again["name"], again.get("revision"), "confirm:again:" + request_key)
                frappe.db.commit()
                time.sleep(int(params.get("settle", 45)))
                frappe.db.rollback()
                second["record"] = record_of(again["name"])
        except Exception as error:  # noqa: BLE001 —— 被挡是允许的结果，由账本读回判对错
            frappe.db.rollback()
            second = {"status": "refused", "error": str(error)[:200]}
        result["resubmit"] = second
    last = frappe.db.get_value("Cowork OCR History", name, ["last_push_status", "last_push_record"], as_dict=True)
    result.update(ok=True, history=name, last=last, pushes=frappe.db.count(PUSH, {"history": name}),
                  expected={"number": number, "seller_tax": params["seller_tax"], "amount": float(amount), "vat": float(vat),
                            "total": float(amount + vat), "date": invoice_date})
except SystemExit as stop:
    result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
