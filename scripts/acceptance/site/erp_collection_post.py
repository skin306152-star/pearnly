"""验收场景的站点侧脚本（在测试租户的 ERP 网页容器里跑）：对验收账套里一张未收清的发票录一笔收款 → 放行 → 等 CC 桌面版写成
RE 回执 → 把收款记录、读发票时看到的状态、写账结果、落点账套交回。末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_collection_post.py <站点> '<JSON 参数>'

参数：user（有收款权限的员工）、company_tax / branch、ledger（验收账套路径，只认它）、
entry：web（网页「新建」：`service.submit_web`，提交即放行）/ line、cc（`service.create_draft` + `service.release`，来源记 line / cc）、
customer（客户的 Express 编码；spoken = 真时先把不换行空格换成普通空格再交进去，像模型复述时那样）、invoice（发票单号）、
cash / transfer（本次现金、转账各多少；full = 真时按此刻可收余额全收现金）、
variant：""（正常）/ twice（web：同一个请求号提交两次）/ edit（line：出卡后改金额，拿旧版本放行应被挡，再拿新版本放行）/
refused（line：草稿没确认时网页先把同一张收清，再放行草稿应被拒、账本不多写）、
edit_cash（edit 变体改成的现金数）、read_wait（等发票读回最多几秒，默认 120）、wait（等写入最多几秒，默认 300）。

三处入口共用同一组服务函数（`collection/service.py`），这里直接走它们，登记里标 via = "api"：证明「同意之后写进账本并且写对」、
三处口径一致；LINE 的逐步对话、网页弹窗、CC 对话本身在流程用例与真机行上验。
防护同别的验收站点脚本：只在测试租户跑；这家公司由 CC 报上来的账套必须全部就是 ledger；写完按记录冻结的账套核实际路径。
"""
import base64
import io
import json
import secrets
import sys
import time

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]
DONE = ("written", "failed", "withdrawn")


def same_path(left, right) -> bool:
    def norm(text):
        return str(text or "").strip().replace("/", "\\").rstrip("\\").casefold()
    return norm(left) == norm(right)


def slip() -> bytes:
    """一张能过原件层的白底图片当转账凭证（内容不参与写账）。"""
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (600, 800), "white").save(out, "JPEG")
    return out.getvalue()


site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
try:
    from pearnly_core.cc_desktop import books
    from pearnly_core.execution.replay import TEST_SITES
    from pearnly_erp_portal.collection import contracts, service

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    filters = {"tax_id": params["company_tax"], "is_active": 1}
    if params.get("branch"):
        filters["branch"] = params["branch"]
    company = None
    for name in frappe.get_all("ERP Company", filters=filters, pluck="name", order_by="name"):
        tax_id, branch = frappe.db.get_value("ERP Company", name, ["tax_id", "branch"])
        reported = [item.get("path") for item in books.for_company(tax_id, branch)]
        result.setdefault("reported", {})[name] = reported
        if reported and all(same_path(path, params["ledger"]) for path in reported):
            company = name
            break
    if not company:
        raise SystemExit("refuse: no company whose reported books are exactly the acceptance ledger")
    frappe.set_user(params["user"])
    entry, variant, invoice = params["entry"], params.get("variant") or "", params["invoice"]
    if entry not in contracts.SOURCES:
        raise SystemExit(f"unknown entry {entry}")

    # ── 收款方式：账套资料还没带收款方式（旧读回）时服务端自己请电脑重读，读好就恢复（11-15 线上：「CC 桌面版需更新」重读即恢复）
    deadline, waited, methods = time.monotonic() + int(params.get("read_wait", 120)), [], None
    while methods is None:
        try:
            methods = service.methods(company)
        except contracts.CollectionError as error:
            frappe.db.commit()   # 请电脑重读的那条请求要留下
            waited.append(error.code)
            if error.code != "express.masters_reading" or time.monotonic() > deadline:
                raise SystemExit(f"receipt methods not readable: {error.code}") from error
            time.sleep(5)
    result["methods"] = {"cash": methods["cash"], "transfers": [item["prefix"] for item in methods["transfers"]],
                         "discount": bool(methods["discount"]), "waited": waited}

    # ── 客户编码：交进去的写法（可能被复述走样）→ 服务端换回账本里的原样
    given = str(params["customer"])
    spoken = given.replace("\xa0", " ") if params.get("spoken") else given
    customer = service.customer_code(company, spoken)
    result["customer"] = {"given": given, "spoken": spoken, "canonical": customer}

    # ── 读这位客户的未收发票：刚开的发票要等电脑读回；没读回之前不许当成「没有这张」
    def available():
        found = service.open_invoices(company, spoken)
        frappe.db.commit()
        row = next((item for item in found["items"] if item["docnum"] == invoice), None)
        return found["refresh"], row

    reads, row, deadline = [], None, time.monotonic() + int(params.get("read_wait", 120))
    while True:
        state, row = available()
        reads.append({"refresh": state, "listed": bool(row)})
        if row or time.monotonic() > deadline:
            break
        time.sleep(3)
    result["reads"] = {"count": len(reads), "first": reads[0], "last": reads[-1],
                       # 说「读好了」却没有这张：就是 11-15「刚开的发票被说成已收清」那个缺陷的样子
                       "ready_without_invoice": len([item for item in reads if item["refresh"] == "ready" and not item["listed"]])}
    if not row:
        raise SystemExit(f"invoice {invoice} never showed up as collectable (last refresh: {reads[-1]['refresh']})")
    result["invoice"] = row

    def plan(cash, transfer):
        """（发票行，现金，方式行）。转账要这本账套有转账方式，凭证当场存一张。"""
        rows = []
        if float(transfer or 0) > 0:
            if not methods["transfers"]:
                raise SystemExit("this ledger has no transfer receipt type")
            voucher = service.store_voucher("acc-slip.jpg", slip())["file"]
            rows.append({"prefix": methods["transfers"][0]["prefix"], "amount": transfer, "voucher": voucher})
        amount = contracts.text(contracts.money(cash or 0) + contracts.money(transfer or 0))
        return [{"docnum": invoice, "amount": amount}], cash or 0, rows

    if params.get("full"):
        invoices, cash, rows = plan(row["available"], 0)
    else:
        invoices, cash, rows = plan(params.get("cash") or 0, params.get("transfer") or 0)

    def settle(name, wait=None):
        """等到这一笔有结果（写成 / 失败 / 撤回 / 被电脑退回草稿），回它此刻的样子。"""
        deadline = time.monotonic() + int(wait or params.get("wait", 300))
        while True:
            frappe.db.rollback()
            view = service.status(name)
            if view["status"] in DONE or view.get("returned") or time.monotonic() > deadline:
                break
            time.sleep(3)
        job = frappe.db.get_value(service.DOCTYPE, name, ["job", "book_key", "consent_ref", "attempt"], as_dict=True)
        view["job"] = frappe.db.get_value("Pearnly CC Job", job.job, ["status", "verify_state", "reason"], as_dict=True) if job.job else None
        view["book_paths"] = [item.path for item in frappe.get_all(books.BOOK, filters={"book_key": job.book_key}, fields=["path"])]
        view["consent_ref"], view["attempt"] = job.consent_ref, int(job.attempt or 0)
        return view

    token = secrets.token_hex(6)
    if entry == "web":
        request = f"acc-{token}"
        card = service.submit_web(company, spoken, invoices, cash=cash, methods=rows, request=request)
        frappe.db.commit()
        if variant == "twice":
            again = service.submit_web(company, spoken, invoices, cash=cash, methods=rows, request=request)
            frappe.db.commit()
            result["twice"] = {"same_record": again["name"] == card["name"],
                               "records_for_request": frappe.db.count(service.DOCTYPE, {"consent_ref": service.WEB_CONSENT + request})}
        result["record"] = settle(card["name"])
    else:
        card = service.create_draft(company, spoken, invoices, cash=cash, methods=rows, source=entry)
        frappe.db.commit()
        result["draft"] = {key: card[key] for key in ("name", "version", "status", "ready", "problem", "customer", "total")}
        version, consent = card["version"], f"{entry}:acc-{token}"
        if variant == "edit":
            changed_invoices, changed_cash, changed_rows = plan(params["edit_cash"], 0)
            edited = service.update_draft(card["name"], version, changed_invoices, cash=changed_cash, methods=changed_rows)
            frappe.db.commit()
            stale = service.release(card["name"], version, consent)   # 拿改之前那张卡的版本确认：应被挡回去重新出卡
            frappe.db.commit()
            result["edit"] = {"version_before": version, "version_after": edited["version"], "stale_release": stale.get("status"),
                              "stale_version": stale.get("version"), "status_after_stale": service.status(card["name"])["status"]}
            version = edited["version"]
        if variant == "refused":
            first = service.submit_web(company, spoken, [{"docnum": invoice, "amount": row["available"]}], cash=row["available"],
                                       request=f"acc-first-{token}")
            frappe.db.commit()
            result["first"] = settle(first["name"])
            late = service.release(card["name"], version, consent)    # 同一张已被收清：写前就拒，一行不写
            frappe.db.commit()
            result["refused"] = {"status": late.get("status"), "code": late.get("code"), "detail": late.get("detail"),
                                 "draft_status": service.status(card["name"])["status"]}
            service.discard_draft(card["name"])
            frappe.db.commit()
            result["record"] = service.status(card["name"])
        else:
            released = service.release(card["name"], version, consent)
            frappe.db.commit()
            result["release"] = {"status": released.get("status"), "code": released.get("code")}
            if released.get("status") != "released":
                raise SystemExit(f"release was not accepted: {released.get('status')} {released.get('code')}")
            result["record"] = settle(card["name"])
    result.update(ok=True, company=company, entry=entry)
except SystemExit as stop:
    result["why"] = str(stop)
except Exception as error:
    frappe.db.rollback()
    code = getattr(error, "code", None)   # 收款规则不过（`contracts.CollectionError`）：码与数原样交回
    result["why"] = f"collection rule refused: {code} {getattr(error, 'detail', '')}" if code else frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
