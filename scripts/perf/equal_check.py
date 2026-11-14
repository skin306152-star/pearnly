"""改取数方式的对照：同一份数据上，旧版模块（改前的源码文件）与当前模块的返回值逐字段相等（只在任务自有的回归实验室跑）。

性能体检第 3 步（逐行查询改成按名单一次取）要求「行为不变」：旧源码按原模块名另载一份（它 import 的其它模块照常用当前的），
每个读接口用同样参数各调一次，结果 JSON 化后逐字段比；写接口（录入后重算合计）各跑一次后读回受影响的行比，然后回滚。

    ../env/bin/python /workspace/scripts/perf/equal_check.py <站点> <对照组> <旧源码目录>
对照组：erp-count（ERP 盘点）、erp-team / cowork-team（团队名单）。旧源码目录里放改前的同名文件（如 count.py、count_state.py）。末行 `EQUAL {json}`，不相等的列出第一处差别。
"""
import importlib.util
import json
import os
import sys

if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
    raise SystemExit("Requires the task-owned regression laboratory")

import frappe  # noqa: E402

ADMIN = "admin@erp.regression"


def load_old(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name + "__old", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canon(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def first_diff(a, b, path="$"):
    if type(a) is not type(b):
        return f"{path}: {a!r} != {b!r}"
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                return f"{path}.{key}: 只在一边"
            found = first_diff(a[key], b[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: 长度 {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            found = first_diff(x, y, f"{path}[{i}]")
            if found:
                return found
        return None
    return None if a == b else f"{path}: {a!r} != {b!r}"


def call(fn, kwargs):
    """返回值，或抛出的错误类型与文字（两边要抛同样的错）。"""
    try:
        return canon(fn(**kwargs))
    except Exception as error:  # noqa: BLE001
        return {"raised": type(error).__name__, "message": str(error)[:200]}
    finally:
        frappe.db.rollback()


def erp_count(old_dir):
    from pearnly_erp_portal.inventory import count as new, count_state as new_state

    old = load_old("pearnly_erp_portal.inventory.count", os.path.join(old_dir, "count.py"))
    old_state = load_old("pearnly_erp_portal.inventory.count_state", os.path.join(old_dir, "count_state.py"))
    counts = frappe.get_all("ERP Stock Count", filters={"item_total": [">", 0]}, pluck="name", order_by="creation asc",
                            limit_page_length=20)
    cases = []
    for name in counts:
        task = frappe.db.get_value("ERP Stock Count", name, ["warehouse", "status"], as_dict=True)
        items = frappe.get_all("ERP Stock Count Item", filters={"count": name}, fields=["name", "product", "code"],
                               order_by="name asc", limit_page_length=3)
        cases += [("review_count", {"name": name}), ("get_count", {"name": name}),
                  ("template_rows", {"name": name}), ("list_entries", {"count": name, "limit": 0})]
        for flt_ in ("all", "uncounted", "counted", "differences", "locations"):
            cases.append(("list_items", {"count": name, "limit": 0, "filter": flt_}))
        cases.append(("list_items", {"count": name, "limit": 20, "start": 5, "q": "1"}))
        for item in items:
            cases.append(("lookup_count", {"count": name, "product": item.product}))
            if item.code:
                cases.append(("lookup_count", {"count": name, "code": item.code}))
            cases.append(("recorded_entries", {"count": name, "items": [item.name]}))
            cases.append(("elsewhere_positions", {"product_id": item.product, "here": {(task.warehouse, "")}}))
            cases.append(("list_entries", {"count": name, "item": item.name, "limit": 0}))
        cases.append(("template_rows", {"warehouse": task.warehouse}))
    cases += [("list_counts", {"limit": 50}), ("list_counts", {"status": "Active"})]
    results = []
    for method, kwargs in cases:
        a, b = (call(getattr(module, method), kwargs) for module in (old, new))
        results.append({"case": method, "kwargs": canon({k: (sorted(v) if isinstance(v, set) else v) for k, v in kwargs.items()}),
                        "diff": first_diff(a, b), "raised": isinstance(a, dict) and "raised" in a})
    # 库存令牌：批量与逐个逐字相等（所有盘点里的物料 + 一个不存在的物料）
    products = sorted(set(frappe.get_all("ERP Stock Count Item", pluck="product", limit_page_length=0)) | {"PRD-NOT-THERE"})
    batch = new_state.stock_tokens(products)
    bad = [p for p in products if batch[p] != old_state.stock_token(p)]
    results.append({"case": "stock_tokens", "kwargs": {"products": len(products)}, "diff": f"{len(bad)} 个不等：{bad[:3]}" if bad else None})
    # 录入后重算：旧、新各跑一次，读回整份盘点的行与表头比
    for name in counts:
        def snapshot():
            return canon([frappe.get_all("ERP Stock Count Item", filters={"count": name}, fields=["name", "difference"],
                                         order_by="name asc", limit_page_length=0),
                          frappe.db.get_value("ERP Stock Count", name, ["item_total", "counted_total", "difference_total"],
                                              as_dict=True)])
        frappe.db.sql("update `tabERP Stock Count Item` set difference = difference + 1 where `count`=%s and mod(crc32(name), 3)=0", name)
        old._refresh_totals(name)
        a = snapshot()
        frappe.db.rollback()
        frappe.db.sql("update `tabERP Stock Count Item` set difference = difference + 1 where `count`=%s and mod(crc32(name), 3)=0", name)
        new._refresh_totals(name)
        b = snapshot()
        frappe.db.rollback()
        results.append({"case": "_refresh_totals", "kwargs": {"count": name}, "diff": first_diff(a, b)})
    return results


def team(app, user, old_dir):
    """团队名单：管理员看、受限的管理者看（各岗位账号）都比。"""
    new = importlib.import_module(f"{app}.team.service")
    old = load_old(f"{app}.team.service", os.path.join(old_dir, "service.py"))
    viewers = [user] + frappe.get_all("User", filters={"name": ["like", f"%@{user.split('@')[1]}"], "enabled": 1},
                                      pluck="name", order_by="name asc", limit_page_length=20)
    results = []
    for viewer in dict.fromkeys(viewers):
        frappe.set_user(viewer)
        a, b = (call(module.list_members, {}) for module in (old, new))
        results.append({"case": "list_members", "kwargs": {"as": viewer}, "diff": first_diff(a, b),
                        "raised": isinstance(a, dict) and "raised" in a})
    if app == "pearnly_erp_portal":
        # 受限的管理者（只开团队菜单、只管 A 公司）：走「按公司判管理范围」那条路
        from pearnly_erp_portal.access import menu

        def as_delegate(module):
            def run():
                frappe.set_user("Administrator")
                frappe.get_doc("User", "stock@erp.regression").add_roles(menu.role("team"))
                frappe.set_user("stock@erp.regression")
                return module.list_members()
            return run

        a, b = (call(as_delegate(module), {}) for module in (old, new))
        results.append({"case": "list_members", "kwargs": {"as": "delegate stock@"}, "diff": first_diff(a, b),
                        "raised": isinstance(a, dict) and "raised" in a, "rows": len(a) if isinstance(a, list) else None})
    frappe.set_user(user)
    results.append({"case": "rows_seen_by_admin", "kwargs": {}, "diff": None,
                    "rows": len(new.list_members())})
    return results


GROUPS = {"erp-count": erp_count,
          "erp-team": lambda old_dir: team("pearnly_erp_portal", ADMIN, old_dir),
          "cowork-team": lambda old_dir: team("pearnly_cowork", "admin@cowork.regression", old_dir)}


def main():
    site, group, old_dir = sys.argv[1:4]
    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    try:
        frappe.set_user(ADMIN if group.startswith("erp") else "admin@cowork.regression")
        results = GROUPS[group](old_dir)
        bad = [r for r in results if r["diff"]]
        for r in bad[:10]:
            print("DIFF", json.dumps(r, ensure_ascii=False))
        print("EQUAL " + json.dumps({"group": group, "cases": len(results), "different": len(bad),
                                       "raised_both": [r["case"] for r in results if r.get("raised")],
                                       "rows": [r.get("rows") for r in results if r.get("rows") is not None]}, ensure_ascii=False))
    finally:
        frappe.db.rollback()
        frappe.destroy()


if __name__ == "__main__":
    main()
