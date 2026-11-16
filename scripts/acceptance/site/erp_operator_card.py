"""验收场景的站点侧脚本（在测试租户的 ERP 网页容器里跑）：代录「开始前卡片 → 记下 → 交单」这条路走一张单，等 CC 桌面版在 Express
界面里录成 → 把卡片、记下的结果、单据、写账件、新建主档挂码与落点账套交回。末行打印 ACC {json}。11-16 真机线三个缺陷的现场：

    env/bin/python /tmp/acc-erp_operator_card.py <站点> '<JSON 参数>'

参数：user（那台电脑的 CC 桌面版上登录的员工）、company_tax / branch、ledger（验收账套路径，只认它）、run、price、wait（默认 900）、
kind：
  intake        H3：放进来的票识别完是收单记录 ``I:<记录>``。不跑识别、不花识别费：照识别结果的样子直接造一条「已识别」的收单记录
                （内容写死：现购、一行账套里已有的库存品、票面合计），再走开始前卡片（写账前置判定 check）→ 记下（remember）→ 交单
                （写入计划「开始」用的 `express_operator.api.hand_in`），开录时提交成 ERP 采购单、交代录。
  stock_group   H5：现购一件账套里没有的新库存品。账套合格的存货科目组两个以上、这家公司没记过选哪个时，开始前卡片要问
                「新物料记哪个物料组」；答第一项（推荐）后记住，交代录照这个组建档。开跑前清掉这家公司记过的存货科目组选择，
                跑完原样还原（只动测试租户的站点数据）。合格的组不到两个 → 拒跑（造不出「开始前问」的现场）。
  new_masters   代录回执挂码：新供应商 + 新物料的现购单交代录。写成后等主档同步做完，交回单上的供应商 / 物料挂的 Express 码，
                以及这一轮新建的供应商 / 物料档（同步要是另建了一条，这里看得到）。

卡片作答照用户的正路：已定的不动；预选的照预选；没有预选的，物料 / 对方选「新建」，其余选第一项（推荐项排第一）。答案按卡片
展开（`write_precheck.contracts.expand`）后交代录卡片调的同一个接口（`express_operator.api.remember`）。登记里标 via = "api"。
防护同别的验收站点脚本：只在测试租户跑；这家公司由 CC 报上来的账套必须全部就是 ledger；写完按单据记下的账套核实际路径。
"""
import base64
import json
import secrets
import sys
import time
from unittest.mock import patch

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]
DIRECT_SEND = "pearnly_erp_portal.express.push.send"
NEW = "__new__"
FIELDS = ["ledger_state", "ledger_docnum", "ledger_reason", "ledger_job", "ledger_book", "ledger_needed"]


def same_path(left, right) -> bool:
    def norm(text):
        return str(text or "").strip().replace("/", "\\").rstrip("\\").casefold()
    return norm(left) == norm(right)


def tax_id(seed: str) -> str:
    """校验位正确的 13 位税号（ERP 建供应商要核）：前 12 位取自 ``seed``。"""
    digits = ("0105" + "".join(ch for ch in seed if ch.isdigit()) + "00000000")[:12]
    check = (11 - sum(int(d) * (13 - i) for i, d in enumerate(digits)) % 11) % 10
    return digits + str(check)


site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
restore_groups = None
try:
    from pearnly_core.cc_desktop import books
    from pearnly_core.execution.replay import TEST_SITES
    from pearnly_core.express_operator import api as operator_api
    from pearnly_core.express_operator import tasks
    from pearnly_core.express_rules.targets import stock_groups
    from pearnly_core.write_precheck import api as precheck_api
    from pearnly_core.write_precheck import contracts as card_rules
    from pearnly_erp_portal.express import choices, facts, push, target

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    filters = {"tax_id": params["company_tax"], "is_active": 1}
    if params.get("branch"):
        filters["branch"] = params["branch"]
    company = None
    for name in frappe.get_all("ERP Company", filters=filters, pluck="name", order_by="name"):
        tax, branch = frappe.db.get_value("ERP Company", name, ["tax_id", "branch"])
        reported = [item.get("path") for item in books.for_company(tax, branch)]
        result.setdefault("reported", {})[name] = reported
        if reported and all(same_path(path, params["ledger"]) for path in reported):
            company = name
            break
    if not company:
        raise SystemExit("refuse: no company whose reported books are exactly the acceptance ledger")
    result["company"] = company
    frappe.set_user(params["user"])
    kind, run, price, today = params["kind"], params["run"], float(params.get("price", 10)), frappe.utils.today()
    started_at = frappe.utils.now_datetime()
    key = target.pick(push._company(company), today)["book"]["key"]
    by_code = {"express_company": company, "is_active": 1, "express_code": ["is", "set"]}
    warehouse = frappe.db.get_value("ERP Warehouse", {"company": company, "express_code": ["is", "set"]}, "name", order_by="express_code asc")
    if not warehouse:
        raise SystemExit(f"test data missing in {company}: no Express warehouse")

    if kind == "stock_group":
        fit = stock_groups.fit(facts.reported(facts.load(key) or {}).get("reported_stock_acc_groups"))
        result["fit_groups"] = [str(row.get("acccod") or "") for row in fit]
        if len(fit) < 2:
            raise SystemExit(f"unmet: the acceptance book has {len(fit)} fit stock account group(s); asking before the start needs two or more")
        restore_groups = frappe.get_all(choices.CHOICE, filters={"company": company, "kind": choices.STOCK_GROUP},
                                        fields=["subject", "value", "label"], limit_page_length=50)
        frappe.db.delete(choices.CHOICE, {"company": company, "kind": choices.STOCK_GROUP})
        frappe.db.commit()
        result["forgot_groups"] = restore_groups

    task = tasks.create(company, today[:7])
    frappe.db.commit()
    expected = {"qty": 1, "price": price}
    if kind == "intake":
        from pearnly_erp_portal.integrations import intake, line_items

        product = frappe.db.get_value("ERP Product", by_code, ["name", "product_name", "express_code"], order_by="express_code asc", as_dict=True)
        supplier = frappe.db.get_value("ERP Supplier", by_code, ["name", "supplier_name", "tax_id", "express_code"],
                                       order_by="express_code asc", as_dict=True)
        if not (product and supplier):
            raise SystemExit(f"test data missing in {company}: product={bool(product)} supplier={bool(supplier)}")
        total = round(price * 1.07, 2)
        read = {"doc_no": f"ACC-I{run}", "doc_date": today, "doc_kind": "purchase_invoice", "has_vat": 1,
                "supplier_name": supplier.supplier_name, "supplier_tax_id": supplier.tax_id or "",
                "lines": [{"item_type": "goods", "description": product.product_name, "qty": 1, "unit_price": price, "vat_rate": 7}],
                "printed": {"total": f"{total:.2f}"}}
        pixel = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        upload = frappe.get_doc({"doctype": "File", "file_name": f"acc-{run}.png", "is_private": 1, "content": pixel}).insert()
        record = frappe.get_doc({"doctype": intake.DOCTYPE, "status": intake.READY, "direction": "purchase", "owner": frappe.session.user,
                                 "sent_by": frappe.session.user, "source": intake.SOURCE_CC, "request_key": f"acc-{run}-{secrets.token_hex(4)}",
                                 "language": "zh", "media_type": "image", "company": company, "party": "@doc", "is_paid": 1,
                                 "payment_method": "cash", "source_file": upload.file_url, "source_file_name": upload.name,
                                 "file_hash": secrets.token_hex(32), "received_at": frappe.utils.now(),
                                 "result": json.dumps(read, ensure_ascii=False)}).insert(ignore_permissions=True)
        chosen = line_items.prepare(record, read)
        # 同 H3 真机那张票：行已认出物料（账套里那件）、仓库已定；卡片问付款与 Express 那几项
        chosen["lines"] = [{**line, "item_type": "goods", "product": product.name, "new": False, "decided": True} for line in chosen["lines"]]
        chosen["warehouse"] = chosen.get("warehouse") or warehouse
        frappe.db.set_value(intake.DOCTYPE, record.name, "line_choices", json.dumps(chosen))
        frappe.db.commit()
        doc_id, doctype, name = f"I:{record.name}", "Purchase Document", None
        result.update(intake=record.name)
    else:
        from pearnly_erp_portal.purchasing import entry, suppliers
        from pearnly_erp_portal.products import service as products

        if kind == "new_masters":
            supplier = suppliers.create_supplier(supplier_name=f"ACC Supplier {run}", tax_id=tax_id(run))["name"]
        else:
            supplier = frappe.db.get_value("ERP Supplier", by_code, "name", order_by="express_code asc")
        item = products.create_product(product_name=f"ACC Item {run}", unit="ชิ้น", unit_price=price, default_cost=price)["name"]
        frappe.db.commit()
        payload = {"doc_kind": "purchase_invoice", "company": company, "supplier": supplier, "doc_no": f"ACC-{run}", "doc_date": today,
                   "has_vat": 1, "price_mode": "exclusive", "is_paid": 1, "update_stock": 1, "warehouse": warehouse,
                   "lines": [{"product": item, "item_type": "goods", "description": f"ACC Item {run}", "qty": 1, "unit": "ชิ้น",
                              "unit_price": price, "vat_rate": 7, "warehouse": warehouse}]}
        real = frappe.enqueue

        def without_direct_send(method, *args, **kwargs):
            return None if method == DIRECT_SEND else real(method, *args, **kwargs)

        # 网页提交的单会自动排直写；代录接的是「还没交进队列」的单（同 erp_operator_post.py）
        with patch.object(frappe, "enqueue", without_direct_send):
            posted = entry.post(payload, request_id=f"acc-{secrets.token_hex(6)}")
            frappe.db.commit()
        doctype, name = "Purchase Document", posted["name"]
        doc_id = f"P:{name}"
        result.update(made_supplier=supplier, made_item=item)
    result.update(company=company, task=task["name"], doc_id=doc_id, book=key)

    def listed(card):
        ids = set(card.get("ready") or []) | {doc for item in card_rules.items_of(card) for doc in item.get("docs") or []}
        for key_ in ("unknown", "not_writable", "waiting"):
            ids |= {str(row.get("doc_id") or "") for row in card.get(key_) or []}
        return ids

    def view(card):
        return {"listed": doc_id in listed(card), "not_writable": card.get("not_writable") or [], "waiting": card.get("waiting") or [],
                "items": [{"kind": item["kind"], "subject": item["subject"], "state": item["state"], "value": item.get("value") or "",
                           "options": [str(option.get("code") or "") for option in item.get("options") or []][:20]}
                          for item in card_rules.items_of(card)]}

    # 开始前卡片（写账前置判定 check：写入计划、模型的 pearnly_precheck、页面卡片同一个判定）。答了一题可能引出下一题（物料选
    # 「新建」后才问记哪个物料组），所以一轮一轮答，最多三轮；每轮的卡片都留下
    rounds, answers, unanswered, errors, remembered, done = [], [], [], [], {"ok": True, "saved": 0}, set()
    for _round in range(3):
        card = precheck_api.card(company, [doc_id])
        rounds.append(view(card))
        fresh = []
        for item in card_rules.items_of(card):
            if item["state"] == card_rules.DECIDED or item["key"] in done:
                continue
            done.add(item["key"])
            codes = [str(option.get("code") or "") for option in item.get("options") or [] if option.get("code")]
            value = item.get("value") or (NEW if item["kind"] in ("item", "party") and (NEW in codes or item.get("search")) else "") \
                or (codes[0] if codes else "")
            if value:
                fresh.append({"key": item["key"], "value": value, "kind": item["kind"]})
            else:
                unanswered.append(item["key"])
        if not fresh:
            break
        answers += fresh
        expanded, wrong = card_rules.expand(card, [{"key": row["key"], "value": row["value"]} for row in fresh])
        errors += wrong
        if not expanded:
            break
        try:
            saved = operator_api.remember(company, json.dumps(expanded))
            frappe.db.commit()
            remembered["saved"] += int((saved or {}).get("saved") or 0)
        except frappe.PermissionError as refused:
            frappe.db.rollback()
            frappe.clear_messages()
            remembered = {"ok": False, "error": "PermissionError", "message": str(refused)[:200]}
            break
    result.update(card=rounds[0], rounds=rounds, answers=answers, unanswered=unanswered, expand_errors=errors, remembered=remembered,
                  card_after=view(precheck_api.card(company, [doc_id])),
                  group_chosen=next((row["value"] for row in answers if row["kind"] == choices.STOCK_GROUP), ""))

    # 交单（写入计划「开始」走的同一个函数）
    try:
        handed = operator_api.hand_in(company, task["name"], [doc_id])
        frappe.db.commit()
    except Exception as error:  # noqa: BLE001 —— 交不进去也是一种结果，如实交回
        frappe.db.rollback()
        frappe.clear_messages()
        handed = {"error": f"{type(error).__name__}: {str(error)[:200]}"}
    result["handed"] = handed

    deadline, state = time.monotonic() + int(params.get("wait", 900)), {}
    while time.monotonic() < deadline:
        frappe.db.rollback()
        if kind == "intake" and not name:
            name = frappe.db.get_value(intake.DOCTYPE, result["intake"], "purchase")
            if not name:
                if "error" in handed or not any(row.get("status") not in ("failed", "not_permitted")
                                                for row in (handed.get("results") or []) if isinstance(row, dict)):
                    break  # 没交成：不空等
                time.sleep(5)
                continue
        state = frappe.db.get_value(doctype, name, FIELDS, as_dict=True) or {}
        job_status = frappe.db.get_value("Pearnly CC Job", state["ledger_job"], "status") if state.get("ledger_job") else None
        if state.get("ledger_state") in ("written", "mismatch", "failed") or (state.get("ledger_state") == "held" and (
                not state.get("ledger_job") or job_status == "manual")):
            break
        time.sleep(5)
    result.update(doc=name or "", state=state)
    if not name:
        result["ok"] = True
        raise SystemExit(0)
    job = frappe.db.get_value("Pearnly CC Job", state.get("ledger_job"), ["status", "verify_state", "reason", "request_key", "actor"], as_dict=True) \
        if state.get("ledger_job") else None
    written = [row.path for row in frappe.get_all(books.BOOK, filters={"book_key": state.get("ledger_book")}, fields=["path"])] \
        if state.get("ledger_book") else []
    if state.get("ledger_state") == "held":
        # 停下的件：单据上的原因与可选项（卡片按单据页同一份出题）
        result["stopped_card"] = view(precheck_api.card(company, [f"P:{name}"]))
    if kind == "new_masters" and state.get("ledger_state") == "written":
        # 写成后产品排一次主档同步：等它做完再看挂码与有没有多出一条
        until = time.monotonic() + int(params.get("sync_wait", 420))
        time.sleep(20)
        while time.monotonic() < until:
            frappe.db.rollback()
            if frappe.db.get_value("ERP Company", company, "express_sync_state") not in ("queued", "reading", "syncing"):
                break
            time.sleep(10)
        frappe.db.rollback()
        result["sync_state"] = frappe.db.get_value("ERP Company", company, "express_sync_state")
        since = {"express_company": company, "creation": [">=", started_at]}
        result["suppliers_new"] = frappe.get_all("ERP Supplier", filters=since, fields=["name", "express_code"], limit_page_length=50)
        result["products_new"] = frappe.get_all("ERP Product", filters=since, fields=["name", "express_code"], limit_page_length=50)
        result["made_codes"] = {"supplier": frappe.db.get_value("ERP Supplier", result["made_supplier"], "express_code") or "",
                                "item": frappe.db.get_value("ERP Product", result["made_item"], "express_code") or ""}
    doc = frappe.get_doc(doctype, name)
    line = (doc.get("lines") or [None])[0]
    expected.update(supplier_code=frappe.db.get_value("ERP Supplier", doc.supplier, "express_code") or "",
                    item_code=(frappe.db.get_value("ERP Product", line.product, "express_code") or "") if line and line.get("product") else "",
                    totals={key_: float(doc.get(key_) or 0) for key_ in ("subtotal", "vat_amount", "grand_total")})
    view_ = tasks.read(task["name"])
    result.update(ok=True, job=job, book_paths=written, expected=expected,
                  jobs=frappe.db.count("Pearnly CC Job", {"source_doctype": doctype, "source_name": name}),
                  task_view={"status": view_.get("status"), "documents": [{key_: row.get(key_) for key_ in ("doc_id", "state", "docnum", "reason")}
                                                                          for row in view_.get("documents") or []]})
except SystemExit as stop:
    if str(stop) not in ("0", "None"):
        result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    if restore_groups is not None:
        # 还原开跑前这家公司记的存货科目组选择（这一轮答的不留）
        try:
            from pearnly_erp_portal.express import choices

            frappe.db.rollback()
            frappe.db.delete(choices.CHOICE, {"company": result.get("company"), "kind": choices.STOCK_GROUP})
            for row in restore_groups:
                choices.remember(result["company"], choices.STOCK_GROUP, row.subject, row.value, row.label)
            frappe.db.commit()
        except Exception:
            result["restore_error"] = frappe.get_traceback()[-500:]
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
