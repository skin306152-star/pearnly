"""实验栈实证（11-28，并发冲突 B ③ 静默丢更新与管钱底线）：在真 MariaDB 11.8（``innodb_snapshot_isolation=OFF``，同线上）上，
本连接先建好读视图，另一个连接（另一个线程、另一个 Frappe 连接）按产品代码改完并提交，本连接再走同一个函数——
修前的写法读到旧值、静默写错；修后加锁读拿到最新值。每一格跑 ``--rounds`` 轮。

只在本机实验栈（maclab，站点 ``*.maclab.localhost`` / ``billing.internal``）跑，只建、只删自己的测试数据（带 ``b3lab`` 标记）：

    docker exec -i <栈>-erp-bench-1 env/bin/python - erp erp.maclab.localhost [--rounds 10] < scripts/conflict-silent-loss-lab-verify.py
    docker exec -i <栈>-erp-billing-web-1 env/bin/python - billing billing.internal [--rounds 10] < 同上
    docker exec -i <栈>-cowork-bench-1 env/bin/python - cowork cowork.maclab.localhost [--rounds 10] < 同上

末行打印 ``RESULT {...}``：每格 ``{"ok": 轮数, "bad": 轮数, "detail": 第一轮不对的样子}``，全部 ``bad == 0`` 才算通过（退出码 0）。
"""
import json
import os
import secrets
import sys
import threading
import traceback

import frappe

SITES = "/home/frappe/frappe-bench/sites"
TAG = "b3lab"


def connect(site):
    if not (site.endswith(".maclab.localhost") or site == "billing.internal"):
        raise SystemExit("maclab sites only")
    os.chdir(SITES)
    frappe.init(site=site, sites_path=SITES)
    frappe.connect()


def elsewhere(site, user, function):
    """在另一个线程、另一个数据库连接里以 ``user`` 跑 ``function()`` 并提交（另一个请求）。出错原样带回。"""
    box = {}

    def run():
        try:
            frappe.init(site=site, sites_path=SITES)
            frappe.connect()
            frappe.set_user(user)
            box["value"] = function()
            frappe.db.commit()
        except Exception as error:  # noqa: BLE001
            box["error"] = "".join(traceback.format_exception_only(type(error), error)).strip()[:400]
            box["trace"] = traceback.format_exc()[-1500:]
            frappe.db.rollback()
        finally:
            frappe.destroy()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(120)
    if "error" in box:
        print(box.get("trace", ""), file=sys.stderr)
        raise RuntimeError(box["error"])
    return box.get("value")


def read_view(doctype):
    """本连接开一笔新事务、做一次普通读：读视图从此刻起（之后别人提交的普通读都看不到）。"""
    frappe.db.rollback()
    frappe.db.sql(f"select count(*) from `tab{doctype}`")


def tally(results, name, good, detail):
    cell = results.setdefault(name, {"ok": 0, "bad": 0})
    if good:
        cell["ok"] += 1
    else:
        cell["bad"] += 1
        cell.setdefault("detail", detail)


# ── ERP 站：销售单同号、代录资料清单、看板拖卡 ─────────────────────────────────────────────────────


def erp_user():
    return frappe.db.get_value("User", {"name": ["like", "acc.erp@%"], "enabled": 1}, "name") or "Administrator"


def erp_sales_same_request(site, rounds, results):
    """同一个 request_id：另一路刚提交了一张，本连接（读视图早于它）再提交同一份 → 只能有一张，本次回 duplicate。"""
    from pearnly_erp_portal.masters import company as companies
    from pearnly_erp_portal.sales import entry

    user = erp_user()
    frappe.set_user(user)
    co = companies.active_company()
    frappe.db.set_value("ERP Company", co, "express_enabled", 0)
    frappe.db.commit()
    payload = {"company": co, "doc_date": frappe.utils.nowdate(), "doc_kind": "receipt", "buyer_name": f"{TAG} buyer",
               "vat_rate": 0, "has_vat": 0, "lines": [{"item_type": "service", "description": f"{TAG} service", "qty": 1,
                                                       "unit": "งาน", "unit_price": 10}]}
    made = []
    for _ in range(rounds):
        request = f"{TAG}-sale-{secrets.token_hex(6)}"
        read_view("ERP Sales Document")
        elsewhere(site, user, lambda: entry.post(dict(payload), request_id=request))
        try:
            second = entry.post(dict(payload), request_id=request)
        except Exception as error:  # noqa: BLE001
            second = {"error": str(error)[:200]}
        frappe.db.commit()
        names = frappe.get_all("ERP Sales Document", filters={"request_id": request}, pluck="name")
        made += names
        tally(results, "erp.sales_same_request", len(names) == 1 and second.get("duplicate") is True,
              {"docs": len(names), "second": second})
    for name in made:
        frappe.db.set_value("ERP Sales Document", name, "status", "void", update_modified=False)  # 不积成待录单
    frappe.db.commit()


def operator_task(user, company):
    from pearnly_core.express_operator import tasks

    doc = frappe.get_doc({"doctype": tasks.DOCTYPE, "product": tasks.site_product(), "company": company,
                          "company_title": f"{TAG} co", "period": "2026-11", "title": f"{TAG} task", "status": "preparing"})
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    frappe.db.set_value(tasks.DOCTYPE, doc.name, "owner", user, update_modified=False)
    frappe.db.commit()
    return doc.name


def operator_listed(site, rounds, results):
    """同一个代录任务：另一路刚记进 A，本连接（读视图更早）记 B → 快照里 A、B 都在。"""
    from pearnly_core.express_operator import listed, tasks

    user = erp_user()
    frappe.set_user(user)
    company = f"{TAG}-co"
    names = []
    for index in range(rounds):
        task = operator_task(user, company)
        names.append(task)
        read_view(tasks.DOCTYPE)
        frappe.db.get_value(tasks.DOCTYPE, task, listed.FIELD)  # 本连接先读过一次快照（同 CC 工具先看过状态）
        elsewhere(site, user, lambda: listed.remember(task, company, [{"doc_id": f"A{index}", "group": "purchase"}]))
        listed.remember(task, company, [{"doc_id": f"B{index}", "group": "purchase"}])
        frappe.db.commit()
        snapshot = json.loads(frappe.db.get_value(tasks.DOCTYPE, task, listed.FIELD) or "{}")
        tally(results, "core.operator_listed", {f"A{index}", f"B{index}"} <= set(snapshot), {"snapshot": snapshot})
    frappe.set_user("Administrator")
    for task in names:
        frappe.delete_doc(tasks.DOCTYPE, task, force=True, ignore_permissions=True)
    frappe.db.commit()


def board_same_gap(site, rounds, results, service_module, contracts_module, card, column, prefix):
    """同一列 X、Y 两张卡之间：另一路刚把 C1 拖进来，本连接（读视图更早）把 C2 拖进同一个空隙 → 两张位置不同、都在 X 与 Y 之间。"""
    import importlib

    service = importlib.import_module(service_module)
    # 只测位置怎么算：功能购买与看板权限放开（两个线程共用模块，替身两边都生效），跑完还原
    saved = (service.access.allowed, service.access.can)
    service.access.allowed, service.access.can = (lambda user=None: True), (lambda board, right, user=None: True)
    try:
        _board_same_gap(site, rounds, results, service, card, prefix)
    finally:
        service.access.allowed, service.access.can = saved


def _board_same_gap(site, rounds, results, service, card, prefix):
    user = frappe.session.user
    board = service.board()  # 默认看板（不存在就建）
    target = board["lists"][0]["name"] if isinstance(board, dict) and board.get("lists") else None
    if not target:
        raise SystemExit(f"{prefix}: no board list")
    made = []
    for _ in range(rounds):
        x, y, c1, c2 = (service.create(f"{TAG} {label}", target)["name"] for label in ("x", "y", "c1", "c2"))
        made += [x, y, c1, c2]
        service.move(x, target, None, None)
        service.move(y, target, x, None)
        frappe.db.commit()
        read_view(card)
        elsewhere(site, user, lambda: service.move(c1, target, x, y))
        service.move(c2, target, x, y)
        frappe.db.commit()
        pos = {key: frappe.db.get_value(card, key, "position") for key in (x, y, c1, c2)}
        good = pos[c1] != pos[c2] and pos[x] < pos[c1] < pos[y] and pos[x] < pos[c2] < pos[y]
        tally(results, f"{prefix}.board_same_gap", good, {"positions": pos})
    if made:  # 卡片只许经看板接口删（写入闸）：测试行直接删库，连同它们的活动记录
        frappe.db.delete(card, {"name": ["in", made]})
        activity = card.replace("Card", "Activity")
        if frappe.db.table_exists(activity):
            frappe.db.delete(activity, {"card": ["in", made]})
    frappe.db.commit()


def guarded(results, name, function, *args):
    """一格出错不挡别的格：记成这格不过、带上错误。"""
    try:
        function(*args)
    except Exception as error:  # noqa: BLE001
        frappe.db.rollback()
        cell = results.setdefault(name, {"ok": 0, "bad": 0})
        cell["bad"] += 1
        cell["error"] = "".join(traceback.format_exception_only(type(error), error)).strip()[:400]
        traceback.print_exc(file=sys.stderr)


PITFALLS = ("LinkValidationError", "QueryDeadlockError", "TimestampMismatchError", "Traceback")


def void_with_children(site, rounds, results):
    """11-26 第一版「先锁后读」踩过的两个坑，在作废入口上专门验（11-28 B ③）：同一张采购单同时到「作废」「补开收据」（新建 File、
    挂附件子表）「付款」（付款子表），几张单一起。作废那一路不许报 LinkValidationError（读到别人刚挂附件的新单）、不许把子表
    间隙锁的 QueryDeadlockError / 保存前比对漏给用户：只能成功，或按业务规则拒（已作废）。另两路的结果只记录（付款、附件入口归 ②）。"""
    from pearnly_erp_portal.masters import company as companies
    from pearnly_erp_portal.purchasing import entry, suppliers

    user = erp_user()
    frappe.set_user(user)
    co = companies.active_company()
    supplier = frappe.db.get_value("ERP Supplier", {"supplier_name": f"{TAG} supplier"}) or \
        suppliers.create_supplier(supplier_name=f"{TAG} supplier")["name"]
    frappe.db.commit()

    def made(index):
        doc = entry.post({"company": co, "supplier": supplier, "doc_date": frappe.utils.nowdate(), "currency": "THB", "fx_rate": 1,
                          "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "is_paid": 0,
                          "update_stock": 0, "doc_no": f"{TAG}-VC-{secrets.token_hex(3)}-{index}",
                          "lines": [{"item_type": "service", "description": f"{TAG} void children", "qty": 1, "unit": "งาน",
                                     "unit_price": 1000, "vat_rate": 7, "wht_rate": 3}]},
                         request_id=f"{TAG}-vc-{secrets.token_hex(6)}")
        frappe.db.commit()
        return doc["name"]

    for _ in range(rounds):
        names = [made(index) for index in range(4)]
        box, threads = {}, []

        def call(label, function):
            def run():
                try:
                    frappe.init(site=site, sites_path=SITES)
                    frappe.connect()
                    frappe.set_user(user)
                    box[label] = {"ok": function()}
                    frappe.db.commit()
                except Exception as error:  # noqa: BLE001
                    box[label] = {"error": "".join(traceback.format_exception_only(type(error), error)).strip()[:300],
                                  "trace": traceback.format_exc()[-600:]}
                    frappe.db.rollback()
                finally:
                    frappe.destroy()
            threads.append(threading.Thread(target=run))

        from pearnly_erp_portal.purchasing import api as purchase_api
        for name in names:
            call(("void", name), lambda name=name: (frappe.db.sql("select 1"), purchase_api.api_cancel_purchase(name, "b3lab"))[1])
            call(("voucher", name), lambda name=name: purchase_api.api_purchase_voucher(name, "substitute_receipt"))
            call(("pay", name), lambda name=name: purchase_api.api_purchase_payment(
                name, json.dumps({"amount": 100, "method": "cash", "request_id": f"{TAG}-p-{secrets.token_hex(4)}"})))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
        frappe.db.rollback()
        voids = {key[1]: value for key, value in box.items() if key[0] == "void"}
        bad = {name: value for name, value in voids.items()
               if "error" in value and any(word in value["error"] + value.get("trace", "") for word in PITFALLS)}
        states = {name: frappe.db.get_value("Purchase Document", name, "status") for name in names}
        tally(results, "erp.void_with_children", not bad and all(state == "void" for state in states.values()),
              {"bad_void": bad, "states": states,
               "others": {f"{k[0]} {k[1]}": v.get("error", "ok")[:120] for k, v in box.items() if k[0] != "void"}})
        results.setdefault("erp.void_with_children.others", {"ok": 0, "bad": 0, "errors": []})
        results["erp.void_with_children.others"]["errors"] += [v["error"][:100] for k, v in box.items()
                                                               if k[0] != "void" and "error" in v][:6]


def stale_save_keeps_ledger(site, rounds, results):
    """账本栏只由写账链路写（`express.ledger_guard`，11-28 B ③）：写账回执刚记下 Express 单号并提交，本连接（读视图更早，拿着旧副本）
    经付款保存这张单 → 单号仍在（修前被盖空：巡检会再写一遍，重复入账）。付款直调 `entry.payment`（内部路径，入口层的 commit 管不到）。"""
    from pearnly_erp_portal.express import push
    from pearnly_erp_portal.masters import company as companies
    from pearnly_erp_portal.purchasing import entry, suppliers

    user = erp_user()
    frappe.set_user(user)
    co = companies.active_company()
    supplier = frappe.db.get_value("ERP Supplier", {"supplier_name": f"{TAG} supplier"}) or \
        suppliers.create_supplier(supplier_name=f"{TAG} supplier")["name"]
    frappe.db.commit()
    for _ in range(rounds):
        name = entry.post({"company": co, "supplier": supplier, "doc_date": frappe.utils.nowdate(), "currency": "THB", "fx_rate": 1,
                           "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "is_paid": 0,
                           "update_stock": 0, "doc_no": f"{TAG}-SS-{secrets.token_hex(3)}",
                           "lines": [{"item_type": "service", "description": f"{TAG} stale save", "qty": 1, "unit": "งาน",
                                      "unit_price": 1000, "vat_rate": 7}]}, request_id=f"{TAG}-ss-{secrets.token_hex(6)}")["name"]
        frappe.db.commit()
        docnum = f"{TAG}-RR{secrets.token_hex(3)}"
        read_view("Purchase Document")
        frappe.get_doc("Purchase Document", name)  # 本连接先读过这张单
        def receipt():  # 写账回执记下单号；同时有人改了票面合计（`express.printed.set_total` 同样不改修改时间）
            push._set(push.PURCHASE, name, {"ledger_state": "written", "ledger_docnum": docnum})
            frappe.db.set_value("Purchase Document", name, "printed_total", 123.45, update_modified=False)
        elsewhere(site, user, receipt)
        try:
            entry.payment(name, amount=100, method="cash")
            frappe.db.commit()
            outcome = "paid"
        except Exception as error:  # noqa: BLE001
            frappe.db.rollback()
            outcome = repr(error)[:200]
        row = frappe.db.get_value("Purchase Document", name, ["ledger_state", "ledger_docnum", "paid_amount", "printed_total"],
                                  as_dict=True)
        tally(results, "erp.stale_save_keeps_ledger", row.ledger_docnum == docnum and row.ledger_state == "written"
              and float(row.printed_total or 0) == 123.45,
              {"outcome": outcome, "row": dict(row)})
        frappe.db.set_value("Purchase Document", name, {"ledger_state": None, "ledger_docnum": None}, update_modified=False)
        frappe.db.commit()


def erp(site, rounds):
    results = {}
    guarded(results, "erp.sales_same_request", erp_sales_same_request, site, rounds, results)
    guarded(results, "core.operator_listed", operator_listed, site, rounds, results)
    frappe.set_user("Administrator")  # 看板权限：管理员能改所有看板
    guarded(results, "erp.void_with_children", void_with_children, site, rounds, results)
    guarded(results, "erp.stale_save_keeps_ledger", stale_save_keeps_ledger, site, rounds, results)
    frappe.set_user("Administrator")
    guarded(results, "erp.board_same_gap", board_same_gap, site, rounds, results, "pearnly_erp_portal.work.service", None,
            "ERP Work Card", "ERP Work Column", "erp")
    return results


# ── 账本站（billing.internal）：扣费撞冲突重试、用尽留记录；欢迎信 ─────────────────────────────────────


def billing(site, rounds):
    from pearnly_control import billing as ledger
    from pearnly_control import billing_api

    results = {}
    tenant = frappe.db.get_value("Pearnly Tenant", {}, "name")
    if not tenant:
        raise SystemExit("billing: no tenant")
    billing_api._require_service = lambda *args, **kwargs: None  # 服务令牌只在 HTTP 头上，这里直接调函数
    real_write = ledger._write_txn

    def flaky(times):
        state = {"left": times}

        def write(*args, **kwargs):
            if state["left"] > 0:
                state["left"] -= 1
                raise frappe.QueryDeadlockError("Deadlock found when trying to get lock (b3lab injected)")
            return real_write(*args, **kwargs)
        return write

    product = "erp"
    for _ in range(rounds):
        # 撞一次冲突（钱包已改、台账未写时）→ 重试 → 只扣一次
        reference = f"{TAG}:{secrets.token_hex(8)}"
        before = ledger.balance_of(tenant)
        ledger._write_txn = flaky(1)
        try:
            answer = billing_api.service_charge(tenant, product, "pdf", 1, "B", reference, "b3lab retry")
            frappe.db.commit()
        except Exception as error:  # noqa: BLE001
            answer = {"error": str(error)[:200]}
            frappe.db.rollback()  # 同 Frappe 请求出错时整笔回滚
        finally:
            ledger._write_txn = real_write
        rows = frappe.get_all("Pearnly Credit Transaction", filters={"reference": reference, "type": "usage"},
                              fields=["amount_thb"])
        after = ledger.balance_of(tenant)
        charged = -sum(ledger._money(row.amount_thb) for row in rows)
        tally(results, "billing.retry_charges_once",
              answer.get("ok") is True and len(rows) == 1 and before - after == charged and charged > 0,
              {"answer": answer, "rows": len(rows), "before": str(before), "after": str(after)})
        # 一直冲突 → 用尽：没扣、余额不变、留一条带 reference 的「没记上」记录
        reference = f"{TAG}:{secrets.token_hex(8)}"
        before = ledger.balance_of(tenant)
        ledger._write_txn = flaky(99)
        try:
            billing_api.service_charge(tenant, product, "pdf", 1, "B", reference, "b3lab exhausted")
            raised = False
        except Exception:  # noqa: BLE001
            raised = True
        finally:
            ledger._write_txn = real_write
        frappe.db.rollback()
        logged = frappe.db.sql("""select count(*) from `tabError Log` where method = %s and error like %s""",
                               (getattr(billing_api, "CHARGE_NOT_RECORDED", "billing charge conflict exhausted"),
                                f"%reference={reference}%"))[0][0]
        rows = frappe.db.count("Pearnly Credit Transaction", {"reference": reference})
        tally(results, "billing.exhausted_leaves_record",
              raised and rows == 0 and ledger.balance_of(tenant) == before and logged == 1,
              {"raised": raised, "rows": rows, "logged": logged})
        # 同一 reference 补扣一次（产品侧再报）：扣上、只一行
        answer = billing_api.service_charge(tenant, product, "pdf", 1, "B", reference, "b3lab exhausted")
        frappe.db.commit()
        tally(results, "billing.recharge_after_exhausted",
              answer.get("ok") is True and frappe.db.count("Pearnly Credit Transaction", {"reference": reference}) == 1,
              {"answer": answer})
    frappe.db.sql("delete from `tabError Log` where error like %s", (f"%{TAG}:%",))
    frappe.db.commit()
    welcome_once(site, rounds, results)
    return results


def welcome_once(site, rounds, results):
    """开户欢迎信：另一路刚排好、已提交，本连接（读视图更早）再排 → 只有一封。发信本身换成只建队列行的替身。"""
    from pearnly_control.onboarding import welcome

    # 实验站没有开户单：直接插一行只带几个必填栏的测试开户单（不走开户流程、不建站点），跑完删掉
    issue = f"{TAG}-ISS-{secrets.token_hex(4)}"
    frappe.db.sql("""insert into `tabPearnly Issue` (name, creation, modified, owner, modified_by, status, tenant_name,
        account_username) values (%s, now(), now(), 'Administrator', 'Administrator', 'Draft', %s, %s)""",
                  (issue, f"{TAG} tenant", f"{TAG}-user"))
    frappe.db.commit()
    try:
        _welcome_once(site, rounds, results, welcome, issue)
    finally:
        frappe.db.sql("delete from `tabPearnly Issue` where name = %s", (issue,))
        frappe.db.commit()


def _welcome_once(site, rounds, results, welcome, issue):
    sent = []

    def queue(request):
        name = f"{TAG}-{secrets.token_hex(6)}"
        sent.append(name)
        frappe.db.set_value("Pearnly Issue", request.issue, {"welcome_email_queue": name, "welcome_email_error": ""},
                            update_modified=False)
        return name

    for _ in range(rounds):
        frappe.db.set_value("Pearnly Issue", issue, "welcome_email_queue", None, update_modified=False)
        frappe.db.commit()
        sent.clear()
        request = frappe._dict(issue=issue)
        read_view("Pearnly Issue")
        frappe.get_doc("Pearnly Issue", issue)  # 本连接先读过开户单
        elsewhere(site, "Administrator", lambda: _queue_with(welcome, request, queue))
        _queue_with(welcome, request, queue)
        frappe.db.commit()
        tally(results, "control.welcome_once", len(sent) == 1, {"queued": len(sent)})


def _queue_with(welcome, request, queue):
    """``queue_for_request`` 到「排过没有」那一步为止照原样走；排信本身换成 ``queue``（不连邮件账户、不发信）。"""
    source = welcome.queue_for_request
    import inspect

    text = inspect.getsource(source)
    marker = "    require_cloud_target("
    head = text[:text.index(marker)] + "    return QUEUE(request)\n"
    scope = dict(welcome.__dict__, QUEUE=queue)
    exec(compile(head, "<b3lab welcome head>", "exec"), scope)  # noqa: S102 — 只截到「排过没有」那一步
    return scope["queue_for_request"](request)


# ── Cowork 站：自动推送档位、撤回推送、删票、看板 ─────────────────────────────────────────────────


def raw_row(doctype, name, values):
    """实验站是空库：直插一行只带测试要用的几栏（不走控制器），跑完由调用方删。"""
    columns = ["name", "creation", "modified", "owner", "modified_by", *values]
    frappe.db.sql(f"insert into `tab{doctype}` ({', '.join(f'`{c}`' for c in columns)}) values "
                  f"(%s, now(), now(), 'Administrator', 'Administrator'{', %s' * len(values)})", (name, *values.values()))


def cowork(site, rounds):
    results = {}
    frappe.set_user("Administrator")
    guarded(results, "cowork.board_same_gap", board_same_gap, site, rounds, results, "pearnly_cowork.work.service", None,
            "Cowork Work Card", "Cowork Work Column", "cowork")
    company, history = f"{TAG}-CO-{secrets.token_hex(3)}", f"{TAG}-H-{secrets.token_hex(3)}"
    workspace = frappe.db.get_value("Cowork Workspace", {}, "name") or f"{TAG}-WS"
    raw_row("Cowork Company", company, {"title": f"{TAG} co", "workspace": workspace})
    raw_row("Cowork OCR History", history, {"workspace": workspace})
    frappe.db.commit()
    try:
        guarded(results, "cowork.automation_keeps_l0", automation_keeps_l0, site, rounds, results, company)
        guarded(results, "cowork.withdrawn_keeps_new_push", push_state_withdrawn, site, rounds, results, history)
        guarded(results, "cowork.delete_drops_new_records", removal_drops_new_records, site, rounds, results, history)
    finally:
        frappe.db.sql("delete from `tabCowork Company Automation` where company = %s", (company,))
        frappe.db.sql("delete from `tabCowork Push Record` where history = %s", (history,))
        frappe.db.sql("delete from `tabCowork Company` where name = %s", (company,))
        frappe.db.sql("delete from `tabCowork OCR History` where name = %s", (history,))
        frappe.db.commit()
    return results


def removal_drops_new_records(site, rounds, results, history):
    """删票：本连接读视图建好之后，另一路给这张票记了一条已结束的推送记录并提交 → 删票时这条也要删掉，不留孤儿。
    只走 `removal._pushing` + `_drop_related` 这两步（删票本身要权限与原件，不在这里）。"""
    import inspect

    from pearnly_cowork.history import removal

    new_signature = len(inspect.signature(removal._drop_related).parameters) == 2
    doc = frappe._dict(name=history)
    for _ in range(rounds):
        record = f"{TAG}-PR-{secrets.token_hex(4)}"
        read_view("Cowork Push Record")
        frappe.db.sql("select name from `tabCowork Push Record` where history = %s", (history,))
        elsewhere(site, "Administrator", lambda: raw_row("Cowork Push Record", record, {"history": history,
                                                                                     "status": "succeeded"}))
        if new_signature:
            busy, pushed = removal._pushing(doc)
            removal._drop_related(history, pushed)
        else:
            busy = removal._pushing(doc)
            removal._drop_related(history)
        frappe.db.commit()
        left = frappe.db.count("Cowork Push Record", {"history": history})
        tally(results, "cowork.delete_drops_new_records", not busy and left == 0, {"busy": busy, "left": left})
        frappe.db.sql("delete from `tabCowork Push Record` where history = %s", (history,))
        frappe.db.commit()


def automation_keeps_l0(site, rounds, results, company):
    """回读处理完（按旧档位 L2 算出仍是 L2）与写入核不上（降 L0）并发：另一路先降了 L0 并提交，本连接（读视图更早）再按回读写
    → 最后必须是 L0。用 ``_set`` / ``state`` 原样走，回读的证据与评估换成固定答案（只测读档位、写回这一段）。"""
    from pearnly_cowork.companies import automation
    from pearnly_cowork.push.automation import rules as tier_rules

    high = getattr(tier_rules, "L2", None) or "L2"
    saved = {key: getattr(automation, key) for key in ("account_company", "documents")}
    saved_rules = (automation.automation_rules.evaluate, automation.automation_rules.tally, automation.service.facts)
    automation.account_company = lambda connection, row: (company, "")
    automation.documents = lambda company_name, period: ([], True)
    automation.automation_rules.evaluate = lambda tier, since, periods: (tier, "")  # 证据照旧：档位不变
    automation.automation_rules.tally = lambda docs: {}
    automation.service.facts = lambda connection: {"account_sets": [{"source_id": "b3lab-key"}]}
    try:
        for _ in range(rounds):
            automation._set(company, high, "b3lab")
            frappe.db.commit()
            read_view("Cowork Company Automation")
            automation.state(company)  # 本连接先读过档位（回读那笔事务前面读过很多东西）
            elsewhere(site, "Administrator", lambda: automation.on_mismatch("conn", "set", {}))
            automation.on_readback("conn", "b3lab-key", "2026-10")
            frappe.db.commit()
            tier = frappe.db.get_value("Cowork Company Automation", {"company": company}, "tier")
            tally(results, "cowork.automation_keeps_l0", tier_rules.tier_of(tier) == tier_rules.L0, {"tier": tier})
    finally:
        for key, value in saved.items():
            setattr(automation, key, value)
        automation.automation_rules.evaluate, automation.automation_rules.tally, automation.service.facts = saved_rules


def push_state_withdrawn(site, rounds, results, history):
    """撤回旧的推送记录 R1 时，另一路刚重推、票上已指着新记录 R2 并提交 → 本连接（读视图更早）撤回 R1 不能清掉 R2 的状态。"""
    from pearnly_cowork.history import push_state

    saved = (push_state._changed, push_state._announce)
    push_state._changed = push_state._announce = lambda *args, **kwargs: None
    try:
        for _ in range(rounds):
            old, new = f"{TAG}-R1-{secrets.token_hex(4)}", f"{TAG}-R2-{secrets.token_hex(4)}"
            frappe.db.set_value("Cowork OCR History", history, {"last_push_record": old, "last_push_status": "queued"},
                                update_modified=False)
            frappe.db.commit()
            read_view("Cowork OCR History")
            frappe.db.get_value("Cowork OCR History", history, "last_push_record")
            elsewhere(site, "Administrator", lambda: frappe.db.set_value(
                "Cowork OCR History", history, {"last_push_record": new, "last_push_status": "queued"},
                update_modified=False))
            push_state.withdrawn(frappe._dict(name=old, history=history, owner="Administrator"))
            frappe.db.commit()
            now = frappe.db.get_value("Cowork OCR History", history, ["last_push_record", "last_push_status"], as_dict=True)
            tally(results, "cowork.withdrawn_keeps_new_push", now.last_push_record == new and now.last_push_status == "queued",
                  {"now": dict(now)})
    finally:
        push_state._changed, push_state._announce = saved


def main(argv):
    product, site = argv[0], argv[1]
    rounds = int(argv[argv.index("--rounds") + 1]) if "--rounds" in argv else 10
    connect(site)
    results = {"erp": erp, "billing": billing, "cowork": cowork}[product](site, rounds)
    ok = all(cell["bad"] == 0 for cell in results.values())
    print("RESULT " + json.dumps({"ok": ok, "rounds": rounds, "cells": results}, ensure_ascii=False, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
