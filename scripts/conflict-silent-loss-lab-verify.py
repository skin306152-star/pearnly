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
        way = detail.get("way") if isinstance(detail, dict) else None
        if way:  # 一格里有几种做法时，记下每种各错了几轮、各自第一轮的样子
            ways = cell.setdefault("bad_ways", {})
            ways.setdefault(way, {"bad": 0, "detail": detail})["bad"] += 1


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


_FACTS_BEFORE = {}  # 收款格改账套资料之前的原值（按账套键）：收尾照 `scripts/flows/collection_lib.restore` 还原


def _collection_seed():
    """收款格的数据（同 `scripts/flows/collection_lib.seed` 的做法）：收款员、账套收款类型、一位本格专用客户与一张未收清发票。
    改账套资料前记下原值（`_FACTS_BEFORE`），`_collection_cleanup` 还原：不留一份只有收款类型的资料给同一栈上别的流程
    （如 erp_express_void_while_sending 看到「已有资料」就不铺科目）。"""
    import copy

    from pearnly_erp_portal.collection import service, snapshot
    from pearnly_erp_portal.express import facts
    from pearnly_erp_portal.masters import company as companies

    frappe.set_user(erp_user())
    co = companies.active_company() or frappe.db.get_value("ERP Company", {}, "name")
    frappe.db.set_value("ERP Company", co, "express_enabled", 1)
    collector = erp_user()  # 实验栈的会计账号带「收款员」岗位
    row = frappe.db.get_value("ERP Company", co, ["name", "company_name", "tax_id", "branch"], as_dict=True)
    book = service.book_for(row, frappe.utils.nowdate())
    if book["key"] not in _FACTS_BEFORE:
        _FACTS_BEFORE[book["key"]] = copy.deepcopy(facts.load(book["key"]))
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
    if book_key in _FACTS_BEFORE:
        from pearnly_erp_portal.express import facts

        before = _FACTS_BEFORE.pop(book_key)
        if before is not None:
            facts.save(book_key, before)
        else:
            # 账套原来没读回过资料：还原成「没有」（同 collection_lib.restore）
            try:
                os.remove(facts._path(book_key))
            except FileNotFoundError:
                pass


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


def _drop_reads(book_key):
    """删掉这本账套的按需读取（读好的连结果文件一起取走）与发起记录。"""
    from pearnly_core.cc_desktop import documents, requests
    from pearnly_erp_portal.collection import snapshot

    for name in frappe.get_all(documents.REQUEST, filters={"book_key": book_key}, pluck="name"):
        if requests.take(name) is None:
            frappe.db.delete(documents.REQUEST, {"name": name})
    frappe.db.commit()
    frappe.cache.delete_value(snapshot.PENDING)


def snapshot_busy_book(site, rounds, results):
    """④ code-review 1：拿着这本账套快照串行锁的那路一直没提交（进程被杀，锁 120 秒才过期；本格在本进程里直接拿住同一把
    Redis 锁、不放，等锁时间改成 2 秒）。两种各半：
    (a) CC 交写后回执（共用机器通道 `cc_desktop.machine._run`，请求体换成本格的回执；回执里每张发票的余额要改快照）：
        判机器通道回「忙」（503，CC 退避后再交同一份），不是「请求无效」（400，CC 不重来、收款停在写入中）；锁放了再交一次记成已写入。
    (b) 网页 / LINE / CC 列这位客户的发票（`service.open_invoices`），整本那次与按客户那次都已读好：判只等一次锁（不是两次）、
        说「正在读取」、两件读回都还在（下一次问时再落）。修前 (a) 回 400，(b) 等两次。"""
    import time

    from pearnly_core.cc_desktop import http_auth, machine, products
    from pearnly_erp_portal.collection import contracts, service, snapshot

    wait, saved_wait, saved_request = 2, snapshot.SERIAL_WAIT, http_auth.current_request
    snapshot.SERIAL_WAIT = wait
    try:
        for index in range(rounds):
            co, collector, book_key, customer, docnum = _collection_seed()
            _drop_reads(book_key)  # 整本那件按账套只有一个请求号：先清掉别的流程留下的
            store = snapshot._state()
            stuck = store.lock(store.make_key(snapshot.SERIAL + book_key), timeout=60, blocking_timeout=1)
            try:
                frappe.set_user(collector)
                if index % 2 == 0:
                    made = service.create_draft(co, customer, [{"docnum": docnum, "amount": "400"}], cash="400")
                    service.release(made["name"], made["version"], f"line:{TAG}-busy-{index}")
                    frappe.db.commit()
                    job = frappe.db.get_value("ERP Collection", made["name"], "job")
                    view = {"job": job, "source_doctype": "ERP Collection", "source_name": made["name"], "status": "success",
                            "docnum": f"RE{secrets.randbelow(10 ** 7):07d}",
                            "receipt": {"invoices": [{"docnum": docnum, "remaining_after": "600.00"}]}}
                    http_auth.current_request = lambda operation: (None, {})
                    stuck.acquire()
                    answer = None
                    try:
                        machine._run("acknowledge", lambda context, body: products.job_changed(dict(view)))
                        answer = 200
                    except Exception as error:  # noqa: BLE001
                        answer = getattr(error, "http_status_code", type(error).__name__)
                    frappe.db.rollback()  # 同机器请求出错：整笔回滚
                    stuck.release()
                    frappe.set_user(collector)
                    machine._run("acknowledge", lambda context, body: products.job_changed(dict(view)))  # CC 下一轮再交
                    frappe.db.commit()
                    status = frappe.db.get_value("ERP Collection", made["name"], "status")
                    frappe.db.delete("Pearnly CC Job", {"name": job})  # 实验站没有电脑：这件不留在写账队列里
                    frappe.db.commit()
                    tally(results, "erp.snapshot_busy_book", answer == 503 and status == contracts.WRITTEN,
                          {"way": "write receipt", "answer": answer, "status": status})
                else:
                    co_row, book = _snapshot_book()
                    line = {"docnum": docnum, "rectyp": "3", "docdat": frappe.utils.nowdate(), "customer": customer,
                            "salesman": "", "department": "", "net": "1000.00", "received": "0.00", "remaining": "900.00"}
                    whole = _finished_read(co, book, "", [line], frappe.utils.now_datetime().isoformat())
                    mine = _finished_read(co, book, customer, [line], frappe.utils.now_datetime().isoformat())
                    frappe.set_user(collector)
                    stuck.acquire()
                    started = time.monotonic()
                    found = service.open_invoices(co, customer)
                    spent = time.monotonic() - started
                    frappe.db.commit()
                    stuck.release()
                    left = [bool(frappe.db.exists("Pearnly CC Request", name)) for name in (whole, mine)]
                    tally(results, "erp.snapshot_busy_book", found["refresh"] == snapshot.READING and spent < wait * 1.6
                          and all(left), {"way": "list invoices", "refresh": found["refresh"], "seconds": round(spent, 1),
                                          "reads_left": left})
            finally:
                http_auth.current_request = saved_request
                try:
                    stuck.release()
                except Exception:  # noqa: BLE001  已放
                    pass
                frappe.db.rollback()
                _drop_reads(book_key)
                _collection_cleanup(collector, book_key, customer)
    finally:
        snapshot.SERIAL_WAIT = saved_wait
        http_auth.current_request = saved_request


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


def _pdf_bytes():
    import io

    from pypdf import PdfWriter

    writer, buffer = PdfWriter(), io.BytesIO()
    writer.add_blank_page(72, 72)
    writer.add_metadata({"/Title": secrets.token_hex(16)})
    writer.write(buffer)
    return buffer.getvalue()


def line_intake_save_conflict(site, rounds, results, chat=False):
    """④ #12：LINE 收单识别任务（`integrations.intake.run`）在外部动作期间另一路改了这张卡并提交（关快照隔离后本连接的普通读停在
    改之前）。两种各半：识别期间改（原件已在）→ 记结果撞保存前比对；从 LINE 取件期间改 → 记原件撞保存前比对。识别用替身（不花钱），
    LINE 取件 / 回复都是替身。判：卡片成了待提交、结果在；识别只调 1 次（扣费参考号只用 1 次）、取件只 1 次、发 LINE 只 1 次；
    取件那种只留下记在卡上的那一份 File。修前：撞上就整笔抛出（执行器记 workflow_error、不重放），识别已付费、结果丢了。
    ``chat``：CC 对话收单（`intake.run_chat`，格 ``erp.cc_intake_save_conflict``）：原件收下时已存好，每轮都是识别期间改；判同上，
    不发 LINE。"""
    import hashlib
    import types

    from pearnly_core import line as core_line, line_binding
    from pearnly_core.execution import work
    from pearnly_erp_portal.integrations import intake, line_out, line_questions
    from pearnly_erp_portal.masters import company as companies

    user = erp_user()
    frappe.set_user(user)
    co = companies.active_company()
    frappe.db.commit()
    saved = {(module, name): getattr(module, name) for module, name in (
        (intake, "_recogniser"), (line_binding, "user_of"), (line_questions, "begin"), (line_questions, "send"),
        (line_out, "reply_or_push"), (core_line, "download_message_content"))}
    calls = {}

    def touch(name):
        """另一个请求改这张卡（随便一栏，修改时间跟着变）并提交。"""
        elsewhere(site, user, lambda: frappe.db.set_value(intake.DOCTYPE, name, "note", f"{TAG} touched {secrets.token_hex(3)}"))

    def recognise(files, **kw):
        calls["recognise"].append(kw.get("billing_reference"))
        if calls["variant"] == "recognise":
            touch(calls["card"])
        return {"doc_no": calls["doc_no"], "lines": []}

    def download(message_id, max_bytes=None):
        calls["download"] += 1
        if calls["variant"] == "fetch":
            touch(calls["card"])
        return calls["content"]

    intake._recogniser = lambda direction: types.SimpleNamespace(recognise=recognise)
    line_binding.user_of = lambda line_user_id: user
    line_questions.begin = lambda line_user_id, names: []
    line_questions.send = lambda line_user_id, messages, token="": calls["sent"].append(len(messages))
    line_out.reply_or_push = lambda *a, **kw: calls["sent"].append("text")
    core_line.download_message_content = download
    try:
        for index in range(rounds):
            variant = "fetch" if index % 2 and not chat else "recognise"
            source = None if variant == "fetch" else _private_file("LI")
            tag = f"{TAG}-LF-{secrets.token_hex(3)}"
            card = frappe.get_doc({"doctype": intake.DOCTYPE, "status": intake.QUEUED, "direction": "purchase", "sent_by": user,
                                   "owner": user, "line_user_id": "" if chat else "U" + "c" * 32, "source": "cc" if chat else "line", "company": co,
                                   "language": "th", "line_message_id": tag, "media_type": "pdf",
                                   "source_file_name": source.name if source else None,
                                   "source_file": source.file_url if source else None,
                                   "file_hash": hashlib.sha256(source.get_content()).hexdigest() if source else None,
                                   "received_at": frappe.utils.now_datetime()})
            card.insert(ignore_permissions=True)
            frappe.db.commit()
            calls.update(variant=variant, card=card.name, doc_no=f"{TAG}-{secrets.token_hex(3)}", recognise=[], download=0,
                         sent=[], content=_pdf_bytes())  # LINE 上的那份（每轮内容不同）
            job = f"{TAG}-work-{secrets.token_hex(6)}"
            outcome = error = None
            read_view(intake.DOCTYPE)  # 识别任务开跑前执行器刚提交过：读视图从任务里第一次读起
            try:
                with work.context(job):
                    outcome = (intake.run_chat(card.name) if chat
                               else intake.run(card.name, "U" + "c" * 32, filename=f"{tag}.pdf"))
            except Exception as exc:  # noqa: BLE001
                error = "".join(traceback.format_exception_only(type(exc), exc)).strip()[:200]
                frappe.db.rollback()
            frappe.db.rollback()
            row = frappe.db.get_value(intake.DOCTYPE, card.name, ["status", "result", "source_file_name"], as_dict=True)
            result = json.loads(row.result or "{}") if row.result else {}
            files = frappe.get_all("File", filters={"file_name": ["like", f"{tag}%"]}, pluck="name")
            kept = (row.source_file_name == (source.name if source else (files[0] if len(files) == 1 else "?"))
                    and bool(frappe.db.exists("File", row.source_file_name or "")))
            kind = intake.CC_OCR_KIND if chat else intake.OCR_KIND
            good = (outcome == ({"records": [card.name]} if chat else {}) and row.status == intake.READY
                    and result.get("doc_no") == calls["doc_no"] and calls["recognise"] == [kind + ":" + card.name]
                    and calls["sent"] == ([] if chat else [0]) and kept
                    and calls["download"] == (1 if variant == "fetch" else 0))
            tally(results, "erp.cc_intake_save_conflict" if chat else "erp.line_intake_save_conflict", good,
                  {"variant": variant, "outcome": outcome, "error": error, "status": row.status,
                   "doc_no": result.get("doc_no"), "recognise": len(calls["recognise"]), "download": calls["download"],
                   "sent": calls["sent"], "files": files, "source_file_name": row.source_file_name})
            frappe.db.delete("Pearnly OCR Checkpoint", {"work": job})
            for name in set(files + ([source.name] if source else [])):
                if frappe.db.exists("File", name):
                    frappe.delete_doc("File", name, ignore_permissions=True, force=True)
            frappe.db.delete(intake.DOCTYPE, {"name": card.name})
            frappe.db.commit()
    finally:
        for (module, name), value in saved.items():
            setattr(module, name, value)


def _mrerp_doubles(co, calls):
    """MR.ERP 导入直写（`mrerp.push.run`）在实验站上跑：实验站没有 MR.ERP 公司与连接，也不连真 MR.ERP。账本判定、准备、凭据、
    账号占用、主档整读换替身；导入（共用层 `mrerp.push.execute.execute`）换 ``calls["execute"]``。回 ``[(模块, 名字, 原值)]``。"""
    import pearnly_core.mrerp.push.execute as core_execute
    import pearnly_core.session_lock as session_lock
    from pearnly_erp_portal.mrerp import connection as connections, masters as mrerp_masters, push

    book = {"key": f"{TAG}:1", "connection": f"{TAG}-MRC"}
    saved = [(module, key, getattr(module, key)) for module, key in (
        (push, "handles"), (push, "prepare"), (connections, "lease"), (session_lock, "operator_holds"),
        (core_execute, "execute"), (mrerp_masters, "after_write"))]
    push.handles = lambda company: True
    push.prepare = lambda doctype, name, document: {"payload": {"histories": []}, "company": frappe._dict(name=co),
                                                    "direction": "purchase", "book": dict(book)}
    connections.lease = lambda name: {"username": "u", "password": "p", "system_url": "https://mrerp.invalid"}
    session_lock.operator_holds = lambda key: False
    core_execute.execute = lambda *args, **kwargs: calls["execute"](*args)
    mrerp_masters.after_write = lambda company: True
    return saved


def mrerp_conclusion_conflict(site, rounds, results):
    """④ A2 #16：MR.ERP 导入直写记结论撞冲突。导入替身返回「已写入 + 单号」，导入期间另一路改这张单（备注）并提交；记结论那笔事务
    写下单据行之后，另一路（先改了几张别的单、再锁着公司行）来改这张单，本连接接着锁公司行（代替同一笔事务里的后续写：待处理通知、
    代录回执）→ 真 1213，InnoDB 回滚较轻的本连接。判：单据记成已写入、单号在，导入只发 1 次。修前：1213 抛出任务、单据停在
    「正在导入」（15 分钟后被巡检转成结果不明，已写进 MR.ERP 的要人工核）。死锁时被回滚的是另一路的那几轮不算（竞争不真实）。"""
    import threading
    import time

    from pearnly_erp_portal.masters import company as companies
    from pearnly_erp_portal.mrerp import push
    from pearnly_erp_portal.purchasing import suppliers

    user = erp_user()
    frappe.set_user(user)
    co = companies.active_company()
    supplier = frappe.db.get_value("ERP Supplier", {"supplier_name": f"{TAG} supplier"}) or \
        suppliers.create_supplier(supplier_name=f"{TAG} supplier")["name"]
    frappe.db.commit()
    ballast = [_purchase(co, supplier, "MCB") for _ in range(8)]  # 另一路先改这几张：比本连接重，死锁时回滚本连接
    calls, state = {}, {}

    def execute(*args):
        calls["count"] += 1
        elsewhere(site, user, lambda: frappe.db.set_value(push.PURCHASE, state["name"], "notes", f"{TAG} {secrets.token_hex(3)}",
                                                          update_modified=False))
        return {"status": "success", "docnum": state["docnum"], "menus": {}, "masters": None}

    def other(locked, name):
        try:
            frappe.init(site=site, sites_path=SITES)
            frappe.connect()
            for row in ballast:
                frappe.db.sql("update `tabPurchase Document` set notes=%s where name=%s", (secrets.token_hex(4), row))
            frappe.db.sql("select name from `tabERP Company` where name=%s for update", (co,))
            locked.set()
            frappe.db.sql("update `tabPurchase Document` set notes=%s where name=%s", (f"{TAG} other", name))
            frappe.db.commit()
            state["other"] = "committed"
        except Exception as error:  # noqa: BLE001
            state["other"] = type(error).__name__
            frappe.db.rollback()
        finally:
            frappe.destroy()

    db = frappe.local.db
    real_set = db.set_value

    def hooked(doctype, name, field, *args, **kwargs):
        out = real_set(doctype, name, field, *args, **kwargs)
        if state.get("armed") and doctype == push.PURCHASE and name == state["name"] and isinstance(field, dict) \
                and field.get("ledger_state") == "written":
            state["armed"] = False
            locked = threading.Event()
            state["thread"] = threading.Thread(target=other, args=(locked, name))
            state["thread"].start()
            locked.wait(20)
            time.sleep(0.5)  # 另一路已在等这张单的行锁
            try:
                frappe.db.sql("select name from `tabERP Company` where name=%s for update", (co,))
                state["victim"] = "other"
            except Exception as error:  # noqa: BLE001
                state["victim"] = "main" if isinstance(error, frappe.QueryDeadlockError) else type(error).__name__
                raise
        return out

    saved = _mrerp_doubles(co, calls)
    calls["execute"] = execute
    db.set_value = hooked
    made, tried = [], 0
    try:
        while results.get("erp.mrerp_conclusion_conflict", {}).get("ok", 0) + \
                results.get("erp.mrerp_conclusion_conflict", {}).get("bad", 0) < rounds and tried < rounds * 3:
            tried += 1
            name = _purchase(co, supplier, "MC")
            made.append(name)
            frappe.db.set_value(push.PURCHASE, name, {"ledger_state": "queued", "ledger_reason": "", "ledger_target": "mrerp",
                                                      "ledger_job": None, "ledger_docnum": None}, update_modified=False)
            frappe.db.commit()
            calls["count"] = 0
            state.clear()
            state.update(name=name, docnum=f"{TAG}-PG{secrets.token_hex(3)}", armed=True)
            error = None
            try:
                push.run(push.PURCHASE, name, None)
            except Exception as exc:  # noqa: BLE001
                error = "".join(traceback.format_exception_only(type(exc), exc)).strip()[:200]
            frappe.db.rollback()
            if state.get("thread"):
                state["thread"].join(60)
            row = frappe.db.get_value(push.PURCHASE, name, ["ledger_state", "ledger_reason", "ledger_docnum", "notes"], as_dict=True)
            if state.get("victim") != "main":
                cell = results.setdefault("erp.mrerp_conclusion_conflict", {"ok": 0, "bad": 0})
                cell["not_real"] = cell.get("not_real", 0) + 1  # 死锁回滚的是另一路（或没撞上）：这一轮不算
                continue
            tally(results, "erp.mrerp_conclusion_conflict",
                  row.ledger_state == "written" and row.ledger_docnum == state["docnum"] and calls["count"] == 1 and not error,
                  {"error": error, "row": dict(row), "imports": calls["count"], "other": state.get("other")})
    finally:
        db.set_value = real_set
        for module, key, value in saved:
            setattr(module, key, value)
        frappe.db.rollback()
        for name in made + ballast:
            frappe.db.set_value(push.PURCHASE, name, {"ledger_state": None, "ledger_reason": "", "ledger_target": None,
                                                      "ledger_docnum": None, "ledger_sent_at": None}, update_modified=False)
        frappe.db.commit()


def mrerp_collection_conclusion_conflict(site, rounds, results):
    """④ 按类同 A2 #16：收款导入 MR.ERP（`collection.mrerp.run`）记结论撞冲突。导入替身（共用层 `mrerp.push.receipt.execute`）回
    「已写入 + FR 单号」；记结论那笔事务写下收款行之后，另一路（先改了几张别的单、再锁着公司行）来改这笔收款，本连接接着锁公司行
    → 真 1213，InnoDB 回滚较轻的本连接。判：这笔记成已写入、单号在，导入只发 1 次。修前：1213 抛出任务、这笔停在「写入中」
    （15 分钟后被巡检转成结果不明）。账本判定、准备、凭据、账号占用、写后按客户重读、告诉录入的人换替身；死锁回滚另一路的不算。"""
    import threading
    import time

    import pearnly_core.mrerp.push.receipt as core_receipt
    import pearnly_core.session_lock as session_lock
    from pearnly_erp_portal.collection import contracts, mrerp as collection_mrerp, push as collection_push, service
    from pearnly_erp_portal.mrerp import connection as connections
    from pearnly_erp_portal.purchasing import suppliers

    co, collector, book_key, customer, docnum = _collection_seed()
    frappe.set_user(erp_user())
    supplier = frappe.db.get_value("ERP Supplier", {"supplier_name": f"{TAG} supplier"}) or \
        suppliers.create_supplier(supplier_name=f"{TAG} supplier")["name"]
    frappe.db.commit()
    ballast = [_purchase(co, supplier, "MCCB") for _ in range(8)]  # 另一路先改这几张：比本连接重，死锁时回滚本连接
    calls, state = {"count": 0}, {}

    def execute(payload, target, secrets_, url):
        calls["count"] += 1
        return {"status": "success", "docnum": state["docnum"], "menus": {}, "masters": None}

    def other(locked, name):
        try:
            frappe.init(site=site, sites_path=SITES)
            frappe.connect()
            for row in ballast:
                frappe.db.sql("update `tabPurchase Document` set notes=%s where name=%s", (secrets.token_hex(4), row))
            frappe.db.sql("select name from `tabERP Company` where name=%s for update", (co,))
            locked.set()
            frappe.db.sql("update `tabERP Collection` set modified=modified where name=%s", (name,))
            frappe.db.sql("select name from `tabERP Collection` where name=%s for update", (name,))
            frappe.db.commit()
            state["other"] = "committed"
        except Exception as error:  # noqa: BLE001
            state["other"] = type(error).__name__
            frappe.db.rollback()
        finally:
            frappe.destroy()

    db = frappe.local.db
    real_set = db.set_value

    def hooked(doctype, name, field, *args, **kwargs):
        out = real_set(doctype, name, field, *args, **kwargs)
        if state.get("armed") and doctype == collection_mrerp.DOCTYPE and name == state["name"] and isinstance(field, dict) \
                and field.get("status") == contracts.WRITTEN:
            state["armed"] = False
            locked = threading.Event()
            state["thread"] = threading.Thread(target=other, args=(locked, name))
            state["thread"].start()
            locked.wait(20)
            time.sleep(0.5)  # 另一路已在等这笔收款的行锁
            try:
                frappe.db.sql("select name from `tabERP Company` where name=%s for update", (co,))
                state["victim"] = "other"
            except Exception as error:  # noqa: BLE001
                state["victim"] = "main" if isinstance(error, frappe.QueryDeadlockError) else type(error).__name__
                raise
        return out

    book = {"key": book_key, "connection": f"{TAG}-MRC"}
    saved = [(module, key, getattr(module, key)) for module, key in (
        (collection_mrerp, "handles"), (collection_mrerp, "prepare"), (collection_mrerp, "request"),
        (collection_push, "_tell"), (connections, "lease"), (session_lock, "operator_holds"), (core_receipt, "execute"))]
    handles = collection_mrerp.handles
    collection_mrerp.handles = lambda company: True
    collection_mrerp.prepare = lambda doc: {"payload": {"number": doc.name}, "book": dict(book)}
    collection_mrerp.request = lambda company, book, customer: "reading"
    collection_push._tell = lambda doc: None
    connections.lease = lambda name: {"username": "u", "password": "p", "system_url": "https://mrerp.invalid"}
    session_lock.operator_holds = lambda key: False
    core_receipt.execute = execute
    db.set_value = hooked
    made, tried = [], 0
    try:
        while results.get("erp.mrerp_collection_conclusion_conflict", {}).get("ok", 0) + \
                results.get("erp.mrerp_collection_conclusion_conflict", {}).get("bad", 0) < rounds and tried < rounds * 3:
            tried += 1
            frappe.set_user(collector)
            collection_mrerp.handles = handles  # 草稿照实验站原样建（公司接的是 Express 账套），领取起才按 MR.ERP 走
            name = service.create_draft(co, customer, [{"docnum": docnum, "amount": "100"}], cash="100")["name"]
            collection_mrerp.handles = lambda company: True
            made.append(name)
            frappe.db.set_value(collection_mrerp.DOCTYPE, name, {"status": contracts.QUEUED, "job": None, "reason": ""},
                                update_modified=False)
            frappe.db.commit()
            calls["count"] = 0
            state.clear()
            state.update(name=name, docnum=f"FR{TAG}{secrets.token_hex(3)}", armed=True)
            error = None
            try:
                collection_mrerp.run(name)
            except Exception as exc:  # noqa: BLE001
                error = "".join(traceback.format_exception_only(type(exc), exc)).strip()[:200]
            frappe.db.rollback()
            if state.get("thread"):
                state["thread"].join(60)
            row = frappe.db.get_value(collection_mrerp.DOCTYPE, name, ["status", "reason", "docnum"], as_dict=True)
            if state.get("victim") != "main":
                cell = results.setdefault("erp.mrerp_collection_conclusion_conflict", {"ok": 0, "bad": 0})
                cell["not_real"] = cell.get("not_real", 0) + 1  # 死锁回滚的是另一路（或没撞上）：这一轮不算
                cell.setdefault("not_real_detail", {"victim": state.get("victim"), "error": error, "row": dict(row or {})})
                continue
            tally(results, "erp.mrerp_collection_conclusion_conflict",
                  row.status == contracts.WRITTEN and row.docnum == state["docnum"] and calls["count"] == 1 and not error,
                  {"error": error, "row": dict(row), "imports": calls["count"], "other": state.get("other")})
    finally:
        db.set_value = real_set
        for module, key, value in saved:
            setattr(module, key, value)
        frappe.db.rollback()
        for name in made:  # 记成已写入的这几笔不占发票余额，测完改成已撤回，同一栈上的别的格看不到
            frappe.db.set_value(collection_mrerp.DOCTYPE, name, {"status": contracts.WITHDRAWN, "reason": ""},
                                update_modified=False)
        for name in ballast:
            frappe.db.set_value("Purchase Document", name, {"notes": None}, update_modified=False)
        frappe.db.commit()
        _collection_cleanup(collector, book_key, customer)


def mrerp_poll_stale(site, rounds, results):
    """④ 巡检（`mrerp.push.poll`）：每分钟巡检普通读列出「排队中、正在导入且超过 15 分钟」的单，此间导入刚把它记成已写入并提交；
    巡检接着按列出时的旧值改 → 修前盖成「结果不明」（已写进 MR.ERP、单号在，却要人工核）；修后加锁按现值复核、不动。
    本连接先建读视图（巡检这一轮开头列单），另一路写成已写入并提交，再跑巡检；巡检只看采购单（`DIRECTIONS` 换成只有采购）。"""
    from datetime import timedelta

    from pearnly_erp_portal.masters import company as companies
    from pearnly_erp_portal.mrerp import contracts, push
    from pearnly_erp_portal.purchasing import suppliers

    user = erp_user()
    frappe.set_user(user)
    co = companies.active_company()
    supplier = frappe.db.get_value("ERP Supplier", {"supplier_name": f"{TAG} supplier"}) or \
        suppliers.create_supplier(supplier_name=f"{TAG} supplier")["name"]
    frappe.db.commit()
    saved = [(push, "DIRECTIONS", push.DIRECTIONS), (push, "dispatch", push.dispatch)]
    push.DIRECTIONS = {push.PURCHASE: "purchase"}
    push.dispatch = lambda doctype, name: None
    made = []
    try:
        for _ in range(rounds):
            name = _purchase(co, supplier, "MP")
            made.append(name)
            frappe.db.set_value(push.PURCHASE, name, {"ledger_state": "queued", "ledger_reason": contracts.IMPORTING,
                                                      "ledger_target": "mrerp", "ledger_job": None, "ledger_docnum": None,
                                                      "ledger_sent_at": frappe.utils.now_datetime() - timedelta(minutes=20)},
                                update_modified=False)
            frappe.db.commit()
            docnum = f"{TAG}-PG{secrets.token_hex(3)}"
            read_view(push.PURCHASE)  # 巡检这一轮开头：读视图从这里起
            elsewhere(site, user, lambda: push._set(push.PURCHASE, name, {"ledger_state": "written", "ledger_reason": "",
                                                                          "ledger_docnum": docnum, "ledger_needed": None}))
            push.poll()
            frappe.db.rollback()
            row = frappe.db.get_value(push.PURCHASE, name, ["ledger_state", "ledger_reason", "ledger_docnum"], as_dict=True)
            tally(results, "erp.mrerp_poll_stale", row.ledger_state == "written" and row.ledger_docnum == docnum
                  and not row.ledger_reason, {"row": dict(row)})
    finally:
        for module, key, value in saved:
            setattr(module, key, value)
        frappe.db.rollback()
        for name in made:
            frappe.db.set_value(push.PURCHASE, name, {"ledger_state": None, "ledger_reason": "", "ledger_target": None,
                                                      "ledger_docnum": None, "ledger_sent_at": None}, update_modified=False)
        frappe.db.commit()


def stale_job_receipt(site, rounds, results):
    """④ 写账回执只认单据现在那件（`express.push.job_changed`，11-28）。每轮一张采购单，两段：
    A. 旧件 J1 已被电脑领走；本连接是电脑回报 J1 写成的那笔事务（读视图先建好），另一路把单据换成新件 J2（排上、单据记 J2）并提交；
       本连接接着记 J1 写成 → 修前盖成「已写入 + J1」（现在那件 J2 照样会被电脑领走、Express 里写第二张）；修后转人工（结果不明码）、
       J1 的单号留在单据上，J2 不动、领单前兜底不要它，再交不排第三件。
    B. 现在那件 J2 被领走，另一路把单据改成失败并提交；本连接记 J2 写成 → 修前照记已写入，修后转人工、J2 的单号记上；
       两张单号各在写账件表自己那一行。主档同步、挂对方档码换成空函数（只修前会走到）。"""
    from pearnly_core.cc_desktop import jobs
    from pearnly_erp_portal.express import contracts as E, masters, push
    from pearnly_erp_portal.masters import company as companies
    from pearnly_erp_portal.purchasing import suppliers

    moved = getattr(E, "MOVED_DURING_WRITE", "cc_desktop.result_unknown:moved_during_write")  # 修前树上还没有这个常量
    user = erp_user()
    frappe.set_user(user)
    co = companies.active_company()
    enabled = frappe.db.get_value("ERP Company", co, "express_enabled")
    frappe.db.set_value("ERP Company", co, "express_enabled", 0)  # 建单时不自动排写账（本格自己排件）
    supplier = frappe.db.get_value("ERP Supplier", {"supplier_name": f"{TAG} supplier"}) or \
        suppliers.create_supplier(supplier_name=f"{TAG} supplier")["name"]
    frappe.db.commit()
    saved = [(masters, "queue", masters.queue), (masters, "forget", masters.forget), (push, "link_party", push.link_party),
             (push, "link_products", push.link_products)]
    for module, key, _value in saved:
        setattr(module, key, lambda *args, **kwargs: None)
    made, made_jobs = [], []
    book = f"{TAG}-BK-{secrets.token_hex(3)}"

    def enqueue(name, key):
        job = jobs.enqueue(source_doctype=push.PURCHASE, source_name=name, request_key=key, actor=user, book_key=book,
                           payload={"payload_version": 4, "b3lab": True}, payload_version=4, label=f"{TAG}-stale")
        made_jobs.append(job["job"])
        return job["job"]

    def landed(job, docnum):
        # 电脑回报写成（同 `jobs.acknowledge` 记成功后交回产品）：本连接的事务里
        frappe.db.set_value(jobs.JOB, job, {"status": "success", "docnum": docnum, "lease_device": None,
                                            "lease_expires_at": None, "writing_key": None}, update_modified=False)
        push.job_changed(jobs.find_by_name(job))
        frappe.db.commit()

    try:
        for _ in range(rounds):
            name = _purchase(co, supplier, "SJ")
            made.append(name)
            ref = f"erp:purchase:{TAG}-{secrets.token_hex(4)}"
            first = enqueue(name, ref)
            frappe.db.set_value(jobs.JOB, first, {"status": "leased", "lease_device": f"{TAG}-pc"}, update_modified=False)
            frappe.db.set_value(push.PURCHASE, name, {"ledger_state": "queued", "ledger_job": first, "ledger_reason": "",
                                                      "ledger_docnum": None, "ledger_book": book, "ledger_target": None},
                                update_modified=False)
            frappe.db.commit()
            old_no, new_no = f"{TAG}-HPA{secrets.token_hex(2)}", f"{TAG}-HPB{secrets.token_hex(2)}"
            # A：旧件迟到写成
            read_view(push.PURCHASE)
            frappe.db.get_value(push.PURCHASE, name, "ledger_job")  # 回报那笔事务先读过单据

            def replace():
                second = enqueue(name, ref + "#2")
                frappe.db.set_value(push.PURCHASE, name, {"ledger_state": "queued", "ledger_job": second, "ledger_reason": ""},
                                    update_modified=False)
                return second
            second = elsewhere(site, user, replace)
            made_jobs.append(second)
            landed(first, old_no)
            row = frappe.db.get_value(push.PURCHASE, name, ["ledger_state", "ledger_reason", "ledger_docnum", "ledger_job"],
                                      as_dict=True)
            second_view = jobs.find_by_name(second)
            wanted = push.still_wanted(second_view)
            frappe.db.rollback()
            again = push.send(push.PURCHASE, name)
            frappe.db.rollback()
            count = len(jobs.of_source(push.PURCHASE, name))
            good_a = (row.ledger_state, row.ledger_reason, row.ledger_docnum, row.ledger_job) == (
                E.HELD, moved, old_no, first) and second_view["status"] == "queued" and not wanted \
                and (again or {}).get("status") != "sent" and count == 2
            # B：现在那件写成时单据已被改成别的状态
            frappe.db.set_value(jobs.JOB, second, {"status": "leased", "lease_device": f"{TAG}-pc"}, update_modified=False)
            frappe.db.set_value(push.PURCHASE, name, {"ledger_state": "queued", "ledger_job": second, "ledger_reason": "",
                                                      "ledger_docnum": None}, update_modified=False)
            frappe.db.commit()
            read_view(push.PURCHASE)
            frappe.db.get_value(push.PURCHASE, name, "ledger_state")
            elsewhere(site, user, lambda: frappe.db.set_value(push.PURCHASE, name, {"ledger_state": "failed",
                                                                                    "ledger_reason": "replaced"},
                                                              update_modified=False))
            landed(second, new_no)
            row_b = frappe.db.get_value(push.PURCHASE, name, ["ledger_state", "ledger_reason", "ledger_docnum", "ledger_job"],
                                        as_dict=True)
            numbers = {job["job"]: job["docnum"] for job in jobs.of_source(push.PURCHASE, name)}
            good_b = (row_b.ledger_state, row_b.ledger_reason, row_b.ledger_docnum, row_b.ledger_job) == (
                E.HELD, moved, new_no, second) and numbers == {first: old_no, second: new_no}
            tally(results, "erp.stale_job_receipt", good_a and good_b,
                  {"a": dict(row), "second": second_view["status"], "still_wanted": wanted, "send": again, "jobs": count,
                   "b": dict(row_b), "numbers": numbers})
    finally:
        for module, key, value in saved:
            setattr(module, key, value)
        frappe.db.rollback()
        for name in made:
            frappe.db.set_value(push.PURCHASE, name, {"status": "void", "ledger_state": None, "ledger_reason": "",
                                                      "ledger_job": None, "ledger_docnum": None, "ledger_book": None},
                                update_modified=False)
        if made_jobs:
            frappe.db.delete(jobs.JOB, {"name": ["in", made_jobs]})
        frappe.db.sql("delete from `tabError Log` where error like %s", (f"%{TAG}-HP%",))
        frappe.db.set_value("ERP Company", co, "express_enabled", enabled)
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
    guarded(results, "erp.line_intake_save_conflict", line_intake_save_conflict, site, rounds, results)
    guarded(results, "erp.cc_intake_save_conflict", line_intake_save_conflict, site, rounds, results, True)
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
    guarded(results, "erp.snapshot_busy_book", snapshot_busy_book, site, rounds, results)
    guarded(results, "erp.mrerp_conclusion_conflict", mrerp_conclusion_conflict, site, rounds, results)
    guarded(results, "erp.mrerp_poll_stale", mrerp_poll_stale, site, rounds, results)
    guarded(results, "erp.mrerp_collection_conclusion_conflict", mrerp_collection_conclusion_conflict, site, rounds, results)
    guarded(results, "erp.stale_job_receipt", stale_job_receipt, site, rounds, results)
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
        guarded(results, "cowork.folder_push_vs_human", folder_push_vs_human, site, rounds, results, workspace)
        guarded(results, "cowork.mrerp_ack_conflict", mrerp_ack_conflict, site, rounds, results, workspace)
        guarded(results, "cowork.recon_result_conflict", recon_result_conflict, site, rounds, results, workspace)
    finally:
        frappe.db.sql("delete from `tabCowork Company Automation` where company = %s", (company,))
        frappe.db.sql("delete from `tabCowork Push Record` where history = %s", (history,))
        frappe.db.sql("delete from `tabCowork Company` where name = %s", (company,))
        frappe.db.sql("delete from `tabCowork OCR History` where name = %s", (history,))
        frappe.db.commit()
    return results


class _Prepared(Exception):
    """无人值守的推送越过了「人推过没有」那道核、接着往下准备（真站上下一步就冻结目标、建这次的推送记录）。"""


def folder_push_vs_human(site, rounds, results, workspace):
    """④ A2 #1 / #3：无人值守的推送判完「没人改过、没推过」之后，另一路（人在网页）把同一张推往另一份账套并提交（推送记录
    + 票上的推送状态，`push_state.mirror` 原样走）；本连接接着推 → 只能有人推的那一条：推送服务锁住票后要核出人推过、回 human。
    两条路各半：文件夹定时推送（`folder_intake.push.push_one`，判完在取要选的项时人推）、自动推送扫描（`sweep.candidates` +
    `sweep.one`，判定那一步时人推）。实验站没有 Express 连接与目录：连接读取、请求号查找换成替身，越过重核后的第一步
    （`_document_block`）换成 `_Prepared`，算「会建第二条推送记录」；人推往另一份账套，是因为同一份账套的重复准备推送账本按
    单据键（加锁读）本来就挡住（`push.native.service.reserve`），这里测的是它挡不住的那种。"""
    from datetime import timedelta

    from pearnly_cowork.connections import service as connections
    from pearnly_cowork.folder_intake import follow
    from pearnly_cowork.folder_intake import push as folder_push
    from pearnly_cowork.history import push_state
    from pearnly_cowork.push.application import service as application
    from pearnly_cowork.push.automation import sweep
    from pearnly_cowork.push.native import service as ledger

    user = frappe.db.get_value("User", {"name": ["like", "acc.cowork@%"], "enabled": 1}, "name")
    connection = f"{TAG}-CONN-{secrets.token_hex(3)}"
    raw_row("Cowork ERP Connection", connection, {"workspace": workspace, "adapter": "express", "enabled": 1,
                                                    "record_revision": 1})
    frappe.db.commit()
    automation = {"class": "A", "tier": "L0", "trigger": "task", "reasons": []}
    human = {}

    def web_push():
        """另一路请求：人把这张推往另一份账套、已排队（推送记录 + 票上的状态），提交。"""
        history = human.pop("history", None)
        if not history:
            return
        record = f"{TAG}-PR-{secrets.token_hex(4)}"

        def push():
            raw_row("Cowork Push Record", record, {"history": history, "status": "pending", "workspace": workspace,
                                                   "client": "", "connection": connection, "account_key": "BOOK-B",
                                                   "request_digest": secrets.token_hex(16)})
            push_state.mirror(frappe._dict(name=record, history=history, status="pending", workspace=workspace, client="",
                                           connection=connection, account_key="BOOK-B", reason=""),
                              frappe.utils.now_datetime(), select=True)
        elsewhere(site, user, push)

    def judged(*args, **kwargs):
        web_push()  # 判完、接着要推的那一刻
        return {"assessed": True, "action": "push", "class": "A", "tier": "L0", "codes": "", "why": "", "proposal": None,
                "needed": [], "checked_at": sweep._now(), "account_set": "BOOK-A", "automation": dict(automation)}

    def choices(*args, **kwargs):
        web_push()
        return {"needed": [], "account_set": "BOOK-A"}

    def prepared(doc):
        raise _Prepared()

    loaded = frappe._dict(doc=frappe._dict(name=connection), revision=1, endpoint={"adapter": "express"})
    saved = [(push_state, "_changed"), (push_state, "_announce"), (connections, "read_for_push"),
             (ledger, "lookup_request"), (ledger, "has_writer"), (application, "_document_block"),
             (application, "assess"), (application, "choices_view"), (follow, "choose")]
    saved = [(module, key, getattr(module, key)) for module, key in saved]
    push_state._changed = push_state._announce = lambda *args, **kwargs: None
    connections.read_for_push = lambda *args, **kwargs: loaded
    ledger.lookup_request = lambda context, key: None
    ledger.has_writer = lambda *args, **kwargs: True
    application._document_block = prepared
    application.assess, application.choices_view = judged, choices
    follow.choose = lambda rule, history, device=None: None
    rule = frappe._dict(connection=connection, account_set_key="BOOK-A", posting_kind=None, defaults_json="{}",
                        reader_device="")
    made = []
    try:
        for index in range(rounds):
            path = "folder" if index % 2 == 0 else "sweep"
            history = f"{TAG}-H-{secrets.token_hex(4)}"
            made.append(history)
            raw_row("Cowork OCR History", history, {"workspace": workspace, "client": "", "record_version": 1,
                                                    "edit_count": 0, "intake_target": "express", "source": "manual",
                                                    "push_connection": connection, "push_account_set": "BOOK-A"})
            frappe.db.sql("update `tabCowork OCR History` set owner=%s, creation=%s where name=%s",
                          (user, frappe.utils.now_datetime() - timedelta(minutes=20), history))
            frappe.db.commit()
            human["history"] = history
            frappe.set_user(user)
            outcome = None
            try:
                read_view("Cowork OCR History")
                if path == "folder":
                    entry = frappe._dict(name=f"{TAG}-F-{secrets.token_hex(3)}", filename="bill.pdf", reader_device="")
                    outcome = folder_push.push_one(entry, rule, history)
                    good = outcome == {"status": "human"}
                else:
                    row = next((row for row in sweep.candidates() if row.name == history), None)
                    outcome = sweep.one(row) if row else "not_a_candidate"
                    good = outcome == "human"
            except _Prepared:
                outcome, good = "prepared_a_second_push", False
            finally:
                frappe.db.rollback()
                frappe.set_user("Administrator")
            records = frappe.db.count("Cowork Push Record", {"history": history})
            tally(results, "cowork.folder_push_vs_human", good and records == 1 and not human,
                  {"path": path, "outcome": outcome, "records": records, "human_pushed": not human})
            seen = results["cowork.folder_push_vs_human"].setdefault("paths", {}).setdefault(path, {})
            seen[str(outcome)] = seen.get(str(outcome), 0) + 1  # 两条路各自的结局，记录里分开写
            human.clear()
    finally:
        for module, key, value in saved:
            setattr(module, key, value)
        frappe.set_user("Administrator")
        frappe.db.rollback()
        for history in made:
            frappe.db.sql("delete from `tabCowork Push Record` where history = %s", (history,))
            frappe.db.sql("delete from `tabCowork OCR History` where name = %s", (history,))
        frappe.db.sql("delete from `tabCowork ERP Connection` where name = %s", (connection,))
        frappe.db.commit()


def mrerp_ack_conflict(site, rounds, results, workspace):
    """④ B6：Cowork MR.ERP 直写（`push.mrerp.runner.run`）导入之后记回执撞冲突。导入替身回「已写入 + 单号」；导入期间另一路（先改
    几张别的票、再锁着这条推送记录）等着来锁这张票，回执那笔事务已锁住连接与这张票、接着锁推送记录 → 真 1213，InnoDB 回滚较轻的
    回执那笔。判：记录记成已写入、单号在，写者锁放开，导入只发 1 次，接着排了同连接下一条；同一次尝试（同 token / fence）再收同样的
    回执回原记录（replayed，不报 stale）。修前：1213 抛出任务，记录停在「已领」、写者锁不放（等租约过期按结果不明转人工）。
    实验站没有 MR.ERP 连接与凭据：连接读取、凭据、账号占用、导入换替身；领取换替身（照 `ledger.lease` 落行：尝试、写者锁、记录改已领），
    回执走真的 `ledger.ack`。死锁时被回滚的是另一路的那几轮不算（竞争不真实）。"""
    import hashlib
    import threading
    import time
    from datetime import timedelta

    from pearnly_core.express_rules.push import common
    from pearnly_core.express_rules.targets.identity import normalize_express_account_key
    from pearnly_cowork.connections import service as connections
    from pearnly_cowork.history import push_state
    from pearnly_cowork.push.mrerp import runner
    from pearnly_cowork.push.native import service as ledger
    from pearnly_cowork.push.native.documents import persist

    user = frappe.db.get_value("User", {"name": ["like", "acc.cowork@%"], "enabled": 1}, "name") or "Administrator"
    connection = f"{TAG}-MRC-{secrets.token_hex(3)}"
    raw_row("Cowork ERP Connection", connection, {"workspace": workspace, "adapter": "mrerp", "enabled": 1,
                                                    "record_revision": 1})
    ballast = [f"{TAG}-HB-{secrets.token_hex(4)}" for _ in range(8)]  # 另一路先改这几张：比回执那笔重，死锁时回滚回执那笔
    for name in ballast:
        raw_row("Cowork OCR History", name, {"workspace": workspace, "client": "", "record_version": 1, "source": "manual"})
    frappe.db.commit()
    account = normalize_express_account_key("12:1")
    target = {"adapter": "mrerp", "account_set": "12:1"}
    state, made, lock_names = {}, [], set()
    loaded = frappe._dict(doc=frappe._dict(name=connection), revision=1, endpoint={"adapter": "mrerp",
                                                                                  "config": {"system_url": "https://mrerp.invalid"}})

    def lease(name, *, context, adapter, now=None, redeliver=False, writer=""):
        with ledger.atomic():
            locks = ledger._locks(context, target)
            row = ledger._record(name, context)
            timestamp = ledger.now_utc()
            token, fence = secrets.token_urlsafe(32), int(row.fence or 0) + 1
            attempt = ledger._new(ledger.ATTEMPT, f"CWPA-{TAG}-{secrets.token_hex(8)}", push_record=row.name,
                                  workspace=row.workspace, client="", connection=row.connection, fence=fence,
                                  owner_id=adapter.owner, token_digest=hashlib.sha256(token.encode()).hexdigest(),
                                  binding_generation=row.binding_generation, started_at=timestamp,
                                  lease_expires_at=timestamp + timedelta(seconds=ledger.LEASE_SECONDS), status="leased")
            persist(attempt)
            for lock in locks:
                lock.holder, lock.holder_fence, lock.lease_expires_at = row.name, fence, attempt.lease_expires_at
                persist(lock)
            row.status, row.fence, row.active_attempt = "leased", fence, attempt.name
            row.lease_owner, row.lease_expires_at = adapter.owner, attempt.lease_expires_at
            ledger._save_record(row, timestamp)
        state.update(fence=fence, token=token, context=context, adapter=adapter)
        lock_names.update(lock.name for lock in locks)
        return {"leased": True, "fence": fence, "attempt_token": token, "payload": {"histories": []}}

    def other(locked, record, history):
        try:
            frappe.init(site=site, sites_path=SITES)
            frappe.connect()
            for name in ballast:
                frappe.db.sql("update `tabCowork OCR History` set last_push_reason=%s where name=%s", (secrets.token_hex(4), name))
            frappe.db.sql("select name from `tabCowork Push Record` where name=%s for update", (record,))
            locked.set()
            time.sleep(1.0)  # 回执那笔这时已锁住连接与这张票、在等这条推送记录
            frappe.db.sql("select name from `tabCowork OCR History` where name=%s for update", (history,))
            frappe.db.commit()
            state["other"] = "committed"
        except Exception as error:  # noqa: BLE001
            state["other"] = type(error).__name__
            frappe.db.rollback()
        finally:
            frappe.destroy()

    def execute(payload, frozen, secrets_, system_url, today):
        state["imports"] += 1
        locked = threading.Event()
        state["thread"] = threading.Thread(target=other, args=(locked, state["record"], state["history"]))
        state["thread"].start()
        locked.wait(20)
        return {"status": "success", "docnum": state["docnum"], "meta": {}}

    real_ack = ledger.ack

    def ack(*args, **kwargs):
        state["acks"] += 1
        return real_ack(*args, **kwargs)

    saved = [(module, key, getattr(module, key)) for module, key in (
        (connections, "read_for_push"), (connections, "credentials_for_use"), (runner, "operator_holds"),
        (runner, "execute"), (ledger, "lease"), (ledger, "ack"), (runner, "dispatch_next"),
        (push_state, "_changed"), (push_state, "_announce"))]
    connections.read_for_push = lambda *args, **kwargs: loaded
    connections.credentials_for_use = lambda value: {"username": "u", "password": "p"}
    runner.operator_holds = lambda key: False
    runner.execute, ledger.lease, ledger.ack = execute, lease, ack
    runner.dispatch_next = lambda name: state["next"].append(name)
    push_state._changed = push_state._announce = lambda *args, **kwargs: None
    tried = 0
    try:
        while results.get("cowork.mrerp_ack_conflict", {}).get("ok", 0) + \
                results.get("cowork.mrerp_ack_conflict", {}).get("bad", 0) < rounds and tried < rounds * 3:
            tried += 1
            history, record = f"{TAG}-H-{secrets.token_hex(4)}", f"{TAG}-PR-{secrets.token_hex(4)}"
            made.append((history, record))
            raw_row("Cowork OCR History", history, {"workspace": workspace, "client": "", "record_version": 1, "source": "manual"})
            raw_row("Cowork Push Record", record, {
                "record_id": record, "status": "pending", "workspace": workspace, "client": "", "actor": user, "history": history,
                "history_version": 1, "connection": connection, "account_key": account, "branch": "legacy",
                "binding_generation": 0, "connection_revision": 1, "target_json": json.dumps(target),
                "payload_json": json.dumps({"histories": []}), "fence": 0, "wire_attempt": 0, "failure_count": 0,
                "result_unknown": 0, "revision": 0, "document_key": f"{TAG}-dk-{secrets.token_hex(6)}",
                "request_digest": secrets.token_hex(16), "request_fingerprint": secrets.token_hex(16), "checks_json": "[]",
                "created_at": frappe.utils.now_datetime(), "updated_at": frappe.utils.now_datetime()})
            frappe.db.commit()
            state.clear()
            state.update(history=history, record=record, docnum=f"{TAG}-PG{secrets.token_hex(3)}", imports=0, acks=0, next=[])
            error = None
            try:
                runner.run(record)
            except Exception as exc:  # noqa: BLE001
                error = exc
            frappe.db.rollback()
            frappe.set_user("Administrator")
            if state.get("thread"):
                state["thread"].join(60)
            conflicted = isinstance(error, frappe.QueryDeadlockError) or state["acks"] >= 2
            if not conflicted:
                cell = results.setdefault("cowork.mrerp_ack_conflict", {"ok": 0, "bad": 0})
                cell["not_real"] = cell.get("not_real", 0) + 1  # 死锁回滚的是另一路（或没撞上）：这一轮不算
                cell.setdefault("not_real_detail", {"other": state.get("other"), "acks": state["acks"],
                                                    "error": repr(error)[:160] if error else None})
                continue
            row = frappe.db.get_value("Cowork Push Record", record, ["status", "external_docnum", "result_unknown"], as_dict=True)
            held = frappe.db.count("Cowork Push Writer Lock", {"holder": record})
            replay = None
            if row.status == "success":
                frappe.set_user(user)
                try:
                    replay = real_ack(record, context=state["context"], adapter=state["adapter"], fence=state["fence"],
                                      attempt_token=state["token"], success=True, outcome=common.STAGE_SUCCESS,
                                      express_docnum=state["docnum"], error=None, meta={}).get("replayed")
                except Exception as exc:  # noqa: BLE001
                    replay = type(exc).__name__ + ": " + str(exc)[:80]
                frappe.db.rollback()
                frappe.set_user("Administrator")
            tally(results, "cowork.mrerp_ack_conflict",
                  row.status == "success" and row.external_docnum == state["docnum"] and not held and state["imports"] == 1
                  and state["next"] == [connection] and replay is True,
                  {"error": repr(error)[:160] if error else None, "row": dict(row), "held_locks": held,
                   "imports": state["imports"], "acks": state["acks"], "next": state["next"], "replay": replay,
                   "other": state.get("other")})
    finally:
        for module, key, value in saved:
            setattr(module, key, value)
        frappe.set_user("Administrator")
        frappe.db.rollback()
        for history, record in made:
            frappe.db.sql("delete from `tabCowork Push Attempt` where push_record = %s", (record,))
            frappe.db.sql("delete from `tabCowork Push Record` where name = %s", (record,))
            frappe.db.sql("delete from `tabCowork OCR History` where name = %s", (history,))
        for name in lock_names:  # 这条测试连接的写者锁（领取替身经 `ledger._locks` 建的）
            frappe.db.sql("delete from `tabCowork Push Writer Lock` where name = %s", (name,))
        for name in ballast:
            frappe.db.sql("delete from `tabCowork OCR History` where name = %s", (name,))
        frappe.db.sql("delete from `tabCowork ERP Connection` where name = %s", (connection,))
        frappe.db.commit()


# 收入对账的两份输入（合成数据，同 cowork-income-recon-lab-verify.py）：替身读完直接回这两份表格
RECON_GL_SHEETS = [[
    ["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต", "รหัสบัญชี"],
    ["01/06/2569", "GL-1", "sale", "0.00", "1,070.00", "4110-01"],
    ["01/06/2569", "GL-1", "sale 2", "0.00", "30.00", "4110-01"],
    ["02/06/2569", "GL-2", "credit note", "107.00", "0.00", "4110-01"],
    ["03/06/2569", "GL-3", "no vat row", "0.00", "500.00", "4110-02"],
]]
RECON_VAT_CELLS = [
    ["วันที่", "เลขที่ใบกำกับ", "ชื่อผู้ซื้อ", "เลขประจำตัวผู้เสียภาษี", "สาขา", "มูลค่า", "ภาษี", "ยอดรวม"],
    ["01/06/2569", "GL-1", "ACME", "1234567890123", "00000", "1,100.00", "77.00", "1,177.00"],
    ["02/06/2569", "GL-2", "ACME", "1234567890123", "00000", "(107.00)", "(7.49)", "0.00"],
    ["05/06/2569", "INV-9", "BETA", "", "00000", "250.00", "17.50", "267.50"],
]


def recon_result_conflict(site, rounds, results, workspace):
    """④ B7：Cowork 收入对账后台任务（`recon_center.jobs.run` → `_run`）出结果撞冲突。两份源文件是真的私有 File；识别换替身
    （按「对账号 + 哪一份」记检查点：同一对账号再读取回已存结果、不再付费，同 `document_ocr.reader` 按对账号定识别任务名），
    读出的总账 / 税报直接回合成表格；出结果走真的 `income_recon_service.create_from_parsed`。写结果行之前另一路（先改几张别的
    票、再锁住结果行要落的主键空隙）去锁刚插入的任务行 → 结果行一插入就是真 1213，InnoDB 回滚较轻的出结果那笔。
    判：这一轮挂起（记下的等待参数里冲突次数 = 1）、对账没判失败、两份源文件都在；按记下的参数再跑一轮（同每分钟的 tick 放回
    队列）出了结果（任务、结果行都在），源文件这才删掉，识别全程只付 2 次（两份各一次）。修前：判「系统繁忙」失败、源文件当场
    删掉，用户只能重交 → 新对账号 → 识别再付一次。死锁时被回滚的是另一路的那几轮不算（竞争不真实）。名额（slots）换替身；
    记下等待用真的 `waiting.park`（队列 Redis），记下后马上取走（`waiting.drop`），不让实验站的每分钟 tick 把它放回真队列。"""
    import threading
    import time

    from pearnly_cowork.recon_center import document_ocr, jobs, slots, sources, waiting
    from pearnly_cowork.reconciliation import income_recon_service

    task_doctype, row_doctype, cell_name = "Cowork Income Reconciliation", "Cowork Income Reconciliation Row", \
        "cowork.recon_result_conflict"
    ballast = [f"{TAG}-RB-{secrets.token_hex(4)}" for _ in range(8)]  # 另一路先改这几张：比出结果那笔重，死锁时回滚出结果那笔
    for name in ballast:
        raw_row("Cowork OCR History", name, {"workspace": workspace, "client": "", "record_version": 1, "source": "manual"})
    frappe.db.commit()
    state, made = {}, []

    def reader(ocr_job, side, file_name, user):
        def read(document_type, content, filename):
            key = (ocr_job, side, document_type)
            if key not in state["checkpoints"]:
                state["checkpoints"].add(key)
                state["paid"] += 1
            return None  # 替身读完由下面直接回表格
        return read

    def ledger_source(content, filename, read=None):
        read("general_ledger", content, filename)
        return {"kind": "excel", "sheets": RECON_GL_SHEETS}

    def report_source(content, filename, read=None):
        read("vat_report", content, filename)
        return {"kind": "excel", "cells": RECON_VAT_CELLS}

    def other(locked, task_name):
        try:
            frappe.init(site=site, sites_path=SITES)
            frappe.connect()
            for name in ballast:
                frappe.db.sql("update `tabCowork OCR History` set last_push_reason=%s where name=%s", (secrets.token_hex(4), name))
            frappe.db.sql(f"select name from `tab{row_doctype}` where name=%s for update", (f"{task_name}-1",))  # 主键空隙锁
            locked.set()
            frappe.db.sql(f"select name from `tab{task_doctype}` where name=%s for update", (task_name,))  # 等出结果那笔刚插的任务行
            frappe.db.commit()
            state["other"] = "committed"
        except Exception as error:  # noqa: BLE001
            state["other"] = type(error).__name__
            frappe.db.rollback()
        finally:
            frappe.destroy()

    real_rows, real_park = income_recon_service._persist_rows, waiting.park

    def persist_rows(task, rows):
        if state.pop("collide", False):
            locked = threading.Event()
            state["thread"] = threading.Thread(target=other, args=(locked, task.name))
            state["thread"].start()
            locked.wait(20)
            time.sleep(1.0)  # 另一路这时已锁住结果行的空隙、在等任务行
        try:
            return real_rows(task, rows)
        except frappe.QueryDeadlockError:
            state["deadlock"] = True
            raise

    def park(recon_job, arguments, pending):
        outcome = real_park(recon_job, arguments, pending)
        state["parked"] = waiting.record(recon_job)
        waiting.drop(recon_job)  # 不让实验站的 tick 放回真队列：这里自己按记下的参数再跑一轮
        state["outcomes"].append(outcome)
        return "parked"

    saved = [(module, key, getattr(module, key)) for module, key in (
        (document_ocr, "reader"), (sources, "ledger_source"), (sources, "report_source"), (slots, "acquire"),
        (slots, "release"), (income_recon_service, "_persist_rows"), (waiting, "park"))]
    document_ocr.reader, sources.ledger_source, sources.report_source = reader, ledger_source, report_source
    slots.acquire, slots.release = (lambda recon_job, arguments: True), (lambda recon_job: None)
    income_recon_service._persist_rows, waiting.park = persist_rows, park
    tried = 0
    try:
        while results.get(cell_name, {}).get("ok", 0) + results.get(cell_name, {}).get("bad", 0) < rounds and tried < rounds * 3:
            tried += 1
            recon_job = secrets.token_hex(16)
            files = {"left": [_private_file("recon-gl").name], "right": [_private_file("recon-vat").name]}
            made.append((recon_job, files))
            state.clear()
            state.update(checkpoints=set(), paid=0, outcomes=[], collide=True)
            arguments = {"recon_job": recon_job, "kind": "income", "workspace": workspace, "title": f"{TAG} recon",
                         "files": files, "options": json.dumps({"lang": "zh"})}
            frappe.set_user("Administrator")
            error = None
            try:
                jobs.run(**arguments)
            except Exception as exc:  # noqa: BLE001
                error = exc
            frappe.db.rollback()
            if state.get("thread"):
                state["thread"].join(60)
            if not state.get("deadlock"):
                cell = results.setdefault(cell_name, {"ok": 0, "bad": 0})
                cell["not_real"] = cell.get("not_real", 0) + 1  # 死锁回滚的是另一路（或没撞上）：这一轮不算
                cell.setdefault("not_real_detail", {"other": state.get("other"), "error": repr(error)[:160] if error else None})
                continue
            first = jobs.pearnly_state.store().get_value(jobs._key(recon_job)) or {}
            kept = sum(1 for side in ("left", "right") for name in files[side] if frappe.db.exists("File", name))
            parked = state.get("parked") or {}
            retries = json.loads((parked.get("arguments") or {}).get("options") or "{}").get("conflict_retries")
            paid_first = state["paid"]
            second = None
            if parked:
                frappe.set_user(parked.get("user") or "Administrator")
                try:
                    jobs.run(**parked["arguments"])  # 同 tick → wake → requeue：按记下的参数再跑一轮
                except Exception as exc:  # noqa: BLE001
                    error = exc
                frappe.db.rollback()
                frappe.set_user("Administrator")
                second = jobs.pearnly_state.store().get_value(jobs._key(recon_job)) or {}
            task = (second or {}).get("task")
            rows = frappe.db.count(row_doctype, {"task": task}) if task else 0
            left = sum(1 for side in ("left", "right") for name in files[side] if frappe.db.exists("File", name))
            tally(results, cell_name,
                  error is None and first.get("state") != "failed" and kept == 2 and retries == 1 and paid_first == 2
                  and (second or {}).get("state") == "done" and bool(task) and rows > 0 and left == 0 and state["paid"] == 2,
                  {"error": repr(error)[:160] if error else None, "first": {k: first.get(k) for k in ("state", "stage", "error")},
                   "kept_after_conflict": kept, "retries": retries, "outcomes": state["outcomes"],
                   "second": {k: (second or {}).get(k) for k in ("state", "task", "error")}, "rows": rows, "files_left": left,
                   "paid": state["paid"], "other": state.get("other")})
    finally:
        for module, key, value in saved:
            setattr(module, key, value)
        frappe.set_user("Administrator")
        frappe.db.rollback()
        for recon_job, files in made:
            waiting.finish(recon_job)
            name = income_recon_service.TASKS.identity(recon_job)[0]
            frappe.db.sql(f"delete from `tab{row_doctype}` where task = %s", (name,))
            frappe.db.sql(f"delete from `tab{task_doctype}` where name = %s", (name,))
            for side in ("left", "right"):
                for file in files[side]:
                    if frappe.db.exists("File", file):
                        frappe.delete_doc("File", file, ignore_permissions=True, force=True)
        for name in ballast:
            frappe.db.sql("delete from `tabCowork OCR History` where name = %s", (name,))
        frappe.db.commit()


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
