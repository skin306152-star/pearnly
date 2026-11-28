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
    _like_a_request()


def _like_a_request():
    """同网页请求 / 后台任务开头的 `before_request` / `before_job`：出 PDF 解析样式的 cssutils 进程级开关设成「不抛」
    （`pearnly_core.desk.print_css`）。直接调函数不经请求钩子，同一进程几个线程并发出 PDF 会撞上框架坏样式抛 SyntaxErr，
    那是测试替身没走钩子，不是产品问题（11-28 核过：补上后 24 次并发出凭证全过）。"""
    from pearnly_core.desk.print_css import lenient_css_parsing

    lenient_css_parsing()


def elsewhere(site, user, function):
    """在另一个线程、另一个数据库连接里以 ``user`` 跑 ``function()`` 并提交（另一个请求）。出错原样带回。"""
    box = {}

    def run():
        try:
            frappe.init(site=site, sites_path=SITES)
            frappe.connect()
            _like_a_request()
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


ONLY = set()  # ``--only 格名,格名``：只跑这几格


def guarded(results, name, function, *args):
    """一格出错不挡别的格：记成这格不过、带上错误。"""
    if ONLY and name not in ONLY:
        return
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
                    _like_a_request()
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


def _purchase(co, supplier, tag):
    from pearnly_erp_portal.purchasing import entry

    name = entry.post({"company": co, "supplier": supplier, "doc_date": frappe.utils.nowdate(), "currency": "THB", "fx_rate": 1,
                       "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "is_paid": 0,
                       "update_stock": 0, "doc_no": f"{TAG}-{tag}-{secrets.token_hex(3)}",
                       "lines": [{"item_type": "service", "description": f"{TAG} {tag}", "qty": 1, "unit": "งาน",
                                  "unit_price": 1000, "vat_rate": 7}]}, request_id=f"{TAG}-{tag}-{secrets.token_hex(6)}")["name"]
    frappe.db.commit()
    return name


def _threads(site, user, calls):
    """同时跑几路（各自一条连接、各自提交），回 ``{label: {"ok": 结果} | {"error": 一行}}``。"""
    box, threads = {}, []
    for label, function in calls:
        def run(label=label, function=function):
            try:
                frappe.init(site=site, sites_path=SITES)
                frappe.connect()
                _like_a_request()
                frappe.set_user(user)
                box[label] = {"ok": function()}
                frappe.db.commit()
            except Exception as error:  # noqa: BLE001
                box[label] = {"error": "".join(traceback.format_exception_only(type(error), error)).strip()[:240]}
                frappe.db.rollback()
            finally:
                frappe.destroy()
        threads.append(threading.Thread(target=run))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    return box


def same_doc_writes(site, rounds, results):
    """B ② E1 / E4：同一张采购单同时到 3 笔付款 + 挂附件 + 补开凭证（页面同一组白名单入口），几张单一起。修前付款一路漏出 1213 /
    保存前比对（500 / 417）；修后全部成功，每张已付 = 3 笔之和、付款行 3 行。"""
    from pearnly_erp_portal.masters import company as companies
    from pearnly_erp_portal.purchasing import api as purchase_api, suppliers

    user = erp_user()
    frappe.set_user(user)
    co = companies.active_company()
    supplier = frappe.db.get_value("ERP Supplier", {"supplier_name": f"{TAG} supplier"}) or \
        suppliers.create_supplier(supplier_name=f"{TAG} supplier")["name"]
    frappe.db.commit()
    for _ in range(rounds):
        names = [_purchase(co, supplier, "SD") for _ in range(4)]
        calls = []
        for name in names:
            for index in range(3):
                calls.append(((name, f"pay{index}"), lambda name=name, index=index: purchase_api.api_purchase_payment(
                    name, json.dumps({"amount": 100, "method": "cash", "request_id": f"{TAG}-sdp-{name}-{index}"}))))
            calls.append(((name, "voucher"), lambda name=name: purchase_api.api_purchase_voucher(name, "substitute_receipt")))
        box = _threads(site, user, calls)
        frappe.db.rollback()
        errors = {f"{k[0]} {k[1]}": v["error"] for k, v in box.items() if "error" in v}
        paid = {name: (float(frappe.db.get_value("Purchase Document", name, "paid_amount") or 0),
                       frappe.db.count("ERP Purchase Payment", {"parent": name, "is_reversal": 0}))
                for name in names}
        tally(results, "erp.same_doc_writes", not errors and all(value == (300.0, 3) for value in paid.values()),
              {"errors": errors, "paid": paid})


def amend_vs_payment(site, rounds, results, web):
    """B ② E7（管钱）：「修改」作废原单与一笔付款同时到。``web``：页面入口 `api_post_purchase(amended_from=…)`（修后拿 post 锁后 commit，
    按最新付款冲回）；否则内部调用 `entry.post(amended_from=…)`、本连接读视图更早（LINE / CC 那一类在别的事务里调的样子）。
    判（不变式）：原单作废时冲回的金额恒等于它实付的合计（不悄悄少冲）；内部调用撞上并发付款只许报错（整笔回滚、不留半截）。"""
    from pearnly_erp_portal.masters import company as companies
    from pearnly_erp_portal.purchasing import api as purchase_api, entry, suppliers

    user = erp_user()
    frappe.set_user(user)
    co = companies.active_company()
    supplier = frappe.db.get_value("ERP Supplier", {"supplier_name": f"{TAG} supplier"}) or \
        suppliers.create_supplier(supplier_name=f"{TAG} supplier")["name"]
    frappe.db.commit()
    label = "erp.amend_vs_payment_web" if web else "erp.amend_vs_payment_internal"
    for _ in range(rounds):
        name = _purchase(co, supplier, "AM")
        purchase_api.api_purchase_payment(name, json.dumps({"amount": 100, "method": "cash", "request_id": f"{TAG}-am0-{name}"}))
        frappe.db.commit()
        doc = frappe.get_doc("Purchase Document", name)
        payload = {"company": doc.company, "supplier": doc.supplier, "doc_date": str(doc.doc_date), "currency": "THB", "fx_rate": 1,
                   "has_vat": 1, "price_mode": "exclusive", "doc_kind": doc.doc_kind, "is_paid": 0, "update_stock": 0,
                   "doc_no": doc.doc_no + "-A", "lines": [{"item_type": "service", "description": f"{TAG} amended", "qty": 1,
                                                          "unit": "งาน", "unit_price": 1000, "vat_rate": 7}]}
        if web:
            import time

            def paying():
                time.sleep(0.3)  # 落在「修改」那路建好读视图之后、拿锁之前
                return purchase_api.api_purchase_payment(name, json.dumps(
                    {"amount": 200, "method": "cash", "request_id": f"{TAG}-am1-{name}"}))

            def amending():
                frappe.db.sql("select count(*) from `tabPurchase Document`")  # 请求开头的普通读：读视图从这里起
                time.sleep(0.8)
                return purchase_api.api_post_purchase(json.dumps(payload), f"{TAG}-amend-{name}", amended_from=name)

            box = _threads(site, user, [("pay", paying), ("amend", amending)])
        else:
            read_view("Purchase Document")
            frappe.get_doc("Purchase Document", name)  # 本连接（内部调用方的事务）先读过原单
            box = _threads(site, user, [("pay", lambda: purchase_api.api_purchase_payment(name, json.dumps(
                {"amount": 200, "method": "cash", "request_id": f"{TAG}-am1-{name}"})))])
            try:
                entry.post(dict(payload), request_id=f"{TAG}-amend-{name}", amended_from=name)
                box["amend"] = {"ok": True}
            except Exception as error:  # noqa: BLE001
                frappe.db.rollback()
                box["amend"] = {"error": type(error).__name__}
        frappe.db.rollback()
        row = frappe.db.get_value("Purchase Document", name, ["status", "paid_amount"], as_dict=True)
        paid = sum(float(v) for v in frappe.get_all("ERP Purchase Payment", filters={"parent": name, "is_reversal": 0},
                                                       pluck="amount"))
        reversed_ = -sum(float(v) for v in frappe.get_all("ERP Purchase Payment", filters={"parent": name, "is_reversal": 1},
                                                           pluck="amount"))
        voided_right = row.status == "void" and abs(reversed_ - paid) < 0.001 and float(row.paid_amount or 0) == 0
        if web:
            good = voided_right and "error" not in box.get("amend", {})  # 页面入口：一定改成，且按最新付款冲回
        else:
            # 内部调用：要么改成且冲回正确，要么撞冲突整笔回滚（原单未作废、没有冲回行），不许作废了却冲少
            good = voided_right or (row.status != "void" and reversed_ == 0 and "error" in box.get("amend", {}))
        tally(results, label, good, {"box": box, "status": row.status, "paid": paid, "reversed": reversed_})


def _private_file(tag):
    """本人上传的一份私有文件（附件、LINE 原件用）。"""
    import io

    from pypdf import PdfWriter

    writer, buffer = PdfWriter(), io.BytesIO()
    writer.add_blank_page(72, 72)
    writer.add_metadata({"/Title": secrets.token_hex(16)})  # 每份内容不同（不和别的 File 共用盘上那份）
    writer.write(buffer)
    file = frappe.get_doc({"doctype": "File", "file_name": f"{TAG}-{tag}-{secrets.token_hex(3)}.pdf", "is_private": 1,
                           "content": buffer.getvalue()})
    file.insert(ignore_permissions=True)
    frappe.db.commit()
    return file


def _stale_then(delay, function):
    """一路请求：开头先做一次普通读（读视图从这里起，同网页请求 / LINE 回调开头读会话、绑定），等 ``delay`` 秒再做事。"""
    import time

    def run():
        frappe.db.sql("select count(*) from `tabERP Company`")
        time.sleep(delay)
        return function()
    return run


def line_card_submit_vs_cancel(site, rounds, results):
    """B ② E10：同一张 LINE 待提交卡，网页「提交」（`api_submit_line_intake`）与 LINE「取消」（`handlers._save_card` + `cancel_card`）
    同时到，两种先后各半。判：只有一路生效——要么入了账（卡已确认、单据在、原件还挂在单据上），要么取消了（卡已取消、没有单据）；
    不许「卡显示已取消、单据却入了账 / 原件被删」，也不许漏出 LinkValidation / 1213 / 417。"""
    from pearnly_erp_portal.integrations import api as intake_api, handlers, intake, line_out
    from pearnly_erp_portal.masters import company as companies
    from pearnly_erp_portal.purchasing import suppliers

    user = erp_user()
    frappe.set_user(user)
    co = companies.active_company()
    frappe.db.set_value("ERP Company", co, "express_enabled", 0)  # 不排写账（本格只看卡片与单据）
    supplier = frappe.db.get_value("ERP Supplier", {"supplier_name": f"{TAG} supplier"}) or \
        suppliers.create_supplier(supplier_name=f"{TAG} supplier")["name"]
    frappe.db.commit()
    replies = []
    line_out.reply = lambda token, messages: replies.append(messages) or True
    for index in range(rounds):
        file = _private_file("LC")
        card = frappe.get_doc({"doctype": intake.DOCTYPE, "status": intake.READY, "direction": "purchase", "sent_by": user,
                               "line_user_id": "U" + "b" * 32, "source": "line", "company": co, "result": "{}",
                               "source_file_name": file.name, "received_at": frappe.utils.now_datetime()})
        card.insert(ignore_permissions=True)
        frappe.db.commit()
        payload = {"supplier": supplier, "doc_date": frappe.utils.nowdate(), "currency": "THB", "fx_rate": 1, "has_vat": 1,
                   "price_mode": "exclusive", "doc_kind": "purchase_invoice", "is_paid": 0, "update_stock": 0,
                   "doc_no": f"{TAG}-LC-{secrets.token_hex(3)}",
                   "lines": [{"item_type": "service", "description": f"{TAG} line card", "qty": 1, "unit": "งาน",
                              "unit_price": 500, "vat_rate": 7}]}
        first, second = (0.3, 0.8) if index % 2 else (0.8, 0.3)
        box = _threads(site, user, [
            ("submit", _stale_then(first, lambda: intake_api.api_submit_line_intake(card.name, json.dumps(payload)))),
            ("cancel", _stale_then(second, lambda: handlers._save_card(card.name, "", intake.cancel_card)))])
        frappe.db.rollback()
        row = frappe.db.get_value(intake.DOCTYPE, card.name, ["status", "purchase"], as_dict=True)
        attached = frappe.db.get_value("File", file.name, ["attached_to_name"], as_dict=True)
        on_disk = os.path.exists(frappe.utils.get_site_path("private", "files", os.path.basename(file.file_url)))
        docs = frappe.get_all("Purchase Document", filters={"request_id": "line-intake:" + card.name}, pluck="name")
        entered = row.status == intake.CONFIRMED and docs == [row.purchase] and attached \
            and attached.attached_to_name == row.purchase and on_disk
        cancelled = row.status == intake.CANCELLED and not docs
        leaked = {k: v["error"] for k, v in box.items() if "error" in v and any(word in v["error"] for word in PITFALLS)}
        tally(results, "erp.line_card_submit_vs_cancel", (entered or cancelled) and not leaked,
              {"order": "submit first" if index % 2 else "cancel first", "card": row, "docs": docs, "file": attached,
               "on_disk": on_disk, "box": box})
        for name in docs:
            frappe.db.set_value("Purchase Document", name, "status", "void", update_modified=False)
        frappe.db.commit()


def _collection_seed():
    """收款格的数据（同 `scripts/flows/collection_lib.seed` 的做法）：收款员、账套收款类型、一位本格专用客户与一张未收清发票。"""
    from pearnly_erp_portal.collection import service, snapshot
    from pearnly_erp_portal.express import facts
    from pearnly_erp_portal.masters import company as companies

    frappe.set_user(erp_user())
    co = companies.active_company() or frappe.db.get_value("ERP Company", {}, "name")
    frappe.db.set_value("ERP Company", co, "express_enabled", 1)
    collector = erp_user()  # 实验栈的会计账号带「收款员」岗位
    row = frappe.db.get_value("ERP Company", co, ["name", "company_name", "tax_id", "branch"], as_dict=True)
    book = service.book_for(row, frappe.utils.nowdate())
    data = dict(facts.load(book["key"]) or {})
    data.update(receipt_types=[{"code": "02", "prefix": "CH", "name": "รับเงินสด", "name_en": "", "bank": "", "account": "1111-00",
                                "account_issue": ""}], cash_account="1111-00",
                salesmen=data.get("salesmen") or [{"code": "S1", "name": "lab"}])
    data.setdefault("receipt_run", {"prefix": "RE", "next": "2400001", "department": "HQ"})
    if not any(isinstance(item, dict) and item.get("code") == "HQ" for item in data.get("departments") or []):
        data["departments"] = list(data.get("departments") or []) + [{"code": "HQ", "name": "HQ"}]
    facts.save(book["key"], data)
    code = ("CB" + secrets.token_hex(3)).upper()
    frappe.get_doc({"doctype": "ERP Customer", "customer_name": f"{TAG} collect {code}", "express_company": co,
                    "express_code": code, "is_active": 1}).insert(ignore_permissions=True)
    docnum = ("IV" + secrets.token_hex(3)).upper()
    snapshot.replace(co, book["key"], [{"docnum": docnum, "rectyp": "3", "docdat": frappe.utils.nowdate(), "customer": code,
                                         "salesman": "S1", "department": "", "net": "1000.00", "received": "0.00",
                                         "remaining": "1000.00"}], frappe.utils.now_datetime().isoformat(), customer=code)
    frappe.db.commit()
    return co, collector, book["key"], code, docnum


def _collection_cleanup(collector, book_key, customer):
    from pearnly_erp_portal.collection import contracts, service

    frappe.set_user(collector)
    for name in frappe.get_all("ERP Collection", filters={"customer": customer, "status": ["in", [contracts.DRAFT, contracts.QUEUED,
                                                                                                 contracts.FAILED]]}, pluck="name"):
        try:
            service.withdraw(name)
        except Exception:  # noqa: BLE001
            frappe.db.rollback()
    frappe.set_user("Administrator")
    frappe.db.delete("ERP Receivable Snapshot", {"book_key": book_key, "customer": customer})
    frappe.db.commit()


def collection_edit_vs_release(site, rounds, results):
    """B ② E11：同一笔收款草稿，网页「改」（`api_collection_save`）与 LINE / CC「确认放行」（`service.release`，调用方的读视图更早）
    同时到，两种先后各半。判：只有一路按版本生效——改成了（版本 2、仍是草稿、放行回 changed）或放行了（待写入、改那路被拒）；
    不许两路都算成，也不许漏出 1213 / 417 / 500。"""
    from pearnly_erp_portal.collection import api as collection_api, contracts, service

    co, collector, book_key, customer, docnum = _collection_seed()
    try:
        for index in range(rounds):
            frappe.set_user(collector)
            made = service.create_draft(co, customer, [{"docnum": docnum, "amount": "400"}], cash="400")
            frappe.db.commit()
            first, second = (0.3, 0.8) if index % 2 else (0.8, 0.3)
            box = _threads(site, collector, [
                ("save", _stale_then(first, lambda: collection_api.api_collection_save(
                    made["name"], made["version"], [{"docnum": docnum, "amount": "300"}], cash="300"))),
                ("release", _stale_then(second, lambda: service.release(made["name"], made["version"], f"line:{TAG}-{index}")))])
            frappe.db.rollback()
            row = frappe.db.get_value("ERP Collection", made["name"], ["status", "draft_version"], as_dict=True)
            released = (box.get("release") or {}).get("ok") or {}
            saved = "ok" in (box.get("save") or {})
            leaked = {k: v["error"] for k, v in box.items() if "error" in v and any(word in v["error"] for word in PITFALLS)}
            good = (saved and row.status == contracts.DRAFT and int(row.draft_version) == 2
                    and released.get("status") == "changed") or \
                (released.get("status") == "released" and row.status == contracts.QUEUED and not saved
                 and int(row.draft_version) == 1)
            tally(results, "erp.collection_edit_vs_release", good and not leaked,
                  {"order": "save first" if index % 2 else "release first", "row": row, "box": box})
            _collection_cleanup(collector, book_key, customer)
            frappe.db.delete("ERP Receivable Snapshot", {"book_key": book_key, "customer": customer})
            frappe.db.commit()
            co, collector, book_key, customer, docnum = _collection_seed()
    finally:
        _collection_cleanup(collector, book_key, customer)


def collection_double_release(site, rounds, results):
    """B ② E11（管钱）：同一张发票（未收 1000）两笔草稿各收 1000，两路同时放行、读视图都早于对方提交。判：最多放行一笔，
    另一笔回 invalid（超收）；不许两笔都放行（同一张发票收两遍）。"""
    from pearnly_erp_portal.collection import contracts, service

    for index in range(rounds):
        co, collector, book_key, customer, docnum = _collection_seed()
        try:
            frappe.set_user(collector)
            drafts = [service.create_draft(co, customer, [{"docnum": docnum, "amount": "1000"}], cash="1000") for _ in range(2)]
            frappe.db.commit()
            box = _threads(site, collector, [
                ((draft["name"]), _stale_then(0.3 + 0.4 * position, lambda draft=draft, position=position: service.release(
                    draft["name"], draft["version"], f"line:{TAG}-dr-{index}-{position}")))
                for position, draft in enumerate(drafts)])
            frappe.db.rollback()
            statuses = [frappe.db.get_value("ERP Collection", draft["name"], "status") for draft in drafts]
            queued = statuses.count(contracts.QUEUED)
            leaked = {k: v["error"] for k, v in box.items() if "error" in v and any(word in v["error"] for word in PITFALLS)}
            tally(results, "erp.collection_double_release", queued == 1 and not leaked, {"statuses": statuses, "box": box})
        finally:
            _collection_cleanup(collector, book_key, customer)


def _offset_seed(lines):
    """收款格 + 这位客户的发票与抵减单（``lines``：``[(单号前缀, rectyp, 未收)]``，同 CC 桌面版读回的快照行）。回单号表。"""
    from pearnly_erp_portal.collection import snapshot

    co, collector, book_key, customer, _ = _collection_seed()
    docnums = [(prefix + secrets.token_hex(3)).upper() for prefix, _, _ in lines]
    snapshot.replace(co, book_key, [{"docnum": docnum, "rectyp": rectyp, "docdat": frappe.utils.nowdate(), "customer": customer,
                                      "salesman": "S1", "department": "", "net": remaining, "received": "0.00",
                                      "remaining": remaining}
                                     for docnum, (_, rectyp, remaining) in zip(docnums, lines)],
                     frappe.utils.now_datetime().isoformat(), customer=customer)
    frappe.db.commit()
    return co, collector, book_key, customer, docnums


def collection_double_offset(site, rounds, results):
    """退货减项进收款（11-28）× B ②：同一张减项单（未抵 300）被两笔收款各勾满、各收一张不同的发票（1000），两路同时放行、
    读视图都早于对方提交、同时起跑。判：最多放行一笔，另一笔回 invalid（超收）、仍是草稿；不许两笔都抵（减项单被抵 600），
    也不许漏出 1213 / 417 / 500（放行先锁整位客户的快照行，`snapshot.lock_customer`）。"""
    from pearnly_erp_portal.collection import contracts, service

    for index in range(rounds):
        co, collector, book_key, customer, (one, two, credit) = _offset_seed(
            [("IV", "3", "1000.00"), ("IV", "3", "1000.00"), ("SR", "5", "300.00")])
        try:
            frappe.set_user(collector)
            drafts = [service.create_draft(co, customer, [{"docnum": invoice, "amount": "1000"}, {"docnum": credit, "amount": "300"}],
                                           cash="700") for invoice in (one, two)]
            frappe.db.commit()
            box = _threads(site, collector, [
                ((draft["name"]), _stale_then(0.3, lambda draft=draft, position=position: service.release(
                    draft["name"], draft["version"], f"line:{TAG}-do-{index}-{position}")))
                for position, draft in enumerate(drafts)])
            frappe.db.rollback()
            statuses = [frappe.db.get_value("ERP Collection", draft["name"], "status") for draft in drafts]
            refused = [v["ok"].get("code") for v in box.values() if isinstance(v.get("ok"), dict) and v["ok"].get("status") == "invalid"]
            leaked = {k: v["error"] for k, v in box.items() if "error" in v and any(word in v["error"] for word in PITFALLS)}
            good = statuses.count(contracts.QUEUED) == 1 and statuses.count(contracts.DRAFT) == 1 \
                and refused == [contracts.OVER_COLLECTED]
            tally(results, "erp.collection_double_offset", good and not leaked, {"statuses": statuses, "box": box})
        finally:
            _collection_cleanup(collector, book_key, customer)


def collection_same_customer_releases(site, rounds, results):
    """11-28 退货减项 × B ②：同一位客户两笔收款各收一张不同的发票，两路同时起跑放行。判：两笔都放行，不漏出 1213 / 417 / 500。
    修前（先锁各自几张发票、再锁整位客户 / 读别人记录的预留）两路各拿一半互等，10 轮 9 轮 1213。"""
    from pearnly_erp_portal.collection import contracts, service

    for index in range(rounds):
        co, collector, book_key, customer, (one, two) = _offset_seed([("IV", "3", "1000.00"), ("IV", "3", "1000.00")])
        try:
            frappe.set_user(collector)
            drafts = [service.create_draft(co, customer, [{"docnum": invoice, "amount": "1000"}], cash="1000")
                      for invoice in (one, two)]
            frappe.db.commit()
            box = _threads(site, collector, [
                ((draft["name"]), _stale_then(0.3, lambda draft=draft, position=position: service.release(
                    draft["name"], draft["version"], f"line:{TAG}-sc-{index}-{position}")))
                for position, draft in enumerate(drafts)])
            frappe.db.rollback()
            statuses = [frappe.db.get_value("ERP Collection", draft["name"], "status") for draft in drafts]
            leaked = {k: v["error"] for k, v in box.items() if "error" in v}
            tally(results, "erp.collection_same_customer_releases", statuses.count(contracts.QUEUED) == 2 and not leaked,
                  {"statuses": statuses, "box": box})
        finally:
            _collection_cleanup(collector, book_key, customer)


def collection_same_customer_via_line(site, rounds, results):
    """同 :func:`collection_same_customer_releases`，经 LINE【确认】入口（`line_collection._release`：先提交、放行撞冲突整笔重来）。
    回复只收下不发（替身）。判：两笔都放行，每笔只回一次、只排一件写账件，不漏出 1213 / 417 / 500。"""
    from pearnly_core.cc_desktop import jobs
    from pearnly_erp_portal.collection import contracts, service
    from pearnly_erp_portal.integrations import line_collection

    sent = []
    saved = line_collection._send, line_collection._text, line_collection.line_receipt.start
    line_collection._send = lambda token, messages: sent.append(token) or True
    line_collection._text = lambda token, text: sent.append(token) or True
    line_collection.line_receipt.start = lambda key, user, token, fallback, **kw: sent.append(token) or True
    try:
        for index in range(rounds):
            co, collector, book_key, customer, (one, two) = _offset_seed([("IV", "3", "1000.00"), ("IV", "3", "1000.00")])
            try:
                frappe.set_user(collector)
                drafts = [service.create_draft(co, customer, [{"docnum": invoice, "amount": "1000"}], cash="1000")
                          for invoice in (one, two)]
                frappe.db.commit()
                sent.clear()
                box = _threads(site, collector, [
                    ((draft["name"]), _stale_then(0.3, lambda draft=draft, position=position: line_collection._release(
                        "U" + "c" * 32, draft["name"], draft["version"], f"r-{TAG}-{index}-{position}", f"{TAG}-lv-{index}-{position}")))
                    for position, draft in enumerate(drafts)])
                frappe.db.rollback()
                statuses = [frappe.db.get_value("ERP Collection", draft["name"], "status") for draft in drafts]
                queued = frappe.get_all(jobs.JOB, filters={"source_name": ["in", [d["name"] for d in drafts]]}, pluck="name")
                leaked = {k: v["error"] for k, v in box.items() if "error" in v}
                good = statuses.count(contracts.QUEUED) == 2 and len(queued) == 2 and sorted(sent) == sorted(set(sent)) \
                    and len(sent) == 2
                tally(results, "erp.collection_same_customer_via_line", good and not leaked,
                      {"statuses": statuses, "jobs": len(queued), "replies": list(sent), "box": box})
            finally:
                _collection_cleanup(collector, book_key, customer)
    finally:
        line_collection._send, line_collection._text, line_collection.line_receipt.start = saved


OFFSET_SHAPES = (  # (名, 快照行, 草稿的 invoices 金额, 现金, 应写进载荷的 invoices / offsets / cash)
    ("partial", [("IV", "3", "1000.00"), ("SR", "5", "300.00")], ["1000", "300"], "700", "700.00"),
    ("net_zero", [("IV", "3", "300.00"), ("SR", "5", "300.00")], ["300", "300"], "0", "0.00"),
    ("net_below_zero", [("IV", "3", "200.00"), ("SR", "5", "300.00")], ["200", "300"], "-100", "-100.00"),
)


def collection_offset_shapes(site, rounds, results):
    """退货减项进收款（11-28）× B ②：部分抵、全抵（净额 0）、净额小于 0 三种，放行时的加锁读与普通读核出的结果逐项相同，
    放行后待写入，交给 CC 桌面版的载荷里发票、抵减单、现金就是草稿那几样（金额正数、减项 RECTYP 5、现金可负）。"""
    from pearnly_core.cc_desktop import jobs
    from pearnly_erp_portal.collection import contracts, service

    for index in range(rounds):
        for shape, lines, amounts, cash, wrote in OFFSET_SHAPES:
            co, collector, book_key, customer, docnums = _offset_seed(lines)
            try:
                frappe.set_user(collector)
                made = service.create_draft(co, customer, [{"docnum": d, "amount": a} for d, a in zip(docnums, amounts)], cash=cash)
                frappe.db.commit()
                doc = frappe.get_doc("ERP Collection", made["name"])
                book = {"key": doc.book_key, **service._book_row(doc)}
                plain = service._checked(doc.company, book, doc.customer, service._draft_of(doc), record=doc.name)
                frappe.db.rollback()
                locked = service._checked(doc.company, book, doc.customer, service._draft_of(doc), record=doc.name, locked=True)
                frappe.db.rollback()
                released = service.release(made["name"], made["version"], f"line:{TAG}-os-{index}-{shape}")
                frappe.db.commit()
                row = frappe.db.get_value("ERP Collection", made["name"], ["status", "job"], as_dict=True)
                job = jobs.find_by_name(row.job) if row.job else None
                payload = json.loads(frappe.db.get_value(jobs.JOB, row.job, "payload") or "{}") if row.job else {}
                want_invoices = [{"docnum": docnums[0], "amount": contracts.text(amounts[0])}]
                want_offsets = [{"docnum": docnums[1], "rectyp": "5", "amount": contracts.text(amounts[1])}]
                good = (plain == locked and released.get("status") == "released" and row.status == contracts.QUEUED and job
                        and payload.get("payload_version") == contracts.OFFSETS_PAYLOAD_VERSION
                        and [{k: item.get(k) for k in ("docnum", "amount")} for item in payload.get("invoices") or []] == want_invoices
                        and payload.get("offsets") == want_offsets and payload.get("cash") == wrote)
                tally(results, f"erp.collection_offset_{shape}", good,
                      {"same": plain == locked, "released": released.get("status"), "row": row,
                       "payload": {k: payload.get(k) for k in ("payload_version", "invoices", "offsets", "cash", "methods")}})
            finally:
                _collection_cleanup(collector, book_key, customer)


def _snapshot_book():
    """应收快照格的账套：实验栈当前公司按今天定的那本（同收款流程 `service.book_for`）。"""
    from pearnly_erp_portal.collection import service
    from pearnly_erp_portal.masters import company as companies

    frappe.set_user(erp_user())
    co = companies.active_company() or frappe.db.get_value("ERP Company", {}, "name")
    row = frappe.db.get_value("ERP Company", co, ["name", "company_name", "tax_id", "branch"], as_dict=True)
    return co, service.book_for(row, frappe.utils.nowdate())


def _snapshot_rows(book_key, docnum):
    return frappe.db.sql("select name, customer, net, remaining, read_at from `tabERP Receivable Snapshot`"
                         " where book_key = %s and docnum = %s", (book_key, docnum), as_dict=True)


def _finished_read(co, book, customer, items, read_at):
    """造一件电脑已读好的 ``express_ar_open``（请求行 done + 结果文件），并记进每分钟落库的发起记录。回请求号。"""
    from pearnly_core.cc_desktop import documents, requests, store
    from pearnly_erp_portal.collection import snapshot

    key = snapshot.request_key(book["key"], customer)
    made = requests.create(snapshot.KIND, key, snapshot._params(book, customer), book_key=book["key"], actor=erp_user())
    content = json.dumps({"read_at": read_at, "items": items}).encode()
    store.write(made["request"], 0, content)
    frappe.db.set_value(documents.REQUEST, made["request"], {"status": "done", "received_bytes": len(content),
                                                             "result_bytes": len(content), "meta_json": "{}"},
                        update_modified=False)
    snapshot._remember(key, co, book["key"], customer)
    frappe.db.commit()
    return made["request"]


def snapshot_double_land(site, rounds, results):
    """④ C1：同一张发票两路同时落进应收快照，读视图都早于对方提交。两种各半：
    (a) 同一件读好的读回，网页 / LINE / CC 当场落（`snapshot.request`）与每分钟落（`snapshot.advance`）同时到；
    (b) 同一位客户两份读回（先后两次读、晚到的是新的）同时 `snapshot.replace`（只按客户读：整本读会删掉这本账套别的行）。
    判：这张发票只有一行、是读取时刻新的那一份；(a) 另判请求行已删、结果文件已删。修前两行。"""
    from datetime import timedelta

    from pearnly_core.cc_desktop import documents, store
    from pearnly_erp_portal.collection import snapshot

    co, book = _snapshot_book()
    user = erp_user()
    for index in range(rounds):
        customer = ("CS" + secrets.token_hex(3)).upper()
        docnum = ("IV" + secrets.token_hex(3)).upper()
        early = frappe.utils.now_datetime().isoformat()
        line = {"docnum": docnum, "rectyp": "3", "docdat": frappe.utils.nowdate(), "customer": customer, "salesman": "",
                "department": "", "net": "1000.00", "received": "0.00"}
        try:
            if index % 2 == 0:
                name = _finished_read(co, book, customer, [{**line, "remaining": "1000.00"}], early)
                box = _threads(site, user, [
                    ("request", _stale_then(0.3, lambda: snapshot.request(co, book, customer))),
                    ("advance", _stale_then(0.3, snapshot.advance))])
                frappe.db.rollback()
                rows = _snapshot_rows(book["key"], docnum)
                left = bool(frappe.db.exists(documents.REQUEST, name)) or store.read(name) is not None
                good = len(rows) == 1 and not left
                detail = {"way": "request + advance", "rows": rows, "request_left": left, "box": box}
            else:
                late = (frappe.utils.now_datetime() + timedelta(seconds=5)).isoformat()
                box = _threads(site, user, [
                    ("earlier", _stale_then(0.3, lambda: snapshot.replace(co, book["key"], [{**line, "remaining": "1000.00"}],
                                                                          early, customer=customer))),
                    ("later", _stale_then(0.3, lambda: snapshot.replace(co, book["key"], [{**line, "remaining": "600.00"}],
                                                                        late, customer=customer)))])
                frappe.db.rollback()
                rows = _snapshot_rows(book["key"], docnum)
                good = len(rows) == 1 and str(rows[0].remaining) in ("600", "600.0", "600.00", "600.000000000")
                detail = {"way": "replace x2 (same customer)", "rows": rows, "box": box}
            leaked = {k: v["error"] for k, v in box.items() if "error" in v}
            tally(results, "erp.snapshot_double_land", good and not leaked, {**detail, "leaked": leaked})
        finally:
            frappe.db.rollback()
            frappe.db.delete("ERP Receivable Snapshot", {"book_key": book["key"], "customer": customer})
            frappe.db.commit()


def snapshot_balances_stale(site, rounds, results):
    """④ A2 #7：写后回执改余额（`snapshot.apply_balances`）在 CC 的事务里、读视图早就建了；另一路刚把这张的 net 从 1000 改成 1200
    并提交。判：已收按新 net 算（1200 - 700 = 500）。修前按读视图里的旧 net 算成 300。"""
    from decimal import Decimal

    from pearnly_erp_portal.collection import snapshot

    co, book = _snapshot_book()
    user = erp_user()
    for _ in range(rounds):
        customer = ("CB" + secrets.token_hex(3)).upper()
        docnum = ("IV" + secrets.token_hex(3)).upper()
        try:
            snapshot.replace(co, book["key"], [{"docnum": docnum, "rectyp": "3", "docdat": frappe.utils.nowdate(),
                                                "customer": customer, "net": "1000.00", "received": "0.00",
                                                "remaining": "1000.00"}], frappe.utils.now_datetime().isoformat(),
                             customer=customer)
            frappe.db.commit()
            read_view("ERP Receivable Snapshot")
            elsewhere(site, user, lambda: frappe.db.sql("update `tabERP Receivable Snapshot` set net = 1200 where"
                                                        " book_key = %s and docnum = %s", (book["key"], docnum)))
            snapshot.apply_balances(book["key"], {docnum: Decimal("700")})
            frappe.db.commit()
            rows = _snapshot_rows(book["key"], docnum)
            received = frappe.db.get_value("ERP Receivable Snapshot", rows[0].name, "received") if rows else None
            tally(results, "erp.snapshot_balances_stale", rows and Decimal(str(received)) == Decimal("500"),
                  {"rows": rows, "received": received})
        finally:
            frappe.db.rollback()
            frappe.db.delete("ERP Receivable Snapshot", {"book_key": book["key"], "customer": customer})
            frappe.db.commit()


def file_delete_rolled_back(site, rounds, results):
    """删 File 后事务被回滚（整笔重来、用尽回「请稍后重试」）：File 记录回来了，盘上那份也还在；删了并提交：两样都没了。
    修前 Frappe 在 on_trash 当场删盘，回滚后记录在、盘上没了（附件打不开）。"""
    frappe.set_user(erp_user())
    for _ in range(rounds):
        file = _private_file("FR")
        path = frappe.utils.get_site_path("private", "files", os.path.basename(file.file_url))
        frappe.delete_doc("File", file.name, ignore_permissions=True)
        frappe.db.rollback()
        kept = bool(frappe.db.exists("File", file.name)) and os.path.exists(path)
        frappe.delete_doc("File", file.name, ignore_permissions=True)
        frappe.db.commit()
        gone = not frappe.db.exists("File", file.name) and not os.path.exists(path)
        tally(results, "erp.file_delete_rolled_back", kept and gone, {"kept_after_rollback": kept, "gone_after_commit": gone})


def b2_pitfalls(site, rounds, results):
    """11-26 第一版「先锁后读」踩过的两个坑（附件 LinkValidationError、子表间隙锁 QueryDeadlockError）在 B ② 改过的每个入口上专门验：
    采购 / 销售同一张单同时到 付款 ×2、挂附件、删附件、补开凭证（采购）、旧接口付款 / 标已付（采购）；草稿同时删两遍 + 挂附件。
    判：任何一路都不许漏出这两种坑或 417 / 500 原文；业务拒绝（已删、已作废、超付）可以。附件删掉后盘上那份还在、提交后才删。"""
    from pearnly_erp_portal.masters import company as companies
    from pearnly_erp_portal.purchasing import api as purchase_api, suppliers
    from pearnly_erp_portal.sales import api as sales_api, entry as sales_entry

    user = erp_user()
    frappe.set_user(user)
    co = companies.active_company()
    frappe.db.set_value("ERP Company", co, "express_enabled", 0)
    supplier = frappe.db.get_value("ERP Supplier", {"supplier_name": f"{TAG} supplier"}) or \
        suppliers.create_supplier(supplier_name=f"{TAG} supplier")["name"]
    frappe.db.commit()
    sale = {"company": co, "doc_date": frappe.utils.nowdate(), "doc_kind": "receipt", "buyer_name": f"{TAG} buyer", "vat_rate": 0,
            "has_vat": 0, "lines": [{"item_type": "service", "description": f"{TAG} service", "qty": 1, "unit": "งาน",
                                     "unit_price": 1000}]}

    def pay(api, name, amount):
        payload = {"amount": amount, "method": "cash"}
        if api is purchase_api.api_purchase_payment:
            payload["request_id"] = f"{TAG}-pf-{secrets.token_hex(4)}"  # 销售收款没有请求号
        return lambda: api(name, json.dumps(payload))

    for _ in range(rounds):
        purchase = _purchase(co, supplier, "PF")
        sold = sales_entry.post(dict(sale), request_id=f"{TAG}-pfs-{secrets.token_hex(6)}")["name"]
        frappe.db.commit()
        pre = {"purchase": _private_file("PFa"), "sale": _private_file("PFs")}
        purchase_api.api_purchase_attachments(purchase, json.dumps([{"file": pre["purchase"].name, "kind": "bill"}]))
        sales_api.api_sale_attachments(sold, json.dumps([{"file": pre["sale"].name, "kind": "bill"}]))
        frappe.db.commit()
        fresh = {"purchase": _private_file("PFb"), "sale": _private_file("PFt"), "draft": _private_file("PFd")}
        draft = _purchase(co, supplier, "PFD")
        frappe.db.set_value("Purchase Document", draft, "status", "draft", update_modified=False)  # 识别后待确认的那种草稿
        frappe.db.commit()
        calls = [
            ("p.pay1", pay(purchase_api.api_purchase_payment, purchase, 100)),
            ("p.pay2", pay(purchase_api.api_purchase_payment, purchase, 100)),
            ("p.attach", lambda: purchase_api.api_purchase_attachments(purchase, json.dumps([{"file": fresh["purchase"].name,
                                                                                               "kind": "bill"}]))),
            ("p.remove", lambda: purchase_api.api_remove_purchase_attachment(purchase, pre["purchase"].name)),
            ("p.voucher", lambda: purchase_api.api_purchase_voucher(purchase, "substitute_receipt")),
            ("p.old_pay", lambda: purchase_api.api_pay_purchase(name=purchase, amount=50, method="cash")),
            ("s.pay1", pay(sales_api.api_sale_payment, sold, 100)),
            ("s.pay2", pay(sales_api.api_sale_payment, sold, 100)),
            ("s.attach", lambda: sales_api.api_sale_attachments(sold, json.dumps([{"file": fresh["sale"].name, "kind": "bill"}]))),
            ("s.remove", lambda: sales_api.api_remove_sale_attachment(sold, pre["sale"].name)),
        ]
        calls += [("d.delete1", lambda: purchase_api.api_delete_purchase_drafts(json.dumps([draft]))),
                      ("d.delete2", lambda: purchase_api.api_delete_purchase_drafts(json.dumps([draft]))),
                      ("d.attach", lambda: purchase_api.api_purchase_attachments(draft, json.dumps([{"file": fresh["draft"].name,
                                                                                                     "kind": "bill"}])))]
        box = _threads(site, user, calls)
        frappe.db.rollback()
        leaked = {k: v["error"] for k, v in box.items() if "error" in v and any(
            word in v["error"] for word in (*PITFALLS, "Document has been modified", "Deadlock", "1213"))}
        # 删掉的附件：File 记录没了、盘上那份也没了（提交后删）；没删掉的（被拒）两样都在
        disk = {}
        for key, file in pre.items():
            exists = bool(frappe.db.exists("File", file.name))
            path = frappe.utils.get_site_path("private", "files", os.path.basename(file.file_url))
            shared = bool(frappe.db.exists("File", {"file_url": file.file_url, "name": ["!=", file.name]}))
            disk[key] = (exists, os.path.exists(path), shared)
        consistent = all(record == on_disk or (on_disk and shared) for record, on_disk, shared in disk.values())
        tally(results, "erp.b2_pitfalls", not leaked and consistent,
              {"leaked": leaked, "disk": disk, "others": {k: v.get("error", "ok")[:120] for k, v in box.items()}})
        frappe.db.set_value("Purchase Document", purchase, "status", "void", update_modified=False)
        frappe.db.set_value("ERP Sales Document", sold, "status", "void", update_modified=False)
        frappe.db.commit()


def erp(site, rounds):
    results = {}
    guarded(results, "erp.sales_same_request", erp_sales_same_request, site, rounds, results)
    guarded(results, "core.operator_listed", operator_listed, site, rounds, results)
    frappe.set_user("Administrator")  # 看板权限：管理员能改所有看板
    guarded(results, "erp.void_with_children", void_with_children, site, rounds, results)
    guarded(results, "erp.stale_save_keeps_ledger", stale_save_keeps_ledger, site, rounds, results)
    guarded(results, "erp.same_doc_writes", same_doc_writes, site, rounds, results)
    guarded(results, "erp.amend_vs_payment_web", amend_vs_payment, site, rounds, results, True)
    guarded(results, "erp.amend_vs_payment_internal", amend_vs_payment, site, rounds, results, False)
    guarded(results, "erp.line_card_submit_vs_cancel", line_card_submit_vs_cancel, site, rounds, results)
    guarded(results, "erp.b2_pitfalls", b2_pitfalls, site, rounds, results)
    guarded(results, "erp.file_delete_rolled_back", file_delete_rolled_back, site, rounds, results)
    guarded(results, "erp.collection_edit_vs_release", collection_edit_vs_release, site, rounds, results)
    guarded(results, "erp.collection_double_release", collection_double_release, site, rounds, results)
    guarded(results, "erp.collection_double_offset", collection_double_offset, site, rounds, results)
    guarded(results, "erp.collection_same_customer_releases", collection_same_customer_releases, site, rounds, results)
    guarded(results, "erp.collection_same_customer_via_line", collection_same_customer_via_line, site, rounds, results)
    guarded(results, "erp.collection_offset_shapes", collection_offset_shapes, site, rounds, results)
    guarded(results, "erp.snapshot_double_land", snapshot_double_land, site, rounds, results)
    guarded(results, "erp.snapshot_balances_stale", snapshot_balances_stale, site, rounds, results)
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
    if "--only" in argv:
        ONLY.update(argv[argv.index("--only") + 1].split(","))
    connect(site)
    results = {"erp": erp, "billing": billing, "cowork": cowork}[product](site, rounds)
    ok = all(cell["bad"] == 0 for cell in results.values())
    print("RESULT " + json.dumps({"ok": ok, "rounds": rounds, "cells": results}, ensure_ascii=False, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
